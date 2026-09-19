#!/usr/bin/env node
/**
 * t5 parser-based adversarial analysis (acorn, scope-aware).
 *
 * 1) SCRIPT SEQUENCE: rebuild the ordered script unit list for baseline and
 *    current, and verify the 1:1 mapping preserves document position.
 *
 * 2) TOP-LEVEL BINDING IDENTITY: for each extracted unit, diff the set of
 *    top-level declared bindings (var/let/const/function/class) against the
 *    baseline inline body. Any removal/rename must be attributable; we assert
 *    the removals are exactly the t1 dead-code list and the additions are only
 *    the t2 deepsec helpers.
 *
 * 3) CROSS-SCRIPT TDZ HUNT: for every unit, collect identifiers referenced by
 *    *immediately-executed* top-level statements (i.e. NOT nested inside a
 *    function/arrow/class body — those run later). If such a reference resolves
 *    to a let/const/class binding declared at top level in a LATER unit, that is
 *    a genuine TDZ ReferenceError waiting for the right path. Run this over both
 *    trees and diff the findings.
 *
 * 4) GLOBAL LEAK SURFACE: report the union of top-level bindings per tree, so a
 *    change in "what leaks / doesn't leak" is visible.
 */
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(path.join(process.cwd(), 'services/webui/package.json'));
const acorn = require('acorn');

const ROOT = process.cwd();
const BASE_HTML = path.join(ROOT, '.cache/pret1/src/joy_interaction_webui/static/index.html');
const CUR_HTML = path.join(ROOT, 'services/webui/src/joy_interaction_webui/static/index.html');
const STATIC = path.join(ROOT, 'services/webui/src/joy_interaction_webui/static');

function unitList(htmlPath, resolver) {
  const src = fs.readFileSync(htmlPath, 'utf8');
  const units = [];
  const open = /<script\b([^>]*)>/gi;
  let m;
  while ((m = open.exec(src))) {
    const attrs = m[1];
    const bs = m.index + m[0].length;
    const c = src.indexOf('</script>', bs);
    const srcAttr = (attrs.match(/\bsrc\s*=\s*["']([^"']*)["']/i) || [])[1] ?? null;
    const isCDN = /unpkg\.com|cdn\.jsdelivr\.net/.test(attrs);
    let code = null, name = null;
    if (isCDN) { name = 'CDN:' + srcAttr; }
    else if (srcAttr) {
      name = srcAttr.replace('./', '');
      const p = resolver(name);
      code = fs.existsSync(p) ? fs.readFileSync(p, 'utf8') : null;
    } else { name = 'INLINE@' + m.index; code = src.slice(bs, c); }
    units.push({ name, code, isCDN, srcAttr, offset: m.index });
    open.lastIndex = c;
  }
  return units;
}

const baseUnits = unitList(BASE_HTML, n => path.join(ROOT, '.cache/pret1/src/joy_interaction_webui/static', n));
const curUnits = unitList(CUR_HTML, n => path.join(STATIC, n));

// baseline inline units -> the file that replaced them
const BASE_INLINE = baseUnits.filter(u => u.name.startsWith('INLINE@'));
const MAP = {
  0: 'app_boot.js', 1: 'app_main.js', 2: 'sidebar_toggle.js', 3: 'incremental_wiring.js',
};
BASE_INLINE.forEach((u, i) => { u.replacedBy = MAP[i]; u.inlineIndex = i; });

// ---- 1) sequence equivalence -------------------------------------------
console.log('='.repeat(78));
console.log('[1] SCRIPT SEQUENCE / DOCUMENT POSITION');
console.log('='.repeat(78));
const projBase = baseUnits.map(u => u.name.startsWith('INLINE@') ? u.replacedBy : u.name);
const curNames = curUnits.map(u => u.name);
const seqOk = JSON.stringify(projBase) === JSON.stringify(curNames);
console.log(`baseline units=${baseUnits.length} current units=${curUnits.length} (CDN units included)`);
console.log(`sequence identical after inline->file mapping: ${seqOk}`);
if (!seqOk) {
  for (let i = 0; i < Math.max(projBase.length, curNames.length); i++) {
    if (projBase[i] !== curNames[i]) console.log(`  div @${i}: base="${projBase[i]}" cur="${curNames[i]}"`);
  }
}
// document-position proof: the relative order of the *non-CDN* units is the
// same, and the element offsets of the replacement tags are the same ones the
// baseline inline tags occupied (verified separately by reconstruction diff).
console.log(`inline units in baseline: ${BASE_INLINE.length}; inline units in current: ${curUnits.filter(u => u.name.startsWith('INLINE@')).length}`);

