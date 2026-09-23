# 决策评测⑤：回归门禁（两张记分卡接进 drift-gate）（正式）

> 生命周期标记：`正式`
> 作者端点：`测试`
> 日期：2026-09-23
> 工单：#159（父 spec #154 的**第 5 片**纵切；前 4 片 = #155 冻结输入 / #156 决策落盘 / #157 定向轴 / #158 时序轴）
> 关联：`决策/跨域铁律.md`（D-2026-08-01-019『Drift Gate 门禁』）、`决策/业务-评测.md`、
> `doc/specs/drift-gate-harness-spec.md`、`doc/specs/f40-drift-gate-launcher-wiring.md`、
> `doc/standards/test-baseline.md`（#157 / #158 的阈值与判据台账；
> ★ 该台账的定向轴阈值在本轮被**主理人更正**为真值 63/21/70/17/101 —— 见变更清单）
> 合规：套用 `决策/spec编写规范.md` 四要素（因果链 / 条件 harness / 负面约束 / 生命周期标记）。
>
> ★ **上面前两个「关联」目前只提供机制出处，不提供阈值出处**：实测
> `决策/跨域铁律.md` 与 `决策/业务-评测.md` 里 `63.0` / `cost_index` / `记分卡` 等
> **0 命中**（只有 `Drift Gate` 机制条目命中）。契约 `decision_ref` 里那句「决策书：…」
> 因此**尚不成立**，要等 §8 提案经用户批准落盘。详见 §3.4.1。
>
> ★ **本 spec 未写入 `决策/`**：`决策/` 需用户批准后方可落盘，AI 只能起草提案。
> 本票需要的那条 SSOT 提案文本见 §8，由主理人转呈用户。

---

## 1. 因果链（Why / Why-this-choice）

- **Problem**：#157 / #158 交付了两张记分卡，各带一套可证伪判据，但**没有任何东西自动拦人**。
  卡片是「人跑一下看看」的仪式：质量退化只会在有人主动跑卡片时被发现。
  更糟的是，两张卡片的判据**互不覆盖**（定向轴管「该不该说」、时序轴管「说得是不是时候」），
  只看一张卡就会把另一轴的反向退化完全漏掉 —— `doc/standards/test-baseline.md` 已实测到
  这种「同一样本上两个判据自相矛盾」的形态（`T_SAMPLE_FLOOR=无法测量` 与
  `T_ONSET_MEDIAN=pass` 并排出现）。
  母题是**回归门禁**：把「读数」变成「拦人线」。

- **Why this choice**：仓库**已有一整套**门禁机制在跑（`scripts/drift_gate.py` 执行器 +
  `config/drift-contract.json` 契约 + `quality.yml` 的 `drift-gate` job，11 条 check）。
  本票的全部设计决定都服从一条纪律：**复用，不新造**。具体地：

  1. **不新增第二个执行器、不新增第二套退出码体系**。★ **本句原先还写着
     「契约 schema 与执行器一行不改」，那句话与实物不符、已删** ——
     执行器**确实被扩展了**（t1 的 `present_when_missing`、t10 的 `parse_as`
     与 `RecursionError` 反崩溃；实测 `git diff --numstat HEAD -- scripts/drift_gate.py`
     ⇒ **418 增 / 10 删**）。准确的说法是「**扩展同一个执行器，而不是另造一套**」：
     新增的是**可选字段**，缺省保持既有 11 条 check 的语义与判定不变。
     ★ 详见 **§2.1**（含此处与 AC1 字面之间的**张力**，那里没有把话说满）。
  2. **不新增 CI job、不加 `needs`**。既有 `drift-gate` job 的注释已明确记录「刻意不进
     `needs` 链」的原因（`ADR-0016`：上游红会让 job 被 skipped，导致门禁从未真跑过；
     `PR #53` run 30703927856 是实测教训）。本票只在该 job 里**加一步生成产物**。
  3. **沿用既有先例形态**：`产物写到稳定路径 → 门禁断言产物`。既有先例就是
     `logs/vlm-runtime-props.json`（`vlm-n_ctx`）—— 门禁读一个**已落盘的产物**，
     它自己不负责生产那个产物。

- **被否方案及理由**（负面约束的因）：

  | 被否方案 | 否决理由 |
  |---|---|
  | 契约里做数值比较（`observed <= threshold`） | 执行器是 `re.search(pattern, 合并内容)`，**没有数值比较能力**。为一张票把执行器改成能算数，等于把「单一真值」变成「一个小语言解释器」，且会让既有的 11 条 check 也走上一条从未验证过的代码路径。 |
  | 让生成步骤 `set -e` 直接失败 | 定向轴当前**实测 exit=1**（D4 未达标，是 #157 的**如实读数**）。`set -e` 会让 CI 因「读数如实」而红 —— 等于把门禁的判定权搬进了生成步骤，且把评测的判红悄悄吞成基础设施失败。 |
  | 把评测 check 标成 `phase: "runtime"` | ★ **在本机不可执行**，详见 §3.1（实测 `rc=3`）。这是本票对 `#154 §六` 字面要求的一处**已记录偏离**。 |
  | 新建 `drift-gate-eval` job | 违背既有注释记录的设计（并行正交、不进 needs 链）；且多一个 job 就多一处「被 skipped 却看起来绿」的失败模式。 |
  | 产物写到 `reports/eval/` | 该路径在 `.gitignore` 忽略范围内。CI 是干净检出 ⇒ 文件**不存在** ⇒ 契约指向它等于让门禁静默旁路（或让 fail-closed 变成每次必红）。 |
  | 门禁只读「整体 verdict」一个字段 | 定向轴有两个 variant、时序轴的 `criteria` 是列表：只读整体 verdict 无法指出**哪一条**判据退化了，也无法防「一个 variant 退化、另一个顶上匹配」。 |

---

## 2. 范围

- **做什么**：
  1. 两张卡的 JSON 产物落到**入库的稳定路径**：`doc/research/data/`。
  2. `config/drift-contract.json` 新增 **4 条**评测 check（定向轴 ×2 + 时序轴 ×2），
     全部 `severity: block` + `present_when_missing: fail`。
  3. `.github/workflows/quality.yml` 的既有 `drift-gate` job 内，**在门禁之前**加一步生成两张产物。
  4. `scripts/tests/test_eval_gate_contract.py`：把「阈值有出处」「缺结果判红」「pattern 真的匹配」
     变成**可执行断言**，并在 CI 的 `scripts-tests` job 里被收集。
  5. 本 spec + `doc/specs/README.md` 索引登记。

- **不做什么（负面约束）**：
  - ★ **本票**（#159 接线片）**不改卡片代码**（`services/webinfer/**`）：本票是接线，不是改判据。
  - ★ **本票不新增第二套门禁机制**：无第二个执行器、无第二个 job、无第二套退出码体系、不加 `needs`。
    ★ **注意措辞（这里曾经写错、已按实更正）**：负面约束**不是**「不改执行器」——
    执行器 `scripts/drift_gate.py` 在本轮 **确实被改了**（见下条与变更清单）。
    准确的约束是「**不另造一套**」，而不是「不碰既有那套」。
  - ★ **不改既有 11 条 check 的任何一个字段**（这一条**实测成立**）：尤其**不给它们加**
    `present_when_missing`，也不给它们加 `parse_as`
    （它们的缺省 `skip` / `null` 是**刻意**的 fail-open：`vlm-n_ctx` 依赖 `logs/`，而 `logs/` 在
    `.gitignore` 内，干净检出里必然不存在，加 `fail` 会让 CI 每次必红）。
    取证：比对 HEAD 与当前契约 ⇒ `ids added` 恰为 4 条 eval-*、`ids removed` 空、
    `baseline checks whose CONTENT changed` 空。
  - **不修 D4**：定向轴 D4 未达标是 #157 的如实读数，本票**把这条已知红固定在契约里**，
    而不是把它洗绿。

### 2.1 ★ 执行器被改了：如实说明（消除「同一文件两处说法分叉」）

**背景（如实）**：本 spec 的早期版本在 §2 负面约束里写着
「**不改执行器** `scripts/drift_gate.py`（属 t1 的归属；本票只消费其
`present_when_missing` 能力）」。**那句话已经与实物不符** ——
本轮 `scripts/drift_gate.py` 被改了（`git diff --numstat HEAD` ⇒ **418 增 / 10 删**）。
同一个事实在「负面约束」与「变更清单」两处说法分叉，正是本票要消灭的缺陷类型，故在此更正。

**执行器实际改了什么、为什么**：

| 改动 | 属谁 | 为什么必须改 |
|---|---|---|
| 逐 check `present_when_missing`（缺省 `skip`） | t1 | 原执行器在**引用文件全部缺失**时判**通过**（fail-open）—— 对评测产物而言「没产出」不是通过。**本票的核心 AC 依赖它** |
| 逐 check `parse_as`（缺省 `null`；`"json"` ⇒ 不可解析判红） | t10 | 修 **W1**：产物被截断时前缀不是合法 JSON，而「文件存在」就不进 fail-closed 分支 ⇒ 半截产物被判绿（§6.5 W1） |
| 接住 `RecursionError` / `ValueError` 归入「不可解析」 | t10 | 极深嵌套 JSON 抛 `RecursionError` 会穿透 `main()` ⇒ 门禁**整个 crash**，CI 拿到 traceback 而不是报告（§6.5 末节） |

**为何这仍满足 AC1「复用既有 drift-gate 机制、未新增第二套门禁」（如实说明，含张力）**：

满足的部分（可逐条取证）：
1. **仍是同一个执行器 / 同一份契约 / 同一个 job / 同一套退出码**
   （`0` 通过 / `1` 有 block 级失败 / `2` 契约或 schema 错误）；
2. **改动是「加可选字段」，不是「换语义」**：两个新字段**缺省即保持既有行为** ——
   `present_when_missing` 缺省 `skip`、`parse_as` 缺省 `null`，
   既有 11 条 check 的**字段与判定逐字节不变**（取证见上）；
3. **没有引入第二套门禁**：4 条评测 check 就是既有 schema 下的普通 check，
   由同一个执行器读同一份 `config/drift-contract.json` 判定。

★ **张力（不抹平，如实写出）**：AC1 的字面是「**复用**既有 drift-gate 机制」。
「在本票范围内改执行器源码」严格说**超出了「只消费」**的范围 —— 原始计划的语气是
「本票只消费 t1 已交付的能力」，而实际执行器上又落了 `parse_as` 与反崩溃两处改动。
可辩护的说法是「**扩展现有机制**」而非「另造一套」，但**这不是无争议的**：
一位严格的评审者可以主张「那两处应由执行器 owner 单独出票交付」。
⇒ **本票的记录是**：改动**已发生且已复核**（t1/t10 owner 交付、主理人独立复现），
且 AC1 的**实质意图**（不新增第二套门禁、不新增 job、不新增退出码体系）**成立**；
但「复用」一词的**字面边界确实被推移了**，这一点留给评审与用户判断，不在本 spec 里
自行判定为「完全无偏离」。相关偏离另见 §3.1（`phase` 取值）与 §8 提案。

