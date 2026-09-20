"""QA regression: TTS facade delegation equivalence (commit e05fcb1).

Independent verification (QA 严过关) that the thin ``_spawn_sentence_tts`` /
``_synthesize_tts_sentence`` / ``_fetch_tts_pcm`` / ``_wrap_pcm16_wav``
facades on JarvisStateMachine / LiveStateMachine still behave identically to
the pre-extraction inline methods:

  * ``patch.object(sm, "_fetch_tts_pcm")`` still intercepts (facade passes
    ``self._fetch_tts_pcm`` at delegation time);
  * the epoch guard still drops in-flight audio when the epoch is bumped
    mid-fetch (barge-in / exit word semantics);
  * the tts_sentence callback is still wired through;
  * the facade ``_wrap_pcm16_wav`` produces byte-identical WAV output to
    ``tts_turn_common.wrap_pcm16_wav``;
  * live's mode-specific pre-step ``_sentence_spawned_this_turn`` still runs.

Run: python -m pytest tests/test_qa_regression_tts_facade.py -q
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

REPO = Path(__file__).resolve().parents[2]
WEBUI_SRC = REPO / "services" / "webui" / "src"
for _p in (str(REPO), str(WEBUI_SRC)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from joy_interaction_webui import tts_turn_common  # noqa: E402
from joy_interaction_webui.jarvis_mode import JarvisConfig, JarvisStateMachine  # noqa: E402
from joy_interaction_webui.live_mode import LiveStateMachine  # noqa: E402


def _jarvis_config(**overrides) -> JarvisConfig:
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
        **overrides,
    )


def _jarvis_sm() -> JarvisStateMachine:
    return JarvisStateMachine(config=_jarvis_config())


def _live_sm() -> LiveStateMachine:
    return LiveStateMachine(config=_jarvis_config(), session_id="s1")


@pytest.mark.asyncio
async def test_jarvis_facade_fetch_patch_intercepted_and_push_wired():
    """patch.object(sm, "_fetch_tts_pcm") is still effective after delegation;
    a valid sentence flows: fetch -> wrap -> tts_sentence callback."""
    sm = _jarvis_sm()
    pushed: list = []
    sm.on_tts_sentence = lambda text, seq, audio_b64, session: pushed.append(
        (seq, text, audio_b64, session)
    )
    with patch.object(sm, "_fetch_tts_pcm", new=AsyncMock(return_value=b"\x00\x00" * 100)):
        sm._spawn_sentence_tts("hello there", 0, 7)
        await asyncio.gather(*list(sm._tts_sentence_tasks), return_exceptions=True)
    assert len(pushed) == 1
    seq, text, audio_b64, session = pushed[0]
    assert (seq, text, session) == (0, "hello there", 7)
    assert audio_b64  # WAV base64 produced
    # WAV header sanity: RIFF....WAVE
    import base64 as _b64

    wav = _b64.b64decode(audio_b64)
    assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE"


@pytest.mark.asyncio
async def test_jarvis_facade_epoch_bump_drops_inflight_audio():
    """Epoch bumped while fetch is in flight -> result dropped (no push)."""
    sm = _jarvis_sm()
    pushed: list = []
    sm.on_tts_sentence = lambda *a: pushed.append(a)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def controlled_fetch(text: str) -> bytes:
        entered.set()
        await release.wait()
        return b"\x00\x00" * 100

    with patch.object(sm, "_fetch_tts_pcm", new=controlled_fetch):
        sm._spawn_sentence_tts("stale audio", 3, 9)
        await asyncio.wait_for(entered.wait(), timeout=2)  # task inside fetch
        sm._tts_sentence_epoch += 1  # barge-in / exit word
        release.set()
        await asyncio.gather(*list(sm._tts_sentence_tasks), return_exceptions=True)
    assert pushed == []


@pytest.mark.asyncio
async def test_live_facade_epoch_bump_drops_inflight_audio_and_sets_flag():
    """Same epoch-guard semantics on live; mode-specific pre-step preserved."""
    sm = _live_sm()
    pushed: list = []
    sm.on_tts_sentence = lambda *a: pushed.append(a)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def controlled_fetch(text: str) -> bytes:
        entered.set()
        await release.wait()
        return b"\x00\x00" * 100

    with patch.object(sm, "_fetch_tts_pcm", new=controlled_fetch):
        sm._spawn_sentence_tts("live stale", 1, 2)
        assert sm._sentence_spawned_this_turn is True  # live pre-step intact
        await asyncio.wait_for(entered.wait(), timeout=2)
        sm._tts_sentence_epoch += 1
        release.set()
        await asyncio.gather(*list(sm._tts_sentence_tasks), return_exceptions=True)
    assert pushed == []


@pytest.mark.asyncio
async def test_live_facade_fetch_patch_intercepted_and_push_wired():
    sm = _live_sm()
    pushed: list = []
    sm.on_tts_sentence = lambda text, seq, audio_b64, session: pushed.append(
        (seq, text, audio_b64, session)
    )
    with patch.object(sm, "_fetch_tts_pcm", new=AsyncMock(return_value=b"\x00\x00" * 64)):
        sm._spawn_sentence_tts("live hello", 0, 5)
        await asyncio.gather(*list(sm._tts_sentence_tasks), return_exceptions=True)
    assert len(pushed) == 1
    assert (pushed[0][0], pushed[0][1], pushed[0][3]) == (0, "live hello", 5)


def test_jarvis_wrap_facade_matches_shared():
    pcm = bytes(range(256)) * 8
    assert JarvisStateMachine._wrap_pcm16_wav(pcm, sample_rate=24000) == (
        tts_turn_common.wrap_pcm16_wav(pcm, sample_rate=24000)
    )


def test_live_wrap_facade_matches_shared():
    pcm = b"\x00\x00" * 512
    assert LiveStateMachine._wrap_pcm16_wav(pcm, sample_rate=24000) == (
        tts_turn_common.wrap_pcm16_wav(pcm, sample_rate=24000)
    )
