"""Tests for the live visual path (spec draft-live-visual-cb.md §3 层 1).

Layer 1 adds an optional top-level ``frames`` field to ``POST /v1/text/chat``
when ``interaction_mode="live"``:

  * ``frames`` validation (list, <=6, non-empty base64 ``image_b64``,
    numeric ``ts_ms``) — invalid frames are an explicit 400 (约法三章, never
    silently swallowed), missing/empty frames keep the pure-text path;
  * live visual message assembly (four-state live system prompt +
    visual-observation segment + user text + OpenAI ``image_url`` data URIs);
  * routing — ``live`` + frames -> multimodal (streaming for user rounds,
    non-streaming for proactive rounds); non-live modes reject frames;
  * decision parsing — the four-state decision/content protocol is unchanged
    (``parse_model_decision`` / streaming decision-first frames);
  * zero regression — no ``frames`` field runs the existing text path
    byte-for-byte (no image parts ever reach the client).

Run: python -m pytest tests/test_live_visual.py -q
"""

from __future__ import annotations

import asyncio
import base64
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from aiohttp.test_utils import make_mocked_request

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from infer_loop import _LIVE_FRAMES_MAX, _parse_live_frames  # noqa: E402
from prompt_assembly import (  # noqa: E402
    LIVE_VISUAL_OBSERVATION_SEGMENT,
    _build_live_visual_messages,
)
from prompt_constants import LIVE_SYSTEM_PROMPT_EN  # noqa: E402


def _b64(payload: bytes = b"\xff\xd8\xff\xe0 fake-jpeg") -> str:
    """A syntactically valid (but tiny) base64 payload."""
    return base64.b64encode(payload).decode("ascii")


def _frame(index: int = 0, *, ts: float = 1000.0, b64: str | None = None) -> dict[str, Any]:
    return {
        "image_b64": b64 if b64 is not None else _b64(b"frame-" + str(index).encode()),
        "ts_ms": ts,
    }


# ---------------------------------------------------------------------------
# _parse_live_frames — payload validation
# ---------------------------------------------------------------------------


def test_parse_frames_valid():
    frames = _parse_live_frames({"frames": [_frame(0), _frame(1, ts=1500.0)]})
    assert len(frames) == 2
    assert frames[0]["image_b64"] == _b64(b"frame-0")
    assert frames[0]["ts_ms"] == 1000.0
    assert frames[1]["ts_ms"] == 1500.0


def test_parse_frames_missing_is_empty():
    assert _parse_live_frames({}) == []
    assert _parse_live_frames({"frames": None}) == []


def test_parse_frames_empty_list_is_empty():
    assert _parse_live_frames({"frames": []}) == []


def test_parse_frames_over_limit_rejected():
    with pytest.raises(Exception) as exc_info:
        _parse_live_frames({"frames": [_frame(i) for i in range(_LIVE_FRAMES_MAX + 1)]})
    assert exc_info.value.status_code == 400
    assert "exceeds limit" in exc_info.value.text


def test_parse_frames_not_a_list_rejected():
    with pytest.raises(Exception) as exc_info:
        _parse_live_frames({"frames": "not-a-list"})
    assert exc_info.value.status_code == 400


def test_parse_frames_non_dict_item_rejected():
    with pytest.raises(Exception) as exc_info:
        _parse_live_frames({"frames": ["nope"]})
    assert exc_info.value.status_code == 400
    assert "must be an object" in exc_info.value.text


def test_parse_frames_empty_base64_rejected():
    with pytest.raises(Exception) as exc_info:
        _parse_live_frames({"frames": [{"image_b64": "", "ts_ms": 1}]})
    assert exc_info.value.status_code == 400
    assert "non-empty base64" in exc_info.value.text


def test_parse_frames_missing_base64_rejected():
    with pytest.raises(Exception) as exc_info:
        _parse_live_frames({"frames": [{"ts_ms": 1}]})
    assert exc_info.value.status_code == 400


