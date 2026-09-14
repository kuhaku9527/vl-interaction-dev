# 工作区隔离规范（Workspace Isolation Standard）

> 目的：杜绝 agent / 构建工具把文件外溢到工作区以外的盘符（`D:/c` `D:/d` `D:/Cache` `D:/tmp`），
> 让 JoyAI-VL-Interaction 这一套系统的所有产物都收口到本仓库工作树内。

适用角色：所有操作本项目的对话 / agent（前端、后端、测试、code-review 等）。

> ⚠️ **环境说明（2026-09-14 修订）**：本文件原写"**本项目仅由 WorkBuddy 操作**，不引入其它 agent 框架"——**该前提已失效**（现由 DSH 操作，将来也可能换）。
> 因此本文件按性质分两部分：
> - **§1–§2 通用规则**（与 agent 无关，**任何环境都适用**）
> - **§5 历史环境专属机制**（WorkBuddy 特有，已移出正文、仅存档）
>
> 当前环境的约束见 [`../environment-dsh.md`](../environment-dsh.md)。

---

## 1. 缓存收口（关键，杜绝复发）

所有构建 / 工具缓存必须落在 `<workspace>/.cache/`。

> 具体收口机制因环境而异：WorkBuddy 用 `.workbuddy/env/cache.env.ps1` + 用户级 `setx`（**历史机制，见 §5**）。
> **本环境（DSH）**：缓存已落在 `.cache/`（实测有 `deepsec/` 等），无需额外脚本。

### 1.1 会话内收口（脚本用）

> **WorkBuddy 历史机制**（已移出正文生效范围，见 §5）：dot-source `.workbuddy/env/cache.env.ps1`。
> **本环境（DSH）** 无此脚本依赖；若将来需要，可在启动脚本里直接 `export` 下列变量。

需收口的 7 个缓存变量（与工具无关，通用）：
`HF_HOME` `HF_HUB_CACHE` `PIP_CACHE_DIR` `npm_config_cache`
`PLAYWRIGHT_BROWSERS_PATH` `UV_CACHE_DIR` `ELECTRON_CACHE`

---

## 2. agent 路径纪律（通用规则）

1. **一律使用工作区绝对路径**。禁止依赖 `/tmp`、`D:/tmp`（git-bash 下 `/tmp` 解析为 `D:/tmp`）。
2. **草稿写工作区内**：一次性产物放工作区内的忽略目录（`.cache/`），
   或需要留痕的放 `archive/agent-scratch-YYYYMMDD/`（入 VCS）。
   > 原写"放 `.workbuddy/tmp/`"——那是 WorkBuddy 的环境约定，**已移出**（见 §5）。
   > 注（2026-09-14）：`agent-scratch-*` 约定**当前无实例**（历史唯一的 `20260808` 已退役），保留待用。
3. **多实例并发用独立 git worktree**，禁止共享同一工作树乱写（曾因此引发"共享工作树无声覆盖"事故）。此规则与具体 agent 无关。

---

## 3. 看门狗

`scripts/guard-workspace-paths.ps1`（dry-run，绝不自动删）扫描四盘符是否再冒 Joy 文件：
```powershell
pwsh -File scripts/guard-workspace-paths.ps1      # 0=无命中, 1=发现外溢
```
建议接入本地定时任务或 CI 门禁，发现外溢即告警人工处理。

---

## 4. 清理历史外溢（2026-07-23 已执行）

- `D:/Cache/playwright` → 迁移到 `workspace/.cache/playwright`（回归继续可用）。
- `D:/Cache/{electron,npm,pip,uv,huggingface}` → 删除（包管理器缓存，可自动重生成）。
- `D:/tmp/*`（103 项 agent 草稿）→ 全部迁移到 `archive/agent-scratch-20260808/`（入 VCS，零丢失）。
  > 2026-09-14 更正：原写 `agent-scratch-20260723/`——**该目录从未进入过 git 历史**（`git log --all -- '*agent-scratch*'` 只返回 `20260808`）。该目录本身也已随 2026-09-14 文档收口退役。
- `D:/d/AI/{workspace,envs,tmp_ruff}` 与 `D:/d/tmp/{ruff69,ruff-check-venv,rv3}` → 删除（spillover / lint 临时）。
- `D:/c/Users/<user>/.workbuddy` → 删除（错位 HOME，真实 HOME 在 `C:/Users/<user>/.workbuddy`）。

