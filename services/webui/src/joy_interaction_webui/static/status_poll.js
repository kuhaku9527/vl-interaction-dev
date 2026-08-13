// Extracted from index.html so the monolith shrinks (batch-3 split).
// Pure mechanical move: byte-for-byte function bodies, no logic change.
// Kept as top-level global declarations (classic script) so cross-file
// calls and shared `let`/`const` state keep working exactly as before.
// window.JoyXxx namespace is attached additively (D-033 pattern).

        (function () {

            // ---- Service-status polling (LLM / TTS / KWS) ----
            const _STATUS_LABEL = { ok: 'OK', error: 'ERR', degraded: 'DEG', missing: 'MISS' };

            function renderServiceBadge(el, status, kind) {
                if (!el) return;
                const norm = (status || 'unknown').toLowerCase();
                let cls = 'llm-unknown';
                if (norm === 'ok' || norm === 'present') cls = 'llm-ok';
                else if (norm === 'error' || norm === 'missing' || norm === 'unreachable') cls = 'llm-err';
                el.className = 'status-badge ' + cls;
                const label = _STATUS_LABEL[norm] || '?';
                const prefix = kind === 'llm' ? 'LLM' : kind === 'tts' ? 'TTS' : 'KWS';
                el.textContent = prefix + ' ' + label;
            }

            async function pollServiceStatus() {
                try {
                    const resp = await fetch('/api/llm/status?session_id=' + encodeURIComponent(sessionId || 'default'));
                    if (!resp.ok) {
                        const llmEl = document.getElementById('llmServiceStatus');
                        const ttsEl = document.getElementById('ttsServiceStatus');
                        const kwsEl = document.getElementById('kwsServiceStatus');
                        if (llmEl) { llmEl.className = 'status-badge llm-err'; llmEl.textContent = window.JoyI18n.localizeUiString('LLM ERR'); }
                        if (ttsEl) { ttsEl.className = 'status-badge llm-err'; ttsEl.textContent = window.JoyI18n.localizeUiString('TTS ERR'); }
                        if (kwsEl) { kwsEl.className = 'status-badge llm-err'; kwsEl.textContent = window.JoyI18n.localizeUiString('KWS ERR'); }
                        return;
                    }
                    const data = await resp.json();
                    renderServiceBadge(document.getElementById('llmServiceStatus'), (data.llm && data.llm.status) || 'unknown', 'llm');
                    renderServiceBadge(document.getElementById('ttsServiceStatus'), (data.tts && data.tts.status) || 'unknown', 'tts');
                    renderServiceBadge(document.getElementById('kwsServiceStatus'), (data.kws && data.kws.status) || 'unknown', 'kws');
                } catch (e) {
                    const llmEl = document.getElementById('llmServiceStatus');
                    const ttsEl = document.getElementById('ttsServiceStatus');
                    const kwsEl = document.getElementById('kwsServiceStatus');
                    if (llmEl) { llmEl.className = 'status-badge llm-err'; llmEl.textContent = window.JoyI18n.localizeUiString('LLM ERR'); }
                    if (ttsEl) { ttsEl.className = 'status-badge llm-err'; ttsEl.textContent = window.JoyI18n.localizeUiString('TTS ERR'); }
                    if (kwsEl) { kwsEl.className = 'status-badge llm-err'; kwsEl.textContent = window.JoyI18n.localizeUiString('KWS ERR'); }
                }
            }

            let _servicePollTimer = null;
            function startServiceStatusPoll() {
                if (_servicePollTimer) return;
                pollServiceStatus();
                _servicePollTimer = setInterval(pollServiceStatus, 3000);
            }
            function stopServiceStatusPoll() {
                if (_servicePollTimer) {
                    clearInterval(_servicePollTimer);
                    _servicePollTimer = null;
                }
            }
            window.startServiceStatusPoll = startServiceStatusPoll;
            window.stopServiceStatusPoll = stopServiceStatusPoll;

        // ====================================================================
        // Jarvis state-pill (polls /api/jarvis/status every 1s)
        // Shows the KWS_LISTENING / DIALOG_ACTIVE / TTS_PAUSED / etc. transitions.
        // ====================================================================
        const JARVIS_STATE_MAP = {
            'KWS_LISTENING':  { emoji: '🟢', text: 'KWS 监听中', cls: 'jarvis-listening' },
            'WAKE_DETECTED':  { emoji: '🔵', text: '唤醒已触发', cls: 'jarvis-wake' },
            'WAIT_ASR_CONFIRM': { emoji: '🔎', text: 'ASR 确认中', cls: 'jarvis-confirm' },
            'DIALOG_ACTIVE':  { emoji: '🟡', text: '对话中',     cls: 'jarvis-dialog' },
            'TTS_PAUSED':     { emoji: '⏸️', text: '已打断',      cls: 'jarvis-paused' },
            'EXIT_DETECTED':  { emoji: '👋', text: '退出中',     cls: 'jarvis-exit' },
            'ERROR':          { emoji: '❌', text: '错误',       cls: 'jarvis-error' },
        };
        let _jarvisPollTimer = null;

        function renderJarvisStatus(payload) {
            const el = document.getElementById('jarvisStatus');
            if (!el) return;
            if (!payload || !payload.exists) {
                el.textContent = '⚫ 未连接';
                el.className = 'status-badge jarvis-disconnected';
                return;
            }
            const info = JARVIS_STATE_MAP[payload.state] ||
                { emoji: '⚪', text: payload.state, cls: 'jarvis-unknown' };
            const awake = payload.is_awake ? ' (唤醒)' : '';
            el.textContent = info.emoji + ' ' + info.text + awake;
            el.className = 'status-badge ' + info.cls;
        }

        async function pollJarvisStatus() {
            try {
                const sid = sessionId || 'default';
                const resp = await fetch('/api/jarvis/status?session_id=' + encodeURIComponent(sid));
                if (!resp.ok) throw new Error('HTTP ' + resp.status);
                const data = await resp.json();
                renderJarvisStatus(data);
            } catch (err) {
                const el = document.getElementById('jarvisStatus');
                if (el) {
                    el.textContent = '⚪ 状态查询失败';
                    el.className = 'status-badge jarvis-unknown';
                }
            }
        }

        function startJarvisStatusPoll() {
            if (_jarvisPollTimer) return;
            updateStatus(window.JoyI18n.localizeUiString('Connected'), 'connected');
            _jarvisPollTimer = setInterval(pollJarvisStatus, 1000);
        }

        function stopJarvisStatusPoll() {
            if (_jarvisPollTimer) {
                clearInterval(_jarvisPollTimer);
                _jarvisPollTimer = null;
            }
            const el = document.getElementById('jarvisStatus');
            if (el) {
                el.textContent = '⚫ 未连接';
                el.className = 'status-badge jarvis-disconnected';
            }
        }

        // ====================================================================
        // Live state-pill (polls /api/live/status every 1s)
        // Shows the live turn-state transitions (免唤醒词常驻监听).
        // ====================================================================
        const LIVE_STATE_MAP = {
            'LISTENING':       { emoji: '🟢', text: 'Live 监听中', cls: 'live-listening' },
            'USER_SPEAKING':   { emoji: '🔵', text: 'Live 听你说',  cls: 'live-speaking' },
            'PROCESSING':      { emoji: '🟡', text: 'Live 处理中',  cls: 'live-processing' },
            'THINKING':        { emoji: '🟡', text: 'Live 思考中',  cls: 'live-processing' },
            'PRE_SPEECH':      { emoji: '🟡', text: 'Live 回复中',  cls: 'live-speaking' },
            'SPEAKING':        { emoji: '🔊', text: 'Live 回复中',  cls: 'live-speaking' },
            'HARD_INTERRUPTED': { emoji: '⏸️', text: 'Live 已打断', cls: 'live-interrupted' },
            'SOFT_INTERRUPTED': { emoji: '⏸️', text: 'Live 已打断', cls: 'live-interrupted' },
            'COOLDOWN':        { emoji: '⏳', text: 'Live 冷却中',  cls: 'live-interrupted' },
            'ENDED':           { emoji: '⚫', text: 'Live 已结束',  cls: 'live-disconnected' },
            'ERROR':           { emoji: '❌', text: 'Live 错误',    cls: 'live-error' },
        };

        function renderLiveStatus(payload) {
            const el = document.getElementById('liveStatus');
            if (!el) return;
            if (!payload || !payload.exists || !liveModeActive) {
                el.textContent = '⚫ Live 未连接';
                el.className = 'status-badge live-disconnected';
                return;
            }
            const info = LIVE_STATE_MAP[payload.turn_state] ||
                { emoji: '⚪', text: payload.turn_state, cls: 'live-unknown' };
            el.textContent = info.emoji + ' ' + info.text;
            el.className = 'status-badge ' + info.cls;
            // C.B layer 3: keep the proactive switch in sync with the runtime
            // state reported by the backend (env gate + actual loop task).
            if (typeof payload.proactive_supported === 'boolean') {
                liveProactiveSupported = payload.proactive_supported;
                if (liveProactiveToggle) {
                    liveProactiveToggle.checked = Boolean(payload.proactive_enabled);
                }
                setLiveProactiveUiState();
            }
        }

        async function pollLiveStatus() {
            if (!liveModeActive) {
                renderLiveStatus(null);
                return;
            }
            try {
                const sid = sessionId || 'default';
                const resp = await fetch('/api/live/status?session_id=' + encodeURIComponent(sid));
                if (!resp.ok) throw new Error('HTTP ' + resp.status);
                const data = await resp.json();
                renderLiveStatus(data);
            } catch (err) {
                const el = document.getElementById('liveStatus');
                if (el) {
                    el.textContent = '⚪ Live 状态查询失败';
                    el.className = 'status-badge live-unknown';
                }
            }
        }

        function startLiveStatusPoll() {
            if (_livePollTimer) return;
            _livePollTimer = setInterval(pollLiveStatus, 1000);
        }

        function stopLiveStatusPoll() {
            if (_livePollTimer) {
                clearInterval(_livePollTimer);
                _livePollTimer = null;
            }
            const el = document.getElementById('liveStatus');
            if (el) {
                el.textContent = '⚫ Live 未连接';
                el.className = 'status-badge live-disconnected';
            }
        }
        window.startLiveStatusPoll = startLiveStatusPoll;
        window.stopLiveStatusPoll = stopLiveStatusPoll;

        // ====================================================================
        // Extended health badges: Memory / Wiki (#46)
        // Polls /api/services/extended-status every 5s; renders two header
        // badges with 3-state coloring (green / gray / red).
        // ====================================================================
        function renderExtBadge(el, info, kind) {
            if (!el || !info) return;
            const zh = kind === 'memory' ? '记忆' : '知识库';
            const envFlag = kind === 'memory' ? 'JOYAI_ENABLE_MEMORY_STORE=1' : 'WIKI_RECALL_ENABLED=1';
            let cls, text;
            if (!info.enabled) {
                cls = 'llm-unknown';
                text = zh + ' 未启用';
            } else if (!info.reachable) {
                cls = 'llm-unknown';
                text = zh + ' 离线';
            } else if (info.ok === true) {
                cls = 'llm-ok';
                text = zh + ' 在线';
            } else if (info.ok === false) {
                cls = 'llm-err';
                text = zh + ' 异常';
            } else {
                cls = 'llm-unknown';
                text = zh + ' ?';
            }
            el.className = 'status-badge ' + cls;
            el.textContent = text;
            const detail = info.reason || (info.enabled ? '' : ('可选功能，未启用（设 ' + envFlag + ' 启用）'));
            el.title = zh + ' 状态' + (detail ? '：' + detail : '');
            if (info.ok === false && info.enabled) {
                el.style.cursor = 'pointer';
                el.onclick = function () {
                    const sm = document.getElementById('settingsModal');
                    if (sm) sm.classList.add('show');
                    if (kind === 'wiki') {
                        const kb = document.getElementById('knowledgeBaseToggle');
                        if (kb) kb.click();
                        if (window.JoyWiki) window.JoyWiki.loadHealth();
                    }
                };
            } else {
                el.style.cursor = '';
                el.onclick = null;
            }
        }

        async function pollExtendedStatus() {
            try {
                const resp = await fetch('/api/services/extended-status');
                if (!resp.ok) throw new Error('HTTP ' + resp.status);
                const data = await resp.json();
                renderExtBadge(document.getElementById('memoryBadge'), data.memory, 'memory');
                renderExtBadge(document.getElementById('wikiBadge'), data.wiki, 'wiki');
            } catch (err) {
                const fallback = { enabled: false, reachable: false, ok: null, reason: String((err && err.message) || err) };
                renderExtBadge(document.getElementById('memoryBadge'), fallback, 'memory');
                renderExtBadge(document.getElementById('wikiBadge'), fallback, 'wiki');
            }
        }

        let _extPollTimer = null;
        function startExtendedStatusPoll() {
            if (_extPollTimer) return;
            pollExtendedStatus();
            _extPollTimer = setInterval(pollExtendedStatus, 5000);
        }
        function stopExtendedStatusPoll() {
            if (_extPollTimer) {
                clearInterval(_extPollTimer);
                _extPollTimer = null;
            }
        }
        window.startExtendedStatusPoll = startExtendedStatusPoll;
        window.stopExtendedStatusPoll = stopExtendedStatusPoll;

        // Auto-start alongside the other status polls.
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', startJarvisStatusPoll);
            document.addEventListener('DOMContentLoaded', startLiveStatusPoll);
        } else {
            startJarvisStatusPoll();
            startLiveStatusPoll();
        }

            // Auto-start the service-status poll when the page is ready.
            if (document.readyState === 'loading') {
                document.addEventListener('DOMContentLoaded', startServiceStatusPoll);
            } else {
                startServiceStatusPoll();
            }

            // Auto-start extended health badges poll (#46)
            if (document.readyState === 'loading') {
                document.addEventListener('DOMContentLoaded', startExtendedStatusPoll);
            } else {
                startExtendedStatusPoll();
            }
        })();

if (typeof window !== 'undefined') {
    window.JoyStatusPoll = {
        renderJarvisStatus,
        pollJarvisStatus,
        startJarvisStatusPoll,
        stopJarvisStatusPoll,
        renderLiveStatus,
        pollLiveStatus,
        startLiveStatusPoll,
        stopLiveStatusPoll,
        pollExtendedStatus,
        startExtendedStatusPoll,
        stopExtendedStatusPoll,
        startServiceStatusPoll,
        stopServiceStatusPoll
    };
}
