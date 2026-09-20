# Remove ORPHANED kernel-driver service registry keys (leftovers).
#
# WHY sc.exe IS NOT ENOUGH (this is the trap):
#   sc.exe delete talks to the SERVICE DATABASE. These four leftovers are NOT
#   in it any more, so sc.exe fails with "1060: the specified service is not
#   installed" and Get-Service shows nothing. But the REGISTRY KEYS remain
#   under HKLM\SYSTEM\CurrentControlSet\Services with Start=1 (system-start),
#   so the kernel still tries to load them at every boot and logs id=7026.
#   => The keys must be deleted from the registry, not via sc.exe.
#
# SAFETY: 'dam' is deliberately EXCLUDED. It is a MICROSOFT driver
#   (Signer: CN=Microsoft Windows; FileDescription: DAM Kernel Driver) and must
#   survive. Its .sys file exists; the others' files do not.
#
# REQUIRES ADMINISTRATOR. High privilege risk: deleting a wrong key can damage
# boot. The script therefore verifies each name against an explicit allow-list
# and refuses anything else, and it exports a backup before touching anything.
#
# Run: powershell -ExecutionPolicy Bypass -File scripts\cleanup-orphan-drivers.ps1
#      powershell -ExecutionPolicy Bypass -File scripts\cleanup-orphan-drivers.ps1 -WhatIfOnly

param(
    [switch]$WhatIfOnly
)

$ErrorActionPreference = 'Continue'

# --- explicit allow-list: ONLY these may ever be deleted -------------------
$ORPHANS = @('bootsafe', 'e', 'kavbootc', 'klim6')

# --- names that must never be touched --------------------------------------
$PROTECTED = @('dam', 'hvsocketcontrol', 'vmx86', 'hcmon', 'sysdiag')

$base = 'HKLM:\SYSTEM\CurrentControlSet\Services'
$out  = Join-Path $PSScriptRoot '..\doc\research\orphan-driver-cleanup.txt'

"=== Orphan driver key cleanup $(Get-Date -Format o) ===" | Out-File $out -Encoding UTF8
"admin = $((New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator))" | Out-File $out -Append -Encoding UTF8
"WhatIfOnly = $WhatIfOnly" | Out-File $out -Append -Encoding UTF8

if (-not (New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Output 'ERROR: run this in an ADMINISTRATOR PowerShell.'
    "ERROR: not elevated" | Out-File $out -Append -Encoding UTF8
    exit 2
}

# --- 0. backup the whole Services subtree for these names ------------------
$backupDir = Join-Path $PSScriptRoot '..\.cache\driver-key-backup'
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
foreach ($n in ($ORPHANS + $PROTECTED)) {
    $k = Join-Path $base $n
    if (Test-Path $k) {
        $f = Join-Path $backupDir "$n.reg"
        & reg.exe export $k $f /y 2>&1 | Out-Null
        "backed up: $k -> $f" | Out-File $out -Append -Encoding UTF8
    }
}
Write-Output "backup dir: $backupDir"

# --- 1. report current state -----------------------------------------------
"`n--- BEFORE ---" | Out-File $out -Append -Encoding UTF8
foreach ($n in $ORPHANS) {
    $k = Join-Path $base $n
    if (Test-Path $k) {
        $p = Get-ItemProperty $k
        $img = $p.ImagePath
        $file = $null; $exists = $false
        if ($img) {
            $file = $img -replace '^\\\?\?\\','' -replace '^\\SystemRoot\\', "$env:SystemRoot\"
            $file = $file -replace '^system32\\', "$env:SystemRoot\system32\"
            $file = [Environment]::ExpandEnvironmentVariables($file)
            $exists = Test-Path $file
        }
        "  $n : Start=$($p.Start) ImagePath='$img' fileExists=$exists" | Out-File $out -Append -Encoding UTF8
    } else {
        "  $n : (no key)" | Out-File $out -Append -Encoding UTF8
    }
}

# --- 2. refuse to proceed if any file actually EXISTS (would be a real driver)
$blockers = @()
foreach ($n in $ORPHANS) {
    $k = Join-Path $base $n
    if (-not (Test-Path $k)) { continue }
    $img = (Get-ItemProperty $k).ImagePath
    if (-not $img) { continue }
    $file = $img -replace '^\\\?\?\\','' -replace '^\\SystemRoot\\', "$env:SystemRoot\"
    $file = $file -replace '^system32\\', "$env:SystemRoot\system32\"
    $file = [Environment]::ExpandEnvironmentVariables($file)
    if (Test-Path $file) { $blockers += "$n (file EXISTS: $file)" }
}
if ($blockers.Count -gt 0) {
    Write-Output 'REFUSING: these have real driver files on disk, do not blind-delete:'
    $blockers | ForEach-Object { Write-Output "   $_" }
    $blockers | ForEach-Object { "REFUSING: $_" } | Out-File $out -Append -Encoding UTF8
    exit 3
}

# --- 3. delete ------------------------------------------------------------
"`n--- DELETE ---" | Out-File $out -Append -Encoding UTF8
foreach ($n in $ORPHANS) {
    if ($PROTECTED -contains $n) {
        "  SKIP (protected): $n" | Out-File $out -Append -Encoding UTF8
        continue
    }
    $k = Join-Path $base $n
    if (-not (Test-Path $k)) {
        "  already gone: $n" | Out-File $out -Append -Encoding UTF8
        continue
    }
    if ($WhatIfOnly) {
        "  WOULD DELETE: $k" | Out-File $out -Append -Encoding UTF8
        Write-Output "  would delete: $n"
        continue
    }
    try {
        Remove-Item -Path $k -Recurse -Force -ErrorAction Stop
        "  DELETED: $k" | Out-File $out -Append -Encoding UTF8
        Write-Output "  deleted: $n"
    } catch {
        "  FAILED: $k -> $($_.Exception.Message)" | Out-File $out -Append -Encoding UTF8
        Write-Output "  FAILED: $n -> $($_.Exception.Message)"
    }
}

# --- 4. verify ------------------------------------------------------------
"`n--- AFTER ---" | Out-File $out -Append -Encoding UTF8
foreach ($n in ($ORPHANS + 'dam')) {
    $k = Join-Path $base $n
    "  $n : $(if (Test-Path $k) { 'STILL PRESENT' } else { 'gone' })" |
        Out-File $out -Append -Encoding UTF8
}

Write-Output ''
Write-Output 'Verify dam is still present (must be):'
Write-Output "  Test-Path '$base\dam'"
Write-Output ''
Write-Output "log -> $out"
Write-Output 'A REBOOT is required before the id=7026 list reflects the change.'
