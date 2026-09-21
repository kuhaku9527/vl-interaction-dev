# JoyAI-VL-Interaction 文档库

> **最近更新**: 2026-09-14 | **状态**: ✅ 索引已重建（`specs/README.md` 覆盖全部 46 份；**8 个目录**各有索引：specs / subsystems / adr / research / acceptance / architecture / docs / reports）
>
> **如何读这页**：新人先按 👇 入口路径走（35 分钟入门）。需要查特定子系统的设计/规格/决策时，按"分类索引"找。历史文档 `deprecated/` 不进常规阅读路径。
>
> ⚠️ **环境特定内容纪律（2026-09-14 立）**：本项目历经 WorkBuddy / Codex / DSH 三种 agent 环境。
> **凡条目主语是"某 agent 的沙箱/工具"（而非项目本身）的，一律不属于真值源** → 归 `history-agent-environments.md`。
> 当前环境的约束见 `environment-dsh.md`。**发现环境特定内容混入 `决策/` 或活文档时，按此纪律移出。**
>
> **关键文档（最常被读到）**：
> - 📌 [`specs/README.md`](specs/README.md) — **正式 spec 索引（最新，优先看这个）**
> - 📌 [`specs/2026-07-13-current-state.md`](specs/2026-07-13-current-state.md) — 项目现状（端口、模块流程、风险表）
> - 📌 [`specs/2026-07-13-llm-path-consolidation.md`](specs/2026-07-13-llm-path-consolidation.md) — LLM 网关单入口（B 选项实施合同，已 ✅ 实施）
> - 📌 [`specs/2026-07-14-loose-coupling-services.md`](specs/2026-07-14-loose-coupling-services.md) — 4-API config + 单 webinfer 主路 + 3 独立 capture（Phase 2A/B 实施合同，已 ✅ 实施）
> - 📌 [`specs/2026-07-14-project-audit.md`](specs/2026-07-14-project-audit.md) — 项目审查（基于 HEAD=021f429 代码事实）：整体 + 各模块流程图、风险表、对比、疑问解答
> - 📌 [`adr/0006-llm-gateway-single-entrypoint.md`](adr/0006-llm-gateway-single-entrypoint.md) — v3.37 设计决策

---

## 🎯 入口路径（新人在此起步）

1. **[`runtime-topology.md`](runtime-topology.md)** — ⭐ **现行运行拓扑（唯一权威）**：服务/端口/启动模式/交互模式/前端结构（**先读这个**）
2. **[`environment-dsh.md`](environment-dsh.md)** — ⭐ **当前环境的约束与坑**（终端/python stub/审批/已验证命令）—— **动手前必读**
3. **[`main/00-main-direction.md`](main/00-main-direction.md)** — 主方向 + v3.37 路线图
4. **[`../决策/README.md`](../决策/README.md)** — 决策书 SSOT（已拍板事实；与其他文档冲突时以它为准）
5. **[`service-startup.md`](service-startup.md)** — 启动细节与踩坑 ｜ **[`runtime-matrix.md`](runtime-matrix.md)** — 各服务 venv/解释器矩阵
6. **[`glossary.md`](glossary.md)** — BT 语音交互栈术语表
7. 按需要展开到子系统 / ADR / Spec —— 见下方分类索引

> ⚠️ **不要读这两份找"当前架构"**：`local/architecture-current.md`（2026-07-14 快照）与 `local/architecture-local.md`（2026-07-12，11 进程旧设计）**均已过时**，正文保留仅为历史。同理 `specs/2026-07-13-current-state.md` 的端口/模块部分已漂移（其自述也承认）。
>
> ⚠️ **历史环境文档**：`history-agent-environments.md` 记录 WorkBuddy/Codex 时代的操作技巧（**对当前环境不适用**），仅供追溯。

---

## 📚 分类索引

### 主方向（1 份）

| 文档 | 用途 |
| --- | --- |
| [`main/00-main-direction.md`](main/00-main-direction.md) | 项目主方向 + 路线图 |

### API 化主路径（2 份）

| 文档 | 用途 |
| --- | --- |
| [`api/api-optimization.md`](api/api-optimization.md) | API 化方案（主路径核心，~40 KB） |
| [`api/token-plan-comparison.md`](api/token-plan-comparison.md) | 8 家厂商 Token Plan 调研（MiniMax 推荐） |

### 本地化降级方案（3 份）

> **降级方案**：API 路径不可用时的本地兜底。**默认走 API 路径**，看这三个时确认自己真的在本地化部署。

