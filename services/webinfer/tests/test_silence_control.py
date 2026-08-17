# ruff: noqa: RUF001
"""Tests for the radio-silence (无线电静默) control on the webinfer side.

Covers spec ``doc/specs/draft-radio-silence.md`` §2/§3/§4/§6/§7:

  ① suppressed suppression — a live round with a query is fully muted (no
     inference, no TTS) while frames keep flowing;
  ② command phrases — "无线电静默"/"静默" enter, "退出静默" exits, names wake;
  ③ name wake marker — role name arms the one-shot addressee directive and
     the model request carries it;
  ④ T1/T2 timeouts — hint fires once, auto-wake exits silence;
  ⑤ three-mode regression — call / jarvis are untouched by the silence gate.

Run: python -m pytest tests/test_silence_control.py -o asyncio_mode=auto -q
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

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from silence_control import (  # noqa: E402
    DEFAULT_SILENCE_SETTINGS,
    RADIO_SILENCE_WAKE_DIRECTIVE,
    SilenceControlMixin,
)

# ---------------------------------------------------------------------------
# Unit-level harness: mixin state without the full adapter
# ---------------------------------------------------------------------------


class _SilenceHarness(SilenceControlMixin):
    """Minimal harness exposing just the silence mixin (no HTTP / model)."""


def _make_harness() -> _SilenceHarness:
    harness = _SilenceHarness()
    harness._init_silence_control()
    return harness


# ---------------------------------------------------------------------------
# ① suppressed suppression gate
# ---------------------------------------------------------------------------


def test_is_suppressed_live_only():
    """Silence is a LIVE sub-state: call/jarvis never consult it (D-001)."""
    harness = _make_harness()
    assert harness._is_suppressed("live") is False
    harness._silence_enter(reason="test")
    assert harness._is_suppressed("live") is True
    for mode in ("call", "jarvis"):
        assert harness._is_suppressed(mode) is False


def test_enter_exit_transitions_log_and_snapshot():
    harness = _make_harness()
    assert harness._silence_enter(reason="test") is True
    snapshot = harness._silence_snapshot()
    assert snapshot["suppressed"] is True
    assert snapshot["settings"] == DEFAULT_SILENCE_SETTINGS
    assert snapshot["hint_pending"] is True  # T1 on, not fired yet
    assert snapshot["wake_pending"] is False
    # Re-enter is a no-op (idempotent)
    assert harness._silence_enter(reason="test") is False
    assert harness._silence_exit(reason="test") is True
    assert harness._silence_exit(reason="test") is False  # already out
    assert harness._silence_snapshot()["suppressed"] is False


def test_manual_toggle_via_suppressed_flag():
    harness = _make_harness()
    assert harness._silence_suppressed is False
    harness._silence_enter(reason="manual")
    assert harness._is_suppressed("live") is True
    harness._silence_exit(reason="manual")
    assert harness._is_suppressed("live") is False


# ---------------------------------------------------------------------------
# ② command phrase detection
# ---------------------------------------------------------------------------


def test_enter_phrase_radio_silence():
    harness = _make_harness()
    harness._silence_process_transcript("进入无线电静默", "live")
    assert harness._is_suppressed("live") is True


def test_enter_phrase_short_silence():
    harness = _make_harness()
    harness._silence_process_transcript("BT 保持静默", "live")
    assert harness._is_suppressed("live") is True


def test_exit_phrase_wins_over_enter_substring():
    """'退出静默' contains '静默' but must EXIT, never re-enter (ASR on)."""
    harness = _make_harness()
    harness._silence_enter(reason="test")
    harness._silence_update_settings({"asr_enabled": True})
    harness._silence_process_transcript("退出静默", "live")
    assert harness._is_suppressed("live") is False
    assert harness._silence_snapshot()["wake_pending"] is True


def test_enter_phrase_while_suppressed_is_noop():
    harness = _make_harness()
    harness._silence_enter(reason="test")
    harness._silence_process_transcript("继续无线电静默", "live")
    assert harness._is_suppressed("live") is True


def test_asr_off_disables_text_detection_while_suppressed():
    """ASR switch off in silence => no text channel (KWS/combo only, §4 关/关)."""
    harness = _make_harness()
    harness._silence_enter(reason="test")
    harness._silence_update_settings({"asr_enabled": False})
    # Even a name cannot wake through the text layer when ASR is off.
    harness._silence_process_transcript("BT 过来一下", "live")
    assert harness._is_suppressed("live") is True
    assert harness._silence_snapshot()["wake_pending"] is False


def test_asr_on_allows_name_wake_while_suppressed():
    harness = _make_harness()
    harness._silence_enter(reason="test")
    harness._silence_update_settings({"asr_enabled": True})
    harness._silence_process_transcript("BT，过来", "live")
    assert harness._is_suppressed("live") is False
    assert harness._silence_snapshot()["wake_pending"] is True


def test_transcript_ignored_for_non_live_modes():
    """call/jarvis transcripts never touch the silence state (D-001)."""
    harness = _make_harness()
    harness._silence_process_transcript("无线电静默", "call")
    assert harness._is_suppressed("live") is False
    harness._silence_process_transcript("无线电静默", "jarvis")
    assert harness._is_suppressed("live") is False


def test_empty_transcript_noop():
    harness = _make_harness()
    harness._silence_process_transcript("", "live")
    harness._silence_process_transcript(None, "live")
    assert harness._is_suppressed("live") is False


# ---------------------------------------------------------------------------
# ③ name wake marker (addressee bias)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "utterance",
    ["bt", "BT", "b t", "b.t", "铁驭", "贾维斯", "BT 现在几点了", "喂，铁驭"],
)
def test_name_wake_marks_and_arms_directive(utterance: str):
    harness = _make_harness()
    harness._silence_enter(reason="test")
    harness._silence_update_settings({"asr_enabled": True})
    harness._silence_process_transcript(utterance, "live")
    assert harness._is_suppressed("live") is False
    assert harness._silence_snapshot()["wake_pending"] is True
    directive = harness._consume_wake_directive("live")
    assert directive == RADIO_SILENCE_WAKE_DIRECTIVE
    # One-shot: second consume returns None.
    assert harness._consume_wake_directive("live") is None


def test_name_while_not_suppressed_does_not_arm_directive():
    """Normal live 被点名 is NOT a silence wake (same engine, different state)."""
    harness = _make_harness()
    harness._silence_process_transcript("BT，现在几点", "live")
    assert harness._is_suppressed("live") is False
    assert harness._silence_snapshot()["wake_pending"] is False


def test_wake_directive_not_consumed_for_non_live():
    harness = _make_harness()
    harness._silence_enter(reason="test")
    harness._silence_update_settings({"asr_enabled": True})
    harness._silence_process_transcript("BT", "live")
    assert harness._consume_wake_directive("call") is None
    assert harness._consume_wake_directive("jarvis") is None
    # Still armed for the live round.
    assert harness._consume_wake_directive("live") == RADIO_SILENCE_WAKE_DIRECTIVE


# ---------------------------------------------------------------------------
# ④ T1 / T2 timers
# ---------------------------------------------------------------------------


def test_t1_hint_fires_once_and_bumps_count():
    harness = _make_harness()
    harness._silence_enter(reason="test")
    # Default T1 = 15 min = 900s. Enter at t0, check at t0+900.
    t0 = harness._silence_entered_at
    assert harness._silence_check_timeouts(now=t0 + 899) is None
    assert harness._silence_check_timeouts(now=t0 + 900) == "hint"
    assert harness._silence_snapshot()["hint_count"] == 1
    assert harness._silence_snapshot()["hint_pending"] is False
    # One-shot: a later check never hints again.
    assert harness._silence_check_timeouts(now=t0 + 5000) is None
    assert harness._silence_snapshot()["hint_count"] == 1
    # Still suppressed (hint does not wake).
    assert harness._is_suppressed("live") is True


def test_t1_disabled_never_hints():
    harness = _make_harness()
    harness._silence_enter(reason="test")
    harness._silence_update_settings({"timeout_hint_enabled": False})
    t0 = harness._silence_entered_at
    assert harness._silence_check_timeouts(now=t0 + 3600) is None
    assert harness._silence_snapshot()["hint_count"] == 0


def test_t2_auto_wake_exits_silence():
    harness = _make_harness()
    harness._silence_enter(reason="test")
    harness._silence_update_settings({"auto_wake_enabled": True, "auto_wake_minutes": 1})
    t0 = harness._silence_entered_at
    assert harness._silence_check_timeouts(now=t0 + 59) is None
    assert harness._silence_check_timeouts(now=t0 + 60) == "auto_wake"
    assert harness._is_suppressed("live") is False
    # Hint never fired on the way out (60s < 900s T1).
    assert harness._silence_snapshot()["hint_count"] == 0


def test_t2_disabled_never_auto_wakes():
    harness = _make_harness()
    harness._silence_enter(reason="test")
    # T1 (hint) legitimately fires at 900s, but T2 is off => never auto-wakes.
    result = harness._silence_check_timeouts(now=harness._silence_entered_at + 7200)
    assert result != "auto_wake"
    assert harness._is_suppressed("live") is True


def test_t1_and_t2_independent_settings():
    """T1 on + T2 on: hint fires first (shorter), auto-wake later."""
    harness = _make_harness()
    harness._silence_enter(reason="test")
    harness._silence_update_settings(
        {"timeout_hint_minutes": 1, "auto_wake_enabled": True, "auto_wake_minutes": 2}
    )
    t0 = harness._silence_entered_at
    assert harness._silence_check_timeouts(now=t0 + 60) == "hint"
    assert harness._is_suppressed("live") is True
    assert harness._silence_check_timeouts(now=t0 + 120) == "auto_wake"
    assert harness._is_suppressed("live") is False


def test_check_timeouts_when_not_suppressed_returns_none():
    harness = _make_harness()
    assert harness._silence_check_timeouts() is None


# ---------------------------------------------------------------------------
# KWS event channel (webui pushes the event source in)
# ---------------------------------------------------------------------------


def test_kws_event_wakes_when_kws_enabled():
    harness = _make_harness()
    harness._silence_enter(reason="test")
    assert harness._silence_process_kws_event() is True
    assert harness._is_suppressed("live") is False
    assert harness._silence_snapshot()["wake_pending"] is True


def test_kws_event_ignored_when_kws_disabled():
    harness = _make_harness()
    harness._silence_enter(reason="test")
    harness._silence_update_settings({"kws_enabled": False})
    assert harness._silence_process_kws_event() is False
    assert harness._is_suppressed("live") is True


def test_kws_event_ignored_when_not_suppressed():
    harness = _make_harness()
    assert harness._silence_process_kws_event() is False


# ---------------------------------------------------------------------------
# Settings update validation
# ---------------------------------------------------------------------------


def test_settings_update_known_keys_and_minutes_clamp():
    harness = _make_harness()
    harness._silence_update_settings(
        {
            "asr_enabled": True,
            "kws_enabled": False,
            "timeout_hint_enabled": False,
            "timeout_hint_minutes": "30",
            "auto_wake_enabled": True,
            "auto_wake_minutes": -5,
        }
    )
    settings = harness._silence_snapshot()["settings"]
    assert settings["asr_enabled"] is True
    assert settings["kws_enabled"] is False
    assert settings["timeout_hint_enabled"] is False
    assert settings["timeout_hint_minutes"] == 30
    assert settings["auto_wake_enabled"] is True
    assert settings["auto_wake_minutes"] == 0  # negative clamped to 0


def test_settings_unknown_key_logged_and_ignored():
    harness = _make_harness()
    harness._silence_update_settings({"not_a_setting": 1})
    assert "not_a_setting" not in harness._silence_snapshot()["settings"]


def test_settings_invalid_minutes_ignored():
    harness = _make_harness()
    harness._silence_update_settings({"timeout_hint_minutes": "abc"})
    assert harness._silence_snapshot()["settings"]["timeout_hint_minutes"] == 15


# ---------------------------------------------------------------------------
# HTTP-level: GET/POST /v1/live/silence (summarizer-route pattern)
# ---------------------------------------------------------------------------


def _make_http_adapter():
    """Full StreamingInferAdapter with silence control initialised (no model)."""
    from live_adapter import AdapterConfig, StreamingInferAdapter
    from memory_store_client import MemoryStoreClient

    cfg = AdapterConfig()
    cfg.enable_summarizer = False
    cfg.character_prompts_enabled = False
    cfg.memory_store_enabled = False

    adapter = StreamingInferAdapter.__new__(StreamingInferAdapter)
    adapter.config = cfg
    adapter.sessions = {}
    adapter._cleanup_task = None
    adapter._character_prompt_mtime = 0.0
    adapter._system_prompt_cache = {}
    adapter._invalidate_system_prompt_cache = lambda: None
    adapter.memory_store = MemoryStoreClient(base_url="http://127.0.0.1:8997", enabled=False)
    adapter.summarizer = None
    adapter._init_silence_control()
    return adapter


async def _get_silence(adapter) -> dict[str, Any]:
    from aiohttp.test_utils import make_mocked_request

    request = make_mocked_request("GET", "/v1/live/silence")
    resp = await adapter.handle_live_silence(request)
    return json.loads(resp.text)


class _StreamProtocol:
    """Minimal aiohttp StreamReader protocol stub (mirrors existing tests)."""

    def resume_reading(self, *args, **kwargs) -> None:
        pass

    def pause_reading(self, *args, **kwargs) -> None:
        pass


def _mocked_json_post(path: str, raw: bytes):
    """Build a mocked POST request whose body is the given raw bytes."""
    from aiohttp import streams
    from aiohttp.test_utils import make_mocked_request

    loop = asyncio.new_event_loop()
    stream = streams.StreamReader(protocol=_StreamProtocol(), limit=2**16, loop=loop)
    stream.feed_data(raw)
    stream.feed_eof()
    return make_mocked_request(
        "POST",
        path,
        headers={"Content-Type": "application/json"},
        payload=stream,
    )


async def _post_silence(adapter, body: dict[str, Any]) -> dict[str, Any]:
    request = _mocked_json_post("/v1/live/silence", json.dumps(body).encode("utf-8"))
    resp = await adapter.handle_live_silence(request)
    return json.loads(resp.text)


@pytest.mark.asyncio
async def test_http_get_returns_default_snapshot():
    adapter = _make_http_adapter()
    snapshot = await _get_silence(adapter)
    assert snapshot["suppressed"] is False
    assert snapshot["settings"] == DEFAULT_SILENCE_SETTINGS


@pytest.mark.asyncio
async def test_http_post_manual_toggle_and_settings():
    adapter = _make_http_adapter()
    snapshot = await _post_silence(
        adapter,
        {
            "suppressed": True,
            "settings": {"asr_enabled": True, "kws_enabled": False, "auto_wake_enabled": True},
        },
    )
    assert snapshot["suppressed"] is True
    assert snapshot["settings"]["asr_enabled"] is True
    assert snapshot["settings"]["kws_enabled"] is False
    assert snapshot["settings"]["auto_wake_enabled"] is True

    snapshot = await _post_silence(adapter, {"suppressed": False})
    assert snapshot["suppressed"] is False


@pytest.mark.asyncio
async def test_http_post_kws_event_wakes():
    adapter = _make_http_adapter()
    await _post_silence(adapter, {"suppressed": True})
    assert adapter._is_suppressed("live") is True
    snapshot = await _post_silence(adapter, {"kws_event": True})
    assert snapshot["suppressed"] is False
    assert snapshot["wake_pending"] is True


@pytest.mark.asyncio
async def test_http_post_kws_event_respects_kws_switch():
    adapter = _make_http_adapter()
    await _post_silence(adapter, {"suppressed": True, "settings": {"kws_enabled": False}})
    snapshot = await _post_silence(adapter, {"kws_event": True})
    assert snapshot["suppressed"] is True


@pytest.mark.asyncio
async def test_http_post_invalid_json_400():
    adapter = _make_http_adapter()
    request = _mocked_json_post("/v1/live/silence", b"{not json")
    resp = await adapter.handle_live_silence(request)
    assert resp.status == 400


@pytest.mark.asyncio
async def test_http_post_settings_rejects_non_bool():
    """约法三章②: bool("false")==True would wrongly enable a switch — reject."""
    adapter = _make_http_adapter()
    request = _mocked_json_post(
        "/v1/live/silence", json.dumps({"settings": {"asr_enabled": "false"}}).encode("utf-8")
    )
    resp = await adapter.handle_live_silence(request)
    assert resp.status == 400
    assert "asr_enabled" in resp.text
    # Nothing was applied (no partial silent coercion).
    assert adapter._silence_settings["asr_enabled"] is False


@pytest.mark.asyncio
async def test_http_post_settings_rejects_bool_as_minutes():
    """True must not become a minutes value (bool is an int subclass)."""
    adapter = _make_http_adapter()
    request = _mocked_json_post(
        "/v1/live/silence",
        json.dumps({"settings": {"timeout_hint_minutes": True}}).encode("utf-8"),
    )
    resp = await adapter.handle_live_silence(request)
    assert resp.status == 400
    assert adapter._silence_settings["timeout_hint_minutes"] == 15


@pytest.mark.asyncio
async def test_http_post_suppressed_rejects_non_bool():
    """Same trap as settings: bool("false")==True must never enter silence."""
    adapter = _make_http_adapter()
    request = _mocked_json_post(
        "/v1/live/silence", json.dumps({"suppressed": "false"}).encode("utf-8")
    )
    resp = await adapter.handle_live_silence(request)
    assert resp.status == 400
    assert "suppressed" in resp.text
    # Nothing was applied.
    assert adapter._silence_suppressed is False


def test_settings_update_rejects_non_bool_direct():
    harness = _make_harness()
    err = harness._silence_update_settings({"kws_enabled": "false"})
    assert err is not None
    assert "must be a boolean" in err
    # Rejected value is never applied.
    assert harness._silence_snapshot()["settings"]["kws_enabled"] is True


# ---------------------------------------------------------------------------
# ⑤ end-to-end suppression on the live round paths (multimodal + text)
# ---------------------------------------------------------------------------


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
    """Records calls; always returns the scripted content when invoked."""

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


def _make_adapter(scripted: list[str] | None = None) -> tuple[Any, _StubCompletions]:
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
    adapter.summarizer = None
    adapter._init_silence_control()

    stub = _StubCompletions(scripted=scripted)
    client_stub = _StubAsyncOpenAI(stub)
    adapter.main_client = client_stub  # type: ignore[assignment]
    adapter.main_clients = {cfg.main_model: (client_stub, cfg.main_model)}  # type: ignore[assignment]
    return adapter, stub


def _b64(payload: bytes = b"\xff\xd8\xff\xe0 fake-jpeg") -> str:
    return base64.b64encode(payload).decode("ascii")


def _image_body(text: str = "describe this frame") -> dict[str, Any]:
    return {
        "model": "joyai-vl-interaction-preview",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": text},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{_b64()}"},
                    },
                ],
            }
        ],
        "interaction_mode": "live",
    }


async def _post_chat_completions(adapter: Any, body: dict[str, Any], session_id: str) -> Any:
    from aiohttp import streams
    from aiohttp.test_utils import make_mocked_request

    raw = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json", "x-streaming-session": session_id}
    loop = asyncio.new_event_loop()
    stream = streams.StreamReader(protocol=_StreamProtocol(), limit=2**16, loop=loop)
    stream.feed_data(raw)
    stream.feed_eof()
    request = make_mocked_request("POST", "/v1/chat/completions", headers=headers, payload=stream)

    async def _run():
        return await adapter.handle_chat_completions(request)

    return await _run()


async def _post_text_chat(adapter: Any, body: dict[str, Any], session_id: str) -> Any:
    from aiohttp import streams
    from aiohttp.test_utils import make_mocked_request

    raw = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json", "x-streaming-session": session_id}
    loop = asyncio.new_event_loop()
    stream = streams.StreamReader(protocol=_StreamProtocol(), limit=2**16, loop=loop)
    stream.feed_data(raw)
    stream.feed_eof()
    request = make_mocked_request("POST", "/v1/text/chat", headers=headers, payload=stream)

    async def _run():
        return await adapter.handle_text_chat(request)

    return await _run()


@pytest.mark.asyncio
async def test_live_round_with_query_is_suppressed():
    """① Suppressed + query => no model call, silence decision, frames flow."""
    adapter, stub = _make_adapter(scripted=["</response> 不该出现"])
    adapter._silence_enter(reason="test")
    resp = await _post_chat_completions(
        adapter, _image_body(text="有 query 也不响应"), session_id="sil-1"
    )
    assert resp.status == 200
    payload = json.loads(resp.text)
    assert payload["streamingharness"]["decision"] == "silence"
    assert payload["choices"][0]["message"]["content"] == ""
    assert payload["streamingharness"]["silence"] == {
        "suppressed": True,
        "reason": "radio_silence",
    }
    # No inference happened.
    assert len(stub.calls) == 0
    # Vision still advanced: the round was appended as a turn.
    state = adapter.sessions["sil-1"]
    assert state.turn_count == 1


@pytest.mark.asyncio
async def test_enter_command_suppresses_its_own_round():
    """Speaking 无线电静默 enters silence; the entering round is itself muted."""
    adapter, stub = _make_adapter(scripted=["</response> 不该出现"])
    resp = await _post_chat_completions(
        adapter, _image_body(text="进入无线电静默"), session_id="sil-2"
    )
    payload = json.loads(resp.text)
    assert payload["streamingharness"]["decision"] == "silence"
    assert adapter._is_suppressed("live") is True
    assert len(stub.calls) == 0


@pytest.mark.asyncio
async def test_name_wake_round_runs_with_directive():
    """③ Name in silence => wake; the round runs and the directive is injected."""
    adapter, stub = _make_adapter(scripted=["</response> 我在，铁驭。"])
    adapter._silence_enter(reason="test")
    adapter._silence_update_settings({"asr_enabled": True})
    resp = await _post_chat_completions(
        adapter, _image_body(text="BT，过来一下"), session_id="sil-3"
    )
    assert resp.status == 200
    payload = json.loads(resp.text)
    assert payload["streamingharness"]["decision"] == "response"
    assert payload["streamingharness"]["silence"] == {"wake": True}
    assert payload["choices"][0]["message"]["content"] == "我在，铁驭。"
    # The model WAS called (not suppressed) and the directive rode the prompt.
    assert len(stub.calls) == 1
    http_messages = stub.calls[0]["messages"]
    system_contents = [m["content"] for m in http_messages if m.get("role") == "system"]
    assert any(RADIO_SILENCE_WAKE_DIRECTIVE in content for content in system_contents)
    # One-shot: next round has no directive.
    assert adapter._consume_wake_directive("live") is None


@pytest.mark.asyncio
async def test_call_mode_never_suppressed():
    """⑤ Three-mode regression: call mode ignores the silence gate."""
    adapter, stub = _make_adapter(scripted=["</response> 呼叫模式照常"])
    adapter._silence_enter(reason="test")
    body = {
        "model": "joyai-vl-interaction-preview",
        "messages": [{"role": "user", "content": "call me"}],
        "interaction_mode": "call",
    }
    resp = await _post_text_chat(adapter, body, session_id="sil-call")
    assert resp.status == 200
    payload = json.loads(resp.text)
    assert payload["streamingharness"]["decision"] == "response"
    assert payload["choices"][0]["message"]["content"] == "呼叫模式照常"
    assert len(stub.calls) == 1
    assert "silence" not in payload["streamingharness"]


@pytest.mark.asyncio
async def test_jarvis_mode_never_suppressed():
    """⑤ Three-mode regression: jarvis mode ignores the silence gate."""
    adapter, stub = _make_adapter(scripted=["</response> 贾维斯模式照常"])
    adapter._silence_enter(reason="test")
    body = {
        "model": "joyai-vl-interaction-preview",
        "messages": [{"role": "user", "content": "jarvis me"}],
        "interaction_mode": "jarvis",
    }
    resp = await _post_text_chat(adapter, body, session_id="sil-jarvis")
    assert resp.status == 200
    payload = json.loads(resp.text)
    assert payload["streamingharness"]["decision"] == "response"
    assert len(stub.calls) == 1


@pytest.mark.asyncio
async def test_text_path_suppressed_with_query():
    """① Production live text path (/v1/text/chat) mutes too."""
    adapter, stub = _make_adapter(scripted=["</response> 不该出现"])
    adapter._silence_enter(reason="test")
    body = {
        "model": "joyai-vl-interaction-preview",
        "messages": [{"role": "user", "content": "有 query 也不响应"}],
        "interaction_mode": "live",
    }
    resp = await _post_text_chat(adapter, body, session_id="sil-text")
    assert resp.status == 200
    payload = json.loads(resp.text)
    assert payload["streamingharness"]["decision"] == "silence"
    assert payload["choices"][0]["message"]["content"] == ""
    assert payload["streamingharness"]["silence"] == {
        "suppressed": True,
        "reason": "radio_silence",
    }
    assert len(stub.calls) == 0


@pytest.mark.asyncio
async def test_streaming_text_path_suppressed_emits_silence_frames():
    """① Streaming live round while suppressed => decision(silence) + done only."""
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    adapter, stub = _make_adapter()
    adapter._silence_enter(reason="test")
    app = web.Application()
    app.router.add_post("/v1/text/chat", adapter.handle_text_chat)
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    try:
        resp = await client.post(
            "/v1/text/chat",
            json={
                "model": "joyai-vl-interaction-preview",
                "messages": [{"role": "user", "content": "别说话"}],
                "interaction_mode": "live",
                "stream": True,
            },
        )
        text = await resp.text()
    finally:
        await client.close()
    assert resp.status == 200
    frames = [json.loads(line) for line in text.splitlines() if line.strip()]
    assert [f["type"] for f in frames] == ["decision", "done"]
    assert frames[0]["decision"] == "silence"
    assert frames[-1]["decision"] == "silence"
    assert frames[-1]["full_text"] == ""
    assert frames[-1]["silence"] == {"suppressed": True, "reason": "radio_silence"}
    assert len(stub.calls) == 0
