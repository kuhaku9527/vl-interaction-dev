"""Tests for the webui -> webinfer summarizer routing proxy.

The webui must not mutate webinfer's summarizer directly. The /api/
services/config PUT handler triggers _propagate_services_to_runtime,
which calls _webinfer_proxy_summarizer_routing, which POSTs the
summary config to webinfer's /v1/summarizer/route. The operator's
"Summary = cloud" change is then live in the running webinfer process.

These tests pin the composition:
  1. _webinfer_base_url() respects WEBINFER_URL env var.
  2. _webinfer_base_url() falls back to the LLM api_base stripped of /v1.
  3. PUT /api/services/config with summary config triggers a POST to
     webinfer with the right payload.
  4. The proxy returns the new snapshot from webinfer.
  5. When webinfer is unreachable, the proxy returns {ok: false, reason: ...}.

The unreachable case is split across two layers because the summary push is
run as a fire-and-forget task (``_propagate_services_to_runtime`` ->
``loop.create_task``):

  * ``test_propagate_unreachable_webinfer_logs_warning`` runs the REAL proxy
    against a dead local port and pins the fail-open contract itself
    (WARNING + ``{ok: false, reason}``), which the webui-side caller has no
    try/except for;
  * ``test_propagate_wraps_raising_webinfer_proxy`` pins the webui side: the
    PUT caller is never broken by whatever the push does, up to and including
    the proxy raising.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import aiohttp
import pytest

REPO = Path(__file__).resolve().parents[2]
WEBUI_SRC = REPO / "services" / "webui" / "src"
for _p in (str(REPO), str(WEBUI_SRC)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


async def _wait_until(pred, timeout: float = 5.0) -> bool:
    """Poll ``pred`` until it holds (or the timeout expires). Returns its value."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not pred() and loop.time() < deadline:
        await asyncio.sleep(0.01)
    return bool(pred())


async def _settle(*tasks) -> None:
    """Wait for the given tasks to finish (already-done tasks return at once)."""
    if tasks:
        await asyncio.wait(list(tasks), timeout=5.0)


def _only_summary_push() -> None:
    """Make the summarizer push the ONLY fire-and-forget push spawned.

    ``_propagate_services_to_runtime`` also pushes the agent and embedding
    providers when those slots carry a provider (the built-in defaults do, and
    both would attempt real network calls from this test). Blanking the two
    providers is what the production guard tests (``if ... .strip()``), so no
    task is created for them at all and the task count below stays exact.
    """
    from joy_interaction_webui import server

    for slot in ("agent", "embedding"):
        cfg = dict(server._services_config.get(slot) or {})
        cfg["provider"] = ""
        server._services_config[slot] = cfg


async def _start_put_server():
    """Self-contained aiohttp server exposing the real PUT handler (mirrors
    tests/test_services_config_persist.py)."""
    from joy_interaction_webui import server

    app = aiohttp.web.Application()
    app.router.add_get("/api/services/config", server._services_config_handler)
    app.router.add_put("/api/services/config", server._services_config_handler)
    runner = aiohttp.web.AppRunner(app)
    await runner.setup()
    site = aiohttp.web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, f"http://127.0.0.1:{port}/api/services/config"


@pytest.fixture(autouse=True)
def _isolate():
    from joy_interaction_webui import server

    snapshot = dict(server._services_config)
    yield
    server._services_config.clear()
    server._services_config.update(snapshot)


def test_webinfer_base_url_respects_env(monkeypatch):
    from joy_interaction_webui import server

    monkeypatch.setenv("WEBINFER_URL", "http://my-webinfer:9999")
    assert server._webinfer_base_url() == "http://my-webinfer:9999"


def test_webinfer_base_url_strips_v1_from_llm_default(monkeypatch):
    from joy_interaction_webui import server

    monkeypatch.delenv("WEBINFER_URL", raising=False)
    server._services_config["llm"] = {"api_base": "http://localhost:8070/v1"}
    assert server._webinfer_base_url() == "http://localhost:8070"


def test_webinfer_base_url_keeps_non_v1_path(monkeypatch):
    from joy_interaction_webui import server

    monkeypatch.delenv("WEBINFER_URL", raising=False)
    server._services_config["llm"] = {"api_base": "http://localhost:8070/gateway"}
    # /v1 is not at the end, so the URL is left as is.
    assert server._webinfer_base_url() == "http://localhost:8070/gateway"


