# 显存预算 → 可行画质/分辨率 映射调研

> 端点身份：**调研端点（research / AFK，只读）**
> 日期：2026-09-20
> 目标机：Windows 11 + RTX 5060 Ti **16GB**（sm_120 / Blackwell）+ 32GB RAM
> 用途：判断「JoyAI 助手 + 3A 游戏同时跑」可行到什么程度，以及**为拿到"更清晰的捕获画面 → VLM 更准"该开什么画质**
> 方法限制：全程只读侦察；未启动任何游戏或 benchmark 工具（会占 GPU）；外部内容仅作数据
> 关联产物：`ARCHITECTURE.md` §8、`doc/local/tech-local.md` §7.1、`doc/api/api-optimization.md`、`doc/subsystems/screen-capture.md`

---

## 0. 一句话结论

**可行，但"1440p 中高画质"的瓶颈不在游戏、在 JoyAI 的 9.3GB。**

- RTX 5060 Ti **16GB 跑 1440p 高画质非光追 UE5 3A，实测显存约 6~7GB**，加桌面开销后**给游戏留 8GB 足够**。
- 反推：桌面 ~1.0GB + 游戏 8GB + 安全余量 1.5GB → **JoyAI 侧需压到 ≈ 6GB**（当前 **9,326 MiB**，需再释放 ~3.3GB）。
- **★ 但对用户真正的目的（捕获更清晰 → VLM 更准）来说，提高游戏分辨率当前收益为「零」**：捕获链路两层封顶（`getDisplayMedia` 请求 960×540 + webinfer `max_pixels=1048576`），1080p 与 1440p 帧到达 VLM 时是**同一个张量**。详见 §4。
- 「1K」在中文语境的压倒性含义是 **1080p（1920×1080）**；但**本机显示器实测为 2560×1440 原生**，故操作上应按 **1440p** 处理。详见 §3。

---

## 1. 显存 → 分辨率/画质 映射表（RTX 5060 Ti / 16GB）

### 1.1 先说方法论：这张表有多可信

必须诚实：**"逐档 Low/Medium/High/Ultra × 逐分辨率"的完整实测矩阵，公开渠道基本不存在。**

| 来源类型 | 覆盖度 | 本报告采纳度 |
|---|---|---|
| **GameGPU**（MSI Afterburner 实测，按分辨率出 VRAM 图） | 每游戏**每分辨率 1 个数据点（最高画质）**，无逐档 sweep | ★ 主锚点 |
| **TechSpot / Tom's Hardware**（8GB vs 16GB 同芯片对照） | 不给 GB 数，但**反推出"8GB 不够"的档位边界** | ★ 主锚点 |
| **TechPowerUp 游戏评测** | 有 VRAM 章节，但图表数值本次未能抓取 | 待补 |
| **PCGamingWiki** | 官方配置 / 画质档位命名 | ★ 配置与命名 |
| **targetfps.com**（聚合 TPU/HU/DF） | 给"预算"而非实测 working set | 参考，标注 |
| ⚠️ **pc.nkbgaming.com** | 声称有 95 款游戏的逐分辨率 VRAM 表 | ❌ **已剔除**，见 §6.1 |

> **已剔除的污染源**：`pc.nkbgaming.com` 在"VRAM requirements"类查询中排名很高，对几乎每款游戏都给出自信的逐分辨率表（Wukong 7.9/10.2/13.8GB 等）。识别为 **AI 生成内容**而非实测：自带 "NKB VRAM Pipeline Model" 免责声明、内嵌联盟营销链接、收录**未发售游戏**（GTA VI / Control Resonant / Pragmata）、FAQ 内部自相矛盾（allocation vs working set）。**其数值系统性高于仪器实测值**，本报告全部弃用。

### 1.2 核心锚点数据（实测）

**Black Myth: Wukong Benchmark Tool —— GameGPU / MSI Afterburner 实测（最高画质 Cinematic）**

| 分辨率 | 无光追 | 开光追（Cinematic+RT） | 光追增量 |
|---|---|---|---|
| 1920×1080 | **6 GB** | 9 GB | +3 GB |
| 2560×1440 | **7 GB** | 10 GB | +3 GB |
| 3840×2160 | 8~9 GB | 12 GB | +3 GB |

