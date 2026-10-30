"""The eval loads strict labels, scores (transcript, theme) pairs and reports every miss and rejection."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from objection_brief import config
from objection_brief.detect import Detection, Malformed, RejectReason, Speaker
from objection_brief.evaluate import (
    LabelsError,
    Outcome,
    load_labels,
    render,
    save_report,
    score,
)
from objection_brief.guardrail import Verdict
from objection_brief.llm import ModelResult, Usage
from objection_brief.weaknesses import load_themes

THEMES = load_themes(Path(__file__).parents[1] / "data" / "weaknesses.yaml")
THEME_IDS = [t.id for t in THEMES]
STEMS = ["t01", "t02"]


def _labels(tmp_path: Path, text: str) -> dict[str, frozenset[str]]:
    path = tmp_path / "labels.yaml"
    path.write_text(text, "utf-8")
    return load_labels(path, stems=STEMS, theme_ids=THEME_IDS)


def test_labels_load_as_theme_sets(tmp_path: Path) -> None:
    labels = _labels(tmp_path, "t01: [access_permissions, excel_interop]\nt02: []\n")
    assert labels == {"t01": {"access_permissions", "excel_interop"}, "t02": frozenset()}


@pytest.mark.parametrize(
    ("text", "message"),
    [
        # `safe_load` alone would keep the second t01 and score against it.
        ("t01: [access_permissions]\nt02: []\nt01: []\n", "'t01' is listed twice"),
        ("t01: [access_permissions, access_permissions]\nt02: []\n", "a theme is listed twice"),
        ("t01: [made_up]\nt02: []\n", "unknown theme made_up"),
        ("t01:\nt02: []\n", "expected a list of theme ids"),
        ("t01: [1]\nt02: []\n", "expected a list of theme ids"),
        ("t01: []\n", "no labels for t02"),
        ("t01: []\nt02: []\nt03: []\n", "labels for t03, which has no transcript"),
        ("- t01\n", "expected a mapping"),
        ("", "expected a mapping"),
    ],
)
def test_malformed_labels_are_refused(tmp_path: Path, text: str, message: str) -> None:
    with pytest.raises(LabelsError, match=message):
        _labels(tmp_path, text)


def _detection(theme_id: str, confidence: float = 0.9) -> Detection:
    return Detection(theme_id, "a quote", Speaker.PROSPECT, confidence)


def _answer(recorded_at: str = "2026-10-30T12:00:00+00:00") -> ModelResult:
    return ModelResult("{}", "end_turn", Usage(1000, 100), recorded_at, replayed=True)


def _outcome(
    stem: str,
    expected: set[str],
    kept: list[str],
    rejected: Sequence[tuple[str, RejectReason]] = (),
    malformed: tuple[Malformed, ...] = (),
) -> Outcome:
    verdicts = [Verdict(_detection(t), None) for t in kept]
    verdicts += [Verdict(_detection(t), reason) for t, reason in rejected]
    return Outcome(stem, frozenset(expected), tuple(verdicts), malformed, _answer())


# Hand-computed: t01 is correct, its theme found twice; t02 misses excel_interop and
# finds pricing_escalation instead; t03 is a negative with a rejected detection only.
OUTCOMES = [
    _outcome("t01", {"access_permissions"}, ["access_permissions", "access_permissions"]),
    _outcome(
        "t02", {"access_permissions", "excel_interop"}, ["access_permissions", "pricing_escalation"]
    ),
    _outcome("t03", set(), [], [("excel_interop", RejectReason.LOW_CONFIDENCE)]),
]


@pytest.mark.parametrize(
    ("themes", "expected", "found", "correct", "precision", "recall"),
    [
        (["access_permissions"], 2, 2, 2, 1.0, 1.0),
        (["excel_interop"], 1, 0, 0, None, 0.0),
        # No positives: recall is undefined, and the one detection is a false positive.
        (["pricing_escalation"], 0, 1, 0, 0.0, None),
        (["performance_at_scale"], 0, 0, 0, None, None),
        (THEME_IDS, 3, 3, 2, 2 / 3, 2 / 3),
    ],
)
def test_score_counts_pairs_once(
    themes: list[str],
    expected: int,
    found: int,
    correct: int,
    precision: float | None,
    recall: float | None,
) -> None:
    counts = score(OUTCOMES, themes)
    assert (counts.expected, counts.found, counts.correct) == (expected, found, correct)
    assert (counts.precision, counts.recall) == (precision, recall)


def test_report_explains_every_miss_rejection_and_unusable_answer() -> None:
    # The committed Haiku report has none of these, so this is where their rendering is
    # pinned before a model's answers produce them.
    outcomes = [
        *OUTCOMES,
        _outcome("t04", set(), [], malformed=(Malformed("{}", "`quote` is missing"),)),
        Outcome(
            "t05",
            frozenset({"consolidation_depth"}),
            (),
            (),
            _answer("2026-11-02T09:00:00+00:00"),
            "the answer is not JSON (a | b\nc)",
        ),
    ]
    report = render(
        "claude-sonnet-5-5",
        outcomes,
        THEMES,
        prices={"claude-sonnet-5-5": (2.0, 10.0)},
        prices_checked="2026-10-30",
    )

    assert report.startswith("# Detection eval: claude-sonnet-5-5\n")
    assert "answers recorded 2026-11-02)" in report
    assert "5 synthetic transcripts, 3 with objections and 2 without" in report
    assert "| excel_interop | 1 | 0 | 0 | n/a | 0.00 |" in report
    assert "| pricing_escalation | 0 | 1 | 0 | 0.00 | n/a |" in report
    assert "| **Overall** | 4 | 3 | 2 | 0.67 | 0.50 |" in report
    assert "| t01 | access_permissions | access_permissions | correct |" in report
    assert (
        "| t02 | access_permissions, excel_interop | access_permissions, pricing_escalation "
        "| missed excel_interop; false positive pricing_escalation |"
    ) in report
    assert (
        "| t05 | consolidation_depth | none | no usable answer: the answer is not JSON (a \\| b c) |"
        in report
    )
    assert "6 detections returned, 4 kept, 2 rejected." in report
    # Reasons in `RejectReason` order, as brief §7.2 lists the checks.
    assert "| malformed_detection | 1 |\n| low_confidence | 1 |" in report
    assert "| t03 | excel_interop | low_confidence |" in report
    assert "| t04 | (malformed) | malformed_detection |" in report
    # Five answers of 1,000 input and 100 output tokens: 5,000 x $2 + 500 x $10 per million.
    assert "| Tokens | 5,000 | 500 |" in report
    assert "Estimated cost: $0.0150 for 5 transcripts, $3.00 per 1,000 transcripts." in report
    assert "Prices as listed on 2026-10-30; verify against Anthropic's pricing page." in report


def test_report_without_rejections_says_so() -> None:
    report = render("claude-haiku-5-5", OUTCOMES[:1], THEMES)
    assert "2 detections returned, 2 kept, 0 rejected.\n\n## Tokens and cost" in report


def test_report_is_saved_only_under_a_pinned_model(tmp_path: Path) -> None:
    path = save_report("text\n", tmp_path / "reports", "claude-haiku-5-5")
    assert path == tmp_path / "reports" / "eval_claude-haiku-5-5.md"
    assert path.read_text("utf-8") == "text\n"
    with pytest.raises(ValueError, match="is not one of"):
        save_report("text\n", tmp_path, "../claude-haiku-5-5")


def test_every_eval_model_has_a_price_and_the_detect_model_is_one() -> None:
    assert config.detect_model in config.eval_models
    assert set(config.prices_usd_per_mtok) == set(config.eval_models)
