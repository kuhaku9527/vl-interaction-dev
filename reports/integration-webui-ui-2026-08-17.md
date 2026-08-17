# WebUI UI 改造对接清单（前后端）

> 用途：跨对话交接件。前端对话产出 `design/joyai-redesign-preview.html`（v6-lite.10 视觉验证件）+ `doc/specs/webui-redesign-spec.md`（呈现决策 SSOT，<草案>）+ `doc/adr/0020-webui-local-cloud-selector.md`（本云 idiom 决策，<草案>）+ `reports/webui-reality-inventory-2026-08-17.md`（现实清单）；本文件给出 UI 元素 → 真实后端字段 + 服务端契约 的逐项映射，供后端对话按此实现 / 调整实装端 `services/webui/src/joy_interaction_webui/static/`。
>
> 仅做 UI ↔ 服务端 字段表（不重写 `unified-api-config-ui.md` 的契约条款；与该正式件冲突时以正式件为准）。
>
> ⚠️ **v6-lite.11 校准（重要）**：本清单曾误写 `services.<slot>.cloud.*` / `.local.*` / `.mode` 子结构——**这些在真实后端不存在**。真实后端契约是**扁平**的（见 §1）。本版已整体校准，并新增 §4「落地策略」说明 redesign 可**纯前端实现、零后端改动**。
> ⚠️ **v6-lite.10 增量**（仍有效）：Provider 控件（自命名 + +保存整套 + 历史下拉）已加入 main / summary / asr / embedding 四个云端 subform 顶部；见 §2.0。

---

## 0. 摘要（TL;DR）

- **Idiom（决策 §A）**：所有 provider 槽位顶层一律 `本地 / 云端` Seg；云端 subform 顶部一律 = **Provider 命名预设控件**（自命名 + +保存整套 + 历史下拉）+ 三件套（API 地址 + API Key + 模型）。endpoint 配 `<datalist>` 建议。
- **关键修正（v6-lite.11）**：此前写的 `services.<slot>.cloud.*` / `.local.*` / `.mode` **不存在于真实后端**。真实后端是**扁平**契约（§1）。`本地/云端` 是 **UI 派生**（非后端字段）；Provider 预设是 **前端 localStorage**（§2.0 + §4）。
- **落地策略（§4）**：默认**纯前端实现，不动后端契约**——本地＝清空/填本地默认 `api_base`（后端本就「空 api_base=用默认/本地」），云端＝填 cloud `api_base`；Provider 预设 apply 时调**现有** `PUT /api/services/config` 落盘。无需新增端点。
- **DOM id 约定（约束 §3）**：前后端对接必须保留 DOM id（`svc-<slot>-{api-base|model|api-key|provider|test-*}`）+ `data-i18n` key，否则契约测试红线。
- **真实 token（§3 红线 3）**：`--border-color` / `--warning-color` / `--error-color` / `--joy-red`（品牌红）。预览 demo 自造的 `--ok/--brand/--bg-elev-2/--text-3` 仅 demo 局部变量，实装时必须重映射到上述真实 token。

## 1. 真实后端契约（as-built，已确认 — 校准基准）

> 来源：`doc/specs/unified-api-config-ui.md`（<正式>，retro 核验 2026-08-08，PR #118/#122/#124）+ `services/webui/src/joy_interaction_webui/static/config_services.js` + `index.html` Services 面板。

- **配置字典**（webui 单一真源 `_services_config`），slot：`llm` / `summary` / `tts` / `asr` / `agent` / `embedding`（**共 6 个**，redesign 预览当前漏了 `agent`，见 §5 注 8）。
- **每 slot 字段（扁平，无嵌套）**：
  - `llm`：`{api_base, model, api_key}`
  - `summary`：`{provider, api_base, model, api_key}`（provider 选项 openrouter / minimax）
  - `tts`：`{api_base}`（**当前只读 api_base**，无 model/api_key/provider — 见 §2.3 注 6）
  - `asr`：`{api_base, model, api_key}`（**本地-only 现状**，云端 ASR 不在 unified 范围 — 见 §2.4 注 7）
  - `agent`：`{provider, api_base, api_key}`（provider 选项 codex / hermes）
  - `embedding`：`{provider, api_base, model, api_key}`（provider 选项 siliconflow / local / nvidia）
