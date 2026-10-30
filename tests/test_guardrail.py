"""The guardrail keeps only verbatim prospect quotes on known themes above the threshold."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from objection_brief import config
from objection_brief.detect import Detection, Speaker
from objection_brief.guardrail import RejectReason, Verdict, check, count_rejections
from objection_brief.weaknesses import load_themes

DATA = Path(__file__).parents[1] / "data"
THEMES = load_themes(DATA / "weaknesses.yaml")
TRANSCRIPT = (
    "REP: How do you handle access today?\n"
    "\n"
    'PROSPECT: Honestly, setting up permissions for every budget owner sounds "painful".\n'
    "\n"
    "REP: Will you need our team every time you change who sees what?\n"
    "\n"
    "PROSPECT: Probably. That worries me.\n"
)


def _detection(
    *,
    theme_id: str = "access_permissions",
    quote: str = "setting up permissions for every budget owner",
    speaker: Speaker = Speaker.PROSPECT,
    confidence: float = 0.9,
) -> Detection:
    return Detection(theme_id=theme_id, quote=quote, speaker=speaker, confidence=confidence)


def _reason(detection: Detection, transcript: str = TRANSCRIPT) -> RejectReason | None:
    (verdict,) = check([detection], transcript, THEMES)
    return verdict.reject_reason


def test_exact_prospect_quote_is_kept() -> None:
    (verdict,) = check([_detection()], TRANSCRIPT, THEMES)
    assert verdict.kept
    assert verdict.reject_reason is None


@pytest.mark.parametrize(
    "quote",
    [
        "  SETTING up   permissions\nfor every Budget Owner ",
        "sounds \N{LEFT DOUBLE QUOTATION MARK}painful\N{RIGHT DOUBLE QUOTATION MARK}.",
        'honestly, setting up permissions for every budget owner sounds "painful".',
    ],
)
def test_whitespace_case_and_curly_quotes_are_normalised(quote: str) -> None:
    assert _reason(_detection(quote=quote)) is None


def test_curly_quotes_in_the_transcript_match_straight_ones() -> None:
    transcript = (
        "PROSPECT: It\N{RIGHT SINGLE QUOTATION MARK}s a "
        "\N{LEFT DOUBLE QUOTATION MARK}blocker\N{RIGHT DOUBLE QUOTATION MARK} for us.\n"
    )
    assert _reason(_detection(quote='it\'s a "blocker"'), transcript) is None


@pytest.mark.parametrize(
    "quote",
    [
        "configuring permissions for each budget owner",
        "setting up permission for every budget owner",
        "",
        "   ",
    ],
)
def test_paraphrased_or_empty_quote_is_rejected(quote: str) -> None:
    assert _reason(_detection(quote=quote)) is RejectReason.QUOTE_NOT_FOUND


@pytest.mark.parametrize("speaker", [Speaker.REP, Speaker.UNKNOWN])
def test_speaker_other_than_prospect_is_rejected(speaker: Speaker) -> None:
    assert _reason(_detection(speaker=speaker)) is RejectReason.SPEAKER_NOT_PROSPECT


def test_rep_words_labelled_as_prospect_are_rejected() -> None:
    quote = "every time you change who sees what"
    assert _reason(_detection(quote=quote)) is RejectReason.QUOTE_NOT_PROSPECT


def test_rep_raised_objection_in_t11_is_rejected_when_labelled_prospect() -> None:
    # t11's rep reads out the themes' signals and the prospect dismisses each one; a
    # model that misattributes the rep's line must not produce a brief.
    transcript = (DATA / "transcripts" / "t11_rep_raised_objection.txt").read_text("utf-8")
    quote = "setting up permissions for every budget owner is painful"
    assert _reason(_detection(quote=quote), transcript) is RejectReason.QUOTE_NOT_PROSPECT


def test_quote_spanning_two_turns_is_rejected() -> None:
    quote = "who sees what? prospect: probably."
    assert _reason(_detection(quote=quote)) is RejectReason.QUOTE_NOT_PROSPECT


def test_transcript_without_prospect_lines_keeps_nothing() -> None:
    transcript = "REP: Setting up permissions for every budget owner sounds painful.\n"
    assert _reason(_detection(), transcript) is RejectReason.QUOTE_NOT_PROSPECT


def test_confidence_threshold_comes_from_config() -> None:
    assert config.min_confidence == 0.6
    assert _reason(_detection(confidence=0.6)) is None
    assert _reason(_detection(confidence=0.59)) is RejectReason.LOW_CONFIDENCE


def test_threshold_can_be_overridden() -> None:
    (verdict,) = check([_detection(confidence=0.7)], TRANSCRIPT, THEMES, min_confidence=0.8)
    assert verdict.reject_reason is RejectReason.LOW_CONFIDENCE


@pytest.mark.parametrize("confidence", [-0.1, 1.01, math.nan, math.inf])
def test_confidence_outside_zero_to_one_is_rejected(confidence: float) -> None:
    assert _reason(_detection(confidence=confidence)) is RejectReason.INVALID_CONFIDENCE


def test_unknown_theme_is_rejected() -> None:
    assert _reason(_detection(theme_id="onboarding_speed")) is RejectReason.UNKNOWN_THEME


def test_theme_removed_from_the_file_is_rejected() -> None:
    themes = tuple(t for t in THEMES if t.id != "access_permissions")
    (verdict,) = check([_detection()], TRANSCRIPT, themes)
    assert verdict.reject_reason is RejectReason.UNKNOWN_THEME


def test_first_failing_check_names_the_reason() -> None:
    detection = _detection(speaker=Speaker.REP, confidence=0.1, theme_id="onboarding_speed")
    assert _reason(detection) is RejectReason.SPEAKER_NOT_PROSPECT


def test_every_detection_gets_a_verdict_in_order() -> None:
    detections = [
        _detection(),
        _detection(quote="a sentence nobody said"),
        _detection(),
        _detection(confidence=0.2),
    ]
    verdicts = check(detections, TRANSCRIPT, THEMES)
    assert [v.detection for v in verdicts] == detections
    assert [v.kept for v in verdicts] == [True, False, True, False]


def test_rejections_are_counted_by_reason() -> None:
    kept = Verdict(_detection(), None)
    verdicts = [
        kept,
        Verdict(_detection(), RejectReason.LOW_CONFIDENCE),
        Verdict(_detection(), RejectReason.QUOTE_NOT_FOUND),
        Verdict(_detection(), RejectReason.LOW_CONFIDENCE),
    ]
    assert count_rejections(verdicts) == {
        RejectReason.LOW_CONFIDENCE: 2,
        RejectReason.QUOTE_NOT_FOUND: 1,
    }
    assert count_rejections([kept]) == {}
