# 块0 交叉验证报告：全双工语音全景调研 vs 本地事实

> 日期: 2026-08-11
> 方法: 万得深度研究报告（14 份附件）→ 子代理提取 150 条可验证断言（`.workbuddy/tmp/block0-assertions.md`）→ 逐条对照本地 SSOT（`决策/`、`doc/specs/`、真实代码）核验
> 原则: 项目文档仅供查看，结论独立论证；全局思想（沿决策链看现状）；可接受全盘整改、不在不稳地基上打补丁
> 状态: Draft（供用户探讨，冲突点将走 spec 草稿→验证→替换流程）

---

## 一、核验方法

1. 服务端报告 14 份附件全部通读，子代理提取 150 条断言（现状描述 28 / 技术参数 70 / 建议方案 21 / 对比结论 19 / 可验证清单 12）。
2. 关键断言用本地代码/文档实证核验（`grep`/`glob`/读 SSOT），非仅凭文档字面。
3. 结论分级：**证实 / 证伪 / 部分属实（沿决策链说明） / 无法本地验证（外部事实）**。

---

## 二、现状描述类断言核验（报告对"我们"的描述 vs 本地事实）

| # | 报告断言 | 本地事实 | 结论 |
|---|---|---|---|
| 45 | "ASR 和 TTS 已部分走云端（MiniMax TTS）" | TTS 确实云端 MiniMax(:8985, D-047)；**ASR 默认全本地**（:8993 vLLM / in-proc sherpa paraformer），云端仅 opt-in（D-045 08-08 落地，D-080 外部不可达显式报错） | 部分属实（TTS 对，ASR 错） |
| 摘要/87 | "ASR 完全依赖云端" | 证伪：默认本地；云端 08-11 实测帮倒忙（"bt"→"滴滴"）后 pivot 本地 paraformer promotion | **证伪**（决策链误读：报告看到 `run-windows.env` 云端配置残留即推断） |
| 46 | "缺少系统级 Turn Controller——VAD/ASR/LLM/TTS 协调需从'各模块独立运行'升级为'统一状态机调度'" | 项目**已有**：jarvis_mode.py 完整状态机（KWS_LISTENING→WAIT_ASR_CONFIRM→DIALOG_ACTIVE）+ Smart Turn v3.2 语义端点（`smart_turn_adapter.py`）+ live 三判断（沉默/搭话/回复）；但**无统一跨模式 Turn Controller**（live/jarvis/call 各管各的） | 部分属实（"各模块独立"错，但"缺统一调度层"对——正是块4 归一化的靶点） |
| 47 | "传输层如果当前使用 WebSocket，应考虑迁移 WebRTC" | WebRTC **已实现**（`services/webui/src` 8 文件：audio_processor/jarvis_mode/jarvis_routes/server/index.html 等，D-2026-08-06-001 校验行实证） | **证伪其隐含前提**（报告用"如果"猜测，实际已做） |
| 67 | "llama.cpp 支持 Qwen2-VL（需 GGUF）" | 一致：本项目 llama-server 正跑 8.19B IQ4_NL 多模态模型（今日日志实证） | 证实 |
| 74/149 | "记忆需分层（STM/Session/LTM）+ Mem0 是 SOTA" | 项目有 memory-store(:8997, sqlite+usearch) + 决策记忆 + 上下文架构（`决策/业务-上下文架构.md`、`业务-决策记忆.md`） | 部分属实（分层已有，Mem0 架构可作对照） |
| 5/8/14 | "S2S 纯 CPU 不可行/极慢，2026 不推荐主架构" | 与项目约束（纯 CPU llama.cpp+sherpa-onnx）一致 | 证实（与 D-2026-08-06-001"不学 Realtime 协议"方向自洽） |

**小结**：报告对项目现状的**事实性描述 5 条中 3 条有误或部分有误**（ASR 依赖云、缺 Turn Controller、传输层用 WS），根因是报告**未读到本地决策链**，仅凭配置文件残留与通用架构推断。这不影响其外部技术梳理价值，但**凡涉"我们的现状"的结论须以本地核验为准**。

---

## 三、报告内部不一致（子代理标记 5 处，本报告确认）

