# 「游戏特化」可执行训练方案（2026-09-20）

> **端点身份**：LLM 后训练工程师
> **用户已拍板**：目标 = 游戏特化（看画面 + 答攻略）；数据 = 优先用上游开源数据集，不够就搜索找。
> **本轮性质**：**纯方案，零 GPU 任务、零下载、零源码改动**。需要实测的项全部进 §5 待确认清单。
> **证据分档**：[实测] 本机/本仓库文件或既有实测 ｜ [论文] 上游 PDF 原文 ｜ [外部] 公开证据（链接见文末）｜ [估算] 本轮推算，已给算式

## ⚠️ 前置声明：一份「必读」文件在工作区中不存在

**`doc/research/frontier-2026-posttraining-and-compression.md` 不存在。**
核查方式（三重）：`glob doc/research/*.md`（28 份，无此名）｜`es "frontier"`（全盘仅命中 Stardew 模组与无关包）｜
`grep -r "MOSS-VL|同构|前沿侦察" doc/`（零命中）。

⇒ **本轮无法引用该报告的任何内容（含 MOSS-VL 同构工作）。** 若该文存在于别处或未落盘，请主端点补入。
本方案不依赖它：所有结论均由「已确认事实 + 上游 PDF 原文 + 可复核算式」独立推出。

## 0. 正面回答：「我的显卡行不行？」

### 0.1 一句话

**行——但要看你训什么、以及怎么把权重搬进去。** 16 GB 单卡对本题**不是瓶颈**；上次把机器跑爆的是**加载路径 + WSL 内存上限**，与显存容量无关。
**并且我要修正自己上一轮的一个数**：16.7 GB 那个估算，低估了，也**被当成了错误的用途**（见 §0.4）。

### 0.2 ★ 上次为什么把机器跑爆（与显存无关的三重根因）

| # | 根因 | 证据 |
|---|---|---|
| **1** | `AutoModelForCausalLM.from_config()` 在**主机 RAM** 里先把整个 8B 建成 **bf16 ≈ 16 GB** | 脚本直读：`from_config(cfg).to(torch.bfloat16)` 之后才 `.cuda()` |
| **2** | **WSL 的内存上限实测只有 11.72 GiB**（不是 32 GB） | `C:\Users\22186\.wslconfig` 直读：`memory=12582912000`、`swap=34359738368`、`processors=8` |
| **3** | swap 32 GiB 落在 `D:\wsl\Ubuntu\ext4.vhdx` | `es "*.vhdx"` 直读；D 盘余量 122 GB |

⇒ **16 GB 的 bf16 权重在 11.72 GiB 的 RAM 里根本放不下**，第一步就进 swap；再逐层 `.cuda()`，等于
「磁盘→RAM→pinned→GPU」四段搬运，**磁盘 I/O 爆炸、GPU 几乎没动**，与观测现象完全一致。

**日志是决定性的**：`Tried to allocate 88.00 MiB. GPU 0 has a total capacity of 15.93 GiB of which 12.29 GiB is free.`
—— 空闲 12.29 GiB 却分配不出 88 MiB，**这不是容量问题**（该尺寸分配在容量充足时失败，典型原因是碎片、锁页内存耗尽、或搬运路径本身）。

**正确加载路径（本轮第一条硬结论）**：

```python
model = AutoModelForCausalLM.from_pretrained(
    hf_dir, load_in_4bit=True, device_map={"": 0},
    low_cpu_mem_usage=True, torch_dtype=torch.bfloat16)   # 分片加载，逐片在 GPU 上量化
```
主机 RAM 峰值 ≈ **单个 safetensors 分片（2–3 GB）**，而不是 16 GB。**这一条改变一切。**

### 0.3 分档判断（每档给算式，不只给结论）

统一口径：本机可用显存 = `16,311 MiB（torch 报的 15.93 GiB）− ~1,000 MiB 桌面/WSL 基线 ≈ 15,300 MiB`。
Qwen3-8B 几何 [实测/config]：36 层 / hidden 4096 / 32 heads / 8 kv heads / vocab 151,936 / intermediate 11,264。

**8B QLoRA 逐项拆解** [估算，算式可复核]：

| 项 | MiB | 算式 |
|---|---|---|
| NF4 权重（linear） | 4,000–4,100 | 7.11B 线性参数 × 0.5625 B/值（4bit + 64 块 absmax fp32） |
| **embed_tokens 保持 bf16** | **1,240** | 151,936 × 4096 × 2 B ← **旧估算漏项** |
| LoRA r=16 全 linear + 梯度 + 8bit AdamW | 200–450 | 41.9M 可训参数 ×（2+2+4+2）B ← **旧估算按 0.1 GB，偏低** |
| 激活（grad ckpt，seq 4096，bs 1） | 1,200–1,600 | 36 层 × 1×4096×4096×2 B ≈ 1.21 GB + 单层重算瞬态 |
| logits（**fused/chunked CE**） | 50–150 | 分块后峰值 ≈ chunk×151,936×2 B |
| ViT 前向瞬态（冻结，1 帧 432 tok） | 150–350 | 888×540 → 432 tok（32 px/token，mmproj 元数据实读） |
| CUDA ctx / cuBLAS ws / 碎片 | 800–1,300 | `expandable_segments:True` 下的经验区间 |
| **峰值合计** | **7,500–9,300** | **余量 ≈ 6 GB** |

