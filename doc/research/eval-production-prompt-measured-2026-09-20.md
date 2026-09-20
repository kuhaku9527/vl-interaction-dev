# 生产 live prompt 实测决策性能（2026-09-20）

> 端点身份：**测试端点（AFK 执行）**
> 被测对象：`services/webinfer/prompt_constants.py::LIVE_SYSTEM_PROMPT_EN` —— 即
> `prompt_assembly._resolve_base_system_prompt()` 在 `interaction_mode == "live"` 时
> 真正下发的**生产 prompt 本身**（不是脚本内嵌变体）。
> 脚本：`services/scripts/benchmark_production_live_prompt.py`（新增，可重复运行）
> 数据：`doc/research/data/benchmark_production_live_prompt_results.json`（run1）
> 　　　`doc/research/data/benchmark_production_live_prompt_results_repeat.json`（run2 复现）
> 测试集：复用 `benchmark_4state_notforme.py::TEST_SET`（51 例），直接 `import`，未复制粘贴；
> 解码参数 `MAX_TOKENS=1024 TEMPERATURE=0.8 TOP_P=0.9 TOP_K=40`；
> 请求形态与决策解析逻辑与历史 benchmark 完全一致（`call_llm` / `parse_decision_4state` 原样复用）。

---

## 0. 一句话结论

**生产 prompt 的 nfm precision 100% 是"靠弃权"换来的**：纯生产 prompt
**26 例非面向语音中一次都没输出 `</not-for-me>`（recall = 0%）**，
16 例退化成 `</silence>`、10 例直接开口；配上 BT-7274 角色 profile 后才挤出 3 例
（recall 12%），而这 3 例里有 2 例**正是 prompt 自己 few-shot 里逐字教过的句子**。

**三个关键数字（生产实测，两轮均值）**：

| 指标 | 纯生产 prompt | +角色 profile（生产真实组装） |
|---|---|---|
| **误响应率**（非面向 → 开口 response+delegation） | **38.5%**（10/26） | **34.6%**（9/26） |
| **nfm precision** | **0.0%**（预测 0 例） | **100%**（仅 3 例，空精度） |
| **nfm recall** | **0.0%** | **11.5%** |

**是否达判据**：字面**达标**（P2 = 100% ≥ 80%），但**实质不成立**——召回 11.5% 意味着模型
几乎不用这个四态，precision 的分母只有 3。且判据**漏掉了生产真正会出声的路径**
（非面向 → `</silence>`/`</response>`），见 §3。

**水平定位**：优于旧 3 态（误响应 84%），在"少乱插话"上**优于历史最优 C_live4_reframe**
（36% vs 56%），但在**"该沉默时明确判 not-for-me"上远不如 C**（recall 0–12% vs **36%**），
且**吞掉面向句比 C 更多**（详见 §2）。

> 关键机制前提：本模型里 `</silence>`(id 151669) / `</response>`(id 151670) 是**单 special token**，
> 而 `</not-for-me>` **不是词表 token**（=5 个普通 token：`</`+`not`+`-`+`for`+`-me>`）。
> 因此 `</silence>` 会被 llama-server 从 content 中剥离成空串。
> 本次对每条请求加了 `logprobs` 并记录实际生成的 token id（JSON 里 `emitted_token_ids` /
> `emission` 字段），**"沉默 vs not-for-me" 是逐条实测消歧的，不是解析假设**。
> `logprobs` 不影响采样。

---

## 1. 实测数据表

### 1.1 与历史变体同口径对比（**统一到历史结果的 25 例非面向分母**）

⚠️ 历史结果文件的字段分母是 **25**（当时测试集为 50 例：directed 25 / nondirected 25，
`N01b` 是后来才加进 `TEST_SET` 的，历史文件不含它）。生产 prompt 跑的是**当前 51 例**。
为可直接对比，下表**统一在历史与本次共有的 25 例非面向句（N01..N25）上计算**。

