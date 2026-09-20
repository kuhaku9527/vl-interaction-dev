"""P0-A QA boundary tests (independent regression verification).

Complements ``test_text_chat_streaming.py`` (webinfer) and
``test_jarvis_tts_streaming.py`` (jarvis) with adversarial edge cases the
primary suites do not pin:

  * streaming frame protocol with MULTIPLE decision markers — the delegation
    format taught in ``prompt_constants.py`` is ``</response> <note>
    </delegation> <question>``, i.e. ``</response>`` precedes the delegation
    tag. ``parse_model_decision`` gives a delegation tag ANYWHERE priority
    (documented hardening); the streaming frame builder commits at the
    earliest complete marker. These must agree.
  * SentenceBuffer boundaries (max_buffer_chars force flush, abbreviation
    false positives, flush_on_timeout, min_sentence_length merge).
  * fail-open pre-frame retry must NOT double-play (zero tts_sentence).
  * cancellation must bump the sentence epoch and drop in-flight audio.
  * frontend per-sentence queue: out-of-order skip, new-session supersede,
    and ``stopLlmReplyAudio`` preserving the barge-in P0 logic.

NOTE: ``test_qa_stream_delegation_taught_format_matches_parser`` pins the
CORRECT behavior (delegation must not be spoken / must not stream content).
As of commit 32c6db9 it FAILS — that is the point: it documents a real
semantic gap between the streaming frame protocol and the unified parser for
the delegation format the system prompt actually teaches.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

REPO = Path(__file__).resolve().parents[3]
WEBUI_SRC = REPO / "services" / "webui" / "src"
WEBINFER = REPO / "services" / "webinfer"
for _p in (str(REPO), str(WEBUI_SRC), str(WEBINFER)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from infer_loop import build_stream_frames  # noqa: E402
from response_format import parse_model_decision  # noqa: E402

from joy_interaction_webui.jarvis_mode import JarvisConfig, JarvisStateMachine  # noqa: E402
from joy_interaction_webui.turn_controller import SentenceBuffer  # noqa: E402

# ---------------------------------------------------------------------------
# 1. Streaming frame protocol — multi-marker / delegation-taught format
# ---------------------------------------------------------------------------


def test_qa_stream_delegation_taught_format_matches_parser():
    """Delegation taught as ``</response> note </delegation> question``.

    ``parse_model_decision`` (non-streaming semantics) gives a delegation tag
    ANYWHERE priority — the documented hardening against the taught format.
    The streaming frame builder commits at the earliest complete marker
    (``</response>``) and must converge on the SAME decision, otherwise the
    delegation question is streamed as content and spoken as TTS while
    BackgroundModelService never fires.
    """
    raw = "</response> 我来查一下。 </delegation> 查 Cyberpunk 2077 螳螂帮打法攻略"
    decision, clean, delegation_q = parse_model_decision(raw)
    assert decision == "delegation"
    assert clean == ""

    frames = build_stream_frames(
        ["</response> 我来查一下。 ", "</delegation> 查 Cyberpunk 2077 螳螂帮打法攻略"]
    )
    # Correct behavior: same decision as the unified parser, no content frames
    # (delegation question rides in the decision frame, never spoken as TTS).
    assert frames[0]["decision"] == decision, (
        f"streaming decision={frames[0]['decision']} != parser decision={decision} "
        "— delegation misclassified as response; question will be spoken as TTS"
    )
    assert [f["type"] for f in frames] == ["decision"]
    assert frames[0]["delegation_question"] == delegation_q


def test_qa_stream_multiple_markers_late_delegation_after_response():
    """Late delegation tag after a response marker is still delegation.

    Mirrors the parse_model_decision delegation-priority rule for streaming:
    even when ``</response>`` is the first complete marker, a later
    ``</delegation>`` makes the turn a delegation (nothing spoken).
    """
    raw = "</response> Let me check. </delegation> what is 2+2"
    assert parse_model_decision(raw)[0] == "delegation"
    frames = build_stream_frames(["</response> Let me check. ", "</delegation> what is 2+2"])
    assert frames[0]["decision"] == "delegation"
    assert [f["type"] for f in frames] == ["decision"]


def test_qa_stream_no_decision_pure_content_fails_open_response():
    """No decision marker at all -> fail open to response (never hang)."""
    frames = build_stream_frames(["just", " plain", " text"])
    assert frames[0]["decision"] == "response"
    types = [f["type"] for f in frames]
    assert types[0] == "decision"
    assert all(t == "content" for t in types[1:])
    assert "".join(f["token"] for f in frames if f["type"] == "content") == "just plain text"


def test_qa_stream_decision_content_same_delta_then_more():
    """Decision + body in one delta, then more content deltas."""
    frames = build_stream_frames(["</response> One.", " Two."])
    assert frames[0]["decision"] == "response"
    assert "".join(f["token"] for f in frames if f["type"] == "content") == "One. Two."


# ---------------------------------------------------------------------------
# 2. SentenceBuffer boundaries (reused primitive)
# ---------------------------------------------------------------------------


class _FakeClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_qa_sentence_buffer_max_chars_force_flush():
    """A >max_buffer_chars run with no boundary force-flushes (never grows unbounded)."""
    buf = SentenceBuffer(max_buffer_chars=20)
    flushes: list[str] = []
    for _ in range(30):
        out = buf.add_token("a")
        if out is not None:
            flushes.append(out)
    # No boundary in 30 'a's; a force flush of exactly max_buffer_chars fired.
    assert any(len(f) == 20 for f in flushes), f"expected a 20-char force flush, got {flushes}"
    assert buf.remaining_chars == 10


def test_qa_sentence_buffer_max_chars_exact_boundary():
    """Exactly max_buffer_chars is force-flushed on the boundary token."""
    buf = SentenceBuffer(max_buffer_chars=10)
    out = None
    for _ in range(10):
        out = buf.add_token("x")
    assert out is not None and out == "x" * 10
    assert buf.is_empty


def test_qa_sentence_buffer_abbreviation_not_a_boundary():
    """Abbreviation false positives never flush (U.S. / e.g.)."""
    buf = SentenceBuffer(min_sentence_length=5)
    assert buf.add_token("The U.") is None
    assert buf.add_token("S. is big.") == "The U.S. is big."  # flushed at final '.'
    buf2 = SentenceBuffer(min_sentence_length=5)
    assert buf2.add_token("e.g. cats") is None  # 'e.g.' is not a boundary


def test_qa_sentence_buffer_flush_on_timeout():
    """should_flush_on_timeout() true once idle past flush_on_timeout_ms."""
    clock = _FakeClock(0.0)
    buf = SentenceBuffer(flush_on_timeout_ms=500, clock=clock)
    buf.add_token("hello world no boundary")
    assert not buf.should_flush_on_timeout()
    clock.now = 0.499
    assert not buf.should_flush_on_timeout()
    clock.now = 0.501
    assert buf.should_flush_on_timeout()
    assert buf.flush_remaining() == "hello world no boundary"
    assert not buf.should_flush_on_timeout()


def test_qa_sentence_buffer_min_length_merges_short_sentence():
    """Short candidate (<min) merges into following text instead of flushing."""
    buf = SentenceBuffer(min_sentence_length=10)
    assert buf.add_token("Hi. ") is None  # too short to flush on its own
    assert buf.add_token("This is a longer sentence now.") == "Hi. This is a longer sentence now."


# ---------------------------------------------------------------------------
# 3. Fail-open: pre-frame retry must not double-play
# ---------------------------------------------------------------------------


class _FakeStreamResponse:
    """httpx stream response that raises / yields lines, then stops."""

    def __init__(self, lines, status=200, fail_after=None):
        self._lines = list(lines)
        self.status_code = status
        self.fail_after = fail_after

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def aread(self):
        return b"stream error"

    def aiter_lines(self):
        async def gen():
            for i, line in enumerate(self._lines):
                if self.fail_after is not None and i >= self.fail_after:
                    raise RuntimeError("connection reset mid-stream")
                yield line
            if self.fail_after is not None and self.fail_after >= len(self._lines):
                raise RuntimeError("connection reset mid-stream")

        return gen()


class _FakeJsonResponse:
    def __init__(self, content="", decision="response", delegation_question=None):
        self._content = content
        self._decision = decision
        self._delegation_question = delegation_question

    def raise_for_status(self):
        pass

    def json(self):
        return {
            "choices": [{"message": {"content": self._content}}],
            "streamingharness": {
                "decision": self._decision,
                "delegation_question": self._delegation_question,
            },
        }


class _FakeClient:
    def __init__(self, stream_response, post_response=None):
        self._stream_response = stream_response
        self._post_response = post_response
        self.stream_calls = []
        self.post_calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def stream(self, method, url, **kwargs):
        self.stream_calls.append((method, url, kwargs))
        return self._stream_response

    async def post(self, url, json=None):
        self.post_calls.append((url, json))
        return self._post_response


def _build_sm(**overrides):
    cfg = JarvisConfig(
        wake_word="bt",
        kws_model_dir="ignored",
        asr_model_dir="ignored",
        llm_api_url="http://stub/v1",
        llm_text_path="/text/chat",
        llm_multimodal_path="/chat/completions",
        llm_model="stub",
        llm_system_prompt="be brief",
        tts_api_url="http://tts-stub/v1/synthesize",
        tts_voice_id="stub-voice",
        **overrides,
    )
    return JarvisStateMachine(config=cfg)


def _frame(**kwargs):
    return json.dumps(kwargs, ensure_ascii=False)


@pytest.mark.asyncio
async def test_qa_fail_open_pre_frame_no_double_play():
    """Pre-frame failure -> non-streaming retry AND zero tts_sentence pushes.

    The retry path re-runs the legacy single-shot call; because no stream
    frame was ever consumed, no sentence TTS was spawned — the reply is
    spoken exactly once (via the non-streaming path).
    """
    sm = _build_sm()
    sentence_pushes: list = []
    sm.on_tts_sentence = lambda text, seq, audio_b64, session: sentence_pushes.append(seq)

    stream_resp = _FakeStreamResponse([], status=500)
    post_resp = _FakeJsonResponse(content="fallback ok", decision="response")
    client = _FakeClient(stream_response=stream_resp, post_response=post_resp)
    with patch("httpx.AsyncClient", return_value=client):
        await sm._send_to_llm_streaming("hello")

    await asyncio.gather(*list(sm._tts_sentence_tasks), return_exceptions=True)
    assert sentence_pushes == [], "fail-open retry must not double-play via tts_sentence"
    assert len(client.post_calls) == 1
    url, payload = client.post_calls[0]
    assert url.endswith("/text/chat")
    assert "stream" not in payload
    assert sm._conv_history[-1] == ("assistant", "fallback ok")


@pytest.mark.asyncio
async def test_qa_fail_open_mid_stream_flushes_remaining_sentence():
    """Mid-stream failure after a flushed sentence -> remaining text still spoken."""
    sm = _build_sm()
    pushes: list = []
    sm.on_tts_sentence = lambda text, seq, audio_b64, session: pushes.append((seq, text))
    with patch.object(sm, "_fetch_tts_pcm", new=AsyncMock(return_value=b"\x00\x00" * 100)):
        lines = [
            _frame(type="decision", decision="response", delegation_question=None),
            _frame(type="content", token="First sentence."),
            _frame(type="content", token=" Second incomplete"),
        ]
        client = _FakeClient(stream_response=_FakeStreamResponse(lines, fail_after=3))
        with patch("httpx.AsyncClient", return_value=client):
            await sm._send_to_llm_streaming("hello")
        await asyncio.gather(*list(sm._tts_sentence_tasks), return_exceptions=True)
    texts = [t for _, t in pushes]
    assert any("First sentence." in t for t in texts)
    assert any("Second incomplete" in t for t in texts), (
        "buffered remainder lost on mid-stream failure"
    )


# ---------------------------------------------------------------------------
# 4. Cancellation: epoch bump + in-flight drop (real _pause_tts path)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_qa_pause_tts_bumps_epoch_and_cancels_inflight():
    """_pause_tts (barge-in) bumps the sentence epoch and drops in-flight audio.

    A sentence task spawned BEFORE the pause captured an older epoch; the
    epoch bump must invalidate it even if synthesis completes later.
    """
    sm = _build_sm()
    sm._ensure_tts_stream_state()
    epoch_before = sm._tts_sentence_epoch

    # Simulate an in-flight sentence task.
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_tts(text, seq, reply_session, epoch):
        started.set()
        await release.wait()
        await sm._synthesize_tts_sentence(text, seq, reply_session, epoch)

    task = asyncio.create_task(slow_tts("stale", 0, 0, epoch_before))
    sm._tts_sentence_tasks.add(task)
    task.add_done_callback(sm._tts_sentence_tasks.discard)
    await started.wait()

    await sm._pause_tts()
    assert sm._tts_sentence_epoch == epoch_before + 1, "pause must bump the sentence epoch"
    # Give the event loop a chance to deliver the cancellation, then verify
    # the in-flight task was actually cancelled (not just marked).
    await asyncio.gather(task, return_exceptions=True)
    assert task.cancelled(), "pause must cancel the in-flight sentence task"
    assert sm._llm_stream_cancel is True, "pause must flag the LLM stream consumer"

    # A stale task that somehow survives must not push audio (epoch guard).
    stale_pushes: list = []
    sm.on_tts_sentence = lambda text, seq, audio_b64, session: stale_pushes.append(seq)
    with patch.object(sm, "_fetch_tts_pcm", new=AsyncMock(return_value=b"\x00\x00" * 100)):
        await sm._synthesize_tts_sentence("stale", 1, 0, epoch_before)  # old epoch
    assert stale_pushes == [], "stale sentence (old epoch) must never be pushed"


@pytest.mark.asyncio
async def test_qa_stop_tts_cancels_epoch_guard_like_pause():
    """_stop_tts (exit word) also bumps epoch + cancels sentence tasks."""
    sm = _build_sm()
    sm._ensure_tts_stream_state()
    epoch_before = sm._tts_sentence_epoch
    await sm._stop_tts()
    assert sm._tts_sentence_epoch == epoch_before + 1
    assert sm._llm_stream_cancel is True
    assert sm._tts_sentence_tasks == set()


# ---------------------------------------------------------------------------
# 5. Frontend per-sentence queue — static contract
# ---------------------------------------------------------------------------

INDEX_HTML = REPO / "services" / "webui" / "src" / "joy_interaction_webui" / "static" / "index.html"
# Batch-3 split: index.html's inline script#2 was extracted into standalone JS
# files (same dependency order as the <script src> tags). Combined sources keep
# the static-contract assertions pointing at the moved code with unchanged
# semantics.
_SPLIT_JS = (
    "app_boot.js",
    "app_main.js",
    "sidebar_toggle.js",
    "incremental_wiring.js",
    "vlm_history.js",
    "llm_reply_ui.js",
    "ws_dispatcher.js",
    "vlm_render.js",
    "background_rich.js",
    "tts_player.js",
    "speech_input.js",
    "live_ui.js",
    "llm_reply_audio.js",
    "status_poll.js",
)
_JS = "\n".join(
    [INDEX_HTML.read_text(encoding="utf-8")]
    + [(INDEX_HTML.parent / name).read_text(encoding="utf-8") for name in _SPLIT_JS]
)


def test_qa_frontend_out_of_order_skipped_never_hangs():
    """Queue skips a non-matching seq (log, continue) instead of blocking."""
    assert "if (item.seq !== llmReplyQueueNextSeq)" in _JS
    assert "llmReplyQueue.shift();" in _JS
    assert "skipped out-of-order sentence" in _JS


def test_qa_frontend_new_session_supersedes_stale_queue():
    """A new reply session clears the stale queue (backstop)."""
    assert "session !== llmReplyQueueSession" in _JS
    assert "llmReplyQueue = [];" in _JS


def test_qa_frontend_stop_llm_reply_audio_preserves_barge_in_epoch():
    """stopLlmReplyAudio still bumps the epoch FIRST (P0 barge-in intact)."""
    idx = _JS.find("function stopLlmReplyAudio()")
    assert idx != -1
    snippet = _JS[idx : idx + 400]
    # Original P0 logic: epoch bump is the first statement.
    assert snippet.index("llmReplyEpoch += 1;") < snippet.index("llmReplyQueue = [];")
    assert "btTtsPlayer.pause();" in snippet
    assert "btTtsPlayer.currentTime = 0;" in snippet


def test_qa_frontend_playback_failure_falls_back_to_synthesize():
    """decode/play failure falls back to /api/tts/synthesize, never hangs."""
    assert "'/api/tts/synthesize'" in _JS
    assert "audio decode failed, falling back to synthesize" in _JS
