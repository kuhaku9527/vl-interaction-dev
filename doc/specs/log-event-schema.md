# Log Event Schema Spec (v1.0)

> 状态：**待实现**（2026-08-01 提议）。配套 ADR：`doc/adr/0014-log-event-schema.md`。
> 配套决策：`决策/服务-日志.md` D-2026-08-01-060 / D-2026-08-01-061。

## Problem Statement

本项目目前有 4 类"日志"，**每类都是非结构化文本 + 各服务独立写**：

| 来源 | 现状 | 问题 |
|---|---|---|
| `services/.logs/*.log` + `*.err.log` | launcher 启动后由 `Start-Process -Redirect*` 写 | 服务 stdout/stderr 是 unstructured 文本，grep 费力（要做正则）|
| `logs/vlm-runtime-props.json` | probe 写 `/props` snapshot | JSON 但**只覆盖最新**一份 |
| `logs/drift-gate-history/<ts>.json` | drift_gate 每次跑写一份 | JSON 但**只能 grep `.passed==false`**，没法按 service/time 过滤 |
| `logs/vlm-probes/<ts>.json` | probe 历史 | 同上 |
| `logs/webui-access-YYYY-MM-DD.log` | webui 中间件写 | JSONL，**但 schema 是 ad-hoc 临时定的**（ts/method/path/status/latency_ms）|

由此造成 3 个真实维护痛点：

1. **跨服务事件无统一 schema**。webui access log 字段和服务 log 字段不一样；drift_gate report 字段又不一样。想回答"昨天 14:00 哪条 chat 走了 memory_store 失败路径"必须 join 4 种文件
2. **无 query 工具**。grep + jq 是 ad-hoc；想要 `--service webui --since 1h --event memory_recall --filter 'latency_ms>5000'` 这样的过滤没有原子化支持
3. **PII 边界模糊**。`webinfer` 调 logger 时不知道"该打 / 不打 message body"，常打错；需要规范

## Solution

引入 **JSONL 事件流**（`events/<service>-<UTC-ISO>.jsonl`），schema 见下。同时附一个 `scripts/log_query.py` 做 grep/jq 的封装。 <!-- known-absent: 命令示例/记录事实，非仓库根路径 -->

### S-1 JSONL 事件 schema

每一行一条事件，`ts` 升序（按事件发生时间），UTF-8 编码，**必填字段**：

| 字段 | 类型 | 说明 |
|---|---|---|
| `ts` | string (ISO 8601 UTC) | 事件时间，例如 `2026-08-01T07:15:42.123Z` |
| `level` | string ∈ `{"debug","info","warn","error","critical"}` | 严重度。debug 可关 |
| `service` | string | 事件来源服务：`webui` / `webinfer` / `memory-store` / `vllm-llama` / `launcher` / `drift-gate` |
| `event` | string | 事件名（kebab-case），例：`chat_request` / `wiki_recall_fail` / `circuit_breaker_open` / `llama_n_ctx_check` |

**常用可选字段**（鼓励但不强求）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `session_id` | string | 业务会话 id；webui chat / webinfer session 共用 |
| `latency_ms` | int | 该事件从开始到结束的耗时 |
| `status` | int | HTTP / RPC 状态码 |
| `user` | string | 操作者，Windows 用户名 |
| `extra` | object | 事件专属 payload（如 circuit_breaker 附 `failures_count`，n_ctx_check 附 `n_ctx=16384`）|

**PII 红线**（违反 = 拒绝合并）：

- **不记 request/response body**。webui chat 内容、memory-store push 的 blocks 都可能有用户数据 → 只记长度/hash
- **不记 raw session key / API key**。即使 log level 是 debug
- **不记文件内容**。只记 `path` / `size_bytes`
- **不记 IP**（除非安全审计场景）。webui access log 不加 `client_ip` 字段

### S-2 文件命名 + 滚动

`logs/events/<service>-<UTC-YYYY-MM-DD>.jsonl`

- 按天滚动（每个服务每天一个文件）
- 旧文件由 `scripts/log_maintenance.ps1` 默认 30 天清理（**D-2026-08-01-060**）
- 单行写失败**不阻塞**业务路径（logger 是 best-effort）

### S-3 哪些事件必 log

