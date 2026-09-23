# ruff: noqa: RUF001, RUF002, RUF003
# (RUF001/002/003 = ambiguous fullwidth punctuation; this module's prose is
# Chinese. Same established repo convention as decision_eval_axis.py /
# decision_eval_criteria.py / decision_events.py.)
"""时序轴：**就算判断对了，是不是说得是时候**（工单 #158，父 spec #154）.

本模块只算**时序轴**，不算定向轴（#157），也**绝不**与它合成单一 accuracy。

两个数字，两个正交的问题
------------------------
* 定向轴（:mod:`.decision_eval_axis`）问「**该不该说**」；
* 时序轴（本模块）问「**说的时机对不对**」。

父 spec §三明令**不合成单一 accuracy**（上游方法学亦是两条等权轴，
见 ``doc/research/eval-upstream-methodology-2026-09-20.md`` §8.2②）。
故本模块的产出里**不存在**任何合并分数键，且由
:func:`forbidden_combined_keys` 把它变成一条**可判红**的守卫，
而不是一句留在文档里的承诺。

三条必须说清的语义决定
----------------------
1. ★ **耗时只允许来自决策链路自身的轮次打点**（``latency_ms``）。
   帧上虽有 ``ts_ms``，但那是**采集端墙钟** —— 用它会把网络与编码抖动
   算进「模型反应慢」，**得出错误的归因**（本票正文原话）。
   故本模块把「这份 onset 出自哪里」变成结果里的**一等字段**
   （:func:`latency_provenance`），并有一条判据专门判它判红。

2. ★ **``ts`` 只用作「速率的分母」，绝不用于任何耗时**。
   这条区分是承重的：同一个字段在两种用途下**一个合法一个非法**。
   ``ts`` 是事件发射端的墙钟，作为「这段会话持续了多久」的时间基准是正当的；
   而拿两个 ``ts`` 相减去当「模型花了多久」会把链路外的抖动算进来。
   本模块用 :func:`session_seconds` 明确标注它的**唯一**合法用途。

3. **「没测」不是「测到 0」**（承 #157 的纪律）：分母为 0 或样本不足时，
   比率给 ``None`` 而不是 ``0.0``。一个 ``0`` 的误触发率会被读成
   「一次都没乱插」，而真相往往是「这段会话里压根没有一次开口」。

与定向轴共享、与不共享的边界
----------------------------
* **共享**「开口」这个词的定义（:data:`~decision_eval_axis.DECISIONS_SPEAKING`）：
  一个词在同一仓里有两份定义，迟早会分叉，而分叉的那一份是没被测过的
  —— 这是本仓反复付费学过的形态（``decision_eval_axis`` 的宽/窄口径之争）。
* **共享**「误触发」的真值（该说 / 不该说）。⚠️ 这是**有意的**，必须说清：
  时序轴报的「每秒误触发次数」与定向轴的「误响应率」**共用真值、但量的不是
  同一件事** —— 前者是**用户可感知的频率**（次/秒，本票正文点名要的量），
  后者是**逐例的比例**。二者会一起动，**不是两次独立测量**；
  读者不得把它们当成对同一事实的交叉验证。

Run tests: cd services/webinfer && python -m pytest tests/test_decision_eval_timing.py -q
Self-check: python -m decision_eval_timing --self-check
"""

from __future__ import annotations

import argparse

# ★ 决策词 → 行为的**唯一定义**住在定向轴模块里；此处 import 而不是重写。
#   「开口」若有两份定义，迟早分叉，而分叉的那一份是没被测过的。
from decision_eval_axis import (
    DECISIONS_SPEAKING,
)
from decision_eval_axis import (
    _pct as _axis_pct,
)
from decision_eval_axis import (
    decision_of as _axis_decision_of,
)
from decision_events import ROUND_KIND_PROACTIVE, percentile

# --- 真值词汇（时机轴）------------------------------------------------------

#: 这一段**该**开口（真值）。与定向轴的「面向 / 非面向」共用真值，见模块 docstring。
EXPECTED_SPEAK = "speak"
#: 这一段**不该**开口（真值）。出现在它上面的开口就是一次**误触发**。
EXPECTED_QUIET = "quiet"

EXPECTED_VALUES: tuple[str, ...] = (EXPECTED_SPEAK, EXPECTED_QUIET)

#: 记录「决策那一刻用户是否仍在说话」的字段名（**唯一定义处**）.
#:
#: ★ 它住在轴模块而不是 sources 模块，是为了让依赖单向：
#: ``timing_sources``（从哪读）依赖 ``timing``（怎么算），反之则成环。
#: 读侧与真值 sidecar 都用这一个常量，于是字段改名不会只改一半。
FIELD_STILL_SPEAKING = "user_still_speaking_at_decision"

# --- 耗时出处（★ 本票最核心的一条纪律的结构化形态）---------------------------

