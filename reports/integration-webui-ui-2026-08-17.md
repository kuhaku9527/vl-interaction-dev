# WebUI UI 改造对接清单（前后端）

> 用途：跨对话交接件。前端对话产出 `design/joyai-redesign-preview.html`（v6-lite.9 视觉验证件）+ `doc/specs/webui-redesign-spec.md`（呈现决策 SSOT，<草案>）+ `doc/adr/0020-webui-local-cloud-selector.md`（本云 idiom 决策，<草案>）+ `reports/webui-reality-inventory-2026-08-17.md`（现实清单）；本文件给出 UI 元素 → 真实后端字段 + 服务端契约 的逐项映射，供后端对话按此实现 / 调整实装端 `services/webui/static/`。
>
> 仅做 UI ↔ 服务端 字段表（不重写 `unified-api-config-ui.md` 的契约条款；与该正式件冲突时以正式件为准）。

---

## 0. 摘要（TL;DR）

- **Idiom（决策 §A）**：所有 provider 槽位顶层一律 `本地 / 云端` Seg，云端三件套 = API 地址 + API Key + 模型；**不展开 provider 下拉**，用 `<datalist>` 给常用 endpoint 作建议。
- **DOM id 约定（约束 §B）**：前后端对接必须保留 DOM id（svc-<slot>-{provider|api-base|api-key|model}）+ `data-i18n` key，否则契约测试 `services/webui/tests/test_webui_*` 红线。

## 1. UI 元素 → 后端字段映射

> 表格列：UI 元素（视觉） | 真实后端字段（settings.json / 服务端契约） | 备注

### 1.1 模型 tab

| UI 元素 | 后端字段 | 备注 |
|---|---|---|
| 后端服务地址 input | `services.llm.api_base`（默认 `http://127.0.0.1:7060`） | 主/摘本地模式共享（llama.cpp 端口） |
| 主模型 本地/云端 Seg | `services.llm.mode = local\|cloud`（推断字段，新引入见注 1） | 每槽位独立 |
| 主模型.本地：模型名 input | `services.llm.local.model` | 走 llama.cpp |
| 主模型.云端：API 地址 input | `services.llm.cloud.api_base`（默认 `https://api.openai.com/v1`） | `<datalist>` 给 OpenAI / SiliconFlow / NVIDIA … |
| 主模型.云端：API Key | `services.llm.cloud.api_key` | 掩码；后端明文落盘 chmod 0600 |
| 主模型.云端：模型 input | `services.llm.cloud.model` | 自由填 |
| 摘要模型（结构同上） | `services.summary.{mode,local.model,cloud.{api_base,api_key,model}}` | SUMMARY_PRESETS 联动需后端支持（见注 2） |
| 上下文长度 input | `services.llm.context_length`（默认 `16384`） | 新约束下限 |
| 输出长度 input | `services.llm.max_tokens`（默认 `2048`） | 新引入 |

### 1.2 语音（TTS）tab

| UI 元素 | 后端字段 | 备注 |
|---|---|---|
| TTS 本地/云端 Seg | `services.tts.mode = local\|cloud` | |
| TTS.本地：环境 pill | `TTS_PROVIDER=local`（env） + `VOICE_CLONE_DIR` | env 自动探测（前端只读） |
| TTS.本地：本地服务端口 | `services.tts.local.port`（默认 `8985`） | |
| TTS.本地：语音克隆目录 | `services.tts.local.voice_dir`（默认 `D:\AI\voice-clone\voices`） | |
| TTS.本地：采样率 | `services.tts.local.sample_rate` | 选项 16000/24000 |
| TTS.云端：API 地址 | `services.tts.cloud.api_base`（默认 `https://api.minimaxi.com/v1/t2a_v2`） | |
| TTS.云端：API Key | `services.tts.cloud.api_key` | 掩码 |
| TTS.云端：Group ID | `services.tts.cloud.group_id` | Minimax 专属 |
| TTS.云端：Voice ID | `services.tts.cloud.voice_id` | datalist 建议 |
| TTS.云端：模型 | `services.tts.cloud.model`（如 `speech-2.8-hd`） | |
| TTS.云端：采样率 / 语速 / 音量 | `services.tts.cloud.{sample_rate,speed,volume}` | |

### 1.3 输入 & 唤醒（ASR）tab

| UI 元素 | 后端字段 | 备注 |
|---|---|---|
| ASR 本地/云端 Seg（v6-lite.9 新增） | `services.asr.mode = local\|cloud` | |
| ASR.本地：输入设备 | `services.asr.local.device` | getUserMedia deviceId（具体渲染由 `i18n_device_label.js` 处理） |
| ASR.本地：识别语言 | `services.asr.local.language` | zh/en/ja/auto |
| ASR.云端：API 地址 | `services.asr.cloud.api_base`（默认 OpenAI Whisper endpoint） | datalist 给 OpenAI / DashScope |
| ASR.云端：API Key | `services.asr.cloud.api_key` | 掩码 |
| ASR.云端：模型 | `services.asr.cloud.model`（如 `whisper-1`） | datalist 给 whisper-1/large-v3/paraformer-v2 |

### 1.4 记忆（Embedding）tab

