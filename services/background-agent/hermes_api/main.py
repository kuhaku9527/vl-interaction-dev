# ruff: noqa: RUF002 RUF003
"""Hermes-agent gateway provider for StreamingHarness background tasks（AgentProvider 插件）.

2026-08-14 重构：契约层（SolveRequest/SolveResponse/recall/prompt/工具）已抽到
``agent_provider.py``（AgentProvider 统一抽象）；本模块保留 Hermes 特有的
HTTP 转发逻辑（OpenAI 兼容 chat.completions → SolveResponse），实现 HermesProvider。

``app`` 仍暴露 FastAPI（/health + /v1/solve）以便直接 ``uvicorn hermes_api.main:app``
启动；正式链路经 ``agent_app`` 统一入口按 BACKGROUND_AGENT_PROVIDER 选择。
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import sys
import time
from typing import Any

import httpx
from agent_provider import (
    AgentProvider,
    FrameInput,
    SolveRequest,
    SolveResponse,
)
from fastapi import FastAPI, HTTPException

logger = logging.getLogger(__name__)

# 环境配置（保留 CODEX_API_* 名字以兼容 webui legacy 探测）。
DEFAULT_HOST = os.environ.get("CODEX_API_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("CODEX_API_PORT", "8079"))
DEFAULT_MAX_SUBAGENTS = int(os.environ.get("CODEX_API_MAX_SUBAGENTS", "6"))
DEFAULT_MAX_CONCURRENT_RUNS = int(os.environ.get("CODEX_API_MAX_CONCURRENT_RUNS", "2"))
DEFAULT_TIMEOUT_SECONDS = float(os.environ.get("CODEX_API_TIMEOUT_SECONDS", "600"))
DEFAULT_MAX_FRAMES = int(os.environ.get("CODEX_API_MAX_FRAMES", "50"))

# hermes-agent gateway（NousResearch/hermes-agent v0.17.0+）。
HERMES_API_URL = os.environ.get("HERMES_API_URL", "http://127.0.0.1:8642/v1").rstrip("/")
HERMES_API_KEY = os.environ.get("HERMES_API_KEY") or os.environ.get("API_SERVER_KEY", "")
HERMES_MODEL = os.environ.get("HERMES_MODEL", "hermes-agent")
HERMES_GATEWAY_HOST = os.environ.get("HERMES_GATEWAY_HOST", "127.0.0.1")
HERMES_GATEWAY_PORT = int(os.environ.get("HERMES_GATEWAY_PORT", "8642"))
HERMES_GATEWAY_URL = f"http://{HERMES_GATEWAY_HOST}:{HERMES_GATEWAY_PORT}"

# 后向兼容 re-export（tests / webui 引用 agent_provider 的符号走这两个包名）
from agent_provider import _enrich_with_memory as _enrich_with_memory  # noqa: E402


class HermesProvider(AgentProvider):
    """后台 agent 插件：本地 hermes-agent HTTP gateway（OpenAI 兼容 chat.completions）."""

    name = "hermes"

    def __init__(self) -> None:
        self._semaphore = asyncio.Semaphore(max(1, DEFAULT_MAX_CONCURRENT_RUNS))

    async def health(self) -> dict[str, Any]:
        """Probe hermes-agent gateway. Keep the ``codex_api`` key for webui compatibility."""
        gateway_status = 0
        gateway_model = ""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{HERMES_GATEWAY_URL}/health")
                gateway_status = response.status_code
                if response.headers.get("content-type", "").startswith("application/json"):
                    payload = response.json()
                    if isinstance(payload, dict):
                        gateway_model = str(
                            payload.get("model")
                            or payload.get("default_model")
                            or payload.get("agent")
                            or ""
                        )
        except Exception:
            gateway_status = 0

        return {
            "provider": self.name,
            "codex_api": "ok",  # legacy field name; webui only checks HTTP 200
            "hermes_gateway": gateway_status,
            "model": gateway_model or HERMES_MODEL,
            "api_url": HERMES_API_URL,
        }

    async def solve(self, request: SolveRequest) -> SolveResponse:
        """Background solve entry point. Preserves the legacy codex_api contract."""
        from agent_provider import bounded_float, bounded_int, limit_frames

        max_subagents = bounded_int(
            request.max_subagents, default=DEFAULT_MAX_SUBAGENTS, minimum=1, maximum=64
        )
        timeout_seconds = bounded_float(
            request.timeout_seconds,
            default=DEFAULT_TIMEOUT_SECONDS,
            minimum=5.0,
            maximum=24 * 60 * 60,
        )
        frames = limit_frames(request.frames, DEFAULT_MAX_FRAMES)
        logger.info(
            "hermes solve start session=%s task=%s question_len=%d frames=%d timeout=%.1fs",
            request.session_id,
            request.task_id,
            len(request.question),
            len(frames),
            timeout_seconds,
        )

        local_wiki = await self.enrich_with_memory(request.question)
        prompt = self.build_prompt(request, max_subagents, local_wiki=local_wiki)
        user_content = _frames_to_content(prompt, frames)

        body = {
            "model": HERMES_MODEL,
            "stream": False,
            "messages": [{"role": "user", "content": user_content}],
        }

        headers = {
            "Content-Type": "application/json",
            "X-Hermes-Session-Id": request.session_id,
            "X-Background-Task-Id": request.task_id,
        }
        if HERMES_API_KEY:
            headers["Authorization"] = f"Bearer {HERMES_API_KEY}"

        started = time.perf_counter()
        logger.debug(
            "hermes solve dispatch session=%s task=%s -> %s",
            request.session_id,
            request.task_id,
            "/chat/completions",
        )
        async with self._semaphore:
            try:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(timeout_seconds + 30.0, connect=10.0)
                ) as client:
                    response = await client.post(
                        f"{HERMES_API_URL}/chat/completions",
                        json=body,
                        headers=headers,
                    )
            except httpx.TimeoutException as err:
                duration_ms = (time.perf_counter() - started) * 1000.0
                logger.warning(
                    "hermes solve timeout session=%s task=%s duration_ms=%.1f err=%s",
                    request.session_id,
                    request.task_id,
                    duration_ms,
                    err,
                )
                return SolveResponse(
                    status="timeout",
                    text="",
                    thread_id=request.session_id,
                    usage=None,
                    duration_ms=duration_ms,
                    events_digest={"error": f"hermes gateway timeout: {err}"},
                    error=str(err),
                )
            except httpx.HTTPError as err:
                duration_ms = (time.perf_counter() - started) * 1000.0
                logger.error(
                    "hermes solve transport error session=%s task=%s duration_ms=%.1f err=%s",
                    request.session_id,
                    request.task_id,
                    duration_ms,
                    err,
                )
                return SolveResponse(
                    status="failed",
                    text="",
                    thread_id=request.session_id,
                    usage=None,
                    duration_ms=duration_ms,
                    events_digest={"error": f"hermes gateway transport error: {err}"},
                    error=str(err),
                )

        duration_ms = (time.perf_counter() - started) * 1000.0
        result = _build_solve_response(response, request, duration_ms)
        logger.info(
            "hermes solve done session=%s task=%s status=%s duration_ms=%.1f",
            request.session_id,
            request.task_id,
            result.status,
            duration_ms,
        )
        return result


# ---------------------------------------------------------------------------
# Hermes 特有辅助（HTTP 响应解析）
# ---------------------------------------------------------------------------
def _frames_to_content(prompt: str, frames: list[FrameInput]) -> list[dict[str, Any]]:
    """Convert frames into OpenAI multimodal content parts. Decodes base64 lazily so
    the gateway can stream them straight into its image_url slots.
    """
    parts: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for index, frame in enumerate(frames, start=1):
        image_url = _normalize_frame_data_url(frame.image_url, index)
        parts.append(
            {
                "type": "image_url",
                "image_url": {"url": image_url, "detail": "auto"},
            }
        )
    return parts


def _normalize_frame_data_url(value: str, index: int) -> str:
    """Pass through jpeg/png data URLs, fall back to wrapping raw base64 for other inputs."""
    if value.startswith("data:image/"):
        return value
    # Accept either pure base64 (webui currently sends jpeg data URLs) or http(s) URLs.
    if value.startswith("http://") or value.startswith("https://"):
        return value
    try:
        base64.b64decode(value, validate=True)
    except Exception as err:
        raise HTTPException(
            status_code=400,
            detail=f"frame {index} image_url is not a data URL or base64 string: {err}",
        ) from err
    return f"data:image/jpeg;base64,{value}"


def _build_solve_response(
    upstream: httpx.Response,
    request: SolveRequest,
    duration_ms: float,
) -> SolveResponse:
    """Map an OpenAI-compatible hermes response into the legacy SolveResponse shape."""
    status = "completed"
    text = ""
    usage: dict[str, Any] | None = None
    error: str | None = None
    events_digest: dict[str, Any] = {"status_code": upstream.status_code}
    thread_id = request.session_id

    if upstream.status_code >= 400:
        status = "failed"
        error = _extract_error_message(upstream)
        events_digest["error"] = error
        return SolveResponse(
            status=status,
            text="",
            thread_id=thread_id,
            usage=None,
            duration_ms=duration_ms,
            events_digest=events_digest,
            error=error,
        )

    try:
        payload = upstream.json()
    except ValueError as err:
        return SolveResponse(
            status="failed",
            text="",
            thread_id=thread_id,
            usage=None,
            duration_ms=duration_ms,
            events_digest={**events_digest, "error": f"non-json upstream: {err}"},
            error=f"hermes gateway returned non-JSON body: {err}",
        )

    if not isinstance(payload, dict):
        return SolveResponse(
            status="failed",
            text="",
            thread_id=thread_id,
            usage=None,
            duration_ms=duration_ms,
            events_digest={**events_digest, "error": "upstream payload is not a JSON object"},
            error="hermes gateway returned an unexpected payload shape",
        )

    text = _extract_chat_completion_text(payload)
    if not text:
        status = "failed"
        error = "hermes gateway returned an empty completion"
        events_digest["error"] = error

    raw_usage = payload.get("usage")
    if isinstance(raw_usage, dict):
        usage = raw_usage

    model_name = payload.get("model")
    if isinstance(model_name, str) and model_name:
        events_digest["model"] = model_name

    finish_reason = _extract_finish_reason(payload)
    if finish_reason:
        events_digest["finish_reason"] = finish_reason

    # Surface delegated child work if the gateway reported it.
    children = payload.get("children") or payload.get("delegations")
    if isinstance(children, list) and children:
        events_digest["children"] = len(children)

    return SolveResponse(
        status=status,
        text=text,
        thread_id=thread_id,
        usage=usage,
        duration_ms=duration_ms,
        events_digest=events_digest,
        error=error,
    )


def _extract_chat_completion_text(payload: dict[str, Any]) -> str:
    """Robustly pull the assistant text from an OpenAI chat completion payload."""
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    message = first.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            chunks: list[str] = []
            for part in content:
                if isinstance(part, dict):
                    text = part.get("text")
                    if isinstance(text, str):
                        chunks.append(text)
            return "\n".join(chunks).strip()
    text = first.get("text")
    if isinstance(text, str):
        return text
    return ""


def _extract_finish_reason(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    reason = first.get("finish_reason")
    return str(reason) if reason is not None else ""


def _extract_error_message(upstream: httpx.Response) -> str:
    try:
        payload = upstream.json()
    except ValueError:
        return f"hermes gateway HTTP {upstream.status_code}: {upstream.text[:500]}"
    if isinstance(payload, dict):
        err = payload.get("error")
        if isinstance(err, dict):
            message = err.get("message")
            if isinstance(message, str):
                return f"hermes gateway HTTP {upstream.status_code}: {message}"
        if isinstance(err, str):
            return f"hermes gateway HTTP {upstream.status_code}: {err}"
    return f"hermes gateway HTTP {upstream.status_code}: {upstream.text[:500]}"


# ---------------------------------------------------------------------------
# FastAPI app（直接启动入口；正式链路走 agent_app 统一入口）
# ---------------------------------------------------------------------------
app = FastAPI(title="StreamingHarness Hermes API", version="0.1.0")
_provider = HermesProvider()


@app.get("/health")
async def health() -> dict[str, Any]:
    return await _provider.health()


@app.post("/v1/solve", response_model=SolveResponse)
async def solve(request: SolveRequest) -> SolveResponse:
    return await _provider.solve(request)


def main() -> None:
    import uvicorn

    uvicorn.run(
        "hermes_api.main:app",
        host=DEFAULT_HOST,
        port=DEFAULT_PORT,
        reload=False,
    )


if __name__ == "__main__":
    sys.exit(main())
