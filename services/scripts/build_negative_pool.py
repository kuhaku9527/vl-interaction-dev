#!/usr/bin/env python3
"""KWS v5 负样本池构建（WSL2 侧）：用本机已有真实语音造"非 BT"负样本，零下载。

背景
----
§10.2 验收 FAIL 的根因是训练负样本太少（183 正 / 32 负 → 模型过触发，FAR=100%）。
MUSAN 全量下载在本机网络下不可行（openslr ~50KB/s≈60h，VPN 关则无网）。
本机磁盘已有大量真实人声：旧 bt-zai-ma 实验（另一个唤醒词）的录音，
对 BT 模型就是完美的硬负样本（真实语音、但说的不是 BT）。本脚本聚合这些源，
产出训练负样本池 + 冻结验收集，避免依赖网络。

输入源（全部 16k 单声道，已实测）
- bt-zai-ma/positive  : 真实 "zai ma" 人声（硬负样本）
- bt-zai-ma/negative  : 真实 "非 zai ma" 语音/环境声
- <our-neg-dir>       : 本次录的 broadcast/negative（真实非 BT 环境声）

输出
- <out-negative-jsonl>      : 训练+验证用负样本池（prep 会再按 --test-ratio 切）
- <frozen-out>              : §10.2 冻结验收集（held-out，不参与训练，避免泄漏）

约束（约法三章）
- 不静默降级：源 wav 校验失败（损坏/非16k/非单声道/过短）明确 log 并跳过，不假装成功。
- 必有 log：每步打印计数。
- 不删旧数据：只写派生 manifest，源录音与既有 positive 不动。
"""
import argparse
import json
import gzip
import random
import statistics
import wave
from pathlib import Path


def read_wav_meta(path: Path):
    with wave.open(str(path), "rb") as w:
        return w.getframerate(), w.getnchannels(), w.getnframes()


def wav_duration(path: Path) -> float:
    sr, _, n = read_wav_meta(path)
    return n / sr if sr else 0.0


def chunk_long_wav(path: Path, chunk_sec: float, out_dir: Path, min_dur: float = 0.3):
    """把超过 chunk_sec 的 wav 切成若干段，写回 out_dir，返回新路径列表。

    末尾不足 min_dur 的余数段直接丢弃——过短碎片（<0.3s）会在训练 fbank 处
    触发 AssertionError，必须在这里拦掉，不能留给下游。
    """
    sr, ch, n = read_wav_meta(path)
    chunk_frames = int(chunk_sec * sr)
    if n <= chunk_frames:
        return [path]
    out_paths = []
    stem = path.stem
    min_frames = int(min_dur * sr)
    with wave.open(str(path), "rb") as w:
        idx = 0
        while True:
            frames = w.readframes(chunk_frames)
            if not frames:
                break
            if len(frames) < min_frames:
                break  # 末尾不足 min_dur 的余数段，丢弃
            dst = out_dir / f"{stem}_seg{idx}.wav"
            with wave.open(str(dst), "wb") as o:
                o.setnchannels(ch)
                o.setsampwidth(w.getsampwidth())
                o.setframerate(sr)
                o.writeframes(frames)
            out_paths.append(dst)
            idx += 1
    return out_paths


