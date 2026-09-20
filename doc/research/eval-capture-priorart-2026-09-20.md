# 决策评测先例与可捕获性侦察（本地已有 benchmark / 决策输出路径 / 延迟可测性）

> 端点：**调研 / AFK（只读）**
> 日期：2026-09-20
> 范围：为「给模型的决策能力建立可复现判据」侦察本地先例与落盘可行性
> 边界：本文只做侦察；除本报告外**未改动任何文件**
> 姊妹篇：`doc/research/eval-upstream-methodology-2026-09-20.md`（上游方法学，**不重复其内容**）
> 引用纪律：本报告**不写行号**（行号漂移快），一律用文件路径 + 关键词/函数名定位

---

## 0. 一句话结论

**本地已经有一套「能直接改造」的决策评测先例——`benchmark_4state_notforme.py` 及其已落盘数据：它有 50/51 句金标测试集、变体对照、四态决策矩阵、以及把用户偏好（「宁可漏不可乱插」）编码进源码的判据（not-for-me precision ≥ 80%）。其"测的不是生产 prompt"这一最大裂缝，已由同批端点的 `benchmark_production_live_prompt.py` 闭合——**生产四态 prompt 的真实误响应率是 30.8%，不是旧基线的 84%**。它缺的不是方法，而是三块工程件：① 只测**纯文本定向轴**（不喂画面帧、不测 `delegate`、不测时序 onset）；② 决策**没有被落盘成机器可读事件流**（只在 `qa_history` 内存态 + 非结构化 INFO 日志里出现过）；③ 没有离线回放通道（live 音频侧只有 WebRTC 麦克风入口，无 `feed_wav` 式诊断缝）。

**而"决策可捕获"这件事本身是可行的、且成本很低**：决策 token 的解析器是**纯正则 + 索引扫描的单一入口**（`parse_model_decision`），live 的每一轮决策都必经 `finish_llm_turn` 一个函数；`/v1/text/chat` 的 `frames` 协议**直接吃 base64 JPEG**（上限 6 帧），因此**喂固定视频逐秒拿决策序列完全不需要摄像头**。最大的障碍不是技术，是**没有任何一环把决策写成结构化记录**——今天要拿「每轮决策 + 时间戳」，只能去 grep `services/.logs/webui.err.log` 的 `decision=%s` 文本行。

**另一个必须正视的结论**：模型几乎不用 `not-for-me` token（生产 prompt 下 recall 仅 **11.5%**，裸 prompt 下 **0 次**）。它靠 **silence 与空输出**表达"不回应"。⇒ **评测必须同时报 token 级与行为级两个召回，且必须能区分"主动 silence"与"模型吐空"**——否则整份决策语料会被系统性误读。

---

## 1. 已有 benchmark 先例（逐个判定）

### 1.1 `services/scripts/benchmark_4state_notforme.py` —— ★ **能用，且是本项目最有价值的现成资产**

| 项 | 内容 |
|---|---|
| **测什么** | 本地 llama.cpp 四态 decision token 的**定向轴**（addressee）：给定一句 ASR 转写文本，模型输出 `silence` / `response` / `delegation` / `not-for-me` 中的哪一个。对应 spec `addressee-detection.md` §4.3「必测②」 |
| **怎么算"对"** | ground truth 是**二元的**：`directed`（应 response/delegation）vs `nondirected`（应 not-for-me/silence，即"不开口"）。由 `summarize()` 构造 `expected × decision` 混淆矩阵，再算四个比率 |
| **指标定义**（全部是**源码里显式算出来的**，不是叙事） | ① `baseline_mis_response_rate_pct` = nondirected→response / 全部 nondirected（**误响应率**，基线主指标）② `not_for_me_precision_pct` = 真 nfm / 全部预测 nfm ③ `not_for_me_recall_pct` = 真 nfm / 全部 nondirected ④ `directed_miss_rate_pct` = directed→not-for-me / 全部 directed（**漏判率**）⑤ `errors` 计数（HTTP/超时失败**单独计**，不静默丢弃） |
| **判据** | 写在模块 docstring 里、并与用户拍板一致：`not-for-me precision >= 80%`，理由明写「宁漏不乱插：false not-for-me is acceptable, missing a directed utterance is not」 |
| **用什么数据** | **脚本内嵌的测试集**（原始 50 句：25 directed / 25 nondirected；后续追加 `N01b` 后为 **51 句 / 26 nondirected**，见 §1.1.1）。7 个 category：question / command / address / self-talk / reply-other / exclamation / talk-other，中英混合。few-shot 与测试集**刻意不重叠**——测的是泛化不是记忆。**注意：2026-08-12 落盘的 JSON 是 50 例版本，在那之后 `TEST_SET` 变过。** |
| **对照设计** | 5 个变体：`A_live3_prod`（复刻生产 live 三态 prompt + persona）/ `A_live3_clean`（无 persona）/ `B_live4_prod_append`（追加四态教学，4 few-shot）/ `B2_live4_prod_append_rich`（11 few-shot）/ `C_live4_reframe`（transcript 重构 + addressee 前置 + 无 persona） |
| **调用路径** | **直连 llama-server OpenAI 兼容 `/v1/chat/completions`**（不是走 webinfer）。理由写在 docstring：直连可完全控制 system prompt、避免污染 webinfer 会话 |
| **prompt 保真度** | 通过 `sys.path` 注入 `services/webinfer` 后 `import prompt_constants.DEFAULT_SYSTEM_PROMPT_EN` + `system_prompts.compose_system_prompt/load_character_prompts` —— 即**生产三态 prompt 是"引用"而非"复制"**，这一点很关键（见下方"局限"） |
| **输出物** | `doc/research/data/benchmark_4state_notforme_results.json`（**已入 git**，含每句 raw 输出 + latency_s） |

**已落盘实测数据（我复核了 JSON，与 `addressee-cross-validation-2026-08-12.md` 附录 B-1 表一致）**：

| 变体 | 误响应率 | nfm precision | nfm recall | 漏判率 | errors |
|---|---|---|---|---|---|
| `A_live3_prod`（**当前生产三态**） | **84.0%** | 0% | 0% | 0% | 0 |
| `A_live3_clean` | 76.0% | 0% | 0% | 0% | 0 |
| `B_live4_prod_append` | 52.0% | 100% | 8.0% | 0% | 0 |
| `B2_live4_prod_append_rich` | 56.0% | 100% | 8.0% | 0% | 0 |
| `C_live4_reframe`（**最优**） | 52.0% | 100% | **36.0%** | 0% | 0 |

**能否直接改造为「决策能力回归测试」？—— 能，改造量小，但它现在测的不是"决策能力"的全部。**

可复用的骨架（**建议原样保留**）：
1. **金标 schema**：`(id, text, expected, category, note)` 四元组，`expected` 是二元轴标签。**这个形状是对的**——它天然把"定向轴"与"具体动作"解耦，避免了合成单一 accuracy 的错误（与姊妹篇 §8.2② 的建议一致）。
2. **矩阵 + 分轴比率**的产出形状，直接可做回归 diff。
3. **变体对照**机制：prompt 一改就跑全部变体，天然是 A/B 回归。
4. **判据编码在源码**：门槛值不是文档里的口号，是可断言常量。
5. **失败单独计数**：`errors` 字段 + `ok/error` per-row，不会把网络失败当成"模型判 silence"。

必须补的四块（**不改骨架，加轴**）：
1. **视觉轴完全缺失**：现有测试集是纯文本，**一条帧都不喂**。而生产 live 用户轮是 `_frames_payload(recent_frames)` 带帧的（`live_mode._handle_commit` → `_send_to_llm(frames=...)`）。⇒ 当前 benchmark 测的 prompt 与线上 prompt **不完全相同**（线上 system prompt 还会追加 `LIVE_VISUAL_OBSERVATION_SEGMENT`）。
2. **`delegate` 未被评测**：`summarize()` 把 delegation 并列在矩阵里，但**没有任何 metric 使用它**；判据只覆盖 not-for-me。「该不该委派」是一个独立决策维度。
3. **时序轴缺失**：没有 onset 延迟、没有 premature rate、没有 FPM（误触发/分钟）。上游的 timing 轴内涵（不早/不晚/该沉默时沉默）本地一条都没落。
4. **prompt 保真度曾有漂移 —— 已由同批端点补齐（见 §1.1.1）**：本脚本的 `build_live_prompt_3state` 引用的是 `DEFAULT_SYSTEM_PROMPT_EN`（**三态**），而生产 live 走的是 `_resolve_base_system_prompt` → **`LIVE_SYSTEM_PROMPT_EN`（四态）**。⇒ **1.1 的五个变体没有任何一个是生产实际发送的 prompt**，`C_live4_reframe` 的 36% recall 与线上行为**没有对应关系**。这一裂缝在本报告侦察期间被同批端点用 `benchmark_production_live_prompt.py` 闭合，**结论见下节。**

#### 1.1.1 ★ 生产 prompt 的真实数字（同批端点并发产出，2026-09-20）

同一批工作中，另一端点建了 `services/scripts/benchmark_production_live_prompt.py`：**直接 import 真的 `LIVE_SYSTEM_PROMPT_EN`（不复制、不修改）**，走 `prompt_assembly._resolve_base_system_prompt(interaction_mode="live")` 的真实路由，并**复用 1.1 的同一测试集与同一解码参数**（`from benchmark_4state_notforme import TEST_SET`，无测试集重复），产出 `doc/research/data/benchmark_production_live_prompt_results.json`。

