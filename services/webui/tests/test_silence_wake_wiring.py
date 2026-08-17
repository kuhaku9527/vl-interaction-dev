"""B2/B3 wiring tests: radio-silence KWS wake channel + wake.wav ceremony.

Reviewer-gate fixes (spec ``doc/specs/draft-radio-silence.md`` §4/§5):

- **B2 (KWS wake channel)**: the webui live session runs a KWS engine while
  suppressed + kws_enabled; a wake-word hit pushes ``kws_event`` to webinfer
  through the silence proxy (the previously-missing producer). ``set_silence_state``
  mirrors the webinfer-owned state onto the session; ``_propagate_silence_to_live_sessions``
  fans it out from the proxy after any POST.
- **B3 (wake ceremony)**: ``StreamingTurnConsumer`` surfaces webinfer's done-frame
  ``silence: {wake: True}`` via ``on_silence_wake``; the live session pushes the
  pre-recorded wake.wav to the browser as a ``silence_wake`` WS event (zero token),
  cooldown-deduped against the KWS-push fast path.
"""

from __future__ import annotations

import asyncio
import json
import sys
import wave
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
WEBUI_SRC = REPO / "services" / "webui" / "src"
for _p in (str(REPO), str(WEBUI_SRC)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from joy_interaction_webui import (  # noqa: E402
    server,
    silence_proxy,
    ws_notify,
)
from joy_interaction_webui.jarvis_config import JarvisConfig  # noqa: E402
from joy_interaction_webui.live_llm import send_to_llm  # noqa: E402
from joy_interaction_webui.live_mode import LiveStateMachine  # noqa: E402
from joy_interaction_webui.silence_proxy import (  # noqa: E402
    _propagate_silence_to_live_sessions,
)
from joy_interaction_webui.turn_streaming import StreamingTurnConsumer  # noqa: E402

PCM = (np.zeros(1600, dtype=np.int16)).tobytes()  # 100ms @ 16kHz mono silence


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _stub_config(events_dir: str = "") -> JarvisConfig:
    return JarvisConfig(
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
        events_dir=events_dir,
    )


def _build_live(session_id: str = "s1", events_dir: str = "") -> LiveStateMachine:
    class _FakeVAD:
        available = False

    class _FakeASR:
        streaming = True

        def feed_audio(self, pcm, *a, **k):
            pass

        def stop(self):
            pass

    return LiveStateMachine(
        config=_stub_config(events_dir=events_dir),
        session_id=session_id,
        vad=_FakeVAD(),
        asr=_FakeASR(),
        controller=None,
    )


class _FakeKWS:
    """KWS stub that reports a hit (or not) on every feed."""

    def __init__(self, hit: bool = True) -> None:
        self.hit = hit
        self.fed = 0
        self.stopped = False

    def start(self) -> None:
        pass

    def feed_audio(self, pcm: bytes) -> bool:
        self.fed += 1
        return self.hit

    def stop(self) -> None:
        self.stopped = True


def _write_wake_wav(events_dir: Path) -> None:
    """Write a small mono 16kHz wake.wav (valid RIFF/WAVE)."""
    n = 1600
    samples = (np.arange(n) % 1000).astype(np.int16)
    with wave.open(str(events_dir / "wake.wav"), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(samples.tobytes())


class _FakeStreamResponse:
    def __init__(self, lines, status=200):
        self._lines = lines
        self.status_code = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def aread(self):
        return b"x"

    def aiter_lines(self):
        async def gen():
            for line in self._lines:
                yield line

        return gen()


class _FakeClient:
    def __init__(self, stream_response):
        self._stream_response = stream_response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def stream(self, method, url, **kwargs):
        return self._stream_response


def _run_consumer(consumer, lines):
    from unittest.mock import patch

    client = _FakeClient(_FakeStreamResponse(lines))
    with patch("httpx.AsyncClient", return_value=client):
        return asyncio.run(consumer.consume("hello", interaction_mode="live"))


# ---------------------------------------------------------------------------
# B3 — StreamingTurnConsumer surfaces silence.wake
# ---------------------------------------------------------------------------
def test_streaming_consumer_fires_on_silence_wake():
    wakes = []
    consumer = StreamingTurnConsumer(
        endpoint_url="http://stub/v1/text/chat",
        model="stub",
        system_prompt="be brief",
        history_snapshot=[],
        on_silence_wake=lambda: wakes.append(True),
    )
    lines = [
        json.dumps({"type": "decision", "decision": "response"}),
        json.dumps({"type": "done", "decision": "response", "silence": {"wake": True}}),
    ]
    _run_consumer(consumer, lines)
    assert wakes == [True]


def test_streaming_consumer_no_wake_without_silence_meta():
    wakes = []
    consumer = StreamingTurnConsumer(
        endpoint_url="http://stub/v1/text/chat",
        model="stub",
        system_prompt="be brief",
        history_snapshot=[],
        on_silence_wake=lambda: wakes.append(True),
    )
    lines = [
        json.dumps({"type": "decision", "decision": "response"}),
        json.dumps({"type": "done", "decision": "response"}),
    ]
    _run_consumer(consumer, lines)
    assert wakes == []


def test_streaming_consumer_no_wake_for_non_wake_silence_meta():
    """silence: {wake: False} (or non-dict) must not fire."""
    wakes = []
    consumer = StreamingTurnConsumer(
        endpoint_url="http://stub/v1/text/chat",
        model="stub",
        system_prompt="be brief",
        history_snapshot=[],
        on_silence_wake=lambda: wakes.append(True),
    )
    lines = [
        json.dumps({"type": "done", "decision": "silence", "silence": {"wake": False}}),
    ]
    _run_consumer(consumer, lines)
    assert wakes == []


# ---------------------------------------------------------------------------
# B3 — live_llm threads on_silence_wake to the consumer
# ---------------------------------------------------------------------------
def test_live_llm_threads_on_silence_wake():
    captured = {}

    class _RecordingConsumer:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def consume(self, text, interaction_mode, reply_session):
            return _FakeResult()

    class _FakeResult:
        needs_non_streaming_retry = False
        cancelled = False
        full_response = "ok"
        decision = "response"
        delegation_question = None

    async def _run():
        async def _noop_finish(**kwargs):
            return None

        async def _noop_retry(**kwargs):
            return None

        await send_to_llm(
            text="hi",
            interaction_mode="live",
            frames=None,
            config=_stub_config(),
            llm_reply_epoch=0,
            tts_reply_seq=0,
            conv_history=[],
            max_history_turns=4,
            consumer_cls=_RecordingConsumer,
            on_sentence=lambda *a: None,
            is_cancelled=lambda: False,
            on_finish_turn=_noop_finish,
            on_retry_non_streaming=_noop_retry,
            on_silence_wake=lambda: None,
            logger=__import__("logging").getLogger("test"),
        )

    asyncio.run(_run())
    assert "on_silence_wake" in captured
    assert callable(captured["on_silence_wake"])


# ---------------------------------------------------------------------------
# B3 — live session pushes wake.wav to the browser
# ---------------------------------------------------------------------------
def test_live_play_silence_wake_wav_pushes_to_browser(tmp_path, monkeypatch):
    _write_wake_wav(tmp_path)
    sm = _build_live(session_id="s1", events_dir=str(tmp_path))
    pushed = {}
    monkeypatch.setattr(
        ws_notify,
        "notify_session_silence_wake",
        lambda sid, b64: pushed.update(sid=sid, b64=b64),
    )
    sm._play_silence_wake_wav()
    assert pushed.get("sid") == "s1"
    assert pushed.get("b64"), "expected non-empty WAV base64"
    # The pushed blob is a playable WAV.
    import base64

    wav = base64.b64decode(pushed["b64"])
    assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE"


def test_live_play_silence_wake_wav_cooldown_dedup(tmp_path, monkeypatch):
    _write_wake_wav(tmp_path)
    sm = _build_live(session_id="s1", events_dir=str(tmp_path))
    pushes = []
    monkeypatch.setattr(
        ws_notify,
        "notify_session_silence_wake",
        lambda sid, b64: pushes.append(b64),
    )
    sm._play_silence_wake_wav()
    sm._play_silence_wake_wav()  # within cooldown -> deduped
    assert len(pushes) == 1


def test_live_play_silence_wake_wav_missing_file_noop(tmp_path, monkeypatch):
    """Missing wake.wav (still a user TODO) only logs — no crash, no push."""
    sm = _build_live(session_id="s1", events_dir=str(tmp_path))
    pushes = []
    monkeypatch.setattr(
        ws_notify,
        "notify_session_silence_wake",
        lambda sid, b64: pushes.append(b64),
    )
    sm._play_silence_wake_wav()
    assert pushes == []


# ---------------------------------------------------------------------------
# B2 — live silence-KWS channel
# ---------------------------------------------------------------------------
async def test_live_silence_kws_hit_pushes_event_and_clears_mirror(tmp_path, monkeypatch):
    _write_wake_wav(tmp_path)
    sm = _build_live(session_id="s1", events_dir=str(tmp_path))
    sm.set_silence_state(True, True)
    sm._silence_kws = _FakeKWS(hit=True)

    pushed = {}
    monkeypatch.setattr(
        ws_notify,
        "notify_session_silence_wake",
        lambda sid, b64: pushed.update(sid=sid, b64=b64),
    )

    async def fake_forward():
        # webinfer wakes on the kws_event.
        return {"suppressed": False, "wake_pending": True}

    monkeypatch.setattr(silence_proxy, "_silence_forward_kws_event", fake_forward)

    await sm._feed_silence_kws(PCM)
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert sm._silence_suppressed is False
    assert sm._silence_kws is None  # engine released after wake
    assert pushed.get("sid") == "s1"  # wake ceremony played


async def test_live_silence_kws_gated_by_suppressed_in_feed_audio():
    """feed_audio must NOT touch the KWS engine while not suppressed."""
    sm = _build_live()
    sm.set_silence_state(False, True)
    fed = {"calls": 0}

    async def _spy(pcm):
        fed["calls"] += 1

    sm._feed_silence_kws = _spy
    await sm.feed_audio(PCM)
    assert fed["calls"] == 0


def test_live_silence_kws_engine_released_on_exit():
    sm = _build_live()
    sm.set_silence_state(True, True)
    kws = _FakeKWS(hit=False)
    sm._silence_kws = kws
    sm.set_silence_state(False, True)
    assert sm._silence_kws is None
    assert kws.stopped is True


# ---------------------------------------------------------------------------
# B2 — proxy fans the silence state out to live sessions
# ---------------------------------------------------------------------------
def test_propagate_silence_to_live_sessions():
    calls = []

    class _FakeSM:
        def set_silence_state(self, suppressed, kws_enabled):
            calls.append((suppressed, kws_enabled))

    class _FakeSession:
        state_machine = _FakeSM()

    class _FakeManager:
        def live_session_ids(self):
            return ["s1"]

        def get_live_session(self, sid):
            return _FakeSession()

    _propagate_silence_to_live_sessions({"jarvis_manager": _FakeManager()}, True, False)
    assert calls == [(True, False)]


def test_propagate_silence_no_manager_is_noop():
    _propagate_silence_to_live_sessions({}, True, True)  # must not raise


async def test_silence_post_propagates_to_live_sessions(tmp_path, monkeypatch):
    """A POST that enters silence fans the state out to live sessions (B2)."""
    import aiohttp

    calls = []

    class _FakeSM:
        def set_silence_state(self, suppressed, kws_enabled):
            calls.append((suppressed, kws_enabled))

    class _FakeSession:
        state_machine = _FakeSM()

    class _FakeManager:
        def live_session_ids(self):
            return ["s1"]

        def get_live_session(self, sid):
            return _FakeSession()

    # Fake webinfer echoes suppressed=True.
    fake_app = aiohttp.web.Application()
    fake_app["jarvis_manager"] = _FakeManager()

    async def handle_post(request):
        payload = await request.json()
        return aiohttp.web.json_response({"suppressed": payload.get("suppressed", False)})

    wb = aiohttp.web.Application()
    wb.router.add_post("/v1/live/silence", handle_post)
    wb_runner = aiohttp.web.AppRunner(wb)
    await wb_runner.setup()
    wb_site = aiohttp.web.TCPSite(wb_runner, "127.0.0.1", 0)
    await wb_site.start()
    wb_port = wb_site._server.sockets[0].getsockname()[1]

    monkeypatch.setattr(server, "_silence_base_url", lambda: f"http://127.0.0.1:{wb_port}")

    app = aiohttp.web.Application()
    app["jarvis_manager"] = _FakeManager()
    app.router.add_post("/api/live/silence", silence_proxy._silence_handler)
    runner = aiohttp.web.AppRunner(app)
    await runner.setup()
    site = aiohttp.web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.post(
                f"http://127.0.0.1:{port}/api/live/silence", json={"suppressed": True}
            ) as resp,
        ):
            assert resp.status == 200
        assert (True, True) in calls  # suppressed=True, kws_enabled default True
    finally:
        await runner.cleanup()
        await wb_runner.cleanup()
