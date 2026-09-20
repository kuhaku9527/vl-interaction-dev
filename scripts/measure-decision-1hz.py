"""#148 实测：模拟「每秒一帧持续决策」的真实成本。

复现生产 live 链路形态（LIVE_SYSTEM_PROMPT_EN + 单帧图像），
连续 N 轮测温/prompt eval/decode/显存，判断本机能否支撑每秒一次持续推理。

用法: /d/AI/envs/joyai-main/python.exe .cache/ledger/probe.py [轮数]
"""
from __future__ import annotations

import json
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".cache" / "ledger"
BASE = "http://127.0.0.1:7060"
ROUNDS = int(sys.argv[1]) if len(sys.argv) > 1 else 20

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


def vram() -> int:
    out = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
        capture_output=True, text=True,
    ).stdout.strip().splitlines()[0]
    return int(out)


def one_round(idx: int) -> dict:
    body = {
        "model": "joyai",
        "messages": [
            {"role": "system", "content": SYS_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Frame at t=%ds." % idx},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/jpeg;base64," + FRAME_B64},
                    },
                ],
            },
        ],
        "max_tokens": 32,
        "temperature": 0.1,
    }
    req = urllib.request.Request(
        BASE + "/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    t0 = time.perf_counter()
    resp = json.loads(urllib.request.urlopen(req, timeout=180).read())
    wall = (time.perf_counter() - t0) * 1000
    tm = resp.get("timings", {}) or {}
    return {
        "i": idx,
        "wall_ms": round(wall, 1),
        "prompt_ms": round(tm.get("prompt_ms", 0), 1),
        "prompt_n": tm.get("prompt_n", 0),
        "predicted_ms": round(tm.get("predicted_ms", 0), 1),
        "predicted_n": tm.get("predicted_n", 0),
        "vram_mib": vram(),
    }


def main() -> None:
    print("[ledger] frame 768x576, live prompt, rounds=%d" % ROUNDS, flush=True)
    print("[ledger] vram at idle: %d MiB" % vram(), flush=True)
    rows = []
    for i in range(1, ROUNDS + 1):
        r = one_round(i)
        rows.append(r)
        print(
            "[%2d/%d] wall=%7.1fms prompt=%7.1fms(%dtok) decode=%6.1fms(%dtok) vram=%dMiB"
            % (i, ROUNDS, r["wall_ms"], r["prompt_ms"], r["prompt_n"],
               r["predicted_ms"], r["predicted_n"], r["vram_mib"]),
            flush=True,
        )
    walls = [r["wall_ms"] for r in rows]
    prompts = [r["prompt_ms"] for r in rows]
    vrams = [r["vram_mib"] for r in rows]
    summary = {
        "rounds": ROUNDS,
        "wall_ms": {
            "min": round(min(walls), 1),
            "median": round(statistics.median(walls), 1),
            "p90": round(sorted(walls)[int(len(walls) * 0.9) - 1], 1),
            "max": round(max(walls), 1),
        },
        "prompt_ms_median": round(statistics.median(prompts), 1),
        "prompt_tokens": rows[0]["prompt_n"],
        "vram_mib": {"start": vrams[0], "peak": max(vrams), "end": vrams[-1]},
        "rows": rows,
    }
    out = CACHE / "ledger_result.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n[ledger] === SUMMARY ===", flush=True)
    print(json.dumps({k: v for k, v in summary.items() if k != "rows"},
                     ensure_ascii=False, indent=2), flush=True)
    print("[ledger] written to %s" % out, flush=True)


if __name__ == "__main__":
    main()
