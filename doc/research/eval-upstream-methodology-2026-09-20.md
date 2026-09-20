# 上游评测方法学侦察：JoyAI-VL-Interaction 如何定义「决策判断得对」

> 端点：**调研 / AFK（只读）**
> 日期：2026-09-20
> 对象：上游论文 `JoyAI-VL-Interaction-Reportv1.pdf`（21 页）+ 上游仓库 `jd-opensource/JoyAI-VL-Interaction` + 官方 blog
> 目的：为本地「决策能力」评测建立**同构但轻量**的判据
> 边界：本文只做侦察，不改动本仓库其他文件

---

## 0. 一句话结论

**上游把「决策正确」当作人评的一个轴（timing）来测，而不是当作指标来算**：58 个案例、5 名评审、quality/timing 各占一半权重、三级量表（good/fair/poor），最终只报 `win/tie/loss` 与胜率——**全篇没有一个 precision / recall / F1 / PAUC / 时序 IoU 数字，也没有「误触发 vs 漏触发」的代价不对称**；态度上反而是**反向**的：训练目标里 `w_response = 1.5 > w_silence_first = 1 > w_silence_repeated = 0.4`，即**主动压低沉默、抬高发言**。因此上游方法学**可以照搬其「每秒动作 + 时序轴」的形式**，但**指标层必须整体替换**，且它没有本地 `not-for-me` 这一个决策维度的任何对应物。

---

## 1. 上游的评测方法学（§5 拆解 + 原文引用）

### 1.1 §5.1 Benchmark：不测离线基准，测「对打产品」

> "We evaluate JoyAI-VL-Interaction **not on offline video-understanding benchmarks but against the real, deployed products** people would actually reach for, in the live, event-driven setting this paper is about. Concretely, we compare it head-to-head with the in-app video-call assistants of Doubao and Gemini" — p.12 §5.1

- 基线：豆包 / Gemini 的**应用内视频通话功能**（封闭产品，非模型权重）。
- 驱动方式：**无系统级 API**，因此「播放同一段视频、在匹配的时间戳提同样的问题」，两两对比。
  > "Since Doubao and Gemini expose no system-level API, we drive them through their apps: we play the same video to each and pose identical questions at matched timestamps." — p.12 §5.1
- 本地模型跑自家系统，视频经 **MediaMTX over RTSP** 伪装成直播流。
- 系统配置记了超参：三层记忆 `Ts = 100 s, M = 5, L = 15`。

### 1.2 规模：58 案例，六场景

> "The six scenarios comprise **58 cases in total: 10 each for monitoring, counting, translation, and time awareness, and 9 each for commentary and memory**. The cases are drawn mostly from public footage on the web, with a few recorded by us." — p.12 §5.1

### 1.3 Protocol and metric：**这是全文对「决策正确」的核心定义**

> "For every case, human raters score each system on **two equally important axes**: **quality**, whether the response is **correct, relevant, and well-formed**; and **timing**, whether it arrives **at the right moment, neither premature nor late, and whether the system stays silent when nothing is worth saying**. Each axis is rated on a **three-level scale, good, fair, or poor**, and a system's score for the case is the **equal-weight average of its two axis ratings**." — p.12–13 §5.1

关键点：
- **「决策正确」= timing 轴**，其内涵被明确定为三条：① 不早；② 不晚；③ **该沉默的时候沉默**。
- 两条轴**等权**（explicitly "equally important"），quality 与 timing 各 0.5。
- 打分是**序数三级量表**（good/fair/poor），不是二值正确/错误。

### 1.4 偏置控制与最终指标

> "The timing axis is **the crux of the event-driven setting** and the one turn-based products structurally struggle with (§1). To reduce bias, **system identities are hidden from raters and the presentation order is randomized**. **Five raters** carry out the ratings, all educated to at least the university level and working as researchers in LLMs, and **inter-rater agreement is high**." — p.13 §5.1

> "For each case we then compare the two systems' **weighted-average scores**, which gives a **win, tie, or loss** for JoyAI-VL-Interaction. We report the **per-scenario and overall win rate**, the fraction of comparisons it wins … **a tie counts as neither a win nor a loss**." — p.13 §5.1

**最终指标 = win rate**（胜率），per-scenario + overall，tie 单列。（Table 1 / Table 2，p.13）

### 1.5 §5.2 结果叙事：胜在 timing，输在 quality

