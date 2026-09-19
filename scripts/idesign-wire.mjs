#!/usr/bin/env node
/**
 * 把「真实 WebUI 页面」接入 iPolloWork iDesign Studio 的画布。
 *
 * 为什么要这一步（实测结论，非推测）：
 *   1) Studio 画布加载的入口由 design/<sessionId>/manifest.json 的 `entry` 决定
 *      （deepseek-idesign/lib/index.js:4026 —— entry: `design/${projectId}/${manifest.entry}`），
 *      所以「接哪个文件」是可配的，不需要重做设计。
 *   2) Studio 用 srcdoc 内联渲染 HTML，其资源重写只覆盖 img[src]
 *      （studio 产物里 objectUrls 分支只 querySelectorAll("img[src]")），
 *      不处理 <link rel=stylesheet> / <script src>。因此真实页面的
 *      styles.css + 21 个外部 JS 必须内联，否则画布渲染成裸 HTML。
 *   3) 真实页面 index.html 是「自包含」的：无 <style> 块、CSS 全在 styles.css、
 *      HTML 内无 <img>，styles.css 的 url() 只引用 data: 与 SVG fragment。
 *      这正是内联可行的前提（已实测：内联版截图与真实页 SHA-256 完全一致）。
 *
 * 数据边界：Studio 只能读写 design/ 下的文件（lib/index.js:3903 safeRelativePath
 * 默认 prefix="design/"），因此接线只能「拷贝一份到 design/」，无法原地指向
 * services/webui/。这也正好符合 AGENTS.md 的隔离要求：设计迭代不污染真实前端。
 *
 * 用法:
 *   node scripts/idesign-wire.mjs [--session <id>] [--dry-run]
 */
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..');
const STATIC = path.join(ROOT, 'services/webui/src/joy_interaction_webui/static');
const DESIGN_DIR = path.join(ROOT, 'design');

const argv = process.argv.slice(2);
const dryRun = argv.includes('--dry-run');
const sessIdx = argv.indexOf('--session');
const sessionArg = sessIdx >= 0 ? argv[sessIdx + 1] : null;

/**
 * 读取 DSH 当前活跃的会话 id。
 *
 * ⚠️ 重要背景（2026-09-18 实测查清，推翻了此前「用户在开新对话」的错误判断）：
 *   DSH 会因【上下文续接】或【编辑已发送消息】而把当前对话 fork 成新 sessionId：
 *     - dsh-easyrewrite（编辑并重发）调用 ctx.sessions.fork —— 见其 lib/index.js:6/10/230/421
 *     - 上下文续接：新会话带 parentSession + seedLength（实测 127k→234k→469k 递增）
 *   新会话的记录里只有一条 {"type":"session", parentSession, seedLength} 元数据。
 *   而 deepseek-idesign 用 sessionId 硬编码拼接项目路径
 *   （lib/index.js:3912-3914 → design/<sessionId>/），
 *   => sessionId 一变，插件就认为「这是新项目」，生成空白画布。
 *
 * 所以「dsh.sessions.current」在这里是【正确信号】：它就是用户当前正在看的对话。
 * 此前把它当作不可靠来源而拒绝使用，是因为误判了现象原因（怪用户开新对话）。
 * 现在改为：默认读它，并额外做一致性校验（见 assertCurrentSessionMatches）。
 */
function currentSessionId() {
  const store = path.join(
    process.env.APPDATA || '',
    'dsh-desktop/harness/profiles/web/desktop-storage.json',
  );
  if (!fs.existsSync(store)) return null;
  const raw = JSON.parse(fs.readFileSync(store, 'utf8'));
  const cur = raw['dsh.sessions.current'];
  if (!cur) return null;
  try {
    return JSON.parse(cur).sessionId ?? null;
  } catch {
    return null;
  }
}

/** 列出 design/ 下所有 session-* 目录（用于同步与清理）。 */
function listDesignSessionDirs() {
  if (!fs.existsSync(DESIGN_DIR)) return [];
  return fs
    .readdirSync(DESIGN_DIR, { withFileTypes: true })
    .filter((d) => d.isDirectory() && /^session-/.test(d.name))
    .map((d) => path.join(DESIGN_DIR, d.name));
}

const pruneOld = !argv.includes('--keep-old');
const syncAll = argv.includes('--all-sessions');

