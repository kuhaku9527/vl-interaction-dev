"""Tests for the Jarvis VAD bypass layer (form A: bypass + soft-gate, fail-open).

Mirrors the Smart Turn fail-open convention (see test_smart_turn.py): the
Silero VAD ONNX asset is optional. Without it the VadBypass stays
unavailable and is_speech() returns True (full KWS passthrough), never raising.

Run: pytest services/webui/tests/test_vad_bypass.py -v
"""

from __future__ import annotations

import asyncio
import importlib
import os
import sys
from pathlib import Path

import pytest

WEBUI_SRC = Path(__file__).resolve().parents[1] / "src"
if str(WEBUI_SRC) not in sys.path:
    sys.path.insert(0, str(WEBUI_SRC))

from joy_interaction_webui.jarvis_mode import JarvisConfig, JarvisStateMachine  # noqa: E402
from joy_interaction_webui.vad_bypass import VadBypass  # noqa: E402


# ---------------------------------------------------------------------------
# Fake collaborators for the soft-gate test
# ---------------------------------------------------------------------------
class FakeKWS:
    """Records feed_audio calls; no real engine, no network."""

    def __init__(self):
        self.feed_calls = 0
        self.last_pcm = None

    def feed_audio(self, pcm: bytes) -> bool:
        self.feed_calls += 1
        self.last_pcm = pcm
        return False  # never a hit, so _handle_kws takes the miss path

    def start(self):
        pass


class FakeVad:
    """Controllable stand-in for VadBypass (same surface as the real one)."""

    def __init__(self, speech: bool = True, available: bool = True):
        self._speech = speech
        self.available = available
        self.accept_calls = 0

    def accept_waveform(self, samples):
        self.accept_calls += 1

    def is_speech(self) -> bool:
        return self._speech

    def pop_segment(self):
        pass

    def current_segment(self):
        return None

    def reset(self):
        pass


def _silent_pcm(n_samples: int = 800) -> bytes:
    return b"\x00\x00" * n_samples


# ---------------------------------------------------------------------------
# Fail-open contract
# ---------------------------------------------------------------------------
def test_fail_open_disabled():
    bp = VadBypass(enabled=False, model_dir="")
    assert bp.available is False
    assert bp.is_speech() is True  # treat as speech => full KWS passthrough
    # No raise on any method.
    bp.accept_waveform([0.0] * 1600)
    bp.pop_segment()
    bp.current_segment()
    bp.reset()


def test_fail_open_enabled_but_model_missing(tmp_path):
    missing_dir = tmp_path / "no_silero_here"
    missing_dir.mkdir()
    bp = VadBypass(enabled=True, model_dir=str(missing_dir))
    assert bp.available is False
    assert bp.is_speech() is True
    # accept_waveform must not raise even when unavailable.
    bp.accept_waveform([0.0] * 1600)


def test_available_true_with_real_model(tmp_path):
    """If a silero_vad.onnx exists, VadBypass loads and is available.

    Skipped when the asset is absent (mirrors the repo's 'auto-skip until
    asset fetched' convention for optional ONNX weights).
    """
    model_dir = Path(os.environ.get("JARVIS_VAD_MODEL_DIR", "")).expanduser()
    if not (model_dir / "silero_vad.onnx").is_file():
        pytest.skip("silero_vad.onnx not present; fail-open path covered above")

    bp = VadBypass(enabled=True, model_dir=str(model_dir))
    assert bp.available is True
    assert bp.is_speech() is True or bp.is_speech() is False  # bool, no raise


# ---------------------------------------------------------------------------
# feed_audio annotation write
# ---------------------------------------------------------------------------
def test_feed_audio_writes_vad_annotation():
    cfg = JarvisConfig(vad_enabled=True)  # no model -> fail-open, but we inject
    sm = JarvisStateMachine(cfg)
    fake_vad = FakeVad(speech=True, available=True)
    sm._vad = fake_vad
    sm._last_vad_speech = False
    asyncio.run(sm.feed_audio(_silent_pcm()))
    assert fake_vad.accept_calls == 1
    assert sm._last_vad_speech is True


# ---------------------------------------------------------------------------
# Soft-gate (form A)
# ---------------------------------------------------------------------------
def test_softgate_skips_kws_on_silence():
    cfg = JarvisConfig(vad_enabled=True, vad_softgate=True)
    sm = JarvisStateMachine(cfg)
    fake_kws = FakeKWS()
    sm._kws = fake_kws
    fake_vad = FakeVad(speech=False, available=True)
    sm._vad = fake_vad
    # Simulate feed_audio having stored silence for this chunk.
    sm._last_vad_speech = fake_vad.is_speech()  # False
    asyncio.run(sm._handle_kws(_silent_pcm()))
    # Gate returned before _init_kws / kws.feed_audio.
    assert fake_kws.feed_calls == 0


