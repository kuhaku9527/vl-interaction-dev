# ADR-0020：WebUI 本地/云端 两段选择器作为 provider 槽位统一 UI idiom

> 本文档为 ADR-0020 决策建议，供主理人（team-lead）/ 审查组汇编后 ratification，转交用户评审。
> 仅做架构 / 交互决策，不含实现代码。关联：`doc/specs/webui-redesign-spec.md` §3.2、`doc/specs/unified-api-config-ui.md`（数据契约，正式）、`doc/specs/webui-component-consistency-spec.md`（D1–D5）。

## 一句话结论

把「**本地 / 云端 两段 Seg 选择器**」确立为 WebUI 所有 provider 槽位（主模型 / 摘要模型 / TTS / ASR / Embedding / 未来语音扩展）的**统一 UI idiom**：本地 = env 探测 + 自填端口，云端 = Provider 控件（自命名 + +保存整套 + 历史下拉）+ 三件套（API 地址 + API Key + 模型，endpoint 配 `<datalist>` 建议）；切换用纯 CSS 滑动指示器 + `display` 切换 + keyframe 淡入，**禁止依赖 `window.confirm`**。以此消除 v5→v6-lite 期间各槽位选择器形态不一、切换交互失效的反复返工。

---

## 一、决策背景与动机

- **用户 2026-08-16 15:08 定稿原则**：模型 / 语音 / 嵌入三个槽位的选择，顶层一律「本地 / 云端」两段；选哪个就切换不同子表单（本地读已设环境 / 自填规范端口，云端客户自填）。
- **实证事故链**：
  - v3 仅 TTS 切换依赖 `window.confirm`（先切后问），预览 webview 屏蔽 confirm → 「只有语音模块切换有问题」。
  - v4-lite 把 TTS / 嵌入 / KWS 全部改为 `confirm` **前置** → 全部卡死，「点击云端没反映」。
  - v4-lite.2 根因锁定：预览面板屏蔽原生 `confirm`，所有「前置 confirm」的 seg 直接 return，表单不切换。移除 confirm 后交互恢复。
  - 根因不是「本地/云端 分段设计」本身（那是用户定稿决策），而是**选择器 idiom 未统一、切换实现误用 confirm**。
- **Provider 控件回归（v6-lite.10 反思）**：v6-lite.9 我曾把嵌入云端残留的 Provider 下拉直接删除、改成「endpoint 即选择」，把「以名字管理整套配置」这一用户语义当成多余跳转。2026-08-17 19:38 用户反馈纠正：「下拉部分是给提供商的，自己填写、支持保存、以名字保存整套（API 地址/API Key/模型）」。本 ADR 据此把 Provider 控件作为云端 subform 必备前置（详见决策内容 §1.b）。
- **现状**：截至 v6-lite.10，模型 tab（主/摘要各自本云）、嵌入本云、KWS 三段、Provider 控件（main/summary/asr/emb 4 槽位）均落地同一 idiom；TTS 留待下一轮因额外参数独立 Provider 化设计。**无文档把它立为跨槽位强制约束**，后续新增槽位仍可能各造各的。

## 二、决策内容（与 spec 对齐）

1. **统一 idiom**：所有 provider 槽位顶层一律 `本地 / 云端` 二选一 Seg；本地子表单 = env 探测 pill（绿点 + VAR + value）+ 自填端口/路径（不暴露 api_key）；云端子表单 = Provider 控件 + 三件套（API 地址 + API Key + 模型）。

   **a. 本地段（同 v6-lite.9）**：env 探测 pill + 自填端口/路径，不暴露 api_key；不带 Provider 控件。

   **b. 云端 Provider 控件（v6-lite.10 新增）**：自命名 + + 保存整套 + 下拉历史
   - **结构**：`.provider-mgr` 容器含 label/Row/actions/datalist；左中右 flex 排「名称 input（带 datalist 自动补全）/ + 保存按钮 / 下拉历史 + 删除按钮 + 反馈 msg」。
   - **行为**：名称 + 点 + → 校验（name/api_base/model 非空）→ 写入 `localStorage.joyai.providers.<slot>` → 补 datalist/select；下拉选已有 → 自动回填 name/api_base/model（**api_key 不覆盖**——仅后端落盘，前后端都不外传）。
   - **持久化**：浏览器 `localStorage`（按槽位独立）；api_key 不入本地存储。storage 不可用时退化本会话内存并红字提示。建议后端同步开放 `GET/POST/DELETE /api/providers/<slot>` 同步落 settings.json。
   - **endpoint 建议独立于历史下拉**：历史下拉 = 整套（含模型）；`<datalist>` endpoint 建议 = 单字段（地址）建议。两者职责不重不冲突。

2. **切换实现硬约束**：纯 CSS `.seg-indicator` 滑动（`transform` 过渡）+ 子表单 `display` 切换 + `@keyframes` 淡入；**禁止 `window.confirm` 前置**；二次确认（如需）用自定义 toast / modal，不依赖原生 confirm。

3. **状态一致性**：selector / 子表单的状态表达复用 `webui-component-consistency-spec.md` D1–D5（`.chip` / `.seg` / 语义色），不新造形态。

