#!/usr/bin/env python3
# ruff: noqa: RUF001, RUF002, RUF003
"""验证仪式运行器 —— 一条命令跑完标准仪式，输出**天生就是台账里的一行**（工单 #162）.

规格：`doc/specs/draft-test-evidence-baseline.md` §4.2（验证仪式）+ `doc/standards/test-baseline.md`（台账纪律）。

为什么要有这个运行器
--------------------
台账（`doc/standards/test-baseline.md`）已经把纪律写下来了，但**纪律靠人记就一定会漏**：
后人跑完一轮实测，仍然要手工把六条命令的结果抄成表格，且极易漏掉「测量时间」
与「真机/离线」两列 —— 而这两列正是本项目两次事故的判别条件。

本运行器把「跑一轮标准仪式」变成一条命令，并让输出**直接就是台账行**。

★ 三条不可妥协的判据（每条都对应一次真实事故）
-----------------------------------------------
1. **前置缺失 ⇒ 报「无法测量」，不报数字。**
   实测（2026-09-22）：`audit-frontend-residue.mjs` 在静态服务器服务**错目录**时报
   **139 处死引用**，真值 **0** —— 差一个数量级、方向相反，而脚本不报错。
   机制：脚本靠 CDP 载入 `http://127.0.0.1:8123/index.html`；服务错目录 ⇒ 404 ⇒
   页面空白 ⇒ 每个 id 都判「死」。
   ⇒ 本运行器因此**先验前置**：端口在听还不够，还要核对**served 内容与本仓库
   static 目录一致**（hash），不一致同样是「无法测量」。**并且在跑之前就拦下** ——
   前置不满足时那条命令**根本不执行**。

2. **判据取自解析出的数字，不取自退出码。**
   `audit-frontend-residue.mjs` 末尾无条件 `process.exit(0)` ⇒ **rc 恒为 0**，
   完全无区分力。「rc==0 即绿」会把 139 读成 PASS。故本运行器逐项解析**语义判据**
   （死引用数、`block_fail`、`BROKEN` 数、`recall@k` 与分母）。

3. **「没跑」、「测不了」、「测了没通过」是三件不同的事。**
   退出码：`0` **全仪式**全绿 / `1` 有 FAIL / `2` **未跑完或有项无法测量**。
   * 只跑了子集 ⇒ 未跑的项被**点名**，退出码 `2`，不出现 ALL GREEN；
   * 一项都没选中（如 `--only ","`）⇒ **显式报错**，不是静默跑空；
   * ⇒ **没有任何路径会因为「没跑」而返回 0**。

   ★ 这条不是靠一个 `if` 保证的：`run_ritual` 返回的 :class:`Run` **自带覆盖范围**
   （`expected_item_ids` / `uncovered_ids` / `is_complete`）——
   初版返回裸 `list[Result]`，它**无法知道自己缺了什么**，于是把「没跑」说成了「通过」
   （`--only <子集>` 曾打印 ALL GREEN + exit 0，而三项真机项压根没跑）。
   那与 139 假数字是同一个病，故把覆盖范围做进了**类型**里。

用法
----
    # 完整仪式（真机项不可测时会明确标出，离线项照常跑）
    python scripts/verify_ritual.py

    # 只看结论与台账行 / 机器可读输出（供后续四票复用）
    python scripts/verify_ritual.py --summary
    python scripts/verify_ritual.py --json

    # 只跑离线项（无服务环境；**这不会报 ALL GREEN**，未跑项会被点名）
    python scripts/verify_ritual.py --only api-contract,decision-events,golden-recall

前置与只读性
------------
* 本运行器**只读探针**：不修改任何被测对象。对唯一会写盘的子命令（drift_gate
  默认写历史报告）显式传 `--no-history`。
* 子命令若本就依赖 `.env` 类文件里的密钥，运行器只在**进程内**注入，
  **只回报「载入了哪些键名」、绝不回报值**，且**不覆盖调用者已有的环境变量**。
* 真机/离线标记与测量时间由运行器自己打，不靠人填。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# --- 状态（三值，不是布尔） ------------------------------------------------
# 「无法测量」是与 PASS / FAIL **并列**的第三种状态：它既不表示通过，也不表示
# 回归 —— 它表示「这一项本轮没有被真正测到」。工单 #162 要求前置缺失时报的正是
# 这个词（139 假数字的根因是「明明没测到，却报了一个数字」）。
STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_UNMEASURABLE = "无法测量"

# --- 真机/离线标记 ----------------------------------------------------------
MODE_LIVE = "真机"
MODE_OFFLINE = "离线"

# --- 前置条件 id -----------------------------------------------------------
CLAIM_LLAMA = "llama-server"
CLAIM_WEBINFER = "webinfer"
CLAIM_VOICE_CLONE = "voice-clone"
CLAIM_WEBUI = "webui"
CLAIM_STATIC_SERVER = "static-server:8123"
CLAIM_CHROME = "chrome"
CLAIM_KEY_IN_ENV = "SILICONFLOW_API_KEY"

# --- 仪式项的 id -----------------------------------------------------------
ITEM_STACK = "stack"
ITEM_DRIFT = "drift-runtime"
ITEM_RESIDUE = "residue"
ITEM_CONTRACT = "api-contract"
ITEM_EVENTS = "decision-events"
ITEM_RECALL = "golden-recall"

STATIC_DIR_REL = "services/webui/src/joy_interaction_webui/static"
STATIC_SERVER_PORT = 8123
EVENTS_DIR_REL = "logs/events"
ENV_FILE_REL = "services/scripts/run-windows.env"

# ★ 钉住的分母（**独立于** golden 集文件的真值源）。
#
# 为什么不从 `golden_recall_set.json` 现读现算：那会让判据变成**同义反复** ——
# 分母被悄悄改动时，期望值会跟着改，检查恒通过，而台账纪律 5 要防的正是
# 「分母不一致导致跨变体比较静默失效」。故此处写死一个**外部真值**，
# 由 `test_pinned_denominator_matches_the_golden_set_on_disk` 负责在 golden 集
# 真的变动时把它拽红，逼人显式改这个数。
GOLDEN_EXPECTED_DENOMINATOR = 24

# 注释/行内不被当作检查结果的行（避免把说明文字里的 "139" 当成测量值）。
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

# ★ 允许执行的程序**白名单**（本仪式的全部子命令都只可能来自这里）。
#
# 为什么要有它：`execute()` 会跑子进程，而「跑什么」由**注册表**决定、不由外部输入决定。
# 白名单把这个事实**在代码里也成立**，而不是只存在于「调用方都守规矩」的假设里 ——
# 将来若有人把某个 argv 接到别处（配置、环境变量、命令行），这里会先拦下来。
# 这也让 `sast_command_injection_os_system` 不必靠 ignore 规则掩盖（本仓纪律：
# 优先**改写消除触发条件**，而不是加 ignore；见 `.deepsecignore` 的维护纪律段）。
_ALLOWED_EXECUTABLES = frozenset(
    {"python", "python.exe", "python3", "python3.exe", "node", "node.exe"}
)


def _assert_executable_allowed(argv: Sequence[str]) -> list[str]:
    """校验 argv 的首个元素在白名单内，返回规范化后的参数列表.

    Raises
    ------
    ValueError
        argv 为空，或其可执行文件不在白名单内。
    """
    parts = [str(a) for a in argv]
    if not parts:
        raise ValueError("拒绝执行：argv 为空")
    # 取 basename 并统一小写，兼容 Windows 的 `C:\\...\\python.exe` 形态。
    program = Path(parts[0].replace("\\", "/")).name.lower()
    if program not in _ALLOWED_EXECUTABLES:
        raise ValueError(
            f"拒绝执行 {parts[0]!r}：不在本仪式的可执行白名单内 "
            f"（{', '.join(sorted(_ALLOWED_EXECUTABLES))}）"
        )
    return parts


@dataclass(frozen=True)
class Precondition:
    """一项前置条件：跑之前必须先成立，否则结果是假的."""

    claim_id: str
    description: str


@dataclass(frozen=True)
class Command:
    """一条可复制的命令."""

    argv: tuple[str, ...]
    display: str
    cwd: Path | None = None
    env_file: str | None = None


@dataclass(frozen=True)
class Item:
    """仪式里的一项."""

    item_id: str
    title: str
    mode: str
    command: Command
    preconditions: tuple[Precondition, ...] = ()
    judge_hint: str = ""
    # 「这一项测的是什么口径」—— 台账纪律 5 要求口径可见。
    # 例：决策事件项读的是**哪一天**的事件文件；不写清楚，跨轮比较会静默失真。
    scope_note: str = ""


@dataclass
class Result:
    """一项的实测结果（**台账行**的数据来源）."""

    item_id: str
    title: str
    mode: str
    command: Command
    status: str
    measurement: str | None
    detail: str
    observed_at: datetime
    duration_ms: int = 0
    tags: tuple[str, ...] = field(default_factory=tuple)
    # 口径说明（继承自 Item）—— 台账行要能自证「测的是什么范围」。
    scope_note: str = ""


@dataclass
class Run:
    """一整轮仪式的**证据**：逐项结果 + **本轮覆盖了哪些项**.

    ★ 为什么不能只返回 ``list[Result]``
    ----------------------------------
    「一轮是否全绿」是**关于覆盖范围**的判断，而一个结果列表**无法知道自己缺了什么** ——
    `run_ritual` 传进去 3 项就只回得来 3 项，它无从表达「完整仪式还有另外 3 项没跑」。

    初版正是这个形状，于是 `--only <子集>` 报出「✅ ALL GREEN（本轮的每一项都真跑过
    且通过）」，退出码 0 —— 而栈探活/残留审计/运行时门禁**压根没跑**。
    这与本票要消灭的病（139 假数字：**没测到却给了一个数字**）是**同一个病**：
    把「没跑」说成了「通过」。故把覆盖范围做成**类型的一部分**，而不是一句注释里的承诺。
    """

    results: list[Result]
    # 完整仪式应包含的项 id（用于判断本轮是否跑全）
    expected_item_ids: tuple[str, ...] = ()

    def __iter__(self):
        """允许像结果列表一样迭代（调用方多数只关心逐项结果）."""
        return iter(self.results)

    def __len__(self) -> int:
        """结果条数（便于 `len(run)` 直接用于「子集 vs 全部」的比较）."""
        return len(self.results)

    @property
    def covered_ids(self) -> tuple[str, ...]:
        """本轮实际产出结果（无论 PASS/FAIL/无法测量）的项 id."""
        return tuple(r.item_id for r in self.results)

    @property
    def uncovered_ids(self) -> tuple[str, ...]:
        """完整仪式里**本轮压根没选/没跑**的项 id（按完整仪式顺序）."""
        covered = set(self.covered_ids)
        return tuple(i for i in self.expected_item_ids if i not in covered)

    @property
    def is_complete(self) -> bool:
        """本轮是否覆盖了完整仪式的所有项."""
        return bool(self.expected_item_ids) and not self.uncovered_ids


# ---------------------------------------------------------------------------
# 前置探针（默认实现；测试注入假探针）
# ---------------------------------------------------------------------------


def _probe_local(port: int, path: str = "/health", timeout: float = 3.0) -> tuple[bool, str]:
    """探**本机回环**上的一个路径，返回 ``(是否 200, 说明)``.

    ★ URL 由**端口号与固定路径拼出**，不接受调用方传入的完整 URL：
    本运行器只探本机服务，任何「可传任意 URL」的形态都是不必要的攻击面
    （这也是它能对 SSRF 类规则自证清白的原因 —— 见 `_allowed_executables` 的同理注释）。
    """
    if not isinstance(port, int) or not 1 <= port <= 65535:
        return False, f"非法端口：{port!r}"
    url = f"http://127.0.0.1:{port}{path}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 - 仅本机回环
            return resp.status == 200, f"HTTP {resp.status}"
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001 - 探针失败原因需回显给人
        return False, f"{type(exc).__name__}: {exc}"


def _static_server_claim(port: int = STATIC_SERVER_PORT) -> tuple[bool, str]:
    """★ 静态服务器前置 —— 端口在听**还不够**.

    必须核对 served 的 `index.html` 与本仓库 static 目录里的**内容一致**。
    历史事故正是「端口在听、但服务的是别的目录」⇒ 404/空白 ⇒ DOM=0 ⇒ 报 139 假数字。
    """
    local = REPO_ROOT / STATIC_DIR_REL / "index.html"
    if not local.is_file():
        return False, f"本仓库 static/index.html 不存在：{local}（无法核对服务内容）"
    local_digest = sha256(local.read_bytes()).hexdigest()

    url = f"http://127.0.0.1:{port}/index.html"
    try:
        with urllib.request.urlopen(url, timeout=3.0) as resp:  # noqa: S310 - 仅本机回环
            if resp.status != 200:
                return False, f"{url} 返回 HTTP {resp.status}（静态服务器不在位或服务错目录）"
            served = resp.read()
    except urllib.error.HTTPError as exc:
        return (
            False,
            f"{url} 返回 HTTP {exc.code} —— 端口在听但服务的不是本仓库 static 目录"
            "（这正是 139 假数字的成因）",
        )
    except Exception as exc:  # noqa: BLE001 - 探针失败原因需回显给人
        return False, f"{url} 不可达：{type(exc).__name__}: {exc}"

    if sha256(served).hexdigest() != local_digest:
        return (
            False,
            f"{url} 在跑，但 index.html 内容 hash 与本仓库 static/index.html 不符"
            "（服务错目录 ⇒ 审计会给出完全反向的假数字）",
        )
    return True, f"{url} 内容 hash 与本仓库一致"


def _chrome_present() -> tuple[bool, str]:
    """残留审计靠 CDP 驱动 Chrome —— 没有 Chrome 就没有数字."""
    candidates = [
        os.environ.get("CHROME_PATH", ""),
        "C:/Program Files/Google/Chrome/Application/chrome.exe",
        "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
    ]
    for cand in candidates:
        if cand and Path(cand).is_file():
            return True, cand
    return False, "未找到 chrome.exe（残留审计靠 CDP 驱动 Chrome，缺失即无数字）"


def _key_claim(env: dict[str, str] | None = None) -> tuple[bool, str]:
    """向量召回需要 `SILICONFLOW_API_KEY`（只回报有无，不回报值）."""
    source = env if env is not None else os.environ
    return bool(source.get("SILICONFLOW_API_KEY")), (
        "环境变量已设置" if source.get("SILICONFLOW_API_KEY") else "未设置（向量模式无法测量）"
    )


def default_probe(claim_id: str, env: dict[str, str] | None = None) -> tuple[bool, str]:
    """默认前置探针：返回 ``(成立与否, 说明)``.

    全部走 :func:`_probe_local`（端口 + 固定路径），不接受任意 URL。
    """
    if claim_id == CLAIM_LLAMA:
        return _probe_local(7060, "/health")
    if claim_id == CLAIM_WEBINFER:
        return _probe_local(8070, "/v1/models")
    if claim_id == CLAIM_VOICE_CLONE:
        return _probe_local(8985, "/health")
    if claim_id == CLAIM_WEBUI:
        return _probe_local(8099, "/api/tts/health")
    if claim_id == CLAIM_STATIC_SERVER:
        return _static_server_claim()
    if claim_id == CLAIM_CHROME:
        return _chrome_present()
    if claim_id == CLAIM_KEY_IN_ENV:
        return _key_claim(env)
    return False, f"未知前置条件：{claim_id}"


# ---------------------------------------------------------------------------
# env 文件（只注入，不回显值）
# ---------------------------------------------------------------------------


def load_env_file(path: Path, target: dict[str, str]) -> tuple[str, ...]:
    """把 env 文件里的键注入 ``target``，**不覆盖已存在的键**.

    Returns
    -------
        实际注入的**键名**（不含值 —— 值绝不回报，防止密钥进日志）。
    """
    if not path.is_file():
        return ()
    loaded: list[str] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key or key in target:
            continue
        target[key] = value
        loaded.append(key)
    return tuple(loaded)


# ---------------------------------------------------------------------------
# 仪式项定义（规格 §4.2）
# ---------------------------------------------------------------------------


def newest_event_file(events_dir: Path, service: str = "webui") -> Path | None:
    """最新的 ``<service>-<UTC 日>.jsonl``（无则 None）."""
    if not events_dir.is_dir():
        return None
    files = sorted(p for p in events_dir.glob(f"{service}-*.jsonl") if p.is_file())
    return files[-1] if files else None


def _py() -> str:
    """真实解释器路径（子进程实际执行用的；也是 `sys.executable`）."""
    return sys.executable


def interpreter_line() -> str:
    """一行「本轮实际用的解释器」.

    ★ 为什么必须显式报出来
    ----------------------
    台账里的「命令」列要**可复制**，故 Python 命令沿用仓库文档惯例写成 `python ...`
    （可移植）。但本机 `python` 是 Windows Store 占位存根、**静默失败**
    （见 `doc/environment-dsh.md` §2.2）—— 照抄那一列会得到「退出 0 但无输出」，
    与台账行所记的结果对不上。
    ⇒ 故实际解释器单独报一行：命令列保持可移植，解释器不含糊。
    """
    return f"解释器（本轮实际使用）：{_py()}"


def _item_stack(root: Path) -> Item:
    """规格 §4.2 第 1 步：服务栈探活（真机）."""
    return Item(
        item_id=ITEM_STACK,
        title="服务栈探活",
        mode=MODE_LIVE,
        command=Command(
            argv=(_py(), "services/scripts/verify-services.py"),
            display="python services/scripts/verify-services.py",
            cwd=root,
        ),
        judge_hint="判据 ALL GREEN（退出码 0）",
    )


def _item_drift(root: Path) -> Item:
    """规格 §4.2 第 2 步：运行时门禁（真机）."""
    return Item(
        item_id=ITEM_DRIFT,
        title="运行时门禁",
        mode=MODE_LIVE,
        command=Command(
            argv=(
                _py(),
                "scripts/drift_gate.py",
                "--contract",
                "config/drift-contract.json",
                "--phase",
                "runtime",
                "--mode",
                "closed",
                "--no-history",
            ),
            display=(
                "python scripts/drift_gate.py --contract config/drift-contract.json "
                "--phase runtime --mode closed --no-history"
            ),
            cwd=root,
        ),
        preconditions=(
            Precondition(CLAIM_LLAMA, "runtime 阶段需要 VLM 运行态快照（探 llama-server）"),
        ),
        judge_hint="判据 block_fail=0；rc=3 表示测不了（不得当作通过）",
    )


def _item_residue(root: Path) -> Item:
    """规格 §4.2 第 3 步：前端残留审计（真机；**需静态服务器 + Chrome**）."""
    return Item(
        item_id=ITEM_RESIDUE,
        title="前端残留审计（死引用）",
        mode=MODE_LIVE,
        command=Command(
            argv=("node", "scripts/audit-frontend-residue.mjs"),
            display="node scripts/audit-frontend-residue.mjs",
            cwd=root,
        ),
        preconditions=(
            Precondition(
                CLAIM_STATIC_SERVER,
                f"需 {STATIC_SERVER_PORT} 静态服务器，且服务本仓库 static 目录",
            ),
            Precondition(CLAIM_CHROME, "需 Chrome（脚本走 CDP 驱动）"),
        ),
        judge_hint="判据 死引用=0 ★ 该脚本 rc 恒为 0，只能按解析出的数字判",
    )


def _item_contract(root: Path) -> Item:
    """规格 §4.2 第 4 步：路由契约审计（离线）."""
    return Item(
        item_id=ITEM_CONTRACT,
        title="路由契约审计",
        mode=MODE_OFFLINE,
        command=Command(
            argv=("node", "scripts/audit-api-contract.mjs"),
            display="node scripts/audit-api-contract.mjs",
            cwd=root,
        ),
        judge_hint="判据 BROKEN=0",
    )


def _item_events(root: Path) -> Item:
    """规格 §4.2 第 5 步：live 决策事件读取（离线）.

    ★ 口径必须在行里可见：读的是**哪一天**的事件文件。事件文件按日切分，
    「最新一份」会随日期静默变化 —— 不写明的话，后人拿本行与另一轮比，
    可能比的是不同日的数据而毫无察觉（台账纪律 5）。
    """
    events_file = newest_event_file(root / EVENTS_DIR_REL)
    events_arg = str(events_file) if events_file else str(root / EVENTS_DIR_REL)
    scope = (
        f"口径：{events_file.name}（该日最新一份事件文件）"
        if events_file
        else f"口径：{EVENTS_DIR_REL}/ 整个目录（无 webui-*.jsonl 单日文件）"
    )
    return Item(
        item_id=ITEM_EVENTS,
        title="live 决策事件读取",
        mode=MODE_OFFLINE,
        command=Command(
            argv=(
                _py(),
                "-m",
                "decision_events",
                "--events-dir",
                events_arg,
                "--require-latency",
            ),
            display=(
                "python -m decision_events --events-dir "
                f"{_display_path(events_arg, root)} --require-latency"
            ),
            cwd=root / "services" / "webinfer",
        ),
        judge_hint="判据 有事件且 latency_ms/session_id 非空（--require-latency 缺数据即判红）",
        scope_note=scope,
    )


def _item_recall(root: Path) -> Item:
    """规格 §4.2 第 6 步：向量语义召回（离线；需 `SILICONFLOW_API_KEY`）."""
    return Item(
        item_id=ITEM_RECALL,
        title="向量语义召回（golden）",
        mode=MODE_OFFLINE,
        command=Command(
            argv=(_py(), "tools/eval_golden_recall.py", "--mode", "vector"),
            display="python tools/eval_golden_recall.py --mode vector",
            cwd=root / "services" / "memory-store",
            env_file=ENV_FILE_REL,
        ),
        preconditions=(Precondition(CLAIM_KEY_IN_ENV, "向量模式需要 SILICONFLOW_API_KEY"),),
        judge_hint=f"判据 recall@5 报告值 + 分母（期望分母 {GOLDEN_EXPECTED_DENOMINATOR}）",
    )


def build_items(root: Path = REPO_ROOT) -> tuple[Item, ...]:
    """构造仪式项（规格 §4.2 的六步，**顺序即规格表的顺序**）.

    评测类命令需要多目录的可导入路径，故由 :func:`_child_env` 统一注入 `PYTHONPATH`。
    """
    return (
        _item_stack(root),
        _item_drift(root),
        _item_residue(root),
        _item_contract(root),
        _item_events(root),
        _item_recall(root),
    )


def _display_path(path: str, root: Path) -> str:
    """把绝对路径显示成仓库相对路径（台账行要可读、可复制）."""
    try:
        return str(Path(path).resolve().relative_to(root.resolve())).replace("\\", "/")
    except ValueError:
        return path


# ---------------------------------------------------------------------------
# 判据（★ 逐项解析语义数字；不靠退出码）
# ---------------------------------------------------------------------------


def _clean(output: str) -> str:
    return _ANSI_RE.sub("", output)


def _judge_stack(rc: int, text: str) -> tuple[str, str | None, str]:
    """服务栈探活：判据 `ALL GREEN`（该脚本退出码与判据一致，可用作辅助）."""
    del rc
    if "ALL GREEN" in text:
        return STATUS_PASS, "ALL GREEN", "全服务探活通过 + 文本回归通过"
    m = re.search(r"(\d+)\s+FAILURES", text)
    detail = f"{m.group(1)} 项失败" if m else "未见到 ALL GREEN"
    return STATUS_FAIL, None, f"服务栈未全绿：{detail}"


def _judge_drift(rc: int, text: str) -> tuple[str, None | str, str]:
    """运行时门禁：判据 `block_fail=0`；rc=3 = 快照刷不出来（**测不了**，不是通过）."""
    if "RUNTIME-PROBE-FAILED" in text or "RUNTIME-PROBE-ERROR" in text or rc == 3:
        return (
            STATUS_UNMEASURABLE,
            None,
            "runtime probe 未能刷新运行态快照（llama 未起）—— runtime 检查不能 SKIP 兜底",
        )
    m = re.search(r"block_fail=(\d+)", text)
    if not m:
        return STATUS_FAIL, None, "无法判读：输出里没有 block_fail（判据缺失不得当作通过）"
    block_fail = int(m.group(1))
    warn = re.search(r"warn_fail=(\d+)", text)
    warn_n = int(warn.group(1)) if warn else 0
    measurement = f"block_fail={block_fail} / warn_fail={warn_n}"
    if block_fail:
        return STATUS_FAIL, measurement, f"{block_fail} 项 block 级漂移"
    if rc not in (0, 1):
        return STATUS_FAIL, measurement, f"退出码异常 rc={rc}"
    return STATUS_PASS, measurement, "运行时契约无 block 级漂移"


def _judge_residue(rc: int, text: str) -> tuple[str, str | None, str]:
    """前端残留审计：判据「死引用 N」.

    ★ `rc` 被**刻意忽略**：该脚本末尾无条件 `process.exit(0)` ⇒ rc 恒为 0，
    完全无区分力。「rc==0 即绿」会把 139 这个假数字读成 PASS。
    """
    del rc
    m = re.search(r"死引用\s*(\d+)", text)
    if not m:
        return (
            STATUS_FAIL,
            None,
            "无法判读：输出里没有「死引用 N」（判据缺失不得当作通过）",
        )
    dead = int(m.group(1))
    dom = re.search(r"运行时 DOM 中的 id:\s*(\d+)", text)
    n_dom = int(dom.group(1)) if dom else None
    measurement = f"死引用 {dead}"
    if n_dom is not None:
        # DOM id = 0 正是 139 假数字的冒烟枪：页面根本没载入 ⇒
        # 「死引用 0」不是「没有残留」，而是「一个 id 都没读到」——两者都不可信。
        measurement += f"（DOM id {n_dom}）"
    if dead:
        return STATUS_FAIL, measurement, f"死引用 {dead} 处（脚本 rc 恒为 0，按数字判）"
    if n_dom == 0:
        # ★ 自我防御：即使前置守卫被绕过，也不许把「页面空白」报成 0 处残留。
        # 判据本身就不该接受一个自相矛盾的读数（DOM 里没有任何 id ⇒ 无从判定残留）。
        return (
            STATUS_FAIL,
            measurement,
            "读数自相矛盾：运行时 DOM 中的 id 为 0（页面未载入）⇒ 「死引用 0」不可信，不得判通过",
        )
    return STATUS_PASS, measurement, "死引用 0 处"


def _judge_contract(rc: int, text: str) -> tuple[str, str | None, str]:
    """路由契约审计：判据 `BROKEN=0`（该脚本退出码与判据一致）."""
    del rc
    m = re.search(r"【BROKEN】[^\n]*?——\s*(\d+)\s*个", text)
    if not m:
        return STATUS_FAIL, None, "无法判读：输出里没有 BROKEN 计数（判据缺失不得当作通过）"
    broken = int(m.group(1))
    unused = re.search(r"【UNUSED】[^\n]*?——\s*(\d+)\s*个", text)
    measurement = f"BROKEN={broken}"
    if unused:
        measurement += f" / UNUSED={unused.group(1)}"
    if broken:
        return STATUS_FAIL, measurement, f"{broken} 条前端在调、后端未注册的路由"
    return STATUS_PASS, measurement, "无断链路由"


def _judge_events(rc: int, text: str) -> tuple[str, str | None, str]:
    """live 决策事件读取：判据 有事件且延迟齐备（缺数据即判红）."""  # noqa: D403 - "live" 是本项目的形态名（非句首词）
    rounds = re.search(r"rounds=(\d+)", text)
    n_rounds = int(rounds.group(1)) if rounds else 0
    if rc != 0:
        missing = re.search(r"判红：(.+)", text)
        head = missing.group(1).strip() if missing else "缺数据或存在坏行"
        if n_rounds == 0:
            return STATUS_UNMEASURABLE, None, f"没有决策事件可读：{head}"
        return STATUS_FAIL, f"rounds={n_rounds}", f"--require-latency 判红：{head}"
    if n_rounds == 0:
        return STATUS_UNMEASURABLE, None, "读取到 0 轮决策事件（缺失不等于通过）"
    return STATUS_PASS, f"rounds={n_rounds}", "事件可读且延迟字段齐备"


def _judge_recall(rc: int, text: str) -> tuple[str, str | None, str]:
    """向量召回：判据 `recall@k` 与**分母**（分母漂移必须判红 —— 台账纪律 5）."""
    del rc
    if "requires SILICONFLOW_API_KEY" in text or "requires the local bge-m3 weights" in text:
        return STATUS_UNMEASURABLE, None, "评测器自报前置缺失（嵌入器不可用）"
    m = re.search(r"recall@(\d+)\s*=\s*(\d+)\s*/\s*(\d+)", text)
    if not m:
        return STATUS_FAIL, None, "无法判读：输出里没有 recall@k 报告值"
    k, hits, total = int(m.group(1)), int(m.group(2)), int(m.group(3))
    measurement = f"{hits} / {total}"
    if k != 5:
        return STATUS_FAIL, measurement, f"top_k 漂移：报告的是 recall@{k}，期望 recall@5"
    if total != GOLDEN_EXPECTED_DENOMINATOR:
        return (
            STATUS_FAIL,
            measurement,
            f"分母漂移：{total} ≠ {GOLDEN_EXPECTED_DENOMINATOR}"
            "（跨变体比较会静默失效，见台账纪律 5）",
        )
    if hits != total:
        return STATUS_FAIL, measurement, f"recall@{k} = {hits}/{total}，存在未命中"
    return STATUS_PASS, measurement, f"recall@{k} = {hits}/{total} 全命中"


# item_id → 该项的判据函数。用映射而非 if 级联：新增仪式项时**只需加一处**，
# 且「有哪些项」与「每项怎么判」不会各自漂移。
_JUDGES: dict[str, Callable[[int, str], tuple[str, str | None, str]]] = {
    ITEM_STACK: _judge_stack,
    ITEM_DRIFT: _judge_drift,
    ITEM_RESIDUE: _judge_residue,
    ITEM_CONTRACT: _judge_contract,
    ITEM_EVENTS: _judge_events,
    ITEM_RECALL: _judge_recall,
}


def judge(item_id: str, rc: int, output: str) -> tuple[str, str | None, str]:
    """判一项 ``(status, measurement, detail)``.

    判据一律取自**解析出的数字**；`rc` 只在少数地方作为辅助信号使用
    （如残留审计的 rc 恒为 0，**完全不参与判定**）。

    Raises
    ------
    KeyError
        未知 item_id —— 显式抛出而非默认判绿：一个没有判据的项，
        其「结果」不可能是「通过」。
    """
    text = _clean(output)
    try:
        return _JUDGES[item_id](rc, text)
    except KeyError as exc:
        raise KeyError(f"未知仪式项：{item_id}（没有判据 ⇒ 不得当作通过）") from exc


# ---------------------------------------------------------------------------
# 执行
# ---------------------------------------------------------------------------


def _child_env(command: Command, root: Path) -> dict[str, str] | None:
    """构造子进程环境；必要时把密钥从 env 文件注入**子进程**（不回显值）."""
    needs_path = command.cwd is not None and Path(command.cwd).name in {"webinfer", "memory-store"}
    if not needs_path and not command.env_file:
        return None
    env = dict(os.environ)
    if needs_path:
        src = str(root / "services" / "webui" / "src")
        extra = [src, str(command.cwd)]
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            os.pathsep.join([*extra, existing]) if existing else os.pathsep.join(extra)
        )
    if command.env_file:
        load_env_file(root / command.env_file, env)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def execute(command: Command, timeout: float = 300.0) -> tuple[int, str]:
    """真跑一条命令，返回 ``(rc, merged_output)``（UTF-8 解码，中文不乱码）.

    ★ 执行前经 :func:`_assert_executable_allowed` 校验白名单：argv **不来自**
    用户输入（它由 :func:`build_items` 的注册表固定给出），但该事实在代码里也被强制，
    故这里不存在「拿外部串去拼命令」的路径。
    """
    try:
        parts = _assert_executable_allowed(command.argv)
    except ValueError as exc:
        return 126, str(exc)

    env = _child_env(command, REPO_ROOT)
    try:
        proc = subprocess.run(
            parts,
            cwd=str(command.cwd) if command.cwd else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
            check=False,
        )
    except FileNotFoundError as exc:
        return 127, f"命令不存在：{exc}"
    except subprocess.TimeoutExpired:
        return 124, f"命令超时（>{timeout:.0f}s）"
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def run_ritual(
    items: Sequence[Item],
    *,
    probe: Callable[[str], tuple[bool, str]],
    full_ritual_ids: Iterable[str],
    execute: Callable[[Command], tuple[int, str]] = execute,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> Run:
    """跑完给定的仪式项，逐项给结果（**前置不满足 ⇒ 不执行该命令**）.

    Args:
        items: 本轮要跑的项（可能是完整仪式的子集）。
        probe: 前置探针。**每个前置只调用一次**（探针含 HTTP / 文件读取，非幂等）。
        full_ritual_ids: **完整仪式**的项 id。**必填、无默认值** ——
            这是刻意的：初版给了 `None` 默认值，而 `None` 会退化成「传入的项
            就算完整仪式」⇒ 任何省略该参数的调用方都能把子集报成全绿。
            这正是本票要消灭的缺陷，故**不给默认值**，让漏传在调用点就无法编译通过
            （fail-closed：拿不到覆盖范围，就不许声称完整）。
        execute: 命令执行器。
        now: 取时刻（可注入以便测试）。

    Returns
    -------
        一个 :class:`Run`：既有逐项结果，也**知道自己没跑哪些项**。
    """
    item_list = list(items)
    expected = tuple(full_ritual_ids)

    results: list[Result] = []
    for item in item_list:
        observed = now()
        # ★ 每个前置**只探一次**：初版写成 `[... for pc in ... if not probe(...)[0]]`
        # 再在推导式里取 detail，等于对同一 claim 调了两次；探针里有 HTTP 请求与
        # sha256 文件读，白跑一遍且对非幂等探针是错的。
        missing: list[tuple[Precondition, str]] = []
        for pc in item.preconditions:
            ok, detail = probe(pc.claim_id)
            if not ok:
                missing.append((pc, detail))
        if missing:
            why = "；".join(f"{pc.description}：{detail}" for pc, detail in missing)
            results.append(
                Result(
                    item_id=item.item_id,
                    title=item.title,
                    mode=item.mode,
                    command=item.command,
                    status=STATUS_UNMEASURABLE,
                    measurement=None,
                    detail=f"前置缺失 ⇒ 未执行该命令。{why}",
                    observed_at=observed,
                    tags=("前置缺失",),
                    scope_note=item.scope_note,
                )
            )
            continue

        started = now()
        rc, output = execute(item.command)
        finished = now()
        status, measurement, detail = judge(item.item_id, rc, output)
        results.append(
            Result(
                item_id=item.item_id,
                title=item.title,
                mode=item.mode,
                command=item.command,
                status=status,
                measurement=measurement,
                detail=detail,
                observed_at=finished,
                duration_ms=max(0, int((finished - started).total_seconds() * 1000)),
                tags=(f"rc={rc}",),
                scope_note=item.scope_note,
            )
        )
    return Run(results=results, expected_item_ids=expected)


def result_for(results: Sequence[Result], item_id: str) -> Result:
    """取指定项的结果（不存在即抛，防止静默漏项）."""
    for res in results:
        if res.item_id == item_id:
            return res
    raise KeyError(f"仪式结果里没有此项：{item_id}")


def exit_code(run: Run | Sequence[Result]) -> int:
    """0 **全仪式**全绿 / 1 有 FAIL / 2 未覆盖完、有不可测量项、或状态未知.

    ★ 四条不许违反的性质（初版在 1、2 上都是错的，由 code-review 查出）：
      1. **一项都没跑 ⇒ 绝不返回 0**（`--only ","` 曾报 ALL GREEN + exit 0）；
      2. **只跑了子集 ⇒ 绝不返回 0** —— 没跑的项不是「通过」；
      3. **覆盖范围未知 ⇒ 绝不返回 0**（拿不到完整项清单时不许声称完整）；
      4. **状态无法识别 ⇒ 绝不返回 0**（不认识的 status 不能恰好既不是 FAIL
         又不是 N/A，从而滑进 green 分支）。

    ⇒ 只有「覆盖完整仪式 + 每项都是 PASS」才配得上 0。
    """
    if isinstance(run, Run):
        results: Sequence[Result] = run.results
        complete = run.is_complete
    else:
        # 兼容直接传结果列表的老用法：此时无法知道覆盖范围，故**不认完整**。
        # 宁可把「可能不全」判成非绿，也不能把「没跑」说成「通过」。
        results = list(run)
        complete = False

    if any(r.status == STATUS_FAIL for r in results):
        return 1
    if not results:  # ★ 空集：一项都没跑
        return 2
    if any(r.status != STATUS_PASS for r in results):
        # 不是 PASS 的一律非绿：涵盖「无法测量」与将来任何未知状态
        # （未知状态**不得**因为「既非 FAIL 又非 N/A」而滑进 0 分支）。
        return 2
    if not complete:  # ★ 子集：还有项没跑
        return 2
    return 0


# ---------------------------------------------------------------------------
# 输出：汇总 + 台账行 + JSON
# ---------------------------------------------------------------------------


def _result_cell(res: Result) -> str:
    """台账「结果」列：通过/失败时带测量值，测不了时只写状态（原因在说明列）."""
    if res.status == STATUS_UNMEASURABLE:
        return f"**{STATUS_UNMEASURABLE}**"
    prefix = "**ALL PASS**" if res.status == STATUS_PASS else "**FAIL**"
    if res.measurement:
        return f"{prefix} {res.measurement}"
    return f"{prefix} {res.detail}"


def format_ledger_rows(results: Sequence[Result]) -> str:
    """★ 直接可粘贴进台账的行（四要素：命令 / 结果 / 真机或离线 / 测量时间）.

    说明列会带上该项的**口径**（`scope_note`，如「读的是哪一天的事件文件」）——
    台账纪律 5 要求口径可见，否则跨轮比较会静默失真。
    """
    lines = ["| 项 | 命令 | 结果 | 真机? | 测量时间 |", "|---|---|---|---|---|"]
    for res in results:
        observed = res.observed_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        parts = [res.detail, res.scope_note] if res.scope_note else [res.detail]
        detail = "；".join(p for p in parts if p).replace("|", "/")
        result_cell = _result_cell(res).replace("|", "/")
        cells = [
            res.title,
            f"`{res.command.display}`",
            f"{result_cell}<br><sub>{detail}</sub>",
            res.mode,
            observed,
        ]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


_STATUS_ICON = {STATUS_PASS: "[PASS]", STATUS_FAIL: "[FAIL]", STATUS_UNMEASURABLE: "[N/A ]"}

_SUMMARY_WIDTH = 78


def _coverage(
    run: Run | Sequence[Result], full_ritual: Sequence[Item] | None
) -> tuple[list[Result], tuple[str, ...], bool, dict[str, str]]:
    """解出「本轮结果 + 未覆盖项 + 覆盖范围是否已知 + id→标题」.

    ★ 传裸结果列表（或 `run.results`）时**覆盖范围未知** —— 调用方丢掉了
    「完整仪式有哪些项」这个信息。此处如实回报 `coverage_known=False`，
    下游据此**不得**声称全绿。
    """
    titles = {it.item_id: it.title for it in (full_ritual or ())}
    if isinstance(run, Run):
        return list(run.results), run.uncovered_ids, bool(run.expected_item_ids), titles
    results = list(run)
    expected = tuple(titles)
    covered = Run(results=results, expected_item_ids=expected)
    return results, covered.uncovered_ids, bool(expected), titles


def _render_item_lines(results: Sequence[Result], *, verbose: bool) -> list[str]:
    """逐项渲染（未知状态原样显示，且**不**给通过图标）."""
    lines: list[str] = []
    for res in results:
        icon = _STATUS_ICON.get(res.status, f"[{res.status}?]")
        if not verbose:
            measurement = f" —— {res.measurement}" if res.measurement else ""
            lines.append(f"{icon} {res.mode} {res.title}{measurement}")
            continue
        lines.append(f"{icon} {res.mode} {res.title}")
        lines.append(f"       命令：{res.command.display}")
        if res.scope_note:
            lines.append(f"       {res.scope_note}")
        if res.measurement:
            lines.append(f"       结果：{res.measurement}")
        lines.append(f"       说明：{res.detail}")
    return lines


def _verdict_lines(
    results: Sequence[Result],
    uncovered: tuple[str, ...],
    titles: dict[str, str],
    *,
    coverage_known: bool,
) -> list[str]:
    """结论段：把「没跑 / 测不了 / 没通过 / 覆盖未知」逐条说出来.

    ★ **只有**「覆盖已知 + 无 FAIL + 无不可测量 + 无未知状态」才允许出现通过结论。
    """
    lines: list[str] = []
    n_fail = sum(1 for r in results if r.status == STATUS_FAIL)
    n_na = sum(1 for r in results if r.status == STATUS_UNMEASURABLE)
    n_unknown = sum(1 for r in results if r.status not in _STATUS_ICON)

    if uncovered:
        names = [titles.get(i, i) for i in uncovered]
        lines.append(
            "⚠️ 本轮未覆盖（**不是通过**）："
            + "、".join(names)
            + f" —— 共 {len(uncovered)} 项未跑。"
        )
    if n_na:
        live_na = [
            r.title for r in results if r.status == STATUS_UNMEASURABLE and r.mode == MODE_LIVE
        ]
        if live_na:
            lines.append(
                "⚠️ 真机项不可测：" + "、".join(live_na) + "（服务未起或前置不满足 —— 不是通过）"
            )
    if n_unknown:
        lines.append(f"⚠️ {n_unknown} 项状态无法识别 ⇒ 不得视为通过。")

    if n_fail:
        lines.append(
            "❌ 存在问题项：" + "、".join(r.title for r in results if r.status == STATUS_FAIL)
        )
    elif n_na or uncovered or n_unknown:
        lines.append(
            "🟡 无 FAIL，但有项不可测量／未覆盖／状态未知 ⇒ **本轮不是全绿**（退出码 2）。"
        )
    elif not coverage_known:
        # ★ 覆盖范围未知（如只传了 `run.results`）⇒ 不能声称全绿。
        lines.append(
            "🟡 覆盖范围未知（未提供完整仪式项清单）⇒ **不得据此判绿**。"
            "请传完整的 Run 或 full_ritual。"
        )
    else:
        lines.append("✅ ALL GREEN（本轮的每一项都真跑过且通过）。")
    return lines


def format_summary(
    run: Run | Sequence[Result], *, verbose: bool = True, full_ritual: Sequence[Item] | None = None
) -> str:
    """人读汇总（含「真机项不可测」「仪式未跑完」的显式告知）.

    Args:
        run: 一轮仪式（:class:`Run`）或裸结果列表。
        verbose: True 时逐项列出命令/结果/说明；False 时只给状态行与结论
            （`--summary` 用，便于粘贴前快速核对）。
        full_ritual: 完整仪式项；给出时用于告知「本轮未覆盖哪些项」。

    ★ 「ALL GREEN」只在**能证明跑全**时打印。传裸结果列表（或 `run.results`）
    时覆盖范围未知 ⇒ **不认全绿**，改报「覆盖范围未知」。宁可少报一次绿，
    也不能把「可能没跑全」说成「通过」。
    """
    results, uncovered, coverage_known, titles = _coverage(run, full_ritual)

    lines = ["=" * _SUMMARY_WIDTH, "验证仪式（输出即台账行）", "=" * _SUMMARY_WIDTH]
    lines.append(interpreter_line())
    lines.extend(_render_item_lines(results, verbose=verbose))

    n_pass = sum(1 for r in results if r.status == STATUS_PASS)
    n_fail = sum(1 for r in results if r.status == STATUS_FAIL)
    n_na = sum(1 for r in results if r.status == STATUS_UNMEASURABLE)
    # 未知状态也**不得**算作「无事发生」——它们既非 FAIL 也非 N/A，故单独计并要求判非绿。
    n_unknown = sum(1 for r in results if r.status not in _STATUS_ICON)

    lines.append("-" * _SUMMARY_WIDTH)
    lines.append(
        f"合计：PASS {n_pass} / FAIL {n_fail} / 不可测量 {n_na}"
        + (f" / 未覆盖 {len(uncovered)}" if uncovered else "")
        + (f" / 状态未知 {n_unknown}" if n_unknown else "")
    )

    if not results:
        lines.append("🟡 未选中任何项 ⇒ 本轮没有任何证据（退出码 2，不是通过）。")
        lines.append("-" * _SUMMARY_WIDTH)
        return "\n".join(lines)

    lines.extend(_verdict_lines(results, uncovered, titles, coverage_known=coverage_known))
    lines.append("-" * _SUMMARY_WIDTH)
    lines.append("台账行（可直接粘贴进 doc/standards/test-baseline.md）：")
    lines.append(format_ledger_rows(results))
    return "\n".join(lines)


def to_json(run: Run | Sequence[Result]) -> list[dict]:
    """结构化输出（供后续四票复用；四要素齐备，且**带覆盖范围**）."""
    results: Sequence[Result] = run.results if isinstance(run, Run) else list(run)
    return [
        {
            "item_id": res.item_id,
            "title": res.title,
            "command": res.command.display,
            "status": res.status,
            "measurement": res.measurement,
            "detail": res.detail,
            "mode": res.mode,
            "observed_at": res.observed_at.astimezone(timezone.utc).isoformat(timespec="seconds"),
            "duration_ms": res.duration_ms,
            "tags": list(res.tags),
            "scope_note": res.scope_note,
        }
        for res in results
    ]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """解析命令行参数."""
    parser = argparse.ArgumentParser(
        description="跑一轮标准验证仪式，输出即台账行（工单 #162）。",
    )
    parser.add_argument("--json", action="store_true", help="输出结构化 JSON")
    parser.add_argument("--summary", action="store_true", help="只输出汇总（不逐项详列）")
    parser.add_argument(
        "--only",
        default=None,
        help="只跑指定项（逗号分隔的 item id，如 api-contract,decision-events）",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="只列出仪式项（不执行）",
    )
    parser.add_argument("--timeout", type=float, default=300.0, help="单项超时秒数（默认 300）")
    return parser.parse_args(argv)


def selected_items(all_items: Iterable[Item], only: str | None) -> list[Item]:
    """按 `--only` 过滤（未知 id、或一个有效 id 都没有 ⇒ 显式报错，不静默跑空）.

    ★ `None`（**没给这个旗标**）与 `""`（**给了但展开成空**）必须区别对待：
    前者 = 跑完整仪式；后者几乎总是 shell 变量展开成空的意外（`--only "$IDS"`），
    若也当成「跑完整仪式」，就会**静默跑起整轮真机仪式**（耗时且可能 FAIL）。
    二者都不得静默 —— 空白的 `--only` 是调用方的错误，显式报错。
    """
    items = list(all_items)
    known = {it.item_id for it in items}
    if only is None:
        return items
    if not only.strip():
        raise SystemExit(
            f"--only 传了空白值 {only!r}（常见成因：shell 变量展开成空）。"
            f"若想跑完整仪式，请不要传 --only；可选：{', '.join(sorted(known))}"
        )
    wanted = [part.strip() for part in only.split(",") if part.strip()]
    if not wanted:
        # ★ `--only ","` 曾静默解析出空集 ⇒ 跑空 ⇒ 报 ALL GREEN + exit 0。
        # 空选择是**调用方的错误**，必须显式报错，不得当成「跑完并且全绿」。
        raise SystemExit(f"--only 未指定任何有效项：{only!r}；可选：{', '.join(sorted(known))}")
    unknown = [w for w in wanted if w not in known]
    if unknown:
        raise SystemExit(f"未知仪式项：{', '.join(unknown)}；可选：{', '.join(sorted(known))}")
    return [it for it in items if it.item_id in wanted]


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 入口."""
    args = parse_args(argv)
    full_ritual = build_items()
    items = selected_items(full_ritual, args.only)

    if args.list:
        for it in items:
            print(f"{it.item_id:<16} {it.mode}  {it.title}")
            print(f"{'':<16} 命令：{it.command.display}")
            print(f"{'':<16} 判据：{it.judge_hint}")
        return 0

    env_for_probe = dict(os.environ)
    load_env_file(REPO_ROOT / ENV_FILE_REL, env_for_probe)

    run = run_ritual(
        items,
        probe=lambda claim: default_probe(claim, env_for_probe),
        execute=lambda cmd: execute(cmd, timeout=args.timeout),
        # ★ 传入**完整仪式**的 id：这样 `--only` 跑子集时，运行器知道自己没跑全，
        # 不会把「没跑」说成「通过」（初版即在此处报出假 ALL GREEN）。
        full_ritual_ids=[it.item_id for it in full_ritual],
    )

    if args.json:
        print(json.dumps(to_json(run), ensure_ascii=False, indent=2))
    elif args.summary:
        # 只给结论 + 台账行，不给逐项明细（适合粘进台账前先看一眼）。
        print(format_summary(run, verbose=False, full_ritual=full_ritual))
    else:
        print(format_summary(run, verbose=True, full_ritual=full_ritual))

    return exit_code(run)


if __name__ == "__main__":
    raise SystemExit(main())
