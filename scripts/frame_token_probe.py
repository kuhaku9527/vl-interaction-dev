#!/usr/bin/env python3
# ruff: noqa: RUF001, RUF002, RUF003
"""帧的**采集参数与到达服务端的实际值**探针（工单 #163 AC4）。

回答两个问题：
  1. 同一张真实帧在不同分辨率下，**服务端实际收到**多少图像 token？
  2. `max_pixels=1048576` 到底有没有生效？（即高分辨率是否被削）

方法（对照组设计是这里唯一讲究的地方）
------------------------------------
* **每次调用都要全新会话**：webinfer 把每个收到的帧**追加进该会话的 chunk**
  （`_chat_payload_append_turn`）。若会话名固定，第二轮的 prompt 里就带着第一轮的帧，
  `image_tokens` 会**逐轮线性累加**——实测同一分辨率连跑四次得到 399 / 798 / 1197 / 1596，
  即每轮恰好 +399（一帧的量）。那是「历史累积」，不是「这一档的 token 数」。
  ⇒ 会话名带上 `pid + 起始时间戳`，每次运行都是干净会话，且先 `POST /v1/streaming/reset`
  把同名会话彻底清掉（防同一个 pid 内的重跑）。
* 第一档是**极小图基线**（32×32）。不能用「纯文本」当基线：无图的请求会被
  `_forward_text_only` 分流，**根本不过** system prompt 那条路，减出来的差值毫无意义
  （踩过：纯文本基线只有 9 token，减出负数级别的荒谬结果）。
  32×32 走的是**同一条视觉路径、同一份 system prompt**，故差值才是图像本身的贡献。
* 每档之间互不影响（各自独立会话），故五档可以顺序跑。

用法：
    python scripts/frame_token_probe.py <frame-image-path> [--json <out.json>]
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

WEBINFER_CHAT = "http://127.0.0.1:8070/v1/chat/completions"
WEBINFER_RESET = "http://127.0.0.1:8070/v1/streaming/reset"

# (标签, 宽, 高, 说明)
CASES: list[tuple[str, int, int, str]] = [
    ("32x32", 32, 32, "极小图基线：同一视觉路径 + 同一 system prompt，用于扣除固定开销"),
    ("764x540", 764, 540, "getDisplayMedia 实测协商值（ideal 960x540 的实际落点）"),
    ("960x540", 960, 540, "screen_capture.js 请求的 ideal 尺寸"),
    ("1280x720", 1280, 720, "常见 720p"),
    ("2560x1440", 2560, 1440, "1440p：像素数远超 max_pixels=1048576，会被削"),
]

MAX_PIXELS = 1048576  # services/webinfer/app.py `_env_int("MAX_PIXELS", 1048576)`


def _frame_b64(src: str, w: int, h: int) -> str:
    from PIL import Image

    with Image.open(src) as im:
        im = im.convert("RGB")
        if (w, h) != im.size:
            im = im.resize((w, h), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=92)
        return base64.b64encode(buf.getvalue()).decode("ascii")


def _reset_session(session: str) -> dict:
    """清掉该会话（含已累进的帧），确保这一档是干净样本。"""
    body = json.dumps({"user": session}).encode("utf-8")
    req = urllib.request.Request(  # noqa: S310 - 仅本机回环
        WEBINFER_RESET,
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-streaming-session": session,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - 仅本机回环
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - 清会话失败要能被看见，但不该中断测量
        return {"error": f"{type(exc).__name__}: {exc}"}


def _post(content: list, session: str) -> dict:
    body = {
        "model": "streaming-infer-adapter",
        "interaction_mode": "live",
        "messages": [{"role": "user", "content": content}],
        "max_tokens": 64,
        "temperature": 0.7,
    }
    req = urllib.request.Request(  # noqa: S310 - 仅本机回环
        WEBINFER_CHAT,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-streaming-session": session},
        method="POST",
    )
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:  # noqa: S310 - 仅本机回环
            payload = json.loads(resp.read().decode("utf-8"))
            status = resp.status
    except urllib.error.HTTPError as exc:
        payload = json.loads(exc.read().decode("utf-8"))
        status = exc.code
    return {
        "http_status": status,
        "ms": round((time.perf_counter() - t0) * 1000),
        "payload": payload,
    }


def _expected_after_max_pixels(w: int, h: int) -> tuple[int, int]:
    """复算 `io_utils._resize_image_if_needed` 的削法（保持比例、不上采样）。"""
    if w * h <= MAX_PIXELS:
        return w, h
    scale = (MAX_PIXELS / (w * h)) ** 0.5
    return max(1, int(w * scale)), max(1, int(h * scale))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("frame", help="真实帧图片路径（应来自应用自身采集管线）")
    ap.add_argument("--json", default="", help="把结果写入该 JSON 文件（台账证据）")
    args = ap.parse_args()

    if not Path(args.frame).is_file():
        print(f"frame not found: {args.frame}")
        return 2

    # ★ 会话名带 pid + 起始时间戳：webinfer 会把每个帧**追加进会话 chunk**，
    #   会话名固定则第二轮起 prompt 里带着上一轮的帧，image_tokens 逐轮线性累加
    #   （实测同一分辨率连跑四次：399/798/1197/1596，每轮 +399 = 一帧的量）。
    run_tag = f"tokprobe-{os.getpid()}-{int(time.time())}"
    print(f"会话前缀：{run_tag}（每档一个全新会话）")

    rows = []
    baseline_tokens: int | None = None
    for label, w, h, note in CASES:
        session = f"{run_tag}-{label}"
        reset_info = _reset_session(session)
        b64 = _frame_b64(args.frame, w, h)
        out = _post(
            [{"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}],
            session,
        )
        usage = out["payload"].get("usage") or {}
        sh = out["payload"].get("streamingharness") or {}
        pt = usage.get("prompt_tokens")
        if baseline_tokens is None:
            baseline_tokens = pt
        ew, eh = _expected_after_max_pixels(w, h)
        rows.append(
            {
                "label": label,
                "session": session,
                "session_reset": reset_info,
                "requested_px": w * h,
                "requested": f"{w}x{h}",
                "expected_after_max_pixels": f"{ew}x{eh}",
                "note": note,
                "http_status": out["http_status"],
                "ms": out["ms"],
                "b64_chars": len(b64),
                "prompt_tokens_total": pt,
                "image_tokens": (pt - baseline_tokens) if (pt and baseline_tokens) else None,
                "decision": sh.get("decision"),
            }
        )
        time.sleep(1)

    result = {
        "frame": args.frame,
        "max_pixels": MAX_PIXELS,
        "method": (
            "每档一个**全新会话**（会话名含 pid+时间戳，且先 reset）；"
            "32x32 极小图基线（同一视觉路径 + 同一 system prompt）"
        ),
        "run_tag": run_tag,
        "rows": rows,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.json:
        Path(args.json).write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
        )
        print(f"\nwritten: {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
