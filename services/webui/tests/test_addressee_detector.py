"""AddresseeDetector (Phase 1) unit tests — mock sherpa-onnx Speaker API.

Covers the spec ``addressee-detection.md`` §3.1 contract:

  * extract too-short -> None (caller fail-open);
  * enroll success (2-3 usable segments) / failure (<2 usable);
  * classify threshold boundary (0.59 vs 0.60);
  * not-enrolled fail-open -> (True, 1.0);
  * load failure fail-open (available=False, all methods pass-through);
  * manager re-enroll (EMA) keeps the target usable.

The sherpa-onnx module is monkeypatched with deterministic fake
extractor/manager classes so tests never depend on the CAM++ model file.

Run: python -m pytest tests/test_addressee_detector.py -q
"""

from __future__ import annotations

import sys
import types

import numpy as np
import pytest

from joy_interaction_webui.addressee_detector import AddresseeDetector

SR = 16000
DIM = 192

# Deterministic unit embeddings (axis-aligned so cosine is exact).
EMB_TARGET = np.zeros(DIM, dtype=np.float32)
EMB_TARGET[0] = 1.0
EMB_OTHER = np.zeros(DIM, dtype=np.float32)
EMB_OTHER[1] = 1.0
# cosine(EMB_TARGET, EMB_NEAR_LOW) = 0.59
EMB_NEAR_LOW = 0.59 * EMB_TARGET + np.sqrt(1.0 - 0.59**2) * EMB_OTHER
# cosine(EMB_TARGET, EMB_NEAR_AT) ~= 0.60 — biased slightly above the
# threshold (0.602) so float32 rounding (measured 0.5899 for the exact 0.60
# vector) cannot push it under 0.6 and break the boundary assertion.
EMB_NEAR_AT = 0.602 * EMB_TARGET + np.sqrt(1.0 - 0.602**2) * EMB_OTHER


def pcm_with_value(value: float, n_samples: int) -> bytes:
    """Build 16 kHz mono int16 PCM whose first sample encodes ``value``."""
    first = int(np.clip(value, -1.0, 1.0) * 32767.0)
    arr = np.zeros(n_samples, dtype=np.int16)
    arr[0] = first
    return arr.tobytes()


def _embedding_for_samples(samples: np.ndarray) -> np.ndarray:
    """Map a float32 sample array to a deterministic test embedding."""
    if len(samples) < int(SR * 0.5):  # < 0.5s -> extractor not ready
        raise AssertionError("is_ready should gate short segments before compute")
    first = abs(float(samples[0]))
    if first < 0.01:
        return EMB_TARGET
    if abs(first - 0.59) < 0.01:
        return EMB_NEAR_LOW
    if abs(first - 0.60) < 0.01 or abs(first - 0.602) < 0.01:
        return EMB_NEAR_AT
    return EMB_OTHER


class FakeStream:
    def __init__(self) -> None:
        self._samples: np.ndarray | None = None
        self._finished = False

    def accept_waveform(self, sample_rate: int, samples) -> None:
        self._samples = np.asarray(samples, dtype=np.float32)

    def input_finished(self) -> None:
        self._finished = True

    def ready(self) -> bool:
        return bool(self._samples is not None and len(self._samples) >= int(SR * 0.5))

    def compute(self) -> list[float]:
        return _embedding_for_samples(self._samples).tolist()


class FakeExtractor:
    dim = DIM

    def __init__(self, cfg=None) -> None:
        self.cfg = cfg

    def create_stream(self) -> FakeStream:
        return FakeStream()

    def is_ready(self, stream: FakeStream) -> bool:
        return stream.ready()

    def compute(self, stream: FakeStream) -> list[float]:
        return stream.compute()


class FakeExtractorConfig:
    def __init__(self) -> None:
        self.model = ""
        self.num_threads = 1
        self.debug = False
        self.provider = ""
        self.validate = False


class FakeManager:
    def __init__(self, dim: int) -> None:
        self.dim = dim
        self._store: dict[str, list[float]] = {}

    @property
    def num_speakers(self) -> int:
        return len(self._store)

    def add(self, name: str, embedding: list[float]) -> bool:
        self._store[name] = list(embedding)
        return True

    def remove(self, name: str) -> None:
        self._store.pop(name, None)


@pytest.fixture
def fake_sherpa(monkeypatch, tmp_path):
    """Replace sherpa_onnx with deterministic fakes for the test duration."""
    fake = types.ModuleType("sherpa_onnx")
    fake.SpeakerEmbeddingExtractorConfig = FakeExtractorConfig
    fake.SpeakerEmbeddingExtractor = FakeExtractor
    fake.SpeakerEmbeddingManager = FakeManager
    monkeypatch.setitem(sys.modules, "sherpa_onnx", fake)
    # A resolvable (but never actually read) model file so the detector
    # proceeds to build the (faked) extractor.
    model = tmp_path / "3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx"
    model.write_bytes(b"fake-onnx")
    return str(tmp_path)


def _target_pcm(n_samples: int = SR * 2) -> bytes:
    return pcm_with_value(0.0, n_samples)


def _other_pcm(n_samples: int = SR * 2) -> bytes:
    return pcm_with_value(1.0, n_samples)


# ---------------------------------------------------------------------------
# extract
# ---------------------------------------------------------------------------


def test_extract_too_short_returns_none(fake_sherpa):
    det = AddresseeDetector(fake_sherpa)
    assert det.available is True
    short = pcm_with_value(0.0, int(SR * 0.2))  # 0.2s < 0.5s
    assert det.extract(short) is None


