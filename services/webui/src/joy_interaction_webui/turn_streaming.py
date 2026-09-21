"""Shared P0-A streaming turn consumer (webinfer NDJSON -> sentence TTS).

Extracted from ``jarvis_mode.JarvisStateMachine._send_to_llm_streaming`` so
the jarvis voice dialog and the future live dialog share one implementation
(spec ``live-interaction-layer.md`` §4.3). The extraction is a pure
move/re-composition: jarvis behavior must be byte-for-byte identical.

Responsibilities of :class:`StreamingTurnConsumer`:

  * POST ``stream: true`` to webinfer's ``/v1/text/chat`` and consume the
    NDJSON frames:

      - ``decision`` frame — determines silence / response / delegation /
        not-for-me (same semantics as the non-streaming path; silence and
        not-for-me never speak);
      - ``content`` frames (decision == ``response``) — fed into a
        :class:`SentenceBuffer`; each flushed sentence is handed to the
        injected ``on_sentence`` callback (the caller synthesizes TTS and
        pushes ``tts_sentence`` to the browser);
      - ``done`` frame — full text for history / transcript broadcast;
      - ``error`` frame — treated as a transport error.

  * Fail-open (never lose the reply): if the stream errors before any
    frame, the consumer returns ``needs_non_streaming_retry=True`` so the
    caller can re-run the single-shot path; if it errors after a decision,
    the buffered remainder is flushed (spoken) anyway.

  * Cancellation: the injected ``is_cancelled`` callable is checked between
    frames (barge-in / exit word semantics). A cancelled turn returns
    ``cancelled=True`` and the caller skips the broadcast.

The caller owns the turn semantics around the consumer (history
composition, ``_finish_llm_turn`` / delegation routing, reply_epoch
tagging); this module only consumes the stream and produces flushed
sentences + the final reply text/decision.
"""

from __future__ import annotations

import json as _json
import logging
from collections.abc import Callable
from dataclasses import dataclass

from .turn_controller import SentenceBuffer

logger = logging.getLogger("joyai.turn_streaming")


@dataclass
class StreamingTurnResult:
    """Outcome of one streaming turn consumption."""

    full_response: str
    decision: str
    delegation_question: str | None
    cancelled: bool
    reply_session: int
    sentence_count: int
    needs_non_streaming_retry: bool = False
    #: What the DECISION PARSER saw (webinfer's ``done`` frame ``raw_text``),
    #: i.e. the model output before user-facing stripping. Distinct from
    #: ``full_response`` (the cleaned body): a ``not-for-me`` round has an empty
    #: body but a non-empty raw output, and an "empty output" round has both
    #: empty. The decision record (#156) must measure the former, not the body,
    #: or a real non-addressed judgement reads as "the model emitted nothing".
    #: Defaults to ``""``; callers that lack it should pass ``full_response``.
    raw_text: str = ""


