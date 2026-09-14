# Spec（草稿）— Provider 模式收敛（N7）

> 生命周期: **正式**（2026-09-14 转正）
> 实现: b0991cc（provider 收敛 + summary OpenRouter 热切）；`services/provider_base.py`（`ProviderRegistry`）
> 验证: 三个下游模块（asr/tts/background-agent）均已改用统一注册表 + 通用工厂。
> 日期：2026-08-15
> 关联：`决策/AI代码质量约法三章.md`（禁静默 fallback）、ADR-0012（Embedding provider）、
> `doc/specs/asr-provider-unified.md`、`doc/specs/tts-provider-unified.md`、
> `doc/specs/background-agent-codex-bridge.md`（agent 插件化）

---

## 1. 背景与目标

- 现状：agent / tts / asr 三个「ABC + 工厂」模块各写了一份样板（name normalize、
  env 默认值、未知名 fail-loud），逻辑逐字节相似但散落三处；embedding 是单类内部分派
  （不同形态）。用户质疑「为什么热切换/插件化要一个一个模块设计，效率低」——合理。
- 目标：**收敛「选择逻辑」这一层**——统一为注册表 + 通用工厂；各模块业务 ABC 与
  实现类**留在各自模块**（契约不同，不强行统一成单一接口）。
- 边界：本次**不动** webui 前端配置面板（services_config 4 槽位扩全模块留待 N7.1）。

## 2. 收敛设计

### 2.1 公共层 `services/provider_base.py`

```python
class ProviderRegistry:
    def __init__(self, kind, *, env_name=None, default=None): ...
    def register(self, name, factory) -> None      # 大小写不敏感，重复注册拒绝
    def available(self) -> list[str]               # 已注册名字（排序）
    def resolve_name(self, name=None) -> str       # normalize + env 默认；未知名 fail-loud
    def create(self, name=None, **kwargs) -> Any   # kwargs 透传工厂
```

统一约定（约法三章对齐）：
- 未知名一律 `ValueError`（带可用列表），**绝不静默 fallback**；
- name 为空时按 `env_name` -> `default` 解析；两处都无则 fail-loud；
- normalize = strip + lower（大小写/空白容错）。

### 2.2 跨服务 import（bootstrap）

各服务进程 cwd 不同（uvicorn 工作目录 / pytest 目录），公共层以
`services.provider_base` 命名空间包路径共享；消费模块在文件顶部、`import services`
**之前**内联注入仓库根到 sys.path（同 webui `_ensure_repo_root_on_path` 先例）。
上溯层数：agent/tts 2 层、asr 3 层（`services/asr/jarvis/`）。

## 3. 迁移清单

| 模块 | 注册表 | 注册项 | 行为变化 |
|---|---|---|---|
| `services/background-agent/agent_provider.py` | `ProviderRegistry("agent provider")`（无 env 默认——调用方 agent_app 已读 env） | codex / hermes（惰性 import） | 无；`create_agent_provider(name)` 签名不变 |
| `services/tts/tts_provider.py` | `ProviderRegistry("TTS provider", env_name="TTS_PROVIDER", default="minimax")` | minimax | 无；删除 `_default_provider()`（env 解析并入注册表） |
| `services/asr/jarvis/asr_provider.py` | `ProviderRegistry("ASR provider", env_name=JARVIS_ASR_PROVIDER_ENV, default="local")` | local / cloud（工厂函数保留各自 env fallback 组装） | 无；`create_asr_provider(*, provider=...)` 签名不变 |
| `services/memory-store/src/memory_store/embedder.py` | **不迁移** | — | 理由：单类内部分派（非 ABC 层次），无工厂样板可收敛；golden recall 依赖，动它风险>收益 |

错误消息统一为：`unknown {kind} provider {raw!r} (expected one of: {available})`。

## 4. 验证结果（2026-08-15）

| 验证项 | 结果 |
|---|---|
| 注册表测试 `tests/test_provider_base.py` | ✅ 11 passed（注册/重复注册/大小写/env 默认/未知名 fail-loud/available/kwargs 透传） |
| background-agent 全量 | ✅ 35 passed（原 24 + 新 11） |
| tts 全量 | ✅ 28 passed |
| voice-clone 全量 | ✅ 11 passed |
| webui 全量 | ⏳ 回归中（基线 785 passed + 10 split_runtime 环境失败不变） |
| webinfer 全量 | ⏳ 回归中（基线 387 passed） |
| 三模块工厂行为 | ✅ agent codex/hermes + fail-loud；tts fail-loud；asr local + fail-loud |

## 5. 后续

