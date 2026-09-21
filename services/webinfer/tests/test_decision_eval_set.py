# ruff: noqa: RUF001, RUF002, RUF003
# (Same established convention as services/background-agent and
# services/memory-store tests: Chinese prose quotes real test sentences.)
"""冻结测试集资产的行为测试（工单 #155，spec #154）。

断言的是**资产的可观察行为**，不是实现细节：
分母被钉住、开卷归属算得对、改动 prompt 后归属会跟着变、
`delegate` 真的进了计分、遗留视图不丢数据。

★ 每条关键断言都配**负控**（改动数据 → 必须转红），
因为本项目的核心教训是「判据能跑绿 ≠ 判据能分辨对错」。

Run: cd services/webinfer && python -m pytest tests/test_decision_eval_set.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import decision_eval_set as des  # noqa: E402

# ---------------------------------------------------------------------------
# 1. 分母被钉住，且与数据一致
# ---------------------------------------------------------------------------


def test_group_counts_match_declared_canonical_counts():
    """声明的分母必须与数据一致 —— 否则声明会与数据静默分叉。"""
    assert des.canonical_counts_match(), (
        f"声明的分母 {des.CANONICAL_COUNTS} 与实际 {des.group_counts()} 不一致"
    )


def test_total_is_sum_of_groups():
    counts = des.group_counts()
    assert (
        counts["total"]
        == counts[des.GROUP_DIRECTED] + counts[des.GROUP_NONDIRECTED] + counts[des.GROUP_DELEGATE]
    )


def test_case_ids_are_unique():
    ids = [cid for cid, *_ in des.CASES]
    duplicates = {i for i in ids if ids.count(i) > 1}
    assert not duplicates, f"case id 重复: {duplicates}"


def test_historical_denominator_difference_is_recorded():
    """25 vs 26 的差别必须被**显式记录**，不能只存在于某条评论里。

    这是本工单要修的实际缺陷：历史结果的非面向分母是 25，现集合是 26。
    """
    assert des.HISTORICAL_COUNTS[des.GROUP_NONDIRECTED] == 25
    assert des.group_counts()[des.GROUP_NONDIRECTED] == 26
    assert "25" in des.HISTORICAL_DENOMINATOR_NOTE
    assert "26" in des.HISTORICAL_DENOMINATOR_NOTE
    assert "N01b" in des.HISTORICAL_DENOMINATOR_NOTE


# ---------------------------------------------------------------------------
# 2. 开卷归属是**算出来的**
# ---------------------------------------------------------------------------


def test_overlap_is_computed_not_declared():
    """静态定义里**不得**存在 subset 字段 —— 归属必须是算的。"""
    for row in des.CASES:
        assert len(row) == 6, f"{row[0]} 的静态定义元组形状变了（多了硬编码字段？）"
        assert "open-book" not in row
        assert "generalization" not in row


def test_open_book_ids_match_measured_baseline():
    """实测基线：生产 prompt 逐字重叠 **10 句**，其中 **8 句非面向**。

    数字来自地图 #142 的调研（2026-09-20），本测试把它钉在代码里。
    """
    cases = des.load_cases()
    open_book = [c for c in cases if c.is_open_book]
    assert len(open_book) == 10, f"开卷句数变了: {[c.case_id for c in open_book]}"

    nondirected_open = [c for c in open_book if c.group == des.GROUP_NONDIRECTED]
    assert len(nondirected_open) == 8, (
        f"非面向开卷句应为 8，实际 {len(nondirected_open)}: {[c.case_id for c in nondirected_open]}"
    )


def test_generalization_subset_has_no_literal_overlap():
    """★ 泛化子集的定义：与被测 prompt **零逐字重叠**。"""
    prompt = des.production_live_prompt()
    gen = [c for c in des.load_cases() if c.subset == des.SUBSET_GENERALIZATION]
    assert gen, "泛化子集不能为空"
    for case in gen:
        assert case.text not in prompt, f"{case.case_id} 被标为泛化，却逐字出现在 prompt 里"


def test_subset_counts_sum_to_total():
    cases = des.load_cases()
    counts = des.subset_counts(cases)
    assert counts[des.SUBSET_GENERALIZATION] + counts[des.SUBSET_OPEN_BOOK] == len(cases)


# ---------------------------------------------------------------------------
# 3. ★ 冻结性 vs 生产力（最核心的一组）
# ---------------------------------------------------------------------------


def test_subset_flips_when_prompt_changes():
    """★ 关键行为：prompt 一变，开卷归属**跟着变**，无需人改标签。

    负控式断言：往 prompt 里塞进一句原本属于泛化子集的测试句，
    它必须**变成**开卷。若实现退化成硬编码标签，本测试转红。
    """
    cases = des.load_cases(prompt="（空 prompt，零重叠）")
    assert not any(c.is_open_book for c in cases), "空 prompt 下不该有开卷句"

    probe = next(c for c in cases if c.subset == des.SUBSET_GENERALIZATION)
    injected = des.load_cases(prompt=f"随便一些前言……{probe.text}……后话")
    after = next(c for c in injected if c.case_id == probe.case_id)
    assert after.is_open_book, f"把 {probe.case_id} 注入 prompt 后，它应变为开卷"


def test_identical_asset_twice_is_stable():
    """冻结性：连跑两次，输入句集与顺序**逐句相同**。"""
    first = des.load_cases()
    second = des.load_cases()
    assert [c.case_id for c in first] == [c.case_id for c in second]
    assert [c.text for c in first] == [c.text for c in second]
    assert first == second


def test_asset_order_is_the_declared_order():
    """顺序即稳定顺序 —— 不得因加载而被重排。"""
    assert [c.case_id for c in des.load_cases()] == [cid for cid, *_ in des.CASES]


# ---------------------------------------------------------------------------
# 4. delegate 真的进了计分（不是静默跳过）
# ---------------------------------------------------------------------------


def test_delegate_group_exists_and_is_nonempty():
    counts = des.group_counts()
    assert counts[des.GROUP_DELEGATE] == des.CANONICAL_COUNTS[des.GROUP_DELEGATE]
    assert counts[des.GROUP_DELEGATE] > 0, "delegate 组为空 ⇒ 该态又变成永远测不到"


def test_delegate_cases_expect_delegate_action():
    for case in des.load_cases():
        if case.group == des.GROUP_DELEGATE:
            assert case.expected_action == des.ACTION_DELEGATE, (
                f"{case.case_id} 在 delegate 组却期望 {case.expected_action}"
            )


def test_delegate_is_excluded_from_directed_axis():
    """★ delegate **刻意**不在定向轴内。

    理由：它回答「开口之后走哪条路」，不是「该不该开口」。
    这个排除也让两条既有分母（25 / 26）与历史结果**保持可比**。
    """
    assert des.GROUP_DELEGATE not in des.DIRECTED_AXIS_GROUPS
    assert des.DIRECTED_AXIS_GROUPS == (des.GROUP_DIRECTED, des.GROUP_NONDIRECTED)

    counts = des.group_counts()
    assert counts[des.GROUP_DIRECTED] == des.HISTORICAL_COUNTS[des.GROUP_DIRECTED]
    assert counts[des.GROUP_NONDIRECTED] == des.CANONICAL_COUNTS[des.GROUP_NONDIRECTED]


def test_every_case_has_an_expected_action():
    valid = {des.ACTION_RESPOND, des.ACTION_DELEGATE, des.ACTION_SILENT}
    for case in des.load_cases():
        assert case.expected_action in valid, f"{case.case_id} 的期望动作非法"


def test_group_and_action_are_consistent():
    """组与期望动作不得互相矛盾（防止手改数据时漏改一处）。"""
    for case in des.load_cases():
        if case.group == des.GROUP_DIRECTED:
            assert case.expected_action == des.ACTION_RESPOND
        elif case.group == des.GROUP_NONDIRECTED:
            assert case.expected_action == des.ACTION_SILENT
        else:
            assert case.expected_action == des.ACTION_DELEGATE


# ---------------------------------------------------------------------------
# 5. 遗留视图：两个 benchmark 的既有计分代码零改动仍可用
# ---------------------------------------------------------------------------


def test_legacy_view_preserves_shape_and_content():
    legacy = des.legacy_test_set()
    assert len(legacy) == len(des.CASES)
    for row in legacy:
        assert len(row) == 5, "遗留视图必须是 5-tuple（既有 summarize 依赖此形状）"
        cid, text, group = row[0], row[1], row[2]
        assert isinstance(cid, str) and isinstance(text, str)
        assert group in des.GROUPS


def test_legacy_view_includes_delegate():
    """遗留视图**不得**丢掉 delegate —— 否则该态在既有计分里被静默跳过。"""
    groups = {group for _cid, _text, group, *_ in des.legacy_test_set()}
    assert des.GROUP_DELEGATE in groups, "遗留视图丢了 delegate 组"


def test_legacy_view_matches_cases_one_to_one():
    """遗留视图与 CASES 必须逐条对应（防止两处数据分叉）。"""
    legacy = des.legacy_test_set()
    assert [r[0] for r in legacy] == [c[0] for c in des.CASES]
    assert [r[1] for r in legacy] == [c[1] for c in des.CASES]
    assert [r[2] for r in legacy] == [c[2] for c in des.CASES]


# ---------------------------------------------------------------------------
# 6. 负控：证明这些断言真的在分辨对错
# ---------------------------------------------------------------------------


def test_negcontrol_canonical_count_mismatch_is_detected(monkeypatch):
    """★ 负控：把声明的分母改错 → 一致性检查必须判红。"""
    bad = dict(des.CANONICAL_COUNTS)
    bad[des.GROUP_NONDIRECTED] = 999
    monkeypatch.setattr(des, "CANONICAL_COUNTS", bad)
    assert not des.canonical_counts_match(), "分母改错却没被发现 ⇒ 检查是恒真的"


def test_negcontrol_overlap_detection_actually_reads_prompt(monkeypatch):
    """★ 负控：逐字匹配确实在比对文本，而不是永远返回空集。"""
    real = des.production_live_prompt()
    assert des.overlapping_ids(real), "真实 prompt 下开卷集为空 ⇒ 匹配逻辑没生效"
    assert des.overlapping_ids("完全不相干的一句话") == frozenset()


def test_negcontrol_production_prompt_contains_the_teaching_section():
    """★ 负控：被匹配的 prompt 确实是**生产 live prompt**（含 persona 组装）。

    若有人把 prompt 解析改成读一个空串，开卷集会变空、上面那条转红。
    这里额外钉住：prompt 非空且确实含有被判定为开卷的句子。
    """
    prompt = des.production_live_prompt()
    assert len(prompt) > 1000, "生产 prompt 异常短 —— 组装可能坏了"
    sample = next(c for c in des.load_cases() if c.is_open_book)
    assert sample.text in prompt
