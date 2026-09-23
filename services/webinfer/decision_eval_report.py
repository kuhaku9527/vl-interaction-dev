# ruff: noqa: RUF001, RUF002, RUF003
"""定向轴卡片的**渲染与 diff**（工单 #157）.

为什么单独一个模块
------------------
「算出一张卡」与「把这张卡印成人读文本 / 与另一张逐指标比较」是两件事：
前者的正确性靠判据测试，后者的正确性靠**可读性与 diff 输出**的测试。
按 ``coding-standards.md`` §7（模块保持单一职责）分开，两者各自可测。

★ 渲染里有两处刻意的**如实标注**，不要「优化」掉：

* 未测的读数打成 ``--`` 而不是 ``0``（``0`` 是「测到 0」，两者含义相反）；
* 计数显示「和 + 逐轮 + 有几轮非零」而不是光一个和 —— 卡片的比率是**跨轮取中位**，
  只报和会让读者拿一个混合口径的读数去解释中位数。

Run tests: cd services/webinfer && python -m pytest tests/test_decision_eval_card.py -q
"""

from __future__ import annotations

from decision_eval_axis import (
    AXIS_METRIC_KEYS,
    EVIDENCE_NOT_QUIET,
    counter_value,
    median_view,
)
from decision_eval_set import SUBSET_GENERALIZATION, SUBSETS

# --- 渲染与 diff ------------------------------------------------------------


def _fmt(value: object, width: int = 7) -> str:
    """数值右对齐；``None`` 打成 ``--``（未测，不是 0）."""
    if value is None:
        return "--".rjust(width)
    if isinstance(value, float):
        return f"{value:.1f}".rjust(width)
    return str(value).rjust(width)


def _per_round_suffix(counter: dict) -> str:
    """多轮时附上逐轮分布；单轮时留空（避免把 ``[56]`` 这种噪声打进正文）."""
    if counter["n_rounds"] <= 1:
        return ""
    return f" 逐轮 {counter['per_round']}"


