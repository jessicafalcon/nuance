"""Detect prospect objections in a transcript as typed `Detection`s (brief §7.1)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

__all__ = ["Detection", "Speaker"]


class Speaker(StrEnum):
    """Who the model says spoke the quote; the values are the tool schema's enum."""

    PROSPECT = "prospect"
    REP = "rep"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Detection:
    """One objection the model reports, before the guardrail has checked it."""

    theme_id: str
    quote: str
    speaker: Speaker
    confidence: float
