# Addressee Detection 交叉验证报告（2026-08-12）

> 对云端调研（12 份附件、140 条断言）对照本地代码/环境逐条核验。评估原则（用户授权 20:4x）：**效果优先，大改不怕，云端兜底，硬件不设限**。
> 判定标准：✅ 证实（本地实证） / ⚠️ 部分属实或需实测 / ❌ 证伪或与本地冲突

---

## 一、核心推荐（H2 混合路线）核验

| 报告主张 | 判定 | 本地证据 |
|---|---|---|
| **做**——不做则多人场景频繁误响应 | ✅ 成立 | 本地 live 免唤醒常驻监听，VAD 有语音即进 ASR→LLM，无任何说话对象判断；自言自语/旁人说必被响应（日志实证：'对'/'嗯' 等 garbage 被提交后丢弃，但 LLM 已白跑） |
| **H2 混合（声学+语义并行融合）** | ✅ 方向成立 | 声学解决"谁在说"（前置过滤省计算）、语义解决"对谁说"（控误响应）——两缺陷互补，论证自洽 |
| 两阶段：Phase1 声学预筛（1-2 周）→ Phase2 四态+融合（2-4 周） | ✅ 合理 | 分阶段可独立验收/回滚，符合项目渐进接入传统 |

## 二、5 个重点核验点

### 1. CAM++ 在现有 sherpa-onnx 的可用性 — ✅ 证实（且成本低于报告假设）
- **本地实证**：sherpa-onnx **1.13.4** `dir(sherpa_onnx)` 含 `SpeakerEmbeddingExtractor` / `SpeakerEmbeddingExtractorConfig` / `SpeakerEmbeddingManager` / `OfflineSpeakerDiarization`——**原生支持 speaker embedding，无需升级依赖**（报告假设需集成，实际现成）。
- 断言 58/59（Extractor/Manager API 行为、L2-normalized、cosine、无 PLDA）与 sherpa-onnx 官方 API 一致 ✓。
- 模型需下载：`3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx`（27MB）——本地模型目录现无 speaker 模型（只有 asr/kws/vad），一次性下载成本。

