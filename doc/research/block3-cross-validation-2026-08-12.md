# 块3 交叉验证报告：Barge-in/Turn-Taking 深潜 vs 本地事实

> 日期: 2026-08-12
> 方法: 块3 深度研究（13 附件）→ 子代理提取 110 条断言（`.workbuddy/tmp/block3-assertions.md`）→ 对照本地代码核验（jarvis_mode.py / smart_turn_adapter.py / vad_bypass.py / response_format.py）
> 原则: 项目文档仅供查看、结论独立论证；服务端对"我们的现状"的假设须以本地代码为真
> 状态: Draft（供用户探讨；采纳项将修订 `unified-turn-controller.md`）

---

## 一、核心核验发现（3 处现状失准，全部实证）

| # | 报告假设 | 本地事实 | 判定 |
|---|---|---|---|
| 1 | "Smart Turn v3.2 可能是文本输入，存在 100–500ms 转录延迟"（断言 24/38/86/97） | **音频原生**（`smart_turn_adapter.py` L31-32 docstring："The model is audio-native: it does NOT consume a transcript input"；pipecat-ai/smart-turn-v3 ONNX） | **证伪** → "LiveKit Turn Detector 替代"动机不成立 |
| 2 | "Layer 1 的 min_silence_duration_ms=100ms 已做端点检测，与 Layer 2 职责重叠"（断言 21） | 本地 `vad_bypass.py`：Silero VAD 仅做 **KWS 软门控**（`is_speech()`，不做端点决策，默认 OFF，fail-open） | 部分属实（草稿如此，本地现状天然一致） |
| 3 | "Decision Token 应采用分类头方案而非特殊 token"（断言 42） | 本地决策 token = prompt 驱动特殊 token（`</silence>/</response>/</delegation>`），`parse_model_decision` 解析（response_format.py:53），**ADR0006 核心 IP**；纯 CPU llama.cpp 无法加分类头（需改模型/训练） | **与核心 IP 冲突，不采纳** |

**根因（同块0）**：服务端只看到我方草稿文字，未读本地代码——草稿写"Silero VAD→Smart Turn v3.2→Decision Token"，报告据此推断 Smart Turn 是文本模型。交叉验证的价值再次体现。

---

## 二、5 个修正点逐条核验结论

### 修正点 1：HSM 12 状态 —— **部分采纳（语义化拆分，不强行两层 HSM）**

**采纳**：
- `TURN_STARTED` 拆 `USER_SPEAKING` + `PROCESSING`（断言 7）——语义清晰，采纳；
- `INTERRUPTED` 拆 `HARD/SOFT`（断言 8/14）——**本地 jarvis 已有 `TTS_PAUSED`（=HARD 打断）**，拆开后直接对应，采纳；
- 补 `COOLDOWN`（200–500ms 防抢话，断言 5）——采纳；
- 补 `PRE_SPEECH`（TTFT>500ms 填充语"让我想想"，断言 6/71）——采纳（LLM 纯 CPU TTFT 500-2000ms，**本场景刚需**）。

**不采纳/简化**：
- 两层 HSM 父状态嵌套（SESSION_ACTIVE/USER_TURN/AGENT_TURN，断言 9）——**单机单会话项目过度设计**；jarvis 现有扁平状态机（jarvis_mode.py:429-456，7 态）已够用。采用"扁平 + 语义化命名"。
- 缺失状态表 7 项（断言 15）里 `QUEUED/PENDING`、`TOOL_CALLING`——本项目无工具调用链，暂不引入。

**与 jarvis 现状的映射**（重要：统一核心应**吸收**而非从零造）：
| 草稿新增态 | jarvis 已有对应 |
|---|---|
| WARM_UP | `WAKE_DETECTED`（唤醒词听到，播 wake.wav） |
| ERROR | `ERROR`（已存在） |
| HARD_INTERRUPTED | `TTS_PAUSED`（已存在） |
| WAIT_ASR_CONFIRM | `WAIT_ASR_CONFIRM`（唤醒确认，已存在） |

→ 统一核心状态机 = jarvis 状态机**泛化**（去掉 KWS 专属，加 live 的 USER_SPEAKING/PRE_SPEECH/COOLDOWN）。

### 修正点 2：VAD 级联职责 —— **现状天然一致，无需改动；补充参数**

- 核心前提证伪（见 §一.1），"LiveKit Turn Detector v1-mini 替代"**不采纳**（本地 Smart Turn 已音频原生，引入 135M 新模型徒增负担）。
- "Layer1 仅语音检测、端点决策交 Layer2"——本地 `vad_bypass.py` 已如此（is_speech() 供门控，端点决策在 jarvis `_handle_kws`/Smart Turn），**无需改**。
- **采纳参数**（并入配置矩阵）：双阈值 0.65/0.75（断言 12）、打断 VAD 参数族 threshold 0.5-0.7 / min_speech 100-200 / min_silence 300-500 / speech_pad 30-50（断言 93）。
- 保留项：四级降级路径（断言 29）作为长期设计；动态停顿阈值（断言 35）作 P2。

### 修正点 3：Decision Token 多级 —— **与核心 IP 冲突，暂不采纳（P2 研究）**

- "分类头方案"（Freeze-Omni，断言 42）：**否决**——推翻 ADR0006 决策 token 机制、纯 CPU 不可行。
- 保留价值：三态语义（listen/speak/idle，断言 43）与本地 `silence/response/delegation` **天然同构**；backchannel（"嗯/对"）作为远期 P2。
- 采纳论文依据（不采纳实现）：Full-Duplex-Bench "显式控制模块优于端到端"（断言 40）**印证我们决策 token 路线正确**——这是对核心 IP 的正面背书。

