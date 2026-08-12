"""Unit tests for the unified Turn Controller sandbox prototype.

Pure-logic tests: no ASR / LLM / TTS / VAD hardware is touched; all callbacks
are mock/plain functions and the controller is driven with an injectable fake
clock for deterministic timing.

Coverage (per task):
  1. state-transition coverage (live resident / jarvis wake gate / full turn /
     ENDED)
  2. barge-in: HARD path + recovery, SOFT at sentence boundary, THINKING
     interrupt (new path)
  3. COOLDOWN: post-interrupt guard, low-confidence speech ignored
  4. timeouts: max_utterance, LLM TTFT → PRE_SPEECH filler, LLM total
  5. SentenceBuffer: sentence flush, abbreviation false-positive exclusion,
     max_buffer_chars force flush, flush_on_timeout
  6. three preset factories and the scenario registry
  7. illegal transitions raise ``TurnStateError``
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

WEBUI_SRC = Path(__file__).resolve().parents[1] / "src"
if str(WEBUI_SRC) not in sys.path:
    sys.path.insert(0, str(WEBUI_SRC))

from joy_interaction_webui.turn_controller import (  # noqa: E402
    SCENARIO_PRESETS,
    SENTENCE_ENDINGS,
    LLMTokenEvent,
    PartialTranscriptEvent,
    SentenceBuffer,
    SpeechStartedEvent,
    SpeechStoppedEvent,
    TTSAudioEvent,
    TurnConfig,
    TurnController,
    TurnDecisionEvent,
    TurnEvent,
    TurnState,
    TurnStateError,
)

_ALL_STATES = frozenset(
    {
        "IDLE",
        "WARM_UP",
        "LISTENING",
        "USER_SPEAKING",
        "PROCESSING",
        "PRE_SPEECH",
        "SPEAKING",
        "THINKING",
        "HARD_INTERRUPTED",
        "SOFT_INTERRUPTED",
        "COOLDOWN",
        "ENDED",
        "ERROR",
    }
)


class FakeClock:
    """Deterministic monotonic clock in seconds; advance with ``advance(ms)``."""

    def __init__(self, start_s: float = 1000.0) -> None:
        self.t = start_s

    def __call__(self) -> float:
        return self.t

    def advance(self, ms: float) -> None:
        self.t += ms / 1000.0


def _drive_to_speaking(ctrl: TurnController, clock: FakeClock, conf: float = 0.8) -> None:
    """Drive a controller from its current state to SPEAKING via a normal turn."""
    if ctrl.state == TurnState.LISTENING:
        ctrl.on_speech_started(conf)
        clock.advance(1000)
        ctrl.on_speech_stopped(600)  # -> PROCESSING
    ctrl.on_llm_token("Hello world.")  # -> THINKING (sentence flushed)
    ctrl.on_tts_started()  # -> SPEAKING


def _drive_to_cooldown(ctrl: TurnController, clock: FakeClock, conf: float = 0.9) -> None:
    """Drive to COOLDOWN via a hard interrupt + speech stop."""
    _drive_to_speaking(ctrl, clock, conf)
    ctrl.on_speech_started(conf)  # conf >= barge_in_threshold -> HARD_INTERRUPTED
    ctrl.on_speech_stopped(500)  # silence >= barge_in_silence_timeout -> COOLDOWN


# ---------------------------------------------------------------------------
# 1. State-transition coverage
# ---------------------------------------------------------------------------


def test_turn_state_enum_contains_all_documented_states():
    names = {s.name for s in TurnState}
    assert names == _ALL_STATES


def test_live_initial_state_is_resident_listening():
    ctrl = TurnController(TurnConfig.live(), clock=FakeClock())
    assert ctrl.state == TurnState.LISTENING


def test_jarvis_wake_gate_flow():
    ctrl = TurnController(TurnConfig.jarvis(), clock=FakeClock())
    assert ctrl.state == TurnState.IDLE
    ctrl.on_wake_word_detected()
    assert ctrl.state == TurnState.WARM_UP
    ctrl.on_wake_ready()
    assert ctrl.state == TurnState.LISTENING


def test_full_turn_path_live():
    clock = FakeClock()
    commits: list[int] = []
    ctrl = TurnController(TurnConfig.live(), on_turn_commit=lambda: commits.append(1), clock=clock)
    ctrl.on_speech_started(0.8)
    assert ctrl.state == TurnState.USER_SPEAKING
    clock.advance(1000)
    ctrl.on_speech_stopped(600)
    assert ctrl.state == TurnState.PROCESSING
    assert commits == [1]
    ctrl.on_llm_token("Hello")
    assert ctrl.state == TurnState.THINKING
    ctrl.on_tts_started()
    assert ctrl.state == TurnState.SPEAKING
    ctrl.on_tts_finished()
    assert ctrl.state == TurnState.LISTENING


def test_end_turn_and_reset():
    ctrl = TurnController(TurnConfig.live(), clock=FakeClock())
    ctrl.end_turn()
    assert ctrl.state == TurnState.ENDED
    ctrl.reset()
    assert ctrl.state == TurnState.LISTENING


def test_end_turn_from_active_turn():
    ctrl = TurnController(TurnConfig.live(), clock=FakeClock())
    ctrl.on_speech_started(0.8)
    ctrl.end_turn()
    assert ctrl.state == TurnState.ENDED


def test_error_recovery_via_reset():
    ctrl = TurnController(TurnConfig.jarvis(), clock=FakeClock())
    ctrl.on_error()
    assert ctrl.state == TurnState.ERROR
    ctrl.reset()
    assert ctrl.state == TurnState.IDLE  # wake-gated -> back to IDLE


def test_final_transcript_commits_from_listening():
    commits: list[int] = []
    ctrl = TurnController(
        TurnConfig.live(), on_turn_commit=lambda: commits.append(1), clock=FakeClock()
    )
    ctrl.on_partial_transcript("hello there", is_final=True)
    assert ctrl.state == TurnState.PROCESSING
    assert commits == [1]


def test_utterance_too_short_returns_to_listening():
    clock = FakeClock()
    commits: list[int] = []
    ctrl = TurnController(TurnConfig.live(), on_turn_commit=lambda: commits.append(1), clock=clock)
    ctrl.on_speech_started(0.8)
    clock.advance(100)  # 100ms < min_utterance_ms=200
    ctrl.on_speech_stopped(600)
    assert ctrl.state == TurnState.LISTENING
    assert commits == []


# ---------------------------------------------------------------------------
# 2. Barge-in
# ---------------------------------------------------------------------------


def test_hard_barge_in_and_recovery():
    clock = FakeClock()
    barge_ins: list[float] = []
    ctrl = TurnController(TurnConfig.live(), on_barge_in=barge_ins.append, clock=clock)
    _drive_to_speaking(ctrl, clock)
    ctrl.on_speech_started(0.9)  # >= barge_in_threshold 0.75
    assert ctrl.state == TurnState.HARD_INTERRUPTED
    assert barge_ins == [0.9]
    ctrl.on_speech_stopped(500)  # >= barge_in_silence_timeout 300
    assert ctrl.state == TurnState.COOLDOWN
    ctrl.on_cooldown_elapsed()
    assert ctrl.state == TurnState.LISTENING


def test_soft_interrupt_at_sentence_boundary_and_resume():
    clock = FakeClock()
    barge_ins: list[float] = []
    ctrl = TurnController(TurnConfig.live(), on_barge_in=barge_ins.append, clock=clock)
    _drive_to_speaking(ctrl, clock)
    assert ctrl.sentence_buffer.is_empty  # at a sentence boundary
    ctrl.on_speech_started(0.5)  # < barge_in_threshold, at boundary -> SOFT
    assert ctrl.state == TurnState.SOFT_INTERRUPTED
    assert barge_ins == []  # soft interrupt does not fire on_barge_in
    ctrl.on_resume_speaking()
    assert ctrl.state == TurnState.SPEAKING


def test_low_conf_mid_sentence_speech_ignored_while_speaking():
    clock = FakeClock()
    ctrl = TurnController(TurnConfig.live(), clock=clock)
    ctrl.on_speech_started(0.8)
    clock.advance(1000)
    ctrl.on_speech_stopped(600)  # -> PROCESSING
    ctrl.on_llm_token("Hello world")  # -> THINKING, NO sentence ending -> mid-sentence
    ctrl.on_tts_started()  # -> SPEAKING
    assert not ctrl.sentence_buffer.is_empty
    ctrl.on_speech_started(0.5)  # low conf, mid-sentence -> stay SPEAKING
    assert ctrl.state == TurnState.SPEAKING


def test_soft_interrupt_escalates_to_hard():
    clock = FakeClock()
    barge_ins: list[float] = []
    ctrl = TurnController(TurnConfig.live(), on_barge_in=barge_ins.append, clock=clock)
    _drive_to_speaking(ctrl, clock)
    ctrl.on_speech_started(0.5)  # -> SOFT_INTERRUPTED
    assert ctrl.state == TurnState.SOFT_INTERRUPTED
    ctrl.on_speech_started(0.9)  # user keeps talking loudly -> escalate
    assert ctrl.state == TurnState.HARD_INTERRUPTED
    assert barge_ins == [0.9]


def test_thinking_interrupt_new_path():
    clock = FakeClock()
    barge_ins: list[float] = []
    ctrl = TurnController(TurnConfig.live(), on_barge_in=barge_ins.append, clock=clock)
    ctrl.on_speech_started(0.8)
    clock.advance(1000)
    ctrl.on_speech_stopped(600)  # -> PROCESSING
    ctrl.on_llm_token("Hmm")  # -> THINKING
    assert ctrl.state == TurnState.THINKING
    # Low confidence still cancels reasoning immediately (key new path).
    ctrl.on_speech_started(0.5)
    assert ctrl.state == TurnState.HARD_INTERRUPTED
    assert barge_ins == [0.5]


# ---------------------------------------------------------------------------
# 3. COOLDOWN
# ---------------------------------------------------------------------------


def test_cooldown_ignores_low_confidence_speech():
    clock = FakeClock()
    ctrl = TurnController(TurnConfig.live(), clock=clock)
    _drive_to_cooldown(ctrl, clock)
    assert ctrl.state == TurnState.COOLDOWN
    ctrl.on_speech_started(0.5)  # < barge_in_threshold -> ignored
    assert ctrl.state == TurnState.COOLDOWN
    ctrl.on_speech_started(0.9)  # insistent -> reclaim floor
    assert ctrl.state == TurnState.USER_SPEAKING


# ---------------------------------------------------------------------------
# 4. Timeouts
# ---------------------------------------------------------------------------


def test_max_utterance_timeout():
    clock = FakeClock()
    timeouts: list[int] = []
    ctrl = TurnController(TurnConfig.live(), on_timeout=lambda: timeouts.append(1), clock=clock)
    ctrl.on_speech_started(0.8)
    assert ctrl.state == TurnState.USER_SPEAKING
    ctrl.on_max_utterance_timed_out()
    assert ctrl.state == TurnState.COOLDOWN
    assert timeouts == [1]


def test_llm_ttft_timeout_triggers_pre_speech_filler():
    clock = FakeClock()
    fillers: list[int] = []
    ctrl = TurnController(
        TurnConfig.live(), on_pre_speech_filler=lambda: fillers.append(1), clock=clock
    )
    ctrl.on_speech_started(0.8)
    clock.advance(1000)
    ctrl.on_speech_stopped(600)  # -> PROCESSING
    ctrl.on_llm_token("Hmm")  # -> THINKING
    ctrl.on_llm_ttft_timed_out()
    assert ctrl.state == TurnState.PRE_SPEECH
    assert fillers == [1]
    ctrl.on_llm_token("Let me")  # prefill complete -> back to THINKING
    assert ctrl.state == TurnState.THINKING


def test_llm_ttft_timeout_respects_filler_disabled():
    clock = FakeClock()
    fillers: list[int] = []
    config = TurnConfig.live()
    config.pre_speech_filler_enabled = False
    ctrl = TurnController(config, on_pre_speech_filler=lambda: fillers.append(1), clock=clock)
    ctrl.on_speech_started(0.8)
    clock.advance(1000)
    ctrl.on_speech_stopped(600)
    ctrl.on_llm_token("Hmm")  # -> THINKING
    ctrl.on_llm_ttft_timed_out()
    assert ctrl.state == TurnState.THINKING  # stays; no filler
    assert fillers == []


def test_llm_total_timeout():
    clock = FakeClock()
    timeouts: list[int] = []
    ctrl = TurnController(TurnConfig.live(), on_timeout=lambda: timeouts.append(1), clock=clock)
    ctrl.on_speech_started(0.8)
    clock.advance(1000)
    ctrl.on_speech_stopped(600)
    ctrl.on_llm_token("Hmm")  # -> THINKING
    ctrl.on_llm_total_timed_out()
    assert ctrl.state == TurnState.COOLDOWN
    assert timeouts == [1]


# ---------------------------------------------------------------------------
# 5. SentenceBuffer
# ---------------------------------------------------------------------------


def test_sentence_buffer_flush_on_sentence_end():
    buf = SentenceBuffer()
    assert buf.add_token("Hello world.") == "Hello world."
    assert buf.is_empty
    assert buf.flush_remaining() is None


def test_sentence_buffer_multiple_sentences_flush_individually():
    buf = SentenceBuffer()
    assert buf.add_token("First one. Second two.") == "First one."
    assert buf.text == " Second two."  # leading space belongs to the next sentence
    assert buf.flush_remaining() == " Second two."


def test_sentence_buffer_abbreviation_false_positives_do_not_trigger():
    buf = SentenceBuffer()
    assert buf.add_token("Dr. Smith is here.") == "Dr. Smith is here."
    buf2 = SentenceBuffer()
    assert buf2.add_token("I live in the U.S. now.") == "I live in the U.S. now."
    buf3 = SentenceBuffer()
    assert buf3.add_token("Use e.g. examples here.") == "Use e.g. examples here."
    buf4 = SentenceBuffer()
    assert buf4.add_token("At 9 a.m. the meeting starts.") == "At 9 a.m. the meeting starts."


def test_sentence_buffer_short_sentence_merges_with_following_text():
    buf = SentenceBuffer(min_sentence_length=10)
    assert buf.add_token("Hi.") is None  # too short, do not flush
    assert buf.add_token(" How are you?") == "Hi. How are you?"


def test_sentence_buffer_max_buffer_chars_force_flush():
    buf = SentenceBuffer(max_buffer_chars=50)
    out = buf.add_token("a" * 60)
    assert out is not None
    assert len(out) == 60
    assert buf.is_empty


def test_sentence_buffer_flush_on_timeout():
    clock = FakeClock()
    buf = SentenceBuffer(flush_on_timeout_ms=500, clock=clock)
    assert buf.add_token("no ending yet") is None
    assert not buf.should_flush_on_timeout()
    clock.advance(600)
    assert buf.should_flush_on_timeout()
    assert buf.flush_remaining() == "no ending yet"
    assert buf.is_empty
    assert not buf.should_flush_on_timeout()


def test_sentence_ending_regex_matches_documented_set():
    for ch in ".!?。！？\n":
        assert SENTENCE_ENDINGS.fullmatch(ch)


# ---------------------------------------------------------------------------
# 6. Preset factories / scenario matrix
# ---------------------------------------------------------------------------


def test_preset_scenarios_differ():
    live = TurnConfig.live()
    jarvis = TurnConfig.jarvis()
    conv = TurnConfig.conversation()
    assert live.wake_gate_enabled is False
    assert jarvis.wake_gate_enabled is True
    assert conv.wake_gate_enabled is False
    assert live.proactive_speak_enabled is True
    assert jarvis.proactive_speak_enabled is False
    assert conv.proactive_speak_enabled is True
    assert live.vad_threshold > jarvis.vad_threshold
    assert conv.vad_threshold == 0.5
    assert jarvis.silence_timeout_ms == 300
    assert live.silence_timeout_ms == 500


def test_scenario_presets_registry():
    assert set(SCENARIO_PRESETS) == {"live", "jarvis", "conversation"}
    assert SCENARIO_PRESETS["live"] == TurnConfig.live()
    assert SCENARIO_PRESETS["jarvis"] == TurnConfig.jarvis()
    assert SCENARIO_PRESETS["conversation"] == TurnConfig.conversation()


def test_describe_config_includes_scenario_and_key_params():
    desc = TurnConfig.jarvis().describe_config()
    assert "jarvis" in desc
    assert "wake_gate_enabled=True" in desc
    assert "proactive_speak_enabled=False" in desc
    assert "vad_threshold=0.40" in desc


# ---------------------------------------------------------------------------
# 7. Illegal transitions raise TurnStateError
# ---------------------------------------------------------------------------


def test_illegal_speech_stopped_in_listening():
    ctrl = TurnController(TurnConfig.live(), clock=FakeClock())
    with pytest.raises(TurnStateError):
        ctrl.on_speech_stopped(600)


def test_illegal_resume_speaking_outside_soft_interrupt():
    clock = FakeClock()
    ctrl = TurnController(TurnConfig.live(), clock=clock)
    ctrl.on_speech_started(0.8)
    clock.advance(1000)
    ctrl.on_speech_stopped(600)  # -> PROCESSING
    with pytest.raises(TurnStateError):
        ctrl.on_resume_speaking()


def test_illegal_tts_finished_when_not_speaking():
    ctrl = TurnController(TurnConfig.live(), clock=FakeClock())
    with pytest.raises(TurnStateError):
        ctrl.on_tts_finished()


def test_illegal_wake_word_when_gate_disabled():
    ctrl = TurnController(TurnConfig.live(), clock=FakeClock())
    with pytest.raises(TurnStateError):
        ctrl.on_wake_word_detected()


def test_illegal_wake_ready_outside_warm_up():
    ctrl = TurnController(TurnConfig.live(), clock=FakeClock())
    with pytest.raises(TurnStateError):
        ctrl.on_wake_ready()


def test_illegal_end_turn_twice():
    ctrl = TurnController(TurnConfig.live(), clock=FakeClock())
    ctrl.end_turn()
    with pytest.raises(TurnStateError):
        ctrl.end_turn()


# ---------------------------------------------------------------------------
# Events dataclasses
# ---------------------------------------------------------------------------


def test_turn_event_dataclasses():
    e = TurnEvent(timestamp_ms=1, session_id="s1")
    assert e.timestamp_ms == 1 and e.session_id == "s1"
    se = SpeechStartedEvent(timestamp_ms=2, session_id="s1", confidence=0.8)
    assert se.confidence == 0.8
    st = SpeechStoppedEvent(timestamp_ms=3, session_id="s1", silence_duration_ms=400)
    assert st.silence_duration_ms == 400
    pt = PartialTranscriptEvent(timestamp_ms=4, session_id="s1", text="hi", is_final=True)
    assert pt.is_final and pt.text == "hi"
    td = TurnDecisionEvent(timestamp_ms=5, session_id="s1", action="commit")
    assert td.action == "commit"
    with pytest.raises(ValueError):
        TurnDecisionEvent(timestamp_ms=5, session_id="s1", action="bogus")
    tok = LLMTokenEvent(timestamp_ms=6, session_id="s1", token="Hello")
    assert tok.token == "Hello"
    tts = TTSAudioEvent(timestamp_ms=7, session_id="s1", is_first=True, is_last=False)
    assert tts.is_first is True and tts.is_last is False


# ---------------------------------------------------------------------------
# Structured logging (约法三章 ③)
# ---------------------------------------------------------------------------


def test_structured_transition_logging(caplog):
    clock = FakeClock()
    with caplog.at_level(logging.INFO, logger="joyai.turn_controller"):
        ctrl = TurnController(TurnConfig.live(), clock=clock)
        ctrl.on_speech_started(0.8)
    messages = [r.message for r in caplog.records]
    assert any(
        "[turn_controller] state: LISTENING -> USER_SPEAKING, event: on_speech_started" in m
        for m in messages
    )
