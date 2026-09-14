# jarvis TTS 链路延迟分析（打断后新回复慢）

> 日期: 2026-08-12
> 触发: 用户实机——打断优化 P0 已生效（说话即停旧音频），但**新回复 TTS 慢**：打断后重新播报的延迟明显，需定位慢在哪一环
> 方法: 只读代码走查 + 延迟量级估算；对照块3 报告（`turn-controller-2026-08-11/final_report_unified_turn_controller.md`、`turn-controller-2026-08-11/06_e2e_latency_engineering.md`）
> 结论先行: **全链路串行、无任何流式/并行**——LLM 完整生成 → TTS 单次全量合成 → 前端等完整 blob。三大串行等待叠加，打断后新回复 E2E ≈ **2.6s–3.5s+**。最大瓶颈是 **LLM 非流式全量等待**（300–1500ms），次之 **MiniMax 单次全量合成**（300–2000ms+），另有 2s 保守端点检测前置。

---

## 一、全链路各环延迟拆解（证据 + 量级）

当前 jarvis 语音对话路径为 `stream_tts=False`（`jarvis_mode.py:1453`）：后端不做 WebRTC PCM 推送，浏览器 `<audio id="btTtsPlayer">` 经 `playLlmReplyAudio` 播放（`jarvis_mode.py:1448-1452` 注释明确）。

### 链路图（当前实现，全串行）

```
用户停话
  │ ① 2s 静音停滞（保守 endpoint）        jarvis_mode.py:1308-1309
  ▼
ASR final → _send_utterance_to_llm         jarvis_mode.py:1453
  │ ② ③ LLM 非流式完整生成
  ▼
POST /v1/text/chat (webinfer :8070)        jarvis_mode.py:1689-1698  (httpx 单次 await 完整 JSON)
  │   └ chat.completions.create(...) 无 stream=True   infer_loop.py:276-280 / 901-905
  │       主模型 8.19B 量化 @ llama-server :7060      app.py:41-42
  ▼ ④ decision 解析（<5ms）                infer_loop.py:284
完整文本 → WS llm_reply 广播（~10ms）       server.py:148-155
  ▼ ⑤⑥ 前端发起 TTS
前端 playLlmReplyAudio → fetch /api/tts/synthesize   index.html:5234
  │   └ server 转发 POST :8985 /v1/synthesize        server.py:600-650
  ▼ ⑦ MiniMax 单次全量合成（streaming=False）
zero_shot_synthesize → POST /v1/t2a_v2 {"stream": false}  cloud_clone.py:394-421
  │       等完整音频返回（非流式）
  ▼ ⑧ 包 WAV（<5ms）                     server.py:574-597
完整 WAV blob → 前端 play()                index.html:5243-5257（等完整 blob 才播）
```

### 各环延迟预算表

| # | 环节 | 现状 | 代码证据 | 量级估算 |
|---|------|------|----------|----------|
| ① | 端点检测（静音停滞） | 2s 保守 commit | `jarvis_mode.py:1308-1309`（bargein 报告已核实） | **~2000ms** |
| ② | jarvis→webinfer HTTP 往返 | 非流式 await | `jarvis_mode.py:1689-1698`（`httpx.AsyncClient` 单次 POST，无 stream） | 10–50ms |
| ③ | **LLM 完整生成** | `chat.completions.create` 无 `stream=True`，**完整生成才返回** | `infer_loop.py:276-280`（text 路径）、`infer_loop.py:901-905`（`_call_main_model`）；`max_tokens=200`（`jarvis_mode.py:1694`） | **300–1500ms+**（TTFT 200–500ms + 解码；8.19B 量化 GPU 2–5 tok/s 量级，长回复更久） |
| ④ | decision token 解析 | `_parse_decision_tokens` | `infer_loop.py:284` | <5ms |
| ⑤ | WS llm_reply 广播 | 完整文本一次推送 | `server.py:148-155` | 5–10ms |
| ⑥ | 前端 fetch TTS | 拿**完整文本**后发起 | `index.html:5234` | 10–20ms |
| ⑦ | **MiniMax 合成** | `"streaming": false` 单次全量，**等完整音频** | `server.py:624`（`streaming: False`）、`jarvis_mode.py:1827`（`streaming: False`）、`cloud_clone.py:415-421`（非流式 `resp.json()` 整包返回） | **300–2000ms+**（MiniMax 云 TTFB 150–250ms，长文本全量更久） |
| ⑧ | WAV 打包 + blob 传输 | 服务端包完 + 前端等完整 blob | `server.py:574-597`、`index.html:5243-5253` | 50–200ms |
| ⑨ | `<audio>` 播放 | blob 就绪才 `play()` | `index.html:5255-5257` | 20–50ms |

