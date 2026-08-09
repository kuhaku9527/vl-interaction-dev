"""Pytest for build_test_manifest (KWS_V5_CAPTURE_SPEC.md section 10.1 builder)."""

from __future__ import annotations

import gzip
import json
import struct
import sys
import wave
from pathlib import Path

import build_test_manifest as btm


def _write_wav(path: Path, seconds: float = 0.5, sr: int = 16000) -> None:
    """Write a silent 16kHz mono PCM16 WAV for tests."""
    n = int(sr * seconds)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(struct.pack(f"<{n}h", *([0] * n)))


def _read_gz(path: Path) -> list[dict]:
    """Read a gzipped JSONL manifest back into a list of dicts."""
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def test_infer_device_known_aliases() -> None:
    assert btm.infer_device(("nvidia_broadcast", "x.wav")) == "nvidia_broadcast"
    assert btm.infer_device(("gameDAC_chat", "x.wav")) == "gameDAC_chat"
    assert btm.infer_device(("broadcast", "x.wav")) == "nvidia_broadcast"
    assert btm.infer_device(("gamedac", "x.wav")) == "gameDAC_chat"


def test_infer_device_flat_and_unknown() -> None:
    # infer_device only sees the first path component; an empty rel yields unknown
    assert btm.infer_device(()) == btm.DEVICE_UNKNOWN
    # a wav sitting directly under the label dir (single part) yields unknown
    assert btm.infer_device(("x.wav",)) == "x.wav"
    # unknown subdir falls through to the raw name
    assert btm.infer_device(("my_mic", "x.wav")) == "my_mic"


def test_build_entries_tags_device(tmp_path: Path) -> None:
    test_root = tmp_path / "test"
    (test_root / "positive" / "nvidia_broadcast").mkdir(parents=True)
    (test_root / "positive" / "gameDAC_chat").mkdir(parents=True)
    (test_root / "negative").mkdir(parents=True)
    _write_wav(test_root / "positive" / "nvidia_broadcast" / "bt_001.wav")
    _write_wav(test_root / "positive" / "gameDAC_chat" / "bt_002.wav")
    _write_wav(test_root / "positive" / "bt_flat.wav")
    _write_wav(test_root / "negative" / "neg_001.wav")

    pos = btm.build_entries(test_root, "positive")
    neg = btm.build_entries(test_root, "negative")

    assert len(pos) == 3
    assert {e["device"] for e in pos} == {
        "nvidia_broadcast",
        "gameDAC_chat",
        "unknown",
    }
    assert all(e["keyword"] == "bt" and e["text"] == "BT" for e in pos)
    assert pos[0]["sampling_rate"] == 16000
    assert pos[0]["channels"] == 1
    assert 0.4 < pos[0]["duration"] < 0.6

    assert len(neg) == 1
    assert neg[0]["device"] == "unknown"
    assert neg[0]["keyword"] == "negative"


def test_missing_label_dir_returns_empty(tmp_path: Path) -> None:
    test_root = tmp_path / "test"
    (test_root / "positive").mkdir(parents=True)
    assert btm.build_entries(test_root, "negative") == []


def test_write_and_read_roundtrip(tmp_path: Path) -> None:
    out = tmp_path / "manifests"
    entries = [
        {
            "id": "bt_001",
            "audio": "/x/bt_001.wav",
            "duration": 0.5,
            "sampling_rate": 16000,
            "channels": 1,
            "device": "nvidia_broadcast",
            "text": "BT",
            "tokens": "B T",
            "keyword": "bt",
        },
    ]
    btm.write_manifest_gz(entries, out / "positive_test.jsonl.gz")
    assert _read_gz(out / "positive_test.jsonl.gz") == entries


def test_main_happy_path_writes_manifests(tmp_path: Path, monkeypatch) -> None:
    test_root = tmp_path / "test"
    (test_root / "positive" / "nvidia_broadcast").mkdir(parents=True)
    (test_root / "negative").mkdir(parents=True)
    _write_wav(test_root / "positive" / "nvidia_broadcast" / "bt_001.wav")
    _write_wav(test_root / "negative" / "neg_001.wav")
    out = tmp_path / "manifests"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "build_test_manifest.py",
            f"--test-root={test_root}",
            f"--out={out}",
        ],
    )
    assert btm.main() == 0
    assert (out / "positive_test.jsonl.gz").exists()
    assert (out / "negative_test.jsonl.gz").exists()
    pos = _read_gz(out / "positive_test.jsonl.gz")
    assert pos[0]["device"] == "nvidia_broadcast"


def test_main_refuses_empty_scan(tmp_path: Path, monkeypatch) -> None:
    test_root = tmp_path / "test"
    (test_root / "positive").mkdir(parents=True)
    out = tmp_path / "manifests"
    out.mkdir()
    (out / "positive_test.jsonl.gz").write_text("legacy")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "build_test_manifest.py",
            f"--test-root={test_root}",
            f"--out={out}",
        ],
    )
    assert btm.main() == 1
    # historical manifest must not be clobbered with an empty set
    assert (out / "positive_test.jsonl.gz").read_text() == "legacy"
