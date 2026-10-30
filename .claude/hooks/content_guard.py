#!/usr/bin/env python3
"""Block secrets and banned terms before they reach a file, a commit or a pull request.

Two rule sets, checked together:

- **Secrets:** provider key shapes (Anthropic, Slack, GitHub, AWS, Google, private keys)
  and a filled-in value for a variable `.env.example` lists.
- **Banned terms:** the names of real planning vendors (PROJECT-BRIEF.md §1, hard
  rules). The list is stored as SHA-256 digests of normalized phrases of one to three words in
  `banned-terms.sha256`, so the repo never names a vendor while CI can still check.

Entry points:

- Claude Code PreToolUse hook (payload on stdin): text written inside the project by
  Write/Edit/MultiEdit; every Bash command for secrets; and Bash commands that publish
  text (git commit/tag/notes, gh pr/issue/release create/edit/comment/review) for banned
  terms too, including the files they name with `-F`, `--file`, `--body-file` or
  `--notes-file`. Exits 2 on a hit, when a named file can't be read, or on a crash.
- pre-commit (`FILE...`): the staged files at the pre-commit stage, the message file at
  the commit-msg stage, and every tracked file in CI (`pre-commit run --all-files`);
  CI also passes the PR title and body as files. Exits 1 on a hit.
- `--history`: every commit message and diff on every ref (pre-push and CI), so text
  committed without the hooks, or edited into a squash message on GitHub, is caught.
- `--hash-term TERM...`: print the digest line(s) to append to `banned-terms.sha256`.

A publishing command whose text the guard can't see (a body from piped stdin, or a
command substitution other than `$(cat <<'EOF' ... EOF)`) is blocked, not trusted.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import unicodedata
from pathlib import Path

TERMS_FILE = Path(__file__).with_name("banned-terms.sha256")
MAX_PHRASE_WORDS = 3

# Key shapes, specific enough that ordinary code and prose never match. Tests build
# their fake keys by concatenation so these literals never appear in the repo.
SECRET_PATTERNS: dict[str, re.Pattern[str]] = {
    "Anthropic API key": re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    "API key (sk-)": re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{32,}"),
    "Slack webhook URL": re.compile(
        r"hooks\.slack\.com/(?:services|workflows|triggers)/[A-Za-z0-9_/\-]{16,}"
    ),
    "Slack token": re.compile(r"\bxox[abposr]-[A-Za-z0-9\-]{10,}"),
    "GitHub token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,})"),
    "AWS access key": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "Google API key": re.compile(r"\bAIza[0-9A-Za-z_\-]{35}"),
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    # A variable `.env.example` lists, an equals sign, then eight or more value characters.
    # Placeholders (`<your key>`), shell references (`$VAR`) and empty values pass.
    # Keep the names in sync with .env.example.
    "filled-in .env variable": re.compile(
        r"\b(?:ANTHROPIC_API_KEY|SLACK_WEBHOOK_URL)[ \t]*=[ \t]*[\"']?[^\s\"'#<>${}]{8,}"
    ),
}

# A shell word: unquoted characters and quoted strings, so a message passed in quotes is
# one word and a flag named inside it is never read as a real flag.
SHELL_WORD = re.compile(r"""(?:"(?:[^"\\]|\\.)*"|'[^']*'|[^\s"'])+""")
_W = SHELL_WORD.pattern
# Only subcommands that publish text. git takes global options before the subcommand.
PUBLISHING_COMMAND = re.compile(
    rf"\b(git(\s+(-[Cc]\s+{_W}|-[pP]|--[\w-]+((=|\s+){_W})?))*\s+(commit|tag|notes)"
    r"|gh\s+(pr|issue)\s+(create|new|edit|comment|review|merge|close)"
    r"|gh\s+release\s+(create|edit))\b"
)
# A heredoc: operator and marker (1), marker quote (2), marker (3), the rest of the
# marker line (4), body (5). The body is text, not words: its quotes need not balance.
HEREDOC = re.compile(r"""(<<-?\s*(['"]?)(\w+)\2)([^\n]*)\n(.*?)^\t*\3$""", re.DOTALL | re.MULTILINE)
# Command substitution, except the `$(cat <<'EOF' ... EOF)` form whose text is inline.
SUBSTITUTION = re.compile(r"\$\((?!\s*cat\s+<<)|`")
FILE_FLAGS = ("-F", "--file", "--body-file", "--notes-file")


