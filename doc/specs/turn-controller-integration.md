# Spec：Turn Controller 接入设计（Integration）——三阶段蓝图定稿

> 生命周期: **正式**（2026-08-13 定稿，走 草稿→设计评审→实现→QA→真机 流程；Phase A/B 已实现并通过 QA，Phase C 已实现待真机验收）
> 上游: `unified-turn-controller.md`（v2 状态机，已定稿正式）+ `turn_controller.py` 原型（64 测试绿）+ `live-interaction-layer.md`（Phase C.A）+ `live-visual-cb.md`（Phase C.B）
> 原则: 渐进接入、绝不破坏现有行为、可回滚；原型已隔离验证，接入分阶段
> 状态: 三阶段蓝图（Phase A ✅ 影子 / Phase B ✅ 收敛委托（默认关，待真机回归后开）/ Phase C ✅ C.A+C.B 已实现，待真机验收）

---

## §1 目标与范围

- **目标**：把统一 TurnController（原型）渐进接入现有 webui 路径，先服务 jarvis（回归验证），再开 live（新能力），最终验证"直播=核心、jarvis=配置"的全局观。
- **范围**：webui 的 jarvis_mode.py / live_mode.py / 相关 handler；**不动** webinfer decision token（核心 IP）、不动 ASR/KWS/TTS 引擎。
- **不做**：一次性替换 jarvis 状态机（风险高）；不改 decision token 语义；不引入新依赖。
- **已完成**：
  - Phase A（影子，observe-only，commit 95c3df6，104 测试绿：14 shadow + 64 原型 + 26 QA）；
  - Phase B（DIALOG_ACTIVE 委托，legacy 逐字保留 + env 闸门**默认关**，commit 5136e74/7afcf08，QA 独立套件 test_jarvis_turn_delegate_qa 7 点验证）；
  - B1（KWS 静音误唤醒 P0 修复，commit f7645d1/502bccb）；
  - Phase C.A（live 常驻监听层，commit b08c856，见 `live-interaction-layer.md`）——live_mode 直接驱动 `TurnController(TurnConfig.live())`；
  - Phase C.B（VLM 视觉 + 主动搭话，commit a536ef3/80caf37/d9736ee/08ab0c4，见 `live-visual-cb.md`）——状态 Implemented（待真机验收）。
- **待办**：打断延迟优化（见 `doc/research/bargein-latency-analysis-2026-08-12.md`，P0 前端先行停 TTS）；B2 EXIT_WORDS 加"再见"；B3 退出语义（用户待定）；Phase B 正式启用（真机回归后置 `JARVIS_TURN_DELEGATE_ENABLED=true`，见 §5 决策记录）。

## §2 接入策略（三阶段，每阶段可独立验收/回滚）

### Phase A：影子模式（shadow）——只观测，不干预 ✅ 已实现（95c3df6）

- turn_controller 实例化（jarvis 预设）**与 jarvis 状态机并行运行**，事件源相同（同一音频流 + ASR partial + LLM token），**只记录状态转移日志，不驱动任何动作**。
- 目的：验证 turn_controller 的状态序列与 jarvis 真实状态序列**一致性**（真机跑一轮对话，对比两条状态链）。
- 交付：`services/webui/src/joy_interaction_webui/turn_controller_shadow.py`（薄封装，接 jarvis 事件，只 log）。
- **开关**：`JARVIS_TURN_SHADOW_ENABLED`（默认关）。关 = 不创建 shadow，jarvis 行为字节不变；开 = 创建 shadow 并记录 `[turn-shadow]` 对齐日志。init/运行时错误仅 log（fail-open），绝不破坏 jarvis。
- **保留理由（delegate 落地后仍保留）**：shadow 是**回归观测工具**——默认路径（delegate 关）下验证 legacy 状态链与统一核心的对齐度；delegate 开启后仍可并行观测委托路径。运行时只接线 `on_jarvis_transition`（状态转移粒度）；`on_audio_activity`/`on_asr_partial`/`on_llm_token`/`on_tts_event`（L1/L2/L3 事件粒度）是影子观测 API，由测试覆盖（test_turn_controller_shadow.py），供真机对齐运行使用——**保留，非死代码**。
- 验收：真机对话 N 轮，影子链与 jarvis 链关键节点对齐率 ≥95%（人工核对日志）；失败仅 log，jarvis 行为零变化。
- **回滚**：删除 shadow 挂载点即可（零侵入）。

### Phase B：jarvis 状态机收敛——jarvis 专属态映射到统一核心 ✅ 已实现（5136e74，默认关）