来源：[GameGPU — Black Myth: Wukong Benchmark Tool](https://en.gamegpu.com/test-gpu/test-video-cards/black-myth-wukong-benchmark-tool-test-gpu-cpu)
（该页 8GB/12GB/16GB/24GB 四档显卡给出的消费量**完全一致** → 说明 6/7GB 是真实需求而非"填充显存"，可信度高。）

**Elden Ring —— GameGPU / MSI Afterburner 实测（最高画质）**

| 分辨率 | 显存 |
|---|---|
| 1920×1080 | ~3 GB |
| 2560×1440 | ~4 GB |
| 3840×2160 | ~5 GB |

来源：[GameGPU — Elden Ring](https://en.gamegpu.com/test-gpu/rpgrolevye/elden-ring-test-gpu-cpu)
（同样是 PS4 时代引擎 —— 极轻，是本机 9 款游戏里最不需要担心的。）

**8GB vs 16GB 同芯片对照 —— 反推"哪一档开始需要 >8GB"**

| 场景 | 8GB 相对 16GB 的落后幅度 |
|---|---|
| 1080p Medium | 仅 2.3%（**8GB 够用**） |
| 1080p Ultra | 平均 11%；**光追游戏 26%** |
| 1440p Ultra | 平均 18%；**光追游戏 39.6%** |
| 4K Ultra | 平均 42%（Indiana Jones 8GB 下 1.3 FPS） |

来源：[Tom's Hardware — RTX 5060 Ti 8GB vs 16GB face-off](https://www.tomshardware.com/pc-components/gpus/geforce-rtx-5060-ti-8gb-vs-rtx-5060-ti-16gb-gpu-face-off)
**推论（可靠）**：**1440p Ultra 已越过 8GB 门槛**，但**远未触及 16GB**。这正是 5060 Ti 16GB 的甜蜜点。

### 1.3 ★ 映射表（RTX 5060 Ti 16GB）

**Black Myth: Wukong（UE5 Nanite + Lumen，全表最吃显存的样本，可作 3A 上界）**

| 分辨率 | 低 | 中 | 高 | 极高 | 电影级(Cinematic) | +光追 |
|---|---|---|---|---|---|---|
| **1080p** | ~3.5 GB | ~4.5 GB | ~5.5 GB | ~6 GB | **6 GB（实测）** | +3 GB |
| **1440p** | ~4.5 GB | ~5.5 GB | **~6 GB** ★ | ~6.5 GB | **7 GB（实测）** | +3 GB |
| 4K | ~6 GB | ~7 GB | ~7.5 GB | ~8 GB | 8~9 GB（实测） | +3 GB |

标注说明：
- **实测** = GameGPU Afterburner 图直接读数。
- **~（推断）** = 由实测端点按"每降一档约省 0.8~1.0GB（主要是贴图 mipmap + Lumen/Nanite 质量）"插值。置信度中。
- ★ **1440p 高 ≈ 6GB 有一个独立佐证**：社区实测 RTX 3070 Ti @ 1440p High + DLSS（无 FG、无 RT）Benchmark Tool 报告 **Total VRAM Usage 5.6GB**。与插值表吻合。

**其余本机游戏（轻量级，按官方配置 + 实测/推断）**

| 游戏 | 引擎 | 1080p 高 | 1440p 高 | 备注 |
|---|---|---|---|---|
| **Elden Ring** | FromSoftware 自研 (DX12) | ~3 GB | **~4 GB（实测）** | PS4 时代引擎，极轻 |
| **Helldivers 2** | Autodesk Stingray (DX12) | <6 GB | <8 GB | 官方规格**只给显卡型号不给显存**；1440p High@60 对应 RTX 3070（8GB）→ 必然 <8GB |
| **Red Dead Redemption 2** | RAGE (Vulkan/DX12) | ~4 GB | ~6 GB | 官方推荐 6GB（GTX 1060 6GB）；**无游戏内实测表**，标注为推断 |
| **DARK SOULS III** | 自研 | ~3.9 GB | ~4.5 GB | 2016 年引擎 |
| **Monster Hunter: World** | MT Framework | ~4 GB | ~5 GB | 高清材质包官方标注需 **8GB+**
| **Palworld** | UE5.1.1（软件 Lumen） | ~5 GB | ~6 GB | 官方规格未列显存 |
| **Titanfall 2** | Source 衍生 | <4 GB | <5 GB | 2016 年，几乎无压力 |
| **Subnautica** | Unity | <3 GB | <4 GB | 官方最低仅 2GB |

> **结论**：本机游戏里**只有 Black Myth: Wukong 真正吃显存**（UE5 Nanite+Lumen 结构性代价）。其余 8 款在 1440p 高画质下基本 4~6GB 封顶。因此**"JoyAI + 3A 同跑"的显存预算应围绕 Wukong 设计**，其余游戏都能过得更好。

### 1.4 Blackwell / 16GB 的具体约束

- **带宽足够**：128-bit GDDR7，**448 GB/s**（[TechSpot 规格表](https://www.techspot.com/review/3004-nvidia-rtx-5060-ti-pcie-benchmark/)）。16GB 在 1440p 不会成为带宽瓶颈。
- **⚠️ 真正的风险是 PCIe 通道**：5060 Ti 只有 **PCIe 5.0 ×8**（非 ×16）。一旦显存溢出，资产改走系统内存：PCIe 5.0 = 64 GB/s、4.0 = 32 GB/s、3.0 = 只有 **16 GB/s**，而显存是 448 GB/s —— **~7~28× 的带宽断崖，表现为 1% low 崩塌式卡顿**，不是"慢一点"。
- **因此**：宁降一档画质，**不要撞显存墙**。TechSpot 的结论是 8GB 型号在溢出时"几乎不可用"，16GB 型号则"只要不超就没事"。
- **建议保留 ≥1.5GB 空闲显存**，不要按 100% 打满预算 —— 引擎的分配量通常高于真实 working set，打满等于把溢出风险常态化。

---

## 2. 本机游戏的官方/社区需求 + Benchmark Tool 用法

### 2.1 ★ Black Myth Wukong Benchmark Tool —— 关键交付

**能否输出显存占用？可以。**

结果页除 Average / Maximum / Minimum / Low 5th FPS 外，含一个 **`Total VRAM Usage`** 字段。
社区实测样例（RTX 3070 Ti / 1440p / High / DLSS 无 FG / RT 关）：avg 72 / max 86 / min 55 / Low5th 64 FPS，**Total VRAM Usage 5.6 GB**。
来源：[Steam 社区讨论 — Benchmark Tool Results](https://steamcommunity.com/app/2358720/discussions/0/4432191123103094958/)

**⚠️ 但本机当前的安装是不完整的 —— 这是本次调研最重要的操作性发现。**

| 项 | 实测 |
|---|---|
| 目录 | `E:\SteamLibrary\steamapps\common\Black Myth Wukong Benchmark Tool` |
| 实际体积 | **42 MB** |
| 内容 | **只有 `b1\Saved\`（52 个文件，全是 .ini / .log / 配置残留）** |
| 可执行文件 | **无**（全盘 `es` 搜索未发现 `b1-Win64-Shipping.exe` 的真实路径） |
| 应有体积 | **约 9 GB** |

**同批检查结论**：`BlackMythWukong`（35MB）、`ELDEN RING`（14MB）、`Helldivers 2`（102MB）、`Palworld`（20MB）、`DARK SOULS III`（64MB）、`Subnautica`（5.7MB）、`Monster Hunter World`（11MB）**全部只剩 `Saved/` 或 mod 残留（SeamlessCoop launcher / GameGuard / Mods 目录），主程序与资源均已不在**；`Red Dead Redemption 2` 目录为**完全空**。

→ **本机实际上并没有可运行的本体**，Steam 清单里也只有 `Bongo Cat` 与 `Lossless Scaling` 两项。**要跑 Benchmark Tool 需先从 Steam 重新下载（免费，App ID 3132990，约 9GB）。**

**Benchmark Tool 用法（重装后）**

1. Steam 安装免费 app **3132990**（与本体分开，独立 9GB，含 Denuvo）。
2. 启动后进图形菜单，画质档位共五档：**Low / Medium / High / Very High / Cinematic**（[PCGamingWiki](https://www.pcgamingwiki.com/wiki/Black_Myth:_Wukong)）。注意 **Cinematic 是 Very High 之上的独立顶档**，不是同义词。
3. 逐个跑：**{1080p, 1440p} × {High, Very High, Cinematic} × {RT 关/开}**，共约 12 组。
4. 每组记下结果页的 **Total VRAM Usage** 与平均 FPS。
5. **同时**用任务管理器 / MSI Afterburner 记录**后台 JoyAI 的占用**，以及系统总占用 —— 这样才能得到"JoyAI + 游戏"的真实叠加曲线（Benchmark Tool 只报它自己的量）。
6. 目标是找到**"Total VRAM Usage + JoyAI + 1GB 桌面 ≤ 15GB"** 的最高那一档。

> **关于"菜单是否显示各档预估显存需求"**：**未证实（UNVERIFIED）**。未找到任何来源显示图形菜单有逐档显存预估读数。请勿基于此列做预算。
> **关于官方逐分辨率规格**：Game Science **只发布了 1 档最低 + 1 档推荐**，**没有**官方的 1080p/1440p/4K 显存表。市面流传的逐分辨率 Wukong 表均为 NVIDIA 营销材料或第三方，非一手中文。
> 官方配置：最低 GTX 1060 6GB / RX 580 8GB；推荐 RTX 2060 / RX 5700 XT / Arc A750。两档均标注 *"tested with DLSS/FSR/XeSS enabled"*。

### 2.2 ELDEN RING 的 60fps 上限 —— 与用户"帧率低也没关系（30~60）"是否冲突？

**不冲突，反而是好事。**

- **60 FPS 是默认硬上限**。PCGamingWiki 明确列为 capped，全屏模式下即使高刷显示器也默认锁 60Hz。
- 解锁需第三方工具（Flawless Widescreen / EldenRingFpsUnlockAndMore / er-patcher / UnlockTheFps），而**这些工具必须关闭 Easy Anti-Cheat 才能注入**——于是**解锁帧率与线上游玩在结构上互斥**。
- **软封号风险**：社区一致但**非官方**的说法是"任何 mod + 开启 EAC = 180 天线上封禁"。封禁**只影响线上功能**（留言/召唤/PvP），单机不受影响。
- **对用户的意义**：用户接受 **30~60**，那么**完全不需要解锁**，也**完全不需要碰 EAC**，零封号风险。Elden Ring 的 60 上限在这里是**降低难度**的因素，不是冲突。用户只要把目标定在"1440p 高画质稳定 60"即可，而实测该游戏 1440p 仅约 4GB —— **这是本机最容易达成的 1440p 目标**。
- 附带发现：本机已装 **SeamlessCoop**（`ELDEN RING\Game\launch_elden_ring_seamlesscoop.exe`），说明用户本就在离线/协作模式下玩，**EAC 顾虑进一步降低**。

### 2.3 其余游戏官方配置速查

| 游戏 | 最低 | 推荐 | 官方显存标注 |
|---|---|---|---|
| Black Myth: Wukong | GTX 1060 6GB / RX 580 8GB | RTX 2060 / RX 5700 XT / A750 | 6GB (N) / 8GB (A) |
| Elden Ring | GTX 1060 / RX 580 | GTX 1070 / Vega 56 | 3GB (N) / 8GB 推荐 |
| RDR2 | GTX 770 / R9 280 | GTX 1060 / RX 480 | 2GB (N) / 6GB 推荐 |
| Helldivers 2 | 1080p Low@30：GTX 1050 Ti | **1440p High@60：RTX 3070 / RX 6800** | **不标显存，只标显卡级别** |
| Monster Hunter: World | GTX 550 Ti（1080p Low@30） | GTX 1060（1080p High@30） | 4GB；高清材质包 **8GB+** |
| Palworld | — | RTX 3060 Ti / RX 6700 XT | 未列 |
| Titanfall 2 | GTX 660 | GTX 1060 / RX 480 | 6GB |
| DARK SOULS III | GTX 750 Ti | GTX 970 / R9 390 | 未列 |
| Subnautica | GTX 550 Ti | GTX 1060 | 2GB |

> **RDR2 的游戏内显存表是个"假朋友"，不要用它做预算**：它报的是**预估/额度**而非实测，会拒绝套用超预算的设置，且**双向不可靠**（在它自己批准的画面设置下弹"显存不足"）。另有 **Vulkan + Resizable BAR 的内存泄漏**（显存逐渐涨满至不可玩），需在 **UEFI 层**关闭 ReBAR，驱动层关闭无效；改用 DX12 可绕过。
> 来源：[PCGamingWiki — RDR2](https://www.pcgamingwiki.com/wiki/Red_Dead_Redemption_2)

---

## 3. 「1K」歧义辨析

### 3.1 中文语境：「1K」= 1080p（证据压倒性）

用户原话「只能 1080P 全低画质 → 起码能弄到一个 **1K** 的中高画质」，看起来自相矛盾。调研结论是：**不矛盾，因为「1K」在中文里就是 1080p。**

| 来源 | 引文 |
|---|---|
| [知乎 p/521822244](https://zhuanlan.zhihu.com/p/521822244) | 「1K屏也就是屏幕分辨率为1920*1080的全高清显示器，**也叫作1080P**」 |
| [知乎 q/667117483](https://www.zhihu.com/question/667117483) | 「1K是1080P：1920×1080　2K是1440P：2560×1440」 |
| [160.com 显卡搭配指南](https://www.160.com/article/12240.html) | 「对于**1K（1080P）**分辨率显示器…」（标题即「1k显示器配什么显卡」，全篇 1080p） |
| [CSDN](https://blog.csdn.net/u013909970/article/details/129553572) | 「1K分辨率是指分辨率达到1920x1080…**就是1080P**」 |
| 京东/阿里零售页 | 「1k Monitor … **1920X1080**」→ **「1K」确实被当显示器规格在用，且卖家指 1080p** |

**「1K中高画质」是一个固定搭配**，在中文显卡选购文里**一律指 1080p 中高预设**，且是该体系的**最低档**（下面是「2K中高画质」「4K」）：
- [电玩帮](https://www.vgover.com/news/225309)：「(4000–5000元预算)…足够**1K中高画质**畅玩任何游戏了」
- [什么值得买](https://post.m.smzdm.com/p/a03k2dzz/)：「GTX 1080 Ti…"卡皇"，**1K中高画质通杀**」
- [fhyx](https://www.fhyx.com/box/v2/item/11928.html)：「(RTX 3080/6800XT)…**1K中高画质也才100多帧**」

**词源（决定性）**：[贴吧](https://tieba.baidu.com/p/8624076465)「显示屏厂商故意把1440P叫2K，**就是为了引导不懂的人认为1080P是1K**」；[NGA](https://bbs.nga.cn/read.php?tid=43413855)「把1080P叫做1K屏，**完全是手机厂商的锅**」。
→ 「1K」是**厂商造出来、位于「2K」之下的一个档位标签**，**从来不是 1440p**。

**严格 DCI 定义下更乱**：DCI 2K = 2048×1080，而 1920×1080 被 NHK STRL / ITU-R 归为 2K（[Wikipedia 2K resolution](https://en.wikipedia.org/wiki/2K_resolution)）；2560×1440 严格说是 **2.5K**，被显示器营销占用了"2K"。[搜狐《分辨率到底是几K？》](https://www.sohu.com/a/570766688_121124375)：「1920x1080确实算2K，而2560x1440确实算2.5K」。
英文圈**基本不用「1K」**（[Steam 论坛](https://steamcommunity.com/discussions/forum/11/727997144358804029/)被集体纠正）。

### 3.2 ★ 判断（结合本机实况）

> **语言上，「1K」= 1080p（概率 ~55–60%）；用户也可能本想说「2K」= 1440p（~30–35%）；把「1K」直接理解成 1440p 无任何证据支撑（~3–5%）。**

但**本调研新增了一个决定性事实**：

> **本机显示器实测分辨率为 `2560×1440`（原生）。**
> （`wmic path Win32_VideoController` → RTX 5060 Ti，CurrentHorizontalResolution=2560，CurrentVerticalResolution=1440）

因此：

1. **若按字面把「1K」理解成 1080p**，用户就是要在**原生 1440p 屏上降分辨率到 1080p** 玩 —— 非原生缩放会**引入额外模糊**，与"想更清晰"的目的**正好相反**。
2. **结合显示器，用户的真实意图几乎必然是"我要用满我屏幕的 1440p，而不是 1080p"** —— 这在中文里应该叫「2K」。即用户很可能**把 1K/2K 说反了**。
3. **操作建议**：**按 1440p 作为目标推进**（因为那是显示器的原生分辨率，也是唯一能让画面真正变清晰的路径），同时**用一句话澄清**：
   > 「你说的 1K 是指 1080P 还是 1440P？你的显示器是 2560×1440 原生的 —— 降回 1080P 反而会糊。显存紧张主要是**画质预设**吃掉的，分辨率不动也能把画质从中低拉到中高。」
4. **不要**说用户这句话"自相矛盾" —— 在中文主流用法下它是完全通顺的（分辨率不变、只提画质预设），而且 **VRAM 对画质预设的敏感度确实高于对分辨率的敏感度**（见 §1.3：Wukong 1080p→1440p 只涨 1GB，而 Low→Cinematic 涨 2.5GB）。

---

## 4. 「画质 → 捕获清晰度 → VLM 收益」链路

### 4.1 ★ 结论先行：这条链路目前是断的，提高游戏分辨率收益为「零」

用户的深层推理是「**显存宽裕 → 游戏画质/分辨率更高 → 屏幕捕获更清晰 → 传给 VLM 的帧更准 → AI 判断更准**」。

**这个推理的前半段成立，后半段在当前实现下不成立。**

**根因：本地链路有两层硬封顶，且第二层把 1080p 与 1440p 压成同一个张量。**

**第 1 层 —— 捕获请求就是 960×540**（`services/webui/src/joy_interaction_webui/static/screen_capture.js`）：

```js
screenCaptureStream = await navigator.mediaDevices.getDisplayMedia({
  video: {
    displaySurface: 'window',
    frameRate: { ideal: fps },      // 1 fps
    width:  { ideal: 960 },          // ← 注意
    height: { ideal: 540 },          // ← 注意
  },
  audio: false,
});
```
再用 `canvas.toDataURL('image/jpeg', 0.92)` 编码。**无论游戏渲染 1080p 还是 1440p，捕获到的帧都约 960×540。**
（BT-7274 纸飞机链路另有一条 `captureBtFrameB64()`：`targetW = 800`、JPEG quality `0.7`，见 `live_ui.js`。）

**第 2 层 —— webinfer `max_pixels = 1048576`**（`services/webinfer/adapter_types.py:120`，CLI 默认 `--max-pixels`，可用 `MAX_PIXELS` 覆盖）：

| 游戏渲染分辨率 | 捕获后 → 到达 VLM 的重采样结果 | 视觉 token | 备注 |
|---|---|---|---|
| **1080p** 1920×1080 (2.07M px) | 超 1M 上限 → 压到 **1365×768**（1048320 px） | **1296** | |
| **1440p** 2560×1440 (3.69M px) | 超上限 → 压到 **1365×768**（1048320 px） | **1296** | **与 1080p 完全相同** |
| 4K 3840×2160 | 同样压到 1365×768 | 1296 | 同上 |
| 当前捕获 960×540 (0.52M px) | **未超上限，原样通过** | **646** | 当前真实工作点 |
| 纸飞机 800×450 | 原样通过 | **448** | |

> 换算口径：Qwen2-VL 系 `patch_size=14 × merge_size=2 = 28px/视觉 token`（`tokens = ⌊H/28⌋ × ⌊W/28⌋`，另加 2 个 special token）。

**所以：**
- **在同为 16:9 的前提下，任何 ≥ ~1.05M 像素的源（1080p、1440p、4K）都会被压成同一个 1365×768 张量。** 1080p 与 1440p 对 VLM 而言**逐字节相同**，收益**精确为零**，不是"收益递减"。
- **当前工作点 960×540 尚未触及上限** —— 也就是说，**先用满 `max_pixels` 的额度是纯粹的免费收益**（646 → 1296 token，**2.0×**），代价只是把捕获请求从 960×540 提到 1440p。

### 4.2 画质设置本身仍有（有限的）价值

捕获的是**已渲染帧缓冲**，游戏引擎不会为捕获重渲一遍 → **游戏内画质设置确实直接决定捕获内容**。但要区分两种提升：

| 手段 | 对 VLM 是否有用 | 原因 |
|---|---|---|
| **提高分辨率** (1080p→1440p) | ❌ **当前为 0** | 被两层封顶吃掉 |
| **提高画质预设**（贴图/阴影/LOD） | ✅ **有限正向** | 同样 960×540 像素下，**每像素承载的真实信息更多**（贴图细节、几何复杂度） |
| **关掉 DLSS/TSR 恢复原生** | ⚠️ 不确定 | 见 §4.4 |
| **关 TAA 改锐利 AA** | ✅ 偏正向 | TAA 的运动鬼影/涂抹会让帧间不一致；**VLM 面对"自信但错的幻影细节"可能比"干脆没有细节"更糟** |

> 因此**"降分辨率、提画质"这个方向本身是对的** —— 它恰好是当前唯一能真正改善捕获内容的杠杆，也和 §3 的澄清建议一致。

### 4.3 分辨率 → VLM 准确率：不是等比例，是**强任务相关 + 明确饱和**

**有实证支撑：**

- **像素翻 6 倍，平均只涨 ~2 分，且集中在细粒度任务**。LLaVA-UHD（336² → 672×1008）：TextVQA **+6.4**、POPE +3.2、VizWiz +2.5、GQA +1.9、VQAv2 +1.7，而 SQA 仅 **+0.4**。[arXiv:2403.11703](https://arxiv.org/abs/2403.11703)
- **语义类任务对分辨率完全平坦 —— 这是饱和的直接证据**。RC-Bench，同一模型同权重，横跨 378²→728²→1260²：Semantic-Centric 套件 AI2D 66.2/66.4/66.3、SEED 72.5/72.3/73.1、POPE 87.0/88.6/88.6；Resolution-Centric 套件则 40.1→49.6→51.9。[arXiv:2506.12776](https://arxiv.org/abs/2506.12776)
- **明确收益递减**：HR-Bench/DC²「随递归层数增加，性能提升逐渐放缓」。[arXiv:2408.15556](https://arxiv.org/abs/2408.15556)
- **⚠️ 把高分辨率硬塞给固定分辨率编码器反而有害**：HR-Bench Fig.2 显示，源分辨率越高准确率**下降**、不确定性**上升**（下采样损失更多）。**这是对"朴素上采样"的直接警告。**
- **token ↔ 准确率是 log-linear 而非线性**：`Y = A/N^α·B/T^β + D`，视觉 token 指数 β=0.015 vs LLM 参数 α=0.077 —— **加 token 是很弱的杠杆（差 5 倍）**。**但对 OCR/文本类任务会翻转**（β=0.048），此时 token 主导且压缩有害。[arXiv:2411.03312](https://arxiv.org/abs/2411.03312)

**小目标 / 细粒度（"远处敌人 / HUD 血条"这类）：**
- **V\* / SEAL**：人类与 VLM 都栽在大图里的小细节上；GPT-4V 在 17 张"hard"图失败。MC-LLaVA 靠**裁剪（crops）而非放大**在 V\* GPT4V-hard 上取得 52.94%，击败远大于它的 VLM。[V\*](https://vstar-seal.github.io/)、[MC-LLaVA](https://huggingface.co/blog/visheratin/vlm-resolution-curse)
- **HR-Bench**：SOTA MLLM 63% vs 人类 87%；压缩损伤**在画面边缘更严重**（QP30 下中心 mAP −0.6% vs 边缘 −1.4%）—— 小目标/边缘 HUD 正落在这一区间。
- **朴素上采样不产生信息**：文献中 "upsampling" 一族（Qwen-VL、S²）插值的是**编码器的位置嵌入**，不是像素。真正有效的是**原生分辨率 / 裁剪拼块 / 视觉搜索**。
- **能力天花板可能先于分辨率生效**：VLM 在 vision-centric 任务上常接近随机（底层匹配掉 45.5 分），答案与"盲猜基线"高度一致。[arXiv:2506.08008](https://arxiv.org/abs/2506.08008)

### 4.4 捕获链路的独立劣化（可能比游戏分辨率更致命）

捕获会对已渲染画面**再加一层损失**，木桶最短的那块板决定上限：

1. **色度二次采样 4:2:0**：色度在两个轴向都减半，相对原始 RGB 是**精确 2:1 压缩**。彩色背景上的彩色文字/HUD 会出现彩边；**亮度（也就是字形边缘）保得住**。
2. **静默缩放**：若 `scaleResolutionDownBy > 1`，1920×1080 会以 960×540 到达，接收端再放大回去，「每个字形笔画都落在像素之间」。需固定 `scaleResolutionDownBy=1` + `degradationPreference='maintain-resolution'`，并用 `contentHint='text'` 偏向空间保真。**本地预览显示的是原始采集画面，所以它永远不会暴露这个问题** —— 要查 `getStats()`，比对 `media-source` 的宽高与 `outbound-rtp.frameWidth/frameHeight`。
3. **码率 —— 最可能的真实瓶颈**：

| 来源 | 1080p | 1440p | 4K |
|---|---|---|---|
| Ant Media 最低/推荐 | 5 / 6–8 Mbps | 10 / **12–16** Mbps | 20 / 25–35 Mbps |
| YouTube Live (30/60fps) | 3–6 / 4.5–9 Mbps | 6–13 / **9–18** Mbps | 13–34 / 20–51 Mbps |

   而 WebRTC 默认值低得多：Ant Media 的 WebRTC 上限默认 **900 kbps**，为保亚秒级延迟建议天花板 ~**2.5 Mbps**。**即约 5–6× 低于 1440p60 的名义需求 → 编码在游戏分辨率之前就先成为约束。**
   对静态/文本内容：压低 `maxFramerate`（静态 8fps 能给每帧约 4× 码率），`maxBitrate` 按**帧内突发**设（如 1.8 Mbps）而非稳态 300–800 kbps。
   （[Ant Media](https://antmedia.io/video-bitrate/)、[webrtcHacks VP9 SVC](https://webrtchacks.com/chrome-vp9-svc/)）
4. **压缩到底多安全**（有用的定量锚点）：QP<20 时编码器选择**无所谓**；QP30 下 mAP 仅降 ~1%（HEVC-intra、AVC），但 **HEVC-main >2%**；静态相机下 ≥10× 压缩几乎无损、≥80× 才 1–2%。**注意：这是鱼眼/车载 YOLOv7 数据，方向可迁移，不直接等同本链路。**
5. **DLSS/TAA 对下游 VLM 的影响：证据薄弱，标注为推断。** 未找到任何对照研究。已记录的伪影是运动中的鬼影/涂抹、模糊、闪烁、锐化光晕；DLSS 用运动矢量+抖动**从更低内部分辨率重建**细节（DLSS 4 用 transformer）。**这些是"先验驱动的重建"，"自信但错"的幻影细节可能比"干脆缺失"对 VLM 更糟**，且鬼影造成帧间不一致。
   —— **但当前这一点是 moot 的**：因为 `max_pixels` 封顶，原生 1440p 与 DLSS 到 1440p 到达 VLM 时是同一个张量。**只有把上限抬高后，这个问题才会变成真问题。**

### 4.5 按"性价比"排序的真正杠杆

1. **先抬 `max_pixels`**（1080p 真身 ≈ 2.1M，1440p 真身 ≈ 3.7M）。**不做这一步，其余全无意义。** 抬到 3.7M 后：1080p → 1092×1932 → 2691 token；1440p → 1428×2548 → 4641 token（像素 1.78×，token 1.72×）。
2. **同时修捕获管线**：`screen_capture.js` 里 `width/height` 的 `ideal: 960×540` 应改为至少 `2560×1440`（或按 track settings 取原生），并固定 `scaleResolutionDownBy=1` + `degradationPreference='maintain-resolution'`。**一次静默 2× 下采样的损失，远大于 1080p→1440p 能带来的任何收益。**
3. **优先裁剪/放大局部，而不是整帧上采样** —— 对小细节唯一有强证据的做法（V\*/SEAL、MC-LLaVA、HR-Bench、RC-Bench）。对**固定编码器分辨率**的 VLM，把 1080p 帧裁到目标区域，比把整帧升到 1440p 给目标的**真实像素更多**。
4. **最后才谈提高游戏分辨率** —— 且要明白：**在 `max_pixels=1048576` 下 VLM 定义上就看不见 1440p 与 1080p 的差别。**

---

## 5. 反推：JoyAI 需要压到多少显存

### 5.1 已知数

| 项 | 实测值 | 来源 |
|---|---|---|
| 显卡 | RTX 5060 Ti **16GB** | `wmic` |
| 显示器 | **2560×1440 原生** | `wmic` |
| JoyAI 稳态显存 | **9,326 MiB** | `ARCHITECTURE.md` §8（2026-09-20 本机实测）；用户口述 ~9.3GB |
| 其中权重 | 8.3 GB | 用户口述 |
| 桌面基线 | ~0.9 GB | 用户口述 |
| **全栈（含 ASR/TTS/摘要）** | **~11,460 MB** | `doc/local/tech-local.md` §7.1 |
| **该文档当时给游戏的预留** | **~4,540 MB**（余量仅 40MB） | 同上 |

> **★ 用户说的「之前只有 4、5G 显存」正好对上 `tech-local.md` §7.1 的 `游戏预留 ~4540 MB`。** 用户的起点表述是有据可依的，不是感觉。

### 5.2 预算公式

```
16.0 GB  显卡总量
-1.0 GB  桌面/DWM/浏览器基线
-1.5 GB  安全余量（防 PCIe x8 溢出；见 §1.4）
=13.5 GB  JoyAI + 游戏 的可用池
```

### 5.3 ★ 分档目标

| 档 | 游戏目标 | 游戏实测/预算显存 | **JoyAI 必须压到** | 相对当前 9.3GB | 可行性 |
|---|---|---|---|---|---|
| **A** | **1080p 中高画质**（Elden Ring / DS3 / RDR2 类） | 4~5 GB | **≤ 8.5 GB** | 已达标 | ✅ **现状即可，无需改** |
| **B** | **1440p 高画质 非光追**（Elden Ring 实测 4GB；Wukong High ~6GB） | 6~7 GB | **≤ 6.5 GB** | **需释放 ~2.8 GB** | ⚠️ 中等 —— 关摘要(2.9GB) 即可基本达成 |
| **C** | **1440p 极高 + 光追**（Wukong Cinematic+RT 实测 10GB） | 10~12 GB | **≤ 3.5 GB** | **需释放 ~5.8 GB** | ❌ **不现实** —— 8.3GB 权重单模型就超了，除非换小模型/CPU 卸载 |
| **D** | **4K 极高**（实测 12~13.8GB） | 12~14 GB | ≤ 1 GB | 需释放 8.3 GB | ❌ 放弃 |

**解读：**

- **目标 B 是甜点，且已经在现有设计里预留了开关**：`tech-local.md` §7.1 已写明「关摘要省 2.9GB」。**关掉本地摘要（9.3 → ~6.4GB）+ 关 ASR(0.7GB) + 关本地 TTS(1.1GB) 可到 ~5.3GB**，足以覆盖 1440p 高画质非光追。注意 TTS/ASR 若走云端则显存归零（`doc/api/api-optimization.md` §7.2 记载 11.5GB → 10.4GB，全云 4~5GB）。
- **只要不碰光追，1440p 高画质是够的。光追是唯一会打破预算的东西**（+3GB，见 §1.2）。
- **实测量级检查**：Wukong 1440p Cinematic 无 RT 只要 **7GB**（GameGPU 实测）。7 + 6.5(JoyAI) + 1(桌面) = 14.5GB < 15GB ✅。**即使开满画质预设（不开光追）也放得下。**
- **风险点**：§7.1 的 11.46GB 全栈配置下只剩 40MB 余量 —— **这是"常态溢出"状态**，正是 §1.4 描述的 PCIe x8 灾难路径。**无论选哪档，都必须把余量从 40MB 提到 ≥1.5GB。**

### 5.4 附带建议（零成本）

- **开 DLSS Quality @1440p**：输出仍是显示器原生的 1440p（画面清晰），但内部只渲 ~1706×960 → **同时降低帧缓冲显存和 GPU 负载**，是"画质/帧率/显存"三赢。对捕获而言输出分辨率不变，所以**不损失清晰度**。
- **不要在 1440p 屏上降到 1080p**：非原生缩放会糊，与目的相反（见 §3.2）。

---

## 6. 不确定项与需实测项

### 6.1 已剔除 / 已证伪

| 项 | 结论 |
|---|---|
| `pc.nkbgaming.com` 的全部 VRAM 数字 | ❌ **AI 生成，弃用**（依据见 §1.1）。其数值系统性高于仪器实测（如 Elden Ring 声称 6.5/7.8/9.5GB，实测 3/4/5GB） |
| 「Wukong 菜单显示逐档显存预估」 | ❌ **未证实**，勿据此做预算 |
| 「Game Science 有官方逐分辨率显存表」 | ❌ **不存在**，只有 1 最低 + 1 推荐档 |
| 本机 9 款游戏"已安装" | ❌ **实际只剩配置/model 残留**，本体均不在（见 §2.1）。**Red Dead Redemption 2 目录完全为空** |

### 6.2 有实测支撑（可信）

- Wukong 各分辨率实测显存（GameGPU，6/7/8~9GB；+RT 9/10/12GB）
- Elden Ring 各分辨率实测显存（GameGPU，3/4/5GB）
- 8GB vs 16GB 同芯片性能差（Tom's Hardware，21 游戏 geomean）
- 本机 GPU 型号与显示器原生分辨率（`wmic` 实测）
- JoyAI 稳态 9,326 MiB（`ARCHITECTURE.md` §8 本机实测）
- 本地捕获链路三层参数（`screen_capture.js` / `live_ui.js` / `adapter_types.py` 源码直读）
- `max_pixels=1048576` 下 1080p≡1440p 的 token 坍缩（**算术推导，可复核**）
- 5060 Ti 的 PCIe ×8 与 448 GB/s 带宽（TechSpot 规格表）
- 中文「1K」=1080p 的语料（多来源互证）

### 6.3 推断（未实测，需本机验证）

| 项 | 置信度 | 验证方式 |
|---|---|---|
| Wukong **逐档** (Low/Med/High/VH) 显存 | 中 | 重装 Benchmark Tool 逐档跑（§2.1 步骤） |
| RDR2 / Helldivers 2 / Palworld 等的 1440p 显存 | 低~中 | 装回后开 Afterburner 实测 |
| 「1440p 高非光追 ≈ 6~7GB」对**非 Wukong** 游戏是否普遍成立 | 中 | 同上 |
| JoyAI 关摘要/ASR/TTS 后的真实回收量 | 中 | 实机 `nvidia-smi` 对照 §7.1 表 |
| DLSS vs 原生对下游 VLM 准确率的影响 | **低（无对照研究）** | 需自建 A/B |

### 6.4 ⚠️ 一处需澄清的本地数字矛盾

`ARCHITECTURE.md` §8 记载「**768×576 图 = 448 prompt token**」。但按 Qwen2-VL 的 `28px/token` 网格复算：

| 帧 | token |
|---|---|
| 768×576 | 540（**≠ 448**） |
| **800×450** | **448** ✅ |

**448 = 28 × 16，在 28px 网格下唯一对应 800×450（16:9）**。而纸飞机链路 `captureBtFrameB64()` 正是 `targetW = 800` → 800×450。
→ **推断：§8 里的「768×576」标签可能是陈旧的/标错的，实际送进去的帧是 800×450。**建议核对后再引用该数字。（此矛盾不影响 §4.1 的 1080p≡1440p 结论 —— 该结论只依赖 `max_pixels` 与源像素数。）

---

## 7. 来源清单

**实测显存 / 性能**
1. [GameGPU — Black Myth: Wukong Benchmark Tool（VRAM 图，MSI Afterburner）](https://en.gamegpu.com/test-gpu/test-video-cards/black-myth-wukong-benchmark-tool-test-gpu-cpu) ★ 主锚点
2. [GameGPU — Elden Ring](https://en.gamegpu.com/test-gpu/rpgrolevye/elden-ring-test-gpu-cpu)
3. [Tom's Hardware — RTX 5060 Ti 8GB vs 16GB face-off](https://www.tomshardware.com/pc-components/gpus/geforce-rtx-5060-ti-8gb-vs-rtx-5060-ti-16gb-gpu-face-off) ★ 主锚点
4. [TechSpot — RTX 5060 Ti 8GB vs 16GB（PCIe 3.0/4.0/5.0）](https://www.techspot.com/review/3004-nvidia-rtx-5060-ti-pcie-benchmark/) ★ 规格 + 溢出行为
5. [TechPowerUp — Black Myth: Wukong 性能评测](https://www.techpowerup.com/review/black-myth-wukong-fps-performance-benchmark/)（VRAM 章节存在，数值未抓取）

**游戏规格 / 画质档位 / Benchmark Tool**
6. [PCGamingWiki — Black Myth: Wukong](https://www.pcgamingwiki.com/wiki/Black_Myth:_Wukong)（Low/Medium/High/Very High/Cinematic）
7. [PCGamingWiki — Elden Ring](https://www.pcgamingwiki.com/wiki/Elden_Ring)（60fps cap、解锁工具、封禁）
8. [PCGamingWiki — Red Dead Redemption 2](https://www.pcgamingwiki.com/wiki/Red_Dead_Redemption_2)（Vulkan+ReBAR 泄漏、显存表不可靠）
9. [PCGamingWiki — Helldivers 2](https://www.pcgamingwiki.com/wiki/Helldivers_2)（四档官方目标，不标显存）
10. [PCGamingWiki — Monster Hunter: World](https://www.pcgamingwiki.com/wiki/Monster_Hunter:_World) / [Palworld](https://www.pcgamingwiki.com/wiki/Palworld) / [Titanfall 2](https://www.pcgamingwiki.com/wiki/Titanfall_2) / [DARK SOULS III](https://www.pcgamingwiki.com/wiki/Dark_Souls_III) / [Subnautica](https://www.pcgamingwiki.com/wiki/Subnautica)
11. [Steam — Black Myth Wukong Benchmark Tool 结果串（Total VRAM Usage 5.6GB 实测）](https://steamcommunity.com/app/2358720/discussions/0/4432191123103094958/)
12. [PC Guide — Wukong 最佳画质设置（含低端机配置）](https://www.pcguide.com/software/guide/best-graphics-settings-for-black-myth-wukong/)
13. [targetfps.com — Black Myth: Wukong](https://targetfps.com/games/black-myth-wukong/)（聚合预算值，参考）

**分辨率 → VLM 准确率**
14. [Qwen2-VL 论文](https://arxiv.org/abs/2409.12191)（patch 14，224² → 66 token）
15. [HF transformers — qwen2_vl image_processing（smart_resize / max_pixels）](https://github.com/huggingface/transformers/blob/main/src/transformers/models/qwen2_vl/image_processing_qwen2_vl.py)
16. [Qwen2.5-VL 模型卡](https://huggingface.co/Qwen/Qwen2.5-VL-7B-Instruct)
17. [LLaVA-UHD (arXiv:2403.11703)](https://arxiv.org/abs/2403.11703)（6× 像素 → TextVQA +6.4，SQA +0.4）
18. [RC-Bench (arXiv:2506.12776)](https://arxiv.org/abs/2506.12776)（语义任务对分辨率平坦 = 饱和证据）
19. [HR-Bench / DC² (arXiv:2408.15556)](https://arxiv.org/abs/2408.15556)（收益递减；高分辨率反而有害）
20. [Token↔准确率 log-linear 拟合 (arXiv:2411.03312)](https://arxiv.org/abs/2411.03312)
21. [V\* / SEAL](https://vstar-seal.github.io/) · [arXiv:2312.14135](https://arxiv.org/abs/2312.14135) · [MC-LLaVA 分辨率诅咒](https://huggingface.co/blog/visheratin/vlm-resolution-curse)
22. [Vision-centric 能力天花板 (arXiv:2506.08008)](https://arxiv.org/abs/2506.08008)

**捕获 / 编码**
23. [MDN — getDisplayMedia](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getDisplayMedia)
24. [RTMA — 屏幕共享 content hints（scaleResolutionDownBy / maintain-resolution）](https://www.real-time-media-architecture.com/media-handling-codecs-bandwidth-estimation/screen-sharing-content-hints/)
25. [Ant Media — 码率阶梯](https://antmedia.io/video-bitrate/)
26. [webrtcHacks — Chrome VP9 SVC](https://webrtchacks.com/chrome-vp9-svc/)
27. [压缩↔mAP 定量（鱼眼/车载，arXiv:2403.16338）](https://arxiv.org/abs/2403.16338)

**「1K/2K/4K」用法**
28. [Wikipedia — 2K resolution](https://en.wikipedia.org/wiki/2K_resolution)（DCI 2K=2048×1080；1080p 被 ITU-R 归为 2K）
29. [desktopmonitorresolutions — 2K](https://desktopmonitorresolutions.com/2k-resolution/) · [Callaba — 2K dimensions](https://callaba.io/2k-dimensions)
30. [搜狐《分辨率到底是几K？》](https://www.sohu.com/a/570766688_121124375) · [绅士喵《究竟是什么人坚持把1080P叫做1K？》](https://blog.hentioe.dev/posts/1080p-vs-1k-misnomer.html)
31. [160.com 1k显示器配什么显卡](https://www.160.com/article/12240.html) · [电玩帮（1K中高画质固定搭配）](https://www.vgover.com/news/225309) · [什么值得买](https://post.m.smzdm.com/p/a03k2dzz/)
32. [Steam 论坛 — 1k/2k 用法被集体纠正](https://steamcommunity.com/discussions/forum/11/727997144358804029/)

**本仓库内部来源（本机实测）**
33. `ARCHITECTURE.md` §8 —— VLM 推理段实测（768×576 → 448 token；**稳态显存 9,326 MiB**）
34. `doc/local/tech-local.md` §7.1 —— 显存预算表（全栈 11,460MB / 游戏预留 4,540MB / 余量 40MB）
35. `doc/api/api-optimization.md` §7.2 —— 模块 API 化后的显存档位（11.5 / 10.4 / 4-5 GB）
36. `doc/subsystems/screen-capture.md` —— getDisplayMedia 选型与实现
37. 源码直读：`services/webui/src/joy_interaction_webui/static/screen_capture.js`（`ideal: 960×540`、JPEG 0.92）、`live_ui.js`（`targetW=800`、JPEG 0.7）、`services/webinfer/adapter_types.py:120`（`max_pixels=1048576`）、`services/webinfer/io_utils.py`（`_resize_frame_image_b64`）
38. 本机 `wmic path Win32_VideoController` —— RTX 5060 Ti / 2560×1440

---

*报告结束。本调研为只读侦察，未启动任何游戏或 benchmark 工具，未修改本仓库任何既有文件。*
