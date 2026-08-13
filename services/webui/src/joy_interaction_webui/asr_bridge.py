# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Internal ASR bridge (WebUI <-> ASR engine) — split out of server.py.

The user-facing ``asr.api_base`` is an http(s) *provider* URL (standard
OpenAI-compatible contract). The WebUI never connects to it directly; it
always talks to a fixed internal websocket bridge, which forwards audio to
the configured upstream. The bridge endpoint is a code constant and is
NEVER exposed to the user (2026-08-11 contract fix).
"""

import logging
import os
import subprocess
import sys
import time

logger = logging.getLogger(__name__)

ASR_BRIDGE_PORT = int(os.getenv("ASR_ADAPTER_PORT", "8994"))
ASR_BRIDGE_WS = "ws://127.0.0.1:%d/ws/asr" % ASR_BRIDGE_PORT
ASR_BRIDGE_HTTP = "http://127.0.0.1:%d" % ASR_BRIDGE_PORT

_ASR_BRIDGE_PROC: "subprocess.Popen | None" = None
_ASR_BRIDGE_CFG: dict = {}


def _asr_bridge_venv() -> str:
    """Return a python interpreter able to run asr_adapter.py (fastapi/uvicorn)."""
    base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".venv")
    for sub in ("Scripts/python.exe", "bin/python"):
        cand = os.path.join(base, sub)
        if os.path.exists(cand):
            return cand
    return sys.executable


def _asr_bridge_ensure(api_base: str, model: str, api_key: str) -> None:
    """Start (or keep) the ASR bridge pointed at ``api_base``.

    Idempotent on identical config. Raises on launch/readiness failure so the
    caller's reachability gate surfaces it explicitly (no silent fallback).
    """
    global _ASR_BRIDGE_PROC, _ASR_BRIDGE_CFG
    want = {"api_base": api_base, "model": model or "", "api_key": api_key or ""}
    if _ASR_BRIDGE_PROC is not None and _ASR_BRIDGE_PROC.poll() is None and want == _ASR_BRIDGE_CFG:
        return
    _asr_bridge_stop()
    venv_py = _asr_bridge_venv()
    cwd = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "asr")
    env = dict(os.environ)
    env["ASR_UPSTREAM_URL"] = api_base
    env["ASR_MODEL"] = model or ""
    env["ASR_API_KEY"] = api_key or ""
    env["ASR_ADAPTER_PORT"] = str(ASR_BRIDGE_PORT)
    try:
        proc = subprocess.Popen(
            [
                venv_py,
                "asr_adapter.py",
                "serve",
                "--host",
                "127.0.0.1",
                "--port",
                str(ASR_BRIDGE_PORT),
            ],
            cwd=cwd,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as exc:
        logger.error("ASR bridge launch failed: %s", exc)
        raise
    _ASR_BRIDGE_PROC = proc
    _ASR_BRIDGE_CFG = want
    _asr_bridge_wait_ready(15.0)
    logger.info("ASR bridge up: upstream=%s model=%s", api_base, model or "(default)")


def _asr_bridge_stop() -> None:
    global _ASR_BRIDGE_PROC, _ASR_BRIDGE_CFG
    proc = _ASR_BRIDGE_PROC
    if proc is not None:
        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        except Exception as exc:
            logger.warning("ASR bridge stop: %s", exc)
    _ASR_BRIDGE_PROC = None
    _ASR_BRIDGE_CFG = {}


def _asr_bridge_wait_ready(timeout: float) -> None:
    import httpx

    deadline = time.monotonic() + timeout
    last_err = "n/a"
    while time.monotonic() < deadline:
        try:
            with httpx.Client(timeout=1.0) as client:
                resp = client.get(ASR_BRIDGE_HTTP + "/health")
            if resp.status_code == 200:
                return
            last_err = "http %d" % resp.status_code
        except Exception as exc:
            last_err = str(exc)[:120]
        time.sleep(0.5)
    raise RuntimeError("ASR bridge not ready after %.0fs: %s" % (timeout, last_err))


def _asr_bridge_sync() -> None:
    """Reconcile the bridge with the current saved asr config (startup/propagate)."""
    from . import server as _server

    asr_cfg = _server._services_config.get("asr", {}) or {}
    api_base = asr_cfg.get("api_base", "")
    if api_base:
        _asr_bridge_ensure(api_base, asr_cfg.get("model", ""), asr_cfg.get("api_key", ""))
    else:
        _asr_bridge_stop()
