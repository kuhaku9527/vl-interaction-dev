"""Addressee enrollment flow helpers (extracted from ``live_mode.LiveStateMachine``).

Moved from ``live_mode.py``: the Addressee Detection Phase 1 enrollment
surface (spec draft-addressee-detection.md §3) — detector availability,
PCM segment validation, segment-capacity checks, and the VAD-segmented mic
stream collection state machine.  ``LiveStateMachine`` keeps thin facades
(``start_enroll`` / ``feed_enroll_pcm`` / ``finish_enroll`` /
``cancel_enroll`` / ``_feed_enroll_audio``) that delegate here; behavior is
unchanged (default OFF env gate -> no detector, zero behavior change).
"""

from __future__ import annotations

import logging
from typing import Any

#: Minimum enrollment segment duration (seconds) accepted from the mic stream.
_ADDRESSEE_ENROLL_MIN_SEGMENT_S: float = 1.0

#: Max enrollment segments collected (spec §3.1: 2-3 segments).
_ADDRESSEE_ENROLL_MAX_SEGMENTS: int = 3


def enroll_detector_available(detector: Any) -> bool:
    """True when the addressee detector exists and is usable (fail-open)."""
    return detector is not None and detector.available


def enroll_pcm_verdict(enroll_phase: bool, pcm: bytes, segment_count: int) -> str:
    """Verdict for one enrollment PCM append.

    Preserves the original ``feed_enroll_pcm`` guard order:
    ``"ignored"`` (not in enroll phase) -> ``"invalid"`` (empty / odd
    length) -> ``"full"`` (already collected
    ``_ADDRESSEE_ENROLL_MAX_SEGMENTS``) -> ``"ok"`` (appendable).
    """
    if not enroll_phase:
        return "ignored"
    if not pcm or len(pcm) % 2 != 0:
        return "invalid"
    if segment_count >= _ADDRESSEE_ENROLL_MAX_SEGMENTS:
        return "full"
    return "ok"


def feed_enroll_vad(
    *,
    pcm: bytes,
    vad_speech: bool,
    prev_vad_speech: bool,
    enroll_in_seg: bool,
    enroll_cur_segment: bytearray,
    enroll_segments: list[bytes],
    logger: logging.Logger,
) -> tuple[bool, bytearray, list[bytes], bool]:
    """VAD-segment one mic chunk into enrollment utterances.

    Moved verbatim from ``LiveStateMachine._feed_enroll_audio``'s state
    machine: the rising edge starts a segment buffer, speech chunks extend
    it, the falling edge finalizes a segment (>=1s, up to 3) and logs it.
    Returns ``(enroll_in_seg, enroll_cur_segment, enroll_segments,
    prev_vad_speech)`` so the caller can apply the updated state.
    """
    if vad_speech and not prev_vad_speech:
        enroll_in_seg = True
        enroll_cur_segment = bytearray(pcm)
    elif vad_speech and enroll_in_seg:
        enroll_cur_segment.extend(pcm)
    elif not vad_speech and enroll_in_seg:
        enroll_in_seg = False
        segment = bytes(enroll_cur_segment)
        enroll_cur_segment = bytearray()
        duration_s = len(segment) / 32000.0
        if (
            duration_s >= _ADDRESSEE_ENROLL_MIN_SEGMENT_S
            and len(enroll_segments) < _ADDRESSEE_ENROLL_MAX_SEGMENTS
        ):
            enroll_segments.append(segment)
            logger.info(
                "[addressee] enroll segment %d captured (%.2fs)",
                len(enroll_segments),
                duration_s,
            )
    return enroll_in_seg, enroll_cur_segment, enroll_segments, vad_speech


__all__ = [
    "_ADDRESSEE_ENROLL_MAX_SEGMENTS",
    "_ADDRESSEE_ENROLL_MIN_SEGMENT_S",
    "enroll_detector_available",
    "enroll_pcm_verdict",
    "feed_enroll_vad",
]
