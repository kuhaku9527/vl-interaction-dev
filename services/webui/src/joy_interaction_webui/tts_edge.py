# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Edge TTS provider (免费、无需 API Key) —— 供本地测试与免费音色使用。

为什么需要（用户拍板 2026-09-19）：
  MiniMax（:8985 voice-clone）需要 ``MINIMAX_API_KEY`` + ``MINIMAX_GROUP_ID``，
  没有 Key 就无法验证整条 TTS 链路。用户要求「先做一些免费的音色，比如 Edge 的
  免费 tts，方便我们后面的测试。等后续有需求之后再接入大厂的 API 和 Key」。

Edge TTS 恰好满足：微软 Edge 的在线朗读服务，**无需注册、无需 Key**，
中文音色 8 个（zh-CN），质量可接受，适合把链路先跑通。

设计边界（刻意保守，避免污染既有生产路径）：
  * **不改** ``/api/tts/synthesize`` 的既有行为（它走 MiniMax WAV，被
    ``llm_reply_audio.js`` / ``live_ui.js`` 依赖）。Edge 走**独立端点**。
  * 返回 **MP3**（edge-tts 原生格式），浏览器 ``<audio>`` 直接可播，
    无需转 WAV（转码会引入 ffmpeg 依赖，得不偿失）。
  * provider 选择留在前端（TTS 卡的 provider 下拉），后端只提供能力。

端点：
  ``GET  /api/tts/voices``   → 列出可用音色（{id,label,gender,locale}）
  ``POST /api/tts/edge``     → 用 Edge 合成，返回 audio/mpeg
