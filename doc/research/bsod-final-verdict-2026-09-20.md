# 蓝屏最终裁定（2026-09-20）—— dump 级确证

> **数据来源**：`D:\bsod-forensics\092026-16046-01.dmp` 经 WinDbg `!analyze -v` 解析
> （用户以管理员身份执行，原始输出 `logs/blue-log.txt`）。
> **本报告取代此前所有推断**——包括报告 A（DTS）、报告 B（虚拟化栈方向）与专家裁定。

---

## 一、★ dump 给出的权威事实

### 崩溃标识
```
BugCheck:        0x00020001 = HYPERVISOR_ERROR
Arguments:       26 0 0 0
FAILURE_BUCKET_ID: 0x20001_26_0_nt!HvlSkCrashdumpCallbackRoutine
MODULE_NAME:     nt
IMAGE_NAME:      ntkrnlmp.exe
PROCESS_NAME:    System
IMAGE_VERSION:   10.0.19041.6456
Debug session time: 2026-09-20 17:11:54.029 (UTC+8)
System Uptime:      0 days 3:39:48.822
```

**⇒ 崩溃时刻 17:11:54；开机时刻 = 17:11:54 − 3:39:48 = `13:32:05`**（与事件日志 `13:32:10` 吻合）。

### ★★ 调用栈（决定性）

```
nt!KeBugCheckEx
nt!HvlSkCrashdumpCallbackRoutine+0x6b     ← 触发点：Hyper-V 安全内核崩溃转储回调
nt!KiProcessNMI+0xea                      ← 处理 NMI 中断
nt!KxNmiInterrupt+0x82
nt!KiNmiInterrupt+0x212                   ← ★ 收到 NMI
nt!PpmIdleGuestExecute+0x1d               ← ★ 以 guest 身份空闲
nt!PpmIdleExecuteTransition+0x10c0        ← 电源空闲状态转换
nt!PoIdle+0x58a
nt!KiIdleLoop+0x54                        ← ★ 系统在【空闲】
```

**解读**：
系统处于**空闲**（`KiIdleLoop` → `PoIdle` → 电源状态转换），
**hypervisor 自身发生致命错误**，于是**发出 NMI**，
Windows 在 NMI 处理中调用 `HvlSkCrashdumpCallbackRoutine`，
该回调**直接触发 bugcheck**。

**⇒ 不是任何第三方驱动崩溃。**
**⇒ 是 hypervisor 层自己的致命错误。**

### Hypervisor 状态标志
```
Hypervisor.Flags.AnyHypervisorPresent = 1
Hypervisor.RootFlags.IsHyperV          = 1      ← 宿主就是 Hyper-V
Hypervisor.RootFlags.Nested            = 0      ← 未嵌套
Hypervisor.Flags.VsmAvailable          = 1      ← VSM 可用
Hypervisor.Flags.CpuManager            = 1
Hypervisor.Flags.DynamicCpuDisabled    = 1      ← CPU 动态禁用已启用
Hypervisor.RootFlags.CrashdumpEnlightened = 1
Hypervisor.Enlightenments.ValueHex     = 497cf9c
```

## 二、★ 崩溃时加载的第三方内核驱动（从模块表提取）

| 驱动 | 归属 | 类别 |
|---|---|---|
| **`vmx86`** | **VMware** | **hypervisor 层** |
| **`hcmon`** | **VMware** | 监控 |
| `vmnetbridge` / `VMNET` / `vmnetuserif` | VMware | 网络 |
| **`sysdiag`** | **火绒** | 安全软件 |
| **`hrdevmon` / `hrndis6` / `hrwfpdrv`** | **第三个安全软件**（`hr` 前缀） | 设备/网络/WFP 过滤 |
| `ndisrd` | 第三方 NDIS 过滤 | 网络 |
| `RTKVHD64` | Realtek HD Audio | 音频 |
| `nvlddmkm` | NVIDIA | 显卡 |

**⇒ 崩溃时机器上同时运行**：
- **Hyper-V（宿主 hypervisor）**
- **VMware（`vmx86` —— 另一个 type-1 级 hypervisor 组件）**
- **三个安全软件的网络/设备过滤驱动**

**⚠️ 注意**：`DtsService`/`dtstech64.dll` **不在内核模块表中**——再次确认 DTS 与本次蓝屏无因果。

## 三、根因判定

### 主因（高置信）：**VMware hypervisor 与 Hyper-V/VBS 的抢占冲突**

证据链：
1. **栈显示 hypervisor 自身致命错误**（而非某个驱动崩溃）——`HvlSkCrashdumpCallbackRoutine`
2. **`vmx86` 与 `hcmon` 在崩溃时加载** —— VMware 的 hypervisor 组件在场
3. **`IsHyperV = 1`** —— 宿主 Hyper-V 在运行（VBS/HVCI 依赖它）
4. **崩溃发生在空闲时**（`PoIdle`/`PpmIdleGuestExecute`）——
   这正是「两个 hypervisor 争 VT-x / VMCS 状态被破坏」的典型暴露窗口
