"""Contract tests: proactive-round decisions reach the ADR-0014 event stream.

Spec: agentteams issue #146. Proactive rounds never entered ``qa_history``
(that path requires non-empty user text) and emitted no event, so an
agent-initiated "should I speak?" decision left NO trace anywhere. The
``live-visual-cb.md`` §1 acceptance ("真机验收主动搭话质量") is therefore
unevaluable offline.

Pinned contracts:

  1. A proactive round records exactly one ``live_decision`` event with
     ``round_kind="proactive"`` — for ALL outcomes, not just the speaking one.
  2. ★ Both quiet paths are recorded: decision != response (silence /
     not-for-me), and the race-guard skip (controller no longer LISTENING).
     The record must precede both early returns.
  3. ``frames_n`` reflects how many frames the VLM round actually saw.
  4. The recorded decision is the raw VLM value (no rewriting).

Run: python -m pytest tests/test_proactive_decision_event.py -q
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[3]
WEBUI_SRC = REPO / "services" / "webui" / "src"
TESTS_DIR = Path(__file__).resolve().parent
for _p in (str(REPO), str(WEBUI_SRC), str(TESTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402
from test_live_proactive import B64, build_live  # noqa: E402

from joy_interaction_webui import live_proactive as proactive_module  # noqa: E402


def _capture() -> tuple[list[dict], object]:
    """Return (captured_events, patcher) for _record_decision."""
    captured: list[dict] = []

    def fake_record(**kwargs):
        captured.append(kwargs)

    return captured, patch.object(proactive_module, "_record_decision", fake_record)


def _log_text(record) -> str:
    """Render a LogRecord to text without raising on arg-count mismatches."""
    try:
        return record.getMessage()
    except Exception:
        return str(record.msg)


def _guard_logged(caplog) -> bool:
    """True when the proactive race guard announced it skipped speaking."""
    return any("no longer LISTENING" in _log_text(r) for r in caplog.records)


# ---------------------------------------------------------------------------
# 1 + 4. speaking round is recorded with the raw decision
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_proactive_response_records_decision(monkeypatch):
    sm, _vad, _asr = build_live()
    sm.handle_frame(B64, 1000.0)

    async def fake_vlm(frames):
        return "response", "画面里出现了 Boss！"

    async def fake_tts(text):
        return b"\x00\x00" * 10

    monkeypatch.setattr(sm, "_call_proactive_vlm", fake_vlm)
    monkeypatch.setattr(sm, "_fetch_tts_pcm", fake_tts)

    captured, patcher = _capture()
    with patcher:
        await sm._send_proactive_prompt(frames=sm._frames_payload([sm.recent_frames[-1]]))

    assert len(captured) == 1, f"expected exactly one record, got {captured}"
    rec = captured[0]
    assert rec["decision"] == "response"
    assert rec["round_kind"] == "proactive"
    assert rec["frames_n"] == 1


# ---------------------------------------------------------------------------
# 2a. ★ the quiet path (silence) must ALSO be recorded
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_proactive_silence_is_recorded(monkeypatch):
    """Silence is the high-frequency normal path — it must still be recorded.

    An evaluator cannot measure false-positive speaking without knowing how
    often the model chose to stay quiet.
    """
    sm, _vad, _asr = build_live()
    sm.handle_frame(B64, 1000.0)

    async def fake_vlm(frames):
        return "silence", ""

    monkeypatch.setattr(sm, "_call_proactive_vlm", fake_vlm)

    captured, patcher = _capture()
    with patcher:
        await sm._send_proactive_prompt(frames=sm._frames_payload([sm.recent_frames[-1]]))

    assert len(captured) == 1
    assert captured[0]["decision"] == "silence"
    assert captured[0]["round_kind"] == "proactive"


# ---------------------------------------------------------------------------
# 2b. ★ not-for-me must be recorded with its own label (not collapsed)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_proactive_not_for_me_recorded_separately(monkeypatch):
    sm, _vad, _asr = build_live()
    sm.handle_frame(B64, 1000.0)

    async def fake_vlm(frames):
        return "not-for-me", ""

    monkeypatch.setattr(sm, "_call_proactive_vlm", fake_vlm)

    captured, patcher = _capture()
    with patcher:
        await sm._send_proactive_prompt(frames=sm._frames_payload([sm.recent_frames[-1]]))

    assert len(captured) == 1
    assert captured[0]["decision"] == "not-for-me", (
        "not-for-me must not be collapsed into silence — the addressee axis "
        "is the whole point of the fourth state"
    )


# ---------------------------------------------------------------------------
# 2c. ★ the race-guard skip (controller left LISTENING) is a decision too
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_proactive_race_guard_skip_is_still_recorded(monkeypatch, caplog):
    """When the user starts speaking mid-flight we skip speaking — but the VLM
    already made a decision, and losing it would hide real false positives.

    The race guard fires when the controller is no longer LISTENING at decision
    time. We prove it fired by asserting the guard's own log line is emitted —
    the only unambiguous signal, since "nothing was spoken" is also what a TTS
    failure looks like (the companion control test pins that distinction).
    """
    sm, _vad, _asr = build_live()
    sm.handle_frame(B64, 1000.0)

    async def fake_vlm(frames):
        # Model DID want to speak; the user starts talking mid-flight.
        from joy_interaction_webui.turn_controller import TurnState as _TS

        sm._ctrl.state = _TS.THINKING
        return "response", "我看到了一只猫"

    monkeypatch.setattr(sm, "_call_proactive_vlm", fake_vlm)

    captured, patcher = _capture()
    with caplog.at_level(logging.INFO), patcher:
        await sm._send_proactive_prompt(frames=sm._frames_payload([sm.recent_frames[-1]]))

    # Proof the race guard fired (not merely "nothing was spoken").
    assert _guard_logged(caplog), "race guard did not fire — test did not exercise the path"

    # ...and the decision is still recorded (it was a real model decision).
    assert len(captured) == 1
    assert captured[0]["decision"] == "response", (
        "the race-guard skip dropped the decision — real false positives "
        "would become invisible to the evaluator"
    )


@pytest.mark.asyncio
async def test_control_speaking_path_does_not_log_race_guard(monkeypatch, caplog):
    """Control: the same setup WITHOUT the race must NOT log the guard.

    This pins the discriminating power of the race-guard test above. Note that
    we deliberately assert on the ABSENCE of the guard log rather than on a
    successful sentence push: in this harness the TTS stub fails with 502, so
    "no push" is exactly what a TTS failure looks like. That is precisely why
    the guard test asserts on the log line instead of on silence.
    """
    sm, _vad, _asr = build_live()
    sm.handle_frame(B64, 1000.0)

    async def fake_vlm(frames):
        return "response", "我看到了一只猫"  # stays LISTENING

    monkeypatch.setattr(sm, "_call_proactive_vlm", fake_vlm)

    captured, patcher = _capture()
    with caplog.at_level(logging.INFO), patcher:
        await sm._send_proactive_prompt(frames=sm._frames_payload([sm.recent_frames[-1]]))

    assert not _guard_logged(caplog), (
        "control failed: the guard logged without a race, so the race-guard "
        "test's log assertion would be meaningless"
    )
    # The speaking branch WAS entered (agent turn started) and the decision
    # was recorded normally.
    assert len(captured) == 1
    assert captured[0]["decision"] == "response"


# ---------------------------------------------------------------------------
# 3. frames_n reflects the actual frame count
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_frames_n_matches_actual_frames(monkeypatch):
    sm, _vad, _asr = build_live()
    sm.handle_frame(B64, 1000.0)
    sm.handle_frame(B64, 2000.0)

    async def fake_vlm(frames):
        return "silence", ""

    monkeypatch.setattr(sm, "_call_proactive_vlm", fake_vlm)

    captured, patcher = _capture()
    with patcher:
        await sm._send_proactive_prompt(frames=sm._frames_payload(list(sm.recent_frames)))

    assert len(captured) == 1
    assert captured[0]["frames_n"] == 2
