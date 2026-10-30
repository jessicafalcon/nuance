"""The content guard blocks secrets and banned terms in writes, commits and PR text."""

from __future__ import annotations

import io
import json
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest

import content_guard as cg

# Built by concatenation so no complete key shape is ever committed.
FAKE_SECRETS = {
    "Anthropic API key": "sk-" + "ant-api03-" + "a1B2" * 10,
    "Slack webhook URL": "https://hooks." + "slack.com/services/T000/B000/" + "x" * 24,
    "Slack token": "xox" + "b-" + "1234567890-abcdefghij",
    "GitHub token": "gh" + "p_" + "A" * 36,
    "AWS access key": "AK" + "IA" + "ABCDEFGHIJKLMNOP",
    "private key": "-----BEGIN RSA " + "PRIVATE KEY-----",
    "filled-in .env variable": "ANTHROPIC_API_KEY" + "=abcd1234efgh5678",
    "Google API key": "AI" + "za" + "B" * 35,
    "API key (sk-)": "sk-" + "proj-" + "c3D4" * 10,
}
TERM = "northwind planning"  # the fictional company stands in for a vendor name
DIGESTS = frozenset({cg.phrase_digest(TERM)})


@pytest.mark.parametrize(("name", "secret"), FAKE_SECRETS.items())
def test_each_secret_shape_is_found(name: str, secret: str) -> None:
    assert name in cg.find_secrets(f"config = '{secret}'")


@pytest.mark.parametrize(
    "text",
    [
        "ANTHROPIC_API_KEY=",
        "ANTHROPIC_API_KEY=<your key>",
        'key = os.environ["ANTHROPIC_API_KEY"]',
        "SLACK_WEBHOOK_URL=$SLACK_WEBHOOK_URL",
        "a task-ant-colony and sk-short",
    ],
)
def test_placeholders_and_references_pass(text: str) -> None:
    assert cg.find_secrets(text) == []


def test_banned_term_is_found_across_case_punctuation_and_line_breaks() -> None:
    assert cg.find_banned("Unlike NORTHWIND-\nPlanning, we...", DIGESTS) == [TERM]


def test_banned_term_inside_a_longer_word_passes() -> None:
    assert cg.find_banned("northwindplanning", DIGESTS) == []


def test_committed_terms_file_parses_to_digests() -> None:
    digests = cg.load_digests()
    assert digests
    assert all(len(d) == 64 and int(d, 16) >= 0 for d in digests)


def test_the_brief_itself_names_no_banned_term() -> None:
    brief = Path(__file__).parents[2] / "PROJECT-BRIEF.md"
    assert cg.find_banned(brief.read_text(encoding="utf-8"), cg.load_digests()) == []


