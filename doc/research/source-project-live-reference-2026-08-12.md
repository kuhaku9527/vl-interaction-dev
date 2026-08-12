# 源项目 live 模式参考报告（2026-08-12）

> 只读分析产出。源项目 = `D:\AI\workspace\7-22\JoyAI-VL-Interaction-main`（7-22 目录，即上游开源版本，无 git 历史）。当前项目 = `D:\AI\workspace\JoyAI-VL-Interaction-main`（已含 jarvis 唤醒 + live 常驻监听 C.A）。
> 本报告只呈现事实 + 可借鉴点，不做好坏评价。

---

## 0. 核心结论（TL;DR）

1. **源项目没有"免唤醒常驻监听 live 模式"，也没有 jarvis 模式**——全仓库搜不到 `jarvis`；无唤醒词 / KWS / 常驻监听相关代码。源项目的 "live" 是**实时视频流助手**（NVIDIA live-vlm-webui 衍生的 StreamingHarness 推理适配层 + 周期性视频分析）。
2. **源项目有完整的"三判断/决策"机制**，实现于 `services/webinfer/live_adapter.py`：每轮推理模型必须输出三选一决策 token——`</silence>`（沉默）/ `</response>`（说话，含主动搭话）/ `<delegation>`（委派后台模型）。当前项目的 `infer_loop.py` 决策框架正是从它演化而来。
3. **源项目前端没有任何 jarvis/live 模式单选 UI**。语音输入只有"按住说话 / 点击切换 / 松手结束"三类按钮（`asrStopMode`），无互斥模式选择。UI 层唯一与"模式"相关的控件是 **系统提示词下拉**（Default delegation / No delegation）和一批 `toggle-switch` 复选框。
4. **源项目没有显式"说话对象判定"逻辑**。谁对谁说话不做声学/语义预判，而是把"是否值得回应"交给模型：有 query（`current_query_text`）且场景有变化 → `</response>`；无 query → 强制 `</silence>`（`FORCE_SILENCE_BEFORE_QUERY=true`）。
5. **说话/主动搭话的触发点**：前端 ASR 文本 → prompt → 后端周期性 VLM 帧分析 → 模型决策 token → 前端 `getVlmDisplayText()` 过滤 `</silence>` → `speakVlmText()` 走 TTS。决策发生在**模型推理层**，不在前端。

---

## 1. 源项目 live/模式实现结构图（文件 → 职责）