| 变体 | 误响应率（非面向→response） | 开口率（response+**delegation**） | nfm recall（非面向→not-for-me） |
|---|---|---|---|
| A_live3_prod（历史·旧 3 态） | 84.0% (21/25) | 92.0% (23/25) | 0.0% (0/25) |
| A_live3_clean（历史·无角色） | 76.0% (19/25) | 76.0% (19/25) | 0.0% (0/25) |
| B_live4_prod_append（历史） | 52.0% (13/25) | 56.0% (14/25) | 8.0% (2/25) |
| B2_live4_prod_append_rich（历史） | 56.0% (14/25) | 60.0% (15/25) | 8.0% (2/25) |
| C_live4_reframe（历史·重构式参考） | 52.0% (13/25) | 56.0% (14/25) | **36.0% (9/25)** |
| **P_live4_prod_prompt** run1 / run2 | 40.0% / 28.0% | 40.0% / 36.0% | **0.0% / 0.0%** |
| **P2_live4_prod_prompt_profile** run1 / run2 | 28.0% / 32.0% | 36.0% / 36.0% | **12.0% / 12.0%** |

> 生产 prompt 的**两轮均值**（25 例基准）：纯 prompt 误响应 **34.0%**、开口 **38.0%**、recall **0%**；
> +角色 profile 误响应 **30.0%**、开口 **36.0%**、recall **12.0%**。

### 1.2 生产脚本自身口径（当前 51 例，即 JSON 的 `stats` 字段原值）

| 变体 | mis_resp% | nfm precision% | nfm recall% | directed_miss%（仅 directed→not-for-me） |
|---|---|---|---|---|
| P_live4_prod_prompt run1 | 38.5 | 0.0 | 0.0 | 0.0 |
| P_live4_prod_prompt run2 | 26.9 | 0.0 | 0.0 | 0.0 |
| P2_live4_prod_prompt_profile run1 | 30.8 | 100.0 | 11.5 | 0.0 |
| P2_live4_prod_prompt_profile run2 | 30.8 | 100.0 | 15.4 | 0.0 |

混淆矩阵（run1）：

| 变体 | directed: resp / sil / deleg / **nfm** | nondirected: resp / sil / deleg / **nfm** |
|---|---|---|
| P 纯生产 | 16 / 3 / 6 / **0** | 10 / 16 / 0 / **0** |
| P2 +角色 | 11 / 2 / 12 / **0** | **8 / 13 / 2 / 3** |

**`nondirected → not-for-me` 那一列（0 与 3）就是全部病灶。**

### 1.3 面向句被"吞"（比判据字段更严的附加指标）

判据字段 `directed_miss_rate_pct` 只统计 directed→**not-for-me**，所有变体都是 0.0%。
但把"该答却被 `</silence>` 吞掉"也算进来后（25 例面向句基准）：

| 变体 | 面向句被吞（silence 或 not-for-me） |
|---|---|
| C_live4_reframe | **1** (D19) |
| B2_live4_prod_append_rich | 3 (D15, D18, D19) |
| P_live4_prod_prompt | **3** run1 (D19, D20, D25) / **3** run2 (D18, D19, D20) |
| P2_live4_prod_prompt_profile | **2** run1 (D09, D19) / **1** run2 (D09) |

→ **生产 prompt 吞掉面向句的数量 ≥ C 的 1–3 倍**；D19「推荐配置是什么？」在**两轮两变体上稳定被吞**（硬伤）。

### 1.4 稳健性（两轮差异）

- 面向句**很稳**：两个变体 directed→not-for-me 恒为 0，未出现一次"该答被误杀成 not-for-me"。
- 非面向句**不稳**：26 例中 12（P）/ 14（P2）例在两轮之间改变决策。
- **单轮 51 例不足以支撑 1–2 个百分点级结论**；本报告给两轮均值，逐例差异可 diff 两个 JSON 的 `rows[].decision`。

---

## 2. 与历史变体对比：生产 prompt 落在什么水平？

**结论：生产 prompt 是"第一个正式把 addressee 判定写进 prompt 的版本"，
也是"误插话最少"的版本，但它并没有真的学会 `</not-for-me>`；
在判据真正关心的召回维度上，它明显差于 C_live4_reframe。**

1. **对旧 3 态（84% 误响应）：真实大幅进步。**
   生产 prompt 误响应降到 **34%**（纯）/ **30%**（+角色），
   且确实把 `## First: Addressee Judgment (highest priority)` 放在三态动作之前——
   结构上是对的。
