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

        logger.info(
            "[live-mode] session %s initialized (state=%s, vad_threshold=%.2f, "
            "barge_in_threshold=%.2f, silence_timeout_ms=%d)",
            session_id,
            self._ctrl.state.name,
            self._ctrl.config.vad_threshold,
            self._ctrl.config.barge_in_threshold,
            self._ctrl.config.silence_timeout_ms,
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
        }

    def is_active(self) -> bool:
        """True while the live session is not ended/errored."""
        return self._ctrl.state not in (TurnState.ENDED, TurnState.ERROR)

    @property
    def turn_state(self) -> TurnState:
        """The underlying turn controller state (tests + status)."""
        return self._ctrl.state

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
        """
        now = time.time()

        # 1. VAD acoustic onset (rising edge). Fail-open: when the VAD is
        #    unavailable, ASR partial growth below is the speech signal.
        if self._vad is not None and self._vad.available and pcm and len(pcm) % 2 == 0:
            vad_speech = self._vad_speech(pcm)
            if vad_speech and not self._prev_vad_speech:
                self._feed_speech_started()
            self._prev_vad_speech = vad_speech

        # 2. Streaming ASR (16 kHz mono int16).
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
            await self._send_to_llm(utterance, interaction_mode="live", stream=True)
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
    ) -> None:
        """Send ASR text to webinfer (interaction_mode='live', stream=True).

        Reuses the shared :class:`StreamingTurnConsumer` (P0-A) so live and
        jarvis converge on one streaming path. Fail-open: pre-frame stream
        failure falls back to the single-shot call; mid-stream failure keeps
        the flushed sentences (consumer already handled).
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

            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{self._config.llm_api_url}{self._config.llm_text_path}",
                    json={
                        "model": self._config.llm_model,
                        "messages": messages,
                        "max_tokens": 200,
                        "temperature": 0.7,
                        "interaction_mode": interaction_mode,
                    },
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
            # silence / delegation / empty: THINKING -> SPEAKING -> LISTENING.
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
