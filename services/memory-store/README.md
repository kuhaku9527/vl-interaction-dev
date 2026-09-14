# memory-store

> 测试 local wiki 召回的钉死封闭回路见 [tools/README.md](tools/README.md)。
> 端口/路由已按 2026-09-14 实测校正；真值源见 `决策/服务-memory-store.md`。

JoyAI 持久化记忆骨架（spec `doc/specs/memory-store-skeleton-spec.md`）。

## 后端

- `SqliteBackend`（向量语义召回 via bge-m3 余弦；`score`/`last_hit_at`/`hit_count` schema 留位运行时未维护；FTS5 BM25 兜底已删，详见 `决策/服务-memory-store.md` D-2026-08-05-003）
- `PsqlBackend` / `ObsidianBackend` 占位（`NotImplementedError`）
- **USearch 侧车 HNSW**：每 namespace 一份 `.usearch`（见 ADR-0012 方案 C）

## API（11 条路由，2026-09-14 实测）

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/health` | 健康（含 embedder 可加载性） |
| GET | `/v1/backends` | 后端列表 |
| POST | `/v1/blocks/push` | 写入块 |
| POST | `/v1/blocks/recall` | 召回 |
| POST | `/v1/external/sync` | **Local Wiki 同步**（被 `tools/README.md` 的回路依赖） |
| GET | `/v1/namespaces` | 知识库列表 |
| DELETE | `/v1/namespaces/{namespace}` | 删除整库 |
| GET | `/v1/providers/health` | 各 embedding provider 真实 ping |
| GET / PUT | `/v1/settings/network` | 网络/代理设置热生效 |
| POST | `/v1/settings/embedding` | embedding 设置 |

## 启动

```
cd services/memory-store
pip install -e .[dev]
memory-store
# 或（显式指定端口与库路径）
MEMORY_PORT=8997 MEMORY_SQLITE_PATH=./data/memory.sqlite python -m memory_store.app
```

## 端口

- **默认 `8997`**（env `MEMORY_PORT` 覆盖）
  > ⚠️ **不是 8996**。8996 是历史空壳端口，`决策/服务-memory-store.md` D-2026-07-26-031 **明文禁止**任何脚本/agent 启动 8996 记忆服务。此 README 曾错写 8996（2026-09-14 校正）——那正是 `drift-contract.json` 的 `memory-store-port` 检查所防的 DRIFT-2/3 回归。
- 端口被占用 → `OSError: [Errno 98]` 退出非零（不抢、不重试）
