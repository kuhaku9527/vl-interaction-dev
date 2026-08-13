"""Jarvis Mode State Machine.

Wake word (KWS) → streaming ASR → LLM → TTS → exit words → goodbye.

Usage (in server.py or background task):
    from jarvis_mode import JarvisStateMachine

    jarvis = JarvisStateMachine(...)
    asyncio.create_task(jarvis.run())

    # Feed audio from WebRTC callback
    async for pcm_chunk in mic_frames:
        await jarvis.feed_audio(pcm_chunk)
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import time
import wave
from collections import deque
from collections.abc import Callable
from pathlib import Path

from . import jarvis_kws

# Facade re-exports (batch 6: jarvis_config / jarvis_state split): the
# configuration + state declarations moved to their own modules; this module
# re-exports them (`x as x`) so the existing import surface
# (`from joy_interaction_webui.jarvis_mode import JarvisConfig, ...`)
# keeps working unchanged (same pattern as server.py / live_adapter.py).
from .jarvis_config import (
    _ASR_CONFIRM_NON_WORD as _ASR_CONFIRM_NON_WORD,
)
from .jarvis_config import (
    _GARBAGE_PUNCT_ONLY as _GARBAGE_PUNCT_ONLY,
)
from .jarvis_config import (
    EXIT_WORDS as EXIT_WORDS,
)
from .jarvis_config import (
    JarvisConfig as JarvisConfig,
)
from .jarvis_config import (
    _is_garbage_text as _is_garbage_text,
)
from .jarvis_config import (
    _load_default_llm_system_prompt as _load_default_llm_system_prompt,
)
from .jarvis_config import (
    asr_model_display_name as asr_model_display_name,
)
from .jarvis_state import AsrPartial as AsrPartial
from .jarvis_state import JarvisState as JarvisState
from .smart_turn_adapter import SmartTurnAdapter
from .tts_turn_common import (
    fetch_tts_pcm,
    spawn_sentence_tts,
    synthesize_tts_sentence,
    wrap_pcm16_wav,
)
from .vad_bypass import VadBypass

logger = logging.getLogger("joyai.jarvis")


# ============================================================================
# State Machine
# ============================================================================

# Local paraformer promotion reuses the in-process shadow ASR (see
# _feed_kws_shadow_asr) — no cloud call needed. "bt" survives local paraformer
# ("b t 在吗") but is mangled by cloud SenseVoice ("滴滴你在吗"), so promotion
# MUST run locally.


class JarvisStateMachine:
    """Core state machine for BT-7274 Jarvis interaction.

    Lifecycle:
        KWS_LISTENING → WAKE_DETECTED → DIALOG_ACTIVE ⇄ TTS_PAUSED
              ↑                                              ↓
              └────────── EXIT_DETECTED ←────────────────────┘
    """

    def __init__(
        self,
        config: JarvisConfig | None = None,
        *,
        on_wake: Callable[[], None] | None = None,
        on_goodbye: Callable[[], None] | None = None,
        on_asr_partial: Callable[[AsrPartial], None] | None = None,
        on_user_utterance: Callable[[str], None] | None = None,
        on_llm_response: Callable[[str, str], None] | None = None,
        on_tts_sentence: Callable[[str, int, str, int], None] | None = None,
        audio_output: Callable[[bytes, int], asyncio.Future] | None = None,
    ):
        """
        audio_output: async callable(pcm_bytes, sample_rate) to play PCM
        via webui WebRTC audio output track. If None, falls back to
        local simpleaudio/sounddevice (if available) or sleep+log.

        on_tts_sentence: P0-A streaming — async-safe callback
        ``(sentence_text, seq, audio_b64, session)`` fired once a flushed
        sentence's TTS audio (WAV base64) is ready for the browser. The
        caller (jarvis_session) adds the webui session_id and broadcasts a
        ``tts_sentence`` WS message.
        """
        self.config = config or JarvisConfig()
        self.state = JarvisState.KWS_LISTENING

        # Callbacks
        self.on_wake = on_wake
        self.on_goodbye = on_goodbye
        self.on_asr_partial = on_asr_partial
        self.on_user_utterance = on_user_utterance
        self.on_llm_response = on_llm_response
        self.on_tts_sentence = on_tts_sentence
        self.audio_output = audio_output  # async (pcm, sr) -> None

        # Engines (lazy init)
        self._kws = None
        self._asr = None
        self._asr_stream_active = False

        # State
        self._last_speech_time: float = 0.0
        self._current_asr_text: str = ""
        self._tts_task: asyncio.Task | None = None
        # P0-A TTS streaming state. `_tts_sentence_tasks` tracks the
        # per-sentence :8985 synthesis tasks spawned while the LLM stream is
        # consumed; `_tts_sentence_epoch` is bumped whenever TTS must stop
        # (barge-in / exit word), invalidating every in-flight sentence;
        # `_tts_reply_seq` assigns a unique session id per LLM reply so the
        # browser can tell a new reply's sentences from an old reply's;
        # `_llm_stream_cancel` is a per-stream cancellation flag the
        # consumer checks between frames.
        self._tts_sentence_tasks: set[asyncio.Task] = set()
        self._tts_sentence_epoch: int = 0
        self._tts_reply_seq: int = 0
        self._llm_stream_cancel: bool = False
        # P1 (late llm_reply suppression). `_llm_reply_epoch` is a monotonic
        # generation counter bumped once per user turn (in `_send_to_llm`)
        # and on every barge-in / exit word (`_pause_tts` / `_stop_tts`).
        # Each turn's llm_reply broadcast carries the epoch captured at its
        # turn start; a barge-in that bumps the counter therefore expires any
        # in-flight / not-yet-broadcast reply from an older turn.
        # `_current_turn_reply_epoch` is set by `_finish_llm_turn` right
        # before the broadcast so the session callback can tag the WS payload.
        self._llm_reply_epoch: int = 0
        self._current_turn_reply_epoch: int = 0
        # v3.37: when webinfer returns decision="delegation", route the
        # delegated question to BackgroundModelService.handle_foreground_response
        # so the same sub-agent fires for voice requests as for video.
        # Set by JarvisSessionManager.create_session; kept off the class
        # signature so tests can patch it without re-imports.
        self._background_service: object | None = None
        self._consume_task: asyncio.Task | None = None
        self._audio_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=1024)
        self._tts_done = asyncio.Event()
        self._confirm_task: asyncio.Task | None = None
        self._last_asr_match: str = ""

        # Smart Turn (semantic end-of-turn) adapter. Fail-open: if the ONNX
        # asset is absent it stays unavailable and the acoustic endpoint
        # detection remains the source of truth. The gate is default-OFF;
        # enable with SMART_TURN_ENABLED=1 AND a fetched model asset.
        self._smart_turn = SmartTurnAdapter()
        self._smart_turn_enabled = os.environ.get("SMART_TURN_ENABLED", "").lower() in (
            "1",
            "true",
            "yes",
        )

        # Turn Controller shadow (Phase A: observe-only). Default OFF via the
        # JARVIS_TURN_SHADOW_ENABLED env gate — when off, no shadow is created
        # and jarvis behavior is byte-for-byte unchanged. Fail-open: any init
        # error only logs, never breaks jarvis.
        self._turn_shadow = None
        if os.environ.get("JARVIS_TURN_SHADOW_ENABLED", "").lower() in (
            "1",
            "true",
            "yes",
            "on",
        ):
            try:
                from .turn_controller import TurnConfig, TurnController
                from .turn_controller_shadow import ShadowTurnController

                self._turn_shadow = ShadowTurnController(
                    TurnController(TurnConfig.jarvis()),
                    shadow_enabled=True,
                    logger=logger,
                )
                logger.info("[turn-shadow] shadow enabled (JARVIS_TURN_SHADOW_ENABLED)")
            except Exception as exc:
                logger.warning("[turn-shadow] shadow init failed; running without it: %s", exc)
                self._turn_shadow = None

        # Turn Controller delegate (Phase B: active arbitration for the
        # DIALOG_ACTIVE turn rhythm). Default OFF via the
        # JARVIS_TURN_DELEGATE_ENABLED env gate — when off, no delegate is
        # created and jarvis behavior is byte-for-byte unchanged (this is the
        # production default; Phase B acceptance runs with the gate ON).
        # Fail-open: any init error only logs, never breaks jarvis.
        self._turn_delegate = None
        if os.environ.get("JARVIS_TURN_DELEGATE_ENABLED", "").lower() in (
            "1",
            "true",
            "yes",
            "on",
        ):
            try:
                from .turn_controller import TurnConfig, TurnController
                from .turn_controller_delegate import TurnControllerDelegate

                self._turn_delegate = TurnControllerDelegate(
                    TurnController(TurnConfig.jarvis()),
                    logger=logger,
                )
                logger.info("[turn-delegate] delegate enabled (JARVIS_TURN_DELEGATE_ENABLED)")
            except Exception as exc:
                logger.warning("[turn-delegate] delegate init failed; running without it: %s", exc)
                self._turn_delegate = None

        # VAD bypass (Silero, sherpa-onnx) — form A fail-open. Default OFF via
        # JARVIS_VAD_ENABLED. When unavailable it is transparent (KWS gets all
        # audio). Mirrors the Smart Turn fail-open pattern above.
        self._vad = VadBypass(
            enabled=self.config.vad_enabled,
            model_dir=self.config.vad_model_dir,
            threshold=self.config.vad_threshold,
            min_silence_duration=self.config.vad_min_silence_duration,
            min_speech_duration=self.config.vad_min_speech_duration,
            window_size=self.config.vad_window_size,
        )
        logger.info(
            "VAD bypass initialized (enabled=%s available=%s softgate=%s)",
            self.config.vad_enabled,
            self._vad.available,
            self.config.vad_softgate,
        )
        # Latest VAD speech annotation for the most recent fed chunk. Default
        # True (speech) so a missing/early annotation never soft-gates KWS off.
        self._last_vad_speech: bool = True
        # Rolling recent-audio buffer (~8s @ 16kHz mono int16) for Smart Turn
        # context, matching the model's 8s window. Capped to avoid unbounded
        # growth.
        self._recent_audio = bytearray()

        # v3.24 conversation history for LLM context.
        # Bounded FIFO of (role, content) tuples; trimmed to max_turns.
        self._conv_history: deque[tuple[str, str]] = deque(maxlen=20)
        self._max_history_turns: int = 10

        # KWS diagnostics: rolling audio capture + ASR shadow text.  These are
        # deliberately diagnostic-only; they do not promote to wake by themselves.
        self._kws_capture_chunks: deque[bytes] = deque()
        self._kws_capture_bytes = 0
        self._last_kws_capture_at = 0.0
        self._kws_capture_seq = 0
        self._kws_shadow_asr_active = False
        self._kws_shadow_last_text = ""
        self._kws_shadow_last_log_at = 0.0
        self._kws_shadow_last_speech_at = 0.0
        self._last_kws_fresh_probe_at = 0.0
        # ASR promotion (local paraformer recall booster) runtime state.
        self._last_kws_hit_at = 0.0
        self._last_promo_wake_at = 0.0

    # ------------------------------------------------------------------
    # Engine helpers
    # ------------------------------------------------------------------

    def _init_kws(self):
        if self._kws is not None:
            return
        from services.asr.jarvis.kws import JarvisKWS

        self._kws = JarvisKWS(
            model_dir=self.config.kws_model_dir,
            wake_word=self.config.wake_word,
            num_threads=self.config.kws_num_threads,
            keywords_score=self.config.kws_keywords_score,
            keywords_threshold=self.config.kws_keywords_threshold,
            num_trailing_blanks=self.config.kws_num_trailing_blanks,
            max_active_paths=self.config.kws_max_active_paths,
        )
        self._kws.start()

    def _init_asr(self):
        if self._asr is not None:
            return
        from services.asr.jarvis.asr import JarvisASR

        self._asr = JarvisASR(
            model_dir=self.config.asr_model_dir,
            num_threads=self.config.asr_num_threads,
        )

    async def prewarm_engines(self) -> None:
        """Load KWS and ASR models off the event loop.

        Critical for the hybrid wake path: ``_handle_kws`` runs in the
        same event-loop turn as ``_init_asr``. The ASR sherpa-onnx model
        takes ~1.2s to load on first use, which is the exact length of the
        ``asr_confirm_timeout_s`` confirm window — so any wake fired on a
        cold ASR instance is rejected before the engine can process a
        single audio chunk. Prewarming both engines at session start lets
        the post-wake confirm window do its real job.

        Both loads are blocking CPU-bound work, so we dispatch them
        through the default executor so the asyncio loop stays
        responsive to WebRTC and WebSocket traffic.
        """
        loop = asyncio.get_running_loop()

        def _load_kws() -> None:
            self._init_kws()

        def _load_asr() -> None:
            self._init_asr()

        logger.info(
            "Prewarming Jarvis engines (kws=%s asr=%s) — first load may take a few seconds",
            self.config.kws_model_dir,
            self.config.asr_model_dir,
        )
        # KWS is small (~10MB) and ASR is the heavy one (~200MB).
        # Run them sequentially in the executor; we cannot easily overlap
        # them without spinning up a second executor. In practice the
        # combined ~3-4s is acceptable as a one-shot cost when the user
        # clicks Listen.
        await loop.run_in_executor(None, _load_kws)
        await loop.run_in_executor(None, _load_asr)
        logger.info(
            "Jarvis engines ready (kws=%s asr=%s)", self.config.wake_word, self.config.asr_model_dir
        )

    # ------------------------------------------------------------------
    # Audio feed loop (caller-driven)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Smart Turn (semantic end-of-turn) gate
    # ------------------------------------------------------------------
    def _smart_turn_allows_send(self, text: str) -> bool:
        """Gate before sending a finalized utterance to the LLM.

        Returns True (send) when:
          * the gate is disabled (``SMART_TURN_ENABLED`` unset) — the default,
          * the model asset is unavailable (fail-open), or
          * the model judges this is a real end-of-turn.
        Returns False (defer / keep DIALOG_ACTIVE) only when the gate is
        ENABLED and the model judges the user has NOT finished (e.g. a
        trailing "嗯……那个").

        Fail-open + default-off guarantee ZERO behavior change unless the
        operator explicitly enables Smart Turn AND provides the ONNX asset.
        """
        if not getattr(self, "_smart_turn_enabled", False):
            return True
        adapter = getattr(self, "_smart_turn", None)
        if adapter is None or not adapter.available:
            return True  # fail-open: defer to acoustic endpoint detection
        audio = bytes(getattr(self, "_recent_audio", b""))
        complete, _prob = adapter.is_end_of_turn(audio, text)
        if not complete:
            logger.debug("[smart-turn] deferring send (model: not end-of-turn)")
            return False
        return True

    async def feed_audio(self, pcm: bytes):
        """Main audio feed — drive the state machine from mic frames.

        Called from WebRTC audio callback (ideally every 100ms chunk).
        """
        await self._audio_queue.put(pcm)
        # Keep a rolling recent-audio window for Smart Turn context.
        self._recent_audio += pcm
        if len(self._recent_audio) > 256000:  # ~8s @ 16kHz mono int16 (model window)
            del self._recent_audio[: len(self._recent_audio) - 256000]

        # VAD bypass annotation (form A). Convert int16 PCM to float32 [-1,1]
        # and feed the Silero VAD. Fail-open: if VAD is unavailable this is a
        # no-op and is_speech() returns True (KWS keeps receiving all audio).
        if self._vad.available and pcm and len(pcm) % 2 == 0:
            try:
                import numpy as np

                float32 = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
                self._vad.accept_waveform(float32)
                self._last_vad_speech = self._vad.is_speech()
            except Exception as exc:  # never break the audio feed
                logger.debug("[vad] feed_audio annotation failed (%s)", exc)
                self._last_vad_speech = True
        else:
            self._last_vad_speech = True

    # ------------------------------------------------------------------
    # State machine runner
    # ------------------------------------------------------------------

    async def run(self):
        """Main state machine loop (asyncio background task)."""
        logger.info("Jarvis state machine started (state=KWS_LISTENING)")
        try:
            while True:
                pcm = await self._audio_queue.get()

                if self.state == JarvisState.KWS_LISTENING:
                    await self._handle_kws(pcm)

                elif self.state == JarvisState.WAKE_DETECTED:
                    # Block on wake.wav playback (transition handled internally)
                    logger.debug("WAKE_DETECTED: waiting for wake.wav")

                elif self.state == JarvisState.WAIT_ASR_CONFIRM:
                    await self._handle_wait_asr_confirm(pcm)

                elif self.state == JarvisState.DIALOG_ACTIVE:
                    await self._handle_dialog(pcm)

                elif self.state == JarvisState.TTS_PAUSED:
                    await self._handle_dialog(pcm)

                elif self.state == JarvisState.EXIT_DETECTED:
                    # Block on goodbye.wav playback
                    logger.debug("EXIT_DETECTED: waiting for goodbye.wav")
                    await asyncio.sleep(0.1)

                elif self.state == JarvisState.ERROR:
                    await asyncio.sleep(1.0)  # Don't busy-loop

        except asyncio.CancelledError:
            self._cleanup()
            raise

    # ------------------------------------------------------------------
    # Per-state handlers
    # ------------------------------------------------------------------

    async def _handle_kws(self, pcm: bytes):
        """KWS_LISTENING: feed KWS, check for wake word.

        On hit, drain stale audio and transition to WAIT_ASR_CONFIRM.
        The ASR will run for up to ``asr_confirm_timeout_s`` seconds; if its
        text matches one of ``asr_confirm_patterns`` we promote to
        WAKE_DETECTED and continue as before.  Otherwise the wake is
        treated as a false alarm and the session returns to KWS_LISTENING.
        """
        # VAD soft-gate (form A): if VAD is available AND soft-gate is ON AND
        # the latest chunk was classified as silence, skip feeding KWS entirely
        # (do NOT rebuild the KWS stream). Fail-open: if VAD is unavailable or
        # soft-gate is OFF, KWS always receives the chunk (default behaviour).
        if self._vad.available and self.config.vad_softgate and not self._last_vad_speech:
            return
        self._init_kws()
        peak, rms = self._observe_kws_diagnostics(pcm)

        if not self._kws.feed_audio(pcm):
            if await self._probe_kws_fresh_window(peak=peak, rms=rms):
                return
            self._feed_kws_shadow_asr(pcm, peak=peak, rms=rms)
            return

        # v3.19: do NOT drain. ASR needs to see (and re-transcribe) the wake
        # phrase itself to confirm. Drain+post-wake-audio-only was the v3.17
        # mistake — if the user only says "BT" and stops, the post-wake
        # queue is silence and ASR can never match. Instead: tap the wake
        # chunk inline to ASR so it has acoustic material immediately, and
        # let the bg loop's next iteration feed the queued wake-phrase tail.
        self._kws_shadow_asr_active = False
        self._kws_shadow_last_text = ""
        logger.info(
            "Wake word detected: '%s' (peak=%.3f rms=%.3f)",
            self.config.wake_word,
            peak,
            rms,
        )
        self._last_wake_peak = peak
        self._last_wake_rms = rms
        self._last_kws_hit_at = time.time()
        await self._transition_to(JarvisState.WAIT_ASR_CONFIRM)
        self._init_asr()
        self._asr.start()
        # Tap: feed the wake chunk to ASR *before* the queue consumer runs,
        # so ASR has the trailing syllable of the wake phrase to work with
        # even if the user stopped talking immediately after "BT".
        tap_text = ""
        try:
            tap_text = self._asr.feed_chunk(pcm) or ""
        except Exception as exc:
            logger.warning("ASR tap of wake chunk failed: %s", exc)
        self._asr_stream_active = True
        self._current_asr_text = tap_text
        self._last_asr_match = ""
        self._last_speech_time = time.time()
        # Schedule the confirm timeout (cancelled on promotion/rejection).
        self._confirm_task = asyncio.create_task(self._wait_asr_confirm_timeout())
        if self.on_wake:
            self.on_wake()
        # Fast-path: if the tap alone produced a confirm pattern, promote
        # without waiting for more audio. Otherwise let the bg loop drive.
        if tap_text and self._asr_confirm_match(tap_text):
            self._last_asr_match = tap_text
            logger.info("ASR confirmed wake via tap: %r", tap_text)
            await self._promote_from_confirm(matched_pattern=tap_text)

    def _pcm_stats(self, pcm: bytes) -> tuple[float, float]:
        """Return (peak, rms) for int16 mono PCM in the 0..1 range.

        Delegates to ``jarvis_kws.pcm_stats`` (extracted batch 6).
        """
        return jarvis_kws.pcm_stats(pcm)

    def _ensure_kws_diagnostic_state(self) -> None:
        """Initialize diagnostic fields for tests that construct via __new__."""
        if not hasattr(self, "_kws_capture_chunks"):
            self._kws_capture_chunks = deque()
        if not hasattr(self, "_kws_capture_bytes"):
            self._kws_capture_bytes = 0
        if not hasattr(self, "_last_kws_capture_at"):
            self._last_kws_capture_at = 0.0
        if not hasattr(self, "_kws_capture_seq"):
            self._kws_capture_seq = 0
        if not hasattr(self, "_kws_shadow_asr_active"):
            self._kws_shadow_asr_active = False
        if not hasattr(self, "_kws_shadow_last_text"):
            self._kws_shadow_last_text = ""
        if not hasattr(self, "_kws_shadow_last_log_at"):
            self._kws_shadow_last_log_at = 0.0
        if not hasattr(self, "_kws_shadow_last_speech_at"):
            self._kws_shadow_last_speech_at = 0.0
        if not hasattr(self, "_last_wake_peak"):
            self._last_wake_peak = 0.0
        if not hasattr(self, "_last_wake_rms"):
            self._last_wake_rms = 0.0
        if not hasattr(self, "_last_kws_fresh_probe_at"):
            self._last_kws_fresh_probe_at = 0.0

    def _observe_kws_diagnostics(self, pcm: bytes) -> tuple[float, float]:
        """Track live KWS input and save speech-like windows for analysis."""
        self._ensure_kws_diagnostic_state()
        peak, rms = self._pcm_stats(pcm)
        self._remember_kws_pcm(pcm)
        if not getattr(self.config, "kws_capture_enabled", True):
            return peak, rms
        if peak < max(0.0, self.config.kws_capture_peak_threshold):
            return peak, rms
        now = time.time()
        if (now - self._last_kws_capture_at) < max(0.5, self.config.kws_capture_min_interval_s):
            return peak, rms
        self._last_kws_capture_at = now
        self._write_kws_capture(peak=peak, rms=rms, ts=now)
        return peak, rms

    def _remember_kws_pcm(self, pcm: bytes) -> None:
        if not pcm:
            return
        max_bytes = int(max(0.2, self.config.kws_capture_window_s) * self.config.sample_rate * 2)
        self._kws_capture_chunks.append(bytes(pcm))
        self._kws_capture_bytes += len(pcm)
        while self._kws_capture_bytes > max_bytes and self._kws_capture_chunks:
            old = self._kws_capture_chunks.popleft()
            self._kws_capture_bytes -= len(old)

    def _write_kws_capture(self, *, peak: float, rms: float, ts: float) -> None:
        if not self._kws_capture_chunks:
            return
        try:
            out_dir = Path(self.config.kws_capture_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            self._kws_capture_seq += 1
            name = (
                f"kws_live_{int(ts * 1000)}_{self._kws_capture_seq:04d}"
                f"_peak{int(peak * 1000):03d}_rms{int(rms * 1000):03d}.wav"
            )
            out_path = out_dir / name
            pcm = b"".join(self._kws_capture_chunks)
            with wave.open(str(out_path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(self.config.sample_rate)
                wf.writeframes(pcm)
            logger.info(
                "KWS diagnostic capture saved: %s (%.2fs peak=%.3f rms=%.3f)",
                out_path,
                len(pcm) / (self.config.sample_rate * 2),
                peak,
                rms,
            )
        except Exception as exc:
            logger.warning("KWS diagnostic capture failed: %s", exc)

    async def _probe_kws_fresh_window(
        self, *, peak: float, rms: float, bypass_min_s: bool = False
    ) -> bool:
        """Fallback KWS probe using a clean stream over recent PCM.

        Real logs showed a rolling 3s capture could wake offline while the
        long-running live stream missed. This probe keeps the primary stream
        untouched and gives short wake words a clean stream boundary.

        Delegates to ``jarvis_kws.probe_fresh_window`` (extracted batch 6);
        the updated probe timestamp is written back to ``_last_kws_fresh_probe_at``.
        """
        hit, last_probe_at = await jarvis_kws.probe_fresh_window(
            config=self.config,
            last_probe_at=self._last_kws_fresh_probe_at,
            capture_bytes=self._kws_capture_bytes,
            capture_chunks=self._kws_capture_chunks,
            pcm_stats_fn=self._pcm_stats,
            kws=self._kws,
            direct_wake=self._direct_wake_from_kws,
            peak=peak,
            rms=rms,
            bypass_min_s=bypass_min_s,
            logger=logger,
        )
        self._last_kws_fresh_probe_at = last_probe_at
        return hit

    async def _direct_wake_from_kws(self, *, source: str, respect_fresh_gate: bool = True) -> None:
        """Promote a trusted KWS hit (or local ASR promotion) without ASR confirm.

        Used for fresh-window KWS recovery AND local paraformer promotion. The
        normal streaming KWS path still goes through WAIT_ASR_CONFIRM. Promotion
        passes ``respect_fresh_gate=False`` so the
        ``kws_fresh_window_direct_wake`` flag never blocks a local catch.
        """
        if respect_fresh_gate and not getattr(self.config, "kws_fresh_window_direct_wake", True):
            return False
        self._kws_shadow_asr_active = False
        self._kws_shadow_last_text = ""
        await self._transition_to(JarvisState.WAKE_DETECTED)
        if self.on_wake:
            self.on_wake()
        logger.info("Direct wake from %s", source)
        await self._play_wake_wav()
        self._init_asr()
        self._asr.start()
        self._asr_stream_active = True
        self._current_asr_text = ""
        self._last_speech_time = time.time()
        logger.info("ASR stream started after %s wake; listening for utterance", source)
        await self._transition_to(JarvisState.DIALOG_ACTIVE)

    def _feed_kws_shadow_asr(self, pcm: bytes, *, peak: float, rms: float) -> None:
        """Run the in-process paraformer in listening state for KWS-miss evidence.

        When ``asr_promotion_enabled`` is on, a KWS MISS where this shadow ASR
        still hears the wake pattern promotes directly to wake (see
        ``_try_promote_from_local_asr``). Otherwise this path logs only.
        """
        self._ensure_kws_diagnostic_state()
        if not getattr(self.config, "kws_shadow_asr_enabled", True):
            return
        now = time.time()
        speechy = peak >= max(0.0, self.config.kws_capture_peak_threshold * 0.7)
        if not speechy and not self._kws_shadow_asr_active:
            return
        try:
            self._init_asr()
            if not self._kws_shadow_asr_active:
                self._asr.start()
                self._kws_shadow_asr_active = True
                self._kws_shadow_last_text = ""
                logger.info(
                    "KWS shadow ASR started (diagnostic; local promotion active if enabled)"
                )
            text = self._asr.feed_chunk(pcm) or ""
        except Exception as exc:
            logger.warning("KWS shadow ASR failed: %s", exc)
            self._kws_shadow_asr_active = False
            return

        if speechy:
            self._kws_shadow_last_speech_at = now
        if text and text != self._kws_shadow_last_text:
            if (now - self._kws_shadow_last_log_at) >= max(
                0.1, self.config.kws_shadow_log_interval_s
            ):
                logger.info(
                    "KWS shadow ASR partial without KWS hit: %r (peak=%.3f rms=%.3f)",
                    text,
                    peak,
                    rms,
                )
                if self._asr_confirm_match(text):
                    logger.info(
                        "KWS MISS: shadow ASR (local paraformer) saw wake pattern %r, KWS did not fire",
                        text,
                    )
                    self._try_promote_from_local_asr(text)
                self._kws_shadow_last_log_at = now
            self._kws_shadow_last_text = text

        if self._kws_shadow_asr_active and (now - self._kws_shadow_last_speech_at) > 2.0:
            if self._kws_shadow_last_text:
                logger.info("KWS shadow ASR segment ended: %r", self._kws_shadow_last_text)
            self._asr.stop()
            self._kws_shadow_asr_active = False
            self._kws_shadow_last_text = ""

    # ------------------------------------------------------------------
    # ASR promotion (local paraformer recall booster)
    # ------------------------------------------------------------------
    def _try_promote_from_local_asr(self, text: str) -> None:
        """Promote to wake when the local shadow ASR catches a KWS-missed 'bt'.

        Called from ``_feed_kws_shadow_asr`` only on a KWS MISS where the
        in-process paraformer heard the wake pattern. Purely additive (never
        suppresses a KWS hit) and debounced by ``asr_promotion_cooldown_s``.
        Fail-safe: any error is logged; it never raises into the KWS feed path.
        """
        cfg = self.config
        if not getattr(cfg, "asr_promotion_enabled", False):
            return
        if self.state != JarvisState.KWS_LISTENING:
            return
        now = time.time()
        cooldown = getattr(cfg, "asr_promotion_cooldown_s", 2.0)
        if (now - self._last_promo_wake_at) < cooldown:
            return
        if (now - self._last_kws_hit_at) < cooldown:
            logger.info("ASR promotion matched but within KWS cooldown; skip: %r", text)
            return
        self._last_promo_wake_at = now
        logger.info("ASR PROMOTION wake (local paraformer): %r", text)
        # Keep a strong reference to the task so it is not garbage-collected
        # before it runs (satisfies ruff RUF006). The wake is fire-and-forget;
        # any error is logged inside _direct_wake_from_kws.
        self._promo_task = asyncio.create_task(
            self._direct_wake_from_kws(source="asr-promotion-local", respect_fresh_gate=False)
        )

    async def _wait_asr_confirm_timeout(self):
        """Reject the wake if ASR does not match within the configured timeout.

        v3.23: Before giving up, run a fresh-window KWS probe over the captured
        PCM as a recovery path. If a clean-stream KWS hit arrives here, it
        bypasses ASR confirm (which the streaming-paraformer model often fails
        on the two-syllable "bt" wake phrase). This recovers the wake when
        the live KWS fired but ASR partials never spelled "bt" within 1.2s.

        Delegates to ``jarvis_kws.wait_asr_confirm_timeout`` (extracted
        batch 6); the probe/reset callbacks keep the instance monkeypatch
        seams (``_probe_kws_fresh_window`` / ``_reset_to_kws``) intact.
        """
        await jarvis_kws.wait_asr_confirm_timeout(
            asr_confirm_timeout_s=self.config.asr_confirm_timeout_s,
            state=self.state,
            last_wake_peak=getattr(self, "_last_wake_peak", 0.0),
            last_wake_rms=getattr(self, "_last_wake_rms", 0.0),
            probe=self._probe_kws_fresh_window,
            reset_to_kws=self._reset_to_kws,
            logger=logger,
        )

    async def _promote_from_confirm(self, matched_pattern: str) -> None:
        """WAIT_ASR_CONFIRM -> WAKE_DETECTED -> DIALOG_ACTIVE after ASR match."""
        # Cancel timeout task so it does not race with promotion.
        if self._confirm_task and not self._confirm_task.done():
            self._confirm_task.cancel()
            try:
                await self._confirm_task
            except asyncio.CancelledError:
                pass
        await self._transition_to(JarvisState.WAKE_DETECTED)
        await self._play_wake_wav()
        # Drain audio accumulated during wake.wav so ASR starts clean.
        await self._drain_pending_audio(reason="post-wake-wav")
        await self._transition_to(JarvisState.DIALOG_ACTIVE)
        if self.on_wake:
            self.on_wake()

    def _asr_confirm_match(self, text: str) -> bool:
        """Return True if ASR text contains the wake phrase in any common form.

        Two matchers are OR'd together:

        1. Explicit substring patterns from ``asr_confirm_patterns`` (backward
           compatibility / operator override).
        2. A wide normalised match that accepts ``bt``, ``b t``, ``b.t``,
           ``b、t``, ``b  t`` etc.  Non-word characters are collapsed to a
           single space, the text is lower-cased, and we accept either the
           joined token ``bt`` or adjacent tokens ``b`` followed by ``t``.
           This catches paraformer outputs that segment the two-syllable
           wake word with whitespace or punctuation.

        Delegates to ``jarvis_kws.asr_confirm_match`` (extracted batch 6).
        """
        return jarvis_kws.asr_confirm_match(text, getattr(self, "config", None))

    async def _handle_wait_asr_confirm(self, pcm: bytes):
        """WAIT_ASR_CONFIRM: feed ASR, check each partial/final for confirm pattern."""
        if not self._asr_stream_active:
            return
        try:
            text = self._asr.feed_chunk(pcm)
        except Exception as exc:
            logger.warning("ASR feed_chunk failed during confirm: %s", exc)
            return
        # v3.19: log every ASR partial at info so production logs show
        # exactly what the model heard during the confirm window.
        # Costs ~6 lines per wake event; indispensable for diagnosing
        # false alarms vs miss-fires.
        logger.info("WAIT_ASR_CONFIRM ASR partial: %r", text)
        if not text:
            return
        self._current_asr_text = text
        if not self._asr_confirm_match(text):
            return
        self._last_asr_match = text
        logger.info("ASR confirmed wake via pattern match: %r", text)
        await self._promote_from_confirm(matched_pattern=text)

    async def _handle_dialog(self, pcm: bytes):
        """DIALOG_ACTIVE / TTS_PAUSED: stream ASR, check exit words, manage TTS.

        Phase B: when the turn-controller delegate is enabled
        (``JARVIS_TURN_DELEGATE_ENABLED``), the DIALOG_ACTIVE turn rhythm is
        arbitrated by the turn_controller (jarvis preset); otherwise (the
        default) the legacy hand-written logic runs byte-for-byte unchanged.
        Fail-open: any delegate error disables it and falls back to the
        legacy path for the rest of the session.
        """
        if not self._asr_stream_active:
            return
        if getattr(self, "_turn_delegate", None) is None:
            await self._handle_dialog_legacy(pcm)
            return
        try:
            await self._handle_dialog_delegated(pcm)
        except Exception as exc:
            logger.warning(
                "[turn-delegate] dialog delegation failed (%s); falling back to legacy",
                exc,
            )
            self._turn_delegate = None
            await self._handle_dialog_legacy(pcm)

    async def _handle_dialog_legacy(self, pcm: bytes):
        """Legacy DIALOG_ACTIVE / TTS_PAUSED handler (current behavior).

        This is the pre-Phase-B logic, kept verbatim so the default (delegate
        OFF) path is byte-for-byte unchanged. The endpoint-commit block is
        shared with the delegated path via :meth:`_handle_dialog_commit`.
        """
        try:
            text = self._asr.feed_chunk(pcm)
        except Exception as e:
            logger.exception("ASR feed_chunk failed: %s", e)
            return
        now = time.time()

        if text and text != self._current_asr_text:
            # New speech (partial grew) — update accumulator and timer.
            # Log every change so an operator can see ASR's current
            # hypothesis evolving in out.log (no need to be black-box).
            logger.info("ASR partial: %r", text)
            self._current_asr_text = text
            self._last_speech_time = now
        # Stale path: text equal to _current_asr_text means ASR holds the last
        # partial on silence; timer stays untouched so the endpoint below fires.

        if text:
            # Emit partial
            partial = AsrPartial(
                text=text,
                is_final=False,
                timestamp_ms=now * 1000,
                reply_epoch=getattr(self, "_llm_reply_epoch", 0),
            )
            if self.on_asr_partial:
                self.on_asr_partial(partial)

            # Check EXIT_WORDS on partial text (not waiting for final)
            stripped = text.strip().lower()
            if any(stripped.endswith(w) for w in EXIT_WORDS):
                logger.info("Exit word detected: %s", text)
                await self._transition_to(JarvisState.EXIT_DETECTED)
                await self._stop_tts()
                await self._play_goodbye_wav()
                await self._reset_to_kws()
                return

            # Interrupt TTS if user started speaking while TTS is playing
            if self.state == JarvisState.DIALOG_ACTIVE and self._tts_playing:
                await self._pause_tts()
                await self._transition_to(JarvisState.TTS_PAUSED)
                logger.debug("TTS paused")
        # Endpoint detection: Paraformer streaming ASR holds the last partial
        # on silence frames (stale). The `if text` block above only updates
        # _last_speech_time when partial grew, so this fires after ~2s of stale.
        if self._current_asr_text and (time.time() - self._last_speech_time) > 2.0:
            await self._handle_dialog_commit()

    async def _handle_dialog_delegated(self, pcm: bytes):
        """DIALOG_ACTIVE / TTS_PAUSED with turn_controller arbitration.

        ASR-derived signals are fed into the turn controller (jarvis preset)
        and the controller's decisions drive the same jarvis actions as the
        legacy path:
          * commit  -> existing :meth:`_handle_dialog_commit` (reuses
            ``_send_to_llm`` verbatim — no new LLM call path),
          * barge-in -> existing TTS-pause logic (TTS_PAUSED entry),
          * EXIT_WORDS / KWS remain jarvis-owned mode extensions.

        The controller's ``on_speech_stopped`` is fed at the same 2s ASR
        staleness rule jarvis uses today, so commit timing is unchanged.
        """
        delegate = self._turn_delegate
        try:
            text = self._asr.feed_chunk(pcm)
        except Exception as e:
            logger.exception("ASR feed_chunk failed: %s", e)
            return
        now = time.time()

        if text and text != self._current_asr_text:
            # New speech (partial grew) — update accumulator and timer, then
            # feed the acoustic + transcript events into the controller.
            # conf=0.9 >= jarvis preset barge_in_threshold (0.75) so a
            # barge-in while the controller tracks an agent turn maps to
            # HARD_INTERRUPTED deterministically.
            logger.info("ASR partial: %r", text)
            self._current_asr_text = text
            self._last_speech_time = now
            delegate.on_speech_started(conf=0.9)
            delegate.on_partial_transcript(text, is_final=False)

        if text:
            # Emit partial (same as legacy)
            partial = AsrPartial(
                text=text,
                is_final=False,
                timestamp_ms=now * 1000,
                reply_epoch=getattr(self, "_llm_reply_epoch", 0),
            )
            if self.on_asr_partial:
                self.on_asr_partial(partial)

            # EXIT_WORDS stay jarvis-owned (mode extension, not delegated).
            stripped = text.strip().lower()
            if any(stripped.endswith(w) for w in EXIT_WORDS):
                logger.info("Exit word detected: %s", text)
                await self._transition_to(JarvisState.EXIT_DETECTED)
                await self._stop_tts()
                await self._play_goodbye_wav()
                await self._reset_to_kws()
                return

        # Barge-in decision. The controller fires ``on_barge_in`` from
        # HARD_INTERRUPTED when it is tracking an agent turn; ``text`` is the
        # fail-safe backstop for the jarvis dialog path where TTS is played by
        # the browser (stream_tts=False => no in-process ``_tts_task`` => the
        # controller never sits in SPEAKING). Both reuse the existing TTS-pause
        # logic, so the interrupt rhythm is unchanged.
        barge_in = delegate.take_barge_in()
        tts_playing = self.state == JarvisState.DIALOG_ACTIVE and self._tts_playing
        if (barge_in or text) and tts_playing:
            await self._pause_tts()
            await self._transition_to(JarvisState.TTS_PAUSED)
            logger.debug("TTS paused")

        # Endpoint: the jarvis 2s staleness rule feeds the controller's
        # speech-stop; the controller's commit decision then reuses the
        # existing commit path. Timing is identical to the legacy rule.
        if self._current_asr_text and (now - self._last_speech_time) > 2.0:
            silence_ms = int((now - self._last_speech_time) * 1000)
            delegate.on_speech_stopped(silence_ms)
            if delegate.take_commit():
                outcome = await self._handle_dialog_commit()
                if outcome != "sent":
                    # The controller committed but jarvis declined the turn
                    # (garbage / smart-turn defer): drive the controller back
                    # to LISTENING so the next user turn commits cleanly.
                    delegate.on_llm_response_token("")
                    delegate.on_tts_started()
                    delegate.on_tts_finished()
            elif self.state == JarvisState.TTS_PAUSED:
                # Post-barge-in commit: the controller's FSM has no commit
                # from HARD_INTERRUPTED (it went COOLDOWN), so jarvis keeps
                # the legacy commit for the barge-in utterance (TTS_PAUSED is
                # a jarvis mode extension), then realigns to LISTENING.
                delegate.on_cooldown_elapsed()
                await self._handle_dialog_commit()

    async def _handle_dialog_commit(self) -> str:
        """Endpoint commit: send the accumulated utterance to the LLM.

        Shared by the legacy and turn-delegate dialog paths; mirrors the old
        inline endpoint block exactly: transcript reset, ASR stream reset,
        garbage drop, smart-turn gate, ``_send_to_llm``, paused-TTS resume,
        and the return to DIALOG_ACTIVE.

        Returns:
            ``"sent"`` when the utterance was forwarded to the LLM,
            ``"garbage"`` when it was dropped as noise, or ``"deferred"``
            when the Smart Turn gate deferred the send.
        """
        utterance = self._current_asr_text
        self._current_asr_text = ""

        # Reset the streaming ASR session so the next chunk starts
        # fresh; otherwise Paraformer keeps returning the stale partial
        # and we loop the same junk text into the LLM.
        if self._asr is not None:
            try:
                self._asr.start()
            except Exception as exc:
                logger.warning("ASR stream reset failed: %s", exc)

        if _is_garbage_text(utterance):
            logger.info(
                "ASR endpoint reached, dropping garbage: %r",
                utterance,
            )
            await self._transition_to(JarvisState.DIALOG_ACTIVE)
            return "garbage"

        # Smart Turn semantic gate (fail-open + default-off). When it
        # judges the user has NOT finished (e.g. trailing "嗯……那个"),
        # defer: keep the partial, do NOT clear/reset/send/transition.
        if not self._smart_turn_allows_send(utterance):
            logger.debug(
                "Smart Turn deferred send; keeping DIALOG_ACTIVE for: '%s'",
                utterance,
            )
            return "deferred"

        logger.info(
            "ASR endpoint reached, sending to LLM: '%s'",
            utterance,
        )
        if self.on_user_utterance:
            self.on_user_utterance(utterance)
        # stream_tts=False: backend does NOT push PCM via WebRTC
        # SpeakerAudioTrack. The browser plays TTS through
        # <audio> via `playLlmReplyAudio` on llm_reply. Setting
        # this back to True would replay every reply twice (once
        # from the browser, once from the WebRTC speaker).
        await self._send_to_llm(utterance, stream_tts=False, interaction_mode="jarvis")

        # Resume TTS if paused (LLM response will trigger new TTS)
        if self.state == JarvisState.TTS_PAUSED and self._tts_task:
            # Cancel old TTS and restart with new LLM response
            self._tts_task.cancel()
            self._tts_task = None
        await self._transition_to(JarvisState.DIALOG_ACTIVE)
        return "sent"

    # ------------------------------------------------------------------
    # Transition helpers
    # ------------------------------------------------------------------

    async def _drain_pending_audio(self, reason: str = "") -> int:
        """Drop all PCM chunks currently buffered in the audio queue.

        Used right after wake detection so that ASR does not see the wake
        phrase itself or the mic input that piled up during wake.wav
        playback (~4.6s).  Returns the number of chunks dropped.
        """
        dropped = 0
        while True:
            try:
                self._audio_queue.get_nowait()
                dropped += 1
            except asyncio.QueueEmpty:
                break
        if dropped:
            logger.info("Drained %d queued audio chunks (%s)", dropped, reason)
        return dropped

    async def _transition_to(self, new_state: JarvisState):
        old = self.state
        self.state = new_state
        logger.debug("State: %s → %s", old.name, new_state.name)
        # Turn Controller shadow (Phase A): observe-only, fail-open. Mirrors
        # the real transition into the shadow for alignment logging; it never
        # affects jarvis behavior (any error is swallowed here).
        if getattr(self, "_turn_shadow", None) is not None:
            try:
                self._turn_shadow.on_jarvis_transition(
                    old.name, new_state.name, "jarvis-transition"
                )
            except Exception as exc:
                logger.warning("[turn-shadow] shadow observation failed: %s", exc)
        # Turn Controller delegate (Phase B): keep the controller aligned with
        # the jarvis dialog lifecycle. Entering DIALOG_ACTIVE drives the wake
        # gate to LISTENING; returning to KWS_LISTENING resets the controller.
        # Fail-open: any error only logs, never breaks jarvis.
        if getattr(self, "_turn_delegate", None) is not None:
            try:
                if new_state == JarvisState.DIALOG_ACTIVE:
                    self._turn_delegate.on_dialog_enter()
                elif new_state == JarvisState.KWS_LISTENING:
                    self._turn_delegate.on_dialog_reset()
            except Exception as exc:
                logger.warning("[turn-delegate] dialog lifecycle hook failed: %s", exc)

    async def _reset_to_kws(self):
        """Clean up dialog state and return to KWS_LISTENING."""
        self._asr_stream_active = False
        if self._confirm_task and not self._confirm_task.done():
            self._confirm_task.cancel()
            try:
                await self._confirm_task
            except asyncio.CancelledError:
                pass
            self._confirm_task = None
        if self._asr:
            self._asr.stop()
        self._kws_shadow_asr_active = False
        self._kws_shadow_last_text = ""
        self._current_asr_text = ""
        self._tts_task = None
        await self._transition_to(JarvisState.KWS_LISTENING)
        if self._kws:
            self._kws.start()  # fresh KWS stream
        logger.info("Jarvis reset to KWS_LISTENING")

    # ------------------------------------------------------------------
    # Event audio playback
    # ------------------------------------------------------------------

    async def _play_event_wav(self, filename: str):
        """Play a pre-generated event WAV file.

        Reads the WAV (any sample rate; mono PCM16, or downmix from stereo),
        pushes PCM to audio_output callback (webui WebRTC track),
        or falls back to log+sleep if no callback is registered.
        Never raises — log only on errors.
        """
        path = Path(self.config.events_dir) / filename
        if not path.exists():
            logger.warning("Event audio not found: %s (skipping)", path)
            return

        try:
            import wave

            import numpy as _np

            with wave.open(str(path), "rb") as wf:
                sample_rate = wf.getframerate()
                n_channels = wf.getnchannels()
                sampwidth = wf.getsampwidth()
                raw = wf.readframes(wf.getnframes())
            if sampwidth != 2:
                logger.warning(
                    "Event audio %s has sampwidth=%d (expected 2); playback may distort",
                    filename,
                    sampwidth,
                )
            samples = _np.frombuffer(raw, dtype=_np.int16)
            if n_channels > 1:
                # Downmix interleaved channels by averaging. Keeps duration
                # honest and stops SpeakerAudioTrack._resample_pcm16 from
                # treating L/R as consecutive mono samples (pitch shift bug).
                frames = samples.reshape(-1, n_channels)
                samples = frames.mean(axis=1).astype(_np.int16)
                pcm = samples.tobytes()
            else:
                pcm = raw
            duration = samples.size / sample_rate
            logger.info(
                "Playing event: %s (%.1fs, %dHz, %dch, %d bytes)",
                filename,
                duration,
                sample_rate,
                n_channels,
                len(pcm),
            )
            if self.audio_output:
                try:
                    await self.audio_output(pcm, sample_rate)
                except Exception as exc:
                    logger.error("audio_output for %s failed: %s", filename, exc)
                    await asyncio.sleep(duration)
            else:
                # No audio_output callback: log + sleep (silent fail)
                logger.debug("No audio_output registered; sleeping %.1fs", duration)
                await asyncio.sleep(duration)
        except Exception as exc:
            logger.error("Failed to play %s: %s", filename, exc)
            await asyncio.sleep(1.5)  # fallback

    async def _play_wake_wav(self):
        await self._play_event_wav(self.config.wake_wav)

    async def _play_goodbye_wav(self):
        if self.on_goodbye:
            self.on_goodbye()
        await self._play_event_wav(self.config.goodbye_wav)

    # ------------------------------------------------------------------
    # TTS control
    # ------------------------------------------------------------------

    def _ensure_tts_stream_state(self) -> None:
        """Lazily initialize P0-A streaming TTS state.

        Tests (and some callers) construct ``JarvisStateMachine`` via
        ``__new__``, skipping ``__init__``; this mirrors the existing
        ``_ensure_kws_diagnostic_state`` convention so the streaming fields
        exist before they are read.
        """
        if not hasattr(self, "_tts_sentence_tasks"):
            self._tts_sentence_tasks = set()
        if not hasattr(self, "_tts_sentence_epoch"):
            self._tts_sentence_epoch = 0
        if not hasattr(self, "_tts_reply_seq"):
            self._tts_reply_seq = 0
        if not hasattr(self, "_llm_stream_cancel"):
            self._llm_stream_cancel = False
        if not hasattr(self, "_llm_reply_epoch"):
            self._llm_reply_epoch = 0
        if not hasattr(self, "_current_turn_reply_epoch"):
            self._current_turn_reply_epoch = 0

    @property
    def _tts_playing(self) -> bool:
        """True while any backend TTS audio may still be playing.

        Covers both the legacy in-process ``_tts_task`` and the P0-A
        streaming per-sentence synthesis tasks (the browser plays the
        latter, but the backend must still stop paying for :8985 calls
        on barge-in).
        """
        self._ensure_tts_stream_state()
        return bool(
            (self._tts_task is not None and not self._tts_task.done()) or self._tts_sentence_tasks
        )

    async def _stop_tts(self):
        """Stop TTS immediately (user interrupted with exit word)."""
        # P0-A: also stop the streaming sentence queue (bump epoch, cancel
        # every in-flight :8985 sentence task, flag the LLM-stream consumer).
        self._ensure_tts_stream_state()
        self._tts_sentence_epoch += 1
        # P1: expire any in-flight / not-yet-broadcast llm_reply from an
        # older turn so a late broadcast cannot play over the exit word.
        self._llm_reply_epoch += 1
        logger.info(
            "[llm-reply] epoch bumped to %d (reason=%s)",
            self._llm_reply_epoch,
            "exit-word",
        )
        self._llm_stream_cancel = True
        for task in list(self._tts_sentence_tasks):
            if not task.done():
                task.cancel()
        self._tts_sentence_tasks.clear()
        if self._tts_task and not self._tts_task.done():
            self._tts_task.cancel()
            self._tts_task = None
            logger.debug("TTS stopped")

    async def _pause_tts(self):
        """Pause TTS (user started speaking while TTS was playing)."""
        # P0-A: barge-in must also stop the streaming sentence queue — the
        # browser stops old audio via its epoch guard, and we stop paying
        # for :8985 synthesis of sentences the user will never hear.
        self._ensure_tts_stream_state()
        self._tts_sentence_epoch += 1
        # P1: bump the llm_reply epoch so a reply from the interrupted turn
        # that is still in flight (or not yet broadcast) becomes stale and is
        # discarded by the front-end's reply_epoch guard.
        self._llm_reply_epoch += 1
        logger.info(
            "[llm-reply] epoch bumped to %d (reason=%s)",
            self._llm_reply_epoch,
            "barge-in",
        )
        self._llm_stream_cancel = True
        for task in list(self._tts_sentence_tasks):
            if not task.done():
                task.cancel()
        self._tts_sentence_tasks.clear()
        if self._tts_task and not self._tts_task.done():
            self._tts_task.cancel()
            self._tts_task = None
            logger.debug("TTS paused")

    # ------------------------------------------------------------------
    # LLM interaction
    # ------------------------------------------------------------------

    async def _send_to_llm(
        self,
        text: str,
        *,
        stream_tts: bool = True,
        image_b64: str | None = None,
        interaction_mode: str = "jarvis",
    ):
        """Send user's ASR text to the LLM (via webinfer) and drive TTS.

        P0-A: the jarvis voice dialog path (``interaction_mode="jarvis"``,
        text-only) consumes webinfer's NDJSON stream (decision frame first,
        then content) and synthesizes TTS per flushed sentence so the first
        sound arrives in ~800ms instead of after the full reply. Every other
        caller (``call`` mode / paper-plane multimodal / streaming disabled)
        keeps the existing single-shot ``_send_to_llm_non_streaming`` path
        byte-for-byte.
        """
        # P1: every user turn gets a fresh reply epoch. The value captured
        # here is carried by this turn's llm_reply broadcast; a barge-in that
        # bumps ``_llm_reply_epoch`` after this point expires this turn's
        # reply so the front-end discards it when it finally arrives.
        self._ensure_tts_stream_state()
        self._llm_reply_epoch += 1
        turn_reply_epoch = self._llm_reply_epoch
        logger.info(
            "[llm-reply] epoch bumped to %d (reason=%s)",
            turn_reply_epoch,
            "turn-start",
        )
        if (
            interaction_mode == "jarvis"
            and not image_b64
            and getattr(self.config, "llm_streaming_enabled", True)
        ):
            return await self._send_to_llm_streaming(
                text,
                stream_tts=stream_tts,
                interaction_mode=interaction_mode,
                reply_epoch=turn_reply_epoch,
            )
        return await self._send_to_llm_non_streaming(
            text,
            stream_tts=stream_tts,
            image_b64=image_b64,
            interaction_mode=interaction_mode,
            reply_epoch=turn_reply_epoch,
        )

    async def _send_to_llm_non_streaming(
        self,
        text: str,
        *,
        stream_tts: bool = True,
        image_b64: str | None = None,
        interaction_mode: str = "jarvis",
        reply_epoch: int | None = None,
    ):
        """Single-shot LLM call (legacy path, unchanged).

        Calls POST {llm_api_url}/{text|multimodal} and waits for the complete
        JSON response, then optionally triggers TTS with the full text. This
        is the pre-P0-A behavior kept for ``call`` mode, multimodal sends,
        and as the fail-open fallback when streaming errors out.

        ``reply_epoch`` is the P1 turn epoch captured by ``_send_to_llm``; it
        tags the llm_reply broadcast so the front-end can drop a late reply.
        """
        # v3.24: prepend bounded conversation history so BT-7274 retains
        # short-term context across turns without persisting anything.
        messages = [{"role": "system", "content": self.config.llm_system_prompt}]
        # Snapshot history before we mutate it
        history_snapshot = list(self._conv_history)[-self._max_history_turns * 2 :]
        for role, content in history_snapshot:
            messages.append({"role": role, "content": content})
        if image_b64:
            # Multimodal: text + image_url (OpenAI-compatible). Requires
            # llama-server to be loaded with --mmproj (which our default
            # install does; see install/windows/start-llama-server.ps1).
            user_content = [
                {"type": "text", "text": text},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
            ]
            messages.append({"role": "user", "content": user_content})
        else:
            messages.append({"role": "user", "content": text})

        logger.info(
            "LLM input: '%s' (history_turns=%d)",
            text,
            len(history_snapshot) // 2,
        )
        import httpx

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # v3.37 single-LLM-gateway: route by media type.
                # text-only path -> /v1/text/chat (orchestration: prompt
                # composition, token guard, decision-token parsing).
                # multimodal path -> /v1/chat/completions (existing).
                endpoint_path = (
                    self.config.llm_multimodal_path if image_b64 else self.config.llm_text_path
                )
                endpoint_url = f"{self.config.llm_api_url}{endpoint_path}"
                resp = await client.post(
                    endpoint_url,
                    json={
                        "model": self.config.llm_model,
                        "messages": messages,
                        "max_tokens": 200,
                        "temperature": 0.7,
                        "interaction_mode": interaction_mode,
                    },
                )
                resp.raise_for_status()
                response_payload = resp.json()
                choice = (response_payload.get("choices") or [{}])[0]
                response = (choice.get("message") or {}).get("content") or ""
                response = response.strip() if isinstance(response, str) else ""
                harness = response_payload.get("streamingharness") or {}
                decision = harness.get("decision") or ("response" if response else "silence")
                delegation_question = harness.get("delegation_question")
        except Exception as exc:
            logger.error("LLM call failed: %s", exc)
            response = f"[LLM error: {exc}]"
            decision = "silence"
            delegation_question = None

        await self._finish_llm_turn(
            text=text,
            response=response,
            decision=decision,
            delegation_question=delegation_question,
            stream_tts=stream_tts,
            reply_epoch=reply_epoch,
        )

    async def _send_to_llm_streaming(
        self,
        text: str,
        *,
        stream_tts: bool = False,
        interaction_mode: str = "jarvis",
        reply_epoch: int | None = None,
    ):
        """P0-A streaming LLM consumption: decision first, sentence TTS.

        Delegates the actual NDJSON stream consumption + SentenceBuffer
        flushing + per-sentence TTS wiring to the shared
        :class:`~.turn_streaming.StreamingTurnConsumer` (spec
        ``draft-live-interaction-layer.md`` §4.3 — the future live dialog
        reuses the same consumer). This method keeps the jarvis turn
        semantics: per-reply session id, sentence-epoch synthesis wiring,
        fail-open non-streaming retry, and ``_finish_llm_turn`` broadcast.

        Fail-open (never lose the reply): if the stream errors before any
        frame, the whole turn is re-run through the non-streaming path; if it
        errors after a decision, the buffered remainder is synthesized and
        the reply is broadcast anyway (logged).

        ``reply_epoch`` is the P1 turn epoch captured by ``_send_to_llm``;
        it tags the llm_reply broadcast (see ``_finish_llm_turn``).
        """
        from .turn_streaming import StreamingTurnConsumer

        # v3.24: prepend bounded conversation history (same as non-streaming).
        history_snapshot = list(self._conv_history)[-self._max_history_turns * 2 :]

        endpoint_url = f"{self.config.llm_api_url}{self.config.llm_text_path}"
        self._ensure_tts_stream_state()
        self._llm_stream_cancel = False
        reply_session = self._tts_reply_seq
        self._tts_reply_seq += 1

        consumer = StreamingTurnConsumer(
            endpoint_url=endpoint_url,
            model=self.config.llm_model,
            system_prompt=self.config.llm_system_prompt,
            history_snapshot=history_snapshot,
            max_tokens=200,
            temperature=0.7,
            timeout_s=30.0,
            on_sentence=self._spawn_sentence_tts,
            is_cancelled=lambda: self._llm_stream_cancel,
            stream_logger=logger,
        )
        result = await consumer.consume(
            text,
            interaction_mode=interaction_mode,
            reply_session=reply_session,
        )

        if result.needs_non_streaming_retry:
            # No decision was ever delivered (transport failure, HTTP error,
            # or an error frame BEFORE the decision frame): clean retry
            # through the non-streaming path so the reply is never lost —
            # nothing was spoken, so there is no double-play risk.
            return await self._send_to_llm_non_streaming(
                text,
                stream_tts=stream_tts,
                interaction_mode=interaction_mode,
                reply_epoch=reply_epoch,
            )

        if result.cancelled:
            # Barge-in / exit word: stop everything. The epoch bump already
            # cancelled every in-flight sentence task; the partial reply is
            # intentionally not broadcast (the user is talking over it).
            return

        await self._finish_llm_turn(
            text=text,
            response=result.full_response,
            decision=result.decision,
            delegation_question=result.delegation_question,
            stream_tts=stream_tts,
            force_jarvis_voice=True,
            reply_epoch=reply_epoch,
        )

    def _spawn_sentence_tts(self, sentence: str, seq: int, reply_session: int) -> None:
        """Synthesize one flushed sentence in the background and push it.

        The task runs concurrently with LLM streaming (sentence N synthesizes
        while the model generates sentence N+1). The browser plays by seq;
        an epoch bump (barge-in / exit word) cancels every in-flight task and
        the captured epoch makes any task that survives drop its result.
        Delegates to the shared ``tts_turn_common`` implementation.
        """
        self._ensure_tts_stream_state()
        spawn_sentence_tts(
            logger=logger,
            sentence=sentence,
            seq=seq,
            reply_session=reply_session,
            epoch=self._tts_sentence_epoch,
            tasks=self._tts_sentence_tasks,
            synthesize=self._synthesize_tts_sentence,
            queued_format="[tts-stream] sentence %d queued (session=%d, %d chars): '%s'",
        )

    async def _synthesize_tts_sentence(
        self, sentence: str, seq: int, reply_session: int, epoch: int
    ) -> None:
        """Fetch PCM16 for one sentence, wrap as WAV, push ``tts_sentence``.

        Guards with the sentence epoch captured at spawn time so audio
        synthesized after a barge-in / exit word is never pushed to the
        browser. Delegates to the shared ``tts_turn_common`` implementation.
        """
        await synthesize_tts_sentence(
            logger=logger,
            sentence=sentence,
            seq=seq,
            reply_session=reply_session,
            epoch=epoch,
            current_epoch=lambda: self._tts_sentence_epoch,
            fetch_pcm=self._fetch_tts_pcm,
            on_tts_sentence=self.on_tts_sentence,
            log_prefix="[tts-stream]",
        )

    async def _fetch_tts_pcm(self, text: str) -> bytes:
        """POST ``text`` to voice_clone_api :8985 and return PCM16 bytes."""
        return await fetch_tts_pcm(
            tts_api_url=self.config.tts_api_url,
            tts_voice_id=self.config.tts_voice_id,
            text=text,
        )

    @staticmethod
    def _wrap_pcm16_wav(pcm: bytes, sample_rate: int = 24000) -> bytes:
        """Wrap 24kHz mono PCM16 into a RIFF/WAVE container for <audio>."""
        return wrap_pcm16_wav(pcm, sample_rate=sample_rate)

    async def _finish_llm_turn(
        self,
        *,
        text: str,
        response: str,
        decision: str,
        delegation_question: str | None,
        stream_tts: bool,
        force_jarvis_voice: bool = False,
        reply_epoch: int | None = None,
    ) -> None:
        """Shared post-LLM turn completion (delegate, delegation, history, broadcast).

        Used by both the non-streaming path and the P0-A streaming consumer so
        the two converge on identical turn semantics. ``force_jarvis_voice``
        is set by the streaming path: its per-sentence audio is pushed via
        ``tts_sentence`` WS messages, so the ``llm_reply`` transcript must be
        tagged ``jarvis_voice`` to stop the browser from synthesizing the
        full reply again (even if every sentence task already finished).
        """
        logger.info("LLM response (decision=%s): '%s'", decision, response)

        # Turn Controller delegate (Phase B): feed the LLM response into the
        # controller so it tracks the agent turn (PROCESSING -> THINKING).
        # Gated on the delegate existing; fail-open. The whole response is
        # fed as one token (the controller only needs the PROCESSING ->
        # THINKING edge, not sentence-level fidelity).
        delegate = getattr(self, "_turn_delegate", None)
        if delegate is not None:
            try:
                delegate.on_llm_response_token(response)
            except Exception as exc:
                logger.warning("[turn-delegate] on_llm_response_token failed: %s", exc)
                # Fail-open contract: a wedged controller (stuck in PROCESSING
                # after a failed feed) would silently drop the next user turn,
                # so disable the delegate and fall back to legacy.
                self._turn_delegate = None

        # v3.37: when the model opted to delegate, fire BackgroundModelService
        # with the assistant's reply + the user's original ask as the
        # delegated question (webinfer extracts it from </delegation> Q).
        if decision == "delegation":
            try:
                bg = self._background_service
                if (
                    bg is not None
                    and getattr(bg, "enabled", True)
                    and not getattr(bg, "_closed", False)
                ):
                    payload_text = (response or "").strip() or text
                    if delegation_question:
                        payload_text = f"{payload_text}\n\n</delegation> {delegation_question}"
                    metrics = {"user_prompt": text, "delegation_question": delegation_question}
                    bg.handle_foreground_response(payload_text, metrics=metrics)
            except Exception as exc:
                logger.warning("delegation routing failed: %s", exc)
            # Skip TTS for delegated replies; the foreground line is empty
            # and the background agent will surface the real answer.
            stream_tts = False

        # v3.24: append to conversation history (turn-by-turn)
        self._conv_history.append(("user", text))
        self._conv_history.append(("assistant", response))

        # Tag the broadcast with whether the back-end also streamed TTS to the
        # WebRTC audio_output track, so the front-end can avoid double-playing.
        # P0-A: the streaming path pushes per-sentence audio via tts_sentence
        # and tags the transcript as jarvis_voice so the browser does NOT
        # synthesize the full reply again.
        reply_source = "jarvis_voice" if (force_jarvis_voice or stream_tts) else "jarvis_text"
        # P1: publish the turn's reply epoch on the state machine right before
        # the broadcast. `_finish_llm_turn` is synchronous from here to the
        # callback, so the session callback reads exactly this turn's value.
        self._ensure_tts_stream_state()
        if reply_epoch is None:
            reply_epoch = getattr(self, "_llm_reply_epoch", 0)
        self._current_turn_reply_epoch = reply_epoch
        if self.on_llm_response:
            self.on_llm_response(response, source=reply_source)

        # Stream TTS for true voice mode. Silence + delegation suppress TTS.
        if stream_tts and decision != "silence":
            self._tts_task = asyncio.create_task(self._stream_tts(response))
            self._notify_delegate_tts_started()
            self._tts_task.add_done_callback(self._on_delegate_tts_done)
        else:
            # No in-process TTS (browser plays via llm_reply / tts_sentence,
            # or silence / delegation): complete the agent turn in the
            # controller immediately so the next user turn commits cleanly.
            self._notify_delegate_tts_started()
            self._notify_delegate_tts_finished()

    def _notify_delegate_tts_started(self) -> None:
        """Feed TTS-started into the turn controller (Phase B; fail-open)."""
        delegate = getattr(self, "_turn_delegate", None)
        if delegate is None:
            return
        try:
            delegate.on_tts_started()
        except Exception as exc:
            logger.warning("[turn-delegate] on_tts_started failed: %s", exc)
            # Fail-open contract (see _send_to_llm hook): disable + legacy.
            self._turn_delegate = None

    def _notify_delegate_tts_finished(self) -> None:
        """Feed TTS-finished into the turn controller (Phase B; fail-open)."""
        delegate = getattr(self, "_turn_delegate", None)
        if delegate is None:
            return
        try:
            delegate.on_tts_finished()
        except Exception as exc:
            logger.warning("[turn-delegate] on_tts_finished failed: %s", exc)
            # Fail-open contract (see _send_to_llm hook): disable + legacy.
            self._turn_delegate = None

    def _on_delegate_tts_done(self, task: asyncio.Task) -> None:
        """Done-callback for the in-process TTS task (stream_tts=True).

        A cancelled task means the agent was interrupted (barge-in / exit) —
        the controller already knows via ``on_speech_started`` /
        ``on_dialog_reset``, so no spurious finish is fed.
        """
        if task.cancelled():
            return
        self._notify_delegate_tts_finished()

    async def _stream_tts(self, text: str):
        """Stream TTS audio via voice_clone_api /v1/synthesize.

        Returns PCM16 bytes (24kHz mono) and pushes them to audio_output.
        voice_id is bt-7274 (uploaded reference).
        """
        import httpx

        logger.debug("TTS streaming: '%s'", text[:60])
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    self.config.tts_api_url,
                    json={
                        "text": text,
                        "voice_id": self.config.tts_voice_id,
                        "streaming": False,
                        "sample_rate": 24000,
                    },
                )
                resp.raise_for_status()
                payload = resp.json()
                # voice_clone_api returns pcm16_base64 or audio (base64)
                audio_b64 = payload.get("pcm16_base64") or payload.get("audio")
                if not audio_b64:
                    logger.error("TTS response missing audio: %s", payload)
                    return
                pcm = base64.b64decode(audio_b64)
        except Exception as exc:
            logger.error("TTS failed: %s", exc)
            return

        # Push to audio output (WebRTC track or local fallback)
        if self.audio_output:
            try:
                await self.audio_output(pcm, 24000)
            except Exception as exc:
                logger.error("audio_output failed: %s", exc)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def _cleanup(self):
        if self._kws:
            self._kws.stop()
        if self._asr:
            self._asr.stop()
        if self._tts_task and not self._tts_task.done():
            self._tts_task.cancel()
        self._ensure_tts_stream_state()
        for task in list(self._tts_sentence_tasks):
            if not task.done():
                task.cancel()
        self._tts_sentence_tasks.clear()
        logger.info("Jarvis state machine cleaned up")


# ============================================================================
# Standalone test
# ============================================================================


async def _test_main():
    """Quick smoke test (requires sherpa-onnx models)."""
    import wave

    test_wav = Path("prompts/bt/events/wake.wav")  # or any 16kHz mono wav
    if not test_wav.exists():
        print(f"Test WAV not found: {test_wav}")
        return

    jarvis = JarvisStateMachine()
    bg = asyncio.create_task(jarvis.run())

    with wave.open(str(test_wav), "rb") as wf:
        assert wf.getframerate() == 16000, "16kHz only"
        chunk_size = 1600  # 100ms
        while True:
            data = wf.readframes(chunk_size // 2)
            if not data:
                break
            await jarvis.feed_audio(data)
            await asyncio.sleep(0.1)

    await asyncio.sleep(2)
    bg.cancel()
    print("Test done.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_test_main())
