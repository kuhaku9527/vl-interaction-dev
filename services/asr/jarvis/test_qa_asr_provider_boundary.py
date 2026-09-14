# SPDX-License-Identifier: Apache-2.0

"""QA boundary tests for the unified ASR provider (independent regression).

These are NEW cases written by QA (not the engineer) attacking the edges of
``asr-provider-unified.md``:

* CloudBatchProvider failure modes: upstream 5xx / non-JSON body / timeout
  all surface as an explicit ``CloudASRError`` (D-080), never silent;
* buffer cap boundary: exactly-at / over-cap keeps the tail;
* ``finalize`` is consume-once (one POST, second call returns ``""`` — not
  idempotent, documented);
* cloud configured but upstream missing: factory yields an *unavailable*
  provider and the health probe reports ``ok=False`` explicitly;
* jarvis cloud wake promotes DIRECTLY (no WAIT_ASR_CONFIRM window can work
  without partials) — semantic change recorded in spec §3;
* failover (``JARVIS_ASR_ALLOW_LOCAL_FAILOVER=1``) swaps to a local provider
  with an explicit degraded marker (log + ``streaming`` flips True); without
  the opt-in the cloud provider stays and the buffer is reset.

Run standalone: python -m pytest services/asr/jarvis/test_qa_asr_provider_boundary.py -q
"""

from __future__ import annotations

import asyncio
import logging
import sys
from collections import deque
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

