# t5 独立审查报告 — index.html 内联脚本外移（执行顺序 / 作用域 / TDZ）

审查者：reviewer ｜ attempt_id: 7d1f3f1a-756c-47d0-92b2-d9c10d880e6d
被审任务：t2（index.html 内联脚本外移，1895 行 / 4 块）
方法：**不采信 t2/t4 的任何结论**；另建 5 个独立取证脚本 + 自建 A/B 基线服务器，
每一条结论均可由本报告给出的命令与原始输出复现。

## 0. 判定基线（本次审查最关键的前置发现）

`.cache/pret1` **不是一个一致快照**，其 `index.html` 是 pre-t1，而所有 `.js`/`.css`
与 **current 逐字节相同**：

```
.cache/pret1/.../index.html     3216 行  md5 e86d0a839f   ← pre-t1（含 t1 待删死代码）
.cache/pret1/.../app_main.js    md5 26534d4874  ← 与 current 相同
.cache/pret1/.../styles.css     md5 1fc745bfeb  ← 与 current 相同
```

我因此**重建了真正一致的前缀快照**，用它隔离 t2 自身的改动：

| 快照 | 行数 | 内联块 | DOM id | 性质 |
|---|---|---|---|---|
| `.cache/deepsec/manual.4uGD2X/...` | 3216 | 4 | 284 | pre-t1 / pre-t2 |
| **`.cache/t5-review/pret2/index.html`** | **3137** | **4** | **284** | **post-t1 / pre-t2（本次审查基线）** |
| `services/.../index.html` (current) | 1286 | 0 | 284 | post-t2 |

`pret2` 来源：`/tmp/tmp.wqNZjTIgnt/static/index.html`（mtime **18:10**，早于 t2 产物 18:12），
284 id、4 个内联块，且 `getElementById('startBtn')` 与 `function checkApiKeyRequirement`
均已为 0 —— 即 t1 已完成、t2 未开始。**用它才能把 t2 的 delta 与 t1 的 delta 分离**；
用 pret1 会把 t1 的删除误记到 t2 账上（我第一轮就踩了这个坑）。

---

## 1. 执行顺序 / 引入位置：与基线**位置级完全一致** ✅

`scripts/t5-audit-extraction.mjs`

```
A1 script tag count  base=29 cur=29                        PASS
A2 baseline inline blocks = 4                              PASS
A3 current inline blocks = 0                               PASS
A5 full ordered script sequence identical after 1:1 inline->file mapping   PASS
B1-B4 ×4 个替换 tag：无 type= / 非 module / 非 async / 非 defer            PASS ×16
```

补充独立核验（不依赖该脚本）：

- 外部 tag 属性 0 处变化：`changed external-tag attributes: 0`
- 基线带 `module/async/defer/type=` 的 tag = **0**，current 亦 = **0**
- `tags in baseline not in current: (none)`（除 4 个内联块被同名文件取代外无增删）

**最强证据 —— 外壳位置级还原**（`scripts/t5-roundtrip-pret2.mjs`）：
把 pret2 的 4 个内联块原地替换为 `<script src="./X.js"></script>`，与 current **逐行比对**：

```
shell lines=1287  current lines=1287
line differences = 0
```

⇒ `<script src>` 落在与原内联块**完全相同的文档位置**，前后邻居无语义漂移。顺序类风险排除。

---

## 2. 标识符：无重命名 / 无作用域变化 / 无 kind 变化 ✅

`scripts/t5-scope-tdz.mjs`（**acorn 8.17 真实语法分析**，非正则）

```
app_main.js: baseline topLevel=212  current topLevel=207
  removed (7): startBtn, stopBtn, refreshModelsBtn, toggleApiKeyField,
               checkApiKeyRequirement, apiPresetsBtn, apiPresetsMenu
  added   (2): buildCameraOption, buildThemeIcon
  kind-changed: (none)
  surviving-binding order preserved: true (205 bindings)
app_boot.js / sidebar_toggle.js / incremental_wiring.js: 0 / 0，无 delta
```

**7 处删除与 2 处新增全部可归因**：7 项 = t1 授权删除的死代码清单，逐字命中；
2 项 = t2 的 deepsec helper。**无一项无法归因**。

全局面（跨全部 29 个 script 的顶层绑定并集）：

```
baseline 顶层名 396 → current 391
baseline-only (7): startBtn, stopBtn, refreshModelsBtn, toggleApiKeyField,
                   checkApiKeyRequirement, apiPresetsBtn, apiPresetsMenu
current-only  (2): buildCameraOption, buildThemeIcon
kind changes (var<->let<->const/function/class): 0
```

