# Spec Draft：Phase C — C.B 完整直播形态（VLM 视觉 + 主动搭话）

> 生命周期: **草稿 v1**（2026-08-13）——C.B 是"直播=核心"主线的终点验证；走 草稿→设计评审→实现→QA→真机 流程
> 上游: `draft-live-interaction-layer.md`（§0 收敛终点定义）+ `draft-unified-turn-controller.md`（TurnConfig.live() 预设）+ `draft-addressee-detection.md`（四态 not-for-me）
> 前置（已实证就绪）: VLM mmproj 已加载（run-windows.ps1:67 `mmproj-joyai-vl-interaction-preview-f16.gguf`）；webinfer `/v1/chat/completions` 支持 image（infer_loop.py:306 拒绝 image 于 text/chat，指向多模态路径）；前端 `screen_capture.js` 已实现 1fps JPEG 帧 → WS `frame` 消息管线（frameSeq 单调 + 间隔测量）；源项目 live_adapter（7-22）有完整视频流助手实现（帧观察 prompt + video_history + FORCE_SILENCE_BEFORE_QUERY）可参照

---

## §1 目标与验收

**目标**：live 模式具备"看着画面对话 + 主动搭话"的完整直播形态：
1. **视觉上下文对话**：用户说话时，最近 1-N 帧作为视觉输入一起送 LLM → 模型"看着画面"回答（如游戏攻略、屏幕内容问答）；
2. **主动搭话**：无用户语音时周期性抽帧 → VLM 判定"有值得说的"→ 主动 TTS 开口（直播陪伴的核心）；
3. **沉默判断自主发言**：proactive 轮次复用四态 decision（response 才开口，silence/not-for-me 闭嘴）——宁少说、不乱说。

**验收**：真机——live 开启主动搭话 → 屏幕/摄像头画面变化（如游戏进 BOSS）→ AI 主动评论；用户问画面相关内容 → 回答基于画面；jarvis 回归零影响。

## §2 架构决策（本轮裁定）

1. **帧管线复用前端现有 WS `frame` 通道**：`screen_capture.js` 已推 1fps JPEG（frameSeq/interval 已实现）——live 会话内新增后端 `frame` 消息接收（server.py 或 live_routes），**不新建传输**；前端 live 模式启动时复用 startScreenCapture（或摄像头 capture），视频预览走既有 videoElement。
2. **`recent_frames` 环形缓冲（live_mode.py）**：保存最近 N 帧（默认 6，约 6 秒窗口）+ 时间戳；用户说话轮 → 全部注入；proactive 轮 → 只取最新 1-2 帧。
3. **webinfer 新增 live 视觉路径**（不破坏纯文本）：payload 增 `frames: [{image_b64, ts_ms}]`（可选）——有 frames 且 interaction_mode="live" → 走 `/v1/chat/completions` 多模态（image 列表 + 文本 + 视觉 prompt 段落）；无 frames → 现路径不变。四态 decision + 流式 content 复用现有协议。
4. **proactive 循环（live_mode.py）**：LISTENING 态 + `proactive_speak_enabled=True` 时，每 `PROACTIVE_INTERVAL_S`（默认 5s）抽最新帧 → 调 webinfer 视觉判定（轻量：max_tokens 小、仅帧+简短 system 提示、无用户文本）→ decision=response → 主动 TTS（走现有 tts_sentence 链路）；silence/not-for-me → 等下一周期。打断复用现有 barge-in（HARD_INTERRUPTED → COOLDOWN）。**默认关闭**（env `LIVE_PROACTIVE_ENABLED`，防乱开口，真机验证后开）。
5. **addressee 门控前置**：proactive 判定前先过声学门控（无需，因 proactive 与用户语音无关——不适用，仅用户语音轮过门控）。
6. **视觉历史不膨胀对话**：recent_frames 只注入当前轮（不进 history 持久化）；每轮帧随该轮请求发送，history 只存文本——控制 llama context 增长。

## §3 实现分层（三阶段，每层独立可测）

