# doc/specs/ — 决策前的 spec 落点（待审查组消费）

本目录**本就存在**，是各端对话在提出跨域 / 架构 / 不可逆改动时写 **spec** 的既有位置。ADR 在同级目录 `doc/adr/`。

## 正式 Spec 索引（2026-08-13 更新）

以下 spec 已定稿为**正式**（走 草稿→设计评审→实现→QA→真机 流程），实现状态与 commit 链见各文件头部状态栏：

| 正式 spec | 主题 | 实现状态 | 关键实现 commit |
|---|---|---|---|
| `token-link-max-tokens.md` | token 链路 max_tokens 两档规范 + finish_reason 可观测性 | **已实现 + QA PASS** | 29dd71e |
| `unified-turn-controller.md` | 统一 Turn Controller（13 态状态机 + 三层级联 + 7 场景矩阵） | **已实现（沙箱原型）+ QA PASS** | 40ee9dc |
| `tts-streaming-optimization.md` | TTS 链路流式化（LLM 流式 + SentenceBuffer 分句 + 逐句 TTS） | **已实现 + QA PASS** | 32c6db9 / b1f285d / 667e302 |
| `addressee-detection.md` | 说话对象判定（Phase1 CAM++ 声学门控 + Phase2 四态 not-for-me） | **已实现 + QA PASS** | 2b10c0c / ce641fc / 80cffce |
| `live-interaction-layer.md` | live 可交互层（C.A 常驻监听 + 模式互斥 + 日志分离 + 分句修复） | **已实现 + QA PASS** | b08c856 / 00a84eb / 5976581 / 5ae9401 |
| `live-visual-cb.md` | C.B 完整直播形态（VLM 视觉 + 主动搭话） | **Implemented（待真机验收）** | a536ef3 / 80caf37 / d9736ee / 08ab0c4 |

> 注：以上 6 份由 `draft-*.md` 定稿转正，草稿文件已删除（内容完整保留于正式文件，git 历史可追溯）。仍存草稿：`draft-turn-controller-integration.md`（三阶段蓝图，待整合）。

## 命名约定（沿用既有双轨，不强制统一）
- 日期前缀式：`<YYYY-MM-DD>-<topic>.md`（如 `2026-07-14-loose-coupling-services.md`、`2026-07-13-current-state.md`）
- 功能命名式：`<topic>-spec.md` 或 `<topic>.md`（如 `memory-store-skeleton-spec.md`、`hybrid-wake-confirm.md`、`kws-recall-optimization.md`）
- 新写时二选一即可；spec 内可交叉引用配套 ADR：`doc/adr/XXXX-*.md`（见 `memory-store-skeleton-spec.md` 示例）。

## 内容约定
- 结构参考既有文件：`## Problem Statement` / `## Solution` / `## User Stories` / `## Implementation Decisions` / `## Testing Decisions` / `## Out of Scope`。
- **不写最终决策**（那归 `决策/`）；spec 是决策的前提案（what / options / 推荐）。

## 生产路由（防多端污染）
- **只写不读其他端**：端点写完自己的 spec 后，不读其他端对话；由审查组对话统一召回、交叉验证、写 `决策/`，避免多端互读污染。
- 处理后：spec 保留为过程档案（不删），供追溯。
- 触发：用户手动叫审查组对话"去收 `doc/specs/` + `doc/adr/` 写决策"时才处理，**不自动轮询**。
