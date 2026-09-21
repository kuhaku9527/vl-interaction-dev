# ruff: noqa: RUF001, RUF002, RUF003
"""计分器行为测试（工单 #155 的收口；spec #154）。

★ 为什么这个文件存在
--------------------
计分逻辑原先只长在 ``services/scripts/`` 下的 benchmark 里，而那里
**不在 CI 的 pytest 矩阵内** ⇒ **计分器从未被任何测试覆盖**。
它算错、算漏、静默跳过一组，全都无人守。

本文件把「资产真的被计分器读到」与「分组不会被静默跳过」钉成可执行断言，
并**把验收 #6（标签改反 ⇒ 分数变）固定为测试**，而不是靠人手演示一次。

Run: cd services/webinfer && python -m pytest tests/test_decision_eval_score.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import decision_eval_score as score  # noqa: E402
from decision_eval_set import (  # noqa: E402
    CASES,
    GROUP_DELEGATE,
    GROUP_DIRECTED,
    GROUP_NONDIRECTED,
    GROUPS,
)


def _perfect_rows() -> list[dict]:
    """每一行都给出该组「教科书式正确」的决策。"""
    rows = []
    for cid, _text, group, *_rest in CASES:
        decision = {
            GROUP_DIRECTED: "response",
            GROUP_NONDIRECTED: "not-for-me",
            GROUP_DELEGATE: "delegation",
        }[group]
        rows.append({"id": cid, "expected": group, "decision": decision, "ok": True})
    return rows


def _rows_all_decision(decision: str) -> list[dict]:
    """所有行都输出同一个决策（用于造「退化桩」）。"""
    return [
        {"id": cid, "expected": group, "decision": decision, "ok": True}
        for cid, _t, group, *_r in CASES
    ]


# ---------------------------------------------------------------------------
# 1. 三值语义：is_correct
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("group", "decision", "expected_ok"),
    [
        (GROUP_DIRECTED, "response", True),
        (GROUP_DIRECTED, "delegation", True),
        (GROUP_DIRECTED, "silence", False),
        (GROUP_DIRECTED, "not-for-me", False),
        (GROUP_NONDIRECTED, "not-for-me", True),
        (GROUP_NONDIRECTED, "silence", True),
        (GROUP_NONDIRECTED, "response", False),
        (GROUP_NONDIRECTED, "delegation", False),
        (GROUP_DELEGATE, "delegation", True),
        (GROUP_DELEGATE, "response", False),
        (GROUP_DELEGATE, "silence", False),
        (GROUP_DELEGATE, "not-for-me", False),
    ],
)
def test_is_correct_is_three_way(group, decision, expected_ok):
    assert score.is_correct(group, decision) is expected_ok


def test_is_correct_fails_loud_on_unknown_group():
    """未知组必须报错，**不得**静默算成「对」——静默即 fail-open。"""
    with pytest.raises(KeyError):
        score.is_correct("some-new-group", "response")


def test_silence_counts_as_correct_for_nondirected():
    """★ 「判定沉默」与 not-for-me 在行为上都等于不开口，故都算对。

    旧的两值规则把沉默判成错，与项目实测相悖（真实的不开口主要靠 silence 发生）。
    """
    assert score.is_correct(GROUP_NONDIRECTED, "silence") is True
    assert score.legacy_is_correct(GROUP_NONDIRECTED, "silence") is False


def test_legacy_semantics_is_recorded_and_differs():
    """★ 可比性变更必须**被声明**，不能悄悄改掉历史字段的含义。"""
    assert "不可直接比较" in score.LEGACY_CORRECT_SEMANTICS or (
        "不能直接比较" in score.LEGACY_CORRECT_SEMANTICS
    )
    # 三处已知语义差异必须真的存在，否则说明声明与实现不符
    diffs = [
        (GROUP_DIRECTED, "silence"),
        (GROUP_NONDIRECTED, "silence"),
        (GROUP_DELEGATE, "response"),
    ]
    for group, decision in diffs:
        assert score.is_correct(group, decision) != score.legacy_is_correct(group, decision), (
            f"{group}/{decision} 声称语义有变，实际未变"
        )


# ---------------------------------------------------------------------------
# 2. ★ 分组不得被静默跳过
# ---------------------------------------------------------------------------


def test_matrix_covers_every_group():
    stats = score.summarize(_perfect_rows())
    assert set(stats["matrix"]) == set(GROUPS)


def test_perfect_run_scores_100_on_the_directed_axis():
    stats = score.summarize(_perfect_rows())
    assert stats["not_for_me_recall_pct"] == 100.0
    assert stats["directed_miss_rate_pct"] == 0.0
    assert stats["delegate_recall_pct"] == 100.0
    assert stats["n_nondirected"] == 26
    assert stats["n_delegate"] == 5


def test_delegate_is_counted_not_skipped():
    """★ delegate 必须真的进矩阵（此前新增该组会让旧计分器 KeyError）。"""
    stats = score.summarize(_rows_all_decision("silence"))
    assert stats["matrix"][GROUP_DELEGATE]["silence"] == stats["n_delegate"]
    assert stats["n_delegate"] > 0


def test_always_silent_stub_scores_zero_delegate_recall():
    """★ 负控：一个「永远沉默」的桩必须拿 0 delegate 召回（不是静默通过）。"""
    stats = score.summarize(_rows_all_decision("silence"))
    assert stats["delegate_recall_pct"] == 0.0


# ---------------------------------------------------------------------------
# 3. ★ 本模块修的回归：not-for-me 精确率分母必须含全部组
# ---------------------------------------------------------------------------


def test_nfm_precision_denominator_covers_all_groups():
    """★ 回归测试：分母写死两组时，误报 delegate 的模型仍能拿 100%。

    场景：除「把 5 条 delegate 全误报成 not-for-me」外其它都正确。
    分母若漏掉 delegate，精确率仍是 100% —— 那是**静默的假绿**。
    """
    rows = []
    for cid, _t, group, *_r in CASES:
        if group == GROUP_DELEGATE:
            decision = "not-for-me"  # 误报
        elif group == GROUP_NONDIRECTED:
            decision = "not-for-me"
        else:
            decision = "response"
        rows.append({"id": cid, "expected": group, "decision": decision, "ok": True})

    stats = score.summarize(rows)
    # 全部 26 条 nondirected + 5 条 delegate = 31 条 not-for-me 预测
    assert stats["n_not_for_me_predicted"] == stats["n_nondirected"] + stats["n_delegate"]
    assert stats["not_for_me_precision_pct"] < 100.0, (
        "误报 5 条 delegate 却仍得 100% 精确率 ⇒ 分母漏了组（旧缺陷复发）"
    )
    assert stats["not_for_me_precision_pct"] == pytest.approx(83.9, abs=0.1)


def test_nfm_precision_is_100_when_every_prediction_is_真的正确():
    """反向对照：全部正确时仍应为 100%（防止上一条被"恒 <100"满足）。"""
    stats = score.summarize(_perfect_rows())
    assert stats["not_for_me_precision_pct"] == 100.0


# ---------------------------------------------------------------------------
# 4. ★ 验收 #6：标签改反 ⇒ 分数随之变（固化为测试）
# ---------------------------------------------------------------------------


def test_flipping_one_expected_label_changes_the_score():
    """★ 验收 #6：把某句的期望标签改反，计分必须随之改变。

    这证明**资产真的被计分器读到了**，而不是装饰。
    """
    rows = _perfect_rows()
    before = score.summarize(rows)

    flipped = [dict(r) for r in rows]
    target = next(r for r in flipped if r["expected"] == GROUP_NONDIRECTED)
    target["expected"] = GROUP_DIRECTED  # 改反

    after = score.summarize(flipped)

    assert before != after, "改反标签却没改变任何分数 ⇒ 资产是装饰品"
    assert after["n_nondirected"] == before["n_nondirected"] - 1
    assert after["n_directed"] == before["n_directed"] + 1
    assert after["directed_miss_rate_pct"] != before["directed_miss_rate_pct"]


def test_negcontrol_a_broken_row_is_not_silently_correct():
    """★ 负控：置 ``ok=False`` 的行必须进 ``error`` 列，不得算作任何决策。"""
    rows = _perfect_rows()
    rows[0]["ok"] = False
    stats = score.summarize(rows)
    assert stats["errors"] == 1
    assert stats["matrix"][rows[0]["expected"]]["error"] == 1


def test_negcontrol_scores_actually_depend_on_decisions():
    """★ 负控：把所有决策换成另一个值，分数必须变（防止恒真计算）。"""
    a = score.summarize(_rows_all_decision("response"))
    b = score.summarize(_rows_all_decision("silence"))
    assert a != b


# ---------------------------------------------------------------------------
# 5. 子集分列
# ---------------------------------------------------------------------------


def test_subset_breakdown_splits_by_prompt():
    import decision_eval_set as des

    rows = _perfect_rows()
    prompt = des.production_live_prompt()
    breakdown = score.subset_breakdown(rows, prompt)
    assert set(breakdown) == set(score.SUBSETS)
    total = sum(breakdown[s]["n"] for s in breakdown)
    assert total == len(rows)
    assert breakdown[des.SUBSET_OPEN_BOOK]["n"] == 10
    assert breakdown[des.SUBSET_GENERALIZATION]["n"] == len(rows) - 10


def test_subset_breakdown_omits_empty_subsets_cleanly():
    """某子集无样本时给 n=0，**不得**KeyError / 崩。"""
    import decision_eval_set as des

    rows = _perfect_rows()
    breakdown = score.subset_breakdown(rows, prompt="零重叠 prompt")
    assert breakdown[des.SUBSET_OPEN_BOOK]["n"] == 0
    assert breakdown[des.SUBSET_GENERALIZATION]["n"] == len(rows)


# ---------------------------------------------------------------------------
# 6. ★ 离线记分卡（验收 #4：一条命令看到两子集的**句数与分数**）
# ---------------------------------------------------------------------------


def test_format_scorecard_reports_scores_not_just_counts():
    """★ 验收 #4：离线命令必须给出**分数**，不只是句数。"""
    import decision_eval_set as des

    rows = _perfect_rows()
    text = score.format_scorecard(rows, des.production_live_prompt(), title="unit")
    assert "unit" in text
    for subset in score.SUBSETS:
        assert subset in text
    assert "nfm_recall=" in text and "mis_response=" in text and "delegate_recall=" in text
    assert "overall" in text


