"""ProviderRegistry 收敛层测试（N7）。

覆盖注册表自身的契约：注册/重复注册、normalize（大小写/空白）、env 默认、
未知名 fail-loud、available 列表。三个消费模块（agent/tts/asr）各自的工厂
测试仍在其服务目录（行为由注册表驱动后，本测试即注册表通用行为测试）。
"""

import sys
from pathlib import Path

import pytest

# provider_base 在 services/ 根：测试以仓库根为 cwd 运行（services/background-agent）
_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from services.provider_base import ProviderRegistry  # noqa: E402


@pytest.fixture()
def reg() -> ProviderRegistry:
    r = ProviderRegistry("test provider", env_name="TEST_PROVIDER_ENV", default="alpha")
    r.register("alpha", lambda: "alpha-instance")
    r.register("Beta", lambda: "beta-instance")
    return r


def test_create_exact_name(reg):
    assert reg.create("alpha") == "alpha-instance"


def test_create_normalizes_case_and_whitespace(reg):
    # 大小写 + 首尾空白容错（统一小写存储）
    assert reg.create("  BETA ") == "beta-instance"
    assert reg.create("AlPhA") == "alpha-instance"


def test_create_defaults_to_env_when_name_empty(reg, monkeypatch):
    monkeypatch.setenv("TEST_PROVIDER_ENV", "beta")
    assert reg.create(None) == "beta-instance"
    assert reg.create("") == "beta-instance"


def test_create_defaults_to_default_when_env_unset(reg, monkeypatch):
    monkeypatch.delenv("TEST_PROVIDER_ENV", raising=False)
    assert reg.create(None) == "alpha-instance"


def test_unknown_name_fails_loud(reg):
    with pytest.raises(ValueError, match="unknown test provider provider 'bogus'"):
        reg.create("bogus")


def test_unknown_env_fails_loud(reg, monkeypatch):
    monkeypatch.setenv("TEST_PROVIDER_ENV", "nope")
    with pytest.raises(ValueError, match="unknown test provider provider 'nope'"):
        reg.create(None)


def test_no_env_no_default_fails_loud(monkeypatch):
    r = ProviderRegistry("bare provider")
    monkeypatch.delenv("TEST_PROVIDER_ENV", raising=False)
    with pytest.raises(ValueError, match="unknown bare provider provider ''"):
        r.create(None)


def test_available_lists_sorted(reg):
    assert reg.available() == ["alpha", "beta"]


def test_duplicate_register_rejected():
    r = ProviderRegistry("dup provider")
    r.register("same", lambda: 1)
    with pytest.raises(ValueError, match="duplicate provider name 'SAME'"):
        r.register("SAME", lambda: 2)


def test_empty_name_register_rejected():
    r = ProviderRegistry("empty provider")
    with pytest.raises(ValueError, match="empty provider name"):
        r.register("  ", lambda: 1)


def test_kwargs_pass_through_to_factory():
    r = ProviderRegistry("kw provider")
    r.register("echo", lambda **kw: kw)
    assert r.create("echo", a=1, b="x") == {"a": 1, "b": "x"}
