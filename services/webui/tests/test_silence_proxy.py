"""Tests for the radio-silence proxy (webui /api/live/silence -> webinfer).

Pins the spec contract (``doc/specs/draft-radio-silence.md`` §3/§7):

- GET returns ``{suppressed, settings, source}`` — ``suppressed`` comes from
  webinfer (the live owner), ``settings`` from the local persisted file.
- GET fails open when webinfer is unreachable: 200 + last-known snapshot +
  ``source: "local"`` (never a 500).
- POST persists settings through the services_config validation gate (wrong
  types / out-of-range minutes -> 400, never saved) then forwards to webinfer.
- POST still saves settings when webinfer is unreachable, but returns 502.
- The silence slot merges from a persisted ``services.json`` and does not
  leak into the other slots (three-mode regression).
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import aiohttp
import pytest

REPO = Path(__file__).resolve().parents[2]
WEBUI_SRC = REPO / "services" / "webui" / "src"
for _p in (str(REPO), str(WEBUI_SRC)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from joy_interaction_webui import (  # noqa: E402
    server,
    silence_proxy,
)
from joy_interaction_webui import services_config as sc  # noqa: E402

UNREACHABLE_WEBINFER = "http://127.0.0.1:9"


@pytest.fixture(autouse=True)
def _reset_runtime(monkeypatch):
    """Snapshot/restore config + reset the last-known suppressed snapshot."""
    snapshot = copy.deepcopy(server._services_config)
    silence_proxy._silence_suppressed = False

    async def _noop_propagate(*_args, **_kwargs):
        return None

    monkeypatch.setattr(server, "_propagate_services_to_runtime", _noop_propagate)
    yield
    server._services_config.clear()
    server._services_config.update(snapshot)
    silence_proxy._silence_suppressed = False


@pytest.fixture
def persist_path(tmp_path, monkeypatch):
    """Point persistence at a tmp file and start from defaults (no file yet)."""
    path = tmp_path / "config" / "services.json"
    monkeypatch.setattr(server, "_SERVICES_CONFIG_PATH", str(path))
    server._reload_services_config_from_file()
    yield path


def _silence_defaults() -> dict:
    return copy.deepcopy(sc._SERVICES_CONFIG_DEFAULTS["silence"])


async def _start_webui_app():
    app = aiohttp.web.Application()
    app.router.add_get("/api/live/silence", server._silence_handler)
    app.router.add_post("/api/live/silence", server._silence_handler)
    runner = aiohttp.web.AppRunner(app)
    await runner.setup()
    site = aiohttp.web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, "http://127.0.0.1:%d/api/live/silence" % port


async def _start_fake_webinfer(get_body: dict, post_handler=None):
    """A minimal webinfer stub: GET /v1/live/silence + record POSTs."""
    seen = {"posts": []}

    async def handle_get(_request):
        return aiohttp.web.json_response(get_body)

    async def handle_post(request):
        payload = await request.json()
        seen["posts"].append(payload)
        if post_handler is not None:
            return aiohttp.web.json_response(post_handler(payload))
        return aiohttp.web.json_response({"suppressed": payload.get("suppressed", False)})

    app = aiohttp.web.Application()
    app.router.add_get("/v1/live/silence", handle_get)
    app.router.add_post("/v1/live/silence", handle_post)
    runner = aiohttp.web.AppRunner(app)
    await runner.setup()
    site = aiohttp.web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, "http://127.0.0.1:%d" % port, seen


# ---------------------------------------------------------------------------
# GET
# ---------------------------------------------------------------------------
async def test_get_defaults_when_webinfer_unreachable(persist_path, monkeypatch):
    """Fail-open GET: 200 + local defaults + source=local (never a 500)."""
    monkeypatch.setattr(server, "_silence_base_url", lambda: UNREACHABLE_WEBINFER)
    runner, url = await _start_webui_app()
    try:
        async with aiohttp.ClientSession() as session, session.get(url) as resp:
            assert resp.status == 200
            body = await resp.json()
        assert body["suppressed"] is False
        assert body["settings"] == _silence_defaults()
        assert body["source"] == "local"
    finally:
        await runner.cleanup()


async def test_get_webinfer_suppressed_with_local_settings(persist_path, monkeypatch):
    """suppressed comes from webinfer; settings stay from the local file."""
    fake_runner, base, _seen = await _start_fake_webinfer(
        {"suppressed": True, "settings": {"kws_enabled": False}}
    )
    monkeypatch.setattr(server, "_silence_base_url", lambda: base)
    # Local persisted settings DIFFER from what webinfer echoes back: the SPA
    # panel must show the local file (persistence owner), not webinfer's copy.
    server._services_config["silence"]["kws_enabled"] = True
    runner, url = await _start_webui_app()
    try:
        async with aiohttp.ClientSession() as session, session.get(url) as resp:
            assert resp.status == 200
            body = await resp.json()
        assert body["suppressed"] is True
        assert body["settings"]["kws_enabled"] is True  # local file wins
        assert body["source"] == "webinfer"
    finally:
        await runner.cleanup()
        await fake_runner.cleanup()


async def test_get_fail_open_returns_last_known_suppressed(persist_path, monkeypatch):
    """After a successful webinfer round-trip set suppressed=True, a later
    unreachable GET still reports the last-known True (fail-open, no 500)."""
    fake_runner, base, _seen = await _start_fake_webinfer({"suppressed": True})
    monkeypatch.setattr(server, "_silence_base_url", lambda: base)
    runner, url = await _start_webui_app()
    try:
        async with aiohttp.ClientSession() as session, session.get(url) as resp:
            assert (await resp.json())["suppressed"] is True
    finally:
        await runner.cleanup()
        await fake_runner.cleanup()
    # webinfer now "down": GET still returns the last-known snapshot.
    monkeypatch.setattr(server, "_silence_base_url", lambda: UNREACHABLE_WEBINFER)
    runner, url = await _start_webui_app()
    try:
        async with aiohttp.ClientSession() as session, session.get(url) as resp:
            assert resp.status == 200
            body = await resp.json()
        assert body["suppressed"] is True
        assert body["source"] == "local"
    finally:
        await runner.cleanup()


# ---------------------------------------------------------------------------
# POST — happy path + persistence
# ---------------------------------------------------------------------------
async def test_post_persists_settings_and_forwards(persist_path, monkeypatch):
    captured = {}

    def _post_handler(payload):
        captured["payload"] = payload
        return {"suppressed": True, "settings": payload.get("settings", {})}

    fake_runner, base, seen = await _start_fake_webinfer({}, post_handler=_post_handler)
    monkeypatch.setattr(server, "_silence_base_url", lambda: base)
    runner, url = await _start_webui_app()
    try:
        settings = {
            "hotkey": "Ctrl+Alt+S",
            "asr_enabled": True,
            "kws_enabled": False,
            "timeout_hint_enabled": False,
            "timeout_hint_minutes": 30,
            "auto_wake_enabled": True,
            "auto_wake_minutes": 45,
        }
        async with (
            aiohttp.ClientSession() as session,
            session.post(url, json={"suppressed": True, "settings": settings}) as resp,
        ):
            assert resp.status == 200
            body = await resp.json()
        assert body["suppressed"] is True
        # Forwarded payload: hotkey is webui-owned and is NOT sent to webinfer;
        # the 6 webinfer-owned keys go through unchanged.
        expected_forward = {
            "suppressed": True,
            "settings": {
                "asr_enabled": True,
                "kws_enabled": False,
                "timeout_hint_enabled": False,
                "timeout_hint_minutes": 30,
                "auto_wake_enabled": True,
                "auto_wake_minutes": 45,
            },
        }
        assert seen["posts"] == [expected_forward]
        # Durable half: persisted to services.json silence slot (ALL 7 keys,
        # hotkey included — the local file is the frontend panel source).
        assert persist_path.exists()
        on_disk = json.loads(persist_path.read_text(encoding="utf-8"))
        assert on_disk["silence"]["hotkey"] == "Ctrl+Alt+S"
        assert on_disk["silence"]["auto_wake_minutes"] == 45
        # In-memory applied too.
        assert server._services_config["silence"]["asr_enabled"] is True
    finally:
        await runner.cleanup()
        await fake_runner.cleanup()


async def test_post_settings_survive_restart(persist_path, monkeypatch):
    """Persisted silence settings are restored by a simulated webui restart."""
    fake_runner, base, _seen = await _start_fake_webinfer({})
    monkeypatch.setattr(server, "_silence_base_url", lambda: base)
    runner, url = await _start_webui_app()
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.post(
                url, json={"settings": {"kws_enabled": False, "timeout_hint_minutes": 7}}
            ) as resp,
        ):
            assert resp.status == 200
    finally:
        await runner.cleanup()
        await fake_runner.cleanup()
    # Restart: reset to defaults, re-merge the file.
    server._reload_services_config_from_file()
    assert server._services_config["silence"]["kws_enabled"] is False
    assert server._services_config["silence"]["timeout_hint_minutes"] == 7
    assert server._services_config["silence"]["hotkey"] == "Ctrl+Shift+S"


async def test_post_suppressed_only_forwards(persist_path, monkeypatch):
    """A toggle-only POST (no settings) must not rewrite the settings file."""
    fake_runner, base, seen = await _start_fake_webinfer({})
    monkeypatch.setattr(server, "_silence_base_url", lambda: base)
    runner, url = await _start_webui_app()
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.post(url, json={"suppressed": True}) as resp,
        ):
            assert resp.status == 200
        assert seen["posts"] == [{"suppressed": True}]
        # No settings in the body -> nothing persisted.
        assert not persist_path.exists()
    finally:
        await runner.cleanup()
        await fake_runner.cleanup()


async def test_post_forwards_kws_event(persist_path, monkeypatch):
    """KWS wake events pushed by the webui side reach webinfer (唤醒通道)."""
    fake_runner, base, seen = await _start_fake_webinfer({})
    monkeypatch.setattr(server, "_silence_base_url", lambda: base)
    runner, url = await _start_webui_app()
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.post(url, json={"kws_event": True}) as resp,
        ):
            assert resp.status == 200
        assert seen["posts"] == [{"kws_event": True}]
        assert not persist_path.exists()
    finally:
        await runner.cleanup()
        await fake_runner.cleanup()


async def test_get_surfaces_webinfer_hint_fields(persist_path, monkeypatch):
    """T1 hint one-shot (spec §6) is passed through for the status poll."""
    fake_runner, base, _seen = await _start_fake_webinfer(
        {
            "suppressed": True,
            "hint_pending": True,
            "hint_count": 1,
            "wake_pending": False,
        }
    )
    monkeypatch.setattr(server, "_silence_base_url", lambda: base)
    runner, url = await _start_webui_app()
    try:
        async with aiohttp.ClientSession() as session, session.get(url) as resp:
            assert resp.status == 200
            body = await resp.json()
        assert body["suppressed"] is True
        assert body["hint_pending"] is True
        assert body["hint_count"] == 1
        assert body["wake_pending"] is False
        assert body["source"] == "webinfer"
    finally:
        await runner.cleanup()
        await fake_runner.cleanup()


# ---------------------------------------------------------------------------
# POST — fail-open / validation
# ---------------------------------------------------------------------------
async def test_post_unreachable_webinfer_returns_502_but_settings_saved(persist_path, monkeypatch):
    """webinfer down: POST returns 502 yet the settings file is still written."""
    monkeypatch.setattr(server, "_silence_base_url", lambda: UNREACHABLE_WEBINFER)
    runner, url = await _start_webui_app()
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.post(
                url,
                json={"suppressed": True, "settings": {"kws_enabled": False}},
            ) as resp,
        ):
            assert resp.status == 502
            body = await resp.json()
        assert body["saved"] is True
        # Settings survive the webinfer outage (fire-and-forget precedent).
        assert persist_path.exists()
        on_disk = json.loads(persist_path.read_text(encoding="utf-8"))
        assert on_disk["silence"]["kws_enabled"] is False
        # Last-known suppressed snapshot is untouched (toggle not applied).
        assert silence_proxy._silence_suppressed is False
    finally:
        await runner.cleanup()


async def test_post_rejects_wrong_setting_type(persist_path, monkeypatch):
    fake_runner, base, seen = await _start_fake_webinfer({})
    monkeypatch.setattr(server, "_silence_base_url", lambda: base)
    runner, url = await _start_webui_app()
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.post(url, json={"settings": {"kws_enabled": "yes"}}) as resp,
        ):
            assert resp.status == 400
            body = await resp.json()
        assert body["slot"] == "silence"
        assert body["field"] == "kws_enabled"
        # 约法三章②: invalid settings never reach webinfer or the file.
        assert seen["posts"] == []
        assert not persist_path.exists()
        assert server._services_config["silence"]["kws_enabled"] is True
    finally:
        await runner.cleanup()
        await fake_runner.cleanup()


async def test_post_rejects_minutes_out_of_range(persist_path, monkeypatch):
    fake_runner, base, seen = await _start_fake_webinfer({})
    monkeypatch.setattr(server, "_silence_base_url", lambda: base)
    runner, url = await _start_webui_app()
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.post(url, json={"settings": {"auto_wake_minutes": 0}}) as resp,
        ):
            assert resp.status == 400
            body = await resp.json()
        assert body["field"] == "auto_wake_minutes"
        assert "between 1 and 1440" in body["error"]
        assert seen["posts"] == []
        assert not persist_path.exists()
    finally:
        await runner.cleanup()
        await fake_runner.cleanup()


async def test_post_rejects_bool_as_minutes(persist_path, monkeypatch):
    """True must not be accepted as a minutes value (bool is an int subclass)."""
    fake_runner, base, _seen = await _start_fake_webinfer({})
    monkeypatch.setattr(server, "_silence_base_url", lambda: base)
    runner, url = await _start_webui_app()
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.post(url, json={"settings": {"timeout_hint_minutes": True}}) as resp,
        ):
            assert resp.status == 400
            body = await resp.json()
        assert body["field"] == "timeout_hint_minutes"
        assert "must be an integer" in body["error"]
    finally:
        await runner.cleanup()
        await fake_runner.cleanup()


async def test_post_rejects_non_bool_suppressed(persist_path, monkeypatch):
    fake_runner, base, _seen = await _start_fake_webinfer({})
    monkeypatch.setattr(server, "_silence_base_url", lambda: base)
    runner, url = await _start_webui_app()
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.post(url, json={"suppressed": "yes"}) as resp,
        ):
            assert resp.status == 400
            body = await resp.json()
        assert body["error"] == "suppressed must be a boolean"
    finally:
        await runner.cleanup()
        await fake_runner.cleanup()


async def test_post_rejects_bad_json(persist_path, monkeypatch):
    fake_runner, base, _seen = await _start_fake_webinfer({})
    monkeypatch.setattr(server, "_silence_base_url", lambda: base)
    runner, url = await _start_webui_app()
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.post(url, data=b"{bad", headers={"Content-Type": "application/json"}) as resp,
        ):
            assert resp.status == 400
    finally:
        await runner.cleanup()
        await fake_runner.cleanup()


# ---------------------------------------------------------------------------
# services_config integration + regression
# ---------------------------------------------------------------------------
def test_silence_slot_in_defaults():
    d = sc._SERVICES_CONFIG_DEFAULTS["silence"]
    assert d["hotkey"] == "Ctrl+Shift+S"
    assert d["asr_enabled"] is False
    assert d["kws_enabled"] is True
    assert d["timeout_hint_enabled"] is True
    assert d["timeout_hint_minutes"] == 15
    assert d["auto_wake_enabled"] is False
    # Aligned with webinfer silence_control + frontend radio_silence.js.
    assert d["auto_wake_minutes"] == 30


def test_merge_silence_slot_from_persisted_file(tmp_path):
    """A persisted services.json silence slot merges typed fields on load."""
    target = copy.deepcopy(sc._SERVICES_CONFIG_DEFAULTS)
    path = tmp_path / "services.json"
    path.write_text(
        json.dumps(
            {
                "silence": {
                    "hotkey": "Ctrl+Alt+X",
                    "kws_enabled": False,
                    "timeout_hint_minutes": "20",  # numeric string accepted
                    "auto_wake_minutes": 99999,  # out of range -> keep default
                    "bogus_field": "injected",
                }
            }
        ),
        encoding="utf-8",
    )
    assert sc._merge_services_config_file(target, str(path)) is True
    assert target["silence"]["hotkey"] == "Ctrl+Alt+X"
    assert target["silence"]["kws_enabled"] is False
    assert target["silence"]["timeout_hint_minutes"] == 20
    assert target["silence"]["auto_wake_minutes"] == 30
    assert "bogus_field" not in target["silence"]
    # Other slots untouched (three-mode regression).
    assert target["llm"] == sc._SERVICES_CONFIG_DEFAULTS["llm"]
    assert target["asr"] == sc._SERVICES_CONFIG_DEFAULTS["asr"]


def test_merge_silence_ignores_wrong_types(tmp_path):
    """bool must stay bool; True must not become a minutes value."""
    target = copy.deepcopy(sc._SERVICES_CONFIG_DEFAULTS)
    path = tmp_path / "services.json"
    path.write_text(
        json.dumps({"silence": {"asr_enabled": "false", "timeout_hint_minutes": True}}),
        encoding="utf-8",
    )
    assert sc._merge_services_config_file(target, str(path)) is True
    assert target["silence"]["asr_enabled"] is False  # string rejected
    assert target["silence"]["timeout_hint_minutes"] == 15  # bool rejected


async def test_put_services_config_accepts_silence_slot(persist_path, monkeypatch):
    """PUT /api/services/config with a silence slot applies + persists."""
    app = aiohttp.web.Application()
    app.router.add_get("/api/services/config", server._services_config_handler)
    app.router.add_put("/api/services/config", server._services_config_handler)
    runner = aiohttp.web.AppRunner(app)
    await runner.setup()
    site = aiohttp.web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    url = "http://127.0.0.1:%d/api/services/config" % port
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.put(url, json={"silence": {"kws_enabled": False}}) as resp,
        ):
            assert resp.status == 200
            body = await resp.json()
        assert body["silence"]["kws_enabled"] is False
        assert body["silence"]["hotkey"] == "Ctrl+Shift+S"
        assert persist_path.exists()
    finally:
        await runner.cleanup()
