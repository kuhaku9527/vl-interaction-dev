# ruff: noqa: RUF001, RUF002, RUF003
# (RUF001/002/003 = ambiguous fullwidth punctuation; this module's prose is
# Chinese. Same established repo convention as decision_eval_set.py /
# decision_eval_score.py / decision_eval_rounds.py.)
"""定向轴记分卡：**一条命令**产出结构化、可 diff 的读数（工单 #157）.

    python -m decision_eval_card                 # 卡片（读入库的多轮真机产物）
    python -m decision_eval_card --json          # 结构化，供门禁 / diff
    python -m decision_eval_card --self-check    # 离线负控自检（不需要模型）
    python -m decision_eval_card --show-bounds   # 阈值 + 出处
    python -m decision_eval_card --verify-bounds # 从产物**重算**阈值并比对
    python -m decision_eval_card --diff-against FILE

这张卡回答一个问题：**「它该不该开口，判断得对不对」**。
它**不**回答「说得是不是时候」—— 那是 #158 时序轴，两轴正交、不合成单一 accuracy。

三个必须是结构性的（不是三个打印语句）
--------------------------------------
1. **按代价加权**，``C_FP : C_FN`` 可配、默认让误响应更贵（:mod:`.decision_eval_axis`）。
   单一 accuracy 被本工单明确否决：它把两类错误等权，会抹平项目
   「宁可漏，不可乱插」的偏好。
2. **多轮取中位 + 离散度**（复用 :mod:`.decision_eval_rounds` 已实测过的聚合）：
   单轮数字已被证明不可作点估计（同一配置三次真机跑的误响应率中位 38.5→26.9→46.2）。
3. **判据自带出处与负控**（:mod:`.decision_eval_criteria`）：
   ``--verify-bounds`` 从**已入库产物重算**阈值，与代码里声明的常量比对；
   不一致即报错。于是常量与它的证据**没法**静默分叉。

★ 一张卡同时说清「分数」与「这份分数能不能信」
-----------------------------------------------
``criteria`` 之外还有 ``evidence`` 与 ``measurement`` 两块：分母是否齐备、
哪些行有 token 级证据、哪几轮被聚合、开卷/泛化如何分列。**只读分数不读这两块**，
就会重犯「precision 100% 而召回 0%」那次事故 —— 数字是真的，结论是错的。

Run tests: cd services/webinfer && python -m pytest tests/test_decision_eval_card.py -q
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from pathlib import Path

from decision_eval_axis import (
    AXIS_METRIC_KEYS,
    EVIDENCE_CONTRADICTORY,
    EVIDENCE_EMPTY_OUTPUT,
    EVIDENCE_NOT_QUIET,
    aggregate_axis_blocks,
    axis_block,
    counter_value,
)
from decision_eval_criteria import (
    BASELINE_SNAPSHOT,
    BOUNDS,
    CRITERIA,
    MUTATIONS,
    STRUCTURAL_CRITERIA,
    VERDICT_FAIL,
    VERDICT_PASS,
    VERDICT_UNMEASURABLE,
    structural_checks,
)
from decision_eval_set import (
    GROUP_DIRECTED,
    GROUP_NONDIRECTED,
    SUBSET_GENERALIZATION,
    SUBSETS,
    production_live_prompt,
)

#: 入库的多轮真机产物（阈值与卡片都以它为准；`doc/research/data/` 是**入库**目录）。
DEFAULT_ARTIFACT = "doc/research/data/benchmark_production_live_prompt_rounds.json"

_REPO_ROOT = Path(__file__).resolve().parents[2]


#: variant 名 → 是否含 persona（与 :func:`decision_eval_score.load_rows_from_results`
#: 同一条规则：名字里带 profile 的就是带 persona 跑的那一份）。
def _include_profile(variant: str) -> bool:
    """该 variant 是否使用带 persona 的组装 prompt."""
    return "profile" in variant or "prod_prompt_profile" in variant


# --- 离线合成输入（负控自检用；不需要模型、不需要产物）----------------------

#: 合成输入里的「该开口」句。
SYNTHETIC_DIRECTED: tuple[tuple[str, str], ...] = (
    ("D01", "response"),
    ("D02", "response"),
    ("D03", "delegation"),
    ("D04", "response"),
    ("D05", "response"),
    ("D06", "silence"),
)

#: 合成输入里的「不该开口」句。
SYNTHETIC_NONDIRECTED: tuple[tuple[str, str], ...] = (
    ("N01", "silence"),
    ("N02", "not-for-me"),
    ("N03", "silence"),
    ("N04", "response"),
    ("N05", "silence"),
)

#: 决策 → token 级证据（首位 token）。``silence`` 的首位是 special token 151669；
#: ``not-for-me`` 的首位是 151670 但后面跟普通 token —— 这正是「不能用首位 token
#: 判定说话与否」的实测依据。
_TOKEN_FOR_DECISION: dict[str, list[int]] = {
    "silence": [151669, 151645],
    "response": [151670, 200],
    "delegation": [151670, 300],
    "not-for-me": [151670, 222, 99507],
}


def synthetic_findings() -> dict:
    """一份**离线**跑分行，含已知的两类错误（供自检与负控使用）.

    刻意做得「有好有坏」：3 例非面向被正确判成不开口、1 例被误响应、
    5 例面向里 1 例被吞。于是四项比例都不在退化点上，判据有东西可判。
    """
    rows: list[dict] = []
    for case_id, decision in SYNTHETIC_DIRECTED:
        rows.append(
            {
                "id": case_id,
                "expected": GROUP_DIRECTED,
                "decision": decision,
                "ok": True,
                "emitted_token_ids": list(_TOKEN_FOR_DECISION[decision]),
            }
        )
    for case_id, decision in SYNTHETIC_NONDIRECTED:
        rows.append(
            {
                "id": case_id,
                "expected": GROUP_NONDIRECTED,
                "decision": decision,
                "ok": True,
                "emitted_token_ids": list(_TOKEN_FOR_DECISION[decision]),
            }
        )
    return {"rows": rows}


def synthetic_prompt() -> str:
    """合成输入对应的 prompt —— **零逐字重叠**，故全部落在泛化子集.

    刻意不重叠：自检要走的正是「扣掉记忆效应之后还剩多少」那条路径。
    """
    return "（离线合成输入：与任何 case 文本都不逐字重叠）"


# --- 从产物读回逐轮跑分行 ---------------------------------------------------


def _round_rows_with_evidence(entry: dict, *, source: str) -> list[list[dict]]:
    """把产物里的 ``per_round_rows`` 还原成「每轮一组完整跑分行」.

    ``per_round_rows`` 是 #165 为「产物自足」而落的**投影**（每行只留
    ``id`` / ``expected`` / ``decision`` / ``ok``）。#157 追加了 token 级证据字段
    （``first_token_id`` / ``n_tokens``）—— 没有它，「判定沉默」与「空输出」
    在多轮上就分不开（:mod:`decision_eval_axis` 的 ``quiet_evidence`` 会判
    ``no_token_evidence`` 而不是假装知道）。

    Raises
    ------
        KeyError: 产物里既没有 ``per_round_rows`` 也没有 ``rows`` —— 宁可不给卡片，
            也不拿空轮算出一个像结论的数字。
    """
    stored = entry.get("per_round_rows")
    if stored:
        rounds = []
        for index, rows in enumerate(stored, 1):
            usable = [dict(r) for r in rows if r.get("id")]
            if not usable:
                raise KeyError(f"{source} 的第 {index} 轮没有任何跑分行")
            rounds.append(usable)
        return rounds
    rows = [dict(r) for r in entry.get("rows", []) if r.get("id")]
    if not rows:
        raise KeyError(f"{source} 里既没有 per_round_rows 也没有 rows")
    return [rows]


def load_artifact(path: str | Path, variants: list[str] | None = None) -> dict:
    """读入库的多轮产物，返回 ``{variant: {"rounds": [[row…]…], "meta": {...}}}``.

    Raises
    ------
        FileNotFoundError: 产物不存在（**不**静默给空卡片：缺结果就是没测）。
        KeyError: 指定的 variant 不存在。
    """
    artifact_path = Path(path)
    if not artifact_path.is_absolute():
        artifact_path = _REPO_ROOT / artifact_path
    if not artifact_path.exists():
        raise FileNotFoundError(
            f"定向轴记分卡的输入产物不存在：{artifact_path} —— "
            "缺结果等于「没测」，不判绿（这正是本工单要消灭的那类假绿）"
        )
    data = json.loads(artifact_path.read_text(encoding="utf-8"))
    results = data.get("results") or {}
    if not results:
        raise KeyError(f"{artifact_path} 里没有任何 variant")
    wanted = list(variants) if variants else list(results)
    missing = [v for v in wanted if v not in results]
    if missing:
        raise KeyError(f"{artifact_path} 里没有 variant {missing}（有 {sorted(results)}）")

    return {
        "path": str(artifact_path),
        "model": data.get("model"),
        "test_set_size": data.get("test_set_size"),
        "decoding": data.get("decoding"),
        "variants": {
            name: {
                "rounds": _round_rows_with_evidence(
                    results[name], source=f"{artifact_path}#{name}"
                ),
                "prompt": production_live_prompt(include_profile=_include_profile(name)),
            }
            for name in wanted
        },
    }


# --- 多轮聚合（实现住在 decision_eval_axis；此处只做转发，保持单一实现）-----
#
# ★ 聚合最早写在本模块里，但判据的自检需要构造一个**多轮块**才能测出
#   「永远沉默的桩在代价值上不可接受」—— 那会让 criteria 反向依赖 card。
#   移到 axis 后依赖是单向的，且**只有一份**聚合实现（只有一份被测试的读取路径）。


# --- 结构性判据（在聚合块上补两条与「多轮」有关的）--------------------------

_MULTI_ROUND_CRITERIA: dict[str, str] = {
    "S4-no-unattributed-quiet": (
        "没有任何一行「不开口」决策是**空的或自相矛盾的** token 证据"
        " —— 空输出是失效输出、自相矛盾是解析器与模型不一致，两者都"
        "**不是**「判定沉默」。把它们计成「沉默判对了」，"
        "正是本工单要求 token 级证据的那个混淆。"
    ),
    "S5-multi-round": (
        "至少 2 轮、且每轮句集规模一致 —— 单轮无法区分「稳定」与「碰巧」，"
        "而分母跨轮不同时比较无效。"
    ),
}


def _rounds_verdicts(block: dict) -> list[dict]:
    """S4 / S5：与「多轮」和 token 证据归属有关的两条结构性判定.

    ★ **S5 只有一半在这里**，另一半（分母跨轮不一致）由
    :func:`decision_eval_axis.aggregate_axis_blocks` **更早、更硬地**挡住：
    它直接 ``raise ValueError``。

    为什么不在这一层也判一次：那样会多出一条**永远不可达**的分支 ——
    聚合已经抛了，卡片根本构造不出来，这里的分母比较永远跑不到。
    一个声称会判「不可测量」却永远不会执行的分支，比没有它更坏：
    它让读者以为这种情形被优雅处理了（本模块自检最初就抓到过这一形态）。
    故此处只留真正可达的那一半：**轮数 < 2**，那是单轮出卡的路径。
    """
    evidence = block.get("evidence") or {}
    overall = evidence.get("overall") or {}
    coverage = evidence.get("coverage") or {}
    # ★ 「不可归因」有两种形态，**都必须判红**：
    #   * empty_output —— 模型一个 token 都没吐（失效输出）；
    #   * contradictory —— 决策词说不开口，而 token 说它吐的是别的（解析器与模型不一致）。
    #   早先只数前一种，于是「把每一行的 token 证据换成 </response>」这个变异体
    #   **绿着通过**（本模块自检抓出来的）—— 那正是判据恒真的一种形态。
    unattributable = overall.get(EVIDENCE_EMPTY_OUTPUT, 0) + overall.get(EVIDENCE_CONTRADICTORY, 0)
    rounds = block.get("rounds") or {}
    n_rounds = rounds.get("rounds_count") or 0

    if n_rounds >= 2:
        multi_verdict = VERDICT_PASS
        multi_reason = f"{n_rounds} 轮（分母跨轮一致性由聚合层强制，不一致时聚合直接报错）"
    else:
        multi_verdict = VERDICT_FAIL
        multi_reason = (
            f"只聚合了 {n_rounds} 轮 ⇒ 判红：单轮无法区分「稳定」与「碰巧」，调用方本该拒绝出卡"
        )

    return [
        {
            "criterion_id": "S4-no-unattributed-quiet",
            "statement": _MULTI_ROUND_CRITERIA["S4-no-unattributed-quiet"],
            "observed": {
                "quiet_rows_unattributable": unattributable,
                "quiet_rows_empty_output": overall.get(EVIDENCE_EMPTY_OUTPUT, 0),
                "quiet_rows_contradictory": overall.get(EVIDENCE_CONTRADICTORY, 0),
                "quiet_rows": coverage.get("quiet_rows"),
            },
            "verdict": VERDICT_FAIL if unattributable else VERDICT_PASS,
            "reason": (
                "无不开口行的证据缺失或自相矛盾"
                if not unattributable
                else (
                    f"{overall.get(EVIDENCE_EMPTY_OUTPUT, 0)} 行不开口决策的 token 数为 0"
                    "（失效输出），"
                    f"{overall.get(EVIDENCE_CONTRADICTORY, 0)} 行 token 与决策词矛盾"
                    "（解析器与模型不一致）⇒ 都不得计为判定沉默，判红"
                )
            ),
        },
        {
            "criterion_id": "S5-multi-round",
            "statement": _MULTI_ROUND_CRITERIA["S5-multi-round"],
            "observed": {
                "rounds_count": n_rounds,
                "n_directed_per_round": rounds.get("n_directed_per_round"),
                "n_nondirected_per_round": rounds.get("n_nondirected_per_round"),
            },
            "verdict": multi_verdict,
            "reason": multi_reason,
        },
    ]


def structural_verdicts(block: dict) -> list[dict]:
    """全部结构性判定：三条来自 criteria，两条与多轮有关."""
    checks = sorted(
        [*structural_checks(block), *_rounds_verdicts(block)],
        key=lambda item: item["criterion_id"],
    )
    # S3 的适用范围：只在该块真的含不开口行时才有分母可判。
    for check in checks:
        if check["criterion_id"] == "S3-token-evidence-complete":
            quiet_rows = (block.get("evidence") or {}).get("coverage", {}).get("quiet_rows")
            if not quiet_rows:
                check["verdict"] = VERDICT_UNMEASURABLE
                check["reason"] = "没有任何「不开口」行 ⇒ 无适用对象，不判通过"
    return checks


# --- 组装整张卡 -------------------------------------------------------------


def _overall_verdict(index: list[dict], structural: list[dict]) -> str:
    """总判定：有 FAIL 即 FAIL；无 FAIL 但有「无法测量」即不可判绿."""
    verdicts = [item["verdict"] for item in (*index, *structural)]
    if VERDICT_FAIL in verdicts:
        return VERDICT_FAIL
    if VERDICT_UNMEASURABLE in verdicts:
        return VERDICT_UNMEASURABLE
    return VERDICT_PASS


def criteria_registry() -> list[dict]:
    """判据清单（含出处），落进卡片以供电表可追溯（#159 要读它）."""
    return [
        {
            "criterion_id": item.criterion_id,
            "statement": item.statement,
            "metric": item.metric,
            "scope": "/".join(item.scope),
            "direction": item.direction,
            "threshold": item.threshold,
            "source": item.source,
            "min_denominator": item.min_denominator,
        }
        for item in CRITERIA
    ] + [
        {"criterion_id": cid, "statement": statement, "metric": None, "threshold": None}
        for cid, statement in (*STRUCTURAL_CRITERIA.items(), *_MULTI_ROUND_CRITERIA.items())
    ]