- **端点契约**：
  - `GET /api/services/config` → 返回上述扁平 dict 快照。
  - `PUT /api/services/config` → 接收增量合并，触发 `_propagate_services_to_runtime()` 热重载 + `_log_config_change()` 审计（`api_key` 脱敏为 `***set***`/`***cleared***`，ADR-0014）；无效配置返回 `400`/`422` 结构化报错，**无静默 fallback**（D-080）。
  - `GET /api/services/status` → `{slot:{ok,reason}}`，前端据此上色 badge。
  - 持久化：`config/services.json` 原子写（tmp+`os.replace`+fsync，`chmod 0600`，gitignored，#122/#125）。
- **真实 DOM id**（`index.html`，契约测试锁）：`svc-<slot>-{api-base,model,api-key,provider,test-btn,test-label,test-status}`。
  - `provider` 仅 summary / agent / embedding 有 `<select>`；llm / tts / asr **无** provider 控件。
- **真实 token**（`styles.css`）：`--border-color` / `--warning-color` / `--error-color` / `--joy-red`（品牌红）/ `--joy-FFA726` 等调色板。
- **memory-store 是另一直面**：memory-store 走 `/v1/settings/network` + `/v1/settings/embedding`（#124/#129），**与 `embedding` 槽位（svc-embedding-*）是两条线**。redesign 的「记忆(Embedding) tab」= `embedding` 槽位 UI；不动 memory-store 端点（除非另排期 embedding 运行时重载，已存在）。

## 2. UI 元素 → 后端字段映射（校准后）

> 表格列：UI 元素（视觉） | 真实后端字段 / 落地方式 | 备注

> **实装状态（v6-lite.12，已落地）**：本 §2 全部 UI 元素已落到真实 `services/webui/src/joy_interaction_webui/static/`（`styles.css` + `index.html` + `config_services.js#wireSegProvider`），**零后端改动**；契约测试全绿。6 槽位本云 Seg 全到位；Provider 控件落 main/summary/asr/agent/embedding（tts 按 spec 仅 seg，Provider 化留下轮）；实装分支 `ui/redesign-preview`。

### 2.0 Provider 命名预设控件（v6-lite.10，每个云端 subform 顶部 — 前端 localStorage）

| UI 元素 | 后端字段 / 落地方式 | 备注 |
|---|---|---|
| 「提供商」 label | — | 视觉标签 |
| 名称 input（`{slot}-provider-name`） | 用户自命名 alias 字符串 | 也可从 `{slot}-providers` datalist 自动补全 |
| **+ 保存整套** 按钮 | 写入 `localStorage[joyai.providers.<slot>]`，存 `{name, api_base, model}` | **api_key 不入 localStorage**（仅后端落盘）；校验 name/api_base/model 非空 |
| 历史下拉（`{slot}-provider-pick`） | 读自 `localStorage` 列表 + select，回到某 alias | |
| datalist（`{slot}-providers`） | 读自 `localStorage[joyai.providers.<slot>]` 的 name 列表 | |
| 「删除该名字」 按钮 | `localStorage` 删除当前 name | |
| 反馈 msg（`{slot}-provider-msg`） | — | 成功绿字 / 失败红字，2.2s 自动清除 |

> **套用（apply）行为（关键）**：用户选中/填入某预设后点「套用」→ 把该预设的 `api_base` / `model` 填进 `svc-<slot>-{api-base,model}`（若该槽位有 `provider` 则把 name 填进 `svc-<slot>-provider`），然后调**现有** `PUT /api/services/config` 落盘。**不新增后端端点。**
> **后端契约建议（可选增强，非 v6-lite 必需）**：若要做跨设备 Provider 同步，后端可加 `providers[]` 数组 + `GET/POST/DELETE /api/providers/<slot>`；但前端 localStorage 已满足用户「以名字存整套 + 下拉选回」诉求，默认不强制。

### 2.1 主模型（= `llm` slot）

