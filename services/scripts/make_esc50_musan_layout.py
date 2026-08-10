#!/usr/bin/env python3
"""把已 resample 的 ESC-50（非语音环境声，16k 单声道）拼成 MUSAN 兼容布局，
供 prep_kws_data.py 的 build_augmented_positives / build_musan_negatives 复用。

为什么需要这个垫片：
  - 项目内建的 `build_augmented_positives` 用 MUSAN 的 noise/music/speech 三类噪声
    按随机 SNR 混进正样本（缓解召回不足）。但本机 MUSAN 下不下来（openslr 10.3GB @15KB/s）。
  - ESC-50（github karolpiczak/ESC-50）是 2000 段全非语音环境声，正是 MUSAN `noise/`
    类的等价替代。本项目已把它 resample 到 16k 落 `data/kws/esc50_neg/`。
  - 本脚本把 esc50_neg 以 symlink 形式映射成 `noise/ + music/ + speech/` 三类布局，
    使 `resolve_musan_dir` 与 `_collect_musan_wavs` 无需改动即可消费 ESC-50。

布局约定（与 MUSAN 对齐）：
  <out>/noise/   ← 全部 ESC-50（主噪声源，非语音环境声）
  <out>/music/   ← 少量 ESC-50 抽样（仅满足“三类齐全”+非空，避免 rng.choice([]) 崩）
  <out>/speech/  ← 少量 ESC-50 抽样（同上；ESC-50 无真实语音，用人类非语音类近似）

幂等：已存在且指向正确源的 symlink 跳过；指向失效源的重建。
symlink 而非 copy：省 800MB+ 磁盘与复制时间；prep 在 WSL 下读 /mnt/d 符号链接正常。

用法：
  python make_esc50_musan_layout.py [--src DIR] [--out DIR] [--music-n N] [--speech-n N]
"""
from __future__ import annotations

import argparse
import os
import random
import sys
from pathlib import Path

DEFAULT_SRC = Path("/mnt/d/AI/data/kws/esc50_neg")
DEFAULT_OUT = Path("/mnt/d/AI/data/kws/esc50_musan")
MUSAN_SUBDIRS = ("noise", "music", "speech")


def _link_one(src: Path, dst: Path) -> str:
    """建/修一个 symlink，返回状态字符串。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    target = os.path.abspath(str(src))
    if dst.is_symlink():
        if os.path.abspath(os.readlink(dst)) == target:
            return "skip"  # 已正确
        dst.unlink()
    elif dst.exists():
        return "skip-existing-real"  # 真实文件不动
    os.symlink(target, str(dst))
    return "link"


def build(src: Path, out: Path, music_n: int, speech_n: int, seed: int) -> dict:
    if not src.is_dir():
        raise FileNotFoundError(f"esc50_neg 源目录不存在: {src}")
    wavs = sorted(src.rglob("*.wav"))
    if not wavs:
        raise FileNotFoundError(f"esc50_neg 下无 wav: {src}")
    rng = random.Random(seed)

    stats = {k: 0 for k in ("noise", "music", "speech")}

    # noise/ ← 全部 ESC-50（主噪声源）
    noise_dir = out / "noise"
    for w in wavs:
        r = _link_one(w, noise_dir / w.name)
        if r in ("link", "skip"):
            stats["noise"] += 1

    # music/ + speech/ ← 少量抽样（满足三类齐全 + 非空）
    sample = rng.sample(wavs, min(max(music_n, speech_n), len(wavs)))
    half = len(sample) // 2
    music_sample = sample[: max(music_n, half)]
    speech_sample = sample[half: half + speech_n]
    for w in music_sample:
        if _link_one(w, out / "music" / w.name) in ("link", "skip"):
            stats["music"] += 1
    for w in speech_sample:
        if _link_one(w, out / "speech" / w.name) in ("link", "skip"):
            stats["speech"] += 1

    # 校验三类子目录齐全且非空（prep 的 resolve_musan_dir 要求）
    missing = [s for s in MUSAN_SUBDIRS if not (out / s).is_dir()]
    empty = [s for s in MUSAN_SUBDIRS if not list((out / s).glob("*.wav"))]
    ok = (not missing) and (not empty)
    return {
        "src": str(src),
        "out": str(out),
        "total_src_wavs": len(wavs),
        "stats": stats,
        "missing_subdirs": missing,
        "empty_subdirs": empty,
        "valid": ok,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", type=Path, default=DEFAULT_SRC,
                    help=f"已 resample 的 ESC-50 目录 (默认 {DEFAULT_SRC})")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT,
                    help=f"MUSAN 兼容布局输出目录 (默认 {DEFAULT_OUT})")
    ap.add_argument("--music-n", type=int, default=20, help="music/ 抽样数 (默认 20)")
    ap.add_argument("--speech-n", type=int, default=20, help="speech/ 抽样数 (默认 20)")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    try:
        res = build(args.src, args.out, args.music_n, args.speech_n, args.seed)
    except FileNotFoundError as e:
        print(f"[FAIL] {e}", file=sys.stderr)
        return 1

    print(f"[layout] src   = {res['src']} ({res['total_src_wavs']} wavs)")
    print(f"[layout] out   = {res['out']}")
    print(f"[layout] stats = noise {res['stats']['noise']} / "
          f"music {res['stats']['music']} / speech {res['stats']['speech']}")
    if not res["valid"]:
        print(f"[FAIL] 布局无效: missing={res['missing_subdirs']} empty={res['empty_subdirs']}",
              file=sys.stderr)
        return 1
    print(f"[layout] OK — 可直接传给 prep_kws_data.py --musan-dir {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
