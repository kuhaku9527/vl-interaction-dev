"""Tests for the P0-A streaming text-chat path (``stream: true``).

The streaming protocol (NDJSON frames on ``POST /v1/text/chat``):

  * frame 1 (once a complete decision marker is seen, normally the first):
    ``{"type": "decision", "decision": "...", "delegation_question": ...}``
  * then ``{"type": "content", "token": "..."}`` frames for each content
    delta while the decision is ``response`` (never for silence/delegation);
  * final ``{"type": "done", ...}`` frame with usage + full text;
  * ``{"type": "error", ...}`` only on internal failure.

Decision semantics are byte-for-byte identical to the non-streaming path:
the decision is derived by :func:`parse_model_decision` over the accumulated
prefix. The non-streaming path is untouched (regression covered here by
asserting ``create`` is called without ``stream`` when the payload omits it).
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from infer_loop import (  # noqa: E402
    _find_first_decision_marker,
    build_stream_frames,
)
from response_format import parse_model_decision  # noqa: E402


# ---------------------------------------------------------------------------
# Pure frame-builder tests (no HTTP / no model)
# ---------------------------------------------------------------------------


def _types(frames: list[dict[str, Any]]) -> list[str]:
    return [f["type"] for f in frames]


def test_frames_decision_first_then_content():
    """``</response>`` first, then body deltas -> decision + content frames."""
    frames = build_stream_frames(["</response>", " Hello", " world!"])
    assert _types(frames) == ["decision", "content", "content"]
    assert frames[0]["decision"] == "response"
    assert frames[0]["delegation_question"] is None
    assert "".join(f["token"] for f in frames if f["type"] == "content") == " Hello world!"


def test_frames_decision_and_body_in_one_delta():
    """Decision token and first body text arrive in a single delta."""
    frames = build_stream_frames(["</response> Confirmed, iron lady."])
    assert _types(frames) == ["decision", "content"]
    assert frames[0]["decision"] == "response"
    # The initial content goes through parse_model_decision, which strips the
    # leading whitespace after the decision token (same as the non-streaming
    # content field). Deltas AFTER the decision stream verbatim.
    assert frames[1]["token"] == "Confirmed, iron lady."


def test_frames_silence_has_no_content():
    """``</silence>`` -> decision frame only; trailing whitespace never streams."""
    frames = build_stream_frames(["</silence>", "   ", ""])
    assert _types(frames) == ["decision"]
    assert frames[0]["decision"] == "silence"


def test_frames_silence_alone():
    frames = build_stream_frames(["</silence>"])
    assert _types(frames) == ["decision"]
    assert frames[0]["decision"] == "silence"


def test_frames_delegation_question_not_streamed_as_content():
    """Delegation: the question rides in the decision frame, never as content.

    The model emits a short foreground line, then ``</delegation> <question>``
    as the LAST token; the foreground line is buffered (decision unknown) and
    the question must not be spoken as TTS.
    """
    frames = build_stream_frames(
        ["Looking that up.", "</delegation> 查 Cyberpunk 2077 螳螂帮打法攻略"]
    )
    assert _types(frames) == ["decision"]
    assert frames[0]["decision"] == "delegation"
    assert frames[0]["delegation_question"] == "查 Cyberpunk 2077 螳螂帮打法攻略"


def test_frames_empty_deltas_are_skipped():
    """No-token / empty deltas never produce a frame."""
    frames = build_stream_frames(["", "</response>", "", "hi", ""])
    assert _types(frames) == ["decision", "content"]
    assert frames[1]["token"] == "hi"


def test_frames_no_decision_marker_fails_open_to_response():
    """Stream ending without any complete marker -> response with full text.

    The accumulated text is flushed through the unified parser in a single
    content frame (matching how a marker-less non-streaming response is
    treated as ``response`` with the full text as content).
    """
    frames = build_stream_frames(["hello", " world"])
    assert _types(frames) == ["decision", "content"]
    assert frames[0]["decision"] == "response"
    assert frames[1]["token"] == "hello world"


def test_frames_marker_split_across_deltas():
    """A decision marker spanning chunk boundaries is still detected once."""
    frames = build_stream_frames(["</resp", "onse> Hello"])
    assert _types(frames) == ["decision", "content"]
    assert frames[0]["decision"] == "response"
    assert frames[1]["token"] == "Hello"


def test_frames_empty_stream_is_silence():
    """An empty stream fails open to the parser's empty-output decision."""
    frames = build_stream_frames([])
    assert _types(frames) == ["decision"]
    assert frames[0]["decision"] == "silence"


