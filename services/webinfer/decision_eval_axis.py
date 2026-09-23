# ruff: noqa: RUF001, RUF002, RUF003
# (RUF001/002/003 = ambiguous fullwidth punctuation; this module's prose is
# Chinese and quotes real test sentences. Same established repo convention as
# decision_eval_set.py / decision_eval_score.py / decision_eval_rounds.py.)
"""定向轴：该说时说了吗 / 不该说时说了吗（工单 #157，父 spec #154）.

本模块只算**定向轴**，不算时序轴（#158），也不合成单一 accuracy。

为什么要有这个模块
------------------
Spec #154 要的第一个数字是「**它该不该开口，判断得对不对**」。现有计分器
(:mod:`decision_eval_score`) 给不出这个数字，原因有两个，都是**结构性的**：

1. **既有判据 `not-for-me precision >= 80%` 恒真。**
   它只把「预测成 not-for-me」算作乱插。而真正会出声的路径是
   ``</response>`` / ``</delegation>`` —— 一个**永远输出 ``</silence>``** 的模型
   在它下面 precision = 100%（若无预测则记 0.0）、``directed_miss_rate`` = 0%，
   **无条件通过**。实测正走在这条路上：生产 prompt 在 26 例非面向句上一次都没
   输出 ``</not-for-me>``（recall 0%），却被判「字面达标」。

2. **同一件事有两个口径，而字段名分不出来。** 既有 ``summarize()`` 的
   ``baseline_mis_response_rate_pct`` **只数 ``response``、不数 ``delegation``**；
   ``directed_miss_rate_pct`` **只数 directed→not-for-me、不数被 ``silence`` 吞掉的**。
   两者都是「真值里更贵的那一半没被算进去」。本模块给出**宽口径**为主指标，
   并把**窄口径并列保留**，使两者的差**可见**而不是靠人记得。

★ 三条口径决定（每条都由实测数字支撑）
---------------------------------------
1. **开口 = ``response`` ∪ ``delegation``**，不开口 = ``silence`` ∪ ``not-for-me``。
   依据：``decision_eval_axis`` 的兄弟报告
   ``doc/research/eval-production-prompt-measured-2026-09-20.md`` §1.1 早已用
   「开口率（response+delegation）」这一口径，而 ``summarize()`` 的字段名 narrower。
   实测差异非零：``P2`` 某轮有 2 例非面向句被判成 ``delegation``
   （"把别人的话当任务派给后台"）—— 那**比应答更糟**，却不在旧字段里。

2. **漏判 = 面向句里任何「不开口」**（``silence`` 或 ``not-for-me``），
   不只是 ``directed→not-for-me``。依据：实测「面向句被 ``silence`` 吞掉」
   两轮各 2–3 例（D19 稳定被吞），而窄口径字段**恒为 0.0%**。

3. ★ **代价加权，默认让误响应比漏判更贵（``C_FP : C_FN = 3 : 1``）。**
   单一 accuracy 被本工单明确否决：它在两类错误上等权，而项目两类错误的代价
   **明确不对称**（「宁可漏，不可乱插」）。加权把这条偏好编码进数字。

   ⚠️ **比例 3 的出处要说清（不得含糊）**：规范只给出**序关系**
   （``doc/specs/addressee-detection.md`` §1 原则「误响应代价 > 漏判代价」），
   **没有给倍数**。3 是**工程取值**，不是实测值。为免它变成不可质疑的常数，
   本模块另外输出 :func:`break_even_ratio`——「要让两类错误等价，
   ``C_FP/C_FN`` 需要降到多少」——由**实测的 FP/FN** 算出。
   于是「3 是否合理」变成一个**可复核的数字问题**，而不是一个信条。

沿用与不沿用的边界
------------------
* `services/webinfer/` 是 CI 的 pytest 矩阵内目录；`services/scripts/` **不是**。
  计分放这里才可能被单测守住（#152 已付费学过的形态）。
* **不重算** :mod:`decision_eval_score` 已算的东西以外不重复造；
  但宽/窄两个口径**必须分开给**，所以本模块自己算，并在结果里与旧字段并列。

Run tests: cd services/webinfer && python -m pytest tests/test_decision_eval_axis.py -q
"""

from __future__ import annotations

import statistics

from decision_eval_set import (
    GROUP_DELEGATE,
    GROUP_DIRECTED,
    GROUP_NONDIRECTED,
    SUBSETS,
    subset_by_id,
)

# --- 决策词 → 行为 -----------------------------------------------------------

