# ruff: noqa: RUF001, RUF002, RUF003
# (RUF001/002/003 = ambiguous fullwidth punctuation. This file's prose is Chinese
# and quotes real test sentences; the same suppression is used by the sibling
# eval test modules and by decision_eval_timing*.py. Established repo convention.)
"""时序轴**读数**的契约测试（工单 #158，spec #154）.

Spec: #154 §三「两条正交轴，不合成单一 accuracy」+ #158 的 AC。

本文件钉住 #158 的四条核心契约：

  1. **三个量都产出**：onset 延迟（中位/p90）、每秒误触发次数、premature rate；
  2. ★ **耗时只来自决策链路自身的轮次打点** —— 帧的采集端 ``ts_ms`` 与事件
     ``ts`` 差值都被明令禁止，且禁这件事**可判红**（不是一句文档承诺）；
  3. ★ **删掉轮次打点 ⇒ onset 变为「不可测」**，而 ``ts`` 仍在场 ——
     证明实现**没有**退回用墙钟差值补数；
  4. **可从冻结的事件夹具算出**（不需要真机在跑）。

以及两条「分母纪律」的负控（#157 已付费学过的那一类）：

  * ``0.0`` 是「测到 0」，``None`` 才是「没测」—— 两者含义相反；
  * 「不适用」（主动轮）与「未标注」（数据缺口）必须**分开**计数。

Run: cd services/webinfer && python -m pytest tests/test_decision_eval_timing.py -q
"""

from __future__ import annotations

import json
from pathlib import Path

import decision_eval_timing as T
import pytest
from decision_eval_timing_sources import build_rows
from decision_eval_timing_synthetic import (
    synthetic_healthy_rows,
    synthetic_silent_rows,
    synthetic_unstamped_rows,
)

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
TIMING_EVENTS = FIXTURE_DIR / "live_decision_timing_events.jsonl"
TIMING_TRUTH = FIXTURE_DIR / "decision_timing_truth.json"


def _fixture_rows() -> list[dict]:
    return build_rows(TIMING_EVENTS, TIMING_TRUTH)["rows"]


# ---------------------------------------------------------------------------
# 1. 冻结夹具可算（不依赖真机）
# ---------------------------------------------------------------------------


def test_frozen_fixture_exists_and_is_loadable_without_a_machine():
    """#158 AC: 可从冻结的事件夹具算出（不依赖真机在跑）."""
    assert TIMING_EVENTS.is_file(), "冻结的事件夹具必须入库"
    assert TIMING_TRUTH.is_file(), "真值 sidecar 必须入库"
    loaded = build_rows(TIMING_EVENTS, TIMING_TRUTH)
    assert loaded["reading"]["n_rounds"] == 34
    assert loaded["reading"]["is_frozen_fixture"] is True
    assert len(loaded["rows"]) == 34


def test_truth_survives_a_round_trip_through_the_asset_boundary():
    """真值来源是 decision_eval_set，而不是被重抄进夹具.

    ★ 这是承重的：真值若有两个家，改一处就会出现两份互相矛盾的「该不该说」，
    而两轴会各自按不同的真值判分。

    ★ 断言的是**每种 expected_action 都真的被映射了**：这里是「三种 action
    各取一例」而不是「三种常量存在」—— 后者是恒真式，证明不了映射是对的。
    """
    from decision_eval_set import ACTION_DELEGATE, ACTION_RESPOND, ACTION_SILENT, CASES
    from decision_eval_timing_sources import expected_by_case_id

    mapping = expected_by_case_id()
    # 逐 action 取**该 action 的第一条 case id**，故新增 action 类别时这里会
    # 因为 mapping 里没有可断言的对象而立刻需要更新（不是靠人记得）。
    first_of: dict[str, str] = {}
    for case_id, _text, _group, _cat, _note, action in CASES:
        first_of.setdefault(action, case_id)

    assert mapping[first_of[ACTION_RESPOND]] == T.EXPECTED_SPEAK
    assert mapping[first_of[ACTION_DELEGATE]] == T.EXPECTED_SPEAK, (
        "委派也要开口（它会触发检索与播报）"
    )
    assert mapping[first_of[ACTION_SILENT]] == T.EXPECTED_QUIET


