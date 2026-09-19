#!/usr/bin/env node
/**
 * 给设置页「说明文字」类元素补 data-i18n 接线（第二轮汉化补齐）。
 *
 * 背景：applyUiI18n() 只处理带 data-i18n 的元素（i18n_device_label.js:206），
 * 所以即便 UI_STRING_MAP 已有词条，元素缺属性就不会被翻译。
 * 用户反馈「还有很多小文字没汉化」—— 即此类漏网说明句。
 *
 * ⚠️ 沿用 i18n-optin.mjs 的经验：批量插属性必须
 *   ① 用「捕获整个起始标签」的正则，且正则整体只匹配起始标签；
 *   ② 写盘前做结构守恒自检（< / > / 开闭标签 / id 数任一变化即拒绝写入）。
 *
 * 用法: node scripts/i18n-optin-2.mjs [--dry-run]
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..');
const FILE = path.join(ROOT, 'services/webui/src/joy_interaction_webui/static/index.html');
const dryRun = process.argv.includes('--dry-run');

let html = fs.readFileSync(FILE, 'utf8');
const original = html;
const applied = [];

/**
 * 给「起始标签 + 紧随文本」的起始标签补 data-i18n。
 * 文本与闭合标签原样保留（首版 bug 的教训：不能把它们吃进替换结果）。
 */
function optIn(tagRe, textRe, label) {
  const combined = new RegExp(`(${tagRe.source})\\s*(?:${textRe.source})`, tagRe.flags.replace('g', '') + 'g');
  const hits = [];
  let m;
  while ((m = combined.exec(html)) !== null) hits.push({ index: m.index, tag: m[1] });
  for (let i = hits.length - 1; i >= 0; i--) {
    const { index, tag } = hits[i];
    if (/(?:^|\s)data-i18n(?:=|\s|$)/.test(tag)) continue;
    const patched = tag.replace(/(\s*\/?>)$/, ' data-i18n$1');
    if (patched === tag) throw new Error(`${label}: 未能插入属性 -> ${tag.slice(0, 70)}`);
    html = html.slice(0, index) + patched + html.slice(index + tag.length);
    applied.push(label);
  }
}

const DIV_TAG = /<div\b[^>]*class="settings-item-description"[^>]*>/;

// ---- 设置页漏网说明句（与 UI_STRING_MAP 新增条目一一对应）----
optIn(DIV_TAG, /Scale animation when new VLM response arrives/, 'desc:Scale animation');
optIn(DIV_TAG, /Drop old frames if delay exceeds this \(0 = no intervention\)/, 'desc:Drop old frames');
optIn(DIV_TAG, /Run Qwen3\.5-122B-A10B-FP8 for delegated questions, visual reasoning, and chart tasks in the background/, 'desc:Qwen3.5 background');
optIn(DIV_TAG, /Background frames per second relative to foreground streaming FPS/, 'desc:bg fps');
optIn(DIV_TAG, /Recent background frame cache cap; default and maximum are 100/, 'desc:frame cache cap');
optIn(DIV_TAG, /Include request JSON \(image \+ prompt\) under the prompt area; collapsed by default/, 'desc:request JSON');
optIn(DIV_TAG, /Include API response JSON under the VLM output; collapsed by default/, 'desc:response JSON');
optIn(DIV_TAG, /Display mid-term and long-term memory content below VLM output/, 'desc:memory content');
optIn(DIV_TAG, /Reserve only; v1 traffic stays direct \(ADR-0012 §4\)/, 'desc:proxy reserve');

// ---- 知识库：同步目录路径等（用户点名的三大类之一）----
optIn(/<div\b[^>]*class="settings-item-label"[^>]*>/, /Sync folder path \(wiki\/&lt;game&gt;\)/, 'label:Sync folder path');

// ---- 结构性自检：只加属性，绝不能增删尖括号或标签 ----
const count = (re, s) => (s.match(re) || []).length;
const invariants = {
  lt: [count(/</g, original), count(/</g, html)],
  gt: [count(/>/g, original), count(/>/g, html)],
  openTag: [count(/<[a-zA-Z][a-zA-Z0-9-]*\b/g, original), count(/<[a-zA-Z][a-zA-Z0-9-]*\b/g, html)],
  closeTag: [count(/<\/[a-zA-Z][a-zA-Z0-9-]*>/g, original), count(/<\/[a-zA-Z][a-zA-Z0-9-]*>/g, html)],
  idAttr: [count(/\bid="/g, original), count(/\bid="/g, html)],
};
const violations = Object.entries(invariants).filter(([, [a, b]]) => a !== b);
if (violations.length) {
  console.error('❌ 结构自检失败，拒绝写入：');
  for (const [k, [a, b]] of violations) console.error(`   ${k}: ${a} -> ${b}`);
  process.exit(1);
}

const summary = {};
for (const a of applied) summary[a] = (summary[a] || 0) + 1;

const report = {
  file: path.relative(ROOT, FILE),
  dryRun,
  totalApplied: applied.length,
  byKind: summary,
  invariants: Object.fromEntries(Object.entries(invariants).map(([k, [a, b]]) => [k, `${a} -> ${b}`])),
  changed: html !== original,
};

if (dryRun || html === original) {
  console.log(JSON.stringify({ ...report, note: dryRun ? 'dry-run' : '无变化' }, null, 2));
  process.exit(0);
}
fs.writeFileSync(FILE, html, 'utf8');
console.log(JSON.stringify(report, null, 2));
