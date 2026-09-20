# 「每秒/持续决策」文档陈述漂移审计（2026-09-20）

> **端点身份**：审计端点（AFK，只读）。
> **审计对象**：全仓库范围内一切**声称系统"每秒决策 / 持续决策 / 持续看画面 / 每秒自主决策"**的文档陈述。
> **已实测确认的代码事实（本报告直接采信，不重新验证）**：
> ① `VideoProcessorTrack`（1 fps）走 `VLMService.process_frame` → `/v1/chat/completions`，拿**自由文本**，**不解析 decision token**；且在 `services/webui/src/` 下**零构造点**。
> ② `LIVE_PROACTIVE_ENABLED` **default False**，间隔默认 **5.0 秒**。
> ③ proactive 轮**不进 `qa_history`**（决策不留痕）。
> ④ 真正的四态决策只在**用户说话提交后**与 proactive 轮发生。
> ⑤ `RTSPVideoTrack` 无构造点，`/api/rtsp/*` 三个路由是 **501 桩**。
>
> **方法限制遵守声明**：本审计**未运行任何测试、未启动任何服务、未修改本仓库任何文件**（仅新增本报告）。所有代码依据来自静态读取与 ripgrep 检索。

---

## 0. 一句话结论

**夸大严重程度：中等偏高、且高度集中。**

「每秒决策」这一表述在**活跃文档中只有 2 个文件**（`ARCHITECTURE.md` 与 `DELIVERY.md`），但在**已归档交付稿中成体系地出现 9 处以上**，并且它是**上游 SSOT 血统**——`ARCHITECTURE.md` §1 直接继承自 `doc/deprecated/delivery-handoff-2026-07/` 的生成式交付稿。核心事实是：

> **代码里不存在任何"每秒决策"循环。** 存在的是一条 **1 fps 的"画面解说"通道**（`VideoProcessorTrack`，无决策 token、无构造点、实为死代码）和一条 **默认关闭、间隔 5 秒的 proactive 决策通道**。两者**都不是**每秒决策。真正的四态决策**只由用户说话触发**。

因此 `ARCHITECTURE.md` §1 的「核心模型 JoyAI-VL-8B **每秒自主决策**」属于**❌ 错误**（不是夸大——代码里根本没有该机制）。

---

## 1. 穷尽清单（逐条判定）

> 每条给出：**出处文件 → 原文摘录 → 判定 → 代码依据**。不写行号，用章节/标题定位。

### A 组：声称"每秒自主决策"（核心指控）

| # | 出处 | 原文摘录 | 判定 | 代码依据 |
|---|---|---|---|---|
| A1 | `ARCHITECTURE.md` §1「它是什么」 | 「核心模型 **JoyAI-VL-8B** 每秒自主决策「说话 / 沉默 / 委派」（`silence` / `response` / `delegate`）」 | ❌ **错误** | 全仓唯一的周期性决策循环是 `live_proactive.py` 的 `proactive_loop`，节奏为 `await asyncio.sleep(interval_s)`，`interval_s` 默认 **5.0 秒**且 `LIVE_PROACTIVE_ENABLED` **默认 False**（`proactive_enabled_from_env` → `_env_flag(..., default=False)`）。另一条 1 fps 通道 `VideoProcessorTrack` **不解析 decision token**（走 `/v1/chat/completions` 拿自由文本）。⇒ **不存在"每秒决策"机制** |
| A2 | `ARCHITECTURE.md` §4「决策 Token」 | 「**每秒**由 webinfer 产出，送 TTS 前剥离」 | ❌ **错误** | 同上。`parse_model_decision(ctx.raw_text)` 只在**已有推理轮**被调用；推理轮的产生源是用户 `_handle_commit` 与 proactive（5s/默认关）。**没有"每秒产出"的调用点** |
| A3 | `ARCHITECTURE.md` §2 系统全景「主链路」 | 「采集（U2/U3）→ VLM 推理 + 决策 token（M1/M2 → B1）→ …」 | ⚠️ **夸大/误导** | 该箭头图**未区分两条互斥路径**：`/v1/chat/completions`（多模态，**无** decision token，`VideoProcessorTrack` 走这条）与 `/v1/text/chat`（live 四态，**有** decision token）。把"采集→推理+决策 token"画成单一直线，隐含"采集帧必然产出决策"，与代码不符 |
| A4 | `doc/deprecated/delivery-handoff-2026-07/高层架构设计.md` §1 概要 | 「核心模型 JoyAI-VL-8B 每秒自主决策「说话 / 沉默 / 委派」…外围由 5 个可插拔服务组成」 | ❌ **错误**（历史文件） | 同 A1。**但这是 A1 的源头**——`ARCHITECTURE.md` 文末自述「本文档由 9 份生成式架构交付稿（高层/系统/部署/安全/UserStory 等）提炼精简而成」。⇒ 漂移是**继承性**的，不是新造的 |
| A5 | 同上 §2.3 差异化价值 | 「差异化价值：每秒自主决策 silence/response/delegate 的「会说话的 AI」（非被动问答）」 | ❌ **错误**（历史文件） | 同 A1。且"非被动问答"与实际相反——代码中决策**正是**由用户提问驱动的（proactive 是唯一例外且默认关） |
| A6 | `doc/deprecated/delivery-handoff-2026-07/部署设计.md` §1「系统范围」 | 「核心模型 JoyAI-VL-8B 每秒自主决策 `silence` / `response` / `delegate`」 | ❌ **错误**（历史文件） | 同 A1 |
| A7 | 同上「术语表」 | 「决策 token：模型**每秒输出**的三类控制字面量」 | ❌ **错误**（历史文件） | 同 A1 |
| A8 | 同上「容量模型」 | 「峰值 QPS（推理）\| ~1 req/s \| ~1 req/s \| ~2 req/s \| **每秒决策**」 | ❌ **错误**（历史文件） | 该行把 QPS 预算建立在"每秒决策"上。实际：proactive 默认关，**稳态推理 QPS ≈ 0**（仅用户说话时 1 次）；开启后 = 1/5 = **0.2 req/s**。⇒ 容量结论整体失效 |
| A9 | `doc/deprecated/delivery-handoff-2026-07/系统设计.md` 多处 | 「实时交互编排 \| webinfer 8070 单入口网关，**每秒决策**」；「每秒 silence/response/delegate 决策路由」；「触发：每帧/每秒决策周期」；「峰值 QPS（推理）…每秒决策」 | ❌ **错误**（历史文件） | 同 A1/A8 |
| A10 | `doc/deprecated/delivery-handoff-2026-07/UserStory.md` 多处 | 「核心模型 JoyAI-VL-8B 每秒自主决策…」；「模型**每秒输出决策 token**」；「F1 实时交互编排…**每秒** silence/response/delegate 路由」 | ❌ **错误**（历史文件） | 同 A1 |
| A11 | `doc/deprecated/delivery-handoff-2026-07/material_digest.md` | 「D1，§Overview \| …核心模型 JoyAI-VL-8B **每秒自主决策** speak / stay silent / delegate」 | ❌ **错误**（历史文件） | 同 A1。此文件是**素材摘要**，说明该说法来自**上游原始素材**，非本项目测得 |
| A12 | `doc/deprecated/700809-raw-extract.md` | （同上表述的原始抽取） | ❌ **错误**（历史文件） | 同 A1 |
| A13 | `doc/research/eval-upstream-methodology-2026-09-20.md` | 「上游…决策空间：三态…**每秒一次**」 | ✅ **准确** | 该处明确指**上游项目**（JoyAI-VL-Interaction 开源版）的训练/评测形式，且同文档多次划界「与本地…无关」。**这是对上游的正确描述，不是对本地代码的声称** |
| A14 | `doc/research/eval-capture-priorart-2026-09-20.md` §2c | 「⇒「每秒一帧 → 四态决策」在当前代码里**并不存在**：真正的四态决策只发生在用户说话提交后与 proactive 轮（默认 5s 且默认关）」 | ✅ **准确** | 这是**本仓库对 A1 的自我纠正**，且与已确认代码事实完全一致。**该文件应被视为本次审计的既有正解** |

