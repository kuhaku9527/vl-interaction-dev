"""#158: the latency-origin field the timing axis reads must keep its name+value.

Why this test exists
--------------------
`services/webinfer/decision_eval_timing.py` decides whether an onset latency is
trustworthy by reading a **per-row** origin field off the event stream. The two
services cannot import each other (separate packages, separate CI matrix
entries), so the field name and its only legal value are necessarily duplicated
as data in two places:

  * the writer — `joy_interaction_webui.live_llm` (`LATENCY_SOURCE_FIELD` /
    `LATENCY_SOURCE_CHAIN_STAMP`)
  * the reader — `decision_eval_timing` (`LATENCY_SOURCE_CHAIN_STAMP`)

A drift between those copies is silent and one-directional: the writer would
keep stamping rounds while the reader judges every one of them "unattributed".
That reads as "the instrumentation is broken" when the truth is "the two halves
disagree on a string" — precisely the class of defect #158 exists to eliminate.

These tests pin the strings, and pin that the writer only claims the chain
origin when it actually recorded a latency. Claiming it without one would
manufacture evidence: the reader would then pass a round whose origin is
unknown.

Run: python -m pytest tests/test_latency_source_contract.py -q
"""

from __future__ import annotations

import asyncio
import logging
import re
import sys
from collections import deque
from pathlib import Path
from unittest.mock import patch

from joy_interaction_webui import live_llm

REPO = Path(__file__).resolve().parents[3]
WEBINFER = REPO / "services" / "webinfer"


class _StubCtrl:
    """Minimal TurnController stand-in (finish_llm_turn calls two hooks)."""

    def on_llm_token(self, *_a, **_k) -> None:
        pass

    def on_llm_finished(self, *_a, **_k) -> None:
        pass


def _capture_emit(captured: list[dict]):
    """Patch the module-level emit wrapper and record every event."""

    def fake_emit_event(service, event, level="info", **kwargs):
        captured.append({"service": service, "event": event, "level": level, **kwargs})

    return patch.object(live_llm, "emit_event", fake_emit_event, create=True)


def _drive(*, turn_started_at: float | None, now: float = 100.0) -> list[dict]:
    """Run finish_llm_turn once and return the emitted events."""
    captured: list[dict] = []

    async def _noop() -> None:
        return None

    with _capture_emit(captured), patch.object(live_llm.time, "monotonic", lambda: now):
        asyncio.run(
            live_llm.finish_llm_turn(
                text="喂，帮我查一下天气",
                response="</response> 好的。",
                decision="response",
                delegation_question=None,
                reply_epoch=None,
                llm_reply_epoch=0,
                background_service=None,
                conv_history=deque(maxlen=8),
                sentence_spawned_this_turn=False,
                on_llm_response=None,
                ctrl=_StubCtrl(),
                tts_turn_task=None,
                wait_tts_turn_done=_noop,
                logger=logging.getLogger("test.latency-source"),
                session_id="sess-1",
                turn_started_at=turn_started_at,
                raw_text="</response> 好的。",
            )
        )
    return captured


def test_writer_stamps_the_origin_when_a_latency_was_recorded():
    """★ 有轮次打点时，事件必须带上出处 —— 否则读侧一律「不可归因」."""
    events = _drive(turn_started_at=99.5)  # 500 ms before `now`
    assert len(events) == 1, events
    event = events[0]
    assert event["latency_ms"] == 500
    assert event["extra"][live_llm.LATENCY_SOURCE_FIELD] == live_llm.LATENCY_SOURCE_CHAIN_STAMP


def test_writer_does_not_claim_an_origin_without_a_latency():
    """★★ 没有 ``latency_ms`` 时**不得**声明出处.

    ★ 声明了就是**制造证据**：读侧会因此把一个「出处未知」的轮次当成
    「出自链上打点」。实测的读取路径就是这样被对抗性复核绕过的
    （初版让出处由声明决定，于是一个用 ts 差值算耗时的实现判绿）。
    正确行为是把该轮记为 unattributed —— 那是事实。
    """
    events = _drive(turn_started_at=None)
    assert len(events) == 1, events
    event = events[0]
    assert event["latency_ms"] is None
    assert live_llm.LATENCY_SOURCE_FIELD not in event["extra"], (
        "没有耗时却声明了出处 ⇒ 制造证据，读侧会把未知当成已知"
    )


def test_writer_and_reader_agree_on_the_value():
    """★ 写侧与读侧的常量必须一致（两处复制是为了跨服务，必须被钉住）."""
    source = (WEBINFER / "decision_eval_timing.py").read_text(encoding="utf-8")
    match = re.search(
        r'^LATENCY_SOURCE_CHAIN_STAMP\s*=\s*"([^"]+)"', source, re.MULTILINE
    )
    assert match, "读侧没有 LATENCY_SOURCE_CHAIN_STAMP 常量定义"
    assert match.group(1) == live_llm.LATENCY_SOURCE_CHAIN_STAMP, (
        "写侧与读侧的出处取值已分叉：写侧会一直打标，而读侧把每一轮都判成"
        "「不可归因」—— 症状看起来像「埋点坏了」，实际是两半对不上一个字符串"
    )


def test_field_name_matches_what_the_reader_looks_for():
    """★ 字段名也必须一致：读侧扫的是 ``round.latency_source``."""
    source = (WEBINFER / "decision_events.py").read_text(encoding="utf-8")
    assert 'extra.get("latency_source")' in source, (
        "读侧（decision_events）不再从 extra 里读 latency_source —— "
        "写侧的字段名必须随之更新，否则该字段永远读不到"
    )
    assert live_llm.LATENCY_SOURCE_FIELD == "latency_source"


def test_timing_axis_imports_cleanly_in_this_interpreter():
    """★ 两半在同一个解释器里可共存（跨服务契约的最低要求）.

    只是把 webinfer 放进 path 后 import 一次 —— 若哪天读侧引入了 webui 侧
    没有的依赖，这条会先红，而不是等到门禁跑不动才发现。
    """
    if str(WEBINFER) not in sys.path:
        sys.path.insert(0, str(WEBINFER))
    import decision_eval_timing as timing  # noqa: PLC0415

    assert timing.LATENCY_SOURCE_CHAIN_STAMP == live_llm.LATENCY_SOURCE_CHAIN_STAMP
    assert timing.FIELD_STILL_SPEAKING == "user_still_speaking_at_decision"
