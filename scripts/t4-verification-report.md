# t4 独立验证报告 — webui-refactor-2026-09-19

验证者：verifier ｜ attempt_id: e63973f2-dc7f-4d36-bb6a-6886d39e24c8
方法：全部结论均来自**本轮实际执行的命令 + 原始输出**，不采信任何口头声明。
t1/t2/t3 自带的验证脚本只在作为「被测对象」时复跑，关键项另用**自建 A/B 取证**独立复现。

---

## 0. 独立复现基线的关键前置：定位 t1/t2 的真实起点

不能拿 `HEAD` 当基线 —— HEAD 是重设计**之前**的提交态，不是 t1/t2 的起点。

| 状态 | index.html 行数 | DOM id 数 | 来源 |
|---|---|---|---|
| `HEAD`（git 提交态） | 2998 | 264 | `.cache/baseline-wt/`（git worktree add --detach HEAD） |
| **pre-t1/t2 真实起点** | **3217** | **284** | `.cache/deepsec/manual.4uGD2X/.../index.html` |
| current | 1286 | 284 | 工作树 |

判据：`scripts/.invariants.json`（mtime **16:53:22**，早于 t1 createdAt **17:45:30**）记录
`html_lines=3217 / id_count=284`，与 `.cache/deepsec/manual.4uGD2X` 快照**逐 id 完全相同**
（`snapshot == pretree ? true`，284=284）。
故本报告一律以 **pre-t1/t2 树**为基线；用 HEAD 做基线会得到错误的 9 failed/186 passed。

---

## 1. 六套验收脚本 — 全部通过（原始输出）

每套**独立进程、串行执行**（避免 Chrome/端口争用），原始日志在 `.cache/t4-runs/`。

| 脚本 | EXIT | 原始结论 |
|---|---|---|
| `check-button-wrap.mjs` | **0** | `✅ 全部面板的带文字按钮均正常横排`（services14/agent3/voice6/memory3/wiki4/appearance2/advanced3/about2） |
| `check-fullscreen-parity.mjs` | **0** | `通过 17 / 失败 0` |
| `check-advanced-relocation.mjs` | **0** | `通过 12 / 失败 0` |
| `check-topbar-fixes.mjs` | **0** | `通过 10 / 失败 0` |
| `webui-invariants.mjs`（裸跑） | 1 | `html_lines -1930 / css_lines -52 / prompt_editor_append -1` 未声明 → **设计意图**，见 §1.1 |
| `webui-invariants.mjs --id-removed= --expect=…` | **0** | `新增 0`、`删除 0`、`✅ 硬约束保持，且所有计数变化均已声明` |
| `audit-api-contract.mjs` | **0** | `【BROKEN】前端在调、后端无此路由 —— 0 个` |

附加（非六套，但属关键回归面）：
- `audit-frontend-residue.mjs` EXIT=0 → `【B】JS 引用但 DOM 中不存在的 id（死引用）—— 0 个`；`死引用 0 ／ 显式隐藏 72（其中"已删功能疑似残留" 0）`
- `verify-wiki-badge.mjs` EXIT=0；`verify-page-errors.mjs` EXIT=0
- `verify-inline-extraction-scope.mjs` EXIT=0；`qa_loadorder_check.mjs` `stages 41, failures 0`

### 1.1 webui-invariants 裸跑 rc=1 的独立判读

按契约给定的判据（非 delta=0）逐条独立核验：

- **硬判据「删除的 id」= 0** ✅ —— 独立复算：
  ```
  SNAPSHOT(pre-t1/t2) ids: 284   CURRENT ids: 284
  REMOVED (hard criterion must be 0): 0 (none)
  ADDED: 0 (none)
  ```
- **所有计数变化有显式声明** ✅ —— `--expect=html_lines=-1930,css_lines=-52,prompt_editor_append=-1` 后 rc=0，`删除 0 / 新增 0`。
- `prompt_editor_append: 1→0` **非删除**，独立定位到新位置：
  ```
  app_main.js:1019:  fullscreenPromptOverlay.appendChild(promptEditor);
  ```

