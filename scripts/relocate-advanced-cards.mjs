#!/usr/bin/env node
/**
 * 卡片归位 + 按钮回家（2026-09-19 用户拍板）
 *
 * 1. 「Live 常驻模式」与「Wake / ASR」两张卡片从「外观」移到「高级」
 *    —— 它们不是外观（视觉）功能。DOM 上落到 #radioSilenceSection 之后，
 *       成为 .settings-body 的直接子元素（卡片样式依赖该选择器）。
 * 2. 「实时」按钮（#liveModeBtn）回到聊天输入栏原位（用户要求：方便直接开启）。
 * 3. 「主动搭话」由原生 checkbox 改用设计系统既有的 .toggle-switch。
 *
 * 纪律：全部改动带断言；<div> 用「注释等长替换后再配平」的方式定位，避免注释里的
 * <div 字样干扰计数。任何一步不匹配即中止，不写盘。
 */
import fs from 'node:fs';

const F = 'services/webui/src/joy_interaction_webui/static/index.html';
const raw = fs.readFileSync(F, 'utf8');
// 本文件是 CRLF 行尾；脚本内所有模式都用 \n 书写，故先归一为 LF，
// 写盘前再还原 CRLF（保持仓库既有行尾风格，避免整文件 diff 噪声）。
const CRLF = raw.includes('\r\n');
let h = CRLF ? raw.replace(/\r\n/g, '\n') : raw;
const orig = raw;
const log = [];
const fail = (m) => { console.error('❌ ' + m); process.exit(1); };

// ---- 注释等长替换：保持索引不变，供配平计数使用 ----
const sanitized = (s) => s.replace(/<!--[\s\S]*?-->/g, (m) => ' '.repeat(m.length));

// ★ 2026-09-19 修复的关键 bug（两次踩坑，教训）：
//   1) 原实现用 lastIndexOf('<div', idx) —— 当 idx 落在卡片**标题**上时，
//      匹配到的是标题自己的 <div class="settings-section-title">，于是 matchDivEnd
//      从标题内部起算、提前返回，提取出的块被截断（Wake 卡只剩 349B，
//      正文与闭合 </div> 全丢，进而吞掉后面的整个外观面板）。
//   2) 改成「找包含 idx 的最外/最内 <div>」都不对：前者会拿到 #settingsModal 根，
//      后者对标题锚点仍会拿到标题本身 —— 锚点语义本身就不该是"最近的 div"。
//   ✅ 正确做法：以**卡片专属前导注释**为锚，取其后第一个
//      `<div class="settings-section ...">` 作为卡起点。语义明确、无歧义。
function cardStartAfterComment(s, commentStart) {
  const i = s.indexOf('<div class="settings-section', commentStart);
  if (i < 0) fail('注释之后找不到 .settings-section');
  // 确保这个 div 确实属于该注释（中间没有别的卡）
  return i;
}
function matchDivEnd(s, start) {
  const clean = sanitized(s);
  const re = /<div\b|<\/div>/g;
  re.lastIndex = start;
  let depth = 0, m;
  while ((m = re.exec(clean))) {
    if (m[0] === '</div>') { depth--; if (depth === 0) return re.lastIndex; }
    else depth++;
  }
  fail('div 不配平，起点 ' + start);
}

// ================= 1. 提取两块卡片 =================
// 一律以「卡片专属前导注释」为锚，再取其后第一个 .settings-section —— 语义明确。
const liveCommentStart = h.lastIndexOf('<!-- 2026-09-19（路线 B）');
liveCommentStart < 0 && fail('找不到 Live 卡片的前导注释');
const liveOpen = cardStartAfterComment(h, liveCommentStart);
const liveEnd = matchDivEnd(h, liveOpen);
let liveBlock = h.slice(liveCommentStart, liveEnd);

const wakeCommentStart = h.lastIndexOf('<!-- [ASR promotion]');
wakeCommentStart < 0 && fail('找不到 Wake/ASR 前导注释');
const wakeOpen = cardStartAfterComment(h, wakeCommentStart);
const wakeEnd = matchDivEnd(h, wakeOpen);
let wakeBlock = h.slice(wakeCommentStart, wakeEnd);