#: 会**出声**（或被派成任务）的决策：真值不该开口时出现任何一种都算误响应。
#: ★ ``delegation`` 必须在内 —— 旧字段漏了它，而它会触发外部检索 + TTS 播报。
DECISIONS_SPEAKING: tuple[str, ...] = ("response", "delegation")
#: **不出声**的决策。
DECISIONS_QUIET: tuple[str, ...] = ("silence", "not-for-me")
#: 失败行的伪决策（``ok=False`` 时使用）—— 显式区别于「不开口」。
DECISION_ERROR = "error"

# --- token 级证据（AC「区分『判定沉默』与『空输出』」）------------------------

#: 本模型里 ``</silence>`` 是**单个 special token**（对照 :mod:`prompt_constants`）。
TOKEN_ID_SILENCE = 151669
#: ``</response>`` 同样是单个 special token —— 它出现在 ``not-for-me`` 行的首位，
#: 故**不能**用它判定「这一轮在说话」；判定说话看的是 content 与决策词。
TOKEN_ID_RESPONSE = 151670

#: 模型确实吐了 token（有留痕）。含 ``</silence>`` 的 special token 与
#: ``not-for-me`` 的普通 token 串。
EVIDENCE_EVIDENCED = "evidenced"
#: ★ 判了不开口，但模型**一个 token 都没吐** —— 失效输出，与「判定沉默」
#: 在内容层同形（``</silence>`` 被服务端剥离后 content 也是空串）。
EVIDENCE_EMPTY_OUTPUT = "empty_output"
#: 记录里**没有** token 证据（旧产物 / 未请求 logprobs）。
#: ★ 与 ``empty_output`` 严格区分：「不知道」既不是「有输出」的证据，
#: 也不是「零输出」的证据。把它记成 ``empty_output`` 会制造假警报，
#: 记成 ``evidenced`` 会把失效输出洗成正确沉默。
EVIDENCE_NO_TOKEN_EVIDENCE = "no_token_evidence"
#: ★ 决策词说「不开口」，而 token 证据说模型吐的是 ``</silence>`` 之外的
#: **非空首位 token**。这是**解析器与模型不一致**，单独成桶而不是并入任一正常桶。
EVIDENCE_CONTRADICTORY = "contradictory"
#: 该行不是「不开口」决策 ⇒ 不参与沉默证据统计（不是缺失，是适用范围）。
EVIDENCE_NOT_QUIET = "not_quiet"

EVIDENCE_STATES: tuple[str, ...] = (
    EVIDENCE_EVIDENCED,
    EVIDENCE_EMPTY_OUTPUT,
    EVIDENCE_NO_TOKEN_EVIDENCE,
    EVIDENCE_CONTRADICTORY,
)

# --- 代价加权默认值 ----------------------------------------------------------

#: 默认代价比 ``C_FP : C_FN``。★ 3 是**工程取值**（规范只给序关系，无倍数），
#: 见模块 docstring 决定 3；可配，且 :func:`break_even_ratio` 让它可被质疑。
DEFAULT_COST_FP = 3
DEFAULT_COST_FN = 1

#: 分组 → 句数计数器名（与 :data:`decision_eval_score.COUNT_KEY` 同义）。
_COUNT_KEY = {
    GROUP_DIRECTED: "n_directed",
    GROUP_NONDIRECTED: "n_nondirected",
    GROUP_DELEGATE: "n_delegate",
}

#: :func:`axis_metrics` 产出的**可跨轮聚合的数值指标**（其余键是计数或说明文字）。
#:
#: ★ 这份清单是**契约**：:func:`aggregate_axis_blocks` 的跨轮聚合只遍历它，
#: 于是「新加一个指标却忘了聚合」会被
#: ``services/webinfer/tests/test_decision_eval_card.py`` 的覆盖守卫抓住
#: （#165 的教训：少聚合一个指标，那条数字就静默退回「单轮」语义而读者看不出来）。
AXIS_METRIC_KEYS: tuple[str, ...] = (
    "directed_nonresponse_rate_pct",
    "nondirected_spurious_response_rate_pct",
    "nondirected_response_only_rate_pct",
    "directed_nonresponse_as_not_for_me_pct",
    "nondirected_missed_by_not_for_me_pct",
    "not_for_me_precision_pct",
    "not_for_me_recall_pct",
    "not_for_me_prediction_rate_pct",
    "quiet_recall_pct",
    "cost_index",
    # ★ 平凡策略参照也是「跨轮要取中位的读数」，不是常数 —— 它们的**分母随句集**
    #   而变。漏了这两项，卡片上的参照值会永远只有单轮语义。
    "cost_index_always_silent",
    "cost_index_always_speaking",
)

