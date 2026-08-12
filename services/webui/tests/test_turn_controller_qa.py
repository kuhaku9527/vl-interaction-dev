"""QA supplementary edge-case tests for the unified Turn Controller prototype.

Written by QA (independent regression verification, Round 1). These tests
probe boundaries NOT covered by the engineer's 37-case suite:

  1. state machine edge cases:
       WARM_UP + speech_started (user speaks before wake confirm completes)
       COOLDOWN + high/low confidence (dual-threshold floor reclaim)
       HARD_INTERRUPTED repeated speech / double speech_stopped (连打)
       ENDED + stray events (speech vs LLM-token asymmetry)
       ERROR + reset + restart / ERROR without reset
       jarvis IDLE + speech_started (must wake first)
  2. SentenceBuffer boundaries:
       consecutive abbreviations ("Dr. Smith e.g. test.")
       pure punctuation strings / "..." ellipsis not a boundary
       default max_buffer_chars=500 force flush on a single long token
       "Dr." at end-of-line (dot skipped, newline flushes)
       case-insensitive abbreviation matching
       empty/None token safety
  3. timeouts:
       max_utterance in THINKING (agent-turn watchdog)
       max_utterance in SPEAKING (agent-turn watchdog)
       llm_total timeout while in PRE_SPEECH (filler in progress)
  4. config matrix:
       conversation preset barge_in_threshold(0.7) vs vad_threshold(0.5)
       actual preset values vs spec §3.3 matrix (documented in report)
  5. structured logging for the THINKING interrupt new path
  6. benign races: stray LLM token in LISTENING, cooldown_elapsed outside COOLDOWN
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
    SentenceBuffer,
    TurnConfig,
    TurnController,
    TurnState,
    TurnStateError,
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
    """Drive to SPEAKING via a normal committed turn (empty buffer = boundary)."""
    if ctrl.state == TurnState.LISTENING:
        ctrl.on_speech_started(conf)
        clock.advance(1000)
        ctrl.on_speech_stopped(600)  # -> PROCESSING
    ctrl.on_llm_token("Hello world.")  # -> THINKING (sentence flushed -> boundary)
    ctrl.on_tts_started()  # -> SPEAKING


def _drive_to_speaking_mid_sentence(ctrl: TurnController, clock: FakeClock, conf: float = 0.8) -> None:
    """Drive to SPEAKING with an un-flushed buffer (mid-sentence proxy)."""
    if ctrl.state == TurnState.LISTENING:
        ctrl.on_speech_started(conf)
        clock.advance(1000)
        ctrl.on_speech_stopped(600)  # -> PROCESSING
    ctrl.on_llm_token("Hello world")  # -> THINKING, no sentence end -> mid-sentence
    ctrl.on_tts_started()  # -> SPEAKING


def _drive_to_hard_interrupted(
    ctrl: TurnController, clock: FakeClock, conf: float = 0.9
) -> None:
    _drive_to_speaking(ctrl, clock, conf)
    ctrl.on_speech_started(conf)  # >= barge_in_threshold -> HARD_INTERRUPTED


# ---------------------------------------------------------------------------
# 1. State-machine edge cases
# ---------------------------------------------------------------------------


def test_warm_up_speech_started_raises():
    """User speaks before wake confirmation completes -> strict raise (no silent guard)."""
    ctrl = TurnController(TurnConfig.jarvis(), clock=FakeClock())
    ctrl.on_wake_word_detected()
    assert ctrl.state == TurnState.WARM_UP
    with pytest.raises(TurnStateError):
        ctrl.on_speech_started(0.8)


def test_cooldown_dual_threshold_behavior():
    """COOLDOWN: low conf ignored; high conf reclaims floor without on_barge_in."""
    clock = FakeClock()
    barge_ins: list[float] = []
    commits: list[int] = []
    ctrl = TurnController(
        TurnConfig.live(),
        on_barge_in=barge_ins.append,
        on_turn_commit=lambda: commits.append(1),
        clock=clock,
    )
    _drive_to_hard_interrupted(ctrl, clock)
    ctrl.on_speech_stopped(500)  # -> COOLDOWN
    assert ctrl.state == TurnState.COOLDOWN
    # Low confidence during cooldown: ignored, stays COOLDOWN.
    ctrl.on_speech_started(0.4)
    assert ctrl.state == TurnState.COOLDOWN
    assert barge_ins == [0.9]  # only the original hard interrupt fired
    # Insistent high confidence: reclaim floor -> USER_SPEAKING (no barge-in fired).
    ctrl.on_speech_started(0.9)
    assert ctrl.state == TurnState.USER_SPEAKING
    assert barge_ins == [0.9]
    assert ctrl.turn_committed is False  # per-turn commit flag reset on reclaim


def test_hard_interrupted_repeated_speech_started_stays():
    """连打: repeated speech_started while HARD_INTERRUPTED stays put, no duplicate callback."""
    clock = FakeClock()
    barge_ins: list[float] = []
    ctrl = TurnController(TurnConfig.live(), on_barge_in=barge_ins.append, clock=clock)
    _drive_to_hard_interrupted(ctrl, clock)
    assert ctrl.state == TurnState.HARD_INTERRUPTED
    ctrl.on_speech_started(0.5)  # user keeps talking
    assert ctrl.state == TurnState.HARD_INTERRUPTED
    ctrl.on_speech_started(0.6)
    assert ctrl.state == TurnState.HARD_INTERRUPTED
    assert barge_ins == [0.9]  # fired exactly once, on first entry
    assert ctrl.last_speech_conf == 0.6


def test_hard_interrupted_double_stop_then_cooldown():
    """HARD_INTERRUPTED: short silence stays; long silence -> COOLDOWN."""
    clock = FakeClock()
    ctrl = TurnController(TurnConfig.live(), clock=clock)
    _drive_to_hard_interrupted(ctrl, clock)
    ctrl.on_speech_stopped(100)  # 100 < barge_in_silence_timeout 300 -> still talking
    assert ctrl.state == TurnState.HARD_INTERRUPTED
    ctrl.on_speech_stopped(400)  # >= 300 -> COOLDOWN
    assert ctrl.state == TurnState.COOLDOWN


def test_ended_stray_speech_raises_but_llm_token_ignored():
    """ENDED: turn-affecting speech raises; stray LLM token is a benign race (log+ignore)."""
    ctrl = TurnController(TurnConfig.live(), clock=FakeClock())
    ctrl.end_turn()
    assert ctrl.state == TurnState.ENDED
    with pytest.raises(TurnStateError):
        ctrl.on_speech_started(0.8)
    with pytest.raises(TurnStateError):
        ctrl.on_speech_stopped(600)
    # Stray LLM token after ENDED is logged and ignored (benign race).
    ctrl.on_llm_token("stray")  # no raise
    assert ctrl.state == TurnState.ENDED


def test_error_reset_then_full_turn():
    """ERROR -> reset() -> LISTENING -> normal turn works again."""
    clock = FakeClock()
    commits: list[int] = []
    ctrl = TurnController(TurnConfig.live(), on_turn_commit=lambda: commits.append(1), clock=clock)
    ctrl.on_error()
    assert ctrl.state == TurnState.ERROR
    ctrl.reset()
    assert ctrl.state == TurnState.LISTENING
    ctrl.on_speech_started(0.8)
    clock.advance(1000)
    ctrl.on_speech_stopped(600)
    assert ctrl.state == TurnState.PROCESSING
    assert commits == [1]


def test_error_without_reset_speech_started_raises():
    """ERROR is recoverable only via reset(); speaking before reset is illegal."""
    ctrl = TurnController(TurnConfig.live(), clock=FakeClock())
    ctrl.on_error()
    with pytest.raises(TurnStateError):
        ctrl.on_speech_started(0.8)


def test_jarvis_idle_speech_started_raises():
    """jarvis with wake gate: user must wake first; speech in IDLE is illegal."""
    ctrl = TurnController(TurnConfig.jarvis(), clock=FakeClock())
    assert ctrl.state == TurnState.IDLE
    with pytest.raises(TurnStateError):
        ctrl.on_speech_started(0.8)


def test_jarvis_wake_full_flow_then_turn():
    """jarvis end-to-end: wake gate -> wake confirm -> full turn -> back to LISTENING."""
    clock = FakeClock()
    ctrl = TurnController(TurnConfig.jarvis(), clock=clock)
    ctrl.on_wake_word_detected()
    ctrl.on_wake_ready()
    assert ctrl.state == TurnState.LISTENING
    ctrl.on_speech_started(0.6)
    clock.advance(1000)
    ctrl.on_speech_stopped(600)  # -> PROCESSING
    ctrl.on_llm_token("Hello world.")  # -> THINKING
    ctrl.on_tts_started()  # -> SPEAKING
    ctrl.on_tts_finished()  # -> LISTENING
    assert ctrl.state == TurnState.LISTENING


def test_thinking_interrupt_any_confidence():
    """Key new path: THINKING interrupted at very low confidence -> immediate cancel."""
    clock = FakeClock()
    barge_ins: list[float] = []
    ctrl = TurnController(TurnConfig.live(), on_barge_in=barge_ins.append, clock=clock)
    ctrl.on_speech_started(0.8)
    clock.advance(1000)
    ctrl.on_speech_stopped(600)  # -> PROCESSING
    ctrl.on_llm_token("Hmm")  # -> THINKING
    ctrl.on_speech_started(0.05)  # any confidence cancels reasoning
    assert ctrl.state == TurnState.HARD_INTERRUPTED
    assert barge_ins == [0.05]


# ---------------------------------------------------------------------------
# 2. SentenceBuffer boundaries
# ---------------------------------------------------------------------------


def test_sentence_buffer_consecutive_abbreviations():
    buf = SentenceBuffer()
    assert buf.add_token("Dr. Smith e.g. test.") == "Dr. Smith e.g. test."


def test_sentence_buffer_pure_punctuation_no_premature_flush():
    buf = SentenceBuffer(min_sentence_length=10)
    assert buf.add_token("!!!") is None  # each '!' candidate too short -> merged
    assert buf.add_token("...") is None  # ellipsis never a boundary
    assert buf.add_token("???") is None
    assert not buf.is_empty
    # Everything stays buffered until flushed explicitly.
    assert buf.flush_remaining() == "!!!...???"


def test_sentence_buffer_default_max_force_flush_long_line():
    buf = SentenceBuffer()  # max_buffer_chars=500 default
    out = buf.add_token("x" * 501)
    assert out is not None
    assert len(out) == 501
    assert buf.is_empty


def test_sentence_buffer_ellipsis_not_boundary():
    buf = SentenceBuffer()
    assert buf.add_token("Wait... what?") == "Wait... what?"


def test_sentence_buffer_dr_at_line_end():
    """'Dr.' trailing dot is excluded; the newline (not the dot) flushes."""
    buf = SentenceBuffer()
    assert buf.add_token("Call Dr. Smith\n") == "Call Dr. Smith\n"


def test_sentence_buffer_abbreviation_case_insensitive():
    buf = SentenceBuffer()
    assert buf.add_token("DR. SMITH is here.") == "DR. SMITH is here."
    buf2 = SentenceBuffer()
    assert buf2.add_token("dr. smith is here.") == "dr. smith is here."


def test_sentence_buffer_internal_dots_u_s():
    buf = SentenceBuffer()
    assert buf.add_token("We live in the U.S. now.") == "We live in the U.S. now."


def test_sentence_buffer_empty_and_none_token_safe():
    buf = SentenceBuffer()
    assert buf.add_token("") is None
    assert buf.add_token(None) is None  # falsy token guard, no crash
    assert buf.is_empty


# ---------------------------------------------------------------------------
# 3. Timeout edge cases
# ---------------------------------------------------------------------------


def test_max_utterance_timeout_in_thinking():
    """Agent-turn watchdog: max_utterance while THINKING -> COOLDOWN + on_timeout."""
    clock = FakeClock()
    timeouts: list[int] = []
    ctrl = TurnController(TurnConfig.live(), on_timeout=lambda: timeouts.append(1), clock=clock)
    ctrl.on_speech_started(0.8)
    clock.advance(1000)
    ctrl.on_speech_stopped(600)  # -> PROCESSING
    ctrl.on_llm_token("Hmm")  # -> THINKING
    ctrl.on_max_utterance_timed_out()
    assert ctrl.state == TurnState.COOLDOWN
    assert timeouts == [1]


def test_max_utterance_timeout_in_speaking():
    """Agent-turn watchdog: max_utterance while SPEAKING -> COOLDOWN + on_timeout."""
    clock = FakeClock()
    timeouts: list[int] = []
    ctrl = TurnController(TurnConfig.live(), on_timeout=lambda: timeouts.append(1), clock=clock)
    _drive_to_speaking(ctrl, clock)
    ctrl.on_max_utterance_timed_out()
    assert ctrl.state == TurnState.COOLDOWN
    assert timeouts == [1]


def test_llm_total_timeout_in_pre_speech():
    """LLM total timeout while filler is playing (PRE_SPEECH) -> COOLDOWN + on_timeout."""
    clock = FakeClock()
    timeouts: list[int] = []
    fillers: list[int] = []
    ctrl = TurnController(
        TurnConfig.live(),
        on_timeout=lambda: timeouts.append(1),
        on_pre_speech_filler=lambda: fillers.append(1),
        clock=clock,
    )
    ctrl.on_speech_started(0.8)
    clock.advance(1000)
    ctrl.on_speech_stopped(600)  # -> PROCESSING
    ctrl.on_llm_token("Hmm")  # -> THINKING
    ctrl.on_llm_ttft_timed_out()  # -> PRE_SPEECH (filler)
    assert ctrl.state == TurnState.PRE_SPEECH
    assert fillers == [1]
    ctrl.on_llm_total_timed_out()  # filler overran total budget -> fallback
    assert ctrl.state == TurnState.COOLDOWN
    assert timeouts == [1]


# ---------------------------------------------------------------------------
# 4. Config matrix
# ---------------------------------------------------------------------------


def test_conversation_preset_threshold_gap():
    """conversation: vad 0.5 vs barge_in 0.7 — 0.6 is speech but NOT barge-in."""
    conv = TurnConfig.conversation()
    assert conv.vad_threshold == 0.5
    assert conv.barge_in_threshold == 0.7
    assert conv.vad_threshold < conv.barge_in_threshold

    clock = FakeClock()
    barge_ins: list[float] = []
    ctrl = TurnController(conv, on_barge_in=barge_ins.append, clock=clock)
    _drive_to_speaking(ctrl, clock)  # buffer empty -> at sentence boundary
    ctrl.on_speech_started(0.6)  # >= vad 0.5 but < barge 0.7 -> SOFT at boundary
    assert ctrl.state == TurnState.SOFT_INTERRUPTED
    assert barge_ins == []
    ctrl.on_resume_speaking()
    ctrl.on_speech_started(0.75)  # >= barge 0.7 -> HARD
    assert ctrl.state == TurnState.HARD_INTERRUPTED
    assert barge_ins == [0.75]


def test_conversation_mid_sentence_low_conf_not_hard_interrupted():
    """conversation: conf in (vad, barge) mid-sentence -> ignored, NOT soft/hard."""
    conv = TurnConfig.conversation()
    clock = FakeClock()
    ctrl = TurnController(conv, clock=clock)
    _drive_to_speaking_mid_sentence(ctrl, clock)
    assert not ctrl.sentence_buffer.is_empty  # mid-sentence
    ctrl.on_speech_started(0.6)  # >= vad, < barge, mid-sentence -> continue speaking
    assert ctrl.state == TurnState.SPEAKING


def test_preset_matrix_values_within_spec_ranges():
    """Actual preset values; the live silence deviation from spec §3.3 is reported separately."""
    live = TurnConfig.live()
    jarvis = TurnConfig.jarvis()
    conv = TurnConfig.conversation()
    # live vad within spec 0.7-0.9
    assert 0.7 <= live.vad_threshold <= 0.9
    # jarvis vad within 0.3-0.5 and silence within 200-400
    assert 0.3 <= jarvis.vad_threshold <= 0.5
    assert 200 <= jarvis.silence_timeout_ms <= 400
    # conversation vad within 0.4-0.6 and silence within 400-700
    assert 0.4 <= conv.vad_threshold <= 0.6
    assert 400 <= conv.silence_timeout_ms <= 700
    # scenario tags
    assert live.scenario == "live" and jarvis.scenario == "jarvis" and conv.scenario == "conversation"


# ---------------------------------------------------------------------------
# 5. Structured logging (约法三章 ③) for the new THINKING interrupt path
# ---------------------------------------------------------------------------


def test_structured_log_thinking_interrupt(caplog):
    clock = FakeClock()
    with caplog.at_level(logging.INFO, logger="joyai.turn_controller"):
        ctrl = TurnController(TurnConfig.live(), clock=clock)
        ctrl.on_speech_started(0.8)
        clock.advance(1000)
        ctrl.on_speech_stopped(600)
        ctrl.on_llm_token("Hmm")  # -> THINKING
        ctrl.on_speech_started(0.5)  # -> HARD_INTERRUPTED (new path)
    messages = [r.message for r in caplog.records]
    assert any(
        "[turn_controller] state: THINKING -> HARD_INTERRUPTED, event: on_speech_started" in m
        for m in messages
    )


# ---------------------------------------------------------------------------
# 6. Benign races are logged and ignored, not raised
# ---------------------------------------------------------------------------


def test_stray_llm_token_in_listening_ignored():
    ctrl = TurnController(TurnConfig.live(), clock=FakeClock())
    ctrl.on_llm_token("stray")  # no active LLM turn -> ignore, no raise
    assert ctrl.state == TurnState.LISTENING


def test_cooldown_elapsed_ignored_outside_cooldown():
    ctrl = TurnController(TurnConfig.live(), clock=FakeClock())
    ctrl.on_cooldown_elapsed()  # not in COOLDOWN -> log+ignore, no raise
    assert ctrl.state == TurnState.LISTENING
