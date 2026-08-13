// Extracted from index.html so the monolith shrinks (batch-3 split).
// Pure mechanical move: byte-for-byte function bodies, no logic change.
// Kept as top-level global declarations (classic script) so cross-file
// calls and shared `let`/`const` state keep working exactly as before.
// window.JoyXxx namespace is attached additively (D-033 pattern).

        // Install `llm_reply` WS event handler on a WebSocket. Used both
        // by the WrappedWS IIFE below and directly inside connectWebSocket
        // (where the IIFE has not yet run for the very first WS).
        function installLlmReplyHandler(ws) {
            if (!ws) {
                console.error('[llm-handler] installLlmReplyHandler called with null ws');
                return;
            }
            if (ws.__llmReplyHookInstalled) {
                console.debug('[llm-handler] already installed on this ws (readyState=' + ws.readyState + ')');
                return;
            }
            ws.__llmReplyHookInstalled = true;
            ws.addEventListener('message', function (event) {
                try {
                    if (typeof event.data === 'string') {
                        const data = JSON.parse(event.data);
                        if (data && data.type === 'llm_reply') {
                            // P1 reply_epoch guard: the backend tags every
                            // llm_reply with the generation of the user turn
                            // it answers. A barge-in / newer turn raises
                            // llmReplyGeneration (from asr_partial /
                            // pilot_utterance payloads), so a late broadcast
                            // from an older turn is dropped instead of
                            // rendering/playing over the user's speech.
                            if (typeof data.reply_epoch === 'number' && data.reply_epoch < llmReplyGeneration) {
                                console.debug('[llm-reply] stale reply_epoch=' + data.reply_epoch +
                                    ' < generation=' + llmReplyGeneration + '; dropped');
                            } else {
                                if (typeof data.reply_epoch === 'number' && data.reply_epoch >= llmReplyGeneration) {
                                    llmReplyGeneration = data.reply_epoch;
                                }
                                btLatency.llmReplyAt = performance.now();
                                renderBtLatency();
                                appendJarvisToResult(data.text || '', data.source || 'jarvis');
                                playLlmReplyAudio(data.text || '', { source: data.source || 'jarvis' });
                            }
                        } else if (data && data.type === 'tts_sentence') {
                            // P0-A streaming: one sentence's WAV audio. Enqueue
                            // and play strictly in seq order.
                            enqueueLlmReplySentence(data);
                        } else if (data && data.type === 'pilot_utterance') {
                            // ASR final: drop the draft bubble and commit
                            // the canonical pilot message. The user has spoken,
                            // so also stop any reply audio that is still
                            // playing (barge-in backstop; idempotent).
                            stopLlmReplyAudio();
                            // P1: a committed user turn raises the accepted
                            // generation (the backend bumps its per-turn
                            // epoch at the same commit point), so an older
                            // turn's late llm_reply is recognized as stale.
                            if (typeof data.reply_epoch === 'number' && data.reply_epoch > llmReplyGeneration) {
                                llmReplyGeneration = data.reply_epoch;
                            }
                            clearAsrDraft();
                            appendPilotToResult(data.text || '');
                        } else if (data && data.type === 'asr_partial') {
                            // Barge-in (P0): the user has started speaking.
                            // Stop any reply audio playing right now — the
                            // client-first principle — before rendering the
                            // ASR draft. Repeated partials are no-ops.
                            stopLlmReplyAudio();
                            // P1: adopt the backend's live reply epoch so an
                            // interrupted turn's late llm_reply is recognized
                            // as stale (the backend bumps its epoch on the
                            // barge-in; the following partials carry it).
                            if (typeof data.reply_epoch === 'number' && data.reply_epoch > llmReplyGeneration) {
                                llmReplyGeneration = data.reply_epoch;
                            }
                            // ASR streaming hypothesis: render as a draft
                            // bubble so the operator can see what ASR is
                            // currently thinking (replaces guesswork).
                            if (data.is_final) {
                                clearAsrDraft();
                            } else if (data.text) {
                                renderAsrDraft(data.text);
                            }
                        }
                    }
                } catch (e) { /* ignore */ }
            });
            console.log('[llm-handler] installed message listener on ws (readyState=' + ws.readyState + ', url=' + ws.url + ')');
        }
        function appendJarvisToResult(text, source) {
            appendJarvisDialogEntry('assistant', text, source || 'jarvis');
        }
        function appendPilotToResult(text) {
            appendJarvisDialogEntry('pilot', text, 'Pilot');
        }
        function appendJarvisDialogEntry(role, text, source) {
            const cleanText = String(text || '').trim();
            if (!cleanText) return;
            const entry = {
                key: `jarvis:${role}:${Date.now()}:${Math.random().toString(36).slice(2)}`,
                kind: 'jarvis_dialog',
                role,
                source: source || (role === 'pilot' ? 'Pilot' : 'jarvis'),
                response: cleanText,
                rawText: cleanText,
                contextExcluded: true,
            };
            vlmHistory.push(entry);
            appendVlmHistoryEntry(entry, { animateLast: true });
        }

        // Live ASR draft bubble: shows the streaming hypothesis without
        // committing to history. Cleared when pilot_utterance (final) fires.
        let asrDraftElement = null;
        function renderAsrDraft(text) {
            const contentDiv = document.getElementById('resultTextContent');
            if (!contentDiv) return;
            if (!asrDraftElement || !asrDraftElement.isConnected) {
                asrDraftElement = document.createElement('div');
                asrDraftElement.className = 'result-text jarvis-pilot-message asr-draft';
                const role = document.createElement('span');
                role.className = 'jarvis-message-role';
                role.textContent = window.JoyI18n.localizeUiString('Pilot (listening)');
                const body = document.createElement('div');
                body.className = 'jarvis-message-body';
                asrDraftElement.appendChild(role);
                asrDraftElement.appendChild(body);
                contentDiv.appendChild(asrDraftElement);
            }
            const body = asrDraftElement.querySelector('.jarvis-message-body');
            if (body) {
                renderTextIntoElement(body, text);
            }
            if (typeof scrollVlmHistoryToBottom === 'function') {
                scrollVlmHistoryToBottom();
            }
        }
        function clearAsrDraft() {
            if (asrDraftElement && asrDraftElement.parentNode) {
                asrDraftElement.parentNode.removeChild(asrDraftElement);
            }
            asrDraftElement = null;
        }

if (typeof window !== 'undefined') {
    window.JoyLlmReplyUi = {
        installLlmReplyHandler,
        appendJarvisToResult,
        appendPilotToResult,
        appendJarvisDialogEntry,
        renderAsrDraft,
        clearAsrDraft
    };
}