---

## 3. 设计（核心决策点）

### 3.1 ★ 为什么是 `phase: "static"` 而不是 `"runtime"`（一处已记录的偏离）

父工单（`#154 §六`，以工单文本下达；**不是**仓库内文件）字面要求把评测 check 标成
`runtime`。**本机实测：那样写在 CI 永远跑不了**，故本票改用 `static`。
这是一处**有意的偏离**，理由如下（不假装照做）：

执行器 `_props_referenced_by_runtime()` 的逻辑是：只要**任一会被执行的 runtime check**
引用 `logs/vlm-runtime-props.json`，跑 `--phase runtime` 就会**无条件**先刷新 VLM probe。
既有 `vlm-n_ctx` 正是这样一条 runtime check，而 probe 需要一个真在跑的 llama-server。实测：

```
$ python scripts/drift_gate.py --contract config/drift-contract.json --phase runtime --mode closed
[RUNTIME-PROBE-ERROR] ... probe 刷新失败 (rc=2)
>>> RC=3
```

⇒ 无 llama 时 `--phase runtime` **恒** rc=3。若把评测 check 标成 runtime：

- 它们在 CI **永远跑不到**（job 会在 probe 上先死），直接违背「本地与 CI 行为一致」；
- 即便 probe 侥幸成功，把评测门禁的成败**绑在一个无关的运行实例可用性上**，
  也会让「评测退化」与「模型没起来」在报告里同形 —— 而这两件事的处置完全相反。

**为什么 static 在语义上是对的**：执行器对 `static` 是**纯读文件 + `re.search`**，
不碰运行实例、不触发 probe。而「记分卡结果」本来就是**已落盘的产物**——
对执行器而言，读一个落盘 JSON 正是 static 的语义，与它读 `run-windows.env`、
`adapter_types.py` 没有区别。**产物在运行期产生，但它被读取时是静态的**。

> **离线可跑性是本决定的硬约束**：两张卡片实测**完全离线**（各约 0.2 s，不加载模型、
> 不起服务、只读入库产物与冻结夹具），故 CI 不需要任何额外安装步骤。

### 3.2 fail-closed 语义：`present_when_missing`

原始缺陷：执行器在**引用文件全部缺失**时产出 `[SKIP] ... fail-open（不阻断）` 并判**通过**。
对评测结果而言那不是通过，而是**没测过**。

t1 新增的逐 check 字段：

| 取值 | 缺失时行为 |
|---|---|
| 缺省（不写）/ `"skip"` | `[SKIP]`，判**通过**（既有行为，一个字节都没变） |
| `"fail"` | **判红**，报告写明「缺结果 = 没测，≠ 通过」 |
| 其它任何值（`"FALSE"` / `true` / `null` / `1` …） | **rc=2** ＋ `[META-ERROR]` 点名 check id 与非法值 |

★ **非法值显式报错而不是静默当 skip** 是这条设计的关键：一个笔误会把「缺结果判红」
悄悄降级成 fail-open，门禁会**看起来在守**却完全没守。

★ 评测 check 的 fail-closed 只在**「全部引用文件都缺失」**时触发。部分缺失时
`<missing:path>` 占位符进入内容参与正则 ⇒ 自然判红（因为 pattern 不会命中）。
本票 4 条 check 各只引用**一个**文件，故两种路径等价。
（若将来一条 check 引用多个文件，需注意此语义：只要其中一个存在，「全部缺失」分支就不触发。）

### 3.2.1 ★ 解析完整性：`parse_as`（t10 新增，修 W1）

`present_when_missing` 只覆盖**路径不存在**。它覆盖不了「**文件在、但内容不是一份完整卡片**」——
那正是 W1（§6.5）。故新增逐 check 字段：

| 取值 | 文件存在但内容不可解析时 |
|---|---|
| **缺省（不写）/ `null`** | **不解析** ⇒ 行为与新增该字段之前一致（既有 11 条零变更） |
| `"json"` | **判红**（fail-closed），detail 写明「文件在但内容不是一份完整卡片 ⇒ 视同没测」 |
| 其它任何值（`"JSON"` / `true` / `1` / 未知串 …） | **rc=2** ＋ `[META-ERROR]` **点名 check id 与非法值** |

★ **缺省必须是 `null`（不解析），这不是保守而是必须**。逐条核过既有 **11** 条 check 的
`paths`（本次复跑，见下方取证），它们引用的文件**绝大多数不是 JSON**，例如：

- `services/scripts/run-windows.env`（`memory-store-port` / `main-context-env`）—— 环境变量文件；
- `services/scripts/run-windows.ps1`（`vlm-kv-quantization` / `webui-gateway-port`）—— PowerShell 脚本；
- `services/webinfer/adapter_types.py`、`services/webinfer/memory_io.py`、
  `services/memory-store/src/memory_store/app.py` —— Python 源码；
- `.github/workflows/quality.yml`（`ruff-pin`）—— YAML；
- `services/webui/src/joy_interaction_webui/static/index.html`、`.../wiki_frontend.js`
  （`webui-joywiki` / `webui-wikinspace`）—— HTML / JS。

★ **如实记一处细节（避免把话说满）**：并非每一条都非 JSON ——
`vlm-n_ctx` 引用 `logs/vlm-runtime-props.json`，`webui-vitest` 引用
`services/webui/package.json`。故准确的说法是：
**11 条里有 2 条引用 `.json`，其余 9 条引用非 JSON**；
且那 2 条**也不能**被要求解析 —— `logs/vlm-runtime-props.json` 是**运行时产物**
（`logs/` 被 gitignore，干净检出下根本不存在 ⇒ 会走 `present_when_missing` 缺省 `skip`），
`package.json` 虽可解析但它**不是**记分卡、将来被换成非严格 JSON 时也不该连带判红。
⇒ **结论不变：缺省必须 `null`**，否则干净检出下这些 check 会集体判红，
CI 全红而没人做错任何事。
取证：`python -c "import json;d=json.load(open('config/drift-contract.json'));[print(c['id'],c['paths']) for c in d['checks'] if not c['id'].startswith('eval-')]"`

★ 非法值显式 rc=2 而不是静默当 `null`：理由同 §3.2 —— 一个笔误会把「内容不完整判红」
悄悄降级成「不解析」，门禁**看起来在守**却完全没守。
实测（本次复跑）：`parse_as` 为 `"JSON"` / `true` / `1` / `"unknown"` 时**均 rc=2**，
且 `[META-ERROR]` 里**点名了 check id 与非法值**。

★ 本票 4 条 eval-* check 均声明 `parse_as: "json"`（它们引用的就是 JSON 记分卡），
既有 11 条**零变更**（字段保持缺省缺席）。取证：
`python -c "import json;d=json.load(open('config/drift-contract.json'));print([(c['id'],c.get('parse_as')) for c in d['checks']])"`
⇒ 仅 4 条 eval-* 为 `'json'`，其余 11 条为 `None`。

### 3.3 契约断言形态：绑定「判据 → 自己的 threshold / observed / verdict / reason」

执行器没有数值比较能力（§1 被否方案），故「observed 是否越线」只能由**产物自报的判定字段**
来断言。每条判据都绑定**四元组**，缺一不可：

1. `"criterion_id": "<id>"` —— 定位到**这一条**判据；
2. `"threshold": <值>` —— 阈值字面也进断言（★ t6 新增：改线即判红，见下）；
3. `"observed": <值>` 与 `"verdict": "<判定>"` —— 必须落在**同一条判据内**；
4. `reason` 里的数字串（如 `onset_latency_ms=573.0 <= 898.0（达标）`）—— 让
   **阈值与观测值同时出现在断言里**（可追溯性，见 §4）。

★ **结构判据的 `observed` 也必须绑**（t6 修的一处 high）：S1 分母、S3
`rows_without_token_evidence`、S5 `rounds_count`、时序轴 `T_SAMPLE_FLOOR` 的
`n_speaking`/`n_missing_stamp` 都进断言。**只绑 verdict 不足以守住「这份分数能不能信」** ——
对抗复核（t4 R1）把 S1 的 `observed` 改成 `{0,0}` 而 verdict 保持 `pass` 时，门禁**曾判绿**，
而那正是该判据 description 自己承诺要拦住的事。

并用三个结构性守卫防止「跨条目/跨变体误匹配」，外加一条性能守卫：

- **负向先行断言** `(?:(?!"criterion_id")[\s\S]){0,N}?`：让「id → observed → verdict」的窗口
  **不得跨过下一条判据**。否则把某条判据改成 `fail` 时，正则仍能走到**下一条**的 `pass`
  而继续命中（本票开发期负控实测出过这个洞）。
- **变体边界作终止界**：定向轴产物含**两个** variant（`P_live4_prod_prompt` /
  `P2_live4_prod_prompt_profile`），两边的判据 id 完全相同。V1 段的窗口必须以
  **V2 的 card key** 为终止界，否则「只退化 V1」会被 V2 顶上而判绿
  （同为开发期负控实测出的洞）。两个 variant 的读数因此**分别**被断言。
  两 variant 的 `quiet_rows` **不同**（54 / 59），故连期望值都必须分开写 ——
  共用一组值会让其中一个 variant 的断言**恒真**。
- **卡片级 `criteria.verdict` 自身也要绑**（t6 修的另一处 high）：定向轴两 variant 均
  `"fail"`（已知红）、时序轴 `"pass"`。改前无任何断言读它。
- ★ **所有量词必须有界 + 用独立先行断言而非成串 gap**（t6 性能修复）：成串的有界懒惰
  gap 在「末条判据退化」时回溯爆炸，实测 **>120 s**。挂起的门禁比判红更坏 ——
  CI 会**超时**而不是给出漂移报告（无报告 = 无人能定位）。改用「卡片 key + 每个判据一个
  独立 `(?=...)`」后最坏实测 **0.13 s**，并由
  `test_patterns_stay_fast_when_every_assertion_fails` 钉住。

★ **数字值的拼写不等于语义**（R4）：JSON 里 `50.0` 与 `50.00` 是**同一个值**，故契约把
数字写成值等价形式（`50(?:\.0+)?`），合法重排不会误判红；而「真改数值」（50.0 → 99.0）
仍判红。这条边界由两个测试从两侧夹住。

### 3.3.1 ★ t8 的「类别修复」：两层分工，谁都不能替代谁

t7 复核后暴露出**同一根因的残余**：正则绑字面这条路**永远绑不完**。主理人实测两份产物
的标量叶子数为 **directed 1892 / timing 429**（本票复算一致），而 `criteria` 只占其中
一小部分，其余在 `overall` / `by_subset` / `criteria_registry` / `negative_controls` /
`rounds` …。**上一轮补 `observed` 漏了 `overall`，这轮补 `overall` 还会漏 `by_subset`。**

故 t8 做两层，**分工写死**（★ t10 加入 `parse_as` 后此分工**仍成立**，见末行）：

