"""P0-A TTS-streaming tests: jarvis consumes webinfer NDJSON stream + sentence TTS.

Covers the jarvis-side streaming consumer (``_send_to_llm_streaming``):

  * decision frame first (silence / response / delegation semantics preserved);
  * content frames fed into the reusable SentenceBuffer, one ``tts_sentence``
    per flushed sentence (ordered ``seq``, carrying WAV base64 audio);
  * silence never synthesizes TTS; delegation routes to BackgroundModelService;
  * fail-open: a stream that dies before any frame re-runs the non-streaming
    path; a mid-stream failure keeps and speaks the buffered remainder;
  * barge-in / exit-word cancellation stops the sentence queue and drops stale
    audio (epoch guard).
"""

from __future__ import annotations

import asyncio
import base64
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

REPO = Path(__file__).resolve().parents[2]
WEBUI_SRC = REPO / "services" / "webui" / "src"
for _p in (str(REPO), str(WEBUI_SRC)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from joy_interaction_webui.jarvis_mode import JarvisConfig, JarvisStateMachine  # noqa: E402


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeStreamResponse:
    """httpx streaming response: yields NDJSON lines, then stops / raises."""

    def __init__(self, lines, status=200, fail_after=None):
        self._lines = list(lines)
        self.status_code = status
        self.fail_after = fail_after
        self.raised = None

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


class FakeAsyncClient:
    """httpx.AsyncClient fake with .stream (LLM) and .post (fallback / TTS)."""

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


class FakeJsonResponse:
    """Non-streaming webinfer-style JSON response (for fail-open retry)."""

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


def _frame(**kwargs):
    return json.dumps(kwargs, ensure_ascii=False)


def _build_sm(**cfg_overrides):
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
        **cfg_overrides,
    )
    return JarvisStateMachine(config=cfg)


def _run_streaming(sm, stream_response, **kw):
    """Drive _send_to_llm_streaming with a fake httpx client."""
    client = FakeAsyncClient(stream_response=stream_response)
    with patch("httpx.AsyncClient", return_value=client):
        return sm, client


# ---------------------------------------------------------------------------
# Sentence flush order + tts_sentence push
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_streaming_flushes_sentences_in_order():
    """Content tokens -> SentenceBuffer -> one tts_sentence per sentence (seq order)."""
    sm = _build_sm()
    sentences: list = []
    sm.on_tts_sentence = lambda text, seq, audio_b64, session: sentences.append(
        (seq, text, audio_b64, session)
    )
    # Keep the _fetch_tts_pcm patch alive through the background sentence tasks.
    with patch.object(sm, "_fetch_tts_pcm", new=AsyncMock(return_value=b"\x00\x00" * 200)):
        lines = [
            _frame(type="decision", decision="response", delegation_question=None),
            _frame(type="content", token="Hello there, Pilot"),
            _frame(type="content", token="."),
            _frame(type="content", token=" How are you doing today"),
            _frame(type="content", token="?"),
            _frame(
                type="done",
                decision="response",
                full_text="Hello there, Pilot. How are you doing today?",
                delegation_question=None,
            ),
        ]
        client = FakeAsyncClient(stream_response=FakeStreamResponse(lines))
        with patch("httpx.AsyncClient", return_value=client):
            await sm._send_to_llm_streaming("hello")
        await asyncio.gather(*list(sm._tts_sentence_tasks), return_exceptions=True)

    assert [s[0] for s in sentences] == [0, 1]
    assert [s[1].strip() for s in sentences] == [
        "Hello there, Pilot.",
        "How are you doing today?",
    ]
    # audio is WAV base64
    for _, _, audio_b64, _session in sentences:
        raw = base64.b64decode(audio_b64)
        assert raw[:4] == b"RIFF"
        assert raw[8:12] == b"WAVE"
    # all sentences share the same reply session id
    assert len({s[3] for s in sentences}) == 1
    # transcript appended by _finish_llm_turn
    assert sm._conv_history[-1] == (
        "assistant",
        "Hello there, Pilot. How are you doing today?",
    )


@pytest.mark.asyncio
async def test_streaming_silence_skips_tts():
    """decision=silence -> no sentence TTS at all, no audio pushed."""
    sm = _build_sm()
    sentences: list = []
    sm.on_tts_sentence = lambda text, seq, audio_b64, session: sentences.append(seq)
    with patch.object(sm, "_fetch_tts_pcm", new=AsyncMock(return_value=b"\x00\x00" * 100)):
        lines = [
            _frame(type="decision", decision="silence", delegation_question=None),
            _frame(type="done", decision="silence", full_text="", delegation_question=None),
        ]
        client = FakeAsyncClient(stream_response=FakeStreamResponse(lines))
        with patch("httpx.AsyncClient", return_value=client):
            await sm._send_to_llm_streaming("hi")

    await asyncio.gather(*list(sm._tts_sentence_tasks), return_exceptions=True)
    assert sentences == []
    assert sm._conv_history[-1] == ("assistant", "")


