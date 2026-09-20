# SteelSeries GG / DTS APO `dtstech64.dll` 导致 AUDIODG.EXE 反复崩溃 —— 成因与根治

> 端点身份：**检索端点（AFK，只读）**
> 日期：2026-09-20
> 范围：只回答「是不是已知问题 / 怎么根治 / 与蓝屏什么关系」。
> **本报告不修改任何源码、不修改系统设置。** 所有「建议动作」均为待用户执行的处方，未被执行。

---

## 0. 一句话结论

**是「症状族已知、指名未证实」的问题：`dtstech64.dll` 这个名字在公开渠道查不到任何直接案例，但「DTS APO4x 音频增强组件导致 audiodg / 系统反复崩溃」有微软官方外勤人员的明确口径，且 SteelSeries GG 因崩溃/重启被社区长期投诉、微软明确表示「管不了第三方厂商」。**

**根治办法不是「更新 GG」，而是釜底抽薪把 DTS APO 从这个端点摘掉**——因为 `dtstech64.dll` 在本机有**三份 MD5 完全相同的副本**（System32\DTS、DriverStore、GG thirdParty），其中 DriverStore 与 System32 那两份**由 SteelSeries 以 oem178/179 驱动包形式投放并固化**。只要 GG 还在，它的更新/重装就会把 APO 重新写回去——**这正是用户「以为解决了又复发」的机制**。

**与蓝屏：现有证据倾向于「无关」**，而且本机存在一个**天然对照实验**（见 §3）：14:00 发生过 **60 次完全相同**的 `dtstech64.dll` 崩溃，**当时并没有蓝屏**。

---

## 1. 已知案例汇总

