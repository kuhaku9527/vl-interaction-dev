# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the unified ASR provider abstraction (spec §2).

Covers:

* :class:`LocalStreamingProvider` — delegates 1:1 to the wrapped engine
  (behavior byte-for-byte identical to using ``JarvisASR`` directly);
* :class:`CloudBatchProvider` — accumulates PCM, one upstream POST on
  ``finalize``, auth header / model form field, explicit ``CloudASRError``
  (D-080) on upstream failure;
* the provider factory (``JARVIS_ASR_PROVIDER`` local/cloud/invalid) and the
  D-080 failover gate;
* the shared upstream client multipart contract;
* ``service_probe._probe_asr`` cloud branch reachability.

Run: python -m pytest services/asr/jarvis/test_asr_provider.py -q
"""

from __future__ import annotations

import sys
import wave
from io import BytesIO
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
WEBUI_SRC = REPO / "services" / "webui" / "src"
for _p in (str(REPO), str(WEBUI_SRC)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from unittest.mock import patch  # noqa: E402

import pytest  # noqa: E402

from services.asr.jarvis import asr_provider as ap  # noqa: E402
from services.asr.jarvis.asr_upstream import (  # noqa: E402
    pcm16_to_wav_bytes,
    transcribe_wav_bytes,
)


class _FakeEngine:
    """JarvisASR-shaped fake: records calls and holds ``last_text``."""

    def __init__(self) -> None:
        self.last_text = ""
        self.start_calls = 0
        self.stop_calls = 0
        self.fed = []

    def start(self) -> None:
        self.start_calls += 1
        self.last_text = ""

    def stop(self) -> None:
        self.stop_calls += 1

    def feed_chunk(self, pcm: bytes) -> str:
        self.fed.append(pcm)
        return "partial"


def _silent_wav_bytes(duration_ms: int = 100, sample_rate: int = 16000) -> bytes:
    n_samples = int(sample_rate * duration_ms / 1000)
    pcm = b"\x00\x00" * n_samples
    buf = BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# LocalStreamingProvider
# ---------------------------------------------------------------------------


def test_local_provider_delegates_to_wrapped_engine():
    engine = _FakeEngine()
    provider = ap.LocalStreamingProvider(asr=engine)
    assert provider.streaming is True
    assert provider.available is True

    provider.start()
    assert engine.start_calls == 1

    assert provider.feed_chunk(b"\x00\x00") == "partial"
    assert engine.fed == [b"\x00\x00"]

    engine.last_text = "final text"
    assert provider.stop() == "final text"
    assert engine.stop_calls == 1

    provider.reset()
    assert engine.start_calls == 2


def test_local_provider_async_finalize_returns_last_text():
    engine = _FakeEngine()
    provider = ap.LocalStreamingProvider(asr=engine)
    engine.last_text = "你好"
    assert provider.finalize().__await__() is not None
    # finalize delegates to stop(); assert via asyncio.run in the async test
    # below — this sync path just confirms the method exists and is awaitable.


@pytest.mark.asyncio
async def test_local_provider_finalize_awaitable():
    engine = _FakeEngine()
    provider = ap.LocalStreamingProvider(asr=engine)
    engine.last_text = "你好"
    assert await provider.finalize() == "你好"


def test_local_provider_builds_real_engine_without_injection(monkeypatch):
    # Patch the lazily-imported .asr module so sherpa-onnx is never loaded.
    import types

    fake_asr_module = types.ModuleType("services.asr.jarvis.asr")

    class _FakeJarvisASR:
        def __init__(self, model_dir, num_threads):
            self.model_dir = model_dir
            self.num_threads = num_threads

        def start(self):
            pass

        def stop(self):
            pass

        def feed_chunk(self, pcm):
            return ""

        last_text = ""

    fake_asr_module.JarvisASR = _FakeJarvisASR
    monkeypatch.setitem(sys.modules, "services.asr.jarvis.asr", fake_asr_module)
    provider = ap.LocalStreamingProvider(model_dir="/models/x", num_threads=4)
    assert provider._asr.model_dir == "/models/x"
    assert provider._asr.num_threads == 4


# ---------------------------------------------------------------------------
# CloudBatchProvider
# ---------------------------------------------------------------------------


def test_cloud_provider_accumulates_and_reports_no_partials():
    provider = ap.CloudBatchProvider(upstream_url="http://upstream/v1/audio/transcriptions")
    assert provider.streaming is False
    assert provider.available is True

    provider.start()
    assert provider.feed_chunk(b"\x00\x00" * 1600) == ""
    assert provider.feed_chunk(b"\x00\x00" * 1600) == ""
    assert len(provider._pcm) == 6400
    provider.reset()
    assert provider._pcm == bytearray()


def test_cloud_provider_available_requires_upstream():
    assert ap.CloudBatchProvider(upstream_url="").available is False
    assert ap.CloudBatchProvider(upstream_url="  ").available is False
    assert ap.CloudBatchProvider(upstream_url="http://x").available is True


def test_cloud_provider_stop_drops_buffer():
    provider = ap.CloudBatchProvider(upstream_url="http://x")
    provider.start()
    provider.feed_chunk(b"\x00\x00" * 100)
    assert provider.stop() == ""
    assert provider._pcm == bytearray()


@pytest.mark.asyncio
async def test_cloud_provider_finalize_posts_once_and_returns_final(monkeypatch):
    provider = ap.CloudBatchProvider(
        upstream_url="http://upstream/v1/audio/transcriptions",
        api_key="sk-test",
        model="FunAudioLLM/SenseVoiceSmall",
    )
    provider.start()
    provider.feed_chunk(b"\x00\x00" * 3200)

    calls: list = []

    async def fake_transcribe(wav_bytes, *, upstream_url, api_key="", model=None, timeout=120.0):
        calls.append((wav_bytes, upstream_url, api_key, model, timeout))
        return " 你好 "

    monkeypatch.setattr(ap, "transcribe_wav_bytes", fake_transcribe)
    assert await provider.finalize() == " 你好 "
    assert len(calls) == 1
    wav, url, key, model, timeout = calls[0]
    assert url == "http://upstream/v1/audio/transcriptions"
    assert key == "sk-test"
    assert model == "FunAudioLLM/SenseVoiceSmall"
    assert timeout == 120.0
    # WAV container: RIFF/WAVE header + 6400 bytes of PCM (16 kHz mono int16)
    assert wav[:4] == b"RIFF"
    assert wav[8:12] == b"WAVE"
    assert len(wav) == 44 + 6400
    # buffer cleared after finalize
    assert provider._pcm == bytearray()


@pytest.mark.asyncio
async def test_cloud_provider_finalize_empty_buffer_returns_empty(monkeypatch):
    provider = ap.CloudBatchProvider(upstream_url="http://upstream")
    provider.start()
    assert await provider.finalize() == ""


@pytest.mark.asyncio
async def test_cloud_provider_finalize_error_wrapped_explicit(monkeypatch):
    provider = ap.CloudBatchProvider(upstream_url="http://upstream", model=None)
    provider.start()
    provider.feed_chunk(b"\x00\x00" * 100)

    async def boom(wav_bytes, **kwargs):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(ap, "transcribe_wav_bytes", boom)
    with pytest.raises(ap.CloudASRError, match="cloud provider unreachable"):
        await provider.finalize()


@pytest.mark.asyncio
async def test_cloud_provider_finalize_odd_pcm_rejected():
    provider = ap.CloudBatchProvider(upstream_url="http://upstream")
    provider.start()
    provider.feed_chunk(b"\x00")
    with pytest.raises(ap.CloudASRError, match="byte length must be even"):
        await provider.finalize()


@pytest.mark.asyncio
async def test_cloud_provider_buffer_capped_at_max_seconds():
    provider = ap.CloudBatchProvider(
        upstream_url="http://upstream", max_buffer_seconds=0.1, sample_rate=16000
    )
    provider.start()
    # 0.2s of audio (6400 bytes) exceeds the 0.1s cap (3200 bytes); the head
    # is dropped, keeping only the tail.
    provider.feed_chunk(b"\x00\x00" * 6400)
    assert len(provider._pcm) == 3200


# ---------------------------------------------------------------------------
# Shared upstream client (OpenAI /v1/audio/transcriptions contract)
# ---------------------------------------------------------------------------


def test_pcm16_to_wav_bytes_builds_mono_wav():
    pcm = b"\x00\x00" * 1600  # 100ms @ 16 kHz
    wav = pcm16_to_wav_bytes(pcm, 16000)
    assert wav[:4] == b"RIFF"
    assert wav[8:12] == b"WAVE"
    with wave.open(BytesIO(wav), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        assert wf.getframerate() == 16000
        assert wf.readframes(wf.getnframes()) == pcm


@pytest.mark.anyio
async def test_transcribe_wav_bytes_sends_bearer_and_model(httpx_mock):
    url = "https://api.siliconflow.cn/v1/audio/transcriptions"
    httpx_mock.add_response(url=url, method="POST", json={"text": "你好"}, status_code=200)
    text = await transcribe_wav_bytes(
        _silent_wav_bytes(),
        upstream_url=url,
        api_key="sk-test",
        model="FunAudioLLM/SenseVoiceSmall",
    )
    assert text == "你好"
    request = httpx_mock.get_request()
    assert request.headers["Authorization"] == "Bearer sk-test"
    assert b'name="model"' in request.content
    assert b'name="file"' in request.content


@pytest.mark.anyio
async def test_transcribe_wav_bytes_omits_auth_when_no_key(httpx_mock):
    url = "http://127.0.0.1:8993/v1/audio/transcriptions"
    httpx_mock.add_response(url=url, method="POST", json={"text": "hello"}, status_code=200)
    text = await transcribe_wav_bytes(_silent_wav_bytes(), upstream_url=url)
    assert text == "hello"
    request = httpx_mock.get_request()
    assert "Authorization" not in request.headers


@pytest.mark.anyio
async def test_transcribe_wav_bytes_chat_completion_shape(httpx_mock):
    url = "http://upstream/v1/audio/transcriptions"
    httpx_mock.add_response(
        url=url,
        method="POST",
        json={"choices": [{"message": {"content": "via chat"}}]},
        status_code=200,
    )
    text = await transcribe_wav_bytes(_silent_wav_bytes(), upstream_url=url, model=None)
    assert text == "via chat"
    request = httpx_mock.get_request()
    assert b'name="model"' not in request.content  # model=None omits the field


@pytest.mark.anyio
async def test_transcribe_wav_bytes_upstream_error_raises(httpx_mock):
    import httpx

    url = "http://upstream/v1/audio/transcriptions"
    httpx_mock.add_response(url=url, method="POST", status_code=503)
    with pytest.raises(httpx.HTTPStatusError):
        await transcribe_wav_bytes(_silent_wav_bytes(), upstream_url=url)


# ---------------------------------------------------------------------------
# Provider factory (env gate)
# ---------------------------------------------------------------------------


def test_factory_defaults_to_local(monkeypatch):
    monkeypatch.delenv("JARVIS_ASR_PROVIDER", raising=False)
    # Patch the local provider so no sherpa-onnx model load is needed.
    monkeypatch.setattr(ap, "LocalStreamingProvider", lambda **kw: "local-provider")
    provider = ap.create_asr_provider(
        provider=None,
        model_dir="/models/x",
        num_threads=3,
    )
    assert provider == "local-provider"


def test_factory_local_explicit(monkeypatch):
    monkeypatch.setenv("JARVIS_ASR_PROVIDER", "local")
    monkeypatch.setattr(ap, "LocalStreamingProvider", lambda **kw: "local-provider")
    provider = ap.create_asr_provider()
    assert provider == "local-provider"


def test_factory_cloud_reads_env(monkeypatch):
    monkeypatch.setenv("JARVIS_ASR_PROVIDER", "cloud")
    monkeypatch.setenv("ASR_UPSTREAM_URL", "https://api.siliconflow.cn/v1/audio/transcriptions")
    monkeypatch.setenv("ASR_API_KEY", "sk-env")
    monkeypatch.setenv("ASR_MODEL", "env-model")
    provider = ap.create_asr_provider()
    assert isinstance(provider, ap.CloudBatchProvider)
    assert provider.upstream_url == "https://api.siliconflow.cn/v1/audio/transcriptions"
    assert provider.api_key == "sk-env"
    assert provider.model == "env-model"


def test_factory_cloud_defaults_model_when_env_unset(monkeypatch):
    monkeypatch.setenv("JARVIS_ASR_PROVIDER", "cloud")
    monkeypatch.setenv("ASR_UPSTREAM_URL", "http://u")
    monkeypatch.delenv("ASR_MODEL", raising=False)
    provider = ap.create_asr_provider()
    assert provider.model == ap.DEFAULT_CLOUD_MODEL


def test_factory_cloud_explicit_overrides_env(monkeypatch):
    monkeypatch.setenv("JARVIS_ASR_PROVIDER", "cloud")
    monkeypatch.setenv("ASR_UPSTREAM_URL", "http://env-url")
    monkeypatch.setenv("ASR_API_KEY", "sk-env")
    provider = ap.create_asr_provider(
        provider="cloud", upstream_url="http://explicit", api_key="sk-explicit", model="m"
    )
    assert provider.upstream_url == "http://explicit"
    assert provider.api_key == "sk-explicit"
    assert provider.model == "m"


def test_factory_invalid_raises(monkeypatch):
    monkeypatch.setenv("JARVIS_ASR_PROVIDER", "bogus")
    with pytest.raises(ValueError, match="invalid JARVIS_ASR_PROVIDER"):
        ap.create_asr_provider()


def test_allow_local_failover_gate(monkeypatch):
    monkeypatch.delenv("JARVIS_ASR_ALLOW_LOCAL_FAILOVER", raising=False)
    assert ap.allow_local_failover() is False
    monkeypatch.setenv("JARVIS_ASR_ALLOW_LOCAL_FAILOVER", "1")
    assert ap.allow_local_failover() is True
    monkeypatch.setenv("JARVIS_ASR_ALLOW_LOCAL_FAILOVER", "0")
    assert ap.allow_local_failover() is False


# ---------------------------------------------------------------------------
# service_probe._probe_asr cloud branch (spec §4)
# ---------------------------------------------------------------------------


def test_probe_asr_cloud_probes_upstream_reachable(monkeypatch):
    from joy_interaction_webui import service_probe

    monkeypatch.setenv("JARVIS_ASR_PROVIDER", "cloud")
    monkeypatch.setenv("ASR_UPSTREAM_URL", "https://api.siliconflow.cn/v1/audio/transcriptions")

    class _FakeResp:
        status_code = 404  # OpenAI transcriptions route answers 404 to GET

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get(self, url):
            return _FakeResp()

    with patch("httpx.Client", _FakeClient):
        result = service_probe._probe_asr({"api_base": ""})
    assert result["ok"] is True
    assert result["code"] == 404
    assert result["upstream"] == "https://api.siliconflow.cn/v1/audio/transcriptions"


def test_probe_asr_cloud_unreachable(monkeypatch):
    from joy_interaction_webui import service_probe

    monkeypatch.setenv("JARVIS_ASR_PROVIDER", "cloud")
    monkeypatch.setenv("ASR_UPSTREAM_URL", "http://127.0.0.1:1/v1/audio/transcriptions")

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get(self, url):
            raise OSError("connection refused")

    with patch("httpx.Client", _FakeClient):
        result = service_probe._probe_asr({"api_base": ""})
    assert result["ok"] is False
    assert "connection refused" in result["reason"]


def test_probe_asr_cloud_missing_upstream(monkeypatch):
    from joy_interaction_webui import service_probe

    monkeypatch.setenv("JARVIS_ASR_PROVIDER", "cloud")
    monkeypatch.delenv("ASR_UPSTREAM_URL", raising=False)
    result = service_probe._probe_asr({"api_base": ""})
    assert result["ok"] is False
    assert "no upstream configured" in result["reason"]


def test_probe_asr_local_branch_unchanged(monkeypatch):
    """Default (local) provider keeps the pre-split probe behavior."""
    from joy_interaction_webui import service_probe

    monkeypatch.delenv("JARVIS_ASR_PROVIDER", raising=False)
    # empty api_base -> local in-process paraformer is a valid state
    result = service_probe._probe_asr({"api_base": ""})
    assert result == {"ok": True, "note": "local in-process paraformer"}


def test_probe_asr_cloud_via_bridge_when_api_base_http(monkeypatch):
    """Cloud provider + WebUI http(s) slot probes the internal bridge first."""
    from joy_interaction_webui import service_probe

    monkeypatch.setenv("JARVIS_ASR_PROVIDER", "cloud")

    class _FakeResp:
        def __init__(self, code):
            self.status_code = code

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get(self, url):
            if url.endswith("/health"):
                return _FakeResp(200)
            return _FakeResp(404)

    with (
        patch("httpx.Client", _FakeClient),
        patch("joy_interaction_webui.server.ASR_BRIDGE_HTTP", "http://127.0.0.1:8994"),
    ):
        result = service_probe._probe_asr({"api_base": "https://api.siliconflow.cn/v1"})
    assert result["ok"] is True
    assert result["upstream"] == "https://api.siliconflow.cn/v1"
