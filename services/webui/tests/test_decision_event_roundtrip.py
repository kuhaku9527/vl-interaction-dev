"""端到端：**真实写入点 → 真实 JSONL 文件 → 真实读取方**（工单 #156）.

Spec #154 把这条缝定为「**缝一 —— ADR-0014 JSONL 事件流**（读侧契约，端到端不经内部）」，
先例是两个在 emit 边界打桩的既有测试。但打桩只证明「事件对象被构造出来了」，
**不证明它真的落到了盘上、并且真的能被读侧读出来**。本文件补的就是这一段。

链路（全部是真的，只有 LLM 调用是桩）：

    live_llm.finish_llm_turn
      -> event_json.emit_event          （真实 emitter）
      -> logs/events/webui-<UTC>.jsonl  （真实文件，重定向到 tmp_path）
      -> decision_events.load_events    （真实读取方）
      -> aggregate / format_report

★ 含负控：**去掉轮次打点** ⇒ 聚合结果的延迟字段缺失（证明读侧真的在用它）。
  这正是 #156 验收里的那一条，也是「一个没有读者的埋点 = 没有埋点」的反面证据：
  读侧对写入侧的变化**有反应**。

跨服务导入说明：本测试同时 import webui 的写入侧与 webinfer 的读取侧。
两者在**同一个仓库、同一个 checkout** 里，且 ``tests/conftest.py`` 已把
``services/webinfer`` 放进 ``sys.path``（``test_jarvis_webinfer_e2e.py`` 是既有先例）。

Run: python -m pytest tests/test_decision_event_roundtrip.py -q
"""

from __future__ import annotations

