# 前端残留审计 — 2026-09-19

> 端点身份：**前端** ｜ 方法：运行时取证（Chrome headless + CDP），非读码推断
> 复现：`node scripts/audit-frontend-residue.mjs`（需先起 8123 静态服务）
> 触发：用户提问「前端需不需要优化模块拆分/重构/收缩冗余？有可能很多只是**表面删除了**，
> 但**功能实现还没删除**，或者是我忘记了的。」

## 一、总体结论

| 项 | 结果 |
|---|---|
| JS 文件 | 22 个，**全部**被 `<script src>` 加载（**无孤儿文件**） |
| `window.Joy*` 模块 | 19 个命名空间，职责清晰（见 §四） |
| 运行时 DOM id | 280 |
| **死引用**（JS 引用但 DOM 无此 id） | **13 个** ← 本轮重点 |
| 显式隐藏的 id | 72 个（**绝大多数合法**：面板/浮层/audio 播放器） |
| 「已删功能疑似残留」 | 0（按 id 名模式匹配） |

**判断**：模块拆分**已经做得不错**（22 个文件、命名空间清晰、无孤儿）。
真正的问题不是"该不该拆"，而是**有 13 处「DOM 已删、JS 仍在引用」的死代码** ——
正是用户担心的那种"表面删除"。

---

## 二、13 处死引用（逐条取证）

### A. 旧 API 设置面板残留（6 处）—— `#apiPresetsBtn` / `#apiPresetsMenu` / `#apiBaseHint`

`index.html:2782-2816` 整块 `// API Presets Menu`：
- 取 `apiPresetsBtn` / `apiPresetsMenu` → 两者**DOM 中都不存在** → `if (a && b)` 整个跳过
- 内部还引用 `.api-preset-item`（同样不存在）与 `#apiBaseHint`（不存在）
- **结论：整块是死代码**，且它保护的逻辑（URL 预设快切）随面板一起没了

> 背景：`2026-09-18`「API Status 面板已删除（用户明确要求）」+ 「input 面板已删除」。
> DOM 删了，这块 JS 没删。

### B. API Key 字段折叠（3 处）—— `#apiKeyField` / `#apiKeyToggle` / `#apiBaseHint`

- `index.html:2389-2390` `toggleApiKeyField()`：取两个不存在的元素后直接 `.classList` 操作
  → **若被调用会 TypeError**（当前无调用者，故未爆）
- `index.html:2397-2415` `checkApiKeyRequirement()`：内部有 `if (apiKeyField && apiKeyToggle)` 守卫，
  但**这两个变量恒为 null** → 整个"远端 URL 显示 Key 输入框"的逻辑**永久失效**
- **注意**：`checkApiKeyRequirement` **仍在被调用 4 处**（`ws_dispatcher.js:196`、`index.html:2521/2538/2739`）
  → 是"调了但什么都不做"的**静默失效**，不是死代码。这类最危险。

### C. 旧捕获面板控件（4 处）—— `#startBtn` / `#stopBtn` / `#webcamControls` / `#rtspControls` / `#screenControls`

- `index.html:1314-1315` 顶层常量 `startBtn` / `stopBtn` → DOM 无
- `index.html:1665-1697` `// Handle input source tabs (Webcam vs RTSP)`：
  `document.querySelectorAll('.input-source-tab')` → **`.input-source-tab` 在 HTML 中出现 0 次**
  → `forEach` 遍历空集，**整块永不执行**
- 块内引用的 `webcamControls`/`rtspControls`/`screenControls` 也都不存在
- **现状**：三个捕获源已改为**独立面板**（`.capture-block` + 各自的 `webcamStartBtn`/`rtspStartBtn`/`screenStartBtn`，
  见 `index.html:1701` 起的 IIFE，那些是**活的**，且用的是局部变量，与本块无关）
- **结论：整块（含顶层两个常量）是死代码**

### D. 其他（3 处）

| 死引用 | 位置 | 性质 |
|---|---|---|
| `#knowledgeBaseToggle` | `status_poll.js:311` | 知识库徽章点击想展开对应面板；该 id 不存在 → `if (kb)` 静默跳过（**知识库徽章点击展开功能失效**） |
| `#resetSessionBtn` | `ws_dispatcher.js:355-357` | 重置会话按钮已删 → 绑定跳过 |
| `#refreshModelsBtn` | `index.html:1374` + `2706` | 已被 services-panel 重构取代（`data-model-fetch` 新版存在）；注释已说明，属**已知残留** |

---

## 三、风险分级

| 级别 | 项 | 理由 |
|---|---|---|
| 🔴 **静默失效** | `checkApiKeyRequirement`（B） | **仍在被调用**，但内部元素恒为 null → 远端服务不再提示填 Key。用户会以为功能在，其实没有 |
| 🟠 **静默失效** | `#knowledgeBaseToggle`（D） | 知识库异常时点击徽章**不展开**知识库面板 |
| 🟡 **死代码** | apiPresets 整块（A）、旧捕获 tabs 整块（C）、`toggleApiKeyField`、`resetSessionBtn`、`refreshModelsBtn` | 无调用者或遍历空集，不影响运行，但**误导阅读者**（且每次读代码都要重新判断一遍） |

**共同点**：全都**不会报错**（有 `if (el)` 守卫或空集遍历）→ 所以能潜伏很久。
这正是"表面删除、实现还在"的典型形态。

---

## 四、模块拆分现状（用户问"需不需要拆分/重构"）

**结论：拆分已经到位，不需要大动。** 22 个 JS 按职责分：

| 模块 | 大小 | 职责 |
|---|---|---|
| `index.html` | 198 KB | 结构 + **大段内联脚本**（仍是最大单体） |
| `vlm_history.js` | 41 KB | VLM 历史渲染 |
| `live_ui.js` | 41 KB | Live 模式（含 jarvis 唤醒链路） |
| `config_services.js` | 36 KB | 服务配置 / 连接列表 |
| `speech_input.js` | 28 KB | 语音输入 |
| `background_rich.js` / `ws_dispatcher.js` / `status_poll.js` | ~20 KB 各 | 后台委派 / WS 分发 / 状态轮询 |
| 其余 15 个 | <20 KB | 单一职责，清晰 |

**真正的重构收益点**（按价值排序）：
1. **清死代码**（本轮 13 处）—— 低风险、立即可做
2. **修静默失效**（§三 🔴🟠）—— 要么补回 DOM，要么彻底删掉调用点
3. **index.html 内联脚本外移** —— 198 KB 里内联脚本占比大；但**风险高**（全局变量/加载顺序耦合），
   建议单独排期，不要和 UI 修复混在一起

---

## 五、待用户拍板

死代码**删除**与**补回**是两个方向，取决于功能是否还要：

- 若「API Key 远端提示」「知识库徽章点击展开」「URL 预设快切」「重置会话」**都不要了**
  → 删除对应 JS（含 `checkApiKeyRequirement` 的 4 个调用点）
- 若还想要 → 需要**补回 DOM**，而不是留一段静默失效的代码

**默认不动**（遵守"用户删的元素不要恢复，也不要替用户决定删功能"的硬约束）。
