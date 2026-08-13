// Extracted from index.html so the monolith shrinks (batch-3 split).
// Pure mechanical move: byte-for-byte function bodies, no logic change.
// Kept as top-level global declarations (classic script) so cross-file
// calls and shared `let`/`const` state keep working exactly as before.
// window.JoyXxx namespace is attached additively (D-033 pattern).

        function syncSpeechButtons() {
            const pressMode = isPressToTalkLayout();
            const speechActive = isSpeechActive();
            const speechVisualActive = isSpeechVisualActive();
            const idleTitle = isMobileLayout()
                ? (pressMode ? '按住说话' : '切换到语音输入')
                : '点击语音识别';

            promptEditor?.classList.toggle('mobile-voice-mode', pressMode);
            promptEditor?.classList.toggle('voice-ripple-active', pressMode && speechVisualActive);
            fullscreenPromptOverlay?.classList.toggle('voice-panel-open', pressMode);

            speechButtons.forEach(button => {
                button.classList.toggle('recording', speechVisualActive);
                button.title = speechVisualActive
                    ? (pressMode ? '松开停止' : '停止说话')
                    : idleTitle;

                const label = button.querySelector('.speech-label');
                if (label) {
                    if (speechVisualActive) {
                        label.textContent = pressMode ? '松手发送，上滑取消' : '停止说话';
                    } else {
                        label.textContent = pressMode ? '按住说话' : '说话';
                    }
                }
            });
            lucide.createIcons();
        }

        function isSpeechActive() {
            return Boolean(asrStarting || asrRecording);
        }

        function isSpeechVisualActive() {
            if (isPressToTalkLayout()) {
                return asrPointerId !== null || asrStarting || asrRecording || Boolean(asrStream || asrAudioContext || asrWs);
            }
            return isSpeechActive();
        }

        function setSpeechRecording(recording) {
            asrStarting = false;
            asrRecording = recording;
            syncSpeechButtons();
            updatePromptAvailability();
        }

        function suppressNextSpeechClick() {
            ignoreNextSpeechClick = true;
            if (ignoreSpeechClickTimeout) {
                clearTimeout(ignoreSpeechClickTimeout);
            }
            ignoreSpeechClickTimeout = setTimeout(() => {
                ignoreNextSpeechClick = false;
                ignoreSpeechClickTimeout = null;
            }, 450);
        }

        function floatToInt16(input) {
            const output = new Int16Array(input.length);
            for (let i = 0; i < input.length; i += 1) {
                const sample = Math.max(-1, Math.min(1, input[i]));
                output[i] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
            }
            return output;
        }

        function downsampleToInt16(input, inputSampleRate) {
            if (inputSampleRate === ASR_TARGET_SAMPLE_RATE) {
                return floatToInt16(input).buffer;
            }

            const ratio = inputSampleRate / ASR_TARGET_SAMPLE_RATE;
            const length = Math.floor(input.length / ratio);
            const output = new Float32Array(length);
            let inputOffset = 0;

            for (let i = 0; i < length; i += 1) {
                const nextOffset = Math.floor((i + 1) * ratio);
                let sum = 0;
                let count = 0;
                for (let j = inputOffset; j < nextOffset && j < input.length; j += 1) {
                    sum += input[j];
                    count += 1;
                }
                output[i] = count ? sum / count : 0;
                inputOffset = nextOffset;
            }

            return floatToInt16(output).buffer;
        }

        function getAudioRms(input) {
            let sum = 0;
            for (let i = 0; i < input.length; i += 1) {
                sum += input[i] * input[i];
            }
            return Math.sqrt(sum / Math.max(1, input.length));
        }

        function formatBtLatencyMs(ms) {
            if (ms == null || !Number.isFinite(ms) || ms < 0) return '--';
            if (ms < 1000) return `${Math.round(ms)}ms`;
            return `${(ms / 1000).toFixed(ms < 10000 ? 1 : 0)}s`;
        }

        function setBtLatencyText(element, text) {
            if (element) element.textContent = text || '--';
        }

        function renderBtLatency() {
            const now = performance.now();
            const asrText = btLatency.asrStartAt
                ? (btLatency.asrFirstPartialAt
                    ? `first ${formatBtLatencyMs(btLatency.asrFirstPartialAt - btLatency.asrStartAt)}`
                    : (btLatency.asrConnectedAt
                        ? `conn ${formatBtLatencyMs(btLatency.asrConnectedAt - btLatency.asrStartAt)}`
                        : `start ${formatBtLatencyMs(now - btLatency.asrStartAt)}`))
                : '--';
            const llmText = btLatency.sendStartAt
                ? (btLatency.llmReplyAt
                    ? formatBtLatencyMs(btLatency.llmReplyAt - btLatency.sendStartAt)
                    : (btLatency.sendAckAt ? 'waiting' : 'posting'))
                : '--';
            const ttsText = btLatency.ttsStartAt
                ? (btLatency.ttsReadyAt
                    ? formatBtLatencyMs(btLatency.ttsReadyAt - btLatency.ttsStartAt)
                    : 'loading')
                : '--';
            const endAt = btLatency.ttsReadyAt || btLatency.llmReplyAt || btLatency.sendAckAt;
            const e2eText = btLatency.sendStartAt && endAt
                ? formatBtLatencyMs(endAt - btLatency.sendStartAt)
                : '--';
            setBtLatencyText(btAsrLatencyValue, asrText);
            setBtLatencyText(btLlmLatencyValue, llmText);
            setBtLatencyText(btTtsLatencyValue, ttsText);
            setBtLatencyText(btE2eLatencyValue, e2eText);
        }

        function resetBtTurnLatency() {
            btLatency.sendStartAt = null;
            btLatency.sendAckAt = null;
            btLatency.llmReplyAt = null;
            btLatency.ttsStartAt = null;
            btLatency.ttsReadyAt = null;
            btLatency.ttsPlayAt = null;
            renderBtLatency();
        }
        function getMicErrorMessage(error) {
            if (!window.isSecureContext) {
                return '当前页面不是安全来源，浏览器会禁止麦克风。请用 localhost/127.0.0.1 访问，或改用 HTTPS。';
            }
            if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
                return '当前浏览器不支持麦克风采集，或页面没有麦克风权限。';
            }
            if (error && error.name === 'NotAllowedError') {
                return '麦克风权限被拒绝，请在浏览器地址栏允许麦克风后重试。';
            }
            if (error && error.name === 'NotFoundError') {
                return '没有找到可用麦克风。';
            }
            return `麦克风不可用：${error ? error.message : '未知错误'}`;
        }

        function sanitizeAsrTranscriptText(text) {
            return String(text || '')
                .replace(/<\/s>/gi, ' ')
                .replace(/\s{2,}/g, ' ')
                .trimStart();
        }

        function updatePromptFromSpeech() {
            const text = sanitizeAsrTranscriptText([asrFinalText, asrPartialText].filter(Boolean).join(''));
            promptText.value = text;
            resizePromptInput();
        }

        function resetAsrTranscriptState(baseText = '') {
            const text = String(baseText || '');
            asrFinalText = text;
            asrPartialText = '';
            asrLastFinalText = text;
        }

        function resetActiveAsrSegment() {
            if (!asrRecording || !asrStream || !asrAudioContext) {
                return;
            }
            if (asrPromptInputResetTimer) {
                clearTimeout(asrPromptInputResetTimer);
            }
            asrPromptInputResetTimer = setTimeout(() => {
                asrPromptInputResetTimer = null;
                if (!asrRecording || !asrStream || !asrAudioContext) {
                    return;
                }
                const oldWs = asrWs;
                asrResettingSegment = true;
                asrWs = null;
                try {
                    if (oldWs && oldWs.readyState === WebSocket.OPEN) {
                        oldWs.send(JSON.stringify({ type: 'segment_end' }));
                    }
                } catch (err) { /* ignore reset send errors */ }
                try {
                    if (oldWs && [WebSocket.CONNECTING, WebSocket.OPEN].includes(oldWs.readyState)) {
                        oldWs.close();
                    }
                } catch (err) { /* ignore reset close errors */ }
                connectAsrWebSocket();
                setTimeout(() => {
                    asrResettingSegment = false;
                }, 500);
            }, 120);
        }

        function handlePromptManualInput() {
            resetAsrTranscriptState(promptText.value);
            resetActiveAsrSegment();
            resizePromptInput();
        }

        function handleAsrResult(data) {
            const transcriptText = sanitizeAsrTranscriptText(data.text);
            if (!transcriptText || !['IS_PARTIAL', 'IS_FINAL', 'IS_END'].includes(data.event)) {
                return;
            }
            if (data.final) {
                btLatency.asrFinalAt = performance.now();
                renderBtLatency();
                if (transcriptText === asrLastFinalText || asrFinalText.endsWith(transcriptText)) {
                    asrPartialText = '';
                    updatePromptFromSpeech();
                    if (asrSendPromptWhenFinal) {
                        stopSpeech({ sendEnd: false, sendPrompt: true });
                    }
                    return;
                }
                asrLastFinalText = transcriptText;
                asrFinalText += transcriptText;
                asrPartialText = '';
                updatePromptFromSpeech();
                if (asrSendPromptWhenFinal) {
                    stopSpeech({ sendEnd: false, sendPrompt: true });
                }
            } else {
                if (data.event === 'IS_PARTIAL') {
                    // Barge-in signal from the browser ASR path: the user is
                    // speaking, so stop any reply audio (client-first; no-op
                    // when nothing is playing).
                    stopLlmReplyAudio();
                }
                asrPartialText = transcriptText;
                updatePromptFromSpeech();
            }
        }

        async function prepareSpeechMic() {
            asrStream = await navigator.mediaDevices.getUserMedia({
                // 补齐 Chrome 显式 goog* 约束；googHighpassFilter/googTypingNoiseDetection 无标准等价物，去低频轰鸣/抑键盘噪声，提升 ASR 主采集流质量。
                audio: {
                    channelCount: 1,
                    echoCancellation: true,
                    noiseSuppression: true,
                    autoGainControl: true,
                    googEchoCancellation: true,
                    googNoiseSuppression: true,
                    googAutoGainControl: true,
                    googHighpassFilter: true,
                    googTypingNoiseDetection: true
                }
            });

            asrAudioContext = new AudioContext();
            asrSource = asrAudioContext.createMediaStreamSource(asrStream);
            asrProcessor = asrAudioContext.createScriptProcessor(ASR_AUDIO_BUFFER_SIZE, 1, 1);
            asrSilence = asrAudioContext.createGain();
            asrSilence.gain.value = 0;
            asrLastVoiceTime = performance.now();

            asrProcessor.onaudioprocess = (event) => {
                if (!asrRecording || !asrWs || asrWs.readyState !== WebSocket.OPEN) {
                    return;
                }

                const input = event.inputBuffer.getChannelData(0);
                const rms = getAudioRms(input);
                const now = performance.now();
                if (rms >= ASR_SILENCE_RMS_THRESHOLD) {
                    asrLastVoiceTime = now;
                } else if (asrStopMode === 'auto' && now - asrLastVoiceTime >= ASR_SILENCE_AUTO_STOP_MS) {
                    stopSpeech();
                    return;
                }

                const audio = downsampleToInt16(input, asrAudioContext.sampleRate);
                if (audio.byteLength > 0) {
                    asrWs.send(audio);
                }
            };

            asrSource.connect(asrProcessor);
            asrProcessor.connect(asrSilence);
            asrSilence.connect(asrAudioContext.destination);
        }

        function connectAsrWebSocket() {
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const continuous = asrStopMode === 'toggle' ? '&continuous=1' : '';
            const wsUrl = `${protocol}//${window.location.host}${ASR_WS_PATH}?session_id=${encodeURIComponent(sessionId)}${continuous}`;
            const ws = new WebSocket(wsUrl);
            asrWs = ws;
            ws.binaryType = 'arraybuffer';

            ws.onmessage = (event) => {
                if (asrWs !== ws) {
                    return;
                }
                const data = JSON.parse(event.data);
                if (data.type === 'status' && data.message === 'connected') {
                    btLatency.asrConnectedAt = performance.now();
                    renderBtLatency();
                    setSpeechRecording(true);
                    updatePromptAvailability();
                    return;
                }
                if (data.type === 'error') {
                    updateStatus(data.message, 'disconnected');
                    stopSpeech({ sendEnd: false, sendPrompt: false });
                    return;
                }
                if (data.type === 'result') {
                    handleAsrResult(data);
                }
            };

            ws.onclose = () => {
                if (asrWs !== ws) {
                    return;
                }
                const shouldSend = asrSendPromptWhenFinal && Boolean(promptText.value.trim());
                asrWs = null;
                if (!asrResettingSegment && (asrRecording || asrStream || asrAudioContext || asrSendPromptWhenFinal)) {
                    stopSpeech({ sendEnd: false, sendPrompt: shouldSend });
                }
                updatePromptAvailability();
            };

            ws.onerror = () => {
                if (asrWs !== ws) {
                    return;
                }
                updateStatus(window.JoyI18n.localizeUiString('ASR connection failed'), 'disconnected');
                stopSpeech({ sendEnd: false, sendPrompt: false });
                updatePromptAvailability();
            };
        }

        async function startSpeech({ mode = 'toggle' } = {}) {
            if (isSpeechActive() || asrStream || asrAudioContext || asrWs) {
                return;
            }

            const token = ++asrStartToken;
            btLatency.asrStartAt = performance.now();
            btLatency.asrMicReadyAt = null;
            btLatency.asrConnectedAt = null;
            btLatency.asrFirstPartialAt = null;
            btLatency.asrFinalAt = null;
            renderBtLatency();
            asrStarting = true;
            syncSpeechButtons();
            updatePromptAvailability();
            asrStopMode = mode;
            asrStopRequested = false;
            if (mode !== 'press') {
                speechButtons.forEach(button => {
                    button.disabled = true;
                });
            }
            asrFinalText = '';
            asrPartialText = '';
            asrLastFinalText = '';
            asrSendPromptWhenFinal = false;
            if (asrFinalFallbackTimer) {
                clearTimeout(asrFinalFallbackTimer);
                asrFinalFallbackTimer = null;
            }
            promptText.value = '';
            resizePromptInput();

            try {
                await prepareSpeechMic();
                btLatency.asrMicReadyAt = performance.now();
                renderBtLatency();
                if (token !== asrStartToken || asrStopRequested) {
                    await stopSpeech({ sendEnd: false, sendPrompt: false });
                    return;
                }
                connectAsrWebSocket();
            } catch (error) {
                updateStatus(getMicErrorMessage(error), 'disconnected');
                updatePromptAvailability();
                await stopSpeech({ sendEnd: false, sendPrompt: false });
            }
        }

        async function stopSpeech({ sendEnd = true, sendPrompt = true } = {}) {
            asrStartToken += 1;
            asrStopRequested = true;
            const wasRecording = asrRecording;
            asrPointerId = null;
            asrStarting = false;
            setSpeechRecording(false);
            if (sendEnd && wasRecording) {
                asrSendPromptWhenFinal = sendPrompt;
                speechButtons.forEach(button => {
                    button.disabled = true;
                });
                if (asrFinalFallbackTimer) {
                    clearTimeout(asrFinalFallbackTimer);
                }
                asrFinalFallbackTimer = setTimeout(() => {
                    if (asrSendPromptWhenFinal) {
                        stopSpeech({
                            sendEnd: false,
                            sendPrompt: Boolean(promptText.value.trim())
                        });
                    }
                }, ASR_FINAL_FALLBACK_MS);
            }

            if (asrProcessor) {
                asrProcessor.disconnect();
            }
            if (asrSource) {
                asrSource.disconnect();
            }
            if (asrSilence) {
                asrSilence.disconnect();
            }
            if (asrStream) {
                asrStream.getTracks().forEach(track => track.stop());
            }
            if (asrAudioContext) {
                await asrAudioContext.close();
            }

            asrProcessor = null;
            asrSource = null;
            asrSilence = null;
            asrStream = null;
            asrAudioContext = null;

            if (sendEnd && asrWs && asrWs.readyState === WebSocket.OPEN) {
                asrWs.send(JSON.stringify({ type: 'end' }));
            }
            if (asrWs && !sendEnd && [WebSocket.CONNECTING, WebSocket.OPEN].includes(asrWs.readyState)) {
                asrWs.close();
            }
            if (!sendEnd && sendPrompt && promptText.value.trim()) {
                sendBtPrompt();
            }
            if (!sendEnd) {
                if (asrFinalFallbackTimer) {
                    clearTimeout(asrFinalFallbackTimer);
                    asrFinalFallbackTimer = null;
                }
                if (asrPromptInputResetTimer) {
                    clearTimeout(asrPromptInputResetTimer);
                    asrPromptInputResetTimer = null;
                }
                asrResettingSegment = false;
                asrSendPromptWhenFinal = false;
                if (isMobileLayout()) {
                    mobileVoiceMode = false;
                }
                updatePromptAvailability();
            }
        }

        function finishPressToTalk({ sendEnd = true, sendPrompt = true } = {}) {
            if (!isPressToTalkLayout() && asrPointerId === null) {
                return;
            }
            asrPointerId = null;
            stopSpeech({ sendEnd, sendPrompt });
            mobileVoiceMode = false;
            updatePromptAvailability();
        }

        promptPresetBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            const willOpen = promptPresetMenu.classList.contains('hidden');
            closePromptPopovers(willOpen ? 'presets' : null);
            promptPresetMenu.classList.toggle('hidden', !willOpen);
            promptPresetBtn.classList.toggle('active', willOpen);
        });

        promptPresetOptions.forEach(option => {
            option.addEventListener('click', () => {
                promptPresetOptions.forEach(item => item.classList.remove('selected'));
                option.classList.add('selected');
                promptText.value = option.dataset.prompt || '';
                resizePromptInput();
                closePromptPopovers();
                promptText.focus();
            });
        });

        document.addEventListener('click', (e) => {
            if (!e.target.closest('.chat-prompt-shell')) {
                closePromptPopovers();
            }
        });

        let promptImeComposing = false;
        let promptCompositionEndedAt = 0;

        promptText.addEventListener('compositionstart', () => {
            promptImeComposing = true;
        });

        promptText.addEventListener('compositionend', () => {
            promptImeComposing = false;
            promptCompositionEndedAt = Date.now();
        });

        function isPromptImeEnter(e) {
            return Boolean(
                promptImeComposing ||
                e.isComposing ||
                e.keyCode === 229 ||
                Date.now() - promptCompositionEndedAt < 30
            );
        }

        // Apply prompt on Enter key (Shift+Enter for newline)
        promptText.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                if (isPromptImeEnter(e)) {
                    return;
                }
                e.preventDefault();
                sendBtPrompt();
            }
        });

        promptText.addEventListener('input', handlePromptManualInput);

        function toggleSpeechByClick() {
            if (asrRecording) {
                stopSpeech();
            } else {
                startSpeech({ mode: 'toggle' });
            }
        }

        function enterMobileVoiceMode(event) {
            if (!isMobileLayout() || mobileVoiceMode) {
                return false;
            }
            event.preventDefault();
            mobileVoiceMode = true;
            closePromptPopovers();
            updatePromptAvailability();
            return true;
        }

        function preventSpeechLongPressMenu(event) {
            if (
                isMobileLayout() &&
                (event.target === speechBtn || event.target.closest?.('.speech-control, .mobile-voice-mode'))
            ) {
                event.preventDefault();
            }
        }

        function startPressToTalk(event) {
            if (!isMobileLayout()) {
                return;
            }
            event.preventDefault();
            if (enterMobileVoiceMode(event)) {
                asrPointerId = event.pointerId;
                suppressNextSpeechClick();
                event.currentTarget.setPointerCapture?.(event.pointerId);
                syncSpeechButtons();
                startSpeech({ mode: 'press' });
                return;
            }
            if (isSpeechActive() || asrStream || asrAudioContext || asrWs) {
                return;
            }
            asrPointerId = event.pointerId;
            suppressNextSpeechClick();
            event.currentTarget.setPointerCapture?.(event.pointerId);
            syncSpeechButtons();
            startSpeech({ mode: 'press' });
        }

        function stopPressToTalk(event) {
            if (!isPressToTalkLayout() || asrPointerId !== event.pointerId) {
                return;
            }
            event.preventDefault();
            event.currentTarget.releasePointerCapture?.(event.pointerId);
            finishPressToTalk({ sendEnd: true, sendPrompt: true });
        }

        speechBtn.addEventListener('pointerdown', startPressToTalk);
        speechBtn.addEventListener('contextmenu', preventSpeechLongPressMenu);
        speechBtn.addEventListener('selectstart', preventSpeechLongPressMenu);
        promptEditor?.addEventListener('contextmenu', preventSpeechLongPressMenu);
        promptEditor?.addEventListener('selectstart', preventSpeechLongPressMenu);
        speechBtn.addEventListener('pointerup', stopPressToTalk);
        speechBtn.addEventListener('pointercancel', stopPressToTalk);
        speechBtn.addEventListener('pointerleave', stopPressToTalk);
        document.addEventListener('pointerup', (event) => {
            if (asrPointerId !== null && event.pointerId === asrPointerId) {
                finishPressToTalk({ sendEnd: true, sendPrompt: true });
            }
        }, true);
        document.addEventListener('pointercancel', (event) => {
            if (asrPointerId !== null && event.pointerId === asrPointerId) {
                finishPressToTalk({ sendEnd: false, sendPrompt: false });
            }
        }, true);
        window.addEventListener('blur', () => {
            if (asrPointerId !== null || isPressToTalkLayout()) {
                finishPressToTalk({ sendEnd: false, sendPrompt: false });
            }
        });
        document.addEventListener('visibilitychange', () => {
            if (document.hidden && (asrPointerId !== null || isPressToTalkLayout())) {
                finishPressToTalk({ sendEnd: false, sendPrompt: false });
            }
        });
        speechBtn.addEventListener('click', (event) => {
            if (ignoreNextSpeechClick) {
                event.preventDefault();
                ignoreNextSpeechClick = false;
                if (ignoreSpeechClickTimeout) {
                    clearTimeout(ignoreSpeechClickTimeout);
                    ignoreSpeechClickTimeout = null;
                }
                return;
            }
            if (isPressToTalkLayout()) {
                event.preventDefault();
                return;
            }
            if (enterMobileVoiceMode(event)) {
                return;
            }
            toggleSpeechByClick();
        });

        const btMicGainSelectEl = document.getElementById('btMicGainSelect');
        if (btMicGainSelectEl) {
            btMicGainSelectEl.addEventListener('change', () => {
                const v = parseFloat(btMicGainSelectEl.value || '1.0');
                if (btListenGainNode && typeof btListenGainNode.gain === 'object') {
                    btListenGainNode.gain.setTargetAtTime(v, btListenGainNode.context.currentTime, 0.02);
                    console.log('BT mic gain set to', v + 'x');
                } else {
                    console.log('BT mic gain stored for next start:', v + 'x');
                }
            });
        }

if (typeof window !== 'undefined') {
    window.JoySpeechInput = {
        startSpeech,
        stopSpeech,
        handleAsrResult,
        connectAsrWebSocket,
        prepareSpeechMic,
        syncSpeechButtons,
        renderBtLatency,
        resetAsrTranscriptState,
        resetActiveAsrSegment
    };
}
