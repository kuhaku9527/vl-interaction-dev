# 交互模式与决策 Token 规范（跨域 L4）

> 本文件记录 **三交互模式隔离（live/call/jarvis）+ decision token 处理规范** 的已确定决策。
> 来源 spec: `doc/specs/interaction-mode-isolation.md`；落地 PR #71（前端）/ #72（后端）；main=`6df743b`（2026-08-03）。
> 覆盖 L4 `D-2026-08-03-001` ~ `D-2026-08-03-002`。

---

## D-2026-08-03-001  三交互模式语义与边界（live/call/jarvis）

- **事实**: 系统有三种交互模式，语义隔离、不互通：live（源项目既有，三判断：沉默/主动搭话/回复）、call（纯语音转文字→聊天框，等同打字，无模型决策语义）、jarvis（常驻监听→唤醒→任务→结束词静默退出，静默退出靠 `decision` 字段的既有实现）。call/jarvis 的 user-facing 输出不得含决策 token；live 保留完整三判断。
- **来源**: spec `doc/specs/interaction-mode-isolation.md` + PR #71/#72（2026-08-03 合入 main=`6df743b`）
- **校验**: `grep -rn "interaction_mode" services/webinfer/infer_loop.py services/webui/src/joy_interaction_webui/server.py` → webinfer 读取并透传 live/call/jarvis；call 路径传 `"call"`、jarvis 唤醒词传 `"jarvis"`；`grep -n "NO_DECISION_SYSTEM_PROMPT" services/webinfer/prompt_constants.py` → call 用无 token prompt
- **预期**: 三模式行为隔离；call/jarvis 不露决策 token；live 三判断不变
- **Drift**: 无
- **Owner**: 架构 / 前端 / 后端
- **锁定**: 🔒

---

## D-2026-08-03-002  decision token 处理规范（剥离只作用于 content，decision 字段保留）

- **事实**: 决策 token（`</?silence>`/`</?response>`/`</?delegation>`）剥离**只作用于** `_chat_payload_finalize` 的 user-facing `content`（`strip_decision_tokens`）；`decision` 字段由 `parse_model_decision(ctx.raw_text)` 解析（raw_text 保留 token，不得剥离）。jarvis 静默退出靠 `harness["decision"]`（jarvis_mode.py:1218/1261），**不得破坏**。前端 `getVlmDisplayText`（index.html）为唯一清洗源。`force_silence_before_query` 仅 live+无 query 生效（`_is_forced_silence`）。
- **来源**: spec + PR #71/#72 + 代码 `services/webinfer/response_format.py:110`(strip 正则) / `infer_loop.py:758`(_chat_payload_finalize) / `:535`(_is_forced_silence) / 前端 `index.html`:getVlmDisplayText / `jarvis_mode.py:1218,1261`
- **校验**: `grep -n "def strip_decision_tokens" services/webinfer/response_format.py` → 定义（6 变体+大小写+内部空白）；`grep -n "decision = parse_model_decision(ctx.raw_text)" services/webinfer/infer_loop.py` → :758 附近；`grep -n 'harness\["decision"\]\|decision != "silence"' services/webui/src/joy_interaction_webui/jarvis_mode.py` → 静默退出 intact
- **预期**: user-facing content 无 token；decision 字段有 token；jarvis 静默退出功能 intact
- **Drift**: 无
- **Owner**: 后端 / 前端
- **锁定**: 🔒

---

## 关联索引

- 推理网关契约：见 `服务-webinfer.md`（D-023~035）
- 前端清洗源：见 `服务-webui.md`（D-032~039）
- 端口 / 启动：见 `跨域铁律.md`、`启动链路.md`
- 质量门禁（ruff format）：见 `工程规范.md`（D-2026-08-03-003）

---

## D-2026-08-17-001  角色命名统一：BT-7274（「贾维斯」仅内部技术名）

- **事实**: 用户拍板——产品层面角色名统一为 **BT / BT-7274**（泰坦陨落系列），**不再使用「贾维斯」**作为产品称呼。`jarvis` 仅保留为**内部技术标识**（模式名/代码/唤醒词语境，便于 AI 理解），不算产品名。
- **影响**:
  - 唤醒词/称呼产品语义：一律 BT、BT-7274、铁驭（措辞规范：**「铁驭」**为官方译名，旧文档/脚本中的「铁御」为错别字，已废弃，2026-08-17 修正 `generate_event_audio.py` EVENTS 文本 + `prompts/bt/events/README.txt`）。
  - 文档：旧 spec/决策中把角色叫「贾维斯」的表述**不再更新为产品名**（保持技术语境），但新文档一律用 BT-7274；本条目作为命名 SSOT 标注旧称废弃。
  - 代码：`jarvis_mode.py` / `jarvis_state.py` / `interaction_mode="jarvis"` 等技术标识**不改名**（改名成本高、无产品收益，且「jarvis 便于 AI 理解」）。
  - 唤醒词检测（`silence_control.py:80` 名字 token 含「贾维斯」）：live 喊「贾维斯」会唤醒——**保留**（BT 角色可响应曾用名，产品语义待后续可选收紧）。