#: 跨轮聚合的**计数**指标（整数，聚合意义与比率不同：它们跨轮**求和**）。
#: 判据的 ``min_denominator`` 读的就是这里 —— 报小了会把有分母的判成退化。
AXIS_COUNT_KEYS: tuple[str, ...] = (
    "n_directed",
    "n_nondirected",
    "n_delegate_excluded",
    "n_errors",
    "false_positives",
    "false_negatives",
    "true_speaking",
    "true_quiet",
    "not_for_me_true",
    "not_for_me_predicted",
    "cost_units",
)

#: **每轮从零算、跨轮不得相加**的键（它们本身是同分母的比率或权重）。
#: ★ 显式登记是因为 :func:`aggregate_axis_blocks` 对「既不在指标清单、也不在
#: 计数清单」的键采取**原样透传首轮**策略 —— 那对一个会被相加才对的键是错的，
#: 而测试会断言这三类**恰好覆盖** ``axis_metrics`` 的全部产出键。
AXIS_PASSTHROUGH_KEYS: tuple[str, ...] = (
    "cost_fp_weight",
    "cost_fn_weight",
    "break_even_fp_fn_ratio",
)

#: :func:`axis_metrics` 必须产出的全部键（测试据此守覆盖）。
AXIS_ALL_KEYS: tuple[str, ...] = (
    *AXIS_METRIC_KEYS,
    *AXIS_COUNT_KEYS,
    *AXIS_PASSTHROUGH_KEYS,
)


def decision_of(row: dict) -> str:
    """该行的有效决策；``ok=False`` 的行一律记为 :data:`DECISION_ERROR`.

    ★ 失败行**不得**被当成「不开口」：那是「没测到」，与「判定沉默」不是一件事。
    """
    if not row.get("ok", True):
        return DECISION_ERROR
    return row.get("decision") or DECISION_ERROR


def token_evidence(row: dict) -> tuple[bool, int, int | None]:
    """读出一行的 token 级证据：``(有没有证据, token 数, 首位 token id)``.

    ★ 接受**两种**形状，因为它们分别来自两条真实路径：

    * ``emitted_token_ids``（完整列表）—— 刚从模型跑完的行（benchmark 的 ``rows``）。
    * ``first_token_id`` + ``n_tokens``（**紧凑投影**）—— 入库产物里的
      ``per_round_rows``。产物不能逐轮存全部 token id（会膨胀数倍），
      但**必须**存下区分「判定沉默」与「空输出」所需的最小证据。
      #157 为此给投影加了这两个字段（见
      ``services/scripts/benchmark_production_live_prompt.py::_decision_only_rows``），
      否则**入库产物在读侧不可判 语义** —— 那正是本工单要修的那类缺陷。

    Returns
    -------
        ``(has_evidence, n_tokens, first_token_id)``。``has_evidence=False``
        表示两个字段都没有（旧产物 / 未请求 logprobs）—— **不可归因**，
        既不能说「有输出」，也不能说「零输出」。
    """
    tokens = row.get("emitted_token_ids")
    if tokens is not None:
        token_list = list(tokens)
        return True, len(token_list), (token_list[0] if token_list else None)
    n_tokens = row.get("n_tokens")
    first = row.get("first_token_id")
    if n_tokens is None and first is None:
        return False, 0, None
    return True, int(n_tokens or 0), (None if first is None else int(first))


def quiet_evidence(row: dict) -> str:
    """用 **token 级证据** 归类一行的「不开口」是哪一种.

    Returns
    -------
        :data:`EVIDENCE_EVIDENCED` / :data:`EVIDENCE_EMPTY_OUTPUT` /
        :data:`EVIDENCE_NO_TOKEN_EVIDENCE` / :data:`EVIDENCE_CONTRADICTORY` /
        :data:`EVIDENCE_NOT_QUIET`。

    判据（**只看 token，不看 content**）：
    ``</silence>`` / ``</response>`` 是 special token 会被服务端从 content 剥离，
    因此「content 是否为空」**推不出**任何事；只有 ``logprobs`` 里的实际 token id 能。

    ★ 两种不开口决策的 token 形态**不同**，故两者的矛盾判定也不同：

    * ``silence`` ⇒ 模型应吐 **special token 151669**。首位不是它 ⇒ 矛盾。
    * ``not-for-me`` ⇒ 该标记**不是词表 token**（= ``</``+``not``+``-``+``for``+``-me>``
      五个普通 token）⇒ 它**不可能**表现为单个 special token。
      首位是 **151669**（``</silence>``）⇒ 模型实际吐的是沉默而不是 not-for-me ⇒ 矛盾。

    ⚠️ **评审查出的缺陷（这里原先是漏的）**：早先只对 ``silence`` 做矛盾检查，
    于是把每一行 ``not-for-me`` 的 token 证据换成 ``[151669]``（模型其实吐的是沉默）
    **不会触发任何判据** —— 该行的 token 证据实际上从未被校验过，
    而「token 级证据区分判定沉默与空输出」正是本工单的核心 AC。
    现已补上 ``not-for-me`` 一侧；由
    ``test_not_for_me_tokens_must_not_be_the_silence_special_token`` 钉住。
    """
    decision = decision_of(row)
    if decision not in DECISIONS_QUIET:
        return EVIDENCE_NOT_QUIET
    has_evidence, n_tokens, first_token_id = token_evidence(row)
    if not has_evidence:
        # ★ 「字段缺失」与「零 token」不是同一件事 —— 见 EVIDENCE_NO_TOKEN_EVIDENCE。
        return EVIDENCE_NO_TOKEN_EVIDENCE
    if n_tokens == 0:
        return EVIDENCE_EMPTY_OUTPUT
    if decision == "silence" and first_token_id != TOKEN_ID_SILENCE:
        return EVIDENCE_CONTRADICTORY
    if decision == "not-for-me" and first_token_id == TOKEN_ID_SILENCE:
        return EVIDENCE_CONTRADICTORY
    return EVIDENCE_EVIDENCED


