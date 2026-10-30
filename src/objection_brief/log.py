"""Append every detection, kept or rejected, to the DuckDB `detections` table (brief §7.5)."""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from objection_brief.detect import Malformed, RejectReason
from objection_brief.guardrail import Verdict
from objection_brief.llm import Usage

__all__ = ["LogError", "Row", "append", "rows"]

# The BigQuery types of brief §7.5, in the spelling DuckDB accepts: STRING, INT64 and
# BOOL are DuckDB aliases; FLOAT64 is not, and DOUBLE is the same 64-bit float. Keep the
# columns in sync with `Row`, whose field order the insert follows.
_DDL = """\
CREATE TABLE IF NOT EXISTS detections (
    run_id STRING NOT NULL,
    transcript_id STRING NOT NULL,
    theme_id STRING,
    quote STRING,
    confidence DOUBLE,
    model STRING NOT NULL,
    input_tokens INT64 NOT NULL,
    output_tokens INT64 NOT NULL,
    kept BOOL NOT NULL,
    reject_reason STRING,
    created_at TIMESTAMP NOT NULL
)"""
_INSERT = "INSERT INTO detections VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"


class LogError(Exception):
    """The detections could not be written; the message names the file."""


@dataclass(frozen=True, slots=True)
class Row:
    """One row of `detections`; a malformed item has no theme, quote or confidence."""

    run_id: str
    transcript_id: str
    theme_id: str | None
    quote: str | None
    confidence: float | None
    model: str
    # The usage of the detection call that produced the row, repeated on each of its
    # rows, so a sum over rows counts a call once per detection.
    input_tokens: int
    output_tokens: int
    kept: bool
    reject_reason: str | None
    # UTC without a zone, as a DuckDB TIMESTAMP stores it and BigQuery reads it.
    created_at: datetime


def rows(
    *,
    run_id: str,
    transcript_id: str,
    model: str,
    usage: Usage,
    verdicts: Iterable[Verdict],
    malformed: Iterable[Malformed],
    created_at: datetime,
) -> tuple[Row, ...]:
    """One row per verdict and per malformed item, so every detection is logged."""
    timestamp = created_at.astimezone(UTC).replace(tzinfo=None)

    def row(
        theme_id: str | None,
        quote: str | None,
        confidence: float | None,
        reject_reason: RejectReason | None,
    ) -> Row:
        return Row(
            run_id,
            transcript_id,
            theme_id,
            quote,
            confidence,
            model,
            usage.input_tokens,
            usage.output_tokens,
            kept=reject_reason is None,
            reject_reason=reject_reason,
            created_at=timestamp,
        )

    judged = [
        row(v.detection.theme_id, v.detection.quote, v.detection.confidence, v.reject_reason)
        for v in verdicts
    ]
    unparsed = [row(None, None, None, RejectReason.MALFORMED_DETECTION) for _ in malformed]
    return (*judged, *unparsed)


def append(path: Path, new_rows: Sequence[Row]) -> None:
    """Create the table if needed and append the rows in one statement.

    Raises:
        LogError: the database can't be opened or written, for example while another
            process holds it.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with duckdb.connect(str(path)) as db:
            db.execute(_DDL)
            if new_rows:
                db.executemany(_INSERT, [dataclasses.astuple(row) for row in new_rows])
    except (OSError, duckdb.Error) as error:
        raise LogError(f"{path}: could not append detections ({error})") from error