| service | 必 log 事件 | 触发 |
|---|---|---|
| **webui** | `chat_request` `chat_response` `ws_connect` `ws_close` `memory_proxy_fail` | 每次 chat / WebSocket / memory-store 代理失败 |
| **webinfer** | `chat_route` `wiki_recall` `wiki_recall_fail` `circuit_breaker_open` `circuit_breaker_close` `push_memory` `llm_chat` | 每次主路径 + 异常 + 熔断器状态变化 |
| **memory-store** | `block_push` `block_recall` `backend_switch` `startup_fail` | 每次写读 + 后端切换 + 启动失败 |
| **vllm-llama** (外部进程) | `startup` `shutdown` `n_ctx_check` | 启动/关闭/drift_gate probe 后 |
| **launcher** | `start` `stop` `service_up` `service_down` `probe_refresh` | 每次 launcher 动作 |
| **drift-gate** | `run` `block_fail` `check_pass` `check_fail` | 每次 drift_gate 跑 |

PII 严守：webui 的 `chat_request` 事件 extra 字段**最多**含 `{message_chars: 42, has_image: true}`，**永远不**含 raw `text`。

### S-4 事件 schema 演进

- **不删除字段**。新增字段向后兼容（旧 query 工具忽略未知字段即可）
- **不重命名字段**。`status` 一直是 `status`，`latency_ms` 一直是 `latency_ms`
- **breaking change 走 ADR + 决策书 + schema_version bump**（schema 顶部加 `"schema_version": 2`）

## User Stories

1. As a Pilot, 报告"昨天 14:32 chat 挂"，operator `python scripts/log_query.py --service webui --since "yesterday 14:30" --until "yesterday 14:35"` 一行拿到所有该时段的 webui 事件（含 wiki_recall_fail 如果有） <!-- known-absent: 命令示例/记录事实，非仓库根路径 -->
2. As a developer, 排查"为什么 circuit breaker 跳了"，`--event circuit_breaker_open --since 1h` 拉出所有触发记录及 adjacent 服务事件
3. As a SRE, 跑"上周 P95 latency"报表，`--service webui --event chat_request` 拿所有时长后自己算（query 工具不内置统计，但过滤够用）
4. As a compliance officer, 审查"我们有没有记用户消息内容"，grep extra.message_text 应该是空（schema 层保证）

## Implementation Decisions

### I-1 单文件 per service per day

- `logs/events/webui-2026-08-01.jsonl` vs `logs/events/webinfer-2026-08-01.jsonl`
- 选这个而不是单一中心文件（`logs/events-2026-08-01.jsonl`）：避免多 service 并发写时的 race condition；service 故障不影响其他 service 日志

### I-2 用 stdlib `logging` + `python-json-logger`

- `python-json-logger`（`pip install python-json-logger`）输出 JSONL 而非手写 `json.dumps`
- 不引入 structlog（额外学习成本 + 第三方依赖）
- webui / webinfer 已有 Python logging 设施，加 `JsonFormatter` 一行

### I-3 不做 query tool 之外的东西

- 不内置 sqlite 索引（额外 I/O + 同步问题，drift_gate + log_maintenance + grep 已够用）
- 不做 alert（超 spec 范围，drift_gate 的 block 已经起 alert 作用）
- 不做 dashboard / 可视化（超 spec 范围，operator 用 `tail` + `jq` 就够）

## Test Plan

### T-1 单元：logger 配置

- `test_logger_emits_jsonl_with_required_fields`：写一条事件验证 JSONL 输出 + 4 个必填字段都在
- `test_logger_redacts_message_body`：验证传 `extra={'text': 'secret'}` 时输出**没有** `text` 字段（schema 红线）
- `test_logger_writes_one_line_per_event`：验证多线程并发写不会产生半行

### T-2 集成：log_query.py

- `test_query_filters_by_service_and_time`：构造 3 个 service 的 5 条事件，按 service+time 过滤应返回正确子集
- `test_query_handles_missing_file`：当日文件不存在 → 退出码 0 + stderr 提示
- `test_query_filters_by_event_name`：`--event circuit_breaker_open` 应只返回该类型事件

### T-3 端到端：launcher 集成（已有 Commit B 的 transcript 机制基础）

- launcher 启动后 inject 一次 `launcher.start` 事件（`service=launcher, event=start, extra={git_head, mode}`）
- drift_gate 跑完 inject 一次 `drift-gate.run` 事件（已 Commit A 的 history 文件 + 这个 event 一起）
- probe 跑完 inject 一次 `vllm-llama.n_ctx_check` 事件

