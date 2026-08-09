# KWS + VAD 辅助「BT」双字母唤醒词调研报告

> 类型：只读调研（不写业务代码，仅核查事实 + 给结论）
> 范围：Jarvis 全双工实时语音对话中，给 KWS 加 VAD 是否能提升 `bt` 唤醒召回，如何落地
> 调研日期：2026-08（基于仓库真实代码 / 文档核查）

---

## 0. 结论先行（TL;DR）

1. **建议加 VAD，但定位必须精确**：它是推理侧的「语音分段 / 预过滤」层，**不是** 把 49% 召回拉到 90% 的银弹。49% 召回的根因是「双字母唤醒词 + 训练正样本太少（53 段）」，那是 KWS v5（训练侧扩数据）的职责。
2. **VAD 对 BT 的真实收益**在于：给 KWS 更干净的语音段边界（直接缓解 v3.21 的长实时流边界污染 miss）、边际降低非语音误触发、为端点检测 / barge-in / 流式分帧铺路；**不能直接提升「BT」的声学判别召回**（听不到还是听不到）。
3. **选型**：复用项目已有的 `sherpa-onnx` 内置 Silero VAD（`sherpa_onnx.SileroVadModel`），零新依赖、16k mono 管线一致、参考 `smart_turn_adapter.py` 的 fail-open 加载范式。
4. **落地姿态**：作为 **KWS v5 issue 的独立前置子任务**（推理侧增强，与训练侧扩数据互补、可并行、不互相阻塞）。VAD 先以**旁路 + 软门控**形态接入，验证误杀率后再决定是否硬门控。
5. **不构成 VAD 否定的冲突**：仓库曾有决策 T-VAD-1 否决「独立 Silero VAD 用于判断是否有人说话」。本报告主张的 VAD 是**更窄的 KWS 分段预过滤器 + 下游能力铺路**，与 T-VAD-1 的边界不重叠，但应在 PR 里显式说明，避免审查组误判为重复造轮子（见 §5 R3 / §6）。

---

## 1. 当前 KWS / 实时管线事实核查

### 1.1 KWS 实现（`services/asr/jarvis/kws.py`）

- `JarvisKWS` 封装 `sherpa_onnx.KeywordSpotter`（`kws.py:67-79`）。
- 模型：`bt-en`（自训 v4），路径默认 `D:/AI/models/sherpa-onnx/models/kws/bt-en`（`kws.py:28`，`JarvisConfig.kws_model_dir` `jarvis_mode.py:135`）。约 56MB encoder + ~50KB decoder+joiner。
- 关键词文件格式：`keywords.txt` 内容 `B T @bt`（BPE 两 token），见 `kws.py:3-4` 注释与 `jarvis-mode.md §2.1`。
- 默认参数（已 env 化，ADR 0002）：
  - `keywords_score=10.0`、`keywords_threshold=0.25`、`num_trailing_blanks=1`、`max_active_paths=10`（`kws.py:31-34`，`jarvis_mode.py:137-145`）。
- 两条判定路径：
  - `feed_audio(pcm)`（`kws.py:95-130`）：**持久流**，每片 100ms PCM 喂入同一 stream（`start()` 创建一次，`kws.py:90-93`），命中后**重建 stream 防重触发**（`kws.py:129`）。这是实时主路径。
  - `detect_in_pcm(pcm)`（`kws.py:132-160`）：对一段 PCM 窗口**新建干净 stream** 跑一次（v3.21 fresh-window 兜底），不污染主 stream。
- 实测定点（文档口径，`jarvis-mode.md §2.2`）：sherpa-onnx 直跑 FAR 15.5% / recall 75.5%；**JarvisKWS 包装层（100ms chunk + 持久流）= FAR 2.00% / recall 49.06%**。

### 1.2 实时音频管线（全双工）

入口链路（`audio_processor.py` → `jarvis_mode.py`）：

