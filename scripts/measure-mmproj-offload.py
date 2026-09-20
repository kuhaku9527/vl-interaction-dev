"""#146/显存：量化 --no-mmproj-offload 的延迟代价。

背景：调研实测 `--no-mmproj-offload`（视觉编码器留 CPU）能省 ~1,483 MiB，
      但**延迟代价完全未知**。若它把每轮从 320ms 变成 2s，方案即作废。

方法：同一张 768x576 图，分别在两种配置下重复请求，比较
      prompt_ms（含视觉编码 + prefill）与端到端 wall_ms。
      用 llama.cpp 自己的 timings，避免测量口径分歧。

用法:
  python scripts/measure-mmproj-offload.py --port 7060 --label gpu      # GPU 视觉编码
  python scripts/measure-mmproj-offload.py --port 7061 --label cpu      # CPU 视觉编码
"""
from __future__ import annotations

import base64
import io
import json
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "doc" / "research" / "data" / "mmproj_offload_latency.json"

ROUNDS = 12
W, H = 768, 576  # 与既有标定口径一致（实测 448 prompt token）


def vram() -> int:
    out = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
        capture_output=True, text=True,
    ).stdout.strip().splitlines()[0]
    return int(out)


def make_frame(seed: int = 0) -> str:
    """A *distinct* frame per round.

    Production sends a new frame every second, so a cached prompt is not
    representative — repeating one image makes llama.cpp reuse the whole
    prefix and reports ~13ms instead of the real ~370ms. Varying the image
    defeats the prefix cache and mirrors the real workload.
    """
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (W, H), (40, 45, 60))
    d = ImageDraw.Draw(img)
    # Deterministic per-seed variation so runs stay comparable/reproducible.
    ox = (seed * 37) % 200
    oy = (seed * 53) % 120
    d.rectangle([80 + ox, 80 + oy, 420 + ox, 360 + oy], fill=(180, 70, 70))
    d.ellipse([440 - ox, 180 + oy, 660 - ox, 400 + oy], fill=(230, 210, 90))
    d.text((60, 30), "FRAME %04d" % seed, fill=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92)
    return base64.b64encode(buf.getvalue()).decode()


def request(base: str, b64: str, i: int) -> dict:
    body = {
        "model": "joyai",
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": "Describe in one short sentence."},
                {"type": "image_url",
                 "image_url": {"url": "data:image/jpeg;base64," + b64}},
            ],
        }],
        "max_tokens": 24,
        "temperature": 0.0,
    }
    req = urllib.request.Request(
        base + "/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    t0 = time.perf_counter()
    r = json.loads(urllib.request.urlopen(req, timeout=600).read())
    wall = (time.perf_counter() - t0) * 1000
    tm = r.get("timings", {}) or {}
    return {
        "i": i,
        "wall_ms": round(wall, 1),
        "prompt_ms": round(tm.get("prompt_ms", 0), 1),
        "prompt_n": tm.get("prompt_n"),
        "predicted_ms": round(tm.get("predicted_ms", 0), 1),
        "predicted_n": tm.get("predicted_n"),
        "vram_mib": vram(),
    }


def main() -> None:
    port = 7060
    label = "gpu"
    for i, a in enumerate(sys.argv):
        if a == "--port" and i + 1 < len(sys.argv):
            port = int(sys.argv[i + 1])
        if a == "--label" and i + 1 < len(sys.argv):
            label = sys.argv[i + 1]
    base = f"http://127.0.0.1:{port}"

    for _ in range(120):
        try:
            with urllib.request.urlopen(base + "/health", timeout=3) as h:
                if b"ok" in h.read():
                    break
        except Exception:
            time.sleep(2)
    else:
        print("[mmproj] server not ready on", port, flush=True)
        sys.exit(1)

    b64 = make_frame()
    print("[mmproj] label=%s port=%d rounds=%d" % (label, port, ROUNDS), flush=True)
    print("[mmproj] vram at start: %d MiB" % vram(), flush=True)

    rows = []
    for i in range(1, ROUNDS + 1):
        # Distinct frame per round: defeats the prefix cache (a repeated image
        # would report ~13ms instead of the real prefill cost).
        r = request(base, make_frame(i), i)
        rows.append(r)
        print("[%2d/%d] wall=%8.1fms prompt=%8.1fms(%s tok) decode=%7.1fms vram=%dMiB"
              % (i, ROUNDS, r["wall_ms"], r["prompt_ms"], r["prompt_n"],
                 r["predicted_ms"], r["vram_mib"]), flush=True)

    prompts = [r["prompt_ms"] for r in rows[1:]]  # drop round 1 (warm-up)
    walls = [r["wall_ms"] for r in rows[1:]]
    summary = {
        "label": label,
        "port": port,
        "rounds": ROUNDS,
        "prompt_ms": {
            "median": round(statistics.median(prompts), 1),
            "min": round(min(prompts), 1),
            "max": round(max(prompts), 1),
        },
        "wall_ms": {
            "median": round(statistics.median(walls), 1),
            "min": round(min(walls), 1),
            "max": round(max(walls), 1),
        },
        "vram_mib": {
            "start": rows[0]["vram_mib"],
            "end": rows[-1]["vram_mib"],
            "median": int(statistics.median([r["vram_mib"] for r in rows])),
        },
        "prompt_n": rows[0]["prompt_n"],
        "rows": rows,
    }

    out = OUT.parent / ("mmproj_offload_%s.json" % label)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n[mmproj] === SUMMARY (%s) ===" % label, flush=True)
    print(json.dumps({k: v for k, v in summary.items() if k != "rows"},
                     ensure_ascii=False, indent=2), flush=True)
    print("[mmproj] written to %s" % out, flush=True)


if __name__ == "__main__":
    main()
