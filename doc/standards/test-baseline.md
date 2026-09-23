# 测试与验收现状基线（滚动更新）

> **目的**：本项目此前**跑过大量测试但结果没有落盘**，导致后人重复跑、或凭印象认定「坏了 / 好了」。
> 本文件把结果收拢成**一张可查表**：跑过什么、什么结果、**何时测的**、**真机还是离线**。
>
> **维护纪律**：
> 1. **每次实测后立刻追加一行** —— 不追改历史行（数字变了就新增行，并注明被覆盖）。
> 2. **必须写测量时间**。本项目已有实测证明：同一命令在不同时刻结论不同
>    （例：edge-tts 在 13:11 通、13:52 不通、13:53 又通 —— 端点 flaky）。
> 3. **必须区分「真机」与「离线/静态」**。真机 = 6 服务在跑、走真实进程；
>    静态/单测 = 不依赖服务。两者不可互相替代。
> 4. **失败与跳过也要记**。只记绿等于没记。
> 5. **eval 类必须写分母**（本项目踩过 25 vs 26 分母不一致导致跨变体比较静默失效）。
>
> 类型 B（事件型）：本文件是**证据台账**，不是验收计划 —— 验收计划在 `doc/acceptance/`。
>
> **怎么追加证据**：不要手抄。跑 `python scripts/verify_ritual.py`，它的输出**本身就是台账行**
> （含命令 / 结果 / 真机或离线 / 测量时间四要素），直接粘贴即可 —— 见 §7。

---

## §1 当日新增（倒序，最新在上）

### 2026-09-23（★ 时序轴记分卡：把「就算判断对了，是不是说得是时候」变成一条命令；工单 #158）

> **父 spec #154 的第 4 片纵切**（第 2 片 = #157 定向轴，已交付）。
> **前置**：#156（决策事件读侧 + 轮次打点）已交付 ⇒ 本票有数据源可依。
>
> **一句话结论**：时序轴**已能算出并判红**，但在**真机**上它会正确地报出
> 「premature **不可测**」—— 因为写入侧**从未记录**「决策那一刻用户是否仍在说话」。
> 这是**如实读数**，不是缺陷：把它读成 0.0 的抢话率会让一个从没测过的量看起来完全健康。
>
> ★ **两轴正交、不合成**：定向轴（#157）问「该不该说」，时序轴问「说的时机对不对」。
> 父 spec §三明确否决单一 accuracy（两类错误代价不对称）。本票把这条禁令变成
> **可判红的扫描**（`accuracy` / `f1` / `combined_score` …），使「合成」**不可表示**，
> 而不是「可以被检测」。

**装置**（七个模块，按「一个模块一个变化原因」拆分 —— `coding-standards.md` §7）：

| 模块 | 职责 | 行数 |
|---|---|---|
| `decision_eval_timing.py` | 怎么算：onset / 每秒误触发 / premature + **出处取证** + 禁合成扫描 | 875 ⚠️ |
| `decision_eval_timing_criteria.py` | 怎么判：8 条判据 + 阈值出处与冻结快照 | 780 ⚠️ |
| `decision_eval_timing_negatives.py` | 负控与可证伪性自检（10 个故意做错的输入） | 434 |
| `decision_eval_timing_sources.py` | 从哪读：事件流 × 真值 sidecar → 逐轮行 | 280 |
| `decision_eval_timing_synthetic.py` | 离线夹具（三条不变式由生产代码路径断言） | 186 |
| `decision_eval_timing_report.py` | 怎么印 / 怎么 diff / 两轴并排 | 232 |
| `decision_eval_timing_card.py` | **组装成卡 + CLI**（公开入口，再导出下层符号） | 505 |

> `timing` / `criteria` 在 §7 的 600 行「smell」之上（但**均已低于 1000 行「problem」线**）。
> 大头是**判据、负控与出处的说明性文本**（每条都带「为什么这么定」与实测教训），
> 这些文字是交付物的一部分（供后人复核判据是否仍有效），压缩等于删证据。

**命令**：`cd services/webinfer && python -m decision_eval_timing_card`　
**退出码**：`0`（冻结夹具判定 PASS；**「无法测量」映射为 2，永不返回 0**）

| 判据 | 读什么 | 阈值 | 本票读数 | 判定 |
|---|---|---|---|---|
| `T_LATENCY_SOURCE` | 耗时出处 | 无（出处判据） | `decision_chain_round_stamp` | pass |
| `T_ONSET_MEASURED` | 每次开口是否带轮次打点 | 无 | 21/21 带打点 | pass |
| `T_NO_COMBINED_ACCURACY` | 合并分数键扫描 | 无 | 未出现 | pass |
| `T_SPURIOUS_TIMEBASE` | 速率的时间基准 | 无 | 494.3 s | pass |
| `T_PREMATURE_MEASURED` | premature 标注完整性 | 无 | 19/19 有标注（夹具） | pass |
| `T_SAMPLE_FLOOR` | 开口样本量 | ≥10 | 21 | pass |
| `T_ONSET_MEDIAN` | onset 中位数 | ≤898 ms | **573 ms** | pass |
| `T_ONSET_P90` | onset p90 | ≤1120 ms | **731 ms** | pass |

**三个量（冻结夹具，34 轮 / 2 会话 / 30 用户轮 + 4 主动轮）**：

| 量 | 读数 | 分母 |
|---|---|---|
| onset 延迟 | 中位 **573 ms**，p90 **731 ms**（区间 330–880） | 21 次开口，缺打点 0 |
| 每秒误触发 | **0.002 次/秒**（= 0.121 次/分钟） | 1 次误触发 / 494.3 s |
| premature rate | **31.6%** | 6 次抢话 / 19 次有标注的开口 |

> ★ **这份夹具是「作者写的回放输入」，不是真机读数** —— 卡片与资产里都逐条声明了
> 这一点。它证明的是「时序轴能从冻结输入算出并判红」，**不是**生产模型的实际时机质量。

**★ 阈值出处（每个数都能从冻结快照复算，`--verify-bounds` 逐项比对）**：

| 阈值 | 值 | 派生式 | 限定 |
|---|---|---|---|
| `onset_median_ms` | 898 | `ceil(median + pstdev)` = ceil(664 + 233.89) | 样本仅 **4** 例 ⇒ 只是**宽松上界** |
| `onset_p90_ms` | 1120 | `ceil(max × 1.25 / 10) × 10` = ceil(891×1.25/10)×10 | 同上；p90 **不能**用 `ceil(median+pstdev)`（那个量对 p90 不是上界） |

来源快照：`logs/events/webui-2026-09-22.jsonl` 的 4 个非零 `latency_ms`（282/547/781/891）。
★ **不从活文件派生** —— 否则一次退化 + 一次重跑就能把线一起挪走（#157 已付费学过）。

> ⚠️ **「可复算」与「可核验」是两件事，不得混说**（复核纠正过初版措辞）：
> 阈值能从那四个数字**逐项重算**（`--verify-bounds`），但那个来源文件**不可核验** ——
> 它没有 sha256 绑定，且 `logs/` 在 `.gitignore` 下、对作者之外的人不存在。
> 把后者说成前者是「看起来严谨」的典型形态。

**★ 负控（10 个故意做错的输入 / 9 killed + 1 恒等干净）**：

| 负控 | 输入错在哪 | 期望 | 实测 |
|---|---|---|---|
| **`dropped-round-stamp`** | ★ **删掉轮次打点**（`ts` 保留） | `T_ONSET_MEASURED` **判红** | **fail** ✓ |
| **`event-ts-diff-latency-source`** | ★★ 耗时**真的**由 `ts` 差值算出，而**声明一个字都不改** | `T_LATENCY_SOURCE` 判红 | fail ✓ |
| `frame-ts-latency-source` | 耗时**真的**由帧钟算出（1 Hz 整数倍），逐行标注帧钟 | `T_LATENCY_SOURCE` 判红 | fail ✓ |
| `missing-origin-field` | 耗时在，但逐行出处字段被抹掉 | `T_LATENCY_SOURCE` 判红 | fail ✓ |
| `combined-accuracy` | 往块里塞 `accuracy` | `T_NO_COMBINED_ACCURACY` 判红 | fail ✓ |
| `dropped-premature-labels` | 删掉全部 premature 标注 | `T_PREMATURE_MEASURED` 不得判绿 | 无法测量 ✓ |
| `tiny-sample` | 只留 2 次开口 | `T_SAMPLE_FLOOR` 不得判绿 | 无法测量 ✓ |
| `no-timebase` | 抹掉全部 `ts` | `T_SPURIOUS_TIMEBASE` 不得判绿 | 无法测量 ✓ |
| `always-silent` | 永远沉默的桩 | 计量判据**一条都不得判绿** | 全非绿 ✓ |
| `identity` | 什么都不改 | **一条都不判红** | 空 ✓ |

> ★ `always-silent` **没有** `must_fail`：它的产物是「没有开口样本」⇒ 各量全部不可测。
> 声明它必须判红会让自检**假失败**，进而诱使人放宽判据 —— 那比漏检更危险。
> 真正该断言的是「它一条都没判绿」。

**★★ 对抗性复核推翻并修掉的一处真洞（本票最值钱的产出）**

初版的 ★ 负控**是一个声明，不是一道防线**：耗时出处读的是调用方传进来的字符串
（`latency_source=...`），与数据无关。复核把 `latency_ms` **真的**换成由事件 `ts`
差值算出来的数、**声明一个字都不改**，卡片照样报

```
onset median = 480.0 ms（看着完全正常的链路耗时）
provenance.source = decision_chain_round_stamp   legal = True
T_LATENCY_SOURCE = pass      >>> CARD VERDICT = pass
```

—— **正是本票正文点名的「静默出数」，而当时的实现恰好复现了它。**

修法：出处改为**从数据取证**（`latency_audit`），三条独立证据：

1. **逐行 `latency_source` 字段**（写入侧 #158 新增，`live_llm` 只在真记到耗时时才写）；
2. ★ **代数检验**：若耗时由 `ts` 推出，它**必然恰好等于某个 ts 差值** ——
   命中即判该行不可信。这不是统计检验（样本太小），是**恒等式**；
3. 缺耗时/缺出处 ⇒ 记 `unattributed` 并判红。

同一个 forged 输入现在：`legal=False`、19 行被点名 `ts_derived_ids`、
`T_LATENCY_SOURCE=fail`、**卡片判红**。两条回归测试钉住它
（`test_latency_provenance_is_audited_from_data_not_declared` +
对照测试证明伪造值**看起来完全正常**，故只有代数检验能抓住）。

> ★ **为什么这一点值得单列**：本票的主题就是「造一把不骗人的尺子」。
> 一把**由被测量者自己声明出处**的尺子，与被测量者直接写分数没有区别。
> 上一轮（#157）同一位复核专家也推翻过一个刚加的守卫 —— 这是同一个形态第二次出现。

**★ 三条核心纪律（每条都由判据承载，不是文档承诺）**：

1. **耗时只来自决策链路自身的轮次打点**（`latency_ms`，源自 #156 的
   `live_mode._turn_started_at`），且出处**从数据取证**。帧的 `ts_ms` 是**采集端墙钟**、
   事件 `ts` 是**发射端墙钟** —— 用它们会把网络与编码抖动误归因成「模型反应慢」。
   ★ 同一个 `ts` 字段在「速率分母」上**合法**、在「耗时」上**非法**：用途写死在函数名里。
2. **删掉打点判红**（不是「无法测量」）—— 判「无法测量」会把接线缺陷说成「这项不适用」。
   ★ 与之配对：**一次开口都没有**判「无法测量」（无适用对象，不是缺陷）。
   两条合起来才有分辨力：只有前者会漏掉真缺陷，只有后者会误报。
3. **两轴不合成** —— 合并分数键扫描可判红。

**★ /code-review 两轴各查出真缺陷（本票的第二组收获）**

**Spec 轴（★ 上面那处真洞）**：★ 负控是声明而非防线；另查出 `--asset` 分支调用了
**不存在的函数**（`load_timing_asset`），CI 抓不到是因为 import 在分支里、而 `main()`
当时没有测试 —— **一个存在但从不执行的代码路径，与没有它无法区分**。

**Standards 轴**：

- **`criteria` 1004 行**越过 §7「problem」线 ⇒ 拆出 `decision_eval_timing_negatives.py`；
- **分叉守卫是空的**：只扫 `%`，而两条带阈值的判据都把阈值写成 **ms** ⇒
  正则永不命中、`context_numbers` 从未被赋值。改成**单位无关**后，
  守卫**立刻**抓出两处真实未声明数字（「4 例」与分位数名里的「90」）⇒ 已逐个显式声明；
- **四处重复实现**（复核逐条列出）：`decision_of` / `_pct` 声称「与定向轴同一实现」
  实为平行实现；`VERDICT_*` 词汇重抄；总判定规则**三份**；`timing_caveats` 是
  **从未被调用**的纯转发 ⇒ 全部改为 `import` 唯一一份（`combine_verdicts` 落到
  `decision_eval_criteria`），`timing_caveats` 删除；
- **不实措辞**：`BASELINE_EVENTS` 注释写「权威绑定靠 sha256」而**全仓没有该 sha256**，
  且 `logs/` 不入库 ⇒ 改为如实区分「**阈值可复算，来源文件不可核验**」。

**拆模块（评审 + §7 触发）**：`criteria` 初版 1004 行越过「problem」线 ⇒
负控与自检拆到 `decision_eval_timing_negatives.py`，使「判据定义」与「可证伪性自检」
各自只有一个变化原因。

**★ 写入侧接线（#158 新增，跨服务）**：

| 落点 | 内容 |
|---|---|
| `live_llm._record_live_decision` | 事件 `extra.latency_source` —— **只在真记到耗时时才写**（没有耗时却声明出处＝制造证据） |
| `decision_events.Round` | 逐行读回 `latency_source`（缺即 `None`，不给默认值） |
| `services/webui/tests/test_latency_source_contract.py` | ★ 钉住**跨服务的常量一致性**：写侧与读侧各有一份字符串（两服务不能互相 import），分叉是静默且单向的 —— 写侧一直打标、读侧把每一轮判成「不可归因」，症状看着像「埋点坏了」 |

**验证结果（2026-09-23）**：

