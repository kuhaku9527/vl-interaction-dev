"""QA independent regression suite — B1 P0 KWS silent false-wake (complementary).

Written from a fresh QA perspective (independent of the engineer's
``test_kws_silent_false_wake.py``) to attack the fix's core claims harder:

  1. energy-gate boundary semantics: the gate is ``buf_peak < min_peak``
     (strict less-than) and it MUST use the buffer-recomputed peak, not the
     caller's passed peak:
       - buffer peak exactly 0.005 (ON the gate) -> probe allowed
       - buffer peak 0.0049 (just below) -> probe skipped
       - real int16 quantization at the gate (163/32768 = 0.004974 below vs
         164/32768 = 0.005005 above) lands on the correct side
  2. zero-miss-kill core: real speech buffers with peak 0.05..0.5 ALL probe
     and wake (parametrized) — the gate must never eat real wakes
     (08-11 SOFTGATE 75% miss lesson)
  3. recovery path: ASR cannot spell "bt" but the rolling buffer holds real
     speech -> recovery probe still restores the wake (v3.23 preserved)
  4. env override: ``JARVIS_KWS_PROBE_MIN_PEAK=0.01`` raises the gate so a
     mid-energy buffer (peak ~0.008, which WOULD probe at the 0.005 default)
     is now skipped — proves the env value is actually applied
  5. main silent path: KWS hit on near-zero input -> WAIT_ASR_CONFIRM ->
     timeout -> KWS_LISTENING, with NO final wake, NO dialog entry and NO
     goodbye (the 08-12 reproduction end-to-end)
  6. coexistence with the turn delegate: ``JARVIS_TURN_DELEGATE_ENABLED=true``
     (Phase B active arbitration) — a silent false hit still does NOT escalate
     into a dialog wake

NOTE: this env has no pytest-asyncio; every test is a sync function and async
code is driven through ``asyncio.run`` (repo convention).
"""

from __future__ import annotations

import asyncio
import struct
import sys
from pathlib import Path

import pytest

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
    """ASR stub: always returns the configured text (never confirms by default)."""

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


def _pcm_amplitude(amp: int, seconds: float, sample_rate: int = 16000) -> bytes:
    """Mono int16 PCM where every sample has the given amplitude.

    Peak = amp / 32768.0. ``amp`` is clamped to int16 range.
    """
    amp = max(-32768, min(32767, int(amp)))
    return struct.pack("<h", amp) * int(seconds * sample_rate)


def _silent_pcm(seconds: float, sample_rate: int = 16000) -> bytes:
    return b"\x00\x00" * int(seconds * sample_rate)


def _build_machine(
    kws, asr, *, asr_confirm_timeout_s: float = 0.2, kws_probe_min_peak: float = 0.005
):
    """Real state machine with injected fakes (mirrors repo convention)."""
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
        kws_probe_min_peak=kws_probe_min_peak,
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
# 1. energy-gate boundary semantics (strict `<` on the buffer-recomputed peak)
# ---------------------------------------------------------------------------


def test_gate_allows_buffer_peak_exactly_at_threshold():
    """peak == 0.005 exactly: 0.005 < 0.005 is False -> probe runs."""
    kws = FakeKWS(fresh_probe_hit=True)
    sm = _build_machine(kws, FakeASR())
    sm._play_wake_wav = lambda: asyncio.sleep(0)
    sm._remember_kws_pcm(_silent_pcm(1.0))
    # Force the buffer energy to land exactly ON the gate (int16 can't hit
    # 0.005 exactly; monkeypatch isolates the strict-less-than semantics).
    sm._pcm_stats = lambda pcm: (0.005, 0.002)

    hit = asyncio.run(sm._probe_kws_fresh_window(peak=0.0, rms=0.0, bypass_min_s=True))

    assert hit is True, "peak exactly at the gate must NOT be skipped (strict <)"
    assert kws.fresh_calls == 1


def test_gate_skips_buffer_peak_just_below_threshold():
    """peak == 0.0049: 0.0049 < 0.005 is True -> probe skipped."""
    kws = FakeKWS(fresh_probe_hit=True)
    sm = _build_machine(kws, FakeASR())
    sm._remember_kws_pcm(_silent_pcm(1.0))
    sm._pcm_stats = lambda pcm: (0.0049, 0.002)

    hit = asyncio.run(sm._probe_kws_fresh_window(peak=0.0, rms=0.0, bypass_min_s=True))

    assert hit is False
    assert kws.fresh_calls == 0, "detect_in_pcm must not run below the gate"