```
services/webinfer/live_adapter.py   (2775 行)  ← 核心：StreamingHarness live OpenAI 适配器
├─ _get_i18n / DEFAULT_SYSTEM_PROMPT_EN / DEFAULT_SYSTEM_PROMPT   (L76-147)
│    三决策系统提示词：Stay silent / Speak / Delegate（L100-111）
│    NO_DELEGATION 变体：禁用委派的二选一版（L113-124）
├─ normalize_model_output(text)     (L299-321)  模型裸输出 → 规范 </silence> / </response> 首行
├─ extract_response_payload(text)   (L324-329)  </response> 后的实际发言文本
├─ class AdapterConfig              (L509)      配置（含 chunk / 记忆 / 静默开关）
├─ class SessionState               (L574)      会话态（frame_count/turn_count/current_query_text…）
├─ class StreamingInferAdapter      (L610)      主服务
│   ├─ handle_chat_completions / handle_health / handle_reset   (L747-800)  HTTP 面
│   ├─ _handle_chat_payload         (L1071-1384) ★ 每轮决策主流程
│   │    ├─ 无图 → _forward_text_only（L1093）
│   │    ├─ _update_query_state     (L1454-1480)  ★ query 记账（USE_PROMPT_AS_QUERY）
│   │    ├─ 强制静默判断            (L1221-1223)  ★ force_silence_before_query && 无 query
│   │    ├─ 有 query → _call_main_model → normalize_model_output   (L1245-1286)
│   │    └─ response_records / chunk 翻转 / 异步摘要            (L1288-1384)
│   ├─ _flush_chunk / _build_mid_term_summary_entry / _compress_mid_terms  (L1661-1938) 分块记忆
│   └─ _submit_async_summary_if_needed  (L1847) 异步摘要
└─ parse_args                        (L2326)     ★ FORCE_SILENCE_BEFORE_QUERY / USE_PROMPT_AS_QUERY
                                                 CHUNK=200 / KEEP_QA_HISTORY / NORMALIZE_OUTPUT

services/webinfer/memory_summarizer.py  (806 行)  分块中期摘要 + 长期记忆压缩（vLLM 摘要模型）
services/webui/src/joy_interaction_webui/
├─ video_processor.py   VideoProcessorTrack.recv() (L89)  周期抽帧 → vlm_service.process_frame
│                        (L214/L276)  + 新响应回调 text_callback（L295-302）
├─ vlm_service.py       VLMService.analyze_image/analyze_images (L185/L481)  转发 webinfer
│                        get_current_response() (L622)  当前响应 + is_processing
├─ background_model.py  ★ 委派侧：parse_delegation(text) (L199) 解析 </delegation> <question>
│                        handle_foreground_response (L613) → _run_delegation_task → solve_delegation
├─ asr.py               ★ 前端 ASR WebSocket：continuous=1 → stop_on_final=False (L300-321)
└─ server.py            websocket_handler (L454)  WS 控制面（update_prompt/update_model/…）
                        offer() (L768)  WebRTC（aiortc PeerConnection + 音视频 track）

services/webui/.../static/index.html  (8690 行)  前端：单文件
├─ getVlmDisplayText(text)  (L4307-4334)  ★ 前端唯一决策 token 清洗源
│    含 </silence> → 返回 ''（不显示不发音）；<response>…</response> 截取正文
├─ updateResultText()      (L6439)  → getVlmDisplayText → speakVlmText(displayText) (L6457)
├─ speakVlmText()          (L5675)   → TTS WebSocket speak（主动搭话落点）
├─ 语音按钮               (L7261-7330 syncSpeechButtons / L7498 startSpeech / L7545 stopSpeech)
│    asrStopMode = 'toggle' | 'press' | 'auto'（L3994），toggle 时 continuous=1（L7456）
└─ 设置面板               (L3417-3553)  popInToggle/glowToggle/fadeToggle/ttsEnabledToggle/
                                          backgroundEnabledToggle/debugShow*（全部 toggle-switch）
                        (L3645-3646)  ★ system_prompt 下拉：Default (delegation) / No delegation
```

### 1.1 三决策系统提示词（源项目原文）

`live_adapter.py:100-111`：

```
You are a real-time video streaming assistant observing a continuous camera feed frame by frame...
## Action Format
At every inference step you MUST choose exactly one of the following three actions:
**Stay silent** — output ONLY:
</silence>
Choose this when nothing noteworthy has changed in the scene, no user query is pending, or there is nothing useful to say.
**Speak** — output the token followed by a concise reply:
</response> Your reply here.
Choose this when you observe something worth reporting or a significant state change, or when you can answer a user question based on available evidence.

**Delegate** — when a question is too hard or error-prone to answer reliably yourself, speak a brief note that you're delegating, then hand the question to the background solver:
</response> Brief note that you're delegating. </delegation> <the question>
```

关键点：
- **`</silence>` 即"不发言"**，模型直接输出该 token，前端过滤为空白、不触发 TTS。
- **`</response>` 即"说话"**，涵盖**主动搭话**（场景有变化 worth reporting）与**响应式发言**（回答用户 query）。
- **委派**由 `</delegation> <question>` 携带问题，webui 的 `background_model.py:199 parse_delegation()` 提取并转后台求解。

### 1.2 强制静默（无 query 不开模型）

`live_adapter.py:1221-1231`：

```python
is_forced_silence = (
    self.config.force_silence_before_query and not state.current_query_text
)
if is_forced_silence:
    generated_text = "</silence>"
    raw_text = ""
    ...
    inference_skipped=True, skip_reason="force_silence_before_query"
```

即：**没有用户 query 时直接回 `</silence>`，完全不调主模型**。配置项在 `live_adapter.py:2392-2394`（`FORCE_SILENCE_BEFORE_QUERY`，默认 `True`）。这是源项目"沉默判断"的成本控制关键——也是"说话对象判定"的一种**粗糙替代**：ASR 没给出 query 就一律闭嘴。

