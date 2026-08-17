'use strict';

// config_services.js — Services panel config/API form cluster (Block 3)
// Extracted from services/webui/src/joy_interaction_webui/static/index.html
// (original inline IIFE ~lines 4873-4944). Mounted on window.JoyConfig.
//
// NOTE: the button-wiring + load() auto-call stay inline in index.html.
// load() must run only after the services-panel DOM is parsed, otherwise
// writeForm() would populate form fields that do not yet exist.
(function () {
    // N7.1: agent（provider 下拉 codex|hermes）+ embedding（local|siliconflow|nvidia）。
    const SERVICES = ['llm', 'summary', 'tts', 'asr', 'agent', 'embedding'];

    function setBadge(name, status, hint) {
        const b = document.getElementById('badge-' + name);
        if (!b) return;
        b.textContent = status;
        b.className = 'service-badge ' + (status === 'OK' ? 'ok' : status === 'ERR' ? 'err' : '');
        if (hint) b.title = hint;
    }

    function readForm() {
        const cfg = { llm: {}, summary: {}, tts: {}, asr: {}, agent: {}, embedding: {} };
        const urlEl = function (s, k) { return document.getElementById('svc-' + s + '-' + k); };
        cfg.llm.api_base = (urlEl('llm', 'api-base') || {}).value || '';
        cfg.llm.model = (urlEl('llm', 'model') || {}).value || '';
        cfg.llm.api_key = (urlEl('llm', 'api-key') || {}).value || '';
        cfg.summary.provider = (urlEl('summary', 'provider') || {}).value || '';
        cfg.summary.api_base = (urlEl('summary', 'api-base') || {}).value || '';
        cfg.summary.model = (urlEl('summary', 'model') || {}).value || '';
        cfg.summary.api_key = (urlEl('summary', 'api-key') || {}).value || '';
        cfg.tts.api_base = (urlEl('tts', 'api-base') || {}).value || '';
        cfg.asr.api_base = (urlEl('asr', 'api-base') || {}).value || '';
        cfg.asr.model = (urlEl('asr', 'model') || {}).value || '';
        cfg.asr.api_key = (urlEl('asr', 'api-key') || {}).value || '';
        cfg.agent.provider = (urlEl('agent', 'provider') || {}).value || '';
        cfg.agent.api_base = (urlEl('agent', 'api-base') || {}).value || '';
        cfg.agent.api_key = (urlEl('agent', 'api-key') || {}).value || '';
        cfg.embedding.provider = (urlEl('embedding', 'provider') || {}).value || '';
        cfg.embedding.api_base = (urlEl('embedding', 'api-base') || {}).value || '';
        cfg.embedding.model = (urlEl('embedding', 'model') || {}).value || '';
        cfg.embedding.api_key = (urlEl('embedding', 'api-key') || {}).value || '';
        return cfg;
    }

    function writeForm(cfg) {
        const setVal = function (id, v) { const el = document.getElementById(id); if (el) el.value = v || ''; };
        setVal('svc-llm-api-base', cfg.llm && cfg.llm.api_base);
        setVal('svc-llm-model', cfg.llm && cfg.llm.model);
        setVal('svc-llm-api-key', cfg.llm && cfg.llm.api_key);
        setVal('svc-summary-provider', cfg.summary && cfg.summary.provider);
        setVal('svc-summary-api-base', cfg.summary && cfg.summary.api_base);
        setVal('svc-summary-model', cfg.summary && cfg.summary.model);
        setVal('svc-summary-api-key', cfg.summary && cfg.summary.api_key);
        setVal('svc-tts-api-base', cfg.tts && cfg.tts.api_base);
        setVal('svc-asr-api-base', cfg.asr && cfg.asr.api_base);
        setVal('svc-asr-model', cfg.asr && cfg.asr.model);
        setVal('svc-asr-api-key', cfg.asr && cfg.asr.api_key);
        setVal('svc-agent-provider', cfg.agent && cfg.agent.provider);
        setVal('svc-agent-api-base', cfg.agent && cfg.agent.api_base);
        setVal('svc-agent-api-key', cfg.agent && cfg.agent.api_key);
        setVal('svc-embedding-provider', cfg.embedding && cfg.embedding.provider);
        setVal('svc-embedding-api-base', cfg.embedding && cfg.embedding.api_base);
        setVal('svc-embedding-model', cfg.embedding && cfg.embedding.model);
        setVal('svc-embedding-api-key', cfg.embedding && cfg.embedding.api_key);
    }

    async function load() {
        try {
            const r = await fetch('/api/services/config');
            if (!r.ok) throw new Error('HTTP ' + r.status);
            const cfg = await r.json();
            writeForm(cfg);
        } catch (e) {
            console.warn('load services config:', e);
        }
        await probe();
    }

    async function probe() {
        SERVICES.forEach(function (s) { setBadge(s, '...'); });
        try {
            const r = await fetch('/api/services/status');
            if (!r.ok) throw new Error('HTTP ' + r.status);
            const data = await r.json();
            SERVICES.forEach(function (s) {
                const item = data[s] || {};
                setBadge(s, item.ok ? 'OK' : 'ERR', item.reason || '');
            });
        } catch (e) {
            SERVICES.forEach(function (s) { setBadge(s, 'ERR', String(e)); });
        }
    }

    async function save() {
        const cfg = readForm();
        try {
            const r = await fetch('/api/services/config', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(cfg),
            });
            if (!r.ok) throw new Error('HTTP ' + r.status);
            await probe();
        } catch (e) {
            alert('Save failed: ' + e);
        }
    }

    // N8: summary provider 切换联动——切换时自动填推荐 api_base/model 默认值，
    // 并**清空 api-key + 更新提示**（防旧 provider 的 key 误用导致 401——
    // openrouter 与 minimax 的 key 互不通用）。
    const SUMMARY_PRESETS = {
        openrouter: { api_base: 'https://openrouter.ai/api/v1', model: 'google/gemma-4-26b-a4b-it:free', keyHint: '填 OpenRouter API Key' },
        minimax: { api_base: 'https://api.minimaxi.com/v1', model: 'MiniMax-M3', keyHint: '填 MiniMax API Key' },
    };
    function wireSummaryProvider() {
        const sel = document.getElementById('svc-summary-provider');
        if (!sel) return;
        sel.addEventListener('change', function () {
            const preset = SUMMARY_PRESETS[sel.value];
            if (!preset) return;
            const base = document.getElementById('svc-summary-api-base');
            const model = document.getElementById('svc-summary-model');
            const key = document.getElementById('svc-summary-api-key');
            if (base) base.value = preset.api_base;
            if (model) model.value = preset.model;
            if (key) {
                key.value = '';
                key.placeholder = preset.keyHint;
            }
        });
    }

    // Axis 4 (v6-lite.12): local/cloud seg + Provider named presets.
    // - seg: toggle data-mode on .service-row (CSS moves the indicator; no confirm — v4-lite.2).
    // - Provider presets: localStorage[joyai.providers.<slot>] = [{name, api_base, model(, provider)}];
    //   api_key is NEVER stored client-side (backend-only, ADR-0014). Apply fills the visible
    //   fields + hidden provider select, then calls existing save() (PUT /api/services/config).
    const SEG_SLOTS = ['llm', 'summary', 'tts', 'asr', 'agent', 'embedding'];
    const LS_KEY = function (slot) { return 'joyai.providers.' + slot; };

    function _pRead(slot) {
        try {
            const raw = localStorage.getItem(LS_KEY(slot));
            const arr = raw ? JSON.parse(raw) : [];
            return Array.isArray(arr) ? arr : [];
        } catch (_e) { return []; }
    }
    function _pWrite(slot, arr) {
        try { localStorage.setItem(LS_KEY(slot), JSON.stringify(arr)); }
        catch (_e) { /* storage unavailable: degrade to in-session only */ }
    }
    function _pMsg(slot, text, isErr) {
        const el = document.getElementById('svc-' + slot + '-provider-msg');
        if (!el) return;
        el.textContent = text;
        el.className = 'provider-msg ' + (isErr ? 'err' : 'ok');
        setTimeout(function () { el.textContent = ''; el.className = 'provider-msg'; }, 2200);
    }
    function _pFillPick(slot) {
        const pick = document.getElementById('svc-' + slot + '-provider-pick');
        if (!pick) return;
        const arr = _pRead(slot);
        // 安全重建 options：用 DOM 构造 + textContent，避免 innerHTML 拼接
        // （DeepSec sast_xss_inner_html 将 <option> 字符串拼接判为 XSS 模式；
        //  且 textContent 对 & < > " 全部正确转义，优于手工 replace）。
        while (pick.firstChild) pick.removeChild(pick.firstChild);
        const ph = document.createElement('option');
        ph.value = '';
        ph.textContent = '— saved presets —';
        pick.appendChild(ph);
        arr.forEach(function (p) {
            const n = p && p.name ? p.name : '';
            const opt = document.createElement('option');
            opt.value = n;
            opt.textContent = n;
            pick.appendChild(opt);
        });
    }
    function _pApply(slot, name) {
        const arr = _pRead(slot);
        const p = arr.filter(function (x) { return x && x.name === name; })[0];
        if (!p) return;
        const base = document.getElementById('svc-' + slot + '-api-base');
        const model = document.getElementById('svc-' + slot + '-model');
        const prov = document.getElementById('svc-' + slot + '-provider');
        if (base && p.api_base != null) base.value = p.api_base;
        if (model && p.model != null) model.value = p.model;
        if (prov && p.provider != null) prov.value = p.provider; // hidden select → readForm still binds
        const nameEl = document.getElementById('svc-' + slot + '-provider-name');
        if (nameEl) nameEl.value = p.name;
        _pMsg(slot, 'Applied: ' + p.name, false);
        if (save) save(); // persist via existing PUT /api/services/config
    }
    function wireSegProvider() {
        SEG_SLOTS.forEach(function (slot) {
            const row = document.querySelector('.service-row[data-service="' + slot + '"]');
            if (!row) return;
            // seg toggle (CSS-driven indicator; no confirm)
            const seg = row.querySelector('.svc-seg');
            if (seg) {
                seg.querySelectorAll('.svc-seg-btn').forEach(function (btn) {
                    btn.addEventListener('click', function () {
                        const mode = btn.getAttribute('data-mode');
                        if (row.getAttribute('data-mode') === mode) return;
                        seg.querySelectorAll('.svc-seg-btn').forEach(function (b) {
                            const on = b === btn;
                            b.classList.toggle('on', on);
                            b.setAttribute('aria-selected', on ? 'true' : 'false');
                        });
                        row.setAttribute('data-mode', mode);
                    });
                });
            }
            // Provider presets (tts deferred per spec — seg only)
            if (slot === 'tts') return;
            _pFillPick(slot);
            const addBtn = document.getElementById('svc-' + slot + '-provider-add');
            const delBtn = document.getElementById('svc-' + slot + '-provider-del');
            const pick = document.getElementById('svc-' + slot + '-provider-pick');
            const nameEl = document.getElementById('svc-' + slot + '-provider-name');
            if (addBtn) {
                addBtn.addEventListener('click', function () {
                    const name = nameEl ? nameEl.value.trim() : '';
                    if (!name) { _pMsg(slot, 'Name first, then +', true); return; }
                    const base = document.getElementById('svc-' + slot + '-api-base');
                    const model = document.getElementById('svc-' + slot + '-model');
                    const prov = document.getElementById('svc-' + slot + '-provider');
                    const rec = { name: name, api_base: base ? base.value : '', model: model ? model.value : '' };
                    if (prov) rec.provider = prov.value; // only slots with provider select
                    const arr = _pRead(slot);
                    const idx = arr.findIndex(function (x) { return x && x.name === name; });
                    if (idx >= 0) arr[idx] = rec; else arr.push(rec);
                    _pWrite(slot, arr);
                    _pFillPick(slot);
                    if (pick) pick.value = name;
                    _pMsg(slot, 'Saved: ' + name, false);
                });
            }
            if (pick) {
                pick.addEventListener('change', function () {
                    if (pick.value) _pApply(slot, pick.value);
                });
            }
            if (delBtn) {
                delBtn.addEventListener('click', function () {
                    const name = nameEl ? nameEl.value.trim() : '';
                    if (!name) { _pMsg(slot, 'Type the name to delete', true); return; }
                    let arr = _pRead(slot);
                    const before = arr.length;
                    arr = arr.filter(function (x) { return !(x && x.name === name); });
                    if (arr.length === before) { _pMsg(slot, 'Not found: ' + name, true); return; }
                    _pWrite(slot, arr);
                    _pFillPick(slot);
                    if (pick) pick.value = '';
                    _pMsg(slot, 'Deleted: ' + name, false);
                });
            }
        });
    }

    // N9: 槽位「Test」按钮（通用版）——用表单**当前值**验证上游连通性。
    // 契约（与后端 POST /api/services/test 对齐，joyai-backend / joyai-backend-2 实现）：
    //   body { slot, api_base, model, api_key }
    //   成功 → HTTP 2xx 且 body.ok === true：按钮变绿 + 状态 "✓ 可用"
    //   失败 → body.ok === false 或 HTTP 4xx/5xx：alert(body.reason || "HTTP <status>")
    //   网络异常 → alert("网络错误：<msg>")，不崩
    // 测试中按钮禁用 + 文案 "测试中…"（防连点）；结束后按钮恢复常态。
    // 状态元素按 svc-{slot}-test-btn / svc-{slot}-test-label / svc-{slot}-test-status
    // 命名（summary 现有 svc-summary-test-* 保持兼容）。agent/embedding 槽位是
    // provider 下拉（api_base 可能空）——照常透传，探活/鉴权交给后端，前端不特判。
    // XSS: 不使用 innerHTML；reason 经 alert() 展示、状态经 textContent 赋值，
    // 均无注入面（与 radio_silence.js 同一安全基线）。
    async function testSlot(slot, cfg) {
        const btn = document.getElementById('svc-' + slot + '-test-btn');
        if (!btn) return;
        const label = document.getElementById('svc-' + slot + '-test-label');
        const status = document.getElementById('svc-' + slot + '-test-status');
        const conf = cfg || {};
        const payload = {
            slot: slot,
            api_base: conf.api_base || '',
            model: conf.model || '',
            api_key: conf.api_key || '',
        };
        const OK_COLOR = '#3fb950';
        const restore = function () {
            btn.disabled = false;
            btn.style.borderColor = '';
            btn.style.color = '';
            if (label) label.textContent = 'Test';
            if (status) {
                status.textContent = '';
                status.style.color = '';
            }
        };
        // 测试中：禁用 + 文案（防连点）
        btn.disabled = true;
        if (label) label.textContent = '测试中…';
        if (status) { status.textContent = ''; status.style.color = ''; }
        let reason = '';
        try {
            const r = await fetch('/api/services/test', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            let data = null;
            try { data = await r.json(); } catch (_e) { data = null; }
            if (r.ok && data && data.ok === true) {
                // 成功：按钮变绿 + 旁边 "✓ 可用"
                btn.style.borderColor = OK_COLOR;
                btn.style.color = OK_COLOR;
                if (label) label.textContent = 'Test';
                if (status) {
                    status.textContent = '✓ 可用';
                    status.style.color = OK_COLOR;
                }
                btn.disabled = false;
                return;
            }
            reason = (data && data.reason) ? String(data.reason) : ('HTTP ' + r.status);
        } catch (e) {
            reason = '网络错误：' + (e && e.message ? e.message : String(e));
        }
        restore();
        alert(reason);
    }

    // N9 兼容入口：summary 槽位 Test（读表单当前值），等价 testSlot('summary', ...)。
    function testSummary() {
        return testSlot('summary', readForm().summary);
    }

    window.JoyConfig = {
        SERVICES,
        setBadge,
        readForm,
        writeForm,
        load,
        probe,
        save,
        wireSummaryProvider,
        wireSegProvider,
        testSlot,
        testSummary,
    };
})();
