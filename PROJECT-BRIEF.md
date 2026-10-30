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
- `pyyaml`, `duckdb`, `python-dotenv`, `requests`, `pytest`
- Slack: incoming webhook if `SLACK_WEBHOOK_URL` is set; otherwise a mock that writes Block Kit JSON to `out/briefs/` and prints a readable preview

Keep the model call behind one function so switching providers means changing only that function.

## 5. Repo structure

```
objection-brief/
├── README.md
├── CLAUDE.md                  # short pointer to this brief + coding conventions
├── PROJECT_BRIEF.md           # this file
├── .env.example               # ANTHROPIC_API_KEY=, SLACK_WEBHOOK_URL=
├── pyproject.toml
├── data/
│   ├── weaknesses.yaml        # theme definitions + draft answers
│   ├── transcripts/           # ~12 synthetic snippets (.txt)
│   └── labels.yaml            # expected themes per transcript (for eval)
├── src/objection_brief/
│   ├── config.py              # model ids, thresholds, price constants
│   ├── weaknesses.py          # load + validate weaknesses.yaml
│   ├── detect.py              # detect(transcript) -> list[Detection]
│   ├── guardrail.py           # verbatim-quote check + confidence filter
│   ├── brief.py               # build Block Kit message + follow-up draft
│   ├── notify.py              # Slack webhook or mock
│   ├── log.py                 # append detections to DuckDB
│   └── cli.py                 # run / eval commands
├── scripts/
│   └── report.sql             # objections by theme
├── tests/
│   ├── test_guardrail.py
│   ├── test_weaknesses.py
│   └── test_brief.py
└── out/                       # gitignored: briefs, duckdb file, eval reports
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

Validation: unique `id`s, all fields present, `status: draft`.

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

## 8. CLI

```
uv run objection-brief run data/transcripts/t01_permissions.txt   # detect → guardrail → brief → notify → log
uv run objection-brief run-all                                     # every transcript
uv run objection-brief eval --model claude-haiku-5-5               # metrics vs labels.yaml
uv run objection-brief eval --model claude-sonnet-5-5
```

## 9. Evaluation

`eval` prints and saves `out/eval_<model>.md` with:
- precision and recall per theme and overall (after guardrail)
- guardrail rejections by reason
- total tokens and estimated cost (price constants in `config.py`, marked "verify against Anthropic's pricing page")

Note in the README that the test set is small and synthetic, so the numbers show the method works, not real-world accuracy.

## 10. Tests (pytest)

- `test_guardrail.py`: verbatim match with whitespace/case differences passes; paraphrased quote fails; rep speaker fails; low confidence fails; unknown theme fails
- `test_weaknesses.py`: YAML loads, ids unique, required fields present
- `test_brief.py`: Block Kit JSON has the five elements and the draft footer

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

1. Scaffold repo, `pyproject.toml`, `.env.example`, `.gitignore`, `CLAUDE.md` → **checkpoint:** `uv run pytest` runs (no tests yet is fine)
2. `weaknesses.yaml` + loader + `test_weaknesses.py` → **checkpoint:** tests pass
3. Synthetic transcripts + `labels.yaml` → **checkpoint:** I review them before continuing
4. `guardrail.py` + tests → **checkpoint:** tests pass
5. `detect.py` → **checkpoint:** run on 2 transcripts, inspect raw JSON
6. `brief.py`, `notify.py`, `log.py` + tests → **checkpoint:** `run-all` produces briefs in `out/briefs/`
7. `eval` for both models → **checkpoint:** reports in `out/`
8. README → **checkpoint:** final review

## 13. Acceptance criteria

- `run-all` produces a brief for every positive transcript and none for the four negatives (or explains any miss in the eval report)
- All tests pass without network access
- Eval report exists for both models, with cost
- No real vendor, customer or person named anywhere (`grep` check before finishing)
- README follows section 11 and states the limitations plainly

## 14. Conventions for Claude Code

- Ask before adding any dependency not listed in section 4.
- Small, typed functions; docstrings on public functions.
- Run tests after each step; don't move to the next checkpoint with failing tests.
- When unsure about scope, choose the smaller option and note it in the README.
