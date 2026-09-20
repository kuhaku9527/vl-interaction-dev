"""Regression tests for audit P1-1: live visual path silently drops user text.

The text paths extracted ``last_user_text`` only from ``str`` content, while
``compose_live_visual_messages`` drops the caller's trailing user message and
rebuilds the final visual user message from ``last_user_text`` + frames. A
caller sending the question as OpenAI list content (``content:
[{"type": "text", "text": ...}]``) therefore had the question vanish from the
request entirely (the model received only images).

Fix: ``request_parsing._extract_last_user_text`` handles str + list content
and is used by every rebuild site; ``compose_live_visual_messages`` only drops
the trailing user turn when its text is actually carried by ``last_user_text``.

Run: python -m pytest tests/test_p1_1_live_visual_list_content.py -q
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
from aiohttp.test_utils import make_mocked_request

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from prompt_assembly import compose_live_visual_messages  # noqa: E402
from request_parsing import _extract_last_user_text  # noqa: E402

# ---------------------------------------------------------------------------
# _extract_last_user_text — str / list / empty
# ---------------------------------------------------------------------------


def test_extract_last_user_text_plain_str():
    messages = [
        {"role": "user", "content": "earlier"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "  what is on screen?  "},
    ]
    assert _extract_last_user_text(messages) == "what is on screen?"


def test_extract_last_user_text_list_content():
    messages = [
        {"role": "user", "content": "earlier"},
        {"role": "user", "content": [{"type": "text", "text": "  check the map  "}]},
    ]
    assert _extract_last_user_text(messages) == "check the map"


def test_extract_last_user_text_list_with_multiple_text_parts():
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "part one"},
                {"type": "text", "text": "part two"},
            ],
        }
    ]
    assert _extract_last_user_text(messages) == "part one\npart two"


def test_extract_last_user_text_list_ignores_non_text_parts():
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAA"}},
                {"type": "text", "text": "the question"},
            ],
        }
    ]
    assert _extract_last_user_text(messages) == "the question"


def test_extract_last_user_text_empty_when_no_user():
    assert _extract_last_user_text([]) == ""
    assert _extract_last_user_text([{"role": "system", "content": "sys"}]) == ""
    assert _extract_last_user_text([{"role": "user", "content": []}]) == ""


# ---------------------------------------------------------------------------
# compose_live_visual_messages — trailing user dropped only when carried
# ---------------------------------------------------------------------------


def _frame(b64: str = "AAAA") -> dict[str, Any]:
    return {"image_b64": b64, "ts_ms": 1.0}


def test_compose_keeps_trailing_user_when_text_not_carried():
    """If the caller passes last_user_text that does NOT match the trailing
    user's extractable text, the trailing user must be kept (no silent loss)."""
    caller = [
        {"role": "user", "content": [{"type": "text", "text": "question via list"}]},
    ]
    got = compose_live_visual_messages(
        composed_system="sys",
        last_user_text="",  # old str-only extraction would produce this
        frames=[_frame()],
        caller_messages=caller,
    )
    # The list-content trailing user survives in history AND the visual user
    # message carries the frames -> nothing is dropped.
    history_roles = [m["role"] for m in got[1:-1]]
    assert history_roles == ["user"]
    assert got[-1]["content"][0]["type"] == "image_url"
    assert got[-1]["content"][0]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_compose_drops_trailing_user_when_text_carried():
    """Normal live visual round: trailing user text rides on the visual user
    message, so it is dropped from history (unchanged pre-fix behaviour)."""
    caller = [
        {"role": "user", "content": "earlier"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "what is on screen?"},
    ]
    got = compose_live_visual_messages(
        composed_system="sys",
        last_user_text="what is on screen?",
        frames=[_frame()],
        caller_messages=caller,
    )
    assert [m["role"] for m in got[1:-1]] == ["user", "assistant"]
    text_parts = [p for p in got[-1]["content"] if p.get("type") == "text"]
    assert text_parts == [{"type": "text", "text": "what is on screen?"}]


# ---------------------------------------------------------------------------
# HTTP-level: /v1/text/chat + frames + list-content question -> text reaches model
# ---------------------------------------------------------------------------


def _b64(payload: bytes = b"\xff\xd8\xff\xe0 fake-jpeg") -> str:
    return base64.b64encode(payload).decode("ascii")


def _frame_dict(index: int = 0) -> dict[str, Any]:
    return {"image_b64": _b64(b"frame-" + str(index).encode()), "ts_ms": 1000.0}


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
    adapter.main_client = client_stub  # type: ignore[assignment]
    adapter.main_clients = {cfg.main_model: (client_stub, cfg.main_model)}  # type: ignore[assignment]
    adapter.summarizer = None
    return adapter, stub


class _StreamProtocol:
    def resume_reading(self, *args, **kwargs):
        pass

    def pause_reading(self, *args, **kwargs):
        pass


def _post_json(adapter: Any, body: dict[str, Any], session_id: str) -> Any:
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
    request = make_mocked_request("POST", "/v1/text/chat", headers=headers, payload=stream)

    async def _run():
        return await adapter.handle_text_chat(request)

    return _run()


@pytest.mark.asyncio
async def test_live_visual_list_content_text_reaches_model():
    """List-content question + frames: the user text must reach the model."""
    adapter, stub = _make_adapter(scripted=["</response> 屏幕上是一张地图。"])
    body = {
        "model": "joyai-vl-interaction-preview",
        "messages": [{"role": "user", "content": [{"type": "text", "text": "屏幕上是什么?"}]}],
        "interaction_mode": "live",
        "frames": [_frame_dict(0), _frame_dict(1)],
    }
    resp = await _post_json(adapter, body, session_id="p1-1-list")
    assert resp.status == 200
    payload = json.loads(resp.text)
    assert payload["choices"][0]["message"]["content"] == "屏幕上是一张地图。"

    messages = stub.calls[0]["messages"]
    user = messages[-1]
    assert isinstance(user["content"], list)
    text_parts = [p for p in user["content"] if p.get("type") == "text"]
    assert text_parts == [{"type": "text", "text": "屏幕上是什么?"}]
    image_parts = [p for p in user["content"] if p.get("type") == "image_url"]
    assert len(image_parts) == 2


@pytest.mark.asyncio
async def test_live_visual_plain_str_no_regression():
    """Plain str content keeps working byte-for-byte (no regression)."""
    adapter, stub = _make_adapter(scripted=["</response> ok"])
    body = {
        "model": "joyai-vl-interaction-preview",
        "messages": [{"role": "user", "content": "what is on screen?"}],
        "interaction_mode": "live",
        "frames": [_frame_dict(0)],
    }
    resp = await _post_json(adapter, body, session_id="p1-1-str")
    assert resp.status == 200
    messages = stub.calls[0]["messages"]
    user = messages[-1]
    text_parts = [p for p in user["content"] if p.get("type") == "text"]
    assert text_parts == [{"type": "text", "text": "what is on screen?"}]
