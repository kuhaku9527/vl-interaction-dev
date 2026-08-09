"""
Build the frozen KWS regression test manifest (KWS_V5_CAPTURE_SPEC.md, section 10.1).

This script scans the frozen ``test/`` directory for ``.wav`` files, infers a
``device`` label from the immediate sub-directory name (e.g. ``nvidia_broadcast``
or ``gameDAC_chat``), and writes two gzipped JSONL manifests,
``positive_test.jsonl.gz`` and ``negative_test.jsonl.gz``, into the manifests
directory. The ``device`` field lets ``test_kws.py`` (section 10.2) report
per-domain recall / FAR so the deployment microphone can be chosen by measured
accuracy rather than guesswork.

Design notes:
- Stdlib only. WAV metadata is read with the ``wave`` module, matching the
  reader already used by ``test_kws.py``, so the manifest can be rebuilt on any
  machine without installing sherpa-onnx or soundfile.
- The frozen holdout set is never silently destroyed: if a target manifest
  already exists and the new scan yields zero entries for that label, the script
  refuses to overwrite it (unless ``--force`` is given), printing guidance
  instead.

Usage (WSL2, per the spec)::

    python build_test_manifest.py \
        --test-root /mnt/d/AI/data/kws/bt-en/test \
        --out /mnt/d/AI/data/kws/bt-en/manifests
"""

from __future__ import annotations

import argparse
import gzip
import json
import logging
import sys
import wave
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("kws-build-test-manifest")

# Canonical device tokens used by section 10.2 per-domain grouping.
DEVICE_NVIDIA_BROADCAST = "nvidia_broadcast"
DEVICE_GAMEDAC_CHAT = "gameDAC_chat"
DEVICE_UNKNOWN = "unknown"

# Accepted sub-directory names mapped to the canonical device token. Unknown
# sub-directory names fall through to the raw name (with a warning) rather than
# being silently dropped.
DEVICE_ALIASES: dict[str, str] = {
    DEVICE_NVIDIA_BROADCAST: DEVICE_NVIDIA_BROADCAST,
    "nvidia-broadcast": DEVICE_NVIDIA_BROADCAST,
    "broadcast": DEVICE_NVIDIA_BROADCAST,
    "nvbroadcast": DEVICE_NVIDIA_BROADCAST,
    DEVICE_GAMEDAC_CHAT: DEVICE_GAMEDAC_CHAT,
    "gamedac": DEVICE_GAMEDAC_CHAT,
    "gamedacchat": DEVICE_GAMEDAC_CHAT,
    "gamedac-chat": DEVICE_GAMEDAC_CHAT,
    "gameDAC": DEVICE_GAMEDAC_CHAT,
}

DEFAULT_TEST_ROOT = Path("D:/AI/data/kws/bt-en/test")
DEFAULT_OUT_DIR = Path("D:/AI/data/kws/bt-en/manifests")

POSITIVE_LABEL = "positive"
NEGATIVE_LABEL = "negative"

POSITIVE_TEST_MANIFEST = "positive_test.jsonl.gz"
NEGATIVE_TEST_MANIFEST = "negative_test.jsonl.gz"


def get_args() -> argparse.Namespace:
    """Parse command-line arguments for the manifest builder.

    Returns
    -------
    argparse.Namespace
        Parsed arguments with ``test_root``, ``out``, ``dry_run`` and ``force``.
    """
    parser = argparse.ArgumentParser(
        description="Build the frozen KWS regression test manifest (section 10.1).",
    )
    parser.add_argument(
        "--test-root",
        type=Path,
        default=DEFAULT_TEST_ROOT,
        help="Frozen test directory (test/positive/{device}/ + test/negative/).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help="Output directory for the gzipped JSONL manifests.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be written without touching any files.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing manifest even if the scan yields zero entries.",
    )
    return parser.parse_args()


def read_wav_meta(wav_path: Path) -> tuple[int, int, float]:
    """Read sampling rate, channel count and duration from a WAV file.

    Parameters
    ----------
    wav_path : Path
        Path to the ``.wav`` file to inspect.

    Returns
    -------
    tuple[int, int, float]
        A ``(sampling_rate, channels, duration_seconds)`` triple.

    Raises
    ------
    wave.Error
        If the file is not a readable WAV container.
    EOFError
        If the WAV header is truncated.
    """
    with wave.open(str(wav_path), "rb") as wf:
        sampling_rate = wf.getframerate()
        channels = wf.getnchannels()
        n_frames = wf.getnframes()
    duration = n_frames / sampling_rate if sampling_rate else 0.0
    return sampling_rate, channels, duration


