#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
doc_health.py — 文档库健康检查（可重复运行）

背景（2026-09-14 建立）
--------------------------------------------------------------------
本仓库在 2026-08 经历了文档堆积：10 天内新增 96 份文档，而 SSOT 只吸收 3 条
决策。根因是「吸收机制」与「退役机制」双双缺位，导致：

  1. 索引滞后 —— doc/README.md 只覆盖 28% 的 spec，新会话看不到全貌。
  2. 指针失效 —— 目录重排后引用未同步；git filter-repo 重写历史后 commit
     SHA 全部失效，决策书的「来源」列整体不可验证。
  3. 无退役 —— 两个月新增 292 份、删除 1 份。写文档没有成本，删文档没有机制。
  4. 堆文档 —— 各 agent 各自落盘 spec/reports/research，无人汇总。

本脚本把这些检查固化成可反复运行的命令，替代「靠人记忆」。

检查项
--------------------------------------------------------------------
  [LINK]  Markdown 链接与反引号路径 → 目标文件是否存在
  [CODE]  源码注释里的文档引用 → 目标是否存在
  [SSOT]  决策书条目：commit SHA 是否存在、校验命令引用的文件是否存在
  [INDEX] 索引覆盖率：doc/README.md 是否收录实际存在的文档
  [STALE] 陈旧度：文档最后修改时间 vs 仓库 HEAD
  [ORPHAN] 零引用且无索引的文档（「写完即弃」候选）

用法
--------------------------------------------------------------------
  python scripts/doc_health.py                  # 全部检查
  python scripts/doc_health.py --check link     # 单项
  python scripts/doc_health.py --quiet          # 只报问题，不报统计
  python scripts/doc_health.py --json           # 机器可读

退出码
--------------------------------------------------------------------
  0 = 无 block 级问题
  1 = 存在 block 级问题（死链 / SSOT 指针失效）
  2 = 脚本自身错误

注意：务必用项目 venv 的 python，裸 `python` 在此机器上是 Windows Store
stub（exit 49 且无输出）：
  /d/AI/envs/joyai-main/python.exe scripts/doc_health.py
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# 扫描时跳过的目录（依赖 / 缓存 / 归档 / 外部状态）
SKIP_DIRS = {
    ".git", ".cache", ".venv", "node_modules", "__pycache__",
    ".pytest_cache", ".ruff_cache", "dist", "build", "htmlcov",
    "site-packages", ".mypy_cache", "archive", ".workbuddy",
    ".workbuddy_tmp", "doc/deprecated", "doc/research/turn-controller-2026-08-11",
    "doc/research/addressee-detection-2026-08-12",
}

TEXT_EXT = {".md", ".py", ".js", ".ts", ".html", ".css", ".ps1", ".sh",
            ".toml", ".yml", ".yaml", ".json", ".mermaid"}

# ⚠️ 绝不改写的路径：这些是【事实记录】而非文档。
# 历史日志 / 运行报告 / 证据快照一旦被「修正」，就失去了作为记录的价值。
# 2026-09-14 教训：一次批量路径替换曾误改 logs/drift-gate-history/ 下 13 份
# 运行日志（把当时的 decision_ref 值改成修正后的值），造成历史失真且
# 因该目录未被 git 跟踪而无法还原。任何批量替换脚本都必须排除本清单。
NEVER_REWRITE = (
    "logs/", "services/logs/", "services/.logs/",
    "reports/webui-preview-", "doc/research/data/",
    ".cache/", ".workbuddy/", ".workbuddy_tmp/",
    "doc/deprecated/",
)

# 文档引用形态： doc/xxx/yyy.md  /  `xxx.md`  /  [t](path)
# 注意：扩展名按长度降序排列，并以 (?![A-Za-z0-9]) 收尾，否则 `package.json`
# 会被截断成 `package.js`，`*.json` 一律误报。
DOC_PATH_RE = re.compile(
    r"(?:\.\./)*(?:doc|docs|services|scripts|决策|reports)/"
    r"[A-Za-z0-9_.\u4e00-\u9fff/-]+?"
    r"\.(?:json|yaml|yml|mermaid|toml|ps1|sh|md|py|js)"
    r"(?![A-Za-z0-9])"
)
BARE_MD_RE = re.compile(r"`([A-Za-z0-9_.\u4e00-\u9fff-]+\.md)`")
SHA_RE = re.compile(r"\b([0-9a-f]{7,40})\b")