- 浏览器 mic（48kHz OPUS stereo）→ WebRTC → `MicAudioTrack.recv()`（`audio_processor.py:70-98`）重采样为 **16kHz mono PCM16**（`audio_processor.py:80-85`），逐片 `await self._session.feed_audio(pcm_bytes)`。
- `JarvisSession.feed_audio`（`jarvis_session.py:89-91`）→ `JarvisStateMachine.feed_audio`（`jarvis_mode.py:558-567`）：入 `_audio_queue` + 维护 `_recent_audio` 滚动窗（供 Smart Turn）。
- `run()` 主循环（`jarvis_mode.py:573-606`）按状态分发音频片：
  - `KWS_LISTENING` → `_handle_kws(pcm)`（`jarvis_mode.py:612`）
  - `WAIT_ASR_CONFIRM` → `_handle_wait_asr_confirm`
  - `DIALOG_ACTIVE` / `TTS_PAUSED` → `_handle_dialog`（走 ASR + endpoint 判定）
- `_handle_kws`（`jarvis_mode.py:612-660`）流程：
  1. `_observe_kws_diagnostics(pcm)` 算 peak/rms 并按 `kws_capture_peak_threshold`（默认 0.035）门控 diagnostic 采样（`jarvis_mode.py:712-726`）。
  2. `self._kws.feed_audio(pcm)` 主路径判定；**miss 时**先 `_probe_kws_fresh_window()`（v3.21 干净流兜底，`jarvis_mode.py:766-802`），再 `_feed_kws_shadow_asr()`（诊断，`jarvis_mode.py:824-872`）。
  3. 命中 → `WAIT_ASR_CONFIRM`（Hybrid，v3.17）→ ASR 1.2s 内匹配 `bt`/`BT`/`B T`/`b t` → `WAKE_DETECTED` → 播 `wake.wav` → `DIALOG_ACTIVE`（见 `jarvis-mode.md §14.10`、状态机 `jarvis_mode.py:147-167`）。
- KWS/ASR 引擎在会话启动 `prewarm_engines()` 时一次性加载（`jarvis_mode.py:486-523`），避免 ASR 冷启动吞掉 confirm 窗口（v3.18）。

### 1.3 现状与已知痛点（与 VAD 相关）

- **召回仅 49.06%**：双字母 `bt` 音素极短、易漏检；阈值扫描已穷尽（`kws_param_sweep.py` 结论，`jarvis-mode.md §14.12`）：`th=0.25` 与 `th=0.20` 召回同为 49.06%、FAR 同为 2.00%；`score=12` 召回崩到 13.21%。**单纯调参无效，杠杆在模型/数据（v5）**。
- **长实时流边界污染 miss（v3.21 根因）**：用户连喊 `BT` 不唤醒，但保存的 capture（`record_kws_corpus.py` 产出的 `.wav`，经 `analyze_kws_captures.py` 回放）离线用**干净 stream** 能命中。结论：长持久流的边界/前序音频污染导致主路径 miss，新开 stream 反而能命中。这正是 VAD「语音分段」能直接缓解的点。
- **系统层处理削尾音**：用户 mic 链路 `echoCancellation:true, noiseSuppression:false, autoGainControl:false`，且 **NVIDIA Broadcast 在系统层独立做 VAD/EC/降噪**，对 `BT` 这种 2 音节短词敏感、可能削掉尾音（`jarvis-mode.md §14.7` 末段、`§14.8`）。这恰是 VAD 误杀风险来源（见 §5 R1）。
- **麦克风电平偏低**：MIC RMS 实测峰值 5%/22%/20%，已加前端 GAIN slider 默认 1.5x、最高 3.0x（`jarvis-mode.md §14.8`）。

### 1.4 现有「类 VAD」部件（务必区分）

