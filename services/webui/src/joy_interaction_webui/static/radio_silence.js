'use strict';

// radio_silence.js — 无线电静默 (Radio Silence) 前端控制
// Spec: doc/specs/draft-radio-silence.md (§3 设置项 / §4 组合矩阵 / §7 前端落点)
//
// IIFE namespace mounted on window.JoyRadioSilence (D-033 pattern, matches
// config_services.js / status_poll.js). Pure static JS, no framework.
//
// 职责:
//   1. settingsModal 内新增「无线电静默」设置面板（5 项 + Save）。
//   2. 全局组合键监听：切换式进出静默（按一次进，再按出）；输入框聚焦时豁免。
//   3. 角标状态占位 #silenceStatus（完整弹窗是后续任务，本轮只翻角标 + 轻提示）。
//
// 后端契约（由 joyai-backend 实现）:
//   GET  /api/live/silence  → { suppressed, settings, source?, hint_pending?,
//                                hint_count?, wake_pending? }
//      settings 恒为完整 7 键（含 hotkey）。
//      hint_pending 语义 = "T1 尚未触发"（suppressed && hint_enabled && !hint_fired，
//      true 表示还没到 15min）——**不能**拿它判断"T1 已触发"。
//      T1 已触发的正确信号是 hint_count（webinfer 一次性递增，触发时 +1）：
//      前端轮询跟踪 hint_count 递增才弹一次 toast（edge-trigger），初始加载记
//      baseline 不弹；GET 本地快照（source:local）缺 hint_count 时沿用旧值容错。
//   POST /api/live/silence  → body { suppressed? , settings? , kws_event? }
//      后端偶发返回 5xx：save 时设置已保存（乐观更新正确）；toggle 时切换可能
//      未生效——前端优先用 5xx 响应体 suppressed（真实值）更新本地 state，零闪烁，
//      读不到才回退乐观值，5s 轮询兜底校正；仅网络异常/4xx 判真失败。
//      hotkey 格式: "Ctrl+Shift+S"（修饰键+修饰键+字母，'+' 连接）
//
// XSS 说明: 本模块不使用 innerHTML；所有动态文本经 textContent / .title
// 属性赋值（安全）；面板说明文字全部为静态 HTML 文本。