**注意**：本项 `rc=1` 是**继承自 t2 的 index.html 改动**的未声明 delta，与 t1/t2 是否违约无关；t1/t2 未删除任何 DOM id，故不构成回归。

---

## 2. t1 目标：死引用清零 ✅

```
【B】JS 引用但 DOM 中不存在的 id（死引用）—— 0 个
死引用 0 ／ 显式隐藏 72（其中"已删功能疑似残留" 0）
```
且 `verify-page-errors.mjs` 独立确认无残留裸引用导致的 TypeError/ReferenceError：
```
✅ 路径无异常：change  → "ok"
✅ 延迟期（含 WS 轮询）仍无未捕获异常  → []
```

---

## 3. 内联外移后全局可达性（CDP 逐个求值，A/B 对照）✅

自建脚本 `scripts/t4-ab-verify.mjs`：**A=pre-t1/t2(8124) vs B=current(8123)**，每侧**独立 Chrome 实例**，
用 `Page.addScriptToEvaluateOnNewDocument` 预置同一份 fetch stub，使两侧网络条件逐字节一致。
`new Function('return <name>')` 从**跨脚本作用域**求值（不受 sloppy-mode 全局属性解析影响）。

覆盖 **24 个标识符**（≥20），契约点名者全部在内：

| 标识符 | A(pre-t1) | B(current) | 结论 |
|---|---|---|---|
| `vlmHistory` | object | object | same-reachable |
| `lastText` | string | string | same-reachable |
| `promptEditor` | object | object | same-reachable |
| `settings` | object | object | same-reachable |
| `websocket` | object | object | same-reachable |
| `sessionId` | string | string | same-reachable |
| **`showSettingsPanel`** | undefined | **function** | newly-reachable（t1 加法暴露，符合预期） |
| `JoySettingsNav` | undefined | object | newly-reachable（同上） |
| 其余 16 个 | — | — | 全部 same-reachable |

```
标识符总数=24  两侧同可达=22  两侧同缺失=0  退化=0  新增可达=2
```

**`showSettingsPanel` 专项**（契约特别点名）：
pre-t1 侧 `undefined`，current 侧 `function` —— 即 t1 的加法暴露确实**新增**了可达性，
且**零退化**（无任何 pre-t1 可达的符号在 current 变为不可达）。
t2 的 `verify-inline-extraction-scope.mjs` 进一步给出对照（删掉 `window` 暴露后
`typeof=undefined` / 裸调用 `ReferenceError`；还原后恢复 `function`），我用 A/B 数据独立确认了同一结论。

---

## 4. console / pageerror 基线一致性 ✅

`pageerror（未捕获异常）= 0`，**两侧均为 0**：
```
A=HEAD-baseline  pageerror (uncaught): 0
B=current        pageerror (uncaught): 0
```

console 消息**种类集合**两侧完全一致（仅端口号不同，属设计差异）；条数在两轮间浮动，
经 `scripts/t4-console-variance.mjs` 各采样 4 次独立确认属**运行时抖动**而非代码差异：
```
A=HEAD(8124): counts per run = [6, 6, 6, 7]   ← 自身浮动
B=current(8123): counts per run = [6, 6, 7, 7] ← 自身浮动
A 独有消息种类 (2): 仅端口号不同（127.0.0.1:8124 vs :8123），文案逐字相同
```
两侧 5 种消息为：camera `NotAllowedError`、`/api/...` 404（静态服务无后端）、
fetch models 非 JSON、WebSocket error —— 均为**静态服务下无后端**的固有现象，非本次改动引入。

---

## 5. t1 声称的「知识库徽章点击展开面板」独立复验 ✅

**正向**（B=current，注入确定性 extended-status 使 wiki 进入 `enabled && ok=false` 唯一分支后点击徽章）：
```
wikiPanel: before {hidden:true, display:"none"}
           after  {hidden:false, display:"block", w:615, h:613}
           trulyVisible: true
```
→ `cat-hidden` 已解除，且**实际可见**（非仅 class 变化：display!=none 且 615×613 > 0）。

**反向对照**（A=pre-t1，同一脚本、同一注入）：
```
wikiBadge click -> {"after":{"hidden":true,"display":"none","w":0,"h":0},"trulyVisible":false}
```
→ 基线**确实失效**，证明 t1 的修复**非冗余**、断言有效（非"两边都通过"的假阳性）。

