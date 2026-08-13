# -*- coding: utf-8 -*-
"""QA batch-6 edge tests (independent regression additions).

Targets the four moved-pure-logic modules' boundary semantics:
  1. commit_verdict short-circuit order (garbage first; noise never invokes Smart Turn)
  2. probe_fresh_window energy gate boundary (kws_probe_min_peak=0.005)
  3. exit_word_detected consistency with EXIT_WORDS
  4. send_to_llm_streaming cancelled branch: no broadcast, seq still advances
"""
import asyncio
from collections import deque
from types import SimpleNamespace

import pytest

from joy_interaction_webui import jarvis_dialog, jarvis_kws, jarvis_llm
from joy_interaction_webui.jarvis_config import EXIT_WORDS, JarvisConfig
from joy_interaction_webui.jarvis_state import JarvisState


# ---------------------------------------------------------------------------
# 1. commit_verdict short-circuit order
# ---------------------------------------------------------------------------
class TestCommitVerdictShortCircuit:
    def test_garbage_first_skips_smart_turn(self):
        calls = []

        def garbage(text):
            calls.append("garbage")
            return True

        def smart(text):
            calls.append("smart")
            return True

        verdict = jarvis_dialog.commit_verdict("..", garbage, smart)
        assert verdict == "garbage"
        # smart_turn must NOT be invoked for garbage text
        assert calls == ["garbage"], calls

    def test_noise_never_triggers_smart_turn(self):
        # even when Smart Turn would allow send, garbage wins
        calls = []

        def garbage(text):
            calls.append("garbage")
            return True

        def smart(text):
            calls.append("smart")
            return True

        assert jarvis_dialog.commit_verdict("\ufffd\ufffd", garbage, smart) == "garbage"
        assert calls == ["garbage"]

    def test_deferred_when_not_garbage_but_smart_turn_declines(self):
        calls = []

        def garbage(text):
            calls.append("garbage")
            return False

        def smart(text):
            calls.append("smart")
            return False

        assert jarvis_dialog.commit_verdict("嗯……那个", garbage, smart) == "deferred"
        assert calls == ["garbage", "smart"]

    def test_sent_when_all_clear(self):
        assert jarvis_dialog.commit_verdict(
            "帮我查一下天气",
            lambda t: False,
            lambda t: True,
        ) == "sent"


# ---------------------------------------------------------------------------
# 2. probe_fresh_window energy gate boundary (0.005)
# ---------------------------------------------------------------------------
def _pcm_with_peak(target_peak: float, n_samples: int = 1600) -> bytes:
    """Build int16 PCM whose max sample is within one LSB of target_peak."""
    import array

    amp = int(round(target_peak * 32768.0))
    samples = array.array("h", [0] * n_samples)
    samples[0] = amp
    return samples.tobytes()


class _NoHitKWS:
    """detect_in_pcm returns False; counts invocations."""

    def __init__(self):
        self.calls = 0

    def detect_in_pcm(self, pcm):
        self.calls += 1
        return False


class _HitKWS(_NoHitKWS):
    def detect_in_pcm(self, pcm):
        self.calls += 1
        return True


def _probe_cfg():
    cfg = JarvisConfig()
    cfg.kws_fresh_window_probe_enabled = True
    cfg.kws_fresh_window_probe_interval_s = 0.0
    cfg.kws_fresh_window_min_s = 0.1
    cfg.sample_rate = 16000
    cfg.kws_probe_min_peak = 0.005
    cfg.kws_fresh_window_direct_wake = True
    return cfg


class TestProbeEnergyGate:
    async def _probe(self, cfg, pcm, kws, direct_wake=None):
        return await jarvis_kws.probe_fresh_window(
            config=cfg,
            last_probe_at=0.0,
            capture_bytes=len(pcm),
            capture_chunks=deque([pcm]),
            pcm_stats_fn=jarvis_kws.pcm_stats,
            kws=kws,
            direct_wake=direct_wake or (lambda **kw: None),
            peak=0.0,
            rms=0.0,
            bypass_min_s=True,
            logger=__import__("logging").getLogger("joyai.jarvis"),
        )

    def test_peak_just_below_gate_is_skipped(self):
        cfg = _probe_cfg()
        kws = _NoHitKWS()
        # peak = 163/32768 = 0.004974... < 0.005 -> silent, skipped
        pcm = _pcm_with_peak(0.0049)
        hit, ts = asyncio.run(self._probe(cfg, pcm, kws))
        assert hit is False
        assert kws.calls == 0, "detect_in_pcm must not be called for silent buffer"
        assert ts > 0, "probe timestamp still advances (baseline behavior)"

    def test_peak_at_exactly_gate_passes_to_kws(self):
        cfg = _probe_cfg()
        kws = _NoHitKWS()
        # peak = 164/32768 = 0.0050048... >= 0.005 -> probe proceeds
        pcm = _pcm_with_peak(0.005)
        hit, ts = asyncio.run(self._probe(cfg, pcm, kws))
        assert kws.calls == 1, "energy gate passes; detect_in_pcm called"
        assert hit is False  # no hit from KWS

    def test_peak_above_gate_with_hit_direct_wakes(self):
        cfg = _probe_cfg()
        kws = _HitKWS()
        woke = []
        pcm = _pcm_with_peak(0.02)

        async def direct_wake(**kw):
            woke.append(kw)

        hit, ts = asyncio.run(self._probe(cfg, pcm, kws, direct_wake=direct_wake))
        assert hit is True
        assert woke == [{"source": "fresh-window-kws"}]
        assert kws.calls == 1

    def test_direct_wake_flag_false_suppresses_wake(self):
        cfg = _probe_cfg()
        cfg.kws_fresh_window_direct_wake = False
        kws = _HitKWS()
        woke = []
        pcm = _pcm_with_peak(0.02)

        async def direct_wake(**kw):
            woke.append(kw)

        hit, ts = asyncio.run(self._probe(cfg, pcm, kws, direct_wake=direct_wake))
        assert hit is False
        assert woke == [], "direct-wake flag off must suppress wake"
        assert kws.calls == 1

    def test_disabled_probe_returns_false_without_touching_kws(self):
        cfg = _probe_cfg()
        cfg.kws_fresh_window_probe_enabled = False
        kws = _NoHitKWS()
        pcm = _pcm_with_peak(0.02)
        hit, ts = asyncio.run(self._probe(cfg, pcm, kws))
        assert hit is False
        assert kws.calls == 0


