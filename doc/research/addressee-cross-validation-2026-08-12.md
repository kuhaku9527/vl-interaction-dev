# Addressee Detection 交叉验证报告（2026-08-12）

> 对云端调研（12 份附件、140 条断言）对照本地代码/环境逐条核验。评估原则（用户授权 20:4x）：**效果优先，大改不怕，云端兜底，硬件不设限**。
> 判定标准：✅ 证实（本地实证） / ⚠️ 部分属实或需实测 / ❌ 证伪或与本地冲突

---

## 一、核心推荐（H2 混合路线）核验

| 报告主张 | 判定 | 本地证据 |
|---|---|---|
| **做**——不做则多人场景频繁误响应 | ✅ 成立 | 本地 live 免唤醒常驻监听，VAD 有语音即进 ASR→LLM，无任何说话对象判断；自言自语/旁人说必被响应（日志实证：'对'/'嗯' 等 garbage 被提交后丢弃，但 LLM 已白跑） |
| **H2 混合（声学+语义并行融合）** | ✅ 方向成立 | 声学解决"谁在说"（前置过滤省计算）、语义解决"对谁说"（控误响应）——两缺陷互补，论证自洽 |
| 两阶段：Phase1 声学预筛（1-2 周）→ Phase2 四态+融合（2-4 周） | ✅ 合理 | 分阶段可独立验收/回滚，符合项目渐进接入传统 |

## 二、5 个重点核验点

### 1. CAM++ 在现有 sherpa-onnx 的可用性 — ✅ 证实（且成本低于报告假设）
- **本地实证**：sherpa-onnx **1.13.4** `dir(sherpa_onnx)` 含 `SpeakerEmbeddingExtractor` / `SpeakerEmbeddingExtractorConfig` / `SpeakerEmbeddingManager` / `OfflineSpeakerDiarization`——**原生支持 speaker embedding，无需升级依赖**（报告假设需集成，实际现成）。
- 断言 58/59（Extractor/Manager API 行为、L2-normalized、cosine、无 PLDA）与 sherpa-onnx 官方 API 一致 ✓。
- 模型需下载：`3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx`（27MB）——本地模型目录现无 speaker 模型（只有 asr/kws/vad），一次性下载成本。