---

## 6. t3 独立复验

### 6.1 可执行 `innerHTML` — 用真词法器统计（注释/字符串内的不算）

自建 `scripts/t4-innerhtml-scan.mjs`（注释、字符串、正则字面量感知，非朴素正则）。

**t3 的 inScope 两个文件：全部归零** ✅
```
wiki_frontend.js       5 -> 0
joy_ws.js              1 -> 0
```

**但全仓 25 个 .js 中有 2 处仍为可执行**（`render_markdown.js`）：
```
render_markdown.js L36 : element.innerHTML = String(text || '');   // decodeHtmlEntities
render_markdown.js L138: wrapper.innerHTML = String(html || '');   // openLinksInNewTabs
```
经独立核验，这 **2 处与本次工作流无关**：
- `git diff HEAD --name-status -- …/render_markdown.js` → **空**（本工作流未改动该文件）
- HEAD 快照同样为 2 处（`render_markdown.js 2 -> 2`）
- 安全性由构造保证：L36 写入**游离 `textarea`** 并只读回 `.value`（解码实体，非渲染 sink）；
  L138 的入参是上游 `DOMPurify.sanitize(...)` 的输出。

⇒ 契约文案「全部 .js 内可执行 innerHTML = 0」**字面上不成立**，但这是**契约措辞问题**，
不是 t3 缺陷：t3 的 inScope 已归零，剩余 2 处是既有的、被 DOMPurify 保护的、且不在 t3 授权范围内的代码。

### 6.2 deepsec 门禁 — 精确复刻 pre-commit hook ✅

hook 扫描的是**staged 文件集**（`git diff --cached --diff-filter=ACM`，materialize 后扫描）。
我按同一逻辑物化 74 个 staged 文件后扫描：
```
materialized: 74 files
HOOK_EQUIV_EXIT=0
Scanned 46 file(s) in 1545.7 ms; 0 finding(s).   ← findings = 0
```
对照：对**整个 static 目录**扫描得 `EXIT=2; 2 finding(s)`，两条均为 §6.1 的
`render_markdown.js` 既有项，且 `render_markdown.js` **不在 staged 集合内**（`grep -c` = 0），
故**不阻塞提交**，与 hook 实际行为一致。

`.deepsecignore` 有效规则集 HEAD 与 current **逐字相同** ✅
```
HEAD   : rule_ids: - hardcoded_secret_assignment
current: rule_ids: - hardcoded_secret_assignment
```
→ t3 未新增任何 ignore 规则（仅加注释留痕），符合「以修复代替抑制」。

### 6.3 XSS 回归 ✅（含基线反向对照，证明用例有效）

| 注入路径 | A=pre-t1 | B=current |
|---|---|---|
| namespace **名**注入 `<img src=x onerror=…>` | img=0, fired=0 | **img=0, fired=0** ✅ |
| 错误消息路径（`apiJson` 的 `data.error‖data.detail`）→ 列表提示 | **img=1, fired=99** ❌ 真漏洞 | **img=0, fired=0** ✅ |

B 侧渲染结果为纯文本转义：
```html
<div class="input-hint" style="color: var(--warning-color);">加载失败：&lt;img src=x onerror="…"&gt;</div>
```
A 侧同路径**确实执行了** `<img>` 并触发 `onerror`（`fired=99`）—— 证明该用例有效，
t3 修的是一条**真漏洞**而非误报。

---

## 7. vitest 与 JS 语法 ✅

**vitest**（`cd services/webui && ./node_modules/.bin/vitest run`）：
```
Test Files  6 passed (6)
     Tests  44 passed (44)
```
**JS 语法**（`node --check`，逐文件）：
```
checked=25  failed=0        ← 契约要求 21；实际 25（外移新增 app_boot/app_main/sidebar_toggle/incremental_wiring）
```

---

## 8. pytest — 与基线逐条相同 ✅（含一处**方法论纠错**）

