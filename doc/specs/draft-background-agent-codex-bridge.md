# Spec（草稿）— background-agent 插件化：AgentProvider 统一抽象（Codex 默认）

> 状态：**草稿** — 待用户验收后转正式（按 `决策/README.md` §0 治理）。
> 日期：2026-08-13 初版（Codex 桥接）/ 2026-08-14 升级（AgentProvider 插件化）
> 关联：`决策/服务-Hermes.md`（D-048）、`决策/服务-background-agent.md`（D-049）、`doc/subsystems/hermes-integration.md`、ADR-0012（Local Wiki 命名空间）
> 源项目：jd-opensource/JoyAI-VL-Interaction `services/background-agent/codex_api/`（原项目保留的 Codex CLI shim）

---

## 1. 背景与目标

- 现状（2026-08-13）：生产 background-agent 是 **Hermes shim**（:8079 → gateway :8642）。用户从未真正用过 Hermes 桥接，且担心其环境（`D:\Workspace\hermes-data`）之前被改坏过，不想冒险。
- 目标（2026-08-13）：**切到 Codex 桥接**（codex CLI 子进程），Hermes 保留。
- 升级（2026-08-14，用户拍板"插件选择"）：**AgentProvider 统一抽象**——background-agent 是**插件选择器**不是"主/备回退"；统一入口 + 工厂按 `BACKGROUND_AGENT_PROVIDER` 选实现，未来任意 agent（Claude Code / vLLM agent…）实现接口即插即用。
- 关键契约：`POST /v1/solve`（SolveRequest/SolveResponse）**不变**，webui 零改动；Local Wiki recall（D-049）在共享层保留。

## 2. 架构（AgentProvider 插件化）

```
webui ── BACKGROUND_AGENT_API_URL=http://127.0.0.1:8079 ──► agent_app:app（统一入口 :8079）
                                                              │
                              BACKGROUND_AGENT_PROVIDER（默认 codex，工厂选择，非回退）
                                                              ▼
                                                     create_agent_provider(name)
                                                              │
                              ┌───────────────────────────────┴────────────────┐
                              ▼ codex                          ▼ hermes（可选）  ▼ 未来 agent
                         CodexProvider                    HermesProvider      （实现 AgentProvider）
                         codex CLI 子进程                  hermes gateway :8642    即插即用
                         CODEX_HOME=~/.codex
                              └─────────────── 共享契约层 agent_provider.py ──────────────┘
                     SolveRequest/SolveResponse + _enrich_with_memory（D-049）+ build_prompt + 工具
```

### 关键点

| 项 | 值 |
|---|---|
| 统一入口 | `agent_app:app`（:8079，`run-windows.ps1` 固定指向；provider 由 env 决定） |
| 插件选择 | `BACKGROUND_AGENT_PROVIDER=codex\|hermes`（默认 **codex**）；**语义是选择不是回退** |
| 工厂 | `create_agent_provider(name)`——未知名字 **fail-loud**（ValueError，禁静默 fallback，约法三章） |
| 共享契约层 | `agent_provider.py`：ABC + models + recall（D-049）+ prompt + bounded/limit 工具（两 shim 重复代码去重） |
| codex CLI | 0.142.4（npm 全局，已登录 ChatGPT） |
| CODEX_HOME | 指向用户 `~/.codex`（复用 config.toml + auth.json，**不复制敏感文件进工作区**） |
| codex workspace | `<repo>/agent-workspace`（启动时自动创建） |
| 模型源 | 用户本地 relay `127.0.0.1:57321`（MiniMax-M3 等；由用户启动） |
| 安全 | YOLO 模式（`--dangerously-bypass-approvals-and-sandbox`），仅绑 127.0.0.1 |