@pytest.mark.asyncio
async def test_streaming_delegation_routes_to_background_and_skips_tts():
    """decision=delegation -> BackgroundModelService fires, no sentence TTS."""
    from joy_interaction_webui import server

    bg = SimpleNamespace(enabled=True, _closed=False, handle_foreground_response=Mock())
    server.sessions["stream-delegation"] = {"background_service": bg, "vlm_service": SimpleNamespace()}
    try:
        sm = _build_sm()
        sm._background_service = bg
        sentences: list = []
        sm.on_tts_sentence = lambda text, seq, audio_b64, session: sentences.append(seq)
        with patch.object(sm, "_fetch_tts_pcm", new=AsyncMock(return_value=b"\x00\x00" * 100)):
            lines = [
                _frame(
                    type="decision",
                    decision="delegation",
                    delegation_question="查 Cyberpunk 螳螂帮打法攻略",
                ),
                _frame(
                    type="done",
                    decision="delegation",
                    full_text="",
                    delegation_question="查 Cyberpunk 螳螂帮打法攻略",
                ),
            ]
            client = FakeAsyncClient(stream_response=FakeStreamResponse(lines))
            with patch("httpx.AsyncClient", return_value=client):
                await sm._send_to_llm_streaming("帮我查下")

        await asyncio.gather(*list(sm._tts_sentence_tasks), return_exceptions=True)
        assert sentences == []
        bg.handle_foreground_response.assert_called_once()
        call = bg.handle_foreground_response.call_args
        metrics = call.kwargs.get("metrics") or {}
        assert "帮我查下" in call.args[0]
        assert metrics.get("delegation_question") == "查 Cyberpunk 螳螂帮打法攻略"
        assert sm._conv_history[-1] == ("assistant", "")
    finally:
        server.sessions.pop("stream-delegation", None)


# ---------------------------------------------------------------------------
# Fail-open
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_streaming_fail_open_reruns_non_streaming_before_any_frame():
    """Stream dies before any frame -> clean non-streaming retry (reply kept)."""
    sm = _build_sm()
    broadcasts: list = []
    sm.on_llm_response = lambda text, source: broadcasts.append((text, source))

    stream_resp = FakeStreamResponse([], status=500)
    post_resp = FakeJsonResponse(content="fallback ok", decision="response")
    client = FakeAsyncClient(stream_response=stream_resp, post_response=post_resp)
    with patch("httpx.AsyncClient", return_value=client):
        await sm._send_to_llm_streaming("hello")

    # The fallback POST went to /v1/text/chat WITHOUT stream=true.
    assert len(client.post_calls) == 1
    url, payload = client.post_calls[0]
    assert url.endswith("/text/chat")
    assert "stream" not in payload
    assert broadcasts == [("fallback ok", "jarvis_text")]
    assert sm._conv_history[-1] == ("assistant", "fallback ok")


@pytest.mark.asyncio
async def test_streaming_mid_stream_failure_flushes_remainder():
    """Stream breaks after a decision + partial content -> remainder is spoken."""
    sm = _build_sm()
    sentences: list = []
    sm.on_tts_sentence = lambda text, seq, audio_b64, session: sentences.append(
        (seq, text.strip())
    )
    with patch.object(sm, "_fetch_tts_pcm", new=AsyncMock(return_value=b"\x00\x00" * 100)):
        lines = [
            _frame(type="decision", decision="response", delegation_question=None),
            _frame(type="content", token="Hello there"),
        ]
        client = FakeAsyncClient(
            stream_response=FakeStreamResponse(lines, fail_after=2)
        )
        with patch("httpx.AsyncClient", return_value=client):
            await sm._send_to_llm_streaming("hello")
        await asyncio.gather(*list(sm._tts_sentence_tasks), return_exceptions=True)
    # The buffered (un-flushed) remainder was synthesized so nothing is lost.
    assert sentences == [(0, "Hello there")]
    assert sm._conv_history[-1] == ("assistant", "Hello there")


