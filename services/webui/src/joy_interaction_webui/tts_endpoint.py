# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""TTS synthesis endpoint (split out of server.py).

Owns ``POST /api/tts/synthesize`` and the WAV-wrapping helpers that turn a
``voice_clone_api /v1/synthesize`` response (base64 PCM16) into a playable
RIFF/WAVE blob for the HTML5 ``<audio>`` element.
"""

import logging
import os

from aiohttp import web

logger = logging.getLogger(__name__)


def _wav_chunk_header(sample_rate: int, channels: int, bits_per_sample: int = 16) -> bytes:
    """Return a 44-byte canonical PCM WAV header for the given format."""
    riff = b"RIFF"
    wave = b"WAVE"
    fmt_ = b"fmt "
    data = b"data"
    audio_format = 1  # PCM
    byte_rate = sample_rate * channels * bits_per_sample // 8
    block_align = channels * bits_per_sample // 8
    fmt_chunk_size = 16
    return (
        riff
        + b"\x00\x00\x00\x00"  # placeholder; caller fills RIFF size after data
        + wave
        + fmt_
        + fmt_chunk_size.to_bytes(4, "little")
        + audio_format.to_bytes(2, "little")
        + channels.to_bytes(2, "little")
        + sample_rate.to_bytes(4, "little")
        + byte_rate.to_bytes(4, "little")
        + block_align.to_bytes(2, "little")
        + bits_per_sample.to_bytes(2, "little")
        + data
        + b"\x00\x00\x00\x00"  # placeholder; caller fills data size after data
    )


def build_tts_synthesize_payload(upstream_json: dict) -> bytes:
    """Wrap a voice_clone_api /v1/synthesize response in a playable WAV blob.

    The upstream returns ``{"pcm16_base64": "...", "sample_rate": 24000, ...}``;
    browsers need a RIFF/WAVE container to play it via HTML5 ``<audio>``.
    Defaults: sample_rate=24000, channels=1 (MiniMax ``speech-2.8-hd`` shape).
    """
    import base64 as _b64

    pcm_b64 = upstream_json.get("pcm16_base64")
    if not pcm_b64:
        raise ValueError(
            "upstream /v1/synthesize response missing pcm16_base64; "
            f"keys={list(upstream_json.keys())}"
        )
    pcm = _b64.b64decode(pcm_b64)
    sample_rate = int(upstream_json.get("sample_rate") or 24000)
    channels = int(upstream_json.get("channels") or 1)
    header = _wav_chunk_header(sample_rate, channels)
    out = bytearray(header)
    out[4:8] = (len(out) + len(pcm) - 8).to_bytes(4, "little")
    out[40:44] = len(pcm).to_bytes(4, "little")
    out.extend(pcm)
    return bytes(out)


async def _tts_synthesize_handler(request):
    """POST /api/tts/synthesize -- wrap voice_clone_api into playable WAV.

    Body: ``{"text": "..."}`` (voice_id is read from ``JARVIS_TTS_VOICE_ID``).
    Returns ``audio/wav`` bytes on 200; 400 on empty text; 502 on upstream error.
    """
    import httpx as _httpx

    from . import server as _server

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)
    text = (data.get("text") or "").strip()
    if not text:
        return web.json_response({"error": "text is required"}, status=400)
    # Reuse the same JARVIS_TTS_* env that jarvis_mode.py uses, so behavior
    # stays in sync with the audio path used by the WebRTC speaker track.
    tts_api_url = os.environ.get("JARVIS_TTS_API_URL", "http://127.0.0.1:8985/v1/synthesize")
    voice_id = os.environ.get("JARVIS_TTS_VOICE_ID", "minimax_man_33333")
    body = {
        "text": text,
        "voice_id": voice_id,
        "model": os.environ.get("MINIMAX_DEFAULT_MODEL", "speech-2.8-hd"),
        "language_boost": os.environ.get("MINIMAX_LANGUAGE_BOOST", "Chinese"),
        "streaming": False,
    }
    try:
        async with _httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(tts_api_url, json=body)
    except Exception as exc:
        logger.warning("tts_synthesize: upstream unreachable: %s", exc)
        return web.json_response(
            {"error": "upstream unreachable", "reason": str(exc)[:120]}, status=502
        )
    if resp.status_code >= 500:
        return web.json_response(
            {"error": "upstream error", "status": resp.status_code}, status=502
        )
    try:
        upstream_json = resp.json()
    except Exception as exc:
        return web.json_response(
            {"error": "upstream non-json", "reason": str(exc)[:120]}, status=502
        )
    try:
        wav = _server.build_tts_synthesize_payload(upstream_json)
    except ValueError as exc:
        return web.json_response(
            {"error": "upstream payload invalid", "reason": str(exc)[:120]}, status=502
        )
    return web.Response(body=wav, content_type="audio/wav")
