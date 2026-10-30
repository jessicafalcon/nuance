"""`run-all` and `eval` replay the committed cache offline: the same briefs and reports, every entry read."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from objection_brief import config, llm
from objection_brief.cli import main
from objection_brief.evaluate import load_labels
from objection_brief.weaknesses import load_themes

REPO = Path(__file__).parents[1]
LABELS = load_labels(
    REPO / config.labels_file,
    stems=[p.stem for p in (REPO / config.transcripts_dir).glob("*.txt")],
    theme_ids=[t.id for t in load_themes(REPO / config.themes_file)],
)
# The models whose detection answers are committed; Claude Sonnet 5.5's join after its
# live run, and from then every model in `config.eval_models`.
RECORDED_MODELS = ("claude-haiku-5-5",)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A copy of `data/` with the committed cache, so the runs write nothing in the repo."""
    shutil.copytree(REPO / "data", tmp_path / "data")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    return tmp_path


@pytest.fixture
def replayed(monkeypatch: pytest.MonkeyPatch) -> set[str]:
    """The names of the cache entries the runs read."""
    names: set[str] = set()
    replay = llm._replay  # pyright: ignore[reportPrivateUsage]

    def spy(path: Path) -> llm.ModelResult:
        names.add(path.name)
        return replay(path)

    monkeypatch.setattr(llm, "_replay", spy)
    return names


def _briefs(repo: Path) -> dict[str, str]:
    return {p.stem: p.read_text("utf-8") for p in sorted((repo / config.briefs_dir).iterdir())}


def test_run_all_offline_replays_the_committed_cache(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The step 6 checkpoint (brief §12): no call is made, which pytest-socket would fail.
    assert main(["run-all", "--offline"]) == 0
    assert capsys.readouterr().out.rstrip().endswith("12 transcripts, 0 failed")
    first = _briefs(repo)
    shutil.rmtree(repo / config.briefs_dir)
    assert main(["run-all", "--offline"]) == 0

    assert _briefs(repo) == first
    # Brief §13: a brief for every positive and none for the four negatives.
    assert sorted(first) == sorted(stem for stem, themes in LABELS.items() if themes)


@pytest.mark.parametrize("model", RECORDED_MODELS)
def test_eval_offline_reproduces_the_committed_report(repo: Path, model: str) -> None:
    # The step 7 checkpoint (brief §12): the report reads no clock, so a replay matches
    # the committed file byte for byte.
    report = repo / config.reports_dir / f"eval_{model}.md"
    assert main(["eval", "--model", model, "--offline"]) == 0
    first = report.read_text("utf-8")
    report.unlink()
    assert main(["eval", "--model", model, "--offline"]) == 0

    assert report.read_text("utf-8") == first
    assert first == (REPO / config.reports_dir / report.name).read_text("utf-8")


def test_committed_reports_are_exactly_the_recorded_models() -> None:
    assert sorted(p.name for p in (REPO / config.reports_dir).iterdir()) == sorted(
        f"eval_{model}.md" for model in RECORDED_MODELS
    )


def test_every_committed_entry_is_read_and_none_written(repo: Path, replayed: set[str]) -> None:
    # Brief §7.6, hygiene: an entry that neither `run-all` nor an eval reads is stale.
    cache = repo / "data" / "llm_cache"
    entries = sorted(p.name for p in cache.iterdir())

    assert main(["run-all", "--offline"]) == 0
    for model in RECORDED_MODELS:
        assert main(["eval", "--model", model, "--offline"]) == 0

    assert replayed == set(entries)
    assert sorted(p.name for p in cache.iterdir()) == entries


def test_a_theme_named_twice_appears_once_with_its_clearest_quote(repo: Path) -> None:
    # The recorded t01 answer names access_permissions at 0.95 and at 0.6 (brief §15).
    assert main(["run", "data/transcripts/t01_permissions.txt", "--offline"]) == 0
    brief = json.loads((repo / config.briefs_dir / "t01_permissions.json").read_text("utf-8"))
    texts = [b["text"]["text"] for b in brief["blocks"] if b["type"] == "section"]

    assert [t for t in texts if t.startswith("*Objection:*")] == [
        "*Objection:* Complex access permissions"
    ]
    said = next(t for t in texts if t.startswith("*Prospect said:*"))
    assert "fifty budget owners only sees their own cost center" in said
    assert "My bigger worry is what happens after." not in json.dumps(brief)
