#!/usr/bin/env node
/**
 * 全屏态命中测试：证明「退出按钮明明在 DOM 里却点不到」。
 *
 * 假设：.main-content 自带 position:relative;z-index:1 → 新建层叠上下文，
 * 把 .video-card.fullscreen 的 z-index:200 **囚禁**在 z-index:1 里；
 * 而 .header 是 z-index:50，在根层叠上下文里 → header 永远压在视频之上，
 * 其右上角按钮（主题切换等）正好盖住 .fullscreen-btn。
 * 用 elementFromPoint 取证：谁真正接收了这一点的点击。
 */
import http from 'node:http';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const argv = process.argv.slice(2);
const arg = (k, d) => { const i = argv.indexOf(k); return i >= 0 ? argv[i + 1] : d; };
const PORT = Number(arg('--port', 8123));
const URL_ = `http://127.0.0.1:${PORT}/index.html`;
const CDP_PORT = 10012;
const PROFILE = fs.mkdtempSync(path.join(os.tmpdir(), 'fshit-'));
const CHROME = process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const chrome = spawn(CHROME, ['--headless=new', '--disable-gpu', '--hide-scrollbars',
  `--remote-debugging-port=${CDP_PORT}`, `--user-data-dir=${PROFILE}`, '--window-size=1600,900', 'about:blank'], { stdio: 'ignore' });

const get = (p) => new Promise((res, rej) => {
  http.get({ host: '127.0.0.1', port: CDP_PORT, path: p }, (r) => { let d = ''; r.on('data', c => d += c); r.on('end', () => res(JSON.parse(d))); }).on('error', rej);
});
let _id = 0;
const cdp = (ws, m, p = {}) => new Promise((res, rej) => {
  const id = ++_id;
  const h = (ev) => { const x = JSON.parse(ev.data.toString()); if (x.id === id) { ws.removeEventListener('message', h); x.error ? rej(new Error(JSON.stringify(x.error))) : res(x.result); } };
  ws.addEventListener('message', h); ws.send(JSON.stringify({ id, method: m, params: p }));
});
const cleanup = () => { try { chrome.kill(); } catch {} try { fs.rmSync(PROFILE, { recursive: true, force: true }); } catch {} };

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
  await ev(`window.alert=function(){}`);

  await ev(`document.querySelector('.fullscreen-btn').click()`);
  await new Promise(r => setTimeout(r, 1000));

  const out = await ev(`(()=>{
    const desc = el => el ? (el.id ? '#'+el.id : (el.tagName.toLowerCase()+'.'+String(el.className).split(' ').filter(Boolean).slice(0,2).join('.'))) : 'null';
    const probe = (el, label) => {
      if(!el) return {label, missing:true};
      const r = el.getBoundingClientRect();
      const cx = Math.round(r.x + r.width/2), cy = Math.round(r.y + r.height/2);
      const top = document.elementFromPoint(cx, cy);
      return { label, point:[cx,cy], rect:[Math.round(r.x),Math.round(r.y),Math.round(r.width),Math.round(r.height)],
               topmost: desc(top), reachable: top === el || el.contains(top) };
    };
    // 层叠上下文取证
    const chain = [];
    let n = document.getElementById('videoCard');
    while (n && n !== document.documentElement) {
      const cs = getComputedStyle(n);
      chain.push({ el: desc(n), z: cs.zIndex, pos: cs.position,
                   createsCtx: cs.position !== 'static' && cs.zIndex !== 'auto' });
      n = n.parentElement;
    }
    return JSON.stringify({
      probes: [
        probe(document.querySelector('.fullscreen-btn'), '全屏/退出按钮'),
        probe(document.getElementById('quickCameraBtn'), '前后置切换'),
        probe(document.getElementById('settingsBtn'), '设置'),
        probe(document.querySelector('.theme-toggle') || document.getElementById('themeToggle'), '主题切换'),
        probe(document.getElementById('promptSendBtn'), '发送'),
        probe(document.getElementById('speechBtn'), '按住说话'),
        probe(document.getElementById('promptText'), '输入框')
      ],
      stackChain: chain
    });
  })()`);

  const { probes, stackChain } = JSON.parse(out);
  console.log('\n【层叠上下文链】#videoCard → 根');
  stackChain.forEach((c, i) => console.log(`  ${' '.repeat(i*2)}${c.el}  position:${c.pos} z-index:${c.z}${c.createsCtx ? '  ★ 新建层叠上下文' : ''}`));
  console.log('\n【命中测试】谁真正接住了点击');
  probes.forEach(p => {
    if (p.missing) { console.log(`  ?  ${p.label}: DOM 中不存在`); return; }
    console.log(`  ${p.reachable ? '✅ 可点' : '❌ 被遮'} ${p.label.padEnd(14)} @${p.point} rect=${p.rect}  最上层=${p.topmost}`);
  });
  console.log('');
  ws.close(); cleanup();
};
main().catch(e => { console.error('FAILED:', e.message); cleanup(); process.exit(2); });
