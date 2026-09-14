# reports/ — 一次性工作留痕（事件型文档）

> **类型 B（事件型）**：本目录存放一次性交接/审计/集成留痕。**允许写完即封存**，不要求持续更新；
> 但必须定期退役。处置判据见 `../doc/README.md` 的「文档生命周期契约」。

> ⚠️ **注意**：本目录曾堆积 71 项且**从未退役过任何一份**（自仓库诞生）。
> 2026-09-14 首次清理：删 18 份（零引用+已取代+无唯一证据）、归档 17 份到 `../doc/deprecated/reports-2026-07/`、删 3 张重复截图。

## 留存清单（19 份，均有保留理由）

| 文件 | 保留理由 |
|---|---|
| `code-health-audit-20260723.md` | **被 `决策/` 引用 8 次** + ADR-0011 + 测试文件 —— 审计纪律（四章结构）的母本 |
| `optimization-plan-context-architecture-20260723.md` | **被 `决策/业务-上下文架构.md` 引用 6 次**；ADR-0009/0010 草案的唯一载体 |
| `audit-status-20260724.md` | 被 `决策/业务-审计收口.md` 引用 |
| `frontend-review-20260723.md` | 被 `决策/跨域铁律.md` 引用 |
| `webui-reality-inventory-2026-08-17.md` | 被 `doc/specs/webui-redesign-mapping.md` 引为「改造红线」 |
| `integration-webui-ui-2026-08-17.md` | 被 `webui-redesign-spec.md` 引用（活跃） |
| `integration-inputbar-vs-settings-2026-08-18.md` | 被 spec + `static/styles.css` **双引用** |
| `handoff-settings-modal-feedback-2026-08-16.md` | 被 `webui-redesign-spec.md` 引用，仍在追加 |
| `drift-gate-handoff.md` | 被 spec + `scripts/drift_gate.py` 双引用 |
| `handoff-lint-gate-batch2-20260724.md` | 被 ADR-0011 引用 |
| `handoff-lint-gate-batch3-20260724.md` | 被 ADR-0011 + 两个 pyproject 引用 |
| `local-wiki-chat-integration-analysis-20260728.md` | **源码注释指向**（`memory_io.py`/`system_prompts.py`） |
| `acceptance-vad-e2e-2026-08-11.md` | **唯一证据**：VAD 启用参数的量化依据 |
| `ssrf-triage-20260815.md` | **唯一留痕**：原始基线 `.cache/shield_l3_report.json` 被 gitignore |
| `webui-logic-audit-2026-08-18.md` | **唯一证据**：8 维度 DOM 实测，实测态不可复得 |
| `webui-refactor-changelog-2026-08-18.md` | 用户明令「因果链可追溯」的产物 |
| `env-backup-20260723.txt` | 被 `doc/standards/workspace-isolation.md` 引为回滚参照 |
| `frontend-architecture-report.md` | 被 `doc/frontend/README.md` 引用 |
| `security-guardrail-deepsec-minimax.md` | **唯一说明**：DeepSec 护栏的启用/禁用/成本/限制（实质是运维文档，可考虑迁 `docs/`） |
| `webui-preview-2026-08-18/`（14 PNG） | 截图证据（原 17 张，已去重 3 张**内容完全相同**的） |

---

*由 2026-09-14 文档收口建立。跑 `scripts/doc_health.py` 复查。*
