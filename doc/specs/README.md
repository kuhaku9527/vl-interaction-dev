# doc/specs/ — 决策前的 spec 落点（待审查组消费）

本目录是各端对话在提出跨域 / 架构 / 不可逆改动时写 **spec** 的既有位置。ADR 在同级目录 `doc/adr/`。

> **自检**：`/d/AI/envs/joyai-main/python.exe scripts/doc_health.py --check index`
> **最近核对**：2026-09-14（全量重扫，本索引此前只覆盖 7/45 份）

---

## §1 正式 Spec（已定稿，走 草稿→设计评审→实现→QA→真机 流程）

| Spec | 主题 | 实现状态 |
|---|---|---|
| `runtime-topology`（→`../runtime-topology.md`） | **现行运行拓扑**（服务/端口/启动模式/交互模式） | ✅ 现行权威 |
| `token-link-max-tokens.md` | token 链路 max_tokens 两档规范 + finish_reason 可观测性 | ✅ 已实现 + QA PASS |
| `unified-turn-controller.md` | 统一 Turn Controller（状态机 + 三层级联 + 7 场景矩阵） | ✅ 已实现（沙箱原型）+ QA PASS |
| `tts-streaming-optimization.md` | TTS 链路流式化（LLM 流式 + SentenceBuffer + 逐句 TTS） | ✅ 已实现 + QA PASS |
| `addressee-detection.md` | 说话对象判定（Phase1 CAM++ 声学门控 + Phase2 四态 not-for-me） | ✅ 已实现 + QA PASS |
| `live-interaction-layer.md` | live 可交互层（C.A 常驻监听 + 模式互斥 + 日志分离 + 分句修复） | ✅ 已实现 + QA PASS |
| `live-visual-cb.md` | C.B 完整直播形态（VLM 视觉 + 主动搭话） | ✅ Implemented（待真机验收） |
| `turn-controller-integration.md` | Turn Controller 三阶段接入蓝图（影子 → 委托 → live） | ✅ Phase A/B/C（委托默认关，待真机回归） |
| **`radio-silence.md`** | 无线电静默（live 子状态 + 唤醒仪式 + 设置面板） | ✅ 已实现（2026-09-14 转正） |
| **`asr-provider-unified.md`** | ASR 统一抽象（`ASRProvider`，本地/云端可插拔） | ✅ 已实现（2026-09-14 转正） |
| **`tts-provider-unified.md`** | TTS 统一抽象（`TTSSynthesizer`） | ✅ 已实现（2026-09-14 转正） |
| **`background-agent-codex-bridge.md`** | background-agent 插件化（`AgentProvider`，默认 codex） | ✅ 已实现（2026-09-14 转正） |
| **`provider-convergence.md`** | Provider 模式收敛（N7：注册表 + 通用工厂） | ✅ 已实现（2026-09-14 转正） |
| **`call-mode-unknown-delegation.md`** | call 模式"不知道就委派"（N2） | ✅ 已实现（2026-09-14 转正） |
| `2026-07-13-llm-path-consolidation.md` | LLM 网关单入口（B 选项实施合同） | ✅ 已实施 |
| `2026-07-14-loose-coupling-services.md` | 4-API config + 单 webinfer 主路 + 3 独立 capture | ⚠️ 已实施（槽位数已演进：4→7，见 `unified-api-config-ui.md`） |
| `2026-08-11-asr-cloud-config-contract.md` | ASR 云端配置契约 | ✅ 已落地 |
| `2026-08-11-kws-asr-promotion-recall.md` | KWS→ASR 提召回（promotion） | ⚠️ 状态过期（env 已启用，标 Draft） |
| `2026-08-11-startup-harness-do-dont.md` | 启动 harness 该做/不该做 | ✅ 有效 |
| `memory-client-resilience.md` | memory-store 客户端韧性（熔断器） | ✅ 已实现（2026-07-29） |
| `memory-store-skeleton-spec.md` | memory-store 持久化骨架 | ⚠️ 已实现（端口描述曾错写 8996） |
| `hybrid-wake-confirm.md` | 混合唤醒确认窗口 | ⚠️ 已实现（声称的 env 名不存在） |
| `kws-recall-optimization.md` | KWS 召回优化 | ⚠️ 已被 KWS v5 取代 |
| `smart-turn-end-of-turn-spec.md` | Smart Turn 端点检测 | ✅ 已落地（默认关，需 `SMART_TURN_ENABLED`） |
| `log-event-schema.md` | 日志事件 JSONL schema（ADR-0014） | ✅ 已实现（原标"待实现"已更正） |
| `t05-memory-store-health-observability.md` | memory-store 健康端点可观测性 | ✅ Implemented（PR #85） |
| `f34-live-adapter-drift-gate-safe-batch.md` | live adapter drift-gate 安全批 | ✅ Implemented（PR #87） |
| `f40-drift-gate-launcher-wiring.md` | drift-gate launcher 接线 | ✅ 已落地 |
| `drift-gate-harness-spec.md` | drift-gate harness | ✅ APPROVED（2026-07-29） |
| `interaction-mode-isolation.md` | 交互模式隔离 | ⚠️ 已落地（**全篇只有三态，缺第四态 not-for-me**） |
| `live-transcript-ui-spec.md` | 直播转写 UI | ✅ 已实现 |
| `unified-api-config-ui.md` | 统一 API 配置 UI（6/7 槽位） | ⚠️ 槽位已 7 个 |
| `webui-asr-input-state.md` | WebUI ASR 输入状态机 | ✅ 一致 |
| `webui-kws-listening-chain.md` | WebUI KWS 监听链 | ✅ 一致 |
| `webui-device-label-i18n.md` | 设备名/UI 字符串本地化 | ⚠️ 行引用越界（`index.html:4093` 已超文件末尾） |
| `webui-redesign-spec.md` | WebUI 重设计 spec（草案） | ✅ 进行中（v6-lite） |
| `webui-redesign-mapping.md` | WebUI 重设计落地映射（蓝图） | ✅ 最新（v6-lite.24） |
| `webui-component-consistency-spec.md` | WebUI 元件一致性（D1–D5） | ⚠️ 令牌名与实现不符（`--ok`/`--warn`） |
| `voice-prompt-template-spec.md` | 角色 prompt 模板 | ⚠️ `compose_voice_prompt` 已定义但**生产无调用点** |
| `voice-ui-design-tokens-spec.md` | WebUI Design Tokens | ⚠️ 目标已达成（`voice-ui.md` §9 已有），spec 本身无增量价值 |
| `readme-mode-matrix-spec.md` | README 四模式矩阵 | ⚠️ README 已从工作区删除 |
| `2026-07-13-current-state.md` | 项目现状快照 | ❌ **已失效**（端口 8299 错、进程数描述过期；勿作权威） |
| `2026-07-14-project-audit.md` | 项目审查（HEAD=021f429） | ❌ **已失效**（基线 commit 不在对象库，行引用全错） |

