# Decision Token / Turn Decision 语义决策层 — 深度研究报告

> **研究日期**: 2026-08-11
> **研究范围**: 2024–2026 年学术前沿与工业实践
> **目标**: 为草稿第三层 "Decision Token" 机制提供理论与工程依据

---

## 目录

1. [Decision Token 机制](#1-decision-token-机制)
2. [语义 Turn 决策方法](#2-语义-turn-决策方法)
3. [与 ASR 流水线的集成](#3-与-asr-流水线的集成)
4. [与草稿的对照分析](#4-与草稿的对照分析)
5. [开源方案与学术前沿](#5-开源方案与学术前沿)
6. [综合建议](#6-综合建议)

---

## 1. Decision Token 机制

### 1.1 OpenAI Realtime API — Semantic VAD

**来源**: [OpenAI Realtime API 官方文档](https://developers.openai.com/api/docs/guides/realtime-vad), [Latent Space 深度分析](https://www.latent.space/p/realtime-api)

OpenAI 在 Realtime API 中提供了两种 VAD 模式：

| 模式 | 机制 | 特点 |
|------|------|------|
| `server_vad` | 基于静音时长的传统 VAD | 简单可靠，`silence_duration_ms` 可配置 |
| `semantic_vad` | **基于语义理解判断用户是否说完** | 核心创新，由模型根据话语内容判断 utterance 完整性 |

**Semantic VAD 的关键设计**：

- **不是特殊 token，而是模型内部判断**：Semantic VAD 并非在 token 序列中插入 `<turn_end>` 等特殊 token，而是模型在处理音频时**内部判断**用户是否已完成语义表达。
- **`eagerness` 参数**控制灵敏度：
  - `low`：让用户充分表达（最长等待 8s 静音）
  - `medium`/`auto`：平衡模式（最长等待 4s）
  - `high`：尽快响应（最长等待 2s）
- **事件驱动架构**：通过 `input_audio_buffer.speech_started` 和 `input_audio_buffer.speech_stopped` 事件通知上层。
- **实际延迟**：语义 token 被识别后，turn 在下一个 VAD tick（约 **~300ms**）内提交。

**关键洞察**：OpenAI 的 Semantic VAD 本质上是一种 **"隐式 decision token"**——模型在推理过程中内部判断语义完整性，但不暴露为显式 token。这避免了特殊 token 对文本 LLM 的干扰，但牺牲了可解释性和可控性。

> **来源**: OpenAI Developers, "Voice Activity Detection (VAD) | OpenAI API", 2024–2026; Latent Space, "OpenAI Realtime API: The Missing Manual", 2024; GlobalDev Tech Blog, "VAD vs event-triggered for AI speech-to-speech applications", 2025.

---

### 1.2 Moshi (Kyutai) — Inner Monologue 与层次化 Token 设计

**来源**: [Kyutai 官方论文](https://kyutai.org/Moshi.pdf), [arXiv:2410.00037](https://arxiv.org/html/2410.00037v2)

Moshi 是第一个**全双工（full-duplex）实时口语对话大模型**，其核心创新在于：

#### 层次化 Token 结构

Moshi 将语音分解为三层 token 链：

```
Text Tokens (时间对齐) → Semantic Tokens (VQ 第一层) → Acoustic Tokens (RVQ 剩余层)
```

- **Text Tokens**（~2048 个）：Whisper 时间戳对齐的文本 token，12.5Hz
- **Semantic Tokens**：Mimi codec 的第一层 VQ token，捕获语义内容
- **Acoustic Tokens**：Mimi codec 的剩余 7 层 RVQ token，捕获声学细节

#### Inner Monologue 机制

这是 Moshi 最核心的设计——**在生成语音之前，先预测时间对齐的文本 token 作为前缀**：

- 模型**不依赖外部 ASR**，而是内部生成文本作为"内心独白"
- 文本 token 与语音 token 在同一个自回归框架中联合建模
- 这显著提升了生成语音的语言质量（ablation 实验证明这是最重要的设计选择之一）

#### 双流并行架构

- **User Stream**：持续接收用户语音 token
- **System Stream**：持续生成系统语音 token
- 两个流**并行处理**，无需显式 speaker turn 分段
- 天然支持**重叠语音、打断、插话**等复杂对话动态

#### 与 Decision Token 的关系

Moshi **不使用显式的 turn decision token**。其全双工设计使得 turn-taking 成为**涌现行为**——模型通过双流架构自然学习何时听、何时说。这可以理解为一种**隐式、连续的 turn decision**，而非离散的 binary 决策。

**延迟指标**：理论延迟 160ms，实际约 **200ms** 端到端。

> **来源**: Défossez et al., "Moshi: a speech-text foundation model for real-time dialogue", Kyutai, arXiv:2410.00037, Sep 2024; Emergent Mind, "Moshi: Unified Speech-Text Dialogue Model", 2024; Erogol, "Paper Review: Moshi", 2024.

---

### 1.3 特殊 Token 方案对比

| 方案 | Token 类型 | 显式/隐式 | 代表系统 |
|------|-----------|----------|---------|
| **TurnGPT** | `<ts>` (turn-shift token) | 显式 | TurnGPT (Ekstedt & Skantze, 2020) |
| **Freeze-Omni** | 分类层输出 3 状态 | 显式（非 token） | Freeze-Omni (Wang et al., 2024) |
| **SALMONN-omni** | "thinking" 状态转换 | 隐式 | SALMONN-omni (2025, NeurIPS) |
| **OpenAI Semantic VAD** | 内部语义判断 | 隐式 | OpenAI Realtime API |
| **Moshi** | 无显式 turn token | 隐式（涌现） | Moshi (Kyutai, 2024) |
| **SoulX-Duplug** | 流式状态预测 | 显式（多状态） | SoulX-Duplug (2026) |

**关键发现**：

1. **显式特殊 token（如 `<turn_end>`）在学术研究中被使用**（TurnGPT 的 `<ts>` token），但在工业级全双工系统中**趋于隐式化**。
2. **2025–2026 年的趋势**是将 turn decision 从"离散 token 分类"转向**"连续状态预测"**（SoulX-Duplug 的 streaming state prediction、SALMONN-omni 的 dynamic thinking）。
3. 显式 token 的优势是**可解释性和可控性**，劣势是**需要修改 tokenizer 和训练流程**，且可能与预训练 LLM 的 token 分布冲突。

> **来源**: Ekstedt & Skantze, "TurnGPT: a Transformer-based Language Model for Predicting Turn-taking in Spoken Dialog", EMNLP 2020; Wang et al., "Freeze-Omni", arXiv:2411.00774, 2024; Yan et al., "SoulX-Duplug", arXiv:2603.14877, 2026.

---

### 1.4 Decision Token 的推理延迟与精度权衡

#### 延迟分析

| 方案 | 决策延迟 | 端到端延迟 | 精度 |
|------|---------|-----------|------|
| Server VAD (静音检测) | ~200–500ms | ~800ms+ | 低（易误触发） |
| Semantic VAD (OpenAI) | ~300ms (VAD tick) | ~600ms+ | 中高 |
| TurnGPT (纯文本) | ~10ms (单 token) | 依赖 ASR | 中（缺声学信息） |
| Freeze-Omni (chunk 级) | ~40ms (chunk) | ~300ms | 高 |
| Moshi (全双工) | 0（涌现） | ~200ms | 高 |
| SoulX-Duplug (流式) | ~30ms | ~200ms | 高 |

#### 核心权衡

1. **精度-延迟权衡**：更充分的上下文（更长等待）→ 更高精度，但更高延迟。
2. **显式 vs 隐式**：显式 decision token 增加推理步骤但提供可控性；隐式方案延迟更低但黑盒化。
3. **声学+语义融合**：纯语义（文本）方案在 ASR 完成前无法决策；声学+语义联合方案可以在 ASR 进行中就做出预判。

> **来源**: 综合上述各来源的延迟数据。

---

## 2. 语义 Turn 决策方法

### 2.1 基于 LLM 的 Turn-Taking Prediction

#### TurnGPT (Ekstedt & Skantze, EMNLP 2020)

**来源**: [TurnGPT 论文](https://github.com/ErikEkstedt/TurnGPT), [博士论文](https://www.diva-portal.org/smash/get/diva2:1812269/FULLTEXT04.pdf)

- **架构**：基于 GPT-2 的单向 decoder-only transformer
- **核心机制**：在对话文本中插入 `<ts>` (turn-shift) 特殊 token，模型预测 `<ts>` token 的概率作为 turn-shift 的 likelihood
- **输入**：对话历史文本 + speaker embedding
- **输出**：每个位置的 turn-shift 概率，对应 TRP (Transition-Relevance Place) 概念
- **关键能力**：不仅能**检测** turn 完成，还能**预测**即将到来的 turn 完成
- **局限**：纯文本，缺少声学/韵律信息

#### PairwiseTurnGPT (Leishman et al., SemDial 2024)

**来源**: [SemDial 2024](https://www.semdial.org/anthology/Z24-Leishman_semdial_0002.pdf)

- 扩展 TurnGPT 为**双流架构**，每个说话人一个 GPT-2 流
- 权重共享，word-level 对齐
- 提供更细粒度的 turn-taking 行为洞察

#### "LLMs Know What To Say But Not When To Speak" (2024)

**来源**: arXiv:2410.16044 (通过 Emergent Mind 综述引用)

- 核心发现：LLM 擅长**内容生成**（what to say），但在**时机判断**（when to speak）上表现不佳
- 这意味着 turn decision 需要专门的建模，不能简单复用主 LLM

#### ICASSP 2024: Acoustic + LLM Fusion (Wang et al.)

**来源**: [arXiv:2401.14717](https://arxiv.org/abs/2401.14717)

- 提出**声学模型（HuBERT）+ LLM 融合**方案
- 多任务指令微调：同时预测 turn-taking 和 backchannel
- 三种融合策略：Concatenation、Gated Multi-modal Fusion、Cross-Attention
- 实验证明：**文本+声学融合显著优于纯文本或纯声学**

> **来源**: Wang et al., "Turn-taking and Backchannel Prediction with Acoustic and Large Language Model Fusion", ICASSP 2024; Ekstedt & Skantze, "TurnGPT", EMNLP 2020; Leishman et al., "PairwiseTurnGPT", SemDial 2024.

---

### 2.2 基于声学+语义联合建模的方案

#### Voice Activity Projection (VAP) — 声学侧

**来源**: [Skantze et al., 2024–2025](https://baharirfan.com/wp-content/papercite-data/pdf/skantze2025applying.pdf), Emergent Mind 综述

- **连续预测**：每秒 10 次预测未来 2 秒内双方的 voice activity
- **输入**：原始立体声音频波形（过去 30s）
- **架构**：Transformer + self-attention + cross-channel attention
- **输出**：毫秒级精度的 turn-shift、backchannel、打断预测
- **关键优势**：纯声学，不依赖 ASR，延迟极低

#### VAP + TurnGPT 联合方案 (Skantze et al., 2025)

**来源**: [Skantze et al., "Applying General Turn-taking Models to Conversational AI", 2025](https://baharirfan.com/wp-content/papercite-data/pdf/skantze2025applying.pdf)

- 将 VAP（声学）和 TurnGPT（文本）**并行使用**
- ASR 流式结果送 TurnGPT，音频送 VAP
- 两个模型的输出**融合决策**
- 引入 **self-monitoring**：TTS 输出也反馈给 VAP 模型

#### Predictive ASR + End-of-Utterance Detection (Zink et al., 2024)

**来源**: [arXiv:2409.19990](https://arxiv.org/abs/2409.19990)

- 提出**预测性 ASR**：在用户说完之前预测后续词
- 同时预测 **End-of-Utterance (EOU)** 时间点
- 训练策略：随机 mask 未来语音段，训练 decoder 预测
- 基于 cross-attention alignment 精确定位 EOU
- **可在 utterance 结束前 300ms 做出预测**，为下游争取额外时间

> **来源**: Skantze et al., "Applying General Turn-taking Models to Conversational AI", 2025; Zink et al., "Predictive Speech Recognition and End-of-Utterance Detection", arXiv:2409.19990, 2024.

---

### 2.3 是否需要单独的 Turn Decision Model？

**学术界和工业界的分歧**：

| 立场 | 代表系统 | 理由 |
|------|---------|------|
| **需要单独模型** | FlexDuo, SoulX-Duplug, Phoenix-VAD, Easy Turn | 解耦设计，独立优化，可插拔 |
| **复用主 LLM** | Freeze-Omni, SALMONN-omni | 减少模块数量，端到端训练 |
| **完全不需要** | Moshi | 全双工涌现行为 |

**分析**：

1. **单独模型的优势**（2025–2026 年主流趋势）：
   - **FlexDuo** (Liao et al., 2025)：引入显式 Idle 状态，解耦 duplex 控制与对话系统
   - **SoulX-Duplug** (Yan et al., 2026)：统一 VAD + ASR + Turn Detection 为单一流式模块
   - **Phoenix-VAD** (2025)：基于 Qwen2.5-0.5B 的 LLM-based 语义端点检测
   - **Easy Turn** (2025)：声学+语言双模态融合的 turn-taking 模块
   - 共同理念：**plug-and-play**，可独立优化，不侵入主 LLM

2. **复用主 LLM 的优势**：
   - Freeze-Omni 在 frozen LLM 最后加分类层预测 3 种状态
   - SALMONN-omni 的 "dynamic thinking" 机制让 LLM 学习状态转换
   - 减少模块间误差累积

3. **Full-Duplex-Bench 评测结论** (Lin et al., ASRU 2025)：
   > "Commercial systems like Gemini Live and **cascaded architectures with explicit control modules generally outperform transparent end-to-end models** in nuanced interactive behaviors."

   **这意味着：带显式控制模块的级联架构在 turn-taking 上优于端到端模型。**

> **来源**: Liao et al., "FlexDuo", arXiv:2502.13472, 2025; Yan et al., "SoulX-Duplug", arXiv:2603.14877, 2026; Phoenix-VAD, arXiv:2509.20410, 2025; Lin et al., "Full-Duplex-Bench", arXiv:2503.04721, ASRU 2025.

---

### 2.4 决策粒度：Binary vs 多级

**当前主流方案对比**：

| 粒度 | 状态数 | 代表系统 | 说明 |
|------|--------|---------|------|
| **Binary** | 2 | TurnGPT, Server VAD | 说/不说 |
| **三级** | 3 | Freeze-Omni, FlexDuo, TurnSense | 说话/聆听/空闲(Idle) |
| **多级** | 4+ | SoulX-Duplug, Easy Turn | 说话/聆听/打断/回传(backchannel)/噪声拒绝 |

**FlexDuo 的三态设计**（最值得参考）：

```
SPEAKING → LISTENING → IDLE → SPEAKING → ...
```

- **Idle 状态**的引入是关键创新：模拟人类对话中的"信息过滤"机制
- 在 Idle 状态下，系统不打断但也不完全静默，可以发出 backchannel（"嗯"、"对"）
- 有效减少**误打断**和**噪声触发**

**SoulX-Duplug 的多态设计**（2026 最新）：

- 将 duplex 交互控制建模为**流式状态预测问题**
- 统一 VAD、ASR、Turn Detection 为单一框架
- 状态包括：静音、用户说话、系统说话、打断、backchannel 等
- 通过流式 ASR 目标函数注入语义监督

**建议**：对于草稿的 Decision Token 层，**三级（听/说/空闲）是最低可行粒度**，理想情况下应支持**多级决策**（含打断和 backchannel）。

> **来源**: Liao et al., "FlexDuo", 2025; Yan et al., "SoulX-Duplug", 2026; TurnSense, 2026.

---

## 3. 与 ASR 流水线的集成

### 3.1 Streaming ASR 的 Partial/Final 结果传递

**核心挑战**：ASR 的 partial 结果不稳定（会修正），final 结果延迟高。

**主流方案**：

| 方案 | 描述 | 代表系统 |
|------|------|---------|
| **仅用 final** | 等 ASR final 后再决策 | 传统级联系统 |
| **仅用 partial** | 基于 partial 做增量决策 | TurnGPT + VAP |
| **Partial + Final 融合** | 区分对待，final 修正 | SoulX-Duplug, FastTurn |
| **绕过 ASR** | 直接音频→决策 | Moshi, VAP |

**SoulX-Duplug 的方案**（最先进）：

- 在 chunk-based 流式架构中**联合执行 ASR 和状态预测**
- 训练时通过流式 ASR 目标注入语义监督
- 推理时，状态预测不依赖 ASR 完全完成
- 这实现了 **"语义 VAD"** 的效果——在 ASR 进行中就利用语义信息

**FastTurn 的方案**（2026）：

- 引入 **streaming CTC 模块**实现从 partial observation 快速解码
- 减少 ASR 级联引入的延迟累积
- 直接融合声学特征与学习到的决策模型
- 两个变体：FastTurn-Cascaded（ASR→LLM→决策）和 FastTurn-Unified（端到端）

> **来源**: Yan et al., "SoulX-Duplug", 2026; Wang et al., "FastTurn", arXiv:2604.01897, 2026.

---

### 3.2 增量决策 vs 批量决策

| 维度 | 增量决策 (Incremental) | 批量决策 (Batch) |
|------|----------------------|-------------------|
| **延迟** | 低（边收边判） | 高（等完整输入） |
| **精度** | 可能因信息不完整而误判 | 信息完整，精度更高 |
| **修正能力** | 可中途修正 | 一次性决策 |
| **实现复杂度** | 高 | 低 |
| **代表系统** | VAP, SoulX-Duplug, FastTurn | 传统 VAD |

**最佳实践**（来自 Skantze et al., 2025）：

- **增量预测 + 阈值确认**：持续做增量预测，当置信度超过阈值时触发决策
- **可撤销决策**：允许在 final ASR 结果到来时修正之前的决策
- **多模型融合**：VAP（声学，高频）+ TurnGPT（文本，低频）互补

---

### 3.3 ASR 修正（Correction）对 Turn Decision 的影响

**问题**：Streaming ASR 的 partial hypothesis 经常变化（如 "我要..." → "我要订..." → "我要订机票"），每次修正都可能改变语义完整性判断。

**应对策略**：

1. **延迟确认**：不在第一次语义完整时立即决策，等待 N 个 chunk 确认
2. **置信度门控**：仅当 ASR 置信度 + 语义完整性置信度都超过阈值时触发
3. **回退机制**：如果 final 结果与 partial 差异大，允许撤销已做出的 turn decision
4. **SoulX-Duplug 方案**：通过联合训练 ASR 和状态预测，模型学会处理 ASR 不确定性

> **来源**: 综合上述各来源。

---

## 4. 与草稿的对照分析

### 4.1 Decision Token 作为第三层是否合理？

**结论：合理，但需要细化。**

**支持理由**：

1. **分层架构是 2025–2026 年的主流趋势**：
   - Full-Duplex-Bench 评测表明：**带显式控制模块的级联架构优于端到端模型**
   - FlexDuo、SoulX-Duplug、Easy Turn、FastTurn 都是独立的 turn control 模块
   - ICASSP 2026 HumDial Challenge 中，turn-taking strategy 是各参赛系统的**最核心差异化因素**

2. **"Decision Token" 概念与学术前沿一致**：
   - Freeze-Omni 的 chunk-level state prediction 本质上就是 decision token
   - SALMONN-omni 的 "thinking" 机制也是类似概念
   - SoulX-Duplug 的 streaming state prediction 是最接近"decision token"语义层的实现

3. **独立层次的优势**：
   - 可独立优化（不依赖 ASR 或 TTS 的改动）
   - 可插拔（不同场景用不同策略）
   - 可解释（显式状态便于调试）

**需要细化的方面**：

- 决策粒度应从 binary 扩展到至少三级（听/说/空闲）
- 需要明确与第二层（ASR/语义理解）和第四层（TTS/语音生成）的接口协议
- 需要考虑增量决策和可撤销机制

---

### 4.2 是否应该与 Smart Turn v3.2 合并？

**建议：分层但协同设计。**

| 维度 | Decision Token Layer | Smart Turn v3.2 |
|------|---------------------|------------------|
| 关注点 | **何时**说 | **如何**说（打断策略、barge-in） |
| 输入 | 语义完整性、声学信号 | Turn decision + 对话策略 |
| 输出 | Turn 状态（听/说/空闲） | 具体行动（打断/等待/backchannel） |
| 时间尺度 | 实时（~50ms 级） | 近实时（~200ms 级） |

**建议架构**：

```
Decision Token Layer (第三层)
  ↓ turn state {speak, listen, idle}
Smart Turn v3.2 (第三层半 / 策略层)
  ↓ action {interrupt, wait, backchannel, respond}
Response Generation (第四层)
```

两者职责不同：Decision Token 做**低延迟的语义级 turn 判断**，Smart Turn 做**策略级的行动决策**。

---

### 4.3 决策延迟是否可接受（目标 <200ms 端到端）？

**分析**：

| 组件 | 延迟预算 | 可行性 |
|------|---------|--------|
| 音频采集 + 编码 | ~20ms | ✅ |
| Streaming ASR (partial) | ~50ms | ✅ |
| **Decision Token 推理** | **~30ms** | ⚠️ 需要优化 |
| LLM 生成首 token | ~50ms | ⚠️ 需要 prefill 优化 |
| TTS 首音频 | ~50ms | ✅ |
| **总计** | **~200ms** | ⚠️ 紧张但可行 |

**关键优化方向**：

1. **Warm-up / Pre-fill 优化**：
   - 在用户说话期间**预填充**系统 prompt 的 KV cache
   - 在检测到即将结束时**提前开始** LLM prefill
   - 使用 prefix caching 复用系统 prompt

2. **增量决策**：
   - 不等 ASR final，基于 partial 做预判
   - 使用 VAP 类声学模型做高频预测（10Hz）

3. **轻量级 Decision Model**：
   - 使用小模型（如 0.5B 参数）专门做 turn decision
   - 或使用分类头（Freeze-Omni 方案）而非完整 LLM 推理

4. **参考数据**：
   - Moshi：理论 160ms，实际 200ms
   - IntrinsicVoice：声称 <100ms 多轮延迟
   - Freeze-Omni：chunk 级流式，约 300ms 端到端

> **来源**: Moshi paper, 2024; IntrinsicVoice, arXiv 2024; Freeze-Omni, 2024; LLM Inference optimization guides, 2026.

---

### 4.4 是否需要 Warm-up / Pre-fill 优化？

**强烈建议需要。** 这是实现 <200ms 目标的关键。

**具体方案**：

1. **KV Cache 预热**：
   - 系统 prompt 和对话历史的 KV cache 在 turn 开始前预先计算
   - 使用 vLLM 的 prefix caching 或 LMCache

2. **预测性 Prefill**：
   - 在用户说话期间（检测到即将结束时），提前开始 LLM prefill
   - Zink et al. (2024) 的预测性 EOU 检测可在 utterance 结束前 300ms 预测

3. **Chunked Prefill**：
   - 将 prefill 分块，与 decode 交替执行
   - 减少首 token 延迟（TTFT）

4. **Disaggregated Prefill/Decode**：
   - 将 prefill 和 decode 分离到不同 GPU/实例
   - 适合高并发场景

> **来源**: LLM Inference 2026 Guide (FutureAGI); Zylos Research, "Inference Acceleration for AI Agent Loops", 2026.

---

## 5. 开源方案与学术前沿

### 5.1 2024–2026 年顶会/重要论文汇总

#### 2024 年

| 论文 | 会议/期刊 | 核心贡献 |
|------|----------|---------|
| **Moshi** (Défossez et al.) | arXiv:2410.00037 | 首个全双工实时语音 LLM，Inner Monologue，200ms 延迟 |
| **Freeze-Omni** (Wang et al.) | ICML 2025 / arXiv:2411.00774 | Frozen LLM + chunk-level state prediction，3 态分类 |
| **Mini-Omni** (Xie & Wu) | arXiv:2408.16725 | 开源端到端语音对话，text-instructed 语音生成，batch-parallel 推理 |
| **Turn-taking + Backchannel** (Wang et al.) | ICASSP 2024 | 声学(HuBERT) + LLM 融合，多任务指令微调 |
| **Predictive ASR + EOU** (Zink et al.) | arXiv:2409.19990 | 预测性语音识别 + 端到端检测，提前 300ms 预测 |
| **IntrinsicVoice** | arXiv 2024 | LLM 内在实时语音交互，<100ms 延迟 |
| **Spectron** (Google) | ICLR 2024 | 频谱图驱动的口语 LLM，端到端训练 |
| **dGSLM** (Nguyen et al.) | 2022 (持续影响) | 无文本生成式口语对话，双塔 transformer |

#### 2025 年

| 论文 | 会议/期刊 | 核心贡献 |
|------|----------|---------|
| **SALMONN-omni** | NeurIPS 2025 | 无 codec 全双工语音 LLM，dynamic thinking 机制，35.9% 相对提升 |
| **Full-Duplex-Bench** (Lin et al.) | ASRU 2025 | 首个全双工对话 turn-taking 评测基准 |
| **FlexDuo** (Liao et al.) | arXiv:2502.13472 | 可插拔全双工控制模块，三态 Idle 设计 |
| **Phoenix-VAD** | arXiv:2509.20410 | LLM-based 流式语义端点检测 |
| **Easy Turn** | 2025 | 声学+语言双模态 turn-taking，开源 |
| **FireRedChat** | arXiv:2509.06502 | 可插拔全双工语音交互，流式个性化 VAD + 语义 EOT |
| **Qwen2.5-Omni** (Alibaba) | arXiv:2503.20215 | 全模态 omni 模型 |
| **Full-Duplex-Bench v1.5** | 2025 | 增加 overlap 处理评测 |
| **HumDial Challenge** | ICASSP 2026 (筹备) | 全双工交互挑战赛 |

#### 2026 年（截至 8 月）

| 论文 | 会议/期刊 | 核心贡献 |
|------|----------|---------|
| **SoulX-Duplug** (Yan et al.) | arXiv:2603.14877 | 流式状态预测模块，统一 VAD+ASR+Turn Detection |
| **FastTurn** (Wang et al.) | arXiv:2604.01897 | 声学+流式语义融合，CTC 加速 |
| **JAL-Turn** | arXiv 2026 | 声学-语言联合建模 |
| **TurnSense** | 2026 | 三级语义 turn 检测（中英文） |
| **PersonaPlex** (NVIDIA) | 2026 | 全双工对话语音模型 |
| **DuplexOmni** | 2026 | 实时听说看想全双工 |
| **BayLing-Duplex** | 2026 | 单自回归 LLM 原生全双工 |
| **Full-Duplex-Bench v2** | 2026 | 多轮评测框架 + 自动考官 |
| **HumDial Challenge 结果** | ICASSP 2026 | 全双工系统综合评测 |

> **来源**: [Awesome-Full-Duplex-SDM](https://github.com/Ruiqi-Yan/Awesome-Full-Duplex-SDM); [Awesome-SpeechLM-Survey](https://github.com/dreamtheater123/Awesome-SpeechLM-Survey); 各论文 arXiv 页面。

---

### 5.2 可参考的开源实现

| 项目 | 链接 | 类型 | 语言 |
|------|------|------|------|
| **Moshi** | [github.com/kyutai-labs/moshi](https://github.com/kyutai-labs/moshi) | 全双工端到端 | Python |
| **Freeze-Omni** | [github.com/VITA-MLLM/Freeze-Omni](https://github.com/VITA-MLLM/Freeze-Omni) | 端到端（frozen LLM） | Python |
| **Mini-Omni** | Hugging Face: `gpt-omni/mini-omni` | 端到端语音对话 | Python |
| **SoulX-Duplug** | [github.com/Soul-AILab/SoulX-Duplug](https://github.com/Soul-AILab/SoulX-Duplug) | 流式语义 VAD 模块 | Python |
| **Full-Duplex-Bench** | [github.com/DanielLin94144/Full-Duplex-Bench](https://github.com/DanielLin94144/Full-Duplex-Bench) | 评测基准 | Python |
| **HumDial-FDBench** | [github.com/ASLP-lab/HumDial-FDBench](https://github.com/ASLP-lab/HumDial-FDBench) | ICASSP 2026 挑战 | Python |
| **TurnGPT** | [github.com/ErikEkstedt/TurnGPT](https://github.com/ErikEkstedt/TurnGPT) | 文本 turn-taking 模型 | Python |
| **Easy Turn** | 开源（与 HumDial 关联） | 双模态 turn 检测 | Python |
| **SALMONN-omni** | 代码已开源 | 无 codec 全双工 | Python |
| **FireRedChat** | 开源 | 级联/半级联全双工 | Python |

---

### 5.3 关键学术趋势总结

1. **从半双工到全双工**：2024 年 Moshi 开创，2025–2026 年成为主流方向
2. **从端到端到模块化**：Full-Duplex-Bench 评测表明，**带显式控制模块的级联架构在 turn-taking 上优于纯端到端模型**
3. **从 binary 到多级决策**：三态（听/说/空闲）成为最低标准，多态（含打断/backchannel）是趋势
4. **从纯声学到声学+语义融合**：VAP + TurnGPT 联合、SoulX-Duplug 统一框架
5. **从批量到流式增量**：FastTurn 的 CTC 加速、SoulX-Duplug 的 streaming state prediction
6. **评测标准化**：Full-Duplex-Bench v1→v1.5→v2，HumDial Challenge，评测体系快速成熟

---

## 6. 综合建议

### 6.1 对草稿第三层的设计建议

1. **保留 Decision Token 作为独立第三层**：这与 2025–2026 年学术界和工业界的主流趋势一致。

2. **决策粒度采用三级（最低）到多级（推荐）**：
   - 最低：`{SPEAK, LISTEN, IDLE}`
   - 推荐：`{SPEAK, LISTEN, IDLE, BACKCHANNEL, INTERRUPT}`

3. **采用显式状态预测而非隐式涌现**：
   - 参考 Freeze-Omni 的分类头方案（轻量）
   - 或 SoulX-Duplug 的流式状态预测（更先进）
   - 显式方案可解释、可调试、可独立优化

4. **融合声学+语义信号**：
   - 声学侧：参考 VAP 的连续 voice activity 预测
   - 语义侧：参考 TurnGPT 的 turn-shift token 或 SoulX-Duplug 的流式 ASR 联合
   - 融合策略：参考 Wang et al. (ICASSP 2024) 的多模态融合

5. **实现增量决策 + 可撤销机制**：
   - 基于 partial ASR 做预判
   - 设置置信度阈值
   - 允许 final ASR 修正

6. **必须实现 KV Cache 预热**：
   - 系统 prompt 预填充
   - 预测性 prefill（在 EOU 前 300ms 启动）
   - 这是达到 <200ms 目标的关键

### 6.2 与 Smart Turn v3.2 的关系

建议保持两层分离但协同：
- **Decision Token Layer**：低延迟（~30ms）语义级 turn 判断
- **Smart Turn v3.2**：策略级行动决策（打断/等待/backchannel）

### 6.3 延迟预算

```
音频采集:         ~20ms
Streaming ASR:    ~50ms  (partial)
Decision Token:   ~30ms  (轻量分类头/小模型)
LLM Prefill:      ~50ms  (KV cache 预热后)
TTS 首音频:       ~50ms
────────────────────────
总计:            ~200ms  (目标可达)
```

### 6.4 推荐参考实现优先级

1. **SoulX-Duplug**（2026 最新，最接近 Decision Token 概念）
2. **Freeze-Omni**（chunk-level state prediction，简单有效）
3. **FlexDuo**（三态设计，Idle 状态创新）
4. **FastTurn**（CTC 加速，低延迟）
5. **TurnGPT + VAP**（经典声学+语义融合范式）

---

> **文档版本**: v1.0
> **作者**: Alice 27 (Wind Financial AI)
> **最后更新**: 2026-08-11
