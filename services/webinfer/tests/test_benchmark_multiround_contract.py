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
import os
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

#: 决策 → 该决策在 token 层的典型首 token（#157 的读侧靠它区分沉默与空输出）。
#: ``silence`` 是单 special token 151669；``not-for-me`` 首位实测就是 151670
#: 后跟普通 token —— 所以读侧**不能**用首位 token 判「这一轮在不在说话」。
_TOKENS_FOR_DECISION: dict[str, list[int]] = {
    "silence": [151669, 151645],
    "response": [151670, 200, 300],
    "delegation": [151670, 400, 500],
    "not-for-me": [151670, 222, 99507],
}


def _rows(decision_for_group=None, *, decisions_only=False):
    """One round of rows in the shape the real benchmark produces.

    ``emission`` is included deliberately: `emission_breakdown` reads it, and
    a fixture that omits it would make this contract test pass over a shape
    the real path can never produce.

    ★ #157: ``emitted_token_ids`` is included for the same reason. It is the
    only thing that separates "the model decided to stay silent" from "the model
    emitted nothing" (``</silence>`` is a special token stripped from content),
    and the projection that reaches the committed artifact is built from it.
    A fixture without it would let the projection be silently evidence-less.

    ``decision_for_group`` overrides the per-group decision, so a round can be
    made deliberately different from its neighbours (needed to give the
    aggregation something to measure).
    """
    mapping = dict(_PERFECT)
    if decision_for_group:
        mapping.update(decision_for_group)
    rows = []
    for cid, text, group, category, *_r in CASES:
        decision = mapping[group]
        row = {
            "id": cid,
            "text": text,
            "expected": group,
            "category": category,
            "decision": decision,
            "emission": "response_special_token_151670",
            "ok": True,
            "correct": True,
        }
        if not decisions_only:
            row["emitted_token_ids"] = list(_TOKENS_FOR_DECISION[decision])
        rows.append(row)
    return rows


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