### 串行等待合计

```
打断后新回复 E2E（用户停话 → 首音）
  ≈ ① 2000ms + ③ 300-1500ms + ⑦ 300-2000ms + ⑤⑥⑧⑨ 100-300ms
  ≈ 2.6s – 3.5s+（不含 ASR 处理本身）
```

对照业界：Salesforce 级联流水线流式并行 TTFA ≈ **755ms**（`turn-controller-2026-08-11/06_e2e_latency_engineering.md` §6.4），我们当前差距约 **3–5 倍**。

---

## 二、瓶颈定位

### 结论：三大串行等待叠加，无一项是流式

1. **最大瓶颈 —— LLM 非流式全量等待（③）**
   - `webinfer` 两处主模型调用均为 `chat.completions.create(...)` **无 `stream=True`**（`infer_loop.py:276-280` text 路径、`infer_loop.py:901-905` `_call_main_model`）。
   - jarvis 侧同步 `await client.post(...)` 等**完整 JSON**（`jarvis_mode.py:1689`）才解析 decision → 广播。
   - 8.19B 量化 @ GPU 2–5 tok/s：即便 TTFT 只有 200–500ms，正文 50–150 token 的解码时间就把 TTFT 优势吃光。**LLM 的"尾 token"延迟完全暴露给用户**——这正是 Sentence Buffer 要消除的。

2. **次之 —— MiniMax 单次全量合成（⑦）**
   - 协议层**已支持 SSE 流式**（`cloud_clone.py:423-439`，`Accept: text/event-stream`，doc/subsystems/voice-clone.md:270 明确"实时对话要的是首字延迟 <300ms"），但**当前全部调用方都传 `"streaming": false`**（`server.py:624`、`jarvis_mode.py:1827`）。
   - 前端 `playLlmReplyAudio` 走 HTTP 单发，**等完整 WAV blob 才播**（`index.html:5243-5257`）——SSE 流式能力在 jarvis 浏览器链路完全未启用。

3. **前置 —— 2s 保守端点检测（①）**
   - 打断后新 utterance 同样要等 2s 静音停滞才 commit（`jarvis_mode.py:1308-1309`）。
   - 这是"宁可慢打断，不可误打断"的保守策略（bargein 报告 §二），属于可调参数，但不在本次"TTS 慢"的直接修复范围（对**每次**回复都 +2s）。

4. **次要 —— 前端等完整 blob（⑧⑨）**
   - 无缓冲/无分块播放，blob 到达前无任何声音。属 ⑦ 的连带效应，随流式化一并解决。

### 一句话根因

> **LLM 完整生成 → 合成完整 → 播放完整，三个"完整"串行排队**，没有任何一环节开启流式；而链路两端的流式能力（webinfer 可用 `stream=True`、MiniMax 可用 SSE）**全部闲置**。

---

## 三、优化方案候选（按性价比排序）

### 方案 ①：LLM 流式 + Sentence Buffer 分句 TTS ⭐ 首选