| 层 | 守什么 | 跑在哪 | **不**守什么 |
|---|---|---|---|
| **gate**（`config/drift-contract.json` + `scripts/drift_gate.py`） | fail-closed **存在性**（`present_when_missing`）＋ **解析完整性**（`parse_as="json"`，§3.2.1）＋ **决策承载字面**（两 variant 的判据读数、S1–S5 结构计数、`overall.cost_index` 等主指标的 `per_round`/`median`） | `drift-gate` job（**独立于 pytest**） | 全量叶子（正则有上限，做不到）；**内容被改但仍是合法 JSON** 的情形（W2） |
| **pytest**（`scripts/tests/`） | **全量内容绑定**：canonical（`sort_keys=True`）sha256，覆盖**每一个叶子**；外加门禁表达不了的结构不变量（两 variant 重算一致性、`criterion_id` 唯一性、verdict 合法集合） | 既有 `scripts-tests` job | 无（它不需要理解语义） |

★ **不得声称其中一个能替代另一个**。**两条活证据**：
1. **门禁挡得住、摘要层也挡得住，但只有门禁在 CI 里独立跑**：把
   `by_subset.open-book.*.per_round[0]` 篡改（门禁**不**绑的叶子）⇒
   **门禁 rc=0（绿）而 canonical 摘要判红**；
2. ★ **W2 是更锋利的反例**：伪造 `criteria_registry` 后**门禁 rc=0**、**摘要层判红**
   （§6.5 W2 实测）。即「摘要层能拦而门禁拦不住」确实存在 ——
   若谁声称「门禁已足够」，W2 就是反例；反之若只靠 pytest 而无独立门禁，
   则 pytest 没跑时截断产物会过线（W1）。**两层各自兜住对方兜不住的那一类。**
★ `parse_as` 只加在 **gate** 侧，且**不改动**摘要层职责：它修的是「**解析失败**」（W1），
与「解析成功但字段被改」（W2，只有摘要层守）**正交**。


★ **`sort_keys=True` 是承重的**：它让摘要对**键序与缩进不敏感**。t7 的 V4（把产物按
`sort_keys + indent=4` 重写、**值逐字不变**）曾被旧门禁误判红（rc=1）；canonical 摘要在
该变换下**不变**，且门禁层也已改为对排版不敏感（见 §3.3.2）。

### 3.3.2 ★ t8 修掉的三类「同根因残余」（t7 各条 → 实测 rc）

| t7 条目 | 变异输入 | 改前 | 改后 |
|---|---|---|---|
| S4 三个计数键从未被读 | `quiet_rows_unattributable/_empty_output/_contradictory` 0→7（`quiet_rows` 不动） | **0** ❌ | **1** ✅ |
| ★ 主指标无绑定 | `overall.cost_index.median` 82.4→0.0（V1 与 V2 各测） | **0** ❌ | **1** ✅ |
| V4 | 按 `sort_keys+indent=4` 重写（**值不变**） | **1** ❌ 误伤 | **0** ✅ |
| V3 | `not_pattern` 大小写/冒号空白 | `"Unmeasurable"`/`"UNMEASURABLE"` **0** ❌ | **1** ✅ |
| V3 | 追加重复 `criterion_id`（末次写入优先） | **0** ❌ | 结构层判红（摘要 + 唯一性） ✅ |
| — | `by_subset` 深叶子篡改（门禁**不**绑） | 0 | 门禁 0 / **摘要红** ✅（分工证据） |
| V6 | `rm -f` + 原地写在卡片失败时毁工作区 | 产物被重写/删除 ❌ | **失败后入库产物 md5 逐字节不变** ✅ |

**★ 三类根因（t8 的实质修复，不是补字面）：**

1. **跨副本别名（cross-copy aliasing）** —— 同一个字面在产物里出现多次，宽松断言会被
   **另一处副本**满足，于是真字段被改也判绿。实测到的别名：
   `criteria_registry` 会**重复** `criterion_id` 与 `threshold`（`"threshold": 63.0`
   共出现 **4** 次）；`S3` 与 `S4` 都含 `"quiet_rows": 54`；`n_speaking` 在
   `T_SAMPLE_FLOOR` 内出现两次（真块 + 嵌套的 `premature_scope`）；`median` 在多个
   `overall` 指标里重复。
   ⇒ 对策：**锚在拥有者上 + 要求一个别名没有的字段**（registry 没有 `observed`）、
   且每个窗口**不得跨过下一条 `criterion_id`**（必要时再加显式 stop）。
2. **排版耦合** —— 区域顺序/缩进不是不变量：`sort_keys` 会把
   `"P2_live4_prod_prompt_profile"` 排到 `"P_live4_prod_prompt"` **之前**，故
   「V1 段以 V2 的 card key 为界」这类区域写法必然误伤合法重排。⇒ 对策：断言一律锚在
   **自己的 id 上**、字段之间**顺序无关**（共享起点的嵌套先行断言），变体靠**取值**
   区分（D1 50.0/34.6、D4 0.0/11.1、D5 82.4/62.7），取值相同的判据则加**命中两处**的
   计数约束。
3. **锚点本身也要不变量** —— canonical 形式下 `"cards"` 排在 `"kind"` **之前**，
   用 `"kind": "..."` 当文档起点锚会让整条断言落到所有判据**之后**，于是 pattern
   静默 0 命中（实测 4 条全 raw=1/canon=0）。⇒ 对策：锚在**执行器自己写的
   `--- <path> ---` 头**上（`drift_gate.merge_paths` 恒先输出），它与排版无关。

★ **性能仍须守住**：所有量词都有界；`[\\s\\],}]*` 这类**无界**收尾与外侧 gap 组合是
O(n²)，实测把门禁挂死到 **>120 s 被杀**（t8 开发期真的踩了一次）。修成有界后最坏
**0.09 s**，由 `test_patterns_stay_fast_when_every_assertion_fails` 钉住。

### 3.4 阈值出处：三层，各自可核验

`doc/standards/test-baseline.md` 的硬约束是「阈值必须是有出处的数字」。本票的溯源链是：

| 层 | 内容 | 谁守着 |
|---|---|---|
| 1. 冻结快照 | `BASELINE_SNAPSHOT`（绑产物 sha256）/ `TIMING_BASELINE_SNAPSHOT` | 卡片自己的 `--verify-bounds`；**不从活产物派生**，否则「退化 + 重跑」会把线一起挪走 |
| 2. 阈值声明 | `BOUNDS` / `TIMING_BOUNDS` | `scripts/tests/test_eval_gate_contract.py`：**逐条**与产物里每条判据的 `threshold` 比对，**分叉即测试红** |
| 3. 门禁断言 | 契约 `pattern` 里的字面数字 | `test_contract_pattern_matches_committed_artifact`：在真实提交的产物上必须真的命中 |

★ 第 2 层是把「阈值有出处」从**文档承诺**变成**可执行断言**的那一步。实测（本票开发期）：
把 `BOUNDS["nondirected_spurious_response_rate_pct"]` 从 63.0 人为改成 64.0，
`test_directed_artifact_thresholds_match_card_bounds` 与
`test_every_directed_threshold_is_grounded_in_bounds` **立刻判红**；还原后转绿
（★ 那时该文件只有 22 条用例，故早期记录写作「22/22」；**用例数已随本轮增长，别引用** ——
判据是 `-k threshold` 这组用例的 **rc=0**，
取证：`python -m pytest scripts/tests/test_eval_gate_contract.py -q -k threshold`）。

#### 3.4.1 ★ AC4 的可追溯性：**四条腿里有一条目前是空的**（如实登记）

AC4 的要求是「让阈值出处**可被自动化追溯**」。当前状态**不是四条腿都成立**：

| 腿 | 内容 | 当前状态 |
|---|---|---|
| 1. 卡片 `BOUNDS` / `TIMING_BOUNDS` | 阈值的**代码真值源** | ✅ **成立** |
| 2. 冻结快照（绑产物 sha256） | 阈值不随活产物漂移 | ✅ **成立** |
| 3. 跨层绑定测试 | 产物 `threshold` ↔ `BOUNDS` 逐条比对，分叉即红 | ✅ **成立** |
| 4. **决策书条目** | 契约 4 条 eval-* 的 `decision_ref` 写着「决策书：`决策/跨域铁律.md`『Drift Gate 门禁』+ `决策/业务-评测.md`」 | ❌ **目前是空的** |

★ **第 4 腿的实测事实（本次复跑，非转述）**：把这两个文件读出来数关键词 ——

```
决策/跨域铁律.md   17951 B / 12571 chars  →  '63.0' 0  '17.0' 0  '101.0' 0
                                              'cost_index' 0  '记分卡' 0
                                              '定向轴' 0  '时序轴' 0
                                              （但 'Drift Gate' 命中 4 次 —— 门禁机制本身有）
决策/业务-评测.md   2171 B / 1578 chars   →  上述关键词**全部 0 命中**
```

⇒ 这两个文件当前**只覆盖门禁机制本身**（`Drift Gate 门禁` 条目），
**不含**本票的阈值、判据 id 或记分卡口径。
故 `decision_ref` 里那句「决策书：…」目前指向的是**机制出处**，
**不能**被读成「这些阈值已在决策书里可追溯」。

★ **为什么这条腿是空的、以及它何时才成立**：按 `决策/README.md` §0.1，
`决策/` 只能由**用户批准后**落盘。本票需要的那条 SSOT 提案文本已起草在 **§8**，
**尚未获得用户批准**。⇒ **第 4 腿要等 §8 提案经用户批准、真正写入 `决策/` 之后才成立。**

★ **不得**把当前状态写成「已在决策书里可追溯」。复现命令：

```bash
python - <<'PY'
from pathlib import Path
for f in ("决策/跨域铁律.md", "决策/业务-评测.md"):
    t = Path(f).read_text(encoding="utf-8")
    print(f, {k: t.count(k) for k in ("63.0", "17.0", "101.0", "cost_index", "记分卡")})
PY
```

### 3.5 两轴阈值清单（入库读数，全部固定在契约里）

**定向轴**（`decision_eval_criteria.BOUNDS`；阈值 = 跨 variant 的 `ceil(median + pstdev)` 最大者）

| 判据 id | 指标 | 方向 | 阈值 | observed (V1 / V2) | verdict (V1 / V2) |
|---|---|---|---|---|---|
| `D1-nondirected-no-spurious` | `nondirected_spurious_response_rate_pct` | ≤ | 63.0 | 50.0 / 34.6 | pass / pass |
| `D2-directed-nonresponse` | `directed_nonresponse_rate_pct` | ≤ | 21.0 | 12.0 / 12.0 | pass / pass |
| `D3-not-for-me-precision` | `not_for_me_precision_pct` | ≥ | 70.0 | 100.0 / 100.0 | **unmeasurable** / pass |
| `D4-not-for-me-recall-generalization` | `not_for_me_recall_pct` | ≥ | 17.0 | 0.0 / 11.1 | **fail / fail** |
| `D5-cost-index` | `cost_index` | ≤ | 101.0 | 82.4 / 62.7 | pass / pass |

