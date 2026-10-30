"""Score kept detections against `data/labels.yaml` and render the eval report (brief §9)."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import yaml

from objection_brief import config, guardrail
from objection_brief.detect import Malformed, RejectReason
from objection_brief.guardrail import Verdict
from objection_brief.llm import ModelResult
from objection_brief.weaknesses import Theme

__all__ = ["Counts", "LabelsError", "Outcome", "load_labels", "render", "save_report", "score"]


class LabelsError(ValueError):
    """The labels file is malformed; the message names the transcript."""


@dataclass(frozen=True, slots=True)
class Outcome:
    """One transcript's detection answer, checked, with the themes its label expects."""

    transcript_id: str
    expected: frozenset[str]
    verdicts: tuple[Verdict, ...]
    malformed: tuple[Malformed, ...]
    answer: ModelResult
    # Why the answer was unusable as a whole; the transcript is then scored as finding
    # nothing, so a refusal counts against the model instead of vanishing.
    error: str | None = None

    @property
    def found(self) -> frozenset[str]:
        """The themes with at least one kept detection."""
        return frozenset(v.detection.theme_id for v in self.verdicts if v.kept)


@dataclass(frozen=True, slots=True)
class Counts:
    """Expected, found and correct (transcript, theme) pairs."""

    expected: int
    found: int
    correct: int

    @property
    def precision(self) -> float | None:
        """Correct over found; `None` when nothing was found."""
        return self.correct / self.found if self.found else None

    @property
    def recall(self) -> float | None:
        """Correct over expected; `None` when nothing was expected."""
        return self.correct / self.expected if self.expected else None


def load_labels(
    path: Path, *, stems: Collection[str], theme_ids: Collection[str]
) -> dict[str, frozenset[str]]:
    """Read the labels file: one entry per transcript stem, each a list of known theme ids.

    Raises:
        LabelsError: a stem is listed twice, missing or unknown, or a theme list is
            not a list of distinct known theme ids.
    """
    try:
        text = path.read_text("utf-8")
        root = yaml.SafeLoader(text).get_single_node()
        data: object = yaml.safe_load(text)
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise LabelsError(f"{path}: not valid UTF-8 YAML: {exc}") from exc
    if not isinstance(root, yaml.MappingNode) or not isinstance(data, dict):
        raise LabelsError(f"{path}: expected a mapping of transcript ids to theme lists")
    # `safe_load` keeps the last of two equal keys, so a transcript listed twice would be
    # scored against whichever entry came last; the composed nodes still hold both.
    keys = [key.value for key, _ in root.value]
    duplicate = next((k for i, k in enumerate(keys) if k in keys[:i]), None)
    if duplicate is not None:
        raise LabelsError(f"{path}: {duplicate!r} is listed twice")
    labels: dict[str, frozenset[str]] = {}
    for stem, themes in cast("dict[object, object]", data).items():
        where = f"{path}: {stem!r}"
        if not isinstance(themes, list) or not all(
            isinstance(item, str) for item in cast("list[object]", themes)
        ):
            raise LabelsError(f"{where}: expected a list of theme ids")
        names = cast("list[str]", themes)
        if unknown := sorted(set(names) - set(theme_ids)):
            raise LabelsError(f"{where}: unknown theme {', '.join(unknown)}")
        if len(set(names)) != len(names):
            raise LabelsError(f"{where}: a theme is listed twice")
        labels[str(stem)] = frozenset(names)
    if missing := sorted(set(stems) - labels.keys()):
        raise LabelsError(f"{path}: no labels for {', '.join(missing)}")
    if extra := sorted(labels.keys() - set(stems)):
        raise LabelsError(f"{path}: labels for {', '.join(extra)}, which has no transcript")
    return labels


def score(outcomes: Iterable[Outcome], theme_ids: Collection[str]) -> Counts:
    """Count the (transcript, theme) pairs for these themes; a theme found twice counts once.

    >>> score([], ["access_permissions"]).precision is None
    True
    """
    ids = frozenset(theme_ids)
    pairs = [(o.expected & ids, o.found & ids) for o in outcomes]
    return Counts(
        expected=sum(len(expected) for expected, _ in pairs),
        found=sum(len(found) for _, found in pairs),
        correct=sum(len(expected & found) for expected, found in pairs),
    )