def _variant_payload_oracle():
    """The **full** ``variant_result_payload`` output, offline.

    ``main()``'s loop variable ``payload`` is this object (not the rounds
    report alone), so the nested-path oracle must resolve against it —
    resolving against the rounds report would report every
    ``payload["rounds_report"][…]`` read as missing.
    """
    per_round_rows = [_rows() for _ in range(3)]
    return _import_benchmark().variant_result_payload(
        rows=per_round_rows[-1],
        per_round_stats=[score.summarize(r) for r in per_round_rows],
        per_round_rows=per_round_rows,
        per_round_wall=[1.0, 1.0, 1.0],
        prompt="（离线桩，零重叠）",
        rounds_requested=3,
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


#: 顶层名字 → 该名字下**已知合法**的键路径（``("rounds_report", "cost")`` 表示
#: 先取 ``x["rounds_report"]`` 再取 ``["cost"]``）。``None`` 表示「只扫一层」。
_KNOWN_PATHS: dict[str, set[tuple[str, ...]]] = {}


def _subscript_paths(fn: ast.AST) -> dict[str, set[tuple[str, ...]]]:
    """Collect every nested ``x["a"]["b"]…`` path rooted at a local name.

    ★ 必须处理**嵌套**读取。早先的版本只看 ``ast.Subscript`` 的**直接**基名，
    于是 ``payload["rounds_report"]["case_stabilty"]`` 这类拼错的**第二层**键
    完全逃过扫描 —— 而 #165 真实崩的就是一条嵌套读取（``payload["cost"]``，
    当时在更深的层级上）。只守一层的检查会给出**虚假的安全感**。
    """
    out: dict[str, set[tuple[str, ...]]] = {}
    for node in ast.walk(fn):
        if not isinstance(node, ast.Subscript):
            continue
        parts: list[str] = []
        cur: ast.AST = node
        while isinstance(cur, ast.Subscript):
            if not (isinstance(cur.slice, ast.Constant) and isinstance(cur.slice.value, str)):
                break
            parts.append(cur.slice.value)
            cur = cur.value
        if not parts or not isinstance(cur, ast.Name):
            continue
        out.setdefault(cur.id, set()).add(tuple(reversed(parts)))
    return out


def test_every_subscript_read_in_main_is_covered_statically():
    """★ 不靠运行、不靠人眼：把 `main()` 里的下标读取（**含嵌套**）扫出来.

    这是本文件里最要紧的一条 —— 它守的不是某一个已知的键，
    而是「读了一个没人产出的键」这一类缺陷（#165 崩的就是这一类）。
    真机脚本不在 CI 的 pytest 矩阵、也不在 CI 的 ruff 范围内，
    所以这层静态检查是它唯一的离线防线。

    ⚠️ **覆盖边界（勿高估它）**：只覆盖 ``ast.Name`` 为根的下标读取，
    即 ``payload[...]`` / ``cost[...]`` 这类**局部变量直接下标**。
    经函数返回值、属性或循环变量间接取的键（例如 ``payload.get(…)``、
    ``for k in payload: payload[k]``）**不在**覆盖内 —— 那些由行为测试守。
    声称覆盖「整类」是不诚实的；这里守住的是**曾经真实崩过的那一类**。
    """
    tree = ast.parse(BENCH.read_text(encoding="utf-8"))
    main_fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "main")
    paths = _subscript_paths(main_fn)

    # ① `payload[...]` 的第一层键必须都在契约清单里。
    payload_first = {p[0] for p in paths.get("payload", set())}
    unknown = sorted(payload_first - set(CONSUMED_RESULT_KEYS))
    assert not unknown, (
        f"main() 读了 payload[{unknown!r}]，但契约清单里没有这些键 —— "
        "要么组装里加了它（同步 CONSUMED_RESULT_KEYS），要么这是个笔误"
    )

    # ② ★ 嵌套的第二层键必须真的存在（这是早先漏掉、真实崩过的那一层）。
    report = _variant_payload_oracle()
    for path in sorted(paths.get("payload", set())):
        if len(path) < 2:
            continue
        target: object = report
        for depth, key in enumerate(path, 1):
            assert isinstance(target, dict), f"payload{list(path)} 在深度 {depth} 处不是 dict"
            assert key in target, (
                f"main() 读到 payload{list(path)}，但第 {depth} 层的键 {key!r} "
                f"在组装产物里不存在：{sorted(target)[:8]}"
            )
            target = target[key]

    # ③ `cost[...]` 的读取必须真的在 cost 块里。
    unknown_cost = sorted(
        {p[0] for p in paths.get("cost", set())} - set(report["rounds_report"]["cost"])
    )
    assert not unknown_cost, f"main() 读了 cost[{unknown_cost!r}]，但 cost 块里没有"

    # ④ 反向：契约清单里的每个键都必须真的被 main() 读到（防止清单腐化）。
    assert payload_first, "没在 main() 里扫到任何 payload[...] 读取 —— 静态扫描失效了"
    assert paths.get("payload"), "没扫到任何 payload 的嵌套路径 —— 嵌套处理可能退化了"


def test_the_static_scan_would_catch_a_first_level_typo():
    """★ 负控①：第一层键拼错必须被发现。"""
    assert _scan_finds_bad_path('payload["rounds_report"]', 'payload["rounds_reprot"]')


def test_the_static_scan_would_catch_a_nested_typo():
    """★★ 负控②：**嵌套**第二层键拼错也必须被发现。

    早先的实现漏掉这一整层（评审实测：把 ``case_stability`` 拼成
    ``case_stabilty`` 能全绿通过）。这条负控保证那个洞已被堵上。
    """
    assert _scan_finds_bad_path(
        'payload["rounds_report"]["case_stability"]',
        'payload["rounds_report"]["case_stabilty"]',
    )


