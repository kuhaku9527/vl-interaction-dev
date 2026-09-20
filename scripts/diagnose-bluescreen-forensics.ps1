# Focused BSOD forensics around the 2026-09-20 17:18 crash.
#
# Facts already established:
#   - BugCheck 0x20001 (HYPERVISOR_ERROR), param1 = 0x26
#   - Disk error during paging on \Device\Harddisk2\DR2, x4 at 17:18:30
#   - Two unclean shutdowns today: 13:32 (no bugcheck code) and 17:18 (0x20001)
#   - Physical disks: 0 = kimtigo SSD 512GB (C:), 1 = ZHITAI TiPlus7100 1TB (D:, E:)
#   - \Device\Harddisk2 implies a THIRD disk device -> likely a virtual disk (WSL/Docker vhdx)
#
# Goal: confirm what Harddisk2 is, and what happened in the minutes before the crash.
#
# ASCII-only on purpose (non-ASCII breaks PS parsing without a BOM).

$ErrorActionPreference = 'SilentlyContinue'
$out = Join-Path $PSScriptRoot '..\doc\research\bluescreen-forensics.txt'

"=== BSOD forensics $(Get-Date -Format o) ===" | Out-File $out -Encoding UTF8

"`n--- 1. All disk devices (incl. virtual / mounted VHD) ---" | Out-File $out -Append -Encoding UTF8
Get-CimInstance Win32_DiskDrive |
    Select-Object Index, Model, InterfaceType, Size, PNPDeviceID, DeviceID |
    Format-List | Out-File $out -Append -Encoding UTF8

"`n--- 2. Mounted VHD/VHDX (if any) ---" | Out-File $out -Append -Encoding UTF8
Get-Disk | Select-Object Number, FriendlyName, Location, Path, BusType |
    Format-List | Out-File $out -Append -Encoding UTF8

"`n--- 3. Paging configuration detail (where can Windows page?) ---" | Out-File $out -Append -Encoding UTF8
$mm = Get-CimInstance Win32_PageFileSetting
"PageFileSetting count: $($mm.Count)" | Out-File $out -Append -Encoding UTF8
$mm | Format-List | Out-File $out -Append -Encoding UTF8
$pu = Get-CimInstance Win32_PageFileUsage
$pu | Format-List | Out-File $out -Append -Encoding UTF8
"AutomaticManagedPagefile: $((Get-CimInstance Win32_ComputerSystem).AutomaticManagedPagefile)" | Out-File $out -Append -Encoding UTF8

"`n--- 4. Events in the 20 minutes BEFORE the crash (16:58 - 17:19) ---" | Out-File $out -Append -Encoding UTF8
$s = Get-Date '2026-09-20 16:58:00'
$e = Get-Date '2026-09-20 17:19:30'
Get-WinEvent -FilterHashtable @{LogName='System'; StartTime=$s; EndTime=$e} -MaxEvents 200 |
    Sort-Object TimeCreated |
    ForEach-Object {
        $m = ($_.Message -replace '\s+', ' ')
        if ($m.Length -gt 160) { $m = $m.Substring(0, 160) }
        "[$($_.TimeCreated.ToString('HH:mm:ss'))] L=$($_.Level) id=$($_.Id) $($_.ProviderName): $m"
    } | Out-File $out -Append -Encoding UTF8

"`n--- 5. Application log around crash (16:58 - 17:19) ---" | Out-File $out -Append -Encoding UTF8
Get-WinEvent -FilterHashtable @{LogName='Application'; StartTime=$s; EndTime=$e; Level=1,2,3} -MaxEvents 60 |
    Sort-Object TimeCreated |
    ForEach-Object {
        $m = ($_.Message -replace '\s+', ' ')
        if ($m.Length -gt 160) { $m = $m.Substring(0, 160) }
        "[$($_.TimeCreated.ToString('HH:mm:ss'))] L=$($_.Level) id=$($_.Id) $($_.ProviderName): $m"
    } | Out-File $out -Append -Encoding UTF8

"`n--- 6. GPU / display driver events last 2 days ---" | Out-File $out -Append -Encoding UTF8
Get-WinEvent -FilterHashtable @{LogName='System'; StartTime=(Get-Date).AddDays(-2)} |
    Where-Object { $_.ProviderName -match 'nvlddmkm|Display|Dxgkrnl|dxgkrnl' -or $_.Message -match 'nvlddmkm|dxgkrnl' } |
    Select-Object -First 30 |
    ForEach-Object {
        $m = ($_.Message -replace '\s+', ' ')
        if ($m.Length -gt 200) { $m = $m.Substring(0, 200) }
        "[$($_.TimeCreated)] id=$($_.Id) $($_.ProviderName): $m"
    } | Out-File $out -Append -Encoding UTF8

"`n--- 7. WER reports for today (app crashes / GPU) ---" | Out-File $out -Append -Encoding UTF8
$werRoot = "$env:ProgramData\Microsoft\Windows\WER\ReportArchive"
Get-ChildItem $werRoot -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.LastWriteTime -gt (Get-Date).AddDays(-1) } |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 25 |
    ForEach-Object { "$($_.LastWriteTime)  $($_.Name)" } |
    Out-File $out -Append -Encoding UTF8

"`n--- 8. Kernel-Power 41 detail (both crashes today) ---" | Out-File $out -Append -Encoding UTF8
Get-WinEvent -FilterHashtable @{LogName='System'; Id=41} -MaxEvents 6 |
    ForEach-Object {
        "[$($_.TimeCreated)]"
        "  BugcheckCode=$($_.Properties[0].Value) P1=$($_.Properties[1].Value) P2=$($_.Properties[2].Value) P3=$($_.Properties[3].Value) P4=$($_.Properties[4].Value)"
        "  PowerButton=$($_.Properties[5].Value)"
    } | Out-File $out -Append -Encoding UTF8

"`n--- 9. WSL / vmmem state now ---" | Out-File $out -Append -Encoding UTF8
Get-Process -Name 'vmmem','vmmemWSL','wslservice','wsl' -ErrorAction SilentlyContinue |
    Select-Object Name, Id, @{n='WorkingSetGB';e={[math]::Round($_.WorkingSet64/1GB,2)}} |
    Format-Table -AutoSize | Out-File $out -Append -Encoding UTF8

"`n--- 10. Current GPU state ---" | Out-File $out -Append -Encoding UTF8
(nvidia-smi --query-gpu=name,memory.used,memory.total,temperature.gpu,power.draw --format=csv 2>&1 | Out-String) |
    Out-File $out -Append -Encoding UTF8

Write-Output "written to $out"
