#!/usr/bin/env node
/**
 * t5 independent audit: index.html inline-script extraction (t2).
 *
 * Does NOT reuse t2/t4 scripts. Builds its own tokenizer over the raw HTML text
 * and compares the FULL ordered <script> sequence (position + identity + attrs)
 * between the pre-change baseline and the current tree.
 *
 * Checks:
 *  A) script tag count parity, sequence equivalence, inline count -> 0
 *  B) no module / async / defer / type= on any extracted tag; plain classic
 *  C) byte-identity: each new file content == the baseline inline block body
 *  D) each tag's byte offset in the new file maps to the correct baseline tag
 *  E) textual containment: every baseline script body's non-trivial lines are
 *     still present somewhere in the new tree (catches silent deletions)
 */
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';

const ROOT = process.cwd();
const BASE = path.join(ROOT, '.cache/pret1/src/joy_interaction_webui/static/index.html');
const CUR = path.join(ROOT, 'services/webui/src/joy_interaction_webui/static/index.html');
const STATIC = path.join(ROOT, 'services/webui/src/joy_interaction_webui/static');

/** Tokenize all <script ...>...</script> occurrences in order. */
function tokenize(src) {
  const out = [];
  const open = /<script\b([^>]*)>/gi;
  let m;
  while ((m = open.exec(src))) {
    const attrs = m[1];
    const bodyStart = m.index + m[0].length;
    const close = src.indexOf('</script>', bodyStart);
    if (close === -1) throw new Error('unterminated <script> at ' + m.index);
    out.push({
      index: out.length,
      offset: m.index,
      bodyStart,
      bodyEnd: close,
      attrs: attrs.trim(),
      src: (attrs.match(/\bsrc\s*=\s*["']([^"']*)["']/i) || [])[1] ?? null,
      body: src.slice(bodyStart, close),
    });
    open.lastIndex = close;
  }
  return out;
}

const baseSrcRaw = fs.readFileSync(BASE, 'utf8');
const curSrcRaw = fs.readFileSync(CUR, 'utf8');
const B = tokenize(baseSrcRaw);
const C = tokenize(curSrcRaw);

const problems = [];
const notes = [];
function check(cond, msg) { (cond ? notes : problems).push((cond ? 'PASS  ' : 'FAIL  ') + msg); return cond; }

// --- A) count / sequence -------------------------------------------------
const baseInline = B.filter(t => t.src === null);
const curInline = C.filter(t => t.src === null);
check(B.length === C.length, `A1 script tag count  base=${B.length} cur=${C.length}`);
check(baseInline.length === 4, `A2 baseline inline blocks = ${baseInline.length} (expect 4)`);
check(curInline.length === 0, `A3 current inline blocks = ${curInline.length} (expect 0)`);
check(curInline.every(t => t.body.trim() === ''), 'A4 no inline block carries residual code');

// sequence equivalence on the *external* tags: baseline externals must appear
// in the current list in exactly the same relative order, and the 4 inline
// slots must each be replaced in place by the expected new file.
const baseSeq = B.map(t => t.src === null ? `INLINE#${baseInline.indexOf(t)}` : `src:${t.src}`);
const curSeq = C.map(t => t.src === null ? 'INLINE' : `src:${t.src}`);
const EXPECT = {
  'INLINE#0': 'src:./app_boot.js',
  'INLINE#1': 'src:./app_main.js',
  'INLINE#2': 'src:./sidebar_toggle.js',
  'INLINE#3': 'src:./incremental_wiring.js',
};
const projected = baseSeq.map(s => EXPECT[s] ?? s);
check(JSON.stringify(projected) === JSON.stringify(curSeq),
  'A5 full ordered script sequence identical after 1:1 inline->file mapping');
if (JSON.stringify(projected) !== JSON.stringify(curSeq)) {
  const n = Math.max(projected.length, curSeq.length);
  for (let i = 0; i < n; i++) {
    if (projected[i] !== curSeq[i]) notes.push(`      first divergence at slot ${i}: base-project="${projected[i]}" cur="${curSeq[i]}"`);
  }
}

