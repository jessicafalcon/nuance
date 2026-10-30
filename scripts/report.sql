-- Objections by theme, kept and rejected, over every logged run (PROJECT-BRIEF.md §7.5).
-- Add `WHERE run_id = '...'` for one run. Malformed items have no theme.
-- Run: uv run python -c "import duckdb, pathlib; print(duckdb.connect('out/detections.duckdb', read_only=True).sql(pathlib.Path('scripts/report.sql').read_text()))"
SELECT
    coalesce(theme_id, '(malformed)') AS theme_id,
    count(*) FILTER (WHERE kept) AS kept,
    count(*) FILTER (WHERE NOT kept) AS rejected,
    count(*) AS total
FROM detections
GROUP BY 1
ORDER BY total DESC, theme_id;
