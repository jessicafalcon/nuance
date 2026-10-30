#!/usr/bin/env python3
"""Block shell commands that bypass the repo's checks, rewrite shared history or expose secrets.

Claude Code PreToolUse hook on Bash. Exits 2 with the reason on a blocked command, and
on a crash. It is a guardrail against mistakes, not a sandbox: branch protection on
`main` and GitHub secret-scanning push protection are the server-side backstop.

Blocked:

- **Skipping checks:** `--no-verify` (or `commit -n`), `core.hooksPath`, `SKIP=`,
  `PRE_COMMIT_ALLOW_NO_CONFIG`, `pre-commit uninstall`, changing `.git/hooks/`.
- **History:** force pushes (`--force`, `-f`, `--force-with-lease`, `+refspec`,
  `--mirror`), remote deletes, and pushes to `main` once `origin/main` exists.
- **Secrets:** reading `.env` with any command, expanding `$ANTHROPIC_API_KEY` or
  `$SLACK_WEBHOOK_URL`, dumping the environment (`env`, `printenv`), and
  `--env-file` with any program other than `objection-brief`.
- **Ignored files:** `git add -f`, and staging `.env` or `out/`.
- **Repo administration:** `gh repo delete/edit/rename/archive`, `gh pr merge --admin`.
  The maintainer does these by hand.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path

# Keep in sync with .env.example.
SECRET_VARS = ("ANTHROPIC_API_KEY", "SLACK_WEBHOOK_URL")
SECRET_EXPANSION = re.compile(rf"\$\{{?({'|'.join(SECRET_VARS)})\b")
# `.env`, `.env.local`, `path/.env`; never `.env.example`.
ENV_FILE = re.compile(r"(^|/)\.env(\.(?!example$)[\w.-]+)?$")
HEREDOC = re.compile(r"""(<<-?\s*(['"]?)(\w+)\2)[^\n]*\n.*?^\t*\3$""", re.DOTALL | re.MULTILINE)
OPERATORS = {"&&", "||", ";", "|", "&", "|&", ";;", "(", ")"}
# git options that take a value as the next word, before the subcommand.
GIT_VALUE_OPTIONS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}
FORCE_FLAGS = {"--force", "-f", "--force-with-lease", "--force-if-includes", "--mirror"}
DELETE_FLAGS = {"--delete", "-d", "--prune"}
ENV_DUMPERS = {"env", "printenv", "export", "set", "declare"}


def segments(command: str) -> list[list[str]]:
    """Split a command into simple commands (words), dropping heredoc bodies.

    >>> segments("git add -A && git commit -m 'a; b' | cat")
    [['git', 'add', '-A'], ['git', 'commit', '-m', 'a; b'], ['cat']]
    """
    text = HEREDOC.sub(r"\1", command).replace("\n", " ; ")
    lexer = shlex.shlex(text, posix=True, punctuation_chars=";&|()")
    lexer.whitespace_split = True
    result: list[list[str]] = [[]]
    for token in lexer:
        if token in OPERATORS:
            result.append([])
        else:
            result[-1].append(token)
    return [words for words in result if words]


def _strip_assignments(words: list[str]) -> tuple[list[str], list[str]]:
    """Split leading `NAME=value` assignments from the program and its arguments."""
    i = 0
    while i < len(words) and re.fullmatch(r"[A-Za-z_]\w*=.*", words[i]):
        i += 1
    return words[:i], words[i:]


def _git_subcommand(args: list[str]) -> tuple[str, list[str]]:
    """The git subcommand and its arguments, skipping global options."""
    i = 0
    while i < len(args) and args[i].startswith("-"):
        i += 2 if args[i] in GIT_VALUE_OPTIONS else 1
    return (args[i], args[i + 1 :]) if i < len(args) else ("", [])


def _origin_main_exists(root: Path) -> bool:
    git = root / ".git"
    if (git / "refs" / "remotes" / "origin" / "main").is_file():
        return True
    packed = git / "packed-refs"
    return packed.is_file() and "refs/remotes/origin/main" in packed.read_text(encoding="utf-8")


def _on_main(root: Path) -> bool:
    head = root / ".git" / "HEAD"
    return head.is_file() and head.read_text(encoding="utf-8").strip() == "ref: refs/heads/main"


def _check_git(sub: str, args: list[str], root: Path) -> str | None:
    if "--no-verify" in args or (
        sub == "commit" and any(re.fullmatch(r"-[a-zA-Z]*n[a-zA-Z]*", a) for a in args)
    ):
        return "skips the pre-commit checks (--no-verify)"
    if sub == "push":
        if FORCE_FLAGS & set(args) or any(a.startswith("+") for a in args):
            return "force-pushes; rewriting pushed history needs the maintainer"
        if DELETE_FLAGS & set(args) or any(a.startswith(":") for a in args):
            return "deletes a remote ref"
        targets_main = any(re.fullmatch(r"(.*:)?(refs/heads/)?main", a) for a in args)
        refspecs = [a for a in args if not a.startswith("-")]
        implicit = len(refspecs) <= 1 and _on_main(root)  # `git push [remote]` from main
        if (targets_main or implicit) and _origin_main_exists(root):
            return "pushes to main; changes reach main only through a merged PR"
    if sub == "add":
        if {"-f", "--force"} & set(args):
            return "force-adds an ignored file"
        if any(ENV_FILE.search(a) or re.match(r"(\./)?out(/|$)", a) for a in args):
            return "stages `.env` or `out/`, which never enter the repo"
    if sub == "config" and any("hookspath" in a.lower() for a in args):
        return "changes core.hooksPath, which disables the pre-commit checks"
    return None


def check(words: list[str], root: Path) -> str | None:
    """The reason a simple command is blocked, or None.

    >>> check(["git", "commit", "--no-verify", "-m", "x"], Path("/nonexistent"))
    'skips the pre-commit checks (--no-verify)'
    >>> check(["uv", "run", "--env-file", ".env", "objection-brief", "run-all"], Path("/nonexistent")) is None
    True
    """
    assignments, words = _strip_assignments(words)
    if any(a.startswith(("SKIP=", "PRE_COMMIT_ALLOW_NO_CONFIG=")) for a in assignments):
        return "skips pre-commit hooks (SKIP= or PRE_COMMIT_ALLOW_NO_CONFIG=)"
    if not words:
        return None
    program, args = Path(words[0]).name, words[1:]
    if any("core.hookspath" in w.lower() for w in words):
        return "changes core.hooksPath, which disables the pre-commit checks"
    if any(".git/hooks" in w for w in args) and program not in {"ls", "cat", "git"}:
        return "changes .git/hooks/"
    # `set -e` configures the shell; bare `set`, `env` or `export -p` prints every variable.
    if program in ENV_DUMPERS and (not args or args in (["-p"], ["-x"], ["-0"])):
        return "prints the whole environment, which can hold the API key"
    if program == "printenv" and set(args) & set(SECRET_VARS):
        return "prints a secret"
    if "--env-file" in words or any(w.startswith("--env-file=") for w in words):
        if "objection-brief" not in words:
            return "loads `.env` into a program other than `objection-brief`"
    elif any(ENV_FILE.search(w) for w in args):
        return "reads `.env`; only the maintainer opens it"
    if program == "pre-commit" and args[:1] == ["uninstall"]:
        return "uninstalls the pre-commit checks"
    if program == "git":
        sub, sub_args = _git_subcommand(args)
        return _check_git(sub, sub_args, root)
    if program == "gh":
        if (
            args[:1] == ["repo"]
            and args[1:2]
            and args[1] in {"delete", "edit", "rename", "archive"}
        ):
            return f"runs `gh repo {args[1]}`; repo administration is done by the maintainer"
        if args[:2] == ["pr", "merge"] and "--admin" in args:
            return "merges past branch protection (--admin)"
    return None


def blocked_reason(command: str, root: Path) -> str | None:
    if SECRET_EXPANSION.search(command):
        return "expands a secret variable into the command"
    try:
        parts = segments(command)
    except ValueError:  # unbalanced quotes: judge what can't be parsed by its words
        if re.search(r"\b(git|gh|pre-commit)\b|\.env\b", command):
            return "can't be parsed (unbalanced quotes) and touches git, gh or .env"
        return None
    for words in parts:
        if reason := check(words, root):
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
