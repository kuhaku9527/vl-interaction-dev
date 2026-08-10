#!/usr/bin/env python3
"""ingest_esc50.py — 把 ESC-50 环境声集转成 KWS 非语音负样本。

ESC-50 (github.com/karolpiczak/ESC-50) 是 2000 段 5s 环境声，50 类全是非语音
（动物 / 自然 / 室内 / 室外 / 人类非语音），正好替代不可下的 MUSAN noise/ 类，
给 KWS 提供解耦 FAR 所需的非语音负样本（MUSAN 在本机网络下 10.3GB@15KB/s 死路）。

约束（约法三章）：
- 不静默降级：源 wav 读不出 / 重采样失败明确 log 并跳过，不假装成功。
- 必有 log：每步打印计数、跳过原因、进度。
- 幂等：输出 wav 已存在且为 16k 单声道则跳过，可重复跑不重复写。
- 不删源：只写派生 wav 到 out-dir。

输出：out-dir/*.wav（16k 单声道 16-bit PCM）。build_negative_pool.py 用 --esc50-dir 扫它。
可选 --manifest：写一份 {id,audio,duration,category} jsonl 供分析（非管线必需）。

重采样后端：优先 torchaudio；缺失则 ffmpeg（均需 WSL kws-train venv）。
"""
import argparse
import csv
import json
import wave
from pathlib import Path


def log(m):
    print(m)


# ---- 重采样后端探测 ----
# 注意：torchaudio 2.11 的 torchaudio.load 改走 torchcodec 后端，本机未装会报错。
# 故优先 ffmpeg（WSL 自带），兜底用 soundfile 读 + torchaudio.functional.resample
# 做重采样（只用数学 kernel，不碰 load 后端），彻底避开 torchcodec 依赖。
import shutil
import subprocess

_FFMPEG = shutil.which("ffmpeg")
try:
    import soundfile as sf  # noqa: E402
    _HAVE_SF = True
except Exception:  # pragma: no cover
    sf = None
    _HAVE_SF = False
try:
    import torchaudio  # noqa: E402
    _HAVE_TA = True
except Exception:  # pragma: no cover
    torchaudio = None
    _HAVE_TA = False

if _FFMPEG:
    _BACKEND = "ffmpeg"
elif _HAVE_SF and _HAVE_TA:
    _BACKEND = "soundfile+torchaudio"
else:
    _BACKEND = "none"


def read_sr_ch(wav: Path):
    with wave.open(str(wav), "rb") as w:
        return w.getframerate(), w.getnchannels()


def resample_ffmpeg(src: Path, dst: Path, target_sr: int):
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(src), "-ac", "1", "-ar", str(target_sr),
         "-sample_fmt", "s16", str(dst)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def resample_sf_ta(src: Path, dst: Path, target_sr: int):
    import torch
    data, sr = sf.read(str(src), dtype="float32")   # (n,) 或 (n, ch)
    if data.ndim > 1:
        data = data.mean(axis=1)                     # 下混单声道
    wav = torch.from_numpy(data).unsqueeze(0)        # (1, n)
    if sr != target_sr:
        wav = torchaudio.functional.resample(wav, sr, target_sr)
    torchaudio.save(str(dst), wav, target_sr, bits_per_sample=16)


def resample(src: Path, dst: Path, target_sr: int):
    if _BACKEND == "ffmpeg":
        resample_ffmpeg(src, dst, target_sr)
    elif _BACKEND == "soundfile+torchaudio":
        resample_sf_ta(src, dst, target_sr)
    else:
        raise RuntimeError("无可用重采样后端（需 ffmpeg 或 soundfile+torchaudio）")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src",
        default="/mnt/d/AI/workspace/JoyAI-VL-Interaction-main/.cache/esc50_src/audio",
        help="ESC-50 解压后的 audio/ 目录（含 *.wav，44.1k 单声道）")
    ap.add_argument("--out-dir",
        default="/mnt/d/AI/data/kws/esc50_neg",
        help="16k 单声道 wav 输出目录")
    ap.add_argument("--target-sr", type=int, default=16000)
    ap.add_argument("--manifest",
        default="/mnt/d/AI/data/kws/esc50_neg.jsonl",
        help="可选分析用 manifest（含 category）")
    ap.add_argument("--meta-csv",
        default="/mnt/d/AI/workspace/JoyAI-VL-Interaction-main/.cache/esc50_src/meta/esc50.csv",
        help="ESC-50 标注 csv（filename->category）")
    ap.add_argument("--limit", type=int, default=0,
        help="仅处理前 N 条（0=全部）。验证重采样用 --limit 3")
    args = ap.parse_args()

    src = Path(args.src)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not src.exists():
        raise RuntimeError(f"ESC-50 源目录不存在: {src}（先 clone 或解压）")

    log("=== ingest ESC-50 → 非语音负样本 ===")
    log(f"  后端: {_BACKEND}  目标sr: {args.target_sr}")
    log(f"  源: {src}")
    log(f"  输出: {out_dir}")

    # 类别映射（文件名→category）供 manifest 标注（非管线必需）
    cat_map = {}
    mc = Path(args.meta_csv)
    if mc.exists():
        with mc.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                cat_map[row["filename"]] = row.get("category", "")
        log(f"  类别映射: {len(cat_map)} 条")
    else:
        log(f"  [warn] meta csv 缺失，manifest 不含 category: {mc}")

    wavs = sorted(src.glob("*.wav"))
    if args.limit > 0:
        wavs = wavs[: args.limit]
        log(f"  --limit {args.limit}：仅处理前 {len(wavs)} 条")
    else:
        log(f"  源 wav 数: {len(wavs)}")

    ok = 0
    skip = 0
    rows = []
    for i, w in enumerate(wavs, 1):
        dst = out_dir / w.name
        # 幂等：已存在且是 16k 单声道则跳过
        if dst.exists():
            try:
                sr, ch = read_sr_ch(dst)
                if sr == args.target_sr and ch == 1:
                    ok += 1
                    rows.append((dst, cat_map.get(w.name, "")))
                    continue
            except Exception:
                pass  # 损坏则重新生成
        try:
            resample(w, dst, args.target_sr)
            ok += 1
            rows.append((dst, cat_map.get(w.name, "")))
        except Exception as e:
            skip += 1
            log(f"  [skip] {w.name}: {e}")
        if i % 500 == 0:
            log(f"  进度 {i}/{len(wavs)}  ok={ok} skip={skip}")

    # 写 manifest（可选分析用；字段对齐 negative.jsonl 加 category）
    mpath = Path(args.manifest)
    mpath.parent.mkdir(parents=True, exist_ok=True)
    with mpath.open("w", encoding="utf-8") as f:
        for i, (p, cat) in enumerate(rows):
            try:
                sr, ch, n = read_sr_ch(p)
                dur = n / sr
            except Exception:
                dur = 0.0
            f.write(json.dumps({"id": f"esc50_{i:05d}", "audio": str(p),
                                "duration": dur, "text": "",
                                "keyword": "negative", "category": cat},
                               ensure_ascii=False) + "\n")

    log("")
    log("=== 完成 ===")
    log(f"  输出 16k 单声道 wav: {ok}（skip {skip}）")
    log(f"  输出目录: {out_dir}")
    log(f"  manifest: {mpath}（{len(rows)} 条）")
    if ok == 0:
        raise RuntimeError("未产出任何 wav，终止（不写空 manifest）")


if __name__ == "__main__":
    main()
