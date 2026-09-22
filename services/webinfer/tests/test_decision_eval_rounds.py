# ruff: noqa: RUF001, RUF002, RUF003
# (RUF001/002/003 = ambiguous fullwidth punctuation; this module's prose is
# Chinese and quotes real test sentences. Same established repo convention as
# decision_eval_set.py / decision_eval_score.py.)
"""多轮取中位 + 离散度的行为测试（工单 #165，父 spec #161 §D5）。

★ 为什么这些测试必须存在
------------------------
本模块的输出会变成**结论**（「决策评测得分 X%」），而 #165 的出发点是
「**只报中位而不报离散度，会把「稳定」与「碰巧」混为一谈**」。
所以这里断言的核心不是「能算出中位数」，而是：

1. **离散度真的在度量离散** —— ★ 负控：注入高方差桩 ⇒ 离散度**明显上升**；
   反向对照：三轮完全一致 ⇒ 离散度**恒为 0**。
2. **中位数与单轮值并列** —— 只有中位数没有单轮值 ⇒ 断言转红。
3. **分母跨轮统一** —— 某轮少一句必须**报错**，不得静默缩小分母
   （历史 25 vs 26 就是这么静默污染过跨变体比较的）。
4. **可比性变更被显式处理** —— 与历史文件比较要先统一到 25/25 口径。

每条都配负控：本项目最贵的教训是「判据能跑绿 ≠ 判据能分辨对错」（台账 §6.4）。

Run: cd services/webinfer && python -m pytest tests/test_decision_eval_rounds.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import decision_eval_rounds as rounds  # noqa: E402
import decision_eval_score as score  # noqa: E402
from decision_eval_set import (  # noqa: E402
    CASES,
    GROUP_DELEGATE,
    GROUP_DIRECTED,
    GROUP_NONDIRECTED,
    HISTORICAL_COUNTS,
)

_PERFECT_DECISION = {
    GROUP_DIRECTED: "response",
    GROUP_NONDIRECTED: "not-for-me",
    GROUP_DELEGATE: "delegation",
}


def _rows(decision_for_group=None, *, drop_ids=(), break_ids=()):
    """Build one round's rows; ``decision_for_group`` overrides the perfect map."""
    mapping = dict(_PERFECT_DECISION)
    if decision_for_group:
        mapping.update(decision_for_group)
    rows = []
    for cid, _text, group, *_rest in CASES:
        if cid in drop_ids:
            continue
        row = {"id": cid, "expected": group, "decision": mapping[group], "ok": True}
        if cid in break_ids:
            row = {"id": cid, "expected": group, "ok": False}
        rows.append(row)
    return rows


def _stable_rounds(n=3):
    """N rounds that are byte-identical in outcome ⇒ dispersion must be 0."""
    return [score.summarize(_rows()) for _ in range(n)]


# ---------------------------------------------------------------------------
# 1. 中位数与单轮值**并列**
# ---------------------------------------------------------------------------


def test_series_reports_median_alongside_every_single_round():
    """★ AC#2：中位数与单轮值必须**并列**，不得只报其一。"""
    series = rounds.aggregate_series([10.0, 20.0, 60.0])
    assert series["per_round"] == [10.0, 20.0, 60.0]
    assert series["median"] == 20.0
    assert series["single_round_first"] == 10.0
    assert series["n"] == 3


def test_median_is_the_middle_value_not_the_mean():
    """中位数与均值在偏斜样本上必须**不同** —— 否则用的是均值（AC 说的是中位）。"""
    series = rounds.aggregate_series([10.0, 20.0, 90.0])
    assert series["median"] == 20.0
    assert series["median"] != round(sum([10.0, 20.0, 90.0]) / 3, 3)


def test_even_round_count_medians_by_interpolation():
    series = rounds.aggregate_series([10.0, 20.0, 30.0, 40.0])
    assert series["median"] == 25.0


# ---------------------------------------------------------------------------
# 2. ★ 离散度：度量离散，而不是装饰
# ---------------------------------------------------------------------------


def test_identical_rounds_have_zero_dispersion():
    """反向对照：三轮完全一致 ⇒ 离散度**恒为 0**（防止「恒 > 0」的假判据）。"""
    series = rounds.aggregate_series([42.0, 42.0, 42.0])
    assert series["dispersion"]["stdev"] == 0.0
    assert series["dispersion"]["range"] == 0.0
    assert series["dispersion"]["mad"] == 0.0


