"""QA independent regression suite — Phase B jarvis DIALOG_ACTIVE delegation.

Written from a fresh QA perspective (complementary to the engineer's
``test_jarvis_turn_delegate.py``) to attack the "behavior unchanged" claim:

  1. env gate value matrix (default / 1 / true / 0 / false / yes / on / off)
  2. full-cycle state chain (wake -> dialog -> speak -> commit -> exit) with
     jarvis state trace AND turn_controller alignment at every step
  3. barge-in sequencing: TTS playing -> HARD_INTERRUPTED -> TTS_PAUSED ->
     COOLDOWN -> LISTENING (controller + jarvis both asserted)
  4. fail-open depth: several delegate methods monkeypatched to raise ->
     delegate disabled + legacy fallback + utterance still delivered to LLM
  5. behavior-equivalence core: the SAME scripted event sequence run under
     legacy and delegated modes -> jarvis state traces must be identical and
     ``_send_to_llm`` side effects (call count / args) must be identical
  6. exit word in delegated mode stays jarvis-owned (mode extension) -> the
     controller must never commit it, and resets to IDLE after exit
  7. LLM/TTS lifecycle hooks are state-guarded (no TurnStateError on normal
     flows) and non-fatal on delegate-internal errors

NOTE: this env has no pytest-asyncio; every test is a sync function and async
code is driven through ``asyncio.run`` (repo convention).
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
    """Lazy import jarvis_mode (repo convention — module may be reloaded)."""
    from joy_interaction_webui.jarvis_mode import (  # lazy by design
        JarvisConfig,
        JarvisState,
        JarvisStateMachine,
    )

    return JarvisConfig, JarvisState, JarvisStateMachine


class FakeClock:
    """Deterministic monotonic clock; advance with ``advance(ms)``."""

    def __init__(self, start_s: float = 1000.0) -> None:
        self.t = start_s

    def __call__(self) -> float:
        return self.t

    def advance(self, ms: float) -> None:
        self.t += ms / 1000.0


class FakeKWS:
    def __init__(self, fires_on_call: int = 1):
        self.calls = 0
        self.fires_on_call = fires_on_call

    def start(self):
        pass

    def feed_audio(self, pcm: bytes) -> bool:
        self.calls += 1
        return self.calls == self.fires_on_call


class FakeASR:
    """Wake-path ASR stub: emits partials, then repeats last / empty."""

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
    """Dialog ASR stub: replays a sequence, then holds the last text.

    Mirrors the streaming paraformer "stale partial on silence" behaviour that
    drives the 2s endpoint rule.
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


def _build_sm(*, with_delegate: bool = True, state: str = "DIALOG_ACTIVE"):
    """Build a state machine via ``__new__`` (no engine init), like the
    engineer's helper but QA-owned. Returns ``(sm, clock)``."""
    JarvisConfig, JarvisState, JarvisStateMachine = _jarvis_mode()
    cfg = JarvisConfig()
    sm = JarvisStateMachine.__new__(JarvisStateMachine)
    sm.config = cfg
    sm.state = JarvisState[state]
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


def _make_send_stub(calls: list, kwargs: list | None = None):
    async def fake_send(text, *, stream_tts=True, interaction_mode="jarvis"):
        calls.append(text)
        if kwargs is not None:
            kwargs.append({"stream_tts": stream_tts, "interaction_mode": interaction_mode})

    return fake_send


def _wake_to_dialog(monkeypatch, *, with_delegate: bool):
    """Drive the real wake chain KWS -> DIALOG_ACTIVE; return (sm, state_name)."""
    if with_delegate:
        monkeypatch.setenv("JARVIS_TURN_DELEGATE_ENABLED", "1")
    else:
        monkeypatch.delenv("JARVIS_TURN_DELEGATE_ENABLED", raising=False)
    JarvisConfig, JarvisState, JarvisStateMachine = _jarvis_mode()
    sm = JarvisStateMachine(config=JarvisConfig())
    sm._kws = FakeKWS(fires_on_call=1)
    sm._asr = FakeASR(partials=["bt"])
    sm._play_wake_wav = lambda: asyncio.sleep(0)

    async def _scenario():
        await sm._handle_kws(b"\x00\x00" * 80)
        if sm.state == JarvisState.WAIT_ASR_CONFIRM:
            await sm._handle_wait_asr_confirm(b"\x00\x00" * 80)
            await asyncio.sleep(0.05)
        return sm.state.name

    state = asyncio.run(_scenario())
    return sm, state


