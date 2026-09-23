# ruff: noqa: RUF001, RUF002, RUF003
# (RUF001/002/003 = ambiguous fullwidth punctuation; this module's prose is
# Chinese. Same established repo convention as decision_eval_criteria.py.)
"""时序轴的**可证伪判据** + 配套负控（工单 #158，父 spec #154 §六/§七）.

为什么时序轴的判据不能只是「拿定向轴那套改改」
-----------------------------------------------
定向轴的判据回答「该不该说」，它们的阈值出自**入库的真机跑分产物**
（``decision_eval_card`` 的冻结快照）。时序轴面对的是**另一个数据源**（事件流），
于是它有两条定向轴没有的失效模式，各需一条专门判据：

1. ★ **耗时可能来自错误的钟。** 帧上的 ``ts_ms`` 是采集端墙钟；
   拿它当「模型反应慢」的证据会把网络与编码抖动算进模型头上。
   一条只判「有没有数」的判据**抓不到这件事** —— 数一定有，只是归因是错的。
   故 :data:`T_LATENCY_SOURCE` 直接判**出处**。
2. ★ **删掉打点后可能静默退回出数。** 这是本票 ★ 负控的字面要求：
   「删掉轮次打点 → 时序轴**判红**（而不是退回用帧时间戳、静默出数）」。
   故 :data:`T_ONSET_MEASURED` 在缺打点时判**红**（不是「无法测量」）——
   因为**本该有打点**。缺打点是接线的缺陷，不是「这一项不适用」。

★ 三种状态，与定向轴同一套词汇
-------------------------------
``PASS`` / ``FAIL`` / ``UNMEASURABLE``。第三种是第一等公民：分母为 0 时
判「无法测量」，**不判通过也不判 0 分**。区别在于：
「本该有数据而缺」判 **FAIL**（缺陷），「这项在本次输入上无从测量」判
**UNMEASURABLE**（不是缺陷，但也绝不能是绿）。

★ 阈值的出处（本仓硬约束：阈值必须是有出处的数字）
---------------------------------------------------
时序轴目前**没有**可用的真机基线（真机事件流里 ``latency_ms`` 只有 4 个非零样本，
而 ``user_still_speaking_at_decision`` **从未被写过**）。故本模块**不发明阈值**：

* 有真机出处的项 → 从 :data:`TIMING_BASELINE_SNAPSHOT` 派生（可复算，可核验）；
* 没有出处的项 → 判据**不带阈值**，只判「这份测量是否成立」
  （出处/打点/样本量），并把「缺一条有出处的线」这件事显式说出来。

把一条拍脑袋的阈值伪装成「派生」正是本票要消灭的那类谎。

Run tests: cd services/webinfer && python -m pytest tests/test_decision_eval_timing_criteria.py -q
Self-check: python -m decision_eval_timing_criteria --self-check
"""

from __future__ import annotations

import argparse
import math
import re
import statistics
from collections.abc import Callable
from dataclasses import dataclass

from decision_eval_timing import (
    FORBIDDEN_COMBINED_KEYS,
    LATENCY_SOURCE_CHAIN_STAMP,
    MIN_SPEAKING_ROUNDS,
    forbidden_combined_keys,
    timing_block,
)

# --- 判定状态（与定向轴同一套词汇，便于两轴并排读）---------------------------

VERDICT_PASS = "pass"
VERDICT_FAIL = "fail"
#: ★ 「测不了」既不是通过也不是失败（#162 已把这条纪律钉进运行器）。
VERDICT_UNMEASURABLE = "unmeasurable"

VERDICTS: tuple[str, ...] = (VERDICT_PASS, VERDICT_FAIL, VERDICT_UNMEASURABLE)

# --- 阈值出处：冻结快照（不是活产物）-----------------------------------------

#: 阈值来源的那一次读数的**事件文件**（人读用；权威绑定靠 sha256）。
BASELINE_EVENTS = "logs/events/webui-2026-09-22.jsonl"

#: ★ 冻结基线快照：**唯一**可从其中派生时序阈值的来源.
#:
#: ``latency_ms`` 是**决策链路自身轮次打点**的读数（#156 新增），
#: 抄自上面那份事件文件里 4 个非零样本。**不**从活文件派生 —— 与定向轴
#: 同一个理由（``decision_eval_criteria`` 的模块注释）：门禁要挡质量退化，
#: 而退化若伴随一次重跑，阈值会跟着退化一起动，那条线就永远拦不住东西。
#:
#: ⚠️ **这份快照的诚实限定（不得省略）**：只有 **4 个**样本，分位数在这里
#: 几乎是噪声。故它只用来定一条**宽松的上界**（挡住「慢到不可接受」这类明显
#: 退化），并显式标注为**保守取值而非基线水平**。
TIMING_BASELINE_SNAPSHOT: dict = {
    "events": BASELINE_EVENTS,
    "recorded_at": "2026-09-23",
    "model": "joyai-vl-interaction-preview",
    "latency_source": LATENCY_SOURCE_CHAIN_STAMP,
    "sample_count": 4,
    "latency_ms": [282, 547, 781, 891],
    "sessions": ["probe160", "p2", "f61b9db3-c9d0-4af3-9b10-d386b082e388"],
    "limitation": (
        "只有 4 个非零样本，且来自 3 个会话（含 probe 会话）。"
        "分位数在此样本量下几乎是噪声 ⇒ 本快照只支持一条**宽松上界**，"
        "不声称等于基线水平。样本不足这件事本身由判据 T_SAMPLE_FLOOR 判出来。"
    ),
}

#: 派生规则的人读说明。
BOUND_DERIVATION = (
    "阈值 = ceil(median + pstdev)，取自 TIMING_BASELINE_SNAPSHOT.latency_ms"
    "（冻结快照，**不**随活事件文件漂移）。"
    "用「中位 + 离散度」而非中位：单轮不可作点估计（#157 已为此付过一次费）。"
)