### B 组：声称"持续看画面 / 主动搭话"（live C.B）

| # | 出处 | 原文摘录 | 判定 | 代码依据 |
|---|---|---|---|---|
| B1 | `doc/specs/live-interaction-layer.md` §0 战略决策 | 「收敛终点（定死）：统一 Turn Controller 跑通 live **C.B（VLM 持续看画面 + 主动搭话）**」 | ⚠️ **夸大/误导** | 「主动搭话」对应的 `proactive_loop` **默认关闭**（`LIVE_PROACTIVE_ENABLED` default False）；「持续看画面」实为**每 5 秒采样最新 1 帧**（`frames_payload([recent_frames[-1]])`），非"持续"。⇒ 措辞描述的是**开启后的形态**，但未标注默认关 |
| B2 | 同上 §2 目标 | 「**C.B 完整直播形态**（下一步主线）：VLM 持续看画面 + 主动搭话 + 沉默判断自主发言（**需 video 帧决策循环接入**）」 | ⚠️ **夸大/误导** | 括号内「需…接入」是诚实的（承认未接入），但主句以陈述句给出"持续/主动"，且本句**从未随实现更新**——同文件 §2 只标记了 C.A「✅ 已实现」，C.B 仍写"下一步主线"，**落后于 `live-visual-cb.md` 的 Implemented 状态**。⇒ 双文件状态互相矛盾 |
| B3 | 同上 §7 风险表 | 「live 三判断已在后端（默认模式），C.A 先用它跑通基本对话，**主动搭话留 C.B**」 | ✅ **准确** | 明确把主动搭话排除在 C.A 之外，措辞诚实 |
| B4 | `doc/specs/live-visual-cb.md` §1 目标 | 「2. **主动搭话**：无用户语音时周期性抽帧 → VLM 判定"有值得说的"→ 主动 TTS 开口」 | ✅ **准确** | 用"**周期性**"而非"持续/每秒"，且 §2.4 明确「每 `PROACTIVE_INTERVAL_S`（默认 5s）抽最新帧」 |
| B5 | 同上 §2 架构决策 | 「**默认关闭**（env `LIVE_PROACTIVE_ENABLED`，防乱开口，真机验证后开）」 | ✅ **准确** | 与 `proactive_enabled_from_env()` 的 `default=False` 逐字一致。**这是全仓对"默认关"表述最准确的一处** |
| B6 | 同上 §3 层 3 | 「"主动搭话"开关（**默认关**，checkbox/按钮，样式与 live 一致）」 | ✅ **准确** | 与前端 `liveProactiveToggle.disabled = !usable` + env hint 一致 |
| B7 | 同上 文档头「生命周期」 | 「**正式（Implemented，待真机验收）**」 | ⚠️ **夸大/误导** | "Implemented" 指代码已落盘（属实），但**未标注"默认关"**——读者会以为开箱即有主动搭话能力。且 `doc/specs/README.md` 索引同样只写「✅ Implemented（待真机验收）」 |
| B8 | `doc/specs/turn-controller-integration.md` | 「C.B 完整直播形态：**VLM 持续看画面 + 主动搭话** + 沉默判断自主发言。层 1-3 已实现（604 测试 QA PASS），待真机验收」 | ⚠️ **夸大/误导** | 同 B7。「已实现 + QA PASS」为真，但**"持续"为假**（5s 采样）、**默认关未标注** |
| B9 | `doc/specs/draft-mode-isolation-boundaries.md` | 「live…**四态**（+not-for-me，addressee 判定前置）」 | ✅ **准确** | live=四态已由 `_resolve_base_system_prompt` 硬断言（`test_qa_4state_supplement.py`）。**该处未声称周期性，无问题** |
| B10 | `doc/specs/unified-turn-controller.md` | 「L3 LLM \| decision token（`parse_model_decision`）\| 最终话轮/**主动搭话**/静默决策 \| ✅ 已有，核心 IP 不动」 | ⚠️ **夸大/误导** | 标记「✅ 已有」——`parse_model_decision` 确实已有，但"**主动搭话**"作为**触发源**当时尚未接线（该 spec 早于 C.B）；且即便现在也已接线但**默认关**。⇒ 状态标记领先于事实 |
| B11 | `doc/specs/blog`/`webui-redesign-*` 系 | 「主动搭话开关属长期设置项，前台常驻 = 干扰 → 移设置页」 | ✅ **准确** | 纯 UI 归类问题，不涉及能力声称 |
| B12 | `doc/frontend/fullscreen-audit-2026-09-19.md` | 「**主动搭话** `#liveProactiveToggle` — 无语音时周期性看画面」 | ✅ **准确** | 用"**周期性**"，未夸大为"持续"。措辞可作为 §3 修正的模板 |
| B13 | `doc/standards/webui-design-standards.md` | 「**Live 常驻模式**开关」；「主动搭话原用裸 checkbox」 | ⚠️ **夸大/误导** | 「常驻」一词对此开关安全（live 会话确为常驻 LISTENING），但**与"主动搭话"同段出现时易被读成"常驻主动搭话"**。属措辞密度问题，非硬错误 |

