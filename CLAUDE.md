# CLAUDE.md

Session instructions for objection-brief. The plan lives in [PROJECT-BRIEF.md](PROJECT-BRIEF.md)
(read it before writing code); this file is *how we work*, and the single authority
on **when** to use each skill, agent, hook and command. Skills hold the *how*. If a
skill or agent description disagrees with this file about when to run, this file
wins, and the disagreement is a finding to fix.

**This repository is public.** Every file, commit message, PR title, body and comment
is published the moment it's pushed.

## Repo map

```text
src/objection_brief/  the pipeline: config, weaknesses, llm (the only model edge), detect,
                      guardrail, brief, notify, log, evaluate, cli
data/                 weaknesses.yaml, synthetic transcripts/, labels.yaml, llm_cache/ (recorded model responses)
scripts/              report.sql
reports/              eval_<model>.md, generated offline from data/llm_cache/
tests/                one file per module, test_data.py for the committed data, tests/hooks/ for the guard hooks
out/                  gitignored: briefs, the DuckDB file
.claude/              settings.json, hooks/, skills/, agents/
.github/              CI workflow, Dependabot, PR template
```

## Principles, in priority order

1. **Nothing unsafe leaves the machine.** No secret, real vendor, real person or real
   transcript in any commit or PR. Checks run in hooks *and* CI (ob-security).
2. **Trustworthy briefs.** The guardrail is the only path to a brief; quotes are
   verbatim; answers come only from `weaknesses.yaml`; a human sends anything
   (ob-correctness).
3. **Reproducible.** Every model call replays from the committed cache; the pipeline
   and the eval run offline in CI, and the README quotes only committed reports.
4. **Nothing disappears silently.** Every detection is logged, kept or rejected, with
   a reason; the eval lists its own in its report.
5. **Small, typed, tested, offline.** One job per module, one owner per dependency,
   one test per non-trivial unit, no network in tests.
6. **Smallest scope.** When unsure, choose the smaller option and note it in the README
   (brief §14).

## What to invoke, when

"Skill" means invoke it with the Skill tool *before* that kind of work, even if you
remember it: the file is the current standard.

| Moment | Invoke | Then |
|---|---|---|
| **Fresh clone** | `uv sync`, `uv run pre-commit install`; gitleaks 8.30.1 on `PATH` | Installs the commit, commit-msg and pre-push hooks |
| **A live call** needs the API key or webhook | `cp .env.example .env`; the maintainer fills it, never Claude | Run with `uv run --env-file .env objection-brief ...`; new answers are recorded in `data/llm_cache/` and committed with the change. Everything else runs with `--offline`. A new variable goes into `.env.example` (empty) and the content and git guards' variable lists in the same change |
| **Writing** Python, SQL or YAML; choosing a dependency | Skill `ob-craft` | A dependency outside brief §4: ask first |
| ↳ in `src/`, `data/` or eval logic | + skill `ob-correctness` | |
| ↳ reading a secret, calling the model or Slack, writing a file, touching CI, hooks or a dependency | + skill `ob-security` | |
| **Writing or changing a test** | Skill `ob-tests` | |
| **Writing prose**: commit, comment, docstring, PR body, any `.md` | Skill `ob-voice` | |
| **Every edit and Bash call** | Hooks run automatically (table below) | Act on what they report before moving on |
| **Before each commit** | `uv run --frozen pytest` and `uv run --frozen pre-commit run --all-files` | Commit only when both are green |
| **A build checkpoint** (brief §12) | Run what the checkpoint names; step 3 (transcripts) and step 8 (README) wait for the maintainer's review | Never start the next step with failing tests |
| **A plan or design choice** worth a second opinion | Skill `devils-advocate` (user-level) | Before building it |
| **Reading many files** where only the conclusion matters | A subagent (Opus 5.5, see Efficiency) | Keep the conclusion, not the dumps |
| **A result comes back shallow** | Raise effort `high` → `xhigh` for that task only | Back to `high` after |
| **Before opening a PR** | The pre-PR gate below | |
| **A durable, non-obvious fact** (a vendor limit, a preference) | Project memory | Not what the repo already records |

### The pre-PR gate

In order. Stop at the first failing step, fix, and restart from step 1.

1. **Checks green:** `uv run --frozen pytest` and `uv run --frozen pre-commit run --all-files`.
2. **Cleanup review** (reuse, simplification, efficiency, altitude), sized by the PR:
   - **Docs only** (every changed path is `*.md`): skip; the auditor covers it.
   - **Code** (any `*.py`, `*.sql`, hooks included): skill `simplify`.
   - **Config, CI, settings only:** the four angles yourself.