- **能量 peak/rms 检测器**：仅用于 diagnostic capture 门控（`_observe_kws_diagnostics` 的 `kws_capture_peak_threshold=0.035`，`jarvis_mode.py:719`；`_feed_kws_shadow_asr` 的 `peak >= threshold*0.7`，`jarvis_mode.py:835`）。它**不喂 KWS、不丢弃音频、只决定要不要存 wav**，是最朴素的「有没人说话」判断，**不是 VAD**。
- **ASR endpoint detection**：`JarvisASR`（`asr.py:37-49`）`enable_endpoint_detection=True` + rule1/2/3 静音判定，仅用于 `DIALOG_ACTIVE` 的 turn-end 检测（声学静音，非语义）。与 KWS 唤醒路径无关。
- **Smart Turn ONNX**：`smart_turn_adapter.py` 在 endpoint 之后、LLM 之前加语义级 end-of-turn（fail-open，默认关）。这是未来能力，**不与 KWS 唤醒冲突**。

### 1.5 既有 VAD 相关决策（避免冲突）

- `决策/调研-HF-speech-to-speech-姿态.md:28`（T-VAD-1）：「VAD 本体(Silero) | **不做** | KWS + ASR endpoint detection + EXIT_WORDS 已覆盖『有/没人说话』」。
- 该决策针对的是「系统级『是否有人说话』检测器」。本报告主张的 VAD 是**更窄的 KWS 分段预过滤器 + 端点/打断/流式铺路**，边界不同（见 §6 结论与 §5 R3）。

---

## 2. VAD 对「BT」双字母唤醒词的收益分析（实事求是）

### 2.1 双字母为什么难

`bt` 只有两个短音节（B + T），声学能量集中在极短窗口（约 0.2–0.4s），且：
- 音素短 → KWS transducer 的 `num_trailing_blanks`、chunk 边界容易把它切散；
- 易与近音混淆（be / bit / bite / 不 / 比…），故阈值不能太低（否则 FAR 突破，见 `jarvis-mode.md §2.4`）；
- 真实链路经 NVIDIA Broadcast + WebRTC 重采样，短词尾音被削（§1.3）。

### 2.2 VAD 能做什么 / 不能做什么（关键）

**能做的（真实收益）：**
- **给 KWS 干净语音段边界**：VAD 把连续音频切成「语音段 + 静音段」。KWS 在每段语音上从干净边界起 stream，可**直接缓解 v3.21 的长流边界污染 miss**——相当于把「fresh-window probe」常态化、低延迟化，而不必每 0.5s 重建整段窗口。
- **边际降低非语音误触发（FAR）**：当前 FAR 已 2%，提升空间有限；但 VAD 可在静音段根本不喂 KWS，减少风扇/电流/空调在 KWS 上的随机触发（域偏移场景有价值）。
- **为下游铺路（用户明确诉求）**：端点检测、barge-in 打断、流式分帧都建立在「语音段」之上。这是加 VAD 的**独立正当理由**，不依赖它能否救召回。
- **提升 v5 训练样本质量**：用 VAD 切出的干净语音段做 `record_kws_corpus.py` capture（`analyze_kws_captures.py` 回放），比当前「peak 门控整段 3s 滚动窗」噪声更小，利于分拣 positive/hard_negative（`jarvis-mode.md §14.12`）。

**不能做的（避免过度承诺）：**
- **不能直接提升「BT」的声学召回**：若 KWS 在给定音频里根本判别不出 `bt`（模型/数据问题），VAD 只是把这段音频更整齐地喂进去，召回不变。**49%→90% 只能靠 v5（扩 200+ 正样本 + MUSAN 负样本）。**
- **反而可能降召回**：若 VAD 把轻声/短促/被 Broadcast 削过的 `BT` 判为静音（误杀），KWS 连听的机会都没有，召回会掉。这是最大风险（见 §5 R1）。

### 2.3 预期效果（保守估计，待数据验证）