class UncheckableError(Exception):
    """Published text the guard can't see, so it blocks instead of trusting it."""


def quote_at(command: str, end: int) -> str:
    """The shell quote open at position `end`: `'`, `"` or `` (none).

    `$(` starts a new quoting context, as in the shell, so the heredoc in
    `git commit -m "$(cat <<'EOF' ...` is unquoted.

    >>> quote_at('echo "a $(cat <<', 16)
    ''
    >>> quote_at("echo '<<", 6)
    "'"
    """
    stack: list[str] = []
    quote = ""
    i = 0
    while i < end:
        c = command[i]
        if quote == "'":
            quote = "" if c == "'" else quote
        elif c == "\\":
            i += 1
        elif command.startswith("$(", i):
            stack.append(quote)
            quote = ""
            i += 1
        elif c == ")" and stack and not quote:
            quote = stack.pop()
        elif quote == '"':
            quote = "" if c == '"' else quote
        elif c in "'\"":
            quote = c
        i += 1
    return quote


def heredocs(command: str) -> list[re.Match[str]]:
    """Real heredocs: a `<<` inside quotes (`echo '<<EOF'`) is text, not a heredoc.

    Quoting is judged on the command outside earlier heredoc bodies, whose apostrophes
    are text.

    >>> [m.group(3) for m in heredocs("a <<'X'\\nit's\\nX\\nb <<'Y'\\ny\\nY")]
    ['X', 'Y']
    """
    found: list[re.Match[str]] = []
    outside = ""  # the command so far, heredoc bodies removed
    pos = 0
    while match := HEREDOC.search(command, pos):
        before = outside + command[pos : match.start()]
        if quote_at(before, len(before)):
            outside = before + command[match.start() : match.start() + 2]
            pos = match.start() + 2
        else:
            found.append(match)
            outside = before + match.group(1) + match.group(4)
            pos = match.end()
    return found


def strip_heredocs(command: str) -> str:
    """The command with each real heredoc body removed; the marker line is kept.

    >>> strip_heredocs("cat <<'EOF' | sh\\necho hi\\nEOF")
    "cat <<'EOF' | sh"
    """
    parts: list[str] = []
    pos = 0
    for match in heredocs(command):
        parts += [command[pos : match.start()], match.group(1), match.group(4)]
        pos = match.end()
    return "".join([*parts, command[pos:]])


def phrase_digest(phrase: str) -> str:
    """Digest of a phrase after normalization, as stored in the terms file.

    >>> phrase_digest("Northwind  Planning") == phrase_digest("northwind planning")
    True
    """
    return hashlib.sha256(" ".join(_words(phrase)).encode()).hexdigest()


def _words(text: str) -> list[str]:
    """Lowercase ASCII words: accents folded, punctuation and case dropped.

    >>> _words("Café-Planning, v2!")
    ['cafe', 'planning', 'v2']
    """
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.findall(r"[a-z0-9]+", folded.lower())


def load_digests(path: Path = TERMS_FILE) -> frozenset[str]:
    if not path.is_file():
        return frozenset()
    lines = path.read_text(encoding="utf-8").splitlines()
    return frozenset(line.strip() for line in lines if line.strip() and not line.startswith("#"))


