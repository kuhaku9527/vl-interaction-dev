"""Live mode (Phase C C.A) tests — LiveStateMachine + /api/live/* endpoints.

Covers the spec ``draft-live-interaction-layer.md`` §4 C.A acceptance:

  * resident listening (no wake word -> direct LISTENING);
  * speak -> VAD/ASR -> commit -> LLM(interaction_mode="live") -> sentence TTS;
  * interrupt (HARD_INTERRUPTED -> COOLDOWN -> LISTENING) with P1 epoch bump;
  * reply_epoch bump semantics (turn-start / barge-in);
  * end_turn (browser-driven exit);
  * fail-open (controller / ASR / LLM errors never crash the loop);
  * server endpoints /api/live/start | stop | status (mock manager).

Run: python -m pytest tests/test_live_mode.py -q
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[3]
WEBUI_SRC = REPO / "services" / "webui" / "src"
for _p in (str(REPO), str(WEBUI_SRC)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from unittest.mock import AsyncMock  # noqa: E402

import pytest  # noqa: E402
from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from joy_interaction_webui import live_mode as live_module  # noqa: E402
from joy_interaction_webui.jarvis_mode import JarvisConfig  # noqa: E402
from joy_interaction_webui.live_mode import LiveStateMachine  # noqa: E402
from joy_interaction_webui.live_routes import setup_live_routes  # noqa: E402
from joy_interaction_webui.turn_controller import (  # noqa: E402
    TurnConfig,
    TurnController,
    TurnState,
)
from joy_interaction_webui.turn_streaming import StreamingTurnResult  # noqa: E402

PCM = b"\x00\x00" * 100  # 100ms of 16 kHz mono silence (int16)


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class FakeClock:
    """Drop-in replacement for the ``time`` module used by live_mode, AND a
    callable monotonic clock for the TurnController (seconds)."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def time(self) -> float:
        return self.now

    def monotonic(self) -> float:
        return self.now

    def __call__(self) -> float:
        # TurnController clock contract: Callable[[], float] in seconds.
        return self.now


class FakeVAD:
    available = True

    def __init__(self) -> None:
        self.speech = False
        self.accepted = 0

    def accept_waveform(self, samples) -> None:
        self.accepted += 1

    def is_speech(self) -> bool:
        return self.speech

    def set_speech(self, value: bool) -> None:
        self.speech = value


class FakeASR:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.start_calls = 0
        self.stop_calls = 0
        self.feed_calls = 0

    def start(self) -> None:
        self.start_calls += 1

    def stop(self) -> None:
        self.stop_calls += 1

    def feed_chunk(self, pcm: bytes) -> str:
        self.feed_calls += 1
        return self.text

    def set_text(self, text: str) -> None:
        self.text = text


class BoomASR(FakeASR):
    def feed_chunk(self, pcm: bytes) -> str:
        raise RuntimeError("asr boom")


class FakeDetector:
    """Fake AddresseeDetector (Phase 1) — controllable classify/enroll."""

    available = True

    def __init__(self, *, enrolled=True, target=True, score=0.9, boom=False) -> None:
        self._enrolled = enrolled
        self._target = target
        self._score = score
        self.boom = boom
        self.enroll_calls: list = []
        self.num_enroll_calls = 0

    def is_enrolled(self) -> bool:
        return self._enrolled

    def classify(self, pcm: bytes) -> tuple[bool, float]:
        if self.boom:
            raise RuntimeError("detector boom")
        if not self._enrolled:
            return True, 1.0
        return self._target, self._score

    def enroll(self, segments) -> bool:
        self.enroll_calls.append(list(segments))
        self.num_enroll_calls += 1
        self._enrolled = True
        return True


class FakeConsumer:
    """Fake StreamingTurnConsumer: records kwargs + yields sentences."""

    def __init__(self, result: StreamingTurnResult, sentences=()) -> None:
        self.result = result
        self.sentences = list(sentences)
        self.consume_kwargs: dict = {}

    def __call__(self, **kwargs):
        self.consume_kwargs = dict(kwargs)
        return self

    async def consume(self, text, interaction_mode, reply_session):
        self.consume_kwargs.update(
            {
                "text": text,
                "interaction_mode": interaction_mode,
                "reply_session": reply_session,
            }
        )
        on_sentence = self.consume_kwargs.get("on_sentence")
        for seq, sentence in enumerate(self.sentences):
            if on_sentence is not None:
                on_sentence(sentence, seq, reply_session)
        return self.result


def _stub_config() -> JarvisConfig:
    return JarvisConfig(
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
        vad_enabled=False,
    )


