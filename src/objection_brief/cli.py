"""The `objection-brief` command: parse arguments, wire the pipeline, print the result."""

from __future__ import annotations

import argparse
import functools
import sys
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from objection_brief import brief, config, guardrail, llm, log, notify
from objection_brief.detect import DetectError, RejectReason, detect
from objection_brief.weaknesses import Theme, WeaknessesError, load_themes

__all__ = ["main"]

# What to do next, by error; the library errors state only what went wrong.
_HINTS: dict[type[Exception], str] = {
    llm.CacheMissError: "; run once without --offline to record it",
    llm.MissingKeyError: "; a live call loads it with `uv run --env-file .env objection-brief`",
}

# Exit codes: a bad input outranks a failed pipeline step, which outranks success.
_OK, _FAILED, _BAD_INPUT = 0, 1, 2


class _InputError(Exception):
    pass


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command line and return the exit code."""
    parser = argparse.ArgumentParser(prog="objection-brief")
    commands = parser.add_subparsers(dest="command", required=True)
    offline = argparse.ArgumentParser(add_help=False)
    offline.add_argument(
        "--offline", action="store_true", help="replay recorded answers only; a miss is an error"
    )
    run = commands.add_parser(
        "run", parents=[offline], help="detect, check, brief, deliver and log one transcript"
    )
    run.add_argument("transcript", type=Path, help="a .txt transcript, one speaker turn per line")
    commands.add_parser(
        "run-all", parents=[offline], help=f"run every transcript in {config.transcripts_dir}/"
    )
    args = parser.parse_args(argv)
    if args.command == "run":
        return _run([args.transcript], offline=args.offline, summarize=False)
    # A fixed directory, not a user glob, so a run's size and cost are known (ob-security).
    return _run(sorted(config.transcripts_dir.glob("*.txt")), offline=args.offline, summarize=True)


def _run(paths: Sequence[Path], *, offline: bool, summarize: bool) -> int:
    try:
        themes = load_themes(config.themes_file)
    # OSError: a file that can't be read, or a run from outside the repository root.
    except (WeaknessesError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return _BAD_INPUT
    if not paths:
        print(f"error: no .txt transcripts in {config.transcripts_dir}", file=sys.stderr)
        return _BAD_INPUT
    settings = config.load_settings()
    run_id = uuid.uuid4().hex
    # One bad transcript is reported and the rest still run (ob-correctness, invariant 2).
    codes = {
        path.stem: _process(path, themes, settings=settings, offline=offline, run_id=run_id)
        for path in paths
    }
    if summarize:
        failed = [stem for stem, code in codes.items() if code != _OK]
        print(
            f"{len(codes)} transcripts, {len(failed)} failed"
            + (f" ({', '.join(failed)})" if failed else "")
        )
    return max(codes.values())


def _process(
    path: Path,
    themes: Sequence[Theme],
    *,
    settings: config.Settings,
    offline: bool,
    run_id: str,
) -> int:
    """Detect, check, log, brief and deliver one transcript; print what happened."""
    try:
        transcript = _read_transcript(path)
    except (_InputError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return _BAD_INPUT
    stem = path.stem
    cache = llm.Cache(
        directory=config.llm_cache_dir,
        offline=offline,
        record=llm.may_record(path, config.transcripts_dir),
        now=lambda: _utc_now().isoformat(timespec="seconds"),
    )
    call = functools.partial(llm.call_model, cache=cache, api_key=settings.anthropic_api_key)
    try:
        result = detect(transcript, themes, call=call, model=config.detect_model)
    except (llm.LLMError, DetectError) as error:
        print(f"{stem}: {error}{_HINTS.get(type(error), '')}", file=sys.stderr)
        return _FAILED

    answer = result.answer
    source = "replayed" if answer.replayed else "live"
    print(
        f"{stem}: {config.detect_model}, {source}, recorded {answer.recorded_at}, "
        f"{answer.usage.input_tokens} input and {answer.usage.output_tokens} output tokens"
    )
    verdicts = guardrail.check(result.detections, transcript, themes)
    for verdict in verdicts:
        d = verdict.detection
        status = "kept" if verdict.kept else f"rejected ({verdict.reject_reason})"
        print(f"  {status}: {d.theme_id}, {d.speaker}, {d.confidence:.2f}: {d.quote!r}")
    for item in result.malformed:
        print(f"  rejected ({RejectReason.MALFORMED_DETECTION}): {item.problem}: {item.item}")
    rejections = guardrail.count_rejections(verdicts, result.malformed)
    kept = sum(v.kept for v in verdicts)
    counts = ", ".join(f"{reason} {n}" for reason, n in sorted(rejections.items()))
    print(f"  {kept} kept, {rejections.total()} rejected" + (f" ({counts})" if counts else ""))

    # Logged before the brief, so a failed follow-up or delivery still leaves a record.
    rows = log.rows(
        run_id=run_id,
        transcript_id=stem,
        model=config.detect_model,
        usage=answer.usage,
        verdicts=verdicts,
        malformed=result.malformed,
        created_at=_utc_now(),
    )
    try:
        log.append(config.log_db, rows)
    except log.LogError as error:
        print(f"{stem}: {error}", file=sys.stderr)
        return _FAILED

    selected = brief.select(verdicts)
    if not selected:
        print("  no brief: nothing kept")
        return _OK
    by_id = {theme.id: theme for theme in themes}
    try:
        items = [
            brief.BriefItem(
                by_id[d.theme_id],
                d,
                brief.draft_follow_up(
                    by_id[d.theme_id], d.quote, call=call, model=config.follow_up_model
                ),
            )
            for d in selected
        ]
        message = brief.build_message(stem, items)
        where = notify.notify(
            message, stem, webhook_url=settings.slack_webhook_url, out_dir=config.briefs_dir
        )
    except (llm.LLMError, notify.NotifyError) as error:
        print(f"{stem}: {error}{_HINTS.get(type(error), '')}", file=sys.stderr)
        return _FAILED
    withheld = sum(item.follow_up.text is None for item in items)
    print(
        f"  brief {'posted to Slack' if where is None else f'written to {where}'}"
        + (f", {withheld} follow-up draft(s) withheld" if withheld else "")
    )
    print(notify.preview(message))
    return _OK


def _read_transcript(path: Path) -> str:
    if path.suffix != ".txt" or not path.is_file():
        raise _InputError(f"{path} is not a .txt file")
    # The id names output files, so it is checked before anything is read (ob-security).
    if not config.transcript_id_pattern.fullmatch(path.stem):
        raise _InputError(f"{path.name}: the name may use only letters, digits, _ and -")
    if path.stat().st_size > config.max_transcript_bytes:
        raise _InputError(f"{path} is larger than {config.max_transcript_bytes} bytes")
    try:
        return path.read_text("utf-8")
    except UnicodeDecodeError:
        raise _InputError(f"{path} is not UTF-8 text") from None


def _utc_now() -> datetime:
    return datetime.now(UTC)