结构性判据 `S1`–`S5`（分母齐备 / 无失败行 / token 证据完整 / 无可归因缺失 / ≥2 轮）：两 variant 全 pass。

★ **D4 未达标是如实读数，不是接线缺陷**：两个 variant 的泛化子集召回分别是 0.0 与 11.1，
都低于 17.0 的线。本票**有意把这已知红固定在契约里** —— 修 D4（改 prompt / 改判据）
是一条独立工作，届时必须**显式更新契约断言**，不得让它在无人察觉时变绿。
★ `D3` 在 V1 上判 `unmeasurable`（分母退化：`not_for_me_predicted` 逐轮 `[1, 0, 0]`），
在 V2 上判 `pass` —— 两 variant 形状不同，故契约**分别**绑定。

**时序轴**（`decision_eval_timing_criteria.TIMING_BOUNDS`）

| 判据 id | 指标 | 方向 | 阈值 | observed | verdict |
|---|---|---|---|---|---|
| `T_ONSET_MEDIAN` | `onset_latency_ms` | ≤ | 898.0 | 573.0 | pass |
| `T_ONSET_P90` | `onset_latency_ms` | ≤ | 1120.0 | 731.0 | pass |

结构性判据 `T_LATENCY_SOURCE` / `T_ONSET_MEASURED` / `T_NO_COMBINED_ACCURACY` /
`T_SPURIOUS_TIMEBASE` / `T_PREMATURE_MEASURED` / `T_SAMPLE_FLOOR`：全 pass（**阈值均为 null** ——
它们是「这份测量是否成立」的出处/样本量判据，不是计量判据）。

契约另对时序卡设一条**反断言** `not_pattern`（`(?i)"verdict"\s*:\s*"unmeasurable"`）：
「没测」与「测到 0」必须区分，缺测时判红而不是放行。

#### 3.5.1 ★ `not_pattern` 的**已知不对称**（如实登记；**是否补齐留待用户决定**）

实测（本次复跑，`config/drift-contract.json`）4 条 eval-* 的 `not_pattern`：

| check | `not_pattern` |
|---|---|
| `eval-directed-axis-frozen-reading` | **无**（`None`） |
| `eval-directed-axis-structural-guards` | **无**（`None`） |
| `eval-timing-axis-frozen-reading` | **无**（`None`） |
| `eval-timing-axis-structural-guards` | `(?i)"verdict"\s*:\s*"unmeasurable"` |

而**定向卡确实含一条诚实的 `unmeasurable`**（实测：`P_live4_prod_prompt` /
`D3-not-for-me-precision` 的 `verdict == "unmeasurable"`；两张卡里 `"unmeasurable"`
字样出现次数：定向卡 **1**、时序卡 **0**）。

⇒ **不对称的实质**：时序轴有「缺测判红」的守卫，**定向轴没有** ——
若定向卡将来**多出**一条 `unmeasurable`（例如 D1/D2/D5 的分母不足），
时序轴那条反断言会拦，**定向轴两条 check 不会**。

★ **为什么本票没有顺手补齐**：给定向轴加 `not_pattern` 会**改动契约的判定口径**
（`not_pattern` 命中即判红）。定向卡当前有一条**已知、已登记**的诚实 `unmeasurable`（D3），
补守卫需先确认「那条要不要一起判红」—— 这属**判据口径决定**，不是接线工作。
⇒ **本票不擅自改 `config/drift-contract.json`**；**是否给定向轴补对称守卫，留待用户决定。**

★ 本票**做了什么**：把这个不对称**如实登记**在案（本节 + §7 遗留第 8 条），
且**没有**任何断言声称为对称 —— 现状就是「一侧有一侧无」。

### 3.6 CI 接线的退出码取舍

卡片退出码：`0` 全绿 / `1` 有判据判红 / `2` 有「无法测量」项。
生成步骤**不**用 `set -e`，而是逐码分辨：

| 退出码 / 情形 | 处置 |
|---|---|
| `0` 全绿 | 放行，把**判定权交给 `drift_gate`**（它按产物内容判红/判绿） |
| `1` 有判据判红 | 放行，同上（D4 已知红固定在契约里；判定权在门禁，不在生成步骤） |
| `2` 有「无法测量」项 | ★ **失败**（`::error::` ＋ exit 1）——「没测 ≠ 通过」，不得放行 |
| 其它（如崩溃 rc=9） | **失败** —— 这**不是一个读数**，卡片未正常产出 |
| 产物缺失 / 为空 / 不可解析 / `kind` 不对 | **失败** |

★ 这样「生产命令失败但有产物」与「压根没产物」**可被区分**，且评测的判红**没有被吞掉**：
D4 不达标仍由 `drift_gate` 以契约断言的形式判红并拦住合并。

★ **R6 修复（t6）—— 「卡片从未产出结果而全绿」**：原实现只检查「文件存在且可解析」，
而卡片在**无法出卡**时 rc=2 且**一个字节都没写** ⇒ **旧产物**被当成新产物 ⇒
步骤 rc=0、门禁 rc=0、CI 全绿，而卡片其实什么都没测（t4 实测）。
现在生成前先 `rm -f` 两个目标产物，故「卡片没写出东西」⇒ 文件不存在 ⇒ 步骤失败；
顺带消掉「旧产物被当成新产物」这一整类问题。rc=2 也从「放行」改为「失败」。
该步骤的行为由 6 个场景钉住（`run_ci_step` 系列测试，含「rc=2 且不写文件」
与「写空文件」两条负控）。

★ **V6 修复（t8）—— 「失败时毁工作区」**：`rm -f` + **原地写**虽然关掉了 R6 的
fail-open，却引入了新问题：**卡片失败时入库产物已被破坏**（t7 实测：卡片失败时
STEP-RC=1，而定向卡已被重写、时序卡被删）。主理人也踩过（抽该 step 真跑，删掉了入库
timing 卡，靠 `git checkout-index -f -- <path>` 恢复）。
现改为**临时目录生成 + 成功后原子替换**：

1. 卡片只写 `$STAGE`（`mktemp -d "${RUNNER_TEMP:-/tmp}/eval-cards.XXXXXX"`）；
2. 校验**暂存**产物（非空 / 可解析 / `kind`+`ticket` 对得上）；
3. **全部通过后**才 `mv -f` 到入库路径（同一文件系统内 `mv` 是原子的）；
4. 任何失败路径都打印「入库产物未被改动（本步骤只写暂存目录）」并 `exit 1`。

★ **踩坑记录（如实）**：本步骤是 `set -u` 而非 `set -e`，故第 2 步 heredoc 里的
`sys.exit(1)` **不会**自动中止脚本 —— 不显式取 `$?` 的话，校验判红后仍会执行 `mv`，
把坏产物盖到入库路径上。t8 开发期真的写出了这个 bug，被
`test_failure_leaves_committed_artifacts_byte_identical`（空产物场景 rc=0 且产物被替换）
当场抓出，已修为显式 `if [ "$?" -ne 0 ]`（并由 `step_script` fixture 断言 `mv -f` 存在）。
**判据**：失败后入库产物**仍在、md5 逐字节不变** —— 该测试对 rc=2 / rc=1 / 崩溃 rc=9 /
静默 rc=0 四种失败各验一次。

★ **内容指纹进日志（t8 / AC#5）**：校验时打印每个产物的 **sha256**（不再只有 `bytes=`），
使「同一次 CI 内生成 → 断言」的那个对象事后**可核验** —— 拿到指纹就能确认被门禁断言的
到底是哪一份产物。测试同时断言日志里的 sha256 **与实际产物内容一致**（防印假指纹）。

★ **CI 会重新生成产物 ⇒ 本地手工改坏的产物在 CI 里会被洗掉**：这是**设计使然** ——
门禁守的是「**产物 + 契约**」这一对，而 CI 每次都从卡片重算产物。所以：
- 本地伪造产物**能**被本地门禁抓到（这是防「手滑改错」的机制）；
- 但它**不会**在 CI 复现（CI 的产物是新的，内容正确）。
- **别把 CI 当成「本地伪造的复现器」** —— 想守「产物与卡片一致」，靠的是 §3.4 的跨层绑定
  测试，不是 CI 重跑。

---

## 4. Harness（可复现工作流 / 验证仪式）

### 4.1 生成产物（本地命令；CI 逐字相同，只有解释器不同）

```bash
# 本地（本机 venv 解释器；裸 python 是 Windows Store 存根，会静默失败）
/d/AI/envs/joyai-main/python.exe services/webinfer/decision_eval_card.py \
    --json --out doc/research/data/decision_eval_directed_card.json
/d/AI/envs/joyai-main/python.exe services/webinfer/decision_eval_timing_card.py \
    --json --out doc/research/data/decision_eval_timing_card.json

# CI（ubuntu + py3.12）：同一命令，解释器就是 python
python services/webinfer/decision_eval_card.py \
    --json --out doc/research/data/decision_eval_directed_card.json
python services/webinfer/decision_eval_timing_card.py \
    --json --out doc/research/data/decision_eval_timing_card.json
```

实测退出码：定向轴 **1**、时序轴 **0**（见 §3.5：D4 未达标）。两卡**各约 0.2 s，完全离线**。
两张卡都自带 `newline="\n"` 行尾纪律。

★ **「逐字节一致」的适用边界（t6 按 R7 更正）**：实测重跑一致**仅限同机、同检出路径**。
原因是入库时序卡内嵌了**本机绝对路径**（`events` / `truth`，实测 4 处）⇒ 换机器或换检出目录
后这些字段必然不同。跨机/跨路径比对必须**先排除路径字段**再比（细节与后续票见 §6.3 R7）。
在**同机同检出**下，产物既可入库冻结、也可在 CI 现场重生成，两者不冲突。

### 4.2 跑门禁（本机与 CI 等价）

```bash
cd /d/AI/workspace/JoyAI-VL-Interaction-main
/d/AI/envs/joyai-main/python.exe scripts/drift_gate.py \
    --contract config/drift-contract.json --phase static --mode closed --no-history
# 健康态期望 rc=0
```

### 4.3 验证仪式（本票逐条真跑过）

★ 下表是**截至 2026-09-23（t13 收口）实跑**的读数，每条都注明取证命令。
**数字会随本轮各票推进而变** —— 复现时请**自己跑一遍**，不要引用本表的数（本票已多次
因「写死的数字分叉」付费：①本次复跑发现任务书给的「13087 字节」实为**字符**偏移，见 §6.5 W1；
②`pytest` 总数在 t11→t13 之间又从 265 涨到 **276**，故本表一律写成「**截至某时点实测 N**」
的形态，而不是宣称它是一个稳定常量）。