### 层 1：webinfer 视觉路径（infer_loop.py / prompt_assembly.py）
- payload `frames` 解析（image_b64 校验、数量上限 6、尺寸未知按 llama 侧处理）；
- live 视觉请求组装：`_build_live_visual_messages`——system（LIVE_SYSTEM_PROMPT_EN 四态 + 视觉观察段："The following frames are the current visual context…"）+ 用户文本 + frames（image_url data URI）；
- 路由：`interaction_mode="live"` 且有 frames → 多模态 chat.completions；流式（用户轮）与非流式（proactive 轮）双模式；
- 回归：无 frames 时路径逐字节不变（纯文本 live 零回归）；jarvis/call 不受影响。
- 测试：帧消息组装（image_url 格式/数量上限/缺失容错）、视觉轮 decision 解析（四态）、无帧回归。

### 层 2：live_mode.py 视觉上下文 + proactive 循环
- `recent_frames: deque(maxlen=6)`（(image_b64, ts_ms)）；
- `handle_frame(image_b64, ts_ms)`：入缓冲 + 日志 `[live-mode] frame captured (n=.., window=..s)`；
- 用户轮：`_send_to_llm(text, frames=list(recent_frames))` → 视觉流式；
- proactive 循环（asyncio task）：`_proactive_loop()`——LISTENING 且启用时每 N 秒：抽最新帧 → `_send_proactive_prompt(frames=[latest])`（轻量非流式，max_tokens 256，temperature 0.7）→ decision=response → `_spawn_sentence_tts` 主动播报 + controller 进 SPEAKING；silence/not-for-me → 静默等下一周期；用户语音出现（USER_SPEAKING）→ 循环暂停（等回 LISTENING）；
- 打断：主动播报中被用户打断 → 现有 HARD_INTERRUPTED → COOLDOWN → LISTENING（proactive 循环继续）；
- env：`LIVE_PROACTIVE_ENABLED`（默认 false）、`LIVE_PROACTIVE_INTERVAL_S`（默认 5）、`LIVE_FRAME_WINDOW`（默认 6）；
- 约法三章：结构化日志 `[live-proactive] ...`；异常 fail-open（proactive 失败静默跳过，不影响用户对话）。
- 测试：帧缓冲窗口/轮转；用户轮带帧；proactive 循环（mock VLM 返回 response → 播报；silence → 静默；USER_SPEAKING 暂停/恢复；打断）；env 默认关零变化。

### 层 3：前端（index.html + screen_capture.js 复用）
- live 模式新增"视频画面"接入：live 启动时可选启动 screen capture 或摄像头（复用现有 capture UI/按钮，或在 live 面板内加"开画面"按钮）；
- 帧推送：live 会话的 WS 已复用 → screen_capture.js 的 frame 消息在 live 会话内由后端接收（确认 WS 消息路由：`frame` 类型 → live session）；
- "主动搭话"开关（默认关，checkbox/按钮，样式与 live 一致）；
- 视频预览：复用 videoElement（capture 已实现）。
- 静态断言：live 面板控件存在、开关接线、frame 消息处理。

## §4 风险与边界

| 风险 | 缓解 |
|---|---|
| proactive 误开口（模型"值得说"判定不准） | 默认关 + response 才开口 + 真机调阈值/few-shot |
| VLM 帧 prefill 延迟（GPU 上每帧成本） | proactive 轮轻量（1 帧 + 小 max_tokens）；用户轮 6 帧上限；真机实测延迟 |
| 视觉历史膨胀 context | 帧不进 history，每轮随请求发；llama n_ctx 16384 余量足 |
| 帧质量（模糊/黑屏） | 源项目有帧校验先例；proactive 判定自带"无值得说"过滤 |
| 双模式同时推帧（capture+摄像头） | 前端单选（capture or camera，非同时） |

## §5 执行顺序

1. **层 1 webinfer 视觉路径**（后端核心，可独立测）→ 2. **层 2 live_mode 帧缓冲+proactive** → 3. **层 3 前端接入** → QA 独立回归 → 真机验收（开 proactive 观察主动搭话质量；画面问答测试）。

## §6 关联

- `draft-live-interaction-layer.md`（C.B 定位 = §0 收敛终点）
- `draft-addressee-detection.md`（四态 not-for-me 已上线，proactive 判定复用）
- 源项目 `D:/AI/workspace/7-22/JoyAI-VL-Interaction-main/services/webinfer/live_adapter.py`（视频流助手 prompt/FORCE_SILENCE 参照）
- 前端 `screen_capture.js`（1fps 帧推送，复用）
