"""Live mode — 免唤醒词常驻监听 interactive layer (Phase C, C.A).

``LiveStateMachine`` is the independent live driver (spec
``doc/specs/draft-live-interaction-layer.md`` §4.2). Unlike jarvis (KWS wake
chain + exit words, ``JarvisStateMachine``), live has **no wake gate**: the
user speaks and the agent answers. The shared core is reused, not forked:

  * :class:`~.turn_controller.TurnController` with ``TurnConfig.live()`` —
    the 13-state turn-taking FSM (resident LISTENING, barge-in arbitration,
    cooldown);
  * :class:`~.turn_controller.SentenceBuffer` + P0-A streaming consumer
    (:class:`~.turn_streaming.StreamingTurnConsumer`) — webinfer NDJSON
    stream, decision frame first, per-sentence TTS flush;
  * :class:`~.vad_bypass.VadBypass` (Silero, fail-open) — acoustic speech
    onset;
  * :class:`services.asr.jarvis.asr.JarvisASR` — streaming ASR (same model
    as jarvis).

Audio is pushed by the browser WebRTC mic track exactly like jarvis
(``feed_audio(pcm)``, 16 kHz mono int16). The agent reply is played by the
*browser* through the P0-A ``tts_sentence`` queue; this module synthesizes
per-sentence WAV and pushes ``tts_sentence`` via the session callback.

Event → state → action wiring (per turn):

  * VAD rising edge (or ASR partial growth when VAD is unavailable)
    → ``ctrl.on_speech_started(conf)`` — LISTENING → USER_SPEAKING, or
    SPEAKING/THINKING → HARD_INTERRUPTED (barge-in, ``on_barge_in`` fires).
  * ASR partial growth → ``ctrl.on_partial_transcript(text, is_final=False)``
    + ``asr_partial`` WS push (carries the live ``reply_epoch``).
  * ASR 2s-stall endpoint → ``ctrl.on_speech_stopped(silence_ms)``
    → USER_SPEAKING → PROCESSING + ``on_turn_commit`` → ``_send_to_llm``.
  * LLM stream → sentences → ``_spawn_sentence_tts`` → ``tts_sentence`` WS.
  * Barge-in (HARD_INTERRUPTED) → stop playback + bump reply_epoch
    (P1 guard) → COOLDOWN → (cooldown timer) → LISTENING.
  * Exit is browser-driven (no exit word): ``stop()`` → ``ctrl.end_turn()``.

Fail-open: every turn-controller / ASR / TTS / LLM error is logged
explicitly (约法三章 ③) and the session survives (never crashes the loop).

This module is independent from ``jarvis_mode.py``: jarvis is untouched.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import time
from collections import deque
from collections.abc import Callable
from typing import Any

from .jarvis_mode import _is_garbage_text
from .turn_controller import TurnConfig, TurnController, TurnState, TurnStateError
from .turn_streaming import StreamingTurnConsumer
from .vad_bypass import VadBypass

logger = logging.getLogger("joyai.live_mode")

#: Confidence fed to the turn controller when VAD (or ASR partial growth)
#: reports speech. Well above the live barge_in_threshold (0.75) so any real
#: VAD speech can hard-interrupt the agent turn — mirrors the jarvis
#: delegated path which feeds conf=0.9 on partial growth.
LIVE_VAD_SPEECH_CONF: float = 0.9

#: ASR staleness (seconds) that ends an utterance — identical to jarvis.
LIVE_ENDPOINT_TIMEOUT_S: float = 2.0

#: Minimum gated segment length (0.3s @ 16 kHz mono int16 = 9600 bytes) before
#: AddresseeDetector.classify is worth running — shorter blips are dropped.
_ADDRESSEE_MIN_SEGMENT_BYTES: int = 9600

#: Minimum enrollment segment duration (seconds) accepted from the mic stream.
_ADDRESSEE_ENROLL_MIN_SEGMENT_S: float = 1.0

#: Max enrollment segments collected (spec §3.1: 2-3 segments).
_ADDRESSEE_ENROLL_MAX_SEGMENTS: int = 3

# --- live visual context + proactive speak (spec draft-live-visual-cb.md) ---
# Env gates (all default OFF / conservative so default behavior is unchanged):
#   * ``LIVE_FRAME_WINDOW``        (int,   default 6)  recent-frame ring size;
#   * ``LIVE_PROACTIVE_ENABLED``   (bool,  default false) — proactive loop task;
#   * ``LIVE_PROACTIVE_INTERVAL_S``(float, default 5)  seconds between checks.
_DEFAULT_FRAME_WINDOW: int = 6
_DEFAULT_PROACTIVE_INTERVAL_S: float = 5.0


def _env_flag(name: str, default: bool = False) -> bool:
    """Read a boolean env gate (``1/true/yes/on`` are truthy)."""
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


class LiveStateMachine:
    """免唤醒词常驻监听 live driver: audio/VAD/ASR → turn controller → LLM → TTS.

    Owns a :class:`TurnController` (live preset) plus the ASR / VAD / TTS
    engines and the P0-A streaming reply path. Engines may be injected for
    tests; by default real engines are built lazily (ASR loads on first
    ``feed_audio``).
    """

    def __init__(
        self,
        config: Any | None = None,
        *,
        session_id: str = "",
        vad: VadBypass | None = None,
        asr: Any | None = None,
        controller: TurnController | None = None,
        on_asr_partial: Callable[[Any], None] | None = None,
        on_user_utterance: Callable[[str], None] | None = None,
        on_llm_response: Callable[[str, str], None] | None = None,
        on_tts_sentence: Callable[[str, int, str, int], None] | None = None,
        endpoint_timeout_s: float = LIVE_ENDPOINT_TIMEOUT_S,
        vad_speech_conf: float = LIVE_VAD_SPEECH_CONF,
    ) -> None:
        # Reuse the jarvis runtime config (LLM/TTS/ASR endpoints + BT-7274
        # persona prompt). Live ignores the KWS-only fields.
        self._config = config if config is not None else _default_config()
        self.session_id = session_id

        # Callbacks (bound by the session manager to WS pushes).
        self.on_asr_partial = on_asr_partial
        self.on_user_utterance = on_user_utterance
        self.on_llm_response = on_llm_response
        self.on_tts_sentence = on_tts_sentence
        # Interface parity with JarvisStateMachine; live replies are played
        # by the browser (tts_sentence queue), so this is never used.
        self.audio_output: Callable[[bytes, int], Any] | None = None

        # Turn controller (live preset — resident LISTENING). Callbacks are
        # always (re)bound so an injected controller (tests) gets the same
        # wiring as a self-created one.
        self._ctrl = controller if controller is not None else TurnController(TurnConfig.live())
        self._ctrl.on_turn_commit = self._on_turn_commit
        self._ctrl.on_barge_in = self._on_barge_in
        self._ctrl.on_timeout = self._on_timeout
        self._ctrl.on_pre_speech_filler = self._on_pre_speech_filler

        # Engines (injectable for tests). The default VAD is a fail-open
        # Silero VadBypass (same as jarvis): when disabled / model missing it
        # stays unavailable and ASR partial growth drives speech onset.
        if vad is None:
            vad = VadBypass(
                enabled=self._config.vad_enabled,
                model_dir=self._config.vad_model_dir,
                threshold=self._config.vad_threshold,
                min_silence_duration=self._config.vad_min_silence_duration,
                min_speech_duration=self._config.vad_min_speech_duration,
                window_size=self._config.vad_window_size,
            )
        self._vad = vad
        self._asr = asr
        self._prev_vad_speech: bool = False
        self._endpoint_timeout_s = endpoint_timeout_s
        self._vad_speech_conf = vad_speech_conf

        # ASR accumulation (same staleness rule as jarvis).
        self._current_asr_text: str = ""
        self._last_speech_time: float = 0.0

        # Conversation history (bounded FIFO, same as jarvis).
        self._conv_history: deque[tuple[str, str]] = deque(maxlen=20)
        self._max_history_turns: int = 10

        # P0-A streaming TTS state (mirrors JarvisStateMachine).
        self._tts_sentence_tasks: set[asyncio.Task] = set()
        self._tts_sentence_epoch: int = 0
        self._tts_reply_seq: int = 0
        self._llm_stream_cancel: bool = False
        self._tts_task: asyncio.Task | None = None
        self._sentence_spawned_this_turn: bool = False

        # P1 reply-epoch guard (late llm_reply suppression).
        self._llm_reply_epoch: int = 0
        self._current_turn_reply_epoch: int = 0

        # Pending-action flags drained by feed_audio (sync FSM → async bridge).
        self._commit_pending: bool = False
        self._barge_in_pending: bool = False
        self._barge_in_conf: float = 0.0

        # Cooldown / turn-completion watcher tasks.
        self._cooldown_task: asyncio.Task | None = None
        self._tts_turn_task: asyncio.Task | None = None

        # v3.37: webinfer decision="delegation" routing (BackgroundModelService).
        self._background_service: object | None = None

        # v3.40: Addressee Detection Phase 1 (acoustic pre-filter, spec
        # draft-addressee-detection.md). Env gate JARVIS_ADDRESSEE_DETECTOR_ENABLED
        # defaults OFF -> no detector, zero behavior change. When ON, lazily
        # build the CAM++ AddresseeDetector; load failure logs + keeps None
        # (fail-open: all audio passes through unchanged).
        self._addressee: Any | None = None
        self._addressee_model_dir: str = os.environ.get(
            "JARVIS_ADDRESSEE_MODEL_DIR",
            "D:/AI/models/sherpa-onnx/models/speaker",
        )
        if os.environ.get("JARVIS_ADDRESSEE_DETECTOR_ENABLED", "").lower() in (
            "1",
            "true",
            "yes",
            "on",
        ):
            try:
                from .addressee_detector import AddresseeDetector

                detector = AddresseeDetector(self._addressee_model_dir)
                if not detector.available:
                    logger.error("[addressee] detector unavailable; fail-open (all audio passes)")
                else:
                    self._addressee = detector
                    logger.info(
                        "[addressee] detector enabled (model_dir=%s)",
                        self._addressee_model_dir,
                    )
            except Exception as exc:  # fail-open on init error
                logger.error("[addressee] detector init failed (%s); fail-open", exc)

        # ENROLL phase (live_mode layer — NOT in the turn controller core).
        # During enrollment, mic audio goes to the enroll buffer only (no
        # ASR / dialog); the frontend drives start -> speak 3 utterances ->
        # finish via /api/live/enroll.
        self._enroll_phase: bool = False
        self._enroll_segments: list[bytes] = []
        self._enroll_in_seg: bool = False
        self._enroll_cur_segment: bytearray = bytearray()

        # Gated segment buffering: when the detector is active AND enrolled AND
        # a real VAD is available, a speech segment is buffered from the VAD
        # rising edge to the falling edge, then classified. Non-target segments
        # are dropped BEFORE ASR (no ASR / turn_controller work).
        self._addressee_in_seg: bool = False
        self._addressee_seg_buffer: bytearray = bytearray()

        # Live visual context (spec draft-live-visual-cb.md §2.2): recent-frame
        # ring buffer. Frames feed ONLY the current round — they never enter
        # conversation history (spec §2.6).
        self._frame_window: int = int(
            os.environ.get("LIVE_FRAME_WINDOW", str(_DEFAULT_FRAME_WINDOW)) or _DEFAULT_FRAME_WINDOW
        )
        if self._frame_window < 1:
            self._frame_window = _DEFAULT_FRAME_WINDOW
        self._recent_frames: deque[tuple[str, float]] = deque(maxlen=self._frame_window)

        # Proactive speak (spec §2.4): env-gated OFF by default so the default
        # behavior is zero change. When enabled, a background loop samples the
        # latest frame while LISTENING and asks webinfer whether there is
        # something worth saying.
        self._proactive_enabled: bool = _env_flag("LIVE_PROACTIVE_ENABLED", default=False)
        try:
            self._proactive_interval_s: float = float(
                os.environ.get("LIVE_PROACTIVE_INTERVAL_S", str(_DEFAULT_PROACTIVE_INTERVAL_S))
                or _DEFAULT_PROACTIVE_INTERVAL_S
            )
        except ValueError:
            self._proactive_interval_s = _DEFAULT_PROACTIVE_INTERVAL_S
        if self._proactive_interval_s <= 0:
            self._proactive_interval_s = _DEFAULT_PROACTIVE_INTERVAL_S
        self._proactive_task: asyncio.Task | None = None

        logger.info(
            "[live-mode] session %s initialized (state=%s, vad_threshold=%.2f, "
            "barge_in_threshold=%.2f, silence_timeout_ms=%d)",
            session_id,
            self._ctrl.state.name,
            self._ctrl.config.vad_threshold,
            self._ctrl.config.barge_in_threshold,
            self._ctrl.config.silence_timeout_ms,
        )
        logger.info(
            "[live-mode] visual context: frame_window=%d proactive_enabled=%s "
            "proactive_interval_s=%.1fs",
            self._frame_window,
            self._proactive_enabled,
            self._proactive_interval_s,
        )

    # ------------------------------------------------------------------
    # Introspection (browser state)
    # ------------------------------------------------------------------

    def get_state_for_browser(self) -> dict:
        """JSON-safe live state snapshot for the browser status pill."""
        state = self._ctrl.state
        return {
            "mode": "live",
            "turn_state": state.name,
            "listening": state == TurnState.LISTENING,
            "user_speaking": state == TurnState.USER_SPEAKING,
            "replying": state in (TurnState.PROCESSING, TurnState.THINKING),
            "speaking": state in (TurnState.SPEAKING, TurnState.PRE_SPEECH),
            "interrupted": state
            in (TurnState.HARD_INTERRUPTED, TurnState.SOFT_INTERRUPTED, TurnState.COOLDOWN),
            "reply_epoch": self._llm_reply_epoch,
            "enroll_phase": self._enroll_phase,
            "enroll_segment_count": self.enroll_segment_count,
            "addressee_enrolled": self.addressee_enrolled,
        }

    def is_active(self) -> bool:
        """True while the live session is not ended/errored."""
        return self._ctrl.state not in (TurnState.ENDED, TurnState.ERROR)

    @property
    def turn_state(self) -> TurnState:
        """The underlying turn controller state (tests + status)."""
        return self._ctrl.state

    # ------------------------------------------------------------------
    # Addressee detection (Phase 1) introspection
    # ------------------------------------------------------------------

    @property
    def addressee_enrolled(self) -> bool:
        """True when a target voice is registered in the active detector."""
        if self._addressee is None:
            return False
        try:
            return bool(self._addressee.is_enrolled())
        except Exception as exc:  # fail-open
            logger.error("[addressee] is_enrolled failed (%s); treated as not enrolled", exc)
            return False

    @property
    def enroll_segment_count(self) -> int:
        """Number of enrollment segments buffered so far (browser progress)."""
        return len(self._enroll_segments)

    @property
    def enroll_phase(self) -> bool:
        """True while the Addressee enrollment phase is active."""
        return self._enroll_phase

    # ------------------------------------------------------------------
    # Engine helpers
    # ------------------------------------------------------------------

    def _init_asr(self) -> None:
        if self._asr is not None:
            return
        from services.asr.jarvis.asr import JarvisASR

        self._asr = JarvisASR(
            model_dir=self._config.asr_model_dir,
            num_threads=self._config.asr_num_threads,
        )

    async def prewarm_engines(self) -> None:
        """Load the ASR model off the event loop (mirrors jarvis)."""
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._init_asr)
        logger.info("[live-mode] engines ready (asr=%s)", self._config.asr_model_dir)
        # Proactive speak loop (spec §2.4): started only when the env gate is
        # on. Default OFF -> no task -> zero behavior change.
        if self._proactive_enabled and self._proactive_task is None:
            self._proactive_task = asyncio.create_task(self._proactive_loop())
            logger.info(
                "[live-proactive] loop task started (interval=%.1fs, frame_window=%d)",
                self._proactive_interval_s,
                self._frame_window,
            )

    # ------------------------------------------------------------------
    # Live visual context (spec draft-live-visual-cb.md §2.2)
    # ------------------------------------------------------------------

    def handle_frame(self, image_b64: str, ts_ms: float) -> None:
        """Buffer one screen/camera frame as live visual context.

        Appends ``(image_b64, ts_ms)`` to the ring buffer (max
        ``LIVE_FRAME_WINDOW`` frames, oldest dropped on overflow). The buffer
        only feeds the CURRENT round — frames never enter conversation history
        (spec §2.6). ``ts_ms`` is a wall-clock millisecond timestamp.
        """
        self._recent_frames.append((image_b64, ts_ms))
        window_s = 0.0
        if len(self._recent_frames) >= 2:
            window_s = max(0.0, self._recent_frames[-1][1] - self._recent_frames[0][1]) / 1000.0
        logger.info(
            "[live-mode] frame captured (n=%d, window=%.1fs)",
            len(self._recent_frames),
            window_s,
        )

    @property
    def recent_frames(self) -> list[tuple[str, float]]:
        """Snapshot of the recent-frame ring buffer (tests + introspection)."""
        return list(self._recent_frames)

    @staticmethod
    def _frames_payload(frames) -> list[dict]:
        """Convert the internal ``(image_b64, ts_ms)`` buffer to the wire format.

        webinfer's live visual path (layer 1) expects ``frames`` as a list of
        ``{"image_b64": str, "ts_ms": int|float}`` objects (spec
        draft-live-visual-cb.md §2.1/§3). The ring buffer stores tuples for
        cheap deque rotation; the conversion happens only at the send seam.
        """
        return [{"image_b64": b64, "ts_ms": ts} for b64, ts in frames]

    def _reset_asr(self) -> None:
        """Reset the streaming ASR session + accumulated text for a new turn."""
        self._current_asr_text = ""
        self._last_speech_time = 0.0
        if self._asr is not None:
            try:
                self._asr.start()
            except Exception as exc:
                logger.warning("[live-mode] ASR stream reset failed: %s", exc)

    # ------------------------------------------------------------------
    # Addressee enrollment (Phase 1, live_mode layer)
    # ------------------------------------------------------------------

    def start_enroll(self) -> bool:
        """Enter the enrollment phase: mic audio buffers segments, no ASR/dialog.

        Returns False (with an explicit log) when the detector is unavailable —
        the caller should surface that to the user.
        """
        if self._addressee is None or not self._addressee.available:
            logger.error("[addressee] enroll rejected: detector unavailable (fail-open)")
            return False
        self._enroll_phase = True
        self._enroll_segments = []
        self._enroll_in_seg = False
        self._enroll_cur_segment = bytearray()
        logger.info("[addressee] enroll phase started (say 3 utterances)")
        return True

    def feed_enroll_pcm(self, pcm: bytes) -> None:
        """Append one complete enrollment utterance (whole PCM segment).

        Used by the ``/api/live/enroll {action: pcm}`` route (frontend pushes
        each recorded utterance). The WebRTC streaming path instead uses
        ``_feed_enroll_audio`` (VAD-segmented from feed_audio).
        """
        if not self._enroll_phase:
            logger.debug("[addressee] feed_enroll_pcm ignored: not in enroll phase")
            return
        if not pcm or len(pcm) % 2 != 0:
            logger.warning(
                "[addressee] feed_enroll_pcm invalid len=%d; ignored", len(pcm) if pcm else 0
            )
            return
        if len(self._enroll_segments) >= _ADDRESSEE_ENROLL_MAX_SEGMENTS:
            logger.info(
                "[addressee] enroll already has %d segments; extra ignored",
                len(self._enroll_segments),
            )
            return
        self._enroll_segments.append(pcm)
        logger.info(
            "[addressee] enroll segment %d buffered (%.2fs)",
            len(self._enroll_segments),
            len(pcm) / 32000.0,
        )

    def finish_enroll(self) -> bool:
        """Extract + register the buffered enrollment segments.

        On success the target voice is registered and the phase ends. On
        failure the phase also ends (buffer cleared) with an explicit error —
        the caller may retry with a fresh ``start_enroll``.
        """
        if not self._enroll_phase:
            logger.warning("[addressee] finish_enroll called without start_enroll")
            return False
        segments = list(self._enroll_segments)
        self._enroll_phase = False
        self._enroll_segments = []
        self._enroll_in_seg = False
        self._enroll_cur_segment = bytearray()
        if self._addressee is None or not self._addressee.available:
            logger.error("[addressee] finish_enroll failed: detector unavailable")
            return False
        ok = self._addressee.enroll(segments)
        if ok:
            logger.info("[addressee] enroll finished; voice registered")
        else:
            logger.error(
                "[addressee] enroll failed (need >=2 usable segments, got %d)",
                len(segments),
            )
        return ok

    def cancel_enroll(self) -> None:
        """Abort the enrollment phase and discard buffered segments."""
        self._enroll_phase = False
        self._enroll_segments = []
        self._enroll_in_seg = False
        self._enroll_cur_segment = bytearray()
        logger.info("[addressee] enroll cancelled")

    # ------------------------------------------------------------------
    # Turn controller callbacks (sync → pending flags)
    # ------------------------------------------------------------------

    def _on_turn_commit(self) -> None:
        self._commit_pending = True
        logger.info("[live-mode] action=on_turn_commit state=%s", self._ctrl.state.name)

    def _on_barge_in(self, conf: float) -> None:
        self._barge_in_pending = True
        self._barge_in_conf = conf
        logger.info(
            "[live-mode] action=on_barge_in conf=%.2f state=%s",
            conf,
            self._ctrl.state.name,
        )

    def _on_timeout(self) -> None:
        logger.info("[live-mode] action=on_timeout state=%s", self._ctrl.state.name)

    def _on_pre_speech_filler(self) -> None:
        # PRE_SPEECH filler is a Phase C post-item (spec §3.5); log only.
        logger.info("[live-mode] action=on_pre_speech_filler (Phase C: disabled)")

    # ------------------------------------------------------------------
    # Audio feed (browser WebRTC mic track → VAD + ASR → controller)
    # ------------------------------------------------------------------

    async def feed_audio(self, pcm: bytes) -> None:
        """Main live audio feed — drive the controller from mic frames.

        Called from the WebRTC audio consumer (same entry as jarvis). VAD
        rising edge and ASR partial growth both feed ``on_speech_started``;
        ASR 2s staleness feeds ``on_speech_stopped`` (commit / interrupt
        release); pending controller actions are drained at the end.

        Addressee gating (Phase 1): when a detector is active AND enrolled AND
        a real VAD is available, a speech segment is buffered from the VAD
        rising edge to the falling edge, then ``classify()``'d. Non-target
        segments are dropped before ASR (zero ASR / controller work);
        target segments are released to the ASR in sub-chunks. When the
        detector is absent / not enrolled / VAD unavailable -> the original
        streaming path is used (fail-open, zero behavior change).
        """
        now = time.time()

        # 0. ENROLL phase: audio goes to the enroll buffer only (no ASR /
        #    dialog). Returns early so the live loop stays quiet during
        #    registration.
        if self._enroll_phase:
            self._feed_enroll_audio(pcm, now)
            return

        addressee_gated = (
            self._addressee is not None
            and self._addressee.available
            and self.addressee_enrolled
            and self._vad is not None
            and self._vad.available
        )

        # When a gated segment was dropped, its closing (silence) chunk must
        # not reach the ASR either — the whole dropped segment is "not spoken".
        skip_asr_chunk = False

        # 1. VAD acoustic onset (rising edge). Fail-open: when the VAD is
        #    unavailable, ASR partial growth below is the speech signal.
        if self._vad is not None and self._vad.available and pcm and len(pcm) % 2 == 0:
            vad_speech = self._vad_speech(pcm)
            if addressee_gated:
                skip_asr_chunk = self._feed_addressee_vad(vad_speech, pcm, now)
            else:
                if vad_speech and not self._prev_vad_speech:
                    self._feed_speech_started()
                self._prev_vad_speech = vad_speech

        # 2. Streaming ASR (16 kHz mono int16). While a gated segment is being
        #    buffered, chunks are NOT fed to the ASR — they are released (or
        #    dropped) at the segment end by ``_gate_addressee_segment``.
        if not (addressee_gated and self._addressee_in_seg) and not skip_asr_chunk:
            self._feed_asr_chunk(pcm, now)

        # 3. Endpoint detection: ASR holds the last partial on silence; after
        #    the 2s stall the utterance ends (identical rule to jarvis).
        if (
            self._current_asr_text
            and (now - self._last_speech_time) >= self._endpoint_timeout_s
            and self._ctrl.state
            in (TurnState.USER_SPEAKING, TurnState.HARD_INTERRUPTED, TurnState.SOFT_INTERRUPTED)
        ):
            silence_ms = int((now - self._last_speech_time) * 1000)
            logger.info(
                "[live-mode] ASR endpoint silence=%dms text=%r",
                silence_ms,
                self._current_asr_text[:80],
            )
            try:
                self._ctrl.on_speech_stopped(silence_ms)
            except TurnStateError as exc:
                logger.info("[live-mode] on_speech_stopped rejected: %s", exc)
            except Exception as exc:
                logger.error("[live-mode] on_speech_stopped failed: %s", exc)
            if self._ctrl.state == TurnState.COOLDOWN:
                # Post-interrupt release: the user stopped; reset the ASR so
                # the next utterance starts a fresh stream (no stale partial).
                self._reset_asr()

        # 4. Drain pending controller actions (commit / barge-in) and keep
        #    the cooldown timer alive.
        await self._drain_pending()

    def _vad_speech(self, pcm: bytes) -> bool:
        """Feed one PCM chunk to the Silero VAD and return its speech state."""
        try:
            import numpy as np

            float32 = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
            self._vad.accept_waveform(float32)
            return bool(self._vad.is_speech())
        except Exception as exc:
            logger.debug("[live-mode] VAD annotation failed (%s); treat as speech", exc)
            return True

    def _feed_asr_chunk(self, pcm: bytes, now: float) -> str:
        """Feed one PCM chunk into the streaming ASR and push partials.

        Shared by the streaming path and the addressee-gated release path.
        Returns the current ASR text ("" on failure, fail-open).
        """
        if self._asr is None:
            try:
                self._init_asr()
            except Exception as exc:
                logger.error("[live-mode] ASR init failed: %s", exc)
        text = ""
        if self._asr is not None:
            try:
                text = self._asr.feed_chunk(pcm) or ""
            except Exception as exc:
                logger.error("[live-mode] ASR feed_chunk failed: %s", exc)
                text = ""

        if text and text != self._current_asr_text:
            self._current_asr_text = text
            self._last_speech_time = now
            # Speech signal: partial growth means the user is talking (also
            # the backstop when the VAD is unavailable).
            self._feed_speech_started()
            self._feed_partial_transcript(text, is_final=False)
            self._push_asr_partial(text, is_final=False)
        return text

    def _feed_addressee_vad(self, vad_speech: bool, pcm: bytes, now: float) -> bool:
        """Buffer a VAD speech segment; classify it at the falling edge.

        Rising edge -> start the buffer; speech chunks -> extend it; falling
        edge -> ``_gate_addressee_segment`` releases (target) or drops
        (non-target). Runs only when the detector is active AND enrolled AND a
        real VAD is available.

        Returns True when the current chunk belongs to a DROPPED segment (its
        closing chunk must not reach the ASR); False otherwise.
        """
        if vad_speech and not self._prev_vad_speech:
            self._addressee_in_seg = True
            self._addressee_seg_buffer = bytearray(pcm)
        elif vad_speech and self._addressee_in_seg:
            self._addressee_seg_buffer.extend(pcm)
        elif not vad_speech and self._addressee_in_seg:
            self._addressee_in_seg = False
            segment = bytes(self._addressee_seg_buffer)
            self._addressee_seg_buffer = bytearray()
            dropped = self._gate_addressee_segment(segment, now)
            self._prev_vad_speech = vad_speech
            return dropped
        self._prev_vad_speech = vad_speech
        return False

    def _gate_addressee_segment(self, segment: bytes, now: float) -> bool:
        """Classify one buffered VAD segment and release/drop it.

        * too short -> drop (no ASR / controller work);
        * non-target -> drop + ``[addressee] non-target dropped`` log;
        * target -> drive speech onset then release to ASR in sub-chunks.
        * any exception -> fail-open (treat as target, keep the segment).

        Returns True when the segment was dropped (caller skips ASR for the
        closing chunk).
        """
        if len(segment) < _ADDRESSEE_MIN_SEGMENT_BYTES:
            logger.debug(
                "[addressee] segment too short (%.2fs); dropped",
                len(segment) / 32000.0,
            )
            return True
        try:
            is_target, score = self._addressee.classify(segment)
        except Exception as exc:  # fail-open: never drop on error
            logger.error("[addressee] classify failed (%s); fail-open", exc)
            is_target, score = True, 1.0

        if not is_target:
            logger.info(
                "[addressee] non-target dropped (score=%.2f, len=%.2fs)",
                score,
                len(segment) / 32000.0,
            )
            return True

        logger.info(
            "[addressee] target passed (score=%.2f, len=%.2fs)",
            score,
            len(segment) / 32000.0,
        )
        # Speech onset: LISTENING -> USER_SPEAKING (or barge-in release).
        self._feed_speech_started()
        # Release the buffered utterance to ASR in ~100ms sub-chunks so the
        # streaming partial path behaves like the unfiltered path.
        chunk = 3200  # 100ms @ 16 kHz mono int16
        for i in range(0, len(segment), chunk):
            self._feed_asr_chunk(segment[i : i + chunk], now)
        return False

    def _feed_enroll_audio(self, pcm: bytes, now: float) -> None:
        """VAD-segment the mic stream into enrollment utterances.

        Called from ``feed_audio`` while ``enroll_phase`` is active. Each
        completed speech segment (>=1s) is appended to ``_enroll_segments``
        (up to 3); silence between utterances is discarded.
        """
        if self._vad is None or not self._vad.available or not pcm or len(pcm) % 2 != 0:
            return
        vad_speech = self._vad_speech(pcm)
        if vad_speech and not self._prev_vad_speech:
            self._enroll_in_seg = True
            self._enroll_cur_segment = bytearray(pcm)
        elif vad_speech and self._enroll_in_seg:
            self._enroll_cur_segment.extend(pcm)
        elif not vad_speech and self._enroll_in_seg:
            self._enroll_in_seg = False
            segment = bytes(self._enroll_cur_segment)
            self._enroll_cur_segment = bytearray()
            duration_s = len(segment) / 32000.0
            if (
                duration_s >= _ADDRESSEE_ENROLL_MIN_SEGMENT_S
                and len(self._enroll_segments) < _ADDRESSEE_ENROLL_MAX_SEGMENTS
            ):
                self._enroll_segments.append(segment)
                logger.info(
                    "[addressee] enroll segment %d captured (%.2fs)",
                    len(self._enroll_segments),
                    duration_s,
                )
        self._prev_vad_speech = vad_speech

    def _feed_speech_started(self) -> None:
        """Feed VAD/ASR speech onset into the controller (fail-open)."""
        try:
            self._ctrl.on_speech_started(self._vad_speech_conf)
        except TurnStateError as exc:
            logger.info(
                "[live-mode] on_speech_started rejected in state=%s: %s",
                self._ctrl.state.name,
                exc,
            )
        except Exception as exc:
            logger.error("[live-mode] on_speech_started failed: %s", exc)

    def _feed_partial_transcript(self, text: str, is_final: bool) -> None:
        """Feed an ASR partial/final into the controller (fail-open)."""
        try:
            self._ctrl.on_partial_transcript(text, is_final=is_final)
        except TurnStateError as exc:
            logger.info(
                "[live-mode] on_partial_transcript rejected in state=%s: %s",
                self._ctrl.state.name,
                exc,
            )
        except Exception as exc:
            logger.error("[live-mode] on_partial_transcript failed: %s", exc)

    def _push_asr_partial(self, text: str, is_final: bool) -> None:
        """Broadcast an ASR partial to the browser (carries reply_epoch)."""
        if self.on_asr_partial is None:
            return
        try:
            from .jarvis_mode import AsrPartial

            self.on_asr_partial(
                AsrPartial(
                    text=text,
                    is_final=is_final,
                    timestamp_ms=time.time() * 1000,
                    reply_epoch=self._llm_reply_epoch,
                )
            )
        except Exception as exc:
            logger.warning("[live-mode] asr_partial broadcast failed: %s", exc)

    async def _drain_pending(self) -> None:
        """Drain pending controller actions and schedule the cooldown timer."""
        if self._barge_in_pending:
            self._barge_in_pending = False
            await self._handle_barge_in(self._barge_in_conf)
        if self._commit_pending:
            self._commit_pending = False
            await self._handle_commit()
        if self._ctrl.state == TurnState.COOLDOWN and (
            self._cooldown_task is None or self._cooldown_task.done()
        ):
            self._cooldown_task = asyncio.create_task(self._cooldown_timer())

    async def _cooldown_timer(self) -> None:
        """COOLDOWN guard window elapsed → LISTENING."""
        try:
            await asyncio.sleep(self._ctrl.config.cooldown_ms / 1000.0)
            self._ctrl.on_cooldown_elapsed()
            logger.info("[live-mode] cooldown elapsed; listening")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("[live-mode] cooldown timer failed: %s", exc)

    # ------------------------------------------------------------------
    # Turn commit → LLM
    # ------------------------------------------------------------------

    async def _handle_commit(self) -> None:
        """Endpoint commit: send the accumulated utterance to the LLM (live)."""
        utterance = self._current_asr_text
        self._reset_asr()

        if _is_garbage_text(utterance):
            logger.info("[live-mode] ASR endpoint reached, dropping garbage: %r", utterance)
            self._realign_controller_to_listening()
            return

        logger.info("[live-mode] ASR endpoint reached, sending to LLM: %r", utterance)
        if self.on_user_utterance:
            try:
                self.on_user_utterance(utterance)
            except Exception as exc:
                logger.warning("[live-mode] on_user_utterance failed: %s", exc)
        try:
            await self._send_to_llm(
                utterance,
                interaction_mode="live",
                stream=True,
                frames=self._frames_payload(self._recent_frames),
            )
        except Exception as exc:
            logger.error("[live-mode] _send_to_llm failed; fail-open: %s", exc)
            self._realign_controller_to_listening()

    def _realign_controller_to_listening(self) -> None:
        """Drive the controller back to LISTENING after a declined turn."""
        try:
            self._ctrl.on_llm_token("")
            self._ctrl.on_tts_started()
            self._ctrl.on_tts_finished()
        except Exception as exc:
            logger.warning("[live-mode] controller realign failed: %s", exc)

    async def _send_to_llm(
        self,
        text: str,
        *,
        interaction_mode: str = "live",
        stream: bool = True,
        frames: list | None = None,
    ) -> None:
        """Send ASR text to webinfer (interaction_mode='live', stream=True).

        Reuses the shared :class:`StreamingTurnConsumer` (P0-A) so live and
        jarvis converge on one streaming path. ``frames`` (optional
        ``[{image_b64, ts_ms}]``) routes the round through webinfer's live
        visual path (layer 1). Fail-open: pre-frame stream failure falls back
        to the single-shot call; mid-stream failure keeps the flushed
        sentences (consumer already handled).
        """
        self._llm_reply_epoch += 1
        turn_reply_epoch = self._llm_reply_epoch
        logger.info("[live-mode] turn-start reply_epoch bumped to %d", turn_reply_epoch)

        self._llm_stream_cancel = False
        self._sentence_spawned_this_turn = False
        reply_session = self._tts_reply_seq
        self._tts_reply_seq += 1

        history_snapshot = list(self._conv_history)[-self._max_history_turns * 2 :]
        consumer = StreamingTurnConsumer(
            endpoint_url=f"{self._config.llm_api_url}{self._config.llm_text_path}",
            model=self._config.llm_model,
            system_prompt=self._config.llm_system_prompt,
            history_snapshot=history_snapshot,
            max_tokens=200,
            temperature=0.7,
            timeout_s=30.0,
            on_sentence=self._spawn_sentence_tts,
            is_cancelled=lambda: self._llm_stream_cancel,
            stream_logger=logger,
            frames=frames,
        )
        result = await consumer.consume(
            text,
            interaction_mode=interaction_mode,
            reply_session=reply_session,
        )

        if result.needs_non_streaming_retry:
            logger.info("[live-mode] fail-open -> non-streaming retry")
            await self._send_to_llm_non_streaming(
                text,
                interaction_mode=interaction_mode,
                reply_epoch=turn_reply_epoch,
                frames=frames,
            )
            return
        if result.cancelled:
            # Barge-in: the epoch bump already cancelled every in-flight
            # sentence task; the partial reply is intentionally not broadcast.
            logger.info("[live-mode] LLM stream cancelled (barge-in); skipping broadcast")
            return

        await self._finish_llm_turn(
            text=text,
            response=result.full_response,
            decision=result.decision,
            delegation_question=result.delegation_question,
            reply_epoch=turn_reply_epoch,
        )

    async def _send_to_llm_non_streaming(
        self,
        text: str,
        *,
        interaction_mode: str = "live",
        reply_epoch: int | None = None,
        frames: list | None = None,
    ) -> None:
        """Single-shot LLM fallback (fail-open, never lose the reply)."""
        messages: list[dict] = [{"role": "system", "content": self._config.llm_system_prompt}]
        for role, content in list(self._conv_history)[-self._max_history_turns * 2 :]:
            messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": text})

        response = ""
        decision = "silence"
        delegation_question = None
        try:
            import httpx

            request_body: dict = {
                "model": self._config.llm_model,
                "messages": messages,
                "max_tokens": 200,
                "temperature": 0.7,
                "interaction_mode": interaction_mode,
            }
            if frames:
                request_body["frames"] = frames
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{self._config.llm_api_url}{self._config.llm_text_path}",
                    json=request_body,
                )
                resp.raise_for_status()
                payload = resp.json()
                choice = (payload.get("choices") or [{}])[0]
                response = (choice.get("message") or {}).get("content") or ""
                response = response.strip() if isinstance(response, str) else ""
                harness = payload.get("streamingharness") or {}
                decision = harness.get("decision") or ("response" if response else "silence")
                delegation_question = harness.get("delegation_question")
        except Exception as exc:
            logger.error("[live-mode] non-streaming LLM call failed: %s", exc)
            response = ""
            decision = "silence"

        await self._finish_llm_turn(
            text=text,
            response=response,
            decision=decision,
            delegation_question=delegation_question,
            reply_epoch=reply_epoch,
        )

    # ------------------------------------------------------------------
    # Proactive speak (spec draft-live-visual-cb.md §2.4)
    # ------------------------------------------------------------------

    async def _proactive_loop(self) -> None:
        """Periodic VLM visual check while LISTENING (env-gated).

        Every ``LIVE_PROACTIVE_INTERVAL_S`` seconds, when the controller is in
        LISTENING and at least one frame is buffered, sample the LATEST frame
        and ask webinfer (non-streaming, no user text) whether there is
        something worth saying:

          * decision=response -> proactively speak via the sentence-TTS path
            and drive the controller into SPEAKING;
          * silence / not-for-me -> stay quiet and wait for the next interval;
          * any non-LISTENING state (USER_SPEAKING, SPEAKING, ...) pauses the
            loop until the controller returns to LISTENING.

        Exceptions fail open (log + skip the round) so proactive work never
        disturbs the user dialog (约法三章).
        """
        logger.info(
            "[live-proactive] loop started (interval=%.1fs, frame_window=%d)",
            self._proactive_interval_s,
            self._frame_window,
        )
        while True:
            try:
                await asyncio.sleep(self._proactive_interval_s)
                if self.turn_state != TurnState.LISTENING:
                    continue
                if not self._recent_frames:
                    continue
                await self._send_proactive_prompt(
                    frames=self._frames_payload([self._recent_frames[-1]])
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("[live-proactive] loop round failed; fail-open: %s", exc)

    async def _send_proactive_prompt(self, *, frames: list) -> None:
        """Ask webinfer whether the latest frame deserves a spoken comment.

        Lightweight non-streaming VLM round: no user text, small max_tokens.
        decision=response -> speak the reply proactively (sentence TTS path,
        controller -> SPEAKING -> LISTENING via ``_wait_tts_turn_done``);
        silence / not-for-me / empty -> stay quiet. Any failure fails open
        (log + skip) and never disturbs the user dialog.
        """
        reply_session = self._tts_reply_seq
        self._tts_reply_seq += 1
        self._llm_reply_epoch += 1
        logger.info(
            "[live-proactive] VLM visual check (frames=%d, reply_epoch=%d)",
            len(frames),
            self._llm_reply_epoch,
        )
        decision, response = await self._call_proactive_vlm(frames)
        logger.info(
            "[live-proactive] VLM decision=%s response=%r",
            decision,
            (response or "")[:80],
        )
        if decision != "response" or not (response or "").strip():
            logger.info("[live-proactive] staying quiet (decision=%s)", decision)
            # Controller: THINKING -> SPEAKING -> LISTENING (nothing spoken).
            try:
                self._ctrl.on_tts_started()
                self._ctrl.on_tts_finished()
            except Exception as exc:
                logger.warning("[live-proactive] controller quiet feed failed: %s", exc)
            return

        # Proactive speak: agent-initiated turn (LISTENING -> THINKING ->
        # SPEAKING), then synthesize + push the single sentence through the
        # normal TTS path. Barge-in during playback reuses the existing
        # HARD_INTERRUPTED path.
        try:
            self._ctrl.on_agent_turn_started()
            self._ctrl.on_tts_started()
        except Exception as exc:
            logger.warning("[live-proactive] controller feed failed: %s", exc)
        self._spawn_sentence_tts(response, 0, reply_session)
        if self.on_llm_response:
            try:
                self.on_llm_response(response, source="live_proactive")
            except Exception as exc:
                logger.warning("[live-proactive] on_llm_response failed: %s", exc)
        self._tts_turn_task = asyncio.create_task(self._wait_tts_turn_done())

    async def _call_proactive_vlm(self, frames: list) -> tuple[str, str]:
        """POST a lightweight non-streaming VLM visual round to webinfer.

        Returns ``(decision, response)``. Fail-open: on any error log and
        return ``("silence", "")`` so a proactive round never disturbs the
        dialog.
        """
        import httpx

        messages: list[dict] = [{"role": "system", "content": self._config.llm_system_prompt}]
        messages.append({"role": "user", "content": ""})
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{self._config.llm_api_url}{self._config.llm_text_path}",
                    json={
                        "model": self._config.llm_model,
                        "messages": messages,
                        "max_tokens": 256,
                        "temperature": 0.7,
                        "interaction_mode": "live",
                        "frames": frames,
                    },
                )
                resp.raise_for_status()
                payload = resp.json()
                choice = (payload.get("choices") or [{}])[0]
                response = (choice.get("message") or {}).get("content") or ""
                response = response.strip() if isinstance(response, str) else ""
                harness = payload.get("streamingharness") or {}
                decision = harness.get("decision") or ("response" if response else "silence")
                return decision, response
        except Exception as exc:
            logger.error("[live-proactive] VLM call failed; fail-open: %s", exc)
            return "silence", ""

    async def _finish_llm_turn(
        self,
        *,
        text: str,
        response: str,
        decision: str,
        delegation_question: str | None,
        reply_epoch: int | None = None,
    ) -> None:
        """Shared post-LLM turn completion (controller, delegation, broadcast)."""
        logger.info("[live-mode] LLM response (decision=%s): %r", decision, (response or "")[:120])

        # Feed the controller: PROCESSING -> THINKING (even for empty output).
        try:
            self._ctrl.on_llm_token(response or "")
        except Exception as exc:
            logger.warning("[live-mode] on_llm_token failed: %s", exc)

        # v3.37: webinfer decision="delegation" routes to BackgroundModelService
        # (same sub-agent as jarvis / video).
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
                    bg.handle_foreground_response(
                        payload_text,
                        metrics={"user_prompt": text, "delegation_question": delegation_question},
                    )
            except Exception as exc:
                logger.warning("[live-mode] delegation routing failed: %s", exc)
            # Delegation replies are surfaced by the background agent; nothing
            # is spoken (foreground line empty).
            decision = "silence"

        # Addressee-detection Phase 2 (spec draft-addressee-detection.md
        # §4.1/§4.2.4): decision="not-for-me" means the utterance was NOT
        # addressed to the AI (self-talk / replying to someone else / talking
        # to another person). Treat it as a non-target turn: no TTS, back to
        # LISTENING — same controller path as silence, with a dedicated log
        # so the semantic gate is distinguishable from plain silence.
        if decision == "not-for-me":
            logger.info(
                "[addressee] semantic not-for-me: utterance=%r not addressed to AI; not broadcasting",
                (text or "")[:80],
            )

        self._conv_history.append(("user", text))
        self._conv_history.append(("assistant", response or ""))

        # P0-A: the streaming path pushed per-sentence audio via tts_sentence,
        # so the llm_reply transcript must NOT be re-synthesized by the
        # browser (source tag mirrors jarvis_voice). live_voice is ONLY valid
        # when sentences were actually spawned (streaming path); the
        # fail-open non-streaming retry spawns no sentences, so it must tag
        # live_text and let the browser synthesize audio via /api/tts/synthesize
        # (BUG-1: previously a silent reply on the retry path).
        reply_source = (
            "live_voice"
            if (decision == "response" and self._sentence_spawned_this_turn)
            else "live_text"
        )
        self._current_turn_reply_epoch = (
            reply_epoch if reply_epoch is not None else self._llm_reply_epoch
        )
        if self.on_llm_response:
            try:
                self.on_llm_response(response or "", source=reply_source)
            except Exception as exc:
                logger.warning("[live-mode] on_llm_response failed: %s", exc)

        if decision == "response" and self._sentence_spawned_this_turn:
            # THINKING -> SPEAKING (sentences already synthesizing/playing).
            try:
                self._ctrl.on_tts_started()
            except Exception as exc:
                logger.warning("[live-mode] on_tts_started failed: %s", exc)
            self._tts_turn_task = asyncio.create_task(self._wait_tts_turn_done())
        else:
            # silence / delegation / not-for-me / empty: THINKING -> SPEAKING
            # -> LISTENING (nothing is spoken; not-for-me additionally logged
            # as a semantic non-target above).
            try:
                self._ctrl.on_tts_started()
                self._ctrl.on_tts_finished()
                logger.info("[live-mode] agent turn finished (no TTS); listening")
            except Exception as exc:
                logger.warning("[live-mode] tts lifecycle feed failed: %s", exc)

    async def _wait_tts_turn_done(self) -> None:
        """Wait for all sentence TTS tasks, then return the controller to
        LISTENING (SPEAKING -> LISTENING)."""
        try:
            while True:
                pending = [t for t in list(self._tts_sentence_tasks) if not t.done()]
                if not pending:
                    break
                await asyncio.gather(*pending, return_exceptions=True)
        except asyncio.CancelledError:
            raise
        if self._ctrl.state == TurnState.SPEAKING:
            try:
                self._ctrl.on_tts_finished()
                logger.info("[live-mode] agent turn finished; listening")
            except Exception as exc:
                logger.warning("[live-mode] on_tts_finished failed: %s", exc)

    # ------------------------------------------------------------------
    # P0-A per-sentence TTS (reuses the jarvis synthesis + push pattern)
    # ------------------------------------------------------------------

    def _spawn_sentence_tts(self, sentence: str, seq: int, reply_session: int) -> None:
        """Synthesize one flushed sentence in the background and push it.

        The task runs concurrently with LLM streaming; an epoch bump
        (barge-in / stop) cancels every in-flight task and the captured epoch
        makes any survivor drop its result.
        """
        self._sentence_spawned_this_turn = True
        epoch = self._tts_sentence_epoch
        task = asyncio.create_task(
            self._synthesize_tts_sentence(sentence, seq, reply_session, epoch)
        )
        self._tts_sentence_tasks.add(task)
        task.add_done_callback(self._tts_sentence_tasks.discard)
        logger.info(
            "[live-mode] sentence %d queued (session=%d, %d chars): %r",
            seq,
            reply_session,
            len(sentence),
            sentence[:60],
        )

    async def _synthesize_tts_sentence(
        self, sentence: str, seq: int, reply_session: int, epoch: int
    ) -> None:
        """Fetch PCM16 for one sentence, wrap as WAV, push ``tts_sentence``."""
        if self._tts_sentence_epoch != epoch:
            return
        t0 = time.time()
        logger.info(
            "[live-mode] sentence %d TTS synth start (session=%d, %d chars)",
            seq,
            reply_session,
            len(sentence),
        )
        try:
            pcm = await self._fetch_tts_pcm(sentence)
        except Exception as exc:
            logger.error(
                "[live-mode] sentence %d TTS failed after %.0fms: %s",
                seq,
                (time.time() - t0) * 1000,
                exc,
            )
            return
        if self._tts_sentence_epoch != epoch:
            logger.debug("[live-mode] sentence %d stale (epoch bumped); dropping", seq)
            return
        logger.info(
            "[live-mode] sentence %d TTS ok (%.0fms, %d PCM bytes)",
            seq,
            (time.time() - t0) * 1000,
            len(pcm),
        )
        try:
            wav = self._wrap_pcm16_wav(pcm, sample_rate=24000)
            audio_b64 = base64.b64encode(wav).decode("ascii")
        except Exception as exc:
            logger.error("[live-mode] sentence %d WAV wrap failed: %s", seq, exc)
            return
        if self.on_tts_sentence:
            try:
                self.on_tts_sentence(sentence, seq, audio_b64, reply_session)
                logger.info(
                    "[live-mode] sentence %d pushed (session=%d, total %.0fms)",
                    seq,
                    reply_session,
                    (time.time() - t0) * 1000,
                )
            except Exception as exc:
                logger.warning("[live-mode] tts_sentence callback failed: %s", exc)

    async def _fetch_tts_pcm(self, text: str) -> bytes:
        """POST ``text`` to voice_clone_api and return PCM16 bytes."""
        import httpx

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                self._config.tts_api_url,
                json={
                    "text": text,
                    "voice_id": self._config.tts_voice_id,
                    "streaming": False,
                    "sample_rate": 24000,
                },
            )
            resp.raise_for_status()
            payload = resp.json()
            audio_b64 = payload.get("pcm16_base64") or payload.get("audio")
            if not audio_b64:
                raise ValueError(f"TTS response missing audio: {payload}")
            return base64.b64decode(audio_b64)

    @staticmethod
    def _wrap_pcm16_wav(pcm: bytes, sample_rate: int = 24000) -> bytes:
        """Wrap 24kHz mono PCM16 into a RIFF/WAVE container for <audio>."""
        n_channels = 1
        bits_per_sample = 16
        byte_rate = sample_rate * n_channels * bits_per_sample // 8
        block_align = n_channels * bits_per_sample // 8
        data_size = len(pcm)
        header = b"RIFF" + (36 + data_size).to_bytes(4, "little") + b"WAVE"
        header += b"fmt " + (16).to_bytes(4, "little")
        header += (1).to_bytes(2, "little")  # PCM
        header += n_channels.to_bytes(2, "little")
        header += sample_rate.to_bytes(4, "little")
        header += byte_rate.to_bytes(4, "little")
        header += block_align.to_bytes(2, "little")
        header += bits_per_sample.to_bytes(2, "little")
        header += b"data" + data_size.to_bytes(4, "little")
        return header + pcm

    # ------------------------------------------------------------------
    # Barge-in / stop
    # ------------------------------------------------------------------

    async def _handle_barge_in(self, conf: float) -> None:
        """User spoke over the agent turn: stop playback, expire the reply."""
        self._tts_sentence_epoch += 1
        self._llm_reply_epoch += 1
        logger.info(
            "[live-mode] barge-in conf=%.2f: sentence_epoch=%d reply_epoch=%d",
            conf,
            self._tts_sentence_epoch,
            self._llm_reply_epoch,
        )
        self._llm_stream_cancel = True
        for task in list(self._tts_sentence_tasks):
            if not task.done():
                task.cancel()
        self._tts_sentence_tasks.clear()
        if self._tts_task is not None and not self._tts_task.done():
            self._tts_task.cancel()
            self._tts_task = None
        # Keep the interrupting utterance accumulated so the ASR-stall
        # endpoint can release the controller (HARD_INTERRUPTED -> COOLDOWN).
        logger.debug("[live-mode] TTS paused (barge-in)")

    async def stop(self) -> None:
        """Stop the live session: cancel tasks, end the turn, close ASR.

        Live has no exit word — the browser calls this when the user leaves
        the live mode (``/api/live/stop``).
        """
        self._llm_stream_cancel = True
        self._tts_sentence_epoch += 1
        self._llm_reply_epoch += 1
        for task in list(self._tts_sentence_tasks):
            if not task.done():
                task.cancel()
        self._tts_sentence_tasks.clear()
        if self._tts_task is not None and not self._tts_task.done():
            self._tts_task.cancel()
            self._tts_task = None
        if self._cooldown_task is not None and not self._cooldown_task.done():
            self._cooldown_task.cancel()
            self._cooldown_task = None
        if self._tts_turn_task is not None and not self._tts_turn_task.done():
            self._tts_turn_task.cancel()
            self._tts_turn_task = None
        if self._proactive_task is not None and not self._proactive_task.done():
            self._proactive_task.cancel()
            self._proactive_task = None
            logger.info("[live-proactive] loop task cancelled (session stop)")
        if self._ctrl.state != TurnState.ENDED:
            try:
                self._ctrl.end_turn()
            except Exception as exc:
                logger.warning("[live-mode] end_turn failed during stop: %s", exc)
        if self._asr is not None:
            try:
                self._asr.stop()
            except Exception as exc:
                logger.warning("[live-mode] ASR stop failed: %s", exc)
        logger.info("[live-mode] session %s stopped", self.session_id)


def _default_config() -> Any:
    """Build the runtime config (env-overridable, jarvis-compatible)."""
    from .jarvis_mode import JarvisConfig

    return JarvisConfig.from_env()


__all__ = [
    "LIVE_ENDPOINT_TIMEOUT_S",
    "LIVE_VAD_SPEECH_CONF",
    "LiveStateMachine",
]
