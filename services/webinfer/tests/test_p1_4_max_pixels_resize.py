"""Regression tests for audit P1-4: live visual frames bypass max_pixels.

The video path resizes images to ``max_pixels`` before sending
(``io_utils._file_to_data_url(..., max_pixels=...)``), but the live visual
path sent ``frames`` base64 at full resolution, so 4K screen captures reached
the main model at full size and ``_estimate_messages_chars`` (fixed 1024-char
placeholder) badly under-counted the real prompt size.

Fix: frames are resized through the shared io_utils helper before the message
is built; ``_estimate_messages_chars`` counts image_url parts by their actual
data-URL length.

Run: python -m pytest tests/test_p1_4_max_pixels_resize.py -q
"""

from __future__ import annotations

import base64
import io
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from io_utils import _resize_frame_image_b64  # noqa: E402
from prompt_assembly import (  # noqa: E402
    _build_live_visual_user_message,
    compose_live_visual_messages,
)
from prompt_building import _estimate_messages_chars  # noqa: E402


def _jpeg_b64(size: tuple[int, int], quality: int = 90) -> str:
    image = Image.new("RGB", size, color=(200, 30, 40))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _decoded_pixels(b64: str) -> tuple[int, int]:
    with Image.open(io.BytesIO(base64.b64decode(b64))) as image:
        return image.size


# ---------------------------------------------------------------------------
# _resize_frame_image_b64
# ---------------------------------------------------------------------------


def test_large_frame_is_resized_to_max_pixels():
    large = _jpeg_b64((2000, 2000))  # 4 MP, well over the 1 MP budget
    original_size = _decoded_pixels(large)
    assert original_size[0] * original_size[1] > 1048576

    resized = _resize_frame_image_b64(large, max_pixels=1048576)
    assert resized != large
    w, h = _decoded_pixels(resized)
    assert w * h <= 1048576
    assert (w, h) == (1024, 1024)  # 4MP * (1/4) -> 1MP


def test_small_frame_is_left_unchanged():
    small = _jpeg_b64((320, 240))
    assert _resize_frame_image_b64(small, max_pixels=1048576) == small


def test_max_pixels_zero_keeps_original():
    large = _jpeg_b64((2000, 2000))
    assert _resize_frame_image_b64(large, max_pixels=0) == large


def test_undecodable_frame_fails_open():
    """A frame PIL cannot decode returns unchanged (fail-open, video-path
    behaviour) instead of raising."""
    raw = base64.b64encode(b"\xff\xd8\xff\xe0 fake-jpeg").decode("ascii")
    assert _resize_frame_image_b64(raw, max_pixels=1048576) == raw
    assert _resize_frame_image_b64("", max_pixels=1048576) == ""


# ---------------------------------------------------------------------------
# _build_live_visual_user_message / compose_live_visual_messages
# ---------------------------------------------------------------------------


def test_visual_user_message_resizes_frames():
    large = _jpeg_b64((2000, 2000))
    message = _build_live_visual_user_message(
        "what's on screen?",
        [{"image_b64": large, "ts_ms": 1.0}],
        max_pixels=1048576,
    )
    parts = message["content"]
    image_part = next(p for p in parts if p.get("type") == "image_url")
    url = image_part["image_url"]["url"]
    assert url.startswith("data:image/jpeg;base64,")
    payload = url[len("data:image/jpeg;base64,") :]
    assert payload != large
    w, h = _decoded_pixels(payload)
    assert w * h <= 1048576


def test_visual_user_message_no_max_pixels_keeps_original():
    """max_pixels=0 (default in direct-helper tests) sends frames unchanged."""
    large = _jpeg_b64((2000, 2000))
    message = _build_live_visual_user_message("hi", [{"image_b64": large, "ts_ms": 1.0}])
    url = message["content"][1]["image_url"]["url"]
    payload = url[len("data:image/jpeg;base64,") :]
    assert payload == large


def test_compose_live_visual_messages_threads_max_pixels():
    large = _jpeg_b64((2000, 2000))
    got = compose_live_visual_messages(
        composed_system="sys",
        last_user_text="hi",
        frames=[{"image_b64": large, "ts_ms": 1.0}],
        caller_messages=[{"role": "user", "content": "hi"}],
        max_pixels=1048576,
    )
    url = got[-1]["content"][1]["image_url"]["url"]
    payload = url[len("data:image/jpeg;base64,") :]
    w, h = _decoded_pixels(payload)
    assert w * h <= 1048576


# ---------------------------------------------------------------------------
# _estimate_messages_chars — actual image size
# ---------------------------------------------------------------------------


def test_estimate_counts_actual_image_url_length():
    payload = "A" * 5000
    url = f"data:image/jpeg;base64,{payload}"
    msgs = [
        {"role": "system", "content": "ctx"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "hi"},
                {"type": "image_url", "image_url": {"url": url}},
            ],
        },
    ]
    total = _estimate_messages_chars(msgs)
    assert total == 3 + 2 + len(url) + 32


def test_estimate_falls_back_to_placeholder_without_url():
    msgs = [
        {
            "role": "user",
            "content": [{"type": "image_url", "image_url": {}}],
        }
    ]
    # 1024 placeholder + 16 framing
    assert _estimate_messages_chars(msgs) == 1024 + 16


def test_estimate_internal_image_part_uses_placeholder():
    frame_path = "/tmp/frame.jpg"  # noqa: S108  (test fixture path, not real IO)
    msgs = [
        {
            "role": "user",
            "content": [{"type": "image", "image": frame_path, "max_pixels": 1048576}],
        }
    ]
    assert _estimate_messages_chars(msgs) == 1024 + 16
