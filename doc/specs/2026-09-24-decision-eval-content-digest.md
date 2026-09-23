# 决策评测⑥：内容摘要门禁（门禁纵深缺口 —— 同源副本被改）（正式）

> 生命周期标记：`正式`
> 作者端点：`测试`
> 日期：2026-09-24
> 工单：#169（源自 #159 · W2；父 spec #154 的决策评测纵切）
> 关联：`doc/specs/2026-09-23-decision-eval-regression-gate.md`（**父 spec**，本票闭环其 §6.5 W2 与 §7 第 7 条）、
> `doc/specs/drift-gate-harness-spec.md`、`决策/业务-评测.md`（D-2026-09-23-003）、
> `决策/跨域铁律.md`（D-2026-08-01-019『Drift Gate 门禁』）
> 合规：套用 `决策/spec编写规范.md` 四要素（因果链 / 条件 harness / 负面约束 / 生命周期标记）。
>
> ★ **本 spec 未写入 `决策/`**：`决策/` 需用户批准后方可落盘，AI 只能起草提案。
> 本票需要的那条 SSOT 修订提案文本见 §8，由主理人转呈用户。

---

## 1. 因果链（Why / Why-this-choice）

- **Problem（#159 留的洞，本票实测复现）**：#159 把两张记分卡接进了 drift-gate，
  并让「结果文件缺失」（`present_when_missing`）与「内容不可解析」（`parse_as`）都判红。
  但**「文件在、内容合法 JSON、只是字段被改了」这一整类，门禁上一道守卫都没有**。

  最小反例（工单给定，本次**由执行者逐字复跑**）：把某个 variant 的
  `criteria_registry` 整份换成 `[{"criterion_id":"D5-cost-index","threshold":999.0}]`，
  在 `$TEMP` 镜像里跑真执行器 ——

  ```
  drift_gate.py --contract config/drift-contract.json --phase static --mode closed --repo-root <mirror>
  ⇒ rc = 0（门禁判绿）            而 pytest 侧的 canonical 摘要层判红
  ```

- **Why it matters（为什么必须由**门禁**也守住）**：CI 的 `drift-gate` job
  **刻意独立运行**（本仓不加 `needs`，见父 spec §1 被否方案与 `quality.yml` 的注释：
  上游红会让 job 被 skipped，于是"看起来绿"却从未真跑）。⇒ **pytest 没跑或挂了时，
  那唯一一道守卫不在场**。本票要消灭的正是这条"门禁单独跑"的路径。

- **Why this choice（为什么是「给执行器一个内容摘要能力」）**：工单给了两条路 ——
  **(1)** 把 `criteria_registry` 绑进契约；(2) 把摘要校验接进 `drift-gate` job。
  本票选 **(1) 的一般化**：给执行器一个**整份内容的 canonical 摘要**校验能力，
  它**同时满足 (2)** —— 因为门禁本身就跑在 `drift-gate` job 里。

  为什么**不**走"给 registry 补 pattern"那条路（工单也点了这个风险）：
  `criteria_registry` 是**跨副本别名** —— 同一个字面 `"threshold": 63.0` 在产物里出现
  **4 次**；补字面只会进入「这轮补 `overall`、下轮漏 `by_subset`」的循环，
  而那正是父票**返工三次**的原因（t6 补 `observed` → t7 漏 `overall`/`by_subset`/`S4`
  → t8 才改成类别级的 canonical 摘要）。

  ⇒ 本票沿用父 spec §3.3.1 已经验证过的结论：**「正则永远绑不完」是类别问题，
  它的解是内容摘要，不是更多字面**。区别只在于：**把那个类别级手段从 pytest 搬进门禁**。

- **★ 前置条件：先定义「哪些拷贝是同源事实」**（工单的硬要求，不得跳过）：
  工单写明「either way you MUST first define which copies are same-source facts,
  otherwise it degenerates into another round of chasing literals」。
  本票的定义是**可执行**的（§3.1），不是文档承诺。

- **被否方案及理由**（负面约束的因）：

  | 被否方案 | 否决理由 |
  |---|---|
  | 新增 `drift-gate-eval` job / 给既有 job 加 `needs` | ★ 父 spec §1 已否决：本仓刻意让门禁并行独立（`ADR-0016`：上游红 ⇒ job 被 skipped ⇒ 门禁从未真跑）。多一个 job 就多一处「被 skipped 却看起来绿」。本票**不新增 job、不加 `needs`**。 |
  | 另造第二个执行器 / 第二套退出码 | 违背"复用，不新造"。本票只在**同一个**执行器上**加一个可选字段**，退出码沿用 `0/1/2/3`。 |
  | 给 11 条既有 check 也加摘要字段 | 它们读的是仓库内配置/源码（`.ps1` / `.env` / `.py` / `.yml` / `.html` / `.js`）—— 任何一次注释改动都会改摘要 ⇒ CI 每次必红 ⇒ 门禁被判死然后拆掉。 |
  | 只把摘要放进 pytest（现状） | 那就是 W2 本身：`drift-gate` job 独立跑，pytest 不在场时无人守。 |
  | 让执行器做数值比较（重算 `observed <= threshold`） | 父 spec §1 已否：执行器是 `re.search(pattern, 合并内容)`。为一张票把它改成能算数，等于把「单一真值」变成「一个小语言解释器」，且会让既有 11 条走上一条从未验证过的代码路径。 |
  | 摘要覆盖**整份字节**（不做 canonical 化） | 会把「值逐字不变、只重排/换缩进/换行尾」的合法重写判红 —— 父 spec §3.3.2 记录的 t7 V4 正是这个误伤形态（实测 rc=1）。 |
  | 摘要**包含**机器相关绝对路径键 | 产物内嵌本机绝对路径（R7，实测定向卡 3 处 / 时序卡 4 处）⇒ 摘要**只在同机成立**，放进契约后 CI（另一台机器、另一个检出路径）会**恒红**。故必须排除那几个键（§3.2.1）。 |

---

## 2. 范围

- **做什么**：
  1. 新增共享模块 `scripts/eval_card_digest.py`：canonical 化 + sha256 的**唯一**实现
     （`VOLATILE_PATH_KEYS` / `canonical_digest` / `digest_of_file` / `is_legal_digest`）。
  2. `scripts/drift_gate.py` 新增逐 check **可选**字段 `content_digest`
     （缺省 `null` = 不做摘要校验；非法值 rc=2；`paths` 必须**恰好一个**）。
  3. `config/drift-contract.json`：**仅** 4 条 `eval-*` 声明 `content_digest`；
     既有 11 条一个字都不加；`description` 里登记摘要的含义与边界。
  4. 摘要的**期望值以契约为单一来源**：pytest 侧改为从契约读
     （原先 `test_eval_gate_contract.py` 自带一份 `CANONICAL_DIGESTS` 字面量，**已删**）。
  5. 测试：改写既有 canonical 摘要节（来源换出处、性质一条不少）；
     新增 `scripts/tests/test_drift_gate_content_digest.py`（执行器字段行为测试）；
     新增「同源副本」的**语义定义 + 断言 + 负控**；新增卡片重放确定性测试。
  6. 本 spec + `doc/specs/README.md` 索引登记 + 父 spec 的 W2/§7-7 **加注**
     （不删历史）+ `memory/2026-09-24.md`。

