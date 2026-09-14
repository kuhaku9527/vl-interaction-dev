# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Radio-silence proxy (webui -> webinfer /v1/live/silence).

The radio-silence feature (``doc/specs/radio-silence.md``) splits
ownership across services:

- **webui** persists the silence *settings* (``config/services.json``
  ``silence`` slot, owned by ``services_config``) and exposes
  ``GET/POST /api/live/silence`` to the SPA;
- **webinfer** owns the live ``suppressed`` state bit, the wake-phrase
  detection and the T1/T2 timers — the webui forwards to its
  ``/v1/live/silence`` endpoint (single-owner principle, same as the
  summarizer-route proxy in ``webinfer_proxy.py``).

Fail-open contract (spec §7): when webinfer is unreachable, GET returns the
local last-known ``suppressed`` snapshot + persisted settings with
``source: "local"`` (never a 500), and POST still persists the settings and
answers 502. Radio silence is an opt-in convenience, not a hard dependency.

Wake-audio reuse point (spec §5, no jarvis logic change here): the silence
wake response reuses the existing pre-recorded wake.wav playback —
``jarvis_mode._play_wake_wav()`` (jarvis_mode.py) -> ``_play_event_wav``
streams ``config.wake_wav`` (``prompts/bt/events/wake.wav``, resolved by
``jarvis_config.JarvisConfig.__post_init__`` via ``events_dir``) through the
``audio_output`` callback at zero token cost. The ``wake_from_silence`` path
can call the same helper when a name match fires while suppressed. The wav
itself is a TODO (see ``prompts/bt/events/README.txt``): replace with a real
TTS "我在,铁驭" — never commit a fake/silent wav.
"""

import asyncio
import logging

import aiohttp
from aiohttp import web

logger = logging.getLogger(__name__)

#: Last-known live suppressed state (fail-open GET fallback when webinfer is
#: unreachable). Never persisted: radio silence does not survive a restart
#: (spec §2) — this is only the in-process snapshot.
_silence_suppressed: bool = False

#: Settings keys webinfer owns (``silence_control.DEFAULT_SILENCE_SETTINGS``).
#: ``hotkey`` is webui/frontend-owned (a keyboard binding the browser listens
#: for) and is intentionally NOT forwarded — webinfer would only log an
#: "unknown setting ignored" warning for it.
_WEBINFER_SILENCE_KEYS: tuple[str, ...] = (
    "asr_enabled",
    "kws_enabled",
    "timeout_hint_enabled",
    "timeout_hint_minutes",
    "auto_wake_enabled",
    "auto_wake_minutes",
)


def _silence_base_url() -> str:
    """webinfer base URL for the /v1/live/silence proxy.

    Reuses the same gateway resolution as the summarizer-route proxy
    (``WEBINFER_URL`` env var, else the LLM api_base stripped of ``/v1``).
    """
    from . import server as _server

    return _server._webinfer_base_url()


def _silence_settings_snapshot() -> dict:
    """Return a shallow copy of the persisted silence settings.

    Decoupled from the live ``_services_config`` dict so the response
    serializer cannot accidentally expose later in-place mutations.
    """
    from . import server as _server

    return dict(_server._services_config.get("silence", {}))


async def _silence_forward_kws_event() -> dict:
    """Forward one KWS wake event to webinfer (in-process, no HTTP round-trip).

    Called by the live session's silence-KWS listener (B2) so a wake-word hit
    reaches webinfer's ``_silence_process_kws_event``. Returns the webinfer
    snapshot (``{suppressed, settings, wake_pending, ...}``) or an error dict
    when unreachable — the caller decides the wake ceremony. Reuses the same
    gateway + fail-open logging as the HTTP POST path.
    """
    global _silence_suppressed
    from . import server as _server

    base = _server._silence_base_url()
    try:
        async with (
            aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5.0)) as session,
            session.post(base + "/v1/live/silence", json={"kws_event": True}) as resp,
        ):
            body = await resp.json(content_type=None)
            result = dict(body or {})
            if isinstance(result.get("suppressed"), bool):
                _silence_suppressed = result["suppressed"]
            if resp.status >= 400:
                logger.warning(
                    "webinfer /v1/live/silence kws_event returned %d: %s",
                    resp.status,
                    str(body)[:200],
                )
            return result
    except (aiohttp.ClientError, OSError, ValueError) as exc:
        logger.warning("webinfer /v1/live/silence kws_event unreachable: %s", exc)
        return {"error": "webinfer unreachable", "reason": str(exc)[:200]}


def _propagate_silence_to_live_sessions(app, suppressed: bool, kws_enabled: bool) -> None:
    """Mirror the silence state onto every live session (B2 KWS listener).

    The live session's in-process KWS listener only runs while suppressed AND
    kws_enabled, so the webui must push both values when they change. Fail-open
    across the manager boundary (tests / non-live apps have no manager): each
    session is updated independently and a failure only logs.
    """
    manager = app.get("jarvis_manager")
    if manager is None:
        return
    for sid in manager.live_session_ids():
        session = manager.get_live_session(sid)
        sm = getattr(session, "state_machine", None)
        if sm is None:
            continue
        try:
            sm.set_silence_state(suppressed, kws_enabled)
        except Exception as exc:  # noqa: BLE001 - cross-module boundary: one
            # session's failure must not break the fan-out to the others.
            logger.warning("silence state propagate to live session %s failed: %s", sid, exc)


async def _silence_handler(request: web.Request) -> web.Response:
    """GET / POST /api/live/silence.

    GET:  ``{suppressed, settings, source}`` — ``suppressed`` comes from
          webinfer (the live-state owner), ``settings`` from the persisted
          local file. Fail-open: webinfer unreachable -> 200 with the
          last-known snapshot and ``source: "local"``.
    POST: body ``{suppressed?: bool, settings?: {...}}``. Settings are
          validated + persisted to ``config/services.json`` first (same gate
          as PUT /api/services/config), then the whole payload is forwarded to
          webinfer. Webinfer unreachable -> 502 with the settings still saved
          (fire-and-forget + warning log, same propagation precedent as the
          summarizer-route proxy).
    """
    if request.method == "POST":
        return await _silence_post(request)
    return await _silence_get(request)


async def _silence_get(request: web.Request) -> web.Response:
    """GET half: webinfer live state + local persisted settings."""
    global _silence_suppressed
    from . import server as _server

    settings = _silence_settings_snapshot()
    base = _server._silence_base_url()
    try:
        async with (
            aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5.0)) as session,
            session.get(base + "/v1/live/silence") as resp,
        ):
            if resp.status == 200:
                body = await resp.json(content_type=None)
                suppressed = (body or {}).get("suppressed", _silence_suppressed)
                if isinstance(suppressed, bool):
                    _silence_suppressed = suppressed
                return web.json_response(
                    {
                        "suppressed": _silence_suppressed,
                        "settings": settings,
                        # T1 hint one-shot (spec §6): the webui status poll
                        # consumes webinfer's hint_pending / hint_count so the
                        # "仍在静默中" audio can fire exactly once.
                        "hint_pending": bool((body or {}).get("hint_pending", False)),
                        "hint_count": int((body or {}).get("hint_count", 0) or 0),
                        "wake_pending": bool((body or {}).get("wake_pending", False)),
                        "source": "webinfer",
                    }
                )
            text = (await resp.text())[:200]
            logger.warning("webinfer /v1/live/silence GET returned %d: %s", resp.status, text)
    except (aiohttp.ClientError, OSError, ValueError) as exc:
        # Network boundary: connection refused / timeout / bad-json body all
        # fall back to the local snapshot (fail-open, never a 500).
        logger.warning("webinfer /v1/live/silence unreachable (fail-open): %s", exc)
    return web.json_response(
        {
            "suppressed": _silence_suppressed,
            "settings": settings,
            # Stable field set on the local snapshot too, so the frontend can
            # always read hint_pending / hint_count / wake_pending without
            # branching on source (B1 relies on hint_count tracking).
            "hint_pending": False,
            "hint_count": 0,
            "wake_pending": False,
            "source": "local",
            "warning": "webinfer unreachable; showing last-known state",
        }
    )


async def _silence_post(request: web.Request) -> web.Response:
    """POST half: persist settings (durable) then forward to webinfer (live)."""
    global _silence_suppressed
    from . import server as _server

    try:
        payload = await request.json()
    except (ValueError, aiohttp.ClientError):
        return web.json_response({"error": "invalid json"}, status=400)
    if not isinstance(payload, dict):
        return web.json_response({"error": "payload must be a JSON object"}, status=400)

    suppressed = payload.get("suppressed")
    settings = payload.get("settings")
    if suppressed is not None and not isinstance(suppressed, bool):
        return web.json_response({"error": "suppressed must be a boolean"}, status=400)
    if settings is not None and not isinstance(settings, dict):
        return web.json_response({"error": "settings must be a JSON object"}, status=400)

    # 1) Persist settings (durable half). Runs through the SAME validation +
    #    event-log path as PUT /api/services/config (services_config owns the
    #    gate: wrong types / out-of-range minutes -> explicit 400, never saved).
    persisted = False
    if settings is not None:
        loop = asyncio.get_running_loop()
        entry, applied = await _server._validate_and_apply_slot("silence", settings, loop)
        if entry is not None:
            logger.warning(
                "POST /api/live/silence rejected silence field=%s: %s",
                entry.get("field"),
                entry.get("reason"),
            )
            return web.json_response(
                {
                    "error": entry["error"],
                    "slot": entry.get("slot"),
                    "field": entry.get("field"),
                    "reason": entry.get("reason"),
                },
                status=entry.get("status", 400),
            )
        if applied:
            try:
                _server._persist_services_config()
                persisted = True
            except OSError as exc:
                logger.error("failed to persist silence settings: %s", exc)
                return web.json_response(
                    {"error": "persist failed: %s" % exc, "reason": str(exc)[:200]}, status=500
                )

    # 2) Forward to webinfer (live half). suppressed / settings / kws_event all
    #    go through; webinfer owns the live state bit, the T1/T2 timers and the
    #    KWS-wake intake (combo key / voice / text / KWS event all land on this
    #    single API, spec §4/§7).
    base = _server._silence_base_url()
    forward: dict = {}
    if suppressed is not None:
        forward["suppressed"] = suppressed
    if settings is not None:
        # hotkey is a webui/frontend-local keyboard binding; webinfer does not
        # own it, so forward only the 6 settings keys webinfer understands
        # (avoids an "unknown setting ignored" warning on every settings save).
        webinfer_settings = {k: settings[k] for k in _WEBINFER_SILENCE_KEYS if k in settings}
        if webinfer_settings:
            forward["settings"] = webinfer_settings
    if payload.get("kws_event") is True:
        forward["kws_event"] = True
    try:
        async with (
            aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5.0)) as session,
            session.post(base + "/v1/live/silence", json=forward) as resp,
        ):
            body = await resp.json(content_type=None)
            result = dict(body or {})
            if isinstance(result.get("suppressed"), bool):
                _silence_suppressed = result["suppressed"]
            elif suppressed is not None:
                _silence_suppressed = suppressed
            result.setdefault("suppressed", _silence_suppressed)
            # settings is always the full local snapshot (7 keys incl. hotkey),
            # matching GET, so the frontend panel does not depend on webinfer's
            # 6-key echo.
            result["settings"] = _silence_settings_snapshot()
            if resp.status >= 400:
                logger.warning(
                    "webinfer /v1/live/silence POST returned %d: %s",
                    resp.status,
                    str(body)[:200],
                )
            _propagate_silence_to_live_sessions(
                request.app,
                _silence_suppressed,
                bool(_silence_settings_snapshot().get("kws_enabled", True)),
            )
            return web.json_response(result, status=resp.status)
    except (aiohttp.ClientError, OSError, ValueError) as exc:
        logger.warning(
            "webinfer /v1/live/silence unreachable; settings saved, live toggle NOT applied: %s",
            exc,
        )
        _propagate_silence_to_live_sessions(
            request.app,
            _silence_suppressed,
            bool(_silence_settings_snapshot().get("kws_enabled", True)),
        )
        return web.json_response(
            {
                "error": "webinfer unreachable; silence settings saved, live toggle not applied",
                "reason": str(exc)[:200],
                "saved": persisted,
                "suppressed": _silence_suppressed,
                "settings": _silence_settings_snapshot(),
            },
            status=502,
        )
