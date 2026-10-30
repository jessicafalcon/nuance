---
name: ob-pr
description: >
  How pull requests are shaped in objection-brief: one build step or one coherent change
  per PR, branch names, the title as the squash-merge commit, and what goes in each
  section of the PR template. Read this BEFORE writing a PR title or body (gate step 5
  in CLAUDE.md, which owns the gate itself). ob-voice governs the wording.
---

# ob-pr

## One change per PR

A PR carries **one coherent change**: usually one build step of the brief (§12), or a
fix, or related changes to the same area. Never two unrelated changes.

- A fix found while building something else is its own PR, not a drive-by edit.
- Generated files (`uv.lock`) travel with the change that caused them, marked generated.
- Every PR updates the records it implies (CLAUDE.md records map) in the same PR,
  including "Current status" in `CLAUDE.md` and the README's status section.

Branch: `<type>/<slug>`, from an up-to-date `main` (`feat/guardrail`, `docs/readme`).

## The title is the commit on `main`

PRs are **squash-merged**. The title becomes the single commit on `main`, with `(#N)`
appended, so it follows the commit rules in ob-voice exactly.

## Body

Fill every section of `.github/pull_request_template.md`, in order; a small PR keeps
each to a line. Delete the template's HTML comments. Pass the body with
`--body-file <absolute path>` or a heredoc; the content guard reads both.

- **Summary.** The problem, then what this PR changes; the brief step it serves.
- **Changes.** One line per file or unit; generated files marked.
- **Impact.** Security, Behaviour, Data, Cost: each "none" only after checking.
- **Validation.** One box per gate step that applied, with the command or agent and
  what it showed. An unticked box means not done.
- **Records updated.** Each record touched, per the records map, or "none implied".
- **Not done.** Deliberate omissions and open risks, each with where it's tracked, or
  "nothing".

## Before you push

The pre-PR gate is defined in `CLAUDE.md`, and only there. In the diff itself: no
debug output, commented-out code or stray files.