| 文档 | 用途 |
| --- | --- |
| [`local/pm-local.md`](local/pm-local.md) | PM 视角的本地化方案 |
| [`local/tech-local.md`](local/tech-local.md) | 技术实现细节 |
| [`local/architecture-local.md`](local/architecture-local.md) | 11 进程拓扑 + 显存分配 |

### 调研 / 选型（2 份）

| 文档 | 用途 |
| --- | --- |
| [`research/memory-store-research.md`](research/memory-store-research.md) | 持久化层选型 |
| [`research/lightweight-replacement.md`](research/lightweight-replacement.md) | 硬件选型（GGUF / 摘要 / ASR / TTS） |

### 子系统设计（8 份）

> 按模块拆分的设计文档。改某个模块前先读对应文件。

| 文档 | 模块 |
| --- | --- |
| [`subsystems/jarvis-mode.md`](subsystems/jarvis-mode.md) | **Jarvis 模式**（状态机 + 唤醒 + 事件响应，~60 KB 最大文件） |
| [`subsystems/asr-streaming.md`](subsystems/asr-streaming.md) | KWS + 流式 ASR（sherpa-onnx） |
| [`subsystems/screen-capture.md`](subsystems/screen-capture.md) | getDisplayMedia 屏幕捕获 |
| [`subsystems/hermes-integration.md`](subsystems/hermes-integration.md) | Hermes-agent 严格隔离集成 |
| [`subsystems/voice-clone.md`](subsystems/voice-clone.md) | CosyVoice3 零样本克隆 |
| [`subsystems/voice-ui.md`](subsystems/voice-ui.md) | 浏览器语音交互界面 |
| [`subsystems/memory-architecture.md`](subsystems/memory-architecture.md) | 可插拔记忆架构（embedding API 化见 api/api-optimization.md §3.5） |
| [`subsystems/gaming-mode.md`](subsystems/gaming-mode.md) | 旧名"游戏模式"使用指南 |

### 专题 Spec（`specs/`）

> **活跃 spec**：每次变更/上线都要新建/更新对应专题。命名规范 `YYYY-MM-DD-{topic}.md`。

| Spec | 状态 | 用途 |
| --- | --- | --- |
| [`specs/2026-07-13-current-state.md`](specs/2026-07-13-current-state.md) | ✅ 唯一现状权威 | 端口 / 模块流程 / 风险表 |
| [`specs/2026-07-13-llm-path-consolidation.md`](specs/2026-07-13-llm-path-consolidation.md) | ✅ 已实施 | B 选项实施合同 |
| [`specs/hybrid-wake-confirm.md`](specs/hybrid-wake-confirm.md) | ✅ 已实施 | 混合唤醒确认窗口 |
| [`specs/kws-recall-optimization.md`](specs/kws-recall-optimization.md) | ✅ 已实施 | KWS 召回优化 |
| [`specs/memory-store-skeleton-spec.md`](specs/memory-store-skeleton-spec.md) | ✅ 已实施 | 持久化层骨架 |
| [`specs/webui-asr-input-state.md`](specs/webui-asr-input-state.md) | ✅ 已实施 | WebUI ASR 状态机 |
| [`specs/webui-kws-listening-chain.md`](specs/webui-kws-listening-chain.md) | ✅ 已实施 | WebUI KWS 监听链 |
| [`specs/2026-07-14-loose-coupling-services.md`](specs/2026-07-14-loose-coupling-services.md) | ✅ 已实施 | 4-API config + 单 webinfer 主路 + 3 独立 capture 模块（Phase 2A/B 实施合同） |
| [`specs/2026-07-14-project-audit.md`](specs/2026-07-14-project-audit.md) | ✅ 代码事实层现状 | 项目审查（HEAD=021f429）整体+模块流程图 + 风险表 + 疑问解答 |
| [`specs/background-agent-codex-bridge.md`](specs/background-agent-codex-bridge.md) | 🔧 草稿（代码完成待真机） | background-agent 切 Codex 桥接（N1，2026-08-13） |
| [`specs/call-mode-unknown-delegation.md`](specs/call-mode-unknown-delegation.md) | 🔧 草稿（代码完成待真机） | call 模式"不知道就委派"（N2，2026-08-13） |
| [`specs/draft-live-empathy-tuning.md`](specs/draft-live-empathy-tuning.md) | 🔧 草稿（代码完成待 benchmark） | 四态共情类误响应调优（2026-08-14） |

### 架构决策记录（`adr/`，19 份 + 4 份 mermaid）

> "为什么这么改"——决策历史。⚠️ **编号有冲突**：`0007` 与 `0008` 各被 3 个文件占用（设计 + 2 张图），待消歧。

