"""Main inference-loop mixin: chat endpoints, frame parsing, and main-model call.

Defines :class:`InferLoopMixin`, which carries the primary推理 loop:
``handle_text_chat`` / ``handle_chat_completions`` / ``_handle_chat_payload``
(including its five cohesive ``_chat_payload_*`` sub-steps), ``_handle_text_payload``,
frame reference parsing, and the main-model call previously on ``StreamingInferAdapter``.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import re
import sys
import time
from collections.abc import AsyncIterator, Iterable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from adapter_types import SessionState
from aiohttp import web
from io_utils import normalize_image_b64
from openai import AsyncOpenAI
from prompt_assembly import compose_live_visual_messages
from prompt_building import (
    _compute_prompt_guard_max_chars,
    _estimate_messages_chars,
    _trim_messages_to_ctx,
)
from request_parsing import (
    _extract_all_image_refs,
    _extract_time_range_from_request,
    _extract_time_ranges_from_request,
    _extract_user_prompt_text,
    _read_json,
    _request_session_id,
)
from response_format import (
    _chat_completion_response,
    _openai_error_response,
    _parse_decision_tokens,
    _short,
    archive_chunk_response_records,
    build_model_input_record,
    extract_response_payload,
    normalize_model_output,
    parse_model_decision,
    strip_decision_tokens,
)
from time_ranges import (
    _extract_time_range_from_text,
    _format_turn_time_range,
    _parse_start_second,
    _strip_time_range_from_text,
)

from config import reset_chunk_state

LOGGER = logging.getLogger("streaming_infer_adapter")

# Interaction modes isolate the decision-token framework (issues #44/#45).
#   live   (default): full silence/speak/delegate framework + forced silence
#                     before a user query is pending (original behaviour).
#   call   (voice-to-text direct chat): NO decision tokens, forced silence off.
#   jarvis (wake-word driven): decision tokens KEPT (jarvis consumes the
#                     `decision` field), but forced silence off (jarvis drives
#                     its own turn flow).
_VALID_INTERACTION_MODES = frozenset({"live", "call", "jarvis"})

# --- live visual path (spec draft-live-visual-cb.md §3 层 1) ----------------
# An optional top-level ``frames`` field on ``POST /v1/text/chat`` with
# ``interaction_mode="live"`` routes the round through the multimodal path
# (streaming for user rounds, non-streaming for proactive rounds). No
# ``frames`` field -> the existing text path runs byte-for-byte unchanged
# (pure-text live zero-regression rule).
_LIVE_FRAMES_MAX: int = 6


def _parse_live_frames(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate and normalize the optional top-level ``frames`` payload field.

    Contract (约法三章 — invalid frames are an explicit 400, never silently
    swallowed):

      * ``frames`` must be a list of dicts when present;
      * at most ``_LIVE_FRAMES_MAX`` frames;
      * each frame must carry a non-empty, base64-decodable ``image_b64``;
      * ``ts_ms`` (optional) must be a number when present.

    Returns a normalized ``[{"image_b64": str, "ts_ms": int|float|None}]``
    list. A missing ``frames`` field returns ``[]`` (callers treat that as
    "no frames" and keep the pure-text path).
    """
    raw = payload.get("frames")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise web.HTTPBadRequest(text="frames must be a list")
    if len(raw) > _LIVE_FRAMES_MAX:
        raise web.HTTPBadRequest(text=f"frames exceeds limit: {len(raw)} > {_LIVE_FRAMES_MAX}")
    frames: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise web.HTTPBadRequest(text=f"frames[{index}] must be an object")
        image_b64 = item.get("image_b64")
        if not isinstance(image_b64, str) or not image_b64.strip():
            raise web.HTTPBadRequest(
                text=f"frames[{index}].image_b64 must be a non-empty base64 string"
            )
        # Shared normalization (io_utils.normalize_image_b64): tolerate a full
        # data URI (``data:image/<fmt>;base64,<b64>``) by stripping the prefix
        # and validate the payload decodes. The normalized output is always
        # raw base64 so the downstream visual message builder re-prepends its
        # own ``data:image/jpeg;base64,`` prefix exactly once.
        try:
            image_b64 = normalize_image_b64(image_b64)
        except ValueError as exc:
            raise web.HTTPBadRequest(text=f"frames[{index}].image_b64 {exc}") from exc
        ts_ms = item.get("ts_ms")
        if ts_ms is not None and not isinstance(ts_ms, (int, float)):
            raise web.HTTPBadRequest(text=f"frames[{index}].ts_ms must be a number")
        frames.append({"image_b64": image_b64, "ts_ms": ts_ms})
    return frames


def _normalize_interaction_mode(mode: str | None) -> str:
    """Resolve an inbound ``interaction_mode`` to a known mode.

    Missing / empty / unrecognized values fall back to ``"live"``. An
    unknown value is logged (not silently swallowed) so a misconfigured
    caller cannot arm an unexpected code path.
    """
    if not mode:
        return "live"
    normalized = mode.strip().lower()
    if normalized in _VALID_INTERACTION_MODES:
        return normalized
    LOGGER.warning("unknown interaction_mode %r; falling back to 'live'", mode)
    return "live"


# --- P0-A TTS-streaming: NDJSON frame protocol for /v1/text/chat stream=true ----
#
# Decision-token markers the streaming path watches for. Only the markers
# that :func:`parse_model_decision` itself recognises are used, so decision
# semantics are byte-for-byte identical to the non-streaming path (the model
# is taught to emit the closing ``</silence>`` / ``</response>`` /
# ``</delegation>`` / ``</not-for-me>`` form, with ``<delegation>`` and
# ``<not-for-me>`` tolerated like the parser).
_STREAM_DECISION_MARKER_RE = re.compile(
    r"</\s*(?:silence|response|delegation|not-for-me)\s*>"
    r"|<\s*(?:delegation|not-for-me)\s*>",
    re.IGNORECASE,
)


def _find_first_decision_marker(text: str) -> int | None:
    """Return the earliest start index of a complete decision marker, else ``None``.

    A marker may span multiple streamed deltas (``<``, ``/``, ``response``,
    ``>``), so the caller accumulates deltas until this returns a value and
    only then commits to a decision.
    """
    match = _STREAM_DECISION_MARKER_RE.search(text or "")
    return match.start() if match else None


