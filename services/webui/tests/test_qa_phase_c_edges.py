"""Independent QA boundary tests for Phase C (P1 reply_epoch + shared extraction).

QA engineer's own edge cases on top of the engineer's suites. These pin the
PROTOCOL (backend bump timing -> payload tagging -> front-end guard) with a
faithful Python model of the front-end ``llmReplyGeneration`` logic, plus
extra streaming-consumer edges and front-end static contracts.

Run: python -m pytest tests/test_qa_phase_c_edges.py -q
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

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
from joy_interaction_webui.turn_streaming import StreamingTurnConsumer  # noqa: E402

INDEX_HTML = REPO / "services" / "webui" / "src" / "joy_interaction_webui" / "static" / "index.html"
_JS = INDEX_HTML.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Faithful model of the front-end llmReplyGeneration guard (index.html).
# Mirrors the exact accept/drop/raise semantics of installLlmReplyHandler.
# ---------------------------------------------------------------------------


class FrontendGuard:
    """Python mirror of the index.html llmReplyGeneration logic."""

    def __init__(self):
        self.generation = 0
        self.accepted = []  # (reply_epoch, text) that would render + play
        self.dropped = []  # reply_epoch values that were discarded

    def _is_num(self, v):
        # mirrors `typeof data.reply_epoch === 'number'`
        return isinstance(v, int)

    def on_asr_partial(self, reply_epoch):
        if self._is_num(reply_epoch) and reply_epoch > self.generation:
            self.generation = reply_epoch

    def on_pilot_utterance(self, reply_epoch):
        self.on_asr_partial(reply_epoch)

    def on_llm_reply(self, reply_epoch, text):
        if self._is_num(reply_epoch) and reply_epoch < self.generation:
            self.dropped.append((reply_epoch, text))
            return False  # dropped: no render, no play
        if self._is_num(reply_epoch) and reply_epoch >= self.generation:
            self.generation = reply_epoch
        self.accepted.append((reply_epoch, text))
        return True


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
    def __init__(self, lines, status=200, fail_after=None):
        self._lines = list(lines)
        self.status_code = status
        self.fail_after = fail_after

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def aread(self):
        return b"stream error"

    def aiter_lines(self):
        async def gen():
            for i, line in enumerate(self._lines):
                if self.fail_after is not None and i >= self.fail_after:
                    raise RuntimeError("connection reset mid-stream")
                yield line
            if self.fail_after is not None and self.fail_after >= len(self._lines):
                raise RuntimeError("connection reset mid-stream")

        return gen()


class _FakeClient:
    def __init__(self, stream_response):
        self._stream_response = stream_response
        self.stream_calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def stream(self, method, url, **kwargs):
        self.stream_calls.append((method, url, kwargs))
        return self._stream_response


def _consumer(**overrides):
    kwargs = {
        "endpoint_url": "http://stub/v1/text/chat",
        "model": "stub",
        "system_prompt": "be brief",
        "history_snapshot": [],
    }
    kwargs.update(overrides)
    return StreamingTurnConsumer(**kwargs)


def _run(consumer, lines, *, reply_session=0, fail_after=None, status=200):
    client = _FakeClient(_FakeStreamResponse(lines, status=status, fail_after=fail_after))
    with patch("httpx.AsyncClient", return_value=client):
        return asyncio.run(
            consumer.consume("hello", interaction_mode="jarvis", reply_session=reply_session)
        )


# ---------------------------------------------------------------------------
# P1 protocol: two-turn late-broadcast suppression (real backend values)
# ---------------------------------------------------------------------------


def test_two_turn_late_reply_is_dropped_by_frontend_guard():
    """turn1 reply arrives AFTER turn2 bump -> the front-end guard drops it.

    Drives the REAL backend: dispatcher turn-start bump, barge-in bump,
    _finish_llm_turn tagging, then feeds the resulting broadcast epochs into
    the front-end guard model.
    """
    sm = _build_sm()
    sm._ensure_tts_stream_state()
    guard = FrontendGuard()
    broadcasts: list = []

    # Real session-callback equivalent: reads _current_turn_reply_epoch.
    def on_llm_response(text, source):
        broadcasts.append((sm._current_turn_reply_epoch, text))

    sm.on_llm_response = on_llm_response
    captured_turn1: list = []

    async def fake_streaming(text, *, stream_tts, interaction_mode, reply_epoch, finish=False):
        if text == "turn1":
            captured_turn1.append(reply_epoch)  # turn1 in flight, NOT finished
        else:
            # turn2 finishes normally -> broadcasts with its own epoch.
            await sm._finish_llm_turn(
                text=text,
                response="reply2",
                decision="response",
                delegation_question=None,
                stream_tts=False,
                force_jarvis_voice=True,
                reply_epoch=reply_epoch,
            )

    with patch.object(sm, "_send_to_llm_streaming", side_effect=fake_streaming):
        asyncio.run(sm._send_to_llm("turn1", stream_tts=False, interaction_mode="jarvis"))
        turn1_epoch = captured_turn1[0]
        assert turn1_epoch == 1
        assert sm._llm_reply_epoch == 1  # only the turn-start bump so far

        # Barge-in: user starts speaking -> backend bumps; partial carries it.
        asyncio.run(sm._pause_tts())
        assert sm._llm_reply_epoch == 2

        # Real partial emission path: AsrPartial carries the live epoch.
        partials: list = []
        sm.on_asr_partial = partials.append
        sm._asr = SimpleNamespace(feed_chunk=lambda pcm: "hello there")
        asyncio.run(sm._handle_dialog_legacy(b"\x00"))
        assert partials and partials[0].reply_epoch == 2
        guard.on_asr_partial(partials[0].reply_epoch)
        assert guard.generation == 2

        # turn2 starts + finishes: dispatcher bump -> epoch 3, broadcast 3.
        asyncio.run(sm._send_to_llm("turn2", stream_tts=False, interaction_mode="jarvis"))
        assert sm._llm_reply_epoch == 3
        assert broadcasts == [(3, "reply2")]
        guard.on_llm_reply(*broadcasts[0])
        assert guard.generation == 3

        # turn1's late reply finally broadcasts with its OLD captured epoch.
        asyncio.run(
            sm._finish_llm_turn(
                text="turn1",
                response="reply1",
                decision="response",
                delegation_question=None,
                stream_tts=False,
                force_jarvis_voice=True,
                reply_epoch=turn1_epoch,
            )
        )
        assert broadcasts[-1] == (1, "reply1")
        guard.on_llm_reply(*broadcasts[-1])

    # The stale turn1 reply is dropped; only turn2 rendered/played.
    assert [e for e, _ in guard.accepted] == [3]
    assert [e for e, _ in guard.dropped] == [1]
    assert "reply1" not in [t for _, t in guard.accepted]


def test_two_turn_normal_order_is_not_over_suppressed():
    """Without a barge-in, both turns' replies are accepted in order (no 误丢)."""
    sm = _build_sm()
    sm._ensure_tts_stream_state()
    guard = FrontendGuard()
    broadcasts: list = []
    sm.on_llm_response = lambda text, source: broadcasts.append((sm._current_turn_reply_epoch, text))

    async def fake_streaming(text, *, stream_tts, interaction_mode, reply_epoch):
        await sm._finish_llm_turn(
            text=text,
            response=f"reply:{text}",
            decision="response",
            delegation_question=None,
            stream_tts=False,
            force_jarvis_voice=True,
            reply_epoch=reply_epoch,
        )

    with patch.object(sm, "_send_to_llm_streaming", side_effect=fake_streaming):
        asyncio.run(sm._send_to_llm("turn1", stream_tts=False, interaction_mode="jarvis"))
        asyncio.run(sm._send_to_llm("turn2", stream_tts=False, interaction_mode="jarvis"))

    assert [e for e, _ in broadcasts] == [1, 2]
    for epoch, text in broadcasts:
        guard.on_llm_reply(epoch, text)
    assert [e for e, _ in guard.accepted] == [1, 2]
    assert guard.dropped == []


