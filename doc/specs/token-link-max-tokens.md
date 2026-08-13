# Spec：token 链路 max_tokens 规范

> 生命周期: **正式**（2026-08-13 定稿，走 草稿→设计评审→实现→QA→真机 流程完成）
> 上游依据: `doc/research/block2-token-truncation-diagnosis-2026-08-11.md`（根因 A/B 已修复）
> 实现: 29dd71e（webinfer `main_max_tokens` 128→1024 + `normalize_model_output` 多行保留）
> 验证: QA 190 测试（webinfer 回归）— QA PASS
> 状态: 根因已修复（128→1024 + normalize 多行保留）；本 spec 规范"长期如何不复发"

---

## §1 因果链（Why）

- **Why**：早期"决策 token 短回复"设计（live 三判断/静默退出依赖短输出节奏）将 `main_max_tokens` 定为 128，未随疑问对话/长回复需求演进 → 长输出截断（根因 A）；`normalize_model_output` 只保留第一行 → 多行回复丢失（根因 B）。两处均为"设计假设未随需求更新"的魔改积累。
- **现状**：128→1024 + 多行保留已修复并过沙箱验证（178+12 用例）。本草案回答"长期规范怎么定"。

## §2 范围与负面约束

- **做**：定义 max_tokens 与决策 token 语义的解耦规则；定义两档输出（决策短回复 / 内容长回复）的选档原则；定义 `finish_reason=length` 可观测性。
- **不做**：本次不引入按 payload 自动选档的实现（复杂度高、需前端配合，留作 P1）；不改 `honor_inbound_generation_params`（保持 False，行为面不扩大）；不动决策 token 解析三函数。

## §3 方案

### 3.1 原则：max_tokens 是"内容预算"，不是"决策节奏工具"

- 决策节奏（沉默/搭话/回复/静默退出）由 **system prompt 的指令语义**控制（prompt 已含 "concise reply"），**不靠 max_tokens 硬截断**。
- max_tokens 只应限制"单次回复的最大内容量"，默认 1024（已落地）；需要更短/更长回复时按场景显式传参（开 `HONOR_INBOUND_GENERATION_PARAMS` 或按 payload 选档）。

### 3.2 两档输出（P1，待实现）

| 档位 | 适用 | max_tokens 建议 | 说明 |
|---|---|---|---|
| 决策短回复 | live 沉默/搭话判断、jarvis 静默退出、主动搭话触发 | 128–256 | 保延迟与静默节奏 |
| 内容长回复 | 用户疑问问答、总结、多段输出 | 1024–2048 | 完整性优先 |

实现路径（择一）：
- a) 按 payload 字段（如 `max_tokens` 显式传入）→ 开 `HONOR_INBOUND_GENERATION_PARAMS=true` + 前端按场景传参；
- b) 按 interaction_mode / query 类型在 webinfer 内选档（需设计场景判定，复杂度中）。

### 3.3 可观测性（P1）

- 记录 `finish_reason`：`length`（截断）时 `logger.warning`，避免静默截断无从排查。
- 现状：`_chat_completion_response` 硬编码 `finish_reason: "stop"`（response_format.py），未透传真实 finish_reason —— 待补。

## §4 Harness / 验证

1. 真机验收（用户主机）：重启栈后长回答完整、多行不丢、live/jarvis 节奏不变（清单见诊断报告 §六）。
2. P1 实现后：单元测试（finish_reason 透传、两档选档）+ 真机回归。

## §5 验收

- 默认 max_tokens=1024 且 env 可覆盖（已达成）。
- 长回复（>1024 tokens 不要求，但 >128 典型中文长回答）完整输出，无截断、无丢行（已达成，待真机确认）。
- `finish_reason=length` 有日志告警（P1 待实现）。
- 决策 token 三函数零改动回归（已达成：178 用例全绿）。

## §6 关联

- 诊断/修复记录：`doc/research/block2-token-truncation-diagnosis-2026-08-11.md`
- 决策 token 规范：`决策/交互模式与决策token规范.md`（D-2026-08-03-001/002）
- 待 P1 实现后：本草案 → 正式 spec + 决策
