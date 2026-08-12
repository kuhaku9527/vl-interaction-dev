# KWS 静音误唤醒根因诊断 + 双麦克风差异方案（只读调研）

> 类型：只读调研（不改代码，仅核查事实 + 给结论）
> 日期：2026-08-12
> 依据：实机日志 `services/.logs/webui.err.log`（2026-08-12 11:03–11:14）、`services/asr/jarvis/kws.py`、`services/webui/src/joy_interaction_webui/jarvis_mode.py`、`services/webui/src/joy_interaction_webui/vad_bypass.py`、`services/scripts/run-windows.env`、`doc/research/kws-vad-bt-wakeword.md`、`doc/research/kws-v5-2026-08-10-diagnosis.md`、`doc/specs/2026-08-11-kws-asr-promotion-recall.md`

---

## 0. 结论先行（TL;DR）

1. **静音误唤醒是实锤，且根因不在"模型把静音听成 bt"单点，而是"静音误触发 + 绕过确认的兜底路径把误触发升级为最终唤醒"两条链叠加。**
2. 实机证据：08-12 11:06:35 会话启动 **0.7 秒内**（用户未说话、ASR partial 全程为空）、`peak=0.000 rms=0.000` 的纯静音 chunk 上 KWS 命中；1.2s 后 recovery probe 又在 0.48s 纯静音滚动缓冲上二次命中 → **Direct wake（绕过 ASR confirm）** → 播 wake.wav 进 DIALOG_ACTIVE。这就是用户感知的"点击按钮没说话就唤醒"。
3. **三根放大器**：① `JARVIS_VAD_SOFTGATE=false`（静音直接喂 KWS，零过滤）；② `JARVIS_KWS_THRESHOLD=0.20`（比默认 0.25 低，放低门槛）；③ `_wait_asr_confirm_timeout` 的 recovery probe 存在 **peak 兜底缺陷**（静音时把 peak 合成 0.5 继续跑 probe），把静音误触发从"被打回监听"升级为"最终唤醒"。
4. **08-11 SOFTGATE 实测教训（team 传达：误杀 ~75% 真实唤醒）**：Broadcast 麦降噪削尾音 + `bt` 仅 0.2–0.4s 短促音，Silero VAD（thr=0.5）大量判静音 → 软门控直接砍掉真实唤醒。**因此"开 SOFTGATE"不能作为独立方案**，必须级联能量门限做 AND 条件。
5. **推荐组合（优先级从高到低）**：
   - **P0 修 recovery probe 缺陷 + 给 fresh-window probe 加能量门限**（堵住静音缓冲误唤醒的直通路径，零误杀风险）；
   - **P1 在 `_handle_kws` 加静音能量门限**（`peak`/`rms` 低于阈值不喂 KWS，对 Broadcast 麦安全，对 gamechat 麦需自适应噪声底）；
   - **P1 阈值回 0.25**（sweep 证明 recall 无损失）；
   - **P2 按设备区分**（前端设备选择 + 后端参数映射）或统一自适应方案。

---

## 1. 实机证据链（webui.err.log 2026-08-12）

### 1.1 静音误唤醒完整时序（11:06 会话）

```
11:06:35,119  Jarvis session created; state machine started (KWS_LISTENING)
11:06:35,132  ICE completed（浏览器麦克风 track 绑定）
11:06:35,844  Wake word detected: 'bt' (peak=0.000 rms=0.000)   ← KWS 实时流命中
              → transition WAIT_ASR_CONFIRM
11:06:35,844~37,012  WAIT_ASR_CONFIRM ASR partial: ''  （连续 ~60 条，全部为空 = 纯静音）
11:06:37,043  Wake word detected in fresh PCM window: 'bt'
11:06:37,043  fresh-window KWS probe (0.48s peak=0.000 rms=0.000)  ← recovery probe 二次命中
11:06:37,048  WAIT_ASR_CONFIRM recovered via fresh-window KWS probe; direct wake without ASR confirm
11:06:37,048  Direct wake from fresh-window-kws → wake.wav → DIALOG_ACTIVE
```

**关键事实**：
- 会话启动 → KWS 命中仅 **0.725 秒**，用户此时不可能已说完"bt"；
- ASR confirm 窗口内 **所有 partial 为空**，确证麦克风收的是纯静音；
- recovery probe 的 0.48s 缓冲 = 纯静音缓冲，`detect_in_pcm` 依然命中。