⚠️ **同一配置若不开 fused CE**：logits 走 bf16→fp32 上采样，`4096 × 151,936 × 4 B = 2.37 GB`，反向再来一份梯度 ⇒ **额外 +2.4 ~ +5.0 GB**，峰值冲到 **10–14 GB**，就变成「贴边走」。
**⇒ fused/chunked CE 是本方案的第二条硬结论（第一条是加载路径）。**

**六档对照**：

| 档 | 配置 | 峰值显存 | 判定 | 理由 |
|---|---|---|---|---|
| **0** | **冻结主干 + 特征缓存，只训分类头/适配器** | **< 1 GB GPU** | **★ 可行（首选做基线）** | 主干冻结 ⇒ 前向跑一次把 last-hidden 落盘，之后在 CPU/RAM 上训头。**GPU 成本≈0** |
| **1** | **8B QLoRA（推荐配置）** | **7.5–9.3 GB** | **★ 可行，余量 ~6 GB** | 加载路径 + fused CE + gckpt + 1 fps 单帧 + seq ≤4096 |
| 2 | 8B QLoRA，seq 8192 | 8.7–10.9 GB | ✅ 可行 | 激活线性翻倍；仍进屋 |
| 3 | 4B LoRA（Qwen3-VL-4B，hidden 2560） | 5.5–7.0 GB | ✅ 舒适 | 但 KV/层数与 8B 相同（36×8×128），换它同时丢掉上游四态训练 |
| 4 | 2B LoRA | 3.0–4.5 GB | ⚠️ 显存够，**能力高危** | When2Speak：**1B/3B 崩成「永远静默」**，4B 才是安全下限 |
| 5 | 8B LoRA（bf16 主干，非量化） | **16 GB 权重独占** | ❌ **不可行** | 权重本身已打满整卡 |
| 6 | 8B 全参 SFT | **116 GB** | ❌ **不可行** | fp32 参数+梯度+优化器状态 |

### 0.4 修正我上一轮的 16.7 GB 估算

**方向对，但缺项，而且被用错了地方。** 三处低估（均为本轮新增核算）：

1. **漏了 embed_tokens**：bnb 量化只替换 `nn.Linear`，`nn.Embedding` 保持 bf16 ⇒ **+1.24 GB**。
2. **漏了未开 fused CE 时的 logits 代价** ⇒ 潜在 **+2.4–5.0 GB**（这是最大的一项，也是唯一能把 8B QLoRA 从「有余量」变成「贴边走」的开关）。
3. **LoRA 侧偏乐观**：旧表按 r=8/只挂 q,v,o 算 0.1 GB；若 r=16 + 全 linear 挂载，实际 **0.2–0.45 GB**。

反向两处高估：碎片项（旧 1–2 GB → 现 0.8–1.3 GB）与激活项（seq 2048 → 1.2–1.6 GB @ seq 4096，基本持平）。

**净结论**：`16.7 GB` 是「**未优化配置的取值**」，**不是下界，更不能当作「超卡」的依据**。
优化配置下 8B QLoRA ≈ **7.5–9.3 GB**，16 GB 单卡**装得下且有余量**。
**真正该改的不是卡，是（a）加载路径（b）fused CE（c）WSL 内存上限。**

## 1. 「游戏特化」到底该训什么？

### 1.1 先回答：这和上游的「何时说话」是同一个能力吗？

**不是，是两个正交的能力，几乎没有交集。**

| 维度 | 上游四态决策 | 用户要的「看画面答攻略」 |
|---|---|---|
| 输出 | **1–3 个 token**（`</silence>`/`</response>`/`</delegation>`） | **长文本**（几十到几百 token） |
| 信息在哪 | **输入表征里**（低熵判决） | **模型权重 + 外部知识里**（高熵生成） |
| 失效模式 | 该沉默时乱插话 | **内容答错**（幻觉） |
| 上游原话 | §3.2「每秒一个动作」 | §5.3「commentary 阶段**偶尔幻觉**」 |

**⇒ 用户选的 (b) 不是 (a) 的加强版，而是一个新的训练目标。** 本方案按 (b) 展开，但下面两条必须讲清：

**⚠️ 诚实提示（重要）**：**本项目自己实测到的、有确凿训练头寸的缺口，恰恰是 (a) 不是 (b)。**
`eval-production-prompt-measured-2026-09-20.md` 实测：生产 prompt 的 `</not-for-me>` **泛化召回 0–5.6%**，
且 prompt 里**逐字教过的句子 0 例照抄成功**——该报告自己的结论是「**问题不在'没教'，而在'教了也不执行'**」，
即 **prompt 层已经饱和，只剩训练这条路**。这与 When2Speak / Speak-or-Stay-Silent（「8 个 LLM 零样本一致无法做好轮次转换，不是涌现能力，必须显式训练」）完全同向。

