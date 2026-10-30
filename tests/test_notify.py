"""A brief is written under out/briefs/ without a webhook, or posted to Slack without leaking its URL."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import requests

from objection_brief import notify as notify_module
from objection_brief.notify import NotifyError, check_webhook, notify

MESSAGE = {"text": "brief", "blocks": [{"type": "divider"}]}
# Built by concatenation so no webhook-shaped literal sits in the repo (ob-tests).
WEBHOOK = "https://hooks.slack.com/" + "services/" + "T000/B000/" + "x" * 24


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Any]]:
    """Replaces `notify._send`, the one function that reaches Slack; returns 200."""
    calls: list[tuple[str, Any]] = []

    def fake_send(webhook_url: str, message: Any) -> int:
        calls.append((webhook_url, message))
        return 200

    monkeypatch.setattr(notify_module, "_send", fake_send)
    return calls


def test_without_a_webhook_the_brief_is_written_under_out_dir(
    tmp_path: Path, sent: list[tuple[str, Any]]
) -> None:
    out_dir = tmp_path / "out" / "briefs"
    path = notify(MESSAGE, "t01_permissions", webhook_url=None, out_dir=out_dir)

    assert path is not None
    assert path == out_dir / "t01_permissions.json"
    assert json.loads(path.read_text("utf-8")) == MESSAGE
    assert sent == []
    assert [p.name for p in out_dir.iterdir()] == ["t01_permissions.json"]


@pytest.mark.parametrize("transcript_id", ["../escape", "a/b", "", "x" * 65, "t01.json"])
def test_an_id_that_could_leave_out_dir_is_refused(tmp_path: Path, transcript_id: str) -> None:
    with pytest.raises(ValueError, match="not a valid transcript id"):
        notify(MESSAGE, transcript_id, webhook_url=None, out_dir=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_with_a_webhook_the_brief_is_posted_and_no_file_written(
    tmp_path: Path, sent: list[tuple[str, Any]]
) -> None:
    assert notify(MESSAGE, "t01", webhook_url=WEBHOOK, out_dir=tmp_path) is None
    assert sent == [(WEBHOOK, MESSAGE)]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("outcome", "message"),
    [
        (404, "returned HTTP 404"),
        (500, "returned HTTP 500"),
        (requests.ConnectionError(f"cannot reach {WEBHOOK}"), r"\(ConnectionError\)"),
        (requests.Timeout(f"timed out: {WEBHOOK}"), r"\(Timeout\)"),
    ],
)
def test_a_webhook_failure_is_a_notify_error_without_the_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: int | Exception, message: str
) -> None:
    def fake_send(webhook_url: str, message: Any) -> int:
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(notify_module, "_send", fake_send)
    with pytest.raises(NotifyError, match=message) as caught:
        notify(MESSAGE, "t01", webhook_url=WEBHOOK, out_dir=tmp_path)
    assert WEBHOOK not in str(caught.value)
    assert "services/" not in str(caught.value)
    if isinstance(outcome, Exception):
        # `from None`: a printed traceback doesn't show the original error and its URL.
        assert caught.value.__suppress_context__


def test_a_webhook_url_that_is_not_slacks_is_refused_before_posting(
    tmp_path: Path, sent: list[tuple[str, Any]]
) -> None:
    other = "https://collector.example/hook"
    with pytest.raises(NotifyError, match=r"must start with https://hooks\.slack\.com/") as caught:
        check_webhook(other)
    assert "collector.example" not in str(caught.value)
    with pytest.raises(NotifyError, match="must start with"):
        notify(MESSAGE, "t01", webhook_url=other, out_dir=tmp_path)
    assert sent == []
    check_webhook(WEBHOOK)


def test_send_posts_json_with_a_timeout_and_no_redirects(monkeypatch: pytest.MonkeyPatch) -> None:
    # Brief §7.4: the brief goes to Slack's host only, never where a redirect points.
    seen: dict[str, Any] = {}

    class Response:
        status_code = 200

    def fake_post(url: str, **kwargs: Any) -> Response:
        seen.update(url=url, **kwargs)
        return Response()

    monkeypatch.setattr(requests, "post", fake_post)
    assert notify_module._send(WEBHOOK, MESSAGE) == 200  # pyright: ignore[reportPrivateUsage]
    assert seen == {"url": WEBHOOK, "json": MESSAGE, "timeout": 60.0, "allow_redirects": False}
