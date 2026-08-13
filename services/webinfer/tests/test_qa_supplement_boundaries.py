"""QA independent supplement: qa_history window=1 boundary + forced-silence matrix.

* ``_trim_qa_history_to_window`` with window=1 must keep exactly the newest entry.
* forced-silence exemption matrix at the pure-function level:
    live + frames            -> NOT forced (P1-3 fix)
    live, no frames, no query -> forced (historical behaviour preserved)
    live, no frames, query    -> not forced
    call / jarvis             -> never forced, even with frames
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from chat_payload import is_forced_silence  # noqa: E402
from infer_loop import InferLoopMixin  # noqa: E402
from response_format import _trim_qa_history_to_window  # noqa: E402


def test_trim_window_one_keeps_only_newest():
    qa = [{"query": f"q{i}"} for i in range(4)]
    _trim_qa_history_to_window(qa, 1)
    assert [e["query"] for e in qa] == ["q3"]


def test_forced_silence_matrix():
    # pure function signature: (interaction_mode, force_silence_before_query, current_query_text)
    # live + force + NO query -> forced (historical behaviour)
    assert is_forced_silence("live", True, None) is True
    # live + force + query -> not forced
    assert is_forced_silence("live", True, "hello") is False
    # live + force disabled -> never forced
    assert is_forced_silence("live", False, None) is False
    # call / jarvis never forced, even with force flag on
    assert is_forced_silence("call", True, None) is False
    assert is_forced_silence("jarvis", True, None) is False


def test_wrapper_has_frames_exemption_matches_pure_function():
    class W(InferLoopMixin):
        def __init__(self):
            self.config = type("C", (), {"force_silence_before_query": True})()

    w = W()
    state = type("S", (), {"current_query_text": None})()
    # wrapper with frames -> exempt
    assert w._is_forced_silence(state, "live", has_frames=True) is False
    # wrapper without frames -> delegates to pure function (forced, no query)
    assert w._is_forced_silence(state, "live") is True