- 整体：对豆包 77.6% 胜 / 17.2% 平 / 5.2% 负；对 Gemini 87.9% / 10.3% / 1.7%。
- 最强项是**时间最敏感**的场景：monitoring & alerting 对两家均 100%；translation、counting 对 Gemini 100%，对豆包 80% / 70% 且**零负**。
- 唯一让豆包拿分的是 **live commentary**（豆包 22.2% 胜 / 22.2% 平 vs 本模型 55.6%），且论文明确说这是 **quality（模型规模带来的知识/文风）** 而非 timing：
  > "Its edge here is one of **quality rather than timing** … On commentary, however, **its timing works against it**: the responses are temporally erratic, arriving too frequently in some passages and too sparsely in others" — p.14 §5.2
- 唯一让 Gemini 拿分的是 **time awareness**（50% / 40% 平 / 10% 胜），原因也非能力而是**题目性质**：
  > "some time-awareness cases are user-triggered, with the question asked only after the relevant moment has passed, so the real-time demand is low." — p.14 §5.2
- **对自己失分的归因**：全部落在 quality 上（commentary 偶发幻觉），归因于 8B 规模，**没有归因到任何决策/时序失败**。

### 1.6 §5.3 自认的评测局限（对本地设计很有用）

> "The evaluation is similarly preliminary, **six scenarios and 58 human-rated cases against two products**, rather than the larger, more fine-grained study we ultimately want." — p.15 §5.3

> "it is the **data-construction methodology and the approach itself, rather than this particular corpus or benchmark**, that we expect others can most readily adapt and extend." — p.16 §5.3

即上游自己**不建议把这份 benchmark 当作可复现标尺**，而建议借鉴其**数据构造方法学**。

### 1.7 离线侧（与决策评测无关，勿混用）

README 与 blog 另报「26 个标准视频理解基准，均分 57.53 vs Qwen3-VL-8B-Instruct 54.16」。**这只是图 `img/benchmark_table.png`，仓库里没有任何 harness**，且测的是离线视频理解，与每秒决策能力无关。

---

## 2. 上游「决策正确」的定义与指标（汇总）

| 维度 | 上游做法 | 是否可量化 |
|---|---|---|
| **决策空间** | 三态 `</silence>` / `</response>` / `</delegation>`（delegation 必须骑在 response 内），**每秒一次** | 是（标签层已量化） |
| **决策正确** | timing 轴：不早 / 不晚 / **该沉默时沉默** | 否（三级人评，序数） |
| **内容正确** | quality 轴：correct / relevant / well-formed | 否（三级人评，序数） |
| **聚合** | 两轴等权平均 → 案例分 | 序数平均 |
| **对比** | 逐案例 win/tie/loss，报 per-scenario + overall 胜率 | 是（但只对自己 vs 基线有意义） |
| **时序数值** | **未发布**任何 onset 延迟/误差表；仅 §5.2 案例叙述中出现「晚 4–5 秒」「差 1–2 秒」「约 40 秒」等散点 | 否（叙事，非指标） |
| **一致性** | "inter-rater agreement is high"——**未给 κ / α 数值** | 否 |
| **误触发/漏触发代价** | **评测层完全没有区分** | 否 |

**训练侧倒是有量化定义**（但这不是评测指标，且无公开数值）：

- 监督信号：加权交叉熵，`w_first_silence = 1`、`w_repeated_silence = 0.4`、`w_response = 1.5`（p.8 §3.3, Eq.1）。
- RL（GRPO）奖励：
  > "The reward credits responses that are both correct and **emitted within the right window**, rewards **appropriate silence**, and rewards well-judged delegation; it **penalizes false alarms (speaking or delegating with no cause), mistimed responses, and degenerate always-respond behavior**." — p.9 §3.3

  这是全篇**唯一**把「误触发」写成惩罚项的地方——**但只出现在训练奖励，不在 §5 的评测里，也没有公开权重**。

---

## 3. 上游 six real-world streaming scenarios：具体是哪些，正确行为是什么

论文 §5.1 只给一句话定义；**逐案例名称需从官方 blog 补齐**（论文未列案例名）。

