# KWS v5「BT」诊断与调研沉淀（2026-08-10）

> 本文件是 **2026-08-10 一整天 KWS 调参/验收工作的调研与诊断落库**。
> 此前这些结论只散落在 `.workbuddy/memory/2026-08-10.md`（当日日志，30 天后会被蒸馏）
> 和 `.workbuddy/tmp/`（临时，会被清）——**跨会话就丢，等于白参考**。现正式落到
> `doc/research/`，作为可长期检索的 SSOT 旁支。
>
> 既有 SSOT（不要再重造）：`services/kws-training/KWS_V5_CAPTURE_SPEC.md`（采集/验收）、
> `doc/specs/kws-recall-optimization.md`（v3.20 硬约束）、
> `doc/research/kws-vad-bt-wakeword.md`（VAD 调研）、`doc/adr/0002-kws-config-env.md`（env 化）。

---

## 0. 一针见血的重新定性（真瓶颈）

我们一整天陷在"阈值 / 增广 / 负样本"局部调参的试错循环。**根因不是方法错，是追错了指标**：

- `test_kws.py`（§10.2 offline 门禁）测的是 **"直跑干净流"** 口径：
  FAR = 负样本独立 stream 命中比。同一模型在**线上真实链路**（100ms chunk 持久流包装层
  + WAIT_ASR_CONFIRM + fresh-window probe）下，口径完全不同。
- 事实锚点（`services/webui/.../jarvis_mode.py:132` 注释，2026-07-10 实测）：
  - **sherpa-onnx 直跑**：FAR 15.5% / recall 75.5%
  - **JarvisKWS 包装层（100ms chunk）**：**FAR 2.0% / recall 49.0%**
- 推论：**线上包装层 FAR 已经 ≈2%（达标！）**；真瓶颈是 **线上 recall 49%**（远低于 90% 目标）。
  而我们 offline 测到的 FAR 68%（v5-aug）是"直跑"口径，与线上脱节——**用它卡模型是假目标**。
  抬 `keywords_threshold` 压 offline FAR 必塌 recall（已实测证实），纯属把错误指标推向更错。

> **结论**：KWS v5 的验收/部署决策，必须来自**真机端到端（完整 JarvisKWS 包装层）**的
> recall/FAR（spec §10.2 line 345 已预留），而不是 offline `test_kws.py` 的直跑 FAR。
> offline 直跑只应作为"模型没训崩"的开发健康检查，**不是 deploy gate**。

---

## 1. 试错循环根因（两条，都可避免）

1. **评估口径脱节**：offline 直跑 FAR ≠ 线上持久流 FAR（见 §0）。用 offline FAR≤2% 当硬门禁，
   等于在追一个线上已经达标的指标，同时放任真瓶颈（线上 recall）无人测量。
2. **动手前没读既有规划**：项目早有清晰宏观 plan，但前几轮没先读，重复踩坑。既有 SSOT：
   - `KWS_V5_CAPTURE_SPEC.md`：v5 = 真人主动录音 53→**≥200 段** + live 采集全合并 + MUSAN 增广 3×；
     recall 49→90 只能靠**真人声纹多样性**，增广不能替代。
   - `kws-vad-bt-wakeword.md`：VAD = 推理侧分段/铺路层（缓解持久流边界 miss + 降 FAR + 铺路），
     **非召回救星**；点名"双字母 bt 音素极短是本质难题，换更鲁棒唤醒词可结构性提升召回，
     但用户坚持 BT 则不强推"。
   - `kws-recall-optimization.md`：Out of Scope = 不改唤醒词、不换引擎、本 patch 不重训 v5。

---

## 2. 实验对照表（决定性，已全部回填 REGRESSION_LOG.md）

所有 §10.2 offline 直跑（clean stream）口径，严格可比（同 seed 划分纯原始测试集）：