- **做法**
  - `webinfer` `/v1/text/chat` 增加流式分支：`chat.completions.create(stream=True)`（`infer_loop.py:276`），SSE/WS 增量推 token。
  - **decision token 仍先出**（`</response>` 是首 token 之一）→ jarvis 先拿到"是否播报"判断，再对后续正文按句 flush。
  - jarvis 端实现 Sentence Buffer（块3 §7.2 有现成原语，`turn-controller-2026-08-11/final_report_unified_turn_controller.md` §6.3）：句边界（`.!?。！？`）触发 TTS，第一句播时 LLM 还在生成第二句。
  - TTS 侧走方案 ③ 的流式合成（或后端 `_stream_tts` 逐句调用，`jarvis_mode.py:1811` 已有雏形）。
- **改动面**：后端为主（webinfer 流式出口 + jarvis 句级编排）；前端可选（若逐句走 WS 到前端 `<audio>`，需 `playLlmReplyAudio` 支持分段队列）。
- **风险**：中——decision token 流式解析需改 `_handle_text_payload`（`infer_loop.py:228-284`）与 jarvis 的 `_send_to_llm`（`jarvis_mode.py:1689` 的同步 await 结构）；句边界误判需排除 `Dr.`/`U.S.` 等（块3 §7.2 FALSE_POSITIVES）；历史记录仍等完整文本补齐。
- **预期收益**：**最大**。LLM 生成（300–1500ms）与 TTS 播放重叠 → E2E 向 **~1s**（对照块3：1500ms→755ms 的论据）收敛；同时解决"新回复慢"的主体。

### 方案 ②：首句预合成 / 预播放（TTS TTFA 优化）

- **做法**：LLM 返回完整文本后（方案 ① 未落地前的过渡），后端/前端把文本**先切第一句**立即送 TTS 合成并播放，剩余句子后续合成拼接；或前端对完整文本先合成首句、后台合成其余。
- **改动面**：后端（`server.py` TTS 处理拆首句）+ 前端（`playLlmReplyAudio` 分段）；可纯前端做。
- **风险**：低——不触碰 LLM 链路，改动隔离。
- **预期收益**：首音提前 1 句（约 200–500ms），但**治标不治本**——LLM 的 800ms 全量等待仍在关键路径上（因为此时 LLM 已经是完整文本了）。作为 ① 的前置过渡或兜底。

### 方案 ③：MiniMax 流式合成落地（TTS 侧流式化）

- **做法**
  - :8985 `/v1/synthesize` **已支持 `streaming=true`**（`main.py:460-465` → `zero_shot_synthesize`，`cloud_clone.py:423-439` SSE 分块 yield），`streaming=False` 分支走 `resp.json()` 整包（`cloud_clone.py:415-421`）。
  - 把 `server.py:624` 与 `jarvis_mode.py:1827` 的 `"streaming": false` 改 true，`_tts_synthesize_handler`（`server.py:600`）改 SSE 转发（chunk → 前端 `<audio>` 追加）；jarvis 进程内路径 `_stream_tts`（`jarvis_mode.py:1822-1840`）直接消费 `zero_shot_synthesize` 的 async iterator。
- **改动面**：后端为主（server.py 转发层 + jarvis `_stream_tts` 消费流）；前端需支持分块/追加播放。
- **风险**：低-中——MiniMax SSE 已有实现与测试（`test_minimax_client.py`），主要工作是转发层改造；注意 WebSocket/SSE 下 `<audio>` 分块播放需用 MediaSource 或逐段音频队列。
- **预期收益**：TTS 环节首音从"全量等 300–2000ms"降到 **TTFB 150–250ms**；与 ① 组合后 TTS 与 LLM 完全并行。

### 方案 ④：前端分块播放（纯前端兜底）

