# Spec Draft：Addressee Detection（说话对象判定）落地方案

> 生命周期: **草稿**（2026-08-12 20:5x）——交叉验证完成后首版，走 草稿→验证→替换→整合 流程
> 上游: 云端调研 `final_report_addressee_detection.md`（12 份）+ 交叉验证 `doc/research/addressee-cross-validation-2026-08-12.md`
> 用户授权（20:4x/20:5x）：**效果优先，大改不怕，云端兜底，硬件不设限**；**ADR0006 可演进**（旧框架不是不可改，新功能走 spec 记录+后续整合流程）
> 原则: 宁可漏、不可乱插（误响应代价 > 漏判代价）；分阶段独立验收/回滚；env 闸门默认关（fail-open 传统）

---

## §1 目标与范围

- **目标**：让 live 模式区分"用户在跟 AI 说话" vs "自言自语/与旁人交谈/环境声"，只对面向 AI 的语音做 ASR→LLM→TTS，**消灭误响应（AI 乱插话）**。
- **范围**：live_mode.py 链路（VAD→ASR 之间插 AddresseeDetector 门控）+ live_mode 层注册流程（ENROLL 模式态）；**不动** turn_controller 13 态核心（门控在关键转换点加条件）。
- **Phase 1（本轮）**：声学预筛——CAM++ speaker embedding + 注册-比对，非目标说话人语音在 ASR 前丢弃（省 ASR/LLM 计算 + 控部分误响应）。
- **Phase 2（后续）**：四态 decision token（+not-for-me）+ 融合决策层（声学×0.4 + 语义×0.6）——**ADR0006 随此演进**（单入口语义扩展，走 spec→验证→替换→整合）。

## §2 部署真值与技术前提（交叉验证实证）

| 项 | 事实 |
|---|---|
| sherpa-onnx 版本 | **1.13.4，原生支持 Speaker API**（SpeakerEmbeddingExtractor/Manager/OfflineSpeakerDiarization）——无需升级 |
| 模型 | `3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx`（27MB/7.2M 参数/192 维/RTF 0.15-0.18，本地需下载） |
| 延迟/资源 | 声学增量 300-400ms（与 ASR 并行，不增串行）；CPU 瞬时 +5-10%；内存 +30-50MB |
| 插入点 | live_mode.py `feed_audio(pcm)`：VAD 分段后 → AddresseeDetector → （通过）ASR / （丢弃）不送 ASR |
| LLM 部署 | GPU（-ngl 999）——语义判定（Phase2）TTFT 200-500ms 级 |

## §3 Phase 1：声学预筛（本轮实施）

### 3.1 模块设计
- 新建 `services/webui/src/joy_interaction_webui/addressee_detector.py`：
  - `AddresseeDetector`：包装 sherpa-onnx `SpeakerEmbeddingExtractor`（模型路径可配）+ `SpeakerEmbeddingManager`（注册库，内存态 + 可选落盘）。
  - 方法：`register(embedding)` / `is_enrolled() -> bool` / `classify(pcm_segment) -> (is_target: bool, score: float)`——cosine 相似度比对，阈值 `target_threshold`（默认 0.6，建议 0.55-0.65 可配，宁漏不乱插偏高）。
  - `extract(pcm_segment) -> np.ndarray | None`：embedding 提取（段太短 IsReady=false → 返回 None，fail-open 放行）。
- 注册流程：`enroll(pcm_segments, n=2..3)`——2-3 段 2-3s 注册语取平均；增量更新 EMA（α=0.1-0.2）。

### 3.2 live_mode 集成
- `LiveStateMachine` 新增状态分支（**live_mode 层，不进 turn_controller 核心**）：
  - `enroll_phase` 标志 + 前端 `/api/live/enroll` 端点（录制注册语 → 提取 embedding → 注册 → 完成提示）。
  - `feed_audio` 链路：VAD 检测到语音段后 → `AddresseeDetector.classify(segment)`：
    - `is_target=True` → 照常进 ASR（现有路径零改动）；
    - `is_target=False` → **丢弃该段**（不打 ASR、不进 turn_controller），日志 `[addressee] non-target dropped (score=..)`；
    - 未注册/无 embedding/异常 → **fail-open 放行**（保持现状行为）。
- env 闸门：`JARVIS_ADDRESSEE_DETECTOR_ENABLED`（默认 false）——开启才实例化/过滤，默认零行为变化。

### 3.3 实测项（交叉验证必测 ①，Phase1 内完成）
- 下载 CAM++ onnx 后，跑本地实测：RTF / 2s 段提取延迟 / 中文桌面场景（用户声 + 旁人声样本）区分度——数据记入 spec 附录或研究报告，验证 300-400ms 与 <5-10% CPU 假设。

### 3.4 验收
- 注册流程：录制 2-3s 语 → 注册成功 → 状态提示。
- 过滤：旁人语音（未注册声音）→ 日志 `non-target dropped`，无 ASR/LLM/回复；本人语音 → 正常回复。
- 闸门：默认关 → 行为与现状完全一致（回归验证）。
- jarvis/live 双模式回归不受影响（live 独立链路）。

## §4 Phase 2：四态 decision token + 融合（后续，另行 spec 细化）

- decision token 三态 → 四态（+`not-for-me`）：prompt_constants 教学 + parse_model_decision + turn_streaming 消费侧 + GBNF grammar。**ADR0006 随此演进**（单入口语义扩展，走 spec→验证→替换→整合）。
- 融合决策层：`S_fusion = 0.4 × S_acoustic + 0.6 × S_semantic`，双阈值 T_low 0.3 / T_high 0.7（声学低于 0.3 直接判非目标，高于 0.7 直接放行，中间走融合）。
- 本地 llama.cpp not-for-me 准确率实测（必测 ②）+ 数据采集后权重网格搜索。
- 待 Phase1 验收 + 实测数据回填后启动。

## §5 风险与回滚

| 风险 | 缓解 |
|---|---|
| CAM++ 中文桌面区分度不足 | Phase1 实测先行（§3.3）；不足换 ERes2NetV2（68MB/17.8M/短时 SOTA） |
| 注册流程误注册/被绕过 | 2-3 段取平均 + EMA 更新；连续识别失败触发重注册提示 |
| 误杀真实唤醒/对话（宁漏不乱插 vs 别漏） | 阈值可配（0.55-0.65）；未注册 fail-open；env 闸门默认关 |
| 改动 live 链路回归 | 门控只拦"非目标"段，目标段路径零改动；jarvis 独立不受影响；QA 全量回归 |
| ADR0006 演进风险（Phase2） | 单入口不变、仅扩语义；spec→验证→替换→整合流程留痕 |

## §6 执行顺序（Phase 1）

1. **实测**：下载 CAM++ → 本地 RTF/延迟/区分度基准（产出数据，验证假设）；
2. **实现**：addressee_detector.py + live_mode 集成（ENROLL 分支 + 门控）+ /api/live/enroll 端点 + env 闸门；
3. **测试**：单测（mock embedding/注册/过滤/fail-open/闸门）+ 回归；
4. **QA**：独立回归；
5. **真机**：你注册声音 → 旁人说话不响应、你说话正常响应。

## §7 关联

- `draft-live-interaction-layer.md`（live 主草稿，本方案是其 §3.3 的落地）
- `doc/research/addressee-cross-validation-2026-08-12.md`（交叉验证，本 spec 依据）
- ADR0006（llm-gateway 单入口——Phase2 随四态演进，本 spec 记录演进意向）
- 部署真值：run-windows.ps1:372（-ngl 999 GPU）
