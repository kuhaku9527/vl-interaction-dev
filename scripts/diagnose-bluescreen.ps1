"""蓝屏诊断：从 Minidump 与事件日志提取 BugCheck 信息。

本机 PowerShell 输出为 GBK，直接管道会乱码；本脚本写文件后按 GBK 读取。

用法: powershell -NoProfile -ExecutionPolicy Bypass -File scripts/diagnose-bluescreen.ps1
"""
$ErrorActionPreference = 'SilentlyContinue'
$out = "$PSScriptRoot\..\doc\research\bluescreen-diagnosis.txt"

"=== 蓝屏诊断 $(Get-Date -Format o) ===" | Out-File $out -Encoding UTF8

"`n--- 1. Minidump 文件 ---" | Out-File $out -Append -Encoding UTF8
Get-ChildItem C:\Windows\Minidump\*.dmp | Select-Object Name, Length, LastWriteTime |
    Format-Table -AutoSize | Out-File $out -Append -Encoding UTF8

"`n--- 2. BugCheck 事件（最近 5 条）---" | Out-File $out -Append -Encoding UTF8
Get-WinEvent -FilterHashtable @{LogName='System'; Id=1001} -MaxEvents 5 |
    ForEach-Object {
        "TIME: $($_.TimeCreated)"
        $_.Message
        "---"
    } | Out-File $out -Append -Encoding UTF8

"`n--- 3. Kernel-Power 41（非正常关机）最近 5 条 ---" | Out-File $out -Append -Encoding UTF8
Get-WinEvent -FilterHashtable @{LogName='System'; Id=41} -MaxEvents 5 |
    ForEach-Object {
        "TIME: $($_.TimeCreated)"
        "  BugcheckCode: $($_.Properties[0].Value)"
        "  BugcheckParameter1: $($_.Properties[1].Value)"
        "  BugcheckParameter2: $($_.Properties[2].Value)"
        "  BugcheckParameter3: $($_.Properties[3].Value)"
        "  BugcheckParameter4: $($_.Properties[4].Value)"
        "  PowerButtonTimestamp: $($_.Properties[5].Value)"
        "---"
    } | Out-File $out -Append -Encoding UTF8

"`n--- 4. 蓝屏前后的应用/驱动错误（17:00-18:00）---" | Out-File $out -Append -Encoding UTF8
$start = Get-Date '2026-09-20 17:00:00'
$end = Get-Date '2026-09-20 18:00:00'
Get-WinEvent -FilterHashtable @{LogName='System'; StartTime=$start; EndTime=$end; Level=1,2,3} |
    Select-Object -First 40 |
    ForEach-Object {
        "[$($_.TimeCreated)] id=$($_.Id) $($_.ProviderName)"
        "    $(($_.Message -split "`n")[0])"
    } | Out-File $out -Append -Encoding UTF8

"`n--- 5. Display/TDR 相关（显卡驱动超时）---" | Out-File $out -Append -Encoding UTF8
Get-WinEvent -FilterHashtable @{LogName='System'; StartTime=(Get-Date).AddDays(-1)} |
    Where-Object { $_.ProviderName -match 'Display|nvlddmkm|TDR|Graphics' -or $_.Message -match 'nvlddmkm|display driver|TDR' } |
    Select-Object -First 20 |
    ForEach-Object {
        "[$($_.TimeCreated)] id=$($_.Id) $($_.ProviderName)"
        "    $(($_.Message -split "`n")[0])"
    } | Out-File $out -Append -Encoding UTF8

"`n--- 6. 系统信息 ---" | Out-File $out -Append -Encoding UTF8
"显卡驱动: $((Get-CimInstance Win32_VideoController | Select-Object -First 1).DriverVersion)" | Out-File $out -Append -Encoding UTF8
"OS: $((Get-CimInstance Win32_OperatingSystem).Version)" | Out-File $out -Append -Encoding UTF8

Write-Output "written to $out"
