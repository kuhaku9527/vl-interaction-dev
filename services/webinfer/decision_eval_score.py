# ruff: noqa: RUF001, RUF002, RUF003
# (RUF001/002/003 = ambiguous fullwidth punctuation; same established repo
# convention as services/background-agent — see decision_eval_set.py.)
"""决策质量记分卡（决策质量量具 ② 的计分核心）.

Spec: #154 决策质量量具；工单 #155/#157 的交付面。

为什么单独一个模块
------------------
计分逻辑原先长在 ``services/scripts/benchmark_4state_notforme.py`` 里，
而 **``services/scripts`` 不在 CI 的 pytest 矩阵内**
（矩阵 = memory-store / background-agent / webinfer / webui / tts）。
后果：**计分器从来没有被任何测试覆盖过** —— 它是否算对，无人守。

本模块把计分核心搬到 CI 可见处，于是：
  * 计分器可被单测（含负控）；
  * 两个 benchmark 共用同一份计分（不再各写一半）；
  * 「把某句期望标签改反 ⇒ 分数随之变」这条验收可被**固定成测试**，
    而不是靠人手演示一次。

★ 三处必须说清的语义决定
-------------------------
1. **``not_for_me`` 精确率的分母 = 全部组的 not-for-me 预测之和**（本模块修）。
   旧实现写死 ``directed + nondirected``。**只有两组时那恰好等于全部行**，
   所以旧公式是对的；**新增第三组 delegate 后，它静默把 delegate 排除在分母外** ——
   一个把 5 条 delegate 全误报成 not-for-me 的模型仍能拿 **100%**（实测）。
   本模块一律对 :data:`~decision_eval_set.GROUPS` 求和。

2. **``is_correct`` 是三值语义**（开卷/定向轴按组判定）：
   * ``directed``  → 正确 = 开口（``response`` / ``delegation``）
   * ``nondirected`` → 正确 = 不开口（``not-for-me`` / ``silence``）
   * ``delegate`` → 正确 = ``delegation``

   ⚠️ **这与历史结果文件里的 ``correct`` 字段不同语义**，属**已声明的可比性变更**：
   旧规则是两值的（``nondirected`` 只有 ``not-for-me`` 才算对，其余组一律「不是
   not-for-me 就对」）。旧规则把「判定沉默」判成**错**，而沉默与 not-for-me 在
   行为上都等于「不开口」—— 项目实测也确认真实的不开口主要靠 ``silence`` 发生。
   故新规则更准，但它**使既有结果文件里的 ``correct`` 不再能与新字段直接比较**。
   见 :data:`LEGACY_CORRECT_SEMANTICS` 与 :func:`legacy_is_correct`。

3. **delegate 不进定向轴**（见 ``decision_eval_set.DIRECTED_AXIS_GROUPS``），
   但它**进** not-for-me 精确率的分母与 delegate 召回率 —— 不静默跳过任何一组。
"""

from __future__ import annotations

import json
from pathlib import Path

from decision_eval_set import (
    GROUP_DELEGATE,
    GROUP_DIRECTED,
    GROUP_NONDIRECTED,
    GROUPS,
    SUBSETS,
    load_cases,
    production_live_prompt,
    subset_by_id,
)

#: 历史结果文件里的 ``correct`` 用的规则（两值）。保留是为了**可复算历史**，
#: 不是为了继续使用它 —— 新代码一律用 :func:`is_correct`。
LEGACY_CORRECT_SEMANTICS = (
    "历史 results.json 的 correct 是两值的：expected=='nondirected' 时仅 "
    "decision=='not-for-me' 算对；其余组一律 decision!='not-for-me' 算对。"
    "新规则把「沉默」也算作 nondirected 的正确不开口，并把 delegate 独立判定，"
    "故两者不可直接比较。"
)

#: 决策输出里，「不开口」的两种形态。
_SILENT_DECISIONS = ("not-for-me", "silence")
#: 「开口」的两种形态。
_SPEAKING_DECISIONS = ("response", "delegation")

#: 混淆矩阵里除真实决策外的失败列。
_DECISIONS = (*_SPEAKING_DECISIONS, *_SILENT_DECISIONS, "error")


def legacy_is_correct(expected_group: str, decision: str) -> bool:
    """历史 ``correct`` 规则（两值）—— 仅供复算既有结果文件."""
    if expected_group == GROUP_NONDIRECTED:
        return decision == "not-for-me"
    return decision != "not-for-me"


