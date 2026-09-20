"""QA independent supplement (webui): addressee Phase2 consumption edges.

Covers the team-lead QA brief edges that the engineer's own tests do not
contrast directly:

  * live_mode — decision="not-for-me" vs decision="silence" are BEHAVIORALLY
    equivalent (no TTS, empty broadcast, controller back to LISTENING) but
    the not-for-me path logs ``[addressee] semantic not-for-me``;
  * jarvis — receiving a ``not-for-me`` decision is an abnormal scenario
    (jarvis prompt is three-state); it must fail open without crashing and
    without speaking any real content.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

REPO = Path(__file__).resolve().parents[3]
WEBUI_SRC = REPO / "services" / "webui" / "src"
for _p in (str(REPO), str(WEBUI_SRC)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402

from joy_interaction_webui import live_mode as live_module  # noqa: E402
from joy_interaction_webui.jarvis_mode import JarvisConfig, JarvisStateMachine  # noqa: E402
from joy_interaction_webui.live_mode import LiveStateMachine  # noqa: E402
from joy_interaction_webui.turn_controller import (  # noqa: E402
    TurnConfig,
    TurnController,
    TurnState,
)
from joy_interaction_webui.turn_streaming import StreamingTurnResult  # noqa: E402

PCM = b"\x00\x00" * 100


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def time(self) -> float:
        return self.now

    def monotonic(self) -> float:
        return self.now

    def __call__(self) -> float:
        return self.now


class FakeVAD:
    available = True

    def __init__(self) -> None:
        self.speech = False

    def accept_waveform(self, samples) -> None:
        pass

    def is_speech(self) -> bool:
        return self.speech

    def set_speech(self, value: bool) -> None:
        self.speech = value


class FakeASR:
    def __init__(self, text: str = "") -> None:
        self.text = text

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def feed_chunk(self, pcm: bytes) -> str:
        return self.text

    def set_text(self, text: str) -> None:
        self.text = text


class FakeConsumer:
    def __init__(self, result: StreamingTurnResult, sentences=()) -> None:
        self.result = result
        self.sentences = list(sentences)
        self.consume_kwargs: dict = {}

    def __call__(self, **kwargs):
        self.consume_kwargs = dict(kwargs)
        return self

    async def consume(self, text, interaction_mode, reply_session):
        self.consume_kwargs.update(
            {
                "text": text,
                "interaction_mode": interaction_mode,
                "reply_session": reply_session,
            }
        )
        on_sentence = self.consume_kwargs.get("on_sentence")
        for seq, sentence in enumerate(self.sentences):
            if on_sentence is not None:
                on_sentence(sentence, seq, reply_session)
        return self.result


def _stub_config() -> JarvisConfig:
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
        vad_enabled=False,
    )


def build_live(*, controller=None, vad=None, asr=None, **overrides):
    vad = vad if vad is not None else FakeVAD()
    asr = asr if asr is not None else FakeASR()
    sm = LiveStateMachine(
        config=_stub_config(),
        session_id="s1",
        vad=vad,
        asr=asr,
        controller=controller,
        **overrides,
    )
    return sm, vad, asr


def _live_controller(clock=None, cooldown_ms=10) -> TurnController:
    cfg = TurnConfig.live()
    cfg.cooldown_ms = cooldown_ms
    return TurnController(cfg, clock=clock)


def _result(**kwargs) -> StreamingTurnResult:
    defaults = {
        "full_response": "",
        "decision": "response",
        "delegation_question": None,
        "cancelled": False,
        "reply_session": 0,
        "sentence_count": 0,
    }
    defaults.update(kwargs)
    return StreamingTurnResult(**defaults)


async def _drive_turn(sm, vad, asr, clock, text: str):
    """Run one full live utterance through VAD/ASR/commit."""
    vad.set_speech(True)
    await sm.feed_audio(PCM)
    clock.now += 0.5
    asr.set_text(text)
    await sm.feed_audio(PCM)
    vad.set_speech(False)
    clock.now += 2.0
    await sm.feed_audio(PCM)


# ---------------------------------------------------------------------------
# live_mode: not-for-me vs silence — equivalent behavior, distinct log
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_live_not_for_me_vs_silence_equivalent_but_distinct_log(monkeypatch, caplog):
    """not-for-me and silence both: zero TTS, empty broadcast, back to
    LISTENING — but only not-for-me logs ``[addressee] semantic not-for-me``."""

    async def run_case(decision: str) -> dict:
        clock = FakeClock(1000.0)
        sm, vad, asr = build_live(controller=_live_controller(clock))
        monkeypatch.setattr(live_module, "time", clock)
        pushes: list = []
        broadcasts: list = []
        sm.on_tts_sentence = lambda text, seq, audio_b64, session: pushes.append((seq, text))
        sm.on_llm_response = lambda text, source: broadcasts.append((text, source))
        fake = FakeConsumer(_result(full_response="", decision=decision))
        monkeypatch.setattr(live_module, "StreamingTurnConsumer", fake)
        # Same non-garbage utterance for both cases; the FakeConsumer drives
        # the decision, so only the decision label differs between the runs.
        await _drive_turn(sm, vad, asr, clock, "这关怎么这么难啊")
        return {
            "pushes": pushes,
            "broadcasts": broadcasts,
            "state": sm.turn_state,
            "consume_mode": fake.consume_kwargs.get("interaction_mode"),
        }

    # silence baseline first
    base = await run_case("silence")
    assert base["pushes"] == []
    assert base["broadcasts"] == [("", "live_text")]
    assert base["state"] == TurnState.LISTENING

    with caplog.at_level(logging.INFO, logger="joyai.live_mode"):
        nfm = await run_case("not-for-me")
    assert nfm["pushes"] == []
    assert nfm["broadcasts"] == [("", "live_text")]
    assert nfm["state"] == TurnState.LISTENING
    assert nfm["consume_mode"] == "live"
    assert "[addressee] semantic not-for-me" in caplog.text


# ---------------------------------------------------------------------------
# jarvis: abnormal not-for-me decision -> fail-open, no crash, no real speech
# ---------------------------------------------------------------------------


def test_jarvis_finish_turn_not_for_me_fail_open_no_crash():
    """jarvis receiving decision='not-for-me' (should never happen with the
    three-state prompt) must fail open: no exception, empty broadcast, and no
    real TTS content is synthesized (empty text is a no-op)."""
    sm = JarvisStateMachine(config=_stub_config())
    broadcasts: list = []
    sm.on_llm_response = lambda text, source: broadcasts.append((text, source))

    tts_texts: list = []

    async def fake_stream_tts(text: str) -> None:
        tts_texts.append(text)

    with patch.object(sm, "_stream_tts", new=fake_stream_tts):
        asyncio.run(
            sm._finish_llm_turn(
                text="这关怎么这么难啊",
                response="",
                decision="not-for-me",
                delegation_question=None,
                stream_tts=True,
            )
        )

    assert broadcasts == [("", "jarvis_voice")]
    # fail-open: the empty text reached the TTS path but synthesizes nothing
    # meaningful (no content). No crash is the acceptance criterion.
    assert tts_texts == [""]


def test_jarvis_finish_turn_not_for_me_stream_tts_false_no_task():
    """With stream_tts=False the not-for-me turn completes without any TTS."""
    sm = JarvisStateMachine(config=_stub_config())
    broadcasts: list = []
    sm.on_llm_response = lambda text, source: broadcasts.append((text, source))
    with patch.object(sm, "_stream_tts", new=AsyncMock()) as mock_tts:
        asyncio.run(
            sm._finish_llm_turn(
                text="对，我也觉得",
                response="",
                decision="not-for-me",
                delegation_question=None,
                stream_tts=False,
            )
        )
    mock_tts.assert_not_awaited()
    assert broadcasts == [("", "jarvis_text")]
