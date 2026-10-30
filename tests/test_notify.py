"""A brief is written under out/briefs/ without a webhook, or posted to Slack without leaking its URL."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import requests

from objection_brief.notify import NotifyError, notify, preview

MESSAGE = {"text": "brief", "blocks": [{"type": "divider"}]}
# Built by concatenation so no webhook-shaped literal sits in the repo (ob-tests).
WEBHOOK = "https://hooks.slack.com/" + "services/" + "T000/B000/" + "x" * 24


@dataclass
class FakeResponse:
    status_code: int


class FakePost:
    """Stands in for `requests.post`; keeps the arguments, returns or raises as told."""

    def __init__(self, status: int = 200, error: Exception | None = None) -> None:
        self.status, self.error = status, error
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self, url: str, *, json: Any, timeout: float, allow_redirects: bool
    ) -> FakeResponse:
        self.calls.append(
            {"url": url, "json": json, "timeout": timeout, "allow_redirects": allow_redirects}
        )
        if self.error is not None:
            raise self.error
        return FakeResponse(self.status)


def test_without_a_webhook_the_brief_is_written_under_out_dir(tmp_path: Path) -> None:
    out_dir = tmp_path / "out" / "briefs"
    post = FakePost()
    path = notify(MESSAGE, "t01_permissions", webhook_url=None, out_dir=out_dir, post=post)

    assert path is not None
    assert path == out_dir / "t01_permissions.json"
    assert json.loads(path.read_text("utf-8")) == MESSAGE
    assert post.calls == []
    assert [p.name for p in out_dir.iterdir()] == ["t01_permissions.json"]


@pytest.mark.parametrize("transcript_id", ["../escape", "a/b", "", "x" * 65, "t01.json"])
def test_an_id_that_could_leave_out_dir_is_refused(tmp_path: Path, transcript_id: str) -> None:
    with pytest.raises(ValueError, match="not a valid transcript id"):
        notify(MESSAGE, transcript_id, webhook_url=None, out_dir=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_with_a_webhook_the_brief_is_posted_and_no_file_written(tmp_path: Path) -> None:
    post = FakePost()
    assert notify(MESSAGE, "t01", webhook_url=WEBHOOK, out_dir=tmp_path, post=post) is None
    assert post.calls == [
        {"url": WEBHOOK, "json": MESSAGE, "timeout": 60.0, "allow_redirects": False}
    ]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("post", "message"),
    [
        (FakePost(status=404), "returned HTTP 404"),
        (FakePost(status=500), "returned HTTP 500"),
        (FakePost(error=requests.ConnectionError(f"cannot reach {WEBHOOK}")), "(ConnectionError)"),
        (FakePost(error=requests.Timeout(f"timed out: {WEBHOOK}")), "(Timeout)"),
    ],
)
def test_a_webhook_failure_is_a_notify_error_without_the_url(
    tmp_path: Path, post: FakePost, message: str
) -> None:
    with pytest.raises(NotifyError, match=message) as caught:
        notify(MESSAGE, "t01", webhook_url=WEBHOOK, out_dir=tmp_path, post=post)
    assert WEBHOOK not in str(caught.value)
    assert "services/" not in str(caught.value)
    if post.error is not None:
        # `from None`: a printed traceback doesn't show the original error and its URL.
        assert caught.value.__suppress_context__


def test_a_webhook_url_that_is_not_slacks_is_refused_before_posting(tmp_path: Path) -> None:
    post = FakePost()
    with pytest.raises(NotifyError, match=r"must start with https://hooks\.slack\.com/") as caught:
        notify(
            MESSAGE,
            "t01",
            webhook_url="https://collector.example/hook",
            out_dir=tmp_path,
            post=post,
        )
    assert "collector.example" not in str(caught.value)
    assert post.calls == []


def test_preview_drops_control_characters() -> None:
    message = {"blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": "a\x1b[2Jb\nc"}}]}
    # The escape character goes, so what's left is inert text.
    assert preview(message) == "a[2Jb\nc"
