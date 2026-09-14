# StreamingHarness Background-Agent Shim

> 中文文档: [README.zh-CN.md](README.zh-CN.md)

Two FastAPI shims live in this package, both preserving the exact same
`POST /v1/solve` contract the webui already speaks:

| shim | module | runtime | best for |
| --- | --- | --- | --- |
| **Codex API (default)** | `codex_api/main.py` → `CodexProvider` | a system `codex` CLI subprocess (reuses `~/.codex` auth/config) | Windows + Linux; the current default backend since 2026-08-13 (see `doc/specs/background-agent-codex-bridge.md`) |
| **Hermes API (retained)** | `hermes_api/main.py` → `HermesProvider` | a local [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) HTTP gateway (OpenAI-compatible, port 8642) | switchable; set `BACKGROUND_AGENT_PROVIDER=hermes` to select |

> **Plugin architecture (2026-08-14)**: both shims implement the shared
> `AgentProvider` interface (`services/background-agent/agent_provider.py`) and
> are served by the single `agent_app:app` entry. `BACKGROUND_AGENT_PROVIDER`
> (`codex` default | `hermes`) *selects the plugin* — it is a choice, not a
> fallback; an unknown name fails loudly. The webui
> (`services/webui/src/joy_interaction_webui/background_model.py`) only talks to
> `POST {BACKGROUND_AGENT_API_URL}/v1/solve`, so any agent implementing the
> interface is plug-and-play on port 8079.

## Which one is running?

```bash
curl http://127.0.0.1:8079/health
```

- The Codex shim returns `{ provider: "codex", codex_path, codex_version, config_path, config_exists, workspace, ... }`.
- The Hermes shim returns `{ provider: "hermes", "codex_api": "ok", "hermes_gateway": <int>, "model": "..." }`
  (the `codex_api` key is kept for backward-compatibility).

---

## Hermes 接入 (retained fallback; `BACKGROUND_AGENT_PROVIDER=hermes`)

The Hermes shim is a thin OpenAI-format translator that fronts a local
[hermes-agent](https://github.com/NousResearch/hermes-agent) gateway. The
gateway handles the actual agent loop, tool calls, `delegate_task`
sub-orchestration, and (optional) image generation. The shim just packages
the webui's `SolveRequest` into a multimodal `chat.completions` request and
unpacks the response back into the legacy `SolveResponse` shape.

> Since 2026-08-13 the default backend is **Codex** (below); Hermes is
> retained and switchable, not recommended as the default. Hermes's own
> environment (`D:\Workspace\hermes-data`) is never touched by the Codex path.

### 1. Install hermes-agent

```powershell
# One-shot PowerShell installer (Windows native, early-beta but functional):
iex (irm https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.ps1)
```

It drops the `hermes` CLI at `$env:LOCALAPPDATA\hermes\bin\hermes.cmd`.

### 2. Authenticate / pick a model provider

```powershell
hermes setup --portal          # opens the Nous portal for login / provider keys
# or edit $env:LOCALAPPDATA\hermes\.env manually with your OPENAI_API_KEY / OPENROUTER_API_KEY / ...
```

### 3. Tune `~/.hermes/config.yaml`

Key knobs to be aware of:

```yaml
agent:
  max_turns: 30
delegation:
  max_concurrent_children: 6   # mirrors CODEX_API_MAX_SUBAGENTS
```

### 4. Enable the HTTP gateway in `~/.hermes/.env`

```dotenv
API_SERVER_ENABLED=true
API_SERVER_KEY=replace-me-with-a-long-random-string
API_SERVER_CORS_ORIGINS=http://127.0.0.1:8079
```

### 5. Sanity check

```powershell
hermes doctor
```

### 6. Start the gateway (port 8642)

```powershell
cd services\background-agent
powershell -ExecutionPolicy Bypass -File scripts\start-hermes-gateway.ps1
```

This writes `hermes_gateway.pid` and `hermes_gateway.log` next to the script
and probes `GET /health` once the gateway is up.

### 7. Start the shim (port 8079)

```powershell
cd services\background-agent
powershell -ExecutionPolicy Bypass -File scripts\run-windows.ps1
```

This writes `hermes_api.pid` and `hermes_api.log` and probes
`GET /health` once uvicorn is serving.

The webui default `BACKGROUND_AGENT_API_URL=http://127.0.0.1:8079` keeps
working unchanged.

---

## Codex 接入 (default since 2026-08-13; Windows + Linux)

On Windows the shim is started by `services/scripts/run-windows.ps1`
(`Start-BackgroundAgent`, provider default `codex`). On Linux use:

```bash
./services/background-agent/scripts/run.sh
```

`run.sh` prefers the shared environment `services/.venv` created by the
install script. If that environment does not exist, it falls back to
`uv run` development mode.

The WebUI background client uses `http://127.0.0.1:8079` by default.
Override with:

```bash
export BACKGROUND_AGENT_API_URL=http://127.0.0.1:8079
```

### CODEX_HOME / auth

The shim reuses the user's existing `~/.codex` (`CODEX_HOME`, Windows path
`C:\Users\<user>\.codex`) for `config.toml` + `auth.json` — **no per-project
auth copy**. `run-windows.ps1` injects `CODEX_HOME`; the Linux `run.sh`
defaults to `<repo>/services/background-agent/codex-home` (override with
`CODEX_HOME=/path/to/.codex`). The shim runs `codex exec` with
`--ephemeral` so no session files are persisted.

### Workspace

`run.sh` uses `<repo>/agent-workspace` as the default Codex workspace and
creates it on startup; `run-windows.ps1` does the same.

### Local Wiki recall (D-049 contract)

`codex_api` includes the same `_enrich_with_memory` recall as `hermes_api`
(memory-store :8997 `/v1/blocks/recall`, scoped to `WIKI_RECALL_NAMESPACES`,
fail-open + WARNING log) — the D-049 contract is preserved on both backends.

### Security note (Codex path)

The Codex shim uses YOLO mode
(`--dangerously-bypass-approvals-and-sandbox`) so background tasks can run
without interactive approval. Treat it as a high-privilege process: wrap it
in Docker, run as an isolated user, or bind to localhost.

---

## Environment reference

Both shims share the same env-var surface (the Hermes shim reads everything
the Codex shim reads, plus its own prefix):

| variable | default | used by |
| --- | --- | --- |
| `CODEX_API_HOST` | `127.0.0.1` | shim bind host |
| `CODEX_API_PORT` | `8079` | shim bind port (webui targets this) |
| `CODEX_API_MAX_SUBAGENTS` | `6` | shim subagent cap, surfaces in prompt + (Hermes) `delegation.max_concurrent_children` |
| `CODEX_API_MAX_CONCURRENT_RUNS` | `2` | in-process asyncio semaphore |
| `CODEX_API_TIMEOUT_SECONDS` | `600` | upstream call timeout |
| `CODEX_API_MAX_FRAMES` | `50` | tail-truncate frame list |
| `HERMES_API_URL` | `http://127.0.0.1:8642/v1` | Hermes shim only |
| `HERMES_API_KEY` / `API_SERVER_KEY` | _(empty)_ | bearer token for the gateway |
| `HERMES_MODEL` | `hermes-agent` | model name sent in the chat completion body |
| `HERMES_GATEWAY_HOST` / `HERMES_GATEWAY_PORT` | `127.0.0.1` / `8642` | used by `start-hermes-gateway.ps1` and `/health` |

## Health Check

```bash
curl http://127.0.0.1:8079/health
```
