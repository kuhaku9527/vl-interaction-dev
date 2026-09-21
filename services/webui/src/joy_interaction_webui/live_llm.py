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
import os
import sys
import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any

from .turn_controller import TurnState

logger = logging.getLogger(__name__)


def _ensure_event_json_importable() -> str | None:
    """Put ``services/common`` on ``sys.path`` so the shared emitter can be found.

    ★ THIS IS THE ROOT-CAUSE FIX for a silent observability failure.

    ``event_json`` lives in ``services/common/`` and is imported here as a
    TOP-LEVEL module. webui starts with ``PYTHONPATH=<repo>/services/webui/src``
    (see ``services/scripts/run-windows.ps1`` → ``Start-Webui``), so before this
    helper existed the import below raised ``ModuleNotFoundError`` on every
    real start and the guard silently installed the no-op fallback. The result:
    **no ``live_decision`` event was ever written to disk by a real session**,
    while the unit tests (which put the repo root on ``sys.path``) passed.

    That is the same class of defect as the pre-#146 state — and it is exactly
    why "CI green" does not prove an instrumentation works. It is fixed by
    locating the directory structurally (walk up from this file), the same
    approach ``services/webinfer/infer_loop.py`` already uses.

    Returns
    -------
        The directory that was added to ``sys.path``, or ``None`` when
        ``services/common/event_json.py`` could not be located.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    cur = here
    while True:
        common = os.path.join(cur, "services", "common")
        if os.path.exists(os.path.join(common, "event_json.py")):
            if common not in sys.path:
                sys.path.insert(0, common)
            return common
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


_EVENT_JSON_DIR = _ensure_event_json_importable()

try:  # ADR-0014 event stream (services/common); optional in stripped checkouts.
    from event_json import emit_event as _emit_event
except ImportError:  # pragma: no cover - import guard only
    # ★ NEVER silent. The whole point of this module's #156 fix is that a
    # swallowed ImportError previously cost us every decision event on the real
    # path; degrading quietly here would reintroduce exactly that. Mirrors
    # ``services/webinfer/infer_loop.py``, which logs the same condition.
    logger.warning(
        "event_json emitter unavailable (searched from %r, found dir=%r); "
        "live_decision events will NOT be written",
        __file__,
        _EVENT_JSON_DIR,
    )

    def _emit_event(*_args: Any, **_kwargs: Any) -> None:
        """Degraded no-op — logged loudly above, never silently (约法三章)."""


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
        logger.warning("live_decision event emit failed", exc_info=True)


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


def resolve_latency_ms(
    turn_started_at: float | None,
    ended_at: float | None = None,
    clock: Callable[[], float] | None = None,
) -> int | None:
    """Turn latency from a stamped round-start, or ``None`` when there is none.

    #156: the live chain had **no** round-start stamp at all, so the timing
    axis had no data source. The stamp is a monotonic reading taken where the
    round opens; the latency is that span up to the decision record.

    ★ Both ends MUST come from the SAME clock. ``clock`` is the reader's own
    clock function (``LiveStateMachine._clock``, injectable in tests); falling
    back to ``time.monotonic`` while the stamp came from somewhere else would
    produce a nonsense span (measured: 98,263,125 ms — i.e. days — from a test
    clock stamped at 100.0 s). A caller that stamps from a custom clock must
    resolve with that same clock.

    ★ ``None`` in, ``None`` out — a missing stamp must NEVER become ``0``.
    A fabricated zero would read as "instantaneous" and pollute the timing axis
    with a value that means the opposite of the truth (unknown). The reader
    (``decision_events``) counts missing values explicitly for this reason.
    """
    if turn_started_at is None:
        return None
    if ended_at is None:
        ended_at = clock() if clock is not None else time.monotonic()
    return max(0, round((ended_at - turn_started_at) * 1000.0))


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
    raw_text: str | None = None,
) -> None:
    """Record one live-turn decision to the ADR-0014 JSONL event stream.

    MUST be called BEFORE the caller rewrites ``delegation`` to ``silence`` —
    otherwise delegation never appears in the record (see the ordering note in
    ``finish_llm_turn``).

    ``raw_text`` is what the DECISION PARSER saw (the model's raw output, before
    the server stripped its special tokens); it falls back to ``response`` when
    the caller cannot supply it. The distinction is load-bearing for the
    reader: a ``not-for-me`` round has an EMPTY body (content frames only
    accumulate for ``response``) while its raw output carried the marker plus
    prose — measuring only the body would misread a real decision as "the model
    emitted nothing" (#156).

    ★ The fallback is ``if not raw_text``, **not** ``if raw_text is None``.
    An empty string does not mean "the parser saw nothing" — it means "the
    caller did not supply it": the streaming result's ``raw_text`` defaults to
    ``""`` and duck-typed consumers may omit it entirely. Measured on real data
    (96 rows in ``logs/events/webui-2026-09-21.jsonl``), treating ``""`` as a
    genuine zero produced **24 rounds recorded as ``raw_text_len=0`` while their
    bodies held text** — reads that the reader then reported as *failed outputs*
    even though the assistant had plainly spoken. The invariant this restores is
    ``raw_text_len >= response_chars``: whatever the body holds, the parser must
    have seen at least that much.
    """
    parser_input = raw_text or response
    raw_len, raw_hash = _text_fingerprint(parser_input)
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
    session_id: str | None = None,
    turn_started_at: float | None = None,
) -> tuple[int, int]:
    """Send ASR text to webinfer (interaction_mode='live', stream=True).

    Reuses the shared :class:`StreamingTurnConsumer` (P0-A) so live and
    jarvis converge on one streaming path.  ``frames`` (optional
    ``[{image_b64, ts_ms}]``) routes the round through webinfer's live
    visual path (layer 1).  Fail-open: pre-frame stream failure falls back
    to the single-shot call; mid-stream failure keeps the flushed sentences
    (consumer already handled).

    ``session_id`` / ``turn_started_at`` (#156) are carried through to the
    decision record: the first attributes the round to a conversation, the
    second is the monotonic round-start stamp the latency is measured from.

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
        # NB: no session_id / turn_started_at are passed here. The retry
        # callback is the live machine's own facade, which reads BOTH from its
        # own state (``self.session_id`` / ``self._turn_started_at``); the
        # round-start stamp was taken at the round's entry point, so the
        # recorded latency honestly covers the failed stream plus the retry —
        # the user waited for exactly that span. Keeping this call shape
        # unchanged also preserves the existing fail-open contract test.
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
        session_id=session_id,
        turn_started_at=turn_started_at,
        # Duck-typed consumers (tests inject their own ``consumer_cls``) may not
        # carry ``raw_text``; treat its absence as "not supplied" so the record
        # falls back to the body rather than raising inside the turn.
        raw_text=getattr(result, "raw_text", None),
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
    session_id: str | None = None,
    turn_started_at: float | None = None,
) -> None:
    """Single-shot LLM fallback (fail-open, never lose the reply)."""
    messages: list[dict] = [{"role": "system", "content": config.llm_system_prompt}]
    for role, content in list(conv_history)[-max_history_turns * 2 :]:
        messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": text})

    response = ""
    decision = "silence"
    delegation_question = None
    raw_text: str | None = None
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
            # #156: the harness carries the parser's own input verbatim. Taking
            # it here keeps raw_text_len meaningful on the fail-open path too —
            # otherwise the retry path would report the (possibly empty) body
            # and look like an "empty output" round.
            harness_raw = harness.get("raw_content")
            raw_text = harness_raw if isinstance(harness_raw, str) else None
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
        session_id=session_id,
        turn_started_at=turn_started_at,
        raw_text=raw_text,
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
    session_id: str | None = None,
    turn_started_at: float | None = None,
    raw_text: str | None = None,
    clock: Callable[[], float] | None = None,
) -> tuple[int, asyncio.Task | None]:
    """Shared post-LLM turn completion (controller, delegation, broadcast).

    ``session_id`` / ``turn_started_at`` / ``raw_text`` feed the decision
    record (#156) and are keyword-only additions — callers that omit them keep
    working, but then the record cannot attribute the round to a session and
    the latency field stays ``None`` (the reader reports both explicitly).
    ``clock`` must be the SAME clock the stamp came from (see
    :func:`resolve_latency_ms`).

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
        session_id=session_id,
        latency_ms=resolve_latency_ms(turn_started_at, clock=clock),
        logger=logger,
        raw_text=raw_text,
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
