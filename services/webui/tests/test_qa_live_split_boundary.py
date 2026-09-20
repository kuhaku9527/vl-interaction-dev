"""QA boundary supplement for the live_mode split (batch 4/4, commit e986614).

Independent verification (fresh perspective) of the extracted modules:

* ``live_enroll.enroll_pcm_verdict`` — three-guard order + boundaries
  (not-in-phase / empty / odd-len / overflow / ok);
* ``live_enroll.feed_enroll_vad`` — VAD-segmented collection state machine
  (rising edge / extend / falling edge short vs >=1s / max-3 overflow /
  prev_vad_speech returned);
* ``live_frames.frame_window_from_env`` — env clamping (0 / negative /
  huge / malformed);
* ``live_proactive.proactive_switch_action`` — all 5 branches
  (reject / start / already_running / cancel / already_off);
* ``live_llm.send_to_llm`` — cancelled branch must NOT broadcast
  (``on_finish_turn`` skipped) while epoch/seq still bump;
* ``live_llm.finish_llm_turn`` — decision consumption
  (response+spawned -> live_voice / silence -> live_text /
  delegation -> routed + consumed to silence / not-for-me -> live_text);
* degradation point ① runtime proof: the ``is_cancelled`` callback passed
  into the consumer reads the LIVE ``_llm_stream_cancel`` attribute (facade
  resets before delegating; barge-in sets it mid-stream -> callback flips).

Run: python -m pytest tests/test_qa_live_split_boundary.py -q
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
from joy_interaction_webui.live_enroll import (  # noqa: E402
    _ADDRESSEE_ENROLL_MAX_SEGMENTS,
    enroll_pcm_verdict,
    feed_enroll_vad,
)
from joy_interaction_webui.live_frames import frame_window_from_env  # noqa: E402
from joy_interaction_webui.live_mode import LiveStateMachine  # noqa: E402
from joy_interaction_webui.live_proactive import proactive_switch_action  # noqa: E402
from joy_interaction_webui.turn_controller import (  # noqa: E402
    TurnController,
    TurnState,
)
from joy_interaction_webui.turn_streaming import StreamingTurnResult  # noqa: E402

PCM = b"\x00\x00" * 100  # 100ms of 16 kHz mono silence (int16)
SEGMENT_1S = b"\x00\x00" * 32000  # exactly 1.0 s @ 16 kHz mono int16


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

    def accept_waveform(self, samples) -> None:
        pass

    def is_speech(self) -> bool:
        return self.speech

    def set_speech(self, value: bool) -> None:
        self.speech = value


class FakeASR:
    def __init__(self, text: str = "") -> None:
        self.text = text

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def feed_chunk(self, pcm: bytes) -> str:
        return self.text

    def set_text(self, text: str) -> None:
        self.text = text


class FakeConsumer:
    """Fake StreamingTurnConsumer: captures kwargs + records is_cancelled calls."""

    def __init__(self, result: StreamingTurnResult, sentences=()) -> None:
        self.result = result
        self.sentences = list(sentences)
        self.consume_kwargs: dict = {}
        self.is_cancelled_reads: list[bool] = []

    def __call__(self, **kwargs):
        self.consume_kwargs = dict(kwargs)
        return self

    async def consume(self, text, interaction_mode, reply_session):
        is_cancelled = self.consume_kwargs.get("is_cancelled")
        if is_cancelled is not None:
            self.is_cancelled_reads.append(bool(is_cancelled()))
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


def _live_controller(clock: FakeClock | None = None) -> TurnController:
    from joy_interaction_webui.turn_controller import TurnConfig

    cfg = TurnConfig.live()
    cfg.cooldown_ms = 10
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
# 1. live_enroll.enroll_pcm_verdict — three-guard order + boundaries
# ---------------------------------------------------------------------------


def test_enroll_verdict_not_in_phase_ignored():
    assert enroll_pcm_verdict(False, PCM, 0) == "ignored"


def test_enroll_verdict_empty_and_odd_length_invalid():
    assert enroll_pcm_verdict(True, b"", 0) == "invalid"
    assert enroll_pcm_verdict(True, b"\x00", 0) == "invalid"  # odd length


def test_enroll_verdict_overflow_full():
    # At capacity the append is rejected BEFORE any append (count unchanged).
    assert enroll_pcm_verdict(True, PCM, _ADDRESSEE_ENROLL_MAX_SEGMENTS) == "full"
    assert enroll_pcm_verdict(True, PCM, 99) == "full"


def test_enroll_verdict_ok_at_capacity_minus_one():
    assert enroll_pcm_verdict(True, PCM, _ADDRESSEE_ENROLL_MAX_SEGMENTS - 1) == "ok"


def test_enroll_verdict_guard_order_priority():
    # ignored wins over invalid/full; invalid wins over full (original order).
    assert enroll_pcm_verdict(False, b"", 99) == "ignored"
    assert enroll_pcm_verdict(True, b"", 99) == "invalid"


def test_feed_enroll_pcm_facade_overflow_keeps_count(monkeypatch):
    """Facade path: 4th segment after 3 buffered is dropped (count stays 3)."""
    sm, _vad, _asr = build_live()
    fake_detector = type("D", (), {"available": True})()
    sm._addressee = fake_detector
    assert sm.start_enroll() is True
    for _ in range(4):
        sm.feed_enroll_pcm(SEGMENT_1S)
    assert sm.enroll_segment_count == _ADDRESSEE_ENROLL_MAX_SEGMENTS


# ---------------------------------------------------------------------------
# 2. live_enroll.feed_enroll_vad — VAD-segmented collection state machine
# ---------------------------------------------------------------------------


def test_feed_enroll_vad_rising_edge_starts_buffer():
    seg = []
    cur = bytearray()
    in_seg = False
    in_seg, cur, seg, prev = feed_enroll_vad(
        pcm=PCM,
        vad_speech=True,
        prev_vad_speech=False,
        enroll_in_seg=in_seg,
        enroll_cur_segment=cur,
        enroll_segments=seg,
        logger=logging.getLogger("qa"),
    )
    assert in_seg is True
    assert bytes(cur) == PCM
    assert seg == []
    assert prev is True


def test_feed_enroll_vad_extend_and_short_falling_edge_dropped():
    seg = []
    cur = bytearray(PCM)
    in_seg = True
    in_seg, cur, seg, prev = feed_enroll_vad(
        pcm=PCM,
        vad_speech=True,
        prev_vad_speech=True,
        enroll_in_seg=in_seg,
        enroll_cur_segment=cur,
        enroll_segments=seg,
        logger=logging.getLogger("qa"),
    )
    assert bytes(cur) == PCM + PCM
    assert seg == []
    # falling edge with a <1s segment -> dropped, state reset
    in_seg, cur, seg, prev = feed_enroll_vad(
        pcm=b"\x00\x00",
        vad_speech=False,
        prev_vad_speech=True,
        enroll_in_seg=in_seg,
        enroll_cur_segment=cur,
        enroll_segments=seg,
        logger=logging.getLogger("qa"),
    )
    assert in_seg is False
    assert bytes(cur) == b""
    assert seg == []
    assert prev is False


def test_feed_enroll_vad_long_falling_edge_appends():
    seg = []
    cur = bytearray(SEGMENT_1S)  # exactly 1.0s -> accepted
    in_seg = True
    in_seg, cur, seg, prev = feed_enroll_vad(
        pcm=b"\x00\x00",
        vad_speech=False,
        prev_vad_speech=True,
        enroll_in_seg=in_seg,
        enroll_cur_segment=cur,
        enroll_segments=seg,
        logger=logging.getLogger("qa"),
    )
    assert seg == [SEGMENT_1S]
    assert in_seg is False
    assert prev is False


def test_feed_enroll_vad_max_three_overflow_ignored():
    seg = [SEGMENT_1S, SEGMENT_1S, SEGMENT_1S]
    cur = bytearray(SEGMENT_1S)
    in_seg = True
    in_seg, cur, seg, _ = feed_enroll_vad(
        pcm=b"\x00\x00",
        vad_speech=False,
        prev_vad_speech=True,
        enroll_in_seg=in_seg,
        enroll_cur_segment=cur,
        enroll_segments=seg,
        logger=logging.getLogger("qa"),
    )
    assert len(seg) == 3  # 4th segment discarded at capacity
    assert in_seg is False


# ---------------------------------------------------------------------------
# 3. live_frames.frame_window_from_env — clamping
# ---------------------------------------------------------------------------


def test_frame_window_env_default(monkeypatch):
    monkeypatch.delenv("LIVE_FRAME_WINDOW", raising=False)
    assert frame_window_from_env() == 6


def test_frame_window_env_zero_clamps_to_default(monkeypatch):
    monkeypatch.setenv("LIVE_FRAME_WINDOW", "0")
    assert frame_window_from_env() == 6


def test_frame_window_env_negative_clamps_to_default(monkeypatch):
    monkeypatch.setenv("LIVE_FRAME_WINDOW", "-5")
    assert frame_window_from_env() == 6


def test_frame_window_env_huge_ok(monkeypatch):
    monkeypatch.setenv("LIVE_FRAME_WINDOW", "100")
    assert frame_window_from_env() == 100


def test_frame_window_env_valid_override(monkeypatch):
    monkeypatch.setenv("LIVE_FRAME_WINDOW", "3")
    assert frame_window_from_env() == 3


def test_frame_window_env_malformed_raises_value_error(monkeypatch):
    # Parity with the ORIGINAL __init__: the int() conversion is unguarded,
    # so malformed env raises ValueError (deliberately preserved).
    monkeypatch.setenv("LIVE_FRAME_WINDOW", "abc")
    with pytest.raises(ValueError):
        frame_window_from_env()


# ---------------------------------------------------------------------------
# 4. live_proactive.proactive_switch_action — all 5 branches
# ---------------------------------------------------------------------------


def test_switch_action_reject_env_off():
    assert proactive_switch_action(enabled=True, env_gate=False, task_running=False) == "reject"
    assert proactive_switch_action(enabled=True, env_gate=False, task_running=True) == "reject"


def test_switch_action_start():
    assert proactive_switch_action(enabled=True, env_gate=True, task_running=False) == "start"


def test_switch_action_already_running():
    assert (
        proactive_switch_action(enabled=True, env_gate=True, task_running=True) == "already_running"
    )


def test_switch_action_cancel():
    assert proactive_switch_action(enabled=False, env_gate=True, task_running=True) == "cancel"


def test_switch_action_already_off():
    assert (
        proactive_switch_action(enabled=False, env_gate=True, task_running=False) == "already_off"
    )


def test_switch_action_string_coerced_to_bool():
    # bool("false") is True — same coercion as the original set_proactive.
    assert proactive_switch_action(enabled="false", env_gate=True, task_running=False) == "start"


# ---------------------------------------------------------------------------
# 5. live_llm.send_to_llm — cancelled branch: no broadcast, counters still bump
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_to_llm_cancelled_no_broadcast_but_epoch_bumps(monkeypatch):
    sm, _vad, _asr = build_live(controller=_live_controller())
    broadcasts: list = []
    sm.on_llm_response = lambda text, source: broadcasts.append((text, source))
    fake = FakeConsumer(_result(cancelled=True), sentences=[])
    monkeypatch.setattr(live_module, "StreamingTurnConsumer", fake)

    await sm._send_to_llm("你好", interaction_mode="live", stream=True)

    assert broadcasts == []  # cancelled -> finish_llm_turn never runs
    assert sm._llm_reply_epoch == 1  # turn-start bump still applied
    assert sm._tts_reply_seq == 1  # reply session still consumed
    assert sm._current_turn_reply_epoch == 0  # no finish_turn -> epoch not mirrored


@pytest.mark.asyncio
async def test_send_to_llm_retry_branch_calls_non_streaming(monkeypatch):
    sm, _vad, _asr = build_live(controller=_live_controller())
    sm.on_llm_response = lambda text, source: None
    fake = FakeConsumer(_result(needs_non_streaming_retry=True), sentences=[])
    monkeypatch.setattr(live_module, "StreamingTurnConsumer", fake)
    retried: list = []

    async def fake_retry(text, *, interaction_mode, reply_epoch, frames):
        retried.append((text, reply_epoch, frames))

    monkeypatch.setattr(sm, "_send_to_llm_non_streaming", fake_retry)

    await sm._send_to_llm(
        "你好", interaction_mode="live", stream=True, frames=[{"image_b64": "x", "ts_ms": 1.0}]
    )

    assert len(retried) == 1
    assert retried[0][0] == "你好"
    assert retried[0][1] == 1  # turn_reply_epoch
    assert retried[0][2] == [{"image_b64": "x", "ts_ms": 1.0}]


# ---------------------------------------------------------------------------
# 6. live_llm.finish_llm_turn — decision consumption branches
# ---------------------------------------------------------------------------


class FakeCtrl:
    """Duck-typed TurnController: records the calls finish_llm_turn makes."""

    def __init__(self, state=TurnState.LISTENING) -> None:
        self.state = state
        self.llm_token_calls: list[str] = []
        self.tts_started_calls = 0
        self.tts_finished_calls = 0

    def on_llm_token(self, token: str) -> None:
        self.llm_token_calls.append(token)

    def on_tts_started(self) -> None:
        self.tts_started_calls += 1
        self.state = TurnState.SPEAKING

    def on_tts_finished(self) -> None:
        self.tts_finished_calls += 1
        self.state = TurnState.LISTENING


def _run_finish(
    *,
    decision: str,
    response: str,
    spawned: bool = False,
    reply_epoch: int | None = 9,
    text: str = "你好",
    delegation_question: str | None = None,
    background_service: object | None = None,
):
    """Call the module-level finish_llm_turn with a FakeCtrl + captured callbacks."""
    from joy_interaction_webui import live_llm

    ctrl = FakeCtrl()
    history: list[tuple[str, str]] = []
    broadcasts: list[tuple[str, str]] = []

    async def wait_tts():
        await asyncio.sleep(0)

    async def go():
        return await live_llm.finish_llm_turn(
            text=text,
            response=response,
            decision=decision,
            delegation_question=delegation_question,
            reply_epoch=reply_epoch,
            llm_reply_epoch=5,
            background_service=background_service,
            conv_history=history,
            sentence_spawned_this_turn=spawned,
            on_llm_response=lambda t, source: broadcasts.append((t, source)),
            ctrl=ctrl,
            tts_turn_task=None,
            wait_tts_turn_done=wait_tts,
            logger=logging.getLogger("joyai.live_mode"),
        )

    current_epoch, tts_task = asyncio.run(go())
    return ctrl, history, broadcasts, current_epoch, tts_task


def test_finish_turn_response_with_spawned_sentences_live_voice():
    ctrl, history, broadcasts, epoch, task = _run_finish(
        decision="response", response="好的，我在。", spawned=True
    )
    assert ctrl.llm_token_calls == ["好的，我在。"]
    assert ctrl.tts_started_calls == 1
    assert ctrl.tts_finished_calls == 0  # waiting for sentence tasks
    assert broadcasts == [("好的，我在。", "live_voice")]
    assert history == [("user", "你好"), ("assistant", "好的，我在。")]
    assert epoch == 9
    assert task is not None  # tts_turn_task returned for the facade to store


def test_finish_turn_response_without_spawned_sentences_live_text():
    # Streaming did NOT spawn sentences (e.g. flush timing) -> live_text so the
    # browser synthesizes audio (BUG-1 regression guard).
    ctrl, _h, broadcasts, _e, task = _run_finish(
        decision="response", response="好的", spawned=False
    )
    assert broadcasts == [("好的", "live_text")]
    assert ctrl.tts_started_calls == 1
    assert ctrl.tts_finished_calls == 1  # nothing spoken -> immediate finish
    assert task is None


def test_finish_turn_silence_live_text_no_task():
    ctrl, history, broadcasts, epoch, task = _run_finish(
        decision="silence", response="", spawned=False
    )
    assert broadcasts == [("", "live_text")]
    assert history == [("user", "你好"), ("assistant", "")]
    assert ctrl.tts_started_calls == 1
    assert ctrl.tts_finished_calls == 1
    assert task is None
    assert epoch == 9


def test_finish_turn_delegation_routes_to_background_and_consumed_silent():
    calls: list = []
    bg = type(
        "BG",
        (),
        {
            "enabled": True,
            "_closed": False,
            "handle_foreground_response": lambda self, text, metrics=None: calls.append(
                (text, metrics)
            ),
        },
    )()
    ctrl, _h, broadcasts, _e, task = _run_finish(
        decision="delegation",
        response="（转交后台）",
        delegation_question="user asked weather",
        background_service=bg,
    )
    assert len(calls) == 1
    payload, metrics = calls[0]
    assert "</delegation> user asked weather" in payload
    assert metrics == {"user_prompt": "你好", "delegation_question": "user asked weather"}
    # delegation is consumed to silence -> nothing spoken, live_text broadcast
    assert broadcasts == [("（转交后台）", "live_text")]
    assert ctrl.tts_started_calls == 1 and ctrl.tts_finished_calls == 1
    assert task is None


def test_finish_turn_delegation_with_disabled_bg_not_routed():
    bg = type(
        "BG",
        (),
        {
            "enabled": False,
            "_closed": False,
            "handle_foreground_response": lambda self, text, metrics=None: (_ for _ in ()).throw(
                AssertionError("must not route")
            ),
        },
    )()
    ctrl, _h, broadcasts, _e, task = _run_finish(
        decision="delegation", response="", background_service=bg
    )
    assert broadcasts == [("", "live_text")]  # consumed to silence
    assert ctrl.tts_finished_calls == 1
    assert task is None


def test_finish_turn_not_for_me_live_text_and_listening():
    ctrl, history, broadcasts, _e, task = _run_finish(
        decision="not-for-me", response="", spawned=False
    )
    assert broadcasts == [("", "live_text")]
    assert history[-1] == ("assistant", "")  # still recorded in history
    assert ctrl.tts_started_calls == 1 and ctrl.tts_finished_calls == 1
    assert task is None


def test_finish_turn_reply_epoch_none_defaults_to_live_epoch():
    _ctrl, _h, _b, epoch, _t = _run_finish(decision="silence", response="", reply_epoch=None)
    assert epoch == 5  # llm_reply_epoch passed in


# ---------------------------------------------------------------------------
# 7. Facade wiring — returned counters/task are applied to LiveStateMachine
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finish_turn_facade_applies_epoch_and_task(monkeypatch):
    clock = FakeClock(1000.0)
    sm, vad, asr = build_live(controller=_live_controller(clock))
    monkeypatch.setattr(live_module, "time", clock)
    # Fake consumer makes the automatic commit round harmless (cancelled -> no
    # finish_turn, controller stays PROCESSING).
    fake = FakeConsumer(_result(cancelled=True), sentences=[])
    monkeypatch.setattr(live_module, "StreamingTurnConsumer", fake)

    # Drive the controller to PROCESSING exactly like the real feed_audio path.
    vad.set_speech(True)
    await sm.feed_audio(PCM)  # LISTENING -> USER_SPEAKING
    assert sm.turn_state == TurnState.USER_SPEAKING
    clock.now += 0.5
    asr.set_text("你好")
    await sm.feed_audio(PCM)
    vad.set_speech(False)
    clock.now += 2.0  # ASR endpoint -> PROCESSING + on_turn_commit -> fake cancelled round
    await sm.feed_audio(PCM)
    assert sm.turn_state == TurnState.PROCESSING

    broadcasts: list = []
    sm.on_llm_response = lambda text, source: broadcasts.append((text, source))
    sm._sentence_spawned_this_turn = True

    await sm._finish_llm_turn(
        text="你好",
        response="好的",
        decision="response",
        delegation_question=None,
        reply_epoch=4,
    )
    assert broadcasts == [("好的", "live_voice")]
    assert sm._current_turn_reply_epoch == 4  # returned epoch applied
    assert sm._tts_turn_task is not None  # returned task stored on the facade
    await sm._tts_turn_task
    assert sm.turn_state == TurnState.LISTENING  # wait_tts_turn_done path


# ---------------------------------------------------------------------------
# 8. Degradation point ① runtime proof — is_cancelled reads the LIVE flag
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_is_cancelled_lambda_reads_live_llm_stream_cancel(monkeypatch):
    sm, _vad, _asr = build_live(controller=_live_controller())
    sm.on_llm_response = lambda text, source: None
    fake = FakeConsumer(_result(full_response="ok", decision="response"), sentences=["ok"])
    monkeypatch.setattr(live_module, "StreamingTurnConsumer", fake)

    # Before the round, simulate a barge-in that set the cancel flag.
    sm._llm_stream_cancel = True
    await sm._send_to_llm("你好", interaction_mode="live", stream=True)

    # The facade reset _llm_stream_cancel=False BEFORE delegating, so the
    # consumer-visible callback must read False at consume time...
    assert fake.is_cancelled_reads == [False]

    # ...and mutating the live attribute AFTER the round starts flips the
    # SAME callable (closure over self._llm_stream_cancel), not a snapshot.
    captured = fake.consume_kwargs["is_cancelled"]
    sm._llm_stream_cancel = True
    assert captured() is True
    sm._llm_stream_cancel = False
    assert captured() is False


# ---------------------------------------------------------------------------
# 8. Shared-state write-back — _recent_frames reference shared with proactive
# ---------------------------------------------------------------------------


def test_recent_frames_reference_shared_with_proactive_frames_payload():
    sm, _vad, _asr = build_live()
    sm.handle_frame("b64-a", 100.0)
    sm.handle_frame("b64-b", 200.0)
    # proactive_loop receives recent_frames (the same deque) and samples [-1]
    payload = sm._frames_payload([sm._recent_frames[-1]])
    assert payload == [{"image_b64": "b64-b", "ts_ms": 200.0}]
    # the ring buffer is the live object, not a copy
    sm.handle_frame("b64-c", 300.0)
    assert sm.recent_frames[-1] == ("b64-c", 300.0)
    assert len(sm.recent_frames) == 3
