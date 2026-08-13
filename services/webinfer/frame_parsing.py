"""Live-visual frame parsing and image-reference resolution for the webinfer adapter.

Extracted from ``infer_loop.py`` (batch-2 monolith decoupling, zero behaviour
change). Owns the ``frames`` payload contract for ``POST /v1/text/chat`` with
``interaction_mode="live"`` (spec draft-live-visual-cb.md §3 层 1) plus the
image-reference resolution helpers used by the chat/completions video path
(``_resolve_frame_ref`` / ``_save_base64_frame`` / ``_validate_local_image_path``)
and the interaction-mode normalization helper.

All functions here are pure or take their dependencies explicitly (``state`` /
``allowed_roots``) so the module has no ``self`` coupling and no I/O side
effects beyond the documented ``state.session_frame_counter`` bump.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from adapter_types import SessionState
from aiohttp import web
from io_utils import normalize_image_b64

LOGGER = logging.getLogger("streaming_infer_adapter")

# Interaction modes isolate the decision-token framework (issues #44/#45).
#   live   (default): full silence/speak/delegate framework + forced silence
#                     before a user query is pending (original behaviour).
#   call   (voice-to-text direct chat): NO decision tokens, forced silence off.
#   jarvis (wake-word driven): decision tokens KEPT (jarvis consumes the
#                     `decision` field), but forced silence off (jarvis drives
#                     its own turn flow).
_VALID_INTERACTION_MODES = frozenset({"live", "call", "jarvis"})

# --- live visual path (spec draft-live-visual-cb.md §3 层 1) ----------------
# An optional top-level ``frames`` field on ``POST /v1/text/chat`` with
# ``interaction_mode="live"`` routes the round through the multimodal path
# (streaming for user rounds, non-streaming for proactive rounds). No
# ``frames`` field -> the existing text path runs byte-for-byte unchanged
# (pure-text live zero-regression rule).
_LIVE_FRAMES_MAX: int = 6


def _parse_live_frames(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate and normalize the optional top-level ``frames`` payload field.

    Contract (约法三章 — invalid frames are an explicit 400, never silently
    swallowed):

      * ``frames`` must be a list of dicts when present;
      * at most ``_LIVE_FRAMES_MAX`` frames;
      * each frame must carry a non-empty, base64-decodable ``image_b64``;
      * ``ts_ms`` (optional) must be a number when present.

    Returns a normalized ``[{"image_b64": str, "ts_ms": int|float|None}]``
    list. A missing ``frames`` field returns ``[]`` (callers treat that as
    "no frames" and keep the pure-text path).
    """
    raw = payload.get("frames")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise web.HTTPBadRequest(text="frames must be a list")
    if len(raw) > _LIVE_FRAMES_MAX:
        raise web.HTTPBadRequest(text=f"frames exceeds limit: {len(raw)} > {_LIVE_FRAMES_MAX}")
    frames: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise web.HTTPBadRequest(text=f"frames[{index}] must be an object")
        image_b64 = item.get("image_b64")
        if not isinstance(image_b64, str) or not image_b64.strip():
            raise web.HTTPBadRequest(
                text=f"frames[{index}].image_b64 must be a non-empty base64 string"
            )
        # Shared normalization (io_utils.normalize_image_b64): tolerate a full
        # data URI (``data:image/<fmt>;base64,<b64>``) by stripping the prefix
        # and validate the payload decodes. The normalized output is always
        # raw base64 so the downstream visual message builder re-prepends its
        # own ``data:image/jpeg;base64,`` prefix exactly once.
        try:
            image_b64 = normalize_image_b64(image_b64)
        except ValueError as exc:
            raise web.HTTPBadRequest(text=f"frames[{index}].image_b64 {exc}") from exc
        ts_ms = item.get("ts_ms")
        if ts_ms is not None and not isinstance(ts_ms, (int, float)):
            raise web.HTTPBadRequest(text=f"frames[{index}].ts_ms must be a number")
        frames.append({"image_b64": image_b64, "ts_ms": ts_ms})
    return frames


def _normalize_interaction_mode(mode: str | None) -> str:
    """Resolve an inbound ``interaction_mode`` to a known mode.

    Missing / empty / unrecognized values fall back to ``"live"``. An
    unknown value is logged (not silently swallowed) so a misconfigured
    caller cannot arm an unexpected code path.
    """
    if not mode:
        return "live"
    normalized = mode.strip().lower()
    if normalized in _VALID_INTERACTION_MODES:
        return normalized
    LOGGER.warning("unknown interaction_mode %r; falling back to 'live'", mode)
    return "live"


def _time_range_for_frame(frame_index: int, frame_seconds: float) -> str:
    """Format the time range label for a frame at ``frame_index``."""
    start = frame_index * frame_seconds
    return f"{start:.1f} seconds"


def _validate_local_image_path(raw_path: str, allowed_roots: tuple[str, ...]) -> Path:
    """Resolve and validate a caller-supplied local image path.

    The path must exist, use an allowed extension, and live under one of the
    configured ``allowed_roots`` (mirrors the historic ``InferLoopMixin``
    guard — an explicit 400 on any violation, never silent).
    """
    if not allowed_roots:
        raise web.HTTPBadRequest(text="local image paths are disabled")

    path = Path(raw_path).expanduser().resolve()
    if not path.is_file():
        raise web.HTTPBadRequest(text=f"local image path does not exist: {path}")
    if path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}:
        raise web.HTTPBadRequest(text=f"unsupported local image extension: {path.suffix}")

    for root in allowed_roots:
        root_path = Path(root).expanduser().resolve()
        try:
            path.relative_to(root_path)
            return path
        except ValueError:
            continue

    allowed = ", ".join(allowed_roots)
    raise web.HTTPBadRequest(text=f"local image path is outside allowed roots: {allowed}")


def _save_base64_frame(data_url: str, state: SessionState) -> str:
    """Validate a data-URL image reference and bump the session frame counter.

    Shared normalization (io_utils.normalize_image_b64) validates that
    the payload is a decodable base64 image (bare or data-URI prefixed);
    the original value is returned unchanged so memory / output records
    keep the exact data URL the caller supplied.
    """
    try:
        normalize_image_b64(data_url)
    except ValueError as exc:
        raise web.HTTPBadRequest(text="invalid data URL format") from exc
    state.session_frame_counter += 1
    return data_url


def _resolve_frame_ref(
    image_ref: dict[str, str],
    state: SessionState,
    allowed_roots: tuple[str, ...],
) -> str:
    """Resolve a single image reference (path or data URL) to a usable value."""
    if image_ref.get("kind") == "path":
        return str(_validate_local_image_path(image_ref.get("value", ""), allowed_roots))
    if image_ref.get("kind") == "data_url":
        return _save_base64_frame(image_ref.get("value", ""), state)
    raise web.HTTPBadRequest(text="unsupported image reference kind")
