# ruff: noqa: RUF001, RUF002, RUF003
# (RUF001/002/003 = ambiguous fullwidth punctuation. This file's prose is Chinese
# and quotes real test sentences; the same suppression is used by the sibling
# eval test modules and by decision_eval_timing*.py. Established repo convention.)
"""时序轴**判据**的契约测试（工单 #158，spec #154 §六/§七）.

本文件钉住四类东西，每类都是「判据是否能分辨对错」的一个面：

  1. **每条判据都有负控**（父 spec §七：没有负控的判据可能是恒真的）；
  2. ★ **本票 ★ 负控的方向**：删掉轮次打点 ⇒ ``T_ONSET_MEASURED`` 判 **FAIL**
     （而不是「无法测量」）—— 判「无法测量」会把接线缺陷说成「这项不适用」；
  3. **阈值有出处且可从冻结快照复算**（本仓硬约束：阈值必须是有出处的数字）；
  4. **判据文本不得与阈值分叉**（#157 的对抗性复核查出的那一类）。

Run: cd services/webinfer && python -m pytest tests/test_decision_eval_timing_criteria.py -q
"""

from __future__ import annotations

import decision_eval_timing_criteria as C
import decision_eval_timing_negatives as N
import pytest
from decision_eval_timing import timing_block
from decision_eval_timing_synthetic import (
    synthetic_healthy_rows,
    synthetic_silent_rows,
    synthetic_unstamped_rows,
)


def _verdicts(block: dict) -> dict[str, str]:
    return {item["criterion_id"]: item["verdict"] for item in C.criteria_verdicts(block)}


def _healthy_block() -> dict:
    return timing_block(synthetic_healthy_rows())


# ---------------------------------------------------------------------------
# 1. 基线干净 + 覆盖完整性
# ---------------------------------------------------------------------------


def test_healthy_baseline_passes_every_criterion():
    """★ 判据不得恒红：健康输入上必须全绿.

    一个恒红的判据与恒真的判据一样没有分辨力，只是换了个方向骗人。
    """
    verdicts = _verdicts(_healthy_block())
    failed = sorted(k for k, v in verdicts.items() if v == C.VERDICT_FAIL)
    not_green = sorted(k for k, v in verdicts.items() if v != C.VERDICT_PASS)
    assert failed == [], f"健康输入上不该有 FAIL，却出现 {failed}"
    assert not_green == [], f"健康输入上不该有非绿判定，却出现 {not_green}"


def test_every_criterion_has_a_negative_control():
    """★ 父 spec §七：每条新判据必须配套一份故意做错的输入.

    没有负控的判据**可能是恒真的** —— 而恒真判据正是本票要消灭的东西。
    """
    all_ids = {c.criterion_id for c in C.TIMING_CRITERIA}
    covered = {cid for m in N.MUTATIONS for cid in m.covers}
    assert all_ids - covered == set(), (
        f"以下判据没有任何负控声明它必须判红 / 不得判绿：{sorted(all_ids - covered)}"
    )


def test_no_decorative_negative_control():
    """负控必须声明它期待什么；一条不期待任何东西的负控是装饰."""
    for mutation in N.MUTATIONS:
        if mutation.is_identity:
            continue
        assert mutation.covers, f"负控 {mutation.mutation_id} 是装饰性的（covers 为空）"


def test_exactly_one_identity_control():
    """★ 恰好一个恒等对照：没有它，「负控会让判据变红」就没有对照."""
    identities = [m for m in N.MUTATIONS if m.is_identity]
    assert len(identities) == 1


# ---------------------------------------------------------------------------
# 2. ★ 本票 ★ 负控的方向
# ---------------------------------------------------------------------------


