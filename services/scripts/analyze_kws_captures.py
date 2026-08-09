"""Analyze live KWS diagnostic captures.

Reads 16 kHz mono PCM16 WAV files, runs the current Jarvis KWS config and
streaming ASR, then prints one TSV row per file:

    file    kws_hit    asr_text    duration_s

Use this after a live Listen test to decide which captures are positives,
hard negatives, or input-device failures.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import wave
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "services" / "webui" / "src"))

from joy_interaction_webui.jarvis_mode import JarvisConfig

from services.asr.jarvis.asr import JarvisASR
from services.asr.jarvis.kws import JarvisKWS

logger = logging.getLogger("analyze_kws_captures")

DEFAULT_CAPTURE_DIR = Path("D:/AI/data/kws/mic_captures")

# Wake patterns that, if seen in shadow ASR text, mean the user said "bt".
# Mirrors JarvisConfig.asr_confirm_patterns.
_WAKE_PATTERNS = ("bt", "BT", "B T", "b t")


def _iter_wavs(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    return sorted(path.glob("*.wav"))


def _read_chunks(wav_path: Path, chunk_frames: int = 1600):
    with wave.open(str(wav_path), "rb") as wf:
        if wf.getnchannels() != 1 or wf.getsampwidth() != 2 or wf.getframerate() != 16000:
            raise ValueError(f"{wav_path} must be 16kHz mono PCM16")
        nframes = wf.getnframes()
        while True:
            pcm = wf.readframes(chunk_frames)
            if not pcm:
                break
            yield pcm, nframes / 16000.0


def analyze_one(wav_path: Path, cfg: JarvisConfig) -> dict:
    kws = JarvisKWS(
        model_dir=cfg.kws_model_dir,
        wake_word=cfg.wake_word,
        num_threads=cfg.kws_num_threads,
        keywords_score=cfg.kws_keywords_score,
        keywords_threshold=cfg.kws_keywords_threshold,
        num_trailing_blanks=cfg.kws_num_trailing_blanks,
        max_active_paths=cfg.kws_max_active_paths,
    )
    asr = JarvisASR(model_dir=cfg.asr_model_dir, num_threads=cfg.asr_num_threads)
    kws.start()
    asr.start()

    # VAD (form A) — optional, for miss-kill measurement. Fail-open: if the
    # Silero VAD onnx is absent (JARVIS_VAD_MODEL_DIR unset / no asset) we
    # skip VAD scoring and the vad_miss_kill column reports n/a.
    vad = None
    vad_model_dir = os.environ.get("JARVIS_VAD_MODEL_DIR", "").strip()
    if vad_model_dir:
        try:
            import numpy as np
            import sherpa_onnx

            _vcfg = sherpa_onnx.SileroVadModelConfig()
            _vcfg.model = os.path.join(vad_model_dir, "silero_vad.onnx")
            _vcfg.threshold = 0.5
            _vcfg.min_silence_duration = 0.5
            _vcfg.min_speech_duration = 0.25
            _vcfg.window_size = 512
            vad = sherpa_onnx.VoiceActivityDetector(_vcfg, buffer_size_in_seconds=60)
        except Exception as exc:
            logger.warning("VAD unavailable for analysis (%s); skipping VAD column", exc)
            vad = None

    hit = False
    text = ""
    duration = 0.0
    vad_miss_kill = 0  # chunks VAD=silence but shadow ASR heard the wake word
    vad_bt_chunks = 0  # chunks where shadow ASR heard the wake word
    for pcm, chunk_dur in _read_chunks(wav_path):
        duration = chunk_dur
        if kws.feed_audio(pcm):
            hit = True
        next_text = asr.feed_chunk(pcm) or ""
        if next_text:
            text = next_text
        if vad is not None and pcm and len(pcm) % 2 == 0:
            float32 = (
                np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
            )
            vad.accept_waveform(float32)
            vad_speech = vad.is_speech_detected()
            if any(pat in text for pat in _WAKE_PATTERNS):
                vad_bt_chunks += 1
                if not vad_speech:
                    vad_miss_kill += 1
    kws.stop()
    asr.stop()
    return {
        "file": str(wav_path),
        "kws_hit": hit,
        "asr_text": text,
        "duration_s": duration,
        "vad_available": vad is not None,
        "vad_miss_kill": vad_miss_kill,
        "vad_bt_chunks": vad_bt_chunks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze KWS live capture WAVs")
    parser.add_argument("path", nargs="?", type=Path, default=DEFAULT_CAPTURE_DIR)
    args = parser.parse_args()

    cfg = JarvisConfig.from_env()
    wavs = _iter_wavs(args.path)
    if not wavs:
        print(f"No wav files found: {args.path}", file=sys.stderr)
        return 1

    print("file\tkws_hit\tasr_text\tduration_s\tvad_miss_kill")
    total_miss_kill = 0
    total_bt_chunks = 0
    for wav_path in wavs:
        try:
            row = analyze_one(wav_path, cfg)
        except Exception as exc:
            print(f"{wav_path}\tERROR\t{exc}\t0\tn/a")
            continue
        if row["vad_available"]:
            mk = row["vad_miss_kill"]
            total_miss_kill += mk
            total_bt_chunks += row["vad_bt_chunks"]
            mk_str = str(mk)
        else:
            mk_str = "n/a"
        print(
            f"{row['file']}\t{int(row['kws_hit'])}\t"
            f"{row['asr_text']}\t{row['duration_s']:.2f}\t{mk_str}"
        )

    # VAD miss-kill rate (chunks VAD=silence but shadow ASR heard "bt") over
    # all chunks where shadow ASR heard "bt". Only meaningful when VAD enabled.
    if total_bt_chunks > 0:
        rate = total_miss_kill / total_bt_chunks
        print(
            f"VAD miss-kill rate: {total_miss_kill}/{total_bt_chunks} = {rate:.1%}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
