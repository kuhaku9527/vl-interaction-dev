# 上游原版 JoyAI-VL-Interaction「实时决策」机制认定（2026-09-20）

> 调研端点（research / AFK，只读）。目的：认定上游如何驱动「实时决策」，用于判定本地「每秒一帧决策不存在」是**缺陷 / 从未实现 / 有意收敛**。
> 方法：`gh api` 读上游仓库源码（未 clone、未下大文件）+ 本地论文 PDF（21 页，与上游同名文件字节一致）+ 本地仓库 git log / `决策/` / `doc/specs/`。
> 上游快照：`jd-opensource/JoyAI-VL-Interaction` @ `main`（★1923 / fork 192 / open issues 41 / Apache-2.0）。
> 所有外部内容仅作数据引用。**本报告不写行号**（按符号名/章节定位）。

---

## 0. 一句话结论

**判定 (c) 有意收敛为主，叠加一层「文档层面夸大」。**

- **上游确实是「每秒一次、由帧时钟驱动的持续决策」**——这不是论文修辞，是**代码事实**：上游 WebUI 的 `VideoProcessorTrack.process_interval_seconds = 1.0`（类属性默认值）以 1 Hz 抽帧并**每帧发起一次推理**；上游推理适配层 `live_adapter.py` 的 system prompt 原文写死 *"At every inference step you MUST choose exactly one of the following three actions"*，并在 `normalize_model_output()` 里**真正解析** `</silence>` / `</response>` 决策 token。**最硬的一条证据**：上游 `services/webinfer/README.md` 的「Common Pitfalls」明确承认 *"By default, no query forces silence, which can look like 'the model is not talking'"* ——这句话只有在「有一个每秒都在跑、但因无 query 而被短路成 silence 的循环」时才讲得通；它同时反证了**该循环的持续存在**与**它依赖一个常驻 query 才产生真实推理**两个事实。
- **本地的「每秒一帧决策不存在」是主动的设计收敛**，不是改造时丢失：本地 spec `doc/specs/live-visual-cb.md` §2.4 明文裁定 *"proactive 循环…每 `PROACTIVE_INTERVAL_S`（默认 5s）…**默认关闭**（env `LIVE_PROACTIVE_ENABLED`，防乱开口，真机验证后开）"*，且本地**保留了 1 fps 帧传输**（`screen_capture.js` 推 1 fps JPEG，`recent_frames` deque 默认窗口 6）。即：**传输按 1 fps 保留、决策按需触发**，是有意识的取舍。
- **但本地文档确实夸大了**：`ARCHITECTURE.md` §1「核心模型 JoyAI-VL-8B 每秒自主决策」描述的是**上游的架构**，不是本地当前行为。本地当前行为是「用户说话轮 + 可选 5s proactive 轮」的稀疏触发。这部分属 (b) 味道的叙述性夸大。

**对本地 #148 的判定**：工单方向**成立**（本地与上游在「决策驱动机制」上确有真实差距，且差距可量化），但**性质不是 bug 修复，而是能力补齐 / 范围决策**。不应按「回归缺陷」立案，应按「与上游对齐的可选能力 + 文档纠偏」立案。

---

## 1. 上游的决策驱动机制（逐条 + 证据）

### 1.1 有服务端定时循环吗？→ **有。是「帧时钟驱动的 1 Hz 推理循环」，不是「每来一帧处理一帧」的纯被动，也不是独立的 wall-clock 定时器**

上游把「采样节拍」实现在 WebRTC 视频轨的 `recv()` 里，用「距上次处理是否超过间隔」做闸门：

- `services/webui/src/joy_interaction_webui/video_processor.py` → `class VideoProcessorTrack(VideoStreamTrack)`：
  - 类属性 `process_interval_seconds = 1.0`（注释：*"Class variable for processing interval in seconds (can be updated dynamically)"*）
  - 类属性 `frames_per_batch = 1`（注释：*"Number of frames to batch per VLM inference (1 = original behavior)"*）
  - `recv()` 内以 `need_conversion = (time_since_last >= interval_sec) or (self.frame_count == 1)` 判定，命中后调 `self.vlm_service.process_frame(...)`，并传 `timestamp_interval_seconds=interval_sec`
  - 批量为 1 时走单帧路径；`frames_per_batch > 1` 时按 `sub_interval = interval_sec / frames_per_batch` 采子帧、攒满一批走 `process_frame_batch(batch)`
- 该间隔**运行期可改**：`services/webui/src/joy_interaction_webui/server.py` 的 WS 控制面处理 `update_interval` / `update_frames_per_batch`，直接写 `VideoProcessorTrack.process_interval_seconds`；启动时由 CLI `--process-interval` 或环境变量 `LIVE_VLM_PROCESS_INTERVAL`、`LIVE_VLM_FRAMES_PER_BATCH` 赋值。

**结论**：每 1 秒**触发一次推理请求**，由视频轨的帧到达节拍驱动。这是一个**服务端常驻的周期性推理循环**，节拍可动态调整。

### 1.2 决策 token 在哪条路径产生？→ **在推理适配层 `live_adapter.py`，且是「每轮推理都产生」**

