"""QA regression: extra edge cases for ``io_utils.normalize_image_b64``.

Independent verification (QA 严过关) of the base64 normalization dedup
(commit d723fc8).  Pins the shared primitive's contract beyond the 10 cases
shipped by the engineer — especially the edges that could diverge from the
pre-change ``infer_loop._parse_live_frames`` inline logic:

  * case-sensitivity of the ``data:`` / ``;base64,`` markers (unchanged
    from the original inline code — no regression, just pinned);
  * whitespace tolerance (leading whitespace before the data: prefix);
  * URL-safe alphabet ``-``/``_`` (original used stdlib
    ``base64.b64decode(validate=True)`` which rejects them — pinned);
  * non-image ``data:*;base64`` URLs (accepted by the original inline
    ``_parse_live_frames`` because it only checked ``"base64" in prefix``);
  * large payload round-trip (no length cap regression);
  * idempotence of normalize∘normalize.

Run: python -m pytest tests/test_qa_regression_normalize_edges.py -q
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from io_utils import normalize_image_b64  # noqa: E402


def _b64(payload: bytes) -> str:
    return base64.b64encode(payload).decode("ascii")


def test_uppercase_data_prefix_is_rejected_as_invalid_base64():
    """Uppercase ``DATA:`` is NOT a data URL (marker is case-sensitive, same
    as the original inline ``startswith("data:")``); the string then contains
    ``:``/``/`` so it cannot decode -> stable ValueError."""
    raw = _b64(b"jpeg-bytes")
    with pytest.raises(ValueError) as exc_info:
        normalize_image_b64(f"DATA:image/jpeg;base64,{raw}")
    assert "not valid base64" in str(exc_info.value)


def test_uppercase_base64_marker_in_prefix_rejected():
    """``;BASE64,`` marker is case-sensitive (original checked
    ``"base64" not in prefix``) -> 'data URL must be base64'."""
    raw = _b64(b"jpeg-bytes")
    with pytest.raises(ValueError) as exc_info:
        normalize_image_b64(f"data:image/jpeg;BASE64,{raw}")
    assert "must be base64" in str(exc_info.value)


def test_leading_whitespace_before_data_prefix_is_stripped():
    """Whitespace around a data URL is trimmed before prefix detection —
    mirrors the original ``image_b64.strip()`` first step."""
    raw = _b64(b"jpeg-bytes")
    assert normalize_image_b64(f"  data:image/jpeg;base64,{raw}  ") == raw


def test_non_image_data_url_accepted():
    """``data:text/plain;base64,<b64>`` is accepted (original only required
    ``base64`` in the prefix segment, not an ``image/*`` media type)."""
    raw = _b64(b"hello")
    assert normalize_image_b64(f"data:text/plain;base64,{raw}") == raw


def test_urlsafe_base64_rejected():
    """URL-safe alphabet (``-``/``_``) is rejected by stdlib validate=True —
    same as the pre-change inline behavior (no regression, pinned)."""
    # b"\xfb\xff\xff" encodes to "+///" (std) / "-___" (urlsafe) so the
    # URL-safe variant is guaranteed to contain -/_
    urlsafe = base64.urlsafe_b64encode(b"\xfb\xff\xff").decode("ascii")
    assert "-" in urlsafe or "_" in urlsafe
    with pytest.raises(ValueError) as exc_info:
        normalize_image_b64(urlsafe)
    assert "not valid base64" in str(exc_info.value)


def test_whitespace_only_data_payload_rejected():
    """``data:image/jpeg;base64,   `` -> payload strips to empty."""
    with pytest.raises(ValueError) as exc_info:
        normalize_image_b64("data:image/jpeg;base64,   ")
    assert "has no base64 payload" in str(exc_info.value)


def test_large_payload_roundtrip():
    """A ~1 MiB base64 payload round-trips (no length cap)."""
    payload = bytes(range(256)) * 4096  # 1 MiB
    raw = _b64(payload)
    assert len(raw) > 1_000_000
    assert normalize_image_b64(raw) == raw
    assert normalize_image_b64(f"data:image/jpeg;base64,{raw}") == raw


def test_normalize_is_idempotent():
    """normalize∘normalize returns the same bare base64."""
    raw = _b64(b"jpeg-bytes")
    once = normalize_image_b64(f"data:image/png;base64,{raw}")
    assert normalize_image_b64(once) == once == raw


def test_prefixed_with_comma_in_payload_rejected():
    """Split happens at the FIRST comma (``find(",")``, as in the original
    inline logic), so the payload ``AA,AA`` keeps its comma and cannot
    decode (``,`` is not in the base64 alphabet) -> explicit error."""
    with pytest.raises(ValueError) as exc_info:
        normalize_image_b64("data:image/jpeg;base64,AA,AA")
    assert "not valid base64" in str(exc_info.value)
