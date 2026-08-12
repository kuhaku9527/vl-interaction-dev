# 桌面陪伴型语音代理的说话对象判定方案——深度研究报告

> **日期**: 2026-08-12  
> **作者**: Alice 27 (Wind AI)  
> **目标**: 为 Windows 纯 CPU 的 sherpa-onnx + llama.cpp + decision token 三态框架，设计并评估「说话对象判定」（Addressee Detection）的技术方案，输出明确的做/不做决策及推荐路线。  
> **技术栈**: sherpa-onnx (流式ASR) + Silero VAD + llama.cpp + decision token (silence/response/delegation) + turn_controller (13态状态机)  
> **部署环境**: Windows 单机，纯 CPU 推理  
> **设计原则**: 宁可漏、不可乱插（误响应代价 > 漏判代价）

---

## 目录

1. [问题定义与场景建模](#第一章-问题定义与场景建模)
2. [声学路线：基于 sherpa-onnx Speaker Embedding 的方案](#第二章-声学路线基于-sherpa-onnx-speaker-embedding-的方案)
3. [语义路线：LLM Decision Token 扩展方案](#第三章-语义路线llm-decision-token-扩展方案)
4. [混合路线：声学+语义融合架构](#第四章-混合路线声学语义融合架构)
5. [综合对比与推荐决策](#第五章-综合对比与推荐决策)
6. [代码级集成方案](#第六章-代码级集成方案)
7. [交叉验证结论清单与风险提示](#第七章-交叉验证结论清单与风险提示)

---

# 第一章 问题定义与场景建模

本章界定"说话对象判定"在桌面陪伴型语音代理场景下的精确定义、输入输出接口、与现有 decision token 三态框架的关系，并梳理业界产品如何处理同类问题，为后续各章的技术方案评估建立统一的参照系。

## 1.1 场景描述与核心问题

用户在 Windows 桌面环境下运行一个常驻语音代理，使用 live 模式免唤醒词常驻监听。用户离麦克风很近（近场，通常小于 0.5 米），可能同时在打游戏、自言自语、或与身边人交谈。当前系统缺少"说话对象判定"能力——即无法区分用户是在对 AI 说话还是在跟别人说话（或自言自语）。

"说话对象判定"在此场景下的精确定义为：给定一段检测到语音活动（VAD=1）的音频流，判断该段语音的意图接收方是否为 AI 代理，输出二分类结果——AI-directed（面向 AI）或 Non-AI-directed（非面向 AI，包括自言自语、与旁人交谈等）。

## 1.2 输入输出接口定义

该模块的输入为音频流（实时帧级），可选附加上下文包括 ASR 文本假设和说话人嵌入向量。输出为帧级或段级分类标签。粒度方面，借鉴 Google Personal VAD 的设计（Ding et al., 2020），推荐帧级三分类输出：非语音（non-speech）、目标说话人语音（target-speech）、非目标说话人语音（non-target-speech）。延迟要求方面，帧级应小于 100 毫秒，段级（在 VAD 终点后）应小于 500 毫秒。

## 1.3 与现有模块的架构关系

Addressee Detection 应放置在 VAD 之后、ASR 之前（或与 ASR 并行），作为 ASR 的门控模块。只有被判定为 AI-directed 的语音才送入 ASR 和 LLM。这一设计决策有三重收益：降低计算开销（避免对非目标语音做完整的 ASR 和 LLM 推理）、减少误触发（防止代理对用户与旁人的对话做出响应）、保护隐私（非目标语音不进入后续文本处理管线）。

当前 decision token 框架（silence / response / delegation）处理的是"ASR 文本出来后要不要回复"的问题。Addressee Detection 处理的是更上游的问题——"这段语音值不值得做 ASR"。两者互补而非替代：Addressee Detection 的输入是原始音频帧，输出是 AI-directed / Not；Decision Token 的输入是 ASR 文本加对话上下文，输出是 silence / response / delegation。

## 1.4 业界产品对比

主流语音助手几乎都依赖唤醒词作为第一道防线。唤醒词本质上是一种隐式的 addressee detection——用户说出特定短语即表明意图与设备交互。Apple 和 Google 是仅有的两家公开了专门 addressee detection 研究的公司。Apple 的 Device-Directed Speech Detection（DDSD）使用多模态融合（声学 embedding 加 ASR 文本加 LLM），最新方案将 DDSD 建模为文本生成问题，EER 达到 7.45%。Google 的 Personal VAD 是帧级说话人条件 VAD，仅 130K 参数即可在设备端实时运行，专门用于 gating 流式 ASR。

中国主流语音助手（小爱同学、天猫精灵、小度等）均采用纯唤醒词方案，未公开任何 addressee detection 相关技术。桌面 PC 语音代理（InnerZero、MoltBot 等）普遍回避此问题，使用 Push-to-Talk 或唤醒词加 VAD 的简化方案。Google 的 Continued Conversation 模式在唤醒后保持麦克风开启 8 秒，但用户反馈"我们互相说话时它也会误以为在跟它说话"——这说明仅靠 VAD 加时间窗口无法解决 addressee detection 问题。

## 1.5 场景特殊性：桌面陪伴 vs 智能音箱

桌面陪伴场景相比智能音箱场景，存在若干可利用的简化假设。用户离麦克风最近，其语音能量远高于旁人，可以利用能量比作为强特征。用户位置固定（坐在电脑前），声学环境相对稳定，可以建立"用户声学画像"。PC 通常配备摄像头，可以利用视觉线索（面部朝向、嘴唇运动、视线方向）辅助判定。游戏音是可预测的背景噪声，对 VAD 的干扰主要是降低信噪比而非产生假阳性语音段。用户自言自语时通常具有与"对 AI 说话"不同的声学-语言学特征——音量更低、语速更不规律、句法结构更碎片化、缺乏指令式语调。

同时，该场景也存在特有的挑战。免唤醒常驻监听意味着没有明确的"交互开始"信号，系统必须在连续音频流中实时判断每一帧是否面向 AI。用户打游戏时可能大喊、咒骂、欢呼——这些语音能量高、情绪化，容易被误判为"在对 AI 说话"。单麦无法利用空间信息区分说话人，而智能音箱可以用波束成形聚焦于唤醒词来源方向。学术界和工业界的 addressee detection 研究主要面向智能音箱场景（有唤醒词锚点、多麦阵列），桌面常驻监听场景的标注数据几乎不存在。

## 1.6 误判代价分布与设计原则

用户明确要求"宁可漏、不可乱插"——即误响应（没对 AI 说话但 AI 插话）比漏判（对 AI 说话但被忽略）更不能接受。这一偏好深刻影响了后续所有方案的设计：阈值应偏向高精度（低误判），声学预筛的余弦相似度阈值建议设在 0.55 至 0.65 区间，融合决策中语义权重应高于声学权重。漏判的代价较低——用户只需再说一遍即可。

基于以上分析，推荐分层渐进的技术路线：Phase 1 基于声学特征的轻量方案快速可用，Phase 2 引入 ASR 文本特征提升精度，Phase 3 可选引入视觉特征进一步降低误触发。后续各章将分别评估声学路线、语义路线和混合路线的具体方案，并在第五章给出明确的推荐决策。


---

# 第二章 声学路线：基于 sherpa-onnx Speaker Embedding 的方案

承接第一章对问题定义和场景约束的分析，本章深入评估在 sherpa-onnx 生态内增加声学路线说话对象判定的可行性和成本。由于用户当前 pipeline 已使用 sherpa-onnx 作为流式 ASR 引擎，同生态集成成本最低，因此声学路线是首先需要评估的方案。

## 2.1 sherpa-onnx Speaker 模型生态

sherpa-onnx 官方支持三大类 speaker embedding 模型来源：3D-Speaker（阿里达摩院，中文/英文/中英混合）、NeMo（NVIDIA，英文为主）和 WeSpeaker（西工大，英文为主）。对于中文桌面陪伴场景，3D-Speaker 系列是自然首选。

在 3D-Speaker 系列中，CAM++ 模型以 7.2M 参数量、192 维 embedding、27MB ONNX 文件大小和约 0.15 至 0.18 的 CPU RTF 成为速度与精度的最佳平衡点。其 VoxCeleb1-O EER 为 0.65%，CN-Celeb EER 为 6.78%。ERes2NetV2 精度更高（VoxCeleb1-O EER 0.61%），但模型更大（68MB）、速度更慢（RTF 约 0.24）。ERes2Net-Large 精度最高（EER 0.52%），但 180MB 的模型大小和 22.5M 参数量在桌面 CPU 上显得过于沉重。

从 sherpa-onnx 社区实测数据来看，3DSpeaker 加 int8 量化模型的 RTF 约为 0.241，意味着处理 1 秒音频需要约 0.24 秒 CPU 时间。对于实时场景中典型的 2 秒 VAD 语音段，embedding 提取仅需约 0.3 至 0.48 秒。int8 量化是实时场景的必备条件——它使模型大小和推理速度都进入可接受范围。

## 2.2 API 与集成方式

sherpa-onnx 提供完整的 C/C++ Speaker API，核心组件包括 SpeakerEmbeddingExtractor（embedding 提取器）和 SpeakerEmbeddingManager（embedding 管理器）。提取器支持流式输入——通过 OnlineStream 逐步送入音频，AcceptWaveform 可多次调用，IsReady 方法检查音频是否足够长以提取有效 embedding。管理器支持注册（Register）、搜索（Search，基于余弦相似度返回最佳匹配说话人）、验证（Verify）和获取所有已注册说话人（GetAllSpeakers）。多次注册同一说话人时，管理器自动对 embedding 取平均，显著提升稳定性。

注册流程极为简洁：用户说一段 2 至 3 秒的注册语音，提取 embedding，调用 Register 存储为"target_user"。推理阶段，每段 VAD 语音提取 embedding 后调用 Search，若返回"target_user"则送入 ASR 加 LLM，否则丢弃。整个流程无需引入任何额外依赖——sherpa-onnx 已原生支持。

## 2.3 延迟与 CPU 增量评估

端到端延迟方面，Silero VAD 分段约 20 至 50 毫秒（已有 pipeline，几乎无延迟），CAM++ embedding 提取（2 秒语音）约 300 至 360 毫秒，余弦相似度比对小于 1 毫秒，总延迟约 320 至 410 毫秒。这个延迟远小于 LLM 推理延迟（通常 1 至 3 秒），不会成为系统瓶颈。值得注意的是，speaker embedding 提取和 ASR 可以并行执行——它们使用不同的 ONNX 模型和不同的 ONNX Runtime 实例，不增加串行延迟。

CPU 增量方面，speaker embedding 提取是间歇性的（仅在 VAD 检测到语音段时触发），不是持续运行。对于典型的对话场景（用户每 5 至 30 秒说一句话），embedding 提取的 CPU 时间占比极低。使用 CAM++ 模型（27MB，7.2M 参数），内存增量约 30 至 50MB，总体 CPU 增量可忽略不计（小于 5% 平均负载）。

## 2.4 关键工程问题

注册语音的最短时长方面，CAM++ 官方建议至少 0.5 秒，但推荐 2 至 3 秒以获得稳定 embedding。不建议用唤醒词音频做注册——唤醒词通常只有 0.3 至 0.8 秒，太短导致 embedding 质量差。建议让用户说一句完整的注册语（如"你好，我是你的语音助手用户"），可多次注册（如说 3 遍），管理器自动平均。

Embedding 的时效性方面，短期（同一天）影响极小，中期（数周至数月）情绪波动（感冒、疲劳）可能轻微影响 embedding，长期（数年）年龄和声道变化影响中等。建议初始注册时采集 2 至 3 段语音取平均，每次成功识别后用最新 embedding 做滑动平均更新（EMA，α 取 0.1 至 0.2），若连续多次识别失败则触发重新注册提示。

阈值设定方面，在"宁可漏、不可乱插"的原则下，推荐初始阈值设为 0.6（sherpa-onnx Python 示例的默认值），在实际使用中收集数据后根据用户体验调整：若用户抱怨"经常不回复"则降低到 0.55，若"经常误回复"则提高到 0.65。建议最终范围在 0.55 至 0.65 之间。

## 2.5 声学路线的根本局限

声学路线解决的是"谁在说话"（speaker identity），而非"在对谁说话"（addressee）。即使确认语音来自注册用户，也无法区分用户是在对 AI 说话还是在自言自语或与旁人交谈。这是声学路线的根本局限——它只能作为粗筛，不能作为最终判定。此外，短语音（VAD 分段可能产生小于 0.5 秒的短语音段）会导致 IsReady 返回 false，需要设置 VAD 的 min_speech_duration 至少 0.5 秒。高噪声环境下 embedding 质量下降，建议在注册时也采集带噪声的样本。

尽管如此，声学路线作为第一道防线具有不可替代的价值：它可以以极低的成本（小于 5% CPU 增量、300 至 400 毫秒延迟）过滤掉明显非目标说话人的语音，大幅减少后续 ASR 和 LLM 的无效计算。这正是第三章将讨论的语义路线所不具备的优势。


---

# 第三章 语义路线：LLM Decision Token 扩展方案

第二章评估了声学路线的可行性和局限——它能以低成本确认"谁在说话"，但无法回答"在对谁说话"。本章评估另一种思路：利用现有的 LLM（llama.cpp）通过语义上下文直接判断说话对象，并探讨如何将这一能力融入现有的 decision token 三态框架。

## 3.1 纯文本语义判定的能力边界

最关键的证据来自 2025 年 IWSDS 论文对 GPT-4o 的系统性基准测试。在 Addressee Recognition 任务（判断当前话语是对谁说的）中，GPT-4o 准确率仅为 80.9%，而随机基线（始终预测"无特定对象"）为 80.6%——仅比随机高 0.3 个百分点。在 Next Speaker Prediction 任务中，GPT-4o 准确率 46.0%，低于随机基线 50%。模型倾向于过度预测"无特定对象"，经常无法识别话语是针对特定参与者的。

Apple 的 DDSD 系列论文从架构层面印证了这一结论。Apple 将 DDSD 建模为文本生成任务，但必须融合声学 embedding（audio encoder 输出作为 prefix token）加 ASR 文本加置信度信息。纯文本路线的性能在论文中未单独报告，但架构设计本身——以及 Modality Dropout 实验中声学模态缺失时性能显著下降——说明仅靠文本是不够的。工业界共识是声学加文本多模态融合，即使拥有最强 LLM 能力的公司也不认为纯文本语义路线足以解决说话对象判定问题。

纯文本判定之所以困难，根源在于人类判断说话对象依赖大量非文本线索——目光方向、身体朝向、韵律（prosody）、音量——这些信息在 ASR 转写过程中全部丢失。中文口语的省略和指代（"那个""帮我拿一下""他说的对吗"）进一步加剧了歧义。同一句话"几点了"在不同场景下可能是问 AI 也可能是问身边的人，仅从文本无法区分。

## 3.2 Decision Token 扩展方案对比

在现有三态框架上扩展语义说话对象判定，有三种可行方案。方案 A（四态扩展）将 decision token 从 silence/response/delegation 扩展为 silence/response/delegation/not-for-me，LLM 单次推理输出四选一。方案 B（两阶段 pipeline）先用一次 LLM 推理判断说话对象，再用第二次推理做三态决策。方案 C（隐式融入）在 system prompt 中增加"如果用户的话不是对你说的，输出 silence"的指令，让 LLM 在 silence 决策中隐含处理 not-for-me。

方案 A 是语义路线中最优的工程选择。延迟增量几乎为零（仅多一个 token 分类选项，约 50 毫秒），工程复杂度低（仅修改 GBNF grammar 的输出空间），可独立评估 not-for-me 的精确率和召回率，且与现有架构兼容性最好。方案 B 需要两次独立 LLM 推理，延迟翻倍（约 440 毫秒 vs 约 270 毫秒），在 CPU 场景下不推荐。方案 C 虽然工程改动最小，但无法区分"silence 因为不是对我说的"还是"silence 因为不需要回复"，缺乏可调试性。

## 3.3 Prompt 设计建议

基于 GPT-4o 倾向于过度预测"无特定对象"的发现，prompt 设计需要主动引导模型关注"对我说话"的信号。推荐在 system prompt 中明确四态定义和判断规则：如果话语包含对 AI 的称呼（如"嘿""喂"），大概率是对 AI 说的；如果话语是明确的提问或指令且没有指定其他对象，默认是对 AI 说的；如果话语明显是在回应另一个人的话，则不是对 AI 说的。建议在 system prompt 后加入 4 至 6 个覆盖边界情况的 few-shot 示例，让模型通过 in-context learning 隐式学习推理模式。

不建议在 decision token 场景中使用 Chain-of-Thought。CoT 会增加数十到数百个 token 的生成量，在 CPU 上每个 token 约 50 至 200 毫秒，会显著增加延迟。替代方案是将推理过程放在 few-shot 示例中，模型隐式学习而非显式生成推理步骤。

## 3.4 延迟与资源评估

假设使用 Qwen2.5-1.5B Q4_K_M 量化模型在 Windows 桌面 CPU 上运行，现有三态基线（约 200 tokens prompt）的 LLM 推理延迟约 220 毫秒。方案 A 四态扩展（约 250 tokens prompt）约 270 毫秒，仅增加约 50 毫秒。方案 B 两阶段约 440 毫秒，延迟翻倍。llama.cpp 支持通过 cache_prompt 实现跨轮次 KV cache 复用——system prompt 和 few-shot 示例的 KV cache 可在多轮对话中复用，每轮只需处理新增的用户话语部分，可将 prompt eval 延迟从约 250 毫秒降至约 50 至 80 毫秒。

## 3.5 语义路线的根本性架构缺陷

语义路线存在一个无法回避的架构矛盾：必须在 ASR 完整转写后才能判断说话对象，但 ASR 本身是 pipeline 中计算量最大的环节之一。在桌面场景中，用户 8 小时工作中实际对 AI 说话的时间占比可能仅 5% 至 10%，其余 90% 至 95% 的语音是与同事交谈、电话、自言自语或环境噪音。语义路线意味着 sherpa-onnx 流式 ASR 持续运行处理所有这些"无效"语音，80% 至 90% 的 ASR 计算资源被浪费。

这一缺陷正是第二章声学路线的核心价值所在——声学路线可以在 ASR 之前以极低成本过滤非目标语音。语义路线单独使用不可行，但作为混合路线中的精判环节，它提供了声学路线无法提供的"意图理解"维度。这正是第四章将深入探讨的混合路线方案。


---

# 第四章 混合路线：声学+语义融合架构

前两章分别揭示了声学路线和语义路线的互补性——声学路线以低成本确认"谁在说话"但无法判断意图，语义路线能理解意图但必须在 ASR 完成后才能判断且纯文本准确率有限。本章设计并评估三种混合架构方案，调研学术界最新进展，并给出融合策略的工程建议。

## 4.1 三种混合方案设计

方案 H1（级联架构）将声学预筛置于 ASR 之前：Silero VAD 检测到语音段后，先通过 speaker embedding 加余弦相似度快速过滤明显非目标说话人的语音，只有通过声学预筛的语音才送入 ASR 和 LLM。这一方案的工程实现最为简单，各模块独立，声学筛可大幅减少 LLM 调用。但级联架构存在错误传播风险——声学阶段的漏判会导致后续语义阶段完全无法补救。

方案 H2（并行融合架构）将声学和语义分支并行执行：VAD 语音段同时送入 speaker embedding 提取器（声学分支）和 sherpa-onnx ASR 加 LLM decision token（语义分支），两个分支的输出分数在融合决策层合并。融合层可采用加权平均、带交互项的加权融合、逻辑回归或轻量 MLP。这一方案的延迟最优（并行执行，总延迟等于最慢分支的延迟，约 300 至 500 毫秒），声学和语义互补，鲁棒性最好。

方案 H3（特征注入 LLM）将 speaker embedding 作为 LLM 的额外输入 token，让 LLM 在单次推理中同时处理声学和语义信息。这一方案理论上精度最高（LLM 可学习声学-语义联合模式），但需要 LLM 支持额外 token 类型或改造 embedding 层，工程复杂度高，且增加 LLM 推理延迟。

综合延迟、准确率预期、工程复杂度和与现有架构的兼容性，方案 H2（并行融合）是推荐方案。它充分利用了用户技术栈的模块化优势——sherpa-onnx 的 speaker embedding 和 ASR 可以并行运行在不同的 ONNX Runtime 实例上，llama.cpp 的 LLM 推理可以与声学分支并行。

## 4.2 学术界最新进展

Google 的 Personal VAD 系列是最直接可参考的架构。Personal VAD 2.0（Interspeech 2022）使用流式 Conformer 编码器加 FiLM（Feature-wise Linear Modulation）进行说话人嵌入调制，8-bit 量化后模型仅 1.0MB。其核心思想——将说话人嵌入作为条件注入 VAD 模型，实现帧级三分类——可以直接映射到用户场景：用 sherpa-onnx 的 speaker embedding 替代 Google 的 d-vector，用 Silero VAD 的输出作为基础 VAD 信号。

ModeratorLM（2025/2026）代表了最前沿的方向：基于 Qwen3-4B 的 Speech LLM 完全自主决定 turn-taking，不依赖外部 VAD 模块。但 4B 参数级 LLM 在纯 CPU 上的延迟可能过高，且需要语音编码器处理 chunk-wise 音频流。其核心思想——将 turn-taking 决策内化到 LLM 中——与用户现有的 decision token 框架思路一致，可作为长期演进方向。

Wake Word Reference PVAD（ICASSP 2024）提供了一个极具启发性的简化方案：直接使用唤醒词的原始帧级特征作为目标说话人属性，无需额外的说话人验证模型提取嵌入。如果用户的语音代理有唤醒词，可以直接用唤醒词语音段作为注册，大幅简化注册流程。

学术界趋势呈现清晰的演进路径：从独立 VAD 到 Personal VAD 再到 Speech LLM 端到端 turn-taking。说话人条件机制（FiLM、交叉注意力、Speaker PreNet）是主流融合方式。轻量化（8-bit 量化、知识蒸馏、SincNet 特征提取）是设备端部署的关键。

## 4.3 融合策略的工程细节

推荐采用双阈值策略作为融合决策的核心机制。当声学分数 S_acoustic 低于下阈值 T_low（建议 0.3）时，直接判定为 silence，无需进入语义判断——这可以过滤掉明显非目标说话人的语音，节省 LLM 推理。当 S_acoustic 高于上阈值 T_high（建议 0.7）时，进入语义判断，若语义分数也高则响应。当 S_acoustic 处于模糊区间时，使用融合分数 S_fusion 做最终判定。

融合公式推荐从加权平均起步：S_fusion = 0.4 × S_acoustic + 0.6 × S_semantic。语义权重更高（0.6）的理由是语义判断更直接地回答"是否在对我说话"，声学判断只是辅助。后续可升级为逻辑回归融合（加入 ASR 置信度、VAD 段长度等特征），仅需几百条标注数据即可训练。

声学和语义不一致时的处理策略需要根据场景区分。当声学说"是"（高相似度）但语义说"不是"（非定向内容）时——例如设备主人在对别人说话——应以语义为准，这对应 Apple False Trigger Mitigation 的核心逻辑。当声学说"不是"（低相似度）但语义说"是"（定向内容）时——例如访客想使用代理——取决于产品定位：严格模式拒绝，开放模式接受。当两者都不确定时，遵循"宁可漏、不可乱插"原则，采用保守策略判定为 silence。

## 4.4 与业界方案的差距评估

用户技术栈与 Apple/Google 方案的核心差距在于三个方面。单麦无空间信息——智能音箱可以用波束成形聚焦于特定方向，桌面单麦无法利用空间信息区分说话人。这一差距可通过加强说话人嵌入模型的判别力（ECAPA-TDNN 在单麦条件下表现优秀）和利用语义上下文弥补。模块独立而非端到端优化——可通过 H2 并行融合架构的融合层学习模块间互补关系来弥补。训练数据不足——可使用公开数据集（VoxCeleb、LibriSpeech）加数据增强，利用 LLM 的零样本/少样本能力进行语义判断。

用户技术栈也有独特的优势。LLM decision token 框架比 Apple/Google 的浅层语义判断（ASR 格分析）能进行更深层的语义理解。桌面场景不受功耗和散热严格限制，可以使用更大模型。模块化架构可以灵活替换各组件，快速迭代。开源生态（sherpa-onnx 加 llama.cpp）社区活跃，持续改进。

综合以上分析，混合路线——具体而言，方案 H2 并行融合架构——是当前技术栈约束下的最优解。第五章将把四条路线（纯声学、纯语义、混合 H1/H2/H3、不做）放在一起进行全面对比，给出明确的推荐决策。


---

# 第五章 综合对比与推荐决策

前四章分别从问题定义、声学路线、语义路线和混合路线四个维度进行了深入分析。本章将所有方案放在同一框架下进行全面对比，给出明确的推荐决策和取舍论证。

## 5.1 四条路线的全面对比

下表从十个关键维度对比四条路线：不做（维持现状）、纯声学路线、纯语义路线（四态 decision token）、混合路线 H2（声学+语义并行融合）。

| 维度 | 不做（现状） | 纯声学路线 | 纯语义路线 | 混合路线 H2 |
|------|------------|-----------|-----------|------------|
| **说话对象判定能力** | 无 | 仅"谁在说话" | "在对谁说话"（不可靠） | "谁在说话"+"在对谁说话" |
| **误响应控制** | 无（全靠 VAD+decision token） | 中等（过滤非目标说话人） | 低（纯文本准确率仅略高于随机） | 较高（声学+语义双重验证） |
| **漏判风险** | 无（所有语音都处理） | 中等（可能漏判目标说话人） | 低（倾向于过度判定为"对我说的"） | 可控（双阈值可调） |
| **延迟增量** | 0ms | +300-400ms | +50ms | +300-500ms（并行） |
| **CPU 增量** | 0% | <5%（间歇） | ~0%（复用现有 LLM） | <5%（间歇）+ LLM 增量 |
| **内存增量** | 0MB | +30-50MB | ~0MB | +30-50MB |
| **ASR 计算浪费** | 100%（所有语音都做 ASR） | 大幅减少（仅目标说话人） | 100%（所有语音都做 ASR） | 大幅减少（声学预筛） |
| **工程复杂度** | 零 | 低（sherpa-onnx 原生支持） | 低（仅改 grammar+prompt） | 中（需融合层） |
| **可调试性** | N/A | 高（独立模块） | 中（需单独评估 not-for-me） | 高（每分支独立评估） |
| **渐进式部署** | N/A | ✅ 可独立上线 | ✅ 可独立上线 | ✅ 先声学后语义 |

## 5.2 推荐决策：做，选混合路线 H2

**明确推荐：实施混合路线 H2（声学+语义并行融合），分两阶段推进。**

这一推荐基于以下核心论证。第一，纯声学路线虽然成本极低且立即可用，但它只能回答"谁在说话"而非"在对谁说话"——用户对旁人说话时，声学路线会判定为"是目标说话人"而放行，这正是用户最不能接受的误响应场景。第二，纯语义路线虽然能直接回答"在对谁说话"，但 GPT-4o 级别的模型在三方对话中仅略高于随机基线（80.9% vs 80.6%），且必须在 ASR 完成后才能判断，导致 80% 至 90% 的 ASR 计算资源被浪费。第三，混合路线 H2 结合了两者优势——声学预筛过滤非目标说话人（节省 ASR 计算），语义精判区分"对 AI 说"和"对旁人说"（控制误响应），并行执行不增加串行延迟。

不做（维持现状）的代价也需要明确。当前系统在多人场景下会频繁误响应——用户与旁人交谈时，VAD 检测到语音，ASR 转写，LLM 判断为需要回复，AI 插话。这在"宁可漏、不可乱插"的原则下是最不可接受的失败模式。随着语音代理从原型走向日常使用，这一问题的频率和影响会持续增长。

## 5.3 取舍论证

选择混合路线 H2 意味着接受以下取舍。增加的工程复杂度（融合层设计、双阈值调优、两分支并行管理）换来了更低的误响应率和更少的 ASR 计算浪费。增加的 30 至 50MB 内存和小于 5% 的平均 CPU 增量换来了可独立评估、可渐进调优的说话对象判定能力。300 至 500 毫秒的延迟增量（与 LLM 推理并行，不增加串行延迟）换来了在"宁可漏、不可乱插"原则下可控的误判风险。

不选择纯声学路线的原因：它无法区分"用户在对 AI 说话"和"用户在跟旁人说同样的话"——这是桌面陪伴场景的核心痛点。不选择纯语义路线的原因：纯文本准确率不足以支撑生产使用，且 ASR 计算浪费严重。不选择方案 H3（特征注入 LLM）的原因：需要改造 LLM 的 embedding 层或训练专用模型，工程复杂度远超收益，且增加 LLM 推理延迟。

## 5.4 两阶段实施路线

第一阶段（1 至 2 周）实施声学预筛。集成 3D-Speaker CAM++ 模型到现有 C++ pipeline，在 Silero VAD 分段后插入 speaker embedding 提取和余弦相似度比对，实现说话人注册（录制 2 至 3 秒语音，提取 embedding，存储为"target_user"），设置初始阈值 0.6。这一阶段可以独立上线——即使没有语义精判，声学预筛也能过滤掉非目标说话人的语音，减少误响应。

第二阶段（2 至 4 周）实施语义精判和融合。扩展 decision token 为四态（silence/response/delegation/not-for-me），设计专用 prompt 和 few-shot 示例，实现双阈值融合决策层（先用加权平均，收集数据后升级为逻辑回归），联合调优声学和语义阈值。这一阶段在第一阶段的基础上增加了"意图理解"维度，可以区分"目标说话人在对 AI 说话"和"目标说话人在对旁人说/自言自语"。

## 5.5 预期收益与误判代价

预期收益方面，声学预筛可过滤约 70% 至 90% 的非目标说话人语音（取决于场景中旁人的说话频率），大幅减少 ASR 和 LLM 的无效计算。语义精判可在声学预筛通过的基础上进一步过滤"目标说话人但非面向 AI"的语音（如自言自语、与旁人交谈），预期将误响应率降低 50% 至 80%。

误判代价方面，漏判（用户对 AI 说话但被判定为不是）的代价较低——用户只需再说一遍。误响应（没对 AI 说话但 AI 插话）的代价较高——打断用户与旁人的对话或游戏沉浸感。双阈值策略允许独立调节这两个方向的敏感度：降低 T_low 可减少漏判，提高 T_fusion 可减少误响应。在"宁可漏、不可乱插"的原则下，初始参数应偏向保守（高阈值），后续根据用户反馈调整。


---

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


---

# 第七章 交叉验证结论清单与风险提示

前六章完成了从问题定义到代码级集成的完整分析。本章提供一份可交叉验证的结论清单——每一条结论都标注了来源和验证方法——以及实施过程中的关键风险和缓解措施。

## 7.1 可交叉验证的结论清单

以下结论按主题分组，每条结论包含主张、证据来源和验证方法。

### 关于问题定义

**结论 1**：桌面陪伴型语音代理的"说话对象判定"应定义为 VAD 之后、ASR 之前的门控模块，输出帧级三分类（非语音/目标说话人语音/非目标说话人语音）。来源：Google Personal VAD 架构（Ding et al., 2020）和 Apple Voice Trigger 系统（2023）。验证方法：检查 pipeline 中 AddresseeDetector 模块的插入位置是否在 VAD 之后、ASR 之前。

**结论 2**：在"宁可漏、不可乱插"的原则下，系统阈值应偏向高精度（低误判），声学余弦相似度阈值建议 0.55 至 0.65，融合阈值建议 0.5 以上。来源：sherpa-onnx Python 示例默认 threshold=0.6，C# 示例默认 threshold=0.5。验证方法：在实际使用中收集 ROC 数据，确认 FAR 低于用户可接受水平。

### 关于声学路线

**结论 3**：sherpa-onnx 原生支持 speaker identification，推荐模型为 3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx（27MB，7.2M 参数，RTF 约 0.15 至 0.18，192 维 embedding）。来源：sherpa-onnx 官方文档和 HuggingFace 模型仓库。验证方法：下载模型，运行 sherpa-onnx 的 speaker-identification.py 示例，实测 RTF 和 embedding 质量。

**结论 4**：声学路线的端到端延迟约 300 至 400 毫秒（VAD 分段 20 至 50 毫秒加 CAM++ embedding 提取 300 至 360 毫秒加余弦相似度比对小于 1 毫秒），CPU 增量小于 5%（间歇性触发），内存增量约 30 至 50MB。来源：sherpa-onnx Discussion #3233 的 RTF benchmark 数据。验证方法：在目标 Windows 机器上运行 benchmark，实测延迟和 CPU 占用。

**结论 5**：声学路线只能回答"谁在说话"（speaker identity），不能回答"在对谁说话"（addressee）。即使确认语音来自注册用户，也无法区分用户是在对 AI 说话还是在自言自语或与旁人交谈。来源：Personal VAD 论文的问题定义（三分类：非语音/目标说话人/非目标说话人，不含 addressee 维度）。验证方法：构造测试用例——注册用户对旁人说"今天天气不错"——确认声学路线判定为 TARGET_SPEECH（无法区分 addressee）。

### 关于语义路线

**结论 6**：纯文本语义判定说话对象不可靠。GPT-4o 在三方对话中的 addressee recognition 准确率仅 80.9%，随机基线 80.6%。来源：IWSDS 2025 论文 "An LLM Benchmark for Addressee Recognition in Multi-modal Multi-party Dialogue"。验证方法：使用用户的 LLM（llama.cpp）运行相同的 benchmark，确认准确率是否在类似范围。

**结论 7**：方案 A（四态 decision token：silence/response/delegation/not-for-me）是语义路线中最优的工程方案，延迟增量约 50 毫秒，仅需修改 GBNF grammar 和 prompt。来源：第三章的方案对比分析。验证方法：修改 grammar 为四选一，测量延迟增量。

**结论 8**：语义路线存在根本性架构缺陷——必须在 ASR 完成后才能判断，导致 80% 至 90% 的 ASR 计算资源浪费在非面向 AI 的语音上。来源：Apple DDSD 架构分析（声学特征作为 prefix token，在文本之前处理）。验证方法：在实际使用中统计 ASR 处理的语音段中实际面向 AI 的比例。

### 关于混合路线

**结论 9**：方案 H2（声学+语义并行融合）是推荐架构。延迟最优（并行执行，300 至 500 毫秒），声学和语义互补，工程复杂度适中。来源：第四章的三种混合方案对比。验证方法：实现原型后对比 H1（级联）和 H2（并行）的端到端延迟和准确率。

**结论 10**：融合权重建议初始设为 S_fusion = 0.4 × S_acoustic + 0.6 × S_semantic，语义权重更高。来源：第四章的融合策略分析。验证方法：在标注数据上网格搜索最优权重，确认 0.4/0.6 是否接近最优。

**结论 11**：学术界最值得关注的三篇工作——Personal VAD 2.0（Google, Interspeech 2022，Conformer+FiLM，8-bit 量化 1.0MB）、ModeratorLM（2025/2026，Speech LLM 端到端 turn-taking）、Wake Word Reference PVAD（ICASSP 2024，唤醒词直接作为注册语音）。来源：第四章的学术论文汇总表。验证方法：阅读原始论文，评估方法迁移到用户技术栈的可行性。

### 关于工程落地

**结论 12**：推荐两阶段实施——Phase 1（1 至 2 周）声学预筛独立上线，Phase 2（2 至 4 周）语义精判和融合。来源：第五章的推荐决策。验证方法：按阶段实施，每阶段结束后评估误响应率和用户满意度。

**结论 13**：AddresseeDetector 模块应作为独立组件，通过明确的接口与 turn_controller 交互，不改变状态机状态数量，仅在关键状态转换点增加门控条件。来源：第六章的代码级集成方案。验证方法：检查集成后的 turn_controller 代码，确认状态数量未增加，门控逻辑清晰。

## 7.2 风险矩阵

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| CAM++ 模型在中文桌面场景的 embedding 区分度不足 | 中 | 声学预筛效果差，大量 UNCERTAIN 输出 | 在目标场景收集注册和测试语音，实测区分度；必要时换用 ERes2NetV2 |
| 四态 decision token 的 not-for-me 准确率不足 | 高 | 语义精判不可靠，融合决策退化为纯声学 | 持续优化 prompt 和 few-shot 示例；收集真实场景数据做 fine-tuning |
| 声学和语义并行执行导致 CPU 资源竞争 | 低 | 延迟增加，用户体验下降 | 设置线程优先级；声学分支使用独立 ONNX Runtime 实例 |
| 注册语音太短导致 embedding 不稳定 | 中 | 频繁误判为非目标说话人 | 设置最小注册时长 2 秒；多次注册取平均；实现 EMA 更新 |
| 用户声音变化（感冒、疲劳）导致识别率下降 | 中 | 漏判增加 | 实现 embedding 滑动平均更新；连续失败时触发重新注册 |
| 游戏背景音降低 VAD 和 embedding 质量 | 低 | 声学预筛准确率下降 | 注册时采集带游戏背景音的样本；提高 VAD 的语音/非语音阈值 |

## 7.3 下一步行动

基于以上分析和推荐，建议按以下顺序执行。第一步（本周），下载 CAM++ 模型，运行 sherpa-onnx 的 speaker-identification.py 示例，在目标 Windows 机器上实测 RTF、延迟和 CPU 占用，确认声学路线的性能假设。第二步（下周），实现 AddresseeDetector 模块的 C++ 原型，集成到现有 pipeline 中，在 VAD 分段后插入 speaker embedding 提取和比对，收集真实场景的声学分数分布数据。第三步（第三周），扩展 decision token 为四态，设计 prompt 和 few-shot 示例，在标注数据上评估 not-for-me 准确率。第四步（第四周），实现融合决策层，联合调优声学和语义阈值，进行端到端测试。

以上七章构成了从问题定义到工程落地的完整分析。最终推荐明确：实施混合路线 H2，分两阶段推进，先声学预筛快速可用，再语义精判提升精度。这一方案在用户的技术栈约束下（Windows 纯 CPU、sherpa-onnx 加 llama.cpp 加 decision token）是最优的工程选择。


---

