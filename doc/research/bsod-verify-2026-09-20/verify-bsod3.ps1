$ErrorActionPreference='SilentlyContinue'; $ProgressPreference='SilentlyContinue'
$out=New-Object System.Collections.Generic.List[string]
function W($s){ $out.Add([string]$s) }
function Msg($e,$n){ $m=($e.Message -replace "`r`n"," " -replace "`n"," "); if($m.Length -gt $n){$m.Substring(0,$n)}else{$m} }

W "=== 1. CPU / board COUNTER-CHECK (brief said Intel; verify) ==="
foreach($p in @(Get-CimInstance Win32_Processor)){ W ("CPU: " + $p.Name + " | Mfr=" + $p.Manufacturer + " | cores=" + $p.NumberOfCores + " | logical=" + $p.NumberOfLogicalProcessors + " | " + $p.AddressWidth + "bit") }
W ("Board: " + (Get-CimInstance Win32_BaseBoard).Product)
W ("BIOS : " + (Get-CimInstance Win32_BIOS).SMBIOSBIOSVersion + " " + (Get-CimInstance Win32_BIOS).ReleaseDate)
W "-- hypervisor arch binary check (hvix64=Intel, hvax64=AMD) --"
foreach($f in @('hvix64.exe','hvax64.exe')){ W ("  " + $f + " exists=" + (Test-Path "C:\Windows\System32\$f")) }

W ""
W "=== 2. VM activity BEFORE crash: Hyper-V-Worker / Compute / Docker / WSL logs ==="
foreach($p in @('Microsoft-Windows-Hyper-V-Worker','Microsoft-Windows-Hyper-V-Compute','Microsoft-Windows-Hyper-V-VMMS','Microsoft-Windows-Hyper-V-Integration')){
  $ev=@(Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName=$p} -MaxEvents 12)
  W ("-- " + $p + " : " + $ev.Count + " events")
  foreach($e in $ev){ W ("   " + $e.TimeCreated.ToString('09-20 HH:mm:ss') + " id=" + $e.Id + " | " + (Msg $e 150)) }
}

W ""
W "=== 3. Any VM/Guest lifecycle right before 17:11 (16:30-17:12) - broad provider sweep ==="
foreach($e in @(Get-WinEvent -FilterHashtable @{LogName='System'; StartTime=[datetime]'2026-09-20 16:30:00'; EndTime=[datetime]'2026-09-20 17:12:30'} | Where-Object { $_.ProviderName -match 'Hyper-V|VmSwitch|VMMS|vmcompute|Lxss|Wsl|Docker|Virtual' })){
  W ($e.TimeCreated.ToString('HH:mm:ss') + " " + $e.ProviderName + " id=" + $e.Id + " | " + (Msg $e 140))
}

W ""
W "=== 4. VMware Workstation logs (vmware.log / vmware-vmx) ==="
foreach($d in @('D:\VMware Workstation17','C:\ProgramData\VMware','D:\Virtual Machines',"$env:USERPROFILE\Documents\Virtual Machines")){
  if(Test-Path $d){ W ("DIR EXISTS: " + $d); foreach($f in @(Get-ChildItem $d -Recurse -Include '*.log','*.vmx','*.vmdk' -ErrorAction SilentlyContinue | Select-Object -First 12)){ W ("   " + $f.FullName + "  " + $f.Length + "  " + $f.LastWriteTime) } }
  else { W ("absent: " + $d) }
}
W "-- VMware install date --"
foreach($k in @('HKLM:\SOFTWARE\WOW6432Node\VMware, Inc.\VMware Workstation','HKLM:\SOFTWARE\VMware, Inc.\VMware Workstation')){
  if(Test-Path $k){ W ((Get-ItemProperty $k | Select-Object ProductVersion,InstallPath,EULA_Accepted,InstallDate | Format-List | Out-String).Trim()) }
}

W ""
W "=== 5. Docker Desktop install date + vhdx sizes (any 8GB virtual disk?) ==="
foreach($k in @('HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Docker Desktop','HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Docker Desktop')){
  if(Test-Path $k){ W ((Get-ItemProperty $k | Select-Object DisplayName,DisplayVersion,InstallLocation,InstallDate | Format-List | Out-String).Trim()) }
}
W "-- search for *.vhdx (Everything, size) --"
$vh = (es -n 25 "*.vhdx" 2>&1 | Out-String)
W $vh

W ""
W "=== 6. Get-PhysicalDisk incl virtual (identify the 8GB Msft Virtual Disk) ==="
foreach($d in @(Get-PhysicalDisk)){ W ("  #" + $d.DeviceId + " " + $d.FriendlyName + " | bus=" + $d.BusType + " | " + [math]::Round($d.Size/1GB,2) + "GB | " + $d.HealthStatus + " | " + $d.MediaType) }
W "-- Disk2 / partition detail --"
foreach($d in @(Get-Disk)){ W ("  Disk" + $d.Number + " " + $d.FriendlyName + " | " + [math]::Round($d.Size/1GB,2) + "GB | bus=" + $d.BusType + " | " + $d.OperationalStatus + " | " + $d.PartitionStyle) }

