# SPDX-License-Identifier: Apache-2.0
"""Provider 模式收敛（N7）：注册表 + 通用工厂样板。

2026-08-15 起，agent / tts / asr 三个「ABC + 工厂」模块的样板代码统一收敛到这里：

* name normalize（strip + lower，大小写/空白容错）；
* name 缺省时的 env 默认值解析；
* 未知名 fail-loud（ValueError 带可用列表——约法三章：禁静默 fallback）。

各模块的业务 ABC 与实现类**留在各自模块**（契约不同：任务->结果、文本->音频、
音频->文本，不能也不应统一成单一接口）；本文件只收敛「选择逻辑」这一层。

各模块统一用法::

    _REGISTRY = ProviderRegistry("agent provider", env_name=None, default=None)
    _REGISTRY.register("codex", _lazy_codex)
    def create_agent_provider(name=None):
        return _REGISTRY.create(name)

约定（所有 provider 模块一致）：
  * 未知名一律 raise ValueError（带可用列表），绝不悄悄退回默认；
  * name 为空时按 env_name -> default 解析；两处都无则 fail-loud。
"""

from __future__ import annotations

import os
from typing import Any, Callable


class ProviderRegistry:
    """按名字选择 provider 实现的注册表（name -> 工厂函数）。

    - normalize：strip + lower（大小写/空白容错）；
    - name 缺省时依次取 ``env_name`` -> ``default``；
    - 未知名 fail-loud：``ValueError`` 带可用列表（禁静默 fallback）。
    """

    def __init__(
        self,
        kind: str,
        *,
        env_name: str | None = None,
        default: str | None = None,
    ) -> None:
        self._kind = kind
        self._env_name = env_name
        self._default = default
        self._factories: dict[str, Callable[..., Any]] = {}

    # -- 注册 -------------------------------------------------------------

    def register(self, name: str, factory: Callable[..., Any]) -> None:
        """注册 ``name`` -> 工厂。name 大小写不敏感（统一小写存储）。"""
        key = name.strip().lower()
        if not key:
            raise ValueError(f"{self._kind}: empty provider name cannot be registered")
        if key in self._factories:
            raise ValueError(f"{self._kind}: duplicate provider name {name!r}")
        self._factories[key] = factory

    def available(self) -> list[str]:
        """已注册名字（排序，供错误消息与调试）。"""
        return sorted(self._factories)

    # -- 选择 -------------------------------------------------------------

    def resolve_name(self, name: str | None = None) -> str:
        """normalize + env 默认；未知名 fail-loud。返回规范化后的名字。"""
        raw = (name or "").strip()
        if not raw and self._env_name:
            raw = (os.environ.get(self._env_name) or "").strip()
        raw = raw or (self._default or "")
        normalized = raw.lower()
        if normalized not in self._factories:
            expected = "', '".join(self.available())
            raise ValueError(
                f"unknown {self._kind} provider {raw!r} (expected one of: {expected})"
            )
        return normalized

    def create(self, name: str | None = None, **kwargs: Any) -> Any:
        """按名创建 provider；kwargs 原样透传该名字注册的工厂。"""
        return self._factories[self.resolve_name(name)](**kwargs)