def infer_device(rel_parts: tuple[str, ...]) -> str:
    """Map the first path component under a label directory to a device token.

    Parameters
    ----------
    rel_parts : tuple[str, ...]
        Path parts of the wav relative to its label directory (e.g.
        ``("nvidia_broadcast", "bt_001.wav")``).

    Returns
    -------
    str
        The canonical device token, or ``DEVICE_UNKNOWN`` when the wav sits
        directly under the label directory.
    """
    if not rel_parts:
        return DEVICE_UNKNOWN
    subdir = rel_parts[0]
    if subdir in DEVICE_ALIASES:
        return DEVICE_ALIASES[subdir]
    if len(rel_parts) > 1:
        logger.warning("未识别的设备子目录 %r, 原样用作 device 标签", subdir)
    return subdir


def build_entries(test_root: Path, label: str) -> list[dict]:
    """Scan a label directory and build manifest entries tagged with ``device``.

    Positive wavs nested under a device sub-directory (e.g.
    ``positive/nvidia_broadcast/bt_001.wav``) inherit that device; negatives are
    always tagged ``unknown`` because section 10.2 uses them only for the overall
    false-positive rate. A single corrupt wav is logged and skipped rather than
    aborting the whole scan.

    Parameters
    ----------
    test_root : Path
        Root of the frozen test directory.
    label : str
        Either ``"positive"`` or ``"negative"``.

    Returns
    -------
    list[dict]
        Manifest entries, sorted by absolute audio path for deterministic output.
    """
    base = test_root / label
    if not base.exists():
        logger.warning("测试子目录不存在, 跳过: %s", base)
        return []
    entries: list[dict] = []
    for wav in sorted(base.rglob("*.wav")):
        rel_parts = wav.relative_to(base).parts
        device = DEVICE_UNKNOWN
        if label == POSITIVE_LABEL and len(rel_parts) > 1:
            device = infer_device(rel_parts)
        try:
            sampling_rate, channels, duration = read_wav_meta(wav)
        except (wave.Error, EOFError) as err:
            logger.error("跳过无法读取的 WAV %s: %s", wav, err)
            continue
        entry = {
            "id": wav.stem,
            "audio": str(wav.resolve()),
            "duration": round(duration, 3),
            "sampling_rate": sampling_rate,
            "channels": channels,
            "device": device,
        }
        if label == POSITIVE_LABEL:
            entry["text"] = "BT"
            entry["tokens"] = "B T"
            entry["keyword"] = "bt"
        else:
            entry["text"] = ""
            entry["tokens"] = ""
            entry["keyword"] = NEGATIVE_LABEL
        entries.append(entry)
    entries.sort(key=lambda e: e["audio"])
    return entries


def write_manifest_gz(entries: list[dict], out_path: Path) -> None:
    """Write manifest entries as one JSON object per line into a gz file.

    Parameters
    ----------
    entries : list[dict]
        Manifest entries to serialize.
    out_path : Path
        Destination ``.jsonl.gz`` path.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(out_path, "wt", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    logger.info("写出 %d 条 -> %s", len(entries), out_path)


def main() -> int:
    """Build the frozen regression test manifests and return a process exit code.

    Returns
    -------
    int
        ``0`` on success, ``1`` on a recoverable error (missing input, empty
        scan, or a refused overwrite of an existing manifest).
    """
    args = get_args()
    if not args.test_root.exists():
        logger.error("test-root 不存在: %s (先按 §10.1 复制留出集)", args.test_root)
        return 1

    pos_entries = build_entries(args.test_root, POSITIVE_LABEL)
    neg_entries = build_entries(args.test_root, NEGATIVE_LABEL)

    if not pos_entries and not neg_entries:
        logger.error(
            "未在 %s 扫到任何 wav; 拒绝用空集覆盖已有 manifest."
            "先按 §10.1 把留出集复制进 test/, 或用 --force 强制.",
            args.test_root,
        )
        return 1

    targets = [
        (POSITIVE_LABEL, pos_entries, args.out / POSITIVE_TEST_MANIFEST),
        (NEGATIVE_LABEL, neg_entries, args.out / NEGATIVE_TEST_MANIFEST),
    ]

    dev_counts: dict[str, int] = {}
    for entry in pos_entries:
        dev = entry["device"]
        dev_counts[dev] = dev_counts.get(dev, 0) + 1
    logger.info(
        "扫描完成: positive=%d (%s), negative=%d",
        len(pos_entries),
        ", ".join(f"{dev}={cnt}" for dev, cnt in sorted(dev_counts.items())),
        len(neg_entries),
    )

    if args.dry_run:
        logger.info("[dry-run] 不写文件; 目标目录=%s", args.out)
        return 0

    blocked = False
    for label, entries, path in targets:
        if path.exists() and not entries and not args.force:
            logger.error(
                "拒绝覆盖 %s 为空集 (%s 未扫到 wav); 用 --force 强制覆盖",
                path.name,
                label,
            )
            blocked = True
    if blocked:
        return 1

    for _label, entries, path in targets:
        write_manifest_gz(entries, path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
