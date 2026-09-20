# Verify VMware removal completeness after uninstall.
#
# Why: removing the app does not always remove kernel service registrations.
# A leftover service whose .sys is gone produces a load failure at boot (and
# in the worst case a broken driver entry that HVCI/VBS may scrutinise).
#
# Checks: service registry keys, their ImagePath targets, whether the file
# exists, current driver state, and any remaining load-failure events.
#
# ASCII-only on purpose.

$ErrorActionPreference = 'SilentlyContinue'
$out = Join-Path $PSScriptRoot '..\doc\research\vmware-removal-check.txt'

"=== VMware removal check $(Get-Date -Format o) ===" | Out-File $out -Encoding UTF8

$pat = 'vmx|hcmon|VMnet|VMware|vmci|vsock|vmkbd|vnetWFP|VMparport|vstor2'

"`n--- 1. Service registry entries matching VMware ---" | Out-File $out -Append -Encoding UTF8
$keys = Get-ChildItem 'HKLM:\SYSTEM\CurrentControlSet\Services' |
        Where-Object { $_.PSChildName -match $pat }
if (-not $keys) {
    "  (none)" | Out-File $out -Append -Encoding UTF8
} else {
    foreach ($k in $keys) {
        $p = Get-ItemProperty $k.PSPath
        "SERVICE: $($k.PSChildName)" | Out-File $out -Append -Encoding UTF8
        "  Start    : $($p.Start)   (0=boot 1=system 2=auto 3=manual 4=disabled)" | Out-File $out -Append -Encoding UTF8
        "  Type     : $($p.Type)    (1=kernel driver)" | Out-File $out -Append -Encoding UTF8
        "  ImagePath: $($p.ImagePath)" | Out-File $out -Append -Encoding UTF8
        $img = $p.ImagePath
        if ($img) {
            $clean = $img -replace '^\\\?\?\\', '' -replace '^system32', "$env:SystemRoot\system32" -replace '^\\SystemRoot', $env:SystemRoot
            $clean = [Environment]::ExpandEnvironmentVariables($clean)
            "  FileExists: $(Test-Path $clean)  ($clean)" | Out-File $out -Append -Encoding UTF8
        }
        "" | Out-File $out -Append -Encoding UTF8
    }
}

"`n--- 2. Driver status via sc.exe query ---" | Out-File $out -Append -Encoding UTF8
foreach ($n in @('vmx86','hcmon','VMnetBridge','vsock','vmci','vmnetuserif','VMNET','vmkbd','vstor2-mntapi20-shared')) {
    $r = & sc.exe query $n 2>&1 | Out-String
    if ($r -notmatch 'FAILED 1060') {
        "  $n : $((($r -split "`n") | Where-Object { $_ -match 'STATE' }) -join ' ')" | Out-File $out -Append -Encoding UTF8
    }
}

"`n--- 3. Driver files still on disk ---" | Out-File $out -Append -Encoding UTF8
$found = Get-ChildItem "$env:SystemRoot\System32\drivers" -Filter '*vm*' |
         Where-Object { $_.Name -match $pat }
if (-not $found) { "  (none matching)" | Out-File $out -Append -Encoding UTF8 }
else { $found | ForEach-Object { "  $($_.Name)  $($_.Length) bytes" } | Out-File $out -Append -Encoding UTF8 }

"`n--- 4. Load-failure events since last boot (id 7026 / 7000) ---" | Out-File $out -Append -Encoding UTF8
Get-WinEvent -FilterHashtable @{LogName='System'; Id=7026,7000,7001,7011} -MaxEvents 30 |
    ForEach-Object {
        $m = ($_.Message -replace '\s+', ' ')
        if ($m.Length -gt 150) { $m = $m.Substring(0, 150) }
        "[$($_.TimeCreated)] id=$($_.Id) $($_.ProviderName): $m"
    } | Out-File $out -Append -Encoding UTF8

"`n--- 5. BCD hypervisor launch type ---" | Out-File $out -Append -Encoding UTF8
(& bcdedit /enum '{current}' 2>&1 | Select-String -Pattern 'hypervisorlaunchtype|vsm|vm ') |
    ForEach-Object { "  $_" } | Out-File $out -Append -Encoding UTF8
"  (no hypervisorlaunchtype line means default = Auto)" | Out-File $out -Append -Encoding UTF8

"`n--- 6. Hyper-V / VBS feature state ---" | Out-File $out -Append -Encoding UTF8
foreach ($f in @('Microsoft-Hyper-V-All','VirtualMachinePlatform','Microsoft-Windows-Subsystem-Linux','HypervisorPlatform')) {
    $s = (Get-WindowsOptionalFeature -Online -FeatureName $f -ErrorAction SilentlyContinue).State
    "  $f = $s" | Out-File $out -Append -Encoding UTF8
}
$dg = Get-CimInstance -ClassName Win32_DeviceGuard -Namespace root\Microsoft\Windows\DeviceGuard -ErrorAction SilentlyContinue
"  VBS status        = $($dg.VirtualizationBasedSecurityStatus)" | Out-File $out -Append -Encoding UTF8
"  SecServicesRunning= $($dg.SecurityServicesRunning -join ',')" | Out-File $out -Append -Encoding UTF8

"`n--- 7. Remaining virtualisation / filter drivers of interest ---" | Out-File $out -Append -Encoding UTF8
Get-Service | Where-Object { $_.Name -match 'sysdiag|hr[a-z]|ndisrd|vm|hv' } |
    Select-Object Name, Status, StartType |
    Format-Table -AutoSize | Out-File $out -Append -Encoding UTF8

"`n--- 8. Uptime (to know if a reboot already happened since removal) ---" | Out-File $out -Append -Encoding UTF8
$os = Get-CimInstance Win32_OperatingSystem
"  LastBootUpTime: $($os.LastBootUpTime)" | Out-File $out -Append -Encoding UTF8
"  Now           : $(Get-Date)" | Out-File $out -Append -Encoding UTF8

Write-Output "written to $out"
