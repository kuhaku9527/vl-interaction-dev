"""QA 独立补充边界用例 — server.py WS ``frame`` → live session forwarding.

Spec live-visual-cb.md §2.4/§3 层 2: the existing WS ``frame`` message
(1fps screen capture) must also route to the active live session's ring buffer
IN PARALLEL with the pre-existing video pipeline (vlm_service) — the video
pipeline is untouched.

Boundary contracts pinned here:

  * live session exists  -> ``handle_frame(image_b64, ts)`` forwarded;
  * live session absent   -> no forwarding, handler keeps working (the video
    pipeline still runs its normal fail-safe path);
  * ``jarvis_manager`` is None -> no forwarding, no crash (manager optional);
  * timestamp resolution: ``ts`` -> ``timestamp`` -> ``time.time()*1000``.

The pre-existing video pipeline (base64 decode -> PIL -> vlm_service) is left
running on every test as a regression guard: a garbage frame must fail safe
inside the pipeline and never break the WS handler.

Run: python -m pytest tests/test_qa_ws_frame_live_forward.py -q
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

import aiohttp
import pytest

REPO = Path(__file__).resolve().parents[3]
WEBUI_SRC = REPO / "services" / "webui" / "src"
for _p in (str(REPO), str(WEBUI_SRC)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from joy_interaction_webui import server as server_module  # noqa: E402

# A syntactically-valid base64 payload that is NOT a real JPEG: forces the
# pre-existing video pipeline onto its documented fail-safe path.
B64_GARBAGE = base64.b64encode(b"not-a-real-jpeg-at-all").decode("ascii")


class _StubVLM:
    """Minimal stand-in for VLMService (video pipeline)."""

    model = "stub-model"
    api_base = "http://stub/v1"
    prompt = "stub prompt"

    def __init__(self) -> None:
        self.processed: list = []

    def process_frame(self, img, frame_metadata=None):
        self.processed.append((img, frame_metadata))

    def get_current_response(self):
        return "", {}

    def get_metrics(self):
        return {}


class _StubBackground:
    def get_config(self):
        return {}


class _StubManagerConfig:
    asr_promotion_enabled = False


class _StubManager:
    """Stand-in for JarvisSessionManager: exposes get_live_session."""

    def __init__(self, live_session=None) -> None:
        self.live_session = live_session
        self.config = _StubManagerConfig()

    def get_live_session(self, session_id):
        return self.live_session


class _StubLiveSession:
    """Records handle_frame calls (LiveSession passthrough boundary)."""

    def __init__(self) -> None:
        self.frames: list[tuple[str, float]] = []

    def handle_frame(self, image_b64: str, ts_ms: float) -> None:
        self.frames.append((image_b64, ts_ms))


async def _start_server(app):
    runner = aiohttp.web.AppRunner(app)
    await runner.setup()
    site = aiohttp.web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, f"http://127.0.0.1:{port}/ws"


def _build_app(manager: _StubManager | None, vlm: _StubVLM | None = None):
    vlm = vlm or _StubVLM()
    app = aiohttp.web.Application()
    app["jarvis_manager"] = manager
    # Stub the whole session so the handler never touches real services.
    server_module.get_or_create_session = lambda session_id: {
        "vlm_service": vlm,
        "background_service": _StubBackground(),
        "show_request_payload": False,
    }
    server_module.asr_model_display_name = lambda cfg: "stub-asr-model"
    app.router.add_get("/ws", server_module.websocket_handler)
    return app, vlm


async def _send_frame(runner, url, frame_msg: dict):
    async with aiohttp.ClientSession() as session, session.ws_connect(url) as ws:
        await ws.send_json(frame_msg)
        # Give the handler a beat to process the message.
        await asyncio_sleep(0.05)


async def asyncio_sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)


@pytest.mark.asyncio
async def test_ws_frame_forwarded_when_live_session_exists(monkeypatch):
    live = _StubLiveSession()
    manager = _StubManager(live_session=live)
    app, _vlm = _build_app(manager)
    runner, url = await _start_server(app)
    try:
        async with aiohttp.ClientSession() as session, session.ws_connect(url) as ws:
            await ws.send_json({"type": "frame", "data": B64_GARBAGE, "ts": 12345})
            await asyncio_sleep(0.1)
        assert live.frames == [(B64_GARBAGE, 12345.0)]
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_ws_frame_not_forwarded_when_no_live_session(monkeypatch):
    manager = _StubManager(live_session=None)
    app, _vlm = _build_app(manager)
    runner, url = await _start_server(app)
    try:
        async with aiohttp.ClientSession() as session, session.ws_connect(url) as ws:
            await ws.send_json({"type": "frame", "data": B64_GARBAGE, "ts": 12345})
            await asyncio_sleep(0.1)
        # No live session -> nothing forwarded; handler survived the frame.
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_ws_frame_skipped_when_manager_none(monkeypatch):
    app, _vlm = _build_app(manager=None)
    runner, url = await _start_server(app)
    try:
        async with aiohttp.ClientSession() as session, session.ws_connect(url) as ws:
            await ws.send_json({"type": "frame", "data": B64_GARBAGE, "ts": 12345})
            await asyncio_sleep(0.1)
        # manager=None -> get_live_session never consulted; no crash.
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_ws_frame_ts_fallback_timestamp_then_wallclock(monkeypatch):
    live = _StubLiveSession()
    manager = _StubManager(live_session=live)
    app, _vlm = _build_app(manager)
    runner, url = await _start_server(app)
    try:
        # No ts but a timestamp -> falls back to timestamp.
        async with aiohttp.ClientSession() as session, session.ws_connect(url) as ws:
            await ws.send_json({"type": "frame", "data": B64_GARBAGE, "timestamp": 777})
            await asyncio_sleep(0.1)
        assert live.frames == [(B64_GARBAGE, 777.0)]

        # Neither ts nor timestamp -> falls back to time.time()*1000.
        live.frames.clear()
        monkeypatch.setattr(server_module.time, "time", lambda: 1234.5)
        async with aiohttp.ClientSession() as session, session.ws_connect(url) as ws:
            await ws.send_json({"type": "frame", "data": B64_GARBAGE})
            await asyncio_sleep(0.1)
        assert live.frames == [(B64_GARBAGE, 1234500.0)]
    finally:
        await runner.cleanup()