⚠️ 我最初用 `HEAD` worktree 做基线，得到 `9 failed / 186 passed`，
与 captain 的 `10 failed / 185 passed` 不符，一度疑似发现新增失败。
**经独立追查，是我的基线取错**：`HEAD`(2998 行/264 id) 是重设计前的旧提交态，
并非 t1/t2 的起点。改用 §0 确认的 **pre-t1/t2 树**(3217 行/284 id) 复跑：

```
####### PRE-T1 tree         : 10 failed, 185 passed, 7 warnings in 7.08s
####### CURRENT tree (14 files): 10 failed, 185 passed, 7 warnings in 7.76s

--- NEW failures (in current, not pre-t1) ---   (empty)
--- DISAPPEARED (in pre-t1, not current)  ---   (empty)
✅ 逐条完全相同：与基线 diff = 0，无新增也无消失
```

**captain 的基线数字正确，结论成立。** 争议项 `test_bt_latency_hud_is_rendered_in_result_header`
的独立归因（唯一变量实验）：
- pre-t1 树中 `btLatencyInline` **已不存在**（`grep -c` = 0；snapshot `_ids.includes()` = false）
- 在 pre-t1 树上单跑该测试 → **同样 FAILED**
- 在 current 树上补回 `<div id="btLatencyInline">` → `test_webui_static_contract.py` **25 passed**
⇒ 该失败**先于 t1/t2 存在**，属既有失败，不是本次引入。

（全量 `services/webui/tests`：current `20 failed / 851 passed / 2 skipped`。
其中 10 个为上述前端契约既有失败，另 10 个 `test_qa_server_split_runtime.py` 等属后端
导入态测试，与本次前端改动无交集；`test_qa_server_split_runtime.py` 在 pre-t1 树同样失败。）

---

## 9. topbar-fixes 稳定性复跑（captain 预警项②）

连续 10 次独立执行：
```
RESULT: pass=10 fail=0 out of 10
```
→ captain 的 `stopLiveStatusPoll()` 修复有效，**未观察到间歇失败**，无色记为 t2 回归的风险。

---

## 结论

| # | 验收项 | 结果 |
|---|---|---|
| 1 | 6 套验收脚本（含 webui-invariants 按契约判据） | ✅ 通过 |
| 2 | `audit-frontend-residue` 死引用 = 0 | ✅ 通过 |
| 3 | CDP ≥20 标识符可达性、`showSettingsPanel` 可见性未退化 | ✅ 通过（0 退化） |
| 4 | console + pageerror 空或与基线一致 | ✅ 通过（pageerror 0；console 种类一致、条数抖动已证伪为噪声） |
| 5 | 知识库徽章点击展开面板（＋基线反向对照） | ✅ 通过 |
| 6 | t3：inScope `innerHTML` 归零 / deepsec findings=0 / XSS 回归 | ✅ 通过（全仓剩 2 处既有项，属契约措辞问题，详见 §6.1） |
| 7 | vitest 44/44、25 个 JS 语法 | ✅ 通过 |

无阻塞性发现，未发现需要打回的缺陷。

### 两条供 captain / t6 留意的非阻塞事项

1. **契约措辞「全部 .js 内可执行 innerHTML = 0」无法字面满足** —— `render_markdown.js` 有 2 处
   既有 `innerHTML`（游离 textarea 解码 + DOMPurify 后写入），不在 t3 授权范围。
   建议 t6 在提交说明中登记为「既有、已由 DOMPurify/游离节点保证安全」，避免后续审查重复质疑。
2. **全量 pytest 基线不是 `10 failed/185 passed`，而是 `20 failed/851 passed/2 skipped`** ——
   `10/185` 仅是 **14 个引用 index.html 的文件**子集。captain 的 t2 evidence 已注明
   「（14 个引用 index.html 的契约测试）」，措辞无误；此处仅提醒 t6 不要把它当成全量数字引用。

### 环境备注

- 静态服务 8123（current）沿用既有后台任务；**8124（pre-t1/t2 基线）为本轮自建**，任务结束后可关闭。
- 本轮新增取证脚本：`scripts/t4-ab-verify.mjs`、`scripts/t4-innerhtml-scan.mjs`、
  `scripts/t4-console-variance.mjs`、`scripts/t4-run-acceptance.sh`。
