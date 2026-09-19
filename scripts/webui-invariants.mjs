#!/usr/bin/env node
/**
 * 基线不变量快照：改动前后对比，任何一项意外变化都要解释。
 * 用法: node scripts/webui-invariants.mjs [--save baseline|compare]
 */
import fs from 'node:fs';
import path from 'node:path';

const HTML = 'services/webui/src/joy_interaction_webui/static/index.html';
const CSS = 'services/webui/src/joy_interaction_webui/static/styles.css';
const STATIC_DIR = 'services/webui/src/joy_interaction_webui/static';
const DIR = path.dirname(new URL(import.meta.url).pathname.replace(/^\//, ''));
const SNAP = path.join(DIR, '.invariants.json');

// ★ 2026-09-19 修复 F3（reviewer t5 发现）：本脚本原先**只读 index.html + styles.css**，
//   不读任何 .js。内联脚本外移到 app_*.js 后，那些"按 HTML 统计"的指标
//   （prompt_editor_append / on_handlers / ready_placeholder / live_controls_in_settings）
//   就**永久失联** —— 它们会显示 0，而代码其实在新文件里。
//   危险之处：用 `--expect` 把这种变化声明掉，等于**用一个已失联的指标为交付背书**。
//
//   修法：把「已外移的脚本」一并纳入扫描范围（与 tests/ 下 12 个文件补 SPLIT_JS
//   的做法一致）。★ 且不再硬编码文件名 —— 改为**从 index.html 的 <script src> 动态
//   发现**，这样未来再外移也不会有遗漏（硬编码列表正是 t2 踩过的坑，见规范 §9.7）。
const SPLIT_JS = (() => {
  const html = fs.readFileSync(HTML, 'utf8');
  const names = [...html.matchAll(/<script[^>]*\ssrc=["']\.\/([^"']+\.js)["']/g)].map((m) => m[1]);
  const present = names.filter((n) => fs.existsSync(path.join(STATIC_DIR, n)));
  const missing = names.filter((n) => !present.includes(n));
  if (missing.length) {
    console.error(`❌ index.html 引用了不存在的脚本，拒绝继续（否则会静默缩小扫描范围）: ${missing.join(', ')}`);
    process.exit(2);
  }
  // 防呆：外移后的脚本必须存在且被扫到，否则说明发现逻辑失效
  if (!present.length) {
    console.error('❌ 未从 index.html 发现任何 <script src="./*.js">；扫描范围异常，拒绝继续');
    process.exit(2);
  }
  return present;
})();

// 去掉注释与 <script> 内容后再数标签，避免把注释里的示例当结构
const stripNoise = (s) => s
  .replace(/<!--[\s\S]*?-->/g, '')
  .replace(/<script[\s\S]*?<\/script>/gi, '');

const count = (s, re) => (s.match(re) || []).length;

const collect = () => {
  const html = fs.readFileSync(HTML, 'utf8');
  const css = fs.readFileSync(CSS, 'utf8');
  const clean = stripNoise(html);
  const ids = [...html.matchAll(/\sid="([^"]+)"/g)].map(m => m[1]);
  const dup = ids.filter((v, i) => ids.indexOf(v) !== i);
  // 外移脚本的合并文本：按 HTML 统计的那几项必须连它一起数，否则外移即"指标失联"
  const jsText = SPLIT_JS
    .map((n) => fs.readFileSync(path.join(STATIC_DIR, n), 'utf8'))
    .join('\n');
  // 结构类指标（div/尖括号配平）仍只看 HTML —— 它们描述的是标记结构，与脚本无关。
  // 脚本内容类指标（on_handlers/prompt_editor_append/…）改用 html+js 合并文本。
  const htmlJs = html + '\n' + jsText;
  return {
    html_lines: html.split('\n').length,
    css_lines: css.split('\n').length,
    split_js_files: SPLIT_JS.length,
    split_js_lines: jsText.split('\n').length,
    id_count: ids.length,
    id_dups: [...new Set(dup)],
    open_div: count(clean, /<div\b/g),
    close_div: count(clean, /<\/div>/g),
    open_angle: count(clean, /</g),
    close_angle: count(clean, />/g),
    script_tags: count(html, /<script\b/g),
    // ↓ 以下四项描述"脚本里的行为"，扫描范围 = HTML + 外移脚本
    on_handlers: count(htmlJs, /\son[a-z]+\s*=/g),
    data_i18n: count(htmlJs, /data-i18n(?=[\s>])/g),
    ready_placeholder: count(htmlJs, />\s*Ready\s*</g),
    prompt_editor_append: count(htmlJs, /fullscreenPromptOverlay\.appendChild/g),
    live_controls_in_settings: ['btListenBtn','liveEnrollBtn','liveVideoSource','liveProactiveToggle','promptPresetBtn']
      .reduce((n, id) => n + count(htmlJs, new RegExp(`id="${id}"`, 'g')), 0),
    _ids: ids,
  };
};

// ★ 关键：id_count 是**净变化**，会掩盖「删一个 + 加一个」。
// 2026-09-19 实测教训：搬迁 Live 控件时把 #promptText 一并删掉了，
// id_count 只 +1（+2 新增 −1 删除），净差检查完全看不出来，
// 直到渲染验收发现输入框消失才暴露。故必须逐 id 比对集合。
const diffIds = (base, cur) => {
  const b = new Set(base._ids || []), c = new Set(cur._ids || []);
  return {
    removed: [...b].filter(x => !c.has(x)),
    added: [...c].filter(x => !b.has(x)),
  };
};

const cur = collect();
const mode = process.argv.includes('--save') ? 'save' : 'compare';

if (mode === 'save') {
  fs.writeFileSync(SNAP, JSON.stringify(cur, null, 2));
  console.log('已保存基线:');
  console.log(JSON.stringify(cur, null, 2));
  process.exit(0);
}

if (!fs.existsSync(SNAP)) { console.log('无基线可对比。先跑 --save'); console.log(JSON.stringify(cur, null, 2)); process.exit(0); }
const base = JSON.parse(fs.readFileSync(SNAP, 'utf8'));

console.log('指标'.padEnd(26) + '基线'.padStart(8) + '现在'.padStart(8) + '  delta');
console.log('-'.repeat(60));
let hardFail = 0;
for (const k of Object.keys(cur)) {
  if (k === '_ids') continue;
  const a = JSON.stringify(base[k]), b = JSON.stringify(cur[k]);
  const diff = a === b ? '' : '  ← 变了';
  console.log(k.padEnd(26) + String(a).padStart(8) + String(b).padStart(8) + diff);
}

// ★ 逐 id 集合比对：任何「消失的 id」都必须逐项声明，绝不允许被净差掩盖
const { removed, added } = diffIds(base, cur);
console.log('\nid 集合变化:');
console.log(`  新增 ${added.length}: ${added.join(', ') || '（无）'}`);
console.log(`  删除 ${removed.length}: ${removed.join(', ') || '（无）'}`);
const declaredGone = (process.argv.find(a => a.startsWith('--id-removed=')) || '').replace('--id-removed=', '').split(',').filter(Boolean);
const undeclaredGone = removed.filter(x => !declaredGone.includes(x));
if (undeclaredGone.length) {
  console.log(`\n❌ 有 id 被删除且未声明（用 --id-removed=a,b 逐项确认）: ${undeclaredGone.join(', ')}`);
  hardFail = 1;
}
// 硬约束：平衡性与唯一性必须恒等；计数类允许"已声明的 delta"
const mustEqual = ['id_dups', 'script_tags'];
mustEqual.forEach(k => {
  if (JSON.stringify(base[k]) !== JSON.stringify(cur[k])) { console.log(`\n❌ 硬约束破坏: ${k}`); hardFail = 1; }
});
if (cur.open_div !== cur.close_div) { console.log(`\n❌ <div> 不配平: ${cur.open_div} vs ${cur.close_div}`); hardFail = 1; }
if (cur.id_dups.length) { console.log(`\n❌ 重复 id: ${cur.id_dups.join(', ')}`); hardFail = 1; }

// 计数类：变化必须由 --expect k=v 显式声明，否则视为意外
const expected = {};
process.argv.filter(a => a.startsWith('--expect=')).forEach(a => {
  a.replace('--expect=', '').split(',').forEach(pair => {
    const [k, v] = pair.split('=');
    if (k) expected[k.trim()] = Number(v);
  });
});
const cntKeys = ['html_lines', 'css_lines', 'split_js_files', 'split_js_lines', 'id_count', 'open_div', 'close_div', 'open_angle', 'close_angle', 'on_handlers', 'data_i18n', 'ready_placeholder', 'prompt_editor_append', 'live_controls_in_settings'];
const undeclared = [];
cntKeys.forEach(k => {
  if (JSON.stringify(base[k]) === JSON.stringify(cur[k])) return;
  const d = (typeof cur[k] === 'number' && typeof base[k] === 'number') ? cur[k] - base[k] : NaN;
  if (expected[k] !== undefined && expected[k] === d) return;
  undeclared.push(`${k}: ${base[k]} → ${cur[k]} (delta ${d >= 0 ? '+' : ''}${d})`);
});
if (undeclared.length) {
  console.log('\n⚠️  未声明的计数变化（需要逐项确认，或加 --expect=key=delta 声明）:');
  undeclared.forEach(u => console.log('   ' + u));
  hardFail = 1;
}
console.log(hardFail ? '\n❌ 未通过' : '\n✅ 硬约束保持，且所有计数变化均已声明');
process.exit(hardFail);