async def test_propagate_summary_calls_webinfer(monkeypatch):
    from joy_interaction_webui import server

    captured = {}

    async def _fake_proxy(summary_cfg):
        captured["payload"] = {
            "api_base": summary_cfg.get("api_base"),
            "model_name": summary_cfg.get("model"),
            "api_key": summary_cfg.get("api_key"),
        }
        return {"api_base": "x", "model_name": "y", "api_key_set": True}

    monkeypatch.setattr(server, "_webinfer_proxy_summarizer_routing", _fake_proxy)
    server._services_config["summary"] = {
        "api_base": "https://api.minimaxi.com/v1",
        "model": "MiniMax-VL-01",
        "api_key": "sk-test",
    }
    await server._propagate_services_to_runtime()
    # The create_task is fire-and-forget; give the scheduler a chance
    # to run the proxy before we assert.
    await asyncio.sleep(0)
    assert captured["payload"] == {
        "api_base": "https://api.minimaxi.com/v1",
        "model_name": "MiniMax-VL-01",
        "api_key": "sk-test",
    }


async def test_propagate_skips_when_summary_empty(monkeypatch):
    from joy_interaction_webui import server

    called = {"count": 0}

    async def _fake_proxy(summary_cfg):
        called["count"] += 1
        return {}

    monkeypatch.setattr(server, "_webinfer_proxy_summarizer_routing", _fake_proxy)
    server._services_config["summary"] = {"api_base": "", "model": "", "api_key": ""}
    await server._propagate_services_to_runtime()
    assert called["count"] == 0


async def test_propagate_unreachable_webinfer_logs_warning(monkeypatch, caplog):
    """If webinfer is down, the proxy must not raise; it logs a warning and
    returns {ok: false, reason: ...}. _propagate_services_to_runtime must not
    let that bubble up to the PUT /api/services/config caller.

    "webinfer is down" is the REAL thing here: the un-stubbed proxy runs
    against a dead loopback port, so aiohttp's connect() raises and
    ``webinfer_proxy.py:68-70`` is what logs and returns the fail-open dict —
    the very behaviour the old stub skipped by returning ``{ok: false}``.

    The connect refusal takes ~2s, so the propagation is driven from a helper
    thread (its own event loop) while this test's aiohttp loop stays free for
    ``caplog``'s record collection.
    """
    import concurrent.futures

    from joy_interaction_webui import server

    monkeypatch.setenv("WEBINFER_URL", "http://127.0.0.1:1")
    _only_summary_push()
    # Only the summary slot decides whether the summarizer push happens, so the
    # other slots are left exactly as they are (_isolate restores everything).
    server._services_config["summary"] = {
        "api_base": "https://api.minimaxi.com/v1",
        "model": "MiniMax-VL-01",
    }

    def _drive() -> dict:
        """Run the propagation and settle its fire-and-forget push, recording
        what the REAL proxy logged and returned."""

        async def _main():
            # Hold the task: an unretrieved exception drops the loop's weak
            # reference and it vanishes from all_tasks() before it is read.
            captured: list[asyncio.Task] = []
            returns: list[dict] = []
            loop = asyncio.get_running_loop()
            orig_create_task = loop.create_task

            def _capture(coro, **kwargs):
                task = orig_create_task(coro, **kwargs)
                captured.append(task)
                return task

            loop.create_task = _capture  # type: ignore[method-assign]
            real_proxy = server._webinfer_proxy_summarizer_routing

            async def _recording(cfg):
                # The push is fire-and-forget, so its return value has no
                # caller; record it here to pin the {ok: false, reason} contract.
                result = await real_proxy(cfg)
                returns.append(result)
                return result

            server._webinfer_proxy_summarizer_routing = _recording
            try:
                await server._propagate_services_to_runtime()
                await _settle(*captured)
            finally:
                server._webinfer_proxy_summarizer_routing = real_proxy
                loop.create_task = orig_create_task
            return {
                "settled": [
                    (t.done(), t.cancelled(), repr(t.exception()))
                    for t in captured
                    if not t.cancelled()
                ],
                "returns": returns,
            }

        return asyncio.run(_main())

    with (
        caplog.at_level(logging.WARNING, logger="joy_interaction_webui"),
        concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool,
    ):
        fut = pool.submit(_drive)
        while not fut.done():
            await asyncio.sleep(0.05)
        # (a) Must not raise to the propagation caller.
        outcome = fut.result()

    # The push really ran and completed (not cancelled by asyncio.run teardown).
    assert outcome["settled"] == [(True, False, "None")], outcome["settled"]
    # (b) The proxy logged the unreachable route as a WARNING.
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    unreachable = [m for m in warnings if "unreachable" in m]
    assert len(unreachable) == 1, warnings
    assert "127.0.0.1:1" in unreachable[0], unreachable
    # (c) ...and returned {ok: false, reason: ...} instead of raising.
    assert len(outcome["returns"]) == 1, outcome["returns"]
    fail_open = outcome["returns"][0]
    assert fail_open["ok"] is False, fail_open
    assert "127.0.0.1:1" in fail_open["reason"], fail_open
    # (d) The caller is not broken by it: see
    #     test_propagate_wraps_raising_webinfer_proxy (PUT -> 200, pinned with a
    #     deterministic raise rather than this ~2s connect refusal).