2. **对 B/B2（append 式四态）：误响应更低（34%/30% vs 52%/56%），
   但 nfm recall 纯 prompt（0%）反而比 B/B2（8%）更差**，
   加角色 profile 后（12%）才略微超过。
3. **对 C_live4_reframe：这是关键比较——生产 prompt 在"该沉默"上更好，在"识别沉默"上远差。**
   - **误插话更少**：开口率 36–38% vs C 的 56%。→ 生产 prompt 确实"更不乱插"。
   - **recall 差 3–∞ 倍**：C 的 36%（9/25）vs 生产 0%（纯）/ 12%（+角色）。
     C 是唯一把 `</not-for-me>` 真正用起来的 prompt。
   - **吞掉面向句更多**：C 只吞 1 例，生产吞 2–3 例。
   - **C 的 52% 不是"更好"的误响应，而是同一现象的另一半**：
     C 把 13 例非面向判成 response（而非 silence），生产把 10–16 例判成 silence。
     两者误响应数字接近，但**C 换来了 9 例正确的 not-for-me，生产换来的是 0–3 例**。
     **即：同样"没有乱插"的量级下，C 的沉默是"判定出来的"，生产的沉默是"退化的"。**

**一句话定位**：生产 prompt 处在 **"比旧 3 态好得多、比 B/B2 好、但不如 C"** 的水平；
它把问题从"乱插话"改写成了"**该 not-for-me 时只会 silence 或开口**"，并未解决 addressee 判定。

---

## 3. 是否满足判据（`not-for-me precision >= 80%`）？

**字面达标，实质不成立 —— 建议判定为"未通过"。**

1. **字面**：P2（+角色 profile）= **100%** ≥ 80% ✅；
   纯生产 prompt 因 nfm 预测数 = 0，`pct()` 定义下 precision = **0.0%** ❌。
2. **空精度**：那个 100% 的分母只有 **3 例**（`n_not_for_me_predicted = 3`），
   而 recall 只有 **11.5–15.4%**。precision 高 + 召回近零 = 模型基本没在用这个四态，
   判据无法据此证明 addressee 判定能力存在。
3. **判据的立意只达成一半**：源码原话
   `宁漏不乱插: false not-for-me is acceptable, missing a directed utterance is not`。
   - ✅ "missing a directed utterance"（面向句被判 not-for-me）**确实没发生**：恒为 0。
   - ❌ 但"乱插"以 `</response>` / `</delegation>` 形式发生在 **34–38%** 的非面向句上。
     判据把"乱插"只定义为 false *not-for-me*，**恰好漏掉了生产环境真正会出声的
     `</silence>`→`</response>` 这条路径**。这是**判据的度量缺口**，不是模型的通过。
4. **判据可被"弃权"刷过**：一个永远输出 `</silence>` 的模型
   会得到 `not_for_me_precision = 100%`（若偶尔输出一例 not-for-me）与
   `directed_miss_rate = 0%`，**完全通过**。生产 prompt 正走在这条路上。

**建议**：判据补一条上限指标 —— **"非面向 → 开口（response/delegation）率 ≤ X%"**
（当前生产 = 34–38%，C = 56%），否则该判据无法约束真实体验。

---

## 4. 逐条失败案例分析（最有价值的部分）

### 4.1 最典型失败模式：`</silence>` 吞掉 `</not-for-me>`

**纯生产 prompt：26 例非面向中 `</not-for-me>` 输出 0 次，`</silence>` 输出 16 次。**
模型完整学会了"**不要回答**"这个行为，但**没有执行 addressee 判定**，
而是退化回旧 3 态习惯——用 `</silence>` 表达"我决定不说话"。

token 级证据（JSON `emission` 字段全部为 `silence_special_token_151669`）：

