"""Jarvis VAD bypass layer (sherpa-onnx built-in Silero VAD).

Form A — BYPASS + SOFT-GATE. This layer does NOT block the KWS wake path.
It runs sherpa-onnx's built-in Silero VAD on the 16 kHz mono float32 stream
and exposes ``is_speech()`` / segment boundaries so downstream consumers can
use them:

  * KWS soft-gate (this PR): optionally skip ``kws.feed_audio`` on silence
    chunks. Default OFF => KWS receives all audio (safest form A).
  * barge-in / endpoint / short-segment stitching (future follow-up work,
    NOT implemented here) — the ``current_segment()`` / ``pop_segment()``
    hooks are provided so those can be wired later without touching this file.

IMPORTANT — this is NOT the "is anyone speaking" detector that decision
T-VAD-1 rejected. It is the same Silero VAD usage as the HF speech-to-speech
reference: turn/segment management, interruption, streaming, short-segment
stitching. See ``doc/research/kws-vad-bt-wakeword.md §7`` for the rationale
and why this does not conflict with T-VAD-1.

Fail-open (mirrors ``smart_turn_adapter.py``):
  * ``enabled=False`` OR ``import sherpa_onnx`` fails OR the Silero VAD ONNX
    asset is absent/invalid => ``self.available = False`` and every method is
    a no-op or returns ``True`` (treat as speech => full KWS passthrough). It
    never raises, never fakes a result, never blocks the audio pipeline.

Verified API (sherpa_onnx 1.13.4, webui venv):
  * ``config = sherpa_onnx.SileroVadModelConfig()``; ``config.model`` is a
    plain STRING path (the bare ``SileroVadModel`` symbol is NOT exported).
  * ``detector = sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=60)``
  * ``detector.accept_waveform(samples)`` — samples: Sequence[float] in [-1, 1]
    (Silero is fixed at 16 kHz; NO per-call sample_rate argument).
  * ``detector.is_speech_detected() -> bool`` (current speech state).
  * ``detector.current_segment()`` -> in-progress partial SpeechSegment.
  * ``detector.front() / .pop() / .empty() / .flush() / .reset()``.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger("joyai.jarvis.vad")

_SILERO_VAD_FILENAME = "silero_vad.onnx"


class VadBypass:
    """Fail-open Silero VAD bypass layer for the Jarvis KWS path.

    When unavailable (disabled / import error / missing model), ``is_speech()``
    returns ``True`` so the KWS wake path keeps receiving all audio (full
    passthrough). Never raises.
    """

    def __init__(
        self,
        enabled: bool,
        model_dir: str,
        threshold: float = 0.5,
        min_silence_duration: float = 0.5,
        min_speech_duration: float = 0.25,
        window_size: int = 512,
        sample_rate: int = 16000,
    ):
        self.enabled = enabled
        self.model_dir = model_dir
        self._sample_rate = sample_rate
        self._detector = None
        self._available = False

        if not enabled:
            logger.info(
                "[vad] disabled (JARVIS_VAD_ENABLED=false); bypass in fail-open "
                "passthrough mode (KWS receives all audio)"
            )
            return

        model_path = os.path.join(model_dir, _SILERO_VAD_FILENAME) if model_dir else ""
        if not model_path or not os.path.isfile(model_path):
            logger.warning(
                "[vad] Silero VAD model not found at %s; bypass in fail-open "
                "passthrough mode (KWS receives all audio). Fetch silero_vad.onnx "
                "and set JARVIS_VAD_MODEL_DIR to enable.",
                model_path,
            )
            return

        try:
            import numpy as _np  # noqa: F401 - required by accept_waveform callers
            import sherpa_onnx
        except Exception as exc:  # noqa: BLE001 - fail-open, never crash pipeline
            logger.warning(
                "[vad] sherpa_onnx/numpy import failed (%s); bypass in fail-open "
                "passthrough mode",
                exc,
            )
            return

        try:
            config = sherpa_onnx.SileroVadModelConfig()
            config.model = model_path
            config.threshold = threshold
            config.min_silence_duration = min_silence_duration
            config.min_speech_duration = min_speech_duration
            config.window_size = window_size
            config.max_speech_duration = 20.0
            self._detector = sherpa_onnx.VoiceActivityDetector(
                config, buffer_size_in_seconds=60
            )
            self._available = True
            logger.info(
                "[vad] Silero VAD loaded from %s (thr=%.2f min_sil=%.2f win=%d); "
                "available=True",
                model_path,
                threshold,
                min_silence_duration,
                window_size,
            )
        except Exception as exc:  # noqa: BLE001 - fail-open on load error
            logger.warning(
                "[vad] failed to build VoiceActivityDetector (%s); bypass in "
                "fail-open passthrough mode",
                exc,
            )
            self._detector = None

    @property
    def available(self) -> bool:
        """Whether a real VAD detector is loaded. False => fail-open passthrough."""
        return self._available

    def accept_waveform(self, float32_samples) -> None:
        """Feed one chunk of 16 kHz mono float32 samples in [-1, 1]."""
        if not self._available or self._detector is None:
            return
        try:
            self._detector.accept_waveform(float32_samples)
        except Exception as exc:  # noqa: BLE001 - fail-open on inference error
            logger.warning("[vad] accept_waveform failed (%s); ignoring chunk", exc)

    def is_speech(self) -> bool:
        """Current speech state. Returns True when unavailable (full passthrough)."""
        if not self._available or self._detector is None:
            return True
        try:
            return bool(self._detector.is_speech_detected())
        except Exception:  # noqa: BLE001 - fail-open: treat as speech
            return True

    def pop_segment(self) -> None:
        """Pop the front completed segment (downstream barge-in consumption)."""
        if not self._available or self._detector is None:
            return
        try:
            self._detector.pop()
        except Exception:  # noqa: BLE001 - fail-open
            pass

    def current_segment(self):
        """Return the in-progress partial segment (streaming speech_start).

        Used by downstream barge-in wiring (not implemented in form A).
        """
        if not self._available or self._detector is None:
            return None
        try:
            return self._detector.current_segment()
        except Exception:  # noqa: BLE001 - fail-open
            return None

    def reset(self) -> None:
        """Reset the detector for a new stream (e.g. on session restart)."""
        if not self._available or self._detector is None:
            return
        try:
            self._detector.reset()
        except Exception:  # noqa: BLE001 - fail-open
            pass