# ---------------------------------------------------------------------------
# 2. 三个量都产出（AC #1）
# ---------------------------------------------------------------------------


def test_all_three_quantities_are_produced_from_the_frozen_fixture():
    """#158 AC: 产出 onset 延迟（中位/p90）、每秒误触发次数、premature rate."""
    block = T.timing_block(_fixture_rows())
    metrics = block["metrics"]

    onset = metrics["onset_latency_ms"]
    assert onset["median"] is not None, "onset 中位数必须产出"
    assert onset["p90"] is not None, "onset p90 必须产出"
    assert onset["p90"] >= onset["median"], "p90 不得小于中位数"

    assert metrics["spurious_triggers_per_second"] is not None
    assert metrics["spurious_triggers_per_second"] > 0
    assert metrics["spurious_triggers_per_minute"] is not None
    assert metrics["n_spurious"] >= 1

    assert metrics["premature_rate_pct"] is not None
    assert metrics["n_premature"] >= 1


def test_reported_values_match_the_fixture_arithmetic():
    """读数必须等于夹具里**手算得出**的那个数（防止实现悄悄换了口径）."""
    metrics = T.timing_block(_fixture_rows())["metrics"]
    rows = _fixture_rows()

    # 误触发 = 真值 quiet 却开口了的轮次
    expected_spurious = sum(1 for r in rows if r["expected"] == T.EXPECTED_QUIET and T.spoke(r))
    assert metrics["n_spurious"] == expected_spurious == 1

    # 跨度 = 各会话 (最后 ts - 最早 ts) 之和
    assert metrics["session_seconds"] == pytest.approx(494.3, abs=0.05)
    assert metrics["spurious_triggers_per_second"] == pytest.approx(
        expected_spurious / 494.3, abs=1e-6
    )

    scope = metrics["premature_scope"]
    assert metrics["premature_rate_pct"] == pytest.approx(
        100.0 * metrics["n_premature"] / scope["denominator"], abs=0.05
    )


def test_rate_uses_time_not_round_count_as_its_denominator():
    """★ 速率的量纲必须是**时间**，不是轮数.

    ★ 为什么单列一条：把「每秒误触发」实现成「误触发次数 ÷ 轮数」是一个
    看不出来的退化 —— 数字仍在 0..1 之间、仍在「变小就好」的方向上，
    只是含义悄悄从「频率」变成了「比例」。夹具刻意做成**不等距**，
    于是两种口径**必然给出不同的数**，这条断言才有分辨力。
    """
    metrics = T.timing_block(_fixture_rows())["metrics"]
    by_rounds = metrics["n_spurious"] / metrics["n_rounds"]
    by_time = metrics["spurious_triggers_per_second"]
    assert by_time != pytest.approx(by_rounds, abs=1e-9), (
        "速率恰好等于「次数 ÷ 轮数」⇒ 要么夹具退化成等距，要么实现用错了分母"
    )


# ---------------------------------------------------------------------------
# 3. ★ 耗时出处（本票最核心的一条纪律）
# ---------------------------------------------------------------------------


def test_latency_comes_from_the_decision_chain_round_stamp():
    """#158 AC: 耗时来自决策链路自身的轮次打点."""
    block = T.timing_block(_fixture_rows())
    provenance = block["latency_source_block"]
    assert provenance["source"] == T.LATENCY_SOURCE_CHAIN_STAMP
    assert provenance["legal"] is True
    assert set(provenance["forbidden"]) == {
        T.LATENCY_SOURCE_FRAME_TS,
        T.LATENCY_SOURCE_EVENT_TS_DIFF,
    }


