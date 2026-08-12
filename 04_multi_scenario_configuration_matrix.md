# 多场景配置矩阵（直播/助手/实时对话）最佳实践

> **文档版本**: v1.0  
> **生成日期**: 2026-08-11  
> **研究范围**: 2024-2026 年业界主流语音 AI 平台的 turn control 参数配置方案

---

## 目录

1. [场景分类与参数化](#1-场景分类与参数化)
2. [业界配置矩阵方案](#2-业界配置矩阵方案)
3. [关键配置参数详解](#3-关键配置参数详解)
4. [与草稿的对照分析](#4-与草稿的对照分析)
5. [配置管理最佳实践](#5-配置管理最佳实践)
6. [附录：完整配置矩阵参考表](#6-附录完整配置矩阵参考表)

---

## 1. 场景分类与参数化

### 1.1 场景分类框架

基于对 OpenAI Realtime API、Google Gemini Live、LiveKit Agents、Deepgram、ElevenLabs、Vapi、Agora ConvoAI 等主流平台的调研，我们将语音 AI 交互场景归纳为以下 **7 大类**：

| 场景编号 | 场景名称 | 交互模式 | 典型延迟要求 | 核心特征 |
|---------|---------|---------|------------|---------|
| S1 | **直播** (Live Streaming) | 单向为主，偶尔互动 | 中低 (< 2s) | 主播主导，观众偶尔发言；需要过滤背景噪音 |
| S2 | **语音助手/Jarvis** (Voice Assistant) | 指令式，短交互 | 极低 (< 500ms) | 唤醒词触发，短指令，快速响应 |
| S3 | **实时对话** (Real-time Conversation) | 双向，自然对话 | 低 (< 300ms turn-taking) | 自然轮换，允许打断，类似人类对话 |
| S4 | **会议转录** (Meeting Transcription) | 多说话人，被动 | 中 (实时转写) | 多人场景，说话人分离，不主动打断 |
| S5 | **客服** (Customer Service) | 任务导向，结构化 | 中低 (< 1s) | 引导式对话，需要耐心等待用户完整表达 |
| S6 | **教育** (Education) | 互动式，引导性 | 中 (< 1.5s) | 教师角色，需要给学生思考时间 |
| S7 | **面试** (Interview) | 结构化问答 | 中 (< 1s) | 严格轮换，手动控制 turn 边界 |

### 1.2 各场景参数化需求详解

#### S1: 直播场景

**核心矛盾**: 主播持续说话 vs 偶尔需要响应观众互动。

**关键参数取向**:
- **Turn Detection**: 偏向保守（高阈值），避免将主播话语误判为结束
- **打断策略**: 默认关闭或极高阈值，防止环境噪音触发打断
- **VAD 灵敏度**: 低灵敏度，需要明确的语音信号才触发
- **特殊机制**: 建议支持"Push-to-Talk"模式供观众互动，或使用关键词唤醒

**来源**: OpenAI Realtime API 支持 `turn_detection: null` 完全关闭自动 turn detection，适用于手动控制场景（[OpenAI Developer Community, 2025](https://community.openai.com/t/turn-detection-null-breaks-manual-audio-control-in-realtime-api-web-rtc/1146451)）；Agora ConvoAI 支持 `start_of_speech.mode: "manual"` 和 `end_of_speech.mode: "manual"` 用于 push-to-talk 场景（[Agora Docs, 2025](https://docs.agora.io/en/ai/release-notes)）。

#### S2: 语音助手/Jarvis 场景

**核心矛盾**: 极低延迟 vs 准确识别指令边界。

**关键参数取向**:
- **Turn Detection**: 激进（低 silence_duration），快速判断指令结束
- **打断策略**: 开启但需要最小语音时长过滤（min_duration ~200ms）
- **端点检测**: 使用语义 VAD（semantic VAD）判断语义完整性
- **超时策略**: 短超时（~5s），超时后自动取消等待

**来源**: Deepgram Flux 模型支持 `eot_threshold` (0.5-0.9) 和 `eager_eot_threshold` (0.3-0.9) 实现 eager end-of-turn 检测，可在用户说完之前就开始准备响应（[Deepgram Docs, 2025](https://developers.deepgram.com/docs/flux/configuration)）。

#### S3: 实时对话场景

**核心矛盾**: 自然流畅 vs 避免误打断。

**关键参数取向**:
- **Turn Detection**: 平衡型，使用语义 VAD + 动态端点检测
- **打断策略**: 自适应打断（adaptive interruption），区分真实打断和背景音
- **端点检测**: 动态模式（dynamic endpointing），根据用户语速自适应
- **预生成**: 开启 preemptive generation 降低感知延迟

**来源**: LiveKit Agents 1.5.0+ 引入 Adaptive Interruption Handling，使用专用音频模型区分真实打断和咳嗽等背景音（[LiveKit Blog, 2025](https://livekit.com/blog/adaptive-interruption-handling)）；支持 dynamic endpointing 根据用户停顿模式自适应调整 `min_delay`/`max_delay`（[LiveKit Docs, 2025](https://docs.livekit.io/agents/logic/turns)）。

#### S4: 会议转录场景

**核心矛盾**: 多说话人 vs 准确分离和转写。

**关键参数取向**:
- **Turn Detection**: 关闭自动 turn detection，使用连续转录模式
- **打断策略**: 不适用（无主动发言）
- **说话人分离**: 启用 diarization
- **特殊机制**: 多通道音频输入，说话人签名（voice signatures）

**来源**: Azure Speech Service 提供 Meeting Transcription 功能，支持多说话人识别和分离（[Microsoft Docs, 2024-2025](https://learn.microsoft.com/en-us/answers/questions/2123482/)）。

#### S5: 客服场景

**核心矛盾**: 高效解决问题 vs 不打断客户完整表达。

**关键参数取向**:
- **Turn Detection**: 偏保守，给客户充足的表达时间
- **打断策略**: 谨慎开启，高阈值过滤
- **端点检测**: 较长 silence_duration（~800-1000ms）
- **特殊机制**: 非打断性回传信号（backchannel），如"嗯"、"好的"

**来源**: Vapi 提供 Conservative 预设，`waitFunction: "700 + 4000 * max(0, x-0.5)"`，在 50% 置信度时等待约 2700ms（[Vapi Docs, 2025](https://docs.vapi.ai/customization/voice-pipeline-configuration)）；ElevenLabs 支持 `turn_timeout` (1-30s) 和 soft timeout 配置（[ElevenLabs Docs, 2025](https://elevenlabs.io/docs/eleven-agents/customization/conversation-flow)）。

#### S6: 教育场景

**核心矛盾**: 引导思考 vs 及时反馈。

**关键参数取向**:
- **Turn Detection**: 偏保守，给学生思考时间
- **打断策略**: 默认关闭或极高阈值
- **端点检测**: 较长 silence_duration（~1000-1500ms）
- **特殊机制**: 支持手动 turn 控制（如学生举手/按钮）

**来源**: OpenAI Realtime API 在教育场景的应用中，建议使用 `eagerness: "low"` 以给学生充足的回答时间（[Springs, 2025](https://springsapps.com/knowledge/revolutionizing-education-with-openais-realtime-api-ai-teachers-are-getting-close-to-natural-conversations)）。

#### S7: 面试场景

**核心矛盾**: 严格结构化 vs 自然交流。

**关键参数取向**:
- **Turn Detection**: 手动模式或保守自动模式
- **打断策略**: 关闭
- **端点检测**: 手动 EOS（End of Speech）信号
- **特殊机制**: 明确的 turn 边界控制

**来源**: Agora ConvoAI 支持 `start_of_speech.mode: "manual"` 和 `end_of_speech.mode: "manual"`，适用于 AI 面试、互动问答等需要严格 turn 边界控制的场景（[Agora Docs, 2025](https://docs.agora.io/en/ai/release-notes)）。

---

## 2. 业界配置矩阵方案

### 2.1 OpenAI Realtime API — Session Configuration

OpenAI Realtime API 通过 `session.update` 事件配置 turn detection，支持两种 VAD 模式：

#### Server VAD（传统模式）

```json
{
  "type": "session.update",
  "session": {
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

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `threshold` | float (0-1) | 0.5 | VAD 灵敏度，越高越不敏感 |
| `prefix_padding_ms` | int | 300 | 检测到语音开始前保留的音频（ms） |
| `silence_duration_ms` | int | 500 | 判定语音结束所需的静音时长（ms） |
| `create_response` | bool | true | turn 结束时是否自动生成回复 |
| `interrupt_response` | bool | true | 用户说话时是否打断当前回复 |

#### Semantic VAD（默认模式，推荐）

```json
{
  "type": "session.update",
  "session": {
    "turn_detection": {
      "type": "semantic_vad",
      "eagerness": "auto",
      "create_response": true,
      "interrupt_response": true
    }
  }
}
```

| 参数 | 可选值 | 说明 |
|------|--------|------|
| `eagerness` | `"auto"`, `"low"`, `"medium"`, `"high"` | 结束 turn 的激进程度。low = 更保守，high = 更激进 |
| `create_response` | bool | 同上 |
| `interrupt_response` | bool | 同上 |

**关键发现**: Semantic VAD 使用语义分类器判断用户是否说完，比纯静音检测更准确，减少 mid-sentence 打断（[OpenAI API Docs, 2025](https://developers.openai.com/api/docs/guides/realtime-vad)）。

**来源**: [OpenAI Realtime API VAD Guide](https://developers.openai.com/api/docs/guides/realtime-vad); [LiveKit OpenAI Plugin Docs](https://docs.livekit.io/agents/models/realtime/plugins/openai); [Inworld AI Docs](https://docs.inworld.ai/realtime/usage/using-realtime-models)

### 2.2 Google Gemini Multimodal Live API — Speech Config

Google Gemini Live API 通过 `realtime_input_config.automatic_activity_detection` 配置 VAD：

```python
from google.genai import types

config = {
    "response_modalities": ["AUDIO"],
    "realtime_input_config": {
        "automatic_activity_detection": {
            "disabled": False,
            "start_of_speech_sensitivity": types.StartSensitivity.START_SENSITIVITY_LOW,
            "end_of_speech_sensitivity": types.EndSensitivity.END_SENSITIVITY_LOW,
            "prefix_padding_ms": 20,
            "silence_duration_ms": 100,
        }
    }
}
```

| 参数 | 可选值 | 说明 |
|------|--------|------|
| `disabled` | bool | 是否禁用自动 VAD（手动模式） |
| `start_of_speech_sensitivity` | `START_SENSITIVITY_LOW` / `START_SENSITIVITY_HIGH` | 语音开始检测灵敏度 |
| `end_of_speech_sensitivity` | `END_SENSITIVITY_LOW` / `END_SENSITIVITY_HIGH` | 语音结束检测灵敏度 |
| `prefix_padding_ms` | int | 语音开始前保留的音频（ms） |
| `silence_duration_ms` | int | 判定结束的静音时长（ms） |

**关键发现**: Gemini Live 支持 Hybrid VAD 模式——可同时使用服务端 VAD 和本地 VAD（如 Silero），本地 VAD 发送 activity 信号给 Gemini API（[Pipecat Docs, 2025](https://docs.pipecat.ai/api-reference/server/services/s2s/gemini-live)）。

**来源**: [Google AI Gemini Live API Capabilities](https://ai.google.dev/gemini-api/docs/live-api/capabilities); [Agora Gemini Live Docs](https://docs.agora.io/en/ai/models/mllm/gemini); [Google Colab Tutorial](https://colab.research.google.com/github/GoogleCloudPlatform/generative-ai/blob/main/gemini/multimodal-live-api/intro_multimodal_live_api_genai_sdk.ipynb)

### 2.3 LiveKit Agents — Turn Handling Options

LiveKit Agents 提供最完整的 turn control 配置体系（v1.5.x+）：

```python
from livekit.agents import AgentSession, TurnHandlingOptions, inference

session = AgentSession(
    turn_handling=TurnHandlingOptions(
        turn_detection=inference.TurnDetector(),  # 默认语义 turn detector
        endpointing={
            "mode": "dynamic",      # "fixed" 或 "dynamic"
            "min_delay": 0.3,       # 最小等待（秒）
            "max_delay": 2.5,       # 最大等待（秒）
        },
        interruption={
            "mode": "adaptive",     # "adaptive" 或 "vad"
            "min_duration": 0.5,    # 最小语音时长才算打断（秒）
            "min_words": 0,         # 最小词数
            "false_interruption_timeout": 2.0,  # 误打断超时（秒）
            "resume_false_interruption": True,  # 误打断后是否恢复
        },
        preemptive_generation={
            "enabled": True,
            "preemptive_tts": True,  # 是否预生成 TTS
            "max_speech_duration": 10.0,
            "max_retries": 3,
        },
    ),
)
```

**三种 Turn Detection 模式**:

| 模式 | 说明 | 适用场景 |
|------|------|---------|
| `turn_detection=inference.TurnDetector()` | LiveKit 语义模型（~135M SmolLM-v2 fine-tune），结合 acoustic VAD (Silero) | 通用对话（默认） |
| `turn_detection="stt"` | 使用 STT 提供商的端点检测（如 Deepgram Flux, AssemblyAI） | 需要 STT 原生端点检测 |
| `turn_detection="realtime_llm"` | 使用 Realtime LLM 的服务端 turn detection | OpenAI/Google 服务端 VAD |

**来源**: [LiveKit Turn Detection Blog](https://livekit.com/blog/turn-detection-and-interruption-handling); [LiveKit Turn Handling Options Reference](https://docs.livekit.io/reference/agents/turn-handling-options); [LiveKit Turn-taking Tuning](https://docs.livekit.io/agents/logic/turns/tuning); [Forasoft LiveKit Guide 2026](https://www.forasoft.com/blog/article/livekit-ai-agents-guide)

### 2.4 Deepgram — End-of-Turn Detection (Flux)

Deepgram 的 Flux 模型提供精细的 end-of-turn 检测参数：

| 参数 | 范围 | 默认值 | 说明 |
|------|------|--------|------|
| `eot_threshold` | 0.5 - 0.9 | 0.7 | EndOfTurn 事件触发置信度阈值 |
| `eager_eot_threshold` | 0.3 - 0.9 | — | EagerEndOfTurn 事件触发置信度（预判结束） |
| `eot_timeout_ms` | 500 - 60000 | 5000 | 最大静音等待时间，超时强制 EndOfTurn |

**Eager EOT 机制**: 在用户可能快说完时（中等置信度）就触发 `EagerEndOfTurn`，让 LLM 提前开始推理。如果用户继续说，则触发 `TurnResumed` 取消预生成。这可以节省数百毫秒延迟（[Deepgram Docs, 2025](https://developers.deepgram.com/docs/flux/voice-agent-eager-eot)）。

**来源**: [Deepgram Flux Configuration](https://developers.deepgram.com/docs/flux/configuration); [Deepgram Voice Agent Config](https://developers.deepgram.com/docs/configure-voice-agent)

### 2.5 ElevenLabs — Conversation Flow

ElevenLabs Conversational Agents 的对话流配置：

| 参数 | 范围 | 说明 |
|------|------|------|
| `conversation_config.turn.turn_timeout` | 1-30s | 静音后等待多久接管 turn |
| Soft timeout | 可配置 | LLM 思考时播放填充语（filler）的等待时间 |
| Interruptions | 开/关 | 是否允许用户打断 |
| `interruption_ignore_terms` | string[] | 忽略特定短语的打断触发（v2.52.0+） |

**来源**: [ElevenLabs Conversation Flow](https://elevenlabs.io/docs/eleven-agents/customization/conversation-flow); [ElevenLabs Changelog Nov 2025](https://elevenlabs.io/docs/changelog/2025/11/12); [ElevenLabs Release Notes Aug 2026](https://releasebot.io/updates/eleven-labs)

### 2.6 Vapi — Voice Pipeline Configuration

Vapi 提供最丰富的场景预设体系，通过 `startSpeakingPlan` 和 `stopSpeakingPlan` 控制：

**Start Speaking Plan（何时开始说话）**:

```json
{
  "startSpeakingPlan": {
    "waitSeconds": 0.4,
    "smartEndpointingPlan": {
      "provider": "livekit",
      "waitFunction": "2000 / (1 + exp(-10 * (x - 0.5)))"
    }
  }
}
```

**三种预设 waitFunction**:

| 预设 | waitFunction | 50%置信度等待 | 90%置信度等待 | 适用场景 |
|------|-------------|-------------|-------------|---------|
| Aggressive | `2000 / (1 + exp(-10*(x-0.5)))` | ~200ms | ~50ms | 客服、游戏、实时交互 |
| Normal | `(20+500*sqrt(x)+2500*x^3+700+4000*max(0,x-0.5))/2` | ~800ms | ~300ms | 通用对话 |
| Conservative | `700 + 4000 * max(0, x-0.5)` | ~2700ms | ~700ms | 医疗、正式场合 |

**Stop Speaking Plan（何时停止说话）**:

```json
{
  "stopSpeakingPlan": {
    "numWords": 0,
    "voiceSeconds": 0.2,
    "backoffSeconds": 1.0
  }
}
```

| 参数 | 说明 | 推荐值 |
|------|------|--------|
| `numWords` | 需要多少词才触发打断（0 = 仅依赖 VAD） | 0-2 |
| `voiceSeconds` | 用户需要说话多久才触发打断 | 0.15-0.3s |
| `backoffSeconds` | 被打断后等待多久恢复说话 | 0.5-2.0s |

**来源**: [Vapi Voice Pipeline Configuration](https://docs.vapi.ai/customization/voice-pipeline-configuration); [Vapi Speech Configuration](https://docs.vapi.ai/customization/speech-configuration); [Vapi Optimization 2026](https://voiceaiwrapper.com/insights/vapi-voice-ai-optimization-performance-guide-voiceaiwrapper)

### 2.7 Agora ConvoAI — Turn Detection

Agora ConvoAI 支持三种 VAD 类型和手动模式：

```json
{
  "turn_detection": {
    "mode": "server_vad",
    "server_vad_config": {
      "prefix_padding_ms": 800,
      "silence_duration_ms": 640,
      "start_of_speech_sensitivity": "START_SENSITIVITY_HIGH",
      "end_of_speech_sensitivity": "END_SENSITIVITY_HIGH"
    }
  }
}
```

**手动模式**（适用于面试、测验、Push-to-Talk）:
- `start_of_speech.mode: "manual"` — 客户端通过 RTM 信令显式发送 SoS
- `end_of_speech.mode: "manual"` — 客户端通过 RTM 信令显式发送 EoS

**来源**: [Agora ConvoAI Playground](https://agoraio-community.github.io/ConvoAI-Playground); [Agora Release Notes](https://docs.agora.io/en/ai/release-notes)

---

## 3. 关键配置参数详解

### 3.1 VAD 参数族

VAD（Voice Activity Detection）是所有 turn control 的基础层。

| 参数 | 典型范围 | 作用 | 调优方向 |
|------|---------|------|---------|
| `threshold` | 0.0 - 1.0 | 语音检测灵敏度 | ↑ 更高 = 更不敏感，减少误检 |
| `silence_duration_ms` | 100 - 1500 | 判定语音结束的静音时长 | ↑ 更长 = 更保守，等用户说完 |
| `speech_duration_ms` / `min_duration` | 100 - 1000 | 判定为有效语音的最短时长 | ↑ 更长 = 过滤短噪音 |
| `prefix_padding_ms` | 20 - 800 | 语音开始前保留的音频 | ↑ 更长 = 保留更多上下文，但增加延迟 |

**业界默认值对比**:

| 平台 | threshold | silence_duration_ms | prefix_padding_ms |
|------|-----------|-------------------|-------------------|
| OpenAI Server VAD | 0.5 | 500 | 300 |
| Google Gemini Live | — | 100 | 20 |
| Agora VAD | 0.5 | 640 | 800 |
| LiveKit (默认) | — | 动态 | — |

**来源**: 综合各平台官方文档

### 3.2 Turn Decision 参数族

控制何时判定用户说完并触发回复。

| 参数 | 典型范围 | 作用 |
|------|---------|------|
| `turn_sensitivity` / `eagerness` | low/medium/high/auto | 结束 turn 的激进程度 |
| `min_response_delay` / `min_delay` | 0.1 - 1.0s | turn 判定后的最小等待 |
| `max_response_delay` / `max_delay` | 1.0 - 5.0s | turn 判定的最大等待（防悬挂） |
| `eot_threshold` | 0.5 - 0.9 | End-of-Turn 置信度阈值 |
| `eager_eot_threshold` | 0.3 - 0.9 | 预判 End-of-Turn 的置信度阈值 |
| `eot_timeout_ms` | 1000 - 60000 | 最大静音等待，超时强制结束 turn |

**来源**: [Deepgram Flux Configuration](https://developers.deepgram.com/docs/flux/configuration); [LiveKit EndpointingOptions](https://docs.livekit.io/reference/agents/turn-handling-options)

### 3.3 Interruption（打断）参数族

控制用户打断 AI 发言的行为。

| 参数 | 典型范围 | 作用 |
|------|---------|------|
| `barge_in_enabled` / `interrupt_response` | bool | 是否允许打断 |
| `interruption_sensitivity` / `mode` | vad / adaptive | 打断检测模式 |
| `min_duration` | 100 - 1000ms | 最短语音时长才算有效打断 |
| `min_words` | 0 - 5 | 最少词数才算有效打断 |
| `false_interruption_timeout` | 1.0 - 5.0s | 误打断检测超时 |
| `resume_false_interruption` | bool | 误打断后是否恢复发言 |
| `backoff_seconds` | 0.5 - 3.0s | 被打断后等待多久恢复 |

**关键洞察**: LiveKit 的 Adaptive Interruption Handling（2025年发布）使用专用音频模型区分真实打断和咳嗽/背景噪音，是当前业界最先进的打断处理方案（[LiveKit Blog, 2025](https://livekit.com/blog/adaptive-interruption-handling)）。

**来源**: [LiveKit Interruption Handling](https://livekit.com/blog/turn-detection-and-interruption-handling); [Hamming Voice Agent Runbook 2026](https://hamming.ai/resources/voice-agent-interruption-handling-runbook); [FutureAGI Barge-In Guide 2026](https://futureagi.com/blog/voice-ai-barge-in-turn-taking-2026)

### 3.4 场景预设（Preset）设计

基于业界实践，推荐以下预设体系：

#### Preset 设计原则

1. **分层覆盖**: 每个 preset 覆盖 VAD → Turn Decision → Interruption 三层
2. **可覆盖性**: preset 提供默认值，允许逐参数覆盖
3. **命名语义化**: 名称反映场景特征，如 `"live_streaming"`, `"voice_assistant"`, `"natural_conversation"`
4. **版本化**: preset 支持版本号，便于灰度升级

#### 推荐 Preset 列表

| Preset ID | 名称 | 适用场景 | 核心特征 |
|-----------|------|---------|---------|
| `live_streaming` | 直播模式 | S1 | 高 VAD 阈值，关闭打断，手动互动 |
| `voice_assistant` | 语音助手 | S2 | 激进端点检测，短超时，快速响应 |
| `natural_conversation` | 自然对话 | S3 | 自适应打断，动态端点，预生成 |
| `meeting_transcription` | 会议转录 | S4 | 关闭 turn detection，连续转录 |
| `customer_service` | 客服模式 | S5 | 保守端点，长静音等待，backchannel |
| `education` | 教育模式 | S6 | 保守端点，关闭打断，长思考时间 |
| `interview` | 面试模式 | S7 | 手动 turn 控制，关闭打断 |

---

## 4. 与草稿的对照分析

### 4.1 草稿覆盖度评估

假设草稿包含"直播/贾维斯配置矩阵"，对照业界实践：

| 维度 | 草稿覆盖 | 业界实践 | 差距分析 |
|------|---------|---------|---------|
| 场景数量 | 2（直播 + Jarvis） | 7+ | **不足**：缺少客服、教育、面试、会议等关键场景 |
| VAD 参数 | 待确认 | threshold, silence_duration_ms, prefix_padding_ms, speech_duration_ms | 需确认粒度 |
| Turn Decision | 待确认 | eagerness, min/max_delay, eot_threshold, eager_eot | 需确认是否覆盖语义 VAD |
| 打断策略 | 待确认 | mode (adaptive/vad), min_duration, false_interruption | 需确认是否支持自适应打断 |
| 预设体系 | 待确认 | 业界普遍采用 preset 模式 | 建议引入 |

### 4.2 参数粒度建议

**当前业界最佳实践的参数粒度**:

1. **粗粒度（Preset 层）**: 面向产品和运营，选择场景 preset 即可
2. **中粒度（Category 层）**: VAD / Turn Decision / Interruption 三大类，面向调优工程师
3. **细粒度（Parameter 层）**: 单个参数，面向算法工程师和 A/B 测试

**建议**: 草稿应至少覆盖中粒度，即明确三大类参数族，每类 3-5 个关键参数。

### 4.3 动态场景切换需求

**业界实践**: 
- OpenAI Realtime API 支持 `session.update` 在会话中动态修改 turn_detection 配置，无需重连（[AssemblyAI Migration Guide, 2025](https://www.assemblyai.com/blog/migrating-from-openai-realtime-api-to-assemblyai-voice-agent-api)）
- LiveKit Agents 支持运行时更新 endpointing 参数（[LiveKit Docs](https://docs.livekit.io/reference/agents/turn-handling-options)）

**建议**: 支持以下动态切换场景：
- **手动切换**: 用户/开发者通过 API 或 UI 切换 preset
- **条件切换**: 基于会话特征自动切换（如检测到多人 → 切换到会议模式）
- **渐进切换**: 在对话中根据用户行为渐变参数（如用户多次被打断 → 自动降低打断灵敏度）

---

## 5. 配置管理最佳实践

### 5.1 配置热更新

**业界方案**:

1. **OpenAI Realtime API**: 通过 WebSocket `session.update` 事件实时修改配置，无需断开连接
   ```
   ws.send(JSON.stringify({
     type: "session.update",
     session: { turn_detection: { type: "semantic_vad", eagerness: "low" } }
   }))
   ```

2. **LiveKit Agents**: 支持 `session.update_turn_handling()` 运行时更新

3. **Vapi**: 通过 Dashboard 或 API 修改 assistant 配置，即时生效，无需重新部署代码

**推荐架构**:
```
配置中心 (Feature Flag Service)
    │
    ├── 全局默认配置
    ├── 场景 Preset 配置
    ├── A/B 实验配置
    └── 用户/会话级覆盖
         │
         ▼
    Turn Control Engine
         │
         ├── VAD Module
         ├── Turn Decision Module
         └── Interruption Module
```

### 5.2 A/B 测试框架

**业界实践**:

基于 Maxim AI 的 2026 年 AI Agent A/B 测试策略（[Maxim AI, 2026](https://www.getmaxim.ai/articles/5-strategies-for-a-b-testing-for-ai-agent-deployment)）：

1. **单参数 A/B**: 固定其他参数，仅改变一个参数（如 silence_duration_ms: 500 vs 800）
2. **Preset A/B**: 对比不同 preset 的整体效果
3. **多臂老虎机（MAB）**: 自动探索最优参数组合

**关键指标**:
- **延迟指标**: P50/P95 turn-taking 延迟
- **准确率指标**: 误打断率（False Barge-in Rate）、漏打断率（Missed Barge-in Rate）
- **用户体验指标**: 对话完成率、用户满意度评分、平均对话时长

**Hamming 推荐流程**（[Hamming Runbook, 2026](https://hamming.ai/resources/voice-agent-interruption-handling-runbook)）:
1. 选择一个工作流（如预约改期）
2. 冻结测试集（含真实打断、backchannel、噪音、长实体、静音超时）
3. 每次只改一个参数
4. 对比误打断 vs 漏打断
5. 发布后检查 Top 20 被打断通话

### 5.3 场景自动识别

**设计思路**:

虽然当前业界尚无成熟的"场景自动分类器"产品，但可以从以下信号推断场景：

| 信号 | 推断逻辑 |
|------|---------|
| 音频通道数 | 单通道 → 对话/助手；多通道 → 会议 |
| 用户说话占比 | < 10% → 直播；30-50% → 对话；> 80% → 独白 |
| 平均 utterance 长度 | < 2s → 指令式（助手）；> 5s → 叙述式（直播/教育） |
| 打断频率 | 高频 → 自然对话；零 → 面试/教育 |
| 静音段分布 | 长静音 → 教育/思考；短静音 → 快速对话 |
| 应用上下文 | 通过 SDK 传入的 scene hint |

**推荐实现**: 采用"显式声明 + 自动推断"混合模式：
- 优先使用开发者/用户显式指定的 scene
- 无显式声明时，基于信号自动推断并应用对应 preset
- 推断结果以较低置信度应用（偏保守），避免激进推断导致体验问题

---

## 6. 附录：完整配置矩阵参考表

### 6.1 七场景 × 三维度配置矩阵

#### VAD 参数

| 场景 | threshold | silence_duration_ms | prefix_padding_ms | speech_duration_ms |
|------|-----------|-------------------|-------------------|-------------------|
| S1 直播 | 0.7-0.9 | 1000-1500 | 500-800 | 500-800 |
| S2 语音助手 | 0.3-0.5 | 200-400 | 100-200 | 100-200 |
| S3 实时对话 | 0.4-0.6 | 400-700 | 200-400 | 200-400 |
| S4 会议转录 | N/A (连续) | N/A | N/A | N/A |
| S5 客服 | 0.5-0.7 | 700-1000 | 300-500 | 300-500 |
| S6 教育 | 0.5-0.7 | 1000-1500 | 300-500 | 300-500 |
| S7 面试 | N/A (手动) | N/A | N/A | N/A |

#### Turn Decision 参数

| 场景 | eagerness | min_delay (s) | max_delay (s) | eot_threshold | eager_eot |
|------|-----------|--------------|--------------|--------------|-----------|
| S1 直播 | low | 1.0 | 5.0 | 0.8-0.9 | 关闭 |
| S2 语音助手 | high | 0.1 | 1.5 | 0.5-0.6 | 0.3-0.4 |
| S3 实时对话 | auto/medium | 0.3 | 2.5 | 0.6-0.7 | 0.4-0.5 |
| S4 会议转录 | N/A | N/A | N/A | N/A | N/A |
| S5 客服 | low/medium | 0.5 | 3.0 | 0.7-0.8 | 可选 |
| S6 教育 | low | 0.8 | 4.0 | 0.7-0.9 | 关闭 |
| S7 面试 | N/A (手动) | N/A | N/A | N/A | N/A |

#### Interruption 参数

| 场景 | barge_in | mode | min_duration (ms) | false_interruption_timeout (s) | backoff (s) |
|------|----------|------|-------------------|-------------------------------|-------------|
| S1 直播 | 关闭 | — | — | — | — |
| S2 语音助手 | 开启 | vad | 150-250 | 1.0 | 0.3-0.5 |
| S3 实时对话 | 开启 | adaptive | 300-500 | 2.0 | 0.5-1.0 |
| S4 会议转录 | N/A | — | — | — | — |
| S5 客服 | 开启 | adaptive | 400-600 | 2.5 | 1.0-2.0 |
| S6 教育 | 关闭 | — | — | — | — |
| S7 面试 | 关闭 | — | — | — | — |

### 6.2 各平台场景预设对照

| 场景 | OpenAI Realtime | Google Gemini | LiveKit | Deepgram | Vapi | Agora |
|------|----------------|---------------|---------|----------|------|-------|
| 直播 | turn_detection: null | disabled: true | manual mode | — | — | manual SoS/EoS |
| 助手 | eagerness: high | END_SENSITIVITY_HIGH | min_delay: 0.1 | eot_threshold: 0.5 | Aggressive preset | threshold: 0.3 |
| 对话 | eagerness: auto | END_SENSITIVITY_LOW | dynamic endpointing | eot_threshold: 0.7 | Normal preset | default |
| 客服 | eagerness: low | END_SENSITIVITY_LOW | min_delay: 0.5 | eot_threshold: 0.8 | Conservative preset | threshold: 0.7 |
| 教育 | eagerness: low | disabled: false (保守) | min_delay: 0.8 | eot_threshold: 0.9 | Conservative preset | — |
| 面试 | turn_detection: null | disabled: true | manual mode | — | — | manual SoS/EoS |

### 6.3 延迟预算参考

基于 Forasoft 2026 LiveKit 指南和 VoiceAIWrapper 2026 优化报告：

| 阶段 | 激进模式 | 平衡模式 | 保守模式 |
|------|---------|---------|---------|
| Endpointing | ~80ms | ~200ms | ~500ms |
| ASR (STT) | ~120ms | ~150ms | ~200ms |
| LLM | ~180ms | ~350ms | ~500ms |
| TTS | ~70ms | ~100ms | ~150ms |
| **总计 (P50)** | **~450ms** | **~800ms** | **~1350ms** |

**关键洞察**: 端点检测（endpointing）的配置对总延迟的影响可能超过 ASR+LLM+TTS 之和。Vapi 默认的 1500ms `onNoPunctuationSeconds` 是最大的单一延迟来源（[VoiceAIWrapper, 2026](https://voiceaiwrapper.com/insights/vapi-voice-ai-optimization-performance-guide-voiceaiwrapper)）。

---

## 参考来源汇总

| 序号 | 来源 | 文档/文章 | URL |
|------|------|----------|-----|
| 1 | OpenAI | Realtime API VAD Guide | https://developers.openai.com/api/docs/guides/realtime-vad |
| 2 | LiveKit | OpenAI Realtime API Plugin Guide | https://docs.livekit.io/agents/models/realtime/plugins/openai |
| 3 | LiveKit | Configuring Turn Detection and Interruptions | https://livekit.com/blog/turn-detection-and-interruption-handling |
| 4 | LiveKit | Turn Handling Options Reference | https://docs.livekit.io/reference/agents/turn-handling-options |
| 5 | LiveKit | Turn-taking Tuning | https://docs.livekit.io/agents/logic/turns/tuning |
| 6 | LiveKit | Adaptive Interruption Handling | https://livekit.com/blog/adaptive-interruption-handling |
| 7 | LiveKit | Turn Detection for Voice Agents | https://livekit.com/blog/turn-detection-voice-agents-vad-endpointing-model-based-detection |
| 8 | Google AI | Gemini Live API Capabilities | https://ai.google.dev/gemini-api/docs/live-api/capabilities |
| 9 | Agora | Google Gemini Live Configuration | https://docs.agora.io/en/ai/models/mllm/gemini |
| 10 | Agora | Release Notes (Manual SoS/EoS) | https://docs.agora.io/en/ai/release-notes |
| 11 | Deepgram | End-of-Turn Detection Parameters | https://developers.deepgram.com/docs/flux/configuration |
| 12 | Deepgram | Configure Voice Agent | https://developers.deepgram.com/docs/configure-voice-agent |
| 13 | Deepgram | Eager End of Turn Optimization | https://developers.deepgram.com/docs/flux/voice-agent-eager-eot |
| 14 | Deepgram | ElevenLabs Barge-In Guide | https://deepgram.com/learn/elevenlabs-barge-in-interruptions-turn-taking |
| 15 | ElevenLabs | Conversation Flow | https://elevenlabs.io/docs/eleven-agents/customization/conversation-flow |
| 16 | ElevenLabs | Changelog (Soft Timeout) | https://elevenlabs.io/docs/changelog/2025/11/12 |
| 17 | Vapi | Voice Pipeline Configuration | https://docs.vapi.ai/customization/voice-pipeline-configuration |
| 18 | Vapi | Speech Configuration | https://docs.vapi.ai/customization/speech-configuration |
| 19 | VoiceAIWrapper | Vapi Optimization 2026 | https://voiceaiwrapper.com/insights/vapi-voice-ai-optimization-performance-guide-voiceaiwrapper |
| 20 | Forasoft | LiveKit AI Voice Agents 2026 Playbook | https://www.forasoft.com/blog/article/livekit-ai-agents-guide |
| 21 | Hamming | Voice Agent Interruption Handling Runbook | https://hamming.ai/resources/voice-agent-interruption-handling-runbook |
| 22 | FutureAGI | Voice AI Barge-In and Turn-Taking 2026 | https://futureagi.com/blog/voice-ai-barge-in-turn-taking-2026 |
| 23 | Maxim AI | A/B Testing for AI Agent Deployment | https://www.getmaxim.ai/articles/5-strategies-for-a-b-testing-for-ai-agent-deployment |
| 24 | Inworld AI | Configuring Realtime Models | https://docs.inworld.ai/realtime/usage/using-realtime-models |
| 25 | AssemblyAI | OpenAI Realtime API Migration Guide | https://www.assemblyai.com/blog/migrating-from-openai-realtime-api-to-assemblyai-voice-agent-api |
| 26 | Pipecat | Gemini Live API Reference | https://docs.pipecat.ai/api-reference/server/services/s2s/gemini-live |
| 27 | Agora | ConvoAI Playground | https://agoraio-community.github.io/ConvoAI-Playground |
| 28 | Medium (Prashant Agarwal) | Building Real-Time AI Conversations with Gemini Live API | https://medium.com/@agarwalprashant355/building-real-time-ai-conversations-with-googles-gemini-live-api-a-complete-guide-05d7c93b33db |
| 29 | Springs | OpenAI Realtime API in Education | https://springsapps.com/knowledge/revolutionizing-education-with-openais-realtime-api-ai-teachers-are-getting-close-to-natural-conversations |
| 30 | Introl | Voice AI Infrastructure 2025 | https://introl.com/blog/voice-ai-infrastructure-real-time-speech-agents-asr-tts-guide-2025 |

---

> **文档结束** — 本报告基于 2024-2026 年公开可用的业界文档、API 参考和工程实践编写。所有参数推荐值均为基于业界实践的参考值，实际部署需根据具体场景进行 A/B 测试调优。
