# ruff: noqa: RUF001, RUF002, RUF003
"""时序轴的**输入来源**：冻结事件流 × 真值标签 → 逐轮行（工单 #158）.

为什么单独一个模块
------------------
「输入从哪来」与「怎么算」「怎么判」「怎么印」是四种不同的变化原因
（``code-review-checklist.md`` 的 Divergent Change 条）。输入形状变了只该改这里。
与 ``decision_eval_sources.py``（定向轴读入库产物）并列，而不是合并：
两者的输入**不是同一个东西**（一个是模型跑分产物，一个是事件流 + 真值），
合并会得到一个「既读 A 又读 B、且变化原因有两个」的模块。

★ 权威切分：一个事实只有一处来源
---------------------------------
* **决策 / ts / latency_ms 只由事件流承载** —— 由 :mod:`decision_events`
  （读侧唯一入口）解析，本模块**不**自己解析 JSONL、也**不**复制它的字段口径。
* **真值只在标签文件里** —— 事件流里没有「这句该不该理我」这个事实。
* 两者按事件**自身的身份** ``(session_id, ts)`` 对齐，且任何一侧对不上就**报错**，
  不静默丢弃。错位是静默的，而静默正是本项目最贵的教训。

★ 为什么真值不写进事件流
------------------------
事件流的字段是**线上真实记录**（ADR-0014 schema）。往里塞一个真值字段，
会让「这份事件是夹具还是真机」变成不可判 —— 而那是评测结论可信度的前提。
故真值另存 sidecar，并且 sidecar 里逐条声明它的性质。

Run tests: cd services/webinfer && python -m pytest tests/test_decision_eval_timing.py -q
"""

from __future__ import annotations

import json
from pathlib import Path

import decision_events as de
from decision_eval_set import ACTION_DELEGATE, ACTION_RESPOND, ACTION_SILENT, CASES
from decision_eval_sources import REPO_ROOT
from decision_eval_timing import (
    EXPECTED_QUIET,
    EXPECTED_SPEAK,
    FIELD_STILL_SPEAKING,
)

#: 冻结的事件夹具（**入库**：``logs/`` 在 .gitignore 下，真机事件不入库）。
DEFAULT_TIMING_EVENTS = "services/webinfer/tests/fixtures/live_decision_timing_events.jsonl"

#: 真值标签（sidecar；只含事件流里**没有**的事实）。
DEFAULT_TIMING_TRUTH = "services/webinfer/tests/fixtures/decision_timing_truth.json"

#: ``expected_action`` → 时序轴真值。
#:
#: ★ 与定向轴共用同一份真值来源（:data:`decision_eval_set.CASES`），
#: 只是换了个问法：定向轴问「该不该开口」，时序轴问「该开口的那些，
#: 开口的时机对不对」。「该不该」这个词在两轴里**必须是同一个定义**，否则
#: 「误触发」在两条轴上是两件事，而两条轴的数字会被并排读。
_ACTION_TO_EXPECTED: dict[str, str] = {
    ACTION_RESPOND: EXPECTED_SPEAK,
    ACTION_DELEGATE: EXPECTED_SPEAK,
    ACTION_SILENT: EXPECTED_QUIET,
}

#: 事件 ``extra`` 里承载「决策时用户是否仍在说话」的字段（#158 新增写入）。
#: ★ 唯一定义在 :mod:`decision_eval_timing`（避免成环），此处只是转发。
#: 字段缺失 ⇒ 该量**不可测**，不得当成 ``False``（见模块 docstring）。
FIELD_STILL_SPEAKING = FIELD_STILL_SPEAKING


def expected_by_case_id() -> dict[str, str]:
    """``case_id -> speak|quiet``（真值来源是 :data:`decision_eval_set.CASES`）.

    Raises
    ------
        ValueError: 出现未被 :data:`_ACTION_TO_EXPECTED` 覆盖的 expected_action。
            **不静默取默认值** —— 一个默认值会让新加的动作类别悄悄被当成
            「不该开口」，而那正是把「没测」洗成一个结论的形态。
    """
    out: dict[str, str] = {}
    for case_id, _text, _group, _category, _note, action in CASES:
        if action not in _ACTION_TO_EXPECTED:
            raise ValueError(
                f"case {case_id} 的 expected_action={action!r} 未映射到时序轴真值 —— "
                f"已知 {sorted(_ACTION_TO_EXPECTED)}；请显式登记，不要默认"
            )
        out[case_id] = _ACTION_TO_EXPECTED[action]
    return out