4. **数据契约边界**：本 idiom 只管**呈现**；槽位字段 / 热重载端点以 `unified-api-config-ui.md`（正式）为准。

5. **适用范围（v6-lite.10 起）**：主模型 / 摘要模型 / TTS / ASR / Embedding 五槽位均落地同一 idiom（其中 main / summary / asr / embedding 4 个 Provider 控件已实现，TTS 留待下轮——参数 voice_id / group_id / 采样率 / 语速需单独设计 Provider 化方案）。未来新增槽位默认套用，不重复规定。

## 三、被否方案

- **每槽位各造选择器（v3 前状态）**：形态不一、切换交互各自实现、易踩 confirm 坑 → 否决。
- **保留 `window.confirm` 做切换二次确认**：预览 webview 屏蔽 confirm，必现「切换没反映」→ 否决（v4-lite.2 实证）。
- **绝对定位 + rAF 重排子表单做动画**（v4 尝试）：运行时高度坍缩 / 层叠拦截点击，导致「全站切换死」→ 否决（v4-lite 回退教训）；纯 CSS transform + display 切换为零风险等价表达。

## 四、落地与护栏

- 新增 / 改动 provider 槽位 UI 的 PR **必须引用**本 ADR + `webui-redesign-spec.md` §3.2 + 一致性 spec D1–D5；reviewer 核对 idiom 一致、无 confirm 依赖。
- `services/webui/static/` 回灌时，选择器相关样式（`.seg` / `.seg-indicator` / `.subform` / `.fade-in`）从预览文件平移，不在实装端另造平行变体。
- 可选加 static lint：标记 `window.confirm` 调用（UI 交互路径禁用）。

### 落地记录（v6-lite.12）

- idiom 已落地真实 `services/webui/src/joy_interaction_webui/static/`：6 槽位本云 Seg（`.service-row[data-service][data-mode]` + `.svc-seg` 滑动指示器，纯 CSS 无 confirm）+ Provider 控件（main/summary/asr/agent/embedding；tts 按 spec 仅 seg）通过 `config_services.js#wireSegProvider` 接线（seg 切换 + localStorage 预设 CRUD + 套用调现有 `PUT /api/services/config`）。
- **零后端改动**；`svc-*` id / `data-i18n` / lucide 图标 / 真实设计令牌全部保留；契约测试（`config_services.test.js` / `i18n` / `test_webui_static_contract.py`）全绿。
- 实装分支 `ui/redesign-preview`；ratification 由审查组对话转 `决策/`。

## 五、影响面

- 正向：新增 provider 槽位自动获得统一选择器 + 已知安全的切换实现，减少返工；与已正式的 `unified-api-config-ui.md` 数据契约解耦清晰。
- 负向：无破坏性改动；不改动后端；不引入新依赖。

---

## 六、修订 R1（2026-09-18）：连接列表后端持久化 + agent 分离

> 触发：用户在使用一两个月后反馈「**这套 UI 不合格、不跟随主流**」——
> 「保存按钮在哪我都不知道」「预设 UI 不实用」「我已经不知道该怎么用了」。
> 本节记录据此产生的**契约级修订**，与 §二 决策内容冲突处**以本节为准**。

### R1.1 连接列表迁到后端持久化（原 §1.b 的「持久化」条款被取代）

**原决策**（§1.b）：Provider 预设存 `localStorage.joyai.providers.<slot>`，
并**建议**后端同步开放 `GET/POST/DELETE /api/providers/<slot>`（当时**未实现**，
见原文「负向：不改动后端」）。

**问题**（实测确认）：
- 预设住在浏览器 → 换浏览器 / 清缓存**即丢失**；
- 预设**不含 `api_key`**（原设计为安全考虑），导致「应用预设后必 401」——
  换了 `api_base` 却沿用旧 key，用户看不出原因；
- 删除要求把名字**重新打进输入框**，下拉里明明选着却删不掉 —— 这正是
  「用户忘了怎么保存/删除」的代码原因。

**修订**：
- 存储改为**后端独立文件** `config/connections.json`（`chmod 0600` + gitignore）。
  选择独立文件而非并入 `services.json`，是为了**对现有加载路径零侵入**
  （`_merge_services_config_file` 会忽略未知槽位键）。
- 端点：`GET/PUT /api/connections`（**整体替换**语义；单用户本地无并发写者）。
- **`api_key` 现在允许随连接保存**（安全基线 E3，与 `services.json` 一致：
  明文 + 0600 + gitignore；`GET` 响应**脱敏**为 `api_key_set: bool`，不回明文）。
- 连接以**稳定 `id`** 为主键（不再用 `name`）—— 用户重命名后不会丢引用/误删。
- 一次性迁移：后端为空且 localStorage 有旧数据时自动导入。
- 实现见 `services/webui/src/joy_interaction_webui/connections_store.py`。

### R1.2 「服务商」下拉从死 UI 恢复（原 §1.b 结构被简化）