def build_live(
    *,
    controller: TurnController | None = None,
    vad: FakeVAD | None = None,
    asr: FakeASR | None = None,
    **overrides,
):
    """Build a LiveStateMachine with controllable fake VAD / ASR."""
    vad = vad if vad is not None else FakeVAD()
    asr = asr if asr is not None else FakeASR()
    sm = LiveStateMachine(
        config=_stub_config(),
        session_id="s1",
        vad=vad,
        asr=asr,
        controller=controller,
        **overrides,
    )
    return sm, vad, asr


def _live_controller(clock: FakeClock | None = None, cooldown_ms: int = 10) -> TurnController:
    cfg = TurnConfig.live()
    cfg.cooldown_ms = cooldown_ms
    return TurnController(cfg, clock=clock)


def _result(**kwargs) -> StreamingTurnResult:
    defaults = {
        "full_response": "",
        "decision": "response",
        "delegation_question": None,
        "cancelled": False,
        "reply_session": 0,
        "sentence_count": 0,
    }
    defaults.update(kwargs)
    return StreamingTurnResult(**defaults)


# ---------------------------------------------------------------------------
# 1. Resident listening (no wake word)
# ---------------------------------------------------------------------------


def test_resident_listening_no_wake_word():
    """TurnConfig.live() has wake_gate_enabled=False -> direct LISTENING."""
    sm, _, _ = build_live()
    assert sm.turn_state == TurnState.LISTENING


def test_feed_speech_moves_listening_to_user_speaking():
    sm, vad, _ = build_live()
    vad.set_speech(True)
    asyncio.run(sm.feed_audio(PCM))
    assert sm.turn_state == TurnState.USER_SPEAKING


# ---------------------------------------------------------------------------
# 2. speak -> commit -> LLM(live) -> sentence TTS
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_speak_commit_llm_live_sentence_tts(monkeypatch):
    clock = FakeClock(1000.0)
    sm, vad, asr = build_live(controller=_live_controller(clock))
    monkeypatch.setattr(live_module, "time", clock)

    pushes: list = []
    broadcasts: list = []
    sm.on_tts_sentence = lambda text, seq, audio_b64, session: pushes.append((seq, text))
    sm.on_llm_response = lambda text, source: broadcasts.append((text, source))

    fake = FakeConsumer(
        _result(full_response="好的，我在听。", sentence_count=1),
        sentences=["好的，我在听。"],
    )
    monkeypatch.setattr(live_module, "StreamingTurnConsumer", fake)

    async def fake_tts(text):
        return b"\x00\x00" * 100

    monkeypatch.setattr(sm, "_fetch_tts_pcm", fake_tts)

    # 1. Speech onset -> USER_SPEAKING.
    vad.set_speech(True)
    await sm.feed_audio(PCM)
    assert sm.turn_state == TurnState.USER_SPEAKING

    # 2. ASR partials accumulate (utterance > min_utterance_ms).
    clock.now += 0.5
    asr.set_text("你好")
    await sm.feed_audio(PCM)
    assert sm._current_asr_text == "你好"

    # 3. User stops -> 2s ASR stall -> endpoint -> commit -> LLM(live).
    vad.set_speech(False)
    clock.now += 2.0
    await sm.feed_audio(PCM)

    assert sm._current_asr_text == ""  # commit reset the ASR stream
    assert fake.consume_kwargs["text"] == "你好"
    assert fake.consume_kwargs["interaction_mode"] == "live"
    assert sm._llm_reply_epoch == 1  # turn-start bump
    assert broadcasts == [("好的，我在听。", "live_voice")]
    assert sm._conv_history[-1] == ("assistant", "好的，我在听。")

    # 4. Sentence TTS pushed and the turn returns to LISTENING.
    await asyncio.gather(*list(sm._tts_sentence_tasks), return_exceptions=True)
    if sm._tts_turn_task is not None:
        await sm._tts_turn_task
    assert pushes and pushes[0][1] == "好的，我在听。"
    assert sm.turn_state == TurnState.LISTENING


