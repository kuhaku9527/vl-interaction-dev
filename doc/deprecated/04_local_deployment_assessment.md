# 开源项目本地/低算力 Windows 环境部署可行性评估

> **评估日期**: 2026-08-11  
> **基准环境**: Windows 10/11, 纯 CPU 推理, 8-16GB RAM, 目标框架 llama.cpp + sherpa-onnx

---

## 目录

1. [部署基准环境](#1-部署基准环境)
2. [ASR 模块评估](#2-asr-模块评估)
3. [VAD 模块评估](#3-vad-模块评估)
4. [LLM/VLM 模块评估](#4-llmvlm-模块评估)
5. [TTS 模块评估](#5-tts-模块评估)
6. [全栈方案评估](#6-全栈方案评估)
7. [云端备选方案](#7-云端备选方案)
8. [工程代价综合对比](#8-工程代价综合对比)
9. [推荐模块组合方案](#9-推荐模块组合方案)
10. [信息来源](#10-信息来源)

---

## 1. 部署基准环境

| 维度 | 规格 |
|------|------|
| **操作系统** | Windows 10/11 (x64) |
| **推理硬件** | 纯 CPU（无 GPU） |
| **内存预算** | 8-16 GB RAM |
| **目标推理框架** | llama.cpp（LLM/VLM）、sherpa-onnx（ASR/TTS/VAD） |
| **关键约束** | 无 CUDA、无 Metal、无 Vulkan 加速；所有模型必须 CPU 可运行 |

---

## 2. ASR 模块评估

### 2.1 sherpa-onnx + SenseVoice

| 维度 | 评估 | 详情 |
|------|------|------|
| **Windows 兼容性** | ✅ 优秀 | 官方提供 Windows x64 预编译二进制和 Python wheel（`pip install sherpa-onnx`），无需编译 |
| **CPU 推理延迟** | ✅ 极低 | SenseVoice-Small RTF ≈ 0.015（CPU），处理 10s 音频仅需 ~150ms；比 Whisper-Small 快约 5x |
| **内存占用** | ✅ 低 | SenseVoice-Small 模型约 80-120MB（ONNX 格式），运行时内存 < 500MB |
| **模型大小** | ✅ 小 | ONNX 量化模型 < 100MB |
| **中文识别** | ✅ 优秀 | 原生支持中英日韩粤语，中文识别准确率优于 Whisper |
| **安装复杂度** | ⭐ 极低 | `pip install sherpa-onnx` 即可，含所有依赖 |
| **综合评价** | 🟢 **强烈推荐** | Windows CPU 环境下最佳 ASR 方案 |

### 2.2 Faster-Whisper / Whisper.cpp

| 维度 | 评估 | 详情 |
|------|------|------|
| **Windows 兼容性** | ✅ 良好 | whisper.cpp 提供 Windows 预编译二进制（`whisper-bin-x64.zip`），无需编译环境；Faster-Whisper 需 Python + CTranslate2 |
| **CPU 推理速度** | ⚠️ 中等 | tiny: ~6min/60min 音频（RTF≈0.1）；small: ~60min/60min（RTF≈1.0）；medium: ~200min/60min（RTF≈3.3）；large-v3: ~600min/60min（RTF≈10） |
| **模型选择** | ✅ 丰富 | tiny(39M) / base(74M) / small(244M) / medium(769M) / large-v3(1.55B) / turbo |
| **内存占用** | ⚠️ 中等 | tiny ~1GB, small ~2GB, medium ~5GB, large-v3 ~10GB |
| **中文识别** | ⚠️ 一般 | 中文 WER 高于 SenseVoice，large-v3 才有较好中文效果 |
| **安装复杂度** | ⭐⭐ 低 | whisper.cpp 预编译二进制直接可用；Faster-Whisper 需 `pip install faster-whisper` |
| **综合评价** | 🟡 **备选** | tiny/small 模型适合低资源场景，但中文效果不如 SenseVoice |

### 2.3 FunASR

| 维度 | 评估 | 详情 |
|------|------|------|
| **Windows 兼容性** | ⚠️ 改善中 | 2026年7月发布 llama.cpp runtime v0.1.9，提供 Windows CPU/AVX2 预编译包（`funasr-llamacpp-windows-x64-cpu.zip`），无需 Python |
| **CPU 推理速度** | ✅ 良好 | 通过 llama.cpp/GGUF 运行时，SenseVoiceSmall 在 CPU 上 RTF ≈ 0.015-0.02 |
| **依赖复杂度** | ⚠️ 中等 | Python 版需 `funasr` + `modelscope` + `torch`；新 llama.cpp runtime 版零依赖 |
| **内存占用** | ✅ 低 | GGUF q8 量化模型约 80-120MB |
| **综合评价** | 🟢 **推荐（llama.cpp runtime）** | 新 llama.cpp runtime 路径使 FunASR 在 Windows CPU 上变得可行，与现有 llama.cpp 生态无缝集成 |

### 2.4 ASR 模块总结

| 方案 | Windows兼容 | CPU延迟 | 内存 | 中文效果 | 安装难度 | 推荐度 |
|------|:--:|:--:|:--:|:--:|:--:|:--:|
| **sherpa-onnx + SenseVoice** | ✅ | RTF 0.015 | <500MB | 优秀 | ⭐ | 🟢 首选 |
| **FunASR llama.cpp runtime** | ✅ | RTF 0.015-0.02 | <500MB | 优秀 | ⭐ | 🟢 推荐 |
| **whisper.cpp small** | ✅ | RTF ~1.0 | ~2GB | 一般 | ⭐ | 🟡 备选 |
| **whisper.cpp tiny** | ✅ | RTF ~0.1 | ~1GB | 差 | ⭐ | 🟡 低资源备选 |
| **Faster-Whisper** | ✅ | 类似whisper.cpp | ~2-5GB | 一般 | ⭐⭐ | 🟡 备选 |
| **FunASR Python** | ⚠️ | 需GPU | 高 | 优秀 | ⭐⭐⭐ | 🔴 不推荐CPU |

---

## 3. VAD 模块评估

### 3.1 Silero VAD

| 维度 | 评估 | 详情 |
|------|------|------|
| **ONNX 导出** | ✅ 支持 | 官方提供 ONNX 导出，模型约 2.2MB |
| **Windows 兼容性** | ✅ 良好 | PyTorch JIT 或 ONNX Runtime 均可运行 |
| **CPU 推理延迟** | ✅ 极低 | 处理 30ms 音频块 < 1ms（单 CPU 线程），RTF ≈ 0.004 |
| **内存占用** | ✅ 极低 | 模型 ~2MB，运行时 < 50MB |
| **准确度** | ✅ 高 | 优于 WebRTC VAD，支持 8000/16000 Hz |
| **安装复杂度** | ⭐⭐ 低 | `pip install silero-vad` 或直接用 ONNX Runtime |
| **综合评价** | 🟢 **强烈推荐** | 轻量、高精度、低延迟，Windows CPU 完美运行 |

### 3.2 WebRTC VAD

| 维度 | 评估 | 详情 |
|------|------|------|
| **Windows 集成** | ✅ 优秀 | `pip install webrtcvad`，纯 Python 绑定 |
| **参数调优** | ✅ 简单 | 仅一个参数：aggressiveness (0-3)，0=最不激进，3=最激进 |
| **CPU 推理延迟** | ✅ 极低 | 极轻量，几乎无感知延迟 |
| **内存占用** | ✅ 极低 | < 1MB |
| **准确度** | ⚠️ 一般 | 约为 Silero VAD 的一半准确度，容易漏检或误检 |
| **综合评价** | 🟡 **轻量备选** | 适合对延迟极度敏感、可接受一定误检的场景 |

### 3.3 sherpa-onnx 内置 VAD

| 维度 | 评估 | 详情 |
|------|------|------|
| **Windows 兼容性** | ✅ 优秀 | 随 sherpa-onnx 一起安装，预编译 Windows 二进制 |
| **CPU 推理延迟** | ✅ 极低 | 使用 Silero VAD 模型，RTF ≈ 0.004 |
| **集成便利性** | ✅ 极佳 | 与 sherpa-onnx ASR 无缝集成，单次调用完成 VAD+ASR |
| **综合评价** | 🟢 **强烈推荐** | 如果已使用 sherpa-onnx ASR，内置 VAD 是最佳选择 |

### 3.4 VAD 模块总结

| 方案 | 延迟 | 准确度 | 内存 | 集成难度 | 推荐度 |
|------|:--:|:--:|:--:|:--:|:--:|
| **sherpa-onnx 内置 VAD** | <1ms | 高 | <50MB | ⭐ | 🟢 首选 |
| **Silero VAD (ONNX)** | <1ms | 高 | <50MB | ⭐⭐ | 🟢 推荐 |
| **WebRTC VAD** | <0.1ms | 中 | <1MB | ⭐ | 🟡 轻量备选 |

---

## 4. LLM/VLM 模块评估

### 4.1 llama.cpp

| 维度 | 评估 | 详情 |
|------|------|------|
| **Windows 编译** | ✅ 良好 | 官方 GitHub Releases 提供 Windows 预编译二进制（CPU/AVX2/Vulkan/CUDA），无需手动编译 |
| **CPU 推理性能** | ⚠️ 取决于模型 | 7B Q4 模型在 16GB RAM 下约 2-5 tok/s；1.5B-3B 模型可达 10-20 tok/s |
| **VLM 支持** | ✅ 支持 | 支持 LLaVA 1.5/1.6、Qwen2-VL（需 GGUF 格式），Windows CPU 可运行 |
| **Qwen2-VL 支持** | ✅ 支持 | 需将模型转换为 GGUF 格式，llama.cpp 提供 `llama-qwen2vl-cli` 工具 |
| **内存占用** | ⚠️ 较高 | 7B Q4: ~4-5GB；3B Q4: ~2GB；1.5B Q4: ~1GB |
| **安装复杂度** | ⭐⭐ 低 | 预编译二进制直接下载使用；Python 绑定 `pip install llama-cpp-python` |
| **综合评价** | 🟢 **强烈推荐** | Windows CPU LLM 推理的事实标准，生态最成熟 |

### 4.2 Ollama

| 维度 | 评估 | 详情 |
|------|------|------|
| **Windows 支持** | ✅ 优秀 | 2024年2月发布 Windows Preview，2025年正式支持原生 Windows 应用 |
| **API 兼容性** | ✅ 优秀 | 完全兼容 OpenAI Chat Completions API（`POST /v1/chat/completions`） |
| **CPU 推理** | ✅ 支持 | 无 GPU 时自动回退到 CPU 推理 |
| **安装复杂度** | ⭐ 极低 | `OllamaSetup.exe` 一键安装，无需任何配置 |
| **内存占用** | ⚠️ 较高 | 底层使用 llama.cpp，内存占用与 llama.cpp 相同 |
| **VLM 支持** | ⚠️ 有限 | 支持 LLaVA 等模型，但 VLM 支持不如原生 llama.cpp 灵活 |
| **综合评价** | 🟢 **推荐** | 安装最简单，API 兼容性好，适合快速原型开发 |

### 4.3 vLLM

| 维度 | 评估 | 详情 |
|------|------|------|
| **CPU 推理** | 🔴 基本不可行 | vLLM 设计为 GPU 优先，CPU 推理性能极差（10-50x 慢于 GPU） |
| **Windows 支持** | 🔴 不支持 | vLLM 仅支持 Linux，Windows 需通过 WSL2 间接运行 |
| **内存需求** | 🔴 极高 | 需要 64GB+ RAM 才能勉强运行 7B 模型 |
| **综合评价** | 🔴 **不推荐** | 完全不适合 Windows CPU 环境 |

### 4.4 LLM/VLM 模块总结

| 方案 | Windows兼容 | CPU性能 | VLM支持 | 安装难度 | 推荐度 |
|------|:--:|:--:|:--:|:--:|:--:|
| **llama.cpp** | ✅ | 2-20 tok/s | ✅ LLaVA/Qwen2-VL | ⭐⭐ | 🟢 首选 |
| **Ollama** | ✅ | 2-20 tok/s | ⚠️ 有限 | ⭐ | 🟢 推荐 |
| **vLLM** | 🔴 | 不可用 | ❌ | ⭐⭐⭐⭐⭐ | 🔴 不推荐 |

---

## 5. TTS 模块评估

### 5.1 sherpa-onnx TTS (VITS/Matcha-TTS)

| 维度 | 评估 | 详情 |
|------|------|------|
| **Windows 兼容性** | ✅ 优秀 | 预编译 Windows 二进制和 Python wheel，含 `sherpa-onnx-offline-tts` |
| **CPU 推理延迟** | ✅ 低 | VITS 模型 CPU 推理 RTF < 0.1（生成速度远快于实时） |
| **内存占用** | ✅ 低 | 模型约 50-100MB，运行时 < 500MB |
| **音色质量** | ⚠️ 中等 | VITS 中文音色自然度一般，Matcha-TTS 稍好 |
| **综合评价** | 🟢 **推荐** | 与 sherpa-onnx 生态无缝集成，Windows CPU 开箱即用 |

### 5.2 ChatTTS

| 维度 | 评估 | 详情 |
|------|------|------|
| **Windows 支持** | ⚠️ 有限 | 需要 PyTorch + transformers，Windows 下依赖安装较复杂 |
| **CPU 推理** | 🔴 慢 | ChatTTS 为 GPU 优化，CPU 推理极慢（生成 10s 音频需数十秒） |
| **内存占用** | 🔴 高 | 模型约 1-2GB，运行时需 4GB+ |
| **综合评价** | 🔴 **不推荐 CPU** | 不适合纯 CPU 环境 |

### 5.3 CosyVoice

| 维度 | 评估 | 详情 |
|------|------|------|
| **Windows 支持** | ⚠️ 有限 | 需 Conda 环境，依赖复杂（PyTorch, vLLM 等） |
| **CPU 推理** | 🔴 极慢 | 官方明确：CPU 推理 10-50x 慢于 GPU，需 16GB+ RAM |
| **模型大小** | 🔴 大 | CosyVoice-300M: 4-6GB VRAM; CosyVoice2-0.5B: 6-8GB VRAM |
| **综合评价** | 🔴 **不推荐 CPU** | 设计为 GPU 推理，CPU 不可行 |

### 5.4 piper TTS

| 维度 | 评估 | 详情 |
|------|------|------|
| **Windows 原生支持** | ✅ 优秀 | 提供 Windows 预编译二进制，`pip install piper-tts` |
| **CPU 推理** | ✅ 极快 | 现代桌面 CPU 上比实时快约 10x；树莓派 5 可达实时 |
| **内存占用** | ✅ 极低 | 单个语音模型 ONNX 文件数十 MB，运行时 < 200MB |
| **音色质量** | ⚠️ 中等 | VITS 架构，自然度不如 ChatTTS/CosyVoice，但可接受 |
| **中文支持** | ✅ 支持 | 提供 `zh_CN-huayan-medium` 等中文语音模型 |
| **综合评价** | 🟢 **强烈推荐** | Windows CPU 环境下最轻量、最快的本地 TTS |

### 5.5 TTS 模块总结

| 方案 | Windows兼容 | CPU延迟 | 内存 | 音色质量 | 安装难度 | 推荐度 |
|------|:--:|:--:|:--:|:--:|:--:|:--:|
| **piper TTS** | ✅ | RTF<0.1 | <200MB | 中等 | ⭐ | 🟢 首选 |
| **sherpa-onnx TTS** | ✅ | RTF<0.1 | <500MB | 中等 | ⭐ | 🟢 推荐 |
| **ChatTTS** | ⚠️ | 极慢 | 4GB+ | 优秀 | ⭐⭐⭐ | 🔴 不推荐CPU |
| **CosyVoice** | ⚠️ | 极慢 | 8GB+ | 优秀 | ⭐⭐⭐⭐ | 🔴 不推荐CPU |

---

## 6. 全栈方案评估

### 6.1 sherpa-onnx 全栈（ASR + VAD + TTS）

| 维度 | 评估 |
|------|------|
| **Windows 一键部署** | ✅ 可行。`pip install sherpa-onnx` 即可获得 ASR + VAD + TTS 全部功能 |
| **预编译二进制** | ✅ 官方提供 Windows x64 预编译包，含所有可执行文件 |
| **集成复杂度** | ⭐ 极低。统一 API，VAD+ASR 可单次调用完成 |
| **资源占用** | 总计 < 1GB RAM（SenseVoice + Silero VAD + VITS TTS） |
| **综合评价** | 🟢 **最推荐的全栈方案** |

### 6.2 LiveKit + 本地组件

| 维度 | 评估 |
|------|------|
| **Windows 部署** | ✅ 可行。LiveKit 提供 Windows 预编译二进制（GitHub Releases 下载） |
| **开发模式** | ✅ `livekit-server --dev` 一键启动 |
| **生产部署** | ⚠️ 官方推荐 Docker/Linux，Windows 仅适合开发测试 |
| **资源占用** | LiveKit 服务端本身轻量（Go 编写），但需额外运行 AI Worker |
| **综合评价** | 🟡 **适合开发测试**，生产建议 Linux |

### 6.3 Pipecat + 本地组件

| 维度 | 评估 |
|------|------|
| **Windows 兼容性** | ⚠️ 有限。Pipecat 为 Python 框架，Windows 下部分依赖（音频处理、WebRTC）可能有问题 |
| **架构特点** | 客户端-服务器架构，Python 服务端处理 AI 逻辑 |
| **依赖复杂度** | ⭐⭐⭐⭐ 高。需 Python 3.11+、uv 包管理器、多个 AI 服务 SDK |
| **综合评价** | 🟡 **不推荐 Windows 部署**。Pipecat 设计为服务端/云端运行，Windows 本地部署体验差 |

### 6.4 纯 llama.cpp 生态（whisper.cpp + llama.cpp + 本地 TTS）

| 维度 | 评估 |
|------|------|
| **Windows 部署** | ✅ 可行。whisper.cpp + llama.cpp 均有 Windows 预编译二进制 |
| **集成复杂度** | ⭐⭐⭐ 中等。需手动串联三个独立工具 |
| **资源占用** | 较高。whisper.cpp small + llama.cpp 3B Q4 + piper TTS ≈ 4-5GB RAM |
| **综合评价** | 🟡 **可行但集成工作量大**。适合已有 llama.cpp 经验的开发者 |

### 6.5 全栈方案对比

| 方案 | 部署难度 | 集成度 | 资源占用 | 生产就绪 | 推荐度 |
|------|:--:|:--:|:--:|:--:|:--:|
| **sherpa-onnx 全栈** | ⭐ | 极高 | <1GB | ✅ | 🟢 首选 |
| **llama.cpp 生态** | ⭐⭐ | 低 | 4-5GB | ⚠️ | 🟡 备选 |
| **LiveKit + 本地** | ⭐⭐ | 中 | 2-4GB | ⚠️ | 🟡 开发测试 |
| **Pipecat + 本地** | ⭐⭐⭐⭐ | 中 | 3-5GB | 🔴 | 🔴 不推荐 |

---

## 7. 云端备选方案

### 7.1 ASR 云端对比

| 服务 | 延迟 | 价格 | 中文支持 | 隐私 |
|------|------|------|:--:|:--:|
| **Deepgram Nova-3** | <300ms | $0.0043/min (~$0.26/hr) | ✅ 36+语言 | 数据经云端 |
| **Deepgram Flux** | <200ms | $0.0065/min | ✅ 多语言 | 数据经云端 |
| **Azure Speech** | 1.5-2s | $0.017/min ($1.00/hr) | ✅ | 数据经云端 |
| **OpenAI Whisper API** | N/A (非流式) | $0.006/min | ✅ | 数据经云端 |
| **阿里云 ASR** | ~1-3s | ~¥3.5/小时（实时） | ✅ 优秀 | 数据经云端 |

### 7.2 TTS 云端对比

| 服务 | 延迟 | 价格 | 音色质量 |
|------|------|------|:--:|
| **MiniMax Speech 2.6** | <250ms | $60-100/百万字符 | 🟢 优秀 |
| **Deepgram Aura-2** | <200ms | 按使用量 | 🟢 良好 |
| **Azure Speech TTS** | ~200-500ms | 按字符计费 | 🟢 优秀 |

### 7.3 本地 vs 云端决策矩阵

| 模块 | 本地首选 | 云端备选 | 切换条件 |
|------|------|------|------|
| **ASR** | sherpa-onnx + SenseVoice | Deepgram Nova-3 | 需要更高准确度或多语种 |
| **VAD** | sherpa-onnx 内置 VAD | 无需云端 | 本地已完美满足 |
| **LLM** | llama.cpp 3B-7B Q4 | OpenAI/Claude API | 需要更强推理能力 |
| **TTS** | piper TTS | MiniMax Speech 2.6 | 需要更高音色自然度 |

---

## 8. 工程代价综合对比

### 8.1 安装复杂度

| 组件 | 依赖数量 | 编译需求 | 安装命令数 | 评分 |
|------|:--:|:--:|:--:|:--:|
| sherpa-onnx | 0 | 无需 | 1 (`pip install`) | ⭐ |
| llama.cpp | 0 | 无需(预编译) | 1 (下载解压) | ⭐ |
| Ollama | 0 | 无需 | 1 (安装包) | ⭐ |
| whisper.cpp | 0 | 无需(预编译) | 1 (下载解压) | ⭐ |
| piper TTS | 0 | 无需 | 1 (`pip install`) | ⭐ |
| FunASR Python | 5+ | 无需 | 3+ | ⭐⭐⭐ |
| ChatTTS | 5+ | 无需 | 3+ | ⭐⭐⭐ |
| CosyVoice | 10+ | 可能需要 | 5+ | ⭐⭐⭐⭐ |

### 8.2 运行时资源占用（估算）

| 全栈组合 | CPU 占用 | RAM 占用 | 磁盘占用 |
|------|:--:|:--:|:--:|
| sherpa-onnx ASR+VAD+TTS | 10-20% | ~800MB | ~300MB |
| + llama.cpp 3B Q4 LLM | 30-50% | ~3GB | ~2.5GB |
| + llama.cpp 7B Q4 LLM | 50-80% | ~6GB | ~5GB |
| whisper.cpp small + llama.cpp 3B + piper | 40-60% | ~4GB | ~3GB |

### 8.3 延迟估算（端到端）

| 流水线阶段 | 本地方案 | 预计延迟 |
|------|------|:--:|
| VAD 检测 | Silero VAD | <1ms |
| ASR 转写 | SenseVoice-Small | 100-200ms |
| LLM 首 Token | llama.cpp 3B Q4 | 500-2000ms |
| LLM 生成 | llama.cpp 3B Q4 | 2-10 tok/s |
| TTS 首音频 | piper TTS | 50-100ms |
| **端到端总延迟** | | **~1-3s（不含 LLM 生成）** |

---

## 9. 推荐模块组合方案

### 🥇 方案 A：sherpa-onnx 全栈 + llama.cpp（最推荐）

```
ASR:  sherpa-onnx + SenseVoice-Small
VAD:  sherpa-onnx 内置 Silero VAD
LLM:  llama.cpp (Qwen2.5 3B Q4 / 7B Q4)
TTS:  sherpa-onnx VITS 或 piper TTS
```

| 优势 | 劣势 |
|------|------|
| 统一框架，集成度最高 | TTS 音色自然度一般 |
| 安装最简单（2个 pip install） | LLM 推理速度受限于 CPU |
| 资源占用最低（<4GB RAM） | |
| Windows 原生支持最好 | |

### 🥈 方案 B：llama.cpp 全生态

```
ASR:  whisper.cpp (small) 或 FunASR llama.cpp runtime
VAD:  Silero VAD (ONNX)
LLM:  llama.cpp (Qwen2.5 3B Q4)
TTS:  piper TTS
```

| 优势 | 劣势 |
|------|------|
| 全部 C++ 原生，性能最优 | 组件间集成需手动串联 |
| 社区最活跃 | ASR 中文效果不如 SenseVoice |
| 支持 VLM（Qwen2-VL） | |

### 🥉 方案 C：混合本地+云端

```
ASR:  sherpa-onnx + SenseVoice (本地)
VAD:  sherpa-onnx 内置 (本地)
LLM:  OpenAI/Claude API (云端) 或 llama.cpp (本地)
TTS:  MiniMax Speech API (云端) 或 piper TTS (本地)
```

| 优势 | 劣势 |
|------|------|
| LLM 推理质量最高 | 网络依赖 |
| TTS 音色最自然 | 持续成本 |
| 本地模块延迟最低 | 隐私风险 |

---

## 10. 信息来源

1. **sherpa-onnx 官方文档** — k2-fsa.github.io/sherpa/onnx — Windows 预编译二进制、ASR/TTS/VAD 支持
2. **SenseVoice vs Whisper Benchmark** — whispernotes.app — SenseVoice-Small RTF 0.015, 比 Whisper-Small 快 5x
3. **FunASR GitHub** — github.com/modelscope/FunASR — llama.cpp runtime v0.1.9 (2026-07-23), Windows CPU/AVX2 预编译包
4. **whisper.cpp 性能数据** — starwhisper.ai — Windows CPU 各模型推理时间对比
5. **whisper.cpp vs faster-whisper 2026** — promptquorum.com — RTF 对比、模型选择指南
6. **Silero VAD GitHub** — github.com/snakers4/silero-vad — <1ms 延迟, 2MB 模型, ONNX 支持
7. **WebRTC VAD GitHub** — github.com/wiseman/py-webrtcvad — Python 绑定, Windows 兼容
8. **VAD 对比评测** — picovoice.ai/blog/best-voice-activity-detection-vad — Silero vs WebRTC vs Cobra
9. **llama.cpp 预编译二进制** — github.com/ggml-org/llama.cpp/releases — Windows CPU/AVX2/Vulkan/CUDA
10. **Qwen2-VL on llama.cpp** — dev.to — GGUF 转换 + CPU 推理完整指南
11. **Ollama Windows Preview** — ollama.com/blog/windows-preview — 原生 Windows 应用, OpenAI API 兼容
12. **Piper TTS** — github.com/rhasspy/piper — ONNX 推理, Windows 预编译, CPU 实时合成
13. **CosyVoice 官方** — github.com/QwenAudio/CosyVoice — CPU 推理 10-50x 慢, 需 16GB+ RAM
14. **LiveKit 官方文档** — docs.livekit.io — Windows 预编译二进制, 本地开发模式
15. **Pipecat 官方** — github.com/pipecat-ai/pipecat — Python 框架, 服务端架构
16. **Deepgram 定价** — deepgram.com/pricing — Nova-3 $0.0043/min, Flux $0.0065/min
17. **Azure Speech 定价** — brasstranscripts.com — 实时 $1.00/hr, 批量 $0.36/hr
18. **MiniMax Speech 2.6** — minimax.io — <250ms 延迟, $60-100/百万字符
19. **AMD sherpa-onnx Windows 生产部署** — amd.com — Windows CRT 兼容性方案
20. **TEN VAD vs Silero VAD** — huggingface.co/TEN-framework/ten-vad — RTF 对比, Silero RTF 0.004-0.013

---

> **结论**: 在 Windows 10/11 纯 CPU 环境下，**sherpa-onnx 全栈（ASR+VAD+TTS）+ llama.cpp（LLM）** 是部署可行性最高、工程代价最低的组合方案。所有组件均提供 Windows 预编译二进制，无需 GPU，总内存占用可控制在 4GB 以内，端到端延迟可控制在 1-3 秒（不含 LLM 生成时间）。
