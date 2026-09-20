"""#145 实测：图像分辨率 -> prompt token 曲线（核实 448 token 的归属）。

背景：调研端点指出 ARCHITECTURE.md 记的「768x576 = 448 token」复算不符，
      448 唯一对应 800x450（纸飞机 targetW=800）。
      该数字已写入 SSOT，必须实测核实。

方法：起 llama-server（独立端口 7062），对多种分辨率发单图请求，
      读返回的 usage.prompt_tokens 与 llama 的 timings。
      同时给出「像素 -> token」的实测曲线，供采集链路决策使用。

用法: python scripts/measure-image-tokens.py
"""
from __future__ import annotations

import base64
import io
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "doc" / "research" / "data" / "image_token_curve.json"
PORT = 7062
BASE = f"http://127.0.0.1:{PORT}"

# (标签, 宽, 高, 与生产链路的对应关系)
CASES: list[tuple[str, int, int, str]] = [
    ("800x450", 800, 450, "纸飞机 captureBtFrameB64 targetW=800 -> 实际产物"),
    ("768x576", 768, 576, "此前 SSOT 记录的 448 token 归属待核"),
    ("960x540", 960, 540, "getDisplayMedia ideal（当前采集端）"),
    ("1280x720", 1280, 720, "建议的采集端目标"),
    ("1365x768", 1365, 768, "max_pixels 削后上限（1080p/1440p 共同落点）"),
    ("1920x1080", 1920, 1080, "1080p 原始"),
    ("2560x1440", 2560, 1440, "1440p 原始"),
]


def make_frame(w: int, h: int) -> str:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (w, h), (40, 45, 60))
    d = ImageDraw.Draw(img)
    d.rectangle([w // 10, h // 10, w // 2, h // 2], fill=(180, 70, 70))
    d.ellipse([w // 2, h // 3, w * 4 // 5, h * 3 // 4], fill=(230, 210, 90))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92)
    return base64.b64encode(buf.getvalue()).decode()


def measure(b64: str) -> dict:
    body = {
        "model": "joyai",
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": "Describe this image in one word."},
                {"type": "image_url",
                 "image_url": {"url": "data:image/jpeg;base64," + b64}},
            ],
        }],
        "max_tokens": 4,
        "temperature": 0.0,
    }
    req = urllib.request.Request(
        BASE + "/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    r = json.loads(urllib.request.urlopen(req, timeout=180).read())
    usage = r.get("usage", {}) or {}
    tm = r.get("timings", {}) or {}
    return {
        "prompt_tokens": usage.get("prompt_tokens"),
        "prompt_ms": round(tm.get("prompt_ms", 0), 1),
        "predicted_n": tm.get("predicted_n"),
    }


def main() -> None:
    # 等就绪
    import time as _t
    for _ in range(90):
        try:
            with urllib.request.urlopen(BASE + "/health", timeout=3) as h:
                if b"ok" in h.read():
                    break
        except Exception:
            _t.sleep(2)
    else:
        print("[tok] server not ready", flush=True)
        sys.exit(1)

    results = []
    print("[tok] === 图像分辨率 -> prompt token ===", flush=True)
    print("%-12s %10s %8s %10s  %s" % ("case", "pixels", "tokens", "ms", "note"), flush=True)
    for label, w, h, note in CASES:
        b64 = make_frame(w, h)
        try:
            m = measure(b64)
        except Exception as e:
            print("[tok] %-12s FAILED: %s" % (label, e), flush=True)
            results.append({"case": label, "w": w, "h": h, "note": note, "error": str(e)})
            continue
        px = w * h
        results.append({
            "case": label, "w": w, "h": h, "pixels": px,
            "note": note, **m,
            "tokens_per_1k_px": round(m["prompt_tokens"] / (px / 1000), 4)
            if m.get("prompt_tokens") else None,
        })
        print("%-12s %10d %8s %10.1f  %s"
              % (label, px, m.get("prompt_tokens"), m.get("prompt_ms", 0), note),
              flush=True)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"cases": results}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print("\n[tok] written to %s" % OUT, flush=True)


if __name__ == "__main__":
    main()