def _pct(numerator: int, denominator: int) -> float | None:
    """百分数；**分母为 0 时返回 ``None``（未测），不返回 0.0**.

    ★ 这是本工单的核心纪律之一：``0.0`` 是「测到 0」，``None`` 是「没测」。
    把「没测」写成 ``0.0`` 正是那个「空精度 100%」事故的成因。
    """
    if denominator == 0:
        return None
    return round(100.0 * numerator / denominator, 1)


def count_evidence(rows: list[dict]) -> dict[str, int]:
    """逐桶统计不开口的证据形态（含 ``not_quiet``，使覆盖关系可核对）."""
    counts = dict.fromkeys(EVIDENCE_STATES, 0)
    counts[EVIDENCE_NOT_QUIET] = 0
    for row in rows:
        counts[quiet_evidence(row)] += 1
    return counts


def break_even_ratio(false_positives: int, false_negatives: int) -> float | None:
    """两类错误代价相等时的 ``C_FP / C_FN``.

    ``C_FP·FP = C_FN·FN`` ⇒ ``C_FP/C_FN = FN/FP``。

    ★ 存在的理由：默认权重 3 是**工程取值**（规范只给序关系）。有了这个数，
    「3 贵不贵」就变成可复核的问题：它 < 等代价点 ⇒ FP 被判得比等价更贵。
    ``FP=0`` 时无定义（任何有限代价比都无法让 FP 变贵）⇒ ``None``。
    """
    if false_positives == 0:
        return None
    return round(false_negatives / false_positives, 3)


