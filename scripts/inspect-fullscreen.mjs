#!/usr/bin/env node
/**
 * 全屏态结构剖析：渲染 index.html → 点全屏 → 测量每个元素的真实几何位置 + 截图。
 *
 * 为什么需要：全屏态不是"另一个页面"，而是 toggleFullscreen() 把 #promptEditor
 * **搬 DOM 父节点**（appendChild）到 .fullscreen-prompt-overlay 里。父节点一变，
 * 所有 `.prompt-editor-inline <sel>` 作用域下的 display:none / 布局规则**全部失效**，
 * 一堆控件重新冒出来 —— 只有实测几何才能看清谁盖了谁。
 *
 * 需要先起静态服务：
 *   services/.venv/Scripts/python.exe -m http.server 8123 \
 *     --directory services/webui/src/joy_interaction_webui/static --bind 127.0.0.1
 *
 * 用法: node scripts/inspect-fullscreen.mjs [--port 8123] [--out scripts/.fs-inspect]
 */
import http from 'node:http';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const argv = process.argv.slice(2);
const arg = (k, d) => { const i = argv.indexOf(k); return i >= 0 ? argv[i + 1] : d; };
const PORT = Number(arg('--port', 8123));
const OUT = arg('--out', 'scripts/.fs-inspect');
const URL_ = `http://127.0.0.1:${PORT}/index.html`;
const CDP_PORT = 10011;
const PROFILE = fs.mkdtempSync(path.join(os.tmpdir(), 'fsinspect-'));

const CHROME = process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
if (!fs.existsSync(CHROME)) { console.error(`未找到 Chrome: ${CHROME}`); process.exit(2); }
fs.mkdirSync(OUT, { recursive: true });

const chrome = spawn(CHROME, ['--headless=new', '--disable-gpu', '--hide-scrollbars',
  `--remote-debugging-port=${CDP_PORT}`, `--user-data-dir=${PROFILE}`,
  '--window-size=1600,900', 'about:blank'], { stdio: 'ignore' });

const get = (p) => new Promise((res, rej) => {
  http.get({ host: '127.0.0.1', port: CDP_PORT, path: p }, (r) => {
    let d = ''; r.on('data', (c) => d += c); r.on('end', () => res(JSON.parse(d)));
  }).on('error', rej);
});
let _id = 0;
const cdp = (ws, m, p = {}) => new Promise((res, rej) => {
  const id = ++_id;
  const h = (ev) => {
    const x = JSON.parse(ev.data.toString());
    if (x.id === id) { ws.removeEventListener('message', h); x.error ? rej(new Error(JSON.stringify(x.error))) : res(x.result); }
  };
  ws.addEventListener('message', h); ws.send(JSON.stringify({ id, method: m, params: p }));
});
const cleanup = () => { try { chrome.kill(); } catch {} try { fs.rmSync(PROFILE, { recursive: true, force: true }); } catch {} };

// 全屏态下「值得看的每一个节点」—— 用 DOM 直查，不硬编码坐标
const PROBE = `(()=>{
  const sel = [
    '.video-card', '.video-card video', '.video-placeholder', '.video-overlay',
    '.video-tools', '.fullscreen-btn', '#fullscreenIcon', '#quickCameraBtn',
    '.fullscreen-prompt-overlay', '#promptEditor', '.fullscreen-vlm-overlay',
    '#fullscreenVlmContent', '#fullscreenVlmMetrics', '.capture-fab', '#captureOverlay',
    '#camBtn', '#liveModeBtn', '#btListenBtn', '#promptPresetBtn', '#promptText',
    '#liveEnrollRow', '#liveVideoRow', '#liveProbe', '#speechBtn', '#promptSendBtn',
    '.header', '#settingsBtn', '.sidebar', '.main-content', '#vlmOutputCard',
    '#toastHost', '#latencyPill'
  ];
  const rows = [];
  sel.forEach(s => {
    document.querySelectorAll(s).forEach(el => {
      const r = el.getBoundingClientRect();
      const cs = getComputedStyle(el);
      rows.push({
        sel: s, id: el.id || '', cls: (el.className && el.className.baseVal !== undefined ? el.className.baseVal : el.className || '').toString().slice(0,60),
        parent: el.parentElement ? (el.parentElement.id || el.parentElement.className.toString().split(' ')[0]) : '',
        x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height),
        z: cs.zIndex, disp: cs.display, vis: cs.visibility, op: cs.opacity, pos: cs.position,
        hidden: r.width === 0 || r.height === 0 || cs.display === 'none' || cs.visibility === 'hidden'
      });
    });
  });
  return JSON.stringify({
    promptEditorParent: document.getElementById('promptEditor')?.parentElement?.id
      || document.getElementById('promptEditor')?.parentElement?.className,
    viewport: [innerWidth, innerHeight],
    rows
  });
})()`;