**⇒ 建议：把 (b) 作为主目标，但 (a) 的 addressee/决策头作为「顺带修复项」一并纳入同一次训练**（零额外成本，
数据现成、评测集现成 51 例）。**不要浪费这个已经证实的头寸。**

### 1.2 (b) 的失效根因：先分清「看不见」还是「不知道」

用户原话是「基本够用，**偶尔错**但能接受」。「偶尔错」只有两种来源，**处置方式完全相反**：

| 根因 | 表现 | 正确工具 | 训练有用吗 |
|---|---|---|---|
| **(i) 不知道**（boss 弱点、物品位置、版本数值） | 画面看对了，答案编了 | **RAG / Local Wiki / delegation** | ❌ **没用**——事实随版本变更，训进权重=出厂即过期 |
| **(ii) 看不见**（HUD 血量、小地图、物品名、怪物当前动作） | 检索 query 就是错的 → 答案必错 | **视觉 grounding 训练** | ✅ **有用** |

**而 (i) 这条路本项目已经修好了** [实测]：`hermes-integration.md` 记录 `</delegation>` → shim(8079) → MiniMax M2 + web_extract → 11 s 拿回整理后的攻略；
`memory-architecture.md` 记录 **Local Wiki（memory-store:8997）原始定义就是「游戏攻略 / 角色 lore 预置外部知识库」**，bge-m3 语义召回已落地。

**⇒ 判断：在 (i) 上训练是「用错工具」。** 正确动作是把 Local Wiki 的攻略库建起来 + 修召回注入，**成本远低于训练**。
**⇒ 训练只在 (ii) 成立时才值得做**——这是一个**必须先测量才能决定**的分叉，见 §5 #6/#7。

### 1.3 训练目标怎么定义（若 (ii) 成立）

**目标（一句话）**：教会模型在 1 fps 画面流下，**先做可验证的场景 grounding，再把需要外部事实的部分路由出去**，
而不是直接编答案。

**损失设计**（上游 §3.3 原文公式为基础，标注我的改动）：

上游原文（逐字采信）：
```
L(θ) = −(1/|A|) Σ_{j∈A} w_j · log p_θ(y_j | y_<j)
w_first_silence = 1 ； w_repeated_silence = 0.4 ； w_response = 1.5
# delegation 无独立权重（总嵌在 response 里）；加权损失只用于 time-aligned 数据，常规数据用标准 SFT loss
```

**本方案的三处改动**（**均为本轮设计，非上游原话，须标注**）：

1. **加权集合 C 从 2 个控制 token 扩到 4 个**：加入 `w_delegate = 1.5`、`w_ground = 1.5`。
   - 理由：上游说「delegation 无需独立权重，因为它总嵌在 response 里」——那是**当 delegation 是既定行为**时成立。
     游戏场景要的是**判别**「该答 vs 该查」，必须给它独立权重，否则淹没在 response 的 1.5 里。
2. **输出前加一段结构化 grounding 前缀**（可机器校验、标注成本低）：
   `</response> <scene game="…" hud_hp="…" target="…"/> 正文…` —— 对这段单独加 `w_ground=1.5`。
3. **每条样本仍逐秒一对** user/assistant（保持上游格式），但**每秒只放 1 帧**（理由见 §3.4）。

**数据格式（逐字对齐上游附录 Listing 1–3）**：

```json
{"role":"user","content":"<4.0 seconds>\n<image>"}
{"role":"assistant","content":"</silence>"}
{"role":"user","content":"<5.0 seconds>\n<image>"}
{"role":"assistant","content":"</response> <scene game=\"Elden Ring\" target=\"Margit\" hud_hp=\"68%\"/> 血条过半，二阶段快到了，留一个闪避。"}
{"role":"user","content":"玛尔基特弱什么？\n<6.0 seconds>\n<image>"}
{"role":"assistant","content":"</response> 我查一下。 </delegation> 艾尔登法环 玛尔基特 弱点 出血 抗性"}
```

### 1.4 这个目标值不值得训？——我的判断（分三层，不含糊）

| 层 | 判断 | 依据 |
|---|---|---|
| **(i) 攻略事实** | ❌ **不训**。用 RAG + Local Wiki + delegation | 事实有版本、且检索路径**已建好并已实测跑通 11 s** |
| **(ii) 视觉 grounding** | ⚠️ **先测再定**。这是唯一值得训的方向 | 公开证据最强：VideoGameBunny 在 **HUD/UI +21.0 pp、Anomalies/Glitches +32–34 pp、Gameplay Mechanics +8.9 pp**（30K 样本），且**小模型可超过 4.2× 参数的 LLaVA-1.6-34B**（85.1% vs 83.9%） |
| **(a) addressee/决策** | ✅ **该训**（本项目自己实测的头寸） | 生产 prompt 泛化召回 0–5.6%；prompt 已饱和 |

