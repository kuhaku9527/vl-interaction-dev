# 现行运行拓扑（Runtime Topology — 唯一现行权威）

> **生命周期**: **稳态型**（类型 A）—— 端口/服务拓扑变更时**必须**同步本文件。
> **建立**: 2026-09-14 ｜ **基线**: HEAD `3a282ef`（2026-08-18）
> **权威性**: 本文件是**当前**拓扑的唯一入口。历史快照见 `local/architecture-current.md`（2026-07-14）与 `local/architecture-local.md`（2026-07-12）——**两者均已过时，不要据此实施**。
> **自检**: `python scripts/doc_health.py --check link` 应无本文件的死链。

---

## 1. 服务与端口（实测真值）

来源：`services/scripts/run-windows.ps1` 的 `$P` 哈希表与 `$PortMap`。

| 服务 | 默认端口 | 环境变量覆盖 | 默认启动? |
|---|---|---|---|
| `llama-main`（VLM + LLM 主模型） | **7060** | `MAIN_MODEL_PORT` | ✅ |
| `webinfer`（推理编排 / LLM 网关单入口） | **8070** | `ADAPTER_PORT` | ✅ |
| `background-agent`（委派后端，默认 provider=codex） | **8079** | `CODEX_API_PORT` | ✅ |
| `webui`（浏览器交互界面） | **8099** | `WEBUI_PORT` | ✅ |
| `voice-clone`（TTS / 声音克隆） | **8985** | `VOICE_CLONE_PORT` | 仅 `voice`/`default` 档 |
| `memory-store`（持久化 + Local Wiki） | **8997** | `MEMORY_PORT` | ✅ 默认 ON |
| `hermes-gateway` | 8642 | `HERMES_GATEWAY_PORT` | ❌ **从不启动** |
| `asr` 模型（whisper.cpp） | 8993 | `ASR_MODEL_PORT` | ❌ **从不启动** |
| `asr-adapter` | 8994 | `ASR_ADAPTER_PORT` | ❌ **从不启动** |

### ⚠️ 三个"幽灵服务"（在端口表里但永不启动）

`hermes-gateway` / `asr-model` / `asr-adapter` 出现在 `$PortMap` 与启动顺序数组里，**但不在任何 plan 中**，被 `if (-not $plan[$name]) { continue }` 跳过。

其中 **`whisper` 的启动函数 `Start-Whisper` 从未定义**：`run-windows.ps1` 的 `$map` 里映射到 `"Start-Whisper"`，但全文无该函数定义 —— 即 `-Restart whisper` 会调用 `$null`。**这是一个真实缺陷（非文档问题），待修。**

---

## 2. 启动模式（4 档，`ValidateSet`）

来源：`services/scripts/run-windows.ps1` 与 `start-joyai.ps1`。

```
-Mode default | minimal | voice | gaming
```

| 档 | 启动的服务 |
|---|---|
| `minimal` | llama-main + webinfer + webui + **background-agent** |
| `voice` | llama-main + voice-clone + webinfer + webui + background-agent |
| `default` | 同 `voice` |
| `gaming` | 同 `voice`，另设 `FORCE_SILENCE_BEFORE_QUERY=false`、`LOG_LEVEL=WARNING` |

**memory-store 的追加规则**：`if ($env:JOYAI_ENABLE_MEMORY_STORE -ne "0") { $plan["memory-store"] = $true }`
→ **默认 ON，opt-OUT**（设 `JOYAI_ENABLE_MEMORY_STORE=0` 才关）。

> ❗ **常见混淆（已致错）**：`jarvis` **不是启动模式**，它是**交互模式**（见 §4）。
> `doc/subsystems/gaming-mode.md` 曾教用户跑 `-Mode jarvis`，该命令会直接报错。

**启动顺序**：llama-main → whisper → voice-clone → hermes-gateway → background-agent → webinfer → asr-adapter → webui → memory-store。
每服务 `Wait-Http` 就绪（超时 900s），**任一失败即 `Stop-All` 回滚**。启动前跑 drift-gate 静态门禁，非零退出则中止启动。

---

## 3. 交互模式（3 种，默认 `live`）