def test_negcontrol_high_variance_stub_raises_dispersion():
    """★★ AC#5 负控：注入高方差的桩结果 ⇒ 离散度**明显上升**。

    这条是整票的核心：若离散度是装饰（恒 0、或对输入不敏感），本测试转红。
    """
    stable = _stable_rounds(3)
    stable_series = rounds.aggregate_rounds(stable)["metrics"]["not_for_me_recall_pct"]
    assert stable_series["dispersion"]["stdev"] == 0.0

    # 把第 3 轮换成「非面向句全误响应」的桩 —— 只有这一轮变
    stubbed = [*_stable_rounds(2), score.summarize(_rows({GROUP_NONDIRECTED: "response"}))]
    stub_series = rounds.aggregate_rounds(stubbed)["metrics"]["not_for_me_recall_pct"]

    assert stub_series["dispersion"]["stdev"] > stable_series["dispersion"]["stdev"], (
        "注入高方差桩后离散度没上升 ⇒ 该字段没在度量离散"
    )
    assert stub_series["dispersion"]["range"] > 0.0
    # ★ 而**中位数应当岿然不动**：这正是选它而非均值的理由 ——
    #   单轮离群不会把结论带走。这条断言与上一条一起看才有意义：
    #   离散度上升 + 中位数不变 = 「结论稳定，但你要知道它抖过」。
    assert stub_series["median"] == stub_series["single_round_first"] == 100.0
    assert stub_series["dispersion"]["stdev"] > 0, "抖动必须被记录，不能被中位数抹平"


def test_single_outlier_round_leaves_the_median_alone():
    """★ 反向性质：一轮掉到 0 不改变中位（3 轮下），但**必须**推高离散度。

    这是「多轮取中位」这个设计的**可证伪陈述**：若实现取的是均值，
    第一条断言转红；若离散度是装饰，第二条转红。
    """
    series = rounds.aggregate_series([100.0, 100.0, 0.0])
    assert series["median"] == 100.0
    assert series["dispersion"]["stdev"] > 0
    assert series["dispersion"]["range"] == 100.0
    # 对照：均值会被这一轮带走 ⇒ 两者必须不同，否则用的是均值
    assert series["median"] != round(sum([100.0, 100.0, 0.0]) / 3, 3)


def test_dispersion_magnitude_tracks_the_spread():
    """离散度必须**单调**跟着展布走：展布更大 ⇒ stdev 更大。"""
    small = rounds.aggregate_series([50.0, 52.0, 54.0])["dispersion"]["stdev"]
    large = rounds.aggregate_series([10.0, 50.0, 90.0])["dispersion"]["stdev"]
    assert large > small


def test_dispersion_reports_n_and_absolute_spread():
    """只给一个 stdev 不足以读懂 —— 必须同时给出 n / min / max / range / mad。"""
    disp = rounds.aggregate_series([10.0, 50.0, 90.0])["dispersion"]
    assert disp["n"] == 3
    assert disp["min"] == 10.0
    assert disp["max"] == 90.0
    assert disp["range"] == 80.0
    assert disp["mad"] == 40.0


def test_dispersion_survives_a_zero_median_without_inf():
    """中位数为 0 时相对离散度不得变成 inf/NaN（会污染 JSON）。"""
    disp = rounds.aggregate_series([0.0, 0.0, 0.0])["dispersion"]
    assert disp["relative_stdev_pct"] is None


def test_dispersion_values_are_rounded_not_float_residue():
    """★ AC#3 的前置：所有离散量必须取整到 3 位。

    真机实测写出过 ``range: 11.600000000000001`` —— 那种残渣会让两次运行
    的结果文件**逐字节**不同却毫无语义差异，直接破坏「可 diff」。
    """
    disp = rounds.aggregate_series([19.2, 30.8, 30.8])["dispersion"]
    for key in ("min", "max", "range", "stdev", "mad"):
        value = disp[key]
        assert round(value, 3) == value, f"{key}={value!r} 带浮点残渣"


# ---------------------------------------------------------------------------
# 2b. ★ 分母退化轮：比率的 0.0 有两种相反含义
# ---------------------------------------------------------------------------


