# WebUI 重构变更记录 — v6-lite.25（2026-08-18）

> 文件：`services/webui/src/joy_interaction_webui/static/styles.css`（+ 验证用 `index.html` 未改结构）
> 参考样板：`design/joyai-redesign-preview.html`
> 验证环境：静态服务器 `http://127.0.0.1:8099/`（伺服 `static/` 目录）
> 备份：`styles.css.bak`、`index.html.bak`（改动前全量 `cp` 留存，可回滚）

## 0. 用户诉求与本次范围

- 输入：用户截图反馈「重叠、没展开、还有很多问题」，要求重构现项目 Web（太乱）。
- 约束：**所有修改必须留痕，记录「为什么这么改」+ 因果链可追溯**（用户原话）。
- 范围：修复可见的功能性布局缺陷（重叠 / 截断 / 移动端输入栏错位 / 亮色冲突），并清理明确可证的死代码；**不做**大规模设计系统重写（见 §6  deferred）。

## 1. 根因总览（最关键的一条因果链）

v6 reskin 基础段（约 L4707–L4914）被**追加在**「≤768 / ≤420 媒体查询」段（约 L3713–L4149）**之后**。
按 CSS 源码顺序，后定义者胜出 → reskin 基础规则在移动端反客为主，盖掉了媒体查询里
`.container` / `.main-content` / `.prompt-editor-inline` 的声明。这同时导致：

- 移动端 `.prompt-editor-inline` 仍走 reskin 基础的 `position`/padding，而媒体查询里的
  `position:sticky;bottom:0` + `margin:auto -12px` 又被 420 段 `margin:auto -10px` 覆盖 → 输入栏跑到内容顶部并溢出视口。
- `.main-content` 被 reskin 基础改回 `display:grid;overflow:hidden` → 移动端无法内部滚动。

修复思路：把移动端布局意图在 **reskin 基础之后** 重新声明（追加媒体查询块，见 §3.4），保证断点内胜出；
同时修正各媒体查询内部的具体错误声明。

## 2. 设置面板：从 grid 改为 flex column（修复「重叠 / 没展开 / 右栏错位」）

### 2.1 `.settings-body`：grid(1fr 1fr) → flex column
- **位置**：约 L256
- **原因**：旧 `.settings-body{display:grid;grid-template-columns:1fr 1fr}` 使带
  `grid-column:1/-1` 的元素（`#aboutFooter`、`#capSettingCard`）被强制跨两列，
  实际渲染时错位到第二列（诊断实测 `left≈805`，本应全宽 `left≈781+padding`）。
- **改动**：`display:flex; flex-direction:column; gap:18px; padding:20px 22px;`
- **因果链**：flex 上下文下 `grid-column` 失效 → 所有 section 自然全宽纵向堆叠 →
  `#aboutFooter`/`#capSettingCard` 回到 `left=803`（= body 左 781 + 左 padding 22），全宽。✅ 已验证。
- **副作用**：`.settings-section.full-width{grid-column:1/-1}` 随之成为死规则（见 §4.3）。

### 2.2 `.panel-content`：解除 Services 面板内容截断
- **位置**：约 L514
- **原因**：`.panel-content{max-height:1000px;overflow:hidden}`。Services 面板含 6 个
  `.service-row`，实测内容高 `scrollH=3542px`，被 `overflow:hidden` + `1000px` 上限截断，
  **2542px 不可见**（早期把 `overflow:visible→hidden` 的修改反而加剧了截断）。
- **改动**：`max-height:4000px; overflow:auto;`
- **因果链**：保留 `max-height` 以维持折叠动画（`collapsed` 态 `max-height:0→` 展开）；
  上限提到 4000px 覆盖 Services 实际高度（3542<4000）→ `overflow:auto` 仅在极端超长时出滚动条，**永不静默截断**。✅ 已验证 `scrollH=3542 < 4000`，6 行全部可达。
- **注意**：折叠动画依赖 `.panel-content` 的 `max-height` 过渡（`collapsed` 类），
  故未直接删除 `max-height`（否则折叠变瞬切）。

