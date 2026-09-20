# ruff: noqa: RUF002 RUF003
"""AgentProvider 统一抽象：background-agent 的后端 agent 插件层（2026-08-14）.

设计（同 services/asr/jarvis/asr_provider.py 模式）：
  * AgentProvider ABC —— 统一 ``/v1/solve`` 契约（SolveRequest -> SolveResponse）。
  * 具体实现按 env ``BACKGROUND_AGENT_PROVIDER`` 选择：
      - "codex"  -> CodexProvider（codex_api.main，子进程 codex CLI）
      - "hermes" -> HermesProvider（hermes_api.main，HTTP hermes gateway）
  * 公共契约层（本模块）：FrameInput/SolveRequest/SolveResponse + _enrich_with_memory
    （Local Wiki recall, D-049）+ _build_prompt + bounded/limit 工具函数——
    两个 provider 逐字节相同的部分只保留一份，消除重复。
  * create_agent_provider(name) 工厂；未知名字 fail-loud（约法三章：禁静默 fallback）。
"""

from __future__ import annotations

import logging
import os

# N7 provider 收敛：注册表（选择逻辑）在 services/provider_base.py，本模块只
# 注册实现。跨服务共享需把仓库根注入 sys.path（本文件上溯 2 层到仓库根）——
# 必须在 import services 之前完成（bootstrap，同 webui _ensure_repo_root_on_path 先例）。
import sys as _sys
from abc import ABC, abstractmethod
from pathlib import Path as _Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

_REPO_ROOT = _Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_REPO_ROOT))

from services.provider_base import ProviderRegistry  # noqa: E402

logger = logging.getLogger(__name__)

# memory-store（Local Wiki 源）。仅 recall，任何失败不阻塞（fail-open）→ agent 走 web search。
MEMORY_STORE_URL = os.environ.get("MEMORY_STORE_URL", "http://127.0.0.1:8997").rstrip("/")
# [Local Wiki] recall 范围（ADR-0012）：只注入这些 namespace 下的块——会话记忆不泄漏进 wiki recall。
WIKI_RECALL_NAMESPACES = os.environ.get("WIKI_RECALL_NAMESPACES", "wiki:*")


# ---------------------------------------------------------------------------
# 请求 / 响应模型。字段名与 webui（background_model.py）dict 访问逐字一致，
# 不得改名（连带更新 webui 客户端）。
# ---------------------------------------------------------------------------
class FrameInput(BaseModel):
    image_url: str = Field(..., description="JPEG data URL")
    timestamp: float | None = None
    timestamp_kind: str | None = None
    pts: int | None = None


class SolveRequest(BaseModel):
    session_id: str
    task_id: str
    question: str
    foreground_text: str = ""
    frames: list[FrameInput] = Field(default_factory=list)
    max_subagents: int | None = None
    timeout_seconds: float | None = None


class SolveResponse(BaseModel):
    status: Literal["completed", "failed", "timeout"]
    text: str
    thread_id: str | None = None
    usage: dict[str, Any] | None = None
    duration_ms: float
    events_digest: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


# ---------------------------------------------------------------------------
# AgentProvider 接口（插件契约）
# ---------------------------------------------------------------------------
class AgentProvider(ABC):
    """后台 agent 插件。任何 agent（Codex/Hermes/未来）实现 solve+health 即插即用.

    契约：``POST /v1/solve``（SolveRequest -> SolveResponse）+ ``GET /health``。
    """

    name: str = "base"

    @abstractmethod
    async def solve(self, request: SolveRequest) -> SolveResponse:
        """处理一次委派。必须返回 SolveResponse（不许 raise 到路由层）."""

    @abstractmethod
    async def health(self) -> dict[str, Any]:
        """探活。返回可 JSON 化的 dict（webui 只查 HTTP 200）."""

    # -- 共享辅助（子类可覆盖，默认走公共实现） -------------------------
    async def enrich_with_memory(self, question: str) -> str:
        """Local Wiki recall（D-049）。fail-open：任何错误返回 ""."""
        return await _enrich_with_memory(question)

    def build_prompt(
        self, request: SolveRequest, max_subagents: int, *, local_wiki: str = ""
    ) -> str:
        """构造委派 prompt（agent 名字可微调措辞）."""
        return _build_prompt(request, max_subagents, local_wiki=local_wiki)


# ---------------------------------------------------------------------------
# 工厂（N7 收敛：注册表驱动，同 ASR/TTS 模式）
# ---------------------------------------------------------------------------
_AGENT_REGISTRY = ProviderRegistry("agent provider")


def _lazy_codex() -> AgentProvider:
    from codex_api.main import CodexProvider

    return CodexProvider()


def _lazy_hermes() -> AgentProvider:
    from hermes_api.main import HermesProvider

    return HermesProvider()


_AGENT_REGISTRY.register("codex", _lazy_codex)
_AGENT_REGISTRY.register("hermes", _lazy_hermes)


def create_agent_provider(name: str | None = None) -> AgentProvider:
    """按名字返回 agent provider 实现（注册表驱动，同 ASR/TTS 模式）.

    未知名字 fail-loud（约法三章：禁静默 fallback）——配置错误必须炸出来，
    而不是悄悄退回某个默认后端。
    """
    return _AGENT_REGISTRY.create(name)


