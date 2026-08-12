"""QA boundary tests for the Turn Controller shadow adapter (Phase A).

Independent regression verification authored by QA (严过关). Complements
``test_turn_controller_shadow.py`` with adversarial boundary cases:

1. Mapping table edges: unknown jarvis states are honest mismatches (no
   crash); every legal DIALOG_ACTIVE sub-state is reachable via public
   controller events and still aligns.
2. Fail-open depth: a *class-level* failure inside TurnController (not just
   a patched shadow method) is swallowed — the caller is never affected.
3. Env gate: JARVIS_TURN_SHADOW_ENABLED value parsing (default / explicit
   false / explicit true) and full no-op behaviour when disabled.
4. Mount-point safety: ``_transition_to`` must not AttributeError on
   instances built via ``__new__`` without ``_turn_shadow`` (the pattern used
   by existing tests such as ``_make_sm``), and must swallow a broken shadow.
5. Alignment arithmetic: precise align_rate values for real event sequences.

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


def _make_ctrl() -> TurnController:
    return TurnController(TurnConfig.jarvis(), clock=FakeClock())


def _make_shadow(enabled: bool = True) -> ShadowTurnController:
    return ShadowTurnController(_make_ctrl(), shadow_enabled=enabled)


# ---------------------------------------------------------------------------
# 1. Mapping table edges
# ---------------------------------------------------------------------------


def test_unknown_jarvis_state_is_mismatch_not_crash():
    """A jarvis state missing from JARVIS_TO_TURN_EXPECTED must be an honest
    mismatch (align_rate drops), never an exception."""
    shadow = _make_shadow()
    shadow.on_jarvis_transition("<init>", "KWS_LISTENING", reason="test")  # match
    shadow.on_jarvis_transition("KWS_LISTENING", "UNKNOWN", reason="test")  # not in table
    stats = shadow.compare()
    assert stats["total"] == 2
    assert stats["match"] == 1
    assert stats["mismatch"] == 1
    assert stats["align_rate"] == 0.5
    # The shadow stays fully usable afterwards.
    shadow.on_jarvis_transition("UNKNOWN", "KWS_LISTENING", reason="recover")
    assert shadow.compare()["match"] == 2


def test_dialog_active_substates_are_all_in_expected_set():
    """Every legal DIALOG_ACTIVE sub-state from the spec is accepted."""
    expected = JARVIS_TO_TURN_EXPECTED["DIALOG_ACTIVE"]
    for sub in ("USER_SPEAKING", "PROCESSING", "THINKING", "SPEAKING"):
        assert sub in expected


def _drive_ctrl_to(ctrl: TurnController, target: TurnState) -> None:
    """Drive a fresh controller to ``target`` using only public events."""
    assert ctrl.state == TurnState.IDLE
    if target == TurnState.IDLE:
        return
    ctrl.on_wake_word_detected()  # IDLE -> WARM_UP
    if target == TurnState.WARM_UP:
        return
    ctrl.on_wake_ready()  # WARM_UP -> LISTENING
    if target == TurnState.LISTENING:
        return
    ctrl.on_speech_started(0.8)  # LISTENING -> USER_SPEAKING
    if target == TurnState.USER_SPEAKING:
        return
    ctrl.on_partial_transcript("utterance", is_final=True)  # -> PROCESSING
    if target == TurnState.PROCESSING:
        return
    ctrl.on_llm_token("reply")  # PROCESSING -> THINKING
    if target == TurnState.THINKING:
        return
    ctrl.on_tts_started()  # THINKING -> SPEAKING
    assert target == TurnState.SPEAKING


@pytest.mark.parametrize(
    "sub",
    [
        TurnState.USER_SPEAKING,
        TurnState.PROCESSING,
        TurnState.THINKING,
        TurnState.SPEAKING,
    ],
)
def test_dialog_active_alignment_from_each_substate(sub):
    """When jarvis enters DIALOG_ACTIVE the shadow must drive the controller
    into the dialog zone (SPEAKING) regardless of the sub-state the controller
    was already in — every sub-state trigger path is reachable and aligns."""
    ctrl = _make_ctrl()
    _drive_ctrl_to(ctrl, sub)
    shadow = ShadowTurnController(ctrl, shadow_enabled=True)
    shadow.on_jarvis_transition("<init>", "DIALOG_ACTIVE", reason="test")
    stats = shadow.compare()
    assert stats["total"] == 1
    assert stats["mismatch"] == 0, f"sub-state {sub.name} did not align"
    assert shadow.shadow_chain[-1] == "SPEAKING"


def test_dialog_active_full_turn_from_listening():
    """Full user-turn: KWS -> DIALOG_ACTIVE lands in SPEAKING via the
    LISTENING -> USER_SPEAKING -> PROCESSING -> THINKING -> SPEAKING path."""
    shadow = _make_shadow()
    shadow.on_jarvis_transition("<init>", "KWS_LISTENING", reason="test")
    shadow.on_jarvis_transition("KWS_LISTENING", "DIALOG_ACTIVE", reason="user-turn")
    assert shadow.shadow_chain[-1] == "SPEAKING"
    stats = shadow.compare()
    assert stats["total"] == 2
    assert stats["mismatch"] == 0
    assert stats["align_rate"] == 1.0


# ---------------------------------------------------------------------------
# 2. Fail-open depth: class-level TurnController failure
# ---------------------------------------------------------------------------


def test_fail_open_when_controller_event_raises(monkeypatch):
    """A RuntimeError raised *inside* TurnController.on_speech_started must
    be swallowed by the shadow — the caller never sees it and the shadow
    remains usable."""
    shadow = _make_shadow()

    def _boom(self, conf: float) -> None:
        raise RuntimeError("deep controller failure")

    monkeypatch.setattr(TurnController, "on_speech_started", _boom)

    # Bootstrap to LISTENING (no speech events involved).
    shadow.on_jarvis_transition("<init>", "KWS_LISTENING", reason="test")
    assert shadow.compare()["total"] == 1

    # DIALOG_ACTIVE drives on_speech_started -> RuntimeError -> swallowed.
    shadow.on_jarvis_transition("KWS_LISTENING", "DIALOG_ACTIVE", reason="test")
    assert shadow.compare()["total"] == 1  # observation skipped, no crash

    # Shadow still usable (no exception leaked to the caller).
    assert shadow.enabled is True
    fresh = _make_shadow()
    fresh.on_jarvis_transition("<init>", "KWS_LISTENING", reason="test")
    assert fresh.compare()["align_rate"] == 1.0


def test_fail_open_when_controller_illegal_event_raises():
    """TurnStateError (illegal transition) inside the controller is also
    swallowed by the shadow entry points."""
    shadow = _make_shadow()
    shadow.on_jarvis_transition("<init>", "KWS_LISTENING", reason="test")
    # Feed a TTS finish while the controller is still IDLE — illegal, but the
    # shadow's fail-open must keep the caller alive. (on_tts_event -> on_tts_finished)
    shadow.on_tts_event(False)  # must not raise
    assert shadow.enabled is True


# ---------------------------------------------------------------------------
# 3. Env gate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("env_value", "expect_enabled"),
    [
        (None, False),  # default: unset -> off
        ("", False),
        ("0", False),
        ("false", False),
        ("FALSE", False),
        ("no", False),
        ("1", True),
        ("true", True),
        ("TRUE", True),
        ("yes", True),
        ("on", True),
    ],
)
def test_env_gate_value_parsing(monkeypatch, env_value, expect_enabled):
    from joy_interaction_webui.jarvis_mode import JarvisConfig, JarvisStateMachine

    if env_value is None:
        monkeypatch.delenv("JARVIS_TURN_SHADOW_ENABLED", raising=False)
    else:
        monkeypatch.setenv("JARVIS_TURN_SHADOW_ENABLED", env_value)
    sm = JarvisStateMachine(config=JarvisConfig())
    assert (sm._turn_shadow is not None) is expect_enabled, (
        f"env={env_value!r} expect_enabled={expect_enabled}"
    )


def test_shadow_disabled_all_methods_noop_with_real_controller():
    """shadow_enabled=False: every public method is a no-op on a real
    controller (which must stay untouched)."""
    ctrl = _make_ctrl()
    shadow = ShadowTurnController(ctrl, shadow_enabled=False)
    shadow.on_jarvis_transition("KWS_LISTENING", "DIALOG_ACTIVE", reason="test")
    shadow.on_audio_activity(True, 0.9)
    shadow.on_audio_activity(False, 0.1)
    shadow.on_asr_partial("hello", True)
    shadow.on_llm_token("hi")
    shadow.on_tts_event(True)
    assert ctrl.state == TurnState.IDLE  # untouched
    assert shadow.jarvis_chain == []
    assert shadow.shadow_chain == []
    assert shadow.compare() == {"total": 0, "match": 0, "mismatch": 0, "align_rate": 1.0}
    assert shadow.summary()["total"] == 0


# ---------------------------------------------------------------------------
# 4. Mount point safety (_transition_to)
# ---------------------------------------------------------------------------


def test_transition_to_without_turn_shadow_attribute():
    """Instances built via __new__ (existing tests' _make_sm pattern) have no
    ``_turn_shadow`` attribute — the mount must not AttributeError."""
    from joy_interaction_webui.jarvis_mode import JarvisState, JarvisStateMachine

    sm = JarvisStateMachine.__new__(JarvisStateMachine)
    sm.state = JarvisState.KWS_LISTENING

    async def _scenario():
        await sm._transition_to(JarvisState.WAKE_DETECTED)

    asyncio.run(_scenario())
    assert sm.state == JarvisState.WAKE_DETECTED


def test_transition_to_with_explicit_none_shadow():
    """Explicit ``_turn_shadow = None`` is the default-off runtime state and
    must be a no-op."""
    from joy_interaction_webui.jarvis_mode import JarvisState, JarvisStateMachine

    sm = JarvisStateMachine.__new__(JarvisStateMachine)
    sm.state = JarvisState.KWS_LISTENING
    sm._turn_shadow = None

    async def _scenario():
        await sm._transition_to(JarvisState.DIALOG_ACTIVE)

    asyncio.run(_scenario())
    assert sm.state == JarvisState.DIALOG_ACTIVE


def test_transition_to_swallows_broken_shadow(caplog):
    """A shadow whose on_jarvis_transition raises must be swallowed by the
    mount — jarvis transition still completes."""
    import logging

    from joy_interaction_webui.jarvis_mode import JarvisConfig, JarvisState, JarvisStateMachine
    from joy_interaction_webui.turn_controller_shadow import ShadowTurnController

    class _BrokenShadow(ShadowTurnController):
        def on_jarvis_transition(self, old_state, new_state, reason):
            raise RuntimeError("broken shadow")

    sm = JarvisStateMachine(config=JarvisConfig())
    sm._turn_shadow = _BrokenShadow(_make_ctrl(), shadow_enabled=True)

    async def _scenario():
        await sm._transition_to(JarvisState.WAKE_DETECTED)

    with caplog.at_level(logging.WARNING, logger="joyai.jarvis"):
        asyncio.run(_scenario())
    assert sm.state == JarvisState.WAKE_DETECTED  # transition not affected
    assert any("shadow observation failed" in rec.getMessage() for rec in caplog.records)


# ---------------------------------------------------------------------------
# 5. Alignment arithmetic (precise values)
# ---------------------------------------------------------------------------


def test_align_rate_precise_real_three_event_sequence():
    """A real 3-event sequence with one genuine mismatch gives a precise
    align_rate of 2/3 (rounded to 4 decimals)."""
    shadow = _make_shadow()
    shadow.on_jarvis_transition("<init>", "KWS_LISTENING", reason="test")  # match
    shadow.on_jarvis_transition("KWS_LISTENING", "EXIT_DETECTED", reason="bye")  # match
    # TTS_PAUSED straight after EXIT: the controller restarts a fresh turn and
    # cannot reach the interrupt zone -> honest mismatch.
    shadow.on_jarvis_transition("EXIT_DETECTED", "TTS_PAUSED", reason="barge")
    stats = shadow.compare()
    assert stats["total"] == 3
    assert stats["match"] == 2
    assert stats["mismatch"] == 1
    assert stats["align_rate"] == pytest.approx(2 / 3, abs=1e-4)
    assert stats["align_rate"] == 0.6667  # round(2/3, 4)


def test_align_rate_all_match_real_sequence():
    """A real full-cycle sequence with no mismatches gives align_rate == 1.0."""
    shadow = _make_shadow()
    for old, new in (
        ("<init>", "KWS_LISTENING"),
        ("KWS_LISTENING", "WAKE_DETECTED"),
        ("WAKE_DETECTED", "DIALOG_ACTIVE"),
        ("DIALOG_ACTIVE", "TTS_PAUSED"),
        ("TTS_PAUSED", "DIALOG_ACTIVE"),
        ("DIALOG_ACTIVE", "EXIT_DETECTED"),
    ):
        shadow.on_jarvis_transition(old, new, reason="test")
    stats = shadow.compare()
    assert stats["total"] == 6
    assert stats["mismatch"] == 0
    assert stats["align_rate"] == 1.0
    assert len(shadow.jarvis_chain) == len(shadow.shadow_chain) == 6
