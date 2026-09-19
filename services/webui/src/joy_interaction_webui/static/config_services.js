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

    // 2026-09-18（用户要求 C1）：保存失败时展示后端返回的结构化错误。
    // 原实现是 `alert('Save failed: ' + e)`，其中 e 只是 'HTTP 400'
    // —— 而后端 admin_endpoints.py:134-142 明确返回 {error, slot, field, reason}，
    // 这份信息被丢弃，用户无从知道是哪个槽、哪个字段错。这是「看不懂/不知道怎么保存」
    // 的直接原因之一。
    function _describeSaveError(status, body) {
        if (!body || typeof body !== 'object') return 'HTTP ' + status;
        const parts = [];
        if (body.slot) parts.push('槽位 ' + body.slot);
        if (body.field) parts.push('字段 ' + body.field);
        if (body.reason) parts.push(body.reason);
        const detail = parts.length ? parts.join(' · ') : (body.error || '');
        return (detail ? detail + '  ' : '') + '(HTTP ' + status + ')';
    }

    async function save() {
        const cfg = readForm();
        try {
            const r = await fetch('/api/services/config', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(cfg),
            });
            if (!r.ok) {
                // 尽量读出后端的结构化错误体（失败不影响主流程）
                let body = null;
                try { body = await r.json(); } catch (_e) { /* 非 JSON 响应 */ }
                throw new Error(_describeSaveError(r.status, body));
            }
            await probe();
        } catch (e) {
            alert('保存失败：' + (e && e.message ? e.message : e));
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
    //
    // 2026-09-18 (A2-b) —— 连接列表从 localStorage 迁到【后端持久化】：
    //   原先：localStorage['joyai.providers.<slot>']，换浏览器/清缓存即丢失，
    //         且 api_key 不存（每次应用预设都要重填）。
    //   现在：GET/PUT /api/connections，落盘 config/connections.json（chmod 0600 +
    //         gitignore，api_key 明文，与 services.json 同一安全基线 E3）。
    //         前端持有内存镜像 _connCache，写操作整体 PUT（后端整体替换语义）。
    //   迁移：首次加载时若后端为空、而 localStorage 有旧数据，自动导入一次
    //         （只导入 name/api_base/model/provider —— 旧格式本就不含 api_key）。
    const SEG_SLOTS = ['llm', 'summary', 'tts', 'asr', 'agent', 'embedding'];
    const LEGACY_LS_KEY = function (slot) { return 'joyai.providers.' + slot; };
    const CONNECTIONS_API = '/api/connections';

    //: 后端返回的连接列表镜像：{ slot: [ {id,name,api_base,model,provider,api_key_set} ] }
    let _connCache = null;
    let _connLoading = null;

    function _normalizeConnections(payload) {
        const out = {};
        const conns = (payload && payload.connections) || {};
        SEG_SLOTS.forEach(function (slot) {
            out[slot] = Array.isArray(conns[slot]) ? conns[slot] : [];
        });
        return out;
    }

    function _readLegacyLocal(slot) {
        try {
            const raw = localStorage.getItem(LEGACY_LS_KEY(slot));
            const arr = raw ? JSON.parse(raw) : [];
            return Array.isArray(arr) ? arr : [];
        } catch (_e) { return []; }
    }

    // 旧 localStorage 数据一次性导入后端（仅在后者为空时）
    async function _migrateLegacyIfNeeded(conns) {
        const empty = SEG_SLOTS.every(function (s) { return !(conns[s] || []).length; });
        if (!empty) return false;
        const merged = {};
        let any = false;
        SEG_SLOTS.forEach(function (slot) {
            const rows = _readLegacyLocal(slot);
            if (!rows.length) return;
            any = true;
            merged[slot] = rows.map(function (r, i) {
                return {
                    id: 'legacy-' + slot + '-' + i,
                    name: (r && r.name) || ('预设 ' + (i + 1)),
                    api_base: (r && r.api_base) || '',
                    model: (r && r.model) || '',
                    provider: (r && r.provider) || '',
                };
            });
        });
        if (!any) return false;
        await _putConnections(merged);
        return true;
    }

    async function loadConnections() {
        if (_connCache) return _connCache;
        if (_connLoading) return _connLoading;
        _connLoading = (async function () {
            let conns = {};
            try {
                const r = await fetch(CONNECTIONS_API, { cache: 'no-store' });
                if (!r.ok) throw new Error('HTTP ' + r.status);
                conns = _normalizeConnections(await r.json());
            } catch (_e) {
                // 后端不可达：退化为空表（UI 仍可用，只是没有已存连接）
                conns = _normalizeConnections(null);
            }
            try {
                const migrated = await _migrateLegacyIfNeeded(conns);
                if (migrated) {
                    const r2 = await fetch(CONNECTIONS_API, { cache: 'no-store' });
                    if (r2.ok) conns = _normalizeConnections(await r2.json());
                }
            } catch (_e) { /* 迁移失败不阻断：用户可重新保存 */ }
            _connCache = conns;
            _connLoading = null;
            return conns;
        })();
        return _connLoading;
    }

    async function _putConnections(conns) {
        const r = await fetch(CONNECTIONS_API, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ connections: conns }),
        });
        if (!r.ok) {
            let reason = 'HTTP ' + r.status;
            try { const b = await r.json(); reason = b.error || reason; } catch (_e) {}
            throw new Error(reason);
        }
        _connCache = _normalizeConnections(await r.json());
        return _connCache;
    }

    /** 取某槽位的连接列表（同步；调用前需先 ensureConnectionsLoaded()）。 */
    function _pRead(slot) {
        if (!_connCache) return [];
        return _connCache[slot] || [];
    }

    /** 写某槽位的连接列表（异步 PUT 后端）。 */
    async function _pWrite(slot, arr) {
        const next = {};
        SEG_SLOTS.forEach(function (s) { next[s] = _pRead(s); });
        next[slot] = arr;
        await _putConnections(next);
    }
    /** 生成连接 id。用 crypto.randomUUID（现代浏览器均有），退化到时间戳+随机。 */
    function _newConnId(slot) {
        try {
            if (window.crypto && typeof window.crypto.randomUUID === 'function') {
                return window.crypto.randomUUID();
            }
        } catch (_e) { /* 非安全上下文：退化 */ }
        return slot + '-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 8);
    }

    function _pMsg(slot, text, isErr) {
        const el = document.getElementById('svc-' + slot + '-provider-msg');
        if (!el) return;
        el.textContent = text;
        el.className = 'provider-msg ' + (isErr ? 'err' : 'ok');
        // 2026-09-18：错误提示停留久一点（4s），成功仍 2.2s —— 出错时用户需要时间读完
        setTimeout(function () { el.textContent = ''; el.className = 'provider-msg'; }, isErr ? 4000 : 2200);
    }
    // 2026-09-18（方案 X）：_pFillPick 已删除 —— 它服务于旧的「下拉历史」控件
    // （svc-<slot>-provider-pick），该控件已被连接列表替换。列表渲染见
    // wireSegProvider 内的 renderConnList（DOM 构造 + textContent，同样不用 innerHTML）。
    function _pApply(slot, idOrName) {
        const arr = _pRead(slot);
        const p = arr.filter(function (x) {
            return x && (x.id === idOrName || x.name === idOrName);
        })[0];
        if (!p) return;
        const base = document.getElementById('svc-' + slot + '-api-base');
        const model = document.getElementById('svc-' + slot + '-model');
        const prov = document.getElementById('svc-' + slot + '-provider');
        const key = document.getElementById('svc-' + slot + '-api-key');
        if (base && p.api_base != null) base.value = p.api_base;
        if (model && p.model != null) model.value = p.model;
        if (prov && p.provider != null) prov.value = p.provider; // provider select → readForm still binds
        // 2026-09-18：API Key 的处理分两种情况 ——
        //   A2-b 之后连接里**可以**存 api_key（后端加密基线与 services.json 一致）。
        //   若该连接存了 key，由于 GET 只回 api_key_set 布尔值（不回明文），
        //   前端拿不到明文 → 保持「需重填」提示，但不清空用户当前已填的值，
        //   避免把刚填好的 key 抹掉。
        //   若未存 key，则沿用原逻辑：清空，防止换服务商后旧 key 误用导致 401。
        if (key) {
            if (!p.api_key_set) key.value = '';
        }
        const nameEl = document.getElementById('svc-' + slot + '-provider-name');
        if (nameEl) nameEl.value = p.name;
        const hint = p.api_key_set
            ? '（该连接已保存密钥，如需更换请重新填写）'
            : (key ? '（API 密钥已清空，请重填）' : '');
        _pMsg(slot, '已应用：' + p.name + hint, false);
        if (save) save(); // persist via existing PUT /api/services/config
    }
    //: 各槽位的连接列表渲染函数（由 wireSegProvider 注册，loadConnections 完成后调用）
    const _listRenderers = {};

    function wireSegProvider() {
        // 2026-09-18 (A2-b)：连接列表改由后端提供，需先 load 再渲染。
        // loadConnections() 内部有缓存（_connCache），重复调用只发一次请求。
        loadConnections().then(function () {
            Object.keys(_listRenderers).forEach(function (slot) {
                try { _listRenderers[slot](); } catch (_e) { /* 单槽渲染失败不影响其它 */ }
            });
        }).catch(function () { /* 后端不可达：列表留空，UI 仍可用 */ });

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
            // ================================================================
            // 2026-09-18（方案 X）：连接列表 —— 替换原「名称 + 加号 + 下拉」三件套。
            //   默认收起；「已保存的连接 (N) ▾」点开显示每项一行（应用 / 删除）。
            //   「保存当前为连接」→ 行内展开名称输入（不用原生 prompt，见 ADR-0020 §二.2）。
            // 元素：svc-<slot>-conn-toggle / -conn-count / -conn-save / -conn-list /
            //       -conn-name-row / -conn-name / -conn-confirm / -conn-cancel
            // ================================================================
            const toggle = document.getElementById('svc-' + slot + '-conn-toggle');
            const listEl = document.getElementById('svc-' + slot + '-conn-list');
            const countEl = document.getElementById('svc-' + slot + '-conn-count');
            const saveBtn2 = document.getElementById('svc-' + slot + '-conn-save');
            const nameRow = document.getElementById('svc-' + slot + '-conn-name-row');
            const nameInput = document.getElementById('svc-' + slot + '-conn-name');
            const confirmBtn = document.getElementById('svc-' + slot + '-conn-confirm');
            const cancelBtn = document.getElementById('svc-' + slot + '-conn-cancel');

            /** 重建连接列表 DOM（DOM 构造 + textContent，不用 innerHTML）。 */
            function renderConnList() {
                if (!listEl) return;
                const arr = _pRead(slot);
                if (countEl) countEl.textContent = String(arr.length);
                while (listEl.firstChild) listEl.removeChild(listEl.firstChild);
                if (!arr.length) {
                    const empty = document.createElement('div');
                    empty.className = 'conn-empty';
                    empty.textContent = '还没有保存的连接。填好下面的地址/密钥/模型后，点「保存当前为连接」。';
                    listEl.appendChild(empty);
                    return;
                }
                arr.forEach(function (c) {
                    const row = document.createElement('div');
                    row.className = 'conn-item';
                    row.setAttribute('data-conn-id', c.id || '');

                    const dot = document.createElement('span');
                    dot.className = 'conn-dot';
                    row.appendChild(dot);

                    const meta = document.createElement('div');
                    meta.className = 'conn-meta';
                    const nm = document.createElement('div');
                    nm.className = 'conn-name';
                    nm.textContent = c.name || '(未命名)';
                    const sub = document.createElement('div');
                    sub.className = 'conn-sub';
                    const bits = [];
                    if (c.provider) bits.push(c.provider);
                    if (c.api_base) bits.push(c.api_base);
                    if (c.model) bits.push(c.model);
                    if (c.api_key_set) bits.push('已存密钥');
                    sub.textContent = bits.join(' · ');
                    meta.appendChild(nm);
                    meta.appendChild(sub);
                    row.appendChild(meta);

                    const actions = document.createElement('div');
                    actions.className = 'conn-actions';
                    const applyBtn = document.createElement('button');
                    applyBtn.type = 'button';
                    applyBtn.className = 'conn-act';
                    applyBtn.textContent = '应用';
                    applyBtn.addEventListener('click', function () { _pApply(slot, c.id); });
                    const delBtn2 = document.createElement('button');
                    delBtn2.type = 'button';
                    delBtn2.className = 'conn-act danger';
                    delBtn2.textContent = '删除';
                    delBtn2.addEventListener('click', async function () {
                        const next = _pRead(slot).filter(function (x) { return !(x && x.id === c.id); });
                        try {
                            await _pWrite(slot, next);
                            renderConnList();
                            _pMsg(slot, '已删除：' + (c.name || c.id), false);
                        } catch (e) {
                            _pMsg(slot, '删除失败：' + (e && e.message ? e.message : e), true);
                        }
                    });
                    actions.appendChild(applyBtn);
                    actions.appendChild(delBtn2);
                    row.appendChild(actions);
                    listEl.appendChild(row);
                });
            }

            function setListOpen(open) {
                if (!listEl) return;
                listEl.hidden = !open;
                if (toggle) {
                    toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
                    toggle.classList.toggle('open', open);
                }
                if (open) renderConnList();
            }

            if (toggle) {
                toggle.addEventListener('click', function () {
                    setListOpen(listEl ? listEl.hidden : false);
                });
            }

            // 「保存当前为连接」：展开行内名称输入（不用原生 prompt）
            if (saveBtn2) {
                saveBtn2.addEventListener('click', function () {
                    if (!nameRow) return;
                    // 从当前表单值预填一个建议名称（provider 或 host）
                    if (nameInput) {
                        const prov = document.getElementById('svc-' + slot + '-provider');
                        const base = document.getElementById('svc-' + slot + '-api-base');
                        let guess = '';
                        if (prov && prov.value) guess = prov.value;
                        else if (base && base.value) {
                            try { guess = new URL(base.value).hostname; } catch (_e) { guess = ''; }
                        }
                        nameInput.value = guess || '';
                    }
                    nameRow.hidden = false;
                    if (nameInput) nameInput.focus();
                });
            }
            if (confirmBtn) {
                confirmBtn.addEventListener('click', async function () {
                    const name = nameInput ? nameInput.value.trim() : '';
                    if (!name) { _pMsg(slot, '请先填写连接名称', true); return; }
                    const base = document.getElementById('svc-' + slot + '-api-base');
                    const model = document.getElementById('svc-' + slot + '-model');
                    const prov = document.getElementById('svc-' + slot + '-provider');
                    const key = document.getElementById('svc-' + slot + '-api-key');
                    const arr = _pRead(slot).slice();
                    const idx = arr.findIndex(function (x) { return x && x.name === name; });
                    const existed = idx >= 0;
                    const rec = existed ? Object.assign({}, arr[idx]) : { id: _newConnId(slot) };
                    rec.name = name;
                    rec.api_base = base ? base.value : '';
                    if (model) rec.model = model.value;
                    if (prov) rec.provider = prov.value;
                    // 只有用户填了 key 才写入（留空 = 保持已存密钥，避免误抹）
                    if (key && key.value) rec.api_key = key.value;
                    if (existed) arr[idx] = rec; else arr.push(rec);
                    try {
                        await _pWrite(slot, arr);
                        if (nameRow) nameRow.hidden = true;
                        setListOpen(true);      // 保存后自动展开，让用户看到结果
                        _pMsg(slot, (existed ? '已更新连接：' : '已保存连接：') + name, false);
                    } catch (e) {
                        _pMsg(slot, '保存失败：' + (e && e.message ? e.message : e), true);
                    }
                });
            }
            if (cancelBtn) {
                cancelBtn.addEventListener('click', function () {
                    if (nameRow) nameRow.hidden = true;
                    if (nameInput) nameInput.value = '';
                });
            }
            if (nameInput) {
                nameInput.addEventListener('keydown', function (e) {
                    if (e.key === 'Enter') { e.preventDefault(); if (confirmBtn) confirmBtn.click(); }
                    if (e.key === 'Escape') { if (cancelBtn) cancelBtn.click(); }
                });
            }
            // 供 loadConnections() 完成后刷新列表
            _listRenderers[slot] = renderConnList;
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
            if (label) label.textContent = window.JoyI18n ? window.JoyI18n.localizeUiString('Test') : 'Test';
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
                if (label) label.textContent = window.JoyI18n ? window.JoyI18n.localizeUiString('Test') : 'Test';
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

    // ========================================================================
    // 2026-09-18 新增：「获取模型」——从上游 /models 拉取候选，填入 <datalist>。
    // 用户诉求：「接口功能需有类似上游探测的实现，比如获取模型、探测是否存活」
    // 后端：POST /api/services/list-models {api_base, api_key} → {ok, models[], status, reason}
    //       该端点纯探测，不写任何配置（与 /api/services/test 同一约定）。
    // 交互：成功后把候选写入 datalist（用户仍可手填，不锁死）；
    //       失败则把原因显示在按钮旁的提示里，并给出可操作建议。
    // ========================================================================
    function _modelMsg(slot, text, isErr) {
        const el = document.getElementById('svc-' + slot + '-test-status');
        if (!el) return;
        el.textContent = text;
        el.style.color = isErr ? 'var(--error-color, #e5484d)' : 'var(--ok-color, #3fb950)';
    }

    async function fetchModels(slot) {
        const btn = document.getElementById('svc-' + slot + '-model-fetch');
        const input = document.getElementById('svc-' + slot + '-model');
        const list = document.getElementById('svc-' + slot + '-model-options');
        if (!btn || !input || !list) return;
        const base = (document.getElementById('svc-' + slot + '-api-base') || {}).value || '';
        const key = (document.getElementById('svc-' + slot + '-api-key') || {}).value || '';
        if (!base.trim()) {
            _modelMsg(slot, '请先填写 API 基础地址', true);
            return;
        }
        const origLabel = btn.querySelector('span') ? btn.querySelector('span').textContent : '';
        btn.disabled = true;
        if (btn.querySelector('span')) btn.querySelector('span').textContent = '获取中…';
        _modelMsg(slot, '正在向上游请求模型列表…', false);
        try {
            const r = await fetch('/api/services/list-models', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ api_base: base.trim(), api_key: key }),
            });
            const data = await r.json().catch(function () { return null; });
            if (!r.ok) {
                const reason = (data && (data.error || data.reason)) || ('HTTP ' + r.status);
                _modelMsg(slot, '获取失败：' + reason, true);
                return;
            }
            const models = (data && data.models) || [];
            // 重建 datalist（DOM 构造，不用 innerHTML —— 与 _pFillPick 同一安全基线）
            while (list.firstChild) list.removeChild(list.firstChild);
            models.forEach(function (id) {
                const opt = document.createElement('option');
                opt.value = id;
                list.appendChild(opt);
            });
            if (models.length) {
                _modelMsg(slot, '已获取 ' + models.length + ' 个模型，点击输入框选择', false);
            } else {
                const why = (data && data.reason) || '上游未返回模型列表';
                _modelMsg(slot, why + '（可手动填写模型名）', true);
            }
        } catch (e) {
            _modelMsg(slot, '请求失败：' + (e && e.message ? e.message : String(e)), true);
        } finally {
            btn.disabled = false;
            if (btn.querySelector('span')) btn.querySelector('span').textContent = origLabel || '获取模型';
        }
    }

    function wireModelFetch() {
        document.querySelectorAll('[data-model-fetch]').forEach(function (btn) {
            const slot = btn.getAttribute('data-model-fetch');
            btn.addEventListener('click', function () { fetchModels(slot); });
        });
    }

    // 2026-09-18（用户要求 B）：service-row 展开/折叠。
    // LLM 与 Summary 默认展开；ASR / Agent / Embedding 默认折叠（HTML 已带 .collapsed）。
    // 折叠态只保留 header + badge —— badge 是唯一始终有信息量的元素，不展开也能看健康度。
    // 纯 class 切换，不持久化（与 data-mode 的现有行为一致）。
    function wireServiceRowCollapse() {
        document.querySelectorAll('#servicesPanel .service-row, #voicePanel .service-row').forEach(function (row) {
            const header = row.querySelector('.service-row-header');
            if (!header) return;
            // 可访问性：header 可聚焦、可键盘操作
            header.setAttribute('role', 'button');
            header.setAttribute('tabindex', '0');
            header.setAttribute('aria-expanded', row.classList.contains('collapsed') ? 'false' : 'true');
            function toggle() {
                const collapsed = row.classList.toggle('collapsed');
                header.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
            }
            header.addEventListener('click', toggle);
            header.addEventListener('keydown', function (e) {
                if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); }
            });
        });
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
        wireServiceRowCollapse,
        fetchModels,
        wireModelFetch,
        loadConnections,
        _putConnections,
        testSlot,
        testSummary,
    };
})();