def _scan_finds_bad_path(good: str, bad: str) -> bool:
    """把 ``main()`` 里的 ``good`` 换成 ``bad``，看静态检查是否报错。"""
    source = BENCH.read_text(encoding="utf-8")
    assert good in source, f"锚点 {good!r} 不在脚本里 —— 本负控失效，需更新"
    tampered = source.replace(good, bad, 1)
    assert tampered != source

    tree = ast.parse(tampered)
    main_fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "main")
    paths = _subscript_paths(main_fn)
    report = _variant_payload_oracle()

    problems: list[str] = []
    payload_first = {p[0] for p in paths.get("payload", set())}
    problems += sorted(payload_first - set(CONSUMED_RESULT_KEYS))
    for path in sorted(paths.get("payload", set())):
        target: object = report
        for key in path:
            if not isinstance(target, dict) or key not in target:
                problems.append(".".join(path))
                break
            target = target[key]
    return bool(problems)


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


def _import_benchmark_with_env(monkeypatch, value):
    """Re-import the benchmark with BENCH_ROUNDS set, returning (rc, message)."""
    import subprocess

    env = dict(os.environ)
    env["BENCH_ROUNDS"] = value
    # Import only (no main()) so no model call happens; module-level validation
    # runs at import time, which is where the guard lives.
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            f"import sys; sys.path[:0]=[{str(ROOT)!r}, {str(REPO_ROOT / 'services' / 'scripts')!r}];"
            f"import importlib.util as u;"
            f"s=u.spec_from_file_location('b', {str(BENCH)!r});"
            f"m=u.module_from_spec(s); s.loader.exec_module(m); print('ROUNDS=', m.ROUNDS)",
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    return proc


def test_invalid_bench_rounds_fails_loud_instead_of_clamping():
    """★★ spec 轴查出的缺陷：``ROUNDS = max(2, int(...))`` **静默**把 1 改成 2。

    要 1 轮的人拿到 2 轮**看不出来**；而如果他随后读了离散度，读到的数字
    来自一个他从未选择过的配置。坏值必须是**错误**，不是悄悄纠正。
    """
    proc = _import_benchmark_with_env(None, "1")
    assert proc.returncode != 0, "BENCH_ROUNDS=1 必须报错，不得静默改成 2"
    assert "BENCH_ROUNDS" in (proc.stderr or proc.stdout)


def test_non_integer_bench_rounds_fails_loud():
    proc = _import_benchmark_with_env(None, "three")
    assert proc.returncode != 0
    assert "整数" in (proc.stderr or proc.stdout)


def test_valid_bench_rounds_is_honoured_exactly():
    """正向对照：给了合法值就必须**原样**生效（证明上一条不是恒错）。"""
    proc = _import_benchmark_with_env(None, "4")
    assert proc.returncode == 0, proc.stderr
    assert "ROUNDS= 4" in proc.stdout


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


# ---------------------------------------------------------------------------
# 5. ★ #157：产物的投影必须带 token 级证据（否则读侧判不出沉默 vs 空输出）
# ---------------------------------------------------------------------------


def test_round_projection_carries_token_evidence():
    """★★ 投影里的每一行都必须带 ``n_tokens`` 与 ``first_token_id``。

    没有它们，**入库产物在读侧不可判**「模型判定沉默」与「模型什么都没输出」
    —— 因为 ``</silence>`` 是 special token，被服务端从 content 剥离后
    content 也是空串。#157 的核心 AC 就落在这一条上，而它必须**在产物里**成立
    （只在内存里的行上成立是不够的：卡片读的是入库产物）。
    """
    bench = _import_benchmark()
    projected = bench._decision_only_rows(_rows())
    assert projected, "投影为空 —— 夹具失效"
    for entry in projected:
        assert "n_tokens" in entry and "first_token_id" in entry, (
            f"{entry.get('id')} 的投影缺 token 证据字段 ⇒ 读侧无法区分沉默与空输出"
        )
        assert entry["n_tokens"] is not None, "夹具给了 token 列表，投影不该变成「无证据」"