- **不做什么（负面约束）**：
  - ★ **不新增 CI job、不加 `needs`、不改既有 job 的依赖关系**。理由见 §1。
    取证：`git diff -- .github/workflows/quality.yml` 为空。
  - ★ **不新增第二个执行器、不新增退出码**。取证：退出码仍是 `0/1/2/3`（§3.4）。
  - ★ **不改既有 11 条 check 的任何一个字段**（实测成立，§4.5 取证）。
  - ★ **不修 D4**（定向轴 `not_for_me_recall_pct` 0.0 / 11.1 < 17.0 是 #157 的如实读数，
    本票继续把它固定在契约里）。
  - ★ **不改两张入库产物**（`doc/research/data/decision_eval_*.json` 一个字节未动，
    取证见 §4.5）。
  - ★ **不改卡片代码**（`services/webinfer/**`）：本票是门禁侧，不是改判据。
  - ★ **不编辑 `决策/`**：只起草提案（§8）。
  - ★ **不修**「紧凑渲染（冒号后不留空格）会让**正则层**判红」这条**先于本票存在的**
    边界 —— 归因见 §6.2，修法属契约正则层的口径变更，不在本票范围。

### 2.1 ★ 与 AC 字面之间的张力（如实写出，不抹平）

工单给的路径 (1) 的字面是「**Bind `criteria_registry` into the contract binding**」。
本票**没有**把 registry 的字面绑进任何 pattern —— 走的是**同一条路的类级一般化**
（工单自己也写着 "option 1 generalized"）。

⇒ **可辩护的部分**：它比"绑字面"更强（覆盖**每一个**叶子、不只是 registry ——
唯二的例外是 :data:`VOLATILE_PATH_KEYS` 那几个机器相关路径键，见 §6.6 的实测扣除数），
且**构造上**不会误伤合法重排；工单把这条一般化明确列为可选项。

⇒ **张力（不抹平）**：一位严格的评审者可以主张「工单写的是绑 registry，
你换成了别的东西」。本票的记录是：**目标（让"门禁单独跑"时 W2 被拦住）达成且可逐条取证**
（§4.4），手段是工单列出的两条路径的**交集**（(1) 的一般化天然满足 (2)，
因为门禁就跑在该 job 里）；但**手段本身与 (1) 的字面不同**，这一点留给评审与用户判断。

---

## 3. 设计（核心决策点）

### 3.1 ★ 先定义「同源事实」：`criteria_registry` 与 `criteria` 是同一批事实

工单的硬要求是**先定义**哪些拷贝是同源事实，否则必然退化成又一轮追字面。
本票把该定义写成**可执行断言**（`test_criteria_registry_is_the_same_fact_as_the_criteria_copies`）：

> 对每张卡、每个 `criterion_id`：`criteria_registry` 里那条与 `criteria`
> （定向轴 = `index + structural`；时序轴 = 列表）里那条，
> **在两者共有的、承载判据语义的字段上必须逐字段相等** ——

| 轴 | 参与比对的共有字段 |
|---|---|
| 定向轴 | `statement` / `metric` / `scope` / `direction` / `threshold` |
| 时序轴 | `statement` / `metric` / `kind` |

实测结论（本次复跑）：**两张卡、三个 card，共 104 个字段逐一相等、0 处不一致**
（定向轴每 card **40** 个字段 × 2 card、时序轴 **24** 个 × 1 card），
且 registry 与 criteria 的**条数相同**（定向 10/10 ×2、时序 8/8 ×1）。
该计数由测试的**下限断言**护住 —— ★ 下限是**逐轴**的、**不是**一个 90：
`floor = 70 if key == "directed" else 20`（该测试按轴参数化，每次只比一条轴）。
实测 80（定向，下限 70）/ 24（时序，下限 20）⇒ 若哪天有人把比对范围悄悄缩小，
下限会先红。★ 时序侧余量只有 **4** 个字段，比定向侧紧，改动该测试时要留意。

**由此得到的结论（供门禁设计用）**：`criteria_registry` **不是**一个独立事实，
而是**同一批阈值 / 判据 id 的第二份拷贝**；registry 独有键（定向轴 `min_denominator`、
时序轴 `statistic`）在 criteria 副本里**没有对应物**，故**不属**"同一事实"的定义域
（§6.3 如实登记该作用域）。

★ 为什么这一步必须**先做**：它把「伪造 registry」从"发现了一个新数字"重述为
**"同一事实的两份副本对不上"** —— 而后者正是**内容摘要**（绑整份内容）能抓住、
正则（锚在 `criteria` 副本上）抓不住的形态。**定义的边界就是本票覆盖面的边界**。

### 3.2 `content_digest`：门禁侧的内容摘要（第三个可选字段）

执行器新增逐 check 可选字段，与既有两个**同族同形态**
（都由 `validate_*` 校验、缺省都定义在执行器常量里）：

| 取值 | 行为 |
|---|---|
| **缺省（不写）/ `null`** | **不做**摘要校验 ⇒ 与加该字段之前**逐字节相同**（既有 11 条零变更） |
| `"<64 位小写 hex>"` | 与该 check **唯一一个**引用文件的实际 canonical 摘要比对；不符 ⇒ **判红**，detail 点名**文件 / 期望值 / 实际值**，并说明"内容变了但它**仍是一份合法 JSON** ⇒ 同源副本被改动" |
| 其它任何值（`"abc"` / `true` / `1` / 大写 hex / 63 或 65 位 / 未知串） | **rc=2** ＋ `[META-ERROR]` **点名 check id 与非法值** |
| `paths` 为空 / 不止一个 | **rc=2**（一个摘要只能对应**恰好一份**内容） |

★ **非法值显式报错而不是静默当"不校验"**：理由与既有两个字段**逐字相同** ——
一个笔误会让门禁**看起来在守内容摘要却完全没守**，而这层正是 W2 唯一的门禁侧守卫。

★ **`paths` 恰好一个**是硬要求，不是偏好：多 path 时把 `run_check_files` 的**合并串**
拿去算摘要是错的（合并串含 `--- <path> ---` 头，不是任何真实文件的内容）；
"用第一个文件"会让**其余文件悄悄脱离校验** —— 正是本仓实测踩过的
「守卫存在、有测试，但**它不在这条路径上**」（`evaluate` 曾因可选 `repo_root`
而整层静默消失）。零 path 更坏：一个没有对象的摘要**恒真**。

#### 3.2.1 ★ 摘要的定义与两条承重性质

```
canonical_digest(text) := sha256(
    json.dumps(strip_volatile(json.loads(text)),
               sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"
).hexdigest()
```

**性质 1 —— 对排版不敏感（构造上成立）**：`sort_keys` 消键序、`separators` 消缩进/
空格、`json.loads` 消行尾。故「值逐字不变、只重排 / 换缩进 / 换行尾」**必须**得到
同一个摘要 —— 合法重写不得被误判红（父 spec §3.3.2 的 V4 误伤形态）。
由 `test_layout_only_rewrite_does_not_red_the_gate` 与
`test_layout_only_rewrite_does_not_red` 两侧钉住。

