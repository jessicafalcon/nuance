"""The guardrail keeps only verbatim prospect quotes on known themes above the threshold."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from objection_brief import config
from objection_brief.detect import Detection, Speaker
from objection_brief.guardrail import RejectReason, Verdict, check, count_rejections
from objection_brief.weaknesses import Theme, load_themes

DATA = Path(__file__).parents[1] / "data"
THEMES = load_themes(DATA / "weaknesses.yaml")
T11 = (DATA / "transcripts" / "t11_rep_raised_objection.txt").read_text("utf-8")
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


def _reason(
    detection: Detection,
    transcript: str = TRANSCRIPT,
    themes: tuple[Theme, ...] = THEMES,
    **threshold: float,
) -> RejectReason | None:
    (verdict,) = check([detection], transcript, themes, **threshold)
    return verdict.reject_reason


def test_exact_prospect_quote_is_kept() -> None:
    (verdict,) = check([_detection()], TRANSCRIPT, THEMES)
    assert verdict.kept


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


@pytest.mark.parametrize(
    ("quote", "transcript"),
    [
        pytest.param(
            "complicated", "PROSPECT: The setup is uncomplicated.\n", id="starts-mid-word"
        ),
        pytest.param("setting up permissions for every budget own", TRANSCRIPT, id="ends-mid-word"),
    ],
)
def test_quote_cut_inside_a_word_is_rejected(quote: str, transcript: str) -> None:
    assert _reason(_detection(quote=quote), transcript) is RejectReason.QUOTE_NOT_FOUND


def test_quote_cut_inside_a_word_on_the_prospect_line_is_rejected() -> None:
    # The whole word is on the rep's line, so only the single-turn check can catch it.
    transcript = "REP: Is it complicated?\n\nPROSPECT: No, it's uncomplicated.\n"
    detection = _detection(quote="complicated")
    assert _reason(detection, transcript) is RejectReason.QUOTE_NOT_PROSPECT


@pytest.mark.parametrize("speaker", [Speaker.REP, Speaker.UNKNOWN])
def test_speaker_other_than_prospect_is_rejected(speaker: Speaker) -> None:
    assert _reason(_detection(speaker=speaker)) is RejectReason.SPEAKER_NOT_PROSPECT


@pytest.mark.parametrize(
    ("quote", "transcript"),
    [
        pytest.param("every time you change who sees what", TRANSCRIPT, id="rep-line"),
        # t11's rep reads out the themes' signals and the prospect dismisses each one;
        # a model that misattributes the rep's line must not produce a brief.
        pytest.param("setting up permissions for every budget owner is painful", T11, id="t11"),
        pytest.param("who sees what? prospect: probably.", TRANSCRIPT, id="spans-two-turns"),
        pytest.param(
            "setting up permissions for every budget owner",
            "REP: Setting up permissions for every budget owner sounds painful.\n",
            id="no-prospect-lines",
        ),
    ],
)
def test_quote_outside_a_single_prospect_turn_is_rejected(quote: str, transcript: str) -> None:
    assert _reason(_detection(quote=quote), transcript) is RejectReason.QUOTE_NOT_PROSPECT


def test_confidence_threshold_comes_from_config() -> None:
    assert config.min_confidence == 0.6
    below = math.nextafter(config.min_confidence, 0.0)
    assert _reason(_detection(confidence=config.min_confidence)) is None
    assert _reason(_detection(confidence=below)) is RejectReason.LOW_CONFIDENCE


def test_threshold_can_be_overridden() -> None:
    detection = _detection(confidence=0.7)
    assert _reason(detection, min_confidence=0.8) is RejectReason.LOW_CONFIDENCE


@pytest.mark.parametrize("confidence", [-0.1, 1.01, math.nan, math.inf])
def test_confidence_outside_zero_to_one_is_rejected(confidence: float) -> None:
    assert _reason(_detection(confidence=confidence)) is RejectReason.INVALID_CONFIDENCE


@pytest.mark.parametrize(
    ("theme_id", "themes"),
    [
        pytest.param("onboarding_speed", THEMES, id="not-a-theme"),
        pytest.param(
            "access_permissions",
            tuple(t for t in THEMES if t.id != "access_permissions"),
            id="removed-from-the-file",
        ),
    ],
)
def test_unknown_theme_is_rejected(theme_id: str, themes: tuple[Theme, ...]) -> None:
    detection = _detection(theme_id=theme_id)
    assert _reason(detection, themes=themes) is RejectReason.UNKNOWN_THEME


@pytest.mark.parametrize(
    ("detection", "expected"),
    [
        pytest.param(
            _detection(quote="a sentence nobody said", speaker=Speaker.REP),
            RejectReason.QUOTE_NOT_FOUND,
            id="not-found-before-speaker",
        ),
        pytest.param(
            _detection(quote="who sees what", speaker=Speaker.REP),
            RejectReason.SPEAKER_NOT_PROSPECT,
            id="speaker-before-turn",
        ),
        pytest.param(
            _detection(quote="who sees what", confidence=math.nan),
            RejectReason.QUOTE_NOT_PROSPECT,
            id="turn-before-invalid-confidence",
        ),
        pytest.param(
            _detection(confidence=math.nan, theme_id="onboarding_speed"),
            RejectReason.INVALID_CONFIDENCE,
            id="invalid-before-low-confidence-and-theme",
        ),
        pytest.param(
            _detection(confidence=0.1, theme_id="onboarding_speed"),
            RejectReason.LOW_CONFIDENCE,
            id="low-confidence-before-theme",
        ),
    ],
)
def test_first_failing_check_names_the_reason(detection: Detection, expected: RejectReason) -> None:
    assert _reason(detection) is expected


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