def axis_metrics(
    rows: list[dict],
    *,
    cost_fp: int = DEFAULT_COST_FP,
    cost_fn: int = DEFAULT_COST_FN,
) -> dict:
    """一组跑分行的定向轴读数（宽窄两个口径都给，且代价加权）.

    Args:
        rows: 跑分行（``id`` / ``expected`` / ``decision`` / 可选 ``ok`` /
            可选 ``emitted_token_ids``）。
        cost_fp: 误响应的代价权重。
        cost_fn: 漏判的代价权重。

    Returns
    -------
        可直接 ``json.dumps`` 的扁平 dict。**键顺序固定**（构造顺序即顺序），
        故两次运行的差异可逐行 diff。

    Notes
    -----
        ``delegate`` 组**不进定向轴**（:data:`DIRECTED_AXIS_GROUPS`）—— 它是
        「开口之后走哪条路」。但它**被计数**在 ``n_delegate_excluded`` 里，
        不静默跳过。
    """
    directed = [r for r in rows if r["expected"] == GROUP_DIRECTED]
    nondirected = [r for r in rows if r["expected"] == GROUP_NONDIRECTED]
    delegate = [r for r in rows if r["expected"] == GROUP_DELEGATE]

    nd_decisions = [decision_of(r) for r in nondirected]
    dr_decisions = [decision_of(r) for r in directed]

    false_positives = sum(1 for d in nd_decisions if d in DECISIONS_SPEAKING)
    false_positives_response_only = sum(1 for d in nd_decisions if d == "response")
    true_quiet = sum(1 for d in nd_decisions if d in DECISIONS_QUIET)
    not_for_me_true = sum(1 for d in nd_decisions if d == "not-for-me")
    false_negatives = sum(1 for d in dr_decisions if d not in DECISIONS_SPEAKING)
    false_negatives_as_nfm = sum(1 for d in dr_decisions if d == "not-for-me")
    true_speaking = len(directed) - false_negatives

    not_for_me_predicted = sum(1 for r in rows if decision_of(r) == "not-for-me")

    n_directed = len(directed)
    n_nondirected = len(nondirected)
    scored = n_directed + n_nondirected
    cost_units = cost_fp * false_positives + cost_fn * false_negatives

    n_non_quiet_nd = sum(
        1 for d in nd_decisions if d not in DECISIONS_QUIET and d != DECISION_ERROR
    )

    # ★ 平凡策略参照：同一个句集上「永远不开口」与「永远开口」的代价。
    #   没有这两个参照，cost_index 只是一个孤立数字（93 是好还是坏？）。
    #   有了它们，「这个模型有没有跑赢一个只会沉默的桩」是当场可读的。
    silence_cost = cost_fn * n_directed
    speak_cost = cost_fp * n_nondirected
    #    ★ 实测结论（必须留在卡片里，因为它反直觉）：在**本测试集的基率**下
    #    （25 面向 / 26 非面向），只要 C_FP > 1.67，「永远沉默」就比当前生产模型
    #    的加权代价更低。也就是说：**代价指数单独无法惩罚沉默策略**，
    #    真正挡住「沉默刷分」的是 D2/D4 那两条召回下限。
    #    把这个算式摆在卡片上，读者才不会把 cost_index 当成万能的守门人。

    return {
        "n_directed": n_directed,
        "n_nondirected": n_nondirected,
        "n_delegate_excluded": len(delegate),
        "n_errors": sum(1 for r in rows if not r.get("ok", True)),
        # --- 混淆计数（宽口径）---
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "true_speaking": true_speaking,
        "true_quiet": true_quiet,
        # --- 主指标（宽口径）---
        "directed_nonresponse_rate_pct": _pct(false_negatives, n_directed),
        "nondirected_spurious_response_rate_pct": _pct(false_positives, n_nondirected),
        # --- 窄口径并列（与既有 summarize() 字段同名同义，使差额可见）---
        "nondirected_response_only_rate_pct": _pct(false_positives_response_only, n_nondirected),
        "directed_nonresponse_as_not_for_me_pct": _pct(false_negatives_as_nfm, n_directed),
        "nondirected_missed_by_not_for_me_pct": _pct(n_non_quiet_nd, n_nondirected),
        # --- not-for-me 四态的两个率 ---
        "not_for_me_true": not_for_me_true,
        "not_for_me_predicted": not_for_me_predicted,
        "not_for_me_precision_pct": _pct(not_for_me_true, not_for_me_predicted),
        "not_for_me_recall_pct": _pct(not_for_me_true, n_nondirected),
        "not_for_me_prediction_rate_pct": _pct(not_for_me_predicted, len(rows)),
        # ★ 宽口径召回：「任何不开口」在非面向句上的比例。
        #   它**不是**可以替代 not_for_me_recall 的指标（那样就退回恒真判据了），
        #   而是解释「为什么 precision 100% 而 addressee 判定等于没做」的那个数。
        "quiet_recall_pct": _pct(true_quiet, n_nondirected),
        # --- 代价加权主指标 ---
        "cost_fp_weight": cost_fp,
        "cost_fn_weight": cost_fn,
        "cost_units": cost_units,
        "cost_index": _pct(cost_units, scored),
        "break_even_fp_fn_ratio": break_even_ratio(false_positives, false_negatives),
        # --- ★ 平凡策略参照（让 cost_index 可解释，而不是一个孤立数字）---
        # 同为这个句集上，两个「什么判断都不做」的桩要付多少代价。
        # 读法：cost_index 高于两者 ⇒ 这个模型比它想替代的桩更贵。
        "cost_index_always_silent": _pct(silence_cost, scored),
        "cost_index_always_speaking": _pct(speak_cost, scored),
    }


