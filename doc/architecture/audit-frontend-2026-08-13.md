# 前端 + 整体架构一致性审计报告（2026-08-13）

- 审计人：software-engineer-3（只读全代码审查）
- 范围：`services/webui/src/joy_interaction_webui/static/`（index.html 2477 行 + 全部 22 个 JS + styles.css）+ 跨服务边界（`ws_notify.py` / `ws_handler.py` / `server.py` / `service_probe.py` / `live_routes.py` / `jarvis_routes.py` / `live_llm.py` / `jarvis_mode.py` / `video_processor.py` / `vlm_service.py`）
- 方法：逐文件读码 + grep 跨文件符号表 + 前后端 WS 消息类型逐一对表 + node 复现关键时序
- 结论：**发现 1 × P0，5 × P1，7 × P2（合计 13 项）**。其中 1 个 P0 + 2 个 P1 为本次拆分直接引入或拆分未修复的功能性回归；其余为前项目组遗留隐患 / 前后端协议断层 / 死代码 / 过度设计。

---

## 一、P0 — 页面主内联脚本加载期崩溃（拆分引入，页面核心功能瘫痪）

### P0-1 `getVlmDisplayText` 跨脚本加载顺序错误 → 主内联脚本在 index.html:1585 抛 ReferenceError

- **位置**：`static/index.html:1571`（`applyOverlayPosition` 内调用）→ 触发点 `index.html:1585` `applyOverlayPosition(settings.overlayPosition);`
- **问题**：`getVlmDisplayText` 只定义在 `static/vlm_render.js:13`，而 vlm_render.js 在 `index.html:2441` **主内联脚本之后**加载。主内联脚本执行到第 1585 行时该函数尚不存在（经典脚本中函数声明只在本 script 内提升），`vlmHistory[...]?.response || getVlmDisplayText(lastText)` 求值 `lastText=''` → 必然调用 → **ReferenceError: getVlmDisplayText is not defined**，未捕获 → 主内联脚本自 1585 行起整体中止。
- **影响（脚本中止后的连锁失效）**：
  - `window.JoyWs.register(...)`（2140 行）不执行 → `connectWebSocket` 因 `_ctx=null` 直接返回 → **WebSocket 永远连不上**，页面整体与服务端失联（无 vlm_response / llm_reply / 状态轮询失效）；
  - 设置弹窗按钮（settingsBtn 监听在 1624 行）不绑定 → **设置面板打不开**；
  - `window load` 监听（2400 行）不注册 → 页面无任何 WS 自举；
  - WrappedWS IIFE（2420 行）不执行 → llm_reply 钩子缺失；
  - modelSelect / processEvery / maxLatency / ASR promotion / background config 等所有 1585 行之后的控件监听全部失效。
- **证据**：`archive/agent-scratch-20260808/inline_2.js:685` 中 `getVlmDisplayText` 原定义在 2854 行 `applyOverlayPosition` 之前（同脚本内，未拆前可运行）；拆分后其定义被搬进后置 vlm_render.js，调用点却留在主内联脚本，形成唯一的加载期跨后置脚本依赖。已用 node 复现：无 `getVlmDisplayText` 时该行抛 `ReferenceError: getVlmDisplayText is not defined`。
- **建议修复（最小）**：将 index.html:1585 的立即调用延迟到 vlm_render.js 之后（如改为在 vlm_render.js 末尾执行 `applyOverlayPosition(settings.overlayPosition)`，或包一层 `typeof getVlmDisplayText === 'function' ? applyOverlayPosition(...) : null` + DOMContentLoaded 兜底）。修复后需回归：设置弹窗、WS 连接、模式切换。

---

## 二、P1 — 功能性 bug / 协议断层

### P1-1 `window.websocket` 从未被赋值 → 屏幕采集帧管线与 Live 摄像头帧推送静默失效（遗留 + 拆分未修复）