| ADR | 主题 | 状态 |
| --- | --- | --- |
| [`adr/0001-voice-clone-sync.md`](adr/0001-voice-clone-sync.md) | Rapid Clone 同步路径 vs `/v1/t2a_async_v2` | Accepted |
| [`adr/0002-kws-config-env.md`](adr/0002-kws-config-env.md) | KWS 调参改 env 化 | Accepted |
| [`adr/0003-llm-reply-panel.md`](adr/0003-llm-reply-panel.md) | LLM 回复面板可见性 | ⚠️ 严重过时（引用符号全 0 命中） |
| [`adr/0004-service-lifecycle.md`](adr/0004-service-lifecycle.md) | 服务停止方案 | ⚠️ 部分失效（端口表含已废 8991/8992） |
| [`adr/0005-memory-store-start.md`](adr/0005-memory-store-start.md) | 持久化层启动策略 | ⚠️ 部分失效（锁定 8996，真值 8997） |
| [`adr/0006-llm-gateway-single-entrypoint.md`](adr/0006-llm-gateway-single-entrypoint.md) | LLM 网关单入口（v3.37/v3.38） | ✅ Accepted（最新最准） |
| [`adr/0007-split-live-adapter.md`](adr/0007-split-live-adapter.md) | live_adapter 拆分 | ⚠️ 已被后续重构超越 |
| [`adr/0007-milestone2-design.md`](adr/0007-milestone2-design.md) | Milestone 2 设计 | ⚠️ mixin 数已变（5→7） |
| [`adr/0008-p0-adapter-fixes-design.md`](adr/0008-p0-adapter-fixes-design.md) | P0 adapter 修复 | ✅ 已落地（状态待改 Accepted） |
| [`adr/0011-phased-lint-gate.md`](adr/0011-phased-lint-gate.md) | 分阶段 lint 门禁 | ✅（Batch 2/3 已落地） |
| [`adr/0013-webinfer-memory-client-resilience.md`](adr/0013-webinfer-memory-client-resilience.md) | memory 客户端韧性 | ✅ Accepted |
| [`adr/0014-log-event-schema.md`](adr/0014-log-event-schema.md) | 日志事件 schema | ⚠️ 引用的 `scripts/log_query.py` 不存在 |
| [`adr/0015-memory-store-health-observability.md`](adr/0015-memory-store-health-observability.md) | memory-store 健康可观测性 | ✅ Accepted |
| [`adr/0016-live-adapter-drift-gate-safe-batch.md`](adr/0016-live-adapter-drift-gate-safe-batch.md) | drift-gate 安全批 | ✅ Accepted |
| [`adr/0017-drift-gate-launcher-wiring.md`](adr/0017-drift-gate-launcher-wiring.md) | drift-gate launcher 接线 | ✅ 已落地（状态待改 Accepted） |
| [`adr/0018-smart-turn-end-of-turn.md`](adr/0018-smart-turn-end-of-turn.md) | Smart Turn 端点检测 | ✅ Accepted |
| [`adr/0019-webui-component-consistency.md`](adr/0019-webui-component-consistency.md) | WebUI 元件一致性（D1–D5） | ✅ 有效 |
| [`adr/0020-webui-local-cloud-selector.md`](adr/0020-webui-local-cloud-selector.md) | 本云选择器 idiom | ✅ 有效 |
| [`adr/ADR-0012-v6-proposal.md`](adr/ADR-0012-v6-proposal.md) | Local Wiki 方案 C（bge-m3+USearch+Obsidian） | ⚠️ 全文用 8996（真值 8997） |

### 工具书

| 文档 | 用途 |
| --- | --- |
| [`glossary.md`](glossary.md) | BT 语音交互栈术语表 |

### 规范（`standards/`，动手前必读）

> **纪律**：改 WebUI / 改代码风格前先查这里。规范是**强制约束**，不是参考建议。

| 文档 | 状态 | 用途 |
| --- | --- | --- |
| [`standards/webui-design-standards.md`](standards/webui-design-standards.md) | ✅ **2026-09-19 大幅扩充**（896 行） | **WebUI 设计规范**：卡片契约 / 按钮尺寸（§4）/ i18n 纪律（§5）/ 主题 token（§3）/ 批量改界面的结构守恒自检（§6）/ **控件族契约（§7.5–7.7：勾选类、分段滑块、参数滑块、滚动条、厂商参数差异、输出去处）** / 删除元素纪律（§8）/ **工程纪律 §9.1–§9.13（13 条实测教训）** / 附录：契约速查 + 18 个自测脚本清单 |
| [`standards/coding-standards.md`](standards/coding-standards.md) | ✅ | 编码规范 |
| [`standards/code-review-checklist.md`](standards/code-review-checklist.md) | ✅ | 代码审查清单 |
| [`standards/lint-baseline.md`](standards/lint-baseline.md) | ✅ | Lint 基线（ADR-0011 分阶段门禁） |
| [`standards/workspace-isolation.md`](standards/workspace-isolation.md) | ✅ | 工作区隔离规范 |