| UI 元素 | 真实后端字段 / 落地方式 | 备注 |
|---|---|---|
| 后端服务地址 input | `svc-llm-api-base` → `services.llm.api_base`（占位 `http://127.0.0.1:8070/v1`，本地 llama.cpp） | |
| 主模型 本地/云端 Seg | **UI 派生（无 mode 字段）**：本地＝清空/填本地默认 `api_base`（后端「空 api_base=用默认/本地」），隐藏 api_key；云端＝显示三件套 | |
| 主模型.本地：模型名 input | `svc-llm-model` → `services.llm.model` | 走 llama.cpp |
| 主模型.云端：三件套 | `svc-llm-{api-base,api-key,model}` → `services.llm.{api_base,api_key,model}` | `<datalist>` 给 OpenAI / SiliconFlow / NVIDIA … |
| 主模型.云端：Provider 控件 | 见 §2.0（localStorage `joyai.providers.main`） | |

### 2.2 摘要模型（= `summary` slot，真实有 provider `<select>`）

| UI 元素 | 真实后端字段 / 落地方式 | 备注 |
|---|---|---|
| 摘要 本地/云端 Seg | UI 派生（同 §2.1） | |
| 摘要.云端：Provider 控件 | 见 §2.0（localStorage `joyai.providers.summary`） | **替换**真实静态 `<select>` openrouter/minimax |
| 摘要.本地：模型名 | `svc-summary-model` → `services.summary.model` | |
| 摘要.云端：三件套 | `svc-summary-{api-base,api-key,model}` → `services.summary.{api_base,api_key,model}` | |
| SUMMARY_PRESETS 联动 | 真实 `wireSummaryProvider()`：切 provider 自动填推荐 api_base/model + **清空 api_key**（防跨 provider key 误用 401） | redesign 的 Provider 控件**接管**此行为：apply preset 即填 + 清 key |

### 2.3 语音（TTS，= `tts` slot，真实当前仅 `api_base`）

| UI 元素 | 真实后端字段 / 落地方式 | 备注 |
|---|---|---|
| TTS 本地/云端 Seg | UI 派生：本地＝`JARVIS_TTS_API_URL` 经 `JarvisConfig.from_env()` 读（unified spec）；云端＝填 `api_base` | |
| TTS.云端：API 地址 | `svc-tts-api-base` → `services.tts.api_base`（占位 `http://127.0.0.1:8985/v1/synthesize`） | 真实已有 |
| TTS.云端：Group ID / Voice ID / 采样率 / 语速 / 模型 | **当前后端 tts 槽位无这些字段** | ⚠️ 注 6：TTS 完整参数化是 redesign 引入的新需求，需后端确认是否扩展 `tts` 字段 |

### 2.4 输入 & 唤醒（ASR，= `asr` slot，真实本地-only 现状）

| UI 元素 | 真实后端字段 / 落地方式 | 备注 |
|---|---|---|
| ASR 本地/云端 Seg（v6-lite.9 新增） | UI 派生 | |
| ASR.本地：输入设备 / 识别语言 | `getUserMedia` deviceId（`i18n_device_label.js` 处理）+ `language`（zh/en/ja/auto） | 真实本地 ASR（whisper.cpp/FunASR/Qwen3-ASR :8993） |
| ASR.云端：三件套 | `svc-asr-{api-base,api-key,model}` → `services.asr.{api_base,api_key,model}` | ⚠️ 注 7：**云端 ASR 是前瞻 UI**（unified spec §2 负面约束 4 明确「ASR 云 provider 需另行实现 provider 层，不在本 spec 范围」）。真实落地云端 ASR 需后端先实现 provider 层 |
| ASR.云端：Provider 控件 | 见 §2.0（localStorage `joyai.providers.asr`） | 同上，前瞻 |

### 2.5 记忆（Embedding，= `embedding` slot，真实有 provider `<select>`）

