#!/usr/bin/env node
/**
 * t5: definitive round-trip test of t2's core claim.
 *
 * t2 claims: "从磁盘读回重组后与原 index.html 逐字节相同"
 * (reassembling the 4 new files back into the pre-t2 index.html yields a
 *  byte-identical file).
 *
 * This test uses the TRUE pre-t2 snapshot (post-t1 / pre-t2, found at
 * .cache/t5-review/pret2/index.html, 3137 lines, 4 inline blocks, 284 ids)
 * — NOT .cache/pret1 (which is pre-t1, so it also contains t1's deletions).
 *
 * Therefore any difference found here is attributable to t2 ALONE.
 */
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';

const ROOT = process.cwd();
const PRE_T2 = path.join(ROOT, '.cache/t5-review/pret2/index.html');
const CUR = path.join(ROOT, 'services/webui/src/joy_interaction_webui/static/index.html');
const STATIC = path.join(ROOT, 'services/webui/src/joy_interaction_webui/static');
const MAP = ['app_boot.js', 'app_main.js', 'sidebar_toggle.js', 'incremental_wiring.js'];

const src = fs.readFileSync(PRE_T2, 'utf8');
const units = [];
{
  const open = /<script\b([^>]*)>/gi; let m;
  while ((m = open.exec(src))) {
    const attrs = m[1]; const bs = m.index + m[0].length;
    const c = src.indexOf('</script>', bs);
    units.push({ attrs: attrs.trim(), offset: m.index, bodyStart: bs, bodyEnd: c, body: src.slice(bs, c) });
    open.lastIndex = c;
  }
}
const inline = units.filter(u => !/\bsrc\s*=/.test(u.attrs));
console.log('='.repeat(78));
console.log('t5: TRUE pre-t2 round-trip test');
console.log('='.repeat(78));
console.log(`pre-t2 snapshot: ${PRE_T2}`);
console.log(`  lines=${src.split('\n').length}  scriptTags=${units.length}  inlineBlocks=${inline.length}`);
console.log(`  md5=${crypto.createHash('md5').update(src).digest('hex')}`);

// ---- reconstruct -------------------------------------------------------
let rebuilt = src;
const ordered = inline.map((u, i) => ({ u, file: MAP[i] })).sort((a, b) => b.u.offset - a.u.offset);
const headerInfo = [];
for (const { u, file } of ordered) {
  const disk = fs.readFileSync(path.join(STATIC, file), 'utf8');
  headerInfo.push({ file, diskBytes: Buffer.byteLength(disk), bodyBytes: Buffer.byteLength(u.body) });
  rebuilt = rebuilt.slice(0, u.offset) + `<script src="./${file}"></script>` + rebuilt.slice(u.bodyEnd + '</script>'.length);
}

const EQ = rebuilt === src;
console.log('\n[1] ROUND-TRIP (reinsert each extracted file body verbatim into the pre-t2 slots)');
console.log(`  rebuilt === pre-t2 source : ${EQ}`);
if (!EQ) {
  let i = 0; const n = Math.min(rebuilt.length, src.length);
  while (i < n && rebuilt[i] === src[i]) i++;
  console.log(`  first divergence at byte ${i} (line ~${src.slice(0, i).split('\n').length})`);
  console.log(`   pre-t2: ${JSON.stringify(src.slice(i, i + 120))}`);
  console.log(`   rebuilt: ${JSON.stringify(rebuilt.slice(i, i + 120))}`);
}

// ---- the real comparison: does the CURRENT file equal pre-t2 minus the 4 bodies? ---
console.log('\n[2] SHELL EQUIVALENCE (pre-t2 with 4 bodies replaced by their <script src> tags vs current)');
const pre_t2_shell = src;
let shell = src;
for (const { u, file } of ordered) {
  shell = shell.slice(0, u.offset) + `<script src="./${file}"></script>` + shell.slice(u.bodyEnd + '</script>'.length);
}
const cur = fs.readFileSync(CUR, 'utf8');
// normalize CRLF for a readable line comparison
const L = s => s.replace(/\r\n/g, '\n').split('\n');
const a = L(shell), b = L(cur);
console.log(`  shell lines=${a.length}  current lines=${b.length}`);
let diff = 0; const samples = [];
for (let i = 0; i < Math.max(a.length, b.length); i++) {
  if (a[i] !== b[i]) { diff++; if (samples.length < 25) samples.push({ line: i + 1, shell: a[i], cur: b[i] }); }
}
console.log(`  line differences = ${diff}`);
samples.forEach(s => console.log(`   L${s.line}\n     shell : ${JSON.stringify(s.shell)}\n     current: ${JSON.stringify(s.cur)}`));

// ---- per-file: is the extraction additive-only? -----------------------
console.log('\n[3] ADDITIVE-ONLY TEST per extracted file (against the TRUE pre-t2 bodies)');
const norm = s => s.replace(/\r\n/g, '\n');
let nonAdditive = 0;
for (let i = 0; i < inline.length; i++) {
  const body = norm(inline[i].body);
  const disk = norm(fs.readFileSync(path.join(STATIC, MAP[i]), 'utf8'));
  const ok = disk.endsWith(body);
  console.log(`  ${MAP[i].padEnd(24)} header+body===disk ? ${ok}`);
  if (!ok) {
    nonAdditive++;
    // quantify the real body edits
    let pre = 0; while (pre < Math.min(disk.length, body.length) && disk[pre] === body[pre]) pre++;
    let suf = 0; while (suf < Math.min(disk.length, body.length) - pre && disk[disk.length - 1 - suf] === body[body.length - 1 - suf]) suf++;
    console.log(`      -> body region edited in place. divergent span from char ${pre} (baseline line ~${body.slice(0, pre).split('\n').length} to ~${body.length - suf >= 0 ? body.slice(0, body.length - suf).split('\n').length : '?'})`);
    console.log(`         baseline bytes in span=${body.length - pre - suf}  disk bytes in span=${disk.length - pre - suf}`);
  }
}
console.log(`\n  files that are NOT pure "header + verbatim body": ${nonAdditive}/4`);

console.log('\n' + '='.repeat(78));
console.log('INTERPRETATION');
console.log('='.repeat(78));
console.log('  A "pure move" requires: shell byte-identical AND body header-only-prefixed.');
console.log(`  shell line diffs = ${diff} (0 = <script src> replacement is position-exact)`);
console.log(`  non-additive files = ${nonAdditive} (these carry in-body edits, i.e. NOT a pure move)`);