def negative_control_registry(rounds_n: int = 3) -> list[dict]:
    """负控清单 + **对合成输入的实际判定**（证明每条判据真的会判红）.

    ★ 它跑在**离线合成输入**上，不依赖真机也不依赖产物 ——
    所以「判据是否可证伪」这件事在任何机器上都能被复核。

    ★ 与 :func:`decision_eval_criteria.self_check` 用**同一条**多轮路径与同一组
    负控定义（``MUTATIONS`` 的 ``apply`` 收的是整份多轮输入）。这里把它落进卡片，
    使「一条命令的产出」本身就带着判据可证伪的证据，而不只是自己声称可证伪。
    """
    baseline_rounds = [synthetic_findings()["rows"] for _ in range(rounds_n)]
    out: list[dict] = []
    for mutation in MUTATIONS:
        mutated_rounds = mutation.apply([list(rows) for rows in baseline_rounds])
        block = _offline_block(mutated_rounds)
        verdicts = {item["criterion_id"]: item["verdict"] for item in criteria_verdicts(block)}
        fired = sorted(cid for cid, verdict in verdicts.items() if verdict == VERDICT_FAIL)
        missing = [cid for cid in mutation.must_fail if verdicts.get(cid) != VERDICT_FAIL]
        still_green = [cid for cid in mutation.must_not_pass if verdicts.get(cid) == VERDICT_PASS]
        if mutation.is_identity:
            # 恒等对照：它必须**什么都不变**，故状态直接表达这一点。
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


