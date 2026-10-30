"""Pipeline settings: thresholds, model IDs and price constants, in one place."""

from __future__ import annotations

from typing import Final

__all__ = ["min_confidence"]

# The guardrail rejects a detection the model scores below this (brief §7.2).
min_confidence: Final = 0.6
