# Chapter 3: Decision Token 语义决策层对照 — 显式决策与增量预测

草稿在第三层使用 "Decision Token" 机制，由 LLM 原生输出 turn 决策。这一设计与 2025–2026 年学术界和工业界的前沿趋势高度吻合，但在决策粒度、实现方式和与第二层的关系上需要进一步细化。

## 3.1 显式 Decision Token vs 隐式语义判断

当前业界在 turn decision 的实现上存在显式和隐式两条路径。OpenAI 的 Semantic VAD 采用隐式方案——模型在推理过程中内部判断语义完整性，通过 `eagerness` 参数控制灵敏度，但不暴露为显式 token。Moshi 的全双工设计则更进一步，turn-taking 完全成为涌现行为，无需任何显式决策机制。

然而，Full-Duplex-Bench（ASRU 2025）的评测结论明确支持显式控制模块："带显式控制模块的级联架构在 turn-taking 上优于端到端模型。"这一结论为草稿的 Decision Token 设计提供了强有力的学术支撑。

显式 Decision Token 的核心优势在于可解释性和可控性。Freeze-Omni 在 frozen LLM 最后添加分类层预测三种状态（听/说/空闲），SoulX-Duplug 将 duplex 交互控制建模为流式状态预测问题，统一 VAD、ASR 和 Turn Detection 为单一框架。这些方案都采用了显式的状态输出，便于调试、A/B 测试和独立优化。

草稿的 Decision Token 如果设计为显式特殊 token（如 `<turn_end>`），需要权衡：TurnGPT 证明了 `<ts>` token 的有效性，但特殊 token 需要修改 tokenizer 和训练流程，且可能与预训练 LLM 的 token 分布冲突。建议采用 Freeze-Omni 的分类头方案——在 LLM 输出层添加轻量分类头，输出多级 turn 状态，而非修改 token 空间。

## 3.2 决策粒度：从 Binary 到多级

草稿的 Decision Token 如果仅输出 binary 决策（说/不说），则粒度不足。FlexDuo 的三态设计（SPEAKING → LISTENING → IDLE）引入了关键的 Idle 状态——模拟人类对话中的"信息过滤"机制，在 Idle 状态下系统不打断但也不完全静默，可以发出 backchannel（"嗯"、"对"）。这有效减少了误打断和噪声触发。

SoulX-Duplug 进一步将状态扩展到多级：静音、用户说话、系统说话、打断、backchannel 等。对于草稿的 Decision Token 层，三级（听/说/空闲）是最低可行粒度，理想情况下应支持多级决策（含打断和 backchannel）。

## 3.3 与 Smart Turn v3.2 的分工

Decision Token 和 Smart Turn v3.2 不应合并，而应分层协同。两者的关注点不同：Decision Token 关注"何时说"（低延迟语义级 turn 判断，约 30ms），Smart Turn 关注"如何说"（策略级行动决策，约 200ms，包括打断策略、等待策略、backchannel 策略）。

建议架构为：Decision Token Layer 输出 turn 状态（speak/listen/idle），Smart Turn v3.2 基于此状态做出具体行动决策（interrupt/wait/backchannel/respond），Response Generation 执行最终动作。这种分层设计让每层可以独立优化和替换。

## 3.4 延迟可行性：200ms 目标

草稿的 Decision Token 延迟目标如果设定为低于 200ms 端到端，在技术上是可行但需要精心优化。延迟预算分解为：音频采集约 20ms，Streaming ASR partial 约 50ms，Decision Token 推理约 30ms（使用轻量分类头或小模型如 0.5B 参数），LLM Prefill 约 50ms（KV Cache 预热后），TTS 首音频约 50ms，总计约 200ms。

实现这一目标的关键优化包括：KV Cache 预热（系统 prompt 和对话历史在 turn 开始前预先计算）、预测性 Prefill（Zink et al. 2024 的预测性 EOU 检测可在 utterance 结束前 300ms 预测，提前启动 LLM prefill）、以及使用轻量级 Decision Model（如 0.5B 参数专门做 turn decision，而非完整 LLM 推理）。

## 3.5 增量决策与可撤销机制

Streaming ASR 的 partial 结果不稳定（会修正），final 结果延迟高。Skantze et al. (2025) 的最佳实践是增量预测加阈值确认——持续做增量预测，当置信度超过阈值时触发决策，同时允许在 final ASR 结果到来时修正之前的决策。SoulX-Duplug 通过联合训练 ASR 和状态预测，让模型学会处理 ASR 不确定性，这是更先进的方案。

## 3.6 对照总结

草稿的 Decision Token 作为独立第三层的设计是正确的，与 2025–2026 年主流趋势一致。需要修正的方面包括：决策粒度应从 binary 扩展到至少三级（听/说/空闲）；建议采用分类头方案而非特殊 token；需要与 Smart Turn v3.2 保持分层协同而非合并；必须实现 KV Cache 预热和增量决策以达到延迟目标。下一章将分析草稿的多场景配置矩阵。