#: 唯一合法的耗时出处：决策链路自己的轮次开启时刻打点
#: （``live_mode._turn_started_at`` → ``live_llm.resolve_latency_ms``）。
LATENCY_SOURCE_CHAIN_STAMP = "decision_chain_round_stamp"
#: 帧的采集端时间戳 —— **明令禁止**用作耗时（本票 AC 原话）。
LATENCY_SOURCE_FRAME_TS = "frame_capture_ts_ms"
#: 事件 ``ts`` 相减 —— 同样**禁止**：它是发射端墙钟，含链路外抖动。
LATENCY_SOURCE_EVENT_TS_DIFF = "event_ts_diff"

FORBIDDEN_LATENCY_SOURCES: tuple[str, ...] = (
    LATENCY_SOURCE_FRAME_TS,
    LATENCY_SOURCE_EVENT_TS_DIFF,
)

# --- 禁止合成的分数键 --------------------------------------------------------

#: ★ 「两轴不合成单一 accuracy」的**可判红**形态。
#:
#: 父 spec §三明确否决单一 accuracy（它在两类错误上等权，而本项目两类错误代价
#: 明确不对称）。一条留在文档里的禁令**不是**防线；这里把它变成
#: :func:`forbidden_combined_keys` 的扫描表 —— 任何合并分数键出现在块里即判红。
FORBIDDEN_COMBINED_KEYS: tuple[str, ...] = (
    "accuracy",
    "combined_score",
    "overall_score",
    "single_accuracy",
    "f1",
    "composite_score",
    "weighted_total",
)

#: 判「说得多快」的最小样本量。低于它 ⇒ 时序轴判**无法测量**，不判绿。
#:
#: ★ 理由不是「不够准」而是**无法自证准不准**（#165 的同一课）：
#: 中位数与 p90 在 3 个样本上是噪声。一个 p90(3 样本) 与「同一配置的偶然抖动」
#: **不可区分**，故宁可拒给结论。
MIN_SPEAKING_ROUNDS = 10


def forbidden_combined_keys(block: object, _prefix: str = "") -> list[str]:
    """递归找出**禁止的合并分数键**（返回其路径列表；空 = 干净）.

    ★ 存在的理由：父 spec 禁止把两轴合成单一 accuracy。**禁令写在文档里
    不构成防线** —— 下一个人（包括我自己）加一个 ``accuracy`` 字段时不会有
    任何东西拦他。故把它变成一条可在卡片上判红的扫描：
    合并分数**不可表示**（一旦出现即被点名），而不是「可以被检测」。
    """
    found: list[str] = []
    if isinstance(block, dict):
        for key, value in block.items():
            path = f"{_prefix}.{key}" if _prefix else str(key)
            if str(key).lower() in FORBIDDEN_COMBINED_KEYS:
                found.append(path)
            found.extend(forbidden_combined_keys(value, path))
    elif isinstance(block, list):
        for index, value in enumerate(block):
            found.extend(forbidden_combined_keys(value, f"{_prefix}[{index}]"))
    return found


# --- 逐轮归类 ---------------------------------------------------------------


def decision_of(row: dict) -> str:
    """该行的有效决策；``ok=False`` 的行一律记为 :data:`DECISION_ERROR`.

    ★ 真的与定向轴**同一实现**（import 自 :mod:`decision_eval_axis`，
    在此仅作为本模块的公开名转发）。失败行是「没测到」，**不得**被当成
    「不开口」—— 当成不开口会把一次失效输出洗成一次正确的沉默。

    ⚠️ 对抗性复核查出过一处措辞不实：初版这里是**另一份实现**，
    而 docstring 却写着「与定向轴同一实现」。两份实现即便今天行为相同，
    也迟早分叉 —— 而分叉的那一份是没被测过的（正是本仓反复记的形态）。
    """
    return _axis_decision_of(row)


def spoke(row: dict) -> bool:
    """这一轮是否**开口**了（``response`` ∪ ``delegation``）."""
    return decision_of(row) in DECISIONS_SPEAKING


def is_spurious(row: dict) -> bool:
    """★ 一次**误触发**：真值说「不该开口」，而它开口了.

    ``delegation`` **计入** —— 它会触发外部检索与播报，比单纯应答更糟
    （这条宽口径与定向轴 D1 同源，不是本模块另立的）。
    """
    return spoke(row) and row.get("expected") == EXPECTED_QUIET


def is_premature(row: dict) -> bool | None:
    """★ 一次**抢话**：话还没说完就插.

    Returns
    -------
        ``True`` / ``False``，或 ``None`` 表示**不适用 / 未标注**。

    ★ 为什么是 ``None`` 而不是 ``False``
    ------------------------------------
    live 链路**当前并不记录**「决策那一刻用户还在不在说话」—— 这不是本票能
    顺手补的（它要动状态机）。故本模块**不假装知道**：未标注一律 ``None``，
    由判据判「无法测量」，并在卡片上显式说明这个缺口。
    把它当成 ``False`` 会把「不知道」洗成「没抢话」—— 正是本仓最贵的那类缺陷。

    ★ 主动轮（proactive）**没有分母**，故直接 ``None``（不适用，不是缺失）。
    主动轮由定时器在 LISTENING 时发起，**根本没有「用户正在说哪句话」**这个
    前提 —— 把它算进 premature 的分母会让这个率的分母含义含混，
    并让一个纯粹的主动轮配置永远卡在「1 次开口未标注」而判不出结论。
    两种 ``None`` 的区别由 :func:`premature_scope` 显式计数说清，
    读侧不必从值本身去猜。
    """
    if not spoke(row):
        return False
    if str(row.get("round_kind") or "") == ROUND_KIND_PROACTIVE:
        return None
    flag = row.get(FIELD_STILL_SPEAKING)
    if flag is None:
        return None
    return bool(flag)


