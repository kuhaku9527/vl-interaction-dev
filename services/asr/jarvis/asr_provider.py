# SPDX-License-Identifier: Apache-2.0
"""Unified ASR provider abstraction (local streaming / cloud batch).

Spec ``doc/specs/draft-asr-provider-unified.md`` §2. jarvis/live dialog ASR
selects a provider via ``JARVIS_ASR_PROVIDER=local|cloud`` (default ``local``
— the existing sherpa streaming path, byte-for-byte unchanged).

Providers:

* :class:`LocalStreamingProvider` — wraps :class:`services.asr.jarvis.asr.JarvisASR`
  (sherpa streaming-paraformer, true streaming partials);
* :class:`CloudBatchProvider` — accumulates PCM and transcribes the whole
  segment on :meth:`CloudBatchProvider.finalize` (one OpenAI-compatible
  ``/v1/audio/transcriptions`` round trip, reusing the call-path client from
  :mod:`services.asr.jarvis.asr_upstream`).

The synchronous :meth:`ASRProvider.stop` is used by reset paths (it clears
session state); the asynchronous :meth:`ASRProvider.finalize` returns the
final text (cloud performs the upstream POST there). jarvis/live call
``finalize`` only when ``provider.streaming is False``, so the local default
path never changes.
"""

from __future__ import annotations

import abc
import logging
import os

from .asr_upstream import pcm16_to_wav_bytes, transcribe_wav_bytes

logger = logging.getLogger("joyai.asr.provider")

#: Env gate selecting the jarvis/live ASR provider (default ``local``).
JARVIS_ASR_PROVIDER_ENV = "JARVIS_ASR_PROVIDER"
#: Explicit opt-in to degrade to the local streaming provider when the cloud
#: upstream is unreachable (D-080: otherwise an explicit error, never a
#: silent fallback).
JARVIS_ASR_ALLOW_LOCAL_FAILOVER_ENV = "JARVIS_ASR_ALLOW_LOCAL_FAILOVER"
#: Upstream config envs (already used by the call-path ASR bridge).
ASR_UPSTREAM_URL_ENV = "ASR_UPSTREAM_URL"
ASR_API_KEY_ENV = "ASR_API_KEY"
ASR_MODEL_ENV = "ASR_MODEL"

#: Model id used when cloud mode is on but ``ASR_MODEL`` is unset. Kept in
#: sync with the ASR adapter default so a bare cloud setup still sends a
#: form ``model`` field.
DEFAULT_CLOUD_MODEL = "Qwen/Qwen3-ASR-1.7B"

#: PCM peak (0..1) above which a cloud chunk counts as speech activity. Cloud
#: providers emit no partials, so the 2s endpoint timer is driven by audio
#: energy instead (spec §3 accumulate strategy).
ASR_SPEECH_PEAK_THRESHOLD = 0.01


class CloudASRError(RuntimeError):
    """Cloud upstream transcription failed (unreachable / auth / 5xx)."""


class ASRProvider(abc.ABC):
    """ASR provider interface (spec §2).

    ``feed_chunk`` returns partial text (local streaming) or ``""`` (cloud
    batch). ``stop`` is the synchronous end-of-session used by reset paths;
    ``finalize`` is the asynchronous end-of-session that returns the final
    text (cloud performs one upstream round trip there).
    """

    @abc.abstractmethod
    def start(self) -> None:
        """Begin a new recognition session."""

    @abc.abstractmethod
    def feed_chunk(self, pcm: bytes) -> str:
        """Feed one PCM chunk (16 kHz mono int16); return partial text.

        Parameters
        ----------
        pcm
            Raw little-endian int16 PCM bytes.

        Returns
        -------
        str
            Latest partial transcription (``""`` for batch providers).
        """

    @abc.abstractmethod
    def stop(self) -> str:
        """End the session synchronously and clear state.

        Used by reset paths where the final text is not needed. Local
        providers return the last text before clearing; batch providers
        return ``""`` (their buffer is dropped).
        """

    async def finalize(self) -> str:
        """End the session and return the final text (async-capable).

        Default implementation delegates to :meth:`stop`; cloud providers
        override with the upstream round trip. Raises :class:`CloudASRError`
        when the upstream is unreachable (D-080 explicit error).
        """
        return self.stop()

    @abc.abstractmethod
    def reset(self) -> None:
        """Clear session state without an upstream round trip."""

    @property
    @abc.abstractmethod
    def available(self) -> bool:
        """True when the provider is usable (model loaded / upstream set)."""

    @property
    @abc.abstractmethod
    def streaming(self) -> bool:
        """True when partial results are produced; False for batch."""