def _extract_stream_delta(chunk: Any) -> str:
    """Extract the incremental content string from an OpenAI-style stream chunk.

    A chunk may legitimately carry no content (role-only first chunk, a
    usage-only final chunk when ``include_usage`` is on); those yield ``""``
    and are skipped by the frame builder (protocol: no empty token frames).
    """
    choices = getattr(chunk, "choices", None) or []
    if not choices:
        return ""
    delta = getattr(choices[0], "delta", None)
    if delta is None:
        return ""
    content = getattr(delta, "content", None)
    return content or ""


def _extract_stream_usage(chunk: Any) -> Any:
    """Return the usage object carried by a stream chunk, or ``None``."""
    return getattr(chunk, "usage", None)


def build_stream_frames(deltas: Iterable[str]) -> list[dict[str, Any]]:
    """Build the P0-A NDJSON frame list from an iterable of content deltas.

    Protocol (decision-first + continuous content):
      * ``{"type": "decision", "decision": ..., "delegation_question": ...}``
        is emitted as soon as the first complete decision marker is seen
        (normally the very first frame: the model emits ``</silence>`` /
        ``</response>`` before the body). The decision is derived with
        :func:`parse_model_decision` over the accumulated prefix, so the
        semantics match the non-streaming parser exactly.
      * ``{"type": "content", "token": ...}`` frames follow for every delta
        after a ``response`` decision; silence / delegation / not-for-me
        produce no content frames (silence has nothing to say; the
        delegation question is carried by the decision frame and must not
        be spoken as TTS; not-for-me is a bare non-addressed marker).
      * **Delegation-taught format hardening**: the system prompt teaches
        ``</response> <note> </delegation> <question>`` — ``</response>``
        PRECEDES the delegation tag. ``parse_model_decision`` gives a
        delegation tag ANYWHERE priority (documented hardening); the frame
        builder mirrors it: a ``response`` commit at the first marker is
        provisional, and a later ``</delegation>`` / ``<delegation>`` in the
        stream re-judges the whole turn as delegation — the frame list is
        rebuilt as a single delegation decision frame (no content frames, so
        the question is never spoken as TTS). A later ``</not-for-me>`` /
        ``<not-for-me>`` re-judges the whole turn as not-for-me the same way
        (independent single-marker state — never mixed with a response body).
      * Empty / no-token deltas are skipped (never emitted as a frame).
      * If the stream ends before any complete marker, the accumulated text
        is failed-open through :func:`parse_model_decision` (which maps a
        marker-less output to ``response``) so the caller never hangs.
    """
    frames: list[dict[str, Any]] = []
    pending = ""
    raw_prefix = ""  # accumulated text up to and including the FIRST marker
    raw_tail = ""  # content after the first marker (watched for a late delegation tag)
    decision: str | None = None
    delegation_question: str | None = None
    for delta in deltas:
        if not delta:
            continue
        if decision is None:
            pending += delta
            if _find_first_decision_marker(pending) is None:
                continue
            decision, clean_text, delegation_question = parse_model_decision(pending)
            raw_prefix = pending
            frames.append(
                {
                    "type": "decision",
                    "decision": decision,
                    "delegation_question": delegation_question,
                }
            )
            if decision == "response" and clean_text:
                raw_tail += clean_text
                frames.append({"type": "content", "token": clean_text})
            pending = ""
        elif decision == "response":
            raw_tail += delta
            if _find_first_decision_marker(raw_tail) is not None:
                # Late decision tag (taught delegation format, or a model
                # correction to </not-for-me>). Re-judge the whole turn;
                # the delegated question / not-for-me body must never
                # stream as content.
                decision, _clean, delegation_question = parse_model_decision(raw_prefix + raw_tail)
                frames = [
                    {
                        "type": "decision",
                        "decision": decision,
                        "delegation_question": delegation_question,
                    }
                ]
            else:
                frames.append({"type": "content", "token": delta})
        elif decision == "delegation":
            # The delegated question may arrive in later deltas; keep the
            # decision frame's question fresh so it is never truncated.
            raw_tail += delta
            _d, _c, delegation_question = parse_model_decision(raw_prefix + raw_tail)
            if frames and frames[0].get("type") == "decision":
                frames[0]["delegation_question"] = delegation_question
    if decision is None:
        decision, clean_text, delegation_question = parse_model_decision(pending)
        frames.append(
            {
                "type": "decision",
                "decision": decision,
                "delegation_question": delegation_question,
            }
        )
        if decision == "response" and clean_text:
            frames.append({"type": "content", "token": clean_text})
    return frames


# --- ADR-0014 JSONL event emission (services/common/event_json.py) ----------
try:

    def _ensure_event_json_importable() -> None:
        """Put ``services/common`` on ``sys.path`` by walking up from this file.

        Returns without inserting if the shared emitter cannot be located;
        the subsequent import then raises ImportError, which is caught below
        and downgraded to a logged no-op (约法三章: not silent, just not fatal).
        """
        here = os.path.dirname(os.path.abspath(__file__))
        cur = here
        while True:
            common = os.path.join(cur, "services", "common")
            if os.path.exists(os.path.join(common, "event_json.py")):
                if common not in sys.path:
                    sys.path.insert(0, common)
                return
            parent = os.path.dirname(cur)
            if parent == cur:
                return
            cur = parent

    _ensure_event_json_importable()
    from event_json import emit_event
except ImportError:
    logging.getLogger(__name__).warning(
        "event_json emitter unavailable; JSONL event emission disabled "
        "(packaged build without services/common on path?)"
    )

    def emit_event(*_args, **_kwargs):
        """No-op fallback used only when the shared emitter is unavailable.

        Logged once above (not silent) per 约法三章 - never swallow the failure.
        """
        return None