def test_barge_in_partial_expires_inflight_reply():
    """The exact Known Issue: barge-in partial raises generation -> late reply dropped."""
    sm = _build_sm()
    sm._ensure_tts_stream_state()
    guard = FrontendGuard()

    # Turn 1 starts (epoch 1) and its reply is still in flight.
    sm._llm_reply_epoch += 1
    turn1_epoch = sm._llm_reply_epoch
    assert turn1_epoch == 1

    # Barge-in: user speech bumps the epoch and the partial carries it.
    asyncio.run(sm._pause_tts())
    assert sm._llm_reply_epoch == 2
    partial = AsrPartial(
        text="wait",
        is_final=False,
        timestamp_ms=1.0,
        reply_epoch=getattr(sm, "_llm_reply_epoch", 0),
    )
    guard.on_asr_partial(partial.reply_epoch)
    assert guard.generation == 2

    # The stale reply (captured epoch 1) finally broadcasts -> dropped.
    guard.on_llm_reply(turn1_epoch, "old reply")
    assert [e for e, _ in guard.dropped] == [1]
    assert guard.accepted == []


def test_exit_word_stop_bumps_epoch_and_expires_inflight():
    """_stop_tts (exit word) bumps; a subsequent partial adopts the new epoch."""
    sm = _build_sm()
    sm._ensure_tts_stream_state()
    guard = FrontendGuard()

    sm._llm_reply_epoch += 1  # turn 1
    turn1_epoch = sm._llm_reply_epoch

    asyncio.run(sm._stop_tts())  # exit word
    assert sm._llm_reply_epoch == turn1_epoch + 1

    partial = AsrPartial(
        text="bye",
        is_final=False,
        timestamp_ms=1.0,
        reply_epoch=getattr(sm, "_llm_reply_epoch", 0),
    )
    guard.on_asr_partial(partial.reply_epoch)
    guard.on_llm_reply(turn1_epoch, "old reply")
    assert [e for e, _ in guard.dropped] == [turn1_epoch]


