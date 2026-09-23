#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Drift Gate 执行器（决策书 → 机器可读契约 → 运行态校验）

依据：
  - doc/specs/drift-gate-harness-spec.md（已批准 / 2026-07-29）
  - reports/drift-gate-handoff.md（交接给后端 / DevOps）
  - config/drift-contract.json（契约，本仓库根 config/）

设计原则（来自 spec）：
  - 脚本**不硬编码**任何端口 / n_ctx 等值；值全部来自契约。
  - 门禁是"一致性检查器"，不是"值冻结器"。
  - 默认 fail-open（不符仅 warning，不阻断），避免瞬时误杀合法改动。**但**对
    "结果文件根本不存在"这一类，每个 check 可用 `present_when_missing` 单独要求
    fail-closed（见下）—— 缺结果 = 没测 ≠ 通过。
  - phase 区分：static=查配置/代码常量；runtime=查运行实例 / 端口 / 日志。
  - **跨平台**：纯 Python 读文件 + re.search，不 shell-out 调 grep / cmd.exe。

契约 schema（v2）：
  {
    "id": "<check-id>",
    "decision_ref": "决策/...md D-XXX",
    "description": "...",
    "phase": "static" | "runtime",
    "paths": ["rel/path/to/file", ...],   # 必填；要读的文件列表
    "pattern": "<regex>",                  # 必填；必须在合并文件内容中至少匹一次
    "not_pattern": "<regex>" | null,       # 可选；若设置则要求**不**匹配（一般不推荐）
    "severity": "block" | "warn",
    "present_when_missing": "skip" | "fail",  # 可选；**缺省 "skip"**
    "parse_as": "json" | null                 # 可选；**缺省 null**（不做解析）
  }

`present_when_missing`（可选，逐 check 生效；只看 "paths 是否**全部**缺失/不可读"）：
  - `"skip"`（缺省，= 历史行为）：全部缺失 -> `[SKIP]` fail-open，passed=True。
    缺省**必须**是 skip：`services/scripts/run-windows.env` 被 .gitignore 忽略、
    CI 不 checkout，若缺省改成 fail，CI 会整体变红。
  - `"fail"`：全部缺失 -> **判红**（passed=False，detail 点名缺失文件）。是否为
    阻断仍由既有 `severity` + `mode` 决定：只有 severity=block 且 mode=closed 才 rc=1。
  - **部分缺失**（有的在、有的不在）不走这条分支，维持既有语义：
    `<missing:path>` 占位符进入内容参与正则。
  - 非法值（`"FALSE"` / `true` / `1` / 未知字符串 / `null`）-> `[META-ERROR]` + rc=2，
    **绝不**静默降级成 `"skip"`。

`parse_as`（可选，逐 check 生效；在 `present_when_missing` **之上**再加一层）：
  - `null`（缺省，= 历史行为）：不做任何解析校验。缺省**必须**是 null：既有 check
    引用的 `run-windows.env` / `.py` / `.yml` 都不是 JSON，强行要求解析会把 CI 全红。
  - `"json"`：**逐文件**对 `paths` 里每个"存在且可读"的文件跑 `json.loads`；
    **任一个**不可解析 -> **判红**（passed=False，detail 点名坏文件与行列）。
    是否为阻断同样只由 `severity` + `mode` 决定。
  - 为什么逐文件而不是解析合并串：合并串前面有 `--- <path> ---` 头，**永远不是**
    合法 JSON，解析它等于无条件判红；逐文件还能点名是哪个文件坏了。
  - 缺失 / 不可读的文件**不归这一层管**（跳过），那是 `present_when_missing` 的
    既有职责 —— 两层并列、互不覆盖。
  - ★ 这层堵的是"**文件在、内容不是一份完整卡片**"：评测产物生成中途被杀/被截断
    时，前缀往往仍能命中 pattern（全部观测点都在文件前段），旧设计会判绿。
    对评测而言那不是通过，而是**没测完**。
  - 非法值（`"JSON"` / `true` / `1` / 未知字符串）-> `[META-ERROR]` + rc=2，
    **绝不**静默降级成 `null`。

用法：
  python scripts/drift_gate.py --contract config/drift-contract.json --phase static --mode open
  python scripts/drift_gate.py --contract config/drift-contract.json --phase runtime --mode closed --report drift_report.txt
  python scripts/drift_gate.py --contract config/drift-contract.json --json