上游的架构是 **WebUI（抽帧）→ VLMService（HTTP 转发）→ live_adapter（组装上下文 + 调主模型 + 规范化决策 token）→ vLLM 主模型**。

- `services/webui/src/joy_interaction_webui/vlm_service.py`：`process_frame()` → `analyze_image()`，把单帧 JPEG + prompt 组装成 OpenAI 多模态 `Chat Completions` 请求；`analyze_images()` 为批量版。注意它**只是转达方**——真正的决策产生在下游适配层。
- `services/webinfer/live_adapter.py`（约 2809 行）：
  - `DEFAULT_SYSTEM_PROMPT_EN` / `DEFAULT_SYSTEM_PROMPT`：原文规定三选一动作 `</silence>` / `</response> Your reply here.` / `</response> … </delegation> <the question>`；并有 `DEFAULT_SYSTEM_PROMPT_NO_DELEGATION` 二选一版。措辞是 *"At every inference step you MUST choose exactly one of the following three actions"*。
  - `normalize_model_output(text)`：把裸输出规范化为 `</silence>` 或 `</response> 首行`。这是**决策 token 的服务端解析点**。
  - `extract_response_payload(text)`：取出 `</response>` 之后的实际发言文本。
  - `_handle_chat_payload` → 每轮：`_update_query_state` → 判定是否强制静默 → `_call_main_model` → `normalize_model_output` → 记录 `response_records` → chunk 翻转 / 异步摘要。日志打点 `[%s] turn=%d timing: total=… vllm=…`。
  - 路由仅 4 个：`/health`、`/v1/models`、`/v1/chat/completions`、`/v1/streaming/reset`（`app.router.add_*`）。**没有任何独立的后台推理定时任务**——唯一的 `while True` 是 `_session_cleanup_loop()`，`await asyncio.sleep(300)` 做会话回收，与推理无关。**这反证：决策节拍来自帧到达，不来自 adapter 内部**。
  - `services/webinfer/README.md` §Inference and Memory Flow 第 3-7 步把这条链路写得很清楚：每帧包成内部消息（`<time range>` + image）→ prompt 默认即当前 query 且**持续存在直到被新 prompt 替换** → 无 query 时直接返回 `</silence>` 且**不调主模型**。

**结论**：决策 token 在**每一轮推理**产生（持续产生），产生点是 `live_adapter` 的 `normalize_model_output`；消费点在上游前端（`getVlmDisplayText()` 过滤 `</silence>`）与 `background_model.parse_delegation()`。

### 1.3 有类似 proactive 的机制吗？→ **没有独立的「proactive 开关」，但 proactive 是这套设计的默认工作模式**

上游**不存在** `proactive` 这个命名、也没有 `LIVE_PROACTIVE_ENABLED` 这样的门控。上游的做法更彻底：**per-second 循环永远在跑，靠一个「常驻 query」把主动权交给模型**。

- `AdapterConfig.force_silence_before_query: bool = True`（**上游默认值就是 True**）；CLI 提供 `--force-silence-before-query` / `--no-force-silence-before-query`。
- `AdapterConfig.use_prompt_as_query: bool = True`；`_update_query_state()` 的语义是：**prompt 一旦设置就持续生效**，直到被一个新 prompt 替换（替换时归档上一段 QA）。README §Inference and Memory Flow 第 4 步原文：*"The prompt is treated as the current user question by default. The question persists until a new prompt replaces it."*
- 强制静默的分支逻辑：`is_forced_silence = self.config.force_silence_before_query and not state.current_query_text`；命中则 `generated_text = "</silence>"`，且日志明写 *"(forced silence, inference skipped)"*——**这是一次「省算」短路，不是取消循环**。

**这套机制怎么支撑「主动说话」**：用户给一句**常驻任务型 query**（论文 Listing 1 原文：*"Alert me whenever a physical confrontation happens."*），此后**每秒**循环都真实调模型，模型每秒自己决定 `</silence>` 还是 `</response>`。论文 §5.2 的 Fall Detection 案例正是这个形态（*"JoyAI-VL-Interaction raises the alert at the instant of the fall"*，而 Doubao *"reacts four to five seconds later"*，论文明确指出该延迟 *"is the polling interval of an external trigger surfacing as latency"*）。

- 默认开还是关：**循环默认开（1 Hz 常驻）**；**「无 query 时静默」默认开（短路）**。所以「开箱即用时不主动说话」是上游的**默认体验**，上游 README 的 Pitfalls 亲口承认这一点。
- 间隔：**1 秒**（`process_interval_seconds = 1.0`，env `LIVE_VLM_PROCESS_INTERVAL`，container 16GB profile 也固化为 `LIVE_VLM_PROCESS_INTERVAL=1.0`）。

### 1.4 帧怎么进模型？→ **逐帧（每秒 1 帧），单帧即一次请求；支持 N 帧批量；上下文靠「分块摘要 + 前缀复用」而非堆帧**

