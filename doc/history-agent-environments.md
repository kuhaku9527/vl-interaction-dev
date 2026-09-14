# 历史环境记录：WorkBuddy / Codex 时代（2026-07 ~ 2026-08）

> **本文件的性质**：**历史档案**。记录 2026-07 至 2026-08 期间，本项目由 **WorkBuddy** 与 **Codex** 两个 agent 应用操作时，**因那两个环境的沙箱/工具特性而产生**的操作技巧与踩坑。
>
> ⚠️ **这些内容对当前环境（DSH）不适用，不要照做。** 它们被移出 `决策/`（真值源）与本目录的活文档，正是为了避免误导。
>
> **为什么保留**：这些是**真实的实测记录**（例如"沙箱下 `git stash` 会丢分支 ref"是当时确实发生的事故），且若将来换回 WorkBuddy 环境仍有价值。删除会丢失历史。
>
> **移出日期**：2026-09-14 ｜ **移出依据**：`doc/standards/workspace-isolation.md` 原文前提"**本项目仅由 WorkBuddy 操作**"已失效。

---

## §0 为什么这些内容会污染

`决策/` 是真值源，其条目会被后来的 agent 当作**项目规则**遵从。但下面这些条目的主语是"**WorkBuddy 沙箱**"——那是一个**已不存在的环境**。

原子句（现已移出）：

> 多个 **WorkBuddy** 对话同时改码必须各自一个 git worktree
> **WorkBuddy** 沙箱里 `git stash push` 会丢弃分支 ref
> **WorkBuddy** Bash 默认 sandbox 拦截 git/gh 网络……用 `gh` CLI（绕沙箱）+ `dangerouslyDisableSandbox:true`

在新环境里读到的 agent 会：① 试图使用不存在的开关；② 把"绕沙箱"当成必要的操作步骤；③ 困惑于"我是不是 WorkBuddy"。**这就是污染。**

**判据（可复用于将来）**：
> 一条规则如果**主语是某个 agent 应用的环境**（而非项目本身），它就属于"环境记录"，不属于真值源。

---

## §1 WorkBuddy 环境特定条目（原在 `决策/跨域铁律.md`）

### D-2026-07-23-012（环境部分）｜多 WorkBuddy 对话并发 = 独立 worktree

- **原文**：多个 WorkBuddy 对话同时改码必须各自一个 git worktree；**禁止**共享同一工作树乱写。
- **环境特定之处**：多个**对话实例**共享同一工作区，是 WorkBuddy 的会话模型。
- **当时的 Drift**：2026-07-23 多对话同 worktree 改码，缓存/草稿互相覆盖。
- **通用部分已留在真值源**（见 §4）：**多 agent/多实例并发改码时，工作树必须隔离**。这条与具体是哪个 agent 无关。

### D-2026-07-27-013（整条）｜WorkBuddy 沙箱的 git 写入陷阱

- **原文**：WorkBuddy 沙箱里 `git stash push` 会**丢弃分支 ref**；`git checkout`/`merge` 大量写文件时**部分文件静默丢失**（工作树被部分删除）。**保留未提交改动勿用 `git stash`**。
- **来源**：实测 2026-07-27（B3/B4 翻案当晚，工作树被静默删 39 文件）。
- **Drift 记录**：🟥 2026-07-27 stash 后分支 ref 丢失；checkout/merge 后 39 文件被静默删（已用 `git checkout HEAD --` 还原）。
- **为何按 WorkBuddy 特定处理**：这是该沙箱实现的行为，非 git 本身语义。
- **⚠️ 但保留一条通用警示**：**改动量大时，先用 `git status` 确认文件在位再继续** —— 任何环境下都值得做。

### D-2026-07-27-016（环境部分）｜GitHub 推送绕沙箱

- **原文**：WorkBuddy Bash 默认 sandbox 拦截 git/gh 网络。`git push` 走 gh-proxy `insteadOf` 重写会被 `Connection reset`。**可靠路径**：用 `gh` CLI（绕沙箱）+ `dangerouslyDisableSandbox:true`。
- **环境特定之处**：`dangerouslyDisableSandbox` 是 **WorkBuddy 特有的工具参数**，DSH 无此概念。
- **当时 Drift**：多次 push 失败 → 改 `git config --local --unset-all 'url.https://gh-proxy.com/...insteadof'`（local+global 两层、小写键）绕开。
- **通用部分已留在真值源**（见 §4）：**`gh` CLI 是本项目的 GitHub 操作工具**。

---

## §2 WorkBuddy 专属机制（原在 `doc/standards/workspace-isolation.md`）

### 2.1 会话内缓存收口：`.workbuddy/env/cache.env.ps1`

```powershell
. .\.workbuddy\env\cache.env.ps1
```