退出码：
  mode=open    -> 永远 0（仅打印告警）
  mode=closed  -> 任一 severity=block 的检查不符则 1，否则 0
  契约缺失 / JSON 解析失败 / present_when_missing 或 parse_as 取非法值 -> 2
      （meta-error，区别于业务漂移：契约本身写错了，判绿判红都不算数）
  runtime 阶段 probe 刷新失败 -> 3（RUNTIME-PROBE-ERROR，见 F4-P1a 顺序保护）
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("drift_gate")

# --- present_when_missing：逐 check 的"结果缺失"语义 -------------------------
# 决策表：键不存在 -> "skip"；显式 "skip" -> "skip"；显式 "fail" -> "fail"；
# 其余任何值（含 JSON null / true / 1 / 大写笔误）-> meta-error rc=2。
# 拒绝 null 与 pytest.mark.skip 同理：写 `null` 是"我本该填一个值但没填"，
# 静默当成 skip 正是本字段要杀的那类 fail-open。
DEFAULT_PRESENT_WHEN_MISSING = "skip"
VALID_PRESENT_WHEN_MISSING = ("skip", "fail")


# --- parse_as：逐 check 的"结构化产物必须可解析"语义 -------------------------
# 决策表：键不存在 -> None；显式 null -> None；显式 "json" -> "json"；
# 其余任何值（"JSON" / true / 1 / 未知串）-> meta-error rc=2。
#
# ★ 与 present_when_missing 的一处**刻意不对称**：那里显式 null 是非法值，
# 这里显式 null 是**合法**的（= 缺省）。理由：该字段的缺省值本身就**是** JSON 的
# null（"不做解析"），写 `"parse_as": null` 等于把缺省值写出来；而
# present_when_missing 的缺省是字符串 "skip"，写 null 属于类型错（"我本该填一个
# 值但没填"），两者的"null 是否表达得清意图"并不相同。
DEFAULT_PARSE_AS = None
VALID_PARSE_AS = ("json",)


# --- F4-P1a: runtime 顺序保护（gate 严格晚于 probe） -----------------------
# runtime 阶段依赖 VLM runtime props 快照文件。若该文件缺失/过期，gate 会在
# 跑 runtime 检查 *之前* 自动调 vlm_runtime_probe.py 刷新，避免"gate 早于 probe
# 跑 → 缺 props → 误判 SKIP/closed" 的顺序 bug。仅在 runtime 阶段触发，
# static 阶段保持纯读文件 + re.search，不调 probe、不被破坏。
VLM_PROPS_REL = "logs/vlm-runtime-props.json"
VLM_PROPS_STALE_SECONDS = 300.0
VLM_PROBE_REL = "scripts/vlm_runtime_probe.py"
VLM_PROBE_BASE_URL = "http://127.0.0.1:7060"
VLM_PROBE_WAIT_SECONDS = 5


def _severity_head(check: dict, mode: str) -> str:
    """构造判定失败时的行首标签：``[BLOCK] <id>`` 或 ``[WARN] <id>``.

    ★ 该判断在收敛前于本文件重复 3 处，且必须处处一致 —— 它是**唯一的**"这条失败
    会不会阻断"的可读信号（真正的阻断由 ``run_all`` 的 ``any_block_fail`` 决定，判别式
    同形：只在 severity=block **且** mode=closed 时为真）。两处漂移会让报告与退出码
    互相矛盾，故收敛成一处。

    Parameters
    ----------
    check : dict
        契约里的单条 check。
    mode : str
        ``"open"`` 或 ``"closed"``。

    Returns
    -------
    str
        ``"[BLOCK] <id>"`` 或 ``"[WARN] <id>"``。
    """
    cid = check.get("id", "?")
    severity = check.get("severity", "warn")
    return f"[BLOCK] {cid}" if (severity == "block" and mode == "closed") else f"[WARN] {cid}"


def present_when_missing_of(check: dict) -> str:
    """返回该 check 的 ``present_when_missing`` 语义（已校验，只有 skip / fail）.

    ``load_contract`` 已把非法值挡在 rc=2；这里再兜一层并把"键不存在"映射到
    :data:`DEFAULT_PRESENT_WHEN_MISSING`，故调用方读到的一定是合法值。

    Parameters
    ----------
    check : dict
        契约里的单条 check。

    Returns
    -------
    str
        ``"skip"``（缺省）或 ``"fail"``。

    Raises
    ------
    ValueError
        契约被绕过 ``load_contract`` 直接构造且值非法时（编程错误，不静默降级）。
    """
    value = check.get("present_when_missing", DEFAULT_PRESENT_WHEN_MISSING)
    if value not in VALID_PRESENT_WHEN_MISSING:
        raise ValueError(
            f"check {check.get('id', '?')!r} 的 present_when_missing 非法: {value!r}"
        )
    return value