- **进模型方式**：默认 `frames_per_batch = 1` → 每 1 秒把**当前最新 1 帧**转 RGB → JPEG → Base64 data-URL，包成 `<time range>` 文本 + image 的 user message 发走。可配 `frames_per_batch > 1` 做「一个推理周期内均匀采 N 帧、一次请求发 N 张」。论文 §4.1 Ingestion 原文与之一致：*"A sampling module downsamples the stream at a fixed interval, 1 Hz by default and configurable per scenario… The sampled frames are converted to RGB, JPEG-encoded, and wrapped as Base64 data-URLs"*。
- **历史帧不进上下文（关键）**：帧按 `chunk`（默认 200 帧，container 16GB 用 `CHUNK=70`，24GB 用 `CHUNK=100`）切块；每满一块由**独立的摘要模型**生成中期摘要，每 `compress_every_n_chunks`（默认 5，16GB profile 用 3）个中期摘要压缩为长期记忆。即：**帧只活在当前 chunk 的窗口里，更早的一切降级为文本摘要**。
- **AdaCodec 的定位**：论文 §3.1 原文——*"AdaCodec spends only about 16 tokens on each predictable frame and full ViT tokens only at scene changes, so the budget grows far more slowly and its heavy part scales with how much the scene changes rather than with frame count."* 论文 §4.4 进一步把它作为**服务端成本控制**手段：*"AdaCodec (§3.1) further shrinks each predictable frame to about sixteen tokens, keeping per-step work small."*
- **前缀复用**：论文 §4.4 明确——文本记忆「每块预填一次进 KV cache」，此后**每步只算新观察到的帧 + 上一轮回复**，记忆与块内早期轮次**不重算**。
- 上游 `AdapterConfig` 里的上下文参数：`max_pixels = 262144`、`main_max_tokens = 128`（webinfer 默认；container 16GB 用 `MAIN_MAX_TOKENS=256`）、`frame_seconds = 1.0`、`chunk = 200`、`compress_every_n_chunks = 5`、`keep_qa_history = True`。

### 1.5 一句话归纳上游机制

> **上游 = 「1 Hz 帧时钟 → 每次一帧 → 一次推理 → 一个决策 token」，循环常驻；用一个常驻 query 决定这循环是「真实推理」还是「短路成 silence」。proactive 不是一个功能开关，而是这个常驻循环 + 常驻 query 的自然产物。**

---

## 2. 上游 vs 本地：架构差异对照表

| 维度 | 上游原版 | 本地 fork | 证据 |
|---|---|---|---|
| **帧抽取节拍** | 1 Hz，`VideoProcessorTrack.process_interval_seconds = 1.0`，运行期可改 | 前端 `screen_capture.js` 推 1 fps JPEG（`frameSeq` 单调）；后端 `recent_frames` deque 默认窗口 6 | 上游 `video_processor.py` / `server.py`；本地 `doc/specs/live-visual-cb.md` §2.2、§3 层 1 |
| **决策节拍** | **每帧一次推理 → 每秒一个决策 token** | **用户说话轮**（提交后）+ **可选 proactive 轮**（`LIVE_PROACTIVE_INTERVAL_S` 默认 **5.0 s**，`LIVE_PROACTIVE_ENABLED` 默认 **false**） | 上游 `live_adapter.py` system prompt + `normalize_model_output`；本地 `live_proactive.py`（`_DEFAULT_PROACTIVE_INTERVAL_S = 5.0`、`proactive_enabled_from_env()` default=False） |
| **决策在哪产生** | `live_adapter` 推理适配层，**每轮推理都产生**，并被服务端解析 | 本地 `webinfer` `/v1/text/chat` 的**四态决策**（silence/response/delegate/not-for-me），**仅在用户轮与（开启后的）proactive 轮**产生 | 上游 `services/webinfer/README.md`、`live_adapter.py`；本地 `决策/交互模式与决策token规范.md`、`doc/specs/live-visual-cb.md` §2.3 |
| **触发方式** | **常驻循环 + 常驻 query**；无 query → 短路 `</silence>` 且**不推理**（`force_silence_before_query=True` 为**上游默认**） | **事件驱动**：用户提交文本/语音；proactive 需显式开启（env 门控 + 前端 checkbox） | 上游 `AdapterConfig.force_silence_before_query`、`--no-force-silence-before-query`；本地 `live_proactive.py` + `doc/specs/live-visual-cb.md` §2.4 |
| **上下文控制** | chunk（200/70/100 帧）→ 中期摘要（独立摘要模型）→ 长期记忆；**帧不进长历史**；AdaCodec ≈16 tok/可预测帧；vLLM 前缀复用 | 帧**只注入当前轮**、不进 history（`recent_frames` 不持久化）；本地实测 **768×576 单图 prompt eval 453 ms** | 上游论文 §3.1 / §4.3 / §4.4、`services/webinfer/README.md`；本地 `doc/specs/live-visual-cb.md` §2.6、`决策/服务-VLM.md` |
| **1 fps 帧路径是否保留** | 是（核心路径） | **传输保留、决策未接**：`VideoProcessorTrack` 仍在仓库，但本地**零构造点**；本地改用 `screen_capture.js` → WS `frame` → `live_mode.py` | 本地实测（本项目已确认）；`doc/specs/live-visual-cb.md` §2.1 |
| **推理后端** | vLLM（`vllm/vllm-openai:v0.22.0`），主模型 **INT4 AWQ G32**，`MAX_MODEL_LEN=32768`（16GB profile） | **llama.cpp**，分离式双模型：IQ4_NL GGUF LLM + mmproj F16 | 上游 `container/16GB/.env.example`、`container/16GB/README.md`；本地实测 |
| **硬件** | 16GB profile = **1×16GB 主 + 3×16GB**（或 3 个 API）；24GB profile = 1×24GB + 3×24GB | **单卡 RTX 5060 Ti 16GB**，实测显存 **9.3 GB** | 上游 `container/16GB/README.md`、`container/24GB/README.md`；本地实测（git `7f7eb37` 修正 VRAM 5.8→9.3GB） |

