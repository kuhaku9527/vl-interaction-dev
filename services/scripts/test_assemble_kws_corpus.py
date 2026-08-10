"""
assemble_kws_corpus.py 的单元测试（stdlib unittest, 适配托管 Python 3.13）。

用 tempfile.TemporaryDirectory + wave 模块造最小 16k 单声道 wav 作假数据,
绝不触碰真实 D:/AI/data/kws/bt-en。覆盖：

  - 正常合并：双 src-root 各造 N(>holdout) 条正样本 + 现有 out/positive 几条；
    断言合并条数正确、jsonl 中每条 audio 指向文件存在、test/positive 两域各含
    --holdout 条、test/negative 含 --neg-holdout 条、test_manifests/positive_test.jsonl.gz
    存在且正样本条目含正确 device 字段、且未误写到 <out>/manifests/。
  - 正样本不足 --holdout -> 进程退出码非 0（subprocess 实跑脚本）。
  - --dry-run -> 不复制任何文件（test/ 不应生成 wav）。
"""

from __future__ import annotations

import argparse
import gzip
import json
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import assemble_kws_corpus as mod  # noqa: E402

PY = sys.executable
SCRIPT = HERE / "assemble_kws_corpus.py"


def make_wav(path: Path, seconds: float = 0.4, sr: int = 16000) -> None:
    """Write a minimal valid 16k mono int16 WAV (silence)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    n_frames = int(seconds * sr)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(b"\x00\x00" * n_frames)


def _pos_entry(wav: Path) -> dict:
    return {
        "id": wav.stem,
        "audio": str(wav.resolve()),
        "duration": 0.4,
        "sampling_rate": 16000,
        "channels": 1,
        "text": "BT",
        "tokens": "B T",
        "keyword": "bt",
    }


def _neg_entry(wav: Path) -> dict:
    return {
        "id": wav.stem,
        "audio": str(wav.resolve()),
        "duration": 0.4,
        "sampling_rate": 16000,
        "channels": 1,
        "text": "",
        "tokens": "",
        "keyword": "negative",
    }


def make_src_root(root: Path, n_pos: int, n_neg: int = 0) -> None:
    """Create a fake src-root with positive/ (+ optional negative/) wavs + jsonl."""
    pos_dir = root / "positive"
    pos_dir.mkdir(parents=True, exist_ok=True)
    pos_entries = []
    for i in range(n_pos):
        wav = pos_dir / f"positive_{i + 1:04d}.wav"
        make_wav(wav)
        pos_entries.append(_pos_entry(wav))
    with (root / "positive.jsonl").open("w", encoding="utf-8") as f:
        for e in pos_entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    if n_neg > 0:
        neg_dir = root / "negative"
        neg_dir.mkdir(parents=True, exist_ok=True)
        neg_entries = []
        for i in range(n_neg):
            wav = neg_dir / f"negative_{i + 1:04d}.wav"
            make_wav(wav)
            neg_entries.append(_neg_entry(wav))
        with (root / "negative.jsonl").open("w", encoding="utf-8") as f:
            for e in neg_entries:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")


def make_existing_pool(out: Path, n_pos: int, n_neg: int = 0) -> None:
    """Seed <out>/positive(.jsonl) and <out>/negative(.jsonl) as a pre-existing pool."""
    pos_dir = out / "positive"
    pos_dir.mkdir(parents=True, exist_ok=True)
    pos_entries = []
    for i in range(n_pos):
        wav = pos_dir / f"positive_{i + 1:04d}.wav"
        make_wav(wav)
        pos_entries.append(_pos_entry(wav))
    with (out / "positive.jsonl").open("w", encoding="utf-8") as f:
        for e in pos_entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    if n_neg > 0:
        neg_dir = out / "negative"
        neg_dir.mkdir(parents=True, exist_ok=True)
        neg_entries = []
        for i in range(n_neg):
            wav = neg_dir / f"negative_{i + 1:04d}.wav"
            make_wav(wav)
            neg_entries.append(_neg_entry(wav))
        with (out / "negative.jsonl").open("w", encoding="utf-8") as f:
            for e in neg_entries:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")


def make_args(
    src_roots: list[Path],
    out: Path,
    holdout: int = 3,
    neg_holdout: int = 2,
    no_build: bool = False,
    dry_run: bool = False,
    force: bool = False,
    device_map: list[str] | None = None,
) -> argparse.Namespace:
    """Build an argparse.Namespace for mod.assemble without touching sys.argv."""
    return argparse.Namespace(
        src_roots=list(src_roots),
        out=out,
        holdout=holdout,
        neg_holdout=neg_holdout,
        no_build=no_build,
        dry_run=dry_run,
        force=force,
        device_map=list(device_map) if device_map else [],
    )


class AssembleKwsCorpusTest(unittest.TestCase):
    def test_normal_merge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            out = tmp / "out"
            # 既有训练池：正 3 / 负 2
            make_existing_pool(out, n_pos=3, n_neg=2)
            # 双声学域：broadcast 6 正 4 负；gameDAC 5 正 3 负
            src_b = tmp / "src" / "broadcast"
            src_g = tmp / "src" / "gameDAC"
            make_src_root(src_b, n_pos=6, n_neg=4)
            make_src_root(src_g, n_pos=5, n_neg=3)

            holdout = 3
            neg_holdout = 2
            args = make_args(
                [src_b, src_g], out, holdout=holdout, neg_holdout=neg_holdout
            )
            summary = mod.assemble(args)

            # 训练正样本 = 既有 3 + broadcast 余 3 + gameDAC 余 2 = 8
            pos_jsonl = out / "positive.jsonl"
            self.assertTrue(pos_jsonl.exists(), "positive.jsonl 应被写出")
            pos = mod.load_jsonl(pos_jsonl)
            self.assertEqual(len(pos), 3 + (6 - holdout) + (5 - holdout))
            for e in pos:
                self.assertTrue(Path(e["audio"]).exists(), f"audio 缺失: {e['audio']}")

            # 训练负样本 = 既有 2 + 4 + 3 = 9
            neg = mod.load_jsonl(out / "negative.jsonl")
            self.assertEqual(len(neg), 2 + 4 + 3)

            # 两域各含 holdout 条留出正样本
            b_dir = out / "test" / "positive" / "nvidia_broadcast"
            g_dir = out / "test" / "positive" / "gameDAC_chat"
            self.assertEqual(len(list(b_dir.glob("*.wav"))), holdout)
            self.assertEqual(len(list(g_dir.glob("*.wav"))), holdout)

            # 留出负样本
            neg_test_dir = out / "test" / "negative"
            self.assertEqual(len(list(neg_test_dir.glob("*.wav"))), neg_holdout)

            # 冻结 manifest 落独立目录（绝不 manifests/）
            manifest = out / "test_manifests" / "positive_test.jsonl.gz"
            self.assertTrue(manifest.exists(), "test_manifests/positive_test.jsonl.gz 应存在")
            self.assertFalse(
                (out / "manifests").exists(),
                "绝不能误写到 <out>/manifests/ (会与 prep_kws_data 冲突)",
            )
            devices: set[str] = set()
            with gzip.open(manifest, "rt", encoding="utf-8") as f:
                for line in f:
                    e = json.loads(line)
                    devices.add(e["device"])
                    self.assertIn(e["device"], ("nvidia_broadcast", "gameDAC_chat"))
            self.assertEqual(devices, {"nvidia_broadcast", "gameDAC_chat"})

            # 汇总字段
            self.assertEqual(summary["holdout_pos"], holdout * 2)
            self.assertEqual(summary["holdout_neg"], neg_holdout)
            self.assertEqual(summary["train_neg"], 2 + 4 + 3)
            self.assertTrue(summary["built"])

    def test_insufficient_positive_exits_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            out = tmp / "out"
            # 仅 2 条正样本, holdout 要求 5 -> 必须非 0 退出
            src = tmp / "src" / "broadcast"
            make_src_root(src, n_pos=2, n_neg=0)

            cmd = [
                PY,
                str(SCRIPT),
                "--src-roots",
                str(src),
                "--out",
                str(out),
                "--holdout",
                "5",
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            self.assertNotEqual(proc.returncode, 0, msg=proc.stderr)
            self.assertIn("正样本仅", proc.stderr + proc.stdout)

    def test_dry_run_no_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            out = tmp / "out"
            make_existing_pool(out, n_pos=2, n_neg=1)
            src_b = tmp / "src" / "broadcast"
            src_g = tmp / "src" / "gameDAC"
            make_src_root(src_b, n_pos=5, n_neg=2)
            make_src_root(src_g, n_pos=4, n_neg=2)

            args = make_args(
                [src_b, src_g], out, holdout=3, neg_holdout=2, dry_run=True
            )
            summary = mod.assemble(args)
            self.assertTrue(summary["dry_run"])

            # 不复制/不写文件
            self.assertFalse((out / "test").exists(), "dry-run 不应生成 test/")
            self.assertFalse(
                (out / "test_manifests").exists(), "dry-run 不应生成 test_manifests/"
            )
            new_pos = list((out / "positive").glob("*_pos_*.wav"))
            self.assertEqual(new_pos, [], "dry-run 不应复制训练正样本")
            new_neg = list((out / "negative").glob("*_neg_*.wav"))
            self.assertEqual(new_neg, [], "dry-run 不应复制训练负样本")

    def test_no_build_skips_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            out = tmp / "out"
            make_existing_pool(out, n_pos=2, n_neg=1)
            src_b = tmp / "src" / "broadcast"
            make_src_root(src_b, n_pos=5, n_neg=2)

            args = make_args([src_b], out, holdout=3, neg_holdout=2, no_build=True)
            summary = mod.assemble(args)
            self.assertFalse(summary["built"])
            # 训练池与留出集仍生成, 但 manifest 不生成
            self.assertTrue((out / "positive.jsonl").exists())
            self.assertEqual(len(list((out / "test" / "positive").rglob("*.wav"))), 3)
            self.assertFalse((out / "test_manifests").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
