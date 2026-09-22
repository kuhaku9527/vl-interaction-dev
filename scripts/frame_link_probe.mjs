#!/usr/bin/env node
/**
 * 帧链路真机探针（工单 #163）—— 「摄像头/屏幕 → WS → VLM」这条链路的**可复现装置**。
 *
 * 为什么是这个形状（三条都是实测教训，不是设计偏好）
 * --------------------------------------------------
 * 1. ★ **必须用应用自身的 socket**（`window.websocket`）。已实测：自建
 *    `new WebSocket('/ws?session_id=…')` 发帧**收不到 `vlm_response`**
 *    （只回 `status` / `server_config`）—— 据此会得到完全错误的判定。
 *    故本探针**从不自己开连接**，只附着到已运行的应用页面。
 *
 * 2. ★ **观察应用自己的采集，而不是另行注入一帧。**
 *    实测：应用自身 1fps 采集在跑时，`vlm_service.process_frame` 的
 *    `_processing_lock` 正被占用，另注入的帧会命中
 *    `logger.debug("VLM busy, skipping frame")` 被**静默丢弃**，永远等不到
 *    与它同 `frame_seq` 的响应。⇒ 探针改为**观察应用自身采集发出的帧**
 *    （这正是用户路径本身），并用 `frame_seq` 把响应与帧配对。
 *
 * 3. ★ **判据取自数字，且必须能判红。**
 *    解析 `metrics.api_call_ms`（模型是否真被调用）、`metrics.total_inferences`
 *    （是否递增）、`metrics.user_prompt`（本轮是否携带 prompt）、`text`（内容）。
 *    2026-09-22 首次实测正是**机制通（api_call_ms=416.5）而内容空**
 *    （`text="Empty model response: stop"`，`user_prompt=""`）——
 *    「收到响应」≠「链路有意义」。
 *    负控（`--expect-no-response`）用于停 8070 / 静态服务器不可达的轮次：
 *    此时**不该**收到配对响应；若仍收到，说明探针在自欺。
 *
 * 用法
 * ----
 *     # 正常轮（应用已在做屏幕采集）：复现「无 prompt」形态
 *     node scripts/frame_link_probe.mjs --observe-ms 12000
 *
 *     # 建立「正常基线」：经应用自身 update_prompt 带上问题（期望有内容）
 *     node scripts/frame_link_probe.mjs --prompt "请描述当前画面内容，一句话。" \
 *         --require-content --observe-ms 12000
 *
 *     # 负控轮（先停掉 8070）：期望**收不到**配对响应
 *     node scripts/frame_link_probe.mjs --expect-no-response --observe-ms 12000
 *
 * 采集参数（AC4：分辨率 / `max_pixels` 影响）在**每一档**都会记录，无需额外旗标。
 *
 * 退出码：0 = PASS；1 = FAIL；2 = 装置或前置不可用（**不是通过**）。
 *
 * 人在回路（HITL，spec §4.3）：启动真实采集要弹浏览器窗口选择器，
 * 必须在应用页面上点「视频 → 屏幕采集 → Start」并授权。本探针**不替你点**，
 * 也不会挂起等人 —— 无采集时直接报「不可用」并退出 2。
 */

import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';

const DEFAULT_APP_URL = 'http://127.0.0.1:8099/';
const STATE_FILE = process.env.LOCALAPPDATA
  ? path.join(process.env.LOCALAPPDATA, 'ego-lite-linux', 'browser.json')
  : '';

function parseArgs(argv) {
  const out = {
    cdp: 0,
    appUrl: DEFAULT_APP_URL,
    prompt: null,
    requireContent: false,
    expectNoResponse: false,
    saveFrame: '',
    frameFile: '',
    observeMs: 12000,
    json: false,
    resetSession: false,
    resetSessionId: '',
  };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--cdp') out.cdp = Number(argv[++i]);
    else if (a === '--app-url') out.appUrl = argv[++i];
    else if (a === '--prompt') out.prompt = argv[++i];
    else if (a === '--require-content') out.requireContent = true;
    else if (a === '--expect-no-response') out.expectNoResponse = true;
    else if (a === '--save-frame') out.saveFrame = argv[++i];
    else if (a === '--frame-file') out.frameFile = argv[++i];
    else if (a === '--observe-ms') out.observeMs = Number(argv[++i]);
    else if (a === '--reset-session') out.resetSession = true;
    else if (a === '--reset-session-id') out.resetSessionId = argv[++i];
    else if (a === '--json') out.json = true;
    else throw new Error(`unknown flag: ${a}`);
  }
  return out;
}

