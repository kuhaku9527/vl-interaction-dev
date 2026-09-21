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

"Nothing was forwarded" and "no crash" are NOT asserted as "the frame did not
raise aiohttp error": ``websocket_handler`` wraps the whole message dispatch in
a broad ``except Exception`` that logs and CONTINUES, so an exception inside
the frame branch is invisible to the client. The negative tests instead assert
observables that the catch-all cannot fake:

  * the frame really entered the live-visual branch (a spy on
    ``get_live_session`` must have been consulted), so the negative assertion
    is not vacuous;
  * no ERROR record reached the outer catch-all
    (``Error handling client message``);
  * the message loop still serves the session — a second WS connection to the
    SAME session must still answer the deterministic ``update_model`` control
    message (see ``_assert_handler_survived``), which catches a ``return`` /
    ``break`` / aborted loop that "did not raise" would miss.

Run: python -m pytest tests/test_qa_ws_frame_live_forward.py -q
"""

from __future__ import annotations

import asyncio
import base64
import logging
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

    def set_model(self, model=None):
        """Control-message probe target: ``update_model`` replies only when
        this is truthy, so the reply proves the WS message loop is alive."""
        return True

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

    def __init__(self, live_session=None, report_none: bool = False) -> None:
        #: The session object the branch would reach if it read the attribute
        #: directly instead of going through the accessor.
        self.live_session = live_session
        #: Make ``get_live_session`` report "no live session" while the
        #: attribute above stays a live recorder (see the negative test).
        self.report_none = report_none
        self.config = _StubManagerConfig()
        # Spy: proves the frame actually reached the live-visual branch (an
        # absent guard call would make "nothing forwarded" vacuous).
        self.get_live_session_calls: list[str] = []

    def get_live_session(self, session_id):
        self.get_live_session_calls.append(session_id)
        return None if self.report_none else self.live_session


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


HANDLER_ERROR_LOG = "Error handling client message"


def _handler_errors(caplog) -> list[str]:
    """ERROR records from the catch-all wrapping the WS message dispatch.

    A raise inside the frame branch is swallowed by that ``except Exception``
    and only shows up here — which is precisely why the negative tests cannot
    stop at "did not raise".
    """
    return [
        r.getMessage()
        for r in caplog.records
        if r.levelno >= logging.ERROR and HANDLER_ERROR_LOG in r.getMessage()
    ]


def _live_route_failures(caplog) -> list[str]:
    """Warnings from the live-frame forward's own inner ``except``.

    The forward is wrapped separately (``live frame route failed``), so calling
    ``handle_frame`` on a None session would be swallowed as a WARNING instead
    of surfacing as an ERROR — a second, quieter place a dropped guard hides.
    """
    return [r.getMessage() for r in caplog.records if "live frame route failed" in r.getMessage()]


async def _open_ws(url, session_id: str):
    """Open a /ws client bound to an explicit session (connect_ws, not a
    context manager: the probe needs the socket to stay open)."""
    session = aiohttp.ClientSession()
    ws = await session.ws_connect(url, params={"session_id": session_id})
    return session, ws


async def _assert_handler_survived(url, session_id: str) -> str:
    """Prove the handler is still serving ``session_id`` after the frame.

    Sends a deterministic control message (``update_model``) over a SECOND WS
    bound to the same session; the reply is sent by the same handler loop that
    processed the frame, so receiving it means the loop is still alive and
    dispatching. A wedged, ``return``-ed or crashed handler cannot produce it.
    The handshake messages (``status`` / ``server_config``) are drained first.
    """
    session, ws = await _open_ws(url, session_id)
    try:
        await ws.send_json({"type": "update_model", "model": "stub-model"})
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 5.0
        reply = None
        while reply is None or reply.get("type") != "model_updated":
            remaining = deadline - loop.time()
            assert remaining > 0, f"handler never answered update_model (last={reply!r})"
            reply = await asyncio.wait_for(ws.receive_json(), timeout=remaining)
            if reply.get("type") == "status":
                assert reply["session_id"] == session_id, reply
        assert reply == {"type": "model_updated", "model": "stub-model"}, reply
        return reply["model"]
    finally:
        await ws.close()
        await session.close()


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
async def test_ws_frame_not_forwarded_when_no_live_session(monkeypatch, caplog):
    """No live session (``get_live_session`` -> None) => nothing forwarded,
    and the handler survives the frame.

    ``live`` is a real recorder wired to the manager, but ``get_live_session``
    reports None while the frame flows. The recorded session then observes the
    forwarding the branch must NOT do — so ``frames == []`` is a real
    assertion about a reachable object, not a check on a session nobody holds.
    """
    live = _StubLiveSession()
    manager = _StubManager(live_session=live, report_none=True)
    app, _vlm = _build_app(manager)
    runner, url = await _start_server(app)
    session_id = "qa-no-live"
    try:
        with caplog.at_level(logging.WARNING):
            async with (
                aiohttp.ClientSession() as session,
                session.ws_connect(url, params={"session_id": session_id}) as ws,
            ):
                await ws.send_json({"type": "frame", "data": B64_GARBAGE, "ts": 12345})
                await asyncio_sleep(0.1)
            # Non-vacuous: the frame really reached the live-visual branch and
            # consulted the manager (a missing guard call would show up here).
            assert manager.get_live_session_calls == [session_id]
            # Nothing forwarded: no live session was handed out, so no
            # handle_frame call was made on the recorded session.
            assert live.frames == []
            # ...and the branch did not call handle_frame on the None session
            # either (that would be swallowed as a "live frame route failed"
            # warning by the forward's own inner except).
            assert _live_route_failures(caplog) == []
            # The frame did not raise inside the branch: an exception there is
            # swallowed by ws_handler's broad `except Exception`, so the only
            # observable is the catch-all's ERROR record.
            assert _handler_errors(caplog) == []
        # Survived: the loop still serves this session (control-message probe).
        await _assert_handler_survived(url, session_id)
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_ws_frame_skipped_when_manager_none(monkeypatch, caplog):
    """``jarvis_manager`` is None => the manager is never consulted and the
    frame does not crash the handler.

    The manager is genuinely absent (``request.app.get("jarvis_manager")`` is
    None), so there is no stub spy to read; the observables are the absence of
    an ERROR record from the handler's catch-all and a still-serving loop.
    """
    app, _vlm = _build_app(manager=None)
    runner, url = await _start_server(app)
    session_id = "qa-no-manager"
    try:
        with caplog.at_level(logging.WARNING):
            async with (
                aiohttp.ClientSession() as session,
                session.ws_connect(url, params={"session_id": session_id}) as ws,
            ):
                await ws.send_json({"type": "frame", "data": B64_GARBAGE, "ts": 12345})
                await asyncio_sleep(0.1)
            # No AttributeError on None: the manager-None guard held, so the
            # frame branch completed without hitting the catch-all.
            assert _handler_errors(caplog) == []
        # Survived: the loop still serves this session (control-message probe).
        await _assert_handler_survived(url, session_id)
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