# ---------------------------------------------------------------------------
# 1. env gate value matrix
# ---------------------------------------------------------------------------


def test_env_gate_value_matrix(monkeypatch):
    JarvisConfig, _JarvisState, JarvisStateMachine = _jarvis_mode()

    for value in (None, "0", "false", "no", "off", "FALSE", " 1"):
        if value is None:
            monkeypatch.delenv("JARVIS_TURN_DELEGATE_ENABLED", raising=False)
        else:
            monkeypatch.setenv("JARVIS_TURN_DELEGATE_ENABLED", value)
        sm = JarvisStateMachine(config=JarvisConfig())
        assert sm._turn_delegate is None, f"expected delegate OFF for {value!r}"

    for value in ("1", "true", "yes", "on", "TRUE", "Yes"):
        monkeypatch.setenv("JARVIS_TURN_DELEGATE_ENABLED", value)
        sm = JarvisStateMachine(config=JarvisConfig())
        assert isinstance(sm._turn_delegate, TurnControllerDelegate), (
            f"expected delegate ON for {value!r}"
        )
        assert sm._turn_delegate.controller.config.scenario == "jarvis"


def test_env_gate_init_failure_is_fail_open(monkeypatch, caplog):
    """Even a broken delegate import/init must never break jarvis."""
    monkeypatch.setenv("JARVIS_TURN_DELEGATE_ENABLED", "1")
    JarvisConfig, _JarvisState, JarvisStateMachine = _jarvis_mode()

    with (
        patch(
            "joy_interaction_webui.turn_controller_delegate.TurnControllerDelegate",
            side_effect=RuntimeError("init boom"),
        ),
        caplog.at_level(logging.WARNING, logger="joyai.jarvis"),
    ):
        sm = JarvisStateMachine(config=JarvisConfig())
    assert sm._turn_delegate is None
    assert any("[turn-delegate] delegate init failed" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# 2. full-cycle state chain + controller alignment
# ---------------------------------------------------------------------------


def test_full_cycle_state_chain_and_controller_alignment(monkeypatch):
    """wake -> dialog -> speak -> commit -> exit, asserting BOTH the jarvis
    state trace and the controller state at every observable step."""
    sm, state = _wake_to_dialog(monkeypatch, with_delegate=True)
    _JarvisConfig, JarvisState, _JarvisStateMachine = _jarvis_mode()
    assert state == "DIALOG_ACTIVE"
    delegate = sm._turn_delegate
    # entering DIALOG_ACTIVE must have driven the wake gate to LISTENING
    assert delegate.controller.state == TurnState.LISTENING

    trace = [("wake", sm.state.name, delegate.controller.state.name)]
    posted = []

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "收到。"}}]}

    client = AsyncMock()

    async def _post(url, json):
        posted.append(json)
        return FakeResponse()

    client.post = _post
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    # dialog: user speaks then commits (real _send_to_llm drives the
    # controller PROCESSING -> THINKING -> SPEAKING -> LISTENING)
    sm._asr = ScriptedASR(["你好"])
    with patch("httpx.AsyncClient", return_value=client):
        asyncio.run(sm._handle_dialog(b"\x00\x00" * 80))  # partial "你好"
        assert delegate.controller.state == TurnState.USER_SPEAKING
        trace.append(("speak", sm.state.name, delegate.controller.state.name))
        sm._last_speech_time = time.time() - 2.5
        # wake-chain controller uses the real monotonic clock; let real time
        # elapse so utterance_ms >= min_utterance_ms (200ms)
        time.sleep(0.3)
        asyncio.run(sm._handle_dialog(b"\x00\x00" * 80))  # stale -> commit
    assert posted, "LLM must have been called for the committed utterance"
    assert sm.state == JarvisState.DIALOG_ACTIVE
    assert delegate.controller.state == TurnState.LISTENING
    trace.append(("commit", sm.state.name, delegate.controller.state.name))

    # exit word stays jarvis-owned -> controller must reset to IDLE
    sm._asr = ScriptedASR(["谢谢"])
    sm._play_goodbye_wav = lambda: asyncio.sleep(0)
    asyncio.run(sm._handle_dialog(b"\x00\x00" * 80))
    assert sm.state == JarvisState.KWS_LISTENING
    assert delegate.controller.state == TurnState.IDLE
    trace.append(("exit", sm.state.name, delegate.controller.state.name))

    assert trace == [
        ("wake", "DIALOG_ACTIVE", "LISTENING"),
        ("speak", "DIALOG_ACTIVE", "USER_SPEAKING"),
        ("commit", "DIALOG_ACTIVE", "LISTENING"),
        ("exit", "KWS_LISTENING", "IDLE"),
    ]


