#!/usr/bin/env node
/**
 * 给「模型」字段加「获取模型」按钮 + 候选下拉（<datalist>）。
 *
 * 用户诉求（2026-09-18）：
 *   「接口功能：需要有类似『上游探测』那种基本的实现，比如获取模型、探测是否存活之类」
 *   —— 这是模型接入的标配（RAGFlow / Open WebUI / LiteLLM 都有）。
 *
 * 为什么用 <datalist> 而不是 <select>：
 *   - 上游模型列表可能为空/不可用，此时必须仍允许手填（本地模型尤其如此）
 *   - <datalist> 天然「可输入 + 有候选」，不锁死用户
 *   - 零依赖、浏览器原生，无需引入组件库
 *
 * 幂等：已存在 data-model-fetch 标记时跳过。
 * 用法: node scripts/add-model-fetch.mjs [--dry-run]
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

// 只有「有 model 输入框」的槽位才加按钮（tts / agent 没有 model 字段）
const SLOTS = ['llm', 'summary', 'asr', 'embedding'];

for (const slot of SLOTS) {
  const inputId = `svc-${slot}-model`;
  if (!html.includes(`id="${inputId}"`)) {
    applied.push(`SKIP ${slot}: 无 model 字段`);
    continue;
  }
  if (html.includes(`data-model-fetch="${slot}"`)) {
    applied.push(`SKIP ${slot}: 已存在`);
    continue;
  }
  // 定位该 form-group（含 label + input），把它整体替换为「label + 行内按钮 + datalist」
  const groupRe = new RegExp(
    `<div class="form-group"><label data-i18n>Model</label><input type="text" id="${inputId}"([^>]*)></div>`,
  );
  const m = html.match(groupRe);
  if (!m) {
    applied.push(`SKIP ${slot}: 结构未匹配`);
    continue;
  }
  const attrs = m[1];
  const replacement =
    `<div class="form-group">` +
    `<label data-i18n>Model</label>` +
    `<div class="model-field">` +
    `<input type="text" id="${inputId}"${attrs} list="${inputId}-options" data-model-input>` +
    `<datalist id="${inputId}-options"></datalist>` +
    `<button class="icon-btn model-fetch-btn" id="${inputId}-fetch" type="button" ` +
    `title="从上游获取可用模型列表" data-i18n-title="从上游获取可用模型列表" ` +
    `data-model-fetch="${slot}"><i data-lucide="download"></i><span data-i18n>获取模型</span></button>` +
    `</div>` +
    `</div>`;
  html = html.replace(groupRe, replacement);
  applied.push(`ADD ${slot}`);
}

const report = {
  file: path.relative(ROOT, FILE),
  dryRun,
  changes: applied,
  added: applied.filter((a) => a.startsWith('ADD')).length,
};

if (dryRun || html === original) {
  console.log(JSON.stringify({ ...report, note: dryRun ? 'dry-run' : '无变化' }, null, 2));
  process.exit(0);
}
fs.writeFileSync(FILE, html, 'utf8');
console.log(JSON.stringify(report, null, 2));
