// Extracted from index.html so the monolith shrinks (batch-3 split).
// Pure mechanical move: byte-for-byte function bodies, no logic change.
// Kept as top-level global declarations (classic script) so cross-file
// calls and shared `let`/`const` state keep working exactly as before.
// window.JoyXxx namespace is attached additively (D-033 pattern).

        // Mirrors vlm_history.js createLucideIcon: build a Lucide icon
        // placeholder without innerHTML assignment (avoids sast_xss_inner_html
        // false positives). The <i data-lucide> node is converted to <svg> by the
        // existing lucide.createIcons() calls in this file.
        function createLucideIcon(name) {
            const icon = document.createElement('i');
            icon.setAttribute('data-lucide', name);
            return icon;
        }

        function parseBackgroundChart(text) {
            const candidates = extractFencedBlocks(text, 'json');
            extractInlineJsonObjectCandidates(text, 'bar_chart').forEach(candidate => {
                candidates.push(candidate.text);
            });
            const trimmed = String(text || '').trim();
            if (trimmed.startsWith('{') && trimmed.endsWith('}')) {
                candidates.unshift(trimmed);
            }

            for (const candidate of candidates) {
                try {
                    const parsed = JSON.parse(candidate);
                    if (
                        parsed &&
                        parsed.type === 'bar_chart' &&
                        Array.isArray(parsed.labels) &&
                        Array.isArray(parsed.values)
                    ) {
                        const labels = parsed.labels.map(label => String(label));
                        const values = parsed.values.map(value => Number(value));
                        if (labels.length && labels.length === values.length && values.every(Number.isFinite)) {
                            return {
                                title: String(parsed.title || ''),
                                labels,
                                values
                            };
                        }
                    }
                } catch (err) {
                    // Keep looking for a valid structured block.
                }
            }
            return null;
        }

        function extractBackgroundHtml(text) {
            return extractBackgroundHtmlDetails(text).html;
        }

        function extractBackgroundExplanation(text) {
            let source = String(text || '').trim();
            if (!source) return '';

            source = source
                .replace(/```[ \t]*html[ \t]*\r?\n[\s\S]*?(?:\r?\n```|$)/gi, '')
                .replace(/```[ \t]*json[ \t]*\r?\n\s*\{[\s\S]*?"type"\s*:\s*"(?:html|bar_chart)"[\s\S]*?(?:\r?\n```|$)/gi, '')
                .trim();
            source = stripInlineStructuredJsonFromText(source);

            const doctypeIndex = source.search(/<!doctype html/i);
            const htmlIndex = source.search(/<html[\s>]/i);
            const starts = [doctypeIndex, htmlIndex].filter(index => index >= 0);
            if (starts.length) {
                source = source.slice(0, Math.min(...starts)).trim();
            }

            return source;
        }

        function extractBackgroundHtmlDetails(text) {
            const explicitHtmlCandidates = [];
            const htmlBlocks = extractFencedBlockCandidates(text, 'html', { includeUnclosed: true });
            htmlBlocks.forEach((block) => {
                explicitHtmlCandidates.push({
                    text: block.text,
                    incomplete: block.incomplete
                });
            });

            const candidates = extractFencedBlocks(text, 'json');
            extractInlineJsonObjectCandidates(text, 'html').forEach(candidate => {
                candidates.push(candidate.text);
            });
            const trimmed = String(text || '').trim();
            if (trimmed.startsWith('{') && trimmed.endsWith('}')) {
                candidates.unshift(trimmed);
            }
            for (const candidate of candidates) {
                try {
                    const parsed = JSON.parse(candidate);
                    if (parsed && parsed.type === 'html' && typeof parsed.html === 'string') {
                        explicitHtmlCandidates.push({
                            text: parsed.html,
                            incomplete: false
                        });
                    }
                } catch (err) {
                    // Keep looking.
                }
            }

            const explicitBest = pickBestBackgroundHtmlCandidate(explicitHtmlCandidates);
            if (explicitBest) {
                return explicitBest;
            }

            const fallbackBest = pickBestBackgroundHtmlCandidate([{
                text: trimmed,
                incomplete: false
            }]);
            if (fallbackBest) {
                return fallbackBest;
            }
            return { html: '', incomplete: false };
        }

        function pickBestBackgroundHtmlCandidate(htmlCandidates) {
            let best = null;
            htmlCandidates.forEach((candidate) => {
                const details = normalizeExtractedHtmlDetails(candidate.text, {
                    sourceIncomplete: candidate.incomplete
                });
                if (!details.html) return;
                const scored = {
                    ...details,
                    score: scoreHtmlCandidate(details.html, details.incomplete)
                };
                if (!best || scored.score > best.score) {
                    best = scored;
                }
            });

            if (best) {
                return {
                    html: best.html,
                    incomplete: best.incomplete
                };
            }
            return null;
        }

        function normalizeExtractedHtml(value) {
            return normalizeExtractedHtmlDetails(value).html;
        }

        function normalizeExtractedHtmlDetails(value, options = {}) {
            let html = decodeHtmlEntities(value).trim();
            if (!html) {
                return { html: '', incomplete: false };
            }

            html = html
                .replace(/^```[ \t]*(?:html)?[ \t]*\r?\n?/i, '')
                .replace(/\r?\n?```[ \t]*$/i, '')
                .trim();

            const doctypeIndex = html.search(/<!doctype html/i);
            const htmlIndex = html.search(/<html[\s>]/i);
            const startCandidates = [doctypeIndex, htmlIndex].filter(index => index >= 0);
            if (!startCandidates.length) {
                if (/<(head|body|main|section|style|div|h1|p)\b/i.test(html)) {
                    return {
                        html: completeStaticHtmlDocument(`<!doctype html>\n<html><head><meta charset="UTF-8"></head><body>${html}`),
                        incomplete: Boolean(options.sourceIncomplete || looksIncompleteHtml(html))
                    };
                }
                return { html: '', incomplete: false };
            }

            const startIndex = Math.min(...startCandidates);
            const endMatch = /<\/html\s*>/i.exec(html.slice(startIndex));
            if (endMatch) {
                const endIndex = startIndex + endMatch.index + endMatch[0].length;
                return {
                    html: html.slice(startIndex, endIndex).trim(),
                    incomplete: false
                };
            }
            return {
                html: completeStaticHtmlDocument(html.slice(startIndex).trim()),
                incomplete: true
            };
        }

        function scoreHtmlCandidate(html, incomplete = false) {
            const source = String(html || '');
            const lower = source.toLowerCase();
            let score = Math.min(source.length, 20000);
            if (!incomplete) score += 5000;
            if (lower.includes('<style')) score += 2500;
            if (lower.includes('<body')) score += 1500;
            if (/<(body|main|section|article|aside|header|footer|nav|div|h[1-6]|p|button|ul|ol|li|span|img|figure|table)\b/i.test(source)) {
                score += 1500;
            }
            const structuralMatches = source.match(/<(?:div|section|main|header|nav|footer|button|h[1-6]|p|li)\b/gi) || [];
            score += Math.min(structuralMatches.length * 120, 3000);
            const placeholderCount = (source.match(/\.{3}|…/g) || []).length;
            score -= placeholderCount * 3000;
            const placeholderElementCount = (source.match(/>\s*\.{3}\s*</g) || []).length;
            score -= placeholderElementCount * 5000;
            return score;
        }

        function looksIncompleteHtml(value) {
            const html = String(value || '');
            return (
                (/<html\b/i.test(html) && !/<\/html\s*>/i.test(html)) ||
                (/<body\b/i.test(html) && !/<\/body\s*>/i.test(html)) ||
                (/<head\b/i.test(html) && !/<\/head\s*>/i.test(html)) ||
                (/<style\b/i.test(html) && !/<\/style\s*>/i.test(html))
            );
        }

        // Static-HTML sanitizers moved to sanitize_static_html.js (window.JoySanitize).
        // Local aliases keep existing call sites (completeStaticHtmlDocument, sanitizeStaticHtml, ...) working.
        const {
            completeStaticHtmlDocument,
            sanitizeStaticHtml,
            normalizeStaticHtmlDocument,
            sanitizeStaticHtmlFallback,
            makeStaticHtmlNodeCleaner,
            isSafeStaticUrl,
            sanitizeStaticCss,
        } = window.JoySanitize;


        function drawBackgroundBarChart(canvas, chart) {
            const dpr = window.devicePixelRatio || 1;
            const rect = canvas.getBoundingClientRect();
            const width = Math.max(320, Math.floor(rect.width || canvas.clientWidth || 640));
            const height = Math.max(220, Math.floor(rect.height || canvas.clientHeight || 280));
            canvas.width = width * dpr;
            canvas.height = height * dpr;
            const ctx = canvas.getContext('2d');
            ctx.scale(dpr, dpr);
            ctx.clearRect(0, 0, width, height);

            const styles = getComputedStyle(document.documentElement);
            const textColor = styles.getPropertyValue('--text-primary').trim() || '#f5f5f5';
            const mutedColor = styles.getPropertyValue('--text-secondary').trim() || '#aaa';
            const accentColor = styles.getPropertyValue('--warning-color').trim() || '#FFA726';
            const gridColor = styles.getPropertyValue('--border-color').trim() || '#333';
            const padding = { top: chart.title ? 36 : 20, right: 18, bottom: 58, left: 48 };
            const plotW = width - padding.left - padding.right;
            const plotH = height - padding.top - padding.bottom;
            const maxValue = Math.max(...chart.values, 0);
            const scaleMax = maxValue <= 0 ? 1 : maxValue * 1.12;

            ctx.font = '600 14px system-ui, sans-serif';
            ctx.fillStyle = textColor;
            if (chart.title) {
                ctx.fillText(chart.title, padding.left, 22);
            }

            ctx.strokeStyle = gridColor;
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.moveTo(padding.left, padding.top);
            ctx.lineTo(padding.left, padding.top + plotH);
            ctx.lineTo(padding.left + plotW, padding.top + plotH);
            ctx.stroke();

            const barGap = Math.max(6, Math.min(18, plotW / chart.labels.length * 0.18));
            const barW = Math.max(10, (plotW - barGap * (chart.labels.length - 1)) / chart.labels.length);
            ctx.textAlign = 'center';
            ctx.font = '12px system-ui, sans-serif';
            chart.values.forEach((value, index) => {
                const x = padding.left + index * (barW + barGap);
                const barH = Math.max(1, (value / scaleMax) * plotH);
                const y = padding.top + plotH - barH;
                ctx.fillStyle = accentColor;
                ctx.fillRect(x, y, barW, barH);
                ctx.fillStyle = textColor;
                ctx.fillText(String(value), x + barW / 2, Math.max(padding.top + 12, y - 6));
                ctx.fillStyle = mutedColor;
                const label = chart.labels[index];
                const shortLabel = label.length > 14 ? label.slice(0, 13) + '…' : label;
                ctx.fillText(shortLabel, x + barW / 2, padding.top + plotH + 22);
            });
            ctx.textAlign = 'left';
        }

        let activeBackgroundModal = null;

        function createBackgroundActionButton(icon, label, onClick) {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'background-rich-action';
            button.appendChild(createLucideIcon(icon));
            const labelEl = document.createElement('span');
            labelEl.textContent = label;
            button.appendChild(labelEl);
            button.addEventListener('click', onClick);
            return button;
        }

        function openHtmlArtifact(url) {
            const value = String(url || '').trim();
            if (!value) return;
            window.open(value, '_blank', 'noopener,noreferrer');
        }

        function renderBackgroundRawView(view, text, options = {}) {
            view.className = 'background-raw-shell';
            const toolbar = document.createElement('div');
            toolbar.className = 'background-raw-toolbar';

            const copyButton = createBackgroundActionButton('copy', '复制', async () => {
                try {
                    await navigator.clipboard.writeText(String(text || ''));
                    const copiedIcon = createLucideIcon('check');
                    const copiedLabel = document.createElement('span');
                    copiedLabel.textContent = '已复制';
                    copyButton.replaceChildren(copiedIcon, copiedLabel);
                    lucide.createIcons();
                    setTimeout(() => {
                        const copyIcon = createLucideIcon('copy');
                        const copyLabel = document.createElement('span');
                        copyLabel.textContent = '复制';
                        copyButton.replaceChildren(copyIcon, copyLabel);
                        lucide.createIcons();
                    }, 900);
                } catch (err) {
                    console.error('Failed to copy background raw result:', err);
                }
            });
            toolbar.appendChild(copyButton);

            if (!options.modal) {
                toolbar.appendChild(createBackgroundActionButton('maximize-2', '放大', () => {
                    openBackgroundModal('原始', (body) => renderBackgroundRawView(body, text, { modal: true }));
                }));
            }

            const pre = document.createElement('pre');
            pre.className = 'background-raw-block';
            pre.textContent = String(text || '');
            view.appendChild(toolbar);
            view.appendChild(pre);
        }

        function renderBackgroundChartView(view, chart, options = {}) {
            view.className = 'background-view-shell';
            if (!options.modal) {
                const toolbar = document.createElement('div');
                toolbar.className = 'background-view-toolbar';
                toolbar.appendChild(createBackgroundActionButton('maximize-2', '放大', () => {
                    openBackgroundModal('图表', (body) => renderBackgroundChartView(body, chart, { modal: true }));
                }));
                view.appendChild(toolbar);
            }

            const canvas = document.createElement('canvas');
            canvas.className = 'background-chart';
            view.appendChild(canvas);
            requestAnimationFrame(() => drawBackgroundBarChart(canvas, chart));
        }

        function renderBackgroundHtmlView(view, html, options = {}) {
            view.className = 'background-view-shell';
            if (!options.modal) {
                const toolbar = document.createElement('div');
                toolbar.className = 'background-view-toolbar';
                if (options.htmlUrl) {
                    toolbar.appendChild(createBackgroundActionButton('external-link', '打开', () => {
                        openHtmlArtifact(options.htmlUrl);
                    }));
                }
                toolbar.appendChild(createBackgroundActionButton('maximize-2', '放大', () => {
                    openBackgroundModal('预览', (body) => renderBackgroundHtmlView(body, html, {
                        modal: true,
                        incomplete: options.incomplete,
                        htmlUrl: options.htmlUrl
                    }));
                }));
                view.appendChild(toolbar);
            } else if (options.htmlUrl) {
                const toolbar = document.createElement('div');
                toolbar.className = 'background-view-toolbar';
                toolbar.appendChild(createBackgroundActionButton('external-link', '打开', () => {
                    openHtmlArtifact(options.htmlUrl);
                }));
                view.appendChild(toolbar);
            }

            if (options.incomplete) {
                const warning = document.createElement('div');
                warning.className = 'background-preview-warning';
                warning.textContent = '后台 HTML 可能被截断，预览已尽量补齐闭合标签；请以“原始”内容为准。';
                view.appendChild(warning);
            }

            const iframe = document.createElement('iframe');
            iframe.className = 'background-html-frame';
            iframe.setAttribute('sandbox', '');
            iframe.srcdoc = sanitizeStaticHtml(html);
            view.appendChild(iframe);
        }

        function openBackgroundModal(title, renderContent) {
            closeBackgroundModal();

            const backdrop = document.createElement('div');
            backdrop.className = 'background-modal-backdrop';
            backdrop.setAttribute('role', 'dialog');
            backdrop.setAttribute('aria-modal', 'true');

            const dialog = document.createElement('div');
            dialog.className = 'background-modal-dialog';
            dialog.addEventListener('click', (event) => event.stopPropagation());

            const header = document.createElement('div');
            header.className = 'background-modal-header';
            const titleElement = document.createElement('h3');
            titleElement.className = 'background-modal-title';
            titleElement.textContent = title;
            const closeButton = document.createElement('button');
            closeButton.type = 'button';
            closeButton.className = 'background-modal-close';
            closeButton.setAttribute('aria-label', '关闭');
            closeButton.appendChild(createLucideIcon('x'));
            closeButton.addEventListener('click', closeBackgroundModal);
            header.appendChild(titleElement);
            header.appendChild(closeButton);

            const body = document.createElement('div');
            body.className = 'background-modal-body';
            renderContent(body);

            dialog.appendChild(header);
            dialog.appendChild(body);
            backdrop.appendChild(dialog);
            backdrop.addEventListener('click', closeBackgroundModal);

            document.body.appendChild(backdrop);
            document.body.classList.add('background-modal-open');
            activeBackgroundModal = backdrop;
            lucide.createIcons();
            closeButton.focus();
        }

        function closeBackgroundModal() {
            if (!activeBackgroundModal) return;
            activeBackgroundModal.remove();
            activeBackgroundModal = null;
            document.body.classList.remove('background-modal-open');
        }

        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape' && activeBackgroundModal) {
                closeBackgroundModal();
            }
        });

        function renderBackgroundRichContent(element, text, rich = null) {
            const structuredChart = rich && rich.chart && Array.isArray(rich.chart.labels) && Array.isArray(rich.chart.values)
                ? {
                    title: String(rich.chart.title || ''),
                    labels: rich.chart.labels.map(label => String(label)),
                    values: rich.chart.values.map(value => Number(value))
                }
                : null;
            const chart = structuredChart || parseBackgroundChart(text);
            let htmlDetails;
            if (rich && typeof rich.html === 'string' && rich.html.trim()) {
                htmlDetails = normalizeExtractedHtmlDetails(rich.html, {
                    sourceIncomplete: Boolean(rich.html_incomplete)
                });
                htmlDetails.incomplete = Boolean(rich.html_incomplete || htmlDetails.incomplete);
            } else {
                htmlDetails = extractBackgroundHtmlDetails(text);
            }
            const html = htmlDetails.html;
            const hasRich = Boolean(chart || html);
            const explanation = extractBackgroundExplanation(text);
            if (!hasRich) {
                renderTextIntoElement(element, text);
                element.classList.toggle('markdown-rendered', window.JoyState.markdownEnabled);
                return;
            }

            element.replaceChildren();
            element.classList.add('background-rich-content');

            const tabs = document.createElement('div');
            tabs.className = 'background-rich-tabs';
            const body = document.createElement('div');
            const views = [];
            let preferredViewLabel = chart ? '图表' : html ? '预览' : explanation ? '说明' : '原始';

            const addView = (label, render) => {
                const tab = document.createElement('button');
                tab.type = 'button';
                tab.className = 'background-rich-tab';
                tab.textContent = label;
                const view = document.createElement('div');
                view.style.display = 'none';
                render(view);
                tab.addEventListener('click', () => {
                    views.forEach(item => {
                        item.tab.classList.toggle('active', item.view === view);
                        item.view.style.display = item.view === view ? 'block' : 'none';
                    });
                });
                tabs.appendChild(tab);
                body.appendChild(view);
                views.push({ label, tab, view });
            };

            if (explanation) {
                addView('说明', (view) => {
                    view.className = 'background-view-shell markdown-rendered';
                    renderTextIntoElement(view, explanation);
                });
            }

            addView('原始', (view) => {
                renderBackgroundRawView(view, text);
            });

            if (chart) {
                addView('图表', (view) => {
                    renderBackgroundChartView(view, chart);
                });
            }

            if (html) {
                addView('预览', (view) => {
                    renderBackgroundHtmlView(view, html, {
                        incomplete: htmlDetails.incomplete,
                        htmlUrl: rich && typeof rich.html_url === 'string' ? rich.html_url : ''
                    });
                });
            }

            element.appendChild(tabs);
            element.appendChild(body);
            if (views.length) {
                const defaultView = views.find(item => item.label === preferredViewLabel) || views[0];
                defaultView.tab.click();
                lucide.createIcons();
            }
        }

if (typeof window !== 'undefined') {
    window.JoyBackgroundRich = {
        parseBackgroundChart,
        extractBackgroundHtml,
        extractBackgroundExplanation,
        extractBackgroundHtmlDetails,
        normalizeExtractedHtml,
        renderBackgroundRichContent,
        openBackgroundModal,
        closeBackgroundModal,
        drawBackgroundBarChart
    };
}