⇒ 「② 全局标识符泄漏范围变化」排除：泄漏面变化 = 7 个死符号消失 + 2 个本地 helper 新增，
**无任何符号改变 var/let/const/function 种类**（这是最容易造成「看似全局实则词法」静默失效的路径）。

残留引用核查：`toggleApiKeyField / checkApiKeyRequirement / refreshModelsBtn /
apiPresetsBtn / apiPresetsMenu / apiBaseHint` 在全仓 JS/HTML/pytest/vitest 中
**非注释的活引用 = 0**（命中全部落在解释性注释里）。

---

## 3. 三类高危之一：跨脚本 TDZ（const/let 提前访问）✅

首版扫描器有**盲区**：它把函数体一律当作「延迟执行」，因而对
`incremental_wiring.js`（整体是一个 IIFE）**什么都没扫到** —— 恰恰是契约点名的最高危文件。
我重写为 **IIFE 感知**版（`scripts/t5-tdz-v2.mjs`）：直接调用的函数体（IIFE）
视为**立即执行**，其内部标识符按载入期读取处理。

```
[baseline] parsed units=25  crossScriptTDZ=17   in-unit TDZ=0
[current]  parsed units=25  crossScriptTDZ=17   in-unit TDZ=0
```

两侧**数量与内容完全相同**；脚本报出的 2 条「NEW」经查是**纯单元改名**
（`INLINE@97354` 即 `app_main.js`）造成的签名差异，非新增风险。

对全部 17 条逐条下钻核查：它们**全部是 `window.JoyX = { name, ... }` 简写注册**
（如 `joy_ws.js:415` 的 `cleanupServerSession`、`render_markdown.js:147` 的 `escapeHtml`），
这些名字在各自文件内是 **IIFE 局部 function**，被我的解析器错误解析到外层同名 const。
即**误报**，且两侧逐条相同 ⇒ **真实 TDZ 风险 = 0（基线 0，改动后 0，新增 0）**。

**③ DOMContentLoaded / 立即执行时机**：`document.readyState === 'loading'` 分支、
`window.addEventListener('DOMContentLoaded'|'load')` 的注册点全部保留在原文件原地；
独立重跑既有顺序验收：

```
$ node services/webui/tests/qa_loadorder_check.mjs
stages: 41, failures: 0
```
（含 `window.JoyWs attached`、`window.websocket mirror`、`DOMContentLoaded[0..7]` 全 PASS）

---

## 4. t2 自身 delta 的语义等价性（我另建 A/B 实测）✅

t2 在 `app_main.js` 内**并非纯搬迁**：把 8 处 `innerHTML` 改写为 DOM 拼装
（`replaceChildren` / `createElement`+`textContent`）。这是**功能面改动**，必须独立验证渲染等价。

自建 A 侧：`pre-t2 index.html` + **与 B 侧逐字节相同的 JS**，仅此一个变量差异
（`scripts/t5-static-server.mjs`，已关闭）。

**(I) 旧写法 vs 新写法，在真实页面内并列求值比对序列化 DOM**（`scripts/t5-ab-paths.mjs`）：

```
✅ camera: No cameras found        ✅ theme icon: sun
✅ camera: Error detecting cameras ✅ theme icon: moon
✅ camera: clear                   ✅ theme icon: monitor
✅ fullscreen metrics              ❌ rtsp: connected status
     old: "✅ Connected!<br>H264 1280x720 @30fps"
     new: "<span>✅ Connected!</span><br><span>H264 1280x720 @30fps</span>"
```

8 处中 7 处**序列化后完全一致**。唯一差异是 RTSP 分支多了 `<span>` 包裹 ——
序列化不同不等于渲染不同，故单独立项验证（见下）。

**(II) 真实路径 A/B 对照**（同脚本两侧：

```
✅ theme cycle produces identical #themeIcon DOM on both sides  → 4 steps（含 SVG 逐字节相同）
✅ cameraSelect DOM identical on both sides
     A: "<option value=\"\">检测摄像头出错</option>"
     B: "<option value=\"\">检测摄像头出错</option>"
```

**RTSP `<span>` 布局中性证**（`scripts/t5-rtsp-span-neutrality.mjs`，把元素移入可见宿主，
并用 `!important` 覆盖 ID 级 `display:none`，使测量**非退化**）：

```
✅ textContent identical
✅ innerText identical
✅ container rect identical      → {"w":380,"h":60,"x":20,"y":16}（两侧相同）
✅ container ORIGIN identical (x,y)
✅ container display/white-space identical
✅ child count identical
✅ DISTINCT rendered line geometry identical (dedup by y,width)
     [{"y":16,"w":0,"h":15},{"y":16,"w":73.75,"h":15},{"y":31,"w":115.13,"h":15}]（两侧相同）
✅ non-degenerate measurement (box has area)
```