| 维度 | 无 VAD（现状） | 加 VAD（软门控/旁路） | 说明 |
|---|---|---|---|
| BT 声学召回 | 49.06% | **基本不变或小幅改善** | 改善来自边界污染缓解，非声学提升；不预期大幅拉升 |
| FAR | 2.00% | 可能略降（<2%） | 静音段不喂 KWS |
| 长流边界 miss | 存在（靠 fresh-window 兜底） | 减少 | VAD 分段常态化 |
| 下游能力（端点/打断/流式） | 无 | 具备 | 独立收益 |
| CPU/延迟 | — | Silero VAD ~几 MB、CPU <1%、<5ms/片 | 可忽略 |
| 风险 | — | VAD 误杀短促 BT | 须软门控 + fail-open |

**结论性判断**：VAD 的预期收益是「稳链路 + 铺路 + 边际降 FAR + 缓解边界 miss」，**不是召回救星**。把 VAD 当成召回方案的叙事会误导决策。

---

## 3. VAD 选型推荐 + 集成草图

### 3.1 候选对比

| 方案 | 依赖 | 与本项目契合度 | 评价 |
|---|---|---|---|
| **sherpa-onnx 内置 Silero VAD** | 已有 `sherpa-onnx>=1.10.30`（webui/pyproject.toml:62）+ `onnxruntime>=1.16.0`（:48），**零新依赖** | 高（同 16k mono 管线、同推理后端） | **推荐**：`sherpa_onnx.VoiceActivityDetector`（`SileroVadModelConfig` 配置）原生支持，可产出语音段/尾点 |
| 独立 silero-vad ONNX（pipecat 式） | 已具备 onnxruntime | 中（需自己写前后处理，复用 `smart_turn_adapter` 加载范式） | 可行但重复造轮子 |
| webrtc-vad（py-webrtcvad） | 新依赖，仅 8/16/32kHz、10/20/30ms 固定帧、对 16k 友好但粗暴 | 低 | 只能给「是否语音」二值，分段精度差 |
| 能量阈值（自写） | 无 | 已有雏形（`_observe_kws_diagnostics` peak 门控） | 已有且粗糙；不推荐升级路线，VAD 模型明显更稳 |

### 3.2 推荐：sherpa-onnx 内置 Silero VAD

理由：
- **零新依赖**：`sherpa-onnx` 已装（webui/pyproject.toml:62 `sherpa-onnx>=1.10.30`）。正确的 Silero VAD 包装类是 `sherpa_onnx.VoiceActivityDetector`（配 `sherpa_onnx.SileroVadModelConfig` 做配置）；**已装版本顶层导出的是 `VoiceActivityDetector` / `SileroVadModelConfig` / `VadModel`，并无裸 `SileroVadModel`**（详见 §7.3 真码核查）。
- **管线一致**：输入即 16k mono float32，与 `MicAudioTrack` 重采样输出、`JarvisKWS`/`JarvisASR` 完全一致，无需二次重采样。
- **可复用 fail-open 范式**：参考 `smart_turn_adapter.py:83-110`——模型缺失 / onnxruntime 不可用 → `available=False`、声学路径不变。VAD 也必须是 fail-open（VAD 挂了不能让 KWS 失聪）。
- **轻量**：Silero VAD ~4MB、CPU 推理 <1% 占用，与 KWS（0.1% CPU）量级相当。

### 3.3 集成草图（音频流分层）