def test_parse_frames_non_base64_rejected():
    with pytest.raises(Exception) as exc_info:
        _parse_live_frames({"frames": [{"image_b64": "!!!not-base64!!!", "ts_ms": 1}]})
    assert exc_info.value.status_code == 400
    assert "not valid base64" in exc_info.value.text


def test_parse_frames_unpadded_base64_accepted():
    # Frontends may strip '=' padding; the parser tolerates it.
    raw = base64.b64encode(b"jpeg-bytes").decode("ascii").rstrip("=")
    frames = _parse_live_frames({"frames": [{"image_b64": raw, "ts_ms": 1}]})
    assert frames[0]["image_b64"] == raw


def test_parse_frames_data_uri_prefix_stripped():
    """A full ``data:image/jpeg;base64,<b64>`` value is accepted and normalized
    to raw base64 (QA hardening; the contract stays raw base64)."""
    raw = base64.b64encode(b"jpeg-bytes").decode("ascii")
    frames = _parse_live_frames(
        {"frames": [{"image_b64": f"data:image/jpeg;base64,{raw}", "ts_ms": 1}]}
    )
    assert frames[0]["image_b64"] == raw


def test_parse_frames_data_uri_non_base64_rejected():
    """A data URI without a base64 payload marker is an explicit 400."""
    with pytest.raises(Exception) as exc_info:
        _parse_live_frames({"frames": [{"image_b64": "data:image/jpeg;plain,abc", "ts_ms": 1}]})
    assert exc_info.value.status_code == 400


def test_parse_frames_data_uri_empty_payload_rejected():
    with pytest.raises(Exception) as exc_info:
        _parse_live_frames({"frames": [{"image_b64": "data:image/jpeg;base64,", "ts_ms": 1}]})
    assert exc_info.value.status_code == 400


def test_parse_frames_bad_ts_rejected():
    with pytest.raises(Exception) as exc_info:
        _parse_live_frames({"frames": [{"image_b64": _b64(), "ts_ms": "soon"}]})
    assert exc_info.value.status_code == 400
    assert "ts_ms must be a number" in exc_info.value.text


# ---------------------------------------------------------------------------
# _build_live_visual_messages — message assembly
# ---------------------------------------------------------------------------


def test_visual_messages_system_has_four_state_plus_visual_segment():
    messages = _build_live_visual_messages(
        LIVE_SYSTEM_PROMPT_EN, "how do I beat the boss?", [_frame(0)]
    )
    assert len(messages) == 2
    system = messages[0]
    assert system["role"] == "system"
    assert LIVE_SYSTEM_PROMPT_EN in system["content"]
    assert "The following image frames are the current visual context" in system["content"]
    assert LIVE_VISUAL_OBSERVATION_SEGMENT.strip() in system["content"]


def test_visual_messages_user_has_text_and_image_urls():
    frames = [_frame(0), _frame(1)]
    messages = _build_live_visual_messages(LIVE_SYSTEM_PROMPT_EN, "what's on screen?", frames)
    user = messages[-1]
    assert user["role"] == "user"
    assert isinstance(user["content"], list)
    text_parts = [p for p in user["content"] if p.get("type") == "text"]
    image_parts = [p for p in user["content"] if p.get("type") == "image_url"]
    assert len(text_parts) == 1
    assert text_parts[0]["text"] == "what's on screen?"
    assert len(image_parts) == 2
    for frame, part in zip(frames, image_parts):
        url = part["image_url"]["url"]
        assert url.startswith("data:image/jpeg;base64,")
        assert url[len("data:image/jpeg;base64,") :] == frame["image_b64"]


def test_visual_messages_proactive_empty_text_images_only():
    """Proactive rounds carry no user text: text part omitted, images only."""
    messages = _build_live_visual_messages(LIVE_SYSTEM_PROMPT_EN, "", [_frame(0)])
    user = messages[-1]
    text_parts = [p for p in user["content"] if p.get("type") == "text"]
    image_parts = [p for p in user["content"] if p.get("type") == "image_url"]
    assert text_parts == []
    assert len(image_parts) == 1


