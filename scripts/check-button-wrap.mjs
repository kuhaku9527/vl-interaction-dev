#!/usr/bin/env node
/**
 * 扫描 WebUI 全部设置面板，找出「带文字的按钮被挤成竖排」的问题。
 *
 * 为什么需要（2026-09-18 实测教训）：
 *   中文（全角）在窄容器里会**逐字换行**。`.action-row .icon-btn` 的 nowrap 契约
 *   只覆盖 .action-row 的直接子元素；写在内联 `style="display:flex"` 容器里的按钮
 *   不会被覆盖 —— 肉眼看着"容器已经横排了"，但按钮内部仍会竖排。
 *   该问题在 记忆 / 知识库 / 服务 / 语音 四个面板各自出现过一次。
 *
 * 判据：按钮含文字 span，且 (高度 > 40px) 或 (宽度 < 60px 且含中文) → 判定竖排。
 *
 * 需要先起静态服务：
 *   services/.venv/Scripts/python.exe -m http.server 8123 \
 *     --directory services/webui/src/joy_interaction_webui/static --bind 127.0.0.1
 *
 * 用法: node scripts/check-button-wrap.mjs [--port 8123]
 */
import http from 'node:http';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const argv = process.argv.slice(2);
const portIdx = argv.indexOf('--port');
const PORT = portIdx >= 0 ? Number(argv[portIdx + 1]) : 8123;
const URL_ = `http://127.0.0.1:${PORT}/index.html`;
const CDP_PORT = 10010;
const PROFILE = fs.mkdtempSync(path.join(os.tmpdir(), 'wrapcheck-'));

const CHROME = process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
if (!fs.existsSync(CHROME)) {
  console.error(`未找到 Chrome: ${CHROME}（可用 CHROME_PATH 环境变量覆盖）`);
  process.exit(2);
}
const chrome = spawn(CHROME, ['--headless=new', '--disable-gpu', '--hide-scrollbars',
  `--remote-debugging-port=${CDP_PORT}`, `--user-data-dir=${PROFILE}`,
  '--window-size=1440,900', 'about:blank'], { stdio: 'ignore' });

const get = (p) => new Promise((res, rej) => {
  http.get({ host: '127.0.0.1', port: CDP_PORT, path: p }, (r) => {
    let d = ''; r.on('data', (c) => d += c); r.on('end', () => res(JSON.parse(d)));
  }).on('error', rej);
});
const cdp = (ws, m, p = {}, id = 1) => new Promise((res, rej) => {
  const h = (ev) => {
    const x = JSON.parse(ev.data.toString());
    if (x.id === id) { ws.removeEventListener('message', h); x.error ? rej(new Error(JSON.stringify(x.error))) : res(x.result); }
  };
  ws.addEventListener('message', h); ws.send(JSON.stringify({ id, method: m, params: p }));
});

const cleanup = () => { try { chrome.kill(); } catch {} try { fs.rmSync(PROFILE, { recursive: true, force: true }); } catch {} };

const main = async () => {
  for (let i = 0; i < 80; i++) { try { await get('/json/version'); break; } catch { await new Promise((r) => setTimeout(r, 300)); } }
  const t = (await get('/json/list')).find((x) => x.type === 'page');
  const ws = new WebSocket(t.webSocketDebuggerUrl);
  await new Promise((r, j) => { ws.addEventListener('open', r); ws.addEventListener('error', j); });
  await cdp(ws, 'Runtime.enable'); await cdp(ws, 'Page.enable');
  await cdp(ws, 'Page.navigate', { url: URL_ });
  await new Promise((r) => setTimeout(r, 5000));
  const ev = async (e) => (await cdp(ws, 'Runtime.evaluate', { expression: e, returnByValue: true, awaitPromise: true })).result.value;

  await ev(`window.alert=function(){}; const b=document.getElementById('settingsBtn'); if(b)b.click();`);
  await new Promise((r) => setTimeout(r, 900));

  const raw = await ev(`JSON.stringify([...document.querySelectorAll('#modalNav .nav-item')].map(n=>n.getAttribute('data-panel')))`);
  const panels = JSON.parse(raw || '[]');
  let totalBad = 0;
  const problems = [];

  for (const p of panels) {
    await ev(`(()=>{const n=document.querySelector('#modalNav .nav-item[data-panel="${p}"]'); if(n)n.click();})()`);
    await new Promise((r) => setTimeout(r, 450));
    const res = await ev(`(()=>{
      const out=[];
      document.querySelectorAll('.icon-btn, .conn-act, .provider-add, .provider-del, .chat-prompt-action').forEach(b=>{
        const sp=b.querySelector('span');
        const text=sp ? sp.textContent.trim() : b.textContent.trim();
        if(!text) return;
        const rr=b.getBoundingClientRect();
        if(rr.width===0 && rr.height===0) return;      // 隐藏
        // 设计上就是「图标 + 文字」双行的入口按钮（如输入栏 #camBtn，52px 高、
        // 纵向 flex 是有意为之），不算竖排事故。判据：显式 flex-direction:column
        // 且高度在 44~60px 区间 —— 真正的竖排事故是被挤到 60px 以上。
        const isLabeledIcon = getComputedStyle(b).flexDirection === 'column' && rr.height <= 60;
        const wrapped = !isLabeledIcon && (rr.height > 40 || (rr.width < 60 && /[\\u4e00-\\u9fa5]/.test(text)));
        out.push({id:b.id||b.className.split(' ')[0], w:Math.round(rr.width), h:Math.round(rr.height), text:text.slice(0,14), wrapped});
      });
      return JSON.stringify(out);
    })()`);
    const items = JSON.parse(res || '[]');
    const bad = items.filter((x) => x.wrapped);
    totalBad += bad.length;
    if (bad.length) problems.push({ panel: p, items: bad });
    console.log(`[${p}] 带文字按钮 ${items.length} 个` + (bad.length ? `  ⚠️ 竖排 ${bad.length}: ${bad.map((x) => x.id).join(', ')}` : '  ✅'));
  }

  ws.close(); cleanup();
  console.log('');
  if (totalBad) {
    console.log(`❌ 共 ${totalBad} 个按钮竖排。修复建议：迁到 .action-row，或提高 min-width（见 doc/standards/webui-design-standards.md §4.2）`);
    process.exit(1);
  }
  console.log('✅ 全部面板的带文字按钮均正常横排');
  process.exit(0);
};
main().catch((e) => { console.error('FAILED:', e.message); cleanup(); process.exit(2); });
