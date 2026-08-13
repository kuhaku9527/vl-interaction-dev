"""Jarvis runtime configuration (extracted from ``jarvis_mode.py``).

Moved from ``jarvis_mode.py`` (spec codebase-map-2026-08-13.md §2.1,
priority 5): the ``JarvisConfig`` dataclass + env overrides, the wake/exit
word constants, the ASR-text helpers (``_is_garbage_text`` /
``_ASR_CONFIRM_NON_WORD``), the persona-prompt loader, and the
``asr_model_display_name`` label helper. ``jarvis_mode`` re-exports all of
them so existing import surfaces keep working unchanged.

The ``logger`` here intentionally uses the same ``joyai.jarvis`` name as the
state machine so log routing / caplog assertions are byte-identical.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("joyai.jarvis")

# Collapse runs of non-word characters (spaces, punctuation, CJK punctuation,
# emoji, etc.) to a single space when normalising ASR confirm text.
_ASR_CONFIRM_NON_WORD = re.compile(r"[^\w]+", flags=re.UNICODE)

EXIT_WORDS = {"明白", "了解", "ok", "好的", "知道了"}
"""Words that signal "I'm done talking" — treated as end-of-conversation signal."""

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
            # .../services/webui/src/joy_interaction_webui/jarvis_config.py
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


__all__ = [
    "EXIT_WORDS",
    "_ASR_CONFIRM_NON_WORD",
    "_GARBAGE_PUNCT_ONLY",
    "JarvisConfig",
    "_is_garbage_text",
    "_load_default_llm_system_prompt",
    "asr_model_display_name",
]
