"""`run`, `run-all` and `eval` refuse bad input, record only synthetic transcripts, and log as specified."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import duckdb
import pytest

from objection_brief import config, llm, notify
from objection_brief.brief import FOOTER
from objection_brief.cli import main
from objection_brief.llm import Usage

REPO = Path(__file__).parents[1]
T01_QUOTE = (
    "Honestly, setting up permissions so each of our fifty budget owners only sees their own "
    "cost center sounds like a project in itself."
)
FOLLOW_UP = (
    "Scoping access for fifty budget owners does take deliberate setup. For example, [PROOF]. "
    "Could we book a 30-minute permissions design session?"
)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A working directory with the theme file, synthetic transcripts and an empty cache."""
    (tmp_path / "data" / "transcripts").mkdir(parents=True)
    shutil.copy(REPO / "data" / "weaknesses.yaml", tmp_path / "data")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    return tmp_path


def _copy_transcript(repo: Path, stem: str) -> Path:
    path = repo / "data" / "transcripts" / f"{stem}.txt"
    shutil.copy(REPO / "data" / "transcripts" / path.name, path)
    return path


def _logged(repo: Path) -> list[tuple[Any, ...]]:
    with duckdb.connect(str(repo / config.log_db), read_only=True) as db:
        return db.execute("SELECT transcript_id, theme_id, kept FROM detections").fetchall()


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
    return _copy_transcript(repo, "t01_permissions")


