# Spec（草稿）— 四态共情类误响应调优（AFK 工作线）

> 状态：**草稿**（**保留原因已核实**：功能部分已落盘，但第 3 项「persona 文件级弱化」依赖真机 benchmark 结果决定，故不转正）
> 日期：2026-08-14（改动落盘）｜关联：`doc/research/addressee-cross-validation-2026-08-12.md` §B（必测②）、`doc/specs/addressee-detection.md`（正式，Phase2 四态）、08-13 真机失败句实证
> 背景：live 四态（silence/response/delegation/**not-for-me**）已上线，但**共情类自言自语**（"哎呀这关怎么那么难" / "唉，好累"）仍被判 `response` 并播报共情安慰——08-13 真机实证：与 few-shot"这关怎么这么难啊"几乎同款的"哎呀这关怎么那么难呢"→ `decision=response` + "看到你遇到困难了…"。报告 §B.4 定位根因：**persona "User is your Pilot" 压制 not-for-me 召回（主阻塞）** + few-shot 措辞泛化失败。

---

## 1. 目标

让 live 四态把**共情/抱怨类自言自语**（无请求、无称呼）判为 `not-for-me`（不播报），同时**不误伤**真正面向 AI 的提问/指令（漏判率保持 0%，宁漏不乱插性质不破）。

## 2. 根因（来自交叉验证 §B + 真机实证）

| # | 根因 | 证据 |
|---|---|---|
| 1 | **persona Pilot 假设压制**：`compose_system_prompt` 把 bt-7274.txt（"User is your Pilot"）**前缀**在 LIVE 四态 prompt 之前，模型先入为主"都是铁驭在对我说" | 报告 B.4：D 变体（persona+重构）not-for-me 召回 **0%**；B/B2 仅 8% |
| 2 | **few-shot 措辞泛化失败**：few-shot 有"这关怎么这么难啊"，但模型对变体"哎呀这关怎么那么难呢"仍判 response | 08-13 真机：几乎同款仍误判 |
| 3 | **缺强化句**："Even if you can answer it, stay silent" 是报告 C 变体（召回 8%→36%）关键句，生产版缺失 | 报告 B.6 |

## 3. 调优方案（2026-08-14 已落盘 2 项，第 3 项待评估）

### 3.1 ✅ 已落盘：`services/webinfer/prompt_constants.py` `LIVE_SYSTEM_PROMPT_EN`

1. **Addressee override 段（新增，persona 弱化声明）**：LIVE prompt 开头显式声明"即使 persona 称你为 Pilot，房间内不是所有语音都对你说的；addressee 判定是最优先级规则，覆盖 persona 的 'assist the Pilot' 指令"——**不改 bt-7274.txt**（避免影响 jarvis 生产链路），只在 live 模式 prompt 层声明。
2. **强化句**：Not-For-Me 规则加 "even if you CAN answer it, stay silent when it is not for you. A frustrated or tired complaint (… ) is the speaker venting to themselves, NOT a request for you to comfort them."
3. **few-shot 扩到 11 个**（对齐报告 B.6 变体数）：新增共情/感叹反例 "哎呀，这关怎么那么难"（真机失败句精确变体）、"好累啊，今天"、"完了完了，要迟到了"、"哇，这画面真好看"。

### 3.2 ✅ 已落盘：`services/scripts/benchmark_4state_notforme.py`

测试集新增 `N01b "哎呀，这关怎么那么难"`（08-13 真机失败句）作为回归项——验证调优后该句应判 `not-for-me`。

### 3.3 ⏸ 待评估：persona 文件级弱化（bt-7274.txt）

报告建议将 "User is your Pilot" 弱化为 "User is the primary speaker; you may also overhear non-directed speech"。**影响面大**（jarvis 主链路共用 persona），若 3.1 的 prompt 层 override 生效足够则不做；benchmark 后决定。

## 4. 验证计划

- [x] webinfer 测试全量 **385/385 绿**（prompt 改动无回归）
- [ ] **benchmark 真机**（需 llama-server 7060 在线）：`python services/scripts/benchmark_4state_notforme.py`
  - 判据：N01b 判 not-for-me；非面向误响应率较调优前（52%）下降；**面向句漏判率保持 0%**（宁漏不乱插性质）
- [ ] live 真机：live 模式说"哎呀这关怎么那么难" → 无播报（`decision=not-for-me` 日志）
- [ ] jarvis 回归：jarvis 模式不受影响（LIVE prompt 只 live 用，jarvis 走 config_system_prompt）

## 5. 边界 / 风险

- 只影响 live 模式（`_resolve_base_system_prompt` 按 interaction_mode 路由，jarvis/call 零改动，测试已证）。
- 共情句误判为 not-for-me 的代价：铁驭真在向 AI 倾诉时 AI 不回应——报告判据接受（"宁漏不乱插"：not-for-me 误插可接受，漏判不可接受）。
- benchmark 数值为快照（temperature 0.8 有随机性），方向结论为准。

## 6. 关联

- 正式 spec：`doc/specs/addressee-detection.md`（Phase2 四态，§4.2）
- 交叉验证：`doc/research/addressee-cross-validation-2026-08-12.md` §B（B.4 persona 压制 / B.6 few-shot / B.7.2）
- 立项：`doc/main/00-main-direction.md` §4.0b（四态共情类调优，AFK 工作线）