- **位置**：`index.html:1078/1213/1258/1293`、`live_ui.js:147/161/181/198`、`screen_capture.js:34`；赋值桥 `index.html:2148` `setWebSocket: (ws) => { websocket = ws; }`（只改词法变量）
- **问题**：全工程 grep `window.websocket\s*=` **零命中**。顶层 `let websocket`（index.html:931）不会挂到 window；`screen_capture.js` 的 `resolveWebSocket` 回退到 `window.websocket` 恒为 `undefined` → `startScreenCapture(window.websocket, ...)` 拿到的 ws 为 null → 采集间隔回调 `if (!liveWs || readyState !== OPEN) return;` → **帧永远发不出去**。`live_ui.js:147` 的 Live 摄像头 `frame` 推送同样被 `if (!window.websocket ...) return` 短路。
- **影响**：侧边栏「屏幕采集」按钮显示 "Capturing (1 fps)" 但后端收不到任何 `frame` → 屏幕 → VLM 分析整条链路死；Live 面板「开画面 → 摄像头」帧推送到 live session ring buffer 的链路死（主动搭话视觉轮询也拿不到画面）；`live_ui.js:181` 的 `!window.websocket` 判断恒真，重复调用 connectWebSocket（幂等，仅噪音）。
- **标注**：真 bug / 前项目组遗留（archive 中同样的 `window.websocket` 用法已存在，但 `websocket` 当时已是 `let`，说明该问题在拆分前就存在；本次拆分未顺带修复）。
- **建议修复（一行）**：`setWebSocket` 桥同时写 `window.websocket = ws;`（并在 onclose/resetSession 置 null 处同步），或把所有 `window.websocket` 读取改为 `getWebSocket()`。

### P1-2 `live_ui.js:569` 对 `const audioTrack` 赋值 → 麦克风增益（GAIN 滑块）静默失效

- **位置**：`live_ui.js:542` `const audioTrack = btListenStream.getAudioTracks()[0];` + `live_ui.js:569` `audioTrack = boostedTrack;`
- **问题**：`audioTrack` 是 `const`，对其赋值抛 `TypeError: Assignment to constant variable`，被外层 `try/catch (gainErr)` 吞掉（日志 "Mic gain init failed, using raw track:"）。因此 KWS 永远拿到**原始（未增益）音轨**，GAIN 滑块（默认 1.5x）实际无效。代码注释声称"previous condition was inverted (if (!audioTrack)) 已修复"，但修复本身引入了 const 赋值 bug。
- **影响**：监听链路 KWS 输入无增益放大；弱麦克风/远场唤醒可靠性下降；用户调 GAIN 无任何效果且无报错。
- **标注**：真 bug / 前项目组遗留（拆分文件为"纯搬移"，bug 保留）。
- **建议修复**：将 542 行改为 `let audioTrack = ...`（最小改动），或在 try 内用新变量并在 replaceTrack 时使用最终值。

### P1-3 前后端 WS 消息类型断层：`update_processing` 后端不认

- **位置**：前端 `index.html:2248/2266` 发送 `{type:'update_processing', process_interval}`；后端 `ws_handler.py:98` 只处理 `update_process_interval`（全局 grep 无 `update_processing` 处理器）
- **问题**：RTSP 面板「Processing Interval」修改永不生效，后端也不会回 `processing_updated`（该回包只由 `update_process_interval` 触发）。
- **影响**：RTSP 推理间隔设置静默失效（默认 1s 无法改）。
- **标注**：真 bug / 前后端协议不一致（前项目组遗留）。
- **建议修复**：统一消息名（改前端为 `update_process_interval` 或后端加 `update_processing` 分支），并补测试。

### P1-4 后端 `update_frames_per_batch` 处理器不回写新值（echo 旧值）

- **位置**：`ws_handler.py:110-118`
- **问题**：分支只 `await ws.send_json({"type":"frames_per_batch_updated","frames_per_batch": VideoProcessorTrack.frames_per_batch})`，**从未执行 `VideoProcessorTrack.frames_per_batch = data.get("frames_per_batch")`**。前端收到的是旧值并回写输入框 → 用户改完被"弹回"。
- **影响**：Frames per Batch 设置失效。
- **标注**：真 bug / 前项目组遗留。
- **建议修复**：先 `VideoProcessorTrack.frames_per_batch = max(1, int(data.get("frames_per_batch") or 1))` 再 echo。

### P1-5 `update_background_config` 无后端处理器 → 后台模型设置整组失效

