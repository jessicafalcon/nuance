---
name: ob-voice
description: >
  The written voice of objection-brief: commit messages, PR titles and bodies, code
  comments, docstrings, and every Markdown file (README, CLAUDE.md, the brief, skills).
  Measured and factual, result first, validation stated honestly, no slogans and no
  claims beyond the evidence. Read this BEFORE writing a commit, a PR, a comment or
  docstring, or any .md file. Pairs with ob-pr (PR structure).
---

# ob-voice

Prose reads like an engineer explaining a change to a colleague who will maintain it:
what changed, the mechanism, the consequence, how it was checked. The brief's README
rule applies everywhere: **measured and factual, no slogans, no overclaiming results
we don't have.**

## Commit messages

Conventional Commits, lowercase type, imperative subject, no trailing period. `(#N)` is
appended on squash-merge.

- **Types:** `feat`, `fix`, `refactor`, `perf`, `test`, `docs`, `chore`, `ci`, `build`.
  Pick by what the change does for a user of the tool.
- **Scope** is the area: `feat(guardrail):`, `fix(notify):`, `chore(hooks):`, `ci:`.
- **Subject: the change and its effect**, with "so" or "instead of" when the why fits.
  Code identifiers in backticks. Aim for ≤ 72 characters.

```text
feat(guardrail): reject quotes that only match after paraphrase so briefs quote the prospect
fix(notify): drop the webhook URL from request errors so it never reaches a traceback
chore(hooks): block `git add -f` so ignored files can't be staged
```

Claude-authored commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Pull requests

Title: same form as a commit subject. Body: `.github/pull_request_template.md`, with
ob-pr for each section. Wording rules:

- **For a fix, the root cause is mechanistic:** the function, value or ordering that
  fails and why.
- **Numbers with arrows:** `min_confidence 0.5 → 0.6`, `recall 0.71 → 0.86`.
- **Validation is honest:** the command and what it showed, and what was *not* run
  ("not run against the live API in this PR").

Claude-authored PR bodies end with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

## Code comments and docstrings

- Comments carry the *why*, the invariant, or the consequence of getting it wrong, in
  full sentences. No comment that restates the next line.
- "Keep in sync with X" wherever two places must change together.
- No commented-out code; no TODO without a trigger.
- Module docstring: one line stating its job. Public function: one imperative line;
  sections only when the signature isn't enough (`Raises`, a doctest example).

## Markdown

- **Open with what it is**, in one sentence. Then the result or the reason to care.
- Plain headings, tables for options and thresholds, numbered lists for procedures,
  one idea per paragraph.
- **Factual, not promotional.** "X, because Y" beats adjectives. Cut "powerful",
  "seamless", "robust", "AI-powered", "revolutionary".
- **The hypothesis is unvalidated, and every document says so where it matters.**
  Results are labelled with their data (synthetic, n transcripts), model and date.
- **Name limits and non-goals plainly** as design choices.
- **No real vendors, customers, people or review text.** Review themes are described
  at category level and paraphrased, with sources and dates.
- **State a fact once,** in the record that owns it (CLAUDE.md, records map), and point
  to it elsewhere. Restated rules drift; `docs-guard` checks only the facts that must repeat.
- **A spec has no changelog.** The brief and CLAUDE.md state the current design; what
  changed lives in git history.
- **No meta-framing.** Documents describe what the tool does; they never frame it as
  a portfolio piece, a take-home or work for a specific organization.
