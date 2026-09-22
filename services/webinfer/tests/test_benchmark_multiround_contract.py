# ruff: noqa: RUF001, RUF002, RUF003
"""契约测试：真机跑分脚本的结果结构与消费方**对得上**（工单 #165）.

★ 这个文件为什么存在（不是「补一个测试」，是修一个真实事故）
------------------------------------------------------------
#165 实现过程中，**一次已跑完 336 次推理的真机轮在最后写盘时崩了**
（`KeyError: 'cost'`）：结果块的组装内联在 `main()` 里，重构改掉了键名，
而读取方还在读旧键。等崩的时候，7 分钟与 336 次推理**已经花掉了**，
且**结果文件一个字节都没写出来**。

根因不是「写错一个键」，而是**结构性的**：`services/scripts` **不在 CI 的
pytest 矩阵内**（矩阵 = memory-store / background-agent / webinfer / webui /
tts），所以那个文件里的任何 `KeyError` 都**只会**等到真机跑完才暴露。
这正是本仓 §6.4 反复记的那一类（「CI 绿 ≠ 断言有效」），
只是这次连 CI 都没覆盖到。

**修法**：把结果组装抽成**纯函数** `variant_result_payload`
（无 I/O、不调模型），并在 **CI 可见处**（本目录）断言
「组装产出的键 ≥ 消费方读取的键」。于是同类错误在**离线、秒级**转红。

Run: cd services/webinfer && python -m pytest tests/test_benchmark_multiround_contract.py -q
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
BENCH = REPO_ROOT / "services" / "scripts" / "benchmark_production_live_prompt.py"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import decision_eval_rounds as rounds  # noqa: E402
import decision_eval_score as score  # noqa: E402
from decision_eval_set import (  # noqa: E402
    CASES,
    GROUP_DELEGATE,
    GROUP_DIRECTED,
    GROUP_NONDIRECTED,
)

#: 消费方（打印 / 写盘 / diff）从 result 块里读的键。
#: ★ 这份清单来自阅读 `benchmark_production_live_prompt.py` 的 `main()`；
#: 若那里新增读取，**必须**同步加到这里 —— 这正是本测试要守的契约。
CONSUMED_RESULT_KEYS: tuple[str, ...] = (
    "stats",
    "subsets",
    "emission_breakdown",
    "category_breakdown",
    "rows",
    "rounds_report",
    "historical_comparable_stats",
)

#: 消费方从 `rounds_report` 里读的键。
CONSUMED_ROUNDS_KEYS: tuple[str, ...] = (
    "metrics",
    "case_stability",
    "denominator",
    "open_book",
    "cost",
    "rounds_completed",
    "rounds_requested",
)

_PERFECT = {
    GROUP_DIRECTED: "response",
    GROUP_NONDIRECTED: "not-for-me",
    GROUP_DELEGATE: "delegation",
}


def _rows(decision_for_group=None):
    """One round of rows in the shape the real benchmark produces.

    ``emission`` is included deliberately: `emission_breakdown` reads it, and
    a fixture that omits it would make this contract test pass over a shape
    the real path can never produce.

    ``decision_for_group`` overrides the per-group decision, so a round can be
    made deliberately different from its neighbours (needed to give the
    aggregation something to measure).
    """
    mapping = dict(_PERFECT)
    if decision_for_group:
        mapping.update(decision_for_group)
    return [
        {
            "id": cid,
            "text": text,
            "expected": group,
            "category": category,
            "decision": mapping[group],
            "emission": "response_special_token_151670",
            "ok": True,
            "correct": True,
        }
        for cid, text, group, category, *_r in CASES
    ]


def _payload(rounds_n=3):
    """Reproduce exactly what ``variant_result_payload`` builds, offline."""
    per_round_rows = [_rows() for _ in range(rounds_n)]
    return rounds.build_rounds_report(
        [score.summarize(r) for r in per_round_rows],
        per_round_rows,
        prompt="（离线桩，零重叠）",
        round_case_ids=[[r["id"] for r in rws] for rws in per_round_rows],
        round_wall_seconds=[1.0] * rounds_n,
        llm_calls=len(CASES) * rounds_n,
        rounds_requested=rounds_n,
    )


# ---------------------------------------------------------------------------
# 1. ★ 组装产出的键必须覆盖消费方读取的键
# ---------------------------------------------------------------------------


def test_rounds_report_exposes_every_key_the_benchmark_reads():
    """★ 回归 #165 的真机崩溃：`cost` 曾在 `rounds_report` 里读不到。"""
    report = _payload()
    missing = [k for k in CONSUMED_ROUNDS_KEYS if k not in report]
    assert not missing, (
        f"benchmark 会读这些键但 rounds_report 里没有：{missing} —— "
        "真机上会在跑完所有推理之后才崩（#165 已实际发生一次）"
    )


