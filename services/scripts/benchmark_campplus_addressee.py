"""CAM++ speaker embedding benchmark for Addressee Detection Phase 1.

Measures (spec draft-addressee-detection.md §3.3):
  1. 2s segment embedding extraction latency (ms) — verify 300-400ms claim
  2. rough CPU cost (wall-clock RTF per second of audio)
  3. same-person vs different-person cosine score distribution (区分度)

Uses real audio:
  * target speaker: user KWS mic captures (16 kHz mono, 3.0s)
    D:/AI/data/kws/mic_captures/*.wav (high-peak samples = real user voice)
  * different speaker: BT-7274 reference voice (16 kHz mono)
    D:/AI/workspace/bt-voice/ref_audio/bt_reference.wav
  * environment noise: esc50_neg samples (non-speech, 16 kHz)

Run: D:/AI/envs/joyai-main/python.exe services/scripts/benchmark_campplus_addressee.py
"""

from __future__ import annotations

import glob
import os
import time
import wave

import numpy as np

MODEL = (
    r"D:/AI/models/sherpa-onnx/models/speaker/3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx"
)
MIC_CAPTURES = r"D:/AI/data/kws/mic_captures"
BT_REF = r"D:/AI/workspace/bt-voice/ref_audio/bt_reference.wav"
ESC50_NEG = r"D:/AI/data/kws/esc50_neg"

TARGET_THRESHOLD = 0.6


def _read_wav_mono16(path: str) -> tuple[np.ndarray, int]:
    """Read a WAV as float32 mono in [-1, 1]; resample-on-read for 48k sources."""
    with wave.open(path, "rb") as w:
        sr = w.getframerate()
        nch = w.getnchannels()
        raw = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    if nch > 1:
        raw = raw.reshape(-1, nch)[:, 0]
    samples = raw.astype(np.float32) / 32768.0
    if sr != 16000:
        # Linear resample to 16k (benchmark only; live path already 16k).
        x_old = np.linspace(0.0, 1.0, num=len(samples), endpoint=False)
        x_new = np.linspace(0.0, 1.0, num=int(len(samples) * 16000 / sr), endpoint=False)
        samples = np.interp(x_new, x_old, samples).astype(np.float32)
        sr = 16000
    return samples, sr


def _slice_2s(samples: np.ndarray, sr: int = 16000) -> list[np.ndarray]:
    """Split into non-overlapping 2s slices (drop a trailing partial)."""
    n = int(sr * 2.0)
    return [samples[i : i + n] for i in range(0, len(samples) - n + 1, n)]


def _embed(extractor, samples: np.ndarray, sr: int) -> np.ndarray | None:
    """Compute one embedding; None if not ready (too short)."""
    stream = extractor.create_stream()
    stream.accept_waveform(sr, samples)
    stream.input_finished()
    if not extractor.is_ready(stream):
        return None
    emb = np.asarray(extractor.compute(stream), dtype=np.float32)
    return emb


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def _load_speaker_set(
    extractor, paths: list[str], label: str, max_slices: int = 12
) -> list[tuple[np.ndarray, str]]:
    """Embed each 2s slice of every file; returns [(embedding, file)]."""
    out: list[tuple[np.ndarray, str]] = []
    for p in paths:
        try:
            samples, sr = _read_wav_mono16(p)
        except Exception as exc:  # noqa: BLE001 - benchmark robustness
            print(f"  skip {os.path.basename(p)}: {exc}")
            continue
        for sl in _slice_2s(samples, sr):
            emb = _embed(extractor, sl, sr)
            if emb is not None:
                out.append((emb, os.path.basename(p)))
            if len(out) >= max_slices:
                return out
    return out