**⇒ 总判断**：**「game 特化」的合法训练面很窄——只有 (ii)+(a)，且 (ii) 必须先被测量证明。**
**不是「prompt/RAG 就能达成全部」，但「prompt/RAG 能达成的那部分恰好是用户最痛的那部分（答错）。」**
先花 1 天测 §5 #6/#7，再决定是否花 30 小时训练。

## 2. 数据方案

### 2.1 上游数据集能否用于「游戏特化」？逐条判定

`jdopensource/JoyAI-VL-Interaction`，4M+ 样本，标注 JSON ≈3.49 GB（不含视频）。

| # | 项 | 判定 | 说明 |
|---|---|---|---|
| 1 | **每秒对齐格式 + 加权损失配方 + delegation 协议** | ✅ **能用（最有价值）** | 这是「可迁移的方法」，论文 §3.2 自称 *"transferable recipe"* |
| 2 | `response` 标签 | ✅ 能用 | 唯一既显式标注、又有明确时刻语义的态 [实测核实] |
| 3 | `delegate` 标签 | 🟡 **需加工** | 仅内联标记 `</delegation>`，可靠 `task_type=="background"` 或字符串匹配后处理打标；**README 未书面确认该等价性（属假设）** |
| 4 | `silence` 标签 | ⚠️ **能推但语义被污染** | 是 `convert_data.py` 的 **else 默认填充**，把「正在思考的延迟窗口」与「真无人说话」混为一类且不可区分 |
| 5 | `not-for-me` 标签 | ❌ **零命中** | `datasets/` grep 零命中；转换管线无任何产出路径；管线未开源（issue #13 未兑现） |
| 6 | **游戏画面内容** | ❌ **完全没有** | 六大家族 = 监控告警/时间对齐问答/计数/解说/闲聊/委派；**游戏不是任何一类** |
| 7 | **攻略答案内容** | ❌ **没有** | 上游不承载任何游戏知识；且攻略内容**天然不适合作训练目标**（见 §1.4） |
| 8 | **分布一致性** | 🟡 **可对齐** | fps 按时长自适应（≥160s→1.0，≥64s→2.0，否则→4.0）；**评测集必须复现同一套 fps 与 320s 截断规则**，否则与训练分布不一致 |

**⇒ 结论：上游数据集提供「配方」和「格式」，不提供「内容」。
游戏画面必须自采，攻略知识必须外挂。**——这一条不可绕开。

### 2.2 「不够就搜索找」：具体来源（已核实）

| 来源 | 规模 | 与本案的契合度 | 用途 |
|---|---|---|---|
| **`VideoGameBunny/Dataset`** [外部] | 185,259 张真实游戏图（**413 款游戏**）+ **389,565 图文指令对**（短标题 70,673 / 长标题 70,799 / **image-to-JSON 136,974** / QA 81,122）；评测集 3,375 MCQ × 10 类 | ★★★ **最契合**：真实 3A/主机游戏画面 + **结构化 grounding 监督**（16 元素 JSON），且给了逐类增益 | **训练 (ii) visual grounding** |
| **`OpenMOSS-Team/GameQA-140K`**（Game-RL, arXiv:2505.13886）[外部，已读 dataset viewer] | **140K QA**（126,760 train / 15,047 test），30 款游戏、158 个可验证任务、4 大类（多步推理/模式识别/策略规划/3D 空间）；字段含 `question` + `options` + `answer` + `analysis` + `refinement` | ★★ 但**全部是小游戏/益智游戏**（扫雷/空当接龙/祖玛/Tetris/数独/Pac-Man/Minecraft 风格/迷宫/生命游戏…），**没有 3A 剧情与攻略** | **训练坐标/网格级视觉推理 + 评测** |
| **VideoGameQA-Bench**（arXiv:2505.15952, NeurIPS 2025 D&B）[外部] | 基准，非训练集：视觉单测/回归/针尖找麦/glitch 检测/bug 报告 | ★★ | **只作评测集**（「看画面」对不对） |
| **上游 `JoyAI-VL-Interaction`** | 见 §2.1 | ★★ 格式 | **配方与格式** |
| **自采：用户自己的 9 款游戏** | 1 fps 采集，管线已存在（`screen_capture.js`） | ★★★ **唯一能拿到「用户真实画面」的路** | **训练 (ii) + 评测（不可替代）** |
| **攻略文本（Fextralife / Fandom / GameFAQs / NGA / B 站）** | — | — | **Local Wiki 语料，不是训练数据** |

⚠️ **三项必须先核实再下载**：
(a) VideoGameBunny 数据集的**许可**（论文/站点未明示，需查 HF dataset card）；
(b) GameQA-140K 的**许可**（OpenMOSS 通常 Apache-2.0，需核实）；
(c) 上游数据集已确认 **apache-2.0** [实测]。
**许可未确认前不得下载或用于训练。** 见 §5 #9。

### 2.3 若需自建：最小可行规模与造法

**规模锚点**（来自 VideoGameBunny 的消融表，[外部] 实测）：

