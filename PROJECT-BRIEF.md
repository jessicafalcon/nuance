# Project Brief — Objection Brief

> One-day build. Read this whole file before writing code. Follow the build order, run the tests at each checkpoint, and keep the scope minimal.

## 1. Context

**Northwind Planning** is a *fictional* B2B planning/EPM SaaS company used as the demo setting. The weakness themes are based on recurring complaints found in public reviews of planning software (G2, Gartner Peer Insights, analyst comparison pages, 2026). They are category-level, not specific to any real vendor.

**Hard rules**
- Never name a real planning vendor anywhere in code, data, comments or docs.
- No real customer names, people or review text. Transcripts and examples are synthetic; any review language is paraphrased.
- No secrets in the repo. API keys come from `.env` (gitignored); ship `.env.example`.
- README tone: measured and factual. No slogans, no overclaiming results we don't have.

## 2. Problem and hypothesis

Known product weaknesses (e.g. complex access permissions, Excel round-trips, price escalation) come up in sales calls. In a fast-growing sales team with many ramping reps, these objections are likely handled inconsistently, and the best answers live in a few top performers' heads.

**Hypothesis (unvalidated):** surfacing the objection right after the call, with an approved honest answer, one piece of proof and a draft follow-up, makes handling consistent and speeds up follow-up. This has not been validated with a sales team; the README includes a validation plan.

## 3. What we build (one problem, one trigger, one output)

**Input:** a sales-call transcript (text).
**Output:** a short brief for the account executive, formatted as a Slack message:
1. Objection theme detected
2. The prospect's exact words (verbatim quote)
3. The approved answer (from the weakness file)
4. One proof point (from the weakness file)
5. A drafted follow-up paragraph the AE edits before sending (human-in-the-loop; nothing is sent automatically to prospects)

Every detection is logged so objection frequency by theme can be analysed later.

**Non-goals (do not build):** web app, FastAPI, React, dbt, Terraform, cloud deployment, multi-agent setups, auto-sending emails, a stuck-trial signal. These go in the README "Production path" section only.

## 4. Tech stack

- Python 3.11+, managed with `uv` (or `pip` + `requirements.txt` if uv unavailable)
- `anthropic` (Anthropic Python SDK) — detection via tool use with a JSON schema for structured output
- Models: `claude-haiku-5-5` for detection (default); `claude-sonnet-5-5` optional for the follow-up draft and for the model comparison in the eval
- `pyyaml`, `duckdb`, `requests`
- Secrets load with `uv run --env-file .env ...`; no code reads `.env` (§15)
- Dev only: `pytest`, `pytest-socket` (no network in tests), `ruff`, `pyright`, `pre-commit`; pre-commit also runs gitleaks and zizmor, and `uv audit` checks the lockfile (§15)
- Slack: incoming webhook if `SLACK_WEBHOOK_URL` is set; otherwise a mock that writes Block Kit JSON to `out/briefs/` and prints a readable preview

Keep the model call behind one function so switching providers means changing only that function.
That function replays answers from a committed response cache (§7.6), so the whole pipeline and the
eval run offline, in CI and for any reader, with no API key.

## 5. Repo structure

```
nuance/                        # GitHub repo; package objection_brief, CLI objection-brief
├── README.md
├── CLAUDE.md                  # how we work: tools, pre-PR gate, records, current status
├── PROJECT-BRIEF.md           # this file
├── SECURITY.md                # private vulnerability reporting
├── LICENSE                    # MIT
├── .env.example               # ANTHROPIC_API_KEY=, SLACK_WEBHOOK_URL=
├── .pre-commit-config.yaml    # commit and push checks (secrets, lint, types, tests, audit)
├── pyproject.toml
├── uv.lock
├── .claude/                   # settings, hooks/ (guards), skills/ (ob-*), agents/ (reviewers)
├── .github/                   # CI workflow, Dependabot, PR template
├── data/
│   ├── weaknesses.yaml        # theme definitions + draft answers
│   ├── transcripts/           # ~12 synthetic snippets (.txt)
│   ├── labels.yaml            # expected themes per transcript (for eval)
│   └── llm_cache/             # recorded model responses, one JSON file per call (§7.6)
├── src/objection_brief/
│   ├── config.py              # model ids, thresholds, price constants
│   ├── weaknesses.py          # load + validate weaknesses.yaml
│   ├── llm.py                 # call_model(): the one function that calls a model provider
│   ├── detect.py              # detect(transcript) -> list[Detection]
│   ├── guardrail.py           # verbatim-quote check + confidence filter
│   ├── brief.py               # build Block Kit message + follow-up draft
│   ├── notify.py              # Slack webhook or mock
│   ├── log.py                 # append detections to DuckDB
│   └── cli.py                 # run / eval commands
├── scripts/
│   └── report.sql             # objections by theme
├── reports/                   # eval_<model>.md, generated offline from llm_cache/ (§9)
├── tests/
│   ├── hooks/                 # tests for the guard hooks
│   ├── test_llm.py
│   ├── test_pipeline.py
│   ├── test_guardrail.py
│   ├── test_weaknesses.py
│   └── test_brief.py
└── out/                       # gitignored: briefs, duckdb file
```