/**
 * 清掉 webinfer 侧该会话的**视觉历史**，让本轮的第一帧是干净样本。
 *
 * 为什么需要它：帧会逐帧累进 webinfer 会话的 current_chunk（每帧约 +1.4k
 * prompt token）。不清会话时，几帧之后 prompt 就超过 llama `n_ctx=16384`，
 * 之后所有帧都回 502 —— 那时测到的是「上下文累积」而不是帧链路本身。
 * 这是**测量前置**，不是绕过：清的是被测会话的历史，不动被测代码。
 *
 * ★ 会话名不是 webui 的 WS session_id：webui 建 `VLMService` 时没传 session_id，
 *   故 webinfer 侧一律落在 **"default"**，`x-streaming-session` 也是它
 *   （`server.py::get_or_create_session` → `vlm_service.py::extra_headers`）。
 *   传 WS session_id 会得到 `removed:false`（静默什么都没清），
 *   于是「清了但还是 502」—— 故默认值取 "default"，并用 --reset-session-id 可覆盖。
 */
function resetWebinferSession(sessionId) {
  return new Promise((resolve) => {
    const body = JSON.stringify({ user: sessionId });
    const req = http.request(
      {
        host: '127.0.0.1',
        port: 8070,
        path: '/v1/streaming/reset',
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Content-Length': Buffer.byteLength(body),
          'x-streaming-session': sessionId,
        },
      },
      (r) => {
        let d = '';
        r.on('data', (c) => (d += c));
        r.on('end', () => resolve({ status: r.statusCode, body: d.slice(0, 200) }));
      },
    );
    req.on('error', (e) => resolve({ status: 0, body: String(e.message) }));
    req.write(body);
    req.end();
  });
}

function discoverCdpPort() {
  if (!STATE_FILE || !fs.existsSync(STATE_FILE)) return 0;
  try {
    return Number(JSON.parse(fs.readFileSync(STATE_FILE, 'utf8')).port) || 0;
  } catch {
    return 0;
  }
}

const getJson = (port, p) =>
  new Promise((res, rej) => {
    http
      .get({ host: '127.0.0.1', port, path: p }, (r) => {
        let d = '';
        r.on('data', (c) => (d += c));
        r.on('end', () => {
          try {
            res(JSON.parse(d));
          } catch (e) {
            rej(e);
          }
        });
      })
      .on('error', rej);
  });

let _id = 0;
const cdp = (ws, method, params = {}) =>
  new Promise((res, rej) => {
    const id = ++_id;
    const h = (e) => {
      const x = JSON.parse(e.data.toString());
      if (x.id === id) {
        ws.removeEventListener('message', h);
        x.error ? rej(new Error(JSON.stringify(x.error))) : res(x.result);
      }
    };
    ws.addEventListener('message', h);
    ws.send(JSON.stringify({ id, method, params }));
  });

async function connect(cdpPort, appUrl) {
  let targets;
  try {
    targets = await getJson(cdpPort, '/json/list');
  } catch (e) {
    // ★ CDP 连不上必须**作为「装置不可用」返回**（退出 2），而不是抛出去被
    //   顶层 catch 变成「探针自身失败」—— 后者看起来像探针坏了，而真相是
    //   前置没就位。两者对使用者是**不同的行动**（去开浏览器 vs 去修探针）。
    return { error: `CDP ${cdpPort} 不可达（${e && e.message ? e.message : e}）` };
  }
  const pages = targets.filter((t) => t.type === 'page');
  const want = new URL(appUrl);
  const page = pages.find((t) => {
    try {
      return new URL(t.url).host === want.host;
    } catch {
      return false;
    }
  });
  if (!page) {
    return {
      error:
        `CDP ${cdpPort} 上没有已打开 ${appUrl} 的标签页（现有 page target：` +
        `${pages.map((p) => p.url).join(', ') || '无'}）。` +
        '请先用浏览器打开应用页面 —— 本探针不自己开页面，否则装置与用户路径不同构。',
    };
  }
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  try {
    await new Promise((r, j) => {
      ws.addEventListener('open', r);
      ws.addEventListener('error', (e) => j(new Error(String((e && e.message) || 'WS error'))));
    });
  } catch (e) {
    return { error: `连接页面调试通道失败（${e && e.message ? e.message : e}）` };
  }
  await cdp(ws, 'Runtime.enable');
  return { ws, page };
}