def test_visual_messages_history_inserted_between_system_and_user():
    history = [
        {"role": "user", "content": "earlier turn"},
        {"role": "assistant", "content": "earlier reply"},
    ]
    messages = _build_live_visual_messages(
        LIVE_SYSTEM_PROMPT_EN,
        "now?",
        [_frame(0)],
        history_messages=history,
    )
    assert len(messages) == 4
    assert messages[0]["role"] == "system"
    assert [m["role"] for m in messages[1:-1]] == ["user", "assistant"]
    assert messages[-1]["role"] == "user"
    assert messages[-1]["content"][-1]["type"] == "image_url"


# ---------------------------------------------------------------------------
# HTTP-level tests: routing + decision parsing (non-streaming)
# ---------------------------------------------------------------------------


@dataclass
class _StubChoice:
    message: _StubMessage


@dataclass
class _StubMessage:
    content: str


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
class _StubChatCompletion:
    choices: list[_StubChoice]
    usage: _StubUsage | None = None


@dataclass
class _StubDelta:
    content: str | None = None


@dataclass
class _StubStreamChoice:
    delta: _StubDelta


@dataclass
class _StubStreamChunk:
    choices: list[Any] = field(default_factory=list)
    usage: Any = None


@dataclass
class _StubFullMessage:
    content: str


@dataclass
class _StubFullChoice:
    message: _StubFullMessage


@dataclass
class _StubFullCompletion:
    choices: list[Any]
    usage: Any = None


class _AsyncStream:
    """Async iterable of openai-style stream chunks."""

    def __init__(self, deltas: list[str]) -> None:
        self._chunks: list[_StubStreamChunk] = []
        for delta in deltas:
            self._chunks.append(
                _StubStreamChunk(choices=[_StubStreamChoice(delta=_StubDelta(content=delta))])
            )
        if self._chunks:
            self._chunks[-1].usage = _StubUsage()

    def __aiter__(self) -> _AsyncStream:
        self._it = iter(self._chunks)
        return self

    async def __anext__(self) -> _StubStreamChunk:
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration from None


class _StubCompletions:
    """client.chat.completions stub that scripts stream or full responses."""

    def __init__(self, scripted: list[str] | None = None) -> None:
        self._scripted = list(scripted or [])
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if kwargs.get("stream"):
            deltas = [self._scripted.pop(0)] if self._scripted else []
            return _AsyncStream(deltas)
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
    """Construct a minimal StreamingInferAdapter with a stubbed main client."""
    from live_adapter import AdapterConfig, StreamingInferAdapter
    from memory_store_client import MemoryStoreClient

    cfg = AdapterConfig()
    cfg.enable_summarizer = False
    cfg.character_prompts_enabled = False  # keep system prompt predictable
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
    adapter.main_client = client_stub  # type: ignore[assignment]
    adapter.main_clients = {cfg.main_model: (client_stub, cfg.main_model)}  # type: ignore[assignment]
    adapter.summarizer = None
    return adapter, stub


class _StreamProtocol:
    """Minimal protocol stub aiohttp.streams.StreamReader needs to feed_data."""

    def resume_reading(self, *args, **kwargs):
        pass

    def pause_reading(self, *args, **kwargs):
        pass


def _post_json(adapter: Any, body: dict[str, Any], session_id=None) -> Any:
    """Build a mocked POST request and dispatch to handle_text_chat."""
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


def _visual_body(
    *, stream: bool = False, mode: str = "live", text: str = "what's on screen?"
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": "joyai-vl-interaction-preview",
        "messages": [{"role": "user", "content": text}],
        "interaction_mode": mode,
        "frames": [_frame(0), _frame(1)],
    }
    if stream:
        body["stream"] = True
    return body


# --- routing: live + frames -> multimodal (non-streaming) ------------------


