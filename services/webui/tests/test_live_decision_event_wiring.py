"""`live_decision` 事件**写入侧**的 #156 契约（round-start 打点 + 会话/延迟落地）.

Spec: #154 §一；工单 #156。

#146 建好了写入点，但两个写入点把 **会话标识** 与 **延迟** 硬编码成空值：

  * ``live_llm.finish_llm_turn``      → ``session_id=None, latency_ms=None``
  * ``live_proactive.send_proactive_prompt`` → 同上

而 live 链路**完全没有轮次起始时刻**，所以「从该开口到真的开口隔了多久」
**没有数据源头**。本文件钉住 #156 新增的三条契约：

  1. 用户轮记录里的 ``session_id`` 是**真实会话标识**（不是 None）；
  2. ``latency_ms`` 来自**轮次开启时刻**的打点（不是 0、不是常数）；
  3. 主动轮同样带上 ``session_id`` 与延迟。

外加：

  4. **顺序契约不回退** —— 委派仍以「委派」被记录（既有测试另在
     ``test_live_decision_event.py``，本文件补一条端到端的同轮断言）；
  5. ★ **负控**：去掉轮次打点 ⇒ 延迟字段必须是 ``None`` 而**不是** ``0``。
     （0 会被读侧当成一个「快到不可能」的真实耗时，比缺失更坏。）

Run: python -m pytest tests/test_live_decision_event_wiring.py -q
"""

from __future__ import annotations