def find_banned(text: str, digests: frozenset[str]) -> list[str]:
    """Phrases of one to three words in `text` whose digest is in `digests`.

    >>> find_banned("We compared it to Northwind Planning.", frozenset({phrase_digest("northwind planning")}))
    ['northwind planning']
    """
    if not digests:
        return []
    words = _words(text)
    hits: set[str] = set()
    for size in range(1, MAX_PHRASE_WORDS + 1):
        for start in range(len(words) - size + 1):
            phrase = " ".join(words[start : start + size])
            if hashlib.sha256(phrase.encode()).hexdigest() in digests:
                hits.add(phrase)
    return sorted(hits)


def find_secrets(text: str) -> list[str]:
    """Names of the secret patterns that match `text` (never the matched value)."""
    return sorted(name for name, pattern in SECRET_PATTERNS.items() if pattern.search(text))


def _unquote(word: str) -> str:
    """Remove shell quoting, never expanding variables.

    A name like `$TMPDIR/body.md` then matches no file and the guard fails closed, and
    its error message shows the name as typed, so it can never echo a variable's value.
    """
    try:
        return "".join(shlex.split(word))
    except ValueError:  # unbalanced quote: keep the word as typed
        return word


def flag_values(command: str, flags: tuple[str, ...]) -> list[str]:
    """Values given to `flags` in a shell command.

    Long flags as `--flag VALUE` or `--flag=VALUE`; short ones also attached (`-Fb.md`)
    or last in a bundle (`-dF b.md`), as git and gh parse them.

    >>> flag_values('gh pr create -t "use -F x" --body-file=b.md -Fc.md -dF d.md', ("-F", "--body-file"))
    ['b.md', 'c.md', 'd.md']
    """
    words = SHELL_WORD.findall(strip_heredocs(command))
    short = "".join(f[1] for f in flags if len(f) == 2)
    bundle = re.compile(rf"-[A-Za-z]*?[{short}](.*)") if short else None
    values: list[str] = []
    for i, word in enumerate(words):
        name, equals, value = word.partition("=")
        if name in flags and (equals or i + 1 < len(words)):
            values.append(_unquote(value if equals else words[i + 1]))
        elif bundle and not word.startswith("--") and (match := bundle.fullmatch(word)):
            if match.group(1):
                values.append(_unquote(match.group(1)))
            elif i + 1 < len(words):
                values.append(_unquote(words[i + 1]))
    return values


def read_message_file(name: str, bases: list[Path]) -> str:
    """Read a file named by `-F`/`--body-file`, relative to the shell's directory or `-C`.

    Raises FileNotFoundError when no candidate exists: the guard fails closed rather
    than let an unread file through.
    """
    path_name = Path(name).expanduser()
    for base in bases:
        path = base / path_name  # an absolute name ignores the base
        if path.is_file():
            return path.read_text(encoding="utf-8", errors="replace")
    raise FileNotFoundError(2, "cannot read the file to check it", name)


def _stdin_hidden(stripped: str) -> bool:
    """True when a publishing command reads stdin from a pipe or a `<` redirect, text the
    guard never sees; only a heredoc on the command's own segment is visible.

    >>> _stdin_hidden("git commit -F - <<'EOF' && git log | head")
    False
    >>> _stdin_hidden("cat b.md | gh pr create --body-file -")
    True
    """
    for part in re.split(r"&&|\|\||;|\n", stripped):
        if match := PUBLISHING_COMMAND.search(part):
            piped = "|" in part[: match.start()]
            redirected = re.search(r"(?<!<)<(?![<(])", part)
            if piped or redirected or "<<" not in part:
                return True
    return False


