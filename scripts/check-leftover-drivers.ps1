# Inspect leftover security-software kernel driver services.
#
# Context: every boot logs id=7026 "the following boot-start or system-start
# drivers did not load: bootsafe dam e kavbootc klim6". These are leftovers
# from Kaspersky and/or another security product. The goal is to classify each
# one as SAFE-TO-DELETE (registration with no file) vs NEEDS-VENDOR-TOOL
# (file still present).
#
# Read-only. ASCII only.

$ErrorActionPreference = 'SilentlyContinue'
$out = Join-Path $PSScriptRoot '..\doc\research\leftover-drivers-check.txt'

"=== Leftover driver services check $(Get-Date -Format o) ===" | Out-File $out -Encoding UTF8

$names = @('bootsafe','dam','e','kavbootc','klim6')

"`n--- 1. Registry entries ---" | Out-File $out -Append -Encoding UTF8
$rows = @()
foreach ($n in $names) {
    $k = "HKLM:\SYSTEM\CurrentControlSet\Services\$n"
    if (Test-Path $k) {
        $p = Get-ItemProperty $k
        $img = $p.ImagePath
        $file = $null
        $exists = $false
        if ($img) {
            $file = $img -replace '^\\\?\?\\', ''
            $file = $file -replace '^\\SystemRoot\\', "$env:SystemRoot\"
            $file = $file -replace '^system32\\', "$env:SystemRoot\system32\"
            $file = [Environment]::ExpandEnvironmentVariables($file)
            $exists = Test-Path $file
        }
        $rows += [PSCustomObject]@{
            Name      = $n
            Start     = $p.Start
            Type      = $p.Type
            ImagePath = $img
            FileExists= $exists
            File      = $file
            DisplayName = $p.DisplayName
        }
    } else {
        $rows += [PSCustomObject]@{
            Name = $n; Start = 'NO-KEY'; Type = ''; ImagePath = ''
            FileExists = $false; File = ''; DisplayName = ''
        }
    }
}
$rows | Format-List | Out-File $out -Append -Encoding UTF8

"`n--- 2. Summary table ---" | Out-File $out -Append -Encoding UTF8
$rows | Format-Table Name,Start,Type,FileExists,ImagePath -AutoSize |
    Out-File $out -Append -Encoding UTF8

"`n--- 3. sc.exe query (current driver state) ---" | Out-File $out -Append -Encoding UTF8
foreach ($n in $names) {
    $r = & sc.exe query $n 2>&1 | Out-String
    if ($r -match 'FAILED 1060') {
        "  $n : NOT INSTALLED (1060)" | Out-File $out -Append -Encoding UTF8
    } else {
        $state = (($r -split "`n") | Where-Object { $_ -match 'STATE|TYPE' }) -join ' | '
        "  $n : $state" | Out-File $out -Append -Encoding UTF8
    }
}

"`n--- 4. Any vendor files left on disk ---" | Out-File $out -Append -Encoding UTF8
$pats = @('*kav*','*klim*','*bootsafe*','*klif*','*kl1*','*kltap*','*dam*.sys')
foreach ($pat in $pats) {
    Get-ChildItem "$env:SystemRoot\System32\drivers" -Filter $pat |
        ForEach-Object { "  $($_.Name)  $($_.Length) bytes  ($($_.LastWriteTime))" } |
        Out-File $out -Append -Encoding UTF8
}
"  (empty above = no vendor driver files found)" | Out-File $out -Append -Encoding UTF8

"`n--- 5. Uninstall entries mentioning Kaspersky / security ---" | Out-File $out -Append -Encoding UTF8
$uninst = @(
  'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
  'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*'
)
foreach ($u in $uninst) {
    Get-ItemProperty $u -ErrorAction SilentlyContinue |
        Where-Object { $_.DisplayName -match 'Kaspersky|卡巴|火绒|Huorong|360|安全' } |
        ForEach-Object { "  $($_.DisplayName)  |  $($_.DisplayVersion)  |  $($_.Publisher)" } |
        Out-File $out -Append -Encoding UTF8
}
"  (empty above = no matching uninstall entries)" | Out-File $out -Append -Encoding UTF8

"`n--- 6. Services still present (hcmon/vmnet/vsock after deletion attempt) ---" | Out-File $out -Append -Encoding UTF8
foreach ($n in @('hcmon','VMnetBridge','vsock','hvsocketcontrol','vmx86')) {
    $k = "HKLM:\SYSTEM\CurrentControlSet\Services\$n"
    "  $n : $(if (Test-Path $k) { 'STILL REGISTERED' } else { 'gone' })" |
        Out-File $out -Append -Encoding UTF8
}

Write-Output "written to $out"