def test_offline_miss_is_reported_with_the_transcript_id(
    t01: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["run", str(t01), "--offline"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("t01_permissions: no recorded answer for key ")
    assert f"(prompt {config.detect_prompt_version}); run once without --offline" in err


def test_a_recorded_negative_replays_offline_with_no_brief(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # t11's committed answer has no objections: it replays with no call, which
    # pytest-socket would fail, logs nothing, and writes neither a brief nor an entry.
    shutil.copytree(REPO / "data" / "llm_cache", repo / "data" / "llm_cache")
    t11 = _copy_transcript(repo, "t11_rep_raised_objection")
    entries = sorted((repo / "data" / "llm_cache").iterdir())

    assert main(["run", str(t11), "--offline"]) == 0
    out = capsys.readouterr().out
    assert out.startswith(
        f"t11_rep_raised_objection: {config.detect_model}, replayed, recorded 2026-10-30"
    )
    assert "0 kept, 0 rejected\n  no brief: nothing kept\n" in out
    assert not (repo / config.briefs_dir).exists()
    assert _logged(repo) == []
    assert sorted((repo / "data" / "llm_cache").iterdir()) == entries


def test_live_run_without_a_key_says_how_to_load_it(
    t01: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["run", str(t01)]) == 1
    assert "ANTHROPIC_API_KEY is not set; a live call loads it with" in capsys.readouterr().err


@pytest.fixture
def live(repo: Path, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """A fake key and a fake live call, with no network; returns the requests it got.

    Detection (the request with a schema) finds t01's objection; the follow-up call
    returns a grounded paragraph.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-" + "ant-" + "x" * 40)
    requests: list[dict[str, Any]] = []

    def fake_send(request: dict[str, Any], api_key: str) -> tuple[str, str | None, Usage]:
        requests.append(request)
        if "format" in request["output_config"]:
            item = {
                "theme_id": "access_permissions",
                "quote": T01_QUOTE,
                "speaker": "prospect",
                "confidence": 0.95,
            }
            return json.dumps({"objections": [item]}), "end_turn", Usage(100, 20)
        return FOLLOW_UP, "end_turn", Usage(80, 60)

    monkeypatch.setattr(llm, "_send", fake_send)
    return requests


def test_live_run_logs_writes_and_previews_the_brief(
    t01: Path, live: list[dict[str, Any]], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["run", str(t01)]) == 0

    brief = json.loads(
        (t01.parents[2] / config.briefs_dir / "t01_permissions.json").read_text("utf-8")
    )
    proof = (
        "[PLACEHOLDER] customer story: mid-market company scoped cost-center access in onboarding"
    )
    assert any(
        FOLLOW_UP.replace("[PROOF]", proof) in block.get("text", {}).get("text", "")
        for block in brief["blocks"]
    )
    out = capsys.readouterr().out
    assert "brief written to out/briefs/t01_permissions.json" in out
    assert out.rstrip().endswith(FOOTER)
    assert _logged(t01.parents[2]) == [("t01_permissions", "access_permissions", True)]
    # One detection call, then one follow-up call for the kept theme.
    assert [r["model"] for r in live] == [config.detect_model, config.follow_up_model]


@pytest.mark.usefixtures("live")
def test_live_run_records_a_synthetic_transcript(t01: Path) -> None:
    assert main(["run", str(t01)]) == 0
    assert len(list((t01.parents[2] / "data" / "llm_cache").iterdir())) == 2


@pytest.mark.usefixtures("live")
def test_live_run_outside_the_synthetic_directory_is_never_recorded(repo: Path) -> None:
    # Brief §7.6, privacy: only transcripts under data/transcripts/ reach the cache.
    elsewhere = repo / "elsewhere" / "t01_permissions.txt"
    elsewhere.parent.mkdir()
    shutil.copy(REPO / "data" / "transcripts" / elsewhere.name, elsewhere)
    assert main(["run", str(elsewhere)]) == 0
    assert not (repo / "data" / "llm_cache").exists()


@pytest.mark.usefixtures("live")
def test_an_offline_follow_up_miss_fails_the_transcript_after_logging(
    t01: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = t01.parents[2]
    assert main(["run", str(t01)]) == 0
    for entry in (repo / "data" / "llm_cache").iterdir():
        if (
            json.loads(entry.read_text("utf-8"))["prompt_version"]
            == config.follow_up_prompt_version
        ):
            entry.unlink()
    (repo / config.briefs_dir / "t01_permissions.json").unlink()
    capsys.readouterr()

    assert main(["run", str(t01), "--offline"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("t01_permissions: no recorded answer for key ")
    assert f"(prompt {config.follow_up_prompt_version})" in err
    # The detections were logged on both runs, and no brief claims a draft it lacks.
    assert len(_logged(repo)) == 2
    assert not (repo / config.briefs_dir / "t01_permissions.json").exists()


@pytest.mark.usefixtures("live")
def test_run_all_continues_past_a_failed_transcript(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _copy_transcript(repo, "t01_permissions")
    (repo / "data" / "transcripts" / "t00_bad.txt").write_bytes(b"PROSPECT: \xff\n")

    assert main(["run-all"]) == 2
    captured = capsys.readouterr()
    assert "t00_bad.txt is not UTF-8 text" in captured.err
    assert captured.out.rstrip().endswith("2 transcripts, 1 failed (t00_bad)")
    assert (repo / config.briefs_dir / "t01_permissions.json").exists()


def test_a_webhook_that_is_not_slacks_is_refused_before_any_call(
    t01: Path,
    live: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Checked up front, so a mistyped URL costs no model call; no request is made
    # either (pytest-socket would fail one).
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://collector.example/hook")
    assert main(["run", str(t01)]) == 2
    err = capsys.readouterr().err
    assert "SLACK_WEBHOOK_URL must start with https://hooks.slack.com/" in err
    assert "collector.example" not in err
    assert live == []


def test_a_webhook_run_posts_and_reports_withheld_drafts_and_tokens(
    t01: Path,
    live: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    posted: list[Any] = []
    monkeypatch.setattr(notify, "_send", lambda url, message: posted.append(message) or 200)
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/" + "services/" + "x" * 24)
    # A follow-up that states a figure nobody gave is withheld, and the run says so.
    invented = FOLLOW_UP + " Teams save 40% of setup time."
    send = llm._send  # pyright: ignore[reportPrivateUsage]

    def fake_send(request: dict[str, Any], api_key: str) -> tuple[str, str | None, Usage]:
        output, stop, usage = send(request, api_key)
        return (output if "format" in request["output_config"] else invented), stop, usage

    monkeypatch.setattr(llm, "_send", fake_send)

    assert main(["run", str(t01)]) == 0
    out = capsys.readouterr().out
    assert (
        "brief posted to Slack, 1 follow-up call(s), 80 input and 60 output tokens, "
        "1 draft(s) withheld"
    ) in out
    assert len(posted) == 1
    assert invented not in json.dumps(posted[0])
    assert not (t01.parents[2] / config.briefs_dir).exists()


def test_run_outside_the_repository_root_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["run", str(REPO / "data" / "transcripts" / "t01_permissions.txt")]) == 2
    assert "weaknesses.yaml" in capsys.readouterr().err


def test_run_all_with_no_transcripts_is_refused(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["run-all"]) == 2
    assert "no .txt transcripts in data/transcripts" in capsys.readouterr().err


@pytest.fixture
def test_set(repo: Path) -> Path:
    """The working directory with every committed transcript and the labels, no cache."""
    shutil.copytree(
        REPO / "data" / "transcripts", repo / "data" / "transcripts", dirs_exist_ok=True
    )
    shutil.copy(REPO / "data" / "labels.yaml", repo / "data")
    return repo


def test_eval_offline_miss_writes_no_report(
    test_set: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A report scored on part of the test set would understate misses.
    assert main(["eval", "--model", "claude-sonnet-5-5", "--offline"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("t01_permissions: no recorded answer for key ")
    assert err.endswith("error: no report written for claude-sonnet-5-5\n")
    assert not (test_set / config.reports_dir).exists()


def test_eval_scores_an_unusable_answer_as_a_miss_and_logs_nothing(
    test_set: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-" + "ant-" + "x" * 40)
    stop_reasons = iter(["refusal"])

    def fake_send(request: dict[str, Any], api_key: str) -> tuple[str, str | None, Usage]:
        # The first transcript, t01, is refused; every other answer finds nothing.
        return '{"objections": []}', next(stop_reasons, "end_turn"), Usage(100, 10)

    monkeypatch.setattr(llm, "_send", fake_send)

    assert main(["eval", "--model", "claude-sonnet-5-5"]) == 0
    report = (test_set / config.reports_dir / "eval_claude-sonnet-5-5.md").read_text("utf-8")
    assert (
        "| t01_permissions | access_permissions | none | no usable answer: the model stopped "
        "with 'refusal', not 'end_turn' |"
    ) in report
    # The refused answer's tokens count: twelve answers of 100 input and 10 output tokens.
    assert "| Tokens | 1,200 | 120 |" in report
    assert "report written to reports/eval_claude-sonnet-5-5.md" in capsys.readouterr().out
    assert len(list((test_set / "data" / "llm_cache").iterdir())) == 12
    # The eval report lists every rejection; the log holds `run` and `run-all` only (brief §15).
    assert not (test_set / config.log_db).exists()


def test_eval_refuses_bad_labels_before_any_call(
    test_set: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    labels = test_set / "data" / "labels.yaml"
    labels.write_text(labels.read_text("utf-8") + "t01_permissions: []\n", "utf-8")
    assert main(["eval", "--model", "claude-haiku-5-5", "--offline"]) == 2
    assert "'t01_permissions' is listed twice" in capsys.readouterr().err


def test_eval_accepts_only_the_pinned_models(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["eval", "--model", "claude-haiku-5-5/../x"])
    assert "invalid choice" in capsys.readouterr().err


def test_eval_refuses_a_bad_transcript_before_any_call(
    test_set: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The test set must be complete, so one unreadable transcript stops the whole eval.
    (test_set / "data" / "transcripts" / "t12_no_objection.txt").write_text(
        "x" * (config.max_transcript_bytes + 1), "utf-8"
    )
    assert main(["eval", "--model", "claude-haiku-5-5", "--offline"]) == 2
    assert "larger than" in capsys.readouterr().err


def test_eval_reports_a_report_that_cannot_be_written(
    test_set: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    shutil.copytree(REPO / "data" / "llm_cache", test_set / "data" / "llm_cache")
    # A file where the reports directory should be: the write fails after scoring.
    (test_set / config.reports_dir).write_text("", "utf-8")
    assert main(["eval", "--model", "claude-haiku-5-5", "--offline"]) == 1
    assert capsys.readouterr().err.startswith("error: ")
