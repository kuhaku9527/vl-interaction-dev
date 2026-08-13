# Spec：Phase C — live 可交互层（接入设计 v3）

> 生命周期: **正式**（2026-08-13 定稿，走 草稿→设计评审→实现→QA→真机 流程完成）
> 上游: `unified-turn-controller.md`（v2 状态机 + live 预设）+ `turn-controller-integration.md`（v2 三阶段蓝图）+ `tts-streaming-optimization.md`（P0-A 流式链路）
> 实现: b08c856（C.A 常驻监听层 + BUG-1 修复）→ 00a84eb（模式互斥 radiogroup）→ 5976581（日志心跳分离）→ 5ae9401（分句粒度修复）→ 59201d3（reply_epoch 地基）→ 667e302（turn_streaming 共享抽取）
> 验证: QA PASS（webui live_mode 系列回归：test_live_mode / test_p0a_qa / test_reply_epoch_guard 等）
> 用户全局观: **live 为当前主线，jarvis 模式之后搞**（避免局部优化崩坏全局）；最终验证"直播=核心、jarvis=配置矩阵"

---

## §0 战略决策（2026-08-12 20:1x，用户拍板）

- **不推倒重来，继续收敛式重构**：底层选型已验证正确（块0 + 交叉验证 + 源项目对照三重确认：级联流式 = 2026 生产默认；GPU(LLM)+CPU(语音侧)+云TTS 混合部署 = 现实最优），推倒重来成本/风险极高且最终架构与收敛式重构趋同。
- **收敛终点（定死，防无限重构）**：统一 Turn Controller 跑通 live **C.B**（VLM 持续看画面 + 主动搭话）→ jarvis 收敛到同一核心 → 架构层达到"干净态"（共享核心 + 配置矩阵 + 流式链路）。此后代码库不再有"打补丁"痕迹。
- **部署形态真值**（run-windows.ps1:372 `-ngl 999` 实证）：LLM/VLM 8.19B = **GPU**（VRAM~7GB）；语音侧（ASR int8 CPU / KWS CPU / VAD·Smart Turn CPU / 可选云端 ASR）= CPU；TTS = 云端 MiniMax。**"纯 CPU"仅指语音侧**，禁止笼统表述（见 2026-08-12 记忆更正段）。

---

## §1 现状实证（2026-08-12 代码核验）

| 层 | 现状 | 缺口 |
|---|---|---|
| webinfer 后端 | `interaction_mode="live"` 是**默认值**，live 三判断（silence/speak/delegate + forced silence）框架**已完整**（infer_loop.py:63-85 `_normalize_interaction_mode`，live=默认） | 无（可直接调用） |
| webui 后端 | JarvisSessionManager 会话框架可用（create_session/feed_audio/回调绑定）；jarvis 音频=浏览器 WebRTC `feed_audio`（jarvis_mode.py:757），`run()` 单 asyncio 循环按状态分派（:788） | **无 live 驱动循环**、无 `interaction_mode="live"` 触发点（之前实证：webui 仅 jarvis/call 两处） |
| 前端 | btListenBtn（jarvis 监听）+ call 录音按钮 | **无 live 入口按钮** |
| 共享核心 | turn_controller.py（13 态 + TurnConfig.live() 预设 + SentenceBuffer）+ P0-A 流式链路（webinfer NDJSON 流 + tts_sentence 队列 + 前端 epoch） | 迁移复用 |

## §2 目标（Phase C，分两步）

- **C.A 常驻监听形态**（✅ 已实现，commit b08c856 + QA PASS）：前端 live 入口 → 免唤醒词常驻监听 → 说话即 VAD→ASR→LLM(live)→流式 TTS → 回复；含打断/冷却。**三项体验优化已落地**（见 §3.2 决策留痕）。
- **C.B 完整直播形态**（下一步主线）：VLM 持续看画面 + 主动搭话 + 沉默判断自主发言（需 video 帧决策循环接入）。
- **地基 P1**（✅ 已实现，commit 59201d3）：后端抑制迟到旧 llm_reply（reply_epoch 守卫，live/jarvis 共用流式广播正确性）。

## §3 架构决策（本轮裁定，供评审）

