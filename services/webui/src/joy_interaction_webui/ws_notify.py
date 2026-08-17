# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""WebSocket notify contract layer (split out of server.py).

Owns the WS push facade (``send_to_session`` / ``notify_session_*``) and the
per-session WebSocket registries so that ``jarvis_session`` can depend on THIS
module instead of ``server``. This kills the reverse-import / double-module-load
problem documented in server.py's ``__main__`` alias comment (see
``doc/subsystems/jarvis-mode.md`` changelog v3.22).

Design notes
------------
- The mutable registries (``session_websockets``, ``websockets``) live HERE as
  the single source of truth. ``server`` re-exports the same objects, so
  mutations through either module reference stay shared (no dual-dict bug).
- Cross-module state that stays owned by ``server`` (``sessions``, the session
  registry) is resolved through the server facade lazily at call time, keeping
  the module-load graph acyclic while preserving the exact monkeypatch contract
  exercised by the test suite (e.g. ``patch.object(server, "send_to_session")``
  and ``patch.object(server, "notify_session_llm_reply")`` must keep working).
"""

import asyncio
import json
import logging
import time
from collections import defaultdict

logger = logging.getLogger(__name__)

# Background task registry: keep strong refs to fire-and-forget tasks so they
# are not garbage-collected before completion (satisfies ruff RUF006).
_BACKGROUND_TASKS: set[asyncio.Task] = set()


def _spawn_bg(coro):
    task = asyncio.create_task(coro)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    return task


#: WebSocket registries — single source of truth for the notify facade.
session_websockets = defaultdict(set)
websockets = set()


def _server_send_to_session(session_id, payload):
    """Deliver a JSON-serialized payload via the server facade's ``send_to_session``.

    Resolved through ``server`` (not the local binding) so tests that patch
    ``server.send_to_session`` keep working after the split — before the split,
    ``jarvis_session``'s callback picked the patch up via ``from .server import``.
    """
    from . import server as _server

    _server.send_to_session(session_id, json.dumps(payload, ensure_ascii=False))


# send_to_session must actually run the WS send coroutine. The previous
# implementation used asyncio.create_task(ws.send_str(message)) directly,
# which silently drops the message on real aiohttp WebSocketResponse
# (send_str awaits internally and the surrounding coroutine returns
# before the scheduler runs the task). See
# tests/test_send_to_session_actually_awaits.py for the regression test.


async def _safe_send_str(ws, message, session_id):
    try:
        await ws.send_str(message)
    except Exception as exc:
        logger.warning("send_to_session: WS send failed for %s: %s", session_id, exc)


def send_to_session(session_id, message):
    targets = list(session_websockets.get(session_id, set()))
    if not targets:
        # Common during early LLM startup before browser WS reconnects, log at INFO.
        logger.info(
            "send_to_session: no WS targets for session %s (total sessions in dict: %d). Message DROPPED: %s",
            session_id,
            len(session_websockets),
            message[:200],
        )
        return
    for ws in targets:
        try:
            _spawn_bg(_safe_send_str(ws, message, session_id))
        except RuntimeError as exc:
            logger.error("send_to_session: schedule failed for %s: %s", session_id, exc)


def notify_session_json(session_id, payload):
    handle_background_handoff_for_interaction(session_id, payload)
    _server_send_to_session(session_id, payload)


def notify_session_llm_reply(session_id, text, source="jarvis", reply_epoch=0):
    payload = {
        "type": "llm_reply",
        "text": text or "",
        "source": source or "jarvis",
        # P1: per-turn generation counter captured by the state machine at
        # turn start. The front-end discards any llm_reply whose reply_epoch
        # is older than its accepted generation (late broadcast after a
        # barge-in / newer turn).
        "reply_epoch": int(reply_epoch or 0),
        "ts": time.time(),
    }
    _server_send_to_session(session_id, payload)


def notify_session_tts_sentence(session_id, text, seq, audio_b64, session):
    """Push one P0-A streaming TTS sentence (WAV base64) to the browser.

    The front-end keeps a per-sentence playback queue ordered by ``seq``;
    ``session`` identifies the LLM reply the sentence belongs to so a new
    reply can discard a stale queue. ``audio_b64`` is a playable WAV.
    """
    payload = {
        "type": "tts_sentence",
        "seq": int(seq or 0),
        "session": int(session or 0),
        "text": text or "",
        "audio_b64": audio_b64 or "",
        "ts": time.time(),
    }
    targets = session_websockets.get(session_id, set())
    logger.info(
        "tts_sentence push seq=%s session=%s text=%r ws_targets=%d",
        seq,
        session,
        (text or "")[:40],
        len(targets),
    )
    _server_send_to_session(session_id, payload)


def notify_session_silence_wake(session_id, audio_b64):
    """Push the radio-silence wake ceremony audio (WAV base64) to the browser.

    Live-mode analogue of jarvis's server-side ``_play_wake_wav``: live has no
    server speaker track (replies are played by the browser), so the
    pre-recorded wake.wav (zero token, spec §5 唤醒仪式) is sent to the
    frontend to play on receipt. ``audio_b64`` is a playable WAV.
    """
    payload = {
        "type": "silence_wake",
        "audio_b64": audio_b64 or "",
        "ts": time.time(),
    }
    targets = session_websockets.get(session_id, set())
    logger.info("silence_wake push ws_targets=%d", len(targets))
    _server_send_to_session(session_id, payload)


def notify_session_pilot_utterance(session_id, text, source="asr", reply_epoch=0):
    payload = {
        "type": "pilot_utterance",
        "text": text or "",
        "source": source or "asr",
        # P1: the live llm_reply epoch at commit time. The front-end adopts
        # it as its accepted generation, so any older llm_reply is dropped.
        "reply_epoch": int(reply_epoch or 0),
        "ts": time.time(),
    }
    _server_send_to_session(session_id, payload)


def notify_session_asr_partial(session_id, text, is_final=False, reply_epoch=0):
    payload = {
        "type": "asr_partial",
        "text": text or "",
        "is_final": bool(is_final),
        # P1: the live llm_reply epoch at speech time. A barge-in (whose
        # partials carry the post-bump value) raises the front-end's accepted
        # generation so a late llm_reply from the interrupted turn is dropped.
        "reply_epoch": int(reply_epoch or 0),
        "ts": time.time(),
    }
    _server_send_to_session(session_id, payload)


def handle_background_handoff_for_interaction(session_id, payload):
    if not isinstance(payload, dict) or payload.get("type") != "background_result_ready":
        return
    from . import server as _server

    session = _server.sessions.get(session_id)
    if not session or not session.get("vlm_service"):
        return


def get_session_callback(session_id):
    def callback(text, metrics, frame_seq=None):
        from . import server as _server

        session = _server.sessions.get(session_id)
        display_text = text
        if session and session.get("background_service"):
            display_text = session["background_service"].handle_foreground_response(
                text, metrics=metrics
            )
        sh = metrics.get("summarizer_history") if isinstance(metrics, dict) else None
        summarizer_timing = sh.get("summarizer_timing") if isinstance(sh, dict) else None
        out = {"type": "vlm_response", "text": display_text, "metrics": metrics}
        if summarizer_timing:
            out["summarizer_timing"] = summarizer_timing
        if frame_seq is not None:
            out["frame_seq"] = frame_seq
        _server.send_to_session(session_id, json.dumps(out, ensure_ascii=False))

    return callback


async def _safe_send_str_all(ws, message):
    try:
        await ws.send_str(message)
    except Exception as exc:
        logger.warning("broadcast_text_update: WS send failed: %s", exc)


def broadcast_text_update(text, metrics):
    if not websockets:
        return
    message = json.dumps({"type": "vlm_response", "text": text, "metrics": metrics})
    for ws in list(websockets):
        try:
            _spawn_bg(_safe_send_str_all(ws, message))
        except RuntimeError as exc:
            logger.error("broadcast_text_update: schedule failed: %s", exc)