def _reject_non_dict_check(check: object) -> None:
    """checks 数组里出现非对象元素 -> meta-error rc=2（两个校验器共用）."""
    if not isinstance(check, dict):
        print(f"[META-ERROR] 契约 schema 错误: checks 里出现非对象元素: {check!r}")
        sys.exit(2)


def _validate_enum_field(
    checks: list[dict],
    field: str,
    valid: tuple,
    default: object,
    *,
    default_is_valid: bool,
    why: str,
    default_note: str = "",
) -> None:
    """表驱动校验一个"枚举值 + 缺省"字段；任一非法 -> meta-error rc=2.

    ``present_when_missing`` 与 ``parse_as`` 的校验逻辑逐行同构（只有字段名、合法值集、
    缺省、报错末尾的理由不同），故用一个实现覆盖，避免两份会各自漂移的拷贝。

    ★ 为什么两个字段共用表驱动，但**报错文案仍逐字段定制**：`why` / `default_note`
    由调用方给，且都是面向"读报错的人"的完整句子。为去重而把它们压成一句通用文案会
    丢掉"这个字段写错具体会怎样"的信息 —— 那正是这两个字段存在的理由（见各自调用点）。

    Parameters
    ----------
    checks : list[dict]
        契约的 ``checks`` 数组。
    field : str
        字段名，如 ``"parse_as"``。
    valid : tuple
        合法值集合（不含缺省）。
    default : object
        缺省值。
    default_is_valid : bool
        缺省值本身是否算合法（``present_when_missing`` 的 ``null`` 非法，
        ``parse_as`` 的 ``null`` 合法）。
    why : str
        这个字段写错会造成的**具体**后果（拼进报错信息）。
    default_note : str, optional
        对缺省值的补充说明（拼进报错信息）。
    """
    for check in checks:
        _reject_non_dict_check(check)
        if field not in check:
            continue
        value = check[field]
        if (default_is_valid and value is default) or value in valid:
            continue
        print(
            f"[META-ERROR] 契约 schema 错误: check '{check.get('id', '?')}' 的 "
            f"{field}={value!r} 非法；合法值只有 "
            f"{' / '.join(repr(v) for v in valid)}"
            f"（缺省={default!r}{default_note}）。{why}"
        )
        sys.exit(2)


def validate_present_when_missing(checks: list[dict]) -> None:
    """校验全部 check 的 ``present_when_missing``；任一非法 -> meta-error rc=2.

    ★ 为什么必须**显式报错**而不是静默当 ``"skip"``：本字段存在的全部意义就是
    让"结果文件缺失"能判红。一个笔误（``"FALSE"`` / ``true`` / ``1`` / 未知拼写）
    若被静默降级成缺省 ``"skip"``，门禁会**看起来在守**却完全没守 —— 正是
    「缺结果被读成通过」的原始缺陷，只是换到了配置层。

    报错文本点名 **check id** 与**非法值**，让人不必翻整个契约文件。
    """
    _validate_enum_field(
        checks,
        "present_when_missing",
        VALID_PRESENT_WHEN_MISSING,
        DEFAULT_PRESENT_WHEN_MISSING,
        default_is_valid=False,  # 显式 null 在这里是类型错（"我本该填一个值但没填"）
        why="不静默降级：本字段写错会让门禁看起来在守却完全没守。",
    )


def parse_as_of(check: dict) -> str | None:
    """返回该 check 的 ``parse_as`` 语义（已校验，只有 ``None`` / ``"json"``）.

    ``load_contract`` 已把非法值挡在 rc=2；这里再兜一层并把"键不存在"映射到
    :data:`DEFAULT_PARSE_AS`，故调用方读到的一定是合法值。

    Parameters
    ----------
    check : dict
        契约里的单条 check。

    Returns
    -------
    str | None
        ``None``（缺省，不做解析）或 ``"json"``。

    Raises
    ------
    ValueError
        契约被绕过 ``load_contract`` 直接构造且值非法时（编程错误，不静默降级）。
    """
    value = check.get("parse_as", DEFAULT_PARSE_AS)
    if value is not DEFAULT_PARSE_AS and value not in VALID_PARSE_AS:
        raise ValueError(f"check {check.get('id', '?')!r} 的 parse_as 非法: {value!r}")
    return value