# ---------------------------------------------------------------------------
# 3. barge-in sequencing
# ---------------------------------------------------------------------------


def test_barge_in_sequence_hard_interrupted_to_cooldown_to_listening():
    """TTS playing -> user speaks -> HARD_INTERRUPTED (ctrl) + TTS_PAUSED
    (jarvis) -> silence -> COOLDOWN -> LISTENING (ctrl) + commit (jarvis)."""
    sm, clock = _build_sm(with_delegate=True)
    _JarvisConfig, _JarvisState, _JarvisStateMachine = _jarvis_mode()
    sm._asr = ScriptedASR(["继续"])
    delegate = sm._turn_delegate
    calls = []
    sm._send_to_llm = _make_send_stub(calls)

    # Drive the controller into an active agent turn (SPEAKING).
    delegate.on_speech_started(0.9)
    clock.advance(1000)
    delegate.on_speech_stopped(600)
    delegate.on_llm_response_token("收到。")
    delegate.on_tts_started()
    assert delegate.controller.state == TurnState.SPEAKING

    async def _run():
        tts_task = asyncio.create_task(asyncio.sleep(30))
        sm._tts_task = tts_task
        # user speaks during agent TTS
        await sm._handle_dialog(b"\x00\x00" * 80)
        await asyncio.sleep(0)
        hard_state = (tts_task.cancelled(), sm.state.name, delegate.controller.state.name)
        # user stops; 2s staleness -> commit of the barge-in utterance
        sm._last_speech_time = time.time() - 2.5
        clock.advance(2000)
        await sm._handle_dialog(b"\x00\x00" * 80)
        await asyncio.sleep(0)
        return hard_state, (sm.state.name, delegate.controller.state.name, list(calls))

    hard, after = asyncio.run(_run())
    # during speech: TTS paused, jarvis TTS_PAUSED, controller HARD_INTERRUPTED
    assert hard == (True, "TTS_PAUSED", "HARD_INTERRUPTED")
    # after silence: jarvis committed the barge-in utterance, controller
    # realigned through COOLDOWN -> LISTENING
    assert after == ("DIALOG_ACTIVE", "LISTENING", ["继续"])