def test_degenerate_denominator_rounds_are_flagged():
    """★★ 真机暴露的缺陷：某轮「一条 not-for-me 都没预测」⇒ 精确率分母为 0。

    此时 ``summarize`` 记 0.0，与「预测了但全错」的 0.0 **数值相同、含义相反**，
    并使跨轮 stdev 虚高（真机实测 47.14 —— 那是分母退化，不是模型抖动）。
    报告必须**点明是哪几轮**，否则读者会把退化当成抖动。
    """
    perfect = score.summarize(_rows())
    # 造一个 nfm_pred=0 的轮：所有行都判 response
    no_nfm = score.summarize(_rows({GROUP_NONDIRECTED: "response", GROUP_DELEGATE: "response"}))
    assert no_nfm["n_not_for_me_predicted"] == 0

    report = rounds.aggregate_rounds([perfect, no_nfm, perfect])
    degenerate = report["metrics_note"]["degenerate_rounds"]
    assert degenerate["not_for_me_precision_pct"] == [2]
    assert "含义相反" in report["metrics_note"]["why"]


def test_no_degenerate_flag_when_every_round_predicts_something():
    """反向对照：每轮都预测了 not-for-me ⇒ 不得乱标退化。"""
    report = rounds.aggregate_rounds(_stable_rounds(3))
    assert report["metrics_note"]["degenerate_rounds"] == {}


def test_delegate_recall_0_from_absent_group_is_flagged_not_read_as_failure():
    """★★ 存量历史文件的陷阱：没有 delegate 组 ⇒ 该比率被记 0.0。

    读起来像「委派全失败」，实际是**该态当年根本没被测**（#155 才新增该组）。
    报告必须点明，否则与「测了但 0 召回」无法区分。
    """
    rows = [r for r in _rows() if r["expected"] != GROUP_DELEGATE]
    stats = score.summarize(rows)
    assert stats["delegate_recall_pct"] == 0.0
    assert stats["n_delegate"] == 0

    report = rounds.aggregate_rounds([stats, stats])
    assert report["metrics_note"]["degenerate_rounds"]["delegate_recall_pct"] == [1, 2]


def test_degenerate_map_covers_every_percentage_metric():
    """★ 结构性守卫：每条比率都必须登记分母，否则下一个 0.0 又会骗人。

    ⚠️ 这条只守**覆盖**（每条都有登记），**不守正确性** —— 见下一条。
    """
    for metric in rounds.AGGREGATED_METRICS:
        if metric.endswith("_pct"):
            assert metric in rounds.RATIO_DENOMINATORS, (
                f"{metric} 没有登记分母 ⇒ 它的 0.0 无法区分「没测」与「全错」"
            )
            assert rounds.RATIO_DENOMINATORS[metric] in rounds.AGGREGATED_METRICS, (
                f"{metric} 的分母计数器 {rounds.RATIO_DENOMINATORS[metric]} 未被聚合"
            )


#: ★ **独立金标**：每条比率的分母，由**指标名本身的语义**推出
#: （「A 比 B」→ 分母是 B 的规模），不是从实现里抄的。
#: 之所以要在测试里再写一遍：上一条只断言「登记了某个计数器」，
#: 对 3/5 条比率是**恒真**的 —— 变异测试证明把
#: `baseline_mis_response_rate_pct` 的分母改成 `n_directed`、
#: `not_for_me_recall_pct` 改成 `n_delegate`、
#: `directed_miss_rate_pct` 改成 `n_nondirected`，全都**绿着通过**。
#: 分母登记错 = 退化轮会被判在不该判的地方（fail-open），必须逐条钉住。
EXPECTED_DENOMINATORS: dict[str, str] = {
    # 「非面向句里被误响应的比例」→ 分母 = 非面向句数
    "baseline_mis_response_rate_pct": "n_nondirected",
    # 「预测为 not-for-me 里真的比例」→ 分母 = 预测为 not-for-me 的条数
    "not_for_me_precision_pct": "n_not_for_me_predicted",
    # 「非面向句里被认出的比例」→ 分母 = 非面向句数
    "not_for_me_recall_pct": "n_nondirected",
    # 「面向句里被漏判的比例」→ 分母 = 面向句数
    "directed_miss_rate_pct": "n_directed",
    # 「委派句里被委派的比例」→ 分母 = 委派句数
    "delegate_recall_pct": "n_delegate",
}


def test_ratio_denominators_match_an_independent_oracle():
    """★★ 逐条比对独立金标 —— 这条才真正守**正确性**（杀死分母换错）。"""
    assert rounds.RATIO_DENOMINATORS == EXPECTED_DENOMINATORS


