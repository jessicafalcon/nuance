# Objection Brief

A command-line prototype that reads a sales-call transcript, detects known product
objections raised by the prospect, and drafts a short brief for the account executive:
the objection theme, the prospect's exact words, the approved answer, one proof point,
and a follow-up paragraph the account executive edits before sending.

The setting is **Northwind Planning**, a fictional B2B planning software company. All
transcripts are synthetic. No real vendor, customer or person appears in this repository.

## Status

**Prototype, build complete: `run` and `run-all` detect, check and brief the objections in
a transcript; `eval` scores detection against hand-written labels for two models. The
hypothesis behind it has not been tested with a sales team (see [Validation plan](#validation-plan)).**

| Step | Scope | State |
|---|---|---|
| 1 | Scaffold, security hooks, CI | Done |
| 2 | Objection themes (`weaknesses.yaml`) and loader | Done |
| 3 | Synthetic transcripts and labels | Done |
| 4 | Guardrail (verbatim quote, speaker, confidence) | Done |
| 5 | Detection through the Anthropic API | Done |
| 6 | Brief, Slack or file output, DuckDB log | Done |
| 7 | Evaluation against labels, for two models | Done |
| 8 | Full README: evidence, results, validation plan, cost, limitations | Done |

The plan, scope and component specs are in [PROJECT-BRIEF.md](PROJECT-BRIEF.md).

What can be built on top of it, without changing the pipeline's shape:

- **Objection frequency by theme.** Every detection, kept or rejected, is already logged
  to DuckDB; [`scripts/report.sql`](scripts/report.sql) counts them by theme.
- **A voice-of-customer feed for Product.** The same log, read by theme and over time,
  shows which weaknesses prospects raise most.
- **More themes.** A theme is an entry in [`data/weaknesses.yaml`](data/weaknesses.yaml);
  the detection schema takes its theme ids from that file, so no code changes. The prompt
  changes with it, so every recorded answer is re-recorded and the labels gain the theme.
- **Approved content.** Product Marketing and Sales Enablement replace the draft answers
  and placeholder proof points once they validate them; the briefs pick them up as they are.

Running it on real calls (a call-recorder trigger, a queue, a warehouse table) is
described under [Production path](#production-path).

## Why

Known product weaknesses, such as complex access permissions, spreadsheet round-trips
or price increases at renewal, come up in sales calls. In a fast-growing sales team with
many ramping reps, these objections are likely handled inconsistently, and the best
answers stay with a few experienced people.

**Hypothesis (unvalidated):** surfacing the objection right after the call, with an
approved honest answer, one proof point and a draft follow-up, makes handling more
consistent and follow-up faster. This has not been tested with a sales team; the
[validation plan](#validation-plan) says how it would be.

## Evidence

The six themes are recurring complaints in public reviews of planning software, read in
2026 on review platforms (G2, Gartner Peer Insights) and analyst comparison pages. They
are described at category level, in our own words; none is attributed to a specific
vendor or reviewer, and no review text is quoted.

| Theme | Recurring complaint, paraphrased |
|---|---|
| Complex access permissions | Setting up who sees what, such as budget owners seeing only their own cost center, takes significant effort or vendor help |
| Steep learning curve outside finance | People outside finance can't build or change models without consultants or a central admin |
| Excel and spreadsheet round-trips | Moving data to and from spreadsheets is painful, and reporting layouts end up rebuilt in Excel |
| Performance at scale | Large models are slow to recalculate or time out |
| Consolidation depth | Complex multi-entity consolidation (partial ownership, several accounting standards) is beyond the planning tool |
| Pricing escalation | Per-seat costs grow with adoption, and annual or renewal increases are hard to forecast |

The themes come from reading reviews, not from counting them: they say which complaints
recur, not how often.

## What it does

```text
transcript.txt → detect    one model call, structured output: theme, quote, speaker, confidence
               → guardrail quote verbatim in one PROSPECT: turn; confidence ≥ 0.6; known theme
               → log       every detection, kept or rejected with its reason, to DuckDB
               → brief     one item per theme; a follow-up drafted only from the theme's
                           approved points, its proof inserted by the code; a draft that
                           fails a check is withheld with its reason
               → notify    Slack webhook if configured, otherwise out/briefs/<id>.json
```

The guardrail is the only path to a brief, so a quote the prospect didn't say word for
word never reaches one. Nothing is sent to prospects: every brief is a draft for the
account executive to review.

Each theme in [`data/weaknesses.yaml`](data/weaknesses.yaml) holds the approved answer,
the proof point and the follow-up angle. The model picks the theme and the quote and
drafts the paragraph; the answer and the proof come from the file.

### Example brief

The brief for `t01_permissions`, as `run-all --offline` writes it to
`out/briefs/t01_permissions.json` (Slack Block Kit; the header, divider and some keys are
left out here):

```json
[
  {"type": "section", "text": {"type": "mrkdwn", "text": "*Objection:* Complex access permissions"}},
  {"type": "section", "text": {"type": "mrkdwn", "text": "*Prospect said:*\n>Honestly, setting up permissions so each of our fifty budget owners only sees their own cost center sounds like a project in itself."}},
  {"type": "section", "text": {"type": "mrkdwn", "text": "*Approved answer (draft):*\nAcknowledge that granular permissions take deliberate setup. Explain the role-based templates and the guided permissions session included in onboarding."}},
  {"type": "section", "text": {"type": "mrkdwn", "text": "*Proof:*\n[PLACEHOLDER] customer story: mid-market company scoped cost-center access in onboarding"}},
  {"type": "section", "text": {"type": "mrkdwn", "text": "*Follow-up draft, edit before sending:*\nYou're right that scoping access so each of your fifty budget owners sees only their own cost center takes deliberate setup, and we don't pretend otherwise. To make that manageable, onboarding includes role-based permission templates and a guided permissions session, so your team isn't starting from a blank configuration. [PLACEHOLDER] customer story: mid-market company scoped cost-center access in onboarding. If it would help, I'd suggest a 30-minute permissions design session with one of our solutions consultants, where we can map your budget owners to the right access levels and see how much of the work the templates cover for your structure."}},
  {"type": "context", "elements": [{"type": "mrkdwn", "text": "Draft answer — review before using."}]}
]
```

### Commands

```bash
uv run objection-brief run data/transcripts/t01_permissions.txt --offline
uv run objection-brief run-all --offline       # every transcript in data/transcripts/
uv run objection-brief eval --model claude-haiku-5-5 --offline
```

`--offline` replays the model answers committed in `data/llm_cache/` and makes a missing
recording an error, so these commands need no API key. Without it, a missing answer is
requested live and recorded (see [Development setup](#development-setup)).

## Outcome

Detection results from the committed eval reports, after the guardrail, over
(transcript, theme) pairs:

| Model | Answers recorded | Pairs found | Precision | Recall | False positives | Detection cost per 1,000 transcripts |
|---|---|---|---|---|---|---|
| Claude Haiku 5.5 | 2026-10-30 | 10 of 10 | 1.00 | 1.00 | 0 | $0.32 |
| Claude Sonnet 5.5 | 2026-10-30 | 10 of 10 | 1.00 | 1.00 | 0 | $5.46 |

Sources: [`reports/eval_claude-haiku-5-5.md`](reports/eval_claude-haiku-5-5.md) and
[`reports/eval_claude-sonnet-5-5.md`](reports/eval_claude-sonnet-5-5.md), prompt
`detect-v2`, with per-theme and per-transcript results. Neither model had a detection
rejected by the guardrail.

The test set is 12 short synthetic transcripts, 8 with objections and 4 without, written
and labelled for this project. The numbers show that the method works on clear cases,
not its accuracy on real calls. CI regenerates both reports from the cache and fails if
either differs from the committed file.

In production, these would show whether the hypothesis holds:

| Metric | Question it answers |
|---|---|
| Win rate on opportunities where an objection was detected | Do briefed objections close at a higher rate than before? |
| Time from call to follow-up | Does the draft make follow-up faster? |
| Share of drafts accepted, edited or discarded | Are the answers and drafts usable as they are? |
| Objection frequency by theme | Which weaknesses come up, and is that changing? |

## Validation plan

Before building further, five conversations: two account executives, one solutions
consultant, Product Marketing and Sales Enablement. Three questions for each:

1. Which objections come up most often in your calls?
2. Is the way they're handled consistent across the team?
3. What exists today for this (battle cards, call notes, enablement sessions)?

**Stop condition:** if these objections turn out to be rare, or already well handled, the
brief isn't needed; the detection log becomes a voice-of-customer feed for Product
instead.

## Production path

Described, not built:

1. The call recorder sends a webhook when a transcript is ready.
2. A task queue takes it, so a burst of calls never runs as a burst of model calls.
3. A container service runs the same pipeline, posting the brief to the account
   executive's Slack channel.
4. The detections table goes to the warehouse; its columns already use BigQuery types.
5. The detector is exposed as a tool for an internal research agent, so questions like
   "which deals raised consolidation this quarter" read the same detections.
6. The infrastructure is managed as code.

Sending anything to prospects stays out by design: the account executive sends the
follow-up. A web app, email integration and scheduling are not part of this prototype.

## Cost

Prices are Anthropic's list prices as of 2026-10-30, in `config.py`; verify against
Anthropic's pricing page before relying on them.

**Detection, one call per transcript.** From the eval reports: an average of 2,341 input
tokens per synthetic transcript.

| Model | Per call | Per week, 500 calls of synthetic length | Per week, 500 calls of 10,000 input tokens |
|---|---|---|---|
| Claude Haiku 5.5 | $0.00032 | $0.16 | $0.54 |
| Claude Sonnet 5.5 | $0.0055 | $2.73 | $10.39 |

The weekly figures are estimates, not measurements. 500 calls a week is an assumed team
volume. The synthetic transcripts are 300 to 1,200 words; a 45-minute call is several
times longer, so the last column assumes 10,000 input tokens with the measured output
per call (172 tokens on Claude Haiku 5.5, 77 on Claude Sonnet 5.5).

**Follow-up, one call per kept theme, on Claude Haiku 5.5.** The 10 committed
`follow-up-v4` answers used 5,599 input and 2,063 output tokens: $0.0016 in all, about
$0.00016 per kept theme or $0.16 per 1,000. Its input is the theme's approved points and
the quote, not the transcript, so a longer call doesn't change it. At one kept theme per
call, 500 calls a week add $0.08.

`run` and `run-all` use Claude Haiku 5.5 for both calls; Claude Sonnet 5.5 is evaluated
for detection only.

## Limitations

- **Synthetic, small test set.** 12 transcripts written and labelled together for this
  project; perfect scores on them say nothing about real calls. A quote must sit inside
  one prospect turn, so an objection spread across several turns is quoted in part or
  not at all.
- **Draft content.** Every answer is a draft marked for Product Marketing and Sales
  Enablement to validate, and every proof point is a placeholder.
- **English only.** The themes, prompts and transcripts are in English; other languages
  are untested.
- **Partial follow-up checks.** The code checks that a draft has one proof slot, doesn't
  restate the proof, and states no figure missing from the approved points or the quote.
  It doesn't check every claim; the account executive does.
- **Confidence is the model's own.** The 0.6 threshold filters on a number the model
  reports about itself, not a calibrated probability.
- **Slack delivery is untested live.** The webhook path is tested with fakes; without a
  webhook, briefs are written to `out/briefs/`.

Where the scope was unclear, the smaller option was taken: one brief item per theme (its
most confident detection), one detection call per transcript with no chunking, and the
smaller model for the follow-up.

## Development setup

Requires [uv](https://docs.astral.sh/uv/) 0.12.5 and [gitleaks](https://github.com/gitleaks/gitleaks) 8.30.1 on `PATH`.

```bash
uv sync
uv run pre-commit install   # commit, commit-msg and pre-push checks
uv run pytest               # offline; network access fails the test
```

Live runs need an Anthropic API key: copy `.env.example` to `.env`,
fill it in, and run commands with `uv run --env-file .env objection-brief ...`. Use a key
from a workspace with a monthly spend limit. New answers are recorded in
`data/llm_cache/` only for transcripts in `data/transcripts/`.

## Repository safeguards

The repository is public, so checks run before anything leaves a machine and again in CI:

- **Commit:** gitleaks and a content check for API keys, webhook URLs and real vendor
  names (matched by SHA-256 digest, so the list itself names no one); ruff with security
  rules; pyright; workflow lint (zizmor); and a records check that the build status,
  pinned tool versions and tool lists agree across the documents.
- **Push:** the test suite; gitleaks and the content check over the full history,
  commit messages included; and `uv audit` on the locked dependencies.
- **CI:** all of the above on every pull request, plus the PR title and body, with no
  secrets in the environment.
- **GitHub:** `main` accepts changes only through pull requests with passing checks;
  secret-scanning push protection and Dependabot security updates are on.

See [SECURITY.md](SECURITY.md) to report a vulnerability.

## License

[MIT](LICENSE)