**性质 2 —— 对机器相关绝对路径不敏感（R7 的必然要求）**：
`VOLATILE_PATH_KEYS = ("events", "truth", "artifact", "source_file", "fixture")`
整键丢弃。★ **实测取证（本次复跑）**：两张产物里**恰好 7 个**字符串含本机绝对路径
（定向卡 3 处全在 `artifact`；时序卡 4 处在 `events` / `truth`），
**逐个都被该列表覆盖，0 个遗漏**：

```
doc/.../decision_eval_directed_card.json  3 处  covered=True  keys=['artifact']
doc/.../decision_eval_timing_card.json    4 处  covered=True  keys=['events','truth']
```

⇒ **为什么必须排**：不排的话摘要**只在同机同检出成立**（R7），放进契约后
CI（另一台机器、另一个绝对路径）会**恒红** —— 一个恒红的门禁会被拆掉。
⇒ **代价（如实登记）**：这几个键里的任何改动（例如内嵌路径被改坏）**不**会让摘要变。
准确说法是「覆盖**除这些键之外**的**全量**叶子」，**不得**说成"覆盖全量内容"。

#### 3.2.2 ★ 三层分工（并列、互不覆盖）

| 层 | 字段 | 守什么 | 引入 |
|---|---|---|---|
| 1 | `present_when_missing` | 文件**在不在** | #159 / t1 |
| 2 | `parse_as` | 文件在、内容**是不是一份完整卡片**（W1） | #159 / t10 |
| 3 | `content_digest` | 文件在、内容合法，但**内容变了**（W2） | **本票** |

★ **判定顺序刻意是 1 → 2 → 3**：内容根本解析不了时，报"解析失败"比报"摘要不符"
更准确（前者才是根因），两层**不争抢同一句话**。由
`test_unparsable_content_is_still_handled_by_parse_as` 与
`test_missing_file_is_still_handled_by_present_when_missing` 钉住。

★ **三层都只决定 `passed`，是否阻断仍只由 `severity` + `mode` 决定**（既有语义未动）；
`severity=warn` 与 `mode=open` 下 rc 仍为 0，但报告里必须留下 `passed=False`。

★ **声明了摘要却算不出实际摘要**（内容不是合法 JSON）时，本层**判红而不是跳过**：
「声明了要校验却没校验」比不声明更坏（与 `parse_as` 非法值同一条理由）。
由 `test_digest_on_unparsable_content_still_reds_without_parse_as` 钉住
（该用例**刻意不写** `parse_as`，让摘要层**自己**成立）。

### 3.3 ★ 单一来源：摘要的**实现**与**期望值**各只有一份

这是本票最容易被写成"两份副本"的地方，故两条都**结构性**钉住：

| 事实 | 唯一来源 | 谁消费 | 怎么钉住 |
|---|---|---|---|
| **算法**（canonical 化 + sha256） | `scripts/eval_card_digest.py` | 执行器 `drift_gate.py` + 两套测试 | `test_executor_and_tests_share_one_digest_implementation` 断言 `dg.canonical_digest is ecd.canonical_digest`（**同一个函数对象**） |
| **期望值**（每张卡的摘要） | `config/drift-contract.json` 的 `content_digest` | 执行器判红/判绿 + pytest 的断言 | `test_expected_digest_comes_from_the_contract_not_a_local_copy` **扫描测试源文件**：不得再出现任何 64 位 hex 字面量 |

★ 为什么期望值必须搬进契约：本票之前，期望值是 `test_eval_gate_contract.py` 里的
`CANONICAL_DIGESTS` **字面量**。契约现在也持有该值（并被**执行器**消费）⇒
两份副本必须收敛成一份，否则「门禁判红的那个数」与「测试期望的那个数」
可以各自被改动而互不察觉 —— 那正是 `eval_gate_fixtures.py` 当初被抽出来要消灭的形态
（本仓已为此付过费）。

★ 读取逻辑（含"缺了/同轴两条不一致就判红"的判据）也收敛在
`scripts/tests/eval_gate_fixtures.py`（`declared_digest_of` / `declared_digest_by_axis`），
不授权任何测试文件各写一份。

### 3.4 退出码与报告：零改动 + 只增字段

| 退出码 | 含义 |
|---|---|
| `0` | 无阻断（`mode=open` 下恒为 0） |
| `1` | `mode=closed` 且有 `severity=block` 的不符（**新增的摘要不符走这一条**） |
| `2` | meta-error：契约缺失 / JSON 解析失败 / `present_when_missing`、`parse_as`、**`content_digest`** 取非法值 / **`content_digest` 配了 ≠1 个 paths** |
| `3` | runtime 阶段 probe 刷新失败（未动） |

`--json` 报告：既有字段名与语义**零改动**，只**新增** `results[].content_digest`
（缺省时归一化为 `null`，而不是键缺失 —— 下游要能区分"看过并决定不校验"
与"这个字段还没接上"）。

### 3.5 CI：**不改 `quality.yml`**，并说明为什么 option (2) 仍然达成

工单的路径 (2) 是「把摘要校验接进 `drift-gate` job」。本票**没有改 `quality.yml`**，
理由是**结构性的**：

- `drift-gate` job 里跑的就是 `python scripts/drift_gate.py --contract config/drift-contract.json --phase static --mode closed --no-history`；
- 本票给**同一个执行器**加了摘要校验，且**真契约**里 4 条 eval-* 都声明了摘要；
- ⇒ **该 job 的下一次运行就已经在做摘要校验**，无需任何 YAML 改动。

★ 本票**没有**新增 job、**没有**加 `needs`（取证：`git diff --name-only` 不含
`.github/workflows/quality.yml`）。父 spec 记录的"刻意独立运行"决定**未被触碰** ——
本票恰恰是**利用**那个独立性：门禁独立跑时，它自己也持有内容摘要。

★ 生成步骤的既有注释**无需事实性更正**：它描述的是**产物生成**（暂存目录 + 原子替换 +
sha256 进日志），本票一个字都没改那条路径的语义。

---

## 4. Harness（可复现工作流 / 验证仪式）

### 4.1 判据摘要的命令（本机与 CI 等价）

```bash
cd /d/AI/workspace/JoyAI-VL-Interaction-main
# 健康态（须 rc=0）
/d/AI/envs/joyai-main/python.exe scripts/drift_gate.py \
    --contract config/drift-contract.json --phase static --mode closed --no-history

# 契约里的摘要（唯一真值源）
/d/AI/envs/joyai-main/python.exe -c "import json;d=json.load(open('config/drift-contract.json',encoding='utf-8'));[print(c['id'],c['content_digest']) for c in d['checks'] if c['id'].startswith('eval-')]"
```

### 4.2 ★ 本票提交进契约的摘要值

| 产物 | `content_digest`（sha256，小写 64 位） |
|---|---|
| `doc/research/data/decision_eval_directed_card.json` | `81c79bcdece27cd1a69534c81297562170bb900ee5f5c77807051d026104609a` |
| `doc/research/data/decision_eval_timing_card.json` | `a41759a2ccb310924f035075faa4493ae32c515256a662f137807454e0fe8879` |

**怎么算出来的**：用**本票新增的唯一实现**算，命令即实现本身（可复现）：

