#!/usr/bin/env node
/**
 * t4 independent runtime verifier — isolated Chrome instance PER SIDE.
 *
 * Side A = HEAD baseline            (http://127.0.0.1:8124)
 * Side B = current workstream       (http://127.0.0.1:8123)
 *
 * Covers the contract items that must not rely on t1/t2's own scripts:
 *   1. console + pageerror, collected from a brand-new Chrome per side
 *      (stub /api/services/extended-status and /v1/namespaces ONCE, at document
 *       start, via Page.addScriptToEvaluateOnNewDocument, so the two sides see
 *       byte-identical network conditions)
 *   2. global reachability: >=20 identifiers, evaluated via new Function()
 *      (cross-script scope) — typeof + type, A/B compared
 *   3. wiki badge click -> #wikiPanel loses `cat-hidden` and is really visible
 *   4. XSS regression on the namespace-name path AND the error-message path
 *      (the real vulnerability of t3): img count must stay 0, onerror must not fire
 */
import http from 'node:http';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const IDENTS = [
  'vlmHistory', 'lastText', 'promptEditor', 'settings', 'websocket', 'sessionId',
  'showSettingsPanel', 'JoySettingsNav', 'JoyWs', 'JoyI18n', 'JoyRender', 'JoySanitize',
  'JoyWiki', 'llmReplyQueue', 'installLlmReplyHandler', 'connectWebSocket',
  'detectServices', 'fetchModels', 'renderVlmHistory', 'promptText',
  'videoElement', 'resultText', 'getVlmDisplayText', 'syncSpeechButtons',
];

// Stub installed BEFORE any page script runs: deterministic backend for both sides.
const STUB = `
(() => {
  const XSS = '<img src=x onerror="window.__xssFired=(window.__xssFired||0)+1">';
  window.__xssFired = 0;
  window.__stub = { hit: [] };
  const realFetch = window.fetch.bind(window);
  window.fetch = function (input, init) {
    const url = (typeof input === 'string') ? input : (input && input.url) || '';
    window.__stub.hit.push(url);
    if (url.includes('/api/services/extended-status')) {
      return Promise.resolve(new Response(JSON.stringify({
        memory: { enabled: true, reachable: true, ok: true, reason: 'stub-ok' },
        wiki:   { enabled: true, reachable: false, ok: false, reason: 'stub-down' }
      }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    }
    if (url.includes('/v1/namespaces') && !url.includes('DELETE')) {
      return Promise.resolve(new Response(JSON.stringify({
        namespaces: [{ namespace: XSS, blocks: 3, indexed: 3 }]
      }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    }
    if (url.includes('/v1/providers/health')) {
      return Promise.resolve(new Response(JSON.stringify({}), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    }
    return realFetch(input, init);
  };
})();
`;

const mkChrome = (cdpPort) => {
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), `t4-${cdpPort}-`));
  const proc = spawn(CHROME, ['--headless=new', '--disable-gpu', '--hide-scrollbars',
    `--remote-debugging-port=${cdpPort}`, `--user-data-dir=${profile}`,
    '--window-size=1600,1000', 'about:blank'], { stdio: 'ignore' });
  return { proc, profile, cdpPort };
};

const get = (port, p) => new Promise((res, rej) => {
  http.get({ host: '127.0.0.1', port, path: p }, (r) => {
    let d = ''; r.on('data', (c) => d += c); r.on('end', () => res(JSON.parse(d)));
  }).on('error', rej);
});

