# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""WebSocket connection / message handling (split out of server.py).

Owns the ``/ws`` main loop (connection setup, message dispatch, teardown).
Cross-module names that stay owned by ``server`` (``ws_to_session``,
``get_or_create_session``, ``asr_model_display_name``) are resolved through
the server facade lazily at call time so the module-load graph stays acyclic
while preserving the exact monkeypatch contract exercised by the test suite
(e.g. ``test_qa_ws_frame_live_forward`` patches ``server.get_or_create_session``
and ``server.asr_model_display_name`` before invoking the handler).
"""

import base64
import io
import json
import logging
import time
import uuid

from aiohttp import web

from .ws_notify import get_session_callback, session_websockets, websockets

logger = logging.getLogger(__name__)


async def websocket_handler(request):
    from . import server as _server

    ws = web.WebSocketResponse()
    await ws.prepare(request)
    session_id = request.query.get("session_id", "").strip() or str(uuid.uuid4())
    _server.ws_to_session[ws] = session_id
    session_websockets[session_id].add(ws)
    websockets.add(ws)
    logger.info(
        "WebSocket client connected. session_id=%s, total clients: %d", session_id, len(websockets)
    )
    session = _server.get_or_create_session(session_id)
    svc = session["vlm_service"]
    # Jarvis session manager (holds the shared JarvisConfig; used for the
    # ASR-promotion runtime toggle and to advertise the ASR model name).
    manager = request.app.get("jarvis_manager")
    bg_svc = session.get("background_service")
    background_service = bg_svc
    try:
        await ws.send_json(
            {
                "type": "status",
                "text": "Connected to server",
                "status": "Ready",
                "session_id": session_id,
            }
        )
        from .video_processor import VideoProcessorTrack as _VPT

        await ws.send_json(
            {
                "type": "server_config",
                "model": svc.model,
                "api_base": svc.api_base,
                "prompt": svc.prompt,
                "process_interval": _VPT.process_interval_seconds,
                "frames_per_batch": _VPT.frames_per_batch,
                "background_model": (
                    background_service.get_config() if background_service else None
                ),
                "asr_promotion_enabled": (
                    bool(manager.config.asr_promotion_enabled) if manager else False
                ),
                "asr_model_name": (
                    _server.asr_model_display_name(manager.config)
                    if manager
                    else "sherpa-onnx local paraformer (unknown)"
                ),
                "session_id": session_id,
            }
        )
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                except json.JSONDecodeError:
                    continue
                try:
                    t = data.get("type")
                    if t == "update_prompt":
                        svc.update_prompt(data.get("prompt", ""))
                        await ws.send_json(
                            {"type": "prompt_updated", "prompt": data.get("prompt", "")}
                        )
                    elif t == "update_model":
                        if svc.set_model(data.get("model", "")):
                            await ws.send_json(
                                {"type": "model_updated", "model": data.get("model", "")}
                            )
                    elif t == "update_process_interval":
                        from .video_processor import VideoProcessorTrack

                        VideoProcessorTrack.process_interval_seconds = float(
                            data.get("process_interval", 1.0)
                        )
                        await ws.send_json(
                            {
                                "type": "processing_updated",
                                "process_interval": VideoProcessorTrack.process_interval_seconds,
                            }
                        )
                    elif t == "update_frames_per_batch":
                        # P1-4 (audit-frontend-2026-08-13): this branch used to
                        # echo the OLD value without ever writing the new one,
                        # so the frontend setting was silently ignored. Write
                        # the requested batch size back to the shared video
                        # pipeline config, then reply with the ACTUAL value
                        # that took effect (invalid input keeps the previous
                        # value instead of silently dropping the reply).
                        from .video_processor import VideoProcessorTrack

                        try:
                            new_batch = max(1, int(data.get("frames_per_batch") or 1))
                        except (TypeError, ValueError):
                            new_batch = VideoProcessorTrack.frames_per_batch
                            logger.warning(
                                "update_frames_per_batch: invalid value %r, keeping %s",
                                data.get("frames_per_batch"),
                                new_batch,
                            )
                        VideoProcessorTrack.frames_per_batch = new_batch
                        await ws.send_json(
                            {
                                "type": "frames_per_batch_updated",
                                "frames_per_batch": VideoProcessorTrack.frames_per_batch,
                            }
                        )
                    elif t == "frame":
                        # Screen capture frames shipped via WebSocket (parallel to WebRTC).
                        # Decode base64 JPEG -> PIL Image -> vlm_service.process_frame, then broadcast the
                        # resulting text exactly like VideoProcessorTrack does for webcam/RTSP streams.
                        payload = data.get("data") or ""
                        if not isinstance(payload, str) or not payload:
                            logger.warning("frame: empty data")
                        else:
                            # Live visual path (spec live-visual-cb.md
                            # §2.4): when a live session is active, forward the
                            # raw frame to its ring buffer in parallel with the
                            # video pipeline below (independent paths).
                            live_session = (
                                manager.get_live_session(session_id)
                                if manager is not None
                                else None
                            )
                            if live_session is not None:
                                try:
                                    live_session.handle_frame(
                                        payload,
                                        float(
                                            data.get("ts")
                                            or data.get("timestamp")
                                            or time.time() * 1000
                                        ),
                                    )
                                except Exception as live_exc:
                                    logger.warning("live frame route failed: %s", live_exc)
                            try:
                                from PIL import Image as _PILImage

                                t_arrive = time.perf_counter()
                                raw = base64.b64decode(payload)
                                img = _PILImage.open(io.BytesIO(raw)).convert("RGB")
                                t_decoded = time.perf_counter()
                                meta = {
                                    "source": data.get("source") or "screen",
                                    "format": data.get("format") or "jpeg",
                                    "width": data.get("width"),
                                    "height": data.get("height"),
                                    "timestamp": data.get("timestamp"),
                                }
                                await svc.process_frame(img, frame_metadata=meta)
                                response, _ = svc.get_current_response()
                                t_processed = time.perf_counter()
                                metrics = svc.get_metrics()
                                if response:
                                    get_session_callback(session_id)(
                                        response, metrics, data.get("frame_seq")
                                    )
                                logger.info(
                                    "latency[transport+infer-screen]: arrive->processed_ms=%.1f decode_ms=%.1f seq=%s",
                                    (t_processed - t_arrive) * 1000,
                                    (t_decoded - t_arrive) * 1000,
                                    data.get("frame_seq"),
                                )
                            except Exception as frame_exc:
                                logger.warning("frame decode/process failed: %s", frame_exc)
                    elif t == "background_request":
                        if background_service and data.get("question"):
                            try:
                                task_id = background_service.handle_background_request(
                                    data["question"], session_id=session_id
                                )
                                await ws.send_json(
                                    {
                                        "type": "background_request_accepted",
                                        "task_id": task_id,
                                        "session_id": session_id,
                                    }
                                )
                            except Exception as exc:
                                await ws.send_json(
                                    {
                                        "type": "background_result_error",
                                        "task_id": "",
                                        "error": str(exc),
                                    }
                                )
                    elif t == "update_asr_promotion":
                        # Runtime toggle for the local paraformer ASR
                        # promotion (recall booster). The frontend sends
                        # {type:"update_asr_promotion", enabled: bool}. The
                        # change propagates to every live Jarvis session via
                        # the shared JarvisConfig (same asyncio event loop).
                        raw = data.get("enabled")
                        if isinstance(raw, str):
                            enabled = raw.strip().lower() in {"1", "true", "yes", "on"}
                        else:
                            enabled = bool(raw)
                        if manager is None:
                            logger.warning("update_asr_promotion: jarvis_manager unavailable")
                            await ws.send_json(
                                {
                                    "type": "asr_promotion_updated",
                                    "enabled": False,
                                    "asr_model_name": "sherpa-onnx local paraformer (unknown)",
                                    "error": "jarvis_manager unavailable",
                                }
                            )
                        else:
                            manager.set_asr_promotion_enabled(enabled)
                            await ws.send_json(
                                {
                                    "type": "asr_promotion_updated",
                                    "enabled": bool(enabled),
                                    "asr_model_name": _server.asr_model_display_name(
                                        manager.config
                                    ),
                                }
                            )
                    elif t == "update_background_config":
                        # P1-5 (audit-frontend-2026-08-13): no backend handler
                        # existed, so the "Enable delegation solver / Frame
                        # multiplier / Max background frames" settings were
                        # silently ignored. Map the fields the frontend sends
                        # (vlm_history.js sendBackgroundConfig) onto the
                        # session's BackgroundModelService and reply with the
                        # effective config. When the service is unavailable the
                        # reply carries an explicit error instead of going
                        # silent.
                        if background_service is None:
                            logger.warning(
                                "update_background_config: background_service unavailable"
                            )
                            await ws.send_json(
                                {
                                    "type": "background_config_updated",
                                    "background_model": None,
                                    "error": "background_service unavailable",
                                }
                            )
                        else:
                            try:
                                config = background_service.update_config(
                                    enabled=bool(data.get("enabled", background_service.enabled)),
                                    frame_multiplier=data.get("frame_multiplier"),
                                    max_frames=data.get("max_frames"),
                                )
                            except Exception as exc:
                                logger.warning(
                                    "update_background_config failed: %s", exc, exc_info=True
                                )
                                await ws.send_json(
                                    {
                                        "type": "background_config_updated",
                                        "background_model": background_service.get_config(),
                                        "error": str(exc),
                                    }
                                )
                            else:
                                await ws.send_json(
                                    {
                                        "type": "background_config_updated",
                                        "background_model": config,
                                    }
                                )
                except Exception as e:
                    logger.error("Error handling client message: %s", e)
            elif msg.type == web.WSMsgType.ERROR:
                logger.error("WebSocket error: %s", ws.exception())
    finally:
        s = session_websockets.get(session_id)
        if s is not None:
            s.discard(ws)
            if not s:
                session_websockets.pop(session_id, None)
        _server.ws_to_session.pop(ws, None)
        websockets.discard(ws)
        logger.info(
            "WebSocket client disconnected. session_id=%s, total clients: %d",
            session_id,
            len(websockets),
        )
    return ws