# ---------------------------------------------------------------------------
# 3. exit_word_detected consistency with EXIT_WORDS
# ---------------------------------------------------------------------------
class TestExitWordDetected:
    def test_every_exit_word_detected_in_any_case(self):
        for w in EXIT_WORDS:
            assert jarvis_dialog.exit_word_detected(w) is True, w
            assert jarvis_dialog.exit_word_detected(w.upper()) is True, w.upper()
            assert jarvis_dialog.exit_word_detected("  " + w + "  ") is True, "stripped " + w

    def test_exit_word_embedded_in_sentence(self):
        # ends-with semantics: word at end of sentence is detected
        assert jarvis_dialog.exit_word_detected("好的") is True
        assert jarvis_dialog.exit_word_detected("那我们就这样，好的") is True
        # endswith is suffix-based, NOT substring: "明白了" does not end with "明白"
        assert jarvis_dialog.exit_word_detected("明白") is True
        assert jarvis_dialog.exit_word_detected("明白了") is False
        assert jarvis_dialog.exit_word_detected("好的吧") is False  # "吧" suffix breaks exact endswith
        assert jarvis_dialog.exit_word_detected("好的，那就这样") is False  # word not at end

    def test_non_exit_word_not_detected(self):
        assert jarvis_dialog.exit_word_detected("帮我写个邮件") is False
        assert jarvis_dialog.exit_word_detected("what is the weather") is False
        assert jarvis_dialog.exit_word_detected("") is False

    def test_exit_words_set_matches_module_constant(self):
        # EXIT_WORDS must be the same object referenced by the facade
        from joy_interaction_webui import jarvis_mode

        assert jarvis_mode.EXIT_WORDS is EXIT_WORDS


# ---------------------------------------------------------------------------
# 4. send_to_llm_streaming cancelled branch: no broadcast, seq still advances
# ---------------------------------------------------------------------------
class _FakeResult:
    def __init__(self, *, cancelled=False, needs_retry=False, full="", decision="response"):
        self.cancelled = cancelled
        self.needs_non_streaming_retry = needs_retry
        self.full_response = full
        self.decision = decision
        self.delegation_question = None


class _FakeConsumer:
    def __init__(self, result, **kwargs):
        self.result = result
        self.kwargs = kwargs

    async def consume(self, text, *, interaction_mode, reply_session):
        return self.result


class TestSendToLlmStreamingEdges:
    def _make(self, result, cancelled_flag=True):
        calls = {"finish": 0, "retry": 0, "cancelled": []}

        async def on_retry(text, *, stream_tts, interaction_mode, reply_epoch):
            calls["retry"] += 1

        async def on_finish(**kw):
            calls["finish"] += 1

        async def run():
            return await jarvis_llm.send_to_llm_streaming(
                text="hello",
                stream_tts=False,
                interaction_mode="jarvis",
                reply_epoch=7,
                config=SimpleNamespace(
                    llm_api_url="http://x", llm_text_path="/text/chat",
                    llm_model="m", llm_system_prompt="s",
                ),
                conv_history=deque(),
                max_history_turns=10,
                tts_reply_seq=41,
                consumer_cls=lambda **kw: _FakeConsumer(result, **kw),
                on_sentence=lambda *a, **k: None,
                is_cancelled=lambda: cancelled_flag,
                on_retry_non_streaming=on_retry,
                on_finish_turn=on_finish,
                logger=__import__("logging").getLogger("joyai.jarvis"),
            )

        return run, calls

    def test_cancelled_no_broadcast_but_seq_advances(self):
        run, calls = self._make(_FakeResult(cancelled=True))
        seq = asyncio.run(run())
        assert seq == 42, "tts_reply_seq must still advance on cancelled"
        assert calls["finish"] == 0, "cancelled branch must NOT broadcast on_finish_turn"
        assert calls["retry"] == 0

    def test_retry_branch_reruns_non_streaming_and_advances(self):
        run, calls = self._make(_FakeResult(needs_retry=True))
        seq = asyncio.run(run())
        assert seq == 42
        assert calls["retry"] == 1
        assert calls["finish"] == 0

    def test_normal_broadcasts_and_advances(self):
        run, calls = self._make(_FakeResult(cancelled=False))
        seq = asyncio.run(run())
        assert seq == 42
        assert calls["finish"] == 1
        assert calls["retry"] == 0