@pytest.mark.parametrize(("ratio", "counter"), sorted(EXPECTED_DENOMINATORS.items()))
def test_zeroing_the_registered_denominator_flags_exactly_that_ratio(ratio, counter):
    """★ 行为验证：把**该**比率的分母清零 ⇒ 它被点名。

    与金标比对互补：金标守「静态声明对不对」，本条守「声明真的接进了判定」。

    分母分两类，构造方式不同：
    * ``n_directed`` / ``n_nondirected`` / ``n_delegate`` 是**句集规模**，
      只能靠**取子集**清零（决策改不了它们）；
    * ``n_not_for_me_predicted`` 是**预测计数**，靠把决策都改成非 not-for-me 清零。
    """
    if counter == "n_not_for_me_predicted":
        stats = score.summarize(_rows({GROUP_NONDIRECTED: "response", GROUP_DELEGATE: "response"}))
    else:
        # 只保留「不含该组」的行 ⇒ 该组句数变 0
        excluded = {
            "n_directed": GROUP_DIRECTED,
            "n_nondirected": GROUP_NONDIRECTED,
            "n_delegate": GROUP_DELEGATE,
        }[counter]
        stats = score.summarize([r for r in _rows() if r["expected"] != excluded])

    assert stats[counter] == 0, f"构造失败：{counter} 应为 0，实际 {stats[counter]}"
    flagged = rounds.aggregate_rounds([stats, stats])["metrics_note"]["degenerate_rounds"]
    assert ratio in flagged, f"{counter}=0 时 {ratio} 应被点名"


def test_a_zero_denominator_does_not_flag_unrelated_ratios():
    """★★ 反向：分母为 0 只能点名**用它的**那些比率，不得牵连别的。

    这条是杀死「分母换错」变异体的关键 —— 例如把
    ``directed_miss_rate_pct`` 的分母错登记成 ``n_nondirected`` 时，
    一个**全是面向句**的轮（``n_nondirected=0``）就会把「面向句漏判率」
    误判成退化，而那一轮它明明算得出来。
    """
    # 全是面向句 ⇒ n_nondirected = 0、n_delegate = 0，但 n_directed > 0
    directed_only = [r for r in _rows() if r["expected"] == GROUP_DIRECTED]
    stats = score.summarize(directed_only)
    assert stats["n_nondirected"] == 0 and stats["n_directed"] == len(directed_only)

    flagged = rounds.aggregate_rounds([stats, stats])["metrics_note"]["degenerate_rounds"]
    assert "directed_miss_rate_pct" not in flagged, (
        "全是面向句时「面向句漏判率」的分母是 n_directed（非 0），不该被判退化"
    )
    assert "not_for_me_recall_pct" in flagged, "非面向句为 0 ⇒ 召回率确实退化"

    # 全是非面向句 ⇒ n_directed = 0，但 n_nondirected > 0
    nondirected_only = [r for r in _rows() if r["expected"] == GROUP_NONDIRECTED]
    stats2 = score.summarize(nondirected_only)
    assert stats2["n_directed"] == 0
    flagged2 = rounds.aggregate_rounds([stats2, stats2])["metrics_note"]["degenerate_rounds"]
    assert "baseline_mis_response_rate_pct" not in flagged2, (
        "全是非面向句时「误响应率」的分母是 n_nondirected（非 0），不该被判退化"
    )
    assert "directed_miss_rate_pct" in flagged2, "面向句为 0 ⇒ 漏判率确实退化"


def test_denominator_counters_are_aggregated_not_dropped():
    """★ 计数器必须逐轮聚合 —— 没有它就无法判断比率是否退化。"""
    report = rounds.aggregate_rounds(_stable_rounds(3))
    counters = report["metrics"]["n_not_for_me_predicted"]
    assert len(counters["per_round"]) == 3
    assert counters["median"] == counters["per_round"][0] > 0
    for counter in ("n_directed", "n_nondirected", "n_delegate"):
        assert len(report["metrics"][counter]["per_round"]) == 3


# ---------------------------------------------------------------------------
# 3. ★ 分母跨轮统一（历史 25 vs 26 的教训，不能再发生一次）
# ---------------------------------------------------------------------------


def test_fewer_than_two_rounds_fails_loud():
    """★ 单轮算不出离散度 —— 必须**报错**，不得默默回一个「中位数」。"""
    with pytest.raises(ValueError, match="轮"):
        rounds.aggregate_rounds([score.summarize(_rows())])


def test_dropped_case_across_rounds_fails_loud():
    """★ 某轮少一句 ⇒ 分母被静默改小 ⇒ 跨轮比较无效。必须报错。

    这正是 25 vs 26 缺陷的机制：分母不同而无人报警。
    """
    per_round_ids = [
        [cid for cid, *_ in CASES],
        [cid for cid, *_ in CASES],
        [cid for cid, *_ in CASES if cid != "N01b"],  # 少一句
    ]
    with pytest.raises(ValueError, match=r"分母|case id|句集"):
        rounds.aggregate_rounds(_stable_rounds(3), round_case_ids=per_round_ids)