def is_correct(expected_group: str, decision: str) -> bool:
    """该决策是否满足该组的 ground truth（三值语义）.

    Raises
    ------
        KeyError: 未知分组 —— 宁可 fail loud，也不静默把新组算成「对」。
    """
    if expected_group == GROUP_NONDIRECTED:
        return decision in _SILENT_DECISIONS
    if expected_group == GROUP_DELEGATE:
        return decision == "delegation"
    if expected_group == GROUP_DIRECTED:
        return decision in _SPEAKING_DECISIONS
    raise KeyError(f"unknown expected group: {expected_group!r}")


def summarize(rows: list[dict]) -> dict:
    """一组跑分行的混淆矩阵 + 关键指标.

    Args:
        rows: 每行含 ``expected``（组名）/ ``decision`` / ``ok``（可选，缺省视为成功）。

    Returns
    -------
        结构化结果。组取自 :data:`GROUPS`，故**新增组会被计数**，
        不会 KeyError、也不会被静默丢弃。
    """
    matrix: dict[str, dict[str, int]] = {group: dict.fromkeys(_DECISIONS, 0) for group in GROUPS}
    for row in rows:
        expected = row["expected"]
        decision = row["decision"] if row.get("ok") else "error"
        matrix[expected][decision] += 1

    counts = {group: sum(matrix[group].values()) for group in GROUPS}

    def pct(num: int, den: int) -> float:
        return round(100.0 * num / den, 1) if den else 0.0

    # ★ 分母对**全部组**求和（不是写死两组）—— 见模块 docstring 决定 1。
    predicted_nfm = sum(matrix[group]["not-for-me"] for group in GROUPS)
    true_nfm = matrix[GROUP_NONDIRECTED]["not-for-me"]
    missed_nfm = matrix[GROUP_DIRECTED]["not-for-me"]
    delegate_hit = matrix[GROUP_DELEGATE]["delegation"]
    mis_response = matrix[GROUP_NONDIRECTED]["response"]

    return {
        "n_directed": counts[GROUP_DIRECTED],
        "n_nondirected": counts[GROUP_NONDIRECTED],
        "n_delegate": counts[GROUP_DELEGATE],
        "matrix": matrix,
        "baseline_mis_response_rate_pct": pct(mis_response, counts[GROUP_NONDIRECTED]),
        "not_for_me_precision_pct": pct(true_nfm, predicted_nfm),
        "not_for_me_recall_pct": pct(true_nfm, counts[GROUP_NONDIRECTED]),
        "directed_miss_rate_pct": pct(missed_nfm, counts[GROUP_DIRECTED]),
        "delegate_recall_pct": pct(delegate_hit, counts[GROUP_DELEGATE]),
        "n_not_for_me_predicted": predicted_nfm,
        "n_not_for_me_true": true_nfm,
        "n_directed_missed_as_notforme": missed_nfm,
        "n_delegate_hit": delegate_hit,
        "errors": sum(1 for r in rows if not r.get("ok")),
    }


def subset_breakdown(rows: list[dict], prompt: str) -> dict[str, dict]:
    """同一批跑分**按开卷 / 泛化两个子集分列**.

    Args:
        rows: 跑分行（``id`` 用于查子集归属）。
        prompt: 本次实际被测的 system prompt —— 子集归属由它对逐字匹配算得。

    Returns
    -------
        ``{subset: {"n": int, **summarize(...)}}``；无样本的子集给 ``{"n": 0}``。
    """
    mapping = subset_by_id(prompt)
    out: dict[str, dict] = {}
    for subset in SUBSETS:
        picked = [r for r in rows if mapping.get(r["id"]) == subset]
        out[subset] = {"n": len(picked), **summarize(picked)} if picked else {"n": 0}
    return out