def test_extract_invalid_pcm_returns_none(fake_sherpa):
    det = AddresseeDetector(fake_sherpa)
    assert det.extract(b"\x00\x00\x00") is None  # odd length
    assert det.extract(b"") is None


def test_extract_returns_192dim_embedding(fake_sherpa):
    det = AddresseeDetector(fake_sherpa)
    emb = det.extract(_target_pcm())
    assert emb is not None
    assert emb.shape == (DIM,)
    assert emb.dtype == np.float32


# ---------------------------------------------------------------------------
# enroll
# ---------------------------------------------------------------------------


def test_enroll_success(fake_sherpa):
    det = AddresseeDetector(fake_sherpa)
    assert det.is_enrolled() is False
    ok = det.enroll([_target_pcm(), _target_pcm(), _target_pcm()])
    assert ok is True
    assert det.is_enrolled() is True


def test_enroll_two_segments_success(fake_sherpa):
    det = AddresseeDetector(fake_sherpa)
    ok = det.enroll([_target_pcm(), _target_pcm()])
    assert ok is True


def test_enroll_failure_too_few_usable(fake_sherpa):
    det = AddresseeDetector(fake_sherpa)
    # Only 1 usable segment (the other is too short -> extract None).
    ok = det.enroll([_target_pcm(), pcm_with_value(0.0, int(SR * 0.2))])
    assert ok is False
    assert det.is_enrolled() is False


def test_enroll_failure_no_segments(fake_sherpa):
    det = AddresseeDetector(fake_sherpa)
    assert det.enroll([]) is False
    assert det.is_enrolled() is False


def test_re_enroll_ema_keeps_target(fake_sherpa):
    det = AddresseeDetector(fake_sherpa)
    assert det.enroll([_target_pcm(), _target_pcm()]) is True
    # Re-enroll with more target voice (EMA merge path).
    assert det.enroll([_target_pcm(), _target_pcm()], ema_alpha=0.15) is True
    assert det.is_enrolled() is True
    is_target, score = det.classify(_target_pcm())
    assert is_target is True
    assert score > 0.99


# ---------------------------------------------------------------------------
# classify
# ---------------------------------------------------------------------------


def test_classify_not_enrolled_fail_open(fake_sherpa):
    det = AddresseeDetector(fake_sherpa)
    is_target, score = det.classify(_other_pcm())
    assert is_target is True
    assert score == 1.0


def test_classify_target_passes(fake_sherpa):
    det = AddresseeDetector(fake_sherpa)
    det.enroll([_target_pcm(), _target_pcm()])
    is_target, score = det.classify(_target_pcm())
    assert is_target is True
    assert score > 0.99


def test_classify_other_rejected(fake_sherpa):
    det = AddresseeDetector(fake_sherpa)
    det.enroll([_target_pcm(), _target_pcm()])
    is_target, score = det.classify(_other_pcm())
    assert is_target is False
    assert score < 0.01


def test_classify_threshold_boundary_059_vs_060(fake_sherpa):
    det = AddresseeDetector(fake_sherpa, target_threshold=0.6)
    det.enroll([_target_pcm(), _target_pcm()])

    low_is_target, low_score = det.classify(pcm_with_value(0.59, SR * 2))
    at_is_target, at_score = det.classify(pcm_with_value(0.602, SR * 2))

    assert abs(low_score - 0.59) < 2e-2
    assert abs(at_score - 0.60) < 2e-2
    assert low_is_target is False, "0.59 < 0.6 must be rejected"
    assert at_is_target is True, "0.60 >= 0.6 must pass"


def test_classify_too_short_fail_open(fake_sherpa):
    det = AddresseeDetector(fake_sherpa)
    det.enroll([_target_pcm(), _target_pcm()])
    is_target, score = det.classify(pcm_with_value(1.0, int(SR * 0.2)))
    assert is_target is True
    assert score == 1.0


# ---------------------------------------------------------------------------
# load failure fail-open
# ---------------------------------------------------------------------------


def test_load_failure_available_false(monkeypatch, tmp_path):
    fake = types.ModuleType("sherpa_onnx")
    fake.SpeakerEmbeddingExtractorConfig = FakeExtractorConfig
    fake.SpeakerEmbeddingExtractor = FakeExtractor

    class BoomManager:
        def __init__(self, dim: int) -> None:
            raise RuntimeError("manager boom")

    fake.SpeakerEmbeddingManager = BoomManager
    monkeypatch.setitem(sys.modules, "sherpa_onnx", fake)
    (tmp_path / "3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx").write_bytes(b"fake")

    det = AddresseeDetector(str(tmp_path))
    assert det.available is False
    assert det.is_enrolled() is False
    assert det.extract(_target_pcm()) is None
    assert det.enroll([_target_pcm(), _target_pcm()]) is False
    is_target, score = det.classify(_target_pcm())
    assert is_target is True
    assert score == 1.0


def test_model_missing_available_false(monkeypatch):
    fake = types.ModuleType("sherpa_onnx")
    fake.SpeakerEmbeddingExtractorConfig = FakeExtractorConfig
    fake.SpeakerEmbeddingExtractor = FakeExtractor
    fake.SpeakerEmbeddingManager = FakeManager
    monkeypatch.setitem(sys.modules, "sherpa_onnx", fake)

    det = AddresseeDetector("")  # no model path -> unavailable
    assert det.available is False
    is_target, score = det.classify(_target_pcm())
    assert is_target is True
    assert score == 1.0