def _hook(
    tool: str, tool_input: Mapping[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> int:
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.setattr(cg, "load_digests", lambda: DIGESTS)
    payload = {"tool_name": tool, "tool_input": tool_input, "cwd": str(tmp_path)}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    return cg.main([])


def test_hook_blocks_a_secret_written_into_the_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool_input = {"file_path": str(tmp_path / "x.py"), "content": FAKE_SECRETS["Anthropic API key"]}
    assert _hook("Write", tool_input, tmp_path, monkeypatch) == 2


def test_hook_blocks_a_banned_term_in_a_multiedit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool_input = {
        "file_path": str(tmp_path / "README.md"),
        "edits": [{"new_string": "Northwind Planning"}],
    }
    assert _hook("MultiEdit", tool_input, tmp_path, monkeypatch) == 2


def test_hook_ignores_writes_outside_the_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool_input = {
        "file_path": str(tmp_path.parent / "elsewhere" / "notes.md"),
        "content": "Northwind Planning",
    }
    assert _hook("Write", tool_input, tmp_path, monkeypatch) == 0


def test_hook_blocks_a_secret_in_any_bash_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    command = f"echo {FAKE_SECRETS['Slack webhook URL']} > notes.txt"
    assert _hook("Bash", {"command": command}, tmp_path, monkeypatch) == 2


def test_hook_checks_terms_only_in_publishing_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _hook("Bash", {"command": "grep -ri 'northwind planning' ."}, tmp_path, monkeypatch) == 0
    assert (
        _hook("Bash", {"command": "git commit -m 'like Northwind Planning'"}, tmp_path, monkeypatch)
        == 2
    )


def test_hook_reads_a_pr_body_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "body.md").write_text("Compared with Northwind Planning.", encoding="utf-8")
    command = "gh pr create --title t --body-file body.md"
    assert _hook("Bash", {"command": command}, tmp_path, monkeypatch) == 2


def test_hook_fails_closed_on_an_unreadable_body_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    command = "gh pr create --title t --body-file missing.md"
    assert _hook("Bash", {"command": command}, tmp_path, monkeypatch) == 2


def test_hook_fails_closed_on_piped_stdin_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    command = "cat body.md | gh pr create --title t --body-file -"
    assert _hook("Bash", {"command": command}, tmp_path, monkeypatch) == 2


def test_hook_reads_a_heredoc_body(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    command = "gh pr create --title t --body-file - <<'EOF'\nNorthwind Planning\nEOF"
    assert _hook("Bash", {"command": command}, tmp_path, monkeypatch) == 2


def test_file_mode_flags_a_staged_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    staged = tmp_path / "config.py"
    staged.write_text(f"KEY = '{FAKE_SECRETS['GitHub token']}'\n", encoding="utf-8")
    assert cg.main([str(staged)]) == 1
    err = capsys.readouterr().err
    assert "GitHub token" in err
    assert FAKE_SECRETS["GitHub token"] not in err  # the value itself is never echoed


def test_hash_term_prints_the_digest(capsys: pytest.CaptureFixture[str]) -> None:
    assert cg.main(["--hash-term", "Northwind Planning"]) == 0
    assert capsys.readouterr().out.strip() == cg.phrase_digest(TERM)


@pytest.mark.parametrize(
    "command",
    [
        "gh pr new --title t --body-file {d}/body.md",
        "gh issue new --title t --body-file {d}/body.md",
        "git -C {d} commit -F body.md",
        "git commit --file={d}/body.md",
        "git tag v1 -F {d}/body.md",
        "git notes add --file {d}/body.md",
        "gh release create v1 --notes-file {d}/body.md",
        "git commit -m \"$(cat <<'EOF'\nfeat: x\n\nUnlike Northwind Planning.\nEOF\n)\"",
    ],
)
def test_every_publishing_form_is_read(
    command: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "body.md").write_text("Compared with Northwind Planning.", encoding="utf-8")
    assert _hook("Bash", {"command": command.format(d=tmp_path)}, tmp_path, monkeypatch) == 2


@pytest.mark.parametrize(
    "command",
    [
        'gh pr create --title t --body "$(cat /tmp/body.md)"',
        'git commit -m "`cat msg.txt`"',
        "cat /tmp/b.md | gh pr create -t t --body-file - && cat <<'EOF'\nx\nEOF",
        "gh pr create -t t --body-file - < /tmp/b.md",
        "echo '<<EOF'\ngh pr create --title t --body-file missing.md\nEOF",
    ],
)
def test_text_the_guard_cannot_see_is_blocked(
    command: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _hook("Bash", {"command": command}, tmp_path, monkeypatch) == 2


@pytest.mark.parametrize(
    "command",
    [
        "git commit -m \"$(cat <<'EOF'\nfeat: add the loader\n\nIt's typed.\nEOF\n)\"",
        "git commit -F - <<'EOF'\nfeat: add the loader\nEOF",
        "git add -A && git commit -q -F - <<'EOF'\nfeat: x\nEOF\ngit log --oneline | head -4",
        "gh pr create --title 't' --body-file - <<'EOF'\n## Summary\nEOF",
        "git commit -m 'use `uv run` here'",
    ],
)
def test_visible_clean_text_passes(
    command: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _hook("Bash", {"command": command}, tmp_path, monkeypatch) == 0


def test_hook_checks_notebook_cells(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tool_input = {
        "file_path": str(tmp_path / "n.ipynb"),
        "new_source": FAKE_SECRETS["AWS access key"],
    }
    assert _hook("NotebookEdit", tool_input, tmp_path, monkeypatch) == 2


def test_history_mode_scans_the_git_log(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cg, "load_digests", lambda: DIGESTS)
    monkeypatch.setattr(cg, "git_history", lambda root: "commit\n\nfeat: beat Northwind Planning\n")
    assert cg.main(["--history"]) == 1
    monkeypatch.setattr(cg, "git_history", lambda root: "commit\n\nfeat: add the loader\n")
    assert cg.main(["--history"]) == 0


def test_a_crashing_guard_blocks() -> None:
    result = subprocess.run(  # noqa: S603
        [sys.executable, cg.__file__], input="not json", capture_output=True, text=True, check=False
    )
    assert result.returncode == 2


def test_body_file_variables_are_never_expanded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("FAKEVAR", "leaked-value-123")
    assert (
        _hook("Bash", {"command": 'git commit --body-file=$FAKEVA""R'}, tmp_path, monkeypatch) == 2
    )
    assert "leaked-value-123" not in capsys.readouterr().err


def test_an_apostrophe_in_one_heredoc_does_not_hide_the_next(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    command = "python3 - <<'PY'\nprint(\"it's `x`\")\nPY\ngit commit -F - <<'EOF'\nfeat: x\nEOF"
    assert _hook("Bash", {"command": command}, tmp_path, monkeypatch) == 0
    blocked = command.replace("feat: x", "feat: beat Northwind Planning")
    assert _hook("Bash", {"command": blocked}, tmp_path, monkeypatch) == 2