来源：`services/webinfer/frame_parsing.py` 的 `_VALID_INTERACTION_MODES` 与 `_normalize_interaction_mode`。

| 模式 | 决策 token | 说明 |
|---|---|---|
| **`live`**（**默认**） | **四态**：`silence` / `response` / `delegation` / `not-for-me` | 常驻监听，完整决策框架 |
| `jarvis` | **三态**：`silence` / `response` / `delegation` | 唤醒词触发；prompt 保持 byte-for-byte 三态 |
| `call` | **零态**（无 decision token） | 纯语音转文字 → 聊天框，等同打字 |

- 空值或缺省 → `live`；未知值 → 记 warning 后回退 `live`。
- 第 4 态 `not-for-me` 由 addressee-detection Phase 2 引入（2026-08-12）。
- 定义处：`services/webinfer/response_format.py`（decision 枚举 + `parse_model_decision`）。

> ❗ **口径澄清**：「三态 vs 四态」两种说法各对一半 —— **live=四态、jarvis=三态、call=零态**。
> `决策/交互模式与决策token规范.md` 的 D-2026-08-03-001 写「live 保留完整三判断」，是四态上线前的表述，**尚未更新**。

---

## 4. LLM 路由（单入口）

**所有 LLM 调用必须经 `webinfer` :8070**。webui **不持有**指向 :7060 的直连。

- 依据：ADR-0006（`doc/adr/0006-llm-gateway-single-entrypoint.md`）。
- 代码实证：`jarvis_config.py` 的 `llm_api_url` 默认 `http://127.0.0.1:8070/v1`。
- 失败语义：webinfer 不可用时**显式失败，不回退**到 :7060 直连。

> ❗ `doc/main/00-main-direction.md` 曾写「Jarvis 文字/语音对话在 WebUI 内直连 7060」——**与 ADR-0006 直接矛盾**，已作历史表述处理。

---

## 5. 前端结构

`services/webui/src/joy_interaction_webui/static/`：**21 个外部 JS 模块** + `index.html`（4 段内联 `<script>`）+ `styles.css`。

**加载顺序**（`index.html`）：
1. CDN：lucide / katex / marked / dompurify（均带 SRI + 版本钉死）
2. **`joy_state.js`**（最先加载的应用脚本 —— 跨模块状态中心 `window.JoyState`）
3. `screen_capture.js` → `capture_webcam.js` → `capture_rtsp.js`
4. `render_markdown.js` → `sanitize_static_html.js`
5. `config_services.js` → `radio_silence.js` → `wiki_frontend.js`
6. `joy_ws.js` → `i18n_device_label.js`
7. （内联）→ `vlm_history.js` → `llm_reply_ui.js` → `ws_dispatcher.js`
8. （内联）→ `vlm_render.js` → `background_rich.js` → `tts_player.js`
9. `speech_input.js` → `live_ui.js` → `llm_reply_audio.js` → `status_poll.js`

> ❗ `doc/frontend/README.md` 称前端是「单文件原生 JS SPA，逻辑集中在两个内联脚本块（约 6000 行）」——**已严重过时**。模块化拆分（`window.JoyXxx` 命名空间模式）是 2026-08 的主要成果。

---

## 6. 本文件与历史文档的关系

| 文档 | 状态 | 处置 |
|---|---|---|
| **本文件** | ✅ **现行权威** | 拓扑变更时更新 |
| `local/architecture-current.md` | ❌ 过时（2026-07-14 快照，含幽灵端口 8088） | 已标注历史；**勿据此实施** |
| `local/architecture-local.md` | ❌ 过时（2026-07-12，正文仍是 11 进程 + CosyVoice 8991） | 已标注历史 |
| `local/pm-local.md`、`local/tech-local.md` | ❌ 过时（基于 CosyVoice/whisper 时代） | 待归档 |
| `runtime-matrix.md` | ✅ 有效但**讲的是 venv 矩阵，非拓扑** | 与本文件互补 |
| `service-startup.md` | ✅ 准确（端口/venv/env 实测吻合） | 互补：讲启动细节与坑 |
| `ARCHITECTURE.md` §3 | ✅ 端口表基本准确 | 指针式镜像，回指本文件 |
