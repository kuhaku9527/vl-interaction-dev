"""QA 独立补充边界用例 — layer 2 proactive/frame-buffer (spec draft-live-visual-cb.md §3 层 2).

These tests pin boundary contracts NOT explicitly covered by the engineer's
``test_live_proactive.py``:

  * ``on_agent_turn_started`` — LISTENING -> THINKING (agent-initiated turn
    entry); non-LISTENING call is ignored with a log (fail-open, never raises);
  * proactive loop — two consecutive responses are separated by at least one
    interval (interval gating); a VLM-call failure inside a round fails open
    to silence (prompt-level fail-open);
  * frame ring buffer — the 7th frame evicts the 1st (window rotation);
  * env default OFF — after ``prewarm_engines`` the proactive task stays None
    (already asserted at build time; this pins the post-prewarm invariant).

Run: python -m pytest tests/test_qa_live_proactive_boundary.py -q
"""

from __future__ import annotations

import asyncio
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
    TurnConfig,
    TurnController,
    TurnState,
)
from joy_interaction_webui.turn_streaming import StreamingTurnResult  # noqa: E402

PCM = b"\x00\x00" * 100
B64 = "LzlqL0FBQUFBQUFBQUFGRG1GcmFtZS1qcGVn"


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
    def __init__(self, result: StreamingTurnResult, sentences=()) -> None:
        self.result = result
        self.sentences = list(sentences)
        self.consume_kwargs: dict = {}

    def __call__(self, **kwargs):
        self.consume_kwargs = dict(kwargs)
        return self

    async def consume(self, text, interaction_mode, reply_session):
        self.consume_kwargs.update(
            {"text": text, "interaction_mode": interaction_mode, "reply_session": reply_session}
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
    cfg = TurnConfig.live()
    cfg.cooldown_ms = cooldown_ms
    return TurnController(cfg, clock=clock)


# ---------------------------------------------------------------------------
# 1. turn_controller.on_agent_turn_started — pure-increment boundary
# ---------------------------------------------------------------------------


def test_on_agent_turn_started_listening_to_thinking():
    ctrl = _live_controller()
    assert ctrl.state == TurnState.LISTENING
    ctrl.on_agent_turn_started()
    assert ctrl.state == TurnState.THINKING
    # A subsequent on_tts_started completes the agent-initiated turn entry
    # (THINKING -> SPEAKING) so barge-in behaves like a user-initiated turn.
    ctrl.on_tts_started()
    assert ctrl.state == TurnState.SPEAKING


def test_on_agent_turn_started_non_listening_ignored_no_raise(caplog):
    """Non-LISTENING call must NOT raise; it logs and is ignored (fail-open)."""
    ctrl = _live_controller()
    ctrl.on_agent_turn_started()  # -> THINKING
    assert ctrl.state == TurnState.THINKING
    with caplog.at_level("INFO", logger="joyai.turn_controller"):
        ctrl.on_agent_turn_started()  # called again while THINKING
    assert ctrl.state == TurnState.THINKING  # unchanged
    assert "agent turn started ignored" in caplog.text


def test_on_agent_turn_started_from_speaking_ignored(caplog):
    ctrl = _live_controller()
    ctrl.on_agent_turn_started()
    ctrl.on_tts_started()  # -> SPEAKING
    assert ctrl.state == TurnState.SPEAKING
    with caplog.at_level("INFO", logger="joyai.turn_controller"):
        ctrl.on_agent_turn_started()
    assert ctrl.state == TurnState.SPEAKING  # unchanged
    assert "agent turn started ignored" in caplog.text


# ---------------------------------------------------------------------------
# 2. Frame ring buffer: 7th frame evicts the 1st
# ---------------------------------------------------------------------------


def test_frame_buffer_seventh_evicts_first():
    sm, _vad, _asr = build_live()
    for i in range(7):
        sm.handle_frame(f"b64-{i}", float(i * 1000))
    assert len(sm.recent_frames) == 6
    assert sm.recent_frames[0] == ("b64-1", 1000.0)  # b64-0 evicted
    assert sm.recent_frames[-1] == ("b64-6", 6000.0)


# ---------------------------------------------------------------------------
# 3. Proactive loop: interval gating between consecutive responses
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_proactive_loop_two_responses_interval_gated(monkeypatch):
    """Two consecutive proactive responses must be separated by >= interval."""
    from itertools import pairwise

    monkeypatch.setenv("LIVE_PROACTIVE_ENABLED", "true")
    monkeypatch.setenv("LIVE_PROACTIVE_INTERVAL_S", "0.05")
    sm, _vad, _asr = build_live()
    sm.handle_frame(B64, 1000.0)
    timestamps: list[float] = []

    real_time = live_module.time

    async def fake_proactive(*, frames):
        timestamps.append(real_time.time())

    monkeypatch.setattr(sm, "_send_proactive_prompt", fake_proactive)
    await sm.prewarm_engines()
    await asyncio.sleep(0.25)  # ~5 intervals worth
    assert len(timestamps) >= 2
    # Each pair of consecutive calls is gated by the configured interval.
    gaps = [b - a for a, b in pairwise(timestamps)]
    assert all(gap >= 0.04 for gap in gaps)  # allow scheduler slop
    await sm.stop()


@pytest.mark.asyncio
async def test_proactive_vlm_failure_fails_open_to_silence(monkeypatch):
    """A VLM-call exception inside a proactive round must fail open: no speech,
    controller stays LISTENING, the loop keeps running."""
    monkeypatch.setenv("LIVE_PROACTIVE_ENABLED", "true")
    monkeypatch.setenv("LIVE_PROACTIVE_INTERVAL_S", "0.01")
    sm, _vad, _asr = build_live()
    sm.handle_frame(B64, 1000.0)
    pushes: list = []

    async def boom(frames):
        raise RuntimeError("webinfer down")

    sm.on_tts_sentence = lambda *args: pushes.append(args)
    monkeypatch.setattr(sm, "_call_proactive_vlm", boom)
    await sm.prewarm_engines()
    await asyncio.sleep(0.06)
    # fail-open: nothing spoken, controller still listening, loop alive.
    assert pushes == []
    assert sm.turn_state == TurnState.LISTENING
    assert sm._proactive_task is not None and not sm._proactive_task.done()
    await sm.stop()


@pytest.mark.asyncio
async def test_proactive_does_not_speak_over_user_speech(monkeypatch):
    """Spec §2.4/§3: 用户语音出现 (USER_SPEAKING) -> 循环暂停 (等回 LISTENING).

    Regression: the loop-level LISTENING gate is checked between rounds, but a
    proactive round whose VLM call is in flight when the user starts speaking
    must NOT then speak over the user once the VLM returns. If the controller
    is no longer LISTENING at decision time, the round must stay silent
    (fail-open — proactive work never disturbs the user dialog).
    """
    sm, _vad, _asr = build_live()
    sm.handle_frame(B64, 1000.0)
    pushes: list = []
    sm.on_tts_sentence = lambda *args: pushes.append(args)

    async def fake_vlm(frames):
        return "response", "画面里出现了 Boss！"

    async def fake_tts(text):
        return b"\x00\x00" * 100

    monkeypatch.setattr(sm, "_call_proactive_vlm", fake_vlm)
    monkeypatch.setattr(sm, "_fetch_tts_pcm", fake_tts)

    # The user starts speaking while the proactive VLM round is in flight.
    sm._ctrl.on_speech_started(0.9)  # LISTENING -> USER_SPEAKING
    assert sm.turn_state == TurnState.USER_SPEAKING

    await sm._send_proactive_prompt(frames=sm._frames_payload([sm.recent_frames[-1]]))
    await asyncio.gather(*list(sm._tts_sentence_tasks), return_exceptions=True)

    # Desired behavior: no TTS audio is pushed while the user is speaking.
    assert pushes == []
    assert sm.turn_state == TurnState.USER_SPEAKING
    await sm.stop()


# ---------------------------------------------------------------------------
# 4. Env default OFF: no proactive task after prewarm
# ---------------------------------------------------------------------------


def test_proactive_disabled_no_task_after_prewarm(monkeypatch):
    monkeypatch.delenv("LIVE_PROACTIVE_ENABLED", raising=False)
    sm, _vad, _asr = build_live()
    asyncio.run(sm.prewarm_engines())
    assert sm._proactive_task is None
    assert sm._proactive_enabled is False
