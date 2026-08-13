# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""webinfer summarizer-route proxy (split out of server.py).

The webui never mutates webinfer's summarizer directly; it forwards the
snapshot via ``/v1/summarizer/route`` (the single-webinfer-main-path
principle). The saved ``_services_config`` (owned by ``services_config`` /
re-exported through the server facade) is resolved lazily so the module-load
graph stays acyclic.
"""

import logging
import os

import aiohttp
from aiohttp import web

logger = logging.getLogger(__name__)


def _webinfer_base_url() -> str:
    """webinfer base URL for the /v1/summarizer/route proxy.

    Defaults to http://127.0.0.1:8070. Override with WEBINFER_URL env
    var. The webui's own LLM api_base can also point to webinfer (the
    two share the same OpenAI-compatible gateway).
    """
    from . import server as _server

    env = os.environ.get("WEBINFER_URL")
    if env:
        return env.rstrip("/")
    llm_cfg = _server._services_config.get("llm", {})
    llm_base = llm_cfg.get("api_base", "http://127.0.0.1:8070/v1").rstrip("/")
    if llm_base.endswith("/v1"):
        llm_base = llm_base[:-3]
    return llm_base


async def _webinfer_proxy_summarizer_routing(summary_cfg: dict) -> dict:
    """Push summary config into the running webinfer process.

    The webui never mutates the summarizer directly. It tells webinfer
    to mutate its own state via /v1/summarizer/route, then webinfer
    ships the snapshot back. This is the single-webinfer-main-path
    principle: branches only happen inside webinfer.
    """
    from . import server as _server

    base = _server._webinfer_base_url()
    payload = {
        "api_base": summary_cfg.get("api_base"),
        "model_name": summary_cfg.get("model"),
        "api_key": summary_cfg.get("api_key"),
    }
    try:
        async with (
            aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5.0)) as session,
            session.post(base + "/v1/summarizer/route", json=payload) as resp,
        ):
            if resp.status >= 400:
                body = await resp.text()
                logger.warning(
                    "webinfer /v1/summarizer/route returned %d: %s", resp.status, body[:200]
                )
                return {"ok": False, "status": resp.status, "body": body[:200]}
            return await resp.json()
    except Exception as exc:
        logger.warning("webinfer /v1/summarizer/route unreachable: %s", exc)
        return {"ok": False, "reason": str(exc)[:200]}


async def _webinfer_summarizer_route_handler(request):
    """GET / POST /api/webinfer/summarizer/route.

    Proxies directly to webinfer. Saves the round-trip through
    /api/services/config -> _propagate_services_to_runtime when the
    UI just wants to read or push the current snapshot synchronously.
    """
    from . import server as _server

    base = _server._webinfer_base_url()
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
                async with session.get(base + "/v1/summarizer/route") as resp:
                    payload = await resp.json(content_type=None)
                    return web.json_response(payload, status=resp.status)
            else:
                async with session.post(base + "/v1/summarizer/route", json=body) as resp:
                    payload = await resp.json(content_type=None)
                    return web.json_response(payload, status=resp.status)
    except Exception as exc:
        logger.warning("webinfer summarizer route proxy failed: %s", exc)
        return web.json_response(
            {"error": "webinfer unreachable", "reason": str(exc)[:200]}, status=502
        )