```bash
/d/AI/envs/joyai-main/python.exe -c "
import sys; sys.path.insert(0,'scripts')
from eval_card_digest import digest_of_file
from pathlib import Path
for r in ('doc/research/data/decision_eval_directed_card.json',
          'doc/research/data/decision_eval_timing_card.json'):
    print(r, digest_of_file(Path(r)))"
```

★ **与父 spec 里那份值的交叉核对（重要的独立性证据）**：这两个值与父 spec
`test_eval_gate_contract.py` 里**原先**的 `CANONICAL_DIGESTS` 字面量**逐字符相同**
（`81c79bcd…` / `a41759a2…`）。⇒ 本票是**搬家**（把值从测试搬进契约），
**不是**重新定义摘要 —— 若本票顺手改了算法，这两个值必然不同，而它们相同。

### 4.3 ★ 核心 AC：改前 / 改后读数（**逐条实测，非推断**）

镜像一律建在 `.cache/169-repro/` 下（`--repo-root` 指向镜像），**不碰入库产物**。

| # | 输入（镜像） | 改前 rc（**模拟**：契约副本去掉 `content_digest`） | 改后 rc | 改后判红的 check |
|---|---|---|---|---|
| 1 | 健康产物（原样） | **0** | **0** ✅ | — |
| 2 | 伪造 V1 `criteria_registry` | **0** ❌ | **1** ✅ | `eval-directed-axis-frozen-reading`、`eval-directed-axis-structural-guards` |
| 3 | 伪造 V2 `criteria_registry` | **0** ❌ | **1** ✅ | 同上（该轴两条） |
| 4 | `sort_keys + indent=4` 重写（值不变） | 0 | **0** ✅ | —（反误伤） |
| 5 | `sort_keys + indent=2` 重写（值不变） | 0 | **0** ✅ | —（反误伤） |
| 6 | CRLF 行尾重写（值不变） | 0 | **0** ✅ | —（反误伤） |

★ **「改前」是模拟，不是 checkout**（本仓禁 `stash` / `reset` / `checkout`，共享工作树）：
做法是把**当前契约副本**里 4 条 eval-* 的 `content_digest` **删掉**后跑**同一个**当前执行器。
这精确复现"没有该字段时"的判定路径（缺省 `null` ⇒ 不做摘要校验），
与父 spec §6.5 W1 记录 `parse_as` 改前读数用的是**同一手法**。
★ 该模拟的归因是**干净的**，由两条测试夹住：
`test_forged_registry_was_green_before_the_digest_field`（去掉字段 ⇒ 同一伪造输入仍 rc=0）
与 `test_mirror_without_the_digest_field_keeps_the_pattern_layer_honest`
（去掉字段的健康镜像仍 rc=0）—— 变量只有摘要字段与那处伪造。

★ **归因的第三重证据（用真契约的真 check，不是自己编的正则）**：
`test_pattern_layer_alone_would_not_catch_it` 取真契约里那条
`eval-directed-axis-frozen-reading`（逐字照抄），去掉摘要字段后对**同一伪造输入**
调 `evaluate()` ⇒ **`passed=True`**。⇒ 抓它的**只能**是摘要层。

### 4.4 负控（★ 本票的核心取证）—— 该红的红、不该红的不红

| # | 变形 | 期望 | 实测 |
|---|---|---|---|
| NC1 | 伪造 V1 `criteria_registry` | 门禁 rc=1，点名该轴两条 check；detail 含**文件 / 期望值 / 实际值** | ✅ |
| NC2 | 伪造 V2 `criteria_registry` | 同上 | ✅ |
| NC3 | 去掉字段后同一伪造输入 | rc=**0**（证明新字段在起作用） | ✅ |
| NC4 | 去掉字段的健康镜像 | rc=**0**（证明镜像本身没引入别的差异） | ✅ |
| NC5 | 非法 `content_digest`（`"abc"` / 大写 hex / 63 位 / 65 位 / 非 hex / `true` / `1` / `""` / `" "*64` / `[]` / `{}`） | **rc=2**，点名 check id 与非法值，且**无任何报告** | ✅ 11 例参数化 |
| NC6 | `paths: []` + 摘要 | rc=2（一个没有对象的摘要恒真） | ✅ |
| NC7 | 两条 paths + 摘要 | rc=2（"摘要算的是哪一份"没有答案） | ✅ |
| NC8 | 声明摘要但内容不可解析（**不写** `parse_as`） | rc=1（**不静默跳过**） | ✅ |
| NC9 | 文件缺失 + 摘要 | rc=1，措辞仍是「引用的结果文件全部缺失」（**不被摘要层抢走**） | ✅ |
| NC10 | 布局重写（indent=4 / indent=2 / CRLF） | **rc=0**（反误伤） | ✅ |
| NC11 | 真改一个叶子（`overall.cost_index.median` +1.0） | rc=1 | ✅ |
| NC12 | 「同源定义」负控：内存里伪造 registry | 同源比对**必须**发现不一致（否则该定义是恒绿装饰） | ✅ |
| NC13 | ★★ **复核期查出并已修**：伪造 registry **并**在产物内容里注入 `<missing:<自己路径>>` / `<read-error:…>` 字面量 | **rc=1**（修复前实测 **rc=0** —— 产物内容把"文件缺失"判据伪造了，守卫整体跳过）。真缺失仍走 `present_when_missing` 措辞 | ✅ 两种占位符形态各一例 + 成对对照 |

★ **反假阳性对照（每条修复都必须同时满足）**：健康态 rc=0 / 4 条 eval-* 全 `[OK]` /
既有 10 条 static 判定不变（**9×`[OK]` + 1×`[WARN]` `webui-joywiki`**）/ 全量 pytest 绿。
上表所有"改后"行都在这个前提下取得。

### 4.5 本票实跑的读数（**截至 2026-09-24**）

| # | 命令 | 实测 |
|---|---|---|
| ① | `python scripts/drift_gate.py --contract config/drift-contract.json --phase static --mode closed --no-history` | rc=**0**；`total=14 block_fail=0 warn_fail=1`；13×`[OK]` + 1×`[WARN]`（`webui-joywiki`，既有、逐行不变） |
| ② | 核心 AC（§4.3 表，两种摘要配置 × 6 种输入） | 见 §4.3；改前 **0** → 改后 **1** |
| ③ | 布局重写三轴 | 均 rc=**0** |
| ④ | `python -m pytest scripts/tests/ -q` | **337 passed**, rc=**0** ★ **时点快照，会随票演进，不得引用为判据**（本票开工前基线为 276 passed；为修 §6.1.1 的 fail-open 与 §5.1 的复核项，本票新增 4 条用例 + 收敛重复夹具） |
| ⑤ | `python scripts/run_ci_ruff.py` | **14/14 CI ruff steps PASS** |
| ⑥ | `python scripts/drift_gate_smoke_test.py` | `OK all smoke checks passed` |
| ⑦ | `python -m pytest services/webinfer/tests/ -q` | **823 passed**, rc=0（无既有失败） |

★ **「既有 10 条 static check 判定逐条不变」的取证方式**是**逐行比对**命令输出
（9×`[OK]` + 1×`[WARN]`，共 10 条），而不是只看总 rc —— 总 rc 绿并不排除某条判定被换掉。