### 1.2 对照：11:04 会话（正常唤醒，非静音）

```
11:04:38,629  capture peak=0.825 rms=0.113（有真实语音）
11:04:43,176  KWS shadow ASR partial: '对' (peak=0.825 rms=0.119)  ← 用户真实说话
11:04:48,204  fresh-window KWS probe (3.00s peak=0.825 rms=0.113)  ← 兜底命中（v3.21 设计意图）
```

对照组证明：**KWS 对真实语音能正常判别；probe 兜底路径本身没错，错的是它在纯静音缓冲上也会命中。**

### 1.3 同时段静音误唤醒多次复现（日志行号）

| 时间 | 事件 | 特征 |
|---|---|---|
| 11:06:35 | feed_audio 命中 | peak=0.000 |
| 11:06:37 | recovery probe 命中 → direct wake | 0.48s peak=0.000 |
| 11:08:51 | feed_audio 命中 | peak=0.000 |
| 11:08:53 | recovery probe 命中 → direct wake | 0.96s peak=0.000 |
| 11:12:34 | feed_audio 命中 | peak=0.000 |
| 11:12:35 | recovery probe 命中 → direct wake | 0.48s peak=0.000 |
| 11:09:00/31/40 | fresh-window probe 命中 | 1.0–1.08s peak=0.000 |

> 注：11:09 的几次是 `_handle_kws` 内 feed_audio miss 后触发的常规 probe（非 recovery），同样在 peak=0.000 上命中。

---

## 2. A. 静音误唤醒根因链（分层）

### 2.1 根因 0（最底层）：KWS 模型对静音/全零输入的系统性误触发

`kws.py` 链路（`JarvisKWS.feed_audio` / `detect_in_pcm`）把 int16 PCM 归一化为 float32 后**无条件喂给 sherpa-onnx KeywordSpotter**（`kws.py:113-114`），**没有任何能量/VAD 前置过滤**。模型本身对全零/近零输入的行为：

- `bt-en` v4 是自训模型（53 段正样本 + 200 段负样本，均为真实语音/噪声，`jarvis-mode.md`），**训练分布未覆盖"极端干净的全零/近零静音"**（Broadcast 降噪后麦克风输出的形态），模型在静音特征上输出非 blank 音素的概率偏离预期；
- `keywords_score=10.0`（`kws.py:31`, `run-windows.env:80`）对 keyword 路径强力 boost —— 直跑 FAR 就是 15.5%（`jarvis-mode.md §2.2`），是 FAR/recall 的甜蜜点但本身偏高；
- `keywords_threshold=0.20`（`run-windows.env:81`）比代码默认 0.25（`kws.py:32`）低 —— 进一步放低触发门槛；
- `num_trailing_blanks=1`（`kws.py:34`）：keyword 后只需 1 个 blank 帧即结算，静音段连续 blank 会立即触发结算。

**观测**：静音积累约 0.5–0.7s 即触发（11:06 会话 0.7s、0.48s 缓冲 probe 也命中），与"低阈值 + 高 boost 在静音特征上缓慢累积分数"的机理吻合。

### 2.2 根因 1：VAD SOFTGATE=false —— 静音零过滤直喂 KWS

- `run-windows.env:101/107`：`JARVIS_VAD_ENABLED=true`、`JARVIS_VAD_SOFTGATE=false`；
- `jarvis_mode.py:773`：软门控条件 `if self._vad.available and self.config.vad_softgate and not self._last_vad_speech: return` —— **SOFTGATE=false 时该分支永不生效**，VAD 只做注解（`feed_audio` 里 `_last_vad_speech` 照常更新），静音 chunk 全部进入 `_kws.feed_audio`；
- 实机日志确认 VAD 已加载可用：`[vad] Silero VAD loaded ... available=True` + `VAD bypass initialized (enabled=True available=True softgate=False)`（webui.err.log:146-147）。
- **贡献度**：不背首锅（模型本身会误触发），但它是"本该拦住却没拦"的失效防线。

### 2.3 根因 2：fresh-window probe 无能量门限 —— 静音缓冲照样触发