### C 组：声称"1 fps"（须与"每秒决策"区分）

| # | 出处 | 原文摘录 | 判定 | 代码依据 |
|---|---|---|---|---|
| C1 | `services/webui/src/.../static/screen_capture.js` 注释 + `index.html` 提示 | 「Captures a user-selected window/tab at 1 fps and ships JPEG frames」；「把屏幕或摄像头画面以 **1fps** 推送给模型」 | ✅ **准确** | 采集端 `frameRate: {ideal: 1}`，`1000/fps` 节拍，`frame_seq` 单调。**采集确为 1 fps** |
| C2 | `doc/subsystems/screen-capture.md` | 「[系统] 1 fps 视频帧 → **VLM 识别**」 | ✅ **准确** | 采集→VLM 识别确实发生（`VideoProcessorTrack`/WS frame → `process_frame`）。**该处只说"识别"，没说"决策"——措辞正确** |
| C3 | `doc/main/00-main-direction.md` | 「BT 仍走 **1fps WS frame**」 | ✅ **准确** | 同上 |
| C4 | **`ARCHITECTURE.md` §1 与 §4 的"每秒"** | （见 A1/A2） | — | ⚠️ **关键区分**：**"1 fps 采集"为真，"每秒决策"为假。** 文档漂移的本质正是把**采集节奏**误当成**决策节奏**。全仓检索证实：`VideoProcessorTrack` 的 1 fps **不解析 token** |
| C5 | `doc/specs/2026-07-13-current-state.md` | 「rt[WebRTC video track VideoProcessorTrack] --> sample[抽帧 1 fps JPEG]」 | ⚠️ **夸大/误导** | 抽帧 1 fps 为真，但该流程图**把 `VideoProcessorTrack` 画成活跃组件**——而它在 `services/webui/src/` 下**零构造点**（全仓仅类定义与 `ws_handler.py` 里的**运行时参数改写**，无实例化）。⇒ 图与实况不符 |

### D 组：性能承诺与"持续/实时"绑定

