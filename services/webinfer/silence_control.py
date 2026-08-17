"""Radio-silence (无线电静默) control mixin for the webinfer adapter.

Defines :class:`SilenceControlMixin`, which owns the live-mode
``suppressed`` sub-state (spec ``doc/specs/draft-radio-silence.md``):

* ``GET/POST /v1/live/silence`` — status snapshot, settings update, manual
  toggle, and KWS wake-event intake (the webui backend proxies to this
  endpoint, the same summarizer-route pattern);
* ``_silence_process_transcript`` — ASR-transcript command detection:
  enter phrases (``无线电静默`` / ``静默``), exit phrases (``退出静默`` …),
  and BT-7274 role names (``bt`` / ``铁驭`` / ``贾维斯`` …);
* ``_silence_process_kws_event`` — KWS wake event pushed from the webui
  side (honours the silence KWS switch);
* ``_silence_check_timeouts`` — T1 (one-shot "仍在静默中" hint) / T2
  (auto-wake) timers, each independently switchable + minute value;
* ``_is_suppressed`` — the live-loop suppression gate (no inference, no TTS).

Design notes
------------
* **Live-only sub-state (D-001 isolation)**: ``call`` and ``jarvis`` never
  consult radio silence; ``_is_suppressed`` returns ``False`` for any
  non-live interaction mode so the three-mode isolation contract holds.
* **Process-local, non-persistent**: ``suppressed`` lives on the adapter
  instance only; a restart always comes back to the live 常驻 (normal)
  state per spec §2 (never persist the flag).
* **Vision continues, output suppressed**: suppressed live rounds still
  append frames / advance the chunk (隐身观察); only inference + TTS are
  cut. A name-wake round additionally arms a one-shot addressee directive
  so the model replies with ``</response>`` (never ``</not-for-me>``).
* **Timers are lazily evaluated**: ``_silence_check_timeouts`` runs on every
  live round and on every ``GET /v1/live/silence`` poll, so the webui
  status poll drives the one-shot T1 hint with no extra RPC. T2 still
  auto-wakes as long as either a round or a poll arrives.
* **Name detection** follows the project's ASR-confirm matching conventions
  (lowercase + non-word collapse, the ``jarvis_kws.asr_confirm_match``
  style): a bare ``bt``, segmented ``b t`` / ``b.t`` and the Chinese role
  names all match.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from aiohttp import web
from request_parsing import _read_json
from response_format import _openai_error_response

LOGGER = logging.getLogger("streaming_infer_adapter")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Default silence settings (spec §3). ASR off / KWS on / T1 hint on 15min /
#: T2 auto-wake off by default.
DEFAULT_SILENCE_SETTINGS: dict[str, Any] = {
    "asr_enabled": False,
    "kws_enabled": True,
    "timeout_hint_enabled": True,
    "timeout_hint_minutes": 15,
    "auto_wake_enabled": False,
    "auto_wake_minutes": 30,
}

_SILENCE_SETTING_KEYS = tuple(DEFAULT_SILENCE_SETTINGS)

#: ASR-transcript phrases that ENTER radio silence (spec §1: "无线电静默" / "静默";
#: BT/静默 are not common words, plain substring matching is enough).
SILENCE_ENTER_PHRASES = ("无线电静默", "静默")

#: Explicit wake-out phrases checked BEFORE enter phrases so "退出静默" never
#: re-enters (it contains "静默"). Natural speech escape hatch besides names.
SILENCE_EXIT_PHRASES = ("退出静默", "取消静默", "解除静默", "结束静默")

#: BT-7274 role names that wake the adapter from radio silence (spec §5:
#: 静默态叫名字 = 唤醒, same engine as 常态被点名).
#: NOTE (product semantics, 2026-08-17): "贾维斯" (Jarvis) is intentionally
#: kept here — it shares the project's wake-name vocabulary and a Pilot who
#: habitually says the other assistant's name expects a response. Whether BT
#: (a military Titan) SHOULD answer to "贾维斯" is a product question, not a
#: correctness one; behaviour is intentionally unchanged until the product
#: owner decides (a token removal is a one-line change here).
SILENCE_NAME_TOKENS = ("bt", "b t", "b.t", "铁驭", "贾维斯")

#: One-shot addressee directive injected into the prompt of the FIRST live
#: round after a name/KWS wake — the utterance is addressed to the AI, so the
#: model must answer instead of emitting </not-for-me> / </silence>.
RADIO_SILENCE_WAKE_DIRECTIVE = (
    "[Radio-silence wake] The Pilot just called your name to wake you from "
    "radio silence. This utterance IS addressed to you: reply with "
    "</response> and a concise acknowledgment. Never output </not-for-me> "
    "or </silence> on a wake round."
)

_NON_WORD_RE = re.compile(r"[^\w]+", flags=re.UNICODE)


class SilenceControlMixin:
    """Radio-silence sub-state, settings, and wake channels (live only)."""

    # ------------------------------------------------------------------
    # State initialisation
    # ------------------------------------------------------------------

    def _init_silence_control(self) -> None:
        """Initialise the process-local silence state (call from ``__init__``)."""
        self._silence_suppressed = False
        self._silence_settings: dict[str, Any] = dict(DEFAULT_SILENCE_SETTINGS)
        self._silence_entered_at: float | None = None
        self._silence_hint_fired = False
        self._silence_hint_count = 0
        self._silence_wake_pending = False
        LOGGER.info(
            "[silence] control initialised (suppressed=%s settings=%s)",
            self._silence_suppressed,
            self._silence_settings,
        )

    def _ensure_silence_initialized(self) -> None:
        """Lazily initialise silence state.

        Used when the adapter was built via ``__new__`` (test harnesses
        bypass ``__init__``). Idempotent and logged — production always
        initialises eagerly in ``__init__``.
        """
        if not hasattr(self, "_silence_suppressed"):
            LOGGER.warning(
                "[silence] state not initialised (adapter built via __new__?); initialising lazily"
            )
            self._init_silence_control()

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------

    def _silence_enter(self, reason: str) -> bool:
        """Enter radio silence. Returns True on a real transition."""
        if self._silence_suppressed:
            LOGGER.info("[silence] enter ignored (already suppressed, reason=%s)", reason)
            return False
        self._silence_suppressed = True
        self._silence_entered_at = time.monotonic()
        self._silence_hint_fired = False
        self._silence_wake_pending = False
        LOGGER.info(
            "[silence] entered (reason=%s, settings=%s)",
            reason,
            self._silence_settings,
        )
        return True

    def _silence_exit(self, reason: str) -> bool:
        """Exit radio silence. Returns True on a real transition."""
        if not self._silence_suppressed:
            LOGGER.info("[silence] exit ignored (not suppressed, reason=%s)", reason)
            return False
        self._silence_suppressed = False
        self._silence_entered_at = None
        self._silence_hint_fired = False
        LOGGER.info("[silence] exited (reason=%s)", reason)
        return True

    def _silence_wake(self, reason: str) -> bool:
        """Exit silence and arm the one-shot wake directive for the next live round."""
        if not self._silence_exit(reason):
            return False
        self._silence_wake_pending = True
        LOGGER.info("[silence] wake directive armed (reason=%s)", reason)
        return True

    # ------------------------------------------------------------------
    # Transcript command / name detection (webinfer side of ASR switch)
    # ------------------------------------------------------------------

    def _silence_process_transcript(self, text: str | None, interaction_mode: str) -> None:
        """Detect silence enter/wake commands in an ASR transcript (live only).

        Command semantics (spec §1 / §5):
          * ``退出静默``-style exit phrases win over enter phrases (they contain
            the enter substring ``静默``);
          * enter phrases (``无线电静默`` / ``静默``) enter radio silence;
          * a role name (bt / 铁驭 / 贾维斯 …) wakes from silence and arms the
            one-shot addressee directive.

        When suppressed AND the silence ASR switch is off, no text detection
        runs at all (关则纯 KWS/组合键唤醒, no text channel) — the caller still
        suppresses the round via :meth:`_is_suppressed`.
        """
        if interaction_mode != "live":
            return
        self._ensure_silence_initialized()
        if not text:
            return
        if self._silence_suppressed and not self._silence_settings["asr_enabled"]:
            LOGGER.info("[silence] transcript ignored (suppressed + asr_enabled=False)")
            return

        lowered = text.lower()
        if any(phrase in lowered for phrase in SILENCE_EXIT_PHRASES):
            self._silence_wake(reason="command-exit")
            return
        if any(phrase in lowered for phrase in SILENCE_ENTER_PHRASES):
            self._silence_enter(reason="command-enter")
            return
        if self._silence_suppressed and self._silence_name_matched(lowered):
            self._silence_wake(reason="name")
            return

    def _silence_name_matched(self, lowered_text: str) -> bool:
        """Return True when a BT-7274 role name appears in the (lowercased) transcript.

        Mirrors ``jarvis_kws.asr_confirm_match``: a wide normalised match that
        collapses non-word characters so ``bt``, ``b t``, ``b.t`` and the
        Chinese role names all hit. Returns False on empty input.
        """
        if not lowered_text:
            return False
        if any(token in lowered_text for token in SILENCE_NAME_TOKENS):
            return True
        normalised = " ".join(_NON_WORD_RE.split(lowered_text))
        return "bt" in normalised

    # ------------------------------------------------------------------
    # KWS wake event (webui pushes the KWS event source into this module)
    # ------------------------------------------------------------------

    def _silence_process_kws_event(self) -> bool:
        """Process a KWS wake event pushed from the webui side.

        Returns True when the event actually woke the adapter. KWS only wakes
        while suppressed AND the silence KWS switch is on (组合矩阵 开/开 and
        关/开); otherwise the event is logged and ignored (仅组合键/语音唤醒).
        """
        self._ensure_silence_initialized()
        if not self._silence_suppressed:
            LOGGER.info("[silence] KWS event ignored (not suppressed)")
            return False
        if not self._silence_settings["kws_enabled"]:
            LOGGER.info("[silence] KWS event ignored (kws_enabled=False)")
            return False
        self._silence_wake(reason="kws")
        return True

    # ------------------------------------------------------------------
    # Timers: T1 (hint) / T2 (auto-wake)
    # ------------------------------------------------------------------

    def _silence_check_timeouts(self, now: float | None = None) -> str | None:
        """Evaluate the T1/T2 timers; returns ``"hint"`` / ``"auto_wake"`` / ``None``.

        T1 (静默超时提示): one-shot per silence period; when it fires the
        ``hint_count`` is bumped so the webui poll can play the pre-recorded
        "仍在静默中" audio exactly once. T2 (自动唤醒): auto-exits silence.
        Both are independent (spec §6); when T2 is off only manual wake works.
        """
        self._ensure_silence_initialized()
        if not self._silence_suppressed or self._silence_entered_at is None:
            return None
        now = time.monotonic() if now is None else now
        elapsed = now - self._silence_entered_at
        settings = self._silence_settings

        if settings["timeout_hint_enabled"] and not self._silence_hint_fired:
            t1_sec = max(0, int(settings["timeout_hint_minutes"] or 0)) * 60
            if t1_sec > 0 and elapsed >= t1_sec:
                self._silence_hint_fired = True
                self._silence_hint_count += 1
                LOGGER.info(
                    "[silence] T1 hint fired after %.0fs (hint_count=%d)",
                    elapsed,
                    self._silence_hint_count,
                )
                return "hint"

        if settings["auto_wake_enabled"]:
            t2_sec = max(0, int(settings["auto_wake_minutes"] or 0)) * 60
            if t2_sec > 0 and elapsed >= t2_sec:
                self._silence_exit(reason="auto_wake")
                LOGGER.info("[silence] T2 auto-wake after %.0fs", elapsed)
                return "auto_wake"
        return None

    # ------------------------------------------------------------------
    # Suppression gate + wake directive (consumed by the live loop)
    # ------------------------------------------------------------------

    def _is_suppressed(self, interaction_mode: str) -> bool:
        """Return True when a live round must be fully suppressed (no inference / TTS).

        Radio silence is a LIVE sub-state only: ``call`` and ``jarvis`` return
        False so the D-001 three-mode isolation is never broken.
        """
        if interaction_mode != "live":
            return False
        self._ensure_silence_initialized()
        return bool(self._silence_suppressed)

    def _consume_wake_directive(self, interaction_mode: str) -> str | None:
        """Return the one-shot wake directive text and clear the marker.

        Only armed by a name/KWS wake out of radio silence; the directive is
        injected into the FIRST live round after the wake so the model replies
        instead of treating the wake utterance as not-for-me.
        """
        if interaction_mode != "live":
            return None
        self._ensure_silence_initialized()
        if not self._silence_wake_pending:
            return None
        self._silence_wake_pending = False
        LOGGER.info("[silence] wake directive consumed by live round")
        return RADIO_SILENCE_WAKE_DIRECTIVE

    # ------------------------------------------------------------------
    # Settings + snapshot
    # ------------------------------------------------------------------

    def _silence_update_settings(self, updates: dict[str, Any]) -> str | None:
        """Apply validated settings updates.

        Returns ``None`` on success, or a human-readable error message when a
        value has the wrong type — the caller (``handle_live_silence``) turns
        that into a 400. Per 约法三章② invalid config is rejected explicitly,
        never silently coerced: ``bool("false") == True`` would wrongly enable
        a switch, and ``True`` must not become a minutes value (bool is an int
        subclass). Minutes stay otherwise lenient (numeric strings accepted,
        clamped >= 0) — the webui proxy already sends clean ints.
        """
        for key, value in updates.items():
            if key not in _SILENCE_SETTING_KEYS:
                LOGGER.warning("[silence] unknown setting ignored: %s", key)
                continue
            if key.endswith("_minutes"):
                if isinstance(value, bool):
                    return f"{key} must be an integer (got bool)"
                try:
                    value = max(0, int(value))
                except (TypeError, ValueError):
                    LOGGER.warning("[silence] invalid minutes value for %s: %r", key, value)
                    continue
            else:
                if not isinstance(value, bool):
                    return f"{key} must be a boolean (got {type(value).__name__})"
            self._silence_settings[key] = value
        LOGGER.info("[silence] settings updated: %s", self._silence_settings)
        return None

    def _silence_snapshot(self) -> dict[str, Any]:
        """Return the current suppressed state + settings + pending events."""
        self._ensure_silence_initialized()
        return {
            "suppressed": self._silence_suppressed,
            "settings": dict(self._silence_settings),
            "hint_pending": bool(
                self._silence_suppressed
                and self._silence_settings["timeout_hint_enabled"]
                and not self._silence_hint_fired
            ),
            "hint_count": self._silence_hint_count,
            "wake_pending": self._silence_wake_pending,
        }

    # ------------------------------------------------------------------
    # HTTP route handler
    # ------------------------------------------------------------------

    async def handle_live_silence(self, request: web.Request) -> web.Response:
        """``GET/POST /v1/live/silence`` — silence state + settings + KWS events.

        GET returns the current snapshot (``{suppressed, settings, hint_pending,
        hint_count, wake_pending}``) and lazily evaluates the T1/T2 timers so
        the webui status poll drives the one-shot hint.

        POST body (all keys optional):
          * ``suppressed: bool`` — manual toggle (组合键 / 语音切换);
          * ``settings: {asr_enabled, kws_enabled, timeout_hint_enabled,
            timeout_hint_minutes, auto_wake_enabled, auto_wake_minutes}`` —
            validated settings update;
          * ``kws_event: true`` — a KWS wake event pushed by the webui side
            (honours the silence KWS switch).
        """
        if request.method == "GET":
            self._silence_check_timeouts()
            return web.json_response(self._silence_snapshot())

        try:
            payload = await _read_json(request)
        except web.HTTPException as exc:
            return _openai_error_response(exc.text or "invalid JSON body", status=400)

        if payload.get("kws_event") is True:
            self._silence_process_kws_event()

        settings = payload.get("settings")
        if isinstance(settings, dict):
            err = self._silence_update_settings(settings)
            if err is not None:
                LOGGER.warning("[silence] settings rejected: %s", err)
                return _openai_error_response(err, status=400)

        raw_suppressed = payload.get("suppressed")
        if raw_suppressed is not None:
            # 约法三章②: same trap class as settings — ``bool("false")==True``
            # would wrongly enter silence, so a non-bool toggle is rejected.
            if not isinstance(raw_suppressed, bool):
                LOGGER.warning("[silence] suppressed rejected: must be a boolean")
                return _openai_error_response("suppressed must be a boolean", status=400)
            if raw_suppressed:
                self._silence_enter(reason="manual")
            else:
                self._silence_exit(reason="manual")

        self._silence_check_timeouts()
        return web.json_response(self._silence_snapshot())