W ""
W "=== 7. Memory: FULL detail incl. rank / voltage if exposed ==="
foreach($m in @(Get-CimInstance Win32_PhysicalMemory)){ W ($m | Select-Object BankLabel,DeviceLocator,Capacity,Speed,ConfiguredClockSpeed,Manufacturer,PartNumber,SerialNumber,SMBIOSMemoryType,FormFactor | Format-List | Out-String) }
W "-- Win32_MemoryDeviceArray / slot occupancy --"
W ("slots total=" + (Get-CimInstance Win32_PhysicalMemoryArray).MemoryDevices + "  populated=" + @(Get-CimInstance Win32_PhysicalMemory).Count)
W "-- MemoryDiagnostics results --"
foreach($e in @(Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-MemoryDiagnostics-Results'} -MaxEvents 5)){ W ($e.TimeCreated.ToString() + " id=" + $e.Id + " | " + (Msg $e 200)) }
W ("MemoryDiagnostics-Results count = " + @(Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-MemoryDiagnostics-Results'}).Count)

W ""
W "=== 8. Third-party kernel driver census: non-Microsoft, RUNNING right now ==="
$ms = 0; $third = New-Object System.Collections.Generic.List[string]
foreach($d in @(Get-CimInstance Win32_SystemDriver | Where-Object { $_.State -eq 'Running' })){
  $p = $d.PathName -replace '^\\\?\?\\','' -replace '^\\SystemRoot','C:\Windows' -replace '^system32','C:\Windows\system32'
  $co = ''
  if(Test-Path $p){ $co = (Get-Item $p).VersionInfo.CompanyName }
  if($co -notmatch 'Microsoft'){ $third.Add("  " + $d.Name + " | " + $d.StartMode + " | co=" + $co + " | " + $d.PathName) } else { $ms++ }
}
W ("Microsoft-signed running drivers: " + $ms)
W ("NON-Microsoft running drivers: " + $third.Count)
foreach($t in ($third | Sort-Object)){ W $t }

W ""
W "=== 9. Installed AV / security products (WSC) ==="
foreach($k in @(Get-ChildItem 'HKLM:\SOFTWARE\Microsoft\Security Center\Provider\Av' -ErrorAction SilentlyContinue)){ W ($k.PSChildName + " -> " + ((Get-ItemProperty $k.PSPath).DisplayName)) }
foreach($k in @(Get-ChildItem 'HKLM:\SOFTWARE\Microsoft\Windows Defender\Exclusions\Paths' -ErrorAction SilentlyContinue)){ W ("Defender exclusion: " + $k.PSChildName) }

W ""
W "=== 10. Sleep/hibernate + fast startup config (idle-transition angle) ==="
W ((powercfg /a 2>&1 | Out-String).Trim())
$pw = Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power'
W ("HiberbootEnabled (fast startup) = " + $pw.HiberbootEnabled)
W "-- sleep events last 7 days --"
foreach($e in @(Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-Kernel-Power'; Id=42,107,187,506,507} -MaxEvents 20)){ W ($e.TimeCreated.ToString('MM-dd HH:mm:ss') + " id=" + $e.Id + " | " + (Msg $e 140)) }

W ""
W "=== 11. HWiNFO / temp-dir kernel driver + unsigned-ish driver paths ==="
foreach($d in @(Get-CimInstance Win32_SystemDriver)){ if($d.PathName -match 'Users\\|Temp|ProgramData'){ W ("  " + $d.Name + " | state=" + $d.State + " | start=" + $d.StartMode + " | " + $d.PathName) } }

W ""
W "=== 12. Reliability: WER Kernel_20001 report CONTENT ==="
$dir='C:\ProgramData\Microsoft\Windows\WER\ReportArchive\Kernel_20001_35b468f5dd64ee8aefdbd72e2a41b161919cf45_00000000_cab_a47cc72b-0b1d-4ce6-bf84-6a2fffb78a6e'
if(Test-Path $dir){ foreach($f in @(Get-ChildItem $dir -Recurse -File)){ W ("FILE: " + $f.Name + " " + $f.Length); if($f.Extension -in @('.txt','.wer') -or $f.Name -match 'Report'){ Get-Content $f.FullName -ErrorAction SilentlyContinue | Select-Object -First 80 | ForEach-Object { W ("    " + $_) } } } } else { W "report dir not readable/absent" }

W ""
W "=== 13. Minidump header bytes (can we at least read the header?) ==="
try { $fs=[System.IO.File]::Open('C:\Windows\Minidump\092026-16046-01.dmp',[System.IO.FileMode]::Open,[System.IO.FileAccess]::Read,[System.IO.FileShare]::ReadWrite); $b=New-Object byte[] 64; $fs.Read($b,0,64)|Out-Null; $fs.Close(); W ("sig=" + [System.Text.Encoding]::ASCII.GetString($b,0,4) + " hex=" + (($b|ForEach-Object{$_.ToString('x2')}) -join ' ')) } catch { W ("header read FAILED: " + $_.Exception.Message) }

W ""
W "=== END ==="
$dest='D:\AI\workspace\JoyAI-VL-Interaction-main\doc\research\bsod-verify-2026-09-20\verify-bsod-out3.txt'
$out -join "`r`n" | Out-File $dest -Encoding utf8
Write-Host ("WROTE " + $dest + " lines=" + $out.Count)