### 2.3 设置导航跳转（modalNav）恢复
- **原因**：旧 grid 错位时，点击导航项 `scrollIntoView` 会把目标 section 滚到错误位置。
- **验证**：点击「关于 About」→ `.settings-body` 内部滚动，`#aboutFooter` `inView=true`，正确进入视口。✅

## 3. 移动端（≤768 / ≤420）：输入栏贴底 + 主区内部滚动

### 3.1 `.container`（≤768）：恢复 flex 应用壳
- **位置**：约 L3794
- **改动**：`flex:1; min-height:0; overflow:hidden;`（删除原 `height:auto; min-height:calc(100dvh-59px)`）
- **因果链**：与 reskin 基础 `.container` 一致 → 容器填满视口剩余高度并裁剪，子项按 flex 排布。✅ 实测 `T=60 B=812 W=375`。

### 3.2 `.main-content`（≤768）：flex column + 内部滚动
- **位置**：约 L3801
- **改动**：`order:1; flex:1 1 auto; min-height:0; height:auto; overflow:auto; display:flex; flex-direction:column;`
  （原 `height:calc(100dvh-59px); overflow:visible` + 固定 min-height）
- **因果链**：主区占满剩余空间并**自身滚动**，视频卡 / 结果卡在其内部排布，不被容器裁剪。✅

### 3.3 `.prompt-editor-inline`（≤768）：去 sticky / 去负 margin，改相对定位贴底
- **位置**：约 L3943
- **改动**：`order:2; position:relative; flex:0 0 auto; width:100%; margin:0;
  padding:8px 10px calc(8px + env(safe-area-inset-bottom));`
  （原 `position:sticky;bottom:0; margin:auto -12px -12px`）
- **因果链**：`body`/`.container` 均为 `overflow:hidden`（非滚动容器），`sticky` 无法吸附 →
  元素按 DOM 顺序（且 `order` 默认 0，排在 `order:1` 的 main-content 之前）跑到**顶部**，
  与视频卡重叠（`L=-10` 还因负 margin 溢出视口左右各 10px）。改为 `order:2` + `position:relative`
  后，作为 flex 应用壳最后子项**自然贴底**，全宽不溢出。✅ 实测 `T=710 B=812 L=0 R=375`，无重叠。

### 3.4 追加「reskin 之后的移动端覆盖块」（v6-lite.25，文件末尾）
- **位置**：文件末尾（约 L4916 之后）
- **原因**：见 §1 根因——媒体查询被 reskin 基础覆盖。把 §3.1–3.3 的移动端意图在 reskin 基础**之后**
  重新声明，确保 ≤768/≤420 断点内胜出（含 `.header` padding、`.video-card`/`.result-card` 顺序）。
- **因果链**：源码顺序后置 → 同特异性下后定义胜 → 移动端布局按预期生效。✅

### 3.5 `.prompt-editor-inline`（≤420）：去负 margin
- **位置**：约 L4125
- **改动**：`margin:auto -10px -10px` → `margin:0`（保留 `padding-left/right:8px`）
- **因果链**：消除手机端输入栏左右各 10px 视口外溢出。✅ 实测 `L=0 R=375`。

### 3.6 回归验证（多断点）
| 视口 | main-content | prompt-editor-inline | 结论 |
|---|---|---|---|
| 375×812 | flex 列，内部滚 `overflow:auto` | `T=710 B=812` 贴底全宽 | ✅ |
| 800×900 | grid 1fr 1fr（reskin 900 查询） | `T=801 B=900` 贴底 | ✅ 无回归 |
| 1280×900 | grid 1.5fr 1fr | `T=801 B=900` 贴底 | ✅ 无回归 |
| 1440×900 | grid 1.5fr 1fr | 底部药丸输入栏 | ✅（桌面始终正常） |

## 4. CSS 变量 / 类名冲突清理

### 4.1 删除死掉的 `[data-theme="light"]` 亮色 token 块
- **位置**：原 L4402–L4420（已替换为说明注释）
- **原因**：JS 主题切换实际执行 `document.body.classList.add('light-theme')`
  （`index.html:1781`），**永不设置 `data-theme="light"`** → 该块是永远不可达的
  「第二套亮色 token 定义」，与 reskin 基础 `body.light-theme`（约 L4712）重复且冲突。