async def test_propagate_wraps_raising_webinfer_proxy(monkeypatch, tmp_path):
    """The PUT /api/services/config caller survives the summary push raising.

    The unreachable case above takes ~2s of connect refusal, so the ordering
    ("did the push record its exception before PUT returned?") cannot be pinned
    against it. A stub that raises immediately makes the window deterministic:
    even a proxy that raises, rather than returning ``{ok: false}``, is not
    allowed to reach the PUT caller. Observed: it does not — the push is
    fire-and-forget, so the exception stays inside the task (asserted below)
    and the PUT still answers 200 with the saved config.
    """
    from joy_interaction_webui import server

    calls = {"count": 0}
    raised = ConnectionRefusedError("Connection refused")
    # Hold a strong reference to the fire-and-forget task: an unretrieved
    # exception lets the loop drop its weak reference, which would make the
    # task disappear from asyncio.all_tasks() before it can be inspected.
    push_tasks: list[asyncio.Task] = []

    async def _raising_proxy(summary_cfg):
        calls["count"] += 1
        push_tasks.append(asyncio.current_task())
        raise raised

    monkeypatch.setattr(server, "_webinfer_proxy_summarizer_routing", _raising_proxy)
    _only_summary_push()
    server._services_config["summary"] = {
        "api_base": "https://api.minimaxi.com/v1",
        "model": "MiniMax-VL-01",
    }

    # The push is FIRE-AND-FORGET: it is dispatched as a task, never awaited.
    # The spawn is recorded separately here so a regression to `await` fails on
    # this statement instead of on an incidental side effect. Scoped to the
    # direct call only — the PUT phase below uses the real create_task.
    loop = asyncio.get_running_loop()
    real_create_task = loop.create_task
    spawned: list = []

    def _record_task(coro, **kwargs):
        task = real_create_task(coro, **kwargs)
        spawned.append(task)
        return task

    loop.create_task = _record_task  # type: ignore[method-assign]
    try:
        # Direct call: dispatch happens, and nothing propagates to this caller.
        try:
            await server._propagate_services_to_runtime()
        except BaseException as exc:
            pytest.fail(f"propagation must not raise to the caller: {exc!r}")
    finally:
        loop.create_task = real_create_task  # type: ignore[method-assign]

    # The push was spawned as a task and left NOT awaited (still pending)...
    assert len(spawned) == 1, spawned
    assert not spawned[0].done(), "the push must not be awaited by the caller"
    assert calls["count"] == 0, calls
    # ...so the raise surfaces only on that task, which nothing retrieves.
    await _wait_until(lambda: spawned[0].done())
    assert calls["count"] == 1, calls
    assert len(push_tasks) == 1, push_tasks
    assert isinstance(push_tasks[0].exception(), ConnectionRefusedError), push_tasks[0]

    # End-to-end through the real PUT handler: 200 even though the push raised.
    persist = tmp_path / "config" / "services.json"
    monkeypatch.setattr(server, "_SERVICES_CONFIG_PATH", str(persist))
    monkeypatch.setattr(
        server, "_probe_summary", lambda _c: {"ok": True, "endpoint": "x", "code": 200}
    )
    runner, url = await _start_put_server()
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.put(
                url,
                json={"summary": {"api_base": "https://api.minimaxi.com/v2", "model": "m2"}},
            ) as resp,
        ):
            assert resp.status == 200, await resp.text()
            body = await resp.json()
        assert body["summary"]["api_base"] == "https://api.minimaxi.com/v2", body
        # The PUT drove a SECOND push, which raised the same way and still did
        # not reach the HTTP caller.
        await _wait_until(lambda: len(push_tasks) >= 2 and push_tasks[1].done())
        assert calls["count"] == 2, calls
        assert len(push_tasks) == 2, push_tasks
        assert isinstance(push_tasks[1].exception(), ConnectionRefusedError), push_tasks[1]
    finally:
        await runner.cleanup()