| 项 | 结果 |
|---|---|
| webinfer 全量 | **810 passed**（#157 时 804 → 本票新增 6 个模块的测试；全票从 722 起算 +88） |
| webui 全量 | **937 passed, 1 skipped**（含新增跨服务契约 5 条） |
| memory-store 全量 | 75 passed, 8 skipped（无回归） |
| ruff check + format --check | **All checks passed** / 92 files already formatted（按 CI 的 `--extend-ignore` 口径） |
| 卡片自检 | **PASS**：轴层 / 判据层 / 卡片层三层全绿；10 个负控 9 killed + 1 恒等干净 |
| 阈值核验 | **2/2 一致**（从冻结快照复算） |
| ★ 复核证伪复现 | **已闭合**：声明不动、耗时由 ts 差值伪造 ⇒ `legal=False`、19 行被点名、**卡片判红** |
| 行尾核验 | `--numstat` 与 `--ignore-cr-at-eol` **一致**（无整文件行尾改写） |

### 2026-09-23（★ 定向轴记分卡：把「该不该开口判断得对不对」变成一条命令；工单 #157）

> **这是父 spec #154 的第 2 片纵切**，也是欠账里最核心的那个数字。
> **前置**：#155（冻结资产）与 #165（多轮取中位）均已交付 ⇒ 本票可开工。
>
> **一句话结论**：**两个生产 variant 在原判据下「达标」的东西，在宽口径判据下判红** ——
> 泛化子集 nfm 召回 **0% / 11.1%**，低于阈值 17%。而旧字段
> `directed_miss_rate_pct` 在同一批数据上**恒为 0.0%**（它只数 `→not-for-me`）。
>
> ★ **实测证实了工单正文对旧判据的否决**：`not-for-me precision >= 80%` 在这批真机上
> 报 P2 = **100%** / P = **0.0%（分母退化）**，看起来「达标」；而把**真正会出声**的
> 路径（`response` ∪ `delegation`）算进来后，非面向句误响应率是 **34.6% / 50.0%**。

**装置**（六个模块，按「一个模块一个变化原因」拆分 —— `coding-standards.md` §7）：

| 模块 | 职责 | 行数 |
|---|---|---|
| `decision_eval_axis.py` | 怎么算：宽窄口径 + 代价加权 + token 证据 + 跨轮聚合 | 717 ⚠️ |
| `decision_eval_criteria.py` | 怎么判：判据 + 负控 + 冻结基线快照 | 946 ⚠️ |
| `decision_eval_sources.py` | 从哪读：产物 → 逐轮行（卡片与阈值核验共用） | 113 |
| `decision_eval_synthetic.py` | 离线夹具（三处共用同一份，不各自造） | 90 |
| `decision_eval_bounds.py` | 阈值出处核验 + 漂移报告 | 188 |
| `decision_eval_report.py` | 怎么印 / 怎么 diff | 257 |
| `decision_eval_card.py` | **组装成一张卡 + CLI**（公开入口，再导出下层符号） | 704 |

全部在 **CI 的 pytest 矩阵内**（放 `services/scripts/` 会永不被收集 —— #152 的形态）。

> ⚠️ `axis` 与 `criteria` 仍在 §7 的 600 行「smell」之上（但已低于 1000 行「problem」线）。
> 两者的大头是**判据与负控的说明性文本**（每条判据都带出处与「为什么这么定」），
> 这些文字是交付物的一部分（供后人复核判据是否仍有效），压缩它们等于删证据。
> 若继续增长，按「判据定义 / 负控 / 结构性守卫」再拆。</

**命令**：`cd services/webinfer && python -m decision_eval_card`　**退出码**：`1`
（两个 variant 的 `D4` 判红 ⇒ 判定 FAIL 映射为 1；**「无法测量」映射为 2，永不返回 0**）

| 判据 | 指标 | 阈值 | `P`（裸 prompt） | `P2`（带 persona） | 判定 |
|---|---|---|---|---|---|
| **D1** | 非面向→开口率（**含 delegation**） | ≤63% | **50.0%** | **34.6%** | pass |
| **D2** | 面向句非响应率（**含被 silence 吞掉**） | ≤21% | 12.0% | 12.0% | pass |
| **D3** | nfm 精确率（**非退化守卫**） | ≥70% | **无法测量**（分母 `[1,0,0]`） | 100.0% | pass/unmeasurable |
| **D4** | **泛化子集** nfm 召回 | ≥17% | **0.0%** | **11.1%** | ★ **fail** |
| **D5** | 代价加权（3×误响应 + 1×漏判） | ≤101 | 82.4 | 62.7 | pass |
| S1–S5 | 分母 / 失败行 / token 证据 / 可归因 / ≥2 轮 | 结构性 | 全 pass | 全 pass | — |

> ★ **宽窄口径的差额就是本票的证据**：D1 把 `delegation` 算作开口、D2 把被 `</silence>`
> 吞掉的面向句算作漏判。旧字段（`baseline_mis_response_rate_pct` 只数 `response`、
> `directed_miss_rate_pct` 只数 `→not-for-me`）在**同一批数据**上给出的是 0.0% ——
> 差的不是小数点，是「真正会出声的那一半」。
>
> ⚠️ **`cost_index` 单独挡不住「永远沉默」**（实测，写下来以免后人误信）：
> 平凡沉默桩的加权代价是 **49.0**，而生产 prompt 是 **82.4** —— 在 25 面向 / 26 非面向
> 的基率下 `C_FN×25 < 3×FP + 1×FN`。⇒ 挡住沉默策略的是 **D2/D4 两条召回下限**。
> 卡片里的 `cost_index_always_silent` / `cost_index_always_speaking` 就是为此提供的参照。

**★ 阈值不随产物漂移（一条被实测逼出来的修正）**

初版让阈值**每次从当前入库产物重算**。看起来「阈值与证据绑在一起」很严谨，实则**循环**：
门禁要挡的是质量退化，而退化若伴随一次产物重跑（一次真机 3 轮就会重写该文件），
阈值会**跟着退化一起动** —— 那条线于是永远拦不住东西。**这正是 fail-open 且看起来在工作。**
⇒ 现改为**冻结基线快照**（`BASELINE_SNAPSHOT`，含产物 `sha256` 与逐轮序列），
`--verify-bounds` 绑快照；产物重跑由 `bound_drift_report()` **报出来**（不报错，但可见）。

**★ 非退化守卫必须看「每轮是否非零」，不能只看总和**

真机产物里 `not_for_me_predicted` 三轮是 **`[1, 0, 0]`** —— 总和为 1（非零），
于是「总和式」守卫让**一轮里的一例**换来的 100% 精确率**判绿**。
那与「precision 100% 而分母只有 3 例」是同一种缺陷，只是换了个地方发生。
⇒ 判据读 `n_rounds_nonzero`（本卡片据此把 `P` 的 D3 判为**无法测量**）。

**★ 可证伪性（AC 的 ★★ 两条）**

**命令**：`python -m decision_eval_criteria --self-check`　**退出码**：`0`

```
identity                 判红=[]（应为空）
always-silence           判红=['D2-directed-nonresponse', 'D4-not-for-me-recall-generalization']
always-not-for-me        判红=['D2-directed-nonresponse', 'D3-not-for-me-precision']
always-response          判红=['D1-...', 'D4-...', 'D5-cost-index']
always-delegation        判红=['D1-...', 'D4-...', 'D5-cost-index']
inverted-labels          判红=[全部五条指标判据]
no-token-evidence        判红=['S3-token-evidence-complete']
quiet-without-tokens     判红=['S4-no-unattributed-quiet']
inverted-token-evidence  判红=['S4-no-unattributed-quiet']
failed-round             判红=['D2-...', 'D4-...', 'S2-no-round-errors']
single-round             判红=['S5-multi-round']
empty-nondirected-denominator 判红=['S1-denominators-present']
empty-directed-denominator    判红=['S1-denominators-present']
verdict: PASS（卡片全部 10 条判据各有负控；13 个故意做错的输入全部按声明判红或「无法测量」）
```

① **★ 负控：「永远输出沉默」的桩必须判红** —— 旧判据下它 `precision=100%`、
`directed_miss=0%`，**无条件通过**；本卡片下它在 D2/D4 上判红。
② **★ 恒真式判据能被抓到** —— `test_self_check_is_falsifiable_by_a_broken_criterion`
把 `Criterion.evaluate` 换成恒 PASS 的桩，自检**必须转红**（证明自检不是装饰）。
③ **恒等对照**：`identity` 负控必须让**任何判据都不变红**（证明「变红」不是来自基线输入本身）。

| 项 | 命令 | 结果 | 真机? | 测量时间 |
|---|---|---|---|---|
| 真机 3 轮（重跑，带 token 级证据） | `BENCH_ROUNDS=3 BENCH_PROD_OUT=…rounds.json python services/scripts/benchmark_production_live_prompt.py` | 退出码 0；产物含 `per_round_rows[].n_tokens`/`first_token_id`（**新增**，见下） | **真机**（7060 在位） | 2026-09-23T13:0x |
| 一条命令出卡 | `cd services/webinfer && python -m decision_eval_card` | 两个 variant **判定 FAIL**（D4 泛化召回 0.0% / 11.1%）；exit **1** | 离线（读入库产物） | 2026-09-23T13:2x |
| 阈值核验 | `python -m decision_eval_card --verify-bounds` | **5/5 一致**（`declared == derive(snapshot)`）；产物 sha 仍等于快照绑定值 | 离线 | 2026-09-23T13:2x |
| 判据负控自检 | `python -m decision_eval_criteria --self-check` | **PASS**，13 个负控全部按声明判红 | 离线 | 2026-09-23T13:2x |
| 卡片自检（含单轮判红 + 永远沉默桩） | `python -m decision_eval_card --self-check` | **PASS** | 离线 | 2026-09-23T13:2x |
| 定向轴行为测试 | `python -m pytest tests/test_decision_eval_axis.py -q` | **35 passed** | 离线 | 2026-09-23T13:5x |
| 判据/负控行为测试 | `python -m pytest tests/test_decision_eval_criteria.py -q` | **48 passed**（含评审后补的 10 条） | 离线 | 2026-09-23T13:5x |
| 卡片行为测试 | `python -m pytest tests/test_decision_eval_card.py -q` | **28 passed** | 离线 | 2026-09-23T13:2x |
| webinfer 全量 | `python -m pytest -o asyncio_mode=auto -q` | **717 passed**（#165 时 602 → 本轮 **+115**） | 离线 | 2026-09-23T13:5x |
| ruff（CI 门禁同款） | `ruff check services/webinfer --extend-ignore D101,…` + `ruff format --check` | **All checks passed** / 82 files already formatted | 离线 | 2026-09-23T13:5x |
| 行尾核验 | `git diff --numstat` 对比 `--ignore-cr-at-eol` | 一**致**（产物写入显式 `newline="\n"`） | 离线 | 2026-09-23T13:2x |

**★ 产物必须带 token 级证据（否则 AC 在数据上不可能成立）**

`per_round_rows` 的投影新增 `n_tokens` + `first_token_id` 两个字段。
没有它们，**入库产物在读侧不可判**「模型判定沉默」（`</silence>` 是 special token，
被服务端从 content 剥离后 content 也是空串）与「模型什么都没输出」——
而 #157 的核心 AC 正落在这一条上。产物只增约 10 字节/行（全轮约 +4 KB）。

> ⚠️ 未产出 token 列表时投影写 **`None`（无证据）而不是 `0`（零输出）**：
> 两者含义相反（「没采集到」vs「模型什么都没吐」），混起来正是本工单要消除的混淆。
> 有专门的负控测试钉住这一点（`test_round_projection_reports_missing_token_evidence_as_none_not_zero`）。

**★ `/code-review` 两轴各查出真缺陷（本轮全部修掉）**

**Standards 轴**（2 处真缺陷）：
① **两条恒真断言** —— `assert ... or True`（`test_decision_eval_criteria.py`、
`test_decision_eval_card.py` 各一处）。★ 在一份以「可证伪」为主题的改动里写恒真断言，
正是本工单要消灭的病；已换成真正的不变式，并为原先没被执行的「产物漂移」分支
补了一个**篡改产物**的负控。
② **模块越线**：`decision_eval_card.py` 1172 行 > §7 的 1000 行「problem」线 ⇒ 按职责拆成
六个模块（见上表，最大 703 行）。

**Spec 轴**（3 处真缺陷，拿真反例证出来的）：
① **HIGH —— 阈值的「发表版」与「执行版」分叉**：`Criterion.statement` 里写死了
「54%」「27%」「19%」「≤93」，而 `threshold` 是 63/21/17/101。`statement` 会随卡片
落进 JSON，**正是门禁作者会照抄的那句话**，而 `--verify-bounds` 只守 `threshold`。
⇒ 改为 f-string 从 `BOUNDS` 插值 + `statements_match_bounds()` + 配套负控。
② **MEDIUM —— `not-for-me` 的 token 证据从未被校验**：把每一行 `not-for-me` 的 token
换成 `[151669]`（模型其实吐的是沉默）**不触发任何判据**。⇒ 补上该侧矛盾检查 + 负控。
③ **LOW —— `structural_checks({})` 的 S3 fail-open**：覆盖块**整体缺失**时
`.get()` 返回 `None`、`not None` 为真 ⇒ **判绿**。⇒ 改三值（缺块/无对象 ⇒ 无法测量）。

评审同时**证伪失败**的三条声明（即它们成立）：永远沉默的桩确实判红且 D3 判「无法测量」；
负控非装饰（恒等对照 + `must_not_pass` + 覆盖完整性都是机器强制的，评审未能造出装饰性负控）；
`BOUNDS` 本身确实 sha 绑定、5/5 重算一致。

**★ 本轮修的四类缺陷（全部由**自检/测试**发现，不是我先想对的）**

