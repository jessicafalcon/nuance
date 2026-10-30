#!/usr/bin/env python3
"""Fail when the records state a fact two ways (CLAUDE.md, records map).

Covers the drift that can be computed, not prose that restates a rule (the "state once"
rule in CLAUDE.md and ob-docs-auditor cover that):

- **Build steps:** each PROJECT-BRIEF.md §12 checkbox matches its README status row
  ("Done" if and only if ticked). In CLAUDE.md "Current status", every "Build step N
  ...: merged" line is ticked, and the latest ticked step has one; older steps may be
  condensed away.
- **Pinned versions:** uv (pyproject.toml `required-version`) and gitleaks (its
  .pre-commit-config.yaml rev) match the uv-lock hook rev, CI's gitleaks VERSION and every
  "uv X.Y.Z" / "gitleaks X.Y.Z" in the Markdown records. Other spellings are not matched.
- **Tooling tables:** every skill, agent and hook on disk has a row in its CLAUDE.md
  table, and every `ob-*` skill, agent and hook row exists on disk.

A section it parses that is missing or empty is a finding, so a format change can't
make it pass silently.

Entry points:

- Claude Code PostToolUse hook (payload on stdin): after an edit to a Markdown file, a
  file in .claude/hooks/ or a pinned config (pyproject.toml, .pre-commit-config.yaml,
  the CI workflow), checks the whole repo and exits 2 with the findings. A record update
  spans several files, so a finding mid-update is expected; finish the update.
- pre-commit and CI (`--check`): checks the repo in the working directory (pre-commit
  runs from the root), exits 1 on findings.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

BRIEF = "PROJECT-BRIEF.md"
README = "README.md"
CLAUDE = "CLAUDE.md"
PYPROJECT = "pyproject.toml"
PRECOMMIT = ".pre-commit-config.yaml"
CI = ".github/workflows/ci.yml"
# The Markdown records (CLAUDE.md, records map) plus the agent definitions.
DOC_GLOBS = ("*.md", ".claude/skills/*/SKILL.md", ".claude/agents/*.md", ".github/*.md")
# An edit to any Markdown file, these configs or a hook can change a checked fact.
WATCHED_FILES = {PYPROJECT, PRECOMMIT, CI}
HOOKS_DIR = ".claude/hooks/"

_SEMVER = r"(\d+\.\d+\.\d+)"
_VERSION = rf"v?{_SEMVER}"
# "uv 0.12.5" or "[uv](https://docs.astral.sh/uv/) 0.12.5"; same for gitleaks.
DOC_VERSION = {
    tool: re.compile(rf"(?<![\w-])\[?{tool}\]?(?:\([^)\s]*\))?\s+{_VERSION}")
    for tool in ("uv", "gitleaks")
}


def section(text: str, heading: str) -> str | None:
    """The body of the `## <heading>...` section, or None when there is none.

    >>> section("# T\\n## 12. Build\\n- a\\n## 13. Next\\n", "12.")
    '- a\\n'
    """
    match = re.search(rf"^## {re.escape(heading)}.*\n((?:(?!## ).*\n?)*)", text, re.MULTILINE)
    return match.group(1) if match else None


def first_cell_names(table: str) -> set[str]:
    """Backticked names in the first cell of each table row.

    >>> sorted(first_cell_names("| `a`, `b` (x) | y |\\n|---|---|\\n| `c` | z |"))
    ['a', 'b', 'c']
    """
    cells = re.findall(r"^\|([^|\n]*)\|", table, re.MULTILINE)
    return {name for cell in cells for name in re.findall(r"`([^`]+)`", cell)}


def step_findings(brief: str, readme: str, claude: str) -> list[str]:
    """Build-step state in the brief's §12, the README status table and CLAUDE.md."""
    steps_section = section(brief, "12.")
    status_section = section(readme, "Status")
    if steps_section is None or status_section is None:
        return [f"{BRIEF} §12 or {README} '## Status' is missing"]
    ticked = {
        int(n): mark == "x"
        for mark, n in re.findall(r"^- \[([ x])\] (\d+)\.", steps_section, re.MULTILINE)
    }
    rows = {
        int(n): state.strip()
        for n, state in re.findall(r"^\| (\d+) \|.*\|([^|\n]+)\|$", status_section, re.MULTILINE)
    }
    if not ticked or not rows:
        return [f"no build steps found in {BRIEF} §12 or the {README} status table"]
    if ticked.keys() != rows.keys():
        return [f"{README} status table has steps {sorted(rows)}; {BRIEF} §12 has {sorted(ticked)}"]
    found = [
        f"{README}: step {n} is {rows[n]!r} but {BRIEF} §12 has it "
        f"{'ticked' if done else 'unticked'}"
        for n, done in sorted(ticked.items())
        if done != (rows[n] == "Done")
    ]
    status = section(claude, "Current status")
    if status is None:
        return [*found, f"{CLAUDE}: '## Current status' is missing"]
    merged = {int(n) for n in re.findall(r"\*\*Build step (\d+)\b[^*]*:\*\* merged", status)}
    found += [
        f"{CLAUDE}: Current status says step {n} is merged; {BRIEF} §12 doesn't tick it"
        for n in sorted(merged)
        if not ticked.get(n)
    ]
    latest = max((n for n, done in ticked.items() if done), default=None)
    if latest is not None and latest not in merged:
        found.append(
            f"{CLAUDE}: Current status has no 'Build step {latest} (...): merged' line, "
            f"the latest step {BRIEF} §12 ticks"
        )
    return found