## Cross-References

- 决策：`决策/服务-日志.md` D-2026-08-01-060 (schema 锁定) / D-061 (PII 红线)
- ADR：`doc/adr/0014-log-event-schema.md`
- 现有日志：Commit A (`logs/drift-gate-history/`) / Commit B (`logs/launcher-<ts>.log`) / Commit C (`logs/vlm-probes/`) / Commit D (`logs/webui-access-*.log`) — 这些是 4 个 Q1 补漏；本 spec 是**把它们统一到 JSONL 事件流**的下一阶段
- 工具：通用 `scripts/log_query.py` **仍未落盘**（跨服务任意查询，TBD） <!-- known-absent: 命令示例/记录事实，非仓库根路径 -->

## 决策事件的读侧（2026-09-21 新增，工单 #156）

本 spec 的通用查询工具仍缺席，但**决策轮次**这一具体用途已有专用读取方：
`services/webinfer/decision_events.py`（`python -m decision_events --events-dir logs/events`）。
它只做决策聚合，按 **会话 × 轮次** 输出 `decision` / `ts` / `latency_ms` / `frames_n`，
用**输出长度**（`raw_text_len`）而不是 content 区分「判定沉默」与「空输出」。
范围边界照旧：**不做**通用 log_query、**不做**索引 / 告警 / 看板。

### `raw_text_len` 与 `response_chars` 的语义（★ 本轮修正）

- `raw_text_len` = **决策解析器的输入**长度（模型原始输出，剥 special token 之前）；
- `response_chars` = **清洗后正文**长度。

**硬不变量：`raw_text_len >= response_chars`。** 解析器的输入必然包含正文，
所以小于关系是结构性不可能，出现即记录不可信。

⚠️ **先前记录的 24 行「`raw_text_len=0` 而 `response_chars>0`」是缺陷产物，不是真实信号。**
在写入侧接上「解析器输入」之前，记录的指纹取自消费端清洗后的正文；
而正文由 `content` 帧累积、`raw_text` 却默认空串 ⇒ 两者被混为一谈，
读侧会把**已经开口**的轮次报成「失效输出」。
2026-09-21 已修正（空串视为「未提供」而非「真零输出」），并固化为不变量测试。
⇒ **不变量被违反时，应当怀疑记录管道，而不是把那些轮次当作评测结论。**

### ★ 本次查清的一处静默失效（已修，值得当作教训）

`live_decision` 事件此前**从未真正落过盘**，尽管写入点的单元测试全绿。原因：
`event_json` 位于 `services/common/`，而 webui 以
`PYTHONPATH=<repo>/services/webui/src` 启动（`services/scripts/run-windows.ps1` → `Start-Webui`）。
顶层 `import event_json` 因此**每次都抛 `ModuleNotFoundError`**，被 `except` 兜底成一个
**静默 no-op**；而测试因为 pytest 会把仓库根放进 `sys.path`，永远看不到这个失败。
实测证据：`logs/events/webui-*.jsonl` 里只有 `config.services.patch`
（那条走的是另一个直接开文件的路径），`live_decision` **零条**。

⇒ 修法：写入侧按 `infer_loop.py` 的既有做法，从自身位置向上搜索
`services/common/event_json.py` 并放入 `sys.path`；**降级路径必须打日志**
（原来的静默 no-op 正是本缺陷得以隐藏的原因）；并补一条**在生产
`PYTHONPATH` 下另起子进程**的回归测试 + 其负控。
**判据有效性**：测试里放仓库根会掩盖生产路径解析失败 —— 这正是
「CI 全绿只证明 CI 跑到的断言成立」的又一实例。

## Migration Plan (Q1 → Q2 路径)

不在本 spec 范围但需要明确：

1. 本 spec 落地后，4 个 Q1 文件继续**双写**：Q1 格式 + JSONL 事件。`grep` 老 Q1 仍然能工作
2. 一周后**Q1 文件转只读**（标记"deprecated"），所有新排查走 `log_query.py`
3. 一个月后**Q1 文件删除**，仅留 JSONL

每步都独立 commit + 不破坏现有 `drift_gate` / `log_maintenance` 行为。
