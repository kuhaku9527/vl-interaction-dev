# Spec：call 模式"不知道就委派"（N2）

> 生命周期: **正式**（2026-09-14 转正；原为草稿，2026-08-13）
> 实现: `services/webui/src/joy_interaction_webui/jarvis_mode.py` 的 N2 分支（call 模式未命中已知信息 → 触发委派）
> 验证: `services/webui/tests/test_jarvis_unknown_delegation.py`（182 行）
> 日期：2026-08-13
> 关联：`doc/main/00-main-direction.md` §4.0b N2、`doc/specs/background-agent-codex-bridge.md`（N1，Codex 桥接）、`决策/服务-background-agent.md`（D-049）
> 背景事实：call 模式（`/api/llm/message` 文本直达，`interaction_mode="call"`）使用 `NO_DECISION_SYSTEM_PROMPT`（`prompt_constants.py`），**明确禁止** `</delegation>` decision token——因此模型在 call 模式答"不知道今天几号/天气"时**永远不会自行委派**。这是设计使然，但暴露了体验缺口：用户问了实时/外部信息，模型只能干答"不知道"。

---

## 1. 目标

call 模式下，当主模型回复命中"不知道/无法回答"模式时，后端**自动**触发一次 BackgroundModelService 委派（codex/hermes 查证），结果经 `background_result_ready` WS 广播补答——用户无需重问，得到一次自动查证的机会。

## 2. 非目标（边界）

- **不改 jarvis/live 模式**：两者已有 decision token 教学（`</delegation>`），模型自己会委派，不需要后端兜底。
- **不做"每个不知道都查"**：仅当回复命中未知模式才触发；命中后若后台不可用（relay 挂 / shim 关）→ fail-open 保留原回复，不阻塞对话。
- **不改变 webinfer / prompt**：纯 webui 侧（jarvis_mode.py）后置检测，零改动推理链路。

## 3. 方案（复用现有 delegation 路由）

```
webui /api/llm/message (interaction_mode="call")
  └─ _send_to_llm_non_streaming
       ├─ POST webinfer /v1/text/chat  → response + harness.decision
       ├─ [新增] 命中未知模式？(interaction_mode=="call" && looks_like_unknown_reply(response) && bg 可用)
       │     ├─ response = "让我查一下再告诉你。"
       │     ├─ decision = "delegation"
       │     └─ delegation_question = text (用户原问题)
       └─ _finish_llm_turn (现有 delegation 分支, jarvis_mode.py:1794)
            ├─ bg.handle_foreground_response(payload_text, metrics)  → 后台查证
            └─ background_result_ready WS → 前端补答显示
```

### 关键点

| 项 | 值 |
|---|---|
| 检测函数 | `looks_like_unknown_reply(reply) -> bool`（正则：不知道\|不清楚\|不了解\|无法回答\|无法获取\|没有能力\|不确定\|查不到 等） |
| 触发条件 | `interaction_mode == "call"` 且命中 且 `bg.enabled and not bg._closed` |
| 委派问题 | 用户原问题 `text`（self-contained，符合 P-D 协议） |
| 前台表现 | 替换为过渡语"让我查一下再告诉你。"，后台结果到达后补答 |
| 失败兜底 | 检测/委派任何异常 → 保留原 response，记 WARNING（约法三章：禁静默） |
| 防重复 | 仅 webinfer 未给 `delegation_question` 时才触发（若模型已委派则不重复） |

## 4. 改动清单

- `services/webui/src/joy_interaction_webui/jarvis_mode.py`：
  - 新增模块级函数 `looks_like_unknown_reply(reply: str) -> bool`
  - `_send_to_llm_non_streaming`：拿到 response + decision 后、调 `_finish_llm_turn` 前，插入未知模式改写逻辑（仅 call 模式）
- 新增测试 `services/webui/tests/test_jarvis_unknown_delegation.py`：
  - `looks_like_unknown_reply` 正/反例（"我不知道今天几号" ✅ / "我知道答案" ❌ / 空串 ❌）
  - call 模式命中 → decision 改写为 delegation + 委派问题 = 用户原问题
  - call 模式未命中 → 不改写
  - jarvis 模式命中 → 不改写（不越权）
  - bg 不可用 → fail-open 保留原回复

## 5. 验证

- [x] webui 测试：新增 7 项全绿（`tests/test_jarvis_unknown_delegation.py`）；全量 785 passed（10 个 `test_qa_server_split_runtime.py` 失败为环境基线问题——stash 验证与本次改动无关，services/.venv 依赖不完整）
- [ ] 真机（2026-08-14 验收计划 C6）：call 模式问"今天几号" → 模型答"不知道" → 后台自动查证 → 补答显示
- [ ] 与 N1（Codex 桥接）联动：补答来自 codex（`:8079`）

## 6. 风险

- 误触发：回复含"不知道"但实为反问/修辞（如"你不知道吗？"）——正则按整句匹配降低误伤；真机观察，必要时收紧。
- call 模式 stream_tts=False（纯文本直达），无 TTS 打断问题。
