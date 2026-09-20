# ruff: noqa: RUF002 RUF003
"""TTS 插件化抽象（N5，2026-08-14）：TTSSynthesizer ABC + 工厂（同 AgentProvider/ASRProvider 模式）.

设计：
  * ``TTSSynthesizer`` ABC —— 统一流式合成契约（``synthesize`` 逐块产出音频 + ``ping`` 探活）。
  * 具体实现按 env ``TTS_PROVIDER``（默认 minimax）选择；未知名 fail-loud（约法三章）。
  * 第一个实现：MiniMaxTTSSynthesizer（Speech 2.8 SSE，自 http_synthesizer 适配，契约不变）。
  * 未来接 OpenAI TTS / CosyVoice 等：实现本 ABC + 工厂注册一行，即插即用（voice-clone
    的 provider 分支已留好：main.py ``_do_synthesize`` 按 ``tts_provider`` 分发）。
"""

from __future__ import annotations

import logging

# N7 provider 收敛：注册表（选择逻辑）在 services/provider_base.py，本模块只
# 注册实现。跨服务共享需把仓库根注入 sys.path（本文件上溯 2 层到仓库根）——
# 必须在 import services 之前完成（bootstrap，同 webui _ensure_repo_root_on_path 先例）。
import sys as _sys
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path as _Path

_REPO_ROOT = _Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_REPO_ROOT))

from services.provider_base import ProviderRegistry  # noqa: E402

logger = logging.getLogger("tts_provider")


# ---------------------------------------------------------------------------
# 统一配置（provider 无关的公共参数）
# ---------------------------------------------------------------------------
@dataclass
class TTSConfig:
    """TTS 插件公共配置（各 provider 可扩展私有字段）."""

    api_key: str = ""
    """提供方 API key（MiniMax=MINIMAX_API_KEY；未来 provider 读各自 env）。"""

    voice_id: str = ""
    """默认 voice_id（MiniMax=MINIMAX_VOICE_ID）。"""

    sample_rate: int = 16000
    """输出采样率（MiniMax 支持 16000/24000）。"""


# ---------------------------------------------------------------------------
# TTSSynthesizer 接口（插件契约）
# ---------------------------------------------------------------------------
class TTSSynthesizer(ABC):
    """TTS 合成插件。任何提供方实现 synthesize+ping 即插即用."""

    name: str = "base"

    @abstractmethod
    async def synthesize(
        self,
        text: str,
        *,
        voice_id: str | None = None,
        speed: float | None = None,
        vol: float | None = None,
    ) -> AsyncIterator[bytes]:
        """流式合成文本，逐块 yield 原始音频字节（WAV/PCM，可拼接）.

        Args:
            text: 待合成文本。
            voice_id: 覆盖默认 voice_id。
            speed: 语速覆盖（0.5-2.0）。
            vol: 音量覆盖（0.1-2.0）。
        """

    @abstractmethod
    async def ping(self) -> bool:
        """探活（不产生计费请求或尽可能低成本的连通性检查）."""


# ---------------------------------------------------------------------------
# 工厂（N7 收敛：注册表驱动，同 ASR/Agent 模式）
# ---------------------------------------------------------------------------
_TTS_REGISTRY = ProviderRegistry("TTS provider", env_name="TTS_PROVIDER", default="minimax")


def _lazy_minimax(**kwargs) -> TTSSynthesizer:
    from http_synthesizer import MiniMaxTTSConfig, MiniMaxTTSSynthesizer

    return MiniMaxTTSSynthesizer(MiniMaxTTSConfig(**kwargs))


_TTS_REGISTRY.register("minimax", _lazy_minimax)


def create_tts_provider(name: str | None = None, **kwargs) -> TTSSynthesizer:
    """按名字返回 TTS provider 实现（注册表驱动，同 ASR/Agent 模式）.

    未知名 fail-loud（约法三章：禁静默 fallback）——配置错误必须炸出来。
    name 缺省时按 ``TTS_PROVIDER`` env（默认 minimax）解析。
    """
    return _TTS_REGISTRY.create(name, **kwargs)
