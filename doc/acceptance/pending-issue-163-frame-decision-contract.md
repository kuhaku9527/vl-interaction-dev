# 待开 issue（工单 #163 判定出的缺陷，需用户确认后才提交）

> ⚠️ **本文件已被线上工单 #168 取代**（提交于 2026-09-22）。下面的正文是**提交时的初稿**，
> 其中「诊断串显示给用户」的表述**已被实测推翻**（详见 `doc/standards/test-baseline.md`
> §1 #163 轮的「★ 可见面实测」）。**以 #168 的线上正文为准。**
> 保留本文件仅作离线追溯，避免 `logs/`（gitignored）那种跨机器死链。
>
> 状态：**已写好、已提交为 #168**。`issue_open` 被 auto-mode 审核拦下（对外可见、难以撤销，
> 且 `/implement #163` 未授权在另一仓库开单）。等用户确认后再提交。
>
> **本文件位置的理由**：`#163` 的最后一条 AC 是「若判定为缺陷 → 结论写入台账并**另立工单**」。
> 提交动作被拦，故把**待提交的正文**落在可入库处（`doc/acceptance/`），
> 而不是 `logs/`（被 gitignore，跨机器即失效）。提交后本文件可退役。
> 关联证据：`doc/standards/test-baseline.md` §1「2026-09-22（★ 帧链路…；工单 #163）」。

**repo**：`kuhaku9527/vl-interaction-dev`
**labels**：`ready-for-agent`, `bug`

## 标题

帧链路：webui 不消费 webinfer 的 decision 契约 —— 四态沉默被显示成内部诊断串「Empty model response: stop」

## 正文

### 来源

由工单 #163（帧链路真机判定 + 建正常基线）判定的**缺陷**。按 spec
`doc/specs/draft-test-evidence-baseline.md` §2「不改被测对象」，#163 **只判定不修**，
故另立本票。

证据台账：`doc/standards/test-baseline.md` §1「2026-09-22（★ 帧链路：判定「内容空」+ 建立正常基线；工单 #163）」。

### 现象

帧链路（屏幕/摄像头 → WS `frame` → webinfer → VLM）在**无 prompt** 时，
**webui 的 VLM 服务层**产出内部诊断串（⚠️ 早先写作「用户可见面收到」，**该措辞已更正** ——
诊断串在当前 HEAD 上到不了用户可见面，见下文「可见面实测」）：

```
text = "Empty model response: stop"
metrics.user_prompt = ""
```

而链路本身**完全正常**：`api_call_ms=420.87`、`total_inferences` 递增、HTTP 200。

### 判定：**读侧缺陷**（不是预期行为）

三条判据（均为 2026-09-22 真机实测）：

| # | 判据 | 读数 |
|---|---|---|
| 1 | 模型**真的产出**了内容 | 同一张真机帧、image-only 直连 7060 → `"这张图片展示的是一个视频采集或屏幕捕获工具的界面…"`（64 token，`finish_reason=length`） |
| 2 | webinfer 的**决策契约**也正常 | image-only → HTTP 200，`streamingharness.decision="silence"`，`usage.completion_tokens=2` |
| 3 | **frame 路径无人消费该契约** | `vlm_service.py` 全文**无** `streamingharness` / `decision` 字样；而 live/jarvis 三条路径**都读** `harness.get("decision")` |

### 根因（机制级）

`ws_handler.py` 的 `frame` 分支 → `svc.process_frame` → `analyze_image`，后者只看
`choices[0].message.content`，**不读** `streamingharness.decision`。

而 webinfer 把四态控制标记（`</silence>` / `</response>` / `</not-for-me>`）从 `content`
里**剥掉**，只留在 `streamingharness.raw_content`（`response_format.py::strip_decision_tokens`
的设计如此，见 `test_text_chat_endpoint.py:211` 的断言）——于是「模型按契约选择沉默」
这件事到达 webui 时**只剩一个空字符串**，`_extract_response_text` 便回落到
`f"Empty model response{': ' + finish_reason}"`（`vlm_service.py:629`）。

⇒ **决策语义在网络层丢失**，用户看到的是内部诊断文案。

**对照（证明这是 frame 路径独有）**：`live_llm.py:365`、`live_proactive.py:297`、
`jarvis_mode.py:1610` 三处都写 `decision = harness.get("decision") or (...)`，
**只有帧路径漏了**。

### 决策态依据

* `doc/subsystems/screen-capture.md` §3.5.5：「视频框实时显示游戏画面，**同时** BT-7274
  看到同一路画面，玩家可以直接喊『bt，这个怪怎么打』→ **BT 回复攻略**」
