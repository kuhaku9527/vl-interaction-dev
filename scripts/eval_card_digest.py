#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""记分卡「内容摘要」的**唯一**实现（canonical 化 + sha256）。

依据：
  - doc/specs/2026-09-24-decision-eval-content-digest.md（本票 #169）
  - doc/specs/2026-09-23-decision-eval-regression-gate.md（父 spec #159，§3.3.1 两层分工 / §6.5 W2）

★ 为什么这个模块必须存在（而不是「执行器一份、测试一份」）
--------------------------------------------------------
#159 的 W2 缺陷是**同一个事实存在两份副本**：产物里 `criteria` 与 `criteria_registry`
是同一批阈值/判据 id 的两份拷贝，契约只绑了前者的字面 ⇒ 伪造后者时门禁判绿。
本轮（#169）的修法是给执行器一个**整体内容摘要**能力，于是**摘要的算法本身**也成了
一个「必须只有一份」的事实：

  * 执行器 `scripts/drift_gate.py` 用它判红/判绿（CI 的 `drift-gate` job 独立跑）；
  * `scripts/tests/` 用它证明全量叶子覆盖、排版不敏感等性质（`scripts-tests` job）。

若两边各写一份 `json.dumps(..., sort_keys=True, separators=...)`，它们**会各自漂移**
（改一处、忘一处），而两边都对「产物有没有被改」下断言 —— 那种「两边都绿、但算的不是
同一个数」的形态，正是本仓反复付费的那一类（`scripts/tests/eval_gate_fixtures.py`
就是为同一个理由抽出来的单一来源）。故本模块是**唯一**实现，两侧都 import 它。

