# Spec v2：统一 Turn Controller（共享核心 + 配置矩阵）

> 生命周期: **正式**（2026-08-13 定稿，走 草稿→设计评审→实现→QA→真机 流程完成）
> 上游: 用户全局观（jarvis=直播变种，优先直播模式）+ 块0 交叉验证（"缺统一调度层"）+ 块3 深潜 + **块3 交叉验证**（`doc/research/block3-cross-validation-2026-08-12.md`）
> 实现: 40ee9dc（沙箱原型：13 态状态机 + SentenceBuffer + 3 预设 TurnConfig）
> 验证: 64 单测（turn_controller + SentenceBuffer）+ QA 27 — QA PASS
> 状态: 架构正式 v2（沙箱原型已验证；live/jarvis 收敛落地见 `live-interaction-layer.md` / `live-visual-cb.md`）
> v1→v2 变更: 状态机 7→12 态扁平（吸收 jarvis 已有态）；配置矩阵 2→7 场景；新增 Sentence Buffer/LLM 超时三层；否决分类头方案（与 ADR0006 冲突）

---

## §1 因果链（Why）

- **现状三套割裂的 turn 逻辑**（块0 交叉验证确认"缺统一 Turn Controller"）：
  1. **jarvis 状态机**（`jarvis_mode.py:429-456`）：`KWS_LISTENING → WAKE_DETECTED → DIALOG_ACTIVE ⇄ TTS_PAUSED ← EXIT_DETECTED / WAIT_ASR_CONFIRM / ERROR`——唤醒门 + 打断（TTS_PAUSED）+ 退出词，最完整但**只服务 jarvis**；
  2. **live 三判断**（决策 token，D-2026-08-03-001/002）：silence/response/delegation，模型语义判定，无显式状态机；
  3. **Smart Turn v3.2**（`smart_turn_adapter.py`，**音频原生** ONNX，默认关）——语义端点。
- **用户全局观**：直播优先；jarvis = 直播变种（模块开关）→ 共享核心状态机 + 配置矩阵。
- **块3 外部佐证**：Full-Duplex-Bench"显式控制模块优于端到端"（arXiv:2503.04721）**背书决策 token 路线**；级联 >85% 生产份额。

## §2 范围与负面约束

- **做**：统一状态机 v2 + 7 场景配置矩阵；Sentence Buffer / LLM 超时三层设计；与 jarvis/live/Smart Turn 的收敛映射。
- **不做**：不实现代码（待原型）；**不推翻 ADR0006 决策 token**（分类头方案否决——CPU-only llama.cpp 分类头不可行且逆转核心 IP）；不引入 LiveKit Turn Detector（本地 Smart Turn 已音频原生，无需替换）；不引入两层 HSM 嵌套（单机单会话，扁平足够）；不实现 7 场景中未映射的场景（仅入矩阵）。

## §3 方案

### 3.1 统一状态机 v2（12 态扁平，吸收 jarvis 已有态）

```
[IDLE] → [WARM_UP(←jarvis WAKE_DETECTED)] → [LISTENING] → [USER_SPEAKING]
   ↑            │ 唤醒门(仅jarvis)              ↑              │
   │            ▼  live 常驻 LISTENING         │              ▼
   │                                          │          [PROCESSING]
   │                                          │              │ LLM 决策
   │                                          │              ▼
   │                                     [PRE_SPEECH] 填充语(TTFT>500ms)
   │                                          │              │
   │                                          ▼              ▼
   │                                     [SPEAKING] ←── [THINKING]
   │                                          │
   │                                  打断(声学+语义)
   │                                          ▼
   │                              [HARD_INTERRUPTED(←TTS_PAUSED)]
   │                              [SOFT_INTERRUPTED(句边界)]
   │                                          │
   │                                          ▼
   │                                   [COOLDOWN 200-500ms] → LISTENING
   └── [ENDED] ← 退出语义 / [ERROR]（任意态，可恢复，←jarvis ERROR）
```

- **与 jarvis 映射**（统一核心 = jarvis 泛化，不重造）：WARM_UP=WAKE_DETECTED；HARD_INTERRUPTED=TTS_PAUSED；ERROR=ERROR；WAIT_ASR_CONFIRM 保留为 jarvis 配置下的子流程。
- **关键路径**：`THINKING → INTERRUPTED`（用户打断 LLM 推理，立即取消进 LISTENING）——jarvis 现状缺此路径，v2 补上（块3 断言 14/87）。

### 3.2 三层级联（职责边界，经交叉验证确认）

| 层 | 组件 | 职责 | 本地现状 |
|---|---|---|---|
| L1 声学 | Silero VAD（`vad_bypass.py`） | **仅语音检测** `is_speech()`，不做端点决策 | ✅ 天然一致（KWS 软门控用途，默认 OFF） |
| L2 语义 | Smart Turn v3.2（`smart_turn_adapter.py`） | 语义话轮边界（**音频原生**，无转录延迟） | ✅ 已有，纳入核心按模式启用 |
| L3 LLM | decision token（`parse_model_decision`） | 最终话轮/主动搭话/静默决策 | ✅ 已有，核心 IP 不动 |