def _pct(numerator: int, denominator: int) -> float | None:
    """百分数；**分母为 0 时返回 ``None``（未测），不返回 0.0**.

    ★ 转发到 :func:`decision_eval_axis._pct`（**同一实现**）而不是另写一份。
    两轴的「没测 ≠ 测到 0」必须是同一条纪律的同一个函数 ——
    两份实现即便今天行为相同，也迟早分叉（对抗性复核正是照这条查出
    本模块 ``decision_of`` 曾是一份平行实现却在 docstring 里声称同一实现）。
    """
    return _axis_pct(numerator, denominator)


# --- 速率的时间基准（ts 的唯一合法用途）--------------------------------------


def session_seconds(rows: list[dict]) -> dict:
    """★ 会话总时长（秒）—— 这是 ``ts`` 在本模块里**唯一**的合法用途.

    逐会话取 ``max(ts) - min(ts)`` 后求和。分母用于把「误触发次数」变成
    「每秒误触发次数」—— 一个**用户可感知的频率**。

    ★ 为什么这里用 ``ts`` 合法，而耗时用它就非法
    --------------------------------------------
    ``ts`` 是事件**发射端**的墙钟。问「这段会话持续了多久」时它是正当的时间基准；
    问「模型花了多久」时它会把**链路之外**的抖动（网络、编码、调度）算进来，
    从而把一个采集端问题误归因成「模型反应慢」—— 那正是本票正文点名要避免的事。

    同一字段、两种用途、一对一错。故本函数在名字上就写死用途，
    并在返回块里带上 ``"use": "rate_denominator_only"`` 以免被挪用。

    Returns
    -------
        ``{seconds_total, per_session, sessions_without_span, use, why}``。
        ``seconds_total`` 为 0 表示**没有任何可用时间基准** ⇒ 下游速率必须是
        ``None``（未测），而不是除零或 ``inf``。
    """
    by_session: dict[str, list[str]] = {}
    for row in rows:
        by_session.setdefault(str(row.get("session_id") or ""), []).append(str(row.get("ts") or ""))

    per_session: dict[str, float] = {}
    without_span: list[str] = []
    for session_id, stamps in by_session.items():
        # ★ ISO-8601 UTC 是定长且字典序＝时间序，故可直接比较。
        #   缺 ts（空串）的轮次**不参与**跨度计算，但所在会话会被点名列进
        #   sessions_without_span —— 静默跳过会让「时间基准其实不完整」看不出来。
        present = sorted(s for s in stamps if s)
        if len(present) < 2:
            without_span.append(session_id)
            continue
        span = _iso_seconds_between(present[0], present[-1])
        if span is None:
            without_span.append(session_id)
            continue
        per_session[session_id] = span

    return {
        "seconds_total": round(sum(per_session.values()), 3),
        "per_session": per_session,
        "sessions_without_span": without_span,
        "use": "rate_denominator_only",
        "why": (
            "ts 是事件发射端墙钟：作为「会话持续多久」的时间基准合法，"
            "作为「模型花了多久」非法（会把网络/编码/调度抖动误归因成模型慢）。"
            "耗时一律只取 latency_ms（决策链路自身的轮次打点）。"
        ),
    }


def _iso_seconds_between(start: str, end: str) -> float | None:
    """两个 ISO-8601 时间戳之间的秒数；不可解析时返回 ``None``（不猜）."""
    from datetime import datetime

    def _parse(text: str) -> datetime | None:
        normalized = text.strip().replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(normalized)
        except ValueError:
            return None

    first, last = _parse(start), _parse(end)
    if first is None or last is None:
        return None
    return round((last - first).total_seconds(), 3)


# --- 耗时出处（★ 从数据取证，不是声明）------------------------------------