import asyncio
import logging
import sys
from collections import deque
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[3]
WEBUI_SRC = REPO / "services" / "webui" / "src"
TESTS_DIR = Path(__file__).resolve().parent
for _p in (str(REPO), str(WEBUI_SRC), str(TESTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402
from test_live_proactive import B64, build_live  # noqa: E402

from joy_interaction_webui import live_llm  # noqa: E402
from joy_interaction_webui import live_proactive as proactive_module  # noqa: E402


class _StubCtrl:
    """Minimal TurnController stand-in (finish_llm_turn calls two hooks)."""

    def on_llm_token(self, *_a, **_k) -> None:
        pass


async def _noop() -> None:
    return None


def _capture_emit(captured: list[dict]):
    """Patch the module-level emit wrapper and record every event."""

    def fake_emit_event(service, event, level="info", **kwargs):
        captured.append({"service": service, "event": event, "level": level, **kwargs})

    return patch.object(live_llm, "emit_event", fake_emit_event, create=True)


def _run_finish_turn(
    *,
    session_id: str | None = None,
    turn_started_at: float | None = None,
    raw_text: str | None = None,
    response: str = "</response> 先打碎它身上的水晶。",
    decision: str = "response",
    now: float | None = None,
) -> list[dict]:
    """Drive finish_llm_turn and return the captured emitted events."""
    captured: list[dict] = []
    with _capture_emit(captured):
        if now is not None:
            # finish_llm_turn resolves latency with the real monotonic clock.
            with patch.object(live_llm.time, "monotonic", return_value=now):

                async def _go() -> None:
                    await live_llm.finish_llm_turn(
                        text="玛尔基特怎么打？",
                        response=response,
                        decision=decision,
                        delegation_question=None,
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
                        session_id=session_id,
                        turn_started_at=turn_started_at,
                        raw_text=raw_text,
                    )

                asyncio.run(_go())
        else:

            async def _go_plain() -> None:
                await live_llm.finish_llm_turn(
                    text="玛尔基特怎么打？",
                    response=response,
                    decision=decision,
                    delegation_question=None,
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
                    session_id=session_id,
                    turn_started_at=turn_started_at,
                    raw_text=raw_text,
                )

            asyncio.run(_go_plain())
    return captured


def _decision_event(events: list[dict]) -> dict:
    return next(e for e in events if e["event"] == "live_decision")


# ---------------------------------------------------------------------------
# 1. latency: the round-start stamp
# ---------------------------------------------------------------------------


def test_latency_comes_from_the_round_start_stamp():
    """★ #156 AC: 决策记录里的延迟来自轮次开启时刻的打点（不是 0、不是常数）."""
    events = _run_finish_turn(turn_started_at=1000.0, now=1000.742)
    rec = _decision_event(events)
    assert rec["latency_ms"] == 742, f"expected the stamped span, got {rec['latency_ms']}"


def test_two_rounds_with_different_spans_record_different_latencies():
    """Not a constant: two rounds with different stamps must differ."""
    first = _decision_event(_run_finish_turn(turn_started_at=1000.0, now=1000.100))
    second = _decision_event(_run_finish_turn(turn_started_at=1000.0, now=1002.500))
    assert first["latency_ms"] == 100
    assert second["latency_ms"] == 2500
    assert first["latency_ms"] != second["latency_ms"]


def test_latency_is_never_negative_even_if_the_clock_goes_backwards():
    """A monotonic clock should not go backwards, but a bad stamp must not emit -5ms."""
    events = _run_finish_turn(turn_started_at=1000.0, now=999.0)
    assert _decision_event(events)["latency_ms"] == 0


# ---------------------------------------------------------------------------
# 2. session id lands for real
# ---------------------------------------------------------------------------


def test_session_id_is_recorded_verbatim():
    """★ #156 AC: 两个写入点不再把会话标识硬编码为空."""
    events = _run_finish_turn(session_id="sess-live-42", turn_started_at=1000.0, now=1000.5)
    rec = _decision_event(events)
    assert rec["session_id"] == "sess-live-42"


def test_absent_session_id_stays_absent_rather_than_becoming_a_literal():
    """A caller that genuinely has no session must not get a fake one.

    The reader groups missing sessions under an explicit "(unattributed)"
    bucket; inventing a placeholder here would make that bucket unreachable.
    """
    events = _run_finish_turn(session_id=None, turn_started_at=1000.0, now=1000.5)
    rec = _decision_event(events)
    assert rec.get("session_id") is None


# ---------------------------------------------------------------------------
# 3. ★ negative control: remove the stamp -> latency must be MISSING, not 0
# ---------------------------------------------------------------------------


def test_negative_control_without_the_stamp_latency_is_absent_not_zero():
    """★ #156 负控：去掉延迟赋值 → 聚合结果的延迟字段缺失或异常.

    Here we remove the *source* (the round-start stamp). The writer must emit
    ``latency_ms: None`` — emitting ``0`` would be worse than missing, because
    the reader would treat it as a real, implausibly fast round.
    """
    events = _run_finish_turn(turn_started_at=None)
    rec = _decision_event(events)
    assert rec.get("latency_ms") is None
    assert rec.get("latency_ms") != 0


def test_resolve_latency_helper_contract():
    assert live_llm.resolve_latency_ms(None) is None
    assert live_llm.resolve_latency_ms(5.0, 5.0) == 0
    assert live_llm.resolve_latency_ms(5.0, 5.25) == 250
    assert live_llm.resolve_latency_ms(5.0, 4.0) == 0, "negative spans clamp to 0"


def test_resolve_latency_uses_the_caller_clock_not_the_wall_clock():
    """★ Both ends of the span must come from the SAME clock.

    Found while implementing #156: stamping from an injected test clock and
    resolving against the real ``time.monotonic`` produced a span of
    **98,354,343 ms** (≈ 27 hours) instead of 250 ms. The reader would have
    recorded that as a real latency, silently poisoning the timing axis.
    """
    assert live_llm.resolve_latency_ms(50.0, clock=lambda: 50.25) == 250
    assert live_llm.resolve_latency_ms(1_000.0, clock=lambda: 1_000.5) == 500


# ---------------------------------------------------------------------------
# 4. raw_text: the parser's input, not the cleaned body
# ---------------------------------------------------------------------------


def test_raw_text_len_measures_the_parser_input_not_the_body():
    """★ A not-for-me round has an EMPTY body but a non-empty raw output.

    Measuring the body would record `raw_text_len == 0`, which the reader
    classifies as "the model emitted nothing" — misreading a real non-addressed
    judgement as a failed output.
    """
    raw = " 这是您自己在说话，未针对 BT-7274 发出指令。 </not-for-me>"
    events = _run_finish_turn(
        raw_text=raw,
        response="",  # the body is empty by construction
        decision="not-for-me",
        turn_started_at=1000.0,
        now=1000.4,
    )
    extra = _decision_event(events)["extra"]
    assert extra["raw_text_len"] == len(raw)
    assert extra["response_chars"] == 0
    assert extra["raw_text_len"] != extra["response_chars"]


def test_raw_text_defaults_to_the_response_when_the_caller_has_none():
    """Backwards compatible: a caller that cannot supply raw_text still records
    a usable length (the body is the only thing it knows)."""
    events = _run_finish_turn(response="</response> 好的。", raw_text=None)
    extra = _decision_event(events)["extra"]
    assert extra["raw_text_len"] == len("</response> 好的。")


def test_empty_string_raw_text_falls_back_to_the_body():
    """★ An empty string means "not supplied", NOT "the parser saw nothing".

    Found on real data: 96 rows in ``logs/events/webui-2026-09-21.jsonl`` held
    **24 rounds with ``raw_text_len == 0`` while ``response_chars > 0``** — all
    ``decision="response"``, i.e. the assistant had spoken. Treating ``""`` as a
    genuine zero made the reader report those as *failed outputs*
    (``empty_output``), a false signal worse than a missing one.

    The structural invariant: whatever the body holds, the parser must have
    seen at least that much.
    """
    events = _run_finish_turn(response="</response> 好的。", raw_text="")
    extra = _decision_event(events)["extra"]
    assert extra["raw_text_len"] == len("</response> 好的。"), (
        "an empty raw_text was recorded as a real zero-length output"
    )
    assert extra["raw_text_len"] >= extra["response_chars"]


@pytest.mark.parametrize(
    ("response", "raw_text"),
    [
        ("</response> 你好。", None),
        ("</response> 你好。", ""),
        ("</response> 你好。", "</response> 你好。"),
        ("", " 不是对我说的。 </not-for-me>"),
        ("", ""),
        ("</response> 短。", " </response> 短。 </delegation> 问题"),
    ],
)
def test_raw_text_len_is_never_below_response_chars(response, raw_text):
    """★ The invariant, over every supply/no-supply combination.

    ``raw_text_len < response_chars`` is structurally impossible: the parser's
    input contains the body by definition. A record violating it is corrupt and
    the reader would misclassify it (a spoken round filed as a failed output).
    """
    extra = _decision_event(_run_finish_turn(response=response, raw_text=raw_text))["extra"]
    assert extra["raw_text_len"] >= extra["response_chars"], extra


# ---------------------------------------------------------------------------
# 5. ordering contract + proactive round
# ---------------------------------------------------------------------------


def test_delegation_is_still_recorded_as_delegation_with_the_new_plumbing():
    """★ 顺序契约不回退：委派仍以「委派」被记录（含新增字段的那条路径）."""
    captured: list[dict] = []
    with _capture_emit(captured):

        async def _go() -> None:
            await live_llm.finish_llm_turn(
                text="查一下明天的天气",
                response="正在查。",
                decision="delegation",
                delegation_question="明天的天气",
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
                session_id="sess-x",
                turn_started_at=None,
            )

        asyncio.run(_go())

    rec = _decision_event(captured)
    assert rec["extra"]["decision"] == "delegation"
    assert rec["session_id"] == "sess-x"


@pytest.mark.asyncio
async def test_proactive_round_records_session_and_latency(monkeypatch):
    """★ proactive 轮同样带会话标识与真实延迟（#156 覆盖两个写入点）."""
    sm, _vad, _asr = build_live()
    sm.handle_frame(B64, 1000.0)

    async def fake_vlm(frames):
        return "silence", ""

    monkeypatch.setattr(sm, "_call_proactive_vlm", fake_vlm)

    captured: list[dict] = []

    def fake_record(**kwargs):
        captured.append(kwargs)

    with patch.object(proactive_module, "_record_decision", fake_record):
        await sm._send_proactive_prompt(frames=sm._frames_payload([sm.recent_frames[-1]]))

    assert len(captured) == 1
    rec = captured[0]
    assert rec["round_kind"] == "proactive"
    assert rec["session_id"] == "s1", f"proactive round lost its session id: {rec['session_id']}"
    assert rec["latency_ms"] is not None, "proactive round has no round-start stamp"
    assert rec["latency_ms"] >= 0
    # The span is measured against this machine's real monotonic clock, so its
    # exact value is not pinmable here. The pinned span lives in
    # ``test_proactive_latency_uses_the_injected_clock``; asserting anything
    # stronger than "not None / not negative" would need a clock injection.


@pytest.mark.asyncio
async def test_proactive_vlm_raw_input_is_measured_not_the_body(monkeypatch):
    """★ A proactive `not-for-me` has an EMPTY body but real raw output.

    Measuring the body would file a genuine non-addressed judgement under
    "the model emitted nothing" — the exact confusion #156 exists to remove.
    """
    sm, _vad, _asr = build_live()
    sm.handle_frame(B64, 1000.0)

    raw = " 这是背景里的对话，不是对我说的。 </not-for-me>"

    async def fake_vlm(frames):
        return "not-for-me", "", raw  # 3-tuple: the production arity

    monkeypatch.setattr(sm, "_call_proactive_vlm", fake_vlm)

    captured: list[dict] = []

    def fake_record(**kwargs):
        captured.append(kwargs)

    with patch.object(proactive_module, "_record_decision", fake_record):
        await sm._send_proactive_prompt(frames=sm._frames_payload([sm.recent_frames[-1]]))

    rec = captured[0]
    assert rec["decision"] == "not-for-me"
    assert rec["response"] == "", "the body is empty by construction"
    assert rec["raw_text"] == raw, "the parser's input must be carried through"


@pytest.mark.asyncio
async def test_injected_2tuple_proactive_vlm_still_works(monkeypatch):
    """Backwards compatible: an injected callable may still return 2 values.

    Several existing tests inject ``call_proactive_vlm`` with the older arity;
    a 2-tuple means "raw text not supplied" and must NOT crash the loop.
    """
    sm, _vad, _asr = build_live()
    sm.handle_frame(B64, 1000.0)

    async def fake_vlm(frames):
        return "silence", ""

    monkeypatch.setattr(sm, "_call_proactive_vlm", fake_vlm)

    captured: list[dict] = []

    def fake_record(**kwargs):
        captured.append(kwargs)

    with patch.object(proactive_module, "_record_decision", fake_record):
        await sm._send_proactive_prompt(frames=sm._frames_payload([sm.recent_frames[-1]]))

    assert len(captured) == 1
    assert captured[0]["raw_text"] is None, (
        "an unsupplied raw text must be signalled as None so the record falls "
        "back to the body instead of guessing"
    )


def test_call_proactive_vlm_returns_three_values_from_the_harness():
    """The real HTTP path must surface the parser's input, not just the body."""
    import asyncio as _asyncio

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            # Mirrors webinfer's _chat_completion_response: the cleaned body is
            # empty (special token stripped) while raw_content keeps the marker.
            return {
                "choices": [{"message": {"content": ""}}],
                "streamingharness": {
                    "decision": "not-for-me",
                    "raw_content": " 不是对我说的。 </not-for-me>",
                },
            }

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            return _Resp()

    fake_httpx = type("httpx", (), {"AsyncClient": _Client})
    with patch.dict(sys.modules, {"httpx": fake_httpx}):
        decision, response, raw_text = _asyncio.run(
            proactive_module.call_proactive_vlm(
                config=type(
                    "C",
                    (),
                    {
                        "llm_api_url": "http://x",
                        "llm_text_path": "/t",
                        "llm_model": "m",
                        "llm_system_prompt": "s",
                    },
                )(),
                frames=[],
                logger=logging.getLogger("test"),
            )
        )

    assert (decision, response) == ("not-for-me", "")
    assert raw_text == " 不是对我说的。 </not-for-me>"


@pytest.mark.asyncio
async def test_proactive_latency_uses_the_injected_clock(monkeypatch):
    """The stamp is taken at round entry, so an injected clock pins the span."""
    sm, _vad, _asr = build_live()
    sm.handle_frame(B64, 1000.0)

    class _Clock:
        def __init__(self) -> None:
            self.now = 100.0

        def __call__(self) -> float:
            return self.now

    clock = _Clock()
    sm._clock = clock

    async def fake_vlm(frames):
        clock.now = 100.35  # the VLM call took 350 ms
        return "silence", ""

    monkeypatch.setattr(sm, "_call_proactive_vlm", fake_vlm)

    captured: list[dict] = []

    def fake_record(**kwargs):
        captured.append(kwargs)

    with patch.object(proactive_module, "_record_decision", fake_record):
        await sm._send_proactive_prompt(frames=sm._frames_payload([sm.recent_frames[-1]]))

    assert captured[0]["latency_ms"] == 350


# ---------------------------------------------------------------------------
# 6. user round end-to-end through the live machine
# ---------------------------------------------------------------------------


def test_user_round_through_the_live_machine_carries_session_and_latency(monkeypatch):
    """Drive the real facade (``sm._finish_llm_turn``) rather than the module fn.

    This is the wiring the ticket cares about: `live_mode` must forward its own
    session id and its own round-start stamp, not pass None along.
    """
    sm, _vad, _asr = build_live()
    assert sm.session_id == "s1"

    class _Clock:
        def __init__(self) -> None:
            self.now = 50.0

        def __call__(self) -> float:
            return self.now

    clock = _Clock()
    # The stamp and the resolve must both come from the machine's clock.
    sm._clock = clock

    captured: list[dict] = []
    with _capture_emit(captured):
        sm._turn_started_at = 50.0
        clock.now = 50.25  # 250 ms later, after the LLM round

        async def _go() -> None:
            await sm._finish_llm_turn(
                text="你好",
                response="</response> 你好。",
                decision="response",
                delegation_question=None,
            )

        asyncio.run(_go())

    rec = _decision_event(captured)
    assert rec["session_id"] == "s1"
    assert rec["latency_ms"] == 250


def test_live_machine_stamps_the_round_start_on_commit():
    """`_handle_commit` must take the stamp; without it nothing downstream can."""
    sm, _vad, _asr = build_live()
    assert sm._turn_started_at is None
    sm._clock = lambda: 777.0
    sm._current_asr_text = ""

    async def _go() -> None:
        # Empty text -> garbage guard returns early, but only AFTER the stamp.
        await sm._handle_commit()

    asyncio.run(_go())
    assert sm._turn_started_at == 777.0, (
        "the round-start stamp was not taken at commit time; the latency axis "
        "would have no data source again"
    )
