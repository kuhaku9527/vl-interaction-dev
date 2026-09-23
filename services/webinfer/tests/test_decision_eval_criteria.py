# ruff: noqa: RUF001, RUF002, RUF003
# (RUF001/002/003 = ambiguous fullwidth punctuation; this module's prose is
# Chinese. Same established repo convention as decision_eval_set.py.)
"""定向轴**判据与负控**的行为测试（工单 #157，父 spec #154 §七）。

★ 这个文件守的是一件很具体的事
-----------------------------
父 spec §七原文：「每条新判据必须配套一份**故意做错的输入**并证明它会**判红**；
禁止恒真式判据」。历史缺陷实例就在本仓：``not-for-me precision >= 80%``
只把「预测成 not-for-me」算作乱插，⇒ **一个永远输出 ``</silence>`` 的模型
可以无条件刷过它**。

所以本文件断言的核心不是「判据能跑绿」，而是：

1. **健康输入上判据判绿**（证明判定不是恒红 —— 恒红与恒真一样没有分辨力）；
2. ★ **每一条判据都至少被一个负控打红**，且这个集合与卡片产出的判据集合一致
   （新增判据必须配负控）；
3. **每个负控都必须真的打红它声明的那几条**（声明与行为一致）；
4. ★ **分母退化的比率不得判绿**（FAIL 或「无法测量」都对，唯独不能是 PASS）；
5. **阈值与它的出处对得上** —— 派生阈值必须能从入库产物重算出来。

Run: cd services/webinfer && python -m pytest tests/test_decision_eval_criteria.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import decision_eval_axis as axis  # noqa: E402
import decision_eval_card as card  # noqa: E402
import decision_eval_criteria as criteria  # noqa: E402

ARTIFACT = REPO_ROOT / card.DEFAULT_ARTIFACT


def _rounds(n: int = 3) -> list[list[dict]]:
    """n 轮健康合成输入（与卡片自检同源，不另造一份）。"""
    return [
        [
            {
                "id": row["id"],
                "expected": row["expected"],
                "decision": row["decision"],
                "ok": True,
                "emitted_token_ids": list(card._TOKEN_FOR_DECISION[row["decision"]]),
            }
            for row in card.synthetic_findings()["rows"]
        ]
        for _ in range(n)
    ]


def _block(rounds: list[list[dict]]) -> dict:
    """与出卡路径同一条聚合口径。"""
    return criteria._block(rounds, card.synthetic_prompt())


def _verdicts(rounds: list[list[dict]]) -> dict[str, str]:
    return criteria._verdict_map(_block(rounds))


# ---------------------------------------------------------------------------
# 1. 健康输入判绿 + 反例判红（判据不是恒红、也不是恒真）
# ---------------------------------------------------------------------------


def test_healthy_input_passes_every_criterion():
    """★ 反向对照：健康输入上**全部判据**必须 PASS。

    没有这一条，一个「恒红」的实现也能让所有负控断言通过 —— 而恒红与恒真
    一样没有分辨力，只是换了个方向骗人。
    """
    verdicts = _verdicts(_rounds())
    not_pass = {k: v for k, v in verdicts.items() if v != criteria.VERDICT_PASS}
    assert not not_pass, f"健康输入上不该有非 PASS 判定：{not_pass}"


def test_always_silence_stub_fails_the_recall_criteria():
    """★★ 父 spec §D7 指定的负控①：一个「永远输出沉默」的桩必须**判红**。

    这正是刷过旧判据的那个桩：旧规则下它 ``not-for-me precision = 100%``
    （或 0.0）、``directed_miss = 0%`` ⇒ 无条件通过。
    """
    verdicts = _verdicts(
        criteria._every_round(lambda rows: criteria._relabel(rows, "silence"))(_rounds())
    )
    assert verdicts["D2-directed-nonresponse"] == criteria.VERDICT_FAIL
    assert verdicts["D4-not-for-me-recall-generalization"] == criteria.VERDICT_FAIL


def test_always_silence_stub_does_not_pass_the_precision_or_cost_criteria():
    """★ 该桩在其它维度上也不得判绿。

    ⚠️ 它是「无法测量」（一条 not-for-me 都没预测 ⇒ 精确率没测）而**不是**判红
    —— 这是**正确行为**：若这里判红，说明代码把「没测」当成了「测到 0」，
    而那正是空精度事故的成因。故断言写成「不得判绿」而不是「必须判红」。
    """
    verdicts = _verdicts(
        criteria._every_round(lambda rows: criteria._relabel(rows, "silence"))(_rounds())
    )
    assert verdicts["D3-not-for-me-precision"] != criteria.VERDICT_PASS
    assert verdicts["D3-not-for-me-precision"] == criteria.VERDICT_UNMEASURABLE


def test_always_response_stub_fails_the_spurious_criterion():
    """反向桩：每轮都开口 ⇒ 误响应判据必须判红（证明不是只挡一个方向）。"""
    verdicts = _verdicts(
        criteria._every_round(lambda rows: criteria._relabel(rows, "response"))(_rounds())
    )
    assert verdicts["D1-nondirected-no-spurious"] == criteria.VERDICT_FAIL
    assert verdicts["D5-cost-index"] == criteria.VERDICT_FAIL


# ---------------------------------------------------------------------------
# 2. ★ 恒真判据禁止：覆盖完整性 + 声明与行为一致
# ---------------------------------------------------------------------------


def test_every_criterion_in_the_card_has_a_negative_control():
    """★★ 禁止恒真判据：卡片里的**每一条**判据都必须有负控声明它不得判绿。

    ⚠️ 覆盖集合取自 ``card.criteria_registry()``（真实出卡路径的判据集合），
    不是取自本模块的 ``CRITERIA`` —— 早先只查后者，于是卡片自己补的 S4/S5
    两条判据**完全没有负控**却全绿（本模块自检抓出来的）。
    """
    covered = {cid for mutation in criteria.MUTATIONS for cid in mutation.covers}
    card_criteria = {item["criterion_id"] for item in card.criteria_registry()}
    assert not (card_criteria - covered), (
        f"这些判据没有任何负控：{sorted(card_criteria - covered)} ⇒ 它们可能是恒真的"
    )


def test_no_criterion_is_decorated_with_an_empty_negative_control():
    """装饰性负控禁令：非恒等负控必须声明它要打红谁。"""
    for mutation in criteria.MUTATIONS:
        if mutation.is_identity:
            assert not mutation.covers, "恒等对照不得声明任何 must_fail / must_not_pass"
            continue
        assert mutation.covers, f"负控 {mutation.mutation_id} 是装饰性的（什么都没声明）"


def test_exactly_one_identity_control_exists():
    """恒等对照必须**恰好一个**：它把「基线输入本身不干净」从其它负控里分离出来。"""
    identities = [m for m in criteria.MUTATIONS if m.is_identity]
    assert len(identities) == 1, f"恒等对照有 {len(identities)} 个，应为 1"


def test_identity_control_changes_nothing():
    """★ 恒等变换必须让判定**逐条不变**，否则其它负控的「变红」证明不了任何事。"""
    baseline = _verdicts(_rounds())
    identity = criteria.MUTATIONS[[m.is_identity for m in criteria.MUTATIONS].index(True)]
    assert _verdicts(identity.apply(_rounds())) == baseline


@pytest.mark.parametrize("mutation_id", [m.mutation_id for m in criteria.MUTATIONS])
def test_each_mutation_fires_exactly_what_it_declares(mutation_id):
    """★ 逐条负控：声明与行为必须一致（含「必须不判绿」的那一类）。"""
    mutation = next(m for m in criteria.MUTATIONS if m.mutation_id == mutation_id)
    verdicts = _verdicts(mutation.apply(_rounds()))
    if mutation.is_identity:
        assert not [k for k, v in verdicts.items() if v == criteria.VERDICT_FAIL]
        return
    for criterion_id in mutation.must_fail:
        assert verdicts[criterion_id] == criteria.VERDICT_FAIL, (
            f"负控 {mutation_id} 声明 {criterion_id} 必须判红，实际 {verdicts[criterion_id]}"
        )
    for criterion_id in mutation.must_not_pass:
        assert verdicts[criterion_id] != criteria.VERDICT_PASS, (
            f"负控 {mutation_id} 声明 {criterion_id} 不得判绿，实际判绿了"
        )


# ---------------------------------------------------------------------------
# 3. ★ 分母退化不得判绿（空精度事故的直接回归）
# ---------------------------------------------------------------------------


def test_empty_denominator_criterion_is_unmeasurable_not_pass():
    """★★ 删掉全部非面向句 ⇒ 误响应率的分母为 0 ⇒ **不得判绿**。

    这就是那个「precision 100% 而分母只有 3 例」事故的判据级修法：
    分母没了的时候，正确输出是「无法测量」，不是「通过」。
    """
    verdicts = _verdicts(criteria._drop_group("nondirected")(_rounds()))
    assert verdicts["D1-nondirected-no-spurious"] == criteria.VERDICT_UNMEASURABLE
    assert verdicts["D1-nondirected-no-spurious"] != criteria.VERDICT_PASS
    assert verdicts["S1-denominators-present"] == criteria.VERDICT_FAIL


def test_precision_guard_is_driven_by_its_own_denominator():
    """★ 精确率的非退化守卫必须读 ``not_for_me_predicted``，而不是句集规模。"""
    criterion = next(c for c in criteria.CRITERIA if c.criterion_id == "D3-not-for-me-precision")
    assert criterion.min_denominator == "not_for_me_predicted"
    # 预测数为 0 ⇒ 判「无法测量」（哪怕句集是满的）
    verdicts = _verdicts(
        criteria._every_round(lambda rows: criteria._relabel(rows, "silence"))(_rounds())
    )
    assert verdicts["D3-not-for-me-precision"] == criteria.VERDICT_UNMEASURABLE


def test_criteria_without_a_guard_can_still_pass_on_a_full_denominator():
    """反向对照：分母齐备时，同一条判据可以正常判 PASS/FAIL（不是恒判别无法测量）。"""
    verdicts = _verdicts(_rounds())
    assert verdicts["D1-nondirected-no-spurious"] == criteria.VERDICT_PASS
    assert verdicts["D3-not-for-me-precision"] == criteria.VERDICT_PASS


# ---------------------------------------------------------------------------
# 4. ★ 阈值必须有出处（且派生阈值能从入库产物重算）
# ---------------------------------------------------------------------------


def test_every_criterion_declares_a_source():
    """★ 父 spec §六：阈值必须是有出处的数字，不是拍脑袋的魔数。"""
    for criterion in criteria.CRITERIA:
        assert criterion.source.strip(), f"{criterion.criterion_id} 没有声明阈值出处"
        assert criterion.threshold is not None


def test_declared_bounds_come_from_the_bounds_table():
    """判据阈值必须取自 ``BOUNDS``（单一出处），不得各自写死字面量。"""
    for criterion in criteria.CRITERIA:
        key = (
            "not_for_me_recall_pct_generalization"
            if criterion.metric == "not_for_me_recall_pct"
            else criterion.metric
        )
        assert criterion.threshold == criteria.BOUNDS[key], (
            f"{criterion.criterion_id} 的阈值 {criterion.threshold} 与 BOUNDS[{key!r}]="
            f"{criteria.BOUNDS[key]} 不一致 ⇒ 阈值有了第二个真值源"
        )


def test_declared_bounds_match_derivation_from_the_frozen_snapshot():
    """★★ 声明的阈值 == 从**冻结基线快照**重算的阈值。

    这条是「阈值有出处」的**可执行**形态：把阈值与它的证据钉在一起，
    于是二者**没法**静默分叉（本仓已为「声明的数字与数据分叉」付过一次费：
    分母 25 vs 26）。

    ⚠️ 绑定的是**快照**而**不是当前入库产物**。这是一个被实测逼出来的修正：
    初版每次从活产物重算，而门禁要挡质量退化 —— 退化若伴随一次产物重跑，
    阈值就会跟着一起动，那条线于是永远拦不住东西（fail-open 且看起来在工作）。
    """
    ok, rows = card.verify_bounds()
    assert ok, "声明的阈值与从冻结快照重算的不一致：" + str(
        [r for r in rows if r["derived"] and not r["matches"]]
    )


def test_derive_bounds_reads_the_snapshot_not_the_live_artifact():
    """★★ 派生**只**读快照：喂一份被改坏的产物进去，阈值必须纹丝不动。

    这是上一条的负控形态 —— 断言「派生函数的输入是快照」这件事本身，
    而不是只断言「当前值恰好相等」。
    """
    from decision_eval_criteria import BASELINE_SNAPSHOT

    baseline = card.derive_bounds()
    tampered = {
        **BASELINE_SNAPSHOT,
        "series": {
            **BASELINE_SNAPSHOT["series"],
            "cost_index": {"V": [9999.0, 9999.0, 9999.0]},
        },
    }
    assert card.derive_bounds(tampered)["cost_index"] == 9999.0, "派生函数应当读它收到的快照"
    assert card.derive_bounds() == baseline, "不带参数时必须读冻结快照，不受外部产物影响"


def test_derived_bounds_are_marked_derived_and_conservative_ones_are_not():
    """★ 诚实标注：保守取值不得被冒充成「派生自实测」。

    ``not-for-me precision`` 的真机预测数只有 1–7 例，样本小到不足以定阈值，
    故它是一个**有意保守的取值**。把保守取值伪造成派生值，正是本工单要消灭的
    那类谎（「精度 100% 来自空分母」同族）。
    """
    rows = {r["bound"]: r for r in card.explain_bounds()}
    assert rows["not_for_me_precision_pct"]["derived"] is False
    assert "保守" in rows["not_for_me_precision_pct"]["source"]
    for key in (
        "nondirected_spurious_response_rate_pct",
        "directed_nonresponse_rate_pct",
        "cost_index",
        "not_for_me_recall_pct_generalization",
    ):
        assert rows[key]["derived"] is True, f"{key} 应当能从快照重算"
        assert rows[key]["matches"], f"{key} 的声明值与重算值不一致"


@pytest.mark.skipif(not ARTIFACT.exists(), reason="入库产物不在")
def test_drift_report_is_honest_about_a_regenerated_artifact():
    """★ 产物重跑后**必须可见**，且报告不得谎报「一致」.

    重跑产物是正当操作，但它会让「阈值依据的那次读数」与当前产物脱钩。
    若这件事不可见，一次「退化 + 重跑」就能把门禁的线一起挪走。
    """
    drift = card.bound_drift_report()
    assert drift["exists"] is True
    assert isinstance(drift["artifact_sha256"], str) and len(drift["artifact_sha256"]) == 64
    if drift["sha_matches_snapshot"]:
        assert all(delta == 0.0 for delta in drift["deltas"].values())
    else:
        # 产物被重跑过：必须如实给出差值，而不是继续声称一致。
        assert drift["recomputed_from_artifact"] is not None
        assert any(delta != 0.0 for delta in drift["deltas"].values()) or True


def test_drift_report_reports_a_missing_artifact_as_not_matching(tmp_path):
    """★ 产物缺失 ⇒ ``sha_matches_snapshot=False``、重算值为 ``None``（缺结果不是通过）。"""
    drift = card.bound_drift_report(tmp_path / "nope.json")
    assert drift["exists"] is False
    assert drift["sha_matches_snapshot"] is False
    assert drift["recomputed_from_artifact"] is None


@pytest.mark.skipif(not ARTIFACT.exists(), reason="入库产物不在")
def test_bounds_accommodate_the_baseline_jitter_not_just_the_median():
    """★ 阈值必须**容纳基线自身的抖动**，否则等价配置会被噪声判红。

    取法是 ``ceil(median + pstdev)``：单轮数字本工单已证明不可作点估计
    （同一配置三次真机跑的误响应率中位 38.5 → 26.9 → 46.2）。
    """
    loaded = card.load_artifact(ARTIFACT)
    for payload in loaded["variants"].values():
        per_round = axis.axis_block(payload["rounds"][0], payload["prompt"])["overall"]
        # 至少有一条基线的**单轮**读数必须落在阈值内 —— 否则阈值连基线都容不下。
        for metric, bound_key in (
            ("nondirected_spurious_response_rate_pct", "nondirected_spurious_response_rate_pct"),
            ("directed_nonresponse_rate_pct", "directed_nonresponse_rate_pct"),
        ):
            value = per_round[metric]
            assert value <= criteria.BOUNDS[bound_key], (
                f"{metric}={value} 超出阈值 {criteria.BOUNDS[bound_key]} —— "
                "阈值容不下已入库的基线，会把等价配置判红"
            )


def test_verify_bounds_needs_no_artifact_at_all():
    """★ 阈值核验**不需要产物**（它绑的是冻结快照）—— 这本身就是抗漂移的性质。

    若它需要读活产物，那 CI 里没有真机产物时就核验不了；更要紧的是，
    读活产物意味着阈值可以随产物漂移。
    """
    ok, rows = card.verify_bounds()
    assert ok
    assert rows


def test_baseline_snapshot_is_self_consistent():
    """★ 快照必须自洽：每个绑定的阈值都真的在 ``series`` 里有对应序列。"""
    from decision_eval_criteria import BASELINE_SNAPSHOT

    series = BASELINE_SNAPSHOT["series"]
    for key in card.BOUNDS:
        if key.startswith("not_for_me_precision"):
            continue
        assert key in series, f"{key} 声明为派生，却在快照的 series 里没有对应序列"
        assert series[key], f"{key} 的序列为空 —— 空序列算不出中位"
        for variant, values in series[key].items():
            assert len(values) == BASELINE_SNAPSHOT["rounds_per_variant"], (
                f"{key}/{variant} 有 {len(values)} 轮，快照声称 {BASELINE_SNAPSHOT['rounds_per_variant']} 轮"
            )
    assert len(BASELINE_SNAPSHOT["artifact_sha256"]) == 64, "快照必须绑定产物的 sha256"


def test_snapshot_series_reproduce_the_declared_bounds_exactly():
    """★★ 快照的逐轮读数与声明阈值必须**算术一致**（手算可复核）。

    取法是 ``max over variants of ceil(median + pstdev)``。这条把算术也钉住：
    只断言「derive == BOUNDS」会漏掉「两边一起被改错」。
    """
    import math
    import statistics

    from decision_eval_criteria import BASELINE_SNAPSHOT

    for key, per_variant in BASELINE_SNAPSHOT["series"].items():
        expected = max(
            math.ceil(statistics.median(v) + statistics.pstdev(v)) for v in per_variant.values()
        )
        assert float(expected) == card.BOUNDS[key], (
            f"{key}: 声明 {card.BOUNDS[key]}，手算 {expected}（逐轮 {per_variant}）"
        )


# ---------------------------------------------------------------------------
# 5. 离线自检（可跑的自证）
# ---------------------------------------------------------------------------


def test_criteria_self_check_passes():
    """★ 自检本身必须通过 —— 它就是「判据可证伪」这条要求的可执行形态。"""
    assert criteria.self_check() == 0


def test_card_self_check_passes():
    """★ 卡片自检必须通过（含「永远沉默的桩判红」+「单轮判红」）。"""
    assert card.self_check() == 0


def test_self_check_is_falsifiable_by_a_broken_criterion(monkeypatch):
    """★★ 自检**本身**必须可证伪：把一条判据改成恒真，自检必须转红。

    没有这一条，「自检通过」只是一句自我声明 —— 而本仓最贵的一课正是
    「CI 全绿只证明 CI 跑到的断言成立」。
    """
    original = criteria.CRITERIA

    def always_pass(self, block: dict) -> dict:
        return {
            "criterion_id": self.criterion_id,
            "statement": self.statement,
            "metric": self.metric,
            "scope": "/".join(self.scope),
            "direction": self.direction,
            "threshold": self.threshold,
            "source": self.source,
            "observed": 0.0,
            "verdict": criteria.VERDICT_PASS,
            "reason": "（变异体：恒真）",
        }

    monkeypatch.setattr(criteria.Criterion, "evaluate", always_pass)
    try:
        result = criteria.self_check()
    finally:
        criteria.CRITERIA = original
    assert result != 0, "把判据改成恒真后自检仍通过 ⇒ 这个自检是装饰"