| 缺陷 | 怎么暴露的 | 修法 |
|---|---|---|
| **阈值随产物漂移（循环）** | 重跑真机产物后 `--verify-bounds` 立刻报 5/5 不一致 —— 而「不一致」的正确解读不是「产物错了」，是**阈值设计错了** | 改为冻结基线快照（绑产物 sha256）；产物的偏离由 `bound_drift_report()` 报出 |
| **非退化守卫只看总和** | 真机产物形状是 `[1,0,0]`：总和非零 ⇒ 一例换来的 100% 精确率判绿 | 守卫改读 `n_rounds_nonzero`，要求**每轮**非零 |
| **S5 有一条永远不可达的分支** | 自检报「恒等对照让 S1 判红」时顺着查到：聚合层早已 `raise`，那条「判不可测量」的分支根本执行不到 | 删除该分支，只留可达的「轮数 < 2」；并写下「声称处理了却永不执行的分支比没有更坏」 |
| **两条分母负控互相搞反** | 自检报「`empty-nondirected-denominator` 让 D1 判绿」—— 名为「清空非面向分母」的负控实际删掉了**面向**句（`_keep_only` 名字与语义相反） | 改名 `_drop_group`，名字与语义对齐 |
| **两条 `F401`/`F821`** | `ruff check`：删错了仍被引用的 import，导致 `EVIDENCE_NOT_QUIET`、`AXIS_METRIC_KEYS` 未定义 | 补回并跑完整门禁 |

> ★ **`cost_index` 只在「非不开口」的行上有效**：它在 `P` 上是 82.4 而平凡沉默桩是 49.0 ——
> 若只读这一个数字会得出「沉默更好」的错误结论。故卡片**并列**两个平凡桩读数，
> 并由 D2/D4 承担挡沉默的职责。

---

### 2026-09-22（★ 多轮取中位 + 离散度：把「单轮结论」升级为可判稳的读数；工单 #165）

> **这是欠账清单 §4 #3 的那一条**（「❌ 未做。存量只有 2 轮」），也是 **#157 的硬前置**
> （#157 的验收明写「多轮取中位并记录轮数与离散度」）。
>
> **前置**：llama-server (7060) 在位（`/v1/models` 200，模型 `joyai-vl-interaction-preview`）；
> 本项**只需 7060**，不需要其余 5 个服务、不需要浏览器（是**离线可复算的模型层评测**，
> 不是端到端链路）。执行解释器 `D:\AI\envs\joyai-main\python.exe`（3.12.13）。
>
> **本轮结论一句话**：**中位数确实稳住了结论，而离散度证明单轮结论本来就不可信** ——
> 单轮之间的 stdev 达 **4.8–6.5 个百分点**，而「不稳定句」在三轮里有 **33 句**
> （共 56 句）**决策不一致**。⇒ 此前所有单轮得分都应视为**该分布的一次抽样**，不是能力的点估计。
>
> ⚠️ **注**：本节数字取自入库产物
> `doc/research/data/benchmark_production_live_prompt_rounds.json`（**同一次**真机跑的读数）。
> 本票全程跑了**三次**真机 3 轮，各次绝对数字**明显不同**（`P` 的误响应率中位
> 38.5 → 26.9 → 46.2）—— 这**正是本票要证明的那件事**：单轮数字不可作为点估计。
> 故引用时必须认准是哪一次（本文引用的是入库文件的那一次）。

**★ 真机三轮（每 variant × 3 轮 × 56 例 = 168 次推理，两个 variant 共 336 次）**

**命令**：`BENCH_ROUNDS=3 BENCH_PROD_OUT=doc/research/data/benchmark_production_live_prompt_rounds.json \
  python services/scripts/benchmark_production_live_prompt.py`　**退出码**：`0`

| metric | variant | median | 单轮值(首轮) | **stdev** | range | per_round |
|---|---|---|---|---|---|---|
| 误响应率 | `P_live4_prod_prompt`（裸 prompt） | **46.2** | 46.2 | **5.421** | 11.5 | [46.2, 57.7, 46.2] |
| 误响应率 | `P2_…_profile`（带 persona） | **23.1** | 19.2 | **6.537** | 15.4 | [19.2, 23.1, 34.6] |
| nfm 精确率 | `P` | **0.0** ⚠️ | 0.0 | 0.0 ⚠️ | 0.0 | [0.0, 0.0, 0.0] ⚠️ |
| nfm 精确率 | `P2` | **100.0** | 100.0 | 0.0 | 0.0 | [100.0, 100.0, 100.0] |
| nfm 召回率 | `P` | **0.0** | 0.0 | 0.0 | 0.0 | [0.0, 0.0, 0.0] |
| nfm 召回率 | `P2` | **19.2** | 19.2 | **4.784** | 11.5 | [19.2, 26.9, 15.4] |
| 面向句漏判率 | 两者 | **0.0** | 0.0 | 0.0 | 0.0 | 三轮全 0 |
| delegate 召回 | `P` | **100.0** | 100.0 | 0.0 | 0.0 | [100, 100, 100] |
| delegate 召回 | `P2` | **100.0** | 100.0 | 0.0 | 0.0 | [100, 100, 100] |

**★ 逐句稳定性（本票把「12–14 例不一致」变成字段的那一项）**

| variant | stable | **unstable** | 按组 | 成本 |
|---|---|---|---|---|
| `P_live4_prod_prompt` | 23 | **33** | directed 15 / **nondirected 18** / delegate 0 | 91.36 s / 168 calls → **0.544 s/call** |
| `P2_…_profile` | 23 | **33** | directed 12 / **nondirected 21** / delegate 0 | 80.52 s / 168 calls → **0.479 s/call** |

⇒ **56 句里有 33 句（两 variant 皆然）跨三轮决策不一致**。非面向组（26 句）里 18–21 句不稳定 ——
与工单正文「26 例非面向中 **12–14** 例两轮不一致」**同量级且更严重**（三轮自然更多机会不一致）。

> ⚠️ **`P` 的 nfm 精确率整列 0.0 不是「测了但全错」，是三轮全部分母退化。**
> 三轮**一条 not-for-me 都没预测**（`n_not_for_me_predicted=0`，见 `degenerate_rounds=[1,2,3]`），
> 而 `summarize` 在分母为 0 时把比率记作 **0.0** —— 与「预测了但全错」的 0.0
> **数值相同、含义相反**。本轮据此在结果结构里加了 `metrics_note.degenerate_rounds`
> 显式点名退化轮（见下「本轮修的三处」）。**读这张表时勿把该列 0.0 当成「委派/精确率全错」。**
>
> ★ 顺便印证本票的立论：**同一配置三次真机跑，`P` 的误响应率中位依次为 38.5 → 26.9 → 46.2**
> （本节引用的都是各自那一轮入库文件的读数）。单轮样本落到哪个数**全看运气** ——
> 这正是「单轮不可作点估计」最直白的证据。

**★ 同口径（AC#4：历史 25 vs 现 26 的差异必须显式处理）**

| 口径 | 句数（directed/nondirected/delegate） | `P` 误响应率 | `P2` 误响应率 | `P` nfm召回 | `P2` nfm召回 |
|---|---|---|---|---|---|
| **本资产（权威）** | 25 / **26** / 5（共 56） | 46.2%（中位，本页上表） | 23.1%（中位，本页上表） | 0.0% ⚠️ | 19.2% |
| **同口径历史可比**（排 `N01b` + 整组 delegate） | **25 / 25 / 0**（共 50） | 44.0% | 36.0% | 0.0% | 12.0% |

⇒ 两行的**分母不同（56 vs 50）**，这正是 #155 记录的「25 vs 26」在整组 delegate 加入后的完整形态；
⇒ 跨这两行比较**无效**，必须先统一分母 —— `denominator.historical_comparable`
把该口径连同「为什么」一起写进结果文件，于是这件事不再靠人记得。
（上表「本资产」两列取自本轮 3 轮读数；「同口径」两列取自 `historical_comparable_stats`，
即**末轮**在排除 `N01b` 与 delegate 后重算的值 —— 与历史文件同分母，故可比。）

**★ 开卷标注（AC#7）**：两 variant 均为 **开卷考**，与生产 prompt 逐字重叠 **10 句**（非面向 8 句）。

| variant | generalization（n=46） | open-book（n=10） |
|---|---|---|
| `P`（裸 prompt） | 误响应 **50.0%** / nfm_recall **0.0%** | 误响应 37.5% / nfm_recall 0.0% |
| `P2`（带 persona） | 误响应 **33.3%** / nfm_recall **11.1%** | 误响应 37.5% / nfm_recall **25.0%** |

⇒ 泛化子集 nfm_recall **0–11.1%**，#155 记录的是「扣掉记忆效应后泛化 recall 仅 0–5.6%」，
两者**同一量级**（本次三轮抽样里 `P2` 偏高一点，而单轮抽样本就落在 0–11% 之间波动）。
⚠️ 这两列取自**末轮**（单轮视图），而末轮本身是抽样的 ——
上表的 median/stdev 才是本票的结论面；**引用泛化分请连轮数一起引**。

**★ 存量历史 2 轮：离线重聚合（不跑模型，AC#4 的可执行形态）**

**命令**（★ 须**先 cd 进 `services/webinfer`** —— 该模块是那里的顶层模块，
从仓库根直接跑会 `No module named decision_eval_rounds`）：

```bash
cd services/webinfer && python -m decision_eval_rounds --from-results \
  ../../doc/research/data/benchmark_production_live_prompt_results.json \
  ../../doc/research/data/benchmark_production_live_prompt_results_repeat.json --variant <V>
```
（装置在 `services/webinfer/decision_eval_rounds.py`，CI 可见、有单测）

| variant | rounds | unstable | 按组 | 退化轮 |
|---|---|---|---|---|
| `P_live4_prod_prompt` | 2 | 20 | directed 8 / **nondirected 12** / delegate 0 | 精确率 + delegate 召回（分母皆 0） |
| `P2_…_profile` | 2 | 19 | directed 5 / **nondirected 14** / delegate 0 | delegate 召回（分母 0） |

⇒ **工单正文那句「12–14 例两轮不一致」由这两个数字逐字复现（12 与 14）** ——
本票把它从工单散文变成了**一条可重跑命令的输出**。
⇒ 同时证明「存量只有 2 轮」的账**没有被浪费**：不重跑模型即可得到中位与离散度。

**★ 负控（AC#5：离散度必须真的在度量离散）**

**命令**：`python -m decision_eval_rounds --self-check`（离线，无需模型）　**退出码**：`0`

```
stable not_for_me_recall_pct: per_round=[100.0, 100.0, 100.0]  median=100.0  stdev=0.0    range=0.0
stub   not_for_me_recall_pct: per_round=[100.0, 100.0, 0.0]    median=100.0  stdev=47.14  range=100.0
verdict: PASS
```

高方差桩（第 3 轮非面向句全判误响应）⇒ stdev **0.0 → 47.14**、range **0.0 → 100.0**。
**且中位数岿然不动（100.0）** —— 这正是选它而非均值的理由：单轮离群不带走结论，
但读者**必须**能从离散度看到它抖过。自检本身可证伪（把 `_dispersion_stdev` 换成恒 0 的桩 ⇒ 判红）。

**★ 本轮修的四类缺陷**

| 缺陷 | 怎么暴露的 | 修法 |
|---|---|---|
| **写盘时崩：`KeyError: 'cost'`** —— 结果块组装内联在 `main()`，重构改了键名而读取方读旧键 | 首次真机轮**跑完 336 次推理后**崩溃，**结果文件一个字节都没写**（≈7 分钟白跑）。根因是结构性的：`services/scripts` **既不在 CI 的 pytest 矩阵、也不在 CI 的 ruff 范围内** ⇒ 那里的键名不匹配**只能**等真机跑完才暴露 | 把组装抽成**纯函数** `variant_result_payload`；在 **CI 可见处**加 `tests/test_benchmark_multiround_contract.py`（静态 AST 扫描 `main()` 的下标读取，**含嵌套层**，附两层负控），同类错误从此**离线秒级**转红 |
| **退化分母被读成「全错」** —— 分母为 0 时 `summarize` 记 0.0 | 真机上 `P` 的 nfm 精确率出现 stdev 47.14（看着像剧烈抖动）；存量历史文件里 `delegate_recall_pct=0.0`（看着像委派全失败，实际是**当年没测**） | 加 `RATIO_DENOMINATORS` 逐条登记比率→分母，聚合时产出 `metrics_note.degenerate_rounds` 点名退化轮 |
| **落盘产物不自足** —— 只存末轮的 `rows` | **自查**（非真机）：用新产物跑 `--from-results` 会被拒「不足 2 轮」，尽管文件里明明有三轮 ⇒ 重新分析就得**再花 3 分钟跑模型** | 产物加 `per_round_rows`（逐轮决策，已裁到重聚合所需的 4 个字段，避免把产物撑到 484 KB）；`report_from_results_files` 兼容「一文件一轮」与「一文件 N 轮」两种形状 |
| **★ `/code-review` 两轴查出的 6 处**（下列为其中真正是缺陷的） | 独立评审（Standards + Spec 两轴） | ① **CRLF 污染**：`Path.write_text` 在 Windows 默认把 `\n` 写成 `\r\n`，两个新文件与产物成了 CRLF（`git diff --numstat` 与 `--ignore-cr-at-eol` 不一致 = AGENTS.md 定义的「整文件行尾被改写」）⇒ 产物写入显式 `newline="\n"`，并把两个文件归回 LF；② **`--from-results` 声称「0.0 秒/轮」**：从没测过耗时的文件里读出「测到了 0」⇒ 改 `None` + 明说「未测」；③ **`BENCH_ROUNDS=1` 被静默改成 2** ⇒ 改为**报错**；④ **`RATIO_DENOMINATORS` 守卫恒真**（变异测试：3/5 条比率的分母改错仍全绿）⇒ 加独立金标 + 行为正/负控，**三个变异体现已被杀死**；⑤ **静态扫描漏嵌套层**（`["rounds_report"]["case_stabilty"]` 扫不出）⇒ 改为递归处理嵌套路径；⑥ **两处自述不实**（模块 docstring 称两个 benchmark 都已接线，实际只接了一个；`rounds_semantics` 指向不存在的 `results[<v>].rounds`）⇒ 逐条改正 |

> 前两类属本仓最贵的那一类（**静默**：一个让证据丢失、一个让读数反向），
> 且**只有真机会暴露** —— 离线单测在设计时全是绿的。
> 第四类说明**离线全绿也不够**：评审用变异测试证明「看着在守、其实恒真」的守卫有三处。