★ **为什么只给「截至时点」而不是「当前值」**：`pytest` 的总数取决于**收集到多少个用例**，
每张票都可能增删 ⇒ 写死它必然分叉。**判据是退出码**（rc=0），不是计数。

### 4.6 ★ 确定性：本地代理与它**不**覆盖的部分

`test_card_scripts_reproduce_the_declared_digest` 在 `tmp_path` 里把两张卡片**重新生成**，
再比对内容摘要是否仍等于契约声明的值。实测：**两张都与契约声明相符**。

★ **边界（不得读成"跨机已验证"）**：该测试在**同一台机器、同一个检出**上跑，
它证明的是「产物可由脚本**确定性重放**」。**跨机**是否也一致，取决于
`VOLATILE_PATH_KEYS` 是否把**所有**随机器变化的叶子都排掉了 —— 那只有
**CI（另一台机器、另一个绝对路径）跑 `drift-gate` 时的真实读数**能证明。
⇒ **本票不声称跨机已验证**；本节的作用是给出本地代理，并**如实登记**
"真正的跨机测试是 CI 的下一次运行"。

★ 退出码说明：定向轴重放**如实 rc=1**（D4 未达标），故该测试**不能**断言 rc=0 ——
那是设计意图。判据是"产物被写出来了 + 摘要对得上"，`rc ∈ {0,1}` 都合法。

---

## 5. 验收 / 排除

- **验收判据**（对应 #169）：
  1. ★ **核心 AC**：在 `drift-gate` 单独跑的场景下，伪造 `criteria_registry`
     ⇒ **rc=1** 且点名正确的 check id（§4.3 实测：改前 0 → 改后 1）。
  2. ★ **不误伤**：布局重写（indent / sort_keys / CRLF）⇒ rc=0（§4.3 第 4–6 行）。
  3. ★ **既有 11 条零变更**（字段、语义、判定逐条不变）；★ 两张入库产物**零改动**。
  4. ★ **不新增 job / 不加 `needs` / 不新增执行器 / 不新增退出码**（§3.5 取证）。
  5. ★ **同源事实已定义**（§3.1），且有**负控**证明该定义不是恒绿装饰。
  6. ★ **单一来源**：摘要算法一份（同一函数对象）、期望值一份（契约），
     两条都由**结构性**断言钉住（源码扫描 / 函数对象比对）。
  7. 本 spec 落盘 + `doc/specs/README.md` 索引登记 + 父 spec 的 W2/§7-7 **加注**
     （不删历史）+ `memory/2026-09-24.md`。
  8. 全量 `pytest` rc=0、`run_ci_ruff.py` 全过、`drift_gate_smoke_test.py` 通过。

- **明确排除**（不属本 spec）：
  - **修 D4 未达标**（属 #157 的独立工作）。
  - **`_is_all_missing` 内容嗅探（W3）** —— 父 spec 已登记，本票未动（属执行器既有语义）。
    ★ 注意区分：本票**修掉**的是同一根因在**摘要层与 `parse_as` 层**的实例
    （§6.1.1，那里一条路径即可触发、严重度高一档）；W3 那条需要两个条件同时成立，
    父票已判"当前不构成绕过"，故此条仍**未修**。

### 5.1 ★ 复核轮（两轴 code review）查出并已修的问题（如实登记）

本票在交付前跑了两轴复核（Standards / Spec），下列问题**由复核查出**并在提交前修复。
登记它们的理由：其中第 1 条是**严重度最高的一条**，且它恰恰出在本票新增的守卫身上。

| # | 问题（复核发现） | 处置 |
|---|---|---|
| 1 ★★ | **摘要层/parse 层可被产物内容绕过**（`<missing:…>` 字面量 ⇒ 判据被伪造 ⇒ 跳过校验 ⇒ rc=0） | **已修**（`_path_state` 问文件系统），三条回归测试 + 负控取证 ⇒ §6.1.1 |
| 2 | `config/drift-contract.json` 的 4 条 `description` **各自复述了自己 `content_digest` 的数值**（同一事实 8 份副本，且无断言保证正文与字段一致 ⇒ 改字段会留过期正文） | **已修**：正文改为指向字段、不复述数值（实测该值现在只剩字段本身 2 处/轴） |
| 3 | 注释引用了**不存在**的测试名（`test_digest_layer_reds_the_gate_end_to_end`、`_mirror_eval_contract_keeps_the_pattern_layer_honest`） | **已修**：改为实测存在的用例名，并注明曾写错 |
| 4 | `validate_content_digest` docstring 声称"两个字段共用的是**报错格式**"，而实现是**手写**文案、并未共用 | **已修**：改为如实说明"共用的是判定谓词，报错文案是手写的" |
| 5 | §3.1 曾写"下限断言 `compared >= 90`" —— 代码实为**逐轴** `70 / 20`，`>= 90` 查无此物 | **已修**：改为实测的逐轴下限（80/70、24/20），并注明时序侧余量仅 4 |
| 6 | 「单一来源」的源码扫描**只扫本文件** ⇒ 字面量放进同目录别的测试文件可逃过 | **已修**：扫描范围扩到 `scripts/tests/*.py` 全目录 |
| 7 | 「同源定义」的负控**另抄了一份比对循环** ⇒ 只证明"抄件会抓"，真断言被改坏时照样绿 | **已修**：抽出 `registry_agree_problems`，负控与真断言调**同一个**谓词；并补一条"只改一个字段（条数相同）"的更隐晦负控 |
| 8 | `content_digest: null` 在 **docstring** 里被列为非法值，而**实现刻意接受**它（= 缺省，与 `parse_as` 同形） | **已修**：文档改为如实说明（并说明"把守卫改成 null 会被 pytest 抓"，见下条） |

★ 上述第 8 条的兜底**已实测**：把某条 `eval-*` 的 `content_digest` 改成 `null`
（静默关掉该轴守卫）⇒ `pytest scripts/tests/` **26 failed** ⇒ 该退化不可能悄悄合入。
⇒ 故 `null` 合法**不构成**新的 fail-open 面：它的语义是"把缺省值写出来"，
而"把已接线的守卫改回缺省"是被测试钉住的**配置改动**（会显眼地红）。

★ **未修（如实登记为遗留）**：复核还指出若干判断项 ——
`eval_card_digest.canonical_text` / `digest_of_file` 与
`eval_gate_fixtures.artifact_of` 目前**无调用方**（前者是公开 API 的分解步骤、
后两者属预留）；`64` 这个长度常量在三处各写一份
（`eval_card_digest.DIGEST_HEX_LENGTH` / `drift_gate.CONTENT_DIGEST_HEX_LENGTH` /
测试里的 `EXPECTED_HEX_LENGTH`）—— 其中最后一条**有断言钉住三者相等**，
故不是可漂移的副本。**这些不影响本票判定能力**，登记在此以免被读成"已全部解决"。