def test_dropping_round_stamps_judges_the_criterion_red():
    """★★ #158 ★ 负控：删掉轮次打点 ⇒ 时序轴**判红**.

    ★ 判「无法测量」是**不可接受**的：那会把「打点被删掉（接线缺陷）」说成
    「这项在本次输入上不适用」，即 fail-open。本票正文点名要求「判红」。
    """
    verdicts = _verdicts(timing_block(synthetic_unstamped_rows()))
    assert verdicts["T_ONSET_MEASURED"] == C.VERDICT_FAIL, (
        f"删掉轮次打点后 T_ONSET_MEASURED 是 {verdicts['T_ONSET_MEASURED']!r}，本票要求它判红"
    )


def test_dropping_round_stamps_does_not_leave_onset_green():
    """删掉打点后，两条 onset 计量判据**绝不能判绿**（不能退回出数）."""
    verdicts = _verdicts(timing_block(synthetic_unstamped_rows()))
    for cid in ("T_ONSET_MEDIAN", "T_ONSET_P90"):
        assert verdicts[cid] != C.VERDICT_PASS, f"{cid} 在缺打点时判绿了"


# ---------------------------------------------------------------------------
# 3. 禁止合并两轴
# ---------------------------------------------------------------------------


def test_combined_accuracy_is_judged_red():
    """★ 父 spec §三：单一 accuracy 被明确否决 ⇒ 出现即判红."""
    tainted = {**_healthy_block(), "accuracy": 0.9}
    assert _verdicts(tainted)["T_NO_COMBINED_ACCURACY"] == C.VERDICT_FAIL


@pytest.mark.parametrize(
    "key", ["accuracy", "combined_score", "overall_score", "single_accuracy", "f1"]
)
def test_all_listed_combined_keys_are_caught(key: str):
    """扫描表里的每一个键都要真的被抓到（不是只抓 accuracy 一个）."""
    assert _verdicts({**_healthy_block(), key: 1})["T_NO_COMBINED_ACCURACY"] == C.VERDICT_FAIL


def test_baseline_has_no_combined_keys():
    """基线本身干净 —— 否则上一条判据的「判红」证明不了它真的在扫描."""
    assert _verdicts(_healthy_block())["T_NO_COMBINED_ACCURACY"] == C.VERDICT_PASS


# ---------------------------------------------------------------------------
# 4. 耗时出处
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "illegal",
    ["frame_capture_ts_ms", "event_ts_diff"],
)
def test_forbidden_latency_sources_are_judged_red(illegal: str):
    """★ 帧的采集端时间戳 / 事件 ts 差值 ⇒ 判红（错的数据比缺失更坏）."""
    verdicts = _verdicts(timing_block(synthetic_healthy_rows(), latency_source=illegal))
    assert verdicts["T_LATENCY_SOURCE"] == C.VERDICT_FAIL


def test_chain_stamp_source_passes():
    """链上打点是通过的唯一出处."""
    assert _verdicts(_healthy_block())["T_LATENCY_SOURCE"] == C.VERDICT_PASS


# ---------------------------------------------------------------------------
# 5. 分母纪律
# ---------------------------------------------------------------------------


def test_always_silent_never_gets_a_green_timing_score():
    """★ 「永远沉默」的桩：一条计量判据都不得判绿.

    ★ 它**没有** must_fail（见 :data:`decision_eval_timing_negatives.MUTATIONS` 的注释）：产物是「没有开口
    样本」⇒ 三个量全部不可测。声明它必须判红会让自检假失败，进而诱使人放宽判据。
    要断言的正是「不可测 ≠ 完美」。
    """
    verdicts = _verdicts(timing_block(synthetic_silent_rows()))
    for cid in ("T_ONSET_MEDIAN", "T_ONSET_P90", "T_PREMATURE_MEASURED"):
        assert verdicts[cid] != C.VERDICT_PASS, f"{cid} 对永远沉默的桩判绿了"