def test_barge_in_controller_state_path_trace():
    """Trace the controller's state chain across the barge-in frames."""
    sm, clock = _build_sm(with_delegate=True)
    sm._asr = ScriptedASR(["继续"])
    delegate = sm._turn_delegate
    calls = []
    sm._send_to_llm = _make_send_stub(calls)

    # agent turn in progress
    delegate.on_speech_started(0.9)
    clock.advance(1000)
    delegate.on_speech_stopped(600)
    delegate.on_llm_response_token("收到。")
    delegate.on_tts_started()
    states = []

    async def _run():
        tts_task = asyncio.create_task(asyncio.sleep(30))
        sm._tts_task = tts_task
        await sm._handle_dialog(b"\x00\x00" * 80)  # barge-in frame
        states.append(delegate.controller.state.name)
        sm._last_speech_time = time.time() - 2.5
        clock.advance(2000)
        await sm._handle_dialog(b"\x00\x00" * 80)  # silence frame
        states.append(delegate.controller.state.name)

    asyncio.run(_run())
    # after barge-in frame -> HARD_INTERRUPTED; after silence frame ->
    # on_speech_stopped moved it COOLDOWN, then jarvis realigned to LISTENING
    assert states[0] == "HARD_INTERRUPTED"
    assert states[1] == "LISTENING"
    # the intermediate COOLDOWN was transient inside the frame; verify the
    # controller actually passed through it by checking on_cooldown_elapsed
    # was the realigner: replay the same feed against a fresh controller and
    # capture the transition via the delegate's cooldown call.
    delegate2 = _make_delegate(clock)
    delegate2.on_dialog_enter()
    delegate2.on_speech_started(0.9)
    clock.advance(1000)
    delegate2.on_speech_stopped(600)
    delegate2.on_llm_response_token("x")
    delegate2.on_tts_started()
    delegate2.on_speech_started(0.9)  # barge-in
    assert delegate2.controller.state == TurnState.HARD_INTERRUPTED
    delegate2.on_speech_stopped(2000)  # user stopped
    assert delegate2.controller.state == TurnState.COOLDOWN
    delegate2.on_cooldown_elapsed()
    assert delegate2.controller.state == TurnState.LISTENING


# ---------------------------------------------------------------------------
# 4. fail-open depth
# ---------------------------------------------------------------------------


def test_fail_open_partial_transcript_error_falls_back_and_keeps_text(caplog):
    sm, _clock = _build_sm(with_delegate=True)
    sm._asr = ScriptedASR(["你好"])
    sm._turn_delegate.on_partial_transcript = lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("partial boom")
    )
    with caplog.at_level(logging.WARNING, logger="joyai.jarvis"):
        asyncio.run(sm._handle_dialog(b"\x00\x00" * 80))
    _JarvisConfig, JarvisState, _JarvisStateMachine = _jarvis_mode()
    assert sm._turn_delegate is None
    assert sm._current_asr_text == "你好", "legacy fallback must keep the utterance"
    assert sm.state == JarvisState.DIALOG_ACTIVE
    assert any("[turn-delegate] dialog delegation failed" in r.getMessage() for r in caplog.records)


def test_fail_open_take_barge_in_error_falls_back(caplog):
    sm, _clock = _build_sm(with_delegate=True)
    sm._asr = ScriptedASR(["喂"])
    sm._turn_delegate.take_barge_in = lambda: (_ for _ in ()).throw(RuntimeError("barge boom"))
    with caplog.at_level(logging.WARNING, logger="joyai.jarvis"):
        asyncio.run(sm._handle_dialog(b"\x00\x00" * 80))
    _JarvisConfig, JarvisState, _JarvisStateMachine = _jarvis_mode()
    assert sm._turn_delegate is None
    assert sm._current_asr_text == "喂"
    assert sm.state == JarvisState.DIALOG_ACTIVE


def test_fail_open_take_commit_error_still_delivers_via_legacy():
    calls = []
    sm, clock = _build_sm(with_delegate=True)
    sm._asr = ScriptedASR(["你好"])
    sm._send_to_llm = _make_send_stub(calls)
    sm._turn_delegate.take_commit = lambda: (_ for _ in ()).throw(RuntimeError("commit boom"))

    asyncio.run(sm._handle_dialog(b"\x00\x00" * 80))  # partial: delegate alive
    assert sm._turn_delegate is not None
    sm._last_speech_time = time.time() - 2.5
    clock.advance(2000)
    asyncio.run(sm._handle_dialog(b"\x00\x00" * 80))  # stale: boom -> legacy
    _JarvisConfig, JarvisState, _JarvisStateMachine = _jarvis_mode()
    assert sm._turn_delegate is None
    assert calls == ["你好"], "legacy fallback must deliver the utterance"
    assert sm.state == JarvisState.DIALOG_ACTIVE


