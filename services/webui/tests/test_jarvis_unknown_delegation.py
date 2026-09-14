"""N2 — call-mode unknown-reply auto-delegation (spec call-mode-unknown-delegation.md).

call mode uses NO_DECISION_SYSTEM_PROMPT which forbids </delegation>, so the
model answers "不知道今天几号 / 无法回答" instead of delegating. This suite
locks the webui-side detection + delegation rewrite:

* looks_like_unknown_reply() positive/negative cases
* call mode + unknown reply + bg available -> decision=delegation, question=user text
* call mode + normal reply -> unchanged
* jarvis mode + unknown reply -> NOT rewritten (jarvis has decision tokens)
* bg unavailable -> fail-open, original reply kept
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

REPO = Path(__file__).resolve().parents[2]
WEBUI_SRC = REPO / "services" / "webui" / "src"
for _p in (str(REPO), str(WEBUI_SRC)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from joy_interaction_webui.jarvis_config import looks_like_unknown_reply  # noqa: E402


# --- looks_like_unknown_reply -------------------------------------------------

def test_unknown_positive_patterns():
    assert looks_like_unknown_reply("我不知道今天几号")
    assert looks_like_unknown_reply("抱歉，我无法回答这个问题")
    assert looks_like_unknown_reply("我不清楚天气情况")
    assert looks_like_unknown_reply("这个问题超出了我的能力范围")
    assert looks_like_unknown_reply("查不到相关信息")
    assert looks_like_unknown_reply("我不确定，没法回答")


def test_unknown_negative_patterns():
    assert not looks_like_unknown_reply("我知道答案")
    assert not looks_like_unknown_reply("今天天气不错")
    assert not looks_like_unknown_reply("")
    assert not looks_like_unknown_reply("   ")
    assert not looks_like_unknown_reply(None)


# --- _send_to_llm_non_streaming rewrite ---------------------------------------

def _make_sm(bg):
    """Build a minimal JarvisStateMachine-shaped object with _background_service."""
    cfg = SimpleNamespace(
        llm_system_prompt="be brief",
        llm_api_url="http://127.0.0.1:8070/v1",
        llm_text_path="/text/chat",
        llm_multimodal_path="/chat/completions",
        llm_model="stub",
        llm_streaming_enabled=False,
        tts_api_url="http://127.0.0.1:8985",
        tts_voice_id="v1",
    )
    sm = SimpleNamespace(
        config=cfg,
        _background_service=bg,
        _llm_reply_epoch=0,
        _tts_sentence_epoch=0,
        _conv_history=[],
        _max_history_turns=4,
        _finish_llm_turn=AsyncMock(),
        _ensure_tts_stream_state=lambda: None,
    )
    return sm


class _FakeResp:
    def __init__(self, response, decision="response", delegation_question=None):
        self._payload = {
            "choices": [{"message": {"content": response}}],
            "streamingharness": {"decision": decision},
        }
        if delegation_question:
            self._payload["streamingharness"]["delegation_question"] = delegation_question

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _run_non_streaming(sm, text, fake_resp, interaction_mode="call"):
    from joy_interaction_webui.jarvis_mode import JarvisStateMachine

    with patch("httpx.AsyncClient") as mc:
        client = mc.return_value.__aenter__.return_value
        client.post.return_value = fake_resp
        asyncio.run(
            JarvisStateMachine._send_to_llm_non_streaming(
                sm, text, stream_tts=False, interaction_mode=interaction_mode
            )
        )
    return sm._finish_llm_turn


def test_call_mode_unknown_rewrites_to_delegation():
    bg = SimpleNamespace(enabled=True, _closed=False, handle_foreground_response=AsyncMock())
    sm = _make_sm(bg)
    finish = _run_non_streaming(
        sm,
        "今天几号？",
        _FakeResp("我不知道今天几号"),
        interaction_mode="call",
    )
    args = finish.await_args
    assert args is not None
    assert args.kwargs["decision"] == "delegation"
    assert args.kwargs["delegation_question"] == "今天几号？"
    assert "让我查一下" in args.kwargs["response"]


def test_call_mode_normal_reply_unchanged():
    bg = SimpleNamespace(enabled=True, _closed=False, handle_foreground_response=AsyncMock())
    sm = _make_sm(bg)
    finish = _run_non_streaming(
        sm,
        "你好",
        _FakeResp("你好，有什么可以帮你？"),
        interaction_mode="call",
    )
    args = finish.await_args
    assert args.kwargs["decision"] == "response"
    assert args.kwargs["delegation_question"] is None
    assert args.kwargs["response"] == "你好，有什么可以帮你？"


def test_jarvis_mode_unknown_not_rewritten():
    """jarvis mode keeps its decision-token path untouched (N2 scope is call only)."""
    bg = SimpleNamespace(enabled=True, _closed=False, handle_foreground_response=AsyncMock())
    sm = _make_sm(bg)
    finish = _run_non_streaming(
        sm,
        "今天几号？",
        _FakeResp("我不知道今天几号"),
        interaction_mode="jarvis",
    )
    args = finish.await_args
    assert args.kwargs["decision"] == "response"
    assert args.kwargs["delegation_question"] is None
    assert args.kwargs["response"] == "我不知道今天几号"


def test_call_mode_unknown_bg_unavailable_keeps_reply():
    """bg disabled/closed -> fail-open: keep original reply, no rewrite."""
    sm = _make_sm(None)
    finish = _run_non_streaming(
        sm,
        "今天几号？",
        _FakeResp("我不知道今天几号"),
        interaction_mode="call",
    )
    args = finish.await_args
    assert args.kwargs["decision"] == "response"
    assert args.kwargs["delegation_question"] is None
    assert args.kwargs["response"] == "我不知道今天几号"


def test_call_mode_delegation_question_present_no_rewrite():
    """If webinfer already returned a delegation_question, do not double-delegate."""
    bg = SimpleNamespace(enabled=True, _closed=False, handle_foreground_response=AsyncMock())
    sm = _make_sm(bg)
    finish = _run_non_streaming(
        sm,
        "今天几号？",
        _FakeResp("正在查", decision="delegation", delegation_question="今天几号？"),
        interaction_mode="call",
    )
    args = finish.await_args
    assert args.kwargs["decision"] == "delegation"
    assert args.kwargs["delegation_question"] == "今天几号？"
    assert args.kwargs["response"] == "正在查"
