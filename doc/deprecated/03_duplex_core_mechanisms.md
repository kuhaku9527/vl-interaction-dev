# 全双工语音对话核心机制深度拆解

> **研究日期**: 2026-08-11  
> **研究范围**: Turn-Taking、Barge-In、端点检测、低延迟流式传输、回声消除  
> **目标**: 为自研全双工语音对话架构提供技术选型参考

---

## 目录

1. [Turn-Taking（话轮交接）](#1-turn-taking话轮交接)
2. [Barge-In（打断机制）](#2-barge-in打断机制)
3. [端点检测与静默判断](#3-端点检测与静默判断)
4. [低延迟流式音频传输](#4-低延迟流式音频传输)
5. [回声消除与音频前端处理](#5-回声消除与音频前端处理)
6. [综合架构建议](#6-综合架构建议)

---

## 1. Turn-Taking（话轮交接）

### 1.1 问题定义

话轮交接是全双工对话系统最核心的挑战：系统需要判断"用户是否说完了"以及"系统何时应该开始说话"。传统半双工系统采用严格的"你说完→我再说"模式，而全双工系统需要支持重叠语音、中途打断和自然的话轮切换。

### 1.2 三种技术路线

#### 1.2.1 传统方法：基于 VAD 的硬切换

**原理**：通过声学 VAD 检测静默超时（silence timeout），超过阈值（通常 500ms-800ms）后判定用户话轮结束，系统接管说话权。

**代表实现**：
- WebRTC VAD + 固定静默阈值
- 早期 IVR 系统

**关键参数**：
- `silence_duration_ms`：静默超时阈值（典型值 500-1000ms）
- `prefix_padding_ms`：语音开始前的缓冲 padding（典型值 100-300ms）

**优点**：
- 实现简单，计算开销极低
- 延迟可预测

**缺点**：
- 无法区分"思考停顿"和"话轮结束"
- 对语速慢的用户容易误判（过早打断）
- 无法处理 backchannel（"嗯"、"对"等反馈语）
- 用户体验机械、不自然

#### 1.2.2 现代方法：语义级软切换

**原理**：利用 LLM 或专用模型判断语义完整性，在语义完成时主动让出话轮，而非依赖声学静默。

**代表实现**：

| 系统 | 机制 | 技术细节 |
|------|------|----------|
| **Moshi (Kyutai)** | 多流并行生成 | 双流架构：Inner Monologue（文本 token）+ Audio（声学 token）同时生成。模型始终处于"听+说"状态，不显式建模 speaker turn，而是通过训练数据自然学习对话动态。7B 参数，Mimi 神经音频编解码器 12.5 tokens/s |
| **GPT-4o Realtime API** | Server VAD + Semantic VAD | 两种模式：(1) `server_vad`：基于声学的 VAD，`silence_duration_ms` 默认 500ms；(2) `semantic_vad`：基于语义的 VAD，`eagerness` 参数控制响应积极性（low/medium/high/auto） |
| **Pipecat SmartTurn** | 三层联合判定 | VAD（Silero，200ms 触发）→ SmartTurn 音频模型（Whisper Tiny 基座，8M 参数，分析最近 8 秒音频的韵律/语调/填充词）→ LLM 语义完成判定（`UserTurnCompletionLLMServiceMixin`） |
| **FireRedChat** | pVAD + EoT 检测器 | 流式个性化 VAD（ECAPA-TDNN 说话人嵌入 + GRU）+ 语义 EoT 检测器（BERT 微调，83 万条训练样本） |

**Moshi 架构深度解析**：

Moshi 是第一个真正的全双工实时对话 LLM，其核心创新包括：

1. **多流自回归架构**：同时预测两个 token 流——文本 token（内部独白，不输出给用户）和音频 token（输出给用户）
2. **Mimi 编解码器**：将语义和声学信息融合到单一 tokenizer 中，使用残差向量量化（RVQ）+ 知识蒸馏自自监督语音模型
3. **深度 Transformer**：7 个声学残差码本层级并行生成（非串行），语义 token 权重 100 倍于声学 token
4. **全双工原生**：训练时就建模了重叠语音和打断场景，不需要显式 speaker turn 建模
5. **延迟**：TTFA（Time To First Audio）极低，因为声学 token 在语义序列完成前就开始生成

#### 1.2.3 混合策略：声学 + 语义联合判定

**2026 年生产级最佳实践**（来源：FutureAGI、Hamming AI、Pipecat）：

```
三层级联判定架构：

Layer 1: 声学 VAD（快速响应层）
  ├─ 200ms 短触发窗口
  ├─ 检测到语音能量 → 立即标记潜在打断
  └─ 检测到静默 → 触发 Layer 2

Layer 2: 音频 Turn Detection 模型（韵律分析层）
  ├─ 分析语调、语速、填充词（"嗯"、"那个"）
  ├─ 区分 backchannel vs. barge-in vs. 继续静默
  └─ 输出：turn_complete / turn_continuing / backchannel

Layer 3: LLM 语义完成判定（语义确认层）
  ├─ 基于对话上下文判断语义完整性
  ├─ 输出特殊 token（如 <EOS>）或完成概率
  └─ 最终决策：切换话轮 / 继续等待
```

### 1.3 关键指标对比

| 指标 | 纯 VAD | 纯语义 | 混合方案 |
|------|--------|--------|----------|
| 切换延迟 | 500-800ms | 100-300ms | 150-400ms |
| 误切换率 | 高（~30%） | 低（~5-10%） | 最低（~3-5%） |
| 计算开销 | 极低 | 高 | 中等 |
| 自然度 | 差 | 好 | 最好 |
| Backchannel 处理 | 不支持 | 支持 | 支持 |

**FireRedChat 实测数据**：
- T90 延迟：170ms（vs LiveKit 140ms, Ten 90ms）
- 误打断率：10.2%（vs LiveKit 33.4%, Ten 78.1%）
- 关键洞察：延迟略高但误打断率大幅降低，用户体验更优

### 1.4 对自研架构的建议

| 组件 | 建议 | 理由 |
|------|------|------|
| VAD 层 | **复用开源** Silero VAD | 成熟、准确率高（87.7% TPR）、CPU 友好、Python 生态 |
| Turn Detection 模型 | **自研或微调** | 需要针对中文语音韵律优化；可参考 Pipecat SmartTurn（Whisper Tiny + 线性分类器，8M 参数） |
| LLM 语义判定 | **自研 Prompt 工程** | 在对话 LLM 的 system prompt 中加入 turn completion 判定逻辑，输出 `<TURN_COMPLETE>` token |
| 整体调度 | **自研 Turn Controller** | 需要协调三层信号、管理状态机、处理竞态条件 |

---

## 2. Barge-In（打断机制）

### 2.1 问题定义

Barge-in 指用户在系统说话时插入语音，系统需要：
1. 快速检测到用户语音
2. 立即停止 TTS 输出
3. 清理已缓冲但未播放的音频
4. 恢复对话上下文到打断前的正确状态
5. 开始处理用户的新输入

### 2.2 三种技术路线

#### 2.2.1 声学层面打断

**原理**：检测到用户语音能量超过阈值 → 立即中断 TTS 输出。

**实现细节**：
- VAD 持续运行在麦克风输入流上
- 当 VAD 检测到 `speech_started` 事件且系统正在播放 TTS → 触发打断
- 中断信号发送到 TTS 引擎（WebSocket `stop` 消息或本地 `cancel` 调用）

**延迟要求**：
- 检测延迟：< 50ms（从用户开口到系统检测到）
- 中断延迟：< 100ms（从检测到 TTS 停止播放）
- 总打断响应延迟：< 150ms

**优点**：响应最快
**缺点**：容易误触发（咳嗽、环境噪声、backchannel）

#### 2.2.2 语义层面打断

**原理**：ASR 识别到完整语义后，判断是否需要打断。

**缺点**：延迟太高（ASR 需要积累足够音频才能识别），不适合实时打断场景。通常作为声学打断的"事后确认"层。

#### 2.2.3 混合策略（2026 生产标准）

```
声学快速响应 + 语义事后确认：

Phase 1: 声学检测（< 50ms）
  └─ VAD 检测到语音能量 → 立即暂停 TTS

Phase 2: 快速分类（50-200ms）
  └─ Turn Detection 模型判断：barge-in / backchannel / noise
  ├─ 若是 noise → 恢复 TTS（误触发恢复）
  └─ 若是 barge-in → 确认打断，进入 Phase 3

Phase 3: 状态恢复（200-500ms）
  ├─ 截断对话历史到用户实际听到的位置
  ├─ 标记被中断的 agent 话语（previous_agent_utterance_interrupted flag）
  └─ 启动新的 STT → LLM → TTS 流水线
```

### 2.3 开源实现分析

#### LiveKit Agents 打断机制

```
架构：AgentSession 统一编排

打断流程：
1. VAD 检测到用户语音 → 触发 interruption 事件
2. 自动暂停 agent TTS 播放
3. 自动截断对话历史（只保留用户实际听到的部分）
4. 启动新的 STT pass
5. 可通过 session.interrupt() 显式触发

关键配置：
- turn_detection: "vad" | "stt" | "semantic"
- interruption.enabled: true/false
- 支持禁用打断（通过 say() 的 turn_handling 参数）
```

#### Pipecat 打断机制

```
架构：Pipeline 帧处理 + UserTurnStrategies

打断流程：
1. SileroVADAnalyzer 持续检测
2. TurnAnalyzerUserTurnStopStrategy 判定
3. LLMContextAggregatorPair 管理上下文
4. 被打断的 agent 话语存入 conversation state
   - previous_agent_utterance_interrupted: "Your account balance is..."
5. LLM 根据 flag 决定：重复 / 继续 / 重新开始

关键优化：
- 预分配 cancel handles（取消延迟 < 100ms）
- 服务端 TTS 中断支持（Cartesia/ElevenLabs/PlayHT WebSocket stop 消息，30-60ms）
```

#### GPT-4o Realtime API 打断机制

```
事件驱动架构：

关键事件：
- input_audio_buffer.speech_started  → 用户开始说话
- input_audio_buffer.speech_stopped  → 用户停止说话
- response.create                    → 创建响应
- response.cancel                    → 取消当前响应（打断核心）
- input_audio_buffer.clear           → 清空音频缓冲
- conversation.item.truncate         → 截断对话项

打断流程：
1. 服务端 VAD 检测到 speech_started
2. 自动发送 response.cancel（如果正在生成响应）
3. 清空未播放的音频缓冲
4. 截断对话历史
5. 开始处理新的用户输入

两种 VAD 模式：
- server_vad: threshold=0.5, silence_duration_ms=500, prefix_padding_ms=300
- semantic_vad: eagerness="low"|"medium"|"high"|"auto"
```

### 2.4 关键挑战与解决方案

| 挑战 | 描述 | 解决方案 |
|------|------|----------|
| **回声自触发** | 系统播放的 TTS 被麦克风拾取，误判为用户语音 | AEC（声学回声消除）+ 扬声器参考信号 |
| **Backchannel 误判** | 用户的"嗯"、"对"被误判为打断 | Turn Detection 模型分类 backchannel vs. barge-in |
| **打断后状态恢复** | 打断后对话上下文不完整 | 截断历史 + interrupted flag + LLM 感知 |
| **竞态条件** | 用户在系统即将结束时说话 | 状态机 + 原子操作 + turn_id 追踪 |

### 2.5 关键指标

| 指标 | 目标值 | 测量方法 |
|------|--------|----------|
| 打断检测延迟 | < 150ms | speech_started → TTS stop |
| 误打断率 | < 5% | false_positive / total_interruptions |
| 打断后恢复成功率 | > 95% | interruption.recovered / total_interruptions |
| 打断后任务完成率 | > 90% | 用户意图是否在打断后正确完成 |

### 2.6 对自研架构的建议

| 组件 | 建议 | 理由 |
|------|------|------|
| 声学打断检测 | **复用开源** Silero VAD | 低延迟、高准确率 |
| Turn 分类模型 | **自研** | 中文 backchannel 特征与英文不同，需定制训练数据 |
| 上下文恢复 | **自研** | 与对话管理深度耦合，需定制 |
| TTS 中断 | **复用** 服务端 TTS API 的 stop 能力 | Cartesia/ElevenLabs 等已提供标准接口 |
| 状态机 | **自研** | 核心调度逻辑，需精确控制 |

---

## 3. 端点检测与静默判断

### 3.1 声学 VAD 方案对比

#### 3.1.1 WebRTC VAD

| 属性 | 详情 |
|------|------|
| **原理** | 基于高斯混合模型（GMM），6 个频带能量特征 |
| **帧大小** | 10/20/30ms |
| **模式** | 0（低检测）~ 3（高检测） |
| **准确率** | TPR ~50%（5% FPR），AUC 较低 |
| **延迟** | < 1ms（极低） |
| **优点** | 超轻量、浏览器原生支持、零依赖 |
| **缺点** | 准确率低，噪声环境下表现差 |
| **适用场景** | 嵌入式/IoT、浏览器端初筛 |

#### 3.1.2 Silero VAD

| 属性 | 详情 |
|------|------|
| **原理** | 神经网络模型（1.6MB），基于短时傅里叶变换特征 |
| **帧大小** | 30/60/100ms（可配置） |
| **准确率** | TPR ~87.7%（5% FPR），AUC 显著优于 WebRTC |
| **延迟** | < 1ms（CPU 推理） |
| **优点** | 准确率高、CPU 友好、Python 生态、开源 |
| **缺点** | 模型较大（1.6MB vs WebRTC 的几 KB） |
| **适用场景** | **2025-2026 年开源首选**，绝大多数新项目采用 |

#### 3.1.3 其他 VAD 方案

| 方案 | 特点 | 适用场景 |
|------|------|----------|
| **Cobra VAD** (Picovoice) | TPR 98.9%（5% FPR），跨平台 SDK，企业级 | 商业产品 |
| **MarbleNet** (NVIDIA) | 基于 Jasper 架构，GPU 优化 | 服务端高吞吐 |
| **Personal VAD** | 目标说话人条件化，抑制非目标说话人 | 多说话人场景 |
| **TEN VAD** | 精度优于 Silero，计算量更低 | 新兴开源替代 |
| **Pyannote VAD** | 优化说话人分割，处理重叠语音 | 说话人日志场景 |

#### 3.1.4 VAD 准确率对比

```
在 5% FPR 下的 TPR（True Positive Rate）：
┌─────────────────┬──────────┐
│ VAD 方案         │ TPR      │
├─────────────────┼──────────┤
│ WebRTC VAD       │ ~50%     │
│ Silero VAD       │ ~87.7%   │
│ Cobra VAD        │ ~98.9%   │
│ TEN VAD          │ > Silero │
└─────────────────┴──────────┘

关键发现：Silero 比 WebRTC 错误少 4 倍，Cobra 比 Silero 错误少 12 倍
```

### 3.2 语义级 EOS 判定

#### 3.2.1 LLM 特殊 Token 方法

**原理**：训练/提示 LLM 在语义完成时输出特殊 token（如 `<EOS>` 或 `<TURN_COMPLETE>`）。

**代表实现**：
- **Pipecat `UserTurnCompletionLLMServiceMixin`**：在 LLM prompt 中注入 turn completion 判定逻辑，模型输出单 token 标签
- **GPT-4.1 / Gemini 2.5 Flash / Claude Sonnet 4.5**：新一代模型能可靠输出单 token 判定
- **流式 ASR 中的 EOS token**：在 Transducer-based ASR 中，使用特殊 token 检测句子结束，通过训练损失惩罚延迟预测

**优点**：语义准确，能理解上下文
**缺点**：依赖 LLM 推理延迟（100-500ms），不适合单独使用

#### 3.2.2 专用语义 EoT 模型

**FireRedChat EoT 检测器**：
- 基于 BERT 微调的分类器
- 训练数据：83 万条文本实例（从完整话语中采样部分 span 模拟"未完成"状态）
- 输入：ASR 转录文本
- 输出：语义完成 / 未完成

**Pipecat SmartTurn V3**：
- 基于 Whisper Tiny（8M 参数）+ 线性分类器
- 输入：原始音频波形（非转录文本！）
- 分析最近 8 秒音频
- 输出：turn_complete / turn_incomplete
- 关键优势：捕获转录文本丢失的韵律信息（语调、语速、填充词）

### 3.3 混合方案架构

```
                    ┌──────────────┐
   麦克风音频 ──────▶│   Silero VAD  │──────▶ speech / silence 帧
                    └──────┬───────┘
                           │ silence detected
                           ▼
                    ┌──────────────┐
                    │ SmartTurn    │──────▶ turn_complete / incomplete
                    │ (音频模型)    │        (基于韵律 + 填充词)
                    └──────┬───────┘
                           │ turn_complete
                           ▼
                    ┌──────────────┐
                    │ LLM 语义判定  │──────▶ 最终决策
                    │ (文本上下文)   │
                    └──────────────┘
```

**时序设计**：
- VAD 层：每 20-30ms 输出一次判定
- SmartTurn 层：VAD 检测到静默后触发，延迟 ~50ms
- LLM 层：SmartTurn 判定 turn_complete 后触发，延迟 ~200ms
- 总端点检测延迟：~250-300ms（vs 纯 VAD 的 500-800ms）

### 3.4 对自研架构的建议

| 组件 | 建议 | 理由 |
|------|------|------|
| 基础 VAD | **复用开源** Silero VAD v5 | 最佳开源选择，1.6MB，CPU 推理 < 1ms |
| 中文 Turn Detection | **自研训练** | 中文韵律特征（声调、语气词"吧/嘛/呢"）与英文不同；可参考 SmartTurn 架构（Whisper 基座 + 分类头） |
| LLM EOS 判定 | **自研 Prompt** | 在对话 LLM 中注入判定逻辑，利用现有推理能力 |
| 整体调度 | **自研** | 三层信号融合 + 超时兜底策略 |

---

## 4. 低延迟流式音频传输

### 4.1 WebSocket vs WebRTC 对比

#### 4.1.1 核心差异

| 维度 | WebSocket | WebRTC |
|------|-----------|--------|
| **传输协议** | TCP（可靠有序） | UDP/RTP（不可靠但低延迟） |
| **丢包行为** | 阻塞重传 → 全链路暂停 | 丢包跳过 → 20ms 音频帧丢失几乎不可感知 |
| **延迟** | 80-150ms（服务器中转） | 50-80ms（SFU 中转）或更低（P2P） |
| **NAT 穿透** | 天然支持（HTTPS 端口） | 需要 ICE/STUN/TURN |
| **音频处理** | 需自行实现 | 浏览器内置 AEC/NS/AGC/Opus |
| **服务器控制** | 完全可控 | 有限（直连模式）或通过 SFU |
| **实现复杂度** | 低 | 高 |
| **适用场景** | 服务端 Agent ↔ 模型 API | 客户端 ↔ 服务端 |

#### 4.1.2 2026 年生产架构推荐

```
混合传输架构（OpenAI、LiveKit、Pipecat 均采用）：

客户端（浏览器/App）
  │  WebRTC (UDP/RTP + Opus)
  │  内置 AEC/NS/AGC
  ▼
LiveKit SFU / 媒体服务器
  │  WebSocket (TCP)
  │  服务端完全控制
  ▼
AI Agent 服务（STT → LLM → TTS）
  │  WebSocket
  ▼
模型 API（OpenAI Realtime / Gemini / etc.）
```

**延迟叠加分析**：
- 客户端 → SFU：20-40ms（WebRTC UDP）
- SFU → Agent：10-20ms（内网 WebSocket）
- Agent → 模型 API：50-100ms（WebSocket）
- 总传输延迟：80-160ms

### 4.2 音频编码：Opus vs PCM

| 维度 | Opus | PCM (16-bit, 16kHz) |
|------|------|---------------------|
| **算法延迟** | 26.5ms（默认 20ms 帧 + 5ms lookahead + 1.5ms 重采样） | 0ms（无编码） |
| **最小延迟** | 5ms（最小帧 2.5ms） | 0ms |
| **比特率** | 6-510 kbps（语音典型 32 kbps） | 256 kbps（16bit × 16kHz） |
| **带宽占用** | 极低（~4 KB/s） | 高（~32 KB/s） |
| **音质** | 32kbps 以上接近透明 | 无损 |
| **丢包恢复** | 内置 FEC（前向纠错） | 无 |
| **浏览器支持** | 原生 WebRTC 支持 | 需自行处理 |
| **推荐场景** | 网络传输 | 本地管道 / 服务端内部 |

**建议**：
- **客户端 ↔ 服务端**：Opus（WebRTC 默认），32-64 kbps
- **服务端内部**（Agent ↔ 模型）：PCM 或 Opus，取决于模型 API 要求
- **本地管道**（进程间）：PCM（零编码延迟）

### 4.3 流式传输优化

#### 4.3.1 Chunk 大小优化

| Chunk 大小 | 延迟 | 吞吐效率 | 推荐场景 |
|------------|------|----------|----------|
| 10ms | 极低 | 低（包头开销大） | 极致低延迟 |
| 20ms | 低 | 中 | **推荐默认值**（Opus 默认帧大小） |
| 40ms | 中 | 高 | 带宽受限 |
| 60ms+ | 高 | 高 | 非实时场景 |

#### 4.3.2 Jitter Buffer 优化

- **目标大小**：80-150ms（而非传统的 500ms）
- **策略**：自适应 jitter buffer + 丢包隐藏（PLC）
- **关键**：宁可偶尔小丢包，不要大缓冲

#### 4.3.3 服务端优化

1. **LLM Prompt 前缀缓存**：减少重复推理
2. **TTS 流式输出**：首句生成即开始播放，不等完整响应
3. **预分配 Cancel Handle**：打断时 O(1) 查找
4. **模型路由**：短回复用小模型（Haiku/Flash），长回复用大模型

### 4.4 对自研架构的建议

| 组件 | 建议 | 理由 |
|------|------|------|
| 客户端传输 | **复用** WebRTC（LiveKit SFU 或自建） | 浏览器原生、AEC/NS/Opus 内置、NAT 穿透 |
| 服务端传输 | **复用** WebSocket | 完全控制、实现简单 |
| 音频编码 | **复用** Opus（传输）+ PCM（内部） | 业界标准组合 |
| Jitter Buffer | **自研** 自适应策略 | 需根据实际场景调优 |
| 流式调度 | **自研** | 核心编排逻辑 |

---

## 5. 回声消除与音频前端处理

### 5.1 AEC 在全双工对话中的必要性

**核心问题**：系统播放的 TTS 音频被自身麦克风拾取，形成回声回路：
1. 回声被 VAD 误判为用户语音 → 误触发打断
2. 回声混入用户语音 → ASR 准确率下降
3. 极端情况 → 啸叫（反馈循环）

**场景分析**：

| 场景 | 回声严重程度 | AEC 必要性 |
|------|-------------|-----------|
| 耳机/耳麦 | 几乎无 | 低 |
| 手机免提 | 中等 | 中 |
| 智能音箱（远场） | 严重 | **必须** |
| 会议室全双工 | 严重 | **必须** |

### 5.2 开源方案对比

#### 5.2.1 WebRTC AEC3

| 属性 | 详情 |
|------|------|
| **原理** | 自适应滤波器 + 非线性处理，维护扬声器参考信号模型，从麦克风输入中减去预测回声 |
| **延迟** | < 10ms |
| **优点** | 浏览器内置、漂移补偿、舒适噪声生成、广泛部署 |
| **缺点** | 近端/远端信号比接近时性能下降；适应时间较长；配置复杂 |
| **适用** | 浏览器端、WebRTC 应用 |

#### 5.2.2 SpeexDSP

| 属性 | 详情 |
|------|------|
| **原理** | 基于 NLMS 自适应滤波器的回声衰减器（非真正消除） |
| **延迟** | < 5ms |
| **优点** | 轻量、C 实现、Python 绑定、简单 |
| **缺点** | 仅衰减非消除、无漂移补偿、近端/远端比高时仍有效（简单粗暴） |
| **适用** | 嵌入式、简单场景 |

#### 5.2.3 其他方案

| 方案 | 特点 |
|------|------|
| **Pipecat/Daily** | 内置 AEC，媒体服务器级别处理 |
| **Switchboard SDK** | 模块化音频图，WebRTC AEC3 节点 |
| **AI 增强 AEC** | 万得 3C 会议引入 AI 算法完善回声消除，CD 级音质 |

### 5.3 AEC 对打断准确率的影响

```
无 AEC：
  用户说话 ──▶ VAD 检测 ✓
  TTS 播放  ──▶ 麦克风拾取 ──▶ VAD 误判为用户语音 ✗
  结果：频繁误打断，用户体验极差

有 AEC：
  用户说话 ──▶ VAD 检测 ✓
  TTS 播放  ──▶ 麦克风拾取 ──▶ AEC 消除 ──▶ VAD 正确判定为静默 ✓
  结果：打断准确率显著提升
```

### 5.4 音频前端处理全链路

```
麦克风输入
  │
  ▼
┌──────────┐
│   AEC    │ ◄── 扬声器参考信号
└────┬─────┘
     │
     ▼
┌──────────┐
│    NS    │ (噪声抑制：RNNoise / WebRTC NS)
└────┬─────┘
     │
     ▼
┌──────────┐
│   AGC    │ (自动增益控制)
└────┬─────┘
     │
     ▼
┌──────────┐
│   VAD    │
└────┬─────┘
     │
     ▼
   ASR 输入
```

### 5.5 对自研架构的建议

| 组件 | 建议 | 理由 |
|------|------|------|
| AEC | **复用** WebRTC AEC3（浏览器端）/ SpeexDSP（服务端） | 成熟方案，无需自研 |
| 噪声抑制 | **复用** RNNoise 或 WebRTC NS | 开源成熟 |
| 自动增益 | **复用** WebRTC AGC | 浏览器内置 |
| 音频前端编排 | **复用** WebRTC Audio Processing Module | 一站式解决方案 |

---

## 6. 综合架构建议

### 6.1 自研 vs 复用决策矩阵

| 模块 | 子组件 | 决策 | 优先级 |
|------|--------|------|--------|
| **Turn-Taking** | VAD 基础层 | ✅ 复用 Silero VAD | P0 |
| | Turn Detection 模型 | 🔧 自研（中文优化） | P1 |
| | LLM 语义判定 | 🔧 自研 Prompt | P1 |
| | Turn Controller 状态机 | 🔧 自研 | P0 |
| **Barge-In** | 声学打断检测 | ✅ 复用 Silero VAD | P0 |
| | Turn 分类（barge-in/backchannel/noise） | 🔧 自研 | P1 |
| | 上下文恢复 | 🔧 自研 | P0 |
| | TTS 中断 | ✅ 复用 TTS API | P0 |
| **端点检测** | 基础 VAD | ✅ 复用 Silero VAD | P0 |
| | 语义 EoT 检测 | 🔧 自研 | P1 |
| **音频传输** | 客户端 ↔ 服务端 | ✅ 复用 WebRTC (LiveKit) | P0 |
| | 服务端 ↔ 模型 | ✅ 复用 WebSocket | P0 |
| | 音频编码 | ✅ 复用 Opus + PCM | P0 |
| **音频前端** | AEC | ✅ 复用 WebRTC AEC3 | P0 |
| | NS / AGC | ✅ 复用 WebRTC | P0 |

### 6.2 推荐架构总览

```
┌─────────────────────────────────────────────────────────┐
│                    客户端（浏览器/App）                    │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐ │
│  │ 麦克风    │─▶│ AEC/NS   │─▶│ Opus Enc │─▶│ WebRTC   │ │
│  │ 扬声器    │◀─│ AGC      │◀─│ Opus Dec │◀─│ (UDP)    │ │
│  └──────────┘  └──────────┘  └──────────┘  └────┬─────┘ │
└─────────────────────────────────────────────────┼───────┘
                                                   │
                                          WebRTC (UDP/RTP)
                                                   │
┌─────────────────────────────────────────────────┼───────┐
│                   LiveKit SFU / 媒体服务器         │       │
│                                           ┌─────┴─────┐ │
│                                           │ 音频路由   │ │
│                                           └─────┬─────┘ │
└─────────────────────────────────────────────────┼───────┘
                                                   │
                                          WebSocket (TCP)
                                                   │
┌─────────────────────────────────────────────────┼───────┐
│                      AI Agent 服务                │       │
│  ┌──────────────────────────────────────────────┴─────┐ │
│  │              Turn Controller（自研核心）             │ │
│  │  ┌──────────┐  ┌──────────────┐  ┌──────────────┐ │ │
│  │  │ Silero   │─▶│ SmartTurn    │─▶│ LLM EOS      │ │ │
│  │  │ VAD      │  │ (自研中文)    │  │ (Prompt判定)  │ │ │
│  │  └──────────┘  └──────────────┘  └──────────────┘ │ │
│  │        声学层          韵律层            语义层      │ │
│  └──────────────────────┬────────────────────────────┘ │
│                         │ 话轮决策                      │
│  ┌──────────────────────┴────────────────────────────┐ │
│  │              对话流水线                             │ │
│  │  ┌──────┐    ┌──────┐    ┌──────┐    ┌──────┐    │ │
│  │  │ STT  │───▶│ LLM  │───▶│ TTS  │───▶│ 输出  │    │ │
│  │  └──────┘    └──────┘    └──────┘    └──────┘    │ │
│  │       ▲ 打断信号可中断任一级                         │ │
│  └───────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────┘
```

### 6.3 分阶段实施路线图

| 阶段 | 内容 | 目标 |
|------|------|------|
| **Phase 1: 基础** | WebRTC 传输 + Silero VAD + 基础打断 + WebRTC AEC | 可用的半双工对话 |
| **Phase 2: 增强** | 自研 Turn Controller + 中文 Turn Detection 模型 | 自然的全双工对话 |
| **Phase 3: 优化** | LLM 语义判定 + 上下文恢复 + 延迟优化 | 生产级全双工体验 |
| **Phase 4: 进阶** | 端到端语音模型评估（Moshi/PersonaPlex 路线） | 下一代架构探索 |

---

## 信息来源

1. **Moshi**: Kyutai, "Moshi: a speech-text foundation model for real-time dialogue" (arXiv:2410.00037v2), 2024. https://kyutai.org/Moshi.pdf
2. **FireRedChat**: "FireRedChat: A Pluggable, Full-Duplex Voice Interaction System with Cascaded and Semi-Cascaded Implementations" (arXiv:2509.06502), 2025.
3. **NVIDIA PersonaPlex**: NVIDIA ADLR, "Natural Conversational AI With Any Role and Voice", 2026. https://research.nvidia.com/labs/adlr/personaplex
4. **NVIDIA VoiceChat-11B**: NVIDIA NemotronLabs, FullDuplexBench 1.0 results, 2026. https://huggingface.co/nvidia/NVIDIA-NemotronLabs-VoiceChat-11B
5. **OpenAI Realtime API**: OpenAI, "Voice activity detection (VAD) - Realtime API Guide", 2025-2026. https://developers.openai.com/api/docs/guides/realtime-vad
6. **LiveKit Agents**: LiveKit, "Turns overview", "Sequential Pipeline Architecture", 2025-2026. https://docs.livekit.io/agents/logic/turns
7. **Pipecat SmartTurn**: Pipecat/Daily, "Smart Turn Detection", "Smart Turn v3", 2025-2026. https://docs.pipecat.ai/pipecat-cloud/guides/smart-turn; https://huggingface.co/pipecat-ai/smart-turn-v3
8. **VAD 对比**: Picovoice, "Best Voice Activity Detection 2026: Cobra vs Silero vs WebRTC", 2026. https://picovoice.ai/blog/best-voice-activity-detection-vad
9. **VAD 窗口实验**: "Window Size Versus Accuracy Experiments in Voice Activity Detectors" (arXiv:2601.17270v1), 2026.
10. **Barge-In 指南**: FutureAGI, "Voice AI Barge-In and Turn-Taking: 2026 Guide", 2026. https://futureagi.com/blog/voice-ai-barge-in-turn-taking-2026
11. **打断处理**: Hamming AI, "Voice Agent Interruption Handling: Barge-In, Backchannels, and Turn Detection", 2026. https://hamming.ai/resources/voice-agent-interruption-handling-runbook
12. **全双工系统**: EmergentMind, "Full-Duplex Dialogue System", 2025. https://www.emergentmind.com/topics/full-duplex-dialogue-system
13. **全双工语音模型**: DinoDial, "What Full-Duplex Speech Models Actually Change in Voice AI", 2026. https://dinodial.ai/full-duplex-speech-models
14. **WebRTC vs WebSocket**: Apptitude, "AI Voice Agent Transport: WebRTC vs WebSocket Decision Guide", 2026. https://apptitude.io/blog/ai-voice-agent-webrtc-vs-websocket-transport
15. **WebRTC 优势**: LiveKit, "Why WebRTC beats WebSockets for realtime voice AI", 2025. https://livekit.com/blog/why-webrtc-beats-websockets-for-voice-ai-agents
16. **AEC 对比**: Coval, "Voice AI Echo Cancellation: Causes, Fixes, and Best Practices", 2025-2026. https://www.coval.ai/blog/voice-ai-echo-cancellation
17. **WebRTC AEC3**: Switchboard Audio, "How WebRTC AEC3 Works", 2025. https://switchboard.audio/hub/how-webrtc-aec3-works
18. **Opus Codec**: Wikipedia, "Opus (audio format)"; XiphWiki, "Opus Recommended Settings". https://wiki.xiph.org/Opus_Recommended_Settings
19. **延迟优化**: FutureAGI, "Optimize Voice Agent Latency: 12 Techniques for 2026", 2026. https://futureagi.com/blog/how-to-optimize-voice-agent-latency-2026
20. **语义端点检测**: "Improving endpoint detection in end-to-end streaming ASR" (arXiv:2505.17070v1), 2025.
21. **企业级语音 Agent**: "Building Enterprise Realtime Voice Agents from Scratch" (arXiv:2603.05413v1), 2026.
22. **万得 3C 会议**: Wind, "万得3C会议的音视频技术特点", 2025.
