"""Unit tests for the Turn Controller shadow adapter (Phase A: observe-only).

The shadow wraps a real ``TurnController`` (jarvis preset), is driven by
jarvis state transitions, and compares the jarvis chain against the
turn_controller chain without ever changing jarvis behavior. No real
ASR / LLM / TTS / VAD hardware is touched.

NOTE: this environment has no pytest-asyncio, so every test is a sync
function and async code is driven through ``asyncio.run``.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

WEBUI_SRC = Path(__file__).resolve().parents[1] / "src"
if str(WEBUI_SRC) not in sys.path:
    sys.path.insert(0, str(WEBUI_SRC))

from joy_interaction_webui.turn_controller import (  # noqa: E402
    TurnConfig,
    TurnController,
    TurnState,
    TurnStateError,
)
from joy_interaction_webui.turn_controller_shadow import (  # noqa: E402
    JARVIS_TO_TURN_EXPECTED,
    ShadowTurnController,
)


class FakeClock:
    """Deterministic monotonic clock in seconds; advance with ``advance(ms)``."""

    def __init__(self, start_s: float = 1000.0) -> None:
        self.t = start_s

    def __call__(self) -> float:
        return self.t

    def advance(self, ms: float) -> None:
        self.t += ms / 1000.0


def _make_shadow(enabled: bool = True) -> ShadowTurnController:
    return ShadowTurnController(
        TurnController(TurnConfig.jarvis(), clock=FakeClock()),
        shadow_enabled=enabled,
    )


# ---------------------------------------------------------------------------
# 1. State mapping: jarvis chain -> turn_controller chain
# ---------------------------------------------------------------------------


def test_mapping_table_covers_all_jarvis_states():
    from joy_interaction_webui.jarvis_mode import JarvisState

    for state in JarvisState:
        assert state.name in JARVIS_TO_TURN_EXPECTED, (
            f"missing mapping for jarvis state {state.name}"
        )
    assert "LISTENING" in JARVIS_TO_TURN_EXPECTED["KWS_LISTENING"]
    assert "WARM_UP" in JARVIS_TO_TURN_EXPECTED["WAKE_DETECTED"]
    assert "WARM_UP" in JARVIS_TO_TURN_EXPECTED["WAIT_ASR_CONFIRM"]
    assert JARVIS_TO_TURN_EXPECTED["DIALOG_ACTIVE"] == frozenset(
        {"USER_SPEAKING", "PROCESSING", "THINKING", "SPEAKING"}
    )
    assert "HARD_INTERRUPTED" in JARVIS_TO_TURN_EXPECTED["TTS_PAUSED"]
    assert "ENDED" in JARVIS_TO_TURN_EXPECTED["EXIT_DETECTED"]
    assert "ERROR" in JARVIS_TO_TURN_EXPECTED["ERROR"]


def test_jarvis_to_turn_mapping_chain_all_match():
    shadow = _make_shadow()
    transitions = [
        ("<init>", "KWS_LISTENING"),
        ("KWS_LISTENING", "WAKE_DETECTED"),
        ("WAKE_DETECTED", "DIALOG_ACTIVE"),
        ("DIALOG_ACTIVE", "TTS_PAUSED"),
        ("TTS_PAUSED", "DIALOG_ACTIVE"),
        ("DIALOG_ACTIVE", "EXIT_DETECTED"),
    ]
    for old, new in transitions:
        shadow.on_jarvis_transition(old, new, reason="test")

    assert shadow.jarvis_chain == [
        "KWS_LISTENING",
        "WAKE_DETECTED",
        "DIALOG_ACTIVE",
        "TTS_PAUSED",
        "DIALOG_ACTIVE",
        "EXIT_DETECTED",
    ]
    # Key nodes of the shadow chain (spec §2 Phase A mapping):
    # LISTENING -> WARM_UP -> [dialog] -> HARD_INTERRUPTED -> [dialog] -> ENDED.
    assert shadow.shadow_chain == [
        "LISTENING",
        "WARM_UP",
        "SPEAKING",
        "HARD_INTERRUPTED",
        "SPEAKING",
        "ENDED",
    ]
    stats = shadow.compare()
    assert stats["match"] == 6
    assert stats["mismatch"] == 0
    assert stats["align_rate"] == 1.0


def test_wait_asr_confirm_maps_to_warm_up_and_back_to_listening():
    shadow = _make_shadow()
    shadow.on_jarvis_transition("<init>", "KWS_LISTENING", reason="test")
    shadow.on_jarvis_transition("KWS_LISTENING", "WAIT_ASR_CONFIRM", reason="wake")
    assert shadow.shadow_chain[-1] == "WARM_UP"
    assert shadow.compare()["mismatch"] == 0
    shadow.on_jarvis_transition("WAIT_ASR_CONFIRM", "KWS_LISTENING", reason="false-alarm")
    assert shadow.shadow_chain[-1] == "LISTENING"
    assert shadow.compare()["align_rate"] == 1.0


def test_error_and_reset_cycle_matches():
    shadow = _make_shadow()
    shadow.on_jarvis_transition("<init>", "KWS_LISTENING", reason="test")
    shadow.on_jarvis_transition("KWS_LISTENING", "ERROR", reason="boom")
    assert shadow.shadow_chain[-1] == "ERROR"
    shadow.on_jarvis_transition("ERROR", "KWS_LISTENING", reason="recover")
    assert shadow.shadow_chain[-1] == "LISTENING"
    assert shadow.compare()["align_rate"] == 1.0


# ---------------------------------------------------------------------------
# 2. Alignment rate statistics
# ---------------------------------------------------------------------------


def test_alignment_rate_reflects_genuine_mismatch():
    shadow = _make_shadow()
    shadow.on_jarvis_transition("<init>", "KWS_LISTENING", reason="test")  # match
    shadow.on_jarvis_transition("KWS_LISTENING", "EXIT_DETECTED", reason="test")  # match
    # TTS_PAUSED straight after exit: the shadow can no longer reach the
    # interrupt zone (the controller restarts a fresh turn) -> honest mismatch.
    shadow.on_jarvis_transition("EXIT_DETECTED", "TTS_PAUSED", reason="test")
    stats = shadow.compare()
    assert stats["total"] == 3
    assert stats["match"] == 2
    assert stats["mismatch"] == 1
    assert stats["align_rate"] == pytest.approx(2 / 3, abs=1e-3)


def test_unknown_jarvis_state_is_mismatch_not_crash():
    shadow = _make_shadow()
    shadow.on_jarvis_transition("<init>", "KWS_LISTENING", reason="test")
    shadow.on_jarvis_transition("KWS_LISTENING", "MYSTERY_STATE", reason="test")
    stats = shadow.compare()
    assert stats["total"] == 2
    assert stats["match"] == 1
    assert stats["mismatch"] == 1
    assert stats["align_rate"] == 0.5


def test_summary_logs_and_returns_same_stats(caplog):
    import logging

    shadow = _make_shadow()
    shadow.on_jarvis_transition("<init>", "KWS_LISTENING", reason="test")
    with caplog.at_level(logging.INFO, logger="joyai.turn_shadow"):
        stats = shadow.summary()
    assert stats["total"] == 1
    assert stats["align_rate"] == 1.0
    assert any("[turn-shadow] summary:" in rec.getMessage() for rec in caplog.records)


# ---------------------------------------------------------------------------
# 3. Fail-open: shadow exceptions never propagate
# ---------------------------------------------------------------------------


def test_fail_open_event_errors_are_swallowed(monkeypatch):
    shadow = _make_shadow()

    def _boom(*args, **kwargs):
        raise RuntimeError("shadow boom")

    monkeypatch.setattr(shadow._turn, "on_speech_started", _boom)
    shadow.on_audio_activity(True, 0.9)  # must not raise

    def _boom_tts(*args, **kwargs):
        raise TurnStateError("illegal tts finish")

    monkeypatch.setattr(shadow._turn, "on_tts_finished", _boom_tts)
    shadow.on_tts_event(False)  # must not raise

    monkeypatch.setattr(shadow._turn, "on_partial_transcript", _boom)
    shadow.on_asr_partial("hello", True)  # must not raise

    monkeypatch.setattr(shadow._turn, "on_llm_token", _boom)
    shadow.on_llm_token("hi")  # must not raise

    # The shadow is still usable and records observations.
    assert shadow.enabled is True


def test_fail_open_drive_errors_are_swallowed(monkeypatch):
    shadow = _make_shadow()

    def _boom(target: str):
        raise RuntimeError("drive boom")

    monkeypatch.setattr(shadow, "_drive_to_jarvis_state", _boom)
    shadow.on_jarvis_transition("KWS_LISTENING", "WAKE_DETECTED", reason="test")  # swallowed

    # A fresh shadow is fully functional afterwards.
    fresh = _make_shadow()
    fresh.on_jarvis_transition("KWS_LISTENING", "WAKE_DETECTED", reason="test")
    stats = fresh.compare()
    assert stats["total"] == 2  # bootstrap KWS_LISTENING + WAKE_DETECTED
    assert stats["mismatch"] == 0


# ---------------------------------------------------------------------------
# 4. Shadow disabled = pure no-op
# ---------------------------------------------------------------------------


def test_shadow_disabled_is_noop():
    ctrl = TurnController(TurnConfig.jarvis(), clock=FakeClock())
    shadow = ShadowTurnController(ctrl, shadow_enabled=False)
    shadow.on_jarvis_transition("KWS_LISTENING", "WAKE_DETECTED", reason="test")
    shadow.on_audio_activity(True, 0.9)
    shadow.on_asr_partial("hello", True)
    shadow.on_llm_token("hi")
    shadow.on_tts_event(True)
    assert ctrl.state == TurnState.IDLE  # controller untouched
    assert shadow.compare()["total"] == 0
    assert shadow.enabled is False


def test_shadow_disabled_accepts_none_controller():
    shadow = ShadowTurnController(None, shadow_enabled=False)
    shadow.on_jarvis_transition("KWS_LISTENING", "WAKE_DETECTED", reason="test")
    shadow.on_audio_activity(True, 0.9)
    shadow.on_tts_event(True)
    assert shadow.compare()["total"] == 0


# ---------------------------------------------------------------------------
# 5. jarvis_mode mount point: env gate + zero behavior change
# ---------------------------------------------------------------------------


def test_mount_point_disabled_by_default(monkeypatch):
    monkeypatch.delenv("JARVIS_TURN_SHADOW_ENABLED", raising=False)
    from joy_interaction_webui.jarvis_mode import JarvisConfig, JarvisState, JarvisStateMachine

    async def _scenario():
        sm = JarvisStateMachine(config=JarvisConfig())
        assert sm._turn_shadow is None
        await sm._transition_to(JarvisState.WAKE_DETECTED)
        assert sm.state == JarvisState.WAKE_DETECTED

    asyncio.run(_scenario())


def test_mount_point_enabled_by_env(monkeypatch):
    monkeypatch.setenv("JARVIS_TURN_SHADOW_ENABLED", "1")
    from joy_interaction_webui.jarvis_mode import JarvisConfig, JarvisState, JarvisStateMachine
    from joy_interaction_webui.turn_controller_shadow import ShadowTurnController

    async def _scenario():
        sm = JarvisStateMachine(config=JarvisConfig())
        assert isinstance(sm._turn_shadow, ShadowTurnController)
        await sm._transition_to(JarvisState.WAKE_DETECTED)
        assert sm.state == JarvisState.WAKE_DETECTED
        assert sm._turn_shadow.compare()["total"] >= 1

    asyncio.run(_scenario())


def test_transition_to_identical_with_and_without_shadow():
    from joy_interaction_webui.jarvis_mode import JarvisConfig, JarvisState, JarvisStateMachine

    async def _scenario():
        sm_plain = JarvisStateMachine(config=JarvisConfig())
        sm_shadow = JarvisStateMachine(config=JarvisConfig())
        sm_shadow._turn_shadow = ShadowTurnController(
            TurnController(TurnConfig.jarvis(), clock=FakeClock()), shadow_enabled=True
        )
        for target in (
            JarvisState.WAKE_DETECTED,
            JarvisState.DIALOG_ACTIVE,
            JarvisState.TTS_PAUSED,
            JarvisState.EXIT_DETECTED,
        ):
            await sm_plain._transition_to(target)
            await sm_shadow._transition_to(target)
            assert sm_plain.state == sm_shadow.state == target
        assert sm_shadow._turn_shadow.compare()["total"] == 5  # bootstrap + 4 transitions

    asyncio.run(_scenario())
