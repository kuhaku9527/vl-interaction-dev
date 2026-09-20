"""找出「含非 ASCII 但缺 UTF-8 BOM」的 PowerShell/env 文件。

背景：Windows PowerShell 5.1 在**无 BOM** 时按系统 ANSI 代码页（本机 GBK）解码，
UTF-8 的中文会被读成乱码，连带破坏引号/括号配对，报「缺少表达式 / 字符串缺少终止符」。
PowerShell 7（pwsh）默认按 UTF-8 读，不受影响。

修法：给文件加 UTF-8 BOM（EF BB BF），两种 PowerShell 都能正确解析。

用法: python scripts/find-ps1-encoding-issues.py [--fix]
"""
from __future__ import annotations

import pathlib
import sys

SKIP_PARTS = ("/.cache/", "/.git/", "/.venv/", "/.workbuddy", "/archive/",
              "/node_modules/", "/.workbuddy_tmp/", "/dist/", "/build/")

PATTERNS = ("**/*.ps1", "**/*.psm1", "**/*.psd1", "**/*.env", "**/*.cmd",
            "**/*.bat")


def iter_targets():
    seen: set[str] = set()
    for pat in PATTERNS:
        for f in pathlib.Path(".").glob(pat):
            s = f.as_posix()
            if any(x in "/" + s for x in SKIP_PARTS):
                continue
            if s in seen:
                continue
            seen.add(s)
            yield f, s


def main() -> None:
    fix = "--fix" in sys.argv
    issues: list[tuple[str, int, int]] = []

    for f, s in iter_targets():
        try:
            raw = f.read_bytes()
        except Exception:
            continue
        if raw[:3] == b"\xef\xbb\xbf":
            continue
        non_ascii = sum(1 for x in raw if x > 127)
        if non_ascii > 0:
            issues.append((s, non_ascii, len(raw)))

    print(f"含非 ASCII 但缺 UTF-8 BOM 的文件：{len(issues)} 个")
    print()
    for s, na, n in sorted(issues, key=lambda x: -x[1]):
        print(f"  {s:60s} 非ASCII {na:>5}B / {n:>8}B")

    if not issues:
        print("  （无）")
        return

    if not fix:
        print()
        print("加 --fix 以补 BOM。")
        return

    print()
    print("=== 补 BOM ===")
    for s, _, _ in issues:
        f = pathlib.Path(s)
        raw = f.read_bytes()
        if raw[:3] == b"\xef\xbb\xbf":
            continue
        f.write_bytes(b"\xef\xbb\xbf" + raw)
        print(f"  [fixed] {s}")


if __name__ == "__main__":
    main()
