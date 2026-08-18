// Extracted from index.html so the monolith shrinks (batch-3 split).
// Pure mechanical move: byte-for-byte function bodies, no logic change.
// Kept as top-level global declarations (classic script) so cross-file
// calls and shared `let`/`const` state keep working exactly as before.
// window.JoyXxx namespace is attached additively (D-033 pattern).

        function connectWebSocket() {
            window.JoyWs.connectWebSocket();
        }

        // dispatchServerMessage: server→client protocol router (was ws.onmessage).
        // Kept INLINE so its reassignments of monolith closure vars (sessionId, serverConfigApplied,
        // lastText, fadeTimeout) stay the single source of truth. window.JoyWs.connectWebSocket
        // wires it via the dispatchServerMessage ref passed in JoyWs.register(...).
        // Guard uses event.target (the socket that fired) vs the closure 'websocket' (current socket),
        // preserving the original stale-socket protection.
        function dispatchServerMessage(event) {
            if (websocket !== event.target) {
                return;
            }
            const data = JSON.parse(event.data);

            if (data.type === 'vlm_response') {
                if (!isAnalysisRunning) {
                    return;
                }

                if (data.text !== lastText) {
                    resultText.classList.remove('fade');

                    if (fadeTimeout) {
                        clearTimeout(fadeTimeout);
                    }

                    // Start fade after 2 seconds if enabled
                    if (settings.fade) {
                        fadeTimeout = setTimeout(() => {
                            resultText.classList.add('fade');
                        }, 2000);
                    }
                }

                updateResultText(data.text, data.metrics, data.metrics?.background_handoff || null);
                lastText = data.text;

                // Update video overlay (always, visibility is controlled by CSS)
                const displayText = getVlmDisplayText(data.text);
                videoOverlay.textContent = displayText;

                // Latency loop closure (issue #43): correlate the rendered vlm_response back
                // to the screen frame that produced it via frame_seq. NOTE: sendToRenderMs
                // spans the WHOLE tSend->render interval (upstream transport + backend VLM
                // inference + downstream + render scheduling), NOT a pure render-only segment.
                if (data.frame_seq != null && typeof window !== 'undefined' && window.__screenSentAt) {
                  const sentAt = window.__screenSentAt[data.frame_seq];
                  if (sentAt != null) {
                    const sendToRenderMs = performance.now() - sentAt;
                    if (!window.__screenRenderLatency) window.__screenRenderLatency = [];
                    const sample = {
                      seq: data.frame_seq,
                      send_to_render_ms: Math.round(sendToRenderMs * 100) / 100,
                      ts: Date.now(),
                      text_len: (data.text || '').length,
                    };
                    window.__screenRenderLatency.push(sample);
                    if (window.__screenRenderLatency.length > 120) window.__screenRenderLatency.shift();
                    console.info('[latency][send→render]', sample);
                    // 尽力持久化（#43 收口）：把样本上报到服务端。fire-and-forget，不阻塞渲染；keepalive 使其能跨页面卸载存活。
                    try {
                      fetch('/api/screen-latency', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(sample),
                        keepalive: true,
                      }).catch((err) => { console.warn('[latency] persist failed', err); });
                    } catch (err) {
                      console.warn('[latency] persist skipped', err);
                    }
                    delete window.__screenSentAt[data.frame_seq];
                  }
                }

                videoOverlay.classList.remove('show', 'top', 'bottom');
                if (displayText && settings.overlayPosition !== 'none') {
                    videoOverlay.classList.add('show', settings.overlayPosition);
                }

                if (data.metrics) {
                    metricsInline.style.display = 'flex';
                    latencyValue.textContent = Math.round(data.metrics.last_latency_ms);
                    avgLatencyValue.textContent = Math.round(data.metrics.avg_latency_ms);
                    countValue.textContent = data.metrics.total_inferences;
                    if (
                        revealVideoAfterMetricsToken === streamStartToken &&
                        Number(data.metrics.total_inferences) > 0
                    ) {
                        revealVideoWhenReady(streamStartToken);
                    }
                    logLatencyBreakdown(data.metrics);
                }
                if (data.summarizer_timing) {
                    const st = data.summarizer_timing;
                    const stKey = JSON.stringify(st);
                    if (stKey !== window._lastSummarizerKey) {
                        window._lastSummarizerKey = stKey;
                        let parts = [];
                        if (st.last_mid_term_ms != null) parts.push(`mid-term=${st.last_mid_term_ms}ms (chunk#${st.last_mid_term_chunk})`);
                        if (st.barrier_wait_ms != null) parts.push(`barrier_wait=${st.barrier_wait_ms}ms`);
                        if (st.last_long_term_ms != null) parts.push(`long-term=${st.last_long_term_ms}ms`);
                        if (parts.length) console.log(`[summary model] ${parts.join(', ')}`);
                    }
                }
                if (data.request_payload) {
                    const block = document.getElementById('requestPayloadDebug');
                    const pre = document.getElementById('requestPayloadContent');
                    if (block && pre && document.getElementById('debugShowRequestPayload')?.checked) {
                        block.style.display = 'block';
                        pre.textContent = JSON.stringify(data.request_payload, null, 2);
                    }
                }
                if (data.response_payload) {
                    const block = document.getElementById('responsePayloadDebug');
                    const pre = document.getElementById('responsePayloadContent');
                    if (block && pre && document.getElementById('debugShowResponsePayload')?.checked) {
                        block.style.display = 'block';
                        // Redact model decision tokens before exposing the raw payload.
                        pre.textContent = JSON.stringify(sanitizeDebugPayload(data.response_payload), null, 2);
                    }
                }
                if (data.memory_state) {
                    const summaries = data.memory_state.mid_term_summaries || [];
                    const longTerm = data.memory_state.long_term_memory || '';
                    const memHash = JSON.stringify(data.memory_state);
                    if (memHash !== _lastMemoryStateHash) {
                        _lastMemoryStateHash = memHash;
                        // UI display (controlled by toggle)
                        const memBlock = document.getElementById('memoryStateDebug');
                        if (memBlock && document.getElementById('debugShowMemoryState')?.checked) {
                            memBlock.style.display = 'block';
                            const midPre = document.getElementById('midTermMemoryContent');
                            if (midPre) {
                                midPre.textContent = summaries.length
                                    ? summaries.map(s => `[Chunk ${s.chunk_index} | ${s.frame_range}]\n${s.summary_text}`).join('\n\n')
                                    : '(empty)';
                            }
                            const ltPre = document.getElementById('longTermMemoryContent');
                            if (ltPre) {
                                ltPre.textContent = longTerm || '(empty)';
                            }
                        }
                        // Console output
                        console.group('%c[Memory State]', 'color: #4fc3f7; font-weight: bold;');
                        if (summaries.length) {
                            console.groupCollapsed(`Mid-term memory (${summaries.length} chunks)`);
                            summaries.forEach(s => {
                                console.log(`%c[Chunk ${s.chunk_index} | ${s.frame_range}]`, 'color: #81c784;', '\n' + s.summary_text);
                            });
                            console.groupEnd();
                        } else {
                            console.log('Mid-term memory: (empty)');
                        }
                        if (longTerm) {
                            console.groupCollapsed('Long-term memory');
                            console.log(longTerm);
                            console.groupEnd();
                        } else {
                            console.log('Long-term memory: (empty)');
                        }
                        console.groupEnd();
                    }
                }
            } else if (data.type === 'status') {
                // Don't show status messages in result balloon (too flashy)
                // Status is already shown in the header
            } else if (
                data.type === 'background_task_started' ||
                data.type === 'background_result_ready' ||
                data.type === 'background_result_error'
            ) {
                appendOrUpdateBackgroundEntry(data);
            } else if (data.type === 'server_config') {
                if (data.session_id) {
                    window.sessionId = data.session_id;
                }
                // Server sent its current configuration (model, api_base, prompt)
                if (data.model) {
                    document.getElementById('modelName').textContent = data.model;
                    // Also update the model select if it matches
                    if (modelSelect.querySelector(`option[value="${data.model}"]`)) {
                        modelSelect.value = data.model;
                    }
                }
                if (data.api_base) {
                    apiBaseUrl.value = data.api_base;
                    checkApiKeyRequirement(apiBaseUrl.value);
                    serverConfigApplied = true;
                    fetchModels();
                }
                if (data.process_interval != null && !isNaN(data.process_interval) && processEvery) {
                    processEvery.value = String(data.process_interval);
                }
                if (data.frames_per_batch != null && !isNaN(data.frames_per_batch) && framesPerBatch) {
                    framesPerBatch.value = String(data.frames_per_batch);
                }
                if (data.background_model) {
                    applyBackgroundConfig(data.background_model);
                }
                // ASR promotion (joyai-asr-promotion-ui): reflect runtime toggle + serving model name
                if (typeof data.asr_promotion_enabled === 'boolean') {
                    if (asrPromotionToggle) asrPromotionToggle.checked = data.asr_promotion_enabled;
                    settings.asrPromotionEnabled = data.asr_promotion_enabled;
                }
                if (asrModelName) {
                    asrModelName.textContent = (data.asr_model_name && typeof data.asr_model_name === 'string')
                        ? data.asr_model_name
                        : '—';
                }
                const reqCb = document.getElementById('debugShowRequestPayload');
                const resCb = document.getElementById('debugShowResponsePayload');
                const memCb = document.getElementById('debugShowMemoryState');
                if ((reqCb?.checked || resCb?.checked || memCb?.checked) && websocket && websocket.readyState === WebSocket.OPEN) {
                    websocket.send(JSON.stringify({
                        type: 'set_debug',
                        show_request_payload: !!reqCb?.checked,
                        show_response_payload: !!resCb?.checked,
                        show_memory_state: !!memCb?.checked
                    }));
                }
            } else if (data.type === 'model_updated') {
                // Model was updated on server
                if (data.model) {
                    document.getElementById('modelName').textContent = data.model;
                    console.log('Model updated to:', data.model);
                }
            } else if (data.type === 'prompt_updated') {
                // Prompt was updated on server (already handled in applyPrompt)
                console.log('Prompt updated:', data.prompt);
            } else if (data.type === 'processing_updated') {
                if (data.process_interval != null && processEvery) processEvery.value = String(data.process_interval);
                console.log('Processing interval updated:', data.process_interval, 's');
            } else if (data.type === 'frames_per_batch_updated') {
                if (data.frames_per_batch != null && framesPerBatch) framesPerBatch.value = String(data.frames_per_batch);
                if (data.background_model) applyBackgroundConfig(data.background_model);
                console.log('Frames per batch updated:', data.frames_per_batch);
            } else if (data.type === 'background_config_updated') {
                applyBackgroundConfig(data.background_model);
                console.log('Background model config updated:', data.background_model);
            } else if (data.type === 'asr_promotion_updated') {
                if (typeof data.enabled === 'boolean') {
                    if (asrPromotionToggle) asrPromotionToggle.checked = data.enabled;
                    settings.asrPromotionEnabled = data.enabled;
                }
                if (asrModelName) {
                    asrModelName.textContent = (data.asr_model_name && typeof data.asr_model_name === 'string')
                        ? data.asr_model_name
                        : '—';
                }
                console.log('ASR promotion updated:', data.enabled, data.asr_model_name || '');
            } else if (data.type === 'silence_wake') {
                // 无线电静默唤醒音频（B3, draft-radio-silence.md §5）：后端消费 live
                // done frame silence:{wake:true} 后把 prompts/bt/events/wake.wav 以
                // WAV base64 推来（live 无服务端扬声器，走浏览器播放）。与 tts_sentence
                // 同通道但独立事件——不接队列（无 seq/会话语义）、不产生文本气泡。
                if (data.audio_b64) {
                    playSilenceWakeAudio(data.audio_b64);
                }
            }

        }

        // silence_wake 唤醒音频播放（B3）：atob→Blob(audio/wav)→独立 #silenceWakePlayer
        // 播一次。用独立元素而非 btTtsPlayer，避免打断 LLM 逐句回复队列（其有
        // epoch/barge-in 不变式）。播放结束/出错即释放 object URL，防泄漏。
        let _silenceWakeUrl = null;
        function playSilenceWakeAudio(audioB64) {
            const player = document.getElementById('silenceWakePlayer');
            if (!player || !audioB64) return;
            try {
                const bin = atob(audioB64);
                const bytes = new Uint8Array(bin.length);
                for (let i = 0; i < bin.length; i += 1) bytes[i] = bin.charCodeAt(i);
                const blob = new Blob([bytes], { type: 'audio/wav' });
                const url = URL.createObjectURL(blob);
                if (_silenceWakeUrl) URL.revokeObjectURL(_silenceWakeUrl);
                _silenceWakeUrl = url;
                const release = () => {
                    if (_silenceWakeUrl === url) _silenceWakeUrl = null;
                    URL.revokeObjectURL(url);
                    player.removeEventListener('ended', release);
                    player.removeEventListener('error', release);
                };
                player.addEventListener('ended', release, { once: true });
                player.addEventListener('error', release, { once: true });
                player.src = url;
                player.play().catch((e) => {
                    console.warn('[silence_wake] play blocked', e);
                    release();
                });
            } catch (e) {
                console.warn('[silence_wake] audio decode failed', e);
            }
        }

        // cleanupServerSession moved to joy_ws.js (window.JoyWs) — see Block 4.
        // The alias `const { cleanupServerSession } = window.JoyWs;` declared at the
        // old applyApiSettings location keeps resetSession's await cleanupServerSession(...)
        // working unchanged.

        async function resetSession({ clearConversation = false, cleanupServer = true } = {}) {
            const oldSessionId = window.sessionId;

            if (!cleanupServer && websocket && websocket.readyState === WebSocket.OPEN) {
                try {
                    websocket.send(JSON.stringify({ type: 'reset_session' }));
                } catch (e) {
                    console.warn('Failed to send reset_session:', e);
                }
            }

            window.sessionId = crypto.randomUUID ? crypto.randomUUID() : 'tab-' + Date.now() + '-' + Math.random().toString(36).slice(2);
            console.log('Session reset:', oldSessionId, '->', window.sessionId);

            // Reconnect WebSocket with new session_id
            if (websocket) {
                websocket.close();
                websocket = null;
            }
            connectWebSocket();

            // Clear prompt input
            promptText.value = '';
            window.JoyState.currentPromptText = '';

            if (clearConversation) {
                clearVlmConversation();
            } else {
                // Reset current streaming state, keep the visible history in the page
                resultText.classList.remove('fade');
                videoOverlay.textContent = '';
                lastText = '';
                lastTtsEventKey = '';
                lastHistoryKey = null;
                lastLoggedInferenceCount = 0;
                renderVlmHistory();
            }

            if (cleanupServer) {
                await cleanupServerSession(oldSessionId);
            }
        }

        // Wire the Reset Session button (was orphaned — resetSession had no call site).
        const resetSessionBtn = document.getElementById('resetSessionBtn');
        if (resetSessionBtn) {
            resetSessionBtn.addEventListener('click', () => {
                resetSession({ clearConversation: true, cleanupServer: true });
            });
        }

if (typeof window !== 'undefined') {
    window.JoyWsDispatcher = {
        connectWebSocket,
        dispatchServerMessage,
        resetSession,
        playSilenceWakeAudio,
        // Split-introduced load crash (same family as P0-1 in audit 2026-08-13):
        // sendDebugFlags is declared in the MAIN inline script, which loads AFTER
        // this pre-main file — a bare shorthand here throws ReferenceError at load
        // and aborts this script before the namespace is attached. `typeof` is safe
        // on not-yet-declared identifiers; the export slot is preserved so the
        // namespace shape is unchanged and the value resolves once the function is
        // defined (recorded as dead export, not deleted — scope guard).
        sendDebugFlags: typeof sendDebugFlags !== 'undefined' ? sendDebugFlags : undefined
    };
}