`summary` / `agent` / `embedding` 三槽位的 provider `<select>` 原先整个
`.form-group` 带 `hidden` → **用户永远无法触碰**，而 `readForm()` 仍读它、
`writeSummaryProvider` 还给它绑了 change 监听 → N8 那段「切 provider 自动填
默认值 + 清 Key 防 401」的逻辑**在 UI 上不可达**。

**修订**：移除 `hidden`，provider 下拉显式可见。其作用是**模板**——
选定后自动填 Base URL / 推荐模型，并清空 key（避免跨服务商误用）。

### R1.3 新增「获取模型」能力（原决策未覆盖）

新增 `POST /api/services/list-models`（body `{api_base, api_key}`，
响应 `{ok, models[], status, reason}`），配套前端「获取模型」按钮 +
`<datalist>` 候选。

- 底层管道原本就有：`service_probe.py` 的 `_probe_llm` **已解析出 `models`**，
  只是 `admin_endpoints.py` 组装 `/api/services/status` 时把它丢掉了。
- 用 `<datalist>` 而非 `<select>`：上游可能无响应，必须**保留手填**
  （本地模型尤其如此）。
- 纯探测，**不写任何配置**（与 `/api/services/test` 同一约定）。

### R1.4 Agent 从「模型」大类分离为独立面板

**原分类错误**：`agent` 曾与 LLM / Summary / ASR / Embedding 同列「模型」面板。
据 `services_config.py:53-56`，agent 的真实语义是**后台委派求解器**：
`provider = codex|hermes` → 经 background-agent `POST /v1/provider/route` **热切**；
其 `api_base` 是 **agent gateway 的服务地址**（`service_test.py:13`
明确为 `GET {api_base}/health` 探活），**不是模型 API 地址**。

**修订**：
- 独立导航项与面板 `#agentPanel`（标题「委派」）。
- 字段精简为：**Provider**（codex / hermes）+ **后端服务地址**（可选，gateway）
  + **测试**（探 `/health`）。
- 移除从模型模板复制来的不适用字段（预设三件套 / API Key / Model）。
- 「服务商」一词在 agent 语境下**措辞为「后端服务地址」**，避免与模型 API 混淆。

### R1.5 UI 形态对齐主流（**已落地**，2026-09-18 二次修订）

原 §1.b 的「自命名 + `+` 保存整套 + 下拉历史」三件套被用户判定为不直观：

> 用户原话：「保存按钮在哪我都不知道，哪个是保存按钮？」「删除按钮倒是有了，但是不协调」
> 「保存的预设 UI 也不实用」「我已经过了一两个月，发现自己都不知道怎么使用了」。

**修订为（方案 X + 默认收起，已实施）**：

```
[已保存的连接 (N) ▾]                    [+ 保存当前为连接]
  └ 展开后（默认收起）：
     ● openrouter 生产    openrouter · https://... · gemma-3-27b · 已存密钥   [应用] [删除]
     ● minimax 测试       minimax · https://... · MiniMax-M3 · 已存密钥      [应用] [删除]
  └ 点「保存当前为连接」→ 行内展开： [给这个连接起个名字（如：openrouter 生产）] [确定] [取消]
```

对照原三件套的改进：
- **「保存」有了明确文字**（原为孤立的 `+`，用户不知道那是什么）
- **删除并入列表项**（原为孤立按钮，位置突兀）
- **列表化**（原为下拉，看不到全貌、无法直接操作）
- **不用原生 `prompt` 取名** —— 遵守 §二.2 禁令（交互路径依赖原生对话框会被预览
  webview 屏蔽，历史上多次导致"点了没反应"）
- **默认收起**，避免 5 个槽位各带一个列表把设置页撑得过长

**适用范围（用户要求「需要的才改」）**：

| 槽位 | 是否改造 | 理由 |
| --- | --- | --- |
| `llm` | ✅ 改 | 有无效的三件套残留 |
| `summary` | ✅ 改 | 真有多服务商（openrouter / minimax） |
| `asr` | ✅ 改 | 用户可能填云 ASR |
| `embedding` | ✅ 改 | provider 白名单 3 个 |
| `tts` | ✅ 改 | 虽只有 url，连接列表仍适用 |
| `agent` | ❌ **不改** | 已分离为独立「委派」面板（R1.4）；它是 provider 二选一 + gateway 地址，**语义不同，不做形式统一** |

> DOM 契约：`svc-<slot>-conn-{toggle,count,save,list,name-row,name,confirm,cancel}`；
> 原 `svc-<slot>-provider-{name,add,pick,del}` **已全部移除**
> （前端 `_pFillPick` 一并删除，列表渲染为 `renderConnList`，仍走 DOM 构造 + `textContent`）。


### R1.6 已废弃 / 不再适用

- §1.b「持久化：浏览器 `localStorage`（按槽位独立）」→ **废弃**，见 R1.1。
- §1.b「api_key 不入本地存储」→ **修订**：现在允许存入后端 `config/connections.json`
  （仍不入 `localStorage`；`GET` 响应脱敏），见 R1.1。
- 「负向：不改动后端」（§五）→ **不再成立**：R1.1 / R1.3 均为后端新增。

