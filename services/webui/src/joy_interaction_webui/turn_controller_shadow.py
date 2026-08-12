"""Turn Controller shadow adapter — Phase A: observe-only (影子模式).

Wraps a real ``TurnController`` (jarvis preset) and mirrors the *live*
``jarvis_mode.JarvisStateMachine`` so the two state chains can be compared
for alignment without changing any jarvis behavior.

Design contract (Phase A)
-------------------------
* Observe-only: every jarvis event is mapped to a turn_controller event and
  the controller is driven through its *own* FSM (public events only, never
  ``ctrl.state`` assignments). Nothing jarvis reads comes from this object.
* Fail-open: any internal error is logged via ``logger.warning`` and
  swallowed. A broken shadow must never break jarvis — mirrors the
  ``smart_turn_adapter`` fail-open tradition.
* Env gate: ``JARVIS_TURN_SHADOW_ENABLED`` (default off). When disabled the
  wrapper is a pure no-op.
* Structured logging: every comparison line is ``logger.info`` prefixed
  ``[turn-shadow]``; no defensive None checks; exceptions are explicit.

The jarvis -> turn_controller mapping (``JARVIS_TO_TURN_EXPECTED``) follows
the integration spec §2 Phase A/B table:

    KWS_LISTENING    -> LISTENING (jarvis preset wake gate exercised)
    WAIT_ASR_CONFIRM -> WARM_UP
    WAKE_DETECTED    -> WARM_UP
    DIALOG_ACTIVE    -> USER_SPEAKING / PROCESSING / THINKING / SPEAKING
    TTS_PAUSED       -> HARD_INTERRUPTED -> COOLDOWN -> LISTENING
    EXIT_DETECTED    -> ENDED
    ERROR            -> ERROR
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any

from .turn_controller import TurnController, TurnState

logger = logging.getLogger("joyai.turn_shadow")

#: jarvis state -> turn_controller states accepted as "aligned". Observation
#: only: Phase A never arbitrates; DIALOG_ACTIVE spans the whole user-turn
#: sub-cycle and TTS_PAUSED spans the interrupt -> cooldown zone.
JARVIS_TO_TURN_EXPECTED: dict[str, frozenset[str]] = {
    "KWS_LISTENING": frozenset({"LISTENING"}),
    "WAIT_ASR_CONFIRM": frozenset({"WARM_UP"}),
    "WAKE_DETECTED": frozenset({"WARM_UP"}),
    "DIALOG_ACTIVE": frozenset({"USER_SPEAKING", "PROCESSING", "THINKING", "SPEAKING"}),
    "TTS_PAUSED": frozenset({"HARD_INTERRUPTED", "COOLDOWN"}),
    "EXIT_DETECTED": frozenset({"ENDED"}),
    "ERROR": frozenset({"ERROR"}),
}

#: Pseudo start markers used by tests / session bootstrap — never driven.
_PSEUDO_STATES: frozenset[str] = frozenset({"", "<init>", "<start>", "<session_start>"})

#: Silence (ms) fed to ``on_speech_stopped`` when the shadow interrupts.
#: >= jarvis preset ``barge_in_silence_timeout_ms`` (300) so the interrupt
#: resolves to COOLDOWN deterministically (no clock dependency).
_SHADOW_SILENCE_MS: int = 500

#: Confidence used to start a shadow user utterance (>= jarvis
#: ``vad_threshold`` 0.4, < ``barge_in_threshold`` 0.75 so LISTENING starts
#: cleanly).
_SHADOW_SPEECH_CONF: float = 0.8

#: Confidence used to simulate a barge-in (>= jarvis ``barge_in_threshold``
#: 0.75 so SPEAKING/THINKING -> HARD_INTERRUPTED deterministically).
_SHADOW_BARGE_CONF: float = 0.9


class ShadowTurnController:
    """Mirror jarvis state transitions into a wrapped TurnController.

    The wrapped controller is driven exclusively through its public event
    methods so every shadow transition is legal in the controller's own FSM.
    Alignment is computed per jarvis transition against
    ``JARVIS_TO_TURN_EXPECTED`` and logged as ``[turn-shadow] ...`` lines.

    Phase A is strictly observational: no jarvis code reads back from this
    object, and every public entry point is fail-open (errors are logged and
    swallowed).
    """

    def __init__(
        self,
        turn_controller: TurnController | None,
        *,
        shadow_enabled: bool = True,
        logger: logging.Logger | None = None,
    ) -> None:
        """Wrap ``turn_controller``; when disabled every method is a no-op.

        Args:
            turn_controller: the TurnController (jarvis preset) to drive in
                shadow; may be ``None`` when ``shadow_enabled`` is False.
            shadow_enabled: master switch; False -> pure no-op wrapper.
            logger: optional logger; defaults to ``joyai.turn_shadow``.
        """
        self._turn = turn_controller
        self._enabled = shadow_enabled
        self._log = logger or logging.getLogger("joyai.turn_shadow")
        self._current_jarvis_state: str = ""
        self._speech_active: bool = False
        #: (jarvis_state, expected_set, turn_state, match, reason)
        self._observations: list[tuple[str, frozenset[str], str, bool, str]] = []
        self._match_count: int = 0
        self._mismatch_count: int = 0

    # -- introspection -----------------------------------------------------

    @property
    def enabled(self) -> bool:
        """True when the shadow is active (non-no-op)."""
        return self._enabled

    @property
    def jarvis_chain(self) -> list[str]:
        """Observed jarvis state names, one per recorded observation."""
        return [obs[0] for obs in self._observations]

    @property
    def shadow_chain(self) -> list[str]:
        """turn_controller state names at each recorded observation."""
        return [obs[2] for obs in self._observations]

    # -- jarvis event injection -------------------------------------------

    def on_jarvis_transition(self, old_state: str, new_state: str, reason: str) -> None:
        """Observe a jarvis state transition and mirror it into the shadow.

        On the very first observation the controller is first aligned with
        the (already left) old state so the shadow chain starts from the
        session's initial state; then it is driven toward the zone the new
        state maps to and a match/mismatch is recorded.

        Fail-open: never raises into the jarvis caller.
        """
        if not self._enabled or self._turn is None:
            return
        try:
            old = self._state_name(old_state)
            new = self._state_name(new_state)
            if self._current_jarvis_state == "" and old not in _PSEUDO_STATES:
                # Bootstrap: first real transition, so also record the initial
                # jarvis state (the controller starts IDLE under the wake gate).
                self._drive_to_jarvis_state(old)
                self._observe(old, reason=f"initial:{reason}")
            self._current_jarvis_state = new
            self._drive_to_jarvis_state(new)
            self._observe(new, reason=reason)
        except Exception as exc:  # noqa: BLE001 - fail-open, never break jarvis
            self._log.warning(
                "[turn-shadow] on_jarvis_transition(%s -> %s) failed: %s",
                old_state,
                new_state,
                exc,
            )

    def on_audio_activity(self, speech: bool, confidence: float) -> None:
        """Mirror VAD speech/silence edges (L1) into the shadow controller.

        A rising edge maps to ``on_speech_started``; a falling edge to
        ``on_speech_stopped``. Fail-open: never raises into the caller.
        """
        if not self._enabled or self._turn is None:
            return
        try:
            if speech and not self._speech_active:
                self._turn.on_speech_started(confidence)
                self._speech_active = True
            elif not speech and self._speech_active:
                self._turn.on_speech_stopped(_SHADOW_SILENCE_MS)
                self._speech_active = False
            self._log.info(
                "[turn-shadow] event=on_audio_activity speech=%s conf=%.2f turn=%s",
                speech,
                confidence,
                self._turn.state.name,
            )
        except Exception as exc:  # noqa: BLE001 - fail-open
            self._log.warning("[turn-shadow] on_audio_activity failed: %s", exc)

    def on_asr_partial(self, text: str, is_final: bool) -> None:
        """Mirror an ASR partial/final transcript (L2) into the controller."""
        if not self._enabled or self._turn is None:
            return
        try:
            self._turn.on_partial_transcript(text, is_final)
            self._log.info(
                "[turn-shadow] event=on_asr_partial is_final=%s turn=%s",
                is_final,
                self._turn.state.name,
            )
        except Exception as exc:  # noqa: BLE001 - fail-open
            self._log.warning("[turn-shadow] on_asr_partial failed: %s", exc)

    def on_llm_token(self, token: str) -> None:
        """Mirror a streamed LLM token (L3) into the controller."""
        if not self._enabled or self._turn is None:
            return
        try:
            self._turn.on_llm_token(token)
            self._log.info(
                "[turn-shadow] event=on_llm_token turn=%s",
                self._turn.state.name,
            )
        except Exception as exc:  # noqa: BLE001 - fail-open
            self._log.warning("[turn-shadow] on_llm_token failed: %s", exc)

    def on_tts_event(self, started: bool) -> None:
        """Mirror a TTS audio lifecycle event into the controller."""
        if not self._enabled or self._turn is None:
            return
        try:
            if started:
                self._turn.on_tts_started()
            else:
                self._turn.on_tts_finished()
            self._log.info(
                "[turn-shadow] event=on_tts_event started=%s turn=%s",
                started,
                self._turn.state.name,
            )
        except Exception as exc:  # noqa: BLE001 - fail-open
            self._log.warning("[turn-shadow] on_tts_event failed: %s", exc)

    # -- alignment ---------------------------------------------------------

    def compare(self) -> dict[str, Any]:
        """Return alignment stats: ``total`` / ``match`` / ``mismatch``.

        ``align_rate`` is match/total (1.0 when no observations have been
        recorded yet — vacuous, and visible via ``total == 0``).
        """
        total = self._match_count + self._mismatch_count
        rate = self._match_count / total if total else 1.0
        return {
            "total": total,
            "match": self._match_count,
            "mismatch": self._mismatch_count,
            "align_rate": round(rate, 4),
        }

    def summary(self) -> dict[str, Any]:
        """Log and return the cumulative alignment summary (对齐率)."""
        stats = self.compare()
        self._log.info(
            "[turn-shadow] summary: total=%d match=%d mismatch=%d align_rate=%.2f%%",
            stats["total"],
            stats["match"],
            stats["mismatch"],
            stats["align_rate"] * 100.0,
        )
        return stats

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _state_name(state: object) -> str:
        """Normalize a state to its bare enum name (``str`` or ``Enum``)."""
        if state is None:
            return ""
        if isinstance(state, Enum):
            return state.name
        name = str(state)
        if "." in name:
            name = name.rsplit(".", 1)[-1]
        return name

    def _observe(self, jarvis_state: str, *, reason: str) -> None:
        """Record one alignment observation and log the structured line."""
        expected = JARVIS_TO_TURN_EXPECTED.get(jarvis_state, frozenset())
        actual = self._turn.state.name if self._turn is not None else ""
        match = actual in expected
        self._observations.append((jarvis_state, expected, actual, match, reason))
        if match:
            self._match_count += 1
        else:
            self._mismatch_count += 1
        self._log.info(
            "[turn-shadow] jarvis=%s turn=%s match=%s reason=%r",
            jarvis_state,
            actual,
            match,
            reason,
        )

    def _drive_to_jarvis_state(self, target: str) -> None:
        """Drive the wrapped controller toward the zone mapped from ``target``.

        Uses only public controller events (never assigns ``ctrl.state``), so
        every step respects the controller's own transition legality. Steps
        that are illegal in the current state are simply skipped — the
        controller stays where it is and the alignment comparison then
        reports an honest mismatch.
        """
        c = self._turn
        if c is None:
            return
        if target == "KWS_LISTENING":
            self._drive_to_listening()
        elif target in ("WAIT_ASR_CONFIRM", "WAKE_DETECTED"):
            # A new wake cycle: the controller must return to WARM_UP.
            if c.state not in (TurnState.IDLE, TurnState.WARM_UP):
                c.reset()  # -> IDLE (legal from any state)
            if c.state == TurnState.IDLE:
                c.on_wake_word_detected()  # -> WARM_UP
        elif target == "DIALOG_ACTIVE":
            self._drive_to_dialog_active()
        elif target == "TTS_PAUSED":
            if c.state in (TurnState.ENDED, TurnState.ERROR):
                c.reset()  # -> IDLE
            if c.state == TurnState.IDLE:
                c.on_wake_word_detected()  # -> WARM_UP
                c.on_wake_ready()  # -> LISTENING
            if c.state in (TurnState.SPEAKING, TurnState.PRE_SPEECH, TurnState.THINKING):
                c.on_speech_started(_SHADOW_BARGE_CONF)  # -> HARD_INTERRUPTED
            elif c.state == TurnState.PROCESSING:
                c.on_llm_token("ok")
                c.on_tts_started()
                c.on_speech_started(_SHADOW_BARGE_CONF)
            elif c.state == TurnState.LISTENING:
                c.on_speech_started(_SHADOW_BARGE_CONF)  # -> USER_SPEAKING
        elif target == "EXIT_DETECTED" and c.state != TurnState.ENDED:
            c.end_turn()  # any active state -> ENDED
        elif target == "ERROR" and c.state != TurnState.ERROR:
            c.on_error()  # any state -> ERROR
        # Unknown targets drive nothing; the mismatch is reported in _observe.

    def _drive_to_listening(self) -> None:
        """Align the controller with jarvis KWS_LISTENING (turn LISTENING)."""
        c = self._turn
        if c is None:
            return
        if c.state in (TurnState.ENDED, TurnState.ERROR):
            c.reset()  # -> IDLE
        if c.state == TurnState.IDLE:
            c.on_wake_word_detected()  # -> WARM_UP
            c.on_wake_ready()  # -> LISTENING
        elif c.state == TurnState.WARM_UP:
            c.on_wake_ready()  # -> LISTENING
        elif c.state == TurnState.COOLDOWN:
            c.on_cooldown_elapsed()  # -> LISTENING
        elif c.state in (TurnState.HARD_INTERRUPTED, TurnState.SOFT_INTERRUPTED):
            c.on_speech_stopped(_SHADOW_SILENCE_MS)  # -> COOLDOWN
            c.on_cooldown_elapsed()  # -> LISTENING
        elif c.state == TurnState.USER_SPEAKING:
            self._finish_agent_turn()  # -> ... -> LISTENING
        elif c.state == TurnState.PROCESSING:
            c.on_llm_token("ok")
            c.on_tts_started()
            c.on_tts_finished()
        elif c.state == TurnState.THINKING:
            c.on_tts_started()
            c.on_tts_finished()
        elif c.state == TurnState.SPEAKING:
            c.on_tts_finished()  # -> LISTENING
        # LISTENING: already aligned.

    def _drive_to_dialog_active(self) -> None:
        """Drive a full user-turn sub-cycle ending in SPEAKING (dialog zone).

        jarvis DIALOG_ACTIVE covers the whole user turn: VAD speech, ASR
        commit, LLM reasoning and TTS playback. The shadow replays that
        sub-cycle with controller events so the controller ends in SPEAKING —
        the state from which a jarvis TTS_PAUSED interrupt maps cleanly.
        """
        c = self._turn
        if c is None:
            return
        if c.state in (TurnState.ENDED, TurnState.ERROR):
            c.reset()  # -> IDLE
        if c.state == TurnState.IDLE:
            c.on_wake_word_detected()  # -> WARM_UP
        if c.state == TurnState.WARM_UP:
            c.on_wake_ready()  # -> LISTENING
        if c.state == TurnState.COOLDOWN:
            c.on_cooldown_elapsed()  # -> LISTENING
        if c.state in (TurnState.HARD_INTERRUPTED, TurnState.SOFT_INTERRUPTED):
            c.on_speech_stopped(_SHADOW_SILENCE_MS)  # -> COOLDOWN
            c.on_cooldown_elapsed()  # -> LISTENING
        if c.state == TurnState.LISTENING:
            c.on_speech_started(_SHADOW_SPEECH_CONF)  # -> USER_SPEAKING
        if c.state == TurnState.USER_SPEAKING:
            c.on_partial_transcript("shadow utterance", is_final=True)  # -> PROCESSING
        if c.state == TurnState.PROCESSING:
            c.on_llm_token("shadow reply")  # -> THINKING
        if c.state == TurnState.THINKING:
            c.on_tts_started()  # -> SPEAKING
        # SPEAKING (or a later dialog-zone state): already aligned.

    def _finish_agent_turn(self) -> None:
        """Drive USER_SPEAKING -> ... -> LISTENING (agent turn completes)."""
        c = self._turn
        if c is None:
            return
        if c.state == TurnState.USER_SPEAKING:
            c.on_partial_transcript("ok", is_final=True)  # -> PROCESSING
        if c.state == TurnState.PROCESSING:
            c.on_llm_token("ok")  # -> THINKING
        if c.state == TurnState.THINKING:
            c.on_tts_started()  # -> SPEAKING
        if c.state == TurnState.SPEAKING:
            c.on_tts_finished()  # -> LISTENING


__all__ = ["JARVIS_TO_TURN_EXPECTED", "ShadowTurnController"]