- jarvis_mode.py 的状态机**骨架保留**，但 DIALOG_ACTIVE 轮次节奏改为**调用 turn_controller**（jarvis 预设）仲裁；jarvis 专属逻辑（KWS/唤醒确认/退出词）挂在 turn_controller 的**模式扩展**上：

| jarvis 态 | turn_controller 态 | 归属 |
|---|---|---|
| KWS_LISTENING | LISTENING（+wake_gate 生效） | 核心 |
| WAIT_ASR_CONFIRM | WARM_UP 子流程（唤醒确认） | 扩展（jarvis 专属） |
| WAKE_DETECTED | WARM_UP（播 wake.wav） | 扩展 |
| DIALOG_ACTIVE | USER_SPEAKING/PROCESSING/THINKING/SPEAKING | 核心 |
| TTS_PAUSED | HARD_INTERRUPTED → COOLDOWN → LISTENING | 核心 |
| EXIT_DETECTED | ENDED（退出语义=end_semantics 配置） | 扩展 |
| ERROR | ERROR | 核心 |

- **实现形态（§5 待明确 #1 已裁定）**：jarvis 内部委托 turn_controller（`TurnControllerDelegate` 主动仲裁适配器，见 `turn_controller_delegate.py`）——侵入大但彻底；不用"双状态机事件同步"（避免双状态维护成本）。
- **开关**：`JARVIS_TURN_DELEGATE_ENABLED`（默认关）。关 = 不创建 delegate，`_handle_dialog_legacy` 逐字保留，jarvis 行为字节不变（**生产默认**）；开 = DIALOG_ACTIVE 轮次由 turn_controller 仲裁（验收运行用）。fail-open：任何 delegate 错误 → 禁用 delegate + 回退 legacy，后续轮次照常提交。
- **关键**：jarvis 现有**行为不变**（唤醒/确认/退出/打断节奏），只是 DIALOG_ACTIVE 状态机实现换底——**这是本阶段验收核心**。
- 交付：jarvis_mode.py env 闸门 + `_handle_dialog` 分发器（legacy/delegated）+ 共享 `_handle_dialog_commit` + `_transition_to` 生命周期钩子 + LLM/TTS 钩子（on_llm_response_token / on_tts_started / on_tts_finished，state-guarded）。
- 验收：现有 jarvis 测试全绿 + 行为等价性（同一脚本序列 legacy vs delegated → jarvis 状态链与 `_send_to_llm` 副作用完全一致，QA 套件断言）+ 真机唤醒/退出/打断回归。

### Phase C：live 接入——无唤醒门常驻 + 主动搭话 ✅ 已实现（C.A b08c856 / C.B live-visual-cb，待真机验收）

- **C.A 常驻监听形态**（`live-interaction-layer.md`）：用 turn_controller（live 预设，wake_gate=False）驱动 live 路径：常驻 LISTENING → USER_SPEAKING → 决策（复用 webinfer decision token）→ SPEAKING，含打断/冷却。
- 与 live 三判断（webinfer 决策 token 驱动）的关系：**决策 token 仍是 L3 语义判定源**（核心 IP 不动），turn_controller 提供**状态机骨架与节奏控制**（何时进/出 LISTENING、打断、冷却、PRE_SPEECH 填充语）。
- **C.B 完整直播形态**（`live-visual-cb.md`）：VLM 持续看画面 + 主动搭话 + 沉默判断自主发言。层 1-3 已实现（604 测试 QA PASS），**待真机验收**（开 proactive 观察主动搭话质量；画面问答测试）。
- 验收：直播模式主动搭话/打断节奏（块3 目标：打断响应 <200ms 目标值，GPU(LLM)+CPU(语音侧) 混合部署可达性实测）；jarvis 回归不受影响。

## §3 事件桥接设计（原型 ↔ 现有管线）

| 原型事件 | 来源（现有管线） | 说明 |
|---|---|---|
| on_speech_started | vad_bypass.is_speech() 上升沿（音频帧） | L1 声学 |
| on_speech_stopped | vad_bypass.is_speech() 下降沿（silence 时长） | L1 |
| on_partial_transcript | jarvis shadow ASR / ASR partial（is_final 区分） | L2 输入 |
| on_llm_token / ttft 超时 | webinfer 流式 token 回调（或 llm 调用 wrapper） | L3 执行态 |
| on_turn_commit | → 触发 LLM 调用（现有 `_send_to_llm` 路径） | 动作接线 |
| on_barge_in | → 停止 TTS + 截断历史（现有 TTS_PAUSED 逻辑） | 动作接线 |
| on_pre_speech_filler | → 播放填充语（新，需 TTS 支持） | 新能力（Phase C 边界，B 阶段 log-only） |