# 只在「来源 / 校验」字段里查 SHA，避免把行号、hash 字面量误判
DECISION_ENTRY_RE = re.compile(r"^#{2,4}\s+(D-[\w-]+)", re.M)
FIELD_RE = re.compile(r"^\|\s*\*\*(来源|校验)\*\*\s*\|(.*?)\|\s*$", re.M)

# 历史留痕型文件：它们记录「当时」的路径，路径失效是史实而非缺陷。
# 对这些文件中的死链降级为 info，避免噪声淹没真问题。
HISTORICAL_FILES = {
    "DELIVERY.md",                 # 变更记录（按日期累积，记录当时的文件树）
    "doc/specs/2026-07-14-project-audit.md",
    "doc/local/architecture-current.md",
    "doc/deprecated/README.md",
}
HISTORICAL_PREFIXES = ("reports/", "doc/research/block", "doc/research/kws-",
                       "doc/research/source-project", "doc/research/lightweight-",
                       # 已明确标注「过时/非现行」的目录（2026-09-14 加警示块）
                       "doc/local/", "doc/api/", "logs/")

# 文档若在开头 N 行内含这些标记，视为已声明「历史/过时」→ 死链降级 info。
# 这样「已标注的陈旧文档」不会再淹没「未标注的真断链」。
OBSOLETE_MARKERS = ("已归档：本文不是现行", "已过时：本文不是现行",
                    "历史快照", "已失效", "⚠️ 部分失效", "❌ 已失效",
                    "本文件不是现行", "已过时（")
OBSOLETE_HEAD_LINES = 40

# 「校验备注」标记：行内声明「此处引用不存在」属于记录事实，不是活指针。
# quality.yml、doc/specs 等处的核查备注用 natural language 表达，不是 HTML 注释。
AUDIT_NOTE_MARKERS = ("从未落盘", "不存在", "已移除", "已失效", "从未存在",
                      "仅存在于 tag", "核查", "known-absent")

# 命令示例模式：`bash scripts/run.sh`、`./scripts/stop.sh` 这类是【在某个目录下
# 执行】的示例，路径相对执行位置而非仓库根。识别到命令行前缀即豁免。
COMMAND_CTX_RE = re.compile(
    r"(?:bash|sh|source|\./|python|python3|pwsh|\.\\|nohup|exec)\s+\S*$"
)

# URL 中的路径片段：`https://raw.githubusercontent.com/<repo>/main/scripts/install.ps1`
# 指向的是【远程仓库】的文件，不是本地路径。
# 2026-09-14 教训：一次「修正」曾把 Hermes 上游的 `scripts/install.ps1` 误改成
# 本地路径，破坏了正确的外部引用。凡行内含 http(s):// 即豁免。
URL_CTX_RE = re.compile(r"https?://\S*$", re.I)

# ⚠️ 2026-09-14 三次事故总结（写入此处防止重犯）：
#   事故 1：批量路径替换误改 `logs/drift-gate-history/` 运行日志 → 历史失真且
#           该目录未被 git 跟踪，无法还原。
#   事故 2：把 Hermes 上游 URL 里的 `scripts/install.ps1` 当成本地路径「修正」，
#           破坏了正确的外部引用。
#   事故 3：给 `services/scripts/stop.sh` / `install/install.sh` 追加 HTML 注释
#           `<!-- known-absent -->` 试图豁免告警 —— shell 中这是语法错误，
#           直接**破坏了两个脚本**（`bash -n` 报 syntax error）。
#   共同根因：**用「修改被测对象」来解决「检测器误报」**。
#   正确做法：改进检测器（见下方各豁免规则），
#             或在不改变语义的前提下换用目标语言原生的注释（`#`、`//`、`<!-- -->` 仅限 .md/html）。
SAFE_COMMENT_EXT = {".md", ".html", ".htm"}

# 变量拼接路径：`"${SERVICES_DIR}/asr/scripts/run.sh"` 无法静态解析 → 豁免。
VAR_PATH_RE = re.compile(r"[\"'`]?\$[{A-Za-z_]|\$\{?[A-Za-z_]+\}?/")