| # | 出处 | 原文摘录 | 判定 | 代码依据 |
|---|---|---|---|---|
| D1 | `ARCHITECTURE.md` §8 非功能基线 | 「端到端延迟 P99 \| **≤ 1.2s** \| 实时交互不掉线底线（当前 0.8–1.5s）」 | ⚠️ **夸大/误导** | 该数字**继承自已废弃交付稿**（`高层架构设计.md` §2.3 / `系统设计.md` §3.5 / `UserStory.md` N1），其前提是"KWS 唤醒 → 一次问答"的**单次交互**，**不是**"每秒决策"。**"当前 0.8–1.5s"在此仓库内无任何实测出处**——本机实测只有 **VLM 推理段**（prompt eval 453.64ms + eval 520ms ≈ 973ms，见 `ARCHITECTURE.md` §8 下方的实测复现段），端到端（采集+ASR+TTS+播放）**无 P99 实测**。⇒ 数值来源不可追溯 |
| D2 | 同上 | 「VLM 推理段稳态 <320ms（DRIFT-6 实测）非瓶颈」 | ✅ **准确**（但口径需注意） | 与 `决策/VLM架构与模型组成.md`「VLM 端到端延迟实测结论」一致，且该处明确划界「≠ 冷启动/首字节」 |
| D3 | `DELIVERY.md` §16.1 屏幕捕获 | 「延迟 \| **<100ms**（捕获 + 编码）」 | ⚠️ **夸大/误导** | 与 `doc/subsystems/screen-capture.md` §2 同源。这是**采集+编码单段**指标，但放在交付验收表里易被读成端到端。**且与 issue #43 结论相悖**——#43 明确「瓶颈在采集/编码链路」（OBS/屏幕捕获 + max_pixels + JPEG 有损） |
| D4 | `DELIVERY.md` §16.1 | 「带宽 \| ~200 KB/s（1 fps，JPEG 70%）」 | ⚠️ **夸大/误导** | 该值基于 1 fps 满速推帧。**但 `VideoProcessorTrack` 零构造点、live 采集需用户手动开启**，故**稳态实际带宽 ≈ 0**。条件未标注 |
| D5 | `ARCHITECTURE.md` §2 主链路 | 「会话沉淀（记忆 B3 + **每 100 帧中期摘要**）」 | ❌ **错误** | 代码 `adapter_types.py`：`chunk: int = 200`，且触发条件为 `state.current_chunk["turn_count"] >= self.config.chunk` —— **单位是"轮"（turn），不是"帧"，数值是 200 不是 100**。⇒ 数值与单位**双错** |
| D6 | `doc/deprecated/.../UserStory.md`、`部署设计.md`、`material_digest.md` | 「每 100 帧中期摘要」（多处） | ❌ **错误**（历史文件） | 同 D5。这证实 D5 的「100 帧」同样是**继承性漂移** |
| D7 | `doc/deprecated/.../部署设计.md` 容量表 | 「R-C-004 \| llama-server \| proc-summary \| 摘要推理 \| VRAM 2.9GB … \| **每 100 帧中期摘要**」 | ❌ **错误**（历史文件） | ①"100 帧"错（见 D5）；②summary 进程**已不在启动计划**（`ARCHITECTURE.md` §8 已自行更正）；③`ARCHITECTURE.md` §3 明示当前仅 **6 个**服务 |

### E 组：经检索**未发现**问题的表述（澄清覆盖范围）

| # | 出处 | 原文摘录 | 判定 |
|---|---|---|---|
| E1 | `doc/specs/radio-silence.md` §1/§2 | 「live 模式常驻实时交互，有沉默判定（silence）」；「live 常驻态：**四态决策+播报**」 | ✅ **准确** | 静默模式**不依赖"持续决策"**——它依赖的是**已存在**的四态判定。"静默态行为：模型推理停"（§2）明确是**关闭**推理，与"持续决策"相反。⇒ **静默 spec 无此漂移** |
| E2 | `doc/runtime-topology.md` §3 | 「`live`（默认）\| 四态：silence/response/delegation/not-for-me \| **常驻监听**，完整决策框架」 | ✅ **准确** | "常驻监听"指**麦克风常驻**（确为真，`TurnConfig.live()` wake_gate=False），非"常驻决策"。措辞无歧义 |
| E3 | `决策/交互模式与决策token规范.md` D-2026-08-03-001 | 「live（源项目既有，**三判断**：沉默/主动搭话/回复）」 | ⚠️ **夸大/误导**（**已知**） | 该条把"主动搭话"列为 live 的**既有**三判断之一。实际 proactive 默认关且 2026-08-13 才落地。**`doc/runtime-topology.md` §3 已自行标注**：「是四态上线前的表述，**尚未更新**」⇒ **漂移已被识别但未修** |
| E4 | `决策/VLM架构与模型组成.md` | 全篇（§一~§八） | — | **零命中**。该文件**只讲分离式双模型架构与延迟实测**，**从未声称每秒决策**。⇒ **本文件无此漂移**（与任务书"特别注意"的预期相反） |
| E5 | `prompts/bt-7274.txt`、`prompts/README.md` | 决策格式说明（`</delegation>` / `</silence>` / `</response>`） | ✅ **准确** | 只定义**输出格式**，未声称触发频率 |
| E6 | `services/webinfer/README.md` | 「prepends it to the built-in decision-token system prompt」 | ✅ **准确** | 只讲 prompt 注入，未声称频率 |

---

## 2. 按严重度分级

### 🔴 会误导架构决策（必须修，且优先级最高）

| ID | 位置 | 问题 |
|---|---|---|
| **A1** | `ARCHITECTURE.md` §1 | 「每秒自主决策」——**架构总纲第一句**。这是所有下游文档、新加入的 agent、外部读者建立项目心智模型的入口。它直接导致：以为存在持续推理循环 → 据此推算显存/QPS/上下文增长 → **全部预算失真** |
| **A2** | `ARCHITECTURE.md` §4 | 「**每秒**由 webinfer 产出」——决策 token 章节的**唯一频率声明**，是 A1 的技术化重述，可信度更高因而危害更大 |
| **A8** | `部署设计.md` 容量模型 | 「峰值 QPS ~1 req/s …**每秒决策**」——**容量规划的算术依据**。若被沿用，会把推理负载高估 **5×**（实际 0.2 req/s）或**∞×**（默认关时为 0） |
| **D5** | `ARCHITECTURE.md` §2 | 「**每 100 帧**中期摘要」——**数值（200）与单位（轮）双错**。若据此估算记忆落盘频率与 memory-store 负载，错 2× 以上且量纲错误 |
| **D1** | `ARCHITECTURE.md` §8 | 「P99 ≤ 1.2s（当前 0.8–1.5s）」——**唯一非功能硬指标**，来源不可追溯、前提（单次交互 vs 每秒）未标注。是 acceptance 与容量模型的共同锚点 |

