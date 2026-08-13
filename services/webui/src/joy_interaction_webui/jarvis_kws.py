"""Jarvis KWS-chain pure logic (extracted from ``jarvis_mode.py``).

Moved from ``jarvis_mode.py`` (spec codebase-map-2026-08-13.md §2.1,
priority 5): the KWS wake-confirm chain's pure decisions — PCM energy stats,
the fresh-window probe (incl. the B1 P0 ``kws_probe_min_peak`` energy gate),
the WAIT_ASR_CONFIRM timeout verdict, and the wake-phrase confirm matcher.
``JarvisStateMachine`` keeps thin facades with identical signatures so
tests can call/monkeypatch them exactly as before; all deep self-state
orchestration (``_handle_kws`` / ``_direct_wake_from_kws`` /
``_feed_kws_shadow_asr`` / ``_promote_from_confirm`` /
``_handle_wait_asr_confirm``) stays in the class.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from array import array
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any

from .jarvis_config import _ASR_CONFIRM_NON_WORD
from .jarvis_state import JarvisState


def pcm_stats(pcm: bytes) -> tuple[float, float]:
    """Return (peak, rms) for int16 mono PCM in the 0..1 range.

    Moved verbatim from ``JarvisStateMachine._pcm_stats``.
    """
    if not pcm:
        return 0.0, 0.0
    if len(pcm) % 2:
        pcm = pcm[:-1]
    samples = array("h")
    samples.frombytes(pcm)
    if not samples:
        return 0.0, 0.0
    peak = max(abs(s) for s in samples) / 32768.0
    square_sum = sum(float(s) * float(s) for s in samples)
    rms = math.sqrt(square_sum / len(samples)) / 32768.0
    return min(1.0, peak), min(1.0, rms)


async def probe_fresh_window(
    *,
    config: Any,
    last_probe_at: float,
    capture_bytes: int,
    capture_chunks: deque[bytes],
    pcm_stats_fn: Callable[[bytes], tuple[float, float]],
    kws: Any,
    direct_wake: Callable[..., Awaitable[None]],
    peak: float,
    rms: float,
    bypass_min_s: bool,
    logger: logging.Logger,
) -> tuple[bool, float]:
    """Fallback KWS probe using a clean stream over recent PCM.

    Moved verbatim from ``JarvisStateMachine._probe_kws_fresh_window``:
    returns ``(hit, last_probe_at)`` — the caller applies the updated probe
    timestamp to its own state. The B1 P0 energy gate recomputes energy from
    the exact buffer that would be probed, so a pure-silence window can never
    escalate into a direct wake (``kws_probe_min_peak``). ``kws.detect_in_pcm``
    is accessed inside the try so a missing/erroring engine stays fail-open
    (logs + returns False), exactly as before.
    """
    if not getattr(config, "kws_fresh_window_probe_enabled", True):
        return False, last_probe_at
    now = time.time()
    interval = max(0.0, config.kws_fresh_window_probe_interval_s)
    if interval and (now - last_probe_at) < interval:
        return False, last_probe_at
    min_s = 0.1 if bypass_min_s else max(0.1, config.kws_fresh_window_min_s)
    if capture_bytes < int(min_s * config.sample_rate * 2):
        return False, last_probe_at
    last_probe_at = now
    pcm = b"".join(capture_chunks)
    # B1 P0 energy gate: probe only buffers with real acoustic content.
    # Recompute energy from the exact window we would probe (authoritative —
    # the passed peak/rms are the CURRENT chunk's stats, which can be ~0 for
    # a wake that settled during trailing blanks while the buffer holds the
    # actual speech). A pure-silence buffer is skipped so it can never
    # escalate into a direct wake.
    buf_peak, buf_rms = pcm_stats_fn(pcm)
    if buf_peak < max(0.0, config.kws_probe_min_peak):
        logger.info(
            "Fresh-window KWS probe skipped: silent window (peak=%.4f < kws_probe_min_peak=%.4f)",
            buf_peak,
            max(0.0, config.kws_probe_min_peak),
        )
        return False, last_probe_at
    try:
        hit = bool(kws.detect_in_pcm(pcm))
    except Exception as exc:
        logger.warning("Fresh-window KWS probe failed: %s", exc)
        return False, last_probe_at
    if not hit:
        return False, last_probe_at
    logger.info(
        "Wake word detected by fresh-window KWS probe (%.2fs peak=%.3f rms=%.3f)",
        len(pcm) / (config.sample_rate * 2),
        buf_peak,
        buf_rms,
    )
    if not getattr(config, "kws_fresh_window_direct_wake", True):
        return False, last_probe_at
    await direct_wake(source="fresh-window-kws")
    return True, last_probe_at


async def wait_asr_confirm_timeout(
    *,
    asr_confirm_timeout_s: float,
    state: JarvisState,
    last_wake_peak: float,
    last_wake_rms: float,
    probe: Callable[..., Awaitable[bool]],
    reset_to_kws: Callable[[], Awaitable[None]],
    logger: logging.Logger,
) -> None:
    """Reject the wake if ASR does not match within the configured timeout.

    Moved verbatim from ``JarvisStateMachine._wait_asr_confirm_timeout``:
    sleeps the confirm window, then (if still in WAIT_ASR_CONFIRM) runs a
    fresh-window KWS probe as recovery. B1 P0: the probe's own buffer-energy
    gate decides whether there is real acoustic content — a silent wake falls
    through to ``reset_to_kws`` instead of direct-waking.
    """
    try:
        await asyncio.sleep(asr_confirm_timeout_s)
    except asyncio.CancelledError:
        return
    if state != JarvisState.WAIT_ASR_CONFIRM:
        return  # already promoted or otherwise moved on
    # Recovery probe: fresh-stream KWS over captured audio.
    # Use peak/rms captured at wake time (more accurate) and bypass
    # the 1s min_s gate since we already have a trusted live KWS hit.
    # B1 P0: do NOT synthesize a fake peak for silent wakes (the old
    # ``peak <= 0 -> 0.5`` fallback let a pure-silence false trigger
    # escalate into a direct wake). Pass the wake-chunk energy as-is;
    # the probe's own buffer-energy gate (kws_probe_min_peak) decides
    # whether there is real acoustic content to recover. A silent wake
    # falls through to _reset_to_kws() below instead of direct-waking.
    peak = last_wake_peak
    rms = last_wake_rms
    if await probe(peak=peak, rms=rms, bypass_min_s=True):
        logger.info(
            "WAIT_ASR_CONFIRM recovered via fresh-window KWS probe; direct wake without ASR confirm"
        )
        return
    logger.info(
        "WAIT_ASR_CONFIRM timeout (%.2fs) without ASR match; returning to KWS_LISTENING",
        asr_confirm_timeout_s,
    )
    await reset_to_kws()


def asr_confirm_match(text: str, config: Any) -> bool:
    """Return True if ASR text contains the wake phrase in any common form.

    Moved verbatim from ``JarvisStateMachine._asr_confirm_match`` (the
    ``config`` argument supplies ``asr_confirm_patterns``).

    Two matchers are OR'd together:

    1. Explicit substring patterns from ``asr_confirm_patterns`` (backward
       compatibility / operator override).
    2. A wide normalised match that accepts ``bt``, ``b t``, ``b.t``,
       ``b、t``, ``b  t`` etc.  Non-word characters are collapsed to a
       single space, the text is lower-cased, and we accept either the
       joined token ``bt`` or adjacent tokens ``b`` followed by ``t``.
       This catches paraformer outputs that segment the two-syllable
       wake word with whitespace or punctuation.
    """
    if not text:
        return False
    lowered = text.lower()

    # (1) explicit operator-provided patterns (override mode)
    patterns = getattr(config, "asr_confirm_patterns", None)
    if patterns:
        return any(p.lower() in lowered for p in patterns)

    # (2) wide normalised match for segmented "b t" / "bt" forms
    normalised = " ".join(_ASR_CONFIRM_NON_WORD.split(lowered))
    if "bt" in normalised:
        return True
    tokens = normalised.split()
    return any(tokens[i] == "b" and tokens[i + 1] == "t" for i in range(len(tokens) - 1))


__all__ = [
    "asr_confirm_match",
    "pcm_stats",
    "probe_fresh_window",
    "wait_asr_confirm_timeout",
]
