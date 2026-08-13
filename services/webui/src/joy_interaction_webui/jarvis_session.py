"""Jarvis Session Manager — bridges state machine into webui server.

Provides per-session Jarvis state management that hooks into:
- server.py: session creation, state broadcast
- asr.py: audio routing, wake/exit word detection
- tts.py: TTS gating

Usage in server.py:
    from jarvis_session import JarvisSessionManager

    manager = JarvisSessionManager()
    jarvis = await manager.create_session(session_id)
    await jarvis.feed_audio(pcm_chunk)
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .jarvis_mode import (
    EXIT_WORDS,
    AsrPartial,
    JarvisConfig,
    JarvisState,
    JarvisStateMachine,
)

if TYPE_CHECKING:
    from .live_mode import LiveStateMachine

logger = logging.getLogger("joyai.jarvis.session")

# ============================================================================
# Per-session wrapper
# ============================================================================


@dataclass
class JarvisSession:
    """A single Jarvis session attached to a webui session."""

    session_id: str
    state_machine: JarvisStateMachine
    state: JarvisState = JarvisState.KWS_LISTENING
    _feed_task: asyncio.Task | None = None
    _bg_task: asyncio.Task | None = None

    async def start(self):
        """Prewarm engines and launch the background state machine loop.

        ``prewarm_engines`` loads the KWS and ASR models in an executor
        *before* the bg loop starts. Without this, the first KWS wake
        fires against a cold ASR instance whose ~1.2s model load is eaten
        by the same-length confirm window, so every wake is rejected as
        a false alarm. See ``prewarm_engines`` docstring for details.
        """
        await self.state_machine.prewarm_engines()
        self._bg_task = asyncio.create_task(self.state_machine.run())

    async def stop(self):
        """Stop the session, cancelling both bg loop and any in-flight feed."""
        if self._bg_task:
            self._bg_task.cancel()
        # Cancel any in-flight diagnostic feed so it cannot keep pushing audio
        # after the session is being torn down.
        if self._feed_task and not self._feed_task.done():
            self._feed_task.cancel()
        # Wait for both tasks to actually finish so cancellation is observable.
        if self._bg_task:
            try:
                await self._bg_task
            except asyncio.CancelledError:
                pass
        if self._feed_task:
            try:
                await self._feed_task
            except asyncio.CancelledError:
                pass

    def attach_feed_task(self, task: asyncio.Task) -> None:
        """Track a background feed task so it can be cancelled on stop().

        If a previous feed task is still running it is cancelled first, so a
        new /api/jarvis/feed_wav call always supersedes the previous one.
        """
        if self._feed_task and not self._feed_task.done():
            self._feed_task.cancel()
        self._feed_task = task

    async def feed_audio(self, pcm: bytes):
        """Route mic audio to the state machine."""
        await self.state_machine.feed_audio(pcm)

    def check_exit_words(self, text: str) -> bool:
        """Check if an ASR partial/final text contains an exit word.

        Call from asr.py's forward_asr_results before forwarding to browser.
        """
        stripped = text.strip().lower()
        return any(stripped.endswith(w) for w in EXIT_WORDS)

    def should_synthesize(self) -> bool:
        """Should TTS be allowed? Blocks TTS when in KWS_LISTENING state."""
        return self.state_machine.state in (
            JarvisState.DIALOG_ACTIVE,
            JarvisState.TTS_PAUSED,
            JarvisState.WAKE_DETECTED,
        )

    def should_analyze_frame(self) -> bool:
        """Should VLM process video frames?

        In KWS_LISTENING, skip detailed analysis to save GPU.
        In DIALOG_ACTIVE, allow full analysis.
        """
        return self.state_machine.state != JarvisState.KWS_LISTENING

    def get_state_for_browser(self) -> dict:
        """Return a JSON-safe state snapshot for the browser UI."""
        return {
            "jarvis_state": self.state_machine.state.name,
            "wake_word": self.state_machine.config.wake_word,
        }

    def attach_audio_output(self, audio_output) -> None:
        """Bind an audio output callback (e.g. SpeakerAudioTrack.push_pcm)."""
        self.state_machine.audio_output = audio_output

    @property
    def is_awake(self) -> bool:
        """Is Jarvis currently awake / in conversation?"""
        return self.state_machine.state in (
            JarvisState.WAKE_DETECTED,
            JarvisState.DIALOG_ACTIVE,
            JarvisState.TTS_PAUSED,
        )


@dataclass
class LiveSession:
    """A single live session (免唤醒词常驻监听) attached to a webui session.

    Live reuses the same session frame as JarvisSession (session registration,
    WS push callbacks, ``feed_audio`` entry) while mounting a
    :class:`~.live_mode.LiveStateMachine` instead of a ``JarvisStateMachine``.
    The agent reply is played by the browser through the P0-A ``tts_sentence``
    queue, so no server-side audio output track is used.
    """

    session_id: str
    state_machine: LiveStateMachine
    _feed_task: asyncio.Task | None = None

    async def start(self):
        """Prewarm the ASR engine (same one-shot cost as jarvis prewarm)."""
        await self.state_machine.prewarm_engines()

    async def stop(self):
        """Stop the live session, cancelling all tasks and ending the turn."""
        await self.state_machine.stop()

    def attach_feed_task(self, task: asyncio.Task) -> None:
        """Track a background feed task so it can be cancelled on stop()."""
        if self._feed_task and not self._feed_task.done():
            self._feed_task.cancel()
        self._feed_task = task

    async def feed_audio(self, pcm: bytes):
        """Route mic audio to the live state machine."""
        await self.state_machine.feed_audio(pcm)

    def handle_frame(self, image_b64: str, ts_ms: float) -> None:
        """Delegate a screen/camera frame to the live state machine's ring buffer.

        Live visual path (spec draft-live-visual-cb.md §2.2): the WS ``frame``
        message routes through the session (mirroring the enroll passthrough
        pattern) into ``LiveStateMachine.handle_frame``.
        """
        self.state_machine.handle_frame(image_b64, ts_ms)

    def set_proactive(self, enabled: bool) -> bool:
        """Runtime toggle for the proactive speak loop (C.B layer 3).

        Delegates to ``LiveStateMachine.set_proactive`` — idempotent, and the
        ``LIVE_PROACTIVE_ENABLED`` env gate stays the final fallback.
        """
        return self.state_machine.set_proactive(enabled)

    def start_enroll(self) -> bool:
        """Delegate enrollment start to the live state machine."""
        return self.state_machine.start_enroll()

    def feed_enroll_pcm(self, pcm: bytes) -> None:
        """Delegate enrollment PCM buffering to the live state machine."""
        self.state_machine.feed_enroll_pcm(pcm)

    def finish_enroll(self) -> bool:
        """Delegate enrollment finalize to the live state machine."""
        return self.state_machine.finish_enroll()

    def cancel_enroll(self) -> None:
        """Delegate enrollment cancel to the live state machine."""
        self.state_machine.cancel_enroll()

    def attach_audio_output(self, audio_output) -> None:
        """Interface parity with JarvisSession; live replies play in-browser.

        Live plays TTS via ``tts_sentence`` WS messages (P0-A queue), so the
        WebRTC speaker track is never used. The callback is stored on the
        state machine so the interface stays symmetric.
        """
        self.state_machine.audio_output = audio_output

    def get_state_for_browser(self) -> dict:
        """Return a JSON-safe live state snapshot for the browser UI."""
        return self.state_machine.get_state_for_browser()

    @property
    def is_active(self) -> bool:
        """Is the live session still listening/replying?"""
        return self.state_machine.is_active()


# ============================================================================
# Session Manager
# ============================================================================


class JarvisSessionManager:
    """Manages multiple Jarvis sessions (one per webui session)."""

    def __init__(self, config: JarvisConfig | None = None):
        self.config = config or JarvisConfig()
        self._sessions: dict[str, JarvisSession] = {}
        # Phase C: live sessions live in a SEPARATE dict so a jarvis session
        # and a live session for the same webui session_id can coexist (双开)
        # without mutating each other's state machines.
        self._live_sessions: dict[str, LiveSession] = {}

    def set_asr_promotion_enabled(self, enabled: bool) -> None:
        """Toggle ASR promotion (local paraformer recall booster) at runtime.

        ``self.config`` is shared **by reference** with every session's
        ``JarvisStateMachine`` (``create_session`` passes ``config=self.config``),
        so mutating the attribute is immediately visible to all running state
        machines — no restart required. The per-session loop reinforces this
        for any session that was created with a detached config object (e.g. a
        test that builds a state machine with its own config instance).
        """
        enabled = bool(enabled)
        self.config.asr_promotion_enabled = enabled
        for session in self._sessions.values():
            try:
                session.state_machine.config.asr_promotion_enabled = enabled
            except Exception as exc:  # pragma: no cover
                logger.warning(
                    "Failed to propagate asr_promotion_enabled to session %s: %s",
                    session.session_id,
                    exc,
                )
        logger.info("ASR promotion (local paraformer) %s", "ENABLED" if enabled else "DISABLED")

    def get_asr_promotion_enabled(self) -> bool:
        """Return the current ASR promotion flag from the shared config."""
        return bool(getattr(self.config, "asr_promotion_enabled", False))

    async def create_session(
        self,
        session_id: str,
        audio_output=None,
        mode: str = "jarvis",
    ) -> JarvisSession | LiveSession:
        """Create a new session for the given webui session.

        ``mode`` selects the mounted state machine:
          * ``"jarvis"`` (default) — ``JarvisStateMachine`` (wake-gated KWS);
          * ``"live"`` — :class:`~.live_mode.LiveStateMachine` (免唤醒词常驻监听).

        If a session for session_id already exists (same mode), attach the
        new audio_output callback (if any) and return the existing one
        instead of creating a duplicate. Live sessions are tracked in a
        separate dict, so jarvis and live can run simultaneously.
        """
        if mode == "live":
            return await self._create_live_session(session_id, audio_output)

        if session_id in self._sessions:
            existing = self._sessions[session_id]
            if audio_output is not None:
                existing.attach_audio_output(audio_output)
            return existing

        sm = JarvisStateMachine(
            config=self.config,
            on_wake=lambda: logger.info("Session %s: wake detected", session_id),
            on_goodbye=lambda: logger.info("Session %s: goodbye", session_id),
            on_asr_partial=self._make_asr_callback(session_id),
            on_user_utterance=self._make_user_utterance_callback(session_id),
            on_llm_response=self._make_llm_callback(session_id),
            on_tts_sentence=self._make_tts_sentence_callback(session_id),
            audio_output=audio_output,
        )

        # v3.37: wire the BackgroundModelService that server.py registered
        # in ``sessions[session_id]`` so that ``_send_to_llm`` can route
        # ``</delegation>`` replies to the same hermes shim that the video
        # path uses. Looked up lazily so the binding survives later
        # server-side session creation (e.g. ``/api/session/cleanup``
        # recreates the session dict after a reset).
        try:
            from .server import sessions as _server_sessions

            def _bind_background_service():
                session_dict = _server_sessions.get(session_id) or {}
                bg = session_dict.get("background_service")
                if bg is not None:
                    sm._background_service = bg

            _bind_background_service()
        except Exception:  # pragma: no cover
            logger.debug("background_service lookup skipped for %s", session_id)

        session = JarvisSession(session_id=session_id, state_machine=sm)
        await session.start()
        self._sessions[session_id] = session
        logger.info("Jarvis session created: %s", session_id)
        return session

    async def _create_live_session(
        self,
        session_id: str,
        audio_output=None,
    ) -> LiveSession:
        """Create a live session (LiveStateMachine) for the webui session.

        Mirrors ``create_session``'s jarvis wiring: the same WS callbacks
        (``_make_asr_callback`` / ``_make_user_utterance_callback`` /
        ``_make_llm_callback`` / ``_make_tts_sentence_callback``) broadcast
        on the session's WebSocket, and the P1 reply_epoch is read from the
        live state machine (which owns ``_llm_reply_epoch`` like jarvis).
        """
        if session_id in self._live_sessions:
            existing = self._live_sessions[session_id]
            if audio_output is not None:
                existing.attach_audio_output(audio_output)
            return existing

        from .live_mode import LiveStateMachine

        sm = LiveStateMachine(
            config=self.config,
            session_id=session_id,
            on_asr_partial=self._make_asr_callback(session_id),
            on_user_utterance=self._make_user_utterance_callback(session_id),
            on_llm_response=self._make_llm_callback(session_id),
            on_tts_sentence=self._make_tts_sentence_callback(session_id),
        )

        # v3.37: wire the BackgroundModelService that server.py registered in
        # ``sessions[session_id]`` so ``</delegation>`` replies route to the
        # same hermes shim as jarvis / the video path (looked up lazily).
        try:
            from .server import sessions as _server_sessions

            def _bind_background_service():
                session_dict = _server_sessions.get(session_id) or {}
                bg = session_dict.get("background_service")
                if bg is not None:
                    sm._background_service = bg

            _bind_background_service()
        except Exception:  # pragma: no cover
            logger.debug("background_service lookup skipped for %s", session_id)

        session = LiveSession(session_id=session_id, state_machine=sm)
        await session.start()
        self._live_sessions[session_id] = session
        logger.info("Live session created: %s", session_id)
        return session

    def _make_asr_callback(self, session_id: str):
        """Build a callback that pushes ASR partial/final updates to the browser.

        The browser uses this to show what ASR is currently thinking in real
        time (replaces / supplements the on-screen typed text from the user)
        and to commit the final text when the endpoint is reached. Without
        this broadcast, ASR is a black box -- the only signal we have is the
        final pilot_utterance that fires after the user stops speaking.
        """

        def cb(partial: AsrPartial):
            try:
                from .ws_notify import notify_session_asr_partial

                notify_session_asr_partial(
                    session_id,
                    partial.text or "",
                    is_final=bool(getattr(partial, "is_final", False)),
                    reply_epoch=int(getattr(partial, "reply_epoch", 0) or 0),
                )
            except Exception as exc:  # pragma: no cover
                logger.warning("ASR partial broadcast failed for %s: %s", session_id, exc)

        return cb

    def _make_user_utterance_callback(self, session_id: str):
        """Build a callback that pushes final ASR text to the browser."""

        def cb(text: str):
            try:
                from .ws_notify import notify_session_pilot_utterance

                notify_session_pilot_utterance(
                    session_id,
                    text,
                    source="asr",
                    reply_epoch=self._session_reply_epoch(session_id),
                )
            except Exception as exc:  # pragma: no cover
                logger.warning(
                    "Pilot utterance broadcast failed for %s: %s",
                    session_id,
                    exc,
                )

        return cb

    def _make_llm_callback(self, session_id: str):
        """Broadcast the LLM reply to the browser.

        v3.37: ``</delegation>`` routing moved into
        :meth:`jarvis_mode.JarvisStateMachine._send_to_llm` so the voice
        path uses the same webinfer decision tokens that the video path
        does. The callback here only mirrors the cleaned text to the WS;
        no delegation routing here.
        """

        def cb(text: str, source: str = "jarvis_voice"):
            try:
                from .ws_notify import notify_session_llm_reply
            except Exception as exc:  # pragma: no cover
                logger.warning(
                    "LLM reply broadcast import failed for %s: %s",
                    session_id,
                    exc,
                )
                return
            try:
                notify_session_llm_reply(
                    session_id,
                    text,
                    source=source,
                    reply_epoch=self._session_reply_epoch(
                        session_id, attr="_current_turn_reply_epoch"
                    ),
                )
            except Exception as exc:  # pragma: no cover
                logger.warning(
                    "LLM reply broadcast failed for %s: %s",
                    session_id,
                    exc,
                )

        return cb

    def _make_tts_sentence_callback(self, session_id: str):
        """Push one P0-A streaming TTS sentence (WAV base64) to the browser.

        The jarvis state machine does not know its webui session_id, so this
        closure binds it and forwards to ``notify_session_tts_sentence``.
        """

        def cb(text: str, seq: int, audio_b64: str, session: int):
            try:
                from .ws_notify import notify_session_tts_sentence
            except Exception as exc:  # pragma: no cover
                logger.warning(
                    "tts_sentence broadcast import failed for %s: %s",
                    session_id,
                    exc,
                )
                return
            try:
                notify_session_tts_sentence(
                    session_id,
                    text or "",
                    seq,
                    audio_b64 or "",
                    session,
                )
            except Exception as exc:  # pragma: no cover
                logger.warning(
                    "tts_sentence broadcast failed for %s: %s",
                    session_id,
                    exc,
                )

        return cb

    def _session_reply_epoch(self, session_id: str, attr: str = "_llm_reply_epoch") -> int:
        """Read a P1 reply-epoch attribute from the session's state machine.

        Used by the WS broadcast callbacks so the payload carries the exact
        backend generation value (the state machine is the single source of
        truth for ``_llm_reply_epoch`` / ``_current_turn_reply_epoch``).
        Checks jarvis sessions first, then live sessions (both mount a state
        machine with the same epoch attributes). Returns 0 when the session
        (or attribute) is unavailable — the front-end treats a missing/0
        epoch as "no constraint yet".
        """
        session = self._sessions.get(session_id)
        if session is None:
            session = self._live_sessions.get(session_id)
        sm = session.state_machine if session else None
        try:
            return int(getattr(sm, attr, 0) or 0)
        except (TypeError, ValueError):  # pragma: no cover
            return 0

    def get_session(self, session_id: str) -> JarvisSession | None:
        """Get an existing Jarvis session, or None."""
        return self._sessions.get(session_id)

    def get_live_session(self, session_id: str) -> LiveSession | None:
        """Get an existing live session, or None."""
        return self._live_sessions.get(session_id)

    async def remove_session(self, session_id: str):
        """Stop and remove a session."""
        session = self._sessions.pop(session_id, None)
        if session:
            await session.stop()
            logger.info("Jarvis session removed: %s", session_id)

    async def remove_live_session(self, session_id: str):
        """Stop and remove a live session."""
        session = self._live_sessions.pop(session_id, None)
        if session:
            await session.stop()
            logger.info("Live session removed: %s", session_id)


# ============================================================================
# Singleton (optional)
# ============================================================================

_global_manager: JarvisSessionManager | None = None


def get_global_manager() -> JarvisSessionManager:
    """Get or create the global Jarvis session manager singleton."""
    global _global_manager
    if _global_manager is None:
        _global_manager = JarvisSessionManager()
    return _global_manager