def test_identical_case_id_sets_are_accepted():
    """正向对照：三轮句集一致 ⇒ 通过，并把分母钉进报告。"""
    ids = [cid for cid, *_ in CASES]
    report = rounds.aggregate_rounds(_stable_rounds(3), round_case_ids=[ids, ids, ids])
    denom = report["denominator"]
    assert denom["per_round_case_id_sets_identical"] is True
    assert denom["case_ids_total"] == len(CASES)
    assert denom["canonical"][GROUP_NONDIRECTED] == 26


def test_denominator_block_records_the_historical_delta():
    """★ AC#4：历史 25 与现集合 26 的差异必须在结果里**显式**出现。"""
    ids = [cid for cid, *_ in CASES]
    denom = rounds.aggregate_rounds(_stable_rounds(3), round_case_ids=[ids] * 3)["denominator"]
    assert denom["historical"][GROUP_NONDIRECTED] == 25
    assert denom["canonical"][GROUP_NONDIRECTED] == 26
    assert "N01b" in denom["note"]
    assert "25" in denom["note"] and "26" in denom["note"]


def test_historical_comparable_view_hits_the_historical_denominators():
    """★ AC#4 的**可执行**部分：统一口径后必须恰好回到历史的 25/25。

    「差异被显式处理」不是加一条注释，而是给出一条能真正对齐分母的路径。
    """
    view = rounds.historical_comparable_view(_rows())
    counts = score.summarize(view)
    assert counts["n_directed"] == HISTORICAL_COUNTS[GROUP_DIRECTED] == 25
    assert counts["n_nondirected"] == HISTORICAL_COUNTS[GROUP_NONDIRECTED] == 25
    assert counts["n_delegate"] == 0, "delegate 组是 #155 新增，历史文件里没有它"
    report = rounds.aggregate_rounds(
        [score.summarize(view) for _ in range(3)],
        round_case_ids=[[r["id"] for r in view]] * 3,
    )
    assert report["denominator"]["historical_comparable"]["n_directed"] == 25
    assert report["denominator"]["historical_comparable"]["n_nondirected"] == 25
    assert "N01b" in report["denominator"]["historical_comparable"]["excluded_ids"]


# ---------------------------------------------------------------------------
# 4. 逐句稳定性：把「12–14 例两轮不一致」变成报告里的字段
# ---------------------------------------------------------------------------


def test_case_stability_flags_only_the_cases_that_moved():
    per_round_rows = [
        _rows(),
        _rows({GROUP_NONDIRECTED: "response"}),  # 非面向句这一轮全部误响应
        _rows(),
    ]
    stability = rounds.case_stability(per_round_rows)
    assert stability["n_unstable"] == 26, "26 例非面向句应全部记为不稳定"
    assert stability["n_stable"] == len(CASES) - 26
    assert stability["n_unstable_by_group"][GROUP_NONDIRECTED] == 26
    assert stability["n_unstable_by_group"][GROUP_DIRECTED] == 0
    assert stability["unstable_ids"][0].startswith("N")


def test_case_stability_is_zero_when_every_round_agrees():
    """反向对照：三轮一模一样 ⇒ 不稳定句数恒为 0。"""
    stability = rounds.case_stability([_rows(), _rows(), _rows()])
    assert stability["n_unstable"] == 0
    assert stability["unstable_ids"] == []


def test_case_stability_treats_a_failed_row_as_a_decision_change():
    """★ 一轮里某句失败（``ok=False``）⇒ 它与其他轮的决策不同 ⇒ 记为不稳定。"""
    stability = rounds.case_stability([_rows(), _rows(break_ids={"D01"}), _rows()])
    assert stability["per_case"]["D01"]["stable"] is False
    assert "error" in stability["per_case"]["D01"]["decisions"]
    assert stability["n_unstable"] == 1


# ---------------------------------------------------------------------------
# 5. 开卷标注 + 运行成本（AC#7 / AC#6）
# ---------------------------------------------------------------------------


def test_open_book_block_matches_the_asset():
    """★ AC#7：是否「开卷」必须写进结果，且取自**算得**的重叠。"""
    import decision_eval_set as des

    block = rounds.open_book_report(des.production_live_prompt())
    assert block["is_open_book"] is True
    assert block["n_overlapping"] == 10
    assert block["n_overlapping_nondirected"] == 8
    assert len(block["overlapping_ids"]) == 10