- **做法**：`playLlmReplyAudio` 对完整文本按句切段，逐段 `fetch` 合成、逐段入队播放（边合成边播）。
- **改动面**：前端 only。
- **风险**：中——句间拼接停顿、并发合成顺序乱序、epoch 打断竞态（`index.html:5247-5262` 的 barge-in 守卫需扩展）。
- **预期收益**：不依赖后端改动即可让首音提前（等同 ② 的完整版），但单次合成量减半后 MiniMax 返回更快。

### 性价比总览

| 方案 | 改动面 | 风险 | 预期收益 | 推荐 |
|------|--------|------|----------|------|
| ① LLM 流式 + Sentence Buffer | 后端为主 | 中 | **最大**（E2E → ~1s） | ⭐ P0 |
| ③ MiniMax 流式合成 | 后端为主 | 低-中 | 大（TTS 首音 150–250ms） | P0（与①组合） |
| ② 首句预合成 | 后端/前端 | 低 | 中（首音提前 1 句） | P1 过渡 |
| ④ 前端分块播放 | 前端 only | 中 | 中（无后端改动） | P1 兜底 |

> 补充：① 与 ③ 是同一件事的两端（LLM 流式喂 TTS，TTS 流式播放），建议一起做——单独做 ① 而 TTS 仍全量等，Sentence Buffer flush 出的句子依然会被 `streaming=false` 合成卡住；单独做 ③ 而 LLM 仍全量等，TTS 无句可流。

---

## 四、与 Phase C（live 接入）的复用关系

**本链路优化是 Phase C 的"预实现"，live 直接受益、可整体复用：**

1. **Sentence Buffer / 句级编排**（方案 ①）：是 Phase C 流式并行流水线（`turn-controller-2026-08-11/final_report_unified_turn_controller.md` §6.3/§6.4）的**核心原语**。jarvis 侧先落地后，live adapter 直接复用同一 buffer 与 flush 逻辑。
2. **webinfer 流式出口**（`/v1/text/chat` stream 分支）：Phase C 的 live LLM 流式调用与它同源（`_call_main_model` 加 `stream=True`），一次改造、两条链路共用。
3. **MiniMax SSE 流式合成**（方案 ③）：live 的 TTS 必须流式（TTFA <300ms，`voice-clone.md:270`），本方案落地的 `streaming=true` 链路与 `_stream_tts` 消费方式，live 直接复用 :8985 既有能力。
4. **打断竞态守卫**（`index.html` llmReplyEpoch）：已为分块/分段播放预留 epoch 机制，流式化后直接承接"流中打断"语义。

**结论**：当前 jarvis 链路的流式化改造 ≈ Phase C live 接入的前 60% 工程量（LLM 流式 + Sentence Buffer + TTS 流式），剩余为 live 的传输层（WebRTC）与 Turn Controller 事件接线。**先在此链路验证，再把已验证的流式原语迁到 live，风险与返工最小。**

---

## 五、关键代码索引

| 位置 | 说明 |
|------|------|
| `jarvis_mode.py:1453` | `stream_tts=False`，浏览器播放路径 |
| `jarvis_mode.py:1689-1698` | jarvis→webinfer 非流式 await（`max_tokens=200`） |
| `jarvis_mode.py:1811-1840` | `_stream_tts`（进程内 TTS，`streaming: False`） |
| `infer_loop.py:276-280` | text 路径 `chat.completions.create`（无 stream） |
| `infer_loop.py:901-905` | `_call_main_model`（无 stream） |
| `app.py:41-42` | 主模型后端 llama-server `:7060` |
| `server.py:600-650` | `/api/tts/synthesize` 转发（`streaming: False`） |
| `server.py:148-155` | `llm_reply` WS 广播 |
| `voice-clone/main.py:482-581` | `_do_synthesize`（非流式 `zero_shot_synthesize` 全量等） |
| `cloud_clone.py:415-421` vs `423-439` | 非流式整包 vs SSE 流式（**已实现，未启用**） |
| `index.html:5234-5257` | 前端等完整 blob 才播放 |
| `index.html:5247-5262` | barge-in epoch 守卫（流式化可复用） |