def _offline_block(per_round_rows: list[list[dict]]) -> dict:
    """离线合成输入 → 判定用的块（单轮不聚合，多轮聚合）."""
    blocks = [axis_block(rows, synthetic_prompt()) for rows in per_round_rows]
    if len(blocks) == 1:
        block = dict(blocks[0])
        block["rounds"] = {
            "rounds_count": 1,
            "n_directed_per_round": [block["overall"].get("n_directed")],
            "n_nondirected_per_round": [block["overall"].get("n_nondirected")],
        }
        return block
    return aggregate_axis_blocks(blocks)


def criteria_verdicts(block: dict) -> list[dict]:
    """一次算完「指标判据 + 结构性判据」的判定（合成块与真块共用一条路径）."""
    return [
        *[item.evaluate(block) for item in CRITERIA],
        *structural_verdicts(block),
    ]


def measurement_note(rounds_n: int) -> dict:
    """落进卡片的「这次测量本身是什么」说明块（供人读，也供门禁审计）."""
    return {
        "rounds_aggregated": rounds_n,
        "subsets": list(SUBSETS),
        "open_book_note": (
            "生产 prompt 的 few-shot 与测试集逐字重叠 10 句（其中 8 句非面向）"
            "⇒ 分数必须**按子集分别读**，混算会把记忆当成能力。"
        ),
    }


