"""QA 独立补充边界用例 — layer 1 live visual path (spec live-visual-cb.md §3 层 1).

These tests pin additional boundary contracts NOT explicitly covered by the
engineer's ``test_live_visual.py``:

  * frames exactly at the upper limit (6) is accepted; 7 is an explicit 400;
  * ``image_b64`` carrying a ``data:image/...;base64,`` prefix — documented
    boundary: the wire contract is RAW base64 (the frontend screen-capture
    pipeline sends raw base64), so a data-URI-prefixed value is rejected with
    an explicit 400 (约法三章 — never silently swallowed). Tolerance of the
    prefix is a hardening suggestion, not a spec wire-format requirement;
  * empty ``frames`` array vs missing ``image_b64`` field — two distinct
    semantic outcomes (no-frames text path vs explicit 400);
  * live + frames + stream=false (the proactive round shape) parses the
    four-state decision from the non-streaming response body.

Run: python -m pytest tests/test_qa_live_visual_boundary.py -q
"""

from __future__ import annotations

import asyncio
import base64
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from infer_loop import _LIVE_FRAMES_MAX, _parse_live_frames  # noqa: E402


def _b64(payload: bytes = b"\xff\xd8\xff\xe0 fake-jpeg") -> str:
    return base64.b64encode(payload).decode("ascii")


def _frame(index: int = 0, *, ts: float = 1000.0, b64: str | None = None) -> dict[str, Any]:
    return {
        "image_b64": b64 if b64 is not None else _b64(b"frame-" + str(index).encode()),
        "ts_ms": ts,
    }


# ---------------------------------------------------------------------------
# 1. Upper-limit boundary: exactly 6 accepted, 7 -> 400
# ---------------------------------------------------------------------------


def test_parse_frames_exactly_max_accepted():
    frames = _parse_live_frames({"frames": [_frame(i, ts=float(i)) for i in range(_LIVE_FRAMES_MAX)]})
    assert len(frames) == _LIVE_FRAMES_MAX == 6
    assert [f["ts_ms"] for f in frames] == [float(i) for i in range(_LIVE_FRAMES_MAX)]
    assert all(f["image_b64"] for f in frames)


def test_parse_frames_max_plus_one_rejected():
    with pytest.raises(web.HTTPBadRequest) as exc_info:
        _parse_live_frames({"frames": [_frame(i) for i in range(_LIVE_FRAMES_MAX + 1)]})
    assert exc_info.value.status_code == 400
    assert "exceeds limit" in exc_info.value.text
    assert str(_LIVE_FRAMES_MAX) in exc_info.value.text


# ---------------------------------------------------------------------------
# 2. data-URI prefix: QA hardening contract (tolerated + stripped, not 400)
# ---------------------------------------------------------------------------


def test_parse_frames_data_uri_prefix_stripped():
    """QA hardening (d9736ee): a full ``data:image/<fmt>;base64,<b64>`` value
    is accepted; the prefix is stripped and the normalized output is RAW
    base64 so the visual message builder prepends its prefix exactly once.
    Non-base64 / empty-payload data URIs remain explicit 400s."""
    raw = _b64(b"jpeg-bytes")
    frames = _parse_live_frames(
        {"frames": [{"image_b64": f"data:image/jpeg;base64,{raw}", "ts_ms": 1}]}
    )
    assert frames[0]["image_b64"] == raw
    assert "data:" not in frames[0]["image_b64"]


def test_parse_frames_data_uri_non_base64_rejected():
    """A data URI without a base64 payload marker is an explicit 400."""
    with pytest.raises(web.HTTPBadRequest) as exc_info:
        _parse_live_frames(
            {"frames": [{"image_b64": "data:image/jpeg;plain,abc", "ts_ms": 1}]}
        )
    assert exc_info.value.status_code == 400
    assert "must be base64" in exc_info.value.text


def test_parse_frames_data_uri_empty_payload_rejected():
    """A data URI with an empty base64 payload is an explicit 400."""
    with pytest.raises(web.HTTPBadRequest) as exc_info:
        _parse_live_frames(
            {"frames": [{"image_b64": "data:image/jpeg;base64,", "ts_ms": 1}]}
        )
    assert exc_info.value.status_code == 400
    assert "no base64 payload" in exc_info.value.text


def test_parse_frames_data_uri_prefix_not_double_prepended():
    """Downstream invariant: the normalized raw base64 round-trips through
    _build_live_visual_messages with the data-URI prefix added exactly once."""
    from prompt_assembly import _build_live_visual_messages

    raw = _b64(b"jpeg-bytes")
    frames = _parse_live_frames(
        {"frames": [{"image_b64": f"data:image/png;base64,{raw}", "ts_ms": 1}]}
    )
    messages = _build_live_visual_messages("sys", "look", frames)
    user_content = messages[-1]["content"]
    urls = [
        part["image_url"]["url"]
        for part in user_content
        if isinstance(part, dict) and part.get("type") == "image_url"
    ]
    assert urls == [f"data:image/jpeg;base64,{raw}"]  # exactly one prefix