★ 摘要的语义（写清楚，避免被当成"另一种哈希"）
------------------------------------------------
    canonical_digest(text) := sha256(
        json.dumps(_strip_volatile(json.loads(text)),
                   sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).hexdigest()

即「**解析成 JSON 值 → 去掉机器相关路径叶子 → 按键排序、紧凑重排 → sha256**」。
由此得到两条**承重**性质：

1. **对排版不敏感（构造上成立，不是巧合）**：`sort_keys=True` 让键序无关、
   `separators=(",",":")` 让缩进/空格无关、`json.loads` 让行尾（LF/CRLF）无关。
   故「值逐字不变、只是重排/换缩进/换行尾」的产物**必须**得到同一个摘要 ——
   合法重写不得被误判红（父 spec §3.3.2 的 V4 误伤形态、t7 实测）。
2. **对机器相关绝对路径不敏感**：见 :data:`VOLATILE_PATH_KEYS` 处的说明（R7）。

★ 它**不**是什么：它不是「签名」。摘要只绑**内容**，一份「连摘要一起改掉」的伪造产物
在本地是无法被摘要层拦住的（那属评审与 `git diff` 的职责）—— 父 spec §6.1 的推理
在此完全适用，别把覆盖面说大。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

#: 机器相关绝对路径所在的**键名**。canonical 化时整键丢弃，摘要才可跨机成立。
#:
#: ★ 为什么必须排除（R7，实测，不是猜测）：`doc/research/data/decision_eval_timing_card.json`
#: 里 `events` / `truth` 是**本机绝对路径**（`D:\AI\workspace\...\live_decision_timing_events.jsonl`），
#: `artifact` / `source_file` / `fixture` 同族。它们随「哪台机器、检出到哪个目录」变化，
#: 故**逐字节冻结只在同机同检出下成立**（父 spec §6.3 R7 已登记）。
#: 而本摘要**要**放进契约、由 CI 在别的机器上判红/判绿 ⇒ 必须把这几个键排除，
#: 使它变成一条**机器可移植**的事实。这是本模块唯一的"取舍"，故写明后果：
#:   ⇒ **代价**：这几个键里的任何改动（例如内嵌路径被改坏）**不**会让摘要变。
#:      它们由卡片脚本自己产出、且不承载判据语义，故本票接受该代价并如实登记
#:      （见新 spec 的「剩余局限」一节）。**不得**把它说成「摘要覆盖全量内容」
#:      而不加限定 —— 准确说法是「覆盖**除这些键之外**的全量叶子」。
VOLATILE_PATH_KEYS: tuple[str, ...] = ("events", "truth", "artifact", "source_file", "fixture")

#: sha256 十六进制摘要的长度（契约里 `content_digest` 的合法形状之一）。
DIGEST_HEX_LENGTH = 64


def strip_volatile(node: object) -> object:
    """递归丢弃 :data:`VOLATILE_PATH_KEYS` 里的键，返回可直接 canonical 序列化的值。

    只按**键名**判定（不按值像不像路径）：产物里同名的路径键可能出现在任意深度
    （顶层 `source`、card 内 `events`…），按键名递归才与深度无关、才可复现。

    Parameters
    ----------
    node : object
        ``json.loads`` 得到的任意 JSON 值。

    Returns
    -------
    object
        同构的新对象（dict / list 都是新建的，输入不被改动）。
    """
    if isinstance(node, dict):
        return {k: strip_volatile(v) for k, v in node.items() if k not in VOLATILE_PATH_KEYS}
    if isinstance(node, list):
        return [strip_volatile(v) for v in node]
    return node


def canonical_text(text: str) -> str:
    """把一份 JSON 文本规范化成**唯一**的字节形态（含结尾换行）。

    规范化后再取 sha256，是「摘要与排版无关」的**实现方式**（理由见模块 docstring）。

    Parameters
    ----------
    text : str
        JSON 文本（任意缩进 / 键序 / 行尾）。

    Returns
    -------
    str
        ``json.dumps(sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\\n"``。

    Raises
    ------
    json.JSONDecodeError
        内容不是合法 JSON（调用方**必须** fail-closed，不得静默跳过）。
    RecursionError
        嵌套过深：CPython 的 JSON 解码器会爆栈（实测 `"[" * 200000`）。
        ★ 那**不是** ``JSONDecodeError``；只接后者会让异常穿透并让调用方整个 crash。
    ValueError
        ``json.loads`` 对超长数字字面量等还会抛别的 ``ValueError`` 子类。
    """
    data = strip_volatile(json.loads(text))
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"


def canonical_digest(text: str) -> str:
    """``canonical_text`` 的 sha256 十六进制摘要（小写，64 位）。

    ★ 这是**唯一**的摘要入口：执行器与测试都必须经它取值，不得各自实现。

    Parameters
    ----------
    text : str
        JSON 文本。

    Returns
    -------
    str
        64 位小写十六进制 sha256。

    Raises
    ------
    json.JSONDecodeError, RecursionError, ValueError
        同 :func:`canonical_text`（内容不可 canonical 化时**不返回**任何值）。
    """
    return hashlib.sha256(canonical_text(text).encode("utf-8")).hexdigest()


def digest_of_file(path: Path) -> str:
    """读文件（UTF-8 容错）并返回其 :func:`canonical_digest`。

    ★ 与调用方自己的读盘口径必须一致：``errors="replace"`` 与
    ``drift_gate.run_check_files`` 相同 —— 否则「门禁读到的字节」与「被摘要的字节」
    可能不是同一份，那种分叉会让判红/判绿的理由与被报告的理由不一致。

    Raises
    ------
    OSError
        文件读不出来（调用方按自己的层级语义处置：缺失归 ``present_when_missing``）。
    """
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    return canonical_digest(text)


def is_legal_digest(value: object) -> bool:
    """``value`` 是否是**合法**的摘要字面（小写 64 位十六进制字符串）。

    ★ 为什么形状校验必须严格（这是契约层唯一能做的校验）：
    一个笔误（大写、少一位、写成 ``true`` / ``1`` / ``null``）若被静默当成
    「没有声明摘要」，门禁会**看起来在守却完全没守** —— 与
    ``present_when_missing`` / ``parse_as`` 的非法值同一种病，故同样 rc=2。

    ★ 为什么只收**小写**：``hashlib`` 的 ``hexdigest()`` 恒小写。收大写会让
    「同一个摘要有两种合法写法」—— 于是契约与产物的比对要先做大小写归一，
    那正是"两份同一事实"的开端。宁可让人把大写改回小写。
    """
    if not isinstance(value, str) or len(value) != DIGEST_HEX_LENGTH:
        return False
    return all(ch in "0123456789abcdef" for ch in value)