def _resolve(path: str | Path) -> Path:
    """相对路径按**仓库根**解析（与 ``decision_eval_sources`` 同一口径）."""
    resolved = Path(path)
    return resolved if resolved.is_absolute() else REPO_ROOT / resolved


def load_truth(path: str | Path = DEFAULT_TIMING_TRUTH) -> dict:
    """读真值 sidecar，返回 ``{labels, meta}``（**不**校验与事件的配对）.

    Raises
    ------
        FileNotFoundError: 标签文件不存在。缺真值等于「没测」——
            **不**退回「全部当成 speak」，那会给出一份看起来正常的假读数。
    """
    resolved = _resolve(path)
    if not resolved.exists():
        raise FileNotFoundError(
            f"时序轴的真值标签不存在：{resolved} —— 缺真值等于没测，"
            "不得退回一个默认真值（那会产出一份看起来正常的假读数）"
        )
    data = json.loads(resolved.read_text(encoding="utf-8"))
    labels = data.get("labels")
    if not isinstance(labels, list) or not labels:
        raise ValueError(f"{resolved} 里没有任何 labels")
    return {
        "labels": labels,
        "meta": {k: v for k, v in data.items() if k != "labels"},
        "path": str(resolved),
    }


def _truth_key(session_id: str | None, ts: str) -> tuple[str, str]:
    """标签与事件的配对键 —— 事件**自身的身份**，不是序号.

    ★ 用 ``(session_id, ts)`` 而非序号：序号在任一侧增删一行时会**静默错位**，
    而错位的真值会让判据在一个错误的世界里「通过」。时间戳是事件自己带的事实，
    错位必然对不上（对不上就报错）。
    """
    return (str(session_id or ""), str(ts or ""))


def _latency_source_of(payload: dict) -> str | None:
    """逐行耗时**出处**：从事件/资产自带的事实读出，不给默认值.

    ★ 返回 ``None`` 表示**该行没有出处证据**（不可归因），由
    :func:`decision_eval_timing.latency_audit` 记为 ``unattributed`` 并判红。

    ★ 为什么不给默认值：给出 ``LATENCY_SOURCE_CHAIN_STAMP`` 会让每一行都
    「看起来」出自链上打点 —— 而那正是一次对抗性复核查出的失败模式
    （出处由调用方声明而非从数据取证，于是一个用 ts 差值算耗时的实现判绿）。
    没有证据就是没有证据。

    取法（按可靠性顺序）：
      1. 事件 ``extra.latency_source``（写入侧显式标注，最可靠）；
      2. 事件的 ``latency_ms`` **存在**且事件 schema 声明其为轮次打点时的
         ``extra.latency_source`` 缺失情形 —— ⚠️ 这一条**刻意不猜**：
         缺字段即 ``None``。因为 #156 的写入侧还没有写这个字段，
         而「没写」与「写了链上打点」必须可区分。
    """
    value = payload.get("latency_source")
    if isinstance(value, str) and value:
        return value
    return None


