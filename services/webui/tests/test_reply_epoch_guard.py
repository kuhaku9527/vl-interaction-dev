"""P1 reply_epoch guard tests: suppress late/stale llm_reply broadcasts.

The P1 Known Issue is that after the user starts speaking (barge-in), a
llm_reply from the *previous* turn that arrives late is still played by the
front-end. The fix:

  * backend bumps ``_llm_reply_epoch`` once per user turn (in
    ``_send_to_llm``) and on every barge-in / exit word (``_pause_tts`` /
    ``_stop_tts``);
  * each turn's llm_reply broadcast carries the epoch captured at its turn
    start (``_current_turn_reply_epoch``), so a barge-in that bumps the
    counter expires any in-flight / not-yet-broadcast reply from an older
    turn;
  * asr_partial / pilot_utterance payloads carry the live epoch so the
    front-end's accepted generation (``llmReplyGeneration``) mirrors the
    backend without drift;
  * the front-end drops an llm_reply whose reply_epoch is older than its
    accepted generation (static contract asserted here).

Covers:
  * turn-start bump + broadcast tagging (streaming & non-streaming & call);
  * pause/stop bump (barge-in / exit word) with structured logging;
  * late-reply suppression semantics via the session broadcast path;
  * AsrPartial carries the live epoch;
  * front-end static contract: llm_reply branch has the epoch guard and the
    asr_partial / pilot_utterance branches adopt the backend epoch.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

REPO = Path(__file__).resolve().parents[3]
WEBUI_SRC = REPO / "services" / "webui" / "src"
for _p in (str(REPO), str(WEBUI_SRC)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from joy_interaction_webui.jarvis_mode import (  # noqa: E402
    AsrPartial,
    JarvisConfig,
    JarvisStateMachine,
)
from joy_interaction_webui.jarvis_session import JarvisSessionManager  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_sm(**cfg_overrides):
    cfg = JarvisConfig(
        wake_word="bt",
        kws_model_dir="ignored",
        asr_model_dir="ignored",
        llm_api_url="http://stub/v1",
        llm_text_path="/text/chat",
        llm_multimodal_path="/chat/completions",
        llm_model="stub",
        llm_system_prompt="be brief",
        tts_api_url="http://tts-stub/v1/synthesize",
        tts_voice_id="stub-voice",
        **cfg_overrides,
    )
    return JarvisStateMachine(config=cfg)


def _frame(**kwargs):
    return json.dumps(kwargs, ensure_ascii=False)


class _FakeStreamResponse:
    def __init__(self, lines, status=200):
        self._lines = list(lines)
        self.status_code = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def aread(self):
        return b"stream error"

    def aiter_lines(self):
        async def gen():
            for line in self._lines:
                yield line

        return gen()


class _FakeJsonResponse:
    def __init__(self, content="", decision="response", delegation_question=None):
        self._content = content
        self._decision = decision
        self._delegation_question = delegation_question

    def raise_for_status(self):
        pass

    def json(self):
        return {
            "choices": [{"message": {"content": self._content}}],
            "streamingharness": {
                "decision": self._decision,
                "delegation_question": self._delegation_question,
            },
        }


class _FakeClient:
    def __init__(self, stream_response, post_response=None):
        self._stream_response = stream_response
        self._post_response = post_response
        self.stream_calls = []
        self.post_calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def stream(self, method, url, **kwargs):
        self.stream_calls.append((method, url, kwargs))
        return self._stream_response

    async def post(self, url, json=None):
        self.post_calls.append((url, json))
        return self._post_response


# ---------------------------------------------------------------------------
# Backend: turn-start bump + broadcast tagging
# ---------------------------------------------------------------------------


def test_reply_epoch_initialised_and_lazy_ensured():
    """The epoch fields exist after __init__ AND after __new__ (test pattern)."""
    sm = _build_sm()
    assert sm._llm_reply_epoch == 0
    assert sm._current_turn_reply_epoch == 0

    raw = JarvisStateMachine.__new__(JarvisStateMachine)
    assert not hasattr(raw, "_llm_reply_epoch")
    raw._ensure_tts_stream_state()
    assert raw._llm_reply_epoch == 0
    assert raw._current_turn_reply_epoch == 0


def test_send_to_llm_bumps_epoch_once_per_turn_non_streaming():
    """Every _send_to_llm dispatch bumps the reply epoch exactly once."""
    sm = _build_sm(llm_streaming_enabled=False)
    client = _FakeClient(
        stream_response=_FakeStreamResponse([]),
        post_response=_FakeJsonResponse(content="ok", decision="response"),
    )
    broadcasts: list = []
    sm.on_llm_response = lambda text, source: broadcasts.append(text)

    async def _run_turn(text: str):
        with patch("httpx.AsyncClient", return_value=client):
            await sm._send_to_llm(text, stream_tts=False, interaction_mode="jarvis")

    asyncio.run(_run_turn("first"))
    first_epoch = sm._llm_reply_epoch
    assert first_epoch == 1
    assert sm._current_turn_reply_epoch == 1
    assert broadcasts == ["ok"]

    asyncio.run(_run_turn("second"))
    assert sm._llm_reply_epoch == 2
    assert sm._current_turn_reply_epoch == 2


@pytest.mark.asyncio
async def test_streaming_broadcast_carries_turn_epoch():
    """The streaming path broadcasts llm_reply tagged with the turn epoch."""
    sm = _build_sm()
    broadcasts: list = []
    sm.on_llm_response = lambda text, source: broadcasts.append(text)
    with patch.object(sm, "_fetch_tts_pcm", new=AsyncMock(return_value=b"\x00\x00" * 100)):
        lines = [
            _frame(type="decision", decision="response", delegation_question=None),
            _frame(type="content", token="Hello."),
            _frame(type="done", decision="response", full_text="Hello.", delegation_question=None),
        ]
        client = _FakeClient(stream_response=_FakeStreamResponse(lines))
        with patch("httpx.AsyncClient", return_value=client):
            await sm._send_to_llm("hello", stream_tts=False, interaction_mode="jarvis")
    assert sm._llm_reply_epoch == 1
    assert sm._current_turn_reply_epoch == 1
    assert broadcasts == ["Hello."]


@pytest.mark.asyncio
async def test_fail_open_retry_preserves_same_turn_epoch():
    """Pre-frame failure retries non-streaming with the SAME turn epoch (no double bump)."""
    sm = _build_sm()
    with patch.object(sm, "_fetch_tts_pcm", new=AsyncMock(return_value=b"\x00\x00" * 100)):
        stream_resp = _FakeStreamResponse([], status=500)
        post_resp = _FakeJsonResponse(content="fallback ok", decision="response")
        client = _FakeClient(stream_response=stream_resp, post_response=post_resp)
        with patch("httpx.AsyncClient", return_value=client):
            await sm._send_to_llm("hello", stream_tts=False, interaction_mode="jarvis")
    # One bump for the turn; the fail-open retry reuses it.
    assert sm._llm_reply_epoch == 1
    assert sm._current_turn_reply_epoch == 1


@pytest.mark.asyncio
async def test_call_mode_also_bumps_epoch():
    """interaction_mode='call' (paper-plane) bumps the epoch like any turn."""
    sm = _build_sm(llm_streaming_enabled=True)
    post_resp = _FakeJsonResponse(content="call reply", decision="response")
    client = _FakeClient(stream_response=_FakeStreamResponse([]), post_response=post_resp)
    with patch("httpx.AsyncClient", return_value=client):
        await sm._send_to_llm("hi", stream_tts=False, interaction_mode="call")
    assert sm._llm_reply_epoch == 1
    assert sm._current_turn_reply_epoch == 1


# ---------------------------------------------------------------------------
# Backend: barge-in / exit-word bump
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pause_tts_bumps_reply_epoch():
    """_pause_tts (barge-in) bumps the reply epoch (invalidating old turns)."""
    sm = _build_sm()
    sm._ensure_tts_stream_state()
    before = sm._llm_reply_epoch
    await sm._pause_tts()
    assert sm._llm_reply_epoch == before + 1


@pytest.mark.asyncio
async def test_stop_tts_bumps_reply_epoch():
    """_stop_tts (exit word) bumps the reply epoch."""
    sm = _build_sm()
    sm._ensure_tts_stream_state()
    before = sm._llm_reply_epoch
    await sm._stop_tts()
    assert sm._llm_reply_epoch == before + 1


def test_asr_partial_carries_live_reply_epoch():
    """AsrPartial created in the dialog path carries the live epoch."""
    sm = _build_sm()
    sm._ensure_tts_stream_state()
    partial = AsrPartial(
        text="hello",
        is_final=False,
        timestamp_ms=1.0,
        reply_epoch=getattr(sm, "_llm_reply_epoch", 0),
    )
    assert partial.reply_epoch == 0
    # After a turn start the partials carry the bumped value.
    sm._llm_reply_epoch = 3
    partial2 = AsrPartial(
        text="hi",
        is_final=False,
        timestamp_ms=1.0,
        reply_epoch=getattr(sm, "_llm_reply_epoch", 0),
    )
    assert partial2.reply_epoch == 3


# ---------------------------------------------------------------------------
# Backend: session broadcast carries reply_epoch
# ---------------------------------------------------------------------------


def test_session_manager_llm_reply_broadcast_payload():
    """notify_session_llm_reply payload carries reply_epoch (server contract)."""
    from joy_interaction_webui import server

    captured: list = []

    def _fake_send(session_id, message):
        captured.append(json.loads(message))

    with patch.object(server, "send_to_session", side_effect=_fake_send):
        server.notify_session_llm_reply("s1", "hello", source="jarvis_voice", reply_epoch=7)
    assert captured[0]["type"] == "llm_reply"
    assert captured[0]["reply_epoch"] == 7


def test_session_manager_asr_partial_payload_carries_epoch():
    from joy_interaction_webui import server

    captured: list = []

    def _fake_send(session_id, message):
        captured.append(json.loads(message))

    with patch.object(server, "send_to_session", side_effect=_fake_send):
        server.notify_session_asr_partial("s1", "partial", is_final=False, reply_epoch=4)
        server.notify_session_pilot_utterance("s1", "final", source="asr", reply_epoch=5)
    assert captured[0]["reply_epoch"] == 4
    assert captured[1]["reply_epoch"] == 5


def test_session_callback_reads_sm_current_turn_epoch():
    """The jarvis session's llm callback tags the payload with the turn epoch."""
    from joy_interaction_webui import server

    sm = _build_sm()
    sm._current_turn_reply_epoch = 9
    manager = JarvisSessionManager(config=sm.config)
    manager._sessions["s1"] = SimpleNamespace(state_machine=sm)

    captured: list = []

    def _fake_send(session_id, message):
        captured.append(json.loads(message))

    cb = manager._make_llm_callback("s1")
    with patch.object(server, "send_to_session", side_effect=_fake_send):
        cb("hello", source="jarvis_voice")
    assert captured[0]["reply_epoch"] == 9