- **时钟**：原型已支持 clock 注入——接入时用真实 `time.monotonic`。
- **并发**：jarvis `run()` 是单 asyncio 循环逐帧处理，turn_controller 事件调用为同步（≤1ms），无额外竞争。

## §4 风险与回滚

| 风险 | 缓解 |
|---|---|
| Phase B 改 jarvis 生产路径引入回归 | 影子模式先验证一致性；每阶段独立验收；git 已存档（29dd71e/40ee9dc/e210495）可回滚；env 闸门默认关 = 生产默认 legacy 路径 |
| 打断节奏变化（双阈值 0.65/0.75 vs jarvis 现状） | Phase B 参数默认对齐 jarvis 现状（jarvis 预设），新阈值仅 live 预设启用 |
| PRE_SPEECH 填充语需 TTS 打断支持 | Phase C 才启用；先验证填充语本身，打断边界作 P2 |
| live 接入与 webinfer 决策 token 双控制源冲突 | 明确职责：decision token=语义判定，turn_controller=状态机节奏；仲裁规则写入设计 |
| delegate 卡死（PROCESSING 卡住吞掉下一轮） | fail-open 钩子：delegate 内部错误 → 禁用 + 回退 legacy（5136e74 闭合，QA 验证） |

## §5 决策记录（2026-08-13 定稿）

**D-2026-08-13-TC1：Phase B delegate 默认关 = 安全基线；真机回归通过后置 `JARVIS_TURN_DELEGATE_ENABLED=true`**

- **事实**：Phase B 委托已实现并通过测试层 QA（行为等价性：同一脚本序列 legacy vs delegated 状态链/副作用完全一致；fail-open 深度；env 闸门值矩阵；退出词仍 jarvis 专属）。但生产启用前需**真机回归**（唤醒/退出/打断节奏在真实音频链上的不变性）——测试层证明的是逻辑不变性，不覆盖真实 ASR/KWS/TTS 延迟与音频行为。
- **决定**：`JARVIS_TURN_DELEGATE_ENABLED` 保持默认关（= legacy 路径，生产默认）。真机回归通过后置 true（在 `run-windows.env` 或环境变量设置）。
- **启用门槛（真机回归清单）**：
  1. 唤醒（KWS → WAKE_DETECTED → DIALOG_ACTIVE）节奏与 legacy 一致；
  2. 对话提交（2s ASR 停滞 → commit → LLM）时序一致；
  3. 打断（TTS 播放中说话 → TTS_PAUSED → COOLDOWN → LISTENING）节奏一致；
  4. 退出词（EXIT_DETECTED → goodbye）行为一致；
  5. 影子对齐日志（`[turn-shadow]`）在 delegate 开启下仍显示核心与 jarvis 链对齐。
- **未达门槛**：保持默认关，回退 legacy，报告偏差项。
- **Owner**: 架构 / 后端。

**D-2026-08-13-TC2：shadow 保留为回归观测工具**

- **事实**：Phase B delegate 落地后，shadow（Phase A）仍保留——它是**默认路径（delegate 关）下验证 legacy 状态链与统一核心对齐度**的唯一观测工具；delegate 开启后可并行观测委托路径。
- **决定**：`JARVIS_TURN_SHADOW_ENABLED` 保持默认关（观测工具，按需开启）。运行时只接线 `on_jarvis_transition`；L1/L2/L3 事件粒度方法（on_audio_activity / on_asr_partial / on_llm_token / on_tts_event）为影子观测 API，测试覆盖 + 真机对齐运行可用——保留，非死代码。
- **Owner**: 后端。

## §6 关联

- 状态机/配置：`unified-turn-controller.md`（v2，已定稿正式）
- live 接入：`live-interaction-layer.md`（C.A）+ `live-visual-cb.md`（C.B）
- 原型：`services/webui/src/joy_interaction_webui/turn_controller.py`（64 测试）
- 委托/影子：`turn_controller_delegate.py`（Phase B）/ `turn_controller_shadow.py`（Phase A）
- jarvis 现状：`jarvis_mode.py`（run() 主循环，`_handle_dialog` 分发器 :823，env 闸门 :179-228）
- 决策 token 规范：`决策/交互模式与决策token规范.md` D-2026-08-03-001/002
- 提交存档：29dd71e（fix）/ 40ee9dc（feat 原型）/ e210495（docs）/ 95c3df6（Phase A）/ 5136e74+7afcf08（Phase B）/ b08c856（Phase C.A）/ a536ef3..08ab0c4（Phase C.B）