| UI 元素 | 后端字段 | 备注 |
|---|---|---|
| 记忆服务地址 | `services.memory_store.api_base`（默认 `http://127.0.0.1:8997`） | |
| 嵌入本地/云端 Seg | `services.embedding.mode = local\|cloud` | |
| 嵌入.本地：环境 pill | `EMBEDDING_PROVIDER=local` | |
| 嵌入.本地：本地服务端口 | `services.embedding.local.port`（默认 `8997`） | |
| 嵌入.本地：模型 | `services.embedding.local.model`（默认 `BAAI/bge-m3`） | |
| **嵌入.云端：API 地址** | `services.embedding.cloud.api_base`（v6-lite.9 起**去掉 Provider 下拉**，直接 endpoint 表达 provider） | datalist 给 SiliconFlow / NVIDIA NIM / OpenAI / 火山方舟 |
| 嵌入.云端：API Key | `services.embedding.cloud.api_key` | 掩码 |
| 嵌入.云端：模型 | `services.embedding.cloud.model` | datalist 给 bge-m3 / bge-large-zh-v1.5 / bce-embedding-base_v1 |

### 1.5 视频采集（点击底部视频按钮弹出浮层，**前端交互层**，不写后端字段）

| UI 元素 | 后端字段 | 备注 |
|---|---|---|
| 摄像头/屏幕/RTSP Seg | `services.capture.source = webcam\|screen\|rtsp` | 由 `capture_webcam.js` / `screen_capture.js` / `capture_rtsp.js` 接管 |
| 摄像头：摄像头设备 | `getUserMedia` deviceId | i18n_device_label.js 处理命名 |
| 摄像头：分辨率 | `services.capture.webcam.resolution` | 1920×1080 / 1280×720 |
| 屏幕：FPS/处理间隔/每批帧数 | `services.capture.screen.{fps,interval_ms,batch_frames}` | 默认 1/1000/1 |
| RTSP：流地址 | `services.capture.rtsp.url` | |

> 注 3：视频采集浮层改的是 UI 触发方式（主视图平铺 → 点击弹出），底层接线（getUserMedia/RTSP/WebRTC）原样保留，影响最小。

## 2. 端点契约（与 `unified-api-config-ui.md` 对齐）

- 服务配置读：`GET /api/services/config` → 返回扁平 dict（如 `services.llm.cloud.api_key` 嵌套）。
- 服务配置写：`PUT /api/services/config` → 接收 `services.<slot>.<mode>.<api_base|api_key|model|...>`。
- 状态查询：`GET /api/services/status` → 返回 6 槽位健康态（含 latency / connected 标记），header `mode-chip` / 底部 mode chip 据此上色。
- 热重载：按 `unified-api-config-ui.md` 已定（保存后 `POST /api/services/reload`），本对接清单不重复规定。

## 3. ⚠️ 红线（影响 CI 验收）

1. **DOM id 必须保留**：`svc-<slot>-{provider|api-base|api-key|model}` —— 契约测试 `services/webui/tests/test_webui_*.py` 会查这些 id；UI 改动若改了 id 必须同步改测试。
2. **`data-i18n` key 必须保留**：`i18n_ui_string.test.js` 锁住所有 UI 文案 key；中文硬编码会破测试。
3. **视觉令牌必须用 `joy-*` 系列 + `--warning-color/--error-color`**：`styles.css` 与 `voice-ui.md §9`，不新造 `--ok/--warn` 等其它名（之前 consistency-spec 误引已修订）。
4. **图标必须用 lucide**：`index.html` 已引 lucide；新加图标用 `<i data-lucide="xxx"></i>`，禁止再写内联 SVG（一致性 spec D1）。
5. **不做切换时调 `window.confirm`**：预览 webview 屏蔽 confirm，必现「切换没反映」（v4-lite.2 实证）。

## 4. 未决项（给后端确认）

- 注 1：槽位 mode 字段（`services.<slot>.mode`）目前 settings.json 是否已存在？如不存在，请后端决定是新增字段还是沿用 `provider`。如沿用 provider，请告知用哪个字符串表达「本地」（如 `local` / `embedded`），以便 UI 命名对齐。
- 注 2：摘要模型的 SUMMARY_PRESETS 联动（如「主模型 = gpt-4o 时摘要模型默认 mini」）目前是后端逻辑还是配置约定？UI 改了 seg 后该行为是否保留？
- 注 3：视频采集浮层化的实装方式（保留 settings-section 折叠 vs 整体移到浮动层），请与既定折叠 section 风格一致，不要新造组件容器。

## 5. 待 ratification（端点不写 `决策/`，由审查组落）

- `doc/specs/webui-redesign-spec.md`（<草案> → 拟升 <正式>）
- `doc/adr/0020-webui-local-cloud-selector.md`（<草案> → 拟升 <正式>）
- `doc/specs/webui-component-consistency-spec.md`（<草案> → 拟升 <正式>）
- `doc/adr/0019-webui-component-consistency.md`（<草案> → 拟升 <正式>）

> 升级进 `决策/` 后，本对接清单才具硬约束效力；在 ratification 前，本清单仅作回灌参考。
