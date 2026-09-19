#!/usr/bin/env node
/**
 * 修复「高级」面板的 cat-hidden 作用域（2026-09-19）
 *
 * 缺陷：PANEL_ROOTS.advanced = 'radioSilenceSection'，而我把 Live / Wake 两张卡
 *   插成了它的**兄弟**节点。showSettingsPanel 只对 PANEL_ROOTS 的根元素切
 *   .cat-hidden → 这两张卡在**任何**面板下都可见（切到「外观」也还在）。
 *
 * 修法：新增 #advancedPanel 包裹层，采用本仓库既有模式
 *   —— 与 #appearanceSection 一样用 `display:contents`：
 *     • 布局上子元素仍等同 .settings-body 的直接子元素（卡片样式不变形）
 *     • `.cat-hidden{display:none !important}` 能盖住 inline 的 display:contents，
 *       从而正确整组隐藏
 *   配套：在补丁 CSS 加 `#advancedPanel .settings-section` 卡片选择器
 *   （与已有的 `.settings-body #appearanceSection .settings-section` 同理，
 *    因为 CSS 选择器按 DOM 树匹配，display:contents 不改变 > 的匹配）。
 */
import fs from 'node:fs';

const F = 'services/webui/src/joy_interaction_webui/static/index.html';
const raw = fs.readFileSync(F, 'utf8');
const CRLF = raw.includes('\r\n');
let h = CRLF ? raw.replace(/\r\n/g, '\n') : raw;
const fail = (m) => { console.error('❌ ' + m); process.exit(1); };
const log = [];

const findEnd = (s, start) => {
  const re = /<div\b|<\/div>/g; re.lastIndex = start;
  let d = 0, m;
  while ((m = re.exec(s))) {
    if (m[0] === '</div>') { d--; if (d === 0) return re.lastIndex; } else d++;
  }
  fail('div 不配平');
};
const openOf = (s, idx) => { const i = s.lastIndexOf('<div', idx); return i < 0 ? fail('找不到 <div') : i; };

const radioIdx = h.indexOf('id="radioSilenceSection"');
radioIdx < 0 && fail('找不到 radioSilenceSection');
const radioOpen = openOf(h, radioIdx);
const radioEnd = findEnd(h, radioOpen);

const wakeIdx = h.indexOf('id="wakeAsrSection"');
wakeIdx < 0 && fail('找不到 wakeAsrSection');
const wakeOpen = openOf(h, wakeIdx);
const wakeEnd = findEnd(h, wakeOpen);
if (!(radioOpen < radioEnd && radioEnd <= wakeOpen && wakeOpen < wakeEnd)) fail('区间顺序异常');
log.push(`radio ${radioOpen}..${radioEnd} ／ wake ${wakeOpen}..${wakeEnd}`);

// 确认 radio→wake 之间**恰好**是三张卡（无线电静默 / Live / Wake），无其它元素。
// 计数用「卡片开标签」精确匹配：`<div class="settings-section ..." id="...">`
const span = h.slice(radioOpen, wakeEnd);
const titleNames = [...span.matchAll(/settings-section-title[^>]*>\s*([^<]{1,40})/g)].map((m) => m[1].trim());
log.push(`radio→wake 区间内的卡片标题: ${JSON.stringify(titleNames)}`);
if (titleNames.length !== 3) fail(`预期三张卡，实测 ${titleNames.length}: ${JSON.stringify(titleNames)}`);
// 去掉三张卡的开标签与标题 div 后，区间内不应再有其它的 <div class="settings-section
// 注意：必须要求 settings-section 之后是空格或引号，否则会误匹配 settings-section-title
const cardOpens = (span.match(/<div class="settings-section(?=["\s])/g) || []).length;
if (cardOpens !== 3) fail(`预期 3 个 .settings-section 开标签，实测 ${cardOpens}`);
log.push('相邻性检查通过（三张卡相邻，无其它元素）');

// 包裹层起点：Radio Silence 前的注释（若有）
const radioCommentStart = h.lastIndexOf('<!-- Radio Silence', radioOpen);
const wrapFrom = radioCommentStart >= 0 ? radioCommentStart : radioOpen;

const OPEN = `<!-- 高级面板包裹层（2026-09-19）。
                 为什么需要：PANEL_ROOTS.advanced 原指向 #radioSilenceSection，
                 而 Live / Wake 两张卡是它的**兄弟**节点 —— showSettingsPanel 只对
                 PANEL_ROOTS 的根元素切 .cat-hidden，兄弟不受控
                 （切到「外观」时这两张卡仍然可见，实测复现）。
                 现按本仓库既有模式改成 display:contents 包裹层（同 #appearanceSection）：
                 子元素在布局上等同 .settings-body 直接子元素，卡片样式不变形；
                 而 .cat-hidden 的 display:none !important 能盖住 inline 的 contents。 -->
            <div id="advancedPanel" style="display:contents">
`;
h = h.slice(0, wrapFrom) + OPEN + h.slice(wrapFrom);

// 重新定位 wake 卡并在其后闭合包裹层
const wakeIdx2 = h.indexOf('id="wakeAsrSection"');
const wakeEnd2 = findEnd(h, openOf(h, wakeIdx2));
h = h.slice(0, wakeEnd2) + '\n            </div><!-- /#advancedPanel -->' + h.slice(wakeEnd2);
log.push('包裹层已插入并闭合');

// PANEL_ROOTS 指向包裹层
const oldMap = "            advanced: 'radioSilenceSection',";
h.includes(oldMap) || fail('找不到 PANEL_ROOTS.advanced 映射');
h = h.replace(oldMap, "            advanced: 'advancedPanel',");
log.push('PANEL_ROOTS.advanced → advancedPanel');

// ---- 自检 ----
const od = (h.match(/<div\b/g) || []).length, cd = (h.match(/<\/div>/g) || []).length;
if (od !== cd) fail(`<div> 不配平 ${od}/${cd}`);
['id="advancedPanel"', 'id="radioSilenceSection"', 'id="liveModeSection"', 'id="wakeAsrSection"']
  .forEach((n) => { const c = h.split(n).length - 1; if (c !== 1) fail(`${n} 出现 ${c} 次`); });
const pos = (n) => h.indexOf(n);
if (!(pos('id="advancedPanel"') < pos('id="radioSilenceSection"')
   && pos('id="radioSilenceSection"') < pos('id="liveModeSection"')
   && pos('id="liveModeSection"') < pos('id="wakeAsrSection"'))) fail('DOM 顺序不符合预期');
if (!h.includes("advanced: 'advancedPanel'")) fail('映射未更新');
log.push(`自检通过：<div> ${od}/${cd} 配平、4 个 id 各 1 次、顺序正确`);

fs.writeFileSync(F, CRLF ? h.replace(/\n/g, '\r\n') : h, 'utf8');
console.log(log.map((l) => '  · ' + l).join('\n'));
console.log(`\n✅ 已写入（${raw.length} → ${h.length} B）`);
