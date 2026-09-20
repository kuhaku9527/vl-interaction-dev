"""Windows KERNEL minidump (PAGEDU64) parser - no debugger required.

Why this exists: kernel minidumps are NOT user-mode minidumps. Their magic is
'PAGE'/'PAGEDU64', not 'MDMP'. A user-mode parser (e.g. one looking for a
MINIDUMP_HEADER) silently mis-reads them, which is exactly the mistake made
earlier in this investigation.

This script reads the crash ONCE and reports, in order of forensic value:
  1. BugCheck code + 4 parameters  (authoritative; event log can disagree)
  2. Version / dump metadata (confirms which crash this is)
  3. Loaded driver list (base address + name)  <- names the actors
  4. Kernel call stack, with each return address RESOLVED to a driver
     by address range. This is the poor-man's !analyze: the module that owns
     the surviving frames is the prime suspect.

Usage:
  python scripts/parse-kernel-dump.py [dump] [--json]
"""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

DEFAULT = Path("D:/bsod-forensics/092026-16046-01.dmp")

# --- DUMP_HEADER64 ------------------------------------------------------
# Signature 'PAGE' at 0x00, ValidDump 'DU64' at 0x04.
HDR_SIZE = 0x2000  # kernel dump header is padded to 8 KiB

BUGCHECK_NAMES = {
    0x00020001: "HYPERVISOR_ERROR",
    0x0000000A: "IRQL_NOT_LESS_OR_EQUAL",
    0x0000001E: "KMODE_EXCEPTION_NOT_HANDLED",
    0x0000003B: "SYSTEM_SERVICE_EXCEPTION",
    0x00000050: "PAGE_FAULT_IN_NONPAGED_AREA",
    0x0000007E: "SYSTEM_THREAD_EXCEPTION_NOT_HANDLED",
    0x0000009F: "DRIVER_POWER_STATE_FAILURE",
    0x000000D1: "DRIVER_IRQL_NOT_LESS_OR_EQUAL",
    0x00000133: "DPC_WATCHDOG_VIOLATION",
    0x00000139: "KERNEL_SECURITY_CHECK_FAILURE",
    0x00000116: "VIDEO_TDR_FAILURE",
    0x0000010E: "VIDEO_MEMORY_MANAGEMENT_INTERNAL",
    0x000000EF: "CRITICAL_PROCESS_DIED",
}


def parse_header(buf: bytes) -> dict:
    sig = buf[0:4]
    valid = buf[4:8]
    h: dict = {
        "signature": sig.decode("latin-1", "replace"),
        "valid_dump": valid.decode("latin-1", "replace"),
    }
    if sig not in (b"PAGE", b"PAGEDU64", b"PAGEDUMP"):
        h["error"] = f"not a kernel dump (sig={sig!r})"
        return h

    (major, minor) = struct.unpack_from("<II", buf, 0x08)
    (dtb,) = struct.unpack_from("<Q", buf, 0x10)
    (pfn_base,) = struct.unpack_from("<Q", buf, 0x18)
    (ps_loaded,) = struct.unpack_from("<Q", buf, 0x20)
    (ps_proc,) = struct.unpack_from("<Q", buf, 0x28)
    (machine,) = struct.unpack_from("<I", buf, 0x30)
    (nproc,) = struct.unpack_from("<I", buf, 0x34)
    (bugcheck,) = struct.unpack_from("<I", buf, 0x38)
    # 0x3C is padding: the parameters are 8-byte aligned
    p1, p2, p3, p4 = struct.unpack_from("<QQQQ", buf, 0x40)
    version_user = buf[0x60:0x80]
    (kddb,) = struct.unpack_from("<Q", buf, 0x80)

    h.update({
        "major": major, "minor": minor,
        "dir_table_base": f"{dtb:#018x}",
        "pfn_data_base": f"{pfn_base:#018x}",
        "ps_loaded_module_list": f"{ps_loaded:#018x}",
        "ps_active_process_head": f"{ps_proc:#018x}",
        "machine_image_type": machine,
        "number_processors": nproc,
        "bugcheck_code": bugcheck,
        "bugcheck_name": BUGCHECK_NAMES.get(bugcheck, "(unknown)"),
        "bugcheck_params": [f"{p1:#018x}", f"{p2:#018x}", f"{p3:#018x}", f"{p4:#018x}"],
        "version_user": version_user.split(b"\x00")[0].decode("latin-1", "replace"),
        "kd_debugger_data_block": f"{kddb:#018x}",
    })
    return h


