"""Live visual frame buffering (extracted from ``live_mode.LiveStateMachine``).

Moved from ``live_mode.py`` (spec live-visual-cb.md §2.2): the
recent-frame ring buffer (``LIVE_FRAME_WINDOW``, default 6) fed by
``handle_frame(image_b64, ts_ms)`` plus the webinfer wire-format conversion
``frames_payload``.  ``LiveStateMachine`` keeps thin facades
(``handle_frame`` / ``recent_frames`` / ``_frames_payload``) so the public
surface is unchanged; behavior is byte-identical (ring rotation, capture
window log, ``{image_b64, ts_ms}`` wire dicts).
"""

from __future__ import annotations

import logging
import os
from collections import deque
from collections.abc import Iterable

#: Default recent-frame ring size (env ``LIVE_FRAME_WINDOW`` overrides).
_DEFAULT_FRAME_WINDOW: int = 6


def frame_window_from_env(default_window: int = _DEFAULT_FRAME_WINDOW) -> int:
    """Resolve the recent-frame ring size from ``LIVE_FRAME_WINDOW``.

    Non-positive values fall back to ``default_window``.  Moved verbatim from
    ``LiveStateMachine.__init__`` (zero behavior change, including the
    unguarded ``int()`` conversion for malformed values).
    """
    window = int(os.environ.get("LIVE_FRAME_WINDOW", str(default_window)) or default_window)
    if window < 1:
        window = default_window
    return window


def append_frame(
    recent_frames: deque[tuple[str, float]],
    image_b64: str,
    ts_ms: float,
    *,
    logger: logging.Logger,
) -> None:
    """Append one frame to the ring buffer and log the capture.

    Appends ``(image_b64, ts_ms)`` (oldest dropped on overflow) and emits the
    ``[live-mode] frame captured (n=..., window=...)`` line on ``logger``.
    Moved verbatim from ``LiveStateMachine.handle_frame``.
    """
    recent_frames.append((image_b64, ts_ms))
    window_s = 0.0
    if len(recent_frames) >= 2:
        window_s = max(0.0, recent_frames[-1][1] - recent_frames[0][1]) / 1000.0
    logger.info(
        "[live-mode] frame captured (n=%d, window=%.1fs)",
        len(recent_frames),
        window_s,
    )


def frames_payload(frames: Iterable[tuple[str, float]]) -> list[dict]:
    """Convert the internal ``(image_b64, ts_ms)`` buffer to the wire format.

    webinfer's live visual path (layer 1) expects ``frames`` as a list of
    ``{"image_b64": str, "ts_ms": int|float}`` objects (spec
    live-visual-cb.md §2.1/§3).  Moved verbatim from
    ``LiveStateMachine._frames_payload``.
    """
    return [{"image_b64": b64, "ts_ms": ts} for b64, ts in frames]


__all__ = [
    "_DEFAULT_FRAME_WINDOW",
    "append_frame",
    "frame_window_from_env",
    "frames_payload",
]
