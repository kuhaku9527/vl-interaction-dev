"""
KWS 数据准备（WSL2 侧）：把 Windows 录好的 positive/negative 转成 lhotse cuts + train/test split。

唤醒词：**BT**（2 字符，纯英文字母，简化训练）
- 词表：~30 token（B/T/A/I + 必要静音 + 兜底 <unk>）
- keywords.txt: `B T @bt`

输入：
  /mnt/d/AI/data/kws/bt-en/positive/   （N wav，positive.jsonl）
  /mnt/d/AI/data/kws/bt-en/negative/   （M wav，negative.jsonl）

MUSAN 接入（fail-open）：
  若检测到 MUSAN（noise/music/speech 三类齐全），则：
    (a) 用 MUSAN 噪声/音乐/人声按随机 SNR 混进正样本 → 增广正样本（默认每样本 3 段），
        把 53 段正样本扩到 200+，缓解召回不足的根因（正样本过少）；
    (b) 把 MUSAN 切 2s 短片段当负样本（默认 400 段），压低 FAR。
  若 MUSAN 缺失 → log WARN 并跳过，管线照常用录制数据训练（不阻断）。

输出：
  /mnt/d/AI/data/kws/bt-en/manifests/
    ├── positive_train.jsonl.gz
    ├── positive_test.jsonl.gz
    ├── negative_train.jsonl.gz
    ├── negative_test.jsonl.gz
    ├── tokens.txt         # 简化词表
    └── keywords.txt       # "B T @bt"

用法（WSL2）：
  /home/ku/kws-train/bin/python /mnt/d/AI/workspace/JoyAI-VL-Interaction-main/services/scripts/prep_kws_data.py \\
      --data-root /mnt/d/AI/data/kws/bt-en \\
      --test-ratio 0.2
  # MUSAN 自动探测 .cache/musan；也可用 --musan-dir 显式指定；--no-musan 强制跳过。
"""

from __future__ import annotations

import argparse
import gzip
import json
import logging
import random
import sys
from pathlib import Path

import numpy as np

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("kws-prep")

# BT 唤醒词词表（最小化：只保留训练需要的 token）
PINYIN_VOCAB = [
    "<blk>",
    "<sos/eos>",
    "<unk>",
    # 唤醒词核心 token
    "B",
    "T",
    # 兜底噪声 / 静音
    "_",
]


def write_tokens(out_path: Path) -> None:
    with out_path.open("w", encoding="utf-8") as f:
        for idx, tok in enumerate(PINYIN_VOCAB):
            f.write(f"{tok} {idx}\n")
    print(f"  [tokens] {len(PINYIN_VOCAB)} tokens → {out_path}")


def write_keywords(out_path: Path) -> None:
    # sherpa-onnx keywords 格式: <phonemes> @<alias>
    out_path.write_text("B T @bt\n", encoding="utf-8")
    print(f"  [keywords] → {out_path}: B T @bt")


def load_manifest(jsonl_path: Path) -> list[dict]:
    entries = []
    with jsonl_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entries.append(json.loads(line))
    return entries


def validate_entries(entries: list[dict], label: str) -> None:
    """正样本 manifest 引用的 wav 必须存在；缺失即显式报错（数据未就绪）。

    不静默跳过：缺 wav 会导致下游 Recording.from_file 在训练时崩溃，
    这里提前 fail-fast 并给出修复指引。
    """
    missing = [e["audio"] for e in entries if not Path(e["audio"]).exists()]
    if missing:
        raise FileNotFoundError(
            f"[{label}] manifest 引用了 {len(missing)} 个不存在的 wav，例如:\n"
            f"    {missing[0]}\n"
            f"  请重新运行 record_kws_corpus.py --label {label} 重建 manifest，"
            f"或删除过期条目后再 prep。"
        )


def split_train_test(
    entries: list[dict], test_ratio: float, seed: int = 42
) -> tuple[list[dict], list[dict]]:
    rng = random.Random(seed)
    indices = list(range(len(entries)))
    rng.shuffle(indices)
    n_test = max(1, int(len(entries) * test_ratio))
    test_idx = set(indices[:n_test])
    train = [e for i, e in enumerate(entries) if i not in test_idx]
    test = [e for i, e in enumerate(entries) if i in test_idx]
    return train, test