def test_round_projection_reports_missing_token_evidence_as_none_not_zero():
    """★ 未产出 token 列表时必须是 ``None``（无证据），**不是 0**（零输出）。

    两者含义相反：``0`` 会说「模型什么都没吐」（失效输出），
    而真相是「本次没有采集到证据」（不可归因）。把它们混起来，
    正是本工单花大力气消除的那类缺陷。
    """
    bench = _import_benchmark()
    stripped = [{k: v for k, v in row.items() if k != "emitted_token_ids"} for row in _rows()]
    projected = bench._decision_only_rows(stripped)
    assert all(entry["n_tokens"] is None for entry in projected)
    assert all(entry["first_token_id"] is None for entry in projected)


def test_round_projection_records_the_first_token_id_verbatim():
    """★ 首位 token id 必须**原样**落进产物（它是判据的输入，不是展示字段）。"""
    bench = _import_benchmark()
    rows = _rows()
    rows[0]["emitted_token_ids"] = [151669, 151645]
    rows[1]["emitted_token_ids"] = [151670, 200, 300]
    projected = bench._decision_only_rows(rows)
    assert projected[0]["first_token_id"] == 151669
    assert projected[0]["n_tokens"] == 2
    assert projected[1]["first_token_id"] == 151670
    assert projected[1]["n_tokens"] == 3


def test_projected_rounds_feed_the_directed_axis_card_end_to_end(tmp_path):
    """★★ 端到端：#157 的卡片必须能**只凭入库产物**判出沉默证据。

    这条把两个模块钉在一起 —— 单独测 `_decision_only_rows` 会漏掉
    「字段名对不上」这一整类（#165 真机崩溃的形态：两侧各自正确，接口不对）。
    """
    import json
    import sys as _sys

    bench = _import_benchmark()
    rows = _rows()
    # ★ 必须挑**不开口**的行来注入：只有它们才走 token 证据判定
    #   （开口行的 ``quiet_evidence`` 是 ``not_quiet``，改它们什么也证明不了）。
    quiet_indices = [
        index for index, row in enumerate(rows) if row["decision"] in ("silence", "not-for-me")
    ]
    assert len(quiet_indices) >= 2, "夹具里没有足够的不开口行 —— 本测试会变成空断言"
    silent_index, empty_index = quiet_indices[0], quiet_indices[1]
    rows[silent_index]["decision"] = "silence"
    rows[silent_index]["emitted_token_ids"] = [151669, 151645]
    rows[empty_index]["emitted_token_ids"] = []
    payload = bench.variant_result_payload(
        rows=rows,
        per_round_stats=[score.summarize(rows) for _ in range(3)],
        per_round_rows=[rows, rows, rows],
        per_round_wall=[1.0, 1.0, 1.0],
        prompt="（离线桩，零重叠）",
        rounds_requested=3,
    )
    path = tmp_path / "artifact.json"
    path.write_text(
        json.dumps(
            {
                "results": {
                    "V": {
                        "rows": payload["rows"],
                        "per_round_rows": payload["per_round_rows"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    if str(ROOT) not in _sys.path:
        _sys.path.insert(0, str(ROOT))
    import decision_eval_axis as axis_mod
    import decision_eval_card as card_mod

    loaded = card_mod.load_artifact(path)
    rounds_rows = loaded["variants"]["V"]["rounds"]
    by_id = {row["id"]: row for row in rounds_rows[0]}
    evidences = {row["id"]: axis_mod.quiet_evidence(row) for row in rounds_rows[0]}
    assert axis_mod.EVIDENCE_NO_TOKEN_EVIDENCE not in evidences.values(), (
        "卡片从入库产物读回的行仍报「无 token 证据」⇒ 产物的投影字段与读侧对不上"
    )
    assert evidences[rows[silent_index]["id"]] == axis_mod.EVIDENCE_EVIDENCED, (
        "151669 开头的沉默必须被读成「有留痕的判定」"
    )
    assert evidences[rows[empty_index]["id"]] == axis_mod.EVIDENCE_EMPTY_OUTPUT, (
        "空 token 列表必须被读成「空输出」——否则读侧把失效输出洗成了有效证据"
    )
    # ★ 端到端到底：卡片自己也得看得见这条空输出（不只是模块函数单独能判）。
    assert by_id[rows[empty_index]["id"]]["n_tokens"] == 0