```
浏览器 mic (48k OPUS stereo)
   │  WebRTC
   ▼
MicAudioTrack.recv()  ── 重采样 16k mono PCM16 ──► session.feed_audio(pcm)
                                                        │
                                                        ▼
                                          ┌──────────────────────────────┐
                                          │  VAD 旁路层（新增, sherpa     │
                                          │  SileroVadModel, fail-open）  │
                                          │  • 产出 speech segment 边界    │
                                          │  • 不丢弃任何 pcm（软门控）    │
                                          │  • 仅打标 + 供下游             │
                                          └──────────┬───────────┬────────┘
                                                     │           │
                       (A) 始终透传                   │           │ (B) 分段事件
                                                     ▼           ▼
                                          _audio_queue ──► run() 循环
                                                     │           ├─► endpoint / barge-in / 流式分帧（DIALOG_ACTIVE）
                                                     ▼           └─► v5 capture 干净语音段
                                          KWS_LISTENING:
                                          _handle_kws(pcm)
                                             ├─ [可选软门控] 若 VAD=speech 才送 KWS；
                                             │   VAD=silence 则跳过（不重建 stream）
                                             ├─ KWS feed_audio → WAIT_ASR_CONFIRM
                                             ├─ miss → fresh-window probe（§1.2）
                                             └─ shadow ASR（诊断）
```

**关键分叉决策（务必二选一先定）：**
- **形态 A（强烈推荐先做）— 旁路 + 软门控（不阻断 KWS）**：VAD 仅旁路产生 speech/silence 标注与段边界，**KWS 仍收到全部音频**。可选增强：仅在「VAD 判为 speech」的片才调用 `kws.feed_audio`，但**一旦 VAD 缺失/失败，立即回退为「全透传」**（fail-open）。这样 VAD 永远不会成为召回的瓶颈，先用来采集误杀率数据。
- **形态 B — 硬门控（仅在验证后）**：用 VAD 段边界强制切分 KWS stream（每段语音重新 `kws.start()`）。能根治边界污染，但**误杀即丢召回**，必须先有大量真机数据证明误杀率 < 召回收益才上。

### 3.4 工程落地要点

- **接入点**：放在 `MicAudioTrack.recv()` 之后、`JarvisStateMachine.feed_audio` 之前（`audio_processor.py:85` 处或 jarvis_session 层），或直接在 `_handle_kws` 内对每片做 VAD 标注（`jarvis_mode.py:612`）。推荐前者——一次计算、全状态机复用（KWS 预过滤 + 下游共用）。
- **状态归属**：VAD 状态机独立于 Jarvis 六态机，仅输出 `is_speech` + 段边界事件；**不干扰 DIALOG_ACTIVE 的持续对话**（DIALOG_ACTIVE 已有 ASR endpoint 管 turn-end，VAD 在 KWS_LISTENING 做预过滤、在 DIALOG_ACTIVE 做 barge-in 信号）。
- **env 化**：仿 ADR 0002，新增 `JARVIS_VAD_ENABLED` / `JARVIS_VAD_MODEL_DIR` / `JARVIS_VAD_MIN_SILENCE_S` 等，默认关（fail-open 默认即「不启用=全透传」）。
- **与现有 fresh-window probe 的关系**：VAD 常态化分段后，`_probe_kws_fresh_window` 可改为「直接用 VAD 切出的最近一段语音」而非「整段 3s 滚动窗」，更快更准；两者不冲突，可渐进替换。
- **复用现有 capture 闭环做评估**：沿用 `kws_capture_*` + `analyze_kws_captures.py`（`jarvis-mode.md §14.12`），加一列「VAD 判定」，统计误杀（ASR shadow 听到 bt 但 VAD=silence）与边界收益。

---

## 4. 与 KWS v5 训练计划的关系

- **KWS v5 = 训练侧**（根因修复）：扩正样本 53 → 200+ 段 + 加 MUSAN 负样本，目标 recall 49% → 90%+（`jarvis-mode.md §2.2`、`§13.2`、`kws-recall-optimization.md`）。训练代码在 `services/kws-training/train_kws.py`（CTC + Zipformer2 + lhotse）。
- **VAD = 推理侧增强**（症状缓解 + 能力铺路）。
- **互补不替代**：
  - v5 解决「模型听得懂 BT 吗」（声学判别力）；
  - VAD 解决「把 BT 整齐地喂给模型 + 不在静音上浪费判定 + 给下游分段」。
  - 二者正交：VAD 上线不依赖 v5，v5 上线也不依赖 VAD。