def version_findings(pyproject: str, precommit: str, ci: str, docs: dict[str, str]) -> list[str]:
    """Pinned uv and gitleaks versions against every config and Markdown copy."""
    uv_pin = re.search(rf'^required-version = "=={_SEMVER}"', pyproject, re.MULTILINE)
    uv_hook = re.search(rf"uv-pre-commit\n\s*rev: \S+ # frozen: {_VERSION}", precommit)
    gl_pin = re.search(rf"gitleaks\n\s*rev: \S+ # frozen: {_VERSION}", precommit)
    gl_ci = re.search(rf"Install gitleaks[\s\S]*?VERSION: {_VERSION}", ci)
    if not (uv_pin and uv_hook and gl_pin and gl_ci):
        return [f"a uv or gitleaks pin is missing from {PYPROJECT}, {PRECOMMIT} or {CI}"]
    truth = {"uv": (uv_pin[1], PYPROJECT), "gitleaks": (gl_pin[1], PRECOMMIT)}
    copies = [(PRECOMMIT, "uv", uv_hook[1]), (CI, "gitleaks", gl_ci[1])]
    for name, text in sorted(docs.items()):
        for tool, pattern in DOC_VERSION.items():
            copies += [(name, tool, version) for version in pattern.findall(text)]
    return [
        f"{name}: {tool} {version}, but {truth[tool][1]} pins {truth[tool][0]}"
        for name, tool, version in copies
        if version != truth[tool][0]
    ]


def table_findings(claude: str, skills: set[str], agents: set[str], hooks: set[str]) -> list[str]:
    """CLAUDE.md's skill, agent and hook tables against what is on disk.

    Hook files use underscores and their table rows use dashes, so names are compared
    with underscores turned into dashes.
    """
    found: list[str] = []
    for heading, on_disk, where in (
        ("Skills", skills, ".claude/skills/<name>/SKILL.md"),
        ("Agents", agents, ".claude/agents/<name>.md"),
        ("Hooks", hooks, ".claude/hooks/<name>.py or .sh"),
    ):
        rows = {n.replace("_", "-") for n in first_cell_names(section(claude, heading) or "")}
        on_disk = {n.replace("_", "-") for n in on_disk}
        if not rows:
            found.append(f"{CLAUDE}: no '## {heading}' table found")
            continue
        found += [f"{CLAUDE}: {heading} table has no row for `{n}`" for n in sorted(on_disk - rows)]
        # User-level and built-in skills live outside the repo; only ours are checked.
        ours = rows if heading != "Skills" else {n for n in rows if n.startswith("ob-")}
        found += [
            f"{CLAUDE}: {heading} table lists `{n}`, but there is no {where.replace('<name>', n)}"
            for n in sorted(ours - on_disk)
        ]
    return found


def check(root: Path) -> list[str]:
    """Every finding for the repository at `root`."""

    def read(rel: str) -> str:
        path = root / rel
        return path.read_text(encoding="utf-8") if path.is_file() else ""

    docs = {
        p.relative_to(root).as_posix(): p.read_text(encoding="utf-8")
        for pattern in DOC_GLOBS
        for p in sorted(root.glob(pattern))
    }
    brief, readme, claude = (docs.get(name, "") for name in (BRIEF, README, CLAUDE))
    return [
        *step_findings(brief, readme, claude),
        *version_findings(read(PYPROJECT), read(PRECOMMIT), read(CI), docs),
        *table_findings(
            claude,
            skills={p.parent.name for p in root.glob(".claude/skills/*/SKILL.md")},
            agents={p.stem for p in root.glob(".claude/agents/*.md")},
            hooks={p.stem for p in root.glob(f"{HOOKS_DIR}*") if p.suffix in {".py", ".sh"}},
        ),
    ]


def watched(rel_path: str) -> bool:
    return rel_path.endswith(".md") or rel_path in WATCHED_FILES or rel_path.startswith(HOOKS_DIR)


def main(argv: list[str]) -> int:
    hook_mode = argv != ["--check"]
    # pre-commit and CI run from the repository root; only the hook needs the project
    # directory, so a stray CLAUDE_PROJECT_DIR can't point a commit's check elsewhere.
    project = os.environ.get("CLAUDE_PROJECT_DIR") if hook_mode else None
    root = Path(project or Path.cwd()).resolve()
    if hook_mode:
        payload = json.load(sys.stdin)
        raw = (payload.get("tool_input") or {}).get("file_path", "")
        if not raw:
            return 0
        try:
            rel = Path(raw).resolve().relative_to(root).as_posix()
        except ValueError:
            return 0
        if not watched(rel):
            return 0

    found = check(root)
    if not found:
        return 0
    print("docs-guard: the records disagree (CLAUDE.md, records map).", file=sys.stderr)
    print("\n".join(f"  - {line}" for line in found), file=sys.stderr)
    print(
        "Update every record that states the fact. Mid-update this is expected; "
        "the commit fails until they agree.",
        file=sys.stderr,
    )
    return 2 if hook_mode else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