@pytest.mark.parametrize(
    "illegal",
    [T.LATENCY_SOURCE_FRAME_TS, T.LATENCY_SOURCE_EVENT_TS_DIFF],
)
def test_frame_or_event_ts_as_latency_source_is_flagged(illegal: str):
    """★ 用帧采集端时间戳 / 事件 ts 差值 ⇒ 该块**不可用作时机结论**.

    ★ 本票正文：帧上的时间戳是**采集端墙钟**，用它会把网络与编码抖动算进
    「模型反应慢」，得出错误的归因。故它不是「少一个数据」，而是**错的数据**。
    """
    block = T.timing_block(synthetic_healthy_rows(), latency_source=illegal)
    assert block["latency_source_block"]["legal"] is False
    caveats = " ".join(block["caveats"])
    assert "不是决策链路的轮次打点" in caveats, caveats


def test_ts_is_never_used_as_a_latency():
    """★ ``ts`` 只作速率分母；任何耗时都不得由它推出.

    ★ 判法：把所有 ``ts`` 抹掉之后，``onset_latency_ms`` 必须**一字不变**。
    若实现里有任何一处用 ``ts`` 参与耗时，这个断言就会红。
    """
    rows = _fixture_rows()
    without_ts = [{**r, "ts": ""} for r in rows]
    before = T.timing_block(rows)["metrics"]["onset_latency_ms"]
    after = T.timing_block(without_ts)["metrics"]["onset_latency_ms"]
    for key in ("median", "p90", "min", "max", "n"):
        assert before[key] == after[key], f"{key} 随 ts 变化 ⇒ ts 参与了耗时计算"


# ---------------------------------------------------------------------------
# 4. ★ 负控：删掉轮次打点 ⇒ 不可测（而不是退回用墙钟）
# ---------------------------------------------------------------------------


def test_dropping_round_stamps_makes_onset_unmeasurable():
    """★ #158 ★ 负控：删掉轮次打点 ⇒ onset **不可测**（而不是静默出数）."""
    block = T.timing_block(synthetic_unstamped_rows())
    onset = block["metrics"]["onset_latency_ms"]
    assert onset["median"] is None, "删掉打点后 onset 仍出数 ⇒ 实现退回了用墙钟补数"
    assert onset["p90"] is None
    assert onset["n"] == 0
    assert onset["n_missing_stamp"] >= 1, "缺失必须被**显式计数**"


def test_dropping_round_stamps_leaves_ts_in_place():
    """★ 上一条负控之所以有意义，靠的是这条：``ts`` **仍在场**.

    若 ``ts`` 也被删了，那「onset 不可测」只能证明输入是空的，
    **证明不了**实现没有用 ``ts`` 兜底。
    """
    block = T.timing_block(synthetic_unstamped_rows())
    assert block["metrics"]["session_seconds"] > 0, (
        "负控夹具必须保留 ts（否则证明不了「没退回用 ts」）"
    )


def test_unstamped_rounds_are_named_not_silently_skipped():
    """缺打点的轮次必须**被点名** —— 静默跳过会让缺口看不出来."""
    block = T.timing_block(synthetic_unstamped_rows())
    onset = block["metrics"]["onset_latency_ms"]
    assert onset["missing_stamp_ids"], "缺打点的轮次 id 必须列出"
    assert len(onset["missing_stamp_ids"]) == onset["n_missing_stamp"]


# ---------------------------------------------------------------------------
# 5. 分母纪律：「没测」不是「测到 0」
# ---------------------------------------------------------------------------


def test_always_silent_stub_gets_no_premature_number():
    """★ 永远沉默的桩：premature 的分母为 0 ⇒ 必须是 ``None``，不是 0.0.

    一个 0.0 的抢话率会被读成「表现完美」；真相是「这个桩根本没开口」。
    """
    metrics = T.timing_block(synthetic_silent_rows())["metrics"]
    assert metrics["n_speaking"] == 0
    assert metrics["premature_rate_pct"] is None, "分母为 0 不得给出一个像分数的数字"
    assert metrics["n_premature"] == 0, "计数仍然是 0（「一次都没有」是事实）"


