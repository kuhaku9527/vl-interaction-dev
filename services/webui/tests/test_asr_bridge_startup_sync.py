"""Regression tests for P1-2: ASR bridge aligns with persisted config at startup.

Covers the audit fix (``doc/architecture/audit-webui-backend-2026-08-13.md``
P1-2): after a webui restart the internal ASR bridge must be reconciled with
the persisted ``config/services.json``. Before the fix the bridge was only
(re)started by ``PUT /api/services/config``, so a persisted cloud ASR
``api_base`` left browser ASR pointing at a dead bridge (ERROR_NO_FALLBACK)
until an operator manually re-PUT the config.

Verifies:

* ``_asr_bridge_sync`` starts the bridge when a persisted ``asr.api_base``
  exists and never starts one when the slot is empty (only stops a stale
  bridge);
* ``server.on_startup`` invokes ``_asr_bridge_sync`` once, so a restart
  automatically recovers the persisted config.
"""

from __future__ import annotations

import copy

import pytest
from aiohttp import web


@pytest.fixture
def services_config():
    """Expose the shared ``server._services_config`` dict and restore it after."""
    from joy_interaction_webui import server as server_mod

    original = copy.deepcopy(server_mod._services_config)
    yield server_mod._services_config
    server_mod._services_config.clear()
    server_mod._services_config.update(original)


def test_asr_bridge_sync_ensures_bridge_with_persisted_config(services_config, monkeypatch):
    from joy_interaction_webui import asr_bridge

    services_config["asr"] = {
        "api_base": "http://asr.example.com/v1",
        "model": "m1",
        "api_key": "k1",
    }
    ensured = []
    stopped = []
    monkeypatch.setattr(asr_bridge, "_asr_bridge_ensure", lambda *a, **k: ensured.append(a))
    monkeypatch.setattr(asr_bridge, "_asr_bridge_stop", lambda: stopped.append(True))

    asr_bridge._asr_bridge_sync()

    assert len(ensured) == 1
    assert ensured[0] == ("http://asr.example.com/v1", "m1", "k1")
    assert stopped == []


def test_asr_bridge_sync_no_config_stops_bridge(services_config, monkeypatch):
    from joy_interaction_webui import asr_bridge

    services_config["asr"] = {"api_base": "", "model": "", "api_key": ""}
    ensured = []
    stopped = []
    monkeypatch.setattr(asr_bridge, "_asr_bridge_ensure", lambda *a, **k: ensured.append(a))
    monkeypatch.setattr(asr_bridge, "_asr_bridge_stop", lambda: stopped.append(True))

    asr_bridge._asr_bridge_sync()

    # Empty api_base must NOT launch a bridge subprocess; only the stop path
    # (no-op when nothing is running) executes.
    assert ensured == []
    assert stopped == [True]


async def test_on_startup_calls_asr_bridge_sync(monkeypatch):
    from joy_interaction_webui import asr as asr_module
    from joy_interaction_webui import server as server_mod

    sync_calls = []
    monkeypatch.setattr(server_mod, "_asr_bridge_sync", lambda: sync_calls.append("sync"))
    # With a cloud ASR configured, the in-proc warm-up returns early and never
    # loads a model.
    monkeypatch.setattr(asr_module, "get_asr_url", lambda: "http://asr.example.com/v1")

    app = web.Application()
    await server_mod.on_startup(app)

    assert sync_calls == ["sync"]
    assert "jarvis_manager" in app
    warmup = app.get("browser_asr_warmup_task")
    if warmup is not None:
        await warmup


async def test_on_startup_no_config_does_not_start_bridge(services_config, monkeypatch):
    from joy_interaction_webui import asr as asr_module
    from joy_interaction_webui import asr_bridge
    from joy_interaction_webui import server as server_mod

    services_config["asr"] = {"api_base": "", "model": "", "api_key": ""}
    ensured = []
    stopped = []
    monkeypatch.setattr(asr_bridge, "_asr_bridge_ensure", lambda *a, **k: ensured.append(a))
    monkeypatch.setattr(asr_bridge, "_asr_bridge_stop", lambda: stopped.append(True))
    monkeypatch.setattr(asr_module, "get_asr_url", lambda: "")
    monkeypatch.setattr(asr_module, "_get_inproc_asr", lambda: object())

    app = web.Application()
    await server_mod.on_startup(app)

    # Startup with no persisted ASR config must not launch a bridge subprocess.
    assert ensured == []
    assert stopped == [True]
    warmup = app.get("browser_asr_warmup_task")
    if warmup is not None:
        await warmup