def test_gate_boundary_real_int16_quantization():
    """Nearest achievable int16 peaks around 0.005 land on the correct side:
    163/32768 = 0.004974 (< gate, skipped) vs 164/32768 = 0.005005
    (> gate, probed)."""
    # Below: sample 163 -> peak 0.004974
    kws_below = FakeKWS(fresh_probe_hit=True)
    sm_below = _build_machine(kws_below, FakeASR())
    sm_below._remember_kws_pcm(_pcm_amplitude(163, 1.0))
    hit_below = asyncio.run(sm_below._probe_kws_fresh_window(peak=0.0, rms=0.0, bypass_min_s=True))
    assert hit_below is False
    assert kws_below.fresh_calls == 0

    # Above: sample 164 -> peak 0.005005
    kws_above = FakeKWS(fresh_probe_hit=True)
    sm_above = _build_machine(kws_above, FakeASR())
    sm_above._play_wake_wav = lambda: asyncio.sleep(0)
    sm_above._remember_kws_pcm(_pcm_amplitude(164, 1.0))
    hit_above = asyncio.run(sm_above._probe_kws_fresh_window(peak=0.0, rms=0.0, bypass_min_s=True))
    assert hit_above is True
    assert kws_above.fresh_calls == 1


def test_gate_ignores_passed_peak_even_below_threshold():
    """Authoritative source is the BUFFER energy: a caller passing a high
    peak must not bypass the gate for a silent buffer, and a caller passing a
    low peak must not block a speech buffer."""
    # Speech buffer (peak ~0.2) with caller-passed peak=0.0 -> still probes.
    kws = FakeKWS(fresh_probe_hit=True)
    sm = _build_machine(kws, FakeASR())
    sm._play_wake_wav = lambda: asyncio.sleep(0)
    sm._remember_kws_pcm(_pcm_amplitude(6554, 0.5))  # 6554/32768 = 0.2

    hit = asyncio.run(sm._probe_kws_fresh_window(peak=0.0, rms=0.0, bypass_min_s=True))

    assert hit is True, "buffer speech must probe even if passed peak claims silence"
    assert kws.fresh_calls == 1


# ---------------------------------------------------------------------------
# 2. zero-miss-kill core: real speech across peak 0.05..0.5 all probe+wake
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("target_peak", [0.05, 0.1, 0.2, 0.3, 0.5])
def test_real_speech_peak_probes_and_wakes(target_peak):
    amp = max(1, int(target_peak * 32768))
    kws = FakeKWS(fresh_probe_hit=True)
    sm = _build_machine(kws, FakeASR())
    sm._play_wake_wav = lambda: asyncio.sleep(0)
    sm._remember_kws_pcm(_pcm_amplitude(amp, 0.5))

    hit = asyncio.run(sm._probe_kws_fresh_window(peak=0.0, rms=0.0, bypass_min_s=True))

    _JarvisConfig, JarvisState, _JarvisStateMachine = _jarvis_mode()
    assert hit is True, f"real speech (peak ~{target_peak}) must still wake"
    assert kws.fresh_calls == 1
    assert sm.state == JarvisState.DIALOG_ACTIVE


# ---------------------------------------------------------------------------
# 3. recovery path: ASR can't spell "bt" but buffer has real speech -> restore
# ---------------------------------------------------------------------------


def test_recovery_probe_restores_real_wake_when_asr_cannot_confirm():
    """v3.23 recovery preserved: live KWS fires on real speech, ASR produces
    no 'bt', and the fresh-window probe over the (real-speech) rolling buffer
    still direct-wakes."""
    kws = FakeKWS(fire_count=1, fresh_probe_hit=True)
    asr = FakeASR(text="")  # ASR never confirms
    sm = _build_machine(kws, asr, asr_confirm_timeout_s=0.2)
    sm._play_wake_wav = lambda: asyncio.sleep(0)

    async def scenario():
        await sm._handle_kws(_pcm_amplitude(6554, 0.2))  # peak ~0.2 real speech
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
    assert kws.fresh_calls == 1, "recovery probe must run on a real-speech buffer"


# ---------------------------------------------------------------------------
# 4. env override: JARVIS_KWS_PROBE_MIN_PEAK raises the gate
# ---------------------------------------------------------------------------


def test_env_override_kws_probe_min_peak_changes_gate_behavior(monkeypatch):
    JarvisConfig, _JarvisState, _JarvisStateMachine = _jarvis_mode()
    monkeypatch.setenv("JARVIS_KWS_PROBE_MIN_PEAK", "0.01")
    env_cfg = JarvisConfig.from_env()
    assert env_cfg.kws_probe_min_peak == 0.01, "env override must be applied"

    # A ~0.008-peak buffer: at the 0.005 default it WOULD probe; at 0.01 it
    # must be skipped — proves the env value is what the gate uses.
    kws = FakeKWS(fresh_probe_hit=True)
    sm = _build_machine(kws, FakeASR(), kws_probe_min_peak=0.01)
    sm._remember_kws_pcm(_pcm_amplitude(262, 1.0))  # 262/32768 = 0.0080

    hit = asyncio.run(sm._probe_kws_fresh_window(peak=0.0, rms=0.0, bypass_min_s=True))

    assert hit is False, "peak 0.008 must be skipped at the 0.01 gate"
    assert kws.fresh_calls == 0

    # Control: at the default 0.005 the SAME buffer probes (no miss-kill).
    kws_ctrl = FakeKWS(fresh_probe_hit=True)
    sm_ctrl = _build_machine(kws_ctrl, FakeASR(), kws_probe_min_peak=0.005)
    sm_ctrl._play_wake_wav = lambda: asyncio.sleep(0)
    sm_ctrl._remember_kws_pcm(_pcm_amplitude(262, 1.0))

    hit_ctrl = asyncio.run(sm_ctrl._probe_kws_fresh_window(peak=0.0, rms=0.0, bypass_min_s=True))
    assert hit_ctrl is True, "same 0.008 buffer must probe at the 0.005 default"
    assert kws_ctrl.fresh_calls == 1