### 1.3 query 记账（USE_PROMPT_AS_QUERY）

`live_adapter.py:1454-1480` `_update_query_state`：

```python
if not self.config.use_prompt_as_query:
    return None
normalized_prompt = (prompt_text or "").strip()
if not normalized_prompt:
    return None
if state.current_query_text is None:
    state.current_query_text = normalized_prompt
    state.query_start_time = time_range
    ...
if normalized_prompt != state.current_query_text:
    state._pending_qa_archive = (state.current_query_text, state.query_start_time)
    state.current_query_text = normalized_prompt
```

query（用户问题）来自每帧请求携带的 prompt（前端 ASR 文本）。query 存在 → 才可能走 `</response>`；query 变化 → 旧 query 的 QA 记录归档。QA 回复与帧/时间范围绑定（`response_records`），供分块记忆用。

### 1.4 主动搭话的完整链路（源项目）

```
[VideoProcessorTrack.recv 周期抽帧]                    video_processor.py:89
        ↓ 每 process_interval 秒 / frames_per_batch 帧
[vlm_service.process_frame / process_frame_batch]       vlm_service.py:433 / 580
        ↓ OpenAI 兼容请求（system prompt = 三决策版）
[webinfer live_adapter /v1/chat/completions]            live_adapter.py:785
        ↓ normalize_model_output → </silence> 或 </response> 文本
[vlm_service.get_current_response()]                    vlm_service.py:622
        ↓ WS broadcast: {"type":"vlm_response", text}
[index.html updateResultText()]                         index.html:6439
        ↓ getVlmDisplayText() 过滤 </silence>           index.html:4307
        ↓ speakVlmText(displayText)  → TTS WebSocket    index.html:6457 / 5675
[浏览器播放]
```

即源项目的"主动搭话"= **周期场景分析 + 模型自主决策发言**，不是"检测到对话对象后搭话"。

---

## 2. 前端模式 UI 的具体做法（可借鉴点）

### 2.1 源项目没有模式单选 —— 只有"说话方式"选择

- 语音输入是**按钮**，不是模式选择：`startSpeech({ mode: 'toggle'|'press' })`（`index.html:7498`）、`syncSpeechButtons`（`index.html:7261`）动态改按钮文案（"按住说话 / 点击说话 / 停止说话"）。
- `asrStopMode = 'toggle'`（`index.html:3994`）控制停止语义：`'toggle'` 时 ASR WebSocket 加 `&continuous=1`（`index.html:7456`）→ 服务端 `stop_on_final=False`（`asr.py:300-321`）持续出字，用户手动停。
- `'auto'` 模式有**静音自动停**：RMS < `ASR_SILENCE_RMS_THRESHOLD`(0.012) 且静音 ≥ `ASR_SILENCE_AUTO_STOP_MS`(2000ms) → `stopSpeech()`（`index.html:7436-7441`）。

### 2.2 与"模式"最接近的 UI 控件

1. **系统提示词下拉（单选）** `index.html:3645-3646`：
   ```html
   <option value="DEFAULT_SYSTEM_PROMPT_EN" selected>Default (delegation)</option>
   <option value="DEFAULT_SYSTEM_PROMPT_NO_DELEGATION">No delegation</option>
   ```
   通过 WS `update_system_prompt`（`index.html:6404-6413` → `server.py:524`）切换后端提示词。这是源项目"在一个 UI 控件里显式互斥切换两种行为"的现成范式。
2. **toggle-switch 复选框组**（`index.html:3417-3553`）：popInToggle / glowToggle / fadeToggle / ttsEnabledToggle / backgroundEnabledToggle / debugShow* —— 设置面板统一用 `.toggle-switch input[type=checkbox]`（`index.html:312-319` CSS）。
3. **委派开关** `#backgroundEnabledToggle`（`index.html:3500`）→ `update_background_config`（`server.py:632`）→ `background_model.py` 的 enabled 位。

### 2.3 当前项目可借鉴的 UI 组织