def _render_scope(name: str, scope: dict, lines: list[str]) -> None:
    """渲染一个作用域（overall 或某个子集）."""
    if not scope.get("n_cases"):
        lines.append(f"  {name:16s} (无样本)")
        return
    # ★ 句数一律经 counter_value 读：多轮块里它是结构化三件套，
    #   直接 f-string 插值会把整个 dict 打进文本（可读性归零，且读起来像乱码）。
    directed = (
        counter_value(scope, "n_directed_cases")
        if scope.get("n_directed_cases")
        else counter_value(scope, "n_directed")
    )
    nondirected = (
        counter_value(scope, "n_nondirected_cases")
        if scope.get("n_nondirected_cases")
        else counter_value(scope, "n_nondirected")
    )
    lines.append(
        f"  {name:16s} n={scope['n_cases']:3d} "
        f"(面向 {directed['sum']}{_per_round_suffix(directed)}"
        f" / 非面向 {nondirected['sum']}{_per_round_suffix(nondirected)})"
    )
    rows = (
        ("误响应率% (非面向→开口)", "nondirected_spurious_response_rate_pct", "upper"),
        ("  窄口径: 仅 response%", "nondirected_response_only_rate_pct", "upper"),
        ("面向句非响应率%", "directed_nonresponse_rate_pct", "upper"),
        ("  窄口径: 仅 nfm%", "directed_nonresponse_as_not_for_me_pct", "upper"),
        ("nfm 精确率%", "not_for_me_precision_pct", "lower"),
        ("nfm 召回率%", "not_for_me_recall_pct", "lower"),
        ("nfm 预测率%", "not_for_me_prediction_rate_pct", "lower"),
        ("宽口径沉默召回% (仅语境)", "quiet_recall_pct", "lower"),
        ("代价加权 cost_index", "cost_index", "upper"),
    )
    for label, key, _direction in rows:
        series = scope.get(key) or {}
        if "median" in series:
            lines.append(
                f"    {label:28s} median={_fmt(series['median'])} "
                f"worst_hi={_fmt(series['max'], 6)} worst_lo={_fmt(series['min'], 6)} "
                f"stdev={_fmt(series['stdev'], 5)} rounds={series['per_round']}"
            )
        else:  # 单轮块（合成输入 / 直接喂行）
            lines.append(f"    {label:28s} value={_fmt(scope.get(key))}")
    # ★ 计数一律走 counter_value（单轮裸 int / 多轮结构化）。
    #   显示「和 + 逐轮 + 有几轮非零」而不是光一个和：本卡片的比率是**跨轮取中位**，
    #   只报和会让读者拿一个混合口径的读数去解释中位数（真机产物就有
    #   not_for_me_predicted=[1,0,0] 这种形状，只报「1」会看着像三轮都测到了）。
    fp = counter_value(scope, "false_positives")
    fn = counter_value(scope, "false_negatives")
    nfm_pred = counter_value(scope, "not_for_me_predicted")
    nfm_true = counter_value(scope, "not_for_me_true")
    errors = counter_value(scope, "n_errors")
    lines.append(
        f"    {'计数(和/逐轮) FP':28s} {_fmt(fp['sum'], 3)} / {fp['per_round']}"
        f"   [{fp['n_rounds_nonzero']}/{fp['n_rounds']} 轮非零]"
    )
    lines.append(
        f"    {'FN':28s} {_fmt(fn['sum'], 3)} / {fn['per_round']}"
        f"   [{fn['n_rounds_nonzero']}/{fn['n_rounds']} 轮非零]"
    )
    lines.append(
        f"    {'nfm 预测 (精确率分母)':28s} {_fmt(nfm_pred['sum'], 3)} / {nfm_pred['per_round']}"
        f"   [{nfm_pred['n_rounds_nonzero']}/{nfm_pred['n_rounds']} 轮非零]"
        "  ← 守卫要求**每轮**非零"
    )
    lines.append(
        f"    {'nfm 真值 (召回率分子)':28s} {_fmt(nfm_true['sum'], 3)} / {nfm_true['per_round']}"
    )
    lines.append(
        f"    {'break_even C_FP/C_FN':28s} "
        f"{_fmt(median_view(scope.get('break_even_fp_fn_ratio')), 5)}"
        "  ← 两类错误等代价点"
    )
    if errors["sum"]:
        lines.append(
            f"    ⚠️ 推理失败行 {errors['sum']}（逐轮 {errors['per_round']}）"
            " —— 「没测到」不是「判定沉默」"
        )


def render_card(card: dict) -> str:
    """把一张卡渲染成人读文本（分数 + 可证伪性 + 证据 + 结构判定）."""
    out: list[str] = []
    src = card["source"]
    out.append(
        f"=== 定向轴记分卡 (#157) — variant {src['variant']}"
        f"{' [带 persona]' if src['include_profile'] else ' [裸 prompt]'} ==="
    )
    out.append(
        f"  产物: {src['artifact']}   model={src['model']}   "
        f"聚合 {card['measurement']['rounds_aggregated']} 轮 × "
        f"{card['measurement']['rows_per_round'][0] if card['measurement']['rows_per_round'] else 0} 例"
    )
    out.append(
        "  代价加权: " + card["cost"]["ratio"] + "（误响应 : 漏判）—— " + card["cost"]["meaning"]
    )
    out.append("")
    _render_scope("overall", card["overall"], out)
    out.append("")
    out.append("  --- 按子集分列（不混算）---")
    for subset in SUBSETS:
        label = "泛化(真本事)" if subset == SUBSET_GENERALIZATION else "开卷(记忆可见)"
        out.append(f"  {label}:")
        _render_scope(f"    {subset}", card["by_subset"][subset], out)
    out.append("")
    out.append("  --- token 级证据（区分「判定沉默」与「空输出」）---")
    evidence = card["evidence"]
    out.append(
        "    overall: "
        + "  ".join(f"{k}={evidence['overall'][k]}" for k in evidence["states"])
        + f"  (非不开口行 {evidence['overall'][EVIDENCE_NOT_QUIET]})"
    )
    cov = evidence["coverage"]
    out.append(
        f"    覆盖率: 不开口 {cov['quiet_rows']} 行，有 token 证据 "
        f"{cov['rows_with_token_evidence']}，缺证据 {cov['rows_without_token_evidence']}"
    )
    out.append("")
    out.append("  --- 判据判定（指数判据 + 结构性判据，逐条含出处）---")
    for item in (*card["criteria"]["index"], *card["criteria"]["structural"]):
        mark = {"pass": "PASS", "fail": "FAIL", "unmeasurable": "无法测量"}[item["verdict"]]
        out.append(f"    [{mark:6s}] {item['criterion_id']}: {item['reason']}")
    out.append(f"  总判定: {card['criteria']['verdict'].upper()}")
    out.append("")
    out.append("  --- 负控（每条判据都有一份故意做错的输入证明它会判红）---")
    for item in card["negative_controls"]:
        out.append(
            f"    {item['status']:9s} {item['mutation_id']:24s} 必须判红={item['must_fail']}"
        )
    return "\n".join(out)