| # | 场景（论文原文） | 论文给的「正确行为」 | blog 里的具体案例 | blog 给的判对标准（原文要点） |
|---|---|---|---|---|
| 1 | **Monitoring and alerting** — "flagging an event **the instant it occurs**" | 事件发生**当刻**报警，不早不晚 | Yellow-card Alert；**Fall Detection Alert** | "The right response is **neither early nor late**: it should arrive **when the watched event occurs**" |
| 2 | **Real-time counting** — "counting of objects or events **over time**" | 每次事件发生**在下一次**正确递增，维持跨事件状态 | Dart Throw Counting；Burpee Counting | "maintain state across repeated events, **not just recognize a static object**"；论文：6 镖全中 vs 豆包 2 且延迟（p.14） |
| 3 | **Real-time translation** — "translation of on-screen content" | **连续性**：每条新字幕出现即译，不漏 | Pharmacy Visit Animation；Street Interview Translation | "the task being to **translate the interview's on-screen subtitles in real time rather than its audio**"（p.14）；"catching every new subtitle the moment it changes **with none left out**" |
| 4 | **Time awareness** — "acting on its own **sense of elapsed time**" | 到点即动：按请求间隔说话 / 报出活动时长 | Timed Cooking Scene（20s 后提示）；Stove Cleaning Timer | 论文：本模型差 **1–2 秒**算过，Gemini ~40 秒（翻倍）算失败（p.14）；另一案例要求**每 4 秒**播报一次并守住节奏（p.15） |
| 5 | **Live commentary and guidance** — "narrating or walking the user through an unfolding scene **at the right moments**" | 既要在该说时说、也要在无话可说时**保持沉默** | Pet Livestream Commentary；Travel Scene Commentary | "Commentary is not just captioning. It requires the model to decide **when a scene deserves narration and when silence is better**" |
| 6 | **Long-horizon memory** — "answering about something seen **far earlier** in the stream" | 后问早前所见，答案正确 | Meatball Count Recall | blog：案例多为**数分钟到十余分钟**；论文自曝基线因**会话超时**（豆包 ~5min、Gemini ~2m15s）根本不在场，半数 memory 案例是「结构性地赢」而非能力赢（p.13） |

补充：论文说六场景是三条底层能力的**采样**：
> "Underneath the six lie the three capabilities that define an interaction model: **real-time operation, proactive response driven by what it sees, and long-horizon memory**." — p.12 §5.1

blog 另把 App guidance / Visual-driven interaction / Agent Delegation 单列为 capability（09 项），**但它们不在 58 案例的六场景口径内**——本地若想对齐，只应取上表六类。

---

## 4. 上游有没有可复用的评测代码 / 数据

### 4.1 结论：**评测代码没有；数据标注格式有，且很有用**

逐条核实（`gh api repos/.../git/trees/main?recursive=1` 全量 182 个路径）：

| 想要的 | 实际情况 |
|---|---|
| 人评 harness / 打分表 | **不存在**。仓库无 `eval/`、`benchmark/`、`scripts/eval*`、无任何 win-rate 计算代码 |
| 58 案例的题目集 / 视频 | **未发布**。只有 blog 上的并排录屏 demo |
| 离线 26 benchmark 的跑分脚本 | **不存在**，只有结果图 `img/benchmark_table.png` |
| 仓库内唯一的 test 目录 | `install/tests/`（`verify_real_env.py`、`run_real_env_tests.sh`）→ 环境自检，**非能力评测** |
| `services/webinfer/smoke.py` | 冒烟，**非评测** |
| **数据标注 schema** | ✅ **有**，见 `datasets/README.md` |
| 标注 → 每秒标签的转换脚本 | ✅ **有**，`datasets/convert_data.py` |

### 4.2 真正可复用的东西：`datasets/README.md` 的 ground-truth 格式

上游标注就两个字段带时间，**这正是「决策正确」的可量化载体**：

```json
[
  {
    "video_name": "name.mp4",
    "video_path": "/path/to/videos/name.mp4",
    "task_type": "task_category",
    "source": "source_dataset",
    "question": [{"content": "user question or instruction", "time": "4"}],
    "response": [{"content": "expected answer or event response", "time": "7"}]
  }
]
```

> "`question`: … `time` is the **timestamp in seconds when the prompt is issued**. `response`: … `time` is the **timestamp in seconds when the answer or event should occur**."

`convert_data.py` 再把它展开成**每秒一条**的训练样本：t=0..6 全 `</silence>`，t=7 出 `</response> <答案>`（见 `datasets/README.md` §4，与论文 Appendix Listing 1–3 / p.19–21 完全一致）。