## 6. Data specs

### 6.1 `data/weaknesses.yaml`

Six themes. Every answer is marked as a draft to be validated by Product Marketing / Sales Enablement.

```yaml
- id: access_permissions
  label: Complex access permissions
  detect_when: Prospect worries that setting up who-sees-what (e.g. budget owners seeing only their cost center) is complicated or needs vendor help.
  example_signals:            # paraphrased, synthetic
    - "setting up permissions for every budget owner sounds painful"
    - "will we need your team every time we change who sees what"
  draft_answer: >
    Acknowledge that granular permissions take deliberate setup. Explain the role-based
    templates and the guided permissions session included in onboarding.
  proof: "[PLACEHOLDER] customer story: mid-market company scoped cost-center access in onboarding"
  follow_up_angle: Offer a 30-minute permissions design session with a solutions consultant.
  status: draft
  owner: Product Marketing / Sales Enablement (to validate)
```

Other themes, same fields:
- `learning_curve_non_finance` — non-finance teams struggling to build or change models without consultants
- `excel_interop` — moving data to/from Excel or Google Sheets, reporting layout falling back to spreadsheets
- `performance_at_scale` — slow interface or timeouts on large models
- `consolidation_depth` — complex multi-entity consolidation (answer must be honest about limits and suggest a realistic path)
- `pricing_escalation` — seat-based cost growth and annual price increases

Validation: unique `id`s in lowercase snake_case (they become schema enum values and log columns), every field present and non-empty, no unknown or repeated fields, `status: draft`, `owner` containing "to validate". A failure names the theme and the field.

### 6.2 `data/transcripts/`

~12 short synthetic snippets (300–1,200 words each), fictional companies and people, speakers labelled `REP:` and `PROSPECT:`.
- 8 positives covering all six themes (some transcripts contain two objections)
- 4 negatives: price mentioned positively, a competitor mentioned without an objection, an objection raised by the rep (not the prospect), and a call with no objection at all

### 6.3 `data/labels.yaml`

```yaml
t01_permissions: [access_permissions]
t09_no_objection: []
```

## 7. Component specs

### 7.1 `detect(transcript) -> list[Detection]`
- One API call. System prompt includes the theme list (`id`, `label`, `detect_when`) and strict rules: only prospect objections count, quote must be copied verbatim, return nothing when unsure.
- Force structured output with a tool whose input schema is:

```json
{
  "objections": [
    {
      "theme_id": "access_permissions | learning_curve_non_finance | excel_interop | performance_at_scale | consolidation_depth | pricing_escalation",
      "quote": "verbatim text from the transcript",
      "speaker": "prospect | rep | unknown",
      "confidence": 0.0
    }
  ]
}
```
- Return token usage alongside results (for cost reporting).

### 7.2 Guardrail
Keep a detection only if all are true:
- `quote` appears in the transcript after normalising whitespace, quotes and case
- `speaker == "prospect"`
- `confidence >= 0.6` (configurable)
- `theme_id` exists in `weaknesses.yaml`

Count and report rejections by reason.

### 7.3 Brief
- Build a Slack Block Kit message with the five elements from section 3, plus a footer: "Draft answer — review before using."
- Follow-up paragraph: one LLM call that may use **only** the matching theme's `draft_answer`, `proof` and `follow_up_angle`, plus the quote. No invented facts, figures or customer names.

### 7.4 Notify
`SLACK_WEBHOOK_URL` set → POST. Otherwise write `out/briefs/<transcript_id>.json` and print a readable preview.