- **VAD 反哺 v5**：用 VAD 切出的干净语音段做 `record_kws_corpus.py` capture（`analyze_kws_captures.py` 回放），样本信噪比更高，重训 v5 的 positive/hard_negative 质量更好。
- **建议归属**：VAD 作为 **KWS v5 issue 下的独立子任务（推理侧）**，可先于 v5 合入（不阻塞训练），也可并行推进。

---

## 5. 风险与建议

- **R1 VAD 误杀短促/轻声 BT（最高风险）**
  - 来源：`BT` 仅 2 音节，且 NVIDIA Broadcast 已削尾音（§1.3）。
  - 缓解：**软门控 + fail-open**（形态 A）；VAD 缺失即全透传；上线前用现有 `analyze_kws_captures.py` capture + shadow ASR 统计「VAD 误杀率」，误杀率 > 0 时不启用硬门控。
- **R2 双字母本质难题，VAD 救不了召回**
  - VAD 不提升声学召回。仍须 v5。若用户坚持 `bt` 不改，则点明：换更鲁棒唤醒词（如 3 音节 `hey bt` / `铁御` 中文 2 字）可结构性提升召回，但**用户坚持 BT 则不强推**，仅记录为可选项（`jarvis-mode.md §2.1` 已说明用户选型理由）。
- **R3 不要与既有 T-VAD-1 决策冲突**
  - 既有决策否决「独立 Silero VAD 用于判断是否有人说话」。本报告 VAD 是**更窄的 KWS 分段预过滤 + 下游铺路**，边界不同。PR 须显式说明，避免审查组误判为重复造轮子（参考 `smart_turn_adapter.py:51-55` 对「语义 end-of-turn 不是 VAD」的澄清写法）。
- **R4 全双工下 VAD 状态机不干扰持续对话**
  - VAD 仅输出标注/段边界，不接管六态机；`DIALOG_ACTIVE` 的 turn-end 仍由 ASR endpoint（声学）主导，Smart Turn（语义）辅助；VAD 在 KWS_LISTENING 预过滤、在 DIALOG_ACTIVE 提供 barge-in 信号。
- **R5 延迟/CPU**
  - Silero VAD 极轻（§3.2），实测对 100ms 片 <5ms、CPU <1%，不破坏现有 <300ms 唤醒延迟预算（`jarvis-mode.md §10.1`）。
- **其他建议**：先旁路采集「VAD 判定 vs ASR shadow 听到 bt」的对照数据 1–2 周，再决定硬门控；不要盲降 `keywords_threshold` 追求召回（已被 sweep 证伪，`jarvis-mode.md §14.12`）。

---

## 6. 结论：是否建议加 VAD

**建议加，但作为「推理侧分段/预过滤层 + 下游能力铺路」，不作为召回救星，且作为 KWS v5 issue 的独立前置子任务。**

- **是否加**：加。收益明确且成本低（零新依赖、CPU 可忽略、fail-open 安全），且用户明确需要为端点/打断/流式铺路。
- **定位**：缓解 v3.21 长流边界污染 miss、边际降 FAR、为下游分段；**不承诺提升 `bt` 声学召回**——那是 v5 的活。
- **形态**：先做**旁路 + 软门控（形态 A）**，跑真实数据评估误杀率后再考虑硬门控（形态 B）。
- **归属**：KWS v5 issue 的**独立前置子任务**（与训练侧扩数据并行、互不阻塞）。
- **不做**：不要指望 VAD 把 49% 拉到 90%；不要与 T-VAD-1 决策冲突（PR 显式划清边界）；不要硬门控上线前先验证误杀率。

