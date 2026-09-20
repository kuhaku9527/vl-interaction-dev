"""#145 核实：出厂采集分辨率（888x540）的真实 token 数 + 各档对照。

背景：调研端点报告称「出厂实测 432 visual token」，但 432+16=448 对应的是
      768x576（标定探针），而它自己测的出厂协商分辨率是 888x540。
      这两个数字不能混用 —— 本脚本实测消歧。

方法：直连 llama-server(7062)，对候选分辨率各测一次，读 usage.prompt_tokens。
      同时用 mmproj 的 1024 px/token 理论值对照。

用法: python scripts/measure-capture-baseline-tokens.py
"""
from __future__ import annotations

import base64
import io
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "doc" / "research" / "data" / "capture_baseline_tokens.json"
PORT = 7062
BASE = f"http://127.0.0.1:{PORT}"
PX_PER_TOKEN = 1024  # patch=16, merge=2 -> 32x32 px per visual token

# (标签, 宽, 高, 说明)
CASES: list[tuple[str, int, int, str]] = [
    ("888x540", 888, 540, "★ 出厂 ideal:960x540 实际协商值（16:9 主屏）"),
    ("960x540", 960, 540, "出厂请求值（对照）"),
    ("768x576", 768, 576, "标定探针（已知 448 total）"),
    ("1280x720", 1280, 720, "首选改进候选 C1"),
    ("1365x768", 1365, 768, "max_pixels 削后上限（1080p/1440p 共同落点）"),
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
                {"type": "text", "text": "One word."},
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
    r = json.loads(urllib.request.urlopen(req, timeout=240).read())
    usage = r.get("usage", {}) or {}
    tm = r.get("timings", {}) or {}
    return {
        "prompt_tokens": usage.get("prompt_tokens"),
        "prompt_ms": round(tm.get("prompt_ms", 0), 1),
        "raw_bytes": len(b64) * 3 // 4,
    }


def main() -> None:
    # 等就绪
    for _ in range(90):
        try:
            with urllib.request.urlopen(BASE + "/health", timeout=3) as h:
                if b"ok" in h.read():
                    break
        except Exception:
            time.sleep(2)
    else:
        print("[tok] server not ready", flush=True)
        sys.exit(1)

    TEXT_TOKENS = None  # 由 768x576 标定点反推
    results = []
    print("[tok] === 出厂采集基线的真实 token 数 ===", flush=True)
    print("%-12s %10s %8s %8s %10s  %s"
          % ("case", "pixels", "理论", "实测", "prefill", "note"), flush=True)
    for label, w, h, note in CASES:
        b64 = make_frame(w, h)
        try:
            m = measure(b64)
        except Exception as e:
            print("[tok] %-12s FAILED: %s" % (label, e), flush=True)
            results.append({"case": label, "w": w, "h": h, "error": str(e)})
            continue
        px = w * h
        theory = round(px / PX_PER_TOKEN)
        results.append({
            "case": label, "w": w, "h": h, "pixels": px,
            "theory_visual_tokens": theory,
            "measured_prompt_tokens": m["prompt_tokens"],
            "prompt_ms": m["prompt_ms"],
            "jpeg_b64_bytes": len(b64),
            "raw_jpeg_bytes": m["raw_bytes"],
            "note": note,
        })
        print("%-12s %10d %8d %8s %9.1fms  %s"
              % (label, px, theory, m["prompt_tokens"], m["prompt_ms"], note),
              flush=True)

    # 用 768x576 标定点反推文本 token 数
    cal = next((r for r in results if r.get("case") == "768x576"), None)
    if cal and cal.get("measured_prompt_tokens"):
        TEXT_TOKENS = cal["measured_prompt_tokens"] - round(cal["pixels"] / PX_PER_TOKEN)
        print("\n[tok] 文本部分 token ≈ %d（由 768x576 标定点反推）" % TEXT_TOKENS, flush=True)
        print("[tok] 若按此扣除，各档 visual token 为：", flush=True)
        for r in results:
            if r.get("measured_prompt_tokens"):
                r["visual_tokens"] = r["measured_prompt_tokens"] - TEXT_TOKENS
                print("  %-12s total=%4d  visual=%4d  (理论 %4d)"
                      % (r["case"], r["measured_prompt_tokens"],
                         r["visual_tokens"], r["theory_visual_tokens"]), flush=True)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"text_tokens_est": TEXT_TOKENS, "cases": results},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n[tok] written to %s" % OUT, flush=True)


if __name__ == "__main__":
    main()