---

## 3. 「hours of continuous video with sub-second latency」的实现拆解

论文摘要原句：*"It supports hours of continuous video with sub-second latency."*

**结论：既不是「靠持续每秒推理」单独实现，也不是「稀疏触发」；而是「持续每秒推理 + 每步只算增量」的组合。上游是「多算」而非「少算」——但每一算都被压到最小。**

拆解为四层：

1. **持续每秒推理是前提，不是可选项。** 论文 §4.1 原文：*"These frames are fed to the model every second, so it always acts on what is happening now rather than on a delayed batch."*；*"at every second it takes one action, to speak, stay silent, or delegate"*。论文 §2.4 更直接：*"the decision of when to act lives inside our model and is taken every second, event-driven, so reaction is bounded by inference rather than by a user's turn or a trigger clock."* 这与本地「稀疏触发」是**范式差异**——上游明确把「帧率」当作延迟上界的来源。
2. **AdaCodec 是为了「每步少算」，但服务于「每秒都算」。** §3.1 原文：*"a per-frame interface spends full ViT tokens on every frame, so cost and latency grow quickly with the length of the stream. AdaCodec spends only about 16 tokens on each predictable frame and full ViT tokens only at scene changes, so the budget grows far more slowly."* ——省的是**每步的 token 数**，不是**步数**。这正是「持续决策」在经济上可行的前提。
3. **前缀复用是为了「步与步之间不重复算」。** §4.4 原文：*"the text memory of §4.3 is prefilled once per chunk into a KV cache, and at every subsequent step only the newly observed frames and the previous reply are computed, while the memory and earlier in-chunk turns are reused without recomputation."* 并且论文把「不做前缀复用」的方案明确判死：*"a sliding window bounds the context but breaks prefix reuse, since successive steps no longer share a common prefix, so an engine's prefix cache buys nothing and latency plateaus above the real-time budget."*
4. **「hours」靠分层记忆的异步压缩，不靠无界上下文。** §4.3 原文：三层记忆（短期原始视觉 token / 中期文本摘要 / 长期压缩块），*"The consolidation steps run asynchronously, ahead of each boundary, so they hide behind mainline inference and never stall the real-time loop, and together the tiers reach up to roughly two hours of context."*；§4.4：*"The system sustains over two hours of continuous video at sub-second end-to-end latency on standard vLLM."*

**关于「sub-second」的诚实读法**：论文的 sub-second 指的是**端到端单步延迟**（推理预算内），而非「任意时刻亚秒响应任意事件」。上游的决策粒度就是 **1 秒**——论文 §1 与 §3 反复写 *"choosing each second"* / *"at every second"* / *"per-second"*。所以「实时」= **1 Hz 决策频率下的亚秒单步延迟**。

---

## 4. 成本对比

### 4.1 上游：官方 profile 就是硬证据

上游**自己发布了 16GB 档的部署 profile**，说明官方认为 8B + 持续 1 Hz 推理**不是单卡 16GB 能独自承担的**：

| 项 | 16GB profile | 24GB profile |
|---|---|---|
| 主模型 | INT4 AWQ G32 | INT4 AWQ G32 |
| 目标 GPU | **1 × 16GB 主 + 3 × 16GB（或 3 个 API）** | **1 × 24GB 主 + 3 × 24GB（或 3 个 API）** |
| `MAX_MODEL_LEN` | 32768 | 81920 |
| 主 GPU 占用率 | 0.95（物理 16GB 卡） | 0.95（物理 24GB 卡） |
| 摘要上下文 | 8192 tok @ 0.95 | 8192 tok @ 0.95 |
| ASR / TTS GPU 占用率 | 0.60 / 0.90 | 0.40 / 0.60 |
| 记忆 | 3 中期块 + 1 长期块 | 5 中期块 + 5 长期块 |
| `CHUNK` | 70 | 100 |
| `LIVE_VLM_PROCESS_INTERVAL` | **1.0** | — |

关键读法：
- **16GB 档也要 4 张卡**（主卡 + 摘要卡 + ASR 卡 + TTS 卡），且主卡占用率 0.95。**「单卡 16GB 跑持续 1 Hz 决策」在上游不是既定配置**。
- 摘要是**独立模型、独立卡**（`Qwen3-VL-4B-Instruct`，`SUMMARY_GPU=1`）——这正好对应论文 §4.3「压缩步骤异步、藏在主推理后面」。
- webinfer 默认端口/模型分配也印证多卡：adapter 8070、主模型 7060、摘要模型 8065，*"Default GPU assignment: summary model uses 0, main model uses 3."*
- 上游**没有公开**吞吐 / 显存 / tok-s 的量化数据；README 与论文只给 `Latency-<1s` 徽章、`vLLM-Inference` 徽章、以及「over two hours / sub-second」这一定性结论。**成本披露止于 profile 级别。**