| id | 句子 | 期望 | 实际 | 备注 |
|---|---|---|---|---|
| N01 | 这关怎么这么难啊 | not-for-me | **silence** | ⚠️ **该句逐字出现在 prompt 自己的 few-shot 里，标注 `</not-for-me>`** |
| N01b | 哎呀，这关怎么那么难 | not-for-me | **silence** | ⚠️ 同上，逐字在 few-shot 里 |
| N02 | 唉，好累 | not-for-me | **silence** | ⚠️ 同上，逐字在 few-shot 里 |
| N06 | 完了完了，要迟到了 | not-for-me | **silence** | ⚠️ 同上，逐字在 few-shot 里 |
| N09 | 嗯，好的好的 | not-for-me | **silence** | ⚠️ 同上，逐字在 few-shot 里 |
| N14 | 妈妈，我回来了 | not-for-me | **silence** | ⚠️ 同上，逐字在 few-shot 里 |
| N05 | 今天天气真好啊 | not-for-me | silence | 同类感叹泛化 |
| N11 | 你说得对，就这样吧 | not-for-me | silence | 同类回应泛化 |
| N12 | 啊，吓死我了 | not-for-me | silence | 感叹 |
| N16 | 这孩子怎么又哭了 | not-for-me | silence | 自言自语 |

**这是本次最尖锐的发现**：prompt 里**已经逐字教了 `</not-for-me>`**，模型仍输出 `</silence>`。
说明在 `</silence>` 是单 special token、且三态动作被反复强调的情况下，
**"示范-模仿"被旧行为的 token 先验压过**。见 §4.4。

### 4.2 第二失败模式：把"非面向"补全成对话并开口（误响应 10/26）

纯生产 prompt 的 10 例开口**全是 `</response>`**，**没有一例被 not-for-me 拦下**：

| id | 句子 | 实际输出（节选） | 失败性质 |
|---|---|---|---|
| N04 | 你把那个拿过来 | "这句话**不是对我说的**，我没收到任务。" | **判对了却仍开口**——把内部判定当话说了出来 |
| N03 | 对，我也觉得 | "这话听起来像是你在回应别人或者自言自语呢，不过既然你这么说了…" | 认出旁听却仍接话 |
| N15 | 老公，晚上吃什么？ | "我是AI助手，没有老公哦。您是在问晚餐吃什么吗？…" | 对非 AI 称呼对象应答 |
| N23 | Honey, did you see my glasses? | "No, I didn't see your glasses…" | **替"老公"回答了** |
| N17 | 加油，你可以的 | "谢谢鼓励！我会继续努力的…" | **把鼓励旁人的话当成对自己的鼓励** |
| N19 | 哎，这日子什么时候是个头 | "听起来心情不太好呢。如果需要倾诉…" | 感叹 → 主动安慰（prompt 明令禁止） |
| N07 | 天哪，这都什么事啊 | "嘿，是不是遇到什么麻烦事了？需要我帮忙查一下…" | 感叹 → 主动服务 |
| N08 | 你先走吧，我一会就来 | "好的，我在这儿等着。" | **把"你先走"当成对自己的指令** |
| N10 | 这个游戏真好玩 | "是啊，节奏很快…你是第一次玩这种类型的吗？" | 感叹 → 反问闲聊 |
| N13 | 我去拿个快递 | "好的，祝你拿快递顺利。" | 告知 → 应答 |

**子模式**：prompt 明写 `Do not reply, help, comfort, or comment`，
并在规则里点名 `A frustrated or tired complaint ("这关太难了" / "好累啊") is the speaker
venting to themselves, NOT a request for you to comfort them`。
但 **N19/N07 正是这条被点名禁止的情形**，且 N01「这关怎么这么难啊」也被判 silence 而非 not-for-me。
→ **规则文本被 persona 的 `P3: Protect the Pilot` / `assist the Pilot`
与"共情应答"先验覆盖**。

### 4.3 角色 profile 的双向作用

- **正面**：加角色 profile 后 `</not-for-me>` 从 **0 → 3/4 例**（N01 / N04 / N16，run2 再加 N01b）；
  非面向→response 从 10 降到 8，误响应 34% → 30%。
  机制像是 **persona 的机械口吻让模型更愿意"照抄示范"**。
- **负面 ①（更严重）**：`</delegation>` 从 **6 → 12**（面向句）。P2 把大量本可直接回答的问题
  推给后台子代理，例：D07「嘿，你听到了吗？」→
  `正在处理。 </delegation> 我可以帮你做什么？`（把寒暄变成检索任务）。