5. 公开案例分布一致：微软 Q&A 有多个 **"VMware 致 `0x20001`"** 报告

**机制**：Hyper-V 是 type-1 hypervisor 并占据 VT-x；VMware Workstation 在
Hyper-V 启用时需走 **Windows Hypervisor Platform (WHP)** 兼容路径。
该兼容层在 **CPU 电源状态转换**（空闲进出）时与 hypervisor 的
VP（virtual processor）状态管理交互，**任何不一致都会导致 hypervisor 致命错误**。

### 次因（中置信）：**第三方内核过滤驱动加剧**

`sysdiag`（火绒）+ `hr*`（第三个安全软件）+ `ndisrd` 共 **6 个网络/设备过滤驱动**
同时挂在栈上。它们不直接触发 bugcheck，但：
- 增加空闲/唤醒路径的 DPC 与状态机复杂度
- 与 VBS/HVCI 的完整性校验交互（`CI.dll` 在模块表中）

### 已彻底排除
| 假设 | 状态 |
|---|---|
| **DTS 音频驱动**（报告 A） | ❌ **排除**。不在内核模块表；14:00 崩 60 次无蓝屏；栈与音频无关 |
| **显卡 / 显存 / TDR** | ❌ **排除**。`nvlddmkm` 在表但栈无它；无 TDR 事件 |
| **游戏 / JoyAI** | ❌ **排除**。栈显示系统**在空闲**时崩溃 |
| **磁盘 / 硬件** | ❌ 大概率排除。无 WHEA；磁盘 Healthy。但**未做 MemTest** |

## 四、⚠️ 与前序报告的差异（必须说明）

| 报告 | 结论 | 判定 |
|---|---|---|
| 报告 A（DTS 音频） | DTS → 内核故障 → 蓝屏 | ❌ **已排除**（dump 无音频栈） |
| 报告 B（虚拟化栈方向） | 方向对，未锁定元凶 | ✅ **方向正确** |
| 专家裁定（候选① VMware） | 主嫌 VMware 冲突，置信"中" | ✅ **本次提为"高"**（dump 支持） |

**专家此前的判读规则中「`vmx86`/`hcmon` 落点 → 候选① 坐实」——
栈里没有 `vmx86` 帧**（因为它不是调用方；hypervisor 错误不经由驱动栈）。
**但 `vmx86` 的加载 + `IsHyperV=1` + 空闲触发，三者合起来指向同一结论。**

## 五、行动方案

### 立即（零风险）
1. **已保全证据** ✅（`D:\bsod-forensics\`：dump + System.evtx + Application.evtx）
2. **确认 Hyper-V 与 VMware 的共存状态**：
   ```powershell
   bcdedit /enum {current} | Select-String hypervisorlaunchtype
   Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V-All | Select State
   Get-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform | Select State
   ```
3. **检查是否近期更新过 VMware / Windows**（更新常是触发点）

### 短期（需还原点，**一次只改一个变量**）
```powershell
Checkpoint-Computer -Description "Pre-BSOD-VMware" -RestorePointType MODIFY_SETTINGS
bcdedit /export D:\bsod-forensics\bcd-backup
```

**路径 A（若你不用 VMware 跑 Linux VM）—— 推荐**
卸载 VMware Workstation；保留 Hyper-V（WSL2/Docker/VBS 都依赖它）。
```powershell
winget uninstall VMware.WorkstationPlayer   # 或从"应用"里卸载
```

**路径 B（若你必须用 VMware）**
- **升级 VMware Workstation 到最新版**（新版对 WHP 兼容性更好）
- 若仍复现：`bcdedit /set hypervisorlaunchtype off`（**会同时关停 WSL2 / Docker / VBS**，需你权衡）

**路径 C（减少变量）**
清理第三方过滤驱动（尤其那个 `hr*` 系列的来源软件 + 火绒与 HVCI 的兼容性确认）。

### 验证
改完**运行 ≥48h 并覆盖至少一次空闲时段**（本次崩溃就在空闲时发生）。
若复现 → 回退还原点，换下一路径。

## 六、诚实边界

| 结论 | 置信度 | 依据 |
|---|---|---|
| hypervisor 自身致命错误（非驱动崩溃） | **确证** | dump 栈 |
| 崩溃发生在空闲/电源转换时 | **确证** | dump 栈（`PoIdle`/`PpmIdleGuestExecute`） |
| VMware + Hyper-V 共存是主因 | **高**（非绝对） | `vmx86`/`hcmon` 加载 + `IsHyperV=1` + 空闲触发 + 公开案例 |
| 具体机制（WHP/VP 状态不一致） | **推断** | 无法从 dump 直接读出 hypervisor 内部状态 |
| `Arg1=0x26` 的含义 | **未查明** | 微软文档标 Reserved，无公开字典 |
| DTS / 显卡 / 游戏 / JoyAI | **已排除** | dump 栈与模块表 |
| 内存/CPU 硬件 | **未证伪** | 无 WHEA，但未跑 MemTest |