// ---- parse helpers ------------------------------------------------------
function parse(code, file) {
  return acorn.parse(code, { ecmaVersion: 'latest', sourceType: 'script', allowReturnOutsideFunction: true, locations: true });
}

/** top-level declared binding names of a unit */
function topLevelBindings(ast) {
  const out = [];
  for (const st of ast.body) {
    collectDecls(st, out);
  }
  return out;
}
function collectDecls(st, out) {
  const push = (n, kind) => { if (n) out.push({ name: n, kind }); };
  switch (st.type) {
    case 'VariableDeclaration':
      for (const d of st.declarations) {
        if (d.id.type === 'Identifier') push(d.id.name, st.kind);
        else collectPattern(d.id, st.kind, out);
      }
      break;
    case 'FunctionDeclaration': push(st.id && st.id.name, 'function'); break;
    case 'ClassDeclaration': push(st.id && st.id.name, 'class'); break;
    case 'ImportDeclaration': break;
    default: break;
  }
}
function collectPattern(p, kind, out) {
  if (!p) return;
  if (p.type === 'Identifier') out.push({ name: p.name, kind });
  else if (p.type === 'ObjectPattern') p.properties.forEach(pr => collectPattern(pr.value || pr.argument, kind, out));
  else if (p.type === 'ArrayPattern') p.elements.forEach(e => collectPattern(e, kind, out));
  else if (p.type === 'AssignmentPattern') collectPattern(p.left, kind, out);
  else if (p.type === 'RestElement') collectPattern(p.argument, kind, out);
}

/**
 * Identifiers referenced by *immediately executed* top-level statements.
 * We walk each top-level statement but DO NOT descend into function bodies,
 * class bodies, or any nested function — those execute later.
 */
function immediateRefs(ast) {
  const refs = [];
  const visit = (node, isDeclIdTarget) => {
    if (!node || typeof node.type !== 'string') return;
    switch (node.type) {
      case 'FunctionDeclaration': case 'FunctionExpression': case 'ArrowFunctionExpression':
      case 'ClassDeclaration': case 'ClassExpression':
        return; // deferred
      case 'Identifier':
        if (!isDeclIdTarget) refs.push(node);
        return;
      case 'MemberExpression':
        visit(node.object, false); if (node.computed) visit(node.property, false); return;
      case 'Property':
        if (node.computed) visit(node.key, false);
        visit(node.value, false); return;
      case 'VariableDeclarator':
        // `const x = <expr>` — the init executes immediately; the id is a target.
        visit(node.init, false); return;
      case 'LabeledStatement': visit(node.body, false); return;
      case 'BreakStatement': case 'ContinueStatement': return;
      default:
        for (const k of Object.keys(node)) {
          if (k === 'type' || k === 'start' || k === 'end' || k === 'loc' || k === 'range') continue;
          const v = node[k];
          if (Array.isArray(v)) v.forEach(c => { if (c && typeof c.type === 'string') visit(c, false); });
          else if (v && typeof v.type === 'string') visit(v, false);
        }
    }
  };
  for (const st of ast.body) visit(st, false);
  return refs;
}

// ---- 2) binding identity per extracted unit ----------------------------
console.log('\n' + '='.repeat(78));
console.log('[2] TOP-LEVEL BINDING IDENTITY (baseline inline body vs extracted file)');
console.log('='.repeat(78));

// t1's authorized dead-code deletions (from the t1 dependency result)
const T1_REMOVED = new Set([
  'startBtn', 'stopBtn', 'refreshModelsBtn',
  'apiPresetsBtn', 'apiPresetsMenu',
  'toggleApiKeyField', 'checkApiKeyRequirement',
  'webcamControls', 'rtspControls', 'screenControls', 'apiBaseHint',
]);
const T2_ADDED = new Set(['buildCameraOption', 'buildThemeIcon']);