| 源项目做法 | 位置 | 可借鉴点 |
|---|---|---|
| 系统提示词下拉单选 | `index.html:3645` | jarvis/live 显式单选可复用同一「下拉/单选组 + WS 下发」范式 |
| toggle-switch 组统一开关 | `index.html:3417-3553` | 若要做"双开/互斥"限制，可在 switch 上做 JS 互斥（如开 live 时联动关 jarvis） |
| `asrStopMode` 按钮三态 | `index.html:3994/7498` | "免唤醒常驻"与"按需说话"本质是同一麦克风链的不同停止策略 |
| WS `update_*` 控制面 | `server.py:511-724` | 新增模式路由 `/api/mode/select` 可并入同一 WS 控制面 |

> 注意：源项目本身**没有** jarvis/live 概念，因此"UI 如何限制同时启用"在源项目中无对应实现。当前项目的后端反而**明确允许 jarvis+live 双开**（见 §4.3）。

---

## 3. 说话对象判定（有 / 无，怎么做）

**结论：源项目没有独立的"说话对象判定"（addressee detection）模块**——既无声学方向性检测，也无"是否提到设备名/是否看着设备"的语义预判。检索 `addressee / self-talk / 自言自语 / directed` 等关键词，全仓库无命中。

实际靠**两层机制**近似实现"别乱回话"：

1. **query 门控（silence 短路）**：`FORCE_SILENCE_BEFORE_QUERY=true` 时，只要本次请求的 prompt 为空（无 query），直接回 `</silence>` 且不调模型（`live_adapter.py:1221-1231`）。即"没人说话 → 肯定不说话"。
2. **模型决策 token（语义判断）**：有 query 时，由 VLM 依据三决策系统提示词自行判断"值不值得开口"。提示词中**唯一接近"说话对象"的线索**是：
   - `no user query is pending` → silence；
   - `can answer a user question based on available evidence` → speak；
   - 场景无显著变化 → silence（`live_adapter.py:100-111`）。

**翻译成当前项目术语**：源项目的"说话对象判定"= 当前项目 webinfer 的 `_is_forced_silence`（仅 live + 无 query）+ `parse_model_decision`（模型 token）。前端 `getVlmDisplayText` 做最后兜底（`</silence>` 一律不显示/不发音，`index.html:4316-4318`）。

如果要补真正的"说话对象判定"，源项目无先例可抄，需自行设计（可考虑：query 文本中是否含称呼/问句特征、KWS 声学能量定向、或让模型在决策 token 前加 `</noop>` 等）。

---

## 4. 与当前项目差异

### 4.1 决策框架：当前项目是源项目的超集（已内化）

| 维度 | 源项目 7-22 | 当前项目 |
|---|---|---|
| 决策 token 三选一 | ✅ `live_adapter.py:100-111` | ✅ 同源，`webinfer/prompt_constants.py:58-69` |
| 强制静默 | ✅ `FORCE_SILENCE_BEFORE_QUERY`（全局） | ✅ 细化为 `_is_forced_silence`，**仅 live 模式生效**（`doc/specs/interaction-mode-isolation.md` §3） |
| 模式隔离 | ❌ 无 | ✅ 三交互模式 `live/call/jarvis`（`webinfer/infer_loop.py:62-69`）；`call` 用 `NO_DECISION_SYSTEM_PROMPT`、剥离决策 token |
| 流式决策协议 | ❌ 非流式逐帧 | ✅ P0-A NDJSON：`{"type":"decision"...}` 决策帧先行（`infer_loop.py:133-160`） |
| 委派解析 | ✅ `background_model.py:199` | ✅ 保留，且 `delegation_question` 走 decision 帧 |
| 摘要/记忆 | ✅ chunk + mid-term + long-term | ✅ 重构为 `memory_summarizer.py / memory_store_client.py` |

> 佐证：当前项目 `webinfer/live_adapter.py:13` 仅 `from app import create_app, main, parse_args`——源项目的单体 `live_adapter.py`（2775 行）已被当前项目拆分为 `adapter_core / infer_loop / prompt_assembly / request_parsing / response_format / session / memory_*` 等模块。