async function evaluate(ws, expression) {
  const r = await cdp(ws, 'Runtime.evaluate', {
    expression,
    returnByValue: true,
    awaitPromise: true,
  });
  if (r.exceptionDetails) {
    throw new Error('页面内求值抛错：' + (r.exceptionDetails.text || JSON.stringify(r.exceptionDetails)));
  }
  return r.result.value;
}

/**
 * 一轮的判据 —— **纯函数**，不碰网络/文件，故可被离线测试直接断言
 * （见 `scripts/tests/test_frame_link_probe.py`）。
 *
 * 为什么把它抽出来：本探针的输出会变成台账证据，**一条假绿/假红都不可接受**。
 * 把判据做成纯函数，才能对「空内容」「错误串」「拿了别人的响应」这些
 * 真实缺陷形态逐条写离线回归，而不是只靠真机轮碰运气。
 *
 * 四条实测教训都固化在这里：
 *  1. `api_call_ms` 在 `metrics.latency_breakdown_ms.api_call_ms`，**不是** `metrics.api_call_ms`
 *     —— 读错层级会把一次成功推理（实测 420.87 ms）误判成「模型未被调用」（假红）。
 *  2. `analyze_image` 异常时返回 `Error: …` 且**不更新** `last_latency_breakdown_ms`
 *     ⇒ 只看那个数字，8070 挂掉会读到上一轮成功的值而判绿（假绿）。故对错误串显式判红。
 *  3. 应用自身 1fps 采集会持续回响应 ⇒ 不按 `frame_seq` 配对就会拿别人的帧给自己判绿。
 *  4. 负控轮（`--expect-no-response`）里**收到**配对响应才是失败 —— 两个方向都要能判。
 *
 * @returns {{problems: string[], notes: string[]}} problems 非空即判 FAIL。
 */