**本地可直接借用这个 schema 来写 ground truth**：一个 `(question, time)` / `(event, time)` 对就是一条「金标准触发时刻」，天然支持后续的 onset 延迟与误触发统计。**这是本次侦察里性价比最高的复用点。**

### 4.3 复用限制（重要）

- 数据在 HF `jdopensource/JoyAI-VL-Interaction`，**只发标注、不发视频**——`source` 字段指回原数据集，视频要自己另取（体积大，本次**未下载**，符合只读约束）。
- `task_type` 是**训练六大族**口径，**不是** §5 的六评测场景，两者不可混用。
- 语言/场景是**英文为主的通用网络视频**，与本地中文桌面 + 会议/陪伴场景分布不同。
- 本地上游 PDF 与仓库内同名 PDF **完全相同**（均 5,020,917 bytes；本地 `sha256` 前缀 `29223b04b572a587cc83`，upstream blob sha `12cd744752099ba4ee28b9c94ed876a532e943a8`），因此论文即最新版，无需另找。

---

## 5. 指定能力的度量（vision-triggered responsiveness / time awareness）

**论文对这两项都没有给出任何指标定义。** §1 只说模型 "excels at vision-triggered responsiveness and time awareness"（p.1 Abstract），§5 把它们**折进 timing 人评轴**，不单独量化。

**上游事实上的（未正式化的）度量，散落在 §5.2 案例叙述与 blog**：

| 能力 | 上游事实上的度量 | 证据 |
|---|---|---|
| **Vision-triggered responsiveness** | **触发相对事件发生的延迟**（秒） | Fall Detection："raises the alert **at the instant of the fall**, whereas Doubao reacts **four to five seconds later**"（p.14）；"it is the **polling interval** of an external trigger surfacing as latency"（p.14） |
| 同上 | **事件召回数 / 总数** | Dart throw："increments its count **exactly as each dart strikes** … registering **all six** throws on time, while Doubao counts only **two** and with noticeable delay"（p.14） |
| 同上 | **连续性：是否单向一次性答复** | Street Interview："both Doubao and Gemini translate only what was visible at the moment the request was issued and **then stop, treating an ongoing task as a single turn**"（p.14） |
| **Time awareness** | **绝对时序误差** \|t_actual − t_target\| | Timed cooking："asks the system to signal once a **twenty-second** interval has elapsed … off by only **one to two seconds**; Doubao fails … Gemini … at around **forty seconds**, roughly **double the target**"（p.14） |
| 同上 | **节奏保持率**（能否守住「每 N 秒」） | Travel commentary："asked to narrate **once every four seconds** … holds to this cadence throughout"（p.15） |

**结论**：这两个能力的上游「定义」= ① onset 延迟（秒）② 事件召回率 ③ 节奏维持。**全部只在正文叙述里出现，没有表格、没有分布、没有容差定义**。本地若要量化，需要自己拍容差（上游隐含容差：±1–2 秒算通过，2× 偏差算失败——可作锚点）。

---

## 6. 代价偏好：上游有没有体现「误触发比漏触发更不可接受」

### 6.1 评测层：**明确没有，且是等权**

> "two **equally important** axes"；"a system's score for the case is the **equal-weight average** of its two axis ratings" — p.12–13 §5.1

一次误触发（不该说却说了）和一次漏触发（该说却没说）在 timing 轴上**都只是把该轴从 good 降到 fair/poor**，**没有权重差异**，也没有任何 FPR/FNR 分列统计。win-rate 口径下更无法表达不对称。

### 6.2 训练层：**有，但方向与本地相反**

| 证据 | 方向 |
|---|---|
| SFT 权重 `w_repeated_silence = 0.4`（**压低**沉默续接）、`w_response = 1.5`（**抬高**发言起始）、`w_first_silence = 1`（p.8 §3.3 Eq.1） | ⚠️ **鼓励说话**，理由写在论文里："**silence steps vastly outnumber the steps on which the model speaks**, so the supervised targets are dominated by the `</silence>` token. Under a standard SFT loss this imbalance pushes the gradient toward continued silence and **dilutes the signal for responding**." |
| RL 惩罚项含 "false alarms (speaking or delegating with no cause)" **且** "mistimed responses" **且** "**degenerate always-respond behavior**"（p.9 §3.3） | 双向惩罚，无公开权重；`degenerate always-respond` 说明上游**也警惕过度发言** |
| 部署默认：`FORCE_SILENCE_BEFORE_QUERY=true` —— "without a user question, the adapter returns `</silence>` directly and **does not call the main model**" | ✅ 工程默认偏保守（**与本地偏好同向**） |
| UI 暴露 `response mode`：default balance / **more talkative** / **more aloof**（p.10 §4.2） | 把「话痨↔高冷」做成**用户可调旋钮**，而非固定代价偏好 |

