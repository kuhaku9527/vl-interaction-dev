# Chapter 2: VAD 级联架构对照 — 三层边界的重新定义

草稿采用 Silero VAD → Smart Turn v3.2 → Decision Token 三层级联架构，这一设计方向与 2026 年业界主流高度一致。Coval 的数据显示级联架构占据超过 85% 的生产部署份额，OpenAI（server_vad + semantic_vad）和 LiveKit（Silero VAD + Turn Detector）均采用类似的多层设计。然而，草稿在各层职责边界、Layer 2 的输入模态和级联失败回退策略上存在需要修正的关键问题。

## 2.1 Layer 1: Silero VAD 的定位修正

Silero VAD 作为 Layer 1 的选择是正确的。截至 2026 年 8 月，Silero VAD 已发布至 v6.2.1，在 5% FPR 下 TPR 为 87.7%，推理延迟低于 1ms（CPU），MIT 开源协议，是业界最广泛采用的轻量级 VAD 方案。LiveKit Agents 默认使用 Silero VAD 作为底层语音检测，Pipecat 生态也将其作为标准 VAD 组件。

但草稿中 Layer 1 的职责边界需要修正。当前设计中，Silero VAD 的 `min_silence_duration_ms` 参数（默认 100ms）实际上已经做了初步的端点检测——判断用户是否停止说话。这与 Layer 2（Smart Turn v3.2）的 turn 边界判断职责重叠。

修正方案是将 Layer 1 的职责严格限定为**语音活动检测**（speech/silence 二分类），不做端点决策。具体而言，将 `min_silence_duration_ms` 设为较小值（100ms），仅用于检测短暂的语音暂停，而将"用户是否说完"的判断完全交给 Layer 2。这消除了两层之间的职责重叠，也让每层的延迟预算更加清晰。

参数调优方面，基于 Picovoice 2026 年的 VAD 基准测试和 Silero VAD GitHub 社区讨论，推荐实时对话场景使用 `threshold=0.4`、`min_speech_duration_ms=100`、`min_silence_duration_ms=100`。这一配置在灵敏度和误触发之间取得平衡，同时将端点决策延迟最小化。

Silero VAD 的已知局限也需要在架构中考虑。在突发噪声场景下 recall 仅 53.9%，远场（超过 3 米）性能显著退化，且不支持重叠语音检测。这些场景需要依赖 Layer 2 的语义判断来弥补，而非期望 Layer 1 解决所有问题。

## 2.2 Layer 2: Smart Turn v3.2 的输入模态问题

这是草稿三层架构中最需要重新审视的环节。Smart Turn v3.2 的定位是"语义级 turn 边界判断"，但如果其输入是文本（依赖 ASR 转录完成），则存在严重的延迟瓶颈——需要等待 100–500ms 的 ASR 转录延迟后才能开始判断。

2025–2026 年的核心趋势是从"等待转录→文本判断"转向"音频原生→语义+声学融合"。LiveKit Turn Detector v1 是这一趋势的代表：它直接从音频工作，语义分支通过音频编码器→适配器→微调 LLM（基于 SmolLM v2）理解语义，声学分支通过独立编码器→循环层捕捉语调、音高和节奏，两个分支融合后做 end-of-turn 预测。这消除了等待转录的延迟，同时融合了纯文本方案无法获取的韵律信息。

OpenAI 的 semantic_vad 虽然内部仍依赖转录模型（parakeet-cpp-realtime_eou_120m-v1），但其设计理念也是将转录和端点检测耦合在同一个流式循环中，而非串行等待。

如果 Smart Turn v3.2 确实是文本输入方案，建议评估以下替代路径：首选是 LiveKit Turn Detector v1-mini（开源，Apache 2.0，135M 参数，支持 14 种语言，CPU 可推理）；次选是 VAP（Voice Activity Projection，自监督 Transformer，预测未来 2 秒语音活动，学术验证充分）；如果必须保留文本方案，则至少需要流式 ASR 提供 partial transcript，并实现增量决策。

## 2.3 Layer 3: Decision Token 的触发时机

Decision Token 作为第三层的定位是合理的，但其触发时机需要明确。当前草稿中，Decision Token 是在 Layer 2 判断 turn 结束后触发，还是持续运行？研究建议采用事件驱动的触发模式：Layer 2 发出 `turn_boundary_candidate` 事件时，Layer 3 进行最终确认。同时，Layer 3 应保留主动干预能力——例如在检测到紧急打断意图时，即使 Layer 2 未发出候选事件，也可以主动触发打断。

## 2.4 级联失败的回退策略

草稿缺少级联失败的回退策略，这是生产环境的必需设计。建议实现四级优雅降级路径：完整链路（Silero VAD → Smart Turn → Decision Token）→ 降级 1（Silero VAD → Smart Turn → 固定 silence timeout）→ 降级 2（Silero VAD → 固定 silence timeout）→ 降级 3（WebRTC VAD → 固定 silence timeout）。每级降级由上一层超时触发，恢复后自动升回完整链路。

## 2.5 延迟预算分配

基于 prodinit.com 和 Master of Code 的 2025–2026 年生产数据分析，VAD/端点检测占总端到端延迟的 30–40%。三层架构的延迟预算建议为：Layer 1（声学 VAD）不超过 10ms，Layer 2（语义 Turn 检测）不超过 100ms，Layer 3（Decision Token）不超过 100ms。总 Turn Controller 延迟控制在 200ms 以内，为下游 ASR/LLM/TTS 留出充足的延迟空间。

## 2.6 对照总结

草稿的三层级联架构方向正确，但需要在三个关键点上修正：Layer 1 应严格限定为语音活动检测而非端点决策；Layer 2 如果基于文本输入则存在延迟瓶颈，建议评估音频原生方案；需要补充完整的级联失败回退策略。三层之间的接口协议（事件传递、时间戳对齐、延迟追踪）也需要明确定义。下一章将深入分析第三层 Decision Token 的语义决策机制。