class InferLoopMixin:
    """Main inference loop: chat endpoints, frame parsing, model call."""

    def _resolve_backend(self, model_name: str | None = None) -> tuple[AsyncOpenAI, str]:
        if model_name and model_name in self.main_clients:
            return self.main_clients[model_name]
        return self.main_client, self.config.main_model

    async def handle_text_chat(self, request: web.Request) -> web.Response:
        """Handle the text-only chat-completions endpoint."""
        # v3.37 single-LLM-gateway: text-only chat-completion endpoint that
        # runs the same system-prompt + memory + token-guard + decision-token
        # parsing pipeline as the multimodal path, but rejects any image_url
        # content so voice-dialog callers cannot smuggle frames through.
        try:
            payload = await _read_json(request)
        except Exception as exc:
            return _openai_error_response(f"invalid JSON body: {exc}", status=400)

        messages = payload.get("messages")
        if not isinstance(messages, list) or not messages:
            return _openai_error_response("messages must be a non-empty list", status=400)
        valid_roles = {"system", "user", "assistant"}
        for index, message in enumerate(messages):
            if not isinstance(message, dict):
                return _openai_error_response(f"messages[{index}] must be a dict", status=400)
            role = message.get("role")
            if role not in valid_roles:
                return _openai_error_response(
                    f"messages[{index}].role must be one of {sorted(valid_roles)}, got {role!r}",
                    status=400,
                )
            content = message.get("content")
            if isinstance(content, list):
                for _, part in enumerate(content):
                    if isinstance(part, dict) and part.get("type") in {
                        "image_url",
                        "image",
                    }:
                        return _openai_error_response(
                            "image content not allowed on /v1/text/chat; use /v1/chat/completions for multimodal",
                            status=400,
                        )
            elif isinstance(content, str):
                if "data:image/" in content and ";base64," in content:
                    return _openai_error_response(
                        "inline base64 image not allowed on /v1/text/chat",
                        status=400,
                    )
            elif content is None:
                return _openai_error_response(
                    f"messages[{index}].content must not be null", status=400
                )
            else:
                return _openai_error_response(
                    f"messages[{index}].content must be str or list, got {type(content).__name__}",
                    status=400,
                )

        session_id = _request_session_id(request, payload)
        requested_model = payload.get("model")
        interaction_mode = _normalize_interaction_mode(payload.get("interaction_mode"))
        client, model_name = self._resolve_backend(requested_model)
        state = self.get_session(session_id)
        t_start = time.perf_counter()
        # Live visual path (spec draft-live-visual-cb.md): an optional
        # top-level ``frames`` field on a live round routes through the
        # multimodal path — streaming (user rounds) or non-streaming
        # (proactive rounds) by the payload's existing ``stream`` flag.
        # Frames are validated explicitly (invalid -> 400, never silent);
        # non-live modes reject frames (they must use /v1/chat/completions).
        # An absent OR empty ``frames`` field keeps the existing text path
        # running byte-for-byte unchanged (pure-text live zero regression).
        if payload.get("frames"):
            try:
                frames = _parse_live_frames(payload)
            except web.HTTPException as exc:
                # Invalid frames are an explicit 400 (约法三章 — never silent).
                return _openai_error_response(exc.text or "invalid frames", status=exc.status_code)
            if interaction_mode != "live":
                return _openai_error_response(
                    "frames only supported with interaction_mode='live'",
                    status=400,
                )
            if payload.get("stream"):
                return await self._handle_text_chat_streaming(
                    request,
                    state,
                    payload,
                    client=client,
                    model_name=model_name,
                    interaction_mode=interaction_mode,
                    session_id=session_id,
                    t_start=t_start,
                    frames=frames,
                )
            async with state.lock:
                try:
                    result = await self._handle_text_payload(
                        state,
                        payload,
                        client=client,
                        model_name=model_name,
                        interaction_mode=interaction_mode,
                        frames=frames,
                    )
                except web.HTTPException:
                    raise
                except Exception as exc:
                    LOGGER.exception("live visual chat completion failed")
                    emit_event(
                        "webinfer",
                        "infer_error",
                        level="error",
                        session_id=session_id,
                        extra={
                            "error_type": type(exc).__name__,
                            "path": "text_chat_live_visual",
                        },
                    )
                    return _openai_error_response(str(exc), status=502)
            emit_event(
                "webinfer",
                "webinfer_request",
                level="info",
                session_id=session_id,
                latency_ms=round((time.perf_counter() - t_start) * 1000),
                extra={"model": model_name, "path": "text_chat_live_visual"},
            )
            return web.json_response(result)
        # P0-A TTS streaming: ``stream: true`` takes the NDJSON streaming path
        # (decision frame first, then content frames). The non-streaming path
        # below is untouched for every other caller (call mode etc.).
        if payload.get("stream"):
            return await self._handle_text_chat_streaming(
                request,
                state,
                payload,
                client=client,
                model_name=model_name,
                interaction_mode=interaction_mode,
                session_id=session_id,
                t_start=t_start,
            )
        async with state.lock:
            try:
                result = await self._handle_text_payload(
                    state,
                    payload,
                    client=client,
                    model_name=model_name,
                    interaction_mode=interaction_mode,
                )
            except web.HTTPException:
                raise
            except Exception as exc:
                LOGGER.exception("text chat completion failed")
                emit_event(
                    "webinfer",
                    "infer_error",
                    level="error",
                    session_id=session_id,
                    extra={"error_type": type(exc).__name__, "path": "text_chat"},
                )
                return _openai_error_response(str(exc), status=502)
        emit_event(
            "webinfer",
            "webinfer_request",
            level="info",
            session_id=session_id,
            latency_ms=round((time.perf_counter() - t_start) * 1000),
            extra={"model": model_name, "path": "text_chat"},
        )
        return web.json_response(result)

    async def _handle_text_payload(
        self,
        state: SessionState,
        payload: dict[str, Any],
        *,
        client: AsyncOpenAI | None = None,
        model_name: str | None = None,
        interaction_mode: str = "live",
        frames: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        # Single-LLM-gateway text path. Composes the system prompt
        # (character profile + [Local Wiki]), runs the v3.34 prompt
        # token guard, forwards to the main model, parses decision
        # tokens, and records the turn in qa_history so the next call
        # sees the same conversation context as the video path.
        client = client or self.main_client
        model_name = model_name or self.config.main_model

        # PR #42 follow-up: _memory_recall must fire on the production text path
        # so the [Local Wiki] section actually lands in the prompt. Fail-open.
        pre_messages = list(payload.get("messages") or [])
        last_user_text = ""
        for m in reversed(pre_messages):
            if m.get("role") == "user" and isinstance(m.get("content"), str):
                last_user_text = m["content"]
                break
        try:
            await self._memory_recall(state, last_user_text)
        except Exception as exc:
            LOGGER.warning("memory_recall failed for %s: %s", state.session_id, exc)

        api_messages = list(payload.get("messages") or [])
        # call mode drops the decision-token framework (issues #44/#45);
        # live mode uses the four-state prompt (addressee Phase 2).
        composed_system = (
            self._build_memory_prompt(
                state,
                include_decision_tokens=interaction_mode != "call",
                interaction_mode=interaction_mode,
            )
            or ""
        ).strip()

        # Resolve any caller-supplied system message into a flat list.
        caller_messages = [dict(m) for m in api_messages if m.get("role") != "system"]
        if frames:
            # Live visual round (spec draft-live-visual-cb.md): the final user
            # turn carries the current utterance + the image frames; history
            # turns stay text-only (frames never enter persistent history).
            http_messages = compose_live_visual_messages(
                composed_system=composed_system,
                last_user_text=last_user_text,
                frames=frames,
                caller_messages=caller_messages,
            )
        elif composed_system:
            http_messages = [{"role": "system", "content": composed_system}, *caller_messages]
        else:
            http_messages = caller_messages

        # v3.34 prompt guard runs LAST so it sees the full assembled
        # messages list (system + turns).
        max_total_chars = _compute_prompt_guard_max_chars(self.config.main_ctx_tokens)
        if max_total_chars > 0:
            http_messages, removed = _trim_messages_to_ctx(
                [dict(m) for m in http_messages], max_total_chars
            )
        else:
            removed = 0

        generation_kwargs = self._main_generation_kwargs(payload)
        response = await client.chat.completions.create(
            model=model_name,
            messages=http_messages,
            **generation_kwargs,
        )
        raw_text = response.choices[0].message.content if response.choices else ""
        usage = response.usage.model_dump() if getattr(response, "usage", None) else None

        decision, clean_text, delegation_question = _parse_decision_tokens(raw_text or "")

        # Update qa_history so the NEXT call sees this turn as context,
        # matching what the multimodal path does for video sessions.
        self._update_text_qa_history(state, api_messages, clean_text, decision)

        memory_chars = len(composed_system)
        qa_history_len = len(state.memory_state.get("qa_history", []))
        prompt_chars = _estimate_messages_chars(http_messages)

        return _chat_completion_response(
            model=self.config.adapter_model,
            content=clean_text,
            usage=usage,
            raw_model=model_name,
            raw_text=raw_text or "",
            decision=decision,
            delegation_question=delegation_question,
            memory_chars=memory_chars,
            qa_history_len=qa_history_len,
            prompt_chars=prompt_chars,
            trimmed_turns=removed,
        )

    # ------------------------------------------------------------------
    # P0-A TTS streaming: /v1/text/chat with stream=true (NDJSON frames)
    # ------------------------------------------------------------------

    async def _handle_text_chat_streaming(
        self,
        request: web.Request,
        state: SessionState,
        payload: dict[str, Any],
        *,
        client: AsyncOpenAI,
        model_name: str,
        interaction_mode: str,
        session_id: str,
        t_start: float,
        frames: list[dict[str, Any]] | None = None,
    ) -> web.StreamResponse:
        """Serve ``POST /v1/text/chat`` with ``stream: true``.

        Each response line is a JSON object (``application/x-ndjson``):
          * ``{"type": "decision", "decision": "...", "delegation_question": ...}``
            — emitted as soon as the first complete decision marker is seen
            (normally the first frame: ``</silence>`` / ``</response>`` first).
          * ``{"type": "content", "token": "..."}`` — one per content delta
            while the decision is ``response`` (never for silence/delegation).
          * ``{"type": "done", ...}`` — final frame with usage / full text.
          * ``{"type": "error", ...}`` — on an internal failure so the caller
            can fail open to the non-streaming path instead of hanging.

        The session lock is held for the whole stream, mirroring the
        non-streaming path (which also holds it across the full model call).
        """
        stream_resp = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "application/x-ndjson; charset=utf-8",
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )
        await stream_resp.prepare(request)
        async with state.lock:
            try:
                async for frame in self._stream_text_payload_frames(
                    state,
                    payload,
                    client=client,
                    model_name=model_name,
                    interaction_mode=interaction_mode,
                    frames=frames,
                ):
                    await stream_resp.write(
                        (json.dumps(frame, ensure_ascii=False) + "\n").encode("utf-8")
                    )
            except web.HTTPException:
                raise
            except Exception as exc:
                LOGGER.exception("[tts-stream] streaming text chat failed")
                emit_event(
                    "webinfer",
                    "infer_error",
                    level="error",
                    session_id=session_id,
                    extra={"error_type": type(exc).__name__, "path": "text_chat_stream"},
                )
                error_frame = {
                    "type": "error",
                    "error": str(exc)[:200],
                    "error_type": type(exc).__name__,
                }
                try:
                    await stream_resp.write(
                        (json.dumps(error_frame, ensure_ascii=False) + "\n").encode("utf-8")
                    )
                except Exception:
                    pass
        await stream_resp.write_eof()
        emit_event(
            "webinfer",
            "webinfer_request",
            level="info",
            session_id=session_id,
            latency_ms=round((time.perf_counter() - t_start) * 1000),
            extra={"model": model_name, "path": "text_chat_stream"},
        )
        return stream_resp

    async def _stream_text_payload_frames(
        self,
        state: SessionState,
        payload: dict[str, Any],
        *,
        client: AsyncOpenAI | None = None,
        model_name: str | None = None,
        interaction_mode: str = "live",
        frames: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Async generator of NDJSON frames for the streaming text path.

        Mirror of :meth:`_handle_text_payload` (memory recall, composed
        system prompt, prompt guard, model call) with ``stream=True``.
        The decision frame is derived with :func:`parse_model_decision` over
        the accumulated prefix so decision semantics are unchanged; content
        deltas stream as ``content`` frames; the final ``done`` frame carries
        usage + full text and updates ``qa_history`` exactly like the
        non-streaming path.
        """
        client = client or self.main_client
        model_name = model_name or self.config.main_model

        pre_messages = list(payload.get("messages") or [])
        last_user_text = ""
        for m in reversed(pre_messages):
            if m.get("role") == "user" and isinstance(m.get("content"), str):
                last_user_text = m["content"]
                break
        try:
            await self._memory_recall(state, last_user_text)
        except Exception as exc:
            LOGGER.warning("memory_recall failed for %s: %s", state.session_id, exc)

        api_messages = list(payload.get("messages") or [])
        composed_system = (
            self._build_memory_prompt(
                state,
                include_decision_tokens=interaction_mode != "call",
                interaction_mode=interaction_mode,
            )
            or ""
        ).strip()

        caller_messages = [dict(m) for m in api_messages if m.get("role") != "system"]
        if frames:
            # Live visual round (spec draft-live-visual-cb.md): the final user
            # turn carries the current utterance + the image frames; history
            # turns stay text-only (frames never enter persistent history).
            http_messages = compose_live_visual_messages(
                composed_system=composed_system,
                last_user_text=last_user_text,
                frames=frames,
                caller_messages=caller_messages,
            )
        elif composed_system:
            http_messages = [{"role": "system", "content": composed_system}, *caller_messages]
        else:
            http_messages = caller_messages

        max_total_chars = _compute_prompt_guard_max_chars(self.config.main_ctx_tokens)
        if max_total_chars > 0:
            http_messages, removed = _trim_messages_to_ctx(
                [dict(m) for m in http_messages], max_total_chars
            )
        else:
            removed = 0

        generation_kwargs = self._main_generation_kwargs(payload)
        generation_kwargs["stream"] = True
        generation_kwargs["stream_options"] = {"include_usage": True}
        response = await client.chat.completions.create(
            model=model_name,
            messages=http_messages,
            **generation_kwargs,
        )

        raw_parts: list[str] = []
        pending = ""
        raw_prefix = ""  # accumulated text up to and including the FIRST marker
        raw_tail = ""  # content after the first marker (watched for a late delegation tag)
        decision: str | None = None
        clean_text = ""
        full_text = ""
        delegation_question: str | None = None
        usage = None
        async for chunk in response:
            usage = _extract_stream_usage(chunk) or usage
            delta = _extract_stream_delta(chunk)
            if not delta:
                continue
            raw_parts.append(delta)
            if decision is None:
                pending += delta
                if _find_first_decision_marker(pending) is None:
                    continue
                decision, clean_text, delegation_question = parse_model_decision(pending)
                raw_prefix = pending
                yield {
                    "type": "decision",
                    "decision": decision,
                    "delegation_question": delegation_question,
                }
                if decision == "response" and clean_text:
                    raw_tail += clean_text
                    full_text += clean_text
                    yield {"type": "content", "token": clean_text}
                pending = ""
            elif decision == "response":
                raw_tail += delta
                full_text += delta
                if _find_first_decision_marker(raw_tail) is not None:
                    # Late decision tag (taught delegation format
                    # ``</response> <note> </delegation> <question>``, or a
                    # model correction to </not-for-me>). Re-judge the whole
                    # turn — the question / non-addressed body must never
                    # stream as content. Content that already streamed for
                    # the note cannot be recalled here; the consumer drops
                    # its buffered remainder on the corrected frame.
                    decision, _clean, delegation_question = parse_model_decision(
                        raw_prefix + raw_tail
                    )
                    full_text = ""
                    yield {
                        "type": "decision",
                        "decision": decision,
                        "delegation_question": delegation_question,
                        "corrected": True,
                    }
                else:
                    yield {"type": "content", "token": delta}
            elif decision == "delegation":
                # The delegated question may arrive in later deltas; keep
                # parsing so the done frame carries the complete question.
                raw_tail += delta
                _d, _c, delegation_question = parse_model_decision(raw_prefix + raw_tail)

        raw_text = "".join(raw_parts)
        if decision is None:
            # Stream ended without a complete decision marker: fail open
            # through the unified parser (marker-less output => response).
            decision, clean_text, delegation_question = parse_model_decision(pending)
            yield {
                "type": "decision",
                "decision": decision,
                "delegation_question": delegation_question,
            }
            if decision == "response" and clean_text:
                full_text += clean_text
                yield {"type": "content", "token": clean_text}

        # The done frame mirrors the unified parser on the FULL raw text: a
        # delegation tag ANYWHERE wins (late tag after </response>), and a
        # delegation / not-for-me turn never speaks anything.
        final_decision, _final_clean, final_delegation_question = parse_model_decision(raw_text)
        if final_decision in ("delegation", "not-for-me"):
            decision = final_decision
            delegation_question = final_delegation_question
            full_text = ""

        usage_dict = usage.model_dump() if getattr(usage, "model_dump", None) else None
        # Keep qa_history consistent with the non-streaming text path
        # (decision + clean text, "" for silence / delegation).
        self._update_text_qa_history(state, api_messages, full_text, decision)
        memory_chars = len(composed_system)
        qa_history_len = len(state.memory_state.get("qa_history", []))
        prompt_chars = _estimate_messages_chars(http_messages)
        yield {
            "type": "done",
            "decision": decision,
            "delegation_question": delegation_question,
            "full_text": full_text,
            "raw_text": raw_text,
            "usage": usage_dict,
            "model": self.config.adapter_model,
            "raw_model": model_name,
            "memory_chars": memory_chars,
            "qa_history_len": qa_history_len,
            "prompt_chars": prompt_chars,
            "trimmed_turns": removed,
        }

    async def handle_chat_completions(self, request: web.Request) -> web.Response:
        """Handle the multimodal chat-completions endpoint."""
        payload = await _read_json(request)
        session_id = _request_session_id(request, payload)
        requested_model = payload.get("model")
        interaction_mode = _normalize_interaction_mode(payload.get("interaction_mode"))
        client, model_name = self._resolve_backend(requested_model)
        state = self.get_session(session_id)
        t_start = time.perf_counter()
        async with state.lock:
            try:
                result = await self._handle_chat_payload(
                    state,
                    payload,
                    request,
                    client=client,
                    model_name=model_name,
                    interaction_mode=interaction_mode,
                )
            except web.HTTPException:
                raise
            except Exception as exc:
                LOGGER.exception("chat completion failed")
                emit_event(
                    "webinfer",
                    "infer_error",
                    level="error",
                    session_id=session_id,
                    extra={"error_type": type(exc).__name__, "path": "chat_completions"},
                )
                return _openai_error_response(str(exc), status=502)
        emit_event(
            "webinfer",
            "webinfer_request",
            level="info",
            session_id=session_id,
            latency_ms=round((time.perf_counter() - t_start) * 1000),
            extra={"model": model_name, "path": "chat_completions"},
        )
        return web.json_response(result)

    async def _handle_chat_payload(
        self,
        state: SessionState,
        payload: dict[str, Any],
        request: web.Request,
        *,
        client: AsyncOpenAI | None = None,
        model_name: str | None = None,
        interaction_mode: str = "live",
    ) -> dict[str, Any]:
        client = client or self.main_client
        model_name = model_name or self.config.main_model
        t_start = time.perf_counter()
        messages = payload.get("messages") or []
        if not isinstance(messages, list):
            raise web.HTTPBadRequest(text="messages must be a list")

        ctx = SimpleNamespace()
        ctx.t_start = t_start
        ctx.interaction_mode = interaction_mode
        await self._chat_payload_resolve_frames(
            state, request, payload, messages, client, model_name, ctx
        )
        if ctx.forward_result is not None:
            return ctx.forward_result

        await self._chat_payload_advance_chunk(state, ctx)
        self._chat_payload_append_turn(state, ctx)
        await self._chat_payload_build_and_infer(
            state, payload, client, model_name, messages, ctx, interaction_mode=interaction_mode
        )
        return self._chat_payload_finalize(state, model_name, ctx)

    async def _chat_payload_resolve_frames(
        self,
        state: SessionState,
        request: web.Request,
        payload: dict[str, Any],
        messages: list[dict[str, Any]],
        client: AsyncOpenAI,
        model_name: str,
        ctx: SimpleNamespace,
    ) -> None:
        """Resolve image references -> frame paths and parse/format time ranges."""
        image_refs = _extract_all_image_refs(messages, request, payload)
        if not image_refs:
            ctx.forward_result = await self._forward_text_only(
                payload, client=client, model_name=model_name
            )
            return

        turn_count = len(state.predictions) + 1
        raw_prompt_text = _extract_user_prompt_text(messages)
        prompt_text = _strip_time_range_from_text(raw_prompt_text)

        # Resolve time ranges for all images
        incoming_time_ranges = _extract_time_ranges_from_request(request, payload)
        if not incoming_time_ranges:
            single = _extract_time_range_from_request(request, payload)
            if single is None:
                single = _extract_time_range_from_text(raw_prompt_text)
            if single:
                incoming_time_ranges = [single]
        time_ranges: list[str] = []
        for i in range(len(image_refs)):
            if i < len(incoming_time_ranges) and incoming_time_ranges[i]:
                time_ranges.append(incoming_time_ranges[i])
            else:
                time_ranges.append(self._time_range_for_frame(state.frame_count + i))
        time_range = _format_turn_time_range(time_ranges)

        image_paths = [self._resolve_frame_ref(ref, state) for ref in image_refs]
        LOGGER.info(
            "[%s] turn=%d frames=%d(+%d) chunk=%d time=%s prompt=%r",
            state.session_id,
            turn_count,
            state.frame_count,
            len(image_refs),
            state.chunk_index,
            time_range,
            _short(prompt_text, 80),
        )

        query_text = self._update_query_state(state, prompt_text, time_ranges[0])

        ctx.turn_count = turn_count
        ctx.time_ranges = time_ranges
        ctx.time_range = time_range
        ctx.image_paths = image_paths
        ctx.query_text = query_text
        ctx.forward_result = None

    async def _chat_payload_advance_chunk(self, state: SessionState, ctx: SimpleNamespace) -> None:
        """Commit due async summaries, then handle chunk boundary / qa archive / flush / carry-over."""
        await self._commit_required_async_summaries(
            state,
            state.turn_count,
            non_blocking=True,
        )

        if self.config.chunk > 0 and state.current_chunk["turn_count"] >= self.config.chunk:
            self._execute_pending_qa_archive(state)
            carry_response_records = []
            if self.config.keep_qa_history and state.current_query_text:
                qa_cutoff = float("inf")
                if (
                    self._async_summary_enabled()
                    and state.async_summary_segment["frame_time_ranges"]
                ):
                    qa_cutoff = _parse_start_second(
                        state.async_summary_segment["frame_time_ranges"][0]
                    )
                    carry_response_records = [
                        (tr, payload)
                        for tr, payload in state.current_chunk["response_records"]
                        if _parse_start_second(tr) >= qa_cutoff
                    ]
                archive_chunk_response_records(
                    state.current_chunk,
                    state.memory_state,
                    state.current_query_text,
                    state.query_start_time,
                    chunk_index=state.chunk_index,
                    before_time_sec=qa_cutoff,
                )
            await self._flush_chunk(state, use_async_summary=self._async_summary_enabled())
            if self._async_summary_enabled() and state.async_summary_segment["turn_count"] > 0:
                carry = copy.deepcopy(state.async_summary_segment)
                carry_frames = carry["frame_count"]
                carry_turns = carry["turn_count"]
                carry["frame_count"] = 0
                carry["turn_count"] = 0
                carry["response_records"] = carry_response_records
                carry["api_msg_cache"] = []
                state.current_chunk = carry
                LOGGER.info(
                    "[%s] carried over %d unsummarized turn(s), %d frame(s) to new chunk",
                    state.session_id,
                    carry_turns,
                    carry_frames,
                )
            else:
                state.current_chunk = reset_chunk_state()
            state.chunk_index += 1
            state.query_in_current_chunk = bool(ctx.query_text)

    def _chat_payload_append_turn(self, state: SessionState, ctx: SimpleNamespace) -> None:
        """Append frames to the chunk, bump counters, and append user/async-summary messages."""
        for tr, ip in zip(ctx.time_ranges, ctx.image_paths):
            state.frame_count += 1
            state.current_chunk["image_paths"].append(str(ip))
            state.current_chunk["frame_time_ranges"].append(tr)
            state.current_chunk["summarizer_frame_cache"].append({"path": str(ip)})
            state.current_chunk["frame_count"] += 1

        state.turn_count += 1
        state.current_chunk["turn_count"] += 1

        user_message = self._build_internal_user_message(
            time_ranges=ctx.time_ranges,
            image_paths=[str(ip) for ip in ctx.image_paths],
            query_text=ctx.query_text,
        )
        state.current_chunk["messages"].append(user_message)
        if self._async_summary_enabled():
            self._append_async_summary_user_message(
                state,
                time_ranges=ctx.time_ranges,
                image_paths=[str(ip) for ip in ctx.image_paths],
                query_text=ctx.query_text,
            )
        ctx.user_message = user_message

    def _is_forced_silence(self, state: SessionState, interaction_mode: str) -> bool:
        """Decide whether this turn is a forced-silence (no-inference) turn.

        Forced silence only applies to the ``live`` mode: it suppresses model
        inference when no user query is pending, so the assistant stays quiet
        between events. ``call`` (direct voice-to-text) and ``jarvis``
        (wake-word driven) modes never force silence -- they drive their own
        turn flow and always want a real model response (issue #45).
        """
        if interaction_mode != "live":
            return False
        return self.config.force_silence_before_query and not state.current_query_text

    async def _chat_payload_build_and_infer(
        self,
        state: SessionState,
        payload: dict[str, Any],
        client: AsyncOpenAI,
        model_name: str,
        messages: list[dict[str, Any]],
        ctx: SimpleNamespace,
        *,
        interaction_mode: str = "live",
    ) -> None:
        """Assemble the model input and run the main-model call (incl. forced-silence branch)."""
        # F-3 P1b: fire Local-Wiki recall on the multimodal path too, so the
        # [Local Wiki] section is mode-consistent (text path already does this).
        last_user_text = ""
        for m in reversed(messages):
            if m.get("role") == "user" and isinstance(m.get("content"), str):
                last_user_text = m["content"]
                break
        if last_user_text:
            try:
                await self._memory_recall(state, last_user_text)
            except Exception as exc:
                LOGGER.warning("memory_recall failed for %s: %s", state.session_id, exc)
        turn_input_record = {
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

        is_forced_silence = self._is_forced_silence(state, interaction_mode)
        # call / jarvis must never teach the model the decision-token framework.
        include_decision_tokens = interaction_mode != "call"
        inference_start = None
        inference_time = 0.0
        chunk_start_model_input_path = None
        turn_model_input_record = None
        model_input_record = None
        t_prompt_build_start = 0.0
        t_prompt_build_end = 0.0
        t_inference_end = 0.0

        if is_forced_silence:
            generated_text = "</silence>"
            raw_text = ""
            usage = None
            turn_model_input_record = build_model_input_record(
                chunk_index=state.chunk_index,
                messages=state.current_chunk["messages"],
                frame_count=state.current_chunk["frame_count"],
                inference_skipped=True,
                skip_reason="force_silence_before_query",
                image_paths=state.current_chunk["image_paths"],
                frame_time_ranges=state.current_chunk["frame_time_ranges"],
            )
            if self.config.save_model_inputs:
                model_input_record = turn_model_input_record
        else:
            t_prompt_build_start = time.perf_counter()
            internal_messages, prefix_content = self._build_main_internal_messages(state)
            api_messages = self._build_cached_api_messages(state, internal_messages)
            generation_kwargs = self._main_generation_kwargs(payload)
            http_messages = self._build_main_http_messages(
                api_messages,
                session_state=state,
                include_decision_tokens=include_decision_tokens,
                interaction_mode=interaction_mode,
            )
            turn_model_input_record = build_model_input_record(
                chunk_index=state.chunk_index,
                messages=http_messages,
                frame_count=state.current_chunk["frame_count"],
                model=model_name,
                generation_kwargs=generation_kwargs,
                image_paths=state.current_chunk["image_paths"],
                frame_time_ranges=state.current_chunk["frame_time_ranges"],
                prefix_content=prefix_content,
            )
            if self.config.save_model_inputs:
                model_input_record = turn_model_input_record
            chunk_start_model_input_path = self._maybe_save_chunk_start_model_input(
                state,
                ctx.turn_count,
                ctx.time_range,
                turn_model_input_record,
            )
            t_prompt_build_end = time.perf_counter()
            inference_start = time.time()
            t_infer_start = time.perf_counter()
            raw_text, usage = await self._call_main_model(
                payload,
                api_messages,
                client=client,
                model_name=model_name,
                session_state=state,
                generation_kwargs=generation_kwargs,
                http_messages=http_messages,
            )
            inference_time = time.time() - inference_start
            t_inference_end = time.perf_counter()
            LOGGER.info(
                "latency[infer]: model_call_ms=%.1f",
                (t_inference_end - t_infer_start) * 1000,
            )
            generated_text = (
                normalize_model_output(raw_text)
                if self.config.normalize_output
                else (raw_text or "").strip()
            )

        ctx.turn_input_record = turn_input_record
        ctx.is_forced_silence = is_forced_silence
        ctx.inference_time = inference_time
        ctx.chunk_start_model_input_path = chunk_start_model_input_path
        ctx.turn_model_input_record = turn_model_input_record
        ctx.model_input_record = model_input_record
        ctx.generated_text = generated_text
        ctx.raw_text = raw_text
        ctx.usage = usage
        ctx.t_prompt_build_start = t_prompt_build_start
        ctx.t_prompt_build_end = t_prompt_build_end
        ctx.t_inference_end = t_inference_end

    def _chat_payload_finalize(
        self, state: SessionState, model_name: str, ctx: SimpleNamespace
    ) -> dict[str, Any]:
        """Parse response, assemble prediction/timing, and package the final result."""
        self._execute_pending_qa_archive(state)

        response_payload = extract_response_payload(ctx.generated_text)
        if response_payload and state.current_query_text:
            state.current_chunk["response_records"].append((ctx.time_range, response_payload))

        state.current_chunk["messages"].append({"role": "assistant", "content": ctx.generated_text})
        if self._async_summary_enabled():
            state.async_summary_segment["messages"].append(
                {"role": "assistant", "content": ctx.generated_text}
            )
            self._submit_async_summary_if_needed(state)

        turn_output_record = {}
        if ctx.is_forced_silence:
            turn_output_record["inference_skipped"] = True
            turn_output_record["skip_reason"] = "force_silence_before_query"

        t_end = time.perf_counter()
        total_time = t_end - ctx.t_start

        prediction = {
            "turn": ctx.turn_count,
            "time_range": ctx.time_range,
            "query": ctx.query_text,
            "input": ctx.turn_input_record,
            "output": turn_output_record,
            "prediction": ctx.generated_text,
            "total_time": round(total_time, 3),
            "inference_time": round(ctx.inference_time, 3),
        }
        if ctx.model_input_record is not None:
            ctx.turn_input_record["model_input"] = ctx.model_input_record
        if ctx.chunk_start_model_input_path:
            prediction["chunk_start_model_input_path"] = ctx.chunk_start_model_input_path
        if ctx.raw_text and ctx.raw_text.strip() != ctx.generated_text:
            prediction["raw_prediction"] = ctx.raw_text
        state.predictions.append(prediction)

        t_end = time.perf_counter()
        adapter_timing = {
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

        if not ctx.is_forced_silence:
            LOGGER.info(
                "[%s] turn=%d timing: total=%.1fms pre=%.1fms prompt_build=%.1fms vllm=%.1fms post=%.1fms",
                state.session_id,
                ctx.turn_count,
                adapter_timing["adapter_total_ms"],
                adapter_timing["pre_inference_ms"],
                adapter_timing["prompt_build_ms"],
                adapter_timing["vllm_inference_ms"],
                adapter_timing["post_process_ms"],
            )
        else:
            LOGGER.info(
                "[%s] turn=%d timing: total=%.1fms (forced silence, inference skipped)",
                state.session_id,
                ctx.turn_count,
                adapter_timing["adapter_total_ms"],
            )

        decision, _, delegation_question = parse_model_decision(ctx.raw_text or "")
        result = _chat_completion_response(
            model=self.config.adapter_model,
            content=strip_decision_tokens(ctx.generated_text),
            usage=ctx.usage,
            raw_model=model_name,
            raw_text=ctx.raw_text,
            decision=decision,
            delegation_question=delegation_question,
        )
        result["streamingharness"]["timing"] = adapter_timing
        summarizer_timing = {}
        if state.mid_term_history:
            last_mid = state.mid_term_history[-1]
            summarizer_timing["last_mid_term_ms"] = round(
                last_mid.get("inference_time", 0) * 1000, 1
            )
            summarizer_timing["last_mid_term_chunk"] = last_mid.get("chunk_index")
            if last_mid.get("barrier_wait_time") is not None:
                summarizer_timing["barrier_wait_ms"] = round(
                    last_mid["barrier_wait_time"] * 1000, 1
                )
        if state.long_term_history:
            last_long = state.long_term_history[-1]
            summarizer_timing["last_long_term_ms"] = round(
                last_long.get("inference_time", 0) * 1000, 1
            )
        result["streamingharness"]["summarizer_timing"] = summarizer_timing
        result["streamingharness"]["memory"] = {
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
        return result

    async def _forward_text_only(
        self,
        payload: dict[str, Any],
        *,
        client: AsyncOpenAI | None = None,
        model_name: str | None = None,
    ) -> dict[str, Any]:
        client = client or self.main_client
        model_name = model_name or self.config.main_model
        generation_kwargs = self._main_generation_kwargs(payload)
        response = await client.chat.completions.create(
            model=model_name,
            messages=payload.get("messages") or [],
            **generation_kwargs,
        )
        raw_text = response.choices[0].message.content if response.choices else ""
        usage = response.usage.model_dump() if getattr(response, "usage", None) else None
        decision, _, delegation_question = parse_model_decision(raw_text or "")
        return _chat_completion_response(
            model=self.config.adapter_model,
            content=raw_text or "",
            usage=usage,
            raw_model=model_name,
            raw_text=raw_text or "",
            decision=decision,
            delegation_question=delegation_question,
        )

    def _time_range_for_frame(self, frame_index: int) -> str:
        start = frame_index * self.config.frame_seconds
        return f"{start:.1f} seconds"

    def _resolve_frame_ref(
        self,
        image_ref: dict[str, str],
        state: SessionState,
    ) -> str:
        if image_ref.get("kind") == "path":
            return str(self._validate_local_image_path(image_ref.get("value", "")))
        if image_ref.get("kind") == "data_url":
            return self._save_base64_frame(image_ref.get("value", ""), state)
        raise web.HTTPBadRequest(text="unsupported image reference kind")

    def _save_base64_frame(self, data_url: str, state: SessionState) -> str:
        # Shared normalization (io_utils.normalize_image_b64) validates that
        # the payload is a decodable base64 image (bare or data-URI prefixed);
        # the original value is returned unchanged so memory / output records
        # keep the exact data URL the caller supplied.
        try:
            normalize_image_b64(data_url)
        except ValueError as exc:
            raise web.HTTPBadRequest(text="invalid data URL format") from exc
        state.session_frame_counter += 1
        return data_url

    def _validate_local_image_path(self, raw_path: str) -> Path:
        if not self.config.allowed_local_image_roots:
            raise web.HTTPBadRequest(text="local image paths are disabled")

        path = Path(raw_path).expanduser().resolve()
        if not path.is_file():
            raise web.HTTPBadRequest(text=f"local image path does not exist: {path}")
        if path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}:
            raise web.HTTPBadRequest(text=f"unsupported local image extension: {path.suffix}")

        for root in self.config.allowed_local_image_roots:
            root_path = Path(root).expanduser().resolve()
            try:
                path.relative_to(root_path)
                return path
            except ValueError:
                continue

        allowed = ", ".join(self.config.allowed_local_image_roots)
        raise web.HTTPBadRequest(text=f"local image path is outside allowed roots: {allowed}")

    def _update_query_state(
        self,
        state: SessionState,
        prompt_text: str,
        time_range: str,
    ) -> str | None:
        if not self.config.use_prompt_as_query:
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

    async def _call_main_model(
        self,
        inbound_payload: dict[str, Any],
        api_messages: list[dict[str, Any]],
        *,
        client: AsyncOpenAI | None = None,
        model_name: str | None = None,
        session_state: SessionState | None = None,
        generation_kwargs: dict[str, Any] | None = None,
        http_messages: list[dict[str, Any]] | None = None,
    ) -> tuple[str, dict[str, Any] | None]:
        client = client or self.main_client
        model_name = model_name or self.config.main_model
        generation_kwargs = generation_kwargs or self._main_generation_kwargs(inbound_payload)
        max_total_chars = _compute_prompt_guard_max_chars(self.config.main_ctx_tokens)
        api_messages = http_messages or self._build_main_http_messages(
            api_messages,
            session_state=session_state,
            max_total_chars=max_total_chars,
        )
        response = await client.chat.completions.create(
            model=model_name,
            messages=api_messages,
            **generation_kwargs,
        )
        raw_text = response.choices[0].message.content if response.choices else ""
        usage = response.usage.model_dump() if getattr(response, "usage", None) else None
        return raw_text or "", usage