### 🟠 误导读者（应修，中等优先）

| ID | 位置 | 问题 |
|---|---|---|
| **A3** | `ARCHITECTURE.md` §2 | 主链路把两条互斥路径（有/无 decision token）画成单一直线 |
| **B1/B2** | `live-interaction-layer.md` §0/§2 | 「VLM 持续看画面 + 主动搭话」未标注默认关；且 C.B 状态落后于 `live-visual-cb.md` |
| **B7/B8** | `live-visual-cb.md` 文档头、`turn-controller-integration.md` | 「Implemented」未附「默认关」 |
| **B10** | `unified-turn-controller.md` | 「主动搭话 ✅ 已有」状态标记领先于事实 |
| **C5** | `2026-07-13-current-state.md` | 把零构造点的 `VideoProcessorTrack` 画成活跃组件 |
| **D3/D4** | `DELIVERY.md` §16.1 | `<100ms` / `~200KB/s` 未标注"需手动开启 + 单段指标" |
| **E3** | `决策/交互模式与决策token规范.md` | 「三判断…主动搭话」为陈旧表述（已被 `runtime-topology.md` 标注但未修） |
| **A4–A12** | `doc/deprecated/delivery-handoff-2026-07/*` 等 9 处 | 全部历史稿。**若读者不查 `deprecated/` 标记即会采信**——已在目录名与文件头标注，风险降低但不为零 |

### 🟡 措辞不精确（可选修）

| ID | 位置 | 问题 |
|---|---|---|
| **B13** | `webui-design-standards.md` | 「Live 常驻模式」与「主动搭话」同段，易连读为「常驻主动搭话」 |
| **D7** | `部署设计.md` | 「100 帧」+ 已废弃 summary 进程 |
| — | `ARCHITECTURE.md` §6 | **文件内自相矛盾**：§3 已更正 llama-server 为「实测稳态 ≈ 9.3GB VRAM…原记 "~5.8GB" 为错值，差 ~60%」，但 §6 关键选型表**仍写「~5.8GB VRAM」**。（非本审计主责范围，但同属"文档未随实测更新"的同一病根） |

---

## 3. 建议的修正措辞（可直接采用）

### 3.1 `ARCHITECTURE.md` §1（替换整句）

> **原文**：核心模型 **JoyAI-VL-8B** 每秒自主决策「说话 / 沉默 / 委派」（`silence` / `response` / `delegate`），外围由可插拔服务组成…

> **建议替换为**：
> 核心模型 **JoyAI-VL-8B** 对每个交互轮输出四态决策「沉默 / 说话 / 委派 / 非面向我」（`silence` / `response` / `delegate` / `not-for-me`），外围由可插拔服务组成。**决策由两类触发源产生**：① **用户说话提交后**（主路径，每轮一次）；② **proactive 主动搭话轮**（周期性采样最新帧，间隔 `LIVE_PROACTIVE_INTERVAL_S` 默认 **5 秒**，且 `LIVE_PROACTIVE_ENABLED` **默认关闭**）。画面采集为 **1 fps**，但**采集帧本身不产生决策 token**（详见 §4）。**系统不存在"每秒决策"循环。**

### 3.2 `ARCHITECTURE.md` §4（替换首句）

> **原文**：每秒由 webinfer 产出，送 TTS 前剥离：

> **建议替换为**：
> 由 webinfer 在**每一轮推理**（用户轮 / proactive 轮）解析产出，送 TTS 前剥离：

### 3.3 `ARCHITECTURE.md` §2 主链路（替换该行）

> **原文**：**主链路**：采集（U2/U3）→ VLM 推理 + 决策 token（M1/M2 → B1）→ …

> **建议替换为**：
> **主链路（两条互斥路径，勿混）**：
> - **决策路径（live 四态）**：用户语音 → VAD/ASR → `_handle_commit` → `/v1/text/chat`（可带 `frames`）→ **决策 token** → `[response]` TTS / `[silence]` / `[delegate]` Hermes / `[not-for-me]`。
> - **画面解说路径（无决策 token）**：1 fps 帧 → `VLMService.process_frame` → `/v1/chat/completions` → **自由文本** → 前端展示，**不解析 decision token**。
>
> **proactive 轮**（可选，默认关）：每 5s 采样最新 1 帧 → 决策路径 → 仅在 `decision=response` 时主动 TTS。
> 会话沉淀：记忆 B3 + **每 200 轮**中期摘要（`chunk=200`，按 **turn** 计数，非帧）。

### 3.4 `ARCHITECTURE.md` §8 P99 行（替换该行）

> **原文**：| 端到端延迟 P99 | ≤ 1.2s | 实时交互不掉线底线（当前 0.8–1.5s） |

> **建议替换为**：
> | 端到端延迟 P99 | ≤ 1.2s（**目标值，无本机实测支撑**） | 口径 = **单次用户交互**（KWS 唤醒 → 字幕/语音呈现）的 99 分位，**不是**"每秒决策"的周期约束。**⚠️ 来源为 2026-07 生成式交付稿（已归档 `doc/deprecated/`），本机无端到端实测**。本机**仅有** VLM 推理段实测：prompt eval 453.64ms + eval 520ms ≈ **0.97s**（768×576 单图，见下）。**"当前 0.8–1.5s" 无可追溯出处，引用前须先实测。** |