const radioIdIdx = h.indexOf('id="radioSilenceSection"');
radioIdIdx < 0 && fail('找不到 radioSilenceSection');
const radioEnd = matchDivEnd(h, h.indexOf("<div class=\"settings-section full-width\" id=\"radioSilenceSection\""));

log.push(`提取 Live 卡 ${liveBlock.length}B（${liveCommentStart}..${liveEnd}）`);
log.push(`提取 Wake 卡 ${wakeBlock.length}B（${wakeCommentStart}..${wakeEnd}）`);
log.push(`高级锚点 radioSilenceSection 结束于 ${radioEnd}`);

// ★ 完整性断言：提取出的块必须自洽（含完整闭合）且体积合理。
//   这正是本次事故的漏网处：Wake 卡因 openDivOf 定位错误被截断成 349B
//   （丢了正文与 </div>），当时无任何断言发现，直到渲染时它吞掉外观面板才暴露。
if (liveBlock.length < 3000) fail(`Live 卡疑似截断：仅 ${liveBlock.length}B`);
if (wakeBlock.length < 600) fail(`Wake 卡疑似截断：仅 ${wakeBlock.length}B`);
const balancedDivs = (b) => (b.match(/<div\b/g) || []).length === (b.match(/<\/div>/g) || []).length;
if (!balancedDivs(liveBlock)) fail('Live 卡 <div> 不配平（提取不完整）');
if (!balancedDivs(wakeBlock)) fail('Wake 卡 <div> 不配平（提取不完整）');
log.push(`完整性断言通过：Live ${(liveBlock.match(/<div\b/g) || []).length} 对 / Wake ${(wakeBlock.match(/<div\b/g) || []).length} 对 div 均配平`);

// ================= 2. 在两块中原位做内容调整 =================

// 2a) Live 卡：注释更新为「已归位到高级」
const oldLiveComment = liveBlock.slice(0, liveBlock.indexOf('-->') + 3);
const newLiveComment = [
  '<!-- Live 常驻模式（2026-09-19 用户拍板：不是「外观」功能，归入「高级」）。',
  '     这些控件原先在 .prompt-editor-inline 内靠作用域 CSS 隐藏，从未在设置页建过',
  '     等价入口 → 功能不可达；且全屏时 #promptEditor 被搬走，隐藏契约失效、控件集体复活。',
  '     现已真删出输入栏、落到设置页。契约：id 与 JS 绑定一律不变。',
  '     注：「实时」按钮不在本卡 —— 用户要求它回到聊天输入栏，方便直接开启。 -->',
].join('\n                    ');
liveBlock = liveBlock.replace(oldLiveComment, newLiveComment);
liveBlock.includes('用户要求它回到聊天输入栏') || fail('Live 注释替换失败');

// 2b) Live 卡：标题更新
const oldLiveTitle = 'data-i18n-title="Live 常驻模式：唤醒方式、声纹注册、画面来源与主动搭话">Live 常驻模式</div>';
const newLiveTitle = 'data-i18n-title="Jarvis 唤醒词监听、声纹注册、画面来源与主动搭话">Live 常驻模式</div>';
liveBlock.includes(oldLiveTitle) || fail('找不到 Live 卡原标题');
liveBlock = liveBlock.replace(oldLiveTitle, newLiveTitle);
liveBlock.includes(newLiveTitle) || fail('Live 标题替换失败');

// 2c) 取出 #liveModeBtn（要送回聊天栏），并把「唤醒方式」项改成单按钮
const liveBtnRe = /[ \t]*<button class="chat-prompt-action listen live-mode" id="liveModeBtn"[\s\S]*?<\/button>\n/;
const liveBtnMatch = liveBlock.match(liveBtnRe);
liveBtnMatch || fail('找不到 #liveModeBtn');
const liveBtnHtml = liveBtnMatch[0].replace(/^[ \t]+/gm, (mm) => mm); // 保持原缩进
liveBlock = liveBlock.replace(liveBtnRe, '');