#: 派生出的阈值（每项都能被 :func:`derive_bounds` 从快照重算）。
#:
#: ★ 这两个数是**算出来的**，不是拍的：``--verify-bounds`` 会从冻结快照重算并
#: 逐项比对，不一致即报错。初版手写 890/1130 与重算值 898/1120 不符 ——
#: 那正是「同一个事实写两遍必然分叉」，而分叉的后果是门禁的线与它的证据脱钩。
TIMING_BOUNDS: dict[str, float] = {
    # onset 延迟中位数上界 = ceil(median + pstdev) = ceil(664 + 233.89)
    "onset_median_ms": 898.0,
    # onset p90 上界 = ceil(max × 1.25 / 10) × 10 = ceil(891×1.25/10)×10
    "onset_p90_ms": 1120.0,
}

# --- 判据 --------------------------------------------------------------------


@dataclass(frozen=True)
class TimingCriterion:
    """一条时序轴判据：一个读法 + 一个方向 + 出处 + 它读哪个块.

    Attributes
    ----------
        criterion_id: 稳定 id（负控与结果文件都用它引用）。
        statement_template: 人读的一句话，**必须含 ``{threshold}`` 占位符**
            —— 阈值由渲染时插值，不写死在模板里（#157 的教训：数字写两遍必然分叉）。
        context_percent: 模板里**合法出现**的其它数字。
        metric: 读 ``metrics`` 里的哪个键；``None`` ⇒ 本判据读的是块结构而不是指标。
        direction: ``"upper"``（越小越好）/ ``"lower"``（越大越好）/
            ``None``（无阈值，只判测量是否成立）。
        threshold: 阈值，取自 :data:`TIMING_BOUNDS`；``None`` ⇒ 本判据无阈值。
        source: 阈值出处（须可追溯、可复算，或**明说它是保守取值**）。
        kind: ``"metric"`` / ``"structural"`` —— 结构性判据的负控是「删掉证据」
            而不是「改指标」（与定向轴同一划分）。
    """

    criterion_id: str
    statement_template: str
    metric: str | None
    direction: str | None
    threshold: float | None
    source: str
    kind: str = "metric"
    context_percent: tuple[float, ...] = ()
    #: 结构性判据的判定函数：块 → ``(verdict, reason)``。无阈值判据必须提供它。
    judge: Callable[[dict], tuple[str, str]] | None = None
    #: ★ 嵌套指标块（onset）里要读的**具体统计量**。
    #:
    #: 曾经这里是猜的（``"median" if "median" in value else "p90"``）—— 于是
    #: ``T_ONSET_P90`` 实际读到的是 **median**，两个判据量同一个数，
    #: 而其中一个的文本写着「p90」。它之所以没被立刻发现，是因为中位数那个阈值
    #: 更宽松、p90 判据恰好在别处判「无法测量」掩盖了它。
    #: ⇒ 显式声明，不猜。
    statistic: str | None = None

    @property
    def statement(self) -> str:
        """渲染后的判据文本 —— 阈值由模板占位符插值而来（一个数渲两次）.

        ★ 与定向轴同一条纪律（``Criterion.statement``）：阈值写死在文本里会与
        ``threshold`` 分叉，而 ``statement`` 会随卡片落进 JSON、**正是门禁作者
        照抄的那句话**。无阈值判据的模板不含占位符，此处原样返回。
        """
        if self.threshold is None:
            return self.statement_template
        return self.statement_template.format(threshold=f"{self.threshold:.0f}")

    def evaluate(self, block: dict) -> dict:
        """对一张时序块作出 PASS / FAIL / 无法测量 的判定."""
        base = {
            "criterion_id": self.criterion_id,
            "statement": self.statement,
            "axis": "timing",
            "metric": self.metric,
            "direction": self.direction,
            "threshold": self.threshold,
            "source": self.source,
            "kind": self.kind,
        }
        if self.judge is not None:
            verdict, reason = self.judge(block)
            # ★ 结构性判据**不**经 ``_observed`` 读指标：它们的输入是块结构
            #   （出处块 / 缺口计数 / 样本量），而不是一条指标。让它们走指标读取
            #   会要求每条都声明一个 statistic，而那对「读缺口计数」的判据毫无意义。
            return {
                **base,
                "observed": self.judge_observed(block),
                "verdict": verdict,
                "reason": reason,
            }

        observed = self._observed(block)
        if observed is None:
            return {
                **base,
                "observed": None,
                "verdict": VERDICT_UNMEASURABLE,
                "reason": (
                    f"{self.metric} 在本次输入上算不出来（无样本/无时间基准）⇒ 判「无法测量」。"
                    "「没测」既不是通过也不是 0 分。"
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

    def judge_observed(self, block: dict) -> object:
        """结构性判据的「观测到什么」块：**原始证据**，不是某一个统计量.

        ★ 刻意给结构性判据一个**不同的**观测视图：它们的判定依据是块结构
        （出处、缺口计数、样本量、时间基准），把这些原始值原样放进 ``observed``，
        读者才能复核判定 —— 只给一个 ``median`` 会让「为什么这条判红」无从追问。
        """
        metrics = block.get("metrics") or {}
        provenance = block.get("latency_source_block") or {}
        return {
            "latency_source": provenance.get("source"),
            "latency_source_legal": provenance.get("legal"),
            "n_speaking": metrics.get("n_speaking"),
            "n_missing_stamp": (metrics.get("onset_latency_ms") or {}).get("n_missing_stamp"),
            "onset_samples": (metrics.get("onset_latency_ms") or {}).get("n"),
            "session_seconds": metrics.get("session_seconds"),
            "premature_scope": metrics.get("premature_scope"),
            "forbidden_keys_found": forbidden_combined_keys(
                {k: v for k, v in block.items() if k != "forbidden_combined_keys"}
            ),
        }

    def _observed(self, block: dict) -> object:
        """从块里读本判据的读数（``None`` = 未测）."""
        metrics = block.get("metrics") or {}
        if self.metric is None:
            return None
        value = metrics.get(self.metric)
        if isinstance(value, dict):
            # onset 这类「样本 + 统计量」的嵌套块：读**显式声明**的那一个统计量。
            if self.statistic is None:
                raise ValueError(
                    f"{self.criterion_id} 读的是嵌套指标块 {self.metric!r}，"
                    "但没有声明 statistic —— 猜统计量会让两个判据量同一个数"
                )
            return value.get(self.statistic)
        return value


# --- 结构性判据的判定函数 ----------------------------------------------------


def _judge_latency_source(block: dict) -> tuple[str, str]:
    """★ T_LATENCY_SOURCE：耗时**必须**出自决策链路的轮次打点.

    这是本票正文「★ 耗时**必须**来自**决策链路自身的打点**」的可执行形态。
    用帧时间戳或 ``ts`` 差值 ⇒ **判红**（不是「无法测量」：那两种来源会产出
    一个看起来正常的数字，只是归因是错的 —— 而错误的归因比缺失更坏）。
    """
    provenance = block.get("latency_source_block") or {}
    if provenance.get("legal"):
        return VERDICT_PASS, (
            f"耗时出自 {provenance.get('origin')}；"
            f"被明令禁止的来源 {provenance.get('forbidden')} 均未被使用"
        )
    return VERDICT_FAIL, (
        f"★ 耗时出处是 {provenance.get('source')!r}，不是决策链路的轮次打点 ⇒ 判红。"
        "帧的 ts_ms 是采集端墙钟、事件 ts 是发射端墙钟，用它们会把链路外的抖动"
        "误归因成「模型反应慢」—— 本票正文点名的错误归因。"
    )


def _judge_onset_stamped(block: dict) -> tuple[str, str]:
    """★ T_ONSET_MEASURED：每一次开口都必须带**轮次打点**（否则判红）.

    这就是本票 ★ 负控的落点：**删掉轮次打点 → 时序轴判红**，
    而不是退回用帧时间戳、静默出数。

    ★ 为什么是 FAIL 而不是 UNMEASURABLE
    -----------------------------------
    缺打点意味着 ``live_mode._turn_started_at`` 的接线断了（或事件来自 #156
    之前的版本）—— 那是**本该有而不有**，是缺陷。判「无法测量」会让它看起来
    像「这项在本次输入上不适用」，而那正是 fail-open 的形态。

    ★ **一处必须与之区分的情形：一次开口都没有。**
    那时 onset 确实无从测起，但**没有任何接线缺陷的证据** —— 判红会给出一个
    指错方向的指控，而错误的告警会训练读者忽略真正的告警。故那种情形判
    「无法测量」。两种情形的差别是「本该有而不有」与「本就没有」。
    """
    metrics = block.get("metrics") or {}
    onset = metrics.get("onset_latency_ms") or {}
    missing = onset.get("n_missing_stamp") or 0
    if not metrics.get("n_speaking"):
        return VERDICT_UNMEASURABLE, (
            "本次输入里没有任何开口轮 ⇒ onset 无适用对象，判「无法测量」；"
            "★ 这与「有开口但缺打点」不同 —— 后者才是接线缺陷（判红）"
        )
    if not onset.get("n"):
        return VERDICT_FAIL, (
            "★ 有开口轮，但**没有一次**带轮次打点 ⇒ 时序轴判红"
            "（缺打点等于没测；不得退回用帧时间戳或 ts 差值补数）"
        )
    if missing:
        return VERDICT_FAIL, (
            f"★ {missing} 次开口缺轮次打点（{onset.get('missing_stamp_ids')}）⇒ 判红："
            "轮次打点本该有而不有，是接线缺陷，不是「不适用」"
        )
    return VERDICT_PASS, f"{onset['n']} 次开口全部带决策链路的轮次打点"


def _judge_no_combined_accuracy(block: dict) -> tuple[str, str]:
    """★ T_NO_COMBINED_ACCURACY：**禁止**把两轴合成单一 accuracy.

    父 spec §三明确否决合成（两类错误代价不对称，合成会抹平它）。
    ★ 一条留在文档里的禁令不是防线 —— 这里把它变成**可判红**的扫描，
    于是「合成」这件事**不可表示**，而不是「可以被检测」。
    """
    found = forbidden_combined_keys({k: v for k, v in block.items() if k != "forbidden"})
    if found:
        return VERDICT_FAIL, (
            f"★ 块里出现禁止的合并分数键 {found} ⇒ 判红：父 spec 明确否决单一 accuracy"
            "（它在两类错误上等权，而本项目两类错误代价明确不对称）"
        )
    return VERDICT_PASS, (f"未出现任何合并分数键（扫描表 {list(FORBIDDEN_COMBINED_KEYS)}）")


def _judge_sample_floor(block: dict) -> tuple[str, str]:
    """T_SAMPLE_FLOOR：开口样本量必须够，否则 onset 分位数不作结论.

    ★ 与 :func:`decision_eval_timing.self_check` 的最小样本是同一条线
    （:data:`MIN_SPEAKING_ROUNDS`）：一处定义，两处引用。
    """
    onset = (block.get("metrics") or {}).get("onset_latency_ms") or {}
    n = onset.get("n") or 0
    if n >= MIN_SPEAKING_ROUNDS:
        return VERDICT_PASS, f"开口样本 {n} ≥ {MIN_SPEAKING_ROUNDS}，分位数可作点估计"
    return VERDICT_UNMEASURABLE, (
        f"开口样本只有 {n} < {MIN_SPEAKING_ROUNDS} ⇒ 中位/p90 与「同一配置的偶然抖动」"
        "不可区分，判「无法测量」而非判绿"
    )


def _judge_spurious_timebase(block: dict) -> tuple[str, str]:
    """T_SPURIOUS_TIMEBASE：每秒误触发必须有**真实的时间基准**.

    ``ts`` 是本模块里唯一合法的时间基准（它只是**速率的分母**，不参与任何耗时）。
    没有基准 ⇒ 判「无法测量」：一个 0 的速率会被读成「一次都没乱插」，
    而真相往往是「这段会话的时间跨度不可知」。
    """
    metrics = block.get("metrics") or {}
    seconds = metrics.get("session_seconds")
    if not seconds:
        return VERDICT_UNMEASURABLE, (
            "会话时间跨度不可用（ts 缺失或只有单点）⇒ 每秒误触发算不出来，判「无法测量」；"
            "0.0 会被读成「一次都没乱插」"
        )
    return VERDICT_PASS, (f"时间基准 {seconds}s（ts 的**唯一**合法用途：速率分母，不参与任何耗时）")


def _judge_premature_labeled(block: dict) -> tuple[str, str]:
    """T_PREMATURE_MEASURED：premature rate 的标注必须完整.

    ★ 这条判据在当前**真机链路上必然判「无法测量」** —— 因为
    ``user_still_speaking_at_decision`` 从未被写入侧写过（见
    :mod:`decision_eval_timing_sources`）。本判据的价值不是「现在能判绿」，
    而是**让这个缺口不能被静默跳过**：把它读成 0.0 的抢话率会让一个
    从没测过的量看起来完全健康。

    ★ 判「无法测量」而不是判红：这不是被测对象的缺陷，是**还没接线**。
    但缺口的性质（未标注 vs 不适用）由读数里的两个计数分开说清。
    """
    scope = (block.get("metrics") or {}).get("premature_scope") or {}
    if not scope.get("n_speaking"):
        return VERDICT_UNMEASURABLE, "本次输入没有任何开口 ⇒ premature 无适用对象"
    if scope.get("n_unlabeled"):
        return VERDICT_UNMEASURABLE, (
            f"{scope['n_unlabeled']} 次开口未标注「决策时用户是否仍在说话」"
            f"（{scope.get('unlabeled_ids')}）⇒ premature rate 不可测。"
            "真机链路上该字段**从未被写入**，故这是接线缺口而非模型表现；"
            "把它读成 0.0 会把一个从没测过的量说成健康。"
        )
    return VERDICT_PASS, (
        f"premature 分母完整（{scope['n_labeled']} 次开口有标注，"
        f"其中主动轮 {scope['n_not_applicable_proactive']} 次不适用已单列）"
    )


#: 全部判据。★ 每条都在 :data:`MUTATIONS` 里至少有一个「必须让它不能判绿」的负控
#: （由 :func:`self_check` 强制，缺失即报错）。
TIMING_CRITERIA: tuple[TimingCriterion, ...] = (
    TimingCriterion(
        criterion_id="T_LATENCY_SOURCE",
        statement_template=(
            "★ onset 耗时必须出自**决策链路自身的轮次打点**（latency_ms）；"
            "用帧的采集端 ts_ms 或事件 ts 差值即判红"
            " —— 后两者会把网络与编码抖动误归因成「模型反应慢」。"
        ),
        metric="latency_source",
        direction=None,
        threshold=None,
        source=(
            "出处规则出自工单 #158 正文（★ 段）与 #156 的写入侧实现"
            "（live_llm.resolve_latency_ms）；**无阈值** —— 这是一条出处判据，"
            "不是一条计量判据"
        ),
        kind="structural",
        judge=_judge_latency_source,
    ),
    TimingCriterion(
        criterion_id="T_ONSET_MEASURED",
        statement_template=(
            "★ 每一次开口都必须带轮次打点 —— 删掉打点即判红（**不得**退回用帧时间戳、静默出数）。"
        ),
        metric="onset_latency_ms",
        direction=None,
        threshold=None,
        source=("工单 #158 的 ★ 负控原文（「删掉轮次打点 → 时序轴判红」）；**无阈值**"),
        kind="structural",
        judge=_judge_onset_stamped,
    ),
    TimingCriterion(
        criterion_id="T_NO_COMBINED_ACCURACY",
        statement_template=(
            "★ 禁止把定向轴与时序轴合成单一 accuracy（含 f1 / combined / overall score）"
            " —— 父 spec §三明确否决：合成会抹平「误响应更贵」这条不对称。"
        ),
        metric=None,
        direction=None,
        threshold=None,
        source="父 spec #154 §三「两条正交轴，不合成单一 accuracy」；**无阈值**",
        kind="structural",
        judge=_judge_no_combined_accuracy,
    ),
    TimingCriterion(
        criterion_id="T_SPURIOUS_TIMEBASE",
        statement_template=(
            "每秒误触发次数必须有真实的时间基准（ts 跨度）—— 没有基准时给「无法测量」，"
            "不得给 0.0（0.0 会被读成「一次都没乱插」）。"
        ),
        metric="spurious_triggers_per_second",
        direction=None,
        threshold=None,
        source="工单 #158 正文（误触发频率是「铁驭最直接能感受到的量」）；**无阈值**",
        kind="structural",
        judge=_judge_spurious_timebase,
    ),
    TimingCriterion(
        criterion_id="T_PREMATURE_MEASURED",
        statement_template=("premature rate 的标注必须完整 —— 缺标注给「无法测量」，不得给 0.0。"),
        metric="premature_rate_pct",
        direction=None,
        threshold=None,
        source=(
            "工单 #158 正文（premature rate）；★ 真机链路当前不记录该事实 ⇒ "
            "本判据现在必然判「无法测量」，那是**如实读数**而非缺陷；**无阈值**"
        ),
        kind="structural",
        judge=_judge_premature_labeled,
    ),
    TimingCriterion(
        criterion_id="T_SAMPLE_FLOOR",
        statement_template=(
            f"开口样本量必须 ≥ {MIN_SPEAKING_ROUNDS} 才能把 onset 分位数当结论"
            " —— 低于它判「无法测量」（样本不足无法自证准不准）。"
        ),
        metric="onset_latency_ms",
        direction=None,
        threshold=None,
        source=(
            f"MIN_SPEAKING_ROUNDS={MIN_SPEAKING_ROUNDS} 是**工程取值**"
            "（时序轴目前没有可支撑分位数的真机样本，见 TIMING_BASELINE_SNAPSHOT "
            "的 limitation）；**无阈值**，且本票不假装它出自实测"
        ),
        kind="structural",
        judge=_judge_sample_floor,
    ),
    TimingCriterion(
        criterion_id="T_ONSET_MEDIAN",
        statement_template=(
            "onset 延迟中位数不得超过 {threshold} ms"
            "（基线最差会话的 ceil(median+pstdev)；样本仅 4 例，属**宽松上界**）。"
        ),
        metric="onset_latency_ms",
        direction="upper",
        threshold=TIMING_BOUNDS["onset_median_ms"],
        source=f"{BOUND_DERIVATION}（保守上界，非基线水平）",
        statistic="median",
    ),
    TimingCriterion(
        criterion_id="T_ONSET_P90",
        statement_template=("onset 延迟 p90 不得超过 {threshold} ms（同上，宽松上界）。"),
        metric="onset_latency_ms",
        direction="upper",
        threshold=TIMING_BOUNDS["onset_p90_ms"],
        source=f"{BOUND_DERIVATION}（保守上界，非基线水平）",
        statistic="p90",
    ),
)


def derive_bounds(snapshot: dict | None = None) -> dict[str, float]:
    """★ 从**冻结基线快照**重算两个 onset 阈值（**不**从活事件文件派生）.

    取法：``ceil(median + pstdev)``。用「中位 + 离散度」而非中位本身：
    单轮/小样本不可作点估计（#157 已为此付过一次费）。

    ⚠️ 不从当前事件文件派生 —— 那是循环的：门禁要挡质量退化，而退化若伴随
    一次重跑，阈值会跟着退化一起动，那条线便永远拦不住东西。

    ★ **中位数阈值与 p90 阈值分别由哪一批数派生，必须写清楚**：
    两部分都取自同一个 4 样本序列，但 ``ceil(median+pstdev)`` 对中位数是一个
    上界，对 p90 **不是**（p90 ≥ median 恒成立）。故 p90 的阈值不是从
    ``ceil(median+pstdev)`` 得来的，而是从**样本的 max 再留一档**得来的 ——
    这条区分写在 :func:`explain_bounds` 里，两项各说各的出处。
    """
    data = TIMING_BASELINE_SNAPSHOT if snapshot is None else snapshot
    values = [float(v) for v in data["latency_ms"] if v]
    if not values:
        raise ValueError("冻结快照里没有 latency_ms 样本 —— 阈值无从派生")
    spread = math.ceil(statistics.median(values) + statistics.pstdev(values))
    return {
        "onset_median_ms": float(spread),
        # ★ p90 的阈值 = max × 1.25 再向上取整到十位：样本只有 4 个，
        #   p90 用样本分位数估出来的上界比 max 还小（p90(4 样本) < max），
        #   那会让判据在基线自己的样本上就判红 —— 一条出厂即红的线。
        "onset_p90_ms": float(math.ceil(max(values) * 1.25 / 10.0) * 10),
    }


def explain_bounds(snapshot: dict | None = None) -> list[dict]:
    """逐条说明阈值：声明值 / 重算值 / 一致与否 / 出处."""
    derived = derive_bounds(snapshot)
    rows = []
    for key, declared in TIMING_BOUNDS.items():
        recomputed = derived.get(key)
        rows.append(
            {
                "bound": key,
                "declared": declared,
                "recomputed_from_snapshot": recomputed,
                "matches": recomputed == declared,
                "source": (
                    "ceil(median + pstdev)，取自 TIMING_BASELINE_SNAPSHOT.latency_ms"
                    if key == "onset_median_ms"
                    else "ceil(max × 1.25 / 10) × 10，取自同一冻结快照"
                    "（p90 不能用 ceil(median+pstdev) —— 那个量对 p90 不是上界）"
                ),
                "limitation": TIMING_BASELINE_SNAPSHOT["limitation"],
            }
        )
    return rows


def verify_bounds(snapshot: dict | None = None) -> tuple[bool, list[dict]]:
    """核验「声明的阈值 == 从冻结快照重算的阈值」。不一致即报错（返回 False）."""
    rows = explain_bounds(snapshot)
    return all(row["matches"] for row in rows), rows


# --- statements 与 threshold 的分叉守卫（#157 的教训，此处一并带上）---------

_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")


def statement_percentages(criterion: TimingCriterion) -> set[float]:
    """抽出 ``statement`` 里所有百分数（**不含**由占位符插值进来的那一个）."""
    if criterion.threshold is None:
        return {float(m) for m in _PERCENT_RE.findall(criterion.statement_template)}
    rendered = criterion.statement.replace(f"{criterion.threshold:.0f}%", "\x00T\x00", 1)
    return {float(m) for m in _PERCENT_RE.findall(rendered)}


def statements_match_bounds() -> list[str]:
    """★ 自检用：``statement`` 里的数字不得与 ``threshold`` 分叉.

    ★ 为什么不是「阈值是否作为子串出现」那种检查
    -------------------------------------------
    #157 的对抗性复核用一句「不得超过 99%…（上一基线为 63%）」绕过了子串守卫
    （``threshold`` 是 63，「63」确实出现在句子里）。**子串匹配永远可以被
    「正确数字恰好出现在别处」骗过。** 故这里的规矩是同一条：
    带阈值的判据**必须**含 ``{threshold}`` 占位符（阈值一个数渲两次，分叉
    **不可表示**）；句子里其它百分数必须逐个声明进 ``context_percent``。
    """
    problems: list[str] = []
    for criterion in TIMING_CRITERIA:
        if criterion.threshold is None:
            continue
        if "{threshold}" not in criterion.statement_template:
            problems.append(
                f"{criterion.criterion_id}: statement_template 里没有 {{threshold}} 占位符 "
                "⇒ 阈值会被写死在文本里，与 threshold 分叉"
            )
            continue
        extra = statement_percentages(criterion) - set(criterion.context_percent)
        if extra:
            problems.append(
                f"{criterion.criterion_id}: statement 里出现未声明的百分数 {sorted(extra)}"
                f"（threshold={criterion.threshold}）—— 作者会照抄这句话，"
                "两处数字分叉就是缺陷"
            )
    return problems


def statements_mention_axis_separation() -> list[str]:
    """两轴分列这件事必须在**判据文本**里可见（不只是在本模块的代码里）."""
    problems: list[str] = []
    ids = {c.criterion_id for c in TIMING_CRITERIA}
    if "T_NO_COMBINED_ACCURACY" not in ids:
        problems.append("缺少 T_NO_COMBINED_ACCURACY ⇒ 两轴不合成这条约束没有判据承载")
        return problems
    criterion = next(c for c in TIMING_CRITERIA if c.criterion_id == "T_NO_COMBINED_ACCURACY")
    if "accuracy" not in criterion.statement:
        problems.append("T_NO_COMBINED_ACCURACY 的文本里没有提到 accuracy ⇒ 读者看不出它在守什么")
    return problems


# --- 组装判定 ----------------------------------------------------------------


def criteria_verdicts(block: dict) -> list[dict]:
    """跑全部时序判据，返回逐条结果（顺序即 :data:`TIMING_CRITERIA` 的顺序）."""
    return [criterion.evaluate(block) for criterion in TIMING_CRITERIA]


def overall_verdict(results: list[dict]) -> str:
    """总判定：有 FAIL 即 FAIL；无 FAIL 但有无从测量即「无法测量」；否则通过.

    ★ 与定向轴 :func:`decision_eval_card._overall_verdict` 同一规则 ——
    「无法测量」**不得**折算成绿。本票的时序轴在当前真机上会落在这个状态
    （premature 未接线），那正是它该有的样子：**如实说测不了**。
    """
    verdicts = [item["verdict"] for item in results]
    if VERDICT_FAIL in verdicts:
        return VERDICT_FAIL
    if VERDICT_UNMEASURABLE in verdicts:
        return VERDICT_UNMEASURABLE
    return VERDICT_PASS


# --- 负控（故意做错的输入） --------------------------------------------------

#: 负控的作用对象是**整份时序块**.
BlockMutator = Callable[[dict], dict]


def _never_speaks(block: dict) -> dict:
    """把块换成「永远沉默」的桩的读数.

    ★ 声明它必须让哪条判据不能判绿：**一条也不该判绿**地证明「沉默很乖」是不可能的。
    在时序轴上，「永远沉默」的产物是：无开口样本 ⇒ 三个量全部不可测。
    故它不是「表现完美」，而是**没东西可测** —— 声明 ``must_fail`` 为空
    是**错的**，正确声明是 ``must_not_pass`` 全部三条计量判据。
    """
    from decision_eval_timing_synthetic import synthetic_silent_rows

    return timing_block(synthetic_silent_rows())


def _unstamped(block: dict) -> dict:
    """★ **删掉轮次打点** —— 本票 ★ 负控的字面形态.

    ``ts`` 一律保留（这正是关键：若实现用 ts 差值兜底，这里就会重新出数）。
    """
    from decision_eval_timing_synthetic import synthetic_unstamped_rows

    return timing_block(synthetic_unstamped_rows())


def _frame_ts_source(block: dict) -> dict:
    """把耗时出处改成**帧的采集端时间戳** —— 本票点名禁止的那一种.

    ★ 它对应一个真实的改动：有人发现 ``latency_ms`` 常缺，于是「顺手」改用帧上的
    ``ts_ms`` 相减。数字会立刻变得很好看（帧每 1Hz 都在），而它与「模型花了多久」
    没有关系。
    """
    from decision_eval_timing import LATENCY_SOURCE_FRAME_TS, latency_provenance
    from decision_eval_timing_synthetic import synthetic_healthy_rows

    mutated = timing_block(synthetic_healthy_rows(), latency_source=LATENCY_SOURCE_FRAME_TS)
    mutated["latency_source_block"] = latency_provenance(LATENCY_SOURCE_FRAME_TS)
    return mutated


def _event_ts_diff_source(block: dict) -> dict:
    """把耗时出处改成**事件 ts 差值** —— 另一种被禁止的来源."""
    from decision_eval_timing import LATENCY_SOURCE_EVENT_TS_DIFF, latency_provenance
    from decision_eval_timing_synthetic import synthetic_healthy_rows

    mutated = timing_block(synthetic_healthy_rows(), latency_source=LATENCY_SOURCE_EVENT_TS_DIFF)
    mutated["latency_source_block"] = latency_provenance(LATENCY_SOURCE_EVENT_TS_DIFF)
    return mutated


def _add_combined_accuracy(block: dict) -> dict:
    """往块里塞一个 ``accuracy`` 字段（模拟「顺手合成一下」）."""
    return {**block, "accuracy": 0.83}


def _drop_premature_labels(block: dict) -> dict:
    """删掉全部 premature 标注 —— 必须判「无法测量」，**不得**判绿."""
    from decision_eval_timing import FIELD_STILL_SPEAKING
    from decision_eval_timing_synthetic import synthetic_healthy_rows

    rows = [
        {k: v for k, v in row.items() if k != FIELD_STILL_SPEAKING}
        for row in synthetic_healthy_rows()
    ]
    return timing_block(rows)


def _tiny_sample(block: dict) -> dict:
    """只留 2 次开口 —— 分位数在此样本量下不可作结论."""
    from decision_eval_timing_synthetic import synthetic_healthy_rows

    return timing_block(synthetic_healthy_rows()[:3])


def _no_timebase(block: dict) -> dict:
    """把所有 ``ts`` 抹成空串 —— 速率失去时间基准."""
    from decision_eval_timing_synthetic import synthetic_healthy_rows

    rows = [{**row, "ts": ""} for row in synthetic_healthy_rows()]
    return timing_block(rows)


@dataclass(frozen=True)
class Mutation:
    """一份**故意做错**的输入，及其「必须让哪些判据不能判绿」的声明.

    Attributes
    ----------
        must_fail: 必须判 :data:`VERDICT_FAIL` 的判据 id。
        must_not_pass: 必须**不判 PASS**（FAIL 或「无法测量」皆可）的判据 id。
            用于「分母退化 / 未接线」这一类：正确行为是判「无法测量」，
            但它同样**绝不能判绿**。
        is_identity: 恒等对照。它**不得**让任何判据变红，作用是把
            「基线本身就不干净」从其它负控里分离出来。恰好一个。
    """

    mutation_id: str
    description: str
    apply: BlockMutator
    must_fail: tuple[str, ...] = ()
    must_not_pass: tuple[str, ...] = ()
    is_identity: bool = False

    @property
    def covers(self) -> tuple[str, ...]:
        """本负控触及的全部判据 id（用于覆盖完整性检查）."""
        return tuple(dict.fromkeys((*self.must_fail, *self.must_not_pass)))


def _identity(block: dict) -> dict:
    """恒等变换 —— 它必须让**任何判定都不变**."""
    return block


#: ★ 负控集合。父 spec §七要求「每条新判据必须配套一份故意做错的输入，
#: 并证明它会判红」；:func:`self_check` 会强制「每条判据至少被一个负控覆盖」，
#: 且拒绝 ``covers`` 为空的负控。
#:
#: ⚠️ **一条实测结论，写在这里以免后人重犯**：
#: ``always-silent`` 在时序轴上**没有** ``must_fail`` —— 它的产物是「没有开口样本」，
#: 故三个量全部「无法测量」。声明它必须让某条判据判红会让自检**假失败**，
#: 进而诱使人去放宽判据；那比漏检更危险。真正该断言的是**它一条都没判绿**。
MUTATIONS: tuple[Mutation, ...] = (
    Mutation(
        mutation_id="identity",
        description="恒等变换（什么都不改）—— 证明「判红」不是来自基线输入本身。",
        apply=_identity,
        is_identity=True,
    ),
    Mutation(
        mutation_id="dropped-round-stamp",
        description=(
            "★ 本票 ★ 负控的字面形态：**删掉轮次打点**（latency_ms 全为 None），"
            "而 ts 一律保留。期望：T_ONSET_MEASURED 判**红**（不是「无法测量」），"
            "且 onset 不得退回用 ts 差值重新出数。"
        ),
        apply=_unstamped,
        must_fail=("T_ONSET_MEASURED",),
        must_not_pass=("T_ONSET_MEDIAN", "T_ONSET_P90", "T_SAMPLE_FLOOR"),
    ),
    Mutation(
        mutation_id="frame-ts-latency-source",
        description=(
            "★ 把耗时出处换成**帧的采集端时间戳**（本票点名禁止）。"
            "数字会变得更好看，但归因是错的 —— 必须判红。"
        ),
        apply=_frame_ts_source,
        must_fail=("T_LATENCY_SOURCE",),
    ),
    Mutation(
        mutation_id="event-ts-diff-latency-source",
        description="把耗时出处换成**事件 ts 差值** —— 另一种被禁止的来源。",
        apply=_event_ts_diff_source,
        must_fail=("T_LATENCY_SOURCE",),
    ),
    Mutation(
        mutation_id="combined-accuracy",
        description=(
            "★ 往块里塞一个 accuracy 字段（「顺手合成两轴」）—— 父 spec §三明确否决，必须判红。"
        ),
        apply=_add_combined_accuracy,
        must_fail=("T_NO_COMBINED_ACCURACY",),
    ),
    Mutation(
        mutation_id="dropped-premature-labels",
        description=(
            "删掉全部 premature 标注（模拟**未接线的真机链路**）—— "
            "必须判「无法测量」，**不得**判绿。"
        ),
        apply=_drop_premature_labels,
        must_not_pass=("T_PREMATURE_MEASURED",),
    ),
    Mutation(
        mutation_id="tiny-sample",
        description="只留 2 次开口 —— onset 分位数不可作结论。",
        apply=_tiny_sample,
        must_not_pass=("T_SAMPLE_FLOOR",),
    ),
    Mutation(
        mutation_id="no-timebase",
        description="抹掉全部 ts —— 每秒误触发失去时间基准，必须不可判绿。",
        apply=_no_timebase,
        must_not_pass=("T_SPURIOUS_TIMEBASE",),
    ),
    Mutation(
        mutation_id="always-silent",
        description=(
            "★ 「永远沉默」的桩。在时序轴上它的产物是**没有开口样本** ⇒ 三个量全部"
            "「无法测量」。★ 它**没有** must_fail（见上方注释）：声明它必须判红会让"
            "自检假失败；要断言的是它**一条都没判绿**。"
        ),
        apply=_never_speaks,
        must_not_pass=("T_ONSET_MEDIAN", "T_ONSET_P90", "T_PREMATURE_MEASURED"),
    ),
)


def _verdict_map(block: dict) -> dict[str, str]:
    """一次算完全部时序判据的判定."""
    return {item["criterion_id"]: item["verdict"] for item in criteria_verdicts(block)}


def self_check() -> int:
    """★ 可证伪性自检：证明每条时序判据**真的会判红**（而不是恒真）.

    做五件事，并把两侧读数都打出来：

    1. **文本与阈值的分叉守卫**（:func:`statements_match_bounds`）；
    2. **无负控的判据 ⇒ 失败**（禁止恒真判据、禁止装饰性负控）；
    3. **恒等变换必须让任何判定都不变**；
    4. 每个负控必须让它 ``must_fail`` 里的判据**判 FAIL**、
       ``must_not_pass`` 里的**不判 PASS**；
    5. ★ **被负控覆盖的判据集合必须等于全部判据集合** —— 守「新增判据即必须配负控」。

    ★ 另外单独断言本票 ★ 负控的**方向**：``dropped-round-stamp`` 必须让
    ``T_ONSET_MEASURED`` 判 **FAIL**，而不是「无法测量」。
    判「无法测量」会让「打点被删掉」看起来像「这项不适用」—— 那正是 fail-open。
    """
    from decision_eval_timing_synthetic import synthetic_healthy_rows

    baseline = timing_block(synthetic_healthy_rows())
    baseline_verdicts = _verdict_map(baseline)
    problems: list[str] = []
    problems.extend(statements_match_bounds())
    problems.extend(statements_mention_axis_separation())

    all_ids = {c.criterion_id for c in TIMING_CRITERIA}
    covered = {cid for mutation in MUTATIONS for cid in mutation.covers}
    for criterion_id in sorted(all_ids - covered):
        problems.append(f"{criterion_id} 没有任何负控声明它必须判红 / 不得判绿 ⇒ 它可能是恒真的")

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
            "没有它，「负控会让判据变红」这句话就没有对照"
        )

    identity_verdicts = _verdict_map(_identity(baseline))
    if identity_verdicts != baseline_verdicts:
        problems.append("恒等变换改变了判定 ⇒ 后面的「变红」可能来自基线输入本身")

    print("=== 时序轴判据的负控自检 (#158) ===")
    print(f"  基线（合成输入）: {_fmt_verdicts(baseline_verdicts)}")
    for mutation in MUTATIONS:
        mutated = mutation.apply(baseline)
        verdicts = _verdict_map(mutated)
        fired = sorted(k for k, v in verdicts.items() if v == VERDICT_FAIL)
        not_green = sorted(k for k, v in verdicts.items() if v != VERDICT_PASS)
        if mutation.is_identity:
            print(f"  {mutation.mutation_id:28s} 判红={fired}（应为空）")
            if fired:
                problems.append(
                    f"恒等对照负控让 {fired} 判红了 ⇒ 基线输入本身就不干净，"
                    "其它负控的「变红」证明不了任何事"
                )
            continue
        print(f"  {mutation.mutation_id:28s} 判红={fired} 非绿={not_green}")
        missing = [cid for cid in mutation.must_fail if verdicts.get(cid) != VERDICT_FAIL]
        if missing:
            problems.append(f"负控 {mutation.mutation_id} 没能让 {missing} 判红")
        still_green = [cid for cid in mutation.must_not_pass if verdicts.get(cid) == VERDICT_PASS]
        if still_green:
            problems.append(
                f"负控 {mutation.mutation_id} 让 {still_green} **判绿了** —— "
                "分母退化 / 未接线这类情形不得判绿"
            )

    # ★ 本票 ★ 负控的方向：必须是 FAIL，不能是 UNMEASURABLE。
    drop_verdict = _verdict_map(_unstamped(baseline)).get("T_ONSET_MEASURED")
    if drop_verdict != VERDICT_FAIL:
        problems.append(
            f"★ 删掉轮次打点后 T_ONSET_MEASURED 的实际判定是 {drop_verdict!r}，"
            "而本票要求它**判红**。判「无法测量」会把接线缺陷说成「这项不适用」"
            "（fail-open）。"
        )

    if problems:
        print("  verdict: FAIL")
        for problem in problems:
            print(f"    - {problem}")
        return 1
    print(
        f"  verdict: PASS（{len(all_ids)} 条判据各有负控；{len(MUTATIONS)} 个故意做错的输入"
        "全部按声明判红或「无法测量」；删掉轮次打点确实判**红**）"
    )
    return 0


def _fmt_verdicts(verdicts: dict[str, str]) -> str:
    """把判定映射打成一行（稳定顺序，便于跨运行 diff）."""
    return "  ".join(f"{k}={verdicts[k]}" for k in sorted(verdicts))


def main(argv: list[str] | None = None) -> int:
    """CLI：默认跑负控自检；``--show-bounds`` 打印阈值与出处."""
    parser = argparse.ArgumentParser(description="时序轴判据的负控自检（工单 #158）")
    parser.add_argument("--self-check", action="store_true", help="跑负控自检（默认）")
    parser.add_argument("--show-bounds", action="store_true", help="打印阈值与出处")
    parser.add_argument("--verify-bounds", action="store_true", help="从冻结快照重算阈值并比对")
    args = parser.parse_args(argv)

    if args.show_bounds or args.verify_bounds:
        ok, rows = verify_bounds()
        for row in rows:
            print(
                f"  {row['bound']:24s} declared={row['declared']:>8} "
                f"recomputed={row['recomputed_from_snapshot']:>8} matches={row['matches']}"
            )
            print(f"      source: {row['source']}")
            print(f"      ⚠️ {row['limitation']}")
        if not ok:
            print("✗ 声明的阈值与从冻结快照重算的不一致 —— BOUNDS 与快照已分叉")
            return 1
        return 0

    return self_check()


if __name__ == "__main__":  # pragma: no cover - CLI glue
    raise SystemExit(main())


__all__ = [
    "BASELINE_EVENTS",
    "BOUND_DERIVATION",
    "MUTATIONS",
    "TIMING_BASELINE_SNAPSHOT",
    "TIMING_BOUNDS",
    "TIMING_CRITERIA",
    "VERDICTS",
    "VERDICT_FAIL",
    "VERDICT_PASS",
    "VERDICT_UNMEASURABLE",
    "Mutation",
    "TimingCriterion",
    "criteria_verdicts",
    "derive_bounds",
    "explain_bounds",
    "main",
    "overall_verdict",
    "self_check",
    "statement_percentages",
    "statements_match_bounds",
    "verify_bounds",
]
