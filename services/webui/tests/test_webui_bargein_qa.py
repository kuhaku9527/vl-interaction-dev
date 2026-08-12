"""QA supplementary static assertions for the P0 barge-in (front-end first-stop TTS).

Independent, reviewer-perspective checks on top of the engineer's own
test_webui_static_contract.py. They pin the *invariants* that make the
epoch race-guard sound and confirm the change is scoped to btTtsPlayer only:

  * epoch is single-writer (initialised to 0, bumped ONLY in stopLlmReplyAudio);
  * playEpoch is captured AFTER the stop call in playLlmReplyAudio, so the
    captured epoch is this play's own generation;
  * both race checks exist with the correct comparison direction (!==);
  * stopLlmReplyAudio is fully idempotent / tear-down complete;
  * the wake/end-tone player (btListenPlayer) is never touched by the new
    stop path and never gets a stopLlmReplyAudio-style call;
  * pre-existing features (dedupe, ASR draft, llm_reply subtitle render) are
    preserved on the same code paths.
"""

from __future__ import annotations

import re
from pathlib import Path

WEBUI_ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = WEBUI_ROOT / "src" / "joy_interaction_webui" / "static" / "index.html"


def _index_html() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


def _function_body(html: str, name: str) -> str:
    match = re.search(
        rf"(?:async\s+)?function {name}\([^)]*\) \{{(?P<body>.*?)\n        \}}", html, re.S
    )
    assert match, f"missing function {name}"
    return match.group("body")


def _stop_positions(html: str) -> list[int]:
    """Absolute character offsets of every stopLlmReplyAudio() call site."""
    return [m.start() for m in re.finditer(r"stopLlmReplyAudio\(\)", html)]


# ---------------------------------------------------------------------------
# 1. Epoch lifecycle: single writer, correct init, correct capture ordering
# ---------------------------------------------------------------------------


def test_epoch_is_initialised_to_zero():
    html = _index_html()
    assert "let llmReplyEpoch = 0;" in html


def test_epoch_has_single_writer_stopLlmReplyAudio():
    """The epoch must be bumped ONLY by the stop helper; any other write
    would break the monotonic generation counter used by the race guard.
    """
    html = _index_html()
    writes = re.findall(r"llmReplyEpoch\s*(\+=|-=|=)\s*([^;]*)", html)
    # Expected writes: `let llmReplyEpoch = 0;` (init) and `llmReplyEpoch += 1;` (stop)
    writes = [op + val for op, val in writes]
    assert writes == ["=0", "+=1"], f"unexpected epoch writes: {writes}"


def test_epoch_bump_happens_before_null_element_guard():
    """Even when btTtsPlayer is missing the epoch must still advance, so an
    in-flight synthesis is invalidated regardless of DOM state.
    """
    body = _function_body(_index_html(), "stopLlmReplyAudio")
    bump = body.index("llmReplyEpoch += 1")
    guard = body.index("if (!btTtsPlayer) return")
    assert bump < guard


def test_play_epoch_captured_after_stop_call():
    """playLlmReplyAudio must capture its own generation AFTER the P0.1 stop,
    otherwise a barge-in landing before capture would go unnoticed.
    """
    body = _function_body(_index_html(), "playLlmReplyAudio")
    stop = body.index("stopLlmReplyAudio()")
    capture = body.index("const playEpoch = llmReplyEpoch")
    assert stop < capture


def test_both_epoch_race_checks_with_correct_direction():
    body = _function_body(_index_html(), "playLlmReplyAudio")
    assert body.count("playEpoch !== llmReplyEpoch") >= 2
    assert "playEpoch === llmReplyEpoch" not in body.replace("playEpoch !== llmReplyEpoch", "")


# ---------------------------------------------------------------------------
# 2. stopLlmReplyAudio tear-down completeness / idempotency
# ---------------------------------------------------------------------------


def test_stop_helper_tears_down_audio_completely():
    body = _function_body(_index_html(), "stopLlmReplyAudio")
    for needle in (
        "btTtsPlayer.pause()",
        "btTtsPlayer.currentTime = 0",
        "btTtsPlayer.onended = null",
        "URL.revokeObjectURL(llmReplyAudioUrl)",
        "llmReplyAudioUrl = null",
        "btTtsPlayer.removeAttribute('src')",
        "btTtsPlayer.load()",
    ):
        assert needle in body, f"stop helper missing teardown: {needle}"


# ---------------------------------------------------------------------------
# 3. Scope isolation: only btTtsPlayer, never btListenPlayer
# ---------------------------------------------------------------------------


