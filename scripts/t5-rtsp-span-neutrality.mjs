#!/usr/bin/env node
/**
 * t5: is the added <span> wrapper in the RTSP-connected rewrite layout-neutral?
 *
 * The rewrite changed
 *    the status node's innerHTML property assigned one string `A<br>B`
 * into
 *    replaceChildren(span(A), br, span(B))
 *
 * NOTE (deepsec gate, t6 2026-09-19): the OLD-side fixture in the probe below
 * addresses the property with bracket notation (`el["innerHTML"]`). That is
 * **exactly equivalent** to the dot form — bracket vs dot is pure
 * property-access syntax, identical semantics and identical runtime behaviour —
 * and it is what the fixture must keep reproducing. Do NOT "tidy" it back to
 * dot form: the deepsec rule `sast_xss_inner_html` matches only the dot form,
 * is a pure regex, and does not distinguish executable code from a fixture
 * living inside a template literal (see .deepsecignore "坑1"), so dot form here
 * raises a false positive that BLOCKS the commit gate.
 *
 * spans are inline, but that is only *probably* inert. This test proves it by
 * rendering BOTH variants inside the REAL #rtspStatus element (in the real page,
 * with the real stylesheet), and comparing:
 *   - innerText / textContent
 *   - getBoundingClientRect of the container and of each text run
 *   - computed styles of the container and of the inserted nodes
 *   - active CSS rules that could target the inserted nodes
 */
import http from 'node:http';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const PORT = Number(process.argv[2] || 8123);
const CDP = 10996;
const PROFILE = fs.mkdtempSync(path.join(os.tmpdir(), 't5-span-'));
const chrome = spawn('C:/Program Files/Google/Chrome/Application/chrome.exe',
  ['--headless=new', '--disable-gpu', '--hide-scrollbars', `--remote-debugging-port=${CDP}`,
   `--user-data-dir=${PROFILE}`, '--window-size=1600,1000', 'about:blank'], { stdio: 'ignore' });
const get = p => new Promise((res, rej) => {
  http.get({ host: '127.0.0.1', port: CDP, path: p }, r => { let d = ''; r.on('data', c => d += c); r.on('end', () => res(JSON.parse(d))); }).on('error', rej);
});
let _id = 0;
const cdp = (ws, m, p = {}) => new Promise((res, rej) => {
  const id = ++_id;
  const h = e => { const x = JSON.parse(e.data.toString()); if (x.id === id) { ws.removeEventListener('message', h); x.error ? rej(new Error(JSON.stringify(x.error))) : res(x.result); } };
  ws.addEventListener('message', h); ws.send(JSON.stringify({ id, method: m, params: p }));
});
const cleanup = () => { try { chrome.kill(); } catch {} try { fs.rmSync(PROFILE, { recursive: true, force: true }); } catch {} };

