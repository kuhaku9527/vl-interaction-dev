# SPDX-License-Identifier: Apache-2.0
"""TTS 插件化 guard（N5，2026-08-14）：工厂 + ABC 契约。

锁定：
  1. create_tts_provider("minimax") 返回 MiniMaxTTSSynthesizer（且实现 TTSSynthesizer ABC）；
  2. 未知名 provider fail-loud（ValueError，不静默 fallback）；
  3. 默认 provider 从 TTS_PROVIDER env 读取（缺省 minimax）；
  4. MiniMaxTTSSynthesizer 保持 synthesize/ping 契约（webui / voice-clone 调用面不变）。

Run:  python -m pytest services/tts/tests -o asyncio_mode=auto
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]  # services/tts
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from http_synthesizer import MiniMaxTTSSynthesizer  # noqa: E402
from tts_provider import TTSSynthesizer, create_tts_provider  # noqa: E402


def test_factory_returns_minimax_provider():
    p = create_tts_provider("minimax", api_key="k", group_id="g", voice_id="v")
    assert isinstance(p, TTSSynthesizer)
    assert isinstance(p, MiniMaxTTSSynthesizer)
    assert p.name == "minimax"


def test_factory_unknown_provider_fails_loud():
    with pytest.raises(ValueError):
        create_tts_provider("cosyvoice")


def test_factory_default_from_env(monkeypatch):
    monkeypatch.setenv("TTS_PROVIDER", "minimax")
    p = create_tts_provider(api_key="k", group_id="g", voice_id="v")
    assert isinstance(p, MiniMaxTTSSynthesizer)


def test_factory_default_without_env():
    # 未设 TTS_PROVIDER 时缺省 minimax（MiniMax-only 决策，main-direction §3）
    p = create_tts_provider(api_key="k", group_id="g", voice_id="v")
    assert p.name == "minimax"


def test_minimax_synthesizer_implements_abc_contract():
    """synthesize/ping 签名与 ABC 一致（webui 调用面不变的前提）。"""
    import inspect

    synth = create_tts_provider("minimax", api_key="k", group_id="g", voice_id="v")
    synth_method = getattr(type(synth), "synthesize")
    ping_method = getattr(type(synth), "ping")
    assert inspect.iscoroutinefunction(synth_method) or inspect.isasyncgenfunction(synth_method)
    assert inspect.iscoroutinefunction(ping_method)