# ---------------------------------------------------------------------------
# 共享工具（两个 provider 逐字节相同的部分，只保留一份）
# ---------------------------------------------------------------------------
def build_prompt_text(
    request: SolveRequest,
    max_subagents: int,
    *,
    local_wiki: str = "",
    solver_label: str = "background solver",
) -> str:
    """构造委派 prompt。原 codex_api 与 hermes_api 共用同一措辞，仅首行 agent 名不同."""
    frame_lines = []
    for index, frame in enumerate(request.frames, start=1):
        timestamp = frame.timestamp if frame.timestamp is not None else "unknown"
        timestamp_kind = frame.timestamp_kind or "unknown"
        pts = frame.pts if frame.pts is not None else "unknown"
        frame_lines.append(
            f"- Frame {index}: timestamp={timestamp} kind={timestamp_kind} pts={pts}"
        )
    frame_context = "\n".join(frame_lines) if frame_lines else "- No recent frames were provided."
    prompt = f"""You are the {solver_label} for a real-time video assistant.

Use Chinese by default for user-facing prose unless the user explicitly asks otherwise.
Use live web search when current or external information is useful.
You may spawn at most {max_subagents} parallel subagents. Do not exceed this limit.
If you spawn subagents, wait for all of them and consolidate their useful results.
The answer is isolated background UI output. Do not write files unless the user explicitly requested an artifact and it is necessary for analysis; return the final content in the response.
For any visual deliverable request, including image generation, posters, illustrations, avatars, cartoon characters, or PPT/slides, default to imagegen / gpt-image-2 to generate real PNG/JPG assets; do not substitute Python/SVG/HTML/CSS drawings unless the user explicitly asks for code or vector output.
If you create a user-visible file artifact, save it under the current working directory. In the final response, include the existing artifact file path as plain text, not in backticks or a code block, and do not return a directory path.
At the very end of your final response, include a concise summary wrapped exactly as <summary>...</summary>. The text inside must be 1-2 Chinese sentences for the frontend summary card.
If a chart is useful, include a fenced JSON block like {{"type":"bar_chart","title":"...","labels":[],"values":[]}}.
If asked to recreate a visible webpage, return a complete static HTML document in a fenced html code block.

Session: {request.session_id}
Task: {request.task_id}
Foreground note: {request.foreground_text}
Delegated question:
{request.question}

Recent frame metadata:
{frame_context}
"""
    if local_wiki:
        prompt += f"\n[Local Wiki]\n{local_wiki}\n(优先用本地资料, 无关时才用 web search)\n"
    return prompt


def _build_prompt(request: SolveRequest, max_subagents: int, *, local_wiki: str = "") -> str:
    """后向兼容别名：AgentProvider.build_prompt 默认实现."""
    return build_prompt_text(request, max_subagents, local_wiki=local_wiki)


async def _enrich_with_memory(question: str) -> str:
    """Local Wiki recall（D-049 契约，同源实现）.

    Scoped to wiki namespaces (ADR-0012) so per-session conversation memory
    never pollutes the [Local Wiki] injection. Fails open: any error, empty
    result, or missing service returns "" so the agent simply falls back to
    live web search. Never blocks the solve.
    """
    if not question:
        return ""
    namespaces = [ns.strip() for ns in WIKI_RECALL_NAMESPACES.split(",") if ns.strip()]
    if not namespaces:
        return ""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{MEMORY_STORE_URL}/v1/blocks/recall",
                json={
                    "query": question,
                    "top_k": 5,
                    "min_score": 0.4,
                    "filter": {"namespaces": namespaces},
                },
            )
            if resp.status_code >= 400:
                return ""
            payload = resp.json()
            blocks = payload.get("blocks") if isinstance(payload, dict) else None
            if not blocks:
                return ""
            lines = []
            for b in blocks:
                if not isinstance(b, dict) or not b.get("content"):
                    continue
                line = f"- {b['content']}"
                images = b.get("images") or []
                if images:
                    line += f" (附图: {', '.join(images)})"
                lines.append(line)
            return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001 - fail open: any recall error falls back to web search
        logger.warning("local wiki recall failed, falling back to web search: %s", exc)
        return ""


def bounded_int(value: int | None, *, default: int, minimum: int, maximum: int) -> int:
    try:
        resolved = int(value if value is not None else default)
    except (TypeError, ValueError):
        resolved = default
    return min(max(resolved, minimum), maximum)


def bounded_float(value: float | None, *, default: float, minimum: float, maximum: float) -> float:
    try:
        resolved = float(value if value is not None else default)
    except (TypeError, ValueError):
        resolved = default
    return min(max(resolved, minimum), maximum)


def limit_frames(frames: list[FrameInput], max_frames: int) -> list[FrameInput]:
    if max_frames <= 0:
        return []
    return list(frames or [])[-max_frames:]


def decode_data_url(value: str) -> bytes:
    import base64

    prefix = "base64,"
    marker = value.find(prefix)
    if not value.startswith("data:image/") or marker < 0:
        raise ValueError("frame image_url must be an image data URL")
    return base64.b64decode(value[marker + len(prefix) :], validate=True)