@pytest.mark.asyncio
async def test_not_for_me_decision_no_broadcast_back_to_listening(monkeypatch, caplog):
    """Addressee Phase 2: decision="not-for-me" -> no TTS, back to LISTENING,
    with a dedicated ``[addressee] semantic not-for-me`` log (distinct from
    silence)."""
    clock = FakeClock(1000.0)
    sm, vad, asr = build_live(controller=_live_controller(clock))
    monkeypatch.setattr(live_module, "time", clock)

    pushes: list = []
    broadcasts: list = []
    sm.on_tts_sentence = lambda text, seq, audio_b64, session: pushes.append((seq, text))
    sm.on_llm_response = lambda text, source: broadcasts.append((text, source))

    fake = FakeConsumer(
        _result(full_response="", decision="not-for-me", sentence_count=0),
        sentences=[],
    )
    monkeypatch.setattr(live_module, "StreamingTurnConsumer", fake)

    with caplog.at_level(logging.INFO, logger="joyai.live_mode"):
        # 1. Speech onset -> USER_SPEAKING.
        vad.set_speech(True)
        await sm.feed_audio(PCM)
        assert sm.turn_state == TurnState.USER_SPEAKING

        # 2. ASR partial accumulates.
        clock.now += 0.5
        asr.set_text("这关怎么这么难啊")
        await sm.feed_audio(PCM)
        assert sm._current_asr_text == "这关怎么这么难啊"

        # 3. User stops -> 2s ASR stall -> endpoint -> commit -> LLM.
        vad.set_speech(False)
        clock.now += 2.0
        await sm.feed_audio(PCM)

    assert sm._current_asr_text == ""  # commit reset the ASR stream
    assert fake.consume_kwargs["interaction_mode"] == "live"
    # Zero TTS sentences; the turn is not broadcast as a spoken reply and the
    # controller returns to LISTENING (same lifecycle as silence).
    assert pushes == []
    assert broadcasts == [("", "live_text")]
    assert sm.turn_state == TurnState.LISTENING
    assert "[addressee] semantic not-for-me" in caplog.text


@pytest.mark.asyncio
async def test_garbage_utterance_is_dropped_not_sent(monkeypatch):
    """A too-short / noisy utterance is dropped (no LLM call, back to LISTENING)."""
    clock = FakeClock(1000.0)
    sm, vad, asr = build_live(controller=_live_controller(clock))
    monkeypatch.setattr(live_module, "time", clock)
    calls: list = []

    async def fake_send(text, *, interaction_mode="live", stream=True):
        calls.append(text)

    monkeypatch.setattr(sm, "_send_to_llm", fake_send)

    vad.set_speech(True)
    await sm.feed_audio(PCM)
    clock.now += 0.5
    asr.set_text("啊")
    await sm.feed_audio(PCM)
    vad.set_speech(False)
    clock.now += 2.0
    await sm.feed_audio(PCM)

    assert calls == []
    assert sm.turn_state == TurnState.LISTENING


# ---------------------------------------------------------------------------
# 3. Interrupt: HARD_INTERRUPTED -> COOLDOWN -> LISTENING (barge-in)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_interrupt_hard_cooldown_listening(monkeypatch):
    clock = FakeClock(1000.0)
    sm, vad, asr = build_live(controller=_live_controller(clock))
    monkeypatch.setattr(live_module, "time", clock)

    # Drive the controller into SPEAKING (agent reply in progress).
    sm._ctrl.on_speech_started(0.9)  # LISTENING -> USER_SPEAKING
    clock.now += 1.0  # utterance longer than min_utterance_ms
    sm._ctrl.on_speech_stopped(600)  # USER_SPEAKING -> PROCESSING (commit flag set)
    sm._commit_pending = False  # skip the LLM send; we are only testing barge-in
    sm._ctrl.on_llm_token("好的")  # PROCESSING -> THINKING
    sm._ctrl.on_tts_started()  # THINKING -> SPEAKING
    assert sm.turn_state == TurnState.SPEAKING

    # Simulate an in-flight sentence task.
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_tts(sentence, seq, reply_session, epoch):
        started.set()
        await release.wait()

    task = asyncio.create_task(slow_tts("stale", 0, 0, sm._tts_sentence_epoch))
    sm._tts_sentence_tasks.add(task)
    task.add_done_callback(sm._tts_sentence_tasks.discard)
    await started.wait()

    # User speaks over the reply -> SPEAKING -> HARD_INTERRUPTED + barge-in.
    vad.set_speech(True)
    await sm.feed_audio(PCM)
    assert sm.turn_state == TurnState.HARD_INTERRUPTED
    assert sm._llm_reply_epoch == 1  # P1: barge-in bump
    assert sm._tts_sentence_epoch == 1
    await asyncio.gather(task, return_exceptions=True)
    assert task.cancelled(), "barge-in must cancel in-flight sentence tasks"
    assert sm._llm_stream_cancel is True

    # User keeps talking -> partials (controller stays HARD_INTERRUPTED).
    asr.set_text("等一下")
    await sm.feed_audio(PCM)
    assert sm.turn_state == TurnState.HARD_INTERRUPTED

    # User stops -> 2s stall -> on_speech_stopped -> COOLDOWN + ASR reset.
    vad.set_speech(False)
    clock.now += 2.0
    await sm.feed_audio(PCM)
    assert sm.turn_state == TurnState.COOLDOWN
    assert sm._current_asr_text == ""  # ASR reset for the next turn

    # Cooldown elapsed -> LISTENING.
    await asyncio.sleep(0.05)
    assert sm.turn_state == TurnState.LISTENING
    await sm.stop()


