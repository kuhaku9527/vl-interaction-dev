# ruff: noqa: RUF001, RUF002, RUF003
# (RUF001/002/003 = ambiguous fullwidth punctuation. This file's prose is Chinese
# and quotes real test sentences; the same suppression is used by the sibling
# eval test modules and by decision_eval_timing*.py. Established repo convention.)
"""时序轴**卡片**的契约测试（工单 #158，spec #154）.

本文件钉住卡片层的三件事：

  1. ★ **两轴分别报告、不合成单一 accuracy** —— 且这条约束**可判红**；
  2. **结果结构化、可 diff**（#158 AC）—— 含「键顺序固定」与「两次运行
     逐字相同」这两条比「能 json.dumps」强得多的性质；
  3. **缺结果即判红**（fail-closed）：空输入 / 缺失的坐标不产出绿卡。

★ 与轴层、判据层测试的分工：那两层测「算对了吗、判对了吗」，
本层测「**装配出来的产物**是否把那些性质带出去了」——
一个算得很对但产物里丢掉出处/分母的卡片，在门禁里与算错没有区别。

Run: cd services/webinfer && python -m pytest tests/test_decision_eval_timing_card.py -q
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import decision_eval_timing_card as K
import pytest
from decision_eval_timing_synthetic import synthetic_healthy_rows

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
TIMING_EVENTS = FIXTURE_DIR / "live_decision_timing_events.jsonl"
TIMING_TRUTH = FIXTURE_DIR / "decision_timing_truth.json"


def _card() -> dict:
    return K.build_card(TIMING_EVENTS, TIMING_TRUTH)


def _card_of(report: dict) -> dict:
    return next(iter(report["cards"].values()))


# ---------------------------------------------------------------------------
# 1. 从冻结夹具出卡（不依赖真机）
# ---------------------------------------------------------------------------


def test_card_builds_from_the_frozen_fixture():
    """#158 AC: 可从冻结的事件夹具算出（不依赖真机在跑）."""
    report = _card()
    assert report["kind"] == "timing-axis-scorecard"
    assert report["ticket"] == "#158"
    card = _card_of(report)
    assert card["reading"]["is_frozen_fixture"] is True
    assert card["reading"]["n_rounds"] == 34


def test_card_carries_all_three_quantities():
    """#158 AC: 卡片里必须同时有 onset / 每秒误触发 / premature."""
    metrics = _card_of(_card())["metrics"]
    assert metrics["onset_latency_ms"]["median"] is not None
    assert metrics["onset_latency_ms"]["p90"] is not None
    assert metrics["spurious_triggers_per_second"] is not None
    assert metrics["premature_rate_pct"] is not None


# ---------------------------------------------------------------------------
# 2. ★ 两轴不合成
# ---------------------------------------------------------------------------


def test_card_contains_no_combined_score():
    """★ 卡片的**任何一层**都不得出现合并分数键."""
    card = _card_of(_card())
    assert card["forbidden_combined_keys"] == [], card["forbidden_combined_keys"]


def test_two_axes_report_has_both_axes_and_no_combined_score():
    """★ 两轴并排产物：两轴都在，且**没有**合并分数."""
    report = K.build_two_axes(timing_report=_card())
    assert set(report["axes"]) == {"timing", "directed"}
    assert report["forbidden_combined_keys"] == []


def test_two_axes_keeps_verdicts_independent():
    """★ 两轴的判定必须**各自独立**，不得被对方拉平.

    ★ 这是「不合成」最容易被悄悄违反的地方：一个「取最差/取平均」的总分
    会让一轴的退化被另一轴的正常掩盖。本断言钉住：两轴的 ``verdict``
    来自各自的计算，且在真实数据上确实**不同**（定向轴 FAIL、时序轴 PASS）。
    """
    report = K.build_two_axes(timing_report=_card())
    timing = report["axes"]["timing"]["card"]["verdict"]
    directed = report["axes"]["directed"]["card"]["verdict"]
    assert timing == K.VERDICT_PASS
    assert directed == K.VERDICT_FAIL, "定向轴在这份入库产物上应为 FAIL（#157 的 D4 泛化召回判红）"
    assert timing != directed, "两轴判定相同 ⇒ 无法分辨它们是否真的独立"
    # 产物里不得有任何「总分」字段
    assert K.forbidden_combined_keys(report) == []