**两个变体 + 与旧变体的并列对照**：

| 变体 | n_directed / n_nondirected | 误响应率 | nfm precision | nfm recall | 漏判率 |
|---|---|---|---|---|---|
| `A_live3_prod`（旧基线） | 25 / 25 | 84.0% | 0% | 0% | 0% |
| `C_live4_reframe`（旧最优） | 25 / 25 | 52.0% | 100% | 36.0% | 0% |
| **`P_live4_prod_prompt`（裸 `LIVE_SYSTEM_PROMPT_EN`）** | 25 / **26** | **38.5%** | **0%**（0 次预测） | **0%** | 0% |
| **`P2_live4_prod_prompt_profile`（+ character profile，= 字节级生产 prompt）** | 25 / **26** | **30.8%** | **100%**（3/3） | **11.5%** | **0%** |

**这组数字推翻了三条原本要靠推测的结论**：
1. **生产四态 prompt 比旧基线好得多**：误响应率 **84% → 30.8%**（`P2`）。旧 benchmark 的 84% 是**三态**基线，**不反映现状**。
2. **但 `not-for-me` token 几乎不被使用**：`P_live4_prod_prompt`（不带 persona）**零次**输出 `</not-for-me>`，`P2` 也只有 3 次（recall 11.5%）。⇒ §4.2 的 `nfm_recall_token` 在**当前生产 prompt 下极低**，与交叉验证 B.7.2 的判断一致：**模型用 silence 表达"不回应"，而不是用 not-for-me token**。
3. **persona 是显著的正面因素**：加上 `<character_profile>` 后误响应率 38.5% → 30.8%，且 nfm 从 0 次变成 3 次。⇒ **不要为了 "not-for-me" 去掉 BT-7274 persona**（这与交叉验证附录 B-2 结论 8 同向，且此处是在**生产 prompt** 上复现的）。
4. **测试集是 51 例不是 50**（nondirected 为 26）—— `TEST_SET` 在四态实现后新增了 `N01b`（"哎呀，这关怎么那么难"，标注为"真机失败句 08-13"）。⇒ **引用旧 JSON（50 例）与新 JSON（51 例）时必须区分**，否则比率不可直接比。

**行为级口径（用户能感知的那个）**：`P2` 的 26 个非面向例中，silence 13 + not-for-me 3 + delegation 2 = **18/26 = 69.2% 不开口**（delegation 在生产 `finish_llm_turn` 里被改写为 silence，**不播报**）。⇒ **token 级指标（11.5%）与行为级指标（69.2%）相差 6 倍**，再次印证 §4.2 必须两条都报。

⚠️ **注意这组数字仍是 `temperature=0.8` 单次采样**（与旧 benchmark 同参），且 `P_live4_prod_prompt` 的 nfm precision 分母为 0（无预测）——**同样是快照，不是稳定判据**。

其他局限：
- **temperature 0.8 单次采样**（默认值），逐句有随机性；附录 B-1 自陈两轮复跑 A_prod 是 96%/84%、C 是 32%/36% —— **方向稳定但数值是快照**。做 CI 回归必须改 `temperature=0` 或多轮多数投票。
- **precision 分母极小**（n=2 / n=2 / n=9），100% 是小样本结果，不能当稳定判据用。
- **直连 llama-server 而非 webinfer** ⇒ **测的是模型能力，不是链路行为**。附录 B-2 做过 5 句 parity 抽查，4/5 一致、1 句不一致（webinfer 侧因 memory/wiki 块或会话态不同判了 silence）。做"决策能力回归"可以用直连；做"链路回归"必须走 webinfer。

### 1.2 `services/scripts/benchmark_campplus_addressee.py` —— **能用，但测的不是决策，是声学门控**

| 项 | 内容 |
|---|---|
| **测什么** | CAM++ speaker embedding 的**工程可行性**（spec §3.3「必测①」）：延迟 / RTF / CPU 增量 / 同人-异人区分度 / 阈值行为 / `SpeakerEmbeddingManager` 端到端 search |
| **怎么算"对"** | **没有金标标签**。是**分布统计**而非分类评测：同人对 cosine 分布 vs 异人对 cosine 分布的重叠程度，再在阈值网格（0.55/0.6/0.65）上看 `same recall` 与 `diff false-acc` |
| **用什么数据** | 三个真源目录：`D:/AI/data/kws/mic_captures/*.wav`（用户实时捕获，3s 16k mono）、`D:/AI/workspace/bt-voice/ref_audio/bt_reference.wav`（BT-7274 克隆音 = 另一说话人）、`D:/AI/data/kws/esc50_neg/*.wav`（环境噪声） |
| **实测结论**（记录在 `addressee-cross-validation-2026-08-12.md` 附录 A） | 2s 段提取 **mean ~19–20ms**（spec 原假设 300–400ms，**乐观一个数量级**）；RTF 0.010；干净语音同人 mean 0.751 / 异人 mean 0.004；0.55/0.60/0.65 三档**同人 recall 全 1.00、异人 false-acc 全 0.00** |

**判定**：**不能改造为"决策能力回归测试"**（它评测的是 speaker identity，不是 addressee，更不是 silence/response/delegate 决策）。但它是**声学门控侧的现成回归资产**，且有一个**必须记录的脆弱点**：
- 它的输入集合**随时间漂移**——`mic_captures` 是实时捕获目录，脚本按 RMS 排序取前 N，附录 A.4 已自陈命中数在 3–6/10 间波动。⇒ **不合格的 CI 判据**；若要做门控回归，必须先**冻结一份注册集/测试集快照**（把 wav 复制进受 git 管理的目录）。
- 它的"环境噪声"负样本只有 8 个文件（`esc50_neg` 取前 8），统计强度弱。

### 1.3 `doc/research/data/benchmark_4state_notforme_results.json` —— **能直接用**

- 结构：`{model, test_set_size, results: {变体名: {stats: {...}, rows: [...]}}}`。
- `stats` 是 §1.1 的四个判据 + 完整混淆矩阵 + `errors`；`rows` 每句含 `id/text/expected/category/note/ok/decision/raw/clean/latency_s`。
- **`raw` 字段是完整模型原文** ⇒ 即使将来解析器改了，也能**离线重放解析**（不必重跑模型）。这是一个被低估的价值点。
- **`latency_s` 已逐句记录** ⇒ 时序数据其实已有，只是没被聚合（没有 p50/p90/分布）。
- **结论：这是目前唯一"可复现"的决策数据快照**，应作为回归基线沿用，**不要另起新格式**。

### 1.4 `doc/research/addressee-detection-2026-08-12/`（5 份）—— **不能用，作为"指标词汇表"可用**

- 性质：**云端调研交付**（作者 Alice 27 / Wind AI），5 份 = 4 份支撑（01 问题定义 / 02 声学 / 03 语义 / 04 混合）+ 1 份终稿。
- **测什么**：什么都没测——**是文献综述 + 架构推荐**，不含任何本机实测。
- **怎么算"对"**：**没有本地判据**。仅 `01_problem_definition.md` §4.3 给出**指标词汇表**（EER / AP_tss / FAR / FRR / Accuracy / Latency），并引用了两篇外部 benchmark：
  - IWSDS 2025「An LLM Benchmark for Addressee Recognition in Multi-modal Multi-party Dialogue」：GPT-4o addressee recognition 准确率 **80.9% vs 随机基线 80.6%**（关键外部锚点，说明纯文本语义判定接近随机）。
  - arXiv 2512.10257「A Benchmark for Voice Assistant Query Rejection in Smart Speakers」：定义 7 类应拒绝输入，**Type 4 = Non-assistant-directed chat (multi-person or self-talk)** 直接对应本地问题。
- **用什么数据**：无本地数据；全部是论文引用。
- **能否改造**：**不能**。它是**设计与选型依据**，不是评测资产。
- **可复用的三样东西**：① §4.3 的指标词汇（本地应直接用 FAR / FRR / EER 这套名字，而非自造）；② 「宁可漏不可乱插」的代价偏好论证（`01` §1.6 + 终稿 §5.5）；③ 声学侧根本局限的明确表述（终稿 §2.5：只能答"谁在说话"，不能答"在对谁说话"）。
- **⚠️ 已知历史漂移（姊妹篇已记录，此处只做交叉引用）**：`04_hybrid_approach.md` 写「陪伴型代理可偏向**高召回（宁可误触发，不可漏响应）**」，与用户拍板**反向**。`addressee-cross-validation-2026-08-12.md` §四已裁决「以主报告 + 用户授权为准：**宁可漏、不可乱插**」。**做评测时必须用后者，不要引用 04 的表述。**

### 1.5 `doc/specs/addressee-detection.md` —— **不能用（spec，非评测），但判据与口径必须从它取**

- **性质**：落地 spec（正式，2026-08-13 定稿，实现 commit `2b10c0c` → `ce641fc` → `80cffce`）。
- **§4.3「必测②」是唯一与评测直接相关的一节**，且**它就是 benchmark 1.1 的**需求来源**（"设计小型测试集（30-50 句）"→ 实际做了 50 句；"not-for-me 精确率 ≥80%"→ 已编码进源码）。
- 必须从它取的口径（避免后续端点混用）：
  - **`not-for-me` 的语义边界**（§4.1 表）：`</silence>` = "用户在跟我说话但无需回复"；`</not-for-me>` = "非面向 AI"。§4.2.4 记录了**实际落地决策**——消费侧把 **not-for-me ∪ silence 同计为"不播报"**（依据交叉验证 B.7.2：not-for-me token 召回低，模型爱用空输出/silence）。⇒ **评测时必须同时报 token 级指标与"最终不开口"的行为级指标**，否则会低估系统实际表现（见 §4.2）。
  - **融合层 0.4/0.6 权重 + 双阈值 T_low 0.3 / T_high 0.7 —— 已明确标注"本轮不实现"**（§4.2.5 落地决策）。⇒ 评测**不要**为融合层建指标，它不存在。
  - **回归要求**：`JARVIS_ADDRESSEE_DETECTOR_ENABLED` 默认 false，闸门关时行为须与现状完全一致（§3.4）。这是一个**现成的回归断言**。