# ---------------------------------------------------------------------------
# 4. reply_epoch bump semantics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reply_epoch_bump_on_turn_start(monkeypatch):
    sm, _, _ = build_live()
    assert sm._llm_reply_epoch == 0
    fake = FakeConsumer(_result(full_response="ok", sentence_count=0))
    monkeypatch.setattr(live_module, "StreamingTurnConsumer", fake)

    await sm._send_to_llm("你好", interaction_mode="live", stream=True)

    assert sm._llm_reply_epoch == 1
    assert fake.consume_kwargs["interaction_mode"] == "live"
    assert fake.consume_kwargs["stream_logger"] is not None


@pytest.mark.asyncio
async def test_reply_epoch_bump_on_barge_in():
    sm, _, _ = build_live()
    assert sm._llm_reply_epoch == 0
    await sm._handle_barge_in(0.9)
    assert sm._llm_reply_epoch == 1
    assert sm._tts_sentence_epoch == 1


# ---------------------------------------------------------------------------
# 5. end_turn (browser-driven exit)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_ends_turn_and_stops_asr():
    sm, vad, asr = build_live()
    vad.set_speech(True)
    await sm.feed_audio(PCM)
    assert sm.turn_state == TurnState.USER_SPEAKING
    await sm.stop()
    assert sm.turn_state == TurnState.ENDED
    assert asr.stop_calls == 1


# ---------------------------------------------------------------------------
# 6. fail-open
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fail_open_controller_exception_logs_and_survives():
    """Illegal controller events (e.g. in ENDED) log + never crash the loop."""
    sm, vad, _ = build_live()
    await sm.stop()  # -> ENDED
    vad.set_speech(True)
    await sm.feed_audio(PCM)  # on_speech_started in ENDED -> TurnStateError, caught
    assert sm.turn_state == TurnState.ENDED


@pytest.mark.asyncio
async def test_fail_open_asr_exception_logs_and_survives():
    sm, vad, _ = build_live(asr=BoomASR())
    vad.set_speech(True)
    await sm.feed_audio(PCM)
    # VAD rising edge still drove the controller; ASR failure was logged.
    assert sm.turn_state == TurnState.USER_SPEAKING


@pytest.mark.asyncio
async def test_fail_open_llm_stream_retries_non_streaming(monkeypatch):
    """Pre-frame stream failure -> clean non-streaming retry (never lose reply)."""
    sm, _, _ = build_live()
    fake = FakeConsumer(
        _result(decision="silence", full_response="", needs_non_streaming_retry=True)
    )
    monkeypatch.setattr(live_module, "StreamingTurnConsumer", fake)
    retry_calls: list = []

    async def fake_retry(text, *, interaction_mode="live", reply_epoch=None, frames=None):
        retry_calls.append((text, interaction_mode, reply_epoch, frames))

    monkeypatch.setattr(sm, "_send_to_llm_non_streaming", fake_retry)

    await sm._send_to_llm("你好", interaction_mode="live", stream=True)

    # frames defaults to None on the pure-text path (zero regression).
    assert retry_calls == [("你好", "live", 1, None)]


@pytest.mark.asyncio
async def test_fail_open_cancelled_stream_skips_broadcast(monkeypatch):
    """Barge-in cancels the stream; the partial reply is NOT broadcast."""
    sm, _, _ = build_live()
    broadcasts: list = []
    sm.on_llm_response = lambda text, source: broadcasts.append((text, source))
    fake = FakeConsumer(_result(full_response="old reply", decision="response", cancelled=True))
    monkeypatch.setattr(live_module, "StreamingTurnConsumer", fake)

    await sm._send_to_llm("你好", interaction_mode="live", stream=True)

    assert broadcasts == []
    assert ("你好", "assistant") not in list(sm._conv_history)


# ---------------------------------------------------------------------------
# 7. Server endpoints (/api/live/start | stop | status)
# ---------------------------------------------------------------------------


class FakeManager:
    """Mock JarvisSessionManager with live-session tracking."""

    def __init__(self):
        self.live_sessions: dict = {}
        self.modes: list = []
        self.removed: list = []

    async def create_session(self, session_id, mode="jarvis"):
        self.modes.append((session_id, mode))
        session = SimpleNamespace(
            get_state_for_browser=lambda: {
                "mode": "live",
                "turn_state": "LISTENING",
                "listening": True,
                "speaking": False,
                "replying": False,
                "interrupted": False,
                "reply_epoch": 0,
            }
        )
        self.live_sessions[session_id] = session
        return session

    def get_live_session(self, session_id):
        return self.live_sessions.get(session_id)

    async def remove_live_session(self, session_id):
        self.removed.append(session_id)
        self.live_sessions.pop(session_id, None)