@pytest.mark.asyncio
async def test_streaming_error_frame_fails_open():
    """webinfer error frame -> same fail-open as a transport error."""
    sm = _build_sm()
    with patch.object(sm, "_fetch_tts_pcm", new=AsyncMock(return_value=b"\x00\x00" * 100)):
        lines = [
            _frame(type="decision", decision="response", delegation_question=None),
            _frame(type="content", token="Hi "),
            _frame(type="error", error="backend exploded", error_type="RuntimeError"),
        ]
        client = FakeAsyncClient(stream_response=FakeStreamResponse(lines))
        with patch("httpx.AsyncClient", return_value=client):
            await sm._send_to_llm_streaming("hello")
    await asyncio.gather(*list(sm._tts_sentence_tasks), return_exceptions=True)
    assert sm._conv_history[-1] == ("assistant", "Hi ")


# ---------------------------------------------------------------------------
# Cancellation (barge-in / exit word)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_streaming_cancellation_stops_consumption_and_no_broadcast():
    """_llm_stream_cancel (barge-in) mid-stream -> stop, no finish broadcast."""
    sm = _build_sm()
    broadcasts: list = []
    sm.on_llm_response = lambda text, source: broadcasts.append((text, source))

    def make_response():
        async def gen():
            yield _frame(type="decision", decision="response", delegation_question=None)
            sm._llm_stream_cancel = True  # simulate barge-in from the audio loop
            yield _frame(type="content", token="ignored after cancel")

        return gen()

    class CancelStream(FakeStreamResponse):
        def aiter_lines(self):
            return make_response()

    client = FakeAsyncClient(stream_response=CancelStream([]))
    with patch("httpx.AsyncClient", return_value=client):
        await sm._send_to_llm_streaming("hello")

    await asyncio.sleep(0.05)
    assert broadcasts == []
    # history is NOT appended for a cancelled turn (user is talking over it)
    assert len(sm._conv_history) == 0


@pytest.mark.asyncio
async def test_pause_tts_cancels_sentence_tasks_and_drops_stale_audio():
    """_pause_tts bumps the epoch; a sentence that survives must not push."""
    sm = _build_sm()
    sentences: list = []
    sm.on_tts_sentence = lambda text, seq, audio_b64, session: sentences.append(seq)

    release = asyncio.Event()

    async def slow_fetch(_text):
        await release.wait()
        return b"\x00\x00" * 100

    with patch.object(sm, "_fetch_tts_pcm", new=slow_fetch):
        sm._spawn_sentence_tts("Hello there, Pilot.", 0, 0)
        await asyncio.sleep(0.01)
        await sm._pause_tts()  # barge-in: bump epoch + cancel in-flight tasks
        release.set()
        await asyncio.gather(*list(sm._tts_sentence_tasks), return_exceptions=True)

    assert sentences == []
    assert sm._tts_sentence_tasks == set()


@pytest.mark.asyncio
async def test_stale_sentence_dropped_by_epoch_guard():
    """A sentence task spawned under an old epoch drops its audio."""
    sm = _build_sm()
    sentences: list = []
    sm.on_tts_sentence = lambda text, seq, audio_b64, session: sentences.append(seq)
    sm._tts_sentence_epoch = 5
    with patch.object(sm, "_fetch_tts_pcm", new=AsyncMock(return_value=b"\x00\x00" * 100)):
        # Same-epoch synthesis is pushed.
        await sm._synthesize_tts_sentence("hello", 0, 0, epoch=5)
        # Epoch bumped (barge-in) while the "synthesis" ran -> dropped.
        sm._tts_sentence_epoch = 6
        await sm._synthesize_tts_sentence("hello", 1, 0, epoch=5)
    assert sentences == [0]


# ---------------------------------------------------------------------------
# Dispatcher: call mode stays non-streaming; wav wrapper sanity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatcher_call_mode_stays_non_streaming():
    """interaction_mode='call' (paper-plane) must keep the non-streaming path."""
    sm = _build_sm(llm_streaming_enabled=True)
    post_resp = FakeJsonResponse(content="call reply", decision="response")
    client = FakeAsyncClient(
        stream_response=FakeStreamResponse([]), post_response=post_resp
    )
    with patch("httpx.AsyncClient", return_value=client):
        await sm._send_to_llm("hi", stream_tts=False, interaction_mode="call")
    assert len(client.stream_calls) == 0
    assert len(client.post_calls) == 1
    assert "stream" not in client.post_calls[0][1]


def test_wrap_pcm16_wav_header():
    """WAV wrapper emits a valid RIFF/WAVE header with the PCM payload."""
    pcm = b"\x01\x02" * 1000
    wav = JarvisStateMachine._wrap_pcm16_wav(pcm, sample_rate=24000)
    assert wav[:4] == b"RIFF"
    assert wav[8:12] == b"WAVE"
    assert wav[36:40] == b"data"
    assert len(wav) == 44 + len(pcm)