| UI 元素 | 真实后端字段 / 落地方式 | 备注 |
|---|---|---|
| 嵌入 本地/云端 Seg | UI 派生：本地＝`EMBEDDING_PROVIDER=local`（D-033 默认 local 不变）；云端＝填 cloud `api_base` | |
| 嵌入.云端：Provider 控件 | 见 §2.0（localStorage `joyai.providers.emb`；v6-lite.9 误删已补回） | **替换**真实静态 `<select>` siliconflow/local/nvidia |
| 嵌入.本地：模型 | `svc-embedding-model` → `services.embedding.model`（默认 `BAAI/bge-m3`） | |
| 嵌入.云端：三件套 | `svc-embedding-{api-base,api-key,model}` → `services.embedding.{api_base,api_key,model}` | `<datalist>` 给 SiliconFlow / NVIDIA NIM / OpenAI / 火山方舟 |
| memory-store 端点 | **不在此 tab 范围**（走 `/v1/settings/network` + `/v1/settings/embedding`） | 见 §1 末 |

### 2.6 视频采集（点击底部视频按钮弹出浮层 — 前端交互层，不改后端接线）

| UI 元素 | 真实后端字段 / 落地方式 | 备注 |
|---|---|---|
| 摄像头/屏幕/RTSP Seg | 由 `capture_webcam.js` / `screen_capture.js` / `capture_rtsp.js` 接管 | |
| 摄像头：设备 / 分辨率 | `getUserMedia` deviceId + resolution | `i18n_device_label.js` 处理命名 |
| 屏幕：FPS/间隔/批帧 | `services.capture.screen.{fps,interval_ms,batch_frames}` | 默认 1/1000/1 |
| RTSP：流地址 | `services.capture.rtsp.url` | |

> 注 3：视频采集浮层改的是 UI 触发方式（主视图平铺 → 点击弹出），底层接线原样保留，影响最小。

## 3. ⚠️ 红线（影响 CI 验收）

1. **DOM id 必须保留**：`svc-<slot>-{api-base|model|api-key|provider|test-btn|test-label|test-status}` —— 契约测试 `services/webui/tests/test_webui_*.py` / `config_services.test.js` 会查这些 id；UI 改动若改了 id 必须同步改测试。
2. **`data-i18n` key 必须保留**：`i18n_ui_string.test.js` 锁住所有 UI 文案 key；中文硬编码会破测试。
3. **视觉令牌必须用真实 token**：`--border-color` / `--warning-color` / `--error-color` / `--joy-red`（品牌红）；**禁止新造 `--ok/--brand/--bg-elev-2/--text-3` 等**（预览 demo 里的这几个是局部演示变量，实装时必须重映射到真实 token，不得带入 `styles.css`）。
4. **图标必须用 lucide**：`<i data-lucide="xxx"></i>`，禁止内联 SVG（一致性 spec D1）。
5. **切换禁用 `window.confirm`**：预览 webview 屏蔽 confirm，必现「切换没反映」（v4-lite.2 实证）。

## 4. 落地策略（关键 — redesign 可纯前端实现）

- **策略 A（推荐，默认）**：**不改后端契约**。
  - `本地/云端` Seg = UI 派生：本地 → 清空/填本地默认 `api_base`（后端「空 api_base=用默认/本地」语义，unified spec §2 局限②），隐藏 api_key；云端 → 显示三件套。
  - Provider 命名预设 = 前端 `localStorage[joyai.providers.<slot>]`（存 `{name,api_base,model}`，无 api_key）；apply 时填 `svc-<slot>-{api-base,model}`(+provider) 后调**现有** `PUT /api/services/config`。
  - 后端零改动即可上线 redesign 表现层。
- **策略 B（可选增强，非 v6-lite 必需）**：跨设备 Provider 同步 —— 后端加 `providers[]` 数组 + `GET/POST/DELETE /api/providers/<slot>`。仅在用户要求跨设备时才做。

## 5. 未决项（给后端确认）

- 注 1（**已由策略 A 消解**）：`mode` 字段不需要 —— `本地/云端` 派生自 `api_base`，不新增后端字段。
- 注 2（**已确认保留**）：SUMMARY_PRESETS 联动由 Provider 控件接管（apply 即填 + 清 key）。
- 注 3：视频采集浮层化实装方式（保留 settings-section 折叠 vs 移到浮动层），请与既定折叠 section 风格一致。
- 注 4（**已由策略 A 消解**）：Provider 池前端 localStorage，不需后端持久化（除非选策略 B）。
- 注 5（**已消解**）：无激活态，仅「套用」回填。
- **注 6（新）**：TTS 完整参数（Group ID / Voice ID / 采样率 / 语速 / 模型）当前后端 `tts` 槽位仅有 `api_base` —— 是否扩展 `tts` 字段？需后端确认排期。
- **注 7（新）**：ASR 云端是前瞻 UI；真实云端 ASR 需后端先实现 provider 层（unified spec §2 负面约束 4 明确排除）—— 是否排期？
- **注 8（新）**：redesign 预览漏了 `agent` 槽位（真实有 `svc-agent-*` codex/hermes）—— 是否纳入 redesign 范围？

