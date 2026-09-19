#!/usr/bin/env node
/**
 * 把输入栏（.prompt-editor-inline，含 #promptEditor）从 .container 底部
 * 搬进右侧聊天卡（#vlmOutputCard）内部，实现「嵌入 + 吸底」（B 方案）。
 *
 * 为什么用脚本而不是手工编辑：
 *   该块 157 行、含大量嵌套 div 与多行属性，手工搬移极易漏带/多带闭合标签。
 *   脚本按行号精确切出整块，做结构自检后再写回。
 *
 * 幂等：若已嵌入（prompt-editor-inline 已在 vlmOutputCard 之内），则不做任何事。
 *
 * 用法: node scripts/embed-prompt-into-chat.mjs [--dry-run]
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..');
const FILE = path.join(ROOT, 'services/webui/src/joy_interaction_webui/static/index.html');
const dryRun = process.argv.includes('--dry-run');

const src = fs.readFileSync(FILE, 'utf8');
const nl = src.includes('\r\n') ? '\r\n' : '\n';
const lines = src.split(nl);

const find = (needle) => {
  const i = lines.findIndex((l) => l.includes(needle));
  if (i < 0) throw new Error(`未找到标记: ${needle}`);
  return i;
};

// ---- 定位三个锚点 ----
const iCardOpen = find('<div class="result-card" id="vlmOutputCard">');
const iPromptOpen = find('<div class="prompt-editor-inline">');
const iPromptShell = find('<div class="chat-prompt-shell" id="promptEditor">');

// prompt-editor-inline 的闭合行：从其开启行起做 div 深度配平
function matchClose(startIdx) {
  let depth = 0;
  for (let i = startIdx; i < lines.length; i++) {
    const opens = (lines[i].match(/<div\b/g) || []).length;
    const closes = (lines[i].match(/<\/div>/g) || []).length;
    depth += opens - closes;
    if (i === startIdx && depth <= 0) throw new Error('起始行未开启 div');
    if (depth === 0) return i;
  }
  throw new Error('未能配平 prompt-editor-inline 的闭合');
}
const iPromptClose = matchClose(iPromptOpen);

// result-card 的闭合行
const iCardClose = matchClose(iCardOpen);

// ---- 判断是否已嵌入 ----
if (iPromptOpen > iCardOpen && iPromptOpen < iCardClose) {
  console.log(JSON.stringify({ status: 'already-embedded', iCardOpen: iCardOpen + 1, iPromptOpen: iPromptOpen + 1 }, null, 2));
  process.exit(0);
}

// ---- 结构自检（搬移前）----
const block = lines.slice(iPromptOpen, iPromptClose + 1);
const blockText = block.join(nl);
const requiredIds = ['promptEditor', 'promptText', 'promptSendBtn', 'camBtn', 'captureOverlay'];
const missing = requiredIds.filter((id) => !blockText.includes(`id="${id}"`));
if (missing.length) throw new Error(`待搬移块缺少关键 id: ${missing.join(', ')}`);

const checkDepth = () => {
  let d = 0;
  for (const l of block) {
    d += (l.match(/<div\b/g) || []).length - (l.match(/<\/div>/g) || []).length;
  }
  return d;
};
if (checkDepth() !== 0) throw new Error(`待搬移块 div 不平衡: ${checkDepth()}`);

// ---- 执行搬移 ----
// 注意：要「嵌入」必须插到 result-card 开标签【之后】（成为其子元素），
// 插到之前只会变成它的兄弟节点（那还是在外部）—— dry-run 自检已拦下该错误。
const withoutBlock = [
  ...lines.slice(0, iPromptOpen),
  ...lines.slice(iPromptClose + 1),
];
// 原块被删后，result-card 开标签索引会前移（若它原本在 prompt 之后）
const shift = iCardOpen > iPromptOpen ? block.length : 0;
const newCardIdx = iCardOpen - shift;
const next = [
  ...withoutBlock.slice(0, newCardIdx + 1),  // 含 result-card 开标签
  ...block,
  ...withoutBlock.slice(newCardIdx + 1),
];

// ---- 搬移后自检 ----
const joined = next.join(nl);
const nCard = next.findIndex((l) => l.includes('id="vlmOutputCard"'));
const nPrompt = next.findIndex((l) => l.includes('<div class="prompt-editor-inline">'));
const nShell = next.findIndex((l) => l.includes('id="promptEditor"'));
const nCardClose = matchClose2(next, nCard);
if (nCard < 0 || nPrompt < 0) throw new Error('搬移后锚点丢失');
if (!(nPrompt > nCard)) throw new Error('搬移后 prompt 未落在 result-card 之后（未嵌入）');
if (!(nPrompt < nCardClose)) throw new Error('搬移后 prompt 落在 result-card 之外');
if (nShell < nPrompt) throw new Error('#promptEditor 未随块一起搬移');
// 搬移后 result-card 的 div 必须仍然配平（否则说明闭合标签错位）
if (nCardClose < 0) throw new Error('搬移后 result-card 无法配平闭合');

// ⚠️ 关键自检：必须真正「移动」而非「复制」—— 容器与关键 id 全程只能有 1 份。
// 首版脚本只 insert 未 delete，导致输入栏在两处各渲染一次（重复 id）。
const dupCheck = {};
for (const id of ['promptEditor', 'promptText', 'promptSendBtn', 'camBtn']) {
  dupCheck[id] = (joined.match(new RegExp(`id="${id}"`, 'g')) || []).length;
}
const dupContainer = (joined.match(/class="prompt-editor-inline"/g) || []).length;
dupCheck['.prompt-editor-inline'] = dupContainer;
const dups = Object.entries(dupCheck).filter(([, n]) => n !== 1);
if (dups.length) {
  throw new Error('搬移后出现重复（说明是复制而非移动）: ' + dups.map(([k, n]) => `${k}=${n}`).join(', '));
}
if (next.length !== lines.length) {
  throw new Error(`行数应守恒（移动不改变总行数）: before=${lines.length} after=${next.length}`);
}

function matchClose2(arr, startIdx) {
  let depth = 0;
  for (let i = startIdx; i < arr.length; i++) {
    depth += (arr[i].match(/<div\b/g) || []).length - (arr[i].match(/<\/div>/g) || []).length;
    if (depth === 0) return i;
  }
  return -1;
}

const report = {
  status: dryRun ? 'dry-run' : 'moved',
  file: path.relative(ROOT, FILE),
  promptBlockLines: `${iPromptOpen + 1}..${iPromptClose + 1}`,
  promptBlockLineCount: block.length,
  movedBefore: `result-card @${iCardOpen + 1}`,
  newPromptLine: nPrompt + 1,
  cardClosesAt: matchClose2(next, nCard) + 1,
  linesBefore: lines.length,
  linesAfter: next.length,
  idChecks: Object.fromEntries(requiredIds.map((id) => [id, joined.includes(`id="${id}"`)])),
};

if (!dryRun) fs.writeFileSync(FILE, next.join(nl), 'utf8');
console.log(JSON.stringify(report, null, 2));