- **负面 ②（最严重）**：非面向句出现 **2 例 delegation**——
  N08「你先走吧，我一会就来」→ `收到，稍等。 </delegation> 你先走吧，我一会就来`；
  N10「这个游戏真好玩」→ `…</delegation> 查这个游戏的攻略和优化设置`。
  **把别人的话当任务派给后台，比单纯应答更糟**（会触发外部检索与 TTS 播报）。
- **负面 ③**：面向句被吞 D09「给我讲个笑话」→ silence（与 persona `No unnecessary dialogue` 一致）。

### 4.4 测试集与 prompt few-shot 逐字重叠（**评测效度警告**）

生产 prompt 的 `## Examples` 区块中，**有 10 句测试集句子逐字出现**，
其中 8 句是非面向：

```
D02  介绍一下你自己            → response   ✅
D03  喂，帮我查一下明天的天气   → delegation ✅
N01  这关怎么这么难啊          → silence    ❌（prompt 里标注 </not-for-me>）
N01b 哎呀，这关怎么那么难      → silence    ❌（prompt 里标注 </not-for-me>）
N02  唉，好累                  → silence    ❌（prompt 里标注 </not-for-me>）
N03  对，我也觉得              → response   ❌（prompt 里标注 </not-for-me>）
N04  你把那个拿过来            → response   ❌（prompt 里标注 </not-for-me>）
N06  完了完了，要迟到了        → silence    ❌（prompt 里标注 </not-for-me>）
N09  嗯，好的好的              → silence    ❌（prompt 里标注 </not-for-me>）
N14  妈妈，我回来了            → silence    ❌（prompt 里标注 </not-for-me>）
```

**含义 1 —— 路走的对不对**：这 8 例非面向逐字示范，模型 **0 例**照抄成 `</not-for-me>`
（6 例 silence、2 例开口）。→ **问题不在"没教"，而在"教了也不执行"。**
这比"没教"更值得警惕：说明加了更多 few-shot 也难解决（与 B2 的 8% recall 一致）。

**含义 2 —— P2 的 12% recall 主要是背诵**：P2 的三个 not-for-me 命中是 N01 / N04 / N16，
其中 **N01、N04 属于逐字重叠集**。拆开算（run1）：

| 子集 | 大小 | P2 的 not-for-me 命中 | P2 recall | P 的命中 | P recall |
|---|---|---|---|---|---|
| 逐字重叠（N01,N02,N03,N04,N06,N09,N14） | 7 | 2 | 28.6% | 0 | 0% |
| **泛化（其余，真本事）** | **18** | **1** (N16) | **5.6%** | **0** | **0%** |

run2 的 4 例 not-for-me 命中为 N01b / N04 / N06 / N14，**全部落在逐字重叠集内**，
泛化子集命中 **0/18 = 0%**（逐字重叠子集则 3/7 = 42.9%）。
→ **P2 表面 12% 的 recall，扣掉记忆后只有 0–5.6%；run2 更是泛化 0%。**
**生产 prompt 的泛化 addressee 判定能力≈没有。**

**含义 3 —— 误响应数字是乐观上界**：在泛化子集（18 例）上重算，同口径对比历史变体：

| 变体 | 误响应（泛化 18 例） | 开口率 | nfm recall |
|---|---|---|---|
| B_live4_prod_append | 55.6% | 61.1% | 11.1% |
| B2_live4_prod_append_rich | 50.0% | 55.6% | 11.1% |
| C_live4_reframe | 55.6% | 61.1% | **33.3%** |
| P_live4_prod_prompt | 44.4% / 27.8% | 44.4% / 33.3% | **0.0% / 0.0%** |
| P2_live4_prod_prompt_profile | 27.8% / 38.9% | 38.9% / 44.4% | **5.6% / 0.0%** |

→ 扣除记忆收益后，**生产 prompt 仍是"乱插最少、但 not-for-me 能力最弱"的那一个**，
结论方向不变。

---

## 5. 局限

