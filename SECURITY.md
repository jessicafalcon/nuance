# Security policy

objection-brief is a command-line prototype that sends synthetic sales-call transcripts
to the Anthropic API and posts a draft brief to an internal Slack webhook or a local
file. It holds no user data and runs no service.

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub: **Security → Report a
vulnerability** on this repository. Do not open a public issue for a suspected
vulnerability or a leaked credential.

Include what you found, how to reproduce it, and the commit you tested. You should get
a first response within 7 days.

## In scope

- A secret (API key, webhook URL) reaching the repository, a log, an output file or an
  error message.
- Transcript text that can change what the tool posts to Slack beyond the verbatim
  quote (prompt injection that bypasses the guardrail, `mrkdwn` injection).
- Files written outside `out/`, or SQL built from untrusted input.
- Weaknesses in the CI workflow or the repository's checks.

## How the repository protects itself

Secrets live only in a local, gitignored `.env`. Every commit runs secret scanning
(gitleaks) on the staged changes, a content check for key shapes and banned names on
the files and the message, and static security lint (ruff's bandit rules). Every push
runs the tests, gitleaks and the content check over the full history (messages and
diffs), and a dependency vulnerability audit (`uv audit`). CI repeats all of it on
every pull request, plus the PR title and body, with no secrets in its environment.
`main` accepts changes only through pull requests with passing checks.

## If you find a leaked credential

Report it privately as above. Credentials are rotated first, then removed from history.
