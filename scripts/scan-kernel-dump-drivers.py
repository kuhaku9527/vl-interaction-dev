"""Locate the driver list inside a kernel triage dump by pattern-scanning.

The TRIAGE_DUMP64 field offsets vary across Windows builds, and a wrong
offset silently yields an empty list (which is what happened on the first
attempt). Instead of guessing, this script SCANS the dump for the driver-list
shape and validates candidates:

  A TRIAGE_DRIVER_ENTRY sequence is plausible when, for many consecutive
  entries, each {Base, Size, NameOffset} triple satisfies:
    * Base is a canonical kernel address  (0xffff8... - 0xfffff...)
    * Size is small and plausible         (< 16 MiB)
    * NameOffset points into the string pool, where a UTF-16 string decodes
      to something that looks like "<name>.sys"

Usage:
  python scripts/scan-kernel-dump-drivers.py [dump]
"""
from __future__ import annotations

import re
import struct
import sys
from pathlib import Path

DEFAULT = Path("D:/bsod-forensics/092026-16046-01.dmp")
HDR = 0x2000

KERNEL_LO = 0xFFFF_8000_0000_0000
KERNEL_HI = 0xFFFF_FFFF_FFFF_FFFF

SYS_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,60}\.sys$")


def read_kstring(buf: bytes, off: int) -> str | None:
    if off + 2 > len(buf):
        return None
    (n,) = struct.unpack_from("<H", buf, off)
    if n == 0 or n > 200 or n % 2:
        return None
    raw = buf[off + 2: off + 2 + n]
    if len(raw) < n:
        return None
    try:
        s = raw.decode("utf-16-le")
    except Exception:
        return None
    if not s or not s.isprintable():
        return None
    return s


def try_layout(buf: bytes, dl: int, stride: int, count: int) -> list[dict] | None:
    """Read `count` entries; return them if the shape validates."""
    out: list[dict] = []
    for i in range(count):
        e = dl + i * stride
        if e + stride > len(buf):
            return None
        try:
            base, size, name_off = struct.unpack_from("<QQI", buf, e)
        except struct.error:
            return None
        if not (KERNEL_LO <= base <= KERNEL_HI):
            return None
        if not (0x1000 <= size <= 0x100_0000):
            return None
        # NameOffset is relative to the string pool; try a generous window of
        # candidate pool bases later. Here just sanity-bound it.
        if name_off > 0x20000:
            return None
        out.append({"base": base, "size": size, "name_off": name_off})
    return out


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    buf = path.read_bytes()
    print(f"dump: {path.name}  ({len(buf):,} bytes)")

    # 1) Find candidate string pools: long runs of UTF-16 names ending in .sys
    print("\n=== scanning for driver-name string pool ===")
    pool_candidates: list[tuple[int, int]] = []
    for off in range(0, len(buf) - 4, 2):
        s = read_kstring(buf, off)
        if s and SYS_RE.match(s):
            pool_candidates.append((off, len(s)))

    print(f"  .sys strings found: {len(pool_candidates)}")
    if not pool_candidates:
        sys.exit(1)

    # cluster them; a real string pool is a dense run
    clusters: list[list[tuple[int, int]]] = []
    cur: list[tuple[int, int]] = []
    last = None
    for off, l in pool_candidates:
        if last is None or off - last <= 0x4000:
            cur.append((off, l))
        else:
            clusters.append(cur)
            cur = [(off, l)]
        last = off
    if cur:
        clusters.append(cur)

    clusters.sort(key=len, reverse=True)
    print(f"  clusters: {len(clusters)}  (largest: {len(clusters[0])} names)")
    pool_base = clusters[0][0][0]
    print(f"  -> string pool region starts near {pool_base:#x}")

    # 2) Scan for the driver-list header: a plausible {count, offset} pair
    print("\n=== scanning for driver list ===")
    best: tuple[int, list[dict], int] | None = None

    for dl in range(HDR, min(len(buf), HDR + 0x20000), 8):
        for stride in (24, 16):
            entries = try_layout(buf, dl, stride, 40)
            if not entries:
                continue
            # validate names resolve into the pool region
            good = 0
            for e in entries:
                for pool_lo in (pool_base,):
                    s = read_kstring(buf, pool_lo + e["name_off"])
                    if s and SYS_RE.match(s):
                        good += 1
                        break
            if good >= 30:
                if best is None or good > best[0]:
                    best = (good, entries, dl, stride)  # type: ignore[arg-type]
                break

    if not best:
        print("  no valid driver list found by scanning.")
        print("  (falling back: dump the string pool so names can still be read)")
        print("\n=== driver names found in pool (unordered) ===")
        seen = []
        for off, _ in clusters[0][:400]:
            s = read_kstring(buf, off)
            if s:
                seen.append(s)
        for s in sorted(set(seen)):
            print("   ", s)
        return

    good, entries, dl, stride = best
    print(f"  FOUND: driver_list_offset={dl:#x} stride={stride} "
          f"validated={good}/{len(entries)}")

    # 3) Read the full list
    pool_lo = pool_base
    drivers: list[dict] = []
    for i in range(600):
        e = dl + i * stride
        if e + stride > len(buf):
            break
        try:
            base, size, name_off = struct.unpack_from("<QQI", buf, e)
        except struct.error:
            break
        if not (KERNEL_LO <= base <= KERNEL_HI):
            break
        name = read_kstring(buf, pool_lo + name_off)
        if not name or not SYS_RE.match(name):
            break
        drivers.append({"base": base, "size": size, "name": name})

    print(f"\n=== DRIVERS ({len(drivers)}) ===")
    notable_keys = ("vmx", "hcmon", "vmnet", "sysdiag", "klim", "kav", "bootsafe",
                     "dam", "dts", "rt", "realtek", "nvlddmkm", "hr", "wfp")
    hot = [d for d in drivers if any(k in d["name"].lower() for k in notable_keys)]
    if hot:
        print("  ** NOTABLE **")
        for d in hot:
            print(f"    {d['base']:#018x}  {d['size']:>10,}  {d['name']}")
    print("\n  all drivers:")
    for d in sorted(drivers, key=lambda x: x["name"].lower()):
        print(f"    {d['base']:#018x}  {d['size']:>10,}  {d['name']}")


if __name__ == "__main__":
    main()
