$ErrorActionPreference = 'SilentlyContinue'
$ProgressPreference   = 'SilentlyContinue'
$out = New-Object System.Collections.Generic.List[string]
function W($s){ $out.Add([string]$s) }
function Msg($e,$n){ $m = ($e.Message -replace "`r`n"," " -replace "`n"," "); if($m.Length -gt $n){ $m.Substring(0,$n) } else { $m } }

W "=== 1. HOST / OS ==="
W ("Hostname : " + (hostname))
$os = Get-CimInstance Win32_OperatingSystem
W ("OS       : " + $os.Caption + " | Build " + $os.BuildNumber + " | LastBoot " + $os.LastBootUpTime)
W ("UptimeHrs: " + [math]::Round(((Get-Date) - $os.LastBootUpTime).TotalHours,2))
$cs = Get-CimInstance Win32_ComputerSystem
W ("HypervisorPresent: " + $cs.HypervisorPresent + " | Model=" + $cs.Model + " | Mfr=" + $cs.Manufacturer)

W ""
W "=== 2. EVENT LOG RETAINED SPAN ==="
foreach($lg in @('System','Application')){
  $o = Get-WinEvent -LogName $lg -MaxEvents 1 -Oldest
  $n = Get-WinEvent -LogName $lg -MaxEvents 1
  W ("$lg : oldest=" + $o.TimeCreated + "  newest=" + $n.TimeCreated)
}