def collect(source_dir: Path, min_dur: float, max_dur: float, chunk_sec: float,
            tmp_dir: Path, log):
    """校验源目录下所有 wav，返回合格 wav 的绝对路径列表。"""
    if not source_dir.exists():
        log(f"  [skip] 源不存在: {source_dir}")
        return []
    results = []
    for wav in sorted(source_dir.glob("*.wav")):
        try:
            sr, ch, n = read_wav_meta(wav)
        except Exception as e:
            log(f"  [skip] 损坏/无法读: {wav.name} ({e})")
            continue
        if sr != 16000:
            log(f"  [skip] 采样率非16k: {wav.name} ({sr})")
            continue
        if ch != 1:
            log(f"  [skip] 非单声道: {wav.name} (ch={ch})")
            continue
        dur = n / sr
        if dur < min_dur:
            log(f"  [skip] 过短: {wav.name} ({dur:.2f}s)")
            continue
        if dur > max_dur:
            segs = chunk_long_wav(wav, chunk_sec, tmp_dir, min_dur=min_dur)
            log(f"  [chunk] {wav.name} ({dur:.1f}s) -> {len(segs)} 段")
            results.extend(segs)
        else:
            results.append(wav)
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bt-zai-ma-dir", default="/mnt/d/AI/data/kws/bt-zai-ma")
    ap.add_argument("--our-neg-dir",
                    default="/mnt/d/AI/data/kws/bt-en/broadcast/negative")
    ap.add_argument("--out-negative-jsonl",
                    default="/mnt/d/AI/data/kws/bt-en/negative.jsonl")
    ap.add_argument("--frozen-out",
                    default="/mnt/d/AI/data/kws/bt-en/test_manifests/negative_test.jsonl.gz")
    ap.add_argument("--frozen-count", type=int, default=50,
                    help="§10.2 冻结验收集负样本数（粒度=1/N，50→2%）")
    ap.add_argument("--min-dur", type=float, default=0.3)
    ap.add_argument("--max-dur", type=float, default=4.0)
    ap.add_argument("--chunk-sec", type=float, default=3.0)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--esc50-dir",
                    default="/mnt/d/AI/data/kws/esc50_neg",
                    help="ESC-50 非语音负样本目录（ingest_esc50.py 产出，16k 单声道）")
    ap.add_argument("--esc50-cap", type=int, default=800,
                    help="ESC-50 最多取多少段（防非语音过量冲淡正样本）")
    args = ap.parse_args()

    def log(msg):
        print(msg)

    tmp_dir = Path(args.out_negative_jsonl).parent / ".neg_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    log("=== 收集真实负样本源 ===")
    pool = []
    # 注意：bt-zai-ma/positive（"在吗" 真人语音）与 "BT" 声学过近、且量易压过正样本，
    # 已证明会把模型压成"几乎不触发"（recall 崩）。故只取 bt-zai-ma/negative（非 zai-ma
    # 语音/环境声）+ 本次录的广播负样本，作为较干净的硬负样本。
    pool += collect(Path(args.bt_zai_ma_dir) / "negative", args.min_dur,
                    args.max_dur, args.chunk_sec, tmp_dir, log)
    pool += collect(Path(args.our_neg_dir), args.min_dur,
                    args.max_dur, args.chunk_sec, tmp_dir, log)

    # ESC-50 非语音环境声（MUSAN 不可下时的等价替代，FAR 解耦关键缺失块）。
    # 全 50 类均非语音，正好当 KWS 非语音负样本。cap 防其过量冲淡正样本。
    esc50 = collect(Path(args.esc50_dir), args.min_dur, args.max_dur,
                    args.chunk_sec, tmp_dir, log)
    esc50_set = set()
    if esc50:
        if len(esc50) > args.esc50_cap:
            log(f"  [cap] ESC-50 {len(esc50)} -> {args.esc50_cap}（防非语音过量）")
            esc50 = esc50[:args.esc50_cap]
        pool += esc50
        esc50_set = {str(Path(p).resolve()) for p in esc50}

    if not pool:
        raise RuntimeError("未收集到任何负样本，终止（不写空 manifest）")

    # 去重 + 绝对路径
    pool = sorted({str(p.resolve()) for p in pool})
    random.seed(args.seed)
    random.shuffle(pool)

    frozen_n = min(args.frozen_count, len(pool) // 4)  # 冻结集不超过池 1/4
    frozen = pool[:frozen_n]
    training = pool[frozen_n:]

    # 写训练负样本池
    out_jsonl = Path(args.out_negative_jsonl)
    out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with out_jsonl.open("w", encoding="utf-8") as f:
        for i, p in enumerate(training):
            src = "esc50" if str(Path(p).resolve()) in esc50_set else "speech"
            f.write(json.dumps({"id": f"neg_{i:05d}", "audio": p,
                                 "duration": wav_duration(Path(p)),
                                 "text": "", "keyword": "negative",
                                 "source": src},
                               ensure_ascii=False) + "\n")

    # 写冻结验收集（带 device 字段，与 positive 冻结集格式一致；负样本无真实域，
    # 统一标 nvidia_broadcast，FAR_overall 不按域分组故不影响门禁）
    frozen_out = Path(args.frozen_out)
    frozen_out.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(frozen_out, "wt", encoding="utf-8") as f:
        for i, p in enumerate(frozen):
            src = "esc50" if str(Path(p).resolve()) in esc50_set else "speech"
            f.write(json.dumps({"id": f"fneg_{i:05d}", "audio": p,
                                 "duration": wav_duration(Path(p)), "text": "",
                                 "keyword": "negative",
                                 "device": "nvidia_broadcast",
                                 "source": src}, ensure_ascii=False) + "\n")

    durs = []
    for p in pool:
        try:
            sr, _, n = read_wav_meta(Path(p))
            durs.append(n / sr)
        except Exception:
            pass
    log("")
    log(f"=== 完成 ===")
    log(f"  收集去重后总数 : {len(pool)}")
    log(f"  训练负样本池   : {len(training)} -> {out_jsonl}")
    log(f"  §10.2 冻结负样本: {len(frozen)} (粒度 {1/len(frozen)*100:.1f}%) -> {frozen_out}")
    if durs:
        log(f"  时长 min={min(durs):.2f} med={statistics.median(durs):.2f} "
            f"max={max(durs):.2f}s")


if __name__ == "__main__":
    main()