| # | 命令（取证） | 截至 2026-09-23 t13 实测 |
|---|---|---|
| ① | `python services/webinfer/decision_eval_card.py --json --out doc/research/data/decision_eval_directed_card.json` | rc=**1**（如实读数：D4 未达标）；产物 **97956 B**，md5 `97437757ad92fadef0f276e57355b338` |
| ② | `python services/webinfer/decision_eval_timing_card.py --json --out doc/research/data/decision_eval_timing_card.json` | rc=**0**；产物 **31467 B**，md5 `f3126cb4cffd4a60136c9a26e14b5188` |
| ③ | `python -m pytest scripts/tests/ -q` | **276 passed**, rc=0 ★ **会随票演进**，别引用这个数；以你自己的复跑为准 |
| ④ | `python scripts/drift_gate.py --contract config/drift-contract.json --phase static --mode closed --no-history` | rc=**0**；`total=14 block_fail=0 warn_fail=1`；逐条 **13×`[OK]`**（= 9 条既有 static + 4 条 eval-*）＋ **1×`[WARN]`**（`webui-joywiki`，既有、逐行不变） |
| ⑤ | `python scripts/run_ci_ruff.py` | **14/14** CI ruff steps pass |
| ⑥ | `python scripts/drift_gate_smoke_test.py` | `OK all smoke checks passed` |

★ 「既有 10 条 static check 判定逐条不变」的取证方式是**逐行比对**该命令输出
（9×`[OK]` + 1×`[WARN]`，共 10 条），而不是只看总 rc —— 总 rc 绿并不排除某条判定被换掉。

★ **为什么只给「截至时点」而不是「当前值」**：`pytest` 的总数取决于**收集到多少个用例**，
而本轮每张票都可能增删用例 ⇒ 写死它必然分叉。**要判定测试是否全绿，看 rc 而不是看计数**：
`python -m pytest scripts/tests/ -q` 的退出码为 0 才是判据。

### 4.4 负控（★ 本票的核心取证）

「健康态判绿」**不证明**门禁有效 —— 一条永不匹配的正则同样会让健康态判绿。
故必须有负控，且负控要能区分「哪条 check 判红」：

| # | 变形 | 期望 | 实测 |
|---|---|---|---|
| NC1 | 移走定向轴产物 | rc=1，仅 2 条定向轴 check `[BLOCK]`；时序轴仍 `[OK]` | ✅（其余不动 ⇒ 证明两轴**各自**被读） |
| NC2 | 移走时序轴产物 | rc=1，仅 2 条时序轴 check `[BLOCK]`；定向轴仍 `[OK]` | ✅ |
| NC3 | 时序轴 `observed` 573.0 → **5000.0**（越过 898 的线） | rc=1，仅 `eval-timing-axis-frozen-reading` | ✅ |
| NC4 | 定向轴 V1 结构性判据 `S1` verdict `pass` → `fail` | rc=1，仅 `eval-directed-axis-structural-guards` | ✅ |
| NC5 | 产物被换成非记分卡内容（`kind` 不对） | rc=1（门禁）+ CI 生成步骤 `::error::` | ✅ |
| NC6 | `BOUNDS` 63.0 → 64.0（阈值分叉） | 跨层绑定测试**判红** 2 条 | ✅ |
| NC7 | 全量变异矩阵（41 例：逐判据 verdict/observed/reason 变体 × 两 variant × 两轴） | 全部按期判红，健康态判绿 | ✅ 41/41 |

★ NC1/NC2 是 `present_when_missing="fail"` 的**直接取证**：改之前同一场景产出
`[SKIP] ... fail-open` 并**判绿**（rc=0）。
★ NC3/NC4/NC7 是「注入明显退化即判红」的取证。
★ 所有负控**恢复方式都是重跑卡片命令**（同机同检出已验证逐字节确定），故不可能把工作区留脏。

#### 4.4.1 t6 修复批次的负控（对抗复核绕过 → 逐条判红）

t4 对抗复核构造出 5 个「门禁 + 作者测试**双双判绿**」的绕过。下表每行的
「改前 rc」都是**实测**值，不是推断；「改后」由 `scripts/tests/test_eval_gate_contract.py`
的对应测试钉住（**篡改一律在 `tmp_path` 镜像里做，入库产物零改动**）。

| # | 变形 | 改前 rc | 改后 rc | 钉住它的测试 |
|---|---|---|---|---|
| R1 | V1 `S1.observed` `{75,78}` → **`{0,0}`**（verdict 仍 pass） | **0** ❌ | 1 ✅ | `test_structural_observed_is_actually_read`（6 例参数化） |
| R1 | V2 `S1.observed` `{75,78}` → **`{0,0}`** | **0** ❌ | 1 ✅ | 同上（独立期望值） |
| R1 | `S3.rows_without_token_evidence` 0 → 9 | **0** ❌ | 1 ✅ | 同上 |
| R1 | V1 / V2 `S3.quiet_rows` 54→0 / 59→0 | **0** ❌ | 1 ✅ | 同上 + `test_two_variants_have_independent_structural_expectations` |
| R1 | `S5.rounds_count` 3 → 1 | **0** ❌ | 1 ✅ | 同上 |
| R1 | 时序 `T_SAMPLE_FLOOR` `n_speaking` 21→2 / `n_missing_stamp` 0→5 | **0** ❌ | 1 ✅ | `test_timing_sample_floor_is_actually_read` |
| R2 | 定向卡 V1 / V2 **卡片级** `criteria.verdict` fail → pass | **0** ❌ | 1 ✅ | `test_card_level_criteria_verdict_is_read`（V1/V2） |
| R2 | 时序卡卡片级 verdict pass → fail | **0** ❌ | 1 ✅ | `test_timing_card_level_verdict_is_read` |
| R3 | 时序卡诚实 `unmeasurable`（单空格 / **双空格** / **制表符**） | 单空格 1；另两种 **0** ❌ | 三者均 1 ✅ | `test_not_pattern_is_whitespace_insensitive` |
| R5 | 产物 `threshold` 63.0→999.0 / 898.0→9999.0 | **0** ❌ | 1 ✅ | `test_threshold_literal_is_bound_in_contract` |
| R4 | `observed` 50.0 → 50.00（**值不变**） | 0 | **0**（正确：不误伤） | `test_value_equivalent_spelling_does_not_false_red` |
| R4 | `observed` 50.0 → 99.0（真改值） | 1 | 1 ✅ | `test_real_value_change_is_still_red` |
| R6 | 卡片 rc=2 且**不写文件**（旧产物在场） | **0** ❌ | 1 ✅ | `test_rc2_without_artifact_fails` |
| R6 | 卡片 rc=0 却不写文件（**证明 `rm -f` 承重**） | **0** ❌ | 1 ✅ | `test_silent_rc0_without_writing_fails` |
| R6 | 卡片写 **0 字节**文件后 rc=0 | — | 1 ✅ | `test_empty_artifact_fails` |
| R6 | 卡片崩溃 rc=9 | — | 1 ✅ | `test_unexpected_exit_code_fails` |
| perf | 末条判据退化 ⇒ 全部断言失败 | **>120 s**（挂起） ❌ | 0.13 s ✅ | `test_patterns_stay_fast_when_every_assertion_fails` |

★ **反假阳性对照（每条修复都必须同时满足）**：健康态 rc=0 / 4 条 eval-* 全 `[OK]` /
既有 10 条 static 判定不变（**9×`[OK]` + 1×`[WARN]` webui-joywiki**）/ 全量 pytest 绿。
上表所有「改后」行都在这个前提下取得。

★ **`rm -f` 承重性的独立取证**（不是推理）：把 `rm -f` 从步骤副本里删掉后，
「卡片 rc=0 且不写文件、旧产物在场」场景 **rc 从 1 变回 0** —— 那正是 t4 的 fail-open。
脚本见 `.cache/t6-repro/prove_rmf.py`（scratch，不入库）。

★ **性能为何也算负控**：挂起的门禁**比判红更坏** —— CI 会超时而不是给出漂移报告，
「无报告 = 无人能定位」。故把最坏情形（全不匹配）的耗时也钉成断言。

---

## 5. 验收 / 排除

- **验收判据**（对应 #159 的 AC）：
  1. 契约新增 4 条评测 check，复用既有 schema 与**同一个**执行器；无第二个执行器/退出码体系。
  2. 产物落在入库的稳定路径（`doc/research/data/`，非 gitignore），门禁读该路径。
  3. ★ 缺结果判红：移走任一产物 ⇒ `rc=1`。
  4. ★ 注入明显退化 ⇒ `rc=1`。
  5. 健康态 `rc=0`，且既有 static check 判定逐条不变（9×`[OK]` + 1×`[WARN]`；`vlm-n_ctx` 是
     runtime，`--phase static` 不跑 ⇒ 实际跑 **10** 条，另 4 条评测 check，共 **14**）。
  6. 阈值有出处：★ **三条实证腿成立**（卡片 `BOUNDS` + 冻结快照 sha256 + 跨层绑定测试，
     分叉即红）+ 文档可见（`doc/standards/test-baseline.md`，本轮已更正到真值）。
     ★ **第 4 条腿（决策书条目）目前是空的** —— 见 §3.4.1：`decision_ref` 指向的
     `决策/跨域铁律.md` / `决策/业务-评测.md` 实测**不含**这些阈值，
     该腿要等 §8 提案**经用户批准**后才成立。**本条不得读作「已在决策书里可追溯」。**
  6.1 ★ **执行器被扩展过**（t1 `present_when_missing` / t10 `parse_as` + 反崩溃）——
     如实记录见 §2.1，含「与 AC1『复用』字面之间的张力」；**不得**读作「执行器一行未改」。
  6.2 ★ **`not_pattern` 存在已知不对称**（时序有、定向无）—— 见 §3.5.1；
     是否补齐**留待用户决定**，本票不擅自改契约口径。
  7. 两条结果文件**各自**被读取（NC1/NC2 分别取证）。
  8. 接线沿用既有先例形态；phase 在 CI 可跑（本地/CI 一致）。
  9. spec 落盘（本文件）+ 索引登记 + 如实说明未写入 `决策/`。
  10. ★ **内容不完整判红**（t10 补，见 §3.2.1 / §6.5 W1）：产物存在但内容不可解析
      （截断 / 非 JSON / 极深嵌套）⇒ `rc=1`，**不得**因「文件存在」而放行。

- **明确排除**（不属本 spec）：
  - **修 D4 未达标**（改 prompt / 改判据口径）—— 独立工作，本票只把它固定在契约里。
  - **补齐 `user_still_speaking_at_decision` 写入点**（`T_PREMATURE_MEASURED` 在真机必然
    「无法测量」的根因）—— 属 #158 遗留的接线工作。
  - **`T_ONSET_MEDIAN` / `T_ONSET_P90` 在真机样本不足时仍出定量结论**（`test-baseline.md`
    记录的 D2 缺陷）—— 属卡片判据本身的口径问题，不在门禁范围。
  - **`--require-latency` 式的卡片侧开关** —— 本票只做读取侧。
  - ★ **W2（`criteria_registry` 伪造）** —— **门禁层不拦**，唯一守卫是摘要层（§6.5 W2）。
    **本票不声称已修**；真正堵住需把 registry 纳入契约 pattern。
  - ★ **W3 的根因（`_is_all_missing()` 内容嗅探）** —— 本票只补端到端测试 + 登记局限，
    `_is_all_missing` **未改**（§6.5 W3）。**本票不声称已修。**
  - bug 修复 / 运维操作 / 起服务 runbook 不属 spec（见 `决策/spec编写规范.md` §3）。

