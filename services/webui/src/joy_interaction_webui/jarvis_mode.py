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
import math
import os
import re
import time
import wave
from array import array
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path

from .smart_turn_adapter import SmartTurnAdapter
from .vad_bypass import VadBypass

logger = logging.getLogger("joyai.jarvis")


# ============================================================================
# Configuration
# ============================================================================

# Collapse runs of non-word characters (spaces, punctuation, CJK punctuation,
# emoji, etc.) to a single space when normalising ASR confirm text.
_ASR_CONFIRM_NON_WORD = re.compile(r"[^\w]+", flags=re.UNICODE)

EXIT_WORDS = {"明白", "了解", "ok", "好的", "知道了"}
"""Words that signal "I’m done talking" — treated as end-of-conversation signal."""

_GARBAGE_PUNCT_ONLY = {
    "\u3002",
    "\u3001",
    "\uff01",
    "\uff1f",
    "\uff1a",
    "\uff1b",
    "\u00b7",
    "\u2026",
    "\u2014",
    "\uff5e",
    "`",
    "~",
    "!",
    "?",
    ",",
    ".",
    ":",
    ";",
    "'",
    "-",
    "/",
    "\\",
    "(",
    ")",
    "[",
    "]",
    "{",
    "}",
    "<",
    ">",
    "\u0022",
    "*",
    "&",
    "#",
    "%",
    "@",
    "^",
    "_",
    "+",
    "=",
    " ",
}


def _is_garbage_text(text):
    """True when an ASR utterance is too noisy to forward to the LLM."""
    if not text:
        return True
    stripped = text.strip()
    if not stripped:
        return True
    if "\ufffd" in stripped:
        return True
    if any(ord(c) < 0x20 for c in stripped):
        return True
    if len(stripped) <= 1:
        return True
    if all(c in _GARBAGE_PUNCT_ONLY for c in stripped):
        return True
    return False


def _load_default_llm_system_prompt() -> str:
    """Read the BT-7274 persona from prompts/bt-7274.txt.

    Falls back to a minimal "stay in character" reminder if the file is
    missing or unreadable so the runtime never hard-fails.
    """
    repo_root = Path(__file__).resolve().parents[4]
    candidate = repo_root / "prompts" / "bt-7274.txt"
    try:
        text = candidate.read_text(encoding="utf-8").strip()
    except Exception:
        text = ""
    if text:
        return text
    return "You are BT-7274, a Pilot's tactical AI assistant. Stay in character at all times."


# Local paraformer promotion reuses the in-process shadow ASR (see
# _feed_kws_shadow_asr) — no cloud call needed. "bt" survives local paraformer
# ("b t 在吗") but is mangled by cloud SenseVoice ("滴滴你在吗"), so promotion
# MUST run locally.