class StreamingTurnConsumer:
    """Consume webinfer's NDJSON LLM stream, flushing sentences to TTS.

    Pure P0-A streaming logic — no jarvis/live knowledge. All jarvis hooks
    (TTS synthesis + ``tts_sentence`` push, stream cancellation, non-streaming
    fail-open retry) are injected as callables so both modes reuse it.
    """

    def __init__(
        self,
        *,
        endpoint_url: str,
        model: str,
        system_prompt: str,
        history_snapshot: list,
        max_tokens: int = 200,
        temperature: float = 0.7,
        timeout_s: float = 30.0,
        max_sentence_chars: int = 80,
        comma_split_enabled: bool = True,
        on_sentence: Callable[[str, int, int], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
        stream_logger: logging.Logger | None = None,
        frames: list | None = None,
        on_silence_wake: Callable[[], None] | None = None,
    ) -> None:
        self.endpoint_url = endpoint_url
        self.model = model
        self.system_prompt = system_prompt
        self.history_snapshot = list(history_snapshot)
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.timeout_s = timeout_s
        self.max_sentence_chars = max_sentence_chars
        self.comma_split_enabled = comma_split_enabled
        self.on_sentence = on_sentence
        self.is_cancelled = is_cancelled
        self._log = stream_logger or logger
        # Radio-silence wake (spec §5): called when webinfer's done frame
        # carries ``silence: {wake: True}`` — the live caller plays wake.wav.
        self.on_silence_wake = on_silence_wake
        # Live visual path (spec live-visual-cb.md §3 层 2): optional
        # ``[{image_b64, ts_ms}]`` frames carried on this round's request.
        # ``None`` (jarvis / text-only live) keeps the request body unchanged.
        self.frames = list(frames) if frames else None

    def build_messages(self, text: str) -> list[dict]:
        """Compose the OpenAI-style message list (system + history + user).

        Mirrors the jarvis non-streaming composition exactly (bounded
        conversation history, current user text appended last).
        """
        messages: list[dict] = [{"role": "system", "content": self.system_prompt}]
        for role, content in self.history_snapshot:
            messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": text})
        return messages

    async def consume(
        self,
        text: str,
        *,
        interaction_mode: str,
        reply_session: int = 0,
    ) -> StreamingTurnResult:
        """POST ``stream: true`` and consume frames; returns the turn outcome.

        ``reply_session`` is the caller-assigned per-reply session id used to
        tag every flushed sentence (``tts_sentence`` session) and the logs;
        it is identical to the jarvis ``_tts_reply_seq`` value.

        The caller decides what to do with the result (retry non-streaming on
        ``needs_non_streaming_retry``, skip broadcast on ``cancelled``, or
        finish the turn with the returned text/decision).
        """
        import httpx

        messages = self.build_messages(text)
        history_turns = len(self.history_snapshot) // 2

        sentence_buffer = SentenceBuffer(
            max_sentence_chars=self.max_sentence_chars,
            comma_split_enabled=self.comma_split_enabled,
        )
        seq = 0
        full_response = ""
        decision = "silence"
        delegation_question = None
        frames_received = False
        decision_received = False
        done_received = False
        cancelled = False
        # #156: webinfer's ``done`` frame carries ``raw_text`` — the model
        # output the decision parser actually saw, before the server stripped
        # its special tokens. Kept separately from ``full_response`` because a
        # not-for-me / silence round has an empty body but (usually) a
        # non-empty raw output; measuring only the body would report those
        # rounds as "the model emitted nothing".
        raw_text = ""

        self._log.info(
            "[tts-stream] LLM stream start: '%s' (session=%d, history_turns=%d)",
            text,
            reply_session,
            history_turns,
        )

        try:
            request_body = {
                "model": self.model,
                "messages": messages,
                "max_tokens": self.max_tokens,
                "temperature": self.temperature,
                "interaction_mode": interaction_mode,
                "stream": True,
            }
            if self.frames is not None:
                request_body["frames"] = self.frames
            async with (
                httpx.AsyncClient(timeout=self.timeout_s) as client,
                client.stream(
                    "POST",
                    self.endpoint_url,
                    json=request_body,
                ) as resp,
            ):
                if resp.status_code != 200:
                    raise RuntimeError(
                        f"webinfer stream HTTP {resp.status_code}: {await resp.aread()!r}"[:200]
                    )
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    frames_received = True
                    try:
                        frame = _json.loads(line)
                    except Exception as exc:
                        self._log.warning("[tts-stream] skipping malformed frame: %s", exc)
                        continue
                    ftype = frame.get("type")
                    if self.is_cancelled and self.is_cancelled():
                        self._log.info(
                            "[tts-stream] cancelled mid-stream (session=%d); stopping",
                            reply_session,
                        )
                        cancelled = True
                        break
                    if ftype == "decision":
                        if decision_received and frame.get("decision") in (
                            "delegation",
                            "not-for-me",
                        ):
                            # Corrected decision: the taught delegation
                            # format is ``</response> <note> </delegation>
                            # <question>``, so a provisional response is
                            # re-judged once the delegation tag arrives; a
                            # late ``</not-for-me>`` correction is handled
                            # the same way. Drop buffered note content — a
                            # delegation / not-for-me must not be spoken as
                            # TTS.
                            sentence_buffer = SentenceBuffer(
                                max_sentence_chars=self.max_sentence_chars,
                                comma_split_enabled=self.comma_split_enabled,
                            )
                            full_response = ""
                            self._log.info(
                                "[tts-stream] corrected to %s (session=%d)",
                                frame.get("decision"),
                                reply_session,
                            )
                        decision_received = True
                        decision = frame.get("decision") or "silence"
                        delegation_question = frame.get("delegation_question")
                        self._log.info(
                            "[tts-stream] decision=%s (session=%d)",
                            decision,
                            reply_session,
                        )
                    elif ftype == "content":
                        token = frame.get("token") or ""
                        if not token:
                            continue
                        # Content only accumulates while the decision is
                        # response (delegation question / silence
                        # whitespace never reaches the sentence buffer).
                        if decision != "response":
                            continue
                        full_response += token
                        sentence = sentence_buffer.add_token(token)
                        if sentence is not None and self.on_sentence is not None:
                            self.on_sentence(sentence, seq, reply_session)
                            seq += 1
                    elif ftype == "done":
                        done_received = True
                        if "full_text" in frame:
                            full_response = frame["full_text"] or ""
                        if isinstance(frame.get("raw_text"), str):
                            raw_text = frame["raw_text"]
                        if frame.get("decision"):
                            decision = frame["decision"]
                        if frame.get("delegation_question") is not None:
                            delegation_question = frame["delegation_question"]
                        # Radio-silence wake (spec §5): webinfer surfaces the
                        # wake round in the done frame; fire the injected
                        # callback so the live caller plays wake.wav.
                        silence_meta = frame.get("silence")
                        if (
                            isinstance(silence_meta, dict)
                            and silence_meta.get("wake") is True
                            and self.on_silence_wake is not None
                        ):
                            try:
                                self.on_silence_wake()
                            except Exception as exc:
                                self._log.warning("[tts-stream] on_silence_wake failed: %s", exc)
                    elif ftype == "error":
                        raise RuntimeError(frame.get("error") or "webinfer stream error")
        except Exception as exc:
            self._log.error(
                "[tts-stream] streaming LLM failed (session=%d, frames=%s, decision=%s): %s",
                reply_session,
                frames_received,
                decision_received,
                exc,
            )
            if not decision_received:
                # No decision was ever delivered (transport failure, HTTP
                # error, or an error frame BEFORE the decision frame): clean
                # retry through the non-streaming path so the reply is never
                # lost — nothing was spoken, so there is no double-play risk.
                self._log.info("[tts-stream] fail-open -> non-streaming retry")
                return StreamingTurnResult(
                    full_response="",
                    decision=decision,
                    delegation_question=delegation_question,
                    cancelled=False,
                    reply_session=reply_session,
                    sentence_count=seq,
                    needs_non_streaming_retry=True,
                )
            # Mid-stream failure after a decision: keep what we have (log, do
            # not re-run — sentences may already be playing).
            if decision == "response":
                remaining = sentence_buffer.flush_remaining()
                if remaining:
                    self._spawn(remaining, seq, reply_session)
                    seq += 1
                    self._log.info(
                        "[tts-stream] flushed %d buffered char(s) after mid-stream failure",
                        len(remaining),
                    )

        if cancelled:
            # Barge-in / exit word: stop everything. The epoch bump already
            # cancelled every in-flight sentence task; the partial reply is
            # intentionally not broadcast (the user is talking over it).
            return StreamingTurnResult(
                full_response=full_response,
                decision=decision,
                delegation_question=delegation_question,
                cancelled=True,
                reply_session=reply_session,
                sentence_count=seq,
            )

        if not done_received:
            # Stream ended without a done frame (e.g. mid-stream failure above):
            # flush any remaining buffered sentence so no text is lost.
            remaining = sentence_buffer.flush_remaining()
            if remaining:
                self._spawn(remaining, seq, reply_session)
                seq += 1
            full_response = full_response or remaining or ""

        # #156: a server that predates the raw_text frame (or a turn that ended
        # without one) still has a usable lower bound — whatever the body held
        # was certainly part of the parser's input. Never let a *spoken* round
        # be recorded as a zero-length output.
        raw_text = raw_text or full_response

        # sentence_buffer may still hold text if neither path flushed it.
        if not sentence_buffer.is_empty:
            remaining = sentence_buffer.flush_remaining()
            if remaining:
                self._spawn(remaining, seq, reply_session)
                seq += 1

        self._log.info(
            "[tts-stream] LLM stream done (session=%d, decision=%s, sentences=%d, chars=%d)",
            reply_session,
            decision,
            seq,
            len(full_response),
        )
        return StreamingTurnResult(
            full_response=full_response,
            decision=decision,
            delegation_question=delegation_question,
            cancelled=False,
            reply_session=reply_session,
            sentence_count=seq,
            raw_text=raw_text,
        )

    def _spawn(self, sentence: str, seq: int, reply_session: int) -> None:
        """Route one flushed sentence to the injected synthesis callback."""
        if self.on_sentence is not None:
            self.on_sentence(sentence, seq, reply_session)