3. **Review agent:**
   - **Not docs only:** `ob-reviewer` on the branch (it runs the security pass).
   - **Docs only:** `ob-docs-auditor`, scoped to the changed docs.
   - **Trust-boundary PRs** (touching `llm.py`, `detect.py`, `brief.py`, `notify.py`,
     `log.py`, `cli.py`, `.github/`, `.claude/hooks/`, `.pre-commit-config.yaml`): also
     skill `security-review`.
   - **Last PR of the build (README):** also `ob-docs-auditor` over the whole repo.

   Give the agent the diff range, the brief sections at stake, and the rules; it reads
   further only when a finding needs it.
4. **Fix** every blocker and major finding and verify each fix. Re-run the agent only
   if a blocker needed a design change. Bring each **decide** to the maintainer.
5. **PR text:** skill `ob-pr` for title and body from `.github/pull_request_template.md`;
   skill `ob-voice` for wording.
6. **Records:** brief §12 checkbox ticked, "Current status" below moved forward, README
   status section matches what exists (records map).
7. **Push the branch, open the PR, and stop.** The maintainer reviews; on "merge",
   squash-merge with `gh pr merge <N> --squash --delete-branch`, `git switch main &&
   git pull`, then branch the next step.

## Skills

| Skill | Holds |
|---|---|
| `ob-craft` | Module map and dependency owners, the model-call function, typing, tooling |
| `ob-correctness` | The seven invariants, boundary validation, the review checklist |
| `ob-security` | Threat model, the check layers, the security review checklist |
| `ob-tests` | Test forms, what each module must prove, fakes, offline rule |
| `ob-voice` | Commits, PRs, comments, docstrings, Markdown tone |
| `ob-pr` | PR size, branch, title, body sections |
| `devils-advocate` (user-level) | Critique of a plan before it's built |
| `simplify`, `security-review` (built-in) | Gate steps 2 and 3 |

## Agents

Both are read-only (a hook limits their shell to checks), start on a fresh context and
report findings plus the record updates they imply. When they can't tell whether the
code or a record is right, they mark it **decide** and the maintainer chooses.

| Agent | When | Scope |
|---|---|---|
| `ob-reviewer` | Gate step 3, every PR that isn't docs only | Branch diff: brief coherence, invariants, security, tests, records |
| `ob-docs-auditor` | Gate step 3: docs-only PRs, and the final README PR | Documents vs code and eval outputs; tone; content rules |

## Hooks (automatic) and what to do when one fires

| Hook | Fires on | When it reports |
|---|---|---|
| `content-guard` | Before every Write/Edit/NotebookEdit in the repo and every Bash call (secrets); commit, tag and PR/issue/release text and their `-F`/`--body-file` files (secrets and banned terms); pre-commit on staged files and the commit message; pre-push and CI over the full history and the PR text | Blocked. Remove the secret (it belongs in `.env`) or describe the category instead of the vendor. Text it can't see blocks: pass an absolute `--body-file`, a heredoc, or `$(cat <<'EOF' ... EOF)`, never piped stdin or another substitution. To ban a new term: `python3 .claude/hooks/content_guard.py --hash-term "<name>" >> .claude/hooks/banned-terms.sha256` |
| `git-guard` | Before every Bash call | Blocked: check bypasses, force push, push to `main`, `git add -f`, `.env` reads (search with `git grep` or `rg`, never `grep -r`), secret expansion, env dumps, `gh repo` admin, mutating `gh api`, `merge --admin`. GitHub settings changes are the maintainer's: give them the exact command to run with `!` |
| `ruff-on-edit` | After editing a `*.py` file | Fix any unresolved lint it prints before the next edit |
| `boundary-guard` | After editing `src/objection_brief/*.py`; pre-commit | Move the import or environment read into its owner (ob-craft module map) |
| `docs-guard` | After editing a `*.md`, a file in `.claude/hooks/`, `pyproject.toml`, `.pre-commit-config.yaml` or the CI workflow; pre-commit | The records disagree on a build step, a pinned version or a tool table. Update every record it names; mid-update this is expected |
| `reviewer_bash_allowlist` | Every Bash call; enforced only for the two review agents | Keeps them read-only; also registered in each agent's frontmatter with `--enforce` |

## Records: every change updates its owner in the same PR

| Fact | Owner |
|---|---|
| The plan: scope, specs, build steps (checkboxes), acceptance criteria, decisions (§15) | `PROJECT-BRIEF.md` |
| How we work; when to invoke each tool; current status | `CLAUDE.md` |
| How code, tests, security and prose are done | `.claude/skills/ob-*` |
| What a user sees: status, quickstart, results (generated), limits | `README.md` |
| How to report a vulnerability; what the repo protects | `SECURITY.md` |

- **A step lands:** tick it in brief §12; move "Current status"; update the README status.
- **A decision is taken:** one line in brief §15 (decision, reason), written into every
  section it affects as the current design.