`jarvis_mode.py:921-957` `_probe_kws_fresh_window`：
- 只要 `_kws_capture_bytes >= min_s`（默认 1s，recovery 时 0.1s）就拼接滚动缓冲跑 `self._kws.detect_in_pcm(pcm)`（`jarvis_mode.py:940-942`）；
- **没有任何 peak/rms 检查**，传入的 `peak/rms` 只是拿来打日志；
- 滚动缓冲 `_kws_capture_chunks`（`jarvis_mode.py:575`, `_remember_kws_pcm`）**不区分语音/静音**，纯静音缓冲也能命中（11:06:37、11:09 多次）。
- 调用点：`_handle_kws` 在 `feed_audio` miss 后每 0.5s 最多一次（`jarvis_mode.py:779`）+ `_wait_asr_confirm_timeout` 超时前的 recovery probe（`jarvis_mode.py:1096`）。

### 2.4 根因 3（放大器/设计缺陷）：recovery probe 的 peak 兜底逻辑把静音误触发"升级为最终唤醒"

`jarvis_mode.py:1090-1096`（`_wait_asr_confirm_timeout`）：

```python
peak = getattr(self, "_last_wake_peak", 0.0)
rms  = getattr(self, "_last_wake_rms", 0.0)
if peak <= 0:
    byte_count = sum(len(c) for c in getattr(self, "_kws_capture_chunks", []))
    peak = 0.5 if byte_count > 0 else 0.0   # ← 静音触发时把 peak 合成 0.5
    rms = peak
if await self._probe_kws_fresh_window(peak=peak, rms=rms, bypass_min_s=True):  # ← probe 无能量检查
```

**缺陷**：当 feed_audio 在静音 chunk 上误触发（peak=0）进入 WAIT_ASR_CONFIRM，ASR 必然匹配不到 → 1.2s 超时 → 这里**人为合成 peak=0.5** 让 probe 继续跑 → probe 在滚动缓冲（可能仍全是静音）上命中 → `_direct_wake_from_kws` 绕过 ASR confirm 直接唤醒。**Hybrid confirm 的"防误唤醒"设计被这条路径完全击穿。**（该兜底本来是为 v3.23 的"live KWS 命中但 ASR 拼不出 bt"场景设计的，参数合成时没考虑静音误触发场景。）

### 2.5 根因 4（FAR 放大器，次要）：本地 ASR promotion

`run-windows.env:62` `JARVIS_ASR_PROMOTION_ENABLED=true`：KWS miss 时 shadow ASR 听到含 "b t" 子串的文本即 promote 唤醒（`jarvis_mode.py:1043-1070`）。`doc/specs/2026-08-11-kws-asr-promotion-recall.md` 已明示"开启会引入额外 FAR，用户选提召回优先"。它主要放大**语音侧**误唤醒（环境音被转出含 b/t 的文本），对纯静音场景不触发（静音 ASR 无文本），故列次要。

### 2.6 08-11 SOFTGATE 误杀 ~75% 真实唤醒的教训（为什么不能简单开 SOFTGATE）

- 数据来源：团队 08-11 实机测试结论（team-lead 传达）；仓库文档中对应的风险理论在 `kws-vad-bt-wakeword.md §5 R1`（"VAD 误杀短促/轻声 BT（最高风险）—— BT 仅 2 音节 + NVIDIA Broadcast 削尾音"）与 `kws-v5-2026-08-10-diagnosis.md §6`（"测 vad_miss_kill 误杀率 → 确认安全再开 vad_softgate"）均有记载，实测数字与之吻合。
- 机理：Broadcast 把 `bt` 的尾音/轻音削掉后，Silero VAD（`vad_bypass.py`, thr=0.50, min_speech=0.25s）把真实唤醒片判为静音 → `_handle_kws:773` 直接 return → KWS 连听的机会都没有 → 真实唤醒大面积丢失。
- **结论**：SOFTGATE 单独开启 = 重演 75% 误杀。任何启用 SOFTGATE 的方案必须附带能量级联（见 §4 方案 S2）且先在真实样本上测 `vad_miss_kill`。

### 2.7 根因链总览

