# [DRAFT] eslint 门禁收窄后：16 个内联外移模块（7256 行）失去全部静态检查

> 状态：**草稿，未提交**（2026-09-20）。等待用户确认后再 `gh issue create`。
> 撰写：主理人 + 子代理复核。复核方修正了主理人 6 处事实错误（见文末「撰写勘误」）。

**建议 label**：`tech-debt` `frontend` `ci`

---

## 背景

`services/webui/eslint.config.js` 的 eslint 门禁在 2026-09-20 由 **glob 改为显式 9 文件清单**（commit `8164638`），原因是 glob 静默扫入了 16 个从未在设计范围内的文件，产生 1297 个错误（`no-undef` 1056）。

该 commit 已随 `main` 推送（run [35511586356](https://github.com/kuhaku9527/vl-interaction-dev/actions/runs/35511586356)，job `eslint` = **success**）。本 issue 跟踪**被显式排除的那 16 个模块**。

## 事实

### 门禁是怎么变红的

| 项 | 值 |
|---|---|
| 配置 | `services/webui/eslint.config.js`（flat config，eslint v9.39.5） |
| 执行 | `.github/workflows/quality.yml` → job `eslint` → `npm run lint`（`eslint .`） |
| 原 scope | `files: ['src/joy_interaction_webui/static/**/*.js']`（**glob**） |
| 最后 CI 为绿的 commit | `b637dfa`（2026-08-11，run 31479401047，job `eslint` = success），当时 glob 匹配 **9** 个文件 |

门禁建于 `d8ade79`（2026-07-22，Phase 0），当时 glob 只匹配 **7** 个 `.js`。此后**两次不同的重构**把内联 JS 搬到**同一目录**，新增 **16** 个模块：

| 阶段 | 时间 | 起始 commit | 新增 |
|---|---|---|---|
| v6-lite 拆分 | 2026-08-13 ~ 08-18 | `bc7ca55`（batch-3 split 1/5） | 12 个：`vlm_history` `llm_reply_ui` `ws_dispatcher` `vlm_render` `tts_player` `background_rich` `live_ui` `speech_input` `status_poll` `llm_reply_audio` `radio_silence` `joy_state` |
| 内联脚本全量外移 | **2026-09-19** | `179961b` | 4 个：`app_main.js`(1684 行) `app_boot.js` `incremental_wiring.js` `sidebar_toggle.js` |

> 注意这是**两次重构**，不是一次。前阶段结束时 `index.html` 仍留 4 块内联；`179961b` 才全部外移（现 `index.html` = 29 script 标签 / 29 `src` / 内联 **0** 行）。

`globals` 列表自建立起只被追加过两次（`confirm` @ `1a6ea9c`、`performance` @ `8685742`），**从未为这 16 个模块扩展过**。

### 为什么潜伏约 5 周

`quality.yml` 的 `push` 只监听 `main`（另有 `workflow_dispatch` 手动兜底），而承载这批重构的 `ui-redesign-preview` 分支**从未 PR 到 main**（`gh run list --branch ui-redesign-preview` → 空）⇒ 该分支 CI 从未运行过。

### 收窄前的实测错误分解

| 规则 | 计数 |
|---|---|
| `no-undef` | **1056** |
| `no-unused-vars` | 153 |
| `quotes` | 71 |
| `strict` | 17 |
| **合计** | **1297**（0 warning） |

`no-undef` 的 202 个标识符中：**199 个名 / 1009 次 = 内联遗留全局**（`btLatency`46 `vlmHistory`25 `liveModeActive`22 `btTtsPlayer`21 `asrWs`20 `ttsWs`20 `videoElement`19 `promptText`19 `sessionId`17 `websocket`17 `settings`17 …）；**3 个名 / 47 次 = `globals` 配置缺口**，但其中 **46 次落在现已 out-of-scope 的文件**，只有 `config_services.js:185` 这 **1 次**真正在范围内（补上即归零）。

**17 个报错文件**：`live_ui`324 `speech_input`289 `app_main`238 `vlm_history`124 `tts_player`107 `ws_dispatcher`84 `vlm_render`34 `llm_reply_audio`31 `background_rich`22 `status_poll`19 `llm_reply_ui`12 `joy_state`4 `app_boot`3 `incremental_wiring`3 `config_services`1 `radio_silence`1 `sidebar_toggle`1。

**原有 9 个模块合计只有 1 个错误。**

## 为什么是「缩范围」而不是「改代码」

页面**能正常工作**。16 个模块裸引用的变量**实际都已在 `app_main.js` 内声明**（`let vlmHistory` :30、`let liveModeActive` :108、`let websocket` :136、`let asrWs` :162、`let btLatency` :184 …）。真正的结构性事实是：**一个原本完整的 IIFE 作用域被拆散到 16 个文件**，跨文件可见性依赖 `app_main.js` 早于后加载脚本执行。`no-undef` 报的不是「变量不存在」，而是「**静态分析看不到那个共享作用域**」。

⇒ 修 1297 个错误 ≠ 修 1297 个 bug；绝大多数是在给一个**跨文件隐式契约**补注解。`8164638` 还做了**双向阴性对照**：in-scope 文件注入未定义全局 → RC=1；out-of-scope 注入 → RC=0。证明门禁恢复后**仍有实效**、未被人为削弱。

### 但这是一次**确实的**质量保证倒退

这 16 个模块合计 **7256 行**，现在的真实状态是：**eslint 覆盖 0 + vitest 覆盖 0 + CI 覆盖 0**。

| 检查 | 覆盖这 16 个模块 | 在 CI 中运行 |
|---|---|---|
| `eslint` | ❌ 显式排除 | ✅ |
| `vitest`（job `frontend-test` = `npm test`） | ❌ **0** | ✅ |
| `services/webui/tests/qa_loadorder_check.mjs` | ✅ 设计上覆盖 | ❌ **不在 CI** |
| `scripts/webui-invariants.mjs` | ⚠️ 部分（结构/计数守恒，非符号解析） | ❌ 不在 CI |
| `scripts/check-button-wrap.mjs` | ❌ 无关（CSS 布局） | ❌ 不在 CI |

证据：
1. `vitest.config.js` 的 `include: ['tests/**/*.test.js']` **匹配不到 `.mjs`** ⇒ `qa_loadorder_check.mjs` 虽在 `tests/` 下，但 `npm test` **永不执行它**。
2. 6 个 `*.test.js` 全部只 import **in-scope 模块** ⇒ 对这 16 个模块覆盖 0。
3. grep 整个 `.github/workflows/`：**零处**引用任何 `.mjs`/验收脚本；全套只由本地 `scripts/t4-run-acceptance.sh` 串跑，**纯人工**。

> `qa_loadorder_check.mjs` 本身质量很高——按文档顺序回放全部 29 个 script 并断言 `window.JoyWs` 挂载、`window.websocket` 镜像、`getVlmDisplayText`/`syncSpeechButtons` 已定义，**恰好就是能抓这类 load-order 回归的工具**。问题不在它写得不好，而在**没接进 CI**。

## 要回答的问题

**Q1 — 契约选型**：`window.Joy*` IIFE 导出是不是正确终局？（实测 `no-undef` 里 **零个** `window.Joy*` 名，说明该 idiom 本身工作良好。）7256 行里有多少是「必须共享的可变状态」（`asrWs`/`websocket`/`btLatency` 这类）？这类跨模块可变状态用命名空间暴露是否反而更难维护？是否走 ESM（会改变 29 个 classic script 的加载模型）？

**Q2 — 中间方案**：能否只拿 lint 价值而不做全量重构？候选：从 `index.html`/现声明**自动生成 globals allowlist**（能拿 `no-undef` 信号，但**拿不到作用域边界信号**，拼错名会被一起放过，且需防漂移）；逐文件 `/* global a,b,c */`（粒度细但 199 名 × 16 文件，是否随重构腐烂）；**渐进 opt-in 小文件优先**（`joy_state.js` 仅 4 个错误）；把 16 个文件分组成独立 config 对象用不同规则集。**请明确采纳哪条，或明确不做中间方案。**

**Q3 — 风险量化（建议先答这一问，唯一不依赖大重构就能闭环）**：`qa_loadorder_check.mjs` 是否纳入 CI？改 `include`、改文件位置，还是在 `quality.yml` 加独立 step？（注意它依赖 `index.html` 的 script 顺序与命名，纳入后每次外移/改名都会牵动它。）`webui-invariants.mjs` 是否一并纳入？`check-button-wrap.mjs` 需 Chrome + 静态服务，是否有条件纳入？若不纳入，「UI 布局回归只靠人工」是否为已知接受项？在此期间这 16 个模块的评审是否需要额外人工 checklist？

**Q4 — 防复发**：`8164638` 已把 glob 换成显式清单，但「新增模块静默逃逸」仍会以「忘了改清单」的形式发生。是否需要一条**元检查**（`static/` 下新增 `.js` 若既不在 `IN_SCOPE` 也未登记为已知 out-of-scope，则 CI 报警）？`index.html` 现存内联 JS 至今未纳入任何 lint，是已知接受项还是待办项？`quality.yml` 的触发分支策略是否需复核（本次事故直接成因是长周期分支无 CI）？

## 验收标准

全部满足方可关闭，逐条给可复现证据：

1. 16 个模块的 lint 状态有**明确归属**：要么全部纳入 `IN_SCOPE` 且 `npx eslint .` **RC=0**；要么明确记为「永久豁免」并在 `eslint.config.js` 与本文档写明**理由**与**替代覆盖手段**（不可只写「暂不处理」）。
2. 若走纳入路径，给出从当前错误数降到 0 的完整过程（含每次提交的 eslint 计数），**不得**用 `eslint-disable` / 规则降级 / 注释规则达成。
3. Q3 结论落地：明确 `qa_loadorder_check.mjs`（及如采纳的 `webui-invariants.mjs`）是否纳入 CI；**若纳入**，给出 CI 实际执行的证据（run URL + job 名），并验证**故意引入 load-order 错误时该 step 会变红**（阴性对照）。
4. 防复发机制落地并验证（新增未登记的 `static/*.js` 时 CI 报警），**附阴性对照**。
5. `eslint.config.js` 头部注释、`doc/standards/coding-standards.md`（如涉及前端）与本文档三者一致，不留「注释说 A、实际做 B」。
6. `main` 上 `quality.yml` 的 `eslint` job 持续 success。

> **阴性对照要求**：仅「绿灯」不足以证明门禁有效——`8164638` 已做过正确示范。凡验收项涉及「门禁/检查有效」，均须提供**双向对照**证据。

## 非目标

1. 不重写 `index.html` 现存内联 JS（独立的「内联彻底外移」议题，与本次 scope 错配成因不同）。
2. 不修 `no-unused-vars`(153) / `quotes`(71) / `strict`(17)，除非落在最终纳入 `IN_SCOPE` 的文件中。（17 处 `strict` **全部**落在 out-of-scope 模块里。）
3. 不重构业务逻辑；只处理「静态检查覆盖」与「跨模块契约」，不改 UI 行为、DOM 结构、后端契约。
4. 不擅自恢复/删除任何 DOM 元素或 CSS 规则（遵循 `AGENTS.md` UI 硬约束）。
5. 不改 `quality.yml` 触发分支策略本身，除非 Q4 明确采纳。
6. 本 issue 是**技术债跟踪，非阻断项**。
7. 不以 `eslint-disable` / 规则降级 / 把 `no-undef` 改为 warn 作为「完成」手段——那会重演本次事故（门禁看似绿、实则无信号）。

---

## 撰写勘误（复核方实测纠正主理人 6 处）

| # | 主理人原说法 | 实测 |
|---|---|---|
| 1 | `d8ade79` 时目录有 **9** 个 `.js` | **7 个**；9 个是最后绿灯 commit `b637dfa` 时的数量 |
| 2 | 拆出 **15** 个新模块 | **16 个** |
| 3 | 拆分窗口 2026-08-13..08-17 | **08-13..09-19**，且其中 4 个来自 `179961b`（**另一次**重构） |
| 4 | `lucide`(21) 是 in-scope 的 globals 缺口 | ❌ 21 次全在 out-of-scope；in-scope 的 `wiki_frontend.js` 只用 `window.lucide.*` |
| 5 | `crypto`(4) 是 in-scope 的 globals 缺口 | ⚠️ 4 次全在 out-of-scope（`app_main`/`ws_dispatcher`）；in-scope 用 `window.crypto.randomUUID()`（已 guard） |
| 6 | `localStorage`(22) 是 globals 缺口 | ⚠️ 部分成立：22 次里 21 次在 out-of-scope，**只有 `config_services.js:185` 那 1 次在范围内** |
| 7 | `doc/adr/0011-phased-lint-gate.md` 预见了本次 | ❌ 该 ADR 状态 `Proposed`，**通篇 ruff/Python**，与 JS 门禁无关；`lint-baseline.md` 同样零处提 eslint |
| 8 | `quality.yml` 只触发 push/PR | ⚠️ 还有 `workflow_dispatch` 手动兜底 |

> ⚠️ 其中第 6 条影响 `8164638` 的 commit message：它写「`crypto` — config_services.js 随机 id」，
> 实测该文件用的是 `window.crypto.randomUUID()`，`crypto` 那条属**预防性**补充而非必需。
> 该 commit 已推送，message 不再改写，**在此记录以免后人误引**。

**其余数字全部逐项复现无误**：1297 总错 / 1056 `no-undef` / 202 名 / 199 名 1009 次 /
3 名 47 次 / 零个 `window.Joy*` / 17 文件逐个计数 / Top 名次全部一致。
