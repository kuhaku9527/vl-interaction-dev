# 蓝屏根因分析（2026-09-20）

> ## ⚠️⚠️ 本文结论已被推翻（同日更正，详见 §0）
>
> **原结论**：「最可能的触发源是 SteelSeries GG 的 DTS 音频驱动（`dtstech64.dll`）引发的内核态故障」
>
> **更正后结论**：**该因果链不成立，应降级为「时间邻接」。**
> 蓝屏原因**未定**，现有线索更指向**虚拟化栈**（Hyper-V + VMware + Docker + WSL 多层，
> 及加载失败的安全软件内核驱动）。
>
> **不要引用本文 §二 的根因判定。** 保留原文仅为留痕。

## §0 更正（2026-09-20，检索端点提出 + 主理人独立复验）

### 反驳证据（三条，主理人已逐条复验成立）

**① 决定性天然对照：14:00 崩了 60 次，却没有蓝屏**

主理人独立跑查（`Application` 日志 `id=1000`，13:30–16:00）：
```
13:47  x1
14:00  x60     ← 60 次签名完全相同的 dtstech64.dll 崩溃
15:36  x1
15:41  x2
```
而同期 BugCheck 事件（`System` 日志 `id=1001`，**自 09-12 起完整保留**）**全天只有 1 条**（17:18:25）。

**⇒ 若「DTS 崩溃 → 内核故障」成立，14:00 就该复现蓝屏。它没有。**

**② 本机不存在任何 DTS 内核驱动**

