"""Unit tests for ``io_utils.normalize_image_b64`` / ``io_utils.is_data_url``.

Dedup point #2 (codebase-map-2026-08-13 §3): the data-URL prefix stripping +
base64 validation used to live in four overlapping places; the shared
primitive is ``normalize_image_b64``.  These tests pin the contract:

  * raw base64 accepted (padded and unpadded), returned unchanged;
  * ``data:image/<fmt>;base64,<b64>`` accepted, prefix stripped;
  * anything not decodable raises ``ValueError`` with a stable message.

Run: python -m pytest tests/test_io_utils_normalize_image_b64.py -q
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from io_utils import is_data_url, normalize_image_b64  # noqa: E402


def _b64(payload: bytes = b"\xff\xd8\xff\xe0 fake-jpeg") -> str:
    return base64.b64encode(payload).decode("ascii")


def test_normalize_accepts_raw_base64_unchanged():
    raw = _b64(b"jpeg-bytes")
    assert normalize_image_b64(raw) == raw


def test_normalize_accepts_unpadded_raw_base64():
    raw = _b64(b"jpeg-bytes").rstrip("=")
    assert normalize_image_b64(raw) == raw


def test_normalize_strips_data_uri_prefix():
    raw = _b64(b"jpeg-bytes")
    assert normalize_image_b64(f"data:image/jpeg;base64,{raw}") == raw
    assert normalize_image_b64(f"data:image/png;base64,{raw}") == raw


def test_normalize_strips_leading_trailing_whitespace():
    raw = _b64(b"jpeg-bytes")
    assert normalize_image_b64(f"  {raw}  ") == raw


def test_normalize_rejects_non_base64_marker():
    with pytest.raises(ValueError) as exc_info:
        normalize_image_b64("data:image/jpeg;plain,abc")
    assert "must be base64" in str(exc_info.value)


def test_normalize_rejects_empty_data_uri_payload():
    with pytest.raises(ValueError) as exc_info:
        normalize_image_b64("data:image/jpeg;base64,")
    assert "has no base64 payload" in str(exc_info.value)


def test_normalize_rejects_non_base64_payload():
    with pytest.raises(ValueError) as exc_info:
        normalize_image_b64("!!!not-base64!!!")
    assert "not valid base64" in str(exc_info.value)


def test_normalize_rejects_empty_string():
    with pytest.raises(ValueError) as exc_info:
        normalize_image_b64("   ")
    assert "empty" in str(exc_info.value)


def test_normalize_rejects_non_string():
    with pytest.raises(ValueError) as exc_info:
        normalize_image_b64(12345)  # type: ignore[arg-type]
    assert "must be a string" in str(exc_info.value)


def test_is_data_url():
    assert is_data_url("data:image/jpeg;base64,AAAA")
    assert not is_data_url("AAAA")
    assert not is_data_url("")
    assert not is_data_url(None)  # type: ignore[arg-type]
