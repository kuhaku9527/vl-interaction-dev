# Determine the ACTUAL post-uninstall state of VMware, without guessing.
#
# Context: the user intended to uninstall VMware Workstation 17.0.0 via MSI,
# but the terminal paste appears to have been mangled (a stray "l.log" fragment)
# so it is UNKNOWN whether msiexec actually ran, is still running, or failed.
#
# This script answers, in order:
#   1. Is an msiexec/uninstall still in progress?
#   2. Does the uninstall log exist, and what does it say?
#   3. Which VMware drivers remain (vs Microsoft Hyper-V's own vm*.sys)?
#   4. Which VMware service registry keys remain?
#   5. Is the MSI product still registered?
#
# CRITICAL DISTINCTION: many vm*.sys belong to MICROSOFT Hyper-V (vmbkmcl,
# vmswitch, vmstorfl, vmsvcext, VmsProxy, vmgencounter, vms3cap). Deleting
# those would break WSL2 / Docker / VBS. Only the 2022-dated ones are VMware's.
#
# Output: doc\research\vmware-poststate.txt   (ASCII only)

$ErrorActionPreference = 'Continue'
$out = Join-Path $PSScriptRoot '..\doc\research\vmware-poststate.txt'

"=== VMware post-uninstall state $(Get-Date -Format o) ===" | Out-File $out -Encoding UTF8

"`n--- 1. Uninstall still running? ---" | Out-File $out -Append -Encoding UTF8
$msi = Get-Process -Name msiexec -ErrorAction SilentlyContinue
if ($msi) {
    $msi | Select-Object Id, StartTime, @{n='MemMB';e={[math]::Round($_.WorkingSet64/1MB,1)}} |
        Format-Table -AutoSize | Out-File $out -Append -Encoding UTF8
} else {
    "  no msiexec process running" | Out-File $out -Append -Encoding UTF8
}

"`n--- 2. Uninstall log ---" | Out-File $out -Append -Encoding UTF8
$log = 'D:\bsod-forensics\vmware-uninstall.log'
if (Test-Path $log) {
    $i = Get-Item $log
    "  log EXISTS: $($i.Length) bytes, modified $($i.LastWriteTime)" | Out-File $out -Append -Encoding UTF8
    "  --- last 40 lines ---" | Out-File $out -Append -Encoding UTF8
    Get-Content $log -Tail 40 -ErrorAction SilentlyContinue |
        Out-File $out -Append -Encoding UTF8
    "  --- key result lines ---" | Out-File $out -Append -Encoding UTF8
    Select-String -Path $log -Pattern 'Installation success|Installation failed|Return value 3|Product: VMware|Removal success|removal' -ErrorAction SilentlyContinue |
        Select-Object -Last 15 |
        ForEach-Object { "    $($_.Line.Trim())" } |
        Out-File $out -Append -Encoding UTF8
} else {
    "  NO LOG -> the msiexec uninstall probably never ran" | Out-File $out -Append -Encoding UTF8
}

"`n--- 3. Driver FILES: VMware vs Microsoft Hyper-V ---" | Out-File $out -Append -Encoding UTF8
$vmwareDrv = 'vmx86','hcmon','vmnet','vmnetadapter','vmnetbridge','vmnetuserif','vmci','vmkbd','vmusb'
$msDrv     = 'vmbkmcl','vmbkmclr','vmswitch','vmstorfl','vmsvcext','VmsProxy','VmsProxyHNic','vmgencounter','vms3cap'

"  [3a] VMware drivers (should disappear after uninstall):" | Out-File $out -Append -Encoding UTF8
foreach ($d in $vmwareDrv) {
    $f = "C:\Windows\System32\drivers\$d.sys"
    if (Test-Path $f) {
        $it = Get-Item $f
        "    STILL PRESENT  $d.sys   $($it.LastWriteTime)" | Out-File $out -Append -Encoding UTF8
    } else {
        "    gone           $d.sys" | Out-File $out -Append -Encoding UTF8
    }
}

"  [3b] Microsoft Hyper-V drivers (MUST stay -- do not touch):" | Out-File $out -Append -Encoding UTF8
foreach ($d in $msDrv) {
    $f = "C:\Windows\System32\drivers\$d.sys"
    if (Test-Path $f) {
        "    intact         $d.sys" | Out-File $out -Append -Encoding UTF8
    } else {
        "    MISSING (!)    $d.sys" | Out-File $out -Append -Encoding UTF8
    }
}

"`n--- 4. VMware service registry keys ---" | Out-File $out -Append -Encoding UTF8
foreach ($d in $vmwareDrv + @('VMnetDHCP','VMware NAT Service','VMUSBArbService','VMnetuserif')) {
    $k = "HKLM:\SYSTEM\CurrentControlSet\Services\$d"
    $exists = Test-Path $k
    if ($exists) {
        $st = (Get-ItemProperty $k -Name Start -ErrorAction SilentlyContinue).Start
        "    PRESENT  $d   Start=$st" | Out-File $out -Append -Encoding UTF8
    } else {
        "    gone     $d" | Out-File $out -Append -Encoding UTF8
    }
}

"`n--- 5. MSI product still registered? ---" | Out-File $out -Append -Encoding UTF8
$code = '{0E992720-1330-4AB3-8155-255F79785535}'
$uninst = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\$code"
$uninst32 = "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\$code"
"  Uninstall key (64-bit view): $(Test-Path $uninst)" | Out-File $out -Append -Encoding UTF8
"  Uninstall key (32-bit view): $(Test-Path $uninst32)" | Out-File $out -Append -Encoding UTF8
$prod = Get-ChildItem 'HKLM:\SOFTWARE\Classes\Installer\Products' -ErrorAction SilentlyContinue |
    Where-Object { (Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue).ProductName -match 'VMware' }
"  Installer\Products entry: $(if ($prod) { 'PRESENT -> ' + $prod.PSChildName } else { 'gone' })" |
    Out-File $out -Append -Encoding UTF8

"`n--- 6. MSI cache still present? ---" | Out-File $out -Append -Encoding UTF8
$msiFile = "C:\Program Files (x86)\Common Files\VMware\InstallerCache\$code.msi"
"  $msiFile : $(Test-Path $msiFile)" | Out-File $out -Append -Encoding UTF8

"`n--- 7. Driver LOAD state right now (loaded vs not) ---" | Out-File $out -Append -Encoding UTF8
foreach ($d in 'vmx86','hcmon','vmci','vmnetbridge','vmnetuserif') {
    $r = (sc.exe query $d 2>&1 | Out-String).Trim()
    $r = ($r -split "`r?`n" | Select-Object -First 4) -join ' | '
    "    $d : $r" | Out-File $out -Append -Encoding UTF8
}

"`n--- 8. VMware processes ---" | Out-File $out -Append -Encoding UTF8
$vp = Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Name -match 'vmware|vmx|vmnat|vmnet' }
if ($vp) { $vp | Select-Object Name, Id | Format-Table -AutoSize | Out-File $out -Append -Encoding UTF8 }
else { "    none running" | Out-File $out -Append -Encoding UTF8 }

Write-Output "written to $out"
