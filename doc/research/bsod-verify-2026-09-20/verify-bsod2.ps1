$ErrorActionPreference = 'SilentlyContinue'
$ProgressPreference   = 'SilentlyContinue'
$out = New-Object System.Collections.Generic.List[string]
function W($s){ $out.Add([string]$s) }
function Msg($e,$n){ $m = ($e.Message -replace "`r`n"," " -replace "`n"," "); if($m.Length -gt $n){ $m.Substring(0,$n) } else { $m } }

W "=== A. HB / Hypervisor binary versions ==="
foreach($f in @('hvix64.exe','hvax64.exe','securekernel.exe','ntoskrnl.exe','ci.dll')){
  $p="C:\Windows\System32\$f"
  if(Test-Path $p){ $vi=(Get-Item $p).VersionInfo; W ("$f v" + $vi.FileVersion + "  " + (Get-Item $p).LastWriteTime) } else { W "$f absent" }
}

W ""
W "=== B. Recent Windows hotfixes (are we freshly patched?) ==="
foreach($h in @(Get-HotFix | Sort-Object InstalledOn -Descending | Select-Object -First 15)){ W ($h.HotFixID + " | " + $h.InstalledOn + " | " + $h.Description) }

W ""
W "=== C. .wslconfig content ==="
$wc = "$env:USERPROFILE\.wslconfig"
if(Test-Path $wc){ Get-Content $wc | ForEach-Object { W ("  " + $_) } } else { W "  no .wslconfig" }

W ""
W "=== D. WSL / vmware / docker state right now ==="
foreach($n in @('vmware-vmx','vmmem','vmmemWSL','wslservice','Docker Desktop','com.docker.backend','vmware','vmware-authd')){
  $ps = @(Get-Process -Name $n -ErrorAction SilentlyContinue)
  if($ps.Count -gt 0){ foreach($p in $ps){ W ("  RUNNING " + $p.ProcessName + " pid=" + $p.Id + " memMB=" + [math]::Round($p.WorkingSet64/1MB,0) + " started=" + $p.StartTime) } }
  else { W ("  not running: " + $n) }
}
W "-- services --"
foreach($s in @('vmx86','VMnetDHCP','VMware NAT Service','VMAuthdService','LxssManager','WslService','com.docker.service','HvHost','vmcompute')){
  $svc = Get-Service -Name $s -ErrorAction SilentlyContinue
  if($svc){ W ("  " + $s + " | " + $svc.Status + " | " + $svc.StartType) } else { W ("  service absent: " + $s) }
}

W ""
W "=== E. WER reports for vmware / wsl / docker / docker / hyperv (ALL retained) ==="
$arch = 'C:\ProgramData\Microsoft\Windows\WER\ReportArchive'
foreach($d in @(Get-ChildItem $arch -Directory -ErrorAction SilentlyContinue)){
  if($d.Name -match 'vmware|wsl|docker|vmx|Hyper|hvix|vmmem'){ W ("  " + $d.LastWriteTime + "  " + $d.Name) }
}
W "-- all ReportArchive dirs grouped by prefix --"
foreach($g in (@(Get-ChildItem $arch -Directory -ErrorAction SilentlyContinue) | Group-Object { ($_.Name -split '_')[0..1] -join '_' } | Sort-Object Count -Descending | Select-Object -First 30)){ W ("  " + $g.Name + " x" + $g.Count) }

W ""
W "=== F. Application-log errors/hangs in the 3h BEFORE crash (14:00-17:12) ==="
foreach($e in @(Get-WinEvent -FilterHashtable @{LogName='Application'; StartTime=[datetime]'2026-09-20 14:00:00'; EndTime=[datetime]'2026-09-20 17:12:30'} | Where-Object { $_.LevelDisplayName -in @('错误','警告','Error','Warning') -and $_.Message -notmatch 'AUDIODG' })){
  W ($e.TimeCreated.ToString('HH:mm:ss') + " id=" + $e.Id + " " + $e.ProviderName + " | " + (Msg $e 170))
}

