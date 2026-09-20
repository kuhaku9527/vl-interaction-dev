"""QA boundary regression for live mode (Phase C C.A) — independent, fresh-eyes.

Written by QA (Edward) as a second-opinion layer over the engineer's
``test_live_mode.py`` / ``test_live_frontend_contract.py``. Every case here
targets a behaviour the spec (``doc/specs/live-interaction-layer.md``
§4 C.A) demands but the existing suite exercises only lightly:

  * two consecutive turns — ``reply_epoch`` monotonic + history growth;
  * double interrupt (连打) — barge-in re-triggered from cooldown;
  * VAD-unavailable partial-driven onset (fail-open);
  * ASR exception mid-turn then recovery;
  * non-streaming retry — reply source tag (audibility contract);
  * ``stop()`` then ``feed_audio`` is inert (no crash / no commit / no broadcast);
  * garbage / too-short utterance dropped -> LISTENING;
  * VAD-only onset with silent ASR (documented edge);
  * jarvis + live dual-session isolation (real JarvisSessionManager);
  * ``/api/live/start`` idempotency and ``/api/live/stop`` tolerance;
  * WebRTC offer branch order (live_audio precedes jarvis);
  * front-end static wiring extras.

Run: python -m pytest tests/test_live_mode_qa_boundary.py -q
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
from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from joy_interaction_webui import live_mode as live_module  # noqa: E402
from joy_interaction_webui.jarvis_mode import JarvisConfig  # noqa: E402
from joy_interaction_webui.jarvis_session import JarvisSessionManager  # noqa: E402
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
# Test doubles (self-contained — do not import from the engineer's tests)
# ---------------------------------------------------------------------------


class FakeClock:
    """Drop-in ``time`` module + monotonic clock for the TurnController."""

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
        self.boom = False
        self.start_calls = 0
        self.stop_calls = 0
        self.feed_calls = 0

    def start(self) -> None:
        self.start_calls += 1

    def stop(self) -> None:
        self.stop_calls += 1

    def feed_chunk(self, pcm: bytes) -> str:
        self.feed_calls += 1
        if self.boom:
            raise RuntimeError("asr boom")
        return self.text

    def set_text(self, text: str) -> None:
        self.text = text


class FakeConsumer:
    """Fake StreamingTurnConsumer: records kwargs + optionally emits sentences."""

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


def build_live(*, controller=None, vad=None, asr=None, **overrides):
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


def _live_controller(clock=None, cooldown_ms: int = 10) -> TurnController:
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


async def _finish_agent_turn(sm):
    """Await in-flight sentence TTS + turn-done watcher -> LISTENING."""
    await asyncio.gather(*list(sm._tts_sentence_tasks), return_exceptions=True)
    if sm._tts_turn_task is not None:
        await sm._tts_turn_task


# ---------------------------------------------------------------------------
# 1. Two consecutive turns — reply_epoch monotonic, history grows
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_consecutive_turns_reply_epoch_monotonic(monkeypatch):
    clock = FakeClock(1000.0)
    sm, vad, asr = build_live(controller=_live_controller(clock))
    monkeypatch.setattr(live_module, "time", clock)

    fake = FakeConsumer(
        _result(full_response="第一轮回复", decision="response", sentence_count=1),
        sentences=["第一轮回复"],
    )
    monkeypatch.setattr(live_module, "StreamingTurnConsumer", fake)

    async def fake_tts(text):
        return b"\x00\x00" * 100

    monkeypatch.setattr(sm, "_fetch_tts_pcm", fake_tts)
    broadcasts: list = []
    sm.on_llm_response = lambda text, source: broadcasts.append((text, source))

    # ---- turn 1 ----
    vad.set_speech(True)
    await sm.feed_audio(PCM)
    clock.now += 0.5
    asr.set_text("你好")
    await sm.feed_audio(PCM)
    vad.set_speech(False)
    clock.now += 2.0
    await sm.feed_audio(PCM)

    assert sm._llm_reply_epoch == 1, "turn-start bump"
    assert sm._current_turn_reply_epoch == 1
    assert fake.consume_kwargs["interaction_mode"] == "live"
    assert sm.turn_state in (TurnState.THINKING, TurnState.SPEAKING)
    await _finish_agent_turn(sm)
    assert sm.turn_state == TurnState.LISTENING

    # ---- turn 2 ----
    vad.set_speech(True)
    await sm.feed_audio(PCM)
    clock.now += 0.5
    asr.set_text("再说一遍")
    await sm.feed_audio(PCM)
    vad.set_speech(False)
    clock.now += 2.0
    await sm.feed_audio(PCM)

    assert sm._llm_reply_epoch == 2, "second turn bumps again (monotonic)"
    assert sm._current_turn_reply_epoch == 2
    assert fake.consume_kwargs["text"] == "再说一遍"

    # History holds both turns, in order.
    hist = list(sm._conv_history)
    assert hist[-4:] == [
        ("user", "你好"),
        ("assistant", "第一轮回复"),
        ("user", "再说一遍"),
        ("assistant", "第一轮回复"),
    ]
    await _finish_agent_turn(sm)
    await sm.stop()


# ---------------------------------------------------------------------------
# 2. Double interrupt (连打): barge-in -> cooldown -> reclaim floor -> commit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_double_interrupt_connected_barge_in(monkeypatch):
    clock = FakeClock(1000.0)
    sm, vad, asr = build_live(controller=_live_controller(clock, cooldown_ms=5000))
    monkeypatch.setattr(live_module, "time", clock)

    sends: list = []

    async def fake_send(text, *, interaction_mode="live", stream=True, frames=None):
        sm._llm_reply_epoch += 1  # mirror the real turn-start bump
        sends.append((text, sm._llm_reply_epoch, interaction_mode, stream))

    monkeypatch.setattr(sm, "_send_to_llm", fake_send)

    # Agent replying (SPEAKING).
    sm._ctrl.on_speech_started(0.9)  # LISTENING -> USER_SPEAKING
    clock.now += 1.0
    sm._ctrl.on_speech_stopped(600)  # -> PROCESSING (commit flag set)
    sm._commit_pending = False  # we drive the controller directly here
    sm._ctrl.on_llm_token("好的")  # -> THINKING
    sm._ctrl.on_tts_started()  # -> SPEAKING
    assert sm.turn_state == TurnState.SPEAKING

    # Barge-in #1.
    vad.set_speech(True)
    await sm.feed_audio(PCM)
    assert sm.turn_state == TurnState.HARD_INTERRUPTED
    assert sm._llm_reply_epoch == 1 and sm._tts_sentence_epoch == 1
    assert sm._llm_stream_cancel is True

    # User keeps talking (partials) -> stays HARD_INTERRUPTED, NO double bump.
    asr.set_text("等一下")
    clock.now += 0.3
    await sm.feed_audio(PCM)
    assert sm.turn_state == TurnState.HARD_INTERRUPTED
    assert sm._llm_reply_epoch == 1, "no spurious bump while still interrupting"
    assert sm._tts_sentence_epoch == 1

    # User stops -> COOLDOWN (interrupted utterance dropped, ASR reset).
    vad.set_speech(False)
    clock.now += 2.0
    await sm.feed_audio(PCM)
    assert sm.turn_state == TurnState.COOLDOWN
    assert sm._current_asr_text == ""

    # 连打: user speaks again during cooldown (conf 0.9 >= 0.75) -> reclaim floor.
    vad.set_speech(True)
    asr.set_text("接着说")
    clock.now += 0.5
    await sm.feed_audio(PCM)
    assert sm.turn_state == TurnState.USER_SPEAKING
    assert sm._llm_reply_epoch == 1, "epoch not bumped until the new turn commits"

    # New utterance endpoints -> commit -> LLM with epoch bump to 2.
    vad.set_speech(False)
    clock.now += 2.0
    await sm.feed_audio(PCM)
    assert sends == [("接着说", 2, "live", True)]
    assert sm._llm_reply_epoch == 2
    await sm.stop()


# ---------------------------------------------------------------------------
# 3. VAD unavailable -> ASR partial growth drives onset (fail-open)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_vad_unavailable_partial_drives_onset(monkeypatch):
    clock = FakeClock(1000.0)
    sm, vad, asr = build_live(controller=_live_controller(clock))
    monkeypatch.setattr(live_module, "time", clock)
    vad.available = False  # Silero missing/disabled -> fail-open

    partials: list = []
    sm.on_asr_partial = lambda p: partials.append(p)

    sends: list = []

    async def fake_send(text, *, interaction_mode="live", stream=True, frames=None):
        sm._llm_reply_epoch += 1
        sends.append((text, interaction_mode, stream))

    monkeypatch.setattr(sm, "_send_to_llm", fake_send)

    # Silence: no ASR text -> no onset -> LISTENING.
    await sm.feed_audio(PCM)
    assert sm.turn_state == TurnState.LISTENING
    assert sm._prev_vad_speech is False

    # ASR partial growth is the speech signal without VAD.
    asr.set_text("你好")
    clock.now += 0.5
    await sm.feed_audio(PCM)
    assert sm.turn_state == TurnState.USER_SPEAKING
    assert partials, "asr_partial pushed to browser"
    assert partials[-1].text == "你好"
    assert partials[-1].is_final is False
    assert partials[-1].reply_epoch == 0

    # Endpoint -> commit -> LLM(live).
    clock.now += 2.0
    await sm.feed_audio(PCM)
    assert sends == [("你好", "live", True)]
    assert sm._current_asr_text == ""
    assert sm._llm_reply_epoch == 1
    await sm.stop()


# ---------------------------------------------------------------------------
# 4. ASR exception mid-turn -> fail-open, then recovery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_asr_exception_mid_turn_recovers(monkeypatch):
    clock = FakeClock(1000.0)
    sm, vad, asr = build_live(controller=_live_controller(clock))
    monkeypatch.setattr(live_module, "time", clock)

    sends: list = []

    async def fake_send(text, *, interaction_mode="live", stream=True, frames=None):
        sm._llm_reply_epoch += 1
        sends.append((text, interaction_mode))

    monkeypatch.setattr(sm, "_send_to_llm", fake_send)

    # ASR explodes on the very first chunk; VAD still drives the onset.
    asr.boom = True
    vad.set_speech(True)
    await sm.feed_audio(PCM)  # must not raise
    assert sm.turn_state == TurnState.USER_SPEAKING
    assert sm._current_asr_text == ""  # no text survived the exception

    # ASR recovers -> partials accumulate -> endpoint -> commit.
    asr.boom = False
    asr.set_text("恢复了")
    clock.now += 0.5
    await sm.feed_audio(PCM)
    assert sm._current_asr_text == "恢复了"

    vad.set_speech(False)
    clock.now += 2.0
    await sm.feed_audio(PCM)
    assert sends == [("恢复了", "live")]
    assert sm.turn_state == TurnState.PROCESSING
    await sm.stop()


# ---------------------------------------------------------------------------
# 5. Non-streaming retry: reply must stay audible (source-tag contract)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_streaming_retry_reply_stays_audible(monkeypatch):
    """FAIL-OPEN CONTRACT: a pre-frame stream failure retries non-streaming and
    the reply must still be *heard*. Live has no server-side speaker track, so
    the browser synthesizes the full reply only when the llm_reply source is
    ``live_text``; ``live_voice`` tells the browser "audio already streamed" —
    which is FALSE for the non-streaming fallback (no tts_sentence was pushed).
    """
    sm, _, _ = build_live()
    broadcasts: list = []
    sm.on_llm_response = lambda text, source: broadcasts.append((text, source))

    fake = FakeConsumer(
        _result(full_response="", decision="response", needs_non_streaming_retry=True)
    )
    monkeypatch.setattr(live_module, "StreamingTurnConsumer", fake)

    async def fake_retry(text, *, interaction_mode="live", reply_epoch=None, frames=None):
        await sm._finish_llm_turn(
            text=text,
            response="重试回复",
            decision="response",
            delegation_question=None,
            reply_epoch=reply_epoch,
        )

    monkeypatch.setattr(sm, "_send_to_llm_non_streaming", fake_retry)

    await sm._send_to_llm("你好", interaction_mode="live", stream=True)

    # No tts_sentence audio was pushed on this path...
    assert not sm._tts_sentence_tasks, "non-streaming retry must not spawn sentences"
    assert not sm._sentence_spawned_this_turn
    # ...therefore the browser must be allowed to synthesize: live_text.
    assert broadcasts == [("重试回复", "live_text")], (
        "non-streaming fallback tagged live_voice -> browser skips synthesis -> SILENT reply"
    )


# ---------------------------------------------------------------------------
# 6. stop() then feed_audio is inert
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_feed_audio_after_stop_is_inert(monkeypatch):
    sm, vad, asr = build_live()
    broadcasts: list = []
    sm.on_llm_response = lambda text, source: broadcasts.append((text, source))

    await sm.stop()
    assert sm.turn_state == TurnState.ENDED
    epoch_before = sm._llm_reply_epoch

    # Feeding after stop must not raise, must not commit, must not broadcast.
    vad.set_speech(True)
    asr.set_text("别理我")
    await sm.feed_audio(PCM)
    assert sm.turn_state == TurnState.ENDED
    assert broadcasts == []
    assert sm._llm_reply_epoch == epoch_before


# ---------------------------------------------------------------------------
# 7. Garbage / too-short utterance dropped -> LISTENING (no LLM call)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_garbage_utterance_dropped_back_to_listening(monkeypatch):
    clock = FakeClock(1000.0)
    sm, vad, asr = build_live(controller=_live_controller(clock))
    monkeypatch.setattr(live_module, "time", clock)

    sends: list = []

    async def fake_send(text, *, interaction_mode="live", stream=True, frames=None):
        sends.append(text)

    monkeypatch.setattr(sm, "_send_to_llm", fake_send)

    vad.set_speech(True)
    await sm.feed_audio(PCM)
    clock.now += 0.5
    asr.set_text("啊")  # _is_garbage_text -> drop
    await sm.feed_audio(PCM)
    vad.set_speech(False)
    clock.now += 2.0
    await sm.feed_audio(PCM)

    assert sends == []
    assert sm.turn_state == TurnState.LISTENING
    assert sm._llm_reply_epoch == 0  # no turn ever started
    await sm.stop()


# ---------------------------------------------------------------------------
# 8. VAD-only onset with silent ASR (documented edge)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_vad_onset_with_silent_asr_is_documented(monkeypatch):
    """VAD fires on noise but ASR returns nothing -> controller stays in
    USER_SPEAKING until real speech produces ASR text (no endpoint, because the
    endpoint requires non-empty accumulated text). Documented as a Known Issue
    (UI shows 'Live 听你说'); it does not crash and recovers on real speech."""
    clock = FakeClock(1000.0)
    sm, vad, asr = build_live(controller=_live_controller(clock))
    monkeypatch.setattr(live_module, "time", clock)

    sends: list = []

    async def fake_send(text, *, interaction_mode="live", stream=True, frames=None):
        sm._llm_reply_epoch += 1
        sends.append((text, interaction_mode))

    monkeypatch.setattr(sm, "_send_to_llm", fake_send)

    vad.set_speech(True)  # acoustic false positive
    await sm.feed_audio(PCM)
    assert sm.turn_state == TurnState.USER_SPEAKING

    # ASR stays silent for 5s -> still USER_SPEAKING (no endpoint path).
    clock.now += 5.0
    await sm.feed_audio(PCM)
    assert sm.turn_state == TurnState.USER_SPEAKING

    # Real speech arrives -> partial -> endpoint -> commit recovers the loop.
    asr.set_text("真的在说话")
    clock.now += 0.5
    await sm.feed_audio(PCM)
    clock.now += 2.0
    await sm.feed_audio(PCM)
    assert sends == [("真的在说话", "live")]
    assert sm._llm_reply_epoch == 1
    assert sm.turn_state == TurnState.PROCESSING
    await sm.stop()


# ---------------------------------------------------------------------------
# 9. Dual-session isolation (real JarvisSessionManager)
# ---------------------------------------------------------------------------


class _FakeJarvisSM:
    """Minimal JarvisStateMachine stand-in (real jarvis internals are covered
    by the jarvis regression suites; here we only exercise manager isolation)."""

    def __init__(self, *args, **kwargs):
        from types import SimpleNamespace

        from joy_interaction_webui.jarvis_mode import JarvisState

        self.state = JarvisState.KWS_LISTENING
        self.config = SimpleNamespace(wake_word="bt")
        self._llm_reply_epoch = 0
        self._current_turn_reply_epoch = 0
        self.audio_output = None

    async def prewarm_engines(self):
        return None

    async def run(self):
        # JarvisSession.start() spawns state_machine.run(); complete instantly.
        return None

    async def stop(self):
        return None

    def is_active(self):
        return True

    def get_state_for_browser(self):
        return {"mode": "jarvis", "turn_state": self.state}

    def attach_audio_output(self, audio_output):
        self.audio_output = audio_output


async def _make_manager(monkeypatch) -> JarvisSessionManager:
    from joy_interaction_webui import jarvis_session as js_module

    # Avoid loading real jarvis engines; keep live prewarm light too.
    monkeypatch.setattr(js_module, "JarvisStateMachine", _FakeJarvisSM)
    monkeypatch.setattr(live_module.LiveStateMachine, "prewarm_engines", lambda self: _noop())
    return JarvisSessionManager(_stub_config())


async def _noop():
    return None


@pytest.mark.asyncio
async def test_jarvis_live_dual_session_isolation(monkeypatch):
    manager = await _make_manager(monkeypatch)

    # Live first, then jarvis, same webui session_id -> both coexist.
    live = await manager.create_session("s1", mode="live")
    jarvis = await manager.create_session("s1")  # default mode="jarvis"

    assert manager.get_live_session("s1") is live
    assert manager.get_session("s1") is jarvis
    assert live is not jarvis
    assert len(manager._live_sessions) == 1
    assert len(manager._sessions) == 1

    # Independent state machines + independent state reporting.
    live_sm = live.state_machine
    jarvis_sm = jarvis.state_machine
    assert live_sm is not jarvis_sm
    assert live.get_state_for_browser()["mode"] == "live"
    # JarvisSession.get_state_for_browser has its own shape (jarvis_state).
    assert jarvis.get_state_for_browser()["jarvis_state"] == "KWS_LISTENING"

    # Driving live does not touch the jarvis session.
    live_sm._ctrl.on_speech_started(0.9)
    assert live_sm.turn_state == TurnState.USER_SPEAKING
    assert jarvis_sm.state.name == "KWS_LISTENING"  # untouched
    assert live_sm._llm_reply_epoch == 0
    assert jarvis_sm._llm_reply_epoch == 0

    # Epoch getter prefers the jarvis session when both exist (jarvis first).
    live_sm._llm_reply_epoch = 7
    assert manager._session_reply_epoch("s1", "_llm_reply_epoch") == 0  # jarvis wins

    # Removing the live session leaves the jarvis session intact.
    await manager.remove_live_session("s1")
    assert manager.get_live_session("s1") is None
    assert manager.get_session("s1") is jarvis
    assert len(manager._sessions) == 1

    # And vice versa: removing jarvis leaves a re-created live session intact.
    live2 = await manager.create_session("s1", mode="live")
    await manager.remove_session("s1")
    assert manager.get_session("s1") is None
    assert manager.get_live_session("s1") is live2


@pytest.mark.asyncio
async def test_dual_session_default_jarvis_path_unchanged(monkeypatch):
    """create_session() without mode must keep the jarvis default behaviour."""
    manager = await _make_manager(monkeypatch)
    session = await manager.create_session("s1")
    assert manager.get_session("s1") is session
    assert manager.get_live_session("s1") is None
    assert isinstance(session.state_machine, _FakeJarvisSM)
    # Idempotent within the jarvis dict too.
    again = await manager.create_session("s1")
    assert again is session


# ---------------------------------------------------------------------------
# 10. /api/live/start idempotency + /api/live/stop tolerance (real manager)
# ---------------------------------------------------------------------------


def _live_app(manager) -> web.Application:
    app = web.Application()
    app["jarvis_manager"] = manager
    setup_live_routes(app)
    return app


@pytest.mark.asyncio
async def test_live_start_idempotent_real_manager(monkeypatch):
    manager = await _make_manager(monkeypatch)
    async with TestServer(_live_app(manager)) as srv, TestClient(srv) as client:
        r1 = await client.post("/api/live/start", json={"session_id": "s1"})
        r2 = await client.post("/api/live/start", json={"session_id": "s1"})
        assert r1.status == 200 and r2.status == 200
        d1, d2 = await r1.json(), await r2.json()
        assert d1["started"] is True and d2["started"] is True
        # Exactly one live session exists after the second call.
        assert len(manager._live_sessions) == 1
        assert d1["turn_state"] == d2["turn_state"] == "LISTENING"


@pytest.mark.asyncio
async def test_live_stop_tolerates_missing_session(monkeypatch):
    manager = await _make_manager(monkeypatch)
    async with TestServer(_live_app(manager)) as srv, TestClient(srv) as client:
        resp = await client.post("/api/live/stop", json={"session_id": "ghost"})
        assert resp.status == 200
        data = await resp.json()
        assert data["stopped"] is True
        assert manager.get_live_session("ghost") is None


@pytest.mark.asyncio
async def test_live_start_requires_session_id(monkeypatch):
    manager = await _make_manager(monkeypatch)
    async with TestServer(_live_app(manager)) as srv, TestClient(srv) as client:
        resp = await client.post("/api/live/start", json={})
        assert resp.status == 400


# ---------------------------------------------------------------------------
# 11. WebRTC offer branch order (live_audio before jarvis)
# ---------------------------------------------------------------------------


def test_offer_live_branch_precedes_jarvis_branch():
    server_py = (
        Path(__file__).resolve().parents[1] / "src" / "joy_interaction_webui" / "server.py"
    ).read_text(encoding="utf-8")

    live_idx = server_py.index('params.get("live_audio") is True')
    jarvis_idx = server_py.index("elif _offer_has_jarvis_audio(params):")
    assert live_idx < jarvis_idx, "live_audio must be checked BEFORE jarvis audio"
    # Both branches exist (jarvis untouched, live added).
    assert "bind_live_audio_for_peer" in server_py
    assert "bind_jarvis_audio_for_peer" in server_py


# ---------------------------------------------------------------------------
# 12. Front-end static wiring extras
# ---------------------------------------------------------------------------


_WEBUI_STATIC = Path(__file__).resolve().parents[1] / "src" / "joy_interaction_webui" / "static"
# The split-module list is DERIVED from index.html by tests/_frontend_corpus.py,
# not hardcoded here. A hardcoded copy went stale twice (see that module's
# docstring and doc/standards/webui-design-standards.md 9.7).
from tests._frontend_corpus import index_html_plus_split_js  # noqa: E402


def _index_html() -> str:
    return index_html_plus_split_js()


def test_frontend_live_entry_points_exist():
    html = _index_html()
    assert 'id="liveModeBtn"' in html
    assert 'id="liveStatus"' in html
    assert "async function startLiveMode(" in html
    # stopLiveMode declares a default options arg: `{ notifyServer = true } = {}`.
    assert "async function stopLiveMode(" in html
    assert "function setLiveModeActive(active)" in html
    assert "startLiveStatusPoll" in html
    assert "stopLiveStatusPoll" in html


def test_frontend_live_offer_carries_live_audio_flag():
    html = _index_html()
    # The offer payload must tag live_audio:true (server mounts LiveStateMachine).
    assert "live_audio: true" in html


def test_frontend_live_skips_voice_and_reuses_queue():
    html = _index_html()
    # P0-A queue reuse: tts_sentence handler enqueues, llmReplyGeneration guards.
    assert "enqueueLlmReplySentence(data)" in html
    assert "data.reply_epoch < window.JoyState.llmReplyGeneration" in html
    # live_voice (streaming path) must not double-play via /api/tts/synthesize.
    assert "meta.source === 'jarvis_voice' || meta.source === 'live_voice'" in html
    # live_text (non-streaming fallback) must still synthesize in the browser.
    assert "meta.source === 'live_text'" in html or "live_text" in html
