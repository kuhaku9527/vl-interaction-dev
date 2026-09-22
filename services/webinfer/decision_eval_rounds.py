# ruff: noqa: RUF001, RUF002, RUF003
# (RUF001/002/003 = ambiguous fullwidth punctuation. This module's prose is
# Chinese and quotes real test sentences; the same suppression is used by
# decision_eval_set.py / decision_eval_score.py. Established repo convention.)
"""决策评测的**多轮取中位 + 离散度**（工单 #165；父 spec #161 §D5）.

为什么要有这个模块
------------------
**决策评测的结论此前建立在单轮样本上，而已知单轮不够。**
实测：生产解码参数（`temperature=0.8`）下一轮跑 26 例非面向句，
有 **12–14 例两轮结果不一致**；而存量历史结果也只有 2 轮
（`benchmark_production_live_prompt_results.json` + `_repeat`）。

单轮样本的问题不是「不够准」，而是**它无法自证准不准**：
一个 30.8% 与一个 34.6% 之间的差别，在单轮下与「同一配置的两次抖动」
**不可区分**。所以本模块把三件事变成结果结构的**一等字段**：

1. **中位数与单轮值并列** —— 只报中位而不报单轮，读者无法看出抖动；
2. **离散度**（stdev / range / MAD / relative）—— 只报中位而不报离散度，
   会把「稳定」与「碰巧」混为一谈（本票正文原话）；
3. **轮数与分母** —— 分母跨轮不一致时比较**无效**（历史 25 vs 26 已静默发生过）。

★ 设计决定
-----------
* **聚合在 `services/webinfer/`，不在 `services/scripts/`** —— 后者**不在
  CI 的 pytest 矩阵内**，放那里等于让它永不被测（#152 的缺陷形态）。
* **分母跨轮必须逐句一致，否则报错**（不是打警告）：比较失效是**静默**的，
  而静默正是这个项目最贵的教训（台账 §6.4）。
* **不引入第三方统计库**：`statistics` 是标准库，且这几个量手算即可核对。* **本模块只做聚合，不跑模型**：目前**唯一**接线的是
  `benchmark_production_live_prompt.py`（`BENCH_ROUNDS`）；
  它把逐轮 stats 喂进这里。这样「怎么算」可以在**离线**下被完整测试，
  而「怎么跑」才需要真机。

  ⚠️ `benchmark_4state_notforme.py` **尚未接线**（它仍只跑单轮）——
  本模块可用，但那条入口的多轮化是遗留工作，不在 #165 范围内。
  写在这里是因为「两个入口都接了」曾是一句不实的自述（评审查出）。

Run tests: cd services/webinfer && python -m pytest tests/test_decision_eval_rounds.py -q
Self-check: python -m decision_eval_rounds --self-check
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

from decision_eval_score import COUNT_KEY, summarize
from decision_eval_set import (
    CASES,
    GROUP_DELEGATE,
    GROUP_DIRECTED,
    GROUP_NONDIRECTED,
    GROUPS,
    HISTORICAL_COUNTS,
    HISTORICAL_DENOMINATOR_NOTE,
    SUBSET_OPEN_BOOK,
    group_counts,
    load_cases,
    overlapping_ids,
    production_live_prompt,
)

#: 逐轮聚合的指标名。**刻意覆盖全部五条产出率与 errors** ——
#: 少聚合一个，那条数字就退回「单轮」语义而读者看不出来。
#:
#: ★ 后两项是**分母计数器**，不是可有可无的装饰。本仓硬约束写明
#: 「eval 类必须写分母」，而精确率恰恰有一个**退化轮**：
#: ``summarize`` 在「一条 not-for-me 都没预测」时把精确率记作 `0.0`（分母为 0），
#: 于是该轮的 0.0% 与「预测了但全错」的 0.0% **在数值上无法区分** ——
#: 而二者的含义完全相反。真机实测就撞上了这个：
#: ``P_live4_prod_prompt`` 第 2 轮 ``nfm_pred=0`` ⇒ 精确率 0.0，
#: 使跨轮 stdev 虚高到 47.14（那是**分母退化**，不是模型抖动）。
#: 把计数器一并聚合，读者才能分辨。
AGGREGATED_METRICS: tuple[str, ...] = (
    "baseline_mis_response_rate_pct",
    "not_for_me_precision_pct",
    "not_for_me_recall_pct",
    "directed_miss_rate_pct",
    "delegate_recall_pct",
    "errors",
    # 分母计数器 —— 见下方 RATIO_DENOMINATORS：没有它们就无法判断某个
    # 0.0 是「真的一分没拿到」还是「压根没测」。
    "n_directed",
    "n_nondirected",
    "n_delegate",
    "n_not_for_me_predicted",
    "n_not_for_me_true",
)

#: 每条比率的**分母计数器**（键 = 比率；值 = :func:`summarize` 里它的分母）.
#:
#: ★ 为什么必须逐条登记：``summarize`` 的分母为 0 时把比率记作 ``0.0``。
#: 于是「**没测**」与「**测了但全错**」在数值上**完全相同**，含义却相反。
#: 真机与存量历史文件里**各自**撞到过一次，两次都足以误导结论：
#:
#: * ``P_live4_prod_prompt`` 某轮 ``n_not_for_me_predicted=0``（一条 not-for-me
#:   都没预测）⇒ 精确率被记 0.0，使跨轮 stdev 虚高到 **47.14** —— 那看起来
#:   像「模型剧烈抖动」，实际是**分母退化**；
#: * 存量历史文件里**根本没有** ``delegate`` 组（#155 才新增）⇒
#:   ``delegate_recall_pct`` 被记 **0.0**，读起来像「委派全失败」，
#:   实际是**该态当时压根没被测**。
RATIO_DENOMINATORS: dict[str, str] = {
    "baseline_mis_response_rate_pct": "n_nondirected",
    "not_for_me_precision_pct": "n_not_for_me_predicted",
    "not_for_me_recall_pct": "n_nondirected",
    "directed_miss_rate_pct": "n_directed",
    "delegate_recall_pct": "n_delegate",
}

#: 默认轮数。★ > 2（本票 AC 的字面要求）：2 轮只能判「一致 / 不一致」，
#: 没有中位可言 —— 3 轮才第一次有真正的中间值。
DEFAULT_ROUNDS = 3

#: 历史文件里**没有**的 case（新补入，会改变分母）。
#: 这是把 :data:`HISTORICAL_COUNTS` 真正对齐所需的排除集；
#: 若资产继续增补 case，:func:`historical_comparable_view` 会**报错**要求更新它，
#: 而不是默默算出一个错的分母。
HISTORICAL_EXTRA_IDS: tuple[str, ...] = ("N01b",)


# --- 描述统计（只用标准库）--------------------------------------------------


def _median(values: list[float]) -> float:
    """中位数（偶数个取中间两个的均值）."""
    return float(statistics.median(values))


def _mad(values: list[float]) -> float:
    """中位绝对偏差 MAD = ``median(|x - median(x)|)``.

    选它而非标准差作**并列**指标：MAD 对离群轮不敏感，
    而「一轮因服务抖动全崩」正是最常见的离群形态 —— 两个指标一起看才读得准。
    """
    center = _median(values)
    return float(statistics.median([abs(v - center) for v in values]))


def _dispersion_stdev(values: list[float]) -> float:
    """总体标准差（本模块唯一的离散度入口，便于被负控替换）.

    ★ 刻意抽成独立函数：自检（:func:`self_check`）会**替换它**来证明
    自检本身可证伪 —— 一个恒 0 的离散度实现必须让自检判红。
    """
    return float(statistics.pstdev(values))


def _dispersion(values: list[float]) -> dict:
    """离散度读数：n / min / max / range / stdev / mad / relative.

    ★ 全部量都**四舍五入到 3 位**。不这样做会写出
    ``range: 11.600000000000001`` 这种浮点残渣 —— 而本票 AC#3 要求结果
    「可 diff」，浮点残渣会让两次运行的文件**逐字节**不同却毫无语义差异。
    """
    stdev = _dispersion_stdev(values)
    center = _median(values)
    return {
        "n": len(values),
        "min": round(min(values), 3),
        "max": round(max(values), 3),
        "range": round(max(values) - min(values), 3),
        "stdev": round(stdev, 3),
        "mad": round(_mad(values), 3),
        # 中位数为 0（或接近）时相对离散度无意义 —— 给 None，**不给 inf**
        # （inf 不是合法 JSON，会让整份结果文件读不出来）。
        "relative_stdev_pct": None if center == 0 else round(100.0 * stdev / abs(center), 1),
    }


def aggregate_series(values: list[float]) -> dict:
    """一条指标的跨轮序列 → 中位数 + **单轮值并列** + 离散度.

    Args:
        values: 逐轮读数（顺序即轮次顺序）。

    Returns
    -------
        ``{n, per_round, median, single_round_first, dispersion}``。
        ``per_round`` 与 ``median`` **同时**在场 —— 这是本票 AC 的字面要求。
    """
    vals = [float(v) for v in values]
    if not vals:
        raise ValueError("至少需要一轮读数才能聚合（空序列无法给出中位数）")
    return {
        "n": len(vals),
        "per_round": vals,
        "median": _median(vals),
        "single_round_first": vals[0],
        "dispersion": _dispersion(vals),
    }


# --- 分母（跨轮统一）--------------------------------------------------------


def _round_total(stats: dict) -> int:
    """一轮的句数（三组之和）—— 判「分母是否跨轮一致」用.

    ★ 复用 :data:`decision_eval_score._COUNT_KEY`（`group -> summarize() 里
    `n_*` 的后缀`），**不再自己维护一份同义映射** —— 两份映射迟早分叉，
    而分叉的后果是分母悄悄算错（本票从头到尾都在防这件事）。
    """
    return sum(stats[f"n_{COUNT_KEY[group]}"] for group in GROUPS)


def historical_comparable_view(rows: list[dict]) -> list[dict]:
    """把本资产的跑分行裁成**与历史结果同分母**的视图（25 / 25）.

    历史文件里非面向分母是 **25**，本资产是 **26**（``N01b`` 后补入），
    而且历史文件里**根本没有** ``delegate`` 组（#155 新增）。
    ⇒ 拿本资产的结果直接与历史数字比，是**分母不同**的比较，无效。

    本函数给出那条**可执行**的对齐路径：排除 :data:`HISTORICAL_EXTRA_IDS`
    与整个 ``delegate`` 组，结果必须**恰好**命中 :data:`HISTORICAL_COUNTS`；
    对不上就报错（说明资产又变了，需人工更新排除集），而不是默默给一个错的分母。
    """
    kept = [
        row
        for row in rows
        if row["expected"] != GROUP_DELEGATE and row["id"] not in HISTORICAL_EXTRA_IDS
    ]
    stats = summarize(kept)
    actual = {
        GROUP_DIRECTED: stats["n_directed"],
        GROUP_NONDIRECTED: stats["n_nondirected"],
    }
    if actual != dict(HISTORICAL_COUNTS):
        raise ValueError(
            f"对齐后的分母 {actual} 与历史声明 {dict(HISTORICAL_COUNTS)} 不一致 —— "
            f"资产已变更，请更新 HISTORICAL_EXTRA_IDS={HISTORICAL_EXTRA_IDS} 后重跑"
        )
    return kept


def _denominator_block(round_case_ids: list[list[str]] | None, stats: list[dict]) -> dict:
    """分母块：逐轮句集是否逐句一致 + 权威分母 + 历史差异.

    ★ 句集不一致 ⇒ **报错**。理由：分母不同时跨轮（以及跨变体）比较**无效**，
    而这件事在本项目里已经**静默发生过一次**（25 vs 26）。静默是缺陷本身。
    """
    canonical = group_counts()
    block: dict = {
        "canonical": {group: canonical[group] for group in GROUPS},
        "canonical_total": canonical["total"],
        "historical": dict(HISTORICAL_COUNTS),
        "note": HISTORICAL_DENOMINATOR_NOTE,
        "per_round_totals": [_round_total(s) for s in stats],
    }
    if round_case_ids is None:
        block["per_round_case_id_sets_identical"] = None
        block["case_ids_total"] = None
        return block

    if len(round_case_ids) != len(stats):
        raise ValueError(f"round_case_ids 有 {len(round_case_ids)} 组，但只有 {len(stats)} 轮读数")
    first = list(round_case_ids[0])
    for index, ids in enumerate(round_case_ids, 1):
        if list(ids) != first:
            missing = sorted(set(first) - set(ids))
            extra = sorted(set(ids) - set(first))
            raise ValueError(
                f"第 {index} 轮的 case id 句集/顺序与首轮不一致"
                f"（缺 {missing}，多 {extra}）⇒ **分母被改变，跨轮比较无效**"
            )
        total = _round_total(stats[index - 1])
        if total != len(first):
            raise ValueError(
                f"第 {index} 轮统计出 {total} 句，但该轮句集有 {len(first)} 条 ⇒ 分母不符"
            )

    block["per_round_case_id_sets_identical"] = True
    block["case_ids_total"] = len(first)
    block["historical_comparable"] = {
        "n_directed": HISTORICAL_COUNTS[GROUP_DIRECTED],
        "n_nondirected": HISTORICAL_COUNTS[GROUP_NONDIRECTED],
        "n_delegate": 0,
        "excluded_ids": list(HISTORICAL_EXTRA_IDS),
        "why": (
            "历史文件的分母是非面向 25（无 N01b）、且无 delegate 组；"
            "本资产是 26 + 5。跨口径比较无效，故给出对齐后的分母。"
        ),
    }
    return block


# --- 逐句稳定性（把「12–14 例两轮不一致」变成字段）--------------------------


def case_stability(per_round_rows: list[list[dict]]) -> dict:
    """逐句统计「跨轮决策是否一致」.

    这是本票开篇那句实测（26 例非面向中 12–14 例两轮不一致）的**结构化形态** ——
    它把「模型偶尔抽风」从散落在日志里的印象，变成结果文件里可 diff 的数字。

    ``ok=False`` 的行记为决策 ``"error"`` ⇒ 它与别的轮的决策不同，
    **算作不稳定**（失败轮不是「没说」，是「没测到」）。
    """
    if not per_round_rows:
        raise ValueError("至少需要一轮跑分行才能统计逐句稳定性")

    order: list[str] = []
    seen: set[str] = set()
    for rows in per_round_rows:
        for row in rows:
            cid = row["id"]
            if cid not in seen:
                seen.add(cid)
                order.append(cid)

    per_case: dict[str, dict] = {}
    unstable_by_group: dict[str, int] = dict.fromkeys(GROUPS, 0)
    unstable_ids: list[str] = []

    for cid in order:
        decisions: list[str] = []
        expected = ""
        for rows in per_round_rows:
            match = next((r for r in rows if r["id"] == cid), None)
            if match is None:
                decisions.append("missing")
                continue
            expected = match["expected"]
            decisions.append(match["decision"] if match.get("ok") else "error")
        stable = len(set(decisions)) == 1
        per_case[cid] = {
            "expected": expected,
            "decisions": decisions,
            "stable": stable,
        }
        if not stable:
            unstable_ids.append(cid)
            if expected in unstable_by_group:
                unstable_by_group[expected] += 1

    return {
        "rounds": len(per_round_rows),
        "n_cases": len(order),
        "n_stable": len(order) - len(unstable_ids),
        "n_unstable": len(unstable_ids),
        "n_unstable_by_group": unstable_by_group,
        "unstable_ids": unstable_ids,
        "per_case": per_case,
    }


# --- 开卷标注与运行成本 -----------------------------------------------------


def open_book_report(prompt: str | None = None) -> dict:
    """★ AC#7：本轮是否「开卷」—— 取自**算得**的逐字重叠，不是手写标签.

    生产 prompt 与测试集逐字重叠 10 句（其中 8 句非面向），
    扣掉记忆效应后泛化 recall 仅 0–5.6%。⇒ **任何分数都必须随它一起读**，
    否则「开卷得分」会被当成能力。
    """
    effective = production_live_prompt() if prompt is None else prompt
    overlap = overlapping_ids(effective)
    nondirected = {cid for cid, _t, group, *_r in CASES if group == GROUP_NONDIRECTED}
    cases = load_cases(effective)
    return {
        "is_open_book": bool(overlap),
        "prompt_length": len(effective),
        "n_overlapping": len(overlap),
        "n_overlapping_nondirected": len(overlap & nondirected),
        "overlapping_ids": sorted(overlap),
        "open_book_subset_size": sum(1 for c in cases if c.subset == SUBSET_OPEN_BOOK),
        "why": (
            "开卷 = 测试句逐字出现在被测 prompt 里（记忆效应可见）。"
            "得分必须与分列的开卷/泛化两个子集一起读 —— 混算会把记忆当成能力。"
        ),
    }


def cost_report(wall_seconds: list[float] | None, llm_calls: int) -> dict:
    """★ AC#6：N 轮耗时，供后续调 N.

    没有成本数字就无法决定 N 该取多少：本票要求默认 N>2，
    但「跑得起几轮」是算出来的，不是拍的。

    ★ ``wall_seconds=None`` 表示**没有测过时间**（例如从已落盘的结果文件
    重新聚合 —— 那里没有耗时数据）。此时全部耗时字段给 ``None``，**不给 0.0**。

    这与本模块对**比率**的处理是同一条纪律：``0.0`` 是「测到了 0」，
    ``None`` 才是「没测」。把「没测」写成 ``0.0``，正是本票花了很大篇幅
    去修的那类缺陷（分母退化的 0.0 与「测了但全错」的 0.0 不可区分）。
    """
    if wall_seconds is None:
        return {
            "rounds": None,
            "wall_seconds_total": None,
            "wall_seconds_per_round": None,
            "wall_seconds_per_round_mean": None,
            "llm_calls": llm_calls,
            "seconds_per_llm_call": None,
            "measured": False,
            "why": "本轮没有耗时数据（例如从已落盘结果重新聚合），故耗时字段一律为 None 而非 0.0",
        }
    values = [float(v) for v in wall_seconds]
    total = round(sum(values), 3)
    return {
        "rounds": len(values),
        "wall_seconds_total": total,
        "wall_seconds_per_round": values,
        "wall_seconds_per_round_mean": round(total / len(values), 3) if values else None,
        "llm_calls": llm_calls,
        "seconds_per_llm_call": round(total / llm_calls, 3) if llm_calls else None,
        "measured": True,
    }


# --- 组装整份报告 -----------------------------------------------------------


def aggregate_rounds(
    per_round_stats: list[dict],
    round_case_ids: list[list[str]] | None = None,
) -> dict:
    """N 轮 stats → 逐指标的中位数 + 单轮值 + 离散度 + 分母块.

    Raises
    ------
        ValueError: 轮数 < 2（算不出离散度），或分母跨轮不一致（比较无效）。
            两者都**宁可报错也不给数字** —— 给个数字就会被当成结论。
    """
    if len(per_round_stats) < 2:
        raise ValueError(
            f"需要至少 2 轮才能给出离散度，收到 {len(per_round_stats)} 轮 —— "
            "单轮无法区分「稳定」与「碰巧」，故拒绝给出一个像结论的数字"
        )

    metrics = {
        name: aggregate_series([s[name] for s in per_round_stats]) for name in AGGREGATED_METRICS
    }

    # ★ 分母退化轮：某轮的分母是 0 ⇒ 该轮的比率**没有意义**（而不是 0）。
    #   真机实测撞到过：nfm_pred=0 的轮把精确率记成 0.0，与「预测了但全错」
    #   数值相同、含义相反，并让跨轮的 stdev 虚高（47.14，其实是分母退化）。
    #   ⇒ 显式标出是哪几轮，读者才不会把分母退化当成模型抖动。
    degenerate: dict[str, list[int]] = {}
    for ratio, counter in RATIO_DENOMINATORS.items():
        rounds_with_zero_denominator = [
            index for index, stats in enumerate(per_round_stats, 1) if not stats.get(counter)
        ]
        if rounds_with_zero_denominator:
            degenerate[ratio] = rounds_with_zero_denominator

    if degenerate:
        metrics_note: dict = {
            "degenerate_rounds": degenerate,
            "why": (
                "这些轮的分母为 0（计数器见 RATIO_DENOMINATORS），"
                "故该轮的比率无意义 —— 它被记作 0.0，与「预测了但全错」的 0.0 "
                "数值相同、含义相反。跨轮离散度会因此虚高：那是分母退化，不是模型抖动。"
            ),
        }
    else:
        metrics_note = {"degenerate_rounds": {}}

    return {
        "rounds": len(per_round_stats),
        "metrics": metrics,
        "metrics_note": metrics_note,
        "denominator": _denominator_block(round_case_ids, per_round_stats),
    }


def build_rounds_report(
    per_round_stats: list[dict],
    per_round_rows: list[list[dict]],
    *,
    prompt: str | None = None,
    round_case_ids: list[list[str]] | None = None,
    round_wall_seconds: list[float] | None = None,
    llm_calls: int = 0,
    rounds_requested: int = DEFAULT_ROUNDS,
) -> dict:
    """整份多轮报告：轮数 / 逐指标中位与离散 / 逐句稳定性 / 分母 / 开卷 / 成本.

    Returns
    -------
        可直接 ``json.dumps`` 的嵌套 dict。**键的顺序固定**（构造顺序即顺序），
        故两次运行的差异可以逐行 diff（本票 AC#3）。
    """
    aggregate = aggregate_rounds(per_round_stats, round_case_ids)
    return {
        "rounds_requested": rounds_requested,
        "rounds_completed": len(per_round_stats),
        "rounds": per_round_stats,
        "metrics": aggregate["metrics"],
        "metrics_note": aggregate["metrics_note"],
        "case_stability": case_stability(per_round_rows),
        "denominator": aggregate["denominator"],
        "open_book": open_book_report(prompt),
        # ★ Pass None straight through when there is no timing data: fabricating
        #   [0.0]*n made a re-aggregated report claim "0.0 s/round" — a measured
        #   zero from unmeasured data, the exact confusion this module flags.
        "cost": cost_report(round_wall_seconds, llm_calls),
    }


# --- 两次运行的 diff --------------------------------------------------------


def diff_reports(a: dict, b: dict) -> str:
    """两次多轮报告的差异（**逐指标**列出中位数与离散度的变化）.

    本票 AC#3 要求「两次运行之间的差异一眼可见」。JSON 本身可 diff，
    但那需要读者自己在几百行里找 —— 这里给的是那张结论表。
    """
    lines: list[str] = []
    if a.get("rounds_completed") != b.get("rounds_completed"):
        lines.append(
            f"rounds_completed  {a.get('rounds_completed')} -> {b.get('rounds_completed')}"
        )

    for name in AGGREGATED_METRICS:
        sa = (a.get("metrics") or {}).get(name)
        sb = (b.get("metrics") or {}).get(name)
        if sa is None or sb is None:
            lines.append(f"{name}  只在一侧存在")
            continue
        med_a, med_b = sa["median"], sb["median"]
        sd_a = sa["dispersion"]["stdev"]
        sd_b = sb["dispersion"]["stdev"]
        if med_a != med_b or sd_a != sd_b:
            lines.append(f"{name}  median {med_a} -> {med_b}  |  stdev {sd_a} -> {sd_b}")

    for key in ("is_open_book", "case_ids_total"):
        va = (a.get("open_book") or {}).get(key)
        vb = (b.get("open_book") or {}).get(key)
        if key == "case_ids_total":
            va = (a.get("denominator") or {}).get(key)
            vb = (b.get("denominator") or {}).get(key)
        if va != vb:
            lines.append(f"{key}  {va} -> {vb}")

    if not lines:
        return "两次运行在聚合指标上无差异（median 与 stdev 逐项相同）。"
    return "\n".join(lines)


# --- 负控自检（离线可跑，AC#5 的可执行形态）--------------------------------


def self_check() -> int:
    """★ AC#5 的**离线可执行**形态：证明离散度真的在度量离散.

    做两件事，并**把两侧读数都打出来**：
      1. 三轮完全一致 ⇒ 离散度应为 0；
      2. 注入一个高方差桩（第 3 轮把非面向句全判误响应）⇒ 离散度应**明显上升**。

    返回 ``0`` 通过 / ``1`` 不通过。刻意做成可被替换
    （见 :func:`_dispersion_stdev`）—— 「自检通过」必须能被证伪才有意义。
    """

    def _rows(decision_for_group: dict[str, str]) -> list[dict]:
        # ★ 这里填的是**决策**（response / not-for-me / delegation），
        #   不是 decision_eval_set 的**期望动作**（respond / silent）——
        #   两者名字相近但取值不同，混用会让自检拿一组非法决策跑出假的「稳定」。
        mapping = {
            GROUP_DIRECTED: "response",
            GROUP_NONDIRECTED: "not-for-me",
            GROUP_DELEGATE: "delegation",
        }
        mapping.update(decision_for_group)
        return [
            {"id": cid, "expected": group, "decision": mapping[group], "ok": True}
            for cid, _t, group, *_r in CASES
        ]

    stable_stats = [summarize(_rows({})) for _ in range(3)]
    stub_stats = [
        *[summarize(_rows({})) for _ in range(2)],
        summarize(_rows({GROUP_NONDIRECTED: "response"})),
    ]

    metric = "not_for_me_recall_pct"
    stable = aggregate_rounds(stable_stats)["metrics"][metric]
    stub = aggregate_rounds(stub_stats)["metrics"][metric]

    print("=== decision eval rounds self-check (#165 AC5) ===")
    for label, series in (("stable", stable), ("stub", stub)):
        print(
            f"  {label:6s} {metric}: "
            f"per_round={series['per_round']}  median={series['median']}  "
            f"stdev={series['dispersion']['stdev']}  range={series['dispersion']['range']}"
        )

    problems: list[str] = []
    if stable["dispersion"]["stdev"] != 0.0:
        problems.append("三轮完全一致时离散度不为 0 ⇒ 反向对照失败")
    if not stub["dispersion"]["stdev"] > stable["dispersion"]["stdev"]:
        problems.append("注入高方差桩后离散度未上升 ⇒ 该字段没在度量离散")
    if stub["dispersion"]["range"] <= 0.0:
        problems.append("高方差桩的 range 为 0 ⇒ range 是装饰")
    if len(stable["per_round"]) < 2 or len(stub["per_round"]) < 2:
        problems.append("单轮值未并列 ⇒ 违反 AC#2")

    if problems:
        print("  verdict: FAIL")
        for problem in problems:
            print(f"    - {problem}")
        return 1
    print("  verdict: PASS（离散度确实在度量离散，中位与单轮值并列）")
    return 0


def report_from_results_files(
    paths: list[str],
    variant: str,
    *,
    prompt: str | None = None,
    rounds_requested: int | None = None,
) -> dict:
    """Re-aggregate **already-stored** per-round result files into one report.

    Why this exists: the stored historical artifact is two separate files
    (``benchmark_production_live_prompt_results.json`` and its ``_repeat``),
    each holding ONE round. That is exactly the 2-round data #165 starts from,
    and it can be turned into a median+dispersion report **without re-running
    the model** — which is what makes the historical claim checkable at all.

    Two file shapes are accepted, so both the old two-file artifact and the
    new multi-round one can be re-read:

    * **one round per file** (historical): ``results[variant].rows``.
    * **N rounds in one file** (``#165`` onward): ``results[variant]
      .per_round_rows`` — a list of rounds. Each element is used as its own
      round, and a file may be mixed with single-round files.

    Args:
        paths: the JSON files to read, in round order.
        variant: variant name inside each file.
        prompt: the system prompt those rounds were produced against — it
            decides the open-book split, so passing the wrong one silently
            mislabels which sentences are open-book. ``None`` ⇒
            :func:`production_live_prompt` **with** the persona block
            (``include_profile=True``), which is what the production live route
            sends and what the ``P2_…_profile`` variants use.

            ⚠️ Unlike :func:`decision_eval_score.load_rows_from_results`, this
            does **not** switch on the variant name: file names are not a
            reliable signal for which prompt was under test, and guessing would
            mislabel the split on exactly the ``P_live4_prod_prompt`` (bare)
            variant. Pass ``prompt=`` explicitly when the variant was bare.
        rounds_requested: defaults to the number of rounds actually found.

    Raises
    ------
        KeyError: a file is missing the variant, or has no rows — fail loud
            rather than aggregate an empty round into a fake median.
        ValueError: fewer than two usable rounds (no dispersion to report).
    """
    per_round_rows: list[list[dict]] = []
    for path in paths:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        variants = data.get("results") or {}
        if variant not in variants:
            raise KeyError(f"{path} 里没有 variant {variant!r}（有 {sorted(variants)}）")
        entry = variants[variant]
        # ★ Prefer the multi-round list when present: one file then carries all
        #   N rounds, and reading only `rows` would silently collapse it to a
        #   single round (and then fail the "at least 2 rounds" guard even
        #   though the file visibly holds three).
        stored_rounds = entry.get("per_round_rows")
        if stored_rounds:
            for index, rows in enumerate(stored_rounds, 1):
                usable = [r for r in rows if r.get("id")]
                if not usable:
                    raise KeyError(f"{path} 的第 {index} 轮没有任何跑分行")
                per_round_rows.append(usable)
            continue
        rows = [r for r in entry.get("rows", []) if r.get("id")]
        if not rows:
            raise KeyError(f"{path} 的 variant {variant!r} 没有任何跑分行")
        per_round_rows.append(rows)

    if len(per_round_rows) < 2:
        raise ValueError(
            f"至少需要 2 轮才能给出离散度，从 {len(paths)} 个文件里只读出 "
            f"{len(per_round_rows)} 轮（每个文件要么给 per_round_rows，要么给一轮的 rows）"
        )

    effective_prompt = production_live_prompt() if prompt is None else prompt
    per_round_stats = [summarize(rows) for rows in per_round_rows]
    return build_rounds_report(
        per_round_stats,
        per_round_rows,
        prompt=effective_prompt,
        round_case_ids=[[r["id"] for r in rows] for rows in per_round_rows],
        round_wall_seconds=None,
        llm_calls=sum(len(rows) for rows in per_round_rows),
        rounds_requested=rounds_requested or len(per_round_rows),
    )


def print_report_text(report: dict, *, include_header: bool = True) -> str:
    """Render a report's median / single-round / dispersion view as one string.

    ★ The **single** renderer: ``print_report`` prints it, and the benchmark
    script re-emits it for each variant. Splitting the rendering out is what
    removes the near-duplicate table that used to live in the benchmark (and
    would have drifted from the unit-tested one).

    Args:
        include_header: ``False`` omits the ``=== N 轮… ===`` banner, for
            callers that already printed their own heading (the benchmark
            prints one per variant, so the banner would otherwise appear
            twice).
    """
    out: list[str] = []
    if include_header:
        out.append(
            f"=== {report['rounds_completed']} 轮中位数 + 离散度 "
            f"(requested {report['rounds_requested']}) ==="
        )
    out += [
        "  "
        + "metric".ljust(32)
        + "median".rjust(9)
        + "single".rjust(9)
        + "stdev".rjust(9)
        + "range".rjust(9)
        + "  per_round",
    ]
    for metric, series in report["metrics"].items():
        disp = series["dispersion"]
        out.append(
            "  "
            + metric.ljust(32)
            + f"{series['median']}".rjust(9)
            + f"{series['single_round_first']}".rjust(9)
            + f"{disp['stdev']}".rjust(9)
            + f"{disp['range']}".rjust(9)
            + "  "
            + str(series["per_round"])
        )
    for ratio, round_numbers in (
        (report.get("metrics_note") or {}).get("degenerate_rounds") or {}
    ).items():
        out.append(
            f"  ⚠️ {ratio} 在轮 {round_numbers} 的**分母为 0** ⇒ 该轮读数无意义"
            "（记作 0.0，与「预测了但全错」数值相同、含义相反）；"
            "跨轮离散度会因此虚高，别当成模型抖动。"
        )
    stability = report["case_stability"]
    out.append(
        f"  逐句稳定性: stable={stability['n_stable']}  unstable={stability['n_unstable']}  "
        f"(by group {stability['n_unstable_by_group']})"
    )
    if stability["unstable_ids"]:
        out.append(f"  不稳定句: {', '.join(stability['unstable_ids'])}")
    ob = report["open_book"]
    out.append(
        f"  开卷考: {ob['is_open_book']}  逐字重叠 {ob['n_overlapping']} 句"
        f"（非面向 {ob['n_overlapping_nondirected']}）"
    )
    denom = report["denominator"]
    if denom.get("case_ids_total") is not None:
        out.append(
            f"  分母: 句集跨轮一致={denom['per_round_case_id_sets_identical']}  "
            f"case_ids={denom['case_ids_total']}  权威={denom['canonical']}"
        )
    cost = report["cost"]
    if cost.get("measured") or cost["wall_seconds_total"]:
        out.append(
            f"  成本: total={cost['wall_seconds_total']}s  "
            f"calls={cost['llm_calls']}  s/call={cost['seconds_per_llm_call']}"
        )
    else:
        # ★ 说清「没测」而不是沉默 —— 沉默会被读成「成本可忽略」。
        out.append("  成本: **未测**（本轮没有耗时数据）—— 不是 0，是未知")
    return "\n".join(out)


def print_report(report: dict) -> None:
    """Print a report's median / single-round / dispersion table."""
    print(print_report_text(report))


def main(argv: list[str] | None = None) -> int:
    """CLI：负控自检 / 资产视图 / 从已落盘的轮次文件重新聚合."""
    parser = argparse.ArgumentParser(
        description="多轮取中位 + 离散度（工单 #165）。默认跑离线负控自检。"
    )
    parser.add_argument("--self-check", action="store_true", help="跑负控自检（默认）")
    parser.add_argument("--show-asset", action="store_true", help="打印冻结资产的分母与开卷清单")
    parser.add_argument(
        "--from-results",
        nargs="+",
        metavar="FILE",
        help="每个文件一轮；重新聚合出中位与离散度（不跑模型）",
    )
    parser.add_argument(
        "--variant", default=None, help="--from-results 用的 variant 名（默认取首个）"
    )
    parser.add_argument("--diff-against", default=None, help="与另一份报告 JSON 逐指标对比")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    args = parser.parse_args(argv)

    if args.show_asset:
        payload = {
            "open_book": open_book_report(),
            "denominator": _denominator_block(None, []),
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            ob = payload["open_book"]
            print(
                f"open-book: {ob['is_open_book']}  重叠 {ob['n_overlapping']} 句"
                f"（其中非面向 {ob['n_overlapping_nondirected']}）"
            )
            print("ids: " + ", ".join(ob["overlapping_ids"]))
        return 0

    if args.from_results:
        variant = args.variant
        if variant is None:
            first = json.loads(Path(args.from_results[0]).read_text(encoding="utf-8"))
            variants = first.get("results") or {}
            if not variants:
                print(f"✗ {args.from_results[0]} 里没有任何 variant")
                return 1
            variant = next(iter(variants))
        # ★ Reuse the rule the rest of the repo already uses for this exact
        #   question (decision_eval_score.load_rows_from_results): a variant
        #   whose name says "profile" was run WITH the persona block, everything
        #   else bare. Without this the open-book split would be computed
        #   against the wrong prompt for the bare variant, silently mislabelling
        #   which sentences are open-book — i.e. the exact defect #155 fixed.
        include_profile = "profile" in variant or "prod_prompt_profile" in variant
        report = report_from_results_files(
            args.from_results,
            variant,
            prompt=production_live_prompt(include_profile=include_profile),
        )
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print(
                f"variant={variant}  来源={', '.join(args.from_results)}  "
                f"开卷归属按 {'带 persona' if include_profile else '裸 prompt'} 口径判定"
            )
            print_report(report)
        if args.diff_against:
            other = json.loads(Path(args.diff_against).read_text(encoding="utf-8"))
            print(f"\n--- diff vs {args.diff_against} ---")
            print(diff_reports(other, report))
        return 0

    return self_check()


if __name__ == "__main__":
    raise SystemExit(main())