# 本检查器自身：其文档字符串/注释里含正则与路径示例，不应自检。
SELF_FILE = "scripts/doc_health.py"

# 说明性注释：`# NOTE: ... use scripts/run-windows.ps1 ... instead` 是在
# **推荐替代方案**，不是在声明一个本文件依赖的路径 → 豁免。
NOTE_CTX_RE = re.compile(r"(?i)\b(note|instead|use|see|refer|deprecated|replaced by)\b")

# 显式「已知缺失」标记：文档故意提到一个不存在的路径（例如记录"该工具从未
# 落盘""证据目录已丢失"）。出现该标记的行整行跳过，避免把「记录缺失」误报
# 为「指针失效」。
KNOWN_ABSENT_MARK = "known-absent"


def rel(p: Path) -> str:
    try:
        return p.relative_to(REPO).as_posix()
    except ValueError:
        return p.as_posix()


def iter_files(exts: set[str] | None = None):
    """遍历工作树内的文本文件（跳过 SKIP_DIRS）。"""
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in {".git", ".cache", ".venv",
                                                "node_modules", "__pycache__",
                                                ".pytest_cache", ".ruff_cache"}]
        rootp = Path(root)
        r = rel(rootp)
        if any(r == s or r.startswith(s + "/") for s in SKIP_DIRS):
            continue
        for f in files:
            p = rootp / f
            if exts is not None and p.suffix not in exts:
                continue
            yield p


def resolve_doc_ref(ref: str, origin: Path) -> Path | None:
    """把文档引用解析为真实路径。

    解析顺序很重要：服务 README 里的 `scripts/run.sh` 指的是
    `<service>/scripts/run.sh`（相对自身），而非仓库根的 `scripts/`。
    故**先试引用者所在目录**，再退到仓库根。
    """
    ref = ref.strip().strip("`").lstrip("./")
    base = origin if origin.is_dir() else origin.parent
    for c in (base / ref, REPO / ref):
        try:
            c = c.resolve()
        except OSError:
            continue
        if c.exists():
            return c
    return None


def _resolve_bare(name: str) -> bool:
    """解析「裸名字或相对路径」。纯文件名无法可靠定位，在常见目录里各试一次；
    找不到不算死链（避免把 `models.py` 这类通用名误报为失效）。"""
    if "/" in name:
        if resolve_doc_ref(name, REPO) is not None:
            return True
    hot = ["", "scripts/", "services/", "services/webui/tests/",
           "services/webinfer/", "services/webui/src/joy_interaction_webui/",
           "决策/", "doc/specs/", "config/",
           # 协作者记忆目录：物理存在但被 gitignore（历史决策的旁证）
           ".workbuddy/memory/", ".workbuddy/tmp/"]
    return any((REPO / h / name).exists() for h in hot)


# --------------------------------------------------------------------------
# 检查 1：文档与代码里的指针是否有效
# --------------------------------------------------------------------------
def _severity_for(file_rel: str, head_text: str = "") -> str:
    """历史留痕 / 已声明过时的文件里的旧路径是史实，不是缺陷 → 降级为 info。"""
    if file_rel in HISTORICAL_FILES or file_rel.startswith(HISTORICAL_PREFIXES):
        return "info"
    # 文件开头已自声明「过时/历史/已失效」→ 其内部死链不算活指针失效
    head = "\n".join(head_text.splitlines()[:OBSOLETE_HEAD_LINES])
    if any(mk in head for mk in OBSOLETE_MARKERS):
        return "info"
    return "block"