def test_missing_timebase_yields_none_not_zero():
    """没有时间基准 ⇒ 速率是 ``None``，不得是 0.0."""
    rows = [{**r, "ts": ""} for r in synthetic_healthy_rows()]
    metrics = T.timing_block(rows)["metrics"]
    assert metrics["session_seconds"] == 0
    assert metrics["spurious_triggers_per_second"] is None
    assert metrics["spurious_triggers_per_minute"] is None


def test_not_applicable_and_unlabeled_are_counted_separately():
    """★ 「不适用」（主动轮）与「未标注」（数据缺口）必须分开.

    ★ 合成一个计数会让一个**纯粹的主动轮配置**永远显示「有缺口」，
    而真假缺口混在一起时读者会学会忽略这个数字 —— 那比不报更坏。
    """
    rows = synthetic_healthy_rows()
    proactive = {**rows[0], "round_kind": "proactive", "id": "proactive-1"}
    rows.append(proactive)
    scope = T.timing_block(rows)["metrics"]["premature_scope"]
    assert scope["n_not_applicable_proactive"] == 1
    assert scope["n_unlabeled"] == 0, "主动轮不得被算作「未标注」"
    assert scope["complete"] is True


def test_unlabeled_speaking_round_blocks_the_rate():
    """一次开口缺标注 ⇒ premature 判「不可测」而不是算出一个偏小的率."""
    rows = [
        {k: v for k, v in r.items() if k != T.FIELD_STILL_SPEAKING}
        for r in synthetic_healthy_rows()
    ]
    metrics = T.timing_block(rows)["metrics"]
    scope = metrics["premature_scope"]
    assert scope["n_unlabeled"] >= 1
    assert metrics["premature_rate_pct"] is None


# ---------------------------------------------------------------------------
# 6. 误触发的口径与定向轴同源
# ---------------------------------------------------------------------------


def test_delegation_counts_as_spurious():
    """``delegation`` 必须算作开口（它会触发外部检索 + 播报，比应答更糟）."""
    rows = [
        {
            "id": "x",
            "session_id": "s",
            "ts": "2026-09-22T05:00:00.000Z",
            "round_kind": "user",
            "decision": "delegation",
            "expected": T.EXPECTED_QUIET,
            "ok": True,
            "latency_ms": 400,
        }
    ]
    assert T.is_spurious(rows[0]) is True
    assert T.timing_block(rows)["metrics"]["n_spurious"] == 1


def test_speaking_and_quiet_vocabulary_is_shared_with_the_directed_axis():
    """★ 「开口」的定义只有一份（import 自定向轴），不是本模块另立的.

    两份定义迟早分叉，而分叉的那一份是没被测过的 —— 本仓反复付费学过的形态。
    更实际的风险：两条轴会对同一份数据给出**互相矛盾**的「开口」判定。
    """
    from decision_eval_axis import DECISIONS_QUIET, DECISIONS_SPEAKING

    speaking = {**synthetic_healthy_rows()[0], "decision": DECISIONS_SPEAKING[0]}
    quiet = {**synthetic_healthy_rows()[0], "decision": DECISIONS_QUIET[0]}
    assert T.spoke(speaking) is True
    assert T.spoke(quiet) is False


def test_failed_round_is_not_counted_as_quiet():
    """★ 失败行是「没测到」，不得被当成「不开口」."""
    failed = {**synthetic_healthy_rows()[0], "ok": False, "decision": "response"}
    assert T.spoke(failed) is False
    assert T.decision_of(failed) == "error"


# ---------------------------------------------------------------------------
# 7. 禁止合成单一 accuracy（父 spec §三）
# ---------------------------------------------------------------------------