- 同一 spec 还记录了一个**本地与云端调研冲突的实证修正**（§4 开头）：云端假设要改 GBNF grammar，**本地无 GBNF**（prompt 教学 + 自由生成 + 解析）⇒ 四态扩展不需要 grammar。评测设计不得假设 grammar 约束存在。

---

## 2. 决策输出路径与可捕获性

### 2a. 是否已有「每轮决策 + 时间戳」的落盘记录？

**部分有，但不是机器可读事件流。**逐条清点（按"可捕获性"从弱到强排）：

| # | 载体 | 在哪 | 格式 | 含 decision？ | 含时间戳？ | 持久化？ | 覆盖范围 |
|---|---|---|---|---|---|---|---|
| 1 | **`qa_history`** | webinfer `SessionState.memory_state`（`memory_io._update_text_qa_history` 写入） | Python dict：`{query_time, query, responses:[{prediction, decision}], text_path:True}` | ✅ **是**（`decision` 字段） | ✅ `query_time`（ISO，秒精度） | ❌ **内存态**，随会话过期消失 | **live 文本路径全覆盖**（`_handle_text_payload` 与 `_stream_text_payload_frames` 都写），但**窗口截断**（`qa_history_window` 默认 12 轮） |
| 2 | **`logs/events/*.jsonl`（ADR-0014）** | `services/common/event_json.py` 的 `emit_event` → `logs/events/<service>-<UTC 日>.jsonl` | JSONL，schema：`ts/level/service/event/session_id/latency_ms/status/user/extra` | ❌ **不含 decision** | ✅ `ts`（ISO-8601 UTC 毫秒） | ✅ 落盘（按天滚动，`log_maintenance.ps1` 默认 30 天） | 只有 `webinfer_request`（`latency_ms` + `extra={model, path}`）与 `infer_error`、`session_start/session_end`。**实测事件名清点：只有 session_start / webinfer_request / wiki_recall / wiki_recall_fail / circuit_breaker_* / push_memory / infer_error / probe_test / session_end** |
| 3 | **webui INFO 文本日志** | `services/.logs/webui.err.log` | 非结构化文本：`<asctime> - <logger> - LEVEL - <message>` | ✅ **是**（`decision=%s`），但**被 `%r[:120]` 截断** | ✅ 前缀 asctime（毫秒） | ✅ 落盘，但 launcher **每次重启会 truncate** | 三个 site：`[live-mode] LLM response (decision=...)`、`[tts-stream] decision=...`、`[addressee] semantic not-for-me: utterance=...`、`[live-proactive] VLM decision=...`。**当前捕获的日志里几乎没有**（需真机跑 live 才有） |
| 4 | **webui access log** | `services/logs/webui-access-<UTC>.log`（注意：在 `services/logs/`，**不是** `logs/`） | JSONL：`{ts, method, path, status, latency_ms}` | ❌ | ✅ | ✅ | 只有 HTTP 层，无决策语义 |
| 5 | **webinfer session output JSON**（`output_*/live/<session>.json`） | `session.py` 的 `_write_session_outputs` / `_flush_session_outputs` | JSON：`{sample_data, total_time, total_turns, predictions[], memory}` | ❌ **live 走不到这里** | ✅ 有 `total_time` | 落盘 **但默认关闭** | ⚠️ **关键发现**：`state.predictions.append(prediction)` **只发生在 `_chat_payload_finalize`**，即 **`/v1/chat/completions` 多模态路径**。live 走的是 `/v1/text/chat`（`_handle_text_payload` / `_stream_text_payload_frames`），**这两条路径根本不写 `predictions`** ⇒ **live 决策不进 session JSON**。此外 `--no-live-save` 默认 `not LIVE_SAVE_OUTPUTS`（即默认**不保存**） |
| 6 | **webinfer 逐轮 timing 日志** | `services/.logs/webinfer.err.log`，`_chat_payload_finalize` 里 `LOGGER.info("turn=%d timing: total=...ms pre=... vllm=...")` | 文本 | ❌ | ✅ | ✅ | ⚠️ 同样**只在多模态路径**；`streamingharness.timing` 也只在该路径挂载 |
| 7 | **webinfer→llama-server 的 `timings`** | llama-server `/v1/chat/completions` 响应体 | JSON | — | ✅ | ❌ | ⚠️ **被 webinfer 丢弃**：`_chat_completion_response` 只重建 `id/object/created/model/choices/usage/streamingharness`，openai SDK 解析成 `ChatCompletion` 对象后未知字段（含 llama 的 `timings`）不保留 |

**结论（a）**：
- **live 的"每轮决策 + 时间戳"确实存在，但只活在两个瞬时态里**：webinfer 进程内的 `qa_history`（会被窗口截断 + 会话过期清零），以及 webui 的非结构化 INFO 日志（可 grep，但字段被截断、无 schema）。
- **`logs/events/*.jsonl` 这个已经建好的机器可读通道，从来没有记录过决策**。这是最值得利用的既存基础设施。
- **历史上不存在可信的"决策事件流"语料**——想复盘只能 grep `services/.logs/webui.err.log`，而该文件在每次 launcher 重启时被 truncate，且捕获的 08-17 日志里 live 决策行几乎为零。

### 2b. 决策 token 的解析器在哪？正则还是状态机？解析失败如何表现？

**解析器有三层，全部是纯函数，无状态机。**

| 层 | 位置 | 实现 | 职责 |
|---|---|---|---|
| **① 权威解析器（单一入口）** | `services/webinfer/response_format.py` → **`parse_model_decision(raw_text)`** | **纯正则 + `str.find` 索引扫描**。优先级链：**① delegation 标签（闭合 `</delegation>` 与开放 `<delegation>`，出现即胜）→ ② not-for-me 标签（ANYWHERE 胜，含开放形式）→ ③ `</response>` / `</silence>` 中**最早**者 → ④ 无任何标记 ⇒ 默认 `response`** | 返回 `(decision, clean_text, delegation_question)`，`decision ∈ {silence, response, delegation, not-for-me}`，**永不返回 None**。兼容别名 `_parse_decision_tokens` |
| **② 归一化（另一条独立路径）** | 同文件 `normalize_model_output(text)` | **独立重实现**（不是调 ①）：先查 not-for-me，再在 response/silence 中取最早，无标记则 `</response> {raw}` | 把原文压成"单个 decision token + body"。**与 ① 的优先级不同**（① delegation 最优先，② 不看 delegation）——历史上是两条并行视图 |
| **③ 流式帧协议** | `services/webinfer/stream_protocol.py` → `build_stream_frames(deltas)` | **有状态**（这是唯一接近"状态机"的地方）：累积 delta，用 `_STREAM_DECISION_MARKER_RE` 探测**跨 delta 被切开的标记**，首次命中即 commit 一个 `decision` 帧；此后若在 response 的正文里又出现决策标记，**重新判定整轮**（`corrected: True`），丢弃已缓冲内容 | 产出 NDJSON 帧：`{type:"decision"}` / `{type:"content", token}` / `{type:"done"}` / `{type:"error"}` |
| ④ 剥离器 | `response_format.strip_decision_tokens` | `_DECISION_TOKEN_RE`（大小写不敏感，容忍内部空白，8 个变体） | **只作用于 user-facing `content`**；`decision` 字段必须由 ① 在 **raw_text** 上算（这是 `决策/交互模式与决策token规范.md` D-2026-08-03-002 冻结的契约，不得破坏） |

**解析失败的表现（这是评测必须覆盖的失败面）**：
1. **空输出 → `silence`**（显式 fail-safe，`parse_model_decision` 开头 `if not text: return "silence"`）。⇒ **产物上无法区分"模型主动静默"与"模型吐空/被截断"**。这是最重要的观测盲区（交叉验证 B.5.2 已实证：B2 变体 24 句非面向里有 18 句是**空输出**判 silence）。
2. **无标记输出 → `response`**（默认放行）。⇒ 模型跑偏不输出 token 时，系统会**当成正常回复并播报**。这是 fail-open，方向与「宁漏不乱插」**相反**。
3. **流式提前结束且无完整标记** → `build_stream_frames` 与 `_stream_text_payload_frames` 都 fail-open 走 ①（同样落到 `response`）。
4. **`max_tokens` 截断**：`finish_reason` **没有被用于决策判定**（`_chat_completion_response` 硬编码 `"finish_reason": "stop"`）⇒ **截断不会被识别**。
5. **大小写/空白变体**：① 用 `str.find` **区分大小写**（只找字面小写 `</response>` 等），③的 marker 正则**不区分大小写**，④的正则**不区分大小写**。⇒ 模型吐 `</Silence>` 时，③会 commit 一个 decision 帧，但帧内的 `decision` 值来自 ①，**会落到 "response" 默认**。**这是一处真实的解析不一致**（值得在评测里加一个畸形输出用例把它钉住）。
6. **配置项 `normalize_output`** 存在（默认 True），说明归一化是可关的——评测时须固定，否则基线不可比。