* 同上 §4.3：「1 fps 视频帧 → VLM 识别 → BT-7274『这个螳螂帮，先用赛博精神病秒掉…』」
* `doc/specs/live-visual-cb.md` §1：「用户说话时，最近 1-N 帧作为视觉输入一起送 LLM →
  模型**看着画面回答**」

三处承诺的都是**有内容**的作答；**没有任何一处**把「无 prompt 时回一句内部诊断串」
写成预期行为。

### 建议修法（请在实现前确认，本票不改被测对象外的范围）

把 frame 路径接到与 live/jarvis **同一条**决策读取约定上，而不是各写各的：

1. `VLMService.analyze_image` 保留 webinfer 的 `streamingharness`（至少 `decision` /
   `raw_content`），或让 `process_frame` 走一条能拿到 harness 的调用；
2. `get_current_response()` 的消费者（`ws_handler.py` / `ws_notify.get_session_callback`）
   按 decision 分派：`silence` / `not-for-me` → **不显示**（与前端
   `vlm_render.js::getVlmDisplayText` 对 `</silence>` 的处理一致）；`response` → 显示正文；
3. **`Empty model response: stop` 不得出现在用户可见面** —— 它是诊断串。空内容要么被
   当作 silence 静默，要么作为**可观测事件**落盘（`emit_event`），而不是当成回复文本。

> ⚠️ 修法 1–3 是**建议**，实现者应先确认哪条与 `决策/交互模式与决策token规范.md`
> 的四态契约一致；本票只锁定「当前行为是缺陷」这一结论。

### 验收

- [ ] 帧链路**无 prompt** 时，用户可见面**不再出现** `Empty model response`
- [ ] 无法判定的空内容有**可观测**去处（事件 / 状态），不静默吞掉
- [ ] 真机复跑 `node scripts/frame_link_probe.mjs --reset-session`：
      该轮判据从「FAIL（占位串）」变为 **PASS 或明确的 silence 语义**（判据本身可能要随修法调整）
- [ ] 正常基线**不回归**：`--prompt "…" --require-content` 仍 PASS 且文本有意义
      （基线值：`api_call_ms` 0.42–0.95 s，返回一句与画面一致的中文描述）
- [ ] ★ 负控：停 8070 → 仍判 FAIL（不得因本次改动变成静默通过）

### ★ 可见面实测（2026-09-22 补做；**本节推翻了本文件初稿的严重度**）

装置：`logs/frame-link/dom_visibility_probe.mjs`（WS 通道与 DOM 通道**分开**记录）。

| 实验 | 装置 | 读数 |
|---|---|---|
| **A（真机）** | 真实 `getDisplayMedia` 1 fps 采集，50 帧 | **后端发了 39 条**诊断串；用户可见面 50/50 次采样**全部不可见**；`#resultText` 长度**恒 `281`**（DOM 一字符未动） |
| **C（受控正控）** | 同装置，**唯一改动** `isAnalysisRunning = true` | `body_has_target: false → true`、`#resultText 343 → 370`；还原后恢复 `false` |
| **G（内部状态）** | 干净页 + 32 条响应（30 条为诊断串） | `lastText` 长度**恒 `[0]`**、`vlmHistory` **从未新增条目** |

机制：`ws_dispatcher.js:24` 是 `vlm_response` 唯一分派点，首句即 `if (!isAnalysisRunning) return;`；
`updateResultText` 只被 `ws_dispatcher.js:43` 调用（在守卫**之后**）；
`isAnalysisRunning` 唯一置位点 `app_main.js:1192` 位于 `showProcessedVideoStream`，
而该函数**零调用点**（静态 grep + 运行时 call-trap `calls: 0`）⇒ **结构性恒 `false`**。

⇒ **本票是「死路径上的 latent 缺陷」，不是当前可见缺陷。** 触发条件（已实测）：
一旦该函数被接回或新增置位路径，诊断串会立刻可见**并被 TTS 念出**（`#ttsSpeakingText`）。

> **已作废的证据**：初稿把 `getVlmDisplayText("Empty model response: stop")` 原样返回
> 当作「诊断串漏到用户可见面」的决定性证据 —— **不成立**（那是**跳过守卫**的函数级注入，
> 与真实路径不同构）。实测反证：30 条真实诊断串到达时 `lastText` 恒 `''`。

### 装置

`scripts/frame_link_probe.mjs`（#163 新建，可复用）。**必须用应用自身 socket**
（`window.websocket`）+ **真实帧**；自建 WebSocket 收不到 `vlm_response`（已实测）。