def build_card_from_rounds(
    per_round_rows: list[list[dict]],
    *,
    prompt: str,
    cost_fp: int | None = None,
    cost_fn: int | None = None,
) -> dict:
    """逐轮跑分行 → 定向轴记分卡（纯函数：无 I/O、不调模型）.

    Args:
        per_round_rows: 每轮一组跑分行。单轮也可出卡，但 S5 会判红 ——
            这正是本工单要的：**能出卡**不等于**结论可判稳**。
        prompt: 产生这些行的 system prompt（决定开卷/泛化子集归属）。
        cost_fp: 误响应代价权重；``None`` ⇒ 默认值。
        cost_fn: 漏判代价权重；``None`` ⇒ 默认值。

    Returns
    -------
        ``json.dumps`` 友好的嵌套 dict，**键顺序固定**（构造顺序即顺序）。

    Raises
    ------
        ValueError: 空输入，或代价权重非正数。
    """
    from decision_eval_axis import DEFAULT_COST_FN, DEFAULT_COST_FP

    if not per_round_rows:
        raise ValueError("至少要有一轮跑分行才能出卡（空输入给不出任何读数）")
    fp = DEFAULT_COST_FP if cost_fp is None else cost_fp
    fn = DEFAULT_COST_FN if cost_fn is None else cost_fn
    if fp <= 0 or fn <= 0:
        raise ValueError(f"代价权重必须为正数，收到 C_FP={fp} C_FN={fn}")

    per_round_blocks = [axis_block(rows, prompt, cost_fp=fp, cost_fn=fn) for rows in per_round_rows]
    block = (
        per_round_blocks[0]
        if len(per_round_blocks) == 1
        else aggregate_axis_blocks(per_round_blocks)
    )
    index = [item.evaluate(block) for item in CRITERIA]
    structural = structural_verdicts(block)

    return {
        "axis": "directed",
        "axis_question": "该说时说了吗 / 不该说时说了吗（不与时序轴合成单一 accuracy）",
        "cost": block["cost"],
        "overall": block["overall"],
        "by_subset": block["by_subset"],
        "evidence": block["evidence"],
        "rounds": block.get(
            "rounds",
            {
                "rounds_count": 1,
                "n_directed_per_round": [block["overall"].get("n_directed")],
                "n_nondirected_per_round": [block["overall"].get("n_nondirected")],
            },
        ),
        "criteria": {
            "index": index,
            "structural": structural,
            "verdict": _overall_verdict(index, structural),
        },
        "criteria_registry": criteria_registry(),
        "negative_controls": negative_control_registry(),
        "measurement": {
            **measurement_note(len(per_round_rows)),
            "rows_per_round": [len(rows) for rows in per_round_rows],
        },
    }


