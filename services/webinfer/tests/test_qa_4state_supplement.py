"""QA independent supplement: addressee Phase2 four-state edge cases.

Written by QA (independent of the engineer's test_decision_notforme.py) to
probe the edges the implementation spec §4.1/§4.2 and the team-lead QA brief
call out:

  * parse_model_decision — not-for-me vs response mixed in BOTH orders,
    nested / no-space markers, delegation wins in BOTH orders, empty input.
  * prompt routing — ``_resolve_base_system_prompt`` live/call/jarvis/unknown;
    ``LIVE_SYSTEM_PROMPT_EN`` only ever reaches live mode; cache-key
    isolation (same language, different interaction_mode -> different key).
  * streaming frame protocol — cross-delta not-for-me marker, content after
    a not-for-me decision is rejected, late not-for-me corrects a
    provisional response.

These are pure-function / adapter-level tests (no network, no model).
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from infer_loop import build_stream_frames  # noqa: E402
from prompt_assembly import _resolve_base_system_prompt  # noqa: E402
from prompt_constants import (  # noqa: E402
    DEFAULT_SYSTEM_PROMPT,
    DEFAULT_SYSTEM_PROMPT_EN,
    LIVE_SYSTEM_PROMPT_EN,
    NO_DECISION_SYSTEM_PROMPT,
)
from response_format import (  # noqa: E402
    normalize_model_output,
    parse_model_decision,
)

# ---------------------------------------------------------------------------
# parse_model_decision — not-for-me vs response / silence mixed orders
# ---------------------------------------------------------------------------


def test_not_for_me_first_then_response_body():
    """``</not-for-me> x </response>`` -> not-for-me, body dropped."""
    decision, clean_text, question = parse_model_decision("</not-for-me> x </response> hi")
    assert decision == "not-for-me"
    assert clean_text == ""
    assert question is None


def test_response_first_then_not_for_me_body():
    """``</response> hi </not-for-me>`` -> not-for-me (ANYWHERE wins, 宁漏不乱插)."""
    decision, clean_text, _ = parse_model_decision("</response> hi there </not-for-me>")
    assert decision == "not-for-me"
    assert clean_text == ""


def test_nested_no_space_response_inside_not_for_me():
    decision, clean_text, _ = parse_model_decision("</not-for-me></response>")
    assert decision == "not-for-me"
    assert clean_text == ""


def test_nested_no_space_not_for_me_after_response():
    decision, clean_text, _ = parse_model_decision("</response></not-for-me>")
    assert decision == "not-for-me"
    assert clean_text == ""


def test_not_for_me_beats_earlier_silence():
    """``</silence> </not-for-me>`` -> not-for-me (anywhere beats silence)."""
    decision, clean_text, _ = parse_model_decision("</silence> </not-for-me>")
    assert decision == "not-for-me"
    assert clean_text == ""


def test_not_for_me_beats_later_silence():
    decision, clean_text, _ = parse_model_decision("</not-for-me> </silence>")
    assert decision == "not-for-me"
    assert clean_text == ""


def test_opening_tag_tolerated_with_body():
    """``<not-for-me>`` (no slash) is tolerated like ``<delegation>``."""
    decision, clean_text, question = parse_model_decision("<not-for-me> 这关怎么这么难啊")
    assert decision == "not-for-me"
    assert clean_text == ""
    assert question is None


def test_delegation_wins_over_not_for_me_both_orders():
    """Delegation ANYWHERE keeps priority over not-for-me (both orders)."""
    d1, _c1, q1 = parse_model_decision("</not-for-me> </delegation> 查攻略")
    assert d1 == "delegation"
    assert q1 == "查攻略"
    # Delegation first + a stray not-for-me marker afterwards: delegation
    # still wins; the question is the raw tail (same three-state precedent:
    # a stray </silence> after </delegation> was also left in the tail).
    d2, _c2, q2 = parse_model_decision("</delegation> 查攻略 </not-for-me>")
    assert d2 == "delegation"
    assert q2 is not None and q2.startswith("查攻略")


def test_empty_input_still_silence():
    decision, clean_text, question = parse_model_decision("")
    assert decision == "silence"
    assert clean_text == ""
    assert question is None


def test_not_for_me_never_leaks_delegation_question_field():
    decision, _c, question = parse_model_decision("</not-for-me> 我去拿个快递")
    assert decision == "not-for-me"
    assert question is None


# ---------------------------------------------------------------------------
# normalize_model_output — not-for-me body never leaks
# ---------------------------------------------------------------------------


def test_normalize_mixed_response_then_not_for_me():
    assert normalize_model_output("</response> hi </not-for-me>") == "</not-for-me>"


def test_normalize_not_for_me_then_response():
    assert normalize_model_output("</not-for-me> hi </response>") == "</not-for-me>"


# ---------------------------------------------------------------------------
# prompt routing — _resolve_base_system_prompt
# ---------------------------------------------------------------------------


def test_resolve_live_uses_four_state_prompt():
    base = _resolve_base_system_prompt(
        DEFAULT_SYSTEM_PROMPT_EN, include_decision_tokens=True, interaction_mode="live"
    )
    assert base == LIVE_SYSTEM_PROMPT_EN
    assert "not-for-me" in base


def test_resolve_jarvis_uses_config_three_state_prompt():
    base = _resolve_base_system_prompt(
        DEFAULT_SYSTEM_PROMPT_EN, include_decision_tokens=True, interaction_mode="jarvis"
    )
    assert base == DEFAULT_SYSTEM_PROMPT_EN
    assert "not-for-me" not in base  # jarvis must stay three-state


def test_resolve_jarvis_chinese_uses_config_zh_prompt():
    base = _resolve_base_system_prompt(
        DEFAULT_SYSTEM_PROMPT, include_decision_tokens=True, interaction_mode="jarvis"
    )
    assert base == DEFAULT_SYSTEM_PROMPT
    assert "not-for-me" not in base


def test_resolve_unknown_mode_falls_back_to_config_not_live():
    """An unrecognized mode must NOT arm the four-state live prompt."""
    base = _resolve_base_system_prompt(
        DEFAULT_SYSTEM_PROMPT_EN, include_decision_tokens=True, interaction_mode="bogus"
    )
    assert base == DEFAULT_SYSTEM_PROMPT_EN
    assert "not-for-me" not in base


def test_resolve_call_uses_no_decision_prompt_even_for_live_flag():
    base = _resolve_base_system_prompt(
        DEFAULT_SYSTEM_PROMPT_EN, include_decision_tokens=False, interaction_mode="live"
    )
    assert base == NO_DECISION_SYSTEM_PROMPT


def test_default_system_prompts_have_no_not_for_me():
    """Hard guard: the jarvis three-state constants never mention not-for-me."""
    assert "not-for-me" not in DEFAULT_SYSTEM_PROMPT_EN
    assert "not-for-me" not in DEFAULT_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# prompt routing — cache-key isolation (anti cross-mode pollution)
# ---------------------------------------------------------------------------


class _FakeAssembly:
    """Minimal PromptAssemblyMixin-ish object exposing the cache-key fn."""

    def __init__(self, system_prompt: str = DEFAULT_SYSTEM_PROMPT_EN) -> None:
        self.config = SimpleNamespace(
            system_prompt=system_prompt,
            character_prompts_enabled=False,
            character_prompt_paths=[],
        )
        self._character_prompt_mtime = 0.0


def test_cache_key_differs_across_interaction_modes():
    """Same language + same include flag, different mode -> different key."""
    k_live = _resolve_base_system_prompt(
        DEFAULT_SYSTEM_PROMPT_EN, include_decision_tokens=True, interaction_mode="live"
    )
    k_jarvis = _resolve_base_system_prompt(
        DEFAULT_SYSTEM_PROMPT_EN, include_decision_tokens=True, interaction_mode="jarvis"
    )
    # The resolved base differs -> the composed prompt cache keys differ, so
    # the live four-state prompt can never be served to the jarvis cache.
    assert k_live != k_jarvis


def test_assembly_system_prompt_cache_key_isolation():
    """End-to-end key: live vs jarvis produce distinct cache keys."""
    from prompt_assembly import PromptAssemblyMixin

    adapter = PromptAssemblyMixin()
    # Mixin attributes the real class expects are supplied by subclasses; we
    # only exercise the key builder through a minimal stand-in.
    adapter.config = _FakeAssembly().config
    adapter._character_prompt_mtime = 0.0

    k_live = adapter._system_prompt_cache_key("en", interaction_mode="live")
    k_jarvis = adapter._system_prompt_cache_key("en", interaction_mode="jarvis")
    k_call = adapter._system_prompt_cache_key(
        "en", include_decision_tokens=False, interaction_mode="call"
    )
    assert k_live != k_jarvis
    assert k_live != k_call
    assert k_jarvis != k_call
    # Same mode+language -> stable key (cache hit path).
    assert adapter._system_prompt_cache_key("en", interaction_mode="live") == k_live


# ---------------------------------------------------------------------------
# build_stream_frames — cross-delta markers / content rejection
# ---------------------------------------------------------------------------


def test_frames_marker_split_at_different_point():
    """``</not-for`` + ``-me>`` (split after 'for') is still recognized."""
    frames = build_stream_frames(["</not-for", "-me>", " tail"])
    assert [f["type"] for f in frames] == ["decision"]
    assert frames[0]["decision"] == "not-for-me"
    assert frames[0]["delegation_question"] is None


def test_frames_marker_split_at_word_start():
    """``</not`` + ``-for-me>`` (split before the hyphen) is still recognized."""
    frames = build_stream_frames(["</not", "-for-me>", " tail"])
    assert [f["type"] for f in frames] == ["decision"]
    assert frames[0]["decision"] == "not-for-me"
    assert frames[0]["delegation_question"] is None


def test_frames_content_after_not_for_me_rejected():
    frames = build_stream_frames(["</not-for-me>", "hello world", "more text"])
    assert [f["type"] for f in frames] == ["decision"]
    assert frames[0]["decision"] == "not-for-me"


def test_frames_late_not_for_me_corrects_response_with_body():
    """``</response> Let me check </not-for-me>`` -> single corrected frame."""
    frames = build_stream_frames(["</response>", "Let me check", "please wait", "</not-for-me>"])
    assert [f["type"] for f in frames] == ["decision"]
    assert frames[0]["decision"] == "not-for-me"
    assert all(f["type"] != "content" for f in frames)


def test_frames_consistent_with_non_streaming_parser_multiple_markers():
    """Streaming result must agree with the unified parser on the same raw text."""
    raw = "</response> hi </not-for-me>"
    decision, clean, question = parse_model_decision(raw)
    frames = build_stream_frames([raw])
    assert frames[0]["decision"] == decision == "not-for-me"
    assert frames[0]["delegation_question"] == question
    assert clean == ""