const probe = async (port, cdpPort, label) => {
  const { proc, profile } = mkChrome(cdpPort);
  let _id = 0;
  const cdp = (ws, m, p = {}) => new Promise((res, rej) => {
    const id = ++_id;
    const h = (e) => { const x = JSON.parse(e.data.toString()); if (x.id === id) { ws.removeEventListener('message', h); x.error ? rej(new Error(JSON.stringify(x.error))) : res(x.result); } };
    ws.addEventListener('message', h); ws.send(JSON.stringify({ id, method: m, params: p }));
  });
  try {
    for (let i = 0; i < 80; i++) { try { await get(cdpPort, '/json/version'); break; } catch { await new Promise((r) => setTimeout(r, 300)); } }
    const t = (await get(cdpPort, '/json/list')).find((x) => x.type === 'page');
    const ws = new WebSocket(t.webSocketDebuggerUrl);
    await new Promise((r, j) => { ws.addEventListener('open', r); ws.addEventListener('error', j); });
    await cdp(ws, 'Runtime.enable'); await cdp(ws, 'Page.enable');
    await cdp(ws, 'Page.addScriptToEvaluateOnNewDocument', { source: STUB });

    const consoleMsgs = [], pageErrors = [];
    ws.addEventListener('message', (e) => {
      const x = JSON.parse(e.data.toString());
      if (x.method === 'Runtime.consoleAPICalled' && ['error', 'warning'].includes(x.params.type)) {
        consoleMsgs.push({ type: x.params.type, text: (x.params.args || []).map((a) => a.value ?? a.description ?? a.type).join(' ').slice(0, 200) });
      }
      if (x.method === 'Runtime.exceptionThrown') {
        const d = x.params.exceptionDetails;
        pageErrors.push({ text: d.text, desc: (d.exception?.description || '').slice(0, 220) });
      }
    });

    await cdp(ws, 'Emulation.setDeviceMetricsOverride', { width: 1600, height: 1000, deviceScaleFactor: 1, mobile: false });
    await cdp(ws, 'Page.navigate', { url: `http://127.0.0.1:${port}/index.html` });
    await new Promise((r) => setTimeout(r, 5000));   // let pollExtendedStatus (1s tick) land

    const ev = async (e) => {
      const r = await cdp(ws, 'Runtime.evaluate', { expression: e, returnByValue: true, awaitPromise: true });
      if (r.exceptionDetails) return { __exc: String(r.exceptionDetails.exception?.description || '').slice(0, 300) };
      return r.result.value;
    };

    // ---- reachability from a fresh cross-script scope ----
    const reach = JSON.parse(await ev(`JSON.stringify((() => {
      const out = {};
      for (const n of ${JSON.stringify(IDENTS)}) {
        let t, ty = null;
        try { t = new Function('return typeof ' + n)(); } catch (e) { t = 'THROWS:' + e.name; }
        if (t !== 'undefined') {
          try { ty = typeof new Function('return ' + n)(); } catch (e) { ty = '<' + e.name + '>'; }
        }
        out[n] = { t, ty };
      }
      return out;
    })())`));

    const domIds = JSON.parse(await ev(`JSON.stringify([...document.querySelectorAll('[id]')].map(e=>e.id))`));

    // ---- wiki badge click ----
    const badge = JSON.parse(await ev(`JSON.stringify((() => {
      const p = document.getElementById('wikiPanel');
      const b = document.getElementById('wikiBadge');
      if (!p || !b) return { err: 'missing panel or badge' };
      const before = { hidden: p.classList.contains('cat-hidden'), display: getComputedStyle(p).display };
      b.click();
      const r = p.getBoundingClientRect();
      return {
        before,
        after: { hidden: p.classList.contains('cat-hidden'), display: getComputedStyle(p).display, w: r.width, h: r.height },
        trulyVisible: !p.classList.contains('cat-hidden') && getComputedStyle(p).display !== 'none' && r.width > 0 && r.height > 0,
      };
    })())`));

    // ---- XSS: namespace-name path (list rows) ----
    const xssList = JSON.parse(await ev(`(async () => {
      const imgBefore = document.querySelectorAll('img').length;
      window.__xssFired = 0;
      window.JoyWiki.loadNamespaces();
      await new Promise(r => setTimeout(r, 900));
      const list = document.getElementById('namespaceList');
      return JSON.stringify({
        imgBefore, imgAfter: document.querySelectorAll('img').length,
        fired: window.__xssFired,
        listHtml: (list ? list.innerHTML : '').slice(0, 300),
        listText: (list ? list.textContent : '').slice(0, 200),
        imgInList: list ? list.querySelectorAll('img').length : -1,
      });
    })()`));

    // ---- XSS: error-message path (the real t3 vulnerability) ----
    const xssErr = JSON.parse(await ev(`(async () => {
      const realFetch = window.fetch;
      window.fetch = function (input, init) {
        const url = (typeof input === 'string') ? input : (input && input.url) || '';
        if (url.includes('/v1/namespaces')) {
          return Promise.resolve(new Response(JSON.stringify({
            error: '<img src=x onerror="window.__xssFired=(window.__xssFired||0)+99">'
          }), { status: 500, headers: { 'Content-Type': 'application/json' } }));
        }
        return realFetch(input, init);
      };
      const imgBefore = document.querySelectorAll('img').length;
      window.__xssFired = 0;
      window.JoyWiki.loadNamespaces();
      await new Promise(r => setTimeout(r, 900));
      window.fetch = realFetch;
      const list = document.getElementById('namespaceList');
      return JSON.stringify({
        imgBefore, imgAfter: document.querySelectorAll('img').length,
        fired: window.__xssFired,
        listHtml: (list ? list.innerHTML : '').slice(0, 300),
        imgInList: list ? list.querySelectorAll('img').length : -1,
      });
    })()`));

    ws.close();
    return { label, port, consoleMsgs, pageErrors, reach, domIds, badge, xssList, xssErr };
  } finally {
    try { proc.kill(); } catch {}
    setTimeout(() => { try { fs.rmSync(profile, { recursive: true, force: true }); } catch {} }, 1500);
  }
};