@dataclass
class JarvisConfig:
    """Runtime knobs for the Jarvis state machine."""

    # KWS — 默认用自训 v4 模型 (bt-en, 53 段正样本, 200 段负样本)

    # 甜蜜点参数: score=10, th=0.25; trailing_blanks=1; max_active_paths=10

    # sherpa-onnx 直跑: FAR 15.5% / recall 75.5%; JarvisKWS 包装层(100ms chunk): FAR 2.0% / recall 49.0%

    # (详见 services/scripts/test_jarvis_kws_e2e.py, 2026-07-10 实测)
    wake_word: str = "bt"
    kws_model_dir: str = "D:/AI/models/sherpa-onnx/models/kws/bt-en"
    kws_num_threads: int = 1
    kws_keywords_score: float = 10.0
    """Boost score per keyword token (sherpa-onnx KeywordSpotter 调优).
    社区默认 1.0 不够; v4 训练后 joiner 信号被 blank 压制, 需要强 boost."""
    kws_keywords_threshold: float = 0.25
    """Acoustic probability threshold to fire keyword."""
    kws_num_trailing_blanks: int = 1
    """Trailing blank frames required after keyword match."""
    kws_max_active_paths: int = 10
    """Beam search width (community default 4 不够, 用 10 提高 recall)."""

    # VAD bypass (Silero, sherpa-onnx) — form A: bypass + soft-gate, fail-open.
    # Default OFF. When off / disabled / model-missing, VAD is transparent and
    # KWS receives ALL audio. Enabling only adds a speech/silence annotation +
    # (optional) soft-gate that skips kws.feed_audio on silence chunks. This is
    # NOT the T-VAD-1-rejected "is-anyone-speaking" detector — it is HF-style
    # turn/segment management (see doc/research/kws-vad-bt-wakeword.md §7).
    vad_enabled: bool = False
    vad_model_dir: str = ""
    vad_min_silence_duration: float = 0.5
    vad_min_speech_duration: float = 0.25
    vad_threshold: float = 0.5
    vad_window_size: int = 512
    vad_softgate: bool = False
    """When True AND vad available, skip self._kws.feed_audio on silence chunks
    (soft-gate). Default False = VAD annotation only, KWS gets all audio."""

    # ASR
    asr_model_dir: str = "D:/AI/models/sherpa-onnx/models/asr/streaming-paraformer-bilingual-zh-en"
    asr_num_threads: int = 2

    # Event audio
    # Resolved at runtime in __post_init__ to an absolute path based on
    # this file's location (so the webui does not depend on cwd).
    events_dir: str = ""
    wake_wav: str = "wake.wav"
    goodbye_wav: str = "goodbye.wav"

    # Hybrid wake confirmation: KWS fires cheaply, ASR confirms within timeout
    asr_confirm_timeout_s: float = 1.2
    """Max seconds to wait in WAIT_ASR_CONFIRM for ASR text containing a confirm pattern."""
    asr_confirm_patterns: tuple = ()
    """Explicit substring patterns (backward compat). Default wide match is used when empty."""

    # ASR promotion (cloud recall booster) — OFF by default (safe).
    # --- ASR promotion (local paraformer recall booster) ------------------
    # When KWS misses a real "bt", the in-process shadow ASR (paraformer) has
    # already heard it as "b t ...". If promotion is enabled, that KWS-MISS +
    # shadow-ASR wake-pattern match promotes directly to wake. Purely additive
    # (never suppresses KWS) and fail-safe. NOTE: must stay LOCAL — cloud
    # SenseVoice mangles "bt" -> "滴滴", so cloud ASR cannot promote.
    asr_promotion_enabled: bool = False
    """Enable local paraformer promotion for recall. Default OFF."""
    asr_promotion_cooldown_s: float = 2.0
    """Min seconds between promotion wakes (debounce repeated shadow-ASR hits)."""

    # KWS diagnostics. These do not wake Jarvis; they make KWS misses observable.
    kws_shadow_asr_enabled: bool = True
    """Run ASR in KWS_LISTENING for diagnostics only; logs text when KWS misses."""
    kws_shadow_log_interval_s: float = 0.75
    kws_capture_enabled: bool = True
    kws_capture_dir: str = "D:/AI/data/kws/mic_captures"
    kws_capture_window_s: float = 3.0
    kws_capture_min_interval_s: float = 4.0
    kws_capture_peak_threshold: float = 0.035
    """Save rolling mic windows above this peak so missed BT samples can retrain KWS."""
    kws_probe_min_peak: float = 0.005
    """Fresh-window KWS probe energy gate (B1 P0 silent false-wake fix).

    A probe over a buffer whose peak is below this value is pure silence —
    skip it so silent input can never escalate into a direct wake. Real
    wake speech always carries energy well above 0.005 (report recommends
    0.005-0.01; ~1/3..1/7 of ``kws_capture_peak_threshold``), so this gate
    does not risk killing real wakes (08-11 SOFTGATE 75% miss lesson)."""
    kws_fresh_window_probe_enabled: bool = True
    """On live KWS miss, re-run KWS over a clean rolling PCM window."""
    kws_fresh_window_probe_interval_s: float = 0.5
    kws_fresh_window_min_s: float = 1.0
    kws_fresh_window_direct_wake: bool = True
    """Trust fresh-window KWS hits for wake during recall testing."""
    error_wav: str = "error.wav"

    # Timing
    silence_before_kws_reset_s: float = 5.0
    """Seconds of silence before resetting KWS state (avoids false re-triggers)."""

    # Audio format
    sample_rate: int = 16000

    # TTS (HTTP) — text -> PCM16 via voice_clone_api
    tts_api_url: str = "http://127.0.0.1:8985/v1/synthesize"  # voice_clone_api FastAPI
    tts_voice_id: str = "minimax_man_33333"  # dashboard-cloned BT-7274 voice (2026-07-11); override via JARVIS_TTS_VOICE_ID env

    # LLM (OpenAI-compatible HTTP) — llama-server 7060
    # v3.37 single-LLM-gateway: voice path goes through webinfer, NOT
    # directly to llama-server. Default base is the OpenAI-compatible
    # adapter; per-media-type sub-paths are kept explicit so callers
    # can swap a different gateway without code changes.
    llm_api_url: str = "http://127.0.0.1:8070/v1"
    llm_text_path: str = "/text/chat"
    llm_multimodal_path: str = "/chat/completions"
    llm_model: str = "joyai-vl-interaction-preview-iq4_nl-imat.gguf"
    llm_system_prompt: str = ""  # populated by __post_init__ from prompts/bt-7274.txt

    # P0-A TTS streaming: jarvis voice dialog consumes webinfer's NDJSON
    # stream (decision frame first, then content) and synthesizes TTS per
    # flushed sentence so the first sound arrives in ~800ms instead of
    # waiting for the full reply. Default ON for the jarvis voice path;
    # set JARVIS_LLM_STREAMING=0 to restore the previous non-streaming call.
    # The ``call`` interaction mode (paper-plane / text send) always stays
    # non-streaming regardless of this flag.
    llm_streaming_enabled: bool = True

    @classmethod
    def from_env(cls) -> JarvisConfig:
        """Build a JarvisConfig with env overrides on the KWS / LLM / TTS paths.

        Env overrides (see doc/adr/0002-kws-config-env.md):
          JARVIS_KWS_MODEL_DIR         (str)   default: bt-en folder
          JARVIS_KWS_SCORE             (float) default 10.0  (FAR/recall balance)
          JARVIS_KWS_THRESHOLD         (float) default 0.25
          JARVIS_KWS_TRAILING_BLANKS   (int)   default 1
          JARVIS_KWS_MAX_ACTIVE_PATHS  (int)   default 10
          JARVIS_LLM_API_URL           (str)
          JARVIS_LLM_MODEL             (str)
          JARVIS_TTS_API_URL           (str)
          JARVIS_TTS_VOICE_ID          (str)
          JARVIS_EVENTS_DIR            (str)
          JARVIS_KWS_SHADOW_ASR        (bool)  default true (diagnostic only)
          JARVIS_KWS_CAPTURE           (bool)  default true
          JARVIS_KWS_CAPTURE_DIR       (str)
          JARVIS_KWS_CAPTURE_WINDOW_S  (float)
          JARVIS_KWS_CAPTURE_INTERVAL_S(float)
          JARVIS_KWS_CAPTURE_PEAK      (float)
          JARVIS_KWS_PROBE_MIN_PEAK    (float)  default 0.005 (fresh-window probe energy gate)
          JARVIS_KWS_FRESH_PROBE       (bool)
          JARVIS_KWS_FRESH_PROBE_INTERVAL_S (float)
          JARVIS_KWS_FRESH_PROBE_MIN_S (float)
          JARVIS_KWS_FRESH_DIRECT_WAKE (bool)
          JARVIS_VAD_ENABLED          (bool)  default false (fail-open passthrough)
          JARVIS_VAD_MODEL_DIR        (str)   dir containing silero_vad.onnx
          JARVIS_VAD_MIN_SILENCE_S    (float) default 0.5
          JARVIS_VAD_MIN_SPEECH_S     (float) default 0.25
          JARVIS_VAD_THRESHOLD        (float) default 0.5
          JARVIS_VAD_WINDOW_SIZE      (int)   default 512
          JARVIS_VAD_SOFTGATE         (bool)  default false (annotation only)
        Invalid float/int values fall back to defaults and log a WARNING so
        config typos surface instead of crashing the webui at boot.
        """

        def _get_str(name: str, default: str) -> str:
            v = os.environ.get(name)
            return v if v and v.strip() else default

        def _get_float(name: str, default: float) -> float:
            raw = os.environ.get(name)
            if raw is None or not raw.strip():
                return default
            try:
                return float(raw)
            except ValueError:
                logger.warning(
                    "%s=%r is not a float; falling back to default %s",
                    name,
                    raw,
                    default,
                )
                return default

        def _get_int(name: str, default: int) -> int:
            raw = os.environ.get(name)
            if raw is None or not raw.strip():
                return default
            try:
                return int(raw)
            except ValueError:
                logger.warning(
                    "%s=%r is not an int; falling back to default %s",
                    name,
                    raw,
                    default,
                )
                return default

        def _get_bool(name: str, default: bool) -> bool:
            raw = os.environ.get(name)
            if raw is None or not raw.strip():
                return default
            value = raw.strip().lower()
            if value in {"1", "true", "yes", "on"}:
                return True
            if value in {"0", "false", "no", "off"}:
                return False
            logger.warning(
                "%s=%r is not a bool; falling back to default %s",
                name,
                raw,
                default,
            )
            return default

        return cls(
            kws_model_dir=_get_str("JARVIS_KWS_MODEL_DIR", cls.kws_model_dir),
            kws_keywords_score=_get_float("JARVIS_KWS_SCORE", cls.kws_keywords_score),
            kws_keywords_threshold=_get_float("JARVIS_KWS_THRESHOLD", cls.kws_keywords_threshold),
            kws_num_trailing_blanks=_get_int(
                "JARVIS_KWS_TRAILING_BLANKS", cls.kws_num_trailing_blanks
            ),
            kws_max_active_paths=_get_int("JARVIS_KWS_MAX_ACTIVE_PATHS", cls.kws_max_active_paths),
            kws_shadow_asr_enabled=_get_bool("JARVIS_KWS_SHADOW_ASR", cls.kws_shadow_asr_enabled),
            kws_capture_enabled=_get_bool("JARVIS_KWS_CAPTURE", cls.kws_capture_enabled),
            kws_capture_dir=_get_str("JARVIS_KWS_CAPTURE_DIR", cls.kws_capture_dir),
            kws_capture_window_s=_get_float(
                "JARVIS_KWS_CAPTURE_WINDOW_S", cls.kws_capture_window_s
            ),
            kws_capture_min_interval_s=_get_float(
                "JARVIS_KWS_CAPTURE_INTERVAL_S", cls.kws_capture_min_interval_s
            ),
            kws_capture_peak_threshold=_get_float(
                "JARVIS_KWS_CAPTURE_PEAK", cls.kws_capture_peak_threshold
            ),
            kws_probe_min_peak=_get_float("JARVIS_KWS_PROBE_MIN_PEAK", cls.kws_probe_min_peak),
            kws_fresh_window_probe_enabled=_get_bool(
                "JARVIS_KWS_FRESH_PROBE", cls.kws_fresh_window_probe_enabled
            ),
            kws_fresh_window_probe_interval_s=_get_float(
                "JARVIS_KWS_FRESH_PROBE_INTERVAL_S", cls.kws_fresh_window_probe_interval_s
            ),
            kws_fresh_window_min_s=_get_float(
                "JARVIS_KWS_FRESH_PROBE_MIN_S", cls.kws_fresh_window_min_s
            ),
            kws_fresh_window_direct_wake=_get_bool(
                "JARVIS_KWS_FRESH_DIRECT_WAKE", cls.kws_fresh_window_direct_wake
            ),
            llm_api_url=_get_str("JARVIS_LLM_API_URL", cls.llm_api_url),
            llm_model=_get_str("JARVIS_LLM_MODEL", cls.llm_model),
            llm_text_path=_get_str("JARVIS_LLM_TEXT_PATH", cls.llm_text_path),
            llm_multimodal_path=_get_str("JARVIS_LLM_MULTIMODAL_PATH", cls.llm_multimodal_path),
            llm_streaming_enabled=_get_bool("JARVIS_LLM_STREAMING", cls.llm_streaming_enabled),
            tts_api_url=_get_str("JARVIS_TTS_API_URL", cls.tts_api_url),
            tts_voice_id=_get_str("JARVIS_TTS_VOICE_ID", cls.tts_voice_id),
            events_dir=_get_str("JARVIS_EVENTS_DIR", cls.events_dir),
            vad_enabled=_get_bool("JARVIS_VAD_ENABLED", cls.vad_enabled),
            vad_model_dir=_get_str("JARVIS_VAD_MODEL_DIR", cls.vad_model_dir),
            vad_min_silence_duration=_get_float(
                "JARVIS_VAD_MIN_SILENCE_S", cls.vad_min_silence_duration
            ),
            vad_min_speech_duration=_get_float(
                "JARVIS_VAD_MIN_SPEECH_S", cls.vad_min_speech_duration
            ),
            vad_threshold=_get_float("JARVIS_VAD_THRESHOLD", cls.vad_threshold),
            vad_window_size=_get_int("JARVIS_VAD_WINDOW_SIZE", cls.vad_window_size),
            vad_softgate=_get_bool("JARVIS_VAD_SOFTGATE", cls.vad_softgate),
            asr_promotion_enabled=_get_bool(
                "JARVIS_ASR_PROMOTION_ENABLED", cls.asr_promotion_enabled
            ),
            asr_promotion_cooldown_s=_get_float(
                "JARVIS_ASR_PROMOTION_COOLDOWN_S", cls.asr_promotion_cooldown_s
            ),
        )

    def __post_init__(self) -> None:
        # (a) Lazy-load the BT-7274 persona prompt from prompts/bt-7274.txt
        #     unless the caller already supplied one explicitly.
        if not self.llm_system_prompt:
            self.llm_system_prompt = _load_default_llm_system_prompt()
        # (b) Resolve `events_dir` to an absolute path based on this
        #     file's location so the webui (which may have a different
        #     cwd) can still find prompts/bt/events/*.wav.
        if not self.events_dir:
            here = Path(__file__).resolve()
            # .../services/webui/src/joy_interaction_webui/jarvis_mode.py
            # repo_root = parents[4]
            repo_root = here.parents[4]
            self.events_dir = str(repo_root / "prompts" / "bt" / "events")