且 `#rtspStatus` 与插入的 `<span>` **命中的样式表规则 = 0**
（`matchedRules: (none)`；`grep -c rtspStatus styles.css` = 0）。

⚠️ 方法学说明：直接用 `range.getClientRects()` 计数会得到 3→5 的差异，那是
`<span>` 嵌套导致**同一行产生两个 inline box**，属取证假象；按 `(y,width)` 去重后
**真实行几何完全一致**，容器盒完全一致。这是「序列化不同、渲染相同」的确定性证据。

---

## 5. t4 证据的充分性与可复现性：逐条复现 ✅（附 2 处标注问题，见 §7）

契约 Verify 的 3 条命令，**我自己跑出的原始输出**：

```
$ node scripts/check-fullscreen-parity.mjs
通过 17 / 失败 0                                    EXIT=0
$ node scripts/check-advanced-relocation.mjs
通过 12 / 失败 0                                    EXIT=0
$ node scripts/webui-invariants.mjs
html_lines 3217→1287 / css_lines 5767→5715 / prompt_editor_append 1→0
id 集合变化: 新增 0 / 删除 0                          EXIT=1（未声明 delta，设计意图）
$ node scripts/webui-invariants.mjs --id-removed= --expect=html_lines=-1930,css_lines=-52,prompt_editor_append=-1
✅ 硬约束保持，且所有计数变化均已声明                  EXIT=0
```

其余关键闸门复现：

| 命令 | 结果 |
|---|---|
| `node services/webui/tests/qa_loadorder_check.mjs` | `stages: 41, failures: 0` EXIT=0 |
| `node scripts/audit-frontend-residue.mjs` | `死引用 0 ／ 显式隐藏 72（已删功能疑似残留 0）` EXIT=0 |
| `node scripts/verify-page-errors.mjs` | 全部 ✅（含 detectServices/blur/change 路径无异常）EXIT=0 |
| `cd services/webui && ./node_modules/.bin/vitest run` | `Test Files 6 passed (6) / Tests 44 passed (44)` EXIT=0 |
| `node --check` × 4 个新文件 | 全部 OK |
| `git check-ignore` × 4 个新文件 | 无输出（未被忽略）；`git status` 均为 `A`（已暂存） |

`webui-invariants` 裸跑 rc=1 的判读我认同 t4：硬判据「删除 id = 0」成立（独立复算
284→284、新增 0/删除 0），rc=1 仅因未声明 delta。**不构成交付失败**。

12 个 pytest 契约文件的改动我也逐文件核对：**全部只有 +4 行 `SPLIT_JS` 增补，
无删除、无其他改动**（`+4 -0`，非 SPLIT_JS 行数 = 0）—— 契约断言把外移后的代码
重新纳入检查，属正确做法。

---

## 6. 审查结论

契约三项验收**全部满足**：

1. ✅ `<script src>` 引入位置/顺序与原内联完全一致，未使用 module/async/defer
   （外壳逐行 diff = 0；29→29；属性变更 0）
2. ✅ 无标识符被意外重命名/删除/改变作用域
   （acorn 逐段对照：delta 全部可归因 = t1 的 7 删 + t2 的 2 增；kind 变化 0；存活绑定顺序保持）
3. ✅ t4 证据充分且可复现（§5 列出每条命令与原始输出）
4. ✅ 三类高危逐一排除：跨脚本 TDZ 新增 0 / 全局泄漏面变化仅可归因项 / DOM(ContentLoaded) 时机不变

**verdict = pass**。§7 的 4 项均为**非阻塞**，不改变上述判定。

---

## 7. 非阻塞发现（建议随提交一并修正，不影响 pass）

### F1｜`app_main.js` 头部「逐字节相同」声明**不成立**（low）
`app_main.js:4` 与 `:8`（4 个新文件同款）声称
「本文件正文与 index.html 内联时**逐字节相同**」/「重组后与搬迁前的 index.html 逐字节相同」。
实测后果：

- 4 个文件中 **3 个确实成立**（app_boot / sidebar_toggle / incremental_wiring = 纯 header + 原文正文）；
- **app_main.js 不成立**：正文含 **8 处 innerHTML→DOM 改写**（diff 显示删除 139 行 / 新增 97 行）；
- 「重组后逐字节相同」在**任何**文件都不成立 —— 重组后比 pret2 多 3874 字节（即 header），
  我实测 `rebuilt === pret2 : false`。