# --- TRIAGE_DUMP64 ------------------------------------------------------
# Immediately follows the 0x2000-byte header.
# Field offsets used below (ULONG unless noted):
#   0x00 ServicePackBuild      0x04 SizeOfDump
#   0x08 ValidOffset           0x0C ContextOffset
#   0x10 ExceptionOffset       0x14 MmOffset
#   0x18 UnloadedDriversOffset 0x1C PrcbOffset
#   0x20 ProcessOffset         0x24 ThreadOffset
#   0x28 CallStackOffset       0x2C SizeOfCallStack
#   0x30 DriverListOffset      0x34 DriverCount
#   0x38 StringPoolOffset      0x3C StringPoolSize
#   0x40 BrokenDriverOffset    0x44 TriageOptions
TRIAGE_OFF = HDR_SIZE


def u32(buf: bytes, off: int) -> int:
    return struct.unpack_from("<I", buf, off)[0]


def parse_triage(buf: bytes) -> dict:
    t = TRIAGE_OFF
    if t + 0x50 > len(buf):
        return {"error": "no triage data"}

    d = {
        "service_pack_build": u32(buf, t + 0x00),
        "size_of_dump": u32(buf, t + 0x04),
        "valid_offset": u32(buf, t + 0x08),
        "call_stack_offset": u32(buf, t + 0x28),
        "size_of_call_stack": u32(buf, t + 0x2C),
        "driver_list_offset": u32(buf, t + 0x30),
        "driver_count": u32(buf, t + 0x34),
        "string_pool_offset": u32(buf, t + 0x38),
        "string_pool_size": u32(buf, t + 0x3C),
    }

    # --- driver list: TRIAGE_DRIVER_ENTRY, 16 bytes each
    #     { ULONG64 Base; ULONG64 Size; ULONG NameOffset; ULONG pad/Filler; }
    # Some builds use { Base, Size|NameOffset packed, Filler, ... }; we read
    # the widely-documented 24-byte layout and validate before trusting it.
    drivers: list[dict] = []
    dl = d["driver_list_offset"]
    dc = d["driver_count"]
    sp = d["string_pool_offset"]

    for layout, stride in ((24, 24), (16, 16)):
        drivers = []
        ok = True
        for i in range(min(dc, 400)):
            e = dl + i * stride
            if e + stride > len(buf):
                ok = False
                break
            try:
                base, size, name_off = struct.unpack_from("<QQI", buf, e)
            except struct.error:
                ok = False
                break
            if base == 0 or base > 0xFFFF_FFFF_FFFF:
                ok = False
                break
            if name_off > d["string_pool_size"]:
                ok = False
                break
            s = sp + name_off
            if s + 2 > len(buf):
                ok = False
                break
            try:
                strlen = struct.unpack_from("<H", buf, s)[0]
                raw = buf[s + 2: s + 2 + strlen]
                name = raw.decode("utf-16-le", "replace")
            except Exception:
                ok = False
                break
            if not name or not name.isprintable():
                ok = False
                break
            drivers.append({"base": base, "size": size, "name": name})
        if ok and drivers:
            d["driver_layout_stride"] = stride
            break

    d["drivers"] = drivers

    # --- call stack as an array of 8-byte addresses
    stack: list[int] = []
    cs_off, cs_size = d["call_stack_offset"], d["size_of_call_stack"]
    if 0 < cs_off and cs_off + cs_size <= len(buf) and cs_size <= 0x40000:
        n = cs_size // 8
        for i in range(min(n, 4096)):
            (v,) = struct.unpack_from("<Q", buf, cs_off + i * 8)
            stack.append(v)
    d["call_stack"] = stack
    return d