def test_cost_key_is_reachable_exactly_as_the_benchmark_reaches_it():
    """★ 崩溃点的**逐字**复现：脚本读的是 ``payload["cost"]["rounds"]``.

    这条断言的是**访问路径**，不是「键存在」—— 崩的那次键是存在的，
    只是层级不对（在 ``rounds_report`` 里，而读取方找的是顶层）。
    """
    report = _payload()
    cost = report["cost"]
    assert cost["rounds"] == 3
    assert cost["llm_calls"] == len(CASES) * 3
    assert cost["seconds_per_llm_call"] is not None


def test_variant_result_payload_keys_match_the_consumer_list():
    """★ 契约：`variant_result_payload` 的产出必须覆盖消费方清单。

    直接 import 真机脚本（它有 `sys.path` 引导，纯导入不触发模型调用）。
    """
    bench = _import_benchmark()
    payload = bench.variant_result_payload(
        rows=_rows(),
        per_round_stats=[score.summarize(_rows()) for _ in range(3)],
        per_round_rows=[_rows() for _ in range(3)],
        per_round_wall=[1.0, 1.0, 1.0],
        prompt="（离线桩，零重叠）",
        rounds_requested=3,
    )
    missing = [k for k in CONSUMED_RESULT_KEYS if k not in payload]
    assert not missing, f"variant_result_payload 漏了消费方会读的键：{missing}"
    assert payload["emission_breakdown"]
    assert payload["historical_comparable_stats"]["n_directed"] == 25


def test_variant_result_payload_requires_at_least_two_rounds():
    """单轮算不出离散度 —— 组装必须**报错**，不得默默降级成单轮结果。"""
    bench = _import_benchmark()
    with pytest.raises(ValueError, match="轮"):
        bench.variant_result_payload(
            rows=_rows(),
            per_round_stats=[score.summarize(_rows())],
            per_round_rows=[_rows()],
            per_round_wall=[1.0],
            prompt="x",
            rounds_requested=1,
        )


# ---------------------------------------------------------------------------
# 2. ★ 静态契约：脚本里读取的键，必须真的由组装处产出
# ---------------------------------------------------------------------------


def test_every_subscript_read_in_main_is_covered_statically():
    """★ 不靠运行、不靠人眼：把 `main()` 里的**全部下标读取**扫出来.

    这是本文件里最要紧的一条 —— 它守的不是某一个已知的键，
    而是**整类**「读了一个没人产出的键」的缺陷（#165 崩的就是这一类）。
    真机脚本不在 CI 矩阵里，所以这层静态检查是它唯一的离线防线。
    """
    tree = ast.parse(BENCH.read_text(encoding="utf-8"))
    main_fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "main")

    # 收集 main() 里对形如 X["key"] 的读取，X 是局部名。
    read_keys: dict[str, set[str]] = {}
    for node in ast.walk(main_fn):
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        ):
            read_keys.setdefault(node.value.id, set()).add(node.slice.value)

    # ① `payload[...]` 的读取必须全在 variant_result_payload 的产出里。
    payload_reads = read_keys.get("payload", set())
    unknown = sorted(payload_reads - set(CONSUMED_RESULT_KEYS))
    assert not unknown, (
        f"main() 读了 payload[{unknown!r}]，但契约清单里没有这些键 —— "
        "要么组装里加了它（同步 CONSUMED_RESULT_KEYS），要么这是个笔误"
    )

    # ② `cost[...]` 的读取必须来自 payload["rounds_report"]["cost"]。
    if "cost" in read_keys:
        report_keys = _payload()
        unknown_cost = sorted(read_keys["cost"] - set(report_keys["cost"]))
        assert not unknown_cost, f"main() 读了 cost[{unknown_cost!r}]，但 cost 块里没有"

    # ③ 反向：契约清单里的每个键都必须真的被 main() 读到（防止清单腐化）。
    assert payload_reads, "没在 main() 里扫到任何 payload[...] 读取 —— 静态扫描失效了"