def build_card(
    artifact_path: str | Path = DEFAULT_ARTIFACT,
    *,
    variants: list[str] | None = None,
    cost_fp: int | None = None,
    cost_fn: int | None = None,
) -> dict:
    """从入库产物建卡（每个 variant 一张）."""
    loaded = load_artifact(artifact_path, variants)
    cards = {
        name: {
            "source": {
                "artifact": loaded["path"],
                "variant": name,
                "model": loaded["model"],
                "test_set_size": loaded["test_set_size"],
                "decoding": loaded["decoding"],
                "include_profile": _include_profile(name),
            },
            **build_card_from_rounds(
                payload["rounds"],
                prompt=payload["prompt"],
                cost_fp=cost_fp,
                cost_fn=cost_fn,
            ),
        }
        for name, payload in loaded["variants"].items()
    }
    return {
        "kind": "directed-axis-scorecard",
        "ticket": "#157",
        "spec": "#154",
        "artifact": loaded["path"],
        "cards": cards,
    }


# --- 阈值的可证伪核验 -------------------------------------------------------


def derive_bounds(snapshot: dict | None = None) -> dict[str, float]:
    """★ 从**冻结基线快照**重算每个阈值（**不**从活产物派生）.

    取法：每个 variant 的逐轮读数 → ``ceil(median + pstdev)``（容纳基线自身的抖动），
    再跨 variant 取**最大**值。

    ⚠️ **不从当前入库产物派生** —— 那是循环的：门禁要挡质量退化，而退化若伴随
    一次产物重跑，阈值就会跟着退化一起动，那条线永远拦不住东西。快照是冻结的，
    产物重跑只会被 :func:`bound_drift_report` **报出来**，不会悄悄改线。

    两个刻意不派生的阈值（``not_for_me_precision_pct``）原样返回声明值，
    并在 :func:`explain_bounds` 里明说它们是**保守取值而非实测**：
    真机 not-for-me 预测数只有 1–7 例，样本不足以定阈值。
    把保守取值伪造成「派生」正是本工单要消灭的那类谎。
    """
    data = BASELINE_SNAPSHOT if snapshot is None else snapshot
    derived: dict[str, float] = {}
    for key, per_variant in data["series"].items():
        candidates = [
            math.ceil(statistics.median(values) + statistics.pstdev(values))
            for values in per_variant.values()
            if values
        ]
        derived[key] = float(max(candidates))
    derived["not_for_me_precision_pct"] = float(BOUNDS["not_for_me_precision_pct"])
    return derived


def explain_bounds(snapshot: dict | None = None) -> list[dict]:
    """逐条说明阈值：声明值 / 从快照重算值 / 一致与否 / 出处."""
    derived = derive_bounds(snapshot)
    rows = []
    for key, declared in BOUNDS.items():
        recomputed = derived.get(key)
        rows.append(
            {
                "bound": key,
                "declared": declared,
                "recomputed_from_snapshot": recomputed,
                "matches": recomputed == declared,
                "derived": not key.startswith("not_for_me_precision"),
                "source": (
                    "保守下界（真机 not-for-me 预测数只 1–7 例，样本不足以定阈值）"
                    if key.startswith("not_for_me_precision")
                    else f"max over variants of ceil(median + pstdev)，取自 {BASELINE_SNAPSHOT['artifact']} 的冻结快照"
                ),
            }
        )
    return rows