**净结论**：上游**在工程默认上保守、在训练目标上激进、在评测上中立**。它从未声称「误触发更不可接受」；相反，它把这种权衡**外化为一个 UI 旋钮**并让用户自选。

### 6.3 本地既有研究与之的冲突（需注意）

本地 `doc/research/addressee-detection-2026-08-12/04_hybrid_approach.md:498` 写的是：

> "可根据场景调整：陪伴型代理可偏向**高召回（宁可误触发，不可漏响应）**"

这与用户当前拍板的 **「宁可漏、不可乱插」** 正好相反，是云端调研期（2026-08-12）的默认假设。**这是一处历史漂移**，为避免后续端点误用，建议由负责该报告端点在定稿时对齐（本次只读，不改动，仅提示）。

---

## 7. 与「宁可漏不可乱插」的匹配度分析

| 上游要素 | 能否支持本地偏好 | 说明 |
|---|---|---|
| 每秒三态动作形式（silence/response/delegate） | ✅ **直接可搬** | 本地的 silence/response/delegate 与之同构；格式（`</silence>` 行 + `<t seconds>` 前缀）可直接照抄 |
| ground-truth schema（`question.time` / `response.time`） | ✅ **直接可搬** | 天然给出「金标准触发时刻」，是 onset 延迟与误触发的唯一必需输入 |
| timing 轴内涵：「该沉默时沉默」 | ✅ **概念同向** | 这是上游最接近本地偏好的地方——它把「无效发言」正式列为决策错误 |
| cost-sensitive 指标（FP 权重 > FN） | ❌ **缺失，必须自建** | 上游等权；win-rate 无法表达不对称 |
| **`not-for-me` 维度** | ❌ **完全没有对应物** | 上游 vision-first、语音为可插拔 I/O，**不存在「这句话不是说给我听的」这一决策**。本地四态比上游多**一个决策维度**，无法照搬 |
| 有/无基线的评测框架 | ❌ **不适用** | win-rate 需要对手；本地是要**给单模型标定阈值**，需要绝对指标（P/R、FPR、代价曲线），不是相对胜率 |
| 三级序数量表 + 5 人评 | ⚠️ 可保留但不作主指标 | 人评可留作定性佐证；不可作为可复现 CI 判据 |
| 训练侧「抬高发言」的权重设计 | ❌ **反向，不可照搬** | 本地若沿用 `w_response=1.5` 会直接**放大误触发** |

**判定**：上游方法学与本地偏好 **形式同构、指标层不可用**。可复用的是「每秒动作 + 带时刻的 ground truth + 时序轴概念」；**必须替换的是整个指标与权重取向**，并**新增 not-for-me 维度**。

---

## 8. 对本地轻量评测的设计建议（照搬 / 必须改）

### 8.1 照搬（低成本、已验证的骨架）

1. **每秒动作数据格式**：沿用 `<t seconds>` + `</silence>` / `</response>...` / `</delegation>...`（论文 p.19–21 Listing 1–3 / `datasets/README.md` §4）。本地再加 `</not-for-me>`（本地独有）。
2. **ground truth = 带时刻的事件/问题对**（`question.time` / `response.time`），不必标每一秒——转换脚本按 1 Hz 展开即可（照抄 `convert_data.py` 的语义，无需其代码）。
3. **per-scenario 分场景报数**，不只报总分（上游 Table 1/2 就这么做，且它揭示出「time awareness 是唯一被追平的场景」这种关键信息）。
4. **±1 秒粒度即可**：上游 1 fps、1 秒决策粒度，且把 1–2 秒误差判为通过（p.14），本地**不必追求亚秒指标**。
5. **保留「平局」概念**（若做 A/B）：上游 tie 单列不折算，避免把「都对」摊成胜负。

### 8.2 必须改（三处，按重要性排序）

**① 指标：从 win-rate 换成代价加权 + 阈值扫描。**
本地是**单模型定阈值**，没有对手可打。替代方案（三级，逐级加严）：