1. **纯文本、无视频帧、无 memory 注入**（最主要局限）：
   本次只发 `system = prompt` + `user = 单句文本`，**未送 image_url / video frame**，
   也**未注入 `[Previous Memory]` / `[Local Wiki]` 块**
   （`compose_system_prompt_with_memory` 的后半段未参与）。
   生产环境有画面上下文，模型可能因"画面值得报告"而**更倾向开口**，
   即**真实误响应率可能高于本测**（方向上使生产表现更差，不改变结论）。
2. **未复现生产完整链路**：未走 webinfer 的 streaming 决策帧路径
   （本次直连 llama-server `/v1/chat/completions`，与历史 benchmark 口径一致，便于对比），
   也未复现 QA history / `interaction_mode` 全链路。
3. **temperature=0.8 有随机性**：已跑两轮并报均值；26 例非面向中 12–14 例两轮不一致，
   说明**单轮 51 例样本量偏小**，1–2 个百分点级差异不可解读。
4. **测试集效度**：8 句非面向句是 prompt few-shot 的逐字复制（§4.4），
   §1.2 的误响应数字为乐观上界；泛化数字见 §4.4 含义 3。
   与历史变体可比性说明：**B/B2/C 的 few-shot 与测试集不重叠**（原脚本注释明示），
   所以历史变体面对的是更公平的测试，生产 prompt 则相当于"开卷考"。
5. **跨变体分母口径**：历史结果文件字段统一 ÷**25**（当时 50 例测试集），
   本次生产脚本字段 ÷**26**（当前 51 例，多了 N01b）。
   §1.1 / §4.4 已统一到共有 25 例 / 泛化 18 例子集重算，**跨表比较请以重算值为准**。
6. **`</not-for-me>` 非词表 token 的测量细节**：空 content 只说明"未生成可见文本"，
   本次用 `logprobs` 的 token id 消歧（151669=`</silence>`、151670=`</response>`）。
   若未来模型把 `</not-for-me>` 合并成单 special token，该消歧逻辑需同步更新。

---

## 6. 建议下一步（供决策端点参考）

1. **不要用"precision 100%"宣布 addressee 判定已解决**：recall 0–12%、泛化 recall 0–5.6%。
2. **优先考虑把 C_live4_reframe 的"raw transcript 重构 + addressee 优先"正式化**：
   C 的 nfm recall 36% 是生产（0–12%）的 3–∞ 倍，代价是开口率 56% vs 36%。
   需要产品侧就"乱插 vs 漏判"重新定权——**但注意 C 也吞掉 1 例面向句，生产吞 2–3 例，C 并不更差。**
3. **修测试集效度**：把与 prompt few-shot 逐字重叠的 10 句剔出主指标（或换成同义改写），
   否则后续 prompt 迭代会持续"开卷作弊"。
4. **审 `</silence>` 与 `</not-for-me>` 的行为混淆**：这是当前**最大的可修复项**——
   模型已学会"不要答案"，只需把输出 token 从 151669 引导到 `</not-for-me>`。
   可考虑：降低 `</silence>` 在 live 模式的可用性、或在 few-shot 中**全部替换**
   而非并列展示两者。
5. **补判据指标**：新增"非面向 → 开口率"上限（当前生产 34–38%、C 56%），
   并给 not-for-me recall 设下限，否则该判据可被"全判 silence"无条件通过。
6. **注意 D19「推荐配置是什么？」的稳定漏判**（两轮两变体均被吞）——明确的用户提问被静音，
   是最直接的用户体感损失，建议单独排查。

---

## 附：复现方式

```bash
# 1. 起 llama-server（见下方命令），等 /health 返回 ok
# 2. 跑生产 prompt 基准（写入 doc/research/data/benchmark_production_live_prompt_results.json）
/d/AI/envs/joyai-main/python.exe services/scripts/benchmark_production_live_prompt.py
# 3. 复现第二轮（temperature=0.8 随机性，写到另一个文件便于 diff）
BENCH_PROD_OUT=doc/research/data/benchmark_production_live_prompt_results_repeat.json \
  /d/AI/envs/joyai-main/python.exe services/scripts/benchmark_production_live_prompt.py
```

本报告数据点由两次完整运行（各 51 例 × 2 变体 = 102 次请求，合计 204 次）产出，0 个请求失败。
