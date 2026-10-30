"""The one function that calls a model, replaying recorded answers from a cache (brief §7.6)."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import anthropic

from objection_brief import config

__all__ = [
    "Cache",
    "CacheEntryError",
    "CacheMissError",
    "ModelCall",
    "ModelCallError",
    "ModelResult",
    "Usage",
    "cache_key",
    "call_model",
    "may_record",
]

type JSON = dict[str, Any]


class CacheMissError(Exception):
    """An offline run asked for an answer that was never recorded."""


class CacheEntryError(Exception):
    """A recorded answer can't be read; the message names the file."""


class ModelCallError(Exception):
    """A live call failed, or could not be made."""


@dataclass(frozen=True, slots=True)
class Usage:
    """Tokens a call used; a replayed call reports the recorded figures."""

    input_tokens: int
    output_tokens: int


@dataclass(frozen=True, slots=True)
class ModelResult:
    """A model answer, live or replayed."""

    output: str
    stop_reason: str | None
    usage: Usage
    recorded_at: str
    replayed: bool


@dataclass(frozen=True, slots=True)
class Cache:
    """Where answers are recorded, and whether a miss may call the API or record."""

    directory: Path
    offline: bool
    record: bool
    # The UTC time stamped on a new entry; the CLI passes the clock, tests a constant.
    now: Callable[[], str]


class ModelCall(Protocol):
    """`call_model` with its cache and key bound, as `detect` and `brief` receive it."""

    def __call__(
        self,
        *,
        model: str,
        prompt_version: str,
        system: str,
        messages: list[JSON],
        output_config: JSON,
        max_tokens: int,
    ) -> ModelResult: ...


def cache_key(request: JSON, prompt_version: str) -> str:
    """Hash every input that shapes the answer into the entry's file name.

    >>> len(cache_key({"model": "m"}, "v1"))
    64
    """
    canonical = json.dumps(
        {**request, "prompt_version": prompt_version}, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def may_record(transcript: Path, transcripts_dir: Path) -> bool:
    """Whether answers for this transcript may be committed (brief §7.6, privacy).

    Only files under `transcripts_dir`, which holds synthetic transcripts, qualify;
    symlinks are followed first, so a link there to a file elsewhere does not.
    """
    return transcript.resolve().is_relative_to(transcripts_dir.resolve())


def call_model(
    *,
    model: str,
    prompt_version: str,
    system: str,
    messages: list[JSON],
    output_config: JSON,
    max_tokens: int,
    cache: Cache,
    api_key: str | None,
) -> ModelResult:
    """Replay the recorded answer for this request, or call the API and record it.

    Raises:
        CacheMissError: offline, and nothing is recorded for the request.
        CacheEntryError: the recorded entry is unreadable.
        ModelCallError: the live call failed or there is no API key.
    """
    request: JSON = {
        "model": model,
        "system": system,
        "messages": messages,
        "output_config": output_config,
        "max_tokens": max_tokens,
    }
    key = cache_key(request, prompt_version)
    path = cache.directory / f"{key}.json"
    if path.exists():
        return _replay(path)
    if cache.offline:
        raise CacheMissError(
            f"no recorded answer for key {key} (prompt {prompt_version}); "
            "run once without --offline to record it"
        )
    if not api_key:
        raise ModelCallError(
            "ANTHROPIC_API_KEY is not set; run with `uv run --env-file .env objection-brief ...`"
        )
    output, stop_reason, usage = _send(request, api_key)
    result = ModelResult(output, stop_reason, usage, recorded_at=cache.now(), replayed=False)
    if cache.record:
        _write(path, prompt_version, request, result)
    return result


def _send(request: JSON, api_key: str) -> tuple[str, str | None, Usage]:
    client = anthropic.Anthropic(
        api_key=api_key, timeout=config.request_timeout_s, max_retries=config.max_retries
    )
    try:
        message = client.messages.create(
            model=request["model"],
            system=request["system"],
            messages=request["messages"],
            output_config=request["output_config"],
            max_tokens=request["max_tokens"],
        )
    except anthropic.APIError as error:
        raise ModelCallError(f"model call failed: {type(error).__name__}: {error}") from error
    # Thinking blocks come first and carry no answer; the JSON is in the text blocks.
    output = "".join(block.text for block in message.content if block.type == "text")
    usage = Usage(message.usage.input_tokens, message.usage.output_tokens)
    return output, message.stop_reason, usage


def _write(path: Path, prompt_version: str, request: JSON, result: ModelResult) -> None:
    # The entry holds the request and the answer, never the API key or a header. The file
    # name is the cache key, so the entry doesn't repeat it (gitleaks reads a 64-hex value
    # under "key" as a secret).
    entry = {
        "prompt_version": prompt_version,
        "request": request,
        "output": result.output,
        "stop_reason": result.stop_reason,
        "usage": {
            "input_tokens": result.usage.input_tokens,
            "output_tokens": result.usage.output_tokens,
        },
        "recorded_at": result.recorded_at,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    # Written beside the target and renamed over it, so a crash never leaves half an entry.
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(json.dumps(entry, indent=2, sort_keys=True) + "\n")
        Path(tmp).replace(path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _replay(path: Path) -> ModelResult:
    try:
        entry: Any = json.loads(path.read_text("utf-8"))
        output, stop_reason, recorded_at = (
            entry["output"],
            entry["stop_reason"],
            entry["recorded_at"],
        )
        tokens = (entry["usage"]["input_tokens"], entry["usage"]["output_tokens"])
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise CacheEntryError(f"{path}: unreadable cache entry ({error})") from error
    if not (
        isinstance(output, str)
        and isinstance(stop_reason, str | None)
        and isinstance(recorded_at, str)
        and all(isinstance(n, int) for n in tokens)
    ):
        raise CacheEntryError(f"{path}: unreadable cache entry (a field has the wrong type)")
    return ModelResult(output, stop_reason, Usage(*tokens), recorded_at, replayed=True)
