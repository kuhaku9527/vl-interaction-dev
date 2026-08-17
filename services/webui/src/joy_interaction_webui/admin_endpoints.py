# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Status / proxy / propagation endpoints (split out of server.py).

Owns the services-config handlers (``/api/services/config``,
``/api/services/status``), the [Local Wiki] frontend gateway
(``/v1/providers/health`` etc.), the screen-latency sink, the extended-status
badge endpoint and ``_propagate_services_to_runtime``. Cross-module names that
the test suite patches through the server facade (``server._probe_*``,
``server.MEMORY_STORE_URL``, ``server._propagate_services_to_runtime``,
``server._webinfer_proxy_summarizer_routing``) are resolved lazily at call
time so the module-load graph stays acyclic.
"""

import asyncio
import datetime
import json
import logging
import os

import aiohttp
from aiohttp import web

from . import asr as asr_module
from .asr_bridge import _asr_bridge_sync
from .services_config import (
    _SERVICES_CONFIG_DEFAULTS,
    _persist_services_config,
    _services_config,
    _validate_and_apply_slot,
)

logger = logging.getLogger(__name__)

# -- [Local Wiki] frontend gateway endpoints (ADR-0012, task F4) ----------
# The webui is the single SPA entry point. It proxies the whitelisted /v1/*
# wiki endpoints to the memory-store service. Provider health (B3) and
# network settings (B4) are owned by the backend (#36); this gateway only
# forwards the F4 knowledge-base surface (namespaces / sync / ingest).

# v0.3 (2026-07-29): in-code default flipped from 8996 (empty shell) to 8997
# (real bge-m3 backend, D-L4-001). JOYAI_MEMORY_STORE_URL env still wins, so the
# run-windows.ps1 launcher can override per-deploy. Operators hitting the legacy
# 8996 shell (e.g. a sandboxed dev box) only need to set the env explicitly.
MEMORY_STORE_URL = os.environ.get("JOYAI_MEMORY_STORE_URL", "http://127.0.0.1:8997").rstrip("/")

# Issue #43 (final piece): persist browser-reported screen-frame send→render
# latency samples to a server-side JSONL ring file so the data survives a page
# refresh / webui process restart. Previously the samples only lived in the
# browser console + an in-memory ring (lost on refresh).
SCREEN_LATENCY_LOG = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..",
    "..",
    "..",
    "..",
    "logs",
    "screen_latency.jsonl",
)
SCREEN_LATENCY_RING_CAP = 2000
# Ensure the repo-root logs/ dir exists at startup. A genuinely unwritable
# logs/ is a real deployment error (project standard: raise, do not silently
# degrade), so this is intentionally run WITHOUT a try/except guard.
os.makedirs(os.path.dirname(SCREEN_LATENCY_LOG), exist_ok=True)

# Last asr subset we propagated, used to detect real changes and avoid
# needless reconnect logging on every PUT. Seeded from the current asr slot
# so the first _propagate_services_to_runtime() call does not false-trigger
# invalidate_asr_client() / reconnect logging.
_last_asr_propagated: dict = {
    "api_base": _services_config.get("asr", {}).get("api_base", ""),
    "api_key": _services_config.get("asr", {}).get("api_key", ""),
    "model": _services_config.get("asr", {}).get("model", ""),
}


async def _services_config_handler(request):
    if request.method == "GET":
        return web.json_response(dict(_services_config))
    if request.method == "PUT":
        try:
            payload = await request.json()
        except Exception as exc:
            return web.json_response({"error": "bad json: %s" % exc}, status=400)
        if not isinstance(payload, dict):
            return web.json_response({"error": "payload must be a JSON object"}, status=400)

        loop = asyncio.get_running_loop()
        invalid: list[dict] = []
        applied_any = False
        # 遍历所有已知槽位（_SERVICES_CONFIG_DEFAULTS 含 N7.1 的 agent/embedding）；
        # 未知槽位（不在默认表）一律忽略——不能注入运行时配置。
        for slot in _SERVICES_CONFIG_DEFAULTS:
            incoming = payload.get(slot)
            if not isinstance(incoming, dict):
                continue
            entry, applied = await _validate_and_apply_slot(slot, incoming, loop)
            if entry is not None:
                invalid.append(entry)
            if applied:
                applied_any = True

        # Persist + propagate only when at least one valid change landed.
        # Invalid slots were rejected above (never persisted) — the validation
        # gate guarantees we never silently store an invalid config.
        if applied_any:
            try:
                _persist_services_config()
            except OSError as exc:
                logger.error("failed to persist services config: %s", exc)
                return web.json_response(
                    {
                        "error": "persist failed: %s" % exc,
                        "slot": None,
                        "field": None,
                        "reason": str(exc)[:200],
                    },
                    status=500,
                )
            from . import server as _server

            await _server._propagate_services_to_runtime()

        if invalid:
            # 约法三章②: invalid config MUST surface an explicit 4xx, never a
            # silent 200. Valid slots (if any) were already applied + persisted.
            first = invalid[0]
            logger.warning(
                "PUT /api/services/config rejected slot=%s field=%s: %s",
                first["slot"],
                first["field"],
                first["reason"],
            )
            return web.json_response(
                {
                    "error": first["error"],
                    "slot": first["slot"],
                    "field": first["field"],
                    "reason": first["reason"],
                },
                status=first["status"],
            )
        return web.json_response(dict(_services_config))
    return web.json_response({"error": "method not allowed"}, status=405)


async def _services_status_handler(request):
    """Normalize the 4 probe results into {ok, reason, endpoint} so the
    UI can read a single shape (item.ok ? "OK" : "ERR", reason tooltip).
    """
    from . import server as _server

    llm_cfg = _services_config.get("llm", {})
    summary_cfg = _services_config.get("summary", {})
    tts_cfg = _services_config.get("tts", {})
    asr_cfg = _services_config.get("asr", {})
    tts_url = tts_cfg.get("api_base") or os.environ.get(
        "JARVIS_TTS_API_URL", "http://127.0.0.1:8985/v1/synthesize"
    )
    # Each probe uses sync httpx with a 2-3s timeout; running them inline
    # would block the aiohttp event loop for up to ~9s. Dispatch them to
    # the default executor and gather so the worst case is the slowest probe.
    loop = asyncio.get_running_loop()
    llm_future = loop.run_in_executor(
        None, _server._probe_llm, llm_cfg.get("api_base", "http://127.0.0.1:8070/v1")
    )
    summary_future = loop.run_in_executor(None, _server._probe_summary, summary_cfg)
    tts_future = loop.run_in_executor(None, _server._probe_tts, tts_url)
    asr_future = loop.run_in_executor(None, _server._probe_asr, asr_cfg)
    # N7.1: agent / embedding 探活（async 直连后端，3s 超时；挂了不炸，返回 ERR）。
    agent_future = _probe_agent_route()
    embedding_future = _probe_embedding_health()
    llm_raw, summary_raw, tts_raw, asr_raw, agent_raw, embedding_raw = await asyncio.gather(
        llm_future, summary_future, tts_future, asr_future, agent_future, embedding_future
    )
    return web.json_response(
        {
            "llm": {
                "ok": llm_raw.get("status") == "ok",
                "reason": llm_raw.get("reason", ""),
                "endpoint": llm_cfg.get("api_base", "") + "/models",
            },
            "summary": summary_raw,
            "tts": {
                "ok": tts_raw.get("status") == "ok",
                "reason": tts_raw.get("reason", ""),
                "endpoint": tts_raw.get("endpoint", tts_url),
            },
            "asr": asr_raw,
            "agent": agent_raw,
            "embedding": embedding_raw,
        }
    )


async def _probe_agent_route() -> dict:
    """Probe background-agent provider route (GET /v1/provider/route)."""
    from . import server as _server

    base = _server._bg_agent_base_url()
    try:
        async with (
            aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3.0)) as session,
            session.get(base + "/v1/provider/route") as resp,
        ):
            if resp.status == 200:
                body = await resp.json(content_type=None)
                provider = (body or {}).get("provider", "")
                return {"ok": True, "reason": f"provider={provider}"}
            return {"ok": False, "reason": f"HTTP {resp.status}"}
    except Exception as exc:
        return {"ok": False, "reason": str(exc)[:120]}


async def _probe_embedding_health() -> dict:
    """Probe memory-store provider health (GET /v1/providers/health)."""
    from . import server as _server

    base = _server.MEMORY_STORE_URL.rstrip("/")
    try:
        async with (
            aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3.0)) as session,
            session.get(base + "/v1/providers/health") as resp,
        ):
            if resp.status == 200:
                return {"ok": True, "reason": "memory-store reachable"}
            return {"ok": False, "reason": f"HTTP {resp.status}"}
    except Exception as exc:
        return {"ok": False, "reason": str(exc)[:120]}


def _append_screen_latency(record: dict) -> None:
    """Append one screen-latency sample as a JSON line and trim the ring.

    Parameters
    ----------
    record: dict
        Serializable sample (seq, send_to_render_ms, ts, text_len, received_at).

    Notes
    -----
    When the file exceeds ``SCREEN_LATENCY_RING_CAP`` lines it is rewritten
    keeping only the most recent entries. Volume is low and the webui runs
    single-process, so this simple full-rewrite trim is acceptable.
    """
    os.makedirs(os.path.dirname(SCREEN_LATENCY_LOG), exist_ok=True)
    with open(SCREEN_LATENCY_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    # lightweight trim (low volume; single-process aiohttp is fine)
    try:
        with open(SCREEN_LATENCY_LOG, encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > SCREEN_LATENCY_RING_CAP:
            with open(SCREEN_LATENCY_LOG, "w", encoding="utf-8") as f:
                f.writelines(lines[-SCREEN_LATENCY_RING_CAP:])
    except OSError as exc:
        logger.error("[screen-latency] trim failed: %s", exc)


async def _screen_latency_handler(request: web.Request) -> web.Response:
    """Persist one browser screen-frame send→render latency sample.

    Expects a JSON body with ``send_to_render_ms`` (number) and ``seq``
    (required). On success a JSONL record is appended to ``SCREEN_LATENCY_LOG``
    and ``204`` is returned. Malformed / invalid input returns ``4xx`` with a
    warning log; a write failure returns ``500``.
    """
    try:
        payload = await request.json()
    except Exception:
        logger.warning("[screen-latency] bad JSON body")
        return web.json_response({"ok": False, "error": "bad_json"}, status=400)
    s2r = payload.get("send_to_render_ms")
    seq = payload.get("seq")
    if not isinstance(s2r, (int, float)) or seq is None:
        logger.warning("[screen-latency] missing/invalid fields seq=%r s2r=%r", seq, s2r)
        return web.json_response({"ok": False, "error": "invalid_fields"}, status=400)
    record = {
        "seq": seq,
        "send_to_render_ms": round(float(s2r), 2),
        "ts": payload.get("ts"),
        "text_len": payload.get("text_len"),
        "received_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    try:
        _append_screen_latency(record)
    except OSError as exc:
        logger.error("[screen-latency] append failed: %s", exc)
        return web.json_response({"ok": False, "error": "write_failed"}, status=500)
    logger.debug("[screen-latency] sample seq=%s s2r=%s", seq, record["send_to_render_ms"])
    # RFC 7231: 204 MUST NOT include a message body, so return an empty response.
    return web.Response(status=204)


async def _proxy_to_memory_store(request: web.Request) -> web.Response:
    """Forward whitelisted [Local Wiki] /v1/* endpoints to memory-store.

    Only the three UI-facing wiki endpoints are proxied; memory-store's
    internal /v1/blocks/* surface is intentionally NOT exposed to the SPA.
    """
    from . import server as _server

    target = _server.MEMORY_STORE_URL + request.path
    if request.query_string:
        target += "?" + request.query_string
    try:
        body = await request.read()
        headers = {
            k: v for k, v in request.headers.items() if k.lower() not in ("host", "content-length")
        }
        async with (
            aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session,
            session.request(request.method, target, data=body or None, headers=headers) as resp,
        ):
            resp_body = await resp.read()
            # Strip any "; charset=..." — aiohttp's content_type arg rejects it
            # (it adds charset itself), and FastAPI/memory-store send it.
            ct = resp.headers.get("Content-Type", "application/json")
            ct = ct.split(";", 1)[0].strip() or "application/json"
            return web.Response(
                status=resp.status,
                body=resp_body,
                content_type=ct,
            )
    except Exception as exc:
        logger.warning("memory-store proxy %s failed: %s", request.path, exc)
        return web.json_response(
            {"error": "memory-store unreachable", "reason": str(exc)[:160]}, status=502
        )


async def extended_status(request: web.Request) -> web.Response:
    """Aggregate Memory-store + [Local Wiki] status for the header badges (#46).

    Three visual states per service:
      - enabled=False            -> gray  "未启用"  (opt-in flag off)
      - enabled=True & !reachable -> gray  "离线"    (probe failed)
      - enabled=True & reachable & ok=False -> red "异常" (health error)
      - enabled=True & reachable & ok=True  -> green "在线"
    Wiki recall (``WIKI_RECALL_ENABLED``) is configured in webinfer, but its
    corpus backend IS memory-store, so wiki reachability/health reuses the same
    probe. Probe failures are reported explicitly (never a silent 500).
    """
    from . import server as _server

    memory_enabled = os.environ.get("JOYAI_ENABLE_MEMORY_STORE") == "1"
    wiki_enabled = os.environ.get("WIKI_RECALL_ENABLED") == "1"
    ok: bool | None = None
    reachable = False
    reason = ""
    latency_ms = None
    try:
        async with (
            aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2)) as session,
            session.get(_server.MEMORY_STORE_URL + "/health") as resp,
        ):
            if resp.status == 200:
                data = await resp.json()
                ok = bool(data.get("ok"))
                reachable = True
                latency_ms = data.get("latency_ms")
                if not ok:
                    reason = str(data.get("error") or data.get("hint") or "health reported not ok")
            else:
                reason = f"memory-store /health HTTP {resp.status}"
    except Exception as exc:
        reachable = False
        ok = None
        reason = str(exc)
        logger.warning("extended_status: memory-store probe failed: %s", exc)
    payload = {
        "memory": {
            "enabled": memory_enabled,
            "ok": ok,
            "reachable": reachable,
            "reason": reason,
            "latency_ms": latency_ms,
        },
        "wiki": {
            "enabled": wiki_enabled,
            "ok": ok,
            "reachable": reachable,
            "reason": reason,
            "latency_ms": latency_ms,
        },
    }
    return web.json_response(payload)


async def _ingest_text_handler(request: web.Request) -> web.Response:
    """POST /v1/external/ingest-text (F4 pasted-markdown entry).

    The browser cannot write files, so the webui gateway accepts raw markdown
    text, stages it as a single .md under a temp dir, and forwards to the
    memory-store sync endpoint (the single ingest path). The temp dir is
    removed after the upstream call resolves.
    """
    from . import server as _server

    try:
        payload = await request.json()
    except Exception as exc:
        return web.json_response({"error": "bad json: %s" % exc}, status=400)
    namespace = (payload.get("namespace") or "").strip()
    text = payload.get("text") or ""
    if not namespace:
        return web.json_response({"error": "namespace required"}, status=422)
    if not text.strip():
        return web.json_response({"error": "text required"}, status=422)
    import tempfile

    tmp = tempfile.mkdtemp(prefix="joyai-wiki-")
    md_path = os.path.join(tmp, "paste.md")
    try:
        with open(md_path, "w", encoding="utf-8") as fh:
            fh.write(text)
        async with (
            aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session,
            session.post(
                _server.MEMORY_STORE_URL + "/v1/external/sync",
                json={"namespace": namespace, "dir": tmp, "drop_first": False},
            ) as resp,
        ):
            upstream = await resp.json(content_type=None)
            return web.json_response(upstream, status=resp.status)
    except Exception as exc:
        logger.warning("ingest-text failed: %s", exc)
        return web.json_response(
            {"error": "memory-store unreachable", "reason": str(exc)[:160]}, status=502
        )
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)


async def _propagate_services_to_runtime():
    """Push the saved llm/summary config into live service instances.

    - LLM: update every session VLMService (api_base + model + api_key).
    - Summary: webinfer owns the summarizer; webui cannot reach into it.
      We log the change so the operator can restart webinfer if needed.
    - TTS / ASR: read on demand by JarvisConfig.from_env(); changes take
      effect for the NEXT session that calls from_env().

    Async because the ASR bridge start/stop (_asr_bridge_sync) is a blocking
    subprocess op (up to 15s readiness poll) that must run off the aiohttp
    event loop. Always invoked from the event loop (PUT handler).
    """
    global _last_asr_propagated
    from . import server as _server

    loop = asyncio.get_running_loop()
    try:
        llm_cfg = _services_config.get("llm", {})
        api_base = llm_cfg.get("api_base")
        model = llm_cfg.get("model")
        api_key = llm_cfg.get("api_key")
        if api_base:
            for _sid, sess in _server.sessions.items():
                vlm = sess.get("vlm_service") if isinstance(sess, dict) else None
                if vlm and hasattr(vlm, "update_api_settings"):
                    vlm.update_api_settings(api_base=api_base, api_key=api_key)
                if vlm and model and hasattr(vlm, "set_model"):
                    vlm.set_model(model)
            _server.default_vlm_config["api_base"] = api_base
            if model:
                _server.default_vlm_config["model"] = model
    except Exception as exc:
        logger.warning("propagate llm config: %s", exc)
    try:
        os.environ["JARVIS_TTS_API_URL"] = _services_config.get("tts", {}).get(
            "api_base", ""
        ) or os.environ.get("JARVIS_TTS_API_URL", "http://127.0.0.1:8985/v1/synthesize")
        asr_cfg = _services_config.get("asr", {})
        # Mirror the model into the env for legacy callers. The authoritative
        # source is asr_cfg["model"] (slot["model"] in asr._asr_cfg()); the
        # ASR_MODEL_DIR env var is only a fallback used when the slot is empty.
        if asr_cfg.get("model"):
            os.environ["ASR_MODEL_DIR"] = asr_cfg["model"]
    except Exception as exc:
        logger.warning("propagate tts/asr config: %s", exc)
    # ASR: connect_asr reads the live config on every new browser session, so
    # hot-reload needs no persistent client. We only bump the invalidation epoch
    # when the asr slot actually changed, to avoid needless reconnect logging.
    # No silent local fallback: an invalid url/key still raises on connect.
    try:
        asr_cfg = _services_config.get("asr", {}) or {}
        # Bridge start/stop is a blocking subprocess op (up to 15s readiness
        # poll); run it off the aiohttp event loop. Per code-review BLOCKING fix.
        await loop.run_in_executor(None, _asr_bridge_sync)
        asr_subset = {
            "api_base": asr_cfg.get("api_base", ""),
            "api_key": asr_cfg.get("api_key", ""),
            "model": asr_cfg.get("model", ""),
        }
        if asr_subset != _last_asr_propagated:
            _last_asr_propagated = dict(asr_subset)
            asr_module.invalidate_asr_client()
            logger.info(
                "ASR config propagated (url set=%s, key set=%s); next ASR session reconnects",
                bool(asr_subset["api_base"]),
                bool(asr_subset["api_key"]),
            )
    except Exception as exc:
        logger.warning("propagate asr config: %s", exc)
    summary_cfg = _services_config.get("summary", {})
    if summary_cfg.get("api_base") or summary_cfg.get("model") or summary_cfg.get("api_key"):
        # Fire-and-forget; the PUT /api/services/config caller does not
        # need to wait for webinfer. If webinfer is down, the warning
        # is logged in the proxy and the saved config is still applied.
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None and loop.is_running():
            loop.create_task(_server._webinfer_proxy_summarizer_routing(summary_cfg))
        # else: no live event loop here (e.g. unit test sync invocation);
        # the next PUT will retry the propagation.

    # N7.1: agent provider 热推（webui -> background-agent :8079
    # POST /v1/provider/route）。Fire-and-forget；background-agent 挂了则
    # 下次 PUT 重试（proxy 内记 WARNING，已保存配置不受影响）。
    agent_cfg = _services_config.get("agent", {})
    if (agent_cfg.get("provider") or "").strip():
        loop.create_task(_server._bg_agent_provider_routing(agent_cfg))

    # N7.1: embedding provider 热推（webui -> memory-store :8997
    # POST /v1/settings/embedding）。memory-store 自带 D-080 校验
    # （未知 provider 400 / 探活失败 502 不换），这里 fire-and-forget。
    embed_cfg = _services_config.get("embedding", {})
    if (embed_cfg.get("provider") or "").strip():
        loop.create_task(_push_embedding_provider(embed_cfg))


async def _push_embedding_provider(embed_cfg: dict) -> None:
    """Fire-and-forget: POST memory-store /v1/settings/embedding (N7.1).

    memory-store rebuilds its in-process embedder on this endpoint; any
    failure (unknown provider / unhealthy) is rejected there and logged here.
    """
    from . import server as _server

    provider = (embed_cfg.get("provider") or "").strip()
    if not provider:
        return
    base = _server.MEMORY_STORE_URL.rstrip("/")
    payload = {
        "provider": provider,
        "api_key": embed_cfg.get("api_key") or None,
    }
    try:
        async with (
            aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5.0)) as session,
            session.post(base + "/v1/settings/embedding", json=payload) as resp,
        ):
            if resp.status not in (200, 201):
                text = (await resp.text())[:200]
                logger.warning(
                    "memory-store embedding route rejected (%s): %s", resp.status, text
                )
    except Exception as exc:
        logger.warning("memory-store embedding route push failed: %s", exc)
