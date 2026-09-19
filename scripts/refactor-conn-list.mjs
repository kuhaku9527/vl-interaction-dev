#!/usr/bin/env node
/**
 * 把「名称 + 加号 + 下拉」三件套替换为「连接列表卡片」（方案 X + 默认收起）。
 *
 * 用户诉求（2026-09-18）：
 *   「卡片列表的位置：替换掉现有的『名称 + 加号 + 下拉』三件套
 *     （这个方案更干净，但改动较大）」
 *   痛点原文：「保存按钮在哪我都不知道，哪个是保存按钮？」「删除按钮倒是有了，
 *             但是不协调」「保存的预设 UI 也不实用」。
 *
 * 替换前后对比：
 *   旧: [预设名称____] [+] [— 已保存的预设 —]   /  [删除]
 *   新: [已保存的连接 (2) ▾]        [+ 保存当前为连接]
 *         └ 展开后：每项一行 [名称] [应用] [删除]
 *         └ 点「保存当前为连接」→ 行内展开名称输入 + 确定/取消（不用原生 prompt）
 *
 * 为什么不用 window.prompt 取名：
 *   ADR-0020 §二.2 明确禁止在交互路径依赖原生对话框（预览 webview 会屏蔽，
 *   历史上多次导致"点了没反应"）。故用行内输入行。
 *
 * 只用 <div>/<button>/<span> 等既有标签，不引入新元素类型；
 * 不新增 id 之外的结构性契约。保留 svc-<slot>-provider-msg 供状态提示复用。
 *
 * 用法: node scripts/refactor-conn-list.mjs [--dry-run]
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

// 需要改造的槽位（agent 已分离且无三件套，不在此列 —— 用户要求「需要的才改」）
const SLOTS = ['llm', 'summary', 'asr', 'embedding', 'tts'];

const applied = [];
const skipped = [];

for (const slot of SLOTS) {
  const oldBlock = new RegExp(
    `\\s*<div class="provider-mgr">\\s*` +
    `<div class="provider-mgr-row">\\s*` +
    `<input id="svc-${slot}-provider-name"[^>]*>\\s*` +
    `<button class="provider-add" id="svc-${slot}-provider-add"[^>]*>[^<]*</button>\\s*` +
    `<select class="provider-pick" id="svc-${slot}-provider-pick"[^>]*>.*?</select>\\s*` +
    `</div>\\s*` +
    `<div class="provider-actions">\\s*` +
    `<button class="provider-del" id="svc-${slot}-provider-del"[^>]*>[^<]*</button>\\s*` +
    `<span class="provider-msg" id="svc-${slot}-provider-msg"[^>]*></span>\\s*` +
    `</div>\\s*</div>`,
    's',
  );

  if (!oldBlock.test(html)) {
    skipped.push(`${slot}: 三件套结构未匹配`);
    continue;
  }

  const newBlock = [
    ``,
    `                        <!-- 2026-09-18（方案 X + 默认收起）：原「名称 + 加号 + 下拉」三件套`,
    `                             被用户判定为不直观（"保存按钮在哪我都不知道"），此处替换为`,
    `                             连接列表：默认收起，展开后每项一行（应用 / 删除）。`,
    `                             点名不用原生 prompt（ADR-0020 §二.2 禁止交互路径依赖原生对话框）。 -->`,
    `                        <div class="conn-mgr">`,
    `                            <div class="conn-mgr-head">`,
    `                                <button class="conn-toggle" id="svc-${slot}-conn-toggle" type="button" aria-expanded="false" aria-controls="svc-${slot}-conn-list">`,
    `                                    <i data-lucide="link"></i>`,
    `                                    <span>已保存的连接</span>`,
    `                                    <span class="conn-count" id="svc-${slot}-conn-count">0</span>`,
    `                                    <i data-lucide="chevron-down" class="conn-caret"></i>`,
    `                                </button>`,
    `                                <button class="icon-btn conn-save-btn" id="svc-${slot}-conn-save" type="button" title="把当前填写的地址 / 密钥 / 模型保存为一个命名连接" data-i18n-title="把当前填写的地址 / 密钥 / 模型保存为一个命名连接">`,
    `                                    <i data-lucide="plus"></i><span>保存当前为连接</span>`,
    `                                </button>`,
    `                            </div>`,
    `                            <div class="conn-list" id="svc-${slot}-conn-list" hidden></div>`,
    `                            <div class="conn-name-row" id="svc-${slot}-conn-name-row" hidden>`,
    `                                <input type="text" id="svc-${slot}-conn-name" placeholder="给这个连接起个名字（如：openrouter 生产）">`,
    `                                <button class="icon-btn" id="svc-${slot}-conn-confirm" type="button"><i data-lucide="check"></i><span>确定</span></button>`,
    `                                <button class="icon-btn" id="svc-${slot}-conn-cancel" type="button"><span>取消</span></button>`,
    `                            </div>`,
    `                            <span class="provider-msg" id="svc-${slot}-provider-msg" role="status" aria-live="polite"></span>`,
    `                        </div>`,
  ].join('\n');

  html = html.replace(oldBlock, newBlock);
  applied.push(slot);
}

// ---- 结构自检：声明的预期 delta ----
// 每槽位（旧 → 新）的元素计数变化：
//   旧：provider-mgr(1 div) + provider-mgr-row(1) + provider-actions(1) = 3 div
//   新：conn-mgr(1) + conn-mgr-head(1) + conn-list(1) + conn-name-row(1) = 4 div
//   => 每槽 <div>/</div> 各 +1（成对，故 net 配平仍为 0 差值对）
//   button：旧 2（add/del），新 4（toggle/save/confirm/cancel）=> +2
const c = (re, s) => (s.match(re) || []).length;
const n = applied.length;
const actual = {
  id: c(/\bid="/g, html) - c(/\bid="/g, original),
  'data-service': c(/data-service="/g, html) - c(/data-service="/g, original),
  providerMgr: c(/class="provider-mgr"/g, html) - c(/class="provider-mgr"/g, original),
  connMgr: c(/class="conn-mgr"/g, html) - c(/class="conn-mgr"/g, original),
  divPair: (c(/<div\b/g, html) - c(/<div\b/g, original)) - (c(/<\/div>/g, html) - c(/<\/div>/g, original)),
  buttonPair: (c(/<button\b/g, html) - c(/<button\b/g, original)) - (c(/<\/button>/g, html) - c(/<\/button>/g, original)),
};
const EXPECTED = {
  // 每槽：删 4 个 id（provider-name/add/pick/del），新增 8 个
  //   （conn-toggle/conn-count/conn-save/conn-list/conn-name-row/conn-name/conn-confirm/conn-cancel）
  id: n * 4,
  'data-service': 0,
  providerMgr: -n,
  connMgr: +n,
  // 开闭标签必须成对（差值为 0 才说明没有落单的标签）
  divPair: 0,
  buttonPair: 0,
};
const violations = Object.entries(actual).filter(([k, d]) => d !== EXPECTED[k]);
if (violations.length) {
  console.error('❌ 结构自检失败（实际 delta 与预期不符）：');
  for (const [k, d] of violations) {
    console.error(`   ${k}: 实际 ${d >= 0 ? '+' : ''}${d}，预期 ${EXPECTED[k] >= 0 ? '+' : ''}${EXPECTED[k]}`);
  }
  process.exit(1);
}

const report = {
  file: path.relative(ROOT, FILE),
  dryRun,
  refactored: applied,
  skipped,
  deltas: actual,
  expectedDeltas: EXPECTED,
  providerMsgStillPresent: SLOTS.every((s) => html.includes(`id="svc-${s}-provider-msg"`)),
};

if (dryRun) {
  console.log(JSON.stringify({ ...report, status: 'dry-run' }, null, 2));
  process.exit(0);
}
fs.writeFileSync(FILE, html, 'utf8');
console.log(JSON.stringify({ ...report, status: 'written' }, null, 2));