| # | 不一致 | 影响 |
|---|---|---|
| 1 | SenseVoice 实时倍率：主报告/report_ch03 "17 倍" vs 02_cascade "340x"（同称 10s 音频 ~70ms） | 低（引用时须统一；340x 更接近 70ms/10s=143x 量级，两值都可能不准） |
| 2 | FireRedChat 对比：8.4 只给误打断率（LiveKit 33.4%/Ten 78.1%），03 另给 T90 延迟（LiveKit 140ms/Ten 90ms） | 中（两维度不可混用；引用须标注维度） |
| 3 | ElevenLabs 语言数：05 文件 3.1 "70+ 语言" vs 4.4 "29+ 语言" | 低 |
| 4 | Silero VAD 模型大小："1.6MB"（主报告/8.4） vs "2MB/2.2MB"（ch05/04） | 低（可本地下载实测） |
| 5 | 级联端到端延迟：7.1 矩阵 "500–800ms" vs 02 文件 "400-800ms" | 低 |

**结论**：报告技术参数存在内部不一致，引用前须按来源逐条校对；5 处均为非关键性偏差，不影响架构判断。

---

## 四、建议方案分级（采纳 / 整改深化 / 否决 / 待验证）

### ✅ 采纳（有据且与项目现状/方向一致）
1. **保持级联流式架构，不推倒重来**（报告 8.1/8.5）——与项目架构、纯 CPU 约束、既有 ADR0006（决策 token 单入口）三方一致。
2. **TTS 保持云端 MiniMax 为主**（报告 5.4/7.5）——项目已如此（D-047），音色质量优先的产品决策成立。
3. **KWS 端侧神经网络 + 99% 目标**（报告 05 文件 1.2/4.4）——项目 KWS=sherpa-onnx 进程内已端侧；"99% 准确率、<0.1 误唤醒/小时"可作为块1 KWS 的目标基准（现状 v4 recall 49.06%/FAR 2%，差距巨大，正是块1 深潜理由）。
4. **llama.cpp + sherpa-onnx 纯 CPU 栈**（报告 5.5/8.1）——项目已用，一致。
5. **Silero VAD 复用**（报告 3.2/4.1/7.5）——项目 08-10 已启用（`vad_bypass.py`，今日日志实证 VadBypass.available=True）——**注意**：这本身是决策链演化实例：D-2026-08-06-001 矩阵曾锁"不做 Silero VAD"，08-10 实际实现推翻之。报告推荐与**当前**现状一致。

### 🔧 整改/深化（有价值，须走 spec 草稿→验证→替换流程）
1. **统一 Turn Controller**（报告 46/51/62/85 核心建议）——项目有 jarvis 状态机 + Smart Turn + live 三判断，但三者割裂。**这正是块4（live/jarvis 归一化）的直接靶点**：设计统一状态机（VAD→Turn Detection→LLM 语义），把 jarvis 唤醒链路、live 三判断、Smart Turn v3.2 收敛进一个调度层。→ 待写 spec 草稿。
2. **中文 Turn Detection 模型**（报告 51：中文韵律——声调、语气词"吧/嘛/呢"与英文不同）——项目 Smart Turn v3.2 是通用语义模型（ONNX），未针对中文韵律。是否自研/微调需块3 深潜后定。→ 块3 输入。
3. **打断后上下文恢复**（报告 52/54：截断历史 + `previous_agent_utterance_interrupted` flag + LLM 感知）——项目 TTS 打断后是否有历史截断+flag 注入需代码核查。→ 待验证 + spec。
4. **Jitter buffer 80-150ms 目标**（报告 134）——报告称"降低缓冲延迟 80-150ms"的来源；项目 WebRTC 缓冲现状需实测。→ 待验证。

### ❌ 否决/搁置（与既有决策冲突或约束不符）
1. **S2S 端到端主架构**（报告自己也说"短期内不推荐"，断言 5/8）——纯 CPU 不可行 + 决策 token 核心 IP 与通用 S2S 协议冲突（ADR0006）。远期参考（Moshi 双流建模、Freeze-Omni 三状态可作思想借鉴）。
2. **"迁移 WebRTC"**（报告 47/87）——已实现，无需迁移；服务端↔模型 API 用 WebSocket 是报告自己认可的混合架构（断言 60/81）。
3. **Pipecat 作为管道框架**（报告 30：自身承认 Windows 本地体验差）——不采纳。
4. **vLLM / CosyVoice / ChatTTS / 8B+ 本地 LLM**（报告 68/36/41：CPU 不可行或许可证限制）——与纯 CPU 约束冲突。