## §2 草稿 Spec（未定稿）

| Spec | 主题 | 为何仍为草稿 |
|---|---|---|
| `draft-live-empathy-tuning.md` | 四态共情类误响应调优 | 功能部分已落盘，但「persona 文件级弱化」待**真机 benchmark** 决定 |
| `draft-mode-isolation-boundaries.md` | 三模式隔离决策边界澄清 | 澄清性文档（非实施合同）；结论已并入 `决策/交互模式与决策token规范.md`，无需转正 |

> 其余原 `draft-*` 已于 2026-09-14 转正（见 §1 加粗行）。**转正记录可 git 追溯**（`git log --follow`）。

## §3 模板与索引

- `_SPEC-TEMPLATE.md` — spec 起手模板
- `README.md` — 本文件

---

## 命名约定（沿用既有双轨，不强制统一）

- 日期前缀式：`<YYYY-MM-DD>-<topic>.md`（如 `2026-07-14-loose-coupling-services.md`）
- 功能命名式：`<topic>-spec.md` 或 `<topic>.md`（如 `memory-store-skeleton-spec.md`）
- 新写时二选一即可；spec 内可交叉引用配套 ADR：`doc/adr/XXXX-*.md`。

## 内容约定

- 结构参考：`## Problem Statement` / `## Solution` / `## User Stories` / `## Implementation Decisions` / `## Testing Decisions` / `## Out of Scope`。
- **不写最终决策**（那归 `决策/`）；spec 是决策的前提案（what / options / 推荐）。
- **状态头必须写**（`生命周期: 正式/草稿`）。⚠️ **注意一条已验证的规律：状态头只朝一个方向衰减** —— 功能上线后没人回头改状态是常态。**凡见"草稿/待实现"，先核实是否其实已上线。**

## 生产路由（防多端污染）

- **只写不读其他端**：端点写完自己的 spec 后，不读其他端对话；由审查组对话统一召回、交叉验证、写 `决策/`。
- 处理后：spec 保留为过程档案（不删），供追溯。
- 触发：用户手动叫审查组对话"去收 `doc/specs/` + `doc/adr/` 写决策"时才处理，**不自动轮询**。

## 维护规则

1. **新增 spec 必须登记进本索引**（否则对 AI 新会话等同不存在）。
2. **转正时**：`git mv draft-x.md x.md`，改状态头，**并全仓修引用**（代码注释、其他 spec、索引）。
3. 移动/改名后跑 `scripts/doc_health.py --check link` 验证无死链。
