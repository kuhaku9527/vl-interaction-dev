#!/usr/bin/env node
/**
 * JS 钩子清单（落实前检查）
 *
 * 为什么需要它：
 *   Studio 画布是「浏览器规范化 + 内联」后的 DOM 快照，设计过程中会自然丢掉
 *   或被有意删掉一些 id。但真实前端的 21 个 JS 靠 getElementById 抓这些钩子，
 *   多数有 null 保护（静默失效），少数没有（抛错）。所以「设计稿 → 代码」落地时
 *   必须先知道：哪些钩子丢了、丢了会怎样。
 *
 * 用法:
 *   node scripts/idesign-hooks.mjs                 # 用当前会话的设计稿做对比
 *   node scripts/idesign-hooks.mjs <design.html>   # 指定设计稿
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..');
const JS_DIR = path.join(ROOT, 'services/webui/src/joy_interaction_webui/static');

function currentSessionId() {
  const store = path.join(process.env.APPDATA || '', 'dsh-desktop/harness/profiles/web/desktop-storage.json');
  if (!fs.existsSync(store)) return null;
  try {
    return JSON.parse(JSON.parse(fs.readFileSync(store, 'utf8'))['dsh.sessions.current']).sessionId ?? null;
  } catch { return null; }
}

const designPath = process.argv[2]
  || path.join(ROOT, 'design', currentSessionId() ?? '', 'index.html');
if (!fs.existsSync(designPath)) {
  console.error(`找不到设计稿: ${designPath}`);
  process.exit(2);
}
const design = fs.readFileSync(designPath, 'utf8');

/** 出现在设计稿里的 id 集合 */
const present = new Set([...design.matchAll(/\bid="([^"]+)"/g)].map((m) => m[1]));

/** 从 JS 里抽出 getElementById('x') 的引用，并判断是否带 null 保护 */
const refs = new Map(); // id -> [{file, line, guarded}]
for (const f of fs.readdirSync(JS_DIR).filter((n) => n.endsWith('.js'))) {
  const lines = fs.readFileSync(path.join(JS_DIR, f), 'utf8').split(/\r?\n/);
  lines.forEach((text, i) => {
    for (const m of text.matchAll(/getElementById\(\s*['"]([^'"]+)['"]\s*\)/g)) {
      const id = m[1];
      // 常见保护写法：先赋给变量再 if (x)，或行内 if (x && ...) / ?. 可选链
      const guarded = /if\s*\(|&&|\?\./.test(text) || /^\s*(const|let|var)\s+\w+\s*=/.test(text);
      if (!refs.has(id)) refs.set(id, []);
      refs.get(id).push({ file: f, line: i + 1, guarded });
    }
  });
}

const missing = [];
for (const [id, uses] of [...refs.entries()].sort()) {
  if (present.has(id)) continue;
  missing.push({
    id,
    // 只要有任意一处没有保护，就算裸引用（风险更高）
    bare: uses.some((u) => !u.guarded),
    uses: uses.map((u) => `${u.file}:${u.line}`).slice(0, 3),
  });
}

const report = {
  design: path.relative(ROOT, designPath).replace(/\\/g, '/'),
  designIds: present.size,
  jsReferencedIds: refs.size,
  missingCount: missing.length,
  missing,
};

console.log(JSON.stringify(report, null, 2));
process.exitCode = missing.length ? 1 : 0;
