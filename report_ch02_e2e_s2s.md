# 第二章 端到端 S2S 技术路线

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
