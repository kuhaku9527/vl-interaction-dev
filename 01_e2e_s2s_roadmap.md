# 全双工语音对话端到端 S2S 技术路线研究报告

> **研究日期**: 2026-08-11  
> **研究范围**: 端到端语音对话模型、音频编解码器、全双工架构、turn-taking 机制  
> **覆盖市场**: OpenAI、Kyutai、智谱、阿里、Meta、Google、中科院、复旦等

---

## 目录

1. [三大架构路线总览](#三大架构路线总览)
2. [代表性模型详细分析](#代表性模型详细分析)
3. [关键论文与学术资源](#关键论文与学术资源)
4. [综合对比表](#综合对比表)
5. [对自研架构的可借鉴点分析](#对自研架构的可借鉴点分析)
6. [信息来源](#信息来源)

---

## 三大架构路线总览

截至 2026 年中，端到端语音对话系统已形成三条主要技术路线：

### 路线 1: 级联架构 (Cascade)
- **代表**: 传统 STT → LLM → TTS 流水线
- **优势**: 模块化、可独立升级、调试方便、生态成熟
- **劣势**: 延迟累积（通常 1-5 秒）、丢失副语言信息（情感、语调）
- **2026 现状**: 仍是生产环境默认方案，但通过流式 ASR + 低延迟 TTS 可将延迟降至亚秒级

### 路线 2: 音频原生 S2S (Audio-Native / Full-Duplex)
- **代表**: Moshi (Kyutai)、Hertz-dev (Standard Intelligence)、GPT-4o Realtime (OpenAI)
- **核心思路**: 音频输入 → 神经音频编解码器 → 离散 token → LLM → 音频 token → 解码器 → 音频输出
- **优势**: 亚 200ms 延迟、保留副语言信息、真正的全双工
- **劣势**: 推理能力弱于文本 LLM、调试困难、训练数据稀缺

### 路线 3: Thinker-Talker 架构 (双通道分离)
- **代表**: Qwen2.5-Omni (阿里)、Freeze-Omni (VITA 团队)、LLaMA-Omni (中科院)
- **核心思路**: Thinker (文本 LLM, 冻结或微调) 负责理解和推理 → Talker (语音生成模块) 负责语音合成
- **优势**: 保留文本 LLM 的推理能力、可注入 RAG 上下文、架构清晰
- **劣势**: 非真正的全双工、Thinker 和 Talker 之间存在协调延迟

---

## 代表性模型详细分析

### 1. GPT-4o Realtime API (OpenAI)

| 属性 | 详情 |
|------|------|
| **GitHub** | 闭源 API，无公开仓库 |
| **论文** | 无公开技术论文 |
| **发布时间** | 2024 年 10 月公测，2025 年正式发布 |
| **Star 数** | N/A (闭源) |
| **许可证** | 商业 API |
| **最新模型** | `gpt-realtime-2` (2026-05-07) |

**架构设计**:
- **连接层**: WebSocket (`wss://`) 或 WebRTC，支持持久化事件驱动连接
- **音频格式**: PCM 16-bit, 24kHz (高质量) 或 G.711 μ-law/a-law 8kHz (电话)
- **音频 Tokenization**: 原生音频推理，不经过文本中间层；模型直接处理音频 token
- **VAD (Voice Activity Detection)**: 内置语音活动检测，`input_audio_buffer.speech_started` 事件触发打断
- **Turn-taking**: 基于 VAD 的 phrase endpointing (话轮检测)，支持用户打断 (barge-in)
- **流式输出**: 双向音频流，同时输出文本和音频
- **延迟**: < 500ms 端到端
- **Function Calling**: 原生支持工具调用

**打断机制**:
- `input_audio_buffer.speech_started` 事件触发客户端立即停止音频播放
- 模拟人类被插话时停止说话的行为
- 已知问题: 快速连续打断可能导致模型"跑偏"和幻觉

**定价** (截至 2026):
- `gpt-realtime`: $32/1M 音频输入 token, $64/1M 音频输出 token
- 缓存输入 token: $0.40/1M

**关键洞察**:
- 将传统 3-5 秒延迟的级联流水线压缩为单一 S2S 流式处理
- 原生音频推理使模型能检测情感、处理打断
- 支持 `gpt-realtime-translate` (翻译)、`gpt-realtime-whisper` (转写) 等变体

---

### 2. Moshi (Kyutai)

| 属性 | 详情 |
|------|------|
| **GitHub** | [kyutai-labs/moshi](https://github.com/kyutai-labs/moshi) |
| **Star 数** | ~10,800 |
| **论文** | "Moshi: a speech-text foundation model for real-time dialogue" (Kyutai, 2024.09) |
| **arXiv** | [2410.00037](https://arxiv.org/abs/2410.00037) |
| **许可证** | Apache 2.0 (权重 CC-BY 4.0) |
| **最新更新** | 活跃维护，支持 PyTorch / MLX / Rust(Candle) 三后端 |

**架构设计**:

```
┌─────────────────────────────────────────────────────┐
│                    Moshi 架构                         │
│                                                       │
│  用户音频 ──→ Mimi Encoder ──→ Audio Tokens (User)    │
│                                      │                │
│                                      ▼                │
│              ┌──────────────────────────┐            │
│              │   Helium 7B LLM          │            │
│              │   (Temporal Transformer) │            │
│              │   + Depth Transformer    │            │
│              └──────────────────────────┘            │
│                   │              │                    │
│                   ▼              ▼                    │
│         Audio Tokens      Text Tokens                │
│         (Moshi output)    (Inner Monologue)          │
│                   │                                  │
│                   ▼                                  │
│         Mimi Decoder ──→ 输出音频                     │
└─────────────────────────────────────────────────────┘
```

**核心组件**:

1. **Mimi 神经音频编解码器**:
   - 语义-声学联合建模
   - 帧率: 12.5 Hz (每秒 12.5 步)
   - 码率: 1.1 kbps
   - 每步 8 个子序列 (Q=8 codebooks)
   - 残差向量量化 (RVQ)
   - 流式处理，专为实时对话设计

2. **Helium 7B LLM**:
   - Kyutai 自研 7B 参数语言模型
   - 在 2.1 万亿 token 上预训练
   - 同时建模文本和音频 token

3. **双音频流建模** (核心创新):
   - **用户流**: 来自音频输入的 token
   - **系统流 (Moshi)**: 模型自回归生成的 token
   - 两条流同时建模，实现真正的全双工

4. **Inner Monologue (内心独白)**:
   - 并行的文本 token 流
   - 模型在"说话"的同时用文本"思考"
   - 将文本 LLM 的推理能力注入语音模型
   - 这是 Moshi 最关键的创新

**性能指标**:
- 端到端延迟: ~200ms (在 L4 GPU 上)
- 支持打断和反向通道 (backchannel)
- 自然的话轮转换

**推理后端**:
- PyTorch (bf16)
- MLX (Apple Silicon, q4/q8/bf16)
- Rust/Candle (q8/bf16)

**纯 CPU 推理**: 理论可行但极慢；MLX 后端在 Apple Silicon 上可运行

**Windows 兼容性**: PyTorch 后端支持，Rust/Candle 后端支持

---

### 3. GLM-4-Voice (智谱 AI)

| 属性 | 详情 |
|------|------|
| **GitHub** | [THUDM/GLM-4-Voice](https://github.com/THUDM/GLM-4-Voice) |
| **Star 数** | ~3,000+ (估算) |
| **论文** | "GLM-4-Voice: Towards Intelligent and Human-Like End-to-End Spoken Chatbot" (2024.12) |
| **arXiv** | [2412.02612](https://arxiv.org/abs/2412.02612) |
| **许可证** | 模型权重需遵循 Model License Agreement，代码 Apache 2.0 |
| **模型大小** | 9B 参数 |

**架构设计**:

GLM-4-Voice 采用三组件架构：

1. **Speech Encoder (语音编码器)**:
   - 将输入语音转换为离散 token
   - 支持中英文

2. **GLM-4 LLM Backbone**:
   - 基于 GLM-4 文本大模型
   - 处理语音 token + 文本 token

3. **Speech Decoder (语音解码器)**:
   - 将模型输出 token 转换为语音波形
   - 支持情感、语调、语速、方言控制

**关键特性**:
- 端到端中英文语音理解和生成
- 实时语音对话
- 情感感知与表达
- 可根据用户指令改变情感、语调、语速、方言
- 需要 GPU 进行推理

**推理要求**:
- 需要 GPU (Decoder 模型不支持通过 transformers 初始化)
- 模型需通过 git-lfs 单独下载

**纯 CPU 推理**: 不可行 (需要 GPU)

**Windows 兼容性**: 支持 (基于 PyTorch)

---

### 4. Qwen2.5-Omni (阿里云 / Qwen 团队)

| 属性 | 详情 |
|------|------|
| **GitHub** | [QwenLM/Qwen2.5-Omni](https://github.com/QwenLM/Qwen2.5-Omni) |
| **Star 数** | ~4,100 |
| **论文** | "Qwen2.5-Omni Technical Report" (2025) |
| **许可证** | Apache 2.0 |
| **模型大小** | 7B (另有 3B 版本) |
| **发布时间** | 2025 年 4 月 |

**架构设计 — Thinker-Talker**:

```
┌──────────────────────────────────────────────────┐
│              Qwen2.5-Omni 架构                     │
│                                                    │
│  文本/图像/音频/视频 ──→ Thinker (多模态 LLM)       │
│                              │                     │
│                              ├──→ 文本输出          │
│                              │                     │
│                              ▼                     │
│                    Hidden States                   │
│                              │                     │
│                              ▼                     │
│                    Talker (双轨 AR 模型)            │
│                              │                     │
│                              ▼                     │
│                    流式音频输出                     │
│                                                    │
│  关键技术: TMRoPE (时间对齐多模态位置编码)          │
│            Block-wise Streaming Processing         │
└──────────────────────────────────────────────────┘
```

**核心创新**:

1. **Thinker-Talker 分离架构**:
   - Thinker: 多模态大语言模型，处理文本/图像/音频/视频，输出推理结果和 hidden states
   - Talker: 双轨自回归模型，将 Thinker 的 hidden representations 转换为流式音频 token
   - 类似人类"大脑决定说什么，发声系统表达"

2. **TMRoPE (Time-aligned Multimodal RoPE)**:
   - 同步视频和音频的时间戳
   - 保持多模态时序一致性

3. **Block-wise Streaming**:
   - 块级流式处理
   - 支持实时语音生成

**性能**:
- 端到端语音指令遵循能力接近文本输入水平 (MMLU, GSM8K)
- OmniBench 多模态推理 SOTA
- 支持实时语音对话

**推理要求**:
- 7B 版本需要 GPU
- 3B 版本可在更多平台运行
- 支持 OpenVINO 在 Intel AI PC 上部署 (NPU/GPU/CPU)

**纯 CPU 推理**: 3B 版本通过 OpenVINO 可在 CPU 上运行

**Windows 兼容性**: 支持 (PyTorch + OpenVINO)

---

### 5. Freeze-Omni (VITA 团队)

| 属性 | 详情 |
|------|------|
| **GitHub** | [VITA-MLLM/Freeze-Omni](https://github.com/VITA-MLLM/Freeze-Omni) |
| **Star 数** | ~500+ (估算) |
| **论文** | "Freeze-Omni: A Smart and Low Latency Speech-to-speech Dialogue Model with Frozen LLM" (2024.11) |
| **arXiv** | [2411.00774](https://arxiv.org/abs/2411.00774) |
| **许可证** | 开源 |
| **基座 LLM** | Qwen2-7B-Instruct (冻结) |

**架构设计**:

```
┌──────────────────────────────────────────────┐
│              Freeze-Omni 架构                  │
│                                                │
│  语音输入 ──→ Chunk-wise Streaming Encoder     │
│                      │                         │
│                      ▼                         │
│                  Adapter                        │
│                      │                         │
│                      ▼                         │
│          ┌──────────────────┐                  │
│          │  Frozen LLM       │ ← 完全冻结      │
│          │  (Qwen2-7B)      │   不参与训练     │
│          └──────────────────┘                  │
│               │          │                     │
│               ▼          ▼                     │
│         文本输出     Hidden States              │
│                         │                      │
│                         ▼                      │
│               Streaming Speech Decoder         │
│                         │                      │
│                         ▼                      │
│                    语音输出                     │
│                                                │
│  状态预测: State 0 (继续接收)                   │
│            State 1 (用户打断)                   │
│            State 2 (话轮结束)                   │
└──────────────────────────────────────────────┘
```

**核心创新**:

1. **LLM 完全冻结**:
   - 训练过程中 LLM 参数完全不更新
   - 避免灾难性遗忘 (catastrophic forgetting)
   - 保持原始文本 LLM 的全部智能

2. **Chunk-wise Streaming Encoder**:
   - 分块流式语音编码器
   - 由下采样卷积层 + Transformer 层组成
   - 快速响应输入语音

3. **三状态预测机制**:
   - State 0: 继续接收语音
   - State 1: 用户打断对话，LLM 重新生成
   - State 2: 话轮结束，无需打断
   - 基于 LLM 最后一层 hidden state 预测

4. **三阶段训练策略**:
   - 保证声学鲁棒性

**关键优势**:
- 训练资源需求低 (LLM 冻结)
- 保持 LLM 原始智能
- 支持流式输入/输出
- 内置打断检测

**推理要求**: 需要 GPU

**纯 CPU 推理**: 不可行

**Windows 兼容性**: 支持 (PyTorch)

---

### 6. LLaMA-Omni / LLaMA-Omni 2 (中科院计算所)

| 属性 | 详情 |
|------|------|
| **GitHub** | Hugging Face: [ICTNLP/Llama-3.1-8B-Omni](https://huggingface.co/ICTNLP/Llama-3.1-8B-Omni) |
| **Star 数** | ~418 likes (HF) |
| **论文** | "LLaMA-Omni: Seamless Speech Interaction with Large Language Models" (2024.09) |
| **arXiv** | [2409.06666](https://arxiv.org/abs/2409.06666) |
| **许可证** | 开源 |
| **基座 LLM** | Llama-3.1-8B-Instruct |

**架构设计**:

```
┌──────────────────────────────────────────────┐
│              LLaMA-Omni 架构                   │
│                                                │
│  语音指令 ──→ Speech Encoder (Whisper)         │
│                      │                         │
│                      ▼                         │
│                Speech Adaptor                   │
│                      │                         │
│                      ▼                         │
│          ┌──────────────────┐                  │
│          │  Llama-3.1-8B    │                  │
│          │  (LLM Backbone)  │                  │
│          └──────────────────┘                  │
│               │          │                     │
│               ▼          ▼                     │
│         文本输出    Hidden States               │
│                         │                      │
│                         ▼                      │
│          Streaming Speech Decoder              │
│          (非自回归 Transformer)                │
│                         │                      │
│                         ▼                      │
│                    语音输出                     │
└──────────────────────────────────────────────┘
```

**LLaMA-Omni 2 升级** (ACL 2025):
- 模型规模: 0.5B 到 14B 参数系列
- 集成自回归 TTS 语言模型 + Causal Flow Matching
- 流式语音生成

**关键特性**:
- 延迟低至 226ms
- 同时生成文本和语音响应
- 训练仅需 4 个 GPU，不到 3 天
- 数据集: InstructionS2S-200K (20 万条语音指令)
- 不先将语音转写为文本，LLM 直接从语音指令解码文本响应

**推理要求**: 需要 GPU (8B 模型)

**纯 CPU 推理**: 不可行

**Windows 兼容性**: 支持 (PyTorch)

---

### 7. SpeechGPT / SpeechGPT 2 (复旦大学)

| 属性 | 详情 |
|------|------|
| **GitHub** | [0nutation/SpeechGPT](https://github.com/0nutation/SpeechGPT) (v1), [OpenMOSS/SpeechGPT-2.0-preview](https://github.com/OpenMOSS/SpeechGPT-2.0-preview) (v2) |
| **Star 数** | ~1,500+ (v1) |
| **论文** | "SpeechGPT: Empowering Large Language Models with Intrinsic Cross-Modal Conversational Abilities" (EMNLP 2023) |
| **arXiv** | [2305.11000](https://arxiv.org/abs/2305.11000) |
| **许可证** | 开源 |

**SpeechGPT v1 架构**:
- 第一个具有内在跨模态对话能力的多模态 LLM
- 使用自监督语音模型进行语音离散化
- 将离散语音 token 扩展到 LLM 词汇表
- 级联方式: Speech → Text → Speech (非真正的端到端)

**SpeechGPT 2.0-preview 架构**:
- 真正的端到端口语对话语言模型
- **超低比特率流式语音 Codec**: 联合建模语义和声学
- **多 LM Head 架构**: 一个 LLM 同时解码文本输出和语音输出
- Patch Decoder: 自回归语言模型，每时间步生成多个 RVQ codec token
- 百万小时级语音数据训练
- 毫秒级低延迟响应
- 支持自然流畅的实时打断交互
- 语音风格泛化能力强

**关键特性**:
- 高效的语音数据爬取系统
- 多功能语音数据清洗流水线
- 多粒度语音数据标注系统

**推理要求**: 需要 GPU

**纯 CPU 推理**: 不可行

**Windows 兼容性**: 支持 (PyTorch)

---

### 8. Spirit LM (Meta / FAIR)

| 属性 | 详情 |
|------|------|
| **GitHub** | [facebookresearch/spiritlm](https://github.com/facebookresearch/spiritlm) |
| **Star 数** | ~500+ (估算) |
| **论文** | "SPIRIT-LM: Interleaved Spoken and Written Language Model" (TACL 2025, EMNLP 2024) |
| **arXiv** | [2402.05755](https://arxiv.org/abs/2402.05755) |
| **许可证** | FAIR Noncommercial Research License |
| **模型大小** | 7B 参数 |

**架构设计**:

```
┌──────────────────────────────────────────────┐
│              Spirit LM 架构                    │
│                                                │
│  文本 + 语音 token 交错序列                     │
│         │                                      │
│         ▼                                      │
│  ┌──────────────────────────┐                 │
│  │  7B Pretrained Text LM   │                 │
│  │  (持续训练: 文本+语音)    │                 │
│  └──────────────────────────┘                 │
│         │                                      │
│         ▼                                      │
│  文本 + 语音 token 交错输出                     │
│                                                │
│  两个版本:                                      │
│  - BASE: 语音音素 token (语义)                  │
│  - EXPRESSIVE: 语音音素 + 音高 + 风格 token     │
│                                                │
│  关键: Word-level Interleaving                 │
│  文本序列: 10-30 词                             │
│  语音序列: 5-15 秒                              │
└──────────────────────────────────────────────┘
```

**核心创新**:
- 文本和语音 token 作为单一 token 流交错训练
- 基于 7B 预训练文本 LM 扩展语音模态
- Word-level interleaving: 自动构建的语音-文本平行语料
- BASE 版本: 语音音素 token
- EXPRESSIVE 版本: 额外包含音高和风格 token，保留更多副语言信息
- RoPE 基频从 10,000 提升到 100,000 以支持长上下文

**关键特性**:
- 自由混合文本和语音
- 情感保留 (EXPRESSIVE 版本)
- 支持 ASR、TTS、语音分类

**推理要求**: 需要 GPU

**纯 CPU 推理**: 不可行

**Windows 兼容性**: 支持 (PyTorch)

---

### 9. AudioLM / AudioPaLM (Google)

| 属性 | 详情 |
|------|------|
| **GitHub** | 未开源 |
| **论文** | AudioLM (2022), AudioPaLM (2023) |
| **arXiv** | AudioPaLM: [2306.12925](https://arxiv.org/abs/2306.12925) |
| **许可证** | 闭源 |

**AudioLM 架构**:
- 将音频生成视为语言建模任务
- **语义 Token**: 自监督 w2v-BERT 模型提取，捕获高层结构
- **声学 Token**: SoundStream 神经编解码器 (RVQ, Q=12, 每层 1024 codes, 50Hz)，捕获细节
- 分层建模: 先语义后声学

**SoundStream 编解码器**:
- 全卷积编码器-解码器 + RVQ
- 对抗训练 + 重建损失
- 3 kbps 质量超过 Opus 12 kbps
- 单一模型覆盖 3-18 kbps

**AudioPaLM**:
- 融合 PaLM-2 (文本) + AudioLM (语音)
- 统一多模态架构
- 支持 ASR、AST (语音翻译)、S2ST
- 用文本 LLM 权重初始化可提升语音任务性能

**关键贡献**:
- 奠定了"音频 tokenization + 语言模型"范式
- SoundStream 成为后续几乎所有神经编解码器的基础

---

### 10. Hertz-dev (Standard Intelligence)

| 属性 | 详情 |
|------|------|
| **GitHub** | [si.inc/hertz-dev](https://github.com/standard-intelligence/hertz-dev) (参考) |
| **Star 数** | ~1,000+ (估算) |
| **论文** | 无正式论文 |
| **发布时间** | 2024 年 11 月 |
| **许可证** | Apache 2.0 |
| **模型大小** | ~8.5B 参数 |

**架构设计**:
- Transformer-based 实时音频模型
- 纯语音到语音，无文本中间层
- 在全双工会话音频数据上训练
- 类似 Moshi 的全双工设计

**关键特性**:
- 真正的全双工 S2S
- 原始音频输入 → 原始音频输出
- 研究级质量，生产部署基准有限
- VRAM 估算: 8-12GB

**推理要求**: GPU (8-12GB VRAM)

**纯 CPU 推理**: 不可行

**Windows 兼容性**: 支持 (PyTorch)

---

### 11. 其他值得关注的模型

| 模型 | 机构 | 特点 | 状态 |
|------|------|------|------|
| **Mini-Omni / Mini-Omni2** | 独立研究者 | 开源 GPT-4o 替代，支持视觉+语音+双工 | GitHub 开源 |
| **IntrinsicVoice** | 复旦大学 | GroupFormer 架构，缩短语音 token 序列 | arXiv 2024 |
| **Qwen2-Audio** | 阿里 | 音频理解 (非生成)，语音聊天+音频分析 | GitHub 开源 |
| **Fish Agent / Fish Speech S2 Pro** | Fish Audio | Dual-AR 架构，100ms TTFA，80+ 语言 | GitHub 开源 |
| **Sesame CSM** | Sesame | 全双工语音模型 | 商业 |
| **Kimi-Audio** | 月之暗面 | 音频理解和生成 | 2025 |
| **Step-Audio** | 阶跃星辰 | 统一理解和生成的智能语音交互 | 2025 |
| **Baichuan-Audio** | 百川智能 | 端到端语音交互统一框架 | 2025 |
| **SLAM-Omni** | - | 音色可控语音交互，单阶段训练 | arXiv 2024 |
| **OmniFlatten** | - | 端到端 GPT 模型无缝语音对话 | 2024 |
| **KE-Omni** | - | 6 万+ 小时合成语音对话数据扩展 SFT | arXiv 2024 |

---

## 关键论文与学术资源

### 综述论文

| 论文 | 会议/期刊 | 年份 | 链接 |
|------|-----------|------|------|
| "Recent Advances in Speech Language Models: A Survey" | ACL 2025 | 2025 | [arXiv:2410.03751](https://arxiv.org/abs/2410.03751) |
| "When Large Language Models Meet Speech: A Survey on Integration Approaches" | ACL 2025 Findings | 2025 | ACL Anthology |
| "Speech-to-Speech Models in 2026: Three Architectural Bets" | Blog | 2026 | [ai.ksopyla.com](https://ai.ksopyla.com/posts/voice-to-voice-models-2026-review) |

### 核心模型论文

| 论文 | 机构 | 年份 | 链接 |
|------|------|------|------|
| Moshi: a speech-text foundation model for real-time dialogue | Kyutai | 2024 | [arXiv:2410.00037](https://arxiv.org/abs/2410.00037) |
| GLM-4-Voice: Towards Intelligent and Human-Like End-to-End Spoken Chatbot | 智谱 AI | 2024 | [arXiv:2412.02612](https://arxiv.org/abs/2412.02612) |
| Qwen2.5-Omni Technical Report | 阿里云 | 2025 | Hugging Face |
| Freeze-Omni: A Smart and Low Latency Speech-to-speech Dialogue Model with Frozen LLM | VITA 团队 | 2024 | [arXiv:2411.00774](https://arxiv.org/abs/2411.00774) |
| LLaMA-Omni: Seamless Speech Interaction with Large Language Models | 中科院计算所 | 2024 | [arXiv:2409.06666](https://arxiv.org/abs/2409.06666) |
| LLaMA-Omni 2: LLM-based Real-time Spoken Chatbot | 中科院计算所 | 2025 | ACL 2025 |
| SpeechGPT: Empowering LLMs with Intrinsic Cross-Modal Conversational Abilities | 复旦大学 | 2023 | [arXiv:2305.11000](https://arxiv.org/abs/2305.11000) |
| SPIRIT-LM: Interleaved Spoken and Written Language Model | Meta FAIR | 2024 | [arXiv:2402.05755](https://arxiv.org/abs/2402.05755) |
| AudioPaLM: A Large Language Model That Can Speak and Listen | Google | 2023 | [arXiv:2306.12925](https://arxiv.org/abs/2306.12925) |
| IntrinsicVoice: Empowering LLMs with Intrinsic Real-time Voice Interaction Abilities | 复旦大学 | 2024 | [arXiv:2410.08035](https://arxiv.org/abs/2410.08035) |

### 音频编解码器论文

| 论文 | 机构 | 年份 | 关键指标 |
|------|------|------|----------|
| SoundStream: An End-to-End Neural Audio Codec | Google | 2021 | 3-18 kbps, RVQ, 50Hz |
| High Fidelity Neural Audio Compression (EnCodec) | Meta | 2022 | 多码率, RVQ, 流式 |

### 架构对比与工程实践

| 资源 | 类型 | 年份 | 链接 |
|------|------|------|------|
| "Cascaded Voice Agents vs Speech-to-Speech: Architecture Tradeoffs in 2026" | Blog | 2026 | [gradium.ai](https://gradium.ai/content/cascaded-voice-agent-vs-speech-to-speech-2026) |
| "Speech-to-Speech vs Cascaded Voice AI: Which Architecture Should You Deploy?" | Blog | 2026 | [coval.ai](https://www.coval.ai/blog/speech-to-speech-vs-cascaded-voice-ai-which-architecture-should-you-deploy) |
| "Building Enterprise Realtime Voice Agents from Scratch" | arXiv | 2025 | [arXiv:2603.05413](https://arxiv.org/abs/2603.05413) |
| "Real-time speech-to-speech translation" | Google Research | 2025 | [research.google](https://research.google/blog/real-time-speech-to-speech-translation) |
| Awesome-Speech-Language-Model | GitHub | 持续更新 | [ddlBoJack/Awesome-Speech-Language-Model](https://github.com/ddlBoJack/Awesome-Speech-Language-Model) |
| Awesome-SpeechLM-Survey | GitHub | 持续更新 | [dreamtheater123/awesome-speechlm-survey](https://github.com/dreamtheater123/awesome-speechlm-survey) |

---

## 综合对比表

### 核心架构对比

| 模型 | 架构路线 | 参数规模 | 编解码器 | 帧率 | 全双工 | 流式输出 | 开源 |
|------|----------|----------|----------|------|--------|----------|------|
| **GPT-4o Realtime** | 音频原生 S2S | 未公开 | 原生音频 | 未公开 | ✅ | ✅ | ❌ |
| **Moshi** | 音频原生 S2S | 7B | Mimi (12.5Hz, 1.1kbps) | 12.5 Hz | ✅ | ✅ | ✅ |
| **GLM-4-Voice** | 音频原生 S2S | 9B | 自研 | 未公开 | ❌ (半双工) | ✅ | ✅ |
| **Qwen2.5-Omni** | Thinker-Talker | 7B/3B | 自研 | 流式 | ❌ | ✅ | ✅ |
| **Freeze-Omni** | Thinker-Talker (Frozen LLM) | 7B (LLM冻结) | 自研 | 流式 | ❌ | ✅ | ✅ |
| **LLaMA-Omni** | Thinker-Talker | 8B | Whisper Encoder + 自研 Decoder | - | ❌ | ✅ | ✅ |
| **SpeechGPT 2** | 音频原生 S2S | 未公开 | 自研超低比特率 Codec | 流式 | ✅ | ✅ | ✅ |
| **Spirit LM** | 文本-语音交错 | 7B | 语音音素 tokenizer | - | ❌ | ❌ | ✅ |
| **Hertz-dev** | 音频原生 S2S | ~8.5B | 自研 | - | ✅ | ✅ | ✅ |
| **AudioPaLM** | 融合架构 | 未公开 | SoundStream (50Hz) | 50 Hz | ❌ | ❌ | ❌ |

### 延迟与推理对比

| 模型 | 端到端延迟 | GPU 需求 | CPU 推理 | Windows | 训练成本 |
|------|-----------|----------|----------|---------|----------|
| **GPT-4o Realtime** | <500ms | API 调用 | N/A | ✅ (Web) | 极高 |
| **Moshi** | ~200ms | L4 (24GB) | 理论可行 (极慢) | ✅ | 高 |
| **GLM-4-Voice** | 未公开 | 需要 GPU | ❌ | ✅ | 高 |
| **Qwen2.5-Omni** | 流式低延迟 | 7B: GPU; 3B: 可 CPU | ✅ (3B+OpenVINO) | ✅ | 高 |
| **Freeze-Omni** | 低延迟 | 需要 GPU | ❌ | ✅ | 低 (LLM冻结) |
| **LLaMA-Omni** | ~226ms | 4 GPU 训练 | ❌ | ✅ | 低 (4 GPU, 3天) |
| **SpeechGPT 2** | 毫秒级 | 需要 GPU | ❌ | ✅ | 高 |
| **Spirit LM** | 非实时 | 需要 GPU | ❌ | ✅ | 高 (2周) |
| **Hertz-dev** | 实时 | 8-12GB VRAM | ❌ | ✅ | 高 |

### Turn-taking 与打断机制对比

| 模型 | Turn-taking 方式 | Barge-in 支持 | 实现机制 |
|------|------------------|---------------|----------|
| **GPT-4o Realtime** | VAD + Phrase Endpointing | ✅ | `speech_started` 事件 → 停止播放 |
| **Moshi** | 双流建模 (用户流+系统流) | ✅ | 架构原生支持，同时建模两条音频流 |
| **GLM-4-Voice** | 基于语音活动检测 | 部分 | 未详细公开 |
| **Qwen2.5-Omni** | Thinker-Talker 协调 | 部分 | 流式处理，非原生全双工 |
| **Freeze-Omni** | 三状态预测 (0/1/2) | ✅ | State 1 触发打断，LLM 重新生成 |
| **LLaMA-Omni** | 请求-响应模式 | ❌ | 非原生打断支持 |
| **SpeechGPT 2** | 实时打断交互 | ✅ | 端到端原生支持 |
| **Hertz-dev** | 全双工原生 | ✅ | 架构原生支持 |

---

## 对自研架构的可借鉴点分析

### 1. 架构选择建议

基于 2026 年的技术格局，推荐 **Thinker-Talker 分离架构** 作为自研起点：

**理由**:
- **保留 LLM 智能**: Freeze-Omni 证明冻结 LLM 可以完全保留文本推理能力
- **训练成本可控**: LLaMA-Omni 仅需 4 GPU 训练 3 天
- **可渐进式升级**: Thinker 可独立升级到更强的 LLM
- **RAG 友好**: 文本中间表示便于注入检索上下文
- **调试友好**: 文本输出可独立验证

### 2. 音频编解码器选择

| 选项 | 优势 | 劣势 | 推荐场景 |
|------|------|------|----------|
| **Mimi** (Moshi) | 12.5Hz 低帧率, 1.1kbps, 开源 | 需适配自研 LLM | 追求最低延迟 |
| **SoundStream** | 成熟稳定, 50Hz, 多码率 | 帧率高, token 序列长 | 高质量音频 |
| **EnCodec** (Meta) | 开源, 多码率, 流式 | 帧率较高 | 通用场景 |
| **自研 Codec** | 完全可控 | 研发成本高 | 差异化需求 |

**推荐**: 初期使用 Mimi (12.5Hz 低帧率天然适合 LLM 处理)，后续可替换为自研。

### 3. Turn-taking 与打断机制

**Moshi 双流建模** 是最优雅的方案:
- 用户音频流 + 系统音频流同时建模
- 架构层面原生支持全双工
- 不需要额外的 VAD 模块

**Freeze-Omni 三状态预测** 是最实用的方案:
- State 0/1/2 简单清晰
- 基于 LLM hidden state 预测
- 易于实现和调试

**推荐**: 初期采用 Freeze-Omni 的状态预测方案，成熟后演进到 Moshi 的双流建模。

### 4. Inner Monologue 机制

Moshi 的 Inner Monologue 是最值得借鉴的创新:
- 模型在生成语音的同时生成文本
- 文本流作为"思考"过程，提升推理质量
- 文本流也可用于日志、调试、RAG 上下文注入

**推荐**: 在 Thinker-Talker 架构中，Thinker 输出文本 + hidden states，Talker 基于两者生成语音。

### 5. 训练策略

| 策略 | 来源 | 优势 |
|------|------|------|
| **LLM 冻结 + 外挂语音模块** | Freeze-Omni | 最低训练成本，保留 LLM 智能 |
| **语音-文本交错预训练** | Spirit LM | 模态对齐自然 |
| **合成语音对话数据** | KE-Omni (6万小时) | 解决数据稀缺 |
| **多阶段训练** | Qwen2.5-Omni | 渐进式能力构建 |

**推荐**: 阶段 1: 冻结 LLM + 训练语音编解码器对齐；阶段 2: 解冻部分 LLM 层微调；阶段 3: 合成对话数据扩展。

### 6. 工程落地建议

1. **流式处理**: 采用 chunk-wise streaming (参考 Freeze-Omni)
2. **推理优化**: 关注 MLX (Apple Silicon) 和 OpenVINO (Intel) 的 CPU 推理方案
3. **WebRTC 集成**: 参考 OpenAI Realtime API 的 WebSocket/WebRTC 双连接方案
4. **VAD 集成**: 初期使用独立 VAD 模块，后期融入模型
5. **评估体系**: 建立音频原生评估指标 (非文本中介)

### 7. 关键风险

| 风险 | 描述 | 缓解措施 |
|------|------|----------|
| **推理能力下降** | 音频原生模型推理弱于文本 LLM | 采用 Thinker-Talker 分离架构 |
| **训练数据稀缺** | 语音对话数据远少于文本 | 合成数据 + 数据增强 |
| **打断导致幻觉** | 快速打断可能使模型跑偏 | 状态机 + 上下文截断策略 |
| **调试困难** | 音频原生模型缺乏文本中间层 | 保留 Inner Monologue 文本流 |
| **GPU 成本** | 实时推理需要 GPU | 模型量化 + 蒸馏 + CPU 推理优化 |

---

## 信息来源

### 论文与学术资源
1. Défossez, A. et al. "Moshi: a speech-text foundation model for real-time dialogue." Kyutai, 2024. arXiv:2410.00037
2. Zeng, A. et al. "GLM-4-Voice: Towards Intelligent and Human-Like End-to-End Spoken Chatbot." 2024. arXiv:2412.02612
3. Qwen Team. "Qwen2.5-Omni Technical Report." Alibaba Cloud, 2025.
4. Wang, X. et al. "Freeze-Omni: A Smart and Low Latency Speech-to-speech Dialogue Model with Frozen LLM." 2024. arXiv:2411.00774
5. Fang, Q. et al. "LLaMA-Omni: Seamless Speech Interaction with Large Language Models." 2024. arXiv:2409.06666
6. Fang, Q. et al. "LLaMA-Omni 2: LLM-based Real-time Spoken Chatbot with Autoregressive Streaming Speech Synthesis." ACL 2025.
7. Zhang, D. et al. "SpeechGPT: Empowering Large Language Models with Intrinsic Cross-Modal Conversational Abilities." EMNLP 2023. arXiv:2305.11000
8. Nguyen, T.A. et al. "SPIRIT-LM: Interleaved Spoken and Written Language Model." TACL 2025. arXiv:2402.05755
9. Rubenstein, P. et al. "AudioPaLM: A Large Language Model That Can Speak and Listen." 2023. arXiv:2306.12925
10. Borsos, Z. et al. "AudioLM: a Language Modeling Approach to Audio Generation." 2022.
11. Zeghidour, N. et al. "SoundStream: An End-to-End Neural Audio Codec." 2021. arXiv:2107.03312
12. Zhang, X. et al. "IntrinsicVoice: Empowering LLMs with Intrinsic Real-time Voice Interaction Abilities." 2024. arXiv:2410.08035
13. Cui, W. et al. "Recent Advances in Speech Language Models: A Survey." ACL 2025. arXiv:2410.03751

### GitHub 仓库
14. [kyutai-labs/moshi](https://github.com/kyutai-labs/moshi) - Moshi 官方仓库 (~10.8k stars)
15. [THUDM/GLM-4-Voice](https://github.com/THUDM/GLM-4-Voice) - GLM-4-Voice 官方仓库
16. [QwenLM/Qwen2.5-Omni](https://github.com/QwenLM/Qwen2.5-Omni) - Qwen2.5-Omni 官方仓库 (~4.1k stars)
17. [VITA-MLLM/Freeze-Omni](https://github.com/VITA-MLLM/Freeze-Omni) - Freeze-Omni 官方仓库
18. [0nutation/SpeechGPT](https://github.com/0nutation/SpeechGPT) - SpeechGPT v1
19. [OpenMOSS/SpeechGPT-2.0-preview](https://github.com/OpenMOSS/SpeechGPT-2.0-preview) - SpeechGPT 2.0
20. [facebookresearch/spiritlm](https://github.com/facebookresearch/spiritlm) - Spirit LM
21. [QwenLM/Qwen2-Audio](https://github.com/QwenLM/Qwen2-Audio) - Qwen2-Audio
22. [ddlBoJack/Awesome-Speech-Language-Model](https://github.com/ddlBoJack/Awesome-Speech-Language-Model) - 语音语言模型资源汇总
23. [dreamtheater123/awesome-speechlm-survey](https://github.com/dreamtheater123/awesome-speechlm-survey) - SpeechLM 综述资源
24. [fishaudio/fish-speech](https://github.com/fishaudio/fish-speech) - Fish Speech S2 Pro

### 技术博客与分析
25. "Speech-to-Speech Models in 2026: Three Architectural Bets and What Each Actually Gives You." Krzysztof Sopyła AI Blog, 2026.
26. "Cascaded Voice Agents vs Speech-to-Speech: Architecture Tradeoffs in 2026." Gradium, 2026.
27. "Speech-to-Speech vs Cascaded Voice AI: Which Architecture Should You Deploy?" Coval, 2026.
28. "Building Enterprise Realtime Voice Agents from Scratch." arXiv:2603.05413, 2025.
29. "Real-time speech-to-speech translation." Google Research Blog, 2025.
30. "GPT Realtime API: Redefining Human-AI Voice Interaction." Medium, 2025.
31. "OpenAI Realtime API: The Missing Manual." Latent Space, 2024.
32. "Neural Audio Codecs: Lyra, EnCodec, SoundStream and What Comes Next." Forasoft, 2024.
33. "Deploy Moshi, Sesame CSM, and Hertz-dev for Sub-300ms Voice Agents." Spheron Network, 2026.

### 官方文档
34. "Use the GPT Realtime API for speech and audio." Microsoft Learn / Azure OpenAI, 2026.
35. "Introducing gpt-realtime and Realtime API updates." OpenAI Blog, 2025.

---

> **文档版本**: v1.0  
> **最后更新**: 2026-08-11  
> **作者**: Alice 27 (Wind AI)  
> **状态**: 初稿完成，待补充更多模型细节和基准测试数据