class LocalStreamingProvider(ASRProvider):
    """Wraps the existing ``JarvisASR`` sherpa streaming engine (spec §2).

    Every call is delegated 1:1, so behavior is byte-for-byte identical to
    using :class:`services.asr.jarvis.asr.JarvisASR` directly. The engine is
    imported lazily to keep sherpa-onnx off the module import path (the webui
    process imports this module at startup).
    """

    def __init__(
        self,
        asr: object | None = None,
        *,
        model_dir: str = "D:/AI/models/sherpa-onnx/models/asr/streaming-paraformer-bilingual-zh-en",
        num_threads: int = 2,
    ) -> None:
        """Wrap an existing engine or build a fresh ``JarvisASR``.

        Parameters
        ----------
        asr
            Optional pre-built engine (tests inject fakes here); when omitted
            a real ``JarvisASR`` is constructed from ``model_dir`` /
            ``num_threads``.
        model_dir
            sherpa-onnx streaming-paraformer model directory.
        num_threads
            sherpa-onnx decode thread count.
        """
        if asr is not None:
            self._asr = asr
        else:
            from .asr import JarvisASR

            self._asr = JarvisASR(model_dir=model_dir, num_threads=num_threads)

    def start(self) -> None:
        """Start a new streaming session on the wrapped engine."""
        self._asr.start()

    def feed_chunk(self, pcm: bytes) -> str:
        """Feed one PCM chunk; delegate to the wrapped engine."""
        return self._asr.feed_chunk(pcm)

    def stop(self) -> str:
        """Capture the last text, then stop the wrapped engine."""
        text = self._asr.last_text or ""
        self._asr.stop()
        return text

    async def finalize(self) -> str:
        """End the session and return the captured final text."""
        return self.stop()

    def reset(self) -> None:
        """Reset the session (equivalent to a fresh ``start``)."""
        self._asr.start()

    @property
    def available(self) -> bool:
        """A constructed local engine is always usable."""
        return True

    @property
    def streaming(self) -> bool:
        """Local sherpa is a true streaming provider."""
        return True

    @property
    def last_text(self) -> str:
        """The latest partial/final text (webui in-proc compatibility)."""
        return self._asr.last_text or ""


class CloudBatchProvider(ASRProvider):
    """Accumulates PCM and transcribes the whole segment on ``finalize``.

    OpenAI ``/v1/audio/transcriptions`` compatible (SiliconFlow etc.),
    reusing the same multipart/header client as the WebUI call path
    (:func:`services.asr.jarvis.asr_upstream.transcribe_wav_bytes`).

    No partials: :meth:`feed_chunk` returns ``""`` and merely accumulates.
    The caller (jarvis/live) triggers :meth:`finalize` after 2s of silence.
    """

    def __init__(
        self,
        upstream_url: str,
        api_key: str = "",
        model: str | None = None,
        max_buffer_seconds: float = 20.0,
        sample_rate: int = 16000,
        timeout: float = 120.0,
    ) -> None:
        """Configure the cloud batch provider.

        Parameters
        ----------
        upstream_url
            Full transcription endpoint URL (must be non-empty; the
            :attr:`available` property reflects this).
        api_key
            Optional Bearer token (SiliconFlow etc.); empty for local
            upstreams.
        model
            Optional upstream model id; ``None`` omits the form field.
        max_buffer_seconds
            PCM buffer cap; audio older than this is dropped (keeps memory
            bounded on long segments).
        sample_rate
            Expected PCM sample rate (Hz); used to build the WAV payload.
        timeout
            httpx request timeout (seconds) for the finalize POST.
        """
        self.upstream_url = (upstream_url or "").strip()
        self.api_key = api_key or ""
        self.model = model
        self.sample_rate = sample_rate
        self.timeout = timeout
        self.max_buffer_bytes = int(max_buffer_seconds * sample_rate * 2)
        self._pcm = bytearray()
        self._started = False

    def start(self) -> None:
        """Begin a new accumulation session (clears any buffered audio)."""
        self._pcm = bytearray()
        self._started = True

    def feed_chunk(self, pcm: bytes) -> str:
        """Accumulate one PCM chunk. Always returns ``""`` (no partials)."""
        if not pcm:
            return ""
        if not self._started:
            self.start()
        self._pcm.extend(pcm)
        if len(self._pcm) > self.max_buffer_bytes:
            # Keep the tail (most recent audio) within the cap so a very long
            # segment cannot grow memory without bound.
            del self._pcm[: len(self._pcm) - self.max_buffer_bytes]
        return ""

    def stop(self) -> str:
        """Drop the accumulated buffer (reset paths; no text available)."""
        self._pcm = bytearray()
        self._started = False
        return ""

    async def finalize(self) -> str:
        """Transcribe the accumulated segment (one upstream round trip).

        Returns
        -------
        str
            Final transcription text (``""`` when nothing was buffered).

        Raises
        ------
        CloudASRError
            The upstream POST failed (unreachable / auth / 5xx). Callers
            surface this explicitly (D-080).
        """
        pcm = bytes(self._pcm)
        self._pcm = bytearray()
        self._started = False
        if not pcm:
            return ""
        if len(pcm) % 2:
            raise CloudASRError(f"cloud provider: pcm16 byte length must be even (got {len(pcm)})")
        wav_bytes = pcm16_to_wav_bytes(pcm, self.sample_rate)
        try:
            return await transcribe_wav_bytes(
                wav_bytes,
                upstream_url=self.upstream_url,
                api_key=self.api_key,
                model=self.model,
                timeout=self.timeout,
            )
        except Exception as exc:
            raise CloudASRError(f"cloud provider unreachable: {exc}") from exc

    def reset(self) -> None:
        """Clear the accumulated buffer without an upstream round trip."""
        self._pcm = bytearray()
        self._started = False

    @property
    def available(self) -> bool:
        """A cloud provider is usable only when an upstream URL is set."""
        return bool(self.upstream_url)

    @property
    def streaming(self) -> bool:
        """Cloud batch produces no partials."""
        return False


