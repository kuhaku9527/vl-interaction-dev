#!/usr/bin/env node
/**
 * 补丁去重后的**规则级**损失审计。
 *
 * 背景：styles.css 曾被重复追加 3 份补丁块（webui-css-patch.mjs 幂等保护失灵）。
 * 去重后必须证明：**没有任何 CSS 规则被误删**。
 * 只比行数不够 —— 必须比「选择器集合」。
 *
 * 用法: node scripts/audit-css-loss.mjs <cleanup前备份> <当前文件>
 */
import fs from 'node:fs';

const [before, after] = process.argv.slice(2);
if (!before || !after) { console.error('用法: node scripts/audit-css-loss.mjs <bak> <now>'); process.exit(2); }

// 抽出所有「选择器块」：去掉注释与 at-rule 嵌套，取每条规则的 selector 文本
const selectorsOf = (file) => {
  let css = fs.readFileSync(file, 'utf8');
  css = css.replace(/\/\*[\s\S]*?\*\//g, '');          // 去注释
  const out = [];
  // 逐字符扫描，记录每个 { 之前的 selector（深度 0 的规则）
  let depth = 0, buf = '';
  for (const ch of css) {
    if (ch === '{') {
      const sel = buf.split(/[};]/).pop().trim().replace(/\s+/g, ' ');
      if (sel && depth === 0) out.push(sel);
      depth++; buf = '';
    } else if (ch === '}') { depth = Math.max(0, depth - 1); buf = ''; }
    else buf += ch;
  }
  return out;
};

const norm = (s) => s.replace(/\s*,\s*/g, ',').trim();
const beforeSel = selectorsOf(before).map(norm);
const afterSel = selectorsOf(after).map(norm);

const setB = new Set(beforeSel), setA = new Set(afterSel);
const lost = [...setB].filter((s) => !setA.has(s));
const added = [...setA].filter((s) => !setB.has(s));

console.log(`选择器总数: 前 ${beforeSel.length}（去重 ${setB.size}） →  后 ${afterSel.length}（去重 ${setA.size}）`);
console.log(`\n仅在「前」出现的选择器（= 可能被误删）: ${lost.length}`);
lost.forEach((s) => console.log('  ❌ ' + s));
console.log(`\n仅在「后」出现的选择器（= 新增/原有）: ${added.length}`);
added.forEach((s) => console.log('  ＋ ' + s));

// 判定：丢失的必须是「重复副本里的旧版本规则」——即其后版本仍存在同名规则
// 这里保守起见：只要 lost 非空就报出来人工确认
console.log(lost.length === 0
  ? '\n✅ 无规则丢失'
  : `\n⚠️ 有 ${lost.length} 条选择器不再出现，需人工确认是否为旧副本残留`);
process.exit(0);