- **主指标（操作点）**：`Cost/min = (C_FP × N_FP + C_FN × N_FN) / 分钟数`。
  按用户偏好取 `C_FP : C_FN = 10 : 1`（**该比值应写成配置项**，便于日后调整）。
- **副指标（阈值无关，用于选型/回归）**：在 **FPR ≤ 目标上限**（如 ≤ 2 次/分钟）区间内最大化召回，报 **partial AUC / precision@固定召回**。这正是**阈值无关**的好处——不受主观代价比绑架。
- **健康度闸门（防退化）**：`response_rate` 上限 + `always-respond` 检测（上游 RL 里 "degenerate always-respond behavior" 的评测版）。**这一条与本地偏好同向，直接照搬其思路。**

**② 维度：把四态拆成两条正交轴，不要合成一个准确率。**

本地四态其实混了两个不同的问题：

| 轴 | 问题 | 上游对应 | 建议指标 |
|---|---|---|---|
| **A 定向轴（本地独有）** | 这句话/这个画面**是不是冲我来的** | **无** | precision / recall / **FAR（False Accept Rate）** 于 `not-for-me` 类；`baseline_mis_response_rate_pct` 直接报 |
| **B 时序轴** | 该说时**是否说对时刻**、不该说时**是否沉默** | §5 timing 轴 | onset 延迟中位/p90（秒）、**premature rate**（早于金标准占比）、**FPM**（False Positive per Minute，用户实际体感数） |

**强烈建议不要**把 A、B 合成单一 accuracy——`doc/research/data/benchmark_4state_notforme_results.json` 已经是这个「矩阵 + 分轴比率」的正确形状（含 `baseline_mis_response_rate_pct`、`not_for_me_precision_pct`、`directed_miss_rate_pct`），**建议沿用它并补齐 FPM 与延迟分布**，而不是引入 win-rate。

**③ 权重取向：明确反转上游训练侧的 1.5 / 0.4。**

- 若本地做任何微调/prompt 调优，**不得**沿用 `w_response > w_silence`；应改为 `w_silence_first ≥ w_response`（或干脆不引入该权重）。
- 部署默认**向上游工程侧对齐**（`FORCE_SILENCE_BEFORE_QUERY=true` 式的保守默认），并把「话痨度」做成显式配置而非隐式训练偏好——上游 `response mode` 三档（balance / talkative / aloof）（p.10 §4.2）是可直接借鉴的**产品化形态**，本地偏好的档位就是 **aloof 端**。

### 8.3 不建议做的

- ❌ 不要复刻 58 案例人评流程（5 人 × 三级量表）：成本高、不可复现、且本地无对手。
- ❌ 不要试图获取上游 58 案例题目集：**未发布**，不存在。
- ❌ 不要用离线 26 benchmark 口径衡量决策能力：测的是视频理解，与每秒决策无关（论文自己也这么划界，p.12 §5.1）。

---

## 9. 出处（页码 / URL / 章节）

**论文**（本地 `JoyAI-VL-Interaction-Reportv1.pdf`，与 upstream 仓库内同名文件字节一致）

| 内容 | 位置 |
|---|---|
| 模型三态决策、每秒一次 | p.5 §3 引言、p.6 §3.2 |
| 「interaction model」判据定义 | p.2 §1 |
| 六族训练数据、silence 是一等标签 | p.6 §3.2；p.7 §3.2（alerting 时序最重要："an alert one second late describes a different moment"） |
| SFT 加权交叉熵与 `1 / 0.4 / 1.5` | p.8 §3.3 + Eq.(1) |
| RL 奖励（penalizes false alarms / mistimed / degenerate always-respond） | p.9 §3.3 |
| `response mode`（balance / talkative / aloof） | p.10 §4.2 |
| **§5.1 六场景定义 + 58 案例** | **p.12** |
| **§5.1 Protocol and metric（quality / timing、三级量表、等权）** | **p.12–13** |
| §5.1 偏置控制（5 评审 / 身份隐藏 / 顺序随机）+ win/tie/loss / tie 不折算 | p.13 |
| Table 1 / Table 2（per-scenario 胜率） | p.13 |
| §5.2 结果叙事（timing 是分水岭；豆包赢在 quality；Gemini 赢在低时序压力的 time-awareness） | p.13–14 |
| §5.2 案例研究（Fall Detection 4–5s、Dart 6 vs 2、20s 目标差 1–2s、Gemini ~40s、每 4 秒节奏） | p.14–15 |
| §5.3 局限（评测「preliminary」、建议借鉴方法学而非 benchmark） | p.15–16 |
| Appendix Listing 1–3（每秒样本格式） | p.19–21 |