def asr_model_display_name(config: JarvisConfig) -> str:
    """Human-readable label for the local paraformer ASR in server_config.

    Derives from ``config.asr_model_dir`` so operator overrides (e.g. via
    ``JARVIS_ASR_MODEL_DIR``) are reflected in the UI. Always tagged
    ``(local)`` because promotion MUST run on the in-process sherpa-onnx
    ASR — cloud SenseVoice mangles ``"bt"`` -> ``"滴滴"`` and cannot promote
    (see :meth:`JarvisStateMachine._try_promote_from_local_asr`).
    """
    model_dir = (getattr(config, "asr_model_dir", "") or "").strip()
    if model_dir:
        basename = Path(model_dir).name
    else:
        basename = "streaming-paraformer-bilingual-zh-en"
    if "paraformer" in basename.lower():
        return f"sherpa-onnx {basename} (local)"
    return f"sherpa-onnx local paraformer ({basename})"


# ============================================================================
# State Machine
# ============================================================================


class JarvisState(Enum):
    KWS_LISTENING = auto()  # Waiting for wake word, KWS running
    WAKE_DETECTED = auto()  # Wake word heard, playing wake.wav
    DIALOG_ACTIVE = auto()  # Full duplex: ASR streaming + TTS
    TTS_PAUSED = auto()  # Interrupt: user started speaking during TTS
    EXIT_DETECTED = auto()  # Exit word detected, playing goodbye.wav
    ERROR = auto()  # Unrecoverable error, log only
    WAIT_ASR_CONFIRM = (
        auto()
    )  # KWS fired; waiting for ASR to confirm wake pattern before playing wake.wav