W ""
W "=== G. Hyper-V-VmSwitch events 16:00-17:12 (was a VM/switch active?) ==="
foreach($e in @(Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-Hyper-V-VmSwitch'; StartTime=[datetime]'2026-09-20 15:30:00'; EndTime=[datetime]'2026-09-20 17:12:30'})){
  W ($e.TimeCreated.ToString('HH:mm:ss') + " id=" + $e.Id + " | " + (Msg $e 150))
}

W ""
W "=== H. Kernel-ShimEngine / CodeIntegrity / DeviceGuard events last 2 days ==="
foreach($e in @(Get-WinEvent -FilterHashtable @{LogName='System'; StartTime=(Get-Date).AddDays(-2)} | Where-Object { $_.ProviderName -match 'ShimEngine|CodeIntegrity|DeviceGuard|SecureBoot|Kernel-Processor-Power' })){
  W ($e.TimeCreated.ToString('MM-dd HH:mm:ss') + " " + $e.ProviderName + " id=" + $e.Id + " | " + (Msg $e 190))
}

W ""
W "=== I. DRIVER VERIFIER / boot config ==="
W ((bcdedit /enum "{current}" 2>&1 | Out-String).Trim())
W ("verifier running: " + (verifier /querysettings 2>&1 | Out-String).Trim())

W ""
W "=== J. Reliability: app-hang + unexpected shutdown records ==="
foreach($e in @(Get-WinEvent -FilterHashtable @{LogName='Application'; ProviderName='Microsoft-Windows-Winlogon'} -MaxEvents 5)){ W ($e.TimeCreated.ToString('MM-dd HH:mm:ss') + " id=" + $e.Id) }
foreach($e in @(Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-Kernel-Boot'} | Where-Object { $_.Id -in @(20,29,30) })){
  W ($e.TimeCreated.ToString('MM-dd HH:mm:ss') + " Kernel-Boot id=" + $e.Id + " | " + (Msg $e 200))
}

W ""
W "=== K. System log: EVERY event in the 70s window right before the crash (17:10:15 - 17:11:30) ==="
foreach($e in @(Get-WinEvent -FilterHashtable @{LogName='System'; StartTime=[datetime]'2026-09-20 17:10:15'; EndTime=[datetime]'2026-09-20 17:11:30'})){
  W ($e.TimeCreated.ToString('HH:mm:ss.fff') + " id=" + $e.Id + " " + $e.ProviderName)
}
W "(if empty above => System log had NO events in that window)"

W ""
W "=== L. USB / hcmon / hrdevmon / HID driver events (Huorong USB monitor conflict?) ==="
foreach($e in @(Get-WinEvent -FilterHashtable @{LogName='System'; StartTime=(Get-Date).AddDays(-2)} | Where-Object { $_.ProviderName -match 'USB|hid|hcmon' })){
  W ($e.TimeCreated.ToString('MM-dd HH:mm:ss') + " " + $e.ProviderName + " id=" + $e.Id + " | " + (Msg $e 170))
}

W ""
W "=== M. Third-party kernel drivers loaded from non-standard paths (AppData/Temp/root) ==="
foreach($d in @(Get-CimInstance Win32_SystemDriver)){
  if($d.PathName -match 'AppData|Temp|\\Users\\|^\?\\|^[A-Za-z]:\\[^\\]'){ W ("  " + $d.Name + " | state=" + $d.State + " | start=" + $d.StartMode + " | " + $d.PathName) }
}

W ""
W "=== N. RTL/Realtek + audio stack quick check ==="
foreach($s in @('IntcAzAudAddService','RTKVHD64','SteelSeries_Sonar_VAD','HdAudAddService','AudioSrv','AudioEndpointBuilder')){
  $svc = Get-Service -Name $s -ErrorAction SilentlyContinue
  if($svc){ W ("  " + $s + " | " + $svc.Status + " | " + $svc.StartType) } else { W ("  absent: " + $s) }
}

W ""
W "=== O. Page file / commit at crash time context (current) ==="
$mem = Get-CimInstance Win32_OperatingSystem
W ("TotalVisible=" + [math]::Round($mem.TotalVisibleMemorySize/1MB,2) + "GB Free=" + [math]::Round($mem.FreePhysicalMemory/1MB,2) + "GB CommitLimit=" + [math]::Round($mem.TotalVirtualMemorySize/1MB,2) + "GB")

W ""
W "=== P. MSI MS-7C94 board + BIOS + XMP-relevant info ==="
W ((Get-CimInstance Win32_BaseBoard | Select-Object Product,Manufacturer,Version,SerialNumber | Format-List | Out-String).Trim())
W ((Get-CimInstance Win32_BIOS | Select-Object SMBIOSBIOSVersion,ReleaseDate,Manufacturer | Format-List | Out-String).Trim())
W ((Get-CimInstance Win32_Processor | Select-Object Name,MaxClockSpeed,NumberOfCores,NumberOfLogicalProcessors | Format-List | Out-String).Trim())

W ""
W "=== END ==="
$dest = 'D:\AI\workspace\JoyAI-VL-Interaction-main\doc\research\bsod-verify-2026-09-20\verify-bsod-out2.txt'
$out -join "`r`n" | Out-File -FilePath $dest -Encoding utf8
Write-Host ("WROTE " + $dest + " lines=" + $out.Count)
