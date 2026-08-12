# 第八章 结论与架构建议

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
