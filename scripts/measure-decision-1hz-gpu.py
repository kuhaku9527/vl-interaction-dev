"""#148 实测三：GPU 占用率的独立采样（修正采样假象）。

问题：probe_1hz.py 在"请求刚结束"时读 nvidia-smi，正好撞上繁忙期，
      得到的 85% 可能是采样假象。

方法：在**独立线程**里持续采样 GPU 利用率（不依赖请求时序），
      分别测「空闲基线」与「1Hz 持续推理」两个窗口，对比真实占用。

用法: python .cache/ledger/probe_gpu.py
"""
from __future__ import annotations

import json
import statistics
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".cache" / "ledger"
BASE = "http://127.0.0.1:7060"

ROOT = Path(__file__).resolve().parents[1]


def _ensure_assets() -> None:
    """Bootstrap the 768x576 test frame + production prompt into CACHE."""
    CACHE.mkdir(parents=True, exist_ok=True)
    frame_path = CACHE / "frame.b64"
    prompt_path = CACHE / "sys_prompt.txt"
    if not frame_path.exists():
        import base64
        import io

        from PIL import Image, ImageDraw

        img = Image.new("RGB", (768, 576), (40, 45, 60))
        d = ImageDraw.Draw(img)
        d.rectangle([80, 80, 420, 360], fill=(180, 70, 70))
        d.ellipse([440, 180, 660, 400], fill=(230, 210, 90))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=85)
        frame_path.write_text(base64.b64encode(buf.getvalue()).decode())
    if not prompt_path.exists():
        sys.path.insert(0, str(ROOT / "services" / "webinfer"))
        from prompt_constants import LIVE_SYSTEM_PROMPT_EN

        prompt_path.write_text(LIVE_SYSTEM_PROMPT_EN, encoding="utf-8")


_ensure_assets()
FRAME_B64 = (CACHE / "frame.b64").read_text().strip()
SYS_PROMPT = (CACHE / "sys_prompt.txt").read_text(encoding="utf-8")

stop_flag = threading.Event()
samples: list[tuple[float, int, int]] = []


def sampler() -> None:
    """独立线程：每 200ms 采一次，不等待请求。"""
    while not stop_flag.is_set():
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True,
        ).stdout.strip().splitlines()
        if out:
            util, mem = [x.strip() for x in out[0].split(",")]
            samples.append((time.time(), int(util), int(mem)))
        time.sleep(0.2)


def request(idx: int) -> float:
    body = {
        "model": "joyai",
        "messages": [
            {"role": "system", "content": SYS_PROMPT},
            {"role": "user", "content": [
                {"type": "text", "text": "t=%d" % idx},
                {"type": "image_url",
                 "image_url": {"url": "data:image/jpeg;base64," + FRAME_B64}},
            ]},
        ],
        "max_tokens": 32, "temperature": 0.1,
    }
    req = urllib.request.Request(
        BASE + "/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    t0 = time.perf_counter()
    urllib.request.urlopen(req, timeout=180).read()
    return (time.perf_counter() - t0) * 1000


def window(label: str, seconds: int, with_load: bool) -> dict:
    global samples
    samples = []
    stop_flag.clear()
    th = threading.Thread(target=sampler, daemon=True)
    th.start()
    t_end = time.time() + seconds
    n = 0
    walls = []
    if with_load:
        i = 1
        while time.time() < t_end:
            walls.append(request(i))
            i += 1
            nxt = time.time() + 1.0  # 严格 1Hz
            while time.time() < nxt and time.time() < t_end:
                time.sleep(0.02)
    else:
        time.sleep(seconds)
    stop_flag.set()
    th.join(timeout=2)
    utils = [s[1] for s in samples]
    mems = [s[2] for s in samples]
    res = {
        "label": label,
        "seconds": seconds,
        "samples": len(utils),
        "gpu_util": {
            "median": round(statistics.median(utils), 1) if utils else 0,
            "mean": round(statistics.mean(utils), 1) if utils else 0,
            "p90": round(sorted(utils)[int(len(utils) * 0.9)], 1) if utils else 0,
            "max": max(utils) if utils else 0,
            "idle_pct_of_samples": round(
                100.0 * sum(1 for u in utils if u <= 5) / len(utils), 1
            ) if utils else 0,
        },
        "vram_mib": {
            "median": int(statistics.median(mems)) if mems else 0,
            "max": max(mems) if mems else 0,
        },
        "requests": len(walls),
        "wall_ms_median": round(statistics.median(walls), 1) if walls else 0,
    }
    print("[gpu] %s -> util median=%.1f%% mean=%.1f%% max=%d%% idle_samples=%.1f%% vram=%dMiB"
          % (label, res["gpu_util"]["median"], res["gpu_util"]["mean"],
             res["gpu_util"]["max"], res["gpu_util"]["idle_pct_of_samples"],
             res["vram_mib"]["median"]), flush=True)
    return res


def main() -> None:
    idle = window("IDLE_30s", 30, with_load=False)
    load = window("LOAD_1HZ_30s", 30, with_load=True)
    summary = {"idle": idle, "load_1hz": load}
    out = CACHE / "ledger_gpu_result.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n[gpu] === SUMMARY ===", flush=True)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
