#!/usr/bin/env node
/**
 * t5 adversarial analysis v2 — IIFE-AWARE cross-script TDZ hunt.
 *
 * Fixes the blind spot of v1: an IIFE `(function(){...})()` at top level runs
 * IMMEDIATELY, so identifiers inside it are read at load time, exactly like
 * bare top-level statements. v1 treated every function body as deferred and
 * therefore scanned *nothing* for incremental_wiring.js (which is one big IIFE)
 * — the very file the contract flags as highest risk.
 *
 * Model:
 *   - a function body is "immediate" iff the function is *directly invoked*
 *     (IIFE) or passed to a known immediate-invoking construct at load time.
 *   - everything else is deferred (event handlers, callbacks, promises).
 *
 * For each immediately-executing reference, we resolve whether it names a
 * let/const/class binding declared at TOP LEVEL of a LATER classic script.
 * In classic scripts all top-level let/const/class share one global lexical
 * environment, so such a read throws ReferenceError (TDZ) — the exact failure
 * mode the contract asks about.
 *
 * Run over baseline and current and diff the results.
 */
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(path.join(process.cwd(), 'services/webui/package.json'));
const acorn = require('acorn');

const ROOT = process.cwd();
const BASE_HTML = path.join(ROOT, '.cache/pret1/src/joy_interaction_webui/static/index.html');
const CUR_HTML = path.join(ROOT, 'services/webui/src/joy_interaction_webui/static/index.html');
const BASE_STATIC = path.join(ROOT, '.cache/pret1/src/joy_interaction_webui/static');
const CUR_STATIC = path.join(ROOT, 'services/webui/src/joy_interaction_webui/static');

function unitList(htmlPath, staticDir) {
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
    if (isCDN) name = 'CDN:' + srcAttr;
    else if (srcAttr) {
      name = srcAttr.replace('./', '');
      const p = path.join(staticDir, name);
      code = fs.existsSync(p) ? fs.readFileSync(p, 'utf8') : null;
    } else { name = 'INLINE@' + m.index; code = src.slice(bs, c); }
    units.push({ name, code, isCDN, offset: m.index });
    open.lastIndex = c;
  }
  return units;
}

const baseUnits = unitList(BASE_HTML, BASE_STATIC);
const curUnits = unitList(CUR_HTML, CUR_STATIC);
const MAP = { 0: 'app_boot.js', 1: 'app_main.js', 2: 'sidebar_toggle.js', 3: 'incremental_wiring.js' };
const baseInline = baseUnits.filter(u => u.name.startsWith('INLINE@'));
baseInline.forEach((u, i) => { u.replacedBy = MAP[i]; });

const parse = code => acorn.parse(code, { ecmaVersion: 'latest', sourceType: 'script', allowReturnOutsideFunction: true, locations: true });

// ---- top-level binding collection --------------------------------------
function topLevelBindings(ast) {
  const out = [];
  const push = (n, kind) => { if (n) out.push({ name: n, kind }); };
  const pat = (p, kind) => {
    if (!p) return;
    if (p.type === 'Identifier') push(p.name, kind);
    else if (p.type === 'ObjectPattern') p.properties.forEach(pr => pat(pr.value || pr.argument, kind));
    else if (p.type === 'ArrayPattern') p.elements.forEach(e => pat(e, kind));
    else if (p.type === 'AssignmentPattern') pat(p.left, kind);
    else if (p.type === 'RestElement') pat(p.argument, kind);
  };
  for (const st of ast.body) {
    if (st.type === 'VariableDeclaration') st.declarations.forEach(d => pat(d.id, st.kind));
    else if (st.type === 'FunctionDeclaration') push(st.id && st.id.name, 'function');
    else if (st.type === 'ClassDeclaration') push(st.id && st.id.name, 'class');
  }
  return out;
}

// ---- immediate-execution reference collector ---------------------------
/**
 * Walk a top-level statement. `immediate` says whether the code being walked
 * runs during script evaluation.
 */
function immediateRefs(ast) {
  const refs = [];
  const walk = (node, immediate) => {
    if (!node || typeof node.type !== 'string') return;
    switch (node.type) {
      case 'Identifier':
        if (immediate) refs.push(node);
        return;
      case 'MemberExpression':
        walk(node.object, immediate);
        if (node.computed) walk(node.property, immediate);
        return;
      case 'Property':
        if (node.computed) walk(node.key, immediate);
        walk(node.value, immediate);
        return;
      case 'VariableDeclarator':
        // init evaluates now; the declared id is a binding target, not a read
        walk(node.init, immediate);
        return;
      case 'FunctionDeclaration':
        // declaration itself is hoisted (no read); its body is deferred unless
        // the declaration is immediately invoked later (handled at CallExpression)
        return;
      case 'FunctionExpression':
      case 'ArrowFunctionExpression':
        // only immediate when the enclosing expression immediately invokes it;
        // handled in CallExpression. Standalone function expressions: deferred.
        return;
      case 'ClassDeclaration':
      case 'ClassExpression':
        // class heritage + static blocks evaluate now, but keep it simple and safe:
        if (node.type === 'ClassExpression' && node.superClass) walk(node.superClass, immediate);
        return;
      case 'CallExpression':
      case 'NewExpression': {
        // is this an IIFE? callee is a function expression/arrow (or a .call/.apply on one)
        let callee = node.callee;
        let iife = false;
        if (callee && (callee.type === 'FunctionExpression' || callee.type === 'ArrowFunctionExpression')) iife = true;
        if (callee && callee.type === 'MemberExpression' && !callee.computed &&
            (callee.property.name === 'call' || callee.property.name === 'apply')) {
          const o = callee.object;
          if (o && (o.type === 'FunctionExpression' || o.type === 'ArrowFunctionExpression')) iife = true;
        }
        if (iife) {
          // arguments evaluate now
          node.arguments.forEach(a => walk(a, immediate));
          // the function parameter list + body run now
          const fn = callee.type === 'MemberExpression' ? callee.object : callee;
          walkFunctionNow(fn, immediate);
          return;
        }
        // ordinary call: callee is a READ, arguments evaluate now, body later
        walk(node.callee, immediate);
        node.arguments.forEach(a => walk(a, immediate));
        return;
      }
      case 'LabeledStatement': walk(node.body, immediate); return;
      case 'BreakStatement': case 'ContinueStatement': return;
      default:
        for (const k of Object.keys(node)) {
          if (k === 'type' || k === 'start' || k === 'end' || k === 'loc' || k === 'range') continue;
          const v = node[k];
          if (Array.isArray(v)) v.forEach(c => { if (c && typeof c.type === 'string') walk(c, immediate); });
          else if (v && typeof v.type === 'string') walk(v, immediate);
        }
    }
  };
  // a function body that runs immediately: default params + body are immediate
  const walkFunctionNow = (fn, immediate) => {
    fn.params.forEach(p => walk(p, immediate));
    if (fn.body && fn.body.type === 'BlockStatement') fn.body.body.forEach(s => walk(s, immediate));
    else if (fn.body) walk(fn.body, immediate);
  };
  ast.body.forEach(st => walk(st, true));
  return refs;
}