@dataclass
class AsrPartial:
    """A partial or final ASR result."""

    text: str
    is_final: bool = False
    timestamp_ms: float = 0.0


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
        """Return (peak, rms) for int16 mono PCM in the 0..1 range."""
        if not pcm:
            return 0.0, 0.0
        if len(pcm) % 2:
            pcm = pcm[:-1]
        samples = array("h")
        samples.frombytes(pcm)
        if not samples:
            return 0.0, 0.0
        peak = max(abs(s) for s in samples) / 32768.0
        square_sum = sum(float(s) * float(s) for s in samples)
        rms = math.sqrt(square_sum / len(samples)) / 32768.0
        return min(1.0, peak), min(1.0, rms)

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
        """
        if not getattr(self.config, "kws_fresh_window_probe_enabled", True):
            return False
        now = time.time()
        interval = max(0.0, self.config.kws_fresh_window_probe_interval_s)
        if interval and (now - self._last_kws_fresh_probe_at) < interval:
            return False
        min_s = 0.1 if bypass_min_s else max(0.1, self.config.kws_fresh_window_min_s)
        if self._kws_capture_bytes < int(min_s * self.config.sample_rate * 2):
            return False
        self._last_kws_fresh_probe_at = now
        pcm = b"".join(self._kws_capture_chunks)
        # B1 P0 energy gate: probe only buffers with real acoustic content.
        # Recompute energy from the exact window we would probe (authoritative —
        # the passed peak/rms are the CURRENT chunk's stats, which can be ~0 for
        # a wake that settled during trailing blanks while the buffer holds the
        # actual speech). A pure-silence buffer is skipped so it can never
        # escalate into a direct wake.
        buf_peak, buf_rms = self._pcm_stats(pcm)
        if buf_peak < max(0.0, self.config.kws_probe_min_peak):
            logger.info(
                "Fresh-window KWS probe skipped: silent window "
                "(peak=%.4f < kws_probe_min_peak=%.4f)",
                buf_peak,
                max(0.0, self.config.kws_probe_min_peak),
            )
            return False
        try:
            hit = bool(self._kws.detect_in_pcm(pcm))
        except Exception as exc:
            logger.warning("Fresh-window KWS probe failed: %s", exc)
            return False
        if not hit:
            return False
        logger.info(
            "Wake word detected by fresh-window KWS probe (%.2fs peak=%.3f rms=%.3f)",
            len(pcm) / (self.config.sample_rate * 2),
            buf_peak,
            buf_rms,
        )
        if not getattr(self.config, "kws_fresh_window_direct_wake", True):
            return False
        await self._direct_wake_from_kws(source="fresh-window-kws")
        return True

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
        """
        try:
            await asyncio.sleep(self.config.asr_confirm_timeout_s)
        except asyncio.CancelledError:
            return
        if self.state != JarvisState.WAIT_ASR_CONFIRM:
            return  # already promoted or otherwise moved on
        # Recovery probe: fresh-stream KWS over captured audio.
        # Use peak/rms captured at wake time (more accurate) and bypass
        # the 1s min_s gate since we already have a trusted live KWS hit.
        # B1 P0: do NOT synthesize a fake peak for silent wakes (the old
        # ``peak <= 0 -> 0.5`` fallback let a pure-silence false trigger
        # escalate into a direct wake). Pass the wake-chunk energy as-is;
        # the probe's own buffer-energy gate (kws_probe_min_peak) decides
        # whether there is real acoustic content to recover. A silent wake
        # falls through to _reset_to_kws() below instead of direct-waking.
        peak = getattr(self, "_last_wake_peak", 0.0)
        rms = getattr(self, "_last_wake_rms", 0.0)
        if await self._probe_kws_fresh_window(peak=peak, rms=rms, bypass_min_s=True):
            logger.info(
                "WAIT_ASR_CONFIRM recovered via fresh-window KWS probe; "
                "direct wake without ASR confirm"
            )
            return
        logger.info(
            "WAIT_ASR_CONFIRM timeout (%.2fs) without ASR match; returning to KWS_LISTENING",
            self.config.asr_confirm_timeout_s,
        )
        await self._reset_to_kws()

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
        """
        if not text:
            return False
        lowered = text.lower()

        # (1) explicit operator-provided patterns (override mode)
        patterns = getattr(getattr(self, "config", None), "asr_confirm_patterns", None)
        if patterns:
            return any(p.lower() in lowered for p in patterns)

        # (2) wide normalised match for segmented "b t" / "bt" forms
        normalised = " ".join(_ASR_CONFIRM_NON_WORD.split(lowered))
        if "bt" in normalised:
            return True
        tokens = normalised.split()
        return any(tokens[i] == "b" and tokens[i + 1] == "t" for i in range(len(tokens) - 1))

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
            partial = AsrPartial(text=text, is_final=False, timestamp_ms=now * 1000)
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
            partial = AsrPartial(text=text, is_final=False, timestamp_ms=now * 1000)
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
            (self._tts_task is not None and not self._tts_task.done())
            or self._tts_sentence_tasks
        )

    async def _stop_tts(self):
        """Stop TTS immediately (user interrupted with exit word)."""
        # P0-A: also stop the streaming sentence queue (bump epoch, cancel
        # every in-flight :8985 sentence task, flag the LLM-stream consumer).
        self._ensure_tts_stream_state()
        self._tts_sentence_epoch += 1
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
        if (
            interaction_mode == "jarvis"
            and not image_b64
            and getattr(self.config, "llm_streaming_enabled", True)
        ):
            return await self._send_to_llm_streaming(
                text,
                stream_tts=stream_tts,
                interaction_mode=interaction_mode,
            )
        return await self._send_to_llm_non_streaming(
            text,
            stream_tts=stream_tts,
            image_b64=image_b64,
            interaction_mode=interaction_mode,
        )

    async def _send_to_llm_non_streaming(
        self,
        text: str,
        *,
        stream_tts: bool = True,
        image_b64: str | None = None,
        interaction_mode: str = "jarvis",
    ):
        """Single-shot LLM call (legacy path, unchanged).

        Calls POST {llm_api_url}/{text|multimodal} and waits for the complete
        JSON response, then optionally triggers TTS with the full text. This
        is the pre-P0-A behavior kept for ``call`` mode, multimodal sends,
        and as the fail-open fallback when streaming errors out.
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
        )

    async def _send_to_llm_streaming(
        self,
        text: str,
        *,
        stream_tts: bool = False,
        interaction_mode: str = "jarvis",
    ):
        """P0-A streaming LLM consumption: decision first, sentence TTS.

        POSTs ``stream: true`` to webinfer's ``/v1/text/chat`` and consumes
        the NDJSON frames:

          * ``decision`` frame — determines silence / response / delegation
            (same semantics as the non-streaming path; silence never speaks);
          * ``content`` frames (decision == ``response``) — fed into a
            :class:`SentenceBuffer`; each flushed sentence is synthesized
            (:8985, short single-shot) in a background task and pushed to the
            browser as a ``tts_sentence`` WS message (seq + session + WAV);
          * ``done`` frame — full text for history / transcript broadcast.

        Fail-open (never lose the reply): if the stream errors before any
        frame, the whole turn is re-run through the non-streaming path; if it
        errors after a decision, the buffered remainder is synthesized and
        the reply is broadcast anyway (logged).
        """
        import json as _json

        from .turn_controller import SentenceBuffer

        # v3.24: prepend bounded conversation history (same as non-streaming).
        messages = [{"role": "system", "content": self.config.llm_system_prompt}]
        history_snapshot = list(self._conv_history)[-self._max_history_turns * 2 :]
        for role, content in history_snapshot:
            messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": text})

        endpoint_url = f"{self.config.llm_api_url}{self.config.llm_text_path}"
        self._ensure_tts_stream_state()
        self._llm_stream_cancel = False
        reply_session = self._tts_reply_seq
        self._tts_reply_seq += 1
        sentence_buffer = SentenceBuffer()
        seq = 0
        full_response = ""
        decision = "silence"
        delegation_question = None
        frames_received = False
        decision_received = False
        done_received = False
        cancelled = False

        logger.info(
            "[tts-stream] LLM stream start: '%s' (session=%d, history_turns=%d)",
            text,
            reply_session,
            len(history_snapshot) // 2,
        )
        import httpx

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                async with client.stream(
                    "POST",
                    endpoint_url,
                    json={
                        "model": self.config.llm_model,
                        "messages": messages,
                        "max_tokens": 200,
                        "temperature": 0.7,
                        "interaction_mode": interaction_mode,
                        "stream": True,
                    },
                ) as resp:
                    if resp.status_code != 200:
                        raise RuntimeError(
                            f"webinfer stream HTTP {resp.status_code}: "
                            f"{await resp.aread()!r}"[:200]
                        )
                    async for line in resp.aiter_lines():
                        if not line.strip():
                            continue
                        frames_received = True
                        try:
                            frame = _json.loads(line)
                        except Exception as exc:
                            logger.warning("[tts-stream] skipping malformed frame: %s", exc)
                            continue
                        ftype = frame.get("type")
                        if self._llm_stream_cancel:
                            logger.info(
                                "[tts-stream] cancelled mid-stream (session=%d); stopping",
                                reply_session,
                            )
                            cancelled = True
                            break
                        if ftype == "decision":
                            decision_received = True
                            decision = frame.get("decision") or "silence"
                            delegation_question = frame.get("delegation_question")
                            logger.info(
                                "[tts-stream] decision=%s (session=%d)",
                                decision,
                                reply_session,
                            )
                        elif ftype == "content":
                            token = frame.get("token") or ""
                            if not token:
                                continue
                            full_response += token
                            if decision != "response":
                                continue
                            sentence = sentence_buffer.add_token(token)
                            if sentence is not None:
                                self._spawn_sentence_tts(sentence, seq, reply_session)
                                seq += 1
                        elif ftype == "done":
                            done_received = True
                            if frame.get("full_text"):
                                full_response = frame["full_text"]
                            if frame.get("decision"):
                                decision = frame["decision"]
                            if frame.get("delegation_question") is not None:
                                delegation_question = frame["delegation_question"]
                        elif ftype == "error":
                            raise RuntimeError(frame.get("error") or "webinfer stream error")
        except Exception as exc:
            logger.error(
                "[tts-stream] streaming LLM failed (session=%d, frames=%s, decision=%s): %s",
                reply_session,
                frames_received,
                decision_received,
                exc,
            )
            if not frames_received:
                # Nothing was consumed — clean retry through the non-streaming
                # path so the reply is never lost (and no audio is doubled).
                logger.info("[tts-stream] fail-open -> non-streaming retry")
                return await self._send_to_llm_non_streaming(
                    text,
                    stream_tts=stream_tts,
                    interaction_mode=interaction_mode,
                )
            # Mid-stream failure: keep what we have (log, do not re-run).
            if decision_received and decision == "response":
                remaining = sentence_buffer.flush_remaining()
                if remaining:
                    self._spawn_sentence_tts(remaining, seq, reply_session)
                    seq += 1
                    logger.info(
                        "[tts-stream] flushed %d buffered char(s) after mid-stream failure",
                        len(remaining),
                    )

        if cancelled:
            # Barge-in / exit word: stop everything. The epoch bump already
            # cancelled every in-flight sentence task; the partial reply is
            # intentionally not broadcast (the user is talking over it).
            return

        if not done_received:
            # Stream ended without a done frame (e.g. mid-stream failure above):
            # flush any remaining buffered sentence so no text is lost.
            remaining = sentence_buffer.flush_remaining()
            if remaining:
                self._spawn_sentence_tts(remaining, seq, reply_session)
                seq += 1
            full_response = full_response or remaining or ""

        # sentence_buffer may still hold text if neither path flushed it.
        if not sentence_buffer.is_empty:
            remaining = sentence_buffer.flush_remaining()
            if remaining:
                self._spawn_sentence_tts(remaining, seq, reply_session)
                seq += 1

        logger.info(
            "[tts-stream] LLM stream done (session=%d, decision=%s, sentences=%d, chars=%d)",
            reply_session,
            decision,
            seq,
            len(full_response),
        )
        await self._finish_llm_turn(
            text=text,
            response=full_response,
            decision=decision,
            delegation_question=delegation_question,
            stream_tts=stream_tts,
            force_jarvis_voice=True,
        )

    def _spawn_sentence_tts(self, sentence: str, seq: int, reply_session: int) -> None:
        """Synthesize one flushed sentence in the background and push it.

        The task runs concurrently with LLM streaming (sentence N synthesizes
        while the model generates sentence N+1). The browser plays by seq;
        an epoch bump (barge-in / exit word) cancels every in-flight task and
        the captured epoch makes any task that survives drop its result.
        """
        self._ensure_tts_stream_state()
        epoch = self._tts_sentence_epoch
        task = asyncio.create_task(
            self._synthesize_tts_sentence(sentence, seq, reply_session, epoch)
        )
        self._tts_sentence_tasks.add(task)
        task.add_done_callback(self._tts_sentence_tasks.discard)
        logger.info(
            "[tts-stream] sentence %d queued (session=%d, %d chars): '%s'",
            seq,
            reply_session,
            len(sentence),
            sentence[:60],
        )

    async def _synthesize_tts_sentence(
        self, sentence: str, seq: int, reply_session: int, epoch: int
    ) -> None:
        """Fetch PCM16 for one sentence, wrap as WAV, push ``tts_sentence``.

        Guards with the sentence epoch captured at spawn time so audio
        synthesized after a barge-in / exit word is never pushed to the
        browser.
        """
        if self._tts_sentence_epoch != epoch:
            return
        try:
            pcm = await self._fetch_tts_pcm(sentence)
        except Exception as exc:
            logger.error("[tts-stream] sentence %d TTS failed: %s", seq, exc)
            return
        if self._tts_sentence_epoch != epoch:
            logger.debug("[tts-stream] sentence %d stale (epoch bumped); dropping", seq)
            return
        try:
            wav = self._wrap_pcm16_wav(pcm, sample_rate=24000)
            audio_b64 = base64.b64encode(wav).decode("ascii")
        except Exception as exc:
            logger.error("[tts-stream] sentence %d WAV wrap failed: %s", seq, exc)
            return
        if self.on_tts_sentence:
            try:
                self.on_tts_sentence(sentence, seq, audio_b64, reply_session)
            except Exception as exc:
                logger.warning("[tts-stream] tts_sentence callback failed: %s", exc)

    async def _fetch_tts_pcm(self, text: str) -> bytes:
        """POST ``text`` to voice_clone_api :8985 and return PCM16 bytes."""
        import httpx

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

    async def _finish_llm_turn(
        self,
        *,
        text: str,
        response: str,
        decision: str,
        delegation_question: str | None,
        stream_tts: bool,
        force_jarvis_voice: bool = False,
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
