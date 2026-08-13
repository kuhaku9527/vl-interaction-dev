"""Jarvis LLM streaming round helper (extracted from ``jarvis_mode.py``).

Moved from ``jarvis_mode.py`` (spec codebase-map-2026-08-13.md §2.1,
priority 5): the P0-A streaming LLM consumption wiring —
``StreamingTurnConsumer`` construction (decision-first NDJSON stream →
SentenceBuffer → per-sentence TTS) plus the fail-open non-streaming retry
and the turn-finish broadcast. ``JarvisStateMachine._send_to_llm_streaming``
keeps a thin facade with the identical signature (tests call it directly and
patch it as an instance attribute); the dispatcher ``_send_to_llm`` and the
single-shot ``_send_to_llm_non_streaming`` / ``_finish_llm_turn`` / TTS
facades stay in the class (the multimodality body is pinned by
``test_webui_static_contract``).
"""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any


async def send_to_llm_streaming(
    *,
    text: str,
    stream_tts: bool,
    interaction_mode: str,
    reply_epoch: int | None,
    config: Any,
    conv_history: deque[tuple[str, str]],
    max_history_turns: int,
    tts_reply_seq: int,
    consumer_cls: type,
    on_sentence: Callable[[str, int, int], None],
    is_cancelled: Callable[[], bool],
    on_retry_non_streaming: Callable[..., Awaitable[None]],
    on_finish_turn: Callable[..., Awaitable[None]],
    logger: logging.Logger,
) -> int:
    """P0-A streaming LLM consumption: decision first, sentence TTS.

    Moved verbatim from ``JarvisStateMachine._send_to_llm_streaming``: the
    actual NDJSON stream consumption + SentenceBuffer flushing + per-sentence
    TTS wiring is delegated to ``consumer_cls`` (the shared
    :class:`~.turn_streaming.StreamingTurnConsumer`, spec
    ``draft-live-interaction-layer.md`` §4.3). This helper keeps the jarvis
    turn semantics: per-reply session id, fail-open non-streaming retry, and
    ``on_finish_turn`` broadcast.

    Fail-open (never lose the reply): if the stream errors before any frame,
    the whole turn is re-run through the non-streaming path; if it errors
    after a decision, the buffered remainder is synthesized and the reply is
    broadcast anyway (logged).

    Returns the updated ``tts_reply_seq`` — the caller applies it to its own
    state. The caller must reset the per-stream cancel flag to False BEFORE
    delegating so the ``is_cancelled`` callback (which reads the live
    attribute) reflects the new round.
    """
    # v3.24: prepend bounded conversation history (same as non-streaming).
    history_snapshot = list(conv_history)[-max_history_turns * 2 :]

    endpoint_url = f"{config.llm_api_url}{config.llm_text_path}"
    reply_session = tts_reply_seq
    tts_reply_seq += 1

    consumer = consumer_cls(
        endpoint_url=endpoint_url,
        model=config.llm_model,
        system_prompt=config.llm_system_prompt,
        history_snapshot=history_snapshot,
        max_tokens=200,
        temperature=0.7,
        timeout_s=30.0,
        on_sentence=on_sentence,
        is_cancelled=is_cancelled,
        stream_logger=logger,
    )
    result = await consumer.consume(
        text,
        interaction_mode=interaction_mode,
        reply_session=reply_session,
    )

    if result.needs_non_streaming_retry:
        # No decision was ever delivered (transport failure, HTTP error,
        # or an error frame BEFORE the decision frame): clean retry
        # through the non-streaming path so the reply is never lost —
        # nothing was spoken, so there is no double-play risk.
        await on_retry_non_streaming(
            text,
            stream_tts=stream_tts,
            interaction_mode=interaction_mode,
            reply_epoch=reply_epoch,
        )
        return tts_reply_seq

    if result.cancelled:
        # Barge-in / exit word: stop everything. The epoch bump already
        # cancelled every in-flight sentence task; the partial reply is
        # intentionally not broadcast (the user is talking over it).
        return tts_reply_seq

    await on_finish_turn(
        text=text,
        response=result.full_response,
        decision=result.decision,
        delegation_question=result.delegation_question,
        stream_tts=stream_tts,
        force_jarvis_voice=True,
        reply_epoch=reply_epoch,
    )
    return tts_reply_seq


__all__ = [
    "send_to_llm_streaming",
]