def test_find_first_decision_marker():
    assert _find_first_decision_marker("</response> hi") == 0
    assert _find_first_decision_marker("hi </silence>") == 3
    assert _find_first_decision_marker("x </delegation> q") == 2
    assert _find_first_decision_marker("hello") is None
    assert _find_first_decision_marker("</resp") is None  # partial marker
    assert _find_first_decision_marker("") is None


def test_stream_decision_matches_non_streaming_parser():
    """For a full raw text, the streamed decision == parse_model_decision."""
    raw = "</response> Confirmed, iron lady."
    frames = build_stream_frames([raw])
    decision, clean, delegation_q = parse_model_decision(raw)
    assert frames[0]["decision"] == decision == "response"
    assert frames[0]["delegation_question"] == delegation_q
    assert "".join(f["token"] for f in frames if f["type"] == "content") == clean


# ---------------------------------------------------------------------------
# HTTP-level tests: real aiohttp app + TestClient against the NDJSON seam
# ---------------------------------------------------------------------------


@dataclass
class _StubUsage:
    prompt_tokens: int = 10
    completion_tokens: int = 5
    total_tokens: int = 15

    def model_dump(self) -> dict[str, int]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }


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


class _StubCompletions:
    """client.chat.completions stub that scripts either stream or full responses."""

    def __init__(self, scripted_deltas: list[list[str]] | None = None) -> None:
        self.scripted_deltas: list[list[str]] = list(scripted_deltas or [])
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if kwargs.get("stream"):
            deltas = self.scripted_deltas.pop(0) if self.scripted_deltas else []
            return _AsyncStream(deltas)
        content = "</silence>"
        if self.scripted_deltas:
            content = "".join(self.scripted_deltas.pop(0))
        return _StubFullCompletion(
            choices=[_StubFullChoice(message=_StubFullMessage(content=content))],
            usage=_StubUsage(),
        )


class _StubChatNamespace:
    """client.chat -> _StubChatNamespace; .completions -> _StubCompletions."""

    def __init__(self, completions: _StubCompletions) -> None:
        self.completions = completions


class _StubAsyncOpenAI:
    """Mirrors the openai SDK layout: client.chat.completions.create(...)."""

    def __init__(self, completions: _StubCompletions) -> None:
        self.chat = _StubChatNamespace(completions)


class _AsyncStream:
    """Async iterable of openai-style stream chunks."""

    def __init__(self, deltas: list[str]) -> None:
        self._chunks: list[_StubStreamChunk] = []
        for i, delta in enumerate(deltas):
            is_last = i == len(deltas) - 1
            self._chunks.append(
                _StubStreamChunk(choices=[_StubStreamChoice(delta=_StubDelta(content=delta))])
            )
        if self._chunks:
            self._chunks[-1].usage = _StubUsage()

    def __aiter__(self) -> "_AsyncStream":
        self._it = iter(self._chunks)
        return self

    async def __anext__(self) -> _StubStreamChunk:
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


def _make_adapter(scripted_deltas: list[list[str]] | None = None):
    """Construct a minimal StreamingInferAdapter with a stubbed main client."""
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

    stub = _StubCompletions(scripted_deltas=scripted_deltas)
    client_stub = _StubAsyncOpenAI(stub)
    adapter.main_client = client_stub  # type: ignore[assignment]
    adapter.main_clients = {cfg.main_model: (client_stub, cfg.main_model)}  # type: ignore[assignment]
    adapter.summarizer = None
    return adapter, stub


def _build_app(adapter) -> web.Application:
    app = web.Application()
    app.router.add_post("/v1/text/chat", adapter.handle_text_chat)
    return app