### 3.5 `DELIVERY.md` §16.1（替换两行）

> **原文**：| 延迟 | <100ms（捕获 + 编码） | / | 带宽 | ~200 KB/s（1 fps，JPEG 70%） |

> **建议替换为**：
> | 延迟 | <100ms（**仅采集+编码单段**，非端到端） | 
> | 带宽 | ~200 KB/s（1 fps，JPEG 70%）**——仅在用户手动开启画面采集时；未开启时 ≈ 0** |
> | 备注 | ⚠️ issue #43 结论：端到端瓶颈**正在**采集/编码链路 |

### 3.6 `doc/specs/live-interaction-layer.md` §0 / §2（替换）

> **原文**：…跑通 live **C.B**（VLM 持续看画面 + 主动搭话）…
> **原文**：**C.B 完整直播形态**（下一步主线）：VLM 持续看画面 + 主动搭话…

> **建议替换为**：
> …跑通 live **C.B**（VLM 周期性看画面 + 主动搭话）…
> **C.B 完整直播形态**（✅ 已实现，见 `live-visual-cb.md`；**主动搭话默认关闭**）：VLM 每 5 秒采样最新帧 + 主动搭话 + 沉默判断自主发言。

### 3.7 `doc/specs/live-visual-cb.md` 文档头（追加一行）

> **建议在「生命周期」行后追加**：
> > ⚠️ **默认关闭**：`LIVE_PROACTIVE_ENABLED` 默认 `false`，间隔 `LIVE_PROACTIVE_INTERVAL_S` 默认 **5 秒**。开箱状态下 **live 仅有"看着画面对话"，无主动搭话**。运行时可经 `POST /api/live/proactive` 热切换（须 env 开闸）。

### 3.8 `doc/specs/turn-controller-integration.md`（替换）

> **原文**：C.B 完整直播形态：VLM 持续看画面 + 主动搭话 + 沉默判断自主发言。层 1-3 已实现（604 测试 QA PASS），待真机验收。

> **建议替换为**：
> C.B 完整直播形态：VLM **周期性（默认 5s）**看画面 + 主动搭话（**默认关闭**）+ 沉默判断自主发言。层 1-3 已实现（604 测试 QA PASS），待真机验收。

### 3.9 `决策/交互模式与决策token规范.md` D-2026-08-03-001（替换）

> **原文**：live（源项目既有，三判断：沉默/主动搭话/回复）

> **建议替换为**：
> live（**四态**：沉默/说话/委派/非面向我；源项目为三判断，本 fork 于 2026-08-12 增第四态。**"主动搭话"为 proactive 轮，默认关闭、间隔 5s**）

### 3.10 `doc/deprecated/delivery-handoff-2026-07/` 全族（建议）

> **建议**：在上述 9 处文件**每个文件的 H1 标题下方**加统一横幅：
> > ⚠️ **历史交付稿（2026-07），已废弃**。文中「每秒自主决策」「每 100 帧中期摘要」「11 进程 / VRAM 11.5GB」等**均与当前代码不符**。现行真值见 `ARCHITECTURE.md` 与 `doc/runtime-topology.md`。

---

## 4. 连带影响清单（哪些既有结论需重新评估）

> 判定原则：**凡是把"每秒一次推理"作为隐含前提的结论，其前提不成立。**

