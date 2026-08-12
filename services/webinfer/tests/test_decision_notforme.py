# ruff: noqa: RUF001
"""Four-state decision-token tests (addressee-detection Phase 2).

Locks in the ``</not-for-me>`` extension (spec
``doc/specs/draft-addressee-detection.md`` §4.1/§4.2):

  * :func:`parse_model_decision` — four states; ``</not-for-me>`` is an
    independent single-marker state (empty body, no delegation question);
    the opening ``<not-for-me>`` form is tolerated; delegation priority is
    unchanged; old three-state outputs parse exactly as before.
  * :func:`normalize_model_output` / :func:`strip_decision_tokens` — the
    not-for-me marker is normalized/stripped and never reaches ``content``.
  * Streaming frame protocol (``build_stream_frames`` /
    ``_find_first_decision_marker``) — a not-for-me decision frame carries
    no content frames; a late ``</not-for-me>`` after a provisional response
    re-judges the whole turn (mirrors the late-delegation correction).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from infer_loop import _find_first_decision_marker, build_stream_frames  # noqa: E402
from response_format import (  # noqa: E402
    normalize_model_output,
    parse_model_decision,
    strip_decision_tokens,
)


def _types(frames: list[dict]) -> list[str]:
    return [f["type"] for f in frames]


# ---------------------------------------------------------------------------
# parse_model_decision — not-for-me
# ---------------------------------------------------------------------------


def test_not_for_me_closing_tag():
    decision, clean_text, question = parse_model_decision("</not-for-me>")
    assert decision == "not-for-me"
    assert clean_text == ""
    assert question is None


def test_not_for_me_opening_tag_tolerated():
    decision, clean_text, question = parse_model_decision("<not-for-me>")
    assert decision == "not-for-me"
    assert clean_text == ""
    assert question is None


def test_not_for_me_with_trailing_body_is_empty():
    """A not-for-me marker must never carry body text into clean_text."""
    decision, clean_text, _ = parse_model_decision("</not-for-me> 这关怎么这么难啊")
    assert decision == "not-for-me"
    assert clean_text == ""


def test_not_for_me_earlier_than_response_wins():
    decision, clean_text, _ = parse_model_decision("</not-for-me> </response> hi")
    assert decision == "not-for-me"
    assert clean_text == ""


def test_not_for_me_anywhere_wins_over_earlier_response():
    """A not-for-me tag ANYWHERE beats an earlier response (宁漏不乱插)."""
    decision, clean_text, _ = parse_model_decision("</response> hi </not-for-me>")
    assert decision == "not-for-me"
    assert clean_text == ""


def test_not_for_me_does_not_conflict_with_delegation_priority():
    """Delegation tag ANYWHERE still wins over not-for-me (priority unchanged)."""
    decision, _clean, question = parse_model_decision("</not-for-me> </delegation> 查攻略")
    assert decision == "delegation"
    assert question == "查攻略"


def test_not_for_me_never_reaches_delegation_question():
    decision, _clean, question = parse_model_decision("</not-for-me> 我去拿个快递")
    assert decision == "not-for-me"
    assert question is None


def test_parser_case_sensitive_like_three_state():
    """parse_model_decision stays case-sensitive (parity with response/silence)."""
    decision, clean_text, _ = parse_model_decision("</NOT-FOR-ME>")
    assert decision == "response"
    assert clean_text == "</NOT-FOR-ME>"


# ---------------------------------------------------------------------------
# parse_model_decision — old three-state regression (unchanged)
# ---------------------------------------------------------------------------


def test_regression_delegation_with_response_prefix_token():
    raw = "</response> 这个问题我不太确定，转交后台求解器。 </delegation> 查 RTX 5060 Ti 价格"
    decision, _clean_text, question = parse_model_decision(raw)
    assert decision == "delegation"
    assert question == "查 RTX 5060 Ti 价格"


def test_regression_bare_delegation_tag():
    decision, _, question = parse_model_decision("</delegation> 查 RTX 5060 Ti 价格")
    assert decision == "delegation"
    assert question == "查 RTX 5060 Ti 价格"


def test_regression_response_token_still_response():
    decision, clean_text, _ = parse_model_decision("</response> hello there")
    assert decision == "response"
    assert clean_text == "hello there"


def test_regression_silence_token_still_silence():
    decision, clean_text, _ = parse_model_decision("</silence>")
    assert decision == "silence"
    assert clean_text == ""


def test_regression_empty_is_silence():
    decision, _, _ = parse_model_decision("")
    assert decision == "silence"


def test_regression_no_token_is_response():
    decision, clean_text, _ = parse_model_decision("just talking to the user")
    assert decision == "response"
    assert clean_text == "just talking to the user"


# ---------------------------------------------------------------------------
# normalize_model_output / strip_decision_tokens
# ---------------------------------------------------------------------------


def test_normalize_not_for_me_alone():
    assert normalize_model_output("</not-for-me>") == "</not-for-me>"


def test_normalize_not_for_me_drops_body():
    """Normalized not-for-me never keeps a body (mirrors silence)."""
    assert normalize_model_output("</not-for-me> 算了算了") == "</not-for-me>"


def test_normalize_not_for_me_earliest_of_markers():
    assert normalize_model_output("</not-for-me> </response> hi") == "</not-for-me>"


def test_strip_not_for_me_token_from_content():
    assert strip_decision_tokens("</not-for-me>") == ""
    assert strip_decision_tokens("<not-for-me> 唉，好累") == "唉，好累"
    assert strip_decision_tokens("</NOT-FOR-ME>") == ""


def test_strip_keeps_existing_three_state_regression():
    assert strip_decision_tokens("</response> hello there") == "hello there"
    assert strip_decision_tokens("</silence>") == ""
    assert strip_decision_tokens("</delegation> 查 RTX") == "查 RTX"


# ---------------------------------------------------------------------------
# Streaming frame protocol — not-for-me
# ---------------------------------------------------------------------------


def test_find_first_decision_marker_not_for_me():
    assert _find_first_decision_marker("</not-for-me>") == 0
    assert _find_first_decision_marker("<not-for-me>") == 0
    assert _find_first_decision_marker("x </not-for-me>") == 2
    assert _find_first_decision_marker("</not-fo") is None  # partial marker


def test_frames_not_for_me_has_no_content():
    frames = build_stream_frames(["</not-for-me>"])
    assert _types(frames) == ["decision"]
    assert frames[0]["decision"] == "not-for-me"
    assert frames[0]["delegation_question"] is None


def test_frames_not_for_me_trailing_deltas_never_stream():
    frames = build_stream_frames(["</not-for-me>", "   ", "ignored tail"])
    assert _types(frames) == ["decision"]
    assert frames[0]["decision"] == "not-for-me"


def test_frames_not_for_me_split_across_deltas():
    frames = build_stream_frames(["</not-f", "or-me>", " tail"])
    assert _types(frames) == ["decision"]
    assert frames[0]["decision"] == "not-for-me"


def test_frames_late_not_for_me_corrects_provisional_response():
    """``</response> note </not-for-me>`` -> single corrected not-for-me frame."""
    frames = build_stream_frames(["</response>", "Let me check", "</not-for-me>"])
    assert _types(frames) == ["decision"]
    assert frames[0]["decision"] == "not-for-me"
    assert all(f["type"] != "content" for f in frames)


def test_frames_not_for_me_matches_non_streaming_parser():
    raw = "这关怎么这么难啊\n</not-for-me>"
    frames = build_stream_frames([raw])
    decision, clean, question = parse_model_decision(raw)
    assert frames[0]["decision"] == decision == "not-for-me"
    assert frames[0]["delegation_question"] == question
    assert clean == ""