def test_premature_without_labels_is_unmeasurable_not_green():
    """缺 premature 标注 ⇒ 「无法测量」（不是绿、也不是红）."""
    from decision_eval_timing import FIELD_STILL_SPEAKING

    rows = [
        {k: v for k, v in r.items() if k != FIELD_STILL_SPEAKING} for r in synthetic_healthy_rows()
    ]
    verdicts = _verdicts(timing_block(rows))
    assert verdicts["T_PREMATURE_MEASURED"] == C.VERDICT_UNMEASURABLE


def test_missing_timebase_is_unmeasurable():
    """没有时间基准 ⇒ 速率判据「无法测量」（0.0 会被读成「没乱插」）."""
    rows = [{**r, "ts": ""} for r in synthetic_healthy_rows()]
    assert _verdicts(timing_block(rows))["T_SPURIOUS_TIMEBASE"] == C.VERDICT_UNMEASURABLE


def test_tiny_sample_is_unmeasurable():
    """开口样本不足 ⇒ 分位数判据「无法测量」（样本不足无法自证准不准）."""
    verdicts = _verdicts(timing_block(synthetic_healthy_rows()[:3]))
    assert verdicts["T_SAMPLE_FLOOR"] == C.VERDICT_UNMEASURABLE


# --- ★ 真机核验查出的三处缺陷的回归测试 -------------------------------------


def test_missing_truth_makes_the_rate_unmeasurable_not_zero():
    """★★ D1 回归：**零真值** ⇒ 每秒误触发判「无法测量」，**不得**是 0.0.

    ★ 真机核验（204 轮真机事件流）查出的 fail-open：初版只看时间基准，
    于是零真值输入报 ``0.0 次/秒`` 且 ``T_SPURIOUS_TIMEBASE`` 判 **pass** ——
    「0 次乱插话」与「不知道有几次」在输出上完全同形。

    ★ 讽刺得很具体：本条判据存在的理由正是「不得把未测读成 0」，
    而它自己放行了那个 0。真机 09-21 正是这个形状（``n_expected_speak = 0``）。
    """
    rows = [{**r, "expected": None} for r in synthetic_healthy_rows()]
    block = timing_block(rows)
    metrics = block["metrics"]
    assert metrics["n_expected_speak"] == 0
    assert metrics["n_expected_quiet"] == 0
    assert metrics["spurious_triggers_per_second"] is None, (
        "零真值下仍给出一个速率 ⇒ 「不知道」被读成「0 次乱插」"
    )
    assert metrics["spurious_triggers_per_minute"] is None
    assert metrics["spurious_rate_measurable"] is False
    assert _verdicts(block)["T_SPURIOUS_TIMEBASE"] == C.VERDICT_UNMEASURABLE


def test_truth_present_still_yields_a_real_rate():
    """★ D1 的**对照**：有真值时速率照常产出（防止修复把功能一起关掉）."""
    metrics = timing_block(synthetic_healthy_rows())["metrics"]
    assert metrics["spurious_rate_measurable"] is True
    assert metrics["spurious_triggers_per_second"] is not None
    assert metrics["spurious_triggers_per_second"] > 0


def test_onset_criteria_respect_the_sample_floor():
    """★★ D2 回归：样本不足时两条**带阈值的** onset 判据也须判「无法测量」.

    ★ 真机核验查出：它们**没有**样本下限，于是样本不足时报「测量」而不是
    「不适用」。实测真机卡片并排打印「T_SAMPLE_FLOOR=无法测量（样本 1<10）」
    与「T_ONSET_MEDIAN=pass（16.0<=898）」—— **语义自相矛盾**，而门禁（#159）
    若读后者就会被误导。

    ★ 两条判据必须在**同一事实**上给出同一判定，否则卡片自己跟自己打架。
    """
    verdicts = _verdicts(timing_block(synthetic_healthy_rows()[:3]))
    assert verdicts["T_SAMPLE_FLOOR"] == C.VERDICT_UNMEASURABLE
    assert verdicts["T_ONSET_MEDIAN"] == C.VERDICT_UNMEASURABLE, (
        "样本不足却报「测量」⇒ 与 T_SAMPLE_FLOOR 自相矛盾"
    )
    assert verdicts["T_ONSET_P90"] == C.VERDICT_UNMEASURABLE