### 4.2 上游对多路视频的态度：**未支持，且 issue 未答**

- 上游 issue **#50**《JoyAI-VL-Interaction 是否支持多路视频？》（作者 `chenqp`，state=OPEN，**0 条评论**，无 label、无 milestone）。即：**多路视频目前是开放未答问题，官方未确认支持**。
- 代码层面也印证：会话以单一 `session_id` 为单位（`x-streaming-session`），每会话一份帧上下文与记忆；WebUI 是「一个视频源 + 一个会话」，无多路复用结构。`AdapterConfig.main_backends` 只是**同一模型多后端路由**（按 `model` 标识分发），不是「多路视频并行」。

### 4.3 与本地实测对比

| 项 | 上游（16GB profile） | 本地 fork（实测） |
|---|---|---|
| 加速器 | 主卡 1×16GB **+ 摘要/ASR/TTS 各 1 卡**（共 4 卡） | **单卡 RTX 5060 Ti 16GB** |
| 模型格式 | INT4 AWQ G32（vLLM 原生量化） | IQ4_NL GGUF + mmproj F16（llama.cpp 分离式） |
| 显存占用 | 主卡 0.95 利用率（含 KV cache，`MAX_MODEL_LEN=32768`） | **9.3 GB**（已实测，含 mmproj） |
| 单步视觉编码 | AdaCodec：可预测帧 ≈16 tok；场景变化处全 ViT token | **768×576 单图 prompt eval 453 ms** |
| 解码吞吐 | 未公开 | **76.9 tok/s** |
| 决策频率 | **1 Hz 常驻** | 用户轮驱动；proactive **默认关**，开启后 **0.2 Hz（5 s）** |
| 摘要/记忆算力 | 独立 4B 模型 + 独立卡（异步） | 与主模型同卡争用（本地无第二张卡） |

**成本量级结论**：
- 上游的「1 Hz 持续决策」是**在 4 卡预算下**取得的能力（16GB 档即 64GB 总显存；或用 3 个云 API 顶掉后 3 张卡）。
- 本地单卡 16GB 若要复刻 1 Hz 常驻循环，单步预算是硬约束：prompt eval **453 ms** + decode（按 76.9 tok/s、决策输出通常 ≤1 句 ≈ 20-40 tok，即 **0.26-0.52 s**）⇒ 单轮 **≈0.7-1.0 s**。这与 1 s 节拍**几乎打平、零余量**；一旦同时跑摘要/记忆压缩或 ASR/TTS，就会直接掉帧。
- **这正是本地把 proactive 设为 5 s 且默认关闭、并把摘要降级为同卡/按需的技术原因**：本地不是「丢了循环」，而是**没有上游那份算力预算**。

---

## 5. 结论与对本地 #148 工单的判定

### 5.1 三选一裁决

| 选项 | 是否成立 | 理由 |
|---|---|---|
| **(a) 缺陷/退化** | **不成立**（作为主因） | 没有任何证据表明本地曾在某个 commit 里跑通过 1 Hz 决策循环后被改坏。本地 `git log` 中 live/proactive 相关工作是从零设计（`doc/specs/live-visual-cb.md` 记载 `a536ef3 → 80caf37 → d9736ee → 08ab0c4` 逐层落地，且 spec 明确写「默认关」）。`VideoProcessorTrack` 零构造点是**继承上游遗留 + 本地另建帧管线**的结果，不是删改所致。 |
| **(b) 从未实现（上游也不是持续决策）** | **上游层面不成立；本地文档层面部分成立** | 上游**确实是**持续每秒决策（代码 + 论文双证，见 §1）。但本地文档（`ARCHITECTURE.md` §1、`决策/VLM架构与模型组成.md`）写的「每秒自主决策」**描述的是上游架构而非本地当前行为**——这部分是真夸大。 |
| **(c) 有意收敛** | **成立（主判定）** | 本地 spec 有明文设计意图、有 env 门控、有「防乱开口 + 真机验证后开」的理由、并**保留了 1 fps 帧传输**。这是典型的有意识范围收敛，不是能力丢失。 |

**主判定：(c) 有意收敛；叠加「本地文档沿用上游叙述、未标注本地实际行为」的表述缺陷（(b) 味道）。**

### 5.2 对 #148 工单的判定

**立工单 → 成立。按「缺陷」立 → 不成立。**

建议把 #148 拆成三条，性质各不相同：

1. **【文档纠偏 · 应做】** `ARCHITECTURE.md` §1 与 `决策/VLM架构与模型组成.md` 中「每秒自主决策」的表述，**当前描述的是上游架构，不是本 fork 行为**。应在该处显式标注本地实际语义（用户轮驱动 + proactive 默认关、5 s）。这是本轮调研**最确定、最该先做**的一条——因为它直接导致后续所有关于「实时性」的讨论都建立在错误前提上。
2. **【能力对齐 · 需用户拍板，非 bug】** 若要向「上游式 1 Hz 常驻决策」对齐，需要的是**能力补齐与成本决策**，不是修 bug：
   - 技术上已有零件：1 fps 帧传输已存在、四态决策协议已存在、proactive 循环已存在（只是 5 s / 默认关）。
   - **真正缺的是算力预算**：本地单步 ≈0.7-1.0 s，与 1 s 节拍零余量。缩小间隔必须同时解决「谁让路」——摘要/记忆压缩、ASR/TTS 都要在**同一张 16GB 卡**上争资源，而上游是把它们放在**另外 3 张卡**上。
   - 因此建议的可行区间不是「直接改 5→1」，而是**先实测「proactive 间隔 = 2 s 且摘要降频」下的稳定上限**，再决定目标。