def build_rows(
    events_path: str | Path = DEFAULT_TIMING_EVENTS,
    truth_path: str | Path = DEFAULT_TIMING_TRUTH,
) -> dict:
    """事件流 × 真值 → 逐轮行（**时序轴的唯一读入口**）.

    Args:
        events_path: 冻结的事件夹具（或任何 ``webui-*.jsonl``）。
        truth_path: 真值 sidecar。

    Returns
    -------
        ``{rows, reading, truth_meta, events_path, truth_path}`` —— ``rows``
        可直接喂 :func:`decision_eval_timing.timing_block`。

    Raises
    ------
        ValueError: 事件与标签**对不上**（多/少/重复）。
            ★ 双向都查：只查一侧会让另一侧的多余项静默消失。
    """
    loaded = de.load_events(_resolve(events_path))
    ordered = de.order_rounds(list(loaded.rounds))
    truth = load_truth(truth_path)
    mapping = expected_by_case_id()

    # ★ 空事件流必须**当场报错**，而且要在「事件数与标签数对不上」之前报。
    #   实测踩到的坑：:func:`decision_events.load_events` 对一个**没解析到**的
    #   相对路径会返回 0 轮（不抛异常）。若不显式挡住，那时唯一的症状会是
    #   「34 个标签没有对应事件」—— 一个指错了方向的错误信息，
    #   而真正的原因（路径没解析到）被藏起来了。
    if not ordered:
        raise ValueError(
            f"事件流里没有任何 live_decision 轮次：{_resolve(events_path)} —— "
            "空输入**不是**「零误触发」，它等于没测（fail-closed）"
        )

    labels: dict[tuple[str, str], dict] = {}
    duplicates: list[str] = []
    for label in truth["labels"]:
        key = _truth_key(label.get("session_id"), label.get("ts"))
        if key in labels:
            duplicates.append(f"{key[0]}@{key[1]}")
        labels[key] = label

    rows: list[dict] = []
    unmatched_events: list[str] = []
    seen: set[tuple[str, str]] = set()
    for round_ in ordered:
        payload = round_.as_dict()
        key = _truth_key(payload.get("session_id"), payload.get("ts"))
        seen.add(key)
        label = labels.get(key)
        if label is None:
            unmatched_events.append(payload["id"])
            label = {}
        row: dict = {
            "id": payload["id"],
            "session_id": payload["session_id"],
            "ts": payload["ts"],
            "round_kind": payload["round_kind"],
            "decision": payload["decision"],
            "latency_ms": payload["latency_ms"],
            # ★★ 每一行的**耗时出处**是数据的一部分，不是调用方的一句话。
            #   读侧在这里从事件流（或资产的逐行字段）把它取出来，供
            #   :func:`decision_eval_timing.latency_audit` 做**自洽性检查**
            #   （⚠️ 只证伪不证明 —— 见该函数的 cannot_prove）。
            "latency_source": _latency_source_of(payload),
            # ★★ 「失效输出」随行带下来（对抗复核 D2）。
            #   真机事件流里**没有** ``ok=False`` 这种形态 —— 写入侧只在**达成决策**
            #   时才写事件（实测 09-21+09-22 共 208 轮，decision 取值只有
            #   response / not-for-me / silence / delegation）。故判「这一轮的输出
            #   是不是失效的」必须用读侧**已有**的 ``output_state``
            #   （``len(raw_text_len) == 0`` 且决策是开口类 ⇒ ``empty_output``）。
            #   ★ 把 ``ok`` 从 benchmark 行搬到这里会是**死代码**：读侧不产它，
            #   于是 ``n_errors`` 恒 0（实测确认）。这正是我自己批评过的形态，
            #   所以这里改为读真正在真机上可得的那个字段。
            "output_state": payload.get("output_state"),
            # ★ 事件流里没有真值 ⇒ ``None``。**不默认成 speak**。
            "expected": None,
            "case_id": label.get("case_id"),
        }
        case_id = label.get("case_id")
        if isinstance(case_id, str):
            if case_id not in mapping:
                raise ValueError(
                    f"标签 {key[0]}@{key[1]} 引用了 case_id={case_id!r}，"
                    "但 decision_eval_set.CASES 里没有这一条 —— 真值来源已漂移"
                )
            row["expected"] = mapping[case_id]
        if FIELD_STILL_SPEAKING in label:
            row[FIELD_STILL_SPEAKING] = bool(label[FIELD_STILL_SPEAKING])
        rows.append(row)

    unmatched_labels = [
        f"{session_id}@{ts}" for (session_id, ts) in labels if (session_id, ts) not in seen
    ]

    problems: list[str] = []
    if duplicates:
        problems.append(f"真值标签有重复键：{sorted(duplicates)}")
    if unmatched_events:
        problems.append(f"{len(unmatched_events)} 个事件没有对应真值：{unmatched_events}")
    if unmatched_labels:
        problems.append(f"{len(unmatched_labels)} 个真值标签没有对应事件：{unmatched_labels}")
    if problems:
        # ★ fail loud：错位是静默的，而静默正是本项目最贵的教训。
        raise ValueError(
            "事件流与真值标签对不上 ⇒ 拒绝给出时序读数：\n  - " + "\n  - ".join(problems)
        )

    return {
        "rows": rows,
        "events_path": str(_resolve(events_path)),
        "truth_path": truth["path"],
        "truth_meta": truth["meta"],
        "reading": {
            "n_rounds": len(rows),
            "n_user_rounds": sum(1 for r in rows if r["round_kind"] == de.ROUND_KIND_USER),
            "n_proactive_rounds": sum(
                1 for r in rows if r["round_kind"] == de.ROUND_KIND_PROACTIVE
            ),
            "n_with_truth": sum(1 for r in rows if r["expected"] is not None),
            "n_skipped_other_events": loaded.skipped_other_events,
            "n_malformed": len(loaded.malformed),
            # ★★ 「这份输入是夹具还是真机」必须**判出来**，不是一句常量。
            #   ★ 真机核验查出的 **D3**：这里原先是硬编码 ``True``，于是
            #   真机 09-21 事件流也返回 ``True``，渲染层照抄夹具告警
            #   「⚠️ 这是冻结夹具（决策为作者写的回放输入）」—— 对真机**说反了**。
            #   一条「必须可判」的注释配一个常量，得到的正是一个**不可判**的字段；
            #   而它守的是评测结论可信度的前提（读者据此决定要不要把数字当真）。
            **is_frozen_fixture_reading(truth["meta"], rows),
        },
    }


