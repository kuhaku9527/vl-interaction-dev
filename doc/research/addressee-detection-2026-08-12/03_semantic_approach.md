# LLM 语义上下文判断说话对象：可行性调研报告

> **调研日期**: 2026-08-12
> **调研背景**: Windows 桌面陪伴型语音代理，技术栈 sherpa-onnx + Silero VAD + llama.cpp + decision token 三态框架
> **核心问题**: 是否可以通过扩展 LLM 的 decision token 来同时完成"用户是否在对 AI 说话"的判定？

---

## 目录

1. [LLM 语义判定能力分析](#1-llm-语义判定能力分析)
2. [Decision Token 扩展方案对比](#2-decision-token-扩展方案对比)
3. [Prompt Engineering 实验设计](#3-prompt-engineering-实验设计)
4. [延迟与资源评估](#4-延迟与资源评估)
5. [语义路线的根本局限性](#5-语义路线的根本局限性)
6. [综合结论与建议](#6-综合结论与建议)

---

## 1. LLM 语义判定能力分析

### 1.1 核心发现：纯文本语义判定说话对象是极其困难的任务

**关键证据 1：GPT-4o 在 Addressee Recognition 上的表现仅略高于随机基线**

2025 年发表于 IWSDS 的论文 *"An LLM Benchmark for Addressee Recognition in Multi-modal Multi-party Dialogue"* 对 GPT-4o 进行了系统性的说话对象识别基准测试：

- **Addressee Recognition 任务**（判断当前话语是对谁说的）：GPT-4o 准确率 **80.9%**，而随机基线（始终预测 "O"——无特定对象）为 **80.6%**。仅比随机高 0.3 个百分点。
- **Next Speaker Prediction 任务**（预测下一个说话者）：GPT-4o 准确率 **46.0%**，低于随机基线 **50%**（三人对话中二选一）。
- 模型倾向于过度预测 "O"（无特定对象），**经常无法识别话语是针对特定参与者的**。

> 来源: [An LLM Benchmark for Addressee Recognition in Multi-modal Multi-party Dialogue](https://arxiv.org/html/2501.16643v1), IWSDS 2025

**关键证据 2：Apple 的 DDSD 系统必须融合声学特征才能达到可用精度**

Apple 在 2024 年 ICASSP 和 Interspeech 上发表了多篇关于 Device-Directed Speech Detection (DDSD) 的论文：

- *"A Multi-signal Large Language Model for Device-directed Speech Detection"* (Wagner et al., ICASSP 2024)：将 DDSD 建模为文本生成任务，但**必须融合声学 embedding（audio encoder 输出作为 prefix token）+ ASR 文本 + 置信度信息**。纯文本路线的性能文中未单独报告，但架构设计本身说明仅靠文本是不够的。
- *"Multimodal Large Language Models with Fusion Low Rank Adaptation for Device Directed Speech Detection"* (Palaskar et al., Interspeech 2024)：进一步使用 Fusion Low-Rank Adaptation 来融合声学和文本模态。
- *"Modality Dropout for Multimodal Device Directed Speech Detection"* (2024)：研究了模态缺失场景——当声学或文本模态不可用时，性能显著下降。这间接证明了**单一文本模态的局限性**。

> 来源: [Apple Machine Learning Research - DDSD系列](https://machinelearning.apple.com/research/llm-device-directed-speech-detection)

### 1.2 为什么纯文本判定说话对象如此困难？

| 挑战维度 | 具体表现 | 对桌面语音代理的影响 |
|---------|---------|-------------------|
| **缺乏声学线索** | 人类判断说话对象依赖目光方向、身体朝向、韵律（prosody）、音量等 | 桌面场景中用户可能背对设备、边走边说 |
| **中文口语省略** | "那个""帮我拿一下""他说得对吗"——没有显式称呼语 | 家庭/办公环境中高频出现 |
| **指代消解** | "你觉得呢？""刚才那个再查一下"——需要多轮上下文 | 多人在场时指代目标模糊 |
| **语境依赖** | 同一句话在不同场景下对象不同（"几点了"可能是问AI也可能是问身边的人） | 无法仅从文本区分 |
| **多说话人混淆** | 多人对话中，LLM 难以追踪 turn-taking 动态 | 家庭场景中家人对话与对AI说话交织 |

### 1.3 学术界关于 "Text-based Addressee Classification" 的现状

- 传统方法依赖多模态特征（声学 + 视觉 + 文本），纯文本方法在文献中极少被单独评估。
- *"Do LLMs Understand Dialogues? A Case Study on Dialogue Acts"* (ACL 2025) 指出 LLM 在对话行为分类中需要理解 Turn Management（谁在说话、对谁说），但这是 LLM 的薄弱环节。
- 目前没有找到任何声称纯文本 addressee detection 能达到生产可用精度（>95%）的已发表工作。

> 来源: [Do LLMs Understand Dialogues?](https://people.engr.tamu.edu/huangrh/papers/acl2025_main_LLM_DA.pdf), ACL 2025

### 1.4 桌面陪伴型场景的特殊性

与 Apple DDSD 场景（智能音箱，用户通常明确呼叫）不同，桌面陪伴型语音代理面临更复杂的场景：

- **无唤醒词**：用户架构是 live 模式免唤醒常驻监听，缺少"Hey Siri"这样的强信号。
- **近距离多人**：桌面场景中用户可能在与同事/家人交谈，AI 需要区分"对我说的"和"对别人说的"。
- **非对称信息**：AI 只能听到语音，看不到视觉线索（目光、手势）。

---

## 2. Decision Token 扩展方案对比

### 2.1 三种方案概览

```
方案 A: 四态 Decision Token
┌──────────┐    ┌─────────────────────────────────────┐
│  ASR文本  │───▶│  LLM 单次推理 → silence/response/   │
│ + 上下文  │    │  delegation/not-for-me (四选一)      │
└──────────┘    └─────────────────────────────────────┘

方案 B: 两阶段 Pipeline
┌──────────┐    ┌──────────────┐    ┌─────────────────┐
│  ASR文本  │───▶│ Stage 1:     │───▶│ Stage 2:        │
│ + 上下文  │    │ 说话对象判定  │    │ 三态 Decision    │
└──────────┘    │ (for-me?)    │    │ (silence/resp/   │
                └──────────────┘    │  delegation)     │
                                    └─────────────────┘

方案 C: 隐式融入 System Prompt
┌────────────────────────────────────────────────────┐
│  System Prompt 中增加:                              │
│  "如果用户的话不是对你说的，输出 silence"             │
│  → LLM 在 silence 决策中隐含处理 not-for-me          │
└────────────────────────────────────────────────────┘
```

### 2.2 详细对比

| 维度 | 方案 A（四态扩展） | 方案 B（两阶段） | 方案 C（隐式融入） |
|------|-------------------|-----------------|-------------------|
| **延迟增量** | 几乎为零（仅多一个 token 分类选项） | **最大**：需要两次独立 LLM 推理 | 几乎为零 |
| **准确率** | 中等：四分类比三分类更难，可能降低原有三态准确率 | 理论上最高：Stage 1 可专门优化 | **最低**：隐式处理缺乏明确决策边界 |
| **工程复杂度** | 低：仅修改 grammar/constrained decoding 的输出空间 | **高**：需要管理两次推理的流水线和状态传递 | 最低：仅修改 prompt |
| **可调试性** | 好：显式输出，可独立评估 | 最好：每阶段独立评估 | **差**：无法区分"silence 因为不是对我说的"还是"silence 因为不需要回复" |
| **Prompt 设计难度** | 中等：需要精心设计四态判别标准 | 低：每阶段任务单一 | 高：需要在不增加 token 的前提下传达复杂规则 |
| **与现有架构兼容性** | 好：直接扩展 grammar | 差：需要重构推理流水线 | 最好：无需改代码 |

### 2.3 推荐方案：方案 A（四态扩展）+ 约束解码

**理由**：
1. **延迟最优**：单次推理，不增加额外 LLM 调用。
2. **工程可行**：llama.cpp 的 GBNF grammar 可以精确约束输出为四选一。
3. **可评估**：可以独立统计 not-for-me 的精确率和召回率。
4. **渐进式**：如果准确率不够，可以后续在 not-for-me 路径上叠加轻量级声学验证。

**GBNF Grammar 示例**：
```gbnf
root ::= "silence" | "response" | "delegation" | "not-for-me"
```

---

## 3. Prompt Engineering 实验设计

### 3.1 推荐 Prompt 模板

基于调研发现（GPT-4o 倾向于过度预测 "O"/无特定对象），prompt 设计需要**主动引导模型关注"对我说话"的信号**：

```
<|system|>
你是一个桌面语音助手的决策模块。你的任务是：
1. 判断用户的话语是否是对你（AI助手）说的
2. 如果是对你说的，决定如何回应

输出格式：只输出以下四个词之一：
- not-for-me: 用户在对其他人说话，或自言自语，不是在对你说话
- silence: 用户在对你说，但不需要回复（如陈述句、自言自语式确认）
- response: 用户在对你说，需要你回复
- delegation: 用户在对你说，需要调用外部工具/技能

判断"是否对你说"的规则：
- 如果话语包含你的名字、称呼（如"嘿""喂""那个AI"），大概率是对你说的
- 如果话语是明确的提问或指令，且没有指定其他对象，默认是对你说的
- 如果话语明显是在回应另一个人的话，或内容与你无关，则不是对你说的
- 如果无法确定，倾向于认为是对你说的（宁可多回应，不要冷漠）

对话历史：
{history}

用户刚说的话：
{utterance}
<|assistant|>
```

### 3.2 Few-shot 示例设计

建议在 system prompt 后加入 4-6 个覆盖边界情况的 few-shot 示例：

```
示例1:
用户说: "帮我把那个文件打开"
输出: response
理由: 明确指令，无其他对象，默认对AI说

示例2:
用户说: "老王你觉得这个方案怎么样"
输出: not-for-me
理由: 明确指定了"老王"为说话对象

示例3:
用户说: "嗯...这个颜色不太对"
输出: silence
理由: 自言自语式陈述，不需要回复

示例4:
用户说: "那个谁，帮我查一下今天的天气"
输出: response
理由: "那个谁"在桌面场景中大概率指AI（无其他人在场时）

示例5:
用户说: "你说他说的对吗"
输出: response
理由: "你说"明确指向AI
```

### 3.3 Chain-of-Thought 的影响

- **不建议在 decision token 场景中使用 CoT**：CoT 会增加数十到数百个 token 的生成量，在 CPU 上每个 token 约 50-200ms，会显著增加延迟。
- **替代方案**：将推理过程放在 prompt 的 few-shot 示例中（如上），让模型通过 in-context learning 隐式学习推理模式，而非显式生成推理步骤。

### 3.4 中文口语特殊挑战的应对

| 挑战 | Prompt 策略 | 预期效果 |
|------|-----------|---------|
| "那个"（指代模糊） | Few-shot 中展示"那个"在不同上下文中的判定 | 部分缓解，无法根治 |
| "帮我拿一下"（无称呼） | 规则：无明确对象的指令默认对AI | 可能在多人场景中误判 |
| "他说的对吗"（依赖前文） | 提供充足对话历史（至少3轮） | 需要历史中有明确指代锚点 |
| 多人对话交织 | 在 prompt 中标注说话人角色 | 需要 ASR 支持说话人分离 |

---

## 4. 延迟与资源评估

### 4.1 llama.cpp 延迟模型

LLM 推理延迟由两部分组成：

```
总延迟 = Prompt Evaluation Time + Token Generation Time
```

- **Prompt Evaluation Time**：处理输入 prompt 的所有 token，在 CPU 上是**主要瓶颈**。llama.cpp 的 prompt processing 在纯 CPU 上约为 **0.8-1.3 tokens/ms**（取决于模型大小和量化级别）。
- **Token Generation Time**：每生成一个 token 的时间。对于 decision token 场景（仅生成 1 个 token），这部分几乎可以忽略。

### 4.2 各方案的延迟估算

假设使用 Qwen2.5-1.5B Q4_K_M 量化模型，在 Windows 桌面 CPU（如 i7-13700）上：

| 场景 | Prompt 长度 | Prompt Eval | Token Gen | 总延迟 |
|------|-----------|-------------|-----------|--------|
| 现有三态（基线） | ~200 tokens | ~200ms | ~20ms | **~220ms** |
| 方案 A（四态扩展） | ~250 tokens | ~250ms | ~20ms | **~270ms** |
| 方案 B（两阶段） | 2× ~200 tokens | ~400ms | ~40ms | **~440ms** |
| 方案 C（隐式融入） | ~250 tokens | ~250ms | ~20ms | **~270ms** |

> 注：以上为估算值。实际延迟受模型大小、量化级别、CPU 型号、内存带宽等因素影响。参考数据来源：
> - [Deploying LLMs on CPU-only Environments with llama.cpp](https://ceur-ws.org/Vol-4164/paper11.pdf) (ProfIT AI'25)：6B-20B 模型在 Sapphire Rapids Xeon 上 20-80ms/token 生成延迟
> - [LLaMA Now Goes Faster on CPUs](https://justine.lol/matmul)：llama.cpp prompt eval 是 CPU 推理的瓶颈
> - [llama.cpp Discussion #229](https://github.com/ggml-org/llama.cpp/discussions/229)：prompt 长度与推理时间线性相关

### 4.3 是否可以共享一次推理？

**可以。方案 A 和方案 C 都只需要一次推理。**

关键设计原则：
- 将"说话对象判定"和"三态决策"融合到**同一个分类任务**中
- 输出空间从 3 类扩展到 4 类（方案 A）或保持 3 类但重新定义 silence 的语义（方案 C）
- 使用 llama.cpp 的 `--grammar` 约束解码，确保只生成 1 个有效 token

**方案 B 需要两次推理**，延迟翻倍，在 CPU 场景下不推荐。

### 4.4 KV Cache 优化

llama.cpp 支持通过 `cache_prompt: true` 实现跨轮次 KV cache 复用：
- System prompt 和 few-shot 示例的 KV cache 可以在多轮对话中复用
- 每轮只需处理新增的用户话语部分
- 可将 prompt eval 延迟从 ~250ms 降至 ~50-80ms（仅处理增量）

> 来源: [llama.cpp KV Cache Reuse Tutorial](https://github.com/ggml-org/llama.cpp/discussions/13606), [LlamaCppEx Performance Guide](https://hexdocs.pm/llama_cpp_ex/performance.html)

---

## 5. 语义路线的根本局限性

### 5.1 架构层面的"先有鸡还是先有蛋"问题

```
语义路线的核心矛盾：

  VAD 检测语音 → ASR 完整转写 → LLM 判断是否对AI说 → 决定是否回复
                                      ↑
                                 此时已经消耗了 ASR 的全部计算资源
```

**这是一个根本性的架构缺陷**：语义路线必须在 ASR 完成之后才能判断说话对象，但 ASR 本身是 pipeline 中计算量最大的环节之一。

### 5.2 与声学路线的对比

| 维度 | 语义路线（本报告方案） | 声学路线（理想方案） |
|------|---------------------|-------------------|
| **判断时机** | ASR 完成后 | ASR 之前或并行 |
| **计算资源浪费** | 对所有语音（包括不是对AI说的）都做完整 ASR | 仅对可能是对AI说的语音做 ASR |
| **准确率上限** | 受限于纯文本信息（GPT-4o 仅略高于随机） | 可利用韵律、音量、方向等丰富信号 |
| **延迟** | ASR延迟 + LLM推理延迟 | 声学模型延迟（通常 <50ms） |
| **工程复杂度** | 低（纯软件，复用现有 LLM） | 高（需要训练/集成声学模型） |
| **隐私** | 所有语音都被转写为文本 | 声学特征可以不保留原始语音内容 |

### 5.3 "浪费"的量化估算

假设桌面场景中，用户 8 小时工作中：
- 实际对 AI 说话的时间占比：约 5-10%（乐观估计）
- 其余 90-95% 的语音是：与同事交谈、电话、自言自语、环境噪音

**语义路线的浪费**：
- ASR 持续运行，消耗 CPU 资源处理 90-95% 的"无效"语音
- sherpa-onnx 流式 ASR 在 CPU 上的典型功耗约为 10-20% CPU 占用
- 这意味着 **80-90% 的 ASR 计算资源被浪费在不需要处理的语音上**

### 5.4 Apple DDSD 的启示：声学路线是工业界主流

Apple 的 DDSD 系统架构明确展示了工业界的共识：
1. **声学特征先行**：audio encoder 将原始波形编码为连续 embedding
2. **多模态融合**：声学 embedding + ASR 文本 + 置信度 → LLM 分类
3. **声学特征作为 prefix token**：在文本之前先处理声学信息

这说明即使是拥有最强 LLM 能力的公司，也**不认为纯文本语义路线足以解决说话对象判定问题**。

> 来源: [A Multi-signal Large Language Model for Device-directed Speech Detection](https://machinelearning.apple.com/research/llm-device-directed-speech-detection), Apple, ICASSP 2024

### 5.5 语义路线的适用边界

语义路线并非完全不可行，它在以下条件下可能足够好：

1. **单人场景**：用户独自在房间内，所有语音都是对 AI 说的 → 不需要说话对象判定
2. **唤醒词模式**：有明确的唤醒词作为"对AI说话"的信号 → 语义判定退化为确认
3. **低精度容忍**：允许一定的误触发（false positive），用户体验影响可控
4. **作为声学路线的补充**：声学模型做初筛，语义模型做精判

---

## 6. 综合结论与建议

### 6.1 核心结论

1. **纯文本语义判定说话对象是不可靠的**：GPT-4o 级别的模型在三方对话中仅略高于随机基线（80.9% vs 80.6%），远未达到生产可用精度。

2. **方案 A（四态 decision token）是语义路线中最优的工程方案**：延迟增量最小（~50ms），工程复杂度低，可渐进式部署。

3. **语义路线存在根本性架构缺陷**：必须在 ASR 完成后才能判断，导致 80-90% 的 ASR 计算资源被浪费。

4. **工业界共识是声学+文本多模态融合**：Apple 的 DDSD 系列论文明确展示了这一方向。

### 6.2 分阶段实施建议

```
Phase 1（立即可做）：方案 A 四态 Decision Token
  - 扩展 grammar 为四选一
  - 设计专用 prompt + few-shot
  - 收集真实场景数据，评估 not-for-me 准确率
  - 预期：在单人/准单人场景下可用，多人场景准确率有限

Phase 2（中期）：语义 + 轻量声学混合
  - 在 Silero VAD 之后、ASR 之前，增加轻量级声学分类器
  - 声学特征（如音量、基频、语速）判断"是否可能在对我说话"
  - 声学初筛 → ASR → LLM 语义精判
  - 可大幅减少无效 ASR 计算

Phase 3（长期）：端到端多模态
  - 参考 Apple DDSD 架构
  - 音频 encoder + 文本 LLM 联合推理
  - 或使用 Audio-LLM（如 Qwen-Audio）直接处理语音
```

### 6.3 关键风险

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| not-for-me 准确率不足 | 频繁误触发或漏响应 | 设置可调阈值；允许用户反馈纠正 |
| 中文口语歧义 | 特定表达系统性误判 | 收集用户特有表达，做 few-shot 定制 |
| CPU 资源竞争 | ASR + LLM 同时运行导致延迟 | 使用 KV cache 复用；考虑更小的 SLM（0.5B-1.5B） |
| 隐私顾虑 | 所有语音被转写为文本 | 本地处理，不上传云端 |

---

## 参考文献

1. *"An LLM Benchmark for Addressee Recognition in Multi-modal Multi-party Dialogue"*, IWSDS 2025. [arXiv](https://arxiv.org/html/2501.16643v1) | [ACL Anthology](https://aclanthology.org/2025.iwsds-1.36.pdf)
2. Wagner, D. et al. *"A Multi-signal Large Language Model for Device-directed Speech Detection"*, Apple, ICASSP 2024. [Apple ML Research](https://machinelearning.apple.com/research/llm-device-directed-speech-detection)
3. Palaskar, S. et al. *"Multimodal Large Language Models with Fusion Low Rank Adaptation for Device Directed Speech Detection"*, Apple, Interspeech 2024. [Apple ML Research](https://machinelearning.apple.com/research/llm-fusion-low-rank)
4. *"Modality Dropout for Multimodal Device Directed Speech Detection using Verbal and Non-Verbal Features"*, Apple, 2024. [Apple ML Research](https://machinelearning.apple.com/research/modality-dropout)
5. *"Do LLMs Understand Dialogues? A Case Study on Dialogue Acts"*, ACL 2025. [PDF](https://people.engr.tamu.edu/huangrh/papers/acl2025_main_LLM_DA.pdf)
6. *"Deploying LLMs on CPU-only Environments with llama.cpp"*, ProfIT AI'25. [CEUR-WS](https://ceur-ws.org/Vol-4164/paper11.pdf)
7. *"LLaMA Now Goes Faster on CPUs"*, Justine Tunney, 2024. [justine.lol](https://justine.lol/matmul)
8. *"Trying to understand why starting with a long prompt is so much slower"*, llama.cpp Discussion #229. [GitHub](https://github.com/ggml-org/llama.cpp/discussions/229)
9. *"Tutorial: KV cache reuse with llama-server"*, llama.cpp Discussion #13606. [GitHub](https://github.com/ggml-org/llama.cpp/discussions/13606)
10. *"Pipeline vs. Realtime Voice Agent Architecture: Full Guide"*, RTC League, 2025. [rtcleague.com](https://rtcleague.com/blogs/pipeline-vs-realtime-voice-agent-architecture)
11. *"Understanding Latency in Voice AI Systems"*, Ultravox, 2025. [ultravox.ai](https://www.ultravox.ai/voice-ai/understanding-latency-in-voice-ai-systems)
12. *"Voice AI Infrastructure: Building Real-Time Speech Agents"*, Introl, 2025. [introl.com](https://introl.com/blog/voice-ai-infrastructure-real-time-speech-agents-asr-tts-guide-2025)
13. *"Best Small Language Models 2026: Top SLMs Ranked"*, Local AI Master, 2026. [localaimaster.com](https://localaimaster.com/blog/small-language-models-guide-2026)
14. *"Complete Guide to Wake Word Detection (2026)"*, Picovoice, 2026. [picovoice.ai](https://picovoice.ai/blog/complete-guide-to-wake-word)
15. *"Prompt Cache: Modular Attention Reuse"*, MLSys 2024. [Proceedings](https://proceedings.mlsys.org/paper_files/paper/2024/file/a66caa1703fe34705a4368c3014c1966-Paper-Conference.pdf)
