#!/usr/bin/env python
"""端到端实测 Edge TTS 两个端点（2026-09-19）。

为何不用 mock：用户要的是"能跑通链路"，必须证明真的能出音频。
用 aiohttp test 工具起真实 app、发真实 HTTP 请求，并校验返回的 MP3 头。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "webui" / "src"))

from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from joy_interaction_webui.tts_edge import register_tts_edge_routes  # noqa: E402

PASS = 0
FAIL = 0


def check(cond: bool, label: str, detail: str = "") -> None:
    global PASS, FAIL
    print(f"  {'✅' if cond else '❌'} {label}{('  ' + detail) if detail else ''}")
    if cond:
        PASS += 1
    else:
        FAIL += 1


async def main() -> int:
    app = web.Application()
    register_tts_edge_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        # ---- 1. GET /api/tts/voices ----
        print("【1】GET /api/tts/voices")
        r = await client.get("/api/tts/voices")
        check(r.status == 200, "HTTP 200", f"status={r.status}")
        data = await r.json()
        check(isinstance(data.get("voices", {}).get("edge"), list), "返回 edge 音色列表")
        check(len(data["voices"]["edge"]) >= 8, "音色数 ≥8", f"{len(data['voices']['edge'])} 个")
        check(bool(data.get("default_voice")), "含默认音色", str(data.get("default_voice")))
        provs = {p["id"]: p for p in data.get("providers", [])}
        check("edge" in provs, "含 edge provider")
        check(provs.get("edge", {}).get("needs_api_key") is False, "edge 标记为无需 Key")
        check(provs.get("minimax", {}).get("needs_api_key") is True, "minimax 标记为需要 Key")
        # 能力描述（供前端按厂商渲染）
        sup = provs.get("edge", {}).get("supports", {})
        check(sup.get("voice") is True and sup.get("rate") is True and sup.get("pitch") is True,
              "edge supports 声明 voice/rate/pitch")
        check(sup.get("emotion") is False, "edge supports 明确 emotion=False（该厂商不支持）")

        # ---- 2. POST /api/tts/edge 空文本 ----
        print("\n【2】POST /api/tts/edge 边界")
        r = await client.post("/api/tts/edge", json={"text": "   "})
        check(r.status == 400, "空文本 → 400", f"status={r.status}")
        r = await client.post("/api/tts/edge", data=b"not json",
                              headers={"Content-Type": "application/json"})
        check(r.status == 400, "非法 JSON → 400", f"status={r.status}")

        # ---- 3. POST /api/tts/edge 真合成 ----
        print("\n【3】POST /api/tts/edge 真实合成")
        r = await client.post("/api/tts/edge", json={
            "text": "你好，我是 BT-7274，这是一次语音链路测试。",
            "voice": "zh-CN-YunxiNeural",
            "rate": 0,
            "pitch": 0,
        })
        body = await r.read()
        check(r.status == 200, "HTTP 200", f"status={r.status}")
        check(r.headers.get("Content-Type", "").startswith("audio/"), "Content-Type 为 audio",
              r.headers.get("Content-Type", ""))
        check(len(body) > 2000, "音频非空", f"{len(body)} 字节")
        # MP3 校验：ID3 头 或 MPEG 帧同步（0xFF 0xEx/0xFx）
        is_id3 = body[:3] == b"ID3"
        is_mpeg = len(body) > 1 and body[0] == 0xFF and (body[1] & 0xE0) == 0xE0
        check(is_id3 or is_mpeg, "是真 MP3（ID3 或 MPEG 帧同步）",
              f"id3={is_id3} mpeg={is_mpeg} head={body[:4].hex()}")

        # ---- 4. 参数生效（语速变化应产生不同长度/内容）----
        print("\n【4】语速参数生效")
        r2 = await client.post("/api/tts/edge", json={
            "text": "你好，我是 BT-7274，这是一次语音链路测试。",
            "voice": "zh-CN-YunxiNeural", "rate": 100, "pitch": 0,
        })
        body2 = await r2.read()
        check(r2.status == 200 and len(body2) > 0, "rate=+100% 仍能合成", f"{len(body2)} 字节")
        check(body2 != body, "不同语速产出不同音频（参数真的生效）")

        # ---- 5. 未知音色不崩 ----
        print("\n【5】未知音色")
        r3 = await client.post("/api/tts/edge", json={"text": "测试", "voice": "no-such-voice"})
        check(r3.status in (200, 502), "未知音色不 500（200 或 502）", f"status={r3.status}")
    finally:
        await client.close()

    print(f"\n{'=' * 52}\n通过 {PASS} / 失败 {FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