| 子集 | 2K | 5K | 10K | 20K | 30K |
|---|---|---|---|---|---|
| image-to-JSON | +3.8 | +5.6 | +7.6 | +8.8 | **+9.8 ~ +11.7** |
| GPT-4o QA | +4.5 | +7.3 | +6.1 | — | — |
| 短标题 | −0.3 | +0.8 | **−35.5（崩塌）** | — | — |
| 混合(Weighted) | 79.0 | 79.8 | 81.4 | 82.3 | **82.6** |
**⇒ 最小可行规模**：
- **冒烟（判方向）：2,000 条** —— 若方向对，2K 就已可测出 +3.8 pp 量级增益。**2K 无增益 = 方向错，立即止损。**
- **MVP（可上线）：10,000 条** 结构化 grounding（image-to-JSON 风格）。
- **饱和点：30,000 条**（超过后混合策略收益收敛）。
- ⚠️ **不要用短标题类数据**（>10K 时崩塌 −35.5 pp）。**不要用纯 caption。**

**自建造法**（照搬论文 §3.2 的多阶段流水线，替换「内容可信」这一前提）：

1. **采集**：1 fps 抓用户真实游戏画面（管线现成）。10 小时游戏 = 36,000 帧；按 10 s 窗口 = **3,600 条**。
2. **打字**：沿用论文的**三种时序类型**（backward / present / forward）——forward 型（问题先出现、证据后出现、模型须保持沉默直到证据出现）对本项目**尤其有价值**，因为它把「等待」也变成监督信号。
3. **标注**：大 VLM 生成 grounding 前缀 + 答案，**但把论文的「内容可信」前提换成「必须逐帧可验证」**——本项目的失效模式正是内容幻觉。
4. **两级校验**（论文原文做法）：**全局**（全部输入帧 + 完整标注）与**局部**（标注时刻的帧 + 绑定的回复）；**任一不过即丢**。
5. **攻略部分不标**：凡需要外部事实的样本，金标注就是 `</delegation> + 检索 query`，**不写答案**。这样训练集不会注入过期事实。

**成本量级** [估算]：3,600 条 × 1 次大模型调用 ≈ 3,600 次 VLM/LLM 调用；若走云 API ≈ **¥50–300**。
**真正的成本是人工抽检（建议 5–10%，约 200–360 条）与评测集建设，不是 GPU。**

## 3. 训练方案（可执行）

### 3.1 基座选择：训哪个

**推荐：继续训上游 JoyAI-VL-Interaction 8B（HF bf16 权重，QLoRA）。**

| 选项 | 判定 | 理由 |
|---|---|---|
| **上游 8B QLoRA** | **★ 选它** | ① 部署链不动：merge→量化→**mmproj 对齐校验**路径已知，ViT 同源；② **保住上游四态决策能力**（换基座=丢弃，且上游未开源配方、本机无法重训）；③ 峰值 7.5–9.3 GB 有余量（§0.3） |
| Qwen3-VL-4B | ❌ 不选 | 显存确实更省（5.5–7 GB），但① 四态决策全丢，② KV 几何与 8B **一字不差**（36×8×128，只省权重 2.2 GB），③ 部署侧要重建 mmproj 与量化链，运维面放大 |
| 2B / 更小 | ❌ 不选 | When2Speak：**1B/3B 崩成永远静默**；4B 才是决策安全下限 |
| 训决策头 | ✅ **并行做**（作为基线，不是替代） | 成本≈0，且能回答「8B 到底值不值」 |

**⇒ 先做「决策头 + 特征缓存」当基线（§5 #11），它便宜到可以顺手做掉；
若决策头已接近 8B prompt 基线 ⇒ 说明容量不是瓶颈，重估整条路线。**

### 3.2 方法

| 项 | 值 | 理由 |
|---|---|---|
| 量化 | **NF4（bnb 4bit）**，double quant 开 | sm_120 已验证通过 |
| PEFT | **LoRA r=16, alpha=32, dropout=0.05, bias="none"** | r=8 更省（优化器 200 MB vs 420 MB），若显存吃紧降 r |
| **目标模块** | **LLM 侧全 linear**：`q,k,v,o,gate,up,down` | 6 处全挂 → 41.9M 可训参数（vs 只挂 q,v,o 的 ~14M） |
| **视觉塔** | **完全冻结（不训、不打 LoRA）** | 论文的 projection layer 是从零训的，动它会破坏 ViT↔LLM 对齐；且 1 帧 432 token 的 ViT 前向只占 150–350 MiB |
| 优化器 | **bitsandbytes `AdamW8bit`** | 已验证 |
| 梯度检查点 | **开**，且**必须** `enable_input_require_grads()` | 项目已实测：否则梯度断裂 |
| **损失** | **fused / chunked cross-entropy**（Liger-Kernel 或 transformers 原生融合 loss） | **硬性**：省 2.4–5.0 GB（§0.3） |
| 序列 | **≤4096**（8–10 s × 1 帧/s + 文本） | 见 §3.4 |
| batch | micro-batch **1** + grad accumulation **8–16** | 有效 batch 8–16 |
| 学习率 | **1e-4**（LoRA），cosine，warmup 3% | 低熵行为微调，1e-4 稳妥 |
| epoch | **1–2** | 上游自称「time-aligned 训练远未饱和、数据高效」；过度训练会冲刷四态决策 |
| 精度 | bf16 计算 / NF4 主干 | — |
| 环境变量 | `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` | 碎片 |
| 数据混合 | **游戏数据 + 上游 time-aligned 数据 + 常规 turn-based 数据** | 上游 §3.3 原话：*"mix the time-aligned interaction data into a large pool of conventional turn-based data"*；**这是防遗忘的关键** |

