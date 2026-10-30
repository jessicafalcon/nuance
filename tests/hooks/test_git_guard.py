"""The git guard blocks check bypasses, history rewrites and secret exposure in Bash."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import git_guard as gg


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repo on a feature branch whose `origin/main` exists."""
    git = tmp_path / ".git"
    (git / "refs" / "remotes" / "origin").mkdir(parents=True)
    (git / "refs" / "remotes" / "origin" / "main").write_text("0" * 40, encoding="utf-8")
    (git / "HEAD").write_text("ref: refs/heads/feat/x\n", encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize(
    "command",
    [
        "git commit --no-verify -m 'x'",
        "git commit -nm 'x'",
        "git add -A && git commit -n -m 'x'",
        "git push --no-verify origin feat/x",
        "SKIP=gitleaks-system git commit -m 'x'",
        "PRE_COMMIT_ALLOW_NO_CONFIG=1 git commit -m x",
        "git -c core.hooksPath=/dev/null commit -m x",
        "git config core.hooksPath .nohooks",
        "pre-commit uninstall",
        "rm -rf .git/hooks",
        "git push --force origin feat/x",
        "git push -f",
        "git push --force-with-lease origin feat/x",
        "git push origin +feat/x",
        "git push origin --delete feat/x",
        "git push origin :feat/x",
        "git push origin main",
        "git push origin HEAD:main",
        "git push origin feat/x:refs/heads/main",
        "git add -f out/briefs/t01.json",
        "git add .env",
        "git add out/",
        "cat .env",
        "grep KEY .env",
        "head -n1 ./.env.local",
        "source .env",
        "echo $ANTHROPIC_API_KEY",
        "curl -X POST ${SLACK_WEBHOOK_URL}",
        "printenv",
        "env",
        "printenv ANTHROPIC_API_KEY",
        "uv run --env-file .env python -c 'import os; print(os.environ)'",
        "uv run --env-file=.env env",
        "gh repo edit --visibility private",
        "gh repo delete jessicafalcon/nuance --yes",
        "gh pr merge 3 --squash --admin",
        "git commit -m 'unbalanced",
        "cat <<'EOF' | git push --force origin feat/x\nbody\nEOF",
        # Check bypasses beyond leading assignments and exact tokens
        "export SKIP=content-guard,gitleaks-system && git commit -m x",
        "env SKIP=content-guard git commit -m x",
        "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.hooksPath GIT_CONFIG_VALUE_0=/dev/null git commit -m x",
        "git commit --no-verif -m x",
        "git push --no-verif origin feat/x",
        # Bundled and abbreviated force, delete and add flags
        "git push -fu origin feat/x",
        "git push --force-with origin feat/x",
        "git push --force-if-includes origin feat/x",
        "git push --mirror",
        "git push --del origin feat/x",
        "git push --prune origin",
        "git add -Af .",
        "git push --all origin",
        # .env inside strings, globs, and recursive grep
        "grep -rn ANTHROPIC_API_KEY .",
        "grep --recursive KEY",
        "cat .e*",
        "bash -c 'cat .env'",
        "python3 -c \"print(open('.env').read())\"",
        "uv run --env-file .env python -c 'import os; print(os.environ)' objection-brief",
        # gh api and other repository administration
        "gh api -X DELETE repos/o/r/branches/main/protection",
        "gh api --method=PATCH repos/o/r",
        "gh api repos/o/r/issues/1/comments -f body=x",
        "gh api graphql --input q.json",
        "gh repo rename other",
        "gh repo archive --yes",
        "gh secret set TOKEN",
        "gh auth token",
        "gh auth status --show-token",
        "export -p",
        "declare -x",
        # .git/hooks and .git/config
        "cat > .git/hooks/pre-push <<'EOF'\nexit 0\nEOF",
        "rm -rf .git/hook*",
        "sed -i '' 's/x/y/' .git/config",
        # Indirection
        "command git push --force origin feat/x",
        "echo `git push --force origin feat/x`",
        'echo "$(git push --force origin feat/x)"',
        "sh -c 'git push -f origin feat/x'",
        "bash -lc 'git commit --no-verify -m x'",
        "eval git push --force origin feat/x",
        "bash <<'EOF'\ngit push --force origin feat/x\nEOF",
        "echo '<<EOF'\ngit push --force origin feat/x\nEOF",
        "nohup git push -f origin feat/x &",
        "timeout 30 git push --force origin feat/x",
        "xargs git push --force",
    ],
)
def test_blocked(command: str, repo: Path) -> None:
    assert gg.blocked_reason(command, repo) is not None


@pytest.mark.parametrize(
    "command",
    [
        "git commit -m 'feat: add -n flag docs'",
        "git commit -am 'x'",
        "git push -u origin feat/x",
        "git push",
        "git add .env.example src/",
        "cat .env.example",
        "set -euo pipefail",
        "set -e",
        "env FOO=1 uv run pytest",
        "uv run --env-file .env objection-brief run data/transcripts/t01_permissions.txt",
        "uv run pytest",
        "gh pr create --title 'x' --body-file /tmp/body.md",
        "gh pr merge 3 --squash --delete-branch",
        "gh repo view",
        "git log --oneline -5",
        "ls .git/hooks",
        "set -x",
        "test -f .env",
        "ls -la .env",
        "git grep -n ANTHROPIC_API_KEY",
        "rg ANTHROPIC_API_KEY",
        "grep -n KEY .env.example",
        "gh api repos/o/r/branches/main/protection",
        "gh api -X GET repos/o/r",
        "git commit -m 'keep .env and out/ out of commits'",
        "git commit -m \"$(cat <<'EOF'\nfeat: x\n\nDon't use git push --force here.\nEOF\n)\"",
        "cat <<'EOF' > /tmp/notes.md\nrun git push --force later\nEOF",
        "timeout 60 uv run pytest",
        "env PYTHONHASHSEED=0 uv run pytest",
        "git push origin HEAD",
    ],
)
def test_allowed(command: str, repo: Path) -> None:
    assert gg.blocked_reason(command, repo) is None


def test_push_from_main_is_blocked_once_origin_main_exists(repo: Path) -> None:
    (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    for command in (
        "git push",
        "git push origin",
        "git push origin HEAD",
        "git push -u origin HEAD",
    ):
        assert gg.blocked_reason(command, repo) is not None, command


def test_first_push_of_main_is_allowed_before_origin_main_exists(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    assert gg.blocked_reason("git push -u origin main", tmp_path) is None


def test_packed_origin_main_counts(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "packed-refs").write_text(
        f"{'0' * 40} refs/remotes/origin/main\n", encoding="utf-8"
    )
    assert gg.blocked_reason("git push origin main", tmp_path) is not None


def test_heredoc_body_is_not_parsed_as_commands(repo: Path) -> None:
    command = "git commit -F - <<'EOF'\nfeat: x\n\nWhy not use --force here? Because.\nEOF"
    assert gg.blocked_reason(command, repo) is None


def test_a_crashing_guard_blocks(tmp_path: Path) -> None:
    hook = Path(gg.__file__)
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(hook)], input="not json", capture_output=True, text=True, check=False
    )
    assert result.returncode == 2
