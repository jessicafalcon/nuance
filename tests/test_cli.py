"""`run` refuses bad input, records only synthetic transcripts, and replays offline."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

from objection_brief import config, llm
from objection_brief.cli import main
from objection_brief.llm import Usage

REPO = Path(__file__).parents[1]


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A working directory with the theme file, synthetic transcripts and an empty cache."""
    (tmp_path / "data" / "transcripts").mkdir(parents=True)
    shutil.copy(REPO / "data" / "weaknesses.yaml", tmp_path / "data")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return tmp_path


@pytest.mark.parametrize(
    ("name", "content", "message"),
    [
        ("t01.md", "PROSPECT: Hi.\n", "not a .txt file"),
        ("t 01.txt", "PROSPECT: Hi.\n", "letters, digits"),
        ("t01.txt", "x" * (config.max_transcript_bytes + 1), "larger than"),
    ],
)
def test_bad_input_is_refused_before_any_call(
    repo: Path, capsys: pytest.CaptureFixture[str], name: str, content: str, message: str
) -> None:
    path = repo / "data" / "transcripts" / name
    path.write_text(content, "utf-8")
    assert main(["run", str(path)]) == 2
    assert message in capsys.readouterr().err


def test_missing_file_is_refused(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["run", "data/transcripts/absent.txt"]) == 2
    assert "not a .txt file" in capsys.readouterr().err


@pytest.fixture
def t01(repo: Path) -> Path:
    """t01 copied into the temporary repository, where nothing is recorded for it."""
    path = repo / "data" / "transcripts" / "t01_permissions.txt"
    shutil.copy(REPO / "data" / "transcripts" / path.name, path)
    return path


def test_offline_miss_is_reported_with_the_transcript_id(
    t01: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["run", str(t01), "--offline"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("t01_permissions: no recorded answer for key ")
    assert f"(prompt {config.detect_prompt_version}); run once without --offline" in err


@pytest.mark.parametrize(
    ("stem", "summary"),
    [("t01_permissions", "2 kept, 0 rejected"), ("t11_rep_raised_objection", "0 kept, 0 rejected")],
)
def test_recorded_answers_replay_offline_without_writing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], stem: str, summary: str
) -> None:
    # The step 5 checkpoint (brief §12): the committed entries replay with no call, which
    # pytest-socket would fail, and no new entry.
    monkeypatch.chdir(REPO)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    entries = sorted((REPO / "data" / "llm_cache").iterdir())

    assert main(["run", f"data/transcripts/{stem}.txt", "--offline"]) == 0
    out = capsys.readouterr().out
    assert out.startswith(f"{stem}: {config.detect_model}, replayed, recorded 2026-10-30")
    assert out.rstrip().endswith(summary)
    assert sorted((REPO / "data" / "llm_cache").iterdir()) == entries


def test_live_run_without_a_key_says_how_to_load_it(
    t01: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["run", str(t01)]) == 1
    assert "ANTHROPIC_API_KEY is not set; a live call loads it with" in capsys.readouterr().err


@pytest.fixture
def live(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A fake key and a fake live call in the temporary repository, with no network."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-" + "ant-" + "x" * 40)

    def fake_send(request: dict[str, Any], api_key: str) -> tuple[str, str | None, Usage]:
        return '{"objections": []}', "end_turn", Usage(100, 5)

    monkeypatch.setattr(llm, "_send", fake_send)


@pytest.mark.usefixtures("live")
def test_live_run_records_a_synthetic_transcript(t01: Path) -> None:
    assert main(["run", str(t01)]) == 0
    assert len(list((t01.parents[2] / "data" / "llm_cache").iterdir())) == 1


@pytest.mark.usefixtures("live")
def test_live_run_outside_the_synthetic_directory_is_never_recorded(repo: Path) -> None:
    # Brief §7.6, privacy: only transcripts under data/transcripts/ reach the cache.
    elsewhere = repo / "elsewhere" / "t01_permissions.txt"
    elsewhere.parent.mkdir()
    shutil.copy(REPO / "data" / "transcripts" / elsewhere.name, elsewhere)
    assert main(["run", str(elsewhere)]) == 0
    assert not (repo / "data" / "llm_cache").exists()


def test_run_outside_the_repository_root_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["run", str(REPO / "data" / "transcripts" / "t01_permissions.txt")]) == 2
    assert "weaknesses.yaml" in capsys.readouterr().err