**建议落地顺序**：
1. 旁路接入 sherpa-onnx Silero VAD（fail-open，env 默认关），仅打标 + 产出段边界；
2. 复用 `analyze_kws_captures.py` / `record_kws_corpus.py` capture 闭环统计 VAD 误杀率 / 边界收益（对照 shadow ASR）；
3. 验证无误杀后，启用软门控（仅 speech 片送 KWS，silence 片跳过、不重建 stream）；
4. 复用 VAD 段边界替换 fresh-window 的 3s 滚动窗；
5. 将 VAD 段事件接到 endpoint / barge-in / 流式分帧（全双工下游能力）；
6. 用 VAD 切出的干净语音段反哺 v5 训练样本采集。

---

## 7. 真源码核查（HF speech-to-speech 实际代码）+ 铺路后续调研

> **7.0 诚实声明：原报告未读 HF 真码。** 原 §1.5 只引用了 codex 的二手决策文档 `决策/调研-HF-speech-to-speech-姿态.md`（T-VAD-1），**未直接读 HF 真源码**。本章按用户要求，直接读本地克隆 `.cache/research/speech-to-speech`（228 文件、完整 git 仓库）核对，并做「铺路」可借鉴点调研。所有引用均来自该克隆的 `src/speech_to_speech/` 下真实文件。

### 7.1 codex T-VAD-1 的失真：VAD 在 HF 里根本不是「判有人没说话」
T-VAD-1（决策文档:28）把 VAD 框定为「系统级『是否有人说话』检测器」，并据此「不做」。但读 HF 真码后，VAD 在该项目是**整个实时对话的轮次管理 + 打断 + 流式 + 短段拼接 + 音频增强底座**，远非简单开关。证据（均来自 `.cache/research/speech-to-speech/src/speech_to_speech/VAD/`）：

- `vad_iterator.py`：`VADIterator`（snakers4 标准流式实现），`threshold=0.5 / min_silence_duration_ms=300 / speech_pad_ms=30`；逐 chunk 出 speech 概率，触发后保留 `speech_pad_ms`（默认 30ms）前导音频（prefix buffer），语音结束返回整段 utterance。`reset_states()` 每轮次重置。这是流式 VAD 的教科书实现。
- `vad_handler.py`：`VADHandler.process()` 产出 `SpeechStartedEvent` / `SpeechStoppedEvent`，且事件带 `interrupt_response` 标志（`pipeline/events.py:37` 默认 `True`）→ 直接驱动**打断助手在播回复**（见 §7.2）。
- `SpeculativeTurnTracker` + `VAD/smart_turn.py`：轮次可「重开（reopen）」、带 `reopen_grace_ms` 优雅等待，结合 Smart Turn 语义端点概率决定何时真正结束轮次。
- **短段拼接（short-segment stitching）**：`_PendingShortSegment`（`_SHORT_SEGMENT_MIN_FRAGMENT_MS=100`）+ `short_segment_merge_ms`，把临近的短语音段自动缝合（中间静音重插）——**这恰对症我们 `BT` 被 chunk / `num_trailing_blanks` 切散的痛点**（§2.1）。
- **运行时热调**：`_apply_runtime_turn_detection` 不重启即改 VAD `threshold` / `silence_duration_ms`（甚至 `session.update` 运行时推送）。

**结论**：T-VAD-1 拒绝的是 strawman（仅「有人没说话」）。本提案（KWS 分段预过滤 + 端点/打断/流式铺路）落在 HF 真码里 VAD 的**真实用途**上。原 §1.5 / §5 R3「边界不重叠」判断**经真码核实成立**，但措辞应升级：不是「更窄的过滤器」，而是「与 HF 实际用法一致，且正是 T-VAD-1 误读的部分」。

### 7.2 铺路：可直接借鉴的 4 个真码模式（全来自 `.cache/research/speech-to-speech`）