### 2. 四态 decision token 与 ADR0006 兼容性 — ✅ 不冲突（需规范流程）
- ADR0006 = llm-gateway **单入口路由**规范（决策 token 静默回归兜底在 jarvis 侧）；四态 = 同一入口内扩语义（silence/response/delegation/**not-for-me**），**不违反单入口原则**。
- 需走 spec→验证→替换流程（ADR0006 核心 IP 的语义扩展，涉及 prompt_constants + parse_model_decision + turn_streaming 消费侧）。

### 3. AddresseeDetector 插在 VAD→ASR 之间 — ✅ 可行
- 本地链路：live_mode.py `feed_audio(pcm)` → VAD → ASR(feed_chunk) → endpoint → LLM。AddresseeDetector 插在 VAD 段后、ASR 前（或并行），与报告建议一致。
- 断言 123（不改 13 态状态机数量，仅加门控）——与本地 turn_controller 架构一致，侵入最小。
- ⚠️ 子代理标注：报告 6.2"不改变状态数量" vs 6.4"新增 ENROLL 状态"并存——**注册流程需一个显式状态/模式**（建议放 live_mode 层而非 turn_controller 核心，保持核心纯净）。

### 4. 融合权重 0.4/0.6 — ⚠️ 设计默认值，无实证
- 报告自认"初始加权平均起步，后续逻辑回归（需几百条标注）"。0.4/0.6 是合理起点但**非实测最优**——落地时留可配置 + 采集数据后网格搜索（清单验证方法也如此）。

### 5. 纯文本语义 80.9% — ⚠️ 外部基准，需本地实测
- 80.9% vs 80.6% 来自 **GPT-4o 三方对话 benchmark（IWSDS 2025, arXiv 2501.16643）**，非本地 llama.cpp 8.19B 实测。
- **判定**：不采信"纯语义不可用"为绝对结论，但作为"语义单独不可靠、需声学配合"的依据成立。落地时用本地模型跑同型 benchmark 验证（清单验证方法）。

## 三、❌ 发现的错误表述（服务端对现状的失准）

| 报告表述 | 本地事实 | 判定 |
|---|---|---|
| **"部署环境: Windows 单机，纯 CPU 推理"**（断言 52，12 份报告头部） | LLM/VLM 8.19B 在 **GPU**（run-windows.ps1:372 `-ngl 999` 全量卸载）；纯 CPU 仅指语音侧 | **❌ 证伪**——部署修正消息未覆盖报告头部，延迟/可行性评估偏保守（LLM 语义判定实际 GPU 200-500ms 级，比"纯 CPU"乐观） |
| 方案 A 延迟"~270ms"、方案 B"~440ms"（断言 42） | 本地 LLM 在 GPU，TTFT 可能 200-500ms 级（非报告假设的 CPU 1-3s） | ⚠️ 延迟绝对值需按 GPU 重估（相对结论"方案 A < 方案 B"不变） |

## 四、⚠️ 报告内部口径差异（落地时需选定，勿混用）

| 项 | 差异 | 落地选择建议 |
|---|---|---|
| CPU 增量 | 主报告"<5% 平均" vs 02 报告"+5-10% 瞬时" | 瞬时口径做预算，平均口径做宣传 |
| 注册时长 | 01"3-10 秒" vs 其余"2-3 秒" | 2-3 秒（02 报告与主报告 2.4 一致，01 为宽松上限） |
| 融合标注量 | "几百条" vs "500-1000 条" | 500-1000（更保守，标注成本可控） |
| 04 报告 4.2"偏高召回" vs 主报告"宁可漏不可乱插" | **方向矛盾** | **以主报告+用户授权为准：宁可漏、不可乱插**（04 报告高召回建议弃用） |
| ASR CPU 占用 | 03"10-20%" vs 02"20-40%" | 不影响 addressee 决策，仅记录 |
| 声学延迟 | 320-410 / 300-400 / 300-500ms 三口径 | 统一 300-400ms（与 ASR 并行不增串行延迟是核心结论） |
| 性能预估（70-90% 过滤、误响应降 50-80%） | 无实测来源的预估 | **必须实测**（Phase1 上线后统计真实过滤率/误响应率） |

## 五、风险矩阵核验（6 项）

- 风险 1（CAM++ 中文桌面区分度不足）— ⚠️ 真实风险，缓解=本地实测注册/测试语音，必要时换 ERes2NetV2（报告已给）✓
- 风险 2（not-for-me 准确率不足）— ✅ 概率"高"的判断合理（GPT-4o 都只 80.9%），缓解=prompt/few-shot + 数据 fine-tune ✓
- 其余 4 项（ASR 浪费、注册被绕过、融合退化、LLM 上下文）— 均为合理风险识别，缓解措施可落地 ✓

## 六、交叉验证总判定

1. **方向正确**：H2 混合路线（声学预筛 + 语义精判 + 融合）成立，且**本地落地成本低于报告假设**（sherpa-onnx 原生 Speaker API 现成）；
2. **一处重大失准**：部署形态"纯 CPU"（LLM 实为 GPU）——不改变路线方向，但延迟预算应乐观化；
3. **7 处口径差异**已选定落地口径（见 §四）；
4. **关键实测项**（落地前必须做）：① CAM++ 本地 RTF/区分度实测（下载模型跑 speaker-identification.py 示例）；② 本地 llama.cpp 四态 not-for-me 准确率实测；③ Phase1 上线后统计真实过滤率/误响应率。

## 七、落地方案建议（下一步，走 spec 草稿流程）

- **Phase 1（声学预筛，独立上线可回滚）**：
  1. 下载 CAM++ onnx + 本地 RTF 实测（验证 300-400ms/CPU<5% 假设）；
  2. 注册流程：录制 2-3s 注册语（2-3 段取平均，EMA 更新 α=0.1-0.2）——放 live_mode 层（新增 ENROLL 模式态，不碰 turn_controller 核心）；
  3. AddresseeDetector 插 live_mode feed_audio 链路：VAD 段后 speaker embedding + cosine（阈值 0.55-0.65，宁可漏不可乱插）→ 非目标说话人直接丢弃（省 ASR/LLM）；
  4. env 闸门默认关（fail-open 传统，验收开启）。
- **Phase 2（四态 + 融合）**：
  1. decision token 扩四态（+not-for-me）——prompt_constants + parse_model_decision + turn_streaming 消费侧 + GBNF grammar；
  2. 融合决策层（S_fusion = 0.4×声学 + 0.6×语义，双阈值 T_low 0.3 / T_high 0.7）；
  3. 本地 not-for-me 准确率实测 + 数据采集后权重网格搜索。

## 八、关联

- 调研交付：final_report_addressee_detection.md（工作区根，12 份）
- 断言清单：.workbuddy/tmp/addressee-assertions.md（140 条）
- 现有架构：live_mode.py（feed_audio 链路）、turn_controller.py（13 态）、turn_streaming.py（decision 消费）、ADR0006（单入口）
- 部署真值：run-windows.ps1:372（-ngl 999 GPU）

---

## 附录 A：CAM++ 本地实测数据（Phase 1 落地前，2026-08-12 晚）

> 实测脚本：`services/scripts/benchmark_campplus_addressee.py`
> 环境：Windows 单机 CPU，`D:/AI/envs/joyai-main/python.exe`，sherpa-onnx 1.13.4，`num_threads=2`
> 模型：`D:/AI/models/sherpa-onnx/models/speaker/3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx`（27MB，192 维，input `[N,T,80]` fbank）
> 音频真源：用户 KWS 实时捕获（16k mono 3s，`D:/AI/data/kws/mic_captures`）+ BT-7274 克隆参考音（16k mono 23s，`D:/AI/workspace/bt-voice/ref_audio/bt_reference.wav`，另一说话人）+ esc50 环境噪声

### A.1 延迟 / RTF / CPU（验证 spec §3.3 假设）

| 指标 | 实测值 | spec 假设 | 判定 |
|---|---|---|---|
| 2s 段 embedding 提取延迟 | **mean 19-20ms**（p50 ~19ms，min 17.6ms，max 29ms） | 300-400ms | ✅ **大幅优于假设**（~15-20 倍余量；一次段尾计算，非逐 chunk） |
| RTF | **0.010**（2s 音频/20ms 计算） | 0.15-0.18（离线表） | ✅ 优于（在线一次性段尾提取远低于流式逐帧） |
| CPU 增量 | **~0.037 core-seconds / 2s 段**（30 次连续提取 proc CPU≈194%，墙钟 0.57s） | 瞬时 +5-10% | ✅ 符合（一次性 ~20ms 突发，可忽略） |

结论：**声学增量实际 ~20ms（非 300-400ms），且只发生在段尾一次**；比 spec §3.3 假设乐观一个量级。与 ASR 并行不增串行延迟的结论仍成立（甚至串行也不痛）。

### A.2 区分度（同人 vs 异人 cosine 分布）

| 对比对 | n | mean | min | max |
|---|---|---|---|---|
| 同人·干净连续语音（BT vs BT，23s 参考音切 2s 段） | 45 | **0.751** | 0.677 | 0.827 |
| 异人（用户 mic vs BT 参考音） | 100 | **0.004** | -0.094 | 0.153 |
| 用户 mic 自比（3s 窗口含短 "bt" 唤醒 + 大量静音） | 45 | 0.486 | 0.185 | 0.796 |
| 环境噪声（用户 vs esc50） | 100 | 0.266 | -0.003 | 0.588 |

要点：
1. **干净连续语音下区分度优秀**：同人 mean 0.75（min 0.677），异人 mean 0.004（max 0.153）——0.6 阈值下 100% 同人通过、0% 异人误放。
2. 用户 KWS 捕获自比偏低（0.486）是**数据特性**而非模型问题：捕获是 3s 滚动窗口包住 ~0.5s "bt" 唤醒 + 大量静音，切 2s 段多数是静音。**注册流程要求 2-3s 连续真人语音段**（spec §3.1），与 clean-speech 场景一致，预期区分度按 BT 行（0.75）计。
3. 环境噪声 mean 0.266、max 0.588 均 < 0.6——噪声不致误放（宁漏不乱插方向正确）。

### A.3 阈值行为（0.55 / 0.6 / 0.65 网格）

| 阈值 | 同人 recall（BT 干净语音） | 异人 false-acc（用户 vs BT） |
|---|---|---|
| 0.55 | 1.00 | 0.00 |
| **0.60（默认）** | **1.00** | **0.00** |
| 0.65 | 1.00 | 0.00 |

> 数据快照修正（2026-08-12 QA 复核）：0.65 行同人 recall 此前误写 0.96。实测 BT 干净语音 45 对 cosine 全部 ≥0.677（min 0.677），**0 对低于 0.65 → recall = 1.00**（0.55/0.60/0.65 三档均 1.00）。

结论：0.6 默认阈值在真源数据上区分度充足；spec 建议的 0.55-0.65 可配置范围合理，宁漏不乱插方向正确（异人侧 0.65 也 0 误放，但为保同人 recall 余量保留 0.6 默认）。

### A.4 SpeakerEmbeddingManager 端到端（注册→search）

- 用前 3 段用户干净语音注册 → `manager.search(emb, 0.6)`：用户语音 **3/10 命中**（7 段低分来自静音主导切片），BT 语音 **0/10 误命中**。
- 与 A.2 一致：静音切片降低召回但不产生误放——Phase 1 门控方向（丢弃非目标、宁可漏）成立。

> 数据快照修正（2026-08-12 QA 复核）：命中数此前误写 6/10。`D:/AI/data/kws/mic_captures` 是**实时捕获目录（383 个 wav，非 git 管控）**，每次运行按 RMS 排序取前 10 的选择集随时间漂移，命中数在 3-6/10 间波动（本次快照实测 3/10）；BT 异人 0/10 误放多次复现精确一致。**附录为快照数据，数值可能随目录变化漂移**——方向结论（静音切片降召回、不产生误放）不受影响。

---

## 附录 B：本地 llama.cpp 四态 decision token 实测（必测②，2026-08-12 晚）

> 实测目的：spec §4.3 必测②——本地模型对"非面向语句"的现有判定能力（基线 A）+ 临时四态教学后的能力提升（增强 B），决定四态改造是否值得做 + few-shot 怎么调。
> 实测脚本：`.workbuddy/tmp/benchmark_addressee_4state.py`（**不改任何仓库文件**；prompt 增强为脚本内嵌）
> 调用方式：**直连 llama-server `127.0.0.1:7060/v1/chat/completions`**（OpenAI 兼容，模型 `joyai-vl-interaction-preview-iq4_nl-imat.gguf`，GPU `-ngl 999`），非流式、`temperature=0`、`max_tokens=256`。与 webinfer live 路径做了 5 句 parity 抽查（见 B.6）。
> 决策解析：复刻 `services/webinfer/response_format.py:parse_model_decision` 逻辑 + 扩展 `</not-for-me>`（delegation 标签优先 → not-for-me → response/silence 最早出现 → 无标记默认 response；空输出 → silence）。
> 原始结果：`.workbuddy/tmp/addressee-bench-results/results_{A,B1,B2,B3,C}.json`（每句 raw 输出可追溯；主跑 42 句 × 4 变体 + 补充 C 变体 = 210 次调用，**0 失败、0 超时**）。

### B.1 测试集（42 句：18 面向 + 24 非面向，中英混合）

| 组 | 类别 | 句数 | 示例 |
|---|---|---|---|
| 面向（应 response） | 提问 | 9 | 玛尔基特怎么打 / 今天有什么重要日程吗 / What is the capital of France |
| | 指令 | 7 | 介绍一下你自己 / 帮我设置一个十分钟的倒计时 / 播放一首轻音乐 |
| | 带称呼 | 2 | 喂，帮我查一下明天天气 / 嘿 BT，现在几点了 |
| 非面向（应 not-for-me/silence） | 自言自语 | 8 | 这关怎么这么难啊 / 唉，好累 / 我是不是忘带钥匙了 |
| | 回应旁人 | 8 | 对，我也觉得 / 行，那就这么定了 / Yeah, sure, sounds good |
| | 无信息意图感叹 | 3 | 哇，这画面真好看 / 天哪，不会吧 / 唉，算了算了 |
| | 与他人对话片段 | 5 | 你把那个拿过来 / 我们点外卖吧 / 儿子，作业写完了吗 |

### B.2 Prompt 变体（A/B 对照设计）

| 变体 | 构成 | 说明 |
|---|---|---|
| **A（基线）** | 生产 live prompt（`<character_profile>`bt-7274 + `DEFAULT_SYSTEM_PROMPT_EN` 三态 + in-character tail）+ 用户句 | 完全复刻 webinfer live 文本路径的 system prompt（memory/wiki 块为空，因 memory-store unhealthy） |
| **B1** | A + system 内嵌 [Addressee Judgment] 教学块（Not-For-Me 定义 + 判定规则 + 4 个内联 few-shot 示例） | spec §4.2.1 的实现方式：教学写在 prompt_constants |
| **B2** | A + 教学块（无内联示例）+ **chat-turn few-shot 4 对**（真实 user/assistant 轮次，assistant 输出 decision token） | few-shot 以对话轮次呈现（更贴近训练分布） |
| **B3** | A + 教学块 + **严格格式约束**（"Reply with EXACTLY ONE token, no explanations"）+ chat-turn few-shot | 检验"格式遵循"是否是瓶颈 |
| **C（补充）** | **去掉 `<character_profile>`**（无 BT-7274 人设），仅 `DEFAULT_SYSTEM_PROMPT_EN` + 教学块 + chat-turn few-shot（同 B2） | 隔离人设混杂（"User is your Pilot" 是否让模型默认"所有话都对我说"） |

### B.3 结果表（42 句 × 5 变体）

| 指标 | A（基线） | B1（内嵌 few-shot） | B2（chat-turn few-shot） | B3（严格格式） | C（无 persona） |
|---|---|---|---|---|---|
| **非面向句误响应率**（response+delegation / 24） | **100.0%**（24/24） | 70.8%（17/24） | **20.8%**（5/24） | 25.0%（6/24） | 29.2%（7/24） |
| 非面向句无回复率（not-for-me+silence） | 0.0% | 29.2% | **79.2%** | 75.0% | 70.8% |
| **not-for-me 精确率**（spec 判据 ≥80%） | — | **100%**（3/3） | **100%**（1/1） | **100%**（1/1） | 0%（0/0，未输出） |
| not-for-me 召回率 | 0.0% | 12.5%（3/24） | 4.2%（1/24） | 4.2%（1/24） | 0.0% |
| 面向句误判 not-for-me 比例 | 0/18 | 0/18 | 0/18 | 0/18 | 0/18 |
| 面向句正确响应率（response+delegation） | 100%（18/18） | 100%（18/18） | 83.3%（15/18） | 83.3%（15/18） | **94.4%**（17/18） |
| 面向句漏判（silence） | 0 | 0 | 3（t07/t11/t12） | 3（t07/t11/t18） | 1（t11） |

### B.4 混淆矩阵

```
A（基线）:           预测→    response  silence  delegation  not-for-me
  真实 面向(18)                 10        0         8          0
  真实 非面向(24)               21        0         3          0

B1:                   预测→    response  silence  delegation  not-for-me
  真实 面向(18)                 14        0         4          0
  真实 非面向(24)               16        4         1          3

B2:                   预测→    response  silence  delegation  not-for-me
  真实 面向(18)                 14        3         1          0
  真实 非面向(24)                4       18         1          1

B3:                   预测→    response  silence  delegation  not-for-me
  真实 面向(18)                 14        3         1          0
  真实 非面向(24)                5       17         1          1
```

### B.5 混淆分析（关键观察）

1. **基线 A 对"非面向"零防护**：24/24 全被判 response/delegation，误响应率 **100%**——实锤 spec §1 前提（现有三态 live prompt 没有任何 addressee 判定，AI 对自言自语/回应旁人也必回复）。
2. **B2 是行为层最优**：chat-turn few-shot 把模型整体推向"少说话"——24 句非面向里 18 句输出**空（EOS 立即结束）**判 silence（+1 句 not-for-me）= **79.2% 无回复**。即使 not-for-me token 没出来，**"不播报"这一最终目标已被大幅达成**。
3. **not-for-me token 本身召回很低（4-12.5%）**：模型更倾向用"空输出/silence"表达不回复，而不是显式 `</not-for-me>`。对纯"不播报"目标够用；对 Phase2 融合层（需要 not-for-me 语义与声学分数融合）**语义信号不足**，需靠 few-shot 数量/质量提升。
4. **精确率 100% = 宁漏不乱插方向达标**：4 个变体全部 0 次"面向句被误判 not-for-me"，spec 判据（not-for-me 精确率 ≥80%）**达标**；代价在召回侧。
5. **B2/B3 的 target 侧成本**：3/18 面向句（"今天有什么重要日程吗"、"Can you summarize the last meeting" 等清晰 AI 提问）被沉默漏判——chat-turn few-shot 过度压制了回复倾向。需要配正向 few-shot 纠偏。
6. **顽固误响应项（B2 仍错的 5/24）**：
   - 共情回应："唉，好累"→"理解，累了就休息一下"；"我真笨，这都忘了"→"没关系，我帮你查一下"（模型把感叹当倾诉对象）；
   - 歧义句："那你说怎么办"（对旁人）→ 模型甚至幻觉出玛尔基特打法内容；"我们点外卖吧"→"这个请求不涉及我的职责范围"（拒绝也是播报=误响应）；
   - **delegation 陷阱**："你去问一下老王"在全部 4 个变体中都判 delegation（模型把"去问X"当检索任务）——这是系统性模式，few-shot 需专门覆盖。
7. **格式遵循 vs 语义理解**：B1 中仅 1 句（"对，我也觉得"）出现"理解正确但 token 错"（raw 文本写明"回应别人的看法，我先不介入"却仍输出 response）——**瓶颈主要是倾向性（silence vs not-for-me 选择），不是"不懂"**。
8. **人设（BT-7274）不是混杂，反而帮忙**（变体 C 对照）：去掉 `<character_profile>` 后，非面向误响应率从 20.8% 升到 29.2%、`</not-for-me>` 输出从 1 句降到 0 句、target 漏判从 3 句降到 1 句——即**人设的"战术型助手/少废话"性格约束强化了沉默倾向**（对非面向有利），代价是 target 侧更容易过度沉默。落地时**保留人设 + 用正向 few-shot 纠 target 漏判**，比去掉人设更优。

### B.6 Parity：webinfer live 路径 vs 直连 llama-server（5 句抽查）

| 句 | webinfer decision | 直连解析（A） | 一致 |
|---|---|---|---|
| 玛尔基特怎么打 | response | response | ✅ |
| 这关怎么这么难啊 | response | response | ✅ |
| 唉，好累 | response | response | ✅ |
| 对，我也觉得 | silence | response | ⚠️ 不一致（webinfer 侧 silence） |
| 喂，帮我查一下明天天气 | delegation | delegation | ✅ |

> 4/5 一致；1 句差异（webinfer 组合 prompt 可能含 memory/wiki 块或会话态影响）。结论：**直连 llama-server 测的是模型决策能力，与生产 webinfer 路径方向一致**，作为 A/B 对照方法有效。

### B.7 结论：四态改造是否值得做 + few-shot 怎么调

1. **值得做（且数据强烈支持）**：
   - 误响应率 **100% → 20.8%**（B2 行为层），核心痛点（AI 乱插话）被大幅缓解——仅 prompt 教学、零代码改动即可获得；
   - spec 判据 **not-for-me 精确率 100% ≥ 80% 达标**，宁漏不乱插方向正确（0 次 target→not-for-me 误判）；
   - 融合层预期更优：Phase1 声学先拦异人语音，语义 not-for-me/silence 兜"目标说话人自言自语"（B2 已兜 79.2%），声学×语义融合后误响应率可再降。

2. **not-for-me token 召回不足是真问题（4-12.5%）**：对"不播报"目标可用 silence 兜底；但对 Phase2 融合需要语义 not-for-me 信号。**落地建议：消费侧把"空输出/silence"与 not-for-me 同等对待为"不播报"**（现有 parse_model_decision 空文本→silence 已覆盖），融合层语义分数可按"not-for-me ∪ silence"计，后续用更多 few-shot 提升 token 纯度。

3. **few-shot 调法结论（B1 vs B2 vs B3 vs C）**：
   - **system 内嵌 inline few-shot（B1）最弱**：教会 token 但对沉默倾向帮助有限（误响应仍 70.8%）；
   - **chat-turn few-shot（B2）行为效果最好**：误响应 20.8%；**严格格式（B3）无额外收益**（25.0%）——格式约束不是瓶颈；
   - **去人设（C）反而更差**：误响应 29.2% 且 not-for-me token 归零——人设的"少废话"约束是沉默行为的贡献者，保留人设；
   - 推荐落地组合：**保留人设 + B2 式 chat-turn few-shot + 扩到 6-8 对 + 正向纠偏**：
     a) 非面向 few-shot 扩到 4-6 对，覆盖 自言自语（"这关怎么这么难啊"→`</not-for-me>`）、回应旁人（"对，我也觉得"→`</not-for-me>`）、感叹（"唉，好累"→`</not-for-me>`）、他人对话（"你把那个拿过来"→`</not-for-me>`）；
     b) **显式区分 silence vs not-for-me**：教学块加一句"用户对我说话但无需回复 → `</silence>`；非面向（自言自语/对旁人）→ `</not-for-me>`，不得用 silence 代替"；
     c) 补 1-2 个正向例纠正过度沉默："今天有什么重要日程吗"→`</response> …`、"Can you summarize the last meeting"→`</response> …`；
     d) 专门覆盖 delegation 陷阱："你去问一下老王"→`</not-for-me>`（对旁人的指令，不是检索任务）。

4. **数据诚实声明**：42 句 × 4 变体 + 补充 C 变体（42 句）= 210 次调用，0 失败/0 超时；每句原始输出在 `.workbuddy/tmp/addressee-bench-results/results_{A,B1,B2,B3,C}.json` 可追溯；temperature=0 单次运行（llama.cpp GPU 存在轻微随机性，未做多轮平均，结论按量级解读）。