W ""
W "=== 3. ALL BUGCHECK REPORTS (WER-SystemErrorReporting 1001) in retained System log ==="
$bc = @(Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-WER-SystemErrorReporting'})
W ("COUNT = " + $bc.Count)
foreach($e in $bc){ W ($e.TimeCreated.ToString('yyyy-MM-dd HH:mm:ss.fff') + " | " + (Msg $e 400)) }

W ""
W "=== 4. Kernel-Power 41 (unexpected shutdown) - ALL retained ==="
foreach($e in @(Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-Kernel-Power'; Id=41})){
  $x=[xml]$e.ToXml(); $d=@{}
  foreach($n in $x.Event.EventData.Data){ $d[$n.Name]=$n.'#text' }
  W ($e.TimeCreated.ToString('yyyy-MM-dd HH:mm:ss') + " | BC=" + $d['BugcheckCode'] + " P1=" + $d['BugcheckParameter1'] + " P2=" + $d['BugcheckParameter2'] + " P3=" + $d['BugcheckParameter3'] + " P4=" + $d['BugcheckParameter4'] + " PwrBtn=" + $d['PowerButtonTimestamp'])
}

W ""
W "=== 5. Event 6008 unexpected-shutdown records - ALL retained ==="
foreach($e in @(Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='EventLog'; Id=6008})){
  W ($e.TimeCreated.ToString('yyyy-MM-dd HH:mm:ss') + " | " + (Msg $e 200))
}

W ""
W "=== 6. BOOT MARKERS (Kernel-General 12) - ALL retained ==="
foreach($e in @(Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-Kernel-General'; Id=12})){
  W ($e.TimeCreated.ToString('yyyy-MM-dd HH:mm:ss') + " | " + (Msg $e 120))
}

W ""
W "=== 7. AUDIODG CRASH CENSUS (Application id=1000, WHOLE retained log) ==="
$ap = @(Get-WinEvent -FilterHashtable @{LogName='Application'; Id=1000})
W ("total Application id=1000 events = " + $ap.Count)
$ad = @($ap | Where-Object { $_.Message -match 'AUDIODG' })
W ("AUDIODG subset = " + $ad.Count)
if($ad.Count -gt 0){
  $sorted = $ad | Sort-Object TimeCreated
  W ("FIRST = " + $sorted[0].TimeCreated.ToString('yyyy-MM-dd HH:mm:ss.fff'))
  W ("LAST  = " + $sorted[-1].TimeCreated.ToString('yyyy-MM-dd HH:mm:ss.fff'))
  W "-- per-minute histogram (whole log) --"
  foreach($g in ($ad | Group-Object { $_.TimeCreated.ToString('MM-dd HH:mm') } | Sort-Object Name)){ W ("  " + $g.Name + "  x" + $g.Count) }
  W "-- crash module census --"
  foreach($g in ($ad | Group-Object { if($_.Message -match '错误模块名称:\s*([^，,\s]+)'){$matches[1]}else{'?'} } | Sort-Object Count -Descending)){ W ("  " + $g.Name + " x" + $g.Count) }
}

W ""
W "=== 8. AUDIODG crashes AFTER the reboot (post 17:18:09) ==="
$post = @($ad | Where-Object { $_.TimeCreated -gt [datetime]'2026-09-20 17:18:09' })
W ("post-reboot AUDIODG id=1000 count = " + $post.Count)
foreach($e in ($post | Sort-Object TimeCreated | Select-Object -First 15)){ W ("  " + $e.TimeCreated.ToString('HH:mm:ss.fff')) }

W ""
W "=== 9. 7026 failed-load driver lists, EVERY boot (compare crash vs non-crash boots) ==="
foreach($e in @(Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Service Control Manager'; Id=7026})){
  W ($e.TimeCreated.ToString('yyyy-MM-dd HH:mm:ss') + " | " + (Msg $e 300))
}

W ""
W "=== 10. Suspect driver files present? + vendor identity ==="
foreach($f in @('dtsapo64.dll','dtstech64.dll','hrdevmon.sys','sysdiag.sys','gameflt.sys','hcmon.sys','vmx86.sys','vmnetbridge.sys','vmnetuserif.sys','bootsafe.sys','kavbootc.sys','klim6.sys','klif.sys','dam.sys','e.sys','nvlddmkm.sys','SteelSeries-Sonar-VAD.sys','VfpExt.sys')){
  $p = "C:\Windows\System32\drivers\$f"
  if(Test-Path $p){ $vi=(Get-Item $p).VersionInfo; W ($f + " | " + $vi.CompanyName + " | " + $vi.FileDescription + " | v" + $vi.FileVersion + " | " + (Get-Item $p).LastWriteTime) }
  else { W ($f + " : NOT in System32\drivers") }
}

W ""
W "=== 11. Registry Start/Type for the 7026 drivers (dangling service entries?) ==="
foreach($n in @('bootsafe','dam','e','kavbootc','klim6')){
  $k = "HKLM:\SYSTEM\CurrentControlSet\Services\$n"
  if(Test-Path $k){
    $p = Get-ItemProperty $k
    W ($n + " : Start=" + $p.Start + " Type=" + $p.Type + " ErrorControl=" + $p.ErrorControl + " ImagePath=" + $p.ImagePath)
  } else { W ($n + " : KEY ABSENT") }
}

W ""
W "=== 12. MEMORY POPULATION (channel balance / part-number mixing) ==="
foreach($m in @(Get-CimInstance Win32_PhysicalMemory)){
  W ($m.BankLabel + " | " + $m.DeviceLocator + " | " + [math]::Round($m.Capacity/1GB,0) + "GB | " + $m.Speed + "MHz | cfg=" + $m.ConfiguredClockSpeed + " | " + $m.Manufacturer + " | " + $m.PartNumber + " | SN=" + $m.SerialNumber)
}
$a = Get-CimInstance Win32_PhysicalMemoryArray
W ("Array: slots=" + $a.MemoryDevices + " maxcap=" + [math]::Round($a.MaxCapacityEx/1MB,0) + "GB")

W ""
W "=== 13. VBS / HVCI state ==="
W ((Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\DeviceGuard' | Format-List | Out-String).Trim())
$sc = 'HKLM:\SYSTEM\CurrentControlSet\Control\DeviceGuard\Scenarios\HypervisorEnforcedCodeIntegrity'
if(Test-Path $sc){ W ("HVCI scenario: " + ((Get-ItemProperty $sc | Format-List | Out-String).Trim())) }
try { W ("Win32_DeviceGuard: " + ((Get-CimInstance -Namespace root\Microsoft\Windows\DeviceGuard -ClassName Win32_DeviceGuard | Format-List | Out-String).Trim())) } catch { W "Win32_DeviceGuard: unavailable" }

W ""
W "=== 14. HYPERVISOR / SECURE KERNEL BOOT EVENTS (all retained) ==="
foreach($e in @(Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-Hyper-V-Hypervisor'})){
  W ($e.TimeCreated.ToString('MM-dd HH:mm:ss') + " id=" + $e.Id + " | " + (Msg $e 220))
}
foreach($e in @(Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-IsolatedUserMode'})){
  W ($e.TimeCreated.ToString('MM-dd HH:mm:ss') + " ISOLATED id=" + $e.Id + " | " + (Msg $e 200))
}

W ""
W "=== 15. WHEA HARDWARE ERRORS (all retained) ==="
$wh = @(Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-WHEA-Logger'})
W ("WHEA count = " + $wh.Count)
foreach($e in ($wh | Select-Object -First 10)){ W ($e.TimeCreated.ToString('MM-dd HH:mm:ss') + " id=" + $e.Id + " | " + (Msg $e 200)) }

W ""
W "=== 16. PRE-CRASH WINDOW #2 : ALL System events 17:00:00 - 17:12:00 (09-20) ==="
foreach($e in @(Get-WinEvent -FilterHashtable @{LogName='System'; StartTime=[datetime]'2026-09-20 17:00:00'; EndTime=[datetime]'2026-09-20 17:12:30'})){
  W ($e.TimeCreated.ToString('HH:mm:ss.fff') + " id=" + $e.Id + " " + $e.ProviderName + " | " + (Msg $e 160))
}

W ""
W "=== 17. PRE-CRASH WINDOW #1 : ALL System events 12:55:00 - 13:15:00 (09-20) ==="
foreach($e in @(Get-WinEvent -FilterHashtable @{LogName='System'; StartTime=[datetime]'2026-09-20 12:55:00'; EndTime=[datetime]'2026-09-20 13:15:00'})){
  W ($e.TimeCreated.ToString('HH:mm:ss.fff') + " id=" + $e.Id + " " + $e.ProviderName + " | " + (Msg $e 160))
}

W ""
W "=== 18. ALL System events on 09-20 grouped by hour (density map) ==="
foreach($g in (@(Get-WinEvent -FilterHashtable @{LogName='System'; StartTime=[datetime]'2026-09-20 00:00:00'; EndTime=[datetime]'2026-09-21 00:00:00'}) | Group-Object { $_.TimeCreated.ToString('HH') } | Sort-Object Name)){
  W ("  " + $g.Name + ":00  x" + $g.Count)
}

W ""
W "=== 19. THERMAL / POWER-TRANSITION / IDLE events last 2 days ==="
foreach($e in @(Get-WinEvent -FilterHashtable @{LogName='System'; StartTime=(Get-Date).AddDays(-2)} | Where-Object { $_.ProviderName -match 'Thermal|Kernel-Processor-Power|Kernel-Power' -and $_.Id -in @(37,38,39,42,107,109,131,132,133,187,203) })){
  W ($e.TimeCreated.ToString('MM-dd HH:mm:ss') + " " + $e.ProviderName + " id=" + $e.Id + " | " + (Msg $e 180))
}

W ""
W "=== 20. VMware / hcmon / VMnet / VmSwitch events last 2 days ==="
foreach($e in @(Get-WinEvent -FilterHashtable @{LogName='System'; StartTime=(Get-Date).AddDays(-2)} | Where-Object { $_.ProviderName -match 'hcmon|vmx86|VMnet|VmSwitch' -and $_.ProviderName -notmatch 'VmSwitch' })){
  W ($e.TimeCreated.ToString('MM-dd HH:mm:ss') + " " + $e.ProviderName + " id=" + $e.Id + " | " + (Msg $e 180))
}

W ""
W "=== 21. Non-running boot/system/auto kernel drivers right now ==="
foreach($d in @(Get-CimInstance Win32_SystemDriver | Where-Object { $_.State -ne 'Running' -and $_.StartMode -ne 'Disabled' })){
  W ("  " + $d.Name + " | state=" + $d.State + " | start=" + $d.StartMode + " | " + $d.PathName)
}

W ""
W "=== 22. DUMPS available ==="
W "-- C:\Windows\Minidump --"
foreach($f in @(Get-ChildItem 'C:\Windows\Minidump' -ErrorAction SilentlyContinue)){ W ("  " + $f.Name + "  " + $f.Length + "  " + $f.LastWriteTime) }
if(-not (Test-Path 'C:\Windows\Minidump')){ W "  (directory not readable / absent)" }
W ("MEMORY.DMP: " + (Test-Path 'C:\Windows\MEMORY.DMP'))
if(Test-Path 'C:\Windows\MEMORY.DMP'){ $m=Get-Item 'C:\Windows\MEMORY.DMP'; W ("  " + $m.Length + " bytes, " + $m.LastWriteTime) }
W "-- LiveKernelReports (WATCHDOG dumps) --"
foreach($f in @(Get-ChildItem 'C:\Windows\LiveKernelReports' -Recurse -File -ErrorAction SilentlyContinue)){ W ("  " + $f.FullName + "  " + $f.Length + "  " + $f.LastWriteTime) }

W ""
W "=== 23. CrashControl / dump configuration ==="
W ((Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\CrashControl' | Select-Object CrashDumpEnabled,AutoReboot,DebugInfoType,DumpFile,MinidumpDir,MinidumpsCount,AlwaysKeepMemoryDump,IgnorePagefileSize,Overwrite | Format-List | Out-String).Trim())

W ""
W "=== 24. GPU / display driver ==="
foreach($v in @(Get-CimInstance Win32_VideoController)){ W ($v.Name + " | driver=" + $v.DriverVersion + " | date=" + $v.DriverDate + " | status=" + $v.Status) }
foreach($e in @(Get-WinEvent -FilterHashtable @{LogName='System'; StartTime=(Get-Date).AddDays(-3)} | Where-Object { $_.ProviderName -match 'nvlddmkm|Display|Kernel-PnP' -and $_.Id -in @(219,4101,4102,13,14) })){ W ($e.TimeCreated.ToString('MM-dd HH:mm:ss') + " " + $e.ProviderName + " id=" + $e.Id + " | " + (Msg $e 160)) }

W ""
W "=== 25. Software versions (virtualization + audio + security stack) ==="
$paths = @(
 'C:\Program Files (x86)\VMware\VMware Workstation\vmware.exe',
 'C:\Program Files\Docker\Docker\Docker Desktop.exe',
 'C:\Program Files\NVIDIA Corporation\NVIDIA app\CEF\NVIDIA app.exe',
 'C:\Program Files\SteelSeries\GG\SteelSeriesGG.exe'
)
foreach($p in $paths){ if(Test-Path $p){ $vi=(Get-Item $p).VersionInfo; W ($p + " -> v" + $vi.FileVersion) } else { W ($p + " : absent") } }
W ((Get-ItemProperty 'HKLM:\SOFTWARE\WOW6432Node\VMware, Inc.\VMware Workstation' | Select-Object ProductVersion,InstallPath | Format-List | Out-String).Trim())
foreach($e in @(Get-WinEvent -FilterHashtable @{LogName='System'; StartTime=(Get-Date).AddDays(-2)} | Where-Object { $_.ProviderName -match 'sysdiag|hrdevmon|VfpExt|gameflt' })){ W ($e.TimeCreated.ToString('MM-dd HH:mm:ss') + " " + $e.ProviderName + " id=" + $e.Id + " | " + (Msg $e 160)) }

W ""
W "=== 26. Application log: non-AUDIODG id=1000 crashes (whole retained log) ==="
foreach($g in (@($ap | Where-Object { $_.Message -notmatch 'AUDIODG' }) | Group-Object { if($_.Message -match '错误应用程序名称:\s*([^，,\s]+)'){$matches[1]}else{'?'} } | Sort-Object Count -Descending | Select-Object -First 25)){ W ("  " + $g.Name + " x" + $g.Count) }

W ""
W "=== 27. Secure Boot / TPM state ==="
try { W ("SecureBoot enabled: " + (Confirm-SecureBootUEFI)) } catch { W "Confirm-SecureBootUEFI: unavailable" }
foreach($e in @(Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-TPM-WMI'} | Select-Object -First 8)){ W ($e.TimeCreated.ToString('MM-dd HH:mm:ss') + " id=" + $e.Id + " | " + (Msg $e 180)) }

W ""
W "=== END ==="

$dest = 'D:\AI\workspace\JoyAI-VL-Interaction-main\doc\research\bsod-verify-2026-09-20\verify-bsod-out.txt'
$out -join "`r`n" | Out-File -FilePath $dest -Encoding utf8
Write-Host ("WROTE " + $dest + "  lines=" + $out.Count)