★ **已在复核轮收敛的一项重复**（原本登记为遗留，提交前已处理）：
`run_gate` / `report_of` / `assert_no_report` / `write_contract` / `CLOSED_JSON`
原先在 `test_drift_gate_missing_result.py` 与 `test_drift_gate_content_digest.py`
里**各有一份逐字相同的拷贝**。两份拷贝的危险不是"多打几个字"，而是**各自漂移**：
改一处忘一处，两个文件就对"门禁怎么跑、报告怎么读"给出不同答案，而两边都在对
门禁行为下断言 —— 正是本票要消灭的那一类。现已全部收敛到共享模块
`eval_gate_fixtures.py`（两套测试从那里 import）；同时删掉因此失去调用方的
本文件级 `GATE` 常量。
  - **`not_pattern` 在定向轴的已知不对称** —— 父 spec §3.5.1 留待用户决定。
  - **紧凑渲染（冒号后无空格）让正则层判红** —— **先于本票存在**（§6.2），
    修法属契约正则层口径变更，不在本票范围。
  - **卡片输出仓库相对路径（R7 的根治）** —— 属 `services/webinfer/**` 产物形态，另立票。
  - **AC4 第 4 腿（决策书条目）的复核** —— 父票已随 `D-2026-09-23-003` 落盘而成立；
    本票只需**修订**该条（§8），不改其成立性。
  - bug 修复 / 运维操作 / 起服务 runbook 不属 spec（见 `决策/spec编写规范.md` §3）。

---

## 6. ★ 如实记录的局限

### 6.1 摘要绑的是**内容**，不是签名（最主要的边界）

**它挡得住**：任何**单侧**的内容改动 —— 只要产物内容变了而契约声明的摘要没变，
门禁就判红。这正是 W2（同源副本里只改一份）的形状。

**它挡不住**：一份**连声明摘要一起改掉**的、自洽的伪造产物 ——
改产物 + 改 `config/drift-contract.json` 里的 64 位 hex 就又能判绿。
那**不属门禁职责**，属**代码评审与 `git diff`**（父 spec §6.1 的同款推理在此完全适用）。

⇒ **不得**声称"本票抵抗了蓄意伪造"。准确说法是：
**门禁现在能独立地发现"内容被改"，此前它发现不了**；而"改的人是否连摘要一起改"
由评审拦。★ 两者的区别很重要：`git diff` 里一个 64 位 hex 的变化**非常显眼**，
而一个 `criteria_registry` 条目的变化**不显眼** —— 本票把"不显眼的那类"变成了可检测的。

### 6.1.1 ★★ 复核期查出的 fail-open：产物**内容**可以伪造"文件缺失"判据（**已修**）

本节是本票**最重要的开发期记录**，因为它是"守卫看起来在守、却被被守卫的对象绕过"
的又一实例 —— 而且出在**为修 W2 而新增的这道守卫自己身上**。

- **原实现**：摘要层与 `parse_as` 层都用
  `if f"<missing:{rel_path}>" in output or f"<read-error:{rel_path}:" in output: continue`
  判断"该文件缺失/不可读 ⇒ 归 `present_when_missing` 管 ⇒ 本层跳过"。
- **为什么那是洞**：`output` 是 `run_check_files` 的**合并内容** ——
  缺失文件的占位符与真实文件的**内容**是**同一种文本**。⇒ 产物只要在自己内容里
  写下一个 `<missing:<自己路径>>` 字面量，这两层就认定"文件缺失"而**整体跳过**。
- **实测读数（本次复跑，修复前）**：伪造 `criteria_registry` **并**在某变体的
  `axis_question` 里注入该字面量 ⇒ 门禁 **rc=0**、`blocked=[]`
  —— 与"压根没有这道守卫"完全一样。两种占位符形态（`missing` / `read-error`）都能绕过。
- **修复**：新增 `drift_gate._path_state(rel_path, repo_root)`，**问文件系统**
  （`Path.read_text` 的 `FileNotFoundError` / `OSError`）而不是嗅探合并串；两层都改用它。
  修复后同一输入 ⇒ **rc=1**，判红该轴两条 check（实测表见 §4.4 的 NC13）。
- **为什么必须修而不是登记**：该守卫是 W2 的**唯一门禁侧防线**（§1），
  一个能被产物内容关掉的防线等于没有防线 —— 而它的洞恰恰由**产物内容**触发，
  即"被守卫的对象可以决定守卫是否生效"。
- **与既有 W3 的关系（如实区分，不混为一谈）**：父 spec §6.5 W3 记录的是
  `_is_all_missing` 的**内容嗅探**（`"--- " not in output`），那里**必须同时满足**
  "路径字面含 `--- `" **且** "pattern 宽到能匹配占位符"才真变绿，故父票判它"当前不构成绕过"。
  本条**不同**：它**一条路径即可触发**、不依赖路径字面形状、也不需要 pattern 配合
  （跳过发生在正则之前）。⇒ 严重度高一档，故本票修掉它，而 W3 仍未修（不属本票）。
- **回归护栏**（成对，缺一不可）：
  `test_content_cannot_forge_the_missing_predicate`（两种占位符形态，**参数化**）
  与 `test_forged_placeholder_injection_and_real_missing_are_distinguished`
  （真缺失仍走 `present_when_missing` 的既有措辞，证明修复没伤到既有语义）；
  `parse_as` 侧另有 `test_content_cannot_forge_the_missing_predicate_parse_layer`。
  ★ 这三条**已做负控取证**：把两处调用点改回内容嗅探后它们**立刻判红**（3 failed），
  改回修复版即绿 —— 即它们真的钉住了这个洞，不是恒绿装饰。

### 6.2 「紧凑渲染」边界：先于本票存在，且**不是**摘要层做的
- **现象**：把产物按 `separators=(",", ":")` 压成**无冒号空格**的紧凑 JSON
  （值逐字不变、摘要**也不变**）时，门禁 **rc=1**。
- **★ 归因（实测，不是推断）**：判红理由来自**正则层**（`pattern=… 未匹配`），
  **不是** `content_digest`；且同一输入在**去掉摘要字段**（= #169 之前的配置）下
  **同样 rc=1**。⇒ 该行为**先于本票存在**。
- **根因**：契约 pattern 里的 `"criterion_id": "D1-…"` 经 `re.escape` 后，
  冒号后是**字面空格**（不是 `\s*`）⇒ 紧凑 JSON 里没有该空格，锚点失配。
- **本票不修**：修法要动 4 条 pattern 的锚定形态（属**契约正则层**的口径变更，
  风险与归属都不同于本票）。⇒ 作为**已知边界登记**，并由
  `test_compact_colon_only_rendering_is_a_pre_existing_regex_limit` 钉住
  「它的红来自正则层、且先于本票」—— 一旦该断言变绿，说明有人改了锚定形态，
  届时应**显式更新**该测试与本节，而不是让边界悄悄消失或悄悄变红。
- ★ **为什么必须登记而不是忽略**：它决定了"合法重写不误伤"这句话的**有效范围** ——
  准确说法是「**缩进 / 键序 / 行尾**三轴不误伤」，**不含**"冒号后不留空格"。

### 6.3 同源定义的作用域（不把话说大）

§3.1 的定义只覆盖 registry 与 criteria 的**共有语义字段**。因此：

- registry **独有**的键（定向轴 `min_denominator`、时序轴 `statistic`）**不参与**比对 ——
  它们在对侧没有对应物，故不属"同一事实"。
