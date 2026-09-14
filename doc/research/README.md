# doc/research/ — 调研与诊断证据链

> **类型 C（证据链型）**：本目录存放云端调研交付、交叉验证、诊断快照。**被 spec/adr 引用即不可删**。
> 与 `reports/` 的区别：`reports/` 是**过程留痕**（handoff/integration），本目录是**设计依据**（调研结论）。
>
> **最近核对**：2026-09-14 ｜ 自检：`/d/AI/envs/joyai-main/python.exe scripts/doc_health.py --check link`

## 顶层文件

| 文件 | 主题 | 被谁引用 |
|---|---|---|
| `addressee-cross-validation-2026-08-12.md` | 说话对象判定：三源交叉验证 | `doc/specs/addressee-detection.md` |
| `bargein-latency-analysis-2026-08-12.md` | 打断延迟分析 | `doc/specs/tts-streaming-optimization.md` 关联 |
| `block0-cross-validation-2026-08-11.md` | 块0 交叉验证（"缺统一调度层"结论） | `doc/specs/unified-turn-controller.md` |
| `block2-token-truncation-diagnosis-2026-08-11.md` | 长输出/多行截断诊断 | `doc/specs/token-link-max-tokens.md`（上游依据） |
| `block3-cross-validation-2026-08-12.md` | 块3 交叉验证 | `doc/specs/unified-turn-controller.md` |
| `kws-silent-false-wake-diagnosis-2026-08-12.md` | KWS 静默误唤醒诊断 | KWS 调参 |
| `kws-v5-2026-08-10-diagnosis.md` | KWS v5 诊断 | KWS 训练 |
| `kws-vad-bt-wakeword.md` | KWS/VAD 唤醒词研究 | KWS 训练 |
| `lightweight-replacement.md` | 硬件/模型选型调研（GGUF/ASR/TTS） | `doc/subsystems/memory-architecture.md` |
| `memory-store-research.md` | 持久化层选型调研 | `doc/specs/memory-store-skeleton-spec.md` + `doc/adr/0005` |
| `source-project-live-reference-2026-08-12.md` | 源项目 live 形态对照快照 | live 模式设计 |
| `tts-latency-analysis-2026-08-12.md` | TTS 链路延迟分析 | `doc/specs/tts-streaming-optimization.md` |

## 子目录（证据链族）

| 目录 | 内容 | 被谁引用 |
|---|---|---|
| `turn-controller-2026-08-11/` | 统一 Turn Controller 调研族（**7 份**：终稿 `final_report_unified_turn_controller.md` + 6 份支撑：01_sota / 02_vad_cascade / 03_decision_token / 04_multi_scenario / 05_interruption / 06_e2e_latency） | `doc/specs/unified-turn-controller.md` §6（"块3 深潜"）、`doc/specs/tts-streaming-optimization.md`、`doc/research/tts-latency-analysis-2026-08-12.md` |
| `addressee-detection-2026-08-12/` | Addressee Detection 调研族（**5 份**：终稿 `final_report_addressee_detection.md` + 4 份支撑：01_problem_definition / 02_acoustic_approach / 03_semantic_approach / 04_hybrid_approach） | `doc/specs/addressee-detection.md`（"上游"）、`doc/research/addressee-cross-validation-2026-08-12.md` |
| `data/` | 基准数据（`benchmark_4state_notforme_results.json`，125 KB） | `services/scripts/benchmark_4state_notforme.py`、`doc/specs/draft-live-empathy-tuning.md` |

> **两个子目录族的来源**：2026-09-14 从仓库根目录迁入（原为散落的 `01_*.md`~`06_*.md` + `final_report_*.md`），
> 因它们被 `doc/specs/` 正式引为"上游调研"，属证据链。迁移时同步修正了所有引用指针。

## 维护规则

1. **被 spec/adr 引用 → 不可删**（引用是硬存活理由）。
2. **`.workbuddy/tmp/` 等历史路径引用属"记录当时的方法"**，是史实，**不要"修正"**（见 `doc/README.md` 维护规则 #8 的 NEVER_REWRITE）。
3. 新增调研：命名 `<topic>-<YYYY-MM-DD>.md` 或建族目录；**必须登记进本索引**。
4. 零引用且无唯一证据的，方可考虑归档（本目录目前无此类）。