- **位置**：前端 `vlm_history.js:775-783`（`sendBackgroundConfig` 发送 `update_background_config`）；后端全工程无此消息处理分支
- **问题**：设置面板「Enable delegation solver / Frame multiplier / Max background frames」的改动只发 WS，后端无人处理。`server_config` 里 `background_model` 只读下发一次。
- **影响**：用户关掉后台模型开关无效（后台模型仍按默认运行，浪费资源）；倍率/帧上限改动无效。
- **标注**：真 bug / 前后端协议断层（前项目组遗留）。
- **建议修复**：ws_handler.py 增加 `update_background_config` 分支，写 session 的 `background_service`（参考 `update_asr_promotion` 的写法），并回 `background_config_updated`。

### P1-6 `applyOverlayPosition` 中 `hasJarvisDialogHistory` 跨文件但主内联可解析（提示级）→ 与 P0-1 合并观察

- 注：`hasJarvisDialogHistory` 定义于 pre-main `vlm_history.js:70`，可解析；仅 `getVlmDisplayText` 是后置依赖。此条并入 P0-1，不再单列。

---

## 三、P2 — 死功能 / 死代码 / 轮询与定时器残留 / 硬编码 / 过度设计

### P2-1 `update_max_latency` 无后端处理器
- `index.html:2312-2321` 发送 `update_max_latency`；后端 `video_processor.py:73` 存在 `max_frame_latency` 类属性但无任何 WS 分支设置它 → WebRTC Max Video Latency 设置纯 UI 摆设。真 bug（前项目组遗留）。

### P2-2 `set_debug` 无后端处理器；debug payload 前端期待与后端产出断层
- 前端 `index.html:2324-2335` / `ws_dispatcher.js:219-229` 发 `set_debug`；后端无任何处理（grep 零命中）。`server.py:305` 给 session 写了 `"show_request_payload": False` 但从未被读取；`vlm_service.py` 内部存 `_last_request_payload/_last_response_payload` 但 `ws_notify.get_session_callback` 从不把它们挂进 `vlm_response`；`memory_state` 同理后端从不推送。
- 影响：设置面板「Show request/response payload / Show memory state」三个开关无实际效果（仅本地 UI 状态）。死功能（前项目组遗留）。

### P2-3 后端 `background_request_accepted` 前端不消费；前端 `background_config_updated` 后端不发送
- `ws_handler.py:184` 发 `background_request_accepted`，`ws_dispatcher.js` 只处理 `background_task_started/ready/error`，该消息静默穿透；`ws_dispatcher.js:246` 处理 `background_config_updated` 但后端从不发（grep 零命中）→ 两段死分支。前端 UI 也从不发 `background_request`（该路径仅外部客户端可用）。死代码（前后端各半）。

### P2-4 轮询/定时器无生命周期清理
- `status_poll.js:286-306`：jarvis/live/service/extended 四组轮询在 DOMContentLoaded 无条件自启，`stop*` 仅导出**从未被任何代码调用**（grep 确认）。`pollLiveStatus` 每 1s 轮询即使 live 未激活也执行（提前 return 渲染 null）。页面单页生命周期内不算泄漏，但无任何停止/清理入口，若未来做 SPA 路由/组件卸载会残留。
- `index.html:1234/1273/1310`：webcam/rtsp/screen 三个 `setInterval(refresh, 1000)` 永不清理（页面级，轻量）。
- `joy_ws.js:173`：WS 断开 2s 重连无上限/退避，服务端关闭时会无限打重连（可考虑指数退避 + 上限）。
- `vlm_history.js` 的 `vlmHistory` / `backgroundTaskEntries` / `backgroundSummaryEntries` 无上限，长会话内存增长（低危）。

### P2-5 死代码清单（grep 确认无调用点）
- `index.html:1827 toggleApiKeyField`（Block 3 后 apiKeyField 元素已不存在，函数无调用点）；
- `index.html:1947 detectServices`（`/detect-services` 路由存在但前端从不调用）；
- `index.html:1912 showProcessedVideoStream`、`revealVideoAfterMetricsToken` 永不被赋 token（"等待首批指标"UX 路径实际死）；`resetVideoButtons`（1891）为空函数；`index.html:1867 const canSendPrompt = true;` 未使用；
- `index.html:832-833 startBtn/stopBtn` 引用已删除的元素 id（null 常量，仅剩注释）；
- `capture_webcam.js` `webcamVideo/getWebcamVideo` 从不赋值/使用（半成品 API）；
- `ws_dispatcher.js:326` 命名空间导出 `sendDebugFlags`（定义在主内联，导出冗余）。

