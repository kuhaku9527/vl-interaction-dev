"""Chat-payload pure logic for the multimodal ``/v1/chat/completions`` path.

Extracted from ``infer_loop.py`` (batch-2 monolith decoupling, zero behaviour
change). Owns the *pure / deterministic* parts of the five-step
``_chat_payload_*`` orchestration — the query-state transition, the
forced-silence decision, and the result data assembly (turn input record,
prediction dict, adapter timing, summarizer timing, memory payload).

The ``InferLoopMixin`` orchestration (which depends on ``self`` — clients,
config, session mixins — and on wall-clock timing for the latency breakdown)
stays in ``infer_loop.py`` and calls these helpers. Timing values are passed
in by the caller so the ``time.perf_counter()`` measurement points are
identical to the pre-split code.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from adapter_types import SessionState


def update_query_state(
    state: SessionState,
    prompt_text: str,
    time_range: str,
    use_prompt_as_query: bool,
) -> str | None:
    """Track the pending user query on ``state`` (mirror of the historic method).

    Returns the active query text, or ``None`` when query tracking is disabled
    or the prompt is blank. A changed query is archived to
    ``state._pending_qa_archive`` before the new one is installed (same
    semantics as the pre-split ``_update_query_state``).
    """
    if not use_prompt_as_query:
        return None

    normalized_prompt = (prompt_text or "").strip()
    if not normalized_prompt:
        return None

    if state.current_query_text is None:
        state.current_query_text = normalized_prompt
        state.query_start_time = time_range
        state.query_in_current_chunk = True
        return normalized_prompt

    if normalized_prompt != state.current_query_text:
        state._pending_qa_archive = (
            state.current_query_text,
            state.query_start_time,
        )
        state.current_query_text = normalized_prompt
        state.query_start_time = time_range
        state.query_in_current_chunk = True
        return normalized_prompt

    return state.current_query_text


def is_forced_silence(
    interaction_mode: str,
    force_silence_before_query: bool,
    current_query_text: str | None,
) -> bool:
    """Decide whether this turn is a forced-silence (no-inference) turn.

    Forced silence only applies to the ``live`` mode: it suppresses model
    inference when no user query is pending, so the assistant stays quiet
    between events. ``call`` (direct voice-to-text) and ``jarvis``
    (wake-word driven) modes never force silence -- they drive their own
    turn flow and always want a real model response (issue #45).
    """
    if interaction_mode != "live":
        return False
    return force_silence_before_query and not current_query_text


def build_turn_input_record(
    messages: list[dict[str, Any]],
    ctx: SimpleNamespace,
    state: SessionState,
) -> dict[str, Any]:
    """Assemble the per-turn model-input record dict (pure data assembly)."""
    return {
        "source_message": messages[-1] if messages else None,
        "vllm_message": ctx.user_message,
        "chunk_index": state.chunk_index,
        "has_image": True,
        "image_path": str(ctx.image_paths[-1]),
        "image_paths_batch": [str(ip) for ip in ctx.image_paths],
        "num_chunk_turns": state.current_chunk["turn_count"],
        "num_chunk_frames": state.current_chunk["frame_count"],
        "image_paths": list(state.current_chunk["image_paths"]),
        "frame_time_ranges": list(state.current_chunk["frame_time_ranges"]),
    }


def build_prediction_dict(
    ctx: SimpleNamespace,
    turn_output_record: dict[str, Any],
    total_time: float,
) -> dict[str, Any]:
    """Assemble the per-turn prediction dict (pure data assembly).

    Mirrors the historic ``_chat_payload_finalize`` assembly: the saved
    ``model_input`` record is attached to ``ctx.turn_input_record`` (the same
    object referenced by ``prediction["input"]``), then the optional
    ``chunk_start_model_input_path`` / ``raw_prediction`` fields are added.
    """
    if ctx.model_input_record is not None:
        ctx.turn_input_record["model_input"] = ctx.model_input_record
    prediction: dict[str, Any] = {
        "turn": ctx.turn_count,
        "time_range": ctx.time_range,
        "query": ctx.query_text,
        "input": ctx.turn_input_record,
        "output": turn_output_record,
        "prediction": ctx.generated_text,
        "total_time": round(total_time, 3),
        "inference_time": round(ctx.inference_time, 3),
    }
    if ctx.chunk_start_model_input_path:
        prediction["chunk_start_model_input_path"] = ctx.chunk_start_model_input_path
    if ctx.raw_text and ctx.raw_text.strip() != ctx.generated_text:
        prediction["raw_prediction"] = ctx.raw_text
    return prediction


def build_adapter_timing(ctx: SimpleNamespace, t_end: float) -> dict[str, Any]:
    """Assemble the ``streamingharness.timing`` dict (pure data assembly).

    ``t_end`` is supplied by the caller (measured at the same point as the
    pre-split code) so the latency breakdown is unchanged.
    """
    adapter_timing: dict[str, Any] = {
        "adapter_total_ms": round((t_end - ctx.t_start) * 1000, 1),
    }
    if not ctx.is_forced_silence:
        adapter_timing["prompt_build_ms"] = round(
            (ctx.t_prompt_build_end - ctx.t_prompt_build_start) * 1000, 1
        )
        adapter_timing["vllm_inference_ms"] = round(ctx.inference_time * 1000, 1)
        adapter_timing["post_process_ms"] = round((t_end - ctx.t_inference_end) * 1000, 1)
        adapter_timing["pre_inference_ms"] = round(
            (ctx.t_prompt_build_start - ctx.t_start) * 1000, 1
        )
    return adapter_timing


def build_summarizer_timing(state: SessionState) -> dict[str, Any]:
    """Assemble the ``streamingharness.summarizer_timing`` dict (pure read)."""
    summarizer_timing: dict[str, Any] = {}
    if state.mid_term_history:
        last_mid = state.mid_term_history[-1]
        summarizer_timing["last_mid_term_ms"] = round(last_mid.get("inference_time", 0) * 1000, 1)
        summarizer_timing["last_mid_term_chunk"] = last_mid.get("chunk_index")
        if last_mid.get("barrier_wait_time") is not None:
            summarizer_timing["barrier_wait_ms"] = round(last_mid["barrier_wait_time"] * 1000, 1)
    if state.long_term_history:
        last_long = state.long_term_history[-1]
        summarizer_timing["last_long_term_ms"] = round(last_long.get("inference_time", 0) * 1000, 1)
    return summarizer_timing


def build_memory_payload(state: SessionState) -> dict[str, Any]:
    """Assemble the ``streamingharness.memory`` dict (pure read)."""
    return {
        "mid_term_summaries": [
            {
                "chunk_index": e["chunk_index"],
                "frame_range": e["frame_range"],
                "summary_text": e["summary_text"],
            }
            for e in state.mid_term_summaries
        ],
        "long_term_memory": state.memory_state.get("long_term_memory", ""),
    }
