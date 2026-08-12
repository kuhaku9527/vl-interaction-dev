"""Independent unit tests for the shared ``turn_streaming`` consumer.

The consumer is the extracted P0-A streaming logic shared by jarvis and the
future live dialog (spec ``draft-live-interaction-layer.md`` §4.3). These
tests pin the consumer's own contract (frame handling, SentenceBuffer
flushing, fail-open, cancellation) without any jarvis state machine; the
jarvis-side regression is covered by ``test_jarvis_tts_streaming.py``.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[3]
WEBUI_SRC = REPO / "services" / "webui" / "src"
for _p in (str(REPO), str(WEBUI_SRC)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from joy_interaction_webui.turn_streaming import StreamingTurnConsumer  # noqa: E402


def _frame(**kwargs):
    return json.dumps(kwargs, ensure_ascii=False)


class FakeStreamResponse:
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


class FakeClient:
    def __init__(self, stream_response):
        self._stream_response = stream_response
        self.stream_calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def stream(self, method, url, **kwargs):
        self.stream_calls.append((method, url, kwargs))
        return self._stream_response


def _consumer(**overrides):
    kwargs = {
        "endpoint_url": "http://stub/v1/text/chat",
        "model": "stub",
        "system_prompt": "be brief",
        "history_snapshot": [],
    }
    kwargs.update(overrides)
    return StreamingTurnConsumer(**kwargs)


def _run(consumer, lines, *, reply_session=0, cancel=None, fail_after=None, status=200):
    client = FakeClient(
        stream_response=FakeStreamResponse(lines, status=status, fail_after=fail_after)
    )
    with patch("httpx.AsyncClient", return_value=client):
        return asyncio.run(
            consumer.consume("hello", interaction_mode="jarvis", reply_session=reply_session)
        )


# ---------------------------------------------------------------------------
# Frame handling / sentence flush
# ---------------------------------------------------------------------------


def test_build_messages_composes_system_history_user():
    consumer = _consumer(history_snapshot=[("user", "prev q"), ("assistant", "prev a")])
    messages = consumer.build_messages("now")
    assert messages == [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "prev q"},
        {"role": "assistant", "content": "prev a"},
        {"role": "user", "content": "now"},
    ]


def test_flushes_sentences_in_order_and_returns_full_text():
    sentences: list = []
    consumer = _consumer(on_sentence=lambda s, seq, rs: sentences.append((seq, s.strip(), rs)))
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
    result = _run(consumer, lines, reply_session=42)
    assert [s[0] for s in sentences] == [0, 1]
    assert [s[1] for s in sentences] == [
        "Hello there, Pilot.",
        "How are you doing today?",
    ]
    assert all(s[2] == 42 for s in sentences), "sentences tagged with reply_session"
    assert result.full_response == "Hello there, Pilot. How are you doing today?"
    assert result.decision == "response"
    assert result.cancelled is False
    assert result.needs_non_streaming_retry is False
    assert result.sentence_count == 2
    assert result.reply_session == 42


def test_long_chinese_reply_splits_at_commas():
    """A long comma-connected Chinese reply (no hard ending) is flushed in
    ~max_sentence_chars chunks instead of one huge block (the 2026-08-12
    sentence-granularity fix)."""
    sentences: list = []
    consumer = _consumer(
        on_sentence=lambda s, seq, rs: sentences.append((seq, s.strip())),
        max_sentence_chars=20,
    )
    full_text = "今天天气很好，我们一起去公园散步，然后回家吃饭，再去看一场电影，"
    lines = [
        _frame(type="decision", decision="response", delegation_question=None),
        _frame(type="content", token="今天天气很好，我们一起去公园散步，"),
        _frame(type="content", token="然后回家吃饭，再去看一场电影，"),
        _frame(type="done", decision="response", full_text=full_text, delegation_question=None),
    ]
    result = _run(consumer, lines, reply_session=7)
    assert [s[0] for s in sentences] == [0, 1]
    assert [s[1] for s in sentences] == [
        "今天天气很好，我们一起去公园散步，",
        "然后回家吃饭，再去看一场电影，",
    ]
    assert result.sentence_count == 2
    assert result.full_response == full_text
    assert result.cancelled is False


def test_silence_never_flushes_sentences():
    sentences: list = []
    consumer = _consumer(on_sentence=lambda s, seq, rs: sentences.append(seq))
    lines = [
        _frame(type="decision", decision="silence", delegation_question=None),
        _frame(type="done", decision="silence", full_text="", delegation_question=None),
    ]
    result = _run(consumer, lines)
    assert sentences == []
    assert result.full_response == ""
    assert result.decision == "silence"


def test_delegation_skips_content_and_carries_question():
    sentences: list = []
    consumer = _consumer(on_sentence=lambda s, seq, rs: sentences.append(seq))
    lines = [
        _frame(type="decision", decision="delegation", delegation_question="查攻略"),
        _frame(type="done", decision="delegation", full_text="", delegation_question="查攻略"),
    ]
    result = _run(consumer, lines)
    assert sentences == []
    assert result.decision == "delegation"
    assert result.delegation_question == "查攻略"


def test_corrected_delegation_drops_buffered_note():
    sentences: list = []
    consumer = _consumer(on_sentence=lambda s, seq, rs: sentences.append((seq, s)))
    lines = [
        _frame(type="decision", decision="response", delegation_question=None),
        _frame(type="content", token="Let me check"),
        _frame(
            type="decision",
            decision="delegation",
            delegation_question="what is 2+2",
            corrected=True,
        ),
        _frame(
            type="done",
            decision="delegation",
            full_text="",
            delegation_question="what is 2+2",
        ),
    ]
    result = _run(consumer, lines)
    assert all("2+2" not in (t or "") for _, t in sentences)
    assert result.decision == "delegation"
    assert result.full_response == ""


def test_not_for_me_never_flushes_sentences():
    """not-for-me (addressee Phase 2) is a zero-TTS decision like silence."""
    sentences: list = []
    consumer = _consumer(on_sentence=lambda s, seq, rs: sentences.append(seq))
    lines = [
        _frame(type="decision", decision="not-for-me", delegation_question=None),
        _frame(
            type="done",
            decision="not-for-me",
            full_text="",
            delegation_question=None,
        ),
    ]
    result = _run(consumer, lines)
    assert sentences == []
    assert result.full_response == ""
    assert result.decision == "not-for-me"
    assert result.delegation_question is None


def test_not_for_me_skips_stray_content_frames():
    """Content frames after a not-for-me decision are never spoken."""
    sentences: list = []
    consumer = _consumer(on_sentence=lambda s, seq, rs: sentences.append(seq))
    lines = [
        _frame(type="decision", decision="not-for-me", delegation_question=None),
        _frame(type="content", token="should never be spoken"),
        _frame(
            type="done",
            decision="not-for-me",
            full_text="",
            delegation_question=None,
        ),
    ]
    result = _run(consumer, lines)
    assert sentences == []
    assert result.full_response == ""
    assert result.decision == "not-for-me"


def test_corrected_not_for_me_drops_buffered_note():
    """A late </not-for-me> correction drops the buffered response (no TTS)."""
    sentences: list = []
    consumer = _consumer(on_sentence=lambda s, seq, rs: sentences.append((seq, s)))
    lines = [
        _frame(type="decision", decision="response", delegation_question=None),
        _frame(type="content", token="Let me check"),
        _frame(
            type="decision",
            decision="not-for-me",
            delegation_question=None,
            corrected=True,
        ),
        _frame(
            type="done",
            decision="not-for-me",
            full_text="",
            delegation_question=None,
        ),
    ]
    result = _run(consumer, lines)
    assert sentences == []
    assert result.decision == "not-for-me"
    assert result.full_response == ""


def test_malformed_frames_are_skipped():
    sentences: list = []
    consumer = _consumer(on_sentence=lambda s, seq, rs: sentences.append((seq, s)))
    lines = [
        "not-json{{",
        _frame(type="decision", decision="response", delegation_question=None),
        _frame(type="content", token="Hi there."),
        _frame(type="done", decision="response", full_text="Hi there.", delegation_question=None),
    ]
    result = _run(consumer, lines)
    assert [s[0] for s in sentences] == [0]
    assert result.full_response == "Hi there."


# ---------------------------------------------------------------------------
# Fail-open
# ---------------------------------------------------------------------------


def test_pre_frame_failure_requests_non_streaming_retry():
    consumer = _consumer()
    result = _run(consumer, [], status=500)
    assert result.needs_non_streaming_retry is True
    assert result.cancelled is False


def test_mid_stream_failure_flushes_remainder():
    sentences: list = []
    consumer = _consumer(on_sentence=lambda s, seq, rs: sentences.append((seq, s.strip())))
    lines = [
        _frame(type="decision", decision="response", delegation_question=None),
        _frame(type="content", token="Hello there"),
    ]
    result = _run(consumer, lines, fail_after=2)
    assert sentences == [(0, "Hello there")]
    assert result.full_response == "Hello there"
    assert result.needs_non_streaming_retry is False
    assert result.cancelled is False


def test_error_frame_fails_open_like_transport_error():
    consumer = _consumer()
    lines = [
        _frame(type="decision", decision="response", delegation_question=None),
        _frame(type="content", token="Hi "),
        _frame(type="error", error="backend exploded", error_type="RuntimeError"),
    ]
    result = _run(consumer, lines)
    assert result.full_response == "Hi "
    assert result.needs_non_streaming_retry is False


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


def test_cancel_flag_stops_consumption_and_returns_cancelled():
    sentences: list = []
    consumer = _consumer(on_sentence=lambda s, seq, rs: sentences.append(seq))
    cancel = {"flag": False}

    def is_cancelled():
        return cancel["flag"]

    consumer.is_cancelled = is_cancelled

    class CancelStream(FakeStreamResponse):
        def aiter_lines(self):
            async def gen():
                yield _frame(type="decision", decision="response", delegation_question=None)
                cancel["flag"] = True  # simulate barge-in from the audio loop
                yield _frame(type="content", token="ignored after cancel")

            return gen()

    client = FakeClient(stream_response=CancelStream([]))
    with patch("httpx.AsyncClient", return_value=client):
        result = asyncio.run(consumer.consume("hello", interaction_mode="jarvis", reply_session=0))
    assert result.cancelled is True
    assert sentences == []