def render_scorecard(report: dict) -> str:
    """渲染整份报告（全部 variant）."""
    out = [
        f"定向轴记分卡 · 工单 {report['ticket']} · 父 spec {report['spec']}",
        f"产物: {report['artifact']}",
        "",
    ]
    for card in report["cards"].values():
        out.append(render_card(card))
        out.append("")
    return "\n".join(out)


def diff_cards(before: dict, after: dict) -> str:
    """两次卡片的结构化差异（**逐指标**列出中位与离散度的变化）.

    本工单 AC 要求「两次运行之间的指标变化能一眼看出」。JSON 本身可 diff，
    但那需要读者自己在几百行里找 —— 这里给的是那张结论表。
    """
    lines: list[str] = []
    for key in ("overall", *(f"by_subset/{s}" for s in SUBSETS)):
        scope_a = _scope_of(before, key)
        scope_b = _scope_of(after, key)
        for metric in AXIS_METRIC_KEYS:
            series_a = scope_a.get(metric)
            series_b = scope_b.get(metric)
            if series_a is None or series_b is None:
                continue
            med_a = median_view(series_a)
            med_b = median_view(series_b)
            sd_a = series_a.get("stdev") if isinstance(series_a, dict) else None
            sd_b = series_b.get("stdev") if isinstance(series_b, dict) else None
            if med_a != med_b or sd_a != sd_b:
                lines.append(
                    f"{key}.{metric}  median {med_a} -> {med_b}  |  stdev {sd_a} -> {sd_b}"
                )

    verdicts_a = _verdict_map(before)
    verdicts_b = _verdict_map(after)
    for criterion_id in sorted(set(verdicts_a) | set(verdicts_b)):
        if verdicts_a.get(criterion_id) != verdicts_b.get(criterion_id):
            lines.append(
                f"判据 {criterion_id}  {verdicts_a.get(criterion_id)} -> {verdicts_b.get(criterion_id)}"
            )

    if not lines:
        return "两次运行在定向轴指标与判据判定上无差异。"
    return "\n".join(lines)


def _scope_of(card: dict, key: str) -> dict:
    """``"overall"`` / ``"by_subset/generalization"`` → 子块."""
    node: object = card
    for part in key.split("/"):
        if not isinstance(node, dict) or part not in node:
            return {}
        node = node[part]
    return node if isinstance(node, dict) else {}


def _verdict_map(card: dict) -> dict[str, str]:
    """卡片里全部判据的 id → 判定."""
    return {
        item["criterion_id"]: item["verdict"]
        for item in (*card["criteria"]["index"], *card["criteria"]["structural"])
    }


def diff_reports(before: dict, after: dict) -> str:
    """全 variant 的差异."""
    blocks: list[str] = []
    for name, card in after["cards"].items():
        old = (before.get("cards") or {}).get(name)
        if not old:
            blocks.append(f"[{name}] 对照文件里没有这个 variant —— 无法 diff")
            continue
        blocks.append(f"[{name}]\n{diff_cards(old, card)}")
    return "\n\n".join(blocks)
