# 声学路线说话对象判定方案：基于 sherpa-onnx Speaker Embedding 生态

> **调研日期**: 2026-08-12  
> **调研目标**: 评估在 sherpa-onnx 生态内增加声学路线说话对象判定的可行性和成本  
> **当前技术栈**: sherpa-onnx (流式ASR) + Silero VAD + llama.cpp + decision token  
> **部署环境**: Windows 单机，纯 CPU 推理

---

## 目录

1. [sherpa-onnx Speaker 模型全面调研](#1-sherpa-onnx-speaker-模型全面调研)
2. [sherpa-onnx Speaker API 调研](#2-sherpa-onnx-speaker-api-调研)
3. [声学路线 Pipeline 设计](#3-声学路线-pipeline-设计)
4. [关键工程问题](#4-关键工程问题)
5. [结论与建议](#5-结论与建议)

---

## 1. sherpa-onnx Speaker 模型全面调研

### 1.1 模型生态概览

sherpa-onnx 官方支持三大类 speaker embedding 模型来源：

| 模型来源 | 说明 | 训练方 |
|---------|------|--------|
| **3D-Speaker** | 阿里达摩院开源，中文/英文/中英混合 | modelscope/3D-Speaker |
| **NeMo** | NVIDIA NeMo 工具包训练，英文为主 | NVIDIA NeMo |
| **WeSpeaker** | 西工大开源，英文为主 | WeSpeaker |

### 1.2 完整模型列表

以下是从 sherpa-onnx 官方 APK 列表和 HuggingFace 模型仓库中提取的所有可用 speaker embedding 模型：

#### 3D-Speaker 系列（中文/中英混合）

| 模型名称 | 架构 | 参数量 | Embedding 维度 | 输入采样率 | ONNX 文件大小（估算） | 语言 |
|---------|------|--------|---------------|-----------|---------------------|------|
| `3dspeaker_speech_campplus_sv_zh-cn_16k-common` | CAM++ | ~7.2M | 192 | 16kHz | ~27 MB | 中文 |
| `3dspeaker_speech_campplus_sv_en_voxceleb_16k` | CAM++ | ~7.2M | 192 | 16kHz | ~27 MB | 英文 |
| `3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k` | ERes2Net | ~6.6M | 192 | 16kHz | ~111 MB | 中文 |
| `3dspeaker_speech_eres2net_base_200k_sv_zh-cn_16k-common` | ERes2Net | ~6.6M | 192 | 16kHz | ~111 MB | 中文 |
| `3dspeaker_speech_eres2net_large_sv_zh-cn_3dspeaker_16k` | ERes2Net-Large | ~22.5M | 192 | 16kHz | ~180 MB | 中文 |
| `3dspeaker_speech_eres2net_sv_zh-cn_16k-common` | ERes2NetV2 | ~17.8M | 192 | 16kHz | ~68 MB | 中文 |
| `3dspeaker_speech_eres2net_sv_en_voxceleb_16k` | ERes2NetV2 | ~17.8M | 192 | 16kHz | ~68 MB | 英文 |

#### NeMo 系列（英文）

| 模型名称 | 架构 | 参数量 | Embedding 维度 | 输入采样率 | ONNX 文件大小（估算） | 语言 |
|---------|------|--------|---------------|-----------|---------------------|------|
| `nemo_en_titanet_small` | TitaNet-Small | ~6M | 192 | 16kHz | ~34 MB | 英文 |
| `nemo_en_titanet_large` | TitaNet-Large | ~23M | 192 | 16kHz | ~97 MB | 英文 |
| `nemo_en_speakerverification_speakernet` | SpeakerNet | ~5M | 192 | 16kHz | ~22 MB | 英文 |

#### WeSpeaker 系列（英文）

| 模型名称 | 架构 | 参数量 | Embedding 维度 | 输入采样率 | ONNX 文件大小（估算） | 语言 |
|---------|------|--------|---------------|-----------|---------------------|------|
| `wespeaker_voxceleb_resnet152_LM` | ResNet152 | ~60M | 256 | 16kHz | ~230 MB | 英文 |
| `wespeaker_voxceleb_resnet221_LM` | ResNet221 | ~100M | 256 | 16kHz | ~380 MB | 英文 |
| `wespeaker_voxceleb_resnet34_LM` | ResNet34 | ~6.3M | 256 | 16kHz | ~25 MB | 英文 |
| `wespeaker_voxceleb_ecapa_tdnn_LM` | ECAPA-TDNN | ~14.7M | 192 | 16kHz | ~56 MB | 英文 |

> **来源**: sherpa-onnx 官方 APK 列表: https://k2-fsa.github.io/sherpa/onnx/speaker-identification/apk.html  
> **来源**: HuggingFace 模型仓库: https://huggingface.co/csukuangfj/speaker-embedding-models  
> **来源**: CAM++ ONNX 模型详情: https://huggingface.co/welcomyou/campplus-3dspeaker-200k-onnx

### 1.3 模型精度对比

基于 3D-Speaker 论文和 CAM++ 论文的 EER（Equal Error Rate）数据：

| 模型 | 参数量 | FLOPs (3s) | VoxCeleb1-O EER | CN-Celeb EER | 3D-Speaker EER |
|------|--------|-----------|-----------------|-------------|----------------|
| **ERes2NetV2** | 17.8M | 12.6G | **0.61%** | 6.14% | **6.52%** |
| **ERes2Net-Large** | 22.5M | 20.4G | **0.52%** | 6.17% | **6.34%** |
| **CAM++** | 7.2M | 1.72G | 0.65% | 6.78% | 7.75% |
| **ERes2Net-Base** | 6.6M | 5.16G | 0.84% | 6.69% | 7.21% |
| **ECAPA-TDNN** | 14.7M | 5.64G | 0.89% | 7.45% | 8.87% |
| **ResNet34** | 6.3M | 2.65G | 1.05% | 6.92% | 7.29% |

> **来源**: 3D-Speaker-Toolkit 论文: https://arxiv.org/html/2403.19971v3  
> **来源**: CAM++ 论文: https://arxiv.org/html/2303.00332v3  
> **来源**: ERes2NetV2 论文: https://www.isca-archive.org/interspeech_2024/chen24l_interspeech.pdf

### 1.4 CPU 性能数据（RTF）

从 sherpa-onnx GitHub Discussions 中获取的实测数据：

| 模型配置 | RTF (CPU) | 相对速度 | 说明 |
|---------|-----------|---------|------|
| **NeMo TitaNet (int8)** | ~0.110 | **最快 (基准)** | 推荐用于实时场景 |
| **3D-Speaker ERes2Net-Base (int8)** | ~0.241 | 2.2x 慢于 TitaNet | 中文场景首选 |
| **3D-Speaker CAM++ (int8)** | ~0.15-0.18 | 1.4-1.6x 慢于 TitaNet | 速度与精度平衡 |

> **来源**: sherpa-onnx Discussion #3233: https://github.com/k2-fsa/sherpa-onnx/discussions/3233  
> 实测环境: Linux, 21.2分钟音频, CPU 推理。3DSpeaker + int8 RTF ≈ 0.241，NeMo TitaNet RTF ≈ 0.110（约 2.7x 更快）。

**关键发现**：
- RTF 0.241 意味着处理 1 秒音频需要 0.241 秒 CPU 时间
- 对于实时场景（如 2 秒的 VAD 语音段），embedding 提取仅需约 0.24-0.48 秒
- **int8 量化模型是实时场景的必备条件**

---

## 2. sherpa-onnx Speaker API 调研

### 2.1 核心 API 组件

sherpa-onnx 提供两个核心 speaker 组件：

#### SpeakerEmbeddingExtractor（Embedding 提取器）

**C API**（`sherpa-onnx/c-api/c-api.h`）:
```c
// 创建提取器
SherpaOnnxSpeakerEmbeddingExtractor* SherpaOnnxCreateSpeakerEmbeddingExtractor(
    const char* model_path, int32_t num_threads, int32_t debug
);

// 创建流
SherpaOnnxOnlineStream* SherpaOnnxSpeakerEmbeddingExtractorCreateStream(
    SherpaOnnxSpeakerEmbeddingExtractor* extractor
);

// 送入音频
void SherpaOnnxOnlineStreamAcceptWaveform(
    SherpaOnnxOnlineStream* stream, int32_t sample_rate, 
    const float* samples, int32_t num_samples
);

// 标记输入结束
void SherpaOnnxOnlineStreamInputFinished(SherpaOnnxOnlineStream* stream);

// 检查是否就绪（音频足够长）
int32_t SherpaOnnxSpeakerEmbeddingExtractorIsReady(
    SherpaOnnxSpeakerEmbeddingExtractor* extractor,
    SherpaOnnxOnlineStream* stream
);

// 计算 embedding
const float* SherpaOnnxSpeakerEmbeddingExtractorCompute(
    SherpaOnnxSpeakerEmbeddingExtractor* extractor,
    SherpaOnnxOnlineStream* stream,
    int32_t* dim  // 输出维度
);

// 获取 embedding 维度
int32_t SherpaOnnxSpeakerEmbeddingExtractorDim(
    SherpaOnnxSpeakerEmbeddingExtractor* extractor
);

// 销毁
void SherpaOnnxDestroySpeakerEmbeddingExtractor(
    SherpaOnnxSpeakerEmbeddingExtractor* extractor
);
```

#### SpeakerEmbeddingManager（Embedding 管理器）

```c
// 创建管理器
SherpaOnnxSpeakerEmbeddingManager* SherpaOnnxCreateSpeakerEmbeddingManager(
    int32_t dim
);

// 注册说话人（支持多次注册同一说话人，自动平均）
int32_t SherpaOnnxSpeakerEmbeddingManagerRegister(
    SherpaOnnxSpeakerEmbeddingManager* manager,
    const char* name,
    const float* embedding
);

// 搜索最佳匹配（返回说话人名字，低于阈值返回 NULL）
const char* SherpaOnnxSpeakerEmbeddingManagerSearch(
    SherpaOnnxSpeakerEmbeddingManager* manager,
    const float* embedding,
    float threshold  // cosine similarity 阈值
);

// 验证两个 embedding 是否来自同一说话人
int32_t SherpaOnnxSpeakerEmbeddingManagerVerify(
    SherpaOnnxSpeakerEmbeddingManager* manager,
    const float* embedding1,
    const float* embedding2,
    float threshold
);

// 检查是否已注册
int32_t SherpaOnnxSpeakerEmbeddingManagerContains(
    SherpaOnnxSpeakerEmbeddingManager* manager,
    const char* name
);

// 获取所有已注册说话人
const char* const* SherpaOnnxSpeakerEmbeddingManagerGetAllSpeakers(
    SherpaOnnxSpeakerEmbeddingManager* manager
);

// 获取注册数量
int32_t SherpaOnnxSpeakerEmbeddingManagerNumSpeakers(
    SherpaOnnxSpeakerEmbeddingManager* manager
);

// 销毁
void SherpaOnnxDestroySpeakerEmbeddingManager(
    SherpaOnnxSpeakerEmbeddingManager* manager
);
```

> **来源**: sherpa-onnx C API 头文件: https://github.com/k2-fsa/sherpa-onnx/blob/master/sherpa-onnx/c-api/c-api.h  
> **来源**: C API 示例代码: https://dev.modelhub.org.cn/EngineX-Iluvatar/enginex_bi_series-sherpa-onnx/src/commit/97654122fa42ec7d029f7722327ee3381d45e5e3/c-api-examples/speaker-identification-c-api.c

**Python API**（`speaker-identification.py`）:
```python
import sherpa_onnx

# 创建提取器
config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
    model='3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx',
    num_threads=1,
    debug=False,
)
extractor = sherpa_onnx.SpeakerEmbeddingExtractor(config)

# 创建管理器
manager = sherpa_onnx.SpeakerEmbeddingManager(extractor.dim)

# 注册说话人
manager.register(name, embedding)

# 搜索匹配
name = manager.search(embedding, threshold=0.6)
```

> **来源**: Python 示例: https://github.com/k2-fsa/sherpa-onnx/blob/master/python-api-examples/speaker-identification.py

### 2.2 流式 vs 离线

| 特性 | 说明 |
|------|------|
| **流式提取** | ✅ 支持。通过 `OnlineStream` 逐步送入音频，`AcceptWaveform` 可多次调用 |
| **离线提取** | ✅ 支持。一次性送入完整音频后调用 `InputFinished` + `Compute` |
| **实时场景适配** | ✅ 流式 API 天然适配 VAD 分段后的实时处理 |

**关键 API 特性**：
- `IsReady()` 方法：检查送入的音频是否足够长以提取有效 embedding
- 如果音频太短，`IsReady()` 返回 false，`Compute()` 会失败
- 支持多次 `AcceptWaveform` 后一次性 `Compute`

### 2.3 注册（Enrollment）流程

```
1. 用户说一段注册语音 → 保存为 WAV 文件
2. 创建 SpeakerEmbeddingExtractor
3. 创建 OnlineStream → AcceptWaveform(注册音频) → InputFinished()
4. 检查 IsReady() → Compute() → 获得 embedding (float32 数组)
5. SpeakerEmbeddingManager.Register("target_speaker", embedding)
6. 可选：多次注册同一说话人，Manager 自动对 embedding 取平均
```

### 2.4 比对（Verification）流程

```
1. 新语音段 → 提取 embedding
2. Manager.Search(embedding, threshold) → 返回说话人名字或 NULL
3. 内部使用 cosine similarity 进行比对
```

**距离度量**：
- 默认使用 **cosine similarity**（L2 归一化后的内积）
- 所有 3D-Speaker 模型输出 L2-normalized embedding
- 不支持 PLDA（sherpa-onnx 未集成 PLDA 后端）

---

## 3. 声学路线 Pipeline 设计

### 3.1 整体架构

```
┌─────────────────────────────────────────────────────────────────┐
│                    声学路线 Pipeline                              │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────┐    ┌──────────┐    ┌───────────────┐              │
│  │ 麦克风   │───▶│ Silero   │───▶│ Speaker       │              │
│  │ 音频流   │    │ VAD      │    │ Embedding     │              │
│  │          │    │ (已有)   │    │ Extractor     │              │
│  └──────────┘    └──────────┘    └───────┬───────┘              │
│                                          │                       │
│                                          ▼                       │
│                              ┌───────────────────────┐          │
│                              │ SpeakerEmbedding      │          │
│                              │ Manager               │          │
│                              │                       │          │
│                              │ Search(emb, threshold)│          │
│                              └───────────┬───────────┘          │
│                                          │                       │
│                    ┌─────────────────────┼──────────────┐       │
│                    ▼                     ▼              ▼       │
│              "target_user"          "unknown"        NULL       │
│              → 是目标说话人         → 非目标         → 低于阈值 │
│                    │                     │              │       │
│                    ▼                     ▼              ▼       │
│              送入 ASR + LLM        丢弃/忽略        丢弃/忽略   │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 3.2 注册阶段（一次性）

```
输入：用户说一段 3-5 秒的注册语音（如"你好，我是XX"）
处理：
  1. 保存为 16kHz mono WAV
  2. SpeakerEmbeddingExtractor 提取 embedding (192 维 float32)
  3. SpeakerEmbeddingManager.Register("target_user", embedding)
  4. 可选：重复 2-3 次，Manager 自动平均
输出：已注册的目标说话人 embedding
```

### 3.3 推理阶段（每段 VAD 语音）

```
输入：Silero VAD 检测到的一段语音（通常 0.5-5 秒）
处理：
  1. SpeakerEmbeddingExtractor 提取 embedding
  2. Manager.Search(embedding, threshold)
  3. 如果返回 "target_user" → 是目标说话人 → 送入 ASR + LLM
  4. 如果返回 NULL/其他 → 非目标说话人 → 丢弃
输出：是/否目标说话人
```

### 3.4 端到端延迟评估

| 阶段 | 延迟（估算） | 说明 |
|------|------------|------|
| Silero VAD 分段 | ~20-50ms | 已有 pipeline，几乎无延迟 |
| Embedding 提取 (CAM++, 2s 语音) | ~300-360ms | RTF 0.15-0.18 × 2s |
| Embedding 提取 (ERes2Net-Base, 2s 语音) | ~480ms | RTF 0.24 × 2s |
| Cosine Similarity 比对 | <1ms | 192 维向量内积，可忽略 |
| **总延迟（CAM++）** | **~320-410ms** | VAD + Embedding + 比对 |
| **总延迟（ERes2Net-Base）** | **~500-530ms** | VAD + Embedding + 比对 |

> **关键洞察**：对于陪伴型语音代理，"用户说完话 → 系统判定是否为本人 → 决定是否回复"的端到端延迟在 300-500ms 是可接受的。这个延迟远小于 LLM 推理延迟（通常 1-3 秒），不会成为瓶颈。

### 3.5 CPU 增量评估

| 组件 | 当前 CPU 占用（估算） | 增加 Speaker Embedding 后 |
|------|---------------------|--------------------------|
| Silero VAD | ~5-10% | 不变 |
| sherpa-onnx ASR (SenseVoice/Zipformer) | ~20-40% | 不变 |
| llama.cpp (LLM 推理) | ~30-60% | 不变 |
| **Speaker Embedding (CAM++)** | 0% | **+5-10%**（仅在 VAD 分段时触发） |
| **Speaker Embedding (ERes2Net-Base)** | 0% | **+10-15%**（仅在 VAD 分段时触发） |

**关键分析**：
- Speaker embedding 提取是**间歇性**的（仅在 VAD 检测到语音段时触发），不是持续运行
- 对于典型的对话场景（用户每 5-30 秒说一句话），embedding 提取的 CPU 时间占比极低
- 使用 CAM++ 模型（27MB，7.2M 参数），内存增量约 30-50MB
- **总体 CPU 增量可忽略不计**（<5% 平均负载）

---

## 4. 关键工程问题

### 4.1 注册语音的最短时长要求

| 模型 | 最短时长（官方） | 推荐时长 | 说明 |
|------|----------------|---------|------|
| CAM++ | ~0.5s | 2-3s | 论文显示短语音性能下降明显 |
| ERes2Net-Base | ~0.5s | 2-3s | ERes2NetV2 专门优化了短时语音 |
| ERes2NetV2 | ~0.3s | 1-2s | 短时语音 SOTA |

> **来源**: ERes2NetV2 论文标题即为 "Boosting Short-Duration Speaker Verification"

**能否用唤醒词音频做注册？**
- ⚠️ **不推荐**。唤醒词通常只有 0.3-0.8 秒，太短导致 embedding 质量差
- ✅ **建议**：让用户说一句完整的注册语（如"你好，我是你的语音助手用户"），约 2-3 秒
- 可以多次注册（如说 3 遍），Manager 自动平均，显著提升稳定性

### 4.2 Embedding 的时效性

| 因素 | 影响 | 建议 |
|------|------|------|
| **短期（同一天）** | 极小 | 无需更新 |
| **中期（数周-数月）** | 轻微 | 情绪波动（感冒、疲劳）可能影响 embedding |
| **长期（数年）** | 中等 | 年龄变化、声道变化 |
| **健康状况** | 中等 | 感冒/鼻塞会显著改变声道特征 |

**建议策略**：
- 初始注册时采集 2-3 段语音，取平均 embedding
- 每次成功识别后，用最新 embedding 做**滑动平均更新**（EMA, α=0.1-0.2）
- 如果连续多次识别失败（可能是感冒），触发重新注册提示

### 4.3 阈值设定策略

**核心原则**：在"宁可漏、不可乱插"的原则下，阈值应偏向**高精度（低误判）**。

| 阈值 | False Accept（误判） | False Reject（漏判） | 适用场景 |
|------|---------------------|---------------------|---------|
| 0.3 | 高 | 极低 | 不推荐 |
| 0.4 | 中 | 低 | 宽松场景 |
| **0.5** | **低** | **中** | **sherpa-onnx 默认值** |
| **0.6** | **很低** | **中高** | **Python 示例默认值** |
| 0.7 | 极低 | 高 | 严格场景 |
| 0.8 | 几乎为零 | 很高 | 极高安全要求 |

**推荐策略**：
1. **初始阈值设为 0.6**（Python 示例的默认值）
2. 在实际使用中收集数据，绘制 ROC 曲线
3. 根据用户体验调整：如果用户抱怨"经常不回复"，降低到 0.55；如果"经常误回复"，提高到 0.65
4. **建议最终范围：0.55-0.65**

> **来源**: Python speaker-identification.py 默认 threshold=0.6: https://github.com/k2-fsa/sherpa-onnx/blob/master/python-api-examples/speaker-identification.py  
> **来源**: C# 示例使用 threshold=0.5: https://github.com/k2-fsa/sherpa-onnx/issues/974

### 4.4 多说话人场景

**场景**：房间里有两个人（用户本人 + 其他人）

| 策略 | 描述 | 优缺点 |
|------|------|--------|
| **单目标注册** | 只注册用户本人 | ✅ 简单，✅ 符合"宁可漏"原则 |
| **多目标注册** | 注册用户 + 家人/同事 | ✅ 可区分多人，❌ 增加误判风险 |
| **白名单模式** | 注册所有允许的人 | ✅ 灵活，❌ 管理复杂 |

**推荐方案**：
- **初期只注册一个目标说话人**（用户本人）
- 非目标说话人的语音 → Manager.Search 返回 NULL → 丢弃
- 如果未来需要支持多人，Manager 原生支持多说话人注册和搜索

### 4.5 与现有 Pipeline 的集成点

```
现有 Pipeline:
  麦克风 → Silero VAD → ASR → decision token → LLM

集成后 Pipeline:
  麦克风 → Silero VAD → Speaker Embedding → [是目标?]
                              │                    │
                              │ YES                │ NO
                              ▼                    ▼
                            ASR → ...           丢弃
```

**集成要点**：
1. VAD 检测到语音段结束 → 立即触发 embedding 提取（在 ASR 之前或并行）
2. 如果判定为非目标说话人 → 跳过 ASR 和 LLM，节省 CPU
3. 如果判定为目标说话人 → 正常走 ASR + LLM 流程
4. **Speaker embedding 提取和 ASR 可以并行执行**（不同模型，不同 ONNX Runtime 实例）

---

## 5. 结论与建议

### 5.1 可行性结论

| 维度 | 评估 | 说明 |
|------|------|------|
| **技术可行性** | ✅ 完全可行 | sherpa-onnx 原生支持 speaker identification |
| **API 成熟度** | ✅ 成熟 | C/C++/Python/Go 等多语言 API |
| **模型可用性** | ✅ 丰富 | 3D-Speaker/NeMo/WeSpeaker 三大系列 |
| **CPU 增量** | ✅ 可忽略 | <5% 平均负载，间歇性触发 |
| **延迟增量** | ✅ 可接受 | 300-500ms，远小于 LLM 推理延迟 |
| **内存增量** | ✅ 可接受 | 30-50MB（CAM++ 模型） |

### 5.2 推荐模型

**首选：`3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx`**

| 维度 | 评分 | 说明 |
|------|------|------|
| 中文支持 | ⭐⭐⭐⭐⭐ | 专门训练中文数据 |
| 模型大小 | ⭐⭐⭐⭐⭐ | 仅 27MB |
| CPU 速度 | ⭐⭐⭐⭐ | RTF ~0.15-0.18 |
| 精度 | ⭐⭐⭐⭐ | EER 0.65% (VoxCeleb) |
| 参数量 | ⭐⭐⭐⭐⭐ | 7.2M，轻量级 |

**备选：`3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx`**
- 精度更高（EER 0.84%），但模型更大（111MB），速度更慢（RTF ~0.24）

### 5.3 实施路线图

```
Phase 1: 原型验证（1-2 天）
  - 下载 CAM++ 模型
  - 编写 Python 原型：注册 + 验证
  - 测试延迟和 CPU 占用

Phase 2: C++ 集成（2-3 天）
  - 将 SpeakerEmbeddingExtractor 集成到现有 C++ pipeline
  - 在 VAD 分段后插入 embedding 提取
  - 实现阈值判定逻辑

Phase 3: 调优（1-2 天）
  - 收集实际使用数据
  - 调整阈值
  - 实现 embedding 滑动平均更新
```

### 5.4 风险与注意事项

1. **短语音问题**：VAD 分段可能产生 <0.5s 的短语音段，`IsReady()` 会返回 false。需要设置 VAD 的 `min_speech_duration` 至少 0.5s。
2. **环境噪声**：高噪声环境下 embedding 质量下降。建议在注册时也采集带噪声的样本。
3. **模型下载**：模型托管在 HuggingFace (`csukuangfj/speaker-embedding-models`)，国内可能需要镜像。
4. **ONNX Runtime 版本**：确保 ONNX Runtime 版本与 sherpa-onnx 兼容（推荐 1.17+）。

---

## 参考来源

1. sherpa-onnx GitHub 仓库: https://github.com/k2-fsa/sherpa-onnx
2. sherpa-onnx 官方文档 - Speaker Identification: https://k2-fsa.github.io/sherpa/onnx/speaker-identification/index.html
3. sherpa-onnx Speaker ID APK 列表: https://k2-fsa.github.io/sherpa/onnx/speaker-identification/apk.html
4. sherpa-onnx C API 头文件: https://github.com/k2-fsa/sherpa-onnx/blob/master/sherpa-onnx/c-api/c-api.h
5. Python speaker-identification.py 示例: https://github.com/k2-fsa/sherpa-onnx/blob/master/python-api-examples/speaker-identification.py
6. C API speaker-identification 示例: https://dev.modelhub.org.cn/EngineX-Iluvatar/enginex_bi_series-sherpa-onnx/src/commit/97654122fa42ec7d029f7722327ee3381d45e5e3/c-api-examples/speaker-identification-c-api.c
7. HuggingFace speaker-embedding-models: https://huggingface.co/csukuangfj/speaker-embedding-models
8. CAM++ ONNX 模型详情: https://huggingface.co/welcomyou/campplus-3dspeaker-200k-onnx
9. sherpa-onnx Discussion #3233 (RTF benchmark): https://github.com/k2-fsa/sherpa-onnx/discussions/3233
10. 3D-Speaker-Toolkit 论文: https://arxiv.org/html/2403.19971v3
11. CAM++ 论文: https://arxiv.org/html/2303.00332v3
12. ERes2NetV2 论文: https://www.isca-archive.org/interspeech_2024/chen24l_interspeech.pdf
13. 3D-Speaker GitHub: https://github.com/modelscope/3D-Speaker
14. sherpa-onnx Go API 文档: https://pkg.go.dev/github.com/k2-fsa/sherpa-onnx/scripts/go
15. sherpa-onnx Speaker Diarization 文档: https://k2-fsa.github.io/sherpa/onnx/speaker-diarization/index.html
16. NVIDIA TitaNet-Large: https://huggingface.co/nvidia/speakerverification_en_titanet_large
17. sherpa-onnx NeMo 模型文档: https://k2-fsa.github.io/sherpa/onnx/nemo/index.html
18. sherpa-onnx CHANGELOG: https://github.com/k2-fsa/sherpa-onnx/blob/master/CHANGELOG.md
