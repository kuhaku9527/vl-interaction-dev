"""Phase B: jarvis DIALOG_ACTIVE turn-delegate tests.

Verifies that enabling the turn-controller delegate
(``JARVIS_TURN_DELEGATE_ENABLED=1``) does NOT change jarvis's observable
behavior (wake chain / exit words / interrupt timing / LLM call path), and
that any turn_controller failure falls back to the legacy dialog logic
(fail-open).

Coverage:
  1. env gate: delegate off by default; on via env; independent of shadow
  2. behavior consistency (delegate ON vs OFF): normal commit, exit words,
     interrupt timing
  3. controller arbitration wiring: on_dialog_enter alignment, commit cycle
     returns to LISTENING, controller-driven barge-in -> existing TTS pause
  4. fail-open: controller error -> delegate disabled + legacy fallback

NOTE: this environment has no pytest-asyncio, so every test is a sync
function and async code is driven through ``asyncio.run``.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from collections import deque
from pathlib import Path
from unittest.mock import AsyncMock, patch

REPO = Path(__file__).resolve().parents[3]
WEBUI_SRC = REPO / "services" / "webui" / "src"
for _p in (str(REPO), str(WEBUI_SRC)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from joy_interaction_webui.turn_controller import (  # noqa: E402
    TurnConfig,
    TurnController,
    TurnState,
    TurnStateError,
)
from joy_interaction_webui.turn_controller_delegate import (  # noqa: E402
    TurnControllerDelegate,
)


def _jarvis_mode():
    """Import jarvis_mode lazily.

    ``test_jarvis_config_env.py`` calls ``importlib.reload`` on the module, so
    module-level class/enum bindings can go stale across files (the reloaded
    ``JarvisState`` enum is a different object and no longer compares equal).
    Following the repo convention, jarvis-mode symbols are imported inside
    functions so each call sees the live module.
    """
    from joy_interaction_webui.jarvis_mode import (  # lazy by design (see docstring)
        JarvisConfig,
        JarvisState,
        JarvisStateMachine,
    )

    return JarvisConfig, JarvisState, JarvisStateMachine


class FakeClock:
    """Deterministic monotonic clock in seconds; advance with ``advance(ms)``."""

    def __init__(self, start_s: float = 1000.0) -> None:
        self.t = start_s

    def __call__(self) -> float:
        return self.t

    def advance(self, ms: float) -> None:
        self.t += ms / 1000.0


class FakeKWS:
    """KWS stub that fires on a chosen call index."""

    def __init__(self, fires_on_call: int = 1):
        self.calls = 0
        self.fires_on_call = fires_on_call

    def start(self):
        pass

    def feed_audio(self, pcm: bytes) -> bool:
        self.calls += 1
        return self.calls == self.fires_on_call


class FakeASR:
    """ASR stub for the wake path: cycles partials, then repeats last / empty."""

    def __init__(self, partials, finals=()):
        self.partials = list(partials)
        self.finals = list(finals)
        self.started = False
        self.stopped = False
        self.chunks = 0
        self._i = 0

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def feed_chunk(self, pcm: bytes) -> str:
        self.chunks += 1
        if self._i < len(self.partials):
            text = self.partials[self._i]
            self._i += 1
            return text
        if self.finals:
            return self.finals[-1]
        return ""


class ScriptedASR:
    """Dialog ASR stub: replays a fixed sequence, then holds the last text.

    Mirrors the streaming paraformer behavior of holding the last partial on
    silence frames (stale text), which is what drives the 2s endpoint rule.
    """

    def __init__(self, texts):
        self.texts = list(texts)
        self.fed = 0
        self.start_count = 0
        self.stop_count = 0

    def start(self):
        self.start_count += 1

    def stop(self):
        self.stop_count += 1

    def feed_chunk(self, pcm: bytes) -> str:
        self.fed += 1
        if not self.texts:
            return ""
        idx = min(self.fed - 1, len(self.texts) - 1)
        return self.texts[idx]


def _make_delegate(clock: FakeClock | None = None) -> TurnControllerDelegate:
    ctrl = TurnController(TurnConfig.jarvis(), clock=clock or FakeClock())
    return TurnControllerDelegate(ctrl)


def _make_sm(*, with_delegate: bool = True):
    """Build a DIALOG_ACTIVE state machine via ``__new__`` (no engine init).

    When ``with_delegate`` is True a turn-controller delegate (jarvis preset,
    fake clock) is attached and driven to LISTENING, as jarvis does when it
    enters DIALOG_ACTIVE. Returns ``(sm, clock)``.
    """
    JarvisConfig, JarvisState, JarvisStateMachine = _jarvis_mode()
    cfg = JarvisConfig(llm_streaming_enabled=False)  # P0-A: delegate suite pins single-shot
    sm = JarvisStateMachine.__new__(JarvisStateMachine)
    sm.config = cfg
    sm.state = JarvisState.DIALOG_ACTIVE
    sm._asr_stream_active = True
    sm._current_asr_text = ""
    sm._last_speech_time = 0.0
    sm._tts_task = None
    sm._audio_queue = asyncio.Queue(maxsize=4)
    sm._confirm_task = None
    sm._kws = FakeKWS()
    sm._conv_history = deque(maxlen=20)
    sm._max_history_turns = 10
    sm.on_wake = None
    sm.on_asr_partial = None
    sm.on_user_utterance = None
    sm.on_llm_response = None
    sm.on_goodbye = None
    sm.audio_output = None
    clock = FakeClock()
    if with_delegate:
        sm._turn_delegate = _make_delegate(clock)
        sm._turn_delegate.on_dialog_enter()
    else:
        sm._turn_delegate = None
    return sm, clock


def _make_send_stub(calls: list):
    async def fake_send(text, *, stream_tts=True, interaction_mode="jarvis"):
        calls.append(text)

    return fake_send


# ---------------------------------------------------------------------------
# 1. Env gate
# ---------------------------------------------------------------------------


def test_delegate_disabled_by_default(monkeypatch):
    monkeypatch.delenv("JARVIS_TURN_DELEGATE_ENABLED", raising=False)
    _JarvisConfig, _JarvisState, JarvisStateMachine = _jarvis_mode()
    sm = JarvisStateMachine(config=_JarvisConfig())
    assert sm._turn_delegate is None


def test_delegate_enabled_by_env(monkeypatch):
    monkeypatch.setenv("JARVIS_TURN_DELEGATE_ENABLED", "1")
    _JarvisConfig, _JarvisState, JarvisStateMachine = _jarvis_mode()
    sm = JarvisStateMachine(config=_JarvisConfig())
    assert isinstance(sm._turn_delegate, TurnControllerDelegate)
    assert sm._turn_delegate.controller.config.scenario == "jarvis"


def test_shadow_and_delegate_gates_independent(monkeypatch):
    monkeypatch.setenv("JARVIS_TURN_SHADOW_ENABLED", "1")
    monkeypatch.delenv("JARVIS_TURN_DELEGATE_ENABLED", raising=False)
    _JarvisConfig, _JarvisState, JarvisStateMachine = _jarvis_mode()
    sm = JarvisStateMachine(config=_JarvisConfig())
    assert sm._turn_shadow is not None
    assert sm._turn_delegate is None


# ---------------------------------------------------------------------------
# 2. Behavior consistency: delegate ON vs OFF
# ---------------------------------------------------------------------------


def _normal_turn_outcome(with_delegate: bool):
    """Drive one normal turn (partial -> 2s staleness -> commit)."""
    calls = []
    sm, clock = _make_sm(with_delegate=with_delegate)
    sm._asr = ScriptedASR(["你好"])

    async def fake_send(text, *, stream_tts=True, interaction_mode="jarvis"):
        calls.append(text)

    sm._send_to_llm = fake_send
    asyncio.run(sm._handle_dialog(b"\x00\x00" * 80))  # partial
    sm._last_speech_time = time.time() - 2.5  # simulate 2s staleness
    if with_delegate:
        clock.advance(2000)  # controller sees >= min_utterance_ms elapsed
    asyncio.run(sm._handle_dialog(b"\x00\x00" * 80))  # stale -> endpoint
    return sm.state.name, list(calls), sm._current_asr_text


def test_normal_turn_identical_with_and_without_delegate():
    off = _normal_turn_outcome(with_delegate=False)
    on = _normal_turn_outcome(with_delegate=True)
    assert off == on == ("DIALOG_ACTIVE", ["你好"], "")


def _exit_word_outcome(with_delegate: bool):
    """Drive one exit word ("好的") from DIALOG_ACTIVE."""
    sm, _clock = _make_sm(with_delegate=with_delegate)
    sm._asr = ScriptedASR(["好的"])
    goodbye_calls = []

    async def fake_goodbye():
        goodbye_calls.append(1)

    sm._play_goodbye_wav = fake_goodbye
    asyncio.run(sm._handle_dialog(b"\x00\x00" * 80))
    return sm, len(goodbye_calls)


def test_exit_words_identical_with_and_without_delegate():
    sm_off, goodbye_off = _exit_word_outcome(with_delegate=False)
    sm_on, goodbye_on = _exit_word_outcome(with_delegate=True)
    assert (
        (sm_off.state.name, goodbye_off)
        == (sm_on.state.name, goodbye_on)
        == (
            "KWS_LISTENING",
            1,
        )
    )
    # with the delegate, the exit reset also reset the controller to IDLE
    assert sm_on._turn_delegate.controller.state == TurnState.IDLE


def _interrupt_outcome(with_delegate: bool):
    """Drive one user-speech-during-TTS interrupt (TTS playing)."""
    sm, _clock = _make_sm(with_delegate=with_delegate)
    sm._asr = ScriptedASR(["喂"])

    async def _run():
        tts_task = asyncio.create_task(asyncio.sleep(30))
        sm._tts_task = tts_task
        await sm._handle_dialog(b"\x00\x00" * 80)  # user speaks during TTS
        await asyncio.sleep(0)  # let cancellation deliver
        return tts_task.cancelled(), sm.state.name

    return asyncio.run(_run())


def test_interrupt_timing_identical_with_and_without_delegate():
    off = _interrupt_outcome(with_delegate=False)
    on = _interrupt_outcome(with_delegate=True)
    assert off == on == (True, "TTS_PAUSED")


def _wake_chain_outcome(monkeypatch, enabled: bool):
    """Drive the KWS -> WAIT_ASR_CONFIRM -> DIALOG_ACTIVE wake chain."""
    if enabled:
        monkeypatch.setenv("JARVIS_TURN_DELEGATE_ENABLED", "1")
    else:
        monkeypatch.delenv("JARVIS_TURN_DELEGATE_ENABLED", raising=False)
    _JarvisConfig, JarvisState, JarvisStateMachine = _jarvis_mode()
    sm = JarvisStateMachine(config=_JarvisConfig())
    sm._kws = FakeKWS(fires_on_call=1)
    sm._asr = FakeASR(partials=["bt"])
    sm._play_wake_wav = lambda: asyncio.sleep(0)

    async def _scenario():
        await sm._handle_kws(b"\x00\x00" * 80)
        # v3.19 fast-path: the tap may promote directly to DIALOG_ACTIVE;
        # otherwise drive the confirm window.
        if sm.state == JarvisState.WAIT_ASR_CONFIRM:
            await sm._handle_wait_asr_confirm(b"\x00\x00" * 80)
            await asyncio.sleep(0.05)
        return sm.state.name

    state = asyncio.run(_scenario())
    return sm, state


def test_wake_chain_identical_with_and_without_delegate(monkeypatch):
    _sm_off, state_off = _wake_chain_outcome(monkeypatch, enabled=False)
    sm_on, state_on = _wake_chain_outcome(monkeypatch, enabled=True)
    assert state_off == state_on == "DIALOG_ACTIVE"
    # entering DIALOG_ACTIVE drove the controller wake gate to LISTENING
    assert sm_on._turn_delegate.controller.state.name == "LISTENING"


# ---------------------------------------------------------------------------
# 3. Controller arbitration wiring
# ---------------------------------------------------------------------------


def test_dialog_lifecycle_keeps_controller_aligned():
    sm, _clock = _make_sm(with_delegate=True)
    delegate = sm._turn_delegate
    assert delegate.controller.state == TurnState.LISTENING
    # jarvis returning to KWS_LISTENING resets the controller to IDLE
    _JarvisConfig, JarvisState, _JarvisStateMachine = _jarvis_mode()
    asyncio.run(sm._transition_to(JarvisState.KWS_LISTENING))
    assert delegate.controller.state == TurnState.IDLE


def test_delegated_commit_cycle_returns_controller_to_listening():
    # Use the real _send_to_llm with a mocked httpx client so the delegate
    # hooks (on_llm_response_token / TTS lifecycle) run end-to-end.
    class FakeResponse:
        def __init__(self, text):
            self._text = text

        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": self._text}}]}

    posted = []
    sm, clock = _make_sm(with_delegate=True)
    sm._asr = ScriptedASR(["你好"])

    client = AsyncMock()

    def make_post(*_a, **_kw):
        async def post(url, json):
            posted.append(json)
            return FakeResponse("收到。")

        return post

    client.post = make_post()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    async def _scenario():
        with patch("httpx.AsyncClient", return_value=client):
            await sm._handle_dialog(b"\x00\x00" * 80)  # partial
            sm._last_speech_time = time.time() - 2.5
            clock.advance(2000)
            await sm._handle_dialog(b"\x00\x00" * 80)  # stale -> commit

    asyncio.run(_scenario())
    _JarvisConfig, JarvisState, _JarvisStateMachine = _jarvis_mode()
    assert len(posted) == 1
    assert sm.state == JarvisState.DIALOG_ACTIVE
    # PROCESSING -> THINKING -> SPEAKING -> LISTENING (full agent-turn cycle)
    assert sm._turn_delegate.controller.state == TurnState.LISTENING


def test_controller_driven_barge_in_fires_existing_tts_pause():
    sm, clock = _make_sm(with_delegate=True)
    sm._asr = ScriptedASR(["继续"])
    delegate = sm._turn_delegate
    # Drive the controller into SPEAKING (agent turn in progress).
    delegate.on_speech_started(0.9)  # LISTENING -> USER_SPEAKING
    clock.advance(1000)
    delegate.on_speech_stopped(600)  # -> PROCESSING
    delegate.on_llm_response_token("收到。")  # -> THINKING
    delegate.on_tts_started()  # -> SPEAKING
    assert delegate.controller.state == TurnState.SPEAKING

    async def _run():
        tts_task = asyncio.create_task(asyncio.sleep(30))
        sm._tts_task = tts_task
        await sm._handle_dialog(b"\x00\x00" * 80)  # user speaks during agent TTS
        await asyncio.sleep(0)
        return tts_task.cancelled(), sm.state.name

    cancelled, state = asyncio.run(_run())
    assert cancelled, "TTS must be paused on controller barge-in"
    assert state == "TTS_PAUSED"
    assert delegate.controller.state == TurnState.HARD_INTERRUPTED


def test_delegated_garbage_drop_returns_controller_to_listening():
    calls = []
    sm, clock = _make_sm(with_delegate=True)
    sm._asr = ScriptedASR(["嗯"])
    sm._send_to_llm = _make_send_stub(calls)

    asyncio.run(sm._handle_dialog(b"\x00\x00" * 80))  # partial "嗯"
    sm._last_speech_time = time.time() - 2.5
    clock.advance(2000)
    asyncio.run(sm._handle_dialog(b"\x00\x00" * 80))  # stale -> garbage drop

    _JarvisConfig, JarvisState, _JarvisStateMachine = _jarvis_mode()
    assert calls == []
    assert sm.state == JarvisState.DIALOG_ACTIVE
    assert sm._turn_delegate.controller.state == TurnState.LISTENING


# ---------------------------------------------------------------------------
# 4. Fail-open: controller error -> disable + legacy fallback
# ---------------------------------------------------------------------------


def test_fail_open_speech_event_error_falls_back_to_legacy(caplog):
    sm, _clock = _make_sm(with_delegate=True)
    sm._asr = ScriptedASR(["你好"])

    def boom(conf):
        raise RuntimeError("controller boom")

    sm._turn_delegate._turn.on_speech_started = boom

    with caplog.at_level(logging.WARNING, logger="joyai.jarvis"):
        asyncio.run(sm._handle_dialog(b"\x00\x00" * 80))

    _JarvisConfig, JarvisState, _JarvisStateMachine = _jarvis_mode()
    assert sm._turn_delegate is None, "delegate must be disabled after a failure"
    assert sm._current_asr_text == "你好", "legacy path must still process the chunk"
    assert sm.state == JarvisState.DIALOG_ACTIVE
    assert any("[turn-delegate] dialog delegation failed" in r.getMessage() for r in caplog.records)


def test_fail_open_commit_error_still_sends_via_legacy():
    calls = []
    sm, clock = _make_sm(with_delegate=True)
    sm._asr = ScriptedASR(["你好"])
    sm._send_to_llm = _make_send_stub(calls)

    def boom(silence_ms):
        raise TurnStateError("illegal speech stop")

    sm._turn_delegate._turn.on_speech_stopped = boom

    asyncio.run(sm._handle_dialog(b"\x00\x00" * 80))  # partial: delegate alive
    assert sm._turn_delegate is not None
    sm._last_speech_time = time.time() - 2.5
    clock.advance(2000)
    asyncio.run(sm._handle_dialog(b"\x00\x00" * 80))  # stale: boom -> fallback

    _JarvisConfig, JarvisState, _JarvisStateMachine = _jarvis_mode()
    assert sm._turn_delegate is None
    assert calls == ["你好"], "legacy endpoint must still deliver the utterance"
    assert sm.state == JarvisState.DIALOG_ACTIVE


def test_fail_open_transition_hook_error_is_swallowed(caplog):
    sm, _clock = _make_sm(with_delegate=True)
    sm._turn_delegate.on_dialog_enter = lambda: (_ for _ in ()).throw(
        RuntimeError("lifecycle boom")
    )
    _JarvisConfig, JarvisState, _JarvisStateMachine = _jarvis_mode()
    with caplog.at_level(logging.WARNING, logger="joyai.jarvis"):
        asyncio.run(sm._transition_to(JarvisState.DIALOG_ACTIVE))
    assert sm.state == JarvisState.DIALOG_ACTIVE
    assert any(
        "[turn-delegate] dialog lifecycle hook failed" in r.getMessage() for r in caplog.records
    )