def test_call_mode_non_streaming_broadcast_carries_epoch():
    """interaction_mode='call' (non-streaming) also tags llm_reply with reply_epoch."""
    sm = _build_sm(llm_streaming_enabled=True)
    broadcasts: list = []
    sm.on_llm_response = lambda text, source: broadcasts.append((sm._current_turn_reply_epoch, text))

    class _FakePost:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "choices": [{"message": {"content": "call reply"}}],
                "streamingharness": {"decision": "response", "delegation_question": None},
            }

    class _FakeClientPost:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def stream(self, method, url, **kwargs):
            raise AssertionError("call mode must NOT take the streaming path")

        async def post(self, url, json=None):
            return _FakePost()

    with patch("httpx.AsyncClient", return_value=_FakeClientPost()):
        asyncio.run(sm._send_to_llm("hi", stream_tts=False, interaction_mode="call"))

    assert sm._llm_reply_epoch == 1
    assert broadcasts == [(1, "call reply")]


def test_session_reply_epoch_reads_state_machine_single_source_of_truth():
    """_session_reply_epoch reads the SM attribute; missing session/attr -> 0."""
    sm = _build_sm()
    sm._current_turn_reply_epoch = 5
    manager = JarvisSessionManager(config=sm.config)
    manager._sessions["s1"] = SimpleNamespace(state_machine=sm)

    assert manager._session_reply_epoch("s1", attr="_current_turn_reply_epoch") == 5
    assert manager._session_reply_epoch("s1", attr="_llm_reply_epoch") == 0
    # Missing session / missing attr -> 0 (front-end treats 0 as no constraint).
    assert manager._session_reply_epoch("ghost", attr="_current_turn_reply_epoch") == 0
    assert manager._session_reply_epoch("s1", attr="_does_not_exist") == 0


# ---------------------------------------------------------------------------
# Shared extraction: streaming consumer edge cases
# ---------------------------------------------------------------------------


def test_corrected_delegation_reset_blocks_later_content():
    """After corrected-delegation, later content frames must NOT flush/accumulate."""
    sentences: list = []
    consumer = _consumer(on_sentence=lambda s, seq, rs: sentences.append((seq, s)))
    lines = [
        _frame(type="decision", decision="response", delegation_question=None),
        _frame(type="content", token="Note: "),
        _frame(type="decision", decision="delegation", delegation_question="q1", corrected=True),
        # content arriving AFTER the corrected decision must be ignored
        _frame(type="content", token="leaked after reset"),
        _frame(type="done", decision="delegation", full_text="", delegation_question="q1"),
    ]
    result = _run(consumer, lines)
    assert sentences == []
    assert result.decision == "delegation"
    assert "leaked" not in result.full_response
    assert result.full_response == ""


