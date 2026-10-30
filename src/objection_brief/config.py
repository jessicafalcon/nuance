"""Pipeline settings in one place: thresholds, models, prompt versions and the environment."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

__all__ = [
    "Settings",
    "briefs_dir",
    "detect_effort",
    "detect_max_tokens",
    "detect_model",
    "detect_prompt_version",
    "eval_models",
    "follow_up_effort",
    "follow_up_max_tokens",
    "follow_up_model",
    "follow_up_prompt_version",
    "labels_file",
    "llm_cache_dir",
    "load_settings",
    "log_db",
    "max_retries",
    "max_transcript_bytes",
    "min_confidence",
    "prices_checked",
    "prices_usd_per_mtok",
    "reports_dir",
    "request_timeout_s",
    "themes_file",
    "transcript_id_pattern",
    "transcripts_dir",
]

# The guardrail rejects a detection the model scores below this (brief §7.2).
min_confidence: Final = 0.6

# The model `run` detects with (brief §4).
detect_model: Final = "claude-haiku-5-5"
# Bump on any change to the detection prompt's text or output schema (brief §7.6). The
# cache key changes with the text anyway; the version names the change for a reader.
detect_prompt_version: Final = "detect-v1"
# The same effort on every model, so the eval compares like with like. Thinking counts
# against `detect_max_tokens`, which leaves room for it.
detect_effort: Final = "low"
detect_max_tokens: Final = 16000

# The follow-up paragraph (brief §7.3): the smaller model, as for detection. Bump the
# version on any change to the follow-up prompt's text (brief §7.6).
follow_up_model: Final = "claude-haiku-5-5"
follow_up_prompt_version: Final = "follow-up-v3"
follow_up_effort: Final = "low"
follow_up_max_tokens: Final = 4000

# USD per million input and output tokens for the models `eval` compares (brief §9), as
# Anthropic's pricing page listed them on `prices_checked`; verify against Anthropic's
# pricing page before quoting a cost. Haiku's price holds for prompts up to 100,000
# tokens; a transcript within `max_transcript_bytes` is about 50,000 at most.
prices_usd_per_mtok: Final = {
    detect_model: (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
}
prices_checked: Final = "2026-10-30"
# `--model` accepts only these, so every evaluated model has a price, and each names its
# report, `reports/eval_<model>.md`.
eval_models: Final = tuple(prices_usd_per_mtok)

# Per-call limits on the model client, so a hung or failing call can't stall a run.
request_timeout_s: Final = 60.0
max_retries: Final = 2

# Relative to the repository root, where the commands run (brief §8). Answers are
# recorded only for transcripts under `transcripts_dir` (brief §7.6).
themes_file: Final = Path("data/weaknesses.yaml")
transcripts_dir: Final = Path("data/transcripts")
labels_file: Final = Path("data/labels.yaml")
llm_cache_dir: Final = Path("data/llm_cache")
# The eval reports, committed (brief §9).
reports_dir: Final = Path("reports")
# Run outputs, gitignored: the briefs `notify` writes when no webhook is set, and the
# detections log (brief §7.4, §7.5).
briefs_dir: Final = Path("out/briefs")
log_db: Final = Path("out/detections.duckdb")

# A transcript's id is its file stem; it names files in `out/`, so `cli` and `notify`
# both check it against this (ob-security).
transcript_id_pattern: Final = re.compile(r"[A-Za-z0-9_-]{1,64}")

# A transcript larger than this is refused before it reaches a prompt (ob-security).
max_transcript_bytes: Final = 200_000


@dataclass(frozen=True, slots=True)
class Settings:
    """Values read from the environment once, when the CLI starts."""

    # Kept out of `repr`, so printing the settings never prints a secret. The webhook
    # URL is a credential: anyone holding it can post to the channel.
    anthropic_api_key: str | None = field(default=None, repr=False)
    slack_webhook_url: str | None = field(default=None, repr=False)


def load_settings() -> Settings:
    """Read the environment into `Settings`; an unset or empty variable becomes `None`."""
    return Settings(
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY") or None,
        slack_webhook_url=os.environ.get("SLACK_WEBHOOK_URL") or None,
    )
