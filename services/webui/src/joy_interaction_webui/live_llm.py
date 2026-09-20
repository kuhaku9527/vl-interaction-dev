"""Live LLM round helpers (extracted from ``live_mode.LiveStateMachine``).

Moved from ``live_mode.py``: the streaming LLM send (P0-A
``StreamingTurnConsumer`` wiring), the fail-open non-streaming retry, the
shared post-LLM turn completion (decision consumption, delegation routing,
conversation history, broadcast), and the TTS-turn completion watcher.
``LiveStateMachine`` keeps thin facades (``_send_to_llm`` /
``_send_to_llm_non_streaming`` / ``_finish_llm_turn`` /
``_wait_tts_turn_done``) that delegate here; behavior is unchanged.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any

from .turn_controller import TurnState

try:  # ADR-0014 event stream (services/common); optional in stripped checkouts.
    from event_json import emit_event as _emit_event
except Exception:  # pragma: no cover - import guard only

    def _emit_event(*_args: Any, **_kwargs: Any) -> None:
        """Fail-open no-op when the shared event sink is unavailable."""


def emit_event(*args: Any, **kwargs: Any) -> None:
    """Emit one ADR-0014 event, never raising into the business path.

    Wrapped (rather than called directly) so the business turn can never be
    aborted by a logging hiccup, and so tests can patch this module-level
    symbol. ``emit_event`` itself documents that a non-serializable ``extra``
    raises ``ValueError`` — we deliberately swallow that here: observability
    is important, but a dropped event must not cost the user a turn.
    """
    try:
        _emit_event(*args, **kwargs)
    except Exception:
        logging.getLogger(__name__).warning(
            "live_decision event emit failed", exc_info=True
        )


def _text_fingerprint(text: str | None) -> tuple[int, str]:
    """Return ``(len, short_sha256)`` for a model output.

    ADR-0014 forbids PII in the ``extra`` payload, so the raw reply is never
    recorded. The length is kept because ``len == 0`` is the single most
    common false-silence mode (the model emitted nothing at all) and must stay
    distinguishable from a deliberate ``</silence>``.
    """
    raw = text or ""
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return len(raw), digest


def _record_live_decision(
    *,
    decision: str,
    text: str,
    response: str,
    delegation_question: str | None,
    session_id: str | None,
    latency_ms: int | None,
    logger: logging.Logger,
    interaction_mode: str = "live",
    round_kind: str = "user",
    frames_n: int | None = None,
) -> None:
    """Record one live-turn decision to the ADR-0014 JSONL event stream.

    MUST be called BEFORE the caller rewrites ``delegation`` to ``silence`` —
    otherwise delegation never appears in the record (see the ordering note in
    ``finish_llm_turn``).
    """
    raw_len, raw_hash = _text_fingerprint(response)
    extra: dict[str, Any] = {
        "decision": decision,
        "round_kind": round_kind,
        "interaction_mode": interaction_mode,
        "raw_text_len": raw_len,
        "raw_text_sha256_16": raw_hash,
        "response_chars": len((response or "").strip()),
        "user_text_len": len(text or ""),
        "delegation_question_len": len(delegation_question or ""),
    }
    if frames_n is not None:
        extra["frames_n"] = frames_n
    emit_event(
        "webui",
        "live_decision",
        "info",
        session_id=session_id,
        latency_ms=latency_ms,
        extra=extra,
    )


async def send_to_llm(
    *,
    text: str,
    interaction_mode: str,
    frames: list | None,
    config: Any,
    llm_reply_epoch: int,
    tts_reply_seq: int,
    conv_history: deque[tuple[str, str]],
    max_history_turns: int,
    consumer_cls: type,
    on_sentence: Callable[[str, int, int], None],
    is_cancelled: Callable[[], bool],
    on_finish_turn: Callable[..., Awaitable[None]],
    on_retry_non_streaming: Callable[..., Awaitable[None]],
    on_silence_wake: Callable[[], None] | None = None,
    logger: logging.Logger,
) -> tuple[int, int]:
    """Send ASR text to webinfer (interaction_mode='live', stream=True).

    Reuses the shared :class:`StreamingTurnConsumer` (P0-A) so live and
    jarvis converge on one streaming path.  ``frames`` (optional
    ``[{image_b64, ts_ms}]``) routes the round through webinfer's live
    visual path (layer 1).  Fail-open: pre-frame stream failure falls back
    to the single-shot call; mid-stream failure keeps the flushed sentences
    (consumer already handled).

    Returns ``(llm_reply_epoch, tts_reply_seq)`` — the caller applies them
    to its own state.  The caller resets ``_llm_stream_cancel`` /
    ``_sentence_spawned_this_turn`` to False BEFORE delegating so the
    ``is_cancelled`` callback (which reads the live attribute) reflects the
    new round.
    """
    llm_reply_epoch += 1
    turn_reply_epoch = llm_reply_epoch
    logger.info("[live-mode] turn-start reply_epoch bumped to %d", turn_reply_epoch)

    reply_session = tts_reply_seq
    tts_reply_seq += 1

    history_snapshot = list(conv_history)[-max_history_turns * 2 :]
    consumer = consumer_cls(
        endpoint_url=f"{config.llm_api_url}{config.llm_text_path}",
        model=config.llm_model,
        system_prompt=config.llm_system_prompt,
        history_snapshot=history_snapshot,
        max_tokens=200,
        temperature=0.7,
        timeout_s=30.0,
        on_sentence=on_sentence,
        is_cancelled=is_cancelled,
        stream_logger=logger,
        frames=frames,
        on_silence_wake=on_silence_wake,
    )
    result = await consumer.consume(
        text,
        interaction_mode=interaction_mode,
        reply_session=reply_session,
    )

    if result.needs_non_streaming_retry:
        logger.info("[live-mode] fail-open -> non-streaming retry")
        await on_retry_non_streaming(
            text,
            interaction_mode=interaction_mode,
            reply_epoch=turn_reply_epoch,
            frames=frames,
        )
        return llm_reply_epoch, tts_reply_seq
    if result.cancelled:
        # Barge-in: the epoch bump already cancelled every in-flight
        # sentence task; the partial reply is intentionally not broadcast.
        logger.info("[live-mode] LLM stream cancelled (barge-in); skipping broadcast")
        return llm_reply_epoch, tts_reply_seq

    await on_finish_turn(
        text=text,
        response=result.full_response,
        decision=result.decision,
        delegation_question=result.delegation_question,
        reply_epoch=turn_reply_epoch,
    )
    return llm_reply_epoch, tts_reply_seq


async def send_to_llm_non_streaming(
    *,
    text: str,
    interaction_mode: str,
    reply_epoch: int | None,
    frames: list | None,
    config: Any,
    conv_history: deque[tuple[str, str]],
    max_history_turns: int,
    on_finish_turn: Callable[..., Awaitable[None]],
    logger: logging.Logger,
) -> None:
    """Single-shot LLM fallback (fail-open, never lose the reply)."""
    messages: list[dict] = [{"role": "system", "content": config.llm_system_prompt}]
    for role, content in list(conv_history)[-max_history_turns * 2 :]:
        messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": text})

    response = ""
    decision = "silence"
    delegation_question = None
    try:
        import httpx

        request_body: dict = {
            "model": config.llm_model,
            "messages": messages,
            "max_tokens": 200,
            "temperature": 0.7,
            "interaction_mode": interaction_mode,
        }
        if frames:
            request_body["frames"] = frames
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{config.llm_api_url}{config.llm_text_path}",
                json=request_body,
            )
            resp.raise_for_status()
            payload = resp.json()
            choice = (payload.get("choices") or [{}])[0]
            response = (choice.get("message") or {}).get("content") or ""
            response = response.strip() if isinstance(response, str) else ""
            harness = payload.get("streamingharness") or {}
            decision = harness.get("decision") or ("response" if response else "silence")
            delegation_question = harness.get("delegation_question")
    except Exception as exc:
        logger.error("[live-mode] non-streaming LLM call failed: %s", exc)
        response = ""
        decision = "silence"

    await on_finish_turn(
        text=text,
        response=response,
        decision=decision,
        delegation_question=delegation_question,
        reply_epoch=reply_epoch,
    )


async def finish_llm_turn(
    *,
    text: str,
    response: str,
    decision: str,
    delegation_question: str | None,
    reply_epoch: int | None,
    llm_reply_epoch: int,
    background_service: object | None,
    conv_history: deque[tuple[str, str]],
    sentence_spawned_this_turn: bool,
    on_llm_response: Callable[[str, str], None] | None,
    ctrl: Any,
    tts_turn_task: asyncio.Task | None,
    wait_tts_turn_done: Callable[[], Awaitable[None]],
    logger: logging.Logger,
) -> tuple[int, asyncio.Task | None]:
    """Shared post-LLM turn completion (controller, delegation, broadcast).

    Returns ``(current_turn_reply_epoch, tts_turn_task)`` — the caller
    applies them to its own state.
    """
    logger.info("[live-mode] LLM response (decision=%s): %r", decision, (response or "")[:120])

    # agentteams #146: persist the decision to the machine-readable ADR-0014
    # event stream. Evaluations ("was it the right moment to speak?") are
    # impossible while decisions only live in memory / truncated text logs.
    #
    # ★ ORDERING IS LOAD-BEARING: the delegation branch below rewrites
    # ``decision`` to ``"silence"``, so the record MUST happen first or
    # delegation is permanently lost from the stream.
    _record_live_decision(
        decision=decision,
        text=text,
        response=response,
        delegation_question=delegation_question,
        session_id=None,
        latency_ms=None,
        logger=logger,
    )

    # Feed the controller: PROCESSING -> THINKING (even for empty output).
    try:
        ctrl.on_llm_token(response or "")
    except Exception as exc:
        logger.warning("[live-mode] on_llm_token failed: %s", exc)

    # v3.37: webinfer decision="delegation" routes to BackgroundModelService
    # (same sub-agent as jarvis / video).
    if decision == "delegation":
        try:
            bg = background_service
            if (
                bg is not None
                and getattr(bg, "enabled", True)
                and not getattr(bg, "_closed", False)
            ):
                payload_text = (response or "").strip() or text
                if delegation_question:
                    payload_text = f"{payload_text}\n\n</delegation> {delegation_question}"
                bg.handle_foreground_response(
                    payload_text,
                    metrics={"user_prompt": text, "delegation_question": delegation_question},
                )
        except Exception as exc:
            logger.warning("[live-mode] delegation routing failed: %s", exc)
        # Delegation replies are surfaced by the background agent; nothing
        # is spoken (foreground line empty).
        decision = "silence"

    # Addressee-detection Phase 2 (spec addressee-detection.md
    # §4.1/§4.2.4): decision="not-for-me" means the utterance was NOT
    # addressed to the AI (self-talk / replying to someone else / talking
    # to another person). Treat it as a non-target turn: no TTS, back to
    # LISTENING — same controller path as silence, with a dedicated log
    # so the semantic gate is distinguishable from plain silence.
    if decision == "not-for-me":
        logger.info(
            "[addressee] semantic not-for-me: utterance=%r not addressed to AI; not broadcasting",
            (text or "")[:80],
        )

    conv_history.append(("user", text))
    conv_history.append(("assistant", response or ""))

    # P0-A: the streaming path pushed per-sentence audio via tts_sentence,
    # so the llm_reply transcript must NOT be re-synthesized by the
    # browser (source tag mirrors jarvis_voice). live_voice is ONLY valid
    # when sentences were actually spawned (streaming path); the
    # fail-open non-streaming retry spawns no sentences, so it must tag
    # live_text and let the browser synthesize audio via /api/tts/synthesize
    # (BUG-1: previously a silent reply on the retry path).
    reply_source = (
        "live_voice" if (decision == "response" and sentence_spawned_this_turn) else "live_text"
    )
    current_turn_reply_epoch = reply_epoch if reply_epoch is not None else llm_reply_epoch
    if on_llm_response:
        try:
            on_llm_response(response or "", source=reply_source)
        except Exception as exc:
            logger.warning("[live-mode] on_llm_response failed: %s", exc)

    if decision == "response" and sentence_spawned_this_turn:
        # THINKING -> SPEAKING (sentences already synthesizing/playing).
        try:
            ctrl.on_tts_started()
        except Exception as exc:
            logger.warning("[live-mode] on_tts_started failed: %s", exc)
        tts_turn_task = asyncio.create_task(wait_tts_turn_done())
    else:
        # silence / delegation / not-for-me / empty: THINKING -> SPEAKING
        # -> LISTENING (nothing is spoken; not-for-me additionally logged
        # as a semantic non-target above).
        try:
            ctrl.on_tts_started()
            ctrl.on_tts_finished()
            logger.info("[live-mode] agent turn finished (no TTS); listening")
        except Exception as exc:
            logger.warning("[live-mode] tts lifecycle feed failed: %s", exc)

    return current_turn_reply_epoch, tts_turn_task


async def wait_tts_turn_done(
    *,
    tts_sentence_tasks: set[asyncio.Task],
    ctrl: Any,
    logger: logging.Logger,
) -> None:
    """Wait for all sentence TTS tasks, then return the controller to
    LISTENING (SPEAKING -> LISTENING)."""
    try:
        while True:
            pending = [t for t in list(tts_sentence_tasks) if not t.done()]
            if not pending:
                break
            await asyncio.gather(*pending, return_exceptions=True)
    except asyncio.CancelledError:
        raise
    if ctrl.state == TurnState.SPEAKING:
        try:
            ctrl.on_tts_finished()
            logger.info("[live-mode] agent turn finished; listening")
        except Exception as exc:
            logger.warning("[live-mode] on_tts_finished failed: %s", exc)


__all__ = [
    "finish_llm_turn",
    "send_to_llm",
    "send_to_llm_non_streaming",
    "wait_tts_turn_done",
]