def write_jsonl_gz(entries: list[dict], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(out_path, "wt", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    print(f"  [manifest] {len(entries):3d} → {out_path}")


# ============== MUSAN 接入（fail-open） ==============

MUSAN_SUBDIRS = ("noise", "music", "speech")


def resolve_musan_dir(args: argparse.Namespace, data_root: Path) -> Path | None:
    """探测 MUSAN 目录；找不到则 fail-open 返回 None。

    探测顺序：
      1. --musan-dir（若显式给出）
      2. <repo>/.cache/musan          （本仓库约定落点，gitignored）
      3. <repo>/services/kws-training/data/musan
      4. <data-root>/musan
    要求 noise/ + music/ + speech/ 三类子目录齐全才算命中。
    """
    if getattr(args, "no_musan", False):
        logger.info("[musan] --no-musan 指定，跳过 MUSAN 增广")
        return None
    candidates: list[Path] = []
    if args.musan_dir is not None:
        candidates.append(Path(args.musan_dir))
    repo = Path(__file__).resolve().parents[2]
    candidates += [
        repo / ".cache" / "musan",
        repo / "services" / "kws-training" / "data" / "musan",
        data_root / "musan",
    ]
    for cand in candidates:
        if cand.is_dir() and all((cand / sub).is_dir() for sub in MUSAN_SUBDIRS):
            logger.info(f"[musan] 命中: {cand}")
            return cand
    logger.warning(
        "[musan] 未找到 MUSAN（需 noise/ + music/ + speech/ 三类齐全），"
        "fail-open 跳过；仅用录制数据训练。可用 --musan-dir 指定或先下载。"
    )
    return None


def _collect_musan_wavs(musan_dir: Path) -> dict[str, list[Path]]:
    cats: dict[str, list[Path]] = {}
    for cat in MUSAN_SUBDIRS:
        d = musan_dir / cat
        if not d.is_dir():
            continue
        # speech 有多层子目录；noise/music 直接在一级
        wavs = sorted(d.rglob("*.wav")) if cat == "speech" else sorted(d.glob("*.wav"))
        if wavs:
            cats[cat] = wavs
    if not cats:
        raise FileNotFoundError(f"MUSAN 目录无 wav: {musan_dir}/{{noise,music,speech}}")
    return cats


def _mix_at_snr(clean: np.ndarray, noise: np.ndarray, snr_db: float) -> np.ndarray:
    """把 noise 按目标 SNR(dB) 混到 clean 上，返回 int16 PCM（峰值裁剪保护）。"""
    clean = clean.astype(np.float32)
    noise = noise.astype(np.float32)
    n = min(len(clean), len(noise))
    if n == 0:
        return clean.astype(np.int16)
    clean = clean[:n]
    noise = noise[:n]
    clean_power = float(np.mean(clean**2)) + 1e-12
    noise_power = float(np.mean(noise**2)) + 1e-12
    snr = 10.0 ** (snr_db / 10.0)
    noise_scaled = noise * (clean_power / (noise_power * snr)) ** 0.5
    mixed = clean + noise_scaled
    peak = float(np.max(np.abs(mixed))) + 1e-9
    if peak > 1.0:
        mixed = mixed / peak
    return (mixed * 32767.0).astype(np.int16)


def build_musan_negatives(
    musan_dir: Path,
    out_dir: Path,
    count: int,
    seg_sec: float,
    seed: int,
) -> list[dict]:
    """把 MUSAN noise/music/speech 切成固定长度短片段当负样本（非 BT 语音/噪声）。

    落点：<data-root>/musan_neg/（在训练数据根内，不外溢工作区）。
    """
    import soundfile as sf

    cats = _collect_musan_wavs(musan_dir)
    flat = [(c, w) for c, ws in cats.items() for w in ws]
    rng = random.Random(seed)
    rng.shuffle(flat)
    out_dir.mkdir(parents=True, exist_ok=True)
    entries: list[dict] = []
    made = 0
    for _cat, wav in flat:
        if made >= count:
            break
        data, sr = sf.read(str(wav), dtype="int16", always_2d=False)
        if data.ndim > 1:
            data = data[:, 0]
        if sr != 16000:
            logger.warning(f"  [musan] {wav.name} sr={sr}!=16000，跳过")
            continue
        seg_len = int(seg_sec * sr)
        if len(data) < seg_len:
            segs = [data]
        else:
            segs = [data[i : i + seg_len] for i in range(0, len(data) - seg_len + 1, seg_len)]
        for seg in segs:
            if made >= count:
                break
            made += 1
            name = f"musan_neg_{made:05d}.wav"
            sf.write(str(out_dir / name), seg, sr, subtype="PCM_16")
            dur = len(seg) / sr
            entries.append(
                {
                    "id": f"musan_neg_{made:05d}",
                    "audio": str((out_dir / name).resolve()),
                    "duration": dur,
                    "sampling_rate": sr,
                    "channels": 1,
                    "text": "",
                    "tokens": "",
                    "keyword": "negative",
                }
            )
    logger.info(f"  [musan] 生成 MUSAN 负样本 {len(entries)} 段（目标 {count}）")
    return entries


def build_augmented_positives(
    pos_entries: list[dict],
    musan_dir: Path,
    out_dir: Path,
    aug_per_pos: int,
    snr_min: float,
    snr_max: float,
    seg_sec: float,
    seed: int,
) -> list[dict]:
    """用 MUSAN 噪声/音乐/人声按随机 SNR 混进正样本，扩正样本数量（缓解召回不足根因）。

    落点：<data-root>/positive_aug/。
    """
    import soundfile as sf

    cats = _collect_musan_wavs(musan_dir)
    flat = [(c, w) for c, ws in cats.items() for w in ws]
    rng = random.Random(seed)
    out_dir.mkdir(parents=True, exist_ok=True)
    entries: list[dict] = []
    for e in pos_entries:
        clean_path = Path(e["audio"])
        if not clean_path.exists():
            continue
        clean, sr = sf.read(str(clean_path), dtype="int16", always_2d=False)
        if clean.ndim > 1:
            clean = clean[:, 0]
        if sr != 16000:
            logger.warning(f"  [musan] 正样本 {clean_path.name} sr={sr}!=16000，跳过增广")
            continue
        stem = Path(e["id"]).stem
        for k in range(aug_per_pos):
            _cat, nwav = rng.choice(flat)
            ndata, nsr = sf.read(str(nwav), dtype="int16", always_2d=False)
            if ndata.ndim > 1:
                ndata = ndata[:, 0]
            if nsr != 16000:
                continue
            seg_len = int(seg_sec * nsr)
            if len(ndata) >= seg_len:
                start = rng.randint(0, len(ndata) - seg_len)
                noise = ndata[start : start + seg_len]
            else:
                noise = ndata
            snr = rng.uniform(snr_min, snr_max)
            mixed = _mix_at_snr(clean, noise, snr)
            name = f"pos_aug_{stem}_{k + 1:02d}.wav"
            sf.write(str(out_dir / name), mixed, sr, subtype="PCM_16")
            dur = len(mixed) / sr
            entries.append(
                {
                    "id": f"pos_aug_{stem}_{k + 1:02d}",
                    "audio": str((out_dir / name).resolve()),
                    "duration": dur,
                    "sampling_rate": sr,
                    "channels": 1,
                    "text": "BT",
                    "tokens": "B T",
                    "keyword": "bt",
                }
            )
    logger.info(
        f"  [musan] 生成增广正样本 {len(entries)} 段"
        f"（原 {len(pos_entries)} × {aug_per_pos}，命中 {len(pos_entries)} 中可增广数）"
    )
    return entries


def main() -> int:
    parser = argparse.ArgumentParser(
        description="KWS 数据准备：lhotse manifest + tokens + keywords（含 MUSAN fail-open）"
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path("/mnt/d/AI/data/kws/bt-en"),
        help="数据根（Windows: D:/AI/data/kws/bt-en）",
    )
    parser.add_argument(
        "--test-ratio",
        type=float,
        default=0.2,
        help="测试集比例（默认 0.2）",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="随机种子",
    )
    # ---- MUSAN 接入（fail-open） ----
    parser.add_argument(
        "--musan-dir",
        type=Path,
        default=None,
        help="MUSAN 目录（含 noise/music/speech）。不传则自动探测 .cache/musan 等。",
    )
    parser.add_argument(
        "--no-musan",
        action="store_true",
        help="强制跳过 MUSAN（即使存在也不增广）。",
    )
    parser.add_argument(
        "--musan-neg-count",
        type=int,
        default=400,
        help="MUSAN 负样本段数（默认 400）。",
    )
    parser.add_argument(
        "--aug-per-pos",
        type=int,
        default=3,
        help="每个正样本用 MUSAN 混出的增广段数（默认 3 → 53 段扩到 ~212）。",
    )
    parser.add_argument("--aug-snr-min", type=float, default=0.0, help="增广 SNR 下限(dB)")
    parser.add_argument("--aug-snr-max", type=float, default=20.0, help="增广 SNR 上限(dB)")
    parser.add_argument("--aug-seg-sec", type=float, default=2.0, help="MUSAN 片段长度(s)")
    args = parser.parse_args()

    data_root: Path = args.data_root
    if not data_root.exists():
        print(f"ERROR: 数据根不存在: {data_root}", file=sys.stderr)
        return 1

    pos_jsonl = data_root / "positive.jsonl"
    neg_jsonl = data_root / "negative.jsonl"
    if not pos_jsonl.exists() or not neg_jsonl.exists():
        print(f"ERROR: 缺 manifest（{pos_jsonl} 或 {neg_jsonl}）", file=sys.stderr)
        print("  先在 Windows 侧跑 record_kws_corpus.py", file=sys.stderr)
        return 1

    pos = load_manifest(pos_jsonl)
    neg = load_manifest(neg_jsonl)
    # 数据就绪校验：manifest 引用的 wav 必须存在，否则 fail-fast
    validate_entries(pos, "positive")
    validate_entries(neg, "negative")
    print(f"  [load] positive={len(pos)}, negative={len(neg)}")

    # ---- MUSAN（fail-open） ----
    musan_dir = resolve_musan_dir(args, data_root)
    if musan_dir is not None:
        try:
            pos_aug = build_augmented_positives(
                pos,
                musan_dir,
                data_root / "positive_aug",
                args.aug_per_pos,
                args.aug_snr_min,
                args.aug_snr_max,
                args.aug_seg_sec,
                args.seed,
            )
            neg_musan = build_musan_negatives(
                musan_dir,
                data_root / "musan_neg",
                args.musan_neg_count,
                args.aug_seg_sec,
                args.seed,
            )
            pos = pos + pos_aug
            neg = neg + neg_musan
            print(
                f"  [musan] 正样本 {len(pos)}（含增广 {len(pos_aug)}）"
                f" / 负样本 {len(neg)}（含 MUSAN {len(neg_musan)}）"
            )
        except FileNotFoundError as e:
            # MUSAN 判定存在但内容不完整：显式报错，不静默回退
            logger.error(f"[musan] MUSAN 内容不完整，终止: {e}")
            return 1

    if len(pos) < 10 or len(neg) < 30:
        print(f"WARN: 数据偏少（pos={len(pos)}, neg={len(neg)}），建议正样本≥200, 负样本≥600")

    pos_train, pos_test = split_train_test(pos, args.test_ratio, args.seed)
    neg_train, neg_test = split_train_test(neg, args.test_ratio, args.seed)
    print(f"  [split] pos train={len(pos_train)} test={len(pos_test)}")
    print(f"  [split] neg train={len(neg_train)} test={len(neg_test)}")

    manifests_dir = data_root / "manifests"
    manifests_dir.mkdir(parents=True, exist_ok=True)

    write_jsonl_gz(pos_train, manifests_dir / "positive_train.jsonl.gz")
    write_jsonl_gz(pos_test, manifests_dir / "positive_test.jsonl.gz")
    write_jsonl_gz(neg_train, manifests_dir / "negative_train.jsonl.gz")
    write_jsonl_gz(neg_test, manifests_dir / "negative_test.jsonl.gz")

    write_tokens(manifests_dir / "tokens.txt")
    write_keywords(manifests_dir / "keywords.txt")

    print()
    print("  [done] 下一步：跑 train_kws.py")
    print(
        "    /home/ku/kws-train/bin/python /mnt/d/AI/workspace/JoyAI-VL-Interaction-main/services/kws-training/train_kws.py \\"
    )
    print(f"        --manifests-dir {manifests_dir} \\")
    print(f"        --exp-dir {data_root / 'exp'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