def verify_bounds(snapshot: dict | None = None) -> tuple[bool, list[dict]]:
    """核验「声明的阈值 == 从冻结快照重算的阈值」。不一致即报错（返回 False）."""
    rows = explain_bounds(snapshot)
    return all(row["matches"] for row in rows if row["derived"]), rows


def bound_drift_report(artifact_path: str | Path = DEFAULT_ARTIFACT) -> dict:
    """★ 活产物是否已经偏离阈值所依据的那份基线快照.

    这不是错误检查（重跑产物是**正当操作**），而是**可见性**检查：
    一次「退化 + 重跑」若无人看见，就会变成「线自己挪了」。
    故本函数把三件事一起给出：产物 sha 是否等于快照绑定的 sha、
    按新产物算阈值会是多少、以及差值。

    Returns
    -------
        ``{artifact, sha_matches_snapshot, snapshot_sha256, artifact_sha256,
        declared, recomputed_from_artifact, deltas, note}``。
        产物缺失时 ``artifact_sha256`` 与 ``recomputed_from_artifact`` 为 ``None``
        （缺结果不是通过）。
    """
    artifact_path = Path(artifact_path)
    if not artifact_path.is_absolute():
        artifact_path = _REPO_ROOT / artifact_path
    report: dict = {
        "artifact": str(artifact_path),
        "snapshot_sha256": BASELINE_SNAPSHOT["artifact_sha256"],
        "declared": dict(BOUNDS),
        "note": (
            "阈值取自**冻结快照**，不随产物自动漂移 —— 否则一次退化 + 一次重跑"
            "就能把线一起挪走。本报告只做可见性：产物变了就报出来。"
        ),
    }
    if not artifact_path.exists():
        report.update(
            {
                "artifact_sha256": None,
                "sha_matches_snapshot": False,
                "recomputed_from_artifact": None,
                "deltas": None,
                "exists": False,
            }
        )
        return report

    digest = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    report["artifact_sha256"] = digest
    report["sha_matches_snapshot"] = digest == BASELINE_SNAPSHOT["artifact_sha256"]
    report["exists"] = True
    try:
        patterns = _artifact_bound_series(artifact_path)
    except (KeyError, ValueError) as exc:
        report["recomputed_from_artifact"] = None
        report["deltas"] = None
        report["error"] = f"{type(exc).__name__}: {exc}"
        return report
    recomputed = {
        key: float(
            max(
                math.ceil(statistics.median(values) + statistics.pstdev(values))
                for values in per_variant.values()
                if values
            )
        )
        for key, per_variant in patterns.items()
    }
    recomputed["not_for_me_precision_pct"] = BOUNDS["not_for_me_precision_pct"]
    report["recomputed_from_artifact"] = recomputed
    report["deltas"] = {
        key: round(recomputed[key] - BOUNDS[key], 3) for key in BOUNDS if key in recomputed
    }
    return report


def _artifact_bound_series(artifact_path: Path) -> dict[str, dict[str, list[float]]]:
    """从活产物抽出与快照同形的逐轮序列（供漂移比较用）."""
    loaded = load_artifact(artifact_path)
    mapping = {
        "nondirected_spurious_response_rate_pct": (
            ("overall",),
            "nondirected_spurious_response_rate_pct",
        ),
        "directed_nonresponse_rate_pct": (("overall",), "directed_nonresponse_rate_pct"),
        "cost_index": (("overall",), "cost_index"),
        "not_for_me_recall_pct_generalization": (
            ("by_subset", SUBSET_GENERALIZATION),
            "not_for_me_recall_pct",
        ),
    }
    out: dict[str, dict[str, list[float]]] = {}
    for key, (scope_path, metric) in mapping.items():
        per_variant: dict[str, list[float]] = {}
        for name, payload in loaded["variants"].items():
            blocks = [axis_block(rows, payload["prompt"]) for rows in payload["rounds"]]
            aggregated = blocks[0] if len(blocks) == 1 else aggregate_axis_blocks(blocks)
            scope: object = aggregated
            for part in scope_path:
                scope = scope[part]  # type: ignore[index]
            series = scope[metric]  # type: ignore[index]
            per_variant[name] = [float(v) for v in series["per_round"] if v is not None]
        out[key] = per_variant
    return out


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
        f"{_fmt(_scalar(scope.get('break_even_fp_fn_ratio')), 5)}"
        "  ← 两类错误等代价点"
    )
    if errors["sum"]:
        lines.append(
            f"    ⚠️ 推理失败行 {errors['sum']}（逐轮 {errors['per_round']}）"
            " —— 「没测到」不是「判定沉默」"
        )


