---
name: ob-craft
description: >
  How code is written in objection-brief: the module map and which module owns each
  external dependency, the single model-call function, typed dataclasses at
  boundaries, the thin CLI, the reuse-first ladder, and tooling (uv, ruff, pyright,
  pytest, pre-commit). Read this BEFORE writing or refactoring any Python, SQL or YAML
  in the repo, or choosing a dependency. Pairs with ob-correctness (what keeps a change
  right), ob-security (what keeps it safe) and ob-voice (comments, commits).
---

# ob-craft

objection-brief is one pipeline, `transcript → detect → guardrail → log → brief → notify`,
small on purpose: a few typed functions, each in the module that owns its job, with
the model, Slack and DuckDB at the edges.

## Module map (each external dependency has exactly one owner)

| Module | Job | Owns |
|---|---|---|
| `config.py` | Model IDs, thresholds, price constants; reads the environment once into a frozen settings object | `os.environ` |
| `weaknesses.py` | Load and validate `data/weaknesses.yaml` into typed themes | — |
| `llm.py` | `call_model(...)`: the one function that calls a model provider, through the replay cache in `data/llm_cache/` | `anthropic` |
| `detect.py` | Build the detection prompt and output schema, parse the answer into `Detection`s | — |
| `guardrail.py` | Pure checks: verbatim quote, speaker, confidence, known theme; reasons for rejection | — |
| `brief.py` | Block Kit message and the grounded follow-up draft | — |
| `notify.py` | POST to the Slack webhook, or write `out/briefs/<id>.json` | `requests` |
| `log.py` | Append detections to DuckDB | `duckdb` |
| `evaluate.py` | Load `labels.yaml`, score kept detections, render and write the eval report | — |
| `cli.py` | Parse arguments, wire the pipeline, print | `print` |

The `boundary-guard` hook enforces the "Owns" column on every edit and in pre-commit.
Switching model provider changes `llm.py` only (PROJECT-BRIEF.md §4).

## Reuse before writing

Stop at the first rung that holds:

1. **Does it need to exist?** Out of the brief's scope → skip it, note it in the README.
2. **Already in the repo?** Reuse the dataclass, loader or helper that's here.
3. **Standard library?** `dataclasses`, `enum`, `pathlib`, `json`, `argparse`, `re`, `unicodedata`.
4. **A dependency brief §4 lists.** Anything else: ask first (brief §14).
5. **Only then** write the minimum that works.

No abstraction with one implementation, no class where a function does, no config for
a value that never changes, no `utils.py`.

## Shape of a function

- **Pure core, I/O at the edges.** `guardrail`, `brief` (the Block Kit part) and the
  eval metrics are pure functions over typed values. Files, network and the clock are
  touched in `notify.py`, `log.py`, `llm.py`, `evaluate.py` (the report) and `cli.py`.
- **Inputs are explicit.** Paths, thresholds, model IDs, `run_id` and `created_at` are
  parameters. Only `config.py` reads the environment; only `cli.py` reads the clock.
- **The CLI is thin.** argparse commands parse, build config, call functions, print.

## Typing and records

- Each module opens with a one-line docstring and `from __future__ import annotations`;
  public names in `__all__`.
- Modern typing: `X | None`, `list[str]`. Every public function typed and documented
  with a one-line docstring. pyright runs strict on `src/`.
- Records crossing a module boundary are `@dataclass(frozen=True, slots=True)`:
  `Theme`, `Detection`, `Verdict`, `Usage`. Fixed vocabularies are `StrEnum`
  (`Speaker`, `RejectReason`).
- Validate at the boundary (YAML load, model output, CLI arguments), then trust
  the type inside.

## Model calls

- One function, `llm.call_model(*, model, prompt_version, system, messages,
  output_config, max_tokens, cache: Cache, api_key) -> ModelResult`, returns the
  answer's text, `stop_reason`, token usage, `recorded_at` and whether it was replayed.
- **Replay first** (brief §7.6 lists the key's inputs): canonical JSON is
  `json.dumps(..., sort_keys=True, separators=(",", ":"))`, and neither `cache` nor
  `api_key` is part of the key. `Cache` is a small frozen dataclass: directory,
  `offline`, `record`, and `now`, the clock `cli.py` passes so `llm.py` reads none.
  `cli.py` builds it from `--offline` and `llm.may_record` (the rule in §7.6).
- **How it reaches callers:** `detect` and `brief` take a `call: ModelCall` parameter,
  a `Protocol` in `llm.py` with `call_model`'s keywords minus `cache` and `api_key`.
  `cli.py` passes `functools.partial(llm.call_model, cache=cache, api_key=...)`; tests
  pass a fake with the same keywords.
- Entries are one JSON file per key, written to a temp file and `os.replace`d; a
  corrupt entry is an error naming the file, never a silent re-call.
- Detection uses **structured outputs** (`output_config.format`, a JSON schema);
  `theme_id` and `speaker` are `enum`s in the schema, so the model can't invent a value.
  Not forced tool use: Claude Sonnet 5.5 rejects it (brief §15).
- Pinned model IDs, prompt versions and effort from `config.py`; no `temperature` or
  other sampling parameter, which API version `2026-10-21` rejects.
- A per-call timeout and bounded retries on the client; `max_tokens` set on every call,
  with room for thinking.

## SQL and data files

- DuckDB: parameterized inserts (`?`), never f-strings (ruff `S608`). The table DDL
  lives in one constant in `log.py`; `scripts/report.sql` is static.
- YAML: `yaml.safe_load` only (ruff `S506`).
- Paths built with `pathlib`; output names derived from validated IDs (ob-security).

## Tooling

- **uv** for env and lockfile: `uv sync --locked` in CI, `uv run --frozen` after it.
  Python 3.12 (`.python-version`).
- **ruff** format + lint (with bandit `S` rules), fixed on every edit by the hook.
- **pyright** strict on `src/`.
- **pre-commit**: the commit and push checks in `.pre-commit-config.yaml`; ob-security
  lists what each layer catches.

Let hooks do the mechanical enforcement. Spend attention on the logic and the test
that proves it.