## 6. 待 ratification（端点不写 `决策/`，由审查组落）

- `doc/specs/webui-redesign-spec.md`（<草案> → 拟升 <正式>）
- `doc/adr/0020-webui-local-cloud-selector.md`（<草案> → 拟升 <正式>）
- `doc/specs/webui-component-consistency-spec.md`（<草案> → 拟升 <正式>）
- `doc/adr/0019-webui-component-consistency.md`（<草案> → 拟升 <正式>）

> 升级进 `决策/` 后，本对接清单才具硬约束效力；在 ratification 前，本清单仅作回灌参考。

---

### 变更日志（本对接清单自身）

- **v6-lite.14（2026-08-16）** — 轴 2 + 轴 7 实装落地真实 `static/`：① 轴 2 纯 CSS 8px 栅格规范化（`.settings-section-title`/`.settings-item`/`.form-group`/`.panel-header` 间距 12/14→16px，`.settings-close` 圆角 4→6px 与表单控件统一；边框/圆角层级不变）；② 轴 7 设置模态 10 个高级 `.settings-section` 默认 `collapsed`（核心「API Status」常驻），标题 `::after` 箭头 + `onclick` 切换父段（`classList.toggle`，无新函数、无 `window.confirm`）。零 JS 逻辑改动、零 id/令牌/`data-i18n` key 改动；契约测试全绿（23 JS + 25 Python）。映射蓝图 `webui-redesign-mapping.md` §2/§7 标注「已落地」。
- **⚠️ 分支名变更（环境限制，v6-lite.14）** — 本沙箱 git 无法持久化**嵌套引用**（带 `/` 的 `ui/redesign-preview` 引用写入即丢失，提交后引用被抹、索引重置为全仓库暂存基线）。故实装分支改用**扁平名 `ui-redesign-preview`**（提交对象/代码与约定名完全一致；后续可 `git branch -m` 改回）。映射蓝图 §2/§7/§4 等处的 `ui/redesign-preview` 指称均对应此扁平分支。
- **v6-lite.12（2026-08-17）** — 轴 4 实装落地真实 `services/webui/src/joy_interaction_webui/static/`：6 槽位本云 Seg（纯 CSS 无 confirm）+ Provider 控件（main/summary/asr/agent/embedding；tts 仅 seg）经 `config_services.js#wireSegProvider` 接线，套用调现有 `PUT /api/services/config`；零后端改动；契约测试全绿（config_services / i18n / webui_static_contract）。§2 标注「已落地」。
- **v6-lite.11（2026-08-17）** — 整体校准：① 修正错误假设 `services.<slot>.cloud.*` / `.local.*` / `.mode`（真实后端为扁平契约，见 §1）；② 路径 `services/webui/static/` → `services/webui/src/joy_interaction_webui/static/`；③ 真实 token 改为 `--border-color/--warning-color/--error-color/--joy-red`，并标注预览 demo 自造 token 须重映射（§3 红线 3）；④ 新增 §4 落地策略（纯前端实现、不改后端契约）；⑤ 未决项 注1/4/5 标记为已由策略 A 消解，新增 注6(TTS 字段)/注7(ASR 云端)/注8(agent 槽位)；⑥ 明确 `embedding` 槽位 ≠ memory-store 端点。
- **v6-lite.10（2026-08-17）** — Provider 控件（自命名 + +保存整套 + 历史下拉）加入 main/summary/asr/embedding 云端 subform；嵌入云端「v6-lite.8 删除 Provider」注释撤回。
- **初始（2026-08-17）** — 首版 UI 元素 → 后端字段映射（含当时未校准的 cloud.* 假设，已在 v6-lite.11 修正）。
