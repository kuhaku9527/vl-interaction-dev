#!/usr/bin/env node
/**
 * 把 UI 修复补丁 CSS 追加到 styles.css 末尾。
 *
 * 为什么用「追加」而不是就地把规则插到对应位置：
 *   1. styles.css 里 .icon-btn 有 3 处定义（约 820 / 4615 / 5099 行），
 *      .panel / .settings-section 也有多代规则并存。CSS 同特异性下【后者胜】，
 *      因此修复必须放在文件末尾才能稳定生效。
 *   2. 追加不改动上方任何既有规则 —— 保留可回溯性，也避免误删
 *      （2026-09-18 曾因一个过宽的正则删代码块而破坏本文件，靠 git 才恢复）。
 *
 * 幂等：已含 PATCH_MARKER 时先移除旧补丁块再追加（支持重复运行更新补丁）。
 *
 * 用法:
 *   node scripts/webui-css-patch.mjs             # 追加/更新补丁
 *   node scripts/webui-css-patch.mjs --dry-run   # 只检查，不写盘
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..');
const CSS = path.join(ROOT, 'services/webui/src/joy_interaction_webui/static/styles.css');
const PATCH = path.join(HERE, 'webui-css-patch.css');
const dryRun = process.argv.includes('--dry-run');

const MARKER_START = '/* ============================================================================\n   2026-09-18 · JoyAI WebUI UI 修复补丁';
const MARKER_END = '/* === END webui-css-patch === */';

if (!fs.existsSync(PATCH)) {
  console.error(`补丁文件不存在: ${PATCH}`);
  process.exit(2);
}
const patchBody = fs.readFileSync(PATCH, 'utf8');
// 补丁本体统一用 LF（与补丁源文件一致）
const patch = patchBody.trimEnd() + '\n\n' + MARKER_END + '\n';

const raw = fs.readFileSync(CSS, 'utf8');
const before = raw;
// ★ 2026-09-19 修复：本文件是 CRLF，而 MARKER_START 用 '\n' 书写。
//   indexOf 只能命中「恰好是 LF」的那份补丁块 → 幂等保护静默失灵，
//   每次运行都追加一份新补丁（曾累积到 3 份，见 scripts/fix-duplicate-patch.mjs）。
//   对策：先归一为 LF 再匹配/拼接，写盘时按原行尾还原。
const CRLF = raw.includes('\r\n');
let css = CRLF ? raw.replace(/\r\n/g, '\n') : raw;

// 幂等：移除**所有**旧补丁块（不只是第一份）——防止历史遗留的多份继续被保留
let removedOld = 0;
for (;;) {
  const s = css.indexOf(MARKER_START);
  if (s < 0) break;
  const e = css.indexOf(MARKER_END, s);
  css = e >= 0
    ? css.slice(0, s) + css.slice(e + MARKER_END.length)
    : css.slice(0, s);
  // 清掉被删块两侧多余的空白，避免反复追加堆积空行
  css = css.replace(/\n{3,}/g, '\n\n').replace(/\s+$/, '');
  removedOld += 1;
}

// 结构自检：追加前后大括号净差必须等于补丁自身的净差
const net = (str) => (str.match(/\{/g) || []).length - (str.match(/\}/g) || []).length;
const netBefore = net(css);
const netPatch = net(patch);
const out = css.trimEnd() + '\n\n' + patch;

const report = {
  file: path.relative(ROOT, CSS),
  dryRun,
  removedOldPatch: removedOld,
  patchBytes: Buffer.byteLength(patch),
  cssBefore: Buffer.byteLength(before),
  cssAfter: Buffer.byteLength(out),
  braceNetBefore: netBefore,
  braceNetAfter: net(out),
  braceNetPatch: netPatch,
  expectedAfter: netBefore + netPatch,
  changed: out !== before,
};

// 自检：净差必须吻合（防止补丁本身或拼接过程损坏结构）
if (report.braceNetAfter !== report.expectedAfter) {
  console.error('❌ 大括号净差不吻合，拒绝写入');
  console.error(JSON.stringify(report, null, 2));
  process.exit(1);
}
// 自检：结果里必须**恰好一份**补丁块（防止幂等失灵导致重复追加）
const countMarkers = (s, m) => s.split(m).length - 1;
if (countMarkers(out, MARKER_END) !== 1) {
  console.error(`❌ 补丁块数量异常：期望 1，实际 ${countMarkers(out, MARKER_END)}（幂等保护失效）`);
  console.error(JSON.stringify(report, null, 2));
  process.exit(1);
}
// 自检：非 dry-run 且文件已有补丁时，必须真的移除了旧块
if (!dryRun && before.includes(MARKER_END) && removedOld === 0) {
  console.error('❌ 检测到既有补丁块却未移除（indexOf 行尾不匹配？），拒绝写入以免重复追加');
  process.exit(1);
}
// 自检：文件不能变小（追加式操作）—— 但清理重复块时会变小，故仅在无旧块时检查
if (!before.includes(MARKER_END) && out.length < before.length) {
  console.error('❌ 结果比原文件小，拒绝写入（疑似误删）');
  process.exit(1);
}

if (dryRun || !report.changed) {
  console.log(JSON.stringify({ ...report, note: dryRun ? 'dry-run' : '无变化' }, null, 2));
  process.exit(0);
}
fs.writeFileSync(CSS, out, 'utf8');
console.log(JSON.stringify(report, null, 2));