const oldWakeItemLabel = '<div class="settings-item-label" data-i18n>唤醒方式</div>\n                                <div class="settings-item-description" data-i18n>Jarvis 监听唤醒词，或 Live 常驻免唤醒（二选一）</div>';
const newWakeItemLabel = '<div class="settings-item-label" data-i18n>Jarvis 唤醒词监听</div>\n                                <div class="settings-item-description" data-i18n>监听唤醒词 “bt” 触发对话；与聊天栏的「实时」互斥（二选一）</div>';
liveBlock.includes(oldWakeItemLabel) || fail('找不到「唤醒方式」标签');
liveBlock = liveBlock.replace(oldWakeItemLabel, newWakeItemLabel);

// 2d) Live 卡：主动搭话改用 .toggle-switch，并独立成 settings-item
const oldStack = `                            <div class="live-mode-stack" id="liveVideoRow">
                                <button class="chat-prompt-action live-video" id="liveVideoBtn" title="打开画面（屏幕/摄像头，1fps 帧推送）" type="button" aria-label="打开画面" disabled>
                                    <i data-lucide="video"></i>
                                    <span class="live-video-label">开画面</span>
                                </button>
                                <select class="live-video-source" id="liveVideoSource" title="画面来源（屏幕 / 摄像头，单选）" aria-label="画面来源" disabled>
                                    <option value="screen" data-i18n>屏幕</option>
                                    <option value="camera" data-i18n>摄像头</option>
                                </select>
                                <label class="live-proactive-toggle" title="主动搭话：无用户语音时周期性看画面，有值得说的就开口">
                                    <input type="checkbox" id="liveProactiveToggle" aria-label="主动搭话" disabled>
                                    <span class="live-proactive-label">主动搭话</span>
                                </label>
                                <span class="live-video-hint" id="liveVideoHint" role="status" aria-live="polite"></span>
                                <span class="live-proactive-hint" id="liveProactiveHint" role="status" aria-live="polite"></span>
                            </div>`;
const newStack = `                            <div class="live-mode-stack" id="liveVideoRow">
                                <button class="chat-prompt-action live-video" id="liveVideoBtn" title="打开画面（屏幕/摄像头，1fps 帧推送）" type="button" aria-label="打开画面" disabled>
                                    <i data-lucide="video"></i>
                                    <span class="live-video-label">开画面</span>
                                </button>
                                <select class="live-video-source" id="liveVideoSource" title="画面来源（屏幕 / 摄像头，单选）" aria-label="画面来源" disabled>
                                    <option value="screen" data-i18n>屏幕</option>
                                    <option value="camera" data-i18n>摄像头</option>
                                </select>
                                <span class="live-video-hint" id="liveVideoHint" role="status" aria-live="polite"></span>
                            </div>
                        </div>

                        <div class="settings-item">
                            <div>
                                <div class="settings-item-label" data-i18n>主动搭话</div>
                                <div class="settings-item-description" data-i18n>无用户语音时周期性看画面，有值得说的就开口</div>
                                <div class="settings-item-description" id="liveProactiveHint" role="status" aria-live="polite" style="margin-top: 4px;"></div>
                            </div>
                            <label class="toggle-switch" title="主动搭话：无用户语音时周期性看画面，有值得说的就开口">
                                <input type="checkbox" id="liveProactiveToggle" aria-label="主动搭话" disabled>
                                <span class="toggle-slider"></span>
                            </label>`;
liveBlock.includes(oldStack) || fail('找不到 liveVideoRow 块');
liveBlock = liveBlock.replace(oldStack, newStack);
log.push('Live 卡：取出 #liveModeBtn、主动搭话改用 .toggle-switch');

// 2e) Wake 卡：加 id，标题/描述改准确（它不是「唤醒词设置」）
wakeBlock = wakeBlock.replace(
  '<!-- [ASR promotion] ASR辅助唤醒 toggle (joyai-asr-promotion-ui) -->',
  `<!-- Wake / ASR（2026-09-19 用户拍板：不是「外观」功能，归入「高级」）。
     注意命名：本卡**不是**唤醒词配置。唤醒词在后端写死为 "bt"
     （jarvis_config.py:162 wake_word / services/asr/jarvis/kws.py:29），
     且 KWS 是自训模型（sherpa-onnx v4），换词需重新训练 —— 这是历史取舍。
     本卡只做一件事：KWS 漏检时用本地 paraformer ASR 兜底唤醒。 -->`);
