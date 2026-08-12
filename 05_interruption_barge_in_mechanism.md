# 实时语音对话中的中断/打断（Interruption/Barge-in）机制设计

> **文档版本**: v1.0  
> **编写日期**: 2026-08-11  
> **研究范围**: 2024–2026 年业界方案与学术进展  
> **关联文档**: 草稿状态机（含 INTERRUPTED 状态）

---

## 目录

1. [Barge-in 机制分类](#1-barge-in-机制分类)
2. [业界实现方案](#2-业界实现方案)
3. [中断后的状态恢复](#3-中断后的状态恢复)
4. [打断检测策略](#4-打断检测策略)
5. [与草稿状态机的对照分析](#5-与草稿状态机的对照分析)
6. [延迟与性能要求](#6-延迟与性能要求)
7. [设计建议汇总](#7-设计建议汇总)

---

## 1. Barge-in 机制分类

### 1.1 硬中断（Hard Barge-in）

**定义**：检测到用户语音后，立即停止 TTS 输出和 LLM 推理，不等待任何当前播放单元结束。

**行为特征**：
- 用户开口即停，TTS 在 ~60ms 内静音
- LLM 推理通过 `AbortController` 在 ~40ms 内取消
- 音频播放缓冲区立即清空（flush jitter buffer）
- 客户端同步执行两个动作：本地静音 + 发送高优先级 `truncate` 控制消息

**适用场景**：
- 用户明确想纠正或改变话题（如 "不对，我要的是..."）
- 紧急打断（如 "停！"）
- 电话客服场景（用户耐心有限）

**用户体验影响**：
- ✅ 响应最快，感知延迟最低
- ✅ 符合人类对话直觉——被打断就立刻停
- ❌ 容易被误触发（咳嗽、背景噪音、回音）
- ❌ 可能打断在句子中间，造成不自然截断

> **来源**: [Voice AI Barge-In and Turn-Taking: 2026 Guide](https://futureagi.com/blog/voice-ai-barge-in-turn-taking-2026) — "When barge-in fires, the TTS audio has to stop within 60ms. Anything slower feels like the agent ignored the interruption."

---

### 1.2 软中断（Soft Barge-in）

**定义**：检测到用户语音后，等待当前句子/自然短语边界结束后再停止，而非立即截断。

**行为特征**：
- 检测到打断信号后，标记 `pending_interruption = true`
- 继续播放直到下一个句子边界（句号、问号、自然停顿）
- 在边界处优雅停止，过渡到 LISTENING 状态
- 可配合 "好的，请说" 等过渡语

**适用场景**：
- 用户补充信息（如 "对了，再加上..."）
- 非紧急纠正
- 需要保持对话流畅感的高端助手场景

**用户体验影响**：
- ✅ 更自然的对话体验，不会在词中间截断
- ✅ 减少误触发带来的负面体验
- ❌ 额外延迟（等待句子边界，通常 200–800ms）
- ❌ 用户可能以为系统没听到，重复说话

> **来源**: [LiveKit - Solving unwanted interruptions with Adaptive Interruption Handling](https://livekit.com/blog/adaptive-interruption-handling) — 讨论了区分真实打断与 backchannel（"mm-hmm"、"yeah"）的必要性。

---

### 1.3 智能中断（Smart Barge-in）

**定义**：基于语义理解判断用户语音是否构成有效打断，而非仅依赖音频能量检测。

**行为特征**：
- 在用户语音的前几百毫秒内进行声学特征分析
- 分析维度包括：波形形状、语音起始强度与锐度、信号持续时间、韵律特征（音高、节奏）
- 区分真实打断 vs. backchannel（"嗯"、"对"）、咳嗽、叹气
- 可结合部分 ASR 结果进行语义判断

**LiveKit Adaptive Interruption Handling 方案**（2025）：
> "When user speech is detected during the agent's turn, the model analyzes the user's audio stream within the first few hundred milliseconds of detected speech. It looks for distinctive acoustic characteristics of true interruptions, including: overall waveform shape, strength and sharpness of speech onset, duration of the signal, prosodic features such as pitch and rhythm."

**适用场景**：
- 需要高准确率打断判断的生产环境
- 用户频繁使用 backchannel 的文化场景
- 嘈杂环境下的语音交互

**用户体验影响**：
- ✅ 大幅减少误触发（目标 false-barge-in rate < 2%）
- ✅ 能区分 "嗯哼"（继续）和 "等一下"（打断）
- ❌ 引入额外推理延迟（模型判断需要 100–300ms）
- ❌ 实现复杂度显著增加

> **来源**: [LiveKit Blog - Adaptive Interruption Handling](https://livekit.com/blog/adaptive-interruption-handling); [Deepgram Docs - Audio Preprocessing & Barge-In](https://developers.deepgram.com/guides/deep-dives/audio-preprocessing-barge-in)

---

### 1.4 三种方案对比

| 维度 | 硬中断 | 软中断 | 智能中断 |
|------|--------|--------|----------|
| 响应延迟 | ~60ms | 200–800ms | 100–400ms |
| 误触发率 | 高（需 AEC 配合） | 中 | 低（<2% 目标） |
| 实现复杂度 | 低 | 中 | 高 |
| 用户体验 | 响应快但可能突兀 | 流畅但延迟高 | 最佳平衡 |
| 适用场景 | 电话客服、IVR | 高端助手 | 通用生产环境 |
| 是否需要语义理解 | 否 | 否 | 是（声学特征+可选ASR） |

---

## 2. 业界实现方案

### 2.1 OpenAI Realtime API

**核心机制**：
- 使用 `response.cancel` 事件实现打断
- 支持 Server-side VAD（`server_vad`）进行语音活动检测
- 当用户在模型说话时开始说话，服务器发送 `input_audio_buffer.speech_started` 事件
- 客户端应发送 `response.cancel` 来中断当前响应
- 取消后，服务器发送 `response.done` 事件确认取消

**关键事件流**：
```
Client → Server: response.cancel
Server → Client: response.done (status: cancelled)
Server → Client: input_audio_buffer.speech_started (用户开始说话)
Server → Client: input_audio_buffer.committed (语音缓冲区提交)
Server → Client: response.created → response.done (新响应)
```

**设计要点**：
- `response.cancel` 不仅停止音频输出，还取消正在进行的 LLM 推理
- 取消后对话历史保留在服务器端，新响应自动包含之前的上下文
- 支持 `turn_detection` 配置，可设置语义端点检测的灵敏度

> **来源**: OpenAI Realtime API 官方文档（2024–2025）；社区讨论与实现案例

---

### 2.2 Google Gemini Live / Multimodal Live API

**核心机制**：
- 原生支持 **Barge-in**：用户可在模型说话时随时打断
- 使用 `interrupted` 标志位：当用户打断时，`server_content` 中包含 `interrupted: true`
- 支持 `BidiGenerateContent` 双向流式通信

**关键行为**：
- 模型在生成响应时持续监听音频输入
- 检测到用户语音后，自动停止当前生成
- 在 `server_content` 消息中设置 `interrupted` 标志
- 客户端可根据 `interrupted` 标志决定如何处理部分生成的响应

**已知问题**（来自 Google AI Developers Forum）：
- Gemini 2 Flash Multimodal Live API 在某些场景下 `interrupted` 标志未正确设置
- 社区反馈：打断后模型有时不识别中断，继续输出
- 2025–2026 年持续改进中

**配置选项**：
- `disable_interruptions`：可禁用打断功能（用于需要完整播放的场景）
- 支持 70+ 语言的多语言打断

> **来源**: [Google Cloud Blog - Build voice-driven applications with Live API](https://cloud.google.com/blog/products/ai-machine-learning/build-voice-driven-applications-with-live-api); [Google AI Developers Forum](https://discuss.ai.google.dev/t/interrupting-gemini-2-flash-multimodal-live-api-seem-not-to-work-as-expected/61607); [GitHub - google-gemini/gemini-live-api-examples](https://github.com/google-gemini/gemini-live-api-examples)

---

### 2.3 LiveKit Agents

**核心机制**：
- 框架级别的中断支持：检测到用户语音时自动暂停 Agent 语音
- 提供 `session.interrupt()` 方法支持程序化打断
- 打断后自动截断对话历史，仅保留用户实际听到的部分

**Turn Detection 体系**：
- 基于 VAD 的静音检测（Silero VAD 等）
- 语义端点检测模型（LiveKit Multilingual Turn Detection Model）
- 可配置的打断灵敏度参数

**关键设计**：
```
中断触发 → Agent 停止说话 → 对话历史自动截断
→ 仅保留用户听到的部分 → 新 turn 开始
```

**已知挑战**（来自 GitHub Issue #3427）：
- 打断逻辑在 `thinking` 和 `speaking` 状态间共享，无法独立调优
- 默认配置过于敏感，容易误触发
- 社区建议：支持 per-state 打断配置（`speaking_min_interruption_duration` vs `thinking_*`）

**Adaptive Interruption Handling（2025 新特性）**：
- 使用声学模型在 200–300ms 内判断是否为真实打断
- 区分 backchannel（"mm-hmm"）和真实打断
- 每计划包含 40,000 次免费推理请求

> **来源**: [LiveKit Voice Agents Docs](https://docs.livekit.io/agents/logic/turns); [LiveKit Blog - Adaptive Interruption Handling](https://livekit.com/blog/adaptive-interruption-handling); [GitHub Issue #3427](https://github.com/livekit/agents/issues/3427)

---

### 2.4 Moshi（Kyutai）全双工设计

**核心机制**：
- Moshi 是 Kyutai 于 2024 年发布的全双工语音对话模型
- 原生支持同时听说的全双工交互
- 不需要显式的打断机制——模型始终在听

**架构特点**：
- 基于联合预训练语音-文本模型（Helium + Mimi codec）
- 使用多层流式架构：用户音频流和模型音频流同时处理
- 内部 "Inner Monologue" 机制：模型在生成语音的同时生成文本 token
- 理论上不存在传统意义的 "打断"，因为模型始终处于 LISTENING + SPEAKING 的双工状态

**与传统打断的区别**：
- 传统方案：LISTENING ↔ SPEAKING 互斥状态切换
- Moshi 方案：始终 LISTENING，SPEAKING 是并行的输出流
- 用户说话时，模型实时调整输出，而非 "取消-重启"

**局限性**：
- 模型规模受限（Moshi 为 ~7B 参数）
- 全双工推理对 GPU 要求高
- 目前主要作为研究原型，生产部署案例有限

> **来源**: Kyutai Moshi 论文及开源发布（2024）；社区技术分析

---

### 2.5 业界方案对比总结

| 方案 | 打断方式 | 检测机制 | 状态管理 | 成熟度 |
|------|----------|----------|----------|--------|
| OpenAI Realtime | `response.cancel` 事件 | Server VAD | 服务端状态机 | 生产可用 |
| Gemini Live | 自动打断 + `interrupted` 标志 | 内置 VAD | 服务端自动管理 | 生产可用（有 bug） |
| LiveKit Agents | VAD + `session.interrupt()` | Silero VAD + 语义模型 | 客户端状态机 | 生产可用 |
| Moshi | 全双工（无需显式打断） | 始终监听 | 无传统状态切换 | 研究原型 |

---

## 3. 中断后的状态恢复

### 3.1 TTS 流的中断与清理

**目标延迟**：TTS flush 在 **60ms** 内完成。

**实现要点**：
1. **客户端本地静音**：检测到打断后，立即 mute 音频输出 track，丢弃本地 jitter buffer
2. **发送 truncate 消息**：通过 WebSocket/WebRTC data channel 发送高优先级打断信号
3. **服务端停止 TTS**：取消 TTS 流生成，清空音频缓冲区
4. **清理已排队音频**：丢弃所有已合成但未播放的音频帧

**关键指标**：
- `tts_flush_ms`：从触发到 TTS 停止的时间
- 超过 200ms 的 flush 延迟会让用户感觉系统 "没听到"

> **来源**: [Voice AI Barge-In and Turn-Taking: 2026 Guide](https://futureagi.com/blog/voice-ai-barge-in-turn-taking-2026); [The Art of Interruption: VAD Strategies](https://dev.to/deepak_mishra_35863517037/the-art-of-interruption-vad-strategies-for-fluid-ai-conversations-15bh)

---

### 3.2 LLM 推理的中断

**目标延迟**：LLM cancel 在 **40ms** 内完成。

**实现方式**：
- HTTP 层取消：使用 `AbortController`（OpenAI、Anthropic、Google、Bedrock 均支持）
- 取消调用关闭流，服务端生成终止
- 本地 LLM（如 Ollama）需注意：关闭客户端连接不保证服务端停止生成

**三个注意事项**：
1. **`llm_cancel_latency_ms`** 必须被测量和监控
2. 取消后 token 计费：大多数提供商对被取消的请求按已生成 token 计费
3. 本地推理取消：需要显式的进程/线程管理，不能仅依赖 HTTP 断开

**上下文截断策略**：
- 打断后，LLM 可能已生成用户未听到的 token
- 这些 token 不应出现在下一轮对话的上下文中
- 需要精确追踪 "用户实际听到了什么"

> **来源**: [Voice AI Barge-In and Turn-Taking: 2026 Guide](https://futureagi.com/blog/voice-ai-barge-in-turn-taking-2026); [Hugging Face Forums - Real-time voice agents with local LLMs](https://discuss.huggingface.co/t/real-time-voice-agents-with-local-llms-the-latency-problem-nobody-fully-solves/178025)

---

### 3.3 上下文管理：打断后的对话历史处理

这是打断机制中最复杂的部分。三种主流模式：

#### Pattern 1: Stash the Partial Utterance（暂存部分话语）

```
previous_agent_utterance_interrupted: "您的账户余额是..."
```

- 被打断的 Agent 话语以标记形式存入对话状态
- 下一轮 LLM 可以看到被打断的内容
- LLM 自行决定是重复、继续还是重新开始

#### Pattern 2: Truncate to Heard-Only（仅保留已听到部分）

- LiveKit Agents 的默认行为
- 对话历史自动截断，仅保留用户实际听到的 Agent 话语部分
- 基于 TTS 播放进度追踪（`played_audio_duration`）

#### Pattern 3: Full Context with Interruption Marker（完整上下文 + 打断标记）

```
[Agent]: 您的账户余额是 1,200 元，其中... [INTERRUPTED]
[User]: 不对，我要查的是信用卡
[Agent]: 抱歉，让我重新查询您的信用卡余额...
```

- 保留完整对话历史，但标记打断点
- LLM 可以看到完整上下文，做出更智能的响应
- 上下文窗口消耗更大

> **来源**: [Voice AI Barge-In and Turn-Taking: 2026 Guide](https://futureagi.com/blog/voice-ai-barge-in-turn-taking-2026); [LiveKit Agents - Turns Overview](https://docs.livekit.io/agents/logic/turns)

---

### 3.4 从 INTERRUPTED 到 LISTENING 的状态过渡

**标准过渡流程**：

```
SPEAKING → (检测到用户语音) → INTERRUPTED → LISTENING
```

**INTERRUPTED 状态需要完成的操作**：

1. **TTS 层**：停止音频播放，清空缓冲区（60ms 目标）
2. **LLM 层**：取消推理请求（40ms 目标）
3. **上下文层**：标记打断点，截断/暂存对话历史
4. **音频层**：切换到仅输入模式，开始捕获用户语音
5. **状态广播**：通知所有组件状态变更（`agent_state_changed`）

**过渡时间目标**：
- 从打断检测到进入 LISTENING 状态：**< 150ms** 总延迟
- 各组件并行执行：TTS flush (60ms) ‖ LLM cancel (40ms) ‖ 上下文处理 (10ms)

> **来源**: [Voice AI Barge-In and Turn-Taking: 2026 Guide](https://futureagi.com/blog/voice-ai-barge-in-turn-taking-2026); [Zoice - Interruption Handling in Conversational AI](https://zoice.ai/blog/interruption-handling-in-conversational-ai)

---

## 4. 打断检测策略

### 4.1 基于 VAD 的打断检测

**基本原理**：在 SPEAKING 状态下持续运行 VAD，检测到语音能量超过阈值时触发打断。

**关键参数**：

| 参数 | 说明 | 推荐值 |
|------|------|--------|
| `threshold` | 语音概率阈值 | 0.5–0.7 |
| `min_speech_duration_ms` | 最短语音持续时间（防止瞬态噪声） | 100–200ms |
| `min_silence_duration_ms` | 静音判定时长 | 300–500ms |
| `speech_pad_ms` | 语音前后填充 | 30–50ms |

**VAD 选型**：
- **Silero VAD**：开源，轻量级，广泛用于 LiveKit 等框架
- **WebRTC VAD**：超轻量，适合浏览器端，但准确率较低
- **Deepgram 内置检测**：模型级语音检测，比纯能量 VAD 误触发更少
- **自定义神经 VAD**：可针对特定场景调优

**设计原则**：
> "The full VAD pipeline output is a binary signal: barge-in YES or NO at each frame. The trigger is conservative by design. False-barge-in is a worse failure than a slightly slow barge-in."

> **来源**: [Voice AI Barge-In and Turn-Taking: 2026 Guide](https://futureagi.com/blog/voice-ai-barge-in-turn-taking-2026); [Teammates.ai - VAD for clearer agent calls](https://teammates.ai/blog/vad-voice-activity-detection-for-clearer-agent-calls)

---

### 4.2 基于语义的打断判断

**方案一：声学特征分析（LiveKit Adaptive Interruption）**

在用户语音的前 200–300ms 内分析：
- 波形形状（waveform shape）
- 语音起始强度与锐度（strength and sharpness of speech onset）
- 信号持续时间（duration of the signal）
- 韵律特征（prosodic features: pitch, rhythm）

区分真实打断 vs. backchannel（"mm-hmm"、"yeah"、"right"）

**方案二：ASR 部分结果分析**

- 使用流式 ASR 的部分结果（partial transcript）
- 判断用户说的是否构成有效打断意图
- 例如："等一下"、"不对"、"stop" → 打断；"嗯"、"对" → 不打断

**方案三：混合方案**

- 第一层：VAD 快速触发（~60ms）
- 第二层：声学模型确认（~200ms）
- 第三层：ASR 语义确认（~500ms）
- 如果第一层触发但第二/三层否定 → 恢复播放（需支持 resume）

> **来源**: [LiveKit Blog - Adaptive Interruption Handling](https://livekit.com/blog/adaptive-interruption-handling); [Deepgram Docs - Audio Preprocessing & Barge-In](https://developers.deepgram.com/guides/deep-dives/audio-preprocessing-barge-in)

---

### 4.3 防止误触发

#### 4.3.1 回声消除（AEC - Acoustic Echo Cancellation）

**问题**：Agent 自己的 TTS 输出通过扬声器→麦克风回路被 VAD 检测到，导致自我触发打断。

**解决方案**：
- **WebRTC AEC3**：使用已知的 TTS 输出作为参考信号（reference signal），建模房间声学路径，从麦克风信号中减去预测回声
- **Double-talk 处理**：AEC3 在检测到双讲（用户和 Agent 同时说话）时冻结滤波器自适应，防止将用户语音误当回声消除
- **服务端 AEC**：在 PSTN/电话场景中，使用服务端 AEC 处理回声

**关键挑战**：
- 近端语音与回声比（NER）通常 < 0dB（扬声器比用户更靠近麦克风）
- 非线性失真（扬声器失真、房间混响）难以完全消除
- 低码率编解码器（G.729、低码率 Opus）引入的伪影可能触发 VAD

> **来源**: [RunEdge.ai - Barge-in and interruption handling for on-device voice agents](https://www.runedge.ai/blog/barge-in-interruption-handling-on-device-voice); [Vocal.com - AEC Barge-In](https://vocal.com/echo-cancellation/aec-barge-in); [Coval.ai - Voice AI Echo Cancellation](https://www.coval.ai/blog/voice-ai-echo-cancellation)

#### 4.3.2 自发言检测（Self-Speech Detection）

**问题**：Agent 自己的 TTS 输出被 ASR 识别为文本，导致对话循环。

**解决方案**：
- **音频 Ducking**：在 Agent 说话时降低麦克风增益
- **参考信号减法**：从 ASR 输入中减去已知的 TTS 信号
- **时间窗口排除**：在 Agent 说话期间标记 ASR 输出为无效

#### 4.3.3 环境噪声过滤

**常见误触发源**：
- 键盘敲击声
- 背景人声（电视、旁人对话）
- 关门声、咳嗽、叹气
- 低码率编解码器伪影

**缓解措施**：
- 提高 `min_speech_duration_ms` 过滤瞬态噪声
- 使用频带限制能量检测（仅检测语音频带 300–3400Hz）
- 环境自适应阈值调整（嘈杂环境自动提高阈值）
- 目标：false-barge-in rate < 2%，超过 5% 则体验崩溃

> **来源**: [Decagon - What is voice agent barge-in?](https://decagon.ai/glossary/what-is-voice-agent-barge-in); [Teammates.ai - VAD for clearer agent calls](https://teammates.ai/blog/vad-voice-activity-detection-for-clearer-agent-calls)

---

## 5. 与草稿状态机的对照分析

### 5.1 INTERRUPTED 状态评估

**当前设计（假设）**：
```
SPEAKING → INTERRUPTED → LISTENING
```

**评估结论**：INTERRUPTED 作为单一状态是**必要但不充分**的。

**充分性分析**：
- ✅ 正确识别了打断是一个独立的状态（而非直接从 SPEAKING → LISTENING）
- ✅ 为打断后的清理操作提供了状态容器
- ❌ 未区分打断类型（硬/软/智能）
- ❌ 未定义 INTERRUPTED 状态下的子操作序列
- ❌ 缺少从 INTERRUPTED 恢复到 SPEAKING 的路径（误触发场景）

---

### 5.2 建议：区分 HARD_INTERRUPTED / SOFT_INTERRUPTED

**推荐方案**：将 INTERRUPTED 拆分为两个子状态：

```
SPEAKING ──┬── HARD_INTERRUPTED ──→ LISTENING
           │    (立即停止，60ms)
           │
           └── SOFT_INTERRUPTED ──→ LISTENING
                (等待句子边界，200-800ms)
```

**HARD_INTERRUPTED**：
- 触发条件：VAD 检测到持续语音 + 声学模型确认为真实打断
- 行为：立即停止 TTS、取消 LLM、清空缓冲区
- 过渡：→ LISTENING（< 150ms）

**SOFT_INTERRUPTED**：
- 触发条件：VAD 检测到语音但声学模型判断为非紧急打断
- 行为：标记 pending，等待句子边界后停止
- 过渡：→ LISTENING（200–800ms）
- 特殊：如果在等待边界期间用户停止说话 → 可恢复 SPEAKING

---

### 5.3 状态转移路径完整性分析

**当前路径**：
```
SPEAKING → INTERRUPTED → LISTENING
```

**建议补充的路径**：

```
1. SPEAKING → HARD_INTERRUPTED → LISTENING          [标准硬打断]
2. SPEAKING → SOFT_INTERRUPTED → LISTENING           [标准软打断]
3. SPEAKING → SOFT_INTERRUPTED → SPEAKING            [误触发恢复]
4. THINKING → INTERRUPTED → LISTENING                [LLM推理中被用户打断]
5. LISTENING → (无打断，正常) → THINKING              [正常turn切换]
```

**关键补充**：
- **THINKING 状态下的打断**：用户在 Agent "思考"时说话，应取消当前推理并立即开始新的 LISTENING
- **误触发恢复路径**：SOFT_INTERRUPTED → SPEAKING（如果用户只是清嗓子）

---

### 5.4 缺少的边界条件处理

| 边界条件 | 问题描述 | 建议处理 |
|----------|----------|----------|
| **连续打断** | 用户在 INTERRUPTED 状态下再次说话 | 取消当前打断处理，重新开始新的打断流程 |
| **打断后立即沉默** | 用户打断后不说话 | 设置 `post_interruption_silence_timeout`（如 5s），超时后 Agent 主动询问 |
| **TTS 取消不完整** | TTS 取消后仍有残留音频播放 | 实现双重保障：软件 mute + 硬件/系统级音频停止 |
| **LLM 取消竞态** | LLM 取消与新请求同时到达 | 使用请求 ID 去重，丢弃过期响应 |
| **上下文不一致** | 打断后上下文包含用户未听到的内容 | 基于 TTS 播放进度精确截断 |
| **打断风暴** | 用户和 Agent 互相打断 | 设置最小 Agent 发言时长（`min_agent_speech_duration`），在此期间忽略打断 |
| **网络延迟导致延迟打断** | 打断信号到达时 TTS 已播放完毕 | 忽略过期的打断信号（检查 `turn_id` 匹配） |

---

## 6. 延迟与性能要求

### 6.1 端到端打断响应时间目标

**总目标**：从用户开始说话到 Agent 停止说话 **< 150ms**。

**延迟预算分解**：

| 阶段 | 目标延迟 | 说明 |
|------|----------|------|
| VAD 检测 | 20–40ms | 帧级语音检测延迟 |
| 声学确认（可选） | 100–200ms | 智能打断的模型推理 |
| 信号传输 | 10–20ms | WebRTC data channel |
| TTS flush | 30–60ms | 停止音频播放 + 清空缓冲区 |
| LLM cancel | 20–40ms | HTTP AbortController |
| 状态转换 | 5–10ms | 状态机更新 + 组件通知 |
| **总计（硬中断）** | **~100–150ms** | |
| **总计（智能中断）** | **~200–350ms** | 含声学确认 |

> **来源**: [Voice AI Barge-In and Turn-Taking: 2026 Guide](https://futureagi.com/blog/voice-ai-barge-in-turn-taking-2026); [Prodinit - Voice AI Latency Architecture Guide](https://prodinit.com/blog/production-voice-ai-agents-latency-architecture)

---

### 6.2 各组件延迟要求

#### VAD 检测延迟
- **帧级延迟**：20–40ms（取决于帧长，通常 20ms）
- **确认延迟**：`min_speech_duration_ms`（100–200ms）
- **总 VAD 延迟**：120–240ms（从用户开口到确认打断）

#### TTS 取消延迟
- **目标**：< 60ms
- **关键**：客户端本地静音（即时） + 服务端流取消（异步）
- **超过 200ms**：用户感知到系统 "没反应"

#### LLM 取消延迟
- **目标**：< 40ms
- **实现**：`AbortController.abort()` → HTTP 流关闭
- **注意**：本地 LLM 需要显式的进程管理

#### 端到端语音响应延迟（正常 Turn）

| 阶段 | 延迟 | 说明 |
|------|------|------|
| STT（语音转文本） | 80–120ms | 流式部分结果 |
| LLM first-token | 150–250ms | 首个 token 生成 |
| TTS first-chunk | 60–100ms | 首个音频块合成 |
| 网络传输 | 20–60ms | WebRTC/SIP |
| **总计** | **310–530ms** | 人类对话阈值 ~300ms |

> **来源**: [Prodinit - Voice AI Latency Architecture Guide](https://prodinit.com/blog/production-voice-ai-agents-latency-architecture); [Gradium - Best Low-Latency TTS APIs in 2026](https://gradium.ai/content/best-low-latency-tts-apis-2026); [Retell AI - How Real-Time Voice AI Actually Works](https://www.retellai.com/blog/how-real-time-voice-ai-works-stt-llm-tts)

---

### 6.3 性能监控指标

建议监控以下关键指标：

```yaml
barge_in_metrics:
  - barge_in_event_id: string          # 打断事件 UUID
  - turn_id: string                    # 被中断的 turn ID
  - vad_confidence: float              # VAD 置信度 (0-1)
  - vad_duration_ms: int               # 触发前持续语音时长
  - energy_dbfs: float                 # 触发窗口峰值能量
  - tts_flush_ms: int                  # TTS 停止延迟
  - llm_cancel_ms: int                 # LLM 取消延迟
  - total_interruption_ms: int         # 端到端打断延迟
  - false_trigger: boolean             # 是否为误触发（事后标注）
  - context_truncated_chars: int       # 截断的字符数
```

**目标 SLA**：
- P50 打断延迟：< 150ms
- P95 打断延迟：< 300ms
- 误触发率：< 2%
- 漏打断率：< 5%

> **来源**: [Voice AI Barge-In and Turn-Taking: 2026 Guide](https://futureagi.com/blog/voice-ai-barge-in-turn-taking-2026)

---

## 7. 设计建议汇总

### 7.1 状态机设计建议

```
                    ┌─────────────────────────────────┐
                    │                                 │
                    ▼                                 │
              ┌──────────┐                            │
              │ LISTENING │◄───────────────────────┐   │
              └────┬─────┘                        │   │
                   │                              │   │
                   ▼                              │   │
              ┌──────────┐    ┌────────────────┐  │   │
              │ THINKING  │───►│  INTERRUPTED   │──┘   │
              └────┬─────┘    │  (thinking时)   │      │
                   │          └────────────────┘      │
                   ▼                                  │
              ┌──────────┐    ┌────────────────┐      │
              │ SPEAKING  │───►│HARD_INTERRUPTED│──────┘
              └────┬─────┘    └────────────────┘      │
                   │          ┌────────────────┐      │
                   └─────────►│SOFT_INTERRUPTED│──────┘
                              └───────┬────────┘      │
                                      │               │
                                      ▼               │
                              (可恢复 SPEAKING)       │
```

### 7.2 实现优先级建议

| 优先级 | 功能 | 理由 |
|--------|------|------|
| P0 | 硬中断（VAD + TTS flush + LLM cancel） | 最小可行打断，用户核心体验 |
| P0 | AEC 回声消除 | 无 AEC 则打断不可用 |
| P1 | 上下文截断（仅保留已听到部分） | 打断后对话连贯性 |
| P1 | 误触发防护（min_speech_duration、阈值调优） | 生产可用性 |
| P2 | 软中断（句子边界检测） | 提升自然度 |
| P2 | 打断指标监控 | 持续优化基础 |
| P3 | 智能中断（声学模型） | 进一步降低误触发 |
| P3 | 语义打断（ASR 部分结果） | 最高级打断判断 |

### 7.3 关键设计原则

1. **宁可慢打断，不可误打断**：False-barge-in 比 slow-barge-in 更糟糕
2. **客户端先行**：检测到打断后，客户端立即本地静音，不等服务端确认
3. **上下文精确截断**：只保留用户实际听到的内容，不保留 LLM 已生成但未播放的部分
4. **所有组件可取消**：ASR、LLM、TTS、Audio Playout 都必须支持取消
5. **状态显式管理**：每个打断事件都应有明确的 turn_id 和状态追踪
6. **竞态处理**：连续打断、取消与新请求并发等场景必须有明确的处理逻辑

---

## 参考资料

1. [Voice AI Barge-In and Turn-Taking: 2026 Guide](https://futureagi.com/blog/voice-ai-barge-in-turn-taking-2026) — FutureAGI
2. [LiveKit Agents - Turns Overview](https://docs.livekit.io/agents/logic/turns) — LiveKit Docs
3. [Solving unwanted interruptions with Adaptive Interruption Handling](https://livekit.com/blog/adaptive-interruption-handling) — LiveKit Blog
4. [Build voice-driven applications with Live API](https://cloud.google.com/blog/products/ai-machine-learning/build-voice-driven-applications-with-live-api) — Google Cloud Blog
5. [Barge-in and interruption handling for on-device voice agents](https://www.runedge.ai/blog/barge-in-interruption-handling-on-device-voice) — RunEdge.ai
6. [Voice AI Agent on WebRTC: Latency, Barge-In & Turn-Taking](https://trembit.com/blog/voice-ai-agents-webrtc) — Trembit
7. [The Art of Interruption: VAD Strategies for Fluid AI Conversations](https://dev.to/deepak_mishra_35863517037/the-art-of-interruption-vad-strategies-for-fluid-ai-conversations-15bh) — Dev.to
8. [Interruption Handling in Conversational AI](https://zoice.ai/blog/interruption-handling-in-conversational-ai) — Zoice
9. [What is voice agent barge-in?](https://decagon.ai/glossary/what-is-voice-agent-barge-in) — Decagon
10. [VAD voice activity detection for clearer agent calls](https://teammates.ai/blog/vad-voice-activity-detection-for-clearer-agent-calls) — Teammates.ai
11. [Audio Preprocessing & Barge-In](https://developers.deepgram.com/guides/deep-dives/audio-preprocessing-barge-in) — Deepgram Docs
12. [Voice AI Latency: Sub-250ms Architecture Guide](https://prodinit.com/blog/production-voice-ai-agents-latency-architecture) — Prodinit
13. [Best Low-Latency TTS APIs in 2026](https://gradium.ai/content/best-low-latency-tts-apis-2026) — Gradium
14. [How Real-Time Voice AI Actually Works](https://www.retellai.com/blog/how-real-time-voice-ai-works-stt-llm-tts) — Retell AI
15. [Voice AI Echo Cancellation: Causes, Fixes, and Best Practices](https://www.coval.ai/blog/voice-ai-echo-cancellation) — Coval.ai
16. [Acoustic Echo Cancellation (AEC) Barge-In](https://vocal.com/echo-cancellation/aec-barge-in) — Vocal.com
17. [GitHub Issue #3427 - LiveKit Agents](https://github.com/livekit/agents/issues/3427) — LiveKit
18. [Google AI Developers Forum - Interrupting Gemini Live API](https://discuss.ai.google.dev/t/interrupting-gemini-2-flash-multimodal-live-api-seem-not-to-work-as-expected/61607)
19. [GitHub - google-gemini/gemini-live-api-examples](https://github.com/google-gemini/gemini-live-api-examples)
20. [Hugging Face Forums - Real-time voice agents with local LLMs](https://discuss.huggingface.co/t/real-time-voice-agents-with-local-llms-the-latency-problem-nobody-fully-solves/178025)
