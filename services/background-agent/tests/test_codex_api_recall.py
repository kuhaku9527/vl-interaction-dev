# SPDX-License-Identifier: Apache-2.0
"""Regression guard for codex_api Local Wiki recall (D-049 contract port).

The recall helper is ported from hermes_api (same contract: memory-store
:8997 /v1/blocks/recall, scoped to wiki namespaces, fail-open). These tests
LOCK the two invariants that matter:

  1. recall failure MUST be logged as WARNING (not silently swallowed),
     while still failing OPEN (return "" so the solve is never blocked);
  2. a healthy recall MUST return the joined block lines for prompt
     injection, and an empty/missing store MUST return "".

Run:  python -m pytest services/background-agent/tests -o asyncio_mode=auto
"""

from __future__ import annotations

import logging

import agent_provider
import httpx
from codex_api import main as capi

LOGGER_NAME = "agent_provider"


class _BoomPostClient:
    """Client whose POST raises -- simulates memory-store unreachable."""

    def __init__(self, exc: Exception):
        self._exc = exc

    async def __aenter__(self) -> _BoomPostClient:
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    async def post(self, url: str, json: dict | None = None) -> None:
        raise self._exc


class _BoomEnterClient:
    """Client whose async-enter raises -- simulates client construction failure."""

    async def __aenter__(self) -> _BoomEnterClient:
        raise httpx.ConnectError("cannot open httpx client")

    async def __aexit__(self, *exc) -> bool:
        return False

    async def post(self, url: str, json: dict | None = None) -> None:
        raise AssertionError("post must not be reached when __aenter__ fails")


class _FakeResponse:
    def __init__(self, payload, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict:
        return self._payload


class _OkPostClient:
    """Client that returns a canned recall payload."""

    def __init__(self, payload, status_code: int = 200):
        self._payload = payload
        self._status_code = status_code

    async def __aenter__(self) -> _OkPostClient:
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    async def post(self, url: str, json: dict | None = None) -> _FakeResponse:
        return _FakeResponse(self._payload, self._status_code)


def _assert_warned_recall_failure(caplog) -> None:
    assert any(
        r.levelno == logging.WARNING
        and r.name == LOGGER_NAME
        and "local wiki recall failed" in r.getMessage()
        for r in caplog.records
    ), (
        "recall failure must be logged as WARNING on codex_api "
        "(D-049 port: must not be silently swallowed)"
    )


async def test_enrich_logs_warning_when_network_raises(monkeypatch, caplog):
    """A ConnectError during recall must be logged, while still failing open."""
    monkeypatch.setattr(
        agent_provider.httpx,
        "AsyncClient",
        lambda *a, **k: _BoomPostClient(httpx.ConnectError("memory-store down")),
    )
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        result = await capi._enrich_with_memory("any question")
    assert result == ""  # fail-open preserved
    _assert_warned_recall_failure(caplog)


async def test_enrich_logs_warning_when_client_entry_raises(monkeypatch, caplog):
    """An exception raised while opening the client must also be logged."""
    monkeypatch.setattr(
        agent_provider.httpx,
        "AsyncClient",
        lambda *a, **k: _BoomEnterClient(),
    )
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        result = await capi._enrich_with_memory("any question")
    assert result == ""
    _assert_warned_recall_failure(caplog)


async def test_enrich_returns_joined_blocks_on_success(monkeypatch):
    """Healthy recall returns block lines joined for prompt injection."""
    payload = {
        "blocks": [
            {"content": "艾尔登法环 出血流配装：双曲剑+血焰刀刃"},  # noqa: RUF001 (intentional Chinese fullwidth colon)
            {"content": "老头环 DLC 幽影树碎片位置", "images": ["img/a.png"]},
        ]
    }
    monkeypatch.setattr(
        agent_provider.httpx,
        "AsyncClient",
        lambda *a, **k: _OkPostClient(payload),
    )
    result = await capi._enrich_with_memory("艾尔登法环出血流怎么配")
    assert "艾尔登法环 出血流配装" in result
    assert "附图: img/a.png" in result
    assert "双曲剑" in result


async def test_enrich_returns_empty_on_http_error(monkeypatch, caplog):
    """HTTP >=400 must fail open with empty string (no exception)."""
    monkeypatch.setattr(
        agent_provider.httpx,
        "AsyncClient",
        lambda *a, **k: _OkPostClient({}, status_code=503),
    )
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        result = await capi._enrich_with_memory("anything")
    assert result == ""


async def test_enrich_returns_empty_on_empty_blocks(monkeypatch):
    """No blocks in a 200 response must return empty string."""
    monkeypatch.setattr(
        agent_provider.httpx,
        "AsyncClient",
        lambda *a, **k: _OkPostClient({"blocks": []}),
    )
    result = await capi._enrich_with_memory("anything")
    assert result == ""


async def test_enrich_empty_question_returns_early():
    """Empty question short-circuits without network I/O."""
    assert await capi._enrich_with_memory("") == ""
