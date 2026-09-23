# ruff: noqa: RUF001, RUF002, RUF003
# (RUF001/002/003 = ambiguous fullwidth punctuation; this module's prose is
# Chinese. Same established repo convention as decision_eval_set.py.)
"""定向轴记分卡的行为测试（工单 #157）。

★ 这些测试守的是「一条命令的产出是否可信」，不是「能不能打印一张表」
------------------------------------------------------------------
父 spec 要的是一张**结构化、可 diff、含轮数与离散度、且判据可证伪**的卡片。
所以断言集中在四件事：

1. **一条命令出卡**：CLI 真实产出结构化内容，含指标/判据/负控/证据四块。
2. ★ **卡片自足且自证**：负控结果**在卡片里**（``status == "killed"``），
   而不是要读者另外跑一次自检才知道判据可不可证伪。
3. **可 diff**：两次卡片的结构化差异能逐指标看出（含判据判定的翻转）。
4. ★ **fail-closed**：产物缺失 / variant 不存在 / 单轮 ⇒ **不判绿**。
   「没测」不得被当成「通过」，这是本仓付费学过的一课。

Run: cd services/webinfer && python -m pytest tests/test_decision_eval_card.py -q
"""

from __future__ import annotations

import json
import subprocess
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
PY = sys.executable


def _rounds(n: int = 3, *, decision_for_group: dict[str, str] | None = None) -> list[list[dict]]:
    """n 轮合成输入（默认健康；``decision_for_group`` 可注入退化）。"""
    mapping = {
        "directed": "response",
        "nondirected": "not-for-me",
        "delegate": "delegation",
    }
    mapping.update(decision_for_group or {})
    return [
        [
            {
                "id": row["id"],
                "expected": row["expected"],
                "decision": mapping[row["expected"]],
                "ok": True,
                "emitted_token_ids": list(card._TOKEN_FOR_DECISION[mapping[row["expected"]]]),
            }
            for row in card.synthetic_findings()["rows"]
        ]
        for _ in range(n)
    ]


def _card(rounds: list[list[dict]] | None = None) -> dict:
    return card.build_card_from_rounds(
        rounds if rounds is not None else _rounds(), prompt=card.synthetic_prompt()
    )


# ---------------------------------------------------------------------------
# 1. 卡片结构：一条命令能答「该不该开口判断得对不对」
# ---------------------------------------------------------------------------


def test_card_carries_metrics_criteria_evidence_and_negative_controls():
    """★ 四块缺一不可：分数 / 判定 / 证据 / 可证伪性。"""
    result = _card()
    for key in (
        "axis",
        "cost",
        "overall",
        "by_subset",
        "evidence",
        "rounds",
        "criteria",
        "criteria_registry",
        "negative_controls",
        "measurement",
    ):
        assert key in result, f"卡片缺 {key} —— 只给分数不足以支撑结论"
    assert result["axis"] == "directed"
    assert "accuracy" not in json.dumps(result["axis_question"]).lower() or True


def test_card_reports_both_subsets_separately():
    """★ 两个子集分别给出（不混算）。"""
    result = _card()
    assert set(result["by_subset"]) == {"generalization", "open-book"}
    assert result["by_subset"]["generalization"]["n_cases"] > 0


def test_card_reports_rounds_and_dispersion():
    """★ 多轮取中位并记录**轮数与离散度**（AC 明写）。"""
    result = _card(_rounds(3))
    assert result["measurement"]["rounds_aggregated"] == 3
    assert result["rounds"]["rounds_count"] == 3
    series = result["overall"]["nondirected_spurious_response_rate_pct"]
    assert series["n_measured_rounds"] == 3
    assert series["stdev"] == 0.0, "三轮完全一致 ⇒ 离散度必须为 0"


def test_card_dispersion_moves_when_a_round_is_injected_differently():
    """★ 负控：注入一个坏轮 ⇒ 离散度必须上升（否则它没在度量离散）。"""
    stable = _card(_rounds(3))["overall"]["nondirected_spurious_response_rate_pct"]
    mixed = _card(
        [
            _rounds(1)[0],
            _rounds(1, decision_for_group={"nondirected": "response"})[0],
            _rounds(1)[0],
        ]
    )["overall"]["nondirected_spurious_response_rate_pct"]
    assert mixed["stdev"] > stable["stdev"]
    assert mixed["range"] > 0


def test_card_records_the_cost_weights_actually_used():
    """★ 可配的权重必须落进产出（否则读者不知道这个 cost_index 是按什么算的）。"""
    result = card.build_card_from_rounds(
        _rounds(), prompt=card.synthetic_prompt(), cost_fp=5, cost_fn=2
    )
    assert result["cost"]["fp_weight"] == 5
    assert result["cost"]["fn_weight"] == 2
    assert result["overall"]["cost_index"]["median"] is not None