| 实验 | 正样本 | 负样本 | recall | FAR | 结论 |
|---|---|---|---|---|---|
| FAIL (exp_bt_v5) | 183(170录+13live) | 32 语音 | 96.7% | **100%** | 负样本不足→过触发 |
| realneg | 136 | 222(含语音硬负) | 52.9% | 27.3% | 语音硬负→recall 崩 |
| fixed | 183 | 152(去zai-ma正) | 75.0% | 89.5% | recall/FAR 强耦合 |
| esc50 基线 | 147 | 990(762非语音) | 69.4% | 49.0% | 非语音负样本降FAR但recall↓ |
| **A 增广** | **147+549增广** | **990** | **86.1%** | **68.2%** | 增广补多样性↑recall，但加噪反抬FAR |

**关键判断**：
- 增广 vs esc50：recall **+16.7pp**（69.4→86.1）→ **坐实"正样本多样性薄"是真因**，增广方向对。
- FAR 不降反升（+19.2pp，49→68.2）→ 加噪增广让模型更激进触发，**过触发加剧**。
- 阈值扫描（candidate-aug，thr 0.25→3.0）：0.25→86.1/68.2；0.5→52.8/22.7；0.75→19.4/5.1；1.0↑→0/0。
  **无甜点区** → recall 对阈值极敏感（0.25→0.5 仅 +0.25 掉 33pp）→ 模型未学会"BT=高分"，
  属**表征/训练层面问题**，阈值平移救不回。

---

## 3. 事实核查（用户指出后更正的两处旧误述）

| 旧误述（§11/§13 初版） | 核查后事实 |
|---|---|
| "真人录音仅 147 段、gameDAC 不足" | `positive.jsonl` 实际 **170 条**（nvidia_broadcast:85 / gameDAC_chat:85 **完全均衡**）+ `mic_captures` 13 条 live = **183**，已接近 spec ≥200。`recall_gameDAC` 78.6% 是测试集仅 14 条 gameDAC 的 split 统计波动，非训练不足。 |
| "VAD 未实施" | **代码已落地**（`vad_bypass.py` + `jarvis_mode.py` 集成 + `services/scripts/run-windows.env` line 88-97 完整 env + `tests/test_vad_bypass.py`）。默认 `JARVIS_VAD_ENABLED=false` **待启用**，且 `silero_vad.onnx` 资产**全盘缺失**→ 当前 fail-open 透传。 |

> 注：旧误述里写的"run-windows.env 有完整 VAD env"路径不精确——实际文件是
> `services/scripts/run-windows.env`（仓库根无 `run-windows.env`）。事实（VAD env 存在、默认关）正确。

---

## 4. 外部调研沉淀（行业最佳实践，之前 web 调研所得，避免白参考）

KWS 低资源 / 双字母短唤醒词的业界共识（与本项目约束交叉验证）：

1. **两阶段检测（detect + verify）**：第一段低阈值高召回捕获候选，第二段用
   ASR/嵌入比对/二次确认拒识非唤醒语音，根治 FAR。本项目 `jarvis_mode.py` 的
   WAIT_ASR_CONFIRM + fresh-window probe 已是"KWS+ASR 验证"形态，但 ASR 对 "bt"
   两音节确认失败（→ 线上 recall 掉到 49%）。**改进方向**：强化验证段（如专用短词
   确认模型、或 phoneme 级匹配），而非抬高 KWS 阈值。
2. **Max-pooling / 拉大正负得分分布**：训练目标层面让正样本得分集中高位、负样本低位，
   阈值才有甜点区。本项目 candidate-aug 得分挤在 0.25~0.5（无甜点）→ 训练目标/配置
   需重审（decoder / epoch / lr / 加噪策略——加噪增广反而抬 FAR，方向可能反了）。
3. **更鲁棒唤醒词（结构解）**：双字母 bt 音素极短、易与噪声/其他词混淆，是本质难题。
   换更长/音素更区分的唤醒词可结构性提升召回。**本项目 spec 列为 Out of Scope**（用户坚持 BT）。
4. **非语音噪声负样本是标配**：阿里小云 KWS / NatsuiroGinga(中文 Icefall+Sherpa-ONNX) 都用
   DEMAND/MS-SNSD/ESC-50 类环境噪声当 noise 类负样本。本项目用 ESC-50 替 MUSAN 正是此路
   （已落 `esc50_neg/` 2000 段 16k，可复用）。

---

## 5. 推荐路径矩阵与优先级（"按你推荐的走"的执行依据）