**⭐ 对评测最重要的结论**：`parse_model_decision` 是**决定"模型到底判了什么"的唯一权威**，且是**纯函数**。任何决策事件流都**必须记录它的输入（raw_text）或输出**，而不是记录日志里的 `decision=%s`（后者经过 delegation→silence 的消费侧改写）。

### 2c. live 的 video 帧决策循环

**两个循环，性质完全不同，不要混。**

#### 循环 A：主动搭话（proactive，真正的"每秒看一帧"候选）

- **入口**：`services/webui/src/joy_interaction_webui/live_proactive.py` → `proactive_loop(...)`
- **节奏**：`await asyncio.sleep(interval_s)`，`interval_s` = **`LIVE_PROACTIVE_INTERVAL_S`（默认 5.0 秒）**——**不是 1 秒**。
- **门控**：默认 **`LIVE_PROACTIVE_ENABLED=false`（关闭）**。运行中可经 `POST /api/live/proactive` 热切换（`proactive_switch_action` 判定 reject/start/cancel）。
- **每轮行为**：仅当 `turn_state() == TurnState.LISTENING` 且 `recent_frames` 非空时，取**最新 1 帧**（`frames_payload([recent_frames[-1]])`）→ `send_proactive_prompt` → `call_proactive_vlm`（**非流式** `httpx` POST，`max_tokens=256`、`temperature=0.7`、`messages` 里 user content 为**空串** + `interaction_mode="live"` + `frames`）。
- **消费决策**：`decision, response = await call_proactive_vlm(frames)`；`decision != "response"` 或 response 为空 → **保持沉默**（`[live-proactive] staying quiet (decision=%s)`）；`== response` 且此刻仍 LISTENING（有 race guard）→ 才 `spawn_sentence_tts` 播报。
- **失败模式**：**全部 fail-open**，任何异常 log 后返回 `("silence", "")`，一轮静默跳过。
- ⚠️ **proactive 轮不进 `qa_history`**（`_update_text_qa_history` 需要非空 user text）——测试 `test_live_visual_proactive_empty_text_no_qa_history_pollution` 专门钉住这一点。⇒ **proactive 的决策在任何现有载体里都不留痕**（除了 §2a 表第 3 行的日志）。

#### 循环 B：video 帧处理（`VideoProcessorTrack`，1 fps）

- **位置**：`services/webui/src/joy_interaction_webui/video_processor.py` → `VideoProcessorTrack.recv()`
- **节奏**：类变量 **`process_interval_seconds = 1.0`**；`frames_per_batch = 1`（>1 时走子采样批处理分支，`sub_interval = interval / batch`）。两者都可经 WS 消息 `update_process_interval` / `update_frames_per_batch` 运行时改（`ws_handler.py`）。
- ⚠️ **它不产出决策 token**：它调用 `VLMService.process_frame` → `analyze_image` → webinfer `/v1/chat/completions`（多模态），拿回来的是**自由文本**（`vlm_service._extract_response_text`），经 `text_callback` → `broadcast_text_update` 推到前端。**它不解析 decision token**（grep 全仓 `webui` 下无 `parse_model_decision` / `streamingharness` 消费于该路径）。
- ⇒ **它是"画面解说"通道，不是"四态决策"通道。**「每秒一帧 → 四态决策」在当前代码里**并不存在**：真正的四态决策只发生在**用户说话提交后**（`_handle_commit`）与 **proactive 轮**（默认 5s 且默认关）。
- **对评测的直接影响**：若要建"每秒一次决策"的评测，**不能挂 `VideoProcessorTrack`**（无 token），要么把 proactive 间隔改成 1s（`LIVE_PROACTIVE_INTERVAL_S=1` + 开闸），要么在离线回放器里自己造 1 Hz 节奏（见 §3）。
- `VideoProcessorTrack` 还依赖 `aiortc.VideoStreamTrack` 且**只在测试之外无任何构造点**（grep 全仓无 `VideoProcessorTrack(` 实例化）——它当前是**死代码/待接线**状态。`RTSPVideoTrack` 同理，且 `/api/rtsp/*` 三个路由是 **501 Not Implemented 桩**。

#### live 帧缓冲与 wire 格式

- `services/webui/src/joy_interaction_webui/live_frames.py`：`deque(maxlen=LIVE_FRAME_WINDOW)`（**默认 6**，`frame_window_from_env` 解析，非法值回退 6）；`append_frame` 存 `(image_b64, ts_ms)` 并打 `[live-mode] frame captured (n=.., window=..s)`；`frames_payload` 转 wire。
- **wire 契约**（webinfer 侧 `frame_parsing._parse_live_frames`）：`frames: [{image_b64: str(非空 base64), ts_ms: number}]`，**上限 `_LIVE_FRAMES_MAX = 6`**，非法输入**显式 400**（约法三章，不静默）。`image_b64` 走 `io_utils.normalize_image_b64`，**容忍完整 data URI 前缀**（会剥掉，下游 builder 只补一次 `data:image/jpeg;base64,`）。
- **帧不进历史**：`prompt_assembly._build_live_visual_messages` 只在**最后一个 user turn** 挂图，`history_messages` 保持纯文本。⇒ **上下文不随帧累积**，这是评测可重复性的有利条件（同样的帧序列总产生同样的 prompt）。
- 前端帧源契约（`static/screen_capture.js`）：WS 消息 `{type:'frame', format:'jpeg', width, height, data(base64), timestamp, source:'screen', frame_seq}`，1 fps（`1000/fps`），`frame_seq` 单调自增。

### 2d. 最小侵入点在哪（回答"若要新增一个决策事件流"）

**先明确：ADR-0014 的 `emit_event` 已经在 webinfer 里可用**（`services/common/event_json.py`，`infer_loop` 与 `session` 都已 import，且有 ImportError → no-op 兜底）。`logs/events/` 目录已存在、已有 webinfer 日文件。**所以"新增一个决策事件"不是从零搭，而是给既有通道加一个 event name。**

**首选挂点 —— `finish_llm_turn`（webui 侧，用户轮全覆盖）**

- 文件：`services/webui/src/joy_interaction_webui/live_llm.py` → **`finish_llm_turn(...)`**
- 为什么是它：
  1. **live 用户轮的每一条决策都必经此函数**，无论流式还是 fail-open 非流式重试（`send_to_llm` → `on_finish_turn`；`send_to_llm_non_streaming` → `on_finish_turn`）。
  2. 它**在签名里已经拿到了全部所需字段**：`text`（用户话语）、`response`、**`decision`**、`delegation_question`、`reply_epoch`。**零额外参数传递**。
  3. 它是 `LiveStateMachine._finish_llm_turn` 的纯委托实现，测试已覆盖（`test_live_mode.test_not_for_me_decision_no_broadcast_back_to_listening` 等）。
- **必须注意的顺序陷阱**：该函数在入口处 `logger.info("LLM response (decision=%s)")` 打的是**原始决策**，但随后**把 `decision == "delegation"` 改写为 `"silence"`**（委派给 BackgroundModelService 后不播报）。⇒ 记录点**必须在改写之前**，否则 `delegation` 会被永久丢失、与 `silence` 混淆。
- 建议 event：`emit_event("webui", "live_decision", extra={...})`。注意 **webui 当前没有任何 `emit_event` 调用**，这会是第一处——好处是顺手把 webui 接入 ADR-0014 通道；如果你希望**零新依赖引入**，替代方案是挂 webinfer 侧。

**次选挂点 —— webinfer 侧的两处 done/response 收口点（更"权威"，但要挂两处）**

- 流式：`services/webinfer/infer_loop.py` → **`_stream_text_payload_frames`** 里构造 `done_frame` 的地方。此处 `decision` / `delegation_question` / `full_text` / `usage` / `memory_chars` / `qa_history_len` / `prompt_chars` **全部就绪**，且 `session_id` 在手。
- 非流式（proactive + live visual）：同文件 **`_handle_text_payload`** 里构造 `_chat_completion_response` 的地方。
- 优点：`emit_event` 已经在同文件 import；决策是**解析器原始输出**，未经消费侧改写；`session_id` 天然可用；能顺手把 `prompt_chars` / `memory_chars`（观测 prompt 漂移）一起落。
- 缺点：**两处**（流式/非流式各一），未来新增路径可能漏挂。⇒ 若要长期稳，可在 `parse_model_decision` 之外加一个**每轮唯一的收口**（见下方"设计注意"）。

**不推荐的挂点**：
- ❌ `parse_model_decision` 本身（`response_format.py`）：它是**纯函数且每轮被调用多次**（流式会对累积前缀反复解析、done 帧再解析一次），挂这里会**重复发事件**。
- ❌ `build_stream_frames`（`stream_protocol.py`）：纯函数、无 session 上下文；且**生产流式路径实际没有调它**（`_stream_text_payload_frames` 内联了等价逻辑，`build_stream_frames` 只被测试与 re-export 使用 —— 见 `infer_loop` 里 `# noqa: F401 (re-export: tests import from infer_loop)`）。
- ❌ 只挂 `logs`：可 grep 但不可靠（截断 + launcher truncate）。

**记录什么（建议字段，兼顾 ADR-0014 PII 红线）**：

