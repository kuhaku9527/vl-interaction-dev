# SPDX-License-Identifier: Apache-2.0
"""AgentProvider 统一抽象 guard（插件化契约，2026-08-14）。

锁定三件事：
  1. 工厂 create_agent_provider(name) 按名字返回正确实现（codex/hermes）；
  2. 未知名字 fail-loud（ValueError，不静默 fallback——约法三章）；
  3. 两个 provider 共享同一 SolveRequest/SolveResponse 契约类型（webui
     客户端零改动的前提），且各自实现 AgentProvider ABC。

Run:  python -m pytest services/background-agent/tests -o asyncio_mode=auto
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PKG_ROOT = Path(__file__).resolve().parents[1]  # services/background-agent
if str(PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(PKG_ROOT))

from agent_provider import (  # noqa: E402
    AgentProvider,
    SolveRequest,
    SolveResponse,
    create_agent_provider,
)


def test_factory_returns_codex_provider():
    from codex_api.main import CodexProvider

    p = create_agent_provider("codex")
    assert isinstance(p, AgentProvider)
    assert isinstance(p, CodexProvider)
    assert p.name == "codex"


def test_factory_returns_hermes_provider():
    from hermes_api.main import HermesProvider

    p = create_agent_provider("hermes")
    assert isinstance(p, AgentProvider)
    assert isinstance(p, HermesProvider)
    assert p.name == "hermes"


def test_factory_unknown_name_fails_loud():
    with pytest.raises(ValueError):
        create_agent_provider("bogus-agent")


def test_factory_case_insensitive():
    assert create_agent_provider("CODEX").name == "codex"
    assert create_agent_provider(" Hermes ").name == "hermes"


def test_shared_contract_types_between_providers():
    """codex_api 与 hermes_api 必须复用同一 SolveRequest/SolveResponse 类型，
    否则 webui 客户端（background_model.py）按契约类型解析会因身份不同而
    出隐性 bug（isinstance / pydantic 校验）。"""
    from codex_api import main as capi
    from hermes_api import main as hapi

    assert capi.SolveRequest is SolveRequest
    assert hapi.SolveRequest is SolveRequest
    assert capi.SolveResponse is SolveResponse
    assert hapi.SolveResponse is SolveResponse
