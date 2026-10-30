# Objection Brief

A command-line prototype that reads a sales-call transcript, detects known product
objections raised by the prospect, and drafts a short brief for the account executive:
the objection theme, the prospect's exact words, the approved answer, one proof point,
and a follow-up paragraph the account executive edits before sending.

The setting is **Northwind Planning**, a fictional B2B planning software company. All
transcripts are synthetic. No real vendor, customer or person appears in this repository.

## Status

**In development: the table below shows which build steps are merged. The `run` command
detects and checks the objections in one transcript; briefs come in step 6.**

| Step | Scope | State |
|---|---|---|
| 1 | Scaffold, security hooks, CI | Done |
| 2 | Objection themes (`weaknesses.yaml`) and loader | Done |
| 3 | Synthetic transcripts and labels | Done |
| 4 | Guardrail (verbatim quote, speaker, confidence) | Done |
| 5 | Detection through the Anthropic API | Done |
| 6 | Brief, Slack or file output, DuckDB log | In review |
| 7 | Evaluation against labels, for two models | Not started |
| 8 | Full README: evidence, results, validation plan, cost, limitations | Not started |

The plan, scope and component specs are in [PROJECT-BRIEF.md](PROJECT-BRIEF.md).

## The hypothesis (unvalidated)

In a fast-growing sales team, objections about known product weaknesses are likely
handled inconsistently. Surfacing the objection right after the call, with an approved
honest answer, one proof point and a draft follow-up, may make handling more consistent
and follow-up faster. This has not been tested with a sales team; the finished README
will include a validation plan.

## Planned design

```text
transcript.txt → detect (one model call, structured output)
               → guardrail (quote must appear verbatim; prospect only; confidence ≥ 0.6; known theme)
               → log (every detection, kept or rejected, to DuckDB)
               → brief (Slack Block Kit + a follow-up drafted only from the approved answer)
               → notify (Slack webhook if configured, otherwise out/briefs/<id>.json)
```

Nothing is sent to prospects. Every brief is a draft for a person to review.

Model responses are recorded in a committed cache (`data/llm_cache/`) and replayed, so
the pipeline and the evaluation run offline, without an API key; from step 7, CI will
check that the committed evaluation reports reproduce exactly.

## Development setup

Requires [uv](https://docs.astral.sh/uv/) 0.12.5 and [gitleaks](https://github.com/gitleaks/gitleaks) 8.30.1 on `PATH`.

```bash
uv sync
uv run pre-commit install   # commit, commit-msg and pre-push checks
uv run pytest               # offline; network access fails the test
```

Live runs need an Anthropic API key: copy `.env.example` to `.env`,
fill it in, and run commands with `uv run --env-file .env objection-brief ...`. Use a key
from a workspace with a monthly spend limit.

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