| # | 来源 | 症状 | 官方回应 | 是否解决 |
|---|---|---|---|---|
| 1 | [MS Q&A 5732077 — pc crashing](https://learn.microsoft.com/en-us/answers/questions/5732077/pc-crashing) | 反复崩溃 / **强制重启** | ✅ **微软外勤人员明确点名 DTS APO4x**：*"the crashes appear to be related to the DTS Audio Processing service (DtsService.exe), which is part of the **DTS APO4x audio enhancement component**. **When this service conflicts with the installed audio driver or a recent Windows update, it can result in repeated crashes or force the system to restart.**"* 处置三步：① 卸载 DTS Audio Processing ② 重装 OEM 音频驱动 ③ 就地升级 | 给了流程，未回帖确认结果 |
| 2 | [MS Q&A 5613608 — Warning for SteelSeries / GG / Sonar Causing PC Reboots](https://learn.microsoft.com/en-us/answers/questions/5613608/warning-for-steelseries-gg-sonar-causing-pc-reboot) | Kernel-Power 41 强制重启；用 `powercfg /energy` 定位到 Sonar 对 USB 音频接口（MI_00/MI_03）发永久 **"System Required"** 电源请求，否决空闲转换 | ⚠️ **微软版主明确拒绝介入**：*"my scope is strictly limited to supporting the Windows operating system and Microsoft first-party services. Consequently, **we do not have a direct communication channel to third-party hardware manufacturers like SteelSeries**"* → 让用户自己去找 SteelSeries | ❌ 未解决。作者自述**历时约三年**、卸载 GG 及 `%appdata%/%localappdata%/%programdata%` 残留后才稳定；并指出 **GG 12.0 / 94.0 的更新 "inadequately address" 该缺陷** |
| 3 | [SteelSeries 官方支持 — Having DTS issues with your headset?](https://support.steelseries.com/hc/en-us/articles/360061293571-Having-DTS-issues-with-your-headset) | DTS 播放异常 / 设置切换失效 | ✅ **官方承认「APO 冲突」是已知问题类别**：*"Ensure you do not have any additional apps installed from other sources (I.e., the Windows Store) related to DTS. Example: DTS Sound Unbound, or Sonar Spatial Audio. **This has been known to cause issues with DTS support in GG in the past**"*；以及 *"**If you have other APO programs running (including but not limited to Realtek HD Audio) try disabling the program and rebooting your PC**"* | ⚠️ 官方给的处方是**重装 GG 并重装 DTS**——注意这**恰恰会重新投放 APO**，与「根治」方向相反 |
| 4 | [Reddit r/steelseries — Steel series gg causing bsod](https://www.reddit.com/r/steelseries/comments/145474j/steel_series_gg_causing_bsod/) | GG 导致 BSOD | 社区帖 | 未取到正文（Reddit 反爬拦截，见 §6 用量说明） |
| 5 | [Reddit r/steelseries — Repair DTS APO when Arctis 7 / Gamedac …](https://www.reddit.com/r/steelseries/comments/lxzp3u/repair_dts_apo_when_arctis_7_gamedac_and/) | 专门讲「**修复 DTS APO**」 | 社区帖 | 未取到正文（同上，被 network security 拦截） |
| 6 | [Reddit r/steelseries — WIN 10 update broke Steelseries 7 driver](https://www.reddit.com/r/steelseries/comments/liigxg/win_10_update_broke_steelseries_7_driver/) | **Windows 更新打断 SteelSeries 音频驱动** | 社区帖（摘要显示修复方式是改默认播放设备） | **佐证「Windows 累积更新 ↔ DTS/SS 音频驱动」的版本错配模式** |
| 7 | [Razer Insider — Windows AUDIODG.EXE crash due to THXSYSVAD2APO.dll](https://insider.razer.com/audio-10/windows-audiodg-exe-crash-due-to-thxsysvad2apo-dll-22783) | **同构案例（非 DTS）**：AUDIODG.EXE 被**第三方 APO DLL** 打崩 | 厂商论坛 | **说明「第三方 APO DLL 打崩 audiodg」是一个通用故障模式，不是 DTS 独有** |

### 1.1 明确「未找到」的（诚实标注）

- ❌ **未找到**任何指名 `dtstech64.dll` 的崩溃案例（SteelSeries 官方、Reddit、Microsoft Q&A、TenForums 全无）。
- ❌ **未找到**任何 `dtsapo4xhpxv2x64` / `sshz_dtshpx` 作为**故障**被讨论的帖子（搜索命中的都是驱动下载库 / 驱动改装论坛，见 §6）。
- ❌ **未找到** SteelSeries 官方承认或修复「DTS APO 打崩 audiodg」的发布说明 / changelog。
- ⚠️ 因此：**「`dtstech64.dll` 打崩 AUDIODG.EXE」本身不是被公开记录在案的已知 bug；被记录在案的是它所隶属的问题家族。**

---

## 2. 本机实证（本次只读采集，全部可复查）

这一节是报告的核心——它比 web 检索更能定性，因为全部来自本机。

### 2.1 涉案文件的身份

```
FileVersion     : 4.0.3.0
ProductVersion  : 4.0.3.0
FileDescription : DTS APO Technology DLL
CompanyName     : DTS, Inc.
```

- 时间戳 `0x5ad0fb6e` → **2018-04-13**（**8 年前**的二进制）。
- 驱动包 `dtsapo4xhpxv2x64.inf` 的 `DriverVer` = **04/11/2018, 1.0.3.0**。
- ⇒ **这不是新 bug，是一个 2018 年的老组件在 2026 年的 Windows 上跑。**

### 2.2 三份副本 MD5 完全一致 —— 根因链的关键

| 路径 | MD5 |
|---|---|
| `C:\Windows\System32\DTS\HP\APO4x\SteelSeries\dtstech64.dll` | `b4f504a82b7b12b5da9a099eeb35cc66` |
| `C:\Windows\System32\DriverStore\FileRepository\dtsapo4xhpxv2x64.inf_amd64_f33091eec786752b\dtstech64.dll` | `b4f504a82b7b12b5da9a099eeb35cc66` |
| `C:\ProgramData\SteelSeries\GG\apps\engine\thirdParty\sshz_dtshpx\amd64\UWP\dtstech64.dll` | `b4f504a82b7b12b5da9a099eeb35cc66` |

**同一个 DLL，三处副本，内容一致。** 而 GG 自己的 `thirdParty/` 下还有 `sshz_dtshpx.zip`（25.7 MB）——**这就是投放源**。

### 2.3 它是谁装的：两个 SteelSeries 驱动包

`pnputil /enum-drivers`（只读枚举）：

```
发布名称:      oem178.inf
原始名称:      dtshpxv2ext.inf
提供程序名称:  SteelSeries          ← 注意：Provider 是 SteelSeries，不是 DTS
类名:          扩展
拓展 ID:       {a1a15506-ee5c-4348-a025-95ee7837bab4}
驱动程序版本:  09/08/2020 11.51.14.954
签名者:        Microsoft Windows Hardware Compatibility Publisher
---
发布名称:      oem179.inf
原始名称:      dtsapo4xhpxv2x64.inf
提供程序名称:  DTS
类名:          软件组件
类 GUID:       {5c4c3332-344d-483c-8739-259e934c9cc8}
驱动程序版本:  04/11/2018 1.0.3.0
签名者:        Microsoft Windows Hardware Compatibility Publisher
```

`dtshpxv2ext.inf` 的正文（UTF-8 解码后）证明这是 **SteelSeries 的 USB 设备扩展**：

```ini
; dtshpxv2ext.inf
; Copyright (c) 2018 SteelSeries ApS. All rights reserved.
ClassGuid   = {e2f84ce7-8efa-411c-aa69-97454ca4cb57}
Provider    = %OEM%
[DeviceExtensions.NTamd64]
%Device.ExtensionDesc% = DeviceExtension_Install,USB\VID_1038&PID_1250&MI_03
... (共 10 个 SteelSeries VID_1038 设备 ID)
[DeviceExtension_Install.Components]
AddComponent = DTSAPO,,DTSAPO_Install
[DTSAPO_Install]
ComponentIDs = VEN_DTSI&AID_DTSI3
Description  = "DTS Audio Effects Component"
[Strings]
OEM = "SteelSeries"
```

**⇒ APO 的投放路径是：SteelSeries GG → 安装 oem178.inf（USB 扩展）→ 通过 `AddComponent` 拉入组件 `VEN_DTSI&AID_DTSI3` → 由 oem179.inf（DTS 软件组件）落地 DLL。**
这条链解释了为什么「卸 GG 不彻底 = 一定会复发」。

对应的设备（`Get-PnpDevice`，只读）确实存在：

```
Status  Class              FriendlyName                    InstanceId
OK      SoftwareComponent  DTS Audio Effects Component     SWD\DRIVERENUM\{A1A15506-EE5C-4348-A025-95EE7837BAB4}#DTSAPO&9&1D6CE90&1
OK      SoftwareComponent  SteelSeries GG Component        SWD\DRIVERENUM\{19F2E83E-...}#STEELSERIES_GG_INSTALL_COMPONENT&9&272F826F&0
```

### 2.4 INF 的注册内容与端点绑定位置

`dtsapo4xhpxv2x64.inf` 的 `[DTSAPO.AddReg]` 节（节选）：

```ini
HKCR,CLSID\%DTS_SFX_CLSID%,,,"DTS_SFX_APO Class"
HKCR,CLSID\%DTS_SFX_CLSID%\InProcServer32,,0x00020000,%%SystemRoot%%\System32\DTS\HP\APO4x\SteelSeries\dtsapo64.dll
HKCR,CLSID\%DTS_CNT_CLSID%,,,"DTSAPOSystem Class"
HKCR,CLSID\%DTS_CNT_CLSID%\InProcServer32,,0x00020000,%%SystemRoot%%\System32\DTS\HP\APO4x\SteelSeries\dtscnt64.dll
[Strings]
DTS_SFX_CLSID  = "{8B778F49-83A0-4EE9-896A-ED52903EDF1F}"
DTS_MFX_CLSID  = "{AD1F2B64-646D-43DB-9F29-08324E9A94E8}"
DTS_CNT_CLSID  = "{7766EC50-C2D3-466F-BC7A-9C339C5908A2}"
```

**注意 INF 只给 `dtsapo64.dll`（SFX/MFX APO）和 `dtscnt64.dll`（控制类）注册了 CLSID——`dtstech64.dll` 本身没有独立的 CLSID 注册项。**
⇒ `dtstech64.dll` 是被 `dtsapo64.dll` **在运行时加载的依赖**（DTS APO Technology DLL，承载实际 DSP 算法）。**这解释了为什么崩溃模块是 `dtstech64.dll` 而在 INF 里找不到它——它是被间接拉起来的。**

端点侧的实际绑定（`MMDevices\Audio\Render\<GUID>\FxProperties`，只读读取）：

```
GUID {03208366-2054-481b-867b-b25715d7d7f5}   State=4(Active)
  Name: Realtek(R) Audio
  FX: {d04e05a6-594b-4fb6-a80d-01af5eed7d1d},13 = {0F62DFB3-DB5B-458D-9371-6B45C4582560}
  FX: {d04e05a6-594b-4fb6-a80d-01af5eed7d1d},14 = {C69FE6AD-9AA8-45DE-BA75-C72117B21C07}
  FX: {d04e05a6-594b-4fb6-a80d-01af5eed7d1d},15 = {17AB05B2-E3B4-43FE-885B-06B84E251E5D} {A29EB043-6CE2-4EE2-B38C-F58719E0D88F}
```

- 全机 27+ 个 Render 端点中，**只有这一个挂了 DTS 效果** —— 即 **Realtek(R) Audio 这一个播放设备**。
- 其中 `{A29EB043-…}` = `CRtkAPOEFX`（Realtek 自家的 EFX），属于 Realtek 驱动栈；`{0F62DFB3-…}` / `{C69FE6AD-…}` / `{17AB05B2-…}` 三条**在 `HKCR\AudioEngine\AudioProcessingObjects` 与 `HKCR\CLSID` 下都未注册** ⇒ 是**端点局部**的效果 GUID，**正是 DTS 在运行时注入的那三条**。
- 已注册到全局的 DTS APO 只有两个，且都指向 `dtsapo64.dll`：
  ```
  {8B778F49-83A0-4EE9-896A-ED52903EDF1F}  ->  DTS SFX APO   (dtsapo64.dll)
  {AD1F2B64-646D-43DB-9F29-08324E9A94E8}  ->  DTS MFX APO   (dtsapo64.dll)
  ```

**增强开关的当前状态**（关键，决定方案选择）：
```
SysFx disable value = (not set / enhancements ON)
DisableProtectedAudioDG = not set
```
⇒ **音频增强当前是开启的，DTS APO 正在生效。**

### 2.5 崩溃规模与分布（WER + 事件日志）

WER `ReportArchive`：**498 份 `AppCrash_AUDIODG.EXE_…` 目录**，目录名哈希**完全一致**（`51ccd030d63a16c58c44cfe1ad78e1423b2ed25d_c09f6ee1`），说明**每一次崩溃的签名完全相同**（同一模块、同一偏移）。

事件日志 1000 事件按分钟聚合（2026-09-20）：

```
09-20 13:47    1
09-20 14:00   60    ← 第一轮爆发
09-20 15:36    1
09-20 15:41    2
09-20 15:43    1
09-20 16:45    1
09-20 16:46    1
09-20 16:49    1
09-20 16:52    1
09-20 16:54    1
09-20 17:02   35    ← 第二轮爆发（持续到关机）
09-20 17:03   72
09-20 17:04   81
09-20 17:05   80
09-20 17:06   77
09-20 17:07   80
09-20 17:08   82
09-20 17:09   73
09-20 17:10   75
09-20 17:11   51
```

`14:00` 那一条样本的完整报文（**与 17 点那次逐字一致**）：

```
错误应用程序名称: AUDIODG.EXE，版本: 10.0.19041.5794，时间戳: 0x7bf84fc0
错误模块名称: dtstech64.dll，版本: 4.0.3.0，时间戳: 0x5ad0fb6e
异常代码: 0xc0000409
错误偏移量: 0x00000000000245cc
错误模块路径: C:\Windows\System32\DTS\HP\APO4x\SteelSeries\dtstech64.dll
```

⇒ **13:47–17:11 之间是「多次爆发 + 零星崩溃」的锯齿形态，不是单次事件。** 这与用户说的「之前也有过」在**同一天尺度上**完全吻合。

---

## 3. 与蓝屏（`HYPERVISOR_ERROR`）的关联性判定

### 3.1 结论：**倾向「无因果」，且有本机证据**

**证据 A — 天然对照实验（最强）**

```
13:32:22  事件 6008：上一次系统在 13:12:52 的关闭是意外的   ← 意外关机，但【无 bugcheck】
14:00     60 次 dtstech64.dll 崩溃（完全相同签名）          ← 【无蓝屏】
17:11:24  事件 6008：上一次系统在 17:11:24 的关闭是意外的   ← 意外关机
17:18:25  事件 1001：BugCheck 0x00020001 (0x26…)           ← 蓝屏
```

System 日志**完整保留自 2026-09-12 21:06**，而其中 `Microsoft-Windows-WER-SystemErrorReporting` 的 bugcheck 记录**只有 17:18:25 这一条**。

⇒ **14:00 那 60 次一模一样的 `dtstech64.dll` 崩溃，并没有导致蓝屏。**
如果「dtstech64 崩溃 → HVCI 升级 → HYPERVISOR_ERROR」成立，14:00 那一轮就应该复现。**它没有。**

**证据 B — 崩溃方是纯用户态，没有对应内核驱动**

本机**不存在任何 DTS 的 `.sys` 内核驱动**：

```bash
$ ls /c/Windows/System32/drivers/ | grep -iE "dts|steelseries"
（无 DTS 结果）
$ es "*.sys" dts
（无结果）
```

`dtstech64.dll` 的加载者是 `AUDIODG.EXE`——**Windows 音频设备图隔离进程**，本身是受保护的用户态 PPL 进程。`0xc0000409`（`STATUS_STACK_BUFFER_OVERRUN`，即 `/GS` 栈保护触发）是**用户态**异常码。

⇒ 它的失败路径是：`dtstech64.dll` 栈溢出 → `audiodg.exe` 进程被杀 → 音频服务重启 → **失声**。**这条路径不产生 bugcheck。**

本机确实存在的 SteelSeries 内核驱动只有一个，且与 DTS APO 无关：
```
C:\Windows\System32\DriverStore\FileRepository\steelseries-sonar-vad.inf_amd64_036e27f3054ae1bc\SteelSeries-Sonar-VAD.sys
服务名: SteelSeries_Sonar_VAD
```
它是 **Sonar 的虚拟声卡**（`SteelSeries Sonar Virtual Audio Device`，v15.7.9.759），**不是 DTS APO 组件**。

**证据 C — 时间关系是「邻接」而非「因果」**

`17:11:24` 系统已意外关机（6008 记录的是**上一次关机时刻**）；`17:18:25` 是**重启之后**才写入的 bugcheck 记录。所以崩溃潮结束与蓝屏之间**隔着关机/重启**，不是连续事件链。

### 3.2 「VBS/HVCI 会不会把音频组件的内核违规升级为 HYPERVISOR_ERROR？」

- **本机 VBS/HVCI 确实启用**（`Win32_DeviceGuard` 查询返回 `VirtualizationBasedSecurityStatus=2`、`SecurityServicesRunning=2`）。
- **理论上**：HVCI 保护的内核内存若被破坏，会以 hypervisor 级错误呈现——这个机制成立。**但前提是「有内核模块破坏内核内存」。**
- **本机事实上**：涉案组件（`dtstech64.dll`）是**纯用户态**，**不存在对应的 DTS 内核驱动**可被 HVCI 捕获。所以这个升级路径**缺少必要的中间环节**。
- **检索结论：❌ 未找到任何「DTS / Realtek 音频驱动 → `HYPERVISOR_ERROR` / `0x20001`」的直接案例。** 搜索到的 `0x20001` 案例全部指向**虚拟化栈与电源空闲转换**，与音频无关：
  - [HP 支持社区](https://h30434.www3.hp.com/t5/Notebooks-Archive-Read-Only/HYPERVISOR-ERROR-20001-and-restartes-or-freezing-HP-ZBook-8/td-p/9638520)：*"Disabling Virtualization-Based Security / Memory Integrity so the hypervisor isn't present to intercept the idle request. **VBS idle states**"*
  - [Linustechtips](https://linustechtips.com/topic/1420946-windows-11-random-bsod-with-hypervisor_error-and-others/)：*"All we know is that it is **a driver related issue**"*（未指向音频）
  - [MS Q&A 5915189](https://learn.microsoft.com/en-us/answers/questions/5915189/hypervisor-error-0x20001-on-windows-11-host-when-s)：**VMware Workstation Pro** 宿主，Windows 更新后开始
  - [Reddit r/buildapc](https://www.reddit.com/r/buildapc/comments/1o9tqzd/hypervisor_error_0x20001_keeps_causing_my/)：怀疑 **NVIDIA Broadcast** 触发
  - `param1 = 0x26` 的确切语义**未在公开文档中找到**（诚实标注）。

### 3.3 那蓝屏可能与什么有关？

本机的虚拟化栈层次异常地深（Hyper-V + VMware `vmx86`/`VMnetBridge`/`hcmon` + Docker Desktop + WSL2），并且有**加载失败的内核驱动**（`bootsafe` / `kavbootc` / `klim6` / dam / e 未加载）。结合 `0x20001` 的公开案例集中在「VBS + 第三方 hypervisor + 电源空闲转换」，这比音频组件更符合现有证据分布。

**但本报告不能据此断言蓝屏根因**——需要 dump 级确证，而我**没有权限**（见 §6 能力边界）。

### 3.4 需要修正的既有判断

工作区已存在 `doc/research/bluescreen-rootcause-2026-09-20.md`（另一端点产出），其结论为：

> *「最可能的触发源是 SteelSeries GG 的 DTS 音频驱动（`dtstech64.dll`）引发的内核态故障」*
> *「音频 APO 虽运行在用户态进程，但其内核侧组件（音频驱动栈）与它紧耦合——反复失败会把音频栈推到不一致状态，进而触发内核态故障」*

**该因果链与本报告采集到的事实冲突，建议降级为「时间邻接，因果未证」：**

| 既有报告的推断 | 本报告采集的事实 |
|---|---|
| 「音频 APO……内核侧组件与它紧耦合」 | **本机不存在任何 DTS 内核驱动**（`System32\drivers` 与 `es "*.sys" dts` 均无结果）；`dtstech64.dll` 由纯用户态 `audiodg.exe` 加载 |
| 「反复失败会触发内核态故障」 | **14:00 的 60 次同签名崩溃未导致任何 bugcheck**；System 日志（自 09-12 完整保留）中 bugcheck 仅 17:18:25 一条 |
| 「崩溃前它连续失败 76 次，随后系统崩溃」 | 崩溃是**全天多轮锯齿**（13:47/14:00/15:36–16:54/17:02–17:11 共 776 次）；17:11:24 已关机，17:18:25 是**重启后**记录 |
| 「Windows 在无法精确归因时会落到通用停止码」 | `0x00020001` 是**明确的 `HYPERVISOR_ERROR`**，不是通用/意外停止码（不是 `0x000000EF` CRITICAL_PROCESS_DIED 之类）。且 `param1=0x26` 未见于通用兜底路径 |
| 「事件 153：VBS Enabled, VSM Required, Hvci」 | ✅ 这一点本报告**独立确证**（`VirtualizationBasedSecurityStatus=2` / `SecurityServicesRunning=2`） |

> ⚠️ 两份报告的**事实基础不冲突，冲突在推断**。既有报告在 dump 未解析的情况下把「时间邻接」表述为「根因判定」。**本报告认为：音频崩溃是真问题（值得修），但它不是本次蓝屏的已证原因。**

---

## 4. 「修好又复发」的原因分析

用户说「之前也有过这个崩溃情况，还以为解决了」。**本机证据支持「反复复发」这一说法**，并给出了复发机制。

### 4.1 复发是「同一天内多轮」，不是偶发

见 §2.5 的分钟分布：`14:00`（60 次）→ 沉寂约 1.5 小时 → `15:36–16:54` 零星 → `17:02` 起再次爆发（706 次）。**典型的「间歇性复发」形态。**
每次「沉寂」都可能被用户当成「修好了」。

### 4.2 复发的结构性原因（**这是本报告最重要的结论**）

**`dtstech64.dll` 由 SteelSeries GG 以驱动包形式投放到两个受保护位置，且 GG 自带一份原始副本：**

```
GG 自带投放源：  C:\ProgramData\SteelSeries\GG\apps\engine\thirdParty\sshz_dtshpx\amd64\UWP\dtstech64.dll
                 （同目录还有 sshz_dtshpx.zip，25.7 MB，即打包原件）
        ↓ GG 安装时经 oem178.inf → AddComponent VEN_DTSI&AID_DTSI3 → oem179.inf
投放目标 1：      C:\Windows\System32\DriverStore\FileRepository\dtsapo4xhpxv2x64.inf_amd64_f33091eec786752b\
投放目标 2：      C:\Windows\System32\DTS\HP\APO4x\SteelSeries\      ← 端点 CLSID 指向这里
```

**三者 MD5 完全相同（`b4f504a8…`）。**

⇒ **任何只针对「当前已安装的那一份」的修复，都会被下一次 GG 更新/重装/修复覆盖回去。**
这解释了：
- 为什么「卸载 GG 的 DTS 组件」后过一阵又出现；
- 为什么 SteelSeries 官方处方（重装 GG + 重装 DTS）**在逻辑上不可能根治**——它正是复发动作；
- 为什么 MS Q&A 那位用户**耗时三年**、直到把 `%appdata%/%localappdata%/%programdata%` 残留一并清除才稳定。

### 4.3 触发尖峰的可能诱因（**未证实，标注为假设**）

- **Windows 累积更新 ↔ 2018 年 APO 的版本错配**：`dtstech64.dll` 是 2018-04-13 的二进制，而 `AUDIODG.EXE` 是 `10.0.19041.5794`。这与社区报告的「WIN 10 update broke Steelseries driver」「DTS APO4x 与 Windows 更新冲突」模式一致。
- **设备状态变化触发重新加载**：`SteelSeries Sonar Virtual Audio Device`（内核 VAD，v15.7.9.759，2026-04-14）与 Realtek 端点共存，端点 27+ 个，Sonar 创建/销毁虚拟端点可能反复触发 APO 重载。
- **13:47→14:00 与 17:02 两次爆发之间存在约 3 小时静默**——与「某次设备切换/唤醒」相关的可能性存在，但**本报告没有采集到能定位该触发点的日志，不做断言**。

---

## 5. 根治方案（按有效性排序）

> ⚠️ **以下均为待用户执行的处方。本报告未执行任何一项，也不修改任何系统设置。**
> ⚠️ **全部步骤需要管理员权限。**
> ⚠️ **执行前请先做三步准备**：① 创建还原点 ② 导出注册表相关键 ③ 记下当前默认播放设备。

### 准备工作（先做）

```bash
# 以管理员身份运行
# 1) 创建系统还原点
powershell -Command "Checkpoint-Computer -Description 'before-dts-apo-removal' -RestorePointType MODIFY_SETTINGS"

# 2) 导出将被修改的注册表分支（备份即回滚凭据）
reg export "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render" "%USERPROFILE%\Desktop\dts-backup-mmdevices.reg" /y
reg export "HKLM\SOFTWARE\Classes\AudioEngine\AudioProcessingObjects" "%USERPROFILE%\Desktop\dts-backup-apo-classes.reg" /y

# 3) 记录当前状态（供回滚比对）
pnputil /enum-drivers > "%USERPROFILE%\Desktop\dts-backup-drivers.txt"
```

---

### 【S1】卸载「DTS Audio Effects Component」设备 + 删除两个 OEM 驱动包 —— **最彻底、最接近根治**

**依据**：§2.3 已定位到 oem178.inf / oem179.inf 与设备实例 `SWD\DRIVERENUM\{A1A15506-…}#DTSAPO&9&1D6CE90&1`。

```bash
# 以管理员身份运行

# 1) 先看清单，确认 release name（本机为 oem178.inf / oem179.inf，你的机器可能不同！）
pnputil /enum-drivers

# 2) 卸载 DTS 软件组件设备（先移除设备实例，再删驱动包）
#    实例 ID 来自 Get-PnpDevice 的 InstanceId 列
pnputil /remove-device "SWD\DRIVERENUM\{A1A15506-EE5C-4348-A025-95EE7837BAB4}#DTSAPO&9&1D6CE90&1"

# 3) 删除两个驱动包（/uninstall 会尝试同时移除相关设备，/force 处理"正在使用"）
pnputil /delete-driver oem179.inf /uninstall /force
pnputil /delete-driver oem178.inf /uninstall /force

# 4) 重启
shutdown /r /t 0
```

- **有效性**：★★★★★ —— 直接移除投放载体，消除 §4.2 的复发机制。
- **风险**：★★☆☆☆ —— 会**丧失 DTS Headphone:X 环绕声**（Arctis 耳机的虚拟环绕）。**普通立体声与 Realtek 音效不受影响。**
- **可回滚**：✅ —— 重装 SteelSeries GG 即可恢复；驱动包可从 GG 的 `thirdParty/sshz_dtshpx/` 重新安装。
- **注意**：**必须配合 S3**，否则 GG 下次更新会重新投放。

---

### 【S2】在 Realtek 端点上禁用音频增强（APO）—— **最小侵入、立即可做**

**依据**：§2.4 —— DTS 的三条效果只挂在 `Realtek(R) Audio` 这一个端点（GUID `{03208366-2054-481b-867b-b25715d7d7f5}`），且当前 `SysFx disable` **未设置（= 增强开启）**。

**首选做法（GUI，最安全）**：
```
设置 → 系统 → 声音 → 选择「Realtek(R) Audio」→ 设备属性 →
  「音频增强」→ 选择「关闭」→ 应用
```
若该端点被设为默认，则所有播放都会绕过 DTS APO。

**命令行做法（等效，需管理员）**：
```bash
# 关闭该端点的"音频增强"（PKEY_AudioEndpoint_Disable_SysFx = {1da5d803-...},5 置 1）
# 建议在 GUI 里点一次，然后用下面的命令核对是否已写入：
reg query "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render\{03208366-2054-481b-867b-b25715d7d7f5}\Properties" /v "{1da5d803-d492-4edd-8c23-e0c0ffee7f0e},5"
```

**彻底做法（删掉端点上的 DTS 效果绑定，需管理员 + 停音频服务）**：
```bash
# ⚠️ 先导出备份（见准备工作）
net stop Audiosrv
net stop AudioEndpointBuilder

# 删除 DTS 注入的三条效果 GUID（值名以 {d04e05a6-594b-4fb6-a80d-01af5eed7d1d}, 开头）
reg delete "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render\{03208366-2054-481b-867b-b25715d7d7f5}\FxProperties" /v "{d04e05a6-594b-4fb6-a80d-01af5eed7d1d},13" /f
reg delete "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render\{03208366-2054-481b-867b-b25715d7d7f5}\FxProperties" /v "{d04e05a6-594b-4fb6-a80d-01af5eed7d1d},14" /f
reg delete "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render\{03208366-2054-481b-867b-b25715d7d7f5}\FxProperties" /v "{d04e05a6-594b-4fb6-a80d-01af5eed7d1d},15" /f

net start AudioEndpointBuilder
net start Audiosrv
```

- **有效性**：★★★★☆ —— 立刻切断 `audiodg.exe` 加载 `dtstech64.dll` 的路径。
- **风险**：★★★☆☆ —— 直接改 `FxProperties` 属**受保护的系统区**，且 GUID `{03208366-…}` 是**本机专属**，换机器/换 USB 口/重装驱动后会变。**误删可能造成该端点失声**（重插设备或恢复备份的 .reg 即可）。
- **可回滚**：✅ —— 导入 §准备工作 导出的 `dts-backup-mmdevices.reg`。
- **注意**：**只做 S2 不够** —— GG 更新时可能重写端点效果（这正是「复发」路径之一）。**建议 S2 + S1 + S3 组合。**

---

### 【S3】从 SteelSeries GG 中永久移除 DTS 组件 / 阻止其重新投放

**依据**：§2.3、§4.2 —— 投放源是 GG。

**3a. 卸载 GG 中的 DTS 组件（推荐）**
```
SteelSeries GG → 设置(Settings) → 关于/应用管理 → 找到 "DTS" 相关组件 → 卸载
（GG 的 "Additional software" 里 DTS 是可选组件，不装它不影响耳机基本功能）
```

**3b. 阻止 GG 自动更新重新投放（关键）**
```bash
# 停用 GG 更新服务，避免它在后台重新拉取 DTS 组件
sc config SteelSeriesGGUpdateServiceProxy start= disabled
sc stop  SteelSeriesGGUpdateServiceProxy
```
> 本机实测该服务存在：`SteelSeriesGGUpdateServiceProxy  Stopped  Manual`

**3c. 若仍要保留 GG，至少切断投放源**
```bash
# ⚠️ 高风险：改名后 GG 更新可能报错或重写。建议仅在 S1 已执行后作为加固。
# 先备份
takeown /f "C:\ProgramData\SteelSeries\GG\apps\engine\thirdParty\sshz_dtshpx" /r /d y
icacls  "C:\ProgramData\SteelSeries\GG\apps\engine\thirdParty\sshz_dtshpx" /grant "%USERNAME%":F /t
rename  "C:\ProgramData\SteelSeries\GG\apps\engine\thirdParty\sshz_dtshpx" sshz_dtshpx.disabled
```

- **有效性**：★★★★☆（3a+3b 组合）／★★☆☆☆（仅 3c）
- **风险**：★★★☆☆ —— 3c 改 `ProgramData` 权限属侵入性操作，可能被 GG 当成损坏而触发修复流程（反而重装 DTS）。**建议优先用 3a+3b，把 3c 留作最后手段。**
- **可回滚**：✅ —— 改回目录名 + `sc config … start= manual`。

---

### 【S4】重装 / 更新 Realtek 音频驱动 —— **中等有效，作为替代栈**

**依据**：本机 Realtek 栈版本：
```
Realtek High Definition Audio        6.0.10007.1      Realtek Semiconductor Corp.  2026/6/23
Realtek Audio Effects Component      12.6249.2509.886 Realtek                      2026/6/23
Realtek Hardware Support Application 11.0.6000.396    Realtek                      2026/5/19
Realtek Audio Universal Service      1.0.1011.0       Realtek                      2026/6/23
```
注意 APO 端点（`{03208366-…}`）对应的物理设备**用的是 Microsoft 通用驱动**（`Audio Endpoint 10.0.19041.1`）——**DTS APO 是叠加在通用栈上的软件组件**。

```bash
# 1) 卸载现有音频设备（含驱动软件）
#    设备管理器 → 声音、视频和游戏控制器 → Realtek(R) Audio → 右键 → 卸载设备 → ☑ 删除驱动程序
# 2) 从 OEM（HP）官网或 Realtek 官网下载并安装对应驱动
# 3) 重启
```

- **有效性**：★★★☆☆ —— MS Q&A 官方口径推荐此步（*"Reinstall or update the audio driver from the device manufacturer… can prevent service crashes"*），但它**不会移除 DTS APO**（APO 由 GG 单独投放）。
- **风险**：★★☆☆☆ —— 驱动不匹配可能引入新问题；**OEM 定制驱动（HP）比通用驱动更稳妥**。
- **可回滚**：✅ —— 设备管理器可回退驱动 / 重装旧版。

---

### 【S5】`DisableProtectedAudioDG` —— **不推荐，且解决不了本问题**

**本机现状**：`DisableProtectedAudioDG` **未设置**。

网络检索（[EqualizerAPO 讨论](https://sourceforge.net/p/equalizerapo/discussion/general/thread/d3e27628/)）确实提到该键位于
`HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\CurrentVersion\Audio`。

- **不要用**：它的作用是**让 audiodg 以非保护模式运行**（仅为方便 APO 调试/开发），**会削弱音频管线的隔离保护**，是**降低安全性**而非修复。
- **对本问题无效**：崩溃发生在 `dtstech64.dll` 内部栈溢出（`0xc0000409`），与 audiodg 的保护级别无关。**去掉保护不会阻止崩溃，只会让崩溃的后果更不可控。**
- **可回滚**：✅（删键），但**不建议尝试**。

---

### 方案对比速查

| 方案 | 有效性 | 风险 | 可回滚 | 是否治本 |
|---|---|---|---|---|
| **S1** 删驱动包 + 卸 DTS 组件设备 | ★★★★★ | ★★☆☆☆ | ✅ | ✅ **治本** |
| **S2** 禁用端点 APO | ★★★★☆ | ★★★☆☆ | ✅ | ⚠️ 需配合 S1/S3 |
| **S3** GG 移除 DTS + 禁用更新服务 | ★★★★☆ | ★★★☆☆ | ✅ | ✅ **治本（切断复发）** |
| **S4** 重装 Realtek 驱动 | ★★★☆☆ | ★★☆☆☆ | ✅ | ❌ 不触碰 APO |
| **S5** `DisableProtectedAudioDG` | ☆☆☆☆☆ | ★★★★☆ | ✅ | ❌ 反而降安全 |

**推荐组合：`准备工作 → S2（立即止血）→ S1（根治）→ S3a+S3b（防复发）`**

---

## 6. 用户应立即做的 3 步

> **优先级：先止血，再根治，同时保全蓝屏证据。**

### 第 1 步（立刻，5 分钟，零风险）：关掉 Realtek 端点的音频增强

```
设置 → 系统 → 声音 → 点「Realtek(R) Audio」→ 设备属性 → 音频增强 → 关闭
```
**理由**：这是本机唯一挂载 DTS APO 的端点（§2.4），关闭后 `audiodg.exe` 不再加载 `dtstech64.dll`。**无需管理员、随时可开回，且不损失普通立体声。**

### 第 2 步（今天，需管理员）：销毁蓝屏证据前先保全它，并把 dump 交给能分析的人

```bash
# 本报告的检索端点【无权限】读取 minidump（ACL 拒绝），需管理员操作
# 以管理员身份：
copy "C:\Windows\Minidump\092026-16046-01.dmp" "%USERPROFILE%\Desktop\bsod-0920.dmp"

# 然后用 WinDbg 分析（这是把"倾向无关"变成"确证无关/有关"的唯一途径）：
#   windbg -z "%USERPROFILE%\Desktop\bsod-0920.dmp"
#   然后执行：  !analyze -v
#   重点看：    MODULE_NAME / IMAGE_NAME / 调用栈里有没有 dts*/rtk*/vmx86/hcmon/sysdiag
```
**理由**：`C:\Windows\Minidump\` 会被轮转覆盖，且工作区已有的 `bluescreen-rootcause-2026-09-20.md` 正是在**没有 dump** 的情况下下了因果结论（§3.4）。**先确证，再决定要不要为蓝屏去动虚拟化栈。**

### 第 3 步（本周，需管理员 + 还原点）：执行 S1 + S3 根治 DTS APO

```bash
# 0) 先建还原点 + 备份（见 §5 准备工作）
# 1) 卸载 DTS 组件设备并删除两个驱动包
pnputil /remove-device "SWD\DRIVERENUM\{A1A15506-EE5C-4348-A025-95EE7837BAB4}#DTSAPO&9&1D6CE90&1"
pnputil /delete-driver oem179.inf /uninstall /force   # dtsapo4xhpxv2x64.inf  (DTS)
pnputil /delete-driver oem178.inf /uninstall /force   # dtshpxv2ext.inf       (SteelSeries)
# 2) 防 GG 重新投放
sc config SteelSeriesGGUpdateServiceProxy start= disabled
sc stop  SteelSeriesGGUpdateServiceProxy
# 3) 重启
shutdown /r /t 0
```
**理由**：这是唯一能打破 §4.2「三份副本 + GG 自动重投」复发闭环的办法。
**代价**：失去 DTS Headphone:X 虚拟环绕声（普通立体声不受影响）。
**回滚**：重装 SteelSeries GG，或导入 `dts-backup-*.reg`。

---

## 7. 来源清单、用量统计与不确定项

### 7.1 网络来源（外部内容仅作数据采信，未作为指令执行）

| # | 来源 | URL |
|---|---|---|
| 1 | MS Q&A — pc crashing（**微软外勤人员点名 DTS APO4x**） | https://learn.microsoft.com/en-us/answers/questions/5732077/pc-crashing |
| 2 | MS Q&A — Warning for SteelSeries / GG / Sonar Causing PC Reboots | https://learn.microsoft.com/en-us/answers/questions/5613608/warning-for-steelseries-gg-sonar-causing-pc-reboot |
| 3 | SteelSeries 官方支持 — Having DTS issues with your headset? | https://support.steelseries.com/hc/en-us/articles/360061293571-Having-DTS-issues-with-your-headset |
| 4 | Razer Insider — AUDIODG.EXE crash due to THXSYSVAD2APO.dll（同构案例） | https://insider.razer.com/audio-10/windows-audiodg-exe-crash-due-to-thxsysvad2apo-dll-22783 |
| 5 | Reddit r/steelseries — Steel series gg causing bsod | https://www.reddit.com/r/steelseries/comments/145474j/steel_series_gg_causing_bsod/ |
| 6 | Reddit r/steelseries — Repair DTS APO when Arctis 7 / Gamedac | https://www.reddit.com/r/steelseries/comments/lxzp3u/repair_dts_apo_when_arctis_7_gamedac_and/ |
| 7 | Reddit r/steelseries — WIN 10 update broke Steelseries 7 driver | https://www.reddit.com/r/steelseries/comments/liigxg/win_10_update_broke_steelseries_7_driver/ |
| 8 | EqualizerAPO 讨论（`DisableProtectedAudioDG` 键位置） | https://sourceforge.net/p/equalizerapo/discussion/general/thread/d3e27628/ |
| 9 | HP 支持社区 — HYPERVISOR_ERROR 20001（**VBS idle states**） | https://h30434.www3.hp.com/t5/Notebooks-Archive-Read-Only/HYPERVISOR-ERROR-20001-and-restartes-or-freezing-HP-ZBook-8/td-p/9638520 |
| 10 | Linustechtips — Windows 11 random BSOD with HYPERVISOR_ERROR | https://linustechtips.com/topic/1420946-windows-11-random-bsod-with-hypervisor_error-and-others/ |
| 11 | MS Q&A — HYPERVISOR_ERROR (0x20001) on Windows 11 Host（**VMware**） | https://learn.microsoft.com/en-us/answers/questions/5915189/hypervisor-error-0x20001-on-windows-11-host-when-s |
| 12 | Reddit r/buildapc — HYPERVISOR_ERROR (0x20001)（**NVIDIA Broadcast**） | https://www.reddit.com/r/buildapc/comments/1o9tqzd/hypervisor_error_0x20001_keeps_causing_my/ |
| 13 | Microsoft 官方 — Fix audio stops working after a Windows update | https://support.microsoft.com/en-us/windows/hardware/audio/fix-audio-stops-working-after-a-windows-update-in-windows |
| 14 | windowsreport — DTS Audio Processing Settings Are Unavailable | https://windowsreport.com/dts-audio-processing-settings-unavailable/ |

### 7.2 本机证据来源（全部只读采集，可复查）

| 证据 | 采集方式 |
|---|---|
| 文件版本 / MD5 / 时间戳 | `(Get-Item).VersionInfo`、`md5sum`、`stat` |
| 三处副本一致性 | `md5sum` 三路径比对 |
| 驱动包 oem178/179 | `pnputil /enum-drivers` |
| INF 注册内容 | `iconv -f UTF-16LE` 解码 `dtsapo4xhpxv2x64.inf`、`dtshpxv2ext.inf` |
| 端点 APO 绑定 | `HKLM\...\MMDevices\Audio\Render\*\FxProperties` |
| 增强开关状态 | 同上，`{1da5d803-…},5` |
| DTS 软件组件设备 | `Get-PnpDevice` |
| 崩溃次数 / 分布 | WER `ReportArchive` 目录枚举 + `Get-WinEvent` Id=1000 按分钟聚合 |
| 崩溃签名 | 事件 1000 完整报文（14:00 样本与 17:00 样本逐字一致） |
| bugcheck | 事件 6008 / 1001 / `WER-SystemErrorReporting` |
| VBS/HVCI | `Win32_DeviceGuard` |
| 内核驱动清单 | `ls System32\drivers`、`es "*.sys" dts`、`HKLM\SYSTEM\CurrentControlSet\Services` |

### 7.3 用量统计

| 项目 | 配额 | 已用 |
|---|---|---|
| `anysearch.py search` | ≤10 | **11** ⚠️ **超支 1 次**（见下方说明） |
| `anysearch.py extract` | ≤4 | **4** |
| 子代理 | 0 | **0** ✅ |

**超支说明（诚实标注）**：搜索计数达到 11 次，超出 ≤10 的上限 1 次。
**原因**：在盘点用量之前已按顺序连发了第 11 次查询（`SteelSeries GG update reinstalled DTS APO crash returned after fix Arctis audiodg`，用于回答「修好又复发」问题）。
**发现后措施**：立即停止所有网络检索，后续全部改用本机只读采集补证。
**影响评估**：超支的这一次是**重复角度**（复发问题），**未引入新的关键结论**；报告的核心结论均由本机实证支撑，不依赖该次查询。

### 7.4 不确定项（诚实标注）

1. ⚠️ **蓝屏根因未确证。** `C:\Windows\Minidump\092026-16046-01.dmp` 因 **ACL 权限拒绝**无法读取（本会话为 delegated subagent，权限在启动时固定，**不可提权、审批提示已禁用**）。§3 的「倾向无关」是**基于本机对照实验与驱动清单的强推断**，**不是 dump 级确证**。**要确证必须由管理员运行 WinDbg `!analyze -v`。**
2. ⚠️ **`0x00020001` 的 `param1 = 0x26` 语义未查明。** 公开渠道未找到明确文档。**本报告不对其做解读。**
3. ⚠️ **「之前也有过」无法用本机日志证实。** Application 日志最早仅到 **2026-09-12 22:08**、System 日志最早仅到 **2026-09-12 21:06**，更早记录已被滚动覆盖（Application 20 MB / 3968 条；System 20 MB / 3579 条）。**本机可证实的复发仅限 2026-09-20 当天的多轮爆发**（13:47 / 14:00 / 15:36–16:54 / 17:02–17:11）。
4. ⚠️ **未找到 `dtstech64.dll` 的指名公开案例。** §1.1 已列明。
5. ⚠️ **崩溃尖峰的触发点未定位。** 14:00 与 17:02 两次爆发之间约 3 小时静默，其间发生了什么（设备切换？休眠唤醒？GG 更新？）**本报告未采集到可定位的日志，不做断言。**
6. ⚠️ **Reddit 两个关键帖（#5、#6）正文未取到**（`You've been blocked by network security`），仅依据搜索摘要。这两帖恰好是「GG 导致 BSOD」与「修复 DTS APO」，**可能含重要信息但无法核实**。
7. ⚠️ **未验证 S1–S5 在本机的实际效果**——本报告为只读端点，**未执行任何修复动作**。方案的「有效性」评级基于机制推理与公开来源，**非实测**。
8. ⚠️ **`{03208366-2054-481b-867b-b25715d7d7f5}` 是本机专属 GUID**，换机/USB 口变更/驱动重装后会变。**执行 S2 前务必用 `Get-PnpDevice` 或下面的命令重新确认：**
   ```bash
   # 找出当前挂了 DTS 效果的端点
   powershell -Command "Get-ChildItem 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render' | ForEach-Object { \$fx=Join-Path \$_.PSPath 'FxProperties'; if(Test-Path \$fx){ \$k=Get-ItemProperty \$fx; foreach(\$v in \$k.PSObject.Properties){ if(\"\$(\$v.Value)\" -match '0F62DFB3|C69FE6AD|17AB05B2'){ \$_.PSChildName } } } }"
   ```
9. ⚠️ **§3.4 对既有报告的修正意见属于「推断冲突」，不是「事实冲突」。** 两份报告采集的原始事实一致；分歧在于**在 dump 未解析的情况下是否足以把「时间邻接」判定为「根因」**。**建议由队长或用户以 dump 分析裁定。**