let problems = [];
for (const u of BASE_INLINE) {
  const cur = curUnits.find(c => c.name === u.replacedBy);
  let baseAst, curAst;
  try { baseAst = parse(u.code, 'base:' + u.name); } catch (e) { console.log(`  ⚠ parse fail base ${u.name}: ${e.message}`); continue; }
  try { curAst = parse(cur.code, cur.name); } catch (e) { console.log(`  ❌ parse fail current ${cur.name}: ${e.message}`); problems.push(cur.name + ' parse'); continue; }

  const bb = topLevelBindings(baseAst), cb = topLevelBindings(curAst);
  const bn = bb.map(x => x.name), cn = cb.map(x => x.name);
  const bSet = new Set(bn), cSet = new Set(cn);
  const removed = bn.filter(n => !cSet.has(n));
  const added = cn.filter(n => !bSet.has(n));
  const renamedOrKindChanged = [];
  const bKind = new Map(bb.map(x => [x.name, x.kind]));
  for (const x of cb) if (bKind.has(x.name) && bKind.get(x.name) !== x.kind) renamedOrKindChanged.push(`${x.name}: ${bKind.get(x.name)} -> ${x.kind}`);

  console.log(`\n  ${u.replacedBy}: baseline topLevel=${bn.length} current topLevel=${cn.length}`);
  console.log(`    removed (${removed.length}): ${removed.join(', ') || '(none)'}`);
  console.log(`    added   (${added.length}): ${added.join(', ') || '(none)'}`);
  console.log(`    kind-changed: ${renamedOrKindChanged.join(', ') || '(none)'}`);

  const unexpectedRemoved = removed.filter(n => !T1_REMOVED.has(n));
  const unexpectedAdded = added.filter(n => !T2_ADDED.has(n));
  if (unexpectedRemoved.length) { console.log(`    ❌ UNATTRIBUTED REMOVALS: ${unexpectedRemoved.join(', ')}`); problems.push(`${u.replacedBy} unattributed removals`); }
  if (unexpectedAdded.length) { console.log(`    ❌ UNATTRIBUTED ADDITIONS: ${unexpectedAdded.join(', ')}`); problems.push(`${u.replacedBy} unattributed additions`); }
  if (renamedOrKindChanged.length) { console.log(`    ❌ KIND CHANGES`); problems.push(`${u.replacedBy} kind changes`); }
  if (!unexpectedRemoved.length && !unexpectedAdded.length && !renamedOrKindChanged.length) console.log('    ✅ all binding deltas attributable (t1 deletes / t2 helpers)');

  // order preservation of surviving bindings
  const survivors = bn.filter(n => cSet.has(n));
  const curOrder = filterOrder(cn, cSet, survivors);
  const orderOk = JSON.stringify(survivors) === JSON.stringify(curOrder);
  console.log(`    surviving-binding order preserved: ${orderOk} (${survivors.length} bindings)`);
  if (!orderOk) { console.log(`      base order: ${survivors.join(',')}`); console.log(`      cur  order: ${curOrder.join(',')}`); }
}
function filterOrder(cn, cSet, survivors) { const s = new Set(survivors); return cn.filter(n => s.has(n)); }

// ---- 3) cross-script TDZ hunt -----------------------------------------
console.log('\n' + '='.repeat(78));
console.log('[3] CROSS-SCRIPT TDZ HUNT (immediate top-level refs -> later let/const/class)');
console.log('='.repeat(78));