### 3.3 显存预算（逐项 + 峰值）

见 §0.3 表。**峰值估算 7,500–9,300 MiB（seq 4096, bs 1, r16, gckpt, fused CE）。**
可用 15,300 MiB ⇒ **余量 ≈ 6 GB**。若 seq 升到 8192 ⇒ 8,700–10,900 MiB，仍进屋。

**主机侧预算**（同样是上次的爆点）：

| 项 | 峰值 | 备注 |
|---|---|---|
| 权重分片加载 | 2–3 GB | `low_cpu_mem_usage=True` 分片加载 |
| 数据集 + dataloader | 1–3 GB | 预先把 JPEG 解码后的张量流式读，**不要一次性全解** |
| Python/torch/CUDA | 1.5–2.5 GB | — |
| **合计** | **5–8 GB** | **须 < 10 GiB**（WSL 当前上限 11.72 GiB） |
| 磁盘 | **≥ 60 GB** | 模型 17 GB + 数据集（Bunny 图片量大）+ checkpoint + swap |

⇒ **§5 #1：把 `.wslconfig` 的 `memory=` 提到 20–24 GiB**（宿主 31.9 GiB），
**并在「玩游戏」时 `wsl --shutdown` 释放内存**——不要让 WSL 常驻吃掉游戏的 RAM。

### 3.4 ⚠️ 必须在训之前定下的一个数：每秒几帧

- 本机采集：**1 fps**，`frames_per_batch=1`，`LIVE_FRAME_WINDOW` 默认 6。
- 上游格式：每秒一条 user message，携带**每帧 1–4 个 `<image>`**（Listing 1 是 2 个，Listing 2/3 是 4 个）。
- 本机 mmproj 元数据实读：`patch_size=16`、`spatial_merge_size=2` ⇒ **32 px/token**；888×540 → **432 token/帧**。

| 每秒帧数 | 每帧 token | 8 s 轨迹的视觉 token | 判定 |
|---|---|---|---|
| **1 帧/s** | 432 | **3,456** | **★ 与部署一致，seq≈4k，可行** |
| 4 帧/s（上游 Listing 2/3） | 432 | 13,824 | seq 14k+，**不可行** |
| 6 帧/s（LIVE_FRAME_WINDOW=6） | 432 | 20,736 | **不可行**；且 6 帧 1MP 时单轮即 ~6,144 token |

**⇒ 用 1 帧/s 训练。** 但 **`vlm-lightweight-2026-09.md` 把这个数标为「需确认」（其 §2 表 #9）**——
**§5 #12 必须先查清单轮实际帧数**，否则训练分布与部署分布不一致，训了也白训。

### 3.5 在哪训：本机 WSL，不租卡

**本机足够。** 吞吐估算 [估算]：
- 5060 Ti（448 GB/s）LoRA+QLoRA+gckpt @ seq 4096 ≈ **1.5–3k tok/s**（4090 的 ~40–50%）
- 10,000 样本 × 4,000 token × 2 epoch = **80M token** → 80e6 / 2e3 ≈ 40,000 s ≈ **11 小时**（取 **10–30 h** 含调试）

**成本对照**（若确需外租）：4090 ¥1.8–2.5/h ⇒ 20–60 卡时 = **¥36–500**（量级参考，非报价）。
**⇒ 本机过夜可完成，租卡只为「seq 必须 >8k」或「数据 >100K」两种情况保留。**

### 3.6 怎么知道训好了（验证方法）

**三层，缺一不可：**

1. **回归（防遗忘）—— 用现成资产**：
   - `services/scripts/benchmark_4state_notforme.py`（51 例）+ `benchmark_production_live_prompt.py`。
   - **硬门槛**：`directed → not-for-me` **保持 0**（生产 prompt 实测两轮恒为 0）；非面向**开口率不得高于基线 34–38%**。
   - ⚠️ 该测试集有**效度缺陷**：8 句非面向句与生产 prompt few-shot **逐字重叠**（开卷考）。
     **必须按泛化子集（18 例）算主指标**，否则训完看不出真实增益。