| 项 | 内容 | 性质 | 状态 |
|---|---|---|---|
| **E（先手止血）** | 对齐评估口径：offline 直跑 FAR 改"开发健康检查"，部署决策以**真机端到端（完整包装层）recall/FAR** 为准；设计真机验收脚本 | 几乎零成本、低风险 | 本文件 + spec §10.2 标注（进行中） |
| **B（并行）** | 启用 VAD（先 `vad_enabled=true` 仅注解、不动行为）→ 用 `analyze_kws_captures.py` 测 `vad_miss_kill`（误杀率）→ 确认安全再开 `vad_softgate` | 代码已就绪，缺 `silero_vad.onnx` 资产 | 见 §6 步骤 |
| **A（根因，需用户物理动作）** | 录音已 170+13=183，仅差 ~17 段到 spec ≥200；spec §9.2 已给出分设备录法 | 真人声纹多样性是 recall 真解 | 用户录即可，工具已备 |
| **C（中长期）** | 训练目标/两阶段验证重审（拉大得分分布、强化验证段） | 不换引擎、重训投入 | 待 A/B/E 验证后再定 |

**当前线上状态**：生效 `bt-en/` = esc50 备份恢复的 live（保 Jarvis 不中断）；
`candidate-aug-20260810_212842` 训练导出 OK 但未部署（offline FAIL，需真机验）。

---

## 6. VAD 启用 + 误杀验证 步骤（B，不擅自翻默认开关）

1. **取资产**：`silero_vad.onnx` 需从官方渠道取得（sherpa-onnx 自带 / 官方 HF 发布），
   放到某目录如 `D:/AI/models/sherpa-onnx/models/vad/`。**本机当前缺失该文件。**
2. **接线**：在 `services/scripts/run-windows.env` 设
   `JARVIS_VAD_ENABLED=true`、`JARVIS_VAD_MODEL_DIR=D:/AI/models/sherpa-onnx/models/vad`
   （`JARVIS_VAD_SOFTGATE` 保持 `false` = 仅注解、不改变 KWS 行为，fail-open 默认透传）。
3. **测误杀**：对 `D:/AI/data/kws/mic_captures/` 跑
   `python services/scripts/analyze_kws_captures.py`（脚本已内建 `vad_miss_kill` 列：
   当 VAD 判静音但 shadow ASR 听到 "bt" 的 chunk 数 = 潜在误杀风险）。
4. **决策**：`vad_miss_kill` 可接受 → 再开 `JARVIS_VAD_SOFTGATE=true`（真正在静音 chunk 跳
   过 KWS feed，边际降 FAR + 省算力）；若误杀高 → 保持关，不软门控。
5. 默认 `JARVIS_VAD_ENABLED=false` 的翻转需用户确认（影响线上行为），不自动改。

---

## 7. 上一轮"局部调参"的结论回收

- 负样本不足（缺非语音噪声）→ 用 **ESC-50 替 MUSAN**，已落 `esc50_neg/` + `esc50_musan/` 布局，
  990 负样本含 762 段非语音。✅ 解决"负样本量/非语音"缺口。
- 正样本多样性薄 → 用内建 `build_augmented_positives` + ESC-50 拼 MUSAN 布局增广，
  recall 69.4→86.1。✅ 证实方向，但加噪反抬 FAR，需配合 §5 训练目标重审。
- 评估口径脱节 → 本文件 §0/§E 已定性，offline FAR 不再是部署 gate。

---

## 8. 既有 SSOT 指针（勿重复造轮子）

- 采集/验收总规：`services/kws-training/KWS_V5_CAPTURE_SPEC.md`
- 召回优化硬约束：`doc/specs/kws-recall-optimization.md`（v3.20）
- VAD 调研：`doc/research/kws-vad-bt-wakeword.md`
- env 化约定：`doc/adr/0002-kws-config-env.md`
- 实验滚动记录：`services/kws-training/REGRESSION_LOG.md`
- 真机端到端验收脚本：`services/scripts/test_jarvis_kws_e2e.py`（100ms chunk 直跑口径）
- live 捕获分析（含 VAD miss-kill）：`services/scripts/analyze_kws_captures.py`
- 当日流水：`.workbuddy/memory/2026-08-10.md`（§1–§14，30 天后蒸馏）