def test_open_book_block_is_false_for_a_clean_prompt():
    block = rounds.open_book_report("完全不相干的 prompt")
    assert block["is_open_book"] is False
    assert block["n_overlapping"] == 0


def test_cost_block_records_rounds_and_wall_time():
    """★ AC#6：必须记 N 轮耗时，供后续调 N。"""
    cost = rounds.cost_report([10.0, 12.0, 11.0], llm_calls=168)
    assert cost["rounds"] == 3
    assert cost["wall_seconds_total"] == 33.0
    assert cost["wall_seconds_per_round"] == [10.0, 12.0, 11.0]
    assert cost["wall_seconds_per_round_mean"] == 11.0
    assert cost["llm_calls"] == 168
    assert cost["seconds_per_llm_call"] == pytest.approx(0.196, abs=0.001)
    assert cost["measured"] is True


def test_cost_block_handles_zero_calls_without_dividing_by_zero():
    cost = rounds.cost_report([1.0, 1.0], llm_calls=0)
    assert cost["seconds_per_llm_call"] is None


def test_unmeasured_cost_is_none_not_zero():
    """★★ 没测过时间 ⇒ 耗时字段一律 ``None``，**不得**给 0.0。

    这是 spec 轴查出的缺陷：``--from-results`` 从已落盘文件重新聚合时**没有**
    耗时数据，而实现曾用 ``[0.0] * n`` 填充 ⇒ 报告声称「单轮 0.0 秒」，
    那是**从没测过的数据里读出「测到了 0」** —— 与本模块对分母退化
    （0.0 = 测到 0 还是没测）所修的是**同一类**错误。
    """
    cost = rounds.cost_report(None, llm_calls=168)
    assert cost["measured"] is False
    assert cost["wall_seconds_total"] is None
    assert cost["wall_seconds_per_round"] is None
    assert cost["wall_seconds_per_round_mean"] is None
    assert cost["seconds_per_llm_call"] is None
    assert cost["rounds"] is None, "没测过就不该声称跑了 N 轮耗时"


def test_reaggregated_report_does_not_claim_a_measured_cost(tmp_path):
    """★ 端到端：从文件重新聚合出的报告，其 cost 必须是「未测」而非 0.0。"""
    variant = "V"
    p = tmp_path / "rounds.json"
    _write_results_file(p, variant, _rows())
    p2 = tmp_path / "rounds2.json"
    _write_results_file(p2, variant, _rows({GROUP_NONDIRECTED: "response"}))

    report = rounds.report_from_results_files([str(p), str(p2)], variant)
    assert report["cost"]["measured"] is False
    assert report["cost"]["wall_seconds_total"] is None
    assert report["cost"]["seconds_per_llm_call"] is None


def test_print_report_says_cost_unmeasured_instead_of_staying_silent(capsys):
    """★ 人读视图必须把「未测」写出来 —— 沉默会被读成「成本可忽略」。"""
    variant = "V"
    import json
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        a = Path(tmp) / "a.json"
        b = Path(tmp) / "b.json"
        for path, rows in ((a, _rows()), (b, _rows())):
            path.write_text(json.dumps({"results": {variant: {"rows": rows}}}), encoding="utf-8")
        report = rounds.report_from_results_files([str(a), str(b)], variant)

    rounds.print_report(report)
    out = capsys.readouterr().out
    assert "未测" in out, "cost 未测时既不该给 0.0，也不该什么都不说"
    assert "0.0s" not in out


# ---------------------------------------------------------------------------
# 6. 整份报告：结构化 + 可 diff
# ---------------------------------------------------------------------------


def _report():
    ids = [cid for cid, *_ in CASES]
    return rounds.build_rounds_report(
        [score.summarize(_rows()) for _ in range(3)],
        [
            [
                {"id": cid, "expected": g, "decision": _PERFECT_DECISION[g], "ok": True}
                for cid, _t, g, *_r in CASES
            ]
            for _ in range(3)
        ],
        prompt="（离线桩，不含重叠）",
        round_case_ids=[ids] * 3,
        round_wall_seconds=[10.0, 10.0, 10.0],
        llm_calls=168,
        rounds_requested=3,
    )


def test_report_carries_rounds_and_median_side_by_side():
    """★ AC#2：结果里 ``rounds`` 与「中位数 + 单轮值」必须同时在。"""
    report = _report()
    assert report["rounds_requested"] == 3
    assert report["rounds_completed"] == 3
    assert len(report["rounds"]) == 3
    metrics = report["metrics"]
    assert set(metrics) == set(rounds.AGGREGATED_METRICS)
    for name, series in metrics.items():
        assert len(series["per_round"]) == 3, f"{name} 缺单轮值"
        assert isinstance(series["median"], float)
        assert "dispersion" in series


