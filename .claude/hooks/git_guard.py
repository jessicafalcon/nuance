#!/usr/bin/env python3
"""Block shell commands that bypass the repo's checks, rewrite shared history or expose secrets.

Claude Code PreToolUse hook on Bash. Exits 2 with the reason on a blocked command, and
on a crash. It is a guardrail against mistakes and casual injection, not a sandbox:
branch protection on `main`, GitHub secret-scanning push protection and CI are the
server-side backstop.

Blocked:

- **Skipping checks:** `--no-verify` and its abbreviations (and `commit -n`),
  `core.hooksPath` through `-c`, `git config` or `GIT_CONFIG_*`, `SKIP=`,
  `PRE_COMMIT_ALLOW_NO_CONFIG=`, `pre-commit uninstall`, touching `.git/hooks` or
  `.git/config`.
- **History:** force pushes (`--force` and its variants, bundled `-f`, `+refspec`,
  `--mirror`), remote deletes, and pushes that reach `main` (explicitly, through `HEAD`
  or `--all`, or a bare push from `main`) once `origin/main` exists.
- **Secrets:** naming `.env` in any word (including inside `sh -c '...'` or
  `python -c "..."`) or with a dotfile glob, except metadata checks (`test`, `ls`,
  `stat`); recursive `grep` (use `git grep` or `rg`, which skip ignored files);
  expanding `$ANTHROPIC_API_KEY` or `$SLACK_WEBHOOK_URL`; dumping the environment;
  `gh auth token`; `--env-file` unless `uv run` runs `objection-brief`.
- **Ignored files:** `git add -f` (also bundled), and staging `.env` or `out/`.
- **Repo administration:** `gh repo delete/edit/rename/archive`, mutating `gh api`
  calls, `gh secret`/`gh variable` changes, `gh workflow disable`, `gh pr merge --admin`.
  The maintainer runs these.

Wrappers (`env`, `command`, `nohup`, `xargs`, ...), nested shells (`sh -c`, `eval`),
command substitutions and heredocs fed to a shell are unwrapped and checked too.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import shlex
import sys
from pathlib import Path

# The content guard owns the shell-quoting rules; both guards read heredocs the same way.
# A guard that can't load must block (exit 2), not fail open with an ImportError (exit 1).
try:
    from content_guard import heredocs, quote_at, strip_heredocs
except Exception as error:
    print(f"git-guard: blocked, the guard failed to load: {error!r}", file=sys.stderr)
    sys.exit(2)

MAX_DEPTH = 4
# Keep in sync with .env.example.
SECRET_VARS = ("ANTHROPIC_API_KEY", "SLACK_WEBHOOK_URL")
SECRET_EXPANSION = re.compile(rf"\$\{{?({'|'.join(SECRET_VARS)})\b")
# `.env`, `./.env`, `.env.local`, `open('.env')`; never `.env.example` or `.envrc`.
ENV_FILE = re.compile(r"(^|[^\w.-])\.env(\.(?!example\b)[\w-]+)*($|[^\w.-])")
SKIP_ASSIGNMENT = re.compile(r"^(SKIP|PRE_COMMIT_ALLOW_NO_CONFIG|GIT_CONFIG_\w+)=")
SUBSTITUTION = re.compile(r"\$\(([^()]*)\)|`([^`]*)`")
OPERATORS = {"&&", "||", ";", "|", "&", "|&", ";;", "(", ")"}
SHELLS = ("sh", "bash", "zsh", "dash", "ksh")
# Programs that run the rest of their arguments as a command.
WRAPPERS = {
    "command",
    "builtin",
    "exec",
    "nohup",
    "time",
    "nice",
    "sudo",
    "doas",
    "xargs",
    "stdbuf",
}
# Programs that show a file's metadata, never its contents.
METADATA = {"test", "[", "ls", "stat"}
# git options that take a value as the next word, before the subcommand.
GIT_VALUE_OPTIONS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}
# `uv run` options that take a value, so the program they run can be found.
UV_VALUE_OPTIONS = {"--env-file", "--with", "--python", "-p", "--group", "--extra", "--package",
                    "--directory", "--project", "--with-requirements"}  # fmt: skip


def segments(command: str) -> list[list[str]]:
    """Split a command into simple commands (words), dropping heredoc bodies.

    >>> segments("git add -A && git commit -m 'a; b' | cat")
    [['git', 'add', '-A'], ['git', 'commit', '-m', 'a; b'], ['cat']]
    """
    text = strip_heredocs(command).replace("\n", " ; ")
    lexer = shlex.shlex(text, posix=True, punctuation_chars=";&|()")
    lexer.whitespace_split = True
    result: list[list[str]] = [[]]
    for token in lexer:
        if token in OPERATORS:
            result.append([])
        else:
            result[-1].append(token)
    return [words for words in result if words]


def is_flag(arg: str, *names: str) -> bool:
    """True when `arg` is one of `names`, or an abbreviation of a long one, as git, gh and
    argparse accept them (`--no-verif`, `--force-with`).

    >>> is_flag("--force-with=x", "--force-with-lease"), is_flag("--follow-tags", "--force")
    (True, False)
    """
    name = arg.split("=", 1)[0]
    return any(
        name == n
        or (n.startswith("--") and name.startswith("--") and len(name) >= 4 and n.startswith(name))
        for n in names
    )


def in_bundle(arg: str, letter: str) -> bool:
    """True when a short-option bundle (`-fu`, `-Af`) contains `letter`."""
    return re.fullmatch(rf"-[a-zA-Z]*{letter}[a-zA-Z]*", arg) is not None


def _origin_main_exists(root: Path) -> bool:
    git = root / ".git"
    if (git / "refs" / "remotes" / "origin" / "main").is_file():
        return True
    packed = git / "packed-refs"
    return packed.is_file() and "refs/remotes/origin/main" in packed.read_text(encoding="utf-8")


def _on_main(root: Path) -> bool:
    head = root / ".git" / "HEAD"
    return head.is_file() and head.read_text(encoding="utf-8").strip() == "ref: refs/heads/main"


def _git_split(args: list[str]) -> tuple[list[str], str, list[str]]:
    """Global options, the subcommand and its arguments."""
    i = 0
    while i < len(args) and args[i].startswith("-"):
        i += 2 if args[i] in GIT_VALUE_OPTIONS else 1
    return (args[:i], args[i], args[i + 1 :]) if i < len(args) else (args, "", [])


def _check_push(args: list[str], root: Path) -> str | None:
    if any(
        is_flag(a, "--force", "--force-with-lease", "--force-if-includes", "--mirror")
        or in_bundle(a, "f")
        or a.startswith("+")
        for a in args
    ):
        return "force-pushes; rewriting pushed history needs the maintainer"
    if any(
        is_flag(a, "--delete", "--prune") or in_bundle(a, "d") or a.startswith(":") for a in args
    ):
        return "deletes a remote ref"
    if not _origin_main_exists(root):
        return None  # the first push of main, before branch protection exists
    refspecs = [a for a in args if not a.startswith("-")]
    if (
        any(is_flag(a, "--all", "--branches") for a in args)
        or any(re.fullmatch(r"(.*:)?(refs/heads/)?main", r) for r in refspecs)
        or (_on_main(root) and (len(refspecs) <= 1 or "HEAD" in refspecs))
    ):
        return "pushes to main; changes reach main only through a merged PR"
    return None


def _check_git(args: list[str], root: Path) -> str | None:
    globals_, sub, args = _git_split(args)
    if any("hookspath" in a.lower() for a in globals_) or (
        sub == "config" and any("hookspath" in a.lower() for a in args)
    ):
        return "changes core.hooksPath, which disables the pre-commit checks"
    if any(is_flag(a, "--no-verify") for a in args) or (
        sub == "commit" and any(in_bundle(a, "n") for a in args)
    ):
        return "skips the pre-commit checks (--no-verify)"
    if sub == "push":
        return _check_push(args, root)
    if sub == "add":
        if any(is_flag(a, "--force") or in_bundle(a, "f") for a in args):
            return "force-adds an ignored file"
        if any(_names_env_file(a) or re.match(r"(\./)?out(/|$)", a) for a in args):
            return "stages `.env` or `out/`, which never enter the repo"
    return None


def _api_mutates(args: list[str]) -> bool:
    for i, arg in enumerate(args):
        following = args[i + 1] if i + 1 < len(args) else ""
        method = None
        if is_flag(arg, "--method"):
            method = arg.partition("=")[2] or following
        elif arg.startswith("-X"):
            method = arg[2:] or following
        if method is not None and method.upper() != "GET":
            return True
        if is_flag(arg, "--field", "--raw-field", "--input") or re.match(r"-[fF]", arg):
            return True
    return False


def _check_gh(args: list[str]) -> str | None:
    group, action = [*args, "", ""][:2]
    if group == "repo" and action in {"delete", "edit", "rename", "archive"}:
        return f"runs `gh repo {action}`; repo administration is done by the maintainer"
    if group in {"secret", "variable"} and action in {"set", "delete", "remove"}:
        return f"changes a repository {group}; the maintainer does that"
    if group == "workflow" and action == "disable":
        return "disables a workflow"
    if group == "auth" and (action == "token" or any(is_flag(a, "--show-token") for a in args)):
        return "prints the GitHub token"
    if group == "pr" and action == "merge" and any(is_flag(a, "--admin") for a in args):
        return "merges past branch protection (--admin)"
    if group == "api" and _api_mutates(args[1:]):
        return "sends a mutating GitHub API request; the maintainer runs those"
    return None


def _env_file_ok(words: list[str]) -> bool:
    """`--env-file` is allowed only as `uv run [options] objection-brief ...`."""
    if words[:2] != ["uv", "run"]:
        return False
    i = 2
    while i < len(words) and words[i].startswith("-"):
        i += 2 if words[i] in UV_VALUE_OPTIONS else 1
    return i < len(words) and words[i] == "objection-brief"


def _names_env_file(word: str) -> bool:
    if ENV_FILE.search(word):
        return True
    # A dotfile glob the shell would expand to `.env` (`.e*`, `./.en?`).
    return any(
        seg.startswith(".") and any(c in seg for c in "*?[") and fnmatch.fnmatchcase(".env", seg)
        for seg in word.split("/")
    )


def _dumps_env(program: str, args: list[str]) -> bool:
    if program in {"env", "printenv"}:
        return not args or args == ["-0"] or bool(set(args) & set(SECRET_VARS))
    if program == "set":
        return not args
    return program in {"export", "declare", "typeset"} and (not args or args in (["-p"], ["-x"]))


def _unwrap(program: str, args: list[str]) -> list[str]:
    """The command a wrapper runs: options, assignments and option values skipped."""
    i = 0
    while i < len(args) and (args[i].startswith("-") or (program == "env" and "=" in args[i])):
        i += 2 if (program, args[i]) in {("env", "-u"), ("xargs", "-I"), ("nice", "-n")} else 1
    if program == "timeout":
        i += 1  # the duration
    return args[i:]


def _check_nested(program: str, args: list[str], root: Path, depth: int) -> str | None:
    """Commands run by a wrapper, `eval` or `sh -c`."""
    if program in WRAPPERS or program in {"env", "timeout"}:
        inner = _unwrap(program, args)
        return check(inner, root, depth + 1) if inner else None
    if program == "eval":
        return blocked_reason(" ".join(args), root, depth + 1)
    if program in SHELLS:
        for i, arg in enumerate(args[:-1]):
            if in_bundle(arg, "c"):
                return blocked_reason(args[i + 1], root, depth + 1)
    return None


def check(words: list[str], root: Path, depth: int = 0) -> str | None:
    """The reason a simple command is blocked, or None.

    >>> check(["git", "commit", "--no-verify", "-m", "x"], Path("/nonexistent"))
    'skips the pre-commit checks (--no-verify)'
    >>> check(["uv", "run", "--env-file", ".env", "objection-brief", "run-all"], Path("/nonexistent")) is None
    True
    """
    if any(SKIP_ASSIGNMENT.match(w) for w in words):
        return "skips pre-commit checks (SKIP=, PRE_COMMIT_ALLOW_NO_CONFIG= or GIT_CONFIG_*)"
    while words and re.fullmatch(r"[A-Za-z_]\w*=.*", words[0]):
        words = words[1:]
    if not words:
        return None
    program, args = Path(words[0]).name, words[1:]

    if program != "ls" and any(".git/hook" in w or ".git/config" in w for w in args):
        return "touches .git/hooks or .git/config, which control the pre-commit checks"
    if _dumps_env(program, args):
        return "prints the environment, which can hold the API key"
    if any(is_flag(w, "--env-file") for w in words):
        if not _env_file_ok(words):
            return "loads `.env` into a program other than `objection-brief`"
    # git and gh words are messages and refs (`-m "keep .env out"`); `git add .env` has
    # its own rule below.
    elif program not in METADATA | {"git", "gh"} and any(_names_env_file(w) for w in args):
        return "reads `.env`; only the maintainer opens it"
    if program in {"grep", "egrep", "fgrep"} and any(
        is_flag(a, "--recursive", "--dereference-recursive") or in_bundle(a, "[rR]") for a in args
    ):
        return "greps recursively, which reads `.env`; use `git grep` or `rg` (they skip ignored files)"
    if program == "pre-commit" and args[:1] == ["uninstall"]:
        return "uninstalls the pre-commit checks"
    if program == "git":
        return _check_git(args, root)
    if program == "gh":
        return _check_gh(args)
    return _check_nested(program, args, root, depth) if depth < MAX_DEPTH else None


def blocked_reason(command: str, root: Path, depth: int = 0) -> str | None:
    if SECRET_EXPANSION.search(command):
        return "expands a secret variable into the command"
    stripped = strip_heredocs(command)
    # Substitutions run as commands of their own, inside "..." or not.
    nested = [
        m.group(1) if m.group(1) is not None else m.group(2)
        for m in SUBSTITUTION.finditer(stripped)
        if quote_at(stripped, m.start()) != "'"
    ]
    # A heredoc fed to a shell is a script: its body runs as commands.
    shell = re.compile(rf"(^|[\s|;&(])({'|'.join(SHELLS)}|eval)(\s|$)")
    for doc in heredocs(command):
        head = command[: doc.start()].rsplit("\n", 1)[-1] + doc.group(4)
        if shell.search(head):
            nested.append(doc.group(5))
    if depth < MAX_DEPTH:
        for text in nested:
            if reason := blocked_reason(text, root, depth + 1):
                return reason
    try:
        parts = segments(command)
    except ValueError:  # unbalanced quotes: judge what can't be parsed by its words
        if re.search(r"\b(git|gh|pre-commit)\b|\.env\b", command):
            return "can't be parsed (unbalanced quotes) and touches git, gh or .env"
        return None
    for words in parts:
        if reason := check(words, root, depth):
            return reason
    return None


def main() -> int:
    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path.cwd()).resolve()
    payload = json.load(sys.stdin)
    command = str((payload.get("tool_input") or {}).get("command") or "")
    reason = blocked_reason(command, root)
    if reason is None:
        return 0
    print(
        f"git-guard: blocked, this command {reason}. If the maintainer asked for it, tell them "
        "the exact command so they can run it themselves with `!`.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:  # exit 1 wouldn't block; a broken guard must not let it through
        print(f"git-guard: blocked, the guard failed: {error!r}", file=sys.stderr)
        sys.exit(2)
