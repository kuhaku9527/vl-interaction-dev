# 蓝屏裁定书：BugCheck 0x20001 HYPERVISOR_ERROR（2026-09-20）

> 端点身份：**通用诊断 / 独立裁定**
> 输入：报告 A、报告 B、时间线原始取证（本目录 `bluescreen-*` / `bsod-*` / `dts-audio-*`）
> 约束：只读，未执行任何命令，未改系统。

---

## 1. 裁定

**B 对，但 B 只说清了一半——不是 A 对，也不是"B 不完整到需要保留 A"。**

一句话：**A 的因果链已被报告 B 的三条反证实质推翻（用户态栈溢出 + 14:00 天然对照组 + bugcheck 为重启后上报），B 的"排除 A"成立、方向（虚拟化栈）正确；但 B 亦未证明虚拟化栈内的具体元凶，故本次裁定 = A 出局、真因未定、候选集中在 hypervisor 层。**

| | 判定 |
|---|---|
| A（DTS → 内核故障 → 蓝屏） | **不成立**。被告无内核侧存在（`drivers\*dts*.sys` 零个），失败路径终止于用户态 `audiodg.exe`；14:00 的 60 次同签名崩溃无任何 bugcheck，构成天然对照，直接否证因果。 |
| B（方向指向虚拟化栈） | **成立**。`0x20001` 按定义即 hypervisor 自身致命错误，且本机 VBS/HVCI + 四层虚拟化栈共存，与公开案例分布一致。 |
| 两者共同缺口 | **都用"时间邻接"代替了 dump 证据**。A 用它证因果（错），B 用它证方向（可接受但不充分）。 |

**保留意见（对 A 的公平话）**：A 的时间点（17:11:24 前后音频栈高频异常）并非无意义——它可能是一个**次要压力源/并发症状**（CPU 时间片与 DPC 抖动），但它**不是触发者**。A 从"时间最近"跳到"根因"是逻辑越界。

---

## 2. `0x20001` 的 `param1 = 0x26` 的含义

**未查明。**