def main() -> None:
    """Run the CAM++ latency / CPU / discrimination benchmark and print results."""
    import sherpa_onnx

    cfg = sherpa_onnx.SpeakerEmbeddingExtractorConfig()
    cfg.model = MODEL
    cfg.num_threads = 2
    extractor = sherpa_onnx.SpeakerEmbeddingExtractor(cfg)
    print(f"[benchmark] model={os.path.basename(MODEL)} dim={extractor.dim}")

    # --- 1. latency: 2s slice extract (20 runs) -------------------------
    mic_files = sorted(glob.glob(os.path.join(MIC_CAPTURES, "*.wav")))
    # Prefer high-peak real voice captures for latency + same-person tests.
    mic_files.sort(key=lambda p: (0 if "_bad" in p else 1, p))
    mic_samples, sr = _read_wav_mono16(mic_files[0])
    slices = _slice_2s(mic_samples, sr)
    lat: list[float] = []
    for _ in range(20):
        for sl in slices[:2]:
            t0 = time.perf_counter()
            _embed(extractor, sl, sr)
            lat.append((time.perf_counter() - t0) * 1000.0)
    lat_ms = float(np.mean(lat))
    rtf = lat_ms / 2000.0
    print(
        f"[benchmark] 2s extract latency: mean={lat_ms:.1f}ms  p50={float(np.median(lat)):.1f}ms  "
        f"min={float(np.min(lat)):.1f}ms  max={float(np.max(lat)):.1f}ms  RTF={rtf:.3f}"
    )

    # --- 1b. CPU cost (one-shot at segment end; sequential extracts) -----
    try:
        import psutil

        proc = psutil.Process(os.getpid())
        _st = extractor.create_stream()
        _st.accept_waveform(sr, slices[0])
        _st.input_finished()
        extractor.compute(_st)  # warmup
        proc.cpu_percent(interval=None)
        t0 = time.perf_counter()
        for _ in range(30):
            _st = extractor.create_stream()
            _st.accept_waveform(sr, slices[0])
            _st.input_finished()
            if extractor.is_ready(_st):
                extractor.compute(_st)
        dt = time.perf_counter() - t0
        cpu = proc.cpu_percent(interval=None)
        core_s = cpu / 100.0 * dt / 30
        print(
            f"[benchmark] CPU: 30x2s extracts wall={dt:.2f}s, "
            f"proc_cpu%={cpu:.0f}, est core-seconds per 2s={core_s:.4f}"
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[benchmark] CPU measure skipped: {exc}")

    # --- 2. same-person (user mic captures) -----------------------------
    # Select REAL 3s voice captures by actual RMS (filename rmsNNN is a
    # percent-ish peak-scale, not linear RMS; 0.2s test files pollute the set).

    def _file_rms(p: str) -> float:
        """Linear RMS of a 3s+ capture; -1.0 for too-short/unreadable files."""
        try:
            with wave.open(p, "rb") as w:
                raw = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
                sr = w.getframerate()
            if len(raw) / sr < 2.5:
                return -1.0  # too short (0.2s test captures)
            s = raw.astype(np.float32) / 32768.0
            return float(np.sqrt(np.mean(s * s)))
        except Exception:  # noqa: BLE001
            return -1.0

    candidates = [p for p in mic_files if "_bad" not in p]
    candidates.sort(key=_file_rms, reverse=True)
    voice_paths = [p for p in candidates if _file_rms(p) >= 0.08][:10]
    user_paths = voice_paths or candidates[:10]
    user_embs = _load_speaker_set(extractor, user_paths, "user", max_slices=14)
    print(
        f"[benchmark] user 2s slices embedded: {len(user_embs)} "
        f"(from {len(voice_paths)} real voice captures)"
    )

    same_scores: list[float] = []
    for i in range(len(user_embs)):
        for j in range(i + 1, len(user_embs)):
            same_scores.append(_cosine(user_embs[i][0], user_embs[j][0]))
    print(
        f"[benchmark] SAME-person (user vs user) n={len(same_scores)}: "
        f"mean={float(np.mean(same_scores)):.3f} min={float(np.min(same_scores)):.3f} "
        f"max={float(np.max(same_scores)):.3f}"
    )

    # --- 3. different person (BT-7274 ref voice) ------------------------
    bt_embs = _load_speaker_set(extractor, [BT_REF], "bt", max_slices=10)
    print(f"[benchmark] BT 2s slices embedded: {len(bt_embs)}")
    diff_scores: list[float] = []
    for e_u, _ in user_embs:
        for e_b, _ in bt_embs:
            diff_scores.append(_cosine(e_u, e_b))
    print(
        f"[benchmark] DIFF-person (user vs BT) n={len(diff_scores)}: "
        f"mean={float(np.mean(diff_scores)):.3f} min={float(np.min(diff_scores)):.3f} "
        f"max={float(np.max(diff_scores)):.3f}"
    )

    # --- 3b. clean self-similarity (BT vs BT, continuous speech) --------
    # Establishes the model's intrinsic same-speaker cosine on CONTINUOUS
    # speech (mic captures are 3s windows around a short "bt" utterance, so
    # their 2s slices are mostly silence -> lower self-scores by construction).
    bt_self: list[float] = []
    for i in range(len(bt_embs)):
        for j in range(i + 1, len(bt_embs)):
            bt_self.append(_cosine(bt_embs[i][0], bt_embs[j][0]))
    print(
        f"[benchmark] SAME-person clean speech (BT vs BT) n={len(bt_self)}: "
        f"mean={float(np.mean(bt_self)):.3f} min={float(np.min(bt_self)):.3f} "
        f"max={float(np.max(bt_self)):.3f}"
    )

    # --- 4. environment noise (esc50 neg, non-speech) -------------------
    esc_paths = sorted(glob.glob(os.path.join(ESC50_NEG, "*.wav")))[:8]
    esc_embs = _load_speaker_set(extractor, esc_paths, "esc", max_slices=10)
    print(f"[benchmark] esc50 slices embedded: {len(esc_embs)}")
    esc_scores: list[float] = []
    for e_u, _ in user_embs:
        for e_n, _ in esc_embs:
            esc_scores.append(_cosine(e_u, e_n))
    if esc_scores:
        print(
            f"[benchmark] NOISE (user vs esc50) n={len(esc_scores)}: "
            f"mean={float(np.mean(esc_scores)):.3f} min={float(np.min(esc_scores)):.3f} "
            f"max={float(np.max(esc_scores)):.3f}"
        )

    # --- 5. threshold behavior at 0.6 -----------------------------------
    def acc(scores: list[float], thr: float) -> float:
        return float(np.mean([1.0 if s >= thr else 0.0 for s in scores]))

    print(
        f"[benchmark] thr=0.6: same recall={acc(same_scores, 0.6):.2f} "
        f"diff false-acc={acc(diff_scores, 0.6):.2f}"
    )
    print(
        f"[benchmark] thr=0.55: same recall={acc(same_scores, 0.55):.2f} "
        f"diff false-acc={acc(diff_scores, 0.55):.2f}"
    )
    print(
        f"[benchmark] thr=0.65: same recall={acc(same_scores, 0.65):.2f} "
        f"diff false-acc={acc(diff_scores, 0.65):.2f}"
    )

    # --- 6. SpeakerEmbeddingManager end-to-end (as used by the detector) -
    manager = sherpa_onnx.SpeakerEmbeddingManager(extractor.dim)
    for e in [emb for emb, _ in user_embs[:3]]:
        manager.add("user", e.tolist())
    print(f"[benchmark] manager enrolled: {manager.num_speakers} speaker(s)")
    hit = 0
    total = 0
    for e, _ in user_embs:
        total += 1
        if manager.search(e.tolist(), TARGET_THRESHOLD) == "user":
            hit += 1
    print(f"[benchmark] manager search (target=user, thr={TARGET_THRESHOLD}): {hit}/{total}")
    diff_hit = 0
    for e, _ in bt_embs:
        if manager.search(e.tolist(), TARGET_THRESHOLD) == "user":
            diff_hit += 1
    print(
        f"[benchmark] manager search (BT voice, thr={TARGET_THRESHOLD}): "
        f"{diff_hit}/{len(bt_embs)} false-accepted"
    )


if __name__ == "__main__":
    main()
