"""Regression tests for the ASR-promotion runtime toggle and model display name.

These are model-free: they exercise ``JarvisSessionManager.set_asr_promotion_enabled``,
``jarvis_mode.asr_model_display_name`` and ``JarvisStateMachine._asr_confirm_match``
without loading sherpa-onnx / KWS models.

Run: pytest services/webui/tests/test_asr_promotion_toggle.py -q
"""

from __future__ import annotations

import types

from joy_interaction_webui.jarvis_mode import (
    JarvisConfig,
    JarvisStateMachine,
    asr_model_display_name,
)
from joy_interaction_webui.jarvis_session import JarvisSessionManager


def _confirm_match(text: str, *, patterns: tuple[str, ...] | None = None) -> bool:
    """Call the wide ASR confirm matcher with an optional pattern override."""
    cfg = JarvisConfig()
    if patterns is not None:
        cfg.asr_confirm_patterns = patterns
    sm = types.SimpleNamespace(config=cfg)
    return JarvisStateMachine._asr_confirm_match(sm, text)  # type: ignore[arg-type]


def _make_manager() -> JarvisSessionManager:
    """Build a manager with a plain config (no sessions, no model loads)."""
    return JarvisSessionManager(config=JarvisConfig())


def test_asr_model_display_name_default_dir():
    cfg = JarvisConfig()
    name = asr_model_display_name(cfg)
    assert name == "sherpa-onnx streaming-paraformer-bilingual-zh-en (local)"
    assert name.endswith("(local)")


def test_asr_model_display_name_derives_from_model_dir():
    cfg = JarvisConfig()
    cfg.asr_model_dir = "/models/sherpa-onnx/asr/streaming-paraformer-bilingual-zh-en"
    assert asr_model_display_name(cfg).startswith("sherpa-onnx streaming-paraformer")


def test_asr_model_display_name_non_paraformer_falls_back():
    cfg = JarvisConfig()
    cfg.asr_model_dir = "/models/some-other-asr"
    assert asr_model_display_name(cfg) == "sherpa-onnx local paraformer (some-other-asr)"


def test_set_asr_promotion_propagates_to_shared_config():
    mgr = _make_manager()
    assert mgr.get_asr_promotion_enabled() is False
    mgr.set_asr_promotion_enabled(True)
    assert mgr.get_asr_promotion_enabled() is True
    # manager.config is shared by reference with every session's state machine.
    assert mgr.config.asr_promotion_enabled is True


def test_set_asr_promotion_propagates_to_active_sessions():
    mgr = _make_manager()
    # Normal path: a live session holds the SAME config object as the manager.
    shared_cfg = mgr.config
    shared_session = types.SimpleNamespace(
        session_id="s1",
        state_machine=types.SimpleNamespace(config=shared_cfg),
    )
    mgr._sessions["s1"] = shared_session

    # Defensive path: a session created with a DETACHED config object.
    detached_cfg = JarvisConfig()
    detached_cfg.asr_promotion_enabled = False
    detached_session = types.SimpleNamespace(
        session_id="s2",
        state_machine=types.SimpleNamespace(config=detached_cfg),
    )
    mgr._sessions["s2"] = detached_session

    mgr.set_asr_promotion_enabled(True)
    assert shared_session.state_machine.config.asr_promotion_enabled is True
    assert detached_session.state_machine.config.asr_promotion_enabled is True

    mgr.set_asr_promotion_enabled(False)
    assert shared_session.state_machine.config.asr_promotion_enabled is False
    assert detached_session.state_machine.config.asr_promotion_enabled is False


# ---------------------------------------------------------------------------
# Wide ASR confirm matcher (local paraformer promotion)
# ---------------------------------------------------------------------------


def test_asr_confirm_match_joined_bt():
    assert _confirm_match("bt") is True
    assert _confirm_match("BT") is True
    assert _confirm_match("hey bt") is True
    assert _confirm_match("bt 在吗") is True
    assert _confirm_match("BT-7274") is True


def test_asr_confirm_match_segmented_b_t():
    assert _confirm_match("b t") is True
    assert _confirm_match("B T") is True
    assert _confirm_match("b  t") is True  # multiple spaces
    assert _confirm_match("b.t") is True  # punctuation
    assert _confirm_match("b、t") is True  # CJK punctuation
    assert _confirm_match("在吗 b t") is True
    assert _confirm_match("a b t c") is True  # adjacent b-t anywhere


def test_asr_confirm_match_rejects_wrong_order_and_similar():
    assert _confirm_match("tb") is False
    assert _confirm_match("t b") is False
    assert _confirm_match("about") is False
    assert _confirm_match("bit") is False
    assert _confirm_match("bet") is False
    assert _confirm_match("ab tc") is False
    assert _confirm_match("") is False


def test_asr_confirm_patterns_override():
    """Explicit patterns keep backward compatibility and can override wide match."""
    assert _confirm_match("custom wake", patterns=("custom",)) is True
    assert _confirm_match("bt", patterns=("custom",)) is False