def _live_app(manager) -> web.Application:
    app = web.Application()
    app["jarvis_manager"] = manager
    setup_live_routes(app)
    return app


async def test_live_start_endpoint():
    manager = FakeManager()
    async with TestServer(_live_app(manager)) as srv, TestClient(srv) as client:
        resp = await client.post("/api/live/start", json={"session_id": "s1"})
        assert resp.status == 200
        data = await resp.json()
        assert data["started"] is True
        assert data["turn_state"] == "LISTENING"
        assert manager.modes == [("s1", "live")]


async def test_live_start_missing_session_id_400():
    manager = FakeManager()
    async with TestServer(_live_app(manager)) as srv, TestClient(srv) as client:
        resp = await client.post("/api/live/start", json={})
        assert resp.status == 400


async def test_live_stop_endpoint():
    manager = FakeManager()
    await manager.create_session("s1", mode="live")
    async with TestServer(_live_app(manager)) as srv, TestClient(srv) as client:
        resp = await client.post("/api/live/stop", json={"session_id": "s1"})
        assert resp.status == 200
        data = await resp.json()
        assert data["stopped"] is True
        assert manager.removed == ["s1"]


async def test_live_status_endpoint():
    manager = FakeManager()
    await manager.create_session("s1", mode="live")
    async with TestServer(_live_app(manager)) as srv, TestClient(srv) as client:
        resp = await client.get("/api/live/status?session_id=s1")
        assert resp.status == 200
        data = await resp.json()
        assert data["exists"] is True
        assert data["turn_state"] == "LISTENING"

        resp2 = await client.get("/api/live/status?session_id=ghost")
        data2 = await resp2.json()
        assert data2["exists"] is False
        assert data2["turn_state"] == "idle"


async def test_live_proactive_endpoint_enable_disable(monkeypatch):
    """POST /api/live/proactive toggles the real session's loop task."""
    from joy_interaction_webui.jarvis_session import LiveSession

    monkeypatch.setenv("LIVE_PROACTIVE_ENABLED", "true")
    monkeypatch.setenv("LIVE_PROACTIVE_INTERVAL_S", "0.01")
    sm, _vad, _asr = build_live()
    session = LiveSession(session_id="s1", state_machine=sm)
    manager = SimpleNamespace(get_live_session=lambda sid: session if sid == "s1" else None)
    async with TestServer(_live_app(manager)) as srv, TestClient(srv) as client:
        resp = await client.post("/api/live/proactive", json={"session_id": "s1", "enabled": True})
        assert resp.status == 200
        data = await resp.json()
        assert data["enabled"] is True
        assert data["applied"] is True
        assert data["supported"] is True
        assert sm._proactive_task is not None

        # Idempotent: repeated enable keeps the same task.
        task_before = sm._proactive_task
        resp2 = await client.post("/api/live/proactive", json={"session_id": "s1", "enabled": True})
        data2 = await resp2.json()
        assert data2["applied"] is True
        assert sm._proactive_task is task_before

        resp3 = await client.post(
            "/api/live/proactive", json={"session_id": "s1", "enabled": False}
        )
        data3 = await resp3.json()
        assert data3["enabled"] is False
        assert data3["applied"] is True
        assert sm._proactive_task is None
    await sm.stop()


async def test_live_proactive_endpoint_env_off_rejected(monkeypatch):
    """Env gate off -> endpoint reports applied=false, supported=false."""
    from joy_interaction_webui.jarvis_session import LiveSession

    monkeypatch.delenv("LIVE_PROACTIVE_ENABLED", raising=False)
    sm, _vad, _asr = build_live()
    session = LiveSession(session_id="s1", state_machine=sm)
    manager = SimpleNamespace(get_live_session=lambda sid: session if sid == "s1" else None)
    async with TestServer(_live_app(manager)) as srv, TestClient(srv) as client:
        resp = await client.post("/api/live/proactive", json={"session_id": "s1", "enabled": True})
        assert resp.status == 200
        data = await resp.json()
        assert data["applied"] is False
        assert data["supported"] is False
        assert sm._proactive_task is None


async def test_live_proactive_endpoint_session_not_found_404():
    manager = SimpleNamespace(get_live_session=lambda sid: None)
    async with TestServer(_live_app(manager)) as srv, TestClient(srv) as client:
        resp = await client.post(
            "/api/live/proactive", json={"session_id": "ghost", "enabled": True}
        )
        assert resp.status == 404
        data = await resp.json()
        assert "live session not found" in data["error"]


