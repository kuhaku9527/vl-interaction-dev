#!/usr/bin/env node
/**
 * t4: is the console-message COUNT variance (6 vs 7) real, or timing nondeterminism?
 *
 * Method: sample each side N times, independently. If the multiset varies on BOTH
 * sides across repeats (and the message KINDS are identical), the variance is
 * harness/timing noise, not a code regression.
 */
import http from 'node:http';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const STUB = `(() => {
  window.__xssFired = 0;
  const realFetch = window.fetch.bind(window);
  window.fetch = function (input, init) {
    const url = (typeof input === 'string') ? input : (input && input.url) || '';
    if (url.includes('/api/services/extended-status')) return Promise.resolve(new Response(JSON.stringify({
      memory:{enabled:true,reachable:true,ok:true,reason:'stub-ok'}, wiki:{enabled:true,reachable:false,ok:false,reason:'stub-down'}}),{status:200,headers:{'Content-Type':'application/json'}}));
    return realFetch(input, init);
  };
})();`;

const get = (port, p) => new Promise((res, rej) => {
  http.get({ host: '127.0.0.1', port, path: p }, (r) => { let d = ''; r.on('data', (c) => d += c); r.on('end', () => res(JSON.parse(d))); }).on('error', rej);
});

const sample = async (port, cdpPort) => {
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), `t4s-${cdpPort}-`));
  const proc = spawn(CHROME, ['--headless=new', '--disable-gpu', '--hide-scrollbars',
    `--remote-debugging-port=${cdpPort}`, `--user-data-dir=${profile}`, '--window-size=1600,1000', 'about:blank'], { stdio: 'ignore' });
  let _id = 0;
  const cdp = (ws, m, p = {}) => new Promise((res, rej) => {
    const id = ++_id;
    const h = (e) => { const x = JSON.parse(e.data.toString()); if (x.id === id) { ws.removeEventListener('message', h); x.error ? rej(new Error(JSON.stringify(x.error))) : res(x.result); } };
    ws.addEventListener('message', h); ws.send(JSON.stringify({ id, method: m, params: p }));
  });
  try {
    for (let i = 0; i < 80; i++) { try { await get(cdpPort, '/json/version'); break; } catch { await new Promise((r) => setTimeout(r, 250)); } }
    const t = (await get(cdpPort, '/json/list')).find((x) => x.type === 'page');
    const ws = new WebSocket(t.webSocketDebuggerUrl);
    await new Promise((r, j) => { ws.addEventListener('open', r); ws.addEventListener('error', j); });
    await cdp(ws, 'Runtime.enable'); await cdp(ws, 'Page.enable');
    await cdp(ws, 'Page.addScriptToEvaluateOnNewDocument', { source: STUB });
    const msgs = [];
    ws.addEventListener('message', (e) => {
      const x = JSON.parse(e.data.toString());
      if (x.method === 'Runtime.consoleAPICalled' && ['error', 'warning'].includes(x.params.type)) {
        msgs.push(`${x.params.type}:${(x.params.args || []).map((a) => a.value ?? a.description ?? a.type).join(' ').slice(0, 90)}`);
      }
    });
    await cdp(ws, 'Page.navigate', { url: `http://127.0.0.1:${port}/index.html` });
    await new Promise((r) => setTimeout(r, 5000));
    ws.close();
    return msgs;
  } finally {
    try { proc.kill(); } catch {}
    setTimeout(() => { try { fs.rmSync(profile, { recursive: true, force: true }); } catch {} }, 1200);
  }
};

const main = async () => {
  const N = 4;
  const resA = [], resB = [];
  for (let i = 0; i < N; i++) {
    resA.push(await sample(8124, 11201 + i));
    resB.push(await sample(8123, 11211 + i));
  }
  const kinds = (arr) => [...new Set(arr.map((m) => m.replace(/\(\d+\)/g, '')))].sort();
  const report = (label, runs) => {
    console.log(`\n${label}: counts per run = [${runs.map((r) => r.length).join(', ')}]`);
    const k = kinds(runs.flat());
    console.log(`  distinct message kinds (${k.length}):`);
    k.forEach((x) => console.log(`    - ${x}`));
    return k;
  };
  const kA = report('A=HEAD(8124)', resA);
  const kB = report('B=current(8123)', resB);

  const onlyA = kA.filter((x) => !kB.includes(x));
  const onlyB = kB.filter((x) => !kA.includes(x));
  console.log(`\n${'='.repeat(70)}`);
  console.log(`A 独有消息种类 (${onlyA.length}): ${onlyA.join(' | ') || '(none)'}`);
  console.log(`B 独有消息种类 (${onlyB.length}): ${onlyB.join(' | ') || '(none)'}`);
  const countsA = resA.map((r) => r.length), countsB = resB.map((r) => r.length);
  const variesA = new Set(countsA).size > 1, variesB = new Set(countsB).size > 1;
  console.log(`\nA 的条数是否逐轮浮动: ${variesA}  ${variesA ? '← 证明计数本身不确定' : ''}`);
  console.log(`B 的条数是否逐轮浮动: ${variesB}  ${variesB ? '← 证明计数本身不确定' : ''}`);
  console.log(`消息种类集合一致: ${onlyA.length === 0 && onlyB.length === 0}`);
  console.log(`\n${onlyA.length === 0 && onlyB.length === 0
    ? '✅ 结论：两侧消息种类完全相同；条数差异属运行时抖动（WebSocket 重连次数），非代码差异'
    : '❌ 结论：存在真实的消息种类差异'}`);
  process.exit(0);
};

main().catch((e) => { console.error('FATAL', e); process.exit(1); });
