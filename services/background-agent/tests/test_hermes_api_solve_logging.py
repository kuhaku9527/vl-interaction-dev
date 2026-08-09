# SPDX-License-Identifier: Apache-2.0
"""Smoke test for the hermes_api solve() structured logging (add-only audit).

These tests verify two things WITHOUT touching the hermes gateway or any
environment/config:

  1. The SolveResponse contract is preserved -- a stubbed 200 chat completion
     still resolves to status="completed" (the logging change must not alter
     any return branch in ``_build_solve_response`` or the solve() happy path).
  2. The new ``logger.*`` calls on the solve() critical path actually fire --
     at least one INFO (or higher) record containing "hermes solve" must be
     emitted. This guards against a future regression that silently drops the
     solve-path logging.

The httpx POST to the gateway (and the incidental memory-store recall POST
inside ``_enrich_with_memory``) are stubbed via monkeypatch, mirroring the
pattern used by test_hermes_api_enrich.py.

Run:  python -m pytest services/background-agent/tests -o asyncio_mode=auto
"""

from __future__ import annotations

import logging

import httpx
from hermes_api import main as hapi

LOGGER_NAME = "hermes_api.main"


class _FakeResponse:
    """Minimal httpx.Response stand-in for both memory-store and gateway POSTs."""

    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = ""

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                str(self.status_code),
                request=None,
                response=None,  # type: ignore[arg-type]
            )

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    """Routes POSTs by URL suffix.

    * ``/chat/completions`` -> a fake but well-formed OpenAI chat completion so
      ``_build_solve_response`` yields status="completed".
    * anything else (e.g. memory-store recall) -> empty wiki blocks so the
      solve proceeds without a real network call.
    """

    def __init__(self, *args, **kwargs):
        self._completion = {
            "id": "fake-completion",
            "model": "hermes-agent",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "假回答 <summary>done</summary>",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    async def post(
        self, url: str, json: dict | None = None, headers: dict | None = None
    ) -> _FakeResponse:
        if url.rstrip("/").endswith("/chat/completions"):
            return _FakeResponse(200, self._completion)
        return _FakeResponse(200, {"blocks": []})


def _assert_logged_hermes_solve(caplog) -> None:
    assert any(
        r.levelno >= logging.INFO and r.name == LOGGER_NAME and "hermes solve" in r.getMessage()
        for r in caplog.records
    ), (
        "solve() must emit an INFO+ log containing 'hermes solve' "
        "(audit add-only: solve-path logging must be active)"
    )


async def test_solve_logs_hermes_solve_and_returns_completed(monkeypatch, caplog):
    """solve() keeps returning status=completed and emits 'hermes solve' INFO logs."""
    monkeypatch.setattr(hapi.httpx, "AsyncClient", _FakeClient)

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        result = await hapi.solve(
            hapi.SolveRequest(
                session_id="sess-1",
                task_id="task-1",
                question="BT-7274 怎么打?",
                frames=[],
            )
        )

    assert result.status == "completed"
    _assert_logged_hermes_solve(caplog)


async def test_solve_logs_with_frames_and_long_question(monkeypatch, caplog):
    """Exercise the same path with frames + a long question; only lengths/counts
    are logged (never frame image bytes or full question text). Contract intact."""
    monkeypatch.setattr(hapi.httpx, "AsyncClient", _FakeClient)

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        result = await hapi.solve(
            hapi.SolveRequest(
                session_id="sess-2",
                task_id="task-2",
                question="x" * 4096,  # long question; must NOT be echoed in full
                frames=[
                    hapi.FrameInput(image_url="data:image/jpeg;base64,AAAA"),
                    hapi.FrameInput(image_url="data:image/jpeg;base64,BBBB"),
                ],
            )
        )

    assert result.status == "completed"
    _assert_logged_hermes_solve(caplog)
    # Guard against the forbidden behavior: frame base64 / full question must
    # never appear in the emitted logs.
    joined = caplog.text
    assert "AAAA" not in joined
    assert "BBBB" not in joined
    assert "x" * 4096 not in joined