def test_report_is_json_round_trippable_and_byte_stable():
    """★ AC#3：结果结构化、可 diff —— 同一输入两次序列化必须**逐字节相同**。"""
    import json

    first = json.dumps(_report(), ensure_ascii=False, sort_keys=True, indent=2)
    second = json.dumps(_report(), ensure_ascii=False, sort_keys=True, indent=2)
    assert first == second
    assert json.loads(first) == json.loads(second)


def test_report_names_every_denominator_and_the_open_book_status():
    report = _report()
    assert report["denominator"]["case_ids_total"] == len(CASES)
    assert report["open_book"]["is_open_book"] is False  # 桩 prompt 无重叠
    assert report["cost"]["rounds"] == 3


def test_report_fails_loud_when_rounds_are_missing():
    with pytest.raises(ValueError, match="轮"):
        rounds.aggregate_rounds([])


# ---------------------------------------------------------------------------
# 7. ★ 两次运行的 diff（AC#3：差异一眼可见）
# ---------------------------------------------------------------------------


def test_diff_marks_every_moved_metric():
    """★ AC#3：diff 必须**逐指标**列出，且标出中位数与离散度的变化。"""
    a = _report()
    ids = [cid for cid, *_ in CASES]
    b = rounds.build_rounds_report(
        [
            score.summarize(_rows()),
            score.summarize(_rows({GROUP_NONDIRECTED: "response"})),
            score.summarize(_rows()),
        ],
        [
            [
                {"id": cid, "expected": g, "decision": _PERFECT_DECISION[g], "ok": True}
                for cid, _t, g, *_r in CASES
            ]
            for _ in range(3)
        ],
        prompt="（离线桩，不含重叠）",
        round_case_ids=[ids] * 3,
        round_wall_seconds=[10.0, 10.0, 10.0],
        llm_calls=168,
        rounds_requested=3,
    )
    text = rounds.diff_reports(a, b)
    assert "not_for_me_recall_pct" in text
    assert "median" in text and "stdev" in text


def test_diff_reports_no_change_for_identical_reports():
    """反向对照：同一份报告自比 ⇒ 明确说「无差异」，而不是空白。"""
    text = rounds.diff_reports(_report(), _report())
    assert "无差异" in text or "no differences" in text.lower()


# ---------------------------------------------------------------------------
# 8. ★ 离线可跑的负控自检（``python -m decision_eval_rounds --self-check``）
# ---------------------------------------------------------------------------


def test_self_check_passes_and_prints_both_dispersions(capsys):
    """★ AC#5 的可执行形态：自检必须真跑并把两侧离散度打出来。"""
    rc = rounds.self_check()
    out = capsys.readouterr().out
    assert rc == 0
    assert "stable" in out and "stub" in out
    assert "stdev" in out


def test_self_check_fails_when_dispersion_is_blind(monkeypatch):
    """★ 自检本身必须**可证伪**：把离散度换成恒 0 的桩 ⇒ 自检判红。

    否则「自检通过」只证明它在跑，不证明它分辨得出对错。
    """
    monkeypatch.setattr(rounds, "_dispersion_stdev", lambda _values: 0.0)
    assert rounds.self_check() != 0


# ---------------------------------------------------------------------------
# 9. ★ 从**已落盘**的轮次文件重新聚合（存量只有 2 轮，不重跑模型）
# ---------------------------------------------------------------------------


def _write_results_file(path, variant, rows):
    import json

    path.write_text(json.dumps({"results": {variant: {"rows": rows}}}), encoding="utf-8")


def test_report_from_results_files_aggregates_stored_rounds(tmp_path):
    """★ 存量 2 轮可以直接变成中位 + 离散度，**不需要**重跑模型。

    这正是历史文件的处境：两个文件各一轮。
    """
    variant = "P2_live4_prod_prompt_profile"
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    _write_results_file(a, variant, _rows())
    _write_results_file(b, variant, _rows({GROUP_NONDIRECTED: "response"}))

    report = rounds.report_from_results_files([str(a), str(b)], variant)
    assert report["rounds_completed"] == 2
    series = report["metrics"]["not_for_me_recall_pct"]
    assert series["per_round"] == [100.0, 0.0]
    assert series["median"] == 50.0
    assert series["dispersion"]["stdev"] > 0


