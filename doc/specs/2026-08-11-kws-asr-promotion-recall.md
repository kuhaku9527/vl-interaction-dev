# Spec: KWS 本地 paraformer 提召回（recall booster）

- 日期: 2026-08-11
- 作者: AI（joyai-devteam 端点对话）
- 状态: Draft（待审查组裁定是否落 `决策/`）
- 关联: `services/webui/src/joy_interaction_webui/jarvis_mode.py`、`run-windows.env`、`services/asr/jarvis/asr.py`

## 背景 / 问题

用户真机反馈「该醒没醒」——部分真实说的 "bt" 没唤醒。根因分层：

1. **唤醒主触发 = KWS**（`services/asr/jarvis/kws.py`，sherpa-onnx `KeywordSpotter`），根本不用 ASR。本地 paraformer 在双音节 "bt" 上常被拼错（代码注释 `jarvis_mode.py:961` 明说），所以 KWS 漏掉的 "bt"，本地 ASR 也不一定接得住。
2. 现有 `WAIT_ASR_CONFIRM` 是**非阻塞**确认 + `fresh-window KWS probe` 兜底直接唤醒（`jarvis_mode.py:956-990`），ASR 质量不影响召回、也不拒绝误唤醒——即「换 ASR 不会动唤醒率」的真相。
3. 「静音误唤醒」经实测音频复核**不成立**：168 个落盘 capture 全有真实音频（rms≥0.025），FAR 高是 KWS 在持续环境音上误命中，不是静音。

## 关键实测发现（决定实现方向）

用户原想「换成云端 ASR（认为有纠错）提召回」。实测证明 **云端反而帮倒忙**：

- 用真实漏检片段（`kws_live_1786405294570_0029_peak500_rms037.wav`，07:41:34，Mic B，用户说 "bt 在吗"）POST 到 SiliconFlow `SenseVoiceSmall`：返回 `{"text":"滴滴你在吗？"}`——**"bt" 被 mangling 成中文「滴滴」**，`_asr_confirm_match` 的 `bt` 子串匹配彻底失效。
- 同一片段，**本地 paraformer**（已在跑的 in-proc shadow ASR）转出 `"b t 在吗"`（日志 07:41 shadow ASR 实证），`_asr_confirm_match("b t 在吗")` 命中。

结论：提召回只能走**本地 paraformer**（云端把 "bt" 吃掉），promotion 复用已在 KWS_LISTENING 跑的 shadow ASR，不再发任何云端请求。

## 目标

用已在跑的**本地 paraformer shadow ASR** 做提召回：KWS 未命中、但本地 ASR 仍听到唤醒词时，直接唤醒。零新增依赖、零云端调用。

## 设计

### 配置（`JarvisConfig`，env 闸门，默认 OFF）
- `asr_promotion_enabled` ← `JARVIS_ASR_PROMOTION_ENABLED`（默认 False，安全）
- `asr_promotion_cooldown_s` (2.0)：两次 promotion 唤醒最小间隔（去抖，避免同一句话重复触发）

（已删除云端字段 `asr_promotion_url/api_key/model/interval_s/min_rms`：`url` 为空即禁用、`rms` 由 shadow ASR 自身只对有声段出文本、无需云端限流。）

### 行为
- 仅在 **KWS_LISTENING 未命中分支**（`_handle_kws` → `_feed_kws_shadow_asr`）触发。
- `_feed_kws_shadow_asr` 本就在每帧跑本地 paraformer；当 `_asr_confirm_match(text)` 命中（即 KWS miss 但 ASR 听到唤醒词），调 `_try_promote_from_local_asr(text)`。
- `_try_promote_from_local_asr`：校验 `state==KWS_LISTENING` → 过 `asr_promotion_cooldown_s`（自 `_last_promo_wake_at` 与 `_last_kws_hit_at` 双向冷却）→ `asyncio.create_task(_direct_wake_from_kws(source="asr-promotion-local", respect_fresh_gate=False))`。

### ASR confirm 宽匹配策略
本地 paraformer 对双音节 "bt" 常输出分段形式（`"b t"`、`"b.t"`、`"b、t"`、`"b  t"` 等）。`_asr_confirm_match` 默认采用**归一化宽匹配**：
- 把所有非单词字符（空格、标点、CJK 标点等）折叠为单空格；
- 接受 `"bt"` 连续 token，或相邻 token `"b"` 后 `"t"`；
- 顺序相反（`"tb"` / `"t b"`）和相似但不包含 b-t 相邻的字（`"about"`、`"bit"`）**不命中**。
- `JarvisConfig.asr_confirm_patterns` 保留为**显式覆盖**：非空时完全替换宽匹配，按给定子串命中（backward compat / operator override）。

此策略在提升召回的同时，避免 `"ab tc"` 这类含子串 `"b t"` 的无关文本被误命中。

### 安全 / 不变量
- **只增不删**：promotion 仅在 KWS miss 时触发，绝不压制 KWS 命中；KWS 命中路径不变。
- **fail-safe**：promotion 全程 `try` 包裹在 KWS feed 路径之外（`asyncio.create_task` 异步唤醒），任何异常只 log，不污染 KWS 主路径；`asr_promotion_enabled=false` 即时关闭（env，无需改码）。
- `_direct_wake_from_kws` 的 `respect_fresh_gate=False` 以免被 `kws_fresh_window_direct_wake` 拦截；其 `start()` 仅重置本地 ASR 流（幂等，已实测安全）。

## 风险 / Tradeoff（已与用户确认方向）

开启 promotion **会引入额外 FAR**：本地 ASR 偶发把非 "bt" 音频转出含 "b t" 子串的文本，触发多余唤醒。用户明确选「提召回优先」。代价可用 env 即时抵消。

## 与现有架构关系

- 完全复用已在跑的 **in-proc paraformer**（`services/asr/jarvis/asr.py`），**不新增模型、不新增密钥、不发任何网络请求**。
- 与既有 `kws_shadow_asr_enabled`（诊断开关）正交：shadow ASR 默认开（诊断），promotion 是叠加在其之上的唤醒动作，受 `asr_promotion_enabled` 单独闸门控制。

## 验收

1. `stop-joyai.ps1` + `start-joyai.ps1 -Mode minimal` 重启。
2. `run-windows.env` 确认 `JARVIS_ASR_PROMOTION_ENABLED=true`（语义已从 cloud 改为 local）。
3. 浏览器说**轻/短/含噪的 "bt"**（KWS 易漏的，尤其 Mic B 低电平场景）看是否仍能唤醒；paraformer 输出无论是 `"bt"` 还是 `"b t"` / `"b.t"` 等分段形式都应触发 promotion。
4. 日志出现 `ASR PROMOTION wake (local paraformer): ...` 即命中提召回路径。
5. 同步统计新增 FAR（误唤醒是否明显上升），决定是否保留 / 调阈值。

## 待办

- [ ] 审查组裁定是否落 `决策/`（端点对话只写 spec/adr，不互读、不改 `决策/`）。
- [ ] 真机验收后回填 `reports/` 与 `REGRESSION_LOG.md`。