// 无显式 --session 时，读「DSH 当前活跃会话」。
// 这正是用户正在看的对话 —— 见 currentSessionId() 上方的长注释（fork 机制说明）。
const sessionId = sessionArg || currentSessionId();

if (!sessionId && !syncAll) {
  console.error(
    [
      '无法确定会话，且未指定 --all-sessions。',
      '',
      '用法：',
      '  node scripts/idesign-wire.mjs                      # 同步到 DSH 当前活跃会话（推荐）',
      '  node scripts/idesign-wire.mjs --all-sessions       # 同步到 design/ 下所有 session-* 目录',
      '  node scripts/idesign-wire.mjs --session <id>       # 显式指定会话',
      '  node scripts/idesign-wire.mjs --dry-run            # 预览（不写盘、不清理）',
      '  node scripts/idesign-wire.mjs --keep-old           # 不清理非当前会话的旧目录',
      '',
      '背景：DSH 会因「上下文续接」或 dsh-easyrewrite 编辑重发而 fork 新 sessionId（实测：',
      '      dsh-easyrewrite/lib/index.js:6/10/230/421 调用 ctx.sessions.fork；续接会话带',
      '      parentSession + seedLength）。deepseek-idesign 按 sessionId 定位项目目录',
      '      （lib/index.js:3912-3914），故每次 fork 都会让 Studio 变成空白画布。',
      '      本脚本据此每次同步都重新指向当前会话，并（默认）清理旧目录。',
      '',
    ].join('\n'),
  );
  process.exit(2);
}
if (sessionId && !/^session-[A-Za-z0-9-]+$/.test(sessionId)) {
  console.error(`sessionId 形态异常，拒绝继续: ${sessionId}`);
  process.exit(2);
}

/** 需要写入的目标目录列表。 */
const targets = syncAll
  ? listDesignSessionDirs()
  : [path.join(DESIGN_DIR, sessionId)];
// --all-sessions 时确保当前会话在列表内
if (syncAll && !targets.includes(path.join(DESIGN_DIR, sessionId))) {
  targets.push(path.join(DESIGN_DIR, sessionId));
}

const projectDir = path.join(DESIGN_DIR, sessionId);
const targetHtml = path.join(projectDir, 'index.html');
const targetTokens = path.join(projectDir, 'design-tokens.css');

// ---- 1) 先跑内联，得到自包含的真实页面 ----
const inlinedPath = path.join(ROOT, '.cache/idesign/inlined.html');
execFileSync(process.execPath, [path.join(HERE, 'idesign-inline.mjs'), inlinedPath], {
  stdio: ['ignore', 'ignore', 'inherit'],
});
let html = fs.readFileSync(inlinedPath, 'utf8');

// ---- 2) 挂上 Studio 的主题令牌钩子 ----
// Studio 通过 rel/data 属性定位令牌样式表（默认模板用 data-ipw-design-tokens）。
// 真实页没有这个契约，导致样式面板认不出元素；这里补一条引用。
const TOKENS_LINK = '  <link rel="stylesheet" href="design-tokens.css" data-ipw-design-tokens>\n';
if (!html.includes('data-ipw-design-tokens')) {
  html = html.replace(/<head(\s[^>]*)?>/i, (m) => `${m}\n${TOKENS_LINK}`);
}
// 注入 theme-role，供 Studio 的分组编辑识别页面根
if (!/data-ipw-theme-role/.test(html)) {
  html = html.replace(/<body(\s[^>]*)?>/i, (m) => m.replace(/<body/i, '<body data-ipw-theme-role="page"'));
}

// ---- 3) 生成 design-tokens.css：把真实页的真实取值映射到 ipw 契约 ----
const stylesCss = fs.readFileSync(path.join(STATIC, 'styles.css'), 'utf8');
function pickVar(name) {
  const re = new RegExp(`--${name}\\s*:\\s*([^;]+);`);
  const m = stylesCss.match(re);
  return m ? m[1].trim() : null;
}
const tokens = {
  'ipw-color-bg': pickVar('bg'),
  'ipw-color-surface': pickVar('bg-elev'),
  'ipw-color-text': pickVar('text'),
  'ipw-color-muted': pickVar('text-2'),
  'ipw-color-border': pickVar('border'),
  'ipw-color-primary': pickVar('joy-red'),
  'ipw-color-secondary': pickVar('joy-red-soft'),
  'ipw-color-accent': pickVar('brand-bright'),
  'ipw-card-bg': pickVar('card-bg'),
  'ipw-card-radius': pickVar('radius'),
};
const resolved = Object.entries(tokens).filter(([, v]) => v);
const unresolved = Object.entries(tokens).filter(([, v]) => !v).map(([k]) => k);