1. **live 驱动循环 = 新建 `live_mode.py`（独立模块），不复用 jarvis run() 主循环**。
   - 理由：jarvis `run()` 与 KWS/唤醒链/退出词强耦合（:788-826 按 7 态分派），泛化它侵入生产路径风险高；live 无唤醒链，独立循环更干净。
   - **共享核心不丢**：live_mode 内部用 **同一 turn_controller（TurnConfig.live() 预设）** + 同一 SentenceBuffer + 同一 tts_sentence 推送 + 同一 webinfer live 语义——这正是"共享核心+配置矩阵"的落地（jarvis=jarvis 预设已委托验证，live=live 预设新实例）。
2. **live 会话复用 JarvisSessionManager 框架**（会话注册/WS 推送/回调绑定通用，不涉及唤醒语义）——泛化点：create_session 支持 mode 参数（jarvis/live），按 mode 挂载不同状态机（jarvis→JarvisStateMachine，live→LiveStateMachine）。
3. **音频链路复用**：浏览器 WebRTC → `feed_audio(pcm)`（同一入口），live 状态机内部把音频帧喂 VAD（vad_bypass.is_speech）→ ASR（JarvisASR 复用）→ endpoint → `_send_to_llm(interaction_mode="live", stream=True)`。
4. **P1 地基先行**：后端 llm_reply 广播带 reply_epoch/session 守卫，barge-in 时抑制迟到旧 llm_reply（live/jarvis 共用正确性，先修再搭 live 播放）。
5. **PRE_SPEECH 填充语**：P1 后置（本轮不启用，保持 jarvis 同款行为面）。

### 3.1 模式互斥 UI（用户决策 2026-08-12 18:5x，已实现 00a84eb+2f3ec43）

- **用户明确**：不需要 jarvis+live 双开（"没这个需求"）；UI 上**区分与限制**（显式单选）；**不要自动切换**（"自动切换是空需求，人类没有这个操作习惯"）。
- **实现**：btListenBtn + liveModeBtn 包进 `role="radiogroup"` 单选组（role=radio/aria-checked）；`selectLiveMode`/`selectBtListenMode`：点击 A 若 B 激活先停 B 再启 A（用户主动点击导致的切换 = 单选固有语义）；**无任何自动切换路径**（无 timer/state watcher）；快速来回点击竞态用 re-check guard（await stop-other 后重查另一模式 active/starting）修复。
- **后端双开能力保留**（create_session mode 参数 + _live_sessions dict 不动）——仅前端 UI 层限制。

### 3.2 2026-08-12 已落地决策留痕（含 commit）

| 决策 | 内容 | commit |
|---|---|---|
| P1 迟到 llm_reply 抑制 | reply_epoch 守卫（turn-start/barge-in/exit 三处 bump；前端只由后端抬升不自增） | 59201d3 |
| P0-A 流式共享抽取 | StreamingTurnConsumer → turn_streaming.py（jarvis 行为零变化） | 667e302 |
| 模式互斥 UI | radiogroup 显式单选 + re-check 竞态守卫 | 00a84eb / 2f3ec43 |
| 日志心跳分离 | `_is_heartbeat_path()` 心跳降级 DEBUG（失败≥400 仍 INFO）+ `JOYAI_LOG_LEVEL` 可恢复 | 5976581 |
| 分句粒度修复 | SentenceBuffer 逗号次级切分（`，、；;,`，max_sentence_chars=80，短句不切碎，jarvis/live 共用） | 5ae9401 |
| 部署形态更正 | "纯 CPU"→"GPU(LLM)+CPU(语音侧)+云TTS"（run-windows.ps1 -ngl 999 实证） | e63651b |

### 3.3 说话对象判定（addressee detection）——调研中，决策待定

- 用户问题：模型怎么判断"在跟它说话 vs 自言自语"？块0/块3 未覆盖（turn-taking≠addressee detection）；源项目无先例。
- 云端专项调研进行中（5 维度：场景建模/声学 speaker embedding/语义 LLM 判定/混合/工程决策）。
- **已定代价偏好**：误响应（AI 乱插话）更不能接受——"宁可漏、不可乱插"（免唤醒常驻监听下乱插话极烦人）。调研结果到后据此落地方案。

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

- `unified-turn-controller.md`（TurnConfig.live() 预设参数：vad 0.5-0.7 / barge_in 0.7 / silence 500）
- `turn-controller-integration.md`（三阶段蓝图，本文件为 Phase C 细化）
- `tts-streaming-optimization.md`（P0-A 流式链路，抽取共享的依据）
- `live-visual-cb.md`（C.B 完整直播形态，下一步主线）
- jarvis 现状：jarvis_mode.py（run :788、_send_to_llm_streaming、tts_sentence 推送）
