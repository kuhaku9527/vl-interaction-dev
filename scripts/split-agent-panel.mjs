#!/usr/bin/env node
/**
 * 把 Agent 从「模型」大类中分离出来，成为独立设置面板。
 *
 * 为什么（用户指出的归类错误，经代码核实成立）：
 *   agent 槽位的真实语义是【后台委派求解器】（services_config.py:53-56）：
 *     "agent": provider = codex|hermes → background-agent POST /v1/provider/route 热切
 *     api_base/api_key 仅为「预留远程 agent」
 *   它跟 LLM / ASR / TTS / Embedding 的差异是本质性的：
 *     - 那四者走「模型推理」协议（/chat/completions、/models），需要 URL+Key+Model
 *     - agent 走 background-agent 的 provider-route 热切，**不是模型调用**
 *   把它放进「模型」大类会让用户误以为要填模型地址/密钥。
 *
 * 同时精简字段：agent 行里从模型模板复制来的
 *   「预设名称 / + / 已保存的预设 / 删除 / API Base URL / API Key / Model」
 *   对 agent 基本无意义（provider 只有 codex|hermes 二选一）。
 * 保留：
 *   - Provider 下拉（真值源，后端 _PROVIDER_CHOICES 白名单校验）
 *   - Test 按钮（探 /v1/provider/route）
 *   - 可选 API Base URL（远程 agent 预留，后端字段确实存在，故不禁用但标注「可选」）
 *
 * 用法: node scripts/split-agent-panel.mjs [--dry-run]
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
const nl = html.includes('\r\n') ? '\r\n' : '\n';

if (html.includes('id="agentPanel"')) {
  console.log(JSON.stringify({ status: 'already-split' }, null, 2));
  process.exit(0);
}

// ---- 1) 摘出 agent 的 service-row（整块）----
function blockRange(marker) {
  const start = html.indexOf(marker);
  if (start < 0) throw new Error(`未找到 ${marker}`);
  // 找到该行行首
  const lineStart = html.lastIndexOf('\n', start) + 1;
  // 从 lineStart 起做 div 配平（按 <div / </div> 计数）
  let depth = 0;
  let i = lineStart;
  const re = /<div\b|<\/div>/g;
  re.lastIndex = lineStart;
  let m;
  while ((m = re.exec(html)) !== null) {
    depth += m[0] === '</div>' ? -1 : 1;
    if (depth === 0) {
      const end = m.index + m[0].length;
      // 扩展到行尾
      const lineEnd = html.indexOf('\n', end);
      return [lineStart, lineEnd < 0 ? html.length : lineEnd];
    }
  }
  throw new Error('agent 块未配平');
}

const [aStart, aEnd] = blockRange('data-service="agent"');
const agentBlock = html.slice(aStart, aEnd);
if (!agentBlock.includes('svc-agent-provider')) throw new Error('摘出的块不含 agent provider');

// ---- 2) 从 servicesPanel 内删掉这块 ----
html = html.slice(0, aStart) + html.slice(aEnd);

// ---- 3) 在 voicePanel 之前插入独立的 agentPanel ----
const agentRow = [
  '                    <div class="service-row" data-service="agent" data-mode="cloud">',
  '                        <div class="service-row-header"><i data-lucide="bot"></i><span>委派求解器</span><span class="service-badge" id="badge-agent">--</span></div>',
  '                        <!-- Agent 不是模型：它是后台委派后端（外接 codex / hermes），',
  '                             经 background-agent POST /v1/provider/route 热切，不走模型推理协议。',
  '                             故此处只保留「选哪个 provider」这一真值源，',
  '                             以及可选的远程 agent 地址（后端字段确实存在）。',
  '                             详见 services_config.py:53-56 与 _PROVIDER_CHOICES:106-110。 -->',
  '                        <div class="form-group"><label data-i18n>Provider</label><select id="svc-agent-provider"><option value="codex">codex</option><option value="hermes">hermes</option></select></div>',
  '                        <div class="form-group"><label>API 基础地址 <span class="opt-tag">可选</span></label><input type="text" id="svc-agent-api-base" placeholder="留空则用本机 background-agent"></div>',
  '                        <div class="input-hint">用于远程 agent 场景；本机部署留空即可。</div>',
  '                        <div class="action-row">',
  '                            <button class="icon-btn" id="svc-agent-test-btn" type="button" title="测试委派后端连通性" data-i18n-title="测试委派后端连通性"><i data-lucide="plug-zap"></i><span id="svc-agent-test-label" data-i18n>Test</span></button>',
  '                            <span id="svc-agent-test-status" class="input-hint" style="margin: 0;" role="status" aria-live="polite"></span>',
  '                        </div>',
  '                    </div>',
].join(nl);

const panel = [
  '',
  '            <!-- 2026-09-18（用户要求）：Agent 从「模型」大类分离为独立面板。',
  '                 原因见 services_config.py:53-56 —— agent 是后台委派求解器（codex/hermes），',
  '                 不是模型推理服务，放进「模型」会误导用户去填模型地址/密钥。 -->',
  '            <div class="panel settings-root-panel" id="agentPanel">',
  '                <div class="panel-header settings-root-header">',
  '                    <div class="panel-title" title="后台委派求解器：外接 codex 或 hermes 处理复杂任务" data-i18n-title="后台委派求解器：外接 codex 或 hermes 处理复杂任务">',
  '                        <span class="panel-icon"><i data-lucide="bot"></i></span>',
  '                        <span class="panel-title-text">委派</span>',
  '                    </div>',
  '                </div>',
  '                <div class="panel-content settings-root-content" id="agentConfig">',
  '                    <div class="input-hint" style="margin-bottom: 12px;">复杂任务（代码执行、工具调用）交给外部 agent 程序处理；主对话不受其影响。</div>',
  agentRow,
  '                </div>',
  '            </div>',
  '',
].join(nl);

const voiceMarker = '            <!-- Voice panel: TTS only';
const vIdx = html.indexOf(voiceMarker);
if (vIdx < 0) throw new Error('未找到 voicePanel 插入点');
html = html.slice(0, vIdx) + panel + nl + html.slice(vIdx);

// ---- 4) 左侧导航加一项 ----
const navMarker = '<button class="nav-item" data-panel="voice"';
const nIdx = html.indexOf(navMarker);
if (nIdx < 0) throw new Error('未找到 voice 导航项');
const navLineStart = html.lastIndexOf('\n', nIdx) + 1;
const navIndent = html.slice(navLineStart, nIdx);
const newNav = `${navIndent}<button class="nav-item" data-panel="agent" type="button" title="后台委派求解器：选 codex 或 hermes 处理复杂任务" data-i18n-title="后台委派求解器：选 codex 或 hermes 处理复杂任务"><i data-lucide="bot"></i> 委派</button>${nl}`;
html = html.slice(0, navLineStart) + newNav + html.slice(navLineStart);

// ---- 5) PANEL_ROOTS 注册 ----
html = html.replace(
  /(\s+)voice: 'voicePanel',/,
  `$1voice: 'voicePanel',$1// 2026-09-18：agent 从「模型」分离为独立面板（它不是模型推理服务）$1agent: 'agentPanel',`,
);

// ---- 6) 守恒自检 ----
// 预期变化（有意为之，逐项声明）：
//   nav-item   +1  （新增「委派」导航项）
//   panel      +1  （新增 #agentPanel）
//   id         -4  （精简 agent 的预设字段：provider-name / provider-add /
//                    provider-pick / provider-del —— 它们由模型模板复制而来，
//                    对「二选一 provider」的 agent 无意义）
const c = (re) => (original.match(re) || []).length;
const c2 = (re) => (html.match(re) || []).length;
const EXPECTED_DELTA = {
  id: -4,
  'data-service': 0,
  'nav-item': +1,
  panel: +1,
};
const actual = {
  id: c2(/\bid="/g) - c(/\bid="/g),
  'data-service': c2(/data-service="/g) - c(/data-service="/g),
  'nav-item': c2(/class="nav-item"/g) - c(/class="nav-item"/g),
  panel: c2(/class="panel settings-root-panel"/g) - c(/class="panel settings-root-panel"/g),
};
const violations = Object.entries(actual).filter(([k, d]) => d !== EXPECTED_DELTA[k]);
if (violations.length) {
  console.error('❌ 守恒失败（实际 delta 与预期不符）:');
  for (const [k, d] of violations) {
    console.error(`   ${k}: 实际 ${d >= 0 ? '+' : ''}${d}，预期 ${EXPECTED_DELTA[k] >= 0 ? '+' : ''}${EXPECTED_DELTA[k]}`);
  }
  process.exit(1);
}

const report = {
  file: path.relative(ROOT, FILE),
  dryRun,
  agentBlockLines: agentBlock.split(nl).length,
  deltas: actual,
  expectedDeltas: EXPECTED_DELTA,
  agentIdStillUnique: (html.match(/id="svc-agent-provider"/g) || []).length === 1,
  agentRemovedFromServicesPanel: !html.slice(
    html.indexOf('id="servicesConfig"'),
    html.indexOf('id="agentPanel"') > 0 ? html.indexOf('id="agentPanel"') : html.length,
  ).includes('svc-agent-provider'),
  note: 'agent 独立成面板；字段精简为 Provider + 可选 API Base + Test',
};

if (dryRun) {
  console.log(JSON.stringify({ ...report, status: 'dry-run' }, null, 2));
  process.exit(0);
}
fs.writeFileSync(FILE, html, 'utf8');
console.log(JSON.stringify({ ...report, status: 'written' }, null, 2));