主理人复验：`C:\Windows\System32\drivers\` 下**零个** `*dts*.sys`。
`dtstech64.dll` 由 **`audiodg.exe`（纯用户态、PPL 保护进程）** 加载，
其 `0xc0000409`（STACK_BUFFER_OVERRUN / BEX64）是**用户态栈溢出**。
⇒ **失败路径是「音频失效」/「反复重启服务」，不产生 bugcheck。**
（原文所谓「内核侧组件与它紧耦合」的假设**与本机事实不符**。）

**③ 时序上 bugcheck 是重启后才写入的**

`id=6008` 原文：`上一次系统的 下午5:11:24 … 的关闭是意外的`。
17:18:25 的 bugcheck 记录是**重启之后**的上报。

### 另一处更正
原文称「Windows 无法精确归因时会落到通用停止码」——
**错误**。`0x00020001` 是**明确的 `HYPERVISOR_ERROR`**，不是通用兜底码。

### 仍然成立的部分
- **与 JoyAI / 显存 / 游戏无关**：无 TDR、无 `nvlddmkm` 事件、无 WHEA、显存未触顶、无游戏崩溃 ✅
- **VBS/HVCI 已启用**：`VirtualizationBasedSecurityStatus=2` / `SecurityServicesRunning=2` ✅
  （检索端点独立确证）

### 现在的归因方向（**未定，待 dump 确证**）
公开 `0x20001` 案例分布指向：**VBS/HVCI + 第三方 hypervisor + 电源空闲转换**。
本机符合前两项且虚拟化栈层次多：
`Hyper-V` + `VMware`(`vmx86`/`VMnetBridge`/`hcmon`) + `Docker Desktop` + `WSL2`，
另有**加载失败的内核驱动**：`bootsafe` / `kavbootc` / `klim6`（`id=7026`）。

### 唯一能定论的一步
**管理员身份解析 dump** → 看 `!analyze -v` 的 `MODULE_NAME`：
```powershell
winget install Microsoft.WinDbg
powershell -ExecutionPolicy Bypass -File scripts\analyze-dump.ps1
```
⚠️ **dump 会被轮转覆盖**，建议先复制保全。

---

## 一、事实（全部来自 Windows 事件日志，可复查）

### 崩溃标识
| 项 | 值 |
|---|---|
| BugCheck 代码 | **`0x00020001`** = **`HYPERVISOR_ERROR`** |
| 参数 1 | `0x26`（38） |
| Dump | `C:\Windows\Minidump\092026-16046-01.dmp`（4,270,176 B，17:18:19 写入） |
| 报告 ID | `9b19323d-671a-4657-abfe-868ff2b713ac` |

**`HYPERVISOR_ERROR` 是 Hyper-V/虚拟化层错误，不是显卡驱动崩溃（不是 TDR）。**

### 关键时间线
| 时间 | 事件 |
|---|---|
| **17:10:59 – 17:11:38** | **`AUDIODG.EXE` 连续崩溃 76 次**，错误模块全部是 **`dtstech64.dll`**（异常码 `0xc0000409` = STACK_BUFFER_OVERRUN） |
| 17:11:24 | 事件 6008：系统**意外关机**（上次关机时间） |
| 17:18:09 | 系统重新启动 |
| 17:18:11 | 事件 41：系统未正常关机 |
| 17:18:25 | BugCheck `0x20001` 记录 |

**⇒ 系统实际在 17:11:24 之前就崩溃了**；17:18 是重启后的时间。

### 崩溃场景的其它事实
- **两次非正常关机**：13:32（无 bugcheck 码）与 17:18（`0x20001`）
- **显卡/显示驱动事件**：`--- 6. GPU / display driver events last 2 days ---` **为空**
  ⇒ **没有任何 TDR（显卡驱动超时）或 `nvlddmkm` 相关错误**
- **无 WHEA 硬件错误事件**（CPU/内存/PCIe 硬件层干净）
- 磁盘错误 `\Device\Harddisk2\DR2` 出现于 **17:18:30**，即**重启之后**——
  是**转储写入失败**（`volmgr id=161 创建转储文件失败`）的后果，**不是原因**
- 物理盘健康：kimtigo SSD 512GB / ZHITAI TiPlus7100 1TB，**两块均 Healthy**
- 页面文件：只有 `C:\pagefile.sys`（16–24 GB），未配置 D 盘页面文件

### 无关项（已排除）
- **显存**：16,311 MiB 总量，JoyAI 占 ~8 GB，**未触及上限**
- **游戏**：日志中**无任何游戏进程崩溃**记录
- **JoyAI**：无相关崩溃记录

## 二、根因判定

### 最可能：SteelSeries DTS 音频驱动
`dtstech64.dll` 位于：
```
C:\Windows\System32\DTS\HP\APO4x\SteelSeries\dtstech64.dll
C:\Windows\System32\DriverStore\FileRepository\dtsapo4xhpxv2x64.inf_amd64_*/dtstech64.dll
C:\ProgramData\SteelSeries\GG\apps\engine\thirdParty\sshz_dtshpx\amd64\UWP\dtstech64.dll
```
它是 **DTS 的音频处理对象（APO）**，由 **SteelSeries GG** 安装，运行在 **`audiodg.exe`（Windows 音频设备图隔离进程）** 中。

**崩溃前它连续失败 76 次**，随后系统崩溃。音频 APO 虽运行在用户态进程，但其**内核侧组件（音频驱动栈）与它紧耦合**——
反复失败会把音频栈推到不一致状态，进而触发内核态故障。

**为什么归为 `HYPERVISOR_ERROR` 而非音频相关**：Windows 在无法精确归因时，
会落到通用/意外类停止码；`HYPERVISOR_ERROR` 亦见于「受 VBS/HVCI 保护的内核内存被破坏」的场景。
本机 **VBS/HVCI 已启用**（事件 153：`VBS Enabled, VSM Required, Hvci`），
任何内核态内存破坏都会被 HVCI 捕获并升级为 hypervisor 级错误。

### 加重因素（非主因）
- **VBS/HVCI 启用**：会把内核内存完整性违规升级为 `HYPERVISOR_ERROR`
- **SteelSeries GG 常驻**：日志显示 `SteelSeriesGGClient.exe` 在跑
- **Vmware / Docker Desktop / WSL 常驻**：`vmx86`、`VMnetBridge`、`hcmon`、`DockerDesktopWSL` 均在启动序列中，
  虚拟化栈层次多（Hyper-V + VMware + Docker）
- **杀毒内核驱动**：`sysdiag`（火绒）、`bootsafe`、`kavbootc`、`klim6`（卡巴斯基残件）
  —— `id=7026 以下引导启动或系统启动驱动程序未加载: bootsafe dam e kavbootc klim6`（**驱动加载失败**）

## 三、与本次实验的关系

**用户是在「跑游戏 + JoyAI」时蓝屏的**，但从证据看：
- **不是显存耗尽**（8 GB / 16 GB，未触顶）
- **不是显卡驱动崩溃**（无 TDR / `nvlddmkm` 事件）
- **不是游戏本身崩溃**（无游戏进程错误）

**更可能是巧合**：长时间高负载运行让本就反复失败的音频驱动更快暴露。
（`dtstech64.dll` 的 76 次崩溃发生在开机后不久，与游戏无必然联系。）

## 四、建议动作（按优先级）

| # | 动作 | 理由 |
|---|---|---|
| **1** | **更新或重装 SteelSeries GG**（或卸载其 DTS 音频组件） | `dtstech64.dll` 是直接嫌疑；该软件更新后常修此类 APO 崩溃 |
| **2** | 用 **WinDbg** 分析 `C:\Windows\Minidump\092026-16046-01.dmp`（**需管理员**） | 可确定崩溃时的**具体驱动栈**，把嫌疑从"很可能"变"确定" |
| **3** | 清理**未加载成功的安全软件驱动残件**（`bootsafe`/`kavbootc`/`klim6`） | 加载失败的内核驱动是已知的不稳定源 |
| **4** | 若再次蓝屏，**先停 SteelSeries GG 再复现** | 对照实验可确证因果 |

## 五、复现与取证

```bash
# 事件日志（本报告数据来源）
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/diagnose-bluescreen.ps1
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/diagnose-bluescreen-forensics.ps1

# Minidump 结构解析（当前因权限受限，需管理员复制 dump）
python scripts/parse-minidump.py [dump路径]
```
原始数据：`bluescreen-diagnosis.txt`、`bluescreen-deepdive.txt`、`bluescreen-forensics.txt`

## 六、不确定项（诚实标注）
- **dump 未解析成功**（`C:\Windows\Minidump\` 需管理员权限）。本报告的根因判定基于**事件日志的时间关联**，
  属**高置信度推断**而非 dump 级确证。**若要确证，请以管理员身份运行 WinDbg**。
- `dtstech64.dll` 的 76 次崩溃与最终蓝屏之间是**时间紧邻**关系；因果方向需 dump 或对照实验确认。
- `HYPERVISOR_ERROR` 的确切参数语义（`0x26`）未在公开文档中查到明确解释。
