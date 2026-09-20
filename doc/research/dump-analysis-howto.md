# 蓝屏 Dump 分析 —— 操作步骤（给用户）

> 我（AI）无法读取 `C:\Windows\Minidump\`（需要管理员权限）。
> 以下步骤需要**你自己用管理员身份**执行。全部命令可直接复制。

---

## 步骤 1：装微软官方命令行调试器（一条命令，约 1 分钟）

**用管理员身份**打开 PowerShell（右键开始菜单 → 「终端(管理员)」或「Windows PowerShell(管理员)」），然后：

```powershell
winget install Microsoft.WinDbg
```

装完后，`cdb.exe` 通常位于：
```
C:\Program Files\WindowsApps\Microsoft.WinDbg_*\amd64\cdb.exe
```
或
```
C:\Program Files (x86)\Windows Kits\10\Debuggers\x64\cdb.exe
```

> 如果你机器上那个「图吧工具箱」的 `windbg.exe` 能用，也可以跳过这步 —— 我的脚本会自动找它。

---

## 步骤 2：跑分析脚本（一条命令）

**保持管理员 PowerShell**，粘贴：

```powershell
powershell -ExecutionPolicy Bypass -File D:\AI\workspace\JoyAI-VL-Interaction-main\scripts\analyze-dump.ps1
```

**它会做这些事**：
- 自动找最新的 dump（`C:\Windows\Minidump\*.dmp`）
- 自动找调试器（先找 `cdb.exe`，找不到就用图吧的 `windbg.exe`）
- 自动下载微软符号（首次会慢几分钟，之后有缓存）
- 执行 `!analyze -v` + 栈回溯 + 模块列表
- 结果写到 `D:\AI\workspace\JoyAI-VL-Interaction-main\doc\research\dump-analysis.txt`

**跑完后告诉我**，我读那个文件给你解读。

---

## 备选：不想跑脚本，手打命令

```powershell
cd "C:\Program Files\WindowsApps\Microsoft.WinDbg_*\amd64"
.\cdb.exe -z C:\Windows\Minidump\092026-16046-01.dmp -y srv*C:\Symbols*https://msdl.microsoft.com/download/symbols -c "!analyze -v; kv; lm kv; q"
```

把输出**全选复制**贴给我即可（或重定向到文件）。

---

## 我要在输出里看什么

| 关键词 | 意义 |
|---|---|
| **`MODULE_NAME`** / **`IMAGE_NAME`** | **最可能的元凶模块**（这是最关键的一行） |
| `FAILURE_BUCKET_ID` | 失败分类（如 `0x20001_..._nvlddmkm!...`） |
| `PROCESS_NAME` | 崩溃时的进程 |
| `STACK_TEXT` | **调用栈**（能看到具体驱动函数） |
| `lm kv` 列表 | 崩溃时加载的内核模块 |

**最关心的**：栈里**有没有 `dtstech64` / DTS / Realtek 音频驱动**，
还是**指向 `nvlddmkm`（NVIDIA）**或别的东西。

---

## 如果 `!analyze -v` 指向的不是音频

那说明我的推断（DTS 音频驱动）**错了**，我会据实修正 ——
这正是要做这一步的原因：**把"很可能"变成"确定"**。

---

## 补充说明

- **需要联网**下载符号（微软官方服务器，只读无需账号）
- 首次分析可能 2–5 分钟（下载符号）；之后有缓存会快
- 不会有副作用，只是读 dump 文件