async def _post_streaming(adapter, body: dict[str, Any]) -> tuple[int, str]:
    from aiohttp.test_utils import TestClient, TestServer

    app = _build_app(adapter)
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    try:
        resp = await client.post("/v1/text/chat", json=body)
        text = await resp.text()
        return resp.status, text
    finally:
        await client.close()


def _parse_ndjson(text: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


@pytest.mark.asyncio
async def test_streaming_http_decision_first_content_stream():
    """HTTP seam: decision frame first, then content, then done."""
    adapter, stub = _make_adapter(
        scripted_deltas=[["</response>", " Hello", " world!"]]
    )
    status, text = await _post_streaming(
        adapter,
        {
            "model": "joyai-vl-interaction-preview",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
    )
    assert status == 200
    frames = _parse_ndjson(text)
    assert [f["type"] for f in frames] == ["decision", "content", "content", "done"]
    assert frames[0]["decision"] == "response"
    content = "".join(f["token"] for f in frames if f["type"] == "content")
    assert content == " Hello world!"
    assert frames[-1]["type"] == "done"
    assert frames[-1]["full_text"] == " Hello world!"
    assert frames[-1]["decision"] == "response"
    # streaming kwargs were used
    assert stub.calls[0]["stream"] is True


@pytest.mark.asyncio
async def test_streaming_http_silence_skips_content():
    adapter, stub = _make_adapter(scripted_deltas=[["</silence>"]])
    status, text = await _post_streaming(
        adapter,
        {
            "model": "joyai-vl-interaction-preview",
            "messages": [{"role": "user", "content": "..."}],
            "stream": True,
        },
    )
    assert status == 200
    frames = _parse_ndjson(text)
    assert [f["type"] for f in frames] == ["decision", "done"]
    assert frames[0]["decision"] == "silence"


@pytest.mark.asyncio
async def test_streaming_http_delegation_question_in_decision_frame():
    adapter, stub = _make_adapter(
        scripted_deltas=[["Looking that up.", "</delegation> 查 RTX 5060 Ti 价格"]]
    )
    status, text = await _post_streaming(
        adapter,
        {
            "model": "joyai-vl-interaction-preview",
            "messages": [{"role": "user", "content": "帮我查下"}],
            "stream": True,
        },
    )
    assert status == 200
    frames = _parse_ndjson(text)
    assert [f["type"] for f in frames] == ["decision", "done"]
    assert frames[0]["decision"] == "delegation"
    assert frames[0]["delegation_question"] == "查 RTX 5060 Ti 价格"
    # Nothing spoken for delegation: no content frames, empty full_text.
    assert frames[-1]["full_text"] == ""


@pytest.mark.asyncio
async def test_streaming_http_error_frame_on_model_failure():
    """A mid-stream failure yields an error frame (caller can fail open)."""

    class _BoomStream(_AsyncStream):
        async def __anext__(self):
            raise RuntimeError("backend exploded")

    class _BoomCompletions(_StubCompletions):
        async def create(self, **kwargs: Any) -> Any:
            self.calls.append(kwargs)
            return _BoomStream(["</response>"])

    adapter = _make_adapter()[0]
    adapter.main_client = _StubAsyncOpenAI(_BoomCompletions())  # type: ignore[assignment]
    status, text = await _post_streaming(
        adapter,
        {
            "model": "joyai-vl-interaction-preview",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
    )
    assert status == 200
    frames = _parse_ndjson(text)
    assert frames[-1]["type"] == "error"
    assert frames[-1]["error_type"] == "RuntimeError"


@pytest.mark.asyncio
async def test_non_streaming_path_is_untouched():
    """Without ``stream`` the non-streaming call path is used unchanged."""
    adapter, stub = _make_adapter(scripted_deltas=[["</response> ok"]])
    from aiohttp.test_utils import TestClient, TestServer

    app = _build_app(adapter)
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    try:
        resp = await client.post(
            "/v1/text/chat",
            json={
                "model": "joyai-vl-interaction-preview",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
        payload = json.loads(await resp.text())
    finally:
        await client.close()
    assert resp.status == 200
    assert payload["choices"][0]["message"]["content"] == "ok"
    assert payload["streamingharness"]["decision"] == "response"
    assert "stream" not in stub.calls[0]
    assert "stream_options" not in stub.calls[0]
