# 启动 Harness：正确示范与错误示范（启动纪律对照）（正式）

> 生命周期标记：`<正式>`
> 作者端点：`<通用诊断>`
> 关联：`决策/启动链路.md`（D-2026-08-04-001）、`doc/service-startup.md`、`.workbuddy/memory/MEMORY.md` §2/§10
> 合规：本 spec 套用 `决策/spec编写规范.md` 四要素（因果链 / 条件 harness / 负面约束 / 生命周期标记）。
> 目的：把"启动服务"的正确 harness 与踩过的错误做法对照固化，防复发。具体错误案例仅作反例，不进入 MEMORY.md（MEMORY 只收抽象纪律）。

## 1. 因果链（Why / Why-this-choice）

- **Problem**：AI agent 在"重启/起服务"任务中，偏离仓库 canonical 启动 harness——自造平行 wrapper、引用陈旧/被污染的规划记忆、未先核验当前状态、错误产物不及时清除，导致混乱与误用（"到底用哪个启动入口"）。
- **Why this choice**：仓库已有 SSOT 启动入口 `start-joyai.ps1`（薄封装→`services/scripts/run-windows.ps1`），其命令、模式、env 加载、后台拉起纪律已在 `决策/启动链路.md` + `doc/service-startup.md` 钉死。任何 agent 直接复用即可，无需另造。把"正确做法"与"错误做法"对照写清，让机制替人盯。
- **被否方案及理由**：
  - 另造平行启动 wrapper（如 `.workbuddy/scripts/launch-joyai-safe.ps1`）：否决。它是为绕沙箱 `Path`/`PATH` 环境块撞键 + 进程树回收而写，把 sandbox 独有怪癖固化成永久 repo 脚本，制造非 SSOT 的第二启动入口，真机根本不需要，且后续造成"到底用哪个"的混淆。沙箱怪癖应临时处理，不落盘。
  - 以更早的规划/记忆记录（如 4 号 `决策/启动链路.md` 中"硬化启动器"段，该段本身是后来错误注入的 wrapper 指引）作为行动依据：否决。启动依据应以"最近一次成功起栈的真实记录（对话 + launcher 日志）"为准，而非陈旧/被污染的规划文字。

## 2. 范围

- **做什么**：定义启动服务的 canonical harness（正确示范），并列出对应的错误示范（anti-pattern）及根因/后果。
- **不做什么（负面约束）**：
  - 不把本 spec 写成新的启动 SSOT——SSOT 仍是 `决策/启动链路.md` + `doc/service-startup.md`；本 spec 仅作对照/反例。
  - 不在 MEMORY.md 写具体案例（日期/脚本名/因果链）；MEMORY 只收抽象纪律（见 §4 指针）。
  - 不在此写 bug 修复/验证步骤（llama.cpp b10155 加载崩溃属 `doc/service-startup.md` runbook，非本 spec）。

## 3. 设计（核心决策点）

### 3.1 正确示范（canonical harness）

- **命令**：`powershell -ExecutionPolicy Bypass -File start-joyai.ps1 -Mode minimal`
  - 薄封装，转发到 `services/scripts/run-windows.ps1 -Mode minimal`。
  - 模式：minimal（main+webinfer+webui+memory-store）｜default（含 voice-clone）｜voice｜gaming。设置页验收用 minimal 即可。
- **env**：脚本自动 `Set-Item Env:` 加载 `services/scripts/run-windows.env`（含 `JOYAI_MEMORY_STORE_URL`/`MAIN_CONTEXT` 等），**无需手动 hand-set**。
- **运行方式（关键）**：必须在真机**后台/独立窗口**拉起。前台工具调用一结束进程树被回收，GPU 服务（llama-server）留不住。
- **验证**：`netstat` 看 7060/8070/8099/8997；读 `logs/launcher-<UTC>.log` 与 `services/.logs/*`。
- **SSOT**：`决策/启动链路.md` D-2026-08-04-001「⚡操作铁律」+ `doc/service-startup.md`。

### 3.2 错误示范（anti-patterns，附根因/后果）

| # | 错误做法 | 根因 | 后果 | 正确对照 |
|---|---|---|---|---|
| E1 | 另造平行启动 wrapper（`.workbuddy/scripts/launch-joyai-safe.ps1`）替代 `start-joyai.ps1` | 为绕沙箱 `Path`/`PATH` 撞键+进程回收，把 sandbox 怪癖固化成 repo 脚本 | 出现非 SSOT 第二入口；真机不需要；后续混淆"用哪个" | 直接用 `start-joyai.ps1`；沙箱怪癖临时处理不落盘 |
| E2 | 启动依据引用更早(4号)规划/记忆记录，而非最近(10号)成功起栈的对话+launcher 日志 | 未先查"最近一次是怎么成功起的" | 基于被污染/过时参考行动，重复造轮子 | 以最近 proven run 为准 |
| E3 | 栈实际已在运行(10号起)仍去"重启" | 行动前未核验当前状态（端口/进程/日志） | 无意义重启、引入风险 | 重启前先确认没在跑；在跑就报状态 |
| E4 | 明知 wrapper 错误/多余，当初没删，遗留至今 | 错误产物未当场清除 | 遗留错误产物本身是后续混乱与误用根源 | 确认错误/多余产物当场删 |

## 4. Harness（对照仪式）

- **正确仪式**：
  1. 先核验：`netstat -ano | findstr "7060 8070 8099 8997"` —— 若已在监听，报告状态、不重启。
  2. 若需起：真机后台窗口执行 `powershell -ExecutionPolicy Bypass -File start-joyai.ps1 -Mode minimal`。
  3. 验证：`logs/launcher-<UTC>.log` 末行 `All services ready` + 端口监听。
- **错误对照（禁做）**：用 `.workbuddy/scripts/launch-joyai-safe.ps1` 作启动入口；在 AI 前台工具里直接跑 `run-windows.ps1` 等阻塞式启动（进程树被回收）。
- **沙箱注意（非真机问题，不落盘为脚本）**：AI 沙箱内 `Start-Process` 可能因环境块 `Path`/`PATH` 大小写重复键撞键、且进程树在工具返回时被回收，导致无法在沙箱内起满栈。这是 sandbox 怪癖，不是真机问题；真机用 canonical 即可。如需沙箱验证，临时归一化 PATH 或在命令内处理，不要写永久 repo wrapper。

## 5. 验收 / 排除

- **验收判据**：任何 agent 接到"重启/起服务"任务时，直接复用 `start-joyai.ps1`、先核验状态、引用最近真值、错误产物即删；仓库内无第二启动入口。
- **明确排除**：llama.cpp b10155 加载期崩溃属 `doc/service-startup.md` runbook（DRIFT-005）；本 spec 只管"用哪个 harness / 别犯哪些错"，不管运行时崩溃。