| # | 既有结论 | 所在位置 | 为何受影响 | 建议动作 |
|---|---|---|---|---|
| **1** | **推理负载 / QPS 容量模型**（~1 req/s 稳态、~2 req/s 峰值） | `部署设计.md` 容量表 | 前提"每秒决策"为假。实际：默认关时**稳态 0 req/s**；开启后 **0.2 req/s**（1/5s） | 🔴 **整体重算**。当前 6 服务方案下推理负载**远低于**原估算 |
| **2** | **显存预算 ≤ 11.5GB 的推导** | `ARCHITECTURE.md` §8、`部署设计.md` | 原预算按**已废弃 11 进程方案**推算（含 summary 2.9GB / CosyVoice 1.1GB / whisper 0.7GB）。**与"每秒决策"无直接算术关系**——但"每秒推理 → KV 持续增长 → 显存吃紧"是当初**维持该预算的理由之一**。既然不做每秒推理，**"KV 吃满"的担忧彻底不成立** | 🟠 **部分重估**：`ARCHITECTURE.md` §8 已自行更正为「KV 仅 1GB 量级、"KV 吃满"的怀疑不成立、实测 9,326 MiB」。**该更正与本次审计一致，应予肯定并推广**。剩余待办：重算**真实可用余量 7.0GB** 而非原记 10.2GB |
| **3** | **端到端 P99 ≤ 1.2s 指标** | `ARCHITECTURE.md` §8、`UserStory.md` N1、`系统设计.md` N1 | ① 前提是"单次交互"非"每秒"；② **本机无实测**；③ issue #43 已定位瓶颈在采集/编码，而该段恰恰**未被 P99 口径覆盖** | 🔴 **标注前提 + 补齐实测**。在补测前，该指标**不应作为验收硬门禁** |
| **4** | **上下文增长 / 16384 ctx 是否够用** | `vlm-lightweight-2026-09.md` §1.1、`upstream-delta-2026-09.md` | 该问题**已被正确回答**：「本项目实际数据流不是每帧留在上下文（历史只剩文本，`qa_history_window=12` 轮）」——**该结论不依赖"每秒决策"**，反而**证伪**了"每秒决策"的上下文压力叙事 | ✅ **无需重估**（且可作为 §3 修正的引用依据） |
| **5** | **记忆 / 会话设计：决策不落盘则记忆里有什么** | `决策/业务-决策记忆.md`、`eval-capture-priorart-2026-09-20.md` §2c | **这是最严重的一条。** 事实链：① proactive 轮**不进 `qa_history`**（`_update_text_qa_history` 需非空 user text，测试 `test_live_visual_proactive_empty_text_no_qa_history_pollution` 钉住）；② `qa_history` 为**内存态**且窗口仅 **12 轮**；③ 事件流中**无决策事件**。⇒ **记忆里"只有用户问答对 + 画面文字描述"，没有任何"AI 为何决定说话/沉默"的痕迹** | 🔴 **设计层面重估**：`决策/业务-决策记忆.md` 目前只覆盖 `_memory_recall` 与 Wiki 召回，**完全未涉及"决策日志"**。若"主动搭话质量"要在真机验收（`live-visual-cb.md` §1 明确要求），**必须先有决策事件流**，否则无据可评 |
| **6** | **"每 100 帧中期摘要 → memory-store 负载"** | `ARCHITECTURE.md` §2、`UserStory.md` US-记忆 | 数值与单位双错（实为 `chunk=200` **轮**） | 🟠 **重算**：记忆落盘频率应为**每 200 轮**。在默认关 proactive + 单用户本地形态下，该频率**极低**，memory-store 压力可忽略 |
| **7** | **采集/编码链路优化优先级** | issue #43（已 CLOSED）、`screen-capture.md` | #43 结论「瓶颈在采集/编码」**建立在"1 fps 持续推帧"的负载画像上**。但 `VideoProcessorTrack` **零构造点**、live 采集**需手动开**，⇒ **稳态下该链路的实际负载 ≈ 0，优化优先级应下调** | 🟠 **重估优先级**：把 1 fps 采集优化从"主瓶颈项"降为"按需项" |
| **8** | **`video_processor.py` / `rtsp_track.py` 的维护定位** | `doc/architecture/codebase-map-2026-08-13.md` | 两文件在 codebase map 中按"活跃组件"登记，实为**待接线/死代码**（`/api/rtsp/*` 为 501 桩） | 🟡 修正 map 状态标记 |
| **9** | **VRAM 预算 ≤ 11.5GB（对外承诺值）** | `DELIVERY.md` §4 验收对照、`ARCHITECTURE.md` §8 | 该值**源自 11 进程方案**，与"每秒决策"同属一份已废弃推导稿。`ARCHITECTURE.md` §8 已标「⚠️ 待重算」 | 🟠 **已识别，待执行**：`DELIVERY.md` §4 仍写「✅ 显存预算 11.5GB」为**已满足**，与 `ARCHITECTURE.md` 的"待重算"**互相矛盾** |

### 4.1 一句话总结连带影响

> **「每秒决策」这一伪前提，其上承载了 3 类结论：①推理 QPS 容量（错 5× 或 ∞×）；②端到端 P99 的前提与可追溯性（无实测）；③"持续推理导致显存/上下文吃紧"的担忧（已被 `ARCHITECTURE.md` §8 与 `vlm-lightweight-2026-09.md` 各自独立证伪）。**
> **而它最大的连带后果不在性能侧，在记忆侧：因为 proactive 决策不留痕，本项目当前**没有任何载体**能回答"AI 上一轮为什么说话/为什么沉默"——这使 `live-visual-cb.md` 要求的"真机验收主动搭话质量"在方法上不可执行。**

---

## 5. 审计方法与覆盖范围

### 5.1 检索模式（穷尽清单）

| 类别 | 检索模式 | 命中量 |
|---|---|---|
| 中文频率 | `每秒`、`逐秒`、`一秒`、`1 ?fps`、`1FPS` | 167 处 / 42 文件 |
| 中文语义 | `持续决策`、`实时决策`、`自主决策`、`主动搭话`、`主动发言`、`常驻`、`持续看`、`持续观察`、`实时推理` | 189 处 / 52 文件（**大/小写不敏感 + `**/*.md` 递归**） |
| 英文 | `per-second`、`every second`、`real-time decision`、`continuous`、`proactive`、`always-on`、`1 fps` | 21 处 / 15 文件 |
| 性能承诺 | `P99`、`1\.2s`、`1200 ?ms`、`latency`、`延迟` | 157 处 / 16 文件（仅 P99 子集） |
| 连线结论 | `qa_history`、`KV 缓存`、`VRAM`、`11\.5GB`、`显存预算`、`每 100 帧` | 155+ 处 / 20+ 文件 |
| 代码侧 | `LIVE_PROACTIVE_ENABLED`、`LIVE_PROACTIVE_INTERVAL_S`、`LIVE_FRAME_WINDOW`、`proactive_speak_enabled`、`VideoProcessorTrack`、`RTSPVideoTrack`、`501`、`chunk:`、`compress_every_n_chunks` | 84+ 处 |

**覆盖目录**：仓库根（`ARCHITECTURE.md` / `DELIVERY.md` / `AGENTS.md`）、`决策/`（全 22 文件）、`doc/`（全树：`specs/` / `adr/` / `research/` / `subsystems/` / `architecture/` / `standards/` / `frontend/` / `local/` / `main/` / `deprecated/`）、`reports/`、`prompts/`、`services/*/README*`、`services/**/*.py`、`services/webui/src/**/*.js`、`scripts/`、`design/`、`datasets/`、`config/`、`install/`。

