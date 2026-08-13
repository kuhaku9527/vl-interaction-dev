# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""
WebRTC Joy VL Interaction Server
Main server that handles WebRTC connections and serves the web interface
"""

import asyncio
import json
import logging
import os as _os_for_accesslog  # access log path
import time as _time_for_accesslog

_access_logger = logging.getLogger("joyai.access")
if not _access_logger.handlers:
    # Default: write one JSONL per line to logs/webui-access-<UTC>.log (rotated daily
    # by launcher path). No-op if logs/ cannot be created. Format: one JSON
    # object per request with ts, method, path, status, latency_ms.
    _log_dir = _os_for_accesslog.path.join(
        _os_for_accesslog.path.dirname(_os_for_accesslog.path.abspath(__file__)),
        "..",
        "..",
        "..",
        "logs",
    )
    # Health/heartbeat polling paths (status pills polled every 1-5s, liveness
    # probes) are logged at DEBUG instead of INFO so normal runs show only real
    # events. Set JOYAI_LOG_LEVEL=DEBUG to restore them for troubleshooting.
    _access_log_level = getattr(
        logging, _os_for_accesslog.environ.get("JOYAI_LOG_LEVEL", "INFO").upper(), logging.INFO
    )
    try:
        _os_for_accesslog.makedirs(_log_dir, exist_ok=True)
        _ts = _os_for_accesslog.path.join(
            _log_dir,
            (
                "webui-access-"
                + _time_for_accesslog.strftime("%Y-%m-%d", _time_for_accesslog.gmtime())
                + ".log"
            ),
        )
        _fh = logging.FileHandler(_ts, encoding="utf-8")
        _fh.setFormatter(logging.Formatter("%(message)s"))
        # The handler gates what lands on disk: INFO by default, DEBUG when
        # JOYAI_LOG_LEVEL=DEBUG (heartbeat traffic becomes visible).
        _fh.setLevel(_access_log_level)
        _access_logger.addHandler(_fh)
        # The logger itself must pass DEBUG so the access middleware can route
        # heartbeat polls to debug(); the FileHandler level is the real gate.
        _access_logger.setLevel(logging.DEBUG)
    except OSError:
        pass  # access log is best-effort; do not break the webui if logs/ is unwritable


#: Exact heartbeat/health endpoints (browser status pills, liveness probes).
_HEARTBEAT_EXACT_PATHS: frozenset[str] = frozenset(
    {
        "/health",
        "/v1/models",
    }
)


def _is_heartbeat_path(path: str) -> bool:
    """True when ``path`` is a health/status polling endpoint (heartbeat).

    Heartbeat traffic (status pills polled every 1-5s, liveness probes) is
    routed to DEBUG instead of INFO so ``webui.err.log`` shows real events.
    Failures (status >= 400) are still logged at INFO so errors stay visible
    without enabling DEBUG.
    """
    base = path.split("?", 1)[0]
    if base in _HEARTBEAT_EXACT_PATHS:
        return True
    if base.startswith("/api/") and (
        base.endswith("/status")
        or base.endswith("/extended-status")
        or base.endswith("/health")
        or base.endswith("/models")
    ):
        return True
    return False


import datetime  # noqa: E402
import os  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402

# Fix double-module-load bug: when run via `python -m joy_interaction_webui.server`,
# Python executes this file as __main__ and *also* registers a separate module
# instance under the dotted name when jarvis_session.py does
# `from .server import notify_session_llm_reply`. Those two instances have
# *independent globals* (separate session_websockets, websockets, ...), so
# websocket_handler writes to one dict while notify_session_llm_reply reads
# from the other, silently dropping every LLM reply.
# Aliasing __main__ under the dotted name makes downstream `from .server import ...`
# resolve to the SAME module instance. See doc/subsystems/jarvis-mode.md changelog v3.22.
if __name__ == "__main__":
    sys.modules.setdefault("joy_interaction_webui.server", sys.modules["__main__"])
from collections import defaultdict  # noqa: E402

import aiohttp  # noqa: E402
from aiohttp import web  # noqa: E402
from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription  # noqa: E402
from aiortc.contrib.media import MediaRelay  # noqa: E402

from . import asr as asr_module  # noqa: E402
from .asr import setup_asr_routes  # noqa: E402

# Facade re-exports from asr_bridge (internal ASR bridge subprocess manager).
from .asr_bridge import ASR_BRIDGE_HTTP as ASR_BRIDGE_HTTP  # noqa: E402
from .asr_bridge import ASR_BRIDGE_PORT as ASR_BRIDGE_PORT  # noqa: E402
from .asr_bridge import ASR_BRIDGE_WS as ASR_BRIDGE_WS  # noqa: E402
from .asr_bridge import _asr_bridge_ensure as _asr_bridge_ensure  # noqa: E402
from .asr_bridge import _asr_bridge_stop as _asr_bridge_stop  # noqa: E402
from .asr_bridge import _asr_bridge_sync as _asr_bridge_sync  # noqa: E402
from .asr_bridge import _asr_bridge_venv as _asr_bridge_venv  # noqa: E402
from .asr_bridge import (  # noqa: E402
    _asr_bridge_wait_ready as _asr_bridge_wait_ready,
)
from .audio_processor import MicAudioTrack  # noqa: E402
from .background_model import BackgroundModelService  # noqa: E402
from .jarvis_mode import (  # noqa: E402
    JarvisState,
)
from .jarvis_mode import (  # noqa: E402
    asr_model_display_name as asr_model_display_name,
)
from .jarvis_routes import bind_audio, setup_jarvis_routes  # noqa: E402
from .jarvis_session import JarvisSessionManager  # noqa: E402
from .live_routes import bind_live_audio_for_peer, setup_live_routes  # noqa: E402
from .local_file_server import setup_local_file_routes  # noqa: E402

# Facade re-exports from service_probe (reachability probes + status endpoints).
from .service_probe import _LLM_PROBE_CACHE as _LLM_PROBE_CACHE  # noqa: E402
from .service_probe import _LLM_PROBE_TTL_S as _LLM_PROBE_TTL_S  # noqa: E402
from .service_probe import _now as _now  # noqa: E402
from .service_probe import _probe_asr as _probe_asr  # noqa: E402
from .service_probe import _probe_kws as _probe_kws  # noqa: E402
from .service_probe import _probe_llm as _probe_llm  # noqa: E402
from .service_probe import _probe_summary as _probe_summary  # noqa: E402
from .service_probe import _probe_tts as _probe_tts  # noqa: E402
from .service_probe import (  # noqa: E402
    _resolve_service_targets as _resolve_service_targets,
)
from .service_probe import llm_status as llm_status  # noqa: E402
from .service_probe import tts_health as tts_health  # noqa: E402
from .services_config import _SERVICES_CONFIG_DEFAULTS as _SERVICES_CONFIG_DEFAULTS  # noqa: E402
from .services_config import _SERVICES_CONFIG_PATH as _SERVICES_CONFIG_PATH  # noqa: E402

# Facade re-exports from services_config (live services config + persistence).
from .services_config import (  # noqa: E402
    _default_services_config_path as _default_services_config_path,
)
from .services_config import _log_config_change as _log_config_change  # noqa: E402
from .services_config import (  # noqa: E402
    _merge_services_config_file as _merge_services_config_file,
)
from .services_config import _persist_services_config as _persist_services_config  # noqa: E402
from .services_config import _probe_result_ok as _probe_result_ok  # noqa: E402
from .services_config import _probe_result_reason as _probe_result_reason  # noqa: E402
from .services_config import _probe_slot as _probe_slot  # noqa: E402
from .services_config import (  # noqa: E402
    _reload_services_config_from_file as _reload_services_config_from_file,
)
from .services_config import _services_config as _services_config  # noqa: E402
from .services_config import _validate_and_apply_slot as _validate_and_apply_slot  # noqa: E402
from .services_config import _validate_api_base as _validate_api_base  # noqa: E402
from .tts import setup_tts_routes  # noqa: E402
from .tts_endpoint import _tts_synthesize_handler as _tts_synthesize_handler  # noqa: E402
from .tts_endpoint import _wav_chunk_header as _wav_chunk_header  # noqa: E402

# Facade re-exports from tts_endpoint (POST /api/tts/synthesize).
from .tts_endpoint import (  # noqa: E402
    build_tts_synthesize_payload as build_tts_synthesize_payload,
)
from .vlm_service import VLMService  # noqa: E402

# Facade re-exports from webinfer_proxy (summarizer-route proxy).
from .webinfer_proxy import _webinfer_base_url as _webinfer_base_url  # noqa: E402
from .webinfer_proxy import (  # noqa: E402
    _webinfer_proxy_summarizer_routing as _webinfer_proxy_summarizer_routing,
)
from .webinfer_proxy import (  # noqa: E402
    _webinfer_summarizer_route_handler as _webinfer_summarizer_route_handler,
)

# Facade re-export from ws_handler (the /ws main loop).
from .ws_handler import websocket_handler as websocket_handler  # noqa: E402

# Facade re-exports from ws_notify (the WS push contract layer) — same objects.
from .ws_notify import _spawn_bg as _spawn_bg  # noqa: E402
from .ws_notify import broadcast_text_update as broadcast_text_update  # noqa: E402
from .ws_notify import get_session_callback as get_session_callback  # noqa: E402
from .ws_notify import notify_session_asr_partial as notify_session_asr_partial  # noqa: E402
from .ws_notify import notify_session_json as notify_session_json  # noqa: E402
from .ws_notify import notify_session_llm_reply as notify_session_llm_reply  # noqa: E402
from .ws_notify import (  # noqa: E402
    notify_session_pilot_utterance as notify_session_pilot_utterance,
)
from .ws_notify import notify_session_tts_sentence as notify_session_tts_sentence  # noqa: E402
from .ws_notify import send_to_session as send_to_session  # noqa: E402
from .ws_notify import session_websockets as session_websockets  # noqa: E402
from .ws_notify import websockets as websockets  # noqa: E402

# ws_notify owns the WS push facade (``send_to_session`` / ``notify_session_*``)
# and the per-session WebSocket registries. server re-exports the SAME mutable
# objects above, so mutations through either module reference stay shared (no
# dual-dict bug) and external callers (jarvis_session, tests, routes) keep
# importing from server while the implementation lives in ws_notify.

logging.basicConfig(
    level=getattr(logging, os.environ.get("JOYAI_LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

relay = MediaRelay()
pcs = set()
vlm_service = None
rtsp_tracks = {}

default_vlm_config = {}
sessions = {}
ws_to_session = {}
session_peer_connections = defaultdict(set)


async def llm_message(request):
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)
    session_id = (data.get("session_id") or "").strip()
    text = (data.get("text") or "").strip()
    if not session_id or not text:
        return web.json_response({"error": "missing session_id or text"}, status=400)
    app = request.app
    manager = app.get("jarvis_manager")
    if manager is None:
        return web.json_response({"error": "jarvis_manager not initialised"}, status=503)
    jarvis_session = await manager.create_session(session_id)
    sm = jarvis_session.state_machine
    if sm.state != JarvisState.DIALOG_ACTIVE:
        sm.state = JarvisState.DIALOG_ACTIVE
    try:
        sm._init_asr()
    except Exception as exc:
        logger.debug("LLM-message: ASR init skipped (%s)", exc)
    # v3.35: optional multimodal frame from the browser paper-plane. When
    # present, jarvis_mode._send_to_llm shapes the user message as a
    # content array (text + image_url) so 7060 llama.cpp (with --mmproj)
    # can describe what is currently on the captured screen.
    image_b64 = data.get("image_b64")
    if isinstance(image_b64, str):
        image_b64 = image_b64.strip() or None
    else:
        image_b64 = None
    # Cap payload at ~3 MB base64 to keep a single request bounded.
    if image_b64 and len(image_b64) > 3 * 1024 * 1024:
        logger.warning("LLM-message: image_b64 too large (%d bytes), dropped", len(image_b64))
        image_b64 = None
    task = asyncio.create_task(
        sm._send_to_llm(text, stream_tts=False, image_b64=image_b64, interaction_mode="call")
    )
    app.setdefault("_llm_tasks", set()).add(task)
    task.add_done_callback(app["_llm_tasks"].discard)
    return web.json_response(
        {
            "session_id": session_id,
            "queued": True,
            "text_chars": len(text),
            "image_attached": bool(image_b64),
        }
    )


def get_or_create_session(session_id):
    api_base = default_vlm_config.get("api_base", "http://127.0.0.1:8070/v1")
    model_name = default_vlm_config.get("model", "streaming-infer-adapter")
    prompt = default_vlm_config.get("prompt")
    vlm = VLMService(api_base=api_base, model=model_name, prompt=prompt)
    sessions[session_id] = {
        "vlm_service": vlm,
        "background_service": BackgroundModelService(
            session_id=session_id,
            notify_callback=lambda payload, sid=session_id: notify_session_json(sid, payload),
            summarizer_api_base=api_base,
        ),
        "show_request_payload": False,
    }
    logger.info("Created new session: %s", session_id)
    return sessions[session_id]


async def session_cleanup(request):
    session_id = request.query.get("session_id", "").strip()
    if not session_id:
        return web.json_response({"error": "missing session_id"}, status=400)
    logger.info("[%s] Cleaning up session", session_id)
    session_sockets = list(session_websockets.pop(session_id, set()))
    for ws in session_sockets:
        try:
            await ws.close()
        except Exception as e:
            logger.warning("[%s] Error closing websocket: %s", session_id, e)
        finally:
            websockets.discard(ws)
            ws_to_session.pop(ws, None)
    if session_id in rtsp_tracks:
        rtsp_track, _processor_track, frame_task = rtsp_tracks.pop(session_id)
        try:
            rtsp_track.stop()
        except Exception as e:
            logger.warning("[%s] Error stopping RTSP track: %s", session_id, e)
        try:
            await frame_task
        except Exception as e:
            logger.warning("[%s] Frame task error: %s", session_id, e)
    pcs_for_session = list(session_peer_connections.pop(session_id, set()))
    for pc in pcs_for_session:
        try:
            await pc.close()
        except Exception as e:
            logger.warning("[%s] Error closing peer connection: %s", session_id, e)
        finally:
            pcs.discard(pc)
    cancelled_vlm_tasks = 0
    cancelled_background_tasks = 0
    session = sessions.pop(session_id, None)
    if session:
        vlm = session.get("vlm_service")
        if vlm:
            try:
                tasks = getattr(vlm, "tasks", set())
                cancelled_vlm_tasks = len(tasks)
                for task in tasks:
                    task.cancel()
            except Exception as e:
                logger.warning("[%s] Error cancelling VLM tasks: %s", session_id, e)
        bg_svc = session.get("background_service")
        if bg_svc:
            try:
                await bg_svc.close(cancel_requests=False)
            except Exception as e:
                logger.warning("[%s] Error closing background service: %s", session_id, e)
    logger.info("[%s] Session cleanup complete", session_id)
    return web.json_response(
        {
            "session_id": session_id,
            "removed": bool(session),
            "websockets_closed": len(session_sockets),
            "peer_connections_closed": len(pcs_for_session),
            "cancelled_vlm_tasks": cancelled_vlm_tasks,
            "cancelled_background_tasks": cancelled_background_tasks,
        }
    )


async def _drain_mic_audio_track(mic_track, session_id):
    """Continuously consume browser mic frames and feed Jarvis.

    aiortc remote tracks only produce frames when something awaits recv().
    MicAudioTrack.recv() does the resample + Jarvis feed, so this task is the
    bridge that makes the always-on KWS listener real.
    """
    try:
        while True:
            await mic_track.recv()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.info("Jarvis mic audio consumer ended for %s: %s", session_id, exc)
    finally:
        try:
            mic_track.stop()
        except Exception as exc:
            logger.warning("mic track stop failed for %s: %s", session_id, exc)


def _start_mic_audio_consumer(mic_track, session_id):
    return asyncio.create_task(_drain_mic_audio_track(mic_track, session_id))


async def bind_jarvis_audio_for_peer(pc, session_id, manager):
    """Wire a WebRTC peer connection into the Jarvis listening chain."""
    session = await manager.create_session(session_id)
    speaker_track = bind_audio(session_id, manager)
    pc.addTrack(speaker_track)
    mic_tasks = set()

    @pc.on("track")
    def on_track(track):
        if getattr(track, "kind", None) != "audio":
            return
        mic_track = MicAudioTrack(track, session)
        task = _start_mic_audio_consumer(mic_track, session_id)
        mic_tasks.add(task)
        task.add_done_callback(mic_tasks.discard)
        logger.info("Jarvis mic track bound for session %s", session_id)

    return {"session": session, "speaker_track": speaker_track, "mic_tasks": mic_tasks}


def _offer_has_jarvis_audio(params):
    if params.get("jarvis_audio") is True:
        return True
    sdp = params.get("sdp") or ""
    return "m=audio" in sdp


async def offer(request):
    params = await request.json()
    offer_sdp = RTCSessionDescription(sdp=params["sdp"], type=params["type"])
    session_id = params.get("session_id", "default")
    pc = RTCPeerConnection(configuration=RTCConfiguration(iceServers=[]))
    pcs.add(pc)
    session_peer_connections[session_id].add(pc)
    if params.get("live_audio") is True:
        # Phase C live mode: independent listening chain (免唤醒词常驻监听).
        # Must be checked BEFORE the jarvis branch because the live offer also
        # carries an m=audio SDP line (which _offer_has_jarvis_audio would
        # otherwise treat as a jarvis offer).
        manager = request.app.get("jarvis_manager")
        if manager is None:
            return web.json_response({"error": "jarvis_manager not initialised"}, status=503)
        await bind_live_audio_for_peer(pc, session_id, manager)
    elif _offer_has_jarvis_audio(params):
        manager = request.app.get("jarvis_manager")
        if manager is None:
            return web.json_response({"error": "jarvis_manager not initialised"}, status=503)
        await bind_jarvis_audio_for_peer(pc, session_id, manager)
    await pc.setRemoteDescription(offer_sdp)
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)
    return web.Response(
        content_type="application/json",
        text=json.dumps(
            {
                "sdp": pc.localDescription.sdp,
                "type": pc.localDescription.type,
                "session_id": session_id,
            }
        ),
    )


async def on_startup(app):
    import asyncio
    import os
    import sys

    here = os.path.dirname(__file__)
    repo_root = os.path.abspath(os.path.join(here, "..", "..", "..", ".."))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    from .jarvis_mode import JarvisConfig

    cfg = JarvisConfig.from_env()
    if os.environ.get("JARVIS_ASR_MODEL_DIR"):
        cfg.asr_model_dir = os.environ["JARVIS_ASR_MODEL_DIR"]
    app["jarvis_manager"] = JarvisSessionManager(config=cfg)

    async def warm_browser_asr():
        try:
            from .asr import _get_inproc_asr

            if asr_module.get_asr_url():
                return
            await asyncio.to_thread(_get_inproc_asr)
            logger.info("Browser ASR in-process fallback warmed")
        except Exception as exc:
            logger.warning("Browser ASR warm-up skipped: %s", exc)

    app["browser_asr_warmup_task"] = asyncio.create_task(warm_browser_asr())
    logger.info(
        "Jarvis session manager initialised (KWS=%s, ASR=%s)", cfg.kws_model_dir, cfg.asr_model_dir
    )


async def on_shutdown(app):
    for ws in list(websockets):
        try:
            await ws.close()
        except Exception as exc:
            logger.warning("error closing websocket during shutdown: %s", exc)
    for _session_id, session in list(sessions.items()):
        bg_svc = session.get("background_service")
        if bg_svc:
            try:
                await bg_svc.close(cancel_requests=False)
            except Exception as exc:
                logger.warning("error closing background service during shutdown: %s", exc)
    for pc in list(pcs):
        try:
            await pc.close()
        except Exception as exc:
            logger.warning("error closing peer connection during shutdown: %s", exc)


_last_asr_propagated: dict = {
    "api_base": _services_config.get("asr", {}).get("api_base", ""),
    "api_key": _services_config.get("asr", {}).get("api_key", ""),
    "model": _services_config.get("asr", {}).get("model", ""),
}


async def _services_config_handler(request):
    if request.method == "GET":
        return web.json_response(dict(_services_config))
    if request.method == "PUT":
        try:
            payload = await request.json()
        except Exception as exc:
            return web.json_response({"error": "bad json: %s" % exc}, status=400)
        if not isinstance(payload, dict):
            return web.json_response({"error": "payload must be a JSON object"}, status=400)

        loop = asyncio.get_running_loop()
        invalid: list[dict] = []
        applied_any = False
        for slot in ("llm", "summary", "tts", "asr"):
            incoming = payload.get(slot)
            if not isinstance(incoming, dict):
                continue
            entry, applied = await _validate_and_apply_slot(slot, incoming, loop)
            if entry is not None:
                invalid.append(entry)
            if applied:
                applied_any = True

        # Persist + propagate only when at least one valid change landed.
        # Invalid slots were rejected above (never persisted) — the validation
        # gate guarantees we never silently store an invalid config.
        if applied_any:
            try:
                _persist_services_config()
            except OSError as exc:
                logger.error("failed to persist services config: %s", exc)
                return web.json_response(
                    {
                        "error": "persist failed: %s" % exc,
                        "slot": None,
                        "field": None,
                        "reason": str(exc)[:200],
                    },
                    status=500,
                )
            await _propagate_services_to_runtime()

        if invalid:
            # 约法三章②: invalid config MUST surface an explicit 4xx, never a
            # silent 200. Valid slots (if any) were already applied + persisted.
            first = invalid[0]
            logger.warning(
                "PUT /api/services/config rejected slot=%s field=%s: %s",
                first["slot"],
                first["field"],
                first["reason"],
            )
            return web.json_response(
                {
                    "error": first["error"],
                    "slot": first["slot"],
                    "field": first["field"],
                    "reason": first["reason"],
                },
                status=first["status"],
            )
        return web.json_response(dict(_services_config))
    return web.json_response({"error": "method not allowed"}, status=405)


async def _services_status_handler(request):
    """Normalize the 4 probe results into {ok, reason, endpoint} so the
    UI can read a single shape (item.ok ? "OK" : "ERR", reason tooltip).
    """
    llm_cfg = _services_config.get("llm", {})
    summary_cfg = _services_config.get("summary", {})
    tts_cfg = _services_config.get("tts", {})
    asr_cfg = _services_config.get("asr", {})
    tts_url = tts_cfg.get("api_base") or os.environ.get(
        "JARVIS_TTS_API_URL", "http://127.0.0.1:8985/v1/synthesize"
    )
    # Each probe uses sync httpx with a 2-3s timeout; running them inline
    # would block the aiohttp event loop for up to ~9s. Dispatch them to
    # the default executor and gather so the worst case is the slowest probe.
    loop = asyncio.get_running_loop()
    llm_future = loop.run_in_executor(
        None, _probe_llm, llm_cfg.get("api_base", "http://127.0.0.1:8070/v1")
    )
    summary_future = loop.run_in_executor(None, _probe_summary, summary_cfg)
    tts_future = loop.run_in_executor(None, _probe_tts, tts_url)
    asr_future = loop.run_in_executor(None, _probe_asr, asr_cfg)
    llm_raw, summary_raw, tts_raw, asr_raw = await asyncio.gather(
        llm_future, summary_future, tts_future, asr_future
    )
    return web.json_response(
        {
            "llm": {
                "ok": llm_raw.get("status") == "ok",
                "reason": llm_raw.get("reason", ""),
                "endpoint": llm_cfg.get("api_base", "") + "/models",
            },
            "summary": summary_raw,
            "tts": {
                "ok": tts_raw.get("status") == "ok",
                "reason": tts_raw.get("reason", ""),
                "endpoint": tts_raw.get("endpoint", tts_url),
            },
            "asr": asr_raw,
        }
    )


# -- [Local Wiki] frontend gateway endpoints (ADR-0012, task F4) ----------
# The webui is the single SPA entry point. It proxies the whitelisted /v1/*
# wiki endpoints to the memory-store service. Provider health (B3) and
# network settings (B4) are owned by the backend (#36); this gateway only
# forwards the F4 knowledge-base surface (namespaces / sync / ingest).

# v0.3 (2026-07-29): in-code default flipped from 8996 (empty shell) to 8997
# (real bge-m3 backend, D-L4-001). JOYAI_MEMORY_STORE_URL env still wins, so the
# run-windows.ps1 launcher can override per-deploy. Operators hitting the legacy
# 8996 shell (e.g. a sandboxed dev box) only need to set the env explicitly.
MEMORY_STORE_URL = os.environ.get("JOYAI_MEMORY_STORE_URL", "http://127.0.0.1:8997").rstrip("/")

# Issue #43 (final piece): persist browser-reported screen-frame send→render
# latency samples to a server-side JSONL ring file so the data survives a page
# refresh / webui process restart. Previously the samples only lived in the
# browser console + an in-memory ring (lost on refresh).
SCREEN_LATENCY_LOG = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..",
    "..",
    "..",
    "..",
    "logs",
    "screen_latency.jsonl",
)
SCREEN_LATENCY_RING_CAP = 2000
# Ensure the repo-root logs/ dir exists at startup. A genuinely unwritable
# logs/ is a real deployment error (project standard: raise, do not silently
# degrade), so this is intentionally run WITHOUT a try/except guard.
os.makedirs(os.path.dirname(SCREEN_LATENCY_LOG), exist_ok=True)


def _append_screen_latency(record: dict) -> None:
    """Append one screen-latency sample as a JSON line and trim the ring.

    Parameters
    ----------
    record: dict
        Serializable sample (seq, send_to_render_ms, ts, text_len, received_at).

    Notes
    -----
    When the file exceeds ``SCREEN_LATENCY_RING_CAP`` lines it is rewritten
    keeping only the most recent entries. Volume is low and the webui runs
    single-process, so this simple full-rewrite trim is acceptable.
    """
    os.makedirs(os.path.dirname(SCREEN_LATENCY_LOG), exist_ok=True)
    with open(SCREEN_LATENCY_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    # lightweight trim (low volume; single-process aiohttp is fine)
    try:
        with open(SCREEN_LATENCY_LOG, encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > SCREEN_LATENCY_RING_CAP:
            with open(SCREEN_LATENCY_LOG, "w", encoding="utf-8") as f:
                f.writelines(lines[-SCREEN_LATENCY_RING_CAP:])
    except OSError as exc:
        logger.error("[screen-latency] trim failed: %s", exc)


async def _screen_latency_handler(request: web.Request) -> web.Response:
    """Persist one browser screen-frame send→render latency sample.

    Expects a JSON body with ``send_to_render_ms`` (number) and ``seq``
    (required). On success a JSONL record is appended to ``SCREEN_LATENCY_LOG``
    and ``204`` is returned. Malformed / invalid input returns ``4xx`` with a
    warning log; a write failure returns ``500``.
    """
    try:
        payload = await request.json()
    except Exception:
        logger.warning("[screen-latency] bad JSON body")
        return web.json_response({"ok": False, "error": "bad_json"}, status=400)
    s2r = payload.get("send_to_render_ms")
    seq = payload.get("seq")
    if not isinstance(s2r, (int, float)) or seq is None:
        logger.warning("[screen-latency] missing/invalid fields seq=%r s2r=%r", seq, s2r)
        return web.json_response({"ok": False, "error": "invalid_fields"}, status=400)
    record = {
        "seq": seq,
        "send_to_render_ms": round(float(s2r), 2),
        "ts": payload.get("ts"),
        "text_len": payload.get("text_len"),
        "received_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    try:
        _append_screen_latency(record)
    except OSError as exc:
        logger.error("[screen-latency] append failed: %s", exc)
        return web.json_response({"ok": False, "error": "write_failed"}, status=500)
    logger.debug("[screen-latency] sample seq=%s s2r=%s", seq, record["send_to_render_ms"])
    # RFC 7231: 204 MUST NOT include a message body, so return an empty response.
    return web.Response(status=204)


async def _proxy_to_memory_store(request: web.Request) -> web.Response:
    """Forward whitelisted [Local Wiki] /v1/* endpoints to memory-store.

    Only the three UI-facing wiki endpoints are proxied; memory-store's
    internal /v1/blocks/* surface is intentionally NOT exposed to the SPA.
    """
    target = MEMORY_STORE_URL + request.path
    if request.query_string:
        target += "?" + request.query_string
    try:
        body = await request.read()
        headers = {
            k: v for k, v in request.headers.items() if k.lower() not in ("host", "content-length")
        }
        async with (
            aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session,
            session.request(request.method, target, data=body or None, headers=headers) as resp,
        ):
            resp_body = await resp.read()
            # Strip any "; charset=..." — aiohttp's content_type arg rejects it
            # (it adds charset itself), and FastAPI/memory-store send it.
            ct = resp.headers.get("Content-Type", "application/json")
            ct = ct.split(";", 1)[0].strip() or "application/json"
            return web.Response(
                status=resp.status,
                body=resp_body,
                content_type=ct,
            )
    except Exception as exc:
        logger.warning("memory-store proxy %s failed: %s", request.path, exc)
        return web.json_response(
            {"error": "memory-store unreachable", "reason": str(exc)[:160]}, status=502
        )


async def extended_status(request: web.Request) -> web.Response:
    """Aggregate Memory-store + [Local Wiki] status for the header badges (#46).

    Three visual states per service:
      - enabled=False            -> gray  "未启用"  (opt-in flag off)
      - enabled=True & !reachable -> gray  "离线"    (probe failed)
      - enabled=True & reachable & ok=False -> red "异常" (health error)
      - enabled=True & reachable & ok=True  -> green "在线"
    Wiki recall (``WIKI_RECALL_ENABLED``) is configured in webinfer, but its
    corpus backend IS memory-store, so wiki reachability/health reuses the same
    probe. Probe failures are reported explicitly (never a silent 500).
    """
    memory_enabled = os.environ.get("JOYAI_ENABLE_MEMORY_STORE") == "1"
    wiki_enabled = os.environ.get("WIKI_RECALL_ENABLED") == "1"
    ok: bool | None = None
    reachable = False
    reason = ""
    latency_ms = None
    try:
        async with (
            aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2)) as session,
            session.get(MEMORY_STORE_URL + "/health") as resp,
        ):
            if resp.status == 200:
                data = await resp.json()
                ok = bool(data.get("ok"))
                reachable = True
                latency_ms = data.get("latency_ms")
                if not ok:
                    reason = str(data.get("error") or data.get("hint") or "health reported not ok")
            else:
                reason = f"memory-store /health HTTP {resp.status}"
    except Exception as exc:
        reachable = False
        ok = None
        reason = str(exc)
        logger.warning("extended_status: memory-store probe failed: %s", exc)
    payload = {
        "memory": {
            "enabled": memory_enabled,
            "ok": ok,
            "reachable": reachable,
            "reason": reason,
            "latency_ms": latency_ms,
        },
        "wiki": {
            "enabled": wiki_enabled,
            "ok": ok,
            "reachable": reachable,
            "reason": reason,
            "latency_ms": latency_ms,
        },
    }
    return web.json_response(payload)


async def _ingest_text_handler(request: web.Request) -> web.Response:
    """POST /v1/external/ingest-text (F4 pasted-markdown entry).

    The browser cannot write files, so the webui gateway accepts raw markdown
    text, stages it as a single .md under a temp dir, and forwards to the
    memory-store sync endpoint (the single ingest path). The temp dir is
    removed after the upstream call resolves.
    """
    try:
        payload = await request.json()
    except Exception as exc:
        return web.json_response({"error": "bad json: %s" % exc}, status=400)
    namespace = (payload.get("namespace") or "").strip()
    text = payload.get("text") or ""
    if not namespace:
        return web.json_response({"error": "namespace required"}, status=422)
    if not text.strip():
        return web.json_response({"error": "text required"}, status=422)
    import tempfile

    tmp = tempfile.mkdtemp(prefix="joyai-wiki-")
    md_path = os.path.join(tmp, "paste.md")
    try:
        with open(md_path, "w", encoding="utf-8") as fh:
            fh.write(text)
        async with (
            aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session,
            session.post(
                MEMORY_STORE_URL + "/v1/external/sync",
                json={"namespace": namespace, "dir": tmp, "drop_first": False},
            ) as resp,
        ):
            upstream = await resp.json(content_type=None)
            return web.json_response(upstream, status=resp.status)
    except Exception as exc:
        logger.warning("ingest-text failed: %s", exc)
        return web.json_response(
            {"error": "memory-store unreachable", "reason": str(exc)[:160]}, status=502
        )
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)


async def _propagate_services_to_runtime():
    """Push the saved llm/summary config into live service instances.

    - LLM: update every session VLMService (api_base + model + api_key).
    - Summary: webinfer owns the summarizer; webui cannot reach into it.
      We log the change so the operator can restart webinfer if needed.
    - TTS / ASR: read on demand by JarvisConfig.from_env(); changes take
      effect for the NEXT session that calls from_env().

    Async because the ASR bridge start/stop (_asr_bridge_sync) is a blocking
    subprocess op (up to 15s readiness poll) that must run off the aiohttp
    event loop. Always invoked from the event loop (PUT handler).
    """
    global _last_asr_propagated
    loop = asyncio.get_running_loop()
    try:
        llm_cfg = _services_config.get("llm", {})
        api_base = llm_cfg.get("api_base")
        model = llm_cfg.get("model")
        api_key = llm_cfg.get("api_key")
        if api_base:
            for _sid, sess in sessions.items():
                vlm = sess.get("vlm_service") if isinstance(sess, dict) else None
                if vlm and hasattr(vlm, "update_api_settings"):
                    vlm.update_api_settings(api_base=api_base, api_key=api_key)
                if vlm and model and hasattr(vlm, "set_model"):
                    vlm.set_model(model)
            default_vlm_config["api_base"] = api_base
            if model:
                default_vlm_config["model"] = model
    except Exception as exc:
        logger.warning("propagate llm config: %s", exc)
    try:
        os.environ["JARVIS_TTS_API_URL"] = _services_config.get("tts", {}).get(
            "api_base", ""
        ) or os.environ.get("JARVIS_TTS_API_URL", "http://127.0.0.1:8985/v1/synthesize")
        asr_cfg = _services_config.get("asr", {})
        # Mirror the model into the env for legacy callers. The authoritative
        # source is asr_cfg["model"] (slot["model"] in asr._asr_cfg()); the
        # ASR_MODEL_DIR env var is only a fallback used when the slot is empty.
        if asr_cfg.get("model"):
            os.environ["ASR_MODEL_DIR"] = asr_cfg["model"]
    except Exception as exc:
        logger.warning("propagate tts/asr config: %s", exc)
    # ASR: connect_asr reads the live config on every new browser session, so
    # hot-reload needs no persistent client. We only bump the invalidation epoch
    # when the asr slot actually changed, to avoid needless reconnect logging.
    # No silent local fallback: an invalid url/key still raises on connect.
    try:
        asr_cfg = _services_config.get("asr", {}) or {}
        # Bridge start/stop is a blocking subprocess op (up to 15s readiness
        # poll); run it off the aiohttp event loop. Per code-review BLOCKING fix.
        await loop.run_in_executor(None, _asr_bridge_sync)
        asr_subset = {
            "api_base": asr_cfg.get("api_base", ""),
            "api_key": asr_cfg.get("api_key", ""),
            "model": asr_cfg.get("model", ""),
        }
        if asr_subset != _last_asr_propagated:
            _last_asr_propagated = dict(asr_subset)
            asr_module.invalidate_asr_client()
            logger.info(
                "ASR config propagated (url set=%s, key set=%s); next ASR session reconnects",
                bool(asr_subset["api_base"]),
                bool(asr_subset["api_key"]),
            )
    except Exception as exc:
        logger.warning("propagate asr config: %s", exc)
    summary_cfg = _services_config.get("summary", {})
    if summary_cfg.get("api_base") or summary_cfg.get("model") or summary_cfg.get("api_key"):
        # Fire-and-forget; the PUT /api/services/config caller does not
        # need to wait for webinfer. If webinfer is down, the warning
        # is logged in the proxy and the saved config is still applied.
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None and loop.is_running():
            loop.create_task(_webinfer_proxy_summarizer_routing(summary_cfg))
        # else: no live event loop here (e.g. unit test sync invocation);
        # the next PUT will retry the propagation.


def main():
    import argparse

    parser = argparse.ArgumentParser(description="JoyAI VL Interaction WebUI Server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8099)
    parser.add_argument("--no-ssl", action="store_true")
    parser.add_argument("--model", default="streaming-infer-adapter")
    parser.add_argument("--api-base", default="http://127.0.0.1:8070/v1")
    args = parser.parse_args()

    default_vlm_config.update({"api_base": args.api_base, "model": args.model, "prompt": None})

    @web.middleware
    async def security_headers_middleware(request, handler):
        # Apply defensive HTTP headers to every response (static pages, JSON API,
        # WebSocket upgrade). SRI on the CDN <script>/<link> tags plus this CSP
        # is the primary supply-chain / XSS defense-in-depth for the SPA.
        try:
            response = await handler(request)
        except Exception:
            raise
        if response is not None and getattr(response, "headers", None) is not None:
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://unpkg.com; "
                "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                "img-src 'self' data: blob:; "
                "font-src 'self' data: https://cdn.jsdelivr.net; "
                "media-src 'self' blob: data:; "
                "connect-src 'self' ws: wss: http://127.0.0.1:* https://127.0.0.1:*; "
                "object-src 'none'; "
                "base-uri 'self'; "
                "frame-ancestors 'none'"
            )
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @web.middleware
    async def access_log_middleware(request, handler):
        # One JSONL line per HTTP request. PII: do NOT log message bodies,
        # only the path + method + status + latency. WebSocket upgrades are
        # recorded at the upgrade point (status 101) but not per frame.
        t0 = _time_for_accesslog.perf_counter()
        status = 500
        try:
            response = await handler(request)
            status = response.status if response is not None else 500
            return response
        finally:
            try:
                latency_ms = int((time.perf_counter() - t0) * 1000)
                line = json.dumps(
                    {
                        "ts": _time_for_accesslog.strftime(
                            "%Y-%m-%dT%H:%M:%SZ", _time_for_accesslog.gmtime()
                        ),
                        "method": request.method,
                        "path": request.path,
                        "status": status,
                        "latency_ms": latency_ms,
                    },
                    ensure_ascii=False,
                )
                if _is_heartbeat_path(request.path) and status < 400:
                    # Heartbeat poll succeeded: DEBUG only (visible with
                    # JOYAI_LOG_LEVEL=DEBUG), so INFO logs show real events.
                    _access_logger.debug(line)
                else:
                    _access_logger.info(line)
            except Exception:  # noqa: S110
                pass  # never let logging fail a request

    async def _health_handler(request):
        # Liveness probe for the drift-gate-runtime CI job (Drift Gate v2.1 阶段A)
        return web.json_response({"status": "ok"})

    app = web.Application(middlewares=[access_log_middleware, security_headers_middleware])
    app.router.add_get("/", _index_handler)
    app.router.add_get("/health", _health_handler)
    app.router.add_get("/models", _models_handler)
    app.router.add_get("/detect-services", _detect_services_handler)
    app.router.add_get("/api/services/config", _services_config_handler)
    app.router.add_put("/api/services/config", _services_config_handler)
    app.router.add_get("/api/services/status", _services_status_handler)
    # [Local Wiki] frontend gateway (ADR-0012, tasks F1-F4). Provider health
    # (B3) and network settings (B4) are OWNED by the backend (#36); this
    # gateway only FORWARDS them to memory-store — no business logic here.
    app.router.add_get("/v1/providers/health", _proxy_to_memory_store)
    app.router.add_get("/v1/settings/network", _proxy_to_memory_store)
    app.router.add_put("/v1/settings/network", _proxy_to_memory_store)
    app.router.add_get("/v1/namespaces", _proxy_to_memory_store)
    app.router.add_post("/v1/external/sync", _proxy_to_memory_store)
    app.router.add_post("/v1/external/ingest-text", _ingest_text_handler)
    app.router.add_delete("/v1/namespaces/{namespace}", _proxy_to_memory_store)
    app.router.add_get("/api/webinfer/summarizer/route", _webinfer_summarizer_route_handler)
    app.router.add_post("/api/webinfer/summarizer/route", _webinfer_summarizer_route_handler)

    app.router.add_get("/ws", websocket_handler)
    setup_asr_routes(app)
    setup_tts_routes(app)
    setup_local_file_routes(app)
    setup_jarvis_routes(app)
    setup_live_routes(app)
    app.router.add_post("/offer", offer)
    app.router.add_post("/api/session/cleanup", session_cleanup)
    app.router.add_get("/api/llm/status", llm_status)
    app.router.add_get("/api/services/extended-status", extended_status)
    app.router.add_get("/api/tts/health", tts_health)
    app.router.add_post("/api/llm/message", llm_message)
    app.router.add_post("/api/tts/synthesize", _tts_synthesize_handler)
    # Issue #43: persist browser screen-frame send→render latency samples
    # (POSTed from the SPA) to the server-side JSONL ring file.
    app.router.add_post("/api/screen-latency", _screen_latency_handler)
    app.router.add_post("/api/rtsp/start", _rtsp_start_stub)
    app.router.add_post("/api/rtsp/stop", _rtsp_stop_stub)
    app.router.add_get("/api/rtsp/status", _rtsp_status_stub)
    images_dir = os.path.join(os.path.dirname(__file__), "static", "images")
    images_dir = os.path.abspath(images_dir)
    if os.path.exists(images_dir):
        app.router.add_static("/images", images_dir, name="images")
        logger.info("Serving static files from: %s", images_dir)
    else:
        logger.warning("static images directory missing: %s", images_dir)
    favicon_dir = os.path.join(os.path.dirname(__file__), "static", "favicon")
    favicon_dir = os.path.abspath(favicon_dir)
    if os.path.exists(favicon_dir):
        app.router.add_static("/favicon", favicon_dir, name="favicon")
        logger.info("Serving favicon files from: %s", favicon_dir)
    else:
        logger.warning("favicon directory missing: %s", favicon_dir)
    # v3.27 missed this: serve the entire static dir at "/" so /screen_capture.js
    # (loaded by index.html line 3650 <script src="./screen_capture.js">) returns
    # 200 instead of 404. Without it the browser never registers
    # window.startScreenCapture / stopScreenCapture and the video frame pipeline
    # stays empty. Static add is registered AFTER explicit routes, so
    # /, /ws, /api/* keep their handlers; only undeclared GETs fall through here.
    static_root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "static"))
    if os.path.exists(static_root_dir):
        app.router.add_static(
            "/", static_root_dir, name="static-root", show_index=False, append_version=False
        )
        logger.info("Serving static root files from: %s", static_root_dir)
    else:
        logger.warning("static root directory missing: %s", static_root_dir)
    test_mode = os.environ.get("JOYAI_TEST_MODE") == "1"
    if not test_mode:
        app.on_startup.append(on_startup)
        app.on_shutdown.append(on_shutdown)
    if args.no_ssl:
        logger.warning("SSL disabled with --no-ssl flag")
        ssl_context = None
    else:
        ssl_context = _build_ssl_context()
    logger.info("Initialized VLM service: model=%s, api_base=%s", args.model, args.api_base)
    print("\n======== Running on http://%s:%d ========" % (args.host, args.port))
    print("(Press CTRL+C to quit)")
    web.run_app(app, host=args.host, port=args.port, ssl_context=ssl_context)


def _build_ssl_context():
    import ssl

    cert = os.path.join(os.path.dirname(__file__), "static", "favicon", "cert.pem")
    key = os.path.join(os.path.dirname(__file__), "static", "favicon", "key.pem")
    if os.path.exists(cert) and os.path.exists(key):
        return ssl.create_default_context(ssl.Purpose.CLIENT_AUTH, cafile=cert)
    return None


async def _index_handler(request):
    from pathlib import Path

    static_dir = Path(os.path.dirname(__file__)) / "static"
    idx = static_dir / "index.html"
    if idx.exists():
        return web.Response(text=idx.read_text(encoding="utf-8"), content_type="text/html")
    return web.Response(text="webui running", content_type="text/plain")


async def _models_handler(request):
    return web.json_response({"models": ["joyai-vl-interaction-preview"]})


async def _detect_services_handler(request):
    return web.json_response(
        {
            "llm": {"url": default_vlm_config.get("api_base")},
            "tts": {
                "url": os.environ.get("JARVIS_TTS_API_URL", "http://127.0.0.1:8985/v1/synthesize")
            },
            "kws": {
                "model_dir": os.environ.get(
                    "JARVIS_KWS_MODEL_DIR", "D:/AI/models/sherpa-onnx/models/kws/bt-en"
                )
            },
        }
    )


async def _rtsp_start_stub(request):
    return web.json_response({"error": "RTSP not implemented"}, status=501)


async def _rtsp_stop_stub(request):
    return web.json_response({"error": "RTSP not implemented"}, status=501)


async def _rtsp_status_stub(request):
    return web.json_response({"error": "RTSP not implemented"}, status=501)


if __name__ == "__main__":
    main()
