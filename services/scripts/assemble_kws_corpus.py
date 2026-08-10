"""
合并 KWS v5 双声学域唤醒词 ("BT") 录音，切出冻结留出集。

背景（KWS_V5_CAPTURE_SPEC.md §10）：正样本分两个声学域录制 ——
  - D:/AI/data/kws/bt-en/broadcast  (设备 idx=1, NVIDIA Broadcast 降噪虚拟麦)
  - D:/AI/data/kws/bt-en/gameDAC    (设备 idx=3, GameDAC Chat 原始麦)
每个 src-root 由 record_kws_corpus.py 生成 ``positive/*.wav`` (+ ``positive.jsonl``)
与可选 ``negative/*.wav`` (+ ``negative.jsonl``)。

本脚本把多个 src-root 合并成单一训练池，并从每个域**复制**（不移动）固定条数正样本
到 ``<out>/test/positive/<device>/`` 作为冻结留出集；负样本同样从合并池取前 N 条复制到
``<out>/test/negative/``。随后调用 build_test_manifest.py 把冻结集扫成
``<out>/test_manifests/positive_test.jsonl.gz`` + ``negative_test.jsonl.gz``。

关键约束（规避与 prep_kws_data.py 的文件名冲突）：
  build_test_manifest.py 必须写到独立的 ``<out>/test_manifests/``，**绝不**写到
  ``<out>/manifests/``（否则会互相覆盖 positive_test.jsonl.gz，导致回归门禁读到的不是真
  冻结集）。

设计要点（约法三章）：
  - 纯标准库（argparse/json/logging/shutil/sys/wave/pathlib），不引入 sounddevice/soundfile/numpy。
  - 读 wav 时长用 ``wave`` 模块（对齐 build_test_manifest.read_wav_meta）。
  - 不写无意义的防御性判空；缺 positive.jsonl / 正样本不足 / 目标 wav 已存在（非 --force）/
    build 返回非 0 一律 raise 明确异常或 log + 显式错误态，绝不静默 fallback。
  - 全程 logging 记录每步条数；结尾打印汇总。

用法::

    python assemble_kws_corpus.py \\
        --src-roots D:/AI/data/kws/bt-en/broadcast D:/AI/data/kws/bt-en/gameDAC \\
        --out D:/AI/data/kws/bt-en \\
        --holdout 15 --neg-holdout 15
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
import sys
import wave
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("kws-assemble")

# 默认双声学域 src-root（Windows 侧实际落点）。
DEFAULT_SRC_ROOTS: list[Path] = [
    Path("D:/AI/data/kws/bt-en/broadcast"),
    Path("D:/AI/data/kws/bt-en/gameDAC"),
]
DEFAULT_OUT: Path = Path("D:/AI/data/kws/bt-en")

# src-root 基名 -> 规范 device token（用作 test/positive/<device>/ 子目录名，
# 同时被 build_test_manifest.infer_device 识别成正确的 device 字段）。
DEFAULT_DEVICE_ALIASES: dict[str, str] = {
    "broadcast": "nvidia_broadcast",
    "gameDAC": "gameDAC_chat",
}

POSITIVE_LABEL = "positive"
NEGATIVE_LABEL = "negative"


def get_args() -> argparse.Namespace:
    """Parse command-line arguments for the corpus assembler.

    Returns
    -------
    argparse.Namespace
        Parsed arguments: ``src_roots``, ``out``, ``holdout``, ``neg_holdout``,
        ``no_build``, ``dry_run``, ``force``, ``device_map``.
    """
    parser = argparse.ArgumentParser(
        description="合并 KWS 双声学域录音 -> 训练池 + 冻结留出集 (§10).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--src-roots",
        nargs="+",
        type=Path,
        default=DEFAULT_SRC_ROOTS,
        help="各声学域录制根（含 positive/ 与可选 negative/）。",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help="训练池 + test/ + test_manifests/ 的根目录。",
    )
    parser.add_argument("--holdout", type=int, default=15, help="每个域冻结多少正样本作留出集。")
    parser.add_argument("--neg-holdout", type=int, default=15, help="冻结多少负样本到 test/negative/。")
    parser.add_argument("--no-build", action="store_true", help="跳过运行 build_test_manifest.py。")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划, 不复制/不写文件。")
    parser.add_argument("--force", action="store_true", help="允许覆盖已存在的 test/ 下 wav。")
    parser.add_argument(
        "--device-map",
        action="append",
        default=[],
        metavar="SRC_BASENAME=DEVICE",
        help="覆盖 src-root 基名 -> device 的映射, 可重复, 如 broadcast=nvidia_broadcast。",
    )
    return parser.parse_args()


def build_device_map(overrides: list[str]) -> dict[str, str]:
    """Merge default device aliases with ``--device-map`` overrides.

    Parameters
    ----------
    overrides : list[str]
        Raw ``key=value`` strings from ``--device-map``.

    Returns
    -------
    dict[str, str]
        Final mapping from src-root basename to canonical device token.

    Raises
    ------
    ValueError
        If an override is not in ``key=value`` form.
    """
    device_map = dict(DEFAULT_DEVICE_ALIASES)
    for item in overrides:
        if "=" not in item:
            raise ValueError(f"--device-map 必须是 key=value 形式, 收到: {item!r}")
        key, val = item.split("=", 1)
        device_map[key.strip()] = val.strip()
    return device_map


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


def load_jsonl(path: Path) -> list[dict]:
    """Load a ``.jsonl`` manifest into a list of dicts.

    Parameters
    ----------
    path : Path
        Manifest path.

    Returns
    -------
    list[dict]
        One dict per non-empty line.
    """
    entries: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entries.append(json.loads(line))
    return entries


def write_jsonl(entries: list[dict], path: Path) -> None:
    """Write manifest entries as one JSON object per line.

    Parameters
    ----------
    entries : list[dict]
        Manifest entries to serialize.
    path : Path
        Destination ``.jsonl`` path.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def copy_wav_make_entry(src: Path, dst: Path, label: str, force: bool) -> dict:
    """Copy a WAV into place and build a manifest entry for the destination.

    Parameters
    ----------
    src : Path
        Source wav (already validated to exist by the caller).
    dst : Path
        Destination wav path.
    label : str
        ``"positive"`` or ``"negative"``; controls the text/tokens/keyword fields.
    force : bool
        If ``False`` and ``dst`` already exists, raise ``FileExistsError``.

    Returns
    -------
    dict
        Manifest entry with ``id``/``audio`` (resolved absolute) / metadata and
        the label-specific fields.

    Raises
    ------
    FileExistsError
        If ``dst`` exists and ``force`` is ``False``.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() and not force:
        raise FileExistsError(
            f"目标 wav 已存在且未指定 --force: {dst}\n  若需覆盖请加 --force"
        )
    shutil.copy2(src, dst)
    sampling_rate, channels, duration = read_wav_meta(dst)
    entry: dict = {
        "id": dst.stem,
        "audio": str(dst.resolve()),
        "duration": round(duration, 3),
        "sampling_rate": sampling_rate,
        "channels": channels,
    }
    if label == POSITIVE_LABEL:
        entry["text"] = "BT"
        entry["tokens"] = "B T"
        entry["keyword"] = "bt"
    else:
        entry["text"] = ""
        entry["tokens"] = ""
        entry["keyword"] = "negative"
    return entry


def assemble(args: argparse.Namespace) -> dict:
    """Merge dual-domain KWS recordings into a training pool + carve a frozen holdout.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed CLI args (``src_roots``, ``out``, ``holdout``, ``neg_holdout``,
        ``no_build``, ``dry_run``, ``force``, ``device_map``).

    Returns
    -------
    dict
        Summary counters for logging/inspection: ``train_pos``, ``train_neg``,
        ``domain_holdout``, ``holdout_pos``, ``holdout_neg``, ``dry_run``, ``built``.

    Raises
    ------
    FileNotFoundError
        When a required ``positive.jsonl`` is missing or a referenced wav does not exist.
    ValueError
        When a domain has fewer positive samples than ``--holdout`` (no silent downgrade).
    FileExistsError
        When a target wav already exists and ``--force`` was not given.
    RuntimeError
        When the frozen-manifest builder (build_test_manifest.py) exits non-zero.
    """
    out: Path = args.out
    holdout: int = args.holdout
    neg_holdout: int = args.neg_holdout
    dry_run: bool = args.dry_run
    device_map = build_device_map(args.device_map)

    # 1. 载入既有扁平池：仅入训练池, 绝不进留出集。
    existing_pos: list[dict] = []
    existing_neg: list[dict] = []
    if (out / "positive.jsonl").exists():
        existing_pos = load_jsonl(out / "positive.jsonl")
        logger.info("载入既有训练正样本池: %d 条 (仅入训练池, 绝不进留出集)", len(existing_pos))
    if (out / "negative.jsonl").exists():
        existing_neg = load_jsonl(out / "negative.jsonl")
        logger.info("载入既有训练负样本池: %d 条", len(existing_neg))

    train_pos: list[dict] = list(existing_pos)
    train_neg: list[dict] = []
    train_neg_src: list[str] = []  # 与 train_neg 平行的来源标签, 用于留出负样本命名
    for entry in existing_neg:
        train_neg.append(entry)
        train_neg_src.append("existing")

    domain_holdout_counts: dict[str, int] = {}
    total_holdout_pos = 0

    # 2. 逐 src-root 处理
    for src in args.src_roots:
        device = device_map.get(src.name)
        if device is None:
            logger.warning(
                "src-root 基名 %r 不在 device-map 中, 退回原样用作 device 标签", src.name
            )
            device = src.name

        pos_jsonl = src / "positive.jsonl"
        if not pos_jsonl.exists():
            raise FileNotFoundError(
                f"域 {device} 缺 positive.jsonl: {pos_jsonl}\n"
                f"  先跑 record_kws_corpus.py --label positive --data-root {src}"
            )
        positive = load_jsonl(pos_jsonl)
        logger.info("域 %s 载入正样本 %d 条 (src=%s)", device, len(positive), src)

        # 2b. 正样本不足 -> 显式报错, 禁止静默降级
        if len(positive) < holdout:
            raise ValueError(
                f"域 {device} 正样本仅 {len(positive)} < holdout {holdout},"
                f"请先多录几段再切留出集"
            )

        hold = positive[:holdout]
        rest = positive[holdout:]
        prefix = src.name

        # 2d. 留出正样本 -> test/positive/<device>/(复制, 不移动)
        test_pos_dir = out / "test" / "positive" / device
        if not dry_run:
            test_pos_dir.mkdir(parents=True, exist_ok=True)
        hold_copied = 0
        for i, entry in enumerate(hold):
            src_wav = Path(entry["audio"])
            dst = test_pos_dir / f"{prefix}_hold_{i:04d}.wav"
            if dry_run:
                logger.info("[dry-run] 将复制留出正样本 %s -> %s", src_wav, dst)
            else:
                if not src_wav.exists():
                    raise FileNotFoundError(f"留出正样本 wav 不存在: {src_wav} (src={src})")
                copy_wav_make_entry(src_wav, dst, POSITIVE_LABEL, args.force)
                hold_copied += 1
        domain_holdout_counts[device] = domain_holdout_counts.get(device, 0) + hold_copied
        total_holdout_pos += hold_copied
        logger.info("域 %s: 留出正样本 %d 条 -> %s", device, hold_copied, test_pos_dir)

        # 2e. 余下正样本 -> <out>/positive/<prefix>_pos_<i>.wav (改写 audio 入训练池)
        train_pos_dir = out / "positive"
        if not dry_run:
            train_pos_dir.mkdir(parents=True, exist_ok=True)
        added = 0
        for i, entry in enumerate(rest):
            src_wav = Path(entry["audio"])
            dst = train_pos_dir / f"{prefix}_pos_{i:04d}.wav"
            if dry_run:
                logger.info("[dry-run] 将复制训练正样本 %s -> %s", src_wav, dst)
            else:
                if not src_wav.exists():
                    raise FileNotFoundError(f"训练正样本 wav 不存在: {src_wav} (src={src})")
                new_entry = copy_wav_make_entry(src_wav, dst, POSITIVE_LABEL, args.force)
                train_pos.append(new_entry)
                added += 1
        logger.info(
            "域 %s: 训练正样本新增 %d 条 (沿用既有 %d 条)", device, added, len(existing_pos)
        )

        # 2f. 负样本 -> <out>/negative/<prefix>_neg_<i>.wav (合并进训练负样本池)
        neg_jsonl = src / "negative.jsonl"
        if neg_jsonl.exists():
            negatives = load_jsonl(neg_jsonl)
            logger.info("域 %s 载入负样本 %d 条", device, len(negatives))
            train_neg_dir = out / "negative"
            if not dry_run:
                train_neg_dir.mkdir(parents=True, exist_ok=True)
            n_added = 0
            for i, entry in enumerate(negatives):
                src_wav = Path(entry["audio"])
                dst = train_neg_dir / f"{prefix}_neg_{i:04d}.wav"
                if dry_run:
                    logger.info("[dry-run] 将复制训练负样本 %s -> %s", src_wav, dst)
                else:
                    if not src_wav.exists():
                        raise FileNotFoundError(f"负样本 wav 不存在: {src_wav} (src={src})")
                    new_entry = copy_wav_make_entry(src_wav, dst, NEGATIVE_LABEL, args.force)
                    train_neg.append(new_entry)
                    train_neg_src.append(prefix)
                    n_added += 1
            logger.info(
                "域 %s: 训练负样本新增 %d 条 (沿用既有 %d 条)", device, n_added, len(existing_neg)
            )
        else:
            logger.info("域 %s: 无 negative.jsonl, 跳过负样本", device)

    # 3/4. 写出训练池 manifest（先校验 audio 真实存在, fail-fast）
    if not dry_run:
        missing_pos = [e["audio"] for e in train_pos if not Path(e["audio"]).exists()]
        if missing_pos:
            raise FileNotFoundError(
                f"训练正样本 manifest 引用 {len(missing_pos)} 个不存在的 wav, 例如:\n"
                f"    {missing_pos[0]}"
            )
        missing_neg = [e["audio"] for e in train_neg if not Path(e["audio"]).exists()]
        if missing_neg:
            raise FileNotFoundError(
                f"训练负样本 manifest 引用 {len(missing_neg)} 个不存在的 wav, 例如:\n"
                f"    {missing_neg[0]}"
            )
        write_jsonl(train_pos, out / "positive.jsonl")
        write_jsonl(train_neg, out / "negative.jsonl")
        logger.info("写出训练正样本 %d 条 -> %s", len(train_pos), out / "positive.jsonl")
        logger.info("写出训练负样本 %d 条 -> %s", len(train_neg), out / "negative.jsonl")

    # 4. 留出负样本 -> test/negative/<src>_neg_<i>.wav (从合并池取前 neg_holdout)
    neg_hold_copied = 0
    if not dry_run:
        test_neg_dir = out / "test" / "negative"
        test_neg_dir.mkdir(parents=True, exist_ok=True)
        for i, (entry, tag) in enumerate(list(zip(train_neg, train_neg_src))[:neg_holdout]):
            src_wav = Path(entry["audio"])
            if not src_wav.exists():
                raise FileNotFoundError(f"留出负样本 wav 不存在: {src_wav}")
            dst = test_neg_dir / f"{tag}_neg_{i:04d}.wav"
            copy_wav_make_entry(src_wav, dst, NEGATIVE_LABEL, args.force)
            neg_hold_copied += 1
        logger.info("留出负样本 %d 条 -> %s", neg_hold_copied, test_neg_dir)
    else:
        # dry-run: 仅统计计划数, 不触碰 fs
        neg_hold_copied = min(neg_holdout, len(train_neg))

    # 5. 生成冻结留出集 manifest（独立目录 test_manifests/, 绝不 manifests/）
    built = False
    if not args.no_build and not dry_run:
        repo_root = Path(__file__).resolve().parents[2]
        build_script = repo_root / "services" / "kws-training" / "build_test_manifest.py"
        test_root = out / "test"
        test_manifests = out / "test_manifests"
        if not build_script.exists():
            raise FileNotFoundError(f"找不到 build_test_manifest.py: {build_script}")
        logger.info(
            "调用 build_test_manifest.py: --test-root %s --out %s", test_root, test_manifests
        )
        try:
            subprocess.run(
                [
                    sys.executable,
                    str(build_script),
                    "--test-root",
                    str(test_root),
                    "--out",
                    str(test_manifests),
                ],
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            logger.error(
                "build_test_manifest.py 返回非零退出码 %s; 冻结留出集生成失败, 未静默忽略",
                exc.returncode,
            )
            raise RuntimeError(f"build_test_manifest 失败 (exit={exc.returncode})") from exc
        built = True

    return {
        "train_pos": len(train_pos),
        "train_neg": len(train_neg),
        "domain_holdout": domain_holdout_counts,
        "holdout_pos": total_holdout_pos,
        "holdout_neg": neg_hold_copied,
        "dry_run": dry_run,
        "built": built,
    }


def main() -> int:
    """Assemble the KWS corpus and return a process exit code.

    Returns
    -------
    int
        ``0`` on success; ``1`` on any explicit error (missing input, insufficient
        positives, existing target wav, or a failed build).
    """
    args = get_args()
    try:
        summary = assemble(args)
    except Exception as exc:  # noqa: BLE001 - top-level boundary, logged explicitly
        logger.error("assemble_kws_corpus 失败 (将以非 0 退出): %s", exc)
        return 1

    logger.info("=" * 46)
    logger.info("汇总 | 训练正样本=%d 训练负样本=%d", summary["train_pos"], summary["train_neg"])
    for dev, cnt in summary["domain_holdout"].items():
        logger.info("  域 %-16s 留出正样本=%d", dev, cnt)
    logger.info("  留出负样本=%d", summary["holdout_neg"])
    if summary["dry_run"]:
        logger.info("  [dry-run] 未写入/复制任何文件")
    elif summary["built"]:
        logger.info("  已生成冻结留出集 manifest -> %s/test_manifests/", args.out)
    logger.info("=" * 46)
    return 0


if __name__ == "__main__":
    sys.exit(main())
