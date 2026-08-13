"""QA independent supplement: diagnostic_save_wav path-variant edge cases.

Extends test_diagnostic_save_wav_security.py with hostile filename variants
the engineer's regression set did not explicitly cover:

* percent-encoded traversal (%2e%2e%2f / %2e%2e%5c) — depends on whether the
  multipart parser decodes percent-encoding in ``filename=`` (it should NOT,
  but the sanitizer must still never let a decoded separator through);
* double slashes / mixed separators (``a//b.wav``, ``..//evil.wav``);
* Unicode lookalike separators (U+2215 division slash, U+FF0F fullwidth
  solidus, U+2216 set minus) — on Windows these are ordinary filename chars,
  so they must either be rejected or land as a literal filename INSIDE the
  capture dir, never escape;
* trailing-dot / reserved names (Windows ``CON``, trailing ``.``) — must not
  escape; ``CON`` is a reserved device name so a 400 or a safe in-dir file is
  both acceptable, but never an escape.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from joy_interaction_webui.jarvis_routes import (  # noqa: E402
    _sanitize_diagnostic_filename,
    setup_jarvis_routes,
)


def _app() -> web.Application:
    app = web.Application()
    app["jarvis_manager"] = None
    setup_jarvis_routes(app)
    return app


def _raw_multipart(filename: str, payload: bytes) -> tuple[bytes, str]:
    boundary = "joyai-qa-boundary"
    body = (
        (
            "--%s\r\n"
            'Content-Disposition: form-data; name="wav"; filename="%s"\r\n'
            "Content-Type: audio/wav\r\n\r\n" % (boundary, filename)
        ).encode()
        + payload
        + ("\r\n--%s--\r\n" % boundary).encode()
    )
    return body, "multipart/form-data; boundary=%s" % boundary


# ---------------------------------------------------------------------------
# sanitizer unit-level: every hostile variant must either be rejected (None)
# or reduce to a plain basename that cannot escape the capture dir.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "variant",
    [
        "%2e%2e/evil.wav",      # percent-encoded dot-dot, forward slash
        "%2e%2e%2fevil.wav",    # fully percent-encoded ../  (no literal slash)
        "%2e%2e%5cevil.wav",    # fully percent-encoded ..\  (no literal slash)
        "%252e%252e%252fevil.wav",  # double-encoded
        "..//evil.wav",         # double slash
        "a//b.wav",             # double slash inside name
        "evil.wav/../../x",     # traversal mid-name
        "..\u2215..\u2215evil.wav",  # U+2215 division slash lookalike
        "..\uFF0Fevil.wav",     # U+FF0F fullwidth solidus lookalike
        "..\u2216evil.wav",     # U+2216 set minus lookalike
        "/",                    # bare separator
        "\\",                   # bare backslash
        "CON",                  # Windows reserved device name
        "mic.wav.",             # trailing dot (Windows strips it)
        "...",                  # dots-only
        ".. ",
    ],
)
def test_sanitizer_rejects_or_reduces_to_basename(variant):
    result = _sanitize_diagnostic_filename(variant)
    if result is None:
        return  # rejected outright — acceptable
    # If accepted it must be a strict plain basename with no separator/.. segments.
    assert result == Path(result).name
    assert "/" not in result and "\\" not in result
    assert result not in (".", "..")
    assert "\x00" not in result


# ---------------------------------------------------------------------------
# HTTP-level: hostile raw bodies never write outside the capture dir.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "variant",
    [
        "%2e%2e%2fevil.wav",   # encoded traversal, no literal slash
        "%2e%2e%5cevil.wav",
        "..//evil.wav",
        "..\u2215..\u2215evil.wav",
        "..\uFF0Fevil.wav",
    ],
)
async def test_save_wav_hostile_variants_never_escape(tmp_path, monkeypatch, variant):
    monkeypatch.setenv("JARVIS_DIAGNOSTIC_WAV_DIR", str(tmp_path))
    body, ctype = _raw_multipart(variant, b"RIFF")
    async with TestServer(_app()) as srv, TestClient(srv) as client:
        resp = await client.post(
            "/api/diagnostic/save_wav", data=body, headers={"Content-Type": ctype}
        )
        # 400 (rejected) or 200 (saved as a literal in-dir filename) both fine;
        # what must NEVER happen is a file outside tmp_path.
        assert resp.status in (200, 400)
    # No file may have been created in the capture dir's parent with an evil name.
    assert not (tmp_path.parent / "evil.wav").exists()
    # Every file this test could have written must be strictly inside tmp_path.
    for p in tmp_path.rglob("*"):
        if p.is_file():
            assert p.parent == tmp_path or tmp_path in p.parents, f"file outside capture dir: {p}"


async def test_save_wav_unicode_plain_name_stays_in_dir(tmp_path, monkeypatch):
    """A non-separator Unicode char is a legit filename char — must stay in-dir."""
    monkeypatch.setenv("JARVIS_DIAGNOSTIC_WAV_DIR", str(tmp_path))
    body, ctype = _raw_multipart("\u4f60\u597d.wav", b"RIFF1234")
    async with TestServer(_app()) as srv, TestClient(srv) as client:
        resp = await client.post(
            "/api/diagnostic/save_wav", data=body, headers={"Content-Type": ctype}
        )
        assert resp.status == 200
        resp_body = await resp.json()
        # Saved path must be strictly inside tmp_path.
        saved = Path(resp_body["saved"][0])
        assert tmp_path in saved.parents or saved.parent == tmp_path