def test_stop_helper_never_touches_bt_listen_player():
    body = _function_body(_index_html(), "stopLlmReplyAudio")
    assert "btListenPlayer" not in body


def test_bt_listen_player_has_no_stop_audio_call():
    """The wake/end-tone player must keep its own lifecycle and never be fed
    through stopLlmReplyAudio-style teardown from the new barge-in code.
    """
    html = _index_html()
    # Only the element lookup + existing WebRTC stream handling may mention it.
    assert "btListenPlayer.pause()" not in html.replace("btListenPlayer.pause();\n                btListenPlayer.srcObject = null", "")
    stop_body = _function_body(html, "stopLlmReplyAudio")
    assert "getElementById('btListenPlayer')" not in stop_body


def test_listen_player_references_are_pre_existing_web_rtc_only():
    html = _index_html()
    # btListenPlayer should only appear in: element lookup, stream attach/play,
    # and the pre-existing disconnect handler (pause + srcObject=null).
    uses = re.findall(r"btListenPlayer\.\w+", html)
    assert set(uses) <= {
        "btListenPlayer.srcObject",
        "btListenPlayer.muted",
        "btListenPlayer.play",
        "btListenPlayer.pause",
    }, f"unexpected btListenPlayer method uses: {set(uses)}"


# ---------------------------------------------------------------------------
# 4. Call-site wiring (three barge-in signals)
# ---------------------------------------------------------------------------


def test_three_barge_in_signals_each_call_stop_once():
    html = _index_html()
    llm_body = _function_body(html, "installLlmReplyHandler")
    asr_body = _function_body(html, "handleAsrResult")
    play_body = _function_body(html, "playLlmReplyAudio")

    # asr_partial branch stops audio BEFORE rendering the draft
    partial_idx = llm_body.index("data.type === 'asr_partial'")
    stop_after_partial = llm_body.index("stopLlmReplyAudio()", partial_idx)
    render_idx = llm_body.index("renderAsrDraft(data.text)", partial_idx)
    assert stop_after_partial < render_idx

    # pilot_utterance branch stops audio BEFORE committing the message
    pilot_idx = llm_body.index("data.type === 'pilot_utterance'")
    stop_after_pilot = llm_body.index("stopLlmReplyAudio()", pilot_idx)
    commit_idx = llm_body.index("appendPilotToResult(data.text || '')", pilot_idx)
    assert stop_after_pilot < commit_idx

    # browser ASR IS_PARTIAL branch stops audio
    assert "data.event === 'IS_PARTIAL'" in asr_body
    assert asr_body.index("stopLlmReplyAudio()") < asr_body.index("asrPartialText = transcriptText")

    # P0.1: play path stops the previous reply before synthesising
    assert "stopLlmReplyAudio()" in play_body


# ---------------------------------------------------------------------------
# 5. Pre-existing features preserved
# ---------------------------------------------------------------------------


def test_dedupe_logic_preserved():
    body = _function_body(_index_html(), "playLlmReplyAudio")
    assert "lastLlmReplyKey === dedupKey" in body
    assert "< 8000" in body


def test_dedupe_return_happens_before_stop():
    """A deduped duplicate reply must NOT stop/restart the audio that is
    already playing (otherwise retries would glitch the current reply).
    """
    body = _function_body(_index_html(), "playLlmReplyAudio")
    dedupe_return = body.index("return;", body.index("lastLlmReplyKey === dedupKey"))
    stop = body.index("stopLlmReplyAudio()")
    assert dedupe_return < stop


def test_subtitle_and_draft_render_paths_preserved():
    html = _index_html()
    llm_body = _function_body(html, "installLlmReplyHandler")
    assert "appendJarvisToResult(data.text || '', data.source || 'jarvis')" in llm_body
    assert "playLlmReplyAudio(data.text || '', { source: data.source || 'jarvis' })" in llm_body
    assert "renderAsrDraft(data.text)" in llm_body
    assert "clearAsrDraft()" in llm_body


def test_onended_still_revokes_object_url():
    body = _function_body(_index_html(), "playLlmReplyAudio")
    assert "URL.revokeObjectURL(url)" in body
    assert "if (llmReplyAudioUrl === url) llmReplyAudioUrl = null" in body


# ---------------------------------------------------------------------------
# 6. Declaration order guard (readability / future-regression)
# ---------------------------------------------------------------------------


def test_stop_helper_declared_before_play_helper():
    html = _index_html()
    assert html.index("function stopLlmReplyAudio()") < html.index("function playLlmReplyAudio")