2. **游戏能力（新增，必须自建）**：60–100 题 / 3 款游戏，三类：
   - (a) **grounding MCQ**（借 VideoGameQA-Bench 的分类：HUD/UI、Small Details、Spatial Reasoning、Anomalies/Glitches）——测「看不看得见」；
   - (b) **答案正确性**（有参考答案）——测 (i)；
   - (c) **路由正确性**：需要外部事实的题，模型是 `</delegation>` 还是**编答案**。指标：**幻觉率** + delegation 精确率/召回率。
3. **部署链校验**：merge → convert → IQ4_NL → mmproj 对齐 → **确认启动日志 `flash_attn = enabled`**（否则 `-ctv q8_0` 直接抛错）→ 同图同问 A/B。

**判据（预先写死，避免事后找理由）**：
- ✅ 通过：(a) 提升 ≥5 pp **且** (c) 幻觉率下降 ≥20% **且** 51 例回归无退化。
- ⚠️ 灰色：只有 (a) 提升。→ 说明训练只买到「看得更清」，**不足以解决用户的「偶尔错」**，转 RAG 路线。
- ❌ 失败：回归退化，或 (b)(c) 均无变化。

## 4. 风险与止损

### 4.1 最大风险：**为了修「答错」，把「何时说话」训坏**

四态决策是**本项目独有的、无法重建的核心资产**（上游未开源配方、本机无法重训）。
它由极少数 token 维持（`</silence>` 是**单 special token id 151669**，`</response>` 是 151670），
**LoRA r=16 全 linear @ lr 1e-4 @ 2 epoch 完全有能力把它冲掉**。

**缓解（三条，全部必做）**：
1. **数据混合**：游戏数据必须与上游 time-aligned + 常规 turn-based 数据同训（上游 §3.3 原话做法）。
2. **r 保持 8–16、epoch ≤2、lr ≤1e-4**。
3. **每 N 步在 51 例上跑一次回归，早停**。

### 4.2 其他风险

| # | 风险 | 缓解 |
|---|---|---|
| 2 | **机器再次被跑爆** | 硬规则：**禁用 `from_config`**；只用 `from_pretrained(load_in_4bit, device_map, low_cpu_mem_usage)`；跑之前 `df`/`free` 检查；WSL 内存上限显式设定；**训练期间不玩游戏** |
| 3 | **训进会过期的事实** | 攻略类样本的金标注是 `</delegation>+query`，**不写答案**（§2.3 第 5 条） |
| 4 | **数据许可污染** | 三项许可核实通过前不下载（§5 #9） |
| 5 | 部署链被打碎 | 不动 ViT、不动 projection；merge 后按既有 GGUF 路径复算并 A/B |
| 6 | sm_120 内核坑 | 已验证通过（bnb 0.50.2 / torch 2.11+cu130）——但**新装的 Liger-Kernel 需单独验**（§5 #4） |
| 7 | 评测集效度 | 泛化子集作主指标 |

### 4.3 ★ 什么情况下应该放弃训练（判据）

**任一条命中即止损，不要「再调调看」：**

1. **§5 #6/#7 测出瓶颈是「知识」而非「视觉」** ⇒ 不训，做 RAG。**这是最可能的结局。**
2. **决策头基线（§5 #11）已接近 8B prompt 基线** ⇒ 容量不是瓶颈 ⇒ 8B 后训练整条路线作废。
3. **2K 冒烟无增益** ⇒ 方向错（VideoGameBunny 证据：方向对时 2K 就有 +3.8 pp）。
4. **51 例回归在两次配置调整后仍退化** ⇒ 止损回滚。
5. **8B QLoRA 峰值显存三次配置调整后仍 >14 GB，或主机 RAM >10 GiB** ⇒ 降到 4B/只训头。
6. **训练只能提升已记忆的事实类题目**（泛化子集不动）⇒ 训的是过期知识，废弃。

## 5. 待确认清单（交给主端点执行）

> 格式：**事项 → 怎么测 → 预期耗时 → 资源占用**。全部**不含训练**，最高只到「一次前向+反向」。

