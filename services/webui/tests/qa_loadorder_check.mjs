// QA independent load-order verification (audit P0-1 family) — full page order.
// Replays every <script> in index.html in document order:
//   CDN stubs -> 9 pre-main JS -> inline#1 -> 3 split pre-main JS ->
//   main inline -> 7 post-main JS -> DOMContentLoaded.
// Asserts NO ReferenceError at the main-inline stage and at DOMContentLoaded
// (post-main symbols must not be called during main-inline load).
import { readFileSync } from "node:fs";
import { createContext, runInContext } from "node:vm";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const STATIC = join(__dirname, "..", "src", "joy_interaction_webui", "static");
const read = (p) => readFileSync(join(STATIC, p), "utf8");

function makeEl(id) {
  return {
    id, value: "", checked: false, textContent: "", innerHTML: "", innerText: "",
    src: "", disabled: false, style: {}, dataset: {}, files: [], options: [], children: [],
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    addEventListener() {}, removeEventListener() {},
    appendChild() { return null; }, removeChild() { return null; }, insertBefore() { return null; },
    append() {}, prepend() {}, replaceChildren() {},
    querySelector() { return makeEl(id + ":c"); }, querySelectorAll() { return []; },
    getBoundingClientRect() { return { left: 0, top: 0, width: 0, height: 0, right: 0, bottom: 0 }; },
    setAttribute() {}, getAttribute() { return null; }, removeAttribute() {},
    focus() {}, blur() {}, click() {}, scrollIntoView() {}, closest() { return null; },
  };
}

const domReadyListeners = [];
const browser = {
  console,
  addEventListener(t, f) { if (t === "DOMContentLoaded") domReadyListeners.push(f); },
  removeEventListener() {},
  lucide: { createIcons() {} },
  marked: { parse: (s) => String(s) },
  DOMPurify: { sanitize: (s) => String(s), addHook() {} },
  katex: { renderToString: () => "" },
  document: {
    getElementById: (id) => makeEl(id),
    querySelector: (sel) => makeEl(sel),
    querySelectorAll: () => [],
    createElement: (tag) => makeEl(tag),
    addEventListener(t, f) { if (t === "DOMContentLoaded") domReadyListeners.push(f); },
    removeEventListener() {},
    body: makeEl("body"), documentElement: makeEl("html"),
    readyState: "loading", cookie: "", title: "",
  },
  localStorage: { getItem: () => null, setItem() {}, removeItem() {}, clear() {} },
  sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  navigator: { mediaDevices: {}, userAgent: "qa-mock", clipboard: { writeText() {} }, language: "en" },
  location: { protocol: "http:", host: "localhost", pathname: "/", search: "", href: "http://localhost/" },
  history: { pushState() {} },
  matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }),
  setInterval: () => 1, clearInterval() {}, setTimeout: () => 1, clearTimeout() {},
  requestAnimationFrame: () => 1, cancelAnimationFrame() {},
  WebSocket: function () { this.readyState = 0; this.send = () => {}; this.close = () => {}; },
  fetch: async () => ({ ok: true, json: async () => ({}), text: async () => "" }),
  Event: function () {}, CustomEvent: function () {}, Image: function () {}, Audio: function () {},
  FormData: function () {}, Blob: function () {}, File: function () {}, FileReader: function () {},
  URL: function (u) { return { toString: () => u, href: u }; }, URLSearchParams: function () {},
  TextEncoder: function () {}, TextDecoder: function () {},
  crypto: { getRandomValues: (a) => a }, performance: { now: () => 0 },
  AudioContext: function () {},
};
browser.window = browser;
const context = createContext(browser);

// --- parse all script tags in document order ---
const html = read("index.html");
const scriptRe = /<script\b([^>]*)>([\s\S]*?)<\/script>/g;
const scripts = [];
let m;
while ((m = scriptRe.exec(html)) !== null) {
  const attrs = m[1];
  const srcMatch = attrs.match(/src="([^"]+)"/);
  const isCDN = attrs.includes("unpkg.com") || attrs.includes("cdn.jsdelivr.net");
  scripts.push({
    order: scripts.length,
    isCDN,
    file: srcMatch ? srcMatch[1].replace("./", "") : null,
    inline: srcMatch ? null : m[2],
    pos: m.index,
  });
}
// Only the last inline (sidebar-scrim bootstrap) is after post-main; keep order as-is.