```
[模型层] bt-en v4 对全零/近零输入误触发
   × score=10.0 boost + th=0.20 低门槛 + trailing_blanks=1 快结算
   ↓
[防线失效] VAD SOFTGATE=false，静音 chunk 直喂 KWS（无过滤）
   ↓
[路径1] feed_audio 持久流在静音上命中 → WAIT_ASR_CONFIRM
   ↓（ASR 匹配不到 → 1.2s 超时）
[路径2] recovery probe：peak 兜底缺陷(合成0.5) + probe 无能量门限
   → 静音缓冲二次命中 → Direct wake 绕过 confirm → DIALOG_ACTIVE
   ↑
（并行）_handle_kws 内常规 fresh-window probe 也常在静音缓冲上命中
```

---

## 3. B. 双麦克风差异分析

### 3.1 NVIDIA Broadcast 麦（降噪）

| 现象 | 机理 |
|---|---|
| 易唤醒 | 降噪后语音信号干净，KWS 判别容易 |
| **静音误唤醒（实锤）** | 降噪后**静音=真·全零**（peak≈0.000），KWS 模型对全零输入误触发（§2.1） |
| 语音识别正常不卡 | 说话时信号干净，ASR endpoint 的 2s 静音规则正常工作 |
| 潜在：真实唤醒被 VAD 误杀 | 降噪削尾音 → Silero 判静音 → SOFTGATE 误杀（08-11 教训） |

**关键推论**：对 Broadcast 麦，**静音就是真静音** —— 能量门限/VAD 过滤是**安全**的（真实唤醒必有能量，不会误杀），可以放心过滤。

### 3.2 gamechat 麦（无降噪，环境音大）

| 现象 | 机理 |
|---|---|
| 难唤醒 | 持续环境音进入 KWS 流，噪声污染声学特征 + 淹没短促 `bt` 信号（类似 v3.21 长流边界污染 miss） |
| 静音误唤醒少 | 输入始终非全零，恰好绕开"全零输入误触发"这一特定缺陷 |
| **卡 ASR 一直监听** | 环境音被 streaming-paraformer 当成持续语音，`_handle_dialog` 里 `_last_speech_time` 被不断刷新的 partial 持续更新，2s 静音 endpoint 永不触发（`jarvis_mode.py:1228`）；ASR 侧 `rule1_min_trailing_silence=2.0` 同样不触发 |

**关键推论**：对 gamechat 麦，能量门限不能设死（环境音底噪可能很高），需自适应噪声底；卡监听是**独立问题**（endpoint 对持续环境音的失效），不是 KWS 误唤醒的延伸。

### 3.3 两麦问题本质不同 → 方案需要区分或自适应

| | Broadcast 麦 | gamechat 麦 |
|---|---|---|
| 主要病 | 静音误唤醒 | 召回低 + 卡监听 |
| 病根 | 全零输入 + 低门槛 + 无过滤 | 环境音淹没信号 / 污染 KWS 流 / 干扰 endpoint |
| 能量过滤安全性 | 安全（静音=真静音） | 需自适应（底噪本身有能量） |
| 对症药 | 静音能量门限 / 阈值 / probe 门限 | 前端降噪预处理 / 设备区分后提高能量门限 + ASR endpoint 调大 |

---

## 4. 候选方案与风险矩阵

### S1（P0，代码，推荐必做）：fresh-window probe 加能量门限 + 修复 recovery peak 兜底缺陷

改动点（`jarvis_mode.py`）：
- `_probe_kws_fresh_window`：入口加 `if peak < kws_probe_min_peak`（建议 0.005–0.01）`return False`；或对拼接的 `pcm` 重算 rms 判断。
- `_wait_asr_confirm_timeout`：删除 `peak <= 0 → 合成 0.5` 的兜底，改为 `peak <= 0 and 缓冲无非静音` 时**直接回 KWS_LISTENING，不跑 probe**。

| 收益 | 风险 |
|---|---|
| 直接堵死"静音缓冲 → probe → direct wake"整条绕过 confirm 的路径（本次实锤的最终放大器） | 极低：仅影响静音场景；真实唤醒 peak 必然 > 门限。需注意门限不要高到砍掉轻声（建议用当前 `kws_capture_peak_threshold=0.035` 的 1/3~1/7 量级） |

### S2（P0，配置，推荐）：VAD 软门控开启 + 能量级联（AND 条件），绝不一刀切

把 `_handle_kws:773` 的跳过条件从 `softgate and not speech` 收紧为 `softgate and not speech AND peak < 0.005`（代码改动 + env 翻 `JARVIS_VAD_SOFTGATE=true`）。