# ---------------------------------------------------------------------------
# 3. Empty frames vs missing image_b64 — two distinct semantic outcomes
# ---------------------------------------------------------------------------


def test_parse_frames_empty_array_means_no_frames():
    """[] -> normalized empty list (callers keep the pure-text path)."""
    assert _parse_live_frames({"frames": []}) == []


def test_parse_frames_missing_image_b64_is_400():
    """A dict without image_b64 is a malformed frame -> explicit 400."""
    with pytest.raises(web.HTTPBadRequest) as exc_info:
        _parse_live_frames({"frames": [{"ts_ms": 1}]})
    assert exc_info.value.status_code == 400
    assert "image_b64" in exc_info.value.text


# ---------------------------------------------------------------------------
# 4. live + frames + stream=false (proactive round) decision parsing
# ---------------------------------------------------------------------------


@dataclass
class _StubFullMessage:
    content: str


@dataclass
class _StubFullChoice:
    message: _StubFullMessage


@dataclass
class _StubUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def model_dump(self) -> dict[str, int]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }


@dataclass
class _StubFullCompletion:
    choices: list[Any]
    usage: Any | None = None


class _StubCompletions:
    """Non-streaming completions stub: returns scripted content + records kwargs."""

    def __init__(self, scripted: list[str] | None = None) -> None:
        self._scripted = list(scripted or [])
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        content = self._scripted.pop(0) if self._scripted else "</silence>"
        return _StubFullCompletion(
            choices=[_StubFullChoice(message=_StubFullMessage(content=content))],
            usage=_StubUsage(),
        )


class _StubChatNamespace:
    def __init__(self, completions: _StubCompletions) -> None:
        self.completions = completions


class _StubAsyncOpenAI:
    def __init__(self, completions: _StubCompletions) -> None:
        self.chat = _StubChatNamespace(completions)


def _make_adapter(scripted: list[str] | None = None):
    from live_adapter import AdapterConfig, StreamingInferAdapter
    from memory_store_client import MemoryStoreClient

    cfg = AdapterConfig()
    cfg.enable_summarizer = False
    cfg.character_prompts_enabled = False
    cfg.memory_store_enabled = False
    cfg.main_ctx_tokens = 16384

    adapter = StreamingInferAdapter.__new__(StreamingInferAdapter)
    adapter.config = cfg
    adapter.sessions = {}
    adapter._cleanup_task = None
    adapter._character_prompt_mtime = 0.0
    adapter._system_prompt_cache = {}
    adapter._invalidate_system_prompt_cache = lambda: None
    adapter.memory_store = MemoryStoreClient(base_url="http://127.0.0.1:8997", enabled=False)

    stub = _StubCompletions(scripted=scripted)
    client_stub = _StubAsyncOpenAI(stub)
    adapter.main_client = client_stub
    adapter.main_clients = {cfg.main_model: (client_stub, cfg.main_model)}
    adapter.summarizer = None
    return adapter, stub


class _StreamProtocol:
    def resume_reading(self, *args, **kwargs):
        pass

    def pause_reading(self, *args, **kwargs):
        pass


def _post_json(adapter: Any, body: dict[str, Any], session_id=None) -> Any:
    from aiohttp import streams

    raw = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if session_id:
        headers["x-streaming-session"] = session_id
    loop = asyncio.new_event_loop()
    stream = streams.StreamReader(
        protocol=_StreamProtocol(),
        limit=2**16,
        loop=loop,
    )
    stream.feed_data(raw)
    stream.feed_eof()
    request = make_mocked_request("POST", "/v1/text/chat", headers=headers, payload=stream)

    async def _run():
        return await adapter.handle_text_chat(request)

    return _run()


@pytest.mark.asyncio
async def test_live_visual_non_streaming_response_decision():
    """Proactive round shape: live + frames, stream=false -> 200 + decision body.

    The response body is the standard non-streaming payload: choices[0].message
    content plus the streamingharness.decision field carrying the four-state
    token (the proactive caller reads decision from ``streamingharness``).
    """
    adapter, stub = _make_adapter(scripted=["</response> BOSS is here."])
    body = {
        "model": "joyai-vl-interaction-preview",
        "messages": [{"role": "user", "content": ""}],
        "interaction_mode": "live",
        "frames": [_frame(0)],
        # NOTE: no "stream" key -> the router takes the non-streaming path.
    }
    resp = await _post_json(adapter, body, session_id="lv-boundary-1")
    assert resp.status == 200
    payload = json.loads(resp.text)
    harness = payload.get("streamingharness") or {}
    # The four-state decision must survive the round-trip (parse_model_decision
    # upstream turns </response> into the harness decision).
    assert "decision" in harness
    choice = (payload.get("choices") or [{}])[0]
    assert (choice.get("message") or {}).get("content")
    # The stub saw the visual request: frames carried to the multimodal call.
    last_call = stub.calls[-1]
    assert last_call.get("stream") is False or "stream" not in last_call
    assert any(
        isinstance(part, dict) and part.get("type") == "image_url"
        for part in last_call["messages"][-1]["content"]
    )