| 项 | 命令 | 结果 | 真机? | 测量时间 |
|---|---|---|---|---|
| 真机 3 轮 × 2 variant（336 次推理） | `BENCH_ROUNDS=3 BENCH_PROD_OUT=…rounds.json python services/scripts/benchmark_production_live_prompt.py` | 退出码 0；结果落 `doc/research/data/benchmark_production_live_prompt_rounds.json`（含 `rounds_report` 多轮块 + `per_round_rows` 逐轮决策） | **真机**（7060 在位） | 2026-09-22T13:4x |
| 产物自足性（不重跑模型即可重新分析） | `cd services/webinfer && python -m decision_eval_rounds --from-results ../../doc/research/data/benchmark_production_live_prompt_rounds.json --variant P_live4_prod_prompt` | **读出 3 轮**，median/stdev 与产物内的 `rounds_report` 逐项一致；成本栏如实报**未测**（产物外的重聚合没有耗时数据） | 离线（读入库产物） | 2026-09-22T13:5x |
| 负控自检（AC#5） | `python -m decision_eval_rounds --self-check` | **PASS**：stable stdev 0.0 → stub stdev **47.14**（range 0→100），中位不变 | 离线 | 2026-09-22T13:0x |
| 存量历史 2 轮重聚合 | `cd services/webinfer && python -m decision_eval_rounds --from-results …results.json …_repeat.json --variant <V>` | **P：nondirected 12 句不一致；P2：14 句** —— 逐字复现工单的 12–14 | 离线（读已落盘文件） | 2026-09-22T13:1x |
| **AC#3 两次运行 diff**（差异一眼可见） | `diff_reports(历史2轮, 本轮3轮)`（经 `--diff-against` 暴露） | **逐指标列出**：`rounds_completed 2 -> 3`、`mis_resp median 30.8 -> 23.1 \| stdev 0.0 -> 6.537`、`nfm_recall 13.45 -> 19.2`、`delegate_recall 0.0 -> 100.0`、**`case_ids_total 51 -> 56`**（连分母变化都点出来）。自比时明确输出「无差异」 | 离线 | 2026-09-22T13:5x |
| aggregator 行为测试 | `cd services/webinfer && python -m pytest tests/test_decision_eval_rounds.py -q` | **54 passed** | 离线 | 2026-09-22T13:5x |
| 契约测试（真机脚本的结果形状） | `cd services/webinfer && python -m pytest tests/test_benchmark_multiround_contract.py -q` | **13 passed**（含跨 CI 边界的静态 AST 扫描 + 两层负控 + 产物自足性端到端 + BENCH_ROUNDS 坏值判红） | 离线 | 2026-09-22T13:5x |
| webinfer 全量单测 | `cd services/webinfer && python -m pytest -o asyncio_mode=auto -q` | **602 passed**（#155 时 535 → 本轮 +67：54 例 aggregator + 13 例跨 CI 契约） | 离线 | 2026-09-22T13:5x |
| scripts 单测（#162/#163 的） | `python -m pytest scripts/tests/ -q` | **89 passed**（与本轮改动前一致，无回归） | 离线 | 2026-09-22T13:2x |
| ruff（CI 门禁同款） | `ruff check services/webinfer --extend-ignore D101,D102,D103,D205,D401,SIM105` + `ruff format --check services/webinfer` | **All checks passed** / **72 files already formatted** | 离线 | 2026-09-22T13:5x |
| **行尾核验**（AGENTS.md 字节核验） | `git ls-files --eol` + `git diff --numstat` 对比 `--ignore-cr-at-eol` | 全部 `i/lf w/lf`（评审查出产物与 2 个新文件曾被写成 CRLF，已修，见上表第四类①） | 离线 | 2026-09-22T13:5x |
| **变异测试**（守卫生效性，评审要求） | 逐个把 `RATIO_DENOMINATORS` 的分母改错（3 个曾**全绿通过**的变异体） | **3/3 已被杀死**，各由 `test_ratio_denominators_match_an_independent_oracle` / `…_zeroing_the_registered_denominator…` / `…_a_zero_denominator_does_not_flag_unrelated_ratios` 杀死 | 离线 | 2026-09-22T13:5x |

> **成本（AC#6，供后续调 N）**：三次真机 3 轮的单轮耗时 ≈ **25–32 s**、**0.473–0.544 s/次推理**。
> ⇒ `ROUNDS=3` 一轮完整评测 ≈ **3 分钟**；要提到 N=5 约 5 分钟 —— N 现在是有数字可依的，不是拍的。
> ⚠️ 从**已落盘文件**重新聚合时**没有**耗时数据，`cost` 会如实报 `measured: false` / `None`
> （**不是 0**）—— 评审查出这条曾是「从没测过的数据里读出测到了 0」，已修。
>
> **可复现性**：逐轮日志在 `logs/bench-rounds-165.log`、历史重聚合结果在
> `logs/rounds-165/`，而 `logs/` 被 gitignore（同 §1 #163 轮的惯例）。
> **结论不依赖那些文件**：真机三轮由上面的命令重跑；
> **入库产物本身即可重新分析**（`--from-results` 一条命令，见上表「产物自足性」行）；
> 历史两轮由 `--from-results` 对**入库的**两个 results 文件重算，命令与判据都在本页。

---

### 2026-09-22（★ 帧链路：判定「内容空」+ 建立正常基线；工单 #163）

> **这是欠账清单里「唯一从未真机测过」的一条**（§4 #1）。本轮把它从「只有间接证据」
> 推到**判定 + 基线 + 负控**三件事齐备。
> **前置**：`start-joyai.ps1 -Mode default` → 6/6 服务 200；ego 真浏览器已打开
> `http://127.0.0.1:8099/`；**真实 `getDisplayMedia` 采集在跑**（点应用页面
> 「视频 → 屏幕采集 → Start」，浏览器窗口选择器需**人在回路**授权）。
> **装置**：`scripts/frame_link_probe.mjs`（本轮新建）—— **只用应用自身 socket**
> （`window.websocket`），**只用真实帧**（取自应用自身采集管线），不合成帧、不自己开连接。

**★ 判定结论：「内容空」是 **读侧缺陷**，不是预期行为。**

| 判据 | 读数 | 出处 |
|---|---|---|
| 模型**真的产出**了内容 | 同图 image-only 直连 7060 → `"这张图片展示的是一个视频采集或屏幕捕获工具的界面…"`（64 token，`finish_reason=length`） | 本轮实测 |
| webinfer 的**决策契约**也正常 | image-only 请求 → HTTP 200，`streamingharness.decision="silence"`，`usage.completion_tokens=2` | 本轮实测 |
| webui 拿到的文本是**占位诊断串** | `metrics.user_prompt=""` 且回落成 `text="Empty model response: stop"` | 台账 §1 #13 + 本轮复现 |
| **决策契约在 `frame` 路径上无人消费** | `vlm_service.py` 全文**没有** `streamingharness` / `decision` 字样；而 live/jarvis 三条路径都读 `harness.get("decision")` | `vlm_service.py` vs `live_llm.py:365` / `live_proactive.py:297` / `jarvis_mode.py:1610` |

**因果链（机制层面确证）**：帧路径（`ws_handler.py` → `svc.process_frame` → `analyze_image`）
**不读** webinfer 的 `streamingharness.decision`；它只看 `choices[0].message.content`。
而 webinfer 把四态控制标记（`</silence>` 等）从 `content` 里剥掉、只留在
`streamingharness.raw_content` —— 于是「模型按契约选择沉默」这件事到达 webui 时
**只剩一个空字符串**，`_extract_response_text` 便回落成
`f"Empty model response{': ' + finish_reason}"` 这个**诊断串**（`vlm_service.py:629`）。
⇒ **决策语义在 webui 的 VLM 服务层丢失。**

> ⚠️ **踩过的措辞错误（必读，否则会误判严重度）**：本条最初写成
> 「**用户可见面**收到的是内部诊断文案」—— **那句话是错的**，见本节末尾
> 「★ 可见面实测」：诊断串在当前 HEAD 上**到不了用户可见面**。
> 准确表述是「**webui 的 VLM 服务层**丢失了决策语义、产出诊断串」，
> 至于它能否显示，是一个**独立的、已单独实测**的问题。
**依据取自决策态承诺**：`doc/subsystems/screen-capture.md` §3.5.5「视频框实时显示游戏画面
**同时** BT-7274 看到同一路画面，玩家喊『bt，这个怪怎么打』→ BT 回复攻略」
+ §4.3「1 fps 视频帧 → VLM 识别 → BT-7274『这个螳螂帮…』」；
`doc/specs/live-visual-cb.md` §1「用户说话时最近 1-N 帧作为视觉输入一起送 LLM → 模型
**看着画面回答**」。两处承诺的都是**有内容**的作答，**没有任何一处**把「无 prompt 时
回一句内部诊断串」写成预期行为。⇒ **判缺陷**，另立工单（本票不修，见 spec §2「不改被测对象」）。

**★ 可见面实测（2026-09-22 补做，独立于上面的判定）**：

上面判的是「**webui 的 VLM 服务层丢失了决策语义**」。它**是否显示给用户**是另一个问题，
单独实测（装置：`logs/frame-link/dom_visibility_probe.mjs`；**WS 通道与 DOM 通道分开记录**）：

| 实验 | 装置 | 读数 | 测量时间 |
|---|---|---|---|
| **A（真机）** | 真实 `getDisplayMedia` 1 fps 采集，50 帧 | **后端发了 39 条** `Empty model response: stop`；用户可见面（`document.body.innerText`）**50/50 次采样全为 false**；`#resultText` 长度恒 `281`（**DOM 一个字符都没动**，`inner_sig` 仅 1 个取值） | 2026-09-22T11:15Z |
| **C（受控正控）** | 同一装置，**唯一改动**：`isAnalysisRunning = true` | `body_has_target: false → **true**`、`#resultText 343 → 370`；还原后 `false` | 2026-09-22T11:03:58Z |
| **G（内部状态）** | 干净页 + 32 条响应（其中 30 条恰为诊断串） | `lastText` 长度**恒 `[0]`**、`vlmHistory` **从未新增条目** ⇒ 守卫下游的内部状态也从未被写入 | 2026-09-22T11:22Z |

**结论：诊断串在当前 HEAD 上到不了用户可见面。** 机制（静态 + 运行时双侧确证）：

* `ws_dispatcher.js:24` 是唯一分派点，第一条语句就是 `if (!isAnalysisRunning) return;`
* `#resultText` 的唯一写入路径 `updateResultText` **只被 `ws_dispatcher.js:43` 调用**，
  而该行在守卫**之后**（结构性证据）
* `isAnalysisRunning` 全仓库**只有一处置位**（`app_main.js:1192`），在其内部函数
  `showProcessedVideoStream`（`:1182`）里；**该函数零调用点**
  （静态 grep 排除 `.bak` + 运行时 call-trap 实测 `calls: 0`，覆盖动态调用）
* ⇒ `isAnalysisRunning` **结构性恒 `false`**，`vlm_response` 被无条件丢弃

**这使 #168 成为「死路径上的 latent 缺陷」，而非当前可见的缺陷。** 触发条件（已实测）：
只要有人把 `showProcessedVideoStream` 接回去或新增置位路径，实验 C-2 显示诊断串
**会立刻可见，并被送进 TTS 念出来**（`#ttsSpeakingText` 出现 348 字符）。
⇒ 降级而非关闭 —— 详见 #168。

> ⚠️ **本轮作废了一条此前被当作「决定性证据」的读数**（防后人继续引用）：
> 早先记录写「`getVlmDisplayText("Empty model response: stop")` → **原样返回该串**
> ⇒ 诊断串确实漏到用户可见面」。**该推理不成立，已作废。**
> 理由：`getVlmDisplayText` 是**纯函数**（`vlm_render.js:20`），**跳过守卫**直接调用它
> 当然原样返回 —— 这与该串**是否真的会流经它**无关。**这是「装置与真实路径不同构」
> 的又一实例**（同 §1 更正里「自建 WebSocket 收不到 vlm_response」那类错误）。
> 实测反证：30 条真实诊断串到达时 `lastText` 恒 `''` ⇒ 该函数在真实路径上
> **从未被以该串调用**。（函数自身行为对；错的是**推理**。）

**四轮结果（命令 / 结果 / 真机 / 时间）**：

| 轮 | 命令 | 结果 | 真机? | 测量时间 |
|---|---|---|---|---|
| A：无 prompt（复现「内容空」） | `node scripts/frame_link_probe.mjs --reset-session --observe-ms 30000` | `round-A-noprompt.json`：`verdict=FAIL`，`problems=[缺少 metrics.api_call_ms]`（**当时判据读错层级**，见下 ⚠️①）；读数 `text="Empty model response: stop"` / `user_prompt=""` / `api_call_ms=420.87` / `total_inferences=5` | 真机 | 2026-09-22T09:40:34Z |
| A2：同 A，重跑确证（离线帧） | `node scripts/frame_link_probe.mjs --frame-file logs/frame-link/real-frame-163.jpg --reset-session --observe-ms 25000` | `round-A2-reconfirm.json`：**`verdict=PASS`、`problems=[]`**，读数 `text="Empty model response: stop"` / `user_prompt=""` / `api_call_ms=953.32` / `total_inferences=1` | 真机 | 2026-09-22T09:52:11Z |
| **B：正常基线**（同帧 + 应用自身 `update_prompt`） | `node scripts/frame_link_probe.mjs --reset-session --observe-ms 40000 --prompt "请描述当前画面内容，一句话。" --require-content` | `round-B-withprompt.json`：`verdict=PASS`，`text="用户打开了浏览器，正在查看一个包含多个代码窗口和设置选项的网页界面。"` / `user_prompt="请描述当前画面内容，一句话。"` / `api_call_ms=729.74` / `total_ms=731.30` | 真机 | 2026-09-22T09:44:46Z |
| **C：负控**（停 8070，`--expect-no-response` 档） | `node scripts/frame_link_probe.mjs --observe-ms 25000 --expect-no-response` | `round-C-negative.json`：**`verdict=PASS`（负控成立 = 未收到配对响应）**；该档 `session_reset` 回 `ECONNREFUSED 127.0.0.1:8070`（8070 确已停） | 真机 | 2026-09-22T09:46:42Z |
| **C2：负控**（停 8070，**正常判据**——AC5 的字面要求） | `node scripts/frame_link_probe.mjs --frame-file … --observe-ms 20000` | `round-C2-negative-normal-mode.json`：**`verdict=FAIL`**（退出码 1，**没有静默通过**），`problems` = 缺 api_call_ms + `total_inferences=0` + 返回错误串 `Error: Error code: 502` | 真机 | 2026-09-22T09:50:16Z |
| R：判据修正后的重跑（同一真实帧） | `node scripts/frame_link_probe.mjs --frame-file logs/frame-link/real-frame-163.jpg --reset-session --observe-ms 25000` | `verdict=FAIL`（诊断串现在恒定判红，见 ⚠️①）；`text="Empty model response: stop"` / `user_prompt=""` / `api_call_ms=415.89` | 真机 | 2026-09-22T10:01:44Z |