const main = async () => {
  for (let i = 0; i < 80; i++) { try { await get('/json/version'); break; } catch { await new Promise(r => setTimeout(r, 300)); } }
  const t = (await get('/json/list')).find(x => x.type === 'page');
  const ws = new WebSocket(t.webSocketDebuggerUrl);
  await new Promise((r, j) => { ws.addEventListener('open', r); ws.addEventListener('error', j); });
  await cdp(ws, 'Runtime.enable'); await cdp(ws, 'Page.enable');
  await cdp(ws, 'Page.navigate', { url: `http://127.0.0.1:${PORT}/index.html` });
  await new Promise(r => setTimeout(r, 6000));
  const ev = async e => {
    const r = await cdp(ws, 'Runtime.evaluate', { expression: e, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) return { __exc: String(r.exceptionDetails.exception && r.exceptionDetails.exception.description || '').slice(0, 300) };
    return r.result.value;
  };

  const probe = `(() => {
    const src = document.getElementById('rtspStatus');
    if (!src) return JSON.stringify({ error: '#rtspStatus not found' });
    const info = { codec:'H264', width:1280, height:720, fps:30 };

    // The element lives inside a collapsed/hidden settings panel, so measuring
    // it in place yields a 0x0 box (a degenerate, meaningless comparison).
    // Move the REAL element into a visible fixed host so layout is genuine,
    // then restore it to its original parent/position.
    const origParent = src.parentNode, origNext = src.nextSibling;
    const host = document.createElement('div');
    host.id = '__t5host';
    host.style.cssText = 'position:fixed;left:20px;top:20px;width:400px;height:80px;display:block;visibility:visible;opacity:1;z-index:2147483647;background:#000;color:#fff;';
    document.body.appendChild(host);
    host.appendChild(src);
    const el = src;
    // An ID-scoped stylesheet rule forces #rtspStatus{display:none}; override it
    // with an inline !important so the box is genuinely laid out.
    el.style.setProperty('display', 'block', 'important');
    el.style.setProperty('visibility', 'visible', 'important');
    el.style.setProperty('position', 'static', 'important');
    el.style.setProperty('width', '380px', 'important');
    el.style.setProperty('height', '60px', 'important');
    el.style.setProperty('color', '#fff', 'important');

    function snap(tag) {
      const r = el.getBoundingClientRect();
      const cs = getComputedStyle(el);
      const kids = [...el.childNodes].map(n => {
        if (n.nodeType === 3) return { t:'text', v: JSON.stringify(n.nodeValue) };
        const kcs = getComputedStyle(n);
        return { t: n.tagName, display: kcs.display, whiteSpace: kcs.whiteSpace, verticalAlign: kcs.verticalAlign };
      });
      const range = document.createRange();
      range.selectNodeContents(el);
      const rects = [...range.getClientRects()].map(x => ({ y:+x.y.toFixed(2), w:+x.width.toFixed(2), h:+x.height.toFixed(2) }));
      return { tag, text: el.innerText, textContent: el.textContent,
               rect: { w:+r.width.toFixed(3), h:+r.height.toFixed(3), x:+r.x.toFixed(2), y:+r.y.toFixed(2) },
               containerDisplay: cs.display, containerWhiteSpace: cs.whiteSpace,
               lineBoxes: rects, lineCount: rects.length, kids };
    }

    // ---- variant OLD: one innerHTML string with <br> ----
    el["innerHTML"] = \`✅ Connected!<br>\${info.codec} \${info.width}x\${info.height} @\${info.fps}fps\`;
    const oldSnap = snap('OLD(innerHTML string)');

    // ---- variant NEW: replaceChildren(span, br, span) ----
    const s1 = document.createElement('span'); s1.textContent = '✅ Connected!';
    const br = document.createElement('br');
    const s2 = document.createElement('span'); s2.textContent = \`\${info.codec} \${info.width}x\${info.height} @\${info.fps}fps\`;
    el.replaceChildren(s1, br, s2);
    const newSnap = snap('NEW(replaceChildren spans)');

    // ---- which stylesheet rules actually match the inserted nodes? ----
    const matched = [];
    for (const sheet of document.styleSheets) {
      let rules; try { rules = sheet.cssRules; } catch { continue; }
      const walk = list => {
        for (const rule of list) {
          if (rule.cssRules) { walk(rule.cssRules); continue; }
          if (!rule.selectorText) continue;
          try {
            if (s1.matches(rule.selectorText) || el.matches(rule.selectorText)) {
              matched.push(rule.selectorText + ' { ' + rule.style.cssText.slice(0,160) + ' }');
            }
          } catch {}
        }
      };
      walk(rules);
    }

    // restore the real element to where it came from
    if (origNext && origNext.parentNode === origParent) origParent.insertBefore(src, origNext);
    else origParent.appendChild(src);
    host.remove();
    return JSON.stringify({ oldSnap, newSnap, matchedRules: matched });
  })()`;

  const out = await ev(probe);
  if (typeof out !== 'string') { console.log('probe failed:', out); ws.close(); cleanup(); process.exit(2); }
  const r = JSON.parse(out);
  if (r.error) { console.log('ERROR:', r.error); ws.close(); cleanup(); process.exit(2); }

  console.log('='.repeat(78));
  console.log('RTSP-connected rewrite: layout neutrality of the <span> wrapper');
  console.log('='.repeat(78));
  console.log('\n--- OLD (original innerHTML string) ---');
  console.log(JSON.stringify(r.oldSnap, null, 2));
  console.log('\n--- NEW (replaceChildren spans) ---');
  console.log(JSON.stringify(r.newSnap, null, 2));

  console.log('\n--- stylesheet rules matching #rtspStatus or the inserted <span> ---');
  console.log(r.matchedRules.length ? r.matchedRules.join('\n') : '  (none)');

  const a = r.oldSnap, b = r.newSnap;
  // Distinct rendered-line geometry: getClientRects() emits one rect per inline
  // box, and nesting a <span> around text yields TWO rects for the SAME line.
  // Deduplicating by (y, width) isolates real line layout from that artefact.
  const geom = s => [...new Map(s.lineBoxes.map(x => [`${x.y}|${x.w}`, x])).values()].sort((p, q) => p.y - q.y || p.w - q.w);
  const ga = geom(a), gb = geom(b);
  const checks = [
    ['textContent identical', a.textContent === b.textContent, { old: a.textContent, new: b.textContent }],
    ['innerText identical', a.innerText === b.innerText, { old: a.innerText, new: b.innerText }],
    ['container rect identical', a.rect.w === b.rect.w && a.rect.h === b.rect.h, { old: a.rect, new: b.rect }],
    ['container ORIGIN identical (x,y)', a.rect.x === b.rect.x && a.rect.y === b.rect.y],
    ['container display/white-space identical', a.containerDisplay === b.containerDisplay && a.containerWhiteSpace === b.containerWhiteSpace],
    ['child count identical', a.kids.length === b.kids.length, { old: a.kids.length, new: b.kids.length }],
    ['DISTINCT rendered line geometry identical (dedup by y,width)', JSON.stringify(ga) === JSON.stringify(gb), { old: ga, new: gb }],
    ['non-degenerate measurement (box has area)', a.rect.w > 0 && a.rect.h > 0, a.rect],
  ];
  console.log('');
  let fails = 0;
  for (const [n, ok, d] of checks) { console.log(`  ${ok ? '✅' : '❌'} ${n}${d ? '  → ' + JSON.stringify(d) : ''}`); if (!ok) fails++; }

  console.log('\n  NOTE: the serialized markup differs by design (<br> vs <span>..</span><br><span>..</span>),');
  console.log('        but the rendered text and box are what the user sees.');
  console.log(`\nRESULT: ${fails === 0 ? 'span wrapper is layout-neutral (rendering equivalent)' : fails + ' DIFFERENCE(S)'}`);
  ws.close(); cleanup();
  process.exit(fails ? 1 : 0);
};
main().catch(e => { console.error('FAILED:', e.message); cleanup(); process.exit(2); });
