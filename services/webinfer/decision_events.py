# ruff: noqa: RUF001, RUF002, RUF003
# (RUF001/002/003 = ambiguous fullwidth punctuation; this module's prose is
# Chinese. Same established repo convention as decision_eval_set.py /
# decision_eval_score.py.)
"""决策事件流的**唯一读取方**（决策质量量具 ③，工单 #156）.

Spec: #154 决策质量量具；上游写入侧 = #146（`edd90c3` / `1f6b76d`）。

为什么要有这个模块
------------------
决策**已经在落盘**（`live_llm.finish_llm_turn` 写用户轮、
`live_proactive.send_proactive_prompt` 写主动轮，事件名 `live_decision`），
但**全仓没有任何读取方**：ADR-0014 明文要求的配套查询工具
``scripts/log_query.py`` 至今不存在。**一个没有读者的埋点，与没有埋点在效果上
无法区分** —— 这正是 #154 / #156 的出发点。

本模块只做**决策轮次聚合**这一个用途（不是通用日志查询工具；那件事另开 spec
且 ADR-0014 已否决索引/看板）：

* 按 **会话 × 轮次** 输出 决策 / 时间 / 延迟 / 帧数；
* **用户轮与主动轮（proactive）可区分**（``round_kind``）；
* 用**输出长度**（``raw_text_len``）而不是「content 是否为空」区分
  「判定不开口」与「模型什么都没输出」。

★ 三条必须说清的语义决定
-------------------------
1. **``raw_text_len`` = 解析器输入的长度（模型原始输出的长度），
   ``response_chars`` = 清洗后正文的长度。** 两者**不是**同一个量的两种写法 ——
   这是本模块对写入侧提出的口径（#156 前，live 链路上二者恒等，因为
   webui 消费端没有把 webinfer ``done`` 帧里的 ``raw_text`` 带下来）。
   区分二者的必要性的实测证据：``not-for-me`` 的正文是空的（内容帧只对
   ``response`` 累积），但原始输出里有 ``</not-for-me>`` 与说明文字 ⇒
   **只看正文长度会把一次真实判定误判成「什么都没输出」**。

2. **判据是长度，不是 content 是否为空。** ``</silence>`` / ``</response>`` 在本
   模型里是**单个 special token**（151669 / 151670），被服务端从正文里剥离成空串。
   因此「正文为空」既可能是**正确的沉默判定**，也可能是**失效输出**——
   用正文判空必然混淆。本模块据此把每轮归入 :data:`OUTPUT_STATES` 之一。

3. ★ **一处诚实标注的残余歧义（不得假装已解决）。**
   纯 ``silence`` 轮在**内容层**上与「模型什么都没输出」同形：模型真实吐
   ``</silence>`` 时，被剥离后 ``raw_text_len == 0``；模型一个字没吐时也是 0。
   二者只能靠 **token 级溯源**（``logprobs`` 里的 token id 151669）分开，
   而那需要让 live 链路请求 ``logprobs`` —— 属 #157「token 级判据」的范围，
   本模块**不**假装已经分开。故 :data:`OUTPUT_QUIET_BARE` 单独成桶，
   并在报告里显式标注为「不可归因」，**不计入**「沉默判对了」。

用法
----
    python -m decision_events --events-dir logs/events
    python -m decision_events --events-dir logs/events --json

Run tests: cd services/webinfer && python -m pytest tests/test_decision_events.py -q
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: 决策事件名（ADR-0014；写入侧两个模块共用）。
DECISION_EVENT = "live_decision"

#: 事件来源服务（ADR-0014 的 ``service`` 字段）。
DECISION_SERVICE = "webui"

#: 轮次种类（写入侧 ``extra.round_kind``）。
ROUND_KIND_USER = "user"
ROUND_KIND_PROACTIVE = "proactive"
ROUND_KINDS: tuple[str, ...] = (ROUND_KIND_USER, ROUND_KIND_PROACTIVE)

#: 会话标识缺失时的占位符 —— 显式可见，绝不静默归并到一个「空会话」。
UNATTRIBUTED = "(unattributed)"

# --- 输出状态词汇（★ 本模块的核心判据） ------------------------------------

#: 判了要开口且真的说了话（正文非空）。
OUTPUT_SPOKE = "spoke"
#: ★ 判了要开口却**一个字都没输出** —— 失效输出（false silence 的主要形态）。
OUTPUT_EMPTY = "empty_output"
#: 判了不开口，且原始输出有留痕（典型：``not-for-me`` 带说明文字）。
OUTPUT_QUIET_TRACED = "quiet_traced"
#: 判了不开口，且原始输出为零长度 —— ★ 与「空输出」在内容层同形，见模块 docstring 决定 3。
OUTPUT_QUIET_BARE = "quiet_bare"

OUTPUT_STATES: tuple[str, ...] = (
    OUTPUT_SPOKE,
    OUTPUT_EMPTY,
    OUTPUT_QUIET_TRACED,
    OUTPUT_QUIET_BARE,
)

#: 「开口」类决策：解析器认为这一轮**应当有话**（正文/委派问题）。
_SPEAKING_DECISIONS = ("response", "delegation")
#: 「不开口」类决策。
_QUIET_DECISIONS = ("silence", "not-for-me")


def output_state(decision: str, raw_text_len: int | None, response_chars: int | None = None) -> str:
    """把一轮 (决策, 输出长度) 归入 :data:`OUTPUT_STATES` 之一.

    ★ 判据用**输出长度**（``raw_text_len``），不用「正文是否为空」：

    * 开口类决策 + 长度为 0 ⇒ :data:`OUTPUT_EMPTY`（**失效输出**）
    * 不开口类决策 + 长度大于 0 ⇒ :data:`OUTPUT_QUIET_TRACED`（有留痕的判定）
    * 不开口类决策 + 长度为 0 ⇒ :data:`OUTPUT_QUIET_BARE`（同形，不可归因）

    ``raw_text_len is None``（旧记录 / 字段缺失）**一律归入不可归因**
    （:data:`OUTPUT_QUIET_BARE`），**不报失效输出**：缺失的长度既不是「有输出」的
    证据，也不是「零输出」的证据 —— 把「不知道」报成「模型没说话」，
    与把「已开口」报成失效输出是**同一类假警报**。缺失本身另有
    ``n_missing_raw_text_len`` 与 caveat 显式计数，不会被悄悄放过。

    Args:
        decision: 四态决策值。
        raw_text_len: 解析器输入长度；``None`` = 字段缺失。
        response_chars: 清洗后正文长度。**给出时用于挡住不可能的记录**：
            当 ``raw_text_len < response_chars`` 时，这行记录自相矛盾
            （见 :attr:`Round.violates_length_invariant`），此时**不报「失效输出」**
            而报不可归因 —— 把一条已经开口的轮次报成失效输出，是比缺失更坏的假信号。
    """
    length = -1 if raw_text_len is None else int(raw_text_len)
    contradictory = (
        raw_text_len is not None
        and response_chars is not None
        and int(raw_text_len) < int(response_chars)
    )
    if decision in _SPEAKING_DECISIONS:
        if contradictory or raw_text_len is None:
            return OUTPUT_QUIET_BARE
        return OUTPUT_SPOKE if length > 0 else OUTPUT_EMPTY
    return OUTPUT_QUIET_TRACED if length > 0 else OUTPUT_QUIET_BARE


@dataclass
class Round:
    """一轮决策（一行 ``live_decision`` 事件的解读结果）."""

    session_id: str
    round_kind: str
    seq: int
    ts: str
    decision: str
    latency_ms: int | None
    frames_n: int | None
    raw_text_len: int | None
    response_chars: int | None
    user_text_len: int | None
    delegation_question_len: int | None
    interaction_mode: str
    source: str
    #: ★ #158：该行耗时**出自哪里**（``extra.latency_source``）.
    #: ``None`` ⇒ 事件没带出处证据 ⇒ 时序轴判「不可归因」（**不给默认值**）。
    #: 有了它，「耗时是不是真的来自决策链路的轮次打点」才是**从数据可查**的，
    #: 而不是靠调用方的一句声明（那正是对抗性复核查出的失败模式）。
    latency_source: str | None = None

    @property
    def id(self) -> str:
        """稳定可读的轮次标识 ``<session>#<seq>``（报告与断言共用）."""
        return f"{self.session_id}#{self.seq}"

    @property
    def output_state(self) -> str:
        """本轮的输出状态（长度判据，见 :func:`output_state`)）."""
        return output_state(self.decision, self.raw_text_len, self.response_chars)

    @property
    def is_empty_output(self) -> bool:
        """★ 失效输出：判了要开口却没输出任何东西."""
        return self.output_state == OUTPUT_EMPTY

    @property
    def is_ambiguous_quiet(self) -> bool:
        """★ 不可归因的沉默：与「空输出」在内容层同形（见模块 docstring 决定 3）."""
        return self.output_state == OUTPUT_QUIET_BARE

    @property
    def violates_length_invariant(self) -> bool:
        """★ ``raw_text_len < response_chars`` —— 记录不可信（见 :meth:`output_state`).

        解析器的输入**必然包含正文**，所以小于关系在结构上不可能出现。
        真出现时，成因是写入管道把「解析器输入」与「清洗后正文」搞混了
        （2026-09-21 实测：24 行如此，已修）。这种记录**不可当作评测结论** ——
        尤其不能把它的 ``output_state`` 读成「失效输出」。
        """
        return (
            self.raw_text_len is not None
            and self.response_chars is not None
            and self.raw_text_len < self.response_chars
        )

    def as_dict(self) -> dict[str, Any]:
        """结构化输出（供 ``--json`` 与后续记分卡 #158 / 门禁 #159 复用）."""
        return {
            "id": self.id,
            "session_id": self.session_id,
            "round_kind": self.round_kind,
            "seq": self.seq,
            "ts": self.ts,
            "decision": self.decision,
            "latency_ms": self.latency_ms,
            # ★ #158：耗时出处随行输出 —— 时序轴据此**取证**而不是靠声明。
            "latency_source": self.latency_source,
            "frames_n": self.frames_n,
            "raw_text_len": self.raw_text_len,
            "response_chars": self.response_chars,
            "user_text_len": self.user_text_len,
            "delegation_question_len": self.delegation_question_len,
            "interaction_mode": self.interaction_mode,
            "output_state": self.output_state,
            "source": self.source,
        }