> ⚠️ **两处必须如实说明，否则上面的行会被误读**：
>
> **① 判据在测量之后被收紧过一次 ⇒ A/A2 的 `verdict` 是「旧判据」的读数。**
> 首版判据把 `api_call_ms` 读成 `metrics.api_call_ms`（**层级错**，实际在
> `metrics.latency_breakdown_ms.api_call_ms`），且只在 `--require-content` 档对
> `Empty model response` 判红。于是：A 因**误读**一个不存在的键而 FAIL（**假红**），
> A2 因不带 `--require-content` 而 PASS（**放过**了诊断串）。**两轮的 `verdict`
> 都不是对「内容空」的判定** —— 对它的判定见上方「判定结论」表，
> 由**判决性对照实验**（直连 7060 / 直连 8070）给出，与这两轮的 `verdict` 无关。
> 修正后重跑（R 行）如实判 **FAIL**。
> ⇒ **读本表时请以「读数」为准，不要以 A/A2 的 `verdict` 为准。**
>
> **② 两条负控的档不同（不是冗余也不是重复）。**
> C 用 `--expect-no-response`（**专为负控设计的档**：期望收不到配对响应，
> 收到才算失败）；C2 用**正常判据**（AC5 的字面要求：停掉 8070 ⇒ 该项判 FAIL）。
> 两者都成立，但意义不同 —— C 证明「负控装置能识别『没有响应』」，
> C2 证明「正常判据在 8070 下线时会判红」。AC5 由 **C2** 满足。
>
> **③ 「发帧前停掉应用 1fps 循环」这句装置描述在 B 轮不成立**：B 的
> `frames_sent_in_window=3`、`total_inferences=11`，说明当时应用自身循环仍在推帧
> （探针停循环的代码在 B 轮之后才定稿）。⇒ B 的**读数**（文本 + 耗时）有效，
> 但它是「多帧中的首帧配对」，不是纯净单帧样本。R 行才是单帧干净样本。

**★ 正常基线（本票 §2 要求建立的那一项）**：

| 项 | 值 |
|---|---|
| 返回文本样例 | `用户打开了浏览器，正在查看一个包含多个代码窗口和设置选项的网页界面。`（与真实画面一致：当时屏幕上是本仓库的编辑器 + 本对话） |
| 耗时量级 | `api_call_ms` **0.42–0.95 s**（A/A2/B 三轮：420.87 / 953.32 / 729.74）；`total_ms` 与 `api_call_ms` 同量级（编解码 <2 ms，不是瓶颈） |
| 端到端（含传输） | 应用日志 `latency[transport+infer-screen]` 实测 **1.6–1.7 s**（1fps 采集下） |
| 帧率/分辨率下表现 | 1 fps、**764×540**（实测协商值，见下）；每帧 b64 ≈ 75 KB |

**★ 采集参数与到达服务端的实际值（AC4）**：

**命令**：`python scripts/frame_token_probe.py logs/frame-link/real-frame-163.jpg --json logs/frame-link/capture-params-163.json`
（该探针为本票新建；**每档一个全新会话**，32×32 极小图作基线扣除固定开销）

| 请求分辨率 | 请求像素 | `max_pixels` 削后应为 | `prompt_tokens` | **图像 token**（减基线后） | b64 字符 |
|---|---|---|---|---|---|
| 32×32（基线） | 1,024 | 32×32（不削） | 956 | 0 | 1,384 |
| **764×540**（**真机协商值**） | 412,560 | 764×540（不削） | 1,355 | **399** | 82,284 |
| 960×540（`screen_capture.js` 的 ideal） | 518,400 | 960×540（不削） | 1,457 | **501** | 99,388 |
| 1280×720 | 921,600 | 1280×720（不削） | 1,867 | **911** | 147,136 |
| 2560×1440 | 3,686,400 | **1365×768**（被削） | 1,979 | **1,023** | 374,724 |

**结论**：
1. **`max_pixels=1048576` 确实生效**：1440p（3.69 Mpx）被削到 ≈1365×768（1.05 Mpx）——
   图像 token 从「按像素线性外推应有的 ~7,300」压到 **1,023**，即**削了约 86%**。
2. **但在本项目的真实采集分辨率下它不生效（恒等变换）**：764×540 = 412 kpx，
   远低于 1 Mpx 预算 ⇒ 与
   `doc/research/capture-resolution-chain-2026-09-20.md` §1 的既有结论**一致**。
3. **`getDisplayMedia` 的协商结果**：`screen_capture.js` 请求 `ideal 960×540`，
   实测只拿到 **764×540**（`width=764` 由被捕获窗口的实际尺寸决定，不是 960）。
   附 `frameRate=1`、`displaySurface="window"`、`screenPixelRatio=1`、
   `resizeMode="crop-and-scale"`。

> ⚠️ **这张表三次重跑才稳定，前两次的读数是错的**（都因会话复用）：
> 会话名固定时 webinfer 会把每个帧**追加进该会话的 chunk**，`image_tokens`
> 逐轮线性累加 —— 实测同一分辨率连跑四次得到 **399 / 798 / 1197 / 1596**
> （每轮恰好 +399 = 一帧的量）。⇒ 上表是**会话名带 pid+时间戳、且先
> `POST /v1/streaming/reset`** 之后的读数，**连续三次重跑逐档完全一致**。
> 前两次的 826 / 1850 / 2074 是**累积污染值**，已作废，不得引用。

**★ 装置纪律（AC6，本票明确要求的那条）**：
自建 `new WebSocket('/ws?session_id=…')` 发帧**收不到 `vlm_response`**（只回
`status` / `server_config`）—— 已在 §1 #14 记为反例。本轮探针因此**从不自己开连接**，
只附着到已运行的应用页面并复用 `window.websocket`；并在 `window.websocket` 不可用时
**直接判「装置不可用」并退出 2**，而不是自建连接凑一个结果。

**本轮踩到并写进探针的 4 个装置坑（防后人重踩）**：

1. **注入帧会被静默丢弃**：应用自身 1fps 采集持有 `vlm_service._processing_lock` 时，
   另注入的帧命中 `logger.debug("VLM busy, skipping frame")`，**永远等不到**与它同
   `frame_seq` 的响应。⇒ 探针先停应用自己的 1fps 循环，再发单帧。
2. **帧会累进 prompt 直到 502**：每帧约 +1.4k prompt token，连推十余帧即超
   llama `n_ctx=16384`（实测 `request (162806 tokens) exceeds the available context size`）。
   ⇒ 这是「上下文累积」而非帧链路故障；探针须先 `POST /v1/streaming/reset` 清会话
   （注意会话名是 **`default`** —— webui 建 `VLMService` 时没传 `session_id`）。
3. **judged 字段层级**：`api_call_ms` 在 `metrics.latency_breakdown_ms.api_call_ms`，
   **不是** `metrics.api_call_ms`。探针首版读错层级，把一次**成功**的推理
   （`api_call_ms=420.87`）误判成「模型未被调用」—— 这正是「假红」。
4. **失败轮会读到上一轮的成功数字**：`analyze_image` 异常时返回 `f"Error: {e}"` 且
   **不更新** `last_latency_breakdown_ms` ⇒ 只看那个数字，8070 挂掉反而可能判绿。
   ⇒ 判据显式对 `Error:` 前缀判红。

**★ 探针自身的判据有效性（「判据能跑绿」≠「判据能分辨对错」）**：

判据被抽成**纯函数**（`judgeRound` 判一轮、`pickPairedResponse` 按 `frame_seq` 配对），
可在 node 里直接 import，故能对真实缺陷形态逐条写**离线**回归，而不是只靠真机轮碰运气。

**变异测试：6/6 全部被杀死，且各由声称守护它的那条测试杀死** ⇒ 这些测试不是同义反复：

| 变异体（把缺陷改回去） | 被哪条测试杀死 |
|---|---|
| `api_call_ms` 读回错层级（`metrics.api_call_ms`） | `test_healthy_round_has_no_problems`（+ `…_reads_api_call_ms_from_latency_breakdown`） |
| 去掉错误串判红（复现「失败轮读上一轮成功数字 ⇒ 假绿」） | `test_error_text_fails_even_without_require_content` |
| 负控反向失效（收到响应也算过） | `test_negative_control_fails_when_a_response_still_arrives` |
| 装置不可用时报绿（`return 0`） | `test_unreachable_cdp_exits_2_not_0`（+ `…_explains_itself`） |
| 诊断串不判红（复现「ALL PASS + 内容空」自相矛盾） | `test_content_empty_placeholder_fails_with_require_content`（+ `…_never_coexists_with_all_pass`） |
| **不按 `frame_seq` 配对**（随便取一条响应） | `test_paired_response_picks_only_the_rounds_own_frame`（+ `…_when_only_foreign_frames_replied`） |

> ⚠️ **最后一行的变异体原本是「幸存」的**：`/code-review` 的 Standards 轴查出，
> 当时只有 `judgeRound` 被覆盖，而**配对逻辑本身没测** —— 把 main() 的
> `filter(frame_seq === seq)` 改成「取任意一条」**能全绿通过**。那份检查还查出
> 变异体 4 的归属写错了（实际由 `…_exits_2_not_0` 杀死，不是 `…_explains_itself`）。
> ⇒ 本轮把配对抽成 `pickPairedResponse` 并补 4 例测试，该变异体现在**被杀死**。
> 这正是「CI 全绿 ≠ 断言有效」的又一实例（本仓 §6.4），故单列。

| 项 | 命令 | 结果 | 真机? | 测量时间 |
|---|---|---|---|---|
| 探针判据的行为测试 | `python -m pytest scripts/tests/ -q`（**解释器：`D:\AI\envs\joyai-main\python.exe` = 3.12.13**） | **ALL PASS** 89 passed（含 #162 的 65 例 + 本票 24 例） | 离线 | 2026-09-22T10:2x |
| 同上，**用本机默认 3.9** | `py -3 -m pytest scripts/tests/ -q` | **1 failed / 88 passed** —— `test_verify_ritual.py`（**#162 的文件**）用了 `zip(strict=)`，那是 3.10+ 语法 ⇒ 在 3.9 下 `TypeError`。**与本票改动无关**，但**必须写清解释器**，否则「85 passed」这句话在默认解释器下是假的 | 离线 | 2026-09-22T10:2x |
| 探针判据的变异测试 | 6 个变异体（见上表） | **ALL PASS** 6/6 被杀死，各由对应测试杀死 | 离线 | 2026-09-22T10:3x |

> **CI 可见性**（防 #152 重演）：`.github/workflows/quality.yml` 的 `scripts-tests`
> job 原先把测试**逐个点名**（`pytest scripts/tests/test_verify_ritual.py`）——
> 新加的测试文件会**静默不被收集**。本轮改为跑**整个目录**（`pytest scripts/tests/`）
> 并显式 `setup-node`（探针是 .mjs）。测试文件里的 node 缺失处理也用
> **fail-closed**（收集期报错）而不是 `skipif` —— 「全 skip」看起来与「全通过」一样绿。

**本票不改被测对象**（spec §2）：缺陷已另立工单 **#168**
（<https://github.com/kuhaku9527/vl-interaction-dev/issues/168>），本票只交
「判定 + 基线 + 装置 + 台账」。该工单正文的本地副本留档于
`doc/acceptance/pending-issue-163-frame-decision-contract.md`（提交前为草稿，
现已提交，副本供离线追溯）。

> **本轮证据产物的可复现性**：逐轮 JSON（`round-A/A2/B/C/C2-*.json`）与真实帧
> （`real-frame-163.jpg`，764×540）落在 `logs/frame-link/`，而 `logs/` 被 gitignore
> ⇒ **换台机器就取不到**。这与台账既有的 `logs/events/webui-*.jsonl` 引用是同一惯例
> （记「命令如当时所跑」），但**结论不依赖那些文件**：四轮结论均可由
> `scripts/frame_link_probe.mjs` 重跑复现，命令与判据都在本页。

---

### 2026-09-22（★ 首次**全绿**真机轮 + AC 负控：停一个服务）

> 这是运行器**第一次跑出 ALL GREEN**（此前三轮都是故意制造的负控轮）。
> **前置**：`start-joyai.ps1 -Mode default` → **6/6 服务 200**
> （7060/8070/8099/8985/8079/8997）；静态服务器 8123 在位且服务本仓库 static 目录。
> 本轮意义：**判据正则首次对「新鲜的真机输出」验证**（此前只对测试里收录的输出样本验证过）。

**命令**：`python scripts/verify_ritual.py`　**退出码**：`0`（**全仪式全绿**）

| 项 | 命令 | 结果 | 真机? | 测量时间 |
|---|---|---|---|---|
| 服务栈探活 | `python services/scripts/verify-services.py` | **ALL PASS** ALL GREEN | 真机 | 2026-09-22T08:17:41Z |
| 运行时门禁 | `python scripts/drift_gate.py --contract config/drift-contract.json --phase runtime --mode closed --no-history` | **ALL PASS** block_fail=0 / warn_fail=0 | 真机 | 2026-09-22T08:17:42Z |
| 前端残留审计（死引用） | `node scripts/audit-frontend-residue.mjs` | **ALL PASS** 死引用 0（DOM id 294） | 真机 | 2026-09-22T08:17:48Z |
| 路由契约审计 | `node scripts/audit-api-contract.mjs` | **ALL PASS** BROKEN=0 / UNUSED=15 | 离线 | 2026-09-22T08:17:48Z |
| live 决策事件读取 | `python -m decision_events --events-dir logs/events/webui-2026-09-22.jsonl --require-latency` | **ALL PASS** rounds=4 | 离线 | 2026-09-22T08:17:48Z |
| 向量语义召回（golden） | `python tools/eval_golden_recall.py --mode vector` | **ALL PASS** 24 / 24 | 离线 | 2026-09-22T08:17:54Z |

