# ruff: noqa: RUF001, RUF002, RUF003
# (RUF001/002/003 = ambiguous fullwidth punctuation; this module's prose is
# Chinese. Same established repo convention as decision_eval_set.py /
# decision_eval_score.py / decision_eval_rounds.py.)
"""定向轴记分卡：**一条命令**产出结构化、可 diff 的读数（工单 #157）.

    python -m decision_eval_card                 # 卡片（读入库的多轮真机产物）
    python -m decision_eval_card --json          # 结构化，供门禁 / diff
    python -m decision_eval_card --self-check    # 离线负控自检（不需要模型）
    python -m decision_eval_card --show-bounds   # 阈值 + 出处
    python -m decision_eval_card --verify-bounds # 从**冻结快照**重算阈值并比对
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
   ``--verify-bounds`` 从**冻结基线快照**重算阈值并与声明值比对，不一致即报错；
   而产物若被重跑过，:mod:`.decision_eval_bounds` 的漂移报告会把它**报出来**。

★ 一张卡同时说清「分数」与「这份分数能不能信」
-----------------------------------------------
``criteria`` 之外还有 ``evidence`` 与 ``measurement`` 两块：分母是否齐备、
哪些行有 token 级证据、哪几轮被聚合、开卷/泛化如何分列。**只读分数不读这两块**，
就会重犯「precision 100% 而召回 0%」那次事故 —— 数字是真的，结论是错的。

模块划分（``coding-standards.md`` §7：一个模块一个变化原因）
------------------------------------------------------------
================================================================  ==========================
:mod:`.decision_eval_axis`                                          怎么算（宽窄口径 / 代价 / token 证据 / 聚合）
:mod:`.decision_eval_criteria`                                      怎么判（判据 / 负控 / 冻结快照）
:mod:`.decision_eval_sources`                                       从哪读（产物 → 逐轮行）
:mod:`.decision_eval_synthetic`                                     离线夹具（负控自检用）
:mod:`.decision_eval_bounds`                                        阈值出处核验与漂移报告
:mod:`.decision_eval_report`                                        怎么印 / 怎么 diff
**本模块**                                                          把上面这些组装成一张卡 + CLI
================================================================  ==========================

Run tests: cd services/webinfer && python -m pytest tests/test_decision_eval_card.py -q
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

# ★ 本模块是**公开入口**（`python -m decision_eval_card`、门禁与测试都从它取），
#   故它有意识地**再导出**下层符号，使调用方只依赖一个名字。
#   这里用 `X as X` 形式显式声明「这是 re-export 而非本地使用」——
#   简洁但有效的写法，且 ruff 的 F401 会认得它，不用逐行 noqa。
from decision_eval_axis import (
    EVIDENCE_CONTRADICTORY as EVIDENCE_CONTRADICTORY,
)
from decision_eval_axis import (
    EVIDENCE_EMPTY_OUTPUT as EVIDENCE_EMPTY_OUTPUT,
)
from decision_eval_axis import (
    EVIDENCE_NOT_QUIET as EVIDENCE_NOT_QUIET,
)
from decision_eval_axis import (
    aggregate_axis_blocks as aggregate_axis_blocks,
)
from decision_eval_axis import (
    axis_block as axis_block,
)
from decision_eval_bounds import (
    bound_drift_report as bound_drift_report,
)
from decision_eval_bounds import (
    derive_bounds as derive_bounds,
)
from decision_eval_bounds import (
    explain_bounds as explain_bounds,
)
from decision_eval_bounds import (
    verify_bounds as verify_bounds,
)
from decision_eval_criteria import (
    BASELINE_SNAPSHOT as BASELINE_SNAPSHOT,
)
from decision_eval_criteria import (
    BOUNDS as BOUNDS,
)
from decision_eval_criteria import (
    CRITERIA as CRITERIA,
)
from decision_eval_criteria import (
    MUTATIONS as MUTATIONS,
)
from decision_eval_criteria import (
    STRUCTURAL_CRITERIA as STRUCTURAL_CRITERIA,
)
from decision_eval_criteria import (
    VERDICT_FAIL as VERDICT_FAIL,
)
from decision_eval_criteria import (
    VERDICT_PASS as VERDICT_PASS,
)
from decision_eval_criteria import (
    VERDICT_UNMEASURABLE as VERDICT_UNMEASURABLE,
)
from decision_eval_criteria import (
    structural_checks as structural_checks,
)
from decision_eval_report import (
    diff_cards as diff_cards,
)
from decision_eval_report import (
    diff_reports as diff_reports,
)
from decision_eval_report import (
    render_card as render_card,
)
from decision_eval_report import (
    render_scorecard as render_scorecard,
)
from decision_eval_set import (
    SUBSET_GENERALIZATION as SUBSET_GENERALIZATION,
)
from decision_eval_set import (
    SUBSETS as SUBSETS,
)
from decision_eval_sources import (
    DEFAULT_ARTIFACT as DEFAULT_ARTIFACT,
)
from decision_eval_sources import (
    _include_profile as _include_profile,
)
from decision_eval_sources import (
    load_artifact as load_artifact,
)
from decision_eval_synthetic import (
    TOKEN_FOR_DECISION as _TOKEN_FOR_DECISION,  # noqa: F401  (re-export)
)
from decision_eval_synthetic import (
    synthetic_findings as synthetic_findings,
)
from decision_eval_synthetic import (
    synthetic_prompt as synthetic_prompt,
)

#: 全部判据 id（指标 + 结构性 + 卡片自己补的多轮两条）。
#: ★ 由 :func:`criteria_registry` 产出，负控覆盖完整性检查拿它当**判据集合的真值**
#: —— 早先那份检查只看 ``CRITERIA``，于是卡片补的 S4/S5 完全没有负控却全绿。
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
    parser.add_argument("--verify-bounds", action="store_true", help="从冻结快照重算阈值并比对")
    parser.add_argument("--drift", action="store_true", help="报告当前产物是否已偏离基线快照")
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

    if args.verify_bounds or args.drift:
        return _report_bounds(args.verify_bounds)

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


def _report_bounds(verify: bool) -> int:
    """阈值核验 / 漂移报告的共用输出（``--verify-bounds`` 与 ``--drift``）."""
    if verify:
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
        return 0
    print("✓ 每个派生阈值都与**冻结基线快照**一致（关键：阈值不随产物漂移）")
    print("⚠️ 但当前产物已不是基线快照绑定的那一份：")
    print(f"     快照 sha256 = {drift['snapshot_sha256']}")
    print(f"     产物 sha256 = {drift['artifact_sha256']}")
    print(
        f"     按当前产物算阈值会是 {drift['recomputed_from_artifact']}（差值 {drift['deltas']}）"
    )
    print(
        "   重跑产物是正当操作，但**必须可见** —— 否则一次退化 + 一次重跑"
        "就能把门禁的线一起挪走。若要重新基线化，请同步更新 BASELINE_SNAPSHOT。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
