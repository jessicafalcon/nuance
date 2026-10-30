"""The brief shows one grounded, escaped item per theme with the five elements and the footer."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest

from objection_brief import config
from objection_brief.brief import (
    FOOTER,
    BriefItem,
    FollowUp,
    build_message,
    draft_follow_up,
    follow_up_prompt,
    grounding_problem,
    items,
    preview,
    select,
)
from objection_brief.detect import Detection, RejectReason, Speaker
from objection_brief.guardrail import Verdict
from objection_brief.llm import CacheMissError, ModelResult, Usage
from objection_brief.weaknesses import load_themes

THEMES = {t.id: t for t in load_themes(Path(__file__).parents[1] / "data" / "weaknesses.yaml")}
PERMISSIONS = THEMES["access_permissions"]
QUOTE = "setting up permissions for fifty budget owners sounds like a project in itself"
DRAFT = (
    "I understand that scoping access for every budget owner looks like a project. Granular "
    "permissions take deliberate setup, and our role-based templates and guided permissions "
    "session in onboarding cover most of it. [PLACEHOLDER] customer story: mid-market company "
    "scoped cost-center access in onboarding. Could we book a 30-minute permissions design "
    "session with a solutions consultant?"
)


def _detection(theme_id: str = "access_permissions", confidence: float = 0.9) -> Detection:
    return Detection(theme_id, QUOTE, Speaker.PROSPECT, confidence)


def _item(quote: str = QUOTE, follow_up: FollowUp | None = None) -> BriefItem:
    return BriefItem(
        PERMISSIONS,
        dataclasses.replace(_detection(), quote=quote),
        follow_up or FollowUp(DRAFT, None, Usage(300, 80)),
    )


class FakeCall:
    """Stands in for `call_model` with its cache bound; keeps the keywords it received."""

    def __init__(self, output: str = DRAFT, stop_reason: str | None = "end_turn") -> None:
        self.answer = ModelResult(
            output, stop_reason, Usage(300, 80), "2026-10-30T12:00:00+00:00", True
        )
        self.kwargs: dict[str, Any] = {}

    def __call__(self, **kwargs: Any) -> ModelResult:
        self.kwargs = kwargs
        return self.answer


# select


def test_a_theme_named_twice_keeps_its_most_confident_detection() -> None:
    # t01's recorded answer names access_permissions twice, at 0.95 and 0.6.
    lead_in = _detection(confidence=0.6)
    clearest = _detection(confidence=0.95)
    other = _detection("pricing_escalation", 0.8)
    verdicts = [Verdict(lead_in, None), Verdict(other, None), Verdict(clearest, None)]
    assert select(verdicts) == (clearest, other)


def test_a_tie_keeps_the_first_and_rejected_detections_are_never_selected() -> None:
    first, second = (
        _detection(confidence=0.8),
        dataclasses.replace(_detection(confidence=0.8), quote="x"),
    )
    rejected = _detection(confidence=0.99)
    verdicts = [
        Verdict(first, None),
        Verdict(rejected, RejectReason.QUOTE_NOT_FOUND),
        Verdict(second, None),
    ]
    assert select(verdicts) == (first,)
    assert select([Verdict(rejected, RejectReason.LOW_CONFIDENCE)]) == ()


# draft_follow_up


def test_follow_up_prompt_holds_only_the_theme_answer_proof_and_next_step() -> None:
    prompt = follow_up_prompt(PERMISSIONS)
    for value in (PERMISSIONS.draft_answer, PERMISSIONS.proof, PERMISSIONS.follow_up_angle):
        assert value in prompt
    # Nothing else from the theme file reaches the prompt: not another theme, nor this
    # theme's detection hints.
    for theme in THEMES.values():
        if theme.id != PERMISSIONS.id:
            assert theme.draft_answer not in prompt
    assert PERMISSIONS.detect_when not in prompt
    assert not any(signal in prompt for signal in PERMISSIONS.example_signals)


def test_quote_goes_only_in_the_user_turn() -> None:
    call = FakeCall()
    follow_up = draft_follow_up(PERMISSIONS, QUOTE, call=call, model=config.follow_up_model)

    assert follow_up == FollowUp(DRAFT, None, Usage(300, 80))
    assert QUOTE not in call.kwargs["system"]
    assert call.kwargs["messages"] == [{"role": "user", "content": f"<quote>\n{QUOTE}\n</quote>"}]
    assert call.kwargs["prompt_version"] == config.follow_up_prompt_version
    assert call.kwargs["output_config"] == {"effort": config.follow_up_effort}
    assert call.kwargs["model"] == config.follow_up_model


@pytest.mark.parametrize(
    ("output", "stop_reason", "problem"),
    [
        (DRAFT, "max_tokens", "stopped with 'max_tokens'"),
        (DRAFT, "refusal", "stopped with 'refusal'"),
        ("   ", "end_turn", "the draft is empty"),
        (DRAFT.replace("[PLACEHOLDER] ", ""), "end_turn", "doesn't copy the proof word for word"),
        (
            DRAFT.replace(
                "[PLACEHOLDER] customer story: mid-market company scoped cost-center access",
                "A mid-market company scoped cost-center access: [PLACEHOLDER] customer story",
            ),
            "end_turn",
            "doesn't copy the proof word for word",
        ),
        (DRAFT + " Teams cut setup time by 40%.", "end_turn", "states '40'"),
    ],
)
def test_an_ungrounded_or_unusable_draft_is_withheld_with_its_reason(
    output: str, stop_reason: str, problem: str
) -> None:
    follow_up = draft_follow_up(
        PERMISSIONS, QUOTE, call=FakeCall(output, stop_reason), model=config.follow_up_model
    )
    assert follow_up.text == output.strip()
    assert follow_up.problem is not None
    assert problem in follow_up.problem


def test_figures_from_the_quote_or_the_approved_points_are_allowed() -> None:
    # "30" is in follow_up_angle; "41" is in the quote.
    text = DRAFT + " That holds for all 41 stores."
    assert grounding_problem(text, PERMISSIONS, "we have 41 stores") is None


def test_a_missing_recording_is_an_error_not_a_withheld_draft() -> None:
    # Offline, a miss must fail the transcript; a brief saying "no draft" would let a
    # missing cache entry pass CI.
    def miss(**kwargs: Any) -> ModelResult:
        raise CacheMissError("no recorded answer")

    with pytest.raises(CacheMissError):
        draft_follow_up(PERMISSIONS, QUOTE, call=miss, model=config.follow_up_model)


# build_message


def _texts(message: dict[str, Any]) -> list[str]:
    texts: list[str] = []
    for block in message["blocks"]:
        if "text" in block:
            texts.append(block["text"]["text"])
        texts.extend(element["text"] for element in block.get("elements", []))
    return texts


def test_message_has_the_five_elements_per_theme_and_the_draft_footer() -> None:
    message = build_message("t01_permissions", [_item()])
    texts = _texts(message)

    assert texts[0] == "Objection brief: t01_permissions"
    assert f"*Objection:* {PERMISSIONS.label}" in texts
    assert f"*Prospect said:*\n>{QUOTE}" in texts
    assert f"*Approved answer (draft):*\n{PERMISSIONS.draft_answer}" in texts
    assert f"*Proof:*\n{PERMISSIONS.proof}" in texts
    assert f"*Follow-up draft, edit before sending:*\n{DRAFT}" in texts
    assert message["blocks"][-1] == {
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": FOOTER, "verbatim": True}],
    }
    assert FOOTER == "Draft answer \N{EM DASH} review before using."
    assert message["unfurl_links"] is False
    assert message["unfurl_media"] is False


def test_a_withheld_draft_shows_its_reason_and_never_its_text() -> None:
    invented = "Teams cut setup time by 40%."
    withheld = FollowUp(
        invented, "the draft states '40', a figure not in the approved points", Usage(1, 1)
    )
    message = build_message("t01", [_item(follow_up=withheld)])
    assert any("_No draft: the draft states '40'" in t for t in _texts(message))
    assert invented not in json.dumps(message)


def test_items_pairs_each_selected_detection_with_its_theme_and_follow_up() -> None:
    call = FakeCall()
    pricing = _detection("pricing_escalation", 0.8)
    verdicts = [
        Verdict(_detection(confidence=0.6), None),
        Verdict(_detection(), None),
        Verdict(pricing, None),
    ]
    entries = items(verdicts, THEMES.values(), call=call, model=config.follow_up_model)

    assert [(e.theme.id, e.detection.confidence) for e in entries] == [
        ("access_permissions", 0.9),
        ("pricing_escalation", 0.8),
    ]
    # One follow-up per theme, not per detection; the last call was for pricing.
    assert THEMES["pricing_escalation"].draft_answer in call.kwargs["system"]


def test_preview_reads_the_message_and_drops_control_characters() -> None:
    text = preview(build_message("t01", [_item(quote="cost & <scale>\x1b[2J")]))
    assert ">cost & <scale>[2J" in text
    assert "\x1b" not in text
    assert text.endswith(FOOTER)


def test_slack_control_sequences_in_a_quote_are_escaped() -> None:
    quote = "<!channel> <@U123> & <https://evil.example|click here>"
    message = build_message("t01", [_item(quote=quote)])
    dumped = json.dumps(message)

    for raw in ("<!channel>", "<@U123>", "<https://"):
        assert raw not in dumped
    assert "&lt;!channel&gt; &lt;@U123&gt; &amp; &lt;https://evil.example|click here&gt;" in dumped


def test_long_text_is_truncated_within_slack_limits_and_the_footer_survives() -> None:
    message = build_message("t01", [_item(quote="worry " * 1000 + "& more")])
    texts = _texts(message)

    assert all(len(t) <= 3000 for t in texts)
    assert any(t.endswith("\N{HORIZONTAL ELLIPSIS}") for t in texts)
    assert texts[-1] == FOOTER


def test_a_cut_never_leaves_half_an_escape() -> None:
    # Placed so the cut lands inside "&amp;".
    quote = "x" * (3000 - len("*Prospect said:*\n>") - 3) + "&&&&"
    texts = _texts(build_message("t01", [_item(quote=quote)]))
    said = next(t for t in texts if t.startswith("*Prospect said:*"))
    assert said.removesuffix("\N{HORIZONTAL ELLIPSIS}").endswith(("x", "&amp;"))


def test_the_model_draft_is_escaped_like_the_quote() -> None:
    draft = FollowUp("Ping <!channel> and see <https://evil.example|our page>.", None, Usage(1, 1))
    dumped = json.dumps(build_message("t01", [_item(follow_up=draft)]))
    assert "<!channel>" not in dumped
    assert "<https://" not in dumped
    assert "&lt;!channel&gt;" in dumped


def test_every_mrkdwn_text_is_verbatim_so_slack_parses_no_bare_mention() -> None:
    message = build_message("t01", [_item(quote="@here look at this")])
    mrkdwn = [b["text"] for b in message["blocks"] if b["type"] == "section"]
    mrkdwn += [e for b in message["blocks"] if b["type"] == "context" for e in b["elements"]]
    assert mrkdwn
    assert all(text["verbatim"] is True for text in mrkdwn)