(function () {
    'use strict';

    const API_PATH = '/api/live/silence';

    // Spec §3 默认值（与后端默认一致；后端接口未就绪时作为离线兜底）。
    const DEFAULTS = {
        hotkey: 'Ctrl+Shift+S',
        asr_enabled: false,
        kws_enabled: true,
        timeout_hint_enabled: true,
        timeout_hint_minutes: 15,
        auto_wake_enabled: false,
        auto_wake_minutes: 30,
    };

    // 角标轮询周期（GET 只读，保持角标诚实反映后端状态；失败静默不刷屏）。
    const POLL_MS = 5000;

    const state = {
        suppressed: false,
        settings: Object.assign({}, DEFAULTS),
        ready: false, // 首次成功读到后端状态后为 true（区分"未知"与"关"）
        // T1 超时提示跟踪：hint_count 递增才弹一次 toast（edge-trigger）。
        // lastHintCount=null 表示尚未建立 baseline（首次加载不弹）。
        hintCount: 0,
        lastHintCount: null,
        pollTimer: null,
    };

    // ---------- 元素辅助 ----------

    function el(id) {
        return document.getElementById(id);
    }

    // ---------- 组合键 ----------

    // 把 "Ctrl+Shift+S" 归一化为匹配用对象 { ctrl, shift, alt, meta, key }。
    // 支持常见别名（Ctrl/Control、Cmd/Command/Meta/Win）。未知部分忽略。
    function parseHotkey(raw) {
        const parts = String(raw || '')
            .split('+')
            .map(function (p) { return p.trim().toLowerCase(); })
            .filter(Boolean);
        const spec = { ctrl: false, shift: false, alt: false, meta: false, key: '' };
        parts.forEach(function (p) {
            if (p === 'ctrl' || p === 'control') spec.ctrl = true;
            else if (p === 'shift') spec.shift = true;
            else if (p === 'alt' || p === 'option') spec.alt = true;
            else if (p === 'meta' || p === 'cmd' || p === 'command' || p === 'win' || p === 'super') spec.meta = true;
            else if (p.length === 1 && /[a-z0-9]/.test(p)) spec.key = p; // 单字母/数字
            else if (p.length > 1) spec.key = p; // 具名键（如 F1）原样保留
        });
        return spec;
    }

    // 归一化后的展示文本（用于角标 title 提示）。
    function formatHotkey(spec) {
        const parts = [];
        if (spec.ctrl) parts.push('Ctrl');
        if (spec.shift) parts.push('Shift');
        if (spec.alt) parts.push('Alt');
        if (spec.meta) parts.push('Meta');
        if (spec.key) parts.push(spec.key.toUpperCase());
        return parts.join('+');
    }

    function hotkeyMatches(e, spec) {
        if (!spec || !spec.key) return false;
        return e.ctrlKey === spec.ctrl &&
            e.shiftKey === spec.shift &&
            e.altKey === spec.alt &&
            e.metaKey === spec.meta &&
            String(e.key || '').toLowerCase() === spec.key;
    }

    // 输入框/文本域/下拉/可编辑区域聚焦时不触发（避免打字误触组合键）。
    function isEditableTarget(t) {
        if (!t) return false;
        const tag = (t.tagName || '').toUpperCase();
        if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true;
        return !!t.isContentEditable;
    }

    // ---------- 表单 <-> state ----------

    function readForm() {
        const s = {
            hotkey: (el('silenceHotkey') || {}).value || DEFAULTS.hotkey,
            asr_enabled: !!(el('silenceAsrToggle') || {}).checked,
            kws_enabled: !!(el('silenceKwsToggle') || {}).checked,
            timeout_hint_enabled: !!(el('silenceTimeoutHintToggle') || {}).checked,
            timeout_hint_minutes: parseInt((el('silenceTimeoutHintMinutes') || {}).value, 10),
            auto_wake_enabled: !!(el('silenceAutoWakeToggle') || {}).checked,
            auto_wake_minutes: parseInt((el('silenceAutoWakeMinutes') || {}).value, 10),
        };
        if (!Number.isFinite(s.timeout_hint_minutes) || s.timeout_hint_minutes < 1) {
            s.timeout_hint_minutes = DEFAULTS.timeout_hint_minutes;
        }
        if (!Number.isFinite(s.auto_wake_minutes) || s.auto_wake_minutes < 1) {
            s.auto_wake_minutes = DEFAULTS.auto_wake_minutes;
        }
        return s;
    }

    function writeForm(s) {
        const setVal = function (id, v) { const x = el(id); if (x) x.value = v; };
        const setChecked = function (id, v) { const x = el(id); if (x) x.checked = !!v; };
        setVal('silenceHotkey', s.hotkey || DEFAULTS.hotkey);
        setChecked('silenceAsrToggle', s.asr_enabled);
        setChecked('silenceKwsToggle', s.kws_enabled);
        setChecked('silenceTimeoutHintToggle', s.timeout_hint_enabled);
        setVal('silenceTimeoutHintMinutes', s.timeout_hint_minutes);
        setChecked('silenceAutoWakeToggle', s.auto_wake_enabled);
        setVal('silenceAutoWakeMinutes', s.auto_wake_minutes);
        // 分钟输入框随开关置灰（开关关时该值不生效，避免误导编辑）。
        syncMinutesDisabled();
    }

    // 分钟输入框禁用态与对应开关联动。
    function syncMinutesDisabled() {
        [
            ['silenceTimeoutHintToggle', 'silenceTimeoutHintMinutes'],
            ['silenceAutoWakeToggle', 'silenceAutoWakeMinutes'],
        ].forEach(function (pair) {
            const t = el(pair[0]);
            const m = el(pair[1]);
            if (t && m) m.disabled = !t.checked;
        });
    }

    // ---------- 角标（占位） ----------

    function renderBadge() {
        const b = el('silenceStatus');
        if (!b) return;
        if (!state.ready) {
            b.className = 'status-badge silence-unknown';
            b.textContent = '静默 ?';
            b.title = '无线电静默状态未知（后端接口未就绪）';
            return;
        }
        const hint = '按 ' + formatHotkey(parseHotkey(state.settings.hotkey)) + ' 切换';
        if (state.suppressed) {
            b.className = 'status-badge silence-on';
            b.textContent = '静默中';
            b.title = '无线电静默已开启：不推理不播报，视觉继续。' + hint;
        } else {
            b.className = 'status-badge silence-off';
            b.textContent = '静默关';
            b.title = '无线电静默已关闭。' + hint;
        }
    }

    // ---------- 轻提示（无框架；避免 alert 打断） ----------

    let _toastEl = null;
    let _toastTimer = null;

    function showFeedback(msg, isError) {
        if (!_toastEl) {
            _toastEl = document.createElement('div');
            _toastEl.id = 'silenceToast';
            _toastEl.setAttribute('role', 'status');
            _toastEl.setAttribute('aria-live', 'polite');
            _toastEl.style.cssText =
                'position:fixed;left:50%;bottom:24px;transform:translateX(-50%);' +
                'z-index:10000;padding:10px 16px;border-radius:8px;font-size:13px;' +
                'background:var(--card-bg,#1e1e24);color:var(--text-primary,#eee);' +
                'border:1px solid var(--border-color,#333);' +
                'box-shadow:0 4px 16px rgba(0,0,0,.35);' +
                'opacity:0;transition:opacity .2s;pointer-events:none;max-width:80vw;';
            document.body.appendChild(_toastEl);
        }
        // textContent 赋值——不做任何 HTML 注入（XSS 闸口）。
        _toastEl.textContent = msg;
        _toastEl.style.borderColor = isError ? 'var(--error-color,#e5484d)' : 'var(--border-color,#333)';
        _toastEl.style.opacity = '1';
        if (_toastTimer) clearTimeout(_toastTimer);
        _toastTimer = setTimeout(function () {
            if (_toastEl) _toastEl.style.opacity = '0';
        }, 2600);
    }

    // ---------- API ----------

    // opts.silent=true 时失败不 console（轮询用，避免接口未就绪时刷屏）；
    // opts.skipForm=true 时只更新 state/角标、不写表单（轮询用——避免覆盖用户
    // 正在 settingsModal 里编辑但尚未保存的值；表单回填由打开 modal 时调用）。
    async function load(opts) {
        const silent = !!(opts && opts.silent);
        const skipForm = !!(opts && opts.skipForm);
        try {
            const r = await fetch(API_PATH, { cache: 'no-store' });
            if (!r.ok) throw new Error('HTTP ' + r.status);
            const data = await r.json();
            state.suppressed = !!data.suppressed;
            state.settings = Object.assign({}, DEFAULTS, data.settings || {});
            state.ready = true;
            // T1 超时提示：hint_pending 语义是"尚未触发"（true=还没到 15min），
            // 不能拿它判断"已触发"；正确信号是 hint_count（T1 触发时 webinfer
            // 一次性 +1）。这里跟踪 hint_count 递增才弹一次 toast（edge-trigger）：
            //   1) lastHintCount===null（首次加载）只记 baseline，不弹；
            //   2) hintCount > lastHintCount → 弹「仍在静默中」，防重复；
            //   3) source:local 快照（webinfer 不可达）的 hint_count 恒为 0 占位，
            //      跳过更新——否则会重置 lastHintCount=0，恢复后重复弹一次
            //      （reviewer follow-up nits，任务 #16）。
            const hasHintCount = typeof data.hint_count === 'number' && data.source !== 'local';
            if (hasHintCount) {
                const hintCount = data.hint_count;
                if (state.lastHintCount !== null && hintCount > state.lastHintCount) {
                    showFeedback('仍在静默中（静默超时提示）');
                }
                state.lastHintCount = hintCount;
                state.hintCount = hintCount;
            }
        } catch (e) {
            // 后端接口未就绪 → 离线默认值；角标显示"未知"。
            state.suppressed = false;
            state.settings = Object.assign({}, DEFAULTS);
            state.ready = false;
            if (!silent) console.warn('load /api/live/silence:', e);
        }
        if (!skipForm) writeForm(state.settings);
        renderBadge();
        return state;
    }

    // 后端行为：POST 偶发返回 5xx 但设置其实已保存。这里把 5xx 视为"请求已到达、
    // 可能已生效"，乐观更新本地 state + 中性提示，5s 轮询会校正真实状态；
    // 只有网络异常/4xx 才判真失败（红脸提示）。
    function postAccepted(r) {
        return r.ok || r.status >= 500;
    }

    async function save() {
        const settings = readForm();
        try {
            const r = await fetch(API_PATH, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ settings: settings }),
            });
            if (!postAccepted(r)) throw new Error('HTTP ' + r.status);
            state.settings = Object.assign({}, state.settings, settings);
            state.ready = true;
            renderBadge();
            showFeedback(r.ok ? '无线电静默设置已保存' : '设置已提交（后端响应异常，稍后自动校正）');
            return r.ok;
        } catch (e) {
            console.warn('save /api/live/silence:', e);
            showFeedback('保存失败：' + e, true);
            return false;
        }
    }

    // 切换式进出静默（组合键 / 后续弹窗按钮复用）。
    async function toggle() {
        const next = !state.suppressed;
        const prev = state.suppressed;
        try {
            const r = await fetch(API_PATH, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ suppressed: next }),
            });
            if (!postAccepted(r)) throw new Error('HTTP ' + r.status);
            // 后端 5xx 时 toggle 可能未生效：优先用响应体 suppressed（真实值）
            // 更新本地 state，避免"乐观置 next + 轮询回正"造成 5s 角标闪烁；
            // 读不到响应体则回退 next（轮询仍会校正）。
            let effective = next;
            if (!r.ok) {
                try {
                    const body = await r.json();
                    if (body && typeof body.suppressed === 'boolean') effective = body.suppressed;
                } catch (_) { /* 无响应体 → 保持 next */ }
            }
            state.suppressed = effective;
            state.ready = true;
            renderBadge();
            if (r.ok) {
                showFeedback(effective ? '无线电静默已开启' : '无线电静默已退出');
            } else if (effective === prev) {
                showFeedback('切换未生效（后端异常），当前仍处于' + (effective ? '静默中' : '静默关'), true);
            } else {
                showFeedback('切换已提交（后端响应异常），状态已更新');
            }
        } catch (e) {
            console.warn('toggle /api/live/silence:', e);
            showFeedback('切换失败：' + e, true);
        }
    }

    // ---------- 事件接线 ----------

    function handleKeydown(e) {
        // 输入框聚焦豁免：设置面板组合键输入框、prompt 编辑器、聊天框等
        // 均不触发，避免打字误触。
        if (isEditableTarget(e.target)) return;
        const spec = parseHotkey(state.settings.hotkey);
        if (!hotkeyMatches(e, spec)) return;
        // Ctrl+Shift+S 是浏览器"另存为"快捷键，命中后必须拦截。
        e.preventDefault();
        e.stopPropagation();
        toggle();
    }

    function startPoll() {
        if (state.pollTimer) return;
        state.pollTimer = setInterval(function () {
            // skipForm: 轮询只同步 state/角标，不覆盖用户正在编辑的表单。
            load({ silent: true, skipForm: true });
        }, POLL_MS);
    }

    function stopPoll() {
        if (state.pollTimer) {
            clearInterval(state.pollTimer);
            state.pollTimer = null;
        }
    }

    function init() {
        const saveBtn = el('silenceSaveBtn');
        if (saveBtn) saveBtn.addEventListener('click', save);
        // 分钟输入框随开关置灰的联动（change 事件）。
        [
            ['silenceTimeoutHintToggle', 'silenceTimeoutHintMinutes'],
            ['silenceAutoWakeToggle', 'silenceAutoWakeMinutes'],
        ].forEach(function (pair) {
            const t = el(pair[0]);
            const m = el(pair[1]);
            if (t && m) t.addEventListener('change', syncMinutesDisabled);
        });
        // capture=true：优先于页面内其它 keydown 处理。
        window.addEventListener('keydown', handleKeydown, true);
        startPoll();
        return load();
    }

    window.JoyRadioSilence = {
        DEFAULTS,
        state,
        load,
        save,
        toggle,
        parseHotkey,
        formatHotkey,
        readForm,
        writeForm,
        renderBadge,
        init,
        startPoll,
        stopPoll,
    };
})();
