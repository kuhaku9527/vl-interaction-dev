"""HTTP routes for the live mode (免唤醒词常驻监听, Phase C C.A).

Exposes three endpoints (used by the browser UI to enter/leave the live
session and to inspect its turn state) plus the WebRTC audio binding helper
invoked from ``server.py``'s offer handler:

* ``POST /api/live/start``   — create/restart the live session (prewarms ASR);
* ``POST /api/live/stop``    — tear down the live session;
* ``GET  /api/live/status``  — live turn-state snapshot (listening / speaking
  / replying / interrupted);
* ``POST /api/live/enroll``  — Addressee Detection Phase 1 enrollment
  (start / pcm / finish / cancel sub-actions; guarded by the
  ``JARVIS_ADDRESSEE_DETECTOR_ENABLED`` env gate, fail-open when disabled).

The mic audio chain reuses jarvis's WebRTC link: the browser pushes 16 kHz
mono PCM via ``feed_audio`` (same entry point); live sessions are tracked in
the manager's separate ``_live_sessions`` dict so jarvis and live coexist.
"""

from __future__ import annotations

import asyncio
import base64
import logging

from aiohttp import web

from .jarvis_session import JarvisSessionManager

logger = logging.getLogger("joyai.live.routes")


def setup_live_routes(app: web.Application) -> None:
    """Register the live HTTP routes on the given aiohttp app."""
    app.router.add_post("/api/live/start", live_start)
    app.router.add_post("/api/live/stop", live_stop)
    app.router.add_get("/api/live/status", live_status)
    app.router.add_post("/api/live/enroll", live_enroll)


# ============================================================================
# Handlers
# ============================================================================


async def live_start(request: web.Request) -> web.Response:
    """Create (or reuse) the live session for the webui session."""
    try:
        data = await request.json()
    except Exception:
        data = {}
    session_id = (data.get("session_id") or "").strip()
    if not session_id:
        return web.json_response({"error": "missing session_id"}, status=400)
    manager: JarvisSessionManager = request.app["jarvis_manager"]
    if manager is None:
        return web.json_response({"error": "jarvis_manager not initialised"}, status=503)
    session = await manager.create_session(session_id, mode="live")
    state = session.get_state_for_browser()
    return web.json_response({"session_id": session_id, "started": True, **state})


async def live_stop(request: web.Request) -> web.Response:
    """Stop and remove the live session for the webui session."""
    try:
        data = await request.json()
    except Exception:
        data = {}
    session_id = (data.get("session_id") or "").strip()
    if not session_id:
        return web.json_response({"error": "missing session_id"}, status=400)
    manager: JarvisSessionManager = request.app["jarvis_manager"]
    await manager.remove_live_session(session_id)
    return web.json_response({"session_id": session_id, "stopped": True})


async def live_status(request: web.Request) -> web.Response:
    """Return the live turn-state snapshot for the webui session."""
    session_id = request.query.get("session_id", "").strip()
    if not session_id:
        return web.json_response({"error": "missing session_id"}, status=400)
    manager: JarvisSessionManager = request.app["jarvis_manager"]
    session = manager.get_live_session(session_id)
    if session is None:
        return web.json_response(
            {
                "session_id": session_id,
                "exists": False,
                "mode": "live",
                "turn_state": "idle",
                "listening": False,
                "speaking": False,
                "replying": False,
                "interrupted": False,
            }
        )
    return web.json_response(
        {"session_id": session_id, "exists": True, **session.get_state_for_browser()}
    )


async def live_enroll(request: web.Request) -> web.Response:
    """Addressee Detection Phase 1 enrollment (start / pcm / finish / cancel).

    Contract (spec draft-addressee-detection.md §3.2):
      * ``{action: "start"}``            — enter enroll phase (mic buffers
        segments via the live WebRTC stream, no ASR/dialog);
      * ``{action: "pcm", audio_b64}``   — push one complete recorded utterance
        (16 kHz mono int16 PCM base64) as an enrollment segment;
      * ``{action: "finish"}``           — extract + register the buffered
        segments; returns ``{enrolled: true|false}``;
      * ``{action: "cancel"}``           — abort enrollment.

    When the detector env gate is off the session has no detector and
    ``start`` returns ``{started: false}`` (fail-open; live behavior unchanged).
    """
    try:
        data = await request.json()
    except Exception:
        data = {}
    session_id = (data.get("session_id") or "").strip()
    if not session_id:
        return web.json_response({"error": "missing session_id"}, status=400)
    action = (data.get("action") or "").strip()
    if not action:
        return web.json_response({"error": "missing action"}, status=400)

    manager: JarvisSessionManager = request.app["jarvis_manager"]
    session = manager.get_live_session(session_id)
    if session is None:
        return web.json_response(
            {"error": "live session not found; start live mode first"}, status=404
        )

    try:
        if action == "start":
            ok = session.start_enroll()
            return web.json_response(
                {
                    "session_id": session_id,
                    "action": action,
                    "started": ok,
                    "enroll_phase": bool(getattr(session, "enroll_phase", False)),
                }
            )
        if action == "pcm":
            audio_b64 = data.get("audio_b64") or ""
            if not audio_b64:
                return web.json_response({"error": "missing audio_b64"}, status=400)
            try:
                pcm = base64.b64decode(audio_b64)
            except Exception as exc:
                logger.warning("[live] enroll pcm base64 decode failed: %s", exc)
                return web.json_response({"error": "invalid audio_b64"}, status=400)
            session.feed_enroll_pcm(pcm)
            return web.json_response(
                {
                    "session_id": session_id,
                    "action": action,
                    "buffered": session.enroll_segment_count,
                }
            )
        if action == "finish":
            ok = session.finish_enroll()
            return web.json_response(
                {
                    "session_id": session_id,
                    "action": action,
                    "enrolled": ok,
                    "addressee_enrolled": bool(getattr(session, "addressee_enrolled", False)),
                }
            )
        if action == "cancel":
            session.cancel_enroll()
            return web.json_response(
                {"session_id": session_id, "action": action, "cancelled": True}
            )
    except Exception as exc:  # fail-open: never 500 the loop
        logger.error("[live] enroll action %r failed: %s", action, exc)
        return web.json_response({"error": f"enroll failed: {exc}"}, status=500)

    return web.json_response({"error": f"unknown action: {action}"}, status=400)


async def bind_live_audio_for_peer(pc, session_id: str, manager: JarvisSessionManager) -> dict:
    """Wire a WebRTC peer connection into the live listening chain.

    Mirrors ``server.bind_jarvis_audio_for_peer``: creates the live session
    and consumes the browser mic track into ``session.feed_audio``. No
    server-side speaker track is added — live replies are played by the
    browser through the P0-A ``tts_sentence`` queue.
    """
    session = await manager.create_session(session_id, mode="live")
    mic_tasks: set[asyncio.Task] = set()

    @pc.on("track")
    def on_track(track):
        if getattr(track, "kind", None) != "audio":
            return
        from .audio_processor import MicAudioTrack
        from .server import _start_mic_audio_consumer

        mic_track = MicAudioTrack(track, session)
        task = _start_mic_audio_consumer(mic_track, session_id)
        mic_tasks.add(task)
        task.add_done_callback(mic_tasks.discard)
        logger.info("[live] mic track bound for session %s", session_id)

    return {"session": session, "mic_tasks": mic_tasks}