def test_report_from_results_files_fails_loud_on_single_file(tmp_path):
    """★ 一个文件算不出离散度 —— 必须报错，不得给一个像结论的单轮数字。"""
    a = tmp_path / "a.json"
    _write_results_file(a, "v", _rows())
    with pytest.raises(ValueError, match="轮"):
        rounds.report_from_results_files([str(a)], "v")


def test_report_from_results_files_fails_loud_on_unknown_variant(tmp_path):
    """★ 负控：variant 名写错必须报错（不是静默聚合空集）。"""
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    _write_results_file(a, "real_variant", _rows())
    _write_results_file(b, "real_variant", _rows())
    with pytest.raises(KeyError, match="variant"):
        rounds.report_from_results_files([str(a), str(b)], "typo_variant")


def test_report_from_results_files_fails_loud_on_empty_rows(tmp_path):
    """★ 负控：某轮没有任何跑分行（全失败）不得被当作「一轮 0 分」。"""
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    _write_results_file(a, "v", _rows())
    _write_results_file(b, "v", [])
    with pytest.raises(KeyError, match="跑分行"):
        rounds.report_from_results_files([str(a), str(b)], "v")


def test_print_report_surfaces_median_dispersion_and_open_book(capsys):
    """★ 人读视图必须同时含中位、单轮、离散度与开卷标注。"""
    rounds.print_report(_report())
    out = capsys.readouterr().out
    for needle in ("median", "stdev", "range", "per_round", "开卷考", "逐句稳定性"):
        assert needle in out, f"人读视图缺 {needle!r}"


# ---------------------------------------------------------------------------
# 10. ★ 新格式（一个文件含 N 轮）也必须可重聚合 —— 否则证据是一次性的
# ---------------------------------------------------------------------------


def _write_multiround_file(path, variant, per_round_rows):
    import json

    path.write_text(
        json.dumps(
            {
                "results": {
                    variant: {
                        "rows": per_round_rows[-1],  # 末轮，仅为兼容旧读取方
                        "per_round_rows": per_round_rows,
                    }
                }
            }
        ),
        encoding="utf-8",
    )


def test_multiround_file_reaggregates_all_its_rounds(tmp_path):
    """★ 一个文件里的 3 轮必须被**全部**读出，不得塌成单轮。

    真机产物只存末轮 `rows` 的话，`--from-results` 就会以「不足 2 轮」拒绝
    —— 尽管文件里明明有三轮。那会让多轮证据变成**一次性的**。
    """
    variant = "P_live4_prod_prompt"
    p = tmp_path / "rounds.json"
    _write_multiround_file(
        p,
        variant,
        [_rows(), _rows({GROUP_NONDIRECTED: "response"}), _rows()],
    )
    report = rounds.report_from_results_files([str(p)], variant)
    assert report["rounds_completed"] == 3
    assert report["metrics"]["not_for_me_recall_pct"]["per_round"] == [100.0, 0.0, 100.0]
    assert report["metrics"]["not_for_me_recall_pct"]["dispersion"]["stdev"] > 0


def test_multiround_file_prefers_per_round_rows_over_the_last_round(tmp_path):
    """★ 有 `per_round_rows` 时**不得**回落到 `rows`（那只是末轮的副本）。

    负控：把 `rows` 换成与各轮都不同的内容，聚合结果必须只反映
    `per_round_rows` —— 若实现读了 `rows`，这条会看到单轮且报「不足 2 轮」。
    """
    variant = "V"
    p = tmp_path / "rounds.json"
    _write_multiround_file(p, variant, [_rows(), _rows()])
    report = rounds.report_from_results_files([str(p)], variant)
    assert report["rounds_completed"] == 2
    assert len(report["metrics"]["errors"]["per_round"]) == 2


def test_multiround_file_with_a_broken_round_fails_loud(tmp_path):
    """★ 负控：某轮为空 ⇒ 报错并点明是第几轮（不得静默丢一轮）。"""
    variant = "V"
    p = tmp_path / "rounds.json"
    _write_multiround_file(p, variant, [_rows(), []])
    with pytest.raises(KeyError, match="第 2 轮"):
        rounds.report_from_results_files([str(p)], variant)


def test_single_round_file_still_fails_loud(tmp_path):
    """反向：单文件且只有一轮 ⇒ 仍须报错（离散度无从谈起）。"""
    variant = "V"
    p = tmp_path / "one.json"
    _write_multiround_file(p, variant, [_rows()])
    with pytest.raises(ValueError, match="只读出 1 轮"):
        rounds.report_from_results_files([str(p)], variant)
