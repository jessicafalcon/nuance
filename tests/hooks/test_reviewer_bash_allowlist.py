"""The review agents' shell stays read-only."""

from __future__ import annotations

import pytest

import reviewer_bash_allowlist as rba


@pytest.mark.parametrize(
    "command",
    [
        "uv run --frozen pytest",
        "uv run --frozen pyright",
        "uv run --frozen ruff check",
        "uv run --frozen ruff format --check",
        "uv audit --frozen",
        "python3 .claude/hooks/content_guard.py README.md",
        "python3 .claude/hooks/boundary_guard.py src/objection_brief/detect.py",
        "python3 .claude/hooks/docs_guard.py --check",
        "git diff main...HEAD",
        "git log --oneline main..HEAD",
        "gh pr view 3",
    ],
)
def test_checks_are_allowed(command: str) -> None:
    assert rba.is_allowed(command)


@pytest.mark.parametrize(
    "command",
    [
        "uv run pytest",  # without --frozen uv may rewrite uv.lock
        "uv run --frozen ruff check --fix",
        "uv run --frozen ruff format",
        "uv run --frozen pytest -o cache_dir=src",
        "python3 .claude/hooks/content_guard.py --hash-term x",
        "python3 .claude/hooks/docs_guard.py",  # hook mode reads stdin; --check only
        "git diff > out.txt",
        "git log; rm -rf src",
        "git diff $(echo main)",
        "git commit -m x",
        "git -C /tmp diff",
        "gh pr merge 3",
        "rm -rf out",
        "git grep -Orm TODO",
        "git grep --open-files-in-pager=rm x",
        "uv run --frozen pytest --log-file=PROJECT-BRIEF.md",
        "uv run --frozen pytest --debug=x.log",
        "uv run --frozen pytest --basete=src",
        "uv run --frozen pytest --pastebin=all",
    ],
)
def test_writes_and_escapes_are_blocked(command: str) -> None:
    assert not rba.is_allowed(command)