def test_pre_frame_failure_never_calls_on_sentence_no_double_play():
    """Pre-decision failure: needs_non_streaming_retry AND zero on_sentence calls."""
    sentences: list = []
    consumer = _consumer(on_sentence=lambda s, seq, rs: sentences.append(seq))
    result = _run(consumer, [], status=500)
    assert result.needs_non_streaming_retry is True
    assert sentences == [], "nothing spoken -> no double-play on non-streaming retry"


def test_mid_stream_failure_flushes_all_buffered_sentences():
    """Mid-stream failure after a flush boundary still flushes the full remainder."""
    sentences: list = []
    consumer = _consumer(on_sentence=lambda s, seq, rs: sentences.append((seq, s.strip())))
    lines = [
        _frame(type="decision", decision="response", delegation_question=None),
        _frame(type="content", token="Hello there."),  # flushes sentence 0
        _frame(type="content", token=" How are you"),  # buffered, no terminal punct
        _frame(type="content", token=" doing?"),  # buffered with terminal punct
    ]
    result = _run(consumer, lines, fail_after=4)
    assert sentences == [(0, "Hello there."), (1, "How are you doing?")]
    assert result.full_response == "Hello there. How are you doing?"
    assert result.needs_non_streaming_retry is False


def test_cancel_checked_between_frames_keeps_played_keeps_no_later():
    """is_cancelled is polled per frame: flushed sentences stay, later frames stop."""
    sentences: list = []
    consumer = _consumer(on_sentence=lambda s, seq, rs: sentences.append((seq, s.strip())))
    cancel = {"flag": False}

    def is_cancelled():
        return cancel["flag"]

    consumer.is_cancelled = is_cancelled

    class CancelStream(_FakeStreamResponse):
        def aiter_lines(self):
            async def gen():
                yield _frame(type="decision", decision="response", delegation_question=None)
                yield _frame(type="content", token="First sentence.")
                cancel["flag"] = True  # barge-in between frames
                yield _frame(type="content", token="Never spoken.")

            return gen()

    client = _FakeClient(CancelStream([]))
    with patch("httpx.AsyncClient", return_value=client):
        result = asyncio.run(consumer.consume("hello", interaction_mode="jarvis", reply_session=0))

    assert result.cancelled is True
    assert sentences == [(0, "First sentence.")], "already-flushed sentence stays"
    assert "Never spoken." not in result.full_response


# ---------------------------------------------------------------------------
# Front-end static contracts (independent re-assertion)
# ---------------------------------------------------------------------------


def test_llm_reply_generation_has_no_local_self_increment():
    """llmReplyGeneration must NEVER be self-incremented locally (no drift)."""
    # No +=, -=, ++, -- on llmReplyGeneration anywhere.
    assert not re.search(r"llmReplyGeneration\s*(\+=|-=|\+\+|--)", _JS)
    # Every write is `= 0` (init) or `= data.reply_epoch` (backend payload).
    writes = re.findall(r"llmReplyGeneration\s*=\s*([^;]*)", _JS)
    assert writes.count("0") == 1, f"exactly one init write, got {writes}"
    assert all(w == "data.reply_epoch" for w in writes if w != "0"), writes
    # Exactly 3 backend-payload adopt sites: llm_reply accept, asr_partial, pilot.
    assert writes.count("data.reply_epoch") == 3, writes


def test_llm_reply_guard_precedes_render_and_play():
    """The stale check must run BEFORE any render/play in the llm_reply branch."""
    idx = _JS.find("function installLlmReplyHandler")
    assert idx != -1
    llm_branch = _JS[idx:]
    guard_idx = llm_branch.index("data.reply_epoch < llmReplyGeneration")
    render_idx = llm_branch.index("appendJarvisToResult(data.text || '', data.source || 'jarvis')")
    play_idx = llm_branch.index("playLlmReplyAudio(data.text || '', { source: data.source || 'jarvis' })")
    assert guard_idx < render_idx < play_idx


def test_p0_tts_playback_epoch_single_writer_intact():
    """P1 must not break the P0 llmReplyEpoch single-writer contract."""
    writes = re.findall(r"llmReplyEpoch\s*(\+=|-=|=)\s*([^;]*)", _JS)
    writes = [op + val for op, val in writes]
    assert writes == ["=0", "+=1"]
