"""QA regression: ``compose_live_visual_messages`` equivalence (commit da93caf).

Independent verification that the extracted ``prompt_assembly`` helper
produces output byte-identical to the previous inline assembly
(``history_messages = list(caller_messages); drop trailing user;
_build_live_visual_messages(...)``) for both call-site shapes.

Run: python -m pytest tests/test_qa_regression_compose_visual.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from prompt_assembly import _build_live_visual_messages, compose_live_visual_messages  # noqa: E402

SYSTEM = "You are LIVE. Four-state."
TEXT = "what do you see?"


def _frame(b64: str = "AAAA") -> dict:
    return {"image_b64": b64, "ts_ms": 1.0}


def _expected(caller_messages, frames):
    """Re-implementation of the pre-change inline block (byte-identical)."""
    history_messages = list(caller_messages)
    if history_messages and history_messages[-1].get("role") == "user":
        history_messages = history_messages[:-1]
    return _build_live_visual_messages(
        SYSTEM,
        TEXT,
        frames,
        history_messages=history_messages,
    )


def test_trailing_user_dropped_from_history():
    caller = [
        {"role": "user", "content": "earlier"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": TEXT},  # current utterance rides on visual msg
    ]
    frames = [_frame("AAAA"), _frame("BBBB")]
    got = compose_live_visual_messages(
        composed_system=SYSTEM,
        last_user_text=TEXT,
        frames=frames,
        caller_messages=caller,
    )
    assert got == _expected(caller, frames)


def test_no_trailing_user_history_kept():
    caller = [
        {"role": "user", "content": "earlier"},
        {"role": "assistant", "content": "ok"},
    ]
    frames = [_frame("CCCC")]
    got = compose_live_visual_messages(
        composed_system=SYSTEM,
        last_user_text=TEXT,
        frames=frames,
        caller_messages=caller,
    )
    assert got == _expected(caller, frames)


def test_empty_caller_messages():
    frames = [_frame("DDDD")]
    got = compose_live_visual_messages(
        composed_system=SYSTEM,
        last_user_text=TEXT,
        frames=frames,
        caller_messages=[],
    )
    assert got == _expected([], frames)
    assert len(got) == 2  # system + visual user


def test_structure_system_visual_context_history_visual_user():
    caller = [
        {"role": "user", "content": "earlier"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": TEXT},
    ]
    frames = [_frame("EEEE")]
    got = compose_live_visual_messages(
        composed_system=SYSTEM,
        last_user_text=TEXT,
        frames=frames,
        caller_messages=caller,
    )
    assert got[0]["role"] == "system"
    assert "[Visual Context]" in got[0]["content"]
    # history turns: trailing user dropped -> only the first two messages
    assert [m["role"] for m in got[1:-1]] == ["user", "assistant"]
    # final visual user message carries text + image_url data URI
    last = got[-1]
    assert last["role"] == "user"
    parts = last["content"]
    assert {"type": "text", "text": TEXT} in parts
    image_urls = [
        p["image_url"]["url"] for p in parts if isinstance(p, dict) and p.get("type") == "image_url"
    ]
    assert image_urls == [f"data:image/jpeg;base64,EEEE"]


def test_empty_frames_still_builds_text_only_user():
    """Even though the router guarantees non-empty frames, the helper must
    not crash on empty frames (matches direct _build_live_visual_messages)."""
    caller = [{"role": "user", "content": "earlier"}, {"role": "user", "content": TEXT}]
    got = compose_live_visual_messages(
        composed_system=SYSTEM,
        last_user_text=TEXT,
        frames=[],
        caller_messages=caller,
    )
    assert got == _expected(caller, [])
