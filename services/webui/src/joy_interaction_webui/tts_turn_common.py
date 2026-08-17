"""Shared per-sentence TTS orchestration (jarvis_mode & live_mode).

Dedup point #3 of ``doc/architecture/codebase-map-2026-08-13.md``: the four
methods ``_spawn_sentence_tts`` / ``_synthesize_tts_sentence`` /
``_fetch_tts_pcm`` / ``_wrap_pcm16_wav`` were byte-identical between
``jarvis_mode.py`` (1970-2087) and ``live_mode.py`` (1291-1406) apart from
the log prefix / queued-log style and the config attribute name.  Both state
machines now delegate here; behavior is unchanged (epoch semantics, fail-open,
``tts_sentence`` callback wiring, and the ``[tts-stream]`` / ``[live-mode]``
log prefixes).  Callers pass their own ``logging.Logger`` so every record is
emitted on the exact same logger (``joyai.jarvis`` / ``joyai.live_mode``)
as before the extraction.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

logger = logging.getLogger(__name__)


def load_event_wav(events_dir: str, filename: str) -> tuple[bytes, int] | None:
    """Load a pre-recorded event WAV for playback (mono PCM16 + sample rate).

    Reads the WAV (any sample rate; mono PCM16, or downmixed from stereo) and
    returns ``(pcm, sample_rate)`` for the caller's audio sink. Returns
    ``None`` when the file is missing (logged) and lets a corrupt/unreadable
    file raise so the caller can apply its own fallback. This is the shared
    loader behind jarvis's ``_play_event_wav`` and the live radio-silence wake
    playback (spec §5 wake.wav, zero token).
    """
    import wave

    import numpy as _np

    path = Path(events_dir) / filename
    if not path.exists():
        logger.warning("Event audio not found: %s (skipping)", path)
        return None
    with wave.open(str(path), "rb") as wf:
        sample_rate = wf.getframerate()
        n_channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        raw = wf.readframes(wf.getnframes())
    if sampwidth != 2:
        logger.warning(
            "Event audio %s has sampwidth=%d (expected 2); playback may distort",
            filename,
            sampwidth,
        )
    samples = _np.frombuffer(raw, dtype=_np.int16)
    if n_channels > 1:
        # Downmix interleaved channels by averaging. Keeps duration honest and
        # stops downstream resamplers from treating L/R as consecutive mono
        # samples (pitch shift bug).
        frames = samples.reshape(-1, n_channels)
        samples = frames.mean(axis=1).astype(_np.int16)
        pcm = samples.tobytes()
    else:
        pcm = raw
    return pcm, sample_rate


def spawn_sentence_tts(
    *,
    logger: logging.Logger,
    sentence: str,
    seq: int,
    reply_session: int,
    epoch: int,
    tasks: set[asyncio.Task],
    synthesize: Callable[[str, int, int, int], Awaitable[None]],
    queued_format: str,
) -> None:
    """Synthesize one flushed sentence in the background and push it.

    The task runs concurrently with LLM streaming (sentence N synthesizes
    while the model generates sentence N+1).  The browser plays by ``seq``;
    an epoch bump (barge-in / exit word / stop) cancels every in-flight task
    and the captured epoch makes any task that survives drop its result.
    ``queued_format`` carries the mode-specific log line verbatim (e.g.
    ``"[tts-stream] sentence %d queued (session=%d, %d chars): '%s'"``).
    """
    task = asyncio.create_task(synthesize(sentence, seq, reply_session, epoch))
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    logger.info(queued_format, seq, reply_session, len(sentence), sentence[:60])


async def synthesize_tts_sentence(
    *,
    logger: logging.Logger,
    sentence: str,
    seq: int,
    reply_session: int,
    epoch: int,
    current_epoch: Callable[[], int],
    fetch_pcm: Callable[[str], Awaitable[bytes]],
    on_tts_sentence: Callable[[str, int, str, int], None] | None,
    log_prefix: str,
) -> None:
    """Fetch PCM16 for one sentence, wrap as WAV, push ``tts_sentence``.

    Guards with the sentence epoch captured at spawn time so audio
    synthesized after a barge-in / exit word is never pushed to the browser.
    Any synthesis / wrap / callback failure is logged explicitly and the
    sentence is dropped (fail-open — the session never crashes).
    """
    if current_epoch() != epoch:
        return
    t0 = time.time()
    logger.info(
        "%s sentence %d TTS synth start (session=%d, %d chars)",
        log_prefix,
        seq,
        reply_session,
        len(sentence),
    )
    try:
        pcm = await fetch_pcm(sentence)
    except Exception as exc:
        logger.error(
            "%s sentence %d TTS failed after %.0fms: %s",
            log_prefix,
            seq,
            (time.time() - t0) * 1000,
            exc,
        )
        return
    if current_epoch() != epoch:
        logger.debug("%s sentence %d stale (epoch bumped); dropping", log_prefix, seq)
        return
    logger.info(
        "%s sentence %d TTS ok (%.0fms, %d PCM bytes)",
        log_prefix,
        seq,
        (time.time() - t0) * 1000,
        len(pcm),
    )
    try:
        wav = wrap_pcm16_wav(pcm, sample_rate=24000)
        audio_b64 = base64.b64encode(wav).decode("ascii")
    except Exception as exc:
        logger.error("%s sentence %d WAV wrap failed: %s", log_prefix, seq, exc)
        return
    if on_tts_sentence:
        try:
            on_tts_sentence(sentence, seq, audio_b64, reply_session)
            logger.info(
                "%s sentence %d pushed (session=%d, total %.0fms)",
                log_prefix,
                seq,
                reply_session,
                (time.time() - t0) * 1000,
            )
        except Exception as exc:
            logger.warning("%s tts_sentence callback failed: %s", log_prefix, exc)


async def fetch_tts_pcm(
    *,
    tts_api_url: str,
    tts_voice_id: str,
    text: str,
) -> bytes:
    """POST ``text`` to the voice-clone TTS API and return PCM16 bytes."""
    import httpx

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            tts_api_url,
            json={
                "text": text,
                "voice_id": tts_voice_id,
                "streaming": False,
                "sample_rate": 24000,
            },
        )
        resp.raise_for_status()
        payload = resp.json()
        audio_b64 = payload.get("pcm16_base64") or payload.get("audio")
        if not audio_b64:
            raise ValueError(f"TTS response missing audio: {payload}")
        return base64.b64decode(audio_b64)


def wrap_pcm16_wav(pcm: bytes, sample_rate: int = 24000) -> bytes:
    """Wrap 24kHz mono PCM16 into a RIFF/WAVE container for <audio>."""
    n_channels = 1
    bits_per_sample = 16
    byte_rate = sample_rate * n_channels * bits_per_sample // 8
    block_align = n_channels * bits_per_sample // 8
    data_size = len(pcm)
    header = b"RIFF" + (36 + data_size).to_bytes(4, "little") + b"WAVE"
    header += b"fmt " + (16).to_bytes(4, "little")
    header += (1).to_bytes(2, "little")  # PCM
    header += n_channels.to_bytes(2, "little")
    header += sample_rate.to_bytes(4, "little")
    header += byte_rate.to_bytes(4, "little")
    header += block_align.to_bytes(2, "little")
    header += bits_per_sample.to_bytes(2, "little")
    header += b"data" + data_size.to_bytes(4, "little")
    return header + pcm
