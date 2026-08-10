"""
KWS 评估：0/1 命中 + FP 率。

输入：
  /mnt/d/AI/models/sherpa-onnx/models/kws/bt-en/  (ONNX 模型)
  /mnt/d/AI/data/kws/bt-en/manifests/positive_test.jsonl.gz
  /mnt/d/AI/data/kws/bt-en/manifests/negative_test.jsonl.gz

输出：
  hit_rate（应接近 1.0）
  false_positive_rate（应接近 0.0）
  per-file 详情

用法（WSL2）：
  python test_kws.py --model-dir /mnt/d/AI/models/sherpa-onnx/models/kws/bt-en

依赖：sherpa-onnx（pip install sherpa-onnx 即可）
"""
from __future__ import annotations

import argparse
import gzip
import json
import logging
import sys
import wave
from pathlib import Path

import numpy as np

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("kws-test")


def get_args():
    p = argparse.ArgumentParser()
    p.add_argument("--model-dir", type=Path, required=True)
    p.add_argument("--manifests-dir", type=Path,
                   default=Path("/mnt/d/AI/data/kws/bt-en/manifests"))
    p.add_argument("--num-threads", type=int, default=1)
    # §10.2 验收门禁阈值
    p.add_argument("--bar-recall", type=float, default=0.9,
                   help="整体 recall 门禁（默认 0.9）")
    p.add_argument("--bar-far", type=float, default=0.02,
                   help="整体 FAR 门禁（默认 0.02）")
    return p.parse_args()


# device 字段 -> 打印短标签（与 spec §10.2 一致）
DEVICE_SHORT = {
    "nvidia_broadcast": "broadcast",
    "gameDAC_chat": "gameDAC",
}


def load_manifest_jsonl_gz(path: Path) -> list[dict]:
    entries = []
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entries.append(json.loads(line))
    return entries


