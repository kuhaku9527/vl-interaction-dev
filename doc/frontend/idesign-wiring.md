# iPolloWork iDesign Studio 接线说明（JoyAI WebUI）

> 建立：2026-09-18 ｜ 端点身份：**前端设计** ｜ 状态：**已实测通过**

## 1. 它是什么

把**真实 WebUI 页面**接进 DSH 的 **Design 视图**（插件 `deepseek-idesign`，iPolloWork Design Studio），
让 UI 重设计在 Studio 画布里交互式进行：**用户选元素 → Ask AI 填草稿 → 用户发送 → agent 改文件 → 画布刷新**。

设计迭代只落在 `design/`，**不碰** `services/webui/` 真实代码，符合 AGENTS.md 隔离要求。

## 2. 关键事实（全部来自源码/实测，非推测）

| 事实 | 证据 |
| --- | --- |
| 插件真名是 `deepseek-idesign`（不是 `design`） | `profiles/web/package.json` dependencies |
| 它是**对话视图**，不是工具集 —— **不向 agent 注册任何 tool** | `lib/index.js:4174-4205` 只注册 web 路由；grep `registerTool`/`defineTool` 零命中 |
| 只有 `webServer` + `workspaceRegistry` 依赖 | `lib/index.js:4175` `inject: ["webServer","workspaceRegistry"]` |
| 画布入口由 **manifest.entry** 决定，可配 | `lib/index.js:4026` ``entry: `design/${projectId}/${manifest.entry}` `` |
| **只能读写 `design/` 前缀** | `lib/index.js:3903` `safeRelativePath(value, prefix="design/")`；逐段校验、拒 `..`、拒逃逸符号链接 |
| 走 **srcdoc 内联渲染** | studio 产物 `index-*.js`：`L.srcdoc=rg(ts.source,…)` |
| 资源重写**只处理 `img[src]`** | 同文件 `objectUrls` 分支：`querySelectorAll("img[src]")` |
| 单文件上限 **20MB** | `lib/index.js:3587` `MAX_REQUEST_BYTES`、`:3890` `MAX_TEXT_BYTES` |
| Ask AI **只填草稿，不自动发送** | README:60/69；`lib/client.js:43-59` `inputActions.setDraft` |

**推论（决定了接线方式）**：因为只用 srcdoc 且只重写 `img[src]`，
真实页面的 `<link href="styles.css">` 与 21 个 `<script src>` 在画布里会全部失效，
**必须先内联**成单文件，否则画布只是裸 HTML。

## 3. 接线步骤（脚本化）

```bash
# 1) 内联：把真实页 inline 成单文件（1 CSS + 21 JS）
node scripts/idesign-inline.mjs .cache/idesign/inlined.html

# 2) 接线：同步到【DSH 当前活跃会话】+ 生成 ipw 令牌契约
node scripts/idesign-wire.mjs                 # 推荐：自动读 dsh.sessions.current
node scripts/idesign-wire.mjs --dry-run       # 预览（不写盘、不清理）
```

其它参数：

| 参数 | 作用 |
| --- | --- |
| （无参数） | 同步到当前活跃会话 + **清理**其它 session-* 旧目录（默认） |
| `--all-sessions` | 同步到 design/ 下**所有** session-* 目录（冗余但免疫 fork） |
| `--session <id>` | 显式指定会话（脚本化场景） |
| `--keep-old` | 不清理旧目录 |
| `--dry-run` | 只预览 |

产物：`design/<sessionId>/{index.html, design-tokens.css, manifest.json}`

### ⚠️ 为什么要「自动读当前会话」+「清理旧目录」

**根因（2026-09-18 实测查清，推翻了此前「用户开了新对话」的错误判断）：**

DSH 会在两种情况下把**同一个对话** fork 成**新 sessionId**：

1. **编辑已发送的消息并重发** —— `dsh-easyrewrite` 插件调用官方 `ctx.sessions.fork`
   （证据：其 `lib/index.js:6`「在 targetSeq 之前的最后一个闭合回合处 fork 新版本」、
   `:10`「依赖服务：sessions（fork/flush）」、`:230`/`:421`「fork 由 client 官方 RPC 执行」）
2. **上下文续接** —— 新会话带 `parentSession` + `seedLength`

**实测证据链**（来自 `harness/sessions/<workspace>/<id>/session.jsonl.zstd` 首条元数据）：

| 会话 | 创建时间 | parentSession | seedLength |
| --- | --- | --- | --- |
| `812e73b3` | — | (根) | — |
| `f536662c` | 17:03 | `812e73b3` | 127,718 |
| `776b091d` | 18:43 | `f536662c` | 234,370 |
| `8c3fd4ee` | 21:40 | `776b091d` | **469,524** |

`seedLength` 逐次翻倍 = 上下文续接特征；每个会话都只有 1 条 `{"type":"session",…}` 元数据
（空壳容器）；旧 id 进 `harness/storages/workspace.json` 的 `archivedSessionIds`。

**冲突点**：`deepseek-idesign` 用 sessionId 硬编码拼接项目路径
（`lib/index.js:3912-3914` → `design/${projectId}/`，`projectId = sessionId + suffix`）。
**sessionId 一变，插件就认为「这是新项目」，于是生成空白画布** —— 用户看到的
「画布又空了」就是这么来的，**不是用户开了新对话，也不是模型切换**。