def check_links() -> list[dict]:
    issues: list[dict] = []
    for p in iter_files(TEXT_EXT):
        if p.suffix == ".mermaid":
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        rp = rel(p)
        if rp == SELF_FILE:
            continue  # 本检查器自身含正则/路径示例，不自检
        seen: set[str] = set()
        # 逐行处理，便于识别「已知缺失」标记行
        lines = text.splitlines()
        for lineno, line_text in enumerate(lines, 1):
            if KNOWN_ABSENT_MARK in line_text:
                continue
            # 核查备注行（声明「此处引用已失效」）是记录事实，不是活指针
            if any(mk in line_text for mk in AUDIT_NOTE_MARKERS):
                continue
            for m in DOC_PATH_RE.finditer(line_text):
                ref = m.group(0)
                if ref in seen:
                    continue
                seen.add(ref)
                # 命令示例（`bash scripts/run.sh`）：路径相对执行目录，非仓库根
                if COMMAND_CTX_RE.search(line_text[: m.start()]):
                    continue
                # URL 里的路径（指向远程仓库，不是本地文件）
                if URL_CTX_RE.search(line_text[: m.start()]):
                    continue
                # 变量拼接路径（`"${SERVICES_DIR}/asr/scripts/run.sh"`）无法静态解析
                if VAR_PATH_RE.search(line_text[: m.start()]):
                    continue
                # 说明性注释（"改用 xxx 脚本"）是推荐，不是依赖声明
                if NOTE_CTX_RE.search(line_text):
                    continue
                # 省略号缩写（如 services/webui/.../jarvis_mode.py）是文档惯用的
                # 概略写法，不是可解析路径 → 跳过，否则噪声淹没真问题。
                if "..." in ref:
                    continue
                if resolve_doc_ref(ref, p) is None:
                    issues.append({
                        "check": "LINK", "severity": _severity_for(rp, text),
                        "file": rp, "line": lineno, "ref": ref,
                        "msg": f"引用的路径不存在: {ref}",
                    })
    return issues


def check_code_comments() -> list[dict]:
    """源码（非 .md）注释里的文档引用。这是 agent 唯一的「就近索引」。"""
    issues: list[dict] = []
    for p in iter_files({".py", ".js", ".html", ".css", ".ps1", ".sh"}):
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for m in BARE_MD_RE.finditer(text):
            ref = m.group(1)
            # 只关心看起来像项目文档的引用
            if not re.match(r"(draft-|doc-|[a-z0-9-]+-(spec|mode|ui|detection|controller|layer|cb|silence|convergence|unified|bridge|delegation|tuning|boundaries)\.md)", ref):
                continue
            if resolve_doc_ref(ref, p) is None and not (REPO / "doc/specs" / ref).exists():
                line = text[: m.start()].count("\n") + 1
                issues.append({
                    "check": "CODE", "severity": "block",
                    "file": rel(p), "line": line, "ref": ref,
                    "msg": f"源码注释引用的文档不存在: {ref}",
                })
    return issues


# --------------------------------------------------------------------------
# 检查 2：SSOT（决策/）的自检能力
# --------------------------------------------------------------------------
def _git_has_object(sha: str) -> bool:
    try:
        r = subprocess.run(["git", "cat-file", "-t", sha], cwd=REPO,
                           capture_output=True, text=True, timeout=10)
        return r.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


def check_ssot() -> list[dict]:
    issues: list[dict] = []
    ddir = REPO / "决策"
    if not ddir.is_dir():
        return issues
    for p in sorted(ddir.glob("*.md")):
        text = p.read_text(encoding="utf-8")
        # 拆成条目块，便于定位是哪个 D-id 出的问题
        marks = [(m.start(), m.group(1)) for m in DECISION_ENTRY_RE.finditer(text)]
        marks.append((len(text), None))

        # a) 校验/来源字段里引用的文件是否存在
        for fm in re.finditer(r"^\|\s*\*\*(来源|校验|对话证据)\*\*\s*\|(.*?)\|\s*$",
                              text, re.M):
            field, body = fm.group(1), fm.group(2)
            line = text[: fm.start()].count("\n") + 1
            # 整行带「已知缺失」标记 → 跳过（记录缺失 ≠ 指针失效）
            line_text = text.splitlines()[line - 1] if line - 1 < len(text.splitlines()) else ""
            if KNOWN_ABSENT_MARK in line_text or KNOWN_ABSENT_MARK in body:
                continue
            did = "?"
            for i in range(len(marks) - 1):
                if marks[i][0] <= fm.start() < marks[i + 1][0]:
                    did = marks[i][1]
                    break
            # 纯文件名（无目录分隔）无法可靠解析：它可能属于仓库任一层。
            # 复用模块级 _resolve_bare（在常见子目录各试一次，找不到不算死链）。
            for ref in re.findall(r"`([^`]+\.(?:md|py|sh|ps1|json))`", body):
                # 反引号里常是整条命令（`grep -n "x" services/a/b.py`），
                # 需先抽出其中的路径 token，否则整条命令会被当作路径。
                cands = re.findall(
                    r"[A-Za-z0-9_.\u4e00-\u9fff/-]+\.(?:md|py|sh|ps1|json)",
                    ref)
                for c in (cands or [ref]):
                    if c.startswith("-") or _resolve_bare(c):
                        continue
                    issues.append({
                        "check": "SSOT", "severity": "block",
                        "file": rel(p), "line": line, "ref": c, "did": did,
                        "msg": f"[{did}] {field} 引用的文件不存在: {c}",
                    })
            # b) commit SHA 是否仍可解析（历史重写后普遍失效）
            for sha in SHA_RE.findall(body):
                if len(sha) < 7 or sha.isdigit():
                    continue
                if not _git_has_object(sha):
                    issues.append({
                        "check": "SSOT", "severity": "warn",
                        "file": rel(p), "line": line, "ref": sha, "did": did,
                        "msg": f"[{did}] {field} 引用的 commit 已不在对象库: {sha}",
                    })
    return issues


