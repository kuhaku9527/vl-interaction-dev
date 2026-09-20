# ruff: noqa: RUF002 RUF003
"""background-agent 统一入口（AgentProvider 插件化，2026-08-14）.

单一 FastAPI app，按 ``BACKGROUND_AGENT_PROVIDER``（默认 codex）选择 provider 插件：
  * codex  -> CodexProvider（codex_api.main）
  * hermes -> HermesProvider（hermes_api.main）
  * 未来 agent -> 实现 AgentProvider 接口即插即用（create_agent_provider 工厂注册）。

启动：``uvicorn agent_app:app --host 127.0.0.1 --port 8079``
（run-windows.ps1 ``Start-BackgroundAgent`` 指向本入口；provider 由 env 决定，
不再按 provider 换 uvicorn 模块。）
"""

from __future__ import annotations

import os
from typing import Any

from agent_provider import (
    AgentProvider,
    SolveRequest,
    SolveResponse,
    create_agent_provider,
)
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="StreamingHarness Background-Agent (AgentProvider)", version="0.2.0")

# 进程级缓存：provider 由 env 决定，首次解析后固定（同 ASR 工厂模式）。
_provider: AgentProvider | None = None


class ProviderRouteRequest(BaseModel):
    """``POST /v1/provider/route`` 请求体（N7.1 前端热切）.

    ``provider`` 必须是已注册的 agent provider 名（codex | hermes），
    未知名 fail-loud -> HTTP 400（D-080，禁静默 fallback）。
    """

    provider: str


def _get_provider() -> AgentProvider:
    global _provider
    if _provider is None:
        name = os.environ.get("BACKGROUND_AGENT_PROVIDER", "codex")
        _provider = create_agent_provider(name)
    return _provider


@app.get("/health")
async def health() -> dict[str, Any]:
    return await _get_provider().health()


@app.post("/v1/solve", response_model=SolveResponse)
async def solve(request: SolveRequest) -> SolveResponse:
    return await _get_provider().solve(request)


# ---------------------------------------------------------------------------
# N7.1 前端热切：GET/POST /v1/provider/route（同 webinfer /v1/summarizer/route 模式）。
# webui 不直接改 provider；它转发快照到此端点，本进程重建 _provider 并回传。
# ---------------------------------------------------------------------------
@app.get("/v1/provider/route")
async def get_provider_route() -> dict[str, Any]:
    """读当前生效的 agent provider 名."""
    return {"provider": _get_provider().name}


@app.post("/v1/provider/route")
async def post_provider_route(req: ProviderRouteRequest) -> dict[str, Any]:
    """热切 agent provider（重建进程级 _provider）.

    未知名 fail-loud -> 400（D-080）；试构成功才替换，绝不留半状态。
    """
    global _provider
    try:
        candidate = create_agent_provider(req.provider)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _provider = candidate
    return {"provider": candidate.name, "status": "ok"}


def main() -> None:
    import uvicorn

    uvicorn.run(
        "agent_app:app",
        host=os.environ.get("CODEX_API_HOST", "127.0.0.1"),
        port=int(os.environ.get("CODEX_API_PORT", "8079")),
        reload=False,
    )


if __name__ == "__main__":
    import sys

    sys.exit(main())