"""

import logging
import os

from aiohttp import web

logger = logging.getLogger(__name__)

#: Edge TTS 常用中文音色白名单。
#: 不依赖 ``list_voices()`` 联网拉全量（那有 ~300 个、多数是外语），
#: 这里固定列出中文可用项 + 少量英语，作为**免费测试集**。
#: 用户拍板「这个表单也是固定的，因为免费的好像就那一两家，方便我们测试」。
_EDGE_VOICES: tuple[dict, ...] = (
    {
        "id": "zh-CN-YunxiNeural",
        "label": "云希 · 男声（青年）",
        "gender": "Male",
        "locale": "zh-CN",
    },
    {
        "id": "zh-CN-YunjianNeural",
        "label": "云健 · 男声（浑厚）",
        "gender": "Male",
        "locale": "zh-CN",
    },
    {
        "id": "zh-CN-YunyangNeural",
        "label": "云扬 · 男声（播报）",
        "gender": "Male",
        "locale": "zh-CN",
    },
    {
        "id": "zh-CN-YunxiaNeural",
        "label": "云夏 · 男声（少年）",
        "gender": "Male",
        "locale": "zh-CN",
    },
    {
        "id": "zh-CN-XiaoxiaoNeural",
        "label": "晓晓 · 女声（通用）",
        "gender": "Female",
        "locale": "zh-CN",
    },
    {
        "id": "zh-CN-XiaoyiNeural",
        "label": "晓伊 · 女声（活泼）",
        "gender": "Female",
        "locale": "zh-CN",
    },
    {
        "id": "zh-CN-liaoning-XiaobeiNeural",
        "label": "晓北 · 女声（东北）",
        "gender": "Female",
        "locale": "zh-CN",
    },
    {
        "id": "zh-CN-shaanxi-XiaoniNeural",
        "label": "晓妮 · 女声（陕西）",
        "gender": "Female",
        "locale": "zh-CN",
    },
    {
        "id": "en-US-AriaNeural",
        "label": "Aria · English (US)",
        "gender": "Female",
        "locale": "en-US",
    },
    {"id": "en-US-GuyNeural", "label": "Guy · English (US)", "gender": "Male", "locale": "en-US"},
)

#: 默认音色（男声云希 —— 与 BT-7274 的男声定位接近）
DEFAULT_EDGE_VOICE = "zh-CN-YunxiNeural"

#: 参数边界（**单位不同，实测踩过坑**）：
#:   rate  —— 百分比（Edge 默认 "+0%"），范围 -100..200
#:   pitch —— **Hz 不是百分比**（Edge 默认 "+0Hz"，实测传 "+0%" 会报
#:            ``Invalid pitch '+0%'``；见 communicate.py:334 的签名默认值）
#:   volume—— 百分比（本次未暴露给前端，保留常量备用）
_RATE_MIN, _RATE_MAX = -100, 200  # 百分比
_PITCH_MIN_HZ, _PITCH_MAX_HZ = -100, 100  # Hz
_VOLUME_MIN, _VOLUME_MAX = -100, 100  # 百分比


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _fmt(n: float, unit: str) -> str:
    """格式化为 Edge 需要的带符号串：``+12%`` / ``-5Hz``。"""
    return f"{'+' if n >= 0 else '-'}{int(abs(n))}{unit}"


def _normalize_percent(raw, lo: int, hi: int, default: int = 0) -> str:
    """语速百分比 → Edge 需要的 ``+N%`` / ``-N%``。"""
    try:
        n = float(raw)
    except (TypeError, ValueError):
        n = float(default)
    return _fmt(_clamp(n, lo, hi), "%")


def _normalize_hz(raw, lo: int, hi: int, default: int = 0) -> str:
    """音调 → Edge 需要的 ``+NHz`` / ``-NHz``。

    ★ 单位是 **Hz**，不是百分比 —— 传 "+0%" 会被 Edge 拒绝（实测）。
    """
    try:
        n = float(raw)
    except (TypeError, ValueError):
        n = float(default)
    return _fmt(_clamp(n, lo, hi), "Hz")


def _edge_available() -> bool:
    """edge-tts 是否可用（缺失时给前端明确原因，而不是 500）。"""
    try:
        import edge_tts  # noqa: F401
    except Exception:
        return False
    return True


async def _tts_voices_handler(request):
    """GET /api/tts/voices —— 返回可选音色列表 + provider 能力描述。

    provider 能力用 ``supports`` 字段显式声明，供前端按厂商渲染不同控件
    （用户提到的"每个厂商可调的东西不统一"问题的**数据侧**解法）。
    """
    providers = [
        {
            "id": "edge",
            "label": "Edge TTS（免费，无需 Key）",
            "needs_api_key": False,
            "needs_model": False,
            # Edge 是内置通道，不走自建地址 —— 故 Base URL 组也隐藏
            "needs_api_base": False,
            "supports": {"voice": True, "rate": True, "pitch": True, "emotion": False},
            "available": _edge_available(),
            "note": "" if _edge_available() else "未安装 edge-tts：python -m pip install edge-tts",
        },
        {
            "id": "minimax",
            "label": "MiniMax（云端，需 Key）",
            "needs_api_key": True,
            "needs_model": True,
            "needs_api_base": True,
            "supports": {"voice": True, "rate": True, "pitch": True, "emotion": True},
            "available": bool(os.environ.get("MINIMAX_API_KEY")),
            "note": "" if os.environ.get("MINIMAX_API_KEY") else "未配置 MINIMAX_API_KEY",
        },
    ]
    return web.json_response(
        {
            "default_voice": DEFAULT_EDGE_VOICE,
            "providers": providers,
            "voices": {"edge": list(_EDGE_VOICES)},
        }
    )


async def _tts_edge_handler(request):
    """POST /api/tts/edge —— 用 Edge TTS 合成，返回 MP3。

    Body: ``{"text": str, "voice": str?, "rate": int?, "pitch": int?}``
      * ``rate``  / ``pitch`` 为百分比整数（-100..100，rate 上限 200）
    返回 ``audio/mpeg``；400 空文本/参数非法；503 未装 edge-tts；502 合成失败。
    """
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    text = (data.get("text") or "").strip()
    if not text:
        return web.json_response({"error": "text is required"}, status=400)

    if not _edge_available():
        return web.json_response(
            {"error": "edge-tts not installed", "hint": "pip install edge-tts"}, status=503
        )

    import edge_tts

    voice = (data.get("voice") or "").strip() or DEFAULT_EDGE_VOICE
    known = {v["id"] for v in _EDGE_VOICES}
    if voice not in known:
        # 未知音色不直接报错 —— 记日志后放行（Edge 侧会拒绝，反馈更真实）
        logger.info("tts_edge: voice %r not in curated list; forwarding anyway", voice)

    rate = _normalize_percent(data.get("rate"), _RATE_MIN, _RATE_MAX, 0)
    pitch = _normalize_hz(data.get("pitch"), _PITCH_MIN_HZ, _PITCH_MAX_HZ, 0)

    try:
        communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
        buf = bytearray()
        async for chunk in communicate.stream():
            if chunk.get("type") == "audio":
                buf.extend(chunk["data"])
    except Exception as exc:
        logger.warning("tts_edge: synthesis failed: %s", exc)
        return web.json_response(
            {"error": "edge synthesis failed", "reason": str(exc)[:160]}, status=502
        )

    if not buf:
        return web.json_response({"error": "edge returned empty audio"}, status=502)

    logger.info("tts_edge: %d bytes, voice=%s rate=%s pitch=%s", len(buf), voice, rate, pitch)
    return web.Response(body=bytes(buf), content_type="audio/mpeg")


def register_tts_edge_routes(app: web.Application) -> None:
    """把 Edge TTS 端点挂到 app 上（由 server.py 调用）。"""
    app.router.add_get("/api/tts/voices", _tts_voices_handler)
    app.router.add_post("/api/tts/edge", _tts_edge_handler)