def resolve(addr: int, drivers: list[dict]) -> str:
    for drv in drivers:
        if drv["base"] <= addr < drv["base"] + drv["size"]:
            off = addr - drv["base"]
            return f"{drv['name']}+{off:#x}"
    return ""


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    as_json = "--json" in sys.argv
    path = Path(args[0]) if args else DEFAULT
    if not path.exists():
        print(f"dump not found: {path}")
        sys.exit(1)

    buf = path.read_bytes()
    hdr = parse_header(buf)

    if as_json:
        tri = parse_triage(buf) if "error" not in hdr else {}
        print(json.dumps({"header": hdr, "triage": tri}, indent=2, ensure_ascii=False))
        return

    print("=" * 74)
    print(f"KERNEL MINIDUMP: {path.name}  ({len(buf):,} bytes)")
    print("=" * 74)

    if hdr.get("error"):
        print(f"ERROR: {hdr['error']}")
        sys.exit(2)

    print("\n--- [1] BUGCHECK (authoritative) ---")
    print(f"  Code : {hdr['bugcheck_code']:#010x}  = {hdr['bugcheck_name']}")
    for i, p in enumerate(hdr["bugcheck_params"], 1):
        print(f"  Param{i}: {p}")
    print(f"  (decoded param1 = {int(hdr['bugcheck_params'][0], 16)} = "
          f"{int(hdr['bugcheck_params'][0], 16):#x})")

    print("\n--- [2] DUMP METADATA ---")
    for k in ("signature", "valid_dump", "major", "minor", "machine_image_type",
              "number_processors", "dir_table_base", "ps_loaded_module_list",
              "version_user"):
        print(f"  {k}: {hdr[k]}")

    tri = parse_triage(buf)
    if tri.get("error"):
        print(f"\n[3] triage: {tri['error']}")
        return

    print("\n--- [3] TRIAGE ---")
    for k in ("service_pack_build", "size_of_dump", "driver_count",
              "driver_layout_stride", "size_of_call_stack"):
        print(f"  {k}: {tri.get(k)}")

    drivers = tri.get("drivers", [])
    print(f"\n--- [4] LOADED DRIVERS ({len(drivers)}) ---")
    # Show third-party / non-Microsoft-looking names first: those are the
    # interesting ones (vmx86, hcmon, sysdiag, DTS, ...).
    hot = [d for d in drivers
           if any(k in d["name"].lower() for k in
                  ("vmx", "hcmon", "vmnet", "sysdiag", "klim", "kav", "bootsafe",
                   "dts", "realtek", "rt", "nvlddmkm", "huorong", "wfp", "hr"))]
    if hot:
        print("  ** notable / third-party **")
        for d in hot:
            print(f"    {d['base']:#018x}  {d['size']:>10,}  {d['name']}")
    print("  (all names, sorted)")
    for d in sorted(drivers, key=lambda x: x["name"].lower()):
        print(f"    {d['base']:#018x}  {d['size']:>10,}  {d['name']}")

    stack = tri.get("call_stack", [])
    print(f"\n--- [5] KERNEL CALL STACK: {len(stack)} frames, RESOLVED ---")
    hist: dict[str, int] = {}
    for i, a in enumerate(stack):
        r = resolve(a, drivers)
        if r:
            mod = r.split("+")[0]
            hist[mod] = hist.get(mod, 0) + 1
            print(f"  #{i:03d}  {a:#018x}  {r}")
        else:
            print(f"  #{i:03d}  {a:#018x}")

    print("\n--- [6] STACK MODULE HISTOGRAM (prime suspects) ---")
    for mod, n in sorted(hist.items(), key=lambda x: -x[1]):
        print(f"  {n:>5} frames  {mod}")


if __name__ == "__main__":
    main()
