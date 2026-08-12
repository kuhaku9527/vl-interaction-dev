# 第六章 代码级集成方案

第五章给出了明确的推荐决策——实施混合路线 H2，分两阶段推进。本章提供可直接对照现有 turn_controller 架构的代码级集成建议，包括模块接口定义、与 13 态状态机的集成点、以及关键代码路径的伪代码。

## 6.1 新增模块：AddresseeDetector

在现有 pipeline 中新增一个独立的 AddresseeDetector 模块，封装声学预筛和融合决策逻辑。该模块位于 Silero VAD 和 sherpa-onnx ASR 之间，与 turn_controller 通过明确的接口交互。

模块的核心接口定义如下。初始化时需要加载 speaker embedding 模型（CAM++ ONNX 文件路径）、设置初始余弦相似度阈值（默认 0.6）、以及可选的已注册说话人 embedding 文件路径。运行时提供两个核心方法：EnrollTargetSpeaker 接受一段注册音频数据，提取 embedding 并存储为"target_user"；ClassifySpeechSegment 接受 VAD 切分后的语音段音频数据，返回三分类结果——TARGET_SPEECH（目标说话人语音，送入 ASR）、NON_TARGET_SPEECH（非目标说话人语音，丢弃）、UNCERTAIN（模糊区间，需语义精判）。

内部实现方面，ClassifySpeechSegment 首先调用 SpeakerEmbeddingExtractor 提取 embedding，然后调用 SpeakerEmbeddingManager.Search 与已注册的 target_user 比对。若余弦相似度低于 T_low（0.3），返回 NON_TARGET_SPEECH；若高于 T_high（0.7），返回 TARGET_SPEECH；若在两者之间，返回 UNCERTAIN。

## 6.2 与 turn_controller 的集成

现有 turn_controller 是一个 13 态状态机，管理语音代理的对话轮次控制逻辑。AddresseeDetector 的集成不改变状态机的状态数量，而是在关键的状态转换点上增加门控条件。

具体而言，在 VAD 检测到语音段结束（VAD_ENDPOINT 事件）后、进入 ASR 处理状态之前，插入一个 ADDressee_CHECK 门控。若 AddresseeDetector 返回 NON_TARGET_SPEECH，状态机直接回到 IDLE 状态，跳过 ASR 和 LLM 推理。若返回 TARGET_SPEECH，正常进入 ASR 处理状态。若返回 UNCERTAIN，正常进入 ASR 处理状态，但在 LLM decision token 阶段启用四态模式（含 not-for-me），由语义分支做最终判定。

在 LLM decision token 阶段，当 AddresseeDetector 此前返回 UNCERTAIN 时，decision token 使用四态 grammar（silence/response/delegation/not-for-me）。若 LLM 输出 not-for-me，状态机进入 SILENCE 状态而非 RESPONSE 状态。当 AddresseeDetector 此前返回 TARGET_SPEECH 时，decision token 使用原有三态 grammar，因为声学预筛已经确认是目标说话人。

## 6.3 融合决策层的实现

融合决策层在 LLM decision token 输出后执行。它接收两个输入：声学分数 S_acoustic（来自 AddresseeDetector 的余弦相似度）和语义分数 S_semantic（从 LLM 输出的 token 概率中提取——若 LLM 输出 not-for-me，S_semantic 为 0；若输出 response 或 delegation，S_semantic 为 1；若输出 silence，S_semantic 为 0.5）。

融合公式初始采用加权平均：S_fusion = 0.4 × S_acoustic + 0.6 × S_semantic。若 S_fusion 大于 0.5，最终判定为面向 AI，正常执行 response 或 delegation。若 S_fusion 小于等于 0.5，最终判定为非面向 AI，状态机进入 SILENCE 状态。

这一设计的关键优势在于：当 AddresseeDetector 返回 TARGET_SPEECH（高置信度目标说话人）时，S_acoustic 约 0.7 至 1.0，即使 LLM 输出 silence（S_semantic 为 0.5），S_fusion 仍约 0.58 至 0.7，高于 0.5 阈值——声学高置信度可以"挽救"语义的不确定。反之，当 AddresseeDetector 返回 UNCERTAIN（S_acoustic 约 0.3 至 0.7）时，语义判断起主导作用——这正是融合权重偏向语义（0.6）的设计意图。

## 6.4 注册流程的集成

注册流程在用户首次使用或手动触发时执行。turn_controller 新增一个 ENROLL 状态，引导用户说出注册语（如"你好，我是你的语音助手用户"）。录制 2 至 3 秒音频后，调用 AddresseeDetector.EnrollTargetSpeaker 提取并存储 embedding。建议录制 2 至 3 遍取平均，sherpa-onnx 的 SpeakerEmbeddingManager.Register 自动对多次注册的 embedding 取平均。

注册数据（target_user 的 embedding 向量）持久化到本地文件（如 JSON 或二进制格式），后续启动时自动加载，无需重复注册。若连续多次识别失败（如连续 5 次 ClassifySpeechSegment 返回 NON_TARGET_SPEECH 但用户确认是在对 AI 说话），可触发重新注册提示。

## 6.5 线程模型与并行化

混合路线 H2 的并行执行是关键设计。声学分支（speaker embedding 提取）和语义分支（ASR 加 LLM）应在不同线程上并行执行。声学分支使用独立的 ONNX Runtime 实例（不影响 ASR 的 ONNX Runtime 实例），语义分支使用现有的 sherpa-onnx ASR 实例和 llama.cpp 实例。

具体线程模型为：VAD 检测到语音段结束后，主线程同时启动两个任务——Task A（声学分支）在线程池线程上运行 speaker embedding 提取和余弦相似度比对，Task B（语义分支）在主线程或另一线程池线程上运行 ASR 和 LLM decision token。两个任务并行执行，融合决策层等待两者都完成后执行。由于声学分支延迟（300 至 400 毫秒）通常小于语义分支延迟（ASR 100 至 300 毫秒加 LLM 200 至 500 毫秒，总计 300 至 800 毫秒），声学分支不会成为瓶颈。

## 6.6 降级与容错

当 speaker embedding 模型加载失败或注册数据缺失时，AddresseeDetector 进入降级模式——所有语音段返回 UNCERTAIN，系统退化为纯语义路线（四态 decision token）。这确保了即使声学模块不可用，系统仍能正常运行（尽管精度降低）。

当 LLM 推理失败或超时时，融合决策层仅依赖声学分数——若 S_acoustic 大于 0.5，判定为面向 AI；否则判定为非面向 AI。这确保了语义模块不可用时，声学预筛仍能提供基本的说话对象判定能力。