const tokensCss = [
  '/* ipw-theme:start */',
  '/* 由 scripts/idesign-wire.mjs 从 services/webui/.../static/styles.css 的真实取值生成。',
  '   这些 --ipw-* 是 iPolloWork Design Studio 的主题契约名（见 deepseek-idesign',
  '   DEFAULT_TOKENS）；右侧值取自真实 WebUI，保证画布观感与线上一致。 */',
  ':root {',
  ...resolved.map(([k, v]) => `  --${k}: ${v};`),
  '}',
  '/* ipw-theme:end */',
  '',
].join('\n');

const report = {
  sessionId,
  projectDir: path.relative(ROOT, projectDir),
  source: path.relative(ROOT, path.join(STATIC, 'index.html')),
  targetHtml: path.relative(ROOT, targetHtml),
  targetTokens: path.relative(ROOT, targetTokens),
  htmlBytes: Buffer.byteLength(html),
  htmlSha256: crypto.createHash('sha256').update(html).digest('hex'),
  tokensResolved: resolved.length,
  tokensUnresolved: unresolved,
  syncAll,
  pruneOld,
  wroteTo: [],
  pruned: [],
  dryRun,
};

if (dryRun) {
  report.wroteTo = targets.map((d) => path.relative(ROOT, d));
  if (pruneOld) {
    report.pruned = listDesignSessionDirs()
      .filter((d) => path.basename(d) !== sessionId)
      .map((d) => path.relative(ROOT, d));
  }
  console.log(JSON.stringify(report, null, 2));
  process.exit(0);
}

// ---- 写入：当前会话目录（必写），或 --all-sessions 时全部目录 ----
for (const dir of targets) {
  fs.mkdirSync(dir, { recursive: true });
  // 保留每个目录已有的 manifest.json（Studio 用它决定 entry）；缺失时补一份默认的
  const manifestPath = path.join(dir, 'manifest.json');
  if (!fs.existsSync(manifestPath)) {
    fs.writeFileSync(manifestPath, JSON.stringify({
      schemaVersion: 1,
      id: 'ipollowork.deepseek-harness.design',
      version: '1.0.0',
      kind: 'design',
      category: 'site',
      subcategory: 'website',
      style: 'minimal',
      tags: ['deepseek-harness', 'studio', 'joyai-webui'],
      surface: 'design',
      title: 'JoyAI VL · WebUI 设计稿',
      description: '真实 WebUI 的 Studio 可编辑镜像，用于交互式 UI 重设计。',
      cover: 'index.html',
      entry: 'index.html',
      source: { name: 'iPolloWork', license: 'MIT' },
      designSystem: {
        tokenVersion: 1,
        editableGroups: ['theme', 'background', 'typography', 'components'],
        tokens: 'design-tokens.css',
        variables: [],
      },
      applyChecklist: ['Preserve the current document structure and linked design token contract.'],
      minimumAppVersion: '0.21.2',
    }, null, 2) + '\n', 'utf8');
  }
  // 覆盖前备份出厂占位页（仅当当前 index.html 仍是占位页时）
  const idx = path.join(dir, 'index.html');
  if (fs.existsSync(idx)) {
    const cur = fs.readFileSync(idx, 'utf8');
    if (cur.length < 3000 && cur.includes('Your design starts here')) {
      fs.writeFileSync(idx + '.placeholder-backup', cur, 'utf8');
    }
  }
  fs.writeFileSync(idx, html, 'utf8');
  fs.writeFileSync(path.join(dir, 'design-tokens.css'), tokensCss, 'utf8');
  report.wroteTo.push(path.relative(ROOT, dir));
}

// ---- 清理：删除非当前会话的旧目录（DSH fork 留下的空壳/过期镜像）----
if (pruneOld && !syncAll) {
  for (const dir of listDesignSessionDirs()) {
    if (path.basename(dir) === sessionId) continue;
    fs.rmSync(dir, { recursive: true, force: true });
    report.pruned.push(path.relative(ROOT, dir));
  }
}

console.log(JSON.stringify(report, null, 2));
