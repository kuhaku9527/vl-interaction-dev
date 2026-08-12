# 说话对象判定（Addressee Detection）——桌面陪伴型语音代理场景研究

> **日期**: 2026-08-12  
> **作者**: Alice 27 (Wind AI)  
> **背景**: Windows 桌面陪伴型 Voice Agent，技术栈 sherpa-onnx + Silero VAD + llama.cpp + decision token (silence/response/delegation)

---

## 目录

1. [问题精确定义](#1-问题精确定义)
2. [接口设计建议](#2-接口设计建议)
3. [业界产品调研](#3-业界产品调研)
4. [学术定义对齐](#4-学术定义对齐)
5. [场景特殊性分析](#5-场景特殊性分析)
6. [参考文献](#6-参考文献)

---

## 1. 问题精确定义

### 1.1 场景描述

用户在 Windows 桌面环境下运行一个常驻语音代理（Voice Agent），使用 **live 模式免唤醒词常驻监听**。用户离麦克风很近（近场），可能同时在打游戏、自言自语、或与身边人交谈。当前系统缺少"说话对象判定"能力——即无法区分用户是在对 AI 说话还是在跟别人说话（或自言自语）。

### 1.2 核心问题

**"说话对象判定"（Addressee Detection）** 在桌面陪伴场景下定义为：

> 给定一段检测到语音活动（VAD=1）的音频流，判断该段语音的**意图接收方**是否为 AI 代理（即用户是否在对 AI 说话），输出二分类结果：**AI-directed（面向AI）** vs **Non-AI-directed（非面向AI，包括自言自语、与旁人交谈等）**。

### 1.3 输入/输出定义

| 维度 | 描述 |
|------|------|
| **输入** | 音频流（实时帧级），可选附加上下文：ASR 文本假设（1-best / lattice）、说话人嵌入向量（speaker embedding）、历史对话上下文 |
| **输出** | 帧级或段级二分类标签：`AI_DIRECTED` / `NOT_AI_DIRECTED`（或三分类：`TARGET_SPEECH` / `NON_TARGET_SPEECH` / `NON_SPEECH`） |
| **粒度** | 帧级（~10-30ms）或段级（utterance-level，由 VAD 端点切割后的完整语音段） |
| **延迟要求** | 流式/在线（streaming），延迟 < 100ms（帧级）或 < 500ms（段级，在 VAD 终点后） |

### 1.4 与现有模块的关系

```
┌─────────────────────────────────────────────────────────┐
│                    音频输入 (单麦)                        │
└──────────────────────┬──────────────────────────────────┘
                       │
                       ▼
              ┌─────────────────┐
              │   Silero VAD    │  ← 语音/非语音检测
              │  (现有模块)      │
              └────────┬────────┘
                       │ speech frames
                       ▼
         ┌─────────────────────────────┐
         │  Addressee Detection (新)    │  ← ★ 本研究的核心模块
         │  说话对象判定                │
         │  输出: AI-directed? Y/N      │
         └─────────────┬───────────────┘
                       │ AI-directed speech
                       ▼
              ┌─────────────────┐
              │  sherpa-onnx    │  ← 流式 ASR
              │  (现有模块)      │
              └────────┬────────┘
                       │ text
                       ▼
              ┌─────────────────┐
              │  llama.cpp      │  ← LLM decision token
              │  decision token │     silence/response/delegation
              └─────────────────┘
```

**关键设计决策**：Addressee Detection 应放在 VAD 之后、ASR 之前（或与 ASR 并行），作为 ASR 的**门控（gating）模块**。只有被判定为 AI-directed 的语音才送入 ASR 和 LLM，从而：

- **降低计算开销**：避免对非目标语音做完整的 ASR + LLM 推理
- **减少误触发**：防止代理对用户与旁人的对话做出响应
- **保护隐私**：非目标语音不进入后续处理管线

### 1.5 与 Decision Token 的关系

当前 decision token 框架（silence / response / delegation）处理的是"ASR 文本出来后要不要回复"的问题。Addressee Detection 处理的是更上游的问题——"这段语音值不值得做 ASR"。两者互补：

| 模块 | 输入 | 输出 | 解决的问题 |
|------|------|------|-----------|
| Addressee Detection | 原始音频帧 | AI-directed / Not | 用户在对谁说话？ |
| Decision Token | ASR 文本 + 上下文 | silence / response / delegation | 代理应该回复吗？ |

---

## 2. 接口设计建议

### 2.1 推荐接口方案：帧级流式三分类

借鉴 Google Personal VAD 的设计（Ding et al., 2020），推荐以下接口：

```
输入:
  - audio_frame: float32[N_samples]  # 原始音频帧，如 10ms/帧
  - target_speaker_embedding: float32[D]  # 可选，已注册的目标说话人嵌入向量
  - (可选) asr_hypothesis: str  # ASR 1-best 假设文本（用于多模态融合）

输出:
  - class_probs: float32[3]  # [P(non-speech), P(target-speech), P(non-target-speech)]
  - decision: enum {NON_SPEECH, TARGET_SPEECH, NON_TARGET_SPEECH}
```

### 2.2 两种部署模式

#### 模式 A：无说话人注册（Enrollment-less）

- 不依赖预先注册的说话人声纹
- 仅依赖声学-语言学特征判断"是否在对 AI 说话"
- 优点：零设置成本，开箱即用
- 缺点：精度较低，难以区分"用户对 AI 说话"和"用户对旁人说话但语气相似"

#### 模式 B：有说话人注册（Enrollment-based）

- 用户预先录制 3-10 秒注册语音，提取 speaker embedding
- 模型以 speaker embedding 为条件，只检测目标说话人的语音
- 优点：精度高，可同时解决"谁在说话"和"在对谁说话"
- 缺点：需要注册步骤；如果用户声音与注册时差异大（感冒、情绪激动等），性能下降

### 2.3 推荐架构（适配 Windows 单机 CPU）

参考 Google Personal VAD 的 ET（Embedding + concaT）架构：

```
音频帧 ──→ [声学特征提取] ──→ concat ──→ [轻量 Bi-GRU / CRNN] ──→ 三分类输出
                                   │
目标说话人嵌入 ─────────────────────┘
```

- **模型大小**：130K-500K 参数（8-bit 量化后约 130-500 KB）
- **推理延迟**：< 5ms/帧（纯 CPU）
- **训练数据**：人工构造的多说话人混合对话数据（LibriSpeech 等拼接）+ 真实对话数据微调

---

## 3. 业界产品调研

### 3.1 主流语音助手对比

| 产品 | 唤醒词 | 是否有专门 Addressee Detection | 方案概述 | 免唤醒连续对话 |
|------|--------|-------------------------------|---------|---------------|
| **Apple Siri** | "Hey Siri" / "Siri" | ✅ 有（DDSD + FTM） | 两级：Voice Trigger（本地）+ Device-Directed Speech Detection（多模态融合 ASR 文本+声学+LLM）+ False Trigger Mitigation（利用 ASR lattice 不确定性） | ❌ 需唤醒词触发 |
| **Google Assistant / Gemini** | "Hey Google" / "OK Google" | ✅ 有（Personal VAD） | Personal VAD 1.0/2.0：帧级三分类（非语音/目标说话人/非目标说话人），130K 参数，用于 gating 流式 ASR | ✅ Continued Conversation（唤醒后 8 秒窗口内免唤醒） |
| **Amazon Alexa** | "Alexa" / "Echo" | ⚠️ 有限 | 主要依赖唤醒词 + 云端二次验证（两阶段：本地检测疑似唤醒词 → 云端确认）。无公开的专门 addressee detection 模块 | ✅ Follow-Up Mode（类似 Continued Conversation） |
| **小爱同学** | "小爱同学" | ❌ 无公开信息 | 依赖唤醒词 + 端点检测。误唤醒问题常见（用户聊天/看电视时误触发），社区反馈建议使用麦克风阵列做二次判断 | ❌ |
| **天猫精灵** | "天猫精灵" / "你好天猫" | ❌ 无公开信息 | 同小爱同学，依赖唤醒词方案 | ❌ |
| **InnerZero (2026 PC)** | 无（Push-to-Talk） | ❌ 无 | 使用按键触发而非常驻监听，明确回避了 addressee detection 问题。开发者表示"开源唤醒词栈尚未达到隐私和误触发标准" | N/A（PTT） |
| **MoltBot / Clawbot** | 自定义唤醒词 | ⚠️ 有限 | 唤醒词 + VAD 端点检测 + Talk Mode（VAD 判断用户说完后自动继续监听），但无专门 addressee detection | ✅ Talk Mode |

### 3.2 关键发现

1. **主流产品几乎都依赖唤醒词作为第一道防线**。唤醒词本质上是一种"隐式的 addressee detection"——用户说出特定短语即表明意图与设备交互。

2. **Apple 和 Google 在唤醒词之外有专门的 addressee detection 研究**：
   - Apple 的 **Device-Directed Speech Detection (DDSD)** 是学术界最接近"说话对象判定"的工业方案，使用多模态融合（声学 + ASR 文本 + ASR 置信度），最新方案甚至引入 LLM 做文本生成式分类（EER 7.45%）。
   - Google 的 **Personal VAD** 是帧级说话人条件 VAD，专门用于 gating 流式 ASR，130K 参数即可在设备端实时运行。

3. **Continued Conversation 模式的困境**：Google 的 Continued Conversation 在唤醒后保持麦克风开启 8 秒，但用户反馈"我们互相说话时它也会误以为在跟它说话"（Android Police 用户评论）。这说明**仅靠 VAD + 时间窗口无法解决 addressee detection 问题**。

4. **桌面 PC 语音代理普遍回避此问题**：InnerZero 使用 Push-to-Talk，MoltBot 使用唤醒词 + VAD，均未实现真正的免唤醒常驻监听 + addressee detection。

### 3.3 中国语音助手生态

中国主流语音助手（小爱同学、天猫精灵、小度等）均采用唤醒词方案，**未公开任何 addressee detection 相关技术**。误唤醒是用户反馈的高频问题（如"小爱同学自己突然说话"），社区讨论的解决方案主要是：
- 使用麦克风阵列做声源定位
- 追踪用户使用状态做二次判断
- 优化唤醒词本身（避免短唤醒词、叠字等）

---

## 4. 学术定义对齐

### 4.1 核心概念辨析

学术界存在多个相关但不同的概念，需要严格区分：

| 概念 | 英文术语 | 定义 | 与本问题的关系 |
|------|---------|------|---------------|
| **说话对象判定** | Addressee Detection | 判断一段语音的**意图接收方**是谁（AI 还是其他人） | ★ 核心问题 |
| **设备定向语音检测** | Device-Directed Speech Detection (DDSD) | 判断语音是否**面向设备/语音助手** | ★ 等价于本问题（Apple 术语） |
| **个人语音活动检测** | Personal VAD / Target-Speaker VAD | 检测**特定目标说话人**的语音活动（帧级） | ● 相关但不同：解决"谁在说话"而非"在对谁说话" |
| **说话人分割聚类** | Speaker Diarization | 回答"谁在什么时候说话" | ○ 间接相关：可作为上游特征 |
| **说话人验证** | Speaker Verification | 验证一段语音是否属于声称的说话人 | ○ 间接相关：可为 Personal VAD 提供 embedding |
| **语音活动检测** | Voice Activity Detection (VAD) | 区分语音/非语音 | ○ 基础组件 |
| **唤醒词检测** | Keyword Spotting (KWS) / Wake Word Detection | 检测特定触发短语 | ● 隐式的 addressee detection（简化方案） |

### 4.2 关键论文

#### Google Personal VAD (2020)
- **标题**: *Personal VAD: Speaker-Conditioned Voice Activity Detection*
- **作者**: Shaojin Ding, Quan Wang, Shuo-yiin Chang, Li Wan, Ignacio Lopez Moreno (Google Inc.)
- **发表**: Speaker Odyssey 2020
- **核心贡献**:
  - 提出帧级三分类：non-speech (ns) / target speaker speech (tss) / non-target speaker speech (ntss)
  - 四种架构对比：ET（Embedding concaT）最优，仅 130K 参数
  - 提出 Weighted Pairwise Loss (WPL)，对不同类型混淆错误赋予不同权重
  - 目标场景：gating 流式 ASR，减少计算和功耗
- **局限**: 解决的是"目标说话人是否在说话"，而非"目标说话人是否在对 AI 说话"

#### Google Personal VAD 2.0 (2022)
- **标题**: *Personal VAD 2.0: Optimizing Personal Voice Activity Detection for On-Device Speech Recognition*
- **作者**: Shaojin Ding, Rajeev Rikhye, Ian McGraw (Google)
- **发表**: INTERSPEECH 2022
- **核心改进**:
  - 支持 enrollment 和 enrollment-less 两种场景
  - 使用 FiLM（Feature-wise Linear Modulation）替代简单 concat 做 speaker embedding 调制
  - 针对生产环境的训练数据增强策略

#### Apple Device-Directed Speech Detection (2024)
- **标题**: *Multimodal AI Approach to Device-Directed Speech Detection with Large Language Models*
- **发表**: Apple Machine Learning Research, 2024
- **核心贡献**:
  - 将 DDSD 建模为文本生成问题
  - 多模态融合：声学 embedding（Whisper/CLAP encoder）+ ASR 文本假设 + ASR 置信度
  - 使用 LLM 做最终分类
  - EER 7.45%（CLAP backbone）
- **局限**: 依赖 ASR 输出，延迟较高；模型较大，不适合纯本地轻量部署

#### Apple Voice Trigger System (2023)
- **标题**: *Voice Trigger System for Siri*
- **发表**: Apple Machine Learning Research
- **核心内容**:
  - 区分设备主要用户与其他说话人
  - 两级触发：本地 Voice Trigger + 云端 False Trigger Mitigation (FTM)
  - FTM 利用 ASR lattice 的不确定性（而非仅 1-best）判断是否为真实触发
  - 支持更短的触发词 "Siri"（去 "Hey"）

#### Voice Assistant Query Rejection Benchmark (2025)
- **标题**: *A Benchmark for Voice Assistant Query Rejection in Smart Speakers*
- **发表**: arXiv 2512.10257
- **核心贡献**:
  - 系统定义了语音助手需要拒绝的 7 类无效输入
  - 其中 **Type 4: Non-assistant-directed chat (multi-person or self-talk)** 直接对应本问题
  - 提供了标注数据集和评测基准

### 4.3 评测指标

| 指标 | 全称 | 含义 | 适用场景 |
|------|------|------|---------|
| **EER** | Equal Error Rate | FAR=FRR 时的错误率 | DDSD 标准指标 |
| **AP_tss** | Average Precision (target speaker speech) | 目标说话人语音检测的平均精度 | Personal VAD |
| **FAR** | False Accept Rate | 非目标语音被误判为目标语音的比例 | 误触发控制 |
| **FRR** | False Reject Rate | 目标语音被误判为非目标语音的比例 | 用户体验 |
| **Accuracy** | 帧级/段级分类准确率 | 三分类整体准确率 | 综合评估 |
| **Latency** | 推理延迟 | 单帧推理时间 | 实时性要求 |

---

## 5. 场景特殊性分析

### 5.1 桌面陪伴场景 vs 智能音箱场景

| 维度 | 桌面陪伴（本场景） | 智能音箱（Siri/Alexa/Google） |
|------|-------------------|------------------------------|
| **麦克风** | 单麦（PC 内置或 USB 麦） | 多麦阵列（2-8 个） |
| **声场** | 近场（< 0.5m） | 远场（1-5m） |
| **声源定位** | ❌ 不可用（单麦无法做波束成形） | ✅ 可用（DOA 估计 + 波束成形） |
| **信噪比** | 较高（近场，直达声为主） | 较低（远场，混响+噪声） |
| **背景噪声** | 游戏音、键盘声、旁人说话 | 电视声、厨房噪声、多人对话 |
| **交互模式** | 免唤醒常驻监听（live 模式） | 唤醒词触发 + 可选 Continued Conversation |
| **用户位置** | 固定（坐在电脑前） | 不固定（在房间内移动） |
| **多说话人** | 偶尔（身边有人时） | 常见（家庭场景多人） |
| **摄像头** | 可能可用（PC 通常有摄像头） | 通常不可用 |
| **算力** | 桌面 CPU（相对充裕） | 嵌入式芯片（高度受限） |

### 5.2 可利用的简化假设

桌面陪伴场景相比智能音箱场景，有以下**有利的简化假设**：

#### H1: 近场单说话人主导
用户离麦克风最近，其语音能量远高于旁人。可以利用**能量比**作为强特征——如果检测到语音但能量显著低于用户正常水平，大概率不是用户在说话。

#### H2: 用户位置固定
用户坐在电脑前，声学环境相对稳定。可以建立"用户声学画像"（包括典型音量、频谱特征等），偏离此画像的语音段可能是旁人。

#### H3: 摄像头可用（多模态）
PC 通常配备摄像头，可以利用**视觉线索**辅助判定：
- 用户是否面朝屏幕？（face orientation）
- 用户嘴唇是否在动？（lip movement 与音频同步）
- 用户是否在看向摄像头方向？（gaze detection）

#### H4: 游戏场景的声学特征
游戏音是可预测的背景噪声（非语音），对 VAD 和 addressee detection 的干扰主要是降低 SNR，而非产生假阳性语音段。

#### H5: 自言自语的特征差异
用户自言自语时，通常具有与"对 AI 说话"不同的声学-语言学特征：
- 音量更低
- 语速更慢/更不规律
- 句法结构更碎片化
- 缺乏"指令式"语调（command-like prosody）

### 5.3 场景特有的挑战

#### C1: 无唤醒词锚点
免唤醒常驻监听意味着没有明确的"交互开始"信号。系统必须在连续音频流中实时判断每一帧是否面向 AI，无法像智能音箱那样在唤醒词后假设后续语音都是 device-directed。

#### C2: 游戏中的情绪化语音
用户打游戏时可能大喊、咒骂、欢呼——这些语音能量高、情绪化，容易被误判为"在对 AI 说话"（因为音量大、语调强烈）。

#### C3: 单麦无法做空间滤波
智能音箱可以用波束成形聚焦于唤醒词来源方向，后续只处理该方向的语音。桌面单麦无法利用空间信息区分说话人。

#### C4: 训练数据稀缺
学术界和工业界的 addressee detection 研究主要面向智能音箱场景（有唤醒词锚点、多麦阵列）。桌面常驻监听场景的标注数据几乎不存在。

### 5.4 推荐的技术路线

基于以上分析，推荐**分层渐进**的技术路线：

```
Phase 1: 基于声学特征的轻量方案（快速可用）
  ├── 说话人注册 → speaker embedding
  ├── Personal VAD 式帧级三分类（130K 参数，CPU 实时）
  └── 仅依赖音频，无需 ASR

Phase 2: 引入 ASR 文本特征（提升精度）
  ├── 对 VAD 段做轻量 ASR（如 sherpa-onnx 已就绪）
  ├── 文本特征：是否有指令式句法？是否有呼语（"嘿"、"那个"）？
  └── 声学+文本多模态融合

Phase 3: 引入视觉特征（可选，进一步降低误触发）
  ├── 摄像头：face orientation / lip movement / gaze
  ├── 用户面朝屏幕 + 嘴唇在动 + 有语音 → 大概率在对 AI 说话
  └── 用户背对屏幕 + 有语音 → 大概率在跟旁人说话
```

---

## 6. 参考文献

### 学术论文

1. Ding, S., Wang, Q., Chang, S., Wan, L., & Lopez Moreno, I. (2020). **Personal VAD: Speaker-Conditioned Voice Activity Detection**. *Speaker Odyssey 2020*. [PDF](https://www.isca-archive.org/odyssey_2020/ding20_odyssey.pdf) | [arXiv:1908.04284](https://arxiv.org/abs/1908.04284)

2. Ding, S., Rikhye, R., & McGraw, I. (2022). **Personal VAD 2.0: Optimizing Personal Voice Activity Detection for On-Device Speech Recognition**. *INTERSPEECH 2022*. [PDF](https://www.isca-archive.org/interspeech_2022/ding22_interspeech.pdf) | [arXiv:2204.03793](https://arxiv.org/abs/2204.03793)

3. Apple Machine Learning Research. (2024). **Device-Directed Speech Detection: Adaptive Knowledge Distillation and Multimodal LLM Approach**. [Apple ML Research](https://machinelearning.apple.com/research/device-directed)

4. Apple Machine Learning Research. (2023). **Voice Trigger System for Siri**. [Apple ML Research](https://machinelearning.apple.com/research/voice-trigger)

5. Anonymous. (2025). **A Benchmark for Voice Assistant Query Rejection in Smart Speakers**. *arXiv:2512.10257*. [PDF](https://arxiv.org/pdf/2512.10257)

6. Empirical Analysis of Learning Improvements in Personal VAD. (2025). *MDPI Electronics*, 14(12), 2372. [DOI](https://www.mdpi.com/2079-9292/14/12/2372)

### 工业产品与技术文章

7. Picovoice. (2026). **Wake Word Detection Guide 2026: Complete Technical Overview**. [URL](https://picovoice.ai/blog/complete-guide-to-wake-word)

8. DaVoice. (2026). **Complete Guide to On-Device Wake Word Detection 2026**. [URL](https://davoice.io/wake-word-guide)

9. Google Blog. (2026-04-21). **Make chats more natural and efficient with Continued Conversation, now in Gemini for Home**. [URL](https://blog.google/products-and-platforms/devices/how-to-use-gemini-continued-conversation)

10. Voicebot.ai. (2018-06-21). **Google Home Introduces Continued Conversation Feature in US**. [URL](https://voicebot.ai/2018/06/21/google-home-introduces-continued-conversation-feature-in-us)

11. Android Police. (2024). **How one overlooked Google Assistant feature changed the way I use my phone**. [URL](https://www.androidpolice.com/google-assistant-continued-conversation)

12. InnerZero. (2026). **The Best AI Voice Assistant for PC in 2026: A Realistic Jarvis**. [URL](https://innerzero.com/blog/best-ai-voice-assistant-jarvis-pc-2026)

13. Ruhr-Universität Bochum. (2020-06-30). **When speech assistants listen even though they shouldn't**. [URL](https://news.rub.de/english/press-releases/2020-06-30-it-security-when-speech-assistants-listen-even-though-they-shouldnt)

14. MarkTechPost. (2024-03-24). **Apple Researchers Propose a Multimodal AI Approach to Device-Directed Speech Detection with Large Language Models**. [URL](https://www.marktechpost.com/2024/03/24/apple-researchers-propose-a-multimodal-ai-approach-to-device-directed-speech-detection-with-large-language-models)

15. 今日头条. (2022-11-28). **智能语音助手命名与唤醒词收集**. [URL](https://www.toutiao.com/article/7171053338513949188/)

16. 智和家. (2021-07-20). **小爱同学自己突然说话**. [URL](http://m.zhihejia.com/ask/18950.html)

### 技术概念参考

17. EmergentMind. **Target-Speaker Voice Activity Detection (TS-VAD)**. [URL](https://www.emergentmind.com/topics/target-speaker-voice-activity-detection-ts-vad)

18. Dusun IoT. **Microphone Array Ultimate Guide: Near-field vs Far-field**. [URL](https://www.dusuniot.com/blog/microphone-array)

19. SISTC Acoustic Insights. **Microphone Array Voice Recognition**. [URL](https://sistc.com/en-blog-microphone-array-voice-recognition)

---

## 附录：术语对照表

| 中文 | 英文 | 缩写 |
|------|------|------|
| 说话对象判定 | Addressee Detection | AD |
| 设备定向语音检测 | Device-Directed Speech Detection | DDSD |
| 个人语音活动检测 | Personal Voice Activity Detection | Personal VAD / PVAD |
| 目标说话人语音活动检测 | Target-Speaker Voice Activity Detection | TS-VAD |
| 语音活动检测 | Voice Activity Detection | VAD |
| 唤醒词检测 | Keyword Spotting / Wake Word Detection | KWS |
| 说话人验证 | Speaker Verification | SV |
| 说话人分割聚类 | Speaker Diarization | SD |
| 误触发缓解 | False Trigger Mitigation | FTM |
| 等错误率 | Equal Error Rate | EER |
| 错误接受率 | False Accept Rate | FAR |
| 错误拒绝率 | False Reject Rate | FRR |
| 特征线性调制 | Feature-wise Linear Modulation | FiLM |
| 加权成对损失 | Weighted Pairwise Loss | WPL |