def latency_provenance(source: str = LATENCY_SOURCE_CHAIN_STAMP) -> dict:
    """★ 「这份 onset 耗时出自哪里」的**声明块**.

    ⚠️ **这不是防线，只是一份自述。** 对抗性复核实测推翻过这一点：
    ``source`` 是**调用方给的字符串**，与数据无关 —— 一份「其实是用事件 ts
    差值算出来的」耗时，只要调用方不主动声明，就会带着
    ``source=decision_chain_round_stamp / legal=True`` **判绿**。
    那正是本票正文点名的「静默出数」。

    真正的防线是 :func:`latency_audit`：它**从数据本身**取证（逐行
    ``latency_source`` 字段 + 「耗时恰好等于某段 ts 差值」的代数检验），
    且 :func:`timing_block` 用它覆盖这里的声明。本函数因此只用于
    「无逐行数据可查」的场合（例如负控里手工构造一个非法块）。
    """
    return {
        "source": source,
        "field": "latency_ms",
        "origin": (
            "live_mode._turn_started_at（轮次开启的单调时钟打点） → live_llm.resolve_latency_ms"
        ),
        "reference_point": (
            "用户轮：ASR endpoint 达成、该 BT 接话的那一刻；"
            "主动轮：proactive 轮开启的那一刻。两者都是「该开口的时刻」。"
        ),
        "forbidden": list(FORBIDDEN_LATENCY_SOURCES),
        "why": (
            "帧上的 ts_ms 是**采集端墙钟**，用它会把网络与编码抖动算进"
            "「模型反应慢」，得出错误的归因（工单 #158 正文原话）。"
            "事件 ts 相减同罪：它是发射端墙钟，含链路外抖动。"
        ),
        "legal": source == LATENCY_SOURCE_CHAIN_STAMP,
        # ★ 让读者一眼看出这只是一份声明，而不是取证结果。
        "evidence_kind": "declared",
    }


def latency_audit(rows: list[dict]) -> dict:
    """★★ **从数据取证**：这份耗时到底出自哪里（本票 ★ 负控的真正防线）.

    为什么需要它（一次真实的失败，写下来以免后人重犯）
    --------------------------------------------------
    初版只让调用方声明 ``latency_source=...``，于是对抗性复核当场推翻：
    把 ``latency_ms`` **真的**换成由事件 ``ts`` 差值算出来的数，调用方什么都
    不用改，卡片照样报 ``source=decision_chain_round_stamp / legal=True /
    T_LATENCY_SOURCE=pass`` —— **一个「其实用了墙钟」的实现静默出数且判绿**。
    这正是本票正文点名的失效模式，而当时的实现恰好复现了它。

    取证的三条独立证据（**任何一条不成立即不可信**）：

    1. **逐行出处字段。** 每行必须带 ``latency_source`` 且其值为
       :data:`LATENCY_SOURCE_CHAIN_STAMP`。读侧
       （:mod:`.decision_eval_timing_sources`）从事件流放这个字段，
       于是「这一行的耗时从哪来」是**数据**而不是一句声明。
    2. ★ **与 ``ts`` 差值的无关性检验。** 若耗时其实由 ``ts`` 推出，那么按构造
       它**必然恰好等于某个 ts 差值**。故这里检查该恒等式 —— 一旦命中即判定
       该行耗时是 ts 派生的。这不是统计检验（样本太小），而是**代数关系**：
       ts 差值算出来的数必然命中，而真实的链路耗时不会。
    3. **缺失必须暴露。** 任何开口行缺 ``latency_ms`` 或缺出处字段 ⇒ 记入
       ``unattributed``，由判据判红。

    Returns
    -------
        ``{source, legal, evidence_kind, per_source, n_attributed,
        n_unattributed, unattributed_ids, ts_derived_ids, why}``。
        ``legal`` 只在**三条证据全部通过**时为 ``True``。
    """
    speaking = [row for row in rows if spoke(row)]
    per_source: dict[str, int] = {}
    unattributed: list[str] = []
    ts_derived: list[str] = []

    # ts 差值的候选集合：若某行耗时「恰好等于」某个 ts 差值，它就不是链上读数。
    spans = _session_ts_spans_ms(rows)

    for row in speaking:
        row_id = str(row.get("id") or "?")
        latency = row.get("latency_ms")
        source = str(row.get("latency_source") or "")
        if source:
            per_source[source] = per_source.get(source, 0) + 1
        if not source or isinstance(latency, bool) or not isinstance(latency, (int, float)):
            unattributed.append(row_id)
            continue
        if int(latency) <= 0 or source != LATENCY_SOURCE_CHAIN_STAMP:
            unattributed.append(row_id)
            continue
        if int(latency) in spans:
            ts_derived.append(row_id)

    sources = sorted(per_source)
    legal = (
        bool(speaking)
        and not unattributed
        and not ts_derived
        and sources == [LATENCY_SOURCE_CHAIN_STAMP]
    )
    if not speaking:
        source: str | None = None
    elif len(sources) == 1:
        source = sources[0]
    else:
        source = "+".join(sources) if sources else None

    return {
        "source": source,
        "legal": legal,
        # ★ 有无适用对象：一次开口都没有 ⇒ 没有耗时可归因 ⇒ 判据应判
        #   「无法测量」而不是判红（与 T_ONSET_MEASURED 同一条区分：
        #   「本该有而不有」是缺陷，「本就没有」是无适用对象）。
        "applicable": bool(speaking),
        "evidence_kind": "audited_from_rows",
        "per_source": per_source,
        "n_attributed": len(speaking) - len(unattributed),
        "n_unattributed": len(unattributed),
        "unattributed_ids": unattributed,
        "ts_derived_ids": ts_derived,
        "forbidden": list(FORBIDDEN_LATENCY_SOURCES),
        "why": (
            "耗时出处**从数据取证**而不是由调用方声明：逐行 latency_source 字段 + "
            "「耗时恰好等于某段 ts 差值」的代数检验（ts 派生的数必然命中该恒等式，"
            "真实链路耗时不会，因为它来自单调时钟而非墙钟差值）。"
            "任何开口行缺耗时或缺出处 ⇒ legal=False（判红）。"
        ),
    }


