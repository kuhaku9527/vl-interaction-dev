# ADR 0006: LLM 网关单入口 + 决策 token 四态（v3.38）

- 状态: Accepted（v3.38 演进，2026-08-13）
- 日期: 2026-07-13（原版）→ 2026-08-13（四态 + frames 边界更新）
- 上下文: doc/specs/2026-07-13-llm-path-consolidation.md + doc/specs/draft-addressee-detection.md（四态）+ doc/specs/draft-live-visual-cb.md（frames）

## 决策
所有 LLM 调用必须经过 webinfer :8070。webui 不再持有指向 :7060 llama-server
的直接连接。webinfer 暴露两个 HTTP 入口:
- POST /v1/text/chat (纯文本为主；live 模式可携带顶层 `frames` 字段组装多模态 payload，拒绝消息级 `image_url`)
- POST /v1/chat/completions (多模态；消息级 `image_url` 含 base64 data URL；非流式)

## 决策 token 四态（v3.38 演进，2026-08-13）
- **三态 → 四态**（live 模式）：`</silence>` / `</response>` / `</delegation>` / **`</not-for-me>`**（用户非面向 AI 的自言自语/回应旁人）。
- **jarvis 模式保持三态**（不引入 not-for-me）：prompt 路由 `_resolve_base_system_prompt` 按 interaction_mode 分发（live→LIVE_SYSTEM_PROMPT_EN 四态 / jarvis→三态 / call→NO_DECISION），cache key 含 interaction_mode 防污染。
- **解析优先级**（parse_model_decision）：delegation ANYWHERE > not-for-me ANYWHERE > response/silence 最早出现。not-for-me ANYWHERE 优先 = "宁可漏、不可乱插"落地。
- **消费语义**：not-for-me ∪ silence 同计"不播报"（模型爱用空输出/silence 表达不回复，召回不足由声学门控 Phase1 + 融合层补偿）。
- 四态是 ADR0006 单入口内的**语义扩展**，不改变"webinfer 单入口 + 显式失败不回退"的核心不变式。

## live 模式 frames 边界（v3.38 补充，2026-08-13 评估裁定方案 B）
- `/v1/text/chat` live 模式可携带顶层 `frames: [{image_b64, ts_ms}]`（≤6 张，非法显式 400；data-URI 前缀容忍 strip）。
- **不走 chat/completions 的原因**（评估实证）：chat/completions 缺 4 项 live 能力——① 无 stream NDJSON 四态协议；② forced-silence 短路使 proactive 空文本轮永不推理；③ 图像写入 chunk 历史每轮重发（违反 C.B 帧不进历史语义）；④ 无 [Visual Context] 观察段。迁移会触碰视频 QA 路径（D-029）回归风险大。
- **共享实现**：base64 归一化 `io_utils.normalize_image_b64`（与 chat/completions 路径共用）；视觉消息组装 `prompt_assembly.compose_live_visual_messages`。去重完成（2026-08-13 第一批）。

## 不变 / 边界
- 系统 prompt 注入、token guard、决策 token 解析、qa_history 写回、memory
  warmup 全部在 webinfer 完成；webui 只做 HTTP 转发 + streamingharness 字段读取。
- webinfer 挂 = 三条入口全瘫。**显式失败，不回退到 :7060 直连**。

## 后果
- webinfer 成为新 SPOF（之前 Jarvis 文本直连绕开了它）。
- _send_to_llm 公共签名不变，向后兼容。
- 决策 token silent regression 兜底（jarvis 侧 fallback 到 decision=response）需要
  在 ADR 里明确写下来（防 schema 漂移）。
- 四态扩展后：live 消费侧（live_mode/turn_streaming）按四态路由；jarvis 侧异常收到 not-for-me 时按旧三态 fail-open（空文本 TTS no-op，不崩）——测试守护（test_qa_4state_webui_supplement）。

## 替代方案（拒了）
- A: 在 webui 层做共享编排 → 维护成本高，webui 已超载。
- C: 各自维护一份编排 → 正是要消灭的两条路径问题。
- D: 四态改分类头（替代 decision token）→ ADR0006 核心 IP 逆转 + 纯 CPU llama.cpp 分类头不可行（块3 交叉验证否决）。
- E: live frames 迁移 chat/completions → 缺 4 项 live 能力 + 触碰 D-029 视频 QA 路径（评估裁定方案 B 保留 text/chat frames）。

## 引用
- doc/specs/2026-07-13-llm-path-consolidation.md
- doc/specs/draft-addressee-detection.md（四态 §4）
- doc/specs/draft-live-visual-cb.md（frames §2.3）
- services/webinfer/response_format.py（parse_model_decision 四态）
- services/webinfer/prompt_constants.py（LIVE_SYSTEM_PROMPT_EN 四态）
- services/webinfer/prompt_assembly.py（_resolve_base_system_prompt 路由 + compose_live_visual_messages）
- services/webinfer/io_utils.py（normalize_image_b64）
- services/webinfer/frame_parsing.py / stream_protocol.py / chat_payload.py（拆分后）
