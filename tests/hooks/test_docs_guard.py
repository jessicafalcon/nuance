"""The docs guard fails when two records state a computable fact differently."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

import docs_guard as dg

REPO = Path(__file__).parents[2]

BRIEF = """# Brief
## 12. Build order
- [x] 1. Scaffold
- [ ] 2. Themes
## 13. Acceptance
- [ ] 9. not a build step
"""
README = """# Readme
## Status
| Step | Scope | State |
|---|---|---|
| 1 | Scaffold | Done |
| 2 | Themes | In progress |
## Next
"""
CLAUDE = """# CLAUDE.md
## Skills
| Skill | Holds |
|---|---|
| `ob-craft` | how |
| `devils-advocate` (user-level) | critique |
## Agents
| Agent | When |
|---|---|
| `ob-reviewer` | each PR |
## Hooks (automatic)
| Hook | Fires on |
|---|---|
| `git-guard` | Bash |
| `reviewer_bash_allowlist` | Bash |
## Current status
- **Build step 1 (scaffold):** merged (#1).
"""
PYPROJECT = 'required-version = "==0.12.5"\n'
PRECOMMIT = """  - repo: https://github.com/astral-sh/uv-pre-commit
    rev: abc123 # frozen: 0.12.5
  - repo: https://github.com/gitleaks/gitleaks
    rev: def456 # frozen: v8.30.1
"""
CI = "      - name: Install gitleaks\n        env:\n          VERSION: 8.30.1\n"
SKILLS = {"ob-craft"}
AGENTS = {"ob-reviewer"}
HOOKS = {"git_guard", "reviewer_bash_allowlist"}
DRIFTED_README = README.replace("| In progress |", "| Done |")


def test_committed_repo_has_no_drift() -> None:
    assert dg.check(REPO) == []


def test_consistent_records_pass() -> None:
    assert dg.step_findings(BRIEF, README, CLAUDE) == []
    assert dg.version_findings(PYPROJECT, PRECOMMIT, CI, {"README.md": "uv 0.12.5"}) == []
    assert dg.table_findings(CLAUDE, SKILLS, AGENTS, HOOKS) == []


def test_done_row_for_unticked_step_is_reported() -> None:
    assert dg.step_findings(BRIEF, DRIFTED_README, CLAUDE) == [
        "README.md: step 2 is 'Done' but PROJECT-BRIEF.md §12 has it unticked"
    ]


def test_ticked_step_without_done_row_or_status_line_is_reported() -> None:
    brief = BRIEF.replace("- [ ] 2.", "- [x] 2.")
    assert dg.step_findings(brief, README, CLAUDE) == [
        "README.md: step 2 is 'In progress' but PROJECT-BRIEF.md §12 has it ticked",
        "CLAUDE.md: Current status has no 'Build step 2 (...): merged' line, "
        "the latest step PROJECT-BRIEF.md §12 ticks",
    ]


def test_older_merged_steps_may_be_condensed_out_of_the_status() -> None:
    brief = BRIEF.replace("- [ ] 2.", "- [x] 2.")
    readme = DRIFTED_README
    claude = CLAUDE.replace("**Build step 1 (scaffold):**", "**Build step 2 (themes):**")
    assert dg.step_findings(brief, readme, claude) == []


def test_missing_current_status_fails_instead_of_passing() -> None:
    claude = CLAUDE.replace("## Current status", "## Status notes")
    assert dg.step_findings(BRIEF, README, claude) == ["CLAUDE.md: '## Current status' is missing"]


def test_merged_status_for_unticked_step_is_reported() -> None:
    claude = CLAUDE + "- **Build step 2 (themes):** merged (#4).\n"
    assert dg.step_findings(BRIEF, README, claude) == [
        "CLAUDE.md: Current status says step 2 is merged; PROJECT-BRIEF.md §12 doesn't tick it"
    ]


def test_missing_status_row_is_reported() -> None:
    readme = README.replace("| 2 | Themes | In progress |\n", "")
    assert dg.step_findings(BRIEF, readme, CLAUDE) == [
        "README.md status table has steps [1]; PROJECT-BRIEF.md §12 has [1, 2]"
    ]


@pytest.mark.parametrize(
    ("brief", "readme"),
    [("# Brief\n", README), (BRIEF, "# Readme\n"), ("## 12. Build\nprose only\n", README)],
)
def test_unparseable_step_records_fail_instead_of_passing(brief: str, readme: str) -> None:
    assert dg.step_findings(brief, readme, CLAUDE)


@pytest.mark.parametrize(
    ("doc", "expected"),
    [
        ("Requires uv 0.12.4.", "README.md: uv 0.12.4, but pyproject.toml pins 0.12.5"),
        (
            "Requires [uv](https://docs.astral.sh/uv/) 0.13.0.",
            "README.md: uv 0.13.0, but pyproject.toml pins 0.12.5",
        ),
        (
            "gitleaks v8.29.0 on PATH",
            "README.md: gitleaks 8.29.0, but .pre-commit-config.yaml pins 8.30.1",
        ),
    ],
)
def test_stale_version_in_a_doc_is_reported(doc: str, expected: str) -> None:
    assert dg.version_findings(PYPROJECT, PRECOMMIT, CI, {"README.md": doc}) == [expected]


@pytest.mark.parametrize("doc", ["uv-lock hook", "uv_build>=0.12", "the uv audit step 1.2.3"])
def test_other_uv_mentions_are_not_versions(doc: str) -> None:
    assert dg.version_findings(PYPROJECT, PRECOMMIT, CI, {"README.md": doc}) == []


def test_stale_config_copies_are_reported() -> None:
    precommit = PRECOMMIT.replace("frozen: 0.12.5", "frozen: 0.12.4")
    ci = CI.replace("8.30.1", "8.30.0")
    assert dg.version_findings(PYPROJECT, precommit, ci, {}) == [
        ".pre-commit-config.yaml: uv 0.12.4, but pyproject.toml pins 0.12.5",
        ".github/workflows/ci.yml: gitleaks 8.30.0, but .pre-commit-config.yaml pins 8.30.1",
    ]


def test_missing_pin_fails_instead_of_passing() -> None:
    assert dg.version_findings("", PRECOMMIT, CI, {})


def test_tool_on_disk_without_a_row_is_reported() -> None:
    found = dg.table_findings(CLAUDE, SKILLS | {"ob-docs"}, AGENTS, HOOKS | {"docs_guard"})
    assert found == [
        "CLAUDE.md: Skills table has no row for `ob-docs`",
        "CLAUDE.md: Hooks table has no row for `docs-guard`",
    ]


def test_row_without_a_tool_on_disk_is_reported() -> None:
    found = dg.table_findings(CLAUDE, set(), set(), {"git_guard"})
    assert found == [
        "CLAUDE.md: Skills table lists `ob-craft`, but there is no .claude/skills/ob-craft/SKILL.md",
        "CLAUDE.md: Agents table lists `ob-reviewer`, but there is no .claude/agents/ob-reviewer.md",
        "CLAUDE.md: Hooks table lists `reviewer-bash-allowlist`, but there is no "
        ".claude/hooks/reviewer-bash-allowlist.py or .sh",
    ]


def test_missing_table_fails_instead_of_passing() -> None:
    claude = CLAUDE.replace("## Agents", "## Other")
    assert "CLAUDE.md: no '## Agents' table found" in dg.table_findings(
        claude, SKILLS, AGENTS, HOOKS
    )


def _repo(tmp_path: Path, readme: str = README) -> Path:
    files = {
        "PROJECT-BRIEF.md": BRIEF,
        "README.md": readme,
        "CLAUDE.md": CLAUDE,
        "pyproject.toml": PYPROJECT,
        ".pre-commit-config.yaml": PRECOMMIT,
        ".github/workflows/ci.yml": CI,
        ".claude/skills/ob-craft/SKILL.md": "craft",
        ".claude/agents/ob-reviewer.md": "reviewer",
        ".claude/hooks/git_guard.py": "",
        ".claude/hooks/reviewer_bash_allowlist.py": "",
        ".claude/hooks/banned-terms.sha256": "",
    }
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    return tmp_path


def _hook(monkeypatch: pytest.MonkeyPatch, root: Path, file_path: str) -> int:
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    payload = {"tool_name": "Edit", "tool_input": {"file_path": str(root / file_path)}}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    return dg.main([])


def test_check_mode_exits_1_on_drift(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(_repo(tmp_path, DRIFTED_README))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(REPO))  # ignored in --check mode
    assert dg.main(["--check"]) == 1


def test_check_mode_passes_a_consistent_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(_repo(tmp_path))
    assert dg.main(["--check"]) == 0


def test_hook_reports_drift_after_a_doc_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _repo(tmp_path, DRIFTED_README)
    assert _hook(monkeypatch, root, "README.md") == 2
    assert "step 2 is 'Done'" in capsys.readouterr().err


@pytest.mark.parametrize(
    "file_path",
    [
        "pyproject.toml",
        ".pre-commit-config.yaml",
        ".github/workflows/ci.yml",
        ".claude/hooks/new_guard.py",
        ".claude/skills/ob-craft/SKILL.md",
    ],
)
def test_hook_runs_after_a_record_config_or_hook_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, file_path: str
) -> None:
    assert _hook(monkeypatch, _repo(tmp_path, DRIFTED_README), file_path) == 2


@pytest.mark.parametrize("tool_input", [{}, {"file_path": ""}, None])
def test_hook_without_a_file_path_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tool_input: object
) -> None:
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(_repo(tmp_path, DRIFTED_README)))
    payload = {"tool_name": "Edit", "tool_input": tool_input}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert dg.main([]) == 0


@pytest.mark.parametrize("file_path", ["src/objection_brief/weaknesses.py", "data/labels.yaml"])
def test_hook_ignores_edits_that_cant_change_a_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, file_path: str
) -> None:
    root = _repo(tmp_path, DRIFTED_README)
    assert _hook(monkeypatch, root, file_path) == 0


def test_hook_ignores_files_outside_the_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path / "repo", DRIFTED_README)
    assert _hook(monkeypatch, root, "../elsewhere/README.md") == 0