3. **【`VideoProcessorTrack` 遗留 · 清理或复用需决策】** 上游该类的职责（1 Hz 抽帧 + 每帧推理）在本 fork 已被 `screen_capture.js` + `live_mode.py` 取代，且本地它**零构造点**，走的是 `/v1/chat/completions` **自由文本、不解析 decision token** 的路径。这是死代码 / 半成品，应明确二选一：**删除**，或**接入并补 decision token 解析**。**不应维持现状**——它同时是「零构造点」和「文档声称每秒决策」之间那道裂缝的来源。

### 5.3 对「本地每秒一帧决策不存在」这一现象本身的一句话定性

> 它不是坏了，是**被换掉了**：上游把「何时说话」交给一个**每秒都在跑、由常驻 query 授权的推理循环**；本地把它改成**以用户轮为主、proactive 为可选补充（默认关、5 s）的稀疏触发**。上游的机制更贵（4 卡），本地的机制更省（1 卡），**两者都是有效设计**——但本地文档不该继续用上游的语言描述本地的行为。

---

## 6. 证据清单

### 6.1 上游仓库文件（`jd-opensource/JoyAI-VL-Interaction` @ `main`）

| 文件 | 关键证据 |
|---|---|
| `services/webui/src/joy_interaction_webui/video_processor.py` | `class VideoProcessorTrack`；类属性 `process_interval_seconds = 1.0`、`frames_per_batch = 1`；`recv()` 内 `need_conversion = (time_since_last >= interval_sec) or (self.frame_count == 1)` 闸门 → `vlm_service.process_frame(...)`；批量路径 `process_frame_batch(batch)` 与 `sub_interval = interval_sec / frames_per_batch` |
| `services/webui/src/joy_interaction_webui/server.py` | WS 控制面 `update_interval` / `update_frames_per_batch` 写 `VideoProcessorTrack.process_interval_seconds`；CLI `--process-interval`；env `LIVE_VLM_PROCESS_INTERVAL` / `LIVE_VLM_FRAMES_PER_BATCH`；`--prompt` 默认空、*"waits for user input"* |
| `services/webui/src/joy_interaction_webui/vlm_service.py` | `class VLMService`；`process_frame()` → `analyze_image()`；`analyze_images()` 批量；`_resolve_prompt_for_inference()`；`_processing_lock` 忙时 `"VLM busy, skipping frame"` |
| `services/webui/src/joy_interaction_webui/background_model.py` | `parse_delegation(text)` 解析 `</delegation>` / `<delegation>`；`handle_foreground_response` 在 foreground 文本含委派时起异步任务 |
| `services/webinfer/live_adapter.py` | `DEFAULT_SYSTEM_PROMPT_EN` / `DEFAULT_SYSTEM_PROMPT`（*"At every inference step you MUST choose exactly one of the following three actions"*、`</silence>` / `</response>` / `</delegation>`）；`DEFAULT_SYSTEM_PROMPT_NO_DELEGATION`；`normalize_model_output`；`extract_response_payload`；`AdapterConfig.force_silence_before_query = True`、`use_prompt_as_query = True`、`frame_seconds = 1.0`、`max_pixels = 262144`、`main_max_tokens = 128`、`chunk = 200`、`compress_every_n_chunks = 5`、`keep_qa_history = True`；`_update_query_state`（*"prompt persists until replaced"*）；`is_forced_silence` 分支 + 日志 `"(forced silence, inference skipped)"`；路由仅 `/health`、`/v1/models`、`/v1/chat/completions`、`/v1/streaming/reset`；唯一 `while True` 为 `_session_cleanup_loop`（`asyncio.sleep(300)`）；CLI `--force-silence-before-query` / `--no-force-silence-before-query` |
| `services/webinfer/README.md` | §Inference and Memory Flow 第 4-7 步（prompt 常驻 / `FORCE_SILENCE_BEFORE_QUERY=true` 默认 / 输出规范化为 `</silence>` 或 `</response>`）；§Common Pitfalls：*"By default, no query forces silence, which can look like 'the model is not talking'"*；默认 GPU 分配 *"summary model uses 0, main model uses 3"*；`FRAME_SECONDS=1.0`、`CHUNK=100`、`COMPRESS_EVERY_N_CHUNKS=5`、`MAIN_MAX_TOKENS=256` |
| `doc/architecture.md` | *"The system watches a live video stream continuously, decides on its own when to speak, stay silent, or delegate to a background agent, and responds in under a second when needed."*；决策 token 三态说明 |
| `doc/api.md` | §Output Normalization Rules 表（`</silence>` / `</response>`）；*"Silence when no question is provided: If the request contains no user question text, the API returns `</silence>` immediately."*；无 SSE |
| `container/16GB/README.md` | *"Target GPUs: 1 × 16GB main + 3 × 16GB / 3 APIs"*；INT4 AWQ G32；`MAX_MODEL_LEN=32768`；主卡利用率 0.95；ASR/TTS 0.60/0.90；`CHUNK=70` |
| `container/16GB/.env.example` | `LIVE_VLM_PROCESS_INTERVAL=1.0`、`LIVE_VLM_FRAMES_PER_BATCH=1`、`MAIN_GPU=0`/`SUMMARY_GPU=1`/`ASR_GPU=2`/`TTS_GPU=3`、`VLLM_IMAGE=vllm/vllm-openai:v0.22.0`、`MAIN_MAX_TOKENS=256` |
| `container/24GB/README.md` | *"Target GPUs: 1 × 24GB main + 3 × 24GB / 3 APIs"*；`MAX_MODEL_LEN=81920`；`CHUNK=100` |
| `README.md` | 徽章 `Latency-<1s`、`vLLM-Inference`；*"one decision the model makes on its own, every second: speak, stay silent, or delegate"*；vLLM-Omni Day-0 部署 recipe 链接 |
| issue `#50` | 《JoyAI-VL-Interaction 是否支持多路视频？》state=OPEN、0 评论、无 label —— 多路视频未答 |