wakeBlock = wakeBlock.replace(
  '<div class="settings-section collapsed">\n                        <div class="settings-section-title"',
  '<div class="settings-section collapsed" id="wakeAsrSection">\n                        <div class="settings-section-title"');
wakeBlock.includes('id="wakeAsrSection"') || fail('Wake 卡加 id 失败');
log.push('Wake 卡：加 id="wakeAsrSection" + 命名澄清注释');

// ================= 3. 组装：移除原位置，插到 radio 之后 =================
if (!(liveCommentStart < wakeCommentStart)) fail('顺序假设不成立（live 应在 wake 之前）');
const A = h.slice(0, liveCommentStart);
const B = h.slice(liveEnd, wakeCommentStart);
const C = h.slice(wakeEnd);
h = A + B + C;
log.push(`已从「外观」移除两块：${orig.length} → ${h.length}B`);

// 在 radioSilenceSection 结束处插入
const radioEnd2 = matchDivEnd(h, h.indexOf('<div class="settings-section full-width" id="radioSilenceSection"'));
const insertAt = radioEnd2;
h = h.slice(0, insertAt) + '\n\n' + liveBlock + '\n\n' + wakeBlock + h.slice(insertAt);
log.push(`已插入到高级锚点之后（位置 ${insertAt}）`);

// ================= 4. 实时按钮回聊天栏 =================
const promptTextAnchor = '            <textarea class="chat-prompt-input" id="promptText" rows="1" placeholder="和 BT-7274 对话..."></textarea>';
h.includes(promptTextAnchor) || fail('找不到输入栏 textarea 锚点');
const liveBtnForBar = `            <button class="chat-prompt-action listen live-mode" id="liveModeBtn" title="进入 Live 常驻模式" type="button" role="radio" aria-checked="false" aria-label="实时">
                <i data-lucide="radio-tower"></i>
                <span class="ctrl-label">实时</span>
            </button>
`;
h = h.replace(promptTextAnchor, liveBtnForBar + promptTextAnchor);
log.push('「实时」按钮已回到聊天输入栏（textarea 之前）');

// ================= 5. 高级导航项描述更新 =================
h = h.replace(
  'title="无线电静默：保留唤醒通道的高级设置" data-i18n-title="无线电静默：保留唤醒通道的高级设置"',
  'title="高级：无线电静默、Live 常驻模式与唤醒兜底" data-i18n-title="高级：无线电静默、Live 常驻模式与唤醒兜底"');
log.push('「高级」导航项描述已更新');

// ================= 6. 结构自检 =================
const need = ['id="liveModeSection"', 'id="wakeAsrSection"', 'id="liveModeBtn"', 'id="btListenBtn"',
              'id="liveProactiveToggle"', 'id="promptText"'];
need.forEach((n) => {
  const c = h.split(n).length - 1;
  if (c !== 1) fail(`期望 "${n}" 出现 1 次，实际 ${c} 次`);
});
// 「主动搭话」由原生 checkbox 改为 .toggle-switch → 全库 +1（声明式预期）
const before = orig.split('toggle-slider').length - 1;
const after = h.split('toggle-slider').length - 1;
if (after !== before + 1) fail(`toggle-slider 期望 ${before} → ${before + 1}，实际 ${before} → ${after}`);
h.includes('live-proactive-toggle') && fail('残留 live-proactive-toggle');
const od = (h.match(/<div\b/g) || []).length, cd = (h.match(/<\/div>/g) || []).length;
if (od !== cd) fail(`<div> 不配平: ${od} vs ${cd}`);
log.push(`结构自检通过：关键 id 各 1 次、<div> ${od}/${cd} 配平`);

fs.writeFileSync(F, CRLF ? h.replace(/\n/g, '\r\n') : h, 'utf8');
console.log(log.map((l) => '  · ' + l).join('\n'));
console.log(`\n✅ 已写入 ${F}（${orig.length} → ${h.length} B）`);