### 5.2 已知遗漏与盲区（诚实声明）

| # | 盲区 | 说明 |
|---|---|---|
| 1 | **`.cache/` 未纳入判定** | 该目录含 279+ 个 README/文档散件（含多个 worktree 快照、uv 包缓存、deepsec/idesign 临时件）。检索命中但**均判定为非 SSOT 副本**，未逐条列表。若其中有被引用的活文档，需单独复查 |
| 2 | **`archive/` 未深查** | 仅确认存在 `AGENTS.codex-legacy.md`。判定为归档区，未逐文件核 |
| 3 | **PDF 未解析** | 仓库根有 `JoyAI-VL-Interaction-Reportv1.pdf`（5.0 MB）。**未解析其中是否有"每秒决策"表述**。若该 PDF 是对外交付物，**须单独审计** |
| 4 | **`design/joyai-redesign-preview.html` 部分命中** | 命中「1fps 截图上传」等，属 UI 预览稿，判定为非 SSOT |
| 5 | **行号按要求未记录** | 按任务要求，全部以"文件 + 章节/标题 + 原文摘录"定位，**未写行号**。故修正时需按摘录文本检索定位 |
| 6 | **未做 git 历史追溯** | 未用 `git log -S` 追溯"每秒决策"是**何时、由哪次提交**引入 `ARCHITECTURE.md`。本审计已通过文档自述（"由 9 份生成式架构交付稿提炼而成"）**间接定位源头**，但未做提交级确认 |
| 7 | **未验证运行时行为** | 严格遵守只读约束：**未启动服务、未跑 pytest、未发请求**。所有代码依据为**静态读取**。proactive 默认关、5s 间隔、不进 `qa_history` 等采信任务书已确认的实测结论 |
| 8 | **`/api/live/proactive` 的实际路由行为** | 已读 `live_routes.py` 文档字符串与 `live_proactive.proactive_switch_action` 分支逻辑，但未实机调用 |

### 5.3 判定标准（本次四类判定的具体含义）

| 判定 | 含义 | 本次实例 |
|---|---|---|
| ✅ **准确** | 与代码实际行为一致，无需修改 | B4/B5/B6/C1/C2/E1/E2 等 |
| ⚠️ **夸大/误导** | 声称的能力**存在但只在特定触发下**，或**默认关闭**，或**未标注前提条件** | A3/B1/B2/B7/D1/D3/D4/E3 等 |
| ❌ **错误** | 代码里**根本不存在**该机制，或数值/单位**客观错误** | A1/A2/A4–A12/D5/D6/D7 |
| ❓ **无法判定** | 需更多信息 | **本次 0 条**——所有条目均已定位代码依据 |

### 5.4 核心方法学结论（供后续审计复用）

> **"1 fps 采集" 与 "每秒决策" 是本仓库最易混淆的一对。** 检索时二者共享 `1 fps` / `每秒` 关键词，但**语义完全不同**：
> - **1 fps 采集**：✅ 真（`screen_capture.js` / `WS frame` 管线）。
> - **每秒决策**：❌ 假（无任何 1 Hz 决策循环；1 fps 通道 `VideoProcessorTrack` 连 token 都不解析）。
>
> **判定任何"实时/持续"表述时，必须同时回答两个问题**：① **谁是触发源？**（用户说话 / 定时器 / 帧到达）② **默认开还是关？** 本仓库**所有**周期性能力（proactive / 画面采集 / 屏幕捕获）**默认均为关闭或需手动开启**——凡未标注此点的"持续/常驻/实时"表述，一律应判 ⚠️ 或 ❌。

---

## 6. 修正优先级建议（执行顺序）

| 顺序 | 动作 | 文件 | 理由 |
|---|---|---|---|
| **P0** | 修 §1 + §4 的"每秒自主决策" | `ARCHITECTURE.md` | 架构总纲第一句，污染面最大 |
| **P0** | 修「每 100 帧」→「每 200 轮」 | `ARCHITECTURE.md` §2 | 数值+单位双错，影响记忆负载判断 |
| **P0** | 给 P99 行加前提标注 + 来源声明 | `ARCHITECTURE.md` §8 | 唯一非功能硬指标，当前不可追溯 |
| **P1** | 标注 proactive 默认关 | `live-visual-cb.md` / `live-interaction-layer.md` / `turn-controller-integration.md` | 三处"Implemented"缺默认关说明 |
| **P1** | 给 `deprecated/delivery-handoff-2026-07/` 全族加废弃横幅 | 9 文件 | 防继承性漂移扩散 |
| **P2** | 修 `DELIVERY.md` §16.1 前提条件 + §4 VRAM 矛盾 | `DELIVERY.md` | 与 `ARCHITECTURE.md` 互相矛盾 |
| **P2** | 决策事件流缺口立项 | `决策/业务-决策记忆.md`（新增条目） | **记忆侧最大连带风险**，阻塞 C.B 真机验收 |
| **P3** | 修 §6 的 `~5.8GB VRAM` 残留 | `ARCHITECTURE.md` | 文件内自相矛盾 |
| **P3** | 修正 `VideoProcessorTrack` 状态标记 | `codebase-map-2026-08-13.md`、`2026-07-13-current-state.md` | 死代码被登记为活跃组件 |

---

> **报告结束。** 本报告为只读审计产出，**未修改本仓库任何其他文件**。
> 审计端点（AFK）｜ 2026-09-20
