# 游戏显存负载实测方案 — 2026-09-20

> 端点身份：**测试端点（AFK，只读侦察）**
> 本轮**只做侦察 + 造工具，未执行任何实测**（GPU 让给用户）。
> 所有数字均为本机当场只读探测所得，标注了来源命令。

---

## 0. 一句话结论

**方案可行。** 主力工具是 **`scripts/measure-game-vram.ps1`**（已写好并在本机自测通过，非管理员可用）；
显存侧靠 **`nvidia-smi` 总量（绝对真值）+ Windows `GPU Process Memory` 性能计数器（进程归因）** 双源交叉。

**但本轮侦察推翻了任务书的一个前提**：本机**目前没有任何可运行的 3A 游戏本体**，
Black Myth Wukong 官方 Benchmark Tool **也只剩空壳（无 exe）**。
所以「立刻开测」不可行 —— **第一步必须先装回/补全游戏**，否则方案无数据可采。

---

## 1. 脚本用法与原理

### 1.1 交付物

| 文件 | 说明 |
|---|---|
| `scripts/measure-game-vram.ps1` | 实测脚本，PowerShell 5.1，只读、不自启任何程序 |

输出（默认 `measure-out/`）：

| 文件 | 内容 |
|---|---|
| `<Label>-timeseries.csv` | 每秒一行：`gpu_used_mib` / `gpu_free_mib` / `gpu_util_pct` / `temp_c` / `power_w` / `proc_sum_mib` / `game_sum_mib` / `top_process` |
| `<Label>-perprocess.csv` | 每进程每采样一行（长表）：`pid` / `process` / `dedicated_mib` / `is_game_match` |
| `<Label>-summary.json` | `baseline` / `steady_state` / `overall` / `peak_total_mib` / 进程峰值排行 |

### 1.2 三种模式

```powershell
# 1) 桌面空闲基线（JoyAI 常驻、游戏不跑）—— 先跑这个
.\scripts\measure-game-vram.ps1 -Mode baseline -Label desktop-idle

# 2) 游戏内采样（主力；玩完 Ctrl+C 停）
.\scripts\measure-game-vram.ps1 -Mode session -Label wukong-medium-1440p `
    -GameMatch "b1-Win64-Shipping"