async def test_live_proactive_endpoint_missing_session_id_400():
    manager = SimpleNamespace(get_live_session=lambda sid: None)
    async with TestServer(_live_app(manager)) as srv, TestClient(srv) as client:
        resp = await client.post("/api/live/proactive", json={"enabled": True})
        assert resp.status == 400


# ---------------------------------------------------------------------------
# 8. Addressee Detection Phase 1 — env gate / gating / enroll
# ---------------------------------------------------------------------------


def _set_detector(sm, **kwargs) -> FakeDetector:
    det = FakeDetector(**kwargs)
    sm._addressee = det
    return det


def test_addressee_env_gate_default_off_no_detector():
    """JARVIS_ADDRESSEE_DETECTOR_ENABLED defaults off -> no detector, no change."""
    sm, _, _ = build_live()
    assert sm._addressee is None
    assert sm.addressee_enrolled is False


def test_addressee_env_gate_on_creates_detector(monkeypatch):
    import joy_interaction_webui.addressee_detector as add_module

    monkeypatch.setattr(
        add_module,
        "AddresseeDetector",
        lambda *a, **k: FakeDetector(enrolled=False),
    )
    monkeypatch.setenv("JARVIS_ADDRESSEE_DETECTOR_ENABLED", "true")
    sm, _, _ = build_live()
    assert sm._addressee is not None
    assert sm._addressee.available is True


@pytest.mark.asyncio
async def test_addressee_non_target_segment_dropped_no_asr():
    """Enrolled detector + non-target VAD segment -> dropped, ASR zero calls."""
    sm, vad, asr = build_live()
    _set_detector(sm, enrolled=True, target=False, score=0.3)

    # VAD rising edge -> buffer; keep feeding speech chunks.
    vad.set_speech(True)
    for _ in range(6):  # 6 x 2000 bytes = 12000 bytes >= min 9600
        await sm.feed_audio(b"\x00\x00" * 1000)
    assert sm._addressee_in_seg is True

    # Falling edge -> classify -> non-target -> dropped.
    vad.set_speech(False)
    await sm.feed_audio(b"\x00\x00" * 1000)

    assert asr.feed_calls == 0, "non-target segment must never reach ASR"
    assert sm.turn_state == TurnState.LISTENING
    assert sm._addressee_in_seg is False
    assert sm._addressee_seg_buffer == bytearray()


@pytest.mark.asyncio
async def test_addressee_target_segment_released_to_asr():
    """Enrolled detector + target VAD segment -> released to ASR normally."""
    sm, vad, asr = build_live()
    _set_detector(sm, enrolled=True, target=True, score=0.9)
    asr.set_text("你好")

    vad.set_speech(True)
    for _ in range(6):
        await sm.feed_audio(b"\x00\x00" * 1000)
    vad.set_speech(False)
    await sm.feed_audio(b"\x00\x00" * 1000)

    assert asr.feed_calls > 0, "target segment must reach ASR"
    assert sm._current_asr_text == "你好"
    assert sm.turn_state == TurnState.USER_SPEAKING


@pytest.mark.asyncio
async def test_addressee_detector_boom_fail_open_releases():
    """Detector classify raises -> fail-open: segment released (never dropped)."""
    sm, vad, asr = build_live()
    _set_detector(sm, enrolled=True, target=True, boom=True)

    vad.set_speech(True)
    for _ in range(6):
        await sm.feed_audio(b"\x00\x00" * 1000)
    vad.set_speech(False)
    await sm.feed_audio(b"\x00\x00" * 1000)

    assert asr.feed_calls > 0, "classify exception must fail-open (release to ASR)"


@pytest.mark.asyncio
async def test_addressee_not_enrolled_fail_open_passthrough():
    """Detector present but not enrolled -> current streaming behavior (no gate)."""
    sm, vad, asr = build_live()
    _set_detector(sm, enrolled=False)

    vad.set_speech(True)
    await sm.feed_audio(b"\x00\x00" * 1000)
    assert sm.turn_state == TurnState.USER_SPEAKING  # unfiltered
    assert asr.feed_calls == 1


# ---------------------------------------------------------------------------
# 9. Addressee enroll flow (live_mode layer)
# ---------------------------------------------------------------------------


