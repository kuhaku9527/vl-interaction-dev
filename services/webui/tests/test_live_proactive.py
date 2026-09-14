"""Live visual context + proactive speak tests (spec live-visual-cb.md §3 层 2).

Layer 2 adds to ``LiveStateMachine``:

  * ``recent_frames`` ring buffer (``LIVE_FRAME_WINDOW``, default 6) fed by
    ``handle_frame(image_b64, ts_ms)`` with ``[live-mode] frame captured`` log;
  * user rounds carry the buffered frames into the streaming LLM round
    (``_send_to_llm(..., frames=...)`` -> StreamingTurnConsumer frames);
  * the proactive loop (``LIVE_PROACTIVE_ENABLED`` env gate, default OFF) —
    every ``LIVE_PROACTIVE_INTERVAL_S`` seconds while LISTENING, sample the
    latest frame, ask webinfer (non-streaming, no user text), and speak only
    on decision=response; silence / not-for-me stay quiet; non-LISTENING
    states pause the loop; barge-in reuses the existing HARD_INTERRUPTED
    path; exceptions fail open;
  * ``LiveSession.handle_frame`` passthrough (WS ``frame`` routing).

Env default OFF must produce zero behavior change (no proactive task).

Run: python -m pytest tests/test_live_proactive.py -q
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
WEBUI_SRC = REPO / "services" / "webui" / "src"
for _p in (str(REPO), str(WEBUI_SRC)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402

from joy_interaction_webui import live_mode as live_module  # noqa: E402
from joy_interaction_webui.jarvis_mode import JarvisConfig  # noqa: E402
from joy_interaction_webui.live_mode import LiveStateMachine  # noqa: E402
from joy_interaction_webui.turn_controller import (  # noqa: E402
    TurnController,
    TurnState,
)
from joy_interaction_webui.turn_streaming import StreamingTurnResult  # noqa: E402

PCM = b"\x00\x00" * 100  # 100ms of 16 kHz mono silence (int16)

#: A valid base64 JPEG payload (syntactically only — never decoded here).
B64 = "LzlqL0FBQUFBQUFBQUFGRG1GcmFtZS1qcGVn"


# ---------------------------------------------------------------------------
# Test doubles (mirror test_live_mode.py)
# ---------------------------------------------------------------------------


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def time(self) -> float:
        return self.now

    def monotonic(self) -> float:
        return self.now

    def __call__(self) -> float:
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


def build_live(*, controller: TurnController | None = None, **overrides):
    """Build a LiveStateMachine with controllable fake VAD / ASR."""
    vad = FakeVAD()
    asr = FakeASR()
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
    from joy_interaction_webui.turn_controller import TurnConfig

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
# 1. Frame buffer window / rotation
# ---------------------------------------------------------------------------


def test_frame_buffer_window_rotation():
    sm, _vad, _asr = build_live()
    for i in range(10):
        sm.handle_frame(f"b64-{i}", float(i * 1000))
    # Default LIVE_FRAME_WINDOW=6: oldest 4 dropped, newest 6 kept.
    assert len(sm.recent_frames) == 6
    assert sm.recent_frames[0] == ("b64-4", 4000.0)
    assert sm.recent_frames[-1] == ("b64-9", 9000.0)


def test_frame_buffer_captured_log(caplog):
    sm, _vad, _asr = build_live()
    with caplog.at_level(logging.INFO, logger="joyai.live_mode"):
        sm.handle_frame("b64-0", 1000.0)
        sm.handle_frame("b64-1", 3000.0)
    assert "[live-mode] frame captured (n=2, window=2.0s)" in caplog.text


def test_frame_buffer_env_window_override(monkeypatch):
    monkeypatch.setenv("LIVE_FRAME_WINDOW", "3")
    sm, _vad, _asr = build_live()
    for i in range(5):
        sm.handle_frame(f"b64-{i}", float(i))
    assert len(sm.recent_frames) == 3
    assert sm.recent_frames[0] == ("b64-2", 2.0)


# ---------------------------------------------------------------------------
# 2. User round carries buffered frames into the streaming LLM round
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_round_carries_frames(monkeypatch):
    clock = FakeClock(1000.0)
    sm, vad, asr = build_live(controller=_live_controller(clock))
    monkeypatch.setattr(live_module, "time", clock)
    for i in range(3):
        sm.handle_frame(f"b64-{i}", float(i * 1000))

    fake = FakeConsumer(_result(full_response="好的。", sentence_count=0), sentences=[])
    monkeypatch.setattr(live_module, "StreamingTurnConsumer", fake)

    vad.set_speech(True)
    await sm.feed_audio(PCM)
    clock.now += 0.5
    asr.set_text("你好")
    await sm.feed_audio(PCM)
    vad.set_speech(False)
    clock.now += 2.0
    await sm.feed_audio(PCM)

    assert fake.consume_kwargs["text"] == "你好"
    assert fake.consume_kwargs["frames"] == [
        {"image_b64": "b64-0", "ts_ms": 0.0},
        {"image_b64": "b64-1", "ts_ms": 1000.0},
        {"image_b64": "b64-2", "ts_ms": 2000.0},
    ]


def test_frames_payload_wire_format():
    """Internal (image_b64, ts_ms) tuples -> webinfer dict wire format."""
    sm, _vad, _asr = build_live()
    payload = sm._frames_payload([("b64-a", 100.0), ("b64-b", 200.0)])
    assert payload == [
        {"image_b64": "b64-a", "ts_ms": 100.0},
        {"image_b64": "b64-b", "ts_ms": 200.0},
    ]


# ---------------------------------------------------------------------------
# 3. Proactive prompt: response -> speak; silence / not-for-me -> quiet
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_proactive_response_speaks_then_listening(monkeypatch):
    sm, _vad, _asr = build_live()
    sm.handle_frame(B64, 1000.0)
    pushes: list = []
    broadcasts: list = []
    sm.on_tts_sentence = lambda text, seq, audio_b64, session: pushes.append((seq, text))
    sm.on_llm_response = lambda text, source: broadcasts.append((text, source))

    async def fake_vlm(frames):
        assert len(frames) == 1
        assert frames[0]["image_b64"] == B64
        assert frames[0]["ts_ms"] == 1000.0
        return "response", "画面里出现了 Boss！"

    async def fake_tts(text):
        return b"\x00\x00" * 100

    monkeypatch.setattr(sm, "_call_proactive_vlm", fake_vlm)
    monkeypatch.setattr(sm, "_fetch_tts_pcm", fake_tts)

    await sm._send_proactive_prompt(frames=sm._frames_payload([sm.recent_frames[-1]]))
    assert sm.turn_state == TurnState.SPEAKING
    assert broadcasts == [("画面里出现了 Boss！", "live_proactive")]

    await asyncio.gather(*list(sm._tts_sentence_tasks), return_exceptions=True)
    assert pushes == [(0, "画面里出现了 Boss！")]
    if sm._tts_turn_task is not None:
        await sm._tts_turn_task
    assert sm.turn_state == TurnState.LISTENING


@pytest.mark.asyncio
async def test_proactive_silence_stays_quiet(monkeypatch):
    sm, _vad, _asr = build_live()
    sm.handle_frame(B64, 1000.0)
    pushes: list = []
    sm.on_tts_sentence = lambda *args: pushes.append(args)

    async def fake_vlm(frames):
        return "silence", ""

    monkeypatch.setattr(sm, "_call_proactive_vlm", fake_vlm)

    await sm._send_proactive_prompt(frames=sm._frames_payload([sm.recent_frames[-1]]))
    assert pushes == []
    assert sm.turn_state == TurnState.LISTENING


@pytest.mark.asyncio
async def test_proactive_not_for_me_stays_quiet(monkeypatch):
    sm, _vad, _asr = build_live()
    sm.handle_frame(B64, 1000.0)
    pushes: list = []
    sm.on_tts_sentence = lambda *args: pushes.append(args)

    async def fake_vlm(frames):
        return "not-for-me", ""

    monkeypatch.setattr(sm, "_call_proactive_vlm", fake_vlm)

    await sm._send_proactive_prompt(frames=sm._frames_payload([sm.recent_frames[-1]]))
    assert pushes == []
    assert sm.turn_state == TurnState.LISTENING


@pytest.mark.asyncio
async def test_proactive_empty_response_stays_quiet(monkeypatch):
    sm, _vad, _asr = build_live()
    sm.handle_frame(B64, 1000.0)
    pushes: list = []
    sm.on_tts_sentence = lambda *args: pushes.append(args)

    async def fake_vlm(frames):
        return "response", "   "

    monkeypatch.setattr(sm, "_call_proactive_vlm", fake_vlm)

    await sm._send_proactive_prompt(frames=sm._frames_payload([sm.recent_frames[-1]]))
    assert pushes == []
    assert sm.turn_state == TurnState.LISTENING


# ---------------------------------------------------------------------------
# 4. Proactive loop gating: LISTENING-only, pause on USER_SPEAKING, resume
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_proactive_loop_fires_only_in_listening(monkeypatch):
    monkeypatch.setenv("LIVE_PROACTIVE_ENABLED", "true")
    monkeypatch.setenv("LIVE_PROACTIVE_INTERVAL_S", "0.01")
    sm, _vad, _asr = build_live()
    sm.handle_frame(B64, 1000.0)
    calls: list = []

    async def fake_proactive(*, frames):
        calls.append(frames)

    monkeypatch.setattr(sm, "_send_proactive_prompt", fake_proactive)
    await sm.prewarm_engines()

    # LISTENING -> loop fires.
    await asyncio.sleep(0.05)
    assert len(calls) >= 1
    assert calls[0][0]["image_b64"] == B64  # latest frame sampled (wire dict)

    # USER_SPEAKING -> loop pauses.
    sm._ctrl.on_speech_started(0.9)
    assert sm.turn_state == TurnState.USER_SPEAKING
    n_before = len(calls)
    await asyncio.sleep(0.05)
    assert len(calls) == n_before  # paused

    # Back to LISTENING -> loop resumes.
    sm._ctrl.on_speech_stopped(600)
    sm._commit_pending = False
    sm._realign_controller_to_listening()
    assert sm.turn_state == TurnState.LISTENING
    await asyncio.sleep(0.05)
    assert len(calls) > n_before  # resumed

    await sm.stop()


@pytest.mark.asyncio
async def test_proactive_loop_no_frames_skips(monkeypatch):
    monkeypatch.setenv("LIVE_PROACTIVE_ENABLED", "true")
    monkeypatch.setenv("LIVE_PROACTIVE_INTERVAL_S", "0.01")
    sm, _vad, _asr = build_live()  # no frames buffered
    calls: list = []

    async def fake_proactive(*, frames):
        calls.append(frames)

    monkeypatch.setattr(sm, "_send_proactive_prompt", fake_proactive)
    await sm.prewarm_engines()
    await asyncio.sleep(0.05)
    assert calls == []  # no frames -> never asks the VLM
    await sm.stop()


@pytest.mark.asyncio
async def test_proactive_loop_fail_open_on_round_error(monkeypatch):
    monkeypatch.setenv("LIVE_PROACTIVE_ENABLED", "true")
    monkeypatch.setenv("LIVE_PROACTIVE_INTERVAL_S", "0.01")
    sm, _vad, _asr = build_live()
    sm.handle_frame(B64, 1000.0)
    calls: list = []

    async def boom(*, frames):
        calls.append(1)
        raise RuntimeError("vlm boom")

    monkeypatch.setattr(sm, "_send_proactive_prompt", boom)
    await sm.prewarm_engines()
    await asyncio.sleep(0.05)
    assert len(calls) >= 2  # the loop survived the failure and kept going
    await sm.stop()


# ---------------------------------------------------------------------------
# 5. Barge-in during proactive speech (existing HARD_INTERRUPTED path)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_proactive_speech_barge_in(monkeypatch):
    clock = FakeClock(1000.0)
    sm, vad, asr = build_live(controller=_live_controller(clock, cooldown_ms=10))
    monkeypatch.setattr(live_module, "time", clock)
    sm.handle_frame(B64, 1000.0)

    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_tts(text):
        started.set()
        await release.wait()
        return b"\x00\x00" * 100

    async def fake_vlm(frames):
        return "response", "画面里有 Boss！"

    monkeypatch.setattr(sm, "_fetch_tts_pcm", slow_tts)
    monkeypatch.setattr(sm, "_call_proactive_vlm", fake_vlm)

    await sm._send_proactive_prompt(frames=sm._frames_payload([sm.recent_frames[-1]]))
    assert sm.turn_state == TurnState.SPEAKING
    await started.wait()

    # User speaks over the proactive reply -> HARD_INTERRUPTED + epoch bump.
    vad.set_speech(True)
    await sm.feed_audio(PCM)
    assert sm.turn_state == TurnState.HARD_INTERRUPTED
    assert sm._tts_sentence_epoch == 1
    release.set()
    await asyncio.gather(*list(sm._tts_sentence_tasks), return_exceptions=True)
    assert sm._tts_sentence_tasks == set()

    # User stops -> ASR endpoint -> COOLDOWN -> LISTENING (proactive loop
    # would resume on the next interval).
    asr.set_text("打断")
    sm._current_asr_text = "打断"
    sm._last_speech_time = clock.now - 2.5
    vad.set_speech(False)
    await sm.feed_audio(PCM)
    assert sm.turn_state == TurnState.COOLDOWN
    if sm._cooldown_task is not None:
        await sm._cooldown_task
    assert sm.turn_state == TurnState.LISTENING
    await sm.stop()


# ---------------------------------------------------------------------------
# 6. Env default OFF -> zero behavior change (no proactive task)
# ---------------------------------------------------------------------------


def test_proactive_disabled_by_default(monkeypatch):
    monkeypatch.delenv("LIVE_PROACTIVE_ENABLED", raising=False)
    sm, _vad, _asr = build_live()
    assert sm._proactive_enabled is False
    assert sm._proactive_task is None
    asyncio.run(sm.prewarm_engines())
    assert sm._proactive_task is None  # task never started


def test_proactive_interval_env_override(monkeypatch):
    monkeypatch.setenv("LIVE_PROACTIVE_INTERVAL_S", "2.5")
    sm, _vad, _asr = build_live()
    assert sm._proactive_interval_s == 2.5


# ---------------------------------------------------------------------------
# 7. LiveSession passthrough (WS frame routing)
# ---------------------------------------------------------------------------


def test_live_session_handle_frame_passthrough():
    from joy_interaction_webui.jarvis_session import LiveSession

    sm, _vad, _asr = build_live()
    session = LiveSession(session_id="s1", state_machine=sm)
    session.handle_frame(B64, 1234.0)
    assert sm.recent_frames == [(B64, 1234.0)]


# ---------------------------------------------------------------------------
# 8. set_proactive runtime switch (C.B layer 3)
# ---------------------------------------------------------------------------


def test_set_proactive_env_off_rejected(monkeypatch):
    """Env gate LIVE_PROACTIVE_ENABLED off -> runtime enable is rejected."""
    monkeypatch.delenv("LIVE_PROACTIVE_ENABLED", raising=False)
    sm, _vad, _asr = build_live()
    assert sm._proactive_enabled is False

    async def _try_enable():
        return sm.set_proactive(True)

    ok = asyncio.run(_try_enable())
    assert ok is False
    assert sm._proactive_task is None


@pytest.mark.asyncio
async def test_set_proactive_idempotent_enable(monkeypatch):
    """Repeated True never creates a second loop task."""
    monkeypatch.setenv("LIVE_PROACTIVE_ENABLED", "true")
    monkeypatch.setenv("LIVE_PROACTIVE_INTERVAL_S", "0.01")
    sm, _vad, _asr = build_live()
    assert sm._proactive_task is None

    ok1 = sm.set_proactive(True)
    await asyncio.sleep(0)  # let the loop task actually start
    task1 = sm._proactive_task
    ok2 = sm.set_proactive(True)
    await asyncio.sleep(0)
    assert ok1 is True
    assert ok2 is True
    assert task1 is not None
    assert task1 is sm._proactive_task  # idempotent: same task, no double-create
    await sm.stop()


@pytest.mark.asyncio
async def test_set_proactive_idempotent_disable(monkeypatch):
    """Repeated False never double-cancels; state ends off."""
    monkeypatch.setenv("LIVE_PROACTIVE_ENABLED", "true")
    monkeypatch.setenv("LIVE_PROACTIVE_INTERVAL_S", "0.01")
    sm, _vad, _asr = build_live()

    assert sm.set_proactive(True) is True
    await asyncio.sleep(0)  # let the loop task start (no never-awaited warning)
    assert sm._proactive_task is not None

    assert sm.set_proactive(False) is True
    await asyncio.sleep(0)
    assert sm._proactive_task is None

    assert sm.set_proactive(False) is True
    await asyncio.sleep(0)
    assert sm._proactive_task is None
    await sm.stop()


@pytest.mark.asyncio
async def test_set_proactive_enable_runs_loop(monkeypatch):
    """Runtime enable actually starts the proactive loop."""
    monkeypatch.setenv("LIVE_PROACTIVE_ENABLED", "true")
    monkeypatch.setenv("LIVE_PROACTIVE_INTERVAL_S", "0.01")
    sm, _vad, _asr = build_live()
    sm.handle_frame(B64, 1000.0)
    calls: list = []

    async def fake_proactive(*, frames):
        calls.append(frames)

    monkeypatch.setattr(sm, "_send_proactive_prompt", fake_proactive)
    assert sm.set_proactive(True) is True
    await asyncio.sleep(0.05)
    assert len(calls) >= 1  # loop is really running
    await sm.stop()


@pytest.mark.asyncio
async def test_set_proactive_state_reported_for_browser(monkeypatch):
    """get_state_for_browser exposes proactive_supported / proactive_enabled."""
    monkeypatch.setenv("LIVE_PROACTIVE_ENABLED", "true")
    sm, _vad, _asr = build_live()
    state = sm.get_state_for_browser()
    assert state["proactive_supported"] is True
    assert state["proactive_enabled"] is False

    assert sm.set_proactive(True) is True
    await asyncio.sleep(0)
    state2 = sm.get_state_for_browser()
    assert state2["proactive_supported"] is True
    assert state2["proactive_enabled"] is True

    await sm.stop()
    state3 = sm.get_state_for_browser()
    assert state3["proactive_enabled"] is False
