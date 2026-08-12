# Chapter 1: 状态机架构对照 — 从扁平 7 状态到层次状态机

草稿中定义的统一 Turn Controller 状态机包含七个扁平状态：IDLE → LISTENING → TURN_STARTED → PROCESSING → SPEAKING → INTERRUPTED → ENDED。这一设计抓住了实时语音对话的核心阶段，但与 2024–2026 年业界主流实践相比，在状态完备性、层次结构和转移条件三个维度上存在显著差距。

## 1.1 状态完备性：缺失的关键状态

将草稿的七个状态与 OpenAI Realtime API、LiveKit Agents、Pipecat、ElevenLabs Conversational AI 等主流平台的隐式或显式状态机进行逐项对照，可以发现草稿缺少至少四个对生产环境至关重要的状态。

**WARM_UP / INITIALIZING** 状态是所有平台的隐含前提。OpenAI Realtime API 在 `session.update` 阶段完成模型加载和 WebSocket 连接建立，LiveKit Agents 在 `AgentSession` 构造期间初始化 STT/LLM/TTS 管道。草稿从 IDLE 直接跳入 LISTENING，缺少对会话初始化阶段的建模。在实际部署中，模型加载可能需要数秒，在此期间到达的音频帧需要被缓冲或丢弃，而非直接进入 LISTENING 状态触发 VAD。

**ERROR / RECOVERY** 状态的缺失是最严重的工程风险。Azure DialogServiceConnector 通过 `TurnStatusReceived` 事件显式报告每个 turn 的执行状态（成功/失败/超时/网络断开），而草稿中任何状态都可能发生错误却没有统一的错误处理路径。ASR 转录失败、LLM 推理超时、TTS 合成错误、网络断连——这些场景在草稿状态机中没有对应的状态转移。

**COOLDOWN** 状态在社区实践中被广泛采用。Agent 完成响应后立即进入 LISTENING 可能导致 Agent "抢话"——在用户还没来得及组织下一句话时就检测到短暂的静音并开始新的 turn。社区最佳实践（dev.to, LogRocket）建议在 SPEAKING 结束后设置 200–500ms 的冷却期，期间忽略 VAD 事件或提高语音检测阈值。

**PRE_SPEECH** 状态对应 ElevenLabs 的 Soft Timeout 机制。当 Agent 需要较长时间思考时（如 LLM TTFT 超过 500ms），系统可以播放填充音频（"嗯，让我想想..."），避免用户感知到"死寂"。这一状态在 PROCESSING 和 SPEAKING 之间提供了可选的过渡。

此外，**TURN_STARTED** 状态的语义模糊是一个根本性问题。从名称看，它可能表示"用户开始了一个 turn"（speech_started），也可能表示"Agent 开始处理一个 turn"（turn committed）。在 OpenAI Realtime API 中，这两个概念通过不同的事件明确区分：`input_audio_buffer.speech_started` 表示用户开始说话，`input_audio_buffer.committed` 表示音频缓冲区已提交。建议将 TURN_STARTED 拆分为 USER_SPEAKING 和 PROCESSING 两个独立状态。

## 1.2 层次结构：从扁平到两层 HSM

草稿采用扁平状态机，所有七个状态处于同一层级。这种设计在简单场景下可工作，但随着状态数量增加（如补充上述缺失状态后达到 12+ 状态），扁平结构会导致状态转移的重复定义和组合爆炸。

业界实践强烈指向层次状态机（HSM）。Barr Group 的 HSM 理论指出，HSM 的核心优势在于行为继承——父状态定义的转移自动适用于所有子状态。在 Turn Controller 场景中，这意味着：

- `speech_started → INTERRUPTED` 转移只需在 AGENT_TURN 父状态定义一次，即可自动适用于 PROCESSING、PRE_SPEECH、SPEAKING 和 TOOL_CALLING 等所有子状态。
- `error → ERROR` 转移只需在 SESSION_ACTIVE 父状态定义一次，即可覆盖所有活跃子状态。
- `session.end → ENDED` 转移作为全局转移，从任意状态可达。

推荐的两层 HSM 结构如下：顶层为 SESSION_ACTIVE 父状态，其下分为 USER_TURN（含 LISTENING 和 USER_SPEAKING）和 AGENT_TURN（含 PROCESSING、TOOL_CALLING、PRE_SPEECH 和 SPEAKING）两个父状态，外加 COOLDOWN 和 INTERRUPTED（瞬态）。全局状态包括 IDLE、WARM_UP、ERROR 和 ENDED。

LiveKit Agents 的三状态模型（LISTENING → THINKING → SPEAKING）可以视为这种 HSM 的简化版本，而 Pipecat Flows 的图状态机则展示了更灵活的节点式状态管理。草稿的扁平设计在状态数量较少时足够清晰，但一旦补充 ERROR、COOLDOWN 等生产必需状态，HSM 的优势将变得不可忽视。

## 1.3 状态转移条件：置信度阈值与超时机制

草稿的状态转移条件不够明确。社区最佳实践（dev.to 的 Voice Agent Turn-Taking 实现）展示了两个关键设计要素：置信度阈值和超时机制。

置信度阈值方面，社区实践使用双重阈值：普通 VAD 使用较低阈值（约 0.65）区分真实语音和噪声，Barge-in 使用较高阈值（约 0.75）防止误触发。草稿没有定义任何置信度阈值，这意味着任何 VAD 检测到的语音都会触发状态转移，在噪声环境下极易导致误触发。

超时机制方面，每个状态都需要定义最大停留时间。LISTENING 状态下用户长时间不说话应触发 COOLDOWN 或 IDLE；PROCESSING 状态下 LLM 超时应触发 ERROR 或降级响应；SPEAKING 状态下 TTS 超时应触发 ERROR。草稿缺少这些超时定义，可能导致状态"卡死"。

INTERRUPTED 状态的转移路径也需要明确。社区实践将 INTERRUPTED 定义为瞬态——进入后立即转移到 USER_SPEAKING，不在 INTERRUPTED 状态停留。草稿中 INTERRUPTED 之后的目标状态不明确，这是一个需要修正的关键点。

## 1.4 对照总结

草稿的七状态设计在概念层面覆盖了语音对话的主要阶段，但在工程完备性上存在四个核心差距：缺少 WARM_UP、ERROR、COOLDOWN 和 PRE_SPEECH 状态；TURN_STARTED 语义模糊需拆分；扁平结构在状态扩展时将面临组合爆炸；状态转移条件缺少置信度阈值和超时机制。下一章将深入分析草稿的三层级联架构，探讨 VAD 层面的具体改进方向。
