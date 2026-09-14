# Deprecated 目录说明

本目录是历史文档快照，按下列规则处置：

| 文件                           | 状态                              | 处置                                       |
| ------------------------------ | --------------------------------- | ------------------------------------------ |
| 700809-raw-extract.md          | 早期 11 进程 + CosyVoice 全套设计 | 仅历史参考；当前现状见 `../specs/2026-07-13-current-state.md` |
| architecture.md / .zh-CN.md    | v1 设计                           | 同上                                       |
| getting_started.md / .zh-CN.md | v1 入门                           | 见 `../README.md`（文档库总索引）          |
| rtsp_streaming.md / .zh-CN.md  | 未实现                            | `../../services/webui/src/joy_interaction_webui/rtsp_track.py` 占位，禁止据此实施 |
| troubleshooting.md / .zh-CN.md | 早期问题库                        | 问题查 `doc/subsystems/jarvis-mode.md` / `../specs/2026-07-13-current-state.md` |

### 2026-09-14 迁入：全双工语音调研族（零引用，归档）

| 文件 | 说明 |
| --- | --- |
| `final_report_full_duplex_voice.md` | 全双工语音对话技术全景深度研究报告（终稿） |
| `01_e2e_s2s_roadmap.md` | 端到端 S2S 技术路线研究报告 |
| `02_cascade_streaming_roadmap.md` | 级联流式技术路线研究报告 |
| `03_duplex_core_mechanisms.md` | 双工核心机制深度拆解 |
| `05_desktop_companion_scenario.md` | 桌面陪伴型场景需求与技术适配 |
| `04_local_deployment_assessment.md` | 开源项目本地/低算力 Windows 部署可行性评估 |
| `overview-pr3-fix.md` | PR #3 前端补修概览（2026-07-22，已完结） |

> 该族 2026-08-11 产出，经全仓核查**零引用**（未被任何 spec/adr/决策引用），故不进 SSOT 证据链，迁入本目录备查。
> 已落地结论见 `../specs/live-interaction-layer.md`、`../specs/unified-turn-controller.md`。

### 2026-09-14 迁入：企业级交付稿（`delivery-handoff-2026-07/`，8 份）

| 文件 | 说明 |
| --- | --- |
| `delivery-handoff-2026-07/高层架构设计.md` | 生成式架构交付稿 |
| `delivery-handoff-2026-07/系统设计.md` | 同上（112 KB，最大） |
| `delivery-handoff-2026-07/部署设计.md` | 同上 |
| `delivery-handoff-2026-07/安全设计.md` | 同上 |
| `delivery-handoff-2026-07/UserStory.md` | 同上 |
| `delivery-handoff-2026-07/research_report.md` | 同上 |
| `delivery-handoff-2026-07/material_digest.md` | 素材消化 |
| `delivery-handoff-2026-07/G6_全量交付汇总.md` | 汇总 |

> **归档理由**：`ARCHITECTURE.md` 末行自述"本文档由 9 份生成式架构交付稿提炼精简而成"——**提炼已完成**，这 8 份为其原料，经全仓核查**零外部引用**（仅在族内互引）。
> 当前架构以 `../../ARCHITECTURE.md` 与 `../runtime-topology.md` 为准。

### 2026-09-14 迁入：reports 一次性交接件（`reports-2026-07/`，17 份）

来源 `reports/`，**全部经实测确认零外部引用**（除自身目录外无任何文件提及）。

| 文件 | 类型 |
| --- | --- |
| `architecture-review-20260723.md` | review（校正版，推翻 0722 版 2 处误报） |
| `backend-handoff-nctx-revert.md` | handoff（n_ctx 回退因果链） |
| `conv-xval-20260728.md` | 交叉验证（DRIFT-4 因果链；源 `会话记录/` 已不存在） |
| `env-verify-20260723.txt` | 环境校验（与仍保留的 `env-backup` 成对） |
| `experience-feedback-20260723.md` | review（用户原声起点） |
| `frontend-decoupling-wrapup-20260722.md` | review（Block 1–6 拆分收尾依据） |
| `handoff-context-overflow-fix-20260723.md` | handoff（上游 PR #25 移植链） |
| `handoff-ui-optimization-2026-08-16.md` | handoff（跨对话职责边界声明） |
| `integration-2026-07-21.md` | integration（全链路联调起点 + 坑清单） |
| `integration-2026-07-21-frontend-fix-handoff.md` | integration（PR #3 修复链） |
| `integration-launcher-ps51-schema-fix-handoff-20260728.md` | integration（PS5.1 / schema 因果链） |
| `latency-diagnosis-methodology-2026-08-03.md` | 方法论（五段计时法，可复用） |
| `local-wiki-chat-integration-handoff-20260728.md` | handoff（缺口发现起点） |
| `local-wiki-code-review-20260728.md` | review（含未复核的治理发现） |
| `local-wiki-vector-design-20260724.md` | review（v4→v5 方案演进链） |
| `mem-hermes-architecture-audit-20260723.md` | audit（唯一合规漂移记录） |
| `upstream-review-20260722.md` | review（上游对照数据） |

> **归档理由**：均属"事件型"文档（类型 B）——描述"我这次干了什么"，事件结束即封存。
> 处置依据：零引用 **且** 工作已落地 **且** 无唯一证据 → 本应删除；但其中多份含**唯一的因果链记录**（如 DRIFT-4 溯源、n_ctx 回退过程），故按"零引用但含历史价值"归入本目录。
>
> **同批删除 18 份**（零引用 + 已取代 + 无唯一证据，未进本目录）：`architecture-review-20260722.md`、`audit-closure-batch2-flip.md`、`branch-cleanliness-audit-20260725.md`、`frontend-architecture-report.html`（.md 的渲染副本）、`frontend-p0-implementation-20260724.md`、`guard-result-20260723.txt` / `guard-result-20260723b.txt`（**两份字节完全相同**）、`handoff-pr13-ruff-fix-20260723.md`、`handoff-remaining-ci-20260724.md`、`integration-2026-07-22-block5-handoff.md`、`integration-pytest-gate-handoff.md`、`lint-review-and-expansion-20260722.md`、`merge-complete-20260722.md`、`pr-review-handoff-milestone2-20260721.md`、`pr-review-handoff-p0-20260721.md`、`ruff-local-audit-20260723.txt`、`state-review-arch-20260726.md`、`workspace-spillover-review-20260722.md`。均在 git 历史中可查。

**规则**：
1. 本目录不进新人入门路径。
2. 任何人翻旧实现时，必须对照 `../runtime-topology.md`（现行拓扑）验证是否仍生效。
3. 半年没引用的文件可批量删除（先 git grep + 30 天观察期）。
4. **每次迁入必须在本文件登记**（含归档理由与来源）。

最近核对日期：2026-09-14