def test_fail_open_speech_stopped_error_delivers_via_legacy():
    calls = []
    sm, clock = _build_sm(with_delegate=True)
    sm._asr = ScriptedASR(["你好"])
    sm._send_to_llm = _make_send_stub(calls)
    sm._turn_delegate.on_speech_stopped = lambda *a, **k: (_ for _ in ()).throw(
        TurnStateError("illegal speech stop")
    )
    asyncio.run(sm._handle_dialog(b"\x00\x00" * 80))
    sm._last_speech_time = time.time() - 2.5
    clock.advance(2000)
    asyncio.run(sm._handle_dialog(b"\x00\x00" * 80))
    _JarvisConfig, _JarvisState, _JarvisStateMachine = _jarvis_mode()
    assert sm._turn_delegate is None
    assert calls == ["你好"]


# ---------------------------------------------------------------------------
# 5. behavior-equivalence core: same scripted sequence, legacy vs delegated
# ---------------------------------------------------------------------------


def _run_scripted_sequence(with_delegate: bool):
    """One scripted multi-event dialog sequence; returns (jarvis trace, llm calls)."""
    sm, clock = _build_sm(with_delegate=with_delegate)
    _JarvisConfig, _JarvisState, _JarvisStateMachine = _jarvis_mode()
    calls = []
    sm._asr = ScriptedASR(["你好", "你好", "再见", "再见"])
    sm._send_to_llm = _make_send_stub(calls)
    trace = [sm.state.name]

    def feed():
        asyncio.run(sm._handle_dialog(b"\x00\x00" * 80))
        trace.append(sm.state.name)

    feed()  # partial "你好"
    sm._last_speech_time = time.time() - 2.5
    clock.advance(2000)
    feed()  # stale -> commit
    feed()  # next partial "再见" (exit word, jarvis-owned)
    return trace, calls


def test_behavior_equivalence_jarvis_trace_and_llm_side_effects():
    off_trace, off_calls = _run_scripted_sequence(with_delegate=False)
    on_trace, on_calls = _run_scripted_sequence(with_delegate=True)
    # jarvis state sequence must be IDENTICAL step-by-step
    assert on_trace == off_trace, f"trace diff: off={off_trace} on={on_trace}"
    # _send_to_llm side effects identical (call count + args)
    assert on_calls == off_calls == ["你好"]


def test_behavior_equivalence_llm_kwargs_identical():
    off_kwargs = []
    sm_off, _clock_off = _build_sm(with_delegate=False)
    sm_off._asr = ScriptedASR(["你好"])
    sm_off._send_to_llm = _make_send_stub([], off_kwargs)
    asyncio.run(sm_off._handle_dialog(b"\x00\x00" * 80))
    sm_off._last_speech_time = time.time() - 2.5
    asyncio.run(sm_off._handle_dialog(b"\x00\x00" * 80))

    on_kwargs = []
    sm_on, clock_on = _build_sm(with_delegate=True)
    sm_on._asr = ScriptedASR(["你好"])
    sm_on._send_to_llm = _make_send_stub([], on_kwargs)
    asyncio.run(sm_on._handle_dialog(b"\x00\x00" * 80))
    sm_on._last_speech_time = time.time() - 2.5
    clock_on.advance(2000)
    asyncio.run(sm_on._handle_dialog(b"\x00\x00" * 80))

    assert on_kwargs == off_kwargs == [{"stream_tts": False, "interaction_mode": "jarvis"}]


# ---------------------------------------------------------------------------
# 6. exit word: jarvis-owned (mode extension), controller reset after exit
# ---------------------------------------------------------------------------