def validate_parse_as(checks: list[dict]) -> None:
    """校验全部 check 的 ``parse_as``；任一非法 -> meta-error rc=2.

    与 :func:`validate_present_when_missing` 同族、同形态（共用
    :func:`_validate_enum_field`）：本字段写错会被静默当成"不做解析"，于是
    **声明了要校验却没校验** —— 门禁看起来在守却完全没守。

    ★ 显式 ``null`` 在这里是**合法**的（等于缺省），与 ``present_when_missing``
    刻意不同 —— 理由见模块顶部 :data:`DEFAULT_PARSE_AS` 处的注释。
    """
    _validate_enum_field(
        checks,
        "parse_as",
        VALID_PARSE_AS,
        DEFAULT_PARSE_AS,
        default_is_valid=True,  # 显式 null == 缺省，是本字段的合法写法
        why="不静默降级：声明了要校验却没校验，比不声明更坏。",
        default_note="，即不做解析",
    )


def load_contract(path: str) -> dict:
    """加载契约 JSON。缺失 / 解析失败 / 字段非法 → meta-error 退出码 2。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"[META-ERROR] 契约文件缺失: {path} — 门禁无法运行，请确认已建立 drift-contract.json")
        sys.exit(2)
    except json.JSONDecodeError as exc:
        print(f"[META-ERROR] 契约 JSON 解析失败: {exc}")
        sys.exit(2)

    if "checks" not in data or not isinstance(data["checks"], list):
        print(f"[META-ERROR] 契约 schema 错误: 缺 'checks' 数组（got keys: {list(data.keys())}）")
        sys.exit(2)
    validate_present_when_missing(data["checks"])
    validate_parse_as(data["checks"])
    return data


def run_check_files(check: dict, repo_root: Path) -> str:
    """读 paths 列出的所有文件并合并成单一字符串返回（UTF-8 容错）。

    任一文件缺失 -> 输出追加 `<missing:path>` 占位，让契约检查者能区分
    "读到但不符" vs "文件不存在"。这是 schema-level 不变量，调用方
    可选择如何处理（默认 evaluate 把它当"含特殊 token"算 fail）。
    """
    paths = check.get("paths") or []
    chunks: list[str] = []
    for p in paths:
        full = repo_root / p
        try:
            content = full.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            chunks.append(f"<missing:{p}>")
            continue
        except OSError as exc:
            chunks.append(f"<read-error:{p}:{exc!r}>")
            continue
        chunks.append(f"--- {p} ---\n{content}\n")
    return "\n".join(chunks)


def _is_all_missing(output: str) -> bool:
    """True iff *no* referenced file was actually read.

    ``run_check_files`` emits a ``--- <path> ---`` header for every file it
    successfully read (even an empty one); only absent / unreadable paths
    become ``<missing:...>`` / ``<read-error:...>`` placeholders. So "all
    missing" is precisely "output carries no ``--- `` content header", which
    is stricter than substring-stripping ``<missing:>`` (that could be fooled
    by literal text inside a real file). Mirrors verify.sh's [DOWN]
    (fail-open) behaviour so the gate stays green in CI where gitignored
    files (e.g. run-windows.env) are not checked out; the guard still fires
    wherever a referenced file exists.

    ★ 措辞重要：这是"**全部**缺失"，不是"部分缺失"。部分缺失时 output 里仍有
    已读文件的 ``--- `` 头，故返回 False，``<missing:path>`` 占位符照旧进入内容
    参与正则 —— 那条路径的语义自始未变。
    """
    return "--- " not in output


def _fail_on_missing_detail(check: dict, output: str, mode: str) -> tuple[bool, str]:
    """``present_when_missing="fail"`` 且所有引用文件都没有时，构造判红结果.

    ★ 这条分支专门堵住原始 fail-open：以前"文件不存在"会产出
    ``[SKIP] ... fail-open（不阻断）`` 并被读成"通过"。对评测结果文件而言那不是
    通过，而是**没测过** —— 二者在报告里必须能区分。

    Returns
    -------
    tuple[bool, str]
        ``(passed=False, detail)``。detail 点名缺失文件，并说明"缺结果 = 没测 ≠
        通过"；是否**阻断**仍由既有 severity + mode 决定（只有 block+closed 才
        rc=1），故这里是 [BLOCK]/[WARN] 的措辞差异，不是退出码差异。
    """
    ref = check.get("decision_ref", "?")
    desc = check.get("description", "")
    paths = check.get("paths") or []
    missing = sorted({p for p in paths if f"<missing:{p}>" in output})
    unreadable = sorted({p for p in paths if f"<read-error:{p}:" in output})
    missing_repr = ", ".join(missing) if missing else "(契约未列出 paths)"
    head = _severity_head(check, mode)
    tail = (
        "缺结果 = 没测，≠ 通过：该结果文件未生成/未产出，检查**没有**被执行过，"
        "故判红而不是 SKIP。"
    )
    lines = [
        f"{head} 引用的结果文件全部缺失（present_when_missing=fail）: {ref}",
        f"       description: {desc}",
        f"       paths: {missing_repr}",
        f"       {tail}",
    ]
    if unreadable:
        lines.append(f"       unreadable: {', '.join(unreadable)}")
    if mode == "open":
        lines.append("       mode=open：不阻断（rc=0），但该项判红必须被看见")
    return False, "\n".join(lines)


def _json_parse_failures(check: dict, output: str, repo_root: Path) -> list[tuple[str, str]]:
    """逐文件尝试 ``json.loads``，返回 ``[(path, 原因)]``（空列表 = 全部可解析）.

    ★ 判据是**逐文件解析入库产物**，不是解析 ``run_check_files`` 的合并串。
    理由（这是本函数唯一需要解释的设计选择）：

    1. 合并串前面掺了 ``--- <path> ---`` 头，本身**永远不是**合法 JSON ——
       解析它必然失败，等于给每条 check 无条件判红；
    2. 逐文件才能**点名是哪个文件**坏了，而多 path 的 check 需要这个信息；
    3. 逐文件天然满足"任一声明为 json 的文件不可解析 ⇒ 判红"，不依赖合并顺序。

    缺失 / 读不动的文件**不在这里判**（返回空、跳过）—— 那是
    ``present_when_missing`` 的既有职责，本条只管"文件在、内容不是 JSON"。
    两者是并列的一层，互不覆盖。

    Parameters
    ----------
    check : dict
        契约里的单条 check。
    output : str
        ``run_check_files`` 的合并输出（用于复用它的 ``<missing:>`` /
        ``<read-error:>`` 占位符判定，避免这里重复读盘口径不一致）。
    repo_root : Path
        仓库根。

    Returns
    -------
    list[tuple[str, str]]
        ``(相对路径, 失败原因)``；空列表表示所有存在的文件都是合法 JSON。
    """
    failures: list[tuple[str, str]] = []
    for rel_path in check.get("paths") or []:
        if f"<missing:{rel_path}>" in output or f"<read-error:{rel_path}:" in output:
            continue  # 缺失/不可读归 present_when_missing 管，本层不越权
        full = repo_root / rel_path
        try:
            text = full.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            failures.append((rel_path, f"读取失败: {exc!r}"))
            continue
        try:
            json.loads(text)
        except json.JSONDecodeError as exc:
            failures.append(
                (rel_path, f"不是合法 JSON（{exc.msg}，行 {exc.lineno} 列 {exc.colno}）")
            )
        except RecursionError:
            # ★ 实测踩到：极深嵌套（如 `[[[[…` 20 万个 `[`）会让 CPython 的 JSON
            # 解码器爆栈 —— 那是 `RecursionError`，**不是** JSONDecodeError。
            # 不接住它 = 未捕获异常 ⇒ 门禁整个 crash，CI 拿到的是 traceback 而不是
            # 一份报告。**崩溃比判红更坏**：判红至少还留下 JSON/文本报告，崩溃会让
            # 下游什么也读不到。故这里把它归成"不可解析"，走同一条 fail-closed 判红。
            failures.append(
                (rel_path, "不是合法 JSON（嵌套过深，解码时超出 Python 递归上限）")
            )
        except ValueError as exc:
            # json.loads 对超长数字字面量等还会抛别的 ValueError 子类；一并收成
            # "不可解析"，同样不静默放过。
            failures.append((rel_path, f"不是合法 JSON（{exc!r}）"))
    return failures


def _fail_on_unparsable_detail(
    check: dict, failures: list[tuple[str, str]], mode: str
) -> tuple[bool, str]:
    """``parse_as="json"`` 且内容不可解析时，构造判红结果.

    ★ 为什么"文件在但内容不是一份完整卡片"必须判红：评测的**结果产物**是
    机器生成的 JSON。半截文件（生成中途被杀 / 磁盘写满 / 复制截断）通常**不是**
    JSON 解析错误，而是一份"看起来有内容、但后面的断言从没读过"的前缀 ——
    旧设计里它会让门禁判绿（W1：时序卡截到 16800 B ⇒ rc=0，而更短的 16761 B
    反而 rc=1）。对评测而言那不是"通过"，而是**没测完**，与"没测"同等处理。

    Returns
    -------
    tuple[bool, str]
        ``(passed=False, detail)``。点名坏掉的文件与原因；是否**阻断**仍由既有
        severity + mode 决定（只有 block+closed 才 rc=1）。
    """
    ref = check.get("decision_ref", "?")
    desc = check.get("description", "")
    head = _severity_head(check, mode)
    lines = [
        f"{head} 引用的结构化产物不可解析（parse_as=json）: {ref}",
        f"       description: {desc}",
    ]
    for rel_path, reason in failures:
        lines.append(f"       {rel_path}: {reason}")
    lines.append(
        "       文件在但内容不是一份完整卡片 ⇒ 视同没测（不是通过）："
        "产物未写完/被截断时，后面的断言从没读过它。"
    )
    if mode == "open":
        lines.append("       mode=open：不阻断（rc=0），但该项判红必须被看见")
    return False, "\n".join(lines)


def evaluate(
    check: dict, output: str, mode: str, repo_root: Path
) -> tuple[bool, str]:
    """比对输出与期望正则，返回 (passed, detail)。

    ``parse_as="json"``（可选）在正则之前**再加一层 fail-closed**：见
    :func:`_fail_on_unparsable_detail`。

    ★ ``repo_root`` 是**必需**参数（曾为可选、缺省 ``None``）。**不要**把它改回
    可选：解析层需要它去读 paths 列出的产物，而"没有 repo_root 就跳过解析"等于
    **在那些调用路径上把整层守卫静默删掉** —— 与它要防的 fail-open 是同一个病
    （「守卫存在、有测试，但它不在这条路径上」）。实测旧缺省：同一输入
    ``evaluate(check, out, "closed", root)`` 判红，而不传 repo_root 时 ``passed=True``。
    宁可让调用点显式传参、把依赖暴露在签名上，也不要留一个会静默降级的缺省。
    """
    cid = check.get("id", "?")
    ref = check.get("decision_ref", "?")
    desc = check.get("description", "")
    pattern = check.get("pattern", "")
    not_pattern = check.get("not_pattern")

    # Every referenced file absent (CI / clean checkout): historically a
    # fail-open [SKIP]. `present_when_missing="fail"` opts this single check
    # into failing instead — "the result file was never produced" means the
    # check never ran, which is not a pass.
    if _is_all_missing(output):
        if present_when_missing_of(check) == "fail":
            return _fail_on_missing_detail(check, output, mode)
        return True, f"[SKIP] {cid} 引用文件均缺失，fail-open（不阻断）: {ref}"

    # 建立在 present_when_missing 之上的**另一层** fail-closed：文件存在，但
    # 声明了 parse_as="json" 而内容不是合法 JSON ⇒ 视同没测。旧设计只在“文件
    # 全缺失”时判红，文件一存在就跳过，于是半截产物（W1）会被放行。
    # ★ 这里**不再**有 `and repo_root is not None` 守卫 —— 见上面 docstring。
    if parse_as_of(check) == "json":
        parse_failures = _json_parse_failures(check, output, repo_root)
        if parse_failures:
            return _fail_on_unparsable_detail(check, parse_failures, mode)

    _flags = re.MULTILINE
    matched = bool(re.search(pattern, output, _flags)) if pattern else False
    not_matched = (not bool(re.search(not_pattern, output, _flags))) if not_pattern else True
    passed = matched and not_matched

    if passed:
        return True, f"[OK]   {cid} 符合决策书: {ref}"

    severity = check.get("severity", "warn")
    paths = check.get("paths") or []
    paths_repr = ", ".join(paths) if len(paths) <= 4 else f"{len(paths)} files"

    head = _severity_head(check, mode)
    why_parts = []
    if pattern and not matched:
        why_parts.append(f"pattern=/{pattern}/ 未匹配")
    if not_pattern and not not_matched:
        why_parts.append(f"not_pattern=/{not_pattern}/ 不应匹配但匹配了")
    if not why_parts:
        why_parts.append("schema 错误（缺 pattern）")
    why = "; ".join(why_parts)

    if mode == "open":
        return False, (
            f"{head} 运行态≠决策态（open 模式不阻断）: {ref}\n"
            f"       description: {desc}\n"
            f"       paths: {paths_repr}\n"
            f"       {why}"
        )
    # closed 模式
    if severity == "block":
        return False, (
            f"{head} 运行态≠决策态 且 severity=block: {ref}\n"
            f"       description: {desc}\n"
            f"       paths: {paths_repr}\n"
            f"       {why}"
        )
    return False, (
        f"{head} 不符 但 severity={severity}: {ref}\n"
        f"       description: {desc}\n"
        f"       {why}"
    )


class RuntimeProbeError(RuntimeError):
    """runtime 阶段无法取得 VLM props 真值：probe 刷新失败。

    gate 假设 probe 已先于自己跑过（顺序保护）。若必须刷新却刷新失败，
    这是显式错误，绝不静默 fallback 成 SKIP —— 否则会放过真实漂移。
    """


def _props_referenced_by_runtime(checks: list[dict], phase: str) -> bool:
    """True iff 任一会被执行的 runtime 检查引用 VLM props 文件。

    *phase* 为 ``"runtime"`` 只看 runtime 检查；``"all"`` 看全部；``"static"``
    永不命中（runtime 检查在 static 阶段被跳过）。用于决定是否需要在 runtime
    阶段前触发 probe 刷新。
    """
    for c in checks:
        if phase != "all" and c.get("phase", "static") != phase:
            continue
        if VLM_PROPS_REL in (c.get("paths") or []):
            return True
    return False


def _refresh_vlm_props_if_needed(repo_root: Path) -> None:
    """顺序保护：runtime 阶段前，确保 VLM props 快照新鲜。

    逻辑：
      - 文件存在且 mtime 距现在 < ``VLM_PROPS_STALE_SECONDS`` → 视为新鲜，跳过
        （避免每次 gate 都打一次 llama /props）。
      - 缺失或过期 → 用 ``sys.executable`` 调 ``scripts/vlm_runtime_probe.py``
        生成/刷新（不硬编码 venv 路径）。
      - probe 返回非 0 → **显式 raise**，绝不静默 fallback 成 SKIP。

    纯 runtime 阶段的例外动作；static 阶段不调用本函数（见 ``run_all`` 守卫）。
    """
    props = repo_root / VLM_PROPS_REL
    need_refresh = False
    if not props.exists():
        need_refresh = True
        logger.info("VLM props 缺失 (%s) — 触发 probe 生成", props)
    else:
        age = (datetime.now() - datetime.fromtimestamp(props.stat().st_mtime)).total_seconds()
        if age > VLM_PROPS_STALE_SECONDS:
            need_refresh = True
            logger.info("VLM props 过期 (age=%.0fs > %.0fs) — 触发 probe 刷新", age, VLM_PROPS_STALE_SECONDS)
    if not need_refresh:
        logger.info("VLM props 新鲜，跳过 probe：%s", props)
        return

    probe = repo_root / VLM_PROBE_REL
    if not probe.exists():
        raise RuntimeProbeError(
            f"[RUNTIME-PROBE-MISSING] {probe} 不存在，无法为 runtime 检查刷新 {VLM_PROPS_REL}。"
            f"请在 gate 之前先运行 vlm_runtime_probe.py，或直接提供该文件。"
        )
    logger.info("调用 %s 刷新 %s", probe.name, VLM_PROPS_REL)
    proc = subprocess.run(
        [
            sys.executable,
            str(probe),
            "--base-url", VLM_PROBE_BASE_URL,
            "--out", str(props),
            "--wait", str(VLM_PROBE_WAIT_SECONDS),
        ],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if proc.returncode != 0:
        probe_err = (proc.stdout or "") + (proc.stderr or "")
        raise RuntimeProbeError(
            f"[RUNTIME-PROBE-FAILED] probe 刷新 {VLM_PROPS_REL} 失败 (rc={proc.returncode})；"
            f"runtime 检查不能 SKIP 兜底。请确认 llama 已在 {VLM_PROBE_BASE_URL} 启动。\n"
            f"{probe_err.strip()[-800:]}"
        )
    logger.info("probe 刷新成功：%s", props)


def run_all(contract: dict, phase: str, mode: str, repo_root: Path) -> dict:
    """跑所有适用 phase 的检查，返回结构化结果。"""
    checks = contract.get("checks", [])
    results: list[dict] = []
    any_block_fail = False
    ran = 0

    # F4-P1a 顺序保护：runtime 阶段若引用了 VLM props，先确保快照新鲜（缺失/过期
    # 则自动跑 probe）。static 阶段不经过此守卫，保持独立。probe 失败会显式 raise。
    if (phase == "runtime" or phase == "all") and _props_referenced_by_runtime(checks, phase):
        _refresh_vlm_props_if_needed(repo_root)

    for c in checks:
        cphase = c.get("phase", "static")
        if phase != "all" and cphase != phase:
            continue
        ran += 1
        out = run_check_files(c, repo_root)
        passed, detail = evaluate(c, out, mode, repo_root)
        results.append(
            {
                "id": c.get("id"),
                "phase": cphase,
                "severity": c.get("severity", "warn"),
                "decision_ref": c.get("decision_ref"),
                "description": c.get("description"),
                "expected_regex": c.get("pattern"),
                "paths": c.get("paths"),
                # Added fields only — every pre-existing key above keeps its
                # name and meaning (drift_gate_smoke_test.py + CI depend on
                # ran_at / total_checks / block_failures / warn_failures /
                # any_block_fail / results[].passed).
                "present_when_missing": present_when_missing_of(c),
                "parse_as": parse_as_of(c),
                "passed": passed,
                "detail": detail,
            }
        )
        if not passed and c.get("severity") == "block" and mode == "closed":
            any_block_fail = True

    return {
        "ran_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "phase": phase,
        "mode": mode,
        "source_of_truth": contract.get("source_of_truth", "决策/"),
        "contract_version": contract.get("version"),
        "total_checks": ran,
        "block_failures": sum(1 for r in results if not r["passed"] and r["severity"] == "block"),
        "warn_failures": sum(1 for r in results if not r["passed"] and r["severity"] != "block"),
        "results": results,
        "any_block_fail": any_block_fail,
    }


def format_text(report: dict) -> str:
    lines = [
        f"# Drift Gate 报告  phase={report['phase']} mode={report['mode']}",
        f"# 真值源: {report['source_of_truth']}  contract_version={report['contract_version']}",
        f"# ran_at={report['ran_at']}  total={report['total_checks']}  block_fail={report['block_failures']}  warn_fail={report['warn_failures']}",
        "",
    ]
    for r in report["results"]:
        lines.append(r["detail"])
    lines.append("")
    lines.append(
        f"# 共跑 {report['total_checks']} 项；mode={report['mode']}；block 级失败={report['any_block_fail']}"
    )
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Drift Gate 执行器（决策书→契约→运行态校验）")
    ap.add_argument("--contract", required=True, help="契约 JSON 路径（如 config/drift-contract.json）")
    ap.add_argument(
        "--phase",
        choices=["static", "runtime", "all"],
        default="all",
        help="只跑该阶段（static=配置/代码, runtime=运行实例）",
    )
    ap.add_argument(
        "--mode",
        choices=["open", "closed"],
        default="open",
        help="open=不符仅告警；closed=block 级不符则失败退出",
    )
    ap.add_argument("--report", default=None, help="把报告写到文件（人类可读文本）")
    ap.add_argument("--json", action="store_true", help="以 JSON 格式输出报告（机器可读）")
    ap.add_argument(
        "--repo-root",
        default=None,
        help="仓库根路径（默认：脚本所在位置的父目录）",
    )
    ap.add_argument(
        "--history-dir",
        default=None,
        help="限定方式：每次 run 写一份 <UTC-ISO>.json 到这个目录（默认 logs/drift-gate-history/），不覆盖。传 --no-history 关闭。",
    )
    ap.add_argument("--no-history", action="store_true", help="不写历史报告（仅 stdout/--report）")
    args = ap.parse_args()

    repo_root = Path(args.repo_root) if args.repo_root else Path(__file__).resolve().parent.parent

    # F4-P1a: probe 刷新诊断需要可见。仅当尚无 handler 时配置一次（幂等）。
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )

    contract = load_contract(args.contract)
    try:
        report = run_all(contract, args.phase, args.mode, repo_root)
    except RuntimeProbeError as exc:
        # 显式错误：runtime 真值拿不到，gate 不能假装通过。rc=3 区别于
        # block-fail(1) 与 meta-error(2)。
        print(f"[RUNTIME-PROBE-ERROR] {exc}", file=sys.stderr)
        return 3

    if args.json:
        out = json.dumps(report, ensure_ascii=False, indent=2)
        print(out)
        if args.report:
            Path(args.report).write_text(out + "\n", encoding="utf-8")
    else:
        # P0-1: bind `out` (not a separate `text`) so the history write at
        # L263 can reuse it. Previously the non-`--json` branch bound `text`
        # while L263 referenced `out`, raising UnboundLocalError on any
        # invocation without --json AND without --no-history.
        out = format_text(report)
        print(out)
        if args.report:
            Path(args.report).write_text(out + "\n", encoding="utf-8")

    if args.mode == "closed" and report["any_block_fail"]:
        return 1
    if not args.no_history:
        # Append a timestamped copy of the report to <history-dir>/. This
        # lets operators see "drift_gate said X at 09:00, Y at 14:00" later
        # without re-running the gate. Default <repo>/logs/drift-gate-history/.
        history_dir = (
            Path(args.history_dir).resolve()
            if args.history_dir
            else (repo_root / "logs" / "drift-gate-history")
        )
        try:
            history_dir.mkdir(parents=True, exist_ok=True)
            # ran_at is already ISO; replace ":" with "-" for Windows-safe filename.
            ts_safe = report["ran_at"].replace(":", "-")
            history_path = history_dir / (ts_safe + ".json")
            history_path.write_text(out, encoding="utf-8")
        except OSError as exc:
            print(f"[WARN] failed to write history report: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