| 字段 | 来源 | 说明 |
|---|---|---|
| `ts` / `level` / `service="webui"` / `event="live_decision"` | 必填 | — |
| `session_id` | 调用方 | — |
| `extra.round_kind` | `"user"` / `"proactive"` | **两种轮次必须可区分**（proactive 现在完全不留痕） |
| `extra.decision` | `finish_llm_turn` 入参**原值** | silence / response / delegation / not-for-me |
| `extra.raw_text_len` / `raw_text_sha256` | 解析器输入 | **PII 红线：不记正文**（ADR-0014 明令）。哈希 + 长度足以做去重与漂移检测 |
| `extra.response_chars` | `response` | 长度即可 |
| `extra.frames_n` | 该轮 `frames` 长度 | 0 / 1..6，用于区分"带帧"与"纯文本"轮 |
| `extra.reply_epoch` | 已有 | 与 barge-in/打断对齐（前端 `llm_reply` 也带此字段） |
| `extra.interaction_mode` | `"live"` | 与 jarvis 轮区分 |
| `extra.is_suppressed` / `radio_silence` | webinfer `streamingharness.silence` | **静默态的轮次不是模型决策**，评测必须能剔除（否则会把"无线电静默压制"算成模型判 silence） |
| `extra.elapsed_ms` | 调用方计时 | 端到端轮延迟 |

**设计注意（三条，都是真实陷阱）**：
1. **"不开口"必须按 not-for-me ∪ silence ∪ radio-silence-suppressed 三态分别记**，不能合并——因为 spec §4.2.5 的落地决策就是"并集算不播报"，而评测需要知道**并集里各占多少**（token 召回 vs 行为召回是两个指标）。
2. **空输出与主动 silence 不可区分**（§2b 失败面 ①）。⇒ 事件里**必须带 `raw_text_len`**：`raw_text_len == 0` 即"模型吐空"，这是最常见的"假 silence"（交叉验证 B.5.2 实证 18/24）。少了这个字段，整份决策语料会被系统性误读。
3. **`finish_reason` 当前被硬编码为 `"stop"`**（`_chat_completion_response`），无法识别 `max_tokens` 截断。若要评测截断率，需要在 webinfer 侧额外记录真实 `finish_reason`（`_stream_text_payload_frames` / `_handle_text_payload` 拿得到 openai 响应对象的 `choices[0].finish_reason`）。

### 2e. 已有可观测性盘点（不要重复造）

- **`logs/events/*.jsonl`（ADR-0014）**：见 §2a 表第 2 行。**schema 已在 `doc/specs/log-event-schema.md` 冻结**（Python 是 `doc/specs/log-event-schema.md`，其中状态标为"待实现"，但**emitter 实际已实现并在跑**——spec 状态落后于实现）。PII 红线（不记 body、不记 key、不记文件内容、不记 IP）**必须遵守**。`scripts/log_query.py` **不存在**（spec 里是 TBD）——grep/`node -e` 是当前唯一查询手段。
- **`frame_seq` 测量环（ARCHITECTURE.md §8 / issue #43，PR #93）**：前端 `screen_capture.js` 自增 `frameSeq` 并写入 `window.__screenSentAt[frameSeq]`，后端 `ws_handler` 把 `data.get('frame_seq')` 回传，前端 `ws_dispatcher.js` 计算 **send→render 跨度**。**注意它测的是采集/编码/传输/渲染，不是决策**。`ARCHITECTURE.md` §8 明确：瓶颈在采集/编码链路，VLM 推理段稳态 <320ms 非瓶颈。
- **`logs/vlm-runtime-props.json` + `logs/vlm-probes/<ts>.json`**（`scripts/vlm_runtime_probe.py`）：llama-server `/props` 快照（含 `n_ctx`），drift_gate runtime 阶段消费；有 300s 新鲜度保护，缺失时自动刷新。
- **`logs/fa-probe-llama.log`**：一次 FA/CLIP 探针的完整 llama-server 启动 + 一次推理，**是本地唯一含 `print_timing` 的日志**（见 §3）。
- **drift_gate（`scripts/drift_gate.py` + `config/drift-contract.json`）**：契约式一致性检查器（10 条 check），`phase: static|runtime`，`severity: block|warn`，`mode open|closed`，退出码 0/1/2/3。**是现成的"回归门禁"骨架**——若决策回归要进 CI/门禁，最省的做法是**复用这套契约 + 退出码语义**，而不是另写一个 gate。当前 10 条 check **全部是配置/端口/常量类型**，无一条涉及决策或模型行为。

---

## 3. 离线回放可行性判断

**结论：可行，且比预期容易——但要分清"回放什么"。**

### 3.1 视频/画面回放：**完全可行，不需要摄像头**

**为什么可行**：webinfer 的 `/v1/text/chat` 在 `interaction_mode="live"` 下接受 `frames: [{image_b64, ts_ms}]`（最多 6 帧），`image_b64` 是**任意 base64 JPEG**（`_parse_live_frames` 只校验"非空 + 可 base64 解码"，不做真实性校验），且**帧不进历史**（prompt 完全由"本轮帧 + 文本 + 前三态/四态 prompt"决定）。⇒ **把一段固定视频按 1 fps 抽帧、逐帧 POST，就能拿到确定性可复现的决策序列。**

- **抽帧工具现成**：`/d/AI/envs/joyai-main/python.exe` 里 **`av` 16.1.0** 可用（`av.open(path)` + `frame.to_image()`），`PIL` / `httpx` 也在。仓库里没有演示视频（`es` 命中的 mp4 全是 Chrome 扩展自带的无关文件），**需要自备一段短视频**（或先用 `/d/AI/frames/test.jpg` 做单帧冒烟）。
- **不需要 webui / live_mode**：直接 POST webinfer，或直连 llama-server（benchmark 1.1 的做法）。
- **两条路径的取舍**：
  - **直连 llama-server**：最可控（system prompt 全权在手），但**会漏掉 webinfer 的 prompt 组装**（`LIVE_SYSTEM_PROMPT_EN` + memory 块 + `LIVE_VISUAL_OBSERVATION_SEGMENT`），测的是"模型"不是"系统"。
  - **走 webinfer `/v1/text/chat`**：拿到的是**线上真实 prompt 与真实解析器**，且响应里带 `streamingharness.decision`（非流式）或 NDJSON `decision` 帧（`stream=True`）。**推荐这条**——它同时覆盖"prompt 漂移"与"解析器漂移"。
- **1 Hz 节奏由回放器制造**：`/v1/text/chat` 是**请求-响应**语义，没有"每秒自动跑一次"的服务端循环。所以离线回放器的形态是：**循环 N 帧 → 每帧一次 POST → 收 `decision`**，把 `(t_seconds, decision, raw_text, latency)` 写成事件流。
  - ⚠️ 若想复刻**生产 proactive 的真实行为**（默认 5s 间隔 + 只在 LISTENING 时跑），应显式把 `LIVE_PROACTIVE_INTERVAL_S` 设成 1 并在回放器里模拟"LISTENING"门控，或**直接接受"回放节奏 ≠ 生产节奏"并把它写进结论**。

### 3.2 音频/对话回放：**部分可行，缺一个诊断缝**

- **live 没有 `feed_wav` 式的音频注入端点**。`/api/jarvis/feed_wav` 存在（`jarvis_routes.jarvis_feed_wav` → `feed_wav_to_session`，限 16kHz mono int16 WAV，`chunk_frames` 分块 + `sleep_s` 节流 + `max_duration_s` 兜底），但**它喂的是 Jarvis 会话，不是 live**；live 的音频入口只有 WebRTC 麦克风（`/offer` + `jarvis_audio:true` → `bind_live_audio_for_peer`）。
- **绕过端点的办法**：`LiveStateMachine.feed_audio(pcm)` 是**普通 async 方法**，`services/webui/tests/test_live_mode.py` 已经用 `FakeVAD` / `FakeASR` + monkeypatch `StreamingTurnConsumer` 的方式在**进程内驱动**它（含"语音起 → ASR partial → 2s 停滞后 commit → LLM"的完整回合）。⇒ **进程内回放的骨架已经存在于测试里**，只是用的是假 VAD/ASR。接真模型即可变成真回放。
  - 音频素材现成：`D:/AI/data/kws/mic_captures/*.wav`（真人语音）、`/d/AI/workspace/bt-voice/ref_audio/bt_reference.wav`。
- **推荐的离线回放分层**（从便宜到贵）：
  - **Tier 0（无模型，秒级）**：拿 `benchmark_4state_notforme_results.json` 里已有的 `raw` 字段**离线重放解析器**。任何解析器改动都能立刻验证"会不会改变历史决策"。**零成本、必做。**
  - **Tier 1（纯文本 + 帧，无需音频/摄像头）**：抽帧 → 逐帧/逐句 POST webinfer `/v1/text/chat`，固定金标文本，拿决策序列。**这是最高性价比的一层**（它把 1.1 的 benchmark 从"直连模型"升级为"走生产链路 + 带画面"）。
  - **Tier 2（进程内全链路）**：`LiveStateMachine` + 真 VAD/ASR + 预录 WAV + 预抽帧，`feed_audio` / `handle_frame` 驱动，LLM 端点指向真 webinfer 或 stub。测的是**含门控、含 barge-in、含 addressee 声学门控**的端到端决策。
  - **Tier 3（真机）**：不可复现，仅做定性佐证。

### 3.3 延迟/性能回放：见 §4.3（可测，但主要靠日志，不需要回放）

### 3.4 离线回放的**已知障碍**

