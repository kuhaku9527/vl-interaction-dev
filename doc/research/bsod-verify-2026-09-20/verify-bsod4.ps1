$ErrorActionPreference='SilentlyContinue'; $ProgressPreference='SilentlyContinue'
$out=New-Object System.Collections.Generic.List[string]
function W($s){ $out.Add([string]$s) }
function Msg($e,$n){ $m=($e.Message -replace "`r`n"," " -replace "`n"," "); if($m.Length -gt $n){$m.Substring(0,$n)}else{$m} }

W "=== 1. Where is VMware actually installed? (registry says D:\VMware Workstation17\) ==="
W "-- Everything search vmware --"
W ((es -n 30 "vmware" 2>&1 | Out-String).Trim())
W "-- vmware-vmx.exe locations --"
W ((es -n 10 "vmware-vmx.exe" 2>&1 | Out-String).Trim())
W "-- D: root --"
foreach($f in @(Get-ChildItem 'D:\' -ErrorAction SilentlyContinue)){ W ("  D:\" + $f.Name + "  " + $f.LastWriteTime) }

W ""
W "=== 2. VMware service binaries + real ImagePath (what actually loads) ==="
foreach($n in @('vmx86','hcmon','vmci','vsock','VMnetBridge','VMnetuserif','VMnetAdapter','VMware NAT Service','VMnetDHCP','VMAuthdService')){
  $k="HKLM:\SYSTEM\CurrentControlSet\Services\$n"
  if(Test-Path $k){ $p=Get-ItemProperty $k; W ("  " + $n + " | Start=" + $p.Start + " Type=" + $p.Type + " | ImagePath=" + $p.ImagePath) } else { W ("  " + $n + " : KEY ABSENT") }
}
W "-- actual file dates/versions of loaded VMware drivers --"
foreach($f in @('vmx86.sys','hcmon.sys','vmci.sys','vsock.sys','vmnetbridge.sys','vmnetuserif.sys','vmnetadapter.sys','vnetWfp.sys')){
  $p="C:\Windows\System32\drivers\$f"
  if(Test-Path $p){ $i=Get-Item $p; $vi=$i.VersionInfo; W ("  " + $f + " | v" + $vi.FileVersion + " | " + $vi.CompanyName + " | " + $i.LastWriteTime + " | " + $i.Length) } else { W ("  " + $f + " : absent") }
}

W ""
W "=== 3. MSI Afterburner / RTSS / overclocking tools (aggressive OC = hypervisor instability) ==="
foreach($p in @('C:\Program Files (x86)\MSI Afterburner','C:\Program Files\RivaTuner Statistics Server','C:\Program Files\AMD\CNext\CNext','C:\Program Files\AMD')){
  W ("  " + $p + " exists=" + (Test-Path $p))
}
foreach($d in @(Get-CimInstance Win32_SystemDriver | Where-Object { $_.Name -match 'afterburner|RTCore|rtss|AMD|amdk|WinRing|NTIOLib|inpout|EneIo|EneTech' })){ W ("  DRV " + $d.Name + " | state=" + $d.State + " | start=" + $d.StartMode + " | " + $d.PathName) }
foreach($pr in @(Get-Process | Where-Object { $_.ProcessName -match 'Afterburner|RTSS|RivaTuner|AMD|Ryzen|iCUE|MSI|Aorus|Armoury|Xtu|Throttle' })){ W ("  PROC " + $pr.ProcessName + " pid=" + $pr.Id) }

W ""
W "=== 4. Update history detail: when did KB5126256 land vs the 09:43 boot ==="
foreach($h in @(Get-HotFix | Sort-Object InstalledOn -Descending | Select-Object -First 6)){ W ("  " + $h.HotFixID + " | InstalledOn=" + $h.InstalledOn + " | " + $h.Description + " | by=" + $h.InstalledBy) }
W "-- CBS / WindowsUpdate events around 09-20 09:00-10:00 --"
foreach($e in @(Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-WindowsUpdateClient'; StartTime=[datetime]'2026-09-19 00:00:00'; EndTime=[datetime]'2026-09-20 18:00:00'})){
  W ("  " + $e.TimeCreated.ToString('MM-dd HH:mm:ss') + " id=" + $e.Id + " | " + (Msg $e 150))
}
W "-- Kernel-PnP / driver install events 09-20 09:00-10:00 (new drivers?) --"
foreach($e in @(Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-Kernel-PnP'; StartTime=[datetime]'2026-09-20 09:40:00'; EndTime=[datetime]'2026-09-20 10:00:00'})){
  W ("  " + $e.TimeCreated.ToString('HH:mm:ss') + " id=" + $e.Id + " | " + (Msg $e 170))
}

W ""
W "=== 5. Was the 09-14->09-20 period really one continuous uptime? ==="
W "-- Kernel-Power 109 (shutdown init) + 41 all retained --"
foreach($e in @(Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-Kernel-Power'} | Where-Object { $_.Id -in @(41,109) })){
  W ("  " + $e.TimeCreated.ToString('MM-dd HH:mm:ss') + " id=" + $e.Id + " | " + (Msg $e 130))
}
W "-- Kernel-General 13 (OS shutting down) all retained --"
foreach($e in @(Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-Kernel-General'; Id=13})){
  W ("  " + $e.TimeCreated.ToString('MM-dd HH:mm:ss') + " | " + (Msg $e 110))
}

W ""
W "=== 6. Critical: minidump readability re-confirm + archive the file hash attempt ==="
W ("exists=" + (Test-Path 'C:\Windows\Minidump\092026-16046-01.dmp'))
foreach($f in @(Get-ChildItem 'C:\Windows\Minidump' -Force -ErrorAction SilentlyContinue)){ W ("  " + $f.Name + " " + $f.Length + " " + $f.LastWriteTime) }
W ("IsElevated = " + ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator))

W ""
W "=== 7. Safe Mode / boot config (bcdedit) ==="
W ((& "$env:SystemRoot\System32\bcdedit.exe" /enum 2>&1 | Out-String).Trim())

W ""
W "=== 8. Windows GameInput / GameViewer virtual display (Xvdd) identity ==="
foreach($d in @(Get-CimInstance Win32_SystemDriver | Where-Object { $_.Name -match 'Xvdd|VirtualRender|gameflt|GameInput|gvinput' })){ W ("  " + $d.Name + " | state=" + $d.State + " | start=" + $d.StartMode + " | " + $d.PathName) }
foreach($p in @(Get-CimInstance Win32_VideoController)){ W ("  VC: " + $p.Name + " | " + $p.DriverVersion + " | " + $p.PNPDeviceID) }

W ""
W "=== 9. Reliability Monitor: unexpected-shutdown 'bugcode 0' at 13:12 — any preceding hang? ==="
W "-- System log 13:05:00-13:13:00 (right before first crash) --"
foreach($e in @(Get-WinEvent -FilterHashtable @{LogName='System'; StartTime=[datetime]'2026-09-20 13:05:00'; EndTime=[datetime]'2026-09-20 13:13:00'})){
  W ("  " + $e.TimeCreated.ToString('HH:mm:ss.fff') + " id=" + $e.Id + " " + $e.ProviderName + " | " + (Msg $e 150))
}
W "-- Application log 13:05:00-13:13:00 --"
foreach($e in @(Get-WinEvent -FilterHashtable @{LogName='Application'; StartTime=[datetime]'2026-09-20 13:05:00'; EndTime=[datetime]'2026-09-20 13:13:00'})){
  W ("  " + $e.TimeCreated.ToString('HH:mm:ss.fff') + " id=" + $e.Id + " " + $e.ProviderName + " | " + (Msg $e 150))
}

W ""
W "=== END ==="
$dest='D:\AI\workspace\JoyAI-VL-Interaction-main\doc\research\bsod-verify-2026-09-20\verify-bsod-out4.txt'
$out -join "`r`n" | Out-File $dest -Encoding utf8
Write-Host ("WROTE " + $dest + " lines=" + $out.Count)