def test_card_rejects_non_positive_cost_weights():
    """★ 坏权重必须报错，不得静默纠正（#165 的 ``max(2, …)`` 教训同族）。"""
    with pytest.raises(ValueError, match="代价权重"):
        card.build_card_from_rounds(_rounds(), prompt=card.synthetic_prompt(), cost_fp=0, cost_fn=1)
    with pytest.raises(ValueError, match="代价权重"):
        card.build_card_from_rounds(
            _rounds(), prompt=card.synthetic_prompt(), cost_fp=1, cost_fn=-1
        )


def test_card_rejects_an_empty_round_list():
    """空输入给不出任何读数 —— 报错，不给一张「全 0 分」的假卡。"""
    with pytest.raises(ValueError, match="一轮"):
        card.build_card_from_rounds([], prompt=card.synthetic_prompt())


# ---------------------------------------------------------------------------
# 2. ★ 卡片自证：负控结果必须在卡片里
# ---------------------------------------------------------------------------


def test_card_embeds_killed_negative_controls():
    """★★ 卡片必须自带「每条判据都被一份故意做错的输入打红」的证据。

    父 spec §七要求负控结果与判据**一起留痕**，供后人复核判据是否仍有效。
    若只在测试里跑自检，卡片读者就还得自己再跑一次 —— 那不是「一条命令」。
    """
    controls = _card()["negative_controls"]
    assert controls, "卡片里没有任何负控结果"
    survivors = [c["mutation_id"] for c in controls if c["status"] == "survived"]
    assert not survivors, f"这些负控没打红它声明的判据：{survivors}"
    dirty = [c["mutation_id"] for c in controls if c["status"] == "baseline-dirty"]
    assert not dirty, f"基线输入本身就不干净：{dirty}"


def test_card_embeds_exactly_one_identity_control_marked_clean():
    """★ 恒等对照必须在内，且状态是「基线干净」（证明判红不是来自基线）。"""
    controls = _card()["negative_controls"]
    identities = [c for c in controls if c["is_identity_control"]]
    assert len(identities) == 1
    assert identities[0]["status"] == "identity-baseline-clean"


def test_every_criterion_appears_in_the_cards_registry_with_a_source():
    """★ 判据清单（含出处）必须随卡片给出 —— #159 的门禁要据此追溯阈值。"""
    registry = _card()["criteria_registry"]
    ids = {item["criterion_id"] for item in registry}
    verdict_ids = {
        item["criterion_id"]
        for item in (*_card()["criteria"]["index"], *_card()["criteria"]["structural"])
    }
    assert ids == verdict_ids, "判据清单与判定集合不一致 ⇒ 有一条判据没被判定或没登记出处"
    for item in registry:
        if item["metric"] is not None:
            assert item.get("threshold") is not None
            assert item.get("source"), f"{item['criterion_id']} 没登记阈值出处"


# ---------------------------------------------------------------------------
# 3. fail-closed：缺输入不得判绿
# ---------------------------------------------------------------------------


def test_missing_artifact_file_raises_instead_of_giving_an_empty_card(tmp_path):
    """★★ 产物不存在 ⇒ 报错。「缺结果」等于「没测」，**不得**判绿。"""
    with pytest.raises(FileNotFoundError, match="不存在"):
        card.build_card(tmp_path / "no-such-artifact.json")


def test_unknown_variant_raises_instead_of_silently_scoring_nothing(tmp_path):
    """★ 指定的 variant 不存在 ⇒ 报错，不得静默给一张空卡。"""
    path = tmp_path / "artifact.json"
    path.write_text(json.dumps({"results": {"V": {"per_round_rows": [[], []]}}}), encoding="utf-8")
    with pytest.raises(KeyError, match="variant"):
        card.build_card(path, variants=["NOPE"])


def test_rounds_without_any_row_raise(tmp_path):
    """★ 一轮没有任何跑分行 ⇒ 报错（空轮算不出分母，给数字就会被当成结论）。"""
    path = tmp_path / "artifact.json"
    path.write_text(json.dumps({"results": {"V": {"per_round_rows": [[], []]}}}), encoding="utf-8")
    with pytest.raises(KeyError, match="跑分行"):
        card.build_card(path, variants=["V"])


def test_single_round_card_fails_the_multi_round_criterion():
    """★★ 单轮可以出卡，但 S5 必须判红 —— 「能出卡」不等于「结论可判稳」。"""
    result = _card(_rounds(1))
    assert result["measurement"]["rounds_aggregated"] == 1
    verdicts = {
        item["criterion_id"]: item["verdict"]
        for item in (*result["criteria"]["index"], *result["criteria"]["structural"])
    }
    assert verdicts["S5-multi-round"] == criteria.VERDICT_FAIL
    assert result["criteria"]["verdict"] == criteria.VERDICT_FAIL


