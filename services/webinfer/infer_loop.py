"""Main inference-loop mixin: chat endpoints, frame parsing, and main-model call.

Defines :class:`InferLoopMixin`, which carries the primary推理 loop:
``handle_text_chat`` / ``handle_chat_completions`` / ``_handle_chat_payload``
(including its five cohesive ``_chat_payload_*`` sub-steps), ``_handle_text_payload``,
frame reference parsing, and the main-model call previously on ``StreamingInferAdapter``.

Batch-2 decoupling (zero behaviour change): the frame parsing / image-reference
logic lives in :mod:`frame_parsing`, the NDJSON streaming frame protocol lives in
:mod:`stream_protocol`, and the chat-payload pure data assembly lives in
:mod:`chat_payload`. This module keeps the orchestration (which depends on
``self`` / ``state`` / ``config``) and re-exports the moved symbols so the
historical import surface (``from infer_loop import _parse_live_frames`` etc.)
stays intact.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import frame_parsing
from adapter_types import SessionState
from aiohttp import web
from chat_payload import (
    build_adapter_timing,
    build_memory_payload,
    build_prediction_dict,
    build_summarizer_timing,
    build_turn_input_record,
    is_forced_silence,
    update_query_state,
)
from frame_parsing import (
    _LIVE_FRAMES_MAX,  # noqa: F401  (re-export: tests import from infer_loop)
    _normalize_interaction_mode,
    _parse_live_frames,
)
from openai import AsyncOpenAI
from prompt_assembly import compose_live_visual_messages
from prompt_building import (
    _compute_prompt_guard_max_chars,
    _estimate_messages_chars,
    _trim_messages_to_ctx,
)
from request_parsing import (
    _extract_all_image_refs,
    _extract_last_user_text,
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
from stream_protocol import (
    _extract_stream_delta,
    _extract_stream_usage,
    _find_first_decision_marker,
    build_stream_frames,  # noqa: F401  (re-export: tests import from infer_loop)
)
from time_ranges import (
    _extract_time_range_from_text,
    _format_turn_time_range,
    _parse_start_second,
    _strip_time_range_from_text,
)

from config import reset_chunk_state

LOGGER = logging.getLogger("streaming_infer_adapter")

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
        # The last user turn may carry OpenAI list content (live visual rounds);
        # _extract_last_user_text handles str + list so the question is never
        # silently dropped when frames rebuild the final user message (audit P1-1).
        pre_messages = list(payload.get("messages") or [])
        last_user_text = _extract_last_user_text(pre_messages)
        try:
            await self._memory_recall(state, last_user_text)
        except Exception as exc:
            LOGGER.warning("memory_recall failed for %s: %s", state.session_id, exc)
        # Radio-silence (spec draft-radio-silence.md): the text path is the
        # production live round path (/v1/text/chat with frames), so command /
        # name detection + T1/T2 evaluation + the suppression gate live here
        # too. Live-only: call/jarvis are untouched (D-001 isolation).
        self._silence_process_transcript(last_user_text, interaction_mode)
        silence_action = self._silence_check_timeouts()
        is_suppressed = self._is_suppressed(interaction_mode)

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
            # max_pixels is forwarded so the frames honour the global image
            # budget instead of reaching the main model at full resolution
            # (audit P1-4).
            http_messages = compose_live_visual_messages(
                composed_system=composed_system,
                last_user_text=last_user_text,
                frames=frames,
                caller_messages=caller_messages,
                max_pixels=self.config.max_pixels,
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

        wake_round = False
        if is_suppressed:
            # 有 query 也不响应: full mute on the production live text path,
            # no model call, silence decision (spec §2).
            raw_text = ""
            usage = None
            LOGGER.info(
                "[%s] live round suppressed (radio silence); text path muted",
                state.session_id,
            )
        else:
            wake_directive = self._consume_wake_directive(interaction_mode)
            if wake_directive:
                wake_round = True
                http_messages = [{"role": "system", "content": wake_directive}, *http_messages]
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

        result = _chat_completion_response(
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
        # Radio-silence round metadata: suppressed / hint / wake ride the same
        # response so the webui can reflect the badge and play hint / wake.wav.
        silence_meta = {}
        if is_suppressed:
            silence_meta["suppressed"] = True
            silence_meta["reason"] = "radio_silence"
        if silence_action == "hint":
            silence_meta["hint"] = True
        if wake_round:
            silence_meta["wake"] = True
        if silence_meta:
            result["streamingharness"]["silence"] = silence_meta
        return result

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
        last_user_text = _extract_last_user_text(pre_messages)
        try:
            await self._memory_recall(state, last_user_text)
        except Exception as exc:
            LOGGER.warning("memory_recall failed for %s: %s", state.session_id, exc)
        # Radio-silence (spec draft-radio-silence.md): same live-only gate as
        # the non-streaming text path — command/name detection first, then
        # T1/T2, then the suppression gate (D-001 isolation for call/jarvis).
        self._silence_process_transcript(last_user_text, interaction_mode)
        silence_action = self._silence_check_timeouts()
        is_suppressed = self._is_suppressed(interaction_mode)

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
            # max_pixels is forwarded so the frames honour the global image
            # budget instead of reaching the main model at full resolution
            # (audit P1-4).
            http_messages = compose_live_visual_messages(
                composed_system=composed_system,
                last_user_text=last_user_text,
                frames=frames,
                caller_messages=caller_messages,
                max_pixels=self.config.max_pixels,
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

        wake_round = False
        if is_suppressed:
            # 有 query 也不响应 (spec §2): mute the streaming path too — emit a
            # silence decision + done frame, no model call. The webui consumer
            # (StreamingTurnConsumer) treats a silence decision as nothing to
            # speak, so the muted round is indistinguishable from a natural
            # </silence> except for the added ``silence`` metadata.
            LOGGER.info(
                "[%s] live stream round suppressed (radio silence); text path muted",
                state.session_id,
            )
            self._update_text_qa_history(state, api_messages, "", "silence")
            memory_chars = len(composed_system)
            qa_history_len = len(state.memory_state.get("qa_history", []))
            prompt_chars = _estimate_messages_chars(http_messages)
            yield {
                "type": "decision",
                "decision": "silence",
                "delegation_question": None,
            }
            silence_meta: dict[str, Any] = {
                "suppressed": True,
                "reason": "radio_silence",
            }
            if silence_action == "hint":
                silence_meta["hint"] = True
            yield {
                "type": "done",
                "decision": "silence",
                "delegation_question": None,
                "full_text": "",
                "raw_text": "",
                "usage": None,
                "model": self.config.adapter_model,
                "raw_model": model_name,
                "memory_chars": memory_chars,
                "qa_history_len": qa_history_len,
                "prompt_chars": prompt_chars,
                "trimmed_turns": removed,
                "silence": silence_meta,
            }
            return
        # Radio-silence wake: the first live round after a name/KWS wake
        # carries the one-shot addressee directive (spec §5 唤醒仪式).
        wake_directive = self._consume_wake_directive(interaction_mode)
        if wake_directive:
            wake_round = True
            http_messages = [{"role": "system", "content": wake_directive}, *http_messages]

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
        done_frame: dict[str, Any] = {
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
        if wake_round:
            # The wake directive was injected above; surface it so the webui
            # can play the wake.wav (spec §5 "我在铁驭" audio).
            done_frame["silence"] = {"wake": True}
        yield done_frame

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
                    qa_history_window=int(self.config.qa_history_window or 0),
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

    def _is_forced_silence(
        self,
        state: SessionState,
        interaction_mode: str,
        *,
        has_frames: bool = False,
    ) -> bool:
        """Decide whether this turn is a forced-silence (no-inference) turn.

        Forced silence only applies to the ``live`` mode: it suppresses model
        inference when no user query is pending, so the assistant stays quiet
        between events. ``call`` (direct voice-to-text) and ``jarvis``
        (wake-word driven) modes never force silence -- they drive their own
        turn flow and always want a real model response (issue #45).

        A live round that carries visual context (``has_frames`` — the video
        path always reaches inference with image frames) is exempt: with a
        camera/screen observation in hand the assistant must decide what to
        say (the four-state prompt still lets it emit ``</silence>`` itself),
        so the ``USE_PROMPT_AS_QUERY=0`` + ``force_silence_before_query=1``
        config combination can no longer make the video path mute every round
        (audit P1-3).
        """
        if interaction_mode == "live" and has_frames:
            return False
        # Implementation lives in chat_payload (batch-2 split, zero behaviour change).
        return is_forced_silence(
            interaction_mode,
            self.config.force_silence_before_query,
            state.current_query_text,
        )

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
        # List content is supported (audit P1-1): a multimodal caller that
        # sends the question as ``content: [{type: text, ...}]`` must still
        # trigger recall on the actual utterance.
        last_user_text = _extract_last_user_text(messages)
        if last_user_text:
            try:
                await self._memory_recall(state, last_user_text)
            except Exception as exc:
                LOGGER.warning("memory_recall failed for %s: %s", state.session_id, exc)
        # Radio-silence (spec draft-radio-silence.md): process the ASR
        # transcript for enter/wake commands, evaluate the T1/T2 timers, then
        # gate the round. Command detection runs BEFORE the suppression check
        # so an entering ("无线电静默") or waking (name) round takes effect
        # immediately. Live-only: call/jarvis are untouched (D-001 isolation).
        self._silence_process_transcript(last_user_text, interaction_mode)
        silence_action = self._silence_check_timeouts()
        is_suppressed = self._is_suppressed(interaction_mode)
        ctx.silence_action = silence_action
        ctx.is_suppressed = is_suppressed
        # Pure data assembly lives in chat_payload (batch-2 split, zero behaviour change).
        turn_input_record = build_turn_input_record(messages, ctx, state)

        is_forced_silence = self._is_forced_silence(
            state, interaction_mode, has_frames=bool(ctx.image_paths)
        )
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

        if is_forced_silence or is_suppressed:
            # Radio silence is a HARDER mute than forced silence: it cuts
            # inference AND TTS even when a user query is pending ("有 query
            # 也不响应"), while the frames already appended above keep the
            # vision context flowing (隐身观察, spec §2).
            generated_text = "</silence>"
            raw_text = ""
            usage = None
            skip_reason = "radio_silence" if is_suppressed else "force_silence_before_query"
            turn_model_input_record = build_model_input_record(
                chunk_index=state.chunk_index,
                messages=state.current_chunk["messages"],
                frame_count=state.current_chunk["frame_count"],
                inference_skipped=True,
                skip_reason=skip_reason,
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
            # Radio-silence wake: the first live round after a name/KWS wake
            # carries a one-shot addressee directive so the model replies
            # (</response>) instead of treating the wake utterance as
            # not-for-me / silence (spec §5 唤醒仪式). The round also surfaces
            # ``silence.wake`` in the result so the webui can play wake.wav.
            wake_directive = self._consume_wake_directive(interaction_mode)
            if wake_directive:
                ctx.wake_from_silence = True
                http_messages = [{"role": "system", "content": wake_directive}, *http_messages]
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
        if getattr(ctx, "is_suppressed", False):
            turn_output_record["inference_skipped"] = True
            turn_output_record["skip_reason"] = "radio_silence"

        t_end = time.perf_counter()
        total_time = t_end - ctx.t_start

        # Pure data assembly lives in chat_payload (batch-2 split, zero behaviour change).
        prediction = build_prediction_dict(ctx, turn_output_record, total_time)
        state.predictions.append(prediction)

        t_end = time.perf_counter()
        adapter_timing = build_adapter_timing(ctx, t_end)

        if getattr(ctx, "is_suppressed", False):
            LOGGER.info(
                "[%s] turn=%d timing: total=%.1fms (radio silence, inference skipped)",
                state.session_id,
                ctx.turn_count,
                adapter_timing["adapter_total_ms"],
            )
        elif not ctx.is_forced_silence:
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
        # Pure data assembly lives in chat_payload (batch-2 split, zero behaviour change).
        result["streamingharness"]["summarizer_timing"] = build_summarizer_timing(state)
        result["streamingharness"]["memory"] = build_memory_payload(state)
        # Radio-silence round metadata (spec draft-radio-silence.md): the
        # suppressed flag lets the webui reflect the 静默 badge per round; the
        # hint / wake flags ride the same response so webui can play the
        # pre-recorded "仍在静默中" hint or the wake.wav ("我在铁驭").
        silence_meta = {}
        if getattr(ctx, "is_suppressed", False):
            silence_meta["suppressed"] = True
            silence_meta["reason"] = "radio_silence"
        if getattr(ctx, "silence_action", None) == "hint":
            silence_meta["hint"] = True
        if getattr(ctx, "wake_from_silence", False):
            silence_meta["wake"] = True
        if silence_meta:
            result["streamingharness"]["silence"] = silence_meta
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
        # Implementation lives in frame_parsing (batch-2 split, zero behaviour change).
        return frame_parsing._time_range_for_frame(frame_index, self.config.frame_seconds)

    def _resolve_frame_ref(
        self,
        image_ref: dict[str, str],
        state: SessionState,
    ) -> str:
        # Implementation lives in frame_parsing (batch-2 split, zero behaviour change).
        return frame_parsing._resolve_frame_ref(
            image_ref, state, self.config.allowed_local_image_roots
        )

    def _save_base64_frame(self, data_url: str, state: SessionState) -> str:
        # Implementation lives in frame_parsing (batch-2 split, zero behaviour change).
        return frame_parsing._save_base64_frame(data_url, state)

    def _validate_local_image_path(self, raw_path: str) -> Path:
        # Implementation lives in frame_parsing (batch-2 split, zero behaviour change).
        return frame_parsing._validate_local_image_path(
            raw_path, self.config.allowed_local_image_roots
        )

    def _update_query_state(
        self,
        state: SessionState,
        prompt_text: str,
        time_range: str,
    ) -> str | None:
        # Implementation lives in chat_payload (batch-2 split, zero behaviour change).
        return update_query_state(state, prompt_text, time_range, self.config.use_prompt_as_query)

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