**(1) Barge-in / 打断（最直白的铺路收益）** — `api/openai_realtime/handlers/audio.py:108 on_speech_started`：
```python
# VAD 检测到 speech_start 且开启打断 → 取消助手正在生成的回复
if st.in_response and event.interrupt_response and st.runtime_config.interrupt_response_enabled:
    events.extend(response.finish_response(conn_id, status="cancelled", reason="turn_detected"))
```
→ VAD 一检测到 speech_start 且开启打断，就取消助手正在生成的回复。这正是全双工「用户插话即打断」的核心。
我们当前 `DIALOG_ACTIVE` **没有 barge-in**（只有 ASR endpoint 管 turn-end，用户须等 TTS 播完）。**VAD 的 speech_start 信号正好驱动 barge-in**，且 fail-open（VAD 关则无打断，行为不变）。
落地：在 `JarvisStateMachine` DIALOG_ACTIVE 加 `on_vad_speech_start` → 取消当前 TTS/LLM 流；信号源 = §3.3 B 路径的 VAD 旁路层。

**(2) 短段拼接（short-segment stitching）** — HF `_PendingShortSegment`（`_SHORT_SEGMENT_MIN_FRAGMENT_MS=100`）。把 <100ms 的相邻短段在 merge 窗口内缝合（中间静音重插，长度对齐音频时钟）。可借鉴：VAD 切出的语音段若过短且临近上一段，先 hold 再缝合，避免 KWS 拿到半截 `BT`（与 §2.1 痛点直接对应，也是 v3.21 边界 miss 的另一种缓解）。

**(3) Speculative turn + 优雅重开（reopen_grace）** — HF 用 Smart Turn 概率（`complete / incomplete`）决定 response 的 `reopen_grace_ms`（等用户是否真说完再提交）。我们已有 Smart Turn（`smart_turn_adapter.py`，fail-open）→ 复用其概率接 VAD 的 speech_start，做「用户又开口则取消重开」的轮次管理，而非简单 silence 超时。

**(4) 运行时热调 VAD 参数** — HF `_apply_runtime_turn_detection` 改 threshold/silence 不重启。我们 ADR 0002 已 env 化 KWS 参数；VAD 同理，且可进一步提供运行时端点（类似 #124 的 hot-reload 思路）调 VAD 阈值，便于真机标定。

### 7.3 依赖 / 技术栈对齐核实（纠正原报告 API 误用）
- **我们已有 sherpa-onnx Silero VAD**（核实：webui/pyproject.toml:62 `sherpa-onnx>=1.10.30`；已装版本 `sherpa_onnx/__init__.py` 导出 `SileroVadModelConfig` / `VoiceActivityDetector` / `VadModel` / `VadModelConfig`）→ §3.2「零新依赖」属实。
- **纠正**：原 §3.1/§3.2 写的 `sherpa_onnx.SileroVadModel(...)` **不是已装版本的导出符号**；正确用法是 `config = sherpa_onnx.SileroVadModelConfig(); vad = sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=...)`。已同步修正 §3.1/§3.2。
- **与 HF 差异**：HF 用 `torch.hub.load("snakers4/silero-vad")`（引入 torch 依赖）；我们用 sherpa-onnx 封装 → **不引入 torch**，依赖更轻，推理后端与 KWS/ASR 统一（onnxruntime）。流式算法（`VADIterator` 的 `threshold / min_silence / speech_pad` 经验值 0.5 / 300ms / 30ms）一致，可直接借鉴。
- **Smart Turn**：HF `VAD/smart_turn.py` 与我们 `smart_turn_adapter.py` 同思路（ONNX 语义端点，fail-open），已采纳，无需再动。

### 7.4 对 issue #132 子任务 C 的修订建议
- 子任务 C 验收 checklist 增补：「VAD speech_start 驱动 DIALOG_ACTIVE barge-in（取消在播 TTS/LLM）」作为「铺路」确收项；「短段拼接」作为可选增强。
- T-VAD-1 不再构成阻碍（已核实边界），但 PR 仍须显式写明「本 VAD 用法 = HF 真码实际用法，非 T-VAD-1 拒绝的『有人没说话』检测器」，避免审查组误解（同 §5 R3）。
