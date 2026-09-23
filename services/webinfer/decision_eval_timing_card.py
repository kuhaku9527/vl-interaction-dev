# ruff: noqa: RUF001, RUF002, RUF003
"""时序轴卡片：**一条命令**产出结构化、可 diff 的时机读数（工单 #158）.

    python -m decision_eval_timing_card                  # 卡片（冻结事件夹具）
    python -m decision_eval_timing_card --json           # 结构化，供门禁 / diff
    python -m decision_eval_timing_card --self-check     # 离线负控自检（不需要模型）
    python -m decision_eval_timing_card --two-axes       # ★ 两轴并排（**无**合并分数）
    python -m decision_eval_timing_card --show-bounds    # 阈值 + 出处
    python -m decision_eval_timing_card --verify-bounds  # 从冻结快照重算阈值并比对
    python -m decision_eval_timing_card --diff-against FILE

这张卡回答第二个数字：**「就算判断对了，是不是说得是时候」**。
它**不**回答「该不该说」（那是 #157 定向轴），两轴**正交、不合成单一 accuracy**。

★ 本票的 ★ 负控在卡片上怎么看
------------------------------
``--self-check`` 会打印 ``dropped-round-stamp`` 那一行的实际判定；
它必须是 **fail**（而不是「无法测量」）—— 判「无法测量」会把「打点被删掉」
说成「这项不适用」，那是 fail-open。卡片里同一条判据的 ``verdict`` 字段
也是门禁要读的东西。

模块划分（``coding-standards.md`` §7：一个模块一个变化原因）
================================================  ==========================
:mod:`.decision_eval_timing`                        怎么算（onset / 速率 / premature）
:mod:`.decision_eval_timing_criteria`               怎么判（判据 / 负控 / 阈值出处）
:mod:`.decision_eval_timing_sources`                从哪读（事件流 × 真值 → 逐轮行）
:mod:`.decision_eval_timing_synthetic`              离线夹具（自检与负控用）
:mod:`.decision_eval_timing_report`                 怎么印 / 怎么 diff / 两轴并排
**本模块**                                          组装成一张卡 + CLI
================================================  ==========================

Run tests: cd services/webinfer && python -m pytest tests/test_decision_eval_timing_card.py -q
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

# ★ 本模块是**公开入口**（`python -m decision_eval_timing_card`、门禁与测试都从它取），
#   故有意识地**再导出**下层符号，使调用方只依赖一个名字。`X as X` 形式显式声明
#   「这是 re-export 而非本地使用」，ruff 的 F401 也认得它。
from decision_eval_timing import (
    FORBIDDEN_COMBINED_KEYS as FORBIDDEN_COMBINED_KEYS,
)
from decision_eval_timing import (
    LATENCY_SOURCE_CHAIN_STAMP as LATENCY_SOURCE_CHAIN_STAMP,
)
from decision_eval_timing import (
    MIN_SPEAKING_ROUNDS as MIN_SPEAKING_ROUNDS,
)
from decision_eval_timing import (
    forbidden_combined_keys as forbidden_combined_keys,
)
from decision_eval_timing import (
    timing_block as timing_block,
)
from decision_eval_timing_criteria import (
    MUTATIONS as MUTATIONS,
)
from decision_eval_timing_criteria import (
    TIMING_BOUNDS as TIMING_BOUNDS,
)
from decision_eval_timing_criteria import (
    TIMING_CRITERIA as TIMING_CRITERIA,
)
from decision_eval_timing_criteria import (
    VERDICT_FAIL as VERDICT_FAIL,
)
from decision_eval_timing_criteria import (
    VERDICT_PASS as VERDICT_PASS,
)
from decision_eval_timing_criteria import (
    VERDICT_UNMEASURABLE as VERDICT_UNMEASURABLE,
)
from decision_eval_timing_criteria import (
    criteria_verdicts as criteria_verdicts,
)
from decision_eval_timing_criteria import (
    explain_bounds as explain_bounds,
)
from decision_eval_timing_criteria import (
    overall_verdict as overall_verdict,
)
from decision_eval_timing_criteria import (
    self_check as criteria_self_check,
)
from decision_eval_timing_criteria import (
    verify_bounds as verify_bounds,
)
from decision_eval_timing_report import (
    diff_reports as diff_reports,
)
from decision_eval_timing_report import (
    render_both_axes as render_both_axes,
)
from decision_eval_timing_report import (
    render_card as render_card,
)
from decision_eval_timing_report import (
    render_report as render_report,
)
from decision_eval_timing_sources import (
    DEFAULT_TIMING_EVENTS as DEFAULT_TIMING_EVENTS,
)
from decision_eval_timing_sources import (
    DEFAULT_TIMING_TRUTH as DEFAULT_TIMING_TRUTH,
)
from decision_eval_timing_sources import (
    build_rows as build_rows,
)
from decision_eval_timing_synthetic import (
    synthetic_healthy_rows as synthetic_healthy_rows,
)

# --- 负控（复用判据模块的定义，此处只做「跑一遍并落进卡片」）-----------------


def negative_control_registry() -> list[dict]:
    """负控清单 + 对合成输入的**实际判定**（证明每条判据真的不能判绿）.

    ★ 它跑在**离线合成输入**上，不依赖真机也不依赖夹具产物 ——
    所以「判据是否可证伪」这件事在任何机器上都能被复核。
    ★ 与 :func:`decision_eval_timing_criteria.self_check` 用**同一条**路径与
    同一组负控定义（``MUTATIONS``）。把结果落进卡片，使「一条命令的产出」
    本身就带着判据可证伪的证据，而不只是自己声称可证伪。
    """
    baseline = timing_block(synthetic_healthy_rows())
    out: list[dict] = []
    for mutation in MUTATIONS:
        mutated = mutation.apply(baseline)
        verdicts = {item["criterion_id"]: item["verdict"] for item in criteria_verdicts(mutated)}
        fired = sorted(cid for cid, verdict in verdicts.items() if verdict == VERDICT_FAIL)
        missing = [cid for cid in mutation.must_fail if verdicts.get(cid) != VERDICT_FAIL]
        still_green = [cid for cid in mutation.must_not_pass if verdicts.get(cid) == VERDICT_PASS]
        if mutation.is_identity:
            status = "identity-baseline-clean" if not fired else "baseline-dirty"
        elif missing or still_green:
            status = "survived"
        else:
            status = "killed"
        out.append(
            {
                "mutation_id": mutation.mutation_id,
                "description": mutation.description,
                "is_identity_control": mutation.is_identity,
                "must_fail": list(mutation.must_fail),
                "must_not_pass": list(mutation.must_not_pass),
                "observed_failed": fired,
                "status": status,
                "missing_expected_failures": missing,
                "unexpectedly_passed": still_green,
            }
        )
    return out


# --- 组装整张卡 -------------------------------------------------------------


def build_card_from_rows(
    rows: list[dict],
    *,
    source: dict | None = None,
    reading: dict | None = None,
    latency_source: str = LATENCY_SOURCE_CHAIN_STAMP,
) -> dict:
    """逐轮行 → 时序轴卡（纯函数：无 I/O、不调模型）.

    Args:
        rows: 逐轮行（见 :func:`decision_eval_timing.timing_metrics` 的字段说明）。
        source: 输入出处（谁的事件流 / 谁的真值）。
        reading: 载入侧的读数（轮次数、有真值数等）。
        latency_source: 耗时出处。

    Returns
    -------
        ``json.dumps`` 友好的嵌套 dict，**键顺序固定**（构造顺序即顺序）。

    Raises
    ------
        ValueError: 空输入。
    """
    if not rows:
        raise ValueError("至少要有一轮才能出时序卡（空输入给不出任何读数）")

    block = timing_block(rows, latency_source=latency_source)
    results = criteria_verdicts(block)

    # ★ 自查：本卡**自己**先证明它没有合并分数键。判据 T_NO_COMBINED_ACCURACY
    #   是第二道门，不是唯一那道。
    card: dict = {
        "axis": "timing",
        "axis_question": block["axis_question"],
        "source": source or {},
        "reading": reading or {},
        "metrics": block["metrics"],
        "latency_source_block": block["latency_source_block"],
        "rounds": block["rounds"],
        "criteria": results,
        "verdict": overall_verdict(results),
        "caveats": block["caveats"],
        "criteria_registry": criteria_registry(),
        "negative_controls": negative_control_registry(),
    }
    card["forbidden_combined_keys"] = forbidden_combined_keys(
        {k: v for k, v in card.items() if k != "forbidden_combined_keys"}
    )
    return card


def criteria_registry() -> list[dict]:
    """判据清单（含出处），落进卡片以供电表可追溯."""
    return [
        {
            "criterion_id": item.criterion_id,
            "statement": item.statement,
            "metric": item.metric,
            "statistic": item.statistic,
            "direction": item.direction,
            "threshold": item.threshold,
            "source": item.source,
            "kind": item.kind,
        }
        for item in TIMING_CRITERIA
    ]


def build_card(
    events_path: str | Path = DEFAULT_TIMING_EVENTS,
    truth_path: str | Path = DEFAULT_TIMING_TRUTH,
) -> dict:
    """从冻结的事件夹具 + 真值标签建一张时序卡."""
    loaded = build_rows(events_path, truth_path)
    card = build_card_from_rows(
        loaded["rows"],
        source={
            "label": "冻结夹具",
            "events": loaded["events_path"],
            "truth": loaded["truth_path"],
            "truth_meta": loaded["truth_meta"],
        },
        reading=loaded["reading"],
    )
    return {
        "kind": "timing-axis-scorecard",
        "ticket": "#158",
        "spec": "#154",
        "events": loaded["events_path"],
        "truth": loaded["truth_path"],
        "cards": {"frozen-fixture": card},
    }


def build_two_axes(
    *,
    directed_artifact: str | None = None,
    timing_report: dict | None = None,
) -> dict:
    """★ 两轴**并排**的产物（定向轴来自 #157 的卡片，时序轴来自本模块）.

    ★ 存在的理由：父 spec §三要求两轴**分别报告、不合成**。一句写在文档里的
    要求不是防线 —— 这里给出的产物让「并排但独立」成为**唯一**可表达的形态，
    并且 :func:`forbidden_combined_keys` 会当场判红任何合并键。

    ⚠️ 定向轴一侧**可能缺席**（本机没有那份入库产物时）—— 缺席时**明说**
    「未测」，**不**给一个空分数（``0`` 会被读成「全错」）。
    """
    timing = timing_report or build_card()
    timing_card = next(iter(timing["cards"].values()))

    axes: dict = {
        "timing": {
            "question": "就算判断对了，是不是说得是时候",
            "card": timing_card,
        }
    }
    try:
        from decision_eval_card import build_card as build_directed_card

        directed = (
            build_directed_card()
            if directed_artifact is None
            else build_directed_card(directed_artifact)
        )
        axes["directed"] = {
            "question": "该说时说了吗 / 不该说时说了吗",
            "card": {
                "verdict": _worst_verdict(
                    [c["criteria"]["verdict"] for c in directed["cards"].values()]
                ),
                "overall": next(iter(directed["cards"].values()))["overall"],
                "by_subset": next(iter(directed["cards"].values()))["by_subset"],
                "source": {
                    "artifact": directed["artifact"],
                    "variant": next(iter(directed["cards"])),
                },
            },
        }
    except (FileNotFoundError, KeyError, ValueError) as exc:
        axes["directed"] = {
            "question": "该说时说了吗 / 不该说时说了吗",
            "card": {
                "verdict": VERDICT_UNMEASURABLE,
                "unavailable_reason": (
                    f"定向轴卡片无法构造：{exc} —— 缺席就是**未测**，"
                    "不给空分数（0 会被读成「全错」）"
                ),
            },
        }

    forbidden = forbidden_combined_keys(axes)
    return {
        "kind": "two-axis-report",
        "ticket": "#158",
        "spec": "#154",
        "note": (
            "两轴正交、分别报告。本结构里**没有**任何合并分数键"
            "（父 spec §三明确否决单一 accuracy）。"
        ),
        "axes": axes,
        "forbidden_combined_keys": forbidden,
    }


def _worst_verdict(verdicts: list[str]) -> str:
    """多 variant 的总判定：FAIL 优先，其次「无法测量」（与卡片同一条规则）."""
    if VERDICT_FAIL in verdicts:
        return VERDICT_FAIL
    if VERDICT_UNMEASURABLE in verdicts:
        return VERDICT_UNMEASURABLE
    return VERDICT_PASS


# --- 离线自检 ---------------------------------------------------------------


def self_check() -> int:
    """★ 离线可跑的负控自检（不需要模型、不需要真机）.

    跑三层，并把三层的读数都打出来：

    1. **轴层**（:func:`decision_eval_timing.self_check`）：三个量双向可辨；
    2. **判据层**（:func:`decision_eval_timing_criteria.self_check`）：
       每条判据都有负控，且 ★ 删掉轮次打点**判红**；
    3. ★ **卡片层**：证明卡片自己把「两轴不合成」与「合并键扫描」落进了产物，
       而且这份自检**能被证伪** —— 往卡里塞一个 ``accuracy`` 必须让本层判红。

    Returns
    -------
        ``0`` 通过 / ``1`` 不通过。
    """
    from decision_eval_timing import self_check as axis_self_check

    print("=== 时序轴卡片自检 (#158) ===")
    problems: list[str] = []

    axis_rc = axis_self_check()
    if axis_rc != 0:
        problems.append("轴层自检未通过（见上方输出）")

    criteria_rc = criteria_self_check()
    if criteria_rc != 0:
        problems.append("判据层自检未通过（见上方输出）")

    card = build_card_from_rows(synthetic_healthy_rows())
    print(f"  卡片层: 合成输入 total={card['verdict']} 合并键={card['forbidden_combined_keys']}")
    if card["forbidden_combined_keys"]:
        problems.append(
            f"★ 卡片里出现合并分数键 {card['forbidden_combined_keys']} ⇒ 违反「两轴不合成」"
        )
    killed = [item for item in card["negative_controls"] if item["status"] == "killed"]
    survived = [item for item in card["negative_controls"] if item["status"] == "survived"]
    dirty = [item for item in card["negative_controls"] if item["status"] == "baseline-dirty"]
    print(
        f"  负控: killed={len(killed)} survived={len(survived)} 基线脏={len(dirty)}"
        f"（共 {len(card['negative_controls'])} 个）"
    )
    if survived:
        problems.append(f"有负控在卡片层存活 {[i['mutation_id'] for i in survived]}")
    if dirty:
        problems.append("恒等对照发现基线不干净 —— 其它负控的「判红」证明不了任何事")
    if not any(i["mutation_id"] == "dropped-round-stamp" for i in killed):
        problems.append(
            "★ 「删掉轮次打点」这条负控没有在卡片层被判红（killed）—— 本票 ★ 负控要求它**判红**"
        )

    # ★ 本层自检的可证伪性：塞一个合并键，扫描必须抓到。
    tainted = {**card, "accuracy": 0.9}
    if not forbidden_combined_keys(tainted):
        problems.append(
            "★ 往卡片里塞 accuracy 后合并键扫描**没有**抓到 ⇒ 这条守卫是装饰（自检本身不可证伪）"
        )

    # ★ 两轴产物必须能被构造，且里面没有任何合并分数键。
    two_axes = build_two_axes()
    if two_axes["forbidden_combined_keys"]:
        problems.append(f"★ 两轴产物里出现合并分数键 {two_axes['forbidden_combined_keys']}")
    if "timing" not in two_axes["axes"]:
        problems.append("两轴产物里没有 timing 轴 ⇒ 分列报告没落地")

    if problems:
        print("  verdict: FAIL")
        for problem in problems:
            print(f"    - {problem}")
        return 1
    print(
        "  verdict: PASS（轴/判据/卡片三层全绿；删掉轮次打点判红；"
        "合并键扫描可证伪；两轴并排产物无合并分数）"
    )
    return 0


# --- CLI --------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """CLI：出卡 / 两轴并排 / 阈值核验 / 自检 / diff."""
    parser = argparse.ArgumentParser(
        description="时序轴记分卡（工单 #158）：onset 延迟 + 每秒误触发 + premature"
    )
    parser.add_argument("--events", default=DEFAULT_TIMING_EVENTS, help="冻结事件夹具")
    parser.add_argument("--truth", default=DEFAULT_TIMING_TRUTH, help="真值标签 sidecar")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    parser.add_argument("--out", default=None, help="把 JSON 写到该路径（供门禁读取）")
    parser.add_argument("--self-check", action="store_true", help="离线负控自检")
    parser.add_argument("--two-axes", action="store_true", help="输出两轴并排产物（无合并分数）")
    parser.add_argument("--directed-artifact", default=None, help="定向轴产物（--two-axes 用）")
    parser.add_argument("--show-bounds", action="store_true", help="打印阈值与出处")
    parser.add_argument("--verify-bounds", action="store_true", help="从冻结快照重算阈值并比对")
    parser.add_argument("--diff-against", default=None, help="与另一份卡片 JSON 逐量对比")
    args = parser.parse_args(argv)

    if args.self_check:
        return self_check()

    if args.show_bounds or args.verify_bounds:
        ok, rows = verify_bounds()
        for row in rows:
            print(
                f"  {row['bound']:24s} declared={row['declared']:>8} "
                f"recomputed={row['recomputed_from_snapshot']:>8} matches={row['matches']}"
            )
            print(f"      source: {row['source']}")
        if args.verify_bounds and not ok:
            print("✗ 声明的阈值与从冻结快照重算的不一致 —— 阈值与其证据已分叉")
            return 1
        return 0

    if args.out and not args.json:
        args.json = True  # --out 必然落结构化内容

    if args.two_axes:
        try:
            report = build_two_axes(directed_artifact=args.directed_artifact)
        except (FileNotFoundError, KeyError, ValueError) as exc:
            print(f"✗ 无法构造两轴产物：{exc}")
            return 2
        _emit(report, args, render_both_axes(report["axes"]))
        return 0

    try:
        report = build_card(args.events, args.truth)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        print(f"✗ 无法出卡：{exc}")
        return 2

    _emit(report, args, render_report(report))

    if args.diff_against:
        previous = json.loads(Path(args.diff_against).read_text(encoding="utf-8"))
        print(f"\n--- diff vs {args.diff_against} ---")
        print(diff_reports(previous, report))

    # 退出码：卡片判定直接映射 —— FAIL 是 1，「无法测量」是 2。
    # ★ 「没测」不得返回 0（本仓 #162 已把这条纪律钉进运行器）。
    verdicts = {card["verdict"] for card in report["cards"].values()}
    if VERDICT_FAIL in verdicts:
        return 1
    if VERDICT_UNMEASURABLE in verdicts:
        return 2
    return 0


def _emit(report: dict, args: argparse.Namespace, human: str) -> None:
    """按 ``--json`` / ``--out`` 输出（``--out`` 一律写结构化内容）."""
    if args.json:
        text = json.dumps(report, ensure_ascii=False, indent=2)
        if args.out:
            out_path = Path(args.out)
            # ★ newline="\n" 是承重的（AGENTS.md 字节核验）：Windows 上文本模式
            #   会把 "\n" 全写成 "\r\n"，产物因此与行尾纪律不符，且破坏 diff。
            out_path.write_text(text + "\n", encoding="utf-8", newline="\n")
            print(f"[card] 已写出 {out_path}")
        else:
            print(text)
        return
    print(human)


if __name__ == "__main__":  # pragma: no cover - CLI glue
    raise SystemExit(main())
