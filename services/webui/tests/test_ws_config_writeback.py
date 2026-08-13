"""QA 独立补充边界用例 - WS 配置消息后端回写 (audit-frontend-2026-08-13 P1-4).

Pinned contracts:

  * ``update_frames_per_batch`` writes the requested batch size onto the
    shared ``VideoProcessorTrack.frames_per_batch`` (previously the branch
    only echoed the old value) and replies with the ACTUAL effective value;
  * invalid ``frames_per_batch`` input keeps the previous value and still
    replies (no silent drop).

Run: python -m pytest tests/test_ws_config_writeback.py -q
"""

from __future__ import annotations

import asyncio
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


class _StubVLM:
    """Minimal stand-in for VLMService (needed for the welcome messages)."""

    model = "stub-model"
    api_base = "http://stub/v1"
    prompt = "stub prompt"


class _StubManagerConfig:
    asr_promotion_enabled = False


class _StubManager:
    def __init__(self) -> None:
        self.config = _StubManagerConfig()


def _build_app():
    app = aiohttp.web.Application()
    app["jarvis_manager"] = _StubManager()
    # Stub the whole session so the handler never touches real services.
    server_module.get_or_create_session = lambda session_id: {
        "vlm_service": _StubVLM(),
        "background_service": None,
        "show_request_payload": False,
    }
    server_module.asr_model_display_name = lambda cfg: "stub-asr-model"
    app.router.add_get("/ws", server_module.websocket_handler)
    return app


async def _start_server(app):
    runner = aiohttp.web.AppRunner(app)
    await runner.setup()
    site = aiohttp.web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, f"http://127.0.0.1:{port}/ws"


async def _recv_until(ws, expected_type: str, timeout: float = 2.0) -> dict:
    """Drain welcome frames, then wait for the reply of the expected type."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise AssertionError(f"timed out waiting for reply type {expected_type!r}")
        msg = await asyncio.wait_for(ws.receive_json(), timeout=remaining)
        if msg.get("type") == expected_type:
            return msg


@pytest.fixture(autouse=True)
def _restore_frames_per_batch():
    """Keep the shared VideoProcessorTrack class variable clean across tests."""
    from joy_interaction_webui.video_processor import VideoProcessorTrack

    original = VideoProcessorTrack.frames_per_batch
    yield
    VideoProcessorTrack.frames_per_batch = original


@pytest.mark.asyncio
async def test_update_frames_per_batch_writes_back():
    from joy_interaction_webui.video_processor import VideoProcessorTrack

    app = _build_app()
    runner, url = await _start_server(app)
    try:
        async with aiohttp.ClientSession() as session, session.ws_connect(url) as ws:
            await ws.send_json({"type": "update_frames_per_batch", "frames_per_batch": 4})
            reply = await _recv_until(ws, "frames_per_batch_updated")
            assert reply["frames_per_batch"] == 4
        # The shared video pipeline config must reflect the new value.
        assert VideoProcessorTrack.frames_per_batch == 4
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_update_frames_per_batch_invalid_keeps_previous():
    from joy_interaction_webui.video_processor import VideoProcessorTrack

    VideoProcessorTrack.frames_per_batch = 3
    app = _build_app()
    runner, url = await _start_server(app)
    try:
        async with aiohttp.ClientSession() as session, session.ws_connect(url) as ws:
            await ws.send_json({"type": "update_frames_per_batch", "frames_per_batch": "bogus"})
            reply = await _recv_until(ws, "frames_per_batch_updated")
            # Invalid input keeps the previous effective value, and the reply
            # still reports the ACTUAL value (no silent drop).
            assert reply["frames_per_batch"] == 3
            assert VideoProcessorTrack.frames_per_batch == 3

            # A subsequent valid update still works.
            await ws.send_json({"type": "update_frames_per_batch", "frames_per_batch": 7})
            reply = await _recv_until(ws, "frames_per_batch_updated")
            assert reply["frames_per_batch"] == 7
            assert VideoProcessorTrack.frames_per_batch == 7
    finally:
        await runner.cleanup()
