"""Regression tests for audit P1-3: video path mute under
USE_PROMPT_AS_QUERY=0 + force_silence_before_query=1.

``update_query_state`` never sets ``state.current_query_text`` when
``use_prompt_as_query=False``, so ``is_forced_silence`` (live + force + no
query) returned True for EVERY ``/v1/chat/completions`` turn and the adapter
produced ``</silence>`` without ever calling the main model — a silent video
path.

Fix: a live round that carries visual context (frames/images) is exempt from
forced silence — with a camera/screen observation in hand the model must get
to decide (the four-state prompt still lets it emit ``</silence>`` itself).

Run: python -m pytest tests/test_p1_3_forced_silence_frames.py -q
"""

from __future__ import annotations

import asyncio
import base64
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aiohttp.test_utils import make_mocked_request

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from infer_loop import InferLoopMixin  # noqa: E402

# ---------------------------------------------------------------------------
# wrapper-level: has_frames exemption
# ---------------------------------------------------------------------------


def _make_wrapper(force: bool = True, use_prompt_as_query: bool = False) -> Any:
    config_defaults = {
        "force_silence_before_query": force,
        "use_prompt_as_query": use_prompt_as_query,
    }
    obj = SimpleNamespace(config=SimpleNamespace(**config_defaults))
    obj._is_forced_silence = InferLoopMixin._is_forced_silence.__get__(obj, InferLoopMixin)
    return obj


def _state(current_query_text: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(current_query_text=current_query_text)


def test_live_frames_exempt_from_forced_silence():
    """live + force + no query + frames -> NOT forced silence (model decides)."""
    adapter = _make_wrapper(force=True)
    state = _state(current_query_text=None)
    assert adapter._is_forced_silence(state, "live", has_frames=True) is False


def test_live_frames_exempt_even_with_use_prompt_as_query_zero():
    """The exact audit trap: query tracking disabled + force on + frames."""
    adapter = _make_wrapper(force=True, use_prompt_as_query=False)
    state = _state(current_query_text=None)
    assert adapter._is_forced_silence(state, "live", has_frames=True) is False


def test_live_no_frames_preserves_original_forced_silence():
    """Without frames, original behaviour is unchanged (still forced silence)."""
    adapter = _make_wrapper(force=True)
    state = _state(current_query_text=None)
    assert adapter._is_forced_silence(state, "live") is True


def test_live_no_frames_with_query_not_silenced():
    adapter = _make_wrapper(force=True)
    state = _state(current_query_text="hi")
    assert adapter._is_forced_silence(state, "live") is False
    assert adapter._is_forced_silence(state, "live", has_frames=True) is False


def test_call_jarvis_never_forced_even_with_frames():
    adapter = _make_wrapper(force=True)
    state = _state(current_query_text=None)
    for mode in ("call", "jarvis"):
        assert adapter._is_forced_silence(state, mode, has_frames=True) is False
        assert adapter._is_forced_silence(state, mode) is False


# ---------------------------------------------------------------------------
# HTTP-level: /v1/chat/completions video path must not mute
# ---------------------------------------------------------------------------


def _b64(payload: bytes = b"\xff\xd8\xff\xe0 fake-jpeg") -> str:
    return base64.b64encode(payload).decode("ascii")


def _image_body() -> dict[str, Any]:
    return {
        "model": "joyai-vl-interaction-preview",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "describe this frame"},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{_b64()}"},
                    },
                ],
            }
        ],
        "interaction_mode": "live",
    }


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
class _StubMessage:
    content: str


@dataclass
class _StubChoice:
    message: _StubMessage


@dataclass
class _StubFullCompletion:
    choices: list[Any]
    usage: Any = None


