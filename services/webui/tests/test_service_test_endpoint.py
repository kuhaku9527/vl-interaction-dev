"""Tests for the webui ``POST /api/services/test`` model-test endpoint.

The admin UI "model test" button POSTs a candidate
``api_base``/``model``/``api_key`` triple; the endpoint must live-test it
against the real upstream WITHOUT touching the saved services config or the
running summarizer / VLM services, then report the raw upstream outcome.

Per-slot contract (always HTTP 200 with ``ok`` true/false for real probes):
  slot=summary|llm : minimal OpenAI-compatible chat request
      ``{api_base}/chat/completions`` with ``max_tokens=1``.
      2xx -> {"ok": true, "model": ..., "status": ...}
      4xx/5xx -> {"ok": false, "reason": <raw body, <=300 chars>, "status": ...}
      unreachable/timeout -> {"ok": false, "reason": ..., "status": 0}
  slot=embedding  : GET ``{api_base or MEMORY_STORE_URL}/v1/providers/health``
  slot=agent      : GET ``{api_base or BACKGROUND_AGENT_API_URL}/health``
  slot=tts|asr    : with api_key -> GET ``{origin}/v1/models`` bearer auth
                    (401 -> reason "unauthorized (api key invalid)")
                    without api_key -> GET ``{origin}`` reachability
                    (any response -> ok true, reason "reachable (no key)")
  slot=asr, empty api_base -> ok true, reason "local model (no key needed)"

Malformed body / unsupported slot / missing required field : HTTP 400.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

WEBUI_SRC = Path(__file__).resolve().parents[1] / "src"
if str(WEBUI_SRC) not in sys.path:
    sys.path.insert(0, str(WEBUI_SRC))

from joy_interaction_webui import service_test  # noqa: E402

SERVER_SRC = WEBUI_SRC / "joy_interaction_webui" / "server.py"


# -- helpers ------------------------------------------------------------------


class _FakeUpstream:
    """Captures every request the webui endpoint sends to the fake upstream."""

    def __init__(self) -> None:
        self.requests: list[dict] = []


def _upstream_app(fake: _FakeUpstream, status: int = 200, body: str = "") -> web.Application:
    """Minimal OpenAI-compatible chat server for one test."""

    async def handler(request: web.Request) -> web.Response:
        payload = await request.json()
        fake.requests.append(
            {
                "path": request.path,
                "body": payload,
                "headers": dict(request.headers),
            }
        )
        return web.Response(status=status, text=body)

    app = web.Application()
    app.router.add_post("/v1/chat/completions", handler)
    return app


def _route_upstream(
    fake: _FakeUpstream,
    status: int = 200,
    body: str = "",
    *,
    method: str = "GET",
    path: str = "/",
) -> web.Application:
    """Minimal one-route upstream for the GET probes (health / models / root)."""

    async def handler(request: web.Request) -> web.Response:
        fake.requests.append({"path": request.path, "headers": dict(request.headers)})
        return web.Response(status=status, text=body)

    app = web.Application()
    app.router.add_route(method, path, handler)
    return app


def _webui_app() -> web.Application:
    """Mini app exposing just the endpoint under test (mirrors server.py wiring)."""
    app = web.Application()
    app.router.add_post("/api/services/test", service_test._services_test_handler)
    return app


def _server_source() -> str:
    return SERVER_SRC.read_text(encoding="utf-8")


async def _post(client: TestClient, payload: dict):
    return await client.post("/api/services/test", json=payload)


# -- route registration -------------------------------------------------------


def test_route_registered_in_server():
    assert 'add_post("/api/services/test", _services_test_handler)' in _server_source()


# -- input validation (HTTP 400) ----------------------------------------------


async def test_unsupported_slot_returns_400():
    async with TestServer(_webui_app()) as srv, TestClient(srv) as client:
        resp = await _post(client, {"slot": "bogus", "api_base": "http://x", "model": "m"})
        assert resp.status == 400
        body = await resp.json()
        assert body["error"] == "unsupported slot"


async def test_bad_json_returns_400():
    async with TestServer(_webui_app()) as srv, TestClient(srv) as client:
        resp = await client.post("/api/services/test", data="not-json")
        assert resp.status == 400
        assert "bad json" in (await resp.json())["error"]


async def test_missing_api_base_returns_400():
    async with TestServer(_webui_app()) as srv, TestClient(srv) as client:
        resp = await _post(client, {"slot": "summary", "model": "m"})
        assert resp.status == 400
        assert (await resp.json())["error"] == "api_base required"


async def test_missing_model_returns_400():
    async with TestServer(_webui_app()) as srv, TestClient(srv) as client:
        resp = await _post(client, {"slot": "summary", "api_base": "http://x"})
        assert resp.status == 400
        assert (await resp.json())["error"] == "model required"


async def test_bad_api_key_type_returns_400():
    async with TestServer(_webui_app()) as srv, TestClient(srv) as client:
        resp = await _post(
            client, {"slot": "summary", "api_base": "http://x", "model": "m", "api_key": 123}
        )
        assert resp.status == 400
        assert (await resp.json())["error"] == "api_key must be a string"


async def test_tts_requires_api_base_returns_400():
    async with TestServer(_webui_app()) as srv, TestClient(srv) as client:
        resp = await _post(client, {"slot": "tts", "api_base": "", "api_key": "sk"})
        assert resp.status == 400
        assert (await resp.json())["error"] == "api_base required"


# -- summary (regression): upstream 2xx -> ok:true ----------------------------


async def test_success_returns_ok_true_and_forwards_request():
    fake = _FakeUpstream()
    async with (
        TestServer(_upstream_app(fake, status=200)) as up,
        TestServer(_webui_app()) as srv,
        TestClient(srv) as client,
    ):
        api_base = f"http://{up.host}:{up.port}/v1"
        resp = await _post(
            client, {"slot": "summary", "api_base": api_base, "model": "MiniMax-VL-01"}
        )
        assert resp.status == 200
        body = await resp.json()
        assert body == {"ok": True, "model": "MiniMax-VL-01", "status": 200}

    assert len(fake.requests) == 1
    req = fake.requests[0]
    assert req["path"] == "/v1/chat/completions"
    assert req["body"] == {
        "model": "MiniMax-VL-01",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 1,
    }
    # No api_key supplied -> no Authorization header is sent.
    assert "Authorization" not in req["headers"]


async def test_success_sends_bearer_key_when_provided():
    fake = _FakeUpstream()
    async with (
        TestServer(_upstream_app(fake, status=200)) as up,
        TestServer(_webui_app()) as srv,
        TestClient(srv) as client,
    ):
        api_base = f"http://{up.host}:{up.port}/v1"
        resp = await _post(
            client,
            {"slot": "summary", "api_base": api_base, "model": "m", "api_key": "sk-test"},
        )
        assert resp.status == 200
        assert (await resp.json())["ok"] is True

    assert fake.requests[0]["headers"].get("Authorization") == "Bearer sk-test"


# -- summary (regression): upstream 4xx/5xx -> ok:false -----------------------


async def test_401_passthrough():
    fake = _FakeUpstream()
    async with (
        TestServer(_upstream_app(fake, status=401, body="Invalid API key")) as up,
        TestServer(_webui_app()) as srv,
        TestClient(srv) as client,
    ):
        api_base = f"http://{up.host}:{up.port}/v1"
        resp = await _post(client, {"slot": "summary", "api_base": api_base, "model": "m"})
        # Endpoint still answers HTTP 200 with ok=false (single-toast contract).
        assert resp.status == 200
        body = await resp.json()
        assert body == {"ok": False, "reason": "Invalid API key", "status": 401}


async def test_404_passthrough():
    fake = _FakeUpstream()
    async with (
        TestServer(_upstream_app(fake, status=404, body="Model not found")) as up,
        TestServer(_webui_app()) as srv,
        TestClient(srv) as client,
    ):
        api_base = f"http://{up.host}:{up.port}/v1"
        resp = await _post(client, {"slot": "summary", "api_base": api_base, "model": "nope"})
        assert resp.status == 200
        body = await resp.json()
        assert body["ok"] is False
        assert body["reason"] == "Model not found"
        assert body["status"] == 404


async def test_429_passthrough():
    fake = _FakeUpstream()
    async with (
        TestServer(_upstream_app(fake, status=429, body="Rate limit exceeded")) as up,
        TestServer(_webui_app()) as srv,
        TestClient(srv) as client,
    ):
        api_base = f"http://{up.host}:{up.port}/v1"
        resp = await _post(client, {"slot": "summary", "api_base": api_base, "model": "m"})
        assert resp.status == 200
        body = await resp.json()
        assert body["ok"] is False
        assert body["reason"] == "Rate limit exceeded"
        assert body["status"] == 429


async def test_reason_truncated_to_300_chars():
    fake = _FakeUpstream()
    long_body = "E" * 500
    async with (
        TestServer(_upstream_app(fake, status=500, body=long_body)) as up,
        TestServer(_webui_app()) as srv,
        TestClient(srv) as client,
    ):
        api_base = f"http://{up.host}:{up.port}/v1"
        resp = await _post(client, {"slot": "summary", "api_base": api_base, "model": "m"})
        assert resp.status == 200
        body = await resp.json()
        assert body["ok"] is False
        assert body["status"] == 500
        assert len(body["reason"]) == 300
        assert body["reason"] == "E" * 300


async def test_empty_error_body_falls_back_to_http_status():
    fake = _FakeUpstream()
    async with (
        TestServer(_upstream_app(fake, status=503, body="")) as up,
        TestServer(_webui_app()) as srv,
        TestClient(srv) as client,
    ):
        api_base = f"http://{up.host}:{up.port}/v1"
        resp = await _post(client, {"slot": "summary", "api_base": api_base, "model": "m"})
        assert resp.status == 200
        body = await resp.json()
        assert body["ok"] is False
        assert body["reason"] == "HTTP 503"
        assert body["status"] == 503


# -- summary (regression): unreachable / timeout -> ok:false, status 0 --------


async def test_connection_refused_status_0():
    async with TestServer(_webui_app()) as srv, TestClient(srv) as client:
        # Port 1 on loopback is not listening; aiohttp raises ClientConnectorError.
        resp = await _post(
            client, {"slot": "summary", "api_base": "http://127.0.0.1:1", "model": "m"}
        )
        assert resp.status == 200
        body = await resp.json()
        assert body["ok"] is False
        assert body["status"] == 0
        assert body["reason"]


async def test_timeout_status_0(monkeypatch):
    captured = {}

    class _FakeResponse:
        async def __aenter__(self):
            raise asyncio.TimeoutError("upstream timed out")

        async def __aexit__(self, *args):
            return False

    class _FakeSession:
        def __init__(self, *args, **kwargs):
            captured["timeout"] = kwargs.get("timeout")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def post(self, url, json, headers):
            captured["url"] = url
            captured["payload"] = json
            captured["headers"] = headers
            return _FakeResponse()

    monkeypatch.setattr(service_test.aiohttp, "ClientSession", _FakeSession)
    result = await service_test._test_openai_compatible("http://up/v1", "m", "k")
    assert result["ok"] is False
    assert result["status"] == 0
    assert "upstream timed out" in result["reason"]
    # The request is still the minimal OpenAI-compatible chat body.
    assert captured["url"] == "http://up/v1/chat/completions"
    assert captured["payload"] == {
        "model": "m",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 1,
    }
    assert captured["headers"].get("Authorization") == "Bearer k"


# -- llm (same OpenAI-compatible chat contract as summary) --------------------


async def test_llm_success_forwards_minimal_chat_request():
    fake = _FakeUpstream()
    async with (
        TestServer(_upstream_app(fake, status=200)) as up,
        TestServer(_webui_app()) as srv,
        TestClient(srv) as client,
    ):
        api_base = f"http://{up.host}:{up.port}/v1"
        resp = await _post(client, {"slot": "llm", "api_base": api_base, "model": "m"})
        assert resp.status == 200
        assert (await resp.json()) == {"ok": True, "model": "m", "status": 200}

    assert fake.requests[0]["path"] == "/v1/chat/completions"
    assert fake.requests[0]["body"] == {
        "model": "m",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 1,
    }


async def test_llm_401_passthrough():
    fake = _FakeUpstream()
    async with (
        TestServer(_upstream_app(fake, status=401, body="Invalid API key")) as up,
        TestServer(_webui_app()) as srv,
        TestClient(srv) as client,
    ):
        api_base = f"http://{up.host}:{up.port}/v1"
        resp = await _post(client, {"slot": "llm", "api_base": api_base, "model": "m"})
        assert resp.status == 200
        assert (await resp.json()) == {
            "ok": False,
            "reason": "Invalid API key",
            "status": 401,
        }


async def test_llm_404_passthrough():
    fake = _FakeUpstream()
    async with (
        TestServer(_upstream_app(fake, status=404, body="Model not found")) as up,
        TestServer(_webui_app()) as srv,
        TestClient(srv) as client,
    ):
        api_base = f"http://{up.host}:{up.port}/v1"
        resp = await _post(client, {"slot": "llm", "api_base": api_base, "model": "nope"})
        assert resp.status == 200
        body = await resp.json()
        assert body["ok"] is False
        assert body["reason"] == "Model not found"
        assert body["status"] == 404


# -- embedding (health probe, no key) -----------------------------------------


async def test_embedding_probe_200():
    fake = _FakeUpstream()
    async with (
        TestServer(_route_upstream(fake, status=200, path="/v1/providers/health")) as up,
        TestServer(_webui_app()) as srv,
        TestClient(srv) as client,
    ):
        api_base = f"http://{up.host}:{up.port}"
        resp = await _post(client, {"slot": "embedding", "api_base": api_base})
        assert resp.status == 200
        assert (await resp.json()) == {"ok": True, "status": 200}

    assert fake.requests[0]["path"] == "/v1/providers/health"
    assert "Authorization" not in fake.requests[0]["headers"]


async def test_embedding_probe_failure_passthrough():
    fake = _FakeUpstream()
    async with (
        TestServer(
            _route_upstream(fake, status=503, body="db down", path="/v1/providers/health")
        ) as up,
        TestServer(_webui_app()) as srv,
        TestClient(srv) as client,
    ):
        api_base = f"http://{up.host}:{up.port}"
        resp = await _post(client, {"slot": "embedding", "api_base": api_base})
        assert resp.status == 200
        assert (await resp.json()) == {"ok": False, "reason": "db down", "status": 503}


async def test_embedding_unreachable_status_0():
    async with TestServer(_webui_app()) as srv, TestClient(srv) as client:
        resp = await _post(client, {"slot": "embedding", "api_base": "http://127.0.0.1:1"})
        assert resp.status == 200
        body = await resp.json()
        assert body["ok"] is False
        assert body["status"] == 0
        assert body["reason"]


async def test_embedding_empty_api_base_uses_default(monkeypatch):
    fake = _FakeUpstream()
    async with (
        TestServer(_route_upstream(fake, status=200, path="/v1/providers/health")) as up,
        TestServer(_webui_app()) as srv,
        TestClient(srv) as client,
    ):
        monkeypatch.setattr(
            service_test, "_default_memory_store_url", lambda: f"http://{up.host}:{up.port}"
        )
        resp = await _post(client, {"slot": "embedding", "api_base": ""})
        assert resp.status == 200
        assert (await resp.json())["ok"] is True

    assert fake.requests[0]["path"] == "/v1/providers/health"


# -- agent (health probe, no key) ---------------------------------------------


async def test_agent_probe_200():
    fake = _FakeUpstream()
    async with (
        TestServer(_route_upstream(fake, status=200, path="/health")) as up,
        TestServer(_webui_app()) as srv,
        TestClient(srv) as client,
    ):
        api_base = f"http://{up.host}:{up.port}"
        resp = await _post(client, {"slot": "agent", "api_base": api_base})
        assert resp.status == 200
        assert (await resp.json()) == {"ok": True, "status": 200}

    assert fake.requests[0]["path"] == "/health"
    assert "Authorization" not in fake.requests[0]["headers"]


async def test_agent_unreachable_status_0():
    async with TestServer(_webui_app()) as srv, TestClient(srv) as client:
        resp = await _post(client, {"slot": "agent", "api_base": "http://127.0.0.1:1"})
        assert resp.status == 200
        body = await resp.json()
        assert body["ok"] is False
        assert body["status"] == 0
        assert body["reason"]


async def test_agent_empty_api_base_uses_default(monkeypatch):
    fake = _FakeUpstream()
    async with (
        TestServer(_route_upstream(fake, status=200, path="/health")) as up,
        TestServer(_webui_app()) as srv,
        TestClient(srv) as client,
    ):
        monkeypatch.setattr(
            service_test, "_default_background_agent_url", lambda: f"http://{up.host}:{up.port}"
        )
        resp = await _post(client, {"slot": "agent", "api_base": ""})
        assert resp.status == 200
        assert (await resp.json())["ok"] is True

    assert fake.requests[0]["path"] == "/health"


# -- tts (auth probe with key, reachability without) --------------------------


async def test_tts_valid_key_200():
    fake = _FakeUpstream()
    async with (
        TestServer(_route_upstream(fake, status=200, path="/v1/models")) as up,
        TestServer(_webui_app()) as srv,
        TestClient(srv) as client,
    ):
        # api_base carries an endpoint path; the probe must strip to the origin.
        api_base = f"http://{up.host}:{up.port}/v1/synthesize"
        resp = await _post(client, {"slot": "tts", "api_base": api_base, "api_key": "sk"})
        assert resp.status == 200
        assert (await resp.json()) == {"ok": True, "status": 200}

    assert fake.requests[0]["path"] == "/v1/models"
    assert fake.requests[0]["headers"].get("Authorization") == "Bearer sk"


async def test_tts_wrong_key_401():
    fake = _FakeUpstream()
    async with (
        TestServer(_route_upstream(fake, status=401, body="bad token", path="/v1/models")) as up,
        TestServer(_webui_app()) as srv,
        TestClient(srv) as client,
    ):
        api_base = f"http://{up.host}:{up.port}/v1/synthesize"
        resp = await _post(client, {"slot": "tts", "api_base": api_base, "api_key": "nope"})
        assert resp.status == 200
        assert (await resp.json()) == {
            "ok": False,
            "reason": "unauthorized (api key invalid)",
            "status": 401,
        }


async def test_tts_no_key_reachability():
    fake = _FakeUpstream()
    async with (
        # Any response (here 404 on the origin) proves reachability.
        TestServer(_route_upstream(fake, status=404, path="/")) as up,
        TestServer(_webui_app()) as srv,
        TestClient(srv) as client,
    ):
        api_base = f"http://{up.host}:{up.port}/v1/synthesize"
        resp = await _post(client, {"slot": "tts", "api_base": api_base, "api_key": ""})
        assert resp.status == 200
        body = await resp.json()
        assert body["ok"] is True
        assert body["reason"] == "reachable (no key)"
        assert body["status"] == 404


async def test_tts_unreachable_status_0():
    async with TestServer(_webui_app()) as srv, TestClient(srv) as client:
        resp = await _post(client, {"slot": "tts", "api_base": "http://127.0.0.1:1", "api_key": ""})
        assert resp.status == 200
        body = await resp.json()
        assert body["ok"] is False
        assert body["status"] == 0
        assert body["reason"]


# -- asr (local degrade / cloud auth probe / reachability) --------------------


async def test_asr_local_model_no_key_needed():
    async with TestServer(_webui_app()) as srv, TestClient(srv) as client:
        resp = await _post(client, {"slot": "asr", "api_base": "", "api_key": ""})
        assert resp.status == 200
        assert (await resp.json()) == {
            "ok": True,
            "reason": "local model (no key needed)",
            "status": 200,
        }


async def test_asr_cloud_valid_key_200():
    fake = _FakeUpstream()
    async with (
        TestServer(_route_upstream(fake, status=200, path="/v1/models")) as up,
        TestServer(_webui_app()) as srv,
        TestClient(srv) as client,
    ):
        api_base = f"http://{up.host}:{up.port}/v1"
        resp = await _post(client, {"slot": "asr", "api_base": api_base, "api_key": "sk"})
        assert resp.status == 200
        assert (await resp.json()) == {"ok": True, "status": 200}

    assert fake.requests[0]["path"] == "/v1/models"
    assert fake.requests[0]["headers"].get("Authorization") == "Bearer sk"


async def test_asr_cloud_wrong_key_401():
    fake = _FakeUpstream()
    async with (
        TestServer(_route_upstream(fake, status=401, body="bad token", path="/v1/models")) as up,
        TestServer(_webui_app()) as srv,
        TestClient(srv) as client,
    ):
        api_base = f"http://{up.host}:{up.port}/v1"
        resp = await _post(client, {"slot": "asr", "api_base": api_base, "api_key": "nope"})
        assert resp.status == 200
        assert (await resp.json()) == {
            "ok": False,
            "reason": "unauthorized (api key invalid)",
            "status": 401,
        }


async def test_asr_cloud_no_key_reachability():
    fake = _FakeUpstream()
    async with (
        TestServer(_route_upstream(fake, status=200, path="/")) as up,
        TestServer(_webui_app()) as srv,
        TestClient(srv) as client,
    ):
        api_base = f"http://{up.host}:{up.port}/v1"
        resp = await _post(client, {"slot": "asr", "api_base": api_base, "api_key": ""})
        assert resp.status == 200
        body = await resp.json()
        assert body["ok"] is True
        assert body["reason"] == "reachable (no key)"
        assert body["status"] == 200