| 收益 | 风险 |
|---|---|
| 只拦"VAD=静音 **且** 能量几乎为零"的 chunk；VAD 判静音但能量不低（可能的轻声 bt）仍喂 KWS | **规避 08-11 的 75% 误杀教训**：单纯 SOFTGATE=true 必须重新实测 `vad_miss_kill`（`kws-v5-2026-08-10-diagnosis.md §6` 方法），误杀率不可接受就保持 AND 能量级联或仅用能量门限 |

### S3（P1，纯配置，低风险）：`JARVIS_KWS_THRESHOLD` 0.20 → 0.25

- sweep 已证明 recall 完全不变（49.06%，`jarvis-mode.md §14.12`：`th=0.25` 与 `th=0.20` 召回/FAR 同为 49.06%/2.00%）；
- 若静音误触发的分数集中在 0.20–0.25 区间，回 0.25 可直接消掉一部分；
- **注意**：jarvis-mode.md 明确"不要盲降 threshold 求召回"，反之回 0.25 是无损动作。但若静音分数 >0.25，此方案不够，仍需 S1/S2。

| 收益 | 风险 |
|---|---|
| 零召回损失，可能边际降静音 FAR | 效果不确定（取决于静音分数的分布，需看 KWS 命中时的 score 日志） |

### S4（P1，代码，推荐）：`_handle_kws` 前端能量门限（不依赖 VAD 判定）

在 `_handle_kws`（`jarvis_mode.py:775` `_observe_kws_diagnostics` 之后、`feed_audio` 之前）加：`if peak < kws_min_peak: return`（对 feed_audio 与 probe 都生效）。

- **Broadcast 麦**：门限 0.005–0.01 即可，安全（静音=真静音）；
- **gamechat 麦**：底噪能量可能 >0.01，需**自适应噪声底**（跟踪最近 N 秒窗口内 rms 的 p5/p10 作为门限基准，门限 = max(固定值, 噪声底 × k)），或配合前端降噪（S6）。

| 收益 | 风险 |
|---|---|
| 不依赖 VAD 判定，无 VAD 误杀风险；Broadcast 麦立竿见影 | gamechat 麦固定门限会误杀轻声唤醒 → 必须自适应；固定门限若过高砍掉真实唤醒（复用 75% 教训的担忧） |

### S5（P1，配置）：`JARVIS_ASR_PROMOTION_ENABLED` 回 false（或加 rms 门限）

promotion 本身是 FAR 放大器（`2026-08-11-kws-asr-promotion-recall.md` 已明示）。召回优先 vs FAR 优先需用户权衡；若主要痛点是误唤醒，先关掉或对 promotion 加能量下限。

| 收益 | 风险 |
|---|---|
| 消除语音侧 promotion 误唤醒 | 牺牲 recall booster（该 boost 主要救"轻声 bt"，对 Broadcast 麦增益有限） |

### S6（P2，前端）：麦克风降噪/门控预处理 + 设备区分

- 前端 `getUserMedia`（index.html:4295）可加 `noiseSuppression: true`（当前 false）或 Web Audio 里加 highpass + noise gate，对 gamechat 麦同时缓解"难唤醒"和"卡监听"；
- 前端加**麦克风设备选择器**（当前无 UI，靠系统默认设备切换），把 `device.label`/`deviceId` 随 offer 传给后端，后端按设备类型选参数（Broadcast：低能量门限 + 可开 SOFTGATE；gamechat：高/自适应门限 + ASR endpoint `rule1_min_trailing_silence` 调大）。

| 收益 | 风险 |
|---|---|
| 按麦精准调参；根治 gamechat 的卡监听与难唤醒 | 改动面大（前后端联动）；设备 label 需权限才能读；WebRTC `noiseSuppression:true` 可能像 Broadcast 一样削尾音（需实测） |

### 方案对比汇总