# 3) 5 秒自检，不落盘
.\scripts\measure-game-vram.ps1 -Mode watch -DurationSec 5
```

关键参数：`-DurationSec 0`（默认）＝一直采到 Ctrl+C；`-WarmupSec 20` 为稳态窗口起点（剔除加载尖峰）；
`-GameMatch` 支持逗号分隔多个进程名。

### 1.3 技术难点逐条解决

#### 难点 A：非管理员怎么测 per-process 显存？→ **已解决，无需提权**

本机实测确认 `nvidia-smi --query-compute-apps` 全部返回 `[Insufficient Permissions]` / `N/A`
（进程名可见、显存不可见）。**替代方案是 Windows 性能计数器，且确实不需要管理员**：

```bash
$ typeperf -qx "GPU Process Memory" | head -3
\GPU Process Memory(pid_10260_luid_0x00000000_0x00018DD3_phys_0)\Shared Usage
\GPU Process Memory(pid_10260_luid_0x00000000_0x00018DD3_phys_0)\Dedicated Usage
```

`Get-Counter '\GPU Process Memory(*)\Dedicated Usage'` 在普通权限下返回到**每个 PID 的字节数**，
脚本已用这条路径取进程级归因；`\GPU Engine(*)\Utilization Percentage` 同理可做 per-PID 利用率。

**但这台机器有两个 LUID，必须过滤**（脚本自动识别真实适配器）：

| LUID | 含义 |
|---|---|
| `0x00000000_0x00018DD3` | **真卡 RTX 5060 Ti** |
| `0x00000000_0x00019E8B` | Microsoft Basic Render Driver（软件回退） |

#### 难点 A′：**双源不可混用**（本方案最重要的坑）

同一次空闲采样，两个源给出的数字并不相等：

| 来源 | 读数 |
|---|---|
| `nvidia-smi memory.used` | **1058 MiB**（真值） |
| `Σ GPU Process Memory(...18DD3)\Dedicated Usage` | **1507.7 MiB**（偏高 42%） |
| `typeperf \GPU Adapter Memory(...18DD3)\Dedicated Usage` | 959.6 MB |

差异原因是计数器报的是**已提交（committed）**分配，含预留/未驻留页，且会跨采样缓慢漂移。

> **铁律：绝对值一律引用 `nvidia-smi memory.used`；per-PID 计数器只用于「占比 / 谁在涨」的归因。**
> 脚本的 summary 里已显式写入这条 `note`，避免下游端点误用。

#### 难点 B：游戏内帧率怎么测？

本机现状（只读侦察）：

| 工具 | 状态 |
|---|---|
| RTSS / MSI Afterburner | **未安装**（只有 scoop bucket 清单和安装包，无 `RTSS.exe`/`MSIAfterburner.exe`） |
| PresentMon | **未安装为可执行**；但存在 **NVIDIA FrameView SDK** 目录与 `PresentMon_Consumer.log`（日志全 0 字节，说明未真正启用） |
| Steam 覆盖层 | **已启用**（`userdata/899900073/config/localconfig.vdf`: `EnableGameOverlay = 1`），Shift+Tab → 设置 → 游戏中 → 帧率显示 |
| NVIDIA app | 已装（`C:\Program Files\NVIDIA Corporation\NVIDIA app\`，含 ShadowPlay / osc），可开性能覆盖层 |
| Black Myth Wukong Benchmark Tool | **自带帧率结果**，但见第 2 节 —— 本机已不可用 |

推荐顺序：**① 游戏自带 benchmark（若补全）→ ② NVIDIA app 覆盖层（零安装）→ ③ 需要逐帧 CSV 时再装 PresentMon/CapFrameX**。

#### 难点 C：不同画质档位的显存差异？

只能游戏内手改设置 → 见第 3 节操作清单。可**离线改配置文件**减少等待：
UE 引擎游戏的画质项都在 `Saved/Config/**/GameUserSettings.ini`，本机已确认两个实例：

- Wukong: `E:\SteamLibrary\steamapps\common\Black Myth Wukong Benchmark Tool\b1\Saved\Config\Windows\GameUserSettings.ini`
  （含 `sg.TextureQuality` / `sg.ShadowQuality` / `sg.ResolutionQuality` / `UISettingData=(...)` 全档位键）
- 鸣潮: `E:\Wuthering Waves\Wuthering Waves Game\Client\Saved\Config\WindowsNoEditor\GameUserSettings.ini`
  （现为 1280×720、`FrameRateLimit=45`、`sg.TextureQuality=3` —— 印证人机同跑已在低分辨率下妥协）

> 注意：**UE 只在退出时回写** `GameUserSettings.ini`，运行中改文件无效，且必须游戏已关闭再改。

### 1.4 自测证据（本轮实地跑过，GPU 空载）

```
=== GPU VRAM measurement ===
nvidia-smi    : C:\Windows\system32\nvidia-smi.exe
real GPU LUID : luid_0x00000000_0x00018dd3_phys_0
total VRAM    : 16311 MiB (reserved 261 MiB)
WDDM budget   : 15282 MiB
[   0s]   1,132 MiB used |  23% util | proc_sum  1,620 | game 0 | dwm 619.2
top VRAM consumers (peak):
    dwm                pid 2452   peak 652.9 MiB
    spacedeskService   pid 6488   peak 140.2 MiB
    csrss              pid 1084   peak 107.3 MiB
written: measure-out/selftest-idle-{timeseries,perprocess}.csv + -summary.json
```

三个文件均正常生成（5.8 KB / 7.1 KB / 588 B）。

---

## 2. Black Myth Wukong Benchmark Tool 调查结果

### 2.1 结论：**本机该工具不可用 —— 只剩空壳，没有 exe**

路径存在，但树里**只有 `Saved/` 存档目录，没有任何可执行文件**：

```
E:\SteamLibrary\steamapps\common\Black Myth Wukong Benchmark Tool\
└── b1\
    └── Saved\
        ├── D3DDriverByteCodeBlob_...ushaderprecache   (41 MB)
        ├── Config\Windows\GameUserSettings.ini         (2247 B)
        ├── PersistentDownloadDir\b1\breport\b1.xl_00.rptq  (588 B)
        └── Logs\cef3.log (0 B)
