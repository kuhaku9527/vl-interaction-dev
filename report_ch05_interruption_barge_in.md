# Chapter 5: 中断/打断机制对照 — INTERRUPTED 状态的重构

草稿状态机包含 INTERRUPTED 状态，表明设计者已经认识到打断处理是 Turn Controller 的核心能力。然而，单一 INTERRUPTED 状态不足以覆盖业界实践中已分化为硬中断、软中断和智能中断的完整打断机制体系。本章从打断分类、状态转移路径、上下文管理和边界条件四个维度进行对照分析。

## 5.1 打断分类：从单一到三种模式

业界已将打断机制分化为三种模式，各有明确的适用场景和延迟目标。硬中断在检测到用户语音后立即停止 TTS 输出和 LLM 推理，TTS flush 目标为 60ms，LLM cancel 目标为 40ms，总打断响应目标低于 150ms。软中断在检测到用户语音后等待当前句子边界结束后再停止，延迟 200–800ms，但体验更自然。智能中断基于声学特征分析（波形形状、语音起始强度、韵律特征）判断用户语音是否构成有效打断，区分真实打断与 backchannel（"嗯"、"对"）、咳嗽和叹气，误触发率目标低于 2%。

LiveKit 于 2025 年推出的 Adaptive Interruption Handling 是智能中断的标杆实现。该方案使用专用音频模型在用户语音的前 200–300ms 内分析声学特征，区分真实打断和背景音。FutureAGI 的 2026 年指南明确指出："宁可慢打断，不可误打断——False-barge-in 是比 slow-barge-in 更糟糕的失败模式。"

草稿的单一 INTERRUPTED 状态无法区分这三种模式。建议将 INTERRUPTED 拆分为 HARD_INTERRUPTED 和 SOFT_INTERRUPTED 两个子状态，并在检测层面支持智能中断的声学确认。

## 5.2 状态转移路径：缺失的关键路径

草稿中 INTERRUPTED 之后的目标状态不明确。社区实践将 INTERRUPTED 定义为瞬态——进入后立即转移到 USER_SPEAKING，不在 INTERRUPTED 状态停留。此外，草稿缺少以下关键转移路径：

THINKING → INTERRUPTED 路径的缺失是一个重要疏漏。用户在 Agent "思考"（LLM 推理）时说话，应取消当前推理并立即开始新的 LISTENING。LiveKit Agents 的 GitHub Issue #3427 专门讨论了 thinking 和 speaking 状态下打断逻辑的独立调优需求。

SOFT_INTERRUPTED → SPEAKING 恢复路径用于处理误触发场景。如果用户只是清嗓子或发出短暂的背景音，声学模型判断为非真实打断后，系统应能恢复播放而非强制进入 LISTENING。

## 5.3 上下文管理：打断后的对话历史

打断后的上下文管理是打断机制中最复杂的部分，草稿未涉及。业界有三种主流模式。Pattern 1（暂存部分话语）将被打断的 Agent 话语以标记形式存入对话状态，下一轮 LLM 可以看到被打断的内容并自行决定处理方式。Pattern 2（仅保留已听到部分）是 LiveKit Agents 的默认行为——对话历史自动截断，仅保留用户实际听到的 Agent 话语部分，基于 TTS 播放进度追踪。Pattern 3（完整上下文加打断标记）保留完整对话历史但标记打断点，LLM 可以看到完整上下文做出更智能的响应，但上下文窗口消耗更大。

推荐采用 Pattern 2 作为默认行为，Pattern 3 作为可选项。关键实现细节是精确追踪"用户实际听到了什么"——需要基于 TTS 播放进度（`played_audio_duration`）截断对话历史，而非简单丢弃整个 turn。

## 5.4 边界条件处理

草稿未覆盖七个关键边界条件。连续打断场景下，用户在 INTERRUPTED 状态下再次说话，应取消当前打断处理并重新开始新的打断流程。打断后立即沉默场景下，用户打断后不说话，应设置 `post_interruption_silence_timeout`（如 5 秒），超时后 Agent 主动询问。TTS 取消不完整场景下，需要实现双重保障——软件 mute 加硬件/系统级音频停止。LLM 取消竞态场景下，使用请求 ID 去重，丢弃过期响应。上下文不一致场景下，基于 TTS 播放进度精确截断。打断风暴场景下，设置最小 Agent 发言时长（`min_agent_speech_duration`），在此期间忽略打断。网络延迟导致延迟打断场景下，忽略过期的打断信号（检查 `turn_id` 匹配）。

## 5.5 回声消除的必要性

Agent 自己的 TTS 输出通过扬声器到麦克风回路被 VAD 检测到，导致自我触发打断，这是打断机制面临的最基础也最容易被忽视的问题。WebRTC AEC3 是标准解决方案——使用已知的 TTS 输出作为参考信号，建模房间声学路径，从麦克风信号中减去预测回声。Coval.ai 的研究指出，近端语音与回声比通常低于 0dB（扬声器比用户更靠近麦克风），非线性失真难以完全消除。无 AEC 则打断不可用，这是 P0 级别的工程要求。

## 5.6 对照总结

草稿的 INTERRUPTED 状态是必要但不充分的。需要将单一 INTERRUPTED 拆分为 HARD_INTERRUPTED 和 SOFT_INTERRUPTED，补充 THINKING → INTERRUPTED 和 SOFT_INTERRUPTED → SPEAKING 恢复路径，实现上下文精确截断，处理七个边界条件，并确保 AEC 回声消除作为基础设施。打断机制的实现优先级建议为：P0 硬中断加 AEC，P1 上下文截断和误触发防护，P2 软中断和指标监控，P3 智能中断和语义打断。下一章将提供可集成到现有技术栈的架构设计与代码级建议。