def axis_block(
    rows: list[dict],
    prompt: str,
    *,
    cost_fp: int = DEFAULT_COST_FP,
    cost_fn: int = DEFAULT_COST_FN,
) -> dict:
    """**两个子集分别**的定向轴读数 + 全体读数 + 沉默证据直方图.

    Args:
        rows: 跑分行（须带 ``id``，用于查子集归属）。
        prompt: 产生这批行的 system prompt —— 子集归属由它对逐字匹配**算得**
            （#155 的资产规则；手写标签会在 prompt 改动后静默过期）。
        cost_fp: 误响应代价权重。
        cost_fn: 漏判代价权重。

    Returns
    -------
        ``{"cost": {...}, "overall": {...}, "by_subset": {subset: {...}},
        "evidence": {...}}``。子集为空时给 ``{"n_cases": 0}`` 而不是零分
        （零分会被读成「全错」）。
    """
    mapping = subset_by_id(prompt)
    by_subset: dict[str, dict] = {}
    for subset in SUBSETS:
        picked = [r for r in rows if mapping.get(r["id"]) == subset]
        if not picked:
            by_subset[subset] = {"n_cases": 0}
            continue
        by_subset[subset] = {
            "n_cases": len(picked),
            "n_directed_cases": sum(1 for r in picked if r["expected"] == GROUP_DIRECTED),
            "n_nondirected_cases": sum(1 for r in picked if r["expected"] == GROUP_NONDIRECTED),
            **axis_metrics(picked, cost_fp=cost_fp, cost_fn=cost_fn),
        }

    return {
        "cost": {
            "fp_weight": cost_fp,
            "fn_weight": cost_fn,
            "ratio": f"{cost_fp}:{cost_fn}",
            "meaning": (
                "主指标 cost_index 按此比例加权：每 100 例里 "
                f"{cost_fp}×误响应 + {cost_fn}×漏判 的加权和。"
                "默认让误响应更贵（对齐「宁可漏，不可乱插」）。"
            ),
        },
        "overall": {"n_cases": len(rows), **axis_metrics(rows, cost_fp=cost_fp, cost_fn=cost_fn)},
        "by_subset": by_subset,
        "evidence": evidence_block(rows, mapping),
    }


def evidence_block(rows: list[dict], mapping: dict[str, str] | None = None) -> dict:
    """Token 级证据块：逐子集的沉默证据直方图 + 覆盖率.

    ★ ``coverage.rows_with_token_evidence`` 必须随卡片一起读：
    没有证据的轮只能判「不可归因」，**不能**算作「判定沉默对了」。
    """
    if mapping is None:
        mapping = subset_by_id(None)
    block: dict = {"states": list(EVIDENCE_STATES)}
    for subset in SUBSETS:
        picked = [r for r in rows if mapping.get(r["id"]) == subset]
        block[subset] = (
            count_evidence(picked)
            if picked
            else dict.fromkeys((*EVIDENCE_STATES, EVIDENCE_NOT_QUIET), 0)
        )
    block["overall"] = count_evidence(rows)
    quiet_rows = [r for r in rows if decision_of(r) in DECISIONS_QUIET]
    block["coverage"] = {
        "quiet_rows": len(quiet_rows),
        "rows_with_token_evidence": sum(
            1 for r in quiet_rows if quiet_evidence(r) != EVIDENCE_NO_TOKEN_EVIDENCE
        ),
        "rows_without_token_evidence": sum(
            1 for r in quiet_rows if quiet_evidence(r) == EVIDENCE_NO_TOKEN_EVIDENCE
        ),
        "why": (
            "「判定沉默」与「空输出」在 content 层同形（</silence> 是 special token，"
            "被服务端剥离成空串）⇒ 只有 logprobs 的实际 token id 能区分。"
            "没有 token 证据的行记为不可归因（no_token_evidence），"
            "**不计入**「沉默判对了」。"
        ),
    }
    return block


# --- 跨轮聚合 ---------------------------------------------------------------
#
# ★ 为什么聚合放在本模块而不是卡片模块：聚合的**分母纪律**是轴的性质，
#   而判据（decision_eval_criteria）需要在自检里构造一个多轮块 —— 放这里
#   才不会有「判据依赖卡片、卡片依赖判据」的环。


def aggregate_metric(values: list[float | None]) -> dict:
    """一条指标的跨轮读数：中位 + 离散度 + **未测轮数**.

    ★ 与 :func:`decision_eval_rounds.aggregate_series` 的关键差别：**允许 ``None``**。
    那里 ``float(None)`` 会直接炸，而这里的 ``None`` 是「该轮分母为 0、这个比率
    无意义」—— 它必须能与「测到了 0」区分开（本工单的核心纪律）。
    未测轮被**排除**在统计外并单独计数；一轮都没测出来 ⇒ ``median`` 为 ``None``
    （不是 0.0）。
    """
    measured = [float(v) for v in values if v is not None]
    out: dict = {
        "per_round": [None if v is None else float(v) for v in values],
        "n_rounds": len(values),
        "n_measured_rounds": len(measured),
        "n_unmeasured_rounds": len(values) - len(measured),
    }
    if not measured:
        out.update(
            {
                "median": None,
                "min": None,
                "max": None,
                "range": None,
                "stdev": None,
                "mad": None,
            }
        )
        return out
    center = float(statistics.median(measured))
    out.update(
        {
            "median": round(center, 3),
            "min": round(min(measured), 3),
            "max": round(max(measured), 3),
            "range": round(max(measured) - min(measured), 3),
            "stdev": round(float(statistics.pstdev(measured)), 3),
            "mad": round(float(statistics.median([abs(v - center) for v in measured])), 3),
        }
    )
    return out