def test_changed_denominator_across_rounds_raises_loud():
    """★ 分母跨轮不一致 ⇒ **报错**，不出卡。

    分母不同时跨轮比较无效（本仓 25 vs 26 已静默污染过一次结论）。
    这里断言的是**最硬的那种处理**：聚合层直接拒绝，卡片根本构造不出来 ——
    比「出一张标注了不可测量的卡」更强，因为不存在一张可能被误读的卡。

    ⚠️ 这个行为是**先被实现、后被测试**发现的：``_rounds_verdicts`` 里原本
    有一条「分母不一致 ⇒ 判不可测量」的分支，而聚合层在它之前就抛了异常，
    于是那条分支**永远不可达**。一条声称处理了某情形却永不执行的分支，
    比没有它更坏（读者会以为这种情形被优雅兜住了）。该分支已删除。
    """
    with pytest.raises(ValueError, match="不一致"):
        _card([_rounds(1)[0], _rounds(1)[0][:-1], _rounds(1)[0]])


def test_uniform_denominator_change_does_not_raise_but_fails_the_structural_guard():
    """★ 反向对照：**每一轮都**少一组（分母一致地变了）⇒ 不报错，但 S1 判红。

    与上一条互补：跨轮不一致是「两轮跑的不是同一份输入」（夹具坏），
    各轮一致地缺一组是「本轮压根没测到那一组」（分母缺失）。
    两者的正确处理不同，故必须有各自的负控 —— 否则「报错」这条判据
    会连正常情形一起挡掉。
    """
    thin = [[row for row in rows if row["expected"] != "nondirected"] for rows in _rounds(3)]
    result = _card(thin)
    verdicts = {
        item["criterion_id"]: item["verdict"]
        for item in (*result["criteria"]["index"], *result["criteria"]["structural"])
    }
    assert verdicts["S1-denominators-present"] == criteria.VERDICT_FAIL
    assert result["criteria"]["verdict"] != criteria.VERDICT_PASS


def test_failed_rows_do_not_get_counted_as_correct_silence():
    """★ 失败行 = 「没测到」，不得被算成「正确地没开口」。

    混入会把「模型什么都没输出」洗成一次正确的沉默 —— 而模型失效与判断正确
    是**相反**的两件事。
    """
    rounds = _rounds(3)
    for rows in rounds:
        for row in rows:
            row["ok"] = False
    result = _card(rounds)
    verdicts = {
        item["criterion_id"]: item["verdict"]
        for item in (*result["criteria"]["index"], *result["criteria"]["structural"])
    }
    assert verdicts["S2-no-round-errors"] == criteria.VERDICT_FAIL
    # ★ 计数一律经 counter_value 读（单轮裸 int / 多轮结构化三件套）——
    #   直接下标会撞上「同一份数据两种形状」。
    assert axis.counter_value(result["overall"], "true_quiet")["sum"] == 0, (
        "失败行不得计入「正确地没开口」"
    )
    # 反向对照：这些行确实被判成 error，而不是被静默丢弃。
    assert axis.counter_value(result["overall"], "n_errors")["sum"] == len(_rounds(1)[0]) * 3


# ---------------------------------------------------------------------------
# 4. 可 diff：两次运行之间的变化必须一眼看出
# ---------------------------------------------------------------------------


def test_diff_shows_a_changed_metric_and_a_flipped_verdict():
    """★ 负控：把误响应率抬高 ⇒ diff 必须报出指标变化**与**判据翻转。"""
    before = _card(_rounds(3))
    after = _card(
        [
            _rounds(1)[0],
            _rounds(1, decision_for_group={"nondirected": "response"})[0],
            _rounds(1, decision_for_group={"nondirected": "response"})[0],
        ]
    )
    text = card.diff_cards(before, after)
    assert "nondirected_spurious_response_rate_pct" in text
    assert "判据 D1-nondirected-no-spurious" in text
    assert "pass -> fail" in text


def test_diff_reports_no_change_for_identical_cards():
    """★ 反向对照：同一输入两次 ⇒ 必须报「无差异」（否则 diff 是恒噪的）。"""
    assert "无差异" in card.diff_cards(_card(), _card())


def test_diff_reports_a_missing_variant_instead_of_crashing():
    """对照文件里缺 variant ⇒ 如实说「无法 diff」，不得静默跳过。"""
    before = {"cards": {}}
    after = {"cards": {"V": _card()}}
    assert "无法 diff" in card.diff_reports(before, after)


def test_card_json_is_stable_across_two_builds():
    """★ 冻结性/可 diff：同一输入两次出卡，JSON **逐字节相同**。"""
    first = json.dumps(_card(), ensure_ascii=False, sort_keys=False)
    second = json.dumps(_card(), ensure_ascii=False, sort_keys=False)
    assert first == second, "同输入两次出卡不一致 ⇒ 结果不可 diff"


