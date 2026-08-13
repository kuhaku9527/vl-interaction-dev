"""Regression tests for P1-3: ``session_cleanup`` tears down jarvis/live sessions.

Covers the audit fix (``doc/architecture/audit-webui-backend-2026-08-13.md``
P1-3): the ``/api/session/cleanup`` endpoint previously only removed the VLM
session dict, leaking ``JarvisStateMachine`` / ``LiveStateMachine`` (and their
~200MB KWS/ASR engines, KWS diagnostic threads, proactive/confirm tasks) until
process exit, and KWS kept listening on dead sessions.

Verifies:

* cleanup calls ``manager.remove_session`` **and** ``manager.remove_live_session``
  (both dicts, covering the 双开 jarvis+live case) so the state machines are
  stopped and dropped from the manager;
* repeated cleanup is idempotent (no crash, reports ``False`` on the second
  pass);
* ``JarvisSessionManager.remove_session`` / ``remove_live_session`` return
  ``bool`` and are idempotent.
"""

from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from joy_interaction_webui import server as server_mod
from joy_interaction_webui.jarvis_session import JarvisConfig, JarvisSessionManager


class FakeStoppable:
    """Minimal session stand-in: records ``stop()`` calls, nothing else."""

    def __init__(self, name: str):
        self.name = name
        self.stop_calls = 0

    async def stop(self):
        self.stop_calls += 1


def _cleanup_app(manager) -> web.Application:
    app = web.Application()
    app["jarvis_manager"] = manager
    app.router.add_post("/api/session/cleanup", server_mod.session_cleanup)
    return app


@pytest.fixture
def cleanup_globals():
    """Snapshot server module-level registries so the handler test is isolated."""
    snap = {
        "sessions": dict(server_mod.sessions),
        "session_websockets": dict(server_mod.session_websockets),
        "session_peer_connections": dict(server_mod.session_peer_connections),
        "pcs": set(server_mod.pcs),
        "rtsp_tracks": dict(server_mod.rtsp_tracks),
    }
    yield
    server_mod.sessions.clear()
    server_mod.sessions.update(snap["sessions"])
    server_mod.session_websockets.clear()
    server_mod.session_websockets.update(snap["session_websockets"])
    server_mod.session_peer_connections.clear()
    server_mod.session_peer_connections.update(snap["session_peer_connections"])
    server_mod.pcs.clear()
    server_mod.pcs.update(snap["pcs"])
    server_mod.rtsp_tracks.clear()
    server_mod.rtsp_tracks.update(snap["rtsp_tracks"])


async def test_session_cleanup_removes_manager_sessions(cleanup_globals):
    manager = JarvisSessionManager(config=JarvisConfig())
    jarvis = FakeStoppable("jarvis")
    live = FakeStoppable("live")
    manager._sessions["s1"] = jarvis
    manager._live_sessions["s1"] = live

    async with TestServer(_cleanup_app(manager)) as srv, TestClient(srv) as client:
        resp = await client.post("/api/session/cleanup?session_id=s1")
        assert resp.status == 200
        body = await resp.json()

    assert body["jarvis_removed"] is True
    assert body["live_removed"] is True
    # Both state machines were dropped from the manager.
    assert manager.get_session("s1") is None
    assert manager.get_live_session("s1") is None
    # Both engines' stop() paths were invoked exactly once.
    assert jarvis.stop_calls == 1
    assert live.stop_calls == 1


async def test_session_cleanup_idempotent(cleanup_globals):
    manager = JarvisSessionManager(config=JarvisConfig())
    manager._sessions["s1"] = FakeStoppable("jarvis")
    manager._live_sessions["s1"] = FakeStoppable("live")

    async with TestServer(_cleanup_app(manager)) as srv, TestClient(srv) as client:
        resp1 = await client.post("/api/session/cleanup?session_id=s1")
        assert resp1.status == 200
        resp2 = await client.post("/api/session/cleanup?session_id=s1")
        assert resp2.status == 200
        body2 = await resp2.json()

    # A second cleanup is a safe no-op (idempotent), not a crash.
    assert body2["jarvis_removed"] is False
    assert body2["live_removed"] is False


async def test_session_cleanup_missing_session_id_400(cleanup_globals):
    manager = JarvisSessionManager(config=JarvisConfig())
    async with TestServer(_cleanup_app(manager)) as srv, TestClient(srv) as client:
        resp = await client.post("/api/session/cleanup")
        assert resp.status == 400


async def test_manager_remove_methods_return_bool_and_are_idempotent():
    manager = JarvisSessionManager(config=JarvisConfig())
    assert await manager.remove_session("nope") is False
    assert await manager.remove_live_session("nope") is False

    jarvis = FakeStoppable("jarvis")
    live = FakeStoppable("live")
    manager._sessions["s1"] = jarvis
    manager._live_sessions["s1"] = live

    assert await manager.remove_session("s1") is True
    assert await manager.remove_live_session("s1") is True
    assert jarvis.stop_calls == 1
    assert live.stop_calls == 1

    # Re-running is a safe no-op.
    assert await manager.remove_session("s1") is False
    assert await manager.remove_live_session("s1") is False
    assert jarvis.stop_calls == 1
    assert live.stop_calls == 1