def test_the_static_scan_would_catch_a_typo():
    """★ 负控：静态扫描必须**真的能**发现坏键，而不是恒过。"""
    source = BENCH.read_text(encoding="utf-8")
    tampered = source.replace('payload["rounds_report"]', 'payload["rounds_reprot"]', 1)
    assert tampered != source, "没找到可篡改的锚点 —— 本负控失效，需更新"

    tree = ast.parse(tampered)
    main_fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "main")
    read_keys: set[str] = set()
    for node in ast.walk(main_fn):
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id == "payload"
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        ):
            read_keys.add(node.slice.value)
    assert sorted(read_keys - set(CONSUMED_RESULT_KEYS)), (
        "把 rounds_report 拼错成 rounds_reprot 却扫不出来 ⇒ 静态检查是恒真的"
    )


# ---------------------------------------------------------------------------
# 3. 离线可复现：把已落盘的多轮结果**重新聚合**，值与文件中一致
# ---------------------------------------------------------------------------


def _import_benchmark():
    """Import the real-machine script without running it.

    The script bootstraps ``sys.path`` itself (webinfer + scripts dirs) and
    imports only stdlib at module level, so importing it triggers no model
    call and no network access.
    """
    import importlib.util

    scripts_dir = REPO_ROOT / "services" / "scripts"
    for path in (str(ROOT), str(scripts_dir)):
        if path not in sys.path:
            sys.path.insert(0, path)
    spec = importlib.util.spec_from_file_location("_bench_contract_probe", BENCH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_rounds_are_configurable_via_env_and_default_above_two():
    """★ AC#1：N 可配，且默认 > 2。"""
    bench = _import_benchmark()
    assert bench.ROUNDS >= 2
    assert rounds.DEFAULT_ROUNDS > 2, "默认轮数必须 > 2（2 轮没有中位可言）"


def test_open_book_flag_is_wired_for_the_production_prompt():
    """★ AC#7：开卷与否必须随结果一起给出，且对生产 prompt 判为真。"""
    bench = _import_benchmark()
    block = rounds.open_book_report(bench.build_production_prompt(include_profile=True))
    assert block["is_open_book"] is True
    assert block["n_overlapping"] == 10


# ---------------------------------------------------------------------------
# 4. ★ 落盘产物必须**自足**：能重新读回来，不必重跑模型
# ---------------------------------------------------------------------------


def test_variant_payload_stores_every_round_so_it_can_be_re_analysed(tmp_path):
    """★★ 只存末轮的 `rows` 会让多轮证据**一次性用完**。

    `decision_eval_rounds --from-results` 要求文件里能读出 ≥2 轮；
    真机产物若只留末轮副本，重新分析就得**再花 3 分钟跑一遍模型** ——
    而多轮证据的全部价值恰恰在于它可以被反复重读。
    """
    import json

    bench = _import_benchmark()
    per_round_rows = [
        _rows(),
        _rows({GROUP_NONDIRECTED: "response"}),
        _rows(),
    ]
    payload = bench.variant_result_payload(
        rows=per_round_rows[-1],
        per_round_stats=[score.summarize(r) for r in per_round_rows],
        per_round_rows=per_round_rows,
        per_round_wall=[1.0, 1.0, 1.0],
        prompt="（离线桩，零重叠）",
        rounds_requested=3,
    )
    assert len(payload["per_round_rows"]) == 3, "产物必须留存每一轮的行"

    # 端到端：把产物落盘 → 用 CLI 同一条读取路径重新聚合 → 必须读出 3 轮
    path = tmp_path / "artifact.json"
    path.write_text(
        json.dumps(
            {
                "results": {
                    "V": {"rows": payload["rows"], "per_round_rows": payload["per_round_rows"]}
                }
            }
        ),
        encoding="utf-8",
    )
    report = rounds.report_from_results_files([str(path)], "V")
    assert report["rounds_completed"] == 3
    assert report["metrics"]["not_for_me_recall_pct"]["per_round"] == [100.0, 0.0, 100.0]
