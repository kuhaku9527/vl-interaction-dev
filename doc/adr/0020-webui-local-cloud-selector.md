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

## 五、影响面

- 正向：新增 provider 槽位自动获得统一选择器 + 已知安全的切换实现，减少返工；与已正式的 `unified-api-config-ui.md` 数据契约解耦清晰。
- 负向：无破坏性改动；不改动后端；不引入新依赖。