| # | 事项 | 怎么测 | 预期耗时 | 资源占用 |
|---|---|---|---|---|
| **1** | **WSL 内存上限** | 已读实：`memory=12582912000`（**11.72 GiB**）。动作＝改到 20–24 GiB（宿主 31.9 GiB），并定「玩游戏时 `wsl --shutdown`」纪律 | 5 min | 无 |
| **2** | **8B QLoRA 真实峰值显存**（决定性） | **正确加载路径**：`from_pretrained(load_in_4bit, device_map={"":0}, low_cpu_mem_usage=True)` 载真实 HF 权重；1 次 fwd+bwd @ seq 4096 / bs 1 / r16 / gckpt / **fused CE**；报 `max_memory_allocated()` + `nvidia-smi` | **1–2 h**（+下载） | GPU ~9 GB、RAM ~6 GB、磁盘 **17 GB** |
| **3** | **加载期主机 RAM 峰值** | 与 #2 同进程，采 `vmmem` / RSS 峰值。**>10 GiB 即失败** | 含在 #2 | 同 #2 |
| **4** | **fused/chunked CE 可得性** | 查现有 transformers 是否有融合损失路径；试装 Liger-Kernel 到 `joyai-vllm`；**在 sm_120 上跑一次 kernel** | 30 min | 磁盘 ~100 MB，GPU 瞬时 |
| **5** | **ViT 冻结前向的真实开销** | 对照测：ViT 在线前向 vs 预抽 image embedding，各测峰值 | 1 h | GPU ~8 GB |
| **6** | **★ Grounding 基线（决定训不训）** | 从用户真实游戏截 60–100 组（截图+问题+参考答案），跑**当前部署的 llama-server**，分类打分（HUD/UI、小细节、空间、glitch） | **3–6 h**（主要在写题） | GPU 推理常态 ~8 GB，无训练 |
| **7** | **★ 知识 vs 视觉 拆分（决定训不训）** | #6 重跑一遍，**开 Local Wiki 召回**，看 Δ。知识型提升大 ⇒ 走 RAG；视觉型不动 ⇒ 才训 | +1 h | 同 #6 |
| **8** | **delegation 路由正确率** | 造 30–50 条「需要外部事实」的游戏问题，统计**编答案 vs `</delegation>`** | 1 h | GPU 推理 |
| **9** | **数据许可核实** | VideoGameBunny dataset card / GameQA-140K（OpenMOSS）/ 上游（已确认 apache-2.0）。**未确认不得下载** | 30 min | 纯网络 |
| **10** | **数据集体积 vs 磁盘** | D 盘余 **122 GB**。VideoGameBunny 185K 图很可能数十 GB ⇒ 评估「只取 image-to-JSON 子集」 | 15 min | 纯网络 |
| **11** | **决策头基线**（便宜且高价值） | 冻结主干，前向一次缓存 last-hidden（可落盘），训 4 类线性头；与 8B prompt 基线比准确率 | 2–4 h | GPU <2 GB（仅前向） |
| **12** | **★ 单轮实际帧数** | 查 `LIVE_FRAME_WINDOW`(默认 6) 与 `frames_per_batch`(1) 的真实交互；抓一次启动/请求日志确认送了几帧 | 30 min | 无（读源码/日志） |
| **13** | **口径对账** | 用户给的 KV q8_0 后显存 **8,014 MiB**，而 `kv-quantization-measured-2026-09-20.md` 表内是 **8,215 MiB**（9,535−1,320）。差 **201 MiB**，需确认哪个是发布口径 | 10 min | 无 |
| **14** | **缺失报告归位** | `frontier-2026-posttraining-and-compression.md` 在工作区不存在（三重检索零命中）。若存在请补入 | 5 min | 无 |

**建议执行顺序**：**#12 → #1 → #4 → #2/#3 → #6 → #7**。
**理由**：#12 决定序列长度（错了全盘重来）；#6/#7 是全案的分叉点；#2/#3 是唯一可能再伤机器的项，放在环境修好之后。

---

## 参考

**本仓库（一手）**：`JoyAI-VL-Interaction-Reportv1.pdf`（§3.2 数据构造 / §3.3 加权 CE+GRPO+EasyVideoR1 / §5.3 Limitations / 附录 Listing 1–3）｜
`doc/research/`：`posttraining-verdict`（本人上一轮；本报告修正其 16.7 GB 估算）、`eval-dataset-labels`、`eval-production-prompt-measured`、
`kv-quantization-measured`、`llamacpp-vram-field-notes`、`lightweight-vlm-candidates`、`vlm-lightweight-2026-09`、`capture-resolution-chain`（均 2026-09-20）｜
`services/scripts/benchmark_4state_notforme.py`（51 例）、`scripts/wsl-setup-qlora.sh`、`scripts/measure-qlora-8b-real.py`｜
`doc/specs/addressee-detection.md`、`doc/subsystems/hermes-integration.md`、`doc/subsystems/memory-architecture.md`｜
本机直读：`C:\Users\22186\.wslconfig`、`df -h`、`wmic`、`es "*.vhdx"`

**外部（仅作数据）**：[VideoGameBunny](https://videogamebunny.github.io/)（WACV 2025；185,259 图 / 413 游戏 / 389,565 指令对；HUD+UI +21.0 pp）｜
[VideoGameQA-Bench](https://arxiv.org/html/2505.15952)（NeurIPS 2025 D&B）｜[GameQA-140K](https://huggingface.co/datasets/OpenMOSS-Team/GameQA-140K)（arXiv:2505.13886）｜
[When2Speak](https://arxiv.org/abs/2605.05626)（1B/3B 崩成永远静默；4B≈8B）｜[Proact-VL](https://arxiv.org/abs/2603.03447)（ICML 2026）｜
[Spheron 2026 VRAM 指南](https://www.spheron.network/blog/gpu-vram-requirements-fine-tune-llm-2026/)（gckpt 只降激活，不降非激活项）

---

*本报告为只读方案输出：未跑任何 GPU 任务、未下载任何模型或数据集、未修改仓库任何既有文件（仅新增本文件）。*
*所有 [估算] 项已给出算式，可复核；所有 [实测] 项均标注了直读来源。*
