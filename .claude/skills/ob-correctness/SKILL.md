---
name: ob-correctness
description: >
  What makes a change correct in objection-brief: the invariants (the guardrail is the
  only path to a brief, nothing dropped silently, human in the loop, follow-ups
  grounded only in the theme file, synthetic content only, honest evaluation, every
  model call replayable from the committed cache), how to validate model output, and
  a review checklist. Read this BEFORE changing llm, detect, guardrail, brief, notify,
  log, eval, a prompt or the data files, and when reviewing code.
---

# ob-correctness

The product promise is a brief an account executive can trust: the quote is really
what the prospect said, the answer is the approved one, and nothing reaches a
prospect without a human. A change that produces a plausible brief but breaks one of
the invariants below is a **defect**, whatever the output looks like.

## The seven invariants

### 1. The guardrail is the only path from model output to a brief

Every detection passes `guardrail.check` before `brief.py` sees it, and is kept only if
every condition in brief §7.2 holds, with the threshold read from
`config.min_confidence`.

Normalization is one function used by both sides of the comparison. It never fuzzes
the match (no edit distance, no stemming): a paraphrase fails by design.

### 2. Nothing disappears silently

Every detection the model returns is logged, kept or not, with `kept` and a
`reject_reason` from the `RejectReason` enum. Rejections are counted by reason in the
`run` output and the eval report. A model answer that fails to parse is a recorded
failure for that transcript, not a crash of `run-all` and not a retry loop.

### 3. Human in the loop

- Nothing is ever sent to a prospect: the only outbound channels are the two in brief
  §7.4.
- Every brief carries the footer brief §7.3 specifies, and the non-goals in brief §3
  stay unbuilt.

### 4. The follow-up is grounded

The follow-up call receives only the inputs brief §7.3 allows, and its prompt forbids
anything not in them, promises included. `proof` values marked `[PLACEHOLDER]` stay
visibly placeholders in the draft.

### 5. Synthetic and neutral content

- The hard rules in brief §1 apply to commits and PRs too (CLAUDE.md, principle 1). The
  content guard blocks the known vendor names by digest; the review agents check the
  rest.
- Every theme stays a draft owned "to validate" (brief §6.1); the loader enforces it.

### 6. Honest evaluation

- Every number comes from the generated report brief §9 specifies, scored against
  `labels.yaml` (§6.3), never typed by hand; the README quotes it the way §9 says,
  caveat included.
- Price constants in `config.py` also carry the date they were checked.

### 7. Every model call is replayable

Brief §7.6 is the spec: the key, the entry, `--offline`, what may be recorded, and the
unread-entry check. A call that bypasses `llm.call_model()`, or a prompt edited without
its version bump, is a defect even when the output looks right.

## Validate at trust boundaries

- **`weaknesses.yaml`:** the validation rules in brief §6.1; a bad file fails loading with
  the theme and field named.
- **Model output:** parsed into `Detection`s; a missing field or a value outside the
  enum is a `malformed_detection` rejection, and a confidence outside [0, 1] an
  `invalid_confidence` one, never an exception.
- **CLI input:** the transcript path exists, is a `.txt` file and is under a size cap.

Fail loud on programmer error. Fail *recorded* on data error.

## Review checklist

Report every finding with a confidence and a severity, then rank. Skip what ruff and
pyright already enforce. In order:

1. **Invariants** 1–7.
2. **Boundaries:** empty transcript, a transcript with no `PROSPECT:` lines, two
   objections with the same theme, a quote with curly quotes or line breaks, a quote
   that spans speakers, an empty model answer, a theme removed from the YAML.
3. **Failure behavior:** API timeout, 429, a refusal, malformed model output, Slack 4xx/5xx,
   DuckDB file locked. Does `run-all` continue past one bad transcript and report it?
4. **The test:** is there one that fails if this logic breaks, with no network?
5. **Over-engineering:** anything to delete, inline, or replace with the stdlib?