def test_format_scorecard_separates_the_two_subsets():
    """两个子集必须**分列**，且分数可以不同（开卷 vs 泛化）。"""
    import decision_eval_set as des

    prompt = des.production_live_prompt()
    rows = []
    for case in des.load_cases(prompt):
        if case.is_open_book:
            decision = {
                des.GROUP_DIRECTED: "response",
                des.GROUP_NONDIRECTED: "not-for-me",
                des.GROUP_DELEGATE: "delegation",
            }[case.group]
        else:
            decision = "response"  # 泛化句一律乱答
        rows.append({"id": case.case_id, "expected": case.group, "decision": decision, "ok": True})

    breakdown = score.subset_breakdown(rows, prompt)
    gen = breakdown[des.SUBSET_GENERALIZATION]
    ob = breakdown[des.SUBSET_OPEN_BOOK]
    assert ob["not_for_me_recall_pct"] > gen["not_for_me_recall_pct"], (
        "开卷子集全对 / 泛化子集全错，两子集分数却没拉开 ⇒ 分列没生效"
    )


def test_load_rows_from_results_fails_loud_on_unknown_variant(tmp_path):
    """★ 负控：未知 variant 必须报错，**不得**静默按空集计分。"""
    import json

    payload = {"results": {"only_variant": {"rows": []}}}
    p = tmp_path / "r.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(KeyError):
        score.load_rows_from_results(p, "no_such_variant")


