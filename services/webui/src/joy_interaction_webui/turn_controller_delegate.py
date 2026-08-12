"""Turn Controller delegate adapter — Phase B: active arbitration (委托模式).

Wraps a real ``TurnController`` (jarvis preset) and *arbitrates* jarvis's
DIALOG_ACTIVE turn rhythm. Unlike the Phase A shadow (``turn_controller_shadow``,
observe-only), the delegate is the decision source for turn commit inside
DIALOG_ACTIVE. KWS wake chain, WAIT_ASR_CONFIRM, EXIT_WORDS and the LLM/TTS
engines remain owned by jarvis (mode extensions, not delegated).

Design contract (Phase B)
-------------------------
* Active arbitration: jarvis feeds the controller through its *own* public
  event methods (never ``ctrl.state`` assignments). The controller's
  callbacks (``on_turn_commit`` / ``on_barge_in`` / ``on_timeout`` /
  ``on_pre_speech_filler``) are translated into *pending action flags* that
  the jarvis caller drains after each feed — the controller stays a pure
  sync FSM while jarvis stays async and reuses its existing action paths
  (``_send_to_llm``, TTS pause, ``_transition_to``).
* Env gate: ``JARVIS_TURN_DELEGATE_ENABLED`` (default off). When disabled
  jarvis never constructs this adapter and behavior is byte-for-byte
  unchanged (see ``jarvis_mode.JarvisStateMachine``).
* Fail-open: every entry point raises explicitly (no swallowing here) — the
  jarvis caller catches, disables the delegate and falls back to the legacy
  dialog logic. Exceptions are always logged by the caller as
  ``[turn-delegate] ...`` warnings.
* State-guarded feeds: ``on_llm_response_token`` / ``on_tts_started`` /
  ``on_tts_finished`` only fire when the controller's FSM allows the
  transition. Feeding those events in other states would raise
  ``TurnStateError`` on completely normal flows (e.g. browser-side TTS after
  the controller already returned to LISTENING), so the guards mirror the
  controller's own transition legality — they are not defensive None checks.
* Phase C boundary: ``on_pre_speech_filler`` is wired to a log-only callback
  so the PRE_SPEECH filler never fires in Phase B (jarvis never drives the
  TTFT timer; the callback is present only to document the boundary).
* Structured logging: ``[turn-delegate] ...``; exceptions are explicit.

The jarvis -> turn_controller mapping follows the integration spec §2
Phase B table:

    DIALOG_ACTIVE -> USER_SPEAKING / PROCESSING / THINKING / SPEAKING
    TTS_PAUSED entry <- HARD_INTERRUPTED (controller arbitration)
    KWS chain / EXIT_DETECTED / ERROR -> jarvis mode extensions (not delegated)
"""

from __future__ import annotations

import logging

from .turn_controller import TurnController, TurnState

logger = logging.getLogger("joyai.turn_delegate")