- **校验**: `grep -rn "贾维斯" 决策/ doc/specs/` → 出现处均为技术语境（jarvis 模式的称呼描述），不再新增产品用途；`grep -n "铁驭\|铁御" services/scripts/generate_event_audio.py prompts/bt/events/README.txt` → 仅「铁驭」。
- **预期**: 产品对外统一 BT-7274；jarvis 仅内部技术名；措辞「铁驭」全项目一致。
- **Drift**: 无
- **Owner**: 产品 / 架构
- **锁定**: 🔒

---

## D-2026-09-19-001  ★ 战略转变：KWS 降级为静默模式内的可选开关，jarvis 模式标记待收敛

- **事实**: 用户拍板（2026-09-19）——**唤醒词（KWS）自训效果差、收敛差、唤醒率低，必须靠其它通道兜底**
  （实测基线 FAR 2% / recall 49%，见 `doc/research/kws-v5-2026-08-10-diagnosis.md`）。
  因此转变战略：
  1. **不再把 KWS 当作主入口**。原「jarvis 唤醒模式」（用户称之为**开机词**：没通话时用唤醒词
     进入对话）**降级为待收敛状态，后续应删除**。
  2. 唤醒词**收编为「无线电静默」模式内的一个开关**：静默中可选让它继续工作，
     用唤醒词作为**静默结束（被召唤）**的通道之一 —— 这与 `radio-silence.md §2/§4/§5`
     既有的「唤醒三通道（KWS 若开 / 组合键 / 语音）」设计**一致**，是该设计的自然延伸。
  3. **实现路径倾向用 ASR 而非 KWS**：用户明确指出「这个功能的实现其实可以直接靠 ASR 来实现，
     KWS 的效率实在是太低了」，具体方案待定（"到时候再说"）。
- **影响**:
  - **产品**：唤入口从「模式切换（jarvis ↔ live）」收敛为「live 常驻 + 静默子状态」单线；
    不再有独立的"开机词"概念。
  - **前端**：`#btListenBtn`（Jarvis 唤醒）与 `#liveModeBtn`（实时）现为**互斥 radio** ——
    按此决策，`btListenBtn` 属待删项（**当前不擅自删除**，等收敛实施时一并处理）。
  - **后端**：`jarvis_mode.py` / `jarvis_state.py` / `interaction_mode="jarvis"` 暂时保留
    （`决策/交互模式与决策token规范.md` D-2026-08-03-001 的三模式隔离**在收敛前仍然有效**，
    不得提前破坏；收敛时需同步改该条）。
  - **KWS 本体**（`services/kws-training/`、`services/asr/jarvis/kws.py`）暂留，
    但**不再有产品主路径依赖**；若 ASR 方案落地，KWS 可退为可选。
- **校验**:
  - `grep -n "唤醒" doc/specs/radio-silence.md` → 唤醒三通道设计已存在（本决策是收编而非新建）
  - `grep -rn "btListenBtn" services/webui/src/joy_interaction_webui/static/` → 当前仍在（待删标记）
  - `grep -n "live 为当前主线，jarvis 模式之后搞" doc/specs/live-interaction-layer.md` → 既有战略与本决策同向
- **预期**: KWS 从"主入口"降为"静默态的兜底通道之一"；jarvis 模式在收敛完成后删除；
  单线交互（live + 静默子状态）。
- **与既有决策的关系**: **不冲突、是延伸**。D-2026-08-03-001 定义三模式**隔离**（隔离仍要维持到收敛完成）；
  本条定义**收敛方向**（三模式 → 单线）。`live-interaction-layer.md §0`「jarvis 收敛到同一核心」
  是本条的**上游战略**，本条给出**收敛后的形态**。
- **已知文档漂移（本决策附带发现，待修）**:
  `doc/specs/draft-mode-isolation-boundaries.md:17` 写「"Jarvis 唤醒"按钮 → **`/api/jarvis/start`**」，
  但该端点在代码中**不存在**（实测 `grep -rn "api/jarvis/start" services/webui/.../*.py` → 零命中）。
  实际唤醒链走 `/offer` + `jarvis_audio:true`（WebRTC），由 `server.py` 的 offer handler 建会话。
  → 该行应修正为实况，或随 jarvis 删除一并移除。
- **Drift**: ⚠️ 有（上述文档漂移；另：前端 `#btListenBtn` 链路完整但产品上已判定为待删）
- **Owner**: 产品 / 架构 / 前端
- **锁定**: 🔒（战略方向已拍板；**具体实施待排期**，实施时须回写本条的"已完成"状态）

