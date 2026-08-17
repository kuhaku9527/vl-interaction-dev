"""Local Codex CLI provider for StreamingHarness background tasks（AgentProvider 插件）。

2026-08-14 重构：契约层（SolveRequest/SolveResponse/recall/prompt/工具）已抽到
``agent_provider.py``（AgentProvider 统一抽象）；本模块保留 Codex 特有的
子进程执行逻辑（codex CLI exec --json 流式解析），实现 CodexProvider。

``app`` 仍暴露 FastAPI（/health + /v1/solve）以便直接 ``uvicorn codex_api.main:app``
启动；正式链路经 ``agent_app`` 统一入口按 BACKGROUND_AGENT_PROVIDER 选择。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException

from agent_provider import (
    AgentProvider,
    FrameInput,
    SolveRequest,
    SolveResponse,
    bounded_float,
    bounded_int,
    decode_data_url,
    limit_frames,
)

logger = logging.getLogger("codex_api")

DEFAULT_HOST = os.environ.get("CODEX_API_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("CODEX_API_PORT", "8079"))
DEFAULT_MAX_SUBAGENTS = int(os.environ.get("CODEX_API_MAX_SUBAGENTS", "6"))
DEFAULT_MAX_CONCURRENT_RUNS = int(os.environ.get("CODEX_API_MAX_CONCURRENT_RUNS", "2"))
DEFAULT_TIMEOUT_SECONDS = float(os.environ.get("CODEX_API_TIMEOUT_SECONDS", "600"))
DEFAULT_MAX_FRAMES = int(os.environ.get("CODEX_API_MAX_FRAMES", "50"))
DEFAULT_BACKGROUND_AGENT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_WORKSPACE_PATH = DEFAULT_BACKGROUND_AGENT_DIR.parent.parent / "agent-workspace"
DEFAULT_WORKSPACE = os.environ.get(
    "CODEX_API_WORKSPACE",
    str(DEFAULT_WORKSPACE_PATH),
)
DEFAULT_CODEX_HOME = os.environ.get(
    "CODEX_HOME",
    str(DEFAULT_BACKGROUND_AGENT_DIR / "codex-home"),
)
STDERR_TAIL_BYTES = int(os.environ.get("CODEX_API_STDERR_TAIL_BYTES", "20000"))
STDOUT_MAX_BYTES = int(os.environ.get("CODEX_API_STDOUT_MAX_BYTES", str(64 * 1024 * 1024)))
STREAM_READER_LIMIT_BYTES = int(
    os.environ.get("CODEX_API_STREAM_READER_LIMIT_BYTES", str(64 * 1024 * 1024))
)

# 后向兼容 re-export（tests / webui 引用 agent_provider 的符号走这两个包名）
from agent_provider import (  # noqa: E402
    FrameInput as FrameInput,
    SolveRequest as SolveRequest,
    SolveResponse as SolveResponse,
)
from agent_provider import build_prompt_text as build_prompt_text  # noqa: E402
from agent_provider import _enrich_with_memory as _enrich_with_memory  # noqa: E402
from agent_provider import bounded_float as _bounded_float  # noqa: E402
from agent_provider import bounded_int as _bounded_int  # noqa: E402
from agent_provider import limit_frames as _limit_frames  # noqa: E402
from agent_provider import decode_data_url as _decode_data_url  # noqa: E402


@dataclass
class JsonlState:
    thread_id: str | None = None
    usage: dict[str, Any] | None = None
    final_message: str = ""
    event_counts: dict[str, int] = field(default_factory=dict)
    item_counts: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    web_searches: int = 0
    command_executions: int = 0

    def ingest(self, line: str) -> None:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            self.errors.append(f"invalid-jsonl: {line[:200]}")
            return
        event_type = str(event.get("type") or "unknown")
        self.event_counts[event_type] = self.event_counts.get(event_type, 0) + 1

        if event_type == "thread.started":
            self.thread_id = event.get("thread_id") or self.thread_id
        elif event_type == "turn.completed":
            usage = event.get("usage")
            if isinstance(usage, dict):
                self.usage = usage
        elif event_type in {"turn.failed", "error"}:
            self.errors.append(json.dumps(event, ensure_ascii=False, default=str)[:1000])

        item = event.get("item")
        if isinstance(item, dict):
            item_type = str(item.get("type") or "unknown")
            self.item_counts[item_type] = self.item_counts.get(item_type, 0) + 1
            if event_type == "item.completed" and item_type == "agent_message":
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    self.final_message = text.strip()
            if item_type == "web_search":
                self.web_searches += 1
            elif item_type == "command_execution":
                self.command_executions += 1

    def digest(self) -> dict[str, Any]:
        return {
            "event_counts": self.event_counts,
            "item_counts": self.item_counts,
            "errors": self.errors[-5:],
            "web_searches": self.web_searches,
            "command_executions": self.command_executions,
        }


class CodexProvider(AgentProvider):
    """后台 agent 插件：系统 codex CLI 子进程（exec --search --json --ephemeral）。"""

    name = "codex"

    def __init__(self) -> None:
        self._semaphore = asyncio.Semaphore(max(1, DEFAULT_MAX_CONCURRENT_RUNS))

    async def health(self) -> dict[str, Any]:
        codex_path = shutil.which("codex")
        version = ""
        if codex_path:
            try:
                proc = await asyncio.create_subprocess_exec(
                    codex_path,
                    "--version",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
                version = stdout.decode(errors="replace").strip()
            except Exception:
                version = ""
        config_path = Path(DEFAULT_CODEX_HOME) / "config.toml"
        return {
            "status": "ok",
            "provider": self.name,
            "codex_path": codex_path,
            "codex_version": version,
            "config_path": str(config_path),
            "config_exists": config_path.exists(),
            "workspace": DEFAULT_WORKSPACE,
            "yolo": True,
            "web_search": "live",
            "max_subagents": DEFAULT_MAX_SUBAGENTS,
            "max_concurrent_runs": DEFAULT_MAX_CONCURRENT_RUNS,
            "stdout_max_bytes": STDOUT_MAX_BYTES,
            "stream_reader_limit_bytes": STREAM_READER_LIMIT_BYTES,
        }

    async def solve(self, request: SolveRequest) -> SolveResponse:
        async with self._semaphore:
            return await self._solve_with_codex(request)

    async def _solve_with_codex(self, request: SolveRequest) -> SolveResponse:
        codex_path = shutil.which("codex")
        if not codex_path:
            raise HTTPException(status_code=500, detail="codex CLI not found on PATH")

        max_subagents = bounded_int(
            request.max_subagents,
            default=DEFAULT_MAX_SUBAGENTS,
            minimum=1,
            maximum=DEFAULT_MAX_SUBAGENTS,
        )
        timeout_seconds = bounded_float(
            request.timeout_seconds,
            default=DEFAULT_TIMEOUT_SECONDS,
            minimum=1.0,
            maximum=DEFAULT_TIMEOUT_SECONDS,
        )
        started = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix="streamingharness-codex-") as tmpdir:
            image_paths = _write_frame_images(limit_frames(request.frames, DEFAULT_MAX_FRAMES), Path(tmpdir))
            argv = _build_codex_argv(
                codex_path=codex_path,
                workspace=DEFAULT_WORKSPACE,
                image_paths=image_paths,
                max_subagents=max_subagents,
            )
            local_wiki = await self.enrich_with_memory(request.question)
            prompt = self.build_prompt(request, max_subagents, local_wiki=local_wiki)
            state = JsonlState()
            stderr_chunks: list[bytes] = []
            stdout_bytes = 0

            codex_env = {**os.environ, "CODEX_HOME": DEFAULT_CODEX_HOME}
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
                limit=STREAM_READER_LIMIT_BYTES,
                env=codex_env,
            )

            async def read_stdout() -> None:
                nonlocal stdout_bytes
                assert proc.stdout is not None
                while True:
                    line = await proc.stdout.readline()
                    if not line:
                        break
                    stdout_bytes += len(line)
                    if stdout_bytes > STDOUT_MAX_BYTES:
                        raise RuntimeError("codex stdout exceeded limit")
                    state.ingest(line.decode(errors="replace").strip())

            async def read_stderr() -> None:
                assert proc.stderr is not None
                while True:
                    chunk = await proc.stderr.read(4096)
                    if not chunk:
                        break
                    stderr_chunks.append(chunk)
                    total = sum(len(item) for item in stderr_chunks)
                    while total > STDERR_TAIL_BYTES and stderr_chunks:
                        removed = stderr_chunks.pop(0)
                        total -= len(removed)

            stdout_task = asyncio.create_task(read_stdout())
            stderr_task = asyncio.create_task(read_stderr())
            try:
                assert proc.stdin is not None
                proc.stdin.write(prompt.encode("utf-8"))
                await proc.stdin.drain()
                proc.stdin.close()
                await asyncio.wait_for(proc.wait(), timeout=timeout_seconds)
                await stdout_task
                await stderr_task
            except asyncio.TimeoutError:
                _terminate_process_group(proc)
                await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
                return SolveResponse(
                    status="timeout",
                    text="",
                    thread_id=state.thread_id,
                    usage=state.usage,
                    duration_ms=(time.perf_counter() - started) * 1000,
                    events_digest=state.digest(),
                    error=f"Codex run timed out after {timeout_seconds:.0f}s",
                )
            except Exception as err:
                _terminate_process_group(proc)
                await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
                return SolveResponse(
                    status="failed",
                    text=state.final_message,
                    thread_id=state.thread_id,
                    usage=state.usage,
                    duration_ms=(time.perf_counter() - started) * 1000,
                    events_digest=state.digest(),
                    error=str(err),
                )

            stderr_tail = b"".join(stderr_chunks)[-STDERR_TAIL_BYTES:].decode(errors="replace")
            duration_ms = (time.perf_counter() - started) * 1000
            if proc.returncode != 0:
                return SolveResponse(
                    status="failed",
                    text=state.final_message,
                    thread_id=state.thread_id,
                    usage=state.usage,
                    duration_ms=duration_ms,
                    events_digest={**state.digest(), "stderr_tail": stderr_tail},
                    error=f"codex exited with {proc.returncode}",
                )
            if not state.final_message:
                return SolveResponse(
                    status="failed",
                    text="",
                    thread_id=state.thread_id,
                    usage=state.usage,
                    duration_ms=duration_ms,
                    events_digest={**state.digest(), "stderr_tail": stderr_tail},
                    error="codex returned no final agent message",
                )
            return SolveResponse(
                status="completed",
                text=state.final_message,
                thread_id=state.thread_id,
                usage=state.usage,
                duration_ms=duration_ms,
                events_digest={**state.digest(), "stderr_tail": stderr_tail},
                error=None,
            )


def _build_codex_argv(
    *,
    codex_path: str,
    workspace: str,
    image_paths: list[Path],
    max_subagents: int,
) -> list[str]:
    argv = [
        codex_path,
        "--search",
        "exec",
        "--json",
        "--ephemeral",
        "--dangerously-bypass-approvals-and-sandbox",
        "--cd",
        workspace,
        "-c",
        f"agents.max_threads={max_subagents}",
        "-c",
        "agents.max_depth=1",
    ]
    for image_path in image_paths:
        argv.extend(["-i", str(image_path)])
    argv.append("-")
    return argv


def _write_frame_images(frames: list[FrameInput], directory: Path) -> list[Path]:
    paths = []
    for index, frame in enumerate(frames, start=1):
        data = decode_data_url(frame.image_url)
        path = directory / f"frame-{index:04d}.jpg"
        path.write_bytes(data)
        paths.append(path)
    return paths


def _terminate_process_group(proc: asyncio.subprocess.Process) -> None:
    """Kill the codex subprocess tree. Windows has no os.killpg, so use taskkill."""
    if proc.returncode is not None or not proc.pid:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=10,
            )
            return
        except Exception as exc:  # taskkill may fail if the process already died
            logger.debug("taskkill fallback for pid %s: %s", proc.pid, exc)
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except Exception:
        try:
            proc.terminate()
        except ProcessLookupError:
            return


# ---------------------------------------------------------------------------
# FastAPI app（直接启动入口；正式链路走 agent_app 统一入口）
# ---------------------------------------------------------------------------
app = FastAPI(title="StreamingHarness Codex API", version="0.1.0")
_provider = CodexProvider()


@app.get("/health")
async def health() -> dict[str, Any]:
    return await _provider.health()


@app.post("/v1/solve", response_model=SolveResponse)
async def solve(request: SolveRequest) -> SolveResponse:
    return await _provider.solve(request)


def main() -> None:
    import uvicorn

    uvicorn.run("codex_api.main:app", host=DEFAULT_HOST, port=DEFAULT_PORT, reload=False)


if __name__ == "__main__":
    import sys

    sys.exit(main())
