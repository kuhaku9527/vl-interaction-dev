# 第五章 本地/低算力 Windows 部署评估

第四章拆解了全双工的核心机制，明确了哪些模块复用开源、哪些自研。本章将这些分析落地到具体的部署环境——Windows 10/11、纯 CPU 推理、8–16GB RAM、目标推理框架 llama.cpp + sherpa-onnx——逐模块评估各开源项目的可运行性，并给出本地/云端两档可行性判断。

## 5.1 ASR 模块：sherpa-onnx 是唯一首选

在 Windows 纯 CPU 环境下，sherpa-onnx + SenseVoice-Small 是部署可行性最高的 ASR 方案。官方提供 Windows x64 预编译二进制和 Python wheel，`pip install sherpa-onnx` 即可完成安装，零额外依赖。SenseVoice-Small 模型在 CPU 上 RTF 仅 0.015——处理 10 秒音频约 150ms，比 Whisper-Small 快约 5 倍。模型大小低于 100MB（ONNX 格式），运行时内存低于 500MB，中文识别准确率优于 Whisper。FunASR 的 llama.cpp runtime v0.1.9（2026 年 7 月发布）提供了另一条可行路径——Windows CPU/AVX2 预编译包，GGUF 格式，与现有 llama.cpp 生态无缝集成，中文效果同样优秀。

Whisper.cpp 的 small 模型在 CPU 上 RTF 约 1.0（刚好实时），tiny 模型 RTF 约 0.1 但中文效果差，适合作为低资源备选。Faster-Whisper 通过 CTranslate2 实现 4 倍 CPU 加速，但中文效果仍不如 SenseVoice。云端备选方面，Deepgram Nova-3 延迟低于 300ms、价格 $0.0043/分钟，Azure Speech 延迟 1.5–2 秒、价格 $1.00/小时，阿里云 ASR 中文效果优秀但延迟 1–3 秒。

结论：ASR 模块本地首选 sherpa-onnx + SenseVoice-Small（完全可行），云端备选 Deepgram Nova-3（更低延迟但需网络）。

## 5.2 VAD 模块：Silero VAD 零成本集成

Silero VAD 在 Windows CPU 上完美运行。模型约 2MB（ONNX 格式），推理延迟低于 1ms（处理 30ms 音频块），运行时内存低于 50MB。sherpa-onnx 内置了 Silero VAD，如果已使用 sherpa-onnx ASR，VAD 零额外集成成本。WebRTC VAD 更轻量（低于 1MB 内存）但准确率仅约 Silero 的一半，适合作为初筛层或极低资源场景。云端备选不需要——本地 VAD 已完美满足需求。

## 5.3 LLM/VLM 模块：llama.cpp 是唯一可行路径

llama.cpp 是 Windows CPU 环境下 LLM 推理的事实标准。官方 GitHub Releases 提供 Windows 预编译二进制（CPU/AVX2/Vulkan/CUDA），无需手动编译。7B Q4_K_M 量化模型在 16GB RAM 下约 2–5 tok/s，3B Q4 模型可达 10–20 tok/s，1.5B Q4 模型可达 20+ tok/s。VLM 支持方面，llama.cpp 支持 LLaVA 1.5/1.6 和 Qwen2-VL（需 GGUF 格式），提供 `llama-qwen2vl-cli` 工具。Ollama 提供 Windows 原生应用一键安装和 OpenAI API 兼容接口，底层同样使用 llama.cpp，适合快速原型开发。vLLM 在 Windows CPU 上完全不可行——设计为 GPU 优先，CPU 推理 10–50 倍慢，且仅支持 Linux。

云端备选方面，OpenAI/Claude API 提供最强推理能力但需网络和持续成本。对于桌面陪伴型场景，建议采用混合策略：日常对话使用本地 llama.cpp 3B 模型（低延迟、隐私保护），复杂推理按需切换到云端 API。

## 5.4 TTS 模块：本地 piper TTS，云端 MiniMax

piper TTS 是 Windows 纯 CPU 环境下最实用的本地 TTS。提供 Windows 预编译二进制，`pip install piper-tts` 即可安装。现代桌面 CPU 上比实时快约 10 倍，单个语音模型 ONNX 文件数十 MB，运行时内存低于 200MB，支持中文（zh_CN-huayan-medium）。sherpa-onnx 内置的 VITS/Matcha-TTS 提供类似性能和更好的生态集成。ChatTTS（39,800 Stars）对话风格自然度优秀，但 300M 参数在 CPU 上推理极慢（生成 10 秒音频需数十秒），且 AGPLv3 许可证限制商用。CosyVoice 中文音色质量最佳，但设计为 GPU 推理，CPU 上 10–50 倍慢于 GPU，需要 16GB+ RAM，在纯 CPU 环境下不可行。

用户已在使用的 MiniMax Speech 2.6 是云端 TTS 的优秀选择——延迟低于 250ms，音色质量优秀，价格 $60–100/百万字符。建议保持云端 MiniMax 作为主 TTS，本地 piper TTS 或 sherpa-onnx TTS 作为离线备选。

## 5.5 全栈方案对比

sherpa-onnx 全栈（ASR + VAD + TTS）+ llama.cpp（LLM）是 Windows 纯 CPU 环境下部署可行性最高的组合。安装仅需两个 `pip install`（sherpa-onnx 和 piper-tts）加 llama.cpp 预编译二进制下载。总内存占用约 3–4GB（SenseVoice-Small + Silero VAD + piper TTS + llama.cpp 3B Q4），端到端延迟约 1–3 秒（不含 LLM 生成时间）。所有组件均提供 Windows 原生支持，无需 WSL2 或 Docker。

llama.cpp 全生态（whisper.cpp + llama.cpp + piper TTS）是备选方案，全部 C++ 原生性能最优，但组件间集成需手动串联，ASR 中文效果不如 SenseVoice。LiveKit + 本地组件适合开发测试（提供 Windows 二进制），但生产环境推荐 Linux。Pipecat + 本地组件不推荐 Windows 部署——设计为服务端/云端运行，Python 依赖在 Windows 下兼容性差。

## 5.6 本地/云端两档可行性总结

按模块分别给出本地和云端两档可行性评估。ASR 模块：本地 sherpa-onnx + SenseVoice 完全可行（RTF 0.015，低于 500MB 内存），云端 Deepgram Nova-3 延迟更低但需网络。VAD 模块：本地 Silero VAD 完全可行且无需云端。LLM 模块：本地 llama.cpp 3B Q4 可行（10–20 tok/s，约 2GB 内存），云端 OpenAI/Claude API 推理质量更高但有网络依赖和成本。TTS 模块：本地 piper TTS 可行（RTF 低于 0.1，低于 200MB 内存）但音色一般，云端 MiniMax Speech 2.6 音色优秀且用户已在使用。传输层：本地 WebRTC（LiveKit SFU）可行，云端同样使用 WebRTC 架构。

这一评估表明，用户当前"ASR 可走云端、TTS 已走云端 MiniMax"的混合部署策略是合理的。建议的优化方向是：将 ASR 增加本地 sherpa-onnx 作为主方案或离线备选（降低延迟和网络依赖），保持 TTS 云端 MiniMax 为主（音色质量优先），LLM 采用本地 3B 模型 + 云端 API 的混合策略。