### 7.5 Log
DuckDB table `detections` (BigQuery-compatible types):
`run_id STRING, transcript_id STRING, theme_id STRING, quote STRING, confidence FLOAT64, model STRING, input_tokens INT64, output_tokens INT64, kept BOOL, reject_reason STRING, created_at TIMESTAMP`

`scripts/report.sql`: objection count by theme, kept vs rejected.

### 7.6 Model-call replay

Every model call goes through `llm.call_model()`, which reads and writes a response cache in `data/llm_cache/`.

- **Key:** `sha256` of the canonical JSON (sorted keys, no whitespace) of model ID, prompt version, system prompt,
  messages, tool schema, `tool_choice`, `max_tokens` and `temperature`. Prompt versions are constants in `config.py`;
  any change to a prompt's text bumps its version.
- **Entry:** `data/llm_cache/<key>.json` holds the key's inputs, the parsed tool input, `stop_reason`, token usage
  and `recorded_at` (UTC, set when the live call returns), written atomically. Never the API key or any header.
- **Modes:** default: a hit replays, a miss calls the API and records the entry. `--offline`: a miss is an error
  naming the key and prompt version, which `cli.py` reports with the transcript ID; no call is made. CI and tests
  are always offline.
- **Privacy:** entries are recorded only for transcripts whose resolved path (symlinks followed) is under
  `data/transcripts/`, which holds only synthetic transcripts; any other input is called live and never written.
- **Hygiene:** `test_pipeline.py` fails on a cache entry that no `run-all` or `eval` reads, so a PR that changes a
  prompt deletes the stale entries.
- **Cost:** replayed calls report the recorded token usage, so offline cost figures equal the live ones.

## 8. CLI

```
uv run objection-brief run data/transcripts/t01_permissions.txt   # detect → guardrail → brief → notify → log
uv run objection-brief run-all                                     # every transcript
uv run objection-brief eval --model claude-haiku-5-5               # metrics vs labels.yaml
uv run objection-brief eval --model claude-sonnet-5-5
uv run objection-brief run-all --offline                           # replay only; a cache miss is an error
uv run objection-brief eval --model claude-haiku-5-5 --offline
uv run objection-brief eval --model claude-sonnet-5-5 --offline
```

## 9. Evaluation

`eval` prints and saves `reports/eval_<model>.md` (committed; `--model` accepts only the IDs pinned in `config.py`) with:
- the model, and the date its responses were recorded (the latest `recorded_at` among the entries read)
- precision and recall per theme and overall (after guardrail)
- guardrail rejections by reason
- total tokens and estimated cost (price constants in `config.py`, marked "verify against Anthropic's pricing page")

The report is generated from the cache and reads no clock, so `eval --offline` reproduces it byte for byte;
`test_pipeline.py` regenerates it in CI and fails if it differs from the committed file. The README quotes only
these reports, with the model and the recorded date they state.

Note in the README that the test set is small and synthetic, so the numbers show the method works, not real-world accuracy.

## 10. Tests (pytest)

- `test_guardrail.py`: verbatim match with whitespace/case differences passes; paraphrased quote fails; rep speaker fails; low confidence fails; unknown theme fails
- `test_weaknesses.py`: YAML loads, ids unique, required fields present; each §6.1 validation rule fails with the theme and field named
- `test_brief.py`: Block Kit JSON has the five elements and the draft footer

- `test_llm.py`: the cache key is stable and changes with each input; a hit replays without a call; an offline miss
  raises; an input outside `data/transcripts/` is never recorded; an entry never contains the API key
- `test_pipeline.py` (in pytest, which CI runs) replays `run-all --offline` and both `eval --offline` runs over
  the committed cache, twice: identical briefs and reports, reports equal to the committed `reports/` files, and
  no cache entry left unread

Tests must not call the API.

## 11. README outline

1. **Why** — the problem and the hypothesis (state clearly it is unvalidated)
2. **Evidence** — recurring complaint themes in public planning-software reviews (sources and dates, category-level, paraphrased)
3. **What** — how it works, with one example brief (screenshot or JSON preview)
4. **Outcome** — eval results (precision/recall, cost per call for both models), and the metrics to measure in production: win rate on opportunities where an objection was detected, time from call to follow-up, share of drafts accepted or edited by reps, objection frequency by theme
5. **Validation plan** — who to talk to (2 AEs, 1 solutions consultant, Product Marketing, Sales Enablement), three questions (which objections are most frequent? is handling consistent? what exists today?), and the stop condition (if objections are rare or already well handled, pivot the logging into a voice-of-customer feed for Product)
6. **Production path** — call-recorder webhook → task queue → container service → warehouse table; detector exposed as a tool for an internal research agent; infrastructure as code. Described, not built.
7. **Cost** — per-call and weekly estimates for both models
8. **Limitations** — synthetic data, draft answers, single-language

