// Extracted from index.html — t2 inline-script externalization (2026-09-19).
// v6-lite.19b 增量接线：health/latency 菜单、设置导航 showSettingsPanel、外观手风琴、捕获浮层定位
//
// 纯机械搬迁：本文件正文与 index.html 内联时**逐字节相同**（未 dedent、未改逻辑），
// 与 batch-3 split 的做法一致。以 classic script（非 module / 非 async / 非 defer）
// 在 <script src="./incremental_wiring.js"> 的**原位置**引入，与其余脚本共享同一全局词法环境，
// 因此顶层 const/let 仍可被后续脚本访问，相对执行顺序与搬迁前完全一致。
// 无损性由 t2 的提取脚本自证：重组后与搬迁前的 index.html 逐字节相同。

    (function () {
        var pill = document.getElementById('healthPill');
        var menu = document.getElementById('healthMenu');
        if (pill && menu) {
            pill.addEventListener('click', function (e) { e.stopPropagation(); menu.classList.toggle('open'); });
            document.addEventListener('click', function (e) {
                if (menu.classList.contains('open') && !menu.contains(e.target) && e.target !== pill && !pill.contains(e.target)) {
                    menu.classList.remove('open');
                }
            });
        }
        // 链路指标胶囊（B2 收纳）：点击开合浮层，并按 aria-expanded 反映状态。
        // 与 healthMenu 互斥：同时只留一个下拉，避免顶栏出现两层浮层叠压。
        (function () {
            var lp = document.getElementById('latencyPill');
            var lm = document.getElementById('latencyMenu');
            if (!lp || !lm) return;
            function setOpen(open) {
                lm.classList.toggle('open', open);
                lp.setAttribute('aria-expanded', open ? 'true' : 'false');
            }
            lp.addEventListener('click', function (e) {
                e.stopPropagation();
                var willOpen = !lm.classList.contains('open');
                if (willOpen && menu) menu.classList.remove('open');
                setOpen(willOpen);
            });
            // 浮层内含 GAIN <select>，交互时不能因点击而关闭
            lm.addEventListener('click', function (e) { e.stopPropagation(); });
            document.addEventListener('click', function (e) {
                if (lm.classList.contains('open') && !lm.contains(e.target) && !lp.contains(e.target)) {
                    setOpen(false);
                }
            });
            document.addEventListener('keydown', function (e) {
                if (e.key === 'Escape' && lm.classList.contains('open')) setOpen(false);
            });
        })();
        // 设置左侧导航：面板切换（参考 design/joyai-redesign-preview.html 的 showSection 模型）
        // 点击导航项 → 仅显示对应大类面板，其余隐藏（不再把所有 section 平铺长滚）。
        var nav = document.getElementById('modalNav');
        var PANEL_ROOTS = {
            services: 'servicesPanel',
            // input 面板已于 2026-09-18 删除（用户要求，C2）：连同导航项一并移除，
            // 映射也删掉，避免 showSettingsPanel 循环里留下指向不存在元素的死键。
            wiki: 'wikiPanel',
            voice: 'voicePanel',
            // 2026-09-18：agent 从「模型」分离为独立面板（它不是模型推理服务）
            agent: 'agentPanel',
            memory: 'memoryPanel',
            // api 面板已于 2026-09-18 删除（用户要求）：不再需要映射，
            // 删掉可避免 showSettingsPanel 循环里出现指向不存在元素的死键。
            appearance: 'appearanceSection',
            advanced: 'advancedPanel',
            about: 'aboutFooter'
        };
        function showSettingsPanel(key, scrollId) {
            Object.keys(PANEL_ROOTS).forEach(function (k) {
                var el = document.getElementById(PANEL_ROOTS[k]);
                if (el) el.classList.toggle('cat-hidden', k !== key);
            });
            if (nav) {
                nav.querySelectorAll('.nav-item').forEach(function (n) {
                    n.classList.toggle('active', n.getAttribute('data-panel') === key);
                });
            }
            if (scrollId) {
                var s = document.getElementById(scrollId);
                if (s) {
                    // 确保目标所在的折叠面板（如 servicesConfig）已展开
                    var pc = s.closest('.panel-content.collapsed');
                    if (pc) {
                        pc.classList.remove('collapsed');
                        var pt = document.getElementById(pc.id + 'Toggle');
                        if (pt) pt.classList.remove('collapsed');
                    }
                    s.scrollIntoView({ behavior: 'smooth', block: 'center' });
                    s.classList.add('nav-flash');
                    setTimeout(function () { s.classList.remove('nav-flash'); }, 5000);
                }
            }
        }
        if (nav) {
            nav.querySelectorAll('.nav-item').forEach(function (it) {
                it.addEventListener('click', function () {
                    showSettingsPanel(it.getAttribute('data-panel'), it.getAttribute('data-scroll'));
                });
            });
            // 初始：仅显示「模型」面板（其余收入左侧栏，按需切换）
            showSettingsPanel('services');
        }
        // 对外暴露面板切换入口（2026-09-19，为 status_poll.js 的知识库徽章点击而加）。
        // 为什么必须显式挂全局：本函数声明在上面包裹整段的 `(function(){…})()` 内部，
        // 属于 IIFE 作用域 —— CDP 实测 `typeof showSettingsPanel === 'undefined'`，
        // 外部脚本裸调用只会 ReferenceError（这正是"看起来对、其实静默失效"的陷阱，
        // 与本次清理修掉的 #knowledgeBaseToggle 同类）。挂到 window 后，
        // status_poll.js 可稳定走 showSettingsPanel('wiki') 这一条现代等价路径。
        // 属**加法**变更：不改函数实现、不删除任何东西，原内联调用点全部不变。
        window.JoySettingsNav = window.JoySettingsNav || {};
        window.JoySettingsNav.showSettingsPanel = showSettingsPanel;
        window.showSettingsPanel = showSettingsPanel;

        // 外观面板内的子节使用手风琴：展开一项时自动收起同面板其他项，避免 9 个标题平铺
        (function () {
            var appearanceSection = document.getElementById('appearanceSection');
            if (appearanceSection) {
                appearanceSection.addEventListener('click', function (e) {
                    var title = e.target.closest('.settings-section-title');
                    if (!title) return;
                    var section = title.parentElement;
                    if (!section || !section.classList.contains('settings-section')) return;
                    // inline onclick 已先切换；若当前处于展开状态，收起兄弟节
                    if (!section.classList.contains('collapsed')) {
                        appearanceSection.querySelectorAll('.settings-section').forEach(function (sib) {
                            if (sib !== section) sib.classList.add('collapsed');
                        });
                    }
                });
            }
        })();
        var cam = document.getElementById('camBtn');
        var cap = document.getElementById('captureOverlay');
        // Reparent the capture overlay to <body> so position:fixed resolves
        // against the viewport instead of a transformed ancestor
        // (.chat-prompt-shell / .prompt-editor-inline). Otherwise fixed
        // positioning is trapped by the local containing block and the
        // overlay renders clipped/offset outside the visible area.
        if (cap && cap.parentElement !== document.body) {
            document.body.appendChild(cap);
        }
        function positionCaptureOverlay() {
            if (!cam || !cap) return;
            var rect = cam.getBoundingClientRect();
            var capWidth = 360;
            var pad = 12;
            var left = rect.left + rect.width / 2 - capWidth / 2;
            var maxLeft = window.innerWidth - capWidth - pad;
            if (left < pad) left = pad;
            if (left > maxLeft) left = maxLeft;
            var spaceAbove = rect.top - pad;
            var spaceBelow = window.innerHeight - rect.bottom - pad;
            // Prefer opening upward (above the button); if it does not fit, shrink the overlay.
            if (spaceAbove >= 180 || spaceAbove >= spaceBelow) {
                cap.style.maxHeight = Math.min(spaceAbove, parseFloat(getComputedStyle(cap).maxHeight) || 600) + 'px';
                cap.style.top = (rect.top - cap.offsetHeight - pad) + 'px';
                cap.style.bottom = 'auto';
            } else {
                cap.style.maxHeight = Math.min(spaceBelow, parseFloat(getComputedStyle(cap).maxHeight) || 600) + 'px';
                cap.style.top = (rect.bottom + pad) + 'px';
                cap.style.bottom = 'auto';
            }
            cap.style.left = left + 'px';
        }
        if (cam && cap) {
            cam.addEventListener('click', function (e) {
                e.stopPropagation();
                var willShow = cap.classList.contains('hidden');
                cap.classList.toggle('hidden');
                if (willShow) positionCaptureOverlay();
            });
            document.addEventListener('click', function (e) {
                if (cap && !cap.classList.contains('hidden') && !cap.contains(e.target) && e.target !== cam && !cam.contains(e.target)) {
                    cap.classList.add('hidden');
                }
            });
        }
    })();
    