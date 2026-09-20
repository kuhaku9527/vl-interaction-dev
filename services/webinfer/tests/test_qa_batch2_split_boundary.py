"""QA supplement: batch-2 split boundary regression tests (independent review).

Targets (per QA review of db72019/badfded/062d3ef):
  1. ``chat_payload.is_forced_silence`` — full mode x force-toggle matrix.
  2. ``chat_payload.update_query_state`` — state side-effect transitions
     (first query / same query / changed query archive / disabled / blank).
  3. ``build_stream_frames`` vs ``parse_model_decision`` consistency across
     split deltas and late-decision re-judgement (multi-marker cases).
  4. Thin-wrapper equivalence — ``InferLoopMixin`` wrappers produce the same
     result as calling the extracted module functions directly.

Run: python -m pytest tests/test_qa_batch2_split_boundary.py -q
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import chat_payload  # noqa: E402
import frame_parsing  # noqa: E402
from adapter_types import SessionState  # noqa: E402
from infer_loop import (  # noqa: E402
    _normalize_interaction_mode,
    _parse_live_frames,
    build_stream_frames,
)
from response_format import parse_model_decision  # noqa: E402

# ---------------------------------------------------------------------------
# 1. is_forced_silence — mode x force-toggle matrix
# ---------------------------------------------------------------------------


class TestIsForcedSilenceMatrix:
    """Boundary matrix: interaction_mode x force_silence_before_query x query."""

    @pytest.mark.parametrize("mode", ["live", "call", "jarvis", "LIVE", " Call ", "unknown", ""])
    @pytest.mark.parametrize("force", [True, False])
    def test_matrix_direct_module(self, mode: str, force: bool) -> None:
        """Direct module call: the pure decision."""
        # live + force + no query -> True; everything else -> False
        expect = mode == "live" and force
        assert chat_payload.is_forced_silence(mode, force, None) is expect
        # with a pending query, live never forces silence
        assert chat_payload.is_forced_silence(mode, force, "hello") is False

    def test_wrapper_matches_module_live_force_on_no_query(self) -> None:
        adapter = _make_adapter(force_silence_before_query=True)
        state = _make_state(current_query_text=None)
        wrapper = adapter._is_forced_silence(state, "live")
        module = chat_payload.is_forced_silence("live", True, None)
        assert wrapper is True
        assert wrapper == module

    def test_wrapper_matches_module_call_jarvis_off(self) -> None:
        adapter = _make_adapter(force_silence_before_query=True)
        state = _make_state(current_query_text=None)
        for mode in ("call", "jarvis"):
            assert adapter._is_forced_silence(state, mode) is False
            assert adapter._is_forced_silence(state, mode) == chat_payload.is_forced_silence(
                mode, True, None
            )

    def test_wrapper_matches_module_force_off(self) -> None:
        adapter = _make_adapter(force_silence_before_query=False)
        state = _make_state(current_query_text=None)
        assert adapter._is_forced_silence(state, "live") is False
        assert adapter._is_forced_silence(state, "live") == chat_payload.is_forced_silence(
            "live", False, None
        )

    def test_query_present_never_forces(self) -> None:
        adapter = _make_adapter(force_silence_before_query=True)
        state = _make_state(current_query_text="hi")
        assert adapter._is_forced_silence(state, "live") is False


# ---------------------------------------------------------------------------
# 2. update_query_state — state side effects
# ---------------------------------------------------------------------------


class TestUpdateQueryStateSideEffects:
    def test_disabled_returns_none_no_mutation(self) -> None:
        state = _make_state(current_query_text="old")
        result = chat_payload.update_query_state(state, "new prompt", "0.0 seconds", False)
        assert result is None
        assert state.current_query_text == "old"
        assert state.query_in_current_chunk is False

    def test_blank_prompt_returns_none(self) -> None:
        state = _make_state(current_query_text="old")
        result = chat_payload.update_query_state(state, "   ", "0.0 seconds", True)
        assert result is None
        assert state.current_query_text == "old"

    def test_first_query_installs(self) -> None:
        state = _make_state(current_query_text=None)
        result = chat_payload.update_query_state(state, "  what is that?  ", "1.0 seconds", True)
        assert result == "what is that?"
        assert state.current_query_text == "what is that?"
        assert state.query_start_time == "1.0 seconds"
        assert state.query_in_current_chunk is True
        assert state._pending_qa_archive is None

    def test_same_query_no_archive_idempotent(self) -> None:
        state = _make_state(current_query_text="same", query_start_time="2.0 seconds")
        state._pending_qa_archive = ("stale", "9.0 seconds")
        result = chat_payload.update_query_state(state, "same", "3.0 seconds", True)
        assert result == "same"
        assert state.current_query_text == "same"
        # same query: time not re-stamped, no archive write
        assert state.query_start_time == "2.0 seconds"
        assert state._pending_qa_archive == ("stale", "9.0 seconds")  # untouched

    def test_changed_query_archives_old(self) -> None:
        state = _make_state(current_query_text="old query", query_start_time="1.0 seconds")
        result = chat_payload.update_query_state(state, "new query", "3.0 seconds", True)
        assert result == "new query"
        assert state.current_query_text == "new query"
        assert state.query_start_time == "3.0 seconds"
        assert state.query_in_current_chunk is True
        assert state._pending_qa_archive == ("old query", "1.0 seconds")

    def test_wrapper_matches_module(self) -> None:
        adapter = _make_adapter(use_prompt_as_query=True)
        state = _make_state(current_query_text=None)
        w = adapter._update_query_state(state, "hello world", "0.5 seconds")
        assert w == "hello world"
        # fresh state, direct module call, same inputs -> same transition
        state2 = _make_state(current_query_text=None)
        m = chat_payload.update_query_state(state2, "hello world", "0.5 seconds", True)
        assert w == m
        assert state.current_query_text == state2.current_query_text
        assert state.query_start_time == state2.query_start_time
        assert state.query_in_current_chunk == state2.query_in_current_chunk


# ---------------------------------------------------------------------------
# 3. build_stream_frames vs parse_model_decision consistency
# ---------------------------------------------------------------------------


class TestBuildStreamFramesConsistency:
    def test_split_delta_marker_matches_full_parse(self) -> None:
        """A decision marker split across deltas commits exactly once."""
        deltas = ["Hel", "lo ", "</", "sil", "ence>", " world"]
        frames = build_stream_frames(deltas)
        assert frames[0]["type"] == "decision"
        # the accumulated prefix is 'Hello </silence>' -> silence decision
        full, _, _ = parse_model_decision("Hello </silence>")
        assert frames[0]["decision"] == full == "silence"
        # silence -> no content frames
        assert all(f["type"] == "decision" for f in frames)

    def test_response_marker_split_then_content_streams(self) -> None:
        deltas = ["</", "response>", "Sure", ", ", "here"]
        frames = build_stream_frames(deltas)
        assert frames[0] == {
            "type": "decision",
            "decision": "response",
            "delegation_question": None,
        }
        assert [f["token"] for f in frames if f["type"] == "content"] == [
            "Sure",
            ", ",
            "here",
        ]

    def test_late_delegation_rejudges_whole_turn(self) -> None:
        """Taught format: </response> <note> </delegation> <question>."""
        deltas = ["</response>", " OK ", "</delegation>", " should I continue?"]
        frames = build_stream_frames(deltas)
        # whole turn re-judged as delegation: single decision frame, question fresh
        assert len(frames) == 1
        assert frames[0]["type"] == "decision"
        assert frames[0]["decision"] == "delegation"
        _, _, q = parse_model_decision("</response> OK </delegation> should I continue?")
        assert frames[0]["delegation_question"] == q
        # no content frames -> question never spoken as TTS
        assert all(f["type"] == "decision" for f in frames)

    def test_late_not_for_me_rejudges(self) -> None:
        deltas = ["</response>", " actually ", "</not-for-me>"]
        frames = build_stream_frames(deltas)
        assert len(frames) == 1
        assert frames[0]["decision"] == "not-for-me"
        full, _, _ = parse_model_decision("</response> actually </not-for-me>")
        assert frames[0]["decision"] == full

    def test_delegation_question_refreshed_across_deltas(self) -> None:
        deltas = ["<delegation>", "Can you", " help", "?"]
        frames = build_stream_frames(deltas)
        assert len(frames) == 1
        assert frames[0]["decision"] == "delegation"
        _, _, q = parse_model_decision("<delegation>Can you help?")
        assert frames[0]["delegation_question"] == q
        assert frames[0]["delegation_question"] == "Can you help?"

    def test_markerless_fail_open_to_response(self) -> None:
        deltas = ["no markers here", " just text"]
        frames = build_stream_frames(deltas)
        assert frames[0]["decision"] == "response"
        # fail-open path: single decision frame + ONE content frame with the
        # whole accumulated clean text (matches parse_model_decision output)
        assert [f["token"] for f in frames if f["type"] == "content"] == [
            "no markers here just text",
        ]
        full, clean, _ = parse_model_decision("no markers here just text")
        assert frames[0]["decision"] == full == "response"
        assert clean == "no markers here just text"

    def test_empty_deltas_skipped(self) -> None:
        frames = build_stream_frames(["", None, "", "x"])
        assert frames[0]["decision"] == "response"
        assert [f["token"] for f in frames if f["type"] == "content"] == ["x"]


# ---------------------------------------------------------------------------
# 4. Thin-wrapper equivalence — wrapper == direct module function
# ---------------------------------------------------------------------------


class TestThinWrapperEquivalence:
    def test_time_range_for_frame(self) -> None:
        adapter = _make_adapter(frame_seconds=2.5)
        for idx in (0, 1, 3):
            w = adapter._time_range_for_frame(idx)
            m = frame_parsing._time_range_for_frame(idx, 2.5)
            assert w == m
            assert w == f"{idx * 2.5:.1f} seconds"

    def test_save_base64_frame_counter_bump(self) -> None:
        adapter = _make_adapter()
        state = _make_state(session_frame_counter=0)
        data_url = "data:image/jpeg;base64," + _b64()
        w = adapter._save_base64_frame(data_url, state)
        assert w == data_url
        assert state.session_frame_counter == 1
        # direct module call on a fresh state bumps identically
        state2 = _make_state(session_frame_counter=0)
        m = frame_parsing._save_base64_frame(data_url, state2)
        assert w == m
        assert state2.session_frame_counter == 1

    def test_validate_local_image_path_equivalence(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            img = root / "pic.jpg"
            img.write_bytes(b"\xff\xd8\xff\xe0 fake")
            adapter = _make_adapter(allowed_local_image_roots=(str(root),))
            w = adapter._validate_local_image_path(str(img))
            m = frame_parsing._validate_local_image_path(str(img), (str(root),))
            assert w == m == img.resolve()

    def test_resolve_frame_ref_path(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            img = root / "pic.png"
            img.write_bytes(b"\x89PNG fake")
            adapter = _make_adapter(allowed_local_image_roots=(str(root),))
            state = _make_state(session_frame_counter=0)
            w = adapter._resolve_frame_ref({"kind": "path", "value": str(img)}, state)
            m = frame_parsing._resolve_frame_ref(
                {"kind": "path", "value": str(img)}, state, (str(root),)
            )
            assert w == m == str(img.resolve())

    def test_resolve_frame_ref_data_url(self) -> None:
        adapter = _make_adapter()
        ref = {"kind": "data_url", "value": "data:image/jpeg;base64," + _b64()}
        # wrapper path (fresh state)
        state_w = _make_state(session_frame_counter=0)
        w = adapter._resolve_frame_ref(ref, state_w)
        # module path (fresh state) -> same result, same single counter bump
        state_m = _make_state(session_frame_counter=0)
        m = frame_parsing._resolve_frame_ref(ref, state_m, ())
        assert w == m == ref["value"]
        assert state_w.session_frame_counter == 1
        assert state_m.session_frame_counter == 1

    def test_resolve_frame_ref_unsupported_kind(self) -> None:
        from aiohttp import web

        adapter = _make_adapter()
        state = _make_state()
        with pytest.raises(web.HTTPBadRequest) as exc_info:
            adapter._resolve_frame_ref({"kind": "remote"}, state)
        assert "unsupported image reference kind" in exc_info.value.text

    def test_parse_live_frames_re_export_is_module_func(self) -> None:
        # the re-exported symbol IS the extracted module function (same object)
        assert _parse_live_frames is frame_parsing._parse_live_frames
        assert _normalize_interaction_mode is frame_parsing._normalize_interaction_mode

    def test_parse_live_frames_equivalence(self) -> None:
        payload = {"frames": [{"image_b64": _b64(), "ts_ms": 12.5}]}
        w = _parse_live_frames(payload)
        m = frame_parsing._parse_live_frames(payload)
        assert w == m == [{"image_b64": _b64(), "ts_ms": 12.5}]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _b64(payload: bytes = b"\xff\xd8\xff\xe0 fake-jpeg") -> str:
    return base64.b64encode(payload).decode("ascii")


def _make_state(**overrides: Any) -> SessionState:
    base: dict[str, Any] = {
        "current_query_text": None,
        "query_start_time": None,
        "query_in_current_chunk": False,
        "_pending_qa_archive": None,
        "session_frame_counter": 0,
    }
    base.update(overrides)
    return SimpleNamespace(**base)  # type: ignore[return-value]


def _make_adapter(**config_overrides: Any) -> Any:
    """A minimal InferLoopMixin-like object exposing the thin wrappers.

    The thin wrappers only read ``self.config`` (and pass ``state`` through),
    so a SimpleNamespace with the right config attributes is sufficient to
    exercise the wrapper delegation path without booting the full adapter.
    """
    from infer_loop import InferLoopMixin

    config_defaults: dict[str, Any] = {
        "frame_seconds": 1.0,
        "allowed_local_image_roots": (),
        "use_prompt_as_query": False,
        "force_silence_before_query": False,
    }
    config_defaults.update(config_overrides)
    obj = SimpleNamespace(config=SimpleNamespace(**config_defaults))
    # bind the thin wrapper methods (unbound functions) to the stub object
    for name in (
        "_is_forced_silence",
        "_time_range_for_frame",
        "_resolve_frame_ref",
        "_save_base64_frame",
        "_validate_local_image_path",
        "_update_query_state",
    ):
        setattr(obj, name, getattr(InferLoopMixin, name).__get__(obj, InferLoopMixin))
    return obj
