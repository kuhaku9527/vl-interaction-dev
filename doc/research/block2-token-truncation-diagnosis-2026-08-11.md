# 块2 初诊：LLM 长输出截断根因（token 链路）

> 日期: 2026-08-11
> 状态: 诊断完成（根因定性）；修复建议待走 spec 草稿→验证→替换流程
> 用户症状: 真机测试中 LLM 输出一旦变长就输出不完全（截断）
> 原则: 项目文档仅供查看、结论独立论证；全局思想沿决策链看

---

## 一、根因（证据链闭合）

**主对话输出上限被硬编码为 128 tokens，且前端无法覆盖。**

| 层 | 位置 | 事实 |
|---|---|---|
| 默认值 | `services/webinfer/adapter_types.py:121` | `main_max_tokens: int = 128` |
| 采信开关 | `adapter_types.py:133` | `honor_inbound_generation_params: bool = False`（前端传的 max_tokens **不生效**） |
| 组装 | `prompt_assembly.py:357/369` | `_main_generation_kwargs` 返回 `max_tokens = config.main_max_tokens`（=128） |
| 调用点 | `infer_loop.py:275/603/788/894` | 主对话全链路（流式推理 + 非流式）全部走该 kwargs |
| CLI/env | `app.py:57` | `--main-max-tokens`，env `MAIN_MAX_TOKENS`，默认 128 |
| 生产配置 | `run-windows.env` + `run-windows.ps1` | **均无 MAIN_MAX_TOKENS / HONOR_INBOUND 设置** → 生产即默认 128 |
| 前端 | webui | 未传 max_tokens（即使传了，honor=False 也不生效） |

**结论**：所有主对话输出（live 语音对话 / jarvis 对话 / QA）被限制在 **128 tokens（中文约 80–100 字）**。模型想回答超过这个长度即被截断——与用户"长输出就输出不完全"症状完全吻合。

**排除项**：与 llama-server `n_ctx=16384`（adapter_types.py:127）无关——上下文窗口大，但**输出上限**是独立的 128。

---

## 二、全局视角（决策链分析）：为什么是 128？

| 对比项 | max_tokens |
|---|---|
| 主对话 main | **128** |
| 中期记忆 mid_term | 4000 |
| 长期记忆 long_term | 2000 |

主对话是唯一被压到 128 的——**这不是巧合**。推断决策链：

1. 早期主对话设计以**决策 token 短回复**为核心（silence/response/delegation 三判断 + 静默退出，见 D-2026-08-03-001/002）——短输出对延迟和静默判断有利（force_silence_before_query 依赖短输出节奏）；
2. 后续演进（记忆注入、长上下文、内容型问答、直播模式的主动搭话）出现**长回复需求**，但 `main_max_tokens` 默认值**没有随之演进**；
3. 这就是"魔改版本处处有麻烦积累"的一个具体实例——**早期合理决策未随需求更新**，且无文档标注"128 是刻意为之"。

---

## 三、修复建议（分级，均须先草稿验证）

### 档位 1：立即止血（配置层，不动代码）
`run-windows.env` 加：
```
MAIN_MAX_TOKENS=1024
```
重启 webinfer 生效。风险最低，可即时验证"截断消失"。

### 档位 2：合理设计（区分两档输出）
- **决策短回复**（silence/response 判断、静默退出、主动搭话触发）：保持小 max_tokens（如 128–256），保延迟与静默节奏；
- **内容长回复**（问答、总结、多段输出）：大 max_tokens（如 1024–2048）。
- 实现：按 payload 场景选档，或开 `HONOR_INBOUND_GENERATION_PARAMS=true` + 前端按场景传 max_tokens。
- **须验证**：改大会不会破坏 live 三判断 / jarvis decision 静默退出 / force_silence_before_query 的节奏——这是不能盲改的约束。

### 档位 3：长期规范（token 链路 spec 草稿）
- max_tokens 与决策 token 语义解耦的规范；
- 流式输出完整性保障（SSE 分片、chunk 大小 200、compress_every_n_chunks=5 与长输出的关系）；
- 输出截断的可观测性（日志记录 finish_reason=length 时告警，而不是静默截断）。

---

## 四、验证方式（草稿→验证→替换）

