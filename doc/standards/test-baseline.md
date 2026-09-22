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

---

## §1 当日新增（倒序，最新在上）

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