### 前端专题（`frontend/`）

| 文档 | 状态 | 用途 |
| --- | --- | --- |
| [`frontend/idesign-wiring.md`](frontend/idesign-wiring.md) | ✅ **2026-09-19 补 §3.1** | 把真实 WebUI 接入 iPolloWork iDesign Studio 的接线步骤；**§3.1 同步纪律**（改完 `static/` 必须跑 `idesign-wire.mjs`） |
| [`frontend/fullscreen-audit-2026-09-19.md`](frontend/fullscreen-audit-2026-09-19.md) | ✅ 事件型 | 全屏实现审计：三个根因（层叠囚禁 / DOM 搬家 / 72px 截断）+ 修复验证矩阵 + **§九 输出去处语义的准确定性** |
| [`frontend/frontend-residue-audit-2026-09-19.md`](frontend/frontend-residue-audit-2026-09-19.md) | ✅ 事件型 | 前端残留审计：13 处「DOM 已删、JS 仍引用」的死引用 + 2 处静默失效 |

### 维护工具（跨目录引用）

- [`../services/scripts/README.md`](../services/scripts/README.md) — 改代码后必跑 [`sync-docs.py`](../services/scripts/sync-docs.py)
- [`../stop-joyai.ps1`](../stop-joyai.ps1) — 一键停全部服务
- [`../start-joyai.ps1`](../start-joyai.ps1) — 薄包装 `run-windows.ps1`
- [`../DELIVERY.md`](../DELIVERY.md) — 变更记录 + 复盘决策

### 已弃用（不进常规阅读路径）

| 目录 | 说明 |
| --- | --- |
| [`deprecated/`](deprecated/) | 历史文档快照（详见 `deprecated/README.md` 的处置规则；不要据此实施） |

---

## 🔗 项目根目录结构

```
JoyAI-VL-Interaction-main/
├── ARCHITECTURE.md      # 架构概览（指针式镜像）
├── DELIVERY.md          # 交付与变更记录
├── 决策/                 # ⭐ SSOT：已拍板决策（冲突时以本目录为准）
├── doc/                 # 本目录：设计/规格/调研/子系统
├── docs/                # 操作手册（runbook，见下方「操作手册」一节）
├── services/            # webinfer / asr / tts / voice-clone / webui / background-agent / common
├── scripts/             # verify.sh / drift_gate.py / 质量门
├── config/              # drift-contract.json
├── install/             # 安装脚本
├── prompts/             # 角色 prompt 模板
├── reports/             # 一次性工作留痕（handoff / 审计 / 验收）
└── datasets/            # 训练数据转换工具（运行时不需要）
```

---

## 🛠 操作手册（`docs/`，与 `doc/` 分工）

> ⚠️ **两个目录只差一个字母，注意区分**：
> - `doc/`（本目录）= **设计/规格/决策**（"为什么这样设计"）
> - `docs/` = **操作手册**（"怎么做"，runbook 性质，跨对话端共用）

| 手册 | 用途 |
| --- | --- |
| [`../docs/github-runbook.md`](../docs/github-runbook.md) | GitHub/git 操作钉死手册（踩过的坑 + 正确动作） |
| [`../docs/local-wiki-methodology.md`](../docs/local-wiki-methodology.md) | Local Wiki 语料扩充 / 换游戏方法论 |
| [`../docs/kws-training-manual-generic.md`](../docs/kws-training-manual-generic.md) | KWS 训练手册（通用） |
| [`../docs/kws-training-manual-local.md`](../docs/kws-training-manual-local.md) | KWS 训练手册（本机环境） |
| [`../docs/kws-training-manual-local-reality-check.md`](../docs/kws-training-manual-local-reality-check.md) | KWS 训练手册（本机现实核对） |
| [`../docs/asrok-user-guide.md`](../docs/asrok-user-guide.md) | ASR/OK 使用指南 |

---

## 📋 文档维护规则

