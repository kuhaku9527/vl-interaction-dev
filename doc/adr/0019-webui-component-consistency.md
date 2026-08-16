# ADR-0019：WebUI 元件一致性纪律（跨表面绑定 §9 token）

> 本文档为 ADR-0019 决策建议，供主理人（team-lead）/ 审查组汇编后 ratification，转交用户评审。
> 仅做架构 / 纪律决策，不含实现代码。

## 一句话结论

将 `voice-ui.md` §9 Design Tokens 的「复用 token、禁止散落」原则**提升为跨所有 WebUI 表面的硬约束**，并显式增补「元件复用白名单 + 禁止自造裸文字状态标签」，以杜绝设置弹窗 / 主视图 / header 各自为政导致的视觉语言破裂（2026-08-16 已发生三轮返工）。

---

## 一、决策背景与动机

- **§9 已存在但范围窄**：`doc/subsystems/voice-ui.md` §9 规定 voice HUD 状态徽章必须复用 token / 语义色 / 8px 网格，但仅限 voice HUD。WebUI 其他表面（主视图 header、设置弹窗、状态指示区）无同等级约束。
- **2026-08-16 实证事故**：设置弹窗 redesign 时，前端自造 `.qt` 裸文字状态标签 + 新配色，破坏 header（`.health-pill`）/ 主视图（`.chip`）/ 健康菜单（`.svc`）既有视觉统一，连续三轮返工（v6-lite → v6-lite.2 → v6-lite.3）才收敛。用户明确批评「ui 元素要统一」「状态说明缺失」。
- **根因**：约束没写到「所有 WebUI 表面」，且没人强制引用 §9。代码 review 没拦住自造元件。

## 二、决策内容（与 spec 对齐）

1. **元件复用白名单（D1）**：状态 / 指示类元素只能取 `.status-badge` / `.chip` / `.svc` / `.health-pill` 四类既有 class；新形态须在 spec §3.1 登记，禁止 PR 内临时造。
2. **状态色语义（D2）**：直接复用 §9.1（绿=健康/激活、黄=未知/警告、灰=未连接/未激活、红=错误），不得新造色值。
3. **禁止裸文字状态标签（D3）**：不得以无边框 / 无圆角 / 无状态点的纯文字表达激活态；状态说明用 `title` / 列表文本补足。
4. **8px 网格（D4）+ 字体归属（D5）**：继承 §9.3 / §9.2。

## 三、被否方案

- **仅依赖 §9（不扩展）**：范围窄，已证失败。
- **每表面各自约定**：漂移、重复、不可比对。
- **纯人工 review 盯**：不可靠。

## 四、落地与护栏

- 新增 / 改动 WebUI 状态类元素的 PR **必须引用** `webui-component-consistency-spec.md` + `voice-ui.md` §9；reviewer 按 D1–D5 核对，不符打回。
- 建议 `services/webui/static/` 加 lint，禁止未登记的新状态指示 class 命名。
- 本 spec 列入 `AGENTS.md` onboarding 必读（与 §9 并列）。

## 五、影响面

- 正向：后续任意 WebUI 表面（含本次 redesign 回灌 `services/webui/static/`）自动获得一致性护栏，减少返工。
- 负向：无破坏性改动；§9 既有 token 不动；不引入 CSS 框架。
