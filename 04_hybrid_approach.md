# 声学+语义混合路线 & 端到端说话对象判定方案调研报告

> **日期**: 2026-08-12  
> **作者**: Alice 27 (Wind AI)  
> **背景**: Windows 桌面陪伴型语音代理，技术栈 sherpa-onnx + Silero VAD + llama.cpp + decision token 三态框架  
> **目标**: 评估声学+语义混合路线是否最优，调研学术界最新进展

---

## 目录

1. [混合路线架构设计](#1-混合路线架构设计)
2. [学术界最新进展（2023-2026）](#2-学术界最新进展2023-2026)
3. [端到端方案可行性](#3-端到端方案可行性)
4. [融合策略的工程细节](#4-融合策略的工程细节)
5. [与业界方案的差距评估](#5-与业界方案的差距评估)
6. [参考文献](#6-参考文献)

---

## 1. 混合路线架构设计

### 1.1 业界已有方案概览

#### 1.1.1 Apple 的语音触发与假触发缓解体系

Apple 的 Siri 语音触发系统是一个**多阶段级联架构**，虽然没有公开命名为 "DDSD" 的单一论文，但其整体架构包含以下关键组件：

**阶段 1：Always-on 触发词检测（"Hey Siri" 检测器）**
- 一个极低功耗的 DNN-HMM 关键词检测模型，始终监听麦克风
- 使用声学模型输出音素概率分布，通过时间积分（动态规划）计算触发词置信度
- 论文: "Hey Siri: An On-device DNN-powered Voice Trigger for Apple's Personal Assistant" (Apple ML Research, 2017)
- 来源: https://machinelearning.apple.com/research/hey-siri

**阶段 2：个性化说话人验证（Speaker ID）**
- 在触发词被检测到后，使用说话人识别系统验证是否为设备主人
- 用户需完成简短注册（5句"Hey Siri"开头的短语）
- 使用 DNN + 课程学习（curriculum learning）训练文本无关的说话人识别
- 论文: "Personalized Hey Siri" (Apple ML Research, 2018)
- 来源: https://machinelearning.apple.com/research/personalized-hey-siri

**阶段 3：假触发缓解（False Trigger Mitigation, FTM）**
- 使用 ASR 解码格（lattice）分析触发后的语音内容
- 双向 Lattice RNN (Bi-LRNN) 判断是否为设备定向语音
- 核心假设：真实触发后的语音是设备定向的、有明确意图的（如 "Siri, what time is it?"），假触发则来自背景噪音或类似触发词的语音
- 知识蒸馏方案：用 LSTM 直接从声学特征判断意图，无需显式 ASR 解码，可在设备端运行
- 论文: "Voice Trigger System for Siri" (Apple ML Research, 2023), "Knowledge Transfer for Efficient On-device False Trigger Mitigation" (Apple ML Research)
- 来源: https://machinelearning.apple.com/research/voice-trigger, https://machinelearning.apple.com/research/on-device-false-trigger

**Apple 方案的关键启示**：
- 采用**级联而非并行**架构：先声学触发 → 再说话人验证 → 再语义意图判断
- 假触发缓解本质上就是"说话对象判定"：判断语音是否真的是对设备说的
- 语义层使用 ASR 格而非完整转写，兼顾隐私和效率

#### 1.1.2 Google Personal VAD 系列

Google 的 Personal VAD 是**最直接相关的声学+说话人条件融合方案**：

**Personal VAD 1.0 (Odyssey 2020)**
- 论文: "Personal VAD: Speaker-Conditioned Voice Activity Detection" (Ding et al., 2020)
- 四种架构对比：
  - **SC (Score Combination)**: 标准 VAD + 说话人验证分数组合（基线，昂贵）
  - **ST (Score Conditioned Training)**: 在特征+分数上训练新模型
  - **ET (Embedding Conditioned Training)**: 在特征+说话人嵌入上训练新模型（**推荐用于设备端**）
  - **SET (Score and Embedding Conditioned Training)**: 同时使用分数和嵌入（最优但参数多）
- ET 架构仅需 SET 2.6% 的运行时参数，性能接近最优
- 来源: https://google.github.io/speaker-id/publications/PersonalVAD/

**Personal VAD 2.0 (Interspeech 2022)**
- 论文: "Personal VAD 2.0: Optimizing Personal Voice Activity Detection for On-Device Speech Recognition" (Ding et al., 2022)
- 核心改进：
  - 使用 **Conformer** 替代 LSTM，支持流式处理（有限左上下文，无右上下文）
  - **FiLM (Feature-wise Linear Modulation)** 层进行说话人嵌入调制，替代简单拼接
  - **Speaker PreNet**：通过余弦相似度将说话人嵌入与声学特征融合
  - 8-bit 量化：模型从 5.8MB 压缩到 1.0MB
- 性能：Non-Concat 条件下 27.2% 等错误率（EER），量化后仅轻微退化至 27.2%
- 来源: https://arxiv.org/abs/2204.03793

**Personal VAD 后续改进 (2024-2025)**
- **Bi-GRU + Cross-Attention 增强** (Electronics, 2025): 在 Conformer 基础上引入双向 GRU 时序建模层和交叉注意力机制，准确率从 86.18% 提升至 87.59%，mAP 从 0.9378 提升
- **Speaker Conditional Sinc-Extractor** (Interspeech 2024): 使用 SincNet 滤波器进行说话人条件特征提取
- **COIN-AT-PVAD** (APSIPA 2024): 条件中间注意力 PVAD
- **Wake Word Reference Speech** (ICASSP 2024): 直接使用唤醒词作为参考语音，无需额外注册
- **Ultra-Short Reference Speech** (APSIPA 2024): 支持超短参考语音的 PVAD
- 来源: https://www.mdpi.com/2079-9292/14/12/2372

#### 1.1.3 其他多模态 Addressee Detection 方案

**MPC-BERT (ACL 2021)**
- 论文: "MPC-BERT: A Pre-Trained Language Model for Multi-Party Conversation Understanding" (Gu et al., 2021)
- 为多方对话设计的预训练语言模型，同时建模"谁在说 → 说什么 → 对谁说"
- 自监督任务包括：说话人识别、addressee 识别、回复选择
- Addressee 识别准确率超越 SOTA 3.51%-5.36%
- 来源: https://aclanthology.org/2021.acl-long.285.pdf

**Who-to-Whom (W2W) 模型**
- 论文: "Who-to-Whom" (Le et al., 2019)
- 识别并补全对话中所有话语的 addressee
- 来源: IJCAI 2022 Survey: https://www.ijcai.org/proceedings/2022/0768.pdf

**ModeratorLM (2025/2026)**
- 论文: "Adaptive Turn-Taking for Real-time Multi-Party Voice Agents" (arXiv 2606.13544, 2025/2026)
- 基于 Speech LLM 的角色扮演语音代理，在 chunk-wise 流式处理中自主决定轮次
- 使用 Qwen3-4B 作为骨干 LLM，语音编码器处理多说话人音频
- **关键创新**：完全由 Speech LLM 自主决定 turn-taking，不依赖外部 VAD 模块
- 引入 reasoning-augmented 变体（ModeratorLM-Think），使用思维链推理
- 来源: https://arxiv.org/html/2606.13544v2

### 1.2 三种混合方案设计

基于以上调研，针对用户技术栈（sherpa-onnx + Silero VAD + llama.cpp + decision token），设计三种混合架构方案：

#### 方案 H1：声学预筛 → 语义终判（级联架构）

```
┌─────────────────────────────────────────────────────────────────┐
│                        方案 H1: 级联架构                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  麦克风音频流                                                    │
│       │                                                          │
│       ▼                                                          │
│  ┌──────────────┐                                                │
│  │  Silero VAD   │ ← 语音/非语音分段                              │
│  └──────┬───────┘                                                │
│         │ 语音段                                                  │
│         ▼                                                        │
│  ┌──────────────────────┐                                        │
│  │ 声学预筛模块          │                                        │
│  │ (Speaker Embedding   │ ← 快速过滤明显非目标说话人              │
│  │  + Cosine Similarity)│   延迟: ~10-50ms                       │
│  └──────┬───────────────┘                                        │
│         │                                                        │
│    相似度 > 阈值?                                                 │
│    ├── NO ──→ silence (快速拒绝)                                  │
│    └── YES ─→                                                    │
│         │                                                        │
│         ▼                                                        │
│  ┌──────────────────────┐                                        │
│  │ sherpa-onnx ASR       │ ← 流式语音识别                         │
│  └──────┬───────────────┘                                        │
│         │ 转写文本                                                │
│         ▼                                                        │
│  ┌──────────────────────┐                                        │
│  │ LLM Decision Token    │ ← 语义终判                            │
│  │ (llama.cpp)           │   silence / response / delegation     │
│  └──────────────────────┘                                        │
│                                                                  │
│  优点: 工程简单，各模块独立，声学筛可大幅减少 LLM 调用            │
│  缺点: 级联错误传播，声学阈值难调                                 │
│  延迟: 声学 ~10-50ms + ASR ~100-300ms + LLM ~200-500ms           │
│        = 总计 ~310-850ms                                          │
└─────────────────────────────────────────────────────────────────┘
```

#### 方案 H2：声学+语义并行打分 → 融合决策

```
┌─────────────────────────────────────────────────────────────────┐
│                      方案 H2: 并行融合架构                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  麦克风音频流                                                    │
│       │                                                          │
│       ▼                                                          │
│  ┌──────────────┐                                                │
│  │  Silero VAD   │ ← 语音/非语音分段                              │
│  └──────┬───────┘                                                │
│         │ 语音段                                                  │
│         │                                                        │
│         ├────────────────────┬───────────────────┐               │
│         ▼                    ▼                   ▼               │
│  ┌────────────┐    ┌──────────────┐    ┌──────────────┐         │
│  │ 声学分支    │    │ ASR 分支      │    │ 声学特征提取  │         │
│  │ Speaker    │    │ sherpa-onnx  │    │ (可选: prosody│         │
│  │ Embedding  │    │ 流式转写      │    │  pitch/energy)│         │
│  │ + 相似度   │    └──────┬───────┘    └──────┬───────┘         │
│  └─────┬──────┘           │                   │                 │
│        │                  ▼                   │                 │
│        │         ┌──────────────┐             │                 │
│        │         │ LLM 语义分支  │             │                 │
│        │         │ Decision     │             │                 │
│        │         │ Token 输出   │             │                 │
│        │         └──────┬───────┘             │                 │
│        │                │                     │                 │
│        ▼                ▼                     ▼                 │
│  ┌─────────────────────────────────────────────────┐           │
│  │              融合决策层 (Fusion Layer)            │           │
│  │  ┌──────────────────────────────────────────┐   │           │
│  │  │ 声学分数 S_acoustic ∈ [0,1]               │   │           │
│  │  │ 语义分数 S_semantic ∈ [0,1]               │   │           │
│  │  │ 融合: S_final = α·S_acoustic + β·S_semantic│   │           │
│  │  │        + γ·(S_acoustic × S_semantic)      │   │           │
│  │  │ 或: 逻辑回归 / 轻量 MLP                   │   │           │
│  │  └──────────────────────────────────────────┘   │           │
│  └──────────────────────┬──────────────────────────┘           │
│                         │                                       │
│                    S_final > 阈值?                               │
│                    ├── NO ──→ silence                           │
│                    └── YES ─→ response / delegation             │
│                                                                  │
│  优点: 声学和语义互补，鲁棒性更好                                 │
│  缺点: 需要设计融合策略，调参复杂                                 │
│  延迟: max(声学, ASR+LLM) ≈ 300-500ms (并行)                     │
└─────────────────────────────────────────────────────────────────┘
```

#### 方案 H3：声学特征注入 LLM（端到端融合）

```
┌─────────────────────────────────────────────────────────────────┐
│                    方案 H3: 特征注入 LLM 架构                      │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  麦克风音频流                                                    │
│       │                                                          │
│       ▼                                                          │
│  ┌──────────────┐                                                │
│  │  Silero VAD   │ ← 语音/非语音分段                              │
│  └──────┬───────┘                                                │
│         │ 语音段                                                  │
│         │                                                        │
│         ├────────────────────┬───────────────────┐               │
│         ▼                    ▼                   ▼               │
│  ┌────────────┐    ┌──────────────┐    ┌──────────────┐         │
│  │ Speaker    │    │ sherpa-onnx  │    │ 声学特征      │         │
│  │ Embedding  │    │ ASR 转写      │    │ (可选)        │         │
│  │ Extractor  │    └──────┬───────┘    └──────┬───────┘         │
│  └─────┬──────┘           │                   │                 │
│        │                  │                   │                 │
│        ▼                  ▼                   │                 │
│  ┌─────────────────────────────────────────────────┐           │
│  │              Prompt 构造层                        │           │
│  │                                                  │           │
│  │  <|speaker_embed|> [0.23, -0.45, 0.67, ...]     │           │
│  │  <|acoustic_features|> pitch=120Hz, energy=0.8  │           │
│  │  <|transcript|> 用户转写文本                      │           │
│  │  <|instruction|> 判断是否在对我说话...            │           │
│  └──────────────────────┬──────────────────────────┘           │
│                         │                                       │
│                         ▼                                       │
│  ┌─────────────────────────────────────────────────┐           │
│  │           LLM (llama.cpp)                        │           │
│  │  输入: prompt + speaker_embedding_token          │           │
│  │  输出: silence / response / delegation          │           │
│  └─────────────────────────────────────────────────┘           │
│                                                                  │
│  优点: 端到端，LLM 可学习声学-语义联合模式                        │
│  缺点: 需要 LLM 支持额外 token 类型（或改造 embedding 层）       │
│        增加 LLM 推理延迟，工程复杂度高                            │
│  延迟: ASR ~100-300ms + LLM(含声学) ~300-800ms                   │
│        = 总计 ~400-1100ms                                        │
└─────────────────────────────────────────────────────────────────┘
```

### 1.3 三种方案对比

| 维度 | H1 级联 | H2 并行融合 | H3 特征注入 LLM |
|------|---------|-------------|-----------------|
| **延迟** | 310-850ms | 300-500ms ⭐ | 400-1100ms |
| **准确率预期** | 中等（级联错误） | 较高（互补融合）⭐⭐ | 最高（联合学习）⭐⭐⭐ |
| **工程复杂度** | 低 ⭐ | 中 | 高 |
| **与现有架构兼容性** | 高（增量添加）⭐⭐⭐ | 中（需融合层） | 低（需改造 LLM） |
| **可解释性** | 高（每阶段可独立调试） | 中 | 低 |
| **CPU 推理可行性** | ✅ 完全可行 | ✅ 完全可行 | ⚠️ 需额外优化 |
| **调参难度** | 低（单阈值） | 中（多权重+阈值） | 高（需微调 LLM） |

**推荐方案：H2（并行融合）**，理由：
1. 延迟最优（并行执行）
2. 声学和语义互补，鲁棒性最好
3. 工程复杂度适中，可在现有架构上增量构建
4. 融合层可先用简单加权，后续升级为学习型融合

---

## 2. 学术界最新进展（2023-2026）

### 2.1 论文汇总表

| 年份 | 论文 | 方法 | 关键指标 | 开源 | 与用户场景相关性 |
|------|------|------|----------|------|------------------|
| 2020 | Personal VAD (Google) | 说话人条件 VAD，ET/SET 架构 | ET: 仅 2.6% 参数达近最优 | ❌ | ⭐⭐⭐⭐⭐ |
| 2022 | Personal VAD 2.0 (Google) | Conformer + FiLM + Speaker PreNet | 8-bit 量化 1.0MB, 27.2% EER | ❌ | ⭐⭐⭐⭐⭐ |
| 2023 | SVVAD (Interspeech) | PVAD 用于说话人验证 | - | ❌ | ⭐⭐⭐⭐ |
| 2024 | TS-VAD+ (ICASSP) | 模块化 TS-VAD，鲁棒说话人日志 | VoxConverse DER 4.55% | ❌ | ⭐⭐⭐ |
| 2024 | MIMO-TSVAD | 多输入多输出 TS-VAD，音视频融合 | VoxConverse DER 4.18% | ❌ | ⭐⭐⭐ |
| 2024 | Flow-TSVAD | 基于 Flow Matching 的 TS-VAD | - | ❌ | ⭐⭐⭐ |
| 2024 | Speaker Conditional Sinc-Extractor PVAD | SincNet 说话人条件特征 | - | ❌ | ⭐⭐⭐⭐ |
| 2024 | Wake Word Reference PVAD (ICASSP) | 唤醒词直接作为参考语音 | 超高召回率 | ❌ | ⭐⭐⭐⭐⭐ |
| 2024 | Ultra-Short Reference PVAD (APSIPA) | 超短参考语音 PVAD | - | ❌ | ⭐⭐⭐⭐ |
| 2025 | EEND-SAA | 无注册主说话人 VAD，自注意力吸引子 | 主说话人 DER 3.61% | ❌ | ⭐⭐⭐ |
| 2025 | Noise-Robust Compact TS-VAD | 因果 DN-APC 预训练 | - | ❌ | ⭐⭐⭐ |
| 2025 | Bi-GRU + Cross-Attention PVAD | Conformer + Bi-GRU + 交叉注意力 | Acc 87.59%, mAP 0.9378 | ❌ | ⭐⭐⭐⭐ |
| 2025 | COIN-AT-PVAD (APSIPA) | 条件中间注意力 PVAD | - | ❌ | ⭐⭐⭐ |
| 2025/26 | ModeratorLM | Speech LLM 自主 turn-taking | Qwen3-4B, 流式 | ❌ | ⭐⭐⭐⭐⭐ |
| 2021 | MPC-BERT (ACL) | 多方对话预训练，addressee 识别 | SOTA +3.51%-5.36% | ❌ | ⭐⭐⭐ |
| 2023 | Multimodal Turn Prediction (ICMI) | Transformer + 注视+IPU 特征 | F1 > 80% | ❌ | ⭐⭐ |
| 2025 | Who Speaks Next (Frontiers in AI) | LLM agent 多方对话轮次 | - | ❌ | ⭐⭐⭐ |

### 2.2 关键论文方法详解

#### 2.2.1 Personal VAD 2.0 — 最直接可参考的架构

**核心方法**：
- 输入：声学特征（log-Mel filterbank）+ 目标说话人 d-vector
- 架构：流式 Conformer 编码器（有限左上下文，无右上下文）
- 说话人嵌入调制方式：
  - **FiLM**: `γ(embed) * features + β(embed)` — 特征层面的线性调制
  - **Speaker PreNet**: 将 d-vector 通过小型网络投影，与声学特征计算余弦相似度后拼接
- 输出：逐帧的目标说话人语音活动概率
- 损失函数：加权交叉熵（对非目标说话人语音给予更高惩罚权重）

**对用户场景的启示**：
- 可以训练一个轻量 Personal VAD 模型，输入为 sherpa-onnx 的声学特征 + 预注册的说话人嵌入
- 输出可直接作为 H2 方案中的声学分数 S_acoustic
- 但训练需要大量标注数据（目标/非目标说话人语音段）

#### 2.2.2 ModeratorLM — 端到端 Speech LLM Turn-Taking

**核心方法**：
- 语音编码器（in-house, variable lookahead）处理 chunk-wise 音频流
- 语音嵌入通过可训练线性投影层注入 LLM（Qwen3-4B）
- **完全由 LLM 自主决定 turn-taking**，不依赖外部 VAD
- 支持角色条件（role-conditioned）的轮次行为
- 推理增强变体（ModeratorLM-Think）使用思维链推理

**对用户场景的启示**：
- 这是最前沿的方向，但需要 4B 参数级 LLM，在纯 CPU 上延迟可能过高
- 核心思想可借鉴：将 turn-taking 决策内化到 LLM 中
- 用户现有的 decision token 框架（silence/response/delegation）与此思路一致

#### 2.2.3 Wake Word Reference PVAD — 最轻量的注册方案

**核心方法** (Zeng et al., ICASSP 2024)：
- 直接使用唤醒词的原始帧级特征作为目标说话人属性
- 无需额外的说话人验证模型提取嵌入
- 实现超高召回率（对语音助手场景至关重要）

**对用户场景的启示**：
- 如果用户的语音代理有唤醒词，可以直接用唤醒词语音段作为注册
- 大幅简化注册流程，无需单独的训练/注册阶段

### 2.3 趋势总结

1. **从 VAD → PVAD → Speech LLM**：学术界正从独立模块走向端到端 Speech LLM
2. **说话人条件机制**：FiLM、交叉注意力、Speaker PreNet 是主流融合方式
3. **轻量化**：8-bit 量化、知识蒸馏、SincNet 特征提取是设备端部署的关键
4. **无注册/少注册**：Wake Word Reference、Enrollment-Less 训练降低使用门槛
5. **多方对话**：ModeratorLM、MPC-BERT 代表从二人对话向多方对话的扩展

---

## 3. 端到端方案可行性

### 3.1 是否有可在 CPU 上运行的端到端模型？

**直接答案：目前没有现成的、可直接部署的端到端"语音输入→说话对象判定"轻量模型。**

但有以下可行路径：

#### 路径 A：组合现有 ONNX 模型（推荐）

sherpa-onnx 生态已支持：
- **说话人分割**：pyannote-segmentation-3.0 (ONNX)
- **说话人嵌入提取**：3D-Speaker (CAM++), NeMo (ECAPA-TDNN), WeSpeaker
- **聚类**：Fast Clustering

可以组合这些模型构建说话人识别管线：
```
音频 → pyannote 分割 → 3D-Speaker 嵌入 → 余弦相似度 → 声学分数
```

**性能参考**（ECAPA-TDNN ONNX, Apple M4 Max CPU）：
- 6 条话语嵌入提取：0.114 秒
- 实时因子：0.008x（远快于实时）
- 同说话人余弦相似度：0.453-0.595
- 不同说话人余弦相似度：0.095-0.276
- 来源: https://dev.to/kiarina/grouping-utterances-by-speaker-with-ecapa-tdnn-and-onnx-runtime-411b

在 Windows x86 CPU 上，预期实时因子约 0.05-0.1x，完全满足实时需求。

#### 路径 B：训练轻量分类器

如果从零训练一个轻量分类器（如 MobileNet + 声学特征）：

**数据需求估算**：
- 最少：100+ 说话人 × 每人 50+ 条语音 = 5,000+ 条标注语音
- 推荐：1,000+ 说话人 × 每人 100+ 条语音 = 100,000+ 条
- 可使用 VoxCeleb、LibriSpeech 等公开数据集

**算力需求估算**：
- MobileNetV3-Small: ~2.5M 参数
- 单 GPU (RTX 3090) 训练：数小时至 1-2 天
- 导出 ONNX 后模型大小：< 10MB
- CPU 推理延迟：< 10ms/帧

**可行性**：✅ 完全可行，但需要标注数据和训练工程

#### 路径 C：微调现有说话人嵌入模型

使用 WeSpeaker 或 3D-Speaker 的预训练模型，在目标域数据上微调：
- WeSpeaker 支持 ONNX 导出，pip 安装即可使用
- 来源: https://github.com/wenet-e2e/wespeaker
- 3D-Speaker CAM++ 已集成在 sherpa-onnx 中
- 来源: https://arxiv.org/html/2303.00332v3

### 3.2 sherpa-onnx 生态内是否有 Personal VAD 模型？

**直接答案：sherpa-onnx 目前没有内置 Personal VAD 模型。**

但 sherpa-onnx 提供了构建 PVAD 所需的所有基础组件：
- 说话人分割模型（pyannote-segmentation-3-0）
- 说话人嵌入模型（3D-Speaker CAM++, NeMo ECAPA-TDNN, WeSpeaker）
- 这些模型都是 ONNX 格式，可在纯 CPU 上运行

**缺失的部分**：将说话人嵌入与 VAD 联合优化的 Personal VAD 模型。Google 的 Personal VAD 未开源。

### 3.3 端到端方案可行性总结

| 方案 | 可行性 | 延迟 | 模型大小 | 开发工作量 |
|------|--------|------|----------|------------|
| sherpa-onnx 说话人嵌入 + 余弦相似度 | ✅ 立即可用 | < 50ms | ~45MB | 低 |
| 训练轻量 MobileNet 分类器 | ✅ 可行 | < 10ms | < 10MB | 中-高 |
| 微调 WeSpeaker/3D-Speaker | ✅ 可行 | < 50ms | ~20MB | 中 |
| 完整 Personal VAD 复现 | ⚠️ 需大量数据 | < 30ms | ~5MB | 高 |
| Speech LLM (ModeratorLM 风格) | ❌ CPU 不可行 | > 1s | > 4GB | 极高 |

---

## 4. 融合策略的工程细节

### 4.1 声学分数和语义分数的融合方法

#### 方法 1：加权平均（推荐起步方案）

```
S_final = α × S_acoustic + (1-α) × S_semantic
```

- α ∈ [0, 1]，控制声学权重的超参数
- 优点：简单、可解释、无训练成本
- 缺点：线性假设可能不最优

#### 方法 2：带交互项的加权融合

```
S_final = α × S_acoustic + β × S_semantic + γ × (S_acoustic × S_semantic)
```

- 交互项捕获"声学和语义都高"的协同效应
- 需要调 3 个超参数

#### 方法 3：逻辑回归融合（推荐进阶方案）

```
S_final = σ(w₁ × S_acoustic + w₂ × S_semantic + w₃ × confidence + b)
```

- 可加入置信度特征（如 ASR 置信度、VAD 段长度）
- 需要少量标注数据训练（几百条即可）
- 可导出为简单公式，推理零成本

#### 方法 4：轻量 MLP

```
S_final = MLP([S_acoustic, S_semantic, confidence, duration, ...])
```

- 2-3 层小型网络，参数量 < 1000
- 需要更多标注数据（几千条）
- 推理延迟可忽略

### 4.2 阈值联合调优

**双阈值策略**（推荐）：

```
if S_acoustic < T_low:
    → silence (快速拒绝，无需语义)
elif S_acoustic > T_high:
    → 进入语义判断
    if S_semantic > T_semantic:
        → response
    else:
        → silence
else:  # T_low ≤ S_acoustic ≤ T_high
    → 进入语义判断（模糊区）
    if S_fusion > T_fusion:
        → response
    else:
        → silence
```

**调优方法**：
1. 收集标注数据（目标说话人/非目标说话人 × 定向/非定向语音）
2. 网格搜索 T_low, T_high, T_semantic, T_fusion
3. 优化目标：最大化 F1 分数（平衡精确率和召回率）
4. 可根据场景调整：陪伴型代理可偏向高召回（宁可误触发，不可漏响应）

### 4.3 声学和语义不一致的处理

**场景 1：声学说"是"（高相似度），语义说"不是"（非定向内容）**

示例：设备主人在对别人说话，声音相似度高，但内容不是对代理说的

处理策略：
- **语义优先**：如果语义置信度高（如明确检测到非定向模式），以语义为准
- 可设置：当 S_semantic < T_semantic_low 时，无论声学分数多高都拒绝
- 这对应 Apple FTM 的核心逻辑：假触发通常来自设备主人对他人说话

**场景 2：声学说"不是"（低相似度），语义说"是"（定向内容）**

示例：访客/家人想使用代理，声音不匹配但内容明确是对代理说的

处理策略：
- **取决于产品定位**：
  - 严格模式：拒绝（仅响应注册用户）
  - 开放模式：接受（允许访客使用）
- 可通过 delegation token 处理：转发给通用处理流程而非个性化流程

**场景 3：两者都不确定**

处理策略：
- 保守策略：silence（避免误触发）
- 激进策略：response（避免漏响应）
- 推荐：陪伴型代理采用略微激进策略，因为误触发的代价低于漏响应

### 4.4 推荐融合策略

```
阶段 1: 声学快速预筛
  ├── S_acoustic < 0.3 → 直接 silence (节省 LLM 推理)
  └── S_acoustic ≥ 0.3 → 进入阶段 2

阶段 2: 语义判断 + 融合
  ├── 并行获取 S_semantic (LLM decision token 置信度)
  ├── S_fusion = 0.4 × S_acoustic + 0.6 × S_semantic
  └── S_fusion > 0.5 → response, 否则 silence
```

权重偏向语义（0.6）的理由：语义判断更直接地回答"是否在对我说话"，声学判断只是辅助。

---

## 5. 与业界方案的差距评估

### 5.1 差距矩阵

| 维度 | Apple Siri | Google PVAD | ModeratorLM | 用户当前技术栈 | 差距 |
|------|-----------|-------------|-------------|---------------|------|
| **麦克风** | 多麦克风阵列 | 单/多麦 | 多通道→单通道 | 单麦 | ⚠️ 中等 |
| **空间信息** | 波束成形、DOA | 部分使用 | 无（downmix） | 无 | ⚠️ 中等 |
| **硬件** | Apple Neural Engine | Edge TPU / 手机 DSP | GPU | 纯 CPU | ⚠️ 中等 |
| **声学模型** | 定制 DNN-HMM | Conformer (自研) | Speech Encoder (自研) | sherpa-onnx (开源) | ✅ 小 |
| **说话人模型** | 定制 DNN | d-vector (自研) | 无独立模块 | 3D-Speaker / WeSpeaker | ✅ 小 |
| **语义模型** | ASR Lattice RNN | 无（仅 VAD） | Qwen3-4B | llama.cpp + decision token | ✅ 小 |
| **注册流程** | 5 句唤醒词 | 需注册语音 | 无注册 | 可自定义 | ✅ 无差距 |
| **端到端优化** | 全链路联合优化 | 全链路联合优化 | 端到端训练 | 模块独立 | ⚠️ 中等 |
| **训练数据** | 亿级真实数据 | 亿级内部数据 | 合成数据 | 公开数据集 | ❌ 大 |
| **功耗** | < 1mW (always-on) | 低功耗 | 高功耗 | 桌面级（不敏感） | ✅ 无差距 |

### 5.2 可通过算法弥补的差距

1. **单麦无空间信息**：
   - 弥补方案：加强说话人嵌入模型的判别力（ECAPA-TDNN 在单麦条件下表现优秀）
   - 利用语义上下文弥补：对话历史、称呼语（"嘿，助手"）等
   - 参考：Single Microphone Own Voice Detection 论文（arXiv 2603.02724）使用模拟声学传递函数增强单麦检测

2. **模块独立 vs 端到端优化**：
   - 弥补方案：使用 H2 并行融合架构，通过融合层学习模块间的互补关系
   - 可对融合层进行端到端微调（固定各模块，仅训练融合参数）

3. **训练数据不足**：
   - 弥补方案：使用公开数据集（VoxCeleb, LibriSpeech）+ 数据增强
   - 利用 LLM 的零样本/少样本能力进行语义判断
   - 合成数据：使用 TTS 生成多说话人对话数据

### 5.3 硬件限制无法逾越的差距

1. **多麦克风阵列的空间信息**：单麦无法获取波达方向（DOA），这是物理限制
   - 影响：无法利用声源定位区分说话人
   - 缓解：在桌面场景中，用户通常正对设备，距离最近，音量最大，可部分弥补

2. **专用 AI 加速器**：纯 CPU 推理延迟高于 NPU/TPU
   - 影响：无法运行大模型（如 ModeratorLM 的 4B 参数 Speech LLM）
   - 缓解：桌面 CPU 性能足够运行 sherpa-onnx + 7B/13B 量化 LLM，延迟在可接受范围

3. **Always-on 低功耗**：桌面场景不需要，这是优势而非劣势
   - 桌面供电充足，可以使用更大模型、更复杂的处理

### 5.4 用户技术栈的独特优势

1. **LLM decision token 框架**：业界方案（Apple/Google）的语义判断较浅（ASR 格分析），用户的 LLM 可以进行更深层的语义理解
2. **桌面场景**：不受功耗和散热的严格限制，可以使用更大模型
3. **模块化架构**：可以灵活替换各组件，快速迭代
4. **开源生态**：sherpa-onnx + llama.cpp 社区活跃，持续改进

---

## 6. 参考文献

### 核心论文

1. Ding, S. et al. "Personal VAD: Speaker-Conditioned Voice Activity Detection." Odyssey 2020.
   - 来源: https://google.github.io/speaker-id/publications/PersonalVAD/

2. Ding, S. et al. "Personal VAD 2.0: Optimizing Personal Voice Activity Detection for On-Device Speech Recognition." Interspeech 2022.
   - 来源: https://arxiv.org/abs/2204.03793

3. Apple ML Research. "Hey Siri: An On-device DNN-powered Voice Trigger for Apple's Personal Assistant." 2017.
   - 来源: https://machinelearning.apple.com/research/hey-siri

4. Apple ML Research. "Personalized Hey Siri." 2018.
   - 来源: https://machinelearning.apple.com/research/personalized-hey-siri

5. Apple ML Research. "Voice Trigger System for Siri." 2023.
   - 来源: https://machinelearning.apple.com/research/voice-trigger

6. Apple ML Research. "Knowledge Transfer for Efficient On-device False Trigger Mitigation."
   - 来源: https://machinelearning.apple.com/research/on-device-false-trigger

7. "Adaptive Turn-Taking for Real-time Multi-Party Voice Agents" (ModeratorLM). arXiv 2606.13544, 2025/2026.
   - 来源: https://arxiv.org/html/2606.13544v2

8. Gu, J.C. et al. "MPC-BERT: A Pre-Trained Language Model for Multi-Party Conversation Understanding." ACL 2021.
   - 来源: https://aclanthology.org/2021.acl-long.285.pdf

9. "Who Says What to Whom: A Survey of Multi-Party Conversations." IJCAI 2022.
   - 来源: https://www.ijcai.org/proceedings/2022/0768.pdf

### PVAD 改进论文

10. Kang, J. et al. "SVVAD: Personal Voice Activity Detection for Speaker Verification." Interspeech 2023.

11. Zeng, B. et al. "Efficient Personal Voice Activity Detection with Wake Word Reference Speech." ICASSP 2024.
    - 来源: https://sites.duke.edu/dkusmiip/files/2024/03/icassp24_zengbang.pdf

12. Xu, L. et al. "Personal Voice Activity Detection with Ultra-Short Reference Speech." APSIPA 2024.

13. Yu, E.L. et al. "Speaker Conditional Sinc-Extractor for Personal VAD." Interspeech 2024.

14. "Empirical Analysis of Learning Improvements in Personal VAD." Electronics, 2025.
    - 来源: https://www.mdpi.com/2079-9292/14/12/2372

### TS-VAD 论文

15. "TS-VAD+: Modularized Target-Speaker Voice Activity Detection for Robust Speaker Diarization."
    - 来源: https://www.semanticscholar.org/paper/Ts-Vad%2B

16. "MIMO-TSVAD: Multi-Input Multi-Output Target-Speaker Voice Activity Detection." arXiv 2401.08052.
    - 来源: https://arxiv.org/html/2401.08052v3

17. "Flow-TSVAD: Target-Speaker Voice Activity Detection via Latent Flow Matching." 2024.

18. "EEND-SAA: Enrollment-Less Main Speaker Voice Activity Detection Using Self-Attention Attractors." 2025.

### 工具和模型

19. sherpa-onnx Speaker Diarization 文档.
    - 来源: https://k2-fsa.github.io/sherpa/onnx/speaker-diarization/index.html

20. Wang, H. et al. "CAM++: A Fast and Efficient Network for Speaker Embedding." 2023.
    - 来源: https://arxiv.org/html/2303.00332v3

21. Wang, H. et al. "Wespeaker: A Research and Production oriented Speaker Embedding Learning Toolkit." 2022.
    - 来源: https://ar5iv.labs.arxiv.org/html/2210.17016

22. "Grouping Utterances by Speaker with ECAPA-TDNN and ONNX Runtime."
    - 来源: https://dev.to/kiarina/grouping-utterances-by-speaker-with-ecapa-tdnn-and-onnx-runtime-411b

### 其他相关

23. "Single Microphone Own Voice Detection based on Simulated Transfer Functions for Hearing Aids." arXiv 2603.02724.
    - 来源: https://arxiv.org/html/2603.02724v1

24. Lee, M.C. et al. "Multimodal Turn Analysis and Prediction for Multi-party Conversations." ICMI 2023.
    - 来源: https://graphics.cs.uh.edu/wp-content/papers/2023/2023-ICMI-MultimodalTurnAnalysis.pdf

25. "Voice AI Infrastructure: Building Real-Time Speech Agents." 2025.
    - 来源: https://introl.com/blog/voice-ai-infrastructure-real-time-speech-agents-asr-tts-guide-2025

26. "How Real-Time Voice AI Actually Works." Retell AI, 2026.
    - 来源: https://www.retellai.com/blog/how-real-time-voice-ai-works-stt-llm-tts

---

## 附录：推荐实施路线图

### 第一阶段（1-2 周）：声学预筛原型
- 集成 3D-Speaker CAM++ 或 WeSpeaker ECAPA-TDNN 到现有管线
- 实现说话人注册（录制 3-5 条语音 → 提取嵌入 → 存储）
- 实现实时余弦相似度计算
- 评估在真实环境中的区分度

### 第二阶段（2-4 周）：融合架构
- 实现 H2 并行融合架构
- 先用加权平均融合，收集数据
- 标注 500-1000 条语音段（目标/非目标 × 定向/非定向）
- 训练逻辑回归融合层

### 第三阶段（4-8 周）：优化和调优
- 联合阈值调优
- 处理边界情况（声学/语义不一致）
- A/B 测试：纯语义 vs 混合路线
- 延迟优化

### 第四阶段（可选）：轻量 PVAD 训练
- 使用公开数据集 + 自采集数据
- 训练 MobileNet 或轻量 Conformer 分类器
- 导出 ONNX，集成到 sherpa-onnx 管线