### 6.2 论文 `JoyAI-VL-Interaction-Reportv1.pdf`（本地 21 页；与上游同名文件字节一致）

| 页码 | 引用要点 |
|---|---|
| p.1（Abstract） | *"The model makes the response decision internally, choosing each second to stay silent, respond, or delegate to a background model"*；*"It supports hours of continuous video with sub-second latency."* |
| p.2（§1） | *"make when to act a learned, per-second decision of the model itself"*；AdaCodec *"spends far fewer tokens on each predictable frame so the budget grows far more slowly over a long stream"*；Doubao 对比 *"firing a background request every few seconds"* 且 *"an event is not observed until the next poll"* |
| p.3（§1 图 1） | *"deciding at each step whether to respond, stay silent, or delegate"*；*"The system runs two concurrent loops, joined by the model's 'delegate' action"* |
| p.4（§2.1） | Doubao *"the server only caches incoming video frames and does not send them to the model, so the application must periodically fire an ExternalTextToLLM trigger to obtain any analysis"*；*"'Monitoring' is thus an external clock bolted onto a turn-based model"* |
| p.5（§2.4 / §3） | *"the decision of when to act lives inside our model and is taken every second, event-driven, so reaction is bounded by inference rather than by a user's turn or a trigger clock"*；*"at every second, takes one of three actions"*；图 2 AdaCodec（P-Tokenizer、256 visual tokens → ~16 visual tokens、*"~16× fewer tokens"*） |
| p.5-6（§3.1） | *"AdaCodec spends only about 16 tokens on each predictable frame and full ViT tokens only at scene changes… its heavy part scales with how much the scene changes rather than with frame count."* |
| p.6-7（§3.2） | *"at each one-second step the model takes one of three actions"*；*"We teach these three decisions at one-second granularity"*；per-second 标签 / silence as first-class label；delegation 双环编排 |
| p.8（§3.3） | 训练目标权重 w_silence=1 / w_repeated_silence=0.4 / w_response=1.5；GRPO 优化 per-second policy |
| p.9（§4.1 Ingestion） | *"A sampling module downsamples the stream at a fixed interval, 1 Hz by default and configurable per scenario, to balance temporal detail against real-time latency."*；RGB → JPEG → Base64 data-URL → OpenAI 兼容多模态请求 |
| p.9-10（§4.1） | *"These frames are fed to the model every second, so it always acts on what is happening now rather than on a delayed batch."*；*"at every second it takes one action, to speak, stay silent, or delegate"*；双环（实时环 + 异步环） |
| p.11（§4.3 / §4.4） | 三层记忆（T_s / T_m=M·T_s / T_l=L·M·T_s），*"up to roughly two hours of context"*；*"The consolidation steps run asynchronously, ahead of each boundary, so they hide behind mainline inference and never stall the real-time loop"*；前缀复用与 KV cache：*"at every subsequent step only the newly observed frames and the previous reply are computed"*；*"The system sustains over two hours of continuous video at sub-second end-to-end latency on standard vLLM."* |
| p.12（§5.1） | 评测用三层记忆 `T_s = 100 s, M = 5, L = 15`；离线视频经 RTSP + MediaMTX 模拟直播源 |
| p.13-14（§5.2） | Fall Detection：*"raises the alert at the instant of the fall, whereas Doubao reacts four to five seconds later"*，*"The lag is not incidental: it is the polling interval of an external trigger surfacing as latency on an event that allows no delay."* |
| p.19-21（§7.1 Appendix） | **逐秒决策数据样例**：`<0.0 seconds>…</silence>` → `<4.0 seconds>…</response> A physical confrontation is happening.` → `<10.0 seconds>…</silence>`；多轮闲聊样例（每 1 s 一个 `<N.0 seconds>` + image）；delegation 样例（`</response> Let me check… </delegation> Explain the structural engineering principles…`） |

### 6.3 本地仓库证据