def test_one_sherpa(kws, wav_path: Path) -> bool:
    """用 sherpa-onnx KeywordSpotter 测一个 wav，返回是否命中。"""
    with wave.open(str(wav_path), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getframerate() == 16000
        assert wf.getsampwidth() == 2
        samples = np.frombuffer(
            wf.readframes(wf.getnframes()), dtype=np.int16
        ).astype(np.float32) / 32768.0

    stream = kws.create_stream()
    chunk = 1600  # 100ms
    for i in range(0, len(samples), chunk):
        stream.accept_waveform(16000, samples[i: i + chunk])
        # 关键：调用 decode_stream 才能触发 KWS 判定
        if kws.is_ready(stream):
            kws.decode_stream(stream)
            result = kws.get_result(stream)
            if result:
                return True
    # 末尾也得跑一遍（音频播完后）
    if kws.is_ready(stream):
        kws.decode_stream(stream)
        result = kws.get_result(stream)
        if result:
            return True
    return False


def compute_metrics(pos_entries: list[dict], neg_hits: list[bool],
                    bar_recall: float, bar_far: float) -> dict:
    """按声学域(device)分组算 recall, 并对整体 recall/FAR 做 §10.2 门禁判定。

    纯逻辑, 不依赖 sherpa / numpy / 音频, 可隔离单测。

    Args:
        pos_entries: 正样本条目列表, 每条需含 ``device``(str) 与 ``hit``(bool)。
        neg_hits:    负样本命中布尔列表, 顺序对应 neg manifest（不按域）。
        bar_recall:  整体 recall 通过阈值。
        bar_far:     整体 FAR 通过阈值。

    Returns:
        dict, 含 recall_by_device / recall_overall / far_overall / passed / reasons
        以及若干辅助计数。
    """
    # 按 device 分组聚合正样本命中
    device_hits: dict[str, int] = {}
    device_total: dict[str, int] = {}
    for e in pos_entries:
        dev = e["device"]
        device_total[dev] = device_total.get(dev, 0) + 1
        device_hits[dev] = device_hits.get(dev, 0) + (1 if e["hit"] else 0)

    recall_by_device: dict[str, float] = {}
    no_sample_devices: list[str] = []
    for dev in sorted(device_total.keys()):
        total = device_total[dev]
        if total == 0:
            recall_by_device[dev] = 0.0
            no_sample_devices.append(dev)
        else:
            recall_by_device[dev] = device_hits[dev] / total

    pos_total = len(pos_entries)
    pos_hits = sum(1 for e in pos_entries if e["hit"])
    recall_overall = (pos_hits / pos_total) if pos_total > 0 else 0.0

    neg_total = len(neg_hits)
    neg_hits_count = sum(1 for h in neg_hits if h)
    far_overall = (neg_hits_count / neg_total) if neg_total > 0 else 0.0

    reasons: list[str] = []
    if recall_overall < bar_recall:
        reasons.append(
            f"recall_overall={recall_overall:.4f} < bar_recall={bar_recall:.4f}"
        )
    if far_overall > bar_far:
        reasons.append(
            f"far_overall={far_overall:.4f} > bar_far={bar_far:.4f}"
        )
    passed = (recall_overall >= bar_recall) and (far_overall <= bar_far)

    return {
        "recall_by_device": recall_by_device,
        "recall_overall": recall_overall,
        "far_overall": far_overall,
        "passed": passed,
        "reasons": reasons,
        "pos_total": pos_total,
        "pos_hits": pos_hits,
        "neg_total": neg_total,
        "neg_hits": neg_hits_count,
        "no_sample_devices": no_sample_devices,
    }


def main():
    args = get_args()
    if not args.model_dir.exists():
        logger.error(f"模型目录不存在: {args.model_dir}")
        sys.exit(1)
    if not (args.model_dir / "encoder.onnx").exists():
        logger.error(f"缺 encoder.onnx: {args.model_dir}")
        sys.exit(1)

    # 加载 sherpa-onnx
    try:
        import sherpa_onnx
    except ImportError:
        logger.error("缺 sherpa-onnx（在 WSL2 跑：~/kws-train/bin/pip install sherpa-onnx）")
        sys.exit(1)
    logger.info(f"sherpa-onnx: {sherpa_onnx.__version__}")

    # chunk-8 优先（小延迟）
    encoder = next(args.model_dir.glob("encoder*chunk-8*.onnx"),
                   args.model_dir / "encoder.onnx")
    decoder = next(args.model_dir.glob("decoder*chunk-8*.onnx"),
                   args.model_dir / "decoder.onnx")
    joiner = next(args.model_dir.glob("joiner*chunk-8*.onnx"),
                  args.model_dir / "joiner.onnx")
    logger.info(f"  encoder: {encoder.name}")
    logger.info(f"  decoder: {decoder.name}")
    logger.info(f"  joiner: {joiner.name}")

    kws = sherpa_onnx.KeywordSpotter(
        tokens=str(args.model_dir / "tokens.txt"),
        encoder=str(encoder),
        decoder=str(decoder),
        joiner=str(joiner),
        keywords_file=str(args.model_dir / "keywords.txt"),
        num_threads=args.num_threads,
        sample_rate=16000,
    )

    # 加载测试集（manifest 缺失 / 正样本为空必须显式报错，禁止静默 fallback）
    pos_path = args.manifests_dir / "positive_test.jsonl.gz"
    neg_path = args.manifests_dir / "negative_test.jsonl.gz"
    if not pos_path.exists():
        logger.error(f"正样本 manifest 缺失: {pos_path}")
        sys.exit(1)
    if not neg_path.exists():
        logger.error(f"负样本 manifest 缺失: {neg_path}")
        sys.exit(1)

    pos = load_manifest_jsonl_gz(pos_path)
    neg = load_manifest_jsonl_gz(neg_path)

    if len(pos) == 0:
        logger.error("正样本为 0 条，无法评估 recall，终止")
        sys.exit(1)
    logger.info(f"Test set: positive={len(pos)}, negative={len(neg)}")

    # 评估：逐条跑 sherpa，并带 device/id/duration 元数据
    pos_entries = [
        {
            "device": e["device"],
            "hit": test_one_sherpa(kws, Path(e["audio"])),
            "id": e["id"],
            "duration": e["duration"],
        }
        for e in pos
    ]
    neg_hits = [test_one_sherpa(kws, Path(e["audio"])) for e in neg]

    # §10.2 门禁判定（按域 recall + 整体 recall/FAR）
    m = compute_metrics(pos_entries, neg_hits, args.bar_recall, args.bar_far)

    # per-file 详情（门禁之前打印，便于排查）
    print()
    print("=" * 60)
    print("  正样本详情（id / 命中 / 时长）:")
    for e in pos_entries:
        mark = "✓" if e["hit"] else "✗"
        print(f"    {mark} {e['id']}  {e['duration']:.2f}s")
    print()
    print("  负样本详情（前 20 个）:")
    for i, hit in enumerate(neg_hits[:20]):
        nid = neg[i].get("id", f"#{i}")
        dur = neg[i].get("duration", 0.0)
        mark = "✓误报" if hit else "✗正确"
        print(f"    {mark}  {nid}  {dur:.2f}s")
    if len(neg_hits) > 20:
        print(f"    ... 剩 {len(neg_hits) - 20} 个省略")

    # 报告 + 门禁
    print()
    print("=" * 60)
    print("  KWS §10.2 验收门禁")
    print("=" * 60)
    print(f"  recall_broadcast = {m['recall_by_device'].get('nvidia_broadcast', 0.0)*100:.1f}%")
    print(f"  recall_gameDAC   = {m['recall_by_device'].get('gameDAC_chat', 0.0)*100:.1f}%")
    print(f"  recall_overall   = {m['recall_overall']*100:.1f}%")
    print(f"  FAR_overall      = {m['far_overall']*100:.1f}%")
    print()
    if m["passed"]:
        print("  PASS  ✓  §10.2 门禁达标")
        print("=" * 60)
    else:
        print("  FAIL  ✗  §10.2 门禁未达标")
        for r in m["reasons"]:
            print(f"    - {r}")
        print("=" * 60)
        sys.exit(1)


if __name__ == "__main__":
    main()