## 12. Build order (checkpoints)

One PR per step, on its own branch (CLAUDE.md, pre-PR gate). A step is ticked when its PR merges.

- [x] 1. Scaffold repo, `pyproject.toml`, `.env.example`, `.gitignore`, `CLAUDE.md`, security hooks, CI → **checkpoint:** `uv run pytest` runs (only the hook tests so far)
- [x] 2. `weaknesses.yaml` + loader + `test_weaknesses.py` → **checkpoint:** tests pass
- [ ] 3. Synthetic transcripts + `labels.yaml` → **checkpoint:** I review them before continuing
- [ ] 4. `guardrail.py` + tests → **checkpoint:** tests pass
- [ ] 5. `llm.py` (with the replay cache, §7.6) + `detect.py` → **checkpoint:** run live on 2 transcripts, inspect raw JSON, commit their cache entries; the same run with `--offline` makes no call
- [ ] 6. `brief.py`, `notify.py`, `log.py` + tests → **checkpoint:** `run-all` produces briefs in `out/briefs/`; the cache covers every transcript and `test_pipeline.py` replays `run-all --offline`
- [ ] 7. `eval` for both models → **checkpoint:** `reports/eval_<model>.md` committed for both; `test_pipeline.py` regenerates them offline and they match
- [ ] 8. README → **checkpoint:** final review

## 13. Acceptance criteria

- `run-all` produces a brief for every positive transcript and none for the four negatives (or explains any miss in the eval report)
- All tests pass without network access
- Eval report exists for both models, with cost, in `reports/`, and `test_pipeline.py` reproduces it offline from `data/llm_cache/` in CI
- No real vendor, customer or person named anywhere (`grep` check before finishing)
- README follows section 11 and states the limitations plainly

## 14. Conventions for Claude Code

- How we work (skills, hooks, review agents, the pre-PR gate, records) is in `CLAUDE.md`.
- Ask before adding any dependency not listed in section 4.
- Small, typed functions; docstrings on public functions.
- Run tests after each step; don't move to the next checkpoint with failing tests.
- When unsure about scope, choose the smaller option and note it in the README.

## 15. Decisions

| Decision | Reason |
|---|---|
| GitHub repo `nuance`, public from the first push; package `objection_brief`, CLI `objection-brief` | The repo name follows the working directory; the code keeps the product name |
| Secrets load with `uv run --env-file .env`; `python-dotenv` dropped | No code ever reads `.env`, one dependency fewer |
| Model calls live in `llm.py` behind `call_model()` | §4's "one function" needs one home shared by `detect.py` and `brief.py`; a hook enforces it |
| Model responses are recorded in a committed cache and replayed; `--offline` makes a miss an error; eval reports are committed in `reports/` | CI has no API key, so without replay the pipeline is never tested end to end and the README's numbers can't be reproduced or checked |
| A report's date is the latest `recorded_at` among the cache entries it read | The report reads no clock, so it reproduces byte for byte and still states when its answers were produced |
| The offline pipeline check runs inside pytest (`test_pipeline.py`), not as separate CI steps | One mechanism that runs the same locally, at pre-push and in CI |
| Dev tooling: ruff, pyright, pre-commit, pytest-socket; gitleaks, zizmor and `uv audit` (native to uv, used instead of pip-audit) | The repo is public: secrets, workflow and dependency checks run at commit, push and in CI |
| Real vendor names are blocked by SHA-256 digest (`.claude/hooks/banned-terms.sha256`) | The hard rule is checked in hooks and CI without the repo naming any vendor |
| Python 3.12 | Within §4's 3.11+; the version CI tests |
| Theme validation is stricter than unique ids, all fields and `status: draft`: ids are lowercase snake_case, no field is empty, unknown or repeated, and `owner` contains "to validate" (§6.1) | Ids become schema enum values and log columns; a misspelled or pasted field would otherwise be ignored or silently replace the approved answer; the owner marker keeps every answer visibly unapproved |
