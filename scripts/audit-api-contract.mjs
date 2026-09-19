#!/usr/bin/env node
/**
 * 前后端 API 契约审计（2026-09-19）
 *
 * 动机：用户"先做后端再做前端，慢慢补上"，两侧可能对不上。
 * 方法：从静态 JS/HTML 抽取所有**路由样字符串**，从 Python 抽取所有已注册路由，
 *       双向比对，找出：
 *         BROKEN  = 前端在调、后端没注册  → 真断链
 *         UNUSED  = 后端注册了、前端不调  → 可能废弃/给外部客户端
 * 用法: node scripts/audit-api-contract.mjs
 */
import fs from 'node:fs';
import path from 'node:path';

const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname.replace(/^\//, '')), '..');
const STATIC = path.join(ROOT, 'services/webui/src/joy_interaction_webui/static');
const PYDIR = path.join(ROOT, 'services/webui/src/joy_interaction_webui');

// 归一化：去掉查询串、把 {param} / ${x} 统一为 :param
const norm = (p) => p
  .replace(/\$\{[^}]*\}/g, ':param')
  .replace(/\{[^}]*\}/g, ':param')
  .replace(/\?.*$/, '')
  .replace(/\/+$/, '') || '/';

// ---- 前端：所有看起来像路由的字符串字面量 ----
const jsFiles = fs.readdirSync(STATIC).filter((f) => /\.(js|html)$/.test(f));
const frontend = new Map();   // route -> Set(file)
for (const f of jsFiles) {
  const src = fs.readFileSync(path.join(STATIC, f), 'utf8');
  // 去掉注释，避免把注释里的端点当成真实调用
  const clean = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
  const re = /["'`](\/(?:api|v1|ws|offer)[A-Za-z0-9_\-/{}$.:]*)/g;
  let m;
  while ((m = re.exec(clean))) {
    const r = norm(m[1]);
    if (!frontend.has(r)) frontend.set(r, new Set());
    frontend.get(r).add(f);
  }
}

// ---- 后端：所有已注册路由 ----
const pyFiles = fs.readdirSync(PYDIR).filter((f) => f.endsWith('.py'));
const backend = new Map();    // route -> Set(file)
for (const f of pyFiles) {
  const src = fs.readFileSync(path.join(PYDIR, f), 'utf8');
  const re = /(?:add_(get|post|put|delete|route)|web\.(get|post|put|delete))\s*\(\s*["']([^"']+)["']/g;
  let m;
  while ((m = re.exec(src))) {
    const method = (m[1] || m[2] || 'route').toUpperCase();
    const r = norm(m[3]);
    if (!backend.has(r)) backend.set(r, new Set());
    backend.get(r).add(`${f}:${method}`);
  }
}

// ---- 比对 ----
const known_external = new Set([
  '/v1/chat/completions', '/v1/models', '/health', '/models',  // 上游/外部客户端
]);
const isExternalish = (r) => known_external.has(r);

const broken = [];
for (const [r, files] of [...frontend].sort()) {
  if (backend.has(r)) continue;
  if (isExternalish(r)) continue;
  broken.push({ route: r, files: [...files].join(', ') });
}
const unused = [];
for (const [r, regs] of [...backend].sort()) {
  if (frontend.has(r)) continue;
  unused.push({ route: r, regs: [...regs].join(', ') });
}

console.log('='.repeat(78));
console.log(`前端引用的路由 ${frontend.size} 个 ／ 后端注册的路由 ${backend.size} 个`);
console.log('='.repeat(78));

console.log(`\n【BROKEN】前端在调、后端无此路由 —— ${broken.length} 个`);
if (!broken.length) console.log('  （无）');
broken.forEach((b) => console.log(`  ❌ ${b.route.padEnd(42)} ← ${b.files}`));

console.log(`\n【UNUSED】后端注册、前端不调 —— ${unused.length} 个`);
if (!unused.length) console.log('  （无）');
unused.forEach((u) => console.log(`  ·  ${u.route.padEnd(42)} ← ${u.regs}`));

console.log('\n【双端匹配】');
const matched = [...frontend.keys()].filter((r) => backend.has(r)).sort();
matched.forEach((r) => console.log(`  ✅ ${r}`));

process.exit(broken.length ? 1 : 0);
