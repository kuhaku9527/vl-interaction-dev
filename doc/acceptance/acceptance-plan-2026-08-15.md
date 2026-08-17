# 真机验收计划 — 2026-08-15（全链路）

> 目的：真机集中验收，覆盖**全部待真机项**（含 08-13/08-14 新增），每轮留痕可追溯。
> 前置阅读：`doc/specs/draft-background-agent-codex-bridge.md`（AgentProvider 插件化）、`doc/specs/draft-call-mode-unknown-delegation.md`（N2）、`doc/specs/draft-live-empathy-tuning.md`（共情调优）、`doc/specs/draft-mode-isolation-boundaries.md`（三模式边界）、`doc/acceptance/live-ca-acceptance-2026-08-12.md`（三决策诱发方法）
> 测试人：＿＿＿＿＿＿　日期：2026-08-15　环境：＿＿＿＿＿＿（麦型号：＿＿＿＿＿＿）
> 被测栈：生产 llama.cpp(7060) + webinfer(8070) + webui(8099) + **agent_app(8079, provider=codex)** + memory-store(8997) + relay(57321)

---

## §0 前置条件（每次测试前确认）

- [ ] 重启栈：`stop-joyai.ps1` → `start-joyai.ps1 -Mode minimal`
- [ ] 确认 `BACKGROUND_AGENT_PROVIDER=codex`（默认）生效：`curl http://127.0.0.1:8079/health` → `provider: "codex"`、codex_path 非空
- [ ] relay 57321 在线：`curl http://127.0.0.1:57321/v1/models` → MiniMax-M3 列表
- [ ] memory-store 8997 在线 + embedding 云端实证：`curl http://127.0.0.1:8997/health` → 确认 `embedder.provider=siliconflow`（N6 已实证，此处留痕）
- [ ] 浏览器 **Ctrl+F5** 强刷（加载新前端）
- [ ] 日志就绪：`services/.logs/webui.err.log`（观察 `[live-mode]` / `decision=` / `delegation` / `[bg]` / `N2` 关键词）

---

## §1 后台委派（AgentProvider 插件化）— 重点

| 编号 | 用例 | 操作步骤 | 预期结果 | 观察点（日志） |
|---|---|---|---|---|
| **C1** | agent_app 统一入口健康 | 启动后 curl :8079/health | `provider:"codex"`、codex 0.142.4、config_exists=true | agent_app 日志无 traceback |
| **C1b** | 插件切换（选择语义） | 临时设 `BACKGROUND_AGENT_PROVIDER=hermes` 重启 background-agent → curl :8079/health | `provider:"hermes"`、hermes_gateway 状态（可 0，仅验证选择逻辑） | agent_app 按 env 换 provider，无回退语义 |
| **C2** | live 委派触发 | live 模式问"查一下 RTX 5070 价格" / "查艾尔登法环出血流攻略" | 不念问题本身；后台触发；**结果播报回主对话** | `decision=delegation` → `[bg]` → `background_result_ready` WS → 二次播报 |
| **C3** | jarvis 委派触发 | jarvis 模式（喊 bt）问"查一下明天天气" | 同 C2 | `decision=delegation`（jarvis 侧） |
| **C4** | 委派结果拼回 | C2/C3 拿到结果后，确认主模型**角色化重述**结果 | BT-7274 口吻包装后播报，非干读 | `background_result_ready` 后有一条新 llm_reply |
| **C5** | 委派失败兜底 | 临时停 relay 57321 后问需外查问题 | 不阻塞对话；主模型可答"外部信息不可用" | `[bg]` 日志有 WARNING；无挂起 |
| **C6** | **call 模式"不知道就委派"（N2 已实现）** | **call 模式（文本输入框）**问"今天几号？" | 模型答"不知道"→ 后端检测改写"让我查一下再告诉你。"→ **自动委派 codex 查证 → 补答显示** | `N2 call-mode unknown detected` 日志 → `decision=delegation` → 补答 |
| **C7** | live/jarvis "不知道"委派 | live/jarvis 模式问"今天几号？""天气？" | 模型应 `</delegation>` 委派（若直接答"不知道"→ 记录，属模型判定） | `decision=` 字段为准 |

## §2 四态共情类调优（2026-08-14 新增，AFK 工作线）

| 编号 | 目标决策 | 建议诱发语句 | 预期 | 判定（日志） |
|---|---|---|---|---|
| **E1** | `</not-for-me>`（共情/抱怨） | live 模式说"哎呀这关怎么那么难" / "唉，好累" / "完了完了要迟到了" | **无播报**（不共情不安慰） | `decision=not-for-me`（或 silence 同计不播报） |
| **E2** | 面向句不漏判 | 紧接 E1 说"嘿 BT，现在几点了" | 正常回复 | `decision=response`（宁漏不乱插性质不破） |
| **E3** | benchmark 脚本（可选） | llama-server 7060 在线时跑 `python services/scripts/benchmark_4state_notforme.py` | N01b（"哎呀这关怎么那么难"）判 not-for-me；误响应率较调优前（52%）下降 | 脚本输出表 |

## §3 live 三决策诱发（承接 08-12 清单 §2）