1. **改代码 → 改 spec**：`specs/2026-07-13-current-state.md` 是基线，端口/模块变更必须同步。
2. **新增专题 → 新建 spec**：写 `specs/YYYY-MM-DD-{topic}.md`，状态用 `✅ 已实施 / 🟡 进行中 / ⚪ 观察项 / ⚫ 弃用`。
3. **设计决策 → 新建 ADR**：顺序编号 `adr/NNNN-{title}.md`，状态 `Accepted / Superseded / Deprecated`。
4. **过 6 个月无引用 → 候选弃用**：先 `deprecated/`，6 个月再未引用 → 删除。
5. **`doc/README.md`（本文件）必须反映最新分类布局**——加新文件时同步更新。
6. **移动文件必须同步引用**：2026-09-14 核查发现，此前一次 `doc/` 目录重排（散文件收进 `subsystems/`/`local/`/`main/`）未同步引用，导致 16 处死指针。移动文件时用 `grep -rn` 全仓扫旧路径。
7. **禁止行号引用**：`file.py:123` 这类引用漂移极快（已发现 15 条因此失效，甚至出现 `index.html:4093` 指向一个只有 2998 行的文件）。改用**关键词 grep** 或**函数名/常量名**。
8. **⚠️ 批量替换必须排除「事实记录」目录**（2026-09-14 教训）：
   一次性路径迁移中，批量替换曾**误改 `logs/drift-gate-history/` 下 13 份运行日志** —— 那是**当时的输出记录**，把其中的旧值"修正"成新值即造成**历史失真**，且该目录未被 git 跟踪，**无法还原**。
   **绝不可改写**（见 `scripts/doc_health.py` 的 `NEVER_REWRITE`）：
   `logs/`、`services/logs/`、`services/.logs/`、`reports/webui-preview-*`、`doc/research/data/`、`.cache/`、`.workbuddy*/`、`doc/deprecated/`。
   **判据**：该文件描述的是**"当时发生了什么"**（记录）还是**"现在应该是什么"**（文档）？前者一律不改。
9. **⚠️ 先问"这是什么"，再下结论**（2026-09-14 教训，**范畴错误**）：
   实测发现用户级 `npm_config_cache` 指向本工作区**之外**（`D:\Workspace\hermes-agent\.cache\npm`），
   当时（AI）直接判定为「违反 D-011 外溢」并标 🔴 —— **判定错误**。
   复核后发现：`hermes-agent` 是**另一个独立项目**（`NousResearch/hermes-agent`，Node 项目，有自己的 `package.json`/`.npmrc`），
   那是**它自己的项目级缓存隔离**，完全正确。
   **D-011 约束的是「本项目的产物收口在本项目工作树内」，不是「这台机器上所有缓存」。**
   **判据**：看到「本项目的变量指向项目外」时，**先问那个目录属于谁**再判定。
   **反证信号**：同组变量中**只有一个不同、其余全对** → 几乎不可能是"环境被污染"，更可能是该值被**另一个项目**按需设置。

---

## 🔄 文档生命周期契约（2026-09-14 建立，防堆积）

> **背景**：2026-08 的 10 天内新增 96 份文档，而 SSOT 只吸收 3 条决策；两个月新增 292 份、删除 1 份。
> 根因是「写没有成本，退没有机制」。下面按**文档寿命类型**分类，每类给明确的维护期望。

### 类型 A：稳态型 —— **必须持续更新**

| 目录 | 寿命 | 维护要求 |
|---|---|---|
| `doc/subsystems/` | 与子系统同寿 | 子系统行为变了就必须改。**参照系：本目录 8 份文件「写完即弃率」为 0%**，是全场最健康的 |
| `doc/main/`、`ARCHITECTURE.md` | 与项目同寿 | 端口/拓扑变更时同步（`ARCHITECTURE.md` 是「指针式镜像」，只改指针不改细节） |
| `决策/`（SSOT） | 永久 | 走 §0 治理协议；新决策必须落这里 |

**判据**：描述的是**"现在怎么工作"**。只要被描述的东西活着，文档就必须跟着改。
**健康指标**：`写完即弃率` 应接近 0%。

### 类型 B：事件型 —— **允许写完即封存**

| 目录 | 寿命 | 维护要求 |
|---|---|---|
| `reports/` | 一次性 | handoff / integration / audit / review。**干完即封存，不要求更新** |
| `doc/acceptance/` | 单次验收 | 验收完成后归档 |
| `doc/architecture/` 的 audit | 快照 | 审计当时的状态，不追改 |

**判据**：描述的是**"我这次干了什么"**。事件结束，文档使命即完成。
**健康指标**：`写完即弃率` **高是正常的**（当前 73%，可接受）；但必须**定期退役**（见下）。

### 类型 C：证据链型 —— **不可删，但必须可验证**

| 目录 | 说明 |
|---|---|
| `doc/research/` | 云端调研的完整证据链（终稿 + 支撑稿）。被 spec 引为「上游」 |
| `doc/adr/` | 决策记录 + 其 Context |
| `决策/` 里的「来源」列 | 指向上述证据 |

