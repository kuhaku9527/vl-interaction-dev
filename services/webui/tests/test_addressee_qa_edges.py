"""QA independent edge-case tests — Addressee Detection Phase 1 (严过关).

Independent verification, not copied from engineer's tests:

Detector unit edges (mock sherpa):
  * enroll with one corrupted/too-short segment + good segments still works;
  * classify empty/None/odd-length PCM fail-open;
  * repeated classify same-person score stability (idempotent, no state drift);
  * EMA re-enroll replaces old embedding (classify against new voice after
    re-enroll no longer matches the old one).

Live gating edges (fake VAD/ASR/detector):
  * VAD unavailable (vad_bypass.available=False) -> gating skipped (pass);
  * enroll cancel mid-way -> normal dialog resumes (ASR works again);
  * finish with <2 segments -> failure, phase ends, no enrollment;
  * non-target drop: closing silence chunk never reaches ASR;
  * feed_enroll_pcm overflow (4th segment ignored).

Real-model end-to-end (skipped when CAM++ model is missing):
  * enroll from BT reference voice slices -> same-person classify passes,
    different-person (user mic) classify drops — proves the gate works with
    the REAL model, not just mocks.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[3]
WEBUI_SRC = REPO / "services" / "webui" / "src"
for _p in (str(REPO), str(WEBUI_SRC)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from joy_interaction_webui.addressee_detector import AddresseeDetector  # noqa: E402
from joy_interaction_webui.live_mode import LiveStateMachine  # noqa: E402
from joy_interaction_webui.turn_controller import TurnState  # noqa: E402

SR = 16000
DIM = 192

# Deterministic unit embeddings (axis-aligned -> exact cosine).
EMB_TARGET = np.zeros(DIM, dtype=np.float32)
EMB_TARGET[0] = 1.0
EMB_OTHER = np.zeros(DIM, dtype=np.float32)
EMB_OTHER[1] = 1.0
EMB_THIRD = np.zeros(DIM, dtype=np.float32)
EMB_THIRD[2] = 1.0


def pcm_with_value(value: float, n_samples: int) -> bytes:
    first = int(np.clip(value, -1.0, 1.0) * 32767.0)
    arr = np.zeros(n_samples, dtype=np.int16)
    arr[0] = first
    return arr.tobytes()


def _embedding_for_samples(samples: np.ndarray) -> np.ndarray:
    if len(samples) < int(SR * 0.5):
        raise AssertionError("is_ready should gate short segments")
    first = abs(float(samples[0]))
    if first < 0.01:
        return EMB_TARGET
    if abs(first - 2.0) < 0.01:
        return EMB_THIRD
    return EMB_OTHER


class FakeStream:
    def __init__(self) -> None:
        self._samples = None

    def accept_waveform(self, sample_rate: int, samples) -> None:
        self._samples = np.asarray(samples, dtype=np.float32)

    def input_finished(self) -> None:
        pass

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
    fake = types.ModuleType("sherpa_onnx")
    fake.SpeakerEmbeddingExtractorConfig = FakeExtractorConfig
    fake.SpeakerEmbeddingExtractor = FakeExtractor
    fake.SpeakerEmbeddingManager = FakeManager
    monkeypatch.setitem(sys.modules, "sherpa_onnx", fake)
    model = tmp_path / "3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx"
    model.write_bytes(b"fake-onnx")
    return str(tmp_path)


def _target_pcm(n: int = SR * 2) -> bytes:
    return pcm_with_value(0.0, n)


def _other_pcm(n: int = SR * 2) -> bytes:
    return pcm_with_value(1.0, n)


def _third_pcm(n: int = SR * 2) -> bytes:
    return pcm_with_value(2.0, n)


# ---------------------------------------------------------------------------
# Detector unit edges
# ---------------------------------------------------------------------------


def test_enroll_with_one_corrupt_segment_still_works(fake_sherpa):
    """1 corrupted (too-short -> extract None) + 2 good -> enroll succeeds."""
    det = AddresseeDetector(fake_sherpa)
    ok = det.enroll([_target_pcm(), pcm_with_value(0.0, int(SR * 0.2)), _target_pcm()])
    assert ok is True
    assert det.is_enrolled() is True
    # The good segments (target voice) still dominate the mean.
    is_target, score = det.classify(_target_pcm())
    assert is_target is True
    assert score > 0.99


def test_enroll_all_corrupt_fails(fake_sherpa):
    det = AddresseeDetector(fake_sherpa)
    ok = det.enroll([pcm_with_value(0.0, int(SR * 0.1)), pcm_with_value(0.0, int(SR * 0.2))])
    assert ok is False
    assert det.is_enrolled() is False


def test_classify_empty_and_odd_length_fail_open(fake_sherpa):
    det = AddresseeDetector(fake_sherpa)
    det.enroll([_target_pcm(), _target_pcm()])
    for bad in (b"", b"\x00", b"\x00\x00\x00"):
        is_target, score = det.classify(bad)
        assert is_target is True
        assert score == 1.0


def test_repeated_classify_same_person_stable(fake_sherpa):
    """Classify the same voice 20x -> identical result every time (no state)."""
    det = AddresseeDetector(fake_sherpa)
    det.enroll([_target_pcm(), _target_pcm()])
    results = [det.classify(_target_pcm()) for _ in range(20)]
    scores = [s for _, s in results]
    assert all(t is True for t, _ in results)
    assert max(scores) - min(scores) < 1e-5, "score must be stable across calls"
    assert results[0][1] == results[-1][1]


def test_ema_re_enroll_replaces_old_embedding(fake_sherpa):
    """Re-enroll with a different voice -> classify flips to the new voice."""
    det = AddresseeDetector(fake_sherpa)
    assert det.enroll([_target_pcm(), _target_pcm()]) is True
    assert det.classify(_target_pcm())[0] is True
    assert det.classify(_other_pcm())[0] is False

    # Re-enroll with the THIRD voice; EMA alpha 1.0 -> fully replaced.
    assert det.enroll([_third_pcm(), _third_pcm()], ema_alpha=1.0) is True
    assert det.classify(_third_pcm())[0] is True, "new voice must pass"
    assert det.classify(_target_pcm())[0] is False, "old voice must be replaced"


# ---------------------------------------------------------------------------
# Live gating edges (fake VAD / ASR / detector)
# ---------------------------------------------------------------------------

# Reuse the same test doubles pattern as test_live_mode (imported at runtime
# to avoid duplicating fakes; the module is on sys.path via conftest).


class _FakeVAD:
    available = True

    def __init__(self) -> None:
        self.speech = False
        self.accepted = 0

    def accept_waveform(self, samples) -> None:
        self.accepted += 1

    def is_speech(self) -> bool:
        return self.speech

    def set_speech(self, value: bool) -> None:
        self.speech = value


class _FakeASR:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.feed_calls = 0

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def feed_chunk(self, pcm: bytes) -> str:
        self.feed_calls += 1
        return self.text

    def set_text(self, text: str) -> None:
        self.text = text


class _FakeDetector:
    available = True

    def __init__(self, *, enrolled=True, target=True, score=0.9) -> None:
        self._enrolled = enrolled
        self._target = target
        self._score = score
        self.enroll_calls: list = []
        self.num_enroll_calls = 0

    def is_enrolled(self) -> bool:
        return self._enrolled

    def classify(self, pcm: bytes) -> tuple[bool, float]:
        if not self._enrolled:
            return True, 1.0
        return self._target, self._score

    def enroll(self, segments) -> bool:
        self.enroll_calls.append(list(segments))
        self.num_enroll_calls += 1
        # Faithful to the real AddresseeDetector contract: >= 2 usable
        # segments required (usable = non-empty; corruption handled by
        # the real extract returning None, which the unit tests cover).
        if len(segments) < 2:
            return False
        self._enrolled = True
        return True


def _stub_config():
    from types import SimpleNamespace

    return SimpleNamespace(
        llm_endpoint="http://127.0.0.1:1",
        tts_endpoint="http://127.0.0.1:1",
        llm_api_key="",
        tts_api_key="",
        asr_endpoint="http://127.0.0.1:1",
        asr_api_key="",
    )


def _build(vad=None, asr=None, **overrides):
    vad = vad if vad is not None else _FakeVAD()
    asr = asr if asr is not None else _FakeASR()
    sm = LiveStateMachine(
        config=_stub_config(),
        session_id="qa-edge",
        vad=vad,
        asr=asr,
        **overrides,
    )
    return sm, vad, asr


def _set_detector(sm, **kwargs) -> _FakeDetector:
    det = _FakeDetector(**kwargs)
    sm._addressee = det
    return det


async def _speech_burst(sm, vad, chunks: int = 6, chunk: bytes | None = None):
    chunk = chunk or (b"\x00\x00" * 1000)  # 1000 int16 = 2000 bytes
    vad.set_speech(True)
    for _ in range(chunks):
        await sm.feed_audio(chunk)
    vad.set_speech(False)
    await sm.feed_audio(chunk)


@pytest.mark.asyncio
async def test_vad_unavailable_gating_skipped():
    """VAD unavailable -> gating skipped, original streaming path (fail-open)."""
    class NoVAD(_FakeVAD):
        available = False

    sm, vad, asr = _build(vad=NoVAD())
    _set_detector(sm, enrolled=True, target=False, score=0.3)  # would drop
    asr.set_text("你好")  # VAD unavailable -> speech signal from ASR growth
    vad.set_speech(True)
    await sm.feed_audio(b"\x00\x00" * 1000)
    assert asr.feed_calls == 1, "VAD unavailable must bypass the gate"
    assert sm.turn_state == TurnState.USER_SPEAKING


@pytest.mark.asyncio
async def test_enroll_cancel_resumes_normal_dialog():
    """Cancel enrollment mid-way -> ASR dialog works again immediately."""
    sm, vad, asr = _build()
    _set_detector(sm, enrolled=False)
    assert sm.start_enroll() is True
    # During enroll phase, audio must NOT reach ASR.
    vad.set_speech(True)
    await sm.feed_audio(b"\x00\x00" * 1000)
    assert asr.feed_calls == 0, "enroll phase audio must not hit ASR"
    vad.set_speech(False)

    sm.cancel_enroll()
    assert sm.enroll_phase is False
    assert sm.enroll_segment_count == 0

    # Normal dialog resumes.
    asr.set_text("你好")
    await _speech_burst(sm, vad, chunks=1)
    assert asr.feed_calls >= 1
    assert sm.turn_state == TurnState.USER_SPEAKING


@pytest.mark.asyncio
async def test_finish_enroll_insufficient_segments_fails():
    """finish with <2 segments -> fail, phase ends, nothing enrolled."""
    sm, vad, _asr = _build()
    det = _set_detector(sm, enrolled=False)
    assert sm.start_enroll() is True
    # Only 1 segment captured: 18 chunks x 2000 bytes = 36000 bytes = 1.125s
    # (>= the 1.0s enrollment minimum -> exactly 1 segment buffered).
    await _speech_burst(sm, vad, chunks=18)
    assert sm.enroll_segment_count == 1

    ok = sm.finish_enroll()
    assert ok is False
    assert sm.enroll_phase is False
    # finish_enroll delegates the >=2 validation to the detector, which
    # rejects the 1-segment buffer (logged: need >=2 usable segments).
    assert det.num_enroll_calls == 1
    assert len(det.enroll_calls[0]) == 1
    assert sm.addressee_enrolled is False


@pytest.mark.asyncio
async def test_non_target_closing_chunk_never_reaches_asr():
    """Dropped segment: its closing silence chunk must not reach ASR."""
    sm, vad, asr = _build()
    _set_detector(sm, enrolled=True, target=False, score=0.2)

    vad.set_speech(True)
    for _ in range(6):
        await sm.feed_audio(b"\x00\x00" * 1000)
    vad.set_speech(False)
    await sm.feed_audio(b"\x00\x00" * 1000)  # closing chunk
    assert asr.feed_calls == 0, "closing chunk of a dropped segment must be dropped too"


@pytest.mark.asyncio
async def test_feed_enroll_pcm_overflow_ignored():
    """4th enrollment segment via pcm route is ignored (max 3)."""
    sm, _vad, _ = _build()
    _set_detector(sm, enrolled=False)
    assert sm.start_enroll() is True
    pcm = b"\x00\x00" * 4000
    sm.feed_enroll_pcm(pcm)
    sm.feed_enroll_pcm(pcm)
    sm.feed_enroll_pcm(pcm)
    sm.feed_enroll_pcm(pcm)  # 4th -> ignored
    assert sm.enroll_segment_count == 3
    assert sm.finish_enroll() is True


# ---------------------------------------------------------------------------
# Real-model end-to-end (runs only when the CAM++ model is present)
# ---------------------------------------------------------------------------

REAL_MODEL = "D:/AI/models/sherpa-onnx/models/speaker/3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx"
BT_REF = "D:/AI/workspace/bt-voice/ref_audio/bt_reference.wav"
MIC_CAPTURES = "D:/AI/data/kws/mic_captures"


def _model_available() -> bool:
    return Path(REAL_MODEL).is_file()


def _read_wav_mono16(path: str):
    import wave

    with wave.open(path, "rb") as w:
        sr = w.getframerate()
        nch = w.getnchannels()
        raw = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    if nch > 1:
        raw = raw.reshape(-1, nch)[:, 0]
    samples = raw.astype(np.float32) / 32768.0
    if sr != 16000:
        x_old = np.linspace(0.0, 1.0, num=len(samples), endpoint=False)
        x_new = np.linspace(0.0, 1.0, num=int(len(samples) * 16000 / sr), endpoint=False)
        samples = np.interp(x_new, x_old, samples).astype(np.float32)
        sr = 16000
    return samples, sr


def _slice_2s(samples: np.ndarray, sr: int = 16000) -> list[np.ndarray]:
    n = int(sr * 2.0)
    return [samples[i : i + n] for i in range(0, len(samples) - n + 1, n)]


def _samples_to_pcm(samples: np.ndarray) -> bytes:
    return (np.clip(samples, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()


def _top_rms_user_captures(n: int = 6) -> list[str]:
    """Pick the loudest >=2.5s non-bad mic captures (real user voice)."""
    import glob
    import os
    import wave

    paths = [p for p in glob.glob(os.path.join(MIC_CAPTURES, "*.wav")) if "_bad" not in p]

    def rms(p: str) -> float:
        try:
            with wave.open(p, "rb") as w:
                raw = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
                sr = w.getframerate()
            if len(raw) / sr < 2.5:
                return -1.0
            s = raw.astype(np.float32) / 32768.0
            return float(np.sqrt(np.mean(s * s)))
        except Exception:
            return -1.0

    paths.sort(key=rms, reverse=True)
    good = [p for p in paths if rms(p) >= 0.08][:n]
    return good or paths[:n]


@pytest.mark.skipif(not _model_available(), reason="CAM++ model not present")
def test_real_model_enroll_bt_classify_bt_vs_user():
    """End-to-end with the REAL CAM++ model: enroll BT voice -> BT passes,
    user mic voice drops (non-target). This proves the gate is real."""
    from joy_interaction_webui.addressee_detector import AddresseeDetector

    det = AddresseeDetector(REAL_MODEL, num_threads=2, target_threshold=0.6)
    assert det.available is True

    bt_samples, sr = _read_wav_mono16(BT_REF)
    bt_slices = _slice_2s(bt_samples, sr)[:3]  # 3 x 2s enrollment segments
    assert len(bt_slices) >= 2
    ok = det.enroll([_samples_to_pcm(s) for s in bt_slices])
    assert ok is True, "real-model enrollment from BT voice must succeed"
    assert det.is_enrolled() is True

    # Remaining BT slices (same person) -> target passes.
    passed = 0
    total = 0
    for sl in _slice_2s(bt_samples, sr)[3:]:
        is_target, _score = det.classify(_samples_to_pcm(sl))
        total += 1
        if is_target:
            passed += 1
    assert total >= 3, f"expected >=3 BT slices to classify, got {total}"
    assert passed / total >= 0.8, (
        f"same-person recall too low with real model: {passed}/{total} (scores low)"
    )

    # User mic captures (different person) -> non-target drops.
    user_paths = _top_rms_user_captures(4)
    dropped = 0
    utotal = 0
    for p in user_paths:
        samples, sr2 = _read_wav_mono16(p)
        for sl in _slice_2s(samples, sr2):
            if len(_samples_to_pcm(sl)) < 9600:
                continue
            is_target, _score = det.classify(_samples_to_pcm(sl))
            utotal += 1
            if not is_target:
                dropped += 1
    assert utotal >= 4, f"expected >=4 user slices, got {utotal}"
    assert dropped / utotal >= 0.8, (
        f"different-person drop rate too low with real model: {dropped}/{utotal}"
    )


# ---------------------------------------------------------------------------
# Frontend static contract — liveEnrollBtn (index.html)
# ---------------------------------------------------------------------------

import re  # noqa: E402

INDEX_HTML = REPO / "services" / "webui" / "src" / "joy_interaction_webui" / "static" / "index.html"
# Batch-3 split: index.html's inline script#2 was extracted into standalone JS
# files (same dependency order as the <script src> tags). Assertions run against
# the combined sources so moved code keeps its contract with unchanged semantics.
_SPLIT_JS = (
    "app_boot.js",
    "app_main.js",
    "sidebar_toggle.js",
    "incremental_wiring.js",
    "vlm_history.js",
    "llm_reply_ui.js",
    "ws_dispatcher.js",
    "vlm_render.js",
    "background_rich.js",
    "tts_player.js",
    "speech_input.js",
    "live_ui.js",
    "llm_reply_audio.js",
    "status_poll.js",
)


def _html() -> str:
    parts = [INDEX_HTML.read_text(encoding="utf-8")]
    for name in _SPLIT_JS:
        parts.append((INDEX_HTML.parent / name).read_text(encoding="utf-8"))
    return "\n".join(parts)


def _function_body(html: str, name: str) -> str:
    match = re.search(
        rf"(?:async\s+)?function {name}\([^)]*\) \{{(?P<body>.*?)\n        \}}", html, re.S
    )
    assert match, f"missing function {name}"
    return match.group("body")


def test_frontend_enroll_button_exists_and_starts_disabled():
    html = _html()
    assert 'id="liveEnrollBtn"' in html
    # Initially disabled: enrollment only makes sense inside an active live
    # session (and while not already enrolling).
    assert "id=\"liveEnrollBtn\"" in html and "disabled" in html.split('id="liveEnrollBtn"')[1][:120]
    assert 'id="liveEnrollHint"' in html
    assert 'role="status"' in html and 'aria-live="polite"' in html
    assert "liveEnrollBtn.addEventListener('click', startLiveEnroll)" in html


def test_frontend_enroll_disabled_logic():
    html = _html()
    # Button enabled only when live is active AND not currently enrolling.
    assert "liveEnrollBtn.disabled = !liveModeActive || liveEnrollActive" in html
    assert "liveEnrollActive = Boolean(active)" in html
    assert "liveEnrollTimer = null" in html


def test_frontend_enroll_auto_finish_on_3_segments_and_timeout():
    html = _html()
    start = _function_body(html, "startLiveEnroll")
    # Poll /api/live/status; >=3 buffered segments -> auto finish.
    assert "'/api/live/enroll'" in start
    assert "action: 'start'" in start
    assert "enroll_segment_count >= 3" in start
    assert "finishLiveEnroll()" in start
    # 25s safety timeout (1s interval x 25 tries).
    assert "tries >= 25" in start
    assert "}, 1000)" in start

    finish = _function_body(html, "finishLiveEnroll")
    assert "action: 'finish'" in finish
    assert "liveEnrollCompleted = true" in finish

    cancel = _function_body(html, "cancelLiveEnroll")
    assert "action: 'cancel'" in cancel


def test_frontend_enroll_cancelled_on_live_stop():
    html = _html()
    stop = _function_body(html, "stopLiveMode")
    assert "cancelLiveEnroll()" in stop
    assert "liveEnrollActive" in stop


def test_live_session_enroll_delegation(monkeypatch):
    """LiveSession must delegate enroll methods to its state machine (regression:
    /api/live/enroll called session.start_enroll but LiveSession lacked the passthrough)."""
    from joy_interaction_webui.jarvis_session import LiveSession
    from joy_interaction_webui.live_mode import LiveStateMachine

    calls = {"start": 0, "feed": 0, "finish": 0, "cancel": 0}
    sm = LiveStateMachine.__new__(LiveStateMachine)
    sm.start_enroll = lambda: (calls.__setitem__("start", calls["start"] + 1), True)[1]
    sm.feed_enroll_pcm = lambda pcm: calls.__setitem__("feed", calls["feed"] + 1)
    sm.finish_enroll = lambda: (calls.__setitem__("finish", calls["finish"] + 1), True)[1]
    sm.cancel_enroll = lambda: calls.__setitem__("cancel", calls["cancel"] + 1)
    session = LiveSession(session_id="s1", state_machine=sm)

    assert session.start_enroll() is True
    session.feed_enroll_pcm(b"pcm")
    assert session.finish_enroll() is True
    session.cancel_enroll()
    assert calls == {"start": 1, "feed": 1, "finish": 1, "cancel": 1}