def create_asr_provider(
    *,
    provider: str | None = None,
    model_dir: str = "D:/AI/models/sherpa-onnx/models/asr/streaming-paraformer-bilingual-zh-en",
    num_threads: int = 2,
    upstream_url: str = "",
    api_key: str = "",
    model: str | None = None,
    max_buffer_seconds: float = 20.0,
) -> ASRProvider:
    """Build the jarvis/live ASR provider from ``JARVIS_ASR_PROVIDER``.

    Parameters
    ----------
    provider
        Explicit provider choice (mainly tests). Defaults to the env value;
        ``local`` when unset (zero behavior change).
    model_dir
        Local sherpa model directory (used by the local provider).
    num_threads
        Local sherpa decode thread count.
    upstream_url / api_key / model
        Cloud overrides; fall back to ``ASR_UPSTREAM_URL`` / ``ASR_API_KEY`` /
        ``ASR_MODEL`` env when empty.
    max_buffer_seconds
        Cloud PCM buffer cap.

    Returns
    -------
    ASRProvider
        ``LocalStreamingProvider`` (default) or ``CloudBatchProvider``.

    Raises
    ------
    ValueError
        ``provider`` is neither ``local`` nor ``cloud`` — explicit, never a
        silent local fallback.
    """
    choice = (provider or os.environ.get(JARVIS_ASR_PROVIDER_ENV, "local")).strip().lower()
    if choice == "local":
        return LocalStreamingProvider(model_dir=model_dir, num_threads=num_threads)
    if choice == "cloud":
        url = upstream_url or os.environ.get(ASR_UPSTREAM_URL_ENV, "").strip()
        key = api_key or os.environ.get(ASR_API_KEY_ENV, "").strip()
        mdl = model
        if mdl is None:
            mdl = os.environ.get(ASR_MODEL_ENV, "").strip() or DEFAULT_CLOUD_MODEL
        return CloudBatchProvider(
            upstream_url=url,
            api_key=key,
            model=mdl,
            max_buffer_seconds=max_buffer_seconds,
        )
    raise ValueError(f"invalid JARVIS_ASR_PROVIDER: {choice!r} (expected 'local' or 'cloud')")


def allow_local_failover() -> bool:
    """Return True when ``JARVIS_ASR_ALLOW_LOCAL_FAILOVER=1`` (D-080 opt-in)."""
    return os.environ.get(JARVIS_ASR_ALLOW_LOCAL_FAILOVER_ENV, "").strip() == "1"
