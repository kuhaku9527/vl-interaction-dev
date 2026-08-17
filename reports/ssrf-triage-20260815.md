# DeepSec remote L3 定向复核报告（SSRF 5 处 + sqlite）

- 时间：2026-08-15 15:00 | 复核方式：MiniMax-M3 remote L3 | 基线：`.cache/shield_l3_report.json`（本地全层 40 条源码）
- 本文档由 `.cache/deepsec/ssrf-triage/TRIAGE.md` 归档至仓库 `reports/`，便于检索与交付。

## 结论先行

**5 处 SSRF 疑似项 + sqlite f-string，MiniMax 判定均无 critical/high 真实风险**——URL 均来自 CLI 参数 / 环境变量（配置绑定），脚本均为本地运维工具（probe/benchmark/verify），非网络暴露端点。本地 `sast_ssrf_fetch_user_url`（medium）为启发式标记，LLM 未确认可利用。

## 逐文件明细

| 文件 | 本地标记 | MiniMax 判定 | 备注 |
|---|---|---|---|
| scripts/vlm_runtime_probe.py L81 | SSRF medium | **low** | base_url 来自 CLI 参数；本地脚本 |
| services/scripts/benchmark_4state_notforme.py L366 | SSRF medium | **low** | LLAMA_BASE_URL 环境变量无白名单；本地基准工具 |
| services/scripts/verify-services.py L36 | SSRF medium | **无**（打包复核） | 健康检查脚本 |
| services/memory-store/tools/fetch_wiki.py L44/L124 | SSRF medium ×2 | **无**（打包复核） | URL 来自配置/wiki 源 |
| memory_store/backends/sqlite_backend.py L407 | SQL f-string medium | **无**（打包复核） | 参数化占位符 |

## LLM 额外提示（均为 low/info，非阻断）

- vlm_runtime_probe.py：`urlopen` 支持 file:///data: 等 scheme（L86）；无响应体大小上限（L59）；错误 `repr(exc)` 写入快照文件（L115）；输出路径透传（L132）；日志文件名可预测（L138）
- benchmark_4state_notforme.py：`LLAMA_BASE_URL` 无 scheme/host 校验（L51）；响应体无上限（L293）；环境变量数值解析无边界检查（L53）；模型原文写入 doc/research（info）
- 打包单请求复核（verify-services / fetch_wiki / sqlite_backend）：0 条

## 建议

1. 5 处 SSRF 可**归档为已核对（配置绑定、本地 CLI）**，无需改动；后续若脚本被改造为网络服务，需重新评估。
2. low 项可选优化：`urlopen` 前限定 http/https scheme、响应体大小上限——非紧急。
3. 复核成本：5 个文件约 8 次 MiniMax 调用（f1/f2 各 1 + 打包 1），< ¥0.5。