# ---------------------------------------------------------------------------
# Front-end static contract
# ---------------------------------------------------------------------------

INDEX_HTML = REPO / "services" / "webui" / "src" / "joy_interaction_webui" / "static" / "index.html"
_JS = INDEX_HTML.read_text(encoding="utf-8")


def test_frontend_llm_reply_branch_has_epoch_guard():
    """installLlmReplyHandler's llm_reply branch must validate reply_epoch."""
    idx = _JS.find("function installLlmReplyHandler")
    assert idx != -1
    llm_branch = _JS[idx:]
    assert "data.type === 'llm_reply'" in llm_branch
    assert "data.reply_epoch < llmReplyGeneration" in llm_branch
    # Discard path skips render + play.
    assert "stale reply_epoch=" in llm_branch
    # Accept path still renders + plays.
    accept_idx = llm_branch.index("appendJarvisToResult(data.text || '', data.source || 'jarvis')")
    assert "playLlmReplyAudio(data.text || '', { source: data.source || 'jarvis' })" in llm_branch
    assert llm_branch.index("appendJarvisToResult") < llm_branch.index("playLlmReplyAudio")
    # The guard must appear BEFORE the render/play calls.
    assert llm_branch.index("data.reply_epoch < llmReplyGeneration") < accept_idx


def test_frontend_adopts_epoch_from_asr_partial_and_pilot():
    """asr_partial / pilot_utterance branches raise llmReplyGeneration."""
    idx = _JS.find("function installLlmReplyHandler")
    assert idx != -1
    handler = _JS[idx:]
    assert "data.type === 'asr_partial'" in handler
    assert "data.reply_epoch > llmReplyGeneration" in handler
    assert "data.type === 'pilot_utterance'" in handler
    # Both branches still stop the reply audio first (P0 barge-in intact).
    assert handler.count("stopLlmReplyAudio()") >= 2


def test_frontend_llm_reply_generation_is_declared():
    """llmReplyGeneration is a distinct declaration from llmReplyEpoch."""
    assert "let llmReplyGeneration = 0;" in _JS
    # The TTS playback epoch keeps its single-writer contract.
    import re

    writes = re.findall(r"llmReplyEpoch\s*(\+=|-=|=)\s*([^;]*)", _JS)
    writes = [op + val for op, val in writes]
    assert writes == ["=0", "+=1"]
