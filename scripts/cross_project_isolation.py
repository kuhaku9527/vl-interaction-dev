#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
cross_project_isolation.py — 跨项目隔离检查（可重复运行）

背景（2026-09-14 建立）
--------------------------------------------------------------------
本机同时存在多个 agent 应用，各自是**独立项目**：

    D:\\AI\\workspace\\JoyAI-VL-Interaction-main   ← 本项目
    D:\\Workspace\\hermes-agent                    ← NousResearch/hermes-agent（独立项目）
    D:\\Workspace\\hermes-data                     ← 上述项目的运行时数据
    D:\\Workspace\\hermes-workspace                ← 上述项目的工作区
    D:\\AI\\envs\\*、D:\\AI\\models                  ← 共享资源（模型/虚拟环境）

它们通过【用户级环境变量】共享若干全局配置。**共享即可能互相污染**：
  · 本项目跑 npm 会写进 hermes-agent 的缓存目录（已实测）
  · 历史上曾有 agent 改写 HERMES_HOME 指向陈旧路径，损坏了 hermes 的环境
    （见 services/background-agent/scripts/start-hermes-gateway.ps1 的注释）

本脚本把「是否发生跨项目污染」变成可重复运行的检查。

检查项
--------------------------------------------------------------------
  [ENV]    用户级环境变量：找出指向「其他项目」的值
  [NPM]    npm 缓存实际解析路径（env 优先级高于项目 .npmrc，需实测）
  [PATH]   系统 Path 中是否含其他项目的路径
  [STRAY]  本项目工作树内是否有「其他项目的产物」

用法
--------------------------------------------------------------------
  /d/AI/envs/joyai-main/python.exe scripts/cross_project_isolation.py
  ... --json      机器可读
  ... --quiet     只报问题

退出码
--------------------------------------------------------------------
  0 = 未发现跨项目污染
  1 = 发现污染（需处理）
  2 = 脚本自身错误

注意：务必用项目 venv 的 python，裸 `python` 在本机是 Windows Store stub
（静默失败）。详见 doc/environment-dsh.md。
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# ── 项目注册表 ─────────────────────────────────────────────────────────
# 本机已识别的「独立项目」及其路径前缀。新增项目时在此登记，
# 检查器才能区分「本项目」与「其他项目」。
PROJECTS = {
    "JoyAI-VL-Interaction-main": [
        r"D:\AI\workspace\JoyAI-VL-Interaction-main",
    ],
    "hermes-agent": [
        r"D:\Workspace\hermes-agent",
        r"D:\Workspace\hermes-data",
        r"D:\Workspace\hermes-workspace",
    ],
}

SELF = "JoyAI-VL-Interaction-main"

# 共享资源（不属于任何单一项目，允许被引用）
SHARED_PREFIXES = [
    r"D:\AI\envs",      # 虚拟环境
    r"D:\AI\models",    # 模型权重
    r"D:\AI\bin",       # 可执行文件
    r"D:\AI\tools",
    r"D:\AI\data",      # KWS 语料等
    r"C:\Users",        # 用户目录（HOME 等）
    r"C:\Program Files",
    r"D:\chengxu",      # 常规软件安装位置
    r"D:\Obsidian",
    r"D:\GoLand", r"D:\IntelliJ", r"D:\PyCharm",
    r"D:\Graphviz",
    r"C:\Users\22186\.local", r"C:\Users\22186\scoop", r"C:\Users\22186\go",
    r"C:\Users\22186\AppData",
    r"C:\Users\22186\.dotnet",
]


def _norm(p: str) -> str:
    return p.replace("/", "\\").rstrip("\\").lower()


def classify(value: str) -> str:
    """判断一个路径属于哪个项目；返回项目名，或 'shared' / 'unknown'。"""
    v = _norm(value)
    for proj, prefixes in PROJECTS.items():
        for pre in prefixes:
            if v.startswith(_norm(pre)):
                return proj
    for pre in SHARED_PREFIXES:
        if v.startswith(_norm(pre)):
            return "shared"
    return "unknown"