**同一台机器上的 AC 负控（★ 工单 #162 要求的「停掉一个服务」）**：

停掉 **voice-clone (8985)**，其余 5 项服务不动 ⇒ 重跑**完整**仪式：

| 项 | 命令 | 结果 | 真机? | 测量时间 |
|---|---|---|---|---|
| 服务栈探活 | `python services/scripts/verify-services.py` | **FAIL** 服务栈未全绿：**2 项失败** | 真机 | 2026-09-22T08:18:5x |
| 运行时门禁 | `python scripts/drift_gate.py … --phase runtime --mode closed --no-history` | **ALL PASS** block_fail=0 / warn_fail=0 | 真机 | 2026-09-22T08:18:5x |
| 前端残留审计（死引用） | `node scripts/audit-frontend-residue.mjs` | **ALL PASS** 死引用 0 | 真机 | 2026-09-22T08:18:5x |
| 路由契约审计 | `node scripts/audit-api-contract.mjs` | **ALL PASS** BROKEN=0 / UNUSED=15 | 离线 | 2026-09-22T08:18:5x |
| live 决策事件读取 | `python -m decision_events … --require-latency` | **ALL PASS** rounds=4 | 离线 | 2026-09-22T08:18:5x |
| 向量语义召回（golden） | `python tools/eval_golden_recall.py --mode vector` | **ALL PASS** 24 / 24 | 离线 | 2026-09-22T08:18:5x |

**退出码 `1`**，结论行为 `❌ 存在问题项：服务栈探活` —— **没有静默跳过、没有整体报绿**。
⇒ 工单 #162 的负控判据（「让任一项失败 → 该项判 FAIL 并体现在输出行里」）**在真机上成立**。
（附：`--only stack` 的子集形态亦同时验证 —— 它额外点名「未覆盖 5 项」并判 `exit 1`。）

### 2026-09-22（运行器首次真跑：栈下线的负控轮）

> **前置**：6 服务**均未起**（7060/8070/8099/8985 全 000）；静态服务器 8123 **在位且服务本仓库
> static 目录**（`index.html` sha256 与仓库一致）。故本轮是**故意制造的负控轮**：
> 验证「真机项不可测时不许给数字」。

**命令**：`python scripts/verify_ritual.py`　**退出码**：`1`（有 FAIL ⇒ 判红，未整体报绿）

| 项 | 命令 | 结果 | 真机? | 测量时间 |
|---|---|---|---|---|
| 服务栈探活 | `python services/scripts/verify-services.py` | **FAIL** 服务栈未全绿：5 项失败 | 真机 | 2026-09-22T07:15:45Z |
| 运行时门禁 | `python scripts/drift_gate.py --contract config/drift-contract.json --phase runtime --mode closed --no-history` | **无法测量**（前置缺失 ⇒ 未执行该命令） | 真机 | 2026-09-22T07:15:45Z |
| 前端残留审计（死引用） | `node scripts/audit-frontend-residue.mjs` | **ALL PASS** 死引用 0（DOM id 294） | 真机 | 2026-09-22T07:15:56Z |
| 路由契约审计 | `node scripts/audit-api-contract.mjs` | **ALL PASS** BROKEN=0 / UNUSED=15 | 离线 | 2026-09-22T07:15:56Z |
| live 决策事件读取 | `python -m decision_events --events-dir logs/events/webui-2026-09-22.jsonl --require-latency` | **ALL PASS** rounds=4 | 离线 | 2026-09-22T07:15:56Z |
| 向量语义召回（golden） | `python tools/eval_golden_recall.py --mode vector` | **ALL PASS** 24 / 24 | 离线 | 2026-09-22T07:16:03Z |

**本轮读出的三件事**：

1. ✅ **程序本身按设计工作**：栈未起时「运行时门禁」判**无法测量**而非给数字
   （旧行为会借 drift_gate 的 rc=3 一路滑过去）；离线三项照常跑完，未整体中止。
2. ⚠️ **「服务栈探活」FAIL 是预期的**（本轮栈故意没起）—— 但注意它与「不可测量」是**两回事**：
   探活脚本**真跑了**且真的探不到服务 ⇒ 内容上判 FAIL；而门禁项**压根没跑** ⇒ 判不可测量。
   这个区别正是 139 假数字事故的核心。
3. ⚠️ **退出码 1，不是 0** —— 负控成立：负控轮没有被报成绿。

### 2026-09-22（负控②：静态服务器服务**错目录** ⇒ 拒绝给数字）

> 这是 139 假数字的**原始形态**复现：另起一个 `http.server` 在 8124 上服务**别的目录**，
> 它 HTTP 200、端口在听，但内容不是本仓库 static。

| 测什么 | 命令 | 结果 | 真机? | 测量时间 |
|---|---|---|---|---|
| 前置探针（正确目录） | `verify_ritual._static_server_claim(8123)` | ✅ `内容 hash 与本仓库一致` | 真机 | 2026-09-22T07:16:38Z |
| 前置探针（**错目录**） | `verify_ritual._static_server_claim(8124)` | ✅ **判定为不成立**：`index.html 内容 hash 与本仓库 static/index.html 不符（服务错目录 ⇒ 审计会给出完全反向的假数字）` | 真机 | 2026-09-22T07:16:38Z |
| 残留审计项（前置指向错目录） | 同上的探针注入运行器 | ✅ **无法测量**，`measurement = None`；**审计命令根本未执行** | 真机 | 2026-09-22T07:16:38Z |

⇒ **只看「端口是否在听」不足以判前置**：错目录那一档端口是通的（200）。故判据取
**served 内容 hash 与仓库一致性**。这是对 §1「死引用 139 是假数字」那一条的机制性封堵。

### 2026-09-22（负控③：code-review 查出「子集运行自称 ALL GREEN」并修掉）

> `/code-review` 两轴（Standards / Spec）**各自独立**复现了同一个缺陷，且 Spec 轴把它标为
> 「★ 只是看起来满足」。这是本票的**同构病**，故单列一行。

| 测什么 | 命令 | 结果 | 真机? | 测量时间 |
|---|---|---|---|---|
| 修前：子集运行自称全绿 | `verify_ritual.py --only api-contract,decision-events,golden-recall` | ❌ **假绿**：打印「✅ ALL GREEN（每一项都真跑过且通过）」，**exit 0**，而栈探活/残留审计/运行时门禁**压根没跑** | 真机 | 2026-09-22T07:2x |
| 修前：空选择静默跑空 | `verify_ritual.py --only ","` | ❌ **假绿**：选不出任何项 ⇒ 空结果集 ⇒ 打印 ALL GREEN 且 **exit 0**；与模块自述「没有任何路径会因为没有运行而返回 0」直接矛盾 | 真机 | 2026-09-22T07:2x |
| 修后：子集运行 | `verify_ritual.py --only api-contract,decision-events,golden-recall` | ✅ **`未覆盖 3`**，点名「服务栈探活、运行时门禁、前端残留审计」为**不是通过**；**exit 2**；输出中不出现 ALL GREEN | 真机 | 2026-09-22T07:28:39Z |
| 修后：空选择 | `verify_ritual.py --only ","` | ✅ **显式报错**：`--only 未指定任何有效项：','`；**exit 1** | 真机 | 2026-09-22T07:28:39Z |

**根因（结构性，值得记住）**：「一轮是否全绿」是**关于覆盖范围**的判断，而
`run_ritual` 初版返回的 `list[Result]` **无法知道自己缺了什么** —— 传 3 项就只好回 3 项，
没有任何字段能表达「完整仪式还有另 3 项没跑」。
⇒ 修法不是加个 `if`，而是把**覆盖范围做成类型的一部分**（`Run` 类型带
`expected_item_ids` / `uncovered_ids` / `is_complete`），并规定：
**只有「覆盖完整仪式 + 每项都 PASS」才配得上退出码 0**。

**其后对抗性复查又查出 3 处 fail-open（同一病根，已一并封死）**：

| 漏洞 | 为什么危险 | 修法 |
|---|---|---|
| `run_ritual(full_ritual_ids=None)` **默认值** | 默认值把「传入的项」当作完整仪式 ⇒ **任何省略该参数的调用方**都能把子集报成全绿。只有 `main()` 传了它 ⇒ 缺陷只存在于库调用路径，最难发现 | 取消默认值（**必填**）：漏传在调用点就是 `TypeError`，而不是悄悄变绿 |
| `format_summary(run.results)` | 传裸结果列表时覆盖范围丢失，文案**仍打印 ALL GREEN**（`exit_code` 当时是安全的，文案不是） | 覆盖范围未知 ⇒ 改报「覆盖范围未知」，不出通过结论 |
| `exit_code` 只挑 FAIL / 无法测量 | **任何别的 status**（将来新增状态、或自定义判据的错值）都会掉进最后一行 `return 0` | 改为 `status != PASS` 一律非绿；未知状态单独计数并在文案里点名 |

⇒ 这一轮的教训值得记住：**「防假绿」的代码本身最需要防假绿**。
三处都不是逻辑写错，而是**默认值 / 兜底分支**这类「看起来安全」的地方漏了出去。

### 2026-09-22（负控④：终局对抗性复查 —— 10/10 变异体被杀死，另补 2 处加固）

> 修复后再做一轮独立对抗性验证：17 种 CLI 组合 + 库入口 + 真实错目录服务器 + **变异测试**。

| 测什么 | 结果 | 真机? | 测量时间 |
|---|---|---|---|
| 变异测试（10 个「把缺陷改回去」的变异体） | ✅ **10/10 全部被杀死**，且各由**声称守护它的那条测试**杀死 ⇒ 测试**不是同义反复** | 离线 | 2026-09-22T07:4x |
| 假绿搜索（17 种 CLI 组合 + 库入口） | ✅ **未找到任何假绿路径**；`--list` 除外（它只列清单、不执行任何东西，返回 0 正确） | 离线 | 2026-09-22T07:4x |
| 防 139 属性（真实起 8127 服务**错目录**，HTTP 200） | ✅ 探针判**不成立**；残留审计项 `无法测量`、`measurement=None`、**命令执行列表为空** | 真机 | 2026-09-22T07:5x |
| 四要素完整性 | ✅ 6 行（含 2 行 N/A）均为 5 格：项/命令/结果/真机?/时间 | 离线 | 2026-09-22T07:4x |

**本轮又补的两处加固**（均为 fail-closed 边界，非阻断级）：

| 加固 | 原来的问题 |
|---|---|
| `--only ""` 显式报错 | `if not only` 把 `None`（没给旗标）与 `""`（**给了但展开成空**）当成同一件事 ⇒ `--only "$IDS"` 在 `IDS` 为空时**静默跑起整轮真机仪式**。虽不会报假绿，但「静默做了一件你没要求的事」本身就是缺陷 |
| 残留判据拒绝「自相矛盾的 0」 | 判据独立接受「死引用 0」+「DOM id 0」。前者前置守卫已堵死（hash 钉住），但**判据应当自我防御** —— DOM 里一个 id 都没有 ⇒ 页面没载入 ⇒ 「0 处残留」不是「没有残留」而是「一个都没读到」。真机 139 那次的 DOM id 恰好就是 0 |

**另：DeepSec 提交门禁拦下 2 条（已按本仓纪律「改写消除」而非加 ignore）**：

| 命中 | 处置 |
|---|---|
| `sast_command_injection_os_system`（high）：`subprocess.run(list(command.argv))` | 加**可执行白名单**（`_ALLOWED_EXECUTABLES`）：argv 事实上由注册表固定给出、不含外部输入，但把该事实**在代码里也强制**成立。实测非白名单程序被拒（rc=126）。补 3 例测试 |
| `sast_ssrf_fetch_user_url`（medium）：`urlopen(url)` 接受完整 URL 参数 | 改为 `_probe_local(port, path)`：URL 由**端口号 + 固定路径**拼出，删掉「可传任意 URL」的形态。四个探活点全部改走它 |

> 处置原则沿用 `.deepsecignore` 里写明的本仓纪律：**优先改写消除触发条件，而不是加 ignore**。
> 修后重扫该文件集 **findings = 0**。

**同轮被查出并一并修掉的其他缺陷**：

| 缺陷 | 修法 |
|---|---|
| `--summary` 与默认分支**打印完全相同**（空旗标） | 简版只给状态行与结论；补回归测试 |
| 前置探针被**调用两次**（判真一次、取 detail 一次） | 每前置只探一次；补测试 |
| `GOLDEN_SET_REL` 死常量；分母硬编码 24 而无人守 | 删死常量；补测试「钉住的分母必须等于磁盘上 golden 集条数」 |
| 台账本节自称「37 例」，实际 65 例（含两轮复查补 11 例） | 改为 65（本文档是**证据 SSOT**，自述数字也错不得） |

### 2026-09-22（真机轮：全栈 + live 语音 + 向量召回 + 审计）

**前置**：`start-joyai.ps1 -Mode default` → 6/6 服务 200。GPU 8261 MiB（模型已加载）。

