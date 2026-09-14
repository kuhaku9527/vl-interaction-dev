# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Service reachability probes + status endpoints (split out of server.py).

Owns the LLM / TTS / KWS / summary / ASR reachability probes and the
``/api/llm/status`` + ``/api/tts/health`` handlers. Cross-module constants
that stay owned by ``server`` (e.g. ``ASR_BRIDGE_HTTP`` before the asr_bridge
split) are resolved through the server facade lazily at call time, keeping the
module-load graph acyclic while preserving the exact monkeypatch contract
exercised by the test suite (probes are patchable via ``server._probe_*``).
"""

import asyncio
import logging
import os
import time

from aiohttp import web

logger = logging.getLogger(__name__)


def _probe_llm(llm_api_url):
    import httpx

    try:
        with httpx.Client(timeout=2.0) as client:
            resp = client.get(llm_api_url.rstrip("/") + "/models")
        if resp.status_code == 200:
            try:
                data = resp.json()
                models = data.get("data") or []
                return {
                    "status": "ok",
                    "models": [m.get("id", "") for m in models if isinstance(m, dict)],
                }
            except Exception as exc:
                return {"status": "degraded", "reason": "parse: %s" % exc}
        return {"status": "error", "reason": "http %d" % resp.status_code}
    except Exception as exc:
        return {"status": "error", "reason": str(exc)[:120]}


def _probe_tts(tts_api_url):
    # Probe the voice_clone_api ``/health`` endpoint first; if absent,
    # fall back to a GET on ``/v1/synthesize`` (POST-only, so 405 also
    # counts as "endpoint present"). Two short-lived clients per probe
    # to avoid any keep-alive edge cases.
    from urllib.parse import urlsplit, urlunsplit

    import httpx

    parsed = urlsplit(tts_api_url.rstrip("/"))
    service_root = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    health_url = service_root + "/health" if service_root else None
    synth_url = tts_api_url.rstrip("/")
    for url in (h for h in (health_url, synth_url) if h):
        try:
            with httpx.Client(timeout=3.0) as client:
                resp = client.get(url)
        except Exception as exc:
            logger.warning("TTS health check failed for %s: %s", url, exc)
            continue
        if resp.status_code == 200:
            return {"status": "ok", "endpoint": url, "code": 200}
        if "synthesize" in url and resp.status_code in (405, 422):
            return {"status": "ok", "endpoint": url, "code": resp.status_code, "note": "POST-only"}
    return {"status": "error", "reason": "unreachable"}


def _probe_kws(kws_model_dir):
    from pathlib import Path

    p = Path(kws_model_dir)
    if not p.exists():
        return {"status": "missing", "reason": "dir not found: %s" % kws_model_dir}
    matches = list(p.glob("encoder*chunk-*.onnx"))
    if not matches and all(
        (p / name).exists() for name in ("encoder.onnx", "decoder.onnx", "joiner.onnx")
    ):
        matches = [p / "encoder.onnx"]
    if not matches:
        return {"status": "missing", "reason": "no encoder*.onnx in %s" % kws_model_dir}
    return {"status": "ok", "model": matches[0].name}


_LLM_PROBE_CACHE = {"payload": None, "ts": 0.0}
_LLM_PROBE_TTL_S = 5.0


def _now():
    return time.time()


def _resolve_service_targets(app):
    from .jarvis_mode import JarvisConfig

    cfg = JarvisConfig.from_env()
    return cfg.llm_api_url, cfg.tts_api_url


async def llm_status(request):
    from . import server as _server

    llm_url, tts_url = _server._resolve_service_targets(request.app)
    kws_dir = os.environ.get("JARVIS_KWS_MODEL_DIR", "D:/AI/models/sherpa-onnx/models/kws/bt-en")
    now = _server._now()
    cached = _server._LLM_PROBE_CACHE
    if cached["payload"] is not None and (now - cached["ts"]) < _server._LLM_PROBE_TTL_S:
        llm_payload = dict(cached["payload"])
    else:
        llm_payload = _server._probe_llm(llm_url)
        cached["payload"] = llm_payload
        cached["ts"] = now
    loop = asyncio.get_running_loop()
    tts_future = loop.run_in_executor(None, _server._probe_tts, tts_url)
    kws_future = loop.run_in_executor(None, _server._probe_kws, kws_dir) if kws_dir else None
    tts_payload, kws_payload = await asyncio.gather(
        tts_future,
        kws_future
        if kws_future is not None
        else asyncio.sleep(
            0, result={"status": "missing", "reason": "kws_model_dir not configured"}
        ),
    )
    overall = "ok"
    for p in (llm_payload, tts_payload, kws_payload):
        if p.get("status") in ("error", "missing"):
            overall = "error"
            break
        if p.get("status") == "degraded" and overall == "ok":
            overall = "degraded"
    return web.json_response(
        {
            "ts": now,
            "overall": overall,
            "llm": {"url": llm_url, **llm_payload},
            "tts": {"url": tts_url, **tts_payload},
            "kws": {"model_dir": kws_dir, **kws_payload},
        }
    )


async def tts_health(request):
    from . import server as _server

    _llm_url, tts_url = _server._resolve_service_targets(request.app)
    payload = await asyncio.get_running_loop().run_in_executor(None, _server._probe_tts, tts_url)
    return web.json_response({"ts": _server._now(), "url": tts_url, **payload})


def _probe_summary(summary_cfg):
    """Lightweight reachability probe for the summary model endpoint.
    Mirrors _probe_llm but with a stricter timeout and tolerates non-model
    responses (501 / 404 / etc). Anything that returns JSON is "ok".
    """
    import httpx

    api_base = (summary_cfg or {}).get("api_base", "").rstrip("/")
    if not api_base:
        return {"ok": False, "reason": "api_base empty"}
    try:
        with httpx.Client(timeout=2.0) as client:
            resp = client.get(api_base + "/models")
        if resp.status_code == 200:
            return {"ok": True, "endpoint": api_base + "/models", "code": 200}
        return {"ok": False, "reason": "http %d" % resp.status_code}
    except Exception as exc:
        return {"ok": False, "reason": str(exc)[:120]}


def _probe_asr(asr_cfg):
    """Probe reachability of the ASR slot.

    The user-facing ``api_base`` is an http(s) *provider* URL. The WebUI does
    not connect to it directly; it connects to a fixed internal bridge
    (``ASR_BRIDGE_HTTP``), which the server keeps pointed at the upstream. So:

    - empty api_base  -> local in-process paraformer is the intended primary
                         path; this is a valid state (ok, no probe).
    - http(s)://       -> probe the internal bridge ``/health`` (the WebUI's
                         actual connection target), not the upstream.
    - ws(s)://          -> operator override (ASR_URL env); not probed, treated ok.

    When ``JARVIS_ASR_PROVIDER=cloud`` the jarvis/live dialog ASR also runs
    against an upstream (``ASR_UPSTREAM_URL`` / the WebUI slot); that path is
    probed explicitly (spec ``asr-provider-unified.md`` §4) instead of
    the local-model branch below.
    """
    provider = os.environ.get("JARVIS_ASR_PROVIDER", "local").strip().lower()
    if provider == "cloud":
        return _probe_asr_cloud(asr_cfg)
    api_base = (asr_cfg or {}).get("api_base", "")
    if not api_base:
        return {"ok": True, "note": "local in-process paraformer"}
    if api_base.startswith("http://") or api_base.startswith("https://"):
        import httpx

        from . import server as _server

        bridge_http = _server.ASR_BRIDGE_HTTP
        try:
            with httpx.Client(timeout=2.0) as client:
                resp = client.get(bridge_http + "/health")
            if resp.status_code == 200:
                return {"ok": True, "endpoint": bridge_http + "/health", "code": 200}
            return {"ok": False, "reason": "http %d (asr bridge)" % resp.status_code}
        except Exception as exc:
            return {"ok": False, "reason": str(exc)[:120]}
    if api_base.startswith("ws://") or api_base.startswith("wss://"):
        return {"ok": True, "endpoint": api_base, "note": "external ws override (not probed)"}
    return {"ok": False, "reason": "api_base must be http(s) or ws override"}


def _probe_asr_cloud(asr_cfg):
    """Probe the cloud ASR upstream used by jarvis/live (D-080 visible state).

    The WebUI call path connects through the internal bridge when an http(s)
    ``api_base`` is configured; the jarvis/live ``CloudBatchProvider`` POSTs
    directly to ``ASR_UPSTREAM_URL``. Both are probed: the bridge ``/health``
    first, then a direct GET on the upstream — any <500 response proves the
    endpoint is reachable (an OpenAI transcriptions route answers 4xx/405 to
    GET, which is fine).
    """
    import httpx

    api_base = (asr_cfg or {}).get("api_base", "")
    upstream_url = ""
    if api_base.startswith("http://") or api_base.startswith("https://"):
        from . import server as _server

        bridge_http = _server.ASR_BRIDGE_HTTP
        try:
            with httpx.Client(timeout=2.0) as client:
                resp = client.get(bridge_http + "/health")
            if resp.status_code != 200:
                return {"ok": False, "reason": "http %d (asr bridge)" % resp.status_code}
        except Exception as exc:
            return {"ok": False, "reason": str(exc)[:120]}
        upstream_url = api_base
    else:
        upstream_url = os.environ.get("ASR_UPSTREAM_URL", "").strip()
    if not upstream_url:
        return {
            "ok": False,
            "reason": "JARVIS_ASR_PROVIDER=cloud but no upstream configured (set ASR_UPSTREAM_URL)",
        }
    try:
        with httpx.Client(timeout=2.0) as client:
            resp = client.get(upstream_url)
        if resp.status_code < 500:
            return {"ok": True, "upstream": upstream_url, "code": resp.status_code}
        return {"ok": False, "reason": "http %d (upstream)" % resp.status_code}
    except Exception as exc:
        return {"ok": False, "reason": str(exc)[:120]}
