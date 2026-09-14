# Spec（草稿）— 三模式隔离决策边界澄清（后端语义 vs 前端交互）

> 状态：**草稿**（**保留原因**：澄清性/说明性文档，非实施合同——其结论已并入 `决策/交互模式与决策token规范.md`，本身无需"转正"。内容经核与代码一致）
> 关联：`doc/specs/addressee-detection.md`（四态）、`doc/subsystems/jarvis-mode.md`（13 态）、`doc/specs/draft-live-empathy-tuning.md`（共情调优）、`services/webinfer/frame_parsing.py:34`、`services/webui/.../static/live_ui.js:659`
> 背景：早前用户反馈"live/jarvis 双开造成奇怪现象"（2026-08-12 真机）——本文件钉死三模式的隔离边界，回答"哪些该在后端区分、哪些该在前端限制"。

---

## 1. 结论（一句话）

**三模式是"一个前端互斥选择 + 两个独立状态机 + 三种 webinfer 语义"——前端管互斥入口，后端管语义路由，两者边界互不越界。**

## 2. 前端交互层（用户入口）

| 模式 | 前端入口 | 互斥关系 |
|---|---|---|
| **jarvis**（唤醒词驱动） | "Jarvis 唤醒"按钮 → `/api/jarvis/start` | 与 live **互斥**（radio group） |
| **live**（常驻监听） | "Live 常驻"按钮 → `/api/live/start` | 与 jarvis **互斥**（radio group） |
| **call**（文本直达） | jarvis/live 会话内文本输入框 → `/api/llm/message`（`interaction_mode="call"`） | **非独立模式**——是会话内文本子通道，无独立按钮 |

**互斥实现（live_ui.js:659-668，单模式不变式）**：
- 两个按钮是 **radio group**：点击一个模式，先调 stop 另一个，再启动自己
- **无任何自动切换路径**（无 timer / 状态观察器 / 回调自动 toggle）——切换只能由用户显式点击
- 快速连点有防竞态处理（losing handler 在 stop-other 后复查对方 active/starting 标志，双模式永不可能同时运行）

## 3. 后端语义层（状态机 + decision token）

| 模式 | 状态机 | 决策框架 | 生效 prompt |
|---|---|---|---|
| **jarvis** | `JarvisStateMachine`（13 态：KWS→ASR→LLM→TTS→退出词） | **三态** decision token（silence/response/delegation） | `config_system_prompt`（bt-7274 persona） |
| **live** | `LiveStateMachine`（常驻监听） | **四态**（+not-for-me，addressee 判定前置） | `LIVE_SYSTEM_PROMPT_EN` |
| **call** | 无独立状态机（走会话 `_send_to_llm`） | **无** decision token（纯文本） | `NO_DECISION_SYSTEM_PROMPT` |

**关键隔离点**：
- 两个状态机**实例独立**（JarvisStateMachine / LiveStateMachine 各自创建，不共享）
- webinfer 按 `interaction_mode` 路由 prompt（`frame_parsing.py:34` `_VALID_INTERACTION_MODES = {"live","call","jarvis"}`），**语义隔离在后端**：jarvis 三态、live 四态、call 无 token
- 未知名 mode 有 fail-open 回退（`frame_parsing.py:104` → 默认 live），但不影响三模式内部隔离

## 4. 边界划分（回答"哪层管什么"）

| 边界 | 归属 | 依据 |
|---|---|---|
| **"同时只能一个语音模式在跑"** | **前端互斥**（radio group + 无自动切换） | 后端不阻止双 session 启动，但前端保证单模式不变式 |
| **"这个模式用几态决策"** | **后端语义**（interaction_mode 路由 prompt） | webinfer `_resolve_base_system_prompt` 三路路由 |
| **"addressee 判定适用谁"** | **后端**（仅 live 四态教学；jarvis 不学 not-for-me） | prompt 常量 + 测试锁定 jarvis 零改动 |
| **"call 模式不许出 decision token"** | **后端**（NO_DECISION_SYSTEM_PROMPT 明确禁止） | issues #44/#45 教训固化 |
| **委派触发（N2）** | **后端**（call 模式"不知道"检测在 `_send_to_llm_non_streaming`） | `call-mode-unknown-delegation.md` |

## 5. 为什么这样分（设计理由）

1. **前端管互斥是对的**：状态机是后端资源，但"用户此刻想用哪个模式"是交互决策；radio group 简单可靠，后端无需感知"另一个状态机存不存在"。
2. **后端管语义是对的**：decision token 教学、prompt 选择、addressee 判定是模型行为，必须与交互模式强绑定（同一个 LLM 因模式不同给不同 prompt）。
3. **call 是子通道而非模式**：它复用 jarvis/live 会话，只是"不带决策框架的纯文本一问一答"（N2 之前永不委派，是设计）；不设独立按钮避免前端模式数膨胀。

## 6. 验证

- [x] 代码实证：radio group 互斥实现（live_ui.js:659-668）+ 无自动切换注释
- [x] 代码实证：双状态机独立（jarvis_mode.py:95 / live_mode.py:128）
- [x] 代码实证：webinfer 三路 prompt 路由（_resolve_base_system_prompt）+ 三态/四态/无 token
- [ ] 真机（验收计划 L4 双开）：live 开启时点 jarvis 监听 → 前者先停；各自对话互不干扰

## 7. 变更记录

| 日期 | 变更 | 作者 |
|---|---|---|
| 2026-08-14 | 初版：基于代码实证钉死三模式隔离边界（task #8 澄清） | workbuddy（历史环境） |