def _session_ts_spans_ms(rows: list[dict]) -> set[int]:
    """所有「可能被误当成耗时」的 ``ts`` 差值（毫秒，取整）.

    ★ 代数检验的另一半：一个由 ``ts`` 推出来的耗时**必然**等于某个 ts 差值。
    故把所有会话内的 ts 两两差值收成集合，供 :func:`latency_audit` 命中判定。
    样本是 O(n²)，而一次评测的轮次数是几十到几百 —— 可接受。
    """
    from datetime import datetime

    def parse(text: object) -> datetime | None:
        try:
            return datetime.fromisoformat(str(text).strip().replace("Z", "+00:00"))
        except ValueError:
            return None

    by_session: dict[str, list[datetime]] = {}
    for row in rows:
        stamp = parse(row.get("ts") or "")
        if stamp is None:
            continue
        by_session.setdefault(str(row.get("session_id") or ""), []).append(stamp)

    spans: set[int] = set()
    for stamps in by_session.values():
        for first in stamps:
            for second in stamps:
                delta_ms = round((second - first).total_seconds() * 1000.0)
                if delta_ms > 0:
                    spans.add(delta_ms)
    return spans


def timing_criteria_hint(block: dict) -> list[str]:
    """把「这份块哪里还不能信」写成显式清单（供卡片与自检共用）.

    ★ 读的键是 ``latency_source_block``（:func:`timing_block` 构造的那个
    出处块）。**不读** ``metrics.latency_source`` 那个字符串 —— 曾经就是这样读的，
    结果 caveat 永远打印 ``source=None``（字符串里没有 ``source`` 键），
    于是一条**恒红**的假警报：它看起来在报警，实际没在看任何东西。
    """
    notes: list[str] = []
    provenance = block.get("latency_source_block") or {}
    if not provenance.get("legal"):
        notes.append(
            f"★ onset 耗时的出处是 {provenance.get('source')!r}，"
            f"不是决策链路的轮次打点 —— 该读数**不可**用作时机结论"
        )
    metrics = block.get("metrics") or {}
    onset = metrics.get("onset_latency_ms") or {}
    if onset.get("n_missing_stamp"):
        notes.append(
            f"!! {onset['n_missing_stamp']} 次开口**没有轮次打点**（latency_ms 缺失）"
            f"：{onset.get('missing_stamp_ids')} —— onset 不可测；"
            "**不得**退回用帧时间戳或 ts 差值补出一个数"
        )
    scope = metrics.get("premature_scope") or {}
    if scope.get("n_unlabeled"):
        notes.append(
            f"{scope['n_unlabeled']} 次开口未标注「决策时用户是否仍在说话」"
            f"（{scope.get('unlabeled_ids')}）⇒ premature 不可测"
            "（live 链路当前不记录该事实）"
        )
    return notes


# --- 轴读数 -----------------------------------------------------------------


def onset_series(rows: list[dict]) -> dict:
    """★ onset 延迟的**样本**：开口轮次各自的链路耗时.

    Returns
    -------
        ``{values, n, n_missing_stamp, missing_ids}``。
        ``values`` 只含**真有轮次打点**的开口轮 —— 缺失既不被补 0，
        也不被帧时间戳顶替（顶替正是本票要防的那件事）。

    ★ 为什么「缺打点」必须是**可见的缺失**而不是被跳过
    ------------------------------------------------
    ``latency_ms`` 缺失意味着 ``live_mode`` 的轮次打点没接线（或事件来自
    #156 之前的版本）。静默跳过会让时序轴在一个**没有数据源**的机器上
    照样出数 —— 那正是本票 ★ 负控要抓的形态。
    """
    values: list[int] = []
    missing: list[str] = []
    for row in rows:
        if not spoke(row):
            continue
        latency = row.get("latency_ms")
        if isinstance(latency, bool) or not isinstance(latency, (int, float)):
            missing.append(str(row.get("id") or "?"))
            continue
        if int(latency) <= 0:
            # ★ 0 不是有效耗时（#156 已把这条纪律钉在读侧）：它是
            #   「打点没接上」的另一种表现，按缺失处理而不是按「极快」处理。
            missing.append(str(row.get("id") or "?"))
            continue
        values.append(int(latency))
    return {
        "values": values,
        "n": len(values),
        "n_missing_stamp": len(missing),
        "missing_ids": missing,
    }