def _scalar(value: object) -> object:
    """单轮块里的值是标量、多轮块里是聚合 dict；取标量视图."""
    if isinstance(value, dict):
        return value.get("median")
    return value


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
            med_a = _scalar(series_a)
            med_b = _scalar(series_b)
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


# --- 离线自检 ---------------------------------------------------------------


def self_check() -> int:
    """★ 离线可跑的负控自检（不需要模型、不需要产物）.

    断言三件互相咬合的事：

    1. **健康的多轮合成输入**上，全部判据 PASS（证明判定不是恒红 —— 一个恒红的
       判据同样没有分辨力，只是换了个方向骗人）；
    2. ★ **「永远输出 ``</silence>``」的桩**上，必须在漏判上判红。
       这是父 spec §D7 指定的负控①，也是旧判据被刷过的那条路。
       ⚠️ 它在**代价值**上必须判「无法测量」而不是判红：该桩一条 not-for-me
       都没预测 ⇒ 精确率「没测」。若这里判红，反而说明代码把「没测」当成了
       「测到 0」—— 那正是空精度事故的成因。故本项断言的是**不判绿**。
    3. **单轮输入**必须判红（S5）—— 「能出卡」不等于「结论可判稳」。
    """
    healthy_rounds = [synthetic_findings()["rows"] for _ in range(3)]
    healthy_block = aggregate_axis_blocks(
        [axis_block(rows, synthetic_prompt()) for rows in healthy_rounds]
    )
    healthy_verdicts = {
        item["criterion_id"]: item["verdict"] for item in criteria_verdicts(healthy_block)
    }

    stub_rounds = [
        [
            {
                "id": row["id"],
                "expected": row["expected"],
                "decision": "silence",
                "ok": True,
                "emitted_token_ids": [151669, 151645],
            }
            for row in synthetic_findings()["rows"]
        ]
        for _ in range(3)
    ]
    stub_block = aggregate_axis_blocks(
        [axis_block(rows, synthetic_prompt()) for rows in stub_rounds]
    )
    stub_verdicts = {
        item["criterion_id"]: item["verdict"] for item in criteria_verdicts(stub_block)
    }

    single_block = axis_block(synthetic_findings()["rows"], synthetic_prompt())
    single_verdicts = {
        item["criterion_id"]: item["verdict"] for item in criteria_verdicts(single_block)
    }

    print("=== 定向轴记分卡自检 (#157) ===")
    problems: list[str] = []
    failed_healthy = sorted(k for k, v in healthy_verdicts.items() if v != VERDICT_PASS)
    print(
        f"  健康输入(3 轮): 判绿 {sorted(k for k, v in healthy_verdicts.items() if v == VERDICT_PASS)}"
    )
    if failed_healthy:
        problems.append(f"健康输入上不该有非 PASS 判定，却出现 {failed_healthy} ⇒ 判据可能在恒红")

    must_fail = ("D2-directed-nonresponse", "D4-not-for-me-recall-generalization")
    # ★ D5 与 D3 **不得**出现在 must_not_pass 里，尽管直觉上「永远沉默」应该也过不了
    #   代价判据。实测结论写在这里以免后人重犯：
    #   * ``cost_index`` 挡不住沉默桩 —— 在 25 面向 / 26 非面向的基率下，
    #     C_FN×25 小于生产 prompt 的 3×FP + 1×FN（实测 49.0 vs 84.3）。
    #     真正挡住它的是 D2/D4 两条召回下限。
    #   * ``D3-not-for-me-precision`` 正确地判「无法测量」（一条 nfm 都没预测）
    #     —— 那是分母退化的正确处理，不是漏判。
    #   把这两条写进必须项，会让自检**假失败**，进而诱使人放宽判据；
    #   那比漏检更危险。
    must_not_pass = ()
    print(
        "  永远沉默的桩(3 轮): 判红 "
        + str(sorted(k for k, v in stub_verdicts.items() if v == VERDICT_FAIL))
        + " / 无法测量 "
        + str(sorted(k for k, v in stub_verdicts.items() if v == VERDICT_UNMEASURABLE))
    )
    for criterion_id in must_fail:
        if stub_verdicts.get(criterion_id) != VERDICT_FAIL:
            problems.append(
                f"★ 「永远输出沉默」的桩没能让 {criterion_id} 判红（实际 "
                f"{stub_verdicts.get(criterion_id)}）—— 旧判据正是这样被刷过的"
            )
    for criterion_id in must_not_pass:
        if stub_verdicts.get(criterion_id) == VERDICT_PASS:
            problems.append(
                f"★ 「永远输出沉默」的桩让 {criterion_id} **判绿了** —— "
                "分母退化的比率不得判绿（FAIL 或「无法测量」才对）"
            )

    if single_verdicts.get("S5-multi-round") != VERDICT_FAIL:
        problems.append(
            "单轮输入没有让 S5-multi-round 判红（实际 "
            f"{single_verdicts.get('S5-multi-round')}）—— 单轮结论不可判稳，出卡路径必须判红"
        )

    if problems:
        print("  verdict: FAIL")
        for problem in problems:
            print(f"    - {problem}")
        return 1
    print(
        "  verdict: PASS（健康 3 轮全绿；永远沉默的桩在漏判上判红、在分母退化的比率上不判绿；"
        "单轮判红）"
    )
    return 0