def format_scorecard(
    rows: list[dict],
    prompt: str,
    *,
    title: str = "",
) -> str:
    """Overall + per-subset scorecard as text, including **scores, not just counts**.

    This is the offline view: given stored rows (or any synthesized rows) and the
    prompt they were produced against, it prints the totals **and** each
    subset's scores. No llama-server needed, which matters because the two
    benchmark entry points require the live model.

    Args:
        rows: scored rows (``id`` / ``expected`` / ``decision`` / optional ``ok``).
        prompt: the system prompt those rows came from — decides open-book membership.
        title: optional heading.
    """
    overall = summarize(rows)
    breakdown = subset_breakdown(rows, prompt)
    lines: list[str] = []
    if title:
        lines.append(f"=== {title} ===")
    lines.append(
        f"overall  n={len(rows)}  "
        + "  ".join(f"{g}={overall['n_' + _COUNT_KEY[g]]}" for g in GROUPS)
    )
    lines.append(
        "         nfm_precision={:.1f}%  nfm_recall={:.1f}%  "
        "directed_miss={:.1f}%  delegate_recall={:.1f}%  errors={}".format(
            overall["not_for_me_precision_pct"],
            overall["not_for_me_recall_pct"],
            overall["directed_miss_rate_pct"],
            overall["delegate_recall_pct"],
            overall["errors"],
        )
    )
    lines.append("")
    lines.append("--- by subset (开卷 vs 泛化; production prompt is an OPEN-BOOK exam) ---")
    for subset in SUBSETS:
        stats = breakdown[subset]
        if not stats.get("n"):
            lines.append(f"  {subset:15s} (空)")
            continue
        lines.append(
            f"  {subset:15s} n={stats['n']:3d}  "
            f"mis_response={stats['baseline_mis_response_rate_pct']:.1f}%  "
            f"nfm_precision={stats['not_for_me_precision_pct']:.1f}%  "
            f"nfm_recall={stats['not_for_me_recall_pct']:.1f}%  "
            f"directed_miss={stats['directed_miss_rate_pct']:.1f}%  "
            f"delegate_recall={stats['delegate_recall_pct']:.1f}%"
        )
    return "\n".join(lines)


def load_rows_from_results(path: str | Path, variant: str | None = None) -> tuple[list[dict], str]:
    """Load scored rows + the prompt that produced them from a benchmark results file.

    Returns ``(rows, prompt)``. ``prompt`` is rebuilt from the asset for the
    variant's shape (with/without persona) — the results file stores lengths,
    not the prompt text.

    Raises
    ------
        KeyError: unknown variant, or a file with no variants — fail loud rather
            than silently rescoring nothing.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    variants = data.get("results") or {}
    if not variants:
        raise KeyError(f"no variants in results file: {path}")
    name = variant or next(iter(variants))
    if name not in variants:
        raise KeyError(f"variant {name!r} not in {sorted(variants)}")
    rows = [r for r in variants[name].get("rows", []) if r.get("id")]
    # "P2"/"profile" variants include the character profile; "P"/bare do not.
    include_profile = "profile" in name or "prod_prompt_profile" in name
    return rows, production_live_prompt(include_profile=include_profile)


#: group -> the ``n_*`` key summarize() reports it under.
_COUNT_KEY = {
    GROUP_DIRECTED: "directed",
    GROUP_NONDIRECTED: "nondirected",
    GROUP_DELEGATE: "delegate",
}


__all__ = [
    "GROUPS",
    "GROUP_DELEGATE",
    "GROUP_DIRECTED",
    "GROUP_NONDIRECTED",
    "LEGACY_CORRECT_SEMANTICS",
    "SUBSETS",
    "format_scorecard",
    "is_correct",
    "legacy_is_correct",
    "load_cases",
    "load_rows_from_results",
    "subset_breakdown",
    "subset_by_id",
    "summarize",
]


if __name__ == "__main__":
    import argparse

    # Resolve the default results path against the REPO ROOT, not the cwd —
    # this module is run from services/webinfer, so a bare relative path would
    # not resolve. (services/webinfer/<this file> -> repo root is parents[2].)
    _repo_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(
        description="Offline decision scorecard: overall + per-subset scores."
    )
    parser.add_argument(
        "--results",
        default=str(_repo_root / "doc/research/data/benchmark_production_live_prompt_results.json"),
        help="benchmark results JSON (default: the production live prompt results)",
    )
    parser.add_argument("--variant", default=None, help="variant name (default: first)")
    parser.add_argument("--list", action="store_true", help="list variants in the file, then exit")
    args = parser.parse_args()

    if args.list:
        _data = json.loads(Path(args.results).read_text(encoding="utf-8"))
        for _name in _data.get("results") or {}:
            print(_name)
        raise SystemExit(0)

    _rows, _prompt = load_rows_from_results(args.results, args.variant)
    print(format_scorecard(_rows, _prompt, title=f"{args.variant or '(first variant)'}"))
