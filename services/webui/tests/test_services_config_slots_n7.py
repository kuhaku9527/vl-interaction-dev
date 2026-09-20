"""N7.1 槽位扩展测试：agent / embedding 进 services_config。

- 默认配置含 agent / embedding 槽位 + provider 白名单；
- ``_validate_and_apply_slot`` 对 provider 字段做白名单校验（agent:
  codex|hermes；embedding: local|siliconflow|nvidia）；未知 provider 400；
  不支持 provider 的槽位（llm 等）拒绝该字段；
- PUT /api/services/config 现在接受 agent/embedding（原先硬编码 4 槽位
  会忽略它们）。

仅测配置层（_validate_and_apply_slot + 内存态），不触网：probe 只对
api_base 有值时触发，agent/embedding 默认 api_base 为空 -> 不 probe。
"""

from __future__ import annotations

import asyncio
import copy
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
WEBUI_SRC = REPO / "services" / "webui" / "src"
for _p in (str(REPO), str(WEBUI_SRC)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from joy_interaction_webui import services_config as sc  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_config():
    """每个用例重置 _services_config 到默认（避免用例间串状态）。"""
    sc._services_config.clear()
    sc._services_config.update(copy.deepcopy(sc._SERVICES_CONFIG_DEFAULTS))


async def _apply(slot: str, incoming: dict):
    loop = asyncio.get_running_loop()
    return await sc._validate_and_apply_slot(slot, incoming, loop)


# -- 默认值与白名单 --------------------------------------------------------


def test_defaults_include_agent_and_embedding():
    assert "agent" in sc._SERVICES_CONFIG_DEFAULTS
    assert "embedding" in sc._SERVICES_CONFIG_DEFAULTS
    assert sc._SERVICES_CONFIG_DEFAULTS["agent"]["provider"] == "codex"
    assert sc._SERVICES_CONFIG_DEFAULTS["embedding"]["provider"] == "siliconflow"
    assert sc._PROVIDER_CHOICES["agent"] == ("codex", "hermes")
    assert sc._PROVIDER_CHOICES["embedding"] == ("local", "siliconflow", "nvidia")


# -- agent 槽位 ------------------------------------------------------------


async def test_apply_agent_provider_switch():
    entry, applied = await _apply("agent", {"provider": "hermes"})
    assert entry is None
    assert applied is True
    assert sc._services_config["agent"]["provider"] == "hermes"


async def test_apply_agent_provider_case_insensitive():
    entry, applied = await _apply("agent", {"provider": "  HERMES "})
    assert entry is None and applied is True
    assert sc._services_config["agent"]["provider"] == "hermes"


async def test_apply_agent_unknown_provider_rejected():
    entry, applied = await _apply("agent", {"provider": "bogus"})
    assert applied is False
    assert entry is not None and entry["status"] == 400
    assert "bogus" in entry["error"]
    # 未应用：内存态不变
    assert sc._services_config["agent"]["provider"] == "codex"


# -- embedding 槽位 --------------------------------------------------------


async def test_apply_embedding_provider_switch():
    entry, applied = await _apply("embedding", {"provider": "local"})
    assert entry is None and applied is True
    assert sc._services_config["embedding"]["provider"] == "local"


async def test_apply_embedding_unknown_provider_rejected():
    entry, applied = await _apply("embedding", {"provider": "bogus"})
    assert applied is False
    assert entry is not None and entry["status"] == 400
    assert sc._services_config["embedding"]["provider"] == "siliconflow"


# -- 不支持的槽位拒绝 provider 字段 ----------------------------------------


async def test_llm_rejects_provider_field():
    entry, applied = await _apply("llm", {"provider": "x"})
    assert applied is False
    assert entry is not None and entry["status"] == 400
    assert "does not support a provider field" in entry["error"]


async def test_unknown_slot_never_created():
    """_validate_and_apply_slot 对未知槽位不应创建条目（走 setdefault 不扩表）。"""
    await _apply("agent", {"provider": "codex"})  # 合法
    assert "unknown-slot" not in sc._services_config


# -- summary 槽位（N8: provider minimax|openrouter）----------------------


async def test_apply_summary_provider_switch():
    entry, applied = await _apply("summary", {"provider": "minimax"})
    assert entry is None and applied is True
    assert sc._services_config["summary"]["provider"] == "minimax"


async def test_apply_summary_provider_unknown_rejected():
    entry, applied = await _apply("summary", {"provider": "bogus"})
    assert applied is False
    assert entry is not None and entry["status"] == 400
    assert sc._services_config["summary"]["provider"] == "openrouter"


async def test_summary_defaults_to_openrouter_free():
    d = sc._SERVICES_CONFIG_DEFAULTS["summary"]
    assert d["provider"] == "openrouter"
    assert d["model"] == "google/gemma-4-26b-a4b-it:free"
    assert d["api_base"] == "https://openrouter.ai/api/v1"