- 微软官方文档 [Bug Check 0x20001 HYPERVISOR_ERROR](https://learn.microsoft.com/en-us/windows-hardware/drivers/debugger/bug-check-0x20001--hypervisor-error) 对该 bugcheck 的 **参数 1–4 全部标注为 `Reserved`**，未公开语义。
- 公开的 `0x20001` 调试资料（含 [bsodtutorials 专文](https://bsodtutorials.wordpress.com/2024/03/03/debugging-stop-0x20001-hypervisor_error/)）一致表述为"**只能逐个 case 分析**"，不提供 param1 字典。
- `0x26` 属于 hypervisor 内部错误码空间（非 Windows NTSTATUS），**任何"0x26 = 某某具体错误"的说法都将是编造**，不予采纳。

**⇒ 结论：不得据 `param1=0x26` 下任何结论。唯一权威来源是 dump 内 `!analyze -v` 的 `MODULE_NAME` / `IMAGE_NAME` / `FAILURE_BUCKET_ID`。**

---

## 3. 根因候选排序（前 3）

### ① VMware 与 Hyper-V/VBS 的 hypervisor 抢占冲突（主嫌）

| 项 | 内容 |
|---|---|
| 支持证据 | 本机 `vmx86`/`VMnetBridge`/`hcmon` 与 Hyper-V/VBS 同时在场，属"两个 hypervisor 争 VT-x"的经典形态；报告 B 已把方向定于此；微软 Q&A 有 [VMware 致 0x20001](https://learn.microsoft.com/en-us/answers/questions/5909591/vmware-cause-hypervisor-error-0x20001) 与 [Win11 宿主启动 VM 时 0x20001](https://learn.microsoft.com/en-us/answers/questions/5915189/hypervisor-error-0x20001-on-windows-11-host-when-s) 两个高度同形的案例（后者明确指向 Windows 更新后宿主/VMM 交互）。Intel 侧历史上亦有 **VMCS 相关致命条件**的记载。 |
| 反驳/弱点 | 无 dump 证据；`0x20001` 亦常见于完全无 VMware 的机器（[reddit 案例](https://www.reddit.com/r/techsupport/comments/1l82tqh/random_bsod_hypervisor_error_20001/)），故是"最可能"而非"已证"。13:32 那次非正常关机（bugcode=0）同类性质尚不明。 |
| 验证方法 | dump `!analyze -v` 看是否命中 `vmx86`/`vmkbd`/`hcmon`/`VMNET`；再用 `bcdedit /enum {current}` 与 `Get-WindowsOptionalFeature` 确认 Hyper-V 与 VBS 的实际共存状态；对照"仅启动 VMware 时"是否复现。 |

### ② VBS/HVCI（内存完整性）与第三方内核驱动的交互（次嫌）

| 项 | 内容 |
|---|---|
| 支持证据 | `VirtualizationBasedSecurityStatus=2` + `SecurityServicesRunning=2` 已确证；HVCI 会对内核代码页执行严格完整性校验，**旧的/异常的第三方内核驱动易被拒或引发 hypervisor 级致命条件**——本机恰有 **加载失败的内核驱动** `bootsafe`/`dam`/`e`/`kavbootc`/`klim6`（卡巴斯基残件，`id=7026`）+ 在跑的 `sysdiag`（火绒）。 |
| 反驳/弱点 | "HVCI 捕获违规并升级为 `0x20001`"**未见公开文档背书**（时间线报告自己也标注为"推断"）。7026 是**加载失败**，失败者通常在故障链外。 |
| 验证方法 | `Get-CimInstance -ClassName Win32_DeviceGuard -Namespace root\Microsoft\Windows\DeviceGuard` 复核 CodeIntegrity 策略；dump 看是否含 `ci.dll`/`securekernel`/`hvix64` 相关栈；临时关闭内存完整性做对照（**需还原点**，见 §4.2）。 |

### ③ Intel 平台 VT-x/微码 或 内存/电源状态转换（硬件侧，不可排除）

| 项 | 内容 |
|---|---|
| 支持证据 | 公开案例中 `0x20001` 有相当比例落在 **CPU/BIOS 组合**与**空闲/电源状态转换**上（[HP 论坛案](https://h30434.www3.hp.com/t5/Notebooks-Archive-Read-Only/HYPERVISOR-ERROR-20001-and-restartes-or-freezing-HP-ZBook-8/td-p/9638520) 明确"非负载下发生"）；本机崩溃时间（17:11）与"非活跃时刻"相符。崩溃发生于**空闲/低负载**而非重载，正是此类的特征。 |
| 反驳/弱点 | **无 WHEA、无内存报错**，削弱硬件说；32GB 内存未做过完整 MemTest 只能算"未证伪"。 |
| 验证方法 | BIOS 更新到最新 + 关闭 XMP/EXPO 跑 MemTest86 至少 4 轮；dump 若落在 `hvix64` 而无第三方驱动栈，则此项显著升位。 |

**已排除项（与 A/B 一致，采信）**：NVIDIA/TDR/显存、游戏、JoyAI、磁盘。**无 TDR、无 `nvlddmkm`、无 WHEA、显存未触顶、磁盘 Healthy。**

---

## 4. 行动方案

### 4.1 立即行动（今天，零风险，只读/可逆）

**第 0 步——保住证据（最高优先，dump 会被轮转覆盖）**
```powershell
mkdir D:\bsod-forensics -Force
copy "C:\Windows\Minidump\092026-16046-01.dmp" "D:\bsod-forensics\"
copy "C:\Windows\MEMORY.DMP" "D:\bsod-forensics\" -ErrorAction SilentlyContinue
wevtutil epl System    D:\bsod-forensics\System.evtx
wevtutil epl Application D:\bsod-forensics\Application.evtx
```

**第 1 步——判定性解析（唯一能定论的动作，只读）**
```powershell
winget install --id Microsoft.WinDbg -e --accept-package-agreements --accept-source-agreements
# 交互式，看 MODULE_NAME / IMAGE_NAME / FAILURE_BUCKET_ID / STACK_TEXT：
windbg -z D:\bsod-forensics\092026-16046-01.dmp -c "!analyze -v; lm kv; !vm; q"
# 命令行版（无 GUI）：
cd "C:\Program Files (x86)\Windows Kits\10\Debuggers\x64"
.\kd.exe -z D:\bsod-forensics\092026-16046-01.dmp -c "!analyze -v; q" | Tee-Object D:\bsod-forensics\analyze.txt
```
**判读规则（先定规则，避免事后找理由）**
| `MODULE_NAME` 落点 | 归因 |
|---|---|
| `vmx86` / `hcmon` / `vmnetbridge` / `vmkbd` | **候选① 坐实** → 走 4.2-A |
| `ci` / `securekernel` / `hvix64` / `hvax64` 且无第三方栈 | **候选② 升位** → 走 4.2-B |
| `sysdiag` / `klim6` / `kavbootc` / `bootsafe` | 安全软件残件为主因 → 走 4.2-C |
| `nt` / `hvix64` 且无第三方模块 | 平台/硬件 → 走 4.2-D |

**第 2 步——环境快照（只读，为后续对照定基线）**
```powershell
bcdedit /enum {current}                       # hypervisorlaunchtype 现状
Get-CimInstance -ClassName Win32_DeviceGuard -Namespace root\Microsoft\Windows\DeviceGuard |
  Select-Object -Property VirtualizationBasedSecurityStatus,SecurityServicesRunning,CodeIntegrityPolicyEnforcementStatus
Get-WinEvent -FilterHashtable @{LogName='System';Id=7026} -MaxEvents 20 | Format-List TimeCreated,Message
driverquery /v /fo csv > D:\bsod-forensics\drivers.csv
sc query vmx86 & sc query hcmon & sc query VMnetBridge & sc query sysdiag
systeminfo | Select-String -Pattern 'BIOS','Hyper-V'
```

### 4.2 短期行动（本周，**每步前先建还原点**）

```powershell
# 前置（必做）：创建还原点 + 备份当前 BCD
Checkpoint-Computer -Description "Pre-BSOD-remediation" -RestorePointType MODIFY_SETTINGS
bcdedit /export D:\bsod-forensics\bcd-backup
```

**A. 若坐实 VMware 冲突（主路径，影响最小 → 最大，逐条做、逐条观察）**
```powershell
# A1 停用 VMware 网络/内核驱动，只留 Hyper-V/VBS（先禁用 VMware 桥接）
Disable-NetAdapter -Name "VMware Network Adapter VMnet1" -Confirm:$false   # 名称按实际改
Disable-NetAdapter -Name "VMware Network Adapter VMnet8" -Confirm:$false
# A2 VMware Workstation 升级到最新版（旧版对 VBS 共存支持差）
winget upgrade --id VMware.WorkstationPlayer   # 或官网装最新 Workstation Pro
# A3 二选一降级（★ 会削弱安全性/改变虚拟化能力，需你拍板）
#    选项甲：保留 VBS，卸载 VMware Workstation
#    选项乙：保留 VMware，关闭 Hyper-V 宿主（需要时用 VMware 跑 Linux）
bcdedit /set hypervisorlaunchtype off    # ← 选项乙；会同时关停 WSL2/Docker Desktop/VBS
```

**B. 若指向 VBS/HVCI 交互**
```powershell
# 图形：Windows 安全中心 → 设备安全性 → 内核隔离 → 内存完整性 → 关
# 命令行（改注册表后重启生效）：
reg add "HKLM\SYSTEM\CurrentControlSet\Control\DeviceGuard\Scenarios\HypervisorEnforcedCodeIntegrity" `
  /v Enabled /t REG_DWORD /d 0 /f
```

**C. 若指向安全软件残件（尤其是加载失败的卡巴斯基内核驱动）**
```powershell
# 用官方卸载工具清残留（普通卸载不删内核驱动），清除后复查 7026
# 卡巴斯基：kavremover ；火绒（sysdiag）：确认与 HVCI 的兼容性/升级版本
# 清理后复验加载失败列表为空：
Get-WinEvent -FilterHashtable @{LogName='System';Id=7026} -MaxEvents 5
```

**D. 若指向平台/硬件**
```powershell
# BIOS/微码升级到最新；关闭 XMP；MemTest86 ≥4 轮；随后逐项恢复
```

**收敛准则**：无论走哪条路径，**一次只改一个变量**，改完运行 ≥48h 且覆盖一次空闲时段；复现即回退到还原点，换下一候选。

---

## 5. 最关键的一个验证手段

**用 WinDbg 解析那份 minidump 并读取 `!analyze -v` 的 `MODULE_NAME` / `IMAGE_NAME`。**

理由：`0x20001` 的参数全部 `Reserved`，**公开资料不存在任何可据以归因的外部字典**；本机同时具备 Hyper-V、VMware、Docker、WSL2、VBS/HVCI、两家安全软件内核驱动——**七个嫌疑对象、零个直接证据**。在这一格局下，A 与 B 的分歧本质上是"猜哪一层"，而 dump 是**唯一**能把"猜"变成"读"的物证：它直接给出崩溃时正在执行/被引用的模块，一步即可在候选①②③之间做出取舍（见 §4.1 判读规则）。

**次关键（若 dump 已丢失）**：`%SystemRoot%\LiveKernelReports` 与 `C:\Windows\MEMORY.DMP` 是否存在同批次记录；再不行则按 §4.2 用**受控单变量法**逐个排除——但那是"排除法"，不是"判定"，成本高一个数量级。

---

## 附：本裁定的置信度边界

| 结论 | 置信度 | 依据 |
|---|---|---|
| A 的因果链不成立 | **高**（实质确证） | 14:00 天然对照组（60 次无蓝屏）+ 无 DTS 内核驱动 + 用户态 `0xc0000409` |
| 真因位于 hypervisor 层 | **高** | `0x20001` 定义即 hypervisor 致命错误 |
| 具体元凶 = VMware 冲突 | **中** | 环境同形 + 公开案例同形；**无 dump 证据** |
| `param1=0x26` 的具体含义 | **未查明** | 微软文档标注 Reserved，无公开字典，拒绝编造 |
| 与 NVIDIA/游戏/显存/JoyAI 有关 | **已排除** | 无 TDR、无 WHEA、无游戏崩溃、显存未触顶 |

**下一步不属本裁定范围**：解析 dump 需管理员权限的执行动作，由主理端点执行。
