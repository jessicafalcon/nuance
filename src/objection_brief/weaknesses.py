"""Load and validate the objection themes in `data/weaknesses.yaml`."""

from __future__ import annotations

import re
from dataclasses import dataclass, fields
from pathlib import Path
from typing import cast

import yaml

__all__ = ["Theme", "WeaknessesError", "load_themes", "parse_themes"]

# Theme ids become tool-schema enum values and log column values, so they are
# restricted to a shape that is safe in both.
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
        text = path.read_text(encoding="utf-8")
        _reject_duplicate_fields(yaml.SafeLoader(text).get_single_node(), str(path))
        data: object = yaml.safe_load(text)
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise WeaknessesError(f"{path}: not valid UTF-8 YAML: {exc}") from exc
    return parse_themes(data, source=str(path))


def _reject_duplicate_fields(root: yaml.Node | None, source: str) -> None:
    # `safe_load` keeps the last of two equal keys, so a pasted second `draft_answer`
    # would silently replace the first. Checking the composed nodes keeps `safe_load`
    # as the only constructor.
    if not isinstance(root, yaml.SequenceNode):
        return
    for position, item in enumerate(root.value, start=1):
        if isinstance(item, yaml.MappingNode):
            keys = [key.value for key, _ in item.value]
            duplicate = next((k for i, k in enumerate(keys) if k in keys[:i]), None)
            if duplicate is not None:
                raise WeaknessesError(f"{source}: theme #{position}: duplicate field {duplicate!r}")


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
    if "id" not in raw:
        raise WeaknessesError(f"{where}: missing field 'id'")
    theme_id = _text(raw["id"], where, "id")
    if not _ID_PATTERN.fullmatch(theme_id):
        raise WeaknessesError(f"{where}: field 'id' must match {_ID_PATTERN.pattern}")
    # From here on, errors name the theme by its id rather than its position.
    where = f"{source}: theme {theme_id!r}"

    unknown = next((key for key in raw if key not in _FIELDS), None)
    if unknown is not None:
        raise WeaknessesError(f"{where}: unknown field {str(unknown)!r}")
    missing = next((name for name in _FIELDS if name not in raw), None)
    if missing is not None:
        raise WeaknessesError(f"{where}: missing field {missing!r}")

    def text(name: str) -> str:
        return _text(raw[name], where, name)

    theme = Theme(
        id=theme_id,
        label=text("label"),
        detect_when=text("detect_when"),
        example_signals=_signals(raw["example_signals"], where),
        draft_answer=text("draft_answer"),
        proof=text("proof"),
        follow_up_angle=text("follow_up_angle"),
        status=text("status"),
        owner=text("owner"),
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