| # | 测什么 | 命令 | 结果 | 真机? | 备注 |
|---|---|---|---|---|---|
| 1 | 服务栈探活 | `python services/scripts/verify-services.py` | **ALL GREEN** | ✅ | 7060/8070/8985/8099 全 OK |
| 2 | drift-gate runtime | `python scripts/drift_gate.py --contract config/drift-contract.json --phase runtime --mode closed` | **block_fail=0 / warn_fail=0**，共 1 项 | ✅ | `vlm-n_ctx` 符合决策书 |
| 3 | 死引用审计 | `node scripts/audit-frontend-residue.mjs`（需 8123 静态服务器 + CDP） | **死引用 0**；显式隐藏 73；已删功能疑似残留 0 | ✅ | index.html 294 id == 运行时 DOM 294 id |
| 4 | 路由契约审计 | `node scripts/audit-api-contract.mjs` | **BROKEN=0**；UNUSED=15（前端引用 30 / 后端注册 45） | ✅ | 15 个已分类：运维/诊断/配置三类 |
| 5 | **live 语音全链路** | 真浏览器麦克风（ego 驱动）+ 真人说话 | ✅ **跑通**：VAD→ASR→endpoint→决策→LLM→barge-in | ✅ | 见 §2 明细 |
| 6 | live 决策事件落盘 | `python -m decision_events`（services/webinfer） | **4 条**真实事件，含真实 `latency_ms` 与 `session_id` | ✅ | 本仓库**首次**有真实值的决策事件 |
| 7 | 向量语义召回（golden） | `python tools/eval_golden_recall.py --mode vector` | **recall@5 = 24/24 = 1.000** | ✅ | 需 `SILICONFLOW_API_KEY`；24 例全 HIT |
| 8 | 向量语义召回（手工） | `POST /v1/blocks/recall`（query「拉塔恩弱什么」，ns=wiki:eldenring_test） | 命中「拉塔恩对出血与猩红腐溃非常脆弱…」 **score 0.62** | ✅ | 语义命中，非关键词 |
| 9 | edge-tts 合成 | `python -m edge_tts …` / `POST /api/tts/edge` | ⚠️ **flaky**：13:11 通（16848B）→ 13:52 不通（502）→ 13:53 通（11232B） | ✅ | `speech.platform.bing.com` 间歇不可达 |
| 10 | MiniMax TTS | `POST /v1/synthesize`（8985） | ❌ **500**：`MiniMax t2a_v2 failed: 2067 Token Plan 用量上限` | ✅ | 属**运维状态**（额度），非缺陷 |
| 11 | webinfer 全量单测 | `pytest`（services/webinfer） | **535 passed** | 离线 | 含并行端点 #156 新增 |
| 12 | webui 全量单测 | `pytest`（services/webui） | **932 passed, 1 skipped** | 离线 | 与基线一致，无回归 |
| 13 | **帧链路（浏览器→WS→VLM）** | ego 驱动真浏览器，用**应用自己的 socket**（`window.websocket`）发 `type:'frame'`，768×576 真实 JPEG | ⚠️ **机制通、内容空**：收到 `vlm_response`（含 `metrics` + `frame_seq`），但 `text = "Empty model response: stop"` | ✅ | 见下方明细；**本项为首次真机测试，尚无「正常应返回什么」的基线** |
| 14 | 帧链路（错误装置对照） | 自建 `new WebSocket('/ws?session_id=…)` 发帧 | ❌ 只收到 `status`/`server_config`，**无 `vlm_response`** | ✅ | 反例：**必须用应用自己的 socket**才走通 |

**§1 补充：帧链路明细（2026-09-22 首次实测）**

真实帧（768×576，深蓝底 + 紫色矩形）经**应用自身 socket** 发送后：

```json
{"type":"vlm_response","text":"Empty model response: stop","frame_seq":9003,
 "metrics":{"last_latency_ms":417.7,"total_inferences":3,
   "latency_breakdown_ms":{"api_call_ms":416.5,"jpeg_encode_ms":0.77,…},
   "user_prompt":""}}
```

**两条可确证的结论**：
1. **链路是通的** —— 帧送达、模型被真实调用（`api_call_ms=416.5ms`，`total_inferences` 递增）、
   响应经 WS 回传。缺口 1「从未真机测过」**已被本次填补**。
2. **返回为空**，且 `user_prompt: ""` —— 帧路径**未携带 prompt**，模型无问题可答。
   服务端同步告警：`VLM returned empty content for session default; finish_reason=stop`（`vlm_service.py:624`）。

**⚠️ 未定性（需判定，不猜）**：这是**缺陷**（帧管线本应自带视觉提问）
还是**默认无 prompt 时的预期行为**（prompt 由前端 `update_prompt` 设置）？
⇒ 本次**只记录事实**，不给结论；判定交给后续工单。

**§1 补充：误判与更正（重要，防后人重踩）**

- ⚠️ **死引用审计「139」是假数字**：服务全 DOWN 时跑同一脚本 → 报 139 处死引用，
  且报「运行时 DOM 中的 id: **0**」。机制：脚本靠 CDP 载入 `http://127.0.0.1:8123/index.html`，
  而 8123 被**一个残留 http.server**（PID 15468，**非 joyai 栈**）占着且服务错目录（404）
  ⇒ 页面空白 ⇒ 每个 id 都判「死」。**换正确静态服务器后归零。**
  ⇒ **纪律**：审计脚本需 red 环境对照；无对照时其输出可完全反向。
- ⚠️ **`static/*.bak` 干扰手工复核**：`index.html.bak` / `styles.css.bak`（未入库，2026-08-18）
  含大量旧 id，手工 `grep -rl getElementById` 会命中而**误判死引用仍在**。
  审计脚本只扫 `*.js` 是**对的**；手工复核必须排除 `.bak`。
- ⚠️ **我一度误判 live 不通**：我用 `fetch('/offer')` 自建 peer connection 测试，未带
  `live_audio: true` ⇒ 落到 jarvis 分支。而**用户用页面真麦克风时走的是另一条 PC，是通的**。
  ⇒ **纪律**：拿自造装置证伪产品前，先确认装置与用户路径同构。

---

## §2 live 语音全链路明细（2026-09-22，真机真人声）

用户 13:25–13:26 对麦克风说话（`f61b9db3-…` 会话）：

| 时刻 | 事件 | 证据 |
|---|---|---|
| 13:25:16 | `LISTENING → USER_SPEAKING` | `conf=0.90`（`vad_threshold=0.70`） |
| 13:26:18 | cloud ASR 定稿 | `'Yeah.。这种事情单独立赔也好干什么。'`，`silence=2020ms` |
| 13:26:18 | `USER_SPEAKING → PROCESSING` | `on_speech_stopped`；`utterance=61594ms -> commit` |
| 13:26:18 | `ASR endpoint reached, sending to LLM`；`reply_epoch bumped to 1` | — |
| 13:26:18 | LLM 决策 `response` | `'你说话的语气听起来有点无奈，像是被什么麻烦事缠上了。需要我帮你分析一下该怎么处理吗？'` |
| 13:26:19 | `SPEAKING → HARD_INTERRUPTED` | **barge-in** `conf=0.90 >= barge_in_threshold=0.75` |

**落盘事件**（`logs/events/webui-2026-09-22.jsonl`）：

| ts | decision | latency_ms | raw_text_len | session |
|---|---|---|---|---|
| 2026-09-22T05:13:11Z | silence | 891 | 0 | probe160 |
| 2026-09-22T05:13:42Z | silence | 547 | 0 | p2 |
| 2026-09-22T05:26:18Z | **response** | **781** | 43 | f61b9db3-… |
| 2026-09-22T05:27:55Z | silence | 282 | 0 | f61b9db3-… |

⇒ **live 四态决策链路真机可用**；延迟量级 0.3–0.9s；`decision=response` 时 `raw_text_len=43`（非空）。

---

## §3 已确认**无法**用「服务未启动」解释的两个已知现象

| 现象 | 机制（代码级） | 依据 |
|---|---|---|
| `POST /api/llm/message` 永不产生 `live_decision` | 该端点调 `JarvisStateMachine._send_to_llm`；emit 点只在 `live_llm.finish_llm_turn` / `live_proactive`（`jarvis_mode` 有**自己的** `_finish_llm_turn`，不 emit） | `server.py:312`；真机实测：`{"queued":true}` 而事件 0 条 |
| `/offer` 不带 `live_audio:true` → 落到 jarvis | `server.py:485` 先判 `params.get("live_audio") is True` 才绑 live；否则走 `_offer_has_jarvis_audio`（live 的 offer 也含 `m=audio`，故会命中） | 真机日志：`Jarvis mic track bound` vs `[live] mic track bound` |

---

## §4 尚**无任何记录**的测试面（欠账清单）

> 这些面从未有过实测记录，或只有间接证据 —— 是「留坑」的具体位置。

| # | 面 | 现状 | 影响 |
|---|---|---|---|
| 1 | **帧链路**（摄像头/屏幕 → VLM） | ✅ **2026-09-22 已判定 + 已建基线 + 已定可见性（§1 #163 轮）**：真机四轮（无 prompt / 正常基线 / 两条负控）+ 可见面三实验（A/C/G）；判定为**读侧缺陷**（另立 #168），且其实测**到不了用户可见面** ⇒ 降级为「死路径上的 latent 缺陷」 | 已收口；#168 走独立工单 |
| 1b | 帧链路分辨率/`max_pixels` | ✅ **2026-09-22 实测**：1fps / 764×540（协商值）；图像 token 764×540→**399**、1280×720→911、2560×1440→1023（`max_pixels` 削后）。⚠️ 早先记的 826/1850/2074 **已作废**（会话复用导致累加污染），见 §1 #163 轮 AC4 表 | 已收口（见 §1 #163 轮 AC4 表） |
| 1c | 帧链路的**输出可见性**（VLM 输出是否到主显示位） | ✅ **2026-09-22 实测**：`#resultText` 的 `vlm_response` 通道当前**结构性不可达**（守卫 `isAnalysisRunning` 恒 `false`，因唯一置位函数零调用点）。**已记入 #168，未另立工单** —— 这是本 fork 有意的架构收敛（`ARCHITECTURE.md:11-14` 明写 `VideoProcessorTrack` 零构造点、四态只在用户说话/proactive 轮发生），**不是新缺陷** | 已收口；与 #168 同源 |
| 2 | **proactive 轮真机** | ❌ 从未真跑。`LIVE_PROACTIVE_ENABLED` 默认 OFF，`proactive_supported:false` | live 两条产出决策的路径之一完全未验 |
| 3 | **多轮取中位** | ✅ **2026-09-22 已做（工单 #165）**：真机 3 轮 × 2 variant（336 次推理）+ 离散度 + 逐句稳定性；存量历史 2 轮**离线重聚合**（复现工单的 12–14 句不一致）；负控（高方差桩 ⇒ stdev 0→47.14）。装置 `services/webinfer/decision_eval_rounds.py`（CI 可见、有单测） | 已收口；#157 的硬前置已满足 |
| 4 | `delegate` 真实行为 | ❌ 只有单测；真机未验 | `delegate` 是四态之一 |
| 5 | 记忆写入端到端（webinfer 自动 push） | ❌ 只手工调过 API | 生产链路的记忆写入未端到端验 |
| 6 | 1Hz 持续决策**现状** | ⚠️ 数据是 09-20 的，未重测 | 地图基线数字可能已过期 |
| 7 | 帧链路分辨率/`max_pixels` 现状 | ⚠️ 数据是 09-20 的 | 同上 |
| 8 | **历史测试结果未落盘** | ❌ 本文件之前，多轮实测只散落在 `memory/YYYY-MM-DD.md`，无统一台账 | 本文件即为补救；见 §6 |

---

## §5 两条写入路径的分工（曾致误判，故单列）

memory-store 有**两条**写入路径，**不要混用**：

| 入口 | 行为 | 召回方式 | 用途 |
|---|---|---|---|
| `POST /v1/external/sync` → `sync_wiki_dir` | 分块 → **embed → 建 HNSW 索引** | **向量语义**（`filter.namespaces` 必填） | Local Wiki |
| `POST /v1/blocks/push` → `backend.push` | **只写 SQLite 行**（不建向量） | `query="__warmup__"` 走**纯 SQL「最近块」**（`_recall_recent`，注释明写 no embedder required） | 对话记忆 |

⇒ **拿 `push` 写的数据去 `recall`（语义）必然返回空** —— 那不是缺陷，是走错入口。
（2026-09-22 实测确认；曾据此误报「向量库坏了」，见 §1 更正。）

---

## §6 历史结果回溯（2026-09-16 ~ 09-21，从 `memory/` 补录）

> 本节是「以前跑过但没落盘」的补救。**注意**：这些数字来自当时的 memory 记述，
> **多数没有命令、没有分母、部分已互相矛盾**。逐条注明可信度。

### §6.1 webui pytest 基线漂移（★ 最需要理解的一处）

| 日期 | 结果 | 来源 | 解读 |
|---|---|---|---|
| 09-19 | **20 failed / 851 passed / 2 skipped** | `memory/2026-09-19.md:453` | 「全量」口径 |
| 09-20 | **20 failed / 867 passed / 2 skipped** | `memory/2026-09-20.md:527,570` | passed +16、failed 同为 20 ⇒ **无新增失败** |
| 09-20（合并后） | **10 failed / 825 passed** | `memory/2026-09-20.md:651` | 当时判为「新红（+9）」 |
| 09-21（收口） | **889 passed / 1 skipped** | `memory/2026-09-21.md` | VAD 用例由「跳过」变为**真正执行** |
| **09-22（本次）** | **932 passed / 1 skipped** | §1 #12 | 含 #156 新增；**与 09-21 环比无回归** |

⚠️ **口径警告**：09-19 那条 `10 failed / 185 passed` 是**14 文件子集**，**不是全量** ——
同一天既有「子集」又有「全量」两个数字在流传。**引用时必须写清口径。**

### §6.2 其他服务的单测

| 日期 | 服务 | 结果 | 来源 |
|---|---|---|---|
| 09-20 | webinfer | **440 passed** | `memory/2026-09-20.md:750` |
| 09-22 | webinfer | **535 passed** | §1 #11（+ #156） |
| 09-19/20 | webui vitest | **44/44** | `memory/2026-09-19.md:173,241` |

### §6.3 真机与门禁

| 日期 | 项 | 结果 | 来源 |
|---|---|---|---|
| 09-20 | 6 服务 | 7060/8070/8079/8099/8985/8997 全 **200** | `memory/2026-09-20.md:800` |
| 09-20 | `verify-services.py` | **ALL GREEN / RC=0** | `memory/2026-09-20.md:692,800` |
| 09-22 | `verify-services.py` | **ALL GREEN** | §1 #1 |
| 09-22 | drift-gate runtime | **block_fail=0** | §1 #2 |
| 09-20 | eslint（合并后） | ⚠️ **1297 errors** → 后修 | `memory/2026-09-20.md` §11.2 |

### §6.4 ★ 已确认「因与自身断言无关的原因而通过」的测试（fails-open）

这一类是本项目**最贵的教训**，必须持续可见：