@dataclass
class ParseResult:
    """一次读取的产物：轮次 + 不可解读的行（**显式计数，不静默丢弃**）."""

    rounds: list[Round] = field(default_factory=list)
    #: 非 ``live_decision`` 的事件行数（正常噪声，例如 config.services.patch）。
    skipped_other_events: int = 0
    #: 无法解析为 JSON 或结构不符的行（**必须在报告里可见**）。
    malformed: list[tuple[str, str]] = field(default_factory=list)


def _as_int(value: Any) -> int | None:
    """把 JSON 里的数字字段收敛成 ``int | None``（``bool`` 不算数字）."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _round_from_event(event: dict[str, Any], source: str) -> Round | None:
    """把一行 ``live_decision`` 事件解成 :class:`Round`；结构不符返回 ``None``."""
    extra = event.get("extra")
    if not isinstance(extra, dict) or "decision" not in extra:
        return None
    decision = str(extra.get("decision") or "")
    if not decision:
        return None
    session_id = event.get("session_id")
    session_id = str(session_id) if isinstance(session_id, str) and session_id else UNATTRIBUTED
    round_kind = str(extra.get("round_kind") or ROUND_KIND_USER)
    # ★ #158：耗时出处。**缺字段即 ``None``，不给默认值** —— 默认值会让每一行
    #   都「看起来」出自链上打点，而「没写这个字段」与「写了链上打点」必须可区分。
    raw_source = extra.get("latency_source")
    latency_source = raw_source if isinstance(raw_source, str) and raw_source else None
    # 只有 event 的顶层字段承载 ts / latency_ms（ADR-0014 schema）。
    return Round(
        session_id=session_id,
        round_kind=round_kind,
        seq=0,  # 排序后统一编号
        ts=str(event.get("ts") or ""),
        decision=decision,
        latency_ms=_as_int(event.get("latency_ms")),
        frames_n=_as_int(extra.get("frames_n")),
        raw_text_len=_as_int(extra.get("raw_text_len")),
        response_chars=_as_int(extra.get("response_chars")),
        user_text_len=_as_int(extra.get("user_text_len")),
        delegation_question_len=_as_int(extra.get("delegation_question_len")),
        interaction_mode=str(extra.get("interaction_mode") or ""),
        source=source,
        latency_source=latency_source,
    )


def parse_lines(lines: Iterable[str], *, source: str = "<memory>") -> ParseResult:
    """把 JSONL 文本行解析成 :class:`ParseResult`.

    Args:
        lines: 文本行（可含空行 / 其他事件 / 半行）。
        source: 该批行的来源标签（文件名），用于让报告能指回文件。

    Returns
    -------
        :class:`ParseResult`。**坏行进 ``malformed`` 而不是被吞掉** ——
        静默丢弃会让「事件少了」看起来像「本来就没有」。
    """
    result = ParseResult()
    for lineno, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            result.malformed.append((f"{source}:{lineno}", f"invalid JSON: {exc.msg}"))
            continue
        if not isinstance(event, dict):
            result.malformed.append((f"{source}:{lineno}", "line is not a JSON object"))
            continue
        if event.get("event") != DECISION_EVENT:
            result.skipped_other_events += 1
            continue
        round_ = _round_from_event(event, source)
        if round_ is None:
            result.malformed.append((f"{source}:{lineno}", "live_decision without extra.decision"))
            continue
        result.rounds.append(round_)
    return result


def iter_event_files(
    events_dir: str | os.PathLike[str], service: str = DECISION_SERVICE
) -> list[Path]:
    """返回 ``<events_dir>/<service>-<UTC 日>.jsonl`` 的**按日排序**文件列表."""
    directory = Path(events_dir)
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.glob(f"{service}-*.jsonl") if p.is_file())


def load_events(paths: str | os.PathLike[str] | Iterable[str | os.PathLike[str]]) -> ParseResult:
    """读取一个事件目录，或一组事件文件（``logs/`` 不入库，故输入需外部冻结）."""
    if isinstance(paths, (str, os.PathLike)):
        items: list[str | os.PathLike[str]] = [paths]
    else:
        items = list(paths)
    files: list[Path] = []
    for item in items:
        path = Path(item)
        if path.is_dir():
            files.extend(iter_event_files(path))
        elif path.is_file():
            files.append(path)
    merged = ParseResult()
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        part = parse_lines(text.splitlines(), source=path.name)
        merged.rounds.extend(part.rounds)
        merged.skipped_other_events += part.skipped_other_events
        merged.malformed.extend(part.malformed)
    return merged


def order_rounds(rounds: list[Round]) -> list[Round]:
    """按会话分组、按时间排序，并给每个会话内的轮次编号（1 起）.

    ``ts`` 是 ISO-8601 UTC（同长度、字典序即时间序）；缺失/异常的行排在最后
    但仍被编号 —— **不丢轮次**。
    """
    ordered = sorted(rounds, key=lambda r: (r.session_id, r.ts or "\uffff", r.source))
    counters: dict[str, int] = {}
    for round_ in ordered:
        counters[round_.session_id] = counters.get(round_.session_id, 0) + 1
        round_.seq = counters[round_.session_id]
    return ordered


def percentile(sorted_values: list[int] | list[float], fraction: float) -> float | None:
    """线性插值分位数（空集返回 ``None``）.

    ★ 公开的名字（原 ``_percentile``）：#158 的时序轴要算同一族的
    onset 中位/p90，而 :func:`_latency_summary` 已经有一份。两份实现会让
    「同一个 p90」在一个仓里有两个值 —— 那正是本仓反复付费学过的缺陷形态
    （见 ``decision_eval_axis`` 的宽/窄口径与 ``decision_eval_rounds`` 的
    重复聚合）。故把唯一实现公开，两边共用。
    """
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    position = fraction * (len(sorted_values) - 1)
    low = int(position)
    high = min(low + 1, len(sorted_values) - 1)
    weight = position - low
    return round(sorted_values[low] * (1.0 - weight) + sorted_values[high] * weight, 1)


def _latency_summary(rounds: list[Round]) -> dict[str, Any]:
    """延迟汇总：**缺失即计数**，绝不把缺失当成 0.

    ★ 这是 #156 负控的落点：把写入侧的延迟赋值去掉之后，``n_missing`` 会等于
    轮次数、``median`` 变成 ``None`` —— 读取方**必须**把这个变化显式说出来。
    """
    present = sorted(r.latency_ms for r in rounds if r.latency_ms is not None)
    missing = [r.id for r in rounds if r.latency_ms is None]
    return {
        "n_present": len(present),
        "n_missing": len(missing),
        "n_zero": sum(1 for value in present if value == 0),
        "median": percentile(present, 0.5),
        "p90": percentile(present, 0.9),
        "min": present[0] if present else None,
        "max": present[-1] if present else None,
        "missing_rounds": missing,
    }


def _tally(rounds: list[Round], key: str) -> dict[str, int]:
    """按某字段计数（**全枚举固定键**，保证跨运行的形状稳定可比）."""
    counts: dict[str, int] = {}
    for round_ in rounds:
        value = str(getattr(round_, key))
        counts[value] = counts.get(value, 0) + 1
    return counts


def _counts_by_kind(rounds: list[Round]) -> dict[str, int]:
    """``{decision: n}`` 按轮次种类拆开 —— 用户轮与主动轮可区分."""
    out: dict[str, dict[str, int]] = {}
    for kind in ROUND_KINDS:
        picked = [r for r in rounds if r.round_kind == kind]
        out[kind] = {
            "n_rounds": len(picked),
            "by_decision": _tally(picked, "decision"),
            "by_output_state": _tally(picked, "output_state"),
        }
    other = [r for r in rounds if r.round_kind not in ROUND_KINDS]
    out["other"] = {
        "n_rounds": len(other),
        "round_kinds": _tally(other, "round_kind"),
        "by_decision": _tally(other, "decision"),
        "by_output_state": _tally(other, "output_state"),
    }
    return out


def summarize(rounds: list[Round]) -> dict[str, Any]:
    """一组轮次的汇总（会话级与全局共用同一形状）."""
    return {
        "n_rounds": len(rounds),
        "by_decision": _tally(rounds, "decision"),
        "by_output_state": _tally(rounds, "output_state"),
        "by_round_kind": _counts_by_kind(rounds),
        "latency_ms": _latency_summary(rounds),
        "frames_n": {
            "n_with_frames": sum(1 for r in rounds if r.frames_n is not None),
            "total_frames": sum(r.frames_n for r in rounds if r.frames_n is not None),
            "max_frames_in_round": max(
                (r.frames_n for r in rounds if r.frames_n is not None), default=None
            ),
        },
        "n_unattributed_session": sum(1 for r in rounds if r.session_id == UNATTRIBUTED),
        "empty_output_rounds": [r.id for r in rounds if r.is_empty_output],
        "ambiguous_quiet_rounds": [r.id for r in rounds if r.is_ambiguous_quiet],
        "quiet_traced_rounds": [r.id for r in rounds if r.output_state == OUTPUT_QUIET_TRACED],
        # ★ 自相矛盾的记录（raw_text_len < response_chars）：见 Round 的说明。
        # 它们**不进** empty_output_rounds，且会让整份聚合不可信。
        "inconsistent_length_rounds": [r.id for r in rounds if r.violates_length_invariant],
        # 字段缺失的轮次数（既不报失效输出，也不被悄悄放过）。
        "n_missing_raw_text_len": sum(1 for r in rounds if r.raw_text_len is None),
    }


def aggregate(
    rounds: list[Round],
    *,
    skipped_other_events: int = 0,
    malformed: list[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    """★ 决策聚合（**本模块的对外主入口**）：按会话 × 轮次输出决策/时间/延迟/帧数.

    Args:
        rounds: 已加载的轮次（本函数内部会排序并编号）。
        skipped_other_events: 同文件里非决策事件的行数（正常噪声，显式计数）。
        malformed: 无法解读的行 ``(where, why)`` —— **不静默丢弃**。

    Returns
    -------
        结构化聚合结果：``sessions``（每会话含逐轮明细）+ ``totals`` +
        ``empty_output`` / ``ambiguous_quiet`` 两个显式清单 + ``caveats``。
    """
    ordered = order_rounds(list(rounds))
    by_session: dict[str, list[Round]] = {}
    for round_ in ordered:
        by_session.setdefault(round_.session_id, []).append(round_)

    sessions = [
        {
            "session_id": session_id,
            "first_ts": picked[0].ts,
            "last_ts": picked[-1].ts,
            "rounds": [r.as_dict() for r in picked],
            **summarize(picked),
        }
        for session_id, picked in sorted(by_session.items())
    ]
    totals = summarize(ordered)
    empty = [r.id for r in ordered if r.is_empty_output]
    ambiguous = [r.id for r in ordered if r.is_ambiguous_quiet]
    return {
        "event": DECISION_EVENT,
        "service": DECISION_SERVICE,
        "n_sessions": len(sessions),
        "sessions": sessions,
        "totals": totals,
        "empty_output_rounds": empty,
        "ambiguous_quiet_rounds": ambiguous,
        "reading": {
            "n_skipped_other_events": skipped_other_events,
            "n_malformed": len(malformed or []),
            "malformed": [{"where": where, "why": why} for where, why in (malformed or [])],
        },
        # ★ 判据可用性自述：这些数不为 0 时，聚合结果**不能**被当成完整结论。
        "caveats": _caveats(totals, len(malformed or [])),
    }


def _caveats(totals: dict[str, Any], n_malformed: int) -> list[str]:
    """把「这份聚合结果哪里还不能信」写成显式清单."""
    notes: list[str] = []
    latency = totals["latency_ms"]
    if latency["n_missing"]:
        notes.append(
            f"{latency['n_missing']}/{totals['n_rounds']} 轮没有 latency_ms —— "
            "延迟字段缺失（写入侧打点未接线或事件来自旧版本）"
        )
    if latency["n_zero"]:
        notes.append(f"{latency['n_zero']} 轮 latency_ms == 0 —— 0 不是有效耗时，按缺失看待")
    if totals["n_unattributed_session"]:
        notes.append(f"{totals['n_unattributed_session']} 轮没有 session_id —— 会话维度聚合不完整")
    if totals["empty_output_rounds"]:
        notes.append(f"★ {len(totals['empty_output_rounds'])} 轮为失效输出（判了要开口却零输出）")
    if totals["n_missing_raw_text_len"]:
        notes.append(
            f"{totals['n_missing_raw_text_len']} 轮缺 raw_text_len —— 输出长度未知，"
            "已按不可归因处理（不报失效输出）"
        )
    if totals["inconsistent_length_rounds"]:
        notes.append(
            f"!! {len(totals['inconsistent_length_rounds'])} 轮的 raw_text_len < response_chars "
            "—— 记录自相矛盾（解析器输入不可能短于它自己的正文）⇒ 写入管道有缺陷，"
            "这些轮次**不可**当作评测结论，其 output_state 已按不可归因处理"
        )
    if totals["ambiguous_quiet_rounds"]:
        notes.append(
            f"★ {len(totals['ambiguous_quiet_rounds'])} 轮沉默在内容层不可归因"
            "（</silence> special token 与空输出同形；需 token 级判据，见 #157）"
        )
    if n_malformed:
        notes.append(f"读取侧有 {n_malformed} 行无法解读（见 reading.malformed）")
    if totals["by_round_kind"]["other"]["n_rounds"]:
        notes.append(
            f"{totals['by_round_kind']['other']['n_rounds']} 轮的 round_kind 不在已知集合内"
        )
    return notes


def format_report(agg: dict[str, Any]) -> str:
    """把聚合结果渲染成人读报告."""
    lines: list[str] = []
    totals = agg["totals"]
    lines.append(
        f"=== 决策事件聚合（{agg['event']} @ {agg['service']}）"
        f"  sessions={agg['n_sessions']}  rounds={totals['n_rounds']} ==="
    )
    latency = totals["latency_ms"]
    lines.append(
        "totals   decisions={}  output_states={}".format(
            _fmt_tally(totals["by_decision"]), _fmt_tally(totals["by_output_state"])
        )
    )
    lines.append(
        "         latency_ms: n={} missing={} zero={} median={} p90={} min={} max={}".format(
            latency["n_present"],
            latency["n_missing"],
            latency["n_zero"],
            latency["median"],
            latency["p90"],
            latency["min"],
            latency["max"],
        )
    )
    frames = totals["frames_n"]
    lines.append(
        "         frames_n: rounds_with_frames={} total={} max_in_round={}".format(
            frames["n_with_frames"], frames["total_frames"], frames["max_frames_in_round"]
        )
    )
    for kind in (*ROUND_KINDS, "other"):
        entry = totals["by_round_kind"][kind]
        if kind == "other" and not entry["n_rounds"]:
            continue
        lines.append(
            f"         [{kind}] rounds={entry['n_rounds']}  decisions={_fmt_tally(entry['by_decision'])}"
            f"  output_states={_fmt_tally(entry['by_output_state'])}"
        )
    if totals["empty_output_rounds"]:
        lines.append(
            f"★ 失效输出（判了要开口却零输出）: {', '.join(totals['empty_output_rounds'])}"
        )
    if totals["ambiguous_quiet_rounds"]:
        lines.append(
            f"★ 不可归因的沉默（内容层与空输出同形）: {', '.join(totals['ambiguous_quiet_rounds'])}"
        )
    lines.append("")
    for session in agg["sessions"]:
        lines.append(
            f"--- session {session['session_id']}  rounds={session['n_rounds']}"
            f"  {session['first_ts']} .. {session['last_ts']}"
        )
        for round_ in session["rounds"]:
            frames_text = "-" if round_["frames_n"] is None else str(round_["frames_n"])
            latency_text = (
                "MISSING" if round_["latency_ms"] is None else f"{round_['latency_ms']}ms"
            )
            lines.append(
                "    #{seq:<3} {ts}  {kind:<9} {decision:<11} {state:<12} "
                "latency={latency:<9} frames={frames:<3} raw_len={raw:<5} body_chars={body}".format(
                    seq=round_["seq"],
                    ts=round_["ts"] or "(no ts)",
                    kind=round_["round_kind"],
                    decision=round_["decision"],
                    state=round_["output_state"],
                    latency=latency_text,
                    frames=frames_text,
                    raw="-" if round_["raw_text_len"] is None else round_["raw_text_len"],
                    body="-" if round_["response_chars"] is None else round_["response_chars"],
                )
            )
    reading = agg["reading"]
    if reading["n_malformed"] or reading["n_skipped_other_events"]:
        lines.append("")
        lines.append(
            "reading  malformed={} skipped_other_events={}".format(
                reading["n_malformed"], reading["n_skipped_other_events"]
            )
        )
        for item in reading["malformed"]:
            lines.append(f"    ! {item['where']}: {item['why']}")
    if agg["caveats"]:
        lines.append("")
        for note in agg["caveats"]:
            lines.append(f"!! {note}")
    return "\n".join(lines)


def _fmt_tally(tally: dict[str, int]) -> str:
    """Render a tally as a sorted, cross-run comparable ``a=1,b=2`` string."""
    return ",".join(f"{key}={tally[key]}" for key in sorted(tally)) or "-"


def _default_events_dir() -> Path:
    """仓库根的 ``logs/events``（本文件在 services/webinfer/ -> parents[2] 为根）."""
    return Path(__file__).resolve().parents[2] / "logs" / "events"


def main(argv: list[str] | None = None) -> int:
    """CLI 入口：读事件目录（或文件）并打印聚合结果.

    Returns
    -------
        进程退出码：正常 0；``--require-latency`` 下延迟缺失或存在解析错误时为 1
        （**缺数据即判红**，供后续回归门禁 #159 复用同一判据）。
    """
    parser = argparse.ArgumentParser(
        description="Aggregate live_decision events by session x round."
    )
    parser.add_argument(
        "--events-dir",
        default=None,
        help="目录（读 <dir>/webui-*.jsonl）或单个事件文件；可给多次",
        action="append",
    )
    parser.add_argument("--json", action="store_true", help="输出结构化 JSON 而不是人读报告")
    parser.add_argument(
        "--require-latency",
        action="store_true",
        help="任一轮缺 latency_ms / 存在坏行时判红（退出码 1）",
    )
    args = parser.parse_args(argv)
    targets = args.events_dir or [str(_default_events_dir())]

    loaded = load_events(targets)
    agg = aggregate(
        loaded.rounds,
        skipped_other_events=loaded.skipped_other_events,
        malformed=loaded.malformed,
    )
    if args.json:
        print(json.dumps(agg, ensure_ascii=False, indent=2))
    else:
        print(format_report(agg))

    if not agg["totals"]["n_rounds"]:
        print(
            f"!! 未找到任何 {DECISION_EVENT} 事件（输入：{', '.join(targets)}）；缺失不等于通过",
            file=sys.stderr,
        )
        return 1
    if args.require_latency:
        # ★ The gate must act on the reader's OWN stated judgement. The report
        # already says "0 不是有效耗时，按缺失看待" and counts it as `n_zero`;
        # exiting 0 while printing that would be a gate that contradicts its
        # own criterion. ("缺数据即判红" — a zero is not a measurement.)
        latency = agg["totals"]["latency_ms"]
        reasons: list[str] = []
        if latency["n_missing"]:
            reasons.append(f"{latency['n_missing']} 轮缺 latency_ms")
        if latency["n_zero"]:
            reasons.append(f"{latency['n_zero']} 轮 latency_ms == 0（0 不是有效耗时）")
        if agg["reading"]["n_malformed"]:
            reasons.append(f"{agg['reading']['n_malformed']} 行无法解读")
        if agg["totals"]["inconsistent_length_rounds"]:
            reasons.append(
                f"{len(agg['totals']['inconsistent_length_rounds'])} 轮 raw_text_len < response_chars"
            )
        if reasons:
            print(f"!! --require-latency 判红：{'；'.join(reasons)}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI glue
    raise SystemExit(main())


__all__ = [
    "DECISION_EVENT",
    "DECISION_SERVICE",
    "OUTPUT_EMPTY",
    "OUTPUT_QUIET_BARE",
    "OUTPUT_QUIET_TRACED",
    "OUTPUT_SPOKE",
    "OUTPUT_STATES",
    "ROUND_KINDS",
    "ROUND_KIND_PROACTIVE",
    "ROUND_KIND_USER",
    "UNATTRIBUTED",
    "ParseResult",
    "Round",
    "aggregate",
    "format_report",
    "iter_event_files",
    "load_events",
    "main",
    "order_rounds",
    "output_state",
    "parse_lines",
    "percentile",
    "summarize",
]