### P2-6 硬编码残留
- `stun.l.google.com:19302` 硬编码于 `capture_webcam.js:60` / `capture_rtsp.js:41` / `live_ui.js:428,576`（3 处 ×2 实例）；国内网络/隔离网段常不可达，建议可配置或走 relay/TURN。
- `service_probe.py:106` 默认 `JARVIS_KWS_MODEL_DIR="D:/AI/models/sherpa-onnx/models/kws/bt-en"`（机器相关绝对路径硬编码，后端遗留，非前端）。
- CDN（unpkg/jsdelivr）带 SRI，可接受；服务地址占位符（127.0.0.1:8070 等）为表单默认值、非请求目标，可接受。

### P2-7 10 个 JS 拆分边界评价（过度设计/可合并）
- **总体合理**：pre-main 3（vlm_history/llm_reply_ui/ws_dispatcher）→ 主内联 → post-main 7（vlm_render/background_rich/tts_player/speech_input/live_ui/llm_reply_audio/status_poll）的依赖方向正确，命名空间（window.JoyXxx）附加式挂载，无重复定义（`llmReplyEpoch/llmReplyGeneration/_livePollTimer` 等 `let` 均单点声明，无 SyntaxError 冲突）。
- **脆弱点**：`llm_reply_ui.js`（pre-main）运行时依赖 post-main 的 `llmReplyGeneration`/`stopLlmReplyAudio`/`playLlmReplyAudio`/`enqueueLlmReplySentence`/`clearAsrDraft`。当前成立仅因 WS 消息必然晚于全部脚本加载；若加载中途有 WS 消息，handle 内 try/catch 会**静默吞掉整条消息**（含 JSON 解析错误，掩盖一切 handler bug）。建议：要么把 llm_reply_ui 挪到 post-main，要么把 `llm_reply_audio.js` 提前，并在 catch 里区分"解析失败"与"业务异常"。
- **重复函数**：`renderTextIntoElement`（vlm_render.js:165）与 `createJarvisDialogNode` 内重复内联渲染逻辑；`extractFencedBlocks/extractInlineJsonObjectCandidates` 在 vlm_render/background_rich 两处各自封装，可合并进 render_markdown.js 或保持现状（轻微重复，不算问题）。
- **双 TTS 链可重叠**：`tts_player.js speakVlmText`（vlm_response 路径，走 /api/tts WS）与 `llm_reply_audio.js playLlmReplyAudio`（llm_reply 路径，走 /api/tts/synthesize）相互独立、无互斥；Webcam/屏幕流式分析（vlm_response 持续产生）+ 手动发 BT 消息（llm_reply）同时进行时可能叠加播放。旧版即如此，建议至少加"同一时刻只允许一条 TTS 链"的互斥或明确文档化。

---

## 四、跨服务一致性逐一对表

### 4.1 客户端 → 服务端（/ws）消息
| 前端发送 | 后端处理器 | 结论 |
|---|---|---|
| `update_model` | ws_handler.py:93 | ✅ 一致 |
| `update_prompt` | ws_handler.py:88 | ✅ 一致 |
| `update_processing` | 无（后端认 `update_process_interval`） | ❌ P1-3 断层 |
| `update_frames_per_batch` | ws_handler.py:110（不回写） | ❌ P1-4 半实现 |
| `update_max_latency` | 无 | ❌ P2-1 死功能 |
| `set_debug` | 无 | ❌ P2-2 死功能 |
| `update_background_config` | 无 | ❌ P1-5 断层 |
| `update_asr_promotion` | ws_handler.py:199 | ✅ 一致 |
| `frame` | ws_handler.py:119 | ✅ 一致（但前端帧来源受 P1-1 阻断） |
| `reset_session` | 无（只有 `/api/session/cleanup`） | ⚠️ 死消息（P2 级） |
| `background_request` | ws_handler.py:178 | ⚠️ UI 从不发送（外部客户端专用） |