**仓库**（`https://github.com/jd-opensource/JoyAI-VL-Interaction`，main 分支，全量 tree 182 条）

| 内容 | 路径 |
|---|---|
| 决策核心描述、四个 Key Features | `README.md` §Introduction / §Key Features |
| 评测章节（两张胜率表 + 离线 26 benchmark） | `README.md` §📊 Evaluation |
| **标注 schema + 每秒标签转换说明** | **`datasets/README.md`**（§2 Data Format、§4 Converted Output Format） |
| 标注转换脚本（有代码、无评测） | `datasets/convert_data.py` |
| 保守默认 `FORCE_SILENCE_BEFORE_QUERY=true` | `services/webinfer/README.md`（第 107、174 行附近） |
| 仓库内唯一 test 目录（环境自检，非评测） | `install/tests/verify_real_env.py`、`install/tests/run_real_env_tests.sh` |
| 冒烟脚本（非评测） | `services/webinfer/smoke.py` |
| 确认无 eval/benchmark 目录 | `gh api repos/.../git/trees/main?recursive=1`（无 `eval/`、`benchmark/`、`benchmarks/`） |
| 上游 PDF | `JoyAI-VL-Interaction-Reportv1.pdf`（5,020,917 B） |

**Blog**（`https://joyai-vl-video-future-academy-jd.github.io/JoyAI-VL-Interaction/`）

- Capabilities 01–09 的逐案例名称与判对标准（Monitoring/Counting/Translation/Time awareness/Commentary/Memory 的 case 明细取自此处 §Capabilities）
- §Evaluation：「58 real, event-driven visual interaction settings」「the memory category ... minute-scale visual recall setting, with cases mostly spanning several minutes to a little over ten minutes」

**本地既有材料（仅记录其引用的上游侧概念，未重复分析）**

- `doc/specs/addressee-detection.md`：`S_fusion = 0.4 × S_acoustic + 0.6 × S_semantic`，双阈值 `T_low 0.3 / T_high 0.7`；「宁可漏、不可乱插（误响应代价 > 漏判代价）」
- `doc/research/addressee-detection-2026-08-12/01_problem_definition.md:246`：FAR（False Accept Rate）定义
- `doc/research/addressee-detection-2026-08-12/04_hybrid_approach.md:297`：ICMI 2023 Multimodal Turn Prediction，F1 > 80%
- `doc/research/addressee-detection-2026-08-12/04_hybrid_approach.md:498`：**「宁可误触发，不可漏响应」——与当前用户拍板相反，历史漂移点**
- `doc/research/data/benchmark_4state_notforme_results.json`：本地现有矩阵形式（`baseline_mis_response_rate_pct` / `not_for_me_precision_pct` / `directed_miss_rate_pct`），形状与建议的 A 轴指标一致

---

## 10. 结论摘要（给决策用）

1. **上游没有「决策正确」的量化定义**，只有人评 timing 轴 + 胜率；可直接复制的只有**形式**（每秒三态 + 带时刻的 ground truth）。
2. **上游未开源任何评测代码**，58 案例题目集也未发布；**唯一可复用物是 `datasets/README.md` 的标注 schema**。
3. **vision-triggered responsiveness / time awareness 无指标定义**，只有正文里的秒级叙事；本地须自定容差（可锚定 ±1–2 秒）。
4. **上游评测层等权，不体现代价不对称**；训练层反而**偏向发言**（`w_response=1.5`），与本地偏好**反向**。→ 本地必须自建 cost-sensitive 指标，且**不要**沿用上游训练权重取向。
5. **`not-for-me` 在上游完全没有对应物**，本地四态比上游多一维；必须把定向轴与时序轴**拆开评测**，不能合成单一准确率。

---

*报告完毕。本次为只读侦察：未 clone 上游仓库、未下载数据集、未改动本报告以外的任何文件。按 AGENTS.md 惯例本可追加 `memory/2026-09-20.md`，因任务明确要求「除本报告外不改任何文件」故未写；如需留痕请由主端点补记。*
