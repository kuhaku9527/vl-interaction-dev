#!/usr/bin/env node
/**
 * 全屏收口验收：两态按钮统一性 + 布局 + 字幕 + 命中测试。
 * 对应需求 2(a) 统一 / 2(b) 底部布局 / 3(c) 字幕只显示 AI 回复。
 *
 * 需要先起静态服务：
 *   services/.venv/Scripts/python.exe -m http.server 8123 \
 *     --directory services/webui/src/joy_interaction_webui/static --bind 127.0.0.1
 *
 * 用法: node scripts/check-fullscreen-parity.mjs [--port 8123]
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
const CDP_PORT = 10014;
const PROFILE = fs.mkdtempSync(path.join(os.tmpdir(), 'fsparity-'));
const OUT = 'scripts/.fs-inspect';
const CHROME = process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
if (!fs.existsSync(CHROME)) { console.error(`未找到 Chrome: ${CHROME}`); process.exit(2); }
fs.mkdirSync(OUT, { recursive: true });

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

// 输入栏内「应该存在的按钮」——用户要求两态统一
const EXPECTED_INPUTBAR = ['camBtn', 'speechBtn', 'promptSendBtn'];
const EXPECTED_TOOLS = ['quickCameraBtn', 'fullscreenIcon'];

const main = async () => {
  for (let i = 0; i < 80; i++) { try { await get('/json/version'); break; } catch { await new Promise(r => setTimeout(r, 300)); } }
  const t = (await get('/json/list')).find(x => x.type === 'page');
  const ws = new WebSocket(t.webSocketDebuggerUrl);
  await new Promise((r, j) => { ws.addEventListener('open', r); ws.addEventListener('error', j); });
  await cdp(ws, 'Runtime.enable'); await cdp(ws, 'Page.enable');
  await cdp(ws, 'Emulation.setDeviceMetricsOverride', { width: 1600, height: 900, deviceScaleFactor: 1, mobile: false });
  await cdp(ws, 'Page.navigate', { url: URL_ });
  await new Promise(r => setTimeout(r, 5500));
  const ev = async (e) => (await cdp(ws, 'Runtime.evaluate', { expression: e, returnByValue: true, awaitPromise: true })).result.value;
  await ev(`window.alert=function(){}`);

  // 收集「输入栏 + 工具条」两态可见按钮集（共用同一判据）
  const SNAP = `(()=>{
    const vis = el => { if(!el) return null; const r=el.getBoundingClientRect(); const cs=getComputedStyle(el);
      return (r.width>0 && r.height>0 && cs.display!=='none' && cs.visibility!=='hidden') ? {w:Math.round(r.width),h:Math.round(r.height),x:Math.round(r.x),y:Math.round(r.y)} : null; };
    const bar = document.getElementById('promptEditor');
    const ids = [...bar.querySelectorAll('button[id], select[id], textarea[id]')].map(e=>e.id).filter(Boolean);
    const inputbarVisible = ids.filter(id => vis(document.getElementById(id)));
    const tools = ['quickCameraBtn','fullscreenIcon'].filter(id => vis(document.getElementById(id)));
    const overlay = document.getElementById('fullscreenVlmOverlay');
    const ovr = vis(overlay);
    return JSON.stringify({
      inputbarVisible, tools,
      promptText: vis(document.getElementById('promptText')),
      barRect: vis(bar),
      // 字幕浮层：可见性 + 文案（用于验证"只显示 AI 回复"与"空则隐藏"）
      subtitles: { visible: !!ovr, text: (document.getElementById('fullscreenVlmContent')||{}).textContent || '' },
      exitHit: (()=>{ const b=document.querySelector('.fullscreen-btn'); if(!b) return 'missing';
        const r=b.getBoundingClientRect(); if(!r.width) return 'zero';
        const top=document.elementFromPoint(Math.round(r.x+r.width/2), Math.round(r.y+r.height/2));
        return (top===b||b.contains(top))?'ok':'blocked-by:'+(top?(top.id||top.className):'null'); })(),
      headerVisible: (()=>{const h=document.querySelector('.header'); if(!h) return false;
        const cs=getComputedStyle(h); const r=h.getBoundingClientRect(); return cs.display!=='none' && r.height>0;})(),
      // 设置页 Live 区块是否真的存在且可见（需先打开设置）
      settingsLive: !!document.getElementById('liveModeSection')
    });
  })()`;

  const shot = async (name) => {
    const r = await cdp(ws, 'Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync(path.join(OUT, name), Buffer.from(r.data, 'base64'));
    return path.join(OUT, name);
  };

  let pass = 0, fail = 0;
  const check = (cond, label, detail = '') => {
    console.log(`  ${cond ? '✅' : '❌'} ${label}${detail ? '  ' + detail : ''}`);
    cond ? pass++ : fail++;
  };

  // ---------- A 常规态 ----------
  const A = JSON.parse(await ev(SNAP));
  console.log('\n【A】常规态');
  console.log(`  输入栏可见控件: ${A.inputbarVisible.join(', ')}`);
  console.log(`  工具条可见: ${A.tools.join(', ')}`);
  console.log(`  #promptText: ${A.promptText ? A.promptText.w + 'px 宽' : '不可见'}`);
  console.log(`  截图: ${await shot('D-normal.png')}`);

  // ---------- B 全屏态 ----------
  await ev(`document.querySelector('.fullscreen-btn').click()`);
  await new Promise(r => setTimeout(r, 1200));
  const B = JSON.parse(await ev(SNAP));
  console.log('\n【B】全屏态');
  console.log(`  输入栏可见控件: ${B.inputbarVisible.join(', ')}`);
  console.log(`  工具条可见: ${B.tools.join(', ')}`);
  console.log(`  #promptText: ${B.promptText ? B.promptText.w + 'px 宽' : '不可见'}`);
  console.log(`  字幕浮层: ${B.subtitles.visible ? '可见' : '已隐藏（无内容）'}  文本="${B.subtitles.text.slice(0, 40)}"`);
  console.log(`  截图: ${await shot('E-fullscreen.png')}`);

  console.log('\n【判定】');
  // 2(a) 两态按钮统一
  const sameBar = JSON.stringify(A.inputbarVisible) === JSON.stringify(B.inputbarVisible);
  check(sameBar, '2(a) 两态输入栏按钮集完全一致',
    sameBar ? `[${B.inputbarVisible.join(', ')}]` : `常规=[${A.inputbarVisible}] 全屏=[${B.inputbarVisible}]`);
  check(JSON.stringify(A.tools) === JSON.stringify(B.tools), '2(a) 两态工具条按钮集一致');
  // 关键：输入栏里不该再有「设置页专属」的 Live 控件。
  // 注意：liveModeBtn（实时）**有意**留在输入栏（用户 2026-09-19 要求"方便直接开启"），
  // 故不在此列。其余 5 个（唤醒/注册声音/开画面/来源/主动搭话/快速预设）必须已迁出。
  const SETTINGS_ONLY = ['btListenBtn', 'liveEnrollBtn', 'liveVideoBtn', 'liveProactiveToggle', 'liveVideoSource', 'promptPresetBtn', 'promptPresetMenu'];
  const leaked = B.inputbarVisible.filter(id => SETTINGS_ONLY.includes(id));
  check(leaked.length === 0, '设置页专属控件已不在输入栏', leaked.length ? `泄漏: ${leaked.join(', ')}` : '');
  // 且「实时」必须在输入栏（回归保护）
  check(B.inputbarVisible.includes('liveModeBtn'), '「实时」按钮在输入栏（用户要求，防回归）');
  // 2(b) 底部布局
  const barY = B.barRect ? B.barRect.y + B.barRect.h : 0;
  check(barY > 700, '2(b) 输入栏贴底（y+h > 700）', B.barRect ? `底部=${barY}px / 视口高 900` : '');
  const centered = B.barRect ? Math.abs((B.barRect.x + B.barRect.w / 2) - 800) < 30 : false;
  check(centered, '2(b) 输入栏水平居中', B.barRect ? `中心=${Math.round(B.barRect.x + B.barRect.w / 2)} / 视口中心 800` : '');
  // 退出按钮
  check(B.exitHit === 'ok', '退出按钮可点（不被遮挡）', B.exitHit);
  // 全屏彻底
  check(!B.headerVisible, '全屏时顶栏已收起（画面独占）');
  // 输入框可用宽度
  check(B.promptText && B.promptText.w >= 200, '输入框宽度正常（未被挤压）', B.promptText ? `${B.promptText.w}px` : '');
  // 3(c) 字幕空则隐藏（本次无 VLM 数据，应为隐藏）
  check(!B.subtitles.visible, '3(c) 无字幕内容时浮层整块隐藏（不再显示"就绪"空盒子）');
  // 设置页落点
  check(B.settingsLive, '设置页存在 Live 常驻模式区块');

  // ---------- C 字幕只显示 AI 回复（注入假数据验证）----------
  // 注意：vlmHistory 是 index.html 内联脚本里的 `let`（词法作用域），
  // 不在 window 上 —— 早期版本用 window.vlmHistory 拿到的是 undefined，
  // 静默空注入导致 2 条断言假失败。这里改为在页面里直接引用标识符。
  const injected = await ev(`(()=>{
    try {
      vlmHistory.length = 0;
      vlmHistory.push({key:'t1', prompt:'用户问的问题不该出现在字幕里', response:'</response>这是模型回复的内容', rawText:''});
      vlmHistory.push({key:'t2', prompt:'第二问', response:'</silence>', rawText:''});
      if (typeof syncVlmToFullscreen === 'function') syncVlmToFullscreen();
      return JSON.stringify({ok:true, len: vlmHistory.length, text: getFullscreenVlmText()});
    } catch (e) { return JSON.stringify({ok:false, err: String(e)}); }
  })()`);
  const inj = JSON.parse(injected);
  if (!inj.ok) { console.log(`\n❌ 注入失败: ${inj.err}`); }
  await new Promise(r => setTimeout(r, 500));
  const C = JSON.parse(await ev(SNAP));
  console.log('\n【C】字幕内容验证（注入 2 条假历史）');
  console.log(`  getFullscreenVlmText() 直接返回: "${inj.text || ''}"`);
  console.log(`  字幕浮层文本: "${C.subtitles.text}"`);
  console.log(`  截图: ${await shot('F-subtitle.png')}`);
  check(inj.ok, '注入测试数据成功');
  check(C.subtitles.visible, '3(c) 有回复时字幕浮层显示');
  check(!C.subtitles.text.includes('用户问的问题'), '3(c) 字幕不含用户输入（不推「输入：…」）');
  check(!C.subtitles.text.includes('</response>'), '3(c) 决策 token </response> 已被剥离');
  check(C.subtitles.text.includes('这是模型回复的内容'), '3(c) 字幕含模型回复正文');
  check(!C.subtitles.text.includes('第二问') && !C.subtitles.text.includes('silence'), '3(c) </silence> 轮次不出字幕');

  ws.close(); cleanup();
  console.log(`\n${'='.repeat(60)}\n通过 ${pass} / 失败 ${fail}`);
  process.exit(fail ? 1 : 0);
};
main().catch(e => { console.error('FAILED:', e.message); cleanup(); process.exit(2); });