### 2. 四态 decision token 与 ADR0006 兼容性 — ✅ 不冲突（需规范流程）
- ADR0006 = llm-gateway **单入口路由**规范（决策 token 静默回归兜底在 jarvis 侧）；四态 = 同一入口内扩语义（silence/response/delegation/**not-for-me**），**不违反单入口原则**。
- 需走 spec→验证→替换流程（ADR0006 核心 IP 的语义扩展，涉及 prompt_constants + parse_model_decision + turn_streaming 消费侧）。

### 3. AddresseeDetector 插在 VAD→ASR 之间 — ✅ 可行
- 本地链路：live_mode.py `feed_audio(pcm)` → VAD → ASR(feed_chunk) → endpoint → LLM。AddresseeDetector 插在 VAD 段后、ASR 前（或并行），与报告建议一致。
- 断言 123（不改 13 态状态机数量，仅加门控）——与本地 turn_controller 架构一致，侵入最小。
- ⚠️ 子代理标注：报告 6.2"不改变状态数量" vs 6.4"新增 ENROLL 状态"并存——**注册流程需一个显式状态/模式**（建议放 live_mode 层而非 turn_controller 核心，保持核心纯净）。

### 4. 融合权重 0.4/0.6 — ⚠️ 设计默认值，无实证
- 报告自认"初始加权平均起步，后续逻辑回归（需几百条标注）"。0.4/0.6 是合理起点但**非实测最优**——落地时留可配置 + 采集数据后网格搜索（清单验证方法也如此）。

### 5. 纯文本语义 80.9% — ⚠️ 外部基准，需本地实测
- 80.9% vs 80.6% 来自 **GPT-4o 三方对话 benchmark（IWSDS 2025, arXiv 2501.16643）**，非本地 llama.cpp 8.19B 实测。
- **判定**：不采信"纯语义不可用"为绝对结论，但作为"语义单独不可靠、需声学配合"的依据成立。落地时用本地模型跑同型 benchmark 验证（清单验证方法）。

## 三、❌ 发现的错误表述（服务端对现状的失准）

| 报告表述 | 本地事实 | 判定 |
|---|---|---|
| **"部署环境: Windows 单机，纯 CPU 推理"**（断言 52，12 份报告头部） | LLM/VLM 8.19B 在 **GPU**（run-windows.ps1:372 `-ngl 999` 全量卸载）；纯 CPU 仅指语音侧 | **❌ 证伪**——部署修正消息未覆盖报告头部，延迟/可行性评估偏保守（LLM 语义判定实际 GPU 200-500ms 级，比"纯 CPU"乐观） |
| 方案 A 延迟"~270ms"、方案 B"~440ms"（断言 42） | 本地 LLM 在 GPU，TTFT 可能 200-500ms 级（非报告假设的 CPU 1-3s） | ⚠️ 延迟绝对值需按 GPU 重估（相对结论"方案 A < 方案 B"不变） |

## 四、⚠️ 报告内部口径差异（落地时需选定，勿混用）

| 项 | 差异 | 落地选择建议 |
|---|---|---|
| CPU 增量 | 主报告"<5% 平均" vs 02 报告"+5-10% 瞬时" | 瞬时口径做预算，平均口径做宣传 |
| 注册时长 | 01"3-10 秒" vs 其余"2-3 秒" | 2-3 秒（02 报告与主报告 2.4 一致，01 为宽松上限） |
| 融合标注量 | "几百条" vs "500-1000 条" | 500-1000（更保守，标注成本可控） |
| 04 报告 4.2"偏高召回" vs 主报告"宁可漏不可乱插" | **方向矛盾** | **以主报告+用户授权为准：宁可漏、不可乱插**（04 报告高召回建议弃用） |
| ASR CPU 占用 | 03"10-20%" vs 02"20-40%" | 不影响 addressee 决策，仅记录 |
| 声学延迟 | 320-410 / 300-400 / 300-500ms 三口径 | 统一 300-400ms（与 ASR 并行不增串行延迟是核心结论） |
| 性能预估（70-90% 过滤、误响应降 50-80%） | 无实测来源的预估 | **必须实测**（Phase1 上线后统计真实过滤率/误响应率） |

## 五、风险矩阵核验（6 项）

- 风险 1（CAM++ 中文桌面区分度不足）— ⚠️ 真实风险，缓解=本地实测注册/测试语音，必要时换 ERes2NetV2（报告已给）✓
- 风险 2（not-for-me 准确率不足）— ✅ 概率"高"的判断合理（GPT-4o 都只 80.9%），缓解=prompt/few-shot + 数据 fine-tune ✓
- 其余 4 项（ASR 浪费、注册被绕过、融合退化、LLM 上下文）— 均为合理风险识别，缓解措施可落地 ✓

## 六、交叉验证总判定

1. **方向正确**：H2 混合路线（声学预筛 + 语义精判 + 融合）成立，且**本地落地成本低于报告假设**（sherpa-onnx 原生 Speaker API 现成）；
2. **一处重大失准**：部署形态"纯 CPU"（LLM 实为 GPU）——不改变路线方向，但延迟预算应乐观化；
3. **7 处口径差异**已选定落地口径（见 §四）；
4. **关键实测项**（落地前必须做）：① CAM++ 本地 RTF/区分度实测（下载模型跑 speaker-identification.py 示例）；② 本地 llama.cpp 四态 not-for-me 准确率实测；③ Phase1 上线后统计真实过滤率/误响应率。

## 七、落地方案建议（下一步，走 spec 草稿流程）

- **Phase 1（声学预筛，独立上线可回滚）**：
  1. 下载 CAM++ onnx + 本地 RTF 实测（验证 300-400ms/CPU<5% 假设）；
  2. 注册流程：录制 2-3s 注册语（2-3 段取平均，EMA 更新 α=0.1-0.2）——放 live_mode 层（新增 ENROLL 模式态，不碰 turn_controller 核心）；
  3. AddresseeDetector 插 live_mode feed_audio 链路：VAD 段后 speaker embedding + cosine（阈值 0.55-0.65，宁可漏不可乱插）→ 非目标说话人直接丢弃（省 ASR/LLM）；
  4. env 闸门默认关（fail-open 传统，验收开启）。
- **Phase 2（四态 + 融合）**：
  1. decision token 扩四态（+not-for-me）——prompt_constants + parse_model_decision + turn_streaming 消费侧 + GBNF grammar；
  2. 融合决策层（S_fusion = 0.4×声学 + 0.6×语义，双阈值 T_low 0.3 / T_high 0.7）；
  3. 本地 not-for-me 准确率实测 + 数据采集后权重网格搜索。

## 八、关联

- 调研交付：final_report_addressee_detection.md（工作区根，12 份）
- 断言清单：.workbuddy/tmp/addressee-assertions.md（140 条）
- 现有架构：live_mode.py（feed_audio 链路）、turn_controller.py（13 态）、turn_streaming.py（decision 消费）、ADR0006（单入口）
- 部署真值：run-windows.ps1:372（-ngl 999 GPU）
