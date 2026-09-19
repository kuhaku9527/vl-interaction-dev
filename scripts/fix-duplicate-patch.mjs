#!/usr/bin/env node
/**
 * 修复 styles.css 中被重复追加的补丁块。
 *
 * 事故（2026-09-19）：
 *   webui-css-patch.mjs 的 MARKER_START 用 '\n' 书写，而 styles.css 是 CRLF 行尾，
 *   于是 css.indexOf(MARKER_START) 恒为 -1 → 幂等保护**从未生效**，
 *   每次运行都把整个补丁再追加一遍（现存 3 份）。
 *   线索其实一直在输出里：`"removedOldPatch": false` —— 首次追加后它就该是 true。
 *
 * 本脚本：保留第一份补丁块之前的全部原始内容，删除其余所有补丁副本，重新追加一次。
 */
import fs from 'node:fs';

const F = 'services/webui/src/joy_interaction_webui/static/styles.css';
const raw = fs.readFileSync(F, 'utf8');
const CRLF = raw.includes('\r\n');
const css = CRLF ? raw.replace(/\r\n/g, '\n') : raw;

const HEAD = '/* ============================================================================\n   2026-09-18 · JoyAI WebUI UI 修复补丁';
const END = '/* === END webui-css-patch === */';

const starts = [];
let i = 0;
while ((i = css.indexOf(HEAD, i)) !== -1) { starts.push(i); i += 1; }
const ends = [];
i = 0;
while ((i = css.indexOf(END, i)) !== -1) { ends.push(i); i += 1; }

console.log(`补丁起始标记 ${starts.length} 处，结束标记 ${ends.length} 处`);
if (starts.length !== ends.length) { console.error('❌ 标记不成对，拒绝处理'); process.exit(1); }
if (starts.length === 0) { console.log('无补丁块，无需处理'); process.exit(0); }

// 每个补丁块的完整范围：从 HEAD 所在注释块起点，到该块之后的 END 结束
const blocks = starts.map((s, k) => ({
  from: css.lastIndexOf('/* ===', s) >= 0 && css.lastIndexOf('/* ===', s) > (k === 0 ? -1 : ends[k - 1]) - 1
        ? s : s,
  to: ends[k] + END.length,
}));
// 起始注释块真正起点：HEAD 之前那行 '/* ====...'
blocks.forEach((b) => {
  const cand = css.lastIndexOf('/* ====', b.from);
  if (cand >= 0 && cand < b.from) b.from = cand;
});

console.log('补丁块:');
blocks.forEach((b, k) => console.log(`  #${k + 1}  ${b.from} .. ${b.to}  (${b.to - b.from} B)`));

// 保留第一块之前的原始内容 + 最后一块之后可能存在的尾巴
const head = css.slice(0, blocks[0].from).replace(/\s+$/, '');
const tail = css.slice(blocks[blocks.length - 1].to).trim();
if (tail) console.log(`⚠️ 末块之后还有 ${tail.length}B 内容；将保留`);

const out = head + (tail ? '\n' + tail : '') + '\n';
fs.writeFileSync(F, CRLF ? out.replace(/\n/g, '\r\n') : out, 'utf8');
console.log(`\n✅ 已清理：${raw.length} → ${out.length} B（删掉 ${starts.length - 1} 份重复补丁）`);
