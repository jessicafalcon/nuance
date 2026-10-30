---
name: ob-tests
description: >
  The testing standard for objection-brief: offline by construction, the lightest test
  form that proves the behaviour, what each module must prove (guardrail, weaknesses,
  brief, detect parsing, notify, log, eval metrics, the llm replay cache, the offline
  pipeline, hooks), replay and fakes instead of the API, and fake secrets built by
  concatenation. Read this BEFORE writing or changing a test,
  or when deciding what a change must prove.
---

# ob-tests

Tests are offline and deterministic: `uv run pytest` needs no network, no API key,
no Slack and no clock. `pytest-socket` blocks every non-Unix socket, so an accidental
live call fails the test instead of spending money.

## The bar

- One runnable check per non-trivial unit: the smallest test that fails when the
  logic breaks.
- Every invariant in ob-correctness and every rule in a hook has a test that fails
  when it's violated.
- The whole suite runs in seconds.

## Pick the lightest form

1. **Doctest** for pure functions; it documents usage too.
   ```python
   >>> normalize("  “Setting up\npermissions”  ")
   '"setting up permissions"'
   ```
2. **Unit test** for error paths and parametrized edge cases, named by behaviour:
   `test_paraphrased_quote_is_rejected`, `test_rep_speaker_is_rejected`.
3. **Replay** for the pipeline: end-to-end tests run offline over the committed
   `data/llm_cache/`, the same entries a reader replays.
4. **Fake client** for anything that calls out: `detect` and `brief` take a
   `call: ModelCall` (ob-craft), so tests pass a function with `call_model`'s keywords
   (minus `cache`) that returns a recorded tool input. `notify` tests use a fake `post` callable, not the network.

## What each module must prove (brief §10 is the floor)

| Module | Must prove |
|---|---|
| `guardrail` | Whitespace/case/curly-quote differences pass; paraphrase fails; rep and unknown speaker fail; confidence below threshold fails; unknown theme fails; each rejection carries its reason |
| `weaknesses` | YAML loads; IDs unique; every field present; `status: draft`; a broken file fails with the theme and field named |
| `brief` | Block Kit has the five elements and the footer; `<`, `>`, `&` and `<!channel>` in a quote are escaped; long text truncates but the footer survives |
| `detect` | A recorded tool input parses into `Detection`s; a missing field or an out-of-enum value becomes a recorded rejection, not an exception |
| `notify` | No webhook → file under `out/briefs/` with a validated name; webhook error → `NotifyError` whose message has no URL |
| `log` | Kept and rejected detections both land with the right columns and types |
| `eval` | Precision and recall on a hand-computed example, including a theme with no positives |
| `llm` | Key stable across runs and changed by each input; a hit makes no call; an offline miss raises; inputs outside `data/transcripts/` never recorded; no entry contains the API key |
| pipeline (`test_pipeline.py`) | `run-all --offline` and both `eval --offline` runs over the committed cache, twice: identical briefs and reports, reports equal the committed `reports/` files, no cache entry left unread |
| hooks | Every block and allow rule (tests/hooks/) |

## Fakes and fixtures

- Fakes are local functions or recorded JSON in `tests/fixtures/`. Never the real API.
- A fake secret is built by concatenation (`"sk-" + "ant-" + "x" * 40`) so no key shape
  appears in the repo; the content guard and gitleaks would block it anyway.
- Fixed `run_id` and `created_at` values are passed in; nothing reads the clock.

## Where tests live

`tests/` mirrors `src/objection_brief/` (`test_guardrail.py`, ...), `tests/hooks/` holds
the hook tests, doctests run from `src/` and `.claude/hooks/` (`--doctest-modules`).
CI runs `uv sync --locked && uv run --frozen pytest` with no credentials.