def texts_to_check(
    tool: str, tool_input: dict[str, object], root: Path, cwd: Path
) -> tuple[list[str], list[str]]:
    """Split a tool call into (texts checked for secrets, texts checked for terms too)."""
    if tool == "Bash":
        command = str(tool_input.get("command") or "")
        if not PUBLISHING_COMMAND.search(command):
            return [command], []
        stripped = strip_heredocs(command)
        if any(quote_at(stripped, m.start()) != "'" for m in SUBSTITUTION.finditer(stripped)):
            raise UncheckableError("a command substitution builds text the guard can't see")
        bases = [cwd, *(cwd / d for d in flag_values(command, ("-C",)))]
        files = flag_values(command, FILE_FLAGS)
        if "-" in files and _stdin_hidden(stripped):
            raise UncheckableError("stdin is visible only as a heredoc, with no pipe or `<`")
        published = [command, *(read_message_file(f, bases) for f in files if f != "-")]
        return published, published
    file_path = str(tool_input.get("file_path") or "")
    if not file_path:
        return [], []
    try:
        Path(file_path).resolve().relative_to(root)
    except ValueError:
        return [], []  # outside the project: memory, scratchpad
    edits = tool_input.get("edits")
    edit_texts = (
        [str(e.get("new_string") or "") for e in edits if isinstance(e, dict)]
        if isinstance(edits, list)
        else []
    )
    texts = [str(tool_input.get(key) or "") for key in ("content", "new_string", "new_source")]
    return texts + edit_texts, texts + edit_texts


def git_history(root: Path) -> str:
    """Every commit message and diff on every ref, as one text."""
    # Fixed argument list, no shell: nothing from the payload reaches this call.
    result = subprocess.run(
        ["git", "log", "--all", "-p", "--format=%n%B"],  # noqa: S607
        cwd=root,
        capture_output=True,
        text=True,
        errors="replace",
        check=True,
    )
    return result.stdout


def findings(secret_texts: list[str], term_texts: list[str], digests: frozenset[str]) -> list[str]:
    secrets = sorted({name for text in secret_texts for name in find_secrets(text)})
    terms = sorted({hit for text in term_texts for hit in find_banned(text, digests)})
    return [f"a {name}" for name in secrets] + [f"the banned term {term!r}" for term in terms]


def _report(where: str, found: list[str]) -> None:
    print(
        f"content-guard: blocked, {where} contains {', '.join(found)}. Secrets live only in "
        "`.env` (gitignored); real planning-vendor names never appear in this repo "
        "(PROJECT-BRIEF.md §1). Rewrite without them; describe the category instead of a vendor.",
        file=sys.stderr,
    )


def main(argv: list[str]) -> int:
    if argv[:1] == ["--hash-term"]:
        print("\n".join(phrase_digest(term) for term in argv[1:]))
        return 0

    digests = load_digests()
    if argv == ["--history"]:
        history = git_history(Path.cwd())
        if found := findings([history], [history], digests):
            _report("the git history", found)
            return 1
        return 0
    if argv:  # pre-commit: staged files, the commit message file, or every tracked file
        failed = False
        for name in argv:
            path = Path(name)
            if not path.is_file() or path.resolve() == TERMS_FILE.resolve():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if found := findings([text], [text], digests):
                _report(name, found)
                failed = True
        return 1 if failed else 0

    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path.cwd()).resolve()
    payload = json.load(sys.stdin)
    cwd = Path(payload.get("cwd") or root)
    try:
        secret_texts, term_texts = texts_to_check(
            payload.get("tool_name", ""), payload.get("tool_input") or {}, root, cwd
        )
    except OSError as error:
        print(
            f"content-guard: blocked, cannot read {error.filename!r} to check it. Pass an "
            "existing absolute path, or put the text inline (a heredoc for stdin).",
            file=sys.stderr,
        )
        return 2
    except UncheckableError as error:
        print(
            f"content-guard: blocked, {error}. Pass the text inline, with `--body-file` and "
            "an absolute path, or as `$(cat <<'EOF' ... EOF)`.",
            file=sys.stderr,
        )
        return 2
    if found := findings(secret_texts, term_texts, digests):
        _report("the text", found)
        return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as error:  # exit 1 wouldn't block a tool call; a broken guard must
        print(f"content-guard: blocked, the guard failed: {error!r}", file=sys.stderr)
        sys.exit(2)