class _StubCompletions:
    def __init__(self, scripted: list[str] | None = None) -> None:
        self._scripted = list(scripted or [])
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> _StubFullCompletion:
        self.calls.append(kwargs)
        content = self._scripted.pop(0) if self._scripted else "</silence>"
        return _StubFullCompletion(
            choices=[_StubChoice(message=_StubMessage(content=content))],
            usage=_StubUsage(),
        )


class _StubChatNamespace:
    def __init__(self, completions: _StubCompletions) -> None:
        self.completions = completions


class _StubAsyncOpenAI:
    def __init__(self, completions: _StubCompletions) -> None:
        self.chat = _StubChatNamespace(completions)


def _make_adapter(scripted: list[str] | None = None, **cfg_overrides):
    from live_adapter import AdapterConfig, StreamingInferAdapter
    from memory_store_client import MemoryStoreClient

    cfg = AdapterConfig()
    cfg.enable_summarizer = False
    cfg.character_prompts_enabled = False
    cfg.memory_store_enabled = False
    cfg.main_ctx_tokens = 16384
    for key, value in cfg_overrides.items():
        setattr(cfg, key, value)

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
    def resume_reading(self, *args, **kwargs):
        pass

    def pause_reading(self, *args, **kwargs):
        pass


def _post_chat_completions(adapter: Any, body: dict[str, Any], session_id: str) -> Any:
    from aiohttp import streams

    raw = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json", "x-streaming-session": session_id}
    loop = asyncio.new_event_loop()
    stream = streams.StreamReader(
        protocol=_StreamProtocol(),
        limit=2**16,
        loop=loop,
    )
    stream.feed_data(raw)
    stream.feed_eof()
    request = make_mocked_request("POST", "/v1/chat/completions", headers=headers, payload=stream)

    async def _run():
        return await adapter.handle_chat_completions(request)

    return _run()


@pytest.mark.asyncio
async def test_video_path_with_frames_not_silenced_when_query_tracking_off():
    """USE_PROMPT_AS_QUERY=0 + force_silence_before_query=1 + image -> the
    main model is called (no more silent video path)."""
    adapter, stub = _make_adapter(
        scripted=["</response> 画面上有一只精英怪。"],
        use_prompt_as_query=False,
        force_silence_before_query=True,
    )

    resp = await _post_chat_completions(adapter, _image_body(), session_id="p1-3-video")
    assert resp.status == 200
    payload = json.loads(resp.text)
    assert payload["streamingharness"]["decision"] == "response"
    assert payload["choices"][0]["message"]["content"] == "画面上有一只精英怪。"
    # The stub was actually called with a multimodal request (not skipped).
    assert len(stub.calls) == 1
    http_messages = stub.calls[0]["messages"]
    image_parts = [
        part
        for message in http_messages
        if isinstance(message.get("content"), list)
        for part in message["content"]
        if part.get("type") == "image_url"
    ]
    assert image_parts, "expected at least one image_url part in the model request"


@pytest.mark.asyncio
async def test_video_path_default_config_still_answers():
    """Default config (query tracking on) keeps working: a text+image turn
    sets the query and is answered normally."""
    adapter, stub = _make_adapter(scripted=["</response> 屏幕上显示的是地图。"])
    resp = await _post_chat_completions(adapter, _image_body(), session_id="p1-3-default")
    assert resp.status == 200
    payload = json.loads(resp.text)
    assert payload["streamingharness"]["decision"] == "response"
    assert len(stub.calls) == 1


@pytest.mark.asyncio
async def test_text_only_chat_completions_still_forwards():
    """No image refs -> _forward_text_only (original behaviour, model called
    directly; never was silenced by the video path's forced-silence branch)."""
    adapter, stub = _make_adapter(scripted=["</response> hi"])
    body = {
        "model": "joyai-vl-interaction-preview",
        "messages": [{"role": "user", "content": "hi"}],
        "interaction_mode": "live",
    }
    resp = await _post_chat_completions(adapter, body, session_id="p1-3-text")
    assert resp.status == 200
    assert len(stub.calls) == 1
