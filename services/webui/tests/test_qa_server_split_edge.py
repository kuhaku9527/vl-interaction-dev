# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""QA edge cases for batch-5 server split (services_config merge, tts endpoint).

These pin the behavior of the split-out modules against the requirements in
``doc/architecture/codebase-map-2026-08-13.md`` priority 4 (server.py split),
focusing on error branches the engineer's regression may not have covered:
- services_config file merge boundaries (missing / corrupt / partial / unknown
  keys / non-dict file).
- _tts_synthesize_handler error branches (missing text, bad base64, upstream
  non-json, upstream unreachable, upstream 5xx).
"""

import asyncio
import base64
import copy
import json
from types import SimpleNamespace
from unittest.mock import patch

from joy_interaction_webui import server, services_config, tts_endpoint, ws_notify


def _fresh_target():
    return copy.deepcopy(services_config._SERVICES_CONFIG_DEFAULTS)


# ---------------------------------------------------------------------------
# services_config._merge_services_config_file boundaries
# ---------------------------------------------------------------------------
def test_merge_missing_file_returns_false_no_mutation(tmp_path):
    target = _fresh_target()
    path = tmp_path / "does-not-exist.json"
    assert services_config._merge_services_config_file(target, str(path)) is False
    assert target == services_config._SERVICES_CONFIG_DEFAULTS


def test_merge_corrupt_json_returns_false_no_mutation(tmp_path):
    target = _fresh_target()
    path = tmp_path / "corrupt.json"
    path.write_text("{ not valid json !!", encoding="utf-8")
    assert services_config._merge_services_config_file(target, str(path)) is False
    assert target == services_config._SERVICES_CONFIG_DEFAULTS


def test_merge_non_dict_file_returns_false_no_mutation(tmp_path):
    target = _fresh_target()
    path = tmp_path / "list.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    assert services_config._merge_services_config_file(target, str(path)) is False
    assert target == services_config._SERVICES_CONFIG_DEFAULTS


def test_merge_unknown_slots_and_keys_ignored(tmp_path):
    """Only known slots/fields merged; unknown keys must not inject state."""
    target = _fresh_target()
    path = tmp_path / "services.json"
    path.write_text(
        json.dumps(
            {
                "llm": {"api_base": "http://new:1/v1", "model": "m2", "hacker": "x"},
                "evil_slot": {"api_base": "http://evil"},
                "tts": "not-a-dict",
            }
        ),
        encoding="utf-8",
    )
    assert services_config._merge_services_config_file(target, str(path)) is True
    assert target["llm"]["api_base"] == "http://new:1/v1"
    assert target["llm"]["model"] == "m2"
    assert "hacker" not in target["llm"]
    assert "evil_slot" not in target
    # tts slot was not a dict -> skipped
    assert target["tts"] == services_config._SERVICES_CONFIG_DEFAULTS["tts"]


def test_merge_incremental_partial_overrides(tmp_path):
    """A file with only one slot/field overrides just that field."""
    target = _fresh_target()
    path = tmp_path / "partial.json"
    path.write_text(json.dumps({"llm": {"api_base": "http://partial:9/v1"}}), encoding="utf-8")
    assert services_config._merge_services_config_file(target, str(path)) is True
    assert target["llm"]["api_base"] == "http://partial:9/v1"
    # untouched fields keep defaults
    assert target["llm"]["model"] == "streaming-infer-adapter"
    assert target["tts"] == services_config._SERVICES_CONFIG_DEFAULTS["tts"]
    assert target["asr"] == services_config._SERVICES_CONFIG_DEFAULTS["asr"]


def test_merge_empty_dict_file_no_op_returns_true(tmp_path):
    target = _fresh_target()
    path = tmp_path / "empty.json"
    path.write_text("{}", encoding="utf-8")
    assert services_config._merge_services_config_file(target, str(path)) is True
    assert target == services_config._SERVICES_CONFIG_DEFAULTS


def test_merge_non_string_values_ignored(tmp_path):
    """Values that are not strings must not overwrite (e.g. api_key: null)."""
    target = _fresh_target()
    path = tmp_path / "types.json"
    path.write_text(
        json.dumps({"llm": {"api_base": None, "model": 123, "api_key": ""}}), encoding="utf-8"
    )
    assert services_config._merge_services_config_file(target, str(path)) is True
    assert target["llm"]["api_base"] == "http://127.0.0.1:8070/v1"
    assert target["llm"]["model"] == "streaming-infer-adapter"
    assert target["llm"]["api_key"] == ""


# ---------------------------------------------------------------------------
# _tts_synthesize_handler error branches
# ---------------------------------------------------------------------------
def _make_request(payload=None, raw=None, raises=False):
    async def _json():
        if raises:
            raise ValueError("bad json")
        return payload if payload is not None else json.loads(raw)

    return SimpleNamespace(json=_json)


def test_tts_missing_text_returns_400():
    req = _make_request(payload={"text": "   "})
    resp = asyncio.run(tts_endpoint._tts_synthesize_handler(req))
    assert resp.status == 400
    assert json.loads(resp.text)["error"] == "text is required"


def test_tts_invalid_json_returns_400():
    req = _make_request(raw="{bad", raises=True)
    resp = asyncio.run(tts_endpoint._tts_synthesize_handler(req))
    assert resp.status == 400
    assert json.loads(resp.text)["error"] == "invalid json"


def test_tts_upstream_unreachable_returns_502():
    req = _make_request(payload={"text": "hi"})
    with patch("httpx.AsyncClient.post", side_effect=OSError("conn refused")):
        resp = asyncio.run(tts_endpoint._tts_synthesize_handler(req))
    assert resp.status == 502
    assert "upstream unreachable" in json.loads(resp.text)["error"]


def test_tts_upstream_5xx_returns_502():
    req = _make_request(payload={"text": "hi"})
    fake_resp = SimpleNamespace(status_code=503, json=lambda: {})
    with patch("httpx.AsyncClient.post", return_value=fake_resp):
        resp = asyncio.run(tts_endpoint._tts_synthesize_handler(req))
    assert resp.status == 502
    assert json.loads(resp.text)["error"] == "upstream error"


def test_tts_upstream_non_json_returns_502():
    req = _make_request(payload={"text": "hi"})
    fake_resp = SimpleNamespace(status_code=200, json=SimpleNamespace())
    fake_resp.json = lambda: (_ for _ in ()).throw(ValueError("no json"))
    with patch("httpx.AsyncClient.post", return_value=fake_resp):
        resp = asyncio.run(tts_endpoint._tts_synthesize_handler(req))
    assert resp.status == 502
    assert json.loads(resp.text)["error"] == "upstream non-json"


def test_tts_upstream_missing_pcm_bad_base64_returns_502():
    """Missing pcm16_base64 -> build_tts_synthesize_payload ValueError -> 502."""
    req = _make_request(payload={"text": "hi"})
    fake_resp = SimpleNamespace(status_code=200, json=lambda: {"sample_rate": 24000})
    with patch("httpx.AsyncClient.post", return_value=fake_resp):
        resp = asyncio.run(tts_endpoint._tts_synthesize_handler(req))
    assert resp.status == 502
    body = json.loads(resp.text)
    assert body["error"] == "upstream payload invalid"
    assert "pcm16_base64" in body["reason"]


def test_tts_upstream_bad_base64_returns_502():
    req = _make_request(payload={"text": "hi"})
    fake_resp = SimpleNamespace(
        status_code=200, json=lambda: {"pcm16_base64": "!!!not-base64!!!", "sample_rate": 24000}
    )
    with patch("httpx.AsyncClient.post", return_value=fake_resp):
        resp = asyncio.run(tts_endpoint._tts_synthesize_handler(req))
    assert resp.status == 502
    assert json.loads(resp.text)["error"] == "upstream payload invalid"


def test_tts_happy_path_wav_bytes():
    req = _make_request(payload={"text": "hi"})
    pcm = b"\x00\x01\x02\x03\x04\x05"
    b64 = base64.b64encode(pcm).decode()
    fake_resp = SimpleNamespace(
        status_code=200, json=lambda: {"pcm16_base64": b64, "sample_rate": 24000, "channels": 1}
    )
    with patch("httpx.AsyncClient.post", return_value=fake_resp):
        resp = asyncio.run(tts_endpoint._tts_synthesize_handler(req))
    assert resp.status == 200
    assert resp.content_type == "audio/wav"
    body = resp.body
    assert body[:4] == b"RIFF"
    assert body[8:12] == b"WAVE"
    assert body[36:40] == b"data"
    assert body[40:44] == len(pcm).to_bytes(4, "little")
    assert body[44:] == pcm


def test_wav_chunk_header_shape():
    h = tts_endpoint._wav_chunk_header(24000, 1, 16)
    assert len(h) == 44
    assert h[22:24] == (1).to_bytes(2, "little")  # channels
    assert h[24:28] == (24000).to_bytes(4, "little")  # sample rate
    assert h[28:32] == (24000 * 1 * 16 // 8).to_bytes(4, "little")  # byte rate


# ---------------------------------------------------------------------------
# notify contract semantics (zero-behavior change spot checks)
# ---------------------------------------------------------------------------
def test_send_to_session_drops_when_no_targets(caplog):
    """No WS targets -> INFO log, no exception (contract unchanged)."""
    import logging

    with (
        patch.object(ws_notify, "session_websockets", {}),
        caplog.at_level(logging.INFO, logger="joy_interaction_webui.ws_notify"),
    ):
        ws_notify.send_to_session("ghost-session", "hello")
    assert any("no WS targets" in r.message for r in caplog.records)


def test_notify_session_llm_reply_payload_shape():
    """Payload shape must be identical to pre-split contract."""
    captured = {}
    with patch.object(
        server, "send_to_session", side_effect=lambda sid, msg: captured.update(sid=sid, msg=msg)
    ):
        ws_notify.session_websockets["sess-shape"] = {"fake"}
        ws_notify.notify_session_llm_reply(
            "sess-shape", "hello world", source="jarvis", reply_epoch=3
        )
    payload = json.loads(captured["msg"])
    assert payload["type"] == "llm_reply"
    assert payload["text"] == "hello world"
    assert payload["source"] == "jarvis"
    assert payload["reply_epoch"] == 3
    assert isinstance(payload["ts"], float)