# --------------------------------------------------------------------------
# 检查 3：索引覆盖率
# --------------------------------------------------------------------------
INDEXED_DIRS = {
    "doc/specs": REPO / "doc" / "specs" / "README.md",
    "doc/subsystems": REPO / "doc" / "README.md",
    "doc/adr": REPO / "doc" / "README.md",
    # 以下目录以「目录级 README.md」为索引（2026-09-14 建立）
    "doc/research": REPO / "doc" / "research" / "README.md",
    "docs": REPO / "docs" / "README.md",
    "reports": REPO / "reports" / "README.md",
    "doc/acceptance": REPO / "doc" / "acceptance" / "README.md",
    "doc/architecture": REPO / "doc" / "architecture" / "README.md",
}


def check_index() -> list[dict]:
    issues: list[dict] = []
    for d, index in INDEXED_DIRS.items():
        dp = REPO / d
        if not dp.is_dir():
            continue
        files = sorted(x.name for x in dp.glob("*.md")
                       if x.name not in {"README.md"})
        if not files:
            continue
        if index is None or not index.exists():
            issues.append({
                "check": "INDEX", "severity": "warn", "file": d, "line": 0,
                "ref": "", "msg": f"{d}/ 无索引文件，{len(files)} 份文档对 AI 新会话等同不存在",
            })
            continue
        idx = index.read_text(encoding="utf-8")
        missing = [f for f in files if f not in idx]
        if missing:
            cov = (len(files) - len(missing)) * 100 // len(files)
            issues.append({
                "check": "INDEX", "severity": "warn", "file": d, "line": 0,
                "ref": "",
                "msg": f"{d}/ 索引覆盖率 {cov}%（{len(missing)}/{len(files)} 份未收录）"
                       f"，如: {', '.join(missing[:4])}",
            })
    return issues


# --------------------------------------------------------------------------
# 检查 4：陈旧度
# --------------------------------------------------------------------------
def git_last_commit(path: Path) -> tuple[str, str]:
    """返回 (日期, 提交数)。"""
    try:
        r = subprocess.run(
            ["git", "log", "--format=%ad", "--date=short", "--follow", "--", rel(path)],
            cwd=REPO, capture_output=True, text=True, timeout=15)
        dates = [x for x in r.stdout.split("\n") if x.strip()]
        return (dates[0] if dates else "?"), str(len(dates))
    except (subprocess.SubprocessError, OSError):
        return "?", "0"


def check_stale(days: int = 60) -> list[dict]:
    issues: list[dict] = []
    head_date = subprocess.run(["git", "log", "-1", "--format=%ad", "--date=short"],
                               cwd=REPO, capture_output=True, text=True).stdout.strip()
    for d in ["doc/specs", "doc/subsystems", "doc/adr", "doc/research",
              "doc/architecture", "doc/acceptance", "docs", "reports"]:
        dp = REPO / d
        if not dp.is_dir():
            continue
        for p in sorted(dp.glob("*.md")):
            date, n = git_last_commit(p)
            if date == "?":
                continue
            issues.append({
                "check": "STALE", "severity": "info",
                "file": rel(p), "line": 0, "ref": date,
                "commits": n, "msg": f"最后更新 {date}（{n} 次提交）",
            })
    return issues


