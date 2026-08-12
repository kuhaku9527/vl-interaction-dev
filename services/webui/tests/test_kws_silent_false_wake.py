"""B1 P0: KWS silent false-wake regression tests.

Pins the silent false-wake fixes (see
``doc/research/kws-silent-false-wake-diagnosis-2026-08-12.md`` §4 S1/S3):

  1. fresh-window KWS probe energy gate (``kws_probe_min_peak``): a
     pure-silence rolling buffer is skipped, so silent input can never
     escalate into a direct wake;
  2. recovery-path fix: ``_wait_asr_confirm_timeout`` no longer synthesizes
     a fake peak for silent wakes — a silent wake falls back to
     KWS_LISTENING instead of direct-waking;
  3. zero-miss-kill guarantee: real speech (peak >= gate) still wakes via
     both the live path and the recovery probe (08-11 SOFTGATE 75% miss
     lesson — the gate must never eat real wakes).

NOTE: this env has no pytest-asyncio; every test is a sync function and async
code is driven through ``asyncio.run`` (repo convention).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
WEBUI_SRC = REPO / "services" / "webui" / "src"
for _p in (str(REPO), str(WEBUI_SRC)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _jarvis_mode():
    """Import jarvis_mode lazily (repo convention — the module may be
    reloaded by test_jarvis_config_env, which would stale module-level
    class/enum bindings)."""
    from joy_interaction_webui.jarvis_mode import (  # lazy by design
        JarvisConfig,
        JarvisState,
        JarvisStateMachine,
    )

    return JarvisConfig, JarvisState, JarvisStateMachine


class FakeKWS:
    """KWS stub: fires live on ``fire_count``-th feed; probe hits per flag."""

    def __init__(self, fire_count: int = 1, fresh_probe_hit: bool = True):
        self._live_calls = 0
        self.fresh_calls = 0
        self.fire_count = fire_count
        self.fresh_probe_hit = fresh_probe_hit

    def start(self):
        pass

    def feed_audio(self, pcm: bytes) -> bool:
        self._live_calls += 1
        return self._live_calls == self.fire_count

    def detect_in_pcm(self, pcm: bytes) -> bool:
        self.fresh_calls += 1
        return self.fresh_probe_hit


class FakeASR:
    """ASR stub: always returns the configured partial (never confirms)."""

    def __init__(self, text: str = ""):
        self.text = text
        self.started = 0
        self.stopped = 0

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1

    def feed_chunk(self, pcm: bytes) -> str:
        return self.text


def _silent_pcm(seconds: float, sample_rate: int = 16000) -> bytes:
    return b"\x00\x00" * int(seconds * sample_rate)


def _speech_pcm(seconds: float, sample_rate: int = 16000) -> bytes:
    # 0x1000 = 4096 -> peak 4096/32768 = 0.125, well above the 0.005 gate.
    return b"\x00\x10" * int(seconds * sample_rate)


def _build_machine(kws, asr, *, asr_confirm_timeout_s: float = 0.2):
    """Real state machine with injected fakes (mirrors test_hybrid_recovery)."""
    JarvisConfig, _JarvisState, JarvisStateMachine = _jarvis_mode()
    cfg = JarvisConfig(
        wake_word="bt",
        kws_model_dir="ignored",
        asr_model_dir="ignored",
        sample_rate=16000,
        kws_capture_window_s=1.0,
        kws_capture_min_interval_s=60.0,
        asr_confirm_timeout_s=asr_confirm_timeout_s,
        kws_fresh_window_min_s=1.0,
        kws_fresh_window_probe_enabled=True,
        kws_fresh_window_direct_wake=True,
        kws_probe_min_peak=0.005,
    )
    sm = JarvisStateMachine(
        config=cfg,
        on_wake=None,
        on_goodbye=None,
        on_asr_partial=None,
        on_user_utterance=None,
        on_llm_response=None,
    )
    sm._kws = kws
    sm._asr = asr
    sm._ensure_kws_diagnostic_state()
    return sm


# ---------------------------------------------------------------------------
# 1. probe energy gate: pure-silence buffer is skipped
# ---------------------------------------------------------------------------


def test_silent_fresh_window_probe_is_skipped():
    kws = FakeKWS(fresh_probe_hit=True)  # would wake IF detect were called
    sm = _build_machine(kws, FakeASR())
    sm._remember_kws_pcm(_silent_pcm(1.0))  # 1s pure silence in the buffer

    hit = asyncio.run(sm._probe_kws_fresh_window(peak=0.0, rms=0.0, bypass_min_s=True))

    _JarvisConfig, JarvisState, _JarvisStateMachine = _jarvis_mode()
    assert hit is False, "silent buffer must not wake via the fresh-window probe"
    assert kws.fresh_calls == 0, "detect_in_pcm must not run on a silent buffer"
    assert sm.state == JarvisState.KWS_LISTENING


def test_silent_probe_skipped_even_when_passed_peak_is_high():
    """The gate uses the BUFFER energy, not the caller's passed peak."""
    kws = FakeKWS(fresh_probe_hit=True)
    sm = _build_machine(kws, FakeASR())
    sm._remember_kws_pcm(_silent_pcm(1.0))

    # passed peak=0.9 simulates a caller claiming speech; buffer is silent.
    hit = asyncio.run(sm._probe_kws_fresh_window(peak=0.9, rms=0.9, bypass_min_s=True))

    assert hit is False
    assert kws.fresh_calls == 0