1. 写 spec 草稿 `doc/specs/draft-token-link-max-tokens.md`（含两档设计与约束分析）；
2. 沙箱/真机试点：档位1 止血（MAIN_MAX_TOKENS=1024）→ 测长回复是否完整 + live/jarvis 三判断是否受影响；
3. 验证通过 → 完善 spec → 替换/新建正式 spec + 决策。

> 状态: **已修复（沙箱验证通过，2026-08-11 晚）**；真机验收待用户主机执行。原 Draft 版本于同日 21:2x 定性。

---

## 六、修复记录（2026-08-11 晚，团队 software-joyai 执行）

### 两个根因（本报告 §一 + §二，另确认根因 B）

**根因 B（"只说一句就停"）**：`response_format.py` `normalize_model_output` 原只保留 `</response>` 后**第一行**（`first_line = " ".join(response_text.splitlines()[0].split())`）→ 多行回复全部丢失。用户看到的"我先验证。"后无后续，即模型多行输出被压成一行。

### 修复内容（工程师寇豆码实施，QA 严过关独立回归）

| 文件 | 改动 |
|---|---|
| `response_format.py` L18-47 | `normalize_model_output` 保留 `</response>` 后**完整多行**（仅 strip 首尾空白）；空→`</silence>`；含 `</silence>`→silence；无 token 非空→`</response> <全文>`。docstring 注明历史根因 |
| `adapter_types.py` L126 | `main_max_tokens` 128→**1024**（注释：早期"决策 token 短回复"遗留，live 简洁性由 prompt 控制，不靠硬截断） |
| `app.py` L57 | `_env_int("MAIN_MAX_TOKENS", ...)` 默认同步 1024 |
| `tests/test_normalize_model_output.py` | 新增 11 用例（多行/空/空白/silence/无 token/CRLF/marker 优先/extract 回归） |
| `scripts/start_adapter.sh` L44 + README×2 | 收尾同步：默认 256→1024（Linux 路径防复现截断） |

**决策解析三函数零改动**（`extract_response_payload` / `strip_decision_tokens` / `parse_model_decision` 独立走 raw_text，不受 normalize 影响）。

### 测试与验证结果

- **178 passed**（既有 167 + 新增 11），环境 `D:\AI\envs\joyai-main\python.exe`（Python 3.12.13）。
- **QA 独立边界用例 12 passed**：代码块/缩进不压扁、空行后接内容、重复 `</response>` 标记、delegation 优先级、parse_args env 覆盖等。
- **生产路径核实（QA）**：Windows 启动 = `start-joyai.ps1` → `run-windows.ps1` → `live_adapter.py` 直启（editable 安装映射源码），**不经 start_adapter.sh**，env 无 MAIN_MAX_TOKENS → 生产走代码默认 **1024 生效**。Linux/bash 路径经 start_adapter.sh 的 256 覆盖已同步为 1024。
- **改动范围**：git diff 仅 3 源文件 + 1 新测试；build/ 副本未跟踪未加载（editable 安装）。

### 真机验收清单（待用户执行）

1. `stop-joyai.ps1` + `start-joyai.ps1 -Mode minimal` 重启。
2. 语音问一个需要长回答的问题（如"总结一下"类），确认输出**不再截断**、**多段/多行回复完整**。
3. 确认 live 三判断（沉默/主动搭话/回复）与 jarvis 唤醒/静默退出**节奏不受影响**。
4. 若有异常，`logs/` 看 `finish_reason` 与 `latency[infer]` 行反馈。

### 后续（走 草稿→验证→替换 流程）

- spec 草稿：`doc/specs/draft-token-link-max-tokens.md`（两档设计与约束分析，见 §三档位2/3）。
- 观察项：`finish_reason=length` 可观测告警（档位3，长期）——当前仍无显式告警，长输出若再截断只能靠日志排查。

---

## 五、附：本次核验的本地证据

```
grep adapter_types.py:121 → main_max_tokens: int = 128
grep adapter_types.py:133 → honor_inbound_generation_params: bool = False
grep prompt_assembly.py:357/369 → max_tokens = inbound.get("max_tokens", config.main_max_tokens) / config.main_max_tokens
grep infer_loop.py → _main_generation_kwargs 4 调用点（275/603/788/894）
grep app.py:57/84 → MAIN_MAX_TOKENS env（默认128）/ HONOR_INBOUND_GENERATION_PARAMS env（默认False）
grep run-windows.env + run-windows.ps1 → 无相关设置（生产=默认128）
```