function judgeRound({ judged, windowFrames, unmatched, expectNoResponse, requireContent, prompt }) {
  const problems = [];
  const notes = [];

  if (windowFrames.length === 0) {
    problems.push('本轮探针未发出任何帧（装置未生效）—— 输出帧未被观察到，本项不可测');
  }
  if (unmatched.length) {
    notes.push(
      `${unmatched.length} 条 vlm_response 的 frame_seq 不等于本轮 seq（未参与判定）`,
    );
  }

  if (expectNoResponse) {
    if (!judged) notes.push('负控成立：观察窗口内未收到任何**配对**的 vlm_response');
    else problems.push(`负控失败：仍收到配对 vlm_response（frame_seq=${judged.frame_seq}）`);
    return { problems, notes };
  }

  if (!judged) {
    problems.push(
      `观察窗口内未收到任何**配对**的 vlm_response（发出 ${windowFrames.length} 帧）` +
        ' —— 帧链路不通，或后端 8070/8099 不可达',
    );
    return { problems, notes };
  }

  const m = judged.metrics;
  const lb = (m && m.latency_breakdown_ms) || {};
  const apiMs = typeof lb.api_call_ms === 'number' ? lb.api_call_ms : null;
  if (apiMs === null) {
    problems.push(
      '响应里没有 metrics.latency_breakdown_ms.api_call_ms —— 无法证明模型真被调用' +
        `（metrics 现有键：${Object.keys(m || {}).join(', ') || '无'}）`,
    );
  } else if (apiMs <= 0) {
    problems.push(`api_call_ms=${apiMs}（模型未被真实调用）`);
  }
  if (m && typeof m.total_inferences === 'number' && m.total_inferences <= 0) {
    problems.push(`total_inferences=${m.total_inferences}（推理计数未递增）`);
  }

  const text = (judged.text || '').trim();
  // ★ 两条**恒定**判红（与 --require-content 无关）—— 它们不是「内容不合格」，
  //   而是「用户可见面上出现了**非答案**」：
  //
  //   ① `Error: …`：`VLMService.analyze_image` 异常时返回 `f"Error: {e}"`
  //      且**不更新** `last_latency_breakdown_ms` ⇒ 只看那个数字，8070 挂掉
  //      会读到上一轮成功的 api_call_ms 而判绿（假绿）。
  //   ② `Empty model response…`：这是 `_extract_response_text` 的**诊断串**
  //      （`vlm_service.py:629`），不是模型回复。#163 已判定：帧路径不消费
  //      webinfer 的 `streamingharness.decision` 契约（四态沉默被剥成空串），
  //      于是这句内部诊断文案漏到用户可见面 —— **判缺陷**。
  //      ⇒ 把该判定**编码进判据**：出现即 FAIL。否则台账行会出现
  //        「**ALL PASS** … text="Empty model response: stop"」这种自相矛盾的
  //        读数，后人极易误读成「链路正常」。
  if (/^Error[:：]/.test(text)) {
    problems.push(`返回的是错误串（${text.slice(0, 160)}…）—— 后端未正常作答`);
  }
  if (/^Empty model response/i.test(text)) {
    problems.push(
      `返回的是内部诊断串 ${JSON.stringify(text)}（而非模型回复）` +
        ' —— #163 已判定为缺陷：frame 路径不消费 webinfer 的 decision 契约',
    );
  }
  if (requireContent && !text) {
    problems.push('--require-content：返回文本为空');
  }
  if (prompt !== null && m && m.user_prompt !== prompt) {
    notes.push(
      `user_prompt 回读=${JSON.stringify(m.user_prompt)} 与本轮设置=${JSON.stringify(prompt)} 不一致` +
        '（服务端在消费后清空 self.prompt，属预期；仅作记录）',
    );
  }
  return { problems, notes };
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const nowIso = () => new Date().toISOString().replace(/\.\d{3}Z$/, 'Z');

/**
 * 从收集到的响应里挑出**与本轮帧配对**的那一条（纯函数，可离线测）。
 *
 * ★ 为什么必须配对而不能「随便收到一条就算」：应用自身的 1fps 采集会持续推帧、
 * 持续回 `vlm_response`。若不按 `frame_seq` 配对，探针就会把**别的帧**的响应
 * 当成自己的结果 —— 那条响应可能来自另一路画面，据此判绿就是**假绿**。
 * 实测形态：一轮里 `frames_sent_in_window` 只有 1，但 `vlm_responses` 有 6 条。
 *
 * @param {Array} responses 收集到的 vlm_response（每项含 frame_seq / text）
 * @param {number} seq 本轮发出的那一帧的 frame_seq
 * @returns {{judged: object|null, unmatched: Array}} 配对的那条（无则 null）+ 未配对的
 */
function pickPairedResponse(responses, seq) {
  const all = Array.isArray(responses) ? responses : [];
  const mine = all.filter((v) => v.frame_seq === seq);
  return {
    judged: mine.length ? mine[mine.length - 1] : null,
    unmatched: all.filter((v) => v.frame_seq !== seq),
  };
}

/**
 * 从 JPEG 字节流里读出宽高（找 SOF0/SOF2 段），返回 `{w, h}`；失败给 0。
 *
 * 为什么需要：`--frame-file` 档只有字节没有 `width`/`height` 字段（那是
 * screen_capture.js 在 payload 里另带的）。不解析就会在台账行里印 `0x0`，
 * 而采集参数正是本票 AC4 要记的东西 —— 印 0 等于把一项证据变成噪声。
 */
function jpegSize(buf) {
  let i = 2; // 跳过 SOI
  while (i + 9 < buf.length) {
    if (buf[i] !== 0xff) {
      i += 1;
      continue;
    }
    const marker = buf[i + 1];
    // SOF0..SOF15（跳过 DHT 0xc4 / JPG 0xc8 / DAC 0xcc）
    if (marker >= 0xc0 && marker <= 0xcf && marker !== 0xc4 && marker !== 0xc8 && marker !== 0xcc) {
      return { h: buf.readUInt16BE(i + 5), w: buf.readUInt16BE(i + 7) };
    }
    if (marker === 0xd8 || (marker >= 0xd0 && marker <= 0xd9)) {
      i += 2;
      continue;
    }
    const len = buf.readUInt16BE(i + 2);
    if (len < 2) break;
    i += 2 + len;
  }
  return { w: 0, h: 0 };
}

async function main() {
  const args = parseArgs(process.argv.slice(2));

  // ★ 先验 `--frame-file`：文件不在就**立刻**报装置不可用，别先花时间连浏览器
  //   再报一个跟真正原因无关的错（实测：连不上 CDP 会把「帧文件不存在」
  //   这个真实原因整个盖掉，使用者被引去修错的东西）。
  if (args.frameFile && !fs.existsSync(args.frameFile)) {
    console.error(
      `[装置不可用] --frame-file 不存在：${args.frameFile}\n` +
        '  ▸ 先用 `--save-frame <path>` 在采集运行中落盘一张真实帧，再用它做离线轮。',
    );
    return 2;
  }

  const cdpPort = args.cdp || discoverCdpPort();
  if (!cdpPort) {
    console.error(
      '[装置不可用] 未发现 CDP 端口：请传 --cdp <port>，或确认 ego-lite 浏览器已启动' +
        (STATE_FILE ? `（已查 ${STATE_FILE}）` : ''),
    );
    return 2;
  }

  const conn = await connect(cdpPort, args.appUrl);
  if (conn.error) {
    console.error('[装置不可用] ' + conn.error);
    return 2;
  }
  const { ws, page } = conn;

  try {
    const pre = JSON.parse(
      await evaluate(
        ws,
        `JSON.stringify({
          url: location.href,
          wsState: window.websocket ? window.websocket.readyState : null,
          wsUrl: window.websocket ? window.websocket.url : null,
          capturing: !!(window.isScreenCapturing && window.isScreenCapturing()),
          video: (() => { const v = window.getScreenCaptureVideo && window.getScreenCaptureVideo();
            return v ? { w: v.videoWidth, h: v.videoHeight, readyState: v.readyState } : null; })(),
        })`,
      ),
    );
    if (!pre.wsUrl || pre.wsState !== 1) {
      console.error(
        `[装置不可用] 应用自身 socket 不可用（window.websocket=${pre.wsUrl}，readyState=${pre.wsState}）；` +
          '本探针拒绝对「自建 WebSocket」取证（已实测它收不到 vlm_response）。',
      );
      return 2;
    }

    // ---- 采集参数（AC4） ---------------------------------------------------
    // 无论走哪一档都采集并写进结果：AC4 要求记录「帧的采集参数与到达服务端的实际值」，
    // 那不是一条可选分支。
    const captureParams = JSON.parse(
      await evaluate(
        ws,
        `(() => {
          const s = window.getScreenCaptureStream && window.getScreenCaptureStream();
          const tr = s && s.getVideoTracks()[0];
          const v = window.getScreenCaptureVideo && window.getScreenCaptureVideo();
          return JSON.stringify({
            capturing: !!(window.isScreenCapturing && window.isScreenCapturing()),
            track_settings: tr ? tr.getSettings() : null,
            track_capabilities: tr && tr.getCapabilities ? tr.getCapabilities() : null,
            video_element: v ? { w: v.videoWidth, h: v.videoHeight, readyState: v.readyState } : null,
          });
        })()`,
      ),
    );

    // ---- 装置：附着到应用自身 socket（不开新连接） --------------------------
    await evaluate(
      ws,
      `(() => {
        window.__flProbe = { vlm: [], frames: [], t0: Date.now() };
        window.websocket.addEventListener('message', (ev2) => {
          let d; try { d = JSON.parse(ev2.data); } catch (e) { return; }
          if (d.type === 'vlm_response') {
            window.__flProbe.vlm.push({ at: Date.now(), text: d.text,
              frame_seq: d.frame_seq ?? null, metrics: d.metrics || null });
          }
        });
        if (!window.__flProbe.patched) {
          const orig = window.websocket.send.bind(window.websocket);
          window.websocket.send = function (p) {
            try { const d = JSON.parse(p);
              if (d.type === 'frame') window.__flProbe.frames.push({ at: Date.now(),
                w: d.width, h: d.height, seq: d.frame_seq ?? null,
                b64_chars: (d.data || '').length, source: d.source || null });
            } catch (e) {}
            return orig(p);
          };
          window.__flProbe.patched = true;
        }
        return JSON.stringify({ armed: true });
      })()`,
    );

    // ---- 可选：先清 webinfer 侧该会话的视觉历史（测量前置） ----------------
    let resetInfo = null;
    const sessionId = args.resetSessionId || 'default';
    if (args.resetSession) {
      resetInfo = await resetWebinferSession(sessionId);
      await sleep(1500);
    }

    // ---- 取一帧真实画面（取自应用自身采集管线） ----------------------------
    // 两种来源，都必须是**真实帧**：
    //   a) 应用当前正在采集 ⇒ 从 getScreenCaptureVideo() 抓当下这一帧；
    //   b) --frame-file：用本探针此前 --save-frame 落盘的**真实帧**（同一采集管线
    //      的产物）。用于负控/回归这类**不该依赖当时有没有人坐在屏幕前**的轮次。
    //   绝不合成帧 —— 合成帧会让「模型看到什么」这件事失去意义。
    let grabbed = null;
    if (args.frameFile) {
      // 存在性已在 main() 开头先验过（见那里的注释：先验才能报出真正的原因）。
      const buf = fs.readFileSync(args.frameFile);
      // 读出 JPEG 的真实宽高（SOF 段），免得台账行里印一个误导性的 0x0。
      grabbed = {
        b64: buf.toString('base64'),
        w: 0,
        h: 0,
        quality: null,
        fromFile: args.frameFile,
        bytes: buf.length,
        ...jpegSize(buf),
      };
    } else {
      grabbed = JSON.parse(
        await evaluate(
          ws,
          `(() => {
            const v = window.getScreenCaptureVideo && window.getScreenCaptureVideo();
            if (!v || !v.videoWidth) return JSON.stringify({ error: 'no_capture' });
            const c = document.createElement('canvas');
            c.width = v.videoWidth; c.height = v.videoHeight;
            c.getContext('2d').drawImage(v, 0, 0);
            const url = c.toDataURL('image/jpeg', 0.92);
            return JSON.stringify({ b64: url.split(',')[1], w: c.width, h: c.height, quality: 0.92 });
          })()`,
        ),
      );
      if (grabbed.error === 'no_capture') {
        console.error(
          '[装置不可用] 应用当前没有正在跑的真实采集（getScreenCaptureVideo() 无画面）。\n' +
            '  帧链路取证**必须用真实帧**，故本探针不会合成帧、也不会替你触发采集\n' +
            '  （那会弹浏览器窗口选择器，需人在回路点授权）。\n' +
            '  ▸ 请先在应用页面上点「视频 → 屏幕采集 → Start」并授权，再重跑本探针；\n' +
            '  ▸ 或传 `--frame-file <此前 --save-frame 落盘的真实帧>` 做离线轮。',
        );
        return 2;
      }
    }

    // ---- 停止应用自身的 1fps 循环，让本轮只留**一个**干净样本 --------------
    // ★ 为什么必须停：应用自身 1fps 采集会持续推帧，而每帧都带着图像累进
    //   webinfer 会话的视觉历史（实测每帧约 +1.4k prompt token）。连推十余帧后
    //   prompt 就超过 llama n_ctx=16384，之后**所有**帧都回 502
    //   （实测：`request (162806 tokens) exceeds the available context size`）。
    //   那时测到的是「上下文累积」而不是帧链路本身。
    //   同时，1fps 循环持有 vlm_service 的 `_processing_lock`，另注入的帧会被
    //   `logger.debug("VLM busy, skipping frame")` **静默丢弃**（实测：注入帧
    //   永远等不到响应）。⇒ 先停循环，再发单帧，样本才干净且可复现。
    const stoppedForRun = await evaluate(
      ws,
      `(() => { const was = !!(window.isScreenCapturing && window.isScreenCapturing());
        if (was) window.stopScreenCapture();
        return was; })()`,
    );
    if (stoppedForRun) {
      // 等在途的那一帧处理完、并把它写进历史的部分一并清掉。
      await sleep(3000);
      resetInfo = await resetWebinferSession(sessionId);
      await sleep(1000);
    }

    // ---- 可选：经**应用自身**的 update_prompt 带上问题（正常基线要靠它） ----
    if (args.prompt !== null) {
      await evaluate(
        ws,
        `window.websocket.send(JSON.stringify({ type: 'update_prompt', prompt: ${JSON.stringify(args.prompt)} })); 'sent'`,
      );
    }

    // ---- 发帧：**经应用自身 socket**，payload 形态与 screen_capture.js 完全一致
    const seq = Date.now();
    await evaluate(
      ws,
      `(() => {
        window.websocket.send(JSON.stringify({
          type: 'frame', format: 'jpeg',
          width: ${grabbed.w}, height: ${grabbed.h},
          data: ${JSON.stringify(grabbed.b64)},
          timestamp: Date.now(), source: 'screen', frame_seq: ${seq},
        }));
        return 'sent';
      })()`,
    );

    // ---- 等「与本轮这一帧同 seq」的 vlm_response ---------------------------
    const observeDeadline = Date.now() + args.observeMs;
    let collected = { vlm: [], frames: [] };
    let judged = null;
    while (Date.now() < observeDeadline) {
      await sleep(1500);
      collected = JSON.parse(
        await evaluate(ws, `JSON.stringify({ vlm: window.__flProbe.vlm, frames: window.__flProbe.frames })`),
      );
      judged = pickPairedResponse(collected.vlm, seq).judged;
      if (judged) break;
    }
    collected = JSON.parse(
      await evaluate(ws, `JSON.stringify({ vlm: window.__flProbe.vlm, frames: window.__flProbe.frames })`),
    );
    const paired = pickPairedResponse(collected.vlm, seq);
    judged = paired.judged;

    const windowFrames = collected.frames.filter((f) => f.seq === seq);
    const unmatched = paired.unmatched;

    // ---- 判据：纯函数（可被离线测试直接调用，见 scripts/tests/test_frame_link_probe.py）
    const { problems, notes } = judgeRound({
      judged,
      windowFrames,
      unmatched,
      expectNoResponse: args.expectNoResponse,
      requireContent: args.requireContent,
      prompt: args.prompt,
    });

    // 真实帧落盘：直接写**本轮实际发出去的那一帧**（含 --frame-file 来源），
    // 而不是重新去 videoElement 抓 —— 采集可能已被本轮停掉，重抓会得到空。
    let savedFrame = null;
    if (args.saveFrame && grabbed.b64) {
      fs.mkdirSync(path.dirname(args.saveFrame), { recursive: true });
      fs.writeFileSync(args.saveFrame, Buffer.from(grabbed.b64, 'base64'));
      savedFrame = { path: args.saveFrame, bytes: fs.statSync(args.saveFrame).size };
    }

    const verdict = problems.length ? 'FAIL' : 'PASS';
    const lastFrame = windowFrames.length ? windowFrames[windowFrames.length - 1] : null;
    const result = {
      verdict,
      observed_at: nowIso(),
      app_url: page.url,
      ws_url: pre.wsUrl,
      device:
        '应用自身 socket（window.websocket）；真实帧取自应用自身采集管线，' +
        '发帧前停掉应用 1fps 循环以得到单帧干净样本',
      observe_ms: args.observeMs,
      session_reset: resetInfo,
      capture_params: captureParams,
      frames_sent_in_window: windowFrames.length,
      last_frame: lastFrame,
      matched_responses: windowFrames.length && judged ? 1 : 0,
      judged,
      saved_frame: savedFrame,
      prompt_sent: args.prompt,
      expect_no_response: args.expectNoResponse,
      problems,
      notes,
    };

    if (args.json) {
      console.log(JSON.stringify(result, null, 2));
    } else {
      console.log('='.repeat(78));
      console.log(`帧链路真机探针（工单 #163）  判定：${verdict}`);
      console.log('='.repeat(78));
      console.log(`测量时间   : ${result.observed_at}`);
      console.log(`装置       : ${result.device}`);
      console.log(`应用页面   : ${page.url}`);
      console.log(`自身 socket: ${pre.wsUrl}`);
      console.log(`观察窗口   : ${args.observeMs}ms，出帧 ${windowFrames.length} 个，配对响应 ${(judged ? 1 : 0)} 条`);
      if (lastFrame) {
        console.log(
          `真实帧     : ${lastFrame.w}x${lastFrame.h} ` +
            (lastFrame.w && lastFrame.h ? '' : '（--frame-file 来源：尺寸见落盘帧本身）') +
            `  jpeg b64=${lastFrame.b64_chars} 字符  seq=${lastFrame.seq}`,
        );
      }
      if (grabbed.fromFile) {
        console.log(`帧来源     : --frame-file ${grabbed.fromFile}（${grabbed.bytes} 字节真实帧）`);
      }
      console.log(
        `采集参数   : settings=${JSON.stringify(captureParams.track_settings)}`,
      );
      console.log(`本轮 prompt: ${args.prompt === null ? '（未设置 —— 复现「无 prompt」形态）' : JSON.stringify(args.prompt)}`);
      if (savedFrame) console.log(`真实帧落盘 : ${savedFrame.path}（${savedFrame.bytes} 字节）`);
      console.log('');
      console.log('vlm_response（与本轮帧配对的那一条）：');
      if (judged) {
        const m = judged.metrics || {};
        const lb = m.latency_breakdown_ms || {};
        console.log(`  text             = ${JSON.stringify(judged.text)}`);
        console.log(`  frame_seq        = ${judged.frame_seq}`);
        console.log(`  api_call_ms      = ${lb.api_call_ms}`);
        console.log(`  total_inferences = ${m.total_inferences}`);
        console.log(`  user_prompt      = ${JSON.stringify(m.user_prompt)}`);
        console.log(`  latency_breakdown= ${JSON.stringify(lb)}`);
      } else {
        console.log('  未收到');
      }
      if (notes.length) console.log('\n说明：\n  - ' + notes.join('\n  - '));
      console.log('\n判据：\n  - ' + (problems.length ? problems.join('\n  - ') : '无问题项'));
      console.log('-'.repeat(78));
      console.log('台账行（可直接粘贴进 doc/standards/test-baseline.md）：');
      const m = judged && judged.metrics ? judged.metrics : null;
      const lb2 = (m && m.latency_breakdown_ms) || {};
      console.log(
        `| 帧链路端到端（真机） | \`node scripts/frame_link_probe.mjs${args.prompt !== null ? ' --prompt "…" --require-content' : ''}${args.expectNoResponse ? ' --expect-no-response' : ''} --observe-ms ${args.observeMs}\` | ${
          verdict === 'PASS' ? '**ALL PASS**' : '**FAIL**'
        } 配对响应=${(judged ? 1 : 0)}/${windowFrames.length} text=${JSON.stringify(judged ? judged.text : null)} api_call_ms=${
          lb2.api_call_ms !== undefined ? lb2.api_call_ms : 'n/a'
        } user_prompt=${JSON.stringify(m ? m.user_prompt : 'n/a')} | 真机 | ${result.observed_at} |`,
      );
    }

    ws.close();
    return verdict === 'PASS' ? 0 : 1;
  } finally {
    try {
      ws.close();
    } catch {}
  }
}

// 离线测试入口：`scripts/tests/test_frame_link_probe.py` 直接 import 本文件，
// 只调用 judgeRound（纯函数），**不会**触发 main()（见下方 guard）。
export { judgeRound, jpegSize, pickPairedResponse };

// ★ 仅在「被当作脚本直接执行」时跑 main()：被 import 时不得自行发起真机动作。
const invokedDirectly =
  process.argv[1] &&
  import.meta.url === new URL(`file://${process.argv[1].replace(/\\/g, '/')}`).href;

if (invokedDirectly) {
  main()
    .then((rc) => process.exit(rc))
    .catch((e) => {
      console.error('探针自身失败：' + (e && e.message ? e.message : e));
      process.exit(2);
    });
}