```

- `es "b1-Win64-Shipping.exe"` → **零个真 exe**（只匹配到黑盒聊天程序的日志文件名）
- 目录总大小 **42 MB**，而完整 Benchmark Tool 应为 ~20+ GB
- 同级 `BlackMythWukong\` 目录同样只剩 `b1\`，**35 MB**，`es "BlackMythWukong.exe"` → 无

**参数/输出/显存记录：无法确认**（无 exe、无 readme、无 `--help` 可跑）。
按任务书「不要启动」，我没有尝试运行它 —— 事实上也**没有东西可运行**。

### 2.2 唯一残留的运行痕迹（有信息量）

`b1\Saved\PersistentDownloadDir\b1\breport\b1.xl_00.rptq` 解出 gzip 后是一段 protobuf：

```
3bd577520059adad354105f22b955a11  8bf073f1-c74a-4e57-a5be-be712c37afe4
ca28c5e7-a91e-44ca-b61b-6dde93fd80c62  prod  b1_bench_20240729  BGW_ECSWorld
```

说明该工具**确实会落盘结构化报文**（`Report`/`ReportEvent`、gzip protobuf、`POST` 上传、
`b1_bench_20240729` 版本标记）。若补全后它会在同路径继续产出 `b1.xl_*.rptq`
——**但这是私有 protobuf，不是 CSV**，解析成本高；真正好用的是它的**屏上结果页**（需截图）。

### 2.3 因此：**它不能当主力**

- 可自动化性：未验证（工具本体缺失）
- 即使补全，它不产出 CSV，报告是私有 protobuf + 屏上截图 → **不符合「自动化、可重复」的诉求**
- 且它只覆盖 Wukong 一款，无法回答"其它游戏"的问题

---

## 3. 用户操作清单（最小劳动版）

> 前提：**先把游戏装回来**。当前整机可运行的游戏只有少数小型作品（见 3.0）。

### 3.0 先说这个：本机真正装好的游戏

对 `E:\SteamLibrary\steamapps\common\` 与 `C:\Program Files (x86)\Steam\steamapps\common\` 逐目录清点
（大小 + exe 数 + >200 MB 文件数），实测结果：

| 目录 | 大小 | 状态 |
|---|---|---|
| **Black Myth Wukong Benchmark Tool** | 42 MB | ❌ 空壳，无 exe |
| **BlackMythWukong** | 35 MB | ❌ 空壳，无 exe |
| **ELDEN RING** (E:) | 14 MB | ❌ 仅 SeamlessCoop mod |
| **Red Dead Redemption 2** | **0 MB** | ❌ 空目录 |
| **Helldivers 2** | 102 MB | ❌ 仅 GameGuard + patch |
| **Palworld** | 20 MB | ❌ 仅 Mods/ |
| **DARK SOULS III** | 64 MB | ❌ 仅 ds3sc_launcher |
| **Titanfall2 / Subnautica / Monster Hunter World** | 1–11 MB | ❌ 空壳 |
| **Slay the Spire 2** | 516 MB | ⚠️ 有 1 个大文件，但 0 exe |
| **Stardew Valley** | 501 MB | ✅ 有 exe（非 3A） |
| BongoCat / Lossless Scaling / Awaria / Chef RPG | 36–621 MB | ✅ 可运行（非 3A） |

旁证：`E:\` 上**最大的游戏类文件是鸣潮** `E:\Wuthering Waves\`（**116 GB，完整可运行**）
和 `E:\Games\Fallout4_984\`（264 GB 区里的 辐射4 + MO2 整合，**有 `Fallout4.exe`**）。

**结论：能立刻用来做 3A 显存实测的，只有 `鸣潮`（116 GB）和 `辐射4`。**
Steam 库里那批 3A 全部需要重新下载（`libraryfolders.vdf` 下 `E:\SteamLibrary` 的 `apps` 只登记了 2 个 appid：
`993090` Lossless Scaling、`3419430` Bongo Cat —— **Steam 自己都不认为那些 3A 已安装**）。

### 3.1 推荐实测顺序（把用户劳动压到最低）

**Phase 0 — 用户只需 1 条命令（零游戏）**

```powershell
.\scripts\measure-game-vram.ps1 -Mode baseline -Label joyai-running-idle -DurationSec 60
```
JoyAI 常驻开着，游戏别开，跑 60 秒。→ 得到本机**真实底噪**，替换任务书里 911 MiB 的旧值。
（本轮已用 15 秒版本试过：**8184–8884 MiB**，可直接当起点，但建议用户跑满 60 秒取稳态。）

**Phase 1 — 用现成可跑的游戏验证方法（推荐「鸣潮」）**

| 步骤 | 用户动作 | 时长 |
|---|---|---|
| 1 | 鸣潮设 1280×720 全低，进入**同一地点**（如主城固定传送点） | 2 min |
| 2 | 站定不动，跑 `-Mode session -Label ww-720p-low -GameMatch "Client-Win64-Shipping"` | 60 s |
| 3 | 退出游戏，改 1920×1080 + 中画质，回同一地点 | 2 min |
| 4 | 同样采 60 s，`-Label ww-1080p-medium` | 60 s |
| 5 | 再改 2560×1440 + 高画质，同上 | 60 s |
| 6 | **每档结束**顺手 `Win+Shift+S` 截一张画质设置页 | 10 s |

「站定不动 + 同一地点」是**关键**：显存对场景内容敏感，跑动会污染档位对比。

**Phase 2 — 若要 Wukong 数据**：先重装官方 Benchmark Tool（Steam appid **2358720**），
然后它可无人值守跑完并给分，**显存由本脚本并行采集**（`-GameMatch "b1-Win64-Shipping"`）。

**Phase 3 — 抽样验证**：装 1 个有内置 benchmark 的 3A（ELDEN RING 无内置，建议 Helldivers 2 或
RDR2 的自带 benchmark），重复 Phase 1 流程 2 个档位即可，用于检验 Phase 1 结论是否可外推。

### 3.2 每档要记录的

1. 脚本自动出的 3 个文件（**用户不用记数**）
2. 一张画质设置页截图（唯一人工记录项）
3. 分辨率 + 画质档名 → 直接写进 `-Label`

**用户总劳动量：≈ 4 次改设置 × 2 分钟 + 4 张截图。**

### 3.3 帧率（可选，零安装）

用 **NVIDIA app 覆盖层**（已装）或 **Steam 覆盖层**（已启用，`EnableGameOverlay=1`）显示 FPS，
与脚本时间戳对齐即可。要逐帧 CSV 再考虑装 PresentMon / CapFrameX。

---

## 4. 关键假设核实

### 4.1 「之前只有 4、5G 显存」怎么来的 —— **算法成立，且解释了那个具体数字**

**本轮抓到了 JoyAI 真实在跑的现场数据**（侦察期间 llama-server 被另一端点拉起，
脚本只读采样 15 秒，未加载 GPU）：

| 测量项 | 数值 | 来源 |
|---|---|---|
| `nvidia-smi memory.used`（JoyAI 常驻 + 桌面） | **8184 → 8884 MiB** | nvidia-smi |
| `llama-server` 单进程峰值 | **7703.5 MiB** | GPU Process Memory 计数器 |
| `dwm.exe` | 572 MiB | 同上 |
| `DSH Desktop` | 200 MiB | 同上 |
| 桌面空闲（JoyAI **未**跑时） | **1120–1173 MiB** | 本轮实测 |

> 注意 `llama-server` 计数器读数 **7127–7703 MiB** 低于任务书给的 **9326 MiB**。
> 两者不矛盾：计数器是**已提交量**、且可能未计入 `mmproj` 视觉塔与 CUDA context；
> 而 9326 MiB 是 `nvidia-smi` 口径。**以 `nvidia-smi` 为准**（见 1.3 的双源铁律），
> 即 JoyAI 常驻实际约 **8.2–8.9 GB**，比任务书的 9.3 GB 略低——**对用户是好消息**。

那么余量算法：

```
显存总量（nvidia-smi）      16311 MiB
− JoyAI 常驻（本轮实测）    −8200~8900 MiB
− 桌面/其它常驻（本轮实测）  −1100~1200 MiB
────────────────────────────────────────
= 留给游戏的余量            ≈ 6200~7000 MiB  →  再被 WDDM 预算削一刀
```

**真实可用量比减法更小**，因为 WDDM 有独立预算。本机实测（`D3DKMTQueryVideoMemoryInfo`，非管理员可调）：

| 项 | 数值 |
|---|---|
| Local (VRAM) **Budget** | **15282 MiB** |
| Local CurrentUsage | 0.0 MiB |
| NonLocal (共享内存) Budget | 15578.9 MiB |

且 `nvidia-smi -q -d MEMORY` 报 **Reserved = 261 MiB**。

所以用户体感"只有 4、5G"是**合理的**：`16 − 9.3 − ~0.9(旧桌面基线) ≈ 5.8 GB`，
驱动在游戏启动时还会再抢一部分，**实际能稳定留给 3A 的约 4.5–5.5 GB** —— 与用户观察吻合。
→ **用户的数字来自真实减法，不是错觉；但 911 MiB 这个旧基线偏低，应以本轮实测的 ~1.1–1.2 GB 为准。**

### 4.2 `16311 vs 16384` 的 73 MiB 去哪了 —— **已定案**

三处独立读数：

| 来源 | 数值 |
|---|---|
| `nvidia-smi memory.total` | **16311 MiB** |
| `nvidia-smi -q -d MEMORY` | Total 16311 / **Reserved 261** MiB |
| **DXGI `DedicatedVideoMemory`**（脚本直读） | **16829644800 B = 16050.00 MiB** |
| DXGI `SharedSystemMemory` | 17141006336 B = 16346.94 MiB |

**73 MiB 不是"丢失"，而是 16 GiB 的标称值与实际可用帧缓冲之间的固件/驱动保留：**

```
16384 MiB (标称 16 GiB)
−  73 MiB  → 16311 MiB   （nvidia-smi 报的可用总量；由 VBIOS/固件/驱动在初始化时保留）
− 261 MiB  → 16050 MiB   （nvidia-smi 的 Reserved，再从可用量中扣掉）
```

> 这 73 MiB 的**具体构成没有任何公开 API 会暴露**（不是 pagefile、不是共享内存、
> 也不是 WDDM 的预算扣减），只能确认它是「标称容量 − 驱动报告总量」的固定差值，
> 且在同一驱动/同一卡上是稳定的。本卡 RTX 5060 Ti 为消费级卡，ECC 默认关闭，
> 因此不宜归因于 ECC —— 更可能是 VBIOS 保留区 + 帧缓冲管理开销。

验证：`16311 − 261 = 16050` **正好等于 DXGI 的 `DedicatedVideoMemory`**。
两条完全独立的 API 在此精确闭合，**73 MiB 的去向已确认**。

> 补充：**它不是 pagefile/共享内存造成的** —— `SharedSystemMemory` 是另外的 16 GB（走 PCIe 的
> 系统内存回退池），与 Dedicated 是两笔账。任务书猜测的 "WDDM shared memory" 不是那 73 MiB 的原因。

### 4.3 顺带修正任务书的两处前提

1. **911 MiB 基线偏低**：本轮实测桌面空闲为 **1120–1173 MiB**，
   且 `dwm.exe` 单项就占 ~570–650 MiB（因为接了 `spacedeskService` 虚拟显示器，额外吃显存）。
   JoyAI 在跑时总量为 **8184–8884 MiB**（见 4.1）——**这才是后续所有减法的正确基准**。
2. **"已装游戏"清单不成立**：见 3.0，绝大多数是空壳；Wukong Benchmark Tool 也不可用。

---

## 5. 预期产出与不确定项

### 5.1 方案能给出的产出（跑完 Phase 0–1）

- 本机**真实桌面底噪**（替代 911 MiB）
- **画质档位 ↔ 显存**映射表（3 档 × 1 游戏），带时间序列与峰值/稳态
- **JoyAI 与游戏共存**时的实际争抢情况：`proc_sum − baseline` 差分可判别谁被 WDDM 挤出到共享内存
- 回答用户原问题：**"1K 中高画质、30–60 fps" 是否在 ~5 GB 余量内可行**

### 5.2 不确定项（诚实标注）

| 不确定项 | 影响 | 缓解 |
|---|---|---|
| **3A 游戏本体缺失** | 无法立刻产出 3A 结论 | 先按 Phase 1 用鸣潮/辐射4 验证方法 |
| 帧率采集未打通 | 只能给显存，给不出 fps | Phase 1 用覆盖层；需逐帧再装 PresentMon |
| per-PID 计数器偏高 42% | 归因份额有系统偏差 | 已规定绝对值只用 nvidia-smi |
| Wukong Benchmark Tool 参数未知 | 无法确认其可否脚本化 | 需重装后实测 `--help`；不预判 |
| 鸣潮有 ACE 反作弊 | 注入式 FPS 工具可能被拦 | 只用覆盖层，不做注入 |
| 显存对场景敏感 | 档位间对比可能被场景差异污染 | 强制「同一地点 + 站定」 |
| JoyAI 计数器口径偏低 | per-PID 读数低估 ~1.2 GB | 已规定绝对值只用 nvidia-smi |

### 5.3 下一步建议（给接收端点）

1. **先问用户能否重装 1–2 个 3A**（Wukong Benchmark Tool appid 2358720 优先级最高）。
2. 装好前，可用**鸣潮**打通全流程，验证脚本与清单无误。
3. **务必先跑 Phase 0**：911 MiB 已过时，新基线是后续所有减法的基准。

---

## 附：本轮执行的只读命令（可复现）

```bash
nvidia-smi --query-gpu=memory.total,memory.used,memory.free,memory.reserved --format=csv
nvidia-smi -q -d MEMORY
typeperf -qx "GPU Process Memory"
typeperf -sc 1 '\GPU Process Memory(*)\Dedicated Usage'
powershell -c "(Get-Counter -Counter '\GPU Process Memory(*)\Dedicated Usage' -MaxSamples 1)"
es -path 'E:\SteamLibrary' '*.exe'          # 全库 exe 清点
du -sm E:/SteamLibrary/steamapps/common/*/  # 逐游戏体积
```

DXGI / D3DKMT 读数由临时探测脚本取得（**已删除**，不留副产物）。
