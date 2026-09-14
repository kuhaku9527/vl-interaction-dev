# Spec：Addressee Detection（说话对象判定）落地方案

> 生命周期: **正式**（2026-08-13 定稿，走 草稿→设计评审→实现→QA→真机 流程完成）
> 上游: 云端调研 [`doc/research/addressee-detection-2026-08-12/`](research/addressee-detection-2026-08-12/)（终稿 `final_report_addressee_detection.md` + 4 份支撑，共 5 份）+ 交叉验证 `doc/research/addressee-cross-validation-2026-08-12.md`
> 用户授权（20:4x/20:5x）：**效果优先，大改不怕，云端兜底，硬件不设限**；**ADR0006 可演进**（旧框架不是不可改，新功能走 spec 记录+后续整合流程）
> 实现: 2b10c0c（Phase1：CAM++ 声学预筛 detector + 本地基准）→ ce641fc（live_mode 门控 + enroll 端点 + 前端）→ 80cffce（Phase2：四态 decision token `+not-for-me` 六层）
> 验证: QA PASS（webui `test_addressee_detector` / `test_addressee_qa_edges` + webinfer 四态 `test_decision_notforme` / `test_qa_4state_supplement` 回归）
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

- decision token 三态 → 四态（+`not-for-me`）：prompt_constants 教学 + parse_model_decision + turn_streaming 消费侧（**注意：本地 webinfer 未用 GBNF grammar**——llama.cpp 侧是 prompt 教学 + 自由生成 + 解析，四态扩展无需 grammar，报告假设不适用本地，已实证）。**ADR0006 随此演进**（单入口语义扩展，走 spec→验证→替换→整合）。
- 融合决策层：`S_fusion = 0.4 × S_acoustic + 0.6 × S_semantic`，双阈值 T_low 0.3 / T_high 0.7（声学低于 0.3 直接判非目标，高于 0.7 直接放行，中间走融合）。
- 本地 llama.cpp not-for-me 准确率实测（必测 ②）+ 数据采集后权重网格搜索。
- 待 Phase1 验收 + 实测数据回填后启动。

### 4.1 四态定义与语义（Phase2 核心）

| token | 语义 | 行为 | 与三态差异 |
|---|---|---|---|
| `</silence>` | 用户在跟我说话，但无需回复 | 不播报，回 LISTENING | 原有 |
| `</response>` | 正常回复 | 播报 | 原有 |
| `</delegation>` | 转后台检索 | 后台触发，不念问题 | 原有 |
| `</not-for-me>` | **非面向 AI**（自言自语/对旁人） | **不播报，回 LISTENING，且不把该轮计入对话历史**（可选） | **新增** |

判定规则（prompt 教学，来源调研断言 44）：
- 话语含对 AI 的称呼（"嘿""喂"）→ 大概率对 AI 说；
- 明确提问/指令且未指定其他对象 → 默认对 AI 说；
- 明显回应旁人的话 / 无信息意图的自言自语 → `</not-for-me>`。

### 4.2 实现分层（改动面）

1. **prompt_constants.py**：LIVE_SYSTEM_PROMPT 加四态教学（Not-For-Me 定义 + 判定规则 + 2-4 个边界 few-shot 示例）；NO_DECISION 提示词同步说明。
2. **response_format.py / infer_loop.py**：`parse_model_decision` + 流式帧协议支持 `not-for-me`（标记识别 + 帧类型）；`normalize_model_output` 的 marker 集合扩展。
3. **turn_streaming.py**：`StreamingTurnConsumer` 消费 `not-for-me` 帧——不触发 on_sentence（零 TTS）、decision 返回 not-for-me；`StreamingTurnResult.decision` 新增值。
4. **live_mode.py `_finish_llm_turn`**：`decision == "not-for-me"` → 不播报、controller 回 LISTENING（与 silence 同路径，但可加独立日志 `[addressee] semantic not-for-me` 区分）；jarvis 路径（jarvis 预设）默认不受影响（四态教学仅 live prompt，jarvis 仍三态——**决策**：四态教学加在 live 专用 prompt，jarvis 保持三态，避免 jarvis 行为变化）。
5. **融合决策层（live_mode 门控增强）**：声学（AddresseeDetector.classify 分数）+ 语义（LLM decision）融合：
   - 声学已拦（<0.3 直接 drop，Phase1 已有）→ 融合层只处理"声学放行但语义判 not-for-me"（目标说话人自言自语）；
   - `decision == "not-for-me"` 且声学分数 ∈ (0.3, 0.7) 模糊区 → 融合判非目标；声学 ≥0.7 且语义 response → 目标放行。
   - 简化落地：**本轮融合 = 语义 not-for-me 与声学分数加权**，权重 0.4/0.6 可配（env），先实现后调参。
   - **落地决策（2026-08-12 实施标注）**：本轮**不实现 0.4/0.6 权重融合/双阈值**——live 门控的"非目标"判定 = 声学已拦（Phase1）∪ 语义 not-for-me（本层直判不播报）；消费侧把 **not-for-me ∪ silence 同计为"不播报"**（交叉验证 B.7.2：not-for-me token 召回低，模型爱用空输出/silence，语义信号按并集计）。权重融合留待数据采集后网格调参。
6. **前端**：not-for-me 轮的 UI（无新播报，字幕可不显示或显示灰色"（未面向 AI，已忽略）"——按最小实现：不显示）。

### 4.3 必测 ②：本地 llama.cpp 四态准确率实测（实施前先做）

- 设计小型测试集（30-50 句）：面向 AI（提问/指令/称呼）vs 非面向（自言自语/回应旁人/无信息意图），中英混合；
- 用现有 webinfer（interaction_mode="live" + 四态 prompt 临时版）或 llama-server 直测：统计四态输出的**准确率/混淆**（尤其 not-for-me 的精确率+召回率）；
- 判据：not-for-me 精确率 ≥80%（宁可漏不可乱插：误判 not-for-me 可接受，漏判 not-for-me 不可接受）；若不足 → 调 prompt few-shot 再测；
- 数据记入交叉验证报告附录 B。

### 4.4 ADR0006 演进记录

- ADR0006（llm-gateway 单入口）**不违反**：四态是同一入口内语义扩展，路由/网关结构不变；
- 演进动作：spec 记录 → 验证（必测②+QA）→ 替换（更新 ADR0006 文本中的决策 token 三态描述为四态，标注演进日期）→ 整合（用户后续统一整合 spec+adr+决策文档）。

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

- `live-interaction-layer.md`（live 主 spec，本方案是其 §3.3 的落地）
- `doc/research/addressee-cross-validation-2026-08-12.md`（交叉验证，本 spec 依据）
- ADR0006（llm-gateway 单入口——Phase2 随四态演进，本 spec 记录演进意向）
- 部署真值：run-windows.ps1:372（-ngl 999 GPU）