### 4.2 前端：当前项目新增 jarvis/live 交互层，源项目完全没有

| 能力 | 源项目 | 当前项目 |
|---|---|---|
| jarvis 唤醒模式 | ❌ 无（无 KWS） | ✅ `jarvis_mode.py`（2307 行）+ `services/asr/jarvis/kws.py`，`services/kws-training/` 训练管道 |
| 免唤醒常驻 live | ❌ 无 | ✅ `live_mode.py:71 LiveStateMachine`（851 行）+ `turn_controller.py TurnConfig.live()` |
| 13 态 turn 控制器 | ❌ 无 | ✅ `turn_controller.py:267-418`（LISTENING→USER_SPEAKING→PROCESSING→THINKING→SPEAKING→COOLDOWN…） |
| VAD | 无（无独立 VAD，靠 ASR） | ✅ `vad_bypass.py`（Silero，fail-open） |
| 打拍子打断（barge-in） | ❌ 无 | ✅ `reply_epoch`/`sentence_epoch` 守卫（`live_mode.py:783-803`） |
| 模式状态轮询 | ❌ 无 | ✅ `/api/jarvis/status`、`/api/live/status` 每秒轮询 + 状态胶囊（`index.html:6259-6361`） |

### 4.3 双开 vs 互斥（用户关心点①的现状）

当前项目后端**明确允许 jarvis 与 live 同时运行**：

`jarvis_session.py:205-208`：
```python
# Phase C: live sessions live in a SEPARATE dict so a jarvis session
# and a live session for the same webui session_id can coexist (双开)
self._live_sessions: dict[str, LiveSession] = {}
```
`jarvis_session.py:250-252`：`separate dict, so jarvis and live can run simultaneously.`

前端 `index.html`：live 有独立 `#liveModeBtn` 按钮（`index.html:761`，点击 → `/api/live/start|stop`，`index.html:4327/4405`）；jarvis 由 WebRTC offer 绑定 `bind_audio`（`jarvis_routes.py:51`）自动创建，前端只在关闭时调 `/api/jarvis/stop`（`index.html:4442/4560`）。**前端没有"jarvis 与 live 二选一"的单选/互斥逻辑**，后端也没有互斥锁——这正是用户关注的缺口：若想限制同时启用，源项目无先例，需在当前项目自行加 UI 单选 + 服务端互斥。

### 4.4 源项目有、当前项目也可再确认的部分

- **frame↔QA 记忆绑定**：`response_records` 带 `time_range`，QA 与帧时间对齐（`live_adapter.py:1290-1292`），可借鉴到当前项目 live 的对话记忆（当前 `_conv_history` 是纯文本 FIFO，`live_mode.py:141`）。
- **chunk 翻转 + 异步摘要**：`_flush_chunk`（`live_adapter.py:1661`）、`_submit_async_summary_if_needed`（`live_adapter.py:1847`），长会话记忆压缩机制。
- **`</delegation>` 委派到后台求解器**：当前项目已保留（`live_mode.py:584-603`），行为与源一致。

---

## 5. 当前项目可借鉴清单（按优先级）

| 优先级 | 借鉴项 | 源项目出处 | 当前项目落点建议 |
|---|---|---|---|
| **P0** | 模式显式单选 UI + 互斥限制（jarvis/live/call 三选一或双开开关） | 无直接先例；范式抄"系统提示词下拉单选"（`index.html:3645-3646`）+ toggle-switch 组（`index.html:3417-3553`） | 前端加模式单选组，WS 控制面（`server.py:511-724`）加 `set_mode` 消息；后端 `jarvis_session.py` 的 `_live_sessions`/`_sessions` 之间加互斥（若产品要求互斥） |
| **P0** | "说话对象判定"：明确要不要做 | 源项目无；现状=模型 token 决策 + 强制静默（`live_adapter.py:1221`） | 若要加：在 `_is_garbage_text`（`jarvis_mode.py:95`）基础上扩展（如问句特征/称呼检测），或新增决策 token（如 `</noop>`）让模型显式判"非对我说话" |
| **P1** | 无 query 强制静默 + 不调模型 | `live_adapter.py:1221-1231` | 当前 webinfer `_is_forced_silence` 已实现（仅 live 模式），确认 live_mode.py 消费路径已覆盖即可 |
| **P1** | QA↔帧时间戳绑定记忆 | `live_adapter.py:1290-1292`、`_format_turn_time_range` | 当前 live 的 `_conv_history`（`live_mode.py:141`）可考虑携带时间范围，供"刚才那帧"类追问 |
| **P2** | 长会话 chunk 翻转 + 异步摘要 | `live_adapter.py:1661-1938` | 当前项目已有 `memory_store`/`memory_summarizer`，可对齐 chunk 边界语义 |
| **P2** | 静音自动停参数曝光 | `index.html:3900-3901`（`ASR_SILENCE_AUTO_STOP_MS=2000`、`ASR_SILENCE_RMS_THRESHOLD=0.012`） | 可加到设置面板，作为 live 模式的 endpoint 灵敏度 |