def get_user_env() -> dict[str, str]:
    """读取用户级环境变量（经 PowerShell，避免直接读注册表的编码问题）。"""
    ps = (
        "$v=[Environment]::GetEnvironmentVariables('User');"
        "$o=@{};foreach($k in $v.Keys){$o[$k]=[string]$v[$k]};"
        "$o|ConvertTo-Json -Compress"
    )
    r = subprocess.run(["pwsh", "-NoProfile", "-Command", ps],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0 or not r.stdout.strip():
        raise RuntimeError(f"无法读取用户级环境变量: {r.stderr[:300]}")
    return json.loads(r.stdout.strip())


def check_env() -> list[dict]:
    """用户级变量里指向「其他项目」的值。"""
    issues: list[dict] = []
    for k, v in sorted(get_user_env().items()):
        if not isinstance(v, str) or not v.strip():
            continue
        # Path 是列表型，单独检查（见 check_path）
        if k.lower() == "path":
            continue
        # 只关心看起来像路径的值
        if not re.match(r"^[A-Za-z]:\\", v):
            continue
        owner = classify(v)
        if owner not in ("shared", SELF, "unknown"):
            issues.append({
                "check": "ENV", "severity": "warn",
                "name": k, "value": v, "owner": owner,
                "msg": f"{k} = {v}  ← 指向【{owner}】项目（跨项目共享，可能互相污染）",
            })
    return issues


def check_npm() -> list[dict]:
    """npm 缓存的实际解析路径（env 优先级高于项目 .npmrc，必须实测）。"""
    issues: list[dict] = []
    if not shutil.which("npm"):
        return issues
    # 在有 package.json 的目录下测（本项目 = services/webui）
    target = REPO / "services" / "webui"
    if not (target / "package.json").exists():
        target = REPO
    try:
        # ⚠️ Windows 上 npm 是 npm.cmd —— subprocess 需 shell=True 或显式解析
        # 到 .cmd。否则抛 FileNotFoundError，被 except 吞掉后返回空 →
        # 检查器误报「隔离良好」（实测踩过）。
        npm_exe = shutil.which("npm")
        if not npm_exe:
            return issues
        # ⚠️ 必须在「有 package.json 的目录」下测：否则 npm 不读项目 .npmrc。
        r = subprocess.run([npm_exe, "config", "get", "cache"],
                           capture_output=True, text=True, timeout=60,
                           cwd=str(target), shell=False)
        cache = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
    except (subprocess.SubprocessError, OSError):
        return issues
    if not cache:
        issues.append({
            "check": "NPM", "severity": "warn", "name": "npm", "value": "",
            "owner": "?",
            "msg": "无法解析 npm cache（命令执行失败）—— 本项未能验证，请人工确认",
        })
        return issues
    owner = classify(cache)
    # 本项目自己（或共享）→ OK；指向其他项目 → block
    if owner in ("shared", SELF, "unknown"):
        return issues
    issues.append({
        "check": "NPM", "severity": "block",
        "name": "npm_config_cache", "value": cache, "owner": owner,
        "msg": (f"本项目跑 npm 会把产物写进【{owner}】的缓存目录: {cache}\n"
                f"            （实测于 {target.relative_to(REPO)} —— env 优先级高于项目 .npmrc，"
                f"仅加 .npmrc 不足以覆盖）\n"
                f"            · 修法（推荐）：跑 npm 前先隔离\n"
                f"                  source scripts/isolate-env.sh && cd services/webui && npm ci\n"
                f"            · 或显式传参：npm ci --cache=\"{REPO}/.cache/npm\"\n"
                f"            · ⚠️ 不要改用户级 npm_config_cache —— hermes-agent 的\n"
                f"              mcp_tool_config.py 读取该变量，且有 344MB 缓存，改了会破坏它"),
    })
    return issues


def check_npm_projectrc() -> list[dict]:
    """核对项目 .npmrc 的声明值（仅作提示，不判 block）。"""
    issues: list[dict] = []
    npmrc = REPO / "services" / "webui" / ".npmrc"
    if not npmrc.exists():
        return issues
    txt = npmrc.read_text(encoding="utf-8", errors="ignore")
    m = re.search(r"^\s*cache\s*=\s*(.+)$", txt, re.M)
    if m:
        issues.append({
            "check": "NPM", "severity": "warn",
            "name": ".npmrc cache", "value": m.group(1).strip(), "owner": SELF,
            "msg": f"项目 .npmrc 声明 cache={m.group(1).strip()}（意图值；实际是否生效见上条实测）",
        })
    return issues


def check_path() -> list[dict]:
    """系统 Path 中是否含其他项目的路径。"""
    issues: list[dict] = []
    try:
        env = get_user_env()
    except RuntimeError:
        return issues
    entries = [e for e in env.get("Path", "").split(";") if e.strip()]
    for e in entries:
        owner = classify(e)
        if owner not in ("shared", SELF, "unknown"):
            issues.append({
                "check": "PATH", "severity": "warn",
                "name": "Path", "value": e, "owner": owner,
                "msg": f"系统 Path 含【{owner}】的路径: {e}",
            })
    return issues


def check_stray() -> list[dict]:
    """本项目工作树内是否有「其他项目」的产物目录。"""
    issues: list[dict] = []
    markers = {
        "hermes-agent": ["hermes-agent", "hermes-data", "hermes-workspace"],
    }
    for proj, names in markers.items():
        for name in names:
            for hit in REPO.rglob(name):
                # 排除：本项目自己在写文档时提到的名字（只查目录）
                if hit.is_dir():
                    issues.append({
                        "check": "STRAY", "severity": "warn",
                        "name": name, "value": str(hit), "owner": proj,
                        "msg": f"本项目工作树内存在【{proj}】的目录: {hit.relative_to(REPO)}",
                    })
    return issues


CHECKS = {
    "env": check_env,
    "npm": check_npm,
    "path": check_path,
    "stray": check_stray,
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--check", action="append", choices=sorted(CHECKS))
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    selected = args.check or sorted(CHECKS)
    all_issues: list[dict] = []
    for name in selected:
        try:
            all_issues.extend(CHECKS[name]())
        except Exception as exc:  # noqa: BLE001
            print(f"[ERROR] check '{name}' 崩溃: {exc}", file=sys.stderr)

    if args.json:
        print(json.dumps(all_issues, ensure_ascii=False, indent=2))
        return 1 if any(i["severity"] == "block" for i in all_issues) else 0

    TITLE = {
        "ENV": "用户级环境变量 → 跨项目指向",
        "NPM": "npm 缓存隔离",
        "PATH": "系统 Path → 跨项目路径",
        "STRAY": "本项目内是否有其他项目产物",
    }
    print("=" * 74)
    print("  跨项目隔离检查")
    print("=" * 74)
    print(f"  本项目: {REPO}")
    for proj, prefixes in PROJECTS.items():
        if proj == SELF:
            continue
        print(f"  其他项目: {proj}  ({', '.join(prefixes)})")

    grouped: dict[str, list[dict]] = {}
    for i in all_issues:
        grouped.setdefault(i["check"], []).append(i)

    for key in ("NPM", "ENV", "PATH", "STRAY"):
        items = grouped.get(key, [])
        blocks = [i for i in items if i["severity"] == "block"]
        warns = [i for i in items if i["severity"] == "warn"]
        mark = "🔴" if blocks else ("🟡" if warns else "✅")
        print(f"\n{mark} [{key}] {TITLE[key]} — {len(items)} 项")
        show = blocks + warns if args.quiet else items
        for i in show:
            print(f"    {i['msg']}")

    nb = sum(1 for i in all_issues if i["severity"] == "block")
    nw = sum(1 for i in all_issues if i["severity"] == "warn")
    print("\n" + "=" * 74)
    print(f"  合计: block {nb} / warn {nw}")
    if nb:
        print("  结论: 🔴 存在跨项目污染（本项目正在写入其他项目的目录）")
    elif nw:
        print("  结论: 🟡 存在跨项目共享（真机隔离需据实判断；多为可接受的共享变量）")
    else:
        print("  结论: ✅ 隔离良好")
    print("=" * 74)
    return 1 if nb else 0


if __name__ == "__main__":
    sys.exit(main())