@pytest.mark.asyncio
async def test_live_visual_non_streaming_routes_multimodal():
    adapter, stub = _make_adapter(scripted=["</response> 你屏幕上现在显示的是地图。"])

    resp = await _post_json(adapter, _visual_body(), session_id="lv1")
    assert resp.status == 200
    payload = json.loads(resp.text)
    assert payload["choices"][0]["message"]["content"] == "你屏幕上现在显示的是地图。"
    assert payload["streamingharness"]["decision"] == "response"

    # The client received OpenAI image_url parts (multimodal route).
    messages = stub.calls[0]["messages"]
    image_parts = [
        part
        for message in messages
        if isinstance(message.get("content"), list)
        for part in message["content"]
        if part.get("type") == "image_url"
    ]
    assert len(image_parts) == 2
    assert image_parts[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    # The system prompt carries the four-state prompt + the visual segment.
    system = messages[0]
    assert LIVE_SYSTEM_PROMPT_EN in system["content"]
    assert "current visual context" in system["content"]


@pytest.mark.asyncio
async def test_live_visual_not_for_me_decision():
    adapter, _stub = _make_adapter(scripted=["</not-for-me>"])

    resp = await _post_json(adapter, _visual_body(text="这关怎么这么难啊"), session_id="lv2")
    assert resp.status == 200
    payload = json.loads(resp.text)
    assert payload["streamingharness"]["decision"] == "not-for-me"
    assert payload["choices"][0]["message"]["content"] == ""


@pytest.mark.asyncio
async def test_live_visual_silence_decision():
    adapter, _stub = _make_adapter(scripted=["</silence>"])

    resp = await _post_json(adapter, _visual_body(), session_id="lv3")
    assert resp.status == 200
    payload = json.loads(resp.text)
    assert payload["streamingharness"]["decision"] == "silence"


@pytest.mark.asyncio
async def test_live_visual_proactive_empty_text_no_qa_history_pollution():
    """Proactive rounds (no user text) must not pollute qa_history."""
    adapter, stub = _make_adapter(scripted=["</response> 画面里有只精英怪。"])

    resp = await _post_json(
        adapter,
        {
            "model": "joyai-vl-interaction-preview",
            "messages": [{"role": "user", "content": ""}],
            "interaction_mode": "live",
            "frames": [_frame(0)],
        },
        session_id="lv4",
    )
    assert resp.status == 200
    state = adapter.sessions["lv4"]
    assert len(state.memory_state.get("qa_history", [])) == 0
    # The user message sent to the model has NO text part (images only).
    messages = stub.calls[0]["messages"]
    user = messages[-1]
    text_parts = [p for p in user["content"] if p.get("type") == "text"]
    assert text_parts == []


@pytest.mark.asyncio
async def test_live_visual_invalid_frames_400_explicit():
    adapter, _stub = _make_adapter()
    body = _visual_body()
    body["frames"] = [_frame(0), {"image_b64": "", "ts_ms": 1}]
    resp = await _post_json(adapter, body, session_id="lv5")
    assert resp.status == 400
    payload = json.loads(resp.text)
    assert "non-empty base64" in payload["error"]["message"]


@pytest.mark.asyncio
async def test_live_visual_over_limit_frames_400():
    adapter, _stub = _make_adapter()
    body = _visual_body()
    body["frames"] = [_frame(i) for i in range(_LIVE_FRAMES_MAX + 1)]
    resp = await _post_json(adapter, body, session_id="lv6")
    assert resp.status == 400
    assert "exceeds limit" in json.loads(resp.text)["error"]["message"]


@pytest.mark.asyncio
async def test_non_live_mode_with_frames_rejected():
    adapter, _stub = _make_adapter()
    resp = await _post_json(adapter, _visual_body(mode="jarvis"), session_id="lv7")
    assert resp.status == 400
    payload = json.loads(resp.text)
    assert "interaction_mode='live'" in payload["error"]["message"]


# --- zero regression: no frames -> pure text path --------------------------


@pytest.mark.asyncio
async def test_no_frames_pure_text_path_unchanged():
    adapter, stub = _make_adapter(scripted=["</response> ok"])

    resp = await _post_json(
        adapter,
        {"model": "joyai-vl-interaction-preview", "messages": [{"role": "user", "content": "hi"}]},
        session_id="lv8",
    )
    assert resp.status == 200
    payload = json.loads(resp.text)
    assert payload["choices"][0]["message"]["content"] == "ok"
    assert payload["streamingharness"]["decision"] == "response"

    messages = stub.calls[0]["messages"]
    for message in messages:
        content = message.get("content")
        if isinstance(content, list):
            assert all(part.get("type") != "image_url" for part in content)
    # No image parts anywhere -> the pure-text path is byte-for-byte unchanged.
    image_parts = [
        part
        for message in messages
        if isinstance(message.get("content"), list)
        for part in message["content"]
        if part.get("type") == "image_url"
    ]
    assert image_parts == []


@pytest.mark.asyncio
async def test_empty_frames_list_keeps_pure_text_path():
    """``frames: []`` is equivalent to 'no frames' (zero regression)."""
    adapter, stub = _make_adapter(scripted=["</silence>"])
    body = {
        "model": "joyai-vl-interaction-preview",
        "messages": [{"role": "user", "content": "hi"}],
        "frames": [],
    }
    resp = await _post_json(adapter, body, session_id="lv9")
    assert resp.status == 200
    payload = json.loads(resp.text)
    assert payload["streamingharness"]["decision"] == "silence"
    messages = stub.calls[0]["messages"]
    image_parts = [
        part
        for message in messages
        if isinstance(message.get("content"), list)
        for part in message["content"]
        if part.get("type") == "image_url"
    ]
    assert image_parts == []


# --- routing: live + frames + stream -> multimodal streaming ---------------


def _parse_ndjson(text: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


async def _post_streaming(adapter: Any, body: dict[str, Any]) -> tuple[int, str]:
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    app = web.Application()
    app.router.add_post("/v1/text/chat", adapter.handle_text_chat)
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    try:
        resp = await client.post("/v1/text/chat", json=body)
        text = await resp.text()
        return resp.status, text
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_live_visual_streaming_decision_first_content_stream():
    adapter, stub = _make_adapter(scripted=["</response> 左边有个宝箱。"])

    status, text = await _post_streaming(adapter, _visual_body(stream=True))
    assert status == 200
    frames = _parse_ndjson(text)
    assert [f["type"] for f in frames] == ["decision", "content", "done"]
    assert frames[0]["decision"] == "response"
    assert frames[-1]["full_text"] == "左边有个宝箱。"

    # The streaming client call carried image_url parts (multimodal route).
    messages = stub.calls[0]["messages"]
    image_parts = [
        part
        for message in messages
        if isinstance(message.get("content"), list)
        for part in message["content"]
        if part.get("type") == "image_url"
    ]
    assert len(image_parts) == 2
    assert image_parts[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")


@pytest.mark.asyncio
async def test_live_visual_streaming_silence_no_content():
    adapter, _stub = _make_adapter(scripted=["</silence>"])
    status, text = await _post_streaming(adapter, _visual_body(stream=True))
    assert status == 200
    frames = _parse_ndjson(text)
    assert [f["type"] for f in frames] == ["decision", "done"]
    assert frames[0]["decision"] == "silence"
    assert frames[-1]["full_text"] == ""


@pytest.mark.asyncio
async def test_live_visual_streaming_proactive_empty_text():
    """Streaming proactive round: no text part, images only, decision parsed."""
    adapter, stub = _make_adapter(scripted=["</not-for-me>"])
    body = {
        "model": "joyai-vl-interaction-preview",
        "messages": [{"role": "user", "content": ""}],
        "interaction_mode": "live",
        "frames": [_frame(0)],
        "stream": True,
    }
    status, text = await _post_streaming(adapter, body)
    assert status == 200
    frames = _parse_ndjson(text)
    assert frames[0]["type"] == "decision"
    assert frames[0]["decision"] == "not-for-me"
    messages = stub.calls[0]["messages"]
    user = messages[-1]
    assert [p for p in user["content"] if p.get("type") == "text"] == []
