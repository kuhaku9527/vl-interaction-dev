# ruff: noqa: RUF001, RUF002, RUF003
# (RUF001/002/003 = ambiguous fullwidth punctuation; this module's prose is
# Chinese. Same established repo convention as decision_eval_set.py /
# decision_eval_score.py / decision_eval_rounds.py.)
"""定向轴的**可证伪判据** + 配套负控（工单 #157，父 spec #154 §七）.

为什么不能只是「加几条阈值」
----------------------------
既有判据 ``not-for-me precision >= 80%`` 的失效不是阈值调错了，而是
**它恒真**：它只把「预测成 not-for-me」算作乱插，而真正会出声的是
``response`` / ``delegation`` ⇒ **一个永远输出 ``</silence>`` 的模型无条件通过**。
把 80 改成 90 不解决这件事。

所以本模块的每条判据都必须自带两样东西：

1. **出处**（:attr:`Criterion.source`）—— 阈值必须有来历。本仓硬约束是
   「阈值必须是有出处的数字」（父 spec §六）。本模块的出处一律是**已入库的
   真机产物**，且 :func:`derive_bounds` 能**从产物重新算出**这些数字；
   ``services/webinfer/tests/test_decision_eval_criteria.py`` 里的
   ``test_declared_bounds_match_derivation`` 断言「声明值 == 重算值」，
   于是常量**没法**与它的证据分叉（这是本仓 25 vs 26 事故的形态）。
2. **配套负控**（:data:`MUTATIONS`）—— 每条判据都绑定至少一个**故意做错的输入**，
   并证明它会让该判据**判红**。没有这一条，「判据能跑绿」不等于「判据能分辨对错」。

★ 三种状态，不是一个布尔
------------------------
:data:`VERDICT_PASS` / :data:`VERDICT_FAIL` / :data:`VERDICT_UNMEASURABLE`。
第三种是第一等公民：**分母为 0 时判「无法测量」，不判通过、也不判 0 分**。
这是 #154 里「一个永远输出沉默的模型可以无条件刷过判据」的直接修法 ——
在旧判据下它拿到 precision 100%；在本模块下它至少拿到**无法测量**，
而它在 ``spurious_response`` 与 ``cost_index`` 上**必然判红**（见负控 ``always_silence``）。

Run tests: cd services/webinfer && python -m pytest tests/test_decision_eval_criteria.py -q
Self-check: python -m decision_eval_criteria --self-check
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass

from decision_eval_axis import (
    DECISIONS_QUIET,
    aggregate_axis_blocks,
    axis_block,
    counter_is_measured_every_round,
    counter_value,
    median_view,
)
from decision_eval_set import GROUP_DIRECTED, GROUP_NONDIRECTED

# --- 判定状态 ---------------------------------------------------------------

VERDICT_PASS = "pass"
VERDICT_FAIL = "fail"
#: ★ 与 PASS / FAIL **并列**的第三种状态：判据缺分母 ⇒ 既不通过也不失败。
#: 「测不了」不等于「没通过」，更不等于「通过」（#162 已把这条纪律钉进运行器）。
VERDICT_UNMEASURABLE = "unmeasurable"

VERDICTS: tuple[str, ...] = (VERDICT_PASS, VERDICT_FAIL, VERDICT_UNMEASURABLE)

# --- 判据出处：**冻结的基线快照**（不是「活产物」）---------------------------
#
# ★ 这里的设计是由一次真实的坑逼出来的，写下来以免后人重犯：
#
# 初版让阈值**每次从当前入库产物重算**。看起来「阈值与它的证据绑在一起」很严谨，
# 实则是**循环的**：门禁要挡的是「质量退化」，而退化若伴随产物重跑（一次真机
# 3 轮就会重写该文件），阈值会**跟着退化一起动** —— 那条线于是永远拦不住任何东西。
# 这正是本仓最贵的那一类缺陷：**fail-open 且看起来在工作**。
#
# 现在的做法：阈值是**冻结常量**，并用一份**快照**记录它们出自哪一次读数
# （含产物 sha256 与逐轮序列）。于是：
#   * `declared == derive(snapshot)` 检验「声明的阈值与它声称的算术一致」；
#   * 产物被重新生成时 `--verify-bounds` **报出来**（不报错 —— 重跑是正当操作，
#     但必须可见），并给出「按新产物算会是多少」供人决定是否重新基线化。
# 基线绝不能自己跟着被测量的东西走。

#: 阈值来源的那一次入库读数的**产物路径**（人读用；权威绑定靠 sha256）。
BASELINE_ARTIFACT = "doc/research/data/benchmark_production_live_prompt_rounds.json"

#: ★ 冻结基线快照：阈值**只能**从这里派生，不从活产物派生。
#:
#: ``series`` 是逐 variant 的逐轮读数（原样抄自上面那份产物），
#: ``sha256`` 是**那份产物**的哈希 —— 于是「这份快照确实来自它」是可核验的，
#: 而不是一句自述。
BASELINE_SNAPSHOT: dict = {
    "artifact": BASELINE_ARTIFACT,
    "artifact_sha256": "0c29eef03dadbfb5244844e010fc1276d9ef8dbd07aa2b07114c860f96fe528f",
    "recorded_at": "2026-09-23",
    "model": "joyai-vl-interaction-preview",
    "rounds_per_variant": 3,
    "cases_per_round": 56,
    "decoding": {"temperature": 0.8, "top_p": 0.9, "top_k": 40, "max_tokens": 1024},
    "series": {
        "nondirected_spurious_response_rate_pct": {
            "P_live4_prod_prompt": [30.8, 50.0, 61.5],
            "P2_live4_prod_prompt_profile": [34.6, 42.3, 26.9],
        },
        "directed_nonresponse_rate_pct": {
            "P_live4_prod_prompt": [20.0, 12.0, 12.0],
            "P2_live4_prod_prompt_profile": [20.0, 12.0, 0.0],
        },
        "cost_index": {
            "P_live4_prod_prompt": [56.9, 82.4, 100.0],
            "P2_live4_prod_prompt_profile": [62.7, 70.6, 41.2],
        },
        "not_for_me_recall_pct_generalization": {
            "P_live4_prod_prompt": [0.0, 0.0, 0.0],
            "P2_live4_prod_prompt_profile": [11.1, 0.0, 11.1],
        },
    },
}

#: 派生规则的人读说明。
BOUND_DERIVATION = (
    "阈值 = max over variants of ceil(median + pstdev)，取自 BASELINE_SNAPSHOT."
    "series（冻结快照，绑定产物的 sha256）。"
    "用「中位 + 离散度」而非中位：单轮不可作点估计（同一配置三次跑的中位 38.5→26.9→46.2）。"
    "取各 variant 的**最大**值：阈值要容纳**最差的那个已入库配置**，"
    "否则它会把一个与基线等价的配置判红。"
    "★ 快照是**冻结**的：阈值不随活产物自动漂移，否则一次退化 + 一次重跑就能把线一起挪走。"
)

#: 派生出的阈值（每项都能被 :func:`decision_eval_card.derive_bounds` 从快照重算）。
BOUNDS: dict[str, float] = {
    # 误响应率上限：基线最差 variant 的 ceil(median+pstdev)
    "nondirected_spurious_response_rate_pct": 63.0,
    # 面向句非响应率上限
    "directed_nonresponse_rate_pct": 21.0,
    # nfm 精确率下限（**带非退化守卫**，见 Criterion.min_denominator）
    "not_for_me_precision_pct": 70.0,
    # nfm 召回率下限（**泛化子集**，扣掉开卷记忆效应）
    "not_for_me_recall_pct_generalization": 17.0,
    # 代价加权主指标上限
    "cost_index": 101.0,
}

# --- 判据 -------------------------------------------------------------------


@dataclass(frozen=True)
class Criterion:
    """一条判据：一条指标 + 一个方向 + 一个阈值 + 一处出处 + 一个作用域.

    Attributes
    ----------
        criterion_id: 稳定 id（负控与结果文件都用它引用）。
        statement: 人读的一句话，含方向与阈值 —— **判据本身必须能被读懂**。
        metric: :func:`decision_eval_axis.axis_metrics` 产出的指标名。
        direction: ``"upper"``（越小越好）或 ``"lower"``（越大越好）。
        threshold: 阈值，取自 :data:`BOUNDS`。
        source: 阈值出处（须可追溯到可复算的产物）。
        scope: 读哪一块：``("overall",)`` 或 ``("by_subset", "<subset>")``。
        min_denominator: ★ **非退化守卫**。该计数器必须**每一轮都非零**
            （见 :func:`decision_eval_axis.counter_is_measured_every_round`）；
            否则判 :data:`VERDICT_UNMEASURABLE`。
            没有它，``precision`` 这类比率会在「一条都没预测」时退化
            —— 那正是 100% 空精度的成因；而**只查总和还不够**，
            因为三轮 ``[1, 0, 0]`` 的和也是非零（本工单真机产物就是这个形状）。
    """

    criterion_id: str
    statement: str
    metric: str
    direction: str
    threshold: float
    source: str
    scope: tuple[str, ...] = ("overall",)
    min_denominator: str | None = None

    def evaluate(self, block: dict) -> dict:
        """对一张定向轴卡片作出 PASS / FAIL / 无法测量 的判定.

        ★ **多轮读中位数、单轮读标量**，两者走同一条代码路径。
        多轮块里的值是 :func:`decision_eval_axis.aggregate_metric` 的产物
        （``{median, stdev, per_round, …}``），单轮块里的是裸标量 ——
        判据不该因此有两份实现（两份必然分叉，而分叉的那一份是没被测过的）。

        ★ **比率与分母取同口径**：多轮时两者都取**中位轮**的量，而不是
        「比率取中位、计数取三轮之和」。混用会让守卫比错东西 ——
        例如 ``not_for_me_predicted`` 三轮和是 6 而某轮是 0，于是
        「本轮分母为 0、精确率无意义」这个事实被和数掩盖，判绿。

        Returns
        -------
            ``{criterion_id, verdict, observed, threshold, direction,
            statement, source, reason}`` —— 可直接落 JSON。
        """
        scope = _resolve_scope(block, self.scope)
        observed = median_view(scope.get(self.metric))
        denominator_ok = (
            counter_is_measured_every_round(scope, self.min_denominator)
            if self.min_denominator
            else True
        )
        base = {
            "criterion_id": self.criterion_id,
            "statement": self.statement,
            "metric": self.metric,
            "scope": "/".join(self.scope),
            "direction": self.direction,
            "threshold": self.threshold,
            "source": self.source,
        }
        if observed is None:
            return {
                **base,
                "observed": None,
                "verdict": VERDICT_UNMEASURABLE,
                "reason": (
                    f"分母为 0：{self.metric} 在该作用域下算不出来 ⇒ 判「无法测量」。"
                    "「没测」既不是通过也不是 0 分 —— 把它读成绿正是空精度事故的成因。"
                ),
            }
        if not denominator_ok:
            counter = counter_value(scope, self.min_denominator)
            return {
                **base,
                "observed": observed,
                "verdict": VERDICT_UNMEASURABLE,
                "reason": (
                    f"{self.min_denominator} 逐轮为 {counter['per_round']} —— "
                    f"只有 {counter['n_rounds_nonzero']}/{counter['n_rounds']} 轮有分母 ⇒ "
                    "本判据的比率**退化**、字面达标而无实质"
                    "（历史缺陷：precision 100% 的分母只有 3 例；本工单真机产物里 "
                    "not_for_me_predicted 是 [1, 0, 0]，只查总和会让它蒙混过关）。"
                    "故判「无法测量」，不得据此判绿。"
                ),
            }
        passing = (
            observed <= self.threshold if self.direction == "upper" else observed >= self.threshold
        )
        comparison = "<=" if self.direction == "upper" else ">="
        return {
            **base,
            "observed": observed,
            "verdict": VERDICT_PASS if passing else VERDICT_FAIL,
            "reason": (
                f"{self.metric}={observed} {comparison} {self.threshold}"
                + ("（达标）" if passing else " —— 未达标")
            ),
        }


def _resolve_scope(block: dict, scope: tuple[str, ...]) -> dict:
    """按 ``("by_subset", "generalization")`` 这类路径取子块；缺则给空 dict."""
    node: object = block
    for key in scope:
        if not isinstance(node, dict) or key not in node:
            return {}
        node = node[key]
    return node if isinstance(node, dict) else {}


#: 全部判据。★ 每条都在 :data:`MUTATIONS` 里至少有一个「必须让它判红」的负控
#: （由 :func:`self_check` 强制，缺失即报错）。
#:
#: ★★ :attr:`Criterion.statement` 里的阈值**一律由 f-string 从 :data:`BOUNDS` 插值**，
#: 不写死数字。这是评审查出的一处 HIGH 缺陷的修法：初版把「54%」「27%」「19%」「≤93」
#: 写死在 statement 里，而 threshold 是 63/21/17/101 —— **同一份产出的两处数字互相矛盾**，
#: 而 `--verify-bounds` 只守 threshold。`statement` 会随卡片落进 JSON，
#: 正是门禁作者会照抄的那句话。数字写两遍，就一定会分叉；由 :func:`statements_match_bounds`
#: 与配套测试钉住。
CRITERIA: tuple[Criterion, ...] = (
    Criterion(
        criterion_id="D1-nondirected-no-spurious",
        statement=(
            "非面向句里「开口」（response ∪ delegation）的比例不得超过 "
            f"{BOUNDS['nondirected_spurious_response_rate_pct']:.0f}%"
            " —— 这是「乱插」的宽口径：delegation 会触发外部检索与播报，比单纯应答更糟。"
        ),
        metric="nondirected_spurious_response_rate_pct",
        direction="upper",
        threshold=BOUNDS["nondirected_spurious_response_rate_pct"],
        source=f"{BOUND_DERIVATION}（基线最差 variant 上界）",
        min_denominator="n_nondirected",
    ),
    Criterion(
        criterion_id="D2-directed-nonresponse",
        statement=(
            "面向句里「不开口」的比例不得超过 "
            f"{BOUNDS['directed_nonresponse_rate_pct']:.0f}% —— 宽口径："
            "被 </not-for-me> 误杀与被 </silence> 吞掉**同罪**"
            "（旧字段只数前者，故恒为 0.0%）。"
        ),
        metric="directed_nonresponse_rate_pct",
        direction="upper",
        threshold=BOUNDS["directed_nonresponse_rate_pct"],
        source=f"{BOUND_DERIVATION}（各 variant 的最坏轮）",
        min_denominator="n_directed",
    ),
    Criterion(
        criterion_id="D3-not-for-me-precision",
        statement=(
            "判成 not-for-me 的句子里，真实非面向的比例不低于 "
            f"{BOUNDS['not_for_me_precision_pct']:.0f}%"
            " —— ★ 附非退化守卫：一条 not-for-me 都没预测时判「无法测量」，"
            "不得判绿（这正是旧判据被刷过的方式）。"
        ),
        metric="not_for_me_precision_pct",
        direction="lower",
        threshold=BOUNDS["not_for_me_precision_pct"],
        source=(
            f"{BOUNDS['not_for_me_precision_pct']:.0f} 不是实测基线（真机里 not-for-me "
            "预测数只 1–7 例，样本小到不足以定阈值）⇒ 取一个**保守下界**并**显式标注**"
            "它是有意保守的：它只用来挡住「往 not-for-me 倾泻」这一类退化，"
            "不声称等于基线水平。"
        ),
        min_denominator="not_for_me_predicted",
    ),
    Criterion(
        criterion_id="D4-not-for-me-recall-generalization",
        statement=(
            "**泛化子集**上 not-for-me 召回率不低于 "
            f"{BOUNDS['not_for_me_recall_pct_generalization']:.0f}%（开卷子集不计入）"
            " —— 生产 prompt 的 few-shot 与测试集逐字重叠 10 句，"
            "混算会把记忆当成能力。"
        ),
        metric="not_for_me_recall_pct",
        direction="lower",
        threshold=BOUNDS["not_for_me_recall_pct_generalization"],
        source=f"{BOUND_DERIVATION}（泛化子集，各 variant 的最坏轮）",
        scope=("by_subset", "generalization"),
        min_denominator="n_nondirected",
    ),
    Criterion(
        criterion_id="D5-cost-index",
        statement=(
            "代价加权主指标 cost_index ≤ "
            f"{BOUNDS['cost_index']:.0f}（每 100 例里 3×误响应 + 1×漏判）"
            " —— 单一 accuracy 已被本工单否决：它在两类错误上等权，"
            "而项目两类错误代价明确不对称。"
        ),
        metric="cost_index",
        direction="upper",
        threshold=BOUNDS["cost_index"],
        source=f"{BOUND_DERIVATION}（各 variant 的最坏轮）",
    ),
)


def statements_match_bounds() -> list[str]:
    """★ 自检用：``statement`` 里出现的数字是否与 ``threshold`` 一致.

    Returns
    -------
        不一致的说明列表（空 = 全部一致）。

    存在的理由：``statement`` 是随卡片落进 JSON、供门禁作者照抄的那句话。
    它与 ``threshold`` 是**同一个事实的两处呈现**，因此必须有东西钉住它们 ——
    否则「阈值有出处、不会静默分叉」这条声明只对 ``threshold`` 成立，
    而对**被发表出去的那一句话**不成立（评审查出的 HIGH 缺陷）。
    """
    problems: list[str] = []
    for criterion in CRITERIA:
        expected = f"{criterion.threshold:.0f}"
        if expected not in criterion.statement:
            problems.append(
                f"{criterion.criterion_id}: threshold={criterion.threshold}，"
                f"但 statement 里找不到「{expected}」—— 两处数字已分叉"
            )
    return problems


#: ★ **实测逼出来的一条边界**（不是设计出来的，故单列而不做判据）：
#:
#: 在本测试集的基率下（25 面向 / 26 非面向），**「永远沉默」这个平凡桩的加权代价
#: 比生产 prompt 更低**：`C_FP:C_FN = 3:1` 时，桩的代价是 ``1×25 = 25`` 个单位，
#: 而生产 prompt 是 ``3×12 + 1×5 = 41`` 个单位（实测 cost_index 中位 49.0 vs 84.3）。
#:
#: ⇒ **`cost_index` 单独挡不住「沉默刷分」**。真正挡住那个桩的是 D2（面向句非响应率）
#: 与 D4（泛化召回）：它们让「什么都不说」当场判红。
#:
#: 为什么把这条写进 `COST_INDEX_CANNOT_GUARD_AGAINST` 而**不**做成一条判据：
#: 做成判据意味着「生产 prompt 必须严格优于永远沉默」，而它**当前并不满足**。
#: 本工单的范围是**造尺子、不动被测量物**（#154 §Out of scope）—— 一条出厂即红的
#: 判据会让 #159 的门禁从第一天起就没法用，也会诱使人去放宽阈值而不是去看问题。
#: 记成一条**可读的实测事实**既留了痕，又不越权。
COST_INDEX_CANNOT_GUARD_AGAINST = (
    "「永远沉默」的桩。实测其 cost_index（49.0）低于生产 prompt（84.3）："
    "在 25/26 的基率下，C_FP:C_FN=3:1 赋予漏判的代价乘以 25 仍小于误响应乘以 12。"
    "⇒ 挡住沉默策略的是 D2/D4 两条召回下限，不是代价指数。"
    "卡片里的 cost_index_always_silent / cost_index_always_speaking 就是为此提供的参照。"
)

#: 结构性判据：它们不看阈值，看**这次测量本身是否成立**（fail-closed）。
#: 与 :data:`CRITERIA` 分开，因为它们的「负控」是**删掉证据**而不是改指标。
STRUCTURAL_CRITERIA: dict[str, str] = {
    "S1-denominators-present": (
        "两个作用域都必须有面向句与非面向句的句数（分母齐备） —— 「没跑完」不得被当成「通过」。"
    ),
    "S2-no-round-errors": (
        "没有任何推理失败行（ok=False）—— 失败行是「没测到」，"
        "不是「判定沉默」，混入会把失效输出洗成正确沉默。"
    ),
    "S3-token-evidence-complete": (
        "每一行「不开口」决策都必须带 token 级证据"
        " —— 没有证据的行不可归因，「判定沉默」与「空输出」在 content 层同形。"
    ),
}


def structural_checks(block: dict) -> list[dict]:
    """跑结构性判据，返回逐条结果（全部 fail-closed）.

    ★ 计数一律经 :func:`decision_eval_axis.counter_value` 读（单轮是裸 int、
    多轮是结构化三件套）。**不用 ``median_view``** —— 那个函数取的是「中位」，
    对计数是错的：句集规模跨轮应当稳定，但错误数才是要看的量，
    而「取中位」会把「一轮全崩」稀释掉（本模块自检抓到过这个）。
    """
    overall = block.get("overall") or {}
    coverage = (block.get("evidence") or {}).get("coverage") or {}
    directed = counter_value(overall, "n_directed")
    nondirected = counter_value(overall, "n_nondirected")
    errors = counter_value(overall, "n_errors")
    n_directed = directed["sum"]
    n_nondirected = nondirected["sum"]
    n_errors = errors["sum"]
    results: list[dict] = [
        {
            "criterion_id": "S1-denominators-present",
            "statement": STRUCTURAL_CRITERIA["S1-denominators-present"],
            "observed": {"n_directed": n_directed, "n_nondirected": n_nondirected},
            "verdict": (
                VERDICT_PASS
                if n_directed
                and n_nondirected
                and directed["n_rounds_nonzero"] == directed["n_rounds"]
                and nondirected["n_rounds_nonzero"] == nondirected["n_rounds"]
                else VERDICT_FAIL
            ),
            "reason": (
                f"分母齐备（面向 {n_directed} / 非面向 {n_nondirected}，逐轮 "
                f"{directed['per_round']} / {nondirected['per_round']}）"
                if n_directed
                and n_nondirected
                and directed["n_rounds_nonzero"] == directed["n_rounds"]
                and nondirected["n_rounds_nonzero"] == nondirected["n_rounds"]
                else (
                    f"缺分母 ⇒ 判红（fail-closed：没跑完不得当成通过）"
                    f"（逐轮 面向 {directed['per_round']} / 非面向 {nondirected['per_round']}）"
                )
            ),
        },
        {
            "criterion_id": "S2-no-round-errors",
            "statement": STRUCTURAL_CRITERIA["S2-no-round-errors"],
            "observed": {"sum": n_errors, "per_round": errors["per_round"]},
            "verdict": VERDICT_PASS if not n_errors else VERDICT_FAIL,
            "reason": (
                "无失败行"
                if not n_errors
                else (
                    f"{n_errors} 行推理失败（逐轮 {errors['per_round']}）⇒ 判红"
                    "（「没测到」不是「判定沉默」）"
                )
            ),
        },
        {
            "criterion_id": "S3-token-evidence-complete",
            "statement": STRUCTURAL_CRITERIA["S3-token-evidence-complete"],
            "observed": {
                "rows_without_token_evidence": coverage.get("rows_without_token_evidence"),
                "quiet_rows": coverage.get("quiet_rows"),
            },
            # ★ **三值**，不是两值。评审查出的 fail-open：早先写成
            #   ``PASS if not coverage.get(...rows_without_token_evidence)`` ——
            #   当 ``evidence.coverage`` **整个缺失**时，``.get`` 返回 ``None``，
            #   ``not None`` 为真 ⇒ **判绿**。而「覆盖块缺失」恰恰意味着
            #   「这次测量没有留下可比对的证据」，与「一条都不缺」是两件事。
            #   这是本仓最贵的那一类缺陷（缺输入被当成通过），故这里逐态分开：
            #   * 缺覆盖块 ⇒ 无法测量（不是通过，也不是被测对象的错）
            #   * 覆盖块在但没有不开口行 ⇒ 无适用对象 ⇒ 无法测量
            #   * 有不开口行且行行有证据 ⇒ 通过
            #   * 有不开口行缺证据 ⇒ 判红
            "verdict": _s3_verdict(coverage),
            "reason": _s3_reason(coverage),
        },
    ]
    return results


def _s3_verdict(coverage: dict) -> str:
    """S3 的三值判定（详见该条 ``observed`` 旁的注释）."""
    if "rows_without_token_evidence" not in coverage:
        return VERDICT_UNMEASURABLE
    if not coverage.get("quiet_rows"):
        return VERDICT_UNMEASURABLE
    if coverage["rows_without_token_evidence"]:
        return VERDICT_FAIL
    return VERDICT_PASS


def _s3_reason(coverage: dict) -> str:
    """S3 的说明文字，与 :func:`_s3_verdict` 逐态对齐."""
    if "rows_without_token_evidence" not in coverage:
        return "覆盖块整体缺失 ⇒ 本次测量没留下可比对的 token 证据，判「无法测量」（不得判绿）"
    quiet_rows = coverage.get("quiet_rows")
    if not quiet_rows:
        return "没有任何「不开口」行 ⇒ 无适用对象，不判通过"
    missing = coverage["rows_without_token_evidence"]
    if missing:
        return f"{missing} 行不开口决策缺 token 证据 ⇒ 不可归因，判红"
    return "所有不开口决策都有 token 证据"


# --- 负控：故意做错的输入 ---------------------------------------------------

#: 决策 → 该决策在 token 层的典型首 token（构造负控用）。
_TOKEN_FOR_DECISION = {
    "silence": [151669, 151645],
    "response": [151670, 200],
    "not-for-me": [151670, 222, 99507],
    "delegation": [151670, 300],
}


def _relabel(rows: list[dict], decision: str, *, drop_tokens: bool = False) -> list[dict]:
    """把每一行都改成同一个决策（带对应的 token 证据）."""
    out = []
    for row in rows:
        new = {**row, "decision": decision, "ok": True}
        if drop_tokens:
            new.pop("emitted_token_ids", None)
        else:
            new["emitted_token_ids"] = list(_TOKEN_FOR_DECISION[decision])
        out.append(new)
    return out


#: 负控的作用对象是**整份多轮输入**（``[round…]``），不是单轮。
#:
#: ★ 为什么：单轮负控会让「三轮里只有一轮坏掉」这类形态逃过判据
#: （比率取中位 ⇒ 一轮坏可能抬不动中位），而**真实卡片就是多轮的**。
#: 另外「只有一轮」这种退化本身就是一条负控（S5），它只能在轮数这一层构造。
RoundsMutator = Callable[[list[list[dict]]], list[list[dict]]]


def _every_round(fn: Callable[[list[dict]], list[dict]]) -> RoundsMutator:
    """把「改一轮」的函数提升为「改每一轮」."""

    def applied(rounds: list[list[dict]]) -> list[list[dict]]:
        return [fn([dict(row) for row in rows]) for rows in rounds]

    return applied


def _only_first_round(rounds: list[list[dict]]) -> list[list[dict]]:
    """只留一轮 —— ★ 负控「单轮不可作点估计」这条判据本身."""
    return [list(rounds[0])]


def _drop_quiet_tokens(rows: list[dict]) -> list[dict]:
    """把不开口行的 token 证据清成空列表（模拟模型一个 token 都没吐）."""
    return [
        {**row, "emitted_token_ids": []} if row["decision"] in DECISIONS_QUIET else dict(row)
        for row in rows
    ]


def _invert_quiet_tokens(rows: list[dict]) -> list[dict]:
    """把不开口行的 token 证据写成 ``</response>``（151670）—— 解析器与模型不一致."""
    return [
        {**row, "emitted_token_ids": [151670, 200]}
        if row["decision"] in DECISIONS_QUIET
        else dict(row)
        for row in rows
    ]


def _mark_failed(rows: list[dict]) -> list[dict]:
    """把整轮标记为推理失败."""
    return [{**row, "ok": False} for row in rows]


def _drop_group(group: str) -> RoundsMutator:
    """删掉某一组的全部行（把该组的分母清零）.

    ★ 名字是 **drop** 而不是 keep —— 早先写成 ``_keep_only(group)`` 而实现是
    「删掉这一组」，于是两条分母负控**互相搞反**：名为「清空非面向分母」的
    负控实际删掉了面向句，判据判绿而自检报了假失败。名字与语义必须一致，
    否则下一次读到它的人（包括我自己）会用错方向。
    """

    def applied(rounds: list[list[dict]]) -> list[list[dict]]:
        return [[dict(row) for row in rows if row["expected"] != group] for rows in rounds]

    return applied


def _invert_expected(rows: list[dict]) -> list[dict]:
    """把 directed / nondirected 的**期望标签对调**（决策不动）."""
    swap = {GROUP_DIRECTED: GROUP_NONDIRECTED, GROUP_NONDIRECTED: GROUP_DIRECTED}
    return [{**row, "expected": swap.get(row["expected"], row["expected"])} for row in rows]


@dataclass(frozen=True)
class Mutation:
    """一份**故意做错**的输入，及其「必须让哪些判据不能判绿」的声明.

    Attributes
    ----------
        mutation_id: 稳定 id。
        description: 这份输入错在哪 —— 必须能对应到一条**具体**的退化形态。
        apply: ``[round…] -> [round…]``（**整份多轮输入**，见 :data:`RoundsMutator`）。
        must_fail: 必须判 :data:`VERDICT_FAIL` 的判据 id 元组。
        must_not_pass: 必须**不判 PASS**（FAIL 或「无法测量」皆可）的判据 id。
            ★ 用于「分母退化」这一类：正确行为是判「无法测量」而不是判红，
            但它同样**绝不能判绿**。
            ``must_fail`` 与 ``must_not_pass`` 至少有一个非空：一条不期待任何判据
            变红的负控是**装饰**，它会在实现悄悄失效时继续「通过」。
    """

    mutation_id: str
    description: str
    apply: RoundsMutator
    must_fail: tuple[str, ...] = ()
    must_not_pass: tuple[str, ...] = ()
    #: ★ 恒等负控：它**不得**让任何判据变红，作用是把「基线本身就不干净」
    #: 这一类错误从其它负控里分离出来。至少且至多有一个负控是恒等的
    #: （:func:`self_check` 强制），否则「负控证明会判红」这句话就没有对照。
    is_identity: bool = False

    @property
    def covers(self) -> tuple[str, ...]:
        """本负控触及的全部判据 id（用于覆盖完整性检查）."""
        return tuple(dict.fromkeys((*self.must_fail, *self.must_not_pass)))


def _no_op(rounds: list[list[dict]]) -> list[list[dict]]:
    """恒等变换 —— 它必须让**任何判定都不变**（见 :func:`self_check`）."""
    return [list(rows) for rows in rounds]


#: ★ 负控集合。父 spec §七的原文要求是「每条新判据必须配套一份故意做错的输入，
#: 并证明它会判红」。:func:`self_check` 会强制「每条判据至少被一个负控覆盖」，
#: 且会拒绝 ``covers`` 为空的负控。
#:
#: ⚠️ **两条实测结论，写在这里以免后人重犯**（两条都是本模块自检逼出来的）：
#:
#: 1. **代价指数 ``cost_index`` 单独挡不住「永远沉默」。** 在 25 面向 / 26 非面向
#:    这个基率下，「永远沉默」的加权代价只有 ``C_FN×25``，`cost_index` 低得很。
#:    ⇒ 真正挡住那个桩的是 **D2（面向句非响应率）与 D4（泛化召回）** 两条召回下限。
#:    因此 ``always-silence`` 负控**不得**声明 ``D5-cost-index`` 判红；
#:    若哪天它真的判红了，说明代价函数的实现变了 —— 那要重新审，不是直接接受。
#: 2. **``quiet-without-tokens`` 不改任何比率**（决策没变），只该由 token 证据类
#:    判据（S4）抓住。声明它让 D5 判红是错的期待，会让自检产生**假失败**、
#:    进而诱使人去放宽判据 —— 那比漏检更危险。
MUTATIONS: tuple[Mutation, ...] = (
    Mutation(
        mutation_id="identity",
        description=(
            "恒等变换（什么都不改）—— ★ 用于证明「判红」不是来自基线输入本身。"
            "它声明的是**没有任何判据会变红**；:func:`self_check` 会把它与基线逐条比对。"
        ),
        apply=_no_op,
        must_fail=(),
        must_not_pass=(),
        is_identity=True,
    ),
    Mutation(
        mutation_id="always-silence",
        description=(
            "★ 恰好是那个刷过旧判据的桩：**每一轮都判 silence**（带真 token 证据）。"
            "旧判据下它 precision=100%（或无预测则记 0.0）、directed_miss=0% ⇒ 无条件通过。"
            "本判据下它必须在**漏判**上判红。"
        ),
        apply=_every_round(lambda rows: _relabel(rows, "silence")),
        must_fail=("D2-directed-nonresponse", "D4-not-for-me-recall-generalization"),
        must_not_pass=("D3-not-for-me-precision",),
    ),
    Mutation(
        mutation_id="always-not-for-me",
        description="往 not-for-me 倾泻（把每一轮都判成 not-for-me）—— 精确率必须塌掉。",
        apply=_every_round(lambda rows: _relabel(rows, "not-for-me")),
        must_fail=("D2-directed-nonresponse", "D3-not-for-me-precision"),
    ),
    Mutation(
        mutation_id="always-response",
        description="反向桩：每一轮都开口 —— 误响应率必须爆表。",
        apply=_every_round(lambda rows: _relabel(rows, "response")),
        must_fail=(
            "D1-nondirected-no-spurious",
            "D4-not-for-me-recall-generalization",
            "D5-cost-index",
        ),
    ),
    Mutation(
        mutation_id="always-delegation",
        description=(
            "把每一轮都派给后台。★ 这个变异体专打**窄口径**：旧字段 "
            "baseline_mis_response_rate_pct 只数 response ⇒ 对它会报 0.0%，"
            "而它每轮都会触发外部检索 + 播报。"
        ),
        apply=_every_round(lambda rows: _relabel(rows, "delegation")),
        must_fail=(
            "D1-nondirected-no-spurious",
            "D4-not-for-me-recall-generalization",
            "D5-cost-index",
        ),
    ),
    Mutation(
        mutation_id="inverted-labels",
        description="把期望标签对调（决策不动）—— 证明判据真的在读资产的真值，不是装饰。",
        apply=_every_round(_invert_expected),
        must_fail=("D1-nondirected-no-spurious", "D2-directed-nonresponse"),
    ),
    Mutation(
        mutation_id="no-token-evidence",
        description=(
            "把所有行的 token 证据**抹掉**（模拟未请求 logprobs / 旧产物）"
            " —— 沉默与空输出不再可分，必须判红而不是静默照常出数。"
        ),
        apply=_every_round(
            lambda rows: [
                {k: v for k, v in row.items() if k != "emitted_token_ids"} for row in rows
            ]
        ),
        must_fail=("S3-token-evidence-complete",),
    ),
    Mutation(
        mutation_id="quiet-without-tokens",
        description=(
            "把所有「不开口」的 token 证据清成**空列表**（模型一个 token 都没吐）"
            " —— 这正是「空输出」与「判定沉默」在 content 层同形的那一种。"
            "它必须被判红（那是失效输出），而**不是**被判成「沉默判对了」。"
        ),
        apply=_every_round(_drop_quiet_tokens),
        must_fail=("S4-no-unattributed-quiet",),
    ),
    Mutation(
        mutation_id="inverted-token-evidence",
        description=(
            "把每一轮不开口的 token 证据写成 ``</response>``（151670）"
            " —— 解析器说沉默、服务端 token 说它在说话。这是**解析器与模型不一致**，"
            "必须单独判红，不得并入任一正常桶。"
        ),
        apply=_every_round(_invert_quiet_tokens),
        must_fail=("S4-no-unattributed-quiet",),
    ),
    Mutation(
        mutation_id="failed-round",
        description="把每一轮全部标记为推理失败 ⇒ 「没测到」不得被当成「沉默判对了」。",
        apply=_every_round(_mark_failed),
        must_fail=(
            "S2-no-round-errors",
            "D2-directed-nonresponse",
            "D4-not-for-me-recall-generalization",
        ),
    ),
    Mutation(
        mutation_id="single-round",
        description=(
            "★ 只留一轮 ⇒ S5 必须判红。单轮数字本工单已证明不可作点估计"
            "（同一配置三次真机跑的误响应率中位 38.5 → 26.9 → 46.2），"
            "而「能出卡」不等于「结论可判稳」。"
        ),
        apply=_only_first_round,
        must_fail=("S5-multi-round",),
    ),
    Mutation(
        mutation_id="empty-nondirected-denominator",
        description=(
            "删掉所有非面向句 ⇒ 误响应率的分母为 0 ⇒ 该判据必须以「无法测量」收场，"
            "**不得**判绿（这正是空精度事故的形态）。"
        ),
        apply=_drop_group(GROUP_NONDIRECTED),
        must_fail=("S1-denominators-present",),
        must_not_pass=("D1-nondirected-no-spurious",),
    ),
    Mutation(
        mutation_id="empty-directed-denominator",
        description="删掉所有面向句 ⇒ 漏判率的分母为 0 ⇒ 同上，必须不可判绿。",
        apply=_drop_group(GROUP_DIRECTED),
        must_fail=("S1-denominators-present",),
        must_not_pass=("D2-directed-nonresponse",),
    ),
)


def _block(per_round_rows: list[list[dict]], prompt: str) -> dict:
    """多轮行 → 聚合块（自检与负控统一走**多轮**路径）.

    ★ 负控必须跑在多轮上：单轮块会让「三轮里只有一轮坏掉」这类形态逃过判据
    （比率取中位 ⇒ 一轮坏可能抬不动中位）。而真实卡片就是多轮的 ——
    自检跑在与真实路径不同的形状上，等于没测那一步。

    ★ **只有一轮时不做聚合**，直接返回单轮块。理由是 :func:`aggregate_axis_blocks`
      刻意拒绝单轮（单轮给不出离散度，它会 fail loud）—— 而「单轮」本身是一条
      待判定的输入形态，不能让它变成异常。单轮块照样能被全部判据读取
      （:func:`decision_eval_axis.median_view` 对标量与聚合 dict 一视同仁），S5 会在那里判红。
      这也让「单轮」这条负控与真实路径用**同一套**判据，而不是另写一份。
    """
    blocks = [axis_block(rows, prompt) for rows in per_round_rows]
    if len(blocks) == 1:
        block = dict(blocks[0])
        block["rounds"] = {
            "rounds_count": 1,
            "n_directed_per_round": [block["overall"].get("n_directed")],
            "n_nondirected_per_round": [block["overall"].get("n_nondirected")],
        }
        return block
    return aggregate_axis_blocks(blocks)


def _verdict_map(block: dict) -> dict[str, str]:
    """一次算完指标判据 + 结构性判据的判定（**走卡片那条实现**，不再另写一份）.

    ★ 刻意转发到 :func:`decision_eval_card.criteria_verdicts`：早先这里有一份
    平行实现（只跑 criteria 自带的三条结构性判据），于是自检**看不见**卡片里
    另外两条与多轮有关的判据（S4/S5）。两份实现意味着自检可能在一个与真实
    出卡路径不同的世界里「通过」—— 这正是本仓反复记的那类缺陷。
    """
    import decision_eval_card as card_mod  # 延迟 import：避免模块级环形依赖

    return {item["criterion_id"]: item["verdict"] for item in card_mod.criteria_verdicts(block)}


def self_check() -> int:
    """★ 可证伪性自检：证明每条判据**真的会判红**，而不是恒真.

    做五件事，并把两侧读数都打出来：

    1. **无负控的判据 ⇒ 失败**（禁止恒真判据、禁止装饰性负控）；
    2. **恒等变换（null mutation）必须让任何判定都不变**
       —— 否则「变红」来自基线输入本身，负控证明不了什么；
    3. 每个负控都必须让它 ``must_fail`` 里的每条判据**判 FAIL**；
    4. 每个负控都必须让它 ``must_not_pass`` 里的每条判据**不判 PASS**
       （FAIL 或「无法测量」皆可 —— 分母退化时正确行为是拒给结论）；
    5. ★ **被负控打红的判据清单必须与卡片里全部判据集合一致**
       —— 守「新增判据即必须配负控」。只查 :data:`CRITERIA` 与
       :data:`STRUCTURAL_CRITERIA` 会漏掉卡片自己补的 S4/S5。

    Returns
    -------
        ``0`` 通过 / ``1`` 不通过。刻意可被替换（见模块测试的变异测试），
        因为「自检通过」本身也必须能被证伪。
    """
    import decision_eval_card as card_mod  # 延迟 import：避免模块级环形依赖

    baseline_rounds = [card_mod.synthetic_findings()["rows"] for _ in range(3)]
    baseline_verdicts = _verdict_map(_block(baseline_rounds, card_mod.synthetic_prompt()))

    problems: list[str] = []

    # 1 & 5. 覆盖完整性：以**卡片实际产出的判据集合**为准。
    card_criteria_ids = {item["criterion_id"] for item in card_mod.criteria_registry()}
    covered = {cid for mutation in MUTATIONS for cid in mutation.covers}
    # ★ S5 由 ``single-round`` 负控覆盖；恒等负控不计入覆盖。
    for criterion_id in sorted(card_criteria_ids - covered):
        problems.append(f"{criterion_id} 没有任何负控声明它必须判红 / 不得判绿 ⇒ 它可能是恒真的")

    # 2. 负控本身不得是装饰，且**恰好一个**是恒等对照。
    for mutation in MUTATIONS:
        if not mutation.covers and not mutation.is_identity:
            problems.append(
                f"负控 {mutation.mutation_id} 既没有 must_fail 也没有 must_not_pass，"
                "又不是显式标注的恒等对照 ⇒ 装饰性负控"
            )
    identities = [m for m in MUTATIONS if m.is_identity]
    if len(identities) != 1:
        problems.append(
            f"恒等对照负控有 {len(identities)} 个（应为恰好 1 个）—— "
            "没有它，「负控会让判据变红」这句话就没有对照；多于一个则说明分工不清"
        )

    # 3. 恒等变换必须什么都不改。
    identity_verdicts = _verdict_map(
        _block([list(r) for r in baseline_rounds], card_mod.synthetic_prompt())
    )
    if identity_verdicts != baseline_verdicts:
        problems.append(
            "恒等变换改变了判定 ⇒ 后面的「变红」可能来自基线输入本身，负控证明不了任何事"
        )

    # 4. 每个负控都必须真的打红它声明的那几条（并不得漏判必须「不判绿」的那几条）。
    #    ★ 负控施加在**每一轮**上：单轮变异会让「三轮里只有一轮坏掉」这种形态
    #    逃过判据（比率取中位 ⇒ 一轮坏掉可能抬不动中位）。多轮一致的负控才是
    #    判据真正要挡的那种退化。
    print("=== 定向轴判据的负控自检 (#157) ===")
    for mutation in MUTATIONS:
        mutated_rounds = mutation.apply([list(rows) for rows in baseline_rounds])
        verdicts = _verdict_map(_block(mutated_rounds, card_mod.synthetic_prompt()))
        fired = sorted(k for k, v in verdicts.items() if v == VERDICT_FAIL)
        if mutation.is_identity:
            print(f"  {mutation.mutation_id:24s} 判红={fired}（应为空）")
            if fired:
                problems.append(
                    f"恒等对照负控让 {fired} 判红了 ⇒ 基线输入本身就不干净，"
                    "其它负控的「变红」证明不了任何事"
                )
            continue
        print(f"  {mutation.mutation_id:24s} 判红={fired}")
        missing = [cid for cid in mutation.must_fail if verdicts.get(cid) != VERDICT_FAIL]
        if missing:
            problems.append(f"负控 {mutation.mutation_id} 没能让 {missing} 判红")
        still_green = [cid for cid in mutation.must_not_pass if verdicts.get(cid) == VERDICT_PASS]
        if still_green:
            problems.append(
                f"负控 {mutation.mutation_id} 让 {still_green} **判绿了** —— "
                "分母退化 / 空输出这类情形不得判绿（FAIL 或「无法测量」才对）"
            )

    if problems:
        print("  verdict: FAIL")
        for problem in problems:
            print(f"    - {problem}")
        return 1
    print(
        f"  verdict: PASS（卡片全部 {len(card_criteria_ids)} 条判据各有负控；"
        f"{len(MUTATIONS)} 个故意做错的输入全部按声明判红或「无法测量」）"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI：默认跑负控自检（用于验证判据仍可证伪）."""
    parser = argparse.ArgumentParser(description="定向轴判据的负控自检（工单 #157）")
    parser.add_argument("--self-check", action="store_true", help="跑负控自检（默认）")
    parser.add_argument("--list", action="store_true", help="列出判据与负控，不跑")
    args = parser.parse_args(argv)

    if args.list:
        print("判据：")
        for criterion in CRITERIA:
            print(
                f"  {criterion.criterion_id:42s} {criterion.metric} "
                f"{'<=' if criterion.direction == 'upper' else '>='} {criterion.threshold}"
            )
        print("结构性判据：")
        for cid in STRUCTURAL_CRITERIA:
            print(f"  {cid}")
        print("负控：")
        for mutation in MUTATIONS:
            print(
                f"  {mutation.mutation_id:24s} → 必须判红 {list(mutation.must_fail)}"
                f" / 不得判绿 {list(mutation.must_not_pass)}"
            )
        return 0

    return self_check()


if __name__ == "__main__":
    raise SystemExit(main())
