// Extracted from index.html — t2 inline-script externalization (2026-09-19).
// 侧栏开合（toggle / scrim / Esc / resize）
//
// 纯机械搬迁：本文件正文与 index.html 内联时**逐字节相同**（未 dedent、未改逻辑），
// 与 batch-3 split 的做法一致。以 classic script（非 module / 非 async / 非 defer）
// 在 <script src="./sidebar_toggle.js"> 的**原位置**引入，与其余脚本共享同一全局词法环境，
// 因此顶层 const/let 仍可被后续脚本访问，相对执行顺序与搬迁前完全一致。
// 无损性由 t2 的提取脚本自证：重组后与搬迁前的 index.html 逐字节相同。

        (function () {
            var body = document.body;
            var toggle = document.getElementById('sidebarToggle');
            var scrim = document.getElementById('sidebarScrim');
            if (!toggle || !scrim) return;
            function setOpen(open) {
                body.classList.toggle('sidebar-open', open);
                toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
            }
            toggle.addEventListener('click', function () {
                setOpen(!body.classList.contains('sidebar-open'));
            });
            scrim.addEventListener('click', function () { setOpen(false); });
            document.addEventListener('keydown', function (e) {
                if (e.key === 'Escape') setOpen(false);
            });
            window.addEventListener('resize', function () {
                if (window.innerWidth >= 1024) setOpen(false);
            });
        })();
        