**要求**：被引用即不可删；但引用本身必须是**可验证的指针**（不用失效 SHA、不用漂移行号）。

### 类型 D：坟场 —— **只进不出，但要有规则**

`doc/deprecated/`。规则见其 `README.md`：零引用才进，半年无引用可批量删，**每次迁入必须登记**。

---

## 🩺 健康检查（定期跑）

```bash
/d/AI/envs/joyai-main/python.exe scripts/doc_health.py
```

六项检查：`LINK`（指针）/ `CODE`（源码注释→文档）/ `SSOT`（决策书自检）/ `INDEX`（索引覆盖）/ `ORPHAN`（零引用）/ `STALE`（陈旧度）。
**block 级非零即须处理**；退出码 1 表示存在死链或 SSOT 指针失效。

### 退役判据（满足即处置，不需要等 6 个月）

| 条件 | 动作 |
|---|---|
| 零引用 **且** 工作已落地 **且** 无唯一证据 | **删除** |
| 零引用 **但** 含历史/因果价值 | **归档**到 `doc/deprecated/`（须登记） |
| 被 SSOT/doc/代码任一引用 | **保留**（引用是硬存活理由） |
| 含**唯一证据链**（原始数据已被 gitignore / 实测态不可复得） | **保留** |

> ⚠️ **不要只看引用数**：2026-09-14 发现 SSOT 引用了 6 个**磁盘上不存在**的报告，同时有真正准确的文档零引用。引用数须**实测校验**后再采信。

---

## ⚠️ 已知的文档债（2026-09-14 审计，待处理）

| # | 问题 | 影响 |
|---|---|---|
| 1 | 约 40+ 条决策条目的「来源」引用已因 `git filter-repo` 失效的 commit SHA | 证据链不可复核（见 `决策/README.md` §1.1） |
| 2 | `doc/local/` 的两份 `architecture-*.md` **都自称"当前架构"但都已过时**（其一含全仓唯一的幽灵端口 `8088`） | 现行拓扑无权威文档；`doc/runtime-matrix.md` + `service-startup.md` 实测最准却近乎零引用 |
| 3 | `doc/subsystems/jarvis-mode.md` 描述的是**已非主线**的 jarvis 模式（EXIT_WORDS 记 8 个实为 5 个、状态机记 6 态实为 7 态） | 但 §2/§4/§12 仍是不可替代的经验资产 → 建议拆出而非删 |
| 4 | `doc/subsystems/voice-ui.md` §1.1 的 HUD 徽章 **5/7 元素已不存在**（`llmBadge`/`ttsBadge`/`kwsBadge` 全仓 0 命中） | 仅 §9 Design Tokens 仍被 ADR-0019 依赖 |
| 5 | 5 个 `draft-*` spec 的功能**已全部上线但状态仍写"草稿"** | 状态头只朝一个方向衰减（详见下） |
| 6 | `doc/specs/README.md` 称"7 份 draft 已删除"，实际仍有 8 个 `draft-*.md` | draft→正式 的转正流程自 2026-08-13 停摆 |
| 7 | `scripts/log_query.py` 被 ADR-0014 与 `决策/服务-日志.md` 引为校验工具，**从未落盘** <!-- known-absent --> | 已锁定决策的验收检查无法执行 |

---

## 🧪 测试现状（2026-09-14 首次全量跑；**2026-09-20 收口**）