### ⏳ 待验证（外部事实，本地可实测或需查证）
- SenseVoice-Small RTF 0.015 / 模型 <100MB（断言 37/63/95）——可本地下载实测；项目现用 paraformer，可对比。
- llama.cpp 3B Q4 10-20 tok/s（断言 66/96）——本地硬件实测；项目现跑 8.19B IQ4_NL。
- piper TTS 中文音色（断言 40/97）——备选 TTS，非当前需要。
- KWS 99% / <0.1 误唤醒/小时 行业基准（断言 144）——块1 目标基准。
- Moshi 200ms / Mimi 12.5Hz 1.1kbps（断言 4/11/13/99）——论文可查（arXiv:2410.00037）。

---

## 五、与既有决策链的关系（全局思想核验）

| 报告观点 | 本地决策链 | 结论 |
|---|---|---|
| "ASR 完全依赖云端" | 07-13 本地 vLLM → 08-08 SiliconFlow opt-in → 08-08 外部不可达显式报错 → 08-11 云实测帮倒忙、pivot 本地 paraformer promotion | 报告误读现状；决策链已证明"本地为主、云为 opt-in"是踩坑后的正确收敛 |
| "缺 Turn Controller" | 07-22 起 live_adapter 三判断 → 08-03 三模式隔离（D-001/002）→ 08-06 采纳 Smart Turn v3.2 → 08-10 VAD 启用 | "缺统一调度层"为真，但非"从零开始"——已有三套局部逻辑待收敛（块4） |
| "WebRTC 迁移" | D-2026-08-06-001 校验行已实证 WebRTC 在 webui 使用 | 已做，报告滞后 |
| "Silero VAD 复用" | 早期决策"不做 Silero"（D-2026-08-06-001）→ 08-10 实际启用（vad_bypass.py）| 报告与**当前**一致；且暴露"决策文档滞后于实现"的治理问题（vad 决策应更新） |

**治理发现**：D-2026-08-06-001 矩阵中"VAD 本体(Silero) 不做"与 08-10 已启用 Silero VAD 的实现**冲突**——决策文档未随实现更新。按"文档仅供查看"原则此不阻塞，但属需治理的漂移项，建议后续由审查组处理。

---

## 六、spec 草稿候选（走 草稿→验证→替换 流程）

1. **`doc/specs/unified-turn-controller.md`**（高优先）：统一 Turn Controller 状态机——收敛 jarvis 唤醒链路 / live 三判断 / Smart Turn v3.2 / VAD 门控，输出统一 turn 语义；直接服务块4（live/jarvis 归一化验证）与块3（barge-in 深化）。验证方式：先以草稿形式设计状态机 + 与现有三套逻辑对照，真机试点后再替换/新建 spec。
2. **`doc/specs/draft-bargein-context-recovery.md`**（中优先）：打断后上下文恢复（历史截断 + interrupted flag + LLM 感知），对照报告 52/54 与项目现状。
3. **块1/块2 输入**：KWS 99% 目标基准（块1）；LLM 长输出截断与 TTFT 500-2000ms 的关联（块2，报告 89 风险一）。

---

## 七、结论

1. **服务端核心判断"根基稳固、不推倒重来"成立**，但成立的原因是**本地决策链早已走过同样的论证**（不是报告的新发现）。
2. **报告对项目现状的描述可靠度低**（5 条事实描述 3 条有误），对**外部技术全景的描述价值高**（论文/项目/参数清单 + 5 处内部不一致已标注）。→ 交叉验证后的正确用法：**外部知识作参考，现状判断以本地为真**。
3. **最高价值输出**：① 统一 Turn Controller 是真实缺口（块4 靶点）；② KWS 99%/<0.1 误唤醒/小时 可作为块1 的目标基准；③ 中文 Turn Detection、打断上下文恢复为块3 预留输入。
4. **无"地基不稳"证据**：级联架构、纯 CPU 栈、决策 token 单入口均获外部佐证。真正需要整改的是**编排层收敛**（Turn Controller）而非地基。

---

## 附：本地核验证据（grep 实证）

- WebRTC 已用：`grep -rln "WebRTC" services/webui/src` → 8 文件命中
- Smart Turn 已有：`grep -rln "smart_turn" services/` → smart_turn_adapter.py / jarvis_mode.py / vad_bypass.py / 测试
- Silero VAD 已启用：`glob **/vad*.py` → vad_bypass.py（今日日志：VadBypass.available=True）
- ASR 本地默认：`决策/服务-语音栈.md` D-045（:8993 vLLM 默认，云端 opt-in）、D-080（外部不可达显式报错）
- TTS 云端：D-047（:8985 MiniMax Rapid Clone + T2A v2）
- 决策 token 单入口：ADR0006 + D-2026-08-03-002