---

## 6. ★ 如实记录的局限

### 6.1 门禁信任产物自报的判定（最主要的局限）

契约只能断言**产物里已经算好的字段**（`observed` / `verdict` / `reason`）。
执行器**没有数值比较能力**，故门禁**不能**独立地重算 `observed <= threshold`。

**后果**：产物若被**整体**伪造（判据数字与 verdict 一起改成一自洽的、看起来达标的样子），
契约的正则会跟着命中，门禁判绿。即**门禁的强度上限 = 卡片的诚实度**。

**为什么它仍满足「阈值有出处」**：

1. 阈值不是门禁发明的 —— 它们由卡片的 `BOUNDS` / `TIMING_BOUNDS` 提供，而后者由
   **冻结快照**派生（绑产物 sha256），不随活产物漂移。产物同时携带 `threshold` 与
   `observed`，故**人工与测试都可以交叉核对**。
2. **跨层绑定测试**独立地把产物里的每个 `threshold` 与 `BOUNDS` 逐条比对（§3.4）：
   只改一侧（改线不重跑产物 / 手改产物数字）**一定**判红。分叉保护是真实存在的。
3. 门禁要挡的是**无意的质量退化**（改了 prompt / 改了链路 ⇒ 重跑产物 ⇒ 数字变差）。
   这类退化会让 `verdict` 与 `reason` 一起变，契约**会**判红。它挡不住的是**蓄意伪造
   一份自洽产物** —— 那属代码评审与 `git diff` 的职责，不是门禁的职责。
4. 把「重算」也做进门禁需要让执行器获得数值语义（§1 已否）；正确的下一步不是扩执行器，
   而是让卡片输出一个**门禁专用结论字段**（§7 遗留）。

### 6.2 内容摘要 / 阈值溯源到哪一层

| 项 | 溯源层级 |
|---|---|
| 阈值**是不是**有出处 | 可自动核验：跨层绑定测试比对产物 `threshold` ↔ 卡片 `BOUNDS` |
| 阈值**该不该**是这个数 | **不可**自动核验：依赖 `BASELINE_SNAPSHOT` 的冻结与卡片 `--verify-bounds`；门禁只保证它**没被悄悄改**，不保证它**取得对** |
| `observed` 数值本身 | 依赖卡片读取路径（#155 冻结输入 / #156 决策落盘）的诚实度；门禁不重算 |
| D4「已知红」的接受 | **人的判断**（#157 遗留），已固定在契约里待显式更新 |

### 6.3 ★ 对抗复核登记的三项局限（**只登记，本票不修**）

以下三项由 t4 对抗复核查出、经主理人独立复现。**本票不修** —— 每项都写清
「现象 / 为何不修 / 后续票」，并**明确声明本票不声称已修**。

**R4 —— 数字拼写的伪造盲区（medium）**

- **现象**：`"observed": 50.0` 与 `50.00` 是**同一个 JSON 值**。改前契约绑字面
  ⇒ 合法重排会让门禁**静默判红**（误伤）；而 t6 改为值等价（`50(?:\.0+)?`）后，
  理论上仍可用「与自身一致的伪造拼写」蒙混（即改判据数字而不触发断言）——
  这是一个**正规模糊地带**：正则做不到「语义等价」的完整判定。
- **为何不修**：把「值等价」做到完备要引入数值语义（§1 已否：执行器是
  `re.search`）。且**真正的兜底不在门禁**，而在那条跨层 BOUNDS 绑定测试
  （`test_directed_artifact_thresholds_match_card_bounds` 等）——它读 `json.loads`
  后的**数值**，拼写无关。
- **分工（重要）**：**门禁挡「产物被改」**（结构 + verdict/reason + threshold 字面），
  **离线测试挡「线与卡片分叉」**（数值级比对）。缺一不可，不要指望任一方单独兜住。
- **现状**：t6 已从两侧夹住该边界（`test_value_equivalent_spelling_does_not_false_red`
  证不误伤，`test_real_value_change_is_still_red` 证真退化仍红）。
- **后续票**：见 §7 第 1 条（门禁专用结论字段）。

**R5 —— 改 `threshold` 时门禁绿、唯一拦截者是离线测试（medium）**

- **现象**：t4 实测改 `threshold` 后门禁判绿，只有离线测试
  （`test_every_directed_threshold_is_grounded_in_bounds`）会红。
- **为何部分不修**：t6 已把 `threshold` **字面**绑进契约 pattern（改字面即判红，
  见 `test_threshold_literal_is_bound_in_contract`），所以「改产物里的阈值字面」
  现在**门禁自己就能抓**。但「阈值该不该是这个数」仍**不可**由门禁判定 ——
  那需要基线判断，属人的职责。
- **职责边界（写清以免误解）**：**门禁** = 产物自洽性与读数一致性；
  **离线测试** = 阈值与卡片 `BOUNDS` 的分叉。R5 描述的那类「线被挪动」由测试守。
- **后续票**：无（现有分工足够）；若要让门禁也参与，需 §7 第 1 条的结论字段。

**R7 —— 入库时序卡内嵌绝对路径 ⇒ 「逐字节一致」仅同机成立（medium）**

- **现象**：`doc/research/data/decision_eval_timing_card.json` 里 `events` / `truth`
  是**本机绝对路径**（`D:\AI\workspace\...\live_decision_timing_events.jsonl` 等，
  实测 4 处）。故本 spec 早先写的「重跑逐字节一致」**只在同一台机器、同一检出路径上成立**。
- **为何不修**：产物形态属 **#158 记分卡**（`services/webinfer/decision_eval_timing_card.py`）
  的输出契约，不在本票 inScope（本票是接线）；且改动它会改变已入库产物的字节。
- **★ 本票不声称已修**：spec 凡涉及「逐字节一致」处均已限定为**同机同检出**
  （§4.1）。跨机/跨路径比对必须**排除路径字段**后再比。
- **后续票**：建议让卡片输出**仓库相对路径**（或 `__file__` 相对解析后转相对），
  使产物真正可跨机冻结。**另立票**，由 #158 侧或新票处置。

### 6.4 已知的联锁影响（★ 已闭环，如实记录过程）

`scripts/tests/test_drift_gate_missing_result.py`（t1 的文件）里有一条**有意**的护栏
`test_existing_contract_checks_are_all_skipped_by_default`，它原先断言真契约里
**任何** check 都没写 `present_when_missing`。

**当时（本票接线期间）**：本票按要求给 4 条评测 check 加了 `"fail"`，故该断言会
**假红**（当时的读数是 `1 failed / 172 passed`）。

★ 这是**预期的联锁**，不是缺陷：该断言的 docstring 自己写明「这条测试会在那一票
把评测 check 接进来时**假红** —— 那时应由接线的人显式更新本断言并说明接的是哪几条」。
按归属纪律，本票**未改动**该文件（`doc/specs/` 属本票，该测试文件不属），
同步该断言的活由主理人路由回 t1 侧。

**现状（已由本轮 t5 闭环）**：该护栏已更新为 —— 带该字段的 check **id 列表必须与
接线清单逐条精确相等**（多一条少一条都判红），且清单内的值必须是 `"fail"`、
既有 check 一律**不写**该字段（缺省语义 `"skip"`）。

★ **全仓 `pytest scripts/tests/ -q` 的读数会随本轮各票推进而变**（截至 2026-09-23 t13 收口
实测为 **276 passed, rc=0**；t11 时为 265 —— **两个数都只是时点快照，别引用**，见 §4.3）。
**本段不复述一个会分叉的总数** —— 只看该护栏本身是否绿：
`python -m pytest scripts/tests/test_drift_gate_missing_result.py -q`（**判据是 rc=0**）。

★ **它仍是有意护栏，且比改前更严**（t5 附了把它真推红的负控
`test_guard_reds_when_an_unwired_check_is_given_the_field`）：改前只能表达「一条都没有」，
改后能抓住四类真阳性 —— ① 未接线的既有 check 被误加该字段（会给 CI 干净检出引入
整体变红）② 该接线的评测 check **漏接**（缺结果又判绿）③ 清单内值被改成 `"skip"`
④ 既有 check 的 id 集合被增删改名。

★ **纪律留痕**：`不得`为了让 pytest 变绿而删掉契约里的 `present_when_missing: "fail"`
字段、也不得删掉该护栏 —— 正确做法是**把断言改成精确清单**（t5 即如此处置）。

### 6.5 ★ t9 对抗复核的三项发现：真实归属（**本票只修了 W1**）

t9 复核出三项。**归属必须写清**，否则下一位读者会把「已知局限」当成「已修」。

#### W1 —— 截断产物：文件在、内容不是完整卡片 ⇒ 曾被判绿（**已修**）

- **现象**：时序卡完整 31467 B；截断到 16800 B（**53%**）后，其前缀**不是合法 JSON**，
  而门禁 **rc=0 全绿** —— 一份「半截产物」被当成读数放行。
- **★ 实测（本次复跑，非转述）**：同一份产物按前缀长度截断后跑**真执行器**：

  | 前缀 | 改前 rc（去掉 `parse_as` 模拟 t10 之前） | 改后 rc |
  |---|---|---|
  | 16761 B | 1 | 1 |
  | **16800 B** | **0** ❌ | **1** ✅ |
  | 20000 B | **0** ❌ | 1 ✅ |
  | 25206 B | **0** ❌ | 1 ✅ |
  | 31400 B | **0** ❌ | 1 ✅ |
  | 31467 B（完整） | 0 | 0 ✅ |

  取证命令（`.cache/t11-repro/verify_claims.py` 同款）：把产物截断写进 `$TEMP` 镜像，
  以 `--repo-root` 指向镜像跑 `scripts/drift_gate.py --phase static --mode closed`。
- **★ 读数单位的一处更正（如实记录）**：任务书给的最靠后观测点是「第 13087 字节
  （`T_ONSET_P`）」。本次复量发现 **13087 是字符偏移，不是字节偏移** ——
  该 token（`T_ONSET_P90`）在原始文件里的**字节偏移是 16121**，合并 `--- path ---`
  头后为 **16178**。差异来自 CJK：该文件 **31467 字节 / 25344 字符**（中文一字 3 字节）。
  故 spec 一律以**字节**计，并注明单位。
