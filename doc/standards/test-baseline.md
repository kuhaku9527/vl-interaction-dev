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
| 1 | **帧链路**（摄像头/屏幕 → VLM） | 🟡 **2026-09-22 首次真机测（§1 #13）**：机制通、**内容空**（无 prompt）。**判定未做**；且**无「正常应返回什么」的基线** | 需判定 + 建立正常基线 |
| 2 | **proactive 轮真机** | ❌ 从未真跑。`LIVE_PROACTIVE_ENABLED` 默认 OFF，`proactive_supported:false` | live 两条产出决策的路径之一完全未验 |
| 3 | **多轮取中位** | ❌ 未做。存量只有 2 轮（且 26 例非面向中 12–14 例两轮不一致） | #157 的硬要求 |
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