1. **proactive 默认关闭**（`LIVE_PROACTIVE_ENABLED=false`）⇒ 忘了设 env 会得到"全程无主动决策"的空结果，且**失败模式是静默的**（`proactive_switch_action` 返回 `reject`，只有一条 log）。
2. **`force_silence_before_query` 默认 `True`**：multi-modal 路径在"无 user query"时会**短路推理**直接返回 silence（`chat_payload._is_forced_silence`，live-only）。proactive 轮之所以能推理，是因为它走 `/v1/text/chat` 的 live visual 分支而非多模态路径。⇒ **用错端点会得到"永远 silence"**。
3. **无线电静默态**：若 live 会话处于 suppressed，`_is_suppressed` 会**完全跳过推理**并返回 silence decision。回放前必须确认状态（`GET /api/live/silence` 或 `GET /api/live/status`）。
4. **`temperature=0.7`（live 生产路径）** ⇒ **同一输入两次结果可能不同**。`StreamingTurnConsumer` 构造里 `temperature=0.7` 是硬编码的（`live_llm.send_to_llm` 传 `temperature=0.7`）。⇒ **要可复现必须走直连端点自定参数，或显式记录"这是随机采样的一次样本"并做多轮统计。**
5. **会话态污染**：`qa_history`（窗口 12）+ memory 块 + Local Wiki 召回会随轮次变化，同一帧序列在不同会话历史下**可能得到不同决策**。⇒ 回放必须**固定 session_id + 每轮快照历史**（或干脆每轮新建会话）。
6. **launcher 会 truncate 日志**：`Start-Background` 在每次启动时 `Remove-Item` 掉 `.log` / `.err.log`。⇒ **回放必须在 launcher 之外自己落盘结果**，不能依赖服务日志留存。

---

## 4. 建议的最小评测实现路径

> 原则：**不新建骨架，复用 1.1 的金标 + 判据；不新建通道，复用 ADR-0014 的 `emit_event`；不新建门禁，复用 drift_gate 的契约/退出码语义。**

### 4.1 第一步（最小、可当天完成）：把决策变成事件流

**挂在哪**：`services/webui/src/joy_interaction_webui/live_llm.py` → `finish_llm_turn`（用户轮）+ `services/webui/src/joy_interaction_webui/live_proactive.py` → `send_proactive_prompt`（proactive 轮）。
**怎么记**：`emit_event("webui", "live_decision", extra={...})`，字段见 §2d 表；**`decision` 必须在 delegation→silence 改写之前取值**；`extra.round_kind` 区分 user/proactive；`extra.raw_text_len` 用来识别"空输出假 silence"。
**为什么先做这个**：**没有这一步，后面所有指标都只能靠 grep 文本日志**。它是唯一的阻塞项，且改动面是**两处函数、零参数传递**。

（若偏好 webinfer 侧：改为挂 `_stream_text_payload_frames` 的 `done_frame` 构造点 + `_handle_text_payload` 的 `_chat_completion_response` 构造点，`emit_event` 已 import，`session_id` 在手。）

**顺带补齐的三个字段**（同一处改动）：真实 `finish_reason`、`frames_n`、`elapsed_ms`。

### 4.2 第二步：把指标定义成两条正交轴（**不要合成单一 accuracy**）

沿用 1.1 的矩阵形状，但**显式分轴**。这条与姊妹篇 §8.2② 的结论一致，此处给出**本地可落地的具体形式**：

**轴 A —— 定向轴（本地独有，线上已有先例）**

> **基线取哪个？—— 取 `P2_live4_prod_prompt_profile`（字节级生产 prompt，51 例），不要取旧的 `A_live3_prod`。** 见 §1.1.1。

| 指标 | 定义 | 生产 prompt 基线（P2） | 旧三态基线（A） |
|---|---|---|---|
| `mis_response_rate` | 非面向轮被判 response\|delegation / 全部非面向轮 | **0.308** | 0.84 |
| `nfm_precision` | 真 nfm / 全部预测 nfm | 1.00（**n=3，小样本，不可当稳定判据**） | 0 |
| `nfm_recall_token` | `decision == "not-for-me"` 的真 nfm / 全部非面向 | **0.115**（裸 prompt 为 **0.0**） | 0 |
| `nfm_recall_behavior` | **"最终不开口"** 的真 nfm / 全部非面向（**not-for-me ∪ silence ∪ delegation**） | **0.692**（18/26）★**这才是用户能感知的指标** | 0.08 |
| `directed_miss_rate` | 面向轮被判 not-for-me / 全部面向轮 | **0.00**（跨两批共 100+ 句次复现，最可靠的定性结论） | 0.00 |