| 项 | 值 |
|---|---|
| shim 端口 | :8079（不变，`CODEX_API_PORT`） |
| 切换开关 | `BACKGROUND_AGENT_PROVIDER=codex\|hermes`（默认 **codex**） |
| codex CLI | 0.142.4（npm 全局，已登录 ChatGPT） |
| CODEX_HOME | 指向用户 `~/.codex`（复用 config.toml + auth.json，**不复制敏感文件进工作区**） |
| codex workspace | `<repo>/agent-workspace`（run-windows.ps1 自动创建） |
| 模型源 | 用户本地 relay `127.0.0.1:57321`（MiniMax-M3 等；由用户启动） |
| Local Wiki recall | 已移植 `_enrich_with_memory`（同 hermes_api，fail-open + WARNING 日志） |
| 安全 | YOLO 模式（`--dangerously-bypass-approvals-and-sandbox`），仅绑 127.0.0.1 |

## 3. 改动清单

### 3.1 `services/background-agent/agent_provider.py`（新增，共享契约层）
- AgentProvider ABC（`solve` + `health` 抽象接口）+ `create_agent_provider(name)` 工厂（未知名 fail-loud）
- FrameInput/SolveRequest/SolveResponse（单一来源，两个 provider 复用同一类型）
- `_enrich_with_memory`（D-049 recall，从两 shim 去重上移）+ `build_prompt_text` + bounded/limit/decode 工具

### 3.2 `services/background-agent/agent_app.py`（新增，统一入口）
- 单一 FastAPI app（`/health` + `/v1/solve`），进程级缓存 provider（env 首次解析）
- 启动：`uvicorn agent_app:app`（run-windows.ps1 固定指向，不再按 provider 换模块）

### 3.3 `services/background-agent/codex_api/main.py`（重构为 CodexProvider）
- 契约层改用 agent_provider（models/recall/prompt/工具）；保留 codex CLI 子进程执行 + Windows 兼容（taskkill 替代 os.killpg）+ JSONL 流式解析
- 保留 `app`（直接启动兼容）+ 符号 re-export（测试/兼容）

### 3.4 `services/background-agent/hermes_api/main.py`（重构为 HermesProvider）
- 契约层改用 agent_provider；保留 hermes gateway HTTP 转发 + 响应解析
- 保留 `app` + 符号 re-export

### 3.5 `services/scripts/run-windows.ps1`
- `Start-BackgroundAgent`：uvicorn 固定 `agent_app:app`，注入 `BACKGROUND_AGENT_PROVIDER`；codex 分支 `CODEX_HOME=~/.codex` + `agent-workspace` 自动创建；hermes 分支 HERMES_API_KEY 注入
- 状态描述同步显示 provider

### 3.6 测试
- `tests/test_codex_api_recall.py`（6 项）+ `tests/test_hermes_api_enrich*.py`（原 13 项）：monkeypatch 目标改为共享层 `agent_provider.httpx` / 常量 / logger（实现位置上移）
- `tests/test_agent_provider_factory.py`（新增 5 项）：工厂返回正确实现 / 未知名 fail-loud / 大小写容错 / 两 provider 契约类型同一身份

## 4. 验证结果（2026-08-13 初版 + 2026-08-14 插件化）

| 验证项 | 结果 |
|---|---|
| 导入 / py_compile（agent_provider / agent_app / codex_api / hermes_api） | ✅ |
| PS 脚本语法解析 | ✅ PS PARSE OK |
| 测试 `services/background-agent/tests` | ✅ **24 passed**（含新增工厂 5 + recall 6 + hermes 13） |
| 工厂 `create_agent_provider`（codex/hermes/未知名） | ✅ 返回正确实现 + fail-loud |
| 统一入口 `agent_app` `/health`（provider=codex） | ✅ codex 0.142.4 / config_exists=true |
| `/v1/solve` 端到端 | ⏳ relay 57321 需在线（2026-08-13 已验证 completed；08-14 relay 未启动） |
| webui 测试（background_model 消费 /v1/solve 契约） | ✅ 785 passed（契约类型未变） |

## 5. 待办 / 风险

- [ ] **用户启动 relay 57321**（委派运行前提；未启动则委派失败）
- [ ] 正式环境重启栈验证（webui delegate 真机，验收计划 C1-C6）
- [ ] codex-home 目录（原项目默认 CODEX_HOME）已不使用，保留不动
- [ ] Hermes 链路保留可切回（`BACKGROUND_AGENT_PROVIDER=hermes`），未真机回归
- [ ] 文档同步：`doc/subsystems/hermes-integration.md` 已补 §12（2026-08-14）
