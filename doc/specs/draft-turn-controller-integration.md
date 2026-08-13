# Spec Draft：Turn Controller 接入设计（Integration）

> 生命周期: **草稿 v2**（2026-08-12）——Phase A/B 已实现并通过 QA（commit 95c3df6/5136e74 等），状态同步见 §2；走 草稿→沙箱验证→替换 流程
> 上游: `unified-turn-controller.md`（v2 状态机，已定稿正式）+ `turn_controller.py` 原型（64 测试绿）+ jarvis_mode.py 现状
> 原则: 渐进接入、绝不破坏现有行为、可回滚；原型已隔离验证，接入分阶段
> 状态: 设计草稿（Phase A ✅ 影子 / Phase B ✅ 收敛委托 / Phase C ⏳ 待做）

---

## §1 目标与范围

- **目标**：把统一 TurnController（原型）渐进接入现有 webui 路径，先服务 jarvis（回归验证），再开 live（新能力），最终验证"直播=核心、jarvis=配置"的全局观。
- **范围**：webui 的 jarvis_mode.py / 相关 handler；**不动** webinfer decision token（核心 IP）、不动 ASR/KWS/TTS 引擎。
- **不做**：一次性替换 jarvis 状态机（风险高）；不改 decision token 语义；不引入新依赖。
- **已完成**：Phase A（影子，100% 真机对齐，commit 95c3df6）；Phase B（DIALOG_ACTIVE 委托，legacy 逐字保留 + env 闸门默认关，commit 5136e74/7afcf08）；B1（KWS 静音误唤醒 P0 修复，commit f7645d1/502bccb）。
- **待办**：Phase C（live 接入）；打断延迟优化（见 `doc/research/bargein-latency-analysis-2026-08-12.md`，P0 前端先行停 TTS）；B2 EXIT_WORDS 加"再见"；B3 退出语义（用户待定）。

## §2 接入策略（三阶段，每阶段可独立验收/回滚）

### Phase A：影子模式（shadow）——只观测，不干预
- turn_controller 实例化（jarvis 预设）**与 jarvis 状态机并行运行**，事件源相同（同一音频流 + ASR partial + LLM token），**只记录状态转移日志，不驱动任何动作**。
- 目的：验证 turn_controller 的状态序列与 jarvis 真实状态序列**一致性**（真机跑一轮对话，对比两条状态链）。
- 交付：`services/webui/src/joy_interaction_webui/turn_controller_shadow.py`（薄封装，接 jarvis 事件，只 log）。
- 验收：真机对话 N 轮，影子链与 jarvis 链关键节点对齐率 ≥95%（人工核对日志）；失败仅 log，jarvis 行为零变化。
- **回滚**：删除 shadow 挂载点即可（零侵入）。

### Phase B：jarvis 状态机收敛——jarvis 专属态映射到统一核心
- jarvis_mode.py 的状态机**骨架保留**，但内部转移改为**调用 turn_controller**（作为唯一状态仲裁者），jarvis 专属逻辑（KWS/唤醒确认/退出词）挂在 turn_controller 的**模式扩展**上：

| jarvis 态 | turn_controller 态 | 归属 |
|---|---|---|
| KWS_LISTENING | LISTENING（+wake_gate 生效） | 核心 |
| WAIT_ASR_CONFIRM | WARM_UP 子流程（唤醒确认） | 扩展（jarvis 专属） |
| WAKE_DETECTED | WARM_UP（播 wake.wav） | 扩展 |
| DIALOG_ACTIVE | USER_SPEAKING/PROCESSING/THINKING/SPEAKING | 核心 |
| TTS_PAUSED | HARD_INTERRUPTED → COOLDOWN → LISTENING | 核心 |
| EXIT_DETECTED | ENDED（退出语义=end_semantics 配置） | 扩展 |
| ERROR | ERROR | 核心 |

- 关键：jarvis 现有**行为不变**（唤醒/确认/退出/打断节奏），只是状态机实现换底——**这是本阶段验收核心**。
- 交付：jarvis_mode.py 改造（`JarvisStateMachine` 内部委托 turn_controller；或保留原状态机、turn_controller 作事件源仲裁——实现细节原型验证后定）。
- 验收：现有 jarvis 测试全绿 + 真机唤醒/退出/打断回归。

### Phase C：live 接入——无唤醒门常驻 + 主动搭话
- 用 turn_controller（live 预设，wake_gate=False）驱动 live 路径：常驻 LISTENING → USER_SPEAKING → 决策（复用 webinfer decision token）→ SPEAKING，含打断/冷却。
- 与现有 live 三判断（webinfer 决策 token 驱动）的关系：**决策 token 仍是 L3 语义判定源**（核心 IP 不动），turn_controller 提供**状态机骨架与节奏控制**（何时进/出 LISTENING、打断、冷却、PRE_SPEECH 填充语）。
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
| on_pre_speech_filler | → 播放填充语（新，需 TTS 支持） | 新能力 |

- **时钟**：原型已支持 clock 注入——接入时用真实 `time.monotonic`。
- **并发**：jarvis `run()` 是单 asyncio 循环逐帧处理，turn_controller 事件调用为同步（≤1ms），无额外竞争。

## §4 风险与回滚

| 风险 | 缓解 |
|---|---|
| Phase B 改 jarvis 生产路径引入回归 | 影子模式先验证一致性；每阶段独立验收；git 已存档（29dd71e/40ee9dc/e210495）可回滚 |
| 打断节奏变化（双阈值 0.65/0.75 vs jarvis 现状） | Phase B 参数默认对齐 jarvis 现状（jarvis 预设），新阈值仅 live 预设启用 |
| PRE_SPEECH 填充语需 TTS 打断支持 | Phase C 才启用；先验证填充语本身，打断边界作 P2 |
| live 接入与 webinfer 决策 token 双控制源冲突 | 明确职责：decision token=语义判定，turn_controller=状态机节奏；仲裁规则写入设计 |

## §5 待明确事项（供用户/架构评审）

1. Phase B 采用「jarvis 内部委托 turn_controller」还是「双状态机事件同步」（前者侵入大但彻底，后者保守但双状态维护成本）？
2. live 模式启动方式：复用 jarvis 会话框架（jarvis_session）开 live 配置实例，还是独立 live 路径？
3. PRE_SPEECH 填充语是否进入 MVP（Phase C）还是 P1？
4. 影子模式对齐率阈值 95% 是否合适？

## §6 关联

- 状态机/配置：`unified-turn-controller.md`（v2，已定稿正式）
- 原型：`services/webui/src/joy_interaction_webui/turn_controller.py`（64 测试）
- jarvis 现状：`jarvis_mode.py`（run() 主循环 :696-729，_handle_kws :735）
- 决策 token 规范：`决策/交互模式与决策token规范.md` D-2026-08-03-001/002
- 提交存档：29dd71e（fix）/ 40ee9dc（feat 原型）/ e210495（docs）
