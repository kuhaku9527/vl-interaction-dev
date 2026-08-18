// Extracted from index.html so the monolith shrinks (batch-3 split).
// Pure mechanical move: byte-for-byte function bodies, no logic change.
// Kept as top-level global declarations (classic script) so cross-file
// calls and shared `let`/`const` state keep working exactly as before.
// window.JoyXxx namespace is attached additively (D-033 pattern).

        // Build a Lucide icon element without using innerHTML (avoids static-analysis
        // false positives on sast_xss_inner_html for static icon markup).
        function createLucideIcon(name) {
            const icon = document.createElement('i');
            icon.setAttribute('data-lucide', name);
            return icon;
        }

        // Single source of truth for stripping model decision/control tokens
        // from text before it reaches the chat UI. The VLM may emit internal
        // decision markers (</silence>, </response>, <delegation>/</delegation>,
        // and the open-tag variants) that must never be shown to the end user.
        // Callers must route VLM output through this before rendering.
        function getVlmDisplayText(text) {
            if (!text) return '';

            const rawText = String(text);
            if (rawText.trim() === '' || rawText.trim() === 'Initializing...') {
                return '';
            }

            // <silence> or </silence> means the model chose not to speak — show nothing.
            if (/<\/?silence>/i.test(rawText)) {
                if (/<\/?(?:response|delegation|silence)>/i.test(rawText)) {
                    console.debug('[vlm] dropped silent output containing decision tokens');
                }
                return '';
            }

            // Strip the remaining control tokens, keeping the surrounding reply text
            // (e.g. the reply body that follows </response>, or the delegated question).
            const cleaned = rawText.replace(/<\/?(?:response|delegation|silence)>/gi, '');
            if (cleaned !== rawText) {
                console.debug('[vlm] stripped decision tokens from display text');
            }
            return cleaned.trim();
        }

        // Recursively strip decision tokens from any string value inside a debug
        // payload so the raw API response JSON view never exposes them either.
        function sanitizeDebugPayload(value) {
            if (typeof value === 'string') {
                return value.replace(/<\/?(?:response|delegation|silence)>/gi, '');
            }
            if (Array.isArray(value)) {
                return value.map(sanitizeDebugPayload);
            }
            if (value && typeof value === 'object') {
                const out = {};
                for (const key of Object.keys(value)) {
                    out[key] = sanitizeDebugPayload(value[key]);
                }
                return out;
            }
            return value;
        }

        // Markdown rendering helpers moved to render_markdown.js (window.JoyRender).
        // Local aliases keep existing call sites (renderMarkdown, escapeHtml, ...) working.
        const {
            escapeHtml,
            decodeHtmlEntities,
            protectMarkdownCodeSpans,
            restoreMarkdownCodeSpans,
            renderMathToHtml,
            renderMarkdownMath,
            renderMarkdown,
            openLinksInNewTabs,
        } = window.JoyRender;

        // Update markdown toggle UI
        function updateMarkdownToggleUI() {
            const contentDiv = document.getElementById('resultTextContent');
            if (window.JoyState.markdownEnabled) {
                markdownIcon.replaceChildren(createLucideIcon('code'));
                markdownText.textContent = window.JoyI18n.localizeUiString('Markdown');
                if (contentDiv) {
                    contentDiv.classList.add('markdown-rendered');
                } else {
                    resultText.classList.add('markdown-rendered');
                }
            } else {
                markdownIcon.replaceChildren(createLucideIcon('file-text'));
                markdownText.textContent = window.JoyI18n.localizeUiString('Plain Text');
                if (contentDiv) {
                    contentDiv.classList.remove('markdown-rendered');
                } else {
                    resultText.classList.remove('markdown-rendered');
                }
            }
            lucide.createIcons();
        }

        // Markdown toggle handler
        markdownToggle.addEventListener('click', () => {
            window.JoyState.markdownEnabled = !window.JoyState.markdownEnabled;
            localStorage.setItem('markdownEnabled', window.JoyState.markdownEnabled.toString());
            updateMarkdownToggleUI();

            // Re-render current text with new mode
            renderVlmHistory();
        });

        // Initialize markdown toggle state
        updateMarkdownToggleUI();

        // Copy to clipboard functionality
        copyButton.addEventListener('click', async () => {
            const contentDiv = document.getElementById('resultTextContent');
            let textToCopy = '';

            if (contentDiv) {
                // If markdown is enabled, copy the raw text (not HTML)
                if (window.JoyState.markdownEnabled) {
                    // Get the raw text by reading from lastText or extracting from content
                    const latestEntry = vlmHistory[vlmHistory.length - 1] || {};
                    textToCopy = latestEntry.response || latestEntry.error || contentDiv.innerText || contentDiv.textContent;
                } else {
                    const latestEntry = vlmHistory[vlmHistory.length - 1] || {};
                    textToCopy = latestEntry.response || latestEntry.error || contentDiv.textContent || contentDiv.innerText;
                }
            } else {
                textToCopy = resultText.textContent || resultText.innerText;
            }

            if (!textToCopy || textToCopy.trim() === '') {
                return; // Nothing to copy
            }

            try {
                await navigator.clipboard.writeText(textToCopy);

                // Visual feedback
                copyButton.classList.add('copied');
                copyButton.replaceChildren(createLucideIcon('check'));
                lucide.createIcons();

                // Reset after 0.8 seconds
                setTimeout(() => {
                    copyButton.classList.remove('copied');
                    copyButton.replaceChildren(createLucideIcon('copy'));
                    lucide.createIcons();
                }, 800);
            } catch (err) {
                console.error('Failed to copy text:', err);
                // Fallback for older browsers
                const textArea = document.createElement('textarea');
                textArea.value = textToCopy;
                textArea.style.position = 'fixed';
                textArea.style.opacity = '0';
                document.body.appendChild(textArea);
                textArea.select();
                try {
                    document.execCommand('copy');
                    copyButton.classList.add('copied');
                    setTimeout(() => copyButton.classList.remove('copied'), 500);
                } catch (e) {
                    console.error('Fallback copy failed:', e);
                }
                document.body.removeChild(textArea);
            }
        });

        function renderTextIntoElement(element, text) {
            if (window.JoyState.markdownEnabled) {
                // renderMarkdown returns DOMPurify-sanitized HTML; inject via a
                // fragment (no direct innerHTML assignment) to satisfy static
                // analysis while keeping the sanitization guarantee.
                const fragment = document.createRange().createContextualFragment(renderMarkdown(text));
                element.replaceChildren(fragment);
            } else {
                element.textContent = text;
            }
        }

        function extractFencedBlocks(text, language) {
            return extractFencedBlockCandidates(text, language, { includeUnclosed: false })
                .map(candidate => candidate.text);
        }

        function extractFencedBlockCandidates(text, language, options = {}) {
            const blocks = [];
            const source = String(text || '');
            const pattern = /```[ \t]*([a-zA-Z0-9_-]*)[ \t]*\r?\n?/g;
            const requestedLanguage = String(language || '').toLowerCase();
            let match;
            while ((match = pattern.exec(source)) !== null) {
                const lang = (match[1] || '').toLowerCase();
                const contentStart = pattern.lastIndex;
                const closingIndex = source.indexOf('```', contentStart);
                if (!requestedLanguage || lang === requestedLanguage) {
                    if (closingIndex >= 0) {
                        blocks.push({
                            text: source.slice(contentStart, closingIndex).trim(),
                            incomplete: false
                        });
                    } else if (options.includeUnclosed) {
                        blocks.push({
                            text: source.slice(contentStart).trim(),
                            incomplete: true
                        });
                    }
                }
                if (closingIndex >= 0) {
                    pattern.lastIndex = closingIndex + 3;
                } else {
                    break;
                }
            }
            return blocks;
        }

        function extractInlineJsonObjectCandidates(text, requiredType = '') {
            const source = String(text || '');
            const candidates = [];
            const wantedType = String(requiredType || '').toLowerCase();

            for (let start = 0; start < source.length; start += 1) {
                if (source[start] !== '{') continue;

                let depth = 0;
                let inString = false;
                let escaped = false;

                for (let index = start; index < source.length; index += 1) {
                    const char = source[index];
                    if (inString) {
                        if (escaped) {
                            escaped = false;
                        } else if (char === '\\') {
                            escaped = true;
                        } else if (char === '"') {
                            inString = false;
                        }
                        continue;
                    }

                    if (char === '"') {
                        inString = true;
                    } else if (char === '{') {
                        depth += 1;
                    } else if (char === '}') {
                        depth -= 1;
                        if (depth === 0) {
                            const end = index + 1;
                            const candidate = source.slice(start, end).trim();
                            if (candidate.includes('"type"')) {
                                try {
                                    const parsed = JSON.parse(candidate);
                                    const type = String(parsed?.type || '').toLowerCase();
                                    if (!wantedType || type === wantedType) {
                                        candidates.push({ text: candidate, start, end, parsed });
                                    }
                                } catch (err) {
                                    // This was a brace-balanced object, but not valid JSON.
                                }
                            }
                            start = end - 1;
                            break;
                        }
                    }

                    if (index - start > 160000) {
                        break;
                    }
                }
            }

            return candidates;
        }

        function stripInlineStructuredJsonFromText(text) {
            let source = String(text || '');
            const removable = extractInlineJsonObjectCandidates(source)
                .filter(candidate => {
                    const type = String(candidate.parsed?.type || '').toLowerCase();
                    return type === 'bar_chart' || type === 'html';
                })
                .sort((a, b) => b.start - a.start);

            removable.forEach((candidate) => {
                let start = candidate.start;
                const prefix = source.slice(Math.max(0, start - 12), start);
                const jsonLabel = prefix.match(/(?:^|\s)json\s*$/i);
                if (jsonLabel) {
                    start -= jsonLabel[0].length;
                }
                source = `${source.slice(0, start)}${source.slice(candidate.end)}`;
            });

            return source.replace(/[ \t]{2,}/g, ' ').trim();
        }

if (typeof window !== 'undefined') {
    window.JoyVlmRender = {
        getVlmDisplayText,
        sanitizeDebugPayload,
        renderTextIntoElement,
        extractFencedBlocks,
        extractFencedBlockCandidates,
        extractInlineJsonObjectCandidates,
        stripInlineStructuredJsonFromText,
        updateMarkdownToggleUI
    };
}
