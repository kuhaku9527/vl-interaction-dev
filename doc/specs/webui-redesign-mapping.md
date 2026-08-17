# WebUI 改造 · 目标视觉规范（样板意图 → 真实落地映射）

> 配套件：`webui-redesign-spec.md`（意图 SSOT）。本文件把已对齐的 7 条视觉/结构轴**翻译到真实令牌 / 组件 / i18n / 契约**之上，是改造的落地蓝图。
>
> **对齐结论（2026-08-17 用户确认）**：改造 = 保逻辑·换表现；样板 = 视觉意图目标，理想化功能保留；7 轴即"整体"；轴 4 走 **(A)**（保留分段选择块视觉，底层映射到真实 provider）。

---

## 0. 现状令牌基线（真实，必须复用，禁造新值）

真实 `styles.css` 已定义完整双主题令牌，**暗色 + 单一红强调本来就是现状**：

| 用途 | 真实令牌（暗/亮双主题已定义） |
|---|---|
| 背景三层 | `--bg-primary`(#080707) / `--bg-secondary` / `--bg-tertiary` |
| 文字 | `--text-primary`(白) / `--text-secondary`(#CCCCCC) |
| 边框 | `--border-color`(#332526) |
| 强调色 | `--accent-color` = `--joy-red`(#c81e2a) / `--accent-hover` = `--joy-red-dark` |
| 警告/错误 | `--warning-color`(橙) / `--error-color`(红) |
| 圆角/阴影 | 既有 `--radius` 系列 + `box-shadow` |

**关键事实**：轴 1（暗色电影感 + 单一红）在真实代码里**已经满足**（joy-red 已是全局 accent，`nvidia-green` 甚至直接别名到 joy-red）。所以轴 1 不是"引入红"，而是**统一到既有 `--accent-color`，消除杂色与错位**。图标体系是 **lucide**，禁内联 SVG（样板用的内联 SVG 须回退到 lucide）。

> ⚠️ 修订待办：早前 `webui-component-consistency-spec.md` 引用的 `--ok`/`--warn` **对不上真实令牌**，须改为 `--warning-color`/`--error-color` 并补 joy-red accent 说明（见 §5）。

---

## 1. 七轴映射表

每轴：现状位置/问题 → 目标 → 真实落地 → 契约影响 → 阶段。

### 轴 1 · 暗色 + 单一红强调
- **现状**：已满足（joy-red accent + 暗主题）。痛点是杂色／错位，非缺红。
- **目标**：统一到 `--accent-color`，去除零散配色，红只做强调。
- **落地**：全局 `--accent-color` 即 joy-red；红仅用于激活态/品牌按钮/状态强调。
- **契约**：无。　**阶段**：1（低风险）。
- **实装状态（v6-lite.13，2026-08-17 审计确认）**：`styles.css` 全量审计确认 `--accent-color`=joy-red 已是全局**唯一交互强调色**（主按钮/激活态/品牌块均用 joy-red 渐变，如 `.chat-prompt-action.send`、header 渐变、`.sidebar-toggle` 等）。其余多色（设备/连接状态点 `.status-dot`、听录/直播态 `.listen.listening`/`.live-mode`、lucide 状态描边 3259–3396、`.service-badge` ok/err）均为**语义状态色**，非杂色，**予以保留**（轴3 仅统一状态「语义」不重着色）。故轴1 落地＝**验证通过，零代码改动**。

### 轴 2 · 统一卡片/面板表面语言（8px 栅格）
- **现状**：已有 `.settings-section` / `.service-card` 等，但"错位、观赏性低"。
- **目标**：统一圆角 / 边框(`--border-color`) / 留白到 8px 栅格，消除参差。
- **落地**：复用既有卡片类，补统一间距工具（8 的倍数），不新造表面元件。
- **契约**：纯 CSS／结构，无。　**阶段**：1。
- **实装状态（v6-lite.14，2026-08-16 审计+落地）**：纯 CSS 规范化 `.settings-section-title`/`.settings-item`/`.form-group`/`.panel-header` 间距对齐 8px 栅格（12/14px→16px），`.settings-close` 圆角 4px→6px 与表单控件统一；边框已统一用 `--border-color`、圆角层级（容器 12 / 卡片 8 / 控件 6）保持不变。零 JS、零 id/令牌改动，契约测试 23/23 + 25/25 全绿。

### 轴 3 · 状态语义统一（chip + 圆点）
- **现状**：`service-badge`(ok/err) + 各处散落徽章，语义不统一。
- **目标**：统一到 `voice-ui.md §9` status-badge 语义（绿=OK/激活、灰=未激活、橙=警告、红=错误），去裸文字状态。
- **落地**：复用 `--warning-color`/`--error-color` + §9 含义，**禁造 `--ok`/`--warn`**；引入文案须走 `data-i18n`。
- **契约**：新增 UI 文案须补 `i18n_ui_string.test.js`。　**阶段**：2。
- **实装状态（v6-lite.15，2026-08-17）**：补 `.service-badge` 样式（此前 CSS 缺失，`badge-<slot>` 为裸文字）。新增 chip+点：`.service-badge` 基准灰（`--text-muted`/`--bg-tertiary`/`--border-color`）+ `::before` 圆点（`currentColor`）；`.ok` 绿（沿用 `status-dot.ok` 的 `#2ecc71`）、`.err` 红（`--error-color`）、`.warning` 橙（`--warning-color`）。复用 JS 已在设的 `.ok`/`.err` 类，真实令牌、零 `--ok`/`--warn`；不动 JS/id/标记。契约测试全绿（23 JS + 25 Python）。

### 轴 4 · provider 分段选择块（**头号痛点**，用户原话："下拉太丑、错位、看不懂找不到"）
- **现状**：每槽位 `<select id="svc-<slot>-provider">` + `api-base` + `api-key` 输入框（id `svc-<slot>-api-base`/`-api-key`）平铺、错位。`config_services.js` 有 `SUMMARY_PRESETS` 联动（N8：切 provider 自动填默认 api_base/model、清空 api_key）。
- **目标（选项 A）**：顶部 **本地 / 云端** 两段 seg；
  - 本地 → `provider='local'`，隐藏 `api-base`/`api-key`；
  - 云端 → 展开 provider 子下拉（siliconflow / nvidia / openai …）+ `api-base` + `api-key`。
- **真实落地**：seg 滑块用 `--accent-color`；子下拉复用原生 `<select>` 重皮肤（圆角 + `--border-color` + `--bg-tertiary`）；**保留全部既有 DOM id**（`svc-<slot>-provider/-api-base/-api-key`）与 `SUMMARY_PRESETS` 逻辑，使其视觉变整洁但不破坏 6 槽位体系。
- **契约**：**不改 id → `*_contract.*` 测试不动**；仅视觉重皮肤。　**阶段**：3（最高优先级）。

### 轴 5 · 视频采集按需浮层
- **现状**：`capture-block` 已结构化（Webcam / RTSP / Screen + start/stop + BETA 徽章），底层接 getUserMedia / RTSP / WebRTC。
- **目标**：收成按需浮层/折叠 + 重皮肤，不在主视图全平铺。
- **落地**：复用 `.capture-block` 结构，改触发为浮层；保留底层采集接线。
- **契约**：保留现有 start/stop 事件绑定。　**阶段**：3。

### 轴 6 · 输入栏麦克风（嵌发送旁 + 激活反馈）
- **现状**：mic + send 按钮已存在。
- **目标**：mic 嵌发送按钮旁 + 激活红光(`--accent-color`) + 发送 `:active` 缩放反馈。
- **落地**：复用既有 mic/send 元素，加 `.active` 态与 glow ring。
- **契约**：保留现有点击绑定。　**阶段**：2。

### 轴 7 · 少即是多（默认收起高级细节）
- **现状**：设置为折叠 `settings-section`，但默认展开过多／错位，"看不懂找不到"。
- **目标**：高级 / provider 细节默认收起，主视图只留核心。
- **落地**：调 `settings-section` 默认 collapsed 态；核心服务状态常驻可见。
- **契约**：无。　**阶段**：1。
- **实装状态（v6-lite.14，2026-08-16）**：设置模态内 10 个高级 `.settings-section`（除核心「API Status」外）默认加 `collapsed`；标题加 `::after` 箭头（旋转指示）+ `onclick` 切换父段 `collapsed`（纯 `classList.toggle`，无新函数、无 `window.confirm`）；CSS `.settings-section.collapsed > *:not(.settings-section-title){display:none}` 隐藏内容。侧栏 `servicesConfig`（provider 细节）本就默认 `collapsed`，主视图核心状态常驻。零契约影响。

---

## 2. 改造红线（来自 `reports/webui-reality-inventory-2026-08-17.md`）

1. 复用真实令牌 / 组件 / **lucide**，禁造新色值、禁内联 SVG。
2. 保留 WS `ws://host/ws?session_id=`（https→wss）核心会话与 1s 状态轮询。
3. 保留 provider DOM id + `SUMMARY_PRESETS` 联动；任何 DOM id / `data-i18n` 改动须同步改 `*_contract.*` 测试与 `i18n_ui_string.test.js`，否则 CI 红。
4. 切换交互**禁依赖 `window.confirm`**（预览 webview 屏蔽 confirm 致"切换没反映"，v4-lite.2 实证）。

---

## 3. 实施阶段建议（每轴一 PR，保 CI 绿）

- **阶段 1（低风险，纯 CSS/结构）**：轴 1 + 轴 2 + 轴 7 — 令牌统一／卡片间距／默认收起。
- **阶段 2（中风险，动 badge/mic）**：轴 3 + 轴 6 — 状态语义统一 / 输入栏 mic。
- **阶段 3（高风险，动交互+契约测试，须同步测试）**：轴 4 + 轴 5 — provider 分段选择块 / 视频采集浮层（**轴 4 优先，用户头号痛点**）。

---

## 4. 待修订治理件（修订后随本映射文档一起 ratification 进 `决策/`）

- `webui-component-consistency-spec.md`：token 引用 `--ok`/`--warn` → 真实 `--warning-color`/`--error-color` + 补 joy-red accent 说明。
- `ADR-0020`：本云 seg 假设改为"轴 4 选项 A：本地/云端 两段 seg 映射到 provider，保留 id + `SUMMARY_PRESETS`"。

---

## 5. 附录 · 真实令牌速查（`styles.css`）

```
暗主题: --bg-primary:#080707  --bg-secondary:#121010  --bg-tertiary:#1b1515
        --text-primary:#FFF  --text-secondary:#CCC  --border-color:#332526
        --accent-color:var(--joy-red)  --accent-hover:var(--joy-red-dark)
        --warning-color:var(--joy-FFA726)  --error-color:var(--joy-EF5350)
亮主题: --bg-primary:#fffafa  --bg-secondary:#fff5f5  --bg-tertiary:#f7ecec
        --text-primary:#000  --text-secondary:#333  --border-color:#ead7d8
        --accent-color:var(--joy-red)  --warning-color:#F57C00  --error-color:var(--joy-D32F2F)
joy-red 族: --joy-red(#c81e2a) --joy-red-dark(#8f111a) --joy-red-light(#f0525b)
            --joy-red-soft / --joy-red-ring / --joy-red-shadow（红强调专用衍生）
```
