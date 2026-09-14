# Hermes-agent 集成（严格隔离）

> 状态：**P0 落地 + 闭环（v3.28）**。`prompts/bt-7274.txt` 加 Delegation Protocol 章节、`jarvis_session.py::_make_llm_callback` 调 `BackgroundModelService.handle_foreground_response` 触发 `</delegation>` → shim(8079) → gateway(8642) → MiniMax M2 + web_extract → `background_result_ready` WS 广播。E2E 烟测：BT-7274 4-case 行为符合预期 + 真实查 Cyberpunk 螳螂帮攻略 11s 拿到 MiniMax 返回。
> 配套文档：`doc/subsystems/jarvis-mode.md` §6 + `doc/local/tech-local.md §3.6` + `services/background-agent/hermes_api/`。
> **2026-07-23 更新**：[Local Wiki] 已落地（shim 委派前先 recall memory-store）；Hermes 本体/配置位置统一为 **`D:\Workspace\hermes-data`**（前 agent 曾误用 `$LOCALAPPDATA\hermes` 并改坏环境，见 §11）。

---

## 0. 核心原则

> **Hermes-agent 是"工具层"，不是"角色层"。**
> - BT-7274 是 webui/webinfer 的主角色
> - Hermes 是被委派的工具（编程、搜索、查询）
> - **两套系统严格隔离**——人格/记忆/Skills/Provider 都独立

---

## 1. 三层架构

```text
┌────────────────────────────────────────────────────────┐
│ Hermes-agent（独立 loop）                                │
│  - 人格: SOUL.md（Hermes 自己的）                        │
│  - 记忆: MEMORY.md / USER.md（Hermes 自己的）            │
│  - Skills: Hermes 自己的                                │
│  - Provider: 18+ providers，用户 `hermes model` 切换     │
│  - 用法: 用户直接用 Hermes CLI / Telegram / Discord      │
└────────────────────────────────────────────────────────┘
            ↑ （通过 hermes gateway 调用，OpenAI 兼容）
            │  hermes_api shim 只做协议转换，不解析人格/记忆
            │
┌────────────────────────────────────────────────────────┐
│ JoyAI webui / webinfer（我们的主对话）                    │
│  - 人格: BT-7274（prompts/bt-7274.txt）                  │
│  - 记忆: memory-store（bt-7274:* 命名空间）              │
│  - 用法: 浏览器 webui + 唤醒词 + 全双工                   │
└────────────────────────────────────────────────────────┘
            ↑ （`</delegate>` 决策 token 触发）
            │
┌────────────────────────────────────────────────────────┐
│ Hermes via hermes_api shim（被委派的工具）                │
│  - 人格: 继承 Hermes（不感知 BT-7274）                   │
│  - 记忆: 继承 Hermes                                     │
│  - 用法: 只在 webui 的 </delegate> 触发                  │
│  - 契约: POST /v1/solve（SolveRequest/SolveResponse）    │
└────────────────────────────────────────────────────────┘
```

---

## 2. 严格隔离原则

### 2.1 人格隔离

| 维度 | Hermes | BT-7274 (我们) | 规则 |
| - | - | - | - |
| **系统 prompt** | Hermes SOUL.md | BT-7274 prompts/bt-7274.txt | shim **不传** system 字段给 hermes |
| **输出语言** | Hermes 自己的风格 | BT-7274 角色化 | 输出回到 BT-7274 上下文，自动角色化 |
| **称呼用户** | Hermes 自己 | BT-7274 自己的 | 互不影响 |

**shim 行为**：
```python
# ✅ 正确：只传 task，不传 BT-7274 人格
payload = {
    "model": "auto",  # 委托给 hermes gateway
    "messages": [
        {"role": "user", "content": req.question}
    ],
    # 不传 system 字段（让 hermes 用自己的 SOUL.md）
    # 不传 context 字段（BT-7274 记忆保留在主对话链路）
}

# ❌ 错误：把 BT-7274 的 system prompt 也传给 hermes
# payload["messages"].insert(0, {"role": "system", "content": bt7274_prompt})
# 这样会让 Hermes "认为"自己也是 BT-7274，破坏人格隔离
```

### 2.2 记忆隔离

