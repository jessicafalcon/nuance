---
name: ob-security
description: >
  Security for objection-brief, a public repository from its first commit: the threat
  model (leaked secrets, prompt injection through transcripts, injection into Slack
  messages, path traversal, SQL, supply chain, CI, cost abuse, private data), the
  layered checks (Claude hooks, pre-commit, pre-push, CI, GitHub settings) and what each
  catches, and the security review checklist. Read this BEFORE writing code that reads
  a secret, calls the model or Slack, writes a file, touches CI or a dependency, and
  when reviewing any PR.
---

# ob-security

The repo is public from the first push. Anything committed, in any commit, in a PR
title, body or comment, is published and may be cached or indexed even after it is
deleted. Design every change for a reader who wants the API key, the webhook URL, or
a way to make the tool post something it shouldn't.

## The layers, and what each catches

| Layer | When | Catches |
|---|---|---|
| `.claude/settings.json` deny | Claude reads or edits `.env` | Claude never opens secrets |
| `content-guard` (PreToolUse) | Claude writes a file or notebook cell, or runs any Bash | Key shapes and filled `.env` variables everywhere; banned vendor names in written files and in published text (commit, tag, PR, issue, release, with their `-F`/`--body-file` files). Blocks published text it can't see (piped stdin, command substitution) |
| `git-guard` (PreToolUse) | Claude runs Bash | `--no-verify` (abbreviated too), `SKIP=`/`GIT_CONFIG_*`, hooksPath, `.git/hooks`, force push (bundled or abbreviated), push to `main`, `git add -f`, staging `.env`/`out/`, naming `.env` in any word or glob, recursive `grep`, expanding secret variables, dumping the environment, `gh auth token`, `gh repo` admin, mutating `gh api`, `merge --admin`; unwraps `env`, `sh -c`, `eval`, substitutions and heredocs fed to a shell |
| `boundary-guard` (PostToolUse + pre-commit) | A file in `src/objection_brief/` changes | `anthropic`/`requests`/`duckdb`/`os.environ` outside their owner; `dotenv` anywhere |
| pre-commit (commit) | Every commit, by anyone | gitleaks on staged changes, `detect-private-key`, content guard on files and the message, ruff `S` rules, zizmor on workflows, large files |
| pre-commit (push) | Every push | pytest, gitleaks and the content guard over the full history (messages and diffs), `uv audit` |
| CI (`check`, `secrets`, `content`, `audit`) | Every PR (and title/body edit) and push to `main` | pre-commit on every tracked file; gitleaks and the content guard over the whole history; the PR title and body; `uv audit`; no secrets in the environment |
| GitHub settings | Server side | Branch protection on `main` (PR + green CI required, no force push), secret-scanning push protection, Dependabot alerts and security updates, private vulnerability reporting |

Local hooks can be bypassed by anyone with a shell; CI and the GitHub settings can't
be bypassed by a PR. A check that matters exists in both.

## Threat model and the rule for each

### Secrets (ANTHROPIC_API_KEY, SLACK_WEBHOOK_URL)

- They live only in `.env` (gitignored, from `.env.example`) and are loaded with
  `uv run --env-file .env objection-brief ...`. No code reads `.env`.
- Read once in `config.py` from `os.environ`; never from a CLI argument (shell history
  and `ps` keep those).
- Never in output: not in logs, the DuckDB table, `out/`, exception messages, eval
  reports, or `repr()` of the settings object (mark the field `repr=False`).
- **The webhook URL is the credential.** `requests` exceptions include the URL in their
  message. `notify.py` catches `requests.RequestException` and re-raises a
  `NotifyError` with the status code only, `from None`, so the URL never reaches a
  traceback.
- CI has no secrets. A test that needs a key uses an obviously fake one built by
  concatenation, never a real-looking literal.
- A leaked secret is public the moment it's pushed: **rotate first**, then clean the
  history.

### Prompt injection through the transcript

The transcript is untrusted text: a prospect (or anyone who edits a recording) can
write "ignore previous instructions". Defenses, all required:

- The transcript goes in the user turn, wrapped in a delimited block, never in the
  system prompt. The system prompt says the block is data to analyze, not instructions.
- Forced tool use with a JSON schema: `theme_id` and `speaker` are enums, `confidence`
  is a bounded number. The model has no free-text channel into the brief except
  `quote`.
- The guardrail's verbatim check means `quote` can only be text that is in the
  transcript; a fabricated quote is rejected.
- The follow-up prompt gets only the theme's approved fields and the quote, and its
  output goes to the account executive as a draft, never to a prospect.

### Injection into Slack messages

Quotes and model text are rendered in Block Kit `mrkdwn`. Escape `&`, `<` and `>` in
every interpolated string (Slack's own escaping rules), so a transcript can't produce
`<!channel>`, `<!here>`, user mentions or disguised links (`<https://evil|click>`).
Set `unfurl_links` and `unfurl_media` to false. Respect Block Kit limits (3,000
characters per text field) by truncating, never by dropping the footer.

### Files and paths

- `transcript_id` is the input file's stem, validated against `^[A-Za-z0-9_-]{1,64}$`
  before it names anything in `out/`. Output paths are built from `out_dir / f"{id}.json"`
  and checked to stay under `out_dir`.
- Transcript input has a size cap (e.g. 200 KB) before it is read into a prompt.
- Writes go to a temp file and `os.replace` so a crash never leaves a half brief.

### SQL

DuckDB inserts use `?` parameters. No SQL is built from transcript text, model output
or file names. `scripts/report.sql` is static.

### YAML and deserialization

`yaml.safe_load` only. No `pickle`, `eval`, `exec`, `subprocess` with `shell=True` (ruff `S`).

### Supply chain

- Dependencies are locked with hashes in `uv.lock`; CI installs with `uv sync --locked`.
- New dependencies need the maintainer's approval (brief §14); prefer the stdlib.
- `uv audit` at push and in CI; Dependabot with a 7-day cooldown for routine updates.
- Actions and pre-commit hook repos are pinned by full commit SHA with the version in
  a comment; zizmor lints the workflows.

### CI

- `permissions: {}` at the top, `contents: read` per job; `persist-credentials: false`.
- Never `pull_request_target`, never `${{ github.event.* }}` interpolated into `run:`.
- No secrets in any workflow: the tests never need one.

### Cost and availability

- `max_tokens` on every call, a per-call timeout, bounded retries; `run-all` processes
  a fixed directory, not an open-ended glob.
- The README tells users to make a key in a workspace with a monthly spend limit.

### Private data

- The repo only ever holds synthetic transcripts. A real transcript or review text
  is never committed, even "temporarily"; `out/` is gitignored.
- The DuckDB file stores quotes; it stays in `out/`.

## Security review checklist

For each item, report pass, finding (with `path:line`), or not applicable:

1. Any secret, key shape, webhook URL or filled `.env` value in the diff, commit
   messages or PR body?
2. Any new place a secret could reach output (log, exception, file, `repr`, eval report)?
3. Untrusted text (transcript, model output) interpolated into a prompt's system turn,
   a Slack `mrkdwn` field without escaping, a file path, or SQL?
4. Model output used without schema validation and the guardrail?
5. Files written outside `out/`, or named from unvalidated input?
6. New dependency, action or tool: approved, locked, pinned by hash or SHA?
7. Workflow changes: permissions, triggers, interpolation, credentials?
8. Real vendor, customer, person or review text anywhere?
9. Any check weakened: a hook, a pre-commit stage, a CI step, a ruff rule, a
   `# noqa: S...` without a reason on the same line?