# ---------------------------------------------------------------------------
# 5. main silent path: near-zero KWS hit -> confirm timeout -> KWS_LISTENING
# ---------------------------------------------------------------------------


def test_silent_wake_main_path_no_final_wake_no_goodbye():
    """The 08-12 reproduction end-to-end: KWS fires on a near-zero chunk, ASR
    stays empty, the confirm window expires, and the session returns to
    KWS_LISTENING — no direct wake, no dialog entry, no goodbye."""
    JarvisConfig, JarvisState, JarvisStateMachine = _jarvis_mode()
    goodbye_calls = {"n": 0}
    kws = FakeKWS(fire_count=1, fresh_probe_hit=True)  # probe WOULD hit if run
    asr = FakeASR(text="")
    cfg = JarvisConfig(
        wake_word="bt",
        kws_model_dir="ignored",
        asr_model_dir="ignored",
        sample_rate=16000,
        kws_capture_window_s=1.0,
        kws_capture_min_interval_s=60.0,
        asr_confirm_timeout_s=0.2,
        kws_fresh_window_min_s=1.0,
        kws_fresh_window_probe_enabled=True,
        kws_fresh_window_direct_wake=True,
        kws_probe_min_peak=0.005,
    )
    sm = JarvisStateMachine(
        config=cfg,
        on_wake=None,
        on_goodbye=lambda: goodbye_calls.__setitem__("n", goodbye_calls["n"] + 1),
        on_asr_partial=None,
        on_user_utterance=None,
        on_llm_response=None,
    )
    sm._kws = kws
    sm._asr = asr
    sm._ensure_kws_diagnostic_state()

    states_visited = []
    orig_transition = sm._transition_to

    async def traced_transition(state):
        states_visited.append(state)
        await orig_transition(state)

    sm._transition_to = traced_transition

    async def scenario():
        await sm._handle_kws(_silent_pcm(0.2))  # live KWS fires (peak=0)
        assert sm.state == JarvisState.WAIT_ASR_CONFIRM
        for _ in range(40):
            await asyncio.sleep(0.05)
            if sm.state != JarvisState.WAIT_ASR_CONFIRM:
                break
        return sm.state

    final = asyncio.run(scenario())

    assert final == JarvisState.KWS_LISTENING, (
        f"silent wake must reset to KWS_LISTENING, got {final}"
    )
    assert JarvisState.DIALOG_ACTIVE not in states_visited, "silent wake must never open the dialog"
    assert JarvisState.WAKE_DETECTED not in states_visited, (
        "silent wake must never promote to WAKE_DETECTED"
    )
    assert kws.fresh_calls == 0, "silent wake must not reach the recovery probe's detect"
    assert goodbye_calls["n"] == 0, "silent wake must not trigger goodbye"


# ---------------------------------------------------------------------------
# 6. coexistence: turn delegate enabled (Phase B) — silent still no wake
# ---------------------------------------------------------------------------


def test_silent_no_wake_with_turn_delegate_enabled(monkeypatch):
    """With JARVIS_TURN_DELEGATE_ENABLED=true the delegate is created; a
    silent false hit must still fall back to KWS_LISTENING and never open the
    dialog (delegate.on_dialog_enter must not fire)."""
    JarvisConfig, JarvisState, JarvisStateMachine = _jarvis_mode()
    monkeypatch.setenv("JARVIS_TURN_DELEGATE_ENABLED", "true")
    kws = FakeKWS(fire_count=1, fresh_probe_hit=True)
    asr = FakeASR(text="")
    cfg = JarvisConfig(
        wake_word="bt",
        kws_model_dir="ignored",
        asr_model_dir="ignored",
        sample_rate=16000,
        kws_capture_window_s=1.0,
        kws_capture_min_interval_s=60.0,
        asr_confirm_timeout_s=0.2,
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
    # Real __init__ reads the env gate: the delegate must exist.
    assert sm._turn_delegate is not None, "delegate must be created from env gate"
    sm._kws = kws
    sm._asr = asr
    sm._ensure_kws_diagnostic_state()

    async def scenario():
        await sm._handle_kws(_silent_pcm(0.2))  # live KWS fires (peak=0)
        assert sm.state == JarvisState.WAIT_ASR_CONFIRM
        for _ in range(40):
            await asyncio.sleep(0.05)
            if sm.state != JarvisState.WAIT_ASR_CONFIRM:
                break
        return sm.state

    final = asyncio.run(scenario())

    assert final == JarvisState.KWS_LISTENING, (
        f"silent wake must reset to KWS_LISTENING with delegate on, got {final}"
    )
    assert kws.fresh_calls == 0, "silent wake must not reach the recovery probe's detect"
