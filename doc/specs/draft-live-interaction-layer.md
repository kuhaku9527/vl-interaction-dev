# Spec Draft：Phase C — live 可交互层（接入设计 v3）

> 生命周期: **草稿 v3**（2026-08-12）——Phase A/B 已实现，Phase C 为当前主线；走 草稿→设计评审→实现→QA→真机 流程
> 上游: `draft-unified-turn-controller.md`（v2 状态机 + live 预设）+ `draft-turn-controller-integration.md`（v2 三阶段蓝图）+ `draft-tts-streaming-optimization.md`（P0-A 流式链路）
> 用户全局观: **live 为当前主线，jarvis 模式之后搞**（避免局部优化崩坏全局）；最终验证"直播=核心、jarvis=配置矩阵"

---

## §1 现状实证（2026-08-12 代码核验）

| 层 | 现状 | 缺口 |
|---|---|---|
| webinfer 后端 | `interaction_mode="live"` 是**默认值**，live 三判断（silence/speak/delegate + forced silence）框架**已完整**（infer_loop.py:63-85 `_normalize_interaction_mode`，live=默认） | 无（可直接调用） |
| webui 后端 | JarvisSessionManager 会话框架可用（create_session/feed_audio/回调绑定）；jarvis 音频=浏览器 WebRTC `feed_audio`（jarvis_mode.py:757），`run()` 单 asyncio 循环按状态分派（:788） | **无 live 驱动循环**、无 `interaction_mode="live"` 触发点（之前实证：webui 仅 jarvis/call 两处） |
| 前端 | btListenBtn（jarvis 监听）+ call 录音按钮 | **无 live 入口按钮** |
| 共享核心 | turn_controller.py（13 态 + TurnConfig.live() 预设 + SentenceBuffer）+ P0-A 流式链路（webinfer NDJSON 流 + tts_sentence 队列 + 前端 epoch） | 迁移复用 |

## §2 目标（Phase C，分两步）

- **C.A 常驻监听形态**（本轮主线）：前端 live 入口按钮 → 进入 live 会话 → **免唤醒词**（wake_gate=False）常驻监听 → 说话即 VAD→ASR→LLM(live)→流式 TTS → 回复；含打断/冷却。复用 jarvis 会话框架 + P0-A 流式链路。
- **C.B 完整直播形态**（后续）：VLM 持续看画面 + 主动搭话 + 沉默判断自主发言（需 video 帧决策循环接入）。
- **地基 P1**：后端抑制迟到旧 llm_reply（Known Issue，live/jarvis 共用流式广播正确性）。

## §3 架构决策（本轮裁定，供评审）

1. **live 驱动循环 = 新建 `live_mode.py`（独立模块），不复用 jarvis run() 主循环**。
   - 理由：jarvis `run()` 与 KWS/唤醒链/退出词强耦合（:788-826 按 7 态分派），泛化它侵入生产路径风险高；live 无唤醒链，独立循环更干净。
   - **共享核心不丢**：live_mode 内部用 **同一 turn_controller（TurnConfig.live() 预设）** + 同一 SentenceBuffer + 同一 tts_sentence 推送 + 同一 webinfer live 语义——这正是"共享核心+配置矩阵"的落地（jarvis=jarvis 预设已委托验证，live=live 预设新实例）。
2. **live 会话复用 JarvisSessionManager 框架**（会话注册/WS 推送/回调绑定通用，不涉及唤醒语义）——泛化点：create_session 支持 mode 参数（jarvis/live），按 mode 挂载不同状态机（jarvis→JarvisStateMachine，live→LiveStateMachine）。
3. **音频链路复用**：浏览器 WebRTC → `feed_audio(pcm)`（同一入口），live 状态机内部把音频帧喂 VAD（vad_bypass.is_speech）→ ASR（JarvisASR 复用）→ endpoint → `_send_to_llm(interaction_mode="live", stream=True)`。
4. **P1 地基先行**：后端 llm_reply 广播带 reply_epoch/session 守卫，barge-in 时抑制迟到旧 llm_reply（live/jarvis 共用正确性，先修再搭 live 播放）。
5. **PRE_SPEECH 填充语**：P1 后置（本轮不启用，保持 jarvis 同款行为面）。

## §4 C.A 详细设计

