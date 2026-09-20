# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""background-agent provider-route proxy (split out of server.py, N7.1).

The webui never mutates background-agent's provider directly; it forwards the
snapshot via ``/v1/provider/route`` (single-owner principle, same as the
summarizer-route proxy in ``webinfer_proxy.py``). The saved
``_services_config`` is resolved lazily so the module-load graph stays acyclic.
"""

import logging
import os

import aiohttp
from aiohttp import web

logger = logging.getLogger(__name__)


def _bg_agent_base_url() -> str:
    """background-agent base URL for the /v1/provider/route proxy.

    Defaults to http://127.0.0.1:8079. Override with BACKGROUND_AGENT_API_URL
    env var (run-windows.ps1 already sets it for the webui process).
    """
    env = os.environ.get("BACKGROUND_AGENT_API_URL")
    if env:
        return env.rstrip("/")
    return "http://127.0.0.1:8079"


async def _bg_agent_provider_routing(agent_cfg: dict) -> dict:
    """Push the agent provider choice into the running background-agent.

    The webui never mutates the provider directly. It tells background-agent
    to rebuild its own ``_provider`` via POST /v1/provider/route, then
    background-agent ships the applied snapshot back (fail-loud 400 on
    unknown name — D-080, no silent fallback).
    """
    base = _bg_agent_base_url()
    provider = (agent_cfg.get("provider") or "").strip()
    if not provider:
        return {"ok": False, "reason": "no provider set"}
    payload = {"provider": provider}
    try:
        async with (
            aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5.0)) as session,
            session.post(base + "/v1/provider/route", json=payload) as resp,
        ):
            if resp.status == 200:
                body = await resp.json(content_type=None)
                applied = (body or {}).get("provider", provider)
                return {"ok": True, "provider": applied}
            text = (await resp.text())[:200]
            logger.warning("background-agent provider route rejected (%s): %s", resp.status, text)
            return {"ok": False, "reason": text, "status": resp.status}
    except Exception as exc:
        logger.warning("background-agent provider route push failed: %s", exc)
        return {"ok": False, "reason": str(exc)[:200]}


async def _bg_agent_provider_route_handler(request: web.Request) -> web.Response:
    """GET / POST /api/bg-agent/provider/route.

    Proxies directly to background-agent. Saves the round-trip through
    /api/services/config -> _propagate_services_to_runtime when the UI just
    wants to read or push the current snapshot synchronously.
    """
    from . import server as _server

    base = _server._bg_agent_base_url()
    method = "POST" if request.method == "POST" else "GET"
    body = None
    if method == "POST":
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid json"}, status=400)
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5.0)) as session:
            if method == "GET":
                async with session.get(base + "/v1/provider/route") as resp:
                    payload = await resp.json(content_type=None)
                    return web.json_response(payload, status=resp.status)
            else:
                async with session.post(base + "/v1/provider/route", json=body) as resp:
                    payload = await resp.json(content_type=None)
                    return web.json_response(payload, status=resp.status)
    except Exception as exc:
        logger.warning("background-agent provider route proxy failed: %s", exc)
        return web.json_response(
            {"error": "background-agent unreachable", "reason": str(exc)[:200]}, status=502
        )
