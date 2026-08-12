# 第四章 实时双工核心机制深度拆解

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
