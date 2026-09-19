#!/usr/bin/env node
/**
 * 把真实 WebUI 页面（services/webui static/）内联成单文件，
 * 以便在 iPolloWork iDesign Studio 的 srcdoc iframe 中渲染与编辑。
 *
 * 背景（实测结论，非猜测）：
 *   Studio 走 srcdoc 内联渲染（studio/dist/assets/index-*.js 里 rg()/L.srcdoc=...），
 *   其资源重写只处理 img[src]（见 objectUrls 分支：querySelectorAll("img[src]")），
 *   不处理 <link rel=stylesheet> / <script src>。
 *   因此相对路径的 styles.css 与 21 个外部 JS 在 Studio 里会全部 404，
 *   必须在接线时内联，否则画布是"裸 HTML"。
 *
 * 用法:
 *   node scripts/idesign-inline.mjs <output.html>
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..');
const STATIC = path.join(ROOT, 'services/webui/src/joy_interaction_webui/static');
const ENTRY = path.join(STATIC, 'index.html');

const out = process.argv[2] || path.join(ROOT, '.cache/idesign/inlined.html');

/** 内联 <script src> 时必须防止提前闭合宿主 script 标签 */
const guardScript = (js) => js.replace(/<\/script/gi, '<\\/script');

const escapeStyle = (css) => css.replace(/<\/style/gi, '<\\/style');

let html = fs.readFileSync(ENTRY, 'utf8');
const stats = { css: [], js: [], missing: [], skipped: [] };

// 1) <link rel="stylesheet" href="styles.css"> -> <style>...</style>
html = html.replace(
  /<link\b[^>]*rel=["']stylesheet["'][^>]*href=["']([^"']+)["'][^>]*>/gi,
  (match, href) => {
    if (/^https?:|^\/\//i.test(href)) {
      stats.skipped.push(`外链样式保留原样: ${href}`);
      return match;
    }
    const file = path.join(STATIC, href.split('?')[0]);
    if (!fs.existsSync(file)) {
      stats.missing.push(href);
      return `<!-- 内联失败，未找到 ${href} -->`;
    }
    const css = fs.readFileSync(file, 'utf8');
    stats.css.push({ href, bytes: Buffer.byteLength(css) });
    return `<style data-inlined-from="${href}">\n${escapeStyle(css)}\n</style>`;
  },
);

// 2) <script src="..."></script> -> 内联脚本
html = html.replace(
  /<script\b([^>]*?)\bsrc=["']([^"']+)["']([^>]*)>\s*<\/script>/gi,
  (match, pre, src, post) => {
    if (/^https?:|^\/\//i.test(src)) {
      stats.skipped.push(`外链脚本保留原样: ${src}`);
      return match;
    }
    const file = path.join(STATIC, src.split('?')[0]);
    if (!fs.existsSync(file)) {
      stats.missing.push(src);
      return `<!-- 内联失败，未找到 ${src} -->`;
    }
    const js = fs.readFileSync(file, 'utf8');
    stats.js.push({ src, bytes: Buffer.byteLength(js) });
    const attrs = `${pre} ${post}`.replace(/\bsrc=["'][^"']*["']/i, '').trim();
    return `<script ${attrs} data-inlined-from="${src}">\n${guardScript(js)}\n</script>`;
  },
);

fs.mkdirSync(path.dirname(out), { recursive: true });
fs.writeFileSync(out, html, 'utf8');

// 3) 自检：内联后不该再残留本地相对引用。
//    注意：必须排除内联脚本体/样式体内部的文本——源码注释里可能出现
//    `Loaded via <script src="./x.js">` 这类字样（render_markdown.js 就有），
//    那不是标签，误判会让自检假阳性。故先剔除已内联的 <script>/<style> 块，
//    再在剩余（真正的标签区）里找引用。
const withoutInlined = html
  .replace(/<script\b[^>]*data-inlined-from=[^>]*>[\s\S]*?<\/script>/gi, '')
  .replace(/<style\b[^>]*data-inlined-from=[^>]*>[\s\S]*?<\/style>/gi, '');
const leftover = [...withoutInlined.matchAll(/<(?:link|script)\b[^>]*(?:href|src)=["']([^"']+)["']/gi)]
  .map((m) => m[1])
  .filter((u) => !/^https?:|^\/\/|^data:|^#/.test(u));

// 装饰性引用（favicon / manifest 等）不阻断渲染，单独列出仅供参考。
const COSMETIC = /favicon|\.ico$|\.webmanifest$|apple-touch-icon/i;
const blockingRefs = leftover.filter((u) => !COSMETIC.test(u));
const cosmeticRefs = leftover.filter((u) => COSMETIC.test(u));

const report = {
  entry: path.relative(ROOT, ENTRY),
  out: path.relative(ROOT, out),
  bytes: Buffer.byteLength(html),
  inlinedCss: stats.css.length,
  inlinedJs: stats.js.length,
  inlinedCssBytes: stats.css.reduce((a, b) => a + b.bytes, 0),
  inlinedJsBytes: stats.js.reduce((a, b) => a + b.bytes, 0),
  missing: stats.missing,
  skipped: stats.skipped,
  blockingRefs,
  cosmeticRefs,
};
console.log(JSON.stringify(report, null, 2));

if (stats.missing.length || blockingRefs.length) process.exitCode = 1;