**对策**：脚本每次同步都重新指向当前会话，并删掉 fork 留下的旧目录
（旧目录不删会持续误导判断：`design/` 下堆着多个 session-* 目录，分不清哪个是活的）。

> 早期版本曾**拒绝**使用 `dsh.sessions.current`（当时误判为「最近活跃对话≠Studio 显示的项目」）。
> 该判断已纠正：`dsh.sessions.current` **就是**用户当前在看的对话，是正确信号。

- `index.html` —— 内联后的真实页面 + Studio 令牌钩子
- `design-tokens.css` —— 10 个 `--ipw-*` 令牌，**取值取自真实 styles.css**
- `manifest.json` —— `entry: "index.html"`，标题改为「JoyAI VL · WebUI 设计稿」

### 为什么必须「拷贝到 design/」而不是原地引用

Studio 的写入边界硬编码为 `design/`（见上表），无法指向 `services/webui/`。
所以接线 = 在 `design/` 下建一份**可编辑镜像**。
真实页面仍是唯一真源；这里改完需要**人工回写**到 `services/webui/`（见 §5）。

## 4. 验收证据（已实测）

| 检查 | 结果 |
| --- | --- |
| 内联完整性 | 1 CSS + 21 JS；`blockingRefs: []`、`missing: []` |
| 内联版渲染 ≡ 真实页 | 截图 **SHA-256 完全一致**：`98d94635…f6d8` |
| 接线后渲染 ≡ 真实页 | 截图 **SHA-256 仍一致**：`98d94635…f6d8` |
| 令牌解析 | 10/10 成功，0 未解析 |
| 真实页可独立服务 | `http://127.0.0.1:8123`（静态 http.server，无需 8070/8099 后端） |

**关键结论：接线后画布观感与线上完全一致**（像素级），所以「所见即所得」成立。

## 5. 已知边界与坑

1. **改完要人工回写**：Studio 改的是镜像，真实前端不会自动变。回写需走独立 git worktree（AGENTS.md）。
2. **`design/joyai-redesign-preview.html` 是旧稿，不要接它**。
   它是 2026-08-17 的青绿配色 v3 稿（91KB/1641 行），而真实 UI 是**深色 + JoyAI 红**，
   两者设计语言不同；且它已被 git 跟踪。**它是参考，不是真源。**
3. **外链 CDN 依赖保留**：lucide / katex / marked / dompurify 仍走 CDN（5 条，已列出）。
   Studio 画布里若离线，这些图标/公式渲染会降级，但主界面不受影响。
4. **favicon 引用是装饰性**：`cosmeticRefs` 5 条，不影响渲染，已从阻断判定中排除。
5. **内联脚本里的注释可能含 `<script src=...>` 字样**（`render_markdown.js` 就有）。
   自检必须先剔除已内联块再匹配，否则假阳性 —— 该坑已在脚本中修掉。
6. **必须验证渲染，不能只看字节数**：第一版自检因注释误报 exit 1，
   实际是成功的；判断依据应是**截图哈希比对**，不是 grep 命中数。
7. **【已纠正】关于 `dsh.sessions.current` 的早期误判**（2026-09-18）。
   早期版本认为「该键 ≠ Studio 当前项目」，一度**默认拒绝**使用它、要求显式 `--session`。
   **该结论是错的**，源于把「sessionId 变了」误判为「用户开了新对话」。
   真实根因见 §3 的 fork 说明：**DSH 会因编辑重发 / 上下文续接而 fork 新 sessionId**，
   所以「当前活跃会话」就是用户眼前那个 —— 它一直是正确信号。
   现已改为**默认读该键**，且每次同步后清理旧目录。
8. **Studio 保存会重写 DOM**（2026-09-18 实测）：用户保存一次后，镜像里
   `<i data-lucide="settings">` 变成展开的 `<svg class="lucide …">`、
   `data-i18n>` 变成 `data-i18n=""`。即镜像是**渲染后快照**，不是源码。
   → **绝不可把镜像直接覆盖回源文件**；回写必须是「读懂意图后在真源重写等价代码」。
   （用户当时删掉的 6 个 id 是**有意清理**，非 Studio 破坏，已与用户确认。）
9. **改完必须复核契约 id**：任何 UI 调整后，逐个确认 7 个延迟/增益 id 与
   `mdToggleHeader`/`copyBtnHeader` 等仍**唯一存在**，否则 `live_ui.js`、
   `speech_input.js`、`vtt_player.js` 会静默失效。

## 6. 复现/回滚

- **回滚**：`design/<sessionId>/*.placeholder-backup` 是接线前的出厂占位页备份。
- **重新接线**：重跑 §3 两条命令即可（幂等，会覆盖）。
- **起真实页预览**：`services/.venv/Scripts/python.exe -m http.server 8123 --directory services/webui/src/joy_interaction_webui/static --bind 127.0.0.1`
  （注意：`python` 不在 PATH，必须用 venv 绝对路径；起真服务 `server.py` 需 cert + 8070 后端，不必需）
- **无头截图复核**：`"C:/Program Files/Google/Chrome/Application/chrome.exe" --headless=new --disable-gpu --hide-scrollbars --virtual-time-budget=8000 --window-size=1440,900 --screenshot=<out.png> http://127.0.0.1:8123/index.html`
  （ego 浏览器在本机不可用：`page.setViewportSize is not a function`，且会抢占到 DSH GUI 页面，**不要用它截图**）