def test_directed_axis_absence_is_reported_as_unmeasured(monkeypatch: pytest.MonkeyPatch):
    """★ 定向轴产物缺席 ⇒ 明说「未测」，**不给空分数**（0 会被读成「全错」）.

    ★ 这里逼的是**真实的缺席路径**：往 ``build_two_axes`` 依赖的那个构造函数
    注入一个 ``FileNotFoundError``，于是走的是与「产物不在本机」完全相同的分支。
    """
    import decision_eval_card as directed

    def _boom(*_a, **_k):
        raise FileNotFoundError("产物不在")

    monkeypatch.setattr(directed, "build_card", _boom)
    report = K.build_two_axes(timing_report=_card())
    entry = report["axes"]["directed"]["card"]
    assert entry["verdict"] == K.VERDICT_UNMEASURABLE
    assert "未测" in entry["unavailable_reason"]


# ---------------------------------------------------------------------------
# 3. 结构化、可 diff
# ---------------------------------------------------------------------------


def test_repeated_builds_are_byte_identical():
    """★ 同一次输入两次出卡必须**逐字相同**（否则 diff 里全是噪声）.

    ★ 比「能 json.dumps」强得多：浮点残渣（``0.30000000000000004``）会让
    两次运行的文件逐字节不同却毫无语义差异，而那正好毁掉本票 AC 要的 diff。
    """
    first = json.dumps(_card(), ensure_ascii=False, sort_keys=False)
    second = json.dumps(_card(), ensure_ascii=False, sort_keys=False)
    assert first == second


def test_key_order_is_fixed_by_construction():
    """键顺序由构造顺序决定（便于逐行 diff，而不是每次哈希乱序）."""
    metrics = _card_of(_card())["metrics"]
    assert list(metrics)[:3] == ["n_rounds", "n_speaking", "n_quiet"]


def test_card_is_json_serializable():
    """产物必须能直接落盘（``--json --out`` 供门禁读取）."""
    text = json.dumps(_card(), ensure_ascii=False)
    restored = json.loads(text)
    assert restored["ticket"] == "#158"


def test_diff_reports_changes_between_two_cards():
    """diff 必须能看出读数与判定的变化（本票 AC：可 diff）."""
    before = _card()
    after = json.loads(json.dumps(before))  # 深拷贝
    card = _card_of(after)
    card["metrics"]["onset_latency_ms"]["median"] = 99999.0
    for item in card["criteria"]:
        if item["criterion_id"] == "T_ONSET_MEDIAN":
            item["verdict"] = K.VERDICT_FAIL
    card["verdict"] = K.VERDICT_FAIL

    text = K.diff_reports(before, after)
    assert "onset_latency_ms.median" in text
    assert "T_ONSET_MEDIAN" in text
    assert "总判定" in text


def test_diff_of_identical_cards_says_no_difference():
    """相同的两份卡 ⇒ 明说「无差异」，不打印一个空串（空串会被读成「diff 坏了」)."""
    text = K.diff_reports(_card(), _card())
    assert "无差异" in text


# ---------------------------------------------------------------------------
# 4. judge：产物必须把「哪里还不能信」带出去
# ---------------------------------------------------------------------------


def test_card_carries_the_latency_provenance():
    """★ 耗时出处必须落进产物（门禁与读者都要读它）."""
    provenance = _card_of(_card())["latency_source_block"]
    assert provenance["source"] == K.LATENCY_SOURCE_CHAIN_STAMP
    assert provenance["legal"] is True
    assert provenance["forbidden"]


def test_card_criteria_registry_carries_sources():
    """★ 每条判据都要带出处（#159 的门禁要追溯阈值来源）."""
    registry = _card_of(_card())["criteria_registry"]
    assert len(registry) == len(K.TIMING_CRITERIA)
    for item in registry:
        assert item["source"], f"{item['criterion_id']} 没有出处"