REPO = Path(__file__).resolve().parents[3]
WEBUI_SRC = REPO / "services" / "webui" / "src"
for _p in (str(REPO), str(WEBUI_SRC)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.asr.jarvis import asr_provider as ap  # noqa: E402

URL = "http://upstream/v1/audio/transcriptions"
SPEECH_PCM = b"\x00\x10" * 40  # int16 0x1000 -> peak 0.125 (>= cloud speech gate)
SILENT_PCM = b"\x00\x00" * 40


def _provider(**kw) -> ap.CloudBatchProvider:
    kw.setdefault("upstream_url", URL)
    return ap.CloudBatchProvider(**kw)


# ---------------------------------------------------------------------------
# CloudBatchProvider failure modes (D-080 explicit, never silent)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cloud_finalize_5xx_wrapped_explicit(httpx_mock):
    provider = _provider()
    provider.start()
    provider.feed_chunk(b"\x00\x00" * 100)
    httpx_mock.add_response(url=URL, method="POST", status_code=503)
    with pytest.raises(ap.CloudASRError, match="cloud provider unreachable"):
        await provider.finalize()


@pytest.mark.asyncio
async def test_cloud_finalize_non_json_response_wrapped_explicit(httpx_mock):
    provider = _provider()
    provider.start()
    provider.feed_chunk(b"\x00\x00" * 100)
    httpx_mock.add_response(
        url=URL, method="POST", status_code=200, content=b"<html>gateway error</html>"
    )
    with pytest.raises(ap.CloudASRError, match="cloud provider unreachable"):
        await provider.finalize()


@pytest.mark.asyncio
async def test_cloud_finalize_timeout_wrapped_explicit(httpx_mock):
    import httpx

    provider = _provider()
    provider.start()
    provider.feed_chunk(b"\x00\x00" * 100)
    httpx_mock.add_exception(httpx.ReadTimeout("timed out", request=None))
    with pytest.raises(ap.CloudASRError, match="cloud provider unreachable"):
        await provider.finalize()


@pytest.mark.asyncio
async def test_cloud_finalize_empty_buffer_makes_no_upstream_call(httpx_mock):
    """Empty buffer -> '' without touching the network."""
    provider = _provider()
    provider.start()
    assert await provider.finalize() == ""
    # pytest-httpx raises if any request was made; a bare finalize must not.
    assert len(httpx_mock.get_requests()) == 0


# ---------------------------------------------------------------------------
# Buffer cap boundary (exactly-at / over)
# ---------------------------------------------------------------------------


def test_cloud_buffer_cap_exact_boundary_keeps_all():
    # 0.1s cap @ 16 kHz = 3200 bytes. Feeding exactly 3200 bytes keeps it all.
    provider = _provider(max_buffer_seconds=0.1, sample_rate=16000)
    provider.start()
    provider.feed_chunk(b"\x00\x00" * 800)  # 1600 bytes
    provider.feed_chunk(b"\x00\x00" * 800)  # 1600 bytes -> 3200 exactly
    assert len(provider._pcm) == 3200
    assert provider._pcm == b"\x00\x00" * 1600


def test_cloud_buffer_cap_over_drops_head_keeps_tail():
    provider = _provider(max_buffer_seconds=0.1, sample_rate=16000)
    provider.start()
    # Feed 0.3s total in 3 chunks of 1600 bytes: cap keeps only the LAST 0.1s.
    provider.feed_chunk(b"\x01\x00" * 800)  # chunk 1 (1600 bytes)
    provider.feed_chunk(b"\x02\x00" * 800)  # chunk 2 (1600 bytes)
    provider.feed_chunk(b"\x03\x00" * 800)  # chunk 3 (1600 bytes)
    assert len(provider._pcm) == 3200
    assert provider._pcm == b"\x02\x00" * 800 + b"\x03\x00" * 800


def test_cloud_buffer_cap_keeps_byte_length_even_after_truncation():
    """Truncation must not leave an odd byte length (would break WAV encode)."""
    provider = _provider(max_buffer_seconds=0.05, sample_rate=16000)  # cap 1600 bytes
    provider.start()
    provider.feed_chunk(b"\x01\x00" * 1200 + b"\x02\x00" * 1200)  # 2400 bytes over cap
    assert len(provider._pcm) == 1600
    assert len(provider._pcm) % 2 == 0


# ---------------------------------------------------------------------------
# finalize consume-once semantics (one POST; second call empty)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cloud_finalize_consume_once_second_call_empty(monkeypatch, httpx_mock):
    provider = _provider()
    provider.start()
    provider.feed_chunk(b"\x00\x00" * 3200)
    httpx_mock.add_response(url=URL, method="POST", json={"text": "你好"}, status_code=200)
    assert await provider.finalize() == "你好"
    assert len(httpx_mock.get_requests()) == 1
    # Second finalize: buffer already consumed -> "" and NO second POST.
    assert await provider.finalize() == ""
    assert len(httpx_mock.get_requests()) == 1


@pytest.mark.asyncio
async def test_cloud_finalize_after_error_keeps_buffer_cleared(httpx_mock):
    """A failed finalize consumes the buffer too (next call returns '')."""
    provider = _provider()
    provider.start()
    provider.feed_chunk(b"\x00\x00" * 100)
    httpx_mock.add_response(url=URL, method="POST", status_code=500)
    with pytest.raises(ap.CloudASRError):
        await provider.finalize()
    assert provider._pcm == bytearray()
    assert provider._started is False


# ---------------------------------------------------------------------------
# Cloud configured but upstream missing -> unavailable + explicit probe false
# ---------------------------------------------------------------------------


def test_factory_cloud_without_upstream_yields_unavailable_provider(monkeypatch):
    monkeypatch.setenv("JARVIS_ASR_PROVIDER", "cloud")
    monkeypatch.delenv("ASR_UPSTREAM_URL", raising=False)
    monkeypatch.delenv("ASR_API_KEY", raising=False)
    provider = ap.create_asr_provider()
    assert isinstance(provider, ap.CloudBatchProvider)
    assert provider.available is False
    assert provider.upstream_url == ""


@pytest.mark.asyncio
async def test_cloud_without_upstream_finalize_still_explicit_error(monkeypatch):
    """No upstream URL: finalize raises CloudASRError, never returns junk."""
    provider = ap.CloudBatchProvider(upstream_url="")
    provider.start()
    provider.feed_chunk(b"\x00\x00" * 100)
    with pytest.raises(ap.CloudASRError, match="cloud provider unreachable"):
        await provider.finalize()


def test_probe_asr_cloud_no_upstream_reports_ok_false(monkeypatch):
    from joy_interaction_webui import service_probe

    monkeypatch.setenv("JARVIS_ASR_PROVIDER", "cloud")
    monkeypatch.delenv("ASR_UPSTREAM_URL", raising=False)
    result = service_probe._probe_asr({"api_base": ""})
    assert result["ok"] is False
    assert "no upstream configured" in result["reason"]


# ---------------------------------------------------------------------------
# jarvis cloud wake: direct promotion (no WAIT_ASR_CONFIRM window)
# ---------------------------------------------------------------------------


class _FakeKWS:
    def __init__(self, fires=True):
        self.calls = 0
        self.fires = fires

    def start(self):
        pass

    def feed_audio(self, pcm: bytes) -> bool:
        self.calls += 1
        return self.fires


class _FakeVAD:
    available = False


def _jarvis_sm():
    from joy_interaction_webui.jarvis_mode import JarvisConfig, JarvisState, JarvisStateMachine

    cfg = JarvisConfig()
    cfg.kws_capture_enabled = False  # keep _observe_kws_diagnostics IO-free
    sm = JarvisStateMachine.__new__(JarvisStateMachine)
    sm.config = cfg
    sm.state = JarvisState.KWS_LISTENING
    sm._vad = _FakeVAD()
    sm._kws = _FakeKWS()
    sm._audio_queue = asyncio.Queue(maxsize=8)
    sm._asr = None
    sm._asr_stream_active = False
    sm._current_asr_text = ""
    sm._last_speech_time = 0.0
    sm._last_asr_match = ""
    sm._confirm_task = None
    sm._tts_task = None
    sm._turn_delegate = None
    sm._turn_shadow = None
    sm._kws_shadow_asr_active = False
    sm._kws_shadow_last_text = ""
    sm._kws_shadow_last_log_at = 0.0
    sm._kws_shadow_last_speech_at = 0.0
    sm._last_kws_hit_at = 0.0
    sm._last_wake_peak = 0.0
    sm._last_wake_rms = 0.0
    sm._last_kws_fresh_probe_at = 0.0
    sm._kws_capture_chunks = deque()
    sm._kws_capture_bytes = 0
    sm._kws_capture_seq = 0
    sm._last_kws_capture_at = 0.0
    sm.on_wake = None
    sm.on_asr_partial = None
    sm.on_user_utterance = None
    sm.on_llm_response = None
    sm.on_goodbye = None
    sm.audio_output = None
    return JarvisState, sm


@pytest.mark.asyncio
async def test_jarvis_cloud_wake_promotes_directly_no_confirm_window(monkeypatch):
    """Cloud (streaming=False): KWS hit -> DIALOG_ACTIVE without WAIT_ASR_CONFIRM."""
    JarvisState, sm = _jarvis_sm()
    provider = _provider()
    sm._asr = provider
    wake_calls = []
    sm.on_wake = lambda: wake_calls.append(1)
    monkeypatch.setattr(sm, "_play_wake_wav", AsyncMock())
    monkeypatch.setattr(sm, "_drain_pending_audio", AsyncMock(return_value=0))

    assert sm.state == JarvisState.KWS_LISTENING
    await sm._handle_kws(SPEECH_PCM)

    # Direct promotion: WAKE_DETECTED then DIALOG_ACTIVE, never WAIT_ASR_CONFIRM.
    assert sm.state == JarvisState.DIALOG_ACTIVE
    assert wake_calls == [1]
    assert sm._confirm_task is None  # no ASR-confirm timeout scheduled
    assert sm._asr_stream_active is True
    assert sm._current_asr_text == ""
    assert sm._last_speech_time == 0.0  # endpoint waits for real audio activity
    # provider started a fresh session for the dialog
    assert provider._started is True


@pytest.mark.asyncio
async def test_jarvis_local_wake_still_uses_confirm_window():
    """Local (default): the existing WAIT_ASR_CONFIRM window is untouched."""
    JarvisState, sm = _jarvis_sm()

    class _LocalLike:
        streaming = True
        available = True

        def start(self):
            pass

        def stop(self):
            pass

        def feed_chunk(self, pcm):
            return ""

        last_text = ""

    sm._asr = _LocalLike()
    sm.on_wake = None
    # Patch the async helpers to avoid real IO in the local path.
    import joy_interaction_webui.jarvis_mode as jm

    original_play = jm.JarvisStateMachine._play_wake_wav
    original_confirm = jm.JarvisStateMachine._wait_asr_confirm_timeout
    jm.JarvisStateMachine._play_wake_wav = AsyncMock()
    jm.JarvisStateMachine._wait_asr_confirm_timeout = AsyncMock()
    try:
        await sm._handle_kws(SPEECH_PCM)
        assert sm.state == JarvisState.WAIT_ASR_CONFIRM
        assert sm._confirm_task is not None
    finally:
        jm.JarvisStateMachine._play_wake_wav = original_play
        jm.JarvisStateMachine._wait_asr_confirm_timeout = original_confirm


# ---------------------------------------------------------------------------
# Failover: opt-in swap to local + degraded marker; off keeps cloud explicit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_jarvis_failover_opt_in_swaps_to_local_with_degraded_marker(monkeypatch, caplog):
    """JARVIS_ASR_ALLOW_LOCAL_FAILOVER=1 -> local provider + explicit degraded log."""
    from joy_interaction_webui.jarvis_mode import JarvisState

    monkeypatch.setenv("JARVIS_ASR_ALLOW_LOCAL_FAILOVER", "1")
    _, sm = _jarvis_sm()
    sm.state = JarvisState.DIALOG_ACTIVE
    provider = _provider()
    sm._asr = provider
    sm._asr_stream_active = True

    class _FakeLocal:
        streaming = True
        available = True

        def __init__(self):
            self.started = False

        def start(self):
            self.started = True

        def stop(self):
            pass

        def feed_chunk(self, pcm):
            return "partial-local"

    fake_local = _FakeLocal()
    monkeypatch.setattr(ap, "LocalStreamingProvider", lambda **kw: fake_local)

    with caplog.at_level(logging.ERROR, logger="joyai.jarvis"):
        await sm._handle_cloud_asr_failure(RuntimeError("connection refused"))

    assert sm._asr is fake_local  # swapped
    assert fake_local.started is True
    # degraded marker: streaming flips to True (subsequent path is local)
    assert getattr(sm._asr, "streaming", True) is True
    assert sm._asr_stream_active is True
    assert any(
        "[asr] degrading to local streaming provider" in r.getMessage() for r in caplog.records
    )


@pytest.mark.asyncio
async def test_jarvis_failover_off_keeps_cloud_and_resets_buffer(monkeypatch, caplog):
    """Without the opt-in the cloud provider stays; buffer reset for retry."""
    from joy_interaction_webui.jarvis_mode import JarvisState

    monkeypatch.delenv("JARVIS_ASR_ALLOW_LOCAL_FAILOVER", raising=False)
    _, sm = _jarvis_sm()
    sm.state = JarvisState.DIALOG_ACTIVE
    provider = _provider()
    sm._asr = provider
    provider.start()
    provider.feed_chunk(b"\x00\x00" * 50)  # 100 bytes buffered
    assert len(provider._pcm) == 100

    with caplog.at_level(logging.ERROR, logger="joyai.jarvis"):
        await sm._handle_cloud_asr_failure(RuntimeError("connection refused"))

    assert sm._asr is provider  # no silent local fallback
    assert provider._pcm == bytearray()  # buffer reset -> next utterance retries
    assert provider._started is True  # provider restarted
    assert sm._last_speech_time == 0.0
    assert any(
        "[asr] cloud provider unreachable" in r.getMessage() for r in caplog.records
    )


# ---------------------------------------------------------------------------
# live failover parity (degraded marker + explicit error)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_live_failover_opt_in_swaps_to_local(monkeypatch, caplog):
    from types import SimpleNamespace

    from joy_interaction_webui import live_mode as lm
    from joy_interaction_webui.turn_controller import TurnController

    class _FakeVAD:
        available = False

        def accept_waveform(self, samples):
            pass

        def is_speech(self):
            return True

    ctrl = TurnController(lm.TurnConfig.live(), clock=None)
    sm = lm.LiveStateMachine.__new__(lm.LiveStateMachine)
    sm._config = SimpleNamespace(asr_model_dir="/models/x", asr_num_threads=2)
    sm._ctrl = ctrl
    sm._vad = _FakeVAD()
    sm._asr = None
    sm._current_asr_text = ""
    sm._last_speech_time = 0.0
    sm._cloud_prev_speech = False
    sm._endpoint_timeout_s = 2.0

    monkeypatch.setenv("JARVIS_ASR_ALLOW_LOCAL_FAILOVER", "1")
    provider = _provider()
    provider.start()
    provider.feed_chunk(b"\x00\x00" * 50)  # non-empty buffer -> real finalize call
    sm._asr = provider

    async def boom(wav_bytes, **kwargs):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(ap, "transcribe_wav_bytes", boom)

    class _FakeLocal:
        streaming = True

        def start(self):
            pass

    fake_local = _FakeLocal()
    monkeypatch.setattr(ap, "LocalStreamingProvider", lambda **kw: fake_local)

    with caplog.at_level(logging.ERROR, logger="joyai.live_mode"):
        await sm._handle_cloud_endpoint_commit(1002.5)

    assert sm._asr is fake_local
    assert getattr(sm._asr, "streaming", True) is True
    assert any(
        "[asr] degrading to local streaming provider" in r.getMessage() for r in caplog.records
    )