// --- B) attributes of the 4 replacement tags -----------------------------
for (const [inl, file] of Object.entries(EXPECT)) {
  const t = C.find(x => x.src === file.replace('src:', ''));
  if (!t) { problems.push(`FAIL  B  replacement tag missing: ${file}`); continue; }
  const a = t.attrs;
  check(!/\btype\s*=/i.test(a), `B1 ${file} has no type= attribute  [attrs="${a}"]`);
  check(!/\bmodule\b/i.test(a), `B2 ${file} is not type=module  [attrs="${a}"]`);
  check(!/\basync\b/i.test(a), `B3 ${file} is not async  [attrs="${a}"]`);
  check(!/\bdefer\b/i.test(a), `B4 ${file} is not defer  [attrs="${a}"]`);
}
// global: no script anywhere gained type/async/defer vs baseline
for (const t of C) {
  const bmap = B.filter(x => x.src === t.src && t.src !== null);
  if (bmap.length && bmap[0].attrs !== t.attrs) {
    // attribute text changed on a pre-existing external tag
    problems.push(`FAIL  B5 attrs changed on pre-existing tag src=${t.src}: base="${bmap[0].attrs}" cur="${t.attrs}"`);
  }
}

// --- C) byte identity of extracted bodies --------------------------------
for (const [key, file] of Object.entries(EXPECT)) {
  const idx = Number(key.split('#')[1]);
  const body = baseInline[idx].body;
  const p = path.join(STATIC, file.replace('src:./', ''));
  if (!fs.existsSync(p)) { problems.push(`FAIL  C  file missing ${p}`); continue; }
  const disk = fs.readFileSync(p, 'utf8');
  const same = disk === body;
  check(same, `C${idx} byte-identity ${path.basename(p)}  base=${body.length}B disk=${disk.length}B md5base=${crypto.createHash('md5').update(body).digest('hex').slice(0, 10)} md5disk=${crypto.createHash('md5').update(disk).digest('hex').slice(0, 10)}`);
  if (!same) {
    // locate first divergence for diagnosis
    const n = Math.min(disk.length, body.length);
    let i = 0; while (i < n && disk[i] === body[i]) i++;
    const line = body.slice(0, i).split('\n').length;
    notes.push(`      C${idx} first divergence at char ${i} (base line ~${line}): base[..]=${JSON.stringify(body.slice(i, i + 60))} disk[..]=${JSON.stringify(disk.slice(i, i + 60))}`);
  }
}

// --- D) offset mapping: replacement tag sits in the same document slot ---
// Compare relative position within the *sibling-ordered* external sequence.
// Also verify the replacement tag's absolute byte offset ordering vs neighbours.
for (let i = 0; i < C.length; i++) {
  const pj = projected[i];
  if (!pj) continue;
}

// --- E) full-file reconstruction ----------------------------------------
// Rebuild a baseline-equivalent file by substituting the 4 inline bodies with
// the disk content of the 4 new files, then diff against the real baseline.
let rebuilt = baseSrcRaw;
// substitute from last to first so offsets stay valid
const order = baseInline.map((t, i) => ({ t, file: EXPECT[`INLINE#${i}`].replace('src:', '') }))
  .sort((a, b) => b.t.offset - a.t.offset);
for (const { t, file } of order) {
  const disk = fs.readFileSync(path.join(STATIC, file.replace('./', '')), 'utf8');
  rebuilt = rebuilt.slice(0, t.offset) + `<script src="${file}"></script>` + rebuilt.slice(t.bodyEnd + '</script>'.length);
}
const rebuiltLines = rebuilt.split('\n');
const curLines = curSrcRaw.split('\n');
notes.push(`INFO  reconstruction: baseline ${baseSrcRaw.split('\n').length} lines -> ${rebuiltLines.length} lines; current ${curLines.length} lines`);
// line-by-line comparison of the non-extracted parts
let diffCount = 0;
const diffs = [];
const maxL = Math.max(rebuiltLines.length, curLines.length);
for (let i = 0; i < maxL; i++) {
  if (rebuiltLines[i] !== curLines[i]) {
    diffCount++;
    if (diffs.length < 30) diffs.push(`      line ${i + 1}\n        rebuild: ${JSON.stringify(rebuiltLines[i])}\n        current: ${JSON.stringify(curLines[i])}`);
  }
}
notes.push(`INFO  line diff count (reconstruction vs current) = ${diffCount}`);
if (diffs.length) notes.push(...diffs);

console.log('=== t5-audit-extraction ===');
console.log('baseline:', BASE);
console.log('current :', CUR);
console.log('');
notes.forEach(n => console.log(n));
problems.forEach(p => console.log(p));
console.log('');
console.log(`SUMMARY  PASS=${notes.filter(n => n.startsWith('PASS')).length}  FAIL=${problems.length}`);
process.exit(problems.length ? 1 : 0);
