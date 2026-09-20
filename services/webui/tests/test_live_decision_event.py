"""Contract tests for the ``live_decision`` event stream (#146).

Spec: agentteams issue #146 — decisions were never persisted as a
machine-readable event stream, which makes "is the decision right?" and
"was it a good moment to speak?" impossible to evaluate offline.

Pinned contracts:

  1. ``live_llm.finish_llm_turn`` emits exactly one ``live_decision`` event
     per finished turn, on the live path.
  2. ★ The recorded ``decision`` is the ORIGINAL value — i.e. ``delegation``
     must be captured BEFORE the function rewrites it to ``silence``
     (otherwise delegation is permanently lost from the record).
  3. The event carries the fields the evaluator needs and NO raw text —
     ADR-0014 forbids PII in ``extra``; only a length + hash is recorded.
  4. ``raw_text_len == 0`` is distinguishable — it means the model emitted
     nothing, which is the most common false-silence mode.
  5. A broken/absent event sink must NOT break the turn (fail-open).

Run: python -m pytest tests/test_live_decision_event.py -q
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections import deque
from typing import Any
from unittest.mock import patch

import pytest

from joy_interaction_webui import live_llm


class _StubCtrl:
    """Minimal TurnController stand-in (finish_llm_turn only calls two hooks)."""

    def on_llm_token(self, *_a, **_k) -> None:
        pass

    def on_llm_finished(self, *_a, **_k) -> None:
        pass


def _run_finish_turn(
    *,
    text: str = "玛尔基特怎么打？",
    response: str = "</response> 先打碎它身上的水晶。",
    decision: str = "response",
    delegation_question: str | None = None,
) -> list[dict]:
    """Drive finish_llm_turn and return the captured emitted events."""
    captured: list[dict] = []

    def fake_emit_event(service, event, level="info", **kwargs):
        captured.append({"service": service, "event": event,
                         "level": level, **kwargs})

    with patch.object(live_llm, "emit_event", fake_emit_event, create=True):
        async def _go() -> None:
            await live_llm.finish_llm_turn(
                text=text,
                response=response,
                decision=decision,
                delegation_question=delegation_question,
                reply_epoch=None,
                llm_reply_epoch=0,
                background_service=None,
                conv_history=deque(maxlen=12),
                sentence_spawned_this_turn=False,
                on_llm_response=None,
                ctrl=_StubCtrl(),
                tts_turn_task=None,
                wait_tts_turn_done=_noop,
                logger=logging.getLogger("test"),
            )

        asyncio.run(_go())
    return captured


async def _noop() -> None:
    return None


# ---------------------------------------------------------------------------
# 1. one event per turn, correct name/service
# ---------------------------------------------------------------------------


def test_emits_exactly_one_live_decision_event():
    events = _run_finish_turn()
    decisions = [e for e in events if e["event"] == "live_decision"]
    assert len(decisions) == 1, f"expected exactly one live_decision, got {events}"
    assert decisions[0]["service"] == "webui"


# ---------------------------------------------------------------------------
# 2. ★ delegation must be captured BEFORE the silence rewrite
# ---------------------------------------------------------------------------


def test_delegation_recorded_as_delegation_not_silence():
    """The function rewrites delegation -> silence; the record must keep delegation."""
    events = _run_finish_turn(
        text="查一下明天的天气",
        response="正在查。",
        decision="delegation",
        delegation_question="明天的天气",
    )
    rec = [e for e in events if e["event"] == "live_decision"][0]
    assert rec["extra"]["decision"] == "delegation", (
        "decision was rewritten to silence before being recorded — "
        "delegation would be permanently lost from the event stream"
    )


# ---------------------------------------------------------------------------
# 3. no raw text (ADR-0014 PII red line); only len + hash
# ---------------------------------------------------------------------------


def test_no_raw_text_in_event_only_len_and_hash():
    text = "玛尔基特怎么打？"
    response = "</response> 先打碎它身上的水晶。"
    events = _run_finish_turn(text=text, response=response)
    rec = [e for e in events if e["event"] == "live_decision"][0]
    extra = rec["extra"]

    # No field may contain the raw utterance/reply
    for key, value in extra.items():
        assert text not in str(value), f"{key} leaked raw user text"
        assert "水晶" not in str(value), f"{key} leaked raw assistant text"

    assert extra["raw_text_len"] == len(response)
    expected = hashlib.sha256(response.encode("utf-8")).hexdigest()[:16]
    assert extra["raw_text_sha256_16"] == expected


# ---------------------------------------------------------------------------
# 4. empty output is distinguishable (raw_text_len == 0)
# ---------------------------------------------------------------------------


def test_empty_model_output_is_distinguishable():
    events = _run_finish_turn(text="唉，好累", response="", decision="silence")
    rec = [e for e in events if e["event"] == "live_decision"][0]
    assert rec["extra"]["raw_text_len"] == 0
    assert rec["extra"]["decision"] == "silence"


# ---------------------------------------------------------------------------
# 5. fail-open: a broken sink must not break the turn
# ---------------------------------------------------------------------------


def test_broken_event_sink_does_not_break_turn():
    """A failing sink (disk full / serialization error) must not cost a turn.

    We patch the LOW-LEVEL sink (``_emit_event``) rather than the module-level
    wrapper, because the wrapper is what provides the fail-open guarantee —
    patching the wrapper would bypass the very protection under test.
    """

    def boom(*_a, **_k):
        raise RuntimeError("sink exploded")

    history: deque = deque(maxlen=12)
    with patch.object(live_llm, "_emit_event", boom):
        async def _go() -> None:
            await live_llm.finish_llm_turn(
                text="hi",
                response="</response> hello",
                decision="response",
                delegation_question=None,
                reply_epoch=None,
                llm_reply_epoch=0,
                background_service=None,
                conv_history=history,
                sentence_spawned_this_turn=False,
                on_llm_response=None,
                ctrl=_StubCtrl(),
                tts_turn_task=None,
                wait_tts_turn_done=_noop,
                logger=logging.getLogger("test"),
            )

        asyncio.run(_go())

    # The turn still completed normally
    assert list(history) == [("user", "hi"), ("assistant", "</response> hello")]


def test_non_serializable_extra_does_not_break_turn():
    """emit_event documents that a non-serializable ``extra`` raises ValueError.

    Our wrapper must swallow it: observability matters, but not more than the
    user's turn.
    """

    def boom(*_a, **_k):
        raise ValueError("not JSON serializable")

    history: deque = deque(maxlen=12)
    with patch.object(live_llm, "_emit_event", boom):
        async def _go() -> None:
            await live_llm.finish_llm_turn(
                text="hi",
                response="</response> hello",
                decision="response",
                delegation_question=None,
                reply_epoch=None,
                llm_reply_epoch=0,
                background_service=None,
                conv_history=history,
                sentence_spawned_this_turn=False,
                on_llm_response=None,
                ctrl=_StubCtrl(),
                tts_turn_task=None,
                wait_tts_turn_done=_noop,
                logger=logging.getLogger("test"),
            )

        asyncio.run(_go())

    assert list(history) == [("user", "hi"), ("assistant", "</response> hello")]


# ---------------------------------------------------------------------------
# 6. the recorded decision set is the four-state vocabulary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("decision", ["silence", "response", "not-for-me", "delegation"])
def test_all_four_states_are_recorded_verbatim(decision):
    events = _run_finish_turn(decision=decision)
    rec = [e for e in events if e["event"] == "live_decision"][0]
    expected = "delegation" if decision == "delegation" else decision
    assert rec["extra"]["decision"] == expected