def test_forbidden_combined_keys_scans_nested_structures():
    """★ 合并分数键的扫描必须**递归**（嵌套一层的 accuracy 也要抓到）."""
    assert T.forbidden_combined_keys({"accuracy": 1}) == ["accuracy"]
    assert T.forbidden_combined_keys({"a": {"Accuracy": 1}}) == ["a.Accuracy"]
    assert T.forbidden_combined_keys({"a": [{"f1": 1}]}) == ["a[0].f1"]
    assert T.forbidden_combined_keys({"n_rounds": 3}) == []


def test_timing_block_reports_no_combined_keys():
    """本模块自己的产物必须干净（判据只是第二道门）."""
    block = T.timing_block(_fixture_rows())
    assert block["forbidden_combined_keys"] == []


# ---------------------------------------------------------------------------
# 8. 帧/事件/夹具的对齐（静默错位是缺陷本身）
# ---------------------------------------------------------------------------


def test_misaligned_truth_fails_loud(tmp_path: Path):
    """事件与真值对不上 ⇒ **报错**，不静默产出读数."""
    truth = json.loads(TIMING_TRUTH.read_text(encoding="utf-8"))
    truth["labels"][0] = {**truth["labels"][0], "ts": "2026-09-22T23:59:59.000Z"}
    bad = tmp_path / "truth.json"
    bad.write_text(json.dumps(truth, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        build_rows(TIMING_EVENTS, bad)
    assert "对不上" in str(exc.value)


def test_empty_event_stream_is_flagged_not_treated_as_zero(tmp_path: Path):
    """空事件流 ⇒ 报错（**不是**「零误触发」）."""
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        build_rows(empty, TIMING_TRUTH)
    assert "没有任何 live_decision" in str(exc.value)


def test_missing_truth_file_fails_loud(tmp_path: Path):
    """缺真值文件 ⇒ 报错，不退回一个默认真值."""
    with pytest.raises(FileNotFoundError):
        build_rows(TIMING_EVENTS, tmp_path / "absent.json")


def test_unmapped_expected_action_is_rejected(monkeypatch: pytest.MonkeyPatch):
    """新的 expected_action 未被映射 ⇒ 报错（不默认成「不该开口」）."""
    import decision_eval_timing_sources as S

    monkeypatch.setattr(S, "CASES", (("X1", "t", "g", "c", "n", "brand-new-action"),))
    with pytest.raises(ValueError) as exc:
        S.expected_by_case_id()
    assert "未映射" in str(exc.value)


# ---------------------------------------------------------------------------
# 9. 合成夹具自身的两条不变式
# ---------------------------------------------------------------------------


def test_synthetic_plan_labels_every_speaking_round():
    """★ 不变式：每个开口轮都要有 premature 标注（否则分母不完整）."""
    for row in synthetic_healthy_rows():
        if T.spoke(row):
            assert row.get(T.FIELD_STILL_SPEAKING) is not None, row["id"]


def test_synthetic_plan_stamps_every_speaking_round():
    """★ 不变式：干净基线里每个开口轮都要有轮次打点（否则恒等对照会脏）."""
    for row in synthetic_healthy_rows():
        if T.spoke(row):
            assert row.get("latency_ms"), row["id"]


def test_synthetic_fixture_needs_no_directed_axis_import():
    """★ 合成夹具与轴的读数**不依赖定向轴模块**的产物/判据.

    ★ 为什么要钉这条：两轴的夹具若互相依赖，一轴改动会让另一轴的自检
    变红或变绿，而「哪一轴坏了」就再也说不清。这里用**子进程**真跑一次
    （在同一个 pytest 进程里 ``decision_eval_card`` 早已被别的测试 import 过，
    检查 ``sys.modules`` 证明不了任何事）。
    """
    import subprocess
    import sys

    code = (
        "import sys;"
        "import decision_eval_timing as T;"
        "from decision_eval_timing_synthetic import synthetic_healthy_rows;"
        "T.timing_block(synthetic_healthy_rows());"
        "assert 'decision_eval_card' not in sys.modules, '夹具牵进了定向轴模块';"
        "print('ok')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(Path(__file__).resolve().parent.parent),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout
