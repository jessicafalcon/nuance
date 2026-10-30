"""Pipeline settings in one place: thresholds, models, prompt versions and the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Final

__all__ = [
    "Settings",
    "detect_effort",
    "detect_max_tokens",
    "detect_model",
    "detect_prompt_version",
    "load_settings",
    "max_retries",
    "max_transcript_bytes",
    "min_confidence",
    "request_timeout_s",
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

# Per-call limits on the model client, so a hung or failing call can't stall a run.
request_timeout_s: Final = 60.0
max_retries: Final = 2

# A transcript larger than this is refused before it reaches a prompt (ob-security).
max_transcript_bytes: Final = 200_000


@dataclass(frozen=True, slots=True)
class Settings:
    """Values read from the environment once, when the CLI starts."""

    # Kept out of `repr`, so printing the settings never prints the key.
    anthropic_api_key: str | None = field(default=None, repr=False)


def load_settings() -> Settings:
    """Read the environment into `Settings`; an unset or empty variable becomes `None`."""
    return Settings(anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY") or None)
