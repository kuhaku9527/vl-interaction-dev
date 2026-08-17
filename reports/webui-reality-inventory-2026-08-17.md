# JoyAI WebUI 真实现状清单（改造基线）

> **目的**：在把优化设计落地到真实前端之前，先把 `services/webui/` 的真实结构 / 接线 / 组件 / 复杂度盘清楚（**只读不改**），作为"改造"（保逻辑、换表现）的基线。本文档专门标注**预览样板 `design/joyai-redesign-preview.html` 与真实代码的冲突点**——这些冲突意味着样板不能直接接入，设计意图必须对齐现实后重写。
>
> **读取范围（本次）**：目录测绘（`services/webui/` 布局、`static/` 20 JS + 1 css + 1 html、后端 55 py、tests）+ 精读 `index.html` / `config_services.js` / `joy_ws.js` / `capture_*.js` / `screen_capture.js` / `i18n_device_label.js` + 抽样 `styles.css` / `status_poll.js`。

---

## 1. 真实架构总览（现实 R0）

| 维度 | 真实代码 | 预览样板 |
|---|---|---|
| 构建 | Vite + npm（`package.json`/`vite`/`vitest`/`eslint`），模块化工程 | 单文件 HTML，无构建 |
| 前端形态 | `index.html`（145KB SPA 外壳）挂载 **20 个职责化 JS 模块** | 单文件内联一切 |
| 样式 | `styles.css`（118KB）真实设计系统 | ~200 行自造 CSS |
| 后端 | 55 个 py（jarvis / live / asr / tts / vlm / kws / ws …） | 无 |
| 测试 | vitest(JS) + pytest(PY)，含大量 `*_contract` 契约测试 | 无 |
| 通信 | WebSocket `ws://host/ws?session_id=` + REST `/api/services/*` | 无 |

→ 样板是**脱离构建 / 测试 / 后端的设计稿**，不是可运行替代品。"替换"式接入 = 丢掉整套工程与契约。

---

## 2. 现实清单（R1–R12）

### R1 设置模态：可折叠 section，不是 8 tab
- **真实**：`settings-modal` 单页滚动，内含 `settings-section`（API Status / 无线电静默 / Layout / Visual Effects / Visual Style / WebRTC / Audio Output / Wake·ASR / Background Model / Debug / Network Proxy），标题走 `data-i18n`。
- **样板**：自造「模型 / 语音 / 输入&唤醒 / 记忆 / 知识库 / 高级 / 外观 / 关于」8 tab。
- **冲突**：8 tab **完全不存在**，是凭空结构。改造只能重排 / 美化现有 section，不能套 8 tab。

### R2 服务槽位模型（真实后端契约）
- **真实**：`config_services.js` 定义 `SERVICES=['llm','summary','tts','asr','agent','embedding']`，每槽 `provider/api_base/model/api_key`；端点 `GET/PUT /api/services/config`、`GET /api/services/status`、`POST /api/services/test`（N9 注释写明契约）。
- **样板**：模型 tab 自造「主模型 / 摘要模型 本地·云端 seg + 上下文长度 + 输出长度」。
- **冲突**：真实没有"主模型 / 摘要模型"二分，而是 6 个命名槽位；"本地 / 云端"在真实里是 `provider` 下拉值（如 embedding `local|siliconflow|nvidia`），**不是两段 seg**。样板须映射回 provider 模型。

### R3 "本云"表述已有实现
- **真实**：`summary` 有 `SUMMARY_PRESETS`（openrouter / minimax，切换自动填 api_base/model 并清空 api_key 防 401）——已存在 provider 联动逻辑。
- **样板**：自造 `.seg.seg-2` 本地 / 云端两段切换。
- **约束**：改造应**复用 / 扩展**现有 provider 下拉 + preset 机制，而非另造 seg 控件。

### R4 i18n 是硬约束
- **真实**：满屏 `data-i18n` / `data-i18n-title` / `data-i18n-aria`；`<html lang="en">`；`i18n_ui_string.test.js` 强制 UI 文案存在。
- **样板**：中文硬写，无 i18n 钩子。
- **约束**：任何文案改动必须走 `data-i18n` 机制，否则测试红 + 破坏多语言。

### R5 设计令牌 / 主题冲突
- **真实**：`styles.css` 用 `--bg-primary/secondary/tertiary`（暗：`joy-080707` 等；亮：`#fffafa` 等）+ `--warning-color` / `--error-color`，暗 / 亮双主题（`themeText`）。
- **样板 / spec**：自造 `--bg-elev` / `--brand` / `--text-2` / `--ok` / `--warn`。**连我写的 `webui-component-consistency-spec.md` 引用的 `--ok/--warn` 也对不上真实 `--warning-color/--error-color`**。
- **约束**：一致性 spec 的令牌名必须**改回真实令牌**；改造复用现有 token，不新造。

### R6 图标库：lucide，不是内联 SVG
- **真实**：`data-lucide="video|cast|monitor|play|square|camera"` 等 lucide 图标。
- **样板**：内联 SVG。
- **约束**：改造沿用 lucide，保持统一图标来源。