- **二分定位（用执行器行为，而不是搜文本）**：`16761 B ⇒ rc=1`、`16800 B ⇒ rc=0`（改前）。
  这与**实测读地平线一致**：把「前缀长度」二分到「pattern 仍能匹配的最短前缀」得到
  **frozen-reading 16793 B / structural-guards 15201 B（取大者 16793 B ≈ 53%）**。
  ⇒ **后 46%（14674 字节）从未被任何断言读过**（定向轴同法实测：地平线 82101 B ≈ 83%，
  未读 16%）。
  ★ 方法与陷阱：**不要用 `rfind` 去找「最靠后的字面量」** —— 同一字面量在
  `criteria_registry` 里还会出现一次（§3.3.2 的跨副本别名），`rfind` 会返回一个
  pattern **从不读取**的偏移（本次实测因此得到虚高的 21340，与门禁行为矛盾）。
  前缀匹配对长度**单调**，故二分「最短可匹配前缀」才是真地平线。
- **★ 「改前」列是模拟而非 checkout（如实说明）**：本仓禁止 `stash`/`reset`/`checkout`
  （共享工作树），故「改前」= 把**当前契约副本**里 4 条 eval-* 的 `parse_as` 字段**删掉**
  后跑**同一个**当前执行器。这精确复现了「没有该字段时」的判定路径
  （缺省 `null` ⇒ 不解析），但不是 t10 之前的源码树。t10 的测试
  `test_parse_as_and_default_differ_only_by_that_field` 用同法对照，且断言两份夹具
  **逐字段只差 `parse_as`** ⇒ 该模拟的归因是干净的。
- **根因**：`_is_all_missing()` **只判路径缺失** —— 文件一存在就不进 fail-closed 分支；
  而 4 条 pattern 的观测点全落在前段 ⇒ 后段怎么样都不影响判定。
  ★ 这是**新锚点设计引入的洞，不是历史遗留**（t8 把断言改成锚在各自 `criterion_id` 上，
  观测点随之集中到文件前段）。
- **为什么必须修**：`drift-gate` job 是**独立跑的**（本仓刻意**不加** `needs`）⇒
  **pytest 没跑或挂了时，半截产物会被放行** —— 正落在 ★ AC「缺结果必须判红」的中央。
- **修法**：`parse_as: "json"` ⇒ 内容不可解析即**判红**，detail 明写
  「**文件在但内容不是一份完整卡片 ⇒ 视同没测**」。见 §3.2.1。

#### W2 —— `criteria_registry` 伪造：门禁层**仍不拦**（**已知局限，本票不声称已修**）

- **实测（本次复跑）**：给 `criteria_registry` 追加一条伪造条目（`"source": "FORGED"`、
  把 D4 的 `direction` 改成 `lower`、阈值改成 `0.0`）后，产物**仍是合法 JSON** ⇒
  **门禁 rc=0**（不拦），而 canonical 摘要层判红
  （committed `81c79bcdece27cd1…` → forged `828ed52b899291f1…`）。
- **归属**：**已知局限，不在本票修复目标内。** W1 与 W2 **正交**：
  **W1 = 解析失败**（内容根本不是一份完整卡片）；**W2 = 解析成功但字段被改**
  （一份格式完好、内容被篡改的卡片）。`parse_as` 只解决前者。
- **唯一守卫是摘要层**（`test_canonical_digest_matches_committed`，§3.3.1 的 pytest 侧）。
  ★ 真正堵住需把 `criteria_registry` 纳入契约 pattern —— **未做**。
- ★ **不得声称门禁能挡 W2**，也**不得声称本票已修 W2**。

#### W3 —— `_is_all_missing()` 是内容嗅探（**登记局限；`_is_all_missing` 未改**）

- **现象**：`_is_all_missing()` 靠 `"--- " not in output` 判断「全部缺失」，是**内容嗅探**。
  若某 check 的**路径字面本身含 `--- `**，缺失会被误判成「读到了」。
- **实测（本次复跑）**：路径 `artifacts/--- trap ---.json` 时
  `_is_all_missing('<missing:artifacts/--- trap ---.json>')` 返回 `False`（被嗅探骗过）。
- **实际后果（本次实测）**：**仅当两个条件同时成立才真变绿** ——
  ①路径字面含 `--- ` **且** ②该 check 的 pattern **宽到能匹配 `<missing:` 占位符**。
  实测：窄 pattern（`"verdict":\s*"pass"`）⇒ 仍判红；宽 pattern（`missing`）⇒ 判**绿**。
  本票 4 条 eval-* 的 pattern 都属于前者，**故当前不构成绕过**。
  ★ 另一点让风险更低：**真实入库路径** `doc/research/data/decision_eval_*.json`
  **不含 `--- `**，故这条陷阱对**实际接线**没有影响 —— 它是一条**依赖路径字面形状**的
  隐式契约。若要彻底消除，应让 `_is_all_missing` 改为**按 `paths` 逐条检查文件存在性**
  （那会动 t1 已交付并复核过的语义 ⇒ 不在本票范围）。**记在 §7 遗留。**
- **本票做了什么**：补了**端到端**测试（走完整 `run_all → run_check_files → evaluate`）——
  ★ 因为 t8 的单元层断言**绕过了 `run_check_files`**，而陷阱恰恰出在那一步：
  单元层直接喂字符串，`run_check_files` 真正生成占位符的那一步被跳过了。
  端到端测试**不依赖** `parse_as` 才成立（它在 `present_when_missing="fail"` **单层**下
  也断言判红），故不是「靠新字段才有的护栏」。
  并把上述残余条件钉成**可执行记录**（测试里同时断言「窄 pattern 仍判红」与
  「宽 pattern 会变绿」，后者一变即红，使边界改动立刻可见）。
- **本票没做什么**：`_is_all_missing()` **一个字未改**（它属执行器，改动面比本票大）。
  **本票不声称已修 W3。**

#### 计划外修复 —— `RecursionError` 让门禁**整个 crash**（已接住）

- **现象**：极深嵌套 JSON（`"[" * 200000`）抛的是 **`RecursionError` 而非
  `JSONDecodeError`** ⇒ 异常穿透 `main()` ⇒ 门禁**整个 crash**，
  **CI 拿到的是 traceback，而不是一份门禁报告**。
- **实测（本次复跑）**：`json.loads("[" * 200000)` ⇒
  `RecursionError: maximum recursion depth exceeded while decoding a JSON array`；
  修复后把该内容当产物跑门禁 ⇒ **rc=1 且输出中无 `Traceback`**。
- **修法**：接住 `RecursionError`（连同 `ValueError`）归入「不可解析」，走**同一条判红**路径。
- ★ **为什么这条必须修（而不仅仅是「顺手」）**：**崩溃比判红更坏**。
  判红**至少留下一份报告**，人能定位到是哪条 check、哪个文件；
  崩溃则让下游**什么都读不到** —— 在 CI 里表现为一个无法归因的红叉，
  甚至可能被误读成「基础设施抖动」而重跑掉。
  这与 §3.3.2 的性能结论同源：**门禁的任何"非报告式"失败都比判红更坏。**

---

## 7. 遗留（已知并如实登记）

1. **门禁专用结论字段**（消除 §6.1 的局限）：让卡片输出一个如
   `"gate_conclusion": {"status": "pass|fail|unmeasurable", "criteria_ids": [...]}` 的字段，
   契约只断言该字段。这仍是「信任产物」，但把「门禁读的字段」与「人读的字段」分开，
   使判据口径的变化不再悄悄改变门禁语义。**另立票**。
2. **真机形态的门禁**：当前门禁断言的是**入库产物**（含冻结夹具）。真机事件流的产物是
   另一份文件，需要独立决定「真机产物是否也进门禁」（`T_PREMATURE_MEASURED` 在真机上
   必然「无法测量」，直接接进来会恒红 —— 那是 #158 遗留的写入点缺失，需先补齐）。
3. **D4 未达标的收口**：需一条独立票（改 prompt / 改判据口径），届时显式更新契约断言。
4. **两轴的 `--two-axes` 并排产物**未接进门禁（当前两条 check 各读各的轴）。
5. **R7：时序卡内嵌本机绝对路径**（§6.3）—— 建议让卡片输出仓库相对路径，
   使产物可跨机冻结。属 #158 侧产物形态，**另立票**；本票不声称已修。
6. **`_is_all_missing` 是内容嗅探**（`"--- " not in output`）：路径字面含 `--- `
   时「全缺失」识别不出。**t8** 在单元层登记了这一点；**t10** 补了**端到端**测试
   （`test_content_sniffing_trap_still_reds_end_to_end`，覆盖 `run_all → run_check_files
   → evaluate`，因为陷阱恰恰出在 `run_check_files` 生成占位符那一步，单元层绕过它）
   并把残余条件钉成可执行记录（`test_content_sniffing_trap_plus_lax_pattern_is_the_documented_risk`）。
   **本票实测该路径仍判红**，故未构成绕过；但若将来出现
   「pattern 能匹配 `<missing:...>` 占位符」的 check，就会变绿。
   ★ 修法：让 `_is_all_missing` 改为**按 `paths` 逐条检查文件存在性** ——
   属执行器（t1 语义），**未做**，不在本票范围。
7. **W2：`criteria_registry` 伪造** —— **门禁层不拦**，唯一守卫是 canonical 摘要层
   （§6.5 W2 实测：门禁 rc=0、摘要判红）。真正堵住需把 registry 纳入契约 pattern；
   **未做**，本票不声称已修。
8. **`not_pattern` 不对称（§3.5.1）** —— 只有 `eval-timing-axis-structural-guards`
   带反断言，**定向轴两条没有**；而定向卡实含一条诚实的 `unmeasurable`（D3/V1）。
   给定向轴补对称守卫会**改动判定口径** ⇒ **留待用户决定**，本票不擅自改契约。
9. **AC4 的第 4 条腿（决策书条目）目前是空的（§3.4.1）** —— 契约 4 条 eval-* 的
   `decision_ref` 指向 `决策/跨域铁律.md` 与 `决策/业务-评测.md`，但实测这两个文件里
   `63.0` / `cost_index` / `记分卡` / `定向轴` / `时序轴` **全部 0 命中**
   （只有 `Drift Gate` 机制条目命中 4 次）。⇒ 该腿**要等 §8 提案经用户批准并写入 `决策/`
   之后才成立**；此前**不得**声称「已在决策书里可追溯」。**另立票 / 等用户拍板。**

---

## 8. ★ 需要写入 `决策/` 的提案文本（待用户批准，AI 不得自行落盘）

> 按 `决策/README.md` §0.1：AI 提议（理由 + 证据）→ 用户同意 → 才落盘。
> 以下文本**未**写入 `决策/`，由主理人转呈用户。

**建议落点**：`决策/业务-评测.md`（该文件目前只覆盖 golden recall / 嵌入一致性，
本条目是其「决策评测」部分的自然扩展），或 `决策/跨域铁律.md`
（与 D-2026-08-01-019『Drift Gate 门禁』并列，因其复用同一门禁机制）。

**建议标题**：D-2026-09-23-XXX ｜ 决策评测回归门禁（两张记分卡接入 drift-gate，缺结果判红）

