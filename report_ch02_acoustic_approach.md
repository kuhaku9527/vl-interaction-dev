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