### 4.2 服务端 → 客户端（notify_* / ws 直发）消息
| 后端发送 | 前端 dispatch | 结论 |
|---|---|---|
| `vlm_response` | ws_dispatcher.js:23 | ✅（frame_seq/summarizer_timing/metrics 字段对齐） |
| `status` | ws_dispatcher.js:172 | ✅ |
| `server_config` | ws_dispatcher.js:181 | ✅（model/api_base/background_model/asr_promotion 字段对齐） |
| `prompt_updated` / `model_updated` | ws_dispatcher.js:236/230 | ✅ |
| `processing_updated` | ws_dispatcher.js:239 | ⚠️ 只有后端收到 `update_process_interval` 才会发，前端从不发 → 链路死（P1-3） |
| `frames_per_batch_updated` | ws_dispatcher.js:242 | ⚠️ 回包会弹回旧值（P1-4） |
| `background_task_started` / `background_result_ready` / `background_result_error` | ws_dispatcher.js:175-180 + vlm_history.js | ✅ |
| `background_request_accepted` | 无 | ❌ P2-3 死消息 |
| `asr_promotion_updated` | ws_dispatcher.js:249 | ✅ |
| `llm_reply` | llm_reply_ui.js:24 | ✅（reply_epoch/source 字段对齐，见 4.4） |
| `tts_sentence` | llm_reply_ui.js:44 → llm_reply_audio.js | ✅（seq/session/audio_b64 字段对齐） |
| `pilot_utterance` / `asr_partial` | llm_reply_ui.js:48/63 | ✅（reply_epoch 字段对齐） |
| `background_config_updated` | ws_dispatcher.js:246 | ❌ 后端从不发送（P2-3） |

### 4.3 live / jarvis 双模式前端状态 ↔ 后端会话映射
- 模式互斥由前端 `selectBtListenMode/selectLiveMode`（live_ui.js:666-692）在 await 停止后二次复查标志位保证，符合 PRD 单选互斥；后端按 `session_id` 绑定 session，`/api/live/start`、`/api/live/stop`、`/api/jarvis/stop`、`/offer(live_audio/jarvis_audio)` 均带 `session_id`，映射一致 ✅。
- 状态轮询字段对齐：`/api/jarvis/status`（exists/state/is_awake）↔ `JARVIS_STATE_MAP`；`/api/live/status`（exists/turn_state/proactive_supported/proactive_enabled/enroll_segment_count）↔ `LIVE_STATE_MAP`，枚举名称逐一对应 ✅。
- 注意点：`startBtListening` 先 `POST /api/jarvis/stop` 重置再建会话（live_ui.js:527）——属设计内行为，但每次"监听"都会先清一次后端状态；若后端 stop 慢，可能短暂竞态（低危）。

### 4.4 decision token 一致性
- 前端 `getVlmDisplayText`（vlm_render.js:13）剥 `</?silence>/</?response>/</?delegation>`，`<silence>`/`</silence>` 整条判空；`sanitizeDebugPayload` 递归剥 token 后展示原始 payload —— 与后端 webinfer harness `decision`（silence/response/delegation/not-for-me，live_llm.py:126-156、live_proactive.py:154-159）+ 文本流残留 token 的四态语义一致 ✅。
- 轻微不一致点：后端 `background_model.py:83` 拼接的占位文案 `"</response> 这个问题需要调用后台模型..."` 依赖前端剥离 `</response>` 后展示；若后端未来不输出该 token，前端会原样显示，属弱耦合（P2 提示）。

### 4.5 端口 / URL / 跨域
- 前端所有请求均为同源相对路径（`/ws`、`/offer`、`/api/*`、`/v1/*`），无跨域请求 ✅；ASR/TTS WS 用 `location.protocol` 推导 wss/ws ✅；唯一外部依赖为 CDN（带 SRI）与 STUN（见 P2-6）。

---

## 五、审计结论

- **必改（先修 P0-1）**：index.html:1585 加载期 ReferenceError 会让整页主链路瘫痪，请优先修复并做回归（WS 连接 + 设置弹窗 + 模式切换）。
- **次优先（P1）**：`window.websocket` 赋值桥、`const audioTrack` 赋值、三处 WS 消息断层/回写缺失（update_processing / update_frames_per_batch / update_background_config）。
- **清理项（P2）**：死代码、死功能（max_latency/set_debug/background_request_accepted/background_config_updated）、轮询生命周期、STUN 硬编码。
- **拆分本身**：10 文件边界总体健康，唯一硬伤是 P0-1 的跨后置脚本加载期调用；建议把"主内联加载期不得调用 post-main 符号"作为拆分守则写进注释。

（本报告只读审计，未修改任何代码。）
