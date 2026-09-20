# Locate an installed VMware product even when winget cannot see it.
#
# Why: winget reported "No installed package found matching input criteria",
# yet the crash dump shows VMware's kernel drivers (vmx86, hcmon, vmnetbridge,
# VMNET, vmnetuserif) were LOADED. So the product is installed but not
# registered in winget's index. Find it via the registry instead.
#
# Also inventories the dependencies that make removal risky:
# WSL2 / Docker Desktop / Hyper-V / VBS all rely on the Hyper-V hypervisor,
# so we must know what is actually in use before recommending a path.
#
# Output: doc\research\vmware-inventory.txt   (ASCII only)

$ErrorActionPreference = 'Continue'
$out = Join-Path $PSScriptRoot '..\doc\research\vmware-inventory.txt'

"=== VMware inventory $(Get-Date -Format o) ===" | Out-File $out -Encoding UTF8

"`n--- 1. Uninstall registry entries mentioning VMware ---" | Out-File $out -Append -Encoding UTF8
$paths = @(
    'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
    'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*'
)
$found = @()
foreach ($p in $paths) {
    Get-ItemProperty $p -ErrorAction SilentlyContinue |
        Where-Object { $_.DisplayName -match 'VMware|Workstation|Player' } |
        ForEach-Object {
            $found += $_
        }
}
if ($found.Count -eq 0) {
    "  (none found in Uninstall keys)" | Out-File $out -Append -Encoding UTF8
} else {
    foreach ($f in $found) {
        "  DisplayName    : $($f.DisplayName)" | Out-File $out -Append -Encoding UTF8
        "  DisplayVersion : $($f.DisplayVersion)" | Out-File $out -Append -Encoding UTF8
        "  InstallDate    : $($f.InstallDate)" | Out-File $out -Append -Encoding UTF8
        "  Publisher      : $($f.Publisher)" | Out-File $out -Append -Encoding UTF8
        "  InstallLocation: $($f.InstallLocation)" | Out-File $out -Append -Encoding UTF8
        "  UninstallString: $($f.UninstallString)" | Out-File $out -Append -Encoding UTF8
        "" | Out-File $out -Append -Encoding UTF8
    }
}

"`n--- 2. VMware services (state + start type) ---" | Out-File $out -Append -Encoding UTF8
Get-Service -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match 'vmx|VMware|VMnet|VMAuthd|vmware' -or $_.DisplayName -match 'VMware' } |
    Select-Object Name, DisplayName, Status, StartType |
    Format-Table -AutoSize | Out-File $out -Append -Encoding UTF8

"`n--- 3. Active VMware processes ---" | Out-File $out -Append -Encoding UTF8
$procs = Get-Process -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match 'vmware|vmx|vmnat|vmnet' }
if ($procs) {
    $procs | Select-Object Name, Id, @{n='MemMB';e={[math]::Round($_.WorkingSet64/1MB,1)}} |
        Format-Table -AutoSize | Out-File $out -Append -Encoding UTF8
} else {
    "  (no VMware processes running)" | Out-File $out -Append -Encoding UTF8
}

"`n--- 4. VMware kernel driver files present ---" | Out-File $out -Append -Encoding UTF8
$drvNames = 'vmx86','hcmon','vmnetbridge','vmnet','vmnetuserif','vmkbd','vmusb','vmci'
foreach ($d in $drvNames) {
    $f = "C:\Windows\System32\drivers\$d.sys"
    if (Test-Path $f) {
        $i = Get-Item $f
        "  PRESENT  $d.sys   $($i.Length) bytes   $($i.LastWriteTime)" | Out-File $out -Append -Encoding UTF8
    } else {
        "  absent   $d.sys" | Out-File $out -Append -Encoding UTF8
    }
}

"`n--- 5. Driver start types in registry (0=boot 1=system 2=auto 3=manual 4=disabled) ---" | Out-File $out -Append -Encoding UTF8
foreach ($d in $drvNames) {
    $k = "HKLM:\SYSTEM\CurrentControlSet\Services\$d"
    if (Test-Path $k) {
        $st = (Get-ItemProperty $k -Name Start -ErrorAction SilentlyContinue).Start
        $ty = (Get-ItemProperty $k -Name Type -ErrorAction SilentlyContinue).Type
        "  $d : Start=$st Type=$ty" | Out-File $out -Append -Encoding UTF8
    }
}

"`n--- 6. WHAT DEPENDS ON HYPER-V (removal risk assessment) ---" | Out-File $out -Append -Encoding UTF8

"`n  [6a] Windows optional features ---" | Out-File $out -Append -Encoding UTF8
foreach ($feat in 'Microsoft-Hyper-V-All','VirtualMachinePlatform',
                  'Microsoft-Windows-Subsystem-Linux','HypervisorPlatform',
                  'Containers-DisposableClientVM','Microsoft-Hyper-V-Hypervisor') {
    $s = (Get-WindowsOptionalFeature -Online -FeatureName $feat -ErrorAction SilentlyContinue).State
    "    $feat = $s" | Out-File $out -Append -Encoding UTF8
}

"`n  [6b] hypervisorlaunchtype ---" | Out-File $out -Append -Encoding UTF8
(bcdedit /enum '{current}' 2>&1 | Select-String -Pattern 'hypervisorlaunchtype|description|identifier') |
    ForEach-Object { "    $_" } | Out-File $out -Append -Encoding UTF8

"`n  [6c] WSL distros ---" | Out-File $out -Append -Encoding UTF8
(wsl.exe -l -v 2>&1 | Out-String) -split "`r?`n" | Where-Object { $_ } |
    ForEach-Object { "    $_" } | Out-File $out -Append -Encoding UTF8

"`n  [6d] Docker Desktop present? ---" | Out-File $out -Append -Encoding UTF8
"    docker.exe: $(Test-Path 'C:\Program Files\Docker\Docker\Docker Desktop.exe')" | Out-File $out -Append -Encoding UTF8
"    docker service: $((Get-Service -Name com.docker.service -ErrorAction SilentlyContinue).Status)" | Out-File $out -Append -Encoding UTF8

"`n  [6e] VBS / HVCI ---" | Out-File $out -Append -Encoding UTF8
$dg = Get-CimInstance -ClassName Win32_DeviceGuard -Namespace root\Microsoft\Windows\DeviceGuard -ErrorAction SilentlyContinue
"    VirtualizationBasedSecurityStatus = $($dg.VirtualizationBasedSecurityStatus)" | Out-File $out -Append -Encoding UTF8
"    SecurityServicesRunning           = $($dg.SecurityServicesRunning -join ',')" | Out-File $out -Append -Encoding UTF8

"`n  [6f] Windows Sandbox / Credential Guard / WSL running now ---" | Out-File $out -Append -Encoding UTF8
Get-Process -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match 'vmmem|wsl|docker|vmwp|VmComputeAgent' } |
    Select-Object Name, Id, @{n='MemMB';e={[math]::Round($_.WorkingSet64/1MB,1)}} |
    Format-Table -AutoSize | Out-File $out -Append -Encoding UTF8

"`n--- 7. Recent VMware-related install/update activity ---" | Out-File $out -Append -Encoding UTF8
$cut = (Get-Date).AddDays(-90)
Get-ChildItem 'C:\Windows\System32\drivers' -Filter 'vm*.sys' -ErrorAction SilentlyContinue |
    ForEach-Object { "    $($_.Name)  $($_.LastWriteTime)  $($_.Length)" } |
    Out-File $out -Append -Encoding UTF8

Write-Output "written to $out"