def test_load_rows_from_results_fails_loud_on_empty_file(tmp_path):
    """★ 负控：没有任何 variant 的文件必须报错（不是「零分」）。"""
    import json

    p = tmp_path / "empty.json"
    p.write_text(json.dumps({"results": {}}), encoding="utf-8")
    with pytest.raises(KeyError):
        score.load_rows_from_results(p)


def test_load_rows_from_results_picks_profile_prompt_by_variant_name(tmp_path):
    """variant 名含 'profile' ⇒ 带 persona 的 prompt；否则用裸 prompt。

    这决定开卷归属 —— 用错 prompt 会让开卷/泛化分列整个错位。
    """
    import json

    import decision_eval_set as des

    rows = [
        {"id": cid, "expected": g, "decision": "silence", "ok": True}
        for cid, _t, g, *_r in des.CASES
    ]
    payload = {"results": {"P2_x_profile": {"rows": rows}, "P_bare": {"rows": rows}}}
    p = tmp_path / "r.json"
    p.write_text(json.dumps(payload), encoding="utf-8")

    _rows_p, prompt_profile = score.load_rows_from_results(p, "P2_x_profile")
    _rows_b, prompt_bare = score.load_rows_from_results(p, "P_bare")
    assert "<character_profile>" in prompt_profile
    assert "<character_profile>" not in prompt_bare
    assert len(prompt_profile) > len(prompt_bare)