| 方案 | 层面 | 对静音误唤醒 | 对 Broadcast | 对 gamechat | 误杀真实唤醒风险 | 工作量 |
|---|---|---|---|---|---|---|
| S1 probe 门限 + recovery 修复 | 后端代码 | **根治（堵绕过 confirm 的直通路径）** | 安全 | 安全 | 极低 | 小 |
| S2 SOFTGATE+能量 AND | 后端代码+env | 显著缓解 | 安全（AND 级联） | 需测 | **中（须先测 vad_miss_kill）** | 小 |
| S3 阈值回 0.25 | 配置 | 边际 | 安全 | 无 | 无 | 零 |
| S4 前端能量门限(自适应) | 后端代码 | 显著缓解 | 安全 | 需自适应 | 低（自适应后） | 中 |
| S5 关 promotion | 配置 | 消除语音侧误唤醒 | 牺牲召回 | 无 | 无 | 零 |
| S6 设备区分+前端降噪 | 前后端 | 根本性 | 精准 | 根治卡监听/难唤醒 | 低（noiseSuppression 需测） | 大 |

---

## 5. 推荐落地组合

### 优先级 P0（最小改动，先止血，零误杀风险）
1. **S1**：`_probe_kws_fresh_window` 加能量门限；修复 `_wait_asr_confirm_timeout` 的 peak 兜底缺陷（静音时直接回 KWS_LISTENING）。—— 直接消除本次实锤的"静音 → direct wake"完整路径。
2. **S3**：`JARVIS_KWS_THRESHOLD` 回 0.25（无损）。

### 优先级 P1（观察 1–2 天后决定）
3. **S2**：`JARVIS_VAD_SOFTGATE=true` **必须带能量级联**（`softgate AND peak<0.005`），上线前用 `analyze_kws_captures.py` 对现有 capture（`D:/AI/data/kws/mic_captures/` 242 个样本）测 `vad_miss_kill`；误杀率不可接受则不启用或纯能量门限替代。
4. **S4**：`_handle_kws` 静音能量门限（峰值门限 0.005–0.01；对 gamechat 用自适应噪声底）。此方案对 Broadcast 麦与 S2 等价且更简单——**若团队想完全绕开 VAD 误杀风险，可只做 S4 不做 S2**。
5. **S5**：与用户确认 FAR/recall 权衡后决定是否关 `JARVIS_ASR_PROMOTION_ENABLED`。

### 优先级 P2（中长线）
6. **S6**：前端麦克风设备选择器 + 按设备参数映射 + （可选）WebRTC noiseSuppression / Web Audio noise gate。这也是 gamechat 麦"难唤醒 + 卡 ASR"的唯一根治路径。

### 特别提示（避免重蹈 08-11 覆辙）
- **任何方案上线前必须用真实样本测误杀率**（`analyze_kws_captures.py` 的 `vad_miss_kill` 列 + shadow ASR 对照），误杀率 > 0 时不启用硬门控（`kws-vad-bt-wakeword.md §5 R1` / `kws-v5-2026-08-10-diagnosis.md §6`）。
- **不要单纯把 SOFTGATE 打开** —— 08-11 实测已证明它误杀 ~75% 真实唤醒。
- KWS 静音误触发的**终极修法**是训练侧（v5 补"全零/近零静音"负样本 + 扩正样本），推理侧方案（S1–S4）只是止血。

---

## 6. 附：关键代码定位

| 位置 | 作用 |
|---|---|
| `jarvis_mode.py:760-826` | `_handle_kws`：KWS 主路径（softgate 检查 → feed_audio → probe → shadow ASR） |
| `jarvis_mode.py:773` | VAD 软门控条件（SOFTGATE=false 时永不生效） |
| `jarvis_mode.py:921-957` | `_probe_kws_fresh_window`：无能量门限 |
| `jarvis_mode.py:1087-1106` | `_wait_asr_confirm_timeout`：recovery probe + **peak 合成 0.5 缺陷** |
| `jarvis_mode.py:959-981` | `_direct_wake_from_kws`：绕过 ASR confirm 直接唤醒 |
| `kws.py:95-130` | `feed_audio`：无过滤直喂 KeywordSpotter |
| `kws.py:132-160` | `detect_in_pcm`：fresh-window 干净流重放 |
| `vad_bypass.py:141-149` | `is_speech()`：fail-open，silence → False |
| `run-windows.env:80-107` | 阈值 0.20 / SOFTGATE=false / VAD_ENABLED=true |
| `asr.py:45-49` | ASR endpoint 规则（rule1 静音 2.0s） |
| `index.html:4295-4300` | `getUserMedia`（无设备选择，noiseSuppression=false） |
