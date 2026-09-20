# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Model-slot live-test endpoint (POST /api/services/test, split out of server.py).

The admin UI "model test" button verifies that a candidate
``api_base``/``model``/``api_key`` triple actually works BEFORE the operator
commits it to the saved services config. Each slot sends the smallest probe
that proves its upstream contract:

- ``summary`` / ``llm``: one minimal OpenAI-compatible chat request
  (``{api_base}/chat/completions``, ``max_tokens=1``).
- ``embedding``: ``GET {api_base}/v1/providers/health`` (no key verified).
- ``agent``: ``GET {api_base}/health`` (no key verified).
- ``tts`` / ``asr`` (cloud): ``GET {origin}/v1/models`` with a bearer key
  (auth check only — no audio / transcription is generated), or a bare
  ``GET {origin}`` reachability check when no key is supplied.
- ``asr`` (local sherpa-onnx, empty api_base): immediate ok — the model-dir
  probe already covers the local path.

It is a pure test action: it never writes back to ``_services_config`` and
never touches the running summarizer / VLM services. Always answers HTTP 200
with ``ok`` true/false so the frontend renders a single toast; only malformed
input / unsupported slots return 4xx.
"""

import asyncio
import logging
from urllib.parse import urlsplit

import aiohttp
from aiohttp import web

logger = logging.getLogger(__name__)

# Upstream error text is operator-facing (shown in a toast); cap it so a
# verbose 4xx/5xx body cannot balloon the response.
_REASON_MAX_CHARS = 300
# The chat test must feel interactive; 10s covers a cold llama.cpp prompt
# load while still failing fast on a dead endpoint.
_TEST_TIMEOUT_S = 10.0
# Probes are pure health checks (no model load); 3s is enough to distinguish
# a dead endpoint from a slow one.
_PROBE_TIMEOUT_S = 3.0
# Slots that can be live-tested. summary/llm hit an OpenAI-compatible chat
# endpoint; embedding/agent hit a plain health endpoint; tts/asr hit an auth
# probe (``/v1/models``) or a bare reachability check.
_SUPPORTED_TEST_SLOTS = ("summary", "llm", "embedding", "agent", "tts", "asr")


async def _services_test_handler(request: web.Request) -> web.Response:
    """POST /api/services/test — live-test a model slot configuration.

    Dispatches on the caller-supplied ``slot`` (never the saved config) and
    reports the raw upstream outcome. Malformed input and unsupported slots
    return 400; every real probe resolves to HTTP 200 with ``ok`` true/false.
    """
    try:
        payload = await request.json()
    except ValueError as exc:
        return web.json_response({"error": "bad json: %s" % exc}, status=400)
    if not isinstance(payload, dict):
        return web.json_response({"error": "payload must be a JSON object"}, status=400)

    slot = payload.get("slot")
    if slot not in _SUPPORTED_TEST_SLOTS:
        logger.warning("POST /api/services/test unsupported slot=%r", slot)
        return web.json_response({"error": "unsupported slot", "slot": slot}, status=400)

    api_base = payload.get("api_base")
    if api_base is not None and not isinstance(api_base, str):
        return web.json_response({"error": "api_base must be a string"}, status=400)
    model = payload.get("model")
    if model is not None and not isinstance(model, str):
        return web.json_response({"error": "model must be a string"}, status=400)
    api_key = payload.get("api_key")
    if api_key is not None and not isinstance(api_key, str):
        return web.json_response({"error": "api_key must be a string"}, status=400)

    api_base = api_base.strip() if api_base else ""
    model = model.strip() if model else ""
    api_key = api_key.strip() if api_key else None

    if slot in ("summary", "llm"):
        if not api_base:
            return web.json_response({"error": "api_base required"}, status=400)
        if not model:
            return web.json_response({"error": "model required"}, status=400)
        result = await _test_openai_compatible(api_base, model, api_key)
    elif slot == "embedding":
        base = api_base or _default_memory_store_url()
        result = await _get_probe(base.rstrip("/") + "/v1/providers/health")
    elif slot == "agent":
        base = api_base or _default_background_agent_url()
        result = await _get_probe(base.rstrip("/") + "/health")
    elif slot == "tts":
        if not api_base:
            return web.json_response({"error": "api_base required"}, status=400)
        result = await _run_tts_test(api_base, api_key)
    else:  # slot == "asr"
        result = await _run_asr_test(api_base, api_key)

    logger.info(
        "POST /api/services/test slot=%s model=%s ok=%s status=%s",
        slot,
        model,
        result["ok"],
        result.get("status"),
    )
    return web.json_response(result)


async def _test_openai_compatible(api_base: str, model: str, api_key: str | None) -> dict:
    """Run one minimal chat completion against an OpenAI-compatible endpoint.

    Parameters
    ----------
    api_base: str
        OpenAI-compatible base URL (``.../v1`` style); the request is sent to
        ``api_base + "/chat/completions"``.
    model: str
        Model id to request.
    api_key: str | None
        Optional bearer token; no Authorization header is sent when None.

    Returns
    -------
    dict
        ``{"ok": bool, "model": str, "status": int, "reason"?: str}`` — always
        resolves (never raises): 2xx maps to ``ok=True``; other HTTP codes map
        to ``ok=False`` with the raw upstream body capped at
        ``_REASON_MAX_CHARS``; transport errors / timeouts map to ``ok=False``
        with ``status=0``.
    """
    url = api_base.rstrip("/") + "/chat/completions"
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 1,
    }
    headers = {}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    try:
        async with (
            aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=_TEST_TIMEOUT_S)) as session,
            session.post(url, json=body, headers=headers) as resp,
        ):
            if resp.status < 400:
                return {"ok": True, "model": model, "status": resp.status}
            reason = (await resp.text(errors="replace"))[:_REASON_MAX_CHARS].strip()
            if not reason:
                reason = "HTTP %d" % resp.status
            logger.warning(
                "model test chat failed status=%d reason=%s",
                resp.status,
                reason[:200],
            )
            return {"ok": False, "reason": reason, "status": resp.status}
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
        logger.warning("model test unreachable %s: %s", url, exc)
        return {"ok": False, "reason": str(exc)[:_REASON_MAX_CHARS], "status": 0}


async def _get_probe(url: str, api_key: str | None = None, *, reachable_ok: bool = False) -> dict:
    """GET ``url`` and report whether the upstream answered.

    Parameters
    ----------
    url: str
        Full URL to GET (never contains the api key).
    api_key: str | None
        Optional bearer token sent via the ``Authorization`` header.
    reachable_ok: bool
        When True, any HTTP response counts as success (reachability probe);
        when False, only 2xx/3xx count (health probe).

    Returns
    -------
    dict
        ``{"ok": bool, "status": int, "reason"?: str}``; transport errors and
        timeouts resolve to ``ok=False`` with ``status=0``.
    """
    headers = {}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    try:
        async with (
            aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=_PROBE_TIMEOUT_S)) as session,
            session.get(url, headers=headers) as resp,
        ):
            if reachable_ok:
                return {"ok": True, "reason": "reachable (no key)", "status": resp.status}
            if resp.status < 400:
                return {"ok": True, "status": resp.status}
            reason = (await resp.text(errors="replace"))[:_REASON_MAX_CHARS].strip()
            if not reason:
                reason = "HTTP %d" % resp.status
            logger.warning(
                "model test probe failed url=%s status=%d reason=%s",
                url,
                resp.status,
                reason[:200],
            )
            return {"ok": False, "reason": reason, "status": resp.status}
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
        logger.warning("model test probe unreachable url=%s: %s", url, exc)
        return {"ok": False, "reason": str(exc)[:_REASON_MAX_CHARS], "status": 0}


def _origin_of(api_base: str) -> str:
    """Extract the ``scheme://host[:port]`` origin, dropping any path/query.

    Tolerates bare origins (``http://127.0.0.1:8985``) and full endpoint URLs
    (``http://127.0.0.1:8985/v1/synthesize``); the services config may store
    either form for tts/asr.
    """
    parsed = urlsplit(api_base)
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")


def _default_memory_store_url() -> str:
    """embedding probe target when api_base is empty (webui's memory-store default)."""
    from . import server  # deferred: server re-exports MEMORY_STORE_URL at startup

    return server.MEMORY_STORE_URL


def _default_background_agent_url() -> str:
    """agent probe target when api_base is empty (BACKGROUND_AGENT_API_URL or :8079)."""
    from .bg_agent_proxy import _bg_agent_base_url

    return _bg_agent_base_url()


async def _auth_or_reachable(origin: str, api_key: str | None) -> dict:
    """Probe ``{origin}/v1/models`` with a bearer key, or bare reachability.

    Parameters
    ----------
    origin: str
        ``scheme://host[:port]`` origin (see ``_origin_of``).
    api_key: str | None
        When set, GET ``{origin}/v1/models`` with ``Authorization: Bearer``;
        401 maps to the fixed reason "unauthorized (api key invalid)", other
        non-2xx statuses pass the raw reason through. When None, any HTTP
        response from the origin proves reachability.

    Returns
    -------
    dict
        Same shape as ``_get_probe``.
    """
    if api_key:
        result = await _get_probe(origin + "/v1/models", api_key=api_key)
        if result["ok"]:
            return result
        if result["status"] == 401:
            return {"ok": False, "reason": "unauthorized (api key invalid)", "status": 401}
        return result
    return await _get_probe(origin, reachable_ok=True)


async def _run_tts_test(api_base: str, api_key: str | None) -> dict:
    """Probe a TTS endpoint: auth check with a key, reachability without.

    The TTS service (MiniMax) exposes an OpenAI-style ``/v1/models`` list; a
    bearer round-trip validates the key without synthesizing any audio.
    """
    return await _auth_or_reachable(_origin_of(api_base), api_key)


async def _run_asr_test(api_base: str, api_key: str | None) -> dict:
    """Probe an ASR endpoint; empty api_base short-circuits to the local model.

    A local sherpa-onnx install needs no network probe (the model-dir probe
    covers it), so empty ``api_base`` resolves immediately to ok. A non-empty
    api_base behaves like ``_run_tts_test``: auth check with a key,
    reachability without.
    """
    if not api_base:
        return {"ok": True, "reason": "local model (no key needed)", "status": 200}
    return await _auth_or_reachable(_origin_of(api_base), api_key)


# ============================================================================
# 2026-09-18 新增：获取上游模型列表（用户要求「透出模型列表」）
# ----------------------------------------------------------------------------
# 背景：底层管道早已存在 —— service_probe.py 的 _probe_llm 已经 GET
#   {api_base}/models 并解析出 data[].id 列表（其 :31-36），
#   但 admin_endpoints.py:178-182 在组装 /api/services/status 响应时
#   只取 status/reason，把 models 丢掉了，所以前端拿不到可选模型。
#
# 本端点直接复用同一套调用约定，把列表透出给前端，供「模型」输入框做候选下拉。
# 设计约束：
#   - 与 /api/services/test 一致：只读探测，永不写回配置（never mutates config）
#   - 与 /api/services/test 一致：坏输入 400，真实探测恒 200 + {ok:false, reason}
#   - api_key 只用于本次请求的 Authorization，不落盘、不记日志
# ============================================================================


async def _fetch_openai_models(api_base: str, api_key: str | None) -> dict:
    """GET ``{api_base}/models`` and return the model ids.

    Returns ``{"ok": bool, "models": [str], "status": int, "reason"?: str}``.
    Never raises: transport errors / timeouts / non-2xx all resolve to
    ``ok=False`` with a short reason.
    """
    url = api_base.rstrip("/") + "/models"
    headers = {}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    try:
        async with (
            aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=_TEST_TIMEOUT_S)) as session,
            session.get(url, headers=headers) as resp,
        ):
            if resp.status >= 400:
                reason = (await resp.text(errors="replace"))[:_REASON_MAX_CHARS].strip()
                return {
                    "ok": False,
                    "models": [],
                    "status": resp.status,
                    "reason": reason or ("HTTP %d" % resp.status),
                }
            try:
                body = await resp.json(content_type=None)
            except Exception as exc:  # 上游返回非 JSON
                return {
                    "ok": False,
                    "models": [],
                    "status": resp.status,
                    "reason": "invalid json: %s" % str(exc)[:120],
                }
            # OpenAI 兼容格式：{"data": [{"id": "..."}]}
            # 少数实现直接返回 {"models": [...]} 或裸数组，这里都兼容。
            rows = None
            if isinstance(body, dict):
                rows = body.get("data")
                if rows is None:
                    rows = body.get("models")
            elif isinstance(body, list):
                rows = body
            ids: list[str] = []
            if isinstance(rows, list):
                for m in rows:
                    if isinstance(m, dict):
                        mid = m.get("id") or m.get("name") or m.get("model")
                        if isinstance(mid, str) and mid:
                            ids.append(mid)
                    elif isinstance(m, str) and m:
                        ids.append(m)
            ids = sorted(set(ids))
            return {
                "ok": True,
                "models": ids,
                "status": resp.status,
                "reason": "" if ids else "upstream returned no model ids",
            }
    except Exception as exc:
        return {"ok": False, "models": [], "status": 0, "reason": str(exc)[:120]}


async def _services_list_models_handler(request: web.Request) -> web.Response:
    """POST /api/services/list-models — 列出上游可用模型。

    请求体：``{"api_base": "https://.../v1", "api_key": "..."}``（api_key 可选）
    响应：``{"ok": bool, "models": [str], "status": int, "reason"?: str}``

    纯探测，不读写任何持久化配置。空 api_base 返回 400（无从探测）。
    """
    try:
        payload = await request.json()
    except ValueError as exc:
        return web.json_response({"error": "bad json: %s" % exc}, status=400)
    if not isinstance(payload, dict):
        return web.json_response({"error": "payload must be a JSON object"}, status=400)

    api_base = payload.get("api_base")
    if not isinstance(api_base, str) or not api_base.strip():
        return web.json_response({"error": "api_base is required"}, status=400)
    api_key = payload.get("api_key")
    if api_key is not None and not isinstance(api_key, str):
        return web.json_response({"error": "api_key must be a string"}, status=400)

    result = await _fetch_openai_models(api_base.strip(), api_key or None)
    logger.info(
        "POST /api/services/list-models base=%s ok=%s n=%s",
        api_base.strip(),
        result.get("ok"),
        len(result.get("models") or []),
    )
    return web.json_response(result)
