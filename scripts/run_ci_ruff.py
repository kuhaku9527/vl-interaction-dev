#!/usr/bin/env python
# ruff: noqa: RUF001, RUF002, RUF003
# ↑ 中文正文必然带全角标点，RUF001/2/3 会把每一个都报成「歧义 Unicode」，故整文件
#   豁免这三条。★ 这不是掩盖问题：实测 CI 里**没有任何**一步 lint `scripts/`
#   （见「Residual limitations」），豁免既不放松门禁，也不改变 CI 结果。
"""Run every ruff command from the CI workflow **verbatim**, locally.

Why this exists
---------------
`/implement #158` shipped a `# noqa: PLC0415` in `services/webui/tests/` that
turned into an `RUF100 unused-directive` under *webui's own* ruff config. It was
verified against `services/webinfer` only — so the local check was **looser than
CI**, and CI caught it after the push. That inversion is the shape #151 already
paid for ("CI 比裸跑更宽松，不是更严").

The root cause was narrower and dumber: the ruff jobs each carry their own
`--extend-ignore` set (and webui runs from its own `working-directory`, picking
up **its own** `[tool.ruff.lint]`). Measured: three hand-typed attempts produced
three different answers — two of them false failures on `memory-store` / `asr` /
`tts`. So do not retype them. **Extract and run.**

What this script guarantees — and what it does NOT cover
--------------------------------------------------------
An earlier revision of this docstring claimed it "cannot drift from CI because it
reads CI". **That claim was false and an adversarial review falsified it.** The
old extractor matched exactly one spelling — a `- name: Ruff …` line followed by
a single-line `run: ruff …` — and a step that did not match was dropped
*silently*: the count got smaller, the summary still said `N/N PASS`, and the exit
code was still 0. Measured on three mutations of a copy of the workflow — rename
the step so `_NAME_RE` no longer applies; convert `run:` to a `run: |` block
scalar; write `python -m ruff check …` — each dropped its step, and in all three
cases the summary printed `13/14 PASS` and exited **0**. Had the dropped step been
the failing one, its red **disappeared**. That is "a gate that passes when its
input is absent" — this repo's most expensive class of defect (see `AGENTS.md`,
测试有效性 09-21).

What is true now: every shape above is **parsed** (block scalars, `python -m
ruff`, `uv run` / `uvx` / `poetry run` wrappers); a shape the parser cannot reduce
to exactly one command is **refused with a non-zero exit and the step named**,
never skipped; and the number of ruff steps the workflow *should* yield is derived
from the workflow itself — its naming convention (`Ruff …`) **plus** a structural
signal (a `run:` that invokes ruff at all) — then compared against what was
parsed. Any shortfall is a hard failure listing the steps it could not parse.

Residual limitations — read these before trusting a green run:

  * **Only the `ruff` job.** The pytest matrix, eslint, drift-gate and
    package-smoke jobs have their own service lists and are not replicated here.
    Do not read a green run of this script as "the quality gate passes".
  * **A ruff step deleted outright is invisible.** Nothing is left to count, so
    the expected count drops with it and this script still prints `N/N PASS`.
    Only the count pin in `scripts/tests/test_ci_ruff_runner.py::
    test_real_workflow_extracts_every_ruff_step` catches that, and only until
    someone edits the number.
  * **`scripts/` itself is not linted.** No CI step covers it, and CI stays the
    single source of truth — this runner deliberately does not invent a step CI
    does not have.
  * **Multi-command block scalars are refused, not executed.** A `run: |` holding
    two commands is a hard failure: one subprocess per step, and "joining" two
    commands into one would be a lie of a different kind.

Usage
-----
    python scripts/run_ci_ruff.py            # run all extracted commands
    python scripts/run_ci_ruff.py -v         # also print each command
    python scripts/run_ci_ruff.py --list     # print the extracted commands only

Exit code: 0 when the workflow's ruff steps were all extracted, all agreed with
the expected count, and every command passed; 1 otherwise.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "quality.yml"

# --- 结构：把 workflow 切成「步骤」 -------------------------------------------------

#: job 里的 `steps:` 键。只在这个序列内部找步骤 —— `jobs: / ruff:` 之类的键
#: 与注释里的 ``ruff`` 字样都不该被当成步骤。
_STEPS_RE = re.compile(r"^(\s*)steps:\s*(?:#.*)?$")
#: 序列项（`- name: …` / `- uses: …`），`(1)` 是缩进。
_ITEM_RE = re.compile(r"^(\s*)- (.*)$")
_NAME_RE = re.compile(r"^\s*- name:\s*[\"']?(.*?)[\"']?\s*$")
_WD_RE = re.compile(r"^\s*working-directory:\s*(\S+)")
_RUN_RE = re.compile(r"^\s*run:\s*(.*)$")
#: `run: |` / `run: >`（含 chomping 修饰符）。
_BLOCK_SCALARS = frozenset({"|", "|-", "|+", ">", ">-", ">+"})

# --- 识别：什么算「一个 ruff 步骤」 ------------------------------------------------

#: workflow 自身的命名约定。实测 `grep -n 'name: Ruff' quality.yml` ⇒ 14 行，
#: 全部是 `Ruff lint (…)` / `Ruff format check (…)`，且全在 ruff job 内。
_RUFF_STEP_NAME_RE = re.compile(r"^Ruff\b")

#: ruff 的常见启动壳。本运行器只认这些；别的包装（`cd x && ruff …`、自定义
#: 脚本）会被**判红并点名**，而不是被静默跳过。
_RUFF_PREFIX = r"(?:(?:python3?|py)\s+-m\s+|uv\s+run\s+|uvx\s+|poetry\s+run\s+)?"

#: 宽松信号：这条 `run:` 到底有没有调用 ruff（用于「本该提取却没提取到」的判定）。
#: 前后 lookaround 是为了不误伤 `pip install ruff==0.15.22`（`ruff` 后面是 `=`）
#: 与 `run_ci_ruff.py`（`ruff` 后面是 `.`）—— 实测这两条就藏在真 workflow 里，
#: 误判会让本脚本在正确的 workflow 上**假红**。
_RUFF_INVOCATION_RE = re.compile(r"(?<![\w.-])" + _RUFF_PREFIX + r"ruff(?![=\w.-])")

#: 归一化：剥掉启动壳，留下以 `ruff ` 开头、可交给 `python -m ruff` 的单条命令。
_RUFF_COMMAND_RE = re.compile(r"^" + _RUFF_PREFIX + r"(ruff\s+\S.*)$")


@dataclass(frozen=True)
class _Step:
    """workflow 里的一个步骤，只保留本脚本需要的三件事 + 诊断信息."""

    name: str | None = None
    working_dir: str | None = None
    commands: tuple[str, ...] = ()
    #: 这个步骤按约定/结构**应当**产出一条 ruff 命令。
    expected: bool = False
    #: 为什么没能产出恰好一条命令（``None`` 表示没有异议）。
    problem: str | None = None


@dataclass(frozen=True)
class WorkflowAudit:
    """一次提取的完整账本：拿到了什么、本该拿到多少、哪些没拿到.

    不变式：步骤只有先被判为「期望」才可能带上 ``problem``，故
    ``extracted_step_count == expected_step_count`` 与 ``unparsed`` 为空等价。
    """

    jobs: list[tuple[str, str | None, str]] = field(default_factory=list)
    expected_step_count: int = 0
    unparsed: list[str] = field(default_factory=list)
    #: 解析出的步骤数（每步恰好一条命令，故它恒等于 ``len(jobs)``）。
    extracted_step_count: int = 0


class ExtractionMismatchError(RuntimeError):
    """应当被提取、却无法归约成唯一一条命令的 ruff 步骤.

    旧版把这类步骤静默丢掉（计数变小、摘要仍 `N/N PASS`、退出码仍 0），故
    「丢了一步」与「全过了」在输出上无法区分（见模块文档）。
    """

    def __init__(self, audit: WorkflowAudit) -> None:
        self.audit = audit
        detail = "\n".join(f"    - {item}" for item in audit.unparsed)
        super().__init__(
            f"workflow 里按约定应有 {audit.expected_step_count} 个 ruff 步骤，"
            f"只解析出 {audit.extracted_step_count} 个。未能解析的步骤：\n{detail}"
        )


class ExtractedCommands(list):
    """``extract_commands`` 的返回值：普通 ``list`` + workflow 自证的期望数.

    是 list 子类，故 ``preflight``/``run_all`` 与既有 monkeypatch 夹具（普通 list）
    都能照旧消费它。``expected`` 必须带出来而不能由 ``len(jobs)`` 反推 ——
    ``len(jobs)`` 正是那个**丢了步也照样自洽**的数字。
    """

    def __init__(self, jobs, *, expected: int) -> None:
        super().__init__(jobs)
        self.expected = expected


def _step_blocks(lines: list[str]) -> list[list[str]]:
    """把每个 job 的 ``steps:`` 序列切成一段段「步骤原文」.

    ``steps:`` 的缩进决定这段的边界：缩进更深的行属于它，遇到缩进不大于它的
    非空行（下一个 job 键 / 下一个顶格键）即结束。
    """
    blocks: list[list[str]] = []
    index, total = 0, len(lines)
    while index < total:
        steps_match = _STEPS_RE.match(lines[index])
        if not steps_match:
            index += 1
            continue
        steps_indent = len(steps_match.group(1))
        cursor = index + 1
        region: list[str] = []
        while cursor < total:
            line = lines[cursor]
            if line.strip() and (len(line) - len(line.lstrip())) <= steps_indent:
                break
            region.append(line)
            cursor += 1
        # 步骤边界取该段内 `- ` 的最小缩进：`with:` 下的嵌套序列不会被误当成新步骤。
        indents = [len(m.group(1)) for line in region if (m := _ITEM_RE.match(line))]
        if indents:
            item_indent = min(indents)
            current: list[str] | None = None
            for line in region:
                match = _ITEM_RE.match(line)
                if match and len(match.group(1)) == item_indent:
                    if current is not None:
                        blocks.append(current)
                    current = [line]
                elif current is not None:
                    current.append(line)
            if current is not None:
                blocks.append(current)
        index = cursor
    return blocks


def _block_commands(body: list[str], *, folded: bool) -> list[str]:
    r"""把 ``run: |`` / ``run: >`` 的块标量还原成命令列表.

    ★ 不能只是 ``" ".join(lines)``：块里可能有两三条**互相独立**的命令，拼成一条
    会跑出一条 CI 里不存在的命令 —— 那是另一种撒谎。``|``（literal）每个物理换行
    是一条命令，以 ``\\`` 结尾的行按 shell 续行规则与下一行合并（多行 ruff 命令就是
    这么写的）；``>``（folded）整块折成**一条**。返回多条时由调用方**拒绝**
    （见 :func:`_parse_step`），不猜。
    """
    parts: list[str] = []
    buffer = ""
    for raw in body:
        text = raw.strip()
        if not text or text.startswith("#"):
            continue
        if folded:
            buffer = f"{buffer} {text}".strip()
            continue
        if text.endswith("\\"):
            buffer = f"{buffer} {text[:-1].rstrip()}".strip()
            continue
        parts.append(f"{buffer} {text}".strip())
        buffer = ""
    if folded:
        return [buffer] if buffer else []
    if buffer:
        parts.append(buffer)
    return parts


def _parse_step(block: list[str]) -> _Step:
    """解析一个步骤：取名、取 working-directory、把 `run:` 归一成恰好一条命令.

    判定「本该产出命令」用两个独立信号，任一成立即**期望**：① 命名约定 ``Ruff …``
    （见 :data:`_RUFF_STEP_NAME_RE`）；② 结构信号：``run:`` 里确实调用了 ruff
    （:data:`_RUFF_INVOCATION_RE`）。信号 ② 是给「步骤被改名、run 没变」留的网 ——
    只靠 ① 的话，一次改名会让期望数与被解析数**一起**变小，差异消失、绿照旧
    （正是旧版被证伪的形态）。
    """
    name: str | None = None
    working_dir: str | None = None
    raw_commands: list[str] = []
    has_run = False
    block_len = len(block)
    index = 0
    while index < block_len:
        line = block[index]
        if name is None and (match := _NAME_RE.match(line)):
            name = match.group(1).strip()
        if working_dir is None and (match := _WD_RE.match(line)):
            working_dir = match.group(1)
        run_match = _RUN_RE.match(line)
        if run_match is None:
            index += 1
            continue
        has_run = True
        inline = run_match.group(1).strip()
        if inline not in _BLOCK_SCALARS:
            raw_commands.append(inline)
            index += 1
            continue
        # 块标量：吃掉所有缩进更深（或空）的行。
        key_indent = len(line) - len(line.lstrip())
        body: list[str] = []
        index += 1
        while index < block_len:
            continuation = block[index]
            cont_indent = len(continuation) - len(continuation.lstrip())
            if continuation.strip() and cont_indent <= key_indent:
                break
            body.append(continuation)
            index += 1
        raw_commands.extend(_block_commands(body, folded=inline.startswith(">")))

    commands = tuple(
        m.group(1).strip() for raw in raw_commands if (m := _RUFF_COMMAND_RE.match(raw))
    )
    mentions = any(_RUFF_INVOCATION_RE.search(raw) for raw in raw_commands)
    named_ruff = bool(name) and _RUFF_STEP_NAME_RE.match(name) is not None
    expected = named_ruff or mentions

    problem: str | None = None
    if expected and len(commands) > 1:
        problem = (
            f"`run:` 里有 {len(commands)} 条 ruff 命令 —— 本运行器每步只跑一条子进程，"
            "拒绝（拼成一条会跑出 CI 里不存在的命令）"
        )
    elif expected and not commands:
        shown = " / ".join(raw_commands)[:80]
        if not has_run:
            problem = "这个步骤没有 `run:` 行"
        elif not raw_commands:
            problem = "`run:` 是空的"
        elif not mentions:
            problem = (
                f"`run:` 里没有可识别的 ruff 调用（按步骤名约定本应是一条 ruff 命令）：{shown!r}"
            )
        else:
            problem = (
                "`run:` 调用了 ruff，但形状不在本运行器支持之内"
                f"（只认单条 `ruff …` / `python -m ruff …` / `uv run ruff …`）：{shown!r}"
            )
    return _Step(name, working_dir, commands, expected, problem)


def audit_workflow(workflow_path: Path = WORKFLOW) -> WorkflowAudit:
    """解析 workflow 并给出完整账本（不抛 :class:`ExtractionMismatchError`）.

    :func:`extract_commands` 与它共用这条路径；测试用这个入口，是因为它把「拿到了
    什么」和「本该拿到多少」**一起**返回，调用方可自行核对差异。workflow 缺失时
    抛 ``FileNotFoundError``（明说原因），绝不报「0 条命令全过」。
    """
    if not workflow_path.is_file():
        raise FileNotFoundError(
            f"CI workflow not found: {workflow_path} — cannot extract ruff commands. "
            "A missing workflow must not read as 'nothing to check'."
        )

    lines = workflow_path.read_text(encoding="utf-8").splitlines()
    steps = [_parse_step(block) for block in _step_blocks(lines)]
    expected_steps = [step for step in steps if step.expected]

    jobs: list[tuple[str, str | None, str]] = []
    unparsed: list[str] = []
    extracted = 0
    for step in expected_steps:
        label = step.name or "<未命名步骤>"
        if step.problem:
            unparsed.append(f"{label}: {step.problem}")
            continue
        extracted += 1
        jobs.extend((label, step.working_dir, command) for command in step.commands)

    return WorkflowAudit(
        jobs=jobs,
        expected_step_count=len(expected_steps),
        unparsed=unparsed,
        extracted_step_count=extracted,
    )


def extract_commands(workflow_path: Path = WORKFLOW) -> ExtractedCommands:
    """Extract ``(job name, working directory, ruff args)`` from the CI workflow.

    Parses the workflow line-by-line rather than with a YAML library: the only
    thing needed is the ordered ``name`` / ``working-directory`` / ``run`` triple,
    and a full YAML parse would add a dependency for no benefit — CI's
    ``scripts-tests`` job installs only ``pytest`` and ``ruff``, so a ``PyYAML``
    import here would turn this runner into an ImportError in CI.

    Returns one entry per ruff step, in workflow order, as an
    :class:`ExtractedCommands` (a ``list`` carrying the expected count).

    Raises ``FileNotFoundError`` when the workflow is missing (fail loud rather
    than report "0 commands, all passed"), and :class:`ExtractionMismatchError`
    when the workflow contains a ruff step this parser could not reduce to exactly
    one command. **That second case is the point of this function**: the old
    version returned a shorter list here and let the caller report success.
    """
    audit = audit_workflow(workflow_path)
    if audit.unparsed:
        raise ExtractionMismatchError(audit)
    return ExtractedCommands(audit.jobs, expected=audit.expected_step_count)


#: ruff 子命令（不是路径）。
_SUBCOMMANDS = frozenset({"check", "format", "rule", "config", "linter", "clean", "version"})

#: 这些标志**带一个值**，其值既不是路径也不是子命令，必须跳过。
_VALUE_FLAGS = frozenset(
    {
        "--select",
        "--ignore",
        "--extend-select",
        "--extend-ignore",
        "--per-file-ignores",
        "--config",
        "--line-length",
        "--target-version",
        "--output-format",
        "--statistics",
        "--exclude",
        "--extend-exclude",
        "--stdin-filename",
        "--fixable",
        "--unfixable",
        "--namespace-packages",
        "--cache-dir",
        "--diff",
    }
)


def ruff_available() -> bool:
    """Whether ruff is importable in this interpreter.

    ★ 这是承重的 fail-closed：**ruff 缺席时本脚本必须判红，不得报绿**。
    否则 `python -m ruff` 会以 ``No module named ruff`` 失败 —— 而那看起来
    像「ruff 报错了」，读起来却可能是「都跑过了」。实测发生过一次
    （CI run 35835923523 的 `scripts-tests` job：装了 pytest、没装 ruff，
    失败信息还是指错方向的「ruff 改变了行为」）。

    本函数独立成函数是为了**可被测试替换**（见
    ``scripts/tests/test_ci_ruff_runner.py::test_runner_refuses_to_run_without_ruff``）。
    """
    import importlib.util

    return importlib.util.find_spec("ruff") is not None


def target_paths(command: str) -> list[str]:
    """Return the path arguments of one ruff command (flags stripped).

    Used by the pre-flight check, because of a **measured** ruff behaviour:

        ruff check --select F821 no/such/path.py   -> exit 0   (!)
        ruff check --select F821 no/such/dir       -> exit 0   (!)
        ruff check --select F821 <file with F821>  -> exit 1

    A mistyped path therefore lints **nothing** and reports success — the same
    fail-open shape as a gate that passes when its input is absent, which would
    make this very script lie about matching CI. So resolve the paths ourselves
    and refuse to run a command whose targets are not on disk.

    Parsing rules (each exists because a naive version got it wrong — the first cut
    treated ``check`` and ``--extend-ignore``'s value as paths, flagging all 14
    jobs): the leading ``ruff`` token is dropped by the caller; a token after a
    :data:`_VALUE_FLAGS` flag is that flag's value, not a path; :data:`_SUBCOMMANDS`
    are commands, not paths; everything else is a path.
    """
    tokens = command.split()[1:]  # skip the leading "ruff"
    out: list[str] = []
    skip_next = False
    for token in tokens:
        if skip_next:
            skip_next = False
            continue
        if token in _VALUE_FLAGS:
            skip_next = True
            continue
        if "=" in token and token.startswith("--"):
            continue  # --select=F821 形式：值内联，整块都是标志
        if token.startswith("-"):
            continue
        if token in _SUBCOMMANDS:
            continue
        out.append(token)
    return out


def preflight(jobs: list[tuple[str, str | None, str]]) -> list[str]:
    """Return a problem string per job whose target paths do not exist.

    ``--select``/``--extend-ignore`` take a value, so their arguments look like
    bare tokens. Those values are rule codes (``F821``, ``D101,...``), not paths,
    and cannot exist on disk. Filter them by shape rather than by position.
    """
    problems: list[str] = []
    for name, working_dir, command in jobs:
        cwd = (REPO_ROOT / working_dir) if working_dir else REPO_ROOT
        for path in target_paths(command):
            # 规则码（F821 / D101,D102 / SIM105）不是路径 —— 按形状排除。
            if re.fullmatch(r"[A-Z]+[0-9]+(,[A-Z]+[0-9]+)*", path):
                continue
            if not (cwd / path).exists():
                problems.append(f"{name}: 目标路径不存在: {path!r} (cwd={working_dir or '.'})")
    return problems


def run_all(
    *,
    verbose: bool = False,
    list_only: bool = False,
    workflow_path: Path = WORKFLOW,
) -> int:
    """Run each extracted command; print a per-job line and a final summary.

    ``workflow_path`` exists so tests can point the whole runner at a temporary
    workflow fixture and exercise the **real** parser (rather than monkeypatching
    the extractor away and testing nothing).

    Returns ``0`` when the extraction was complete (``extracted == expected``) and
    every command passed, ``1`` otherwise. Fail-closed: a missing workflow, an
    incomplete extraction, or a missing ruff are all non-zero.
    """
    try:
        jobs = extract_commands(workflow_path)
    except FileNotFoundError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1
    except ExtractionMismatchError as exc:
        # ★ 旧版撒谎的地方：丢了一步却报 `N/N PASS` 且退出 0；若丢的正是红的那步，
        #   红会**消失**。故提取不完整一律判红，并由异常列出没能解析的步骤。
        print(f"✗ 提取不完整 —— {exc}", file=sys.stderr)
        return 1

    if not jobs:
        print(
            "✗ the workflow yielded ZERO ruff commands — the parser or the workflow "
            "shape changed; a green here would be meaningless",
            file=sys.stderr,
        )
        return 1

    if list_only:
        for name, working_dir, command in jobs:
            where = f" (cwd={working_dir})" if working_dir else ""
            # command 自带 `ruff ` 前缀（见 _RUFF_COMMAND_RE），不要再补一个 ——
            # 旧版这里打成 `ruff ruff check …`，报告与实际执行的命令不一致。
            print(f"  {name}{where}\n      {command}")
        return 0

    # ★ ruff 缺席 ⇒ 判红（见 ruff_available()）：否则「环境里没有 ruff」会被读成绿。
    if not ruff_available():
        print(
            "✗ 本解释器里**没有 ruff** —— 无法复现 CI 的 lint 步骤。\n"
            "  这不是「都通过了」：缺工具的绿与真绿在输出上无法区分，故判红。\n"
            f"  请在当前解释器里装上 CI 钉的版本再跑：{sys.executable} -m pip install ruff==0.15.22",
            file=sys.stderr,
        )
        return 1

    # ★ 前置守卫：ruff 对不存在的路径**退出 0**（实测），故「绿」可能只是
    #   「一个文件都没 lint」。把这种情形挡在跑之前。
    missing = preflight(jobs)
    if missing:
        print("✗ 前置检查失败 —— 下列命令的目标路径不在磁盘上：", file=sys.stderr)
        for problem in missing:
            print(f"    - {problem}", file=sys.stderr)
        print(
            "  ruff 对不存在的路径**退出 0**（实测），故继续跑会产出一次"
            "「一个文件都没 lint」的假绿。",
            file=sys.stderr,
        )
        return 1

    # expected 来自 workflow 自身的约定；monkeypatch 进来的普通 list 没有这个属性，
    # 那种情形下计数器退化为 len(jobs)（既有测试的接缝，不是生产路径）。
    expected = getattr(jobs, "expected", len(jobs))
    print(
        f"=== CI ruff steps: {len(jobs)}/{expected} extracted "
        f"from {workflow_path.name} (每步恰好一条命令，无静默丢步) ==="
    )
    failures: list[tuple[str, str | None, str, str]] = []
    for name, working_dir, command in jobs:
        cwd = (REPO_ROOT / working_dir) if working_dir else REPO_ROOT
        if verbose:
            print(f"      {command}   [cwd={working_dir or '.'}]")
        result = subprocess.run(
            [sys.executable, "-m", *command.split()],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
        )
        output = (result.stdout + result.stderr).strip()
        tail = output.splitlines()[-1] if output else ""
        status = "PASS" if result.returncode == 0 else "FAIL"
        print(f"  {status}  {name:52s} {tail}")
        if result.returncode != 0:
            failures.append((name, working_dir, command, output))

    print()
    for name, working_dir, command, output in failures:
        print(f"--- FAIL: {name}  (cwd={working_dir or '.'})")
        print(f"    command: {command}")
        for line in output.splitlines()[:15]:
            print(f"    {line}")
        print()

    passed = len(jobs) - len(failures)
    # 输出里两处都给 `extracted/expected`：只看 `passed/total` 的话，一次「丢了一步
    # 但其余全过」会印成 `13/13 PASS` —— 外观与真全过完全一样，正是缺陷的形态。
    print(f"=== {passed}/{len(jobs)} CI ruff steps PASS ({len(jobs)}/{expected} extracted) ===")
    if failures:
        print(
            "★ 本地红 = CI 也会红（这些命令就是从 CI 抄的）。\n"
            "  修掉再 push —— 不要靠猜 ignore 集把红改绿：那会让本地比 CI 宽松。"
        )
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Run every CI ruff command locally, extracted verbatim from quality.yml."
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="also print each command")
    parser.add_argument("--list", action="store_true", help="only list the extracted commands")
    args = parser.parse_args(argv)
    return run_all(verbose=args.verbose, list_only=args.list)


if __name__ == "__main__":  # pragma: no cover - CLI glue
    raise SystemExit(main())