class TurnControllerDelegate:
    """Active adapter: jarvis DIALOG_ACTIVE turn rhythm <-> TurnController.

    jarvis drives the wrapped controller through the event methods and then
    drains the pending action flags (``take_commit`` / ``take_barge_in``) to
    decide the same actions it would have taken by hand — reusing the
    existing ``_send_to_llm`` and TTS-pause paths.
    """

    def __init__(
        self,
        turn_controller: TurnController,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self._turn = turn_controller
        self._log = logger or logging.getLogger("joyai.turn_delegate")
        # Pending action flags drained by the jarvis caller after each feed.
        self.commit_pending: bool = False
        self.barge_in_pending: bool = False
        self.barge_in_conf: float = 0.0
        # Wire the controller callbacks to flag actions (sync -> async bridge).
        self._turn.on_turn_commit = self._on_turn_commit
        self._turn.on_barge_in = self._on_barge_in
        self._turn.on_timeout = self._on_timeout
        self._turn.on_pre_speech_filler = self._on_pre_speech_filler
        self._log.info(
            "[turn-delegate] controller ready (scenario=%s state=%s)",
            self._turn.config.scenario,
            self._turn.state.name,
        )

    # -- introspection -----------------------------------------------------

    @property
    def controller(self) -> TurnController:
        """The wrapped turn controller (jarvis preset)."""
        return self._turn

    # -- pending-action drain (called by jarvis after each feed) -----------

    def take_commit(self) -> bool:
        """True when the controller committed a user turn since the last drain."""
        if self.commit_pending:
            self.commit_pending = False
            return True
        return False

    def take_barge_in(self) -> bool:
        """True when the controller detected a hard barge-in since the last drain."""
        if self.barge_in_pending:
            self.barge_in_pending = False
            return True
        return False

    # -- jarvis -> controller event feed -----------------------------------

    def on_speech_started(self, conf: float) -> None:
        """Acoustic speech onset (L1). Raises TurnStateError on illegal states."""
        self._turn.on_speech_started(conf)

    def on_speech_stopped(self, silence_ms: int) -> None:
        """Acoustic silence after speech (L1). Raises on illegal states."""
        self._turn.on_speech_stopped(silence_ms)

    def on_partial_transcript(self, text: str, is_final: bool) -> None:
        """ASR partial/final transcript (L2)."""
        self._turn.on_partial_transcript(text, is_final)

    def on_llm_response_token(self, token: str) -> None:
        """Feed an LLM response token; only meaningful while PROCESSING.

        The controller's FSM accepts LLM tokens only after a commit
        (PROCESSING). Feeding in any other state is meaningless for the turn
        rhythm, so the guard mirrors the controller's transition legality.
        """
        if self._turn.state == TurnState.PROCESSING:
            self._turn.on_llm_token(token)

    def on_tts_started(self) -> None:
        """TTS audio started; only meaningful while THINKING (or PRE_SPEECH)."""
        if self._turn.state == TurnState.THINKING:
            self._turn.on_tts_started()

    def on_tts_finished(self) -> None:
        """TTS audio finished; only meaningful while SPEAKING (or PRE_SPEECH)."""
        if self._turn.state == TurnState.SPEAKING:
            self._turn.on_tts_finished()

    def on_cooldown_elapsed(self) -> None:
        """Cooldown guard window elapsed -> LISTENING (post-interrupt realign)."""
        self._turn.on_cooldown_elapsed()

    # -- dialog lifecycle --------------------------------------------------

    def on_dialog_enter(self) -> None:
        """jarvis entered DIALOG_ACTIVE: ensure the controller is LISTENING.

        Drives the wake gate (IDLE -> WARM_UP -> LISTENING) when the
        controller sits at the gate; recovers from COOLDOWN / interrupt
        zones; leaves an already-listening / mid-turn controller untouched.
        """
        c = self._turn
        if c.state in (TurnState.ENDED, TurnState.ERROR):
            c.reset()  # -> IDLE (wake gate)
        if c.state == TurnState.IDLE:
            c.on_wake_word_detected()  # -> WARM_UP
            c.on_wake_ready()  # -> LISTENING
        elif c.state == TurnState.WARM_UP:
            c.on_wake_ready()  # -> LISTENING
        elif c.state == TurnState.COOLDOWN:
            c.on_cooldown_elapsed()  # -> LISTENING
        elif c.state in (TurnState.HARD_INTERRUPTED, TurnState.SOFT_INTERRUPTED):
            c.on_speech_stopped(500)  # -> COOLDOWN
            c.on_cooldown_elapsed()  # -> LISTENING
        # LISTENING / USER_SPEAKING / PROCESSING / THINKING / SPEAKING: aligned.

    def on_dialog_reset(self) -> None:
        """jarvis left the dialog (reset to KWS / exit): reset the controller."""
        c = self._turn
        if c.state != TurnState.IDLE:
            c.reset()  # -> IDLE (wake gate)
        self.commit_pending = False
        self.barge_in_pending = False

    # -- controller callbacks (sync flags; jarvis drains) ------------------

    def _on_turn_commit(self) -> None:
        self.commit_pending = True
        self._log.info("[turn-delegate] action=on_turn_commit state=%s", self._turn.state.name)

    def _on_barge_in(self, conf: float) -> None:
        self.barge_in_pending = True
        self.barge_in_conf = conf
        self._log.info(
            "[turn-delegate] action=on_barge_in conf=%.2f state=%s",
            conf,
            self._turn.state.name,
        )

    def _on_timeout(self) -> None:
        self._log.info("[turn-delegate] action=on_timeout state=%s", self._turn.state.name)

    def _on_pre_speech_filler(self) -> None:
        # Phase C boundary: the PRE_SPEECH filler is a Phase C capability;
        # jarvis never drives the TTFT timer, so this cannot fire in Phase B.
        self._log.info("[turn-delegate] action=on_pre_speech_filler (Phase B: disabled)")


__all__ = ["TurnControllerDelegate"]