| 实例 | 性质 | 来源 |
|---|---|---|
| `test_qa_server_split_runtime.py` | `SRC` 是唯一 import 通道且无守卫；CI 绿来自 `pip install -e .` ⇒ **CI 比裸跑更宽松** | #151（已修） |
| 等价性测试 7 个 `_BUGFIX_DIVERGED` 中 **6 个断言恒真** | 弱断言恒通过 | #153（**未修**） |
| asr 套件 **46 个测试从未被收集** | `testpaths` 未含 `jarvis` | #152（已修） |
| 计分器（`summarize` 等）**长期零测试覆盖** | 代码在 `services/scripts/`，**不在 CI pytest 矩阵** | #155 复核发现（已修） |

### §6.5 已**被推翻/更正**的历史数字（防后人照抄）

| 曾被记录 | 更正 | 来源 |
|---|---|---|
| 「CI 全绿」可作事实前提 | **不成立** —— CI 只证明跑到的断言成立 | #151 |
| `ctx 16384→4096` 省 1,741 MiB | **按 f16 KV 算的**；q8_0 落地后只省 ≈**918 MiB** | 地图 #142 |
| llama-server 稳态 9,326 MiB | **8,014 MiB**（KV q8_0 后） | 地图 #142 |
| 桌面基线 1,343 MiB | **446 MiB** | 地图 #142 |
| 生产 prompt 误响应 84%（旧三态） | **非现状**；生产四态为 **30.8–34.6%** | #146 |
| 「决策从未落盘」 | **写入侧曾是假 no-op**（`event_json` 导入失败被静默兜底）；已由 #156 修 | #156 |
| 死引用 13 处（09-19） | 09-19 已清理；**2026-09-22 真机实测 = 0** | §1 #3 |
| 死引用 **139 处**（服务 DOWN 时测） | **假数字**（静态服务器未就位 ⇒ DOM=0） | §1 更正 |

### §6.6 本节的可信度自评

- ✅ **可交叉验证**：09-22 全部（有命令、有原始输出、本次亲测）
- 🟡 **单源且有明细**：09-20 的服务/门禁结果（有具体端口与 RC）
- ⚠️ **仅一行记述、无命令无分母**：多数 09-16~09-19 的数字 —— **引用前应重测**
- ❌ **已知矛盾未解**：09-19 的「子集 vs 全量」两套 webui 数字并存

---

## §7 如何用运行器追加证据（工单 #162）

> **一句话**：跑一条命令，把它打印的表格**原样粘贴**进 §1 的当日小节。四要素由运行器打，
> 不靠人记 —— 这就是本票存在的理由（纪律靠人记就一定会漏）。
>
> ⚠️ **运行器只覆盖 6 项平台面，不覆盖链路级真机验证**。链路级（帧链路 / proactive /
> delegate / 记忆端到端）各有自己的探针 —— 见 §7.6。

### 7.1 跑一轮

```bash
# 完整仪式（真机项不可测时会明确标出，离线项照常跑完）
python scripts/verify_ritual.py

# 只跑离线项（无服务环境；仍会给出四要素）
# ⚠️ 子集运行**不会**报 ALL GREEN：未跑的项会被点名，退出码为 2（见下）
python scripts/verify_ritual.py --only api-contract,decision-events,golden-recall

# ⚠️ 别让 shell 变量展开成空：`--only ""` 会**显式报错**（exit 1），
#    而不是静默跑起整轮真机仪式。要想跑全，就不要传 --only。

# 只给结论与台账行，不逐项列明细
python scripts/verify_ritual.py --summary

# 机器可读（供后续四票复用）
python scripts/verify_ritual.py --json

# 看仪式项清单与各项判据（不执行）
python scripts/verify_ritual.py --list
```

**退出码**：`0` **全仪式**全绿 ／ `1` 有 FAIL ／ `2` 无 FAIL 但**未跑完或有项无法测量**。

⇒ **没有任何路径会因为「没跑」而返回 0**：
* 一项都没跑（如 `--only ","`）⇒ **显式报错**（不是静默跑空）；
* 只跑了子集 ⇒ 未跑的项会被**点名**，退出码 `2`，输出里不出现 ALL GREEN；
* 有前置缺失 ⇒ 该项判「无法测量」，退出码 `2`。

> 本轮 code-review 查出并修掉的正是这一条：初版 `--only <子集>` 会打印
> 「✅ ALL GREEN（每一项都真跑过且通过）」并返回 0，而栈探活/残留审计/运行时门禁
> **压根没跑** —— 这与 139 假数字是**同一个病**（把「没测到」说成「通过」）。

> ⚠️ 本机 `python` 是 Windows Store 占位存根（静默失败），须用
> `/d/AI/envs/joyai-main/python.exe`（见 `doc/environment-dsh.md` §2.2）。
> 运行器因此会在开头**单独报出本轮实际使用的解释器** —— 命令列里的 `python`
> 是为了可复制（仓库文档惯例），照抄前先看那一行。

### 7.2 粘进台账

运行器末尾会打印一段自带表头的表格，**这就是台账行**，直接粘到 §1 当日小节：

| 项 | 命令 | 结果 | 真机? | 测量时间 |
|---|---|---|---|---|
| <sub>（运行器输出，勿手改）</sub> | | | | |

**粘贴后人工补两样**（运行器无法知道，只有人知道）：
1. **前置情况**：栈起了没、静态服务器在不在位 —— 决定了这些数字该怎么读；
2. **本轮结论**：这些行说明什么 / 与上一轮比有无变化。

### 7.3 三项纪律（运行器已强制，但要知道它强制了什么）

| 纪律 | 运行器的做法 |
|---|---|
| **前置缺失不得给数字** | 六项里凡有前置的，**前置不成立就根本不执行该命令**，判「无法测量」并写明原因 |
| **判据取自数字，不取自退出码** | 残留审计脚本 **rc 恒为 0**，故按解析出的「死引用 N」判；drift-gate 按 `block_fail` 判；召回按 `recall@k` 与**分母**判 |
| **只读，不改被测对象** | 对唯一会写盘的子命令（drift_gate 的历史报告）显式传 `--no-history`；密钥只注入子进程且**只回报键名、不回报值**，也不覆盖调用者已有的环境变量 |

### 7.4 前置缺失时**不要**绕过它

若某真机项报「无法测量」，正确做法是**把前置补上再跑**，而不是手抄上一轮的数字、
也不是直接跑那条子命令再把输出贴上来。历史教训（§1 更正）：服务 DOWN 时跑残留审计
会得到 **139 处死引用**这个**完全反向的假数字**，而脚本不报错、不警告 ——
一旦它进了台账，就会被后人当作事实引用。

### 7.5 运行器自身的行为测试在哪

`scripts/tests/test_verify_ritual.py`（65 例，**离线、不起服务**，被 CI 的 `scripts-tests`
job 收集）。若运行器本身被改坏（例如又把「无法测量」报成数字），这些测试会红 ——
这也是本票对「假绿色行」的兜底。

### 7.6 链路级探针（不在 7.1 的运行器里）

`verify_ritual.py` 覆盖的是**平台面**（服务栈 / 门禁 / 审计 / 召回）。**链路级**
（帧 → VLM、proactive 轮、delegate、记忆端到端）各有自己的探针，因为它们的判据是
**链路上的语义数字**，不是平台健康度。

**帧链路探针**（工单 #163 新建）：

```bash
# 前置：应用页面已在 Chrome 打开，且**真实屏幕采集在跑**
#   （点应用页面「视频 → 屏幕采集 → Start」；浏览器窗口选择器 = 人在回路）

# ① 无 prompt（复现「内容空」形态）——该轮**本应**判 FAIL
node scripts/frame_link_probe.mjs --reset-session --observe-ms 30000

# ② 正常基线：带问题 → 期望有内容（--require-content 让空内容判红）
node scripts/frame_link_probe.mjs --reset-session --observe-ms 40000 \
    --prompt "请描述当前画面内容，一句话。" --require-content

# ③ 负控：先 `stop-joyai.ps1 -Only 8070`，正常判据**必须**判 FAIL
node scripts/frame_link_probe.mjs --frame-file logs/frame-link/real-frame-163.jpg \
    --observe-ms 20000

# ④ 只用已落盘的真实帧复跑（不需人坐在屏幕前；回归/复核用）
node scripts/frame_link_probe.mjs --frame-file <此前 --save-frame 落盘的帧> --reset-session
```

**退出码**：`0` PASS ／ `1` FAIL ／ `2` **装置或前置不可用**（不是通过）。

> ⚠️ **绝不合成帧**。探针没有真实采集时**直接退出 2**，并提示你先去点 Start ——
> 合成帧会让「模型看到了什么」这件事失去意义（那是本项目已被推翻过的取证方式）。
> ⚠️ **绝不自建 WebSocket**。自建 `new WebSocket(...)` 收不到 `vlm_response`
> （已实测），据此下结论会得到完全错误的判定；探针只复用应用自身的 `window.websocket`。

**多轮取中位 / 离散度**（工单 #165 新建，`services/webinfer/decision_eval_rounds.py`）：

```bash
# 前置：llama-server (7060) 在位。**不需要**其余 5 个服务、不需要浏览器 ——
# 这是模型层评测，不是端到端链路。

# ① 负控自检（离线，秒级；证明离散度真的在度量离散）—— AC#5
cd services/webinfer && python -m decision_eval_rounds --self-check

# ② 真机 N 轮（默认 3；每次 56 例；两 variant 共 2×N×56 次推理）
BENCH_ROUNDS=3 BENCH_PROD_OUT=doc/research/data/benchmark_production_live_prompt_rounds.json \
    python services/scripts/benchmark_production_live_prompt.py

# ③ 存量轮次**离线**重聚合（不跑模型）—— 包括历史上那两个单轮文件
cd services/webinfer && python -m decision_eval_rounds --from-results \
    ../../doc/research/data/benchmark_production_live_prompt_results.json \
    ../../doc/research/data/benchmark_production_live_prompt_results_repeat.json \
    --variant P2_live4_prod_prompt_profile

# ④ 两次运行逐指标对比（AC#3「差异一眼可见」）
BENCH_DIFF_AGAINST=<上一轮 results.json> ... python services/scripts/benchmark_production_live_prompt.py
```

**判据**：结果里 `median` 必须与 `per_round`**并列**，且 `dispersion.stdev/range` 同在；
`metrics_note.degenerate_rounds` 非空时**不得**把该 ratio 的离散度当作模型抖动（那是分母退化）。

**定向轴记分卡**（工单 #157 新建，`services/webinfer/decision_eval_card.py`）：

```bash
cd services/webinfer

# ① 离线负控自检（不需要模型、不需要产物；秒级）—— 证明判据可证伪
python -m decision_eval_card --self-check

# ② 一条命令出卡（读入库的多轮真机产物；人读版）
python -m decision_eval_card

# ③ 结构化 + 落到稳定路径（供 #159 门禁与 diff 消费）
python -m decision_eval_card --json --out <稳定路径>/directed-axis-card.json

# ④ 阈值核验：从**入库产物重算**阈值并与代码里的声明比对（不一致即报错）
python -m decision_eval_card --verify-bounds
python -m decision_eval_card --show-bounds     # 含每条的出处与「是否派生」

# ⑤ 成本可配 + 两次运行 diff
python -m decision_eval_card --cost-fp 5 --cost-fn 1
python -m decision_eval_card --json --diff-against <上一份卡片.json>
```

**退出码**：`0` 全绿 ／ `1` 有判据判红 ／ `2` **有「无法测量」项**（不是通过）。
★ **「没测」永不返回 0** —— 与本仓 `scripts/verify_ritual.py` 同一条纪律（#162 已钉住）。

**判据**（逐条自带出处；`--show-bounds` 可复核）：

| 判据 | 指标 | 方向 | 阈值 | 出处 |
|---|---|---|---|---|
| `D1-nondirected-no-spurious` | 非面向→开口率（**含 delegation**） | ≤ | 54% | 入库产物重算（基线最差 variant 上界） |
| `D2-directed-nonresponse` | 面向句非响应率（**含被 silence 吞掉**） | ≤ | 27% | 同上（最坏轮） |
| `D3-not-for-me-precision` | nfm 精确率 | ≥ | 70% | **保守取值**（真机预测数中位仅 2，样本不足以定阈值） |
| `D4-not-for-me-recall-generalization` | **泛化子集** nfm 召回 | ≥ | 19% | 入库产物重算（泛化子集最坏轮） |
| `D5-cost-index` | 代价加权（3×误响应 + 1×漏判） | ≤ | 93 | 入库产物重算（最坏轮） |
| `S1..S5` | 分母齐备 / 无失败行 / token 证据完整 / 无可归因缺失 / ≥2 轮 | — | 结构性 | fail-closed |

> ★ **宽窄口径并列**：`D1` 把 `delegation` 算作开口（旧字段 `baseline_mis_response_rate_pct`
> 只数 `response`），`D2` 把被 `</silence>` 吞掉的面向句算作漏判（旧字段
> `directed_miss_rate_pct` 只数 `→not-for-me`，故在全部变体上**恒为 0.0%**）。
> 两个窄口径也照常输出，**差额可见**才是重点。
>
> ⚠️ **`cost_index` 单独挡不住「永远沉默」**（实测）：在 25 面向 / 26 非面向的基率下，
> 平凡沉默桩的加权代价（**49.0**）**低于**生产 prompt（**82.4**）——
> 桩更便宜 ⟺ `C_FP/C_FN > (25−FN)/FP`；3:1 已越过等代价点（该轮为 1.69）。
> ⇒ 挡住沉默策略的是 **D2/D4 两条召回下限**。卡片里的
> `cost_index_always_silent` / `cost_index_always_speaking` 就是为此提供的参照。
>
> ⚠️ **本段数字原为「84.3」，已更正为 82.4**（2026-09-23 对抗性复核查出）：
> 84.3 既不符合其自身算式（`100×41/51 = 80.4`）也不符合产物读数（82.4）——
> 典型的「同一事实两处数字分叉」。现已改为由
> `decision_eval_criteria.cost_index_cannot_guard_arithmetic()` **从产物现算**，
> 并有 `test_cost_index_guard_arithmetic_matches_the_real_artifact` 钉住。