| 编号 | 目标决策 | 建议诱发语句 | 预期 | 判定（日志） |
|---|---|---|---|---|
| **D1** | `</silence>` | "嗯""我在呢""哦"（多试几种语气） | 无语音播报 | `decision=silence` |
| **D2** | `</response>` | "你好""讲个笑话""介绍一下玛尔基特" | 有语音回复+字幕 | `decision=response` + sentence 播报 |
| **D3** | `</delegation>` | "查一下玛尔基特怎么打" | 后台触发、结果播报 | `decision=delegation`（同 C2 链路） |

> 三态触发不保证 100%（模型自主），每个目标试 2-3 种语句，以 `decision=` 日志为准。

## §4 ASR cloud 专项（JARVIS_ASR_PROVIDER=cloud）

| 编号 | 用例 | 操作步骤 | 预期 | 观察点 |
|---|---|---|---|---|
| **A1** | 识别率对比 | 同一句话本地 vs cloud 各说一次（读一段固定文本） | cloud 识别率 ≥ 本地，无漏字/错字 | ASR 日志 final 文本对比 |
| **A2** | 无 partial 字幕 | cloud 模式说话，看前端字幕 | **无逐字 partial**（整句出）——语义差异确认可接受 | 前端字幕行为 |
| **A3** | 退出词延迟 | cloud 模式说"好的"退出 | 退出词 final 延迟（云端整段识别），确认可接受 | `退出词` 日志时间点 |
| **A4** | wake 直促 | cloud 模式唤醒后直接说命令 | 唤醒后跳过 ASR confirm 直促（语义差异） | ASR/wake 日志 |

## §5 jarvis 回归（delegate 启用前置门槛 D-TC1）

| 编号 | 用例 | 预期 |
|---|---|---|
| **J1** | 唤醒（喊"bt"） | KWS 命中，进入监听 |
| **J2** | 提交（提问） | 正常 LLM 回复+播报 |
| **J3** | 打断（播报中说话） | 旧声立即停，新回复出 |
| **J4** | 退出（说"好的"） | 退出词生效，回到待唤醒 |

## §6 打断 / 退出语义（08-13 已拍板项回归）

| 编号 | 用例 | 预期 |
|---|---|---|
| **B1** | 打断效果 | 旧声立即停（体感 <150ms），说完 2s 新回复出 |
| **B2** | 退出词集 | **只保留** `{行, 明白, 了解, ok, 好的}`；"行/感谢/谢谢"已删、"再见"未加——真机验证无残留旧词触发 |
| **B3** | 退出语义 | 说"再见"应**不退出**（已定不加）——确认模型不误判 |

## §7 三模式隔离边界（2026-08-14 澄清，task #8）

| 编号 | 用例 | 预期 |
|---|---|---|
| **L4** | 双开互斥 | live 开启时点 jarvis 监听 → **前者先停**；反之亦然；**两者永不同时运行** |
| **M1** | 模式语义正确 | live 模式问话走四态判定；jarvis 模式唤醒后走三态；call 模式文本直达无 decision token 泄漏 | 

## §8 性能体感（可选项）

| 项 | 目标 | 实测 | 备注 |
|---|---|---|---|
| 首句出声延迟 | ≤1s | ＿＿＿s | 唤醒后提问→第一句 |
| 委派 e2e 耗时 | — | ＿＿＿s | C2 触发→结果播报（codex 首轮 ~35s 参考） |
| 连续 5 轮对话无异常 | 稳定 | ＿＿ | 记录异常 |

---

## §9 测试记录表（留痕，逐条填写）

| 编号 | 结果（✅/❌/⚠️） | 现象/偏差描述 | 日志证据（时间点/关键词） | 备注 |
|---|---|---|---|---|
| C1 | | | | |
| C1b | | | | |
| C2 | | | | |
| C3 | | | | |
| C4 | | | | |
| C5 | | | | |
| C6 | | | | |
| C7 | | | | |
| E1 | | | | |
| E2 | | | | |
| E3 | | | | |
| D1 | | | | |
| D2 | | | | |
| D3 | | | | |
| A1 | | | | |
| A2 | | | | |
| A3 | | | | |
| A4 | | | | |
| J1 | | | | |
| J2 | | | | |
| J3 | | | | |
| J4 | | | | |
| B1 | | | | |
| B2 | | | | |
| B3 | | | | |
| L4 | | | | |
| M1 | | | | |

## §10 验收后动作（测试完执行）

1. **Codex 桥接**：C1-C7 全过 → spec 草稿转正式（`draft-background-agent-codex-bridge.md` → 正式）+ 触发审查组更新 D-049（决策/唯一写者）
2. **call 模式委派**：C6 全过 → spec 转正式（`draft-call-mode-unknown-delegation.md`）
3. **共情调优**：E1-E2 全过 → spec 转正式（`draft-live-empathy-tuning.md`）；E3 benchmark 数值留档
4. **ASR cloud**：A1-A4 语义差异确认可接受 → spec 定稿（`draft-asr-provider-unified.md`）
5. **TTS 插件化（N5）**：真机 TTS 播报正常（行为零变化）→ spec 转正式（`draft-tts-provider-unified.md`）
6. 记录本表到 `.workbuddy/memory/2026-08-15.md`
