"""Addressee Detection Phase 1 — acoustic pre-filter (CAM++ speaker embedding).

Wraps sherpa-onnx's native Speaker API (verified on 1.13.4):

  * :class:`sherpa_onnx.SpeakerEmbeddingExtractor` — CAM++
    ``3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx`` (16 kHz, 192-dim);
  * :class:`sherpa_onnx.SpeakerEmbeddingManager` — cosine enrollment store.

This module is the spec ``doc/specs/addressee-detection.md`` §3.1 module.
It sits between VAD segmentation and ASR in the live chain: an enrolled
target voice passes ``classify()``; any other voice scores below
``target_threshold`` and the caller drops the segment before ASR.

Fail-open contract (约法三章 ③, mirrors VadBypass):
  * extractor/manager load failure -> ``available=False`` and every method
    degrades to pass-through (``classify -> (True, 1.0)``);
  * too-short segments (``is_ready()`` false) -> ``extract`` returns ``None``
    and ``classify`` pass-through;
  * no enrollment -> ``classify -> (True, 1.0)`` (unfiltered);
  * exceptions are logged explicitly, never raised to the audio loop.

Sample rate: 16 kHz mono int16 — identical to the live ``feed_audio(pcm)``
chain (``audio_processor.MicAudioTrack`` resamples to 16 kHz before calling
``session.feed_audio``), so no resampling is required here.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Sequence

import numpy as np

logger = logging.getLogger("joyai.addressee")

#: Enrolled speaker key in the manager.
TARGET_SPEAKER = "target"

#: Default CAM++ model file name inside ``model_dir``.
_CAMPPLUS_MODEL_FILENAME = "3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx"


def _resolve_model_path(model_dir: str) -> str:
    """Resolve the CAM++ onnx path (file path or directory containing it)."""
    if not model_dir:
        return ""
    if os.path.isfile(model_dir):
        return model_dir
    if os.path.isdir(model_dir):
        candidate = os.path.join(model_dir, _CAMPPLUS_MODEL_FILENAME)
        if os.path.isfile(candidate):
            return candidate
        # Fallback: first *.onnx in the directory (operator dropped a copy
        # under a different name).
        for name in sorted(os.listdir(model_dir)):
            if name.lower().endswith(".onnx"):
                return os.path.join(model_dir, name)
    return ""


class AddresseeDetector:
    """CAM++ speaker-embedding gate: enroll one target voice, filter others.

    Args:
        model_dir: path to the CAM++ onnx file OR a directory containing
            ``3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx``.
        num_threads: ONNX Runtime threads for the extractor (default 2).
        target_threshold: cosine threshold for ``classify`` (default 0.6;
            spec suggests 0.55-0.65, 宁漏不乱插 keep it high).
    """

    def __init__(
        self,
        model_dir: str,
        *,
        num_threads: int = 2,
        target_threshold: float = 0.6,
    ) -> None:
        self.model_dir = model_dir
        self.num_threads = num_threads
        self.target_threshold = float(target_threshold)
        self._extractor = None
        self._manager = None
        self._target_embedding: np.ndarray | None = None
        self._available = False

        model_path = _resolve_model_path(model_dir)
        if not model_path:
            logger.error(
                "[addressee] CAM++ model not found at %s; detector unavailable "
                "(fail-open: all audio passes through)",
                model_dir,
            )
            return

        try:
            import sherpa_onnx
        except Exception as exc:  # noqa: BLE001 - fail-open on import error
            logger.error("[addressee] sherpa_onnx import failed (%s); detector unavailable", exc)
            return

        try:
            cfg = sherpa_onnx.SpeakerEmbeddingExtractorConfig()
            cfg.model = model_path
            cfg.num_threads = num_threads
            extractor = sherpa_onnx.SpeakerEmbeddingExtractor(cfg)
            manager = sherpa_onnx.SpeakerEmbeddingManager(extractor.dim)
        except Exception as exc:  # noqa: BLE001 - fail-open on load error
            logger.error(
                "[addressee] failed to build extractor/manager from %s (%s); "
                "detector unavailable (fail-open)",
                model_path,
                exc,
            )
            return

        self._extractor = extractor
        self._manager = manager
        self._available = True
        logger.info(
            "[addressee] CAM++ extractor loaded from %s (dim=%d, threads=%d, "
            "threshold=%.2f); available=True",
            model_path,
            extractor.dim,
            num_threads,
            self.target_threshold,
        )

    @property
    def available(self) -> bool:
        """Whether a real extractor/manager is loaded. False => fail-open."""
        return self._available

    # ------------------------------------------------------------------
    # Embedding extraction
    # ------------------------------------------------------------------

    def extract(self, pcm: bytes) -> np.ndarray | None:
        """Extract the 192-dim embedding for one 16 kHz mono PCM segment.

        Returns ``None`` for too-short / invalid input (caller fail-open).
        Never raises: exceptions are logged and return ``None``.
        """
        if not self._available or self._extractor is None:
            return None
        if not pcm or len(pcm) % 2 != 0:
            logger.debug("[addressee] extract skipped: invalid PCM len=%d", len(pcm))
            return None
        try:
            samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
            stream = self._extractor.create_stream()
            stream.accept_waveform(16000, samples)
            stream.input_finished()
            if not self._extractor.is_ready(stream):
                logger.debug(
                    "[addressee] extract not ready (len=%.2fs); fail-open",
                    len(samples) / 16000.0,
                )
                return None
            emb = np.asarray(self._extractor.compute(stream), dtype=np.float32)
            return emb
        except Exception as exc:  # noqa: BLE001 - fail-open on inference error
            logger.error("[addressee] extract failed (%s); fail-open", exc)
            return None

    # ------------------------------------------------------------------
    # Enrollment
    # ------------------------------------------------------------------

    def enroll(self, pcm_segments: Sequence[bytes], *, ema_alpha: float = 0.15) -> bool:
        """Enroll the target voice from 2-3 real speech segments.

        Each segment is embedded; embeddings are averaged (mean), then merged
        into the running target with EMA when re-enrolling (``ema_alpha``).

        Returns True on success (>= 2 usable segments). False + explicit log
        otherwise; the caller keeps the previous enrollment intact.
        """
        if not self._available or self._extractor is None or self._manager is None:
            logger.error("[addressee] enroll rejected: detector unavailable (fail-open)")
            return False

        embeddings: list[np.ndarray] = []
        for idx, seg in enumerate(pcm_segments, start=1):
            emb = self.extract(seg)
            if emb is None:
                logger.warning("[addressee] enroll segment %d unusable (too short/invalid)", idx)
                continue
            embeddings.append(emb)

        if len(embeddings) < 2:
            logger.error(
                "[addressee] enroll failed: need >=2 usable segments, got %d",
                len(embeddings),
            )
            return False

        new_mean = np.mean(np.stack(embeddings), axis=0).astype(np.float32)
        if self._target_embedding is None:
            merged = new_mean
        else:
            alpha = float(ema_alpha)
            merged = ((1.0 - alpha) * self._target_embedding + alpha * new_mean).astype(np.float32)
        self._target_embedding = merged

        try:
            if self._manager.num_speakers > 0:
                self._manager.remove(TARGET_SPEAKER)
            self._manager.add(TARGET_SPEAKER, merged.tolist())
        except Exception as exc:  # noqa: BLE001 - fail-open on manager error
            logger.error("[addressee] manager store failed (%s); enrollment rolled back", exc)
            self._target_embedding = None
            return False

        logger.info(
            "[addressee] enrolled target from %d segments (ema_alpha=%.2f, speakers=%d)",
            len(embeddings),
            ema_alpha,
            self._manager.num_speakers,
        )
        return True

    def is_enrolled(self) -> bool:
        """True when a target voice is registered (and the detector is up)."""
        if not self._available or self._manager is None:
            return False
        return self._manager.num_speakers > 0

    # ------------------------------------------------------------------
    # Classification
    # ------------------------------------------------------------------

    def classify(self, pcm: bytes) -> tuple[bool, float]:
        """Compare one PCM segment against the enrolled target.

        Returns ``(is_target, score)``. Fail-open:
          * detector unavailable / not enrolled -> ``(True, 1.0)`` (pass);
          * extraction failure / too-short -> ``(True, 1.0)`` (pass).
        """
        if not self.is_enrolled() or self._target_embedding is None:
            return True, 1.0
        emb = self.extract(pcm)
        if emb is None:
            return True, 1.0
        try:
            score = self._cosine(emb, self._target_embedding)
        except Exception as exc:  # noqa: BLE001 - fail-open on scoring error
            logger.error("[addressee] classify scoring failed (%s); fail-open", exc)
            return True, 1.0
        return bool(score >= self.target_threshold), float(score)

    @staticmethod
    def _cosine(a: np.ndarray, b: np.ndarray) -> float:
        """L2-normalized cosine similarity (embedding is not pre-normalized)."""
        na, nb = np.linalg.norm(a), np.linalg.norm(b)
        if na == 0.0 or nb == 0.0:
            return 0.0
        return float(np.dot(a, b) / (na * nb))


__all__ = ["AddresseeDetector"]