### 4.1 前端（index.html）
- 新增 "live 模式" 入口按钮（语义：进入 live 常驻会话；样式贴近 btListenBtn 但独立状态）。
- 进入 live：调 `/api/live/start`（新端点，见 4.2）→ 建立 WebRTC 音频推送 + 常驻监听状态指示。
- 退出 live：调 `/api/live/stop`。
- 复用 P0-A 前端播放队列（tts_sentence 分支已通用，按 session 顶旧）——live 回复走同一队列。

### 4.2 webui 后端（server.py + live_mode.py）
- 新端点：`POST /api/live/start`（创建 live 会话，返回 session_id）/ `POST /api/live/stop`。
- `live_mode.py`：`LiveStateMachine`（或 LiveTurnLoop）：
  - 构造：`TurnController(TurnConfig.live())` + JarvisASR + VAD + 回调绑定（on_turn_commit/on_barge_in/on_pre_speech_filler=log-only）。
  - `feed_audio(pcm)`：VAD 上升沿 → `ctrl.on_speech_started(conf)`；ASR partial → `on_partial_transcript`；2s 停滞 endpoint → `on_turn_commit(transcript)` → `_send_to_llm(interaction_mode="live", stream=True)`。
  - LLM 流式消费：复用 P0-A 的 `_send_to_llm_streaming` 逻辑（decision → SentenceBuffer → 每句 `tts_sentence` 推送）——**从 jarvis_mode 抽取为共享工具**（`turn_streaming.py` 或 jarvis_mode 导出复用，避免复制）。
  - 打断：TTS 播放中 `ctrl` HARD_INTERRUPTED → 停播放 + 抑制迟到 llm_reply（P1 守卫）→ COOLDOWN → LISTENING。
  - 结束语义：live 无退出词（常驻），靠用户前端按钮退出。
- 状态上报：复用 jarvis/status 模式，live 状态给前端指示（监听中/说话中/回复中）。

### 4.3 共享抽取（最小化，防重复实现）
- P0-A 流式消费 + tts_sentence 推送逻辑从 jarvis_mode.py 抽取为**共享模块**（如 `services/webui/src/joy_interaction_webui/turn_streaming.py`），jarvis 与 live 共同调用——**行为零变化**（jarvis 回归验证）。

## §5 P1 地基：迟到旧 llm_reply 抑制

- 现象（Known Issue，QA 记录）：用户开口打断后，上一 turn 的 llm_reply 若在开口后到达，前端仍播放（epoch 只防"合成中开口"，不防"迟到旧 llm_reply"）。
- 修复：后端广播 llm_reply 时带 `reply_epoch`（每 turn 递增），barge-in/新 turn 开始时 bump；前端收到 llm_reply 校验 reply_epoch，过期丢弃。**live/jarvis 共用**。
- 验收：打断后旧 llm_reply 不再播放；正常回复不受影响。

## §6 执行顺序（本阶段）

1. **P1 地基**（先修，live 播放正确性前提）：后端 llm_reply epoch 守卫 + 前端校验 —— 团队实现 + QA。
2. **共享抽取**（turn_streaming.py）：P0-A 流式逻辑抽共享，jarvis 回归零变化 —— 团队实现 + QA。
3. **C.A 实现**：前端 live 入口 + server /api/live/* 端点 + live_mode.py 驱动循环 —— 团队实现 + QA。
4. **真机验收**：live 入口 → 常驻监听 → 说话有回复（首句 ≤800ms）→ 打断正常 → jarvis 回归不受影响。

## §7 风险与回滚

| 风险 | 缓解 |
|---|---|
| 共享抽取破坏 jarvis 流式 | 抽取后 jarvis 全套回归（194 webinfer + 364 webui 关键集）；失败即回滚抽取 |
| live 与 jarvis 双循环并发 | 两者独立会话/独立状态机，无共享可变状态（除 turn_controller 核心=只读复用）；真机验证双开 |
| webinfer live 语义行为未知 | live 三判断已在后端（默认模式），C.A 先用它跑通基本对话，主动搭话留 C.B |
| P1 改动广播链路回归 | 独立小改动 + 前端 epoch 校验静态断言 |

## §8 关联

- `draft-unified-turn-controller.md`（TurnConfig.live() 预设参数：vad 0.5-0.7 / barge_in 0.7 / silence 500）
- `draft-turn-controller-integration.md`（三阶段蓝图，本文件为 Phase C 细化）
- `draft-tts-streaming-optimization.md`（P0-A 流式链路，抽取共享的依据）
- jarvis 现状：jarvis_mode.py（run :788、_send_to_llm_streaming、tts_sentence 推送）