def _onset_stats(series: dict) -> dict:
    """Onset 样本 → 中位 / p90 / 极值 / 离群（样本不足时全 ``None``）."""
    values = sorted(series["values"])
    out: dict = {
        "n": len(values),
        "n_missing_stamp": series["n_missing_stamp"],
        "missing_stamp_ids": series["missing_ids"],
        "per_round": series["values"],
        "enough_samples": len(values) >= MIN_SPEAKING_ROUNDS,
        "min_speaking_rounds": MIN_SPEAKING_ROUNDS,
    }
    if not values:
        out.update({"median": None, "p90": None, "min": None, "max": None})
        return out
    out.update(
        {
            # ★ 分位数走 :func:`decision_events.percentile` —— 与读侧同一实现。
            #   两份实现会让「同一个 p90」有两个值，而那是本仓最贵的缺陷形态。
            "median": percentile(values, 0.5),
            "p90": percentile(values, 0.9),
            "min": float(values[0]),
            "max": float(values[-1]),
        }
    )
    return out


def premature_scope(rows: list[dict]) -> dict:
    """Premature 的**分母口径**（把「不适用」与「未标注」分开计数）.

    ★ 为什么必须分开
    ----------------
    两者都让 :func:`is_premature` 返回 ``None``，但含义相反：

    * **不适用** —— 主动轮没有「用户正在说的话」这个前提，本就不该有分母；
    * **未标注** —— 用户轮该有标注而链路没记录 ⇒ **数据缺口**，是真问题。

    合成一个计数会让一个**纯粹的主动轮配置**永远显示「有缺口」，
    而真缺口与假缺口混在一起时，读者会学会忽略这个数字 —— 那比不报更坏。
    """
    speaking = [row for row in rows if spoke(row)]
    # ★ 用**下标**分桶，不用 ``row not in not_applicable``：
    #   后者是**字典相等性**比较，两行内容相同的轮次会被误判成「已在另一桶里」，
    #   于是分母悄悄少算一个。下标不会骗人。
    proactive_idx = {
        index
        for index, row in enumerate(rows)
        if spoke(row) and str(row.get("round_kind") or "") == ROUND_KIND_PROACTIVE
    }
    speaking_idx = [index for index, row in enumerate(rows) if spoke(row)]
    labeled = [
        rows[i]
        for i in speaking_idx
        if i not in proactive_idx and rows[i].get(FIELD_STILL_SPEAKING) is not None
    ]
    unlabeled = [
        rows[i]
        for i in speaking_idx
        if i not in proactive_idx and rows[i].get(FIELD_STILL_SPEAKING) is None
    ]
    return {
        "n_speaking": len(speaking),
        "n_not_applicable_proactive": len(proactive_idx),
        "n_labeled": len(labeled),
        "n_unlabeled": len(unlabeled),
        "unlabeled_ids": [str(row.get("id") or "?") for row in unlabeled],
        "denominator": len(labeled),
        "complete": not unlabeled,
    }


def timing_metrics(
    rows: list[dict],
    *,
    latency_source: str = LATENCY_SOURCE_CHAIN_STAMP,
) -> dict:
    """一组轮次的**时序轴**读数（onset 延迟 + 每秒误触发 + premature rate）.

    Args:
        rows: 逐轮行，字段：``id`` / ``session_id`` / ``ts`` / ``decision`` /
            可选 ``ok`` / 可选 ``latency_ms``（**唯一**合法耗时来源）/
            ``expected``（真值）/ 可选 ``user_still_speaking_at_decision``。
        latency_source: 耗时出处；非链上打点 ⇒ 该块判「不可用」（见
            :func:`latency_provenance`）。

    Returns
    -------
        可直接 ``json.dumps`` 的扁平 dict。**键顺序固定**（构造顺序即顺序），
        故两次运行的差异可逐行 diff（本票 AC「结果结构化、可 diff」）。

    Notes
    -----
        ★ 三个比率的分母都是**显式**的，且分母为 0 时给 ``None`` 而不是 ``0.0``。
        本票正文点名「每秒误触发次数...是铁驭最直接能感受到的量」——
        一个被误记为 0 的误触发率会把「一次都没乱插」和「一次都没开口」
        说成同一件事。
    """
    speaking = [row for row in rows if spoke(row)]
    spurious = [row for row in rows if is_spurious(row)]
    premature_scope_block = premature_scope(rows)
    premature = [row for row in speaking if is_premature(row)]

    series = onset_series(rows)
    span = session_seconds(rows)
    seconds = span["seconds_total"]

    expected_speak = [row for row in rows if row.get("expected") == EXPECTED_SPEAK]
    missed = [row for row in expected_speak if not spoke(row)]

    return {
        # --- 样本规模（**分母必须显式**，本仓硬约束）---
        "n_rounds": len(rows),
        "n_speaking": len(speaking),
        "n_quiet": len(rows) - len(speaking),
        # --- onset 延迟 ---
        "onset_latency_ms": _onset_stats(series),
        # --- 每秒误触发次数（用户体感量）---
        "n_spurious": len(spurious),
        "spurious_round_ids": [str(row.get("id") or "?") for row in spurious],
        "session_seconds": seconds,
        # --- premature rate ---
        "n_premature": len(premature),
        "premature_scope": premature_scope_block,
        "premature_rate_pct": (
            None
            if not premature_scope_block["complete"]
            else _pct(len(premature), premature_scope_block["denominator"])
        ),
        # ★ 速率在「没有时间基准」时必须为 ``None``（未测），**不得**是 0.0，也不得 inf。
        #   本票 AC 点名要的是**每秒**；每分钟（FPM）一并给出，因为上游方法学
        #   （doc/research/eval-upstream-methodology-2026-09-20.md §8.2②）把它列为
        #   「用户实际体感数」的形态，而 0.002 次/秒 不是人读得出来的量。
        "spurious_triggers_per_second": (
            None if not seconds else round(len(spurious) / seconds, 6)
        ),
        "spurious_triggers_per_minute": (
            None if not seconds else round(len(spurious) * 60.0 / seconds, 4)
        ),
        # --- 该说没说（**只报不判**：那是定向轴 D2/D4 的职责，此处不作第二道门）---
        "n_expected_speak": len(expected_speak),
        "n_expected_speak_but_quiet": len(missed),
        "expected_speak_missed_ids": [str(row.get("id") or "?") for row in missed],
        # --- 耗时出处（★ 一等字段，不是注释）---
        "latency_source": latency_source,
    }