- 该定义**不声称**"两份副本必须永远并存"：若哪天卡片改成只输出一份，
  该测试会自然失效并提示更新定义（那是一次**产物形态变更**，应显式改而不是悄悄跳过）。
- 该定义**只覆盖 `criteria_registry` 这一组**同源副本。产物里**还有别的**
  同源/别名关系（父 spec §3.3.2 列了 4 处：`S3`/`S4` 都含 `"quiet_rows": 54`、
  `n_speaking` 在 `T_SAMPLE_FLOOR` 内出现两次、`median` 在多个 `overall` 指标里重复）。
  ★ **本票不去逐个定义它们** —— 那正是"追字面"的路。本票的覆盖面**不依赖**逐条定义，
  而依赖摘要的**整份内容**性质：任何一个叶子变，摘要就变（由
  `test_digest_covers_every_region_not_just_criteria` 抽样证明）。
  ⇒ 逐条定义的作用是**解释 W2 为什么是这一类缺陷**，而不是**枚举守卫的对象**。

### 6.4 跨机确定性未被本票证明（R7 的残余）

见 §4.6。本票证明的是**本地重放确定性**，并**如实登记**：跨机一致性
只有 CI 的真实读数能证；其成立依赖于 `VOLATILE_PATH_KEYS` 的完备性，
而该完备性是**对当前产物形态的实测结论**（7 处绝对路径全被覆盖，§3.2.1），
**不是**对将来产物形态的保证。⇒ 若卡片将来新增别的内嵌绝对路径键，
**摘要会在 CI 恒红**（那是可发现的，不是静默的），修法是把它加进该元组。

### 6.5 一位评审者可以主张的分票意见（如实登记）

「给执行器加一个内容摘要能力」在语义上**超出了 #169 路径 (1) 的字面**
（绑 `criteria_registry`），也**不是**一条纯粹的 YAML 改动（路径 (2)）。
一位严格的评审者可以主张：**这应当是执行器 owner 的一张独立票**
（与 `present_when_missing` 属 t1、`parse_as` 属 t10 的先例一致），
而 #169 只做接线与测试。

**本票的记录**：改动**已发生且已复核**（§4 逐条实测），目标**达成**；
但「一张票是否可以顺手扩展执行器」这条**归属纪律问题**留给评审与用户判断，
本 spec **不自行判定为"完全无偏离"**。

### 6.6 性能（挂起的门禁比判红更坏）

新增的摘要层对每个声明了该字段的 check **多读一次文件 + 一次 canonical 序列化**。
两张产物的实测规模：定向卡 **97956 B / 1892 叶子（摘要覆盖 1889 = 减去 3 个机器相关
路径叶子）**、时序卡 **31467 B / 429 叶子（摘要覆盖 425 = 减去 4 个）**。
★ 这两个扣除数**逐条实测**（剔除位置：定向卡 `artifact` + 两 variant 的
`source.artifact`；时序卡 `events` / `truth` + `source.events` / `source.truth`）——
故凡说"摘要覆盖全量叶子"处一律写作**减去 :data:`VOLATILE_PATH_KEYS` 之后的全量**，
不得只写总数（那会把 3/4 个叶子说成被覆盖）。
现有性能守卫（`test_patterns_are_fast_on_healthy_artifacts` /
`test_patterns_stay_fast_when_every_assertion_fails`）仍在跑且绿。
★ 如实说明：本票**没有**为摘要层单独加**耗时上限**断言 —— 它的成本是
"读一次已读过的文件 + 一次 `json.dumps`"，与既有正则层的回溯风险不同量级。
若将来产物显著变大，应补一条显式上限（属改进项，未做）。

---

## 7. 遗留（已知并如实登记）

1. **跨机确定性**（§4.6 / §6.4）：本地代理已建；真正的跨机测试是 CI 下一次运行。
   *建议*：让卡片输出仓库相对路径（父 spec §6.3 R7），可使产物真正可跨机冻结。
2. **紧凑渲染边界**（§6.2）：先于本票存在，未修。修法 = 把 4 条 pattern 里冒号后的
   字面空格改成 `\s*`（属契约正则层口径变更）。**未做**。
3. **摘要层的耗时上限**未单独断言（§6.6）。
4. **「连声明摘要一起改掉」的伪造**：本票不声称能拦（§6.1），属评审职责。
   若要更强，需要引入**外部锚**（例如把摘要写进 `决策/` 或另一个独立文件并由人签核）——
   那超出"内容摘要"的语义，**未做**。
5. **R7 的根治**（卡片输出相对路径）仍属 `services/webinfer/**`，另立票。
6. **`决策/` 的修订提案**（§8）待用户批准 —— 因为 `D-2026-09-23-003` 第 8 条
   当前把 W2 登记为**未修的已知局限**，本票落地后那句话**已不准确**。

---

## 8. ★ 需要修订 `决策/` 的提案文本（待用户批准，AI 不得自行落盘）

> 按 `决策/README.md` §0.1：AI 提议（理由 + 证据）→ 用户同意 → 才落盘。
> 以下文本**未**写入 `决策/`，由主理人转呈用户。

★ **为什么需要修订而不是新增**：`决策/业务-评测.md` 的 `D-2026-09-23-003` 第 8 条
「已知局限」当前写着：

> **W2 —— 伪造 `criteria_registry`，门禁层不拦**：… **门禁层仍判绿（rc=0）**；
> 唯一拦得住它的是 pytest 的 canonical 摘要层。… **属未来事项，尚未做**。

本票落地后**这句话已不准确**（实测改前 0 → 改后 1）。`决策/` 是**冲突时的裁决源**，
留着它会与实物分叉 —— 故提出**原地修订**（保留历史，加注现状），
把该条从「未修的已知局限」改为「已由 #169 闭环，含新的边界」。

**建议落点**：`决策/业务-评测.md` → `D-2026-09-23-003`
（在其第 8 条的 W2 子条上**原地修订**；**不新增**一条决策条目 ——
本票没有产生新的**决策**，只是把既有决策里的一句话修正到与实物一致）。

**建议修订文本**（替换 W2 子条的那两段）：

