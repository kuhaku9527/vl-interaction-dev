#!/usr/bin/env python
"""Run every ruff command from the CI workflow **verbatim**, locally.

Why this exists
---------------
`/implement #158` shipped a `# noqa: PLC0415` in `services/webui/tests/` that
turned into an `RUF100 unused-directive` under *webui's own* ruff config. It was
verified against `services/webinfer` only — so the local check was **looser than
CI**, and CI caught it after the push.

That inversion is the shape #151 already paid for ("CI 比裸跑更宽松，不是更严").
The root cause here was narrower and dumber: the ruff jobs each carry their own
`--extend-ignore` set (and webui runs from its own `working-directory`, picking
up **its own** `[tool.ruff.lint]`), and retyping those sets by hand is how you
get them wrong. Measured: three hand-typed attempts produced three different
answers — two of them false failures on `memory-store` / `asr` / `tts`.

So do not retype them. **Extract and run.** The workflow is the single source of
truth; this script has no opinion of its own, which is the point — it cannot
drift from CI because it reads CI.

What it does NOT cover
----------------------
Only the `ruff` job. The pytest matrix, eslint, drift-gate and package-smoke
jobs have their own service lists and are not replicated here — do not read a
green run of this script as "the quality gate passes".

Usage
-----
    python scripts/run_ci_ruff.py            # run all extracted commands
    python scripts/run_ci_ruff.py -v         # also print each command
    python scripts/run_ci_ruff.py --list     # print the extracted commands only

Exit code: 0 when every extracted command passes, 1 otherwise.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "quality.yml"

_NAME_RE = re.compile(r"\s*- name: (Ruff (?:lint|format check).*)")
_WD_RE = re.compile(r"\s*working-directory:\s*(\S+)")
_RUN_RE = re.compile(r"\s*run:\s*(ruff .*)")


def extract_commands(workflow_path: Path = WORKFLOW) -> list[tuple[str, str | None, str]]:
    """Extract ``(job name, working directory, ruff args)`` from the CI workflow.

    Parses the workflow line-by-line rather than with a YAML library: the only
    thing needed is the ordered ``name`` / ``working-directory`` / ``run``
    triple, and a full YAML parse would add a dependency for no benefit.

    Returns
    -------
        One entry per ruff step, in workflow order.

    Raises
    ------
        FileNotFoundError: the workflow is missing — fail loud rather than
            silently report "0 commands, all passed".
    """
    if not workflow_path.is_file():
        raise FileNotFoundError(
            f"CI workflow not found: {workflow_path} — cannot extract ruff commands. "
            "A missing workflow must not read as 'nothing to check'."
        )

    lines = workflow_path.read_text(encoding="utf-8").splitlines()
    jobs: list[tuple[str, str | None, str]] = []
    index = 0
    while index < len(lines):
        name_match = _NAME_RE.match(lines[index])
        if not name_match:
            index += 1
            continue
        name = name_match.group(1)
        working_dir: str | None = None
        command: str | None = None
        cursor = index + 1
        while cursor < len(lines) and not _NAME_RE.match(lines[cursor]):
            if working_dir is None:
                wd_match = _WD_RE.match(lines[cursor])
                if wd_match:
                    working_dir = wd_match.group(1)
            if command is None:
                run_match = _RUN_RE.match(lines[cursor])
                if run_match:
                    command = run_match.group(1)
            cursor += 1
        if command:
            jobs.append((name, working_dir, command))
        index = cursor if cursor > index else index + 1
    return jobs


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


def target_paths(command: str) -> list[str]:
    """Return the path arguments of one ruff command (flags stripped).

    Used by the pre-flight check, because of a **measured** ruff behaviour:

        ruff check --select F821 no/such/path.py   -> exit 0   (!)
        ruff check --select F821 no/such/dir       -> exit 0   (!)
        ruff check --select F821 <file with F821>  -> exit 1

    A mistyped path therefore lints **nothing** and reports success. That is the
    same fail-open shape as a gate that passes when its input is absent, and it
    would make this very script lie about matching CI. So resolve the paths
    ourselves and refuse to run a command whose targets are not on disk.

    Parsing rules (each one exists because a naive version got it wrong — the
    first cut treated ``check`` and the value of ``--extend-ignore`` as paths
    and flagged all 14 jobs):

      * the leading ``ruff`` token is dropped by the caller;
      * a token after a :data:`_VALUE_FLAGS` flag is that flag's value, not a path;
      * :data:`_SUBCOMMANDS` are commands, not paths;
      * everything else is a path argument.
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
    and cannot exist on disk. Filter them by shape rather than by position:
    a value that looks like a rule list is skipped.
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


def run_all(*, verbose: bool = False, list_only: bool = False) -> int:
    """Run each extracted command; print a per-job line and a final summary.

    Returns
    -------
        ``0`` when every command passes, ``1`` otherwise (fail-closed: an
        extraction failure or a missing workflow is also a non-zero exit).
    """
    try:
        jobs = extract_commands()
    except FileNotFoundError as exc:
        print(f"✗ {exc}", file=sys.stderr)
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
            print(f"  {name}{where}\n      ruff {command}")
        return 0

    # ★ 前置守卫：见 target_paths() 的说明 —— ruff 对不存在的路径**退出 0**，
    #   故「绿」可能只是「一个文件都没 lint」。先把这种情形挡在跑之前。
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

    print(f"=== CI ruff jobs, run verbatim ({len(jobs)} commands from {WORKFLOW.name}) ===")
    failures: list[tuple[str, str | None, str, str]] = []
    for name, working_dir, command in jobs:
        cwd = (REPO_ROOT / working_dir) if working_dir else REPO_ROOT
        if verbose:
            print(f"      ruff {command}   [cwd={working_dir or '.'}]")
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
        print(f"    command: ruff {command}")
        for line in output.splitlines()[:15]:
            print(f"    {line}")
        print()

    passed = len(jobs) - len(failures)
    print(f"=== {passed}/{len(jobs)} CI ruff steps PASS ===")
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