def test_softgate_feeds_kws_on_speech():
    cfg = JarvisConfig(vad_enabled=True, vad_softgate=True)
    sm = JarvisStateMachine(cfg)
    fake_kws = FakeKWS()
    sm._kws = fake_kws
    fake_vad = FakeVad(speech=True, available=True)
    sm._vad = fake_vad
    # Neutralize downstream engine paths so the test stays hermetic.
    sm._init_kws = lambda: None  # type: ignore[assignment]

    async def _noop_probe(*_a, **_k):
        return False

    sm._probe_kws_fresh_window = _noop_probe  # type: ignore[assignment]
    sm._feed_kws_shadow_asr = lambda *a, **k: None  # type: ignore[assignment]
    sm._init_asr = lambda: None  # type: ignore[assignment]
    sm._last_vad_speech = True
    asyncio.run(sm._handle_kws(_silent_pcm()))
    assert fake_kws.feed_calls == 1


def test_softgate_off_passthrough_when_vad_available():
    """Default form A: soft-gate OFF => KWS always fed even if VAD sees silence."""
    cfg = JarvisConfig(vad_enabled=True, vad_softgate=False)
    sm = JarvisStateMachine(cfg)
    fake_kws = FakeKWS()
    sm._kws = fake_kws
    fake_vad = FakeVad(speech=False, available=True)
    sm._vad = fake_vad
    sm._init_kws = lambda: None  # type: ignore[assignment]

    async def _noop_probe(*_a, **_k):
        return False

    sm._probe_kws_fresh_window = _noop_probe  # type: ignore[assignment]
    sm._feed_kws_shadow_asr = lambda *a, **k: None  # type: ignore[assignment]
    sm._init_asr = lambda: None  # type: ignore[assignment]
    sm._last_vad_speech = False
    asyncio.run(sm._handle_kws(_silent_pcm()))
    assert fake_kws.feed_calls == 1


def test_fail_open_passthrough_when_vad_unavailable():
    """VAD unavailable => soft-gate never triggers; KWS always fed."""
    cfg = JarvisConfig(vad_enabled=True, vad_softgate=True)
    sm = JarvisStateMachine(cfg)
    fake_kws = FakeKWS()
    sm._kws = fake_kws
    # Real VadBypass with no model => unavailable => is_speech() == True.
    assert sm._vad.available is False
    sm._init_kws = lambda: None  # type: ignore[assignment]

    async def _noop_probe(*_a, **_k):
        return False

    sm._probe_kws_fresh_window = _noop_probe  # type: ignore[assignment]
    sm._feed_kws_shadow_asr = lambda *a, **k: None  # type: ignore[assignment]
    sm._init_asr = lambda: None  # type: ignore[assignment]
    sm._last_vad_speech = True  # is_speech() returns True when unavailable
    asyncio.run(sm._handle_kws(_silent_pcm()))
    assert fake_kws.feed_calls == 1


# ---------------------------------------------------------------------------
# from_env mapping for the new VAD vars
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _isolate_vad_env(monkeypatch: pytest.MonkeyPatch):
    for var in (
        "JARVIS_VAD_ENABLED",
        "JARVIS_VAD_MODEL_DIR",
        "JARVIS_VAD_MIN_SILENCE_S",
        "JARVIS_VAD_MIN_SPEECH_S",
        "JARVIS_VAD_THRESHOLD",
        "JARVIS_VAD_WINDOW_SIZE",
        "JARVIS_VAD_SOFTGATE",
    ):
        monkeypatch.delenv(var, raising=False)
    yield


def _reload_jarvis_mode():
    import joy_interaction_webui.jarvis_mode as mod

    importlib.reload(mod)
    return mod


def test_from_env_reads_vad_vars(monkeypatch):
    monkeypatch.setenv("JARVIS_VAD_ENABLED", "true")
    monkeypatch.setenv("JARVIS_VAD_MODEL_DIR", "D:/models/vad")
    monkeypatch.setenv("JARVIS_VAD_MIN_SILENCE_S", "0.3")
    monkeypatch.setenv("JARVIS_VAD_MIN_SPEECH_S", "0.1")
    monkeypatch.setenv("JARVIS_VAD_THRESHOLD", "0.4")
    monkeypatch.setenv("JARVIS_VAD_WINDOW_SIZE", "256")
    monkeypatch.setenv("JARVIS_VAD_SOFTGATE", "true")
    mod = _reload_jarvis_mode()
    cfg = mod.JarvisConfig.from_env()
    assert cfg.vad_enabled is True
    assert cfg.vad_model_dir == "D:/models/vad"
    assert cfg.vad_min_silence_duration == 0.3
    assert cfg.vad_min_speech_duration == 0.1
    assert cfg.vad_threshold == 0.4
    assert cfg.vad_window_size == 256
    assert cfg.vad_softgate is True


def test_from_env_vad_defaults_off():
    mod = _reload_jarvis_mode()
    cfg = mod.JarvisConfig.from_env()
    assert cfg.vad_enabled is False
    assert cfg.vad_softgate is False
    assert cfg.vad_model_dir == ""