def test_card_carries_negative_controls_with_status():
    """★ 负控结果必须落进产物（本票 AC：判据可证伪要留痕）."""
    controls = _card_of(_card())["negative_controls"]
    assert controls
    statuses = {item["status"] for item in controls}
    assert "survived" not in statuses, (
        f"有负控存活：{[i['mutation_id'] for i in controls if i['status'] == 'survived']}"
    )
    dropped = next(i for i in controls if i["mutation_id"] == "dropped-round-stamp")
    assert dropped["status"] == "killed"


# ---------------------------------------------------------------------------
# 5. fail-closed
# ---------------------------------------------------------------------------


def test_empty_input_is_refused():
    """空输入 ⇒ 报错（不产出一张「零误触发」的健康卡）."""
    with pytest.raises(ValueError, match="至少要有一轮"):
        K.build_card_from_rows([])


def test_missing_fixture_fails_loud(tmp_path: Path):
    """夹具缺失 ⇒ 报错（缺结果等于没测）."""
    with pytest.raises((FileNotFoundError, ValueError)):
        K.build_card(tmp_path / "absent.jsonl", TIMING_TRUTH)


# ---------------------------------------------------------------------------
# 6. CLI 的退出码契约
# ---------------------------------------------------------------------------


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "decision_eval_timing_card", *args],
        cwd=str(Path(__file__).resolve().parent.parent),
        capture_output=True,
        text=True,
        check=False,
    )


def test_cli_self_check_passes():
    """``--self-check`` 三层全绿."""
    result = _run_cli("--self-check")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "verdict: PASS" in result.stdout


def test_cli_card_exit_code_maps_to_the_verdict():
    """退出码必须映射判定：FAIL→1、「无法测量」→2、PASS→0.

    ★ 「没测」不得返回 0（本仓 #162 已把这条纪律钉进运行器）。
    """
    result = _run_cli("--json")
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert _card_of(payload)["verdict"] == K.VERDICT_PASS


def test_cli_verify_bounds_matches_the_frozen_snapshot():
    """``--verify-bounds`` 必须与冻结快照一致（阈值没有漂移）."""
    result = _run_cli("--verify-bounds")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "matches=True" in result.stdout


def test_cli_writes_json_to_a_stable_path(tmp_path: Path):
    """``--out`` 写出结构化产物（#159 的门禁要读一个稳定路径）."""
    out = tmp_path / "timing-card.json"
    result = _run_cli("--out", str(out))
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["kind"] == "timing-axis-scorecard"
    # ★ 行尾纪律（AGENTS.md 字节核验）：newline="\n"，不得被写成 CRLF。
    assert b"\r\n" not in out.read_bytes()


def test_cli_two_axes_prints_both_axes():
    """``--two-axes`` 的人读输出必须两轴都在，且明说没有合并分数."""
    result = _run_cli("--two-axes")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "timing" in result.stdout
    assert "directed" in result.stdout
    assert "没有" in result.stdout and "合并分数" in result.stdout


# ---------------------------------------------------------------------------
# 7. 卡片层自检（含它自己的可证伪性）
# ---------------------------------------------------------------------------


def test_card_self_check_passes():
    """卡片层自检整体通过（轴层 + 判据层 + 卡片层）."""
    assert K.self_check() == 0


def test_combined_key_scan_is_falsifiable():
    """★ 合并键扫描**可证伪**：塞一个 accuracy 必须被抓到.

    没有这一条，「扫描返回空」只证明它被调用了，不证明它有效。
    """
    card = K.build_card_from_rows(synthetic_healthy_rows())
    tainted = {**card, "accuracy": 0.9}
    assert K.forbidden_combined_keys(tainted) == ["accuracy"]


def test_negative_control_registry_is_not_decorative():
    """负控清单里必须真有 killed 项（不是一张全绿的装饰表）."""
    controls = K.negative_control_registry()
    killed = [i for i in controls if i["status"] == "killed"]
    assert len(killed) >= len(K.MUTATIONS) - 1, (
        f"只有 {len(killed)} 个负控被杀死，共 {len(K.MUTATIONS)} 个"
    )
