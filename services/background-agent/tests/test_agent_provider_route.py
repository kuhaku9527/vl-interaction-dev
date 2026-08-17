"""agent_app /v1/provider/route 热切端点测试（N7.1）。

GET 读当前 provider；POST 热切（重建进程级 _provider）；未知名 fail-loud
-> 400（D-080）。测试间 _provider 为模块级缓存，按文件内顺序依赖。
"""

import pytest
from fastapi.testclient import TestClient

import agent_app
from agent_app import app


@pytest.fixture()
def client(monkeypatch):
    # 每用例重置进程级缓存，避免用例间串状态
    agent_app._provider = None
    monkeypatch.setenv("BACKGROUND_AGENT_PROVIDER", "codex")
    return TestClient(app)


def test_get_route_returns_default_provider(client):
    r = client.get("/v1/provider/route")
    assert r.status_code == 200
    assert r.json()["provider"] == "codex"


def test_post_route_switches_provider(client):
    r = client.post("/v1/provider/route", json={"provider": "hermes"})
    assert r.status_code == 200
    assert r.json() == {"provider": "hermes", "status": "ok"}
    # 切换后 GET 反映新 provider（进程级缓存已重建）
    r2 = client.get("/v1/provider/route")
    assert r2.json()["provider"] == "hermes"


def test_post_route_case_insensitive(client):
    r = client.post("/v1/provider/route", json={"provider": "  HERMES "})
    assert r.status_code == 200
    assert r.json()["provider"] == "hermes"


def test_post_route_unknown_fails_loud(client):
    r = client.post("/v1/provider/route", json={"provider": "bogus"})
    assert r.status_code == 400
    assert "bogus" in r.json()["detail"]
    # 失败不改变当前 provider（绝不留半状态）
    r2 = client.get("/v1/provider/route")
    assert r2.json()["provider"] == "codex"


def test_post_route_missing_field_422(client):
    r = client.post("/v1/provider/route", json={})
    assert r.status_code == 422
