"""The boundary guard keeps each dependency inside its owning module."""

from __future__ import annotations

import pytest

import boundary_guard as bg


@pytest.mark.parametrize(
    ("source", "filename"),
    [
        ("import anthropic", "detect.py"),
        ("from anthropic import Anthropic", "brief.py"),
        ("import requests as r", "cli.py"),
        ("import duckdb", "eval.py"),
        ("import os\nk = os.environ['ANTHROPIC_API_KEY']", "detect.py"),
        ("import os as o\nk = o.getenv('X')", "notify.py"),
        ("from os import environ", "log.py"),
        ("from dotenv import load_dotenv", "config.py"),
        ("print('hi')", "brief.py"),
    ],
)
def test_leak_is_reported(source: str, filename: str) -> None:
    assert bg.check_source(source, filename)


@pytest.mark.parametrize(
    ("source", "filename"),
    [
        ("import anthropic", "llm.py"),
        ("import requests", "notify.py"),
        ("import duckdb", "log.py"),
        ("import os\nk = os.environ.get('SLACK_WEBHOOK_URL')", "config.py"),
        ("print('brief written')", "cli.py"),
        ("from rich import print\nprint('x')", "brief.py"),
        ('"""Never import anthropic here."""', "detect.py"),
    ],
)
def test_owner_or_mention_passes(source: str, filename: str) -> None:
    assert bg.check_source(source, filename) == []


def test_scope_is_the_package_only() -> None:
    assert bg.in_scope("src/objection_brief/detect.py")
    assert not bg.in_scope("tests/test_detect.py")
    assert not bg.in_scope(".claude/hooks/git_guard.py")