# ---------------------------------------------------------------------------
# 2. zero-miss-kill: real speech still wakes via the probe
# ---------------------------------------------------------------------------


def test_real_speech_fresh_window_probe_still_wakes():
    kws = FakeKWS(fresh_probe_hit=True)
    sm = _build_machine(kws, FakeASR())
    sm._play_wake_wav = lambda: asyncio.sleep(0)
    sm._remember_kws_pcm(_speech_pcm(1.0))  # peak=0.125 >= 0.005

    hit = asyncio.run(sm._probe_kws_fresh_window(peak=0.0, rms=0.0, bypass_min_s=True))

    _JarvisConfig, JarvisState, _JarvisStateMachine = _jarvis_mode()
    assert hit is True, "real-speech buffer must still wake via the probe"
    assert kws.fresh_calls == 1
    assert sm.state == JarvisState.DIALOG_ACTIVE


# ---------------------------------------------------------------------------
# 3. main path: silent wake -> confirm timeout -> KWS_LISTENING (no direct wake)
# ---------------------------------------------------------------------------


def test_silent_wake_timeout_returns_to_kws_without_direct_wake():
    """The 08-12 reproduction: KWS fires on a silent chunk, ASR stays empty,
    and the recovery probe must NOT escalate it into a direct wake."""
    kws = FakeKWS(fire_count=1, fresh_probe_hit=True)  # probe would hit if run
    asr = FakeASR(text="")
    sm = _build_machine(kws, asr, asr_confirm_timeout_s=0.2)

    async def scenario():
        await sm._handle_kws(_silent_pcm(0.2))  # live KWS fires (peak=0)
        assert sm.state == JarvisState.WAIT_ASR_CONFIRM
        for _ in range(40):
            await asyncio.sleep(0.05)
            if sm.state != JarvisState.WAIT_ASR_CONFIRM:
                break
        return sm.state

    _JarvisConfig, JarvisState, _JarvisStateMachine = _jarvis_mode()
    final = asyncio.run(scenario())
    assert final == JarvisState.KWS_LISTENING, (
        f"silent wake must reset to KWS_LISTENING, got {final}"
    )
    assert kws.fresh_calls == 0, "silent wake must not reach the recovery probe's detect"


# ---------------------------------------------------------------------------
# 4. zero-miss-kill: real wake that ASR fails to confirm still recovers
# ---------------------------------------------------------------------------


def test_real_wake_timeout_recovers_via_probe():
    """v3.23 recovery preserved: real speech fires KWS, ASR fails to spell
    'bt', and the fresh-window probe still recovers the wake."""
    kws = FakeKWS(fire_count=1, fresh_probe_hit=True)
    asr = FakeASR(text="")
    sm = _build_machine(kws, asr, asr_confirm_timeout_s=0.2)
    sm._play_wake_wav = lambda: asyncio.sleep(0)

    async def scenario():
        await sm._handle_kws(_speech_pcm(0.2))  # live KWS fires (peak=0.125)
        assert sm.state == JarvisState.WAIT_ASR_CONFIRM
        for _ in range(40):
            await asyncio.sleep(0.05)
            if sm.state != JarvisState.WAIT_ASR_CONFIRM:
                break
        return sm.state

    _JarvisConfig, JarvisState, _JarvisStateMachine = _jarvis_mode()
    final = asyncio.run(scenario())
    assert final in (JarvisState.WAKE_DETECTED, JarvisState.DIALOG_ACTIVE), (
        f"real-speech recovery must still wake, got {final}"
    )
    assert kws.fresh_calls == 1, "real-speech recovery must still run the probe"


# ---------------------------------------------------------------------------
# 5. config: gate default + env override
# ---------------------------------------------------------------------------


def test_kws_probe_min_peak_default_and_env_override(monkeypatch):
    JarvisConfig, _JarvisState, _JarvisStateMachine = _jarvis_mode()
    assert JarvisConfig().kws_probe_min_peak == 0.005
    monkeypatch.setenv("JARVIS_KWS_PROBE_MIN_PEAK", "0.01")
    cfg = JarvisConfig.from_env()
    assert cfg.kws_probe_min_peak == 0.01
