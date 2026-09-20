# 残留驱动核查与清理清单（2026-09-20）

> 每次开机事件 `id=7026`：「以下引导启动或系统启动驱动程序未加载: bootsafe dam e kavbootc klim6」
>
> **⚠️ 重要发现：这 5 个里有一个是微软自己的驱动，不能删。**
> 本清单给出**逐项判定**与**只含安全项的清理命令**。

## 一、逐项核查结果

| 服务名 | ImagePath | 文件存在？ | 签名/归属 | **判定** |
|---|---|---|---|---|
| `bootsafe` | `system32\drivers\bootsafe64_ev.sys` | ❌ 不存在 | —（无文件） | ✅ **可删**（残件） |
| **`dam`** | `system32\drivers\dam.sys` | ✅ **存在** | **Microsoft Windows**（签名 Valid）<br>`FileDescription: DAM Kernel Driver`<br>`CompanyName: Microsoft Corporation`<br>`FileVersion: 10.0.19041.5553` | ❌ **不可删**（微软系统驱动） |
| `e` | `E`（**无有效路径**） | ❌ 不存在 | —（无文件） | ✅ **可删**（损毁注册项） |
| `kavbootc` | `system32\drivers\kavbootc64.sys` | ❌ 不存在 | —（无文件） | ✅ **可删**（卡巴残件） |
| `klim6` | `\SystemRoot\system32\DRIVERS\klim6.sys` | ❌ 不存在 | `DisplayName` 明写<br>**`Kaspersky Anti-Virus NDIS 6 Filter`** | ✅ **可删**（卡巴残件） |

### ★ 为什么 `dam` 必须保留

我的第一份清单把 `dam` 列为"卡巴斯基残件"是**基于命名的猜测**。核实签名后推翻：

```
Signature Status : Valid
Signer           : CN=Microsoft Windows, O=Microsoft Corporation
CompanyName      : Microsoft Corporation
FileDescription  : DAM Kernel Driver
```

**⇒ 它是微软的操作系统组件，删除会破坏系统。**
（我已在报告中把这条风险提示写明；此处是执行层面的确证。）

### 另外 4 个的共同特征
它们**文件都不存在**，但注册项仍标 `Start=1`（System-start）
或 `Start=0`（Boot-start），所以**每次开机都尝试加载并失败** → 产生 `id=7026` 噪音。

**注意**：`dam` 也出现在 `7026` 列表里，但它的**文件存在**——
它出现在该列表的原因**不是"文件缺失"**，而是**它作为 boot/system-start 驱动在本机环境（VBS/HVCI）下未能成功加载**。
**这是另一个待观察项，不是残件问题。**

## 二、清理命令（只含安全项，管理员 PowerShell）

```powershell
# 仅删除「文件确实不存在」的 4 个残件注册项。
# dam 已被显式排除（微软系统驱动）。
foreach ($n in 'bootsafe','e','kavbootc','klim6') {
    sc.exe delete $n
}
```

**复核（应无输出）**：
```powershell
foreach ($n in 'bootsafe','e','kavbootc','klim6') {
    if (Get-Service $n -ErrorAction SilentlyContinue) { "STILL THERE: $n" }
}
```

**确认 dam 仍在（应输出它的状态）**：
```powershell
sc.exe query dam
```

⚠️ **注意**：`sc.exe delete` 对**运行中**的驱动会失败。这 4 个都是 `STOPPED`，所以可以直接删。
若某个报错，说明它被占用，重启后再试。

## 三、清理后的预期

| 项 | 清理前 | 清理后 |
|---|---|---|
| `id=7026` 列表 | `bootsafe dam e kavbootc klim6` | 应只剩 **`dam`**（或为空，视它能否成功加载） |
| 其它 | — | 无副作用 |

## 四、下一步仍是「观察 ≥48 小时」

**蓝屏主因（VMware + Hyper-V/VBS 抢占冲突）的验证**：
- VMware 已从内核彻底移除（文件 + 运行态 + 已重启）
- **判据**：运行 ≥48h 且覆盖至少一次**空闲时段**（本次崩溃在空闲时）
- **若仍复现 `0x20001`** → 主因判定被推翻，转向：
  - **VBS/HVCI × 第三方内核过滤驱动**（`sysdiag` 火绒 + `hr*` 系列 + `ndisrd`）
  - **Intel 平台 / 内存 / 电源**（未做过 MemTest）

## 五、诚实边界
- `bootsafe` / `kavbootc` / `klim6` 属**卡巴斯基残件**：`klim6` 有 `DisplayName` 直接证据；
  `bootsafe`/`kavbootc` 由命名与 ImagePath 推断（与 `klim6` 同批出现，归属一致）
- `e` 的 ImagePath 是字面 `E`（无路径）——属**损毁注册项**，删除无风险
- 这些残件**不是本次蓝屏的原因**（dump 栈未显示它们参与）；清理是**降低变量**，不是修主因
