# 蓝屏时间线修正与因果链（2026-09-20）

> **本报告修正了检索端点中期汇报中的一处结论错误（"时序游离"），
> 并确认崩溃潮与系统崩溃是同一时刻。**

## 一、★ 时间线修正（决定性证据）

检索端点称：「崩溃潮 17:10:59–17:11:38，**17:11:24 已「系统意外关机」**，蓝屏 17:18:25 在 7 分钟之后」
→ 据此判断"时序游离"。

**该判断错误。** 事件日志 `id=6008` 原文：

```
[17:18:24.698] id=6008 EventLog:
   上一次系统的 下午5:11:24 在 20/9/2026 上的关闭是意外的。
```

**⇒ `17:18` 全部是「重启之后写报告」的时间，不是崩溃时间。**
`17:11:24` 才是系统实际崩溃（意外关机）的时刻。

### 修正后的时间线

| 时刻 | 事件 |
|---|---|
| 16:45:01 | AUDIODG 首次崩溃（零星） |
| **17:02:34 起** | **崩溃转为密集：每秒 1–2 次** |
| **17:11:24** | **系统意外关机（=`id=6008` 记录的关闭时刻）** |
| 17:11:38 | AUDIODG **最后一次**崩溃记录（711 条中的最后一条） |
| 17:18:09 | 系统重新启动 |
| 17:18:25 | WER 上报 BugCheck `0x20001`（**重启后**） |
| 17:20:02 | 内核 WER 报告 `Kernel_20001_35b468f5…` 生成 |

**⇒ 崩溃潮的末端（17:11:38）与系统崩溃（17:11:24）**在同一分钟内**，不是游离 7 分钟。**
（17:11:24 关机后仍写入了 17:11:25–17:11:38 的崩溃记录，属关机过程中的残留上报。）

**结论：时间关联性成立，不再是"证据不足"。**

## 二、新增实测事实

### AUDIODG 崩溃规模
| 项 | 值 |
|---|---|
| Application 日志 `id=1000` AUDIODG 崩溃事件 | **711 条** |
| WER `AppCrash_AUDIODG.EXE` 报告目录 | **498 个** |
| 崩溃模块 | **`dtstech64.dll` 4.0.3.0（时间戳 `0x5ad0fb6e`）** |
| 异常码 | `0xc0000409`（STACK_BUFFER_OVERRUN / BEX64） |
| 时间跨度 | 16:45:01 → 17:11:38 |

### DTS 驱动包（`DriverStore`）
```
目录: dtsapo4xhpxv2x64.inf_amd64_f33091eec786752b
INF:  dtsapo4xhpxv2x64.inf
  DriverVer = 04/11/2018, 1.0.3.0     ← 8 年前的驱动
  Provider  = DTS
  DestinationDirs = 11, DTS\HP\APO4x\SteelSeries
  包含: dtsapo64.dll / dtscnt64.dll / dtstech64.dll
        DtsHPXV2Apo4Service.exe        ← ★ 它带一个服务
```
**⇒ 与检索端点找到的微软官方口径吻合**：官方回复点名 *"DTS Audio Processing service (DtsService.exe)"*，
而本机该包里正是 `DtsHPXV2Apo4Service.exe`。**链路对上了。**

### SteelSeries 常驻规模
**13 个 SteelSeries 进程**在跑（`SteelSeriesEngine` / `GG` / `GGClient`×7 / `GGEZ` /
`Moments` / `Prism` / `Sonar`），合计内存约 **420 MB**。

### 关键 APO 绑定（来自检索端点实测，本报告采信）
DTS APO 绑定端点 **Realtek(R) Audio**（GUID `{03208366-2054-481b-867b-b25715d7d7f5}`），
FxProperties 内 3 条 DTS 效果 CLSID：
```
{d04e05a6-594b-4fb6-a80d-01af5eed7d1d},13   → {0F62DFB3-DB5B-458D-9371-6B45C4582560}
{d04e05a6-594b-4fb6-a80d-01af5eed7d1d},14   → {C69FE6AD-9AA8-45DE-BA75-C72117B21C07}
{d04e05a6-594b-4fb6-a80d-01af5eed7d1d},15   → {17AB05B2-E3B4-43FE-885B-06B84E251E5D}
                                              + {A29EB043-6CE2-4EE2-B38C-F58719E0D88F}
```
**⇒ 禁用 APO 的落点：删除这些 FxProperties 项。**

## 三、因果链（更新后）

```
DTS APO（2018 年的 dtstech64.dll，经 SteelSeries GG 投放进 DriverStore）
   ↓  与当前 Windows 10 19045 音频栈不兼容
AUDIODG.EXE 反复崩溃（711 条事件 / 498 份 WER，17:02 起每秒 1–2 次）
   ↓  音频栈被推到不一致状态
内核态故障 → BugCheck 0x20001 (HYPERVISOR_ERROR)
   ↓  本机 VBS/HVCI 已启用
内核内存完整性违规被 HVCI 捕获并升级为 hypervisor 级错误
```

**仍缺的一环**：dump 未解析。**`!analyze -v` 的 `MODULE_NAME` 是判定这一环的唯一权威依据。**

## 四、诚实的边界

| 结论 | 置信度 | 依据 |
|---|---|---|
| `dtstech64.dll` 在崩溃前反复失败 | **已确证** | 711 条事件 + 498 份 WER |
| 崩溃潮与系统崩溃同时刻 | **已确证** | `id=6008` 原文 |
| DTS 驱动包是 2018 年，且带 `DtsHPXV2Apo4Service.exe` | **已确证** | INF 原文 |
| 音频驱动导致本次蓝屏 | **高置信推断** | 时间关联 + 官方口径 + 缺失的 dump 环节 |
| `HYPERVISOR_ERROR` 由 HVCI 升级 | **推断** | HVCI 确已启用；但个例子级 `0x20001` 归因机制未见于公开文档 |
| 与 JoyAI / 显存 / 游戏有关 | **已排除** | 无 TDR、无 WHEA、显存未触顶、无游戏崩溃 |

## 五、下一步（唯一能定论的一步）

**需管理员身份解析 dump**：
```powershell
winget install Microsoft.WinDbg
powershell -ExecutionPolicy Bypass -File scripts\analyze-dump.ps1
```
**看 `MODULE_NAME` / `IMAGE_NAME`**：
- 指向 `dtstech64` / DTS / Realtek → 因果确证
- 指向 `nvlddmkm` 或其他 → **推翻本报告结论，须重新归因**
