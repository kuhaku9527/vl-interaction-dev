// Extracted from index.html so the monolith shrinks (batch-3 split).
// Pure mechanical move: byte-for-byte function bodies, no logic change.
// Kept as top-level global declarations (classic script) so cross-file
// calls and shared `let`/`const` state keep working exactly as before.
// window.JoyXxx namespace is attached additively (D-033 pattern).

        function updateTtsSpeakingDisplay(text = '') {
            const speakingText = String(text || '').trim();
            if (ttsSpeakingLine) {
                ttsSpeakingLine.classList.toggle('show', Boolean(speakingText));
            }
            if (ttsSpeakingText) {
                ttsSpeakingText.textContent = speakingText;
            }
        }

        function stopTtsPlayback() {
            ttsRequestToken += 1;

            if (ttsWs && ttsWs.readyState === WebSocket.OPEN) {
                ttsWs._acceptingAudio = false;
                ttsWs._requestId = '';
                ttsWs.send(JSON.stringify({
                    type: 'stop',
                    request_id: String(ttsRequestToken)
                }));
            }

            ttsSources.forEach(source => {
                try {
                    source.stop();
                } catch (e) {
                    // Source may have already ended.
                }
            });
            ttsSources = [];
            ttsNextPlayTime = 0;
            ttsPcmRemainder = null;
            ttsSpeaking = false;
            pendingTtsText = '';
            updateTtsSpeakingDisplay('');
            if (ttsFinishTimer) {
                clearTimeout(ttsFinishTimer);
                ttsFinishTimer = null;
            }
        }

        function closeTtsWebSocket() {
            stopTtsPlayback();
            if (ttsWs && [WebSocket.CONNECTING, WebSocket.OPEN].includes(ttsWs.readyState)) {
                ttsWs.close();
            }
            ttsWs = null;
            ttsWsReadyPromise = null;
        }

        function maybeSpeakPendingTts() {
            if (ttsSpeaking || !pendingTtsText) {
                return;
            }

            const nextText = pendingTtsText;
            pendingTtsText = '';
            ttsRequestToken += 1;
            startTtsSpeech(nextText);
        }

        function getTtsWebSocket() {
            if (ttsWs && ttsWs.readyState === WebSocket.OPEN) {
                return Promise.resolve(ttsWs);
            }

            if (ttsWsReadyPromise && ttsWs && ttsWs.readyState === WebSocket.CONNECTING) {
                return ttsWsReadyPromise;
            }

            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const wsUrl = `${protocol}//${window.location.host}/api/tts`;
            const ws = new WebSocket(wsUrl);
            ttsWs = ws;
            ws.binaryType = 'arraybuffer';

            ttsWsReadyPromise = new Promise((resolve, reject) => {
                ws.onopen = () => {
                    if (ttsWs === ws) {
                        resolve(ws);
                    } else {
                        reject(new Error('TTS websocket was replaced'));
                    }
                };
                ws.onerror = (error) => {
                    if (ttsWs === ws) {
                        lastTtsEventKey = '';
                    }
                    reject(error instanceof Error ? error : new Error('TTS websocket failed'));
                };
            });

            ws.onmessage = (event) => {
                if (!settings.ttsEnabled) {
                    return;
                }

                if (typeof event.data === 'string') {
                    let message;
                    try {
                        message = JSON.parse(event.data);
                    } catch (error) {
                        console.warn('TTS sent invalid control message:', event.data);
                        return;
                    }

                    if (message.type === 'start') {
                        const startedRequestId = String(message.request_id || message.reqid || '');
                        if (startedRequestId === ws._pendingRequestId) {
                            ws._sampleRate = Number(message.sample_rate) || ws._sampleRate || 24000;
                            ws._requestId = startedRequestId;
                            ws._acceptingAudio = true;
                            ws._serverDone = false;
                        }
                    } else if (message.type === 'done' || message.type === 'stopped') {
                        if (message.request_id && String(message.request_id) === ws._requestId) {
                            ws._acceptingAudio = false;
                            ws._serverDone = true;
                            scheduleTtsFinishCheck(String(message.request_id));
                            maybeFinishTtsSpeech(String(message.request_id));
                        }
                    } else if (message.type === 'error') {
                        lastTtsEventKey = '';
                        console.warn('TTS playback failed:', message.error);
                        if (message.request_id && String(message.request_id) === ws._requestId) {
                            ws._acceptingAudio = false;
                            ws._serverDone = true;
                            maybeFinishTtsSpeech(String(message.request_id), { failed: true });
                        }
                    }
                    return;
                }

                const activeRequestId = String(ttsRequestToken);
                if (!ws._acceptingAudio || ws._requestId !== activeRequestId) {
                    return;
                }
                queueTtsPcmChunk(event.data, ws._sampleRate || 24000, ttsRequestToken);
            };

            ws.onclose = () => {
                if (ttsWs === ws) {
                    ttsWs = null;
                    ttsWsReadyPromise = null;
                }
            };

            return ttsWsReadyPromise;
        }

        function ensureTtsAudioContext() {
            const AudioContextClass = window.AudioContext || window.webkitAudioContext;
            if (!AudioContextClass) {
                throw new Error('Web Audio API is not supported');
            }
            if (!ttsAudioContext || ttsAudioContext.state === 'closed') {
                ttsAudioContext = new AudioContextClass();
            }
            if (ttsAudioContext.state === 'suspended') {
                ttsAudioContext.resume();
            }
            return ttsAudioContext;
        }

        function pcm16ToAudioBuffer(arrayBuffer, sampleRate) {
            const context = ensureTtsAudioContext();
            const pcm = new Int16Array(arrayBuffer);
            const audioBuffer = context.createBuffer(1, pcm.length, sampleRate);
            const channel = audioBuffer.getChannelData(0);

            for (let i = 0; i < pcm.length; i += 1) {
                channel[i] = Math.max(-1, Math.min(1, pcm[i] / 32768));
            }

            return audioBuffer;
        }

        function normalizePcmChunk(arrayBuffer) {
            let bytes = new Uint8Array(arrayBuffer);

            if (ttsPcmRemainder?.length) {
                const combined = new Uint8Array(ttsPcmRemainder.length + bytes.length);
                combined.set(ttsPcmRemainder, 0);
                combined.set(bytes, ttsPcmRemainder.length);
                bytes = combined;
                ttsPcmRemainder = null;
            }

            if (bytes.length % 2 !== 0) {
                ttsPcmRemainder = bytes.slice(bytes.length - 1);
                bytes = bytes.slice(0, bytes.length - 1);
            }

            return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
        }

        function queueTtsPcmChunk(arrayBuffer, sampleRate, requestToken) {
            if (requestToken !== ttsRequestToken || !settings.ttsEnabled || arrayBuffer.byteLength === 0) {
                return;
            }

            const pcmBuffer = normalizePcmChunk(arrayBuffer);
            if (pcmBuffer.byteLength === 0) {
                return;
            }

            const context = ensureTtsAudioContext();
            const source = context.createBufferSource();
            source.buffer = pcm16ToAudioBuffer(pcmBuffer, sampleRate);
            source.connect(context.destination);

            const startAt = Math.max(context.currentTime + 0.02, ttsNextPlayTime || 0);
            ttsNextPlayTime = startAt + source.buffer.duration;
            ttsSources.push(source);
            scheduleTtsFinishCheck(String(requestToken));
            source.onended = () => {
                ttsSources = ttsSources.filter(item => item !== source);
                maybeFinishTtsSpeech(String(requestToken));
            };
            source.start(startAt);
        }

        function scheduleTtsFinishCheck(requestId) {
            if (ttsFinishTimer) {
                clearTimeout(ttsFinishTimer);
            }

            let delayMs = 100;
            if (ttsAudioContext && ttsNextPlayTime > ttsAudioContext.currentTime) {
                delayMs = Math.max(100, (ttsNextPlayTime - ttsAudioContext.currentTime) * 1000 + 80);
            }

            ttsFinishTimer = setTimeout(() => {
                ttsFinishTimer = null;
                maybeFinishTtsSpeech(requestId);
            }, delayMs);
        }

        function maybeFinishTtsSpeech(requestId, { failed = false } = {}) {
            const ws = ttsWs;
            const activeRequestId = ws?._requestId || '';
            const hasQueuedAudio = ttsSources.length > 0;
            const serverStillStreaming = ws?._acceptingAudio && activeRequestId === requestId;
            const serverDone = ws?._serverDone && activeRequestId === requestId;

            if (serverStillStreaming || hasQueuedAudio) {
                return;
            }

            if ((String(ttsRequestToken) === requestId && serverDone) || failed) {
                if (ws && activeRequestId === requestId) {
                    ws._requestId = '';
                    ws._serverDone = false;
                }
                if (ttsFinishTimer) {
                    clearTimeout(ttsFinishTimer);
                    ttsFinishTimer = null;
                }
                ttsSpeaking = false;
                ttsPcmRemainder = null;
                updateTtsSpeakingDisplay('');
                maybeSpeakPendingTts();
            }
        }

        function startTtsSpeech(speechText) {
            const requestToken = ttsRequestToken;
            ttsSpeaking = true;
            updateTtsSpeakingDisplay(speechText);
            ttsPcmRemainder = null;
            ttsNextPlayTime = 0;

            getTtsWebSocket().then(ws => {
                if (requestToken !== ttsRequestToken || !settings.ttsEnabled) {
                    ttsSpeaking = false;
                    updateTtsSpeakingDisplay('');
                    maybeSpeakPendingTts();
                    return;
                }
                ws._sampleRate = 24000;
                ws._requestId = '';
                ws._pendingRequestId = String(requestToken);
                ws._acceptingAudio = false;
                ws._serverDone = false;
                ws.send(JSON.stringify({
                    type: 'speak',
                    text: speechText,
                    session_id: sessionId,
                    request_id: String(requestToken)
                }));
            }).catch(error => {
                if (requestToken === ttsRequestToken) {
                    lastTtsEventKey = '';
                    ttsSpeaking = false;
                    updateTtsSpeakingDisplay('');
                    console.warn('TTS playback failed:', error);
                    maybeSpeakPendingTts();
                }
            });
        }

        function speakVlmText(text, eventKey = '') {
            const speechText = String(text || '').trim();
            const speechEventKey = String(eventKey || '').trim();
            if (!settings.ttsEnabled || !speechText) {
                return;
            }
            if (speechEventKey && speechEventKey === lastTtsEventKey) {
                return;
            }

            lastTtsEventKey = speechEventKey || `event:${++ttsEventSequence}`;

            if (ttsSpeaking) {
                pendingTtsText = speechText;
                return;
            }

            ttsRequestToken += 1;
            startTtsSpeech(speechText);
        }

if (typeof window !== 'undefined') {
    window.JoyTtsPlayer = {
        speakVlmText,
        stopTtsPlayback,
        closeTtsWebSocket,
        getTtsWebSocket,
        queueTtsPcmChunk,
        startTtsSpeech,
        updateTtsSpeakingDisplay
    };
}