def _aggregate_scope(per_round_scopes: list[dict], *, has_cases: bool) -> dict:
    """把逐轮的同一个作用域（overall 或某个子集）聚合成一块.

    只聚合 :data:`AXIS_METRIC_KEYS` 里的数值指标；其余键取自首轮并**校验跨轮一致**
    —— 分母跨轮不同时比较无效，本仓已为此付过一次费（25 vs 26）。
    """
    if not has_cases:
        return {"n_cases": 0}
    first = per_round_scopes[0]
    for index, scope in enumerate(per_round_scopes[1:], 2):
        # ★ 三个都要比：两个目标分母**加上总句数**。只比前两个会漏掉
        #   「某轮少了一句 delegate」这类变化 —— 它不影响定向轴的分母，
        #   却照样说明两轮跑的不是同一份输入，而证据块与 n_cases 都会跟着变。
        for key in ("n_cases", "n_directed", "n_nondirected"):
            if scope.get(key) != first.get(key):
                raise ValueError(
                    f"第 {index} 轮的 {key}={scope.get(key)} 与首轮 {first.get(key)} 不一致 "
                    "⇒ 分母/句集被改变，跨轮比较无效"
                    "（本仓已因此静默污染过一次结论：25 vs 26）"
                )
    out: dict = {"n_cases": first.get("n_cases")}
    for key in AXIS_METRIC_KEYS:
        out[key] = aggregate_metric([scope.get(key) for scope in per_round_scopes])
    # ★ 计数键**跨轮求和**，不是取首轮。取首轮会让「聚合 3 轮」的卡片报出
    #   只有 1 轮的预测数 —— 而 :class:`Criterion` 的 ``min_denominator`` 正是
    #   读这些计数来判断「这个比率有没有分母」。报小了会把有分母的判成退化
    #   （fail-closed，方向安全），但报错就是报错：非退化守卫必须看到真实规模。
    # ★ 计数键聚合成**结构化三件套**，不是光秃秃一个和。
    #   理由（实测逼出来的）：本卡片的比率是**跨轮取中位**，而计数若只报「和」，
    #   就会出现「中位 100.0%，分母 1」这种**混合口径** —— 那个 100.0% 来自
    #   只有一轮测到的那 1 例，而和是三轮加起来的。
    #   ``n_rounds_nonzero`` 让「这个比率到底有几轮真的可算」变成可读的事实，
    #   于是判据能据此拒绝为「三轮里只有一轮有分母」的比率判绿 —— 那正是
    #   「precision 100% 而分母只有 3 例」这一族缺陷的通用形态。
    for key in AXIS_COUNT_KEYS:
        values = [int(scope.get(key) or 0) for scope in per_round_scopes]
        out[key] = {
            "sum": sum(values),
            "per_round": values,
            "n_rounds": len(values),
            "n_rounds_nonzero": sum(1 for v in values if v),
        }
    for key in AXIS_PASSTHROUGH_KEYS:
        out[key] = first.get(key)
    unknown = (
        set(first) - set(AXIS_ALL_KEYS) - {"n_cases", "n_directed_cases", "n_nondirected_cases"}
    )
    if unknown:
        # ★ fail loud：一个既没登记为「取中位」也没登记为「求和」的新指标，
        #   原样透传首轮会让聚合卡报出**单轮语义**的数字而读者看不出来 ——
        #   #165 已为同一形态付过一次费（少聚合一个指标即静默退回单轮）。
        raise ValueError(
            f"axis_metrics 产出了未登记的指标键 {sorted(unknown)} —— "
            "请在 AXIS_METRIC_KEYS（跨轮取中位）/ AXIS_COUNT_KEYS（跨轮求和）/ "
            "AXIS_PASSTHROUGH_KEYS（原样透传）中登记，否则跨轮聚合语义未定义"
        )
    return out


def _aggregate_evidence(per_round: list[dict]) -> dict:
    """把逐轮的证据直方图与覆盖率加起来."""
    keys = (*EVIDENCE_STATES, EVIDENCE_NOT_QUIET)
    block: dict = {"states": list(EVIDENCE_STATES)}
    for subset in SUBSETS:
        block[subset] = {key: sum(entry[subset][key] for entry in per_round) for key in keys}
    block["overall"] = {key: sum(entry["overall"][key] for entry in per_round) for key in keys}
    coverage_keys = ("quiet_rows", "rows_with_token_evidence", "rows_without_token_evidence")
    block["coverage"] = {
        **{key: sum(entry["coverage"][key] for entry in per_round) for key in coverage_keys},
        "why": per_round[0]["coverage"]["why"],
    }
    return block