- [x] **N7.1（2026-08-15 已实现）**：services_config 4 槽位扩到 **6 槽位**
  （+`agent` / `embedding`），前端面板可切所有模块 provider：

  | 槽位 | 热切通道 | 实现 |
  |---|---|---|
  | summary | webinfer `/v1/summarizer/route`（webui 代理） | 已有 |
  | asr | `set_asr_config_source` + invalidate + bridge | 已有 |
  | **agent（新）** | background-agent `POST /v1/provider/route`（重建 `_provider`，未知名 400）+ webui `/api/bg-agent/provider/route` 代理 + 传播热推 | **本次新增** |
  | **embedding（新）** | memory-store `POST /v1/settings/embedding`（已有）+ webui 传播热推 | **本次接线** |
  | llm / tts | 槽位语义 = webui 调用 URL，天然热切 | 已有 |

  - `services_config.py`：`_SERVICES_CONFIG_DEFAULTS` 6 槽位 + `_PROVIDER_CHOICES`
    白名单（agent: codex\|hermes；embedding: local\|siliconflow\|nvidia）；
    `_validate_and_apply_slot` provider 字段校验（未知 400，应用时 strip+lower 规范化）；
    `_merge_services_config_file` / 应用段白名单扩 provider 字段。
  - `admin_endpoints.py`：PUT 遍历 `_SERVICES_CONFIG_DEFAULTS`（原硬编码 4 槽位）；
    `_propagate_services_to_runtime` 加 agent/embedding 热推分支（fire-and-forget）；
    `_services_status_handler` 加 agent/embedding 探活。
  - 新文件 `bg_agent_proxy.py`（webui -> background-agent provider-route 代理，对齐
    `webinfer_proxy.py`）；`server.py` 路由 `/api/bg-agent/provider/route` GET/POST。
  - 前端：`config_services.js` SERVICES 6 槽位 + provider 下拉；`index.html` 加
    agent/embedding 输入组（Six pluggable backends）。
  - 测试：agent route 5 项（默认/切换/大小写/未知名 400/缺字段 422）；槽位 8 项；
    equivalence 豁免 5 个 N7.1 有意改动的函数（`_BUGFIX_DIVERGED`）；
    status/persist 测试同步 6 槽位断言。

- [x] **N8（2026-08-16 已实现）**：summary 槽位默认换 **OpenRouter 免费视觉模型**
  `google/gemma-4-26b-a4b-it:free`（实测图片摘要 cost=0 真实免费，替代计费的
  MiniMax-M3）：
  - `services_config.py`：summary 默认 `provider=openrouter` + `api_base=
    https://openrouter.ai/api/v1` + `api_key=env OPENROUTER_API_KEY`；
    `_PROVIDER_CHOICES` 加 `summary: (minimax, openrouter)`。
  - 前端：summary 槽位加 provider 下拉 + api-key 输入，切换联动默认
    api_base/model（`SUMMARY_PRESETS`）。
  - **关键坑**：`memory_summarizer._chat` 原来固定传 vLLM 专属
    `extra_body={"greedy": False}`——OpenRouter 透传后会让 Gemma 输出整段
    `<pad>`（实测）。已改为默认不传 extra_body（greedy=False 本就是 vLLM
    默认，行为等价），top_k/repetition_penalty 仅非默认才加。
  - 视觉 skill（`minimax-understand-image/scripts/understand_image.py`）加 <!-- known-absent: scripts/understand_image.py 不存在（一次性调试产物/未落盘） -->
    OpenRouter 免费后端（默认 openrouter，MiniMax 保留 `--backend minimax`）。
  - 测试：webinfer summarizer 11 passed（无回归）；webui 槽位 11 passed
    （+3 summary provider 校验）。

#### N8 附：OpenRouter 免费层限流与摘要频率（测试留痕，2026-08-16）

**为什么记这里**：测试/运行时若遇 429，按此排查，避免误判为代码问题。

| 项 | 数值 | 来源 |
|---|---|---|
| 免费层每日配额 | **50 requests/day** | OpenRouter zendesk Rate Limits 官方页 |
| 免费层每分钟配额 | 20 requests/minute | 同上 |
| 充值 ≥$10 | 1000 requests/day（20/min 不变） | 同上 |
| 429 是否计入配额 | **计入**（failed attempts count toward daily quota） | 同上 |
| provider 共享池限流 | 免费模型高峰时段上游限流（`temporarily rate-limited upstream`，如 Darkbloom / Google AI Studio） | 实测 2026-08-16 |

**摘要实际触发频率（代码实证）**：
- chunk 翻页：`config.chunk = 200`（`adapter_types.py:139`，env CHUNK 可改）——每 200 turns 一个 chunk
- mid-term 摘要：每 chunk 一次（`infer_loop._chat_payload_advance_chunk` → `_flush_chunk`）
- long-term 压缩：每 `compress_every_n_chunks = 5` chunk 一次（`adapter_types.py:140`，即每 ~1000 turns）
- 生产估算：活跃直播 ≈ 每 10-20 分钟一次 mid-term；一天 8h ≈ **30-50 次请求——贴着 50/day 上限**（高强度/整天直播可能触顶）

**429 排查要点**：
- 现象 A：`temporarily rate-limited upstream` → provider 共享池限流，**非 key/配额问题**，指数退避重试（1s→2s→4s）
- 现象 B：日配额耗尽 → 响应头 `x-ratelimit-*` 显示剩余；次日恢复，或充值 $10 升 1000/day
- 摘要链路 429 时走 fail-open（`_flush_chunk` WARNING + 跳过，不阻塞主对话）
- 测试教训（2026-08-16）：几秒内连发 5-6 次即触发上游 429，且 429 计入当日配额（一次测试耗 ~10 次配额）——**测试务必克制，预留配额**

**缓解选项**：① 日常免费够用；② 充值 $10 credits 升 1000/day（一次性，余额用完仍保持高配额）；③ BYOK（如 Google AI Studio 免费 key）脱离共享池。

- [ ] embedder 若未来出现多实现（非单类分派），再按注册表迁移。
