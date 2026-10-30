"""The committed transcripts and labels keep the shape the guardrail and the eval rely on."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from objection_brief.weaknesses import load_themes

DATA = Path(__file__).parents[1] / "data"
TRANSCRIPTS = sorted((DATA / "transcripts").glob("*.txt"))
LABELS: dict[str, list[str]] = yaml.safe_load((DATA / "labels.yaml").read_text("utf-8"))
THEME_IDS = {theme.id for theme in load_themes(DATA / "weaknesses.yaml")}


def test_labels_cover_exactly_the_transcripts() -> None:
    assert sorted(LABELS) == [path.stem for path in TRANSCRIPTS]


def test_label_themes_exist_in_the_themes_file() -> None:
    for stem, themes in LABELS.items():
        assert set(themes) <= THEME_IDS, stem


def test_positives_cover_every_theme() -> None:
    assert {theme for themes in LABELS.values() for theme in themes} == THEME_IDS


def test_eight_positives_and_four_negatives() -> None:
    positives = sum(1 for themes in LABELS.values() if themes)
    assert (positives, len(LABELS) - positives) == (8, 4)


@pytest.mark.parametrize("path", TRANSCRIPTS, ids=lambda path: path.stem)
def test_every_line_is_one_labelled_turn(path: Path) -> None:
    # The guardrail reads a prospect turn as one `PROSPECT:` line (brief §7.2), so a turn
    # wrapped onto an unlabelled line would put its quote outside the speaker check, and a
    # rep turn on a `PROSPECT:` line would pass the rep's words off as the prospect's.
    labels = ("REP: ", "PROSPECT: ")
    for number, line in enumerate(path.read_text("utf-8").splitlines(), start=1):
        if line.strip():
            where = f"{path.name}:{number}"
            assert line.startswith(labels), where
            _, _, turn = line.partition(": ")
            assert not any(label in turn for label in labels), where


@pytest.mark.parametrize("path", TRANSCRIPTS, ids=lambda path: path.stem)
def test_transcript_is_ascii_and_within_length(path: Path) -> None:
    text = path.read_text("utf-8")
    assert text.isascii()
    assert 300 <= len(text.split()) <= 1200