def test_onset_criteria_still_measure_with_enough_samples():
    """★ D2 的**对照**：样本充足时两条 onset 判据照常给出判定."""
    verdicts = _verdicts(timing_block(synthetic_healthy_rows()))
    assert verdicts["T_ONSET_MEDIAN"] == C.VERDICT_PASS
    assert verdicts["T_ONSET_P90"] == C.VERDICT_PASS


# ---------------------------------------------------------------------------
# 6. 总判定规则
# ---------------------------------------------------------------------------


def test_no_speaking_at_all_is_unmeasurable_not_red():
    """★ 「一次开口都没有」判**无法测量**，不是判红.

    ★ 这条区分是必须的，而且区分的是两件完全不同的事：

    * 「**有**开口但缺打点」= 打点接线断了 ⇒ 判红（本该有而不有）；
    * 「一次都没开口」= 这次输入上没有 onset 可言 ⇒ 无法测量。

    把后者判红会给「永远沉默的桩」一个**指错方向**的「接线缺陷」指控，
    而错误的告警会训练读者忽略真正的告警。
    """
    verdicts = _verdicts(timing_block(synthetic_silent_rows()))
    assert verdicts["T_ONSET_MEASURED"] == C.VERDICT_UNMEASURABLE, (
        "一次开口都没有时不该判红 —— 那是无适用对象，不是接线缺陷"
    )


def test_speaking_without_any_stamp_is_red_not_unmeasurable():
    """★ 与上一条配对：**有**开口却一次打点都没有 ⇒ 判红.

    ★ 两条一起才构成有分辨力的判据：只有前一条会漏掉真缺陷，
    只有后一条会把「没开口」误报成缺陷。
    """
    verdicts = _verdicts(timing_block(synthetic_unstamped_rows()))
    assert verdicts["T_ONSET_MEASURED"] == C.VERDICT_FAIL


def test_premature_scope_partition_survives_duplicate_rows():
    """★ 分母分桶用**下标**而不是字典相等性（重复行不得被误判成同一桶）.

    ★ 若用 ``row not in not_applicable``，两行内容相同的轮次会被算成
    「已在另一桶里」，于是分母悄悄少算一个 —— 而分母少算一个不会报错，
    只会让比率偏大。这是一条**静默**的缺陷形态。
    """
    from decision_eval_timing import premature_scope

    same = {
        "id": "dup",
        "session_id": "s",
        "ts": "2026-09-22T05:00:00.000Z",
        "round_kind": "user",
        "decision": "response",
        "expected": "speak",
        "ok": True,
        "latency_ms": 400,
        "user_still_speaking_at_decision": True,
    }
    scope = premature_scope([dict(same), dict(same)])
    assert scope["n_speaking"] == 2
    assert scope["n_labeled"] == 2, "重复行的分桶被相等性比较吞掉了一个"
    assert scope["denominator"] == 2


def test_overall_verdict_never_turns_unmeasurable_into_green():
    """★ 「无法测量」不得折算成绿（本仓 #162 已把这条纪律钉进运行器）."""
    assert C.overall_verdict([{"verdict": C.VERDICT_UNMEASURABLE}]) == C.VERDICT_UNMEASURABLE
    assert C.overall_verdict([{"verdict": C.VERDICT_PASS}]) == C.VERDICT_PASS
    assert (
        C.overall_verdict([{"verdict": C.VERDICT_PASS}, {"verdict": C.VERDICT_FAIL}])
        == C.VERDICT_FAIL
    )


# ---------------------------------------------------------------------------
# 7. 阈值出处（本仓硬约束）
# ---------------------------------------------------------------------------