def timing_block(rows: list[dict], *, latency_source: str | None = None) -> dict:
    """逐轮行 → 时序轴的一个块（读数 + 出处 + 缺口 + 本票 AC 的守卫读法）.

    Args:
        rows: 逐轮行。★ 每行**必须**带 ``latency_source``（由读侧从事件流放进来），
            否则该行耗时不可归因 —— 见 :func:`latency_audit`。
        latency_source: ⚠️ **已废弃的声明入参，仅为负控保留**。
            正常路径**不要**传它：出处由 :func:`latency_audit` 从数据取证。
            传入时它只用于标记「这份块是手工构造的非法声明」，取证结果依旧优先。

    Returns
    -------
        ``{axis, axis_question, metrics, latency_source_block, rounds, caveats,
        forbidden_combined_keys}``。★ ``axis`` 恒为 ``"timing"``：
        它与定向轴卡片同形但**永不同块**，见模块 docstring。

    Notes
    -----
        ★★ ``latency_source_block`` 是 :func:`latency_audit` 的**取证结果**，
        不是调用方的一句话。初版让调用方声明，被对抗性复核当场推翻：把
        ``latency_ms`` 真的换成 ts 差值算出来的数，调用方什么都不用改，
        卡片照样 ``legal=True / T_LATENCY_SOURCE=pass``。
        现在出处**必须**从逐行数据里读出来，声明只能让结果**更坏**（不能更好）。
    """
    metrics = timing_metrics(rows, latency_source=latency_source or LATENCY_SOURCE_CHAIN_STAMP)
    audit = latency_audit(rows)
    if latency_source is not None and latency_source != LATENCY_SOURCE_CHAIN_STAMP:
        # 手工声明的非法出处：保留声明值以便报错信息指得出「被声明成了什么」，
        # 但**取证结果说了算** —— legal 取两者之与（声明非法即非法）。
        audit = {
            **audit,
            "declared_source": latency_source,
            "declared_legal": latency_source == LATENCY_SOURCE_CHAIN_STAMP,
            "legal": False,
        }
    elif audit["evidence_kind"] == "audited_from_rows":
        audit = {
            **audit,
            "declared_source": LATENCY_SOURCE_CHAIN_STAMP,
            "declared_legal": True,
        }
    block: dict = {
        "axis": "timing",
        "axis_question": (
            "就算判断对了，是不是说得是时候 —— onset 延迟 / 每秒误触发 / premature；"
            "**不**与定向轴合成单一 accuracy"
        ),
        "metrics": metrics,
        "latency_source_block": {**latency_provenance(audit.get("source") or ""), **audit},
        "rounds": {
            "rounds_count": len(rows),
            "sessions": sorted({str(row.get("session_id") or "") for row in rows}),
        },
    }
    block["caveats"] = timing_criteria_hint(block)
    # ★ 自扫描：本块自己先证明它没有合并分数键（判据只是第二道门）。
    block["forbidden_combined_keys"] = forbidden_combined_keys(
        {k: v for k, v in block.items() if k != "forbidden_combined_keys"}
    )
    return block


# --- 渲染前的一致性自检 ------------------------------------------------------