### R7 模式控件：radiogroup，不是 3 个按钮
- **真实**：`mode-group`(role=radiogroup) 含 `btListenBtn`(Jarvis 唤醒, role=radio) + `liveModeBtn`(Live 常驻, role=radio，互斥) + `live-proactive-toggle`(主动搭话) + `speechBtn`(语音识别)。
- **样板**：底部「语音 / 视频 / 实时」3 按钮 + header `mode-chip`(live/jarvis/kws)。
- **冲突**：真实 live / jarvis 是**单选 radio**；kws 是 Jarvis 唤醒的一部分（非独立）；无"视频"独立模式按钮（视频=采集，独立于交互模式）。样板的结构性简化不成立。

### R8 状态显示：轮询 + 语义徽章 + a11y
- **真实**：`status-badge`(`live-disconnected`/`llm-unknown`/`disconnected`) + `status-dot` + `api-status-item`(Main LLM / Summarizer / Embedding / Memory Store)，`status_poll.js` 每 1s 轮询 `/api/services/status`，带 `role=status aria-live=polite`。
- **样板**：`mode-chip` 静态点。
- **约束**：状态来自轮询后端，改造须保留数据驱动 + a11y 语义，不能静态画。

### R9 视频采集 UI 已存在且结构化
- **真实**：`videoSourceConfig` 可折叠面板内含 3 个 `capture-block`（Webcam / RTSP / Screen），各带 `cameraSelect` + play/stop 按钮(`webcamStartBtn` 等) + 状态 span + BETA 徽章 + lucide。底层 `capture_webcam.js`/`capture_rtsp.js`/`screen_capture.js` 已接 getUserMedia / RTSP / WebRTC。
- **样板**：把"视频采集"重做成 底部按钮 → 浮层 → seg.seg-3 选择块（v6-lite.4~.6）。
- **冲突**：真实采集 UI **本来就是分块、能用、有 start/stop 状态机**，并非"一切都展示出来不美观"的乱摊子。样板重构对象是个 strawman。改造应是**在原 capture-block 上美化 / 统一**（与 R5/R6 令牌 + 图标一致），而非换成浮层 seg。

### R10 WebSocket 是核心接线
- **真实**：`joy_ws.js` `connectWebSocket()` → `ws://host/ws?session_id=`(https→wss)，全应用状态 / 音频 / 视频帧经此通道；`ws_dispatcher.js` 分发。
- **样板**：无任何 WS。
- **约束**：改造 UI 不能破坏 WS 会话生命周期（session_id、重连）；所有实时数据来自 WS，不是 mock。

### R11 契约测试是改造红线
- **真实**：`test_capture_modules_contract.js`、`test_live_frontend_contract.py`、`test_webui_static_contract.py`、`test_webui_mode_radio_contract.py`、`test_ws_config_writeback.py`、`i18n_ui_string.test.js` 等——前端 DOM id / 类名 / 文案 / 通信契约被测试锁定。
- **约束**：改造若改了被测 DOM id / data-i18n / 端点，必须同步改测试，否则 CI 红（本项目铁律：CI 红 = 真实失败，禁绕过）。

### R12 后端接线细节待 phase-2 映射
- 待精确映射：`/api/services/*` 全契约、WS 消息协议（事件名 / 字段）、session 生命周期、各 capture 模块与后端 RTSP / WebRTC 信令。需在动手前补全，作为改造的"接口基线"。

---

## 3. 预览决策 → 现实 冲突汇总

| 预览决策 | 现实 | 结论 |
|---|---|---|
| 8 tab 设置 | 可折叠 section | 废，重排现有 section |
| 主 / 摘 本云 seg | 6 槽位 + provider 下拉 | 映射回 provider 模型 |
| header mode-chip(live/jarvis/kws) | radiogroup(Jarvis/Live)+proactive+speech | 对齐 radio 模型 |
| 视频浮层 + seg.seg-3 | capture-block 已结构化 | 在原块上美化 |
| 中文硬写 | data-i18n | 走 i18n |
| `--bg-elev/--ok/--warn` | `joy-*` + `--warning/--error-color` | 改回真实令牌 |
| 内联 SVG | lucide | 走 lucide |
| 单文件无构建 | Vite 模块化 + 测试 | 在模块内改，保构建 / 测试 |

---

## 4. 改造（保逻辑换表现）的红线

1. 不改 DOM id / data-i18n / 端点契约 → 同步更新对应 `*_contract` 测试。
2. 复用真实令牌（`joy-*`、`--warning/--error-color`）与 lucide，禁用样板自造令牌 / 内联 SVG。
3. 保留 WS 会话生命周期与 1s 状态轮询的数据驱动渲染。
4. 保留现有 provider 下拉 + `SUMMARY_PRESETS` 联动，不另造 seg 控件（除非经 R3 扩展评估）。
5. 所有文案经 `data-i18n`；中文只是其中一种 locale。

---

## 5. 下一步（phase 2，待你确认）

- **详细映射 R12** 后端契约（REST + WS 协议 + session 生命周期），补全"接口基线"。
- 基于本清单，把预览的**视觉意图**（暗色电影感、红色强调、统一卡片 / 状态语义）**翻译**成"在真实令牌 / 组件 / i18n / 契约之上"的具体改法，产出可落地的改造方案（而非样板）。
- 重新审视 `webui-redesign-spec.md` / `ADR-0020` / `webui-component-consistency-spec.md` 中被现实推翻的假设（尤其令牌名、8 tab、本云 seg），修订后再走 ratification。

---

*本清单为改造基线，未改动任何 `services/webui/` 代码。分支 `ui/redesign-preview`，本地备份 tag `archive/ui-redesign-preview-20260817`。*
