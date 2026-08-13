"""Jarvis state-machine pure types (extracted from ``jarvis_mode.py``).

Moved from ``jarvis_mode.py`` (spec codebase-map-2026-08-13.md §2.1,
priority 5): the ``JarvisState`` enum and the ``AsrPartial`` dataclass —
pure, zero-dependency declarations shared by the state machine, the session
manager, and the front-end broadcast layer. ``jarvis_mode`` re-exports both
so existing ``from joy_interaction_webui.jarvis_mode import ...`` import
surfaces keep working unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto


class JarvisState(Enum):
    """Top-level states of the BT-7274 Jarvis interaction state machine."""

    KWS_LISTENING = auto()  # Waiting for wake word, KWS running
    WAKE_DETECTED = auto()  # Wake word heard, playing wake.wav
    DIALOG_ACTIVE = auto()  # Full duplex: ASR streaming + TTS
    TTS_PAUSED = auto()  # Interrupt: user started speaking during TTS
    EXIT_DETECTED = auto()  # Exit word detected, playing goodbye.wav
    ERROR = auto()  # Unrecoverable error, log only
    WAIT_ASR_CONFIRM = (
        auto()
    )  # KWS fired; waiting for ASR to confirm wake pattern before playing wake.wav


@dataclass
class AsrPartial:
    """A partial or final ASR result."""

    text: str
    is_final: bool = False
    timestamp_ms: float = 0.0
    # P1: the live ``_llm_reply_epoch`` at the moment this partial was
    # emitted. The front-end adopts it (``llmReplyGeneration``) so a
    # barge-in invalidates every llm_reply whose turn started earlier.
    reply_epoch: int = 0


__all__ = [
    "AsrPartial",
    "JarvisState",
]