| 文件 | 关键证据 |
|---|---|
| `ARCHITECTURE.md` §1 | *"核心模型 JoyAI-VL-8B **每秒自主决策**「说话 / 沉默 / 委派」"* ——**描述上游架构，非本地当前行为**（本轮判定的文档问题点） |
| `doc/specs/live-visual-cb.md` §2.4 / §3 层 2 | *"proactive 循环（live_mode.py）：LISTENING 态 + `proactive_speak_enabled=True` 时，每 `PROACTIVE_INTERVAL_S`（默认 5s）抽最新帧…**默认关闭**（env `LIVE_PROACTIVE_ENABLED`，防乱开口，真机验证后开）"*；env 清单 `LIVE_PROACTIVE_ENABLED`(false) / `LIVE_PROACTIVE_INTERVAL_S`(5) / `LIVE_FRAME_WINDOW`(6) |
| `doc/specs/live-visual-cb.md` §2.1 / §2.2 / §2.6 | *"前端 `screen_capture.js` 已推 1fps JPEG（frameSeq/interval 已实现）"*；`recent_frames` deque 默认 6（约 6 s 窗口）；*"视觉历史不膨胀对话：recent_frames 只注入当前轮（不进 history 持久化）"* |
| `doc/specs/live-visual-cb.md` §2.3 | 实现路径裁定（方案 B）：不走 `/v1/chat/completions`，理由之一是 *"forced-silence 短路使 proactive 空文本轮永不推理"* ——本地已识别上游强制静默语义并**有意识绕开** |
| `services/webui/src/joy_interaction_webui/live_proactive.py` | `_DEFAULT_PROACTIVE_INTERVAL_S: float = 5.0`；`proactive_enabled_from_env()` → `_env_flag("LIVE_PROACTIVE_ENABLED", default=False)`；模块 docstring：*"env default OFF -> no loop task, zero behavior change"* |
| `决策/VLM架构与模型组成.md` | 本地四态决策与模型组成 SSOT |
| `决策/交互模式与决策token规范.md` | 本地 decision token 规范（silence/response/delegate/not-for-me） |
| `doc/research/source-project-live-reference-2026-08-12.md` | 既有本地调研已独立得出同向结论：源项目决策 token 机制在 `live_adapter.py`；*"无 query → 强制 `</silence>`（`FORCE_SILENCE_BEFORE_QUERY=true`）"*；决策发生在**模型推理层** |
| git log | `7f7eb37` 修正 VRAM 错值（5.8GB→实测 9.3GB）；`01c22ec` 生产 live prompt 首次实测 + 决策链路侦察；`92ac14f` 上游增量侦察。**全历史中无「曾实现 1 Hz 循环后被移除」的痕迹**——支持「非退化」判定 |
| 本地实测（本项目已确认） | `VideoProcessorTrack` 走 `VLMService.process_frame` → `/v1/chat/completions` 自由文本、**不解析 decision token**，且 `services/webui/src/` 下**零构造点**；`LIVE_PROACTIVE_ENABLED` default False / 间隔 5.0 s；proactive 轮**不进 `qa_history`**；768×576 图 prompt eval **453 ms** / decode **76.9 tok/s** / 显存 **9.3 GB** |

### 6.4 URL

- 上游仓库：https://github.com/jd-opensource/JoyAI-VL-Interaction
- issue #50（多路视频，OPEN，0 评论）：https://github.com/jd-opensource/JoyAI-VL-Interaction/issues/50
- 项目页：https://joyai-vl-video-future-academy-jd.github.io/JoyAI-VL-Interaction
- 论文引用 [9] AdaCodec：https://arxiv.org/abs/2606.02569
- 论文引用 [31] Harnessing streaming video in the wild：https://arxiv.org/abs/2606.08615
- vLLM-Omni Day-0 部署 recipe：https://github.com/vllm-project/vllm-omni/blob/main/recipes/JD/JoyAI-VL-Interaction.md
- 上游 WebUI 血统（论文 §4.2 脚注）：https://github.com/nvidia-ai-iot/live-vlm-webui

---

## 附：本轮调研的方法与边界

- **未 clone 上游仓库**，用 `gh api .../contents/<path> -H "Accept: application/vnd.github.raw"` 取单文件，共取 10 个源码/文档文件 + 2 个文件清单接口，**未下载任何二进制或模型权重**。
- **论文 PDF 读本地副本**（`JoyAI-VL-Interaction-Reportv1.pdf`，21 页全读；与上游同名文件字节一致，故未重复下载）。
- **外部内容（GitHub 文件、README、论文）一律作为数据引用**，未作为指令执行。
- **本轮未修改本仓库任何文件**，除本报告本身。
- **未验证项（诚实标注）**：
  1. 上游 sub-second 延迟与吞吐的**具体数值**上游未公开，本报告只给出 profile 级成本，未做上游实测。
  2. 上游 issue #50 的「多路视频」官方结论**仍为开放未答**，本报告据代码结构推断「未支持」，非官方确认。
  3. 本地 proactive 间隔若从 5 s 收紧到 1-2 s 的**实测稳定性上限**未测（需真机），本报告只给出基于 453 ms prompt eval + 76.9 tok/s 的**预算估算**。