def render(model: str, outcomes: Sequence[Outcome], themes: Sequence[Theme]) -> str:
    """Write the report as Markdown, from the outcomes alone, so a replay reproduces it.

    It reads no clock: its date is the latest `recorded_at` among the answers scored.
    """
    positives = sum(1 for o in outcomes if o.expected)
    recorded = max((o.answer.recorded_at for o in outcomes), default="none")[:10]
    lines = [
        f"# Detection eval: {model}",
        "",
        f"Generated by `objection-brief eval --model {model}` from the answers recorded in "
        f"`{config.llm_cache_dir}/` (prompt `{config.detect_prompt_version}`, answers recorded "
        f"{recorded}). {len(outcomes)} synthetic transcripts, {positives} with objections and "
        f"{len(outcomes) - positives} without, scored against `{config.labels_file}`. The test "
        "set is small and synthetic, so these numbers show that the method works, not its "
        "accuracy on real calls.",
        "",
        "## Precision and recall",
        "",
        "After the guardrail. Each (transcript, theme) pair counts once, however many kept "
        "detections name it.",
        "",
        "| Theme | Expected | Found | Correct | Precision | Recall |",
        "|---|---|---|---|---|---|",
    ]
    rows = [(t.id, score(outcomes, [t.id])) for t in themes]
    rows.append(("**Overall**", score(outcomes, [t.id for t in themes])))
    lines += [
        f"| {name} | {c.expected} | {c.found} | {c.correct} | {_ratio(c.precision)} | "
        f"{_ratio(c.recall)} |"
        for name, c in rows
    ]
    lines += ["", "## By transcript", "", "| Transcript | Expected | Kept | Result |"]
    lines.append("|---|---|---|---|")
    lines += [
        f"| {o.transcript_id} | {_themes(o.expected)} | {_themes(o.found)} | {_result(o)} |"
        for o in outcomes
    ]
    lines += ["", *_rejections(outcomes), "", *_cost(model, outcomes)]
    return "\n".join(lines) + "\n"


def save_report(text: str, directory: Path, model: str) -> Path:
    """Write `eval_<model>.md` in `directory` and return its path.

    Raises:
        ValueError: `model` is not one of `config.eval_models`, so it can't name a file.
    """
    # The pinned ids are the only names a report can take, so a `--model` value never
    # reaches a path unchecked (ob-security).
    if model not in config.eval_models:
        raise ValueError(f"{model!r} is not one of {config.eval_models}")
    path = directory / f"eval_{model}.md"
    directory.mkdir(parents=True, exist_ok=True)
    # Written beside the target and renamed over it, so a crash never leaves half a report;
    # a rename replaces a symlink at the path rather than writing through it.
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(text)
        Path(tmp).replace(path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path


def _rejections(outcomes: Sequence[Outcome]) -> list[str]:
    verdicts = [v for o in outcomes for v in o.verdicts]
    malformed = [m for o in outcomes for m in o.malformed]
    counts = guardrail.count_rejections(verdicts, malformed)
    lines = [
        "## Guardrail rejections",
        "",
        f"{len(verdicts) + len(malformed)} detections returned, "
        f"{sum(v.kept for v in verdicts)} kept, {counts.total()} rejected.",
    ]
    if not counts:
        return lines
    lines += ["", "| Reason | Count |", "|---|---|"]
    lines += [f"| {reason} | {counts[reason]} |" for reason in RejectReason if counts[reason]]
    lines += ["", "| Transcript | Theme | Reason |", "|---|---|---|"]
    for o in outcomes:
        lines += [
            f"| {o.transcript_id} | {v.detection.theme_id} | {v.reject_reason} |"
            for v in o.verdicts
            if v.reject_reason is not None
        ]
        lines += [
            f"| {o.transcript_id} | (malformed) | {RejectReason.MALFORMED_DETECTION} |"
            for _ in o.malformed
        ]
    return lines


def _cost(model: str, outcomes: Sequence[Outcome]) -> list[str]:
    prices = config.prices_usd_per_mtok[model]
    tokens = (
        sum(o.answer.usage.input_tokens for o in outcomes),
        sum(o.answer.usage.output_tokens for o in outcomes),
    )
    costs = [n * price / 1_000_000 for n, price in zip(tokens, prices, strict=True)]
    total = sum(costs)
    per_thousand = total / len(outcomes) * 1000 if outcomes else 0.0
    return [
        "## Tokens and cost",
        "",
        "| | Input | Output |",
        "|---|---|---|",
        f"| Tokens | {tokens[0]:,} | {tokens[1]:,} |",
        f"| USD per million tokens | {prices[0]:.2f} | {prices[1]:.2f} |",
        f"| Cost (USD) | {costs[0]:.4f} | {costs[1]:.4f} |",
        "",
        f"Estimated cost: ${total:.4f} for {len(outcomes)} transcripts, "
        f"${per_thousand:.2f} per 1,000 transcripts. One detection call per transcript. "
        f"Prices as listed on {config.prices_checked}; verify against Anthropic's pricing page.",
    ]


def _result(outcome: Outcome) -> str:
    if outcome.error is not None:
        return f"no usable answer: {_cell(outcome.error)}"
    missed = outcome.expected - outcome.found
    extra = outcome.found - outcome.expected
    parts = [f"missed {_themes(missed)}"] if missed else []
    parts += [f"false positive {_themes(extra)}"] if extra else []
    return "; ".join(parts) or "correct"


def _themes(ids: frozenset[str]) -> str:
    return ", ".join(sorted(ids)) or "none"


def _ratio(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def _cell(text: str) -> str:
    # A pipe or a line break would end the Markdown table cell early.
    return " ".join(text.replace("|", "\\|").split())
