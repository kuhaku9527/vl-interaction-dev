# 全双工语音对话技术全景深度研究报告

> **研究日期**: 2026-08-11  
> **研究目标**: 为自研本地实时语音代理（VLM 主动看/说/决策 + 流式 ASR + 唤醒词 KWS + 大模型 + TTS，llama.cpp + sherpa-onnx 纯 CPU 推理）提供架构参考  
> **研究范围**: 端到端 S2S 与级联流式两条技术路线、实时双工核心机制、本地/低算力 Windows 部署评估、桌面陪伴型场景适配  
> **边界条件**: (1) 接受推倒重来，不做将就式打补丁；(2) 部署形态不限于纯本地，按模块给出本地/云端两档评估；(3) 结论可交叉验证

---

## 目录

1. [技术路线全景](#第一章-技术路线全景)
2. [端到端 S2S 技术路线](#第二章-端到端-s2s-技术路线)
3. [级联流式技术路线](#第三章-级联流式技术路线)
4. [实时双工核心机制深度拆解](#第四章-实时双工核心机制深度拆解)
5. [本地/低算力 Windows 部署评估](#第五章-本地低算力-windows-部署评估)
6. [桌面陪伴型场景适配](#第六章-桌面陪伴型场景适配)
7. [技术路线对比矩阵与开源项目排序](#第七章-技术路线对比矩阵与开源项目排序)
8. [结论与架构建议](#第八章-结论与架构建议)

---

全双工语音对话（Full-Duplex Voice Conversation）是 2024–2026 年 AI 领域最活跃的前沿方向之一。与传统的"一问一答"半双工语音交互不同，全双工系统允许用户和 AI 同时说话、随时打断、自然切换话轮，其技术挑战横跨音频编解码、流式推理、实时传输和对话状态管理等多个领域。本章从宏观视角梳理截至 2026 年 8 月已收敛的三条主要技术路线，为后续各章的深入分析建立框架。

## 1.1 三条技术路线的收敛

截至 2026 年中，端到端语音对话系统已形成三条清晰的技术路线，每条路线在延迟、推理质量、工程复杂度和部署灵活性之间存在不同的权衡。

**路线一：级联流式架构（Cascaded Streaming Pipeline）**。这是当前生产环境的默认方案，将语音对话拆解为流式 ASR → LLM → TTS 三个独立阶段，每个阶段在上一个阶段完成之前就开始输出。代表项目包括 LiveKit Agents（12.6K GitHub Stars）、Pipecat（14K Stars）和 FunASR（25.3K Stars），商业平台 Vapi、Bland AI 和 Retell AI 也全部采用此架构。级联方案的核心优势在于模块化——ASR、LLM、TTS 可独立选型、独立升级、独立调试，且天然支持 Function Calling 和 RAG 上下文注入。经过全流式优化后，端到端延迟可从传统的 3–5 秒压缩至 500–800ms，逼近 300ms 的理论下限。Salesforce AI Research 在 2025 年的企业级基准测试（arXiv:2603.05413）中实测 Deepgram + vLLM + ElevenLabs 组合的 P50 首音频延迟（TTFA）为 947ms，最佳可达 729ms。

**路线二：音频原生 S2S（Speech-to-Speech）**。这条路线试图从根本上消除级联架构的延迟累积和信息损失问题——模型直接处理音频 token，输入和输出都是语音，不经过文本中间层。代表模型包括 Moshi（Kyutai，Apache 2.0 开源）、GPT-4o Realtime API（OpenAI，闭源）、Hertz-dev（Standard Intelligence，Apache 2.0 开源）和 SpeechGPT 2（复旦大学）。Moshi 是这条路线最具参考价值的开源实现：其 Mimi 神经音频编解码器将 24kHz 音频压缩至 12.5Hz/1.1kbps 的离散 token 流，Helium 7B LLM 同时建模用户音频流和系统音频流，实现真正的全双工，端到端延迟约 200ms。然而，音频原生模型的推理能力普遍弱于同规模文本 LLM，且训练数据稀缺、调试困难。

**路线三：Thinker-Talker 分离架构**。这条路线试图兼得前两条路线的优势——Thinker（文本 LLM）负责理解和推理，Talker（语音生成模块）负责将 Thinker 的 hidden states 转换为流式音频。代表模型包括 Qwen2.5-Omni（阿里，7B/3B）、Freeze-Omni（VITA 团队，LLM 完全冻结）和 LLaMA-Omni（中科院，8B）。这条路线最关键的创新在于：LLM 可以冻结或部分冻结，从而完全保留文本 LLM 的推理能力，同时训练成本大幅降低——LLaMA-Omni 仅需 4 个 GPU 训练 3 天，Freeze-Omni 的 LLM 参数完全不参与训练。Qwen2.5-Omni 的 3B 版本通过 OpenVINO 甚至可以在 Intel CPU 上运行，为纯 CPU 部署保留了可能性。

## 1.2 路线选择的决策框架

三条路线并非互斥，而是代表了从"工程成熟"到"体验极致"的光谱。级联流式架构在 2026 年仍是生产环境的默认选择，其模块化特性使其成为自研系统最安全的起点。Thinker-Talker 架构是向端到端演进的最佳中间态——它保留了级联架构的可控性，同时向音频原生方向迈出了关键一步。音频原生 S2S 虽然在延迟和自然度上具有理论优势，但其推理能力损失和工程不成熟使其在 2026 年仍不适合作为自研系统的主架构。

对于桌面陪伴型语音智能体这一具体场景，后续章节将论证：级联流式架构作为当前基础是稳固的，但需要引入 Thinker-Talker 的关键设计思想（特别是 Inner Monologue 和流式打断机制），并在架构层面为未来的音频原生演进预留接口。

## 1.3 报告结构导引

接下来的章节将按以下逻辑展开：第二章和第三章分别深入端到端 S2S 和级联流式两条技术路线，梳理代表性论文与开源项目；第四章聚焦实时双工的核心机制——Turn-Taking、Barge-In、端点检测、流式传输和回声消除；第五章评估各开源项目在 Windows 纯 CPU 环境下的部署可行性；第六章将技术方案映射到桌面陪伴型场景的两种交互模式；第七章给出技术路线对比矩阵和按可借鉴性排序的开源项目清单；第八章综合所有发现，对自研架构的根基进行评估，并给出分阶段实施建议。


---

承接第一章对三条技术路线的宏观定位，本章深入端到端语音到语音（Speech-to-Speech, S2S）路线，系统梳理代表性模型、核心论文和开源项目。对于自研架构而言，S2S 路线的主要价值不在于直接采用，而在于从中提取可迁移到级联架构中的关键设计思想——特别是全双工建模、打断机制和 Inner Monologue。

## 2.1 Moshi：全双工开源标杆

Moshi 由法国非营利研究机构 Kyutai 于 2024 年 9 月发布，是第一个真正意义上的开源全双工实时对话模型。其 GitHub 仓库（kyutai-labs/moshi）截至 2026 年 8 月拥有约 10,800 Stars，采用 Apache 2.0 许可证，支持 PyTorch、MLX（Apple Silicon）和 Rust/Candle 三种推理后端。

Moshi 的架构围绕三个核心组件构建。Mimi 神经音频编解码器将 24kHz 原始音频压缩为帧率 12.5Hz、码率仅 1.1kbps 的离散 token 流，使用残差向量量化（RVQ）将每帧分解为 8 个子序列（Q=8 codebooks）。这一极低帧率设计使得 LLM 能够以可管理的序列长度处理音频——相比之下，Google SoundStream 的 50Hz 帧率会产生 4 倍长的 token 序列。Helium 7B LLM 是 Kyutai 自研的 Temporal Transformer，在 2.1 万亿 token 上预训练，同时建模文本和音频 token。

Moshi 最关键的创新是双音频流建模和 Inner Monologue 机制。模型同时处理两条音频流——用户流（来自麦克风输入）和系统流（模型自回归生成的输出）——以及一条并行的文本 token 流（Inner Monologue）。文本流不输出给用户，而是作为模型的"思考"过程，消融实验证明它显著提升了语音生成质量。这一设计意味着 Moshi 从架构层面原生支持全双工：模型始终处于"听+说"状态，不需要显式的 speaker turn 建模，打断和话轮切换通过训练数据中的自然对话动态习得。端到端延迟约 200ms（在 L4 GPU 上），理论延迟 160ms（80ms Mimi 帧 + 80ms 处理）。

对于自研架构，Moshi 的可借鉴点集中在三个层面：Inner Monologue 机制可以直接迁移到 Thinker-Talker 架构中（Thinker 输出文本 + hidden states，Talker 基于两者生成语音）；Mimi 的 12.5Hz 低帧率设计为音频 tokenization 提供了优秀的参考基线；双流建模的全双工思路虽然短期内难以在级联架构中完整复现，但其"同时处理输入输出流"的理念可以指导 Turn Controller 的状态机设计。需要注意的是，Moshi 的纯 CPU 推理在理论上可行但极慢，7B 模型在消费级 GPU（L4 24GB）上才能达到 200ms 延迟，不适合纯 CPU 部署。

## 2.2 GPT-4o Realtime API：云端全双工基准

OpenAI 的 GPT-4o Realtime API 虽然闭源，但其架构设计通过 API 文档和开发者指南得到了充分披露，是理解工业级全双工系统的重要参考。该 API 支持 WebSocket 和 WebRTC 两种连接方式，音频格式为 PCM 16-bit 24kHz，内置服务端 VAD 和语义 VAD 两种模式。打断机制通过事件驱动架构实现：`input_audio_buffer.speech_started` 事件触发客户端立即停止音频播放，`response.cancel` 取消当前响应生成。定价方面，`gpt-realtime-2`（2026 年 5 月发布）的音频输入 token 为 $32/百万，音频输出 token 为 $64/百万。

GPT-4o Realtime API 对自研架构的核心启示在于其传输层设计。OpenAI 在 2024 年底将 Realtime API 从 WebSocket 迁移到 WebRTC，将网络缓冲延迟从 100–200ms 降至 20–50ms——这一决策验证了 WebRTC 作为语音 AI 传输层的必要性。此外，其 VAD 双模式设计（server_vad 的 silence_duration_ms 默认为 500ms，semantic_vad 的 eagerness 参数控制响应积极性）为 Turn Controller 的参数化设计提供了参考。

## 2.3 Thinker-Talker 架构三杰

Qwen2.5-Omni（阿里云，GitHub 约 4,100 Stars，Apache 2.0）是 Thinker-Talker 路线最成熟的实现。其核心创新 TMRoPE（Time-aligned Multimodal RoPE）同步视频和音频的时间戳，保持多模态时序一致性。Thinker 是多模态 LLM（处理文本/图像/音频/视频），Talker 是双轨自回归模型，将 Thinker 的 hidden representations 转换为流式音频 token。7B 版本需要 GPU，但 3B 版本通过 OpenVINO 可在 Intel CPU 上运行——这是目前唯一在纯 CPU 上可运行的端到端语音模型。

Freeze-Omni（VITA 团队，GitHub 约 500+ Stars）在训练策略上做出了关键创新：LLM（Qwen2-7B-Instruct）参数完全冻结，仅训练语音编码器、适配器和语音解码器。这避免了灾难性遗忘，完全保留了原始文本 LLM 的智能，训练成本极低。更值得关注的是其三状态预测机制——基于 LLM 最后一层 hidden state 预测 State 0（继续接收语音）、State 1（用户打断）或 State 2（话轮结束）——这为级联架构中的打断检测提供了直接可借鉴的方案。

LLaMA-Omni（中科院计算所）以极低的训练成本（4 GPU、3 天）实现了 226ms 的端到端延迟，基于 Llama-3.1-8B-Instruct 和 Whisper 语音编码器。其 LLaMA-Omni 2 版本（ACL 2025）进一步将模型规模扩展至 0.5B–14B 系列，集成自回归 TTS 语言模型和 Causal Flow Matching。这两个项目的训练效率证明了 Thinker-Talker 架构的低门槛特性。

## 2.4 其他值得关注的模型

GLM-4-Voice（智谱 AI，GitHub 约 3,000+ Stars）采用三组件架构（语音编码器 + GLM-4 LLM + 语音解码器），支持情感、语调、语速和方言控制，但需要 GPU 推理且 Decoder 模型不支持标准 transformers 初始化。SpeechGPT 2（复旦大学）实现了真正的端到端口语对话，采用超低比特率流式语音 Codec 和多 LM Head 架构，支持实时打断交互。Spirit LM（Meta FAIR）开创了文本-语音 token 交错训练的范式，其 EXPRESSIVE 版本额外包含音高和风格 token。Hertz-dev（Standard Intelligence，Apache 2.0）是纯语音到语音的全双工模型，约 8.5B 参数，需要 8–12GB VRAM。

在音频编解码器层面，Google 的 SoundStream（2021）奠定了"神经音频编解码器 + RVQ"的范式基础，其 3kbps 质量超过 Opus 12kbps 的结论已被后续工作广泛验证。Meta 的 EnCodec 提供了多码率流式方案。Mimi 在此基础上将帧率从 50Hz 降至 12.5Hz，是当前最适合 LLM 处理的编解码器设计。

## 2.5 对自研架构的关键启示

综合以上分析，S2S 路线对自研级联架构的可迁移设计思想可归纳为三点。第一，Inner Monologue 机制（Moshi）可以直接映射到级联架构中——LLM 在生成 TTS 输入文本的同时输出"思考"文本，后者用于日志、调试和 RAG 上下文注入。第二，Freeze-Omni 的三状态预测为 Turn Controller 提供了经过验证的状态机设计——State 0/1/2 的简单分类比复杂的连续概率输出更易于实现和调试。第三，Mimi 的 12.5Hz 低帧率设计为未来可能的音频原生演进提供了编解码器选型参考。然而，必须明确指出：当前所有 S2S 模型在纯 CPU 环境下均不可行或极慢，短期内不应作为自研系统的主架构方向。

下一章将转向级联流式路线，这是当前生产环境的默认选择，也是自研架构最可能的技术基础。


---

第二章分析了端到端 S2S 路线的设计思想与可迁移机制。本章回到生产环境的默认选择——级联流式架构（流式 ASR → LLM → TTS），系统梳理代表性开源框架、模块化生态和延迟优化策略。对于自研系统而言，本章的发现将直接决定架构基础是否稳固。

## 3.1 三大开源框架

Pipecat（pipecat-ai/pipecat，约 14,000 Stars，BSD-2-Clause 许可证）是目前模块化程度最高的语音 AI 管道框架。其核心设计是 Frame-based Pipeline——音频和文本作为连续的小型类型化对象流（Frame）在处理器之间传递，每个音频帧携带 20ms PCM 采样。这一 20ms 帧大小已成为行业标准，被 Vapi、LiveKit 等主流平台采用。Pipecat 集成了 68+ 服务，支持级联和原生 S2S 两种方式，其 Smart Turn v3 打断处理基于 Whisper Tiny（8M 参数）+ 线性分类器，从原始音频波形直接判断话轮完成——这一设计捕获了转录文本丢失的韵律信息（语调、语速、填充词），是级联架构中端点检测的重要创新。然而，Pipecat 设计为服务端/云端运行，在 Windows 本地部署体验较差，不适合作为纯本地系统的管道框架。

LiveKit Agents（livekit/agents，约 12,600 Stars，Apache 2.0）是 WebRTC-first 的实时 AI 代理框架。其架构围绕三个核心抽象构建：Agent（LLM 驱动的应用，包含指令和工具定义）、AgentSession（管理 STT → LLM → TTS 管道）和 AgentServer（协调 Job 调度）。LiveKit 的 SFU（Selective Forwarding Unit）架构是其核心竞争力——通过服务端媒体路由降低客户端 CPU 和带宽压力，WebRTC UDP 传输避免了 TCP 的队头阻塞问题。LiveKit 提供 Windows 预编译二进制，`livekit-server --dev` 可一键启动开发模式，但官方推荐生产环境使用 Docker/Linux。对于自研架构，LiveKit 的 AgentSession 抽象层和 WebRTC SFU 架构是最值得借鉴的设计模式。

FunASR（modelscope/FunASR，约 25,300 Stars，Apache 2.0）是阿里达摩院推出的工业级 ASR 工具包，在中文语音识别领域处于开源绝对领先地位。其子项目 SenseVoice 在 CPU 上达到 17 倍实时速度（处理 10 秒音频仅需约 70ms），中文 CER 比 Whisper.cpp 低约 3 倍。2026 年 7 月发布的 llama.cpp runtime v0.1.9 是一个重要里程碑——提供 Windows CPU/AVX2 预编译包，使 FunASR 可以在无 Python 运行时的情况下通过 GGUF 格式运行，与现有 llama.cpp 生态无缝集成。配套的 CosyVoice 是中文流式 TTS 的最佳开源方案，但需要 GPU 推理，CPU 上 10–50 倍慢于 GPU。

## 3.2 模块化组件生态

在级联架构中，各模块可以独立选型和替换，这既是其最大优势，也意味着需要仔细评估每个模块的候选方案。

ASR 模块方面，sherpa-onnx（k2-fsa/sherpa-onnx）是 Windows 纯 CPU 环境下的最佳选择。它提供预编译 Windows 二进制和 Python wheel，`pip install sherpa-onnx` 即可获得 ASR + VAD + TTS 全栈能力。内置的 SenseVoice-Small 模型在 CPU 上 RTF 仅 0.015（处理 10 秒音频约 150ms），模型小于 100MB，运行时内存低于 500MB。Whisper.cpp 的 small 模型在 CPU 上 RTF 约 1.0（刚好实时），中文效果不如 SenseVoice，但 tiny 模型（RTF 约 0.1）适合极低资源场景。Faster-Whisper 通过 CTranslate2 实现 4 倍 CPU 加速和 INT8 量化，内存减半。

VAD 模块方面，Silero VAD 已成为开源事实标准。其 1.6MB 神经网络模型在 CPU 上推理延迟低于 1ms，在 5% 误检率下的真阳性率（TPR）约 87.7%，比 WebRTC VAD（约 50% TPR）错误少 4 倍。sherpa-onnx 内置了 Silero VAD，与 ASR 无缝集成，单次调用即可完成 VAD + ASR。WebRTC VAD 虽然准确率较低，但极轻量（低于 1MB 内存），适合作为初筛层。

TTS 模块方面，piper TTS（rhasspy/piper）是 Windows 纯 CPU 环境下最实用的选择。它基于 ONNX Runtime 推理，提供 Windows 预编译二进制，现代桌面 CPU 上比实时快约 10 倍，单个语音模型仅数十 MB，运行时内存低于 200MB，支持中文（zh_CN-huayan-medium）。sherpa-onnx 内置的 VITS/Matcha-TTS 提供类似性能，与 sherpa-onnx 生态无缝集成。ChatTTS（39,800 Stars）虽然对话风格自然度优秀，但 300M 参数在 CPU 上推理极慢，且 AGPLv3 许可证限制商用。CosyVoice 的中文音色质量最佳，但设计为 GPU 推理，CPU 上不可行。Fish-Speech 的 Dual-AR 架构和 SGLang 流式推理集成值得关注，但 Windows 下推荐 WSL2 或 Docker。

## 3.3 商业平台的架构启示

Vapi、Bland AI 和 Retell AI 三家商业语音 AI 平台全部采用级联流式架构，其设计选择为自研架构提供了经过市场验证的参考。Vapi 的 Listen → Think → Speak 实时循环以 20ms 音频块流式处理，自定义服务器中间件模式（类似 webhook）实现了灵活的模型替换。Bland AI 自研了全部三个模型（TTS、推理、转录），并设计了名为 Conversational Pathways 的对话流控制语言，将 prompt 拆分为独立节点以防止幻觉。Retell AI 的流式管道每约 50ms 发出部分转录，端到端延迟约 600ms，支持流式知识库检索（RAG）。三家平台的端到端延迟目标均在 600–800ms 范围内，超过 700ms 用户会感到不自然——这一阈值是自研系统延迟优化的关键基准。

## 3.4 延迟优化的关键路径

Salesforce AI Research 在 2025 年的基准测试（arXiv:2603.05413）系统测量了级联架构各阶段的延迟贡献。全流式管道（每阶段在上阶段完成前开始输出）相比批处理可节省 300–600ms。流式 ASR 不等用户说完就开始转录，节省 100–200ms。流式 TTS 不等完整响应就开始合成，节省 200–400ms。WebRTC 相比 PSTN 电话网络节省 150–700ms。模型量化（INT8/INT4）和预热（保持模型在内存中）是纯 CPU 环境下最有效的优化手段。Sentence Buffer 是一个关键的编排原语——累积足够文本后再发送 TTS，在延迟和自然度之间取得平衡。

## 3.5 对自研架构的根基评估

基于以上分析，可以对自研架构的现有方案做出初步评估。用户当前的架构为"VLM + 流式 ASR + KWS + LLM + TTS"，其中 ASR 和 TTS 已部分走云端（MiniMax TTS）。从级联流式路线的视角来看，这一架构的**根基是稳固的**——级联流式架构在 2026 年仍是生产环境的默认选择，所有主流商业平台均采用此路线。现有方案不需要全盘推倒重来。

然而，存在三个需要重点加固的薄弱环节。第一，当前架构缺少系统级的 Turn Controller——VAD、ASR、LLM、TTS 之间的协调逻辑（特别是打断和话轮切换）需要从"各模块独立运行"升级为"统一状态机调度"。第二，传输层如果当前使用 WebSocket，应考虑迁移到 WebRTC 以获取 20–50ms 的缓冲延迟优势和内置 AEC/NS/AGC 音频处理。第三，ASR 如果当前走云端，可以考虑引入本地 sherpa-onnx + SenseVoice 作为离线备选或主方案，以降低网络依赖和延迟。后续章节将对这些加固方向给出具体建议。


---

前两章分别梳理了 S2S 和级联流式两条技术路线的项目与论文。无论选择哪条路线，全双工语音对话都依赖五个核心机制：Turn-Taking（话轮交接）、Barge-In（打断）、端点检测与静默判断、低延迟流式音频传输和回声消除。本章对每个机制进行技术拆解，明确哪些可以复用开源方案、哪些必须自研。

## 4.1 Turn-Taking：从硬切换到三层级联

话轮交接是全双工系统最核心的挑战——系统需要判断用户是否说完、何时应该开始说话。传统方法依赖声学 VAD 的静默超时（silence timeout），典型阈值为 500–800ms。这种方法实现简单但体验机械：无法区分"思考停顿"和"话轮结束"，对语速慢的用户容易误判，且完全无法处理 backchannel（"嗯"、"对"等反馈语）。

2026 年的生产级方案已收敛到三层级联架构。第一层是声学 VAD（快速响应层），Silero VAD 以 200ms 短触发窗口检测语音能量，检测到语音立即标记潜在打断，检测到静默则触发第二层。第二层是音频 Turn Detection 模型（韵律分析层），分析语调、语速和填充词，区分 backchannel、barge-in 和继续静默三种情况。Pipecat SmartTurn v3 是这一层的代表性实现——基于 Whisper Tiny（8M 参数）+ 线性分类器，输入原始音频波形而非转录文本，捕获了文本丢失的韵律信息。第三层是 LLM 语义完成判定（语义确认层），基于对话上下文判断语义完整性，输出特殊 token 或完成概率。

FireRedChat（arXiv:2509.06502）提供了这一架构的工业级验证数据。其流式个性化 VAD（pVAD）结合 ECAPA-TDNN 说话人嵌入和 GRU，T90 延迟为 170ms，误打断率仅 10.2%——相比之下 LiveKit 为 33.4%，Ten 为 78.1%。关键洞察在于：延迟略高但误打断率大幅降低的方案，用户体验反而更优。Moshi 则代表了另一条路径——通过双流建模从架构层面原生支持全双工，不显式建模 speaker turn，而是通过训练数据自然学习对话动态。

对于自研架构，Turn-Taking 的实现建议是：VAD 基础层复用 Silero VAD（成熟、准确、CPU 友好），Turn Detection 模型需要自研或微调（中文韵律特征——声调、语气词"吧/嘛/呢"——与英文不同），LLM 语义判定通过 Prompt 工程实现（在 system prompt 中加入 turn completion 判定逻辑），整体调度由自研 Turn Controller 状态机协调。

## 4.2 Barge-In：打断检测与状态恢复

Barge-in 的核心挑战不是"检测到打断"，而是"打断后怎么办"。2026 年的最佳实践采用三阶段混合策略。第一阶段是声学快速响应（低于 50ms）：VAD 检测到语音能量后立即暂停 TTS 播放。第二阶段是快速分类（50–200ms）：Turn Detection 模型判断是 barge-in、backchannel 还是噪声——若是噪声则恢复 TTS（误触发恢复），若是 barge-in 则确认打断。第三阶段是状态恢复（200–500ms）：截断对话历史到用户实际听到的位置，标记被中断的 agent 话语（`previous_agent_utterance_interrupted` flag），启动新的 STT → LLM → TTS 流水线。

LiveKit Agents 和 Pipecat 都提供了成熟的框架级打断实现。LiveKit 的 AgentSession 在 VAD 检测到用户语音后自动暂停 TTS 播放、截断对话历史、启动新的 STT pass，并支持通过 `session.interrupt()` 显式触发。Pipecat 的打断流程通过 SileroVADAnalyzer 持续检测、TurnAnalyzerUserTurnStopStrategy 判定、LLMContextAggregatorPair 管理上下文，被打断的 agent 话语存入 conversation state，LLM 根据 `previous_agent_utterance_interrupted` flag 决定重复、继续或重新开始。GPT-4o Realtime API 则通过事件驱动架构实现：`input_audio_buffer.speech_started` → `response.cancel` → `input_audio_buffer.clear` → `conversation.item.truncate`。

打断机制面临四个关键挑战。回声自触发——系统播放的 TTS 被麦克风拾取，误判为用户语音——需要 AEC 解决。Backchannel 误判——用户的"嗯"、"对"被误判为打断——需要 Turn Detection 模型分类。打断后状态恢复——对话上下文不完整——需要截断历史 + interrupted flag + LLM 感知。竞态条件——用户在系统即将结束时说话——需要状态机 + 原子操作 + turn_id 追踪。

对于自研架构，声学打断检测复用 Silero VAD，Turn 分类模型需要自研（中文 backchannel 特征不同），上下文恢复与对话管理深度耦合需要自研，TTS 中断复用服务端 TTS API 的 stop 能力（MiniMax 等已提供标准接口），状态机作为核心调度逻辑必须自研。

## 4.3 端点检测：声学 VAD 与语义级判定

端点检测决定了系统在用户说完后多快开始响应。Silero VAD 已成为开源事实标准——1.6MB 模型，CPU 推理低于 1ms，在 5% 误检率下 TPR 约 87.7%，全面替代了 WebRTC VAD（仅约 50% TPR）。Cobra VAD（Picovoice，商业）在同等条件下 TPR 达 98.9%，但需要付费授权。

语义级端点检测是更前沿的方向。Pipecat SmartTurn v3 开创了"从原始音频直接判断话轮完成"的路线——基于 Whisper Tiny（8M 参数）+ 分类头，分析最近 8 秒音频的韵律特征，输出 turn_complete 或 turn_incomplete。这一方法的关键优势在于捕获了转录文本丢失的信息。FireRedChat 的 EoT 检测器则走文本路线——基于 BERT 微调，83 万条训练样本，输入 ASR 转录文本，输出语义完成或未完成。LLM 特殊 token 方法（在 prompt 中注入判定逻辑，模型输出 `<TURN_COMPLETE>` token）是最易实现但延迟最高（100–500ms）的方案。

混合方案的三层时序设计为：VAD 层每 20–30ms 输出一次判定，SmartTurn 层在 VAD 检测到静默后触发（延迟约 50ms），LLM 层在 SmartTurn 判定 turn_complete 后触发（延迟约 200ms），总端点检测延迟约 250–300ms，相比纯 VAD 的 500–800ms 显著改善。

## 4.4 低延迟流式传输：WebRTC 的必然性

WebRTC 与 WebSocket 的核心差异在于传输层协议：WebRTC 使用 UDP/RTP（丢包跳过，20ms 音频帧丢失几乎不可感知），WebSocket 使用 TCP（阻塞重传，全链路暂停）。这一差异在语音体验中是质的区别——WebRTC 的缓冲延迟为 20–50ms，WebSocket 为 100–200ms。OpenAI 在 2024 年底将 Realtime API 从 WebSocket 迁移到 WebRTC，验证了这一技术判断。

2026 年的生产架构采用混合传输：客户端到服务端使用 WebRTC（UDP/RTP + Opus 编码，内置 AEC/NS/AGC），服务端到模型 API 使用 WebSocket（TCP，完全可控）。Opus 编码的算法延迟为 26.5ms（默认 20ms 帧 + 5ms lookahead），32kbps 语音质量接近透明，内置前向纠错（FEC）。服务端内部建议使用 PCM（零编码延迟）。20ms 音频帧大小是经过验证的最佳实践——Vapi、Pipecat、LiveKit 均采用此值。

对于自研架构，客户端传输复用 WebRTC（LiveKit SFU 或自建），服务端传输复用 WebSocket，音频编码复用 Opus（传输）+ PCM（内部），Jitter Buffer 需要自研自适应策略，流式调度作为核心编排逻辑必须自研。

## 4.5 回声消除：打断准确率的前提

AEC（声学回声消除）在全双工对话中的必要性常被低估。系统播放的 TTS 音频被自身麦克风拾取后，会被 VAD 误判为用户语音导致误打断，混入用户语音导致 ASR 准确率下降，极端情况导致啸叫。WebRTC AEC3 是浏览器端的标配方案——基于自适应滤波器 + 非线性处理，维护扬声器参考信号模型，从麦克风输入中减去预测回声，延迟低于 10ms。SpeexDSP 是服务端/嵌入式的轻量替代——基于 NLMS 自适应滤波器，延迟低于 5ms，但仅衰减而非消除回声。对于桌面陪伴型场景（用户通常使用耳机/耳麦），回声严重程度较低，但 AEC 仍应作为标准配置引入。

## 4.6 自研 vs 复用决策

综合五个机制的分析，形成以下决策矩阵。必须复用的模块包括：Silero VAD（成熟度最高）、WebRTC 传输栈（LiveKit SFU 或自建）、Opus 编码、WebRTC AEC3/NS/AGC（音频前端一站式方案）。建议自研的模块包括：Turn Controller 状态机（核心调度逻辑，需精确控制）、中文 Turn Detection 模型（中文韵律特征需定制训练数据）、LLM 语义判定 Prompt（与对话 LLM 深度耦合）、上下文恢复逻辑（与对话管理耦合）。这一决策矩阵将直接指导第五章的部署评估和第八章的架构建议。


---

第四章拆解了全双工的核心机制，明确了哪些模块复用开源、哪些自研。本章将这些分析落地到具体的部署环境——Windows 10/11、纯 CPU 推理、8–16GB RAM、目标推理框架 llama.cpp + sherpa-onnx——逐模块评估各开源项目的可运行性，并给出本地/云端两档可行性判断。

## 5.1 ASR 模块：sherpa-onnx 是唯一首选

在 Windows 纯 CPU 环境下，sherpa-onnx + SenseVoice-Small 是部署可行性最高的 ASR 方案。官方提供 Windows x64 预编译二进制和 Python wheel，`pip install sherpa-onnx` 即可完成安装，零额外依赖。SenseVoice-Small 模型在 CPU 上 RTF 仅 0.015——处理 10 秒音频约 150ms，比 Whisper-Small 快约 5 倍。模型大小低于 100MB（ONNX 格式），运行时内存低于 500MB，中文识别准确率优于 Whisper。FunASR 的 llama.cpp runtime v0.1.9（2026 年 7 月发布）提供了另一条可行路径——Windows CPU/AVX2 预编译包，GGUF 格式，与现有 llama.cpp 生态无缝集成，中文效果同样优秀。

Whisper.cpp 的 small 模型在 CPU 上 RTF 约 1.0（刚好实时），tiny 模型 RTF 约 0.1 但中文效果差，适合作为低资源备选。Faster-Whisper 通过 CTranslate2 实现 4 倍 CPU 加速，但中文效果仍不如 SenseVoice。云端备选方面，Deepgram Nova-3 延迟低于 300ms、价格 $0.0043/分钟，Azure Speech 延迟 1.5–2 秒、价格 $1.00/小时，阿里云 ASR 中文效果优秀但延迟 1–3 秒。

结论：ASR 模块本地首选 sherpa-onnx + SenseVoice-Small（完全可行），云端备选 Deepgram Nova-3（更低延迟但需网络）。

## 5.2 VAD 模块：Silero VAD 零成本集成

Silero VAD 在 Windows CPU 上完美运行。模型约 2MB（ONNX 格式），推理延迟低于 1ms（处理 30ms 音频块），运行时内存低于 50MB。sherpa-onnx 内置了 Silero VAD，如果已使用 sherpa-onnx ASR，VAD 零额外集成成本。WebRTC VAD 更轻量（低于 1MB 内存）但准确率仅约 Silero 的一半，适合作为初筛层或极低资源场景。云端备选不需要——本地 VAD 已完美满足需求。

## 5.3 LLM/VLM 模块：llama.cpp 是唯一可行路径

llama.cpp 是 Windows CPU 环境下 LLM 推理的事实标准。官方 GitHub Releases 提供 Windows 预编译二进制（CPU/AVX2/Vulkan/CUDA），无需手动编译。7B Q4_K_M 量化模型在 16GB RAM 下约 2–5 tok/s，3B Q4 模型可达 10–20 tok/s，1.5B Q4 模型可达 20+ tok/s。VLM 支持方面，llama.cpp 支持 LLaVA 1.5/1.6 和 Qwen2-VL（需 GGUF 格式），提供 `llama-qwen2vl-cli` 工具。Ollama 提供 Windows 原生应用一键安装和 OpenAI API 兼容接口，底层同样使用 llama.cpp，适合快速原型开发。vLLM 在 Windows CPU 上完全不可行——设计为 GPU 优先，CPU 推理 10–50 倍慢，且仅支持 Linux。

云端备选方面，OpenAI/Claude API 提供最强推理能力但需网络和持续成本。对于桌面陪伴型场景，建议采用混合策略：日常对话使用本地 llama.cpp 3B 模型（低延迟、隐私保护），复杂推理按需切换到云端 API。

## 5.4 TTS 模块：本地 piper TTS，云端 MiniMax

piper TTS 是 Windows 纯 CPU 环境下最实用的本地 TTS。提供 Windows 预编译二进制，`pip install piper-tts` 即可安装。现代桌面 CPU 上比实时快约 10 倍，单个语音模型 ONNX 文件数十 MB，运行时内存低于 200MB，支持中文（zh_CN-huayan-medium）。sherpa-onnx 内置的 VITS/Matcha-TTS 提供类似性能和更好的生态集成。ChatTTS（39,800 Stars）对话风格自然度优秀，但 300M 参数在 CPU 上推理极慢（生成 10 秒音频需数十秒），且 AGPLv3 许可证限制商用。CosyVoice 中文音色质量最佳，但设计为 GPU 推理，CPU 上 10–50 倍慢于 GPU，需要 16GB+ RAM，在纯 CPU 环境下不可行。

用户已在使用的 MiniMax Speech 2.6 是云端 TTS 的优秀选择——延迟低于 250ms，音色质量优秀，价格 $60–100/百万字符。建议保持云端 MiniMax 作为主 TTS，本地 piper TTS 或 sherpa-onnx TTS 作为离线备选。

## 5.5 全栈方案对比

sherpa-onnx 全栈（ASR + VAD + TTS）+ llama.cpp（LLM）是 Windows 纯 CPU 环境下部署可行性最高的组合。安装仅需两个 `pip install`（sherpa-onnx 和 piper-tts）加 llama.cpp 预编译二进制下载。总内存占用约 3–4GB（SenseVoice-Small + Silero VAD + piper TTS + llama.cpp 3B Q4），端到端延迟约 1–3 秒（不含 LLM 生成时间）。所有组件均提供 Windows 原生支持，无需 WSL2 或 Docker。

llama.cpp 全生态（whisper.cpp + llama.cpp + piper TTS）是备选方案，全部 C++ 原生性能最优，但组件间集成需手动串联，ASR 中文效果不如 SenseVoice。LiveKit + 本地组件适合开发测试（提供 Windows 二进制），但生产环境推荐 Linux。Pipecat + 本地组件不推荐 Windows 部署——设计为服务端/云端运行，Python 依赖在 Windows 下兼容性差。

## 5.6 本地/云端两档可行性总结

按模块分别给出本地和云端两档可行性评估。ASR 模块：本地 sherpa-onnx + SenseVoice 完全可行（RTF 0.015，低于 500MB 内存），云端 Deepgram Nova-3 延迟更低但需网络。VAD 模块：本地 Silero VAD 完全可行且无需云端。LLM 模块：本地 llama.cpp 3B Q4 可行（10–20 tok/s，约 2GB 内存），云端 OpenAI/Claude API 推理质量更高但有网络依赖和成本。TTS 模块：本地 piper TTS 可行（RTF 低于 0.1，低于 200MB 内存）但音色一般，云端 MiniMax Speech 2.6 音色优秀且用户已在使用。传输层：本地 WebRTC（LiveKit SFU）可行，云端同样使用 WebRTC 架构。

这一评估表明，用户当前"ASR 可走云端、TTS 已走云端 MiniMax"的混合部署策略是合理的。建议的优化方向是：将 ASR 增加本地 sherpa-onnx 作为主方案或离线备选（降低延迟和网络依赖），保持 TTS 云端 MiniMax 为主（音色质量优先），LLM 采用本地 3B 模型 + 云端 API 的混合策略。


---

第五章从部署可行性角度评估了各模块的工程代价。本章将这些技术方案映射到桌面陪伴型语音智能体的两种交互模式——直播模式（Always-On Listening）和贾维斯模式（Wake-Word Triggered）——分析两种模式的技术需求差异、模块共用策略和架构演进路径。

## 6.1 两种模式的本质差异

直播模式和贾维斯模式代表了语音交互光谱的两端。直播模式的核心体验是"朋友式自然对话"——系统持续聆听，无需唤醒词即可主动插话，用户可以随时用语音打断 AI 输出。贾维斯模式的核心体验是"指令式高效执行"——唤醒词显式激活，用户发出指令后系统执行任务，任务完成后回归静默。

这两种模式在技术需求上存在根本性差异。延迟要求方面，直播模式需要低于 200ms 的打断响应和低于 320ms 的端到端响应（GPT-4o Advanced Voice Mode 实测平均 320ms），贾维斯模式对唤醒后响应延迟的要求相对宽松（低于 1 秒）。核心技术方面，直播模式依赖全双工对话、个性化 VAD（pVAD）和 Inner Monologue，贾维斯模式依赖 KWS 唤醒词检测和级联 ASR → NLU → TTS 流水线。隐私风险方面，直播模式因持续监听而显著更高，需要端侧处理作为必要路径。技术成熟度方面，贾维斯模式已商业化多年（Alexa、Siri），直播模式在 2024–2025 年才取得关键突破（Moshi、GPT-4o Advanced Voice）。

## 6.2 打断策略的场景化差异

两种模式对打断策略的要求截然不同。直播模式需要声学级打断——检测到语音能量即暂停 TTS，宁可误打断不可漏打断，延迟预算约 50–80ms。贾维斯模式可以采用语义级打断——确认用户意图后再响应，避免误触发，延迟预算约 300–500ms。

这一差异直接影响了 Turn Controller 的设计。直播模式下的 Turn Controller 需要更激进的三层级联（VAD 200ms 短触发 → Turn Detection 快速分类 → LLM 语义确认），而贾维斯模式下可以更保守（KWS 唤醒 → ASR 完整识别 → NLU 意图理解 → 响应）。两种模式的打断恢复机制也不同：直播模式需要快速恢复对话流并可能回溯被中断内容，贾维斯模式则重新开始指令理解。

## 6.3 长上下文多轮对话与记忆管理

桌面陪伴型场景对长上下文多轮对话有天然需求——用户可能与 AI 持续对话数小时，跨 Session 保持对用户偏好和历史事件的理解。记忆系统需要分层设计：短期记忆（STM）维护当前对话窗口内的最近几轮内容，Session 记忆维护单次会话的任务上下文，长期记忆（LTM）跨 Session 持久化用户偏好和历史决策，情景记忆按事件存储（"上周我们讨论过 X"），语义记忆维护领域知识和事实关系。

Mem0 架构是当前记忆管理的 SOTA 方案——自动提取、存储和检索记忆，支持记忆去重和更新。上下文窗口管理方面，Summarization（对历史对话进行摘要压缩）和 Sliding Window + Retrieval（滑动窗口保持最近对话 + 向量检索召回相关历史）是两种互补策略。

VLM 视觉上下文注入是桌面陪伴型场景的差异化能力。用户语音和屏幕截图/摄像头帧通过多模态融合层进入 LLM，使 AI 能够理解用户当前屏幕内容并据此发起话题或执行操作。Google Project Astra 的连续视频帧编码方案——持续编码视频帧，与语音输入组合成时间线事件序列，缓存历史帧数据支持回溯查询——为这一能力提供了参考架构。Apple Intelligence 的端侧语义索引方案则提供了隐私友好的替代路径。

## 6.4 VLM 主动看/说/决策

VLM 在桌面陪伴型场景中的角色可以从被动到主动分为四个层级。Level 1 是被动视觉问答（"这个图表是什么意思？"→ VLM 分析截图 → 文本回复）。Level 2 是上下文感知交互（检测到用户在写代码 → 主动提供建议）。Level 3 是主动环境感知（持续屏幕监控 → 检测到异常/机会 → 主动发起语音提示）。Level 4 是全自主桌面代理（理解任务目标 → 规划步骤 → 操作桌面 → 语音汇报进度）。

主动发起话题的能力是桌面陪伴型智能体的核心差异化价值。触发条件包括：检测到用户长时间工作（"你已经工作 2 小时了，要休息一下吗？"）、检测到错误操作（"这个配置可能有问题"）、日历事件提醒（"你的会议 15 分钟后开始"）、情绪感知（检测到用户沮丧 → 调整语气和策略）。Hume AI 的 EVI（Empathic Voice Interface）在情感感知方面提供了参考——其 eLLM 处理用户语调，生成情感适配的语音，知道何时说话、如何生成更有同理心的语言。

## 6.5 共享核心 + 差异化前端的架构策略

两种模式不需要完全不同的技术栈，而应采用"共享核心 + 差异化前端"的架构策略。共享核心层包括多模态 LLM 引擎（语音 + 文本 + 视觉统一处理）、记忆管理（统一的 STM/LTM/Episodic 存储层）和对话状态管理（统一的 Session 管理和上下文窗口）。差异化前端方面，直播模式使用低功耗 VAD/pVAD + 全双工音频流 + 声学级打断，贾维斯模式使用 KWS 引擎 + 唤醒词检测 + 级联 ASR + 语义级打断。

模块共用分析表明：多模态 LLM、记忆管理、对话状态、TTS 引擎和 VLM 视觉能力可以完全共用；VAD/KWS、打断策略和音频前端需要独立实现；隐私管理部分共用但直播模式需要更严格的端侧处理策略。

## 6.6 分阶段演进路线

建议采用四阶段演进路线，从贾维斯模式 MVP 逐步过渡到全自主桌面代理。Phase 1（0–6 个月）建立基础语音助手能力：KWS 唤醒词检测（端侧，准确率高于 99%）、级联架构 ASR → LLM → TTS、基础多轮对话 + Session 记忆、屏幕截图理解（被动触发）。Phase 2（6–12 个月）验证全双工对话可行性：引入 Mimi 类神经编解码器（参考 Moshi）、实现 pVAD 个性化语音检测（参考 FireRedChat）、全双工音频流 + 声学级打断、Inner Monologue 机制。Phase 3（12–18 个月）实现双模式智能融合：自适应模式切换（专注工作 → 贾维斯模式，休闲陪伴 → 直播模式，会议通话 → 静默）、统一记忆系统跨模式共享上下文、VLM 主动感知。Phase 4（18–24 个月）迈向全自主桌面代理：桌面操控能力（参考 Claude Computer Use）、多步骤任务规划与执行、跨应用工作流自动化、情感感知与主动关怀（参考 Hume EVI）。

这一路线图的核心原则是：先用贾维斯模式验证产品市场契合度，再逐步引入直播能力，避免过早优化全双工架构。技术债务方面，当前级联架构在 Phase 1–2 完全够用，Phase 3 开始需要评估向 Thinker-Talker 架构迁移的必要性。


---

前六章从技术路线、核心机制、部署可行性和场景适配四个维度进行了系统分析。本章将所有发现汇总为可交叉验证的对比矩阵，并按可借鉴性对开源项目进行排序，为自研架构的模块选型提供直接参考。

## 7.1 技术路线对比矩阵

三条技术路线在八个关键维度上的对比如下。

| 维度 | 级联流式 | Thinker-Talker | 音频原生 S2S |
|------|----------|----------------|--------------|
| **端到端延迟** | 500–800ms（优化后 300ms） | 200–400ms | 160–350ms |
| **LLM 推理能力** | 完整保留（文本 LLM） | 大部分保留（冻结或微调） | 弱于同规模文本 LLM |
| **Function Calling** | 成熟支持 | 有限支持 | 基本不支持 |
| **RAG/上下文注入** | 天然支持（文本中间层） | 支持（Thinker 文本输出） | 困难（无文本中间层） |
| **模块化程度** | 极高（每阶段独立替换） | 中等（Thinker/Talker 解耦） | 低（端到端黑盒） |
| **纯 CPU 部署** | 完全可行（sherpa-onnx + llama.cpp） | 3B 版本可行（Qwen2.5-Omni + OpenVINO） | 不可行（所有模型需 GPU） |
| **训练成本** | 零（使用预训练模型） | 低–中（LLaMA-Omni: 4 GPU, 3 天） | 高–极高 |
| **生产成熟度** | 成熟（商业平台全部采用） | 发展中 | 前沿研究/API 服务 |

从矩阵中可以清晰看出：级联流式架构在模块化、可部署性和生产成熟度上具有压倒性优势，其核心劣势（延迟）正在通过全流式优化快速缩小。Thinker-Talker 架构在延迟和推理能力之间取得了最佳平衡，是向端到端演进的最优中间态。音频原生 S2S 在延迟和自然度上具有理论优势，但在可部署性和工程成熟度上存在明显短板。

## 7.2 核心机制方案对比

Turn-Taking、Barge-In 和端点检测三个核心机制的不同实现方案对比如下。

| 机制 | 方案 | 延迟 | 准确率 | 实现复杂度 | 推荐度 |
|------|------|:----:|:------:|:----------:|:------:|
| **Turn-Taking** | 纯 VAD 硬切换 | 500–800ms | 误切换率 ~30% | 极低 | 不推荐 |
| | 三层级联（VAD + Turn Model + LLM） | 150–400ms | 误切换率 ~3–5% | 高 | 推荐 |
| | Moshi 双流全双工 | 架构原生 | 训练习得 | 极高 | 远期参考 |
| **Barge-In** | 纯声学打断 | <150ms | 误打断率高 | 低 | 基础方案 |
| | 声学 + Turn 分类 + 上下文恢复 | <200ms | 误打断率 <5% | 高 | 推荐 |
| **端点检测** | WebRTC VAD | <1ms | TPR ~50% | 极低 | 不推荐 |
| | Silero VAD | <1ms | TPR ~87.7% | 低 | 推荐 |
| | Silero VAD + SmartTurn + LLM EOS | ~250ms | 最高 | 高 | 最优方案 |

## 7.3 传输层方案对比

| 维度 | WebSocket | WebRTC |
|------|-----------|--------|
| **传输协议** | TCP（可靠有序） | UDP/RTP（低延迟） |
| **缓冲延迟** | 100–200ms | 20–50ms |
| **丢包行为** | 阻塞重传 | 丢包跳过（20ms 帧几乎无感） |
| **内置音频处理** | 无 | AEC/NS/AGC/Opus |
| **NAT 穿透** | 天然支持 | 需 ICE/STUN/TURN |
| **实现复杂度** | 低 | 高 |
| **推荐场景** | 服务端 ↔ 模型 API | 客户端 ↔ 服务端 |

## 7.4 开源项目按可借鉴性排序

以下排序综合考虑了四个维度：对自研架构的直接可借鉴性（权重 40%）、Windows 纯 CPU 部署可行性（权重 30%）、代码质量与社区活跃度（权重 20%）、许可证友好度（权重 10%）。

### Tier 1：核心依赖（必须集成）

**1. sherpa-onnx（k2-fsa/sherpa-onnx）** — 可借鉴性评分 9.5/10。这是 Windows 纯 CPU 环境下 ASR + VAD + TTS 全栈的最佳选择。预编译 Windows 二进制，`pip install` 即可使用，SenseVoice-Small 中文 ASR 在 CPU 上 RTF 0.015，内置 Silero VAD，支持 VITS/Matcha-TTS。Apache 2.0 许可证。对自研架构的可借鉴点：跨平台 C++ 推理引擎设计、ONNX 模型部署最佳实践、ASR/VAD/TTS 模块完全解耦可独立复用。

**2. llama.cpp（ggml-org/llama.cpp）** — 可借鉴性评分 9.5/10。Windows CPU LLM/VLM 推理的事实标准。预编译 Windows 二进制，支持 Qwen2-VL 等多模态模型，3B Q4 模型可达 10–20 tok/s。MIT 许可证。对自研架构的可借鉴点：量化策略（Q4_K_M 平衡速度/质量）、VLM 推理集成、与 sherpa-onnx 生态互补。

**3. Silero VAD（snakers4/silero-vad）** — 可借鉴性评分 9.0/10。开源 VAD 的事实标准，1.6MB 模型，CPU 推理低于 1ms，TPR 87.7%。sherpa-onnx 已内置。MIT 许可证。对自研架构的可借鉴点：直接复用，零自研成本。

### Tier 2：架构参考（设计思想借鉴）

**4. Moshi（kyutai-labs/moshi）** — 可借鉴性评分 8.5/10。全双工开源标杆，Apache 2.0 许可证。虽然纯 CPU 不可行，但其 Inner Monologue 机制、Mimi 编解码器（12.5Hz/1.1kbps）和双流全双工建模为自研架构提供了最重要的设计参考。建议深入阅读其论文（arXiv:2410.00037）和代码，提取 Inner Monologue 和 turn-taking 的设计思想。

**5. LiveKit Agents（livekit/agents）** — 可借鉴性评分 8.0/10。WebRTC SFU 架构和 AgentSession 抽象层是最值得借鉴的设计模式。Apache 2.0 许可证。提供 Windows 预编译二进制（开发模式）。对自研架构的可借鉴点：AgentSession 生命周期管理、WebRTC SFU 传输架构、生产级 barge-in 处理。

**6. Pipecat（pipecat-ai/pipecat）** — 可借鉴性评分 7.5/10。Frame-based Pipeline 设计（20ms 音频帧）和 SmartTurn v3 打断处理是最值得借鉴的机制设计。BSD-2-Clause 许可证。不适合 Windows 本地部署，但设计思想可以直接迁移。对自研架构的可借鉴点：20ms 帧大小标准、SmartTurn 的音频级 turn detection 思路。

**7. Freeze-Omni（VITA-MLLM/Freeze-Omni）** — 可借鉴性评分 7.5/10。三状态预测机制（State 0/1/2）为 Turn Controller 状态机提供了经过验证的设计模板。LLM 冻结策略对训练资源有限的场景极具参考价值。对自研架构的可借鉴点：三状态打断预测、LLM 冻结训练策略。

### Tier 3：模块备选（特定场景使用）

**8. FunASR（modelscope/FunASR）** — 可借鉴性评分 7.0/10。中文 ASR 最强开源方案，llama.cpp runtime 使 Windows CPU 部署变得可行。Apache 2.0 许可证。对自研架构的可借鉴点：Paraformer 流式 ASR 架构、SenseVoice + CosyVoice 中文黄金组合。

**9. piper TTS（rhasspy/piper）** — 可借鉴性评分 7.0/10。Windows 纯 CPU 环境下最实用的本地 TTS。MIT 许可证。对自研架构的可借鉴点：直接复用为离线 TTS 备选。

**10. Qwen2.5-Omni（QwenLM/Qwen2.5-Omni）** — 可借鉴性评分 6.5/10。Thinker-Talker 最成熟实现，3B 版本通过 OpenVINO 可 CPU 推理。Apache 2.0 许可证。对自研架构的可借鉴点：TMRoPE 时序对齐、Thinker-Talker 分离架构参考。

### Tier 4：远期关注（当前不可部署但方向重要）

**11. SpeechGPT 2（OpenMOSS/SpeechGPT-2.0-preview）** — 端到端全双工，实时打断交互，但需要 GPU。

**12. Hertz-dev（standard-intelligence/hertz-dev）** — 纯语音 S2S 全双工，Apache 2.0，但需要 8–12GB VRAM。

**13. ChatTTS（2noise/ChatTTS）** — 对话风格 TTS 自然度最佳，但 CPU 推理极慢且 AGPLv3 限制商用。

**14. CosyVoice（QwenAudio/CosyVoice）** — 中文 TTS 音色质量最佳，但设计为 GPU 推理，CPU 不可行。

## 7.5 推荐模块组合

基于以上排序，针对自研架构推荐以下模块组合。ASR 首选 sherpa-onnx + SenseVoice-Small（本地），备选 Deepgram Nova-3（云端）。VAD 首选 sherpa-onnx 内置 Silero VAD（本地），无需云端。LLM 首选 llama.cpp Qwen2.5 3B Q4（本地），备选 OpenAI/Claude API（云端）。TTS 首选 MiniMax Speech 2.6（云端，用户已在使用），备选 piper TTS（本地离线）。传输层首选 WebRTC（LiveKit SFU 或自建）。Turn Controller 自研（参考 Freeze-Omni 三状态 + Pipecat SmartTurn 思路）。记忆管理自研（参考 Mem0 架构）。

这一组合的核心逻辑是：ASR 和 VAD 本地化以降低延迟和网络依赖，LLM 混合部署以平衡推理质量和成本，TTS 保持云端以维持音色质量，传输层迁移到 WebRTC 以获取最低延迟，Turn Controller 和记忆管理作为核心差异化能力自研。


---

前七章从技术路线、核心机制、部署可行性和场景适配四个维度完成了全双工语音对话技术的全景调研。本章综合所有发现，首先对用户提出的核心问题——"现有自研架构是否根基不稳、是否需要全盘推倒重来"——给出明确回答，然后给出分阶段实施路线图和具体行动建议。

## 8.1 根基评估：稳固但需加固

用户当前的架构为"VLM 主动看/说/决策 + 流式 ASR + 唤醒词 KWS + 大模型 + TTS，本地部署（llama.cpp + sherpa-onnx）"。基于本次调研的全部发现，我们的核心判断是：**现有架构的根基是稳固的，不需要全盘推倒重来**。

这一判断基于以下事实。第一，级联流式架构（流式 ASR → LLM → TTS）在 2026 年仍是生产环境的默认选择——LiveKit Agents、Pipecat、Vapi、Bland AI、Retell AI 等所有主流框架和商业平台均采用此路线。Salesforce AI Research 在 2025 年的企业级基准测试（arXiv:2603.05413）系统验证了级联架构的生产可行性。第二，llama.cpp + sherpa-onnx 的组合是 Windows 纯 CPU 环境下部署可行性最高的技术栈——所有组件均提供 Windows 预编译二进制，总内存占用可控制在 4GB 以内，端到端延迟可控制在 1–3 秒。第三，端到端 S2S 路线虽然在延迟和自然度上具有理论优势，但在 2026 年仍处于前沿研究阶段，所有模型在纯 CPU 环境下均不可行或极慢，短期内不应作为自研系统的主架构方向。

然而，存在三个需要重点加固的薄弱环节，这些环节如果不加固，将构成系统体验的天花板。

**薄弱环节一：缺少系统级 Turn Controller。** 当前架构中 VAD、ASR、LLM、TTS 各模块独立运行，缺少统一的协调逻辑来处理打断、话轮切换和上下文恢复。这导致系统在用户打断时可能出现"继续说被打断的话"或"丢失对话上下文"等问题。加固方案：自研 Turn Controller 状态机，参考 Freeze-Omni 的三状态预测（State 0 继续接收 / State 1 用户打断 / State 2 话轮结束）和 Pipecat SmartTurn 的三层级联（VAD → Turn Detection → LLM 语义判定），将 Silero VAD 作为声学快速响应层，自研中文 Turn Detection 模型作为韵律分析层，LLM Prompt 工程作为语义确认层。

**薄弱环节二：传输层可能使用 WebSocket 而非 WebRTC。** 如果当前传输层使用 WebSocket，网络缓冲延迟为 100–200ms；迁移到 WebRTC 可降至 20–50ms，同时获得内置的 AEC/NS/AGC 音频处理。OpenAI 在 2024 年底将 Realtime API 从 WebSocket 迁移到 WebRTC，验证了这一技术判断。加固方案：引入 LiveKit SFU 或自建 WebRTC 传输层，客户端使用 WebRTC UDP/RTP + Opus 编码，服务端内部使用 WebSocket 或本地管道。

**薄弱环节三：ASR 如果完全依赖云端，存在网络依赖和延迟不可控风险。** 加固方案：引入本地 sherpa-onnx + SenseVoice-Small 作为主方案或离线备选，RTF 0.015，模型低于 100MB，中文识别准确率优于 Whisper。

这三个加固方向都不涉及架构全盘推倒——它们是在现有级联流式架构框架内的增量优化，每个都可以独立实施和验证。

## 8.2 分阶段实施路线图

基于第六章的场景分析和第七章的模块排序，建议按以下四阶段推进。

**Phase 1：基础加固（0–3 个月）。** 目标是将现有架构升级到生产级基础。具体行动包括：引入 sherpa-onnx + SenseVoice-Small 作为本地 ASR（与现有云端 ASR 并行运行，逐步切换）；将传输层迁移到 WebRTC（引入 LiveKit SFU 或自建）；实现基础 Turn Controller（Silero VAD + 简单静默超时 + TTS 中断）；保持 TTS 云端 MiniMax、LLM 本地 llama.cpp 3B Q4 不变。此阶段完成后，系统应具备稳定的半双工对话能力，端到端延迟目标低于 2 秒。

**Phase 2：全双工增强（3–9 个月）。** 目标是在 Phase 1 基础上实现自然的全双工对话体验。具体行动包括：自研中文 Turn Detection 模型（参考 Pipecat SmartTurn 架构——Whisper Tiny 基座 + 分类头，使用中文对话数据微调）；实现三层级联 Turn Controller（VAD → Turn Detection → LLM 语义判定）；实现完整的 Barge-In 流程（声学打断 → Turn 分类确认 → 上下文恢复）；引入 AEC（WebRTC AEC3）确保打断准确率。此阶段完成后，系统应支持自然的打断和话轮切换，误打断率目标低于 10%。

**Phase 3：双模式融合（9–15 个月）。** 目标是在贾维斯模式基础上引入直播模式实验。具体行动包括：实现 pVAD 个性化语音检测（参考 FireRedChat，抑制非目标说话人）；引入 Inner Monologue 机制（LLM 在生成 TTS 文本的同时输出"思考"文本）；实现自适应模式切换（专注工作 → 贾维斯模式，休闲陪伴 → 直播模式）；建立统一记忆系统（STM + LTM + Episodic，参考 Mem0 架构）；增强 VLM 主动感知能力（屏幕理解 → 主动发起话题）。此阶段完成后，系统应支持两种模式的智能切换。

**Phase 4：全自主桌面代理（15–24 个月）。** 目标是从"陪伴"升级到"协作"。具体行动包括：桌面操控能力（参考 Claude Computer Use）；多步骤任务规划与执行；跨应用工作流自动化；情感感知与主动关怀（参考 Hume EVI）；评估向 Thinker-Talker 架构迁移的必要性。此阶段的具体技术方案需要在 Phase 3 完成后根据当时的技术成熟度重新评估。

## 8.3 关键风险与缓解措施

**风险一：纯 CPU 推理延迟可能无法满足直播模式的低于 200ms 打断要求。** llama.cpp 3B Q4 在 CPU 上的首 token 延迟为 500–2000ms，远超直播模式的要求。缓解措施：Phase 2 期间以贾维斯模式为主（延迟要求低于 1 秒），直播模式实验阶段评估是否需要引入 GPU 或云端 LLM API 补充。

**风险二：中文 Turn Detection 模型训练数据稀缺。** 现有 SmartTurn 和 FireRedChat 的训练数据以英文为主，中文对话的韵律特征（声调、语气词）需要专门的训练数据。缓解措施：通过自采集 + 合成数据构建中文训练集，初期可以使用规则 + LLM Prompt 作为过渡方案。

**风险三：WebRTC 引入的工程复杂度。** WebRTC 的实现复杂度显著高于 WebSocket，需要 ICE/STUN/TURN 基础设施。缓解措施：使用 LiveKit SFU 托管服务降低自建复杂度，Phase 1 期间可以保持 WebSocket + 本地音频处理作为过渡。

**风险四：打断后状态恢复的一致性问题。** 快速连续打断可能导致对话上下文混乱和 LLM 幻觉。缓解措施：实现严格的 turn_id 追踪和原子状态转换，设置打断冷却期（如 500ms 内不允许连续打断），在 LLM prompt 中显式注入 interrupted flag。

## 8.4 可交叉验证的结论清单

以下关键结论均附具体来源，便于对照本地实现逐条核验。

1. **级联流式架构是 2026 年生产默认选择**：LiveKit Agents（12.6K Stars）、Pipecat（14K Stars）、Vapi、Bland AI、Retell AI 全部采用此架构。（来源：各项目 GitHub 仓库和官方文档）

2. **Silero VAD 是开源 VAD 事实标准**：TPR 87.7%（5% FPR），1.6MB 模型，CPU 推理低于 1ms。（来源：Picovoice VAD 对比评测 2026）

3. **20ms 音频帧是行业标准**：Vapi、Pipecat、LiveKit 均采用此值。（来源：Vapi 工程博客、Pipecat 文档）

4. **WebRTC 缓冲延迟 20–50ms vs WebSocket 100–200ms**：OpenAI 在 2024 年底将 Realtime API 迁移到 WebRTC。（来源：OpenAI Realtime API 文档、LiveKit WebRTC vs WebSocket 对比）

5. **SenseVoice-Small CPU RTF 0.015**：处理 10 秒音频约 150ms，比 Whisper-Small 快约 5 倍。（来源：whispernotes.app 基准测试）

6. **llama.cpp 3B Q4 在 CPU 上 10–20 tok/s**：7B Q4 约 2–5 tok/s。（来源：llama.cpp 社区基准测试）

7. **piper TTS 在桌面 CPU 上比实时快约 10 倍**：单个模型数十 MB，运行时内存低于 200MB。（来源：piper TTS GitHub 文档）

8. **FireRedChat pVAD 误打断率 10.2%**：vs LiveKit 33.4%、Ten 78.1%，T90 延迟 170ms。（来源：arXiv:2509.06502）

9. **Moshi 端到端延迟约 200ms**：Mimi codec 12.5Hz/1.1kbps，Helium 7B，Apache 2.0 开源。（来源：arXiv:2410.00037）

10. **Freeze-Omni 三状态预测**：State 0 继续接收 / State 1 用户打断 / State 2 话轮结束，LLM 完全冻结。（来源：arXiv:2411.00774）

11. **GPT-4o Advanced Voice Mode 平均延迟 320ms**：最快 232ms，接近人类 210ms 反应时间。（来源：OpenAI 官方发布、DataCamp GPT-4o 指南）

12. **Salesforce 基准测试 P50 TTFA 947ms**：Deepgram + vLLM + ElevenLabs 组合，最佳 729ms。（来源：arXiv:2603.05413）

## 8.5 结语

全双工语音对话是 2024–2026 年 AI 领域最激动人心的技术方向之一。本次调研的核心发现可以浓缩为一句话：**级联流式架构的根基稳固，全盘推倒重来既不必要也不明智；真正的战场不在架构选型，而在 Turn Controller 的工程深度和中文 Turn Detection 的模型质量。** Moshi 的 Inner Monologue、Freeze-Omni 的三状态预测、Pipecat 的 SmartTurn、FireRedChat 的 pVAD——这些来自不同路线的设计思想，都可以在级联流式架构的框架内被吸收和整合。建议团队将资源聚焦于 Phase 1–2 的三个加固方向（Turn Controller、WebRTC 传输层、本地 ASR），在稳固的根基上逐步构建差异化的全双工体验。