def median_view(value: object) -> float | None:
    """单轮块给标量、多轮块给聚合 dict；统一取「中位」（标量则原样）.

    ★ **本特性的唯一一份实现**。早先 ``decision_eval_card._scalar`` 与
    ``decision_eval_criteria._scalar`` 各有一份**同名但契约不同**的版本
    （一个保留 ``None``、一个把 ``None`` 透出去给别处处理），而两者都在做
    「把块里的值读成标量」这同一件事 —— 两份迟早分叉，而分叉的那一份是没被测过的。
    （评审查出；这正是本仓反复记的 Duplicated Code 形态。）

    ★ ``None`` 一路透传，**不换成 0.0**：那是「没测」，不是「测到 0」。
    """
    if value is None:
        return None
    if isinstance(value, dict):
        median = value.get("median")
        return None if median is None else float(median)
    return float(value)  # type: ignore[arg-type]


def counter_value(scope: dict, key: str) -> dict:
    """读一个计数键，**统一**成 ``{sum, per_round, n_rounds, n_rounds_nonzero}``.

    ★ 单轮块里它是裸 int，多轮块里它是上面那个 dict —— 读侧不该因此有两份实现
    （两份必然分叉，而分叉的那一份是没被测过的）。缺键时给全 0，
    于是调用方只需判 ``n_rounds_nonzero``。
    """
    raw = scope.get(key)
    if isinstance(raw, dict):
        return {
            "sum": int(raw.get("sum") or 0),
            "per_round": list(raw.get("per_round") or []),
            "n_rounds": int(raw.get("n_rounds") or 0),
            "n_rounds_nonzero": int(raw.get("n_rounds_nonzero") or 0),
        }
    value = 0 if raw is None else int(raw)
    return {
        "sum": value,
        "per_round": [value],
        "n_rounds": 1,
        "n_rounds_nonzero": 1 if value else 0,
    }


def counter_is_measured_every_round(scope: dict, key: str) -> bool:
    """该计数器是否**每一轮**都非零.

    ★ 这才是非退化守卫的真正判据，而不是「总和不为零」。差别是**实测**出来的：
    真机产物里 ``not_for_me_predicted`` 三轮是 ``[1, 0, 0]`` —— 总和为 1（非零），
    于是「总和」式守卫让 D3 拿**一轮里的一例**换来的 100% 精确率**判绿**。
    那与「precision 100% 而分母只有 3 例」是同一种缺陷，只是换了个地方发生。

    单轮块（``n_rounds == 1``）时退化为「该轮非零」，语义一致。
    """
    counter = counter_value(scope, key)
    return counter["n_rounds"] > 0 and counter["n_rounds_nonzero"] == counter["n_rounds"]


def aggregate_axis_blocks(per_round_blocks: list[dict]) -> dict:
    """逐轮 :func:`axis_block` → 一张多轮聚合块.

    ★ 输出与单轮块**同形**（同样的键），于是判据不需要知道它读的是单轮还是多轮
    —— 判据只有一个实现，只有一份被测试的读取路径。

    Raises
    ------
        ValueError: 轮数 < 2。单轮无法区分「稳定」与「碰巧」，故**宁可报错也不给
            一个像结论的数字**（与 :func:`decision_eval_rounds.aggregate_rounds`
            同一条纪律）。
    """
    if len(per_round_blocks) < 2:
        raise ValueError(
            f"需要至少 2 轮才能给出离散度，收到 {len(per_round_blocks)} 轮 —— "
            "单轮数字本工单已证明不可作点估计（同一配置三次真机跑的误响应率中位 "
            "38.5 → 26.9 → 46.2）"
        )
    first = per_round_blocks[0]
    return {
        "cost": first["cost"],
        "overall": _aggregate_scope(
            [block["overall"] for block in per_round_blocks],
            has_cases=bool(first["overall"].get("n_cases")),
        ),
        "by_subset": {
            subset: _aggregate_scope(
                [block["by_subset"][subset] for block in per_round_blocks],
                has_cases=bool(first["by_subset"][subset].get("n_cases")),
            )
            for subset in SUBSETS
        },
        "evidence": _aggregate_evidence([block["evidence"] for block in per_round_blocks]),
        "rounds": {
            "rounds_count": len(per_round_blocks),
            "n_directed_per_round": [
                block["overall"].get("n_directed") for block in per_round_blocks
            ],
            "n_nondirected_per_round": [
                block["overall"].get("n_nondirected") for block in per_round_blocks
            ],
        },
    }