**⚠️ 两个分母**：旧 JSON 是 25/25（50 例），新 JSON 是 25/**26**（51 例，新增 `N01b`）。**跨 JSON 比比率前必须先对齐测试集版本。**

**轴 B —— 时序轴（本地完全缺失，须新建）**

| 指标 | 定义 | 数据来源 |
|---|---|---|
| `onset_latency_ms` 中位/p90 | 金标时刻 → 实际开口时刻 | 回放器时间戳（需金标 `time` 字段，可借上游 schema：`question[].time` / `response[].time`，见姊妹篇 §4.2） |
| `premature_rate` | 早于金标的开口占比 | 同上 |
| `FPM` | False Positive per Minute（**用户体感数**：每分钟乱插话次数） | 事件流的 `ts` 直接算 |
| `silence_correctness` | "该沉默时沉默"的占比 | 需金标标出"无话可说"的时段 |

**为什么必须分轴**：上游把 timing 与 quality 等权人评（无代价不对称），而本地偏好是**强不对称**（误响应代价 >> 漏判）。**只有分轴才能表达这个不对称**；合成 accuracy 会把"乱插话"和"该说说没说话"摊平成同一个错误，直接违背用户拍板。

**健康度闸门（防退化，直接可用）**：
- `response_rate` 上限（防止变成"话痨"）；
- `always-respond` / `always-silence` 检测——**这两条在本仓库有实证必要性**：交叉验证 B.5.1 实测 B2/B3 变体出现"面向句漏判 3/18"（沉默过度），而基线 A 是 100% 误响应（发言过度）。**两个方向的退化都真实发生过。**

### 4.3 第三步：延迟指标——**不用回放，直接解析既有日志**（性价比最高）

- **既有资产**：`logs/fa-probe-llama.log` 里已有 llama-server 的原生 timing 行（verbosity=3，**默认值，无需额外 flag**）：
  - `slot print_timing: id 0 | task 0 | prompt eval time = 453.64 ms / 448 tokens (1.01 ms per token, 987.56 tokens per second)`
  - `slot print_timing: ... eval time = 520.00 ms / 40 tokens (13.00 ms per token, 76.92 tokens per second)`
  - `slot print_timing: ... total time = 973.65 ms / 488 tokens`
  - `slot print_timing: ... graphs reused = 39`
  - 与 `ARCHITECTURE.md` §8「VLM 推理段实测复现（768×576 图 = 448 prompt token；prompt eval 453.64ms；eval 76.92 tok/s）」逐字对应。
- **⚠️ 一处必须诚实记录的更正**：`doc/research/vlm-lightweight-2026-09.md` §1.6/§2 断言 llama.cpp 日志会打印 **`image slice encoded in N ms`** 与 `image decoded in N ms`。**本次在本机全部已捕获日志中检索，零命中**（`logs/fa-probe-llama.log` 只有 `prompt eval time` / `eval time` / `total time` / `graphs reused`；`services/.logs/llama-main.err.log` 只有启动行）。⇒ **该行存在性未经本地证实**，可能依赖更高 verbosity 或不同构建。**不要把"图像编码耗时"作为已有可采指标**；可采的是 `prompt eval time`（含视觉 token 的 prefill）与 `eval time`（decode）。
- **有无现成解析器？——没有。** 全仓检索 `prompt eval time` / `image slice encoded` / `print_timing`：命中的全部是**文档**与那份日志本身，**零个解析脚本**。
- **落点难度分层**：
  - **零改动（推荐起步）**：解析 `services/.logs/llama-main.err.log` 的 `print_timing` 行，得到 `(prompt_eval_ms, prompt_tokens, eval_ms, eval_tokens, total_ms, graphs_reused)` 时间序列。**一次运行即可把 §1.6 表里的 [推断] 换成 [实测]。**
  - **⚠️ 两个坑**：① launcher 每次启动**truncate** 该文件（`Start-Background` 的 `Remove-Item`）⇒ 采集器必须在服务运行期间读取或先复制；② llama 的 timing 行**没有请求 ID / session 关联**（只有 `id 0 | task 0` 的 slot 序号），**无法自动对应到具体决策轮**。⇒ 要"每轮决策 ↔ 该轮 llama 耗时"对齐，必须走下面那条。
  - **低改动（要轮级对齐时用）**：webinfer 已在多模态路径把 `adapter_timing`（`pre_inference_ms` / `prompt_build_ms` / `vllm_inference_ms` / `post_process_ms` / `adapter_total_ms`）挂进 `streamingharness.timing` 并打 INFO 日志（`_chat_payload_finalize`）。**但 live 文本路径（`/v1/text/chat`，含 streaming）完全没有这个**。⇒ 把它补到 §2d 的同一个挂点上，即可获得**轮级延迟且与 decision 同事件关联**。
  - **可选**：llama-server 有 `/metrics`（Prometheus）与 `/slots` 端点，但当前启动参数**未传 `--metrics`**，且 `/slots` 是轮询式、与请求无绑定；**不作为首选**。
- **混合结论**：**延迟与决策可以共用一个事件流**——在同一事件里放 `decision` + `elapsed_ms` + `vllm_inference_ms`，即可直接算"每轮决策的端到端延迟分布"，而不用去 join 两个日志源。

### 4.4 怎么断言（可执行的回归判据）

沿用 1.1 的"判据编码在源码"做法，建议的三档断言：

```text
① 硬断言（block 级，二值）
   - directed_miss_rate <= 0.10     # 面向句不得被误判 not-for-me（当前 0.00，留余量）
   - nfm_precision  >= 0.80         # spec §4.3 原始判据，原样保留
   - always-respond 检测：mis_response_rate < 1.0 且 nfm_recall_behavior > 0

② 软断言（warn 级，回归 diff）
   - nfm_recall_behavior >= baseline - 0.05   # 不超过 5 个点的退化
   - response_rate 在 [baseline-0.1, baseline+0.1]
   - FPM <= 阈值（按场景配）

③ 健康度（必须存在，防静默失败）
   - errors == 0（沿用 1.1 的 errors 计数，不允许网络失败被算成模型决策）
   - raw_text_len == 0 的轮次占比单独报（"空输出假 silence"率）
```

**挂在哪跑**：复用 `scripts/drift_gate.py` + `config/drift-contract.json` 的契约形状与退出码语义（`phase` / `severity` / `mode open|closed` → 0/1/2/3），把决策断言作为一种新的 check 类型接入。**这是本仓库唯一的门禁先例，直接复用可以省一个 gate 的实现与一套文档。**

### 4.5 明确**不要**做的（避免重复劳动与方向错误）

- ❌ **不要**另起一份新的金标测试集去替代 `benchmark_4state_notforme.py` 的测试集——**而是在它上面加轴**（加帧、加 `delegate`、加 `time` 字段、把 temperature 固定）。也不要再建第三个 benchmark 脚本：生产 prompt 那一版（`benchmark_production_live_prompt.py`）已存在，**下一步应该是在这两份之上加轴，而不是复制第三份**。
- ❌ **不要**把轴 A 与轴 B 合成单一 accuracy（理由见 §4.2）。
- ❌ **不要**沿用 `04_hybrid_approach.md` 的"高召回"口径（已被 `addressee-cross-validation-2026-08-12.md` §四裁决弃用）。
- ❌ **不要**为融合层（`S_fusion = 0.4×声学 + 0.6×语义` / 双阈值）建指标——spec §4.2.5 明确"本轮不实现"。
- ❌ **不要**假设 GBNF grammar 存在（本地无 grammar，§1.5）。
- ❌ **不要**把 `VideoProcessorTrack` 当成决策通道（它不产 token，§2c）。
- ❌ **不要**把 `frame_seq` 环当决策延迟用（它测采集/编码/渲染，§2e）。
- ❌ **不要**用只有 5 个样本的 `addressee-cross-validation` B-2 变体数据当稳定基线（`.workbuddy/` 是 gitignored 的临时目录，**不在版本控制内**——只有 `doc/research/data/benchmark_4state_notforme_results.json` 是入 git 的）。

---

## 5. 证据清单（文件路径 + 关键词，无行号）

### 5.1 评测先例

| 文件 | 关键词 |
|---|---|
| `services/scripts/benchmark_4state_notforme.py` | `parse_decision_4state` / `summarize` / `baseline_mis_response_rate_pct` / `not_for_me_precision_pct` / `directed_miss_rate_pct` / `TEST_SET` / `FOUR_STATE_TAIL` / `FOUR_STATE_REFERENCE` / `Judgment: not-for-me precision >= 80%` / `from prompt_constants import DEFAULT_SYSTEM_PROMPT_EN` / `call_llm` |
| `services/scripts/benchmark_production_live_prompt.py` | ★ **生产 prompt 基准**（同批端点产出）/ `from benchmark_4state_notforme import TEST_SET` / `LIVE_SYSTEM_PROMPT_EN` / `P_live4_prod_prompt` / `P2_live4_prod_prompt_profile` / `prompt_route` |
| `services/scripts/benchmark_campplus_addressee.py` | `SpeakerEmbeddingExtractor` / `_cosine` / `same_scores` / `diff_scores` / `acc(scores, thr)` / `TARGET_THRESHOLD` / `_read_wav_mono16` |
| `doc/research/data/benchmark_4state_notforme_results.json` | `results` / `stats` / `rows` / `raw` / `latency_s` / `matrix`（**已入 git**，50 例，旧三态对照组） |
| `doc/research/data/benchmark_production_live_prompt_results.json` | ★ `prompt_source` / `prompt_route` / `judgment` / `decoding` / `comparison_stats` / `P2_live4_prod_prompt_profile`（**51 例，生产 prompt 真实数字**） |
| `doc/research/addressee-detection-2026-08-12/final_report_addressee_detection.md` | `§5.1` / 基线豆包/Gemini / `H2` 混合路线 / 两阶段实施 |
| `doc/research/addressee-detection-2026-08-12/01_problem_definition.md` | `### 4.3 评测指标` / `EER` / `AP_tss` / `FAR` / `FRR` / `Voice Assistant Query Rejection Benchmark` / `arXiv 2512.10257` |
| `doc/research/addressee-detection-2026-08-12/02_acoustic_approach.md` | CAM++ 生态 / RTF benchmark / `Discussion #3233` |
| `doc/research/addressee-detection-2026-08-12/03_semantic_approach.md` | GPT-4o `80.9%` / 四态方案 A/B/C 对比 |
| `doc/research/addressee-detection-2026-08-12/04_hybrid_approach.md` | `高召回（宁可误触发，不可漏响应）` ← **反向漂移点，勿用** |
| `doc/research/addressee-cross-validation-2026-08-12.md` | **附录 A**（CAM++ 实测）/ **附录 B-1**（50 句，与 JSON 一致）/ **附录 B-2**（42 句，`.workbuddy`）/ `## 四、⚠️ 报告内部口径差异` 的裁决 / `B.7.2` |
| `doc/specs/addressee-detection.md` | `§4.3 必测 ②` / `§4.1` 四态表 / `§4.2.5` 落地决策（不实现融合）/ `JARVIS_ADDRESSEE_DETECTOR_ENABLED` / `未用 GBNF grammar` |
| `doc/research/eval-upstream-methodology-2026-09-20.md` | 上游方法学（**同批姊妹篇，不重复**） |

### 5.2 决策输出路径

| 文件 | 关键词 |
|---|---|
| `services/webinfer/response_format.py` | `parse_model_decision` / `normalize_model_output` / `strip_decision_tokens` / `_DECISION_TOKEN_RE` / `_parse_decision_tokens` / `_chat_completion_response` / `"finish_reason": "stop"` |
| `services/webinfer/stream_protocol.py` | `build_stream_frames` / `_STREAM_DECISION_MARKER_RE` / `_find_first_decision_marker` / `corrected` |
| `services/webinfer/prompt_constants.py` | `LIVE_SYSTEM_PROMPT_EN`（四态）/ `DEFAULT_SYSTEM_PROMPT_EN`（三态）/ `NO_DECISION_SYSTEM_PROMPT` / `Do NOT substitute </silence> for </not-for-me>` |
| `services/webinfer/prompt_assembly.py` | `_resolve_base_system_prompt` / `_build_live_visual_messages` / `compose_live_visual_messages` / `LIVE_VISUAL_OBSERVATION_SEGMENT` / `_build_memory_prompt` |
| `services/webinfer/frame_parsing.py` | `_parse_live_frames` / `_LIVE_FRAMES_MAX` / `_normalize_interaction_mode` / `_validate_local_image_path` |
| `services/webinfer/infer_loop.py` | `_handle_text_chat_streaming` / `_stream_text_payload_frames` / `_handle_text_payload` / `_chat_payload_finalize` / `_chat_payload_build_and_infer` / `emit_event` / `webinfer_request` / `infer_error` / `done_frame` |
| `services/webinfer/chat_payload.py` | `_is_forced_silence` / `build_adapter_timing` / `build_turn_input_record` / `build_prediction_dict` |
| `services/webinfer/session.py` | `get_session` / `_session_output_path` / `_write_session_outputs` / `_flush_session_outputs` / `_maybe_save_chunk_start_model_input` / `emit_event` |
| `services/webinfer/memory_io.py` | `_update_text_qa_history` / `{prediction, decision}` / `qa_history_window` |
| `services/webinfer/adapter_types.py` | `force_silence_before_query` / `keep_qa_history` / `qa_history_window` / `out_dir` / `debug_input_dir` / `session_out_dir` |
| `services/common/event_json.py` | `emit_event` / `EventJsonFormatter` / `logs/events/<service>-<UTC日>.jsonl` / `_LEVEL_MAP` / `PII red line` |
| `services/webui/src/joy_interaction_webui/live_llm.py` | **`finish_llm_turn`**（★首选挂点）/ `decision == "not-for-me"` / `decision = "silence"`（delegation 改写）/ `[addressee] semantic not-for-me` |
| `services/webui/src/joy_interaction_webui/live_proactive.py` | `proactive_loop` / `send_proactive_prompt` / `call_proactive_vlm` / `proactive_enabled_from_env` / `proactive_switch_action` / `staying quiet` |
| `services/webui/src/joy_interaction_webui/live_mode.py` | `handle_frame` / `_handle_commit` / `_send_to_llm` / `_frames_payload` / `get_state_for_browser` / `_default_config` |
| `services/webui/src/joy_interaction_webui/live_frames.py` | `LIVE_FRAME_WINDOW` / `frame_window_from_env` / `append_frame` / `frames_payload` |
| `services/webui/src/joy_interaction_webui/turn_streaming.py` | `StreamingTurnConsumer` / `StreamingTurnResult` / `decision_received` / `[tts-stream] decision=%s` / `frames` / `on_silence_wake` |
| `services/webui/src/joy_interaction_webui/video_processor.py` | `process_interval_seconds` / `frames_per_batch` / `VideoProcessorTrack`（**不产决策 token**） |
| `services/webui/src/joy_interaction_webui/ws_handler.py` | `elif t == "frame"` / `live_session.handle_frame` / `latency[transport+infer-screen]` / `update_process_interval` / `update_frames_per_batch` |
| `services/webui/src/joy_interaction_webui/ws_notify.py` | `notify_session_llm_reply` / `notify_session_tts_sentence` / `reply_epoch` / `notify_session_pilot_utterance` |
| `services/webui/src/joy_interaction_webui/live_routes.py` | `/api/live/start` / `/api/live/status` / `/api/live/enroll` / `/api/live/proactive` |
| `services/webui/src/joy_interaction_webui/jarvis_routes.py` | `feed_wav_to_session` / `/api/jarvis/feed_wav`（**live 无对应端点**） |
| `services/webui/src/joy_interaction_webui/server.py` | `joyai.access` / `webui-access-<UTC>.log` / `offer` / `bind_jarvis_audio_for_peer` |
| `services/webui/src/joy_interaction_webui/addressee_detector.py` | `classify` / `enroll` / `extract` / `target_threshold` |

### 5.3 规格 / 决策（SSOT）

| 文件 | 关键词 |
|---|---|
| `doc/specs/live-interaction-layer.md` | `C.A` / `C.B` / 收敛终点 / `live_mode.py` 驱动循环 |
| `doc/specs/live-visual-cb.md` | 层 1/2/3 / `frames` 协议 / `PROACTIVE_INTERVAL_S` / 帧不进 history |
| `doc/specs/log-event-schema.md` | JSONL schema / PII 红线 / `scripts/log_query.py`（**TBD，不存在**）/ 状态"待实现"（**落后于实现**） |
| `doc/specs/radio-silence.md` | suppressed 子态 / 唤醒三通道（live-only） |
| `doc/specs/interaction-mode-isolation.md` | live/call/jarvis 三模式隔离 |
| `决策/交互模式与决策token规范.md` | `D-2026-08-03-002`（剥离只作用 content，decision 字段保留）/ `D-2026-08-17-001` / `D-2026-09-19-001`（KWS 战略转变） |
| `决策/业务-评测.md` | `D-2026-07-25-081`（**唯一既有"评测"SSOT**：golden recall eval + 嵌入一致性门禁，**与决策评测无关**） |
| `决策/VLM架构与模型组成.md` | `prompt eval 453.64ms` / 「VLM 端到端延迟实测结论」 |
| `ARCHITECTURE.md` §8 | 端到端 P99 ≤ 1.2s / issue #43 已 CLOSED / `frame_seq` 测量环 PR #93 / VRAM 修正 |
| `services/scripts/README.md` | `benchmark_4state_notforme.py` / `benchmark_campplus_addressee.py`（已索引） |

### 5.4 可观测性 / 日志 / 门禁

| 路径 | 内容 |
|---|---|
| `logs/events/webinfer-*.jsonl` | 实测事件名：`session_start` / `webinfer_request`（含 `latency_ms`）/ `wiki_recall` / `wiki_recall_fail` / `circuit_breaker_open` / `circuit_breaker_close` / `push_memory` / `infer_error` / `session_end` / `probe_test` —— **无决策事件** |
| `logs/fa-probe-llama.log` | ★ 唯一含 llama `print_timing` 的日志：`prompt eval time = 453.64 ms / 448 tokens` / `eval time = 520.00 ms / 40 tokens` / `total time = 973.65 ms / 488 tokens` / `graphs reused = 39`；启动行含 `verbosity = 3` / `n_ctx_slot = 16384` / `kv_unified = 'false'` |
| `services/.logs/llama-main.err.log` | llama-server 启动日志（**launcher 每次 truncate**）；`verbosity = 3`；当前无 timing 行（该次运行无推理） |
| `services/.logs/webui.err.log` | 非结构化文本；含 `[live-mode] LLM response (decision=...)` / `[tts-stream] decision=...` / `[addressee] ...` 的**代码路径**，但本次快照中 live 行几乎为零 |
| `services/logs/webui-access-<UTC>.log` | JSONL：`{ts, method, path, status, latency_ms}`（**注意在 `services/logs/`，不是 `logs/`**） |
| `logs/vlm-runtime-props.json` + `logs/vlm-probes/<ts>.json` | `/props` 快照（`n_ctx`）；`scripts/vlm_runtime_probe.py` 生成 |
| `scripts/drift_gate.py` + `config/drift-contract.json` | 契约式门禁（10 条 check，**全为配置/端口类型，无决策项**）；退出码 0/1/2/3；`phase` / `severity` / `mode` 语义 |
| `scripts/log_maintenance.ps1` | 日志/探针保留期清理（默认 14 天 / 探针 1 天） |
| `services/scripts/run-windows.ps1` | `Start-LlamaMain`（`-ngl 999` / `--parallel 1` / `-fit off` / `--jinja`；**`MAIN_CONTEXT` 默认 4096**）/ `Start-Webinfer`（`--frame-save-dir` / `MEMORY_STORE_URL`）/ `Start-Background`（**truncate 日志**）/ `$LogDir = services\.logs` |

### 5.5 测试（现成的"回放骨架"与"决策断言"）

| 文件 | 关键词 |
|---|---|
| `services/webui/tests/test_live_mode.py` | `build_live` / `FakeVAD` / `FakeASR` / `FakeClock` / `FakeConsumer` / `test_not_for_me_decision_no_broadcast_back_to_listening` / `test_addressee_non_target_segment_dropped_no_asr` / `test_fail_open_llm_stream_retries_non_streaming` —— **进程内驱动 `feed_audio` 的完整回合骨架** |
| `services/webinfer/tests/test_live_visual.py` | `_make_adapter` / `_StubCompletions` / `_post_json` / `test_live_visual_not_for_me_decision` / `test_live_visual_proactive_empty_text_no_qa_history_pollution` —— **直发 `/v1/text/chat` 带 `frames` 的请求构造骨架** |
| `services/webinfer/tests/test_decision_notforme.py` | 25 条四态解析器锁定测试（**本地已跑通：25 passed**） |
| `services/webinfer/tests/test_qa_4state_supplement.py` | `_resolve_base_system_prompt` 的 live=四态 / jarvis=三态 硬断言；jarvis prompt 不得含 `not-for-me` |
| `services/webinfer/tests/test_decision_parser_regression.py` / `test_normalize_model_output.py` / `test_decision_token_isolation.py` | 解析器回归面 |
| `services/webinfer/tests/test_video_chat_endpoint.py` | `test_video_forward_*` / `test_video_finalize_*`（多模态路径的 decision 提取） |
| `services/webui/tests/test_addressee_detector.py` / `test_addressee_qa_edges.py` | 声学门控单测（Phase 1） |

**测试规模基线**：`services/webinfer/tests/` 39 个文件、`services/webui/tests/` 82 个文件；其中 live/addressee 相关 webui 测试 12 个。

---

## 附：四处必须向文档/上游反馈的更正

1. **`vlm-lightweight-2026-09.md` §1.6/§2 的 `image slice encoded in N ms` 断言在本机未获证实**——本机全部已捕获日志中零命中，只有 `prompt eval time` / `eval time`。可作为"待验证"而非"已有能力"（§4.3）。
2. **`log-event-schema.md` 状态标为"待实现"，但 emitter（`services/common/event_json.py`）已实现并在生产运行**（已经有 webinfer 日 JSONL 与 `emit_event` 调用点）。spec 状态落后于实现，建议更新（本次只读，不改动）。
3. **`benchmark_4state_notforme.py` 与生产 live prompt 已经不一致**：脚本用的是 `DEFAULT_SYSTEM_PROMPT_EN`（三态），线上走 `LIVE_SYSTEM_PROMPT_EN`（四态）。该 benchmark 建于四态落地**之前**（`d709af6` / `16c4f9d` vs 四态实现 `80cffce`），**此后未复跑对齐**。⇒ 它的 **84% 误响应率是"改造前"的数字，不是现状**；引用时必须标注。**该裂缝已被同批端点的 `benchmark_production_live_prompt.py` 闭合**（§1.1.1，生产 prompt 真实误响应率 **30.8%**）。**建议：把该脚本的 `build_live_prompt_3state` 从"复刻三态"改为"import `LIVE_SYSTEM_PROMPT_EN`"，使基准与生产永久对齐。**
4. **`--image-max-tokens` / `--image-min-tokens` 未被配置**：本机 llama-server 启动参数（`Start-LlamaMain`：`-m` / `--mmproj` / `--host` / `--port` / `-c` / `-ngl 999` / `--parallel 1` / `-fit off` / `--jinja`）**没有 `--image-min-tokens`**，而 llama-server 启动日志**反复告警**「Qwen-VL models require at minimum 1024 image tokens to function correctly on grounding tasks / try adding `--image-min-tokens 1024`」。⇒ 若评测发现"看画面问答不准"，**先查这一条**（它是一个已知的、未采纳的官方建议，可能构成决策质量的隐性上限）。

---

*报告完毕。本次为只读侦察：未修改本仓库任何代码/配置/文档（除本报告外）。*