- **A command, threshold, hook or skill changes:** update every record that states it.
- **State a fact once,** in its owner; other documents point to it ("the rules in brief
  §6.1"). `docs-guard` checks the facts that must repeat: build-step state, pinned tool
  versions, and the CLAUDE.md skill, agent and hook tables (the checked copy; other
  lists of tools are left to review).
- **Code and a record disagree** and it's unclear which is right: ask the maintainer.

## Git

- One coherent change per branch and PR (`<type>/<slug>`), from an up-to-date `main`,
  squash-merged. Branch commits are small and green.
- Never commit to `main` directly; `main` changes only through merged PRs (branch
  protection enforces it server side).
- Confirm with the maintainer before force-push, history rewrite, or any change to the
  GitHub repo's settings or visibility.

## Efficiency (Opus 5.5 everywhere)

`.claude/settings.json` pins Opus 5.5 for the main session and every subagent, so
delegation is about context, not price.

- Delegate fan-out, not single lookups: a fresh agent re-reads the brief and skills
  (tens of thousands of tokens). The gate is sized by PR type for that reason.
- Name the brief sections an agent should read instead of "read the brief".
- Keep this file short and stable: it is the cached prefix of every session. Batch
  edits to it at PR time.
- When a session grows long, at a breakpoint right after a merge, give the maintainer
  a kickoff prompt for a fresh session; "Current status" must be enough to resume.

## Communication

Result first: what changed, passed or failed, then details. After a task: files
touched, commands run and results, records updated, open risks, next step.

## Current status

- **Build step 1 (scaffold, security hooks, CI, replay rules):** merged (#1). The repo is
  public; branch protection on `main` requires the `check`, `secrets`, `content` and
  `audit` jobs; secret-scanning push protection and Dependabot are on.
- **Build step 2 (themes and loader):** merged (#4). `data/weaknesses.yaml` with the six
  themes (brief §6.1, all `draft`, placeholder proofs); `weaknesses.load_themes` →
  `tuple[Theme, ...]`, raising `WeaknessesError` that names the theme and field.
- **Build step 3 (transcripts and labels):** merged (#7). Twelve synthetic transcripts in
  `data/transcripts/` (t01–t08 positives covering all six themes, t07 and t08 with two
  themes; t09–t12 the four negatives in brief §6.2), one speaker turn per line;
  `data/labels.yaml` keyed by file stem. `tests/test_data.py` pins these invariants.
- **Build step 4 (guardrail):** merged (#8). `guardrail.check` →
  one `Verdict` per detection, the first failing check naming its `RejectReason`
  (brief §7.2: word boundaries, a single `PROSPECT:` turn); `normalize` serves both
  sides of the match; `config.min_confidence` 0.6.
- **Build step 5 (llm and detect):** merged (#10). `llm.call_model`
  replays from `data/llm_cache/` (brief §7.6); `detect` uses structured outputs at effort
  `low`, no `temperature`, and owns `RejectReason` with `malformed_detection` (brief §15);
  `objection-brief run <transcript> [--offline]` prints the raw answer and the verdicts.
  Checkpoint run: live answers for t01 and t11 recorded in `data/llm_cache/` (Claude
  Haiku 5.5, 2026-10-30, re-recorded at `detect-v2` in step 7); `tests/test_cli.py`
  replays both offline.
- **Build step 6 (brief, notify, log, `run-all`):** merged (#11). One brief
  item per theme (its most confident kept detection); `guardrail.count_rejections` takes
  the malformed items too; logged before the brief. The follow-up (Claude Haiku 5.5,
  `follow-up-v4` since step 7) writes a `[PROOF]` slot the code fills, and a draft that fails a check
  is withheld with its reason; the v1 and v2 recordings, and why each changed, are in
  #11's commits (brief §15). `run-all --offline` writes briefs for t01–t08 only, and
  `tests/test_pipeline.py` replays it. Briefs go to `out/briefs/`; the Slack path is
  tested with fakes only.
- **Build step 7 (eval):** merged (#12). `objection-brief eval --model <id>
  [--offline]` (`evaluate.py`) scores (transcript, theme) pairs after the guardrail
  against `labels.yaml`, through a strict loader, and writes `reports/eval_<model>.md`;
  it logs nothing (brief §9, §15). `llm.tagged` escapes a closing tag inside a
  transcript or quote (`detect-v2`, `follow-up-v4`), and every entry was re-recorded
  live on 2026-10-30. Claude Haiku 5.5 and Claude Sonnet 5.5 both score 10 of 10 pairs
  with no false positives; detection costs $0.32 and $5.46 per 1,000 transcripts (prices
  checked 2026-10-30). `excel_interop.detect_when` is unchanged: neither report shows a
  false positive from its second clause. `tests/test_pipeline.py` reproduces both
  reports byte for byte.
- **Next:** step 8 (README, brief §11), once step 7 merges; follow-up cost goes in its
  cost section (brief §15).
- **Open decisions:** none.
