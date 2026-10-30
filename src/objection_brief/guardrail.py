"""Keep a detection only if its quote, speaker, confidence and theme pass brief §7.2."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from objection_brief import config
from objection_brief.detect import Detection, Speaker
from objection_brief.weaknesses import Theme

__all__ = ["RejectReason", "Verdict", "check", "count_rejections", "normalize"]

# Typographic quotes become their ASCII forms, so a quote matches whether the model or
# the transcript uses curly or straight ones.
_QUOTES = str.maketrans(
    {
        "\N{LEFT SINGLE QUOTATION MARK}": "'",
        "\N{RIGHT SINGLE QUOTATION MARK}": "'",
        "\N{SINGLE LOW-9 QUOTATION MARK}": "'",
        "\N{SINGLE HIGH-REVERSED-9 QUOTATION MARK}": "'",
        "\N{LEFT DOUBLE QUOTATION MARK}": '"',
        "\N{RIGHT DOUBLE QUOTATION MARK}": '"',
        "\N{DOUBLE LOW-9 QUOTATION MARK}": '"',
        "\N{DOUBLE HIGH-REVERSED-9 QUOTATION MARK}": '"',
    }
)
# Compared after `normalize`, so the label matches in any case.
_PROSPECT_LABEL = "prospect:"


class RejectReason(StrEnum):
    """Why the guardrail rejected a detection; stored as the log's `reject_reason`."""

    QUOTE_NOT_FOUND = "quote_not_found"
    SPEAKER_NOT_PROSPECT = "speaker_not_prospect"
    QUOTE_NOT_PROSPECT = "quote_not_prospect"
    INVALID_CONFIDENCE = "invalid_confidence"
    LOW_CONFIDENCE = "low_confidence"
    UNKNOWN_THEME = "unknown_theme"


@dataclass(frozen=True, slots=True)
class Verdict:
    """A detection and the reason it was rejected, or `None` when it was kept."""

    detection: Detection
    reject_reason: RejectReason | None

    @property
    def kept(self) -> bool:
        """Whether the detection passed every check and may reach a brief."""
        return self.reject_reason is None


def normalize(text: str) -> str:
    r"""Fold whitespace, typographic quotes and case; both sides of a match use it.

    It never fuzzes the match: a paraphrase still fails (ob-correctness, invariant 1).

    >>> normalize("  \N{LEFT DOUBLE QUOTATION MARK}Setting up\nPermissions\N{RIGHT DOUBLE QUOTATION MARK} ")
    '"setting up permissions"'
    """
    return " ".join(text.translate(_QUOTES).casefold().split())


def check(
    detections: Iterable[Detection],
    transcript: str,
    themes: Iterable[Theme],
    *,
    min_confidence: float = config.min_confidence,
) -> tuple[Verdict, ...]:
    """Return one verdict per detection, in order, named by the first check that fails.

    The checks run in the order of `RejectReason`. A quote must sit inside one
    `PROSPECT:` turn as well as in the transcript, so a rep's words the model labels
    as the prospect's are rejected (`QUOTE_NOT_PROSPECT`), as is a quote that spans
    two turns.
    """
    whole = normalize(transcript)
    prospect_turns = _prospect_turns(transcript)
    theme_ids = {theme.id for theme in themes}

    def reason(detection: Detection) -> RejectReason | None:
        quote = normalize(detection.quote)
        # An empty quote is a substring of every transcript, so it is checked first.
        if not quote or quote not in whole:
            return RejectReason.QUOTE_NOT_FOUND
        if detection.speaker != Speaker.PROSPECT:
            return RejectReason.SPEAKER_NOT_PROSPECT
        if not any(quote in turn for turn in prospect_turns):
            return RejectReason.QUOTE_NOT_PROSPECT
        # Written so that NaN fails too.
        if not 0.0 <= detection.confidence <= 1.0:
            return RejectReason.INVALID_CONFIDENCE
        if detection.confidence < min_confidence:
            return RejectReason.LOW_CONFIDENCE
        if detection.theme_id not in theme_ids:
            return RejectReason.UNKNOWN_THEME
        return None

    return tuple(Verdict(detection, reason(detection)) for detection in detections)


def count_rejections(verdicts: Iterable[Verdict]) -> Counter[RejectReason]:
    """Count rejected verdicts by reason, for the `run` output and the eval report."""
    return Counter(v.reject_reason for v in verdicts if v.reject_reason is not None)


def _prospect_turns(transcript: str) -> tuple[str, ...]:
    # Transcripts hold one speaker turn per line (brief §6.2), so a prospect turn is a
    # line labelled `PROSPECT:`, without its label.
    lines = (normalize(line) for line in transcript.splitlines())
    return tuple(
        line.removeprefix(_PROSPECT_LABEL).lstrip()
        for line in lines
        if line.startswith(_PROSPECT_LABEL)
    )