覆盖 7 个变量：`HF_HOME` `HF_HUB_CACHE` `PIP_CACHE_DIR` `npm_config_cache`
`PLAYWRIGHT_BROWSERS_PATH` `UV_CACHE_DIR` `ELECTRON_CACHE`。

- **环境特定**：该脚本位于 `.workbuddy/`（**不在版本控制内**，被 `.gitignore:19` 忽略）——任何 clone 都拿不到。
- **文件当前仍在磁盘**（`.workbuddy/env/cache.env.ps1`），但**只对当时那个环境有效**。

### 2.2 永久收口：用户级 `setx`

把 7 个**用户级**环境变量 `setx` 到 `<workspace>/.cache/*`。`setx` 仅改用户级（非机器级），可逆。

- **环境特定**：这是对**那台机器的那套用户环境**做的改动，不属于项目。
- **通用部分**：缓存应收口到 `<workspace>/.cache/`（见 §4）。

### 2.3 HOME 约定

- **原文**：WorkBuddy HOME = `C:/Users/<user>/.workbuddy`；若发现落到 `D:/c/...` 立即纠正。
- **环境特定**：`HOME` 指向 `.workbuddy` 是该 agent 的约定。

### 2.4 草稿路径

- **原文**：草稿写 `.workbuddy/tmp/`（被 `.gitignore` 的 `**/tmp/` 忽略），或需留痕的放 `archive/agent-scratch-YYYYMMDD/`（入 VCS）。
- **环境特定**：`.workbuddy/tmp/` 是 WorkBuddy 的草稿目录。
- **实测状态（2026-09-14）**：该目录**最后活动停留在 2026-08**（`block3-assertions.md`），此后无新写入 —— 佐证环境已切换。

---

## §3 Codex 环境的约定（原在 `archive/AGENTS.codex-legacy.md`）

该文件（2026-08-03 的 Codex 项目级注入指令）已因**会被自动注入**而更名为 `AGENTS.codex-legacy.md`，不再作为指令生效。其要点摘录：

| 约定 | 内容 |
|---|---|
| 记忆机制 | Codex 无"每轮硬塞 reminder"，靠 `AGENTS.md` 注入 + 自带 `memories` + session jsonl |
| `.workbuddy/` 立场 | 视为**外部副产物**，只读不写 |
| 改码方式 | 用独立 git worktree，不在共享 worktree 乱写 |
| 硬约束 | 禁外溢盘符根；草稿 `.workbuddy/tmp/` 或 `archive/agent-scratch-YYYYMMDD/` |
| gh CLI | 须 `dangerouslyDisableSandbox:true`；push 走 gh-proxy insteadOf；workflow 须 fine-grained PAT |

---

## §4 从这些条目中**提炼出的通用规则**（已留在真值源）

剥离环境外壳后，真正属于**项目**的部分：

| # | 通用规则 | 留在何处 |
|---|---|---|
| 1 | **禁外溢盘符根**：`D:/c` `D:/d` `D:/Cache` `D:/tmp`（git-bash 下 `/tmp` = `D:/tmp`） | `决策/跨域铁律.md` D-011 |
| 2 | **缓存收口**到 `<workspace>/.cache/` | `决策/跨域铁律.md` D-011 |
| 3 | **多 agent/多实例并发改码时，工作树必须隔离**（与具体 agent 无关） | `决策/跨域铁律.md` D-012 |
| 4 | **git 还原用 `git checkout HEAD -- <path>` 而非 `git checkout -- <path>`**（后者从索引还原，索引已被改时是空操作）—— **这是 git 通用语义**，非沙箱怪癖 | `决策/跨域铁律.md` D-014 |
| 5 | **`gh` CLI 是本项目的 GitHub 操作工具** | `决策/工程规范.md` |
| 6 | **外溢看门狗**：`scripts/guard-workspace-paths.ps1`（dry-run，不自动删） | `决策/跨域铁律.md` D-011 |

> **判断标准（供将来复用）**：
> - 主语是「**某 agent 的沙箱/工具**」→ 环境记录（本文件）
> - 主语是「**git / 项目 / 工作区**」→ 真值源（`决策/`）
>
> 特别注意 **第 4 条**：它源于沙箱事故被发现，但结论是 **git 本身的语义**——任何环境都成立，因此**留在真值源**。**"发现于某环境"≠"仅适用于该环境"**，必须逐条验证。

---

## §5 相关文件索引

| 文件 | 说明 |
|---|---|
| `archive/AGENTS.codex-legacy.md` | Codex 项目级注入指令（已停用，仅存档） |
| `.workbuddy/` | WorkBuddy 记忆与草稿（**不在版本控制内**，gitignore） |
| `.workbuddy/memory/MEMORY.md` | WorkBuddy 的项目记忆指针（30 份日记忆在 `memory/`） |
| `reports/*`（2026-07 批） | 部分报告含 WorkBuddy 视角记录 |