要求修正：把 4 个文件的该行改为精确表述（外壳位置级一致 + app_main.js 含 8 处已审阅的
deepsec 改写），或至少让 app_main.js 不再声称「逐字节相同」。**这是提交审查者会依赖的
「无损性自证」，措辞必须与实测一致。**

### F2｜t4 报告 §3/§5 把 A 侧标注为「pre-t1/t2」，实际是 HEAD（medium，属证据标注）
原始产物 `.cache/t4-ab.txt` 明写 `A=HEAD-baseline (static server 8124)`，且 DOM id = **264**；
而 t4 报告 §0 自己确认 HEAD 是 **264**、pre-t1/t2 是 **284**。即 §3 表格表头
「A(pre-t1)」与 §5「A=pre-t1（同一脚本、同一注入）」**用错了标签**：
那个反向对照实际跑在 HEAD（更旧的树）上。

- 结论**方向仍成立**（HEAD 确实缺该修复 → 对照有效），我也独立复现了 B 侧徽章展开；
- 但作为「基线反向对照」其**强度弱于声称**，且报告标题里的「HEAD-baseline」被 §3 改写成
  「pre-t1」会造成后续读者误用基线。建议把 §3/§5 的 A 侧统一改回 `HEAD`，或改用
  `.cache/t5-review/pret2` 重跑一次真正的「pre-t2 反向对照」。

### F3｜`webui-invariants.mjs` 因外移**丢失 JS 覆盖率**（medium，工具盲区）
该脚本只 `readFileSync(index.html)`（第 22 行），不读任何 `.js`。因此
`prompt_editor_append` 由 `1→0` **不是代码去哪了，而是指标看不见了**：

```
pre-t2 index.html : fullscreenPromptOverlay.appendChild ×1
current index.html: ×0
current app_main.js: ×1   ← 代码在，度量在 index.html 上已失联
```

t4 用 `--expect=prompt_editor_append=-1` 把它「声明」掉、从而让 rc=0，
**等于用一个已失联的指标为其背书**。同理 `on_handlers`、`ready_placeholder`、
`live_controls_in_settings` 等按 HTML 统计的项，对外移代码已不再有约束力。
建议：把 `webui-invariants.mjs` 的取样面扩到 SPLIT_JS（与 §5 已验证的 12 个 pytest 文件做法一致），
或明确在脚本头注释其覆盖面已缩小并说明 `prompt_editor_append` 的替代判据。
**这属工具/证据质量，不属 t2 代码缺陷。**

### F4｜`app_main.js:396` 注释与 CSS 实况矛盾（low）
`app_main.js:396` 写「styles.css 中的 `.input-source-tabs` / `.input-source-tab` 规则**本轮未动**
（CSS 不在本任务范围），已记录为后续清理项」，但实测 CSS **已被删除**：

```
pre-change styles.css 5766 行 → current 5714 行（-52）
被删选择器（与 HEAD 逐条 diff）：#apiKeyField / #apiKeyField.collapsed /
  #apiKeyToggle / #apiKeyToggle.collapsed / .input-source-tabs /
  .input-source-tab / .input-source-tab.active / .input-source-tab:hover
```

删除本身**正确且安全**（`.input-source-tab*`、`apiKeyField/Toggle` 在 current HTML/JS 中
活引用 = 0，全部为孤儿规则；这也与 t2「styles.css 孤儿规则已删」一致）。
问题仅是该注释已**过期**，会让读者误以为孤儿 CSS 仍在。建议改为「本轮已一并删除」并在
t6 契约里登记 styles.css 的 -52 行归属。

---

## 8. 产出物与复现命令

本轮新增（均在 `scripts/`）：
`t5-audit-extraction.mjs`、`t5-additive-check.mjs`、`t5-scope-tdz.mjs`、`t5-tdz-v2.mjs`、
`t5-roundtrip-pret2.mjs`、`t5-ab-paths.mjs`、`t5-rtsp-span-neutrality.mjs`、`t5-static-server.mjs`

原始日志：`.cache/t5-review/`（`audit-extraction.log`、`scope-tdz.log`、`tdz-v2.log`、
`roundtrip-pret2.log`、`ab-paths.log`、`rtsp-span.log`、`bodies/*.diff`）
重建的 pre-t2 基线：`.cache/t5-review/pret2/index.html`（md5 2b5a79a71b21fd6f6a8101f56aae1fc3）

环境备注：自建基线服务 8125（Node 静态服务器）**已关闭**；8123 沿用既有后台任务未动。
`node --check`：4 个新文件全部 OK。