def test_declared_bounds_match_derivation_from_the_frozen_snapshot():
    """★ 「声明的阈值 == 从冻结快照重算的阈值」.

    ★ 这是本仓 25 vs 26 事故的形态：常量与它的证据分叉，而分叉是静默的。
    初版手写 890/1130 与重算值 898/1120 不符 —— 正是这条断言抓出来的。
    """
    ok, rows = C.verify_bounds()
    assert ok, [(r["bound"], r["declared"], r["recomputed_from_snapshot"]) for r in rows]
    assert all(r["matches"] for r in rows)


def test_bounds_are_derived_from_the_snapshot_not_the_live_events():
    """★ 阈值只从**冻结快照**派生（不从活事件文件）.

    ★ 理由与定向轴同：门禁要挡质量退化，而退化若伴随一次重跑，
    阈值会跟着退化一起动，那条线便永远拦不住东西。
    """
    doubled = {
        **C.TIMING_BASELINE_SNAPSHOT,
        "latency_ms": [v * 2 for v in C.TIMING_BASELINE_SNAPSHOT["latency_ms"]],
    }
    derived = C.derive_bounds(doubled)
    assert derived["onset_median_ms"] > C.TIMING_BOUNDS["onset_median_ms"], (
        "快照翻倍后重算的阈值没有变大 ⇒ 阈值其实没在读快照"
    )


def test_p90_bound_is_not_below_the_median_bound():
    """★ p90 的阈值必须 ≥ 中位数的阈值（否则一条出厂即红的线）."""
    assert C.TIMING_BOUNDS["onset_p90_ms"] >= C.TIMING_BOUNDS["onset_median_ms"]


def test_snapshot_declares_its_own_limitation():
    """★ 快照必须自带样本量限定 —— 4 个样本不足以声称「基线水平」."""
    assert C.TIMING_BASELINE_SNAPSHOT["sample_count"] == len(
        C.TIMING_BASELINE_SNAPSHOT["latency_ms"]
    )
    assert "4 个" in C.TIMING_BASELINE_SNAPSHOT["limitation"]


# ---------------------------------------------------------------------------
# 8. 文本与阈值的分叉守卫
# ---------------------------------------------------------------------------


def test_statement_templates_do_not_hardcode_thresholds():
    """★ 带阈值的判据必须有 ``{threshold}`` 占位符（阈值一个数渲两次）.

    ★ #157 的教训：阈值写死在文本里会与 ``threshold`` 分叉，
    而 ``statement`` 会随卡片落进 JSON、**正是门禁作者照抄的那句话**。
    """
    problems = C.statements_match_bounds()
    assert problems == [], problems


def test_rendered_statement_contains_the_declared_threshold():
    """渲染后的文本里必须能读到那个阈值（不是只有占位符)."""
    for criterion in C.TIMING_CRITERIA:
        if criterion.threshold is None:
            continue
        assert f"{criterion.threshold:.0f}" in criterion.statement


def test_statement_guard_would_catch_a_hardcoded_threshold(monkeypatch: pytest.MonkeyPatch):
    """★ 守卫本身**可证伪**：把阈值写死进模板必须被抓到.

    没有这一条，「守卫是绿的」只证明守卫被调用了，不证明它有效。
    """
    from dataclasses import replace

    bad = replace(
        C.TIMING_CRITERIA[6],
        statement_template="onset 中位数不得超过 890 ms —— 写死的数字",
    )
    monkeypatch.setattr(C, "TIMING_CRITERIA", (*C.TIMING_CRITERIA[:6], bad, *C.TIMING_CRITERIA[7:]))
    problems = C.statements_match_bounds()
    assert any("占位符" in p or "未声明" in p for p in problems), problems


def test_axis_separation_is_visible_in_the_criteria_text():
    """两轴分列这件事必须在**判据文本**里可见（不只是代码里）."""
    assert C.statements_mention_axis_separation() == []


def test_self_check_passes():
    """判据层的负控自检整体通过."""
    assert N.self_check() == 0
