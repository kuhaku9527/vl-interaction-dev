#!/usr/bin/env node
/**
 * 全屏态候选修复验证：把假设里的 CSS 注入真实页面，用几何+命中测试判定是否真修好。
 * 只有全部 PASS 的方案才会写进给用户的建议里。
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
const CDP_PORT = 10013;
const PROFILE = fs.mkdtempSync(path.join(os.tmpdir(), 'fsfix-'));
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

// ---- 候选修复（纯 CSS，一行一条，逐条独立验证）--------------------------
const CANDIDATES = [
  {
    name: '① 解除 .main-content 的层叠囚禁',
    css: `.container:has(> .main-content .video-card.fullscreen) .main-content{ z-index: auto; }`,
    why: 'fullscreen 的 z-index:200 原本被 main-content 的 z-index:1 关在笼子里，抬不出来'
  },
  {
    name: '② 全屏时收起顶栏（真正全屏）',
    css: `body:has(.video-card.fullscreen) .header{ display: none !important; }`,
    why: '顶栏 z-index:50 永远高过被囚禁的视频；且全屏就该是画面独占'
  },
  {
    name: '③ 全屏态沿用输入栏的隐藏契约',
    css: `#fullscreenPromptOverlay #btListenBtn,
          #fullscreenPromptOverlay #liveEnrollRow,
          #fullscreenPromptOverlay #liveVideoRow,
          #fullscreenPromptOverlay #promptPresetBtn,
          #fullscreenPromptOverlay #promptPresetMenu{ display: none !important; }`,
    why: '父节点搬家后 .prompt-editor-inline 作用域失效，5 个控件复活'
  },
  {
    name: '④ 全屏输入栏去掉写死的 72px 高度上限',
    css: `.video-card.fullscreen .fullscreen-prompt-overlay{ max-height: none; }`,
    why: '#promptEditor 实测 139px，被 72px 上限截断，把 #promptText 挤成 8px 宽'
  }
];

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

  const PROBE = `(()=>{
    const d=el=>el?(el.id?'#'+el.id:(el.tagName.toLowerCase()+'.'+String(el.className).split(' ').filter(Boolean).slice(0,2).join('.'))):'null';
    const hit=el=>{ if(!el) return null; const r=el.getBoundingClientRect(); if(!r.width||!r.height) return 'zero';
      const top=document.elementFromPoint(Math.round(r.x+r.width/2),Math.round(r.y+r.height/2));
      return (top===el||el.contains(top))?'ok':'blocked-by:'+d(top); };
    const box=s=>{const e=document.querySelector(s); if(!e) return null; const r=e.getBoundingClientRect(); return [Math.round(r.x),Math.round(r.y),Math.round(r.width),Math.round(r.height)];};
    const shown=id=>{const e=document.getElementById(id); if(!e) return 'missing'; const r=e.getBoundingClientRect();
      return (getComputedStyle(e).display!=='none' && r.width>0 && r.height>0)?'VISIBLE':'hidden';};
    return JSON.stringify({
      exitBtn: hit(document.querySelector('.fullscreen-btn')),
      camBtn:  hit(document.getElementById('quickCameraBtn')),
      send:    hit(document.getElementById('promptSendBtn')),
      headerVisible: !!document.querySelector('.header') && getComputedStyle(document.querySelector('.header')).display!=='none',
      promptText: box('#promptText'),
      overlay: box('#fullscreenPromptOverlay'),
      editor: box('#promptEditor'),
      revived: { btListen:shown('btListenBtn'), liveEnroll:shown('liveEnrollRow'),
                 liveVideo:shown('liveVideoRow'), preset:shown('promptPresetBtn') }
    });
  })()`;

  const runProbe = async () => JSON.parse(await ev(PROBE));

  const report = (tag, r) => {
    const ok = (v) => v === 'ok' ? '✅' : '❌';
    console.log(`\n${tag}`);
    console.log(`  退出按钮命中最上层 : ${ok(r.exitBtn)} ${r.exitBtn}`);
    console.log(`  前后置按钮         : ${ok(r.camBtn)} ${r.camBtn}`);
    console.log(`  发送按钮           : ${ok(r.send)} ${r.send}`);
    console.log(`  顶栏是否还在       : ${r.headerVisible ? '❌ 在（全屏不彻底）' : '✅ 已收起'}`);
    console.log(`  #promptText 宽度   : ${r.promptText ? r.promptText[2] + 'px ' + (r.promptText[2] >= 120 ? '✅' : '❌ 被挤窄') : 'n/a'}`);
    console.log(`  输入栏溢出         : overlay ${r.overlay?.[3]}px / editor ${r.editor?.[3]}px ` + (r.editor && r.overlay && r.editor[3] <= r.overlay[3] + 2 ? '✅ 装得下' : '❌ 还是溢出'));
    const rev = Object.entries(r.revived).map(([k, v]) => `${k}=${v}`).join(' ');
    console.log(`  复活的 5 个控件    : ${Object.values(r.revived).every(v => v === 'hidden') ? '✅ 全部隐藏' : '❌ ' + rev}`);
  };

  console.log('='.repeat(78));
  console.log('【基线】未加任何修复');
  await ev(`document.querySelector('.fullscreen-btn').click()`);
  await new Promise(r => setTimeout(r, 900));
  report('基线全屏态', await runProbe());

  console.log('\n' + '='.repeat(78));
  console.log('【逐条注入候选修复】');
  for (const c of CANDIDATES) {
    await ev(`(()=>{const s=document.createElement('style');s.id='cand';s.textContent=${JSON.stringify(c.css)};document.head.appendChild(s);})()`);
    await new Promise(r => setTimeout(r, 400));
    console.log(`\n＋ ${c.name}\n   理由：${c.why}`);
    report('   结果', await runProbe());
    await ev(`document.getElementById('cand')?.remove()`);
    await new Promise(r => setTimeout(r, 200));
  }

  console.log('\n' + '='.repeat(78));
  console.log('【全量叠加】4 条一起上');
  const all = CANDIDATES.map(c => c.css).join('\n');
  await ev(`(()=>{const s=document.createElement('style');s.id='cand';s.textContent=${JSON.stringify(all)};document.head.appendChild(s);})()`);
  await new Promise(r => setTimeout(r, 500));
  report('全量修复后', await runProbe());
  const shot = (await cdp(ws, 'Page.captureScreenshot', { format: 'png' })).data;
  fs.mkdirSync('scripts/.fs-inspect', { recursive: true });
  fs.writeFileSync('scripts/.fs-inspect/C-fixed.png', Buffer.from(shot, 'base64'));
  console.log('\n  截图: scripts/.fs-inspect/C-fixed.png');

  ws.close(); cleanup();
};
main().catch(e => { console.error('FAILED:', e.message); cleanup(); process.exit(2); });
