# SPDX-License-Identifier: Apache-2.0
"""Shared upstream transcription client (OpenAI-compatible multipart POST).

Extracted from ``services/asr/asr_adapter.py`` so the WebUI call path (which
streams browser audio through the internal ASR bridge) and the jarvis/live
``CloudBatchProvider`` share ONE implementation of the upstream contract:

* multipart ``file`` upload (mono 16-bit PCM WAV),
* optional ``Authorization: Bearer <api_key>`` header (omitted for local
  upstreams),
* the OpenAI ``/v1/audio/transcriptions`` response shape (``{"text": ...}``
  or ``choices[0].message.content``).

The module has no heavy imports (sherpa-onnx / numpy are deliberately kept
out) so the ASR adapter can import it lazily without paying the local model
load cost.
"""

from __future__ import annotations

import io
import logging
import wave
from typing import Any

logger = logging.getLogger("joyai.asr.upstream")


def pcm16_to_wav_bytes(pcm: bytes, sample_rate: int) -> bytes:
    """Wrap raw int16 PCM into a mono RIFF/WAVE container.

    Parameters
    ----------
    pcm
        Raw little-endian int16 PCM bytes.
    sample_rate
        PCM sample rate (Hz); the caller is responsible for feeding 16 kHz.

    Returns
    -------
    bytes
        A complete WAV file payload ready for multipart upload.
    """
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm)
    return buffer.getvalue()


def extract_transcription_text(payload: dict[str, Any]) -> str:
    """Extract the transcription text from an OpenAI-compatible response.

    Accepts either ``{"text": "..."}`` (whisper.cpp / SiliconFlow) or a chat
    completion shape ``{"choices": [{"message": {"content": "..."}}]}``.

    Parameters
    ----------
    payload
        Parsed JSON response body.

    Returns
    -------
    str
        The transcription text (``""`` when the shape is unknown).
    """
    if isinstance(payload.get("text"), str):
        return payload["text"]
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0] or {}
        message = first.get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            return content
    return ""


async def transcribe_wav_bytes(
    wav_bytes: bytes,
    *,
    upstream_url: str,
    api_key: str = "",
    model: str | None = None,
    timeout: float = 120.0,
) -> str:
    """POST a WAV to an OpenAI-compatible transcription endpoint.

    Parameters
    ----------
    wav_bytes
        Mono 16-bit PCM WAV payload (any sample rate the upstream accepts).
    upstream_url
        Full transcription endpoint URL (e.g. SiliconFlow
        ``/v1/audio/transcriptions``).
    api_key
        Optional Bearer token. Omitted when empty so local upstreams stay
        backward compatible.
    model
        Optional model id sent as form data. ``None`` omits the field;
        an empty string is still sent (matching the ASR adapter contract).
    timeout
        httpx request timeout in seconds.

    Returns
    -------
    str
        Stripped transcription text.

    Raises
    ------
    httpx.HTTPError
        Upstream unreachable / non-2xx. Callers surface this explicitly
        (D-080: no silent local degradation).
    """
    import httpx

    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    data: dict[str, str] = {}
    if model is not None:
        data["model"] = model
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            upstream_url,
            headers=headers,
            data=data,
            files={"file": ("audio.wav", wav_bytes, "audio/wav")},
        )
        response.raise_for_status()
        payload = response.json()
    return extract_transcription_text(payload).strip()