async def _capture_enroll_segment(sm, vad, pcm=b"\x00\x00" * 8000):
    """One VAD speech burst -> falling edge finalizes one enrollment segment."""
    vad.set_speech(True)
    await sm.feed_audio(pcm)  # 8000 bytes = 0.25s per call; repeat for >=1s
    await sm.feed_audio(pcm)
    await sm.feed_audio(pcm)
    await sm.feed_audio(pcm)  # 32000 bytes = 1.0s
    vad.set_speech(False)
    await sm.feed_audio(pcm)


@pytest.mark.asyncio
async def test_addressee_enroll_start_finish_flow():
    sm, vad, _ = build_live()
    det = _set_detector(sm, enrolled=False)

    assert sm.start_enroll() is True
    assert sm.enroll_phase is True

    # 3 utterances -> 3 enrollment segments.
    for _ in range(3):
        await _capture_enroll_segment(sm, vad)
    assert sm.enroll_segment_count == 3

    # finish -> detector.enroll called with the buffered segments.
    assert sm.finish_enroll() is True
    assert sm.enroll_phase is False
    assert det.num_enroll_calls == 1
    assert len(det.enroll_calls[0]) == 3
    assert sm.addressee_enrolled is True


@pytest.mark.asyncio
async def test_addressee_enroll_cancel():
    sm, vad, _ = build_live()
    _set_detector(sm, enrolled=False)

    assert sm.start_enroll() is True
    await _capture_enroll_segment(sm, vad)
    assert sm.enroll_segment_count == 1

    sm.cancel_enroll()
    assert sm.enroll_phase is False
    assert sm.enroll_segment_count == 0


def test_addressee_start_enroll_rejected_no_detector():
    sm, _, _ = build_live()
    assert sm._addressee is None
    assert sm.start_enroll() is False
    assert sm.enroll_phase is False


def test_addressee_finish_enroll_without_start():
    sm, _, _ = build_live()
    _set_detector(sm, enrolled=False)
    assert sm.finish_enroll() is False


# ---------------------------------------------------------------------------
# 10. /api/live/enroll endpoint
# ---------------------------------------------------------------------------


class FakeEnrollSession:
    """Live-session double with the Phase 1 enroll surface (route tests)."""

    def __init__(self, *, detector_available=True) -> None:
        self._detector_available = detector_available
        self._enroll_phase = False
        self._segments: list = []
        self._enrolled = False

    @property
    def enroll_phase(self) -> bool:
        return self._enroll_phase

    @property
    def enroll_segment_count(self) -> int:
        return len(self._segments)

    @property
    def addressee_enrolled(self) -> bool:
        return self._enrolled

    def start_enroll(self) -> bool:
        if not self._detector_available:
            return False
        self._enroll_phase = True
        self._segments = []
        return True

    def feed_enroll_pcm(self, pcm: bytes) -> None:
        if self._enroll_phase:
            self._segments.append(pcm)

    def finish_enroll(self) -> bool:
        if not self._enroll_phase or len(self._segments) < 2:
            return False
        self._enroll_phase = False
        self._enrolled = True
        return True

    def cancel_enroll(self) -> None:
        self._enroll_phase = False
        self._segments = []

    def get_state_for_browser(self) -> dict:
        return {
            "mode": "live",
            "turn_state": "LISTENING",
            "listening": True,
            "speaking": False,
            "replying": False,
            "interrupted": False,
            "reply_epoch": 0,
            "enroll_phase": self._enroll_phase,
            "enroll_segment_count": self.enroll_segment_count,
            "addressee_enrolled": self._enrolled,
        }


class FakeEnrollManager:
    def __init__(self, session=None) -> None:
        self.session = session

    def get_live_session(self, session_id):
        return self.session


def _enroll_app(session) -> web.Application:
    app = web.Application()
    app["jarvis_manager"] = FakeEnrollManager(session)
    setup_live_routes(app)
    return app


async def test_live_enroll_start_finish_endpoint():
    session = FakeEnrollSession()
    async with TestServer(_enroll_app(session)) as srv, TestClient(srv) as client:
        resp = await client.post(
            "/api/live/enroll",
            json={"session_id": "s1", "action": "start"},
        )
        data = await resp.json()
        assert resp.status == 200
        assert data["started"] is True
        assert session.enroll_phase is True


async def test_live_enroll_pcm_and_finish_endpoint():
    session = FakeEnrollSession()
    async with TestServer(_enroll_app(session)) as srv, TestClient(srv) as client:
        await client.post("/api/live/enroll", json={"session_id": "s1", "action": "start"})
        import base64

        pcm = b"\x00\x00" * 4000
        resp = await client.post(
            "/api/live/enroll",
            json={"session_id": "s1", "action": "pcm", "audio_b64": base64.b64encode(pcm).decode()},
        )
        data = await resp.json()
        assert data["buffered"] == 1

        resp = await client.post(
            "/api/live/enroll",
            json={"session_id": "s1", "action": "pcm", "audio_b64": base64.b64encode(pcm).decode()},
        )
        assert (await resp.json())["buffered"] == 2

        resp = await client.post("/api/live/enroll", json={"session_id": "s1", "action": "finish"})
        data = await resp.json()
        assert resp.status == 200
        assert data["enrolled"] is True
        assert session.addressee_enrolled is True


