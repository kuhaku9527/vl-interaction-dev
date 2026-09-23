# ruff: noqa: RUF001, RUF002, RUF003
# (RUF001/002/003 = ambiguous fullwidth punctuation in strings AND comments. This
# module's prose is Chinese. RUF003 was missing here while all six sibling
# `decision_eval_timing*` modules had it — an inconsistency that only showed up
# when a **comment** first carried a fullwidth colon. Keep this line identical
# across the family; a lone module drifting is how `scripts/run_ci_ruff.py`
# caught it.)
"""时序轴的**渲染与 diff**（工单 #158）.

为什么单独一个模块
-----------------
「算出一张卡」与「把这张卡印成人读文本 / 与另一张逐指标比较」是两件事：
前者的正确性靠判据测试，后者的正确性靠**可读性与 diff 输出**的测试。
按 ``coding-standards.md`` §7 分开，两者各自可测（与 ``decision_eval_report``
对定向轴的处理同形）。

★ 渲染里有两处刻意的**如实标注**，不要「优化」掉：

* 未测的读数打成 ``--`` 而不是 ``0``（``0`` 是「测到 0」，两者含义相反）；
* 每个量都带**分母**，且「不适用」与「未标注」分开显示 —— 合成一个计数会让
  真缺口与假缺口混在一起，读者于是学会忽略这个数字（那比不报更坏）。

Run tests: cd services/webinfer && python -m pytest tests/test_decision_eval_timing_card.py -q
"""

from __future__ import annotations

# --- 渲染 --------------------------------------------------------------------


def _fmt(value: object, width: int = 8) -> str:
    """数值右对齐；``None`` 打成 ``--``（未测，不是 0）."""
    if value is None:
        return "--".rjust(width)
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".").rjust(width)
    return str(value).rjust(width)


def _mark(verdict: str) -> str:
    """判定 → 人读标记（与定向轴同一套词汇）."""
    return {"pass": "PASS", "fail": "FAIL", "unmeasurable": "无法测量"}.get(verdict, verdict)


def render_card(card: dict) -> str:
    """把一张时序卡渲染成人读文本（读数 + 出处 + 缺口 + 判定）."""
    out: list[str] = []
    src = card.get("source") or {}
    out.append(f"=== 时序轴记分卡 (#158) — {src.get('label', '冻结夹具')} ===")
    out.append(
        "  这一轴回答：**就算判断对了，是不是说得是时候**。"
        "它**不**与定向轴合成单一 accuracy（父 spec §三）。"
    )
    out.append(f"  事件: {src.get('events')}")
    out.append(f"  真值: {src.get('truth')}")
    reading = card.get("reading") or {}
    out.append(
        f"  轮次: 共 {reading.get('n_rounds')}（用户轮 {reading.get('n_user_rounds')} / "
        f"主动轮 {reading.get('n_proactive_rounds')}），有真值 {reading.get('n_with_truth')}"
    )
    if reading.get("is_frozen_fixture"):
        out.append(
            "  ⚠️ 这是**冻结夹具**（决策为作者写的回放输入），"
            "证明的是「时序轴能算出并判红」，**不是**生产模型的实际时机质量。"
        )
    elif reading.get("is_frozen_fixture") is None:
        # ★ D3 修复的可见面 —— 判不出来就说判不出来，**不冒充**任何一边。
        #   冒充真机会让一个未声明的夹具被当成真机读数；
        #   冒充夹具会给真机读数打上错的告警。
        out.append(f"  ⚠️ 输入性质**未判定**（{reading.get('input_kind_why')}）")
    out.append("")

    metrics = card.get("metrics") or {}
    onset = metrics.get("onset_latency_ms") or {}
    out.append("  --- onset 延迟（从该开口到真的开口）---")
    out.append(
        f"    中位 {_fmt(onset.get('median'), 9)} ms    "
        f"p90 {_fmt(onset.get('p90'), 9)} ms    "
        f"区间 [{_fmt(onset.get('min'), 7)}, {_fmt(onset.get('max'), 7)}] ms"
    )
    out.append(
        f"    样本 {onset.get('n')} 次开口（门槛 {onset.get('min_speaking_rounds')}）　"
        f"缺轮次打点 {onset.get('n_missing_stamp')}"
    )
    if onset.get("missing_stamp_ids"):
        out.append(f"    ★ 缺打点的轮次: {onset['missing_stamp_ids']}")
    out.append("")
    out.append("  --- 每秒误触发次数（乱插话的频率；铁驭最直接能感受到的量）---")
    out.append(
        f"    误触发 {metrics.get('n_spurious')} 次 / 会话跨度 "
        f"{_fmt(metrics.get('session_seconds'), 9)} s = "
        f"{_fmt(metrics.get('spurious_triggers_per_second'), 10)} 次/秒"
    )
    out.append(f"    （换算 {_fmt(metrics.get('spurious_triggers_per_minute'), 8)} 次/分钟）")
    out.append("")
    out.append("  --- premature rate（话还没说完就插）---")
    scope = metrics.get("premature_scope") or {}
    out.append(
        f"    抢话 {metrics.get('n_premature')} 次 / 有标注的开口 {scope.get('n_labeled')} 次 = "
        f"{_fmt(metrics.get('premature_rate_pct'), 8)} %"
    )
    out.append(
        f"    分母构成: 开口 {scope.get('n_speaking')} 次，其中主动轮不适用 "
        f"{scope.get('n_not_applicable_proactive')} 次、未标注 {scope.get('n_unlabeled')} 次"
    )
    if scope.get("unlabeled_ids"):
        out.append(f"    ★ 未标注（数据缺口）: {scope['unlabeled_ids']}")
    out.append("")

    provenance = card.get("latency_source_block") or {}
    out.append("  --- 耗时出处（★ 本票最核心的一条纪律）---")
    out.append(f"    出处 = {provenance.get('source')}　合法 = {provenance.get('legal')}")
    out.append(f"    来源链: {provenance.get('origin')}")
    out.append(f"    禁止使用: {provenance.get('forbidden')}")
    out.append("")

    out.append("  --- 判据判定（逐条含出处）---")
    for item in card.get("criteria") or []:
        out.append(f"    [{_mark(item['verdict']):6s}] {item['criterion_id']}: {item['reason']}")
    out.append(f"  总判定: {str(card.get('verdict', '')).upper()}")
    out.append("")

    out.append("  --- 负控（每条判据都有故意做错的输入证明它不能判绿）---")
    for item in card.get("negative_controls") or []:
        out.append(
            f"    {item['status']:9s} {item['mutation_id']:28s} "
            f"必须判红={item['must_fail']} 不得判绿={item['must_not_pass']}"
        )
    caveats = card.get("caveats") or []
    if caveats:
        out.append("")
        for note in caveats:
            out.append(f"  !! {note}")
    return "\n".join(out)