def self_check() -> int:
    """★ 离线可跑的**判据有效性**自检（不需要模型、不需要真机）.

    证明三件事各自**双向**成立（恒真的判据同样没有分辨力，只是换了个方向骗人）：

    1. 一份「健康」的合成输入上，三个量都能算出来且落在合理区间；
    2. ★ **删掉轮次打点** ⇒ onset 变为「不可测」（``median is None``），
       而 ``ts`` 仍在场 —— 证明实现**没有**退回用墙钟差值补数；
    3. 「永远沉默」的桩 ⇒ ``spurious_triggers_per_second`` 为 0 但
       ``premature_rate_pct`` **不是**一个「好分数」，而是 ``None``（无开口样本）
       —— 分母为 0 不得被读成「表现完美」。

    Returns
    -------
        ``0`` 通过 / ``1`` 不通过。
    """
    from decision_eval_timing_synthetic import (
        synthetic_healthy_rows,
        synthetic_silent_rows,
        synthetic_unstamped_rows,
    )

    healthy = timing_block(synthetic_healthy_rows())
    unstamped = timing_block(synthetic_unstamped_rows())
    silent = timing_block(synthetic_silent_rows())

    print("=== 时序轴自检 (#158) ===")
    problems: list[str] = []

    hm = healthy["metrics"]
    print(
        f"  健康输入: onset median={hm['onset_latency_ms']['median']} "
        f"p90={hm['onset_latency_ms']['p90']} "
        f"spurious/s={hm['spurious_triggers_per_second']} "
        f"premature%={hm['premature_rate_pct']} "
        f"(开口 {hm['n_speaking']}/{hm['n_rounds']})"
    )
    if hm["onset_latency_ms"]["median"] is None:
        problems.append("健康输入上 onset 中位数为 None ⇒ 读数对输入不敏感")
    if hm["premature_rate_pct"] is None:
        problems.append("健康输入上 premature rate 为 None ⇒ 标注没被读进去")
    if not hm["onset_latency_ms"]["enough_samples"]:
        problems.append(
            f"健康输入的开口样本 {hm['onset_latency_ms']['n']} 少于 "
            f"MIN_SPEAKING_ROUNDS={MIN_SPEAKING_ROUNDS} ⇒ 夹具本身不满足最小样本"
        )
    if hm["spurious_triggers_per_second"] is None:
        problems.append("健康输入上每秒误触发为 None ⇒ 时间基准没被算出来")

    um = unstamped["metrics"]
    print(
        f"  删掉轮次打点: onset median={um['onset_latency_ms']['median']} "
        f"缺打点={um['onset_latency_ms']['n_missing_stamp']} "
        f"spurious/s={um['spurious_triggers_per_second']}"
    )
    if um["onset_latency_ms"]["median"] is not None:
        problems.append(
            "★ 删掉轮次打点后 onset 仍给出了数值 ⇒ 实现**退回用墙钟**补数了，"
            "这正是本票 ★ 负控要抓的形态"
        )
    if not um["onset_latency_ms"]["n_missing_stamp"]:
        problems.append("删掉轮次打点后缺口计数为 0 ⇒ 缺失没有被显式计数")
    # ★ 关键：ts 仍在场。若实现在用 ts 差值兜底，这里就会出数。
    if unstamped["metrics"]["session_seconds"] <= 0:
        problems.append("负控夹具的 ts 跨度应保持为正（否则证明不了「没退回用 ts」）")

    sm = silent["metrics"]
    print(
        f"  永远沉默的桩: 开口={sm['n_speaking']} "
        f"spurious/s={sm['spurious_triggers_per_second']} "
        f"premature%={sm['premature_rate_pct']}"
    )
    if sm["premature_rate_pct"] is not None:
        problems.append(
            "★ 永远沉默的桩拿到了一个 premature 数值 ⇒ 分母为 0 的比率被判成了"
            "「表现完美」；分母退化必须是 None（未测）"
        )

    if problems:
        print("  verdict: FAIL")
        for problem in problems:
            print(f"    - {problem}")
        return 1
    print("  verdict: PASS（三个量双向可辨：删打点即不可测、分母退化不判绿）")
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI：跑离线自检（本模块唯一的 CLI 职责）.

    ★ 曾经这里还有一个 ``--asset`` 分支，调用
    ``decision_eval_timing_sources.load_timing_asset`` —— **那个函数从来不存在**，
    于是它在真跑时必然 ``ImportError``。它没被 CI 抓到，因为 import 写在分支里
    （延迟导入），而 ``main()`` 当时没有任何测试覆盖。
    这正是本仓反复记的形态：**一个存在但从不执行的代码路径，与没有它无法区分，
    却让读者以为那条路能走。** 出卡入口在 :mod:`decision_eval_timing_card`，
    故这里直接删掉而不是补一个没人用的资产读取器。
    """
    parser = argparse.ArgumentParser(
        description="时序轴读数的离线自检（工单 #158）：onset 延迟 / 每秒误触发 / premature"
    )
    parser.add_argument("--self-check", action="store_true", help="跑离线自检（默认）")
    parser.parse_args(argv)
    return self_check()


if __name__ == "__main__":  # pragma: no cover - CLI glue
    raise SystemExit(main())


__all__ = [
    "EXPECTED_QUIET",
    "EXPECTED_SPEAK",
    "EXPECTED_VALUES",
    "FORBIDDEN_COMBINED_KEYS",
    "FORBIDDEN_LATENCY_SOURCES",
    "LATENCY_SOURCE_CHAIN_STAMP",
    "LATENCY_SOURCE_EVENT_TS_DIFF",
    "LATENCY_SOURCE_FRAME_TS",
    "MIN_SPEAKING_ROUNDS",
    "decision_of",
    "forbidden_combined_keys",
    "is_premature",
    "is_spurious",
    "latency_provenance",
    "main",
    "onset_series",
    "self_check",
    "session_seconds",
    "spoke",
    "timing_block",
    "timing_criteria_hint",
    "timing_metrics",
]