### 4.1 `npm_config_cache` 指向 hermes-agent —— **不是外溢，是正确的项目隔离**

> **2026-09-14 结论修正**：本条曾被我（AI）误判为「D-011 第一次被实测违反」并标 🔴。
> **用户质疑后复核，原判定错误。** 保留此条以记录推理错误的教训。

**实测**：用户级 `npm_config_cache` = `D:\Workspace\hermes-agent\.cache\npm`；其余 6 个缓存变量均正确指向本工作区。

**为什么这不是外溢**（证据）：

| 事实 | 证据 |
|---|---|
| `hermes-agent` 是**独立项目**，非本项目产物 | `git -C /d/Workspace/hermes-agent remote -v` → `NousResearch/hermes-agent.git` |
| 它是 **Node 项目**，有自己的 npm 需求 | 存在 `package.json` + `.npmrc`（engine-strict、min-release-age 等策略） |
| 它有**自己的** npm 缓存目录 | `/d/Workspace/hermes-agent/.cache/npm` 存在 |
| 它是本项目的**上游依赖** | `install/setup-hermes.ps1` 负责安装（`NousResearch/hermes-agent v0.17.0`） |

→ 该值是 **hermes-agent 为自身设置的项目级缓存隔离**，符合"每个项目缓存收口在自己的工作树内"的精神。

**D-011 的正确范围**：约束的是「**本项目**的产物收口在**本项目**工作树内」，**不是**「这台机器上所有 npm 缓存」。

**唯一真实观察（低危）**：用户级 `setx` 是全局的，故在**本项目**内跑 npm 时缓存也会落到 hermes-agent 目录。本项目 npm 用量极小（仅 `services/webui` 的 eslint/vitest），可忽略。若将来本项目 npm 用量上升，再考虑改用项目级 `.npmrc`（`cache=./.cache/npm`）覆盖。

> ⚠️ **推理教训（写在此处防重犯）**：看到「本项目的变量指向项目外」时，**必须先问「那个目录是什么、属于谁」**，再判定是否为外溢。
> 本例中「6 个变量正确、仅 1 个不同」本身就是反证 —— 环境被污染不会只改一个变量。
> 参见 `doc/README.md` 维护规则：**先验证前提，再下结论**。

> ⚠️ 上文提到的"WorkBuddy HOME"是**历史环境概念**。本环境（DSH）的约束见 [`../environment-dsh.md`](../environment-dsh.md)。

---

## 5. 历史环境专属机制（WorkBuddy，仅存档）

> **以下内容属于 WorkBuddy 环境，在当前环境（DSH）下不适用，不要照做。**
> 保留仅为历史追溯。完整记录见 [`../history-agent-environments.md`](../history-agent-environments.md) §2。

### 5.1 会话内缓存收口脚本

```powershell
# WorkBuddy 专用（脚本位于 .workbuddy/，不在版本控制内）
. .\.workbuddy\env\cache.env.ps1
```

### 5.2 永久收口：用户级 `setx`

```powershell
setx HF_HOME                   "D:\AI\workspace\JoyAI-VL-Interaction-main\.cache\huggingface"
setx HF_HUB_CACHE             "D:\AI\workspace\JoyAI-VL-Interaction-main\.cache\huggingface\hub"
setx PIP_CACHE_DIR            "D:\AI\workspace\JoyAI-VL-Interaction-main\.cache\pip"
setx npm_config_cache         "D:\AI\workspace\JoyAI-VL-Interaction-main\.cache\npm"
setx PLAYWRIGHT_BROWSERS_PATH "D:\AI\workspace\JoyAI-VL-Interaction-main\.cache\playwright"
setx UV_CACHE_DIR             "D:\AI\workspace\JoyAI-VL-Interaction-main\.cache\uv"
setx ELECTRON_CACHE           "D:\AI\workspace\JoyAI-VL-Interaction-main\.cache\electron"
```
> 改前务必备份旧值（见 `reports/env-backup-20260723.txt`），`setx` 仅改**用户级**，可逆。

### 5.3 HOME 约定

WorkBuddy HOME = `C:/Users/<user>/.workbuddy`。若发现产物落到 `D:/c/...`，立即纠正。

### 5.4 草稿路径

`.workbuddy/tmp/`（被 `.gitignore` 的 `**/tmp/` 忽略）。
**实测状态（2026-09-14）**：该目录**最后活动停在 2026-08**，此后无新写入 —— 佐证环境已切换。