async def test_live_enroll_cancel_and_errors():
    session = FakeEnrollSession(detector_available=False)
    async with TestServer(_enroll_app(session)) as srv, TestClient(srv) as client:
        resp = await client.post(
            "/api/live/enroll",
            json={"session_id": "s1", "action": "start"},
        )
        assert (await resp.json())["started"] is False

    # session not found -> 404
    async with TestServer(_enroll_app(None)) as srv, TestClient(srv) as client:
        resp = await client.post(
            "/api/live/enroll",
            json={"session_id": "ghost", "action": "start"},
        )
        assert resp.status == 404

        resp = await client.post(
            "/api/live/enroll",
            json={"session_id": "ghost", "action": "bogus"},
        )
        assert resp.status == 404  # session check comes before action validation


# ---------------------------------------------------------------------------
# 12. Cloud batch ASR provider (JARVIS_ASR_PROVIDER=cloud, spec §3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cloud_provider_commit_via_finalize(monkeypatch):
    """Cloud (no partials): energy drives onset -> 2s silence -> finalize -> commit."""
    from services.asr.jarvis import asr_provider as ap
    from services.asr.jarvis.asr_provider import CloudBatchProvider

    class _UnavailableVAD:
        """VAD-shaped object with no detector (cloud energy drives onset)."""

        available = False

        def accept_waveform(self, samples):
            pass

        def is_speech(self):
            return True

    clock = FakeClock(1000.0)
    sm, _vad, _asr = build_live(controller=_live_controller(clock), vad=_UnavailableVAD())
    monkeypatch.setattr(live_module, "time", clock)

    provider = CloudBatchProvider(upstream_url="http://upstream/v1/audio/transcriptions")
    sm._asr = provider

    async def fake_transcribe(wav_bytes, **kwargs):
        return "你好世界"

    monkeypatch.setattr(ap, "transcribe_wav_bytes", fake_transcribe)

    commits: list = []
    sm.on_user_utterance = lambda text: commits.append(text)
    monkeypatch.setattr(sm, "_send_to_llm", AsyncMock())

    # 1. Speech-energy chunk -> onset -> USER_SPEAKING, no partials.
    await sm.feed_audio(b"\x00\x10" * 50)
    assert sm.turn_state == TurnState.USER_SPEAKING
    assert sm._current_asr_text == ""

    # 2. User stops -> 2s silence -> cloud endpoint finalizes and commits.
    clock.now += 2.5
    await sm.feed_audio(PCM)  # silence

    assert sm._current_asr_text == ""  # commit reset the ASR stream
    assert commits == ["你好世界"]
    assert provider._pcm == bytearray()  # buffer consumed by finalize


@pytest.mark.asyncio
async def test_cloud_provider_unreachable_live_explicit_error(monkeypatch, caplog):
    """D-080: cloud unreachable in live -> explicit error, no commit, no fallback."""
    from services.asr.jarvis import asr_provider as ap
    from services.asr.jarvis.asr_provider import CloudBatchProvider

    class _UnavailableVAD:
        available = False

        def accept_waveform(self, samples):
            pass

        def is_speech(self):
            return True

    monkeypatch.delenv("JARVIS_ASR_ALLOW_LOCAL_FAILOVER", raising=False)
    clock = FakeClock(1000.0)
    sm, _vad, _asr = build_live(controller=_live_controller(clock), vad=_UnavailableVAD())
    monkeypatch.setattr(live_module, "time", clock)

    provider = CloudBatchProvider(upstream_url="http://upstream/v1/audio/transcriptions")
    sm._asr = provider

    async def boom(wav_bytes, **kwargs):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(ap, "transcribe_wav_bytes", boom)

    commits: list = []
    sm.on_user_utterance = lambda text: commits.append(text)
    monkeypatch.setattr(sm, "_send_to_llm", AsyncMock())

    with caplog.at_level(logging.ERROR, logger="joyai.live_mode"):
        await sm.feed_audio(b"\x00\x10" * 50)
        clock.now += 2.5
        await sm.feed_audio(PCM)

    assert commits == []
    assert sm._asr is provider  # still cloud (no silent local fallback)
    assert any("[asr] cloud provider unreachable" in r.getMessage() for r in caplog.records)
