"""Minidump 解析：提取 BugCheck 参数与加载的驱动。

背景：本机事件日志显示 BugCheck 0x20001 (HYPERVISOR_ERROR)，param1=0x26。
但要定位**具体是哪个驱动/模块**，需要解析 minidump。

本脚本做一个轻量的小型转储头解析：
  - 读 MINIDUMP_HEADER，定位 SYSTEM_INFO / MODULE_LIST / EXCEPTION 流
  - 输出模块列表（驱动名 + 基址 + 大小），便于按时间戳/大小辨识嫌疑模块

注意：不做完整符号解析（需要微软符号服务器 + dbghelp）。
本脚本只做「结构解析 + 模块枚举」，足以回答「崩溃时加载了什么」。

用法: python scripts/parse-minidump.py [dump路径]
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

DEFAULT = Path("C:/Windows/Minidump/092026-16046-01.dmp")

# MINIDUMP_STREAM_TYPE
STREAM_MODULE_LIST = 4
STREAM_SYSTEM_INFO = 7
STREAM_MISC_INFO = 15
STREAM_EXCEPTION = 6

# MINIDUMP_MISC_INFO flags
MISC_FLAG_PROCESS_TIMES = 0x2


def read_header(buf: bytes) -> dict:
    sig, ver, nstreams, rva, checksum, ts, flags = struct.unpack_from("<IIIIIIQ", buf, 0)
    return {
        "signature": hex(sig),
        "is_minidump": sig == 0x504D444D,          # 'MDMP'
        "version": hex(ver),
        "n_streams": nstreams,
        "stream_dir_rva": rva,
        "checksum": checksum,
        "timestamp": ts,
        "flags": hex(flags),
    }


def read_stream_dirs(buf: bytes, n: int, rva: int) -> list[tuple[int, int, int]]:
    out = []
    for i in range(n):
        t, size, loc = struct.unpack_from("<III", buf, rva + i * 12)
        out.append((t, size, loc))
    return out


def parse_module_list(buf: bytes, rva: int) -> list[dict]:
    n = struct.unpack_from("<I", buf, rva)[0]
    mods = []
    base = rva + 4
    for i in range(n):
        off = base + i * 108
        if off + 108 > len(buf):
            break
        (
            base_of_image, size_of_image, checksum, timestamp, name_rva,
        ) = struct.unpack_from("<QIIII", buf, off)
        # MINIDUMP_LOCATION_DESCRIPTOR at offset 24
        name_len, name_loc = struct.unpack_from("<II", buf, off + 24 + 4 + 4)
        # 简化：MINIDUMP_STRING = 4-byte length + UTF-16LE
        name = ""
        try:
            slen = struct.unpack_from("<I", buf, name_loc)[0]
            raw = buf[name_loc + 4: name_loc + 4 + slen]
            name = raw.decode("utf-16-le", errors="replace")
        except Exception:
            pass
        mods.append({
            "base": base_of_image,
            "size": size_of_image,
            "timestamp": timestamp,
            "name": name,
        })
    return mods


def parse_misc_info(buf: bytes, rva: int) -> dict:
    size_of_info, flags = struct.unpack_from("<II", buf, rva)
    out: dict = {"size_of_info": size_of_info, "flags": hex(flags)}
    off = rva + 8
    for label in ("ProcessId", "ProcessCreateTime", "ProcessUserTime", "ProcessKernelTime"):
        if off + 4 > len(buf):
            break
        out[label] = struct.unpack_from("<I", buf, off)[0]
        off += 4
    return out


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    if not path.exists():
        print(f"dump not found: {path}")
        sys.exit(1)

    buf = path.read_bytes()
    print(f"=== {path.name} ({len(buf):,} bytes) ===")

    hdr = read_header(buf)
    print("\n--- MINIDUMP_HEADER ---")
    for k, v in hdr.items():
        print(f"  {k}: {v}")

    if not hdr["is_minidump"]:
        print("  NOT a minidump!")
        sys.exit(1)

    dirs = read_stream_dirs(buf, hdr["n_streams"], hdr["stream_dir_rva"])
    print(f"\n--- Streams ({len(dirs)}) ---")
    for t, size, loc in dirs:
        print(f"  type={t:>3}  size={size:>10}  rva={loc}")

    # Module list
    mod_stream = next((d for d in dirs if d[0] == STREAM_MODULE_LIST), None)
    if mod_stream:
        mods = parse_module_list(buf, mod_stream[2])
        print(f"\n--- Modules ({len(mods)}) ---")
        # 关注的：驱动（.sys）与非微软路径
        sysmods = [m for m in mods if m["name"].lower().endswith(".sys")]
        print(f"  .sys modules: {len(sysmods)}")
        for m in sysmods[:60]:
            nm = m["name"].rsplit("\\", 1)[-1]
            print(f"    {m['base']:#018x}  {m['size']:>9,}  {nm}")

        print("\n  --- non-System32 modules (third-party interest) ---")
        for m in mods:
            low = m["name"].lower()
            if m["name"].lower().endswith(".sys"):
                continue
            if "\\windows\\system32" in low or "\\windows\\winsxs" in low:
                continue
            nm = m["name"].rsplit("\\", 1)[-1]
            if nm:
                print(f"    {m['base']:#018x}  {m['size']:>9,}  {nm}")

    # Misc info
    misc = next((d for d in dirs if d[0] == STREAM_MISC_INFO), None)
    if misc:
        print("\n--- MISC_INFO ---")
        for k, v in parse_misc_info(buf, misc[2]).items():
            print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
