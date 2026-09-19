#!/usr/bin/env node
/**
 * 给设置页里「UI_STRING_MAP 已有条目、但元素缺 data-i18n 属性」的文案补属性。
 *
 * 为什么需要（实测根因）：
 *   applyUiI18n()（i18n_device_label.js:206）只遍历 `[data-i18n]` 元素，用
 *   textContent 作为 key 查 UI_STRING_MAP。所以即使 map 里有 'Cloud'→'云端'，
 *   元素上没有 data-i18n 就不会被翻译。（活证据：同区域的 `Preset name`
 *   因带 data-i18n-placeholder 已汉化，而 `Cloud`/`Local`/`Delete` 仍是英文。）
 *
 * ⚠️ 修复过的严重 bug（首版曾破坏 HTML，务必保留此注释）：
 *   首版正则写成 /<button([^>]*class="svc-seg-btn[^"]*"[^>]*>)…/ ——
 *   捕获组 ([^>]*) 不含 "<button" 前缀，而替换时 return 的是 patched（仅捕获组
 *   内容），于是把 `<button` / `<div` 的起始标签名整个吃掉，产出
 *   ` type="button" class="svc-seg-btn" …>` 这种非法 HTML，页面上直接显示出
 *   属性文本（`<` 计数少了 40 个）。
 *   现改为捕获【完整标签】并用 (?:^|\s) 锚定属性边界，且在写盘前做
 *   `<` / 标签数守恒 自检，不通过就拒绝写入。
 *
 * 幂等：已带 data-i18n 的元素不动。
 * 用法: node scripts/i18n-optin.mjs [--dry-run]
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
 * 在完整标签上补 data-i18n。
 * @param {RegExp} re 必须把【整个标签】捕获为 $1；且正则整体只匹配【起始标签】
 *                    （不要把 </button> 等闭合部分包进匹配，否则 return 时会把它们丢掉）
 */
function optInStartTag(re, label) {
  html = html.replace(re, (m, tag) => {
    if (!/^<[a-zA-Z]/.test(tag)) throw new Error(`${label}: 捕获组不是完整标签 -> ${String(tag).slice(0, 60)}`);
    if (/(?:^|\s)data-i18n(?:=|\s|$)/.test(tag)) return m;  // 已 opt-in
    const patched = tag.replace(/(\s*\/?>)$/, ' data-i18n$1');
    if (patched === tag) throw new Error(`${label}: 未能插入属性 -> ${tag.slice(0, 60)}`);
    applied.push(label);
    return patched;
  });
}

/** 用「起始标签 + 紧随其后的文本」判定，只替换起始标签部分（文本与闭合标签原样保留）。 */
function optInByText(tagRe, textRe, label) {
  // 先找到所有「起始标签 + 文本」组合，记录需要补属性的起始标签位置
  const combined = new RegExp(`(${tagRe.source})\\s*(?:${textRe.source})`, tagRe.flags.replace('g', '') + 'g');
  const tags = [];
  let m;
  while ((m = combined.exec(html)) !== null) tags.push({ index: m.index, tag: m[1] });
  // 从后往前替换，避免索引位移
  for (let i = tags.length - 1; i >= 0; i--) {
    const { index, tag } = tags[i];
    if (/(?:^|\s)data-i18n(?:=|\s|$)/.test(tag)) continue;
    const patched = tag.replace(/(\s*\/?>)$/, ' data-i18n$1');
    if (patched === tag) throw new Error(`${label}: 未能插入属性`);
    html = html.slice(0, index) + patched + html.slice(index + tag.length);
    applied.push(label);
  }
}

// ---- 服务段切换按钮 Cloud / Local（各 6 处）----
optInByText(/<button\b[^>]*class="svc-seg-btn[^"]*"[^>]*>/, /Cloud\s*<\/button>/, 'svc-seg-btn:Cloud');
optInByText(/<button\b[^>]*class="svc-seg-btn[^"]*"[^>]*>/, /Local\s*<\/button>/, 'svc-seg-btn:Local');

// ---- provider Delete 按钮（6 处）----
optInByText(/<button\b[^>]*class="provider-del"[^>]*>/, /Delete\s*<\/button>/, 'provider-del:Delete');

// ---- provider pick 的占位 option（6 处，静态）----
optInByText(/<option\b[^>]*>/, /— saved presets —\s*<\/option>/, 'option:saved-presets');

// ---- 后端 local-hint 提示行 ----
optInByText(/<div\b[^>]*class="local-hint"[^>]*>/, /Local default endpoint · no API key\s*<\/div>/, 'local-hint');

// ---- 模型区说明句 ----
optInByText(/<div\b[^>]*>/, /Six pluggable backends\. Save applies at runtime; no service restart\.\s*<\/div>/, 'model-intro');

// ---- Save / Probe（Network Proxy 区）----
optInByText(/<button\b[^>]*id="proxySaveBtn"[^>]*>/, /Save\s*<\/button>/, 'proxySaveBtn:Save');
optInByText(/<button\b[^>]*id="proxyTestBtn"[^>]*>/, /Probe\s*<\/button>/, 'proxyTestBtn:Probe');

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
