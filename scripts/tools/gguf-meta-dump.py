"""Temporary read-only GGUF metadata dump (mmproj image-token keys).

Standalone header parser: the installed `gguf` package fails on numpy 2
(`memmap.newbyteorder` removed), so parse the KV section directly.
Read-only; no model weights are loaded.
"""
from __future__ import annotations

import struct
import sys

GGUF_MAGIC = 0x46554747
# value type -> struct format / handling
SCALAR = {
    0: "B", 1: "b", 2: "H", 3: "h", 4: "I", 5: "i", 6: "f", 7: "?",
    10: "Q", 11: "q", 12: "d",
}
T_STRING = 8
T_ARRAY = 9


def read_string(fh) -> str:
    (n,) = struct.unpack("<Q", fh.read(8))
    return fh.read(n).decode("utf-8", "replace")


def read_value(fh, vtype):
    if vtype == T_STRING:
        return read_string(fh)
    if vtype == T_ARRAY:
        (etype,) = struct.unpack("<I", fh.read(4))
        (count,) = struct.unpack("<Q", fh.read(8))
        # Skip large arrays (weights/token lists) without materialising them.
        if count > 4096:
            for _ in range(count):
                skip_value(fh, etype)
            return f"<array len={count} skipped>"
        return [read_value(fh, etype) for _ in range(count)]
    fmt = SCALAR.get(vtype)
    if fmt is None:
        raise ValueError(f"unknown gguf value type {vtype}")
    size = struct.calcsize(fmt)
    return struct.unpack("<" + fmt, fh.read(size))[0]


def skip_value(fh, vtype):
    if vtype == T_STRING:
        (n,) = struct.unpack("<Q", fh.read(8))
        fh.seek(n, 1)
        return
    if vtype == T_ARRAY:
        (etype,) = struct.unpack("<I", fh.read(4))
        (count,) = struct.unpack("<Q", fh.read(8))
        for _ in range(count):
            skip_value(fh, etype)
        return
    fmt = SCALAR.get(vtype)
    if fmt is None:
        raise ValueError(f"unknown gguf value type {vtype}")
    fh.seek(struct.calcsize(fmt), 1)


def main(path: str) -> None:
    with open(path, "rb") as fh:
        magic, version, n_tensors, n_kv = struct.unpack("<IIQQ", fh.read(24))
        if magic != GGUF_MAGIC:
            raise SystemExit(f"not a GGUF file: magic={magic:#x}")
        print(f"gguf version={version} tensors={n_tensors} kv={n_kv}")
        hits = []
        for _ in range(n_kv):
            key = read_string(fh)
            (vtype,) = struct.unpack("<I", fh.read(4))
            low = key.lower()
            interesting = any(
                k in low
                for k in ("image", "patch", "pixel", "vision", "spatial",
                          "temporal", "merge", "token", "model_type",
                          "model_name", "arch")
            )
            try:
                if interesting:
                    value = read_value(fh, vtype)
                    hits.append((key, value))
                else:
                    skip_value(fh, vtype)
            except Exception as exc:  # noqa: BLE001 - diagnostic dump
                raise SystemExit(f"parse failed at key={key!r}: {exc}") from exc
        for key, value in hits:
            text = str(value)
            if len(text) > 160:
                text = text[:160] + "..."
            print(f"{key:58s} = {text}")


if __name__ == "__main__":
    main(sys.argv[1])