> **背景**：2026-09-14 本轮文档收口共 6 个提交、改了约 60 个文件，但**全程未跑测试**。用户追问"还有没注意的点吗"后才跑 —— 结果发现 **9 个失败**。经 git worktree 二分确认：**这 9 个在我改动之前（`3a282ef`）就存在**，是历史遗留，非本轮引入。
>
> **2026-09-20 更新**：用户批准开修，**那 9 个已全部修完**（详见下节）。本节数字为收口后实测。
>
> ✅ **CI 状态（2026-09-20 收尾，run [35516795934](https://github.com/kuhaku9527/vl-interaction-dev/actions/runs/35516795934) @ `74f7e34`）：**
> **12/12 job 全绿**（ruff / eslint / package-smoke / frontend-test / drift-gate /
> drift-gate-runtime / pytest ×5）。此前 `main` 自 2026-08-11 起长期有 4 个 job 红。

### 各套件实测结果

| 套件 | 结果 | 备注 |
|---|---|---|
| **webinfer** | ✅ **436 passed / 0 failed / 0 skipped**（3.8s） | 三次重跑（含反转文件顺序）结果一致，无 flaky。**无 skip/xfail 掩码**（grep 零匹配） |
| **tts** | ✅ 28 passed | — |
| **voice-clone** | ✅ 11 passed | — |
| **asr** | ✅ 2 passed | — |
| **webui** | ✅ **CI 全绿**（2026-09-20 修复；详见下） | 见 §webui 失败收口 |

### ✅ webui 的 9 个失败 —— 已于 2026-09-20 全部修完

> 本节原记「**已决定暂不修**」（用户 2026-09-14 拍板）。
> **2026-09-20 用户批准开修并已全部落地**，故本节状态由「暂不修」改为「已修复」。
> 保留原本的根因分析以存证据链，**接手者不要再把它当"已知未修"的技术债**。

**根因 A（8 个失败）**：`28c90ec`（S2 重构）把跨模块全局移到 `window.JoyState`（新文件 `joy_state.js`），
但测试的 `_SPLIT_JS` 文件列表没同步，且断言停留在旧的字符串形态。

**根因 B（1 个失败）**：`483fd88`（v6-lite.23 输入栏回滚）删掉了 `<div class="mode-group" role="radiogroup">` 容器，但
- 两个按钮**仍带 `role="radio"`**（孤立 —— WAI-ARIA 要求 `role=radio` 必须在 `radiogroup` 内）
- `styles.css` 里**还留着 7 处 `.mode-group` 规则**（悬空）

| # | 原失败用例 | 性质 | 处置 |
|---|---|---|---|
| 1-3 | `test_reply_epoch_guard.py`（epoch guard / adopt epoch / declaration） | 断言形态未跟上 S2 | ✅ 已重锚到 `window.JoyState` |
| 4-5 | `test_qa_phase_c_edges.py`（no local self-increment / guard precedes render） | 同上 | ✅ 同上 |
| 6 | `test_live_frontend_contract.py` | 同上 | ✅ 同上 |
| 7 | `test_live_mode_qa_boundary.py` | 同上 | ✅ 同上 |
| 8 | `test_live_visual_frontend_contract.py` | 同上 +（末条）radiogroup | ✅ 同上 + radiogroup |
| 9 | `test_webui_mode_radio_contract.py` | **真回归**：radiogroup 容器缺失 | ✅ 已补回 |

#### 修法（2026-09-20）

**A. 语料陈旧 → 改为派生（治本，杜绝第三次失同步）**
新增 `services/webui/tests/_frontend_corpus.py` 作为唯一真值源，12 个测试文件改为
`from tests._frontend_corpus import index_html_plus_split_js`，**零硬编码文件名**。
派生式 = `(index.html 加载的 script，按加载序) − PRE_EXISTING_MODULES(冻结 9 名)`。
- 保住「仅拆分产物」语义：天真 `glob('*.js')` 会多纳入 9 个模块，**实测引入 22 处假红**
  （如某测试断言 `"setInterval" not in select_live`，而 radio_silence/screen_capture 含 `setInterval`）。
- **fail-closed**：index.html 引用不存在的脚本、或派生结果为空 → **import 即抛**
  （`refusing to silently shrink the corpus`），不静默缩小。
- 派生结果实测 = **16 个 = 旧 14 + `joy_state.js` + `radio_silence.js`**，无多无少。

**B. radiogroup（真 ARIA 违规）→ 两个区域各一个容器**
⚠️ **不是「把旧容器原样加回来」** —— 旧形态是**一个**容器包住两个按钮，但那与**用户两次拍板**冲突：
- **2026-09-19**：`btListenBtn` 真删出输入栏、落到设置页（旧隐藏作用域在全屏时会失效，控件集体复活）；
  `liveModeBtn` 按用户要求回聊天栏「方便直接开启」。
- **2026-08-12**：两者必须是**互斥 radio**，不是两个独立开关。

⇒ 两按钮**分处两个 UI 区域**，一个共享容器在结构上不可能。
⇒ 正解：**每个区域各一个 `.mode-group[role="radiogroup"]`**，各带 `aria-label` 说明与对方互斥。
既消除 ARIA 违规，又**不回退任何一次拍板**。`#liveModeSeg` 复用既有 id ⇒ id 集合 zero-diff。
两处（`index.html` / `styles.css` 的 `.mode-group` 注释）都写明了这些出处与
**「勿为『合并成一个容器』回退这两次拍板」**的告诫。

**验证**：目标测试全绿；全量 webui 套件仅剩 `test_qa_server_split_runtime.py` 的 10 个
**本地路径 bug**（CI 里不出现，见下节 → 已立 **#151**）；
布局未退化 —— `check-advanced-relocation` **12-0**（含「实时」双行 **54x52**）、
`check-fullscreen-parity` **17-0**、`check-button-wrap` 全绿、`check-topbar-fixes` **10-0**、
`check-idesign-mirror` **6-0**；`webui-invariants` id 新增 0/删除 0、div 配平。

### `test_qa_server_split_runtime.py` 的本地路径 bug（**代码未修；已立工单 #151**）

该文件 `WEBUI_ROOT = Path(__file__).resolve().parents[2]` 解析到 `services/`（应为 `parents[1]`
= `services/webui`），于是 `SRC = services/src` **不存在** ⇒ 裸跑 `pytest services/webui/tests`
时 10 个用例因 `ModuleNotFoundError` 失败。
**CI 不报**：CI 在该目录内 `pip install -e ".[dev]"`，包已可导入。
⇒ 属**测试自身缺陷**（在裸跑路径下失效），不在 CI 失败名单内。

> **2026-09-21 已立工单 → [#151](https://github.com/kuhaku9527/vl-interaction-dev/issues/151)**
> （wayfinder:task，挂地图 #142）。本轮取证**修正了此处原先的两个判断**：
> 1. **不是「CI 更严格」，而是「CI 更宽松」**。配对对照（`afa3594`）：未修代码
>    `877 passed / 10 failed`；修 `parents[1]` 后 **`887 passed / 0 failed`**；
>    而在**未修**代码上仅从外部补 `PYTHONPATH` 即 `10 passed`
>    ⇒ CI 的 `pip install -e .` 提供了该测试本应自建的导入通道，
>    **该文件在 CI 里从未验证过自己的前置条件**。
> 2. **本地残留的 10 个失败 == 本缺陷的 10 个**，一一对应（非「属预期」的独立现象）。
> 修法已验证为 **1 行 + 一句 `SRC.is_dir()` 守卫**（该文件当前**无任何存在性断言**，
> fails-open：路径写错时报 10 条同源 `ModuleNotFoundError`，读起来像「契约坏了」）。
> 另：31 个同用 `parents[2]` 当 `REPO` 的文件**不失败**（conftest 兜底），
> ⇒ 真因是「把 `SRC` 当唯一导入通道且无守卫」，**不是「下标写错」**（详见工单）。

### 另发现两个 webinfer 的"假通过"测试（子代理诊断）

| 位置 | 问题 | 状态（2026-09-21 复核） |
|---|---|---|
| `test_summarizer_routing.py:219` `test_flush_chunk_fail_open_when_summary_raises` | 桩被写成 `async def _boom`，但真实 `_build_mid_term_summary_entry` 是**同步**函数（经 `asyncio.to_thread` 调用）→ 桩返回**未被 await 的 coroutine**，**永不 raise**。测试通过，但它宣称锁住的失败模式**从未被执行**（把守卫从 `except Exception` 收窄仍会通过）。**生产代码本身正确**，是测试缺陷。修法：把桩改成同步 `def` | ✅ **已修**（commit `a90fc97`）。复核方式：桩现为同步 `def`；**负控实测** —— 把 `summarizer_routing.py` 的 `except Exception` 收窄为 `except ValueError`，该测试**立即转红**（`1 failed`），证明失败模式**真的被执行**了 |
| `pyproject.toml:93` | 声明 `timeout = 60`，但 **pytest-timeout 未安装** → 该超时**实际失效**（`PytestConfigWarning: Unknown config option: timeout`） | ⚠️ **仍成立**。`pyproject.toml:51` 已把 `pytest-timeout>=2.1.0` 列进 dev 依赖，但**本机 venv 未装**，故警告照旧。CI 的 `pip install -e ".[dev]"` 会装上 ⇒ **本地不生效、CI 生效**，属「环境差异」类（见 `doc/standards/webui-design-standards.md` §9.14） |

> **教训（写入本文件防重犯）**：
> **改了代码就必须跑测试。** 本轮 6 个提交、约 60 个文件全程未跑测试，若这 9 个失败中有任何一个是我引入的，就会带着它提交。
> 好在二分证明了不是；但**这是运气，不是纪律**。
> 后续任何改动，收尾前至少跑受影响服务的 `pytest -q`。

### 📌 一条经验规律（值得记住）

> **文档的状态头只会朝一个方向衰减：从"已实现"退不回"待实现"，但"待实现"会一直停留在那里，即使功能早已上线。**

本次审计的 46 份 spec 中，**所有过时的状态标注都错在"尚未实现"一侧，无一例外**。
`log-event-schema.md` 自称"待实现"，而 SSOT `决策/服务-日志.md` 反向引用它作为「来源 spec」——**闭环建立在一个假前提上**。

**推论**：检查文档陈旧度时，**优先怀疑"草稿/待实现"状态的文件**，它们最可能是"已完成但没人更新状态"。
