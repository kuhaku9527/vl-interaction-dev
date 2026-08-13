"""Regression tests for audit P1-2: multimodal path qa_history unbounded growth.

``qa_history_window`` trimming only existed on the text path
(``MemoryIOMixin._update_text_qa_history``); the multimodal path
(``response_format.archive_chunk_response_records``) only appended, so a long
video session grew the system-prompt QA history without bound — the exact
context-overflow the window was added to prevent (upstream PR #25 root
cause 1), unreachable by ``_trim_messages_to_ctx`` because QA history renders
inside the system message (always kept as ``messages[:1]``).

Fix: a shared ``_trim_qa_history_to_window`` helper used by BOTH paths;
``archive_chunk_response_records`` trims after append/extend when the caller
passes ``qa_history_window``.

Run: python -m pytest tests/test_p1_2_qa_history_window.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from adapter_types import AdapterConfig, SessionState  # noqa: E402
from memory_io import MemoryIOMixin  # noqa: E402
from response_format import (  # noqa: E402
    _trim_qa_history_to_window,
    archive_chunk_response_records,
)


class _MemIO(MemoryIOMixin):
    """Minimal stand-in: only ``self.config`` is needed (same as the
    text-path regression test ``test_context_overflow_bounds.py``)."""

    def __init__(self, **cfg):
        self.config = AdapterConfig(**cfg)


def _make_state() -> SessionState:
    state = SessionState(session_id="s", memory_state={"long_term_memory": "", "qa_history": []})
    state.current_chunk = {"response_records": []}
    return state


def _seed_qa(state: SessionState, n: int) -> None:
    for i in range(n):
        state.memory_state["qa_history"].append(
            {
                "query_time": f"t{i}",
                "query": f"q{i}",
                "responses": [["r", {"prediction": f"p{i}", "decision": "response"}]],
                "archived_in_chunk": i,
            }
        )


def _archive(state: SessionState, *, window: int, query: str = "new-q") -> None:
    state.current_chunk["response_records"] = [
        ("5.0 seconds", {"prediction": "answer", "decision": "response"})
    ]
    archive_chunk_response_records(
        state.current_chunk,
        state.memory_state,
        query,
        "1.0 seconds",
        chunk_index=999,
        qa_history_window=window,
    )


# ---------------------------------------------------------------------------
# _trim_qa_history_to_window — shared helper
# ---------------------------------------------------------------------------


def test_helper_trims_to_window():
    qa = [{"query": f"q{i}"} for i in range(5)]
    _trim_qa_history_to_window(qa, 2)
    assert [e["query"] for e in qa] == ["q3", "q4"]


def test_helper_window_zero_keeps_unbounded():
    qa = [{"query": f"q{i}"} for i in range(5)]
    _trim_qa_history_to_window(qa, 0)
    assert len(qa) == 5


def test_helper_larger_than_len_is_noop():
    qa = [{"query": f"q{i}"} for i in range(5)]
    _trim_qa_history_to_window(qa, 50)
    assert len(qa) == 5


# ---------------------------------------------------------------------------
# archive_chunk_response_records — multimodal path now trims
# ---------------------------------------------------------------------------


def test_video_path_qa_history_trimmed_by_window():
    """Long video session: archive_chunk_response_records must cap history."""
    state = _make_state()
    _seed_qa(state, 5)
    assert len(state.memory_state["qa_history"]) == 5

    _archive(state, window=3)

    qa = state.memory_state["qa_history"]
    assert len(qa) == 3, qa
    # oldest three (q0,q1,q2) dropped from the head; newest two retained.
    assert qa[0]["query"] == "q3"
    assert qa[-1]["query"] == "new-q"


def test_video_path_window_zero_keeps_unbounded():
    """window=0 preserves the historical unbounded multimodal behaviour."""
    state = _make_state()
    _seed_qa(state, 5)
    _archive(state, window=0)
    assert len(state.memory_state["qa_history"]) == 6


def test_video_path_window_larger_than_len_is_noop():
    state = _make_state()
    _seed_qa(state, 3)
    _archive(state, window=50)
    assert len(state.memory_state["qa_history"]) == 4


def test_video_path_trims_existing_entry_extend_too():
    """Extending an existing archived entry also counts toward the window."""
    state = _make_state()
    _seed_qa(state, 5)
    # Archive with the SAME query+chunk as q4 so the branch extends it.
    state.current_chunk["response_records"] = [
        ("9.0 seconds", {"prediction": "extra", "decision": "response"})
    ]
    archive_chunk_response_records(
        state.current_chunk,
        state.memory_state,
        "q4",
        "t4",
        chunk_index=4,
        qa_history_window=2,
    )
    qa = state.memory_state["qa_history"]
    assert len(qa) == 2
    assert qa[-1]["query"] == "q4"
    assert any(p["prediction"] == "extra" for _, p in qa[-1]["responses"])


# ---------------------------------------------------------------------------
# text path behaviour unchanged (still uses the shared helper)
# ---------------------------------------------------------------------------


def test_text_path_still_trims_via_shared_helper():
    mem = _MemIO(qa_history_window=3)
    state = _make_state()
    _seed_qa(state, 5)
    mem._update_text_qa_history(
        state,
        [{"role": "user", "content": "new question"}],
        clean_text="new answer",
        decision="response",
    )
    qa = state.memory_state["qa_history"]
    assert len(qa) == 3
    assert qa[0]["query"] == "q3"
    assert qa[-1]["query"] == "new question"


def test_text_path_window_zero_keeps_unbounded():
    mem = _MemIO(qa_history_window=0)
    state = _make_state()
    _seed_qa(state, 5)
    mem._update_text_qa_history(
        state,
        [{"role": "user", "content": "new question"}],
        clean_text="new answer",
        decision="response",
    )
    assert len(state.memory_state["qa_history"]) == 6
