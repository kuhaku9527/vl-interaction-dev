"""Regression tests for P1-1: ``diagnostic_save_wav`` path traversal + size limit.

Covers the audit fix (``doc/architecture/audit-webui-backend-2026-08-13.md``
P1-1):

* client-supplied filenames are reduced to a plain basename — path
  separators / ``..`` / absolute paths are rejected with HTTP 400;
* uploads over ``_MAX_DIAGNOSTIC_WAV_BYTES`` are rejected with HTTP 400 and
  the truncated partial file is removed from disk;
* legitimate uploads still land inside the configured capture directory.

The capture directory is redirected via ``JARVIS_DIAGNOSTIC_WAV_DIR`` so the
tests never touch the real ``D:/AI/data/kws/mic_captures``.
"""

from __future__ import annotations

from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer

from joy_interaction_webui.jarvis_routes import (
    _MAX_DIAGNOSTIC_WAV_BYTES,
    _sanitize_diagnostic_filename,
    setup_jarvis_routes,
)


def _app() -> web.Application:
    app = web.Application()
    app["jarvis_manager"] = None
    setup_jarvis_routes(app)
    return app


def _raw_multipart(filename: str, payload: bytes) -> tuple[bytes, str]:
    """Build a raw multipart body with a literal (un-encoded) filename.

    aiohttp's FormData *client* URL-encodes path separators in filenames, so
    the traversal tests must send a raw body — that is what a malicious client
    would do — to exercise the server-side sanitizer.
    """
    boundary = "joyai-test-boundary"
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


def test_sanitize_diagnostic_filename():
    # Plain basenames pass through unchanged.
    assert _sanitize_diagnostic_filename("mic.wav") == "mic.wav"
    assert _sanitize_diagnostic_filename("a.b.wav") == "a.b.wav"
    # Empty / non-string inputs are rejected.
    assert _sanitize_diagnostic_filename("") is None
    assert _sanitize_diagnostic_filename(None) is None
    # Path separators, .. segments and absolute paths are rejected.
    assert _sanitize_diagnostic_filename("../evil.wav") is None
    assert _sanitize_diagnostic_filename("..\\evil.wav") is None
    assert _sanitize_diagnostic_filename("/etc/passwd") is None
    assert _sanitize_diagnostic_filename("C:/Windows/evil.wav") is None
    assert _sanitize_diagnostic_filename("..") is None
    assert _sanitize_diagnostic_filename(".") is None
    # Overlong names are rejected.
    assert _sanitize_diagnostic_filename("a" * 300 + ".wav") is None


async def test_save_wav_normal(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DIAGNOSTIC_WAV_DIR", str(tmp_path))
    payload = b"RIFF1234abcd"
    async with TestServer(_app()) as srv, TestClient(srv) as client:
        data = FormData()
        data.add_field("wav", payload, filename="mic.wav", content_type="audio/wav")
        resp = await client.post("/api/diagnostic/save_wav", data=data)
        assert resp.status == 200
        body = await resp.json()
        assert body["saved"] == [str(tmp_path / "mic.wav")]
    assert (tmp_path / "mic.wav").read_bytes() == payload


async def test_save_wav_rejects_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DIAGNOSTIC_WAV_DIR", str(tmp_path))
    body, ctype = _raw_multipart("../evil.wav", b"RIFF")
    async with TestServer(_app()) as srv, TestClient(srv) as client:
        resp = await client.post(
            "/api/diagnostic/save_wav", data=body, headers={"Content-Type": ctype}
        )
        assert resp.status == 400
        resp_body = await resp.json()
        assert "invalid wav filename" in resp_body["error"]
    # Nothing escaped into the parent directory and nothing was written.
    assert not (tmp_path.parent / "evil.wav").exists()
    assert list(tmp_path.iterdir()) == []


async def test_save_wav_rejects_oversize(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DIAGNOSTIC_WAV_DIR", str(tmp_path))
    oversized = b"\0" * (_MAX_DIAGNOSTIC_WAV_BYTES + 1)
    async with TestServer(_app()) as srv, TestClient(srv) as client:
        data = FormData()
        data.add_field("wav", oversized, filename="big.wav", content_type="audio/wav")
        resp = await client.post("/api/diagnostic/save_wav", data=data)
        assert resp.status == 400
        body = await resp.json()
        assert "too large" in body["error"]
    # The truncated partial file must not be left on disk.
    assert not (tmp_path / "big.wav").exists()
