// Extracted from index.html — t2 inline-script externalization (2026-09-19).
// head bootstrap：document.title + lucide CDN 兜底 + i18n 初始化（DOM 就绪时）
//
// 纯机械搬迁：本文件正文与 index.html 内联时**逐字节相同**（未 dedent、未改逻辑），
// 与 batch-3 split 的做法一致。以 classic script（非 module / 非 async / 非 defer）
// 在 <script src="./app_boot.js"> 的**原位置**引入，与其余脚本共享同一全局词法环境，
// 因此顶层 const/let 仍可被后续脚本访问，相对执行顺序与搬迁前完全一致。
// 无损性由 t2 的提取脚本自证：重组后与搬迁前的 index.html 逐字节相同。

        document.title = 'JoyAI VL Live';

        // Resilience: lucide is loaded from a CDN. If the CDN is blocked/slow
        // (offline, firewall, flaky network), `lucide` is undefined and every
        // `lucide.createIcons()` call would throw, aborting the inline script
        // mid-bootstrap (theme, settings modal, WS, live-mode handlers...).
        // Stub it to a no-op so the app degrades gracefully (icons missing)
        // instead of hard-crashing. Real env with the CDN up is unaffected.
        if (typeof lucide === 'undefined') {
            window.lucide = { createIcons: function () {} };
        }

        // Initialize Lucide icons immediately when DOM is ready
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', () => {
                lucide.createIcons();
                // Localize static markup (issue #47): Chinese resolved from
                // window.JoyI18n.UI_STRING_MAP, never hardcoded in HTML.
                if (window.JoyI18n && typeof window.JoyI18n.applyUiI18n === 'function') {
                    window.JoyI18n.applyUiI18n(document);
                }
            });
        } else {
            lucide.createIcons();
            if (window.JoyI18n && typeof window.JoyI18n.applyUiI18n === 'function') {
                window.JoyI18n.applyUiI18n(document);
            }
        }
    