> - **W2 —— 伪造 `criteria_registry`：已由 #169 闭环（2026-09-24）**：
>   原状态（#159 交付时，实测）：产物**文件存在、字段被改**（例如把某 variant 的
>   `criteria_registry` 整份换成 `[{"criterion_id":"D5-cost-index","threshold":999.0}]`）时
>   **门禁层判绿（rc=0）**，唯一拦得住它的是 pytest 的 canonical 摘要层。
>   ★ **为什么必须由门禁也守住**：CI 的 `drift-gate` job **刻意独立跑**（不加 `needs`）
>   ⇒ pytest 没跑或挂了时，那唯一一道守卫**不在场**。
>   ★ **现状（#169）**：门禁新增逐 check 可选字段 `content_digest`
>   （缺省 `null` = 不校验，既有 11 条零变更），与既有 `present_when_missing` /
>   `parse_as` 三层并列。摘要 = `json.loads` → 递归丢弃机器相关绝对路径键
>   （`events`/`truth`/`artifact`/`source_file`/`fixture`）→ `sort_keys` + 紧凑重排 → sha256，
>   **故对键序 / 缩进 / 行尾不敏感**（合法重排不误伤）。实测：**改前 rc=0 → 改后 rc=1**
>   （该轴两条 check 判红），健康态与布局重写仍 rc=0。**未新增 job、未加 `needs`、
>   未新增执行器、未新增退出码**。摘要的**期望值以契约为单一来源**（该值同时被
>   执行器与 pytest 消费），算法唯一实现在 `scripts/eval_card_digest.py`。
>   ★ **新的边界（须一并记入，不得省略）**：摘要绑的是**内容**、**不是签名** ——
>   一份**连声明摘要一起改掉**的伪造产物**仍会判绿**，那最终由**代码评审与 `git diff`**
>   拦住（`git diff` 里 64 位 hex 的变化非常显眼，而 registry 条目的变化不显眼 ⇒
>   本票把"不显眼的那类"变成了可检测的）。★ 不得读成"已能抵抗蓄意伪造"。
>   ★ 另有一条**先于本票存在**的边界：把产物压成**无冒号空格**的紧凑 JSON
>   （值不变、摘要也不变）会让**正则层**判红 —— 与本票无关，已如实登记，**未修**。
> - （R7 子条原文保留，不改：本票未触动产物形态；摘要对它的处置是显式剔除路径键，
>   故摘要可跨机成立，而"逐字节一致"仍仅同机同检出成立。）

**证据**（须与文本一同落盘，均为 2026-09-24 实跑）：

1. `python scripts/drift_gate.py --contract config/drift-contract.json --phase static --mode closed --no-history`
   ⇒ 健康态 rc=**0**，`total=14 block_fail=0 warn_fail=1`。
2. **核心 AC**：镜像里伪造 V1 / V2 的 `criteria_registry` ⇒ 改后 rc=**1**，
   判红 `eval-directed-axis-frozen-reading` + `eval-directed-axis-structural-guards`；
   **改前（模拟：#169 之前 = 契约副本去掉该字段）rc=0**。
3. **反误伤**：`sort_keys+indent=4` / `indent=2` / CRLF 重写 ⇒ 均 rc=**0**。
4. `python -m pytest scripts/tests/ -q` ⇒ rc=**0**（★ 计数为时点快照，判据是退出码）。
5. `python scripts/run_ci_ruff.py` ⇒ **14/14 PASS**；`drift_gate_smoke_test.py` ⇒ 通过。
6. 两张入库产物**零改动**：`git diff --stat -- doc/research/data/` 为空。

⇒ **请求用户：批准将上述 W2 子条的原地修订写入 `决策/业务-评测.md` 的
`D-2026-09-23-003`。** 在此之前，本 spec 与契约 `description` 里的相关表述
**不得**被读成"决策书已确认"。**Owner：测试；锁定：🔒（待用户批准后生效）。**

---

## 附：变更清单

| 文件 | 变更 |
|---|---|
| `scripts/eval_card_digest.py` | ★ **本票新增**：canonical 化 + sha256 的**唯一**实现（`VOLATILE_PATH_KEYS` / `strip_volatile` / `canonical_text` / `canonical_digest` / `digest_of_file` / `is_legal_digest`）。执行器与两套测试都 import 它（取证：`test_executor_and_tests_share_one_digest_implementation` 断言两侧是**同一个函数对象**） |
| `scripts/drift_gate.py` | 新增逐 check **可选** `content_digest`（缺省 `null`）：`content_digest_of` / `validate_content_digest` / `validate_content_digest_paths` / `_content_digest_mismatches` / `_fail_on_digest_mismatch_detail`；`evaluate` 里作为 `parse_as` **之后**的第三层；`--json` 报告**只新增** `results[].content_digest`；模块 docstring 的 schema 块与退出码清单同步。**退出码集合未变**（`0/1/2/3`） |
| `config/drift-contract.json` | 4 条 `eval-*` 新增 `content_digest`（2 个值，同轴两条相同）+ `description` 里登记摘要的含义与边界。**既有 11 条零变更**（取证：`ids added` 空、`ids removed` 空、`baseline content changed` 空）。★ **CRLF 保持**（取证：`git diff --numstat` 与 `git diff --ignore-cr-at-eol --numstat` **两者一致**，`git ls-files --eol` 仍为 `i/crlf w/crlf`） |
| `scripts/tests/eval_gate_fixtures.py` | 新增 `AXIS_BY_CHECK_ID` / `DIGEST_MODULE_NAME` / `artifact_of` / `repo_root` / `load_contract_document` / `declared_digest_of` / `declared_digest_by_axis` —— 摘要**期望值的唯一读取逻辑**（含"同轴两条不一致即判红"），供两套测试共用 |
| `scripts/tests/test_eval_gate_contract.py` | canonical 摘要节的**期望值来源换出处**（本地 `CANONICAL_DIGESTS` 字面量与本地 `portable_digest` 实现**已删**，改为从契约读 + 转调唯一实现）；`test_eval_checks_use_existing_schema_only` 的 allowed 集合加 `content_digest`；`_write_eval_contract` 新增 `with_digest` 开关（默认关闭 ⇒ R1–R5 用例继续隔离地测**正则层**，理由写在该函数 docstring 里）；新增：单一来源两条（函数对象 / 源码扫描）、契约↔产物互钉、卡片重放确定性、伪造 registry 核心 AC（V1/V2）+ 改前对照 + 反误伤 + 紧凑渲染边界登记、同源副本的**语义定义 + 负控** |
| `scripts/tests/test_drift_gate_content_digest.py` | ★ **本票新增**：`content_digest` 的**执行器行为**测试（缺省不校验 / 相符即通过 / 伪造判红且 detail 三位齐备 / 非法值 11 例 rc=2 / paths 恰一个 / 反误伤 4 例 / 真值改动判红 / 三层互不覆盖 / severity·mode 语义不变 / 报告字段零改动 + 只增字段 / 真契约精确清单 / 既有形态不受影响）。夹具常量一律取自 `eval_gate_fixtures.py` |
| `doc/specs/2026-09-24-decision-eval-content-digest.md` | 本文件 |
| `doc/specs/2026-09-23-decision-eval-regression-gate.md` | ★ **只加注，不删历史**：§6.5 W2 与 §7 第 7 条各加一条 `★ 现状（已由 #169 闭环）` 的**日期前向指针**（说明闭环方式、改前/改后实测、以及新边界），父 spec 原结论**原文保留** |
| `doc/specs/README.md` | 索引登记（§1 正式 spec 表新增一行） |
| `memory/2026-09-24.md` | ★ 新建：当日工作留痕（目标 / 做了什么 / 踩的坑 / 验证结果 / 遗留） |
| `.github/workflows/quality.yml` | **未改动**（取证：`git diff --name-only` 不含本文件）—— 门禁本身的摘要校验已随执行器生效，option (2) 目标的达成方式见 §3.5 |
| `doc/research/data/decision_eval_directed_card.json` | **未改动**（取证：`git diff --stat -- doc/research/data/` 为空） |
| `doc/research/data/decision_eval_timing_card.json` | **未改动**（同上） |
| `决策/**` | **未改动**（提案见 §8，待用户批准） |
