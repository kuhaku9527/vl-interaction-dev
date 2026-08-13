"""P0-A TTS-streaming NDJSON frame protocol for ``/v1/text/chat stream=true``.

Extracted from ``infer_loop.py`` (batch-2 monolith decoupling, zero behaviour
change). Owns the stateless frame protocol: the decision-marker watch regex,
the delta extractors, and :func:`build_stream_frames`, which converts an
iterable of content deltas into the decision-first NDJSON frame list.

The session-dependent orchestration (``_handle_text_chat_streaming`` /
``_stream_text_payload_frames``) stays on ``InferLoopMixin`` and calls these
pure functions; decision semantics are byte-for-byte identical to the
non-streaming path because every decision is derived through
:func:`response_format.parse_model_decision` over the accumulated prefix.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from response_format import parse_model_decision

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
