"""The `objection-brief` command: parse arguments, wire the pipeline, print the result."""

from __future__ import annotations

import argparse
import functools
import sys
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from objection_brief import brief, config, evaluate, guardrail, llm, log, notify
from objection_brief.detect import DetectError, DetectResult, RejectReason, detect
from objection_brief.guardrail import Verdict
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
    evaluation = commands.add_parser(
        "eval", parents=[offline], help=f"score detection against {config.labels_file}"
    )
    evaluation.add_argument("--model", required=True, choices=config.eval_models)
    args = parser.parse_args(argv)
    if args.command == "eval":
        return _eval(args.model, offline=args.offline)
    if args.command == "run":
        codes = _run([args.transcript], offline=args.offline)
        return max(codes.values(), default=_BAD_INPUT)
    # A fixed directory, not a user glob, so a run's size and cost are known (ob-security).
    codes = _run(sorted(config.transcripts_dir.glob("*.txt")), offline=args.offline)
    if codes:
        failed = [stem for stem, code in codes.items() if code != _OK]
        print(
            f"{len(codes)} transcripts, {len(failed)} failed"
            + (f" ({', '.join(failed)})" if failed else "")
        )
    return max(codes.values(), default=_BAD_INPUT)


def _run(paths: Sequence[Path], *, offline: bool) -> dict[str, int]:
    """Process each transcript and return its exit code by id; empty when nothing could run."""
    settings = config.load_settings()
    try:
        themes = load_themes(config.themes_file)
        if settings.slack_webhook_url is not None:
            # Before any model call, so a mistyped URL costs nothing.
            notify.check_webhook(settings.slack_webhook_url)
        if not paths:
            raise _InputError(f"no .txt transcripts in {config.transcripts_dir}")
    # OSError: a file that can't be read, or a run from outside the repository root.
    except (_InputError, WeaknessesError, notify.NotifyError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return {}
    run_id = uuid.uuid4().hex
    # One bad transcript is reported and the rest still run (ob-correctness, invariant 2).
    return {
        path.stem: _process(path, themes, settings=settings, offline=offline, run_id=run_id)
        for path in paths
    }


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
    call = _call(path, settings=settings, offline=offline)
    try:
        result = detect(transcript, themes, call=call, model=config.detect_model)
    except (llm.LLMError, DetectError) as error:
        return _fail(stem, error)
    verdicts = guardrail.check(result.detections, transcript, themes)
    _print_verdicts(stem, result, verdicts)

    # Logged before the brief, so a failed follow-up or delivery still leaves a record.
    rows = log.rows(
        run_id=run_id,
        transcript_id=stem,
        model=config.detect_model,
        usage=result.answer.usage,
        verdicts=verdicts,
        malformed=result.malformed,
        created_at=datetime.now(UTC),
    )
    try:
        log.append(config.log_db, rows)
        entries = brief.items(verdicts, themes, call=call, model=config.follow_up_model)
        if not entries:
            print("  no brief: nothing kept")
            return _OK
        message = brief.build_message(stem, entries)
        where = notify.notify(
            message, stem, webhook_url=settings.slack_webhook_url, out_dir=config.briefs_dir
        )
    except (log.LogError, llm.LLMError, notify.NotifyError) as error:
        return _fail(stem, error)
    withheld = sum(entry.follow_up.problem is not None for entry in entries)
    tokens = (
        sum(e.follow_up.usage.input_tokens for e in entries),
        sum(e.follow_up.usage.output_tokens for e in entries),
    )
    print(
        f"  brief {'posted to Slack' if where is None else f'written to {where}'}, "
        f"{len(entries)} follow-up call(s), {tokens[0]} input and {tokens[1]} output tokens"
        + (f", {withheld} draft(s) withheld" if withheld else "")
    )
    print(brief.preview(message))
    return _OK


def _eval(model: str, *, offline: bool) -> int:
    """Detect and check every transcript with `model`, then score, print and save the report."""
    settings = config.load_settings()
    # A fixed directory, as for `run-all`, so the test set and its cost are known.
    paths = sorted(config.transcripts_dir.glob("*.txt"))
    try:
        themes = load_themes(config.themes_file)
        labels = evaluate.load_labels(
            config.labels_file, stems=[p.stem for p in paths], theme_ids=[t.id for t in themes]
        )
        transcripts = {path: _read_transcript(path) for path in paths}
    except (_InputError, WeaknessesError, evaluate.LabelsError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return _BAD_INPUT
    outcomes: list[evaluate.Outcome] = []
    for path, transcript in transcripts.items():
        stem = path.stem
        call = _call(path, settings=settings, offline=offline)
        try:
            result = detect(transcript, themes, call=call, model=model)
        except DetectError as error:
            # `detect` always attaches the answer; without it the report can't count its cost.
            if error.answer is None:
                raise
            # A recorded unusable answer is the model's result: scored as finding nothing.
            print(f"{stem}: {error}", file=sys.stderr)
            outcomes.append(evaluate.Outcome(stem, labels[stem], (), (), error.answer, str(error)))
            continue
        except llm.LLMError as error:
            # A missing recording or a failed call says nothing about the model, so no
            # report is written rather than one scored on part of the test set.
            _fail(stem, error)
            print(f"error: no report written for {model}", file=sys.stderr)
            return _FAILED
        verdicts = guardrail.check(result.detections, transcript, themes)
        outcomes.append(
            evaluate.Outcome(stem, labels[stem], verdicts, result.malformed, result.answer)
        )
    report = evaluate.render(model, outcomes, themes)
    try:
        path = evaluate.save_report(report, config.reports_dir, model)
    except OSError as error:
        print(f"error: {error}", file=sys.stderr)
        return _FAILED
    print(report, end="")
    print(f"report written to {path}")
    return _OK


def _call(path: Path, *, settings: config.Settings, offline: bool) -> llm.ModelCall:
    """The model call for one transcript, recording only if the transcript may be committed."""
    cache = llm.Cache(
        directory=config.llm_cache_dir,
        offline=offline,
        record=llm.may_record(path, config.transcripts_dir),
        now=lambda: datetime.now(UTC).isoformat(timespec="seconds"),
    )
    return functools.partial(llm.call_model, cache=cache, api_key=settings.anthropic_api_key)


def _print_verdicts(stem: str, result: DetectResult, verdicts: Sequence[Verdict]) -> None:
    answer = result.answer
    source = "replayed" if answer.replayed else "live"
    print(
        f"{stem}: {config.detect_model}, {source}, recorded {answer.recorded_at}, "
        f"{answer.usage.input_tokens} input and {answer.usage.output_tokens} output tokens"
    )
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


def _fail(stem: str, error: Exception) -> int:
    print(f"{stem}: {error}{_HINTS.get(type(error), '')}", file=sys.stderr)
    return _FAILED


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