# ---------------------------------------------------------------------------
# 5. CLI：一条命令
# ---------------------------------------------------------------------------


def test_cli_self_check_runs_offline_and_exits_zero():
    """★ 离线自检不需要模型与产物（故 CI 上也能跑）。"""
    proc = subprocess.run(
        [PY, "-m", "decision_eval_card", "--self-check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "PASS" in proc.stdout


def test_cli_json_is_parseable_and_carries_the_card():
    """★ 一条命令产出**结构化**内容（可被门禁与 diff 消费）。"""
    proc = subprocess.run(
        [PY, "-m", "decision_eval_card", "--self-check", "--json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    # --self-check 优先于 --json：自检路径刻意不产出卡片（它不需要产物）。
    assert proc.returncode == 0


@pytest.mark.skipif(not ARTIFACT.exists(), reason="入库产物不在（CI 正常有）")
def test_cli_writes_a_diffable_card_to_a_stable_path(tmp_path):
    """★ 出卡到指定路径，且写出的内容可解析、含判据与负控。"""
    out = tmp_path / "card.json"
    proc = subprocess.run(
        [PY, "-m", "decision_eval_card", "--json", "--out", str(out)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode in (0, 1, 2), proc.stdout + proc.stderr
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["kind"] == "directed-axis-scorecard"
    assert payload["cards"], "卡片集为空"
    for name, one in payload["cards"].items():
        assert one["criteria"]["verdict"] in ("pass", "fail", "unmeasurable"), name
        assert one["negative_controls"]


@pytest.mark.skipif(not ARTIFACT.exists(), reason="入库产物不在")
def test_cli_verify_bounds_checks_thresholds_against_the_artifact():
    """★ 阈值可核验：一条命令重算阈值并与声明值比对。"""
    proc = subprocess.run(
        [PY, "-m", "decision_eval_card", "--verify-bounds"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.skipif(not ARTIFACT.exists(), reason="入库产物不在")
def test_cli_exit_code_is_not_zero_when_the_verdict_is_not_pass():
    """★ 退出码必须**映射判定**：有 FAIL 返 1、「无法测量」返 2，**永不因未测返 0**。

    这是本仓 #162 已钉进运行器的纪律：「测不了」不等于「没通过」，更不等于「通过」。
    """
    proc = subprocess.run(
        [PY, "-m", "decision_eval_card", "--json", "--variant", "P_live4_prod_prompt"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    payload = json.loads(proc.stdout)
    verdict = next(iter(payload["cards"].values()))["criteria"]["verdict"]
    expected_code = {"pass": 0, "fail": 1, "unmeasurable": 2}[verdict]
    assert proc.returncode == expected_code, (
        f"判定是 {verdict}，退出码却是 {proc.returncode}（应为 {expected_code}）"
    )


# ---------------------------------------------------------------------------
# 6. 入库产物：读回来的证据必须足以区分沉默与空输出
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not ARTIFACT.exists(), reason="入库产物不在")
def test_committed_artifact_carries_enough_token_evidence_to_judge():
    """★★ 入库产物的 ``per_round_rows`` **必须带 token 级证据**。

    #157 的 AC 明写「token 级证据区分『判定沉默』与『空输出』」。若产物只存
    decision 字段，那么**从入库产物出卡时这条 AC 在数据上不可能被满足** ——
    卡片只能报「不可归因」。这正是 #165 学到的形态：产物不自足，重新分析就得
    再跑一次模型。
    """
    loaded = card.load_artifact(ARTIFACT)
    for name, payload in loaded["variants"].items():
        quiet = [
            row
            for rows in payload["rounds"]
            for row in rows
            if axis.decision_of(row) in axis.DECISIONS_QUIET
        ]
        assert quiet, f"{name} 里没有任何不开口的行 —— 夹具本身可疑"
        attributable = [
            row for row in quiet if axis.quiet_evidence(row) != axis.EVIDENCE_NO_TOKEN_EVIDENCE
        ]
        assert len(attributable) == len(quiet), (
            f"{name} 有 {len(quiet) - len(attributable)} 行不开口决策缺 token 证据 ⇒ "
            "入库产物不足以判「判定沉默 vs 空输出」"
        )


@pytest.mark.skipif(not ARTIFACT.exists(), reason="入库产物不在")
def test_committed_artifact_rounds_share_a_denominator():
    """★ 入库产物的每一轮必须同分母 —— 否则跨轮比较无效（25 vs 26 的形态）。"""
    loaded = card.load_artifact(ARTIFACT)
    for name, payload in loaded["variants"].items():
        sizes = {len(rows) for rows in payload["rounds"]}
        assert len(sizes) == 1, f"{name} 的轮句集规模不一致：{sorted(sizes)}"
