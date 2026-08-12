# 业界最先进的实时语音对话 Turn Controller 架构与状态机设计

> **研究日期**: 2026-08-11
> **研究范围**: 2024-2026 年业界主流实时语音 AI 平台的 Turn Controller 架构
> **目的**: 为统一 Turn Controller 设计提供参考基准

---

## 目录

1. [业界主流 Turn Controller 架构](#1-业界主流-turn-controller-架构)
   - 1.1 [OpenAI Realtime API](#11-openai-realtime-api)
   - 1.2 [Google Gemini Live / Multimodal Live API](#12-google-gemini-live--multimodal-live-api)
   - 1.3 [Microsoft Azure Speech SDK / DialogServiceConnector](#13-microsoft-azure-speech-sdk--dialogserviceconnector)
   - 1.4 [ElevenLabs Conversational AI](#14-elevenlabs-conversational-ai)
   - 1.5 [Deepgram Voice Agent API](#15-deepgram-voice-agent-api)
   - 1.6 [Moshi (Kyutai) — 全双工模型](#16-moshi-kyutai--全双工模型)
   - 1.7 [GLM-4-Voice (智谱) — 端到端语音模型](#17-glm-4-voice-智谱--端到端语音模型)
   - 1.8 [LiveKit Agents](#18-livekit-agents)
   - 1.9 [Pipecat (Daily.co)](#19-pipecat-dailyco)
2. [状态机设计对比](#2-状态机设计对比)
3. [与草稿的对照分析](#3-与草稿的对照分析)
4. [架构模式分析](#4-架构模式分析)
5. [推荐架构与设计建议](#5-推荐架构与设计建议)

---

## 1. 业界主流 Turn Controller 架构

### 1.1 OpenAI Realtime API

**来源**: [OpenAI Realtime API 官方文档](https://developers.openai.com/api/docs/guides/realtime-vad), [OpenAI Realtime API: The Missing Manual (Latent Space)](https://www.latent.space/p/realtime-api), [openai-realtime-api GitHub](https://github.com/transitive-bullshit/openai-realtime-api)

#### 架构概述

OpenAI Realtime API 采用 **事件驱动 + 服务端 VAD** 的架构。核心设计理念是将 Turn Detection 下沉到服务端，客户端只需持续推送音频流，服务端自动判断用户何时开始/停止说话。

#### Turn Detection 模式

OpenAI 支持三种 Turn Detection 模式：

| 模式 | 描述 | 状态 |
|------|------|------|
| `server_vad` | 基于静音检测的 VAD（默认），通过 `silence_duration_ms` 判断用户说完 | 生产可用 |
| `semantic_vad` | 基于语义的 VAD，使用 LLM 判断语义完整性，支持 `eagerness` 参数（low/medium/high） | 2025年新增 |
| `none` / `null` | 关闭自动 Turn Detection，由客户端手动管理 | 需手动发送 `input_audio_buffer.commit` + `response.create` |

#### 关键配置参数

```json
{
  "turn_detection": {
    "type": "server_vad",
    "threshold": 0.5,
    "prefix_padding_ms": 300,
    "silence_duration_ms": 500,
    "create_response": true
  }
}
```

- **`silence_duration_ms`**: 用户停止说话后等待的静音时长（默认 500ms），之后触发 `speech_stopped`
- **`prefix_padding_ms`**: 在检测到语音开始前保留的音频前缀（默认 300ms）
- **`create_response`**: 是否在 turn 结束时自动创建 response

#### 事件驱动的隐式状态机

OpenAI Realtime API 没有显式定义状态机，而是通过 **事件序列** 隐式表达状态转换：

```
[客户端持续发送 audio]
        │
        ▼
input_audio_buffer.speech_started    ← 用户开始说话（VAD 检测到语音）
        │
        ▼
input_audio_buffer.speech_stopped    ← 用户停止说话（静音超过 silence_duration_ms）
        │
        ▼
input_audio_buffer.committed         ← 音频缓冲区自动提交
        │
        ▼
response.created                     ← 模型开始生成响应
        │
        ▼
response.output_audio.delta          ← 流式音频输出（可被中断）
        │
        ▼
response.done                        ← 响应完成
```

#### Barge-in / Interruption 处理

- **自动中断**: 当用户在 Agent 说话期间开始说话，服务端自动：
  1. 发送 `input_audio_buffer.speech_started` 事件
  2. 立即停止当前音频输出
  3. 丢弃未播放的音频缓冲区
  4. 用户语音成为新的活跃 turn
- **无需客户端代码**: 中断逻辑完全由服务端处理
- **可配置**: `session.update` 中可配置中断行为

#### 关键事件类型（完整列表）

**客户端 → 服务端**:
- `input_audio_buffer.append` — 推送音频帧
- `input_audio_buffer.commit` — 手动提交音频缓冲区（VAD 关闭时）
- `input_audio_buffer.clear` — 清空音频缓冲区
- `response.create` — 手动触发响应生成
- `response.cancel` — 取消正在进行的响应

**服务端 → 客户端**:
- `input_audio_buffer.speech_started` — 检测到语音开始
- `input_audio_buffer.speech_stopped` — 检测到语音结束
- `input_audio_buffer.committed` — 缓冲区已提交
- `response.created` — 响应已创建
- `response.output_audio.delta` — 音频输出增量
- `response.output_audio.done` — 音频输出完成
- `response.done` — 响应完全完成

---

### 1.2 Google Gemini Live / Multimodal Live API

**来源**: [Pipecat Gemini Live 文档](https://docs.pipecat.ai/api-reference/server/services/s2s/gemini-live), [Google AI Studio](https://aistudio.google.com/)

#### 架构概述

Gemini Live API 通过 `BidiGenerateContent` 双向流接口实现实时语音对话。与 OpenAI 类似，采用 **服务端 VAD + 事件驱动** 模式。

#### 关键特征

- **Multimodal Processing**: 同时处理音频、视频和文本输入
- **Real-time Streaming**: 低延迟音频和视频处理
- **Voice Activity Detection**: 自动语音检测和 Turn 管理
- **Function Calling**: 支持工具调用
- **Context Management**: 智能对话历史和系统指令处理

#### Turn-Taking 机制

Gemini Live 的 Turn-Taking 通过以下机制实现：

1. **服务端 VAD**: 自动检测用户语音起止
2. **可配置的 VAD 参数**: 通过 Pipecat 等框架配置本地 VAD 来驱动 turn-taking（因为 Gemini 的服务端 VAD 在某些场景下被禁用）
3. **Modality 切换**: 支持 `AUDIO` 和 `TEXT` 两种响应模态

#### 与 Pipecat 集成时的 Turn 管理

在 Pipecat 框架中，Gemini Live 的 turn-taking 通过以下方式实现：

```python
# 配置本地 VAD 来驱动 turn-taking（服务端 VAD 被禁用时）
user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
    context,
    realtime_service_mode=True  # 保持上下文写入正确
)
```

这表明 Gemini Live 在实践中常需要 **客户端侧 VAD** 配合使用，形成 **Hybrid VAD** 模式。

---

### 1.3 Microsoft Azure Speech SDK / DialogServiceConnector

**来源**: [Azure Speech SDK Release Notes](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/releasenotes), [Azure Voice Live API](https://techcommunity.microsoft.com/blog/healthcareandlifesciencesblog/configuring-noise-detection-and-barge%E2%80%91in-with-azure-voice-live-api/4506916), [Azure Communication Services Q&A](https://learn.microsoft.com/en-us/answers/questions/2262358/)

#### DialogServiceConnector 架构

Azure 的 `DialogServiceConnector` 是一个与 Bot Framework / Custom Commands 通信的高级抽象，其 Turn 管理通过以下 API 实现：

| API | 功能 |
|-----|------|
| `ListenOnceAsync()` | 开始单次监听 |
| `StopListeningAsync()` | 立即停止音频捕获并优雅等待结果 |
| `TurnStatusReceived` 事件 | 报告每个 `ITurnContext` 的执行状态（成功/失败/超时/网络断开） |
| `speech_start_detected` 事件 | 检测到语音开始 |
| `speech_end_detected` 事件 | 检测到语音结束 |

#### Azure Voice Live API（新一代）

Azure 在 2025 年推出了 Voice Live API，采用与 OpenAI Realtime API 类似的 WebSocket 事件驱动架构：

```json
{
  "type": "session.update",
  "session": {
    "modalities": ["text", "audio"],
    "turn_detection": {
      "type": "server_vad",
      "threshold": 0.5,
      "prefix_padding_ms": 300,
      "silence_duration_ms": 500,
      "create_response": true,
      "interrupt_response": true
    }
  }
}
```

关键特性：
- **`interrupt_response: true`**: 启用 barge-in，用户说话时立即停止 Agent 音频
- **噪声检测**: 自动过滤背景噪声
- **无需自定义中断逻辑**: 应用只需响应 `speech_started` 事件

#### Barge-in 支持

Azure Communication Services 的 `CallAutomation` SDK 目前 **不原生支持 barge-in**，需要通过以下 workaround：
- 同时运行 `play_media()` 和 `start_recognizing_media()` 
- 在检测到用户语音时手动停止播放

---

### 1.4 ElevenLabs Conversational AI

**来源**: [ElevenLabs Conversation Flow 文档](https://elevenlabs.io/docs/eleven-agents/customization/conversation-flow), [ElevenLabs WebSocket API](https://elevenlabs.io/docs/eleven-agents/api-reference/eleven-agents/websocket), [ElevenLabs Interaction Models](https://elevenlabs.io/blog/interaction-models)

#### 架构概述

ElevenLabs 采用 **专有 Turn-Taking 模型** 作为其核心差异化能力。该模型是研究驱动的系统，能够检测说话者是否真正说完（而非仅仅暂停）。

#### 四大核心组件

```
┌──────────────────────────────────────────────────┐
│                 ElevenAgents                       │
│                                                    │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐      │
│  │   ASR    │   │   LLM    │   │   TTS    │      │
│  │(fine-tuned)│  │(可替换)  │   │(5k+ voices)│    │
│  └──────────┘   └──────────┘   └──────────┘      │
│                                                    │
│  ┌──────────────────────────────────────────┐     │
│  │     Proprietary Turn-Taking Model         │     │
│  │  (研究驱动的对话时机判断)                   │     │
│  └──────────────────────────────────────────┘     │
└──────────────────────────────────────────────────┘
```

#### Conversation Flow 配置

| 配置项 | 描述 |
|--------|------|
| **Turn Eagerness** | eager / normal / patient — 控制 Agent 插话的积极程度 |
| **Silence Timeout** | 用户静音后等待多久再提示 |
| **Soft Timeout** | 当 Agent 需要思考时提供自然音频反馈 |
| **Interruptions** | 可启用/禁用。禁用适用于法律免责声明等场景 |

#### WebSocket 事件

**客户端 → 服务端**:
- `user_audio_append` — 推送用户音频
- `context_update` — 非中断性上下文更新
- `tool_response` — 工具调用结果

**服务端 → 客户端**:
- `agent_audio_delta` — Agent 音频输出
- `user_transcript` — 用户语音实时转录
- `agent_transcript` — Agent 语音转录
- `interruption` — 中断事件
- `turn_start` / `turn_end` — Turn 边界事件

#### Turn-Taking 模型特点

- **研究驱动**: 基于对话语言学线索判断说话结束，而非固定静音阈值
- **不暴露调优参数**: Turn-Taking 模型内部参数不对外开放
- **条件中断**: 需要自定义逻辑（如忽略肯定性回应、对纠正性语句硬中断）时需在客户端实现

---

### 1.5 Deepgram Voice Agent API

**来源**: [Deepgram Voice Agent API](https://deepgram.com/product/voice-agent-api), [Inside Deepgram's Voice Agent API](https://deepgram.com/learn/voice-agent-api-generally-available), [Deepgram vs ElevenLabs Turn-Taking](https://deepgram.com/learn/elevenlabs-barge-in-interruptions-turn-taking)

#### 架构概述

Deepgram 的 Voice Agent API 采用 **统一运行时（Unified Runtime）** 架构，将 STT、LLM 编排和 TTS 整合到单一 API 中。Turn-Taking 和中断控制内置于运行时，而非通过客户端启发式规则外挂。

#### 核心设计原则

```
┌─────────────────────────────────────────────┐
│         Deepgram Voice Agent API             │
│  (单一 WebSocket 连接)                        │
│                                              │
│  ┌──────────────────────────────────────┐   │
│  │        Unified Runtime                │   │
│  │                                       │   │
│  │  Nova-3 (STT) → LLM → Aura-2 (TTS)  │   │
│  │         ↑  Turn-Taking 内嵌  ↑        │   │
│  │         ↑  Barge-in 内嵌    ↑        │   │
│  └──────────────────────────────────────┘   │
└─────────────────────────────────────────────┘
```

#### 关键特性

- **Model-driven Turn-Taking**: Turn-Taking 由模型驱动，而非固定规则
- **Flux 模型**: 首个专为处理中断设计的 Conversational Speech Recognition 模型
- **增量转录**: Nova-3 在用户说话期间增量组装转录结果，一旦检测到可能的语句边界就发送给 LLM
- **流式合成**: Aura-2 不等待标点或固定静音阈值即开始合成
- **中断恢复**: 支持 mid-stream 中断，避免昂贵的流重置

#### Turn-Taking 架构优势

1. **同一流式循环内处理**: Turn-Taking 和中断控制与转录/响应生成在同一流式循环中
2. **减少延迟**: 不需要跨服务协调
3. **内置并发处理**: 运行时原生支持并发

---

### 1.6 Moshi (Kyutai) — 全双工模型

**来源**: [Moshi 论文 (Kyutai)](https://kyutai.org/Moshi.pdf), [Moshi GitHub](https://github.com/kyutai-labs/moshi), [MarkTechPost 分析](https://www.marktechpost.com/2024/09/18/kyutai-open-sources-moshi/)

#### 架构概述

Moshi 是 **首个全双工（Full-Duplex）语音对话基础模型**，从根本上颠覆了传统的 Turn-Based 架构。它不需要显式的 Turn Controller，因为模型本身支持同时听和说。

#### 核心创新

```
┌──────────────────────────────────────────────────┐
│                  Moshi 架构                        │
│                                                    │
│  ┌──────────────────────────────────────────┐     │
│  │         Temporal Transformer (7B)         │     │
│  │         (时间维度建模, 12.5Hz)             │     │
│  │                                           │     │
│  │  ┌─────────┐ ┌──────────┐ ┌──────────┐  │     │
│  │  │  Text   │ │  Moshi   │ │   User   │  │     │
│  │  │ Tokens  │ │  Audio   │ │   Audio  │  │     │
│  │  │ (1层)   │ │ (8层)    │ │  (8层)   │  │     │
│  │  └─────────┘ └──────────┘ └──────────┘  │     │
│  │                                           │     │
│  │  + Depth Transformer (跨 codebook)        │     │
│  │  + Inner Monologue (文本-语音对齐)         │     │
│  └──────────────────────────────────────────┘     │
│                                                    │
│  理论延迟: 160ms | 实际延迟: ~200ms                 │
└──────────────────────────────────────────────────┘
```

#### Inner Monologue 机制

Moshi 的 "Inner Monologue" 是其最关键的创新：

- **时间对齐的文本 Token**: 在每个时间步，文本 token 作为前缀添加到语义 codebook
- **层次化预测**: Text → Semantic Tokens → Acoustic Tokens
- **效果**: 将 NLL 从 4.36 降至 2.77，最大转录长度从 486 扩展到 1920 字符

#### 对 Turn Controller 设计的启示

Moshi 证明了 **全双工模型不需要传统 Turn Controller**。但这对我们的设计有以下启示：

1. **传统级联架构仍需要 Turn Controller**: Moshi 是端到端模型，而大多数生产系统仍使用 ASR→LLM→TTS 级联
2. **Inner Monologue 概念可借鉴**: 在 Turn Controller 中引入"内心独白"状态——Agent 在组织语言时可以有内部状态
3. **双流建模**: 同时建模用户音频流和 Agent 音频流，实现更自然的 turn 切换

---

### 1.7 GLM-4-Voice (智谱) — 端到端语音模型

**来源**: [GLM-4-Voice GitHub](https://github.com/THUDM/GLM-4-Voice), [GLM-4-Voice 论文 (arXiv:2412.02612)](https://arxiv.org/pdf/2412.02612)

#### 架构概述

GLM-4-Voice 是智谱 AI 推出的端到端语音模型，基于 GLM-4-9B，支持中英文实时语音对话。

#### 三组件架构

```
┌─────────────────────────────────────────────┐
│             GLM-4-Voice                       │
│                                               │
│  ┌─────────────┐  ┌──────────────┐           │
│  │  Speech      │  │   GLM-4-9B   │           │
│  │  Tokenizer   │→│   (Base LM)  │           │
│  │  (175bps,    │  │              │           │
│  │   12.5Hz)    │  └──────────────┘           │
│  └─────────────┘         │                    │
│                           ▼                    │
│                    ┌──────────────┐           │
│                    │   Speech      │           │
│                    │   Decoder     │           │
│                    │ (CosyVoice,   │           │
│                    │  流式推理)    │           │
│                    └──────────────┘           │
└─────────────────────────────────────────────┘
```

#### Turn Control 特点

- **流式推理**: 仅需 10 个音频 token 即可开始语音合成，最小化对话延迟
- **截断音频训练**: 在微调阶段使用截断音频样本（前 n·b 秒），使模型适应流式场景
- **Decoupled Task**: 将语音到语音任务解耦为两个子任务，使用 streaming thoughts 模板降低延迟
- **情感/语调/语速/方言控制**: 支持根据用户指令变化

#### 对 Turn Controller 的启示

GLM-4-Voice 作为端到端模型，其 Turn Control 内嵌于模型推理过程中。但它的 **流式推理设计**（10 token 即可开始输出）对级联架构的 Turn Controller 有重要参考价值——意味着 TTS 阶段可以极早启动。

---

### 1.8 LiveKit Agents

**来源**: [LiveKit Agents 文档](https://livekit.com/voice-agents), [LiveKit Agents Python Examples](https://github.com/livekit-examples/python-agents-examples), [LiveKit Agent Starter](https://github.com/livekit-examples/agent-starter-python)

#### 架构概述

LiveKit Agents 是一个开源框架，提供完整的 STT → LLM → TTS 语音管道。其 Turn 管理通过 **Voice Pipeline Agent** 实现。

#### Voice Pipeline 架构

```
┌──────────────────────────────────────────────┐
│           LiveKit Voice Pipeline              │
│                                               │
│  ┌────────┐   ┌────────┐   ┌────────┐       │
│  │  STT   │──→│  LLM   │──→│  TTS   │       │
│  │ (多提供商)│  │ (多提供商)│  │ (多提供商)│      │
│  └────────┘   └────────┘   └────────┘       │
│       │                           │           │
│       └──── VAD / Turn ──────────┘           │
│                                               │
│  Agent State: LISTENING → THINKING → SPEAKING │
└──────────────────────────────────────────────┘
```

#### Agent State 模型

LiveKit Agents 框架隐含地使用三状态模型：

| 状态 | 描述 |
|------|------|
| **LISTENING** | Agent 正在监听用户语音输入 |
| **THINKING** | STT 完成，LLM 正在生成响应 |
| **SPEAKING** | TTS 正在播放 Agent 响应 |

#### 关键特性

- **Interruption 支持**: 用户在 Agent 说话时可以打断
- **Uninterruptable 模式**: 支持配置 Agent 完成响应而不被中断
- **多提供商**: 支持 OpenAI、Cartesia、Deepgram 等 50+ 模型提供商
- **WebRTC 传输**: 基于 WebRTC 实现低延迟音频传输
- **Tool Calling**: 支持函数调用

---

### 1.9 Pipecat (Daily.co)

**来源**: [Pipecat 文档](https://docs.pipecat.ai/), [Pipecat Flows](https://docs.pipecat.ai/pipecat-flows/introduction), [Building Voice Agents with Pipecat (AWS)](https://aws.amazon.com/blogs/machine-learning/building-intelligent-ai-voice-agents-with-pipecat-and-amazon-bedrock-part-1), [Advice on Building Voice AI (Daily.co)](https://www.daily.co/blog/advice-on-building-voice-ai-in-june-2025)

#### 架构概述

Pipecat 是 Daily.co 开发的开源 Python 框架，是 **目前最广泛使用的实时语音 AI 编排框架**（v1.0.0 已发布）。其核心概念是 **Pipeline**: 一系列 Processor（STT、LLM、TTS、自定义 Frame Handler）通过音频帧流式连接。

#### Pipecat Flows — 显式状态机

Pipecat Flows 是框架的状态机层，提供 **结构化对话图**：

```
┌────────────────────────────────────────────┐
│            Pipecat Flows                    │
│                                             │
│  ┌──────────┐    ┌──────────┐              │
│  │  Intent   │───→│   Date   │              │
│  │  Capture  │    │ Selection│              │
│  └──────────┘    └──────────┘              │
│       │                │                    │
│       ▼                ▼                    │
│  ┌──────────┐    ┌──────────┐              │
│  │   Time   │───→│ Confirm  │──→ Complete  │
│  │ Selection│    │          │              │
│  └──────────┘    └──────────┘              │
│                                             │
│  每个 Node 有:                               │
│  - System Instruction                       │
│  - Context Transformation                   │
│  - Tool Calls                               │
│  - Next States                              │
└────────────────────────────────────────────┘
```

#### Smart Turn 模型

Pipecat 生态中的 **Smart Turn** 是一个开源的音频 Turn Detection 模型：
- **开源**: 开放数据、开放训练代码
- **性能**: 据称优于所有专有 Turn Detection 模型
- **定位**: 解决 "2025 问题" — 更好的 Turn Detection

#### Turn Detection 参数（以 AssemblyAI 为例）

```python
stt = AssemblyAISTTService(
    connection_params=AssemblyAIConnectionParams(
        end_of_turn_confidence_threshold=0.7,
        min_end_of_turn_silence_when_confident=300,  # ms
        max_turn_silence=1000,  # ms
    )
)
```

#### VAD 状态机（参考实现）

来自 [Building Enterprise Realtime Voice Agents from Scratch](https://arxiv.org/html/2603.05413v1) 的流式 VAD 状态机：

```
SILENCE ──(energy > threshold)──→ SPEAKING
SPEAKING ──(silence > N frames)──→ SILENCE
```

---

## 2. 状态机设计对比

### 2.1 各方案状态定义汇总

| 平台/框架 | 显式状态机 | 状态列表 | Barge-in | User/Agent Turn 区分 | Pending/Queue |
|-----------|-----------|----------|----------|----------------------|---------------|
| **OpenAI Realtime** | 隐式（事件驱动） | speech_started → speech_stopped → response_created → response_done | ✅ 自动 | ✅ 隐式（事件类型区分） | ❌ 无显式队列 |
| **Gemini Live** | 隐式（事件驱动） | 类似 OpenAI 事件模型 | ✅ | ✅ 隐式 | ❌ |
| **Azure DialogService** | 半显式 | ListenOnce → Recognizing → Speaking → TurnStatus | ⚠️ 需手动实现 | ✅ 显式 API | ❌ |
| **Azure Voice Live** | 隐式（事件驱动） | 兼容 OpenAI 事件模型 | ✅ `interrupt_response: true` | ✅ | ❌ |
| **ElevenLabs** | 专有模型 | turn_start → agent_speaking → (interruption) → turn_end | ✅ 可配置 | ✅ 专有 Turn-Taking 模型 | ❌ |
| **Deepgram** | 运行时内嵌 | 模型驱动的 Turn-Taking | ✅ 内置 | ✅ 运行时管理 | ❌ |
| **Moshi** | 无（全双工） | 同时听和说，无 turn 概念 | N/A（全双工） | N/A | N/A |
| **GLM-4-Voice** | 无（端到端） | 流式推理，10 token 即可输出 | N/A（端到端） | N/A | N/A |
| **LiveKit Agents** | 三状态 | LISTENING → THINKING → SPEAKING | ✅ | ✅ | ❌ |
| **Pipecat** | Flows 状态图 | 自定义 Node 图 + VAD 状态机 | ✅ | ✅ | ✅ Flows 支持 |

### 2.2 状态转移条件对比

#### OpenAI Realtime API 隐式状态转移

```
[任意状态] ──(speech_started)──→ [用户说话中]
[用户说话中] ──(silence_duration_ms 超时)──→ [speech_stopped]
[speech_stopped] ──(自动 commit)──→ [response_created]
[response_created] ──(流式输出)──→ [response.done]
[response 输出中] ──(speech_started)──→ [中断，回到用户说话中]
```

#### LiveKit Agents 三状态转移

```
LISTENING ──(STT 完成)──→ THINKING ──(LLM 完成)──→ SPEAKING ──(TTS 完成)──→ LISTENING
    ↑                        ↑                        │
    │                        │                        │
    └──────(barge-in)────────┴──────(barge-in)────────┘
```

#### 社区最佳实践状态机（来自 dev.to）

```javascript
// 来源: Voice Agent Turn-Taking (dev.to)
function transition(state, event) {
  if (state === 'LISTENING' && event.type === 'speech_started')
    return event.confidence > 0.65 ? 'USER_SPEAKING' : 'LISTENING';
  if (state === 'USER_SPEAKING' && event.type === 'speech_ended')
    return 'THINKING';
  if (state === 'THINKING' && event.type === 'speech_started')
    return 'USER_SPEAKING';  // 用户在思考时打断
  if (state === 'THINKING' && event.type === 'response_ready')
    return 'AGENT_SPEAKING';
  if (state === 'AGENT_SPEAKING' && event.type === 'barge_in')
    return event.confidence > 0.75 ? 'INTERRUPTED' : 'AGENT_SPEAKING';
  if (state === 'AGENT_SPEAKING' && event.type === 'tts_done')
    return 'LISTENING';
  if (state === 'INTERRUPTED')
    return 'USER_SPEAKING';
  return state;
}
```

**关键设计点**:
- 使用 **置信度阈值** 区分真实语音和噪声（`confidence > 0.65`）
- Barge-in 使用 **更高的置信度阈值**（`> 0.75`）防止误触发
- `INTERRUPTED` 是瞬态，立即转移到 `USER_SPEAKING`

---

## 3. 与草稿的对照分析

### 3.1 草稿状态机回顾

```
IDLE → LISTENING → TURN_STARTED → PROCESSING → SPEAKING → INTERRUPTED → ENDED
```

### 3.2 完备性分析

| 草稿状态 | 业界对应 | 评价 |
|----------|----------|------|
| **IDLE** | LiveKit 的初始状态、OpenAI 的 session 创建前 | ✅ 必要，表示会话未开始或已结束 |
| **LISTENING** | LiveKit LISTENING、OpenAI speech_started 后 | ✅ 必要，但建议细分为 LISTENING + USER_SPEAKING |
| **TURN_STARTED** | OpenAI speech_stopped / committed | ⚠️ 语义模糊。是用户 turn 开始还是 Agent turn 开始？ |
| **PROCESSING** | LiveKit THINKING、OpenAI response.created | ✅ 必要，表示 ASR→LLM→TTS 流水线处理中 |
| **SPEAKING** | LiveKit SPEAKING、OpenAI audio.delta 期间 | ✅ 必要 |
| **INTERRUPTED** | 社区状态机的 INTERRUPTED | ✅ 必要，但应为瞬态 |
| **ENDED** | 会话结束 | ✅ 必要 |

### 3.3 缺失的关键状态

基于业界实践，草稿缺少以下关键状态：

| 缺失状态 | 来源参考 | 重要性 | 说明 |
|----------|----------|--------|------|
| **WARM_UP / INITIALIZING** | 所有平台 | 🔴 高 | 会话初始化、模型加载、WebSocket 连接建立。OpenAI 的 `session.update` 阶段 |
| **USER_SPEAKING** | 社区最佳实践 | 🔴 高 | 区分"正在监听"和"用户正在说话"。LISTENING 太宽泛 |
| **PRE_SPEECH / PREAMBLE** | ElevenLabs Soft Timeout | 🟡 中 | Agent 需要思考时播放填充音频（如"嗯，让我想想..."） |
| **COOLDOWN** | 社区实践 | 🟡 中 | 响应完成后短暂冷却期，防止 Agent 立即抢话 |
| **ERROR / RECOVERY** | Azure TurnStatusReceived | 🔴 高 | 处理 ASR 失败、LLM 超时、TTS 错误、网络断开等异常 |
| **QUEUED / PENDING** | Pipecat Flows | 🟡 中 | 多个用户输入排队时的并发控制 |
| **TOOL_CALLING** | OpenAI Function Calling | 🟡 中 | Agent 正在执行工具调用（可能较长延迟） |

### 3.4 状态转移条件分析

草稿的状态转移条件不够清晰。以下是基于业界实践的建议：

```
IDLE ──(session.start)──→ WARM_UP
WARM_UP ──(session.ready)──→ LISTENING

LISTENING ──(VAD: speech_started, confidence > threshold)──→ USER_SPEAKING
LISTENING ──(timeout, no speech)──→ COOLDOWN → LISTENING

USER_SPEAKING ──(VAD: speech_stopped / semantic_end)──→ PROCESSING
USER_SPEAKING ──(max_turn_duration exceeded)──→ PROCESSING  (强制截断)

PROCESSING ──(first_token / first_audio_byte)──→ PRE_SPEECH (可选)
PROCESSING ──(response_ready)──→ SPEAKING
PROCESSING ──(speech_started)──→ INTERRUPTED  (用户在思考时打断)
PROCESSING ──(error / timeout)──→ ERROR

PRE_SPEECH ──(response_ready)──→ SPEAKING
PRE_SPEECH ──(speech_started)──→ INTERRUPTED

SPEAKING ──(tts_done)──→ COOLDOWN
SPEAKING ──(speech_started, confidence > barge_in_threshold)──→ INTERRUPTED

INTERRUPTED ──(immediate)──→ USER_SPEAKING  (瞬态)
INTERRUPTED ──(task_state_preserved)──→ 恢复上下文

COOLDOWN ──(cooldown_timer expired)──→ LISTENING
COOLDOWN ──(speech_started)──→ USER_SPEAKING  (用户主动说话)

ERROR ──(recovery_success)──→ LISTENING
ERROR ──(recovery_failed, retries_exhausted)──→ ENDED

[任意状态] ──(session.end / max_duration)──→ ENDED
```

### 3.5 关键设计缺陷

1. **TURN_STARTED 语义模糊**: 草稿中 `LISTENING → TURN_STARTED` 的转移不清晰。是用户开始说话（speech_started）还是 Agent 开始处理（turn committed）？建议拆分为 `USER_SPEAKING` 和 `PROCESSING`。

2. **缺少置信度阈值**: 草稿没有区分真实语音和噪声的机制。业界实践使用双重阈值（普通 VAD 和 barge-in VAD）。

3. **INTERRUPTED 后状态不明确**: 草稿中 `INTERRUPTED` 之后转移到哪里？应该是瞬态，立即进入 `USER_SPEAKING`。

4. **缺少错误恢复路径**: 任何状态都可能发生错误，草稿没有 ERROR 状态和恢复机制。

5. **缺少超时处理**: 用户长时间不说话、LLM 超时、TTS 超时等场景没有覆盖。

---

## 4. 架构模式分析

### 4.1 Event-Driven vs State-Machine vs Hybrid

| 模式 | 代表 | 优点 | 缺点 |
|------|------|------|------|
| **纯事件驱动** | OpenAI Realtime API | 灵活、松耦合、易于扩展 | 状态隐式、难以调试、并发控制复杂 |
| **纯状态机** | LiveKit Agents (三状态) | 状态明确、易于验证、可预测 | 扩展性差、难以处理复杂场景 |
| **Hybrid（事件驱动 + 状态机）** | Pipecat、社区最佳实践 | 兼具灵活性和可预测性 | 设计复杂度较高 |

**推荐**: **Hybrid 模式**。使用显式状态机管理高层对话状态，使用事件驱动处理底层音频流和异步操作。

### 4.2 层次状态机（HSM）

**来源**: [Introduction to Hierarchical State Machines (Barr Group)](https://barrgroup.com/blog/introduction-hierarchical-state-machines)

HSM 的核心思想是 **状态嵌套 + 行为继承**：

```
┌─────────────────────────────────────────────┐
│              SESSION_ACTIVE                   │
│                                               │
│  ┌─────────────────────────────────────┐    │
│  │         USER_TURN                     │    │
│  │  ┌──────────┐  ┌──────────────┐     │    │
│  │  │ LISTENING │  │USER_SPEAKING │     │    │
│  │  └──────────┘  └──────────────┘     │    │
│  └─────────────────────────────────────┘    │
│                                               │
│  ┌─────────────────────────────────────┐    │
│  │         AGENT_TURN                    │    │
│  │  ┌──────────┐  ┌──────────┐         │    │
│  │  │PROCESSING│  │ SPEAKING │         │    │
│  │  └──────────┘  └──────────┘         │    │
│  │  ┌──────────┐                        │    │
│  │  │TOOL_CALL │ (子状态)               │    │
│  │  └──────────┘                        │    │
│  └─────────────────────────────────────┘    │
│                                               │
│  共享行为:                                     │
│  - speech_started → INTERRUPTED (任意子状态)    │
│  - error → ERROR (任意子状态)                   │
│  - session.end → ENDED (任意子状态)             │
└─────────────────────────────────────────────┘
```

**HSM 优势**:
- **行为继承**: 父状态定义的转移（如 `speech_started → INTERRUPTED`）自动适用于所有子状态
- **减少重复**: 不需要在每个状态中重复定义相同的转移
- **更好的组织**: 自然地区分 User Turn 和 Agent Turn

### 4.3 与 ASR/TTS/LLM 流水线的集成模式

#### 模式 A: 紧密耦合（Deepgram 模式）

```
┌──────────────────────────────────────────┐
│            Unified Runtime                │
│                                           │
│  Audio In → [STT → LLM → TTS] → Audio Out│
│                  ↑                        │
│            Turn Controller                │
│           (同一进程内)                     │
└──────────────────────────────────────────┘
```

- **优点**: 最低延迟、Turn-Taking 与转录/生成在同一循环
- **缺点**: 供应商锁定、难以替换组件

#### 模式 B: 松耦合 + 中央 Turn Controller（推荐）

```
┌─────────────────────────────────────────────────────┐
│                  Turn Controller                      │
│              (独立状态机 + 事件总线)                    │
│                                                       │
│  ┌────────┐   ┌────────┐   ┌────────┐   ┌────────┐  │
│  │  VAD   │   │  ASR   │   │  LLM   │   │  TTS   │  │
│  │(Silero/ │   │(Deepgram│   │(OpenAI/│   │(ElevenL│  │
│  │ WebRTC) │   │ /Azure) │   │ Gemini)│   │ /Azure)│  │
│  └───┬────┘   └───┬────┘   └───┬────┘   └───┬────┘  │
│      │            │            │            │         │
│      └────────────┴────────────┴────────────┘         │
│                     ↑ 事件总线                         │
└─────────────────────────────────────────────────────┘
```

- **优点**: 组件可替换、独立扩展、可观测性强
- **缺点**: 需要精心设计事件协议

#### 模式 C: 端到端（Moshi / GLM-4-Voice 模式）

```
┌──────────────────────────────────────────┐
│         End-to-End Speech Model           │
│                                           │
│  Audio In → [Single Neural Model] → Audio Out
│                                           │
│  Turn Control 内嵌于模型推理               │
└──────────────────────────────────────────┘
```

- **优点**: 最低延迟、最自然的对话体验
- **缺点**: 黑盒、难以调试、无法替换组件

### 4.4 关键架构决策

#### 并发控制

来自 [Gladia: Designing concurrent pipelines for real-time voice AI](https://www.gladia.io/blog/concurrent-pipelines-for-voice-ai):

1. **序列化队列**: 每个会话使用序列化队列，防止 TTS 生成时新的 STT 结果到达
2. **Guard Conditions**: 某些操作每 turn 只应触发一次，使用 guard 条件抑制快速重复事件
3. **快照输入**: 触发响应生成时，捕获当前转录缓冲区的快照，后续更新不影响该 LLM 调用

#### 中断恢复

来自 [Hamming: Voice Agent Interruption Handling](https://hamming.ai/resources/voice-agent-interruption-handling-runbook):

```json
{
  "interruption": {
    "reason": "caller_correction_detected",
    "confidence": 0.87
  },
  "recovery": {
    "agentTranscriptTruncatedAtMs": 1840,
    "newTurnCommitted": true,
    "taskStatePreserved": true
  }
}
```

关键恢复字段:
- `agentTranscriptTruncatedAtMs`: Agent 被截断的位置
- `newTurnCommitted`: 新 turn 是否已提交
- `taskStatePreserved`: 任务状态是否保留（用于恢复上下文）

---

## 5. 推荐架构与设计建议

### 5.1 推荐状态机（改进版）

```
                        ┌─────────────┐
                        │    IDLE     │
                        └──────┬──────┘
                               │ session.start
                               ▼
                        ┌─────────────┐
                        │  WARM_UP    │
                        └──────┬──────┘
                               │ session.ready
                               ▼
                        ┌─────────────┐
               ┌───────│  LISTENING   │◄──────────────┐
               │       └──────┬──────┘                │
               │              │ speech_started         │
               │              ▼                        │
               │       ┌─────────────┐                │
               │       │USER_SPEAKING│                │
               │       └──────┬──────┘                │
               │              │ speech_stopped         │
               │              ▼                        │
               │       ┌─────────────┐                │
               │       │ PROCESSING  │                │
               │       └──┬──────┬───┘                │
               │          │      │                     │
               │   (可选) │      │ response_ready      │
               │          ▼      ▼                     │
               │  ┌──────────┐ ┌─────────┐            │
               │  │PRE_SPEECH│ │SPEAKING │            │
               │  └────┬─────┘ └────┬────┘            │
               │       │            │                  │
               │       └────────────┘                  │
               │              │ tts_done               │
               │              ▼                        │
               │       ┌─────────────┐                │
               │       │  COOLDOWN   │────────────────┘
               │       └─────────────┘   cooldown_end
               │
               │    ┌─────────────┐
               └────│INTERRUPTED  │ (瞬态)
                    └──────┬──────┘
                           │ immediate
                           ▼
                    ┌─────────────┐
                    │USER_SPEAKING│
                    └─────────────┘

        任意状态 ──(error)──→ ┌─────────┐
                              │  ERROR  │
                              └────┬────┘
                                   │ recovery / retry_exhausted
                                   ▼
                              ┌─────────┐
                              │  ENDED  │
                              └─────────┘
```

### 5.2 推荐层次状态机结构

```
SESSION_ACTIVE (父状态)
├── USER_TURN (父状态)
│   ├── LISTENING
│   └── USER_SPEAKING
├── AGENT_TURN (父状态)
│   ├── PROCESSING
│   │   └── TOOL_CALLING (子状态)
│   ├── PRE_SPEECH
│   └── SPEAKING
├── COOLDOWN
└── INTERRUPTED (瞬态)

全局状态:
├── IDLE
├── WARM_UP
├── ERROR
└── ENDED
```

### 5.3 关键设计原则

1. **双重置信度阈值**: 普通 VAD 使用较低阈值（~0.65），Barge-in 使用较高阈值（~0.75）
2. **INTERRUPTED 必须是瞬态**: 不保持状态，立即转移到 USER_SPEAKING
3. **每个状态都需要超时**: LISTENING 超时 → COOLDOWN、PROCESSING 超时 → ERROR
4. **错误恢复**: 支持重试计数、退避策略、优雅降级
5. **状态快照**: 在进入 PROCESSING 时快照当前上下文，用于中断恢复
6. **序列化队列**: 每个会话使用单一事件队列，防止竞态条件
7. **可观测性**: 每个状态转移记录时间戳、触发事件、置信度

### 5.4 与草稿的差异总结

| 维度 | 草稿 | 推荐方案 |
|------|------|----------|
| 状态数量 | 7 | 12（含子状态） |
| 层次结构 | 扁平 | 两层 HSM |
| User/Agent Turn 区分 | 不清晰 | 显式 USER_TURN / AGENT_TURN |
| 错误处理 | 无 | ERROR + 恢复路径 |
| 初始化 | 无 | WARM_UP |
| 冷却期 | 无 | COOLDOWN |
| 填充语 | 无 | PRE_SPEECH（可选） |
| 置信度阈值 | 无 | 双重阈值 |
| 超时处理 | 无 | 每状态超时 |
| 并发控制 | 无 | 序列化队列 + Guard Conditions |

---

## 参考来源

1. OpenAI. "Voice Activity Detection (VAD) — Realtime API." https://developers.openai.com/api/docs/guides/realtime-vad
2. OpenAI. "Realtime Conversations." https://developers.openai.com/api/docs/guides/realtime-conversations
3. Latent Space. "OpenAI Realtime API: The Missing Manual." https://www.latent.space/p/realtime-api
4. transitive-bullshit. "openai-realtime-api." https://github.com/transitive-bullshit/openai-realtime-api
5. Pipecat. "Gemini Live Integration." https://docs.pipecat.ai/api-reference/server/services/s2s/gemini-live
6. Microsoft. "What's new in Azure Speech in Foundry Tools." https://learn.microsoft.com/en-us/azure/ai-services/speech-service/releasenotes
7. Microsoft. "Configuring Noise Detection and Barge-In with Azure Voice Live API." https://techcommunity.microsoft.com/blog/healthcareandlifesciencesblog/
8. ElevenLabs. "Conversation Flow." https://elevenlabs.io/docs/eleven-agents/customization/conversation-flow
9. ElevenLabs. "Interaction Models: Building Natural Human-AI Dialogue." https://elevenlabs.io/blog/interaction-models
10. ElevenLabs. "Agent WebSockets." https://elevenlabs.io/docs/eleven-agents/api-reference/eleven-agents/websocket
11. Deepgram. "Voice Agent API." https://deepgram.com/product/voice-agent-api
12. Deepgram. "Inside Deepgram's Voice Agent API." https://deepgram.com/learn/voice-agent-api-generally-available
13. Deepgram. "ElevenLabs Barge-In & Turn-Taking: Call Center Guide." https://deepgram.com/learn/elevenlabs-barge-in-interruptions-turn-taking
14. Kyutai. "Moshi: a speech-text foundation model for real-time dialogue." https://kyutai.org/Moshi.pdf
15. Kyutai. "Moshi GitHub." https://github.com/kyutai-labs/moshi
16. THUDM. "GLM-4-Voice GitHub." https://github.com/THUDM/GLM-4-Voice
17. Zeng et al. "GLM-4-Voice: Towards Intelligent and Human-Like End-to-End Spoken Chatbot." arXiv:2412.02612
18. LiveKit. "Voice Agents." https://livekit.com/voice-agents
19. LiveKit. "Agent Starter Python." https://github.com/livekit-examples/agent-starter-python
20. Pipecat. "Introduction to Pipecat Flows." https://docs.pipecat.ai/pipecat-flows/introduction
21. Daily.co. "Advice on Building Voice AI in June 2025." https://www.daily.co/blog/advice-on-building-voice-ai-in-june-2025
22. Daily.co. "Voice AI Turn Detection: 2025 Problem." https://www.linkedin.com/posts/kwkramer_smarter-voice-ai-turn-detection-is-a-2025-activity-7311092374988800002-jVQi
23. Gladia. "Designing Concurrent Pipelines for Real-Time Voice AI." https://www.gladia.io/blog/concurrent-pipelines-for-voice-ai
24. Hamming. "Voice Agent Interruption Handling: Barge-In, Backchannels." https://hamming.ai/resources/voice-agent-interruption-handling-runbook
25. Lokutor. "The Architecture of Interruption: A Developer's Guide to Real-Time Voice Agents." https://lokutor.com/blog/developers-guide-voice-agents
26. Barr Group. "Introduction to Hierarchical State Machines (HSMs)." https://barrgroup.com/blog/introduction-hierarchical-state-machines
27. dev.to. "Voice Agent Turn-Taking: Stop Live AI Calls From Talking Over Users." https://dev.to/jackm-singularity/voice-agent-turn-taking-stop-live-ai-calls-from-talking-over-users-590b
28. LogRocket. "How to Build a Real Time Voice AI Agent in the Browser." https://blog.logrocket.com/voice-ai-agent-browser
29. arXiv. "Building Enterprise Realtime Voice Agents from Scratch." arXiv:2603.05413v1
30. AWS. "Building Intelligent AI Voice Agents with Pipecat and Amazon Bedrock." https://aws.amazon.com/blogs/machine-learning/building-intelligent-ai-voice-agents-with-pipecat-and-amazon-bedrock-part-1