# --- CLI --------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """CLI：出卡 / 重算阈值 / 自检 / diff."""
    parser = argparse.ArgumentParser(
        description="定向轴记分卡（工单 #157）：代价加权 + token 级判据 + 可证伪"
    )
    parser.add_argument("--artifact", default=DEFAULT_ARTIFACT, help="多轮真机产物（默认入库那份）")
    parser.add_argument(
        "--variant", action="append", default=None, help="只看某个 variant（可重复）"
    )
    parser.add_argument("--cost-fp", type=int, default=None, help="误响应代价权重（默认 3）")
    parser.add_argument("--cost-fn", type=int, default=None, help="漏判代价权重（默认 1）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    parser.add_argument("--out", default=None, help="把 JSON 写到该路径（供门禁读取）")
    parser.add_argument(
        "--self-check", action="store_true", help="离线负控自检（不需要模型与产物）"
    )
    parser.add_argument("--show-bounds", action="store_true", help="打印阈值与出处")
    parser.add_argument("--verify-bounds", action="store_true", help="从产物重算阈值并比对")
    parser.add_argument("--diff-against", default=None, help="与另一份卡片 JSON 逐指标对比")
    args = parser.parse_args(argv)

    if args.self_check:
        return self_check()

    if args.show_bounds:
        for row in explain_bounds():
            flag = "派生" if row["derived"] else "保守取值"
            print(
                f"  {row['bound']:42s} declared={row['declared']:>7} "
                f"recomputed={row['recomputed_from_snapshot']:>7} "
                f"matches={row['matches']} [{flag}]"
            )
            print(f"      source: {row['source']}")
        return 0

    if args.verify_bounds:
        ok, rows = verify_bounds()
        for row in rows:
            print(
                f"  {row['bound']:42s} declared={row['declared']:>7} "
                f"recomputed={row['recomputed_from_snapshot']:>7} matches={row['matches']}"
            )
        if not ok:
            print(
                "✗ 声明的阈值与从**冻结快照**重算的不一致 —— BOUNDS 与 BASELINE_SNAPSHOT "
                "已分叉（不得静默放过）：要么改回阈值，要么同步更新快照并说明理由"
            )
            return 1
        drift = bound_drift_report()
        if drift["sha_matches_snapshot"]:
            print("✓ 每个派生阈值都与**冻结基线快照**一致；产物的 sha256 仍等于快照绑定值")
        else:
            print("✓ 每个派生阈值都与**冻结基线快照**一致（关键：阈值不随产物漂移）")
            print("⚠️ 但当前产物已不是基线快照绑定的那一份：")
            print(f"     快照 sha256 = {drift['snapshot_sha256']}")
            print(f"     产物 sha256 = {drift['artifact_sha256']}")
            print(
                f"     按当前产物算阈值会是 {drift['recomputed_from_artifact']}"
                f"（差值 {drift['deltas']}）"
            )
            print(
                "   重跑产物是正当操作，但**必须可见** —— 否则一次退化 + 一次重跑"
                "就能把门禁的线一起挪走。若要重新基线化，请同步更新 BASELINE_SNAPSHOT。"
            )
        return 0

    if args.out and not args.json:
        args.json = True  # --out 必然落结构化内容

    try:
        report = build_card(
            args.artifact,
            variants=args.variant,
            cost_fp=args.cost_fp,
            cost_fn=args.cost_fn,
        )
    except (FileNotFoundError, KeyError, ValueError) as exc:
        print(f"✗ 无法出卡：{exc}")
        return 2

    if args.json:
        text = json.dumps(report, ensure_ascii=False, indent=2)
        if args.out:
            out_path = Path(args.out)
            # ★ newline="\n" 是承重的（AGENTS.md 字节核验）：Windows 上文本模式
            # 会把 "\n" 全写成 "\r\n"，产物因此与行尾纪律不符，且破坏 diff。
            out_path.write_text(text + "\n", encoding="utf-8", newline="\n")
            print(f"[card] 已写出 {out_path}")
        else:
            print(text)
    else:
        print(render_scorecard(report))

    if args.diff_against:
        previous = json.loads(Path(args.diff_against).read_text(encoding="utf-8"))
        print(f"\n--- diff vs {args.diff_against} ---")
        print(diff_reports(previous, report))

    # 退出码：卡片判定直接映射 —— 有 FAIL 就是 1，「无法测量」是 2。
    # ★ 「没测」不得返回 0（本仓 #162 已把这条纪律钉进运行器）。
    verdicts = {card["criteria"]["verdict"] for card in report["cards"].values()}
    if VERDICT_FAIL in verdicts:
        return 1
    if VERDICT_UNMEASURABLE in verdicts:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