- **双阈值**（采纳块3）：普通语音 0.65 / barge-in 0.75（v2 引入，配置矩阵参数）。
- 延迟预算：L1≤10ms / L2≤100ms / L3≤100ms，总 ≤200ms（块3 断言 30）。

### 3.3 配置矩阵 v2（2 → 7 场景，本地映射 3 个）

| 场景 | 本地映射 | VAD threshold | silence | 打断 | 主动搭话 |
|---|---|---|---|---|---|
| 直播（实时对话） | **live 模式** | 0.4-0.6 | 400-700 | 开(adaptive) | 开 |
| 单向直播（预留） | — | 0.7-0.9 | 1000-1500 | 关 | 关 |
| 语音助手 | **jarvis 模式**（唤醒门开） | 0.3-0.5 | 200-400 | 开(vad) | 关 |
| 实时对话（并发会话） | 未来核心态 | 0.4-0.6 | 400-700 | 开(adaptive) | 开 |
| 会议 | 预留 | 0.5-0.7 | 700-1000 | 关 | 关 |
| 客服 | 预留 | 0.5-0.7 | 700-1000 | 开(保守) | 关 |
| 教育 | 预留 | 0.5-0.7 | 1000-1500 | 关 | 关 |
| 面试 | 预留 | — | — | 手动 | 关 |

> **映射修正（QA 交叉验证 2026-08-12 裁定）**：项目 live 模式是"陪伴型双向实时对话"（主动搭话+快打断），应映射**实时对话**参数族（silence 400-700 / 打断开），**非**"单向直播"（主播输出、听众只听，silence 1000-1500 / 打断关）。单向直播列为预留场景。jarvis 模式（唤醒门）对应语音助手族。

- **三预设**（采纳 Vapi 思想）：Aggressive（实时对话，wait ~200ms）/ Normal（语音助手 ~800ms）/ Conservative（~2700ms，预留）。
- **LLM 总生成超时裁定（QA 2026-08-12）**：`llm_total_timeout_ms=10s`（与块3 参考实现一致，GPU+CPU 混合部署下长回复余量更充足）；草稿原"5s"为块3 主报告推荐值，更新为 10s。
- 动态切换：条件切换（多人→会议）作 P2。

### 3.4 工程落地（块3 修正点 5，P0 采纳）

1. **Sentence Buffer**（P0，本场景刚需）：LLM 流式输出在**决策 token strip 后**按句边界（`.!?。！？` + 17 缩写排除，原型实测）flush 到 TTS；min_sentence_length=10 / max_buffer_chars=500 / flush_on_timeout_ms=500。云端 MiniMax TTS 流式拼接降感知延迟。
2. **LLM 超时三层**（P0）：TTFT>500ms → PRE_SPEECH 填充语（"让我想想…"）；总生成>10s → fallback；连续 3 次 → 降级规则引擎。
3. **四级降级**（P1，与本地 fail-open 传统一致）：完整链 → 去 L3 → 去 L2 → WebRTC VAD + 固定超时。
4. **可观测性**（P1）：结构化为 OTel 预留；turn_decision_latency / e2e_latency / barge_in_count。
5. **KV Cache 预热**（P2）：llama.cpp prefix caching 验证后定；vLLM 方案不适用（当前为 llama.cpp 部署）。

## §4 Harness / 验证

1. 沙箱原型：以 jarvis 状态机为骨架抽核心（不破坏现有行为）→ live 作为无唤醒门实例跑通；
2. 真机验收：直播模式主动搭话/打断节奏 + jarvis 回归（唤醒/退出不变）+ 长回复完整性（块2 修复回归）；
3. 通过后：v2 草稿 → 正式 spec + ADR + 决策（`决策/` 走审查组）。

## §5 待明确事项

- `WAIT_ASR_CONFIRM` 在统一核心中的位置（jarvis 专属子流程 vs 通用确认态）？
- COOLDOWN 期间是否忽略 VAD（社区建议）还是降阈值（本地 jarvis 现状）？
- PRE_SPEECH 填充语是否也需 TTS 打断支持（填充语被打断的边界）？
- 7 场景矩阵中会议/教育/面试是否值得当前实现（还是纯预留）？

## §6 关联

- 块3 深潜：`final_report_unified_turn_controller.md` + 13 附件（工作区根）
- 块3 交叉验证：`doc/research/block3-cross-validation-2026-08-12.md`
- 决策 token 规范：`决策/交互模式与决策token规范.md`（D-2026-08-03-001/002）
- 语音栈现状：`决策/服务-语音栈.md`
- 块2 修复（长输出/多行）：`doc/research/block2-token-truncation-diagnosis-2026-08-11.md`