import asyncio
import logging
import sys
from collections import deque
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
WEBUI_SRC = REPO / "services" / "webui" / "src"
TESTS_DIR = Path(__file__).resolve().parent
for _p in (str(REPO), str(WEBUI_SRC), str(TESTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import decision_events as de  # noqa: E402
import pytest  # noqa: E402
from test_live_proactive import B64, build_live  # noqa: E402

from joy_interaction_webui import live_llm  # noqa: E402

# ``event_json`` lives in ``services/common/`` and is only reachable once
# ``live_llm`` has put that directory on ``sys.path`` (importing live_llm above
# does exactly that). Resolve it from ``sys.modules`` rather than re-importing,
# so this file proves it is the SAME module object the writer emits through.
live_llm._ensure_event_json_importable()
import event_json  # noqa: E402


class _StubCtrl:
    """Minimal TurnController stand-in (finish_llm_turn calls two hooks)."""

    def on_llm_token(self, *_a, **_k) -> None:
        pass


async def _noop() -> None:
    return None


@pytest.fixture()
def events_dir(tmp_path, monkeypatch):
    """Redirect the REAL emitter at a temp events dir (no /logs pollution).

    ``event_json`` resolves its directory once at import and caches one logger
    per (service, UTC-day); both must be reset or the test would write into the
    repo's real ``logs/events``.
    """
    target = tmp_path / "events"
    target.mkdir()
    monkeypatch.setattr(event_json, "_EVENTS_DIR", str(target))
    monkeypatch.setattr(event_json, "_LOGGERS", {})
    return target


def _finish_turn_coro(
    *,
    session_id: str | None,
    turn_started_at: float | None,
    now: float | None,
    response: str = "</response> 先打碎它身上的水晶。",
    decision: str = "response",
    raw_text: str | None = None,
):
    """Build (but do not run) one real live user round through the real emitter."""
    return live_llm.finish_llm_turn(
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
        clock=(lambda: now) if now is not None else None,
    )


def _round(
    *,
    session_id: str | None,
    turn_started_at: float | None,
    now: float | None,
    response: str = "</response> 先打碎它身上的水晶。",
    decision: str = "response",
    raw_text: str | None = None,
) -> None:
    """Drive one real live user round through the real emitter (sync tests)."""
    asyncio.run(
        _finish_turn_coro(
            session_id=session_id,
            turn_started_at=turn_started_at,
            now=now,
            response=response,
            decision=decision,
            raw_text=raw_text,
        )
    )


def _read(events_dir: Path) -> dict:
    loaded = de.load_events(events_dir)
    assert loaded.malformed == [], loaded.malformed
    return de.aggregate(loaded.rounds, skipped_other_events=loaded.skipped_other_events)


# ---------------------------------------------------------------------------
# 1. the decision really lands on disk and the reader really reads it
# ---------------------------------------------------------------------------


def test_written_decision_is_readable_from_the_events_dir(events_dir):
    """★ End-to-end: 真实写入点 → 真实文件 → 真实读取方."""
    _round(session_id="sess-e2e", turn_started_at=100.0, now=100.742)

    files = list(events_dir.glob("webui-*.jsonl"))
    assert len(files) == 1, f"the emitter did not write a webui day file: {list(events_dir)}"

    agg = _read(events_dir)
    assert agg["n_sessions"] == 1
    session = agg["sessions"][0]
    assert session["session_id"] == "sess-e2e"
    assert session["n_rounds"] == 1

    round_ = session["rounds"][0]
    assert round_["decision"] == "response"
    assert round_["round_kind"] == "user"
    assert round_["latency_ms"] == 742, "the latency must come from the round-start stamp"
    assert round_["ts"], "the emitter must stamp an ISO-8601 UTC time"
    assert round_["output_state"] == de.OUTPUT_SPOKE


def test_multiple_rounds_in_one_session_are_ordered_and_numbered(events_dir):
    _round(session_id="sess-multi", turn_started_at=10.0, now=10.1)
    _round(session_id="sess-multi", turn_started_at=20.0, now=20.5, decision="silence", response="")
    _round(session_id="sess-multi", turn_started_at=30.0, now=30.2)

    agg = _read(events_dir)
    session = agg["sessions"][0]
    assert session["n_rounds"] == 3
    assert [r["seq"] for r in session["rounds"]] == [1, 2, 3]
    assert [r["latency_ms"] for r in session["rounds"]] == [100, 500, 200]
    assert [r["decision"] for r in session["rounds"]] == ["response", "silence", "response"]


def test_two_sessions_stay_separate(events_dir):
    _round(session_id="sess-a", turn_started_at=1.0, now=1.1)
    _round(session_id="sess-b", turn_started_at=2.0, now=2.3)

    agg = _read(events_dir)
    assert agg["n_sessions"] == 2
    assert {s["session_id"] for s in agg["sessions"]} == {"sess-a", "sess-b"}


# ---------------------------------------------------------------------------
# 2. the ambiguity the ticket is about, end to end
# ---------------------------------------------------------------------------


def test_not_for_me_with_a_body_and_an_empty_output_are_told_apart(events_dir):
    """★ 真机形态：`not-for-me` 的正文是空的，但原始输出有内容。

    A reader keying on the body would record BOTH of these as zero-length and
    lose the distinction the whole ticket is about.
    """
    not_for_me_raw = " 这是您自己在说话，未针对 BT-7274 发出指令。 </not-for-me>"
    _round(
        session_id="sess-amb",
        turn_started_at=1.0,
        now=1.5,
        decision="not-for-me",
        response="",  # empty body by construction
        raw_text=not_for_me_raw,
    )
    # A genuinely failed output: the parser saw nothing at all.
    _round(
        session_id="sess-amb",
        turn_started_at=2.0,
        now=2.5,
        decision="response",
        response="",
        raw_text="",
    )

    agg = _read(events_dir)
    session = agg["sessions"][0]
    by_seq = {r["seq"]: r for r in session["rounds"]}

    assert by_seq[1]["output_state"] == de.OUTPUT_QUIET_TRACED
    assert by_seq[1]["raw_text_len"] == len(not_for_me_raw)
    assert by_seq[2]["output_state"] == de.OUTPUT_EMPTY
    assert agg["empty_output_rounds"] == ["sess-amb#2"]


# ---------------------------------------------------------------------------
# 3. ★ negative control: remove the round-start stamp
# ---------------------------------------------------------------------------


def test_negative_control_without_the_stamp_latency_is_missing_in_the_aggregate(events_dir):
    """★ #156 负控：去掉延迟赋值 → 聚合结果的延迟字段缺失或异常.

    This is the pre-#156 behaviour (``latency_ms=None`` hardcoded). The reader
    must GO RED on the latency fields — not quietly report a healthy-looking
    number — and it must say so in ``caveats``.
    """
    _round(session_id="sess-nolat", turn_started_at=None, now=100.0)

    agg = _read(events_dir)
    latency = agg["totals"]["latency_ms"]

    assert latency["n_present"] == 0
    assert latency["n_missing"] == 1
    assert latency["median"] is None, "a missing latency must never become a number"
    assert latency["missing_rounds"] == ["sess-nolat#1"]
    assert any("没有 latency_ms" in note for note in agg["caveats"]), agg["caveats"]
    # ...and the round itself is still fully readable (only the timing is gone).
    assert agg["sessions"][0]["rounds"][0]["decision"] == "response"


def test_negative_control_removing_the_session_id_makes_it_unattributed(events_dir):
    """The other half of the pre-#156 state: ``session_id=None``.

    The round must still be counted (never dropped) but grouped as
    unattributed, and the reader must say the session view is incomplete.
    """
    _round(session_id=None, turn_started_at=1.0, now=1.1)

    agg = _read(events_dir)
    assert agg["totals"]["n_rounds"] == 1
    assert agg["totals"]["n_unattributed_session"] == 1
    assert agg["sessions"][0]["session_id"] == de.UNATTRIBUTED
    assert any("没有 session_id" in note for note in agg["caveats"]), agg["caveats"]


def test_control_with_the_stamp_present_latency_is_reported(events_dir):
    """Control for the negative control above: with the stamp the field is there.

    Without this, the "latency is missing" assertion could be passing simply
    because the reader never reports latency at all.
    """
    _round(session_id="sess-ok", turn_started_at=1.0, now=1.4)

    agg = _read(events_dir)
    latency = agg["totals"]["latency_ms"]
    assert latency["n_present"] == 1
    assert latency["n_missing"] == 0
    assert latency["median"] == 400
    assert not any("没有 latency_ms" in note for note in agg["caveats"])


# ---------------------------------------------------------------------------
# 4. both writer points, one reader (proactive + user in the same aggregate)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_proactive_and_user_rounds_land_in_one_aggregate(events_dir, monkeypatch):
    """★ 两个写入点写进同一条流，读侧能按轮次种类分开 —— 端到端."""
    # The user round runs on the ambient loop (we are inside an async test).
    await _finish_turn_coro(session_id="sess-mixed", turn_started_at=1.0, now=1.1)

    sm, _vad, _asr = build_live()
    sm.session_id = "sess-mixed"
    sm.handle_frame(B64, 1000.0)
    sm._clock = lambda: 5.0

    async def fake_vlm(frames):
        return "silence", ""

    monkeypatch.setattr(sm, "_call_proactive_vlm", fake_vlm)
    await sm._send_proactive_prompt(frames=sm._frames_payload([sm.recent_frames[-1]]))

    agg = _read(events_dir)
    assert agg["n_sessions"] == 1
    session = agg["sessions"][0]
    assert session["n_rounds"] == 2
    kinds = session["by_round_kind"]
    assert kinds["user"]["n_rounds"] == 1
    assert kinds["proactive"]["n_rounds"] == 1
    assert kinds["proactive"]["n_rounds"] == 1
    proactive = next(r for r in session["rounds"] if r["round_kind"] == "proactive")
    assert proactive["latency_ms"] is not None, "proactive round has no latency"
    assert proactive["frames_n"] == 1


# ---------------------------------------------------------------------------
# 5. the report a human / the future gate (#159) reads
# ---------------------------------------------------------------------------


def test_report_renders_the_real_round_trip(events_dir):
    _round(session_id="sess-rpt", turn_started_at=1.0, now=1.25)
    report = de.format_report(_read(events_dir))
    assert "sess-rpt" in report
    assert "250ms" in report
    assert de.OUTPUT_SPOKE in report


def test_cli_require_latency_is_green_on_a_healthy_round_trip(events_dir, capsys):
    """The reusable gate criterion: healthy input -> exit 0."""
    _round(session_id="sess-cli", turn_started_at=1.0, now=1.1)
    rc = de.main(["--events-dir", str(events_dir), "--require-latency"])
    assert rc == 0, capsys.readouterr().err


def test_cli_require_latency_goes_red_on_the_stamp_negative_control(events_dir, capsys):
    """★ ...and the SAME command goes red on the mutated input."""
    _round(session_id="sess-cli", turn_started_at=None, now=1.1)
    rc = de.main(["--events-dir", str(events_dir), "--require-latency"])
    assert rc == 1
    assert "判红" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# 6. the emitter itself is the real one (guard against a stubbed E2E)
# ---------------------------------------------------------------------------


def test_the_e2e_uses_the_real_emitter(events_dir):
    """Guard: if a future refactor patches the emitter, this file proves nothing.

    ``live_llm.emit_event`` must forward to the shared ``event_json`` emitter —
    a monkeypatched no-op here would make every assertion above vacuous.
    """
    assert live_llm._emit_event is event_json.emit_event, (
        "the emit path is no longer the real ADR-0014 emitter"
    )


def test_emitter_resolves_under_production_pythonpath(events_dir):
    """★ Regression: the writer must find `event_json` the way PRODUCTION does.

    Found while implementing #156. ``event_json`` lives in ``services/common/``
    and was imported as a top-level module, but webui starts with
    ``PYTHONPATH=<repo>/services/webui/src`` only (run-windows.ps1 →
    ``Start-Webui``). So on every real start the import raised
    ``ModuleNotFoundError`` and the ``except`` guard silently installed a no-op
    — **the `live_decision` events never reached disk**, while these very unit
    tests passed because pytest puts the repo root on `sys.path`.

    Inspecting the real day files confirmed it: ``logs/events/webui-*.jsonl``
    contained only ``config.services.patch`` (written by a different code path
    that opens the file directly), never a single ``live_decision``.

    This test reproduces the production import condition in a subprocess.
    """
    import subprocess

    script = (
        "import sys;"
        f"sys.path.insert(0, {str(WEBUI_SRC)!r});"
        "from joy_interaction_webui import live_llm;"
        "print(live_llm._emit_event.__module__)"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    resolved = proc.stdout.strip().splitlines()[-1]
    assert resolved == "event_json", (
        f"under production PYTHONPATH the emitter resolved to {resolved!r} — "
        "the no-op fallback is installed and every decision event is being "
        "silently discarded"
    )


def test_writer_would_be_silent_if_the_path_helper_were_removed(events_dir):
    """★ Negative control for the test above.

    Simulate the pre-fix state: `event_json` unreachable ⇒ the fallback no-op is
    installed ⇒ nothing is written. This proves the guard above can actually
    FAIL (rather than being an assertion that holds by construction).
    """
    import subprocess

    script = (
        "import sys;"
        f"sys.path.insert(0, {str(WEBUI_SRC)!r});"
        # Make the structural lookup fail, exactly as it did before the fix.
        "import joy_interaction_webui.live_llm as m;"
        "print(m._emit_event.__module__)"
    )
    # Strip services/common from the resolved path by faking a miss: intercept
    # os.path.exists for the probe path only.
    script = (
        "import sys, os;"
        f"sys.path.insert(0, {str(WEBUI_SRC)!r});"
        "_real = os.path.exists;"
        "os.path.exists = lambda p: False if p.endswith('event_json.py') else _real(p);"
        "import joy_interaction_webui.live_llm as m;"
        "print(m._emit_event.__module__)"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    resolved = proc.stdout.strip().splitlines()[-1]
    assert resolved == "joy_interaction_webui.live_llm", (
        "the negative control did not reproduce the silent no-op, so the "
        "regression test above cannot be trusted to catch it"
    )
