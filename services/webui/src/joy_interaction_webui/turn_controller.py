"""Unified Turn Controller — sandbox prototype (pure logic, no I/O).

This module is a *prototype validation* of the unified turn-taking core
described in ``doc/specs/unified-turn-controller.md`` (spec draft v2)
and cross-validated in ``doc/research/block3-cross-validation-2026-08-12.md``.

Scope
-----
* Pure state machine + pure text primitive (``SentenceBuffer``). No ASR /
  LLM / TTS / VAD hardware is touched; real engines drive the controller
  through the event methods.
* It does NOT replace ``jarvis_mode.py`` / ``live_adapter.py`` /
  ``response_format.py`` / ``vad_bypass.py`` / ``smart_turn_adapter.py``.
  It is an isolated, independently-testable sandbox (prototype validation,
  NOT a replacement).

Design notes
------------
* Flat state set: the spec headline says "12 flat states" but enumerates 13
  names; all 13 enumerated states are kept (THINKING kept distinct from
  PROCESSING: PROCESSING = ASR committed, awaiting LLM decision; THINKING =
  LLM reasoning in progress).
* Every state transition logs a structured ``[turn_controller] state: X ->
  Y, event: Z, reason: ...`` line (约法三章 ③).
* Illegal transitions raise ``TurnStateError`` (explicit, no silent guard —
  约法三章 ①②). Events that are benign races in real pipelines
  (e.g. stray LLM token in LISTENING) are logged and ignored instead.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger("joyai.turn_controller")

# ---------------------------------------------------------------------------
# Primitive: sentence boundary detection (LLM token streaming → TTS flush)
# ---------------------------------------------------------------------------

#: Sentence-ending characters. A ``.`` is only a boundary when it is not part
#: of a known abbreviation (see ``FALSE_POSITIVES``).
SENTENCE_ENDINGS: re.Pattern[str] = re.compile(r"[.!?。！？\n]")

#: Secondary split characters (Chinese comma / enumeration comma / semicolons,
#: plus ASCII comma/semicolon for mixed text). Used ONLY when a long buffer
#: (> ``max_sentence_chars``) has no hard sentence ending — conservative:
#: short sentences are never split at commas, so short phrases stay whole.
COMMA_SPLIT_CHARS: frozenset[str] = frozenset({"，", "、", "；", ";", ","})

#: Abbreviations whose trailing dot must NOT be treated as a sentence end.
FALSE_POSITIVES: frozenset[str] = frozenset(
    {
        "Dr.",
        "Mr.",
        "Mrs.",
        "Ms.",
        "Prof.",
        "Sr.",
        "Jr.",
        "U.S.",
        "U.K.",
        "E.U.",
        "a.m.",
        "p.m.",
        "etc.",
        "vs.",
        "i.e.",
        "e.g.",
        "approx.",
    }
)

#: Lower-cased abbreviation set used for case-insensitive matching ("Dr." vs "dr.").
_FALSE_POSITIVES_LOWER: frozenset[str] = frozenset(a.lower() for a in FALSE_POSITIVES)


class SentenceBuffer:
    """Accumulate LLM streamed tokens and flush complete sentences for TTS.

    P0 primitive (spec §3.4.1): streamed LLM output is flushed to TTS at
    sentence boundaries (see ``SENTENCE_ENDINGS``), excluding the abbreviation
    false positives in ``FALSE_POSITIVES``. This lowers perceived latency for
    the cloud MiniMax TTS path.

    Rules:
      * A candidate sentence shorter than ``min_sentence_length`` does not
        flush on its own — it is merged into the following text.
      * When the buffer reaches ``max_buffer_chars`` without a boundary, it
        is force-flushed (returns everything accumulated).
      * When the buffer exceeds ``max_sentence_chars`` without a hard
        sentence ending AND ``comma_split_enabled``, it is split at the
        closest comma (Chinese comma / enumeration comma / fullwidth
        semicolon, or ASCII semicolon/comma) so a long comma-connected
        Chinese reply is spoken in ~``max_sentence_chars`` chunks instead of
        one huge block. Chunks shorter than ``min_sentence_length`` are never
        split (avoids over-fragmentation).
      * ``should_flush_on_timeout()`` tells the caller a flush is overdue
        (``flush_on_timeout_ms`` since the last token); the caller then calls
        ``flush_remaining()``.
    """

    def __init__(
        self,
        min_sentence_length: int = 10,
        max_buffer_chars: int = 500,
        flush_on_timeout_ms: int = 500,
        max_sentence_chars: int = 80,
        comma_split_enabled: bool = True,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.min_sentence_length = min_sentence_length
        self.max_buffer_chars = max_buffer_chars
        self.flush_on_timeout_ms = flush_on_timeout_ms
        self.max_sentence_chars = max_sentence_chars
        self.comma_split_enabled = comma_split_enabled
        self._clock: Callable[[], float] = clock or time.monotonic
        self._buffer: str = ""
        self._last_token_ms: float = 0.0
        self._last_scan_pos: int = 0

    @property
    def text(self) -> str:
        """Current un-flushed buffer contents."""
        return self._buffer

    @property
    def is_empty(self) -> bool:
        return not self._buffer

    @property
    def remaining_chars(self) -> int:
        return len(self._buffer)

    def add_token(self, token: str) -> str | None:
        """Append a token; return a complete sentence if one is available.

        Returns ``None`` when the accumulated text does not yet form a
        sentence (or the sentence is too short to flush on its own).
        """
        if not token:
            return None
        self._buffer += token
        self._last_token_ms = self._clock() * 1000.0

        for i in range(self._last_scan_pos, len(self._buffer)):
            if not self._is_sentence_end(i):
                continue
            candidate = self._buffer[: i + 1]
            if len(candidate) < self.min_sentence_length:
                # Too short to be a complete sentence; merge with following text.
                self._last_scan_pos = i + 1
                continue
            self._buffer = self._buffer[i + 1 :]
            self._last_scan_pos = 0
            return candidate

        if len(self._buffer) >= self.max_buffer_chars:
            return self.flush_remaining()

        # Secondary split: a long buffer without a hard sentence ending is cut
        # at the closest comma so Chinese long replies stream in ~80-char
        # chunks instead of one huge TTS synthesis (see _find_comma_split).
        if self.comma_split_enabled and len(self._buffer) >= self.max_sentence_chars:
            split_at = self._find_comma_split()
            if split_at is not None:
                candidate = self._buffer[: split_at + 1]
                self._buffer = self._buffer[split_at + 1 :]
                self._last_scan_pos = 0
                return candidate
        return None

    def _find_comma_split(self) -> int | None:
        """Locate a conservative comma split point inside the buffer.

        Only called when the buffer exceeds ``max_sentence_chars`` without a
        hard sentence ending. Prefers the comma nearest to (but not beyond)
        ``max_sentence_chars`` so flushed chunks land near the target size;
        falls back to the first valid comma when every comma lies beyond the
        window. Chunks shorter than ``min_sentence_length`` are never split,
        so short comma-connected phrases stay whole until a real ending.
        """
        candidates: list[int] = []
        for i, ch in enumerate(self._buffer):
            if ch in COMMA_SPLIT_CHARS and i + 1 >= self.min_sentence_length:
                candidates.append(i)
        if not candidates:
            return None
        within_window = [i for i in candidates if i < self.max_sentence_chars]
        return within_window[-1] if within_window else candidates[0]

    def flush_remaining(self) -> str | None:
        """Return and clear any un-flushed text (``None`` if buffer empty)."""
        if not self._buffer:
            return None
        text = self._buffer
        self._buffer = ""
        self._last_scan_pos = 0
        return text

    def should_flush_on_timeout(self) -> bool:
        """True when the buffer is non-empty and idle past ``flush_on_timeout_ms``."""
        if not self._buffer:
            return False
        elapsed_ms = self._clock() * 1000.0 - self._last_token_ms
        return elapsed_ms >= self.flush_on_timeout_ms

    def _is_sentence_end(self, i: int) -> bool:
        """True when buffer[i] terminates a sentence (abbreviation-aware)."""
        ch = self._buffer[i]
        if ch == "\n":
            return True
        if ch not in ".!?。！？":
            return False
        if ch == ".":
            # Ellipsis / multi-dot runs are not boundaries.
            if i > 0 and self._buffer[i - 1] == ".":
                return False
            if i + 1 < len(self._buffer) and self._buffer[i + 1] == ".":
                return False
            # Collect the "word" run (letters + dots) ending at i, e.g. "U.S."
            start = i
            while start > 0 and (
                self._buffer[start - 1].isalpha() or self._buffer[start - 1] == "."
            ):
                start -= 1
            token = self._buffer[start : i + 1].lower()
            if token in _FALSE_POSITIVES_LOWER:
                return False
            # A partial abbreviation ("U.", "e.", "a.") is also not a boundary.
            if any(
                abbr.startswith(token) for abbr in _FALSE_POSITIVES_LOWER if len(abbr) > len(token)
            ):
                return False
        return True


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

TURN_DECISION_ACTIONS: frozenset[str] = frozenset({"commit", "wait", "interrupt", "timeout"})


@dataclass
class TurnEvent:
    """Base class for all turn controller events."""

    timestamp_ms: int = 0
    session_id: str = ""


@dataclass
class SpeechStartedEvent(TurnEvent):
    """VAD reported speech onset (acoustic layer, L1)."""

    confidence: float = 0.0


@dataclass
class SpeechStoppedEvent(TurnEvent):
    """VAD reported silence after speech (acoustic layer, L1)."""

    silence_duration_ms: int = 0


@dataclass
class PartialTranscriptEvent(TurnEvent):
    """ASR partial/final transcript (semantic layer, L2)."""

    text: str = ""
    is_final: bool = False
    confidence: float = 0.0


@dataclass
class TurnDecisionEvent(TurnEvent):
    """LLM decision-token result (decision layer, L3)."""

    action: str = "wait"

    def __post_init__(self) -> None:
        if self.action not in TURN_DECISION_ACTIONS:
            raise ValueError(
                f"invalid turn decision action: {self.action!r} "
                f"(expected one of {sorted(TURN_DECISION_ACTIONS)})"
            )


@dataclass
class LLMTokenEvent(TurnEvent):
    """A streamed LLM content token (decision token stripped upstream)."""

    token: str = ""


@dataclass
class TTSAudioEvent(TurnEvent):
    """TTS audio lifecycle signal."""

    is_first: bool = False
    is_last: bool = False


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


class TurnState(Enum):
    """Flat turn-taking states (spec draft v2 / block-3 cross-validation).

    The spec headline says "12 flat states"; the enumerated list contains 13
    names, all of which are kept. THINKING (LLM reasoning) is distinct from
    PROCESSING (ASR committed, awaiting LLM decision).
    """

    IDLE = "idle"
    WARM_UP = "warm_up"
    LISTENING = "listening"
    USER_SPEAKING = "user_speaking"
    PROCESSING = "processing"
    THINKING = "thinking"
    PRE_SPEECH = "pre_speech"
    SPEAKING = "speaking"
    HARD_INTERRUPTED = "hard_interrupted"
    SOFT_INTERRUPTED = "soft_interrupted"
    COOLDOWN = "cooldown"
    ENDED = "ended"
    ERROR = "error"


class TurnStateError(RuntimeError):
    """Raised when an event is illegal in the current state."""


# State allow-lists used by the strict validation helper.
_ST_IDLE = frozenset({TurnState.IDLE})
_ST_WARM_UP = frozenset({TurnState.WARM_UP})
_ST_SOFT_INTERRUPTED = frozenset({TurnState.SOFT_INTERRUPTED})


# ---------------------------------------------------------------------------
# Configuration (scenario preset matrix)
# ---------------------------------------------------------------------------


@dataclass
class TurnConfig:
    """Scenario-tunable configuration for the unified turn controller.

    Defaults follow the live (streaming) scenario. Three preset factories
    cover the locally-mapped scenarios from the 7-scenario matrix:
    ``live()``, ``jarvis()``, ``conversation()``.
    """

    #: jarvis wake-gate: IDLE → WARM_UP → LISTENING instead of resident LISTENING.
    wake_gate_enabled: bool = False
    #: live proactive speaking (主动搭话); jarvis disables it.
    proactive_speak_enabled: bool = True
    #: acoustic VAD speech threshold (L1; live 0.7 / jarvis 0.4 / conversation 0.5).
    vad_threshold: float = 0.65
    #: barge-in confidence threshold (double-threshold: 0.65 / 0.75 per block-3).
    barge_in_threshold: float = 0.75
    #: silence (ms) after user speech that ends the utterance (normal turn).
    silence_timeout_ms: int = 500
    #: silence (ms) that ends an interrupted/barge-in utterance.
    barge_in_silence_timeout_ms: int = 300
    #: post-interrupt / post-timeout guard window (ms) before LISTENING.
    cooldown_ms: int = 350
    #: utterances shorter than this (ms) are not committed.
    min_utterance_ms: int = 200
    #: utterances longer than this (ms) trigger ``on_timeout``.
    max_utterance_ms: int = 30000
    #: enable the PRE_SPEECH filler when LLM TTFT exceeds the budget.
    pre_speech_filler_enabled: bool = True
    #: LLM time-to-first-token budget (ms); exceeded → PRE_SPEECH filler.
    llm_ttft_timeout_ms: int = 500
    #: LLM total generation budget (ms); exceeded → on_timeout fallback.
    llm_total_timeout_ms: int = 10000
    #: scenario tag used by ``describe_config()``.
    scenario: str = "custom"

    @classmethod
    def live(cls) -> TurnConfig:
        """直播模式: resident listening, proactive speaking, VAD 0.7."""
        return cls(
            wake_gate_enabled=False,
            proactive_speak_enabled=True,
            vad_threshold=0.7,
            barge_in_threshold=0.75,
            silence_timeout_ms=500,
            barge_in_silence_timeout_ms=300,
            scenario="live",
        )

    @classmethod
    def jarvis(cls) -> TurnConfig:
        """语音助手: wake gate on, proactive speaking off, VAD 0.4, silence 300."""
        return cls(
            wake_gate_enabled=True,
            proactive_speak_enabled=False,
            vad_threshold=0.4,
            barge_in_threshold=0.75,
            silence_timeout_ms=300,
            barge_in_silence_timeout_ms=300,
            scenario="jarvis",
        )

    @classmethod
    def conversation(cls) -> TurnConfig:
        """实时对话: full-duplex adaptive, VAD 0.5, lower barge-in threshold."""
        return cls(
            wake_gate_enabled=False,
            proactive_speak_enabled=True,
            vad_threshold=0.5,
            barge_in_threshold=0.7,
            silence_timeout_ms=500,
            barge_in_silence_timeout_ms=300,
            cooldown_ms=300,
            scenario="conversation",
        )

    def describe_config(self) -> str:
        """Return a human-readable description including the scenario name."""
        scenario_label = {
            "live": "直播模式（常驻聆听，主动搭话开）",
            "jarvis": "语音助手（唤醒门开，主动搭话关）",
            "conversation": "实时对话（全双工自适应）",
            "custom": "自定义配置",
        }.get(self.scenario, self.scenario)
        params = [
            f"wake_gate_enabled={self.wake_gate_enabled}",
            f"proactive_speak_enabled={self.proactive_speak_enabled}",
            f"vad_threshold={self.vad_threshold:.2f}",
            f"barge_in_threshold={self.barge_in_threshold:.2f}",
            f"silence_timeout_ms={self.silence_timeout_ms}",
            f"barge_in_silence_timeout_ms={self.barge_in_silence_timeout_ms}",
            f"cooldown_ms={self.cooldown_ms}",
            f"min_utterance_ms={self.min_utterance_ms}",
            f"max_utterance_ms={self.max_utterance_ms}",
            f"pre_speech_filler_enabled={self.pre_speech_filler_enabled}",
            f"llm_ttft_timeout_ms={self.llm_ttft_timeout_ms}",
            f"llm_total_timeout_ms={self.llm_total_timeout_ms}",
        ]
        return f"[turn_controller] config[{self.scenario}] {scenario_label}: " + ", ".join(params)


#: Scenario preset registry (config matrix application).
SCENARIO_PRESETS: dict[str, TurnConfig] = {
    "live": TurnConfig.live(),
    "jarvis": TurnConfig.jarvis(),
    "conversation": TurnConfig.conversation(),
}

# ---------------------------------------------------------------------------
# Controller (event-driven state machine)
# ---------------------------------------------------------------------------

Clock = Callable[[], float]


class TurnController:
    """Unified turn-taking state machine (event-driven, pure logic).

    Lifecycle (no wake gate — live/conversation):
        IDLE → LISTENING → USER_SPEAKING → PROCESSING → THINKING
             → SPEAKING → LISTENING …
    Lifecycle (wake gate — jarvis):
        IDLE → WARM_UP → LISTENING → …

    Key transition rules (spec draft v2 + block-3 cross-validation):
      * LISTENING + speech_started        → USER_SPEAKING
      * USER_SPEAKING + speech_stopped
        (silence ≥ silence_timeout)        → PROCESSING (commit → on_turn_commit)
      * PROCESSING + first LLM token       → THINKING
      * THINKING + TTFT timeout            → PRE_SPEECH (on_pre_speech_filler)
      * THINKING + speech_started          → HARD_INTERRUPTED (immediate cancel —
        the key new path missing from jarvis today)
      * SPEAKING/PRE_SPEECH + speech_started:
          conf ≥ barge_in_threshold        → HARD_INTERRUPTED (on_barge_in)
          conf < threshold ∧ sentence end  → SOFT_INTERRUPTED
          conf < threshold ∧ mid-sentence  → ignore (continue speaking)
      * SOFT_INTERRUPTED + resume          → SPEAKING (on_resume_speaking)
      * interrupted/timeout → COOLDOWN → LISTENING
      * end_turn() → ENDED; on_error() → ERROR (recoverable via reset())

    Illegal transitions raise ``TurnStateError``. Stray events that are benign
    races in real pipelines are logged and ignored.
    """

    def __init__(
        self,
        config: TurnConfig,
        *,
        on_turn_commit: Callable[[], None] | None = None,
        on_barge_in: Callable[[float], None] | None = None,
        on_timeout: Callable[[], None] | None = None,
        on_pre_speech_filler: Callable[[], None] | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.config = config
        self._clock: Clock = clock or time.monotonic
        self.on_turn_commit = on_turn_commit
        self.on_barge_in = on_barge_in
        self.on_timeout = on_timeout
        self.on_pre_speech_filler = on_pre_speech_filler
        self.sentence_buffer = SentenceBuffer(clock=self._clock)
        self._utterance_started_ms: float | None = None
        self._last_speech_conf: float = 0.0
        self._turn_committed: bool = False
        # No wake gate → resident LISTENING; wake gate → IDLE until wake word.
        self.state = TurnState.LISTENING if not config.wake_gate_enabled else TurnState.IDLE
        logger.info(
            "[turn_controller] state: <init> -> %s, event: __init__, reason: "
            "scenario=%s wake_gate_enabled=%s",
            self.state.name,
            config.scenario,
            config.wake_gate_enabled,
        )

    # -- introspection -----------------------------------------------------

    @property
    def last_speech_conf(self) -> float:
        return self._last_speech_conf

    @property
    def turn_committed(self) -> bool:
        return self._turn_committed

    def describe_config(self) -> str:
        return self.config.describe_config()

    # -- internals ---------------------------------------------------------

    def _ms(self) -> float:
        return self._clock() * 1000.0

    def _require(self, allowed: frozenset[TurnState], event: str) -> None:
        if self.state not in allowed:
            raise TurnStateError(
                f"illegal transition: event '{event}' not allowed in state "
                f"'{self.state.name}' (allowed: {sorted(s.name for s in allowed)})"
            )

    def _transition(self, to_state: TurnState, event: str, reason: str) -> None:
        from_state = self.state
        self.state = to_state
        logger.info(
            "[turn_controller] state: %s -> %s, event: %s, reason: %s",
            from_state.name,
            to_state.name,
            event,
            reason,
        )

    def _fire_on_turn_commit(self) -> None:
        if self._turn_committed:
            return
        self._turn_committed = True
        if self.on_turn_commit is not None:
            self.on_turn_commit()

    def _fire_on_barge_in(self, conf: float) -> None:
        if self.on_barge_in is not None:
            self.on_barge_in(conf)

    def _fire_on_timeout(self) -> None:
        if self.on_timeout is not None:
            self.on_timeout()

    def _fire_on_pre_speech_filler(self) -> None:
        if self.on_pre_speech_filler is not None:
            self.on_pre_speech_filler()

    def _feed_llm_token(self, token: str) -> None:
        sentence = self.sentence_buffer.add_token(token)
        if sentence is not None:
            logger.info(
                "[turn_controller] sentence flushed: %r (len=%d)",
                sentence[:80],
                len(sentence),
            )

    def _at_sentence_boundary(self) -> bool:
        # Proxy for "can we pause TTS now": no meaningful text is buffered
        # mid-sentence (trailing whitespace after a flushed sentence is fine).
        return not self.sentence_buffer.text.strip()

    # -- wake gate (jarvis) ------------------------------------------------

    def on_wake_word_detected(self) -> None:
        """Wake word heard (jarvis gate): IDLE → WARM_UP."""
        if not self.config.wake_gate_enabled:
            raise TurnStateError(
                "on_wake_word_detected: wake gate is disabled in config "
                "(wake_gate_enabled=False); use the resident LISTENING path"
            )
        self._require(_ST_IDLE, "on_wake_word_detected")
        self._transition(
            TurnState.WARM_UP,
            "on_wake_word_detected",
            "wake word heard (jarvis wake gate)",
        )

    def on_wake_ready(self) -> None:
        """Wake audio finished: WARM_UP → LISTENING."""
        self._require(_ST_WARM_UP, "on_wake_ready")
        self._transition(
            TurnState.LISTENING,
            "on_wake_ready",
            "wake confirmation complete; listening",
        )

    # -- acoustic events ---------------------------------------------------

    def on_speech_started(self, conf: float) -> None:
        """VAD speech onset. Behaviour depends on the current state."""
        event = "on_speech_started"
        if self.state == TurnState.LISTENING:
            self._utterance_started_ms = self._ms()
            self._last_speech_conf = conf
            self._turn_committed = False
            self._transition(
                TurnState.USER_SPEAKING,
                event,
                f"speech detected conf={conf:.2f} (vad_threshold={self.config.vad_threshold:.2f})",
            )
        elif self.state == TurnState.USER_SPEAKING:
            self._last_speech_conf = conf
            self._transition(
                self.state,
                event,
                f"speech continues conf={conf:.2f}",
            )
        elif self.state == TurnState.THINKING:
            # Key new path (block-3): user talks while LLM is reasoning →
            # cancel immediately, regardless of confidence.
            self._last_speech_conf = conf
            self._transition(
                TurnState.HARD_INTERRUPTED,
                event,
                f"user speech during LLM thinking conf={conf:.2f} -> immediate cancel (new path)",
            )
            self._fire_on_barge_in(conf)
        elif self.state in (TurnState.SPEAKING, TurnState.PRE_SPEECH):
            self._last_speech_conf = conf
            if conf >= self.config.barge_in_threshold:
                self._transition(
                    TurnState.HARD_INTERRUPTED,
                    event,
                    f"barge-in conf={conf:.2f} >= barge_in_threshold={self.config.barge_in_threshold:.2f}",
                )
                self._fire_on_barge_in(conf)
            elif self._at_sentence_boundary():
                self._transition(
                    TurnState.SOFT_INTERRUPTED,
                    event,
                    f"low-conf speech conf={conf:.2f} at sentence boundary -> soft interrupt",
                )
            else:
                self._transition(
                    self.state,
                    event,
                    f"low-conf speech conf={conf:.2f} mid-sentence; continue speaking",
                )
        elif self.state == TurnState.HARD_INTERRUPTED:
            self._last_speech_conf = conf
            self._transition(
                self.state,
                event,
                f"user continues speaking during hard interrupt conf={conf:.2f}",
            )
        elif self.state == TurnState.SOFT_INTERRUPTED:
            self._last_speech_conf = conf
            if conf >= self.config.barge_in_threshold:
                self._transition(
                    TurnState.HARD_INTERRUPTED,
                    event,
                    f"soft interrupt escalated to hard conf={conf:.2f}",
                )
                self._fire_on_barge_in(conf)
            else:
                self._transition(
                    self.state,
                    event,
                    f"user continues speaking during soft interrupt conf={conf:.2f}",
                )
        elif self.state == TurnState.COOLDOWN:
            if conf >= self.config.barge_in_threshold:
                self._utterance_started_ms = self._ms()
                self._turn_committed = False
                self._transition(
                    TurnState.USER_SPEAKING,
                    event,
                    f"insistent speech conf={conf:.2f} during cooldown -> reclaim floor",
                )
            else:
                self._transition(
                    self.state,
                    event,
                    f"low-conf speech conf={conf:.2f} ignored during cooldown",
                )
        elif self.state == TurnState.PROCESSING:
            self._last_speech_conf = conf
            self._transition(
                self.state,
                event,
                f"speech during processing conf={conf:.2f} ignored (decision pending)",
            )
        else:
            raise TurnStateError(
                f"illegal transition: event '{event}' not allowed in state '{self.state.name}'"
            )

    def on_speech_stopped(self, silence_ms: int) -> None:
        """VAD silence after speech. Ends utterances / releases interrupts."""
        event = "on_speech_stopped"
        if self.state == TurnState.USER_SPEAKING:
            if silence_ms < self.config.silence_timeout_ms:
                self._transition(
                    self.state,
                    event,
                    f"silence={silence_ms}ms < silence_timeout={self.config.silence_timeout_ms}ms; still speaking",
                )
                return
            utterance_ms = (
                self._ms() - self._utterance_started_ms
                if self._utterance_started_ms is not None
                else 0
            )
            if utterance_ms < self.config.min_utterance_ms:
                self._transition(
                    TurnState.LISTENING,
                    event,
                    f"utterance too short ({utterance_ms:.0f}ms < min_utterance_ms={self.config.min_utterance_ms}ms)",
                )
                return
            self._transition(
                TurnState.PROCESSING,
                event,
                f"silence={silence_ms}ms >= silence_timeout={self.config.silence_timeout_ms}ms; "
                f"utterance={utterance_ms:.0f}ms -> commit",
            )
            self._fire_on_turn_commit()
        elif self.state == TurnState.HARD_INTERRUPTED:
            if silence_ms < self.config.barge_in_silence_timeout_ms:
                self._transition(
                    self.state,
                    event,
                    f"user still speaking; silence={silence_ms}ms < {self.config.barge_in_silence_timeout_ms}ms",
                )
                return
            self._transition(
                TurnState.COOLDOWN,
                event,
                f"user stopped after hard interrupt (silence={silence_ms}ms); "
                f"cooldown={self.config.cooldown_ms}ms",
            )
        elif self.state == TurnState.SOFT_INTERRUPTED:
            if silence_ms < self.config.barge_in_silence_timeout_ms:
                self._transition(
                    self.state,
                    event,
                    f"user still speaking; silence={silence_ms}ms < {self.config.barge_in_silence_timeout_ms}ms",
                )
                return
            self._transition(
                TurnState.COOLDOWN,
                event,
                f"user stopped after soft interrupt; yield floor (silence={silence_ms}ms)",
            )
        elif self.state == TurnState.PROCESSING:
            self._transition(
                self.state,
                event,
                f"silence={silence_ms}ms after commit ignored",
            )
        else:
            raise TurnStateError(
                f"illegal transition: event '{event}' not allowed in state '{self.state.name}'"
            )

    # -- ASR / LLM events --------------------------------------------------

    def on_partial_transcript(self, text: str, is_final: bool) -> None:
        """ASR partial/final transcript. A final transcript commits the turn."""
        event = "on_partial_transcript"
        if self.state in (TurnState.LISTENING, TurnState.USER_SPEAKING):
            logger.info(
                "[turn_controller] transcript is_final=%s text=%r (state=%s)",
                is_final,
                text[:80],
                self.state.name,
            )
            if is_final:
                self._utterance_started_ms = None
                self._transition(
                    TurnState.PROCESSING,
                    event,
                    "final transcript committed",
                )
                self._fire_on_turn_commit()
        else:
            logger.info(
                "[turn_controller] transcript ignored in state=%s (is_final=%s)",
                self.state.name,
                is_final,
            )

    def on_agent_turn_started(self) -> None:
        """Agent-initiated turn (proactive speak): LISTENING → THINKING.

        Proactive rounds (spec live-visual-cb.md §2.4) have no user
        speech, so the usual LISTENING → USER_SPEAKING → PROCESSING entry does
        not apply. This additive event drives the controller straight to
        THINKING so the following ``on_tts_started`` puts the agent into
        SPEAKING — making barge-in work exactly like a user-initiated turn.
        Only LISTENING accepts it; any other state is logged and ignored
        (fail-open: proactive work never disturbs the dialog).
        """
        event = "on_agent_turn_started"
        if self.state == TurnState.LISTENING:
            self._transition(
                TurnState.THINKING,
                event,
                "agent-initiated turn (proactive speak) -> reasoning started",
            )
        else:
            logger.info(
                "[turn_controller] agent turn started ignored in state=%s",
                self.state.name,
            )

    def on_llm_token(self, token: str) -> None:
        """Streamed LLM content token (decision token stripped upstream)."""
        event = "on_llm_token"
        if self.state == TurnState.PROCESSING:
            self._transition(
                TurnState.THINKING,
                event,
                "first LLM token -> reasoning started",
            )
            self._feed_llm_token(token)
        elif self.state == TurnState.THINKING:
            self._feed_llm_token(token)
        elif self.state == TurnState.PRE_SPEECH:
            self._transition(
                TurnState.THINKING,
                event,
                "first real token after filler -> prefill complete",
            )
            self._feed_llm_token(token)
        elif self.state == TurnState.SPEAKING:
            self._feed_llm_token(token)
        else:
            logger.info(
                "[turn_controller] LLM token ignored in state=%s (no active LLM turn)",
                self.state.name,
            )

    def on_llm_ttft_timed_out(self) -> None:
        """TTFT exceeded budget → PRE_SPEECH filler (if enabled)."""
        event = "on_llm_ttft_timed_out"
        if self.state == TurnState.THINKING:
            if self.config.pre_speech_filler_enabled:
                self._transition(
                    TurnState.PRE_SPEECH,
                    event,
                    f"TTFT > {self.config.llm_ttft_timeout_ms}ms -> pre-speech filler",
                )
                self._fire_on_pre_speech_filler()
            else:
                logger.info(
                    "[turn_controller] TTFT timeout but pre_speech_filler_enabled=False; stay thinking"
                )
        elif self.state == TurnState.PRE_SPEECH:
            logger.info("[turn_controller] TTFT timeout ignored; already in pre-speech filler")
        else:
            logger.info(
                "[turn_controller] TTFT timeout ignored in state=%s",
                self.state.name,
            )

    def on_llm_total_timed_out(self) -> None:
        """LLM total generation exceeded budget → timeout fallback."""
        event = "on_llm_total_timed_out"
        if self.state in (TurnState.THINKING, TurnState.PROCESSING, TurnState.PRE_SPEECH):
            self._transition(
                TurnState.COOLDOWN,
                event,
                f"LLM total generation > {self.config.llm_total_timeout_ms}ms -> timeout fallback",
            )
            self._fire_on_timeout()
        elif self.state == TurnState.SPEAKING:
            logger.info("[turn_controller] LLM total timeout ignored while TTS is speaking")
        else:
            logger.info(
                "[turn_controller] LLM total timeout ignored in state=%s",
                self.state.name,
            )

    # -- TTS events --------------------------------------------------------

    def on_tts_started(self) -> None:
        """TTS audio started → SPEAKING (from THINKING / PRE_SPEECH)."""
        event = "on_tts_started"
        if self.state in (TurnState.THINKING, TurnState.PRE_SPEECH):
            self._transition(
                TurnState.SPEAKING,
                event,
                "TTS audio started",
            )
        elif self.state == TurnState.SPEAKING:
            self._transition(self.state, event, "TTS already speaking")
        else:
            logger.info(
                "[turn_controller] TTS started ignored in state=%s",
                self.state.name,
            )

    def on_tts_finished(self) -> None:
        """TTS audio finished → LISTENING (agent turn complete)."""
        event = "on_tts_finished"
        if self.state == TurnState.SPEAKING:
            self._transition(
                TurnState.LISTENING,
                event,
                "agent speech finished; back to listening",
            )
        elif self.state == TurnState.PRE_SPEECH:
            logger.info("[turn_controller] filler finished; awaiting first LLM token")
        else:
            raise TurnStateError(
                f"illegal transition: event '{event}' not allowed in state '{self.state.name}'"
            )

    def on_resume_speaking(self) -> None:
        """Resume agent speech after a soft interrupt: SOFT_INTERRUPTED → SPEAKING."""
        event = "on_resume_speaking"
        self._require(_ST_SOFT_INTERRUPTED, event)
        self._transition(
            TurnState.SPEAKING,
            event,
            "resume agent speech after soft interrupt",
        )

    # -- cooldown / timeouts -----------------------------------------------

    def on_cooldown_elapsed(self) -> None:
        """Cooldown guard window elapsed → LISTENING."""
        event = "on_cooldown_elapsed"
        if self.state == TurnState.COOLDOWN:
            self._transition(
                TurnState.LISTENING,
                event,
                f"cooldown {self.config.cooldown_ms}ms elapsed; listening",
            )
        else:
            logger.info(
                "[turn_controller] cooldown elapsed ignored in state=%s",
                self.state.name,
            )

    def on_max_utterance_timed_out(self) -> None:
        """User utterance exceeded ``max_utterance_ms`` → on_timeout → COOLDOWN."""
        event = "on_max_utterance_timed_out"
        if self.state == TurnState.USER_SPEAKING:
            self._transition(
                TurnState.COOLDOWN,
                event,
                f"utterance exceeded max_utterance_ms={self.config.max_utterance_ms}ms",
            )
            self._fire_on_timeout()
        elif self.state in (
            TurnState.THINKING,
            TurnState.PROCESSING,
            TurnState.PRE_SPEECH,
            TurnState.SPEAKING,
        ):
            self._transition(
                TurnState.COOLDOWN,
                event,
                "agent-turn watchdog timeout",
            )
            self._fire_on_timeout()
        else:
            logger.info(
                "[turn_controller] max-utterance timeout ignored in state=%s",
                self.state.name,
            )

    # -- lifecycle ---------------------------------------------------------

    def end_turn(self) -> None:
        """Explicit end-of-turn: any active state → ENDED."""
        event = "end_turn"
        if self.state == TurnState.ENDED:
            raise TurnStateError(
                "illegal transition: event 'end_turn' not allowed in state 'ENDED'"
            )
        self._transition(TurnState.ENDED, event, "explicit end-of-turn")

    def on_error(self) -> None:
        """Report an error (recoverable): any state → ERROR."""
        event = "on_error"
        if self.state == TurnState.ERROR:
            logger.info("[turn_controller] already in ERROR")
            return
        self._transition(TurnState.ERROR, event, "error reported; recoverable via reset()")

    def reset(self) -> None:
        """Return to the initial state and clear per-turn state."""
        event = "reset"
        self.sentence_buffer = SentenceBuffer(clock=self._clock)
        self._utterance_started_ms = None
        self._last_speech_conf = 0.0
        self._turn_committed = False
        target = TurnState.LISTENING if not self.config.wake_gate_enabled else TurnState.IDLE
        self._transition(target, event, "controller reset")


__all__ = [
    "FALSE_POSITIVES",
    "SCENARIO_PRESETS",
    "SENTENCE_ENDINGS",
    "TURN_DECISION_ACTIONS",
    "LLMTokenEvent",
    "PartialTranscriptEvent",
    "SentenceBuffer",
    "SpeechStartedEvent",
    "SpeechStoppedEvent",
    "TTSAudioEvent",
    "TurnConfig",
    "TurnController",
    "TurnDecisionEvent",
    "TurnEvent",
    "TurnState",
    "TurnStateError",
]