def test_exit_word_not_committed_by_controller_and_resets_it(monkeypatch):
    """The exit word must be intercepted by jarvis BEFORE the controller can
    commit it; after exit the controller is reset to IDLE."""
    sm, _state = _wake_to_dialog(monkeypatch, with_delegate=True)
    _JarvisConfig, JarvisState, _JarvisStateMachine = _jarvis_mode()
    delegate = sm._turn_delegate
    assert delegate.controller.state == TurnState.LISTENING

    sm._asr = ScriptedASR(["谢谢"])
    sm._play_goodbye_wav = lambda: asyncio.sleep(0)
    calls = []
    sm._send_to_llm = _make_send_stub(calls)
    asyncio.run(sm._handle_dialog(b"\x00\x00" * 80))

    # jarvis intercepted: no LLM call for the exit word, state back to KWS
    assert calls == []
    assert sm.state == JarvisState.KWS_LISTENING
    # controller reset to IDLE (wake gate) — never committed the exit word
    assert delegate.controller.state == TurnState.IDLE


# ---------------------------------------------------------------------------
# 7. LLM/TTS lifecycle hooks: state-guarded + non-fatal
# ---------------------------------------------------------------------------


def test_llm_hook_state_guarded_no_turnstateerror_on_normal_flow():
    """After a commit completes (controller back to LISTENING), a late
    TTS-finished notification must NOT raise TurnStateError (guards mirror the
    FSM's transition legality)."""
    sm, clock = _build_sm(with_delegate=True)
    delegate = sm._turn_delegate
    # drive a full agent turn through the controller
    delegate.on_speech_started(0.9)
    clock.advance(1000)
    delegate.on_speech_stopped(600)
    delegate.on_llm_response_token("收到。")
    delegate.on_tts_started()
    delegate.on_tts_finished()
    assert delegate.controller.state == TurnState.LISTENING
    # stray feed in LISTENING is a benign race -> no raise
    sm._notify_delegate_tts_finished()  # must be a no-op, not TurnStateError
    sm._notify_delegate_tts_started()
    delegate.on_llm_response_token("迟到的 token")  # PROCESSING guard -> no-op


def test_llm_hook_error_is_nonfatal_but_delegate_must_recover():
    """A delegate-internal error in the LLM hook must not break the current
    jarvis turn (reply still delivered, jarvis stays DIALOG_ACTIVE) AND must
    not silently wedge the controller so the NEXT user turn still commits.

    This encodes the fail-open contract: after any delegate error the caller
    either disables the delegate or leaves it in a state where the next turn
    commits. A controller stuck in PROCESSING would drop the next utterance.
    """

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "收到。"}}]}

    posted = []
    sm, clock = _build_sm(with_delegate=True)
    sm._asr = ScriptedASR(["你好"])
    sm.on_llm_response = lambda *a, **k: None
    delegate = sm._turn_delegate

    # force a delegate-internal error in the LLM response hook
    delegate.on_llm_response_token = lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("llm hook boom")
    )

    client = AsyncMock()
    client.post = lambda *a, **k: (_ for _ in ()).throw(AssertionError)  # placeholder

    async def _post(url, json):
        posted.append(json)
        return FakeResponse()

    client.post = _post
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    _JarvisConfig, JarvisState, _JarvisStateMachine = _jarvis_mode()
    with patch("httpx.AsyncClient", return_value=client):
        asyncio.run(sm._handle_dialog(b"\x00\x00" * 80))  # partial
        sm._last_speech_time = time.time() - 2.5
        clock.advance(2000)
        asyncio.run(sm._handle_dialog(b"\x00\x00" * 80))  # stale -> commit

    # current turn: jarvis reply was still delivered (hook error non-fatal)
    assert len(posted) == 1
    assert sm.state == JarvisState.DIALOG_ACTIVE

    # next user turn must still commit (fail-open contract)
    sm._asr = ScriptedASR(["在吗"])
    sm._send_to_llm = _make_send_stub(posted)
    asyncio.run(sm._handle_dialog(b"\x00\x00" * 80))
    sm._last_speech_time = time.time() - 2.5
    clock.advance(2000)
    asyncio.run(sm._handle_dialog(b"\x00\x00" * 80))
    # FAIL-OPEN CONTRACT: the second utterance must reach the LLM too.
    assert posted[-1] == "在吗", (
        "FAIL-OPEN CONTRACT VIOLATION: after an LLM-hook error the controller "
        f"is wedged (state={delegate.controller.state.name}) and the next user "
        "utterance was dropped — delegate should have been disabled/realigned."
    )
