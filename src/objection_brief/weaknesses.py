"""Load and validate the objection themes in `data/weaknesses.yaml`."""

from __future__ import annotations

import re
from dataclasses import dataclass, fields
from pathlib import Path
from typing import cast

import yaml

__all__ = ["Theme", "WeaknessesError", "load_themes", "parse_themes"]

# Theme ids become tool-schema enum values, log columns and parts of output names, so
# they are restricted to a shape that is safe in all three.
_ID_PATTERN = re.compile(r"[a-z][a-z0-9_]*")
# Until Product Marketing and Sales Enablement approve an answer, every theme stays a
# draft with its owner marked for validation (ob-correctness, invariant 5).
_REQUIRED_STATUS = "draft"
_OWNER_MARKER = "to validate"


class WeaknessesError(ValueError):
    """The themes file is malformed; the message names the theme and the field."""


@dataclass(frozen=True, slots=True)
class Theme:
    """One objection theme with its approved draft answer."""

    id: str
    label: str
    detect_when: str
    example_signals: tuple[str, ...]
    draft_answer: str
    proof: str
    follow_up_angle: str
    status: str
    owner: str


_FIELDS = tuple(f.name for f in fields(Theme))


def load_themes(path: Path) -> tuple[Theme, ...]:
    """Read a themes file and return its validated themes, in file order."""
    try:
        data: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise WeaknessesError(f"{path}: not valid YAML: {exc}") from exc
    return parse_themes(data, source=str(path))


def parse_themes(data: object, *, source: str) -> tuple[Theme, ...]:
    """Validate parsed YAML as a list of themes; `source` prefixes every error."""
    if not isinstance(data, list) or not data:
        raise WeaknessesError(f"{source}: expected a non-empty list of themes")
    themes: list[Theme] = []
    seen: set[str] = set()
    for position, entry in enumerate(cast("list[object]", data), start=1):
        theme = _parse_theme(entry, source, position)
        if theme.id in seen:
            raise WeaknessesError(f"{source}: theme {theme.id!r}: duplicate id")
        seen.add(theme.id)
        themes.append(theme)
    return tuple(themes)


def _parse_theme(entry: object, source: str, position: int) -> Theme:
    where = f"{source}: theme #{position}"
    if not isinstance(entry, dict):
        raise WeaknessesError(f"{where}: expected a mapping of fields")
    raw = cast("dict[object, object]", entry)
    # Name the theme by its id as soon as the id is usable, so later errors point at it.
    if "id" in raw:
        theme_id = _text(raw["id"], where, "id")
        if not _ID_PATTERN.fullmatch(theme_id):
            raise WeaknessesError(f"{where}: field 'id' must match {_ID_PATTERN.pattern}")
        where = f"{source}: theme {theme_id!r}"

    unknown = sorted(str(key) for key in raw if key not in _FIELDS)
    if unknown:
        raise WeaknessesError(f"{where}: unknown field {unknown[0]!r}")
    missing = [name for name in _FIELDS if name not in raw]
    if missing:
        raise WeaknessesError(f"{where}: missing field {missing[0]!r}")

    theme = Theme(
        id=_text(raw["id"], where, "id"),
        label=_text(raw["label"], where, "label"),
        detect_when=_text(raw["detect_when"], where, "detect_when"),
        example_signals=_signals(raw["example_signals"], where),
        draft_answer=_text(raw["draft_answer"], where, "draft_answer"),
        proof=_text(raw["proof"], where, "proof"),
        follow_up_angle=_text(raw["follow_up_angle"], where, "follow_up_angle"),
        status=_text(raw["status"], where, "status"),
        owner=_text(raw["owner"], where, "owner"),
    )
    if theme.status != _REQUIRED_STATUS:
        raise WeaknessesError(f"{where}: field 'status' must be {_REQUIRED_STATUS!r}")
    if _OWNER_MARKER not in theme.owner:
        raise WeaknessesError(f"{where}: field 'owner' must say {_OWNER_MARKER!r}")
    return theme


def _text(value: object, where: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WeaknessesError(f"{where}: field {name!r} must be a non-empty string")
    return value.strip()


def _signals(value: object, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise WeaknessesError(f"{where}: field 'example_signals' must be a non-empty list")
    return tuple(_text(item, where, "example_signals") for item in cast("list[object]", value))
