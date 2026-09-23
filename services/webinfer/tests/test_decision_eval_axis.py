# ruff: noqa: RUF001, RUF002, RUF003
# (RUF001/002/003 = ambiguous fullwidth punctuation; this module's prose is
# Chinese and quotes real sentences. Same established repo convention as
# decision_eval_set.py / decision_eval_score.py / decision_eval_rounds.py.)
"""定向轴的行为测试（工单 #157，父 spec #154）。

★ 为什么这些测试长这样
----------------------
本模块的产出会**变成结论**（「该不该开口，判断得对不对」），而 #157 的出发点
正是既有判据 `not-for-me precision >= 80%` **恒真**：它只把「预测成 not-for-me」
算作乱插，于是**一个永远输出 `</silence>` 的模型无条件通过**。

所以这里断言的核心不是「能算出一个百分比」，而是四件事：

1. **宽窄口径分开且都算对** —— delegation 必须算作「开口」（旧字段漏了它）。
2. **token 级证据真的在区分「判定沉默」与「空输出」** —— 且「字段缺失」与
   「空列表」不是一回事（前者是不可归因，后者是失效输出）。
3. ★ **每条判据都能被判红**（负控），且**健康输入上不做恒红**。
4. **分母为 0 时判「无法测量」，不判绿** —— 这正是空精度事故的成因。

每条断言都配一个负控或反向对照：本项目最贵的教训是
「判据能跑绿 ≠ 判据能分辨对错」（台账 §6.4）。

Run: cd services/webinfer && python -m pytest tests/test_decision_eval_axis.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import decision_eval_axis as axis  # noqa: E402
from decision_eval_set import (  # noqa: E402
    GROUP_DELEGATE,
    GROUP_DIRECTED,
    GROUP_NONDIRECTED,
    SUBSET_GENERALIZATION,
    SUBSET_OPEN_BOOK,
)

# --- 构造工具 ---------------------------------------------------------------


def rows(pairs: list[tuple[str, str, str]], *, tokens: bool = True) -> list[dict]:
    """``[(id, expected, decision), …]`` → 跑分行（带 token 证据）."""
    out = []
    for case_id, expected, decision in pairs:
        row = {"id": case_id, "expected": expected, "decision": decision, "ok": True}
        if tokens:
            row["emitted_token_ids"] = list(_TOKENS[decision])
        out.append(row)
    return out


_TOKENS: dict[str, list[int]] = {
    "silence": [151669, 151645],
    "response": [151670, 200],
    "delegation": [151670, 300],
    "not-for-me": [151670, 222, 99507],
}

#: 一份**零逐字重叠**的 prompt —— 全部行落在泛化子集，便于断言子集分列。
NEUTRAL_PROMPT = "（离线桩：与任何 case 文本都不逐字重叠）"


def perfect() -> list[dict]:
    """全对：面向→response、非面向→not-for-me、委派→delegation."""
    return rows(
        [
            ("D1", GROUP_DIRECTED, "response"),
            ("D2", GROUP_DIRECTED, "delegation"),
            ("N1", GROUP_NONDIRECTED, "not-for-me"),
            ("N2", GROUP_NONDIRECTED, "silence"),
            ("G1", GROUP_DELEGATE, "delegation"),
        ]
    )


# ---------------------------------------------------------------------------
# 1. 宽口径：开口 = response ∪ delegation
# ---------------------------------------------------------------------------


def test_delegation_counts_as_spurious_response_not_as_quiet():
    """★ 旧字段漏掉的那一半：非面向句被判 delegation 必须算误响应。

    实测依据：生产 prompt 在非面向句上出现过 2 例 ``</delegation>``
    （「把别人的话当任务派给后台」）—— 它会触发外部检索 + TTS 播报，
    **比单纯应答更糟**，而 ``baseline_mis_response_rate_pct`` 只数 response。
    """
    data = rows(
        [
            ("N1", GROUP_NONDIRECTED, "delegation"),
            ("N2", GROUP_NONDIRECTED, "delegation"),
            ("D1", GROUP_DIRECTED, "response"),
        ]
    )
    m = axis.axis_metrics(data)
    assert m["false_positives"] == 2, "delegation 必须计入误响应（宽口径）"
    assert m["nondirected_spurious_response_rate_pct"] == 100.0
    # ★ 窄口径并列，且**确实不同** —— 差异可见是本模块存在的理由之一。
    assert m["nondirected_response_only_rate_pct"] == 0.0
    assert m["nondirected_response_only_rate_pct"] != m["nondirected_spurious_response_rate_pct"]


def test_narrow_and_wide_miss_rates_differ_for_silenced_directed_cases():
    """★ 面向句被 ``silence`` 吞掉，旧字段（只数 not-for-me）恒为 0.0%。

    实测依据：生产 prompt 两轮各吞掉 2–3 例面向句（D19 稳定被吞），
    而 ``directed_miss_rate_pct`` 在全部变体上都是 **0.0%**。
    """
    data = rows(
        [
            ("D1", GROUP_DIRECTED, "silence"),
            ("D2", GROUP_DIRECTED, "response"),
            ("N1", GROUP_NONDIRECTED, "silence"),
        ]
    )
    m = axis.axis_metrics(data)
    assert m["directed_nonresponse_rate_pct"] == 50.0, "宽口径：silence 也是漏判"
    assert m["directed_nonresponse_as_not_for_me_pct"] == 0.0, "窄口径：只有 not-for-me 才算"
    assert m["directed_nonresponse_rate_pct"] != m["directed_nonresponse_as_not_for_me_pct"]


def test_delegate_group_is_excluded_from_the_directed_axis_but_still_counted():
    """delegate 不进定向轴，但**被计数**（不静默跳过）。"""
    m = axis.axis_metrics(perfect())
    assert m["n_directed"] == 2
    assert m["n_nondirected"] == 2
    assert m["n_delegate_excluded"] == 1
    assert m["false_positives"] == 0 and m["false_negatives"] == 0


def test_a_delegate_row_polluted_into_nondirected_does_not_hide():
    """把 delegate 句标成非面向 ⇒ 它必须进误响应分母，不得被静默排除。"""
    data = rows(
        [
            ("G1", GROUP_NONDIRECTED, "delegation"),
            ("D1", GROUP_DIRECTED, "response"),
        ]
    )
    m = axis.axis_metrics(data)
    assert m["false_positives"] == 1
    assert m["n_delegate_excluded"] == 0


# ---------------------------------------------------------------------------
# 2. 代价加权
# ---------------------------------------------------------------------------


def test_cost_index_is_weighted_and_fp_is_more_expensive_by_default():
    """★ 主指标按代价加权，默认让误响应比漏判更贵（对齐「宁可漏，不可乱插」）。"""
    data = rows(
        [
            ("D1", GROUP_DIRECTED, "silence"),  # 1 个漏判
            ("N1", GROUP_NONDIRECTED, "response"),  # 1 个误响应
            ("N2", GROUP_NONDIRECTED, "silence"),
        ]
    )
    m = axis.axis_metrics(data)
    assert (m["cost_fp_weight"], m["cost_fn_weight"]) == (3, 1), "默认必须是 FP 更贵"
    # 3×1 + 1×1 = 4 个代价单位 / 3 例
    assert m["cost_units"] == 4
    assert m["cost_index"] == pytest.approx(133.3, abs=0.05)


def test_cost_weights_are_configurable_and_the_ranking_flips():
    """★ 可配：把权重反过来，同一个输入的代价指数必须真的变小。"""
    data = rows(
        [
            ("D1", GROUP_DIRECTED, "silence"),
            ("D2", GROUP_DIRECTED, "silence"),
            ("N1", GROUP_NONDIRECTED, "response"),
            ("N2", GROUP_NONDIRECTED, "silence"),
        ]
    )
    fp_expensive = axis.axis_metrics(data, cost_fp=5, cost_fn=1)
    fn_expensive = axis.axis_metrics(data, cost_fp=1, cost_fn=5)
    assert fp_expensive["cost_units"] == 5 * 1 + 1 * 2
    assert fn_expensive["cost_units"] == 1 * 1 + 5 * 2
    assert fp_expensive["cost_index"] != fn_expensive["cost_index"]


def test_single_accuracy_is_not_produced_at_all():
    """★ 明确否决：本模块**不得**产出任何等权的 accuracy 字段。

    这是判据级的决定，不是风格问题 —— 单一 accuracy 会把
    「3 次乱插」与「3 次漏判」算成同一个数，抹平项目的代价偏好。
    """
    m = axis.axis_metrics(perfect())
    forbidden = [k for k in m if "accuracy" in k.lower()]
    assert not forbidden, f"单一 accuracy 已被本工单明确否决，却出现了 {forbidden}"


def test_break_even_ratio_is_reported_so_the_default_weight_is_questionable():
    """★ 默认权重 3 是**工程取值**（规范只给序关系）⇒ 必须给出可质疑的数字。"""
    data = rows(
        [
            ("N1", GROUP_NONDIRECTED, "response"),
            ("N2", GROUP_NONDIRECTED, "response"),
            ("D1", GROUP_DIRECTED, "silence"),
        ]
    )
    m = axis.axis_metrics(data)
    # FP=2, FN=1 ⇒ 等代价点 C_FP/C_FN = FN/FP = 0.5
    assert m["break_even_fp_fn_ratio"] == 0.5
    # ★ 0.5 < 3 ⇒ 默认权重把 FP 判得**比等代价更贵**（这正是「宁可漏」的方向）。
    assert m["break_even_fp_fn_ratio"] < m["cost_fp_weight"] / m["cost_fn_weight"]


def test_break_even_is_none_when_there_are_no_false_positives():
    """FP=0 时等代价点无定义 —— 给 ``None``，不给 0（0 会被读成「FP 免费」）。"""
    data = rows([("D1", GROUP_DIRECTED, "response"), ("N1", GROUP_NONDIRECTED, "silence")])
    assert axis.axis_metrics(data)["break_even_fp_fn_ratio"] is None


def test_trivial_baselines_are_reported_so_the_index_is_interpretable():
    """★ 平凡策略参照：没有它们，cost_index 只是一个孤立数字。"""
    m = axis.axis_metrics(perfect())
    # 「永远沉默」= C_FN × 面向句数；「永远开口」= C_FP × 非面向句数
    assert m["cost_index_always_silent"] == pytest.approx(100.0 * 1 * 2 / 4, abs=0.05)
    assert m["cost_index_always_speaking"] == pytest.approx(100.0 * 3 * 2 / 4, abs=0.05)


# ---------------------------------------------------------------------------
# 3. token 级证据：区分「判定沉默」与「空输出」
# ---------------------------------------------------------------------------


def test_silence_special_token_is_evidence_not_an_empty_output():
    """★ ``</silence>`` 是单 special token（151669）：它是**留痕**，不是空输出。"""
    row = {
        "id": "N1",
        "expected": GROUP_NONDIRECTED,
        "decision": "silence",
        "ok": True,
        "emitted_token_ids": [151669, 151645],
    }
    assert axis.quiet_evidence(row) == axis.EVIDENCE_EVIDENCED


def test_zero_tokens_on_a_quiet_decision_is_an_empty_output_not_a_judgment():
    """★ 空 token 列表 = 模型什么都没吐 ⇒ 失效输出，不得算作「判定沉默」。"""
    row = {
        "id": "N1",
        "expected": GROUP_NONDIRECTED,
        "decision": "silence",
        "ok": True,
        "emitted_token_ids": [],
    }
    assert axis.quiet_evidence(row) == axis.EVIDENCE_EMPTY_OUTPUT


def test_missing_token_field_is_not_the_same_as_an_empty_output():
    """★ 「字段缺失」与「空列表」**不是同一件事**。

    缺失既不是「有输出」的证据，也不是「零输出」的证据 ⇒ 记「不可归因」。
    把它记成 ``empty_output`` 会制造假警报；记成 ``evidenced`` 会把失效输出
    洗成一次正确的沉默。两者都是本工单要消灭的混淆。
    """
    without_field = {"id": "N1", "expected": GROUP_NONDIRECTED, "decision": "silence", "ok": True}
    with_empty = {**without_field, "emitted_token_ids": []}
    assert axis.quiet_evidence(without_field) == axis.EVIDENCE_NO_TOKEN_EVIDENCE
    assert axis.quiet_evidence(with_empty) == axis.EVIDENCE_EMPTY_OUTPUT
    assert axis.quiet_evidence(without_field) != axis.quiet_evidence(with_empty)


def test_contradictory_token_evidence_is_its_own_bucket():
    """★ 决策词说「沉默」而 token 证据说它在说话 ⇒ 单独成桶。

    实测依据：``not-for-me`` 行的首位 token 实测就是 ``</response>``（151670）
    —— 所以**不能**用首位 token 判「这一轮在不在说话」。这里记的是
    「``silence`` 决策却吐了非 151669 的首位 token」，那是解析器与模型不一致。
    """
    row = {
        "id": "N1",
        "expected": GROUP_NONDIRECTED,
        "decision": "silence",
        "ok": True,
        "emitted_token_ids": [151670, 200],
    }
    assert axis.quiet_evidence(row) == axis.EVIDENCE_CONTRADICTORY


def test_not_for_me_leading_with_response_token_is_not_contradictory():
    """★ ``not-for-me`` 首位实测就是 151670 ⇒ 不能算矛盾（否则会误报大量假红）。"""
    row = {
        "id": "N1",
        "expected": GROUP_NONDIRECTED,
        "decision": "not-for-me",
        "ok": True,
        "emitted_token_ids": [151670, 222, 99507],
    }
    assert axis.quiet_evidence(row) == axis.EVIDENCE_EVIDENCED


def test_speaking_rows_are_out_of_scope_rather_than_missing():
    """开口行不属于「沉默证据」的适用对象 —— 记 ``not_quiet``，不是「缺失」。"""
    row = {
        "id": "D1",
        "expected": GROUP_DIRECTED,
        "decision": "response",
        "ok": True,
        "emitted_token_ids": [151670, 200],
    }
    assert axis.quiet_evidence(row) == axis.EVIDENCE_NOT_QUIET


def test_evidence_coverage_counts_rows_that_cannot_be_attributed():
    """覆盖率必须把「不可归因」的行数显式给出（否则读者无法判断卡片可信度）。"""
    data = rows([("N1", GROUP_NONDIRECTED, "silence")], tokens=False) + rows(
        [("N2", GROUP_NONDIRECTED, "silence")]
    )
    block = axis.evidence_block(data)
    coverage = block["coverage"]
    assert coverage["quiet_rows"] == 2
    assert coverage["rows_without_token_evidence"] == 1
    assert coverage["rows_with_token_evidence"] == 1


def test_failed_rows_are_error_not_quiet():
    """★ 失败行是「没测到」，**不是**「判定沉默」。混入会把失效输出洗成正确沉默。"""
    row = {
        "id": "N1",
        "expected": GROUP_NONDIRECTED,
        "decision": "silence",
        "ok": False,
        "emitted_token_ids": [],
    }
    assert axis.decision_of(row) == axis.DECISION_ERROR
    assert axis.quiet_evidence(row) == axis.EVIDENCE_NOT_QUIET
    m = axis.axis_metrics([row])
    assert m["n_errors"] == 1
    assert m["true_quiet"] == 0, "失败行不得被计入「正确地没开口」"


# ---------------------------------------------------------------------------
# 4. 分母为 0 ⇒ None（不是 0.0）—— 空精度事故的直接修法
# ---------------------------------------------------------------------------


def test_precision_is_none_not_zero_when_nothing_was_predicted():
    """★ 「没测」与「测到 0」必须可分。

    旧 ``summarize`` 在分母为 0 时把比率记 ``0.0``，于是「一条 not-for-me
    都没预测」与「预测了但全错」**数值相同、含义相反** —— 那正是 100% 空精度
    那次事故的成因。本模块一律给 ``None``。
    """
    data = rows([("N1", GROUP_NONDIRECTED, "silence"), ("D1", GROUP_DIRECTED, "response")])
    m = axis.axis_metrics(data)
    assert m["not_for_me_predicted"] == 0
    assert m["not_for_me_precision_pct"] is None
    assert m["not_for_me_precision_pct"] != 0.0


def test_measured_zero_is_still_zero_not_none():
    """反向对照：真的测到了 0 就必须给 0.0（不能一律 None，那是另一种混淆）。"""
    data = rows(
        [
            ("D1", GROUP_DIRECTED, "not-for-me"),
            ("N1", GROUP_NONDIRECTED, "response"),
        ]
    )
    m = axis.axis_metrics(data)
    assert m["not_for_me_predicted"] == 1
    assert m["not_for_me_precision_pct"] == 0.0, "预测了但全错 ⇒ 测到 0，不是 None"
    assert m["not_for_me_recall_pct"] == 0.0


def test_axis_block_reports_per_subset_separately():
    """★ 两个子集**分别给出**，不混算（父 spec §四）。

    子集归属由**对被测 prompt 做逐字匹配**算得 —— 所以这里必须把**真实句子文本**
    放进 prompt，放 case id 是没用的（那正是「手写标签」的形态）。
    """
    from decision_eval_set import CASES

    text_of = {cid: text for cid, text, *_rest in CASES}
    data = [
        {
            "id": "D01",
            "expected": GROUP_DIRECTED,
            "decision": "response",
            "ok": True,
            "emitted_token_ids": [151670],
        },
        {
            "id": "N01",
            "expected": GROUP_NONDIRECTED,
            "decision": "response",
            "ok": True,
            "emitted_token_ids": [151670],
        },
    ]
    # 只逐字重叠 N01 ⇒ 它进开卷子集，D01 留在泛化子集。
    open_book_prompt = f"…… {text_of['N01']} ……"
    block = axis.axis_block(data, open_book_prompt)
    assert block["by_subset"][SUBSET_OPEN_BOOK]["n_cases"] == 1
    assert block["by_subset"][SUBSET_GENERALIZATION]["n_cases"] == 1
    assert block["overall"]["n_cases"] == 2
    # ★ 反向对照：零重叠的 prompt 下全部落泛化 —— 证明上面那条不是恒真。
    neutral = axis.axis_block(data, NEUTRAL_PROMPT)
    assert neutral["by_subset"][SUBSET_OPEN_BOOK]["n_cases"] == 0
    assert neutral["by_subset"][SUBSET_GENERALIZATION]["n_cases"] == 2


def test_empty_subset_reports_zero_cases_not_a_zero_score():
    """空子集必须报 ``n_cases=0``（而不是一堆 0.0 分数 —— 那会被读成「全错」）。"""
    data = rows([("D1", GROUP_DIRECTED, "response")])
    block = axis.axis_block(data, NEUTRAL_PROMPT)
    assert block["by_subset"][SUBSET_OPEN_BOOK] == {"n_cases": 0}


def test_axis_metrics_produces_exactly_the_registered_keys():
    """★ 结构守卫：``axis_metrics`` 的产出键必须**恰好**在登记表里。

    跨轮聚合按登记表决定「取中位 / 求和 / 透传」。一个新指标若没登记，
    聚合语义就是未定义的 —— 而它极可能静默退回「单轮」语义（#165 的形态）。
    """
    produced = set(axis.axis_metrics(perfect()))
    registered = set(axis.AXIS_ALL_KEYS)
    assert produced == registered, (
        f"未登记：{sorted(produced - registered)}；登记了但没产出：{sorted(registered - produced)}"
    )


def test_metric_and_count_key_lists_do_not_overlap():
    """取中位的键与求和的键不能重叠（重叠意味着同一键有两种聚合语义）。"""
    assert not set(axis.AXIS_METRIC_KEYS) & set(axis.AXIS_COUNT_KEYS)
    assert not set(axis.AXIS_METRIC_KEYS) & set(axis.AXIS_PASSTHROUGH_KEYS)
    assert not set(axis.AXIS_COUNT_KEYS) & set(axis.AXIS_PASSTHROUGH_KEYS)


# ---------------------------------------------------------------------------
# 5. 跨轮聚合（多轮取中位 + 离散度）
# ---------------------------------------------------------------------------


def test_aggregate_metric_keeps_median_single_rounds_and_dispersion():
    """★ 中位、单轮值、离散度**并列**（只有中位会把「稳定」与「碰巧」混为一谈）。"""
    series = axis.aggregate_metric([10.0, 20.0, 30.0])
    assert series["median"] == 20.0
    assert series["per_round"] == [10.0, 20.0, 30.0]
    assert series["stdev"] == pytest.approx(8.165, abs=0.01)
    assert series["n_measured_rounds"] == 3


def test_aggregate_metric_excludes_unmeasured_rounds_and_counts_them():
    """★ ``None``（未测）不进统计，但**被计数** —— 否则读者看不出有几轮没测。"""
    series = axis.aggregate_metric([10.0, None, 30.0])
    assert series["n_measured_rounds"] == 2
    assert series["n_unmeasured_rounds"] == 1
    assert series["median"] == 20.0
    assert series["per_round"] == [10.0, None, 30.0]


def test_aggregate_metric_all_unmeasured_gives_none_not_zero():
    """★ 一轮都没测出来 ⇒ ``median=None``。

    若这里给 0.0，一个「压根没测」的配置会被读成「得了 0 分」——
    与「测到了 0」混淆，正是本模块要防的那件事。
    """
    series = axis.aggregate_metric([None, None])
    assert series["median"] is None
    assert series["stdev"] is None


def test_aggregate_axis_blocks_sums_counts_and_medians_metrics():
    """★ 比率取中位、计数**求和**（判据的 min_denominator 读计数）。"""
    rounds = [
        axis.axis_block(perfect(), NEUTRAL_PROMPT),
        axis.axis_block(perfect(), NEUTRAL_PROMPT),
        axis.axis_block(perfect(), NEUTRAL_PROMPT),
    ]
    agg = axis.aggregate_axis_blocks(rounds)
    # ★ 计数是**结构化三件套**（和 / 逐轮 / 有几轮非零），不是光秃秃一个和。
    #   只报和会让读者拿一个混合口径的读数去解释「跨轮取中位」得到的比率 ——
    #   真机产物里 not_for_me_predicted=[1,0,0] 就是这种形状。
    directed = agg["overall"]["n_directed"]
    assert directed["sum"] == 2 * 3, "计数必须跨轮求和"
    assert directed["per_round"] == [2, 2, 2]
    assert directed["n_rounds_nonzero"] == 3
    predicted = agg["overall"]["not_for_me_predicted"]
    assert predicted["sum"] == 1 * 3
    assert predicted["n_rounds_nonzero"] == 3, "三轮都有预测 ⇒ 守卫应认为分母健全"
    assert agg["overall"]["nondirected_spurious_response_rate_pct"]["median"] == 0.0
    assert agg["rounds"]["rounds_count"] == 3


def test_counter_guard_distinguishes_a_total_from_every_round_having_a_denominator():
    """★★ 非退化守卫必须看「每轮是否非零」，不能只看**总和**。

    这是真机产物逼出来的：``not_for_me_predicted`` 三轮是 ``[1, 0, 0]`` ——
    总和为 1（非零），于是「总和式」守卫会让一个由**一轮里的一例**换来的
    100% 精确率**判绿**。那与「precision 100% 而分母只有 3 例」是同一种缺陷。

    本测试直接构造这个形状，断言守卫**拒绝**它。
    """
    block = axis.axis_block(perfect(), NEUTRAL_PROMPT)
    block["overall"]["not_for_me_predicted"] = {
        "sum": 1,
        "per_round": [1, 0, 0],
        "n_rounds": 3,
        "n_rounds_nonzero": 1,
    }
    assert axis.counter_value(block["overall"], "not_for_me_predicted")["sum"] == 1
    assert not axis.counter_is_measured_every_round(block["overall"], "not_for_me_predicted")
    # 反向对照：每轮都有 ⇒ 守卫放行（否则它就成了恒拒）。
    healthy = axis.aggregate_axis_blocks(
        [axis.axis_block(perfect(), NEUTRAL_PROMPT) for _ in range(3)]
    )
    assert axis.counter_is_measured_every_round(healthy["overall"], "not_for_me_predicted")


def test_counter_value_unifies_single_round_and_multi_round_shapes():
    """★ 单轮是裸 int、多轮是结构化 dict —— 读侧必须只有**一条**实现。"""
    single = axis.axis_block(perfect(), NEUTRAL_PROMPT)["overall"]
    multi = axis.aggregate_axis_blocks(
        [axis.axis_block(perfect(), NEUTRAL_PROMPT) for _ in range(3)]
    )["overall"]
    for scope in (single, multi):
        counter = axis.counter_value(scope, "n_directed")
        assert counter["n_rounds"] >= 1
        assert counter["sum"] == counter["n_rounds"] * 2
    assert axis.counter_value(single, "n_directed")["n_rounds"] == 1
    assert axis.counter_value(multi, "n_directed")["n_rounds"] == 3
    # 缺键 ⇒ 全 0（调用方只需判 n_rounds_nonzero，不必自己处理 None）
    assert axis.counter_value(single, "not_a_real_key")["sum"] == 0


def test_aggregate_axis_blocks_rejects_a_changed_denominator():
    """★ 分母跨轮不一致 ⇒ **报错**：比较无效，且这件事在本仓静默发生过一次（25 vs 26）。"""
    full = axis.axis_block(perfect(), NEUTRAL_PROMPT)
    short = axis.axis_block(perfect()[:-1], NEUTRAL_PROMPT)
    with pytest.raises(ValueError, match="分母"):
        axis.aggregate_axis_blocks([full, short])


def test_aggregate_axis_blocks_refuses_a_single_round():
    """★ 单轮**报错**而不是给一个像结论的数字（单轮不可作点估计）。"""
    with pytest.raises(ValueError, match="2 轮"):
        axis.aggregate_axis_blocks([axis.axis_block(perfect(), NEUTRAL_PROMPT)])


def test_aggregate_axis_blocks_refuses_an_unregistered_new_metric():
    """★ 未登记的新指标必须报错，不得静默按首轮透传（= 悄悄退回单轮语义）。"""
    block = axis.axis_block(perfect(), NEUTRAL_PROMPT)
    block["overall"]["brand_new_metric"] = 1.0
    with pytest.raises(ValueError, match="未登记"):
        axis.aggregate_axis_blocks([block, axis.axis_block(perfect(), NEUTRAL_PROMPT)])


def test_dispersion_actually_measures_dispersion():
    """★ 负控：注入高方差 ⇒ 离散度明显上升；三轮一致 ⇒ 恒为 0。"""
    stable = axis.aggregate_metric([50.0, 50.0, 50.0])
    jittery = axis.aggregate_metric([10.0, 50.0, 90.0])
    assert stable["stdev"] == 0.0
    assert jittery["stdev"] > stable["stdev"]
    assert jittery["range"] > 0


def test_quiet_recall_is_labelled_as_context_not_as_a_replacement_metric():
    """★ 宽口径「沉默召回」不得被当成 not-for-me 召回的替代品。

    替代了就退回恒真判据（永远沉默的模型宽口径召回 100%）。本模块把它
    算出来只为解释「precision 100% 却等于没做 addressee 判定」这个现象，
    并在卡片里标为「仅语境」。
    """
    data = rows([("N1", GROUP_NONDIRECTED, "silence"), ("N2", GROUP_NONDIRECTED, "silence")])
    m = axis.axis_metrics(data)
    assert m["quiet_recall_pct"] == 100.0, "永远沉默 ⇒ 宽口径召回 100%"
    assert m["not_for_me_recall_pct"] == 0.0, "而真正的 nfm 召回仍是 0"