---

## 6. 差异与风险

1. **概念混用风险**：源项目的 "live"（`interaction_mode=live`）= 三决策视频流模式；当前项目 C.A 的 "live mode" = 免唤醒常驻监听层。当前项目 `live_mode.py:435` 把 C.A 请求标为 `interaction_mode="live"` 送到 webinfer，正好复用源项目的三决策——**语义对齐但务必在文档/代码注释里讲清两层 live 的区别**，否则后续维护易串。
2. **双开已存在**：jarvis+live 同时监听会共享同一麦克风/ASR/WebRTC 链路（`live_routes.py:97-121 bind_live_audio_for_peer` 与 jarvis 同入口），存在回声/抢答风险；当前 `reply_epoch`/`sentence_epoch`（`live_mode.py:152-154`）只防单 session 内串扰，不防双 session 互抢。若做互斥，需在前端 + `jarvis_session.py` 两处落锁。
3. **技术栈差异**：源项目 webinfer 后端为 **vLLM**（`services/webinfer/README.md`：`forwards requests to local vLLM OpenAI API services`），当前项目为 **llama-server**（llama.cpp，`webinfer/pyproject.toml` 关键词 `llama-server`）。决策 token 协议不依赖后端实现，但 **vLLM 对 `</silence>` 等 token 的稳定生成**与 llama.cpp 可能有差异，需在 llama.cpp 侧验证三决策 token 的诱导率（当前项目已有 `test_decision_token_isolation.py` 26 例与 `test_decision_parser_regression.py` 可作基线）。
4. **源项目无 KWS/常驻监听**：所有"唤醒词/免唤醒"代码都是当前项目自研（`services/kws-training/`、`services/asr/jarvis/`），与源项目无关，无法参考。
5. **前端架构差异**：源项目前端是 8690 行单文件 `index.html`；当前项目已拆分为 `index.html + joy_ws.js + render_markdown.js + styles.css` 等。借鉴源项目 UI 时按当前项目模块化方式落位，勿照搬单文件。

---

## 附：技术栈一致性速查

| 层 | 源项目（7-22） | 当前项目 |
|---|---|---|
| WebUI | aiohttp + aiortc + openai SDK + opencv + av（`services/webui/pyproject.toml`） | 同栈 + sherpa-onnx（`services/webui/pyproject.toml`） |
| webinfer 推理适配 | aiohttp，转发 **vLLM** | aiohttp，转发 **llama-server**（llama.cpp） |
| ASR | FastAPI 适配器，转发上游 OpenAI 式 ASR（`asr/scripts/run-adapter.sh` → `:8993/v1/audio/transcriptions`） | sherpa-onnx 进程内 ASR（`services/asr/jarvis/asr.py`）+ 云端可切换 |
| KWS / 唤醒 | 无 | sherpa-onnx KeywordSpotter + 自训 bt-en 模型（`jarvis_mode.py:147-158`） |
| TTS | WebSocket `speak`（`index.html:5658`） | voice_clone_api（`live_mode.py:739-758`） |
| 决策 token 协议 | `</silence>` / `</response>` / `<delegation>` | 同协议 + NDJSON 决策帧流式化 + 三模式隔离 |
