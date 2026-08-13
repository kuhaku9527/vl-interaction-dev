// Extracted from index.html so the monolith shrinks (batch-3 split).
// Pure mechanical move: byte-for-byte function bodies, no logic change.
// Kept as top-level global declarations (classic script) so cross-file
// calls and shared `let`/`const` state keep working exactly as before.
// window.JoyXxx namespace is attached additively (D-033 pattern).

        function isVlmHistoryNearBottom(element) {
            if (!element) return false;
            if (element.scrollHeight <= element.clientHeight) return true;
            return element.scrollHeight - element.scrollTop - element.clientHeight <= 48;
        }

        function scrollVlmHistoryToBottom(element) {
            if (!element) return;

            requestAnimationFrame(() => {
                element.scrollTop = element.scrollHeight;
                requestAnimationFrame(() => {
                    element.scrollTop = element.scrollHeight;
                });
            });
        }
        function clearVlmConversation() {
            closeTtsWebSocket();
            vlmHistory = [];
            backgroundTaskEntries.clear();
            backgroundSummaryEntries.clear();
            lastReadyBackgroundTaskId = '';
            lastText = '';
            lastTtsEventKey = '';
            lastHistoryKey = null;
            currentPromptText = '';
            pendingPromptEntry = null;

            resultText.classList.remove('fade');
            renderVlmHistory();

            videoOverlay.textContent = '';
            videoOverlay.classList.remove('show', 'top', 'bottom');

            currentPrompt.style.display = 'none';
            currentPrompt.textContent = '';
            metricsInline.style.display = 'flex';
            latencyValue.textContent = '0';
            avgLatencyValue.textContent = '0';
            countValue.textContent = '0';

            const requestPayloadDebug = document.getElementById('requestPayloadDebug');
            const requestPayloadContent = document.getElementById('requestPayloadContent');
            const responsePayloadDebug = document.getElementById('responsePayloadDebug');
            const responsePayloadContent = document.getElementById('responsePayloadContent');
            if (requestPayloadDebug) requestPayloadDebug.style.display = 'none';
            if (requestPayloadContent) requestPayloadContent.textContent = '';
            if (responsePayloadDebug) responsePayloadDebug.style.display = 'none';
            if (responsePayloadContent) responsePayloadContent.textContent = '';
            const memoryStateDebug = document.getElementById('memoryStateDebug');
            if (memoryStateDebug) memoryStateDebug.style.display = 'none';
            const midTermMemoryContent = document.getElementById('midTermMemoryContent');
            if (midTermMemoryContent) midTermMemoryContent.textContent = '';
            const longTermMemoryContent = document.getElementById('longTermMemoryContent');
            if (longTermMemoryContent) longTermMemoryContent.textContent = '';

            syncVlmToFullscreen();
        }

        function hasVisiblePrompt(entry) {
            return Boolean(entry?.prompt && entry.prompt.trim() && entry.prompt.trim() !== '--');
        }

        function hasJarvisDialogHistory() {
            return vlmHistory.some(entry => entry?.kind === 'jarvis_dialog');
        }

        function shouldShowVlmHistoryShell() {
            // BT-7274 链路下 chat 容器始终展开，方便 Pilot 看到对话；
            // 空状态下由 .vlm-history-empty-state 提供占位文案。
            return true;
        }

        // empty-state 是 resultText 子级，不会被 innerHTML 清掉
        function syncVlmHistoryEmpty() {
            const empty = document.getElementById("vlmHistoryEmpty");
            if (!empty) return;
            const hasReal = Array.isArray(vlmHistory) && vlmHistory.length > 0;
            empty.classList.toggle("is-hidden", hasReal);
        }

        function createJarvisDialogNode(entry, animateResponse = false) {
            const responseDiv = document.createElement('div');
            const isPilot = entry.role === 'pilot';
            responseDiv.className = `result-text ${isPilot ? 'jarvis-pilot-message' : 'jarvis-reply-message'}`;
            if (animateResponse && settings.popIn) {
                responseDiv.classList.add('new-message');
                if (settings.glow) {
                    responseDiv.classList.add('with-glow');
                }
            }

            const role = document.createElement('span');
            role.className = 'jarvis-message-role';
            role.textContent = isPilot ? 'Pilot' : 'BT-7274';

            const body = document.createElement('div');
            body.className = 'jarvis-message-body';
            renderTextIntoElement(body, entry.response || '');

            responseDiv.appendChild(role);
            responseDiv.appendChild(body);
            requestAnimationFrame(() => lucide.createIcons());
            return responseDiv;
        }

        function createHistoryNodes(entry, animateResponse = false) {
            let promptDiv = null;
            if (hasVisiblePrompt(entry)) {
                promptDiv = document.createElement('div');
                promptDiv.className = 'result-prompt';
                promptDiv.textContent = cleanBackgroundQuestionText(entry.prompt.trim());
            }

            let responseDiv = null;
            if (entry.kind === 'jarvis_dialog') {
                responseDiv = createJarvisDialogNode(entry, animateResponse);
            } else if (entry.kind === 'background') {
                responseDiv = createBackgroundHistoryNode(entry, animateResponse);
            } else if (entry.response) {
                responseDiv = document.createElement('div');
                responseDiv.className = 'result-text';
                if (animateResponse && settings.popIn) {
                    responseDiv.classList.add('new-message');
                    if (settings.glow) {
                        responseDiv.classList.add('with-glow');
                    }
                }
                renderStandardResponseIntoElement(responseDiv, entry);
            }

            entry.promptElement = promptDiv;
            entry.responseElement = responseDiv;

            return { promptDiv, responseDiv };
        }

        function renderStandardResponseIntoElement(responseDiv, entry) {
            renderStandardRichContent(responseDiv, entry);
            appendBackgroundJumpButton(responseDiv, entry);
            requestAnimationFrame(() => lucide.createIcons());
        }

        function isBackgroundSummaryEntry(entry) {
            return entry?.kind === 'background_summary' ||
                Boolean(entry?.backgroundSummary) ||
                Boolean(resolveBackgroundJumpTaskId(entry));
        }

        function isForegroundHistoryEntry(entry) {
            return Boolean(entry) && entry.kind !== 'background' && !isBackgroundSummaryEntry(entry);
        }

        function formatInlineJsonAsMarkdown(text) {
            let source = String(text || '');
            const inlineBlocks = extractInlineJsonObjectCandidates(source)
                .sort((a, b) => b.start - a.start);

            inlineBlocks.forEach((candidate) => {
                let start = candidate.start;
                const prefix = source.slice(Math.max(0, start - 12), start);
                const jsonLabel = prefix.match(/(?:^|\s)json\s*$/i);
                if (jsonLabel) {
                    start -= jsonLabel[0].length;
                }

                let prettyJson = candidate.text;
                try {
                    prettyJson = JSON.stringify(JSON.parse(candidate.text), null, 2);
                } catch (err) {
                    // Keep the original JSON string if prettifying fails.
                }

                const block = `\n\n\`\`\`json\n${prettyJson}\n\`\`\`\n`;
                source = `${source.slice(0, start).trimEnd()}${block}${source.slice(candidate.end).trimStart()}`;
            });

            return source.trim();
        }

        function renderBackgroundSummaryMarkdown(responseDiv, text) {
            responseDiv.innerHTML = '';
            responseDiv.classList.remove('standard-rich-response');
            responseDiv.classList.add('background-summary-message');

            const label = document.createElement('div');
            label.className = 'background-summary-label';
            label.innerHTML = '<i data-lucide="sparkles"></i><span>摘要</span>';

            const body = document.createElement('div');
            body.className = 'background-summary-body markdown-rendered';
            renderTextIntoElement(body, String(text || '').trim());

            responseDiv.appendChild(label);
            responseDiv.appendChild(body);
        }

        function renderStandardRichContent(responseDiv, entry) {
            const text = entry?.response || '';
            if (isBackgroundSummaryEntry(entry)) {
                renderBackgroundSummaryMarkdown(responseDiv, text);
                return;
            }

            const chart = parseBackgroundChart(text);
            const htmlDetails = extractBackgroundHtmlDetails(text);
            const html = htmlDetails.html;

            if (!chart && !html) {
                responseDiv.classList.remove('standard-rich-response');
                if (!isBackgroundSummaryEntry(entry)) {
                    responseDiv.classList.remove('background-summary-message');
                }
                renderTextIntoElement(responseDiv, text);
                return;
            }

            responseDiv.innerHTML = '';
            responseDiv.classList.add('standard-rich-response');
            responseDiv.classList.remove('background-summary-message');
            const explanation = extractBackgroundExplanation(text);
            if (explanation) {
                const explanationElement = document.createElement('div');
                explanationElement.className = 'standard-rich-explanation markdown-rendered';
                renderTextIntoElement(explanationElement, explanation);
                responseDiv.appendChild(explanationElement);
            }

            if (chart) {
                const chartElement = document.createElement('div');
                chartElement.className = 'standard-rich-chart';
                renderBackgroundChartView(chartElement, chart);
                responseDiv.appendChild(chartElement);
            }

            if (html) {
                const htmlElement = document.createElement('div');
                htmlElement.className = 'standard-rich-html';
                renderBackgroundHtmlView(htmlElement, html, {
                    incomplete: htmlDetails.incomplete
                });
                responseDiv.appendChild(htmlElement);
            }
        }

        function getLatestReadyBackgroundEntry() {
            if (lastReadyBackgroundTaskId && backgroundTaskEntries.has(lastReadyBackgroundTaskId)) {
                const entry = backgroundTaskEntries.get(lastReadyBackgroundTaskId);
                if (entry?.ready && entry.status === 'ready') {
                    return entry;
                }
            }

            for (let index = vlmHistory.length - 1; index >= 0; index -= 1) {
                const entry = vlmHistory[index];
                if (entry?.kind === 'background' && entry.ready && entry.status === 'ready') {
                    return entry;
                }
            }
            return null;
        }

        function cleanBackgroundQuestionText(question) {
            return String(question || '')
                .replace(/^\s*请(?:回答|解答)这道(?:简答题|数学题|题目|问题)[，,、\s]*(?:并(?:提供|给出)必要的推理过程和最终答案)?[：:]\s*/u, '')
                .replace(/\[User Query[^\]]*\]\s*/gi, '')
                .replace(/^Prompt:\s*/i, '')
                .replace(/[ \t]{2,}/g, ' ')
                .trim();
        }

        function isBackgroundWaitingNotice(text) {
            const value = String(text || '').trim();
            return /(?:需要|正在)调用后台模型|后台模型(?:正在)?处理|请稍等/.test(value)
                && /继续(?:向我)?提问|期间/.test(value);
        }

        function resolveBackgroundJumpTaskId(entry) {
            const directTaskId = entry?.backgroundHandoff?.task_id;
            if (directTaskId && backgroundTaskEntries.has(String(directTaskId))) {
                return String(directTaskId);
            }

            return '';
        }

        function appendBackgroundJumpButton(responseDiv, entry) {
            if (!isBackgroundSummaryEntry(entry)) {
                return;
            }
            const taskId = resolveBackgroundJumpTaskId(entry);
            if (!taskId) {
                return;
            }

            const backgroundEntry = backgroundTaskEntries.get(String(taskId));
            const questionText = cleanBackgroundQuestionText(
                entry?.backgroundHandoff?.question ||
                backgroundEntry?.question ||
                ''
            );

            const row = document.createElement('div');
            row.className = 'background-jump-row';
            row.dataset.backgroundTaskId = taskId;
            if (questionText) {
                const question = document.createElement('div');
                question.className = 'background-jump-question';
                const label = document.createElement('span');
                label.className = 'background-jump-question-label';
                label.textContent = '对应问题';
                const value = document.createElement('span');
                value.className = 'background-jump-question-text';
                value.textContent = questionText;
                question.appendChild(label);
                question.appendChild(value);
                row.appendChild(question);
            }
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'background-jump-button';
            button.dataset.backgroundTaskId = taskId;
            button.innerHTML = '<i data-lucide="corner-down-left"></i><span>跳转</span>';
            button.title = '跳转到对应的后台结果';
            button.addEventListener('click', (event) => {
                event.preventDefault();
                event.stopPropagation();
                jumpToBackgroundResult(button.dataset.backgroundTaskId || taskId);
            });
            row.appendChild(button);
            responseDiv.appendChild(row);
        }

        function findBackgroundResultElement(taskId) {
            const expectedTaskId = String(taskId || '');
            if (!expectedTaskId) return null;

            const domTarget = Array.from(document.querySelectorAll('[data-background-result-task-id]'))
                .find(element => element.dataset.backgroundResultTaskId === expectedTaskId);
            if (domTarget) {
                return domTarget;
            }

            const entry = backgroundTaskEntries.get(expectedTaskId);
            return entry?.responseElement || null;
        }

        function getScrollableAncestors(element) {
            const ancestors = [];
            let node = element?.parentElement || null;
            while (node && node !== document.body) {
                const style = window.getComputedStyle(node);
                const canScroll = /(auto|scroll|overlay)/.test(style.overflowY)
                    && node.scrollHeight > node.clientHeight + 1;
                if (canScroll) {
                    ancestors.push(node);
                }
                node = node.parentElement;
            }

            const root = document.scrollingElement || document.documentElement;
            if (root) {
                ancestors.push(root);
            }
            return ancestors;
        }

        function scrollElementIntoContainer(target, container, behavior = 'smooth') {
            if (!target || !container) return;
            const containerRect = container.getBoundingClientRect();
            const targetRect = target.getBoundingClientRect();
            const targetTop = container.scrollTop
                + targetRect.top
                - containerRect.top
                - 16;
            container.scrollTo({
                top: Math.max(0, targetTop),
                behavior
            });
        }

        function flashBackgroundJumpTarget(target) {
            if (!target) return;
            target.classList.add('jump-highlight');
            setTimeout(() => target.classList.remove('jump-highlight'), 1800);
        }

        function findBackgroundOriginEntry(entry) {
            const question = cleanBackgroundQuestionText(entry?.question || '');
            if (!question) return null;
            for (let index = vlmHistory.length - 1; index >= 0; index -= 1) {
                const candidate = vlmHistory[index];
                if (!isForegroundHistoryEntry(candidate)) continue;
                if (cleanBackgroundQuestionText(candidate.prompt || '') === question) {
                    return candidate;
                }
            }
            return null;
        }

        function jumpToBackgroundOrigin(entry) {
            const originEntry = findBackgroundOriginEntry(entry);
            const target = originEntry?.promptElement || null;
            if (!target) return;

            const historyContainer = document.getElementById('resultTextContent');
            if (historyContainer && historyContainer.contains(target)) {
                scrollElementIntoContainer(target, historyContainer, 'smooth');
            }
            getScrollableAncestors(target).forEach(container => {
                scrollElementIntoContainer(target, container, 'smooth');
            });
            target.scrollIntoView({ behavior: 'smooth', block: 'start', inline: 'nearest' });
            flashBackgroundJumpTarget(target);
        }

        function jumpToBackgroundResult(taskId) {
            const normalizedTaskId = String(taskId || '');
            const entry = backgroundTaskEntries.get(normalizedTaskId);
            if (!entry) return;
            entry.expanded = true;
            renderVlmHistory();

            const performJump = (attempt = 0) => {
                const target = findBackgroundResultElement(normalizedTaskId);
                if (!target) {
                    if (attempt < 3) {
                        setTimeout(() => performJump(attempt + 1), 80);
                    }
                    return;
                }

                const behavior = attempt === 0 ? 'smooth' : 'auto';
                const historyContainer = document.getElementById('resultTextContent');
                if (historyContainer && historyContainer.contains(target)) {
                    scrollElementIntoContainer(target, historyContainer, behavior);
                }
                getScrollableAncestors(target).forEach(container => {
                    scrollElementIntoContainer(target, container, behavior);
                });
                target.scrollIntoView({ behavior, block: 'start', inline: 'nearest' });
                flashBackgroundJumpTarget(target);

                if (attempt < 2) {
                    setTimeout(() => performJump(attempt + 1), attempt === 0 ? 180 : 360);
                }
            };

            requestAnimationFrame(() => {
                requestAnimationFrame(() => performJump(0));
            });
        }

        function createBackgroundHistoryNode(entry, animateResponse = false) {
            const responseDiv = document.createElement('div');
            responseDiv.className = 'result-text background-result';
            if (entry.taskId) {
                responseDiv.dataset.backgroundResultTaskId = String(entry.taskId);
            }
            if (animateResponse && settings.popIn) {
                responseDiv.classList.add('new-message');
            }

            const shell = document.createElement('div');
            shell.className = 'background-result-shell';

            const header = document.createElement('div');
            header.className = 'background-result-header';

            const icon = document.createElement('div');
            icon.className = 'background-result-icon';
            icon.innerHTML = '<i data-lucide="sparkles"></i>';

            const title = document.createElement('div');
            title.className = 'background-result-title';
            title.textContent = entry.status === 'error'
                ? '后台模型处理失败'
                : entry.ready
                    ? '后台模型返回结果了，现在查看吗？'
                    : '后台模型正在处理...';

            if (entry.question) {
                const question = document.createElement('div');
                question.className = 'background-result-question';
                question.textContent = entry.question;
                title.appendChild(question);
            }

            const action = document.createElement('button');
            action.type = 'button';
            action.className = 'background-result-action';
            action.textContent = entry.expanded ? '收起' : entry.ready ? '查看' : '等待中';
            action.disabled = !entry.ready && entry.status !== 'error';
            action.addEventListener('click', () => {
                entry.expanded = !entry.expanded;
                renderVlmHistory();
            });

            const originEntry = findBackgroundOriginEntry(entry);
            let originAction = null;
            if (originEntry) {
                originAction = document.createElement('button');
                originAction.type = 'button';
                originAction.className = 'background-result-action background-origin-action';
                originAction.innerHTML = '<i data-lucide="corner-up-left"></i>';
                originAction.title = '跳转到原问题';
                originAction.addEventListener('click', (event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    jumpToBackgroundOrigin(entry);
                });
            }

            header.appendChild(icon);
            header.appendChild(title);
            if (originAction) {
                header.appendChild(originAction);
            }
            header.appendChild(action);
            shell.appendChild(header);

            if (entry.expanded) {
                const content = document.createElement('div');
                content.className = 'result-text-content markdown-rendered';
                if (entry.status === 'error') {
                    content.textContent = entry.error || 'Background model failed.';
                } else {
                    renderBackgroundRichContent(content, entry.response || '', entry.rich || null);
                }
                shell.appendChild(content);
            }

            responseDiv.appendChild(shell);
            entry.responseElement = responseDiv;
            requestAnimationFrame(() => lucide.createIcons());
            return responseDiv;
        }

        function appendVlmHistoryEntry(entry, { animateLast = false } = {}) {
            const contentDiv = document.getElementById('resultTextContent');
            if (!contentDiv) return;

            const shouldAutoScroll = isVlmHistoryNearBottom(contentDiv);
            const { promptDiv, responseDiv } = createHistoryNodes(entry, animateLast);

            if (promptDiv) {
                contentDiv.appendChild(promptDiv);
            }
            if (responseDiv) {
                contentDiv.appendChild(responseDiv);
            }
            resultText.style.display = (settings.overlayPosition === 'none' || entry.kind === 'jarvis_dialog') ? 'flex' : 'none';

            if (shouldAutoScroll) {
                scrollVlmHistoryToBottom(contentDiv);
            }
            syncVlmHistoryEmpty();

            const videoCard = document.getElementById('videoCard');
            if (videoCard && videoCard.classList.contains('fullscreen')) {
                syncVlmToFullscreen();
            }
        }

        function renderVlmHistory({ animateLast = false } = {}) {
            // Latency instrumentation (issue #43): tRenderStart marks the moment
            // just before the VLM assistant response(s) are committed to the chat
            // DOM. The duration below measures DOM insertion cost — render segment.
            const tRenderStart = performance.now();
            const contentDiv = document.getElementById('resultTextContent');
            if (!contentDiv) {
                console.info('[latency][render]', { skipped: true, reason: 'resultTextContent missing' });
                return;
            }

            const shouldAutoScroll = isVlmHistoryNearBottom(contentDiv);

            contentDiv.innerHTML = '';
            const fragment = document.createDocumentFragment();
            vlmHistory.forEach((entry, index) => {
                const { promptDiv, responseDiv } = createHistoryNodes(
                    entry,
                    animateLast && index === vlmHistory.length - 1
                );
                if (promptDiv) {
                    fragment.appendChild(promptDiv);
                }
                if (responseDiv) {
                    fragment.appendChild(responseDiv);
                }
            });
            contentDiv.appendChild(fragment);
            console.info('[latency][render]', {
                ms: performance.now() - tRenderStart,
                entries: vlmHistory.length,
            });

            resultText.style.display = shouldShowVlmHistoryShell() ? "flex" : "none";
            syncVlmHistoryEmpty();
            if (shouldAutoScroll) {
                scrollVlmHistoryToBottom(contentDiv);
            }

            // Auto-sync to fullscreen overlay if in fullscreen mode
            const videoCard = document.getElementById('videoCard');
            if (videoCard && videoCard.classList.contains('fullscreen')) {
                syncVlmToFullscreen();
            }
        }

        function appendPromptHistoryEntry(prompt) {
            const latest = vlmHistory[vlmHistory.length - 1];
            const cleanPrompt = cleanBackgroundQuestionText(prompt);

            if (latest && latest.pending && latest.prompt === cleanPrompt) {
                pendingPromptEntry = latest;
                return;
            }

            const entry = {
                key: `prompt:${Date.now()}:${Math.random().toString(36).slice(2)}`,
                prompt: cleanPrompt,
                response: '',
                rawText: '',
                pending: true
            };
            vlmHistory.push(entry);
            pendingPromptEntry = entry;
            appendVlmHistoryEntry(entry);
        }

        function resolveBackgroundSummaryText(data) {
            return String(data?.summary_text || '后台模型已返回结果。').trim();
        }

        function resolveBackgroundSummaryDetailText(data) {
            const direct = String(data?.background_summary || '').trim();
            if (direct) return direct;
            const text = String(data?.text || '').trim();
            if (text) return text;
            const handoff = data?.interaction_handoff;
            if (handoff && typeof handoff === 'object') {
                return String(handoff.summary || '').trim();
            }
            return '';
        }

        function updateBackgroundSummaryEntry(data, summaryText = null, rawText = null) {
            const taskId = String(data?.task_id || '').trim();
            const summary = String(summaryText ?? resolveBackgroundSummaryText(data)).trim();
            const detailText = String(rawText ?? resolveBackgroundSummaryDetailText(data)).trim();
            if (!taskId || !summary) {
                return null;
            }

            let entry = backgroundSummaryEntries.get(taskId);
            if (!entry) {
                entry = {
                    key: `background-summary:${taskId}`,
                    kind: 'background_summary',
                    contextExcluded: true,
                    prompt: '',
                    response: summary,
                    rawText: detailText || summary,
                    backgroundSummary: true,
                    backgroundHandoff: {
                        task_id: taskId,
                        question: cleanBackgroundQuestionText(data.question || '')
                    }
                };
                backgroundSummaryEntries.set(taskId, entry);
            } else {
                entry.kind = 'background_summary';
                entry.contextExcluded = true;
                entry.backgroundSummary = true;
                entry.response = summary;
                entry.rawText = detailText || summary;
                entry.backgroundHandoff = {
                    task_id: taskId,
                    question: cleanBackgroundQuestionText(data.question || entry.backgroundHandoff?.question || '')
                };
            }
            return entry;
        }

        function appendOrUpdateBackgroundSummaryEntry(data) {
            updateBackgroundSummaryEntry(data);
        }

        function appendOrUpdateBackgroundSummaryHistoryEntry(data, summaryText = null, rawText = null) {
            const entry = updateBackgroundSummaryEntry(data, summaryText, rawText);
            if (!entry) {
                return null;
            }

            const alreadyInHistory = vlmHistory.includes(entry);
            if (!alreadyInHistory) {
                vlmHistory.push(entry);
                appendVlmHistoryEntry(entry, { animateLast: true });
            } else if (entry.responseElement) {
                renderStandardResponseIntoElement(entry.responseElement, entry);
            } else {
                renderVlmHistory({ animateLast: true });
            }
            return entry;
        }

        function appendOrUpdateBackgroundEntry(data) {
            const taskId = String(data.task_id || `background:${Date.now()}`);
            let entry = backgroundTaskEntries.get(taskId);
            const isReady = data.type === 'background_result_ready';
            const isError = data.type === 'background_result_error';

            if (!entry) {
                entry = {
                    key: `background:${taskId}`,
                    kind: 'background',
                    contextExcluded: true,
                    taskId,
                    question: data.question || '',
                    response: '',
                    rawText: '',
                    error: '',
                    ready: false,
                    expanded: false,
                    status: 'running'
                };
                backgroundTaskEntries.set(taskId, entry);
                vlmHistory.push(entry);
            }

            entry.question = data.question || entry.question || '';
            entry.foregroundText = data.foreground_text || entry.foregroundText || '';
            entry.metrics = data.metrics || entry.metrics || null;
            entry.model = data.model || entry.model || '';
            entry.contextExcluded = true;

            if (isReady) {
                entry.response = data.text || '';
                entry.rawText = data.text || '';
                entry.rich = data.rich || null;
                entry.ready = true;
                entry.status = 'ready';
                lastReadyBackgroundTaskId = taskId;
                appendOrUpdateBackgroundSummaryHistoryEntry(data);
            } else if (isError) {
                entry.error = data.error || 'Background model failed.';
                entry.ready = true;
                entry.status = 'error';
            }

            renderVlmHistory({ animateLast: true });
        }

        function applyBackgroundConfig(config) {
            if (!config) return;
            backgroundConfig = config;
            if (backgroundEnabledToggle && typeof config.enabled === 'boolean') {
                backgroundEnabledToggle.checked = config.enabled;
            }
            if (backgroundFrameMultiplier && config.frame_multiplier != null) {
                backgroundFrameMultiplier.value = String(config.frame_multiplier);
            }
            if (backgroundMaxFrames) {
                backgroundMaxFrames.value = String(config.max_frames || 100);
            }
        }

        function sendBackgroundConfig() {
            if (!websocket || websocket.readyState !== WebSocket.OPEN) return;
            websocket.send(JSON.stringify({
                type: 'update_background_config',
                enabled: !!backgroundEnabledToggle?.checked,
                frame_multiplier: parseInt(backgroundFrameMultiplier?.value, 10) || 2,
                max_frames: parseInt(backgroundMaxFrames?.value, 10) || 100
            }));
        }

        // Function to update result text (handles both markdown and plain text)
        function updateResultText(text, metrics = null, backgroundHandoff = null) {
            const displayText = getVlmDisplayText(text);
            if (displayText.trim() === '') {
                resultText.style.display = shouldShowVlmHistoryShell() ? 'flex' : 'none';
                return;
            }

            const resolvedBackgroundHandoff = isBackgroundWaitingNotice(displayText)
                ? null
                : (backgroundHandoff || null);
            const prompt = currentPromptText || '';
            const hasInferenceKey = metrics?.total_inferences != null;
            const historyKey = hasInferenceKey
                ? `count:${metrics.total_inferences}`
                : `text:${text}`;
            const ttsEventKey = hasInferenceKey
                ? historyKey
                : `event:${++ttsEventSequence}`;
            speakVlmText(displayText, ttsEventKey);
            const pendingEntry = pendingPromptEntry && vlmHistory.includes(pendingPromptEntry)
                && isForegroundHistoryEntry(pendingPromptEntry)
                ? pendingPromptEntry
                : null;

            if (resolvedBackgroundHandoff?.task_id) {
                appendOrUpdateBackgroundSummaryHistoryEntry(
                    {
                        task_id: resolvedBackgroundHandoff.task_id,
                        question: resolvedBackgroundHandoff.question || ''
                    },
                    displayText,
                    text
                );
                syncVlmToFullscreen();
                return;
            }

            const latest = [...vlmHistory].reverse().find(isForegroundHistoryEntry);

            if (latest && latest.key === historyKey) {
                const handoffChanged = JSON.stringify(latest.backgroundHandoff || null)
                    !== JSON.stringify(resolvedBackgroundHandoff || latest.backgroundHandoff || null);
                if (latest.rawText === text && latest.response === displayText && !handoffChanged) {
                    return;
                }
                const contentDiv = document.getElementById('resultTextContent');
                const shouldAutoScroll = isVlmHistoryNearBottom(contentDiv);
                latest.rawText = text;
                latest.response = displayText;
                latest.backgroundHandoff = resolvedBackgroundHandoff || latest.backgroundHandoff || null;
                if (latest.responseElement) {
                    renderStandardResponseIntoElement(latest.responseElement, latest);
                } else {
                    renderVlmHistory();
                }
                if (shouldAutoScroll) {
                    scrollVlmHistoryToBottom(contentDiv);
                }
            } else if (pendingEntry) {
                const contentDiv = document.getElementById('resultTextContent');
                const shouldAutoScroll = isVlmHistoryNearBottom(contentDiv);
                pendingEntry.key = historyKey;
                pendingEntry.response = displayText;
                pendingEntry.rawText = text;
                pendingEntry.backgroundHandoff = resolvedBackgroundHandoff || pendingEntry.backgroundHandoff || null;
                pendingEntry.pending = false;
                lastHistoryKey = historyKey;
                currentPromptText = '';
                pendingPromptEntry = null;

                if (pendingEntry.responseElement) {
                    renderStandardResponseIntoElement(pendingEntry.responseElement, pendingEntry);
                } else if (pendingEntry.promptElement) {
                    const responseDiv = document.createElement('div');
                    responseDiv.className = 'result-text';
                    if (settings.popIn) {
                        responseDiv.classList.add('new-message');
                        if (settings.glow) {
                            responseDiv.classList.add('with-glow');
                        }
                    }
                    renderStandardResponseIntoElement(responseDiv, pendingEntry);
                    pendingEntry.responseElement = responseDiv;
                    pendingEntry.promptElement.insertAdjacentElement('afterend', responseDiv);
                } else {
                    renderVlmHistory({ animateLast: true });
                }

                if (shouldAutoScroll) {
                    scrollVlmHistoryToBottom(contentDiv);
                }
            } else if (historyKey !== lastHistoryKey && (hasInferenceKey || text !== lastText)) {
                const entry = {
                    key: historyKey,
                    prompt,
                    response: displayText,
                    rawText: text,
                    backgroundHandoff: resolvedBackgroundHandoff || null
                };
                vlmHistory.push(entry);
                lastHistoryKey = historyKey;
                currentPromptText = '';
                appendVlmHistoryEntry(entry, { animateLast: true });
            }

            syncVlmToFullscreen();
        }

        function logLatencyBreakdown(metrics) {
            if (!metrics || !metrics.latency_breakdown_ms) return;
            const inferenceCount = Number(metrics.total_inferences) || 0;
            if (inferenceCount <= lastLoggedInferenceCount) return;
            lastLoggedInferenceCount = inferenceCount;

            const toMs = (value) => Number.isFinite(Number(value)) ? Number(value).toFixed(2) : '--';
            const frameTiming = metrics.frame_timing_ms || {};
            const vlmTiming = metrics.latency_breakdown_ms || {};
            console.groupCollapsed(
                `[VLM latency] #${inferenceCount} total ${toMs(metrics.last_latency_ms)} ms`
            );
            console.table([
                { phase: 'Frame to ndarray', scope: 'Before Latency', ms: toMs(frameTiming.frame_to_ndarray_ms) },
                { phase: 'Frame copy', scope: 'Before Latency', ms: toMs(frameTiming.frame_copy_ms) },
                { phase: 'BGR to RGB + PIL', scope: 'Before Latency', ms: toMs(frameTiming.bgr_to_rgb_pil_ms) },
                { phase: 'Pre-VLM subtotal', scope: 'Before Latency', ms: toMs(frameTiming.pre_vlm_total_ms) },
                { phase: 'JPEG encode', scope: 'Latency', ms: toMs(vlmTiming.jpeg_encode_ms) },
                { phase: 'Base64 encode', scope: 'Latency', ms: toMs(vlmTiming.base64_encode_ms) },
                { phase: 'Request build/debug payload', scope: 'Latency', ms: toMs(vlmTiming.request_build_ms) },
                { phase: 'API call + VLM inference', scope: 'Latency', ms: toMs(vlmTiming.api_call_ms) },
                { phase: 'Response payload record', scope: 'Latency', ms: toMs(vlmTiming.response_payload_ms) },
                { phase: 'Response extract', scope: 'Latency', ms: toMs(vlmTiming.response_extract_ms) },
                { phase: 'Latency total', scope: 'Latency', ms: toMs(vlmTiming.total_ms) },
            ]);
            console.groupEnd();
        }

if (typeof window !== 'undefined') {
    window.JoyVlmHistory = {
        renderVlmHistory,
        appendVlmHistoryEntry,
        appendOrUpdateBackgroundEntry,
        updateResultText,
        clearVlmConversation,
        syncVlmHistoryEmpty,
        hasJarvisDialogHistory,
        updateBackgroundSummaryEntry,
        sendBackgroundConfig,
        applyBackgroundConfig,
        logLatencyBreakdown
    };
}
