"""The `objection-brief` command: parse arguments, wire the pipeline, print the result."""

from __future__ import annotations

import argparse
import functools
import json
import re
import sys
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from objection_brief import config, guardrail, llm
from objection_brief.detect import DetectError, RejectReason, detect
from objection_brief.weaknesses import WeaknessesError, load_themes

__all__ = ["main"]

# The transcript id names output files from step 6 on, so it is checked here (ob-security).
_TRANSCRIPT_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")

# What to do next, by error; the library errors state only what went wrong.
_HINTS: dict[type[Exception], str] = {
    llm.CacheMissError: "; run once without --offline to record it",
    llm.MissingKeyError: "; a live call loads it with `uv run --env-file .env objection-brief`",
}


class _InputError(Exception):
    pass


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command line and return the exit code."""
    parser = argparse.ArgumentParser(prog="objection-brief")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="detect and check the objections in one transcript")
    run.add_argument("transcript", type=Path, help="a .txt transcript, one speaker turn per line")
    run.add_argument(
        "--offline", action="store_true", help="replay recorded answers only; a miss is an error"
    )
    args = parser.parse_args(argv)
    return _run(args.transcript, offline=args.offline)


def _run(path: Path, *, offline: bool) -> int:
    try:
        transcript = _read_transcript(path)
        themes = load_themes(config.themes_file)
    # OSError: a file that can't be read, or a run from outside the repository root.
    except (_InputError, WeaknessesError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    cache = llm.Cache(
        directory=config.llm_cache_dir,
        offline=offline,
        record=llm.may_record(path, config.transcripts_dir),
        now=_utc_now,
    )
    call = functools.partial(
        llm.call_model, cache=cache, api_key=config.load_settings().anthropic_api_key
    )
    try:
        result = detect(transcript, themes, call=call, model=config.detect_model)
    except (llm.LLMError, DetectError) as error:
        print(f"{path.stem}: {error}{_HINTS.get(type(error), '')}", file=sys.stderr)
        return 1

    answer = result.answer
    source = "replayed" if answer.replayed else "live"
    print(
        f"{path.stem}: {config.detect_model}, {source}, recorded {answer.recorded_at}, "
        f"{answer.usage.input_tokens} input and {answer.usage.output_tokens} output tokens"
    )
    print(json.dumps(json.loads(answer.output), indent=2))
    verdicts = guardrail.check(result.detections, transcript, themes)
    for verdict in verdicts:
        d = verdict.detection
        status = "kept" if verdict.kept else f"rejected ({verdict.reject_reason})"
        print(f"{status}: {d.theme_id}, {d.speaker}, {d.confidence:.2f}: {d.quote!r}")
    for item in result.malformed:
        print(f"rejected ({RejectReason.MALFORMED_DETECTION}): {item.problem}: {item.item}")
    rejections = guardrail.count_rejections(verdicts) + Counter(
        {RejectReason.MALFORMED_DETECTION: len(result.malformed)}
    )
    kept = sum(v.kept for v in verdicts)
    counts = ", ".join(f"{reason} {n}" for reason, n in sorted(rejections.items()))
    print(f"{kept} kept, {rejections.total()} rejected" + (f" ({counts})" if counts else ""))
    return 0


def _read_transcript(path: Path) -> str:
    if path.suffix != ".txt" or not path.is_file():
        raise _InputError(f"{path} is not a .txt file")
    if not _TRANSCRIPT_ID.fullmatch(path.stem):
        raise _InputError(f"{path.name}: the name may use only letters, digits, _ and -")
    if path.stat().st_size > config.max_transcript_bytes:
        raise _InputError(f"{path} is larger than {config.max_transcript_bytes} bytes")
    try:
        return path.read_text("utf-8")
    except UnicodeDecodeError:
        raise _InputError(f"{path} is not UTF-8 text") from None


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