const main = async () => {
  const A = await probe(8124, 10991, 'A=HEAD-baseline');
  const B = await probe(8123, 10992, 'B=current');

  const dump = (S) => {
    console.log(`\n${'='.repeat(78)}\n${S.label}  (static server ${S.port})\n${'='.repeat(78)}`);
    console.log(`console error/warning: ${S.consoleMsgs.length}`);
    S.consoleMsgs.forEach((m) => console.log(`   [${m.type}] ${m.text}`));
    console.log(`pageerror (uncaught): ${S.pageErrors.length}`);
    S.pageErrors.forEach((m) => console.log(`   ${m.text} | ${m.desc}`));
    console.log(`live DOM ids: ${S.domIds.length}`);
    console.log(`wikiBadge click -> ${JSON.stringify(S.badge)}`);
    console.log(`XSS namespace-name path -> ${JSON.stringify(S.xssList)}`);
    console.log(`XSS error-message path  -> ${JSON.stringify(S.xssErr)}`);
  };
  dump(A); dump(B);

  console.log(`\n${'='.repeat(78)}\n[2] REACHABILITY  A=HEAD vs B=current   (>=20 identifiers)\n${'='.repeat(78)}`);
  let regress = 0, sameReach = 0, absent = 0, gained = 0;
  for (const n of IDENTS) {
    const a = A.reach[n], b = B.reach[n];
    const ar = a.t === 'function' || a.t === 'object' || a.t === 'string' || a.t === 'number';
    const br = b.t === 'function' || b.t === 'object' || b.t === 'string' || b.t === 'number';
    let tag;
    if (ar && !br) { tag = 'REGRESSION'; regress++; }
    else if (!ar && br) { tag = 'newly-reachable'; gained++; }
    else if (ar && br) { tag = 'same-reachable'; sameReach++; }
    else { tag = 'same-absent'; absent++; }
    const mark = tag === 'REGRESSION' ? '❌' : (tag === 'newly-reachable' ? '⚠️ ' : '  ');
    console.log(`${mark} ${n.padEnd(24)} A=${String(a.t).padEnd(9)}/${String(a.ty).padEnd(9)} B=${String(b.t).padEnd(9)}/${String(b.ty).padEnd(9)} ${tag}`);
  }
  console.log(`\n标识符总数=${IDENTS.length}  两侧同可达=${sameReach}  两侧同缺失=${absent}  退化=${regress}  新增可达=${gained}`);

  console.log(`\n[3] console/pageerror 基线对比`);
  // Normalize away: line:col, the static-server PORT (differs by design), and
  // //127.0.0.1:<port>/ so only real message differences remain.
  const norm = (S) => S.consoleMsgs.map((m) => `${m.type}:${m.text
    .replace(/127\.0\.0\.1:\d+/g, '127.0.0.1:PORT')
    .replace(/:\d+:\d+/g, ':L')}`).sort();
  const nA = norm(A).join('\n'), nB = norm(B).join('\n');
  console.log(`A(HEAD) ${A.consoleMsgs.length} 条 / pageerror ${A.pageErrors.length}`);
  console.log(`B(curr) ${B.consoleMsgs.length} 条 / pageerror ${B.pageErrors.length}`);
  console.log(`规范化后完全一致: ${nA === nB}`);
  if (nA !== nB) {
    const setA = new Set(norm(A)), setB = new Set(norm(B));
    console.log('  仅 A 有:', [...setA].filter((x) => !setB.has(x)).join(' | ') || '(none)');
    console.log('  仅 B 有:', [...setB].filter((x) => !setA.has(x)).join(' | ') || '(none)');
  }

  console.log(`\n[DOM id] A=${A.domIds.length}  B=${B.domIds.length}`);
  // The correct hard criterion is NOT "A ⊆ B": side A is the pristine HEAD
  // worktree, which predates earlier (already-merged) work in this branch that
  // removed ids before t1/t2 began. The contract's hard criterion is
  // "ids removed by t1+t2 = 0", measured against the PRE-t1/t2 snapshot
  // (scripts/.invariants.json). Both are reported so neither is hidden.
  const snapPath = 'scripts/.invariants.json';
  let snapIds = null;
  if (fs.existsSync(snapPath)) {
    try { snapIds = JSON.parse(fs.readFileSync(snapPath, 'utf8'))._ids || []; } catch {}
  }
  const sA = new Set(A.domIds), sB = new Set(B.domIds);
  const rem = [...sA].filter((x) => !sB.has(x));
  console.log(`  A 有而 B 无 (${rem.length}) —— 含 t1/t2 之前就已删除的历史 id`);
  if (snapIds) {
    const snapSet = new Set(snapIds);
    const remByCheckpoint = rem.filter((x) => snapSet.has(x));
    const addByCheckpoint = snapIds.filter((x) => !sB.has(x));
    const addNew = [...sB].filter((x) => !snapSet.has(x));
    console.log(`  ★ 硬判据（对 pre-t1/t2 快照 ${snapPath}）:`);
    console.log(`     删除的 id = ${addByCheckpoint.length}  ${addByCheckpoint.join(', ') || '(none)'}`);
    console.log(`     新增的 id = ${addNew.length}`);
    console.log(`     这 ${rem.length} 个 A-only id 中有 ${remByCheckpoint.length} 个属于 t1/t2 的删除范围`);
    console.log(`     （其余 ${rem.length - remByCheckpoint.length} 个在快照中已不存在 = 更早已删，非本次回归）`);
  } else {
    console.log(`  (未找到 ${snapPath}，无法做 checkpoint 归因)`);
  }

  console.log(`\n${'='.repeat(78)}\nVERDICT GATES\n${'='.repeat(78)}`);
  const gates = [
    ['pageerror 为空（两侧）', A.pageErrors.length === 0 && B.pageErrors.length === 0],
    ['console 与基线一致', nA === nB],
    ['可达性退化 = 0', regress === 0],
    ['≥20 标识符已覆盖', IDENTS.length >= 20],
    ['wikiPanel 点击后真实可见（B）', B.badge.trulyVisible === true],
    ['XSS 列表路径 img=0 且未触发（B）', B.xssList.imgAfter === 0 && B.xssList.fired === 0],
    ['XSS 错误路径 img=0 且未触发（B）', B.xssErr.imgAfter === 0 && B.xssErr.fired === 0],
    ['t1/t2 未删除任何 pre-t1/t2 快照中的 id', (() => {
      if (!snapIds) return false;
      const snapSet = new Set(snapIds);
      return snapIds.every((x) => sB.has(x));
    })()],
    ['XSS 错误路径：基线真实存在漏洞（A 侧 img>0）—— 证明该用例有效', A.xssErr.imgAfter > 0],
    ['wikiPanel 点击（A=HEAD 基线）确实失效 —— 证明 t1 修复非冗余', A.badge.trulyVisible === false],
  ];
  let failed = 0;
  for (const [n, ok] of gates) { console.log(`  ${ok ? '✅' : '❌'} ${n}`); if (!ok) failed++; }
  console.log(`\n${failed === 0 ? '✅ 全部闸门通过' : `❌ ${failed} 个闸门未通过`}`);
  process.exit(0);
};

main().catch((e) => { console.error('FATAL', e); process.exit(1); });