function scan(units, label) {
  const parsed = [];
  const parseErrors = [];
  units.forEach((u, i) => {
    if (!u.code) return;
    try { parsed.push({ i, name: u.name, ast: parse(u.code) }); }
    catch (e) { parseErrors.push(`${u.name}: ${e.message}`); }
  });
  const declUnit = new Map();
  for (const p of parsed) {
    for (const b of topLevelBindings(p.ast)) {
      if (['let', 'const', 'class'].includes(b.kind) && !declUnit.has(b.name)) {
        declUnit.set(b.name, { unitIndex: p.i, kind: b.kind, unit: p.name });
      }
    }
  }
  const findings = [];
  for (const p of parsed) {
    const seen = new Set();
    for (const r of immediateRefs(p.ast)) {
      const d = declUnit.get(r.name);
      if (!d || d.unitIndex <= p.i) continue;
      const key = `${p.name}|${r.name}|${d.unit}`;
      if (seen.has(key)) continue;
      seen.add(key);
      findings.push({ from: p.name, fromIdx: p.i, name: r.name, declUnit: d.unit, declIdx: d.unitIndex, kind: d.kind, line: r.loc && r.loc.start.line });
    }
  }
  console.log(`\n[${label}] parsed units=${parsed.length} parseErrors=${parseErrors.length} crossScriptTDZ=${findings.length}`);
  parseErrors.forEach(e => console.log('   parse error: ' + e));
  findings.forEach(f => console.log(`   ⚠ ${f.from}(L${f.line}) reads "${f.name}" -> top-level ${f.kind} declared LATER in ${f.declUnit}`));
  // secondary: refs to let/const/class declared at top level of the SAME unit
  // but AFTER the read -> also a TDZ, but local to one file (in-unit ordering)
  const sameUnit = [];
  for (const p of parsed) {
    const localDecl = new Map();
    for (const bb of topLevelBindingsWithNodes(p.ast)) localDecl.set(bb.name, bb);
    const seen = new Set();
    for (const r of immediateRefs(p.ast)) {
      const bb = localDecl.get(r.name);
      if (!bb || !bb.node) continue;
      if (bb.node.start > r.start) {
        if (!seen.has(r.name)) { seen.add(r.name); sameUnit.push({ name: r.name, readLine: r.loc.start.line, declLine: bb.node.loc.start.line }); }
      }
    }
  }
  console.log(`   in-unit TDZ (same file, read before later let/const/class): ${sameUnit.length}`);
  sameUnit.forEach(s => console.log(`     ⚠ "${s.name}" read at L${s.readLine}, declared at L${s.declLine}`));
  return findings;
}

// enrich bindings with node so same-unit TDZ can be checked
function topLevelBindingsWithNodes(ast) {
  const out = [];
  for (const st of ast.body) {
    if (st.type === 'VariableDeclaration' && ['let', 'const'].includes(st.kind)) {
      st.declarations.forEach(d => { if (d.id.type === 'Identifier') out.push({ name: d.id.name, kind: st.kind, node: d.id }); });
    } else if (st.type === 'ClassDeclaration' && st.id) out.push({ name: st.id.name, kind: 'class', node: st.id });
  }
  return out;
}

console.log('='.repeat(78));
console.log('t5 v2 IIFE-aware cross-script TDZ hunt');
console.log('='.repeat(78));
const b = scan(baseUnits, 'baseline');
const c = scan(curUnits, 'current');
const sig = f => `${f.name}|${f.declUnit}|${f.kind}`;
const bS = new Set(b.map(sig)), cS = new Set(c.map(sig));
console.log(`\n  NEW cross-script TDZ introduced: ${c.filter(f => !bS.has(sig(f))).length}`);
c.filter(f => !bS.has(sig(f))).forEach(f => console.log('   ❌ ' + sig(f)));
console.log(`  TDZ removed: ${b.filter(f => !cS.has(sig(f))).length}`);
b.filter(f => !cS.has(sig(f))).forEach(f => console.log('   (removed) ' + sig(f)));
console.log('='.repeat(78));