# --------------------------------------------------------------------------
# 检查 5：零引用文档
# --------------------------------------------------------------------------
def check_orphan() -> list[dict]:
    """没有任何其他文件引用、且不在索引里的 .md —— 「写完即弃」候选。"""
    issues: list[dict] = []
    all_md = [p for p in iter_files({".md"})]
    names = {p.name: p for p in all_md}
    referenced: set[str] = set()
    for p in iter_files(TEXT_EXT):
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for name in names:
            if name in text and names[name] != p:
                referenced.add(name)
    for d in ["doc/specs", "doc/research", "doc/architecture", "doc/acceptance",
              "docs", "doc/adr", "reports"]:
        dp = REPO / d
        if not dp.is_dir():
            continue
        for p in sorted(dp.glob("*.md")):
            if p.name in {"README.md"}:
                continue
            if p.name not in referenced:
                issues.append({
                    "check": "ORPHAN", "severity": "info",
                    "file": rel(p), "line": 0, "ref": "",
                    "msg": "零引用（无任何文件提及，且不在索引中）",
                })
    return issues


CHECKS = {
    "link": check_links,
    "code": check_code_comments,
    "ssot": check_ssot,
    "index": check_index,
    "stale": check_stale,
    "orphan": check_orphan,
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--check", action="append", choices=sorted(CHECKS),
                    help="只跑指定检查（可多次）")
    ap.add_argument("--quiet", action="store_true", help="只输出问题")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    if not (REPO / ".git").exists():
        print(f"[FATAL] 不是 git 仓库: {REPO}", file=sys.stderr)
        return 2

    selected = args.check or sorted(CHECKS)
    all_issues: list[dict] = []
    for name in selected:
        try:
            all_issues.extend(CHECKS[name]())
        except Exception as exc:  # noqa: BLE001 - 单检查失败不应终止整体
            print(f"[ERROR] check '{name}' 崩溃: {exc}", file=sys.stderr)

    if args.json:
        print(json.dumps(all_issues, ensure_ascii=False, indent=2))
        return 1 if any(i["severity"] == "block" for i in all_issues) else 0

    grouped: dict[str, list[dict]] = defaultdict(list)
    for i in all_issues:
        grouped[i["check"]].append(i)

    TITLE = {
        "LINK": "指针失效（文档间引用）",
        "CODE": "指针失效（源码注释 → 文档）",
        "SSOT": "决策书自检能力",
        "INDEX": "索引覆盖率",
        "STALE": "陈旧度",
        "ORPHAN": "零引用文档",
    }
    ORDER = ["LINK", "CODE", "SSOT", "INDEX", "ORPHAN", "STALE"]

    print("=" * 72)
    print(f"  文档库健康检查  |  {REPO.name}")
    print("=" * 72)

    for key in ORDER:
        items = grouped.get(key, [])
        blocks = [i for i in items if i["severity"] == "block"]
        warns = [i for i in items if i["severity"] == "warn"]
        if not items and args.quiet:
            continue
        mark = "🔴" if blocks else ("🟡" if warns else "✅")
        print(f"\n{mark} [{key}] {TITLE[key]} — {len(items)} 项"
              f"（block {len(blocks)} / warn {len(warns)}）")
        show = blocks + warns if args.quiet else items
        for i in show[:200]:
            loc = f"{i['file']}:{i['line']}" if i.get("line") else i["file"]
            print(f"    {loc}  {i['msg']}")
        if len(show) > 200:
            print(f"    ... 另有 {len(show) - 200} 项（用 --json 看全部）")

    nb = sum(1 for i in all_issues if i["severity"] == "block")
    nw = sum(1 for i in all_issues if i["severity"] == "warn")
    print("\n" + "=" * 72)
    print(f"  合计: block {nb} / warn {nw} / 全部 {len(all_issues)}")
    if nb:
        print("  结论: 🔴 存在 block 级问题（死链或 SSOT 指针失效），需修复")
    elif nw:
        print("  结论: 🟡 无死链，但有索引/覆盖告警")
    else:
        print("  结论: ✅ 通过")
    print("=" * 72)
    return 1 if nb else 0


if __name__ == "__main__":
    sys.exit(main())