### 修正点 4：7 场景配置矩阵 —— **采纳（高价值，P1）**

- 7 场景（直播/语音助手/实时对话/会议/客服/教育/面试）× 3 参数族（VAD/Turn Decision/Interruption）（断言 52/55/61）——直接扩充草稿 2 场景矩阵。
- **本地映射**：直播=live 模式（打断开、主动搭话开）；语音助手=jarvis 模式（唤醒门开、打断开）；实时对话=未来全双工核心态；会议/客服/教育/面试=预留场景（当前不实现，仅入矩阵）。
- 采纳 Vapi 三档预设思想（Aggressive/Normal/Conservative，断言 57）——映射为直播/语音助手/保守三预设。
- Deepgram Eager EOT（断言 56）——对本地纯 CPU 无对应组件，P2 参考。

### 修正点 5：Sentence Buffer 等工程 —— **采纳（P0，本场景刚需）**

- **Sentence Buffer（断言 63-65）**：本地 TTS 是云端 MiniMax，流式按句 flush 可显著降感知延迟——**采纳为 P0**。注意：须在 strip 决策 token 后的 content 上生效（决策 token 机制不变）。
- LLM 三层超时（TTFT 500ms 填充语/总生成 5s/连续 3 次降级，断言 71）：**采纳**——纯 CPU TTFT 500-2000ms，填充语是刚需（对应 PRE_SPEECH 态）。
- 流水线并行（断言 66，1500→755ms）：采纳为设计方向（ASR/L2 并行已部分具备）。
- 四级降级（断言 70）：采纳为长期（本地已有 fail-open 传统：Smart Turn/VAD 均 fail-open，天然一致）。
- OpenTelemetry（断言 72）：P1（本地已有结构化日志，OTel 可后接）。
- KV Cache 预热 / 预测性 Prefill（断言 45/68）：**纯 CPU llama.cpp 收益有限**（vLLM 方案不适用），P2 验证 llama.cpp prefix caching。

---

## 三、参考代码 6 态 vs 推荐 12 态（内部不一致处理）

报告自身不一致（断言 110）：主报告推荐 12 态，参考实现仅 6 态（IDLE/LISTENING/DECIDING/THINKING/SPEAKING/INTERRUPTED）。**处理**：以"参考实现为可运行基线 + 推荐态为增量"理解；草稿更新采用**语义化扁平状态机**（吸收 jarvis 已有态），状态集约 10 个，不追求 12 态 HSM 形式。

---

## 四、修订后统一 Turn Controller 状态机（草稿 v2 提案）

```
[IDLE] → [WARM_UP(WAKE_DETECTED)] → [LISTENING] → [USER_SPEAKING]
   ↑           │ (唤醒门，jarvis)       ↑              │
   │           ▼                       │              ▼
   │      (live 常驻 LISTENING)        │          [PROCESSING]
   │                                   │              │ (LLM 决策)
   │                                   │              ▼
   │                              [PRE_SPEECH] ← 填充语(TTFT>500ms)
   │                                   │              │
   │                                   ▼              ▼
   │                              [SPEAKING] ←── [THINKING/决策中]
   │                                   │
   │                            打断(声学+语义)
   │                                   ▼
   │                             [HARD/SOFT_INTERRUPTED]
   │                                   │ (jarvis TTS_PAUSED)
   │                                   ▼
   │                              [COOLDOWN(200-500ms)] → LISTENING
   └── [ENDED] ← 退出语义 / [ERROR]（任意态，可恢复）
```

状态集：IDLE / WARM_UP / LISTENING / USER_SPEAKING / PROCESSING / PRE_SPEECH / SPEAKING / HARD_INTERRUPTED / SOFT_INTERRUPTED / COOLDOWN / ENDED / ERROR（12 个，但扁平结构，吸收 jarvis 7 态中的 WAKE_DETECTED/TTS_PAUSED/ERROR/WAIT_ASR_CONFIRM）。

---

## 五、采纳/否决汇总

| 修正点 | 结论 | 动作 |
|---|---|---|
| 1 状态机 | 部分采纳（语义化拆分，吸收 jarvis 已有态，不强行两层 HSM） | 更新草稿 v2 |
| 2 VAD 级联 | 前提证伪；现状天然一致；参数采纳 | 配置矩阵补双阈值/打断参数 |
| 3 Decision Token | **与 ADR0006 冲突，暂不采纳**；三态语义同构作背书 | 保留现状，P2 研究 |
| 4 配置矩阵 | 采纳（7 场景 × 3 参数族 + 3 预设） | 更新草稿 v2 |
| 5 工程落地 | 采纳（Sentence Buffer P0 / LLM 超时三层 / 降级） | 更新草稿 v2；KV Cache P2 |

## 六、本地核验证据

```
smart_turn_adapter.py L31-32 → "audio-native: it does NOT consume a transcript input"（证伪"文本输入"假设）
jarvis_mode.py:429-456 → JarvisState 7 态（KWS_LISTENING/WAKE_DETECTED/DIALOG_ACTIVE/TTS_PAUSED/EXIT_DETECTED/ERROR/WAIT_ASR_CONFIRM）
vad_bypass.py L8-12 → KWS 软门控用途（is_speech()，不做端点决策，默认 OFF，fail-open）
response_format.py:53-102 → parse_model_decision 三态（silence/response/delegation），ADR0006 核心 IP
```