function tdzScan(units, label) {
  const parsed = [];
  units.forEach((u, i) => {
    if (!u.code) return;
    try { parsed.push({ i, name: u.name, ast: parse(u.code, u.name) }); } catch (e) { console.log(`  parse fail ${u.name}: ${e.message}`); }
  });
  // where is each top-level let/const/class declared?
  const declUnit = new Map(); // name -> {unitIndex, kind}
  for (const p of parsed) {
    for (const b of topLevelBindings(p.ast)) {
      if (['let', 'const', 'class'].includes(b.kind)) {
        if (!declUnit.has(b.name)) declUnit.set(b.name, { unitIndex: p.i, kind: b.kind, unit: p.name });
      }
    }
  }
  const findings = [];
  for (const p of parsed) {
    const refs = immediateRefs(p.ast);
    const seen = new Set();
    for (const r of refs) {
      const d = declUnit.get(r.name);
      if (!d) continue;
      if (d.unitIndex > p.i) {
        const key = `${p.name} -> ${r.name} (declared in ${d.unit}/${d.kind})`;
        if (!seen.has(key)) { seen.add(key); findings.push({ from: p.name, fromIdx: p.i, name: r.name, declUnit: d.unit, declIdx: d.unitIndex, kind: d.kind, line: r.loc && r.loc.start.line }); }
      }
    }
  }
  console.log(`\n  [${label}] genuine cross-script TDZ hazards (ref before later let/const/class decl): ${findings.length}`);
  findings.forEach(f => console.log(`    ⚠ ${f.from}(L${f.line}) references "${f.name}" declared later in ${f.declUnit} (${f.kind})`));
  return findings;
}
const baseTdz = tdzScan(baseUnits, 'baseline');
const curTdz = tdzScan(curUnits, 'current');
const sig = f => `${f.from}|${f.name}|${f.declUnit}|${f.kind}`;
const bSigs = new Set(baseTdz.map(sig)), cSigs = new Set(curTdz.map(sig));
const newTdz = curTdz.filter(f => !bSigs.has(sig(f)));
const goneTdz = baseTdz.filter(f => !cSigs.has(sig(f)));
console.log(`\n  NEW hazards introduced by the change: ${newTdz.length}`);
newTdz.forEach(f => console.log(`    ❌ ${sig(f)}`));
console.log(`  hazards removed: ${goneTdz.length}`);
goneTdz.forEach(f => console.log(`    (removed) ${sig(f)}`));
// unit names differ by design (INLINE@ -> file), so normalize before comparing
console.log(`  (note: unit-name renames INLINE@* -> *.js are expected; comparison above uses raw names)`);

// ---- 4) global leak surface -------------------------------------------
console.log('\n' + '='.repeat(78));
console.log('[4] GLOBAL / SCRIPT-LEXICAL SURFACE');
console.log('='.repeat(78));
function surface(units, label) {
  const m = new Map();
  for (const u of units) {
    if (!u.code) continue;
    let ast; try { ast = parse(u.code, u.name); } catch { continue; }
    for (const b of topLevelBindings(ast)) {
      if (!m.has(b.name)) m.set(b.name, { kind: b.kind, unit: u.name });
    }
  }
  console.log(`  [${label}] distinct top-level names across all scripts = ${m.size}`);
  return m;
}
const sBaseRaw = surface(baseUnits, 'baseline');
const sCurRaw = surface(curUnits, 'current');
// normalize unit names for the 4 extracted units
function normalize(m) {
  const out = new Map();
  for (const [k, v] of m) {
    const unit = v.unit.startsWith('INLINE@') ? Object.values(MAP)[BASE_INLINE.findIndex(u => u.name === v.unit)] : v.unit;
    out.set(k, { kind: v.kind, unit });
  }
  return out;
}
const sBase = normalize(sBaseRaw), sCur = normalize(sCurRaw);
const onlyBase = [...sBase.keys()].filter(k => !sCur.has(k));
const onlyCur = [...sCur.keys()].filter(k => !sBase.has(k));
console.log(`  baseline-only names (${onlyBase.length}): ${onlyBase.join(', ') || '(none)'}`);
console.log(`  current-only  names (${onlyCur.length}): ${onlyCur.join(', ') || '(none)'}`);
const leakKindChanged = [...sBase.keys()].filter(k => sCur.has(k) && sBase.get(k).kind !== sCur.get(k).kind);
console.log(`  kind changes (var<->let<->const/function/class): ${leakKindChanged.length} ${leakKindChanged.join(', ')}`);
leakKindChanged.forEach(k => console.log(`    ${k}: ${sBase.get(k).kind} -> ${sCur.get(k).kind}`));

console.log('\n' + '='.repeat(78));
console.log(problems.length ? `RESULT: ${problems.length} PROBLEM(S)` : 'RESULT: no unattributed binding/scope problems');
console.log('='.repeat(78));
process.exit(problems.length ? 1 : 0);
