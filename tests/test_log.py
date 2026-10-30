"""Every detection, kept, rejected or malformed, lands in `detections` with the brief §7.5 columns."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import duckdb
import pytest

from objection_brief import guardrail
from objection_brief.detect import Detection, Malformed, RejectReason, Speaker
from objection_brief.guardrail import Verdict
from objection_brief.llm import Usage
from objection_brief.log import LogError, Row, append, rows

REPORT = Path(__file__).parents[1] / "scripts" / "report.sql"
# Fixed, never read from the clock (ob-tests); 14:00 at UTC+2 is 12:00 UTC.
CREATED = datetime(2026, 10, 30, 14, 0, tzinfo=timezone(timedelta(hours=2)))
KEPT = Verdict(Detection("access_permissions", "sounds painful", Speaker.PROSPECT, 0.9), None)
REJECTED = Verdict(
    Detection("pricing_escalation", "it's fine", Speaker.REP, 0.7),
    RejectReason.SPEAKER_NOT_PROSPECT,
)
MALFORMED = Malformed('{"quote": 1}', "`theme_id` is missing or not a string")


def _rows() -> tuple[Row, ...]:
    return rows(
        run_id="run-1",
        transcript_id="t01_permissions",
        model="claude-haiku-5-5",
        usage=Usage(2572, 134),
        verdicts=[KEPT, REJECTED],
        malformed=[MALFORMED],
        created_at=CREATED,
    )


def test_kept_rejected_and_malformed_detections_land_with_their_columns(tmp_path: Path) -> None:
    db = tmp_path / "out" / "detections.duckdb"
    append(db, _rows())

    with duckdb.connect(str(db), read_only=True) as con:
        columns = con.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name = 'detections' ORDER BY ordinal_position"
        ).fetchall()
        got = con.execute(
            "SELECT * FROM detections ORDER BY kept DESC, theme_id NULLS LAST"
        ).fetchall()

    assert columns == [
        ("run_id", "VARCHAR"),
        ("transcript_id", "VARCHAR"),
        ("theme_id", "VARCHAR"),
        ("quote", "VARCHAR"),
        ("confidence", "DOUBLE"),
        ("model", "VARCHAR"),
        ("input_tokens", "BIGINT"),
        ("output_tokens", "BIGINT"),
        ("kept", "BOOLEAN"),
        ("reject_reason", "VARCHAR"),
        ("created_at", "TIMESTAMP"),
    ]
    # Stored as UTC without a zone, as a TIMESTAMP holds it.
    # Stored as UTC without a zone, as a TIMESTAMP holds it.
    utc_noon = datetime(2026, 10, 30, 12, 0, tzinfo=UTC).replace(tzinfo=None)
    head, usage = ("run-1", "t01_permissions"), ("claude-haiku-5-5", 2572, 134)
    assert got == [
        (*head, "access_permissions", "sounds painful", 0.9, *usage, True, None, utc_noon),
        (
            *head,
            "pricing_escalation",
            "it's fine",
            0.7,
            *usage,
            False,
            "speaker_not_prospect",
            utc_noon,
        ),
        (*head, None, None, None, *usage, False, "malformed_detection", utc_noon),
    ]


def test_logged_reasons_match_the_counted_rejections() -> None:
    # The log and the `run` output count the same rejections, malformed included.
    logged = Counter(
        row.reject_reason
        for row in rows(
            run_id="r",
            transcript_id="t",
            model="m",
            usage=Usage(1, 1),
            verdicts=[KEPT, REJECTED],
            malformed=[MALFORMED, MALFORMED],
            created_at=datetime(2026, 10, 30, tzinfo=UTC),
        )
        if row.reject_reason is not None
    )
    assert logged == guardrail.count_rejections([KEPT, REJECTED], [MALFORMED, MALFORMED])


def test_appends_accumulate_and_the_report_counts_by_theme(tmp_path: Path) -> None:
    db = tmp_path / "detections.duckdb"
    append(db, _rows())
    append(db, _rows())
    append(db, ())

    with duckdb.connect(str(db), read_only=True) as con:
        report = con.sql(REPORT.read_text("utf-8")).fetchall()
    assert sorted(report) == [
        ("(malformed)", 0, 2, 2),
        ("access_permissions", 2, 0, 2),
        ("pricing_escalation", 0, 2, 2),
    ]


def test_a_database_that_cannot_be_opened_is_a_log_error(tmp_path: Path) -> None:
    not_a_db = tmp_path / "detections.duckdb"
    not_a_db.write_text("not a database", "utf-8")
    with pytest.raises(LogError, match=r"detections\.duckdb: could not append"):
        append(not_a_db, _rows())
