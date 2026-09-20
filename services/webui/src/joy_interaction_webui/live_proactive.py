"""Live proactive speak loop helpers (extracted from ``live_mode.LiveStateMachine``).

Moved from ``live_mode.py`` (spec live-visual-cb.md §2.4/§3 layer 2/3):
the env-gated proactive loop (``LIVE_PROACTIVE_ENABLED`` /
``LIVE_PROACTIVE_INTERVAL_S``), the non-streaming VLM visual round, and the
runtime switch decision.  ``LiveStateMachine`` keeps thin facades
(``set_proactive`` / ``_proactive_loop`` / ``_send_proactive_prompt`` /
``_call_proactive_vlm``) that delegate here; behavior is unchanged (env
default OFF -> no loop task, zero behavior change).
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any

from .turn_controller import TurnState

try:  # ADR-0014 event stream; reuse the fail-open wrapper from live_llm.
    from .live_llm import _record_live_decision as _record_decision
except Exception:  # pragma: no cover - import guard only

    def _record_decision(**_kwargs: Any) -> None:
        """Fail-open no-op when the shared event sink is unavailable."""


#: Default seconds between proactive visual checks (env
#: ``LIVE_PROACTIVE_INTERVAL_S`` overrides).
_DEFAULT_PROACTIVE_INTERVAL_S: float = 5.0


def _env_flag(name: str, default: bool = False) -> bool:
    """Read a boolean env gate (``1/true/yes/on`` are truthy)."""
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def proactive_enabled_from_env() -> bool:
    """Read the ``LIVE_PROACTIVE_ENABLED`` env gate (default OFF)."""
    return _env_flag("LIVE_PROACTIVE_ENABLED", default=False)


def proactive_interval_from_env(
    default_interval_s: float = _DEFAULT_PROACTIVE_INTERVAL_S,
) -> float:
    """Resolve the proactive check interval from ``LIVE_PROACTIVE_INTERVAL_S``.

    Invalid / non-positive values fall back to ``default_interval_s``.
    Moved verbatim from ``LiveStateMachine.__init__`` (zero behavior
    change).
    """
    try:
        interval = float(
            os.environ.get("LIVE_PROACTIVE_INTERVAL_S", str(default_interval_s))
            or default_interval_s
        )
    except ValueError:
        interval = default_interval_s
    if interval <= 0:
        interval = default_interval_s
    return interval


def proactive_switch_action(*, enabled: bool, env_gate: bool, task_running: bool) -> str:
    """Decide the runtime proactive switch action.

    Returns one of ``reject`` / ``start`` / ``already_running`` /
    ``cancel`` / ``already_off`` — the branch structure of
    ``LiveStateMachine.set_proactive`` moved verbatim.
    """
    enabled = bool(enabled)
    if enabled:
        if not env_gate:
            return "reject"
        if task_running:
            return "already_running"
        return "start"
    if task_running:
        return "cancel"
    return "already_off"


async def proactive_loop(
    *,
    interval_s: float,
    frame_window: int,
    turn_state: Callable[[], Any],
    recent_frames: list,
    frames_payload: Callable[[Any], list[dict]],
    send_proactive_prompt: Callable[..., Awaitable[None]],
    logger: logging.Logger,
) -> None:
    """Periodic VLM visual check while LISTENING (env-gated).

    Every ``interval_s`` seconds, when the controller is in LISTENING and at
    least one frame is buffered, sample the LATEST frame and ask webinfer
    (non-streaming, no user text) whether there is something worth saying.
    Exceptions fail open (log + skip the round) so proactive work never
    disturbs the user dialog (约法三章).  Moved verbatim from
    ``LiveStateMachine._proactive_loop``.
    """
    logger.info(
        "[live-proactive] loop started (interval=%.1fs, frame_window=%d)",
        interval_s,
        frame_window,
    )
    while True:
        try:
            await asyncio.sleep(interval_s)
            if turn_state() != TurnState.LISTENING:
                continue
            if not recent_frames:
                continue
            await send_proactive_prompt(frames=frames_payload([recent_frames[-1]]))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("[live-proactive] loop round failed; fail-open: %s", exc)


async def send_proactive_prompt(
    *,
    frames: list,
    tts_reply_seq: int,
    llm_reply_epoch: int,
    turn_state: Callable[[], Any],
    call_proactive_vlm: Callable[[list], Awaitable[tuple[str, str]]],
    spawn_sentence_tts: Callable[[str, int, int], None],
    on_llm_response: Callable[[str, str], None] | None,
    ctrl: Any,
    wait_tts_turn_done: Callable[[], Awaitable[None]],
    logger: logging.Logger,
) -> tuple[int, int, asyncio.Task | None]:
    """Ask webinfer whether the latest frame deserves a spoken comment.

    Lightweight non-streaming VLM round: no user text, small max_tokens.
    decision=response -> speak the reply proactively (sentence TTS path,
    controller -> SPEAKING -> LISTENING via ``wait_tts_turn_done``);
    silence / not-for-me / empty -> stay quiet.  Any failure fails open
    (log + skip) and never disturbs the user dialog.

    Returns ``(tts_reply_seq, llm_reply_epoch, tts_turn_task)`` — the
    caller applies them to its own state (``tts_turn_task`` is None when
    nothing was spoken).
    """
    reply_session = tts_reply_seq
    tts_reply_seq += 1
    llm_reply_epoch += 1
    logger.info(
        "[live-proactive] VLM visual check (frames=%d, reply_epoch=%d)",
        len(frames),
        llm_reply_epoch,
    )
    decision, response = await call_proactive_vlm(frames)
    logger.info(
        "[live-proactive] VLM decision=%s response=%r",
        decision,
        (response or "")[:80],
    )

    # agentteams #146: record the proactive decision BEFORE either early
    # return below. Proactive rounds never reached ``qa_history`` (that path
    # needs non-empty user text) and produced no event, so an agent-initiated
    # "should I speak?" decision left *no* trace anywhere — making the
    # "真机验收主动搭话质量" requirement in live-visual-cb.md §1 impossible to
    # evaluate offline. Both quiet paths (silence/not-for-me, and the
    # race-guard skip) are decisions too, so the record must precede them.
    _record_decision(
        decision=decision,
        text="",
        response=response,
        delegation_question=None,
        session_id=None,
        latency_ms=None,
        logger=logger,
        round_kind="proactive",
        frames_n=len(frames),
    )

    if decision != "response" or not (response or "").strip():
        # silence / not-for-me / empty: nothing worth saying; the
        # controller stays LISTENING untouched — wait for the next
        # interval. No controller feed happens here (silence is the
        # normal, high-frequency path and must not produce warnings).
        logger.info("[live-proactive] staying quiet (decision=%s)", decision)
        return tts_reply_seq, llm_reply_epoch, None

    # P2 race guard (QA regression): the user may have started speaking
    # while the VLM call was in flight. If the controller is no longer
    # LISTENING at decision time, stay silent — proactive work must never
    # speak over the user dialog (spec §2.4). Fail-open: skip, no TTS.
    if turn_state() != TurnState.LISTENING:
        logger.info(
            "[live-proactive] controller no longer LISTENING (state=%s); skip proactive speech",
            turn_state().name,
        )
        return tts_reply_seq, llm_reply_epoch, None

    # Proactive speak: agent-initiated turn (LISTENING -> THINKING ->
    # SPEAKING), then synthesize + push the single sentence through the
    # normal TTS path. Barge-in during playback reuses the existing
    # HARD_INTERRUPTED path.
    try:
        ctrl.on_agent_turn_started()
        ctrl.on_tts_started()
    except Exception as exc:
        logger.warning("[live-proactive] controller feed failed: %s", exc)
    spawn_sentence_tts(response, 0, reply_session)
    if on_llm_response:
        try:
            on_llm_response(response, source="live_proactive")
        except Exception as exc:
            logger.warning("[live-proactive] on_llm_response failed: %s", exc)
    tts_turn_task = asyncio.create_task(wait_tts_turn_done())
    return tts_reply_seq, llm_reply_epoch, tts_turn_task


async def call_proactive_vlm(
    *,
    config: Any,
    frames: list,
    logger: logging.Logger,
) -> tuple[str, str]:
    """POST a lightweight non-streaming VLM visual round to webinfer.

    Returns ``(decision, response)``. Fail-open: on any error log and
    return ``("silence", "")`` so a proactive round never disturbs the
    dialog.
    """
    import httpx

    messages: list[dict] = [{"role": "system", "content": config.llm_system_prompt}]
    messages.append({"role": "user", "content": ""})
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{config.llm_api_url}{config.llm_text_path}",
                json={
                    "model": config.llm_model,
                    "messages": messages,
                    "max_tokens": 256,
                    "temperature": 0.7,
                    "interaction_mode": "live",
                    "frames": frames,
                },
            )
            resp.raise_for_status()
            payload = resp.json()
            choice = (payload.get("choices") or [{}])[0]
            response = (choice.get("message") or {}).get("content") or ""
            response = response.strip() if isinstance(response, str) else ""
            harness = payload.get("streamingharness") or {}
            decision = harness.get("decision") or ("response" if response else "silence")
            return decision, response
    except Exception as exc:
        logger.error("[live-proactive] VLM call failed; fail-open: %s", exc)
        return "silence", ""


__all__ = [
    "_DEFAULT_PROACTIVE_INTERVAL_S",
    "call_proactive_vlm",
    "proactive_enabled_from_env",
    "proactive_interval_from_env",
    "proactive_loop",
    "proactive_switch_action",
    "send_proactive_prompt",
]
