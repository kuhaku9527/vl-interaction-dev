# VMware 移除核查结果与收尾（2026-09-20）

> 用户已卸载 VMware 并重启（`LastBootUpTime = 18:56:23`）。
> 本报告为**核查结论**与**收尾步骤**。

## 一、核查结果：主因已移除 ✅

| 检查项 | 结果 |
|---|---|
| **内核驱动文件** | ✅ **8 个全部删除**：`vmx86.sys` / `hcmon.sys` / `vmnetbridge.sys` / `VMNET.SYS` / `vmnetuserif.sys` / `vmkbd.sys` / `vmci.sys` / `vsock.sys` |
| **运行态加载** | ✅ **零个**（`driverquery` 无任何 `vmx*`/`hcmon`/`vmnet*`/`vsock`/`vmci` 条目） |
| **重启状态** | ✅ **已重启**（`LastBootUpTime = 2026-09-20 18:56:23`，在卸载之后） |
| **磁盘残留** | ✅ `System32\drivers` 下无任何匹配文件 |

**⇒ 与蓝屏主因直接相关的 `vmx86` / `hcmon` 已从内核中彻底移除。**

## 二、待清理项

### ⚠️ 收尾 1：4 个空壳服务注册（低危，建议清）

**现象**：
```
SERVICE: hcmon          ImagePath = (空)   Start = (空)
SERVICE: VMnetBridge    ImagePath = (空)   Start = (空)
SERVICE: vsock          ImagePath = (空)   Start = (空)
```
（`hvsocketcontrol` 是 **Hyper-V 自带**的，**不要删** —— 见下表说明）

**判定**：这三个是卸载残留的**空注册项**——**驱动文件不存在、运行态未加载**，
所以**不会导致崩溃**。但它们会让事件日志产生噪音，且有极小概率在某些
枚举路径上被 HVCI 扫描。

**清理命令（管理员 PowerShell）**：
```powershell
foreach ($svc in 'hcmon','VMnetBridge','vsock') {
    if (Get-Service $svc -ErrorAction SilentlyContinue) {
        sc.exe delete $svc
    }
}
# 复核：应无输出
Get-Service hcmon,VMnetBridge,vsock -ErrorAction SilentlyContinue
```

⚠️ **不要删 `hvsocketcontrol`** —— 它是 Hyper-V 的（`hvsocket` 系列），
WSL2 / Docker 依赖它。

### ⚠️ 收尾 2：卡巴斯基残件驱动每次开机加载失败（建议清）

**现象**（每次开机必现，09:43 / 13:32 / 17:18 / 17:21 / 18:56 五次）：
```
id=7026 Service Control Manager:
  以下引导启动或系统启动驱动程序未加载: bootsafe dam e kavbootc klim6
```

**判定**：`bootsafe` / `kavbootc` / `klim6` 是**卡巴斯基（Kaspersky）残留**，
`dam` 可能是另一个安全软件组件。它们在**每次启动时尝试加载并失败**。

**为什么值得处理**：
- 蓝屏 dump 显示本机 **VBS/HVCI 已启用**（`VsmAvailable=1`）；
  HVCI 对内核驱动的完整性校验较严格
- 加载失败的内核驱动是**已知的不稳定源类别**（虽然本次 dump 未显示它们参与崩溃）

**清理方式**：
```powershell
# 先看清这 5 个服务各自的 ImagePath 与状态
foreach ($n in 'bootsafe','dam','e','kavbootc','klim6') {
    $k = "HKLM:\SYSTEM\CurrentControlSet\Services\$n"
    if (Test-Path $k) {
        $p = Get-ItemProperty $k
        [PSCustomObject]@{
            Name       = $n
            ImagePath  = $p.ImagePath
            FileExists = if ($p.ImagePath) { Test-Path ($p.ImagePath -replace '^\\\?\?\\','' -replace '^\\SystemRoot','C:\Windows') } else { $false }
        }
    }
} | Format-Table -AutoSize
```

**若确认文件不存在**（纯残留），逐个删除：
```powershell
foreach ($n in 'bootsafe','dam','e','kavbootc','klim6') {
    sc.exe delete $n
}
```
**若文件存在**（真的装了卡巴斯基/其他安全软件）：
**用厂商官方卸载工具**（卡巴斯基是 `kavremover`），普通卸载**不会**删内核驱动。

⚠️ **风险提示**：`e` 这个名字太通用，删前**务必确认它的 ImagePath 指向确实不是系统组件**。

### ✅ 无需处理
- **`SSGDIO` 服务启动失败**（`证书已被颁发者直接吊销`）—— 这一条**与蓝屏无关**，
  是某个 OEM 服务的证书问题，每次开机都有，属**既有噪音**。
- **`AzureAttestService` 超时** —— 同上，既有噪音。

## 三、下一步：观察与验证

### 判据（预先写定，避免事后找理由）

**改完必须运行 ≥48 小时，且覆盖至少一次空闲时段**（本次崩溃发生在**空闲**时）。

| 观察项 | 期望 |
|---|---|
| **再次蓝屏 `0x20001`** | **不应再出现**。若出现 → 主因判定被推翻，需重新分析（可回退还原点并上路径 C） |
| 事件 7026 | 应从「每次开机都有」变为**为空**（清理卡巴残件后） |
| 事件 41（非正常关机） | 不应再新增 |

### 若仍复现，下一候选
本报告主因是「VMware + Hyper-V/VBS 抢占冲突」，**dump 是间接证据**（栈里没有 `vmx86` 帧，
因为 hypervisor 错误不经由驱动栈）。若移除后**仍复现**，则说明主因判定错误，
应转向：
- **VBS/HVCI 与第三方内核过滤驱动**（`sysdiag` 火绒 + `hr*` 系列 + `ndisrd`）
- **Intel 平台 / 内存 / 电源状态**（本次崩溃在空闲电源转换时，符合此类特征；
  且**未做过 MemTest**）

## 四、诚实边界

| 结论 | 置信度 |
|---|---|
| VMware 已从内核彻底移除 | **确证**（文件 + 运行态 + 已重启） |
| VMware+Hyper-V 共存是主因 | **高**（间接证据三条合指：hypervisor 自身致命错误 + vmx86/hcmon 在场 + 空闲触发） |
| 移除后不会复现 | **待观察**（≥48h + 覆盖空闲时段） |
| 卡巴残件与本次蓝屏有关 | **未证实**（dump 未见其参与）；清理是**降低变量**，非修主因 |

## 五、复现命令
```powershell
powershell -ExecutionPolicy Bypass -File scripts\check-vmware-removal.ps1
```
原始输出：`doc/research/vmware-removal-check.txt`
