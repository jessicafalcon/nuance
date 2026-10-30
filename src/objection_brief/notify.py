"""Deliver a brief: post it to the Slack webhook, or write it under `out/briefs/` (brief §7.4)."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Protocol

import requests

from objection_brief import config
from objection_brief.llm import JSON

__all__ = ["NotifyError", "Post", "notify", "preview"]

# Only Slack's incoming-webhook host gets a brief; any other URL in the variable is a
# mistake that would send prospect quotes somewhere else.
_WEBHOOK_PREFIX = "https://hooks.slack.com/"


class NotifyError(Exception):
    """The brief could not be delivered; the message never contains the webhook URL."""


class _Response(Protocol):
    @property
    def status_code(self) -> int: ...


class Post(Protocol):
    """`requests.post` as `notify` uses it; tests pass a fake."""

    def __call__(
        self, url: str, *, json: Any, timeout: float, allow_redirects: bool
    ) -> _Response: ...


def notify(
    message: JSON,
    transcript_id: str,
    *,
    webhook_url: str | None,
    out_dir: Path,
    post: Post = requests.post,
) -> Path | None:
    """Post the brief when a webhook is set and return `None`; else write it and return the path.

    Raises:
        NotifyError: the webhook URL is not Slack's, or the post failed.
        ValueError: `transcript_id` can't name a file (the CLI checks it first).
    """
    if webhook_url is not None:
        _post(message, webhook_url, post)
        return None
    return _write(message, transcript_id, out_dir)


def _post(message: JSON, webhook_url: str, post: Post) -> None:
    if not webhook_url.startswith(_WEBHOOK_PREFIX):
        raise NotifyError(f"SLACK_WEBHOOK_URL must start with {_WEBHOOK_PREFIX}")
    try:
        # No redirects: the brief goes to Slack's host and nowhere it points to.
        response = post(
            webhook_url, json=message, timeout=config.request_timeout_s, allow_redirects=False
        )
    except requests.RequestException as error:
        # `requests` puts the URL in its messages, and the URL is the credential, so
        # only the error's type is kept, and `from None` drops it from the traceback.
        raise NotifyError(f"the Slack webhook request failed ({type(error).__name__})") from None
    if response.status_code != 200:
        raise NotifyError(f"the Slack webhook returned HTTP {response.status_code}")


def _write(message: JSON, transcript_id: str, out_dir: Path) -> Path:
    # A checked id has no separator or dot-dot, so the file stays directly in `out_dir`.
    if not config.transcript_id_pattern.fullmatch(transcript_id):
        raise ValueError(f"{transcript_id!r} is not a valid transcript id")
    path = out_dir / f"{transcript_id}.json"
    out_dir.mkdir(parents=True, exist_ok=True)
    # Written beside the target and renamed over it, so a crash never leaves half a
    # brief; a rename replaces a symlink at the path rather than writing through it.
    fd, tmp = tempfile.mkstemp(dir=out_dir, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(json.dumps(message, indent=2, ensure_ascii=False) + "\n")
        Path(tmp).replace(path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path


def preview(message: JSON) -> str:
    """Render a Block Kit message as plain text for the terminal.

    Slack's escapes are undone for reading, and control characters are dropped, so
    a transcript can't send escape sequences to the terminal.

    >>> print(preview({"blocks": [{"type": "section", "text": {"text": "a &lt;b&gt;"}}, {"type": "divider"}]}))
    a <b>
    ---
    """
    lines: list[str] = []
    for block in message["blocks"]:
        if block["type"] == "divider":
            lines.append("---")
        elif block["type"] == "context":
            lines.extend(element["text"] for element in block["elements"])
        else:
            lines.append(block["text"]["text"])
    text = "\n".join(lines).replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    return "".join(c for c in text if c == "\n" or c.isprintable())
