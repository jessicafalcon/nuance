"""Deliver a brief: post it to the Slack webhook, or write it under `out/briefs/` (brief §7.4)."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import requests

from objection_brief import config
from objection_brief.llm import JSON

__all__ = ["NotifyError", "check_webhook", "notify"]

# Only Slack's incoming-webhook host gets a brief; any other URL in the variable is a
# mistake that would send prospect quotes somewhere else.
_WEBHOOK_PREFIX = "https://hooks.slack.com/"


class NotifyError(Exception):
    """The brief could not be delivered; the message never contains the webhook URL."""


def check_webhook(webhook_url: str) -> None:
    """Refuse a webhook URL that isn't Slack's; the CLI checks it before any model call.

    Raises:
        NotifyError: the URL doesn't start with Slack's webhook host.
    """
    if not webhook_url.startswith(_WEBHOOK_PREFIX):
        raise NotifyError(f"SLACK_WEBHOOK_URL must start with {_WEBHOOK_PREFIX}")


def notify(
    message: JSON, transcript_id: str, *, webhook_url: str | None, out_dir: Path
) -> Path | None:
    """Post the brief when a webhook is set and return `None`; else write it and return the path.

    Raises:
        NotifyError: the webhook URL is not Slack's, or the post failed.
        ValueError: `transcript_id` can't name a file (the CLI checks it first).
    """
    if webhook_url is None:
        return _write(message, transcript_id, out_dir)
    check_webhook(webhook_url)
    try:
        status = _send(webhook_url, message)
    except requests.RequestException as error:
        # `requests` puts the URL in its messages, and the URL is the credential, so
        # only the error's type is kept, and `from None` drops it from the traceback.
        raise NotifyError(f"the Slack webhook request failed ({type(error).__name__})") from None
    if status != 200:
        raise NotifyError(f"the Slack webhook returned HTTP {status}")
    return None


def _send(webhook_url: str, message: JSON) -> int:
    # The one function that reaches Slack; tests replace it. No redirects: the brief
    # goes to Slack's host and nowhere it points to.
    response = requests.post(
        webhook_url, json=message, timeout=config.request_timeout_s, allow_redirects=False
    )
    return response.status_code


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
