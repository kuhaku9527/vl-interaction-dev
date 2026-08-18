// Extracted from index.html so the monolith shrinks (batch-3 split).
// Pure mechanical move: byte-for-byte function bodies, no logic change.
// Kept as top-level global declarations (classic script) so cross-file
// calls and shared `let`/`const` state keep working exactly as before.
// window.JoyXxx namespace is attached additively (D-033 pattern).

        // Play TTS audio whenever an llm_reply WS event arrives.
        // Hooked BEFORE connectWebSocket so the very first reply is caught.
        let lastLlmReplyAt = 0;
        let lastLlmReplyKey = null;
        // Barge-in (P0) reply-audio lifecycle state. `llmReplyEpoch` is bumped
        // every time the reply audio is stopped (user started speaking, or a
        // new reply supersedes the old one). Any in-flight /api/tts/synthesize
        // result that captured an older epoch is discarded so a stale reply
        // can never resume after the user has already barged in.
        let llmReplyEpoch = 0;
        // P1 reply-generation guard for `llm_reply` broadcasts. This is a
        // DIFFERENT concept from `llmReplyEpoch` (which guards TTS playback
        // races): `llmReplyGeneration` is the newest reply_epoch the browser
        // believes is current, raised ONLY by backend payloads (asr_partial /
        // pilot_utterance / accepted llm_reply), never bumped locally, so it
        // mirrors the backend's per-turn counter without drift. A late
        // llm_reply whose reply_epoch is older is dropped instead of playing
        // over the user's speech (barge-in / newer turn).
        // (llmReplyGeneration now lives on window.JoyState.llmReplyGeneration — see joy_state.js)
        let llmReplyAudioUrl = null;
        // P0-A per-sentence playback queue (tts_sentence WS messages). The
        // browser plays sentences in `seq` order; `llmReplyQueueSession`
        // identifies the LLM reply the sentences belong to so a new reply
        // supersedes a stale queue; `llmReplyQueueNextSeq` is the next seq
        // the queue expects. `stopLlmReplyAudio()` clears the whole queue
        // (barge-in / new reply) via the epoch guard.
        let llmReplyQueue = [];
        let llmReplyQueueSession = null;
        let llmReplyQueueNextSeq = 0;
        let llmReplyQueueBusy = false;
        // Client-first stop primitive: pause the reply audio element, reset its
        // position, release the object URL and invalidate any in-flight
        // synthesis. Idempotent — calling it when nothing is playing is a no-op.
        // Only affects the LLM-reply audio (btTtsPlayer); never wake/end tones
        // (those are short-lived events owned by the jarvis state machine).
        function stopLlmReplyAudio() {
            llmReplyEpoch += 1;
            llmReplyQueue = [];
            llmReplyQueueSession = null;
            llmReplyQueueNextSeq = 0;
            if (!btTtsPlayer) return;
            btTtsPlayer.pause();
            btTtsPlayer.currentTime = 0;
            btTtsPlayer.onended = null;
            if (llmReplyAudioUrl) {
                URL.revokeObjectURL(llmReplyAudioUrl);
                llmReplyAudioUrl = null;
            }
            btTtsPlayer.removeAttribute('src');
            btTtsPlayer.load();
        }
        async function playLlmReplyAudio(text, meta) {
            if (!text || !btTtsPlayer) return;
            // The jarvis/live state machine streams TTS to the browser via
            // tts_sentence WS messages in addition to broadcasting llm_reply.
            // Skip the front-end /api/tts/synthesize call in that case so we
            // don't double-play. jarvis_voice / live_voice are the back-end's
            // tags for the streaming paths; jarvis_text / live_text are the
            // text-only paths that need the front-end TTS.
            if (meta && (meta.source === 'jarvis_voice' || meta.source === 'live_voice')) return;
            // Dedupe: same reply text within 8s is treated as one utterance so a
            // retry / late WS broadcast never replays the last reply.
            const dedupKey = text + '|' + ((meta && meta.source) || 'default');
            const now = Date.now();
            if (lastLlmReplyKey === dedupKey && (now - lastLlmReplyAt) < 8000) return;
            lastLlmReplyKey = dedupKey;
            lastLlmReplyAt = now;
            // P0.1: stop the previous reply audio before starting a new one so
            // overlapping playback can never happen (switch-at-once semantics).
            stopLlmReplyAudio();
            const playEpoch = llmReplyEpoch;
            btLatency.ttsStartAt = performance.now();
            btLatency.ttsReadyAt = null;
            btLatency.ttsPlayAt = null;
            renderBtLatency();
            try {
                const resp = await fetch('/api/tts/synthesize', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ text }),
                });
                if (!resp.ok) {
                    console.warn('tts_synthesize failed', resp.status);
                    return;
                }
                const blob = await resp.blob();
                // P0.2 race guard: if the user started speaking while this
                // reply was being synthesized, the reply is stale — discard it
                // so it never resumes over the user's speech.
                if (playEpoch !== llmReplyEpoch) {
                    console.debug('[barge-in] stale reply TTS discarded (user spoke during synthesis)');
                    return;
                }
                btLatency.ttsReadyAt = performance.now();
                renderBtLatency();
                const url = URL.createObjectURL(blob);
                llmReplyAudioUrl = url;
                btTtsPlayer.src = url;
                try {
                    await btTtsPlayer.play();
                    // A barge-in may land while play() is starting; keep silent.
                    if (playEpoch !== llmReplyEpoch) {
                        btTtsPlayer.pause();
                        btTtsPlayer.currentTime = 0;
                        return;
                    }
                    btLatency.ttsPlayAt = performance.now();
                    renderBtLatency();
                } catch (e) { console.warn('audio play blocked', e); }
                btTtsPlayer.onended = () => {
                    URL.revokeObjectURL(url);
                    if (llmReplyAudioUrl === url) llmReplyAudioUrl = null;
                };
            } catch (err) {
                console.warn('playLlmReplyAudio error', err);
            }
        }

        // ---- P0-A: per-sentence playback queue (tts_sentence WS) ----
        // The backend streams the LLM reply as sentences; each tts_sentence
        // message carries WAV base64 audio + seq + session. The browser plays
        // them strictly in seq order; a barge-in / new reply (stopLlmReplyAudio)
        // clears the whole queue via the epoch guard. Never blocks forever:
        // out-of-order/stale sentences are skipped and playback errors advance
        // to the next sentence (log, don't hang).
        function enqueueLlmReplySentence(data) {
            if (!data || !data.text || !data.audio_b64) return;
            const session = Number(data.session || 0);
            const seq = Number(data.seq || 0);
            // A new reply session supersedes any stale queue (backstop; the
            // primary stop path is stopLlmReplyAudio on user speech).
            if (llmReplyQueueSession !== null && session !== llmReplyQueueSession) {
                console.debug('[tts-queue] reply session ' + session + ' supersedes ' + llmReplyQueueSession);
                llmReplyQueue = [];
                llmReplyQueueNextSeq = 0;
            }
            llmReplyQueueSession = session;
            llmReplyQueue.push({ seq: seq, session: session, text: data.text, audioB64: data.audio_b64 });
            llmReplyQueue.sort((a, b) => a.seq - b.seq);
            processLlmReplyQueue();
        }
        async function playLlmReplySentence(item) {
            if (!btTtsPlayer) return;
            const playEpoch = llmReplyEpoch;
            let url = null;
            if (item.audioB64) {
                try {
                    const bin = atob(item.audioB64);
                    const bytes = new Uint8Array(bin.length);
                    for (let i = 0; i < bin.length; i += 1) bytes[i] = bin.charCodeAt(i);
                    const blob = new Blob([bytes], { type: 'audio/wav' });
                    url = URL.createObjectURL(blob);
                } catch (err) {
                    console.warn('[tts-queue] audio decode failed, falling back to synthesize', err);
                }
            }
            if (!url) {
                // Fallback: synthesize this short sentence via the existing endpoint.
                try {
                    const resp = await fetch('/api/tts/synthesize', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ text: item.text }),
                    });
                    if (!resp.ok) {
                        console.warn('[tts-queue] synthesize fallback failed', resp.status);
                        return;
                    }
                    const blob = await resp.blob();
                    url = URL.createObjectURL(blob);
                } catch (err) {
                    console.warn('[tts-queue] synthesize fallback error', err);
                    return;
                }
            }
            if (playEpoch !== llmReplyEpoch) {
                if (url) URL.revokeObjectURL(url);
                return;
            }
            llmReplyAudioUrl = url;
            btTtsPlayer.src = url;
            try {
                await btTtsPlayer.play();
                if (playEpoch !== llmReplyEpoch) {
                    btTtsPlayer.pause();
                    btTtsPlayer.currentTime = 0;
                    URL.revokeObjectURL(url);
                    if (llmReplyAudioUrl === url) llmReplyAudioUrl = null;
                    return;
                }
            } catch (e) {
                console.warn('[tts-queue] audio play blocked', e);
                URL.revokeObjectURL(url);
                if (llmReplyAudioUrl === url) llmReplyAudioUrl = null;
                return;
            }
            // Wait for playback to finish OR the epoch to change (barge-in).
            await new Promise((resolve) => {
                let settled = false;
                const finish = () => {
                    if (settled) return;
                    settled = true;
                    clearInterval(watch);
                    btTtsPlayer.removeEventListener('ended', finish);
                    btTtsPlayer.removeEventListener('error', finish);
                    resolve();
                };
                const watch = setInterval(() => {
                    if (llmReplyEpoch !== playEpoch) finish();
                }, 100);
                btTtsPlayer.addEventListener('ended', finish);
                btTtsPlayer.addEventListener('error', finish);
            });
            URL.revokeObjectURL(url);
            if (llmReplyAudioUrl === url) llmReplyAudioUrl = null;
        }
        async function processLlmReplyQueue() {
            if (llmReplyQueueBusy) return;
            llmReplyQueueBusy = true;
            const queueEpoch = llmReplyEpoch;
            try {
                while (llmReplyQueue.length > 0) {
                    if (llmReplyEpoch !== queueEpoch) {
                        llmReplyQueue = [];
                        llmReplyQueueNextSeq = 0;
                        break;
                    }
                    const item = llmReplyQueue[0];
                    if (item.seq !== llmReplyQueueNextSeq) {
                        // Gap / out-of-order: skip (log, never hang).
                        llmReplyQueue.shift();
                        console.warn('[tts-queue] skipped out-of-order sentence seq=' + item.seq);
                        continue;
                    }
                    await playLlmReplySentence(item);
                    if (llmReplyEpoch !== queueEpoch) {
                        llmReplyQueue = [];
                        llmReplyQueueNextSeq = 0;
                        break;
                    }
                    llmReplyQueue.shift();
                    llmReplyQueueNextSeq += 1;
                }
            } finally {
                llmReplyQueueBusy = false;
            }
        }

if (typeof window !== 'undefined') {
    window.JoyLlmReplyAudio = {
        stopLlmReplyAudio,
        playLlmReplyAudio,
        enqueueLlmReplySentence,
        playLlmReplySentence,
        processLlmReplyQueue
    };
}
