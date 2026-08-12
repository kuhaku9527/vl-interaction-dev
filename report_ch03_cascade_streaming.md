# 第三章 级联流式技术路线

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