| 维度 | Hermes | BT-7274 (我们) | 规则 |
| - | - | - | - |
| **存储** | `D:\Workspace\hermes-data\memories\` | `memory-store` 服务（:8996） | **不共享** |
| **命名空间** | MEMORY.md / USER.md | `bt-7274:*` | **不交叉** |
| **用户偏好** | Hermes 用户的偏好 | BT-7274 用户的偏好 | **各管各的** |
| **对话历史** | Hermes 自己的 | BT-7274 自己的 | **不共享** |

**为什么不需要共享**：
- 用户在 Hermes（编程）说"用 TypeScript"——这是 Hermes 用户偏好
- 用户对 BT-7274（游戏）说"我不喜欢恐怖游戏"——这是 BT-7274 用户偏好
- **场景不同**——共享反而混乱

**实现**：
- Hermes 跑它自己的 loop（CLI / Telegram / Discord）
- 我们的 webui 只在 `</delegate>` 时调用 hermes gateway
- 两层之间**协议清晰**：webui 不知道也不需要知道 hermes 的内部状态

### 2.3 Skills 隔离

| 维度 | Hermes | BT-7274 (我们) | 规则 |
| - | - | - | - |
| **Skills 目录** | `~/.hermes/skills/` | `prompts/bt/*.md`（未来） | **不共享** |
| **典型 Skills** | git commit, code review | 查询游戏攻略, 读 wiki | 各管各的 |

**不冲突**——独立命名空间，Hermes 不会调用 BT-7274 skills，反之亦然。

### 2.4 Provider 统一

**shim 端不维护 provider 配置**——完全委托给 hermes gateway。

**shim 启动**：
```python
# services/background-agent/hermes_api/main.py
HERMES_API_URL = os.getenv("HERMES_API_URL", "http://127.0.0.1:8642/v1")
HERMES_API_KEY = os.getenv("HERMES_API_KEY") or os.getenv("API_SERVER_KEY", "")
```

**shim 转发**：
```python
async def solve(req: SolveRequest) -> SolveResponse:
    # ✅ shim 只转发，不解析 provider
    resp = await httpx.AsyncClient().post(
        f"{HERMES_API_URL}/chat/completions",
        json={
            "model": "hermes-agent",  # 委托给 hermes gateway 解析
            "messages": [{"role": "user", "content": req.question}],
        },
        headers={"Authorization": f"Bearer {HERMES_API_KEY}"} if HERMES_API_KEY else {},
        timeout=300,
    )
    return parse_response(resp)
```

**用户切换模型**（`hermes model` 一条命令）：
```bash
hermes model  # 交互式选择
hermes model --provider openai --model gpt-5.5
hermes model --provider anthropic --model claude-sonnet-4.6
hermes model --provider openrouter --model anthropic/claude-sonnet-4.6
```

**shim 自动跟着切换**——零配置。

---

## 3. 数据流时序

```text
[1] 用户在 webui 问 BT-7274："赛博朋克 2077 螳螂帮怎么打？"
[2] webinfer 收到，BT-7274 角色 LLM 思考
[3] BT-7274 决定需要外部信息 → 输出 </delegate> 决策 token
[4] webui 解析 </delegate> → 调用 hermes_api shim
[5] shim 转发给 hermes gateway：{"messages":[{"role":"user","content":"查赛博朋克 2077 螳螂帮攻略"}]}
[6] hermes gateway 用当前 provider 推理（用户用 `hermes model` 切换的）
[7] hermes 可能调用工具（web search、读文件等）
[8] hermes 返回 {"choices":[{"message":{"content":"<summary>螳螂帮打法：先用赛博精神病秒掉两个小的...</summary>"}}]}
[9] shim 解析 <summary> 标签 → 返回 SolveResponse
[10] webui 收到 → 把 summary 拼回 BT-7274 上下文
[11] BT-7274 重新生成响应（角色化包装）："螳螂帮打法，我建议..."
[12] TTS 播报
```

**关键点**：
- **第 6 步**：Hermes 完全独立人格/记忆，与 BT-7274 无关
- **第 11 步**：BT-7274 重新包装（角色化），Hermes 输出"脱壳"
- **shim 只在 4-9 步介入**——纯协议转换

---

## 4. hermes_api shim 接口（不变）

```python
# /v1/solve 端点
class FrameInput(BaseModel):
    image_url: str                       # JPEG data URL（必填）
    timestamp: float | None = None       # 帧时间戳（可选）
    timestamp_kind: str | None = None    # 时间戳类型（可选）
    pts: int | None = None               # 演示时间戳（可选）

class SolveRequest(BaseModel):
    session_id: str                      # 会话 ID（必填）
    task_id: str                         # 任务 ID（必填）
    question: str                        # 用户的具体问题（必填）
    foreground_text: str = ""            # 前台文本（可选）
    frames: list[FrameInput] = []        # 多模态分帧输入（可选）
    max_subagents: int | None = None     # 子代理上限（可选）
    timeout_seconds: float | None = None # 超时秒数（可选）

class SolveResponse(BaseModel):
    status: Literal["completed", "failed", "timeout"]  # 处理状态
    text: str                            # 结果文本（取代旧 summary 字段）
    thread_id: str | None = None         # 后台线程 ID（可选）
    usage: dict[str, Any] | None = None  # token 用量统计（可选）
    duration_ms: float                   # 处理耗时（毫秒，必填）
    events_digest: dict[str, Any] = {}   # 事件摘要（可选，默认空）
    error: str | None = None             # 错误信息（可选）
```

> **契约已演进（非「完全不变」）**：shim 契约已演进为多模态/分帧结构（见上）。
> webui 端须按上述**实际字段**对接；旧文档描述的 `context` / `summary` / `tool_calls`
> 字段已不存在，请勿再依赖。


---

## 5. 启动顺序

```text
[1] 启动 hermes gateway（统一 HOME=D:\Workspace\hermes-data）
    # 推荐用封装脚本（已修正到 D:\Workspace\hermes-data）：
    pwsh services/background-agent/scripts/start-hermes-gateway.ps1
    # 或手动：hermes gateway  # 监听 8642（需先 set HERMES_HOME=D:\Workspace\hermes-data）
[2] 配置 provider
    $ hermes model    # 选 OpenAI/Anthropic/OpenRouter/...
[3] 启动 hermes_api shim（我们的服务）
    $ python services/background-agent/hermes_api/main.py --port 8079
[4] 启动 webui
    $ python services/webui/.../server.py --port 8099
[5] webui 启动时探测 hermes gateway 是否可达
    - 可达 → 显示 "Hermes 已连接" 状态
    - 不可达 → 降级到本地 codex_api（代码保留）
```

---

## 6. 故障转移

```text
[主路径] hermes gateway 可达
  → 委派给 hermes

[降级路径] hermes gateway 不可达
  → 切换到 codex_api（原项目保留）
  → 或回退到本地 llama-server

[错误处理] hermes 返回错误
  → 解析错误消息
  → 返回 SolveResponse(status="failed", error=...)
  → webui 显示 "委派失败: ..."
```

---

## 7. Hermes-agent 配置文件位置

```
D:\Workspace\hermes-data\
├── config.yaml     # Settings (model, terminal, TTS, compression, etc.)
├── .env            # API keys and secrets
├── auth.json       # OAuth provider credentials (Nous Portal, etc.)
├── SOUL.md         # Primary agent identity (slot #1 in system prompt)
├── memories/       # Persistent memory (MEMORY.md, USER.md)
├── skills/         # Agent-created skills
├── cron/           # Scheduled jobs
├── sessions/       # Gateway sessions
└── logs/           # Logs (errors.log, gateway.log)
```

**我们的 shim 不读这个目录**——只通过 hermes gateway 的 OpenAI 兼容 API 通信（`D:\Workspace\hermes-data` 是 Hermes 真 HOME，与 `$LOCALAPPDATA\hermes` 无关）。

---

## 8. 决策项（已拍板）

- [x] 严格隔离（人格/记忆/Skills/Provider 全部独立）
- [x] shim 不传 system 字段给 hermes
- [x] shim 不读 hermes 内部配置
- [x] shim 只做协议转换（`/v1/solve` ↔ hermes OpenAI API）
- [x] 用户用 `hermes model` 切换 provider，shim 自动跟随
- [x] webui 端 `/v1/solve` 契约不变
- [x] **2026-08-13：background-agent 默认后端切 Codex**（Hermes 保留可切回）——见 §12

---

## 12. Codex 桥接（2026-08-13 起默认后端）

> **状态**：已实现 + 单测/端到端验证，待真机验收（2026-08-14 验收计划 C1-C6）。
> 关联 spec：`doc/specs/background-agent-codex-bridge.md`；代码：`services/background-agent/codex_api/main.py`。

### 为什么切

- Hermes 桥接从未真机使用过；用户对 Hermes 环境（`D:\Workspace\hermes-data`）被历史 agent 搞乱过有顾虑，不想冒险。
- 原项目自带 `codex_api`（Codex CLI shim）实现完整，本地 `codex-cli 0.142.4` 已装、已 ChatGPT 登录——切 Codex **完全绕开 Hermes 环境**，风险为零。

### 架构（AgentProvider 插件化，2026-08-14 升级；与 Hermes 并存，env 选择）

```
webui ── :8079 ──► agent_app:app（统一入口）
                     └─ create_agent_provider(BACKGROUND_AGENT_PROVIDER)
                          ├─ codex（默认）：CodexProvider → codex CLI（CODEX_HOME=~/.codex）
                          └─ hermes（可选）：HermesProvider → hermes gateway :8642
                     └─ 共享契约层 agent_provider.py（models/recall D-049/prompt/工具）
```

- **端口**：`:8079` 不变（`CODEX_API_PORT`），webui 零改动。
- **选择开关**：`BACKGROUND_AGENT_PROVIDER=codex|hermes`（默认 **codex**）——语义是**插件选择**不是主/备回退；未知名 fail-loud。
- **CODEX_HOME**：指向用户 `~/.codex`（复用 config.toml + auth.json，**不复制敏感文件进工作区**）。
- **工作区**：`<repo>/agent-workspace`（启动时自动创建）。
- **模型源**：用户本地 relay `127.0.0.1:57321`（MiniMax-M3 等，用户自启动）。
- **Local Wiki recall（D-049）**：共享层 `agent_provider._enrich_with_memory`（fail-open + WARNING 日志）——两个 provider 共用一份实现，契约保留。

### 关键改动（2026-08-13 + 2026-08-14 插件化）

| 文件 | 内容 |
|---|---|
| `services/background-agent/agent_provider.py`（新） | AgentProvider ABC + 工厂 + 共享契约层（models/recall D-049/prompt/工具） |
| `services/background-agent/agent_app.py`（新） | 统一 FastAPI 入口（/health + /v1/solve），按 env 选 provider |
| `services/background-agent/codex_api/main.py` | 重构为 CodexProvider（CLI 子进程 + Windows 兼容 taskkill） |
| `services/background-agent/hermes_api/main.py` | 重构为 HermesProvider（gateway HTTP 转发） |
| `services/scripts/run-windows.ps1` | uvicorn 固定 `agent_app:app` + 注入 `BACKGROUND_AGENT_PROVIDER` |
| `tests/test_agent_provider_factory.py`（新） | 工厂/契约类型 guard（5 项） |

### 验证（2026-08-13 实测 + 2026-08-14 插件化冒烟）

- codex exec 直连：relay 在线时 MiniMax-M3 正常回复（含系统命令/联网搜索能力实测）
- 统一入口 `agent_app` `/health`：provider=codex、codex 0.142.4、config_exists 正常
- `/v1/solve` 端到端：2026-08-13 status=completed（35s 带回 `<summary>`）；08-14 冒烟时 relay 未在线（环境依赖）
- 测试：background-agent 24/24 绿（含工厂 guard 5 项）

### 已知依赖

- **relay 57321 必须在线**（用户启动）；掉线 → 委派失败（shim fail-open 不阻塞主对话）。
- codex 能力依赖 relay 模型工具调用支持；若模型不支持工具调用会退化（"不知道今天几号"），属模型层依赖。

---

## 9. 关联文档

- `doc/subsystems/jarvis-mode.md §6`（决策 token `</delegate>` 触发）
- `doc/local/tech-local.md §3.6`（shim 实现）
- `services/background-agent/hermes_api/main.py`（代码）
- `services/background-agent/codex_api/main.py`（Codex shim 代码，2026-08-13 起默认）
- `doc/specs/background-agent-codex-bridge.md`（Codex 桥接 spec 草稿）
- 外部：https://hermes-agent.nousresearch.com/docs/

---

## 11. ⚠️ 历史事故：前 agent 未按文档执行（2026-07-23 只读审计）

> 本会话对 `D:\Workspace\` 全程**只读**（未创建/修改任何文件），仅核实现状。

**问题**：之前有 agent 未按本文档与 `start-hermes-gateway.ps1` 执行，把 Hermes 环境/配置搞乱。只读审计实证：

1. **启动器丢失**：`D:\Workspace\hermes-data\bin\` 里**没有活跃 `hermes.cmd`**，只剩 `hermes.CMD.backup-via-hermesbat` 与 `hermes.CMD.bak.20260715_012052` 两个备份；真实 CLI exe 在 `D:\Workspace\hermes-agent\venv\Scripts\hermes.exe`。
2. **HOME 被改错**：上述备份内容把 `HERMES_HOME` 指向**陈旧的 `C:\Users\<user>\AppData\Local\hermes`**，并用 `D:\anaconda3\envs\hermes_cli\Scripts\hermes.exe`——与当前 venv（`D:\Workspace\hermes-agent\venv\Scripts\hermes.exe`）不一致。
3. **真 HOME 是 `D:\Workspace\hermes-data`**：含 `memories/ skills/ sessions/ logs/ SOUL.md state.db config.yaml .env`（即 Hermes 实际运行态）。
4. **`.env` 无 gateway auth key**：`D:\Workspace\hermes-data\.env` 含各 provider key（MINIMAX_API_KEY 等）但**无 `API_SERVER_KEY`/`HERMES_API_KEY`**，故 gateway 以 auth-disabled 运行（与 shim 契约一致：shim 仅在 key 存在时发 `Authorization`）。

**已修正（本会话）**：
- `start-hermes-gateway.ps1`：HERMES_HOME 固定 `D:\Workspace\hermes-data`；候选启动器优先 `bin\hermes.cmd`，缺失时回退到真实 exe `D:\Workspace\hermes-agent\venv\Scripts\hermes.exe` 与 `gateway-service\Hermes_Gateway.cmd`；整体加载 `D:\Workspace\hermes-data\.env`。
- 本文档 §2.2/§2.4/§5/§7 全部把 Hermes 位置统一为 `D:\Workspace\hermes-data`。

**待处理（D:\Workspace 写入，不在本会话范围）**：把 `hermes.cmd` 恢复进 `D:\Workspace\hermes-data\bin\`（从 `.bak`/`.backup` 还原并核对 venv python），否则脚本会走 exe 兜底路径。

---

## 10. 变更记录

| 日期 | 版本 | 变更 | 作者 |
| - | - | - | - |
| 2026-07-09 | v1.0 | 初版：Hermes-agent 严格隔离方案 | Codex |
| 2026-07-13 | v3.27 | 落地接入：`$env:LOCALAPPDATA\hermes\bin\hermes.cmd` wrapper（venv python → `python -m hermes_cli.main`）解决 `bin\hermes.cmd` 不存在的问题；`Start-Hermes` 用 `API_SERVER_HOST/PORT/KEY` env；`services\background-agent\background-agent.env` 与 `services\scripts\run-windows.env` 同步 `HERMES_API_KEY`；gateway `/health` 200 OK、`/v1/models` 返回 `hermes-agent`、shim `/health` 透出 `hermes_gateway:200`；smoke 调用 `/v1/solve` 返回中文"烟测通过。" | Codex |
| 2026-07-13 | v3.28 | 闭环触发：`prompts/bt-7274.txt` 加 **Delegation Protocol (P-D)** 章节（外部查才触发、tag 必须结尾、foreground 短句、self-contained 问题、3 个中英示例）；`jarvis_session.py::_make_llm_callback` 在 broadcast 后调 `BackgroundModelService.handle_foreground_response(text, metrics)`，从 `sessions[session_id]["background_service"]` 拿实例。E2E：4-case 行为烟测（chitchat/已知识 → 不触发、外查/天气/cyberpunk → 触发并自动改写）+ 真实查询 11s 拿到 MiniMax M2 整理后的攻略。不破坏 hermes env：`HERMES_API_KEY` env 文件不动，shim/gateway 用同 key 由 env 注入 | Codex |
| 2026-07-23 | v3.29 | Hermes 位置统一为 D:\Workspace\hermes-data（修正前 agent 环境污染）；[Local Wiki] 委派前 recall 落地进 hermes_api shim；psql 复用记忆路线取消（ADR-001，避免污染 hermes 原记忆） | Architect |
| 2026-08-13 | v3.38+ | 新增 §12 Codex 桥接（默认后端切 codex，Hermes 保留）；`codex_api` 移植 Local Wiki recall（D-049）+ Windows 兼容；`run-windows.ps1` 加 `BACKGROUND_AGENT_PROVIDER` 开关 | workbuddy（历史环境） |