def render_report(report: dict) -> str:
    """渲染整份报告（逐 variant / 逐夹具）."""
    out = [
        f"时序轴记分卡 · 工单 {report['ticket']} · 父 spec {report['spec']}",
        "★ 与定向轴**分别报告**：本报告里没有任何合并分数（见 criteria 里的 "
        "T_NO_COMBINED_ACCURACY）。",
        "",
    ]
    for card in report["cards"].values():
        out.append(render_card(card))
        out.append("")
    return "\n".join(out)


def render_both_axes(axes: dict) -> str:
    """★ 两轴**并排**报告：各自给各自的判定，**不存在**合并分数.

    ★ 为什么不做成一个「总分」：父 spec §三明确否决。
    本函数存在的意义正是把「分列」这件事变成一个**可读的产物** ——
    一张同时显示两轴、且读者一眼能看出它们各自独立判定的表。
    """
    lines = [
        "=== 决策质量两轴（分别报告，不合成单一 accuracy）===",
    ]
    for axis, block in axes.items():
        card = block.get("card") or {}
        metrics = card.get("metrics") or card.get("overall") or {}
        lines.append(
            f"  [{axis:8s}] 判定 = {str(card.get('verdict', '?')).upper():12s} "
            f"问题 = {block.get('question')}"
        )
        lines.append(
            f"             读数键 = {sorted(metrics)[:6]}{' …' if len(metrics) > 6 else ''}"
        )
    lines.append("")
    lines.append(
        "  ★ 两轴**没有**合并分数。父 spec §三：单一 accuracy 在两类错误上等权，"
        "而本项目两类错误代价明确不对称（「宁可漏，不可乱插」）。"
    )
    return "\n".join(lines)


# --- diff --------------------------------------------------------------------


def diff_cards(before: dict, after: dict) -> str:
    """两次时序卡的结构化差异（**逐量**列出变化）."""
    lines: list[str] = []
    lines.extend(_diff_metrics(before.get("metrics") or {}, after.get("metrics") or {}))
    lines.extend(_diff_verdicts(before, after))
    if not lines:
        return "两次运行在时序读数与判据判定上无差异。"
    return "\n".join(lines)


def _diff_metrics(before: dict, after: dict) -> list[str]:
    """逐量比较（含 onset 的嵌套统计量与 premature 的分母构成）."""
    lines: list[str] = []
    for key in sorted(set(before) | set(after)):
        old, new = before.get(key), after.get(key)
        if isinstance(old, dict) or isinstance(new, dict):
            old_block = old if isinstance(old, dict) else {}
            new_block = new if isinstance(new, dict) else {}
            for sub in sorted(set(old_block) | set(new_block)):
                if sub in ("per_round", "missing_stamp_ids", "unlabeled_ids"):
                    continue
                if old_block.get(sub) != new_block.get(sub):
                    lines.append(f"{key}.{sub}  {old_block.get(sub)} -> {new_block.get(sub)}")
            continue
        if old != new:
            lines.append(f"{key}  {old} -> {new}")
    return lines


def _diff_verdicts(before: dict, after: dict) -> list[str]:
    """判据判定的变化（最需要一眼看到的那一行）."""
    old = {item["criterion_id"]: item["verdict"] for item in (before.get("criteria") or [])}
    new = {item["criterion_id"]: item["verdict"] for item in (after.get("criteria") or [])}
    lines = [
        f"判据 {cid}  {old.get(cid)} -> {new.get(cid)}"
        for cid in sorted(set(old) | set(new))
        if old.get(cid) != new.get(cid)
    ]
    if before.get("verdict") != after.get("verdict"):
        lines.append(f"总判定  {before.get('verdict')} -> {after.get('verdict')}")
    return lines


def diff_reports(before: dict, after: dict) -> str:
    """全卡片的差异."""
    blocks: list[str] = []
    for name, card in after["cards"].items():
        old = (before.get("cards") or {}).get(name)
        if not old:
            blocks.append(f"[{name}] 对照文件里没有这张卡 —— 无法 diff")
            continue
        blocks.append(f"[{name}]\n{diff_cards(old, card)}")
    return "\n\n".join(blocks)


__all__ = [
    "diff_cards",
    "diff_reports",
    "render_both_axes",
    "render_card",
    "render_report",
]
