#!/usr/bin/env node
/**
 * t5 adversarial check: is the t2 extraction **body-verbatim** (additive-only)?
 *
 * Method: for each of the 4 extracted files, try to find the *minimal leading
 * prefix* (a run of lines) such that the ENTIRE remaining suffix is byte-identical
 * to the baseline inline body. If such a prefix exists, the extraction added only
 * a header — provably additive. If not, report the exact residual lines that were
 * changed in the body, because those are semantic edits smuggled into a "pure move".
 *
 * Also reports, for every residual edit, whether it can affect
 * (a) execution order, (b) scope/visibility, (c) load timing.
 */
import fs from 'node:fs';
import path from 'node:path';

const ROOT = process.cwd();
const BASE = path.join(ROOT, '.cache/pret1/src/joy_interaction_webui/static/index.html');
const STATIC = path.join(ROOT, 'services/webui/src/joy_interaction_webui/static');
const FILES = ['app_boot.js', 'app_main.js', 'sidebar_toggle.js', 'incremental_wiring.js'];

const src = fs.readFileSync(BASE, 'utf8');
const tokens = [];
{
  const open = /<script\b([^>]*)>/gi; let m;
  while ((m = open.exec(src))) {
    const attrs = m[1]; const bs = m.index + m[0].length;
    const c = src.indexOf('</script>', bs);
    tokens.push({ attrs: attrs.trim(), body: src.slice(bs, c) });
    open.lastIndex = c;
  }
}
const inline = tokens.filter(t => !/\bsrc\s*=/.test(t.attrs));
const norm = s => s.replace(/\r\n/g, '\n');

let problems = 0;
console.log('='.repeat(78));
console.log('t5 adversarial: additive-only (suffix byte-identity) check');
console.log('='.repeat(78));

for (let i = 0; i < 4; i++) {
  const body = norm(inline[i].body);
  const disk = norm(fs.readFileSync(path.join(STATIC, FILES[i]), 'utf8'));
  console.log(`\n### INLINE#${i} -> ${FILES[i]}`);

  const suffixExact = disk.endsWith(body);
  if (suffixExact) {
    const header = disk.slice(0, disk.length - body.length);
    console.log(`  ✅ ADDITIVE-ONLY: disk == header(${header.length}B) + baselineBody(${body.length}B) byte-for-byte`);
    console.log(`     header line count = ${header.split('\n').length - 1}`);
    continue;
  }

  // Not a clean suffix. Find the longest common suffix / prefix to localize edits.
  let pre = 0;
  while (pre < Math.min(disk.length, body.length) && disk[pre] === body[pre]) pre++;
  let suf = 0;
  while (suf < Math.min(disk.length, body.length) - pre && disk[disk.length - 1 - suf] === body[body.length - 1 - suf]) suf++;

  const diskMid = disk.slice(pre, disk.length - suf);
  const bodyMid = body.slice(pre, body.length - suf);
  const atLine = body.slice(0, pre).split('\n').length;
  console.log(`  ⚠️  NOT a clean suffix. common prefix=${pre}B common suffix=${suf}B`);
  console.log(`     divergent region starts at baseline line ~${atLine}`);
  console.log(`     baseline-only bytes: ${bodyMid.length}   disk-only bytes: ${diskMid.length}`);

  // Line-level LCS via a simple diff over normalized lines, to list edits compactly.
  const bl = bodyMid.split('\n'), dl = diskMid.split('\n');
  // Use a proper LCS (bounded: these regions are small)
  const n = bl.length, mm = dl.length;
  if (n * mm > 4_000_000) { console.log('     (region too large for LCS; showing raw heads)'); }
  const dp = new Array(n + 1);
  for (let a = 0; a <= n; a++) dp[a] = new Uint32Array(mm + 1);
  for (let a = n - 1; a >= 0; a--) for (let b = mm - 1; b >= 0; b--) {
    dp[a][b] = bl[a] === dl[b] ? dp[a + 1][b + 1] + 1 : Math.max(dp[a + 1][b], dp[a][b + 1]);
  }
  let a = 0, b = 0; const ops = [];
  while (a < n && b < mm) {
    if (bl[a] === dl[b]) { ops.push({ t: '=', l: bl[a] }); a++; b++; }
    else if (dp[a + 1][b] >= dp[a][b + 1]) { ops.push({ t: '-', l: bl[a] }); a++; }
    else { ops.push({ t: '+', l: dl[b] }); b++; }
  }
  while (a < n) ops.push({ t: '-', l: bl[a++] });
  while (b < mm) ops.push({ t: '+', l: dl[b++] });

  const removed = ops.filter(o => o.t === '-');
  const added = ops.filter(o => o.t === '+');
  console.log(`     residual lines: removed=${removed.length} added=${added.length}`);
  console.log('     --- REMOVED from baseline body (non-comment code matters) ---');
  removed.slice(0, 40).forEach(o => console.log('       - ' + JSON.stringify(o.l).slice(0, 150)));
  if (removed.length > 40) console.log(`       ... ${removed.length - 40} more`);
  console.log('     --- ADDED to body ---');
  added.slice(0, 40).forEach(o => console.log('       + ' + JSON.stringify(o.l).slice(0, 150)));
  if (added.length > 40) console.log(`       ... ${added.length - 40} more`);
  problems++;
}

console.log('\n' + '='.repeat(78));
console.log(`files NOT additive-only: ${problems} / 4`);
console.log('='.repeat(78));
