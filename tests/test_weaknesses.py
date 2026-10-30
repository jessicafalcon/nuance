"""The themes file loads, and a malformed one fails with the theme and field named."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from objection_brief.weaknesses import Theme, WeaknessesError, load_themes, parse_themes

THEMES_FILE = Path(__file__).parents[1] / "data" / "weaknesses.yaml"
FIELDS = [f.name for f in dataclasses.fields(Theme)]


def _entry(**overrides: object) -> dict[str, object]:
    entry: dict[str, object] = {
        "id": "sample_theme",
        "label": "Sample theme",
        "detect_when": "Prospect raises the sample concern.",
        "example_signals": ["this sounds like the sample concern"],
        "draft_answer": "Acknowledge the concern and explain the sample answer.",
        "proof": "[PLACEHOLDER] sample proof",
        "follow_up_angle": "Offer a sample session.",
        "status": "draft",
        "owner": "Product Marketing / Sales Enablement (to validate)",
    }
    entry.update(overrides)
    return entry


def _error(data: object) -> str:
    with pytest.raises(WeaknessesError) as excinfo:
        parse_themes(data, source="themes.yaml")
    return str(excinfo.value)


def test_committed_file_has_the_six_themes() -> None:
    themes = load_themes(THEMES_FILE)
    assert [t.id for t in themes] == [
        "access_permissions",
        "learning_curve_non_finance",
        "excel_interop",
        "performance_at_scale",
        "consolidation_depth",
        "pricing_escalation",
    ]


def test_committed_themes_are_drafts_with_placeholder_proofs() -> None:
    # No real customer story is in the repo: every proof stays a visible placeholder
    # until an approved one replaces it (ob-correctness, invariants 4 and 5).
    for theme in load_themes(THEMES_FILE):
        assert theme.status == "draft"
        assert "to validate" in theme.owner
        assert theme.proof.startswith("[PLACEHOLDER]"), theme.id


def test_valid_entry_parses_with_text_stripped() -> None:
    (theme,) = parse_themes([_entry(draft_answer="  Answer.\n")], source="themes.yaml")
    assert theme.draft_answer == "Answer."
    assert theme.example_signals == ("this sounds like the sample concern",)


def test_theme_is_immutable() -> None:
    (theme,) = parse_themes([_entry()], source="themes.yaml")
    with pytest.raises(dataclasses.FrozenInstanceError):
        theme.status = "approved"  # pyright: ignore[reportAttributeAccessIssue]


@pytest.mark.parametrize("field", [f for f in FIELDS if f != "id"])
def test_missing_field_names_theme_and_field(field: str) -> None:
    entry = _entry()
    del entry[field]
    assert _error([entry]) == f"themes.yaml: theme 'sample_theme': missing field {field!r}"


def test_missing_id_names_theme_by_position() -> None:
    entry = _entry()
    del entry["id"]
    assert _error([_entry(id="first"), entry]) == "themes.yaml: theme #2: missing field 'id'"


@pytest.mark.parametrize("value", ["", "   ", None, 3, ["a list"]])
def test_empty_or_non_string_field_is_rejected(value: object) -> None:
    message = _error([_entry(proof=value)])
    assert message == "themes.yaml: theme 'sample_theme': field 'proof' must be a non-empty string"


def test_duplicate_id_is_rejected() -> None:
    message = _error([_entry(), _entry()])
    assert message == "themes.yaml: theme 'sample_theme': duplicate id"


@pytest.mark.parametrize("bad_id", ["Has-Caps", "1starts_with_digit", "../escape", "with space"])
def test_id_outside_the_safe_shape_is_rejected(bad_id: str) -> None:
    assert "theme #1: field 'id' must match" in _error([_entry(id=bad_id)])


def test_status_other_than_draft_is_rejected() -> None:
    message = _error([_entry(status="approved")])
    assert message == "themes.yaml: theme 'sample_theme': field 'status' must be 'draft'"


def test_owner_without_validation_marker_is_rejected() -> None:
    message = _error([_entry(owner="Product Marketing")])
    assert message == "themes.yaml: theme 'sample_theme': field 'owner' must say 'to validate'"


def test_unknown_field_is_rejected() -> None:
    message = _error([_entry(follow_up_angel="typo")])
    assert message == "themes.yaml: theme 'sample_theme': unknown field 'follow_up_angel'"


@pytest.mark.parametrize("signals", [[], "a string, not a list", None])
def test_signals_must_be_a_non_empty_list(signals: object) -> None:
    message = _error([_entry(example_signals=signals)])
    assert message.endswith("field 'example_signals' must be a non-empty list")


def test_blank_signal_is_rejected() -> None:
    message = _error([_entry(example_signals=["fine", " "])])
    assert message.endswith("field 'example_signals' must be a non-empty string")


@pytest.mark.parametrize("data", [None, [], {"id": "not_a_list"}])
def test_top_level_must_be_a_non_empty_list(data: object) -> None:
    assert _error(data) == "themes.yaml: expected a non-empty list of themes"


def test_entry_must_be_a_mapping() -> None:
    assert _error(["just a string"]) == "themes.yaml: theme #1: expected a mapping of fields"


def test_invalid_yaml_names_the_file(tmp_path: Path) -> None:
    path = tmp_path / "weaknesses.yaml"
    path.write_text("- id: [unclosed\n", encoding="utf-8")
    with pytest.raises(WeaknessesError, match=r"weaknesses\.yaml: not valid YAML"):
        load_themes(path)