def is_frozen_fixture_reading(truth_meta: dict, rows: list[dict]) -> dict:
    """判定这批输入来自**冻结夹具**还是**真机事件流**（含判据，不猜）.

    ★ 判据取**最接近原始出处**的那一个：真值 sidecar 里的 ``labels_are_authored``。
    夹具的 sidecar 自己声明「本夹具的决策是作者写的回放输入」，真机评估的 sidecar
    不会这么声明。**不**从数据特征反推（例如「有没有写入侧字段」）——那会把
    「#158 之前的旧真机事件」（没有那些字段）误判成夹具，而那一批**正是**真机数据。

    Returns
    -------
        ``{is_frozen_fixture, input_kind, input_kind_why}``：
        ``input_kind`` ∈ ``"fixture"`` / ``"real"`` / ``"unknown"``。

    ⚠️ **一处必须承认的残余不确定**：sidecar 既没声明「作者写的」、也没有任何
    真机痕迹时，两种成因（旧版真机 / 一个没声明的夹具）在输入上无法区分，
    故报 ``"unknown"`` —— 三值分开报，读侧据此决定要不要显示夹具告警。
    把这种情形判成 ``"real"`` 会让一个未声明的夹具冒充真机读数；
    判成 ``"fixture"`` 会让真机读数被打上夹具告警。两者都比 ``unknown`` 坏。
    """
    authored = truth_meta.get("labels_are_authored")
    if isinstance(authored, str) and authored.strip():
        return {
            "is_frozen_fixture": True,
            "input_kind": "fixture",
            "input_kind_why": (
                "真值 sidecar 的 ``labels_are_authored`` 非空 ⇒ 它自己声明了"
                "「决策是作者写的回放输入」= 冻结夹具"
            ),
        }
    write_side_fields = ("latency_source", FIELD_STILL_SPEAKING)
    has_write_side = any(row.get(field) is not None for row in rows for field in write_side_fields)
    if has_write_side:
        return {
            "is_frozen_fixture": False,
            "input_kind": "real",
            "input_kind_why": (
                "sidecar 未声明「作者写的」，且事件流带写入侧字段"
                "（latency_source / user_still_speaking_at_decision）⇒ 真机事件产物"
            ),
        }
    return {
        # ★ ``None``（而不是 ``True``/``False``）：判不出来就说判不出来。
        "is_frozen_fixture": None,
        "input_kind": "unknown",
        "input_kind_why": (
            "sidecar 未声明「作者写的」，事件流也没有任何写入侧字段 ⇒ 无法区分"
            "「#158 之前的旧真机事件」与「一个没声明的夹具」。故给 ``unknown``，"
            "**不假装知道** —— 这正是当初不该把它写成常量的原因。"
        ),
    }


#: 事件流里承载「决策时用户是否仍在说话」的字段名（供写入侧与读侧共用一词）。
__all__ = [
    "DEFAULT_TIMING_EVENTS",
    "DEFAULT_TIMING_TRUTH",
    "FIELD_STILL_SPEAKING",
    "build_rows",
    "expected_by_case_id",
    "is_frozen_fixture_reading",
    "load_truth",
]