- **事实**：决策评测的两张记分卡（#157 定向轴 / #158 时序轴）的结构化产物已落入库稳定路径
  `doc/research/data/decision_eval_{directed,timing}_card.json`，并由既有 drift-gate 机制的
  **4 条**新增契约 check（`eval-directed-axis-frozen-reading` /
  `eval-directed-axis-structural-guards` / `eval-timing-axis-frozen-reading` /
  `eval-timing-axis-structural-guards`）以 `severity: block` +
  `present_when_missing: "fail"` + `parse_as: "json"` 断言。**缺结果（产物未产出/未生成）判红**，
  不再回落到既有的 `[SKIP]` fail-open；**文件在但内容不是一份完整卡片（解析失败）同样判红**
  （`parse_as`，§3.2.1 / §6.5 W1）；健康态判绿。
  所有引用文件缺失时判红；非法 `present_when_missing` 或 `parse_as` 值 rc=2 并点名 check id。
  **未新增第二个执行器、未新增 job、未加 `needs`** —— 复用 `scripts/drift_gate.py` +
  `config/drift-contract.json` + `quality.yml` 的既有 `drift-gate` job。
  **偏离记录**：`#154 §六` 字面要求 `phase: "runtime"`，实测在本机/CI 不可执行
  （`--phase runtime` 因既有 `vlm-n_ctx` 而无条件触发 VLM probe，无 llama 时 rc=3），
  故本票改用 `phase: "static"`（对 static 而言执行器是纯读文件 + 正则；
  记分卡产物本就是已落盘产物）。**此项偏离已如实记录，待用户在 §8 提案上确认** ——
  尚未获得用户批准，故不得读作「已经批过」。
- **来源**：工单 #159（父 spec #154 的决策评测⑤）；实施落点
  `.github/workflows/quality.yml`（`drift-gate` job 新增生成步骤）、
  `config/drift-contract.json`（4 条 check）、
  `scripts/tests/test_eval_gate_contract.py`、
  `scripts/tests/test_drift_gate_missing_result.py`、
  `doc/specs/2026-09-23-decision-eval-regression-gate.md`（本 spec）；
  执行器字段 `present_when_missing` 由 t1、`parse_as` 由 t10 落地于 `scripts/drift_gate.py`。
- **校验**（读数见 §4.3；**均于 t11 交付时实跑**）：
  1. `python scripts/drift_gate.py --contract config/drift-contract.json --phase static --mode closed --no-history`
     ⇒ 健康态 **rc=0**，跑 **14** 项（10 既有 static + 4 评测），`block_fail=0`、`warn_fail=1`；
     逐条 13×`[OK]` + 1×`[WARN]`（`webui-joywiki`，既有）。
  2. **缺结果判红**：移走任一产物 ⇒ **rc=1**，且只有该轴的 2 条 check `[BLOCK]`（另一轴仍 `[OK]`）。
  3. **注入退化判红**：时序轴 `observed` 573.0 → 5000.0 ⇒ rc=1；定向轴 V1 `S1` verdict → `fail` ⇒ rc=1。
  4. **★ 解析失败判红（W1）**：时序卡按前缀截断 —— 改前 16800 / 20000 / 25206 / 31400 B 均
     **rc=0**，改后**均 rc=1**；完整 31467 B 仍 rc=0（§6.5 W1 表）。
  5. **阈值分叉判红**：`BOUNDS` 63.0 → 64.0 ⇒ 跨层绑定测试判红；还原 ⇒ 全绿。
  6. `python -m pytest scripts/tests/ -q` ⇒ **rc=0**（**判据是退出码**）。截至 2026-09-23
     t13 收口实测为 **276 passed** —— ★ **这是时点快照，会随票演进，请勿引用**；
     本票早期交付时曾是 265，同一条命令的计数已变过一次。
     （接线期间的跨票联锁 —— t1 的护栏一度假红成 `1 failed / 172 passed` —— 已由 t5
     把该护栏改为「显式清单精确相等 + 其余仍缺省 skip」而闭环，见 §6.4。）
  7. `python scripts/run_ci_ruff.py` ⇒ 14/14 PASS；`python scripts/drift_gate_smoke_test.py`
     ⇒ `OK all smoke checks passed`。
- **预期**：任何改动只要让两张记分卡的入库读数偏离冻结基线，或让产物不再产出、不再可解析，
  `drift-gate` job 即非零退出、阻断合并。定向轴 `D4`（not-for-me 泛化召回 0.0 / 11.1 < 17.0）
  是**已知红**，已在契约中显式登记，修好时必须显式更新断言。
- **已知局限（须一并记入）**：契约只能断言产物**已算好的**判定字段，不能独立重算
  `observed <= threshold` ⇒ 门禁强度上限 = 卡片的诚实度；它挡**无意退化**有效，
  挡**蓄意伪造的自洽产物**无效（那属评审职责）。
  ★ **更具体的已知漏洞见 §6.5**：`criteria_registry` 的伪造 **门禁层不拦（rc=0）**，
  唯一守卫是 canonical 摘要层（pytest 侧）—— **本票不声称已修 W2**。
  阈值本身由卡片 `BOUNDS` 提供并有跨层绑定测试守着「不被悄悄改」，
  但「阈值取得对不对」仍由冻结快照与人的判断决定。
- **Drift**：无（本票为新增接线，未改动任何既有 check；既有 11 条 check 字段零变更，
  含 t10 新增的 `parse_as` 亦只落在 4 条 eval-* 上）。
- **Owner**：测试
- **锁定**：🔒（待用户批准后生效）

---

## 附：变更清单

| 文件 | 变更 |
|---|---|
| `config/drift-contract.json` | +4 条评测 check（**行数随本轮各票演进**，故不写死；取证：`git diff --numstat HEAD -- config/drift-contract.json` 与 `git diff --ignore-cr-at-eol --numstat HEAD -- config/drift-contract.json` **两者必须一致**（不一致即整文件行尾被改写）。既有 11 条零变更 —— 取证：比对 HEAD 与当前契约，`ids added` 恰为 4 条 eval-*、`ids removed` 为空、`baseline checks whose CONTENT changed` 为空；CRLF 保持）。★ t6 强化：绑定 threshold / 结构判据 observed / 卡片级 verdict；not_pattern 改空白不敏感。★ **t8 重写 4 条 pattern**（类级修复，见 §3.3.1/§3.3.2）：锚点从 `"kind"` 改为执行器的 `--- <path> ---` 头、字段改为**顺序无关**的嵌套先行断言、每个窗口**不得跨过下一条 `criterion_id`**、`criteria_registry` 别名靠「要求 `observed`」排除、S1/S2/S5 与 S4 零计数加**命中两处**约束；新增 `overall.cost_index` 等主指标与 `by_subset.generalization` 召回绑定；`not_pattern` 改 `(?i)` 大小写不敏感。★ **t10**：4 条 eval-* 新增 `parse_as: "json"`（既有 11 条零变更，见 §3.2.1） |
| `.github/workflows/quality.yml` | `drift-gate` job 内新增「生成两张产物」步骤（位于门禁之前）；job 名/`needs`/既有步骤均未变。★ t6（R6）：rc=2 由「放行」改为「失败」、产物校验加「非空」。★ **t8**：改为**暂存目录生成 + 成功后 `mv` 原子替换**（失败时入库产物逐字节不变），并打印产物 **sha256** 进 CI 日志 |
| `scripts/drift_gate.py` | ★ **t1**：逐 check `present_when_missing`（缺省 `skip`；非法值 rc=2 点名 check id）。★ **t10**：逐 check `parse_as`（缺省 `null`；`"json"` ⇒ 内容不可解析判红；非法值 rc=2 点名 check id），并把 **`RecursionError`/`ValueError`** 接住归入「不可解析」—— 修 W1 与反崩溃（§3.2.1 / §6.5） |
| `scripts/tests/test_eval_gate_contract.py` | 新增 22 条起步；★ 用例数**随本轮各票增长**，故不写死具体条数（取证：`python -m pytest scripts/tests/test_eval_gate_contract.py -q --collect-only`）：+R1–R5 回归与反假阳性对照、性能守卫、内容嗅探边界、CI 步骤测试（抽取真脚本跑 8 个场景，含「失败后产物 md5 不变」四例）、**canonical 全量摘要层**（`sort_keys=True` sha256，覆盖全部 1892 leaves）+ 排版不敏感 + 抽样全区域覆盖 + `criterion_id` 唯一性 + verdict 合法集合 + AC2 交叉副本绑定。★ 与 `test_drift_gate_missing_result.py` 共享的常量已抽到 `eval_gate_fixtures.py`（t12）|
| `scripts/tests/test_drift_gate_missing_result.py` | ★ **t1** 新建（`present_when_missing` 行为测试）；**t5** 把「任何 check 都不许写该字段」的护栏改为「显式清单精确相等 + 其余仍缺省 skip」（§6.4）；★ **t10** 补 `parse_as` 行为测试（截断/不可解析判红、非法值 rc=2、`RecursionError` 不 crash）与 **W3 的端到端**测试；★ **t12** 与 `test_eval_gate_contract.py` 的共享夹具抽到下面的 `eval_gate_fixtures.py` |
| `scripts/tests/eval_gate_fixtures.py` | ★ **t12 新增**（**本清单原先漏列，t13 补记**）：把「接线清单 / 既有 11 条清单 / 两张产物路径」等**两处必须一致的常量**收敛为**单一来源** —— `test_eval_gate_contract.py` 与 `test_drift_gate_missing_result.py` **两个测试文件都 import 它**（取证：`grep -rn "eval_gate_fixtures" scripts/tests/*.py`）。理由：此前两个文件各写一份清单，正是「同一事实两处副本」的形态；抽成单一来源后，清单变更只需改一处，且两边的断言不可能再互相漂移 |
| `doc/standards/test-baseline.md` | ★ **本轮由主理人改动，不属本票接线的一部分**（本清单原先漏列，t13 补记；`git diff --numstat HEAD` ⇒ 27 增 / 4 删）。**理由：那张表在发出一条「错误的当前指令」** —— 它把定向轴阈值写成 **54 / 27 / 70 / 19 / 93**，而真值是 **63 / 21 / 70 / 17 / 101**（`decision_eval_criteria.BOUNDS`）。该表**不是历史读数而是「当前该用哪个阈值」的指令**，留着旧值会让后来者照抄错数。已在原地更正并注明更正时间与取证命令；**历史读数表未动**。★ 本 spec 首页「关联」引用了本文件（作为 #157/#158 的阈值与判据台账）—— 引用关系不变，只是台账本身被更正 |
| `doc/research/data/decision_eval_directed_card.json` | 新增（定向轴入库产物，1892 标量叶子；md5 `97437757ad92fadef0f276e57355b338`） |
| `doc/research/data/decision_eval_timing_card.json` | 新增（时序轴入库产物，429 标量叶子；md5 `f3126cb4cffd4a60136c9a26e14b5188`） |
| `doc/specs/2026-09-23-decision-eval-regression-gate.md` | 本文件 |
| `doc/specs/README.md` | 索引登记 |