const shot = async (ws, name) => {
  const r = await cdp(ws, 'Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
  const f = path.join(OUT, name);
  fs.writeFileSync(f, Buffer.from(r.data, 'base64'));
  return f;
};

const dump = (label, data) => {
  const { rows, promptEditorParent, viewport } = data;
  console.log(`\n${'='.repeat(78)}\n${label}   视口 ${viewport[0]}x${viewport[1]}`);
  console.log(`#promptEditor 的父节点 = ${promptEditorParent}`);
  console.log('-'.repeat(78));
  console.log('可见元素（按 y 排序）:');
  rows.filter(r => !r.hidden).sort((a, b) => a.y - b.y || a.x - b.x)
    .forEach(r => console.log(`  y=${String(r.y).padStart(4)} x=${String(r.x).padStart(4)} ${String(r.w).padStart(4)}x${String(r.h).padStart(3)} z=${String(r.z).padStart(5)} ${r.pos.padEnd(8)} ${(r.id ? '#' + r.id : r.sel).padEnd(30)} ← ${r.parent}`));
  console.log(`隐藏元素（display:none / 0x0）: ${rows.filter(r => r.hidden).map(r => r.id ? '#' + r.id : r.sel).join(', ') || '（无）'}`);
  // 重叠检测：全屏态下"谁压着谁"才是混乱的根源
  const vis = rows.filter(r => !r.hidden && r.w > 0 && r.h > 0 && r.pos !== 'fixed' || (!r.hidden && r.sel === '.video-card'));
  const overlap = [];
  for (let i = 0; i < vis.length; i++) for (let j = i + 1; j < vis.length; j++) {
    const a = vis[i], b = vis[j];
    if (a.sel === '.video-card' || b.sel === '.video-card') continue;
    const ox = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x);
    const oy = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y);
    if (ox > 0 && oy > 0) overlap.push(`  ${(a.id || a.sel)} ⨯ ${(b.id || b.sel)}  重叠 ${ox}x${oy}px`);
  }
  console.log(`重叠对: ${overlap.length ? '\n' + overlap.join('\n') : '（无）'}`);
};

const main = async () => {
  for (let i = 0; i < 80; i++) { try { await get('/json/version'); break; } catch { await new Promise(r => setTimeout(r, 300)); } }
  const t = (await get('/json/list')).find(x => x.type === 'page');
  const ws = new WebSocket(t.webSocketDebuggerUrl);
  await new Promise((r, j) => { ws.addEventListener('open', r); ws.addEventListener('error', j); });
  await cdp(ws, 'Runtime.enable'); await cdp(ws, 'Page.enable');
  await cdp(ws, 'Emulation.setDeviceMetricsOverride', { width: 1600, height: 900, deviceScaleFactor: 1, mobile: false });
  await cdp(ws, 'Page.navigate', { url: URL_ });
  await new Promise(r => setTimeout(r, 5000));
  const ev = async (e) => (await cdp(ws, 'Runtime.evaluate', { expression: e, returnByValue: true, awaitPromise: true })).result.value;

  // 静音 alert（已知会在无头下无限阻塞 JS 线程）
  await ev(`window.alert=function(){};window.confirm=function(){return false};window.prompt=function(){return null};`);

  const before = JSON.parse(await ev(PROBE));
  dump('【A】常规态（未点全屏）', before);
  console.log(`截图: ${await shot(ws, 'A-normal.png')}`);

  await ev(`document.querySelector('.fullscreen-btn').click()`);
  await new Promise(r => setTimeout(r, 1200));

  const after = JSON.parse(await ev(PROBE));
  dump('【B】全屏态（点击全屏按钮之后）', after);
  console.log(`截图: ${await shot(ws, 'B-fullscreen.png')}`);

  // 全屏态下：哪些「页内输入栏的隐藏规则」被父节点搬家破坏了？
  const broke = await ev(`(()=>{
    const ids=['btListenBtn','liveEnrollRow','liveVideoRow','promptPresetBtn','promptPresetMenu'];
    return JSON.stringify(ids.map(id=>{
      const el=document.getElementById(id); if(!el) return {id,missing:true};
      const r=el.getBoundingClientRect(); const cs=getComputedStyle(el);
      return {id, display:cs.display, w:Math.round(r.width), h:Math.round(r.height), visible: cs.display!=='none' && r.width>0};
    }));
  })()`);
  console.log('\n【C】原先靠 `.prompt-editor-inline` 作用域隐藏的控件，在全屏态下：');
  JSON.parse(broke).forEach(b => console.log(`  ${b.missing ? b.id + ' (DOM 中不存在)' : (b.visible ? '❌ 重新出现了' : '✅ 仍隐藏')}  ${b.id.padEnd(20)} display=${b.display} ${b.w}x${b.h}`));

  const esc = await ev(`(()=>{document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape'}));return document.getElementById('videoCard').classList.contains('fullscreen')})()`);
  console.log(`\n【D】ESC 退出全屏: ${esc ? '❌ 失败（仍处于 fullscreen）' : '✅ 成功'}`);
  const exitBtn = await ev(`(()=>{const b=document.querySelector('.fullscreen-btn');const r=b.getBoundingClientRect();return JSON.stringify({w:Math.round(r.width),h:Math.round(r.height),x:Math.round(r.x),y:Math.round(r.y),op:getComputedStyle(b).opacity,icon:document.getElementById('fullscreenIcon')?.getAttribute('data-lucide'),hasSvg:!!document.getElementById('fullscreenIcon')})})()`);
  console.log(`【E】唯一的退出控件 .fullscreen-btn: ${exitBtn}`);

  ws.close(); cleanup();
  console.log('\n完成。');
};
main().catch(e => { console.error('FAILED:', e.message); cleanup(); process.exit(2); });