- **因果链**：亮色 token 的 SSOT 已归 `body.light-theme`；删除该块消除重复定义，无运行时影响。✅

### 4.2 `.settings-modal` 亮色背景选择器可达化
- **位置**：约 L4799
- **改动**：`[data-theme="light"] .settings-modal` → `body.light-theme .settings-modal`
- **原因**：原选择器因 `data-theme` 永不设置而**不可达** → 亮色模式下设置遮罩仍用暗色
  `rgba(0,0,0,.55)`，与整体亮色不协调。
- **因果链**：改用 `body.light-theme` 后选择器可达 → 亮色下遮罩变为 `rgba(15,15,30,.32)`。✅ 已验证。

### 4.3 删除 `.settings-section.full-width` 的 `grid-column` 死规则
- **位置**：原 L267–L270（已替换为说明注释）
- **原因**：§2.1 把 `.settings-body` 改为 flex column 后，`grid-column:1/-1` 在 flex 上下文无效，
  对 `radioSilenceSection`（仍带 `full-width` 类）不产生任何视觉作用 → 死代码，留着误导。
- **因果链**：移除后 `.full-width` 类成为无害 no-op（DOM 保留，不影响渲染）。✅

### 4.4 保留项（有意不删，非冲突）
- 旧 token（`--bg-primary`/`--accent-color` 等，文件顶部 `:root`）与 reskin 新 token
  （`--bg-elev`/`--brand` 等）的**双向桥接**是 reskin 刻意保留的向后兼容层
  （reskin 段注释 L4720 起已说明：「旧 token 在暗色下也对齐样板红品牌」「亮色同步旧 token」）。
  直接删除桥接会让仍引用旧 token 的组件在亮/暗下失样式，故**保留**。

## 5. 未改动 / 有意不动的部分
- `index.html` 结构未动（仅此前会话留了 `.bak`）。所有修复靠 CSS，符合「最小改动」原则。
- reskin 基础段（L4707+）本身未改其设计意图，仅修正一处亮色选择器（§4.2）。
- 旧 NVIDIA 风格 `.settings-modal.show` 等遗留规则：与当前 `.settings-modal`/`.settings-modal.show`
  并存，但当前激活路径依赖后者；本次未动以免误伤（见 §6）。

## 6. 后续建议（deferred，非本次范围）
1. **遗留设计系统统一**：文件内存在三套重叠体系——(a) 旧 NVIDIA（`.settings-modal.show`/`--bg-primary`）、
   (b) 早期「样板」系统（`.app`/`.main`/`.card`/`.inputbar`/`.modal-overlay` + 已删的 `[data-theme="light"]`，约 L4422–L4550）、
   (c) 当前激活的 v6 reskin（`.container`/`.main-content`/`.prompt-editor-inline`/`body.light-theme`）。
   其中 (b) 的类名经 grep 确认**未出现在 `index.html`**（仅 `inputbar` 命中一次且为注释），属死代码；
   但 (b) 定义了若干**通用类名**（`.tag`/`.ghost-btn`/`.chip`/`.send`/`.prompt`/`.ctrl`/`.health-pill`/`.svc`/`.modal`），
   当前 reskin 可能部分复用其定义，**贸然整段删除有回归风险**。
   → 建议后续单独排期：对每个通用类审计「reskin 是否已覆盖」，确认后分批删除 (b) 整段。
2. 提交：本次改动尚未 commit。按项目 git 纪律，待用户确认后走 commit/PR 流程（CI 须绿）。

## 7. 验证方法（可复现）
```
# 静态伺服
node -e "..."  # 伺服 static/ 于 http://127.0.0.1:8099/
# 用 agent-browser 在 1440/800/375 视口下 eval getBoundingClientRect + getComputedStyle 校验：
#  - 桌面：.main-content 两列（≈1.5fr:1fr），输入栏贴底
#  - 设置面板：.settings-body display=flex；#aboutFooter/#capSettingCard 全宽；.panel-content 不截断
#  - 移动：.prompt-editor-inline T≈视口底、L=0 R=视口宽、position=relative
#  - 亮色：点击 theme-toggle → body.light-theme；.settings-modal 背景 rgba(15,15,30,.32)
```