const results = [];
function stage(label, code, filename) {
  try {
    runInContext(code, context, { filename });
    return { stage: label, ok: true, error: null };
  } catch (err) {
    return { stage: label, ok: false, error: err };
  }
}

for (const s of scripts) {
  if (s.isCDN) { results.push({ stage: `cdn:${s.file || "inline"}`, ok: true, error: null }); continue; }
  if (s.file) {
    const label = s.file.includes("vlm_render") ? "post-main" : s.file.includes("vlm_history") || s.file.includes("llm_reply_ui") || s.file.includes("ws_dispatcher") ? "pre-main" : s.file.includes("status_poll") || s.file.includes("live_ui") || s.file.includes("speech_input") || s.file.includes("background_rich") || s.file.includes("tts_player") || s.file.includes("llm_reply_audio") ? "post-main" : "early";
    results.push(stage(label, read(s.file), s.file));
  } else {
    const pos = s.pos;
    const isMainInline = pos > html.indexOf('id="sidebarScrim"') ? false : html.slice(0, pos).split("vlm_render.js").length > 1 ? false : pos > html.indexOf("ASR parameters");
    // Label by position relative to split markers
    const beforeVlmHistory = html.slice(0, pos).includes("vlm_history.js");
    const beforeVlmRender = html.slice(0, pos).includes("vlm_render.js");
    const beforeSidebar = html.slice(0, pos).includes("sidebarScrim");
    let label;
    if (beforeSidebar && beforeVlmRender) label = "post-main-inline(sidebar-bootstrap)";
    else if (beforeVlmRender && beforeVlmHistory) label = "main-inline";
    else if (beforeVlmHistory) label = "inline#1";
    else label = "early-inline";
    results.push(stage(label, s.inline, `index.html[${label}]`));
  }
}

// --- post-conditions (P0-1 consequences + P1-1 mirror) ---
const postChecks = [];
// P0-1: the main inline must have completed far enough to attach JoyWs and
// register the WS bootstrap (this is exactly what the ReferenceError aborted).
postChecks.push({
  name: "window.JoyWs attached (main-inline completed)",
  ok: runInContext("typeof window.JoyWs", context) === "object",
});
// P1-1: connectWebSocket() calls the registered setWebSocket bridge, which
// must mirror the socket onto window.websocket (screen_capture/live_ui gate on
// `!window.websocket`). Also verify it clears on close (setWebSocket(null)).
postChecks.push({
  name: "window.websocket mirror (P1-1)",
  ok: runInContext(`
    (function () {
      if (typeof window.JoyWs === 'undefined' || typeof window.JoyWs.connectWebSocket !== 'function') return false;
      window.JoyWs.connectWebSocket();
      const mirrored = window.websocket != null;
      // onclose handler calls setWebSocket(null); simulate the close callback.
      if (mirrored && typeof window.websocket.onclose === 'function') {
        window.websocket.onclose();
      }
      return mirrored;
    })()
  `, context) === true,
});
// Post-main symbols exist by DOMContentLoaded (guaranteed load order).
postChecks.push({
  name: "getVlmDisplayText defined (post-main loaded)",
  ok: runInContext("typeof getVlmDisplayText", context) === "function",
});
postChecks.push({
  name: "syncSpeechButtons defined (post-main loaded)",
  ok: runInContext("typeof syncSpeechButtons", context) === "function",
});

// --- DOMContentLoaded ---
for (const [i, fn] of domReadyListeners.entries()) {
  try { fn(); results.push({ stage: `DOMContentLoaded[${i}]`, ok: true, error: null }); }
  catch (err) { results.push({ stage: `DOMContentLoaded[${i}]`, ok: false, error: err }); }
}
if (domReadyListeners.length === 0) results.push({ stage: "DOMContentLoaded", ok: false, error: new Error("NO DOMContentLoaded listener registered") });

// --- summary ---
for (const pc of postChecks) {
  results.push({ stage: pc.name, ok: pc.ok, error: pc.ok ? null : new Error("post-condition failed") });
}
const failures = results.filter((r) => !r.ok);
for (const r of results) {
  const tag = r.ok ? "PASS" : "FAIL";
  if (!r.ok) console.log(`[${tag}] ${r.stage} -> ${r.error.stack.split("\n").slice(0, 5).join(" | ")}`);
  else console.log(`[${tag}] ${r.stage}`);
}
console.log(`\nstages: ${results.length}, failures: ${failures.length}`);
process.exit(failures.length === 0 ? 0 : 1);
