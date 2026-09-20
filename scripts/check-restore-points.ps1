# Inventory restore points and system-protection state, ASCII output only.
#
# Purpose: Checkpoint-Computer refused with "a restore point was already created
# in the last 1440 minutes". That refusal is harmless IF a usable restore point
# already exists that predates the changes we are about to make. So: list them.
#
# Output: doc\research\restore-points.txt

$ErrorActionPreference = 'Continue'
$out = Join-Path $PSScriptRoot '..\doc\research\restore-points.txt'

"=== Restore point inventory $(Get-Date -Format o) ===" | Out-File $out -Encoding UTF8

"`n--- 1. System Protection status per drive ---" | Out-File $out -Append -Encoding UTF8
try {
    Get-CimInstance -Namespace 'root\default' -ClassName SystemRestore -ErrorAction Stop |
        Select-Object -First 3 | Out-Null
    "  (SystemRestore class reachable)" | Out-File $out -Append -Encoding UTF8
} catch {
    "  SystemRestore class not reachable: $($_.Exception.Message)" | Out-File $out -Append -Encoding UTF8
}

vssadmin list shadowstorage 2>&1 | Out-File $out -Append -Encoding UTF8

"`n--- 2. Existing restore points (Get-ComputerRestorePoint) ---" | Out-File $out -Append -Encoding UTF8
$rps = Get-ComputerRestorePoint -ErrorAction SilentlyContinue
if ($rps) {
    "count: $($rps.Count)" | Out-File $out -Append -Encoding UTF8
    $rps | Sort-Object CreationTime | ForEach-Object {
        $dt = $null
        try { $dt = $_.ConvertToDateTime($_.CreationTime) } catch { }
        "  seq=$($_.SequenceNumber)  time=$dt  desc=$($_.Description)  type=$($_.RestorePointType)"
    } | Out-File $out -Append -Encoding UTF8
} else {
    "  NONE FOUND (or cmdlet unavailable)" | Out-File $out -Append -Encoding UTF8
}

"`n--- 3. SystemRestorePointCreationFrequency setting ---" | Out-File $out -Append -Encoding UTF8
$key = 'HKLM:\Software\Microsoft\Windows NT\CurrentVersion\SystemRestore'
$v = (Get-ItemProperty -Path $key -Name SystemRestorePointCreationFrequency -ErrorAction SilentlyContinue).SystemRestorePointCreationFrequency
if ($null -eq $v) {
    "  not set -> default 1440 minutes" | Out-File $out -Append -Encoding UTF8
} else {
    "  set to: $v minutes" | Out-File $out -Append -Encoding UTF8
}

"`n--- 4. Last restore point age check ---" | Out-File $out -Append -Encoding UTF8
if ($rps) {
    $latest = $rps | Sort-Object CreationTime -Descending | Select-Object -First 1
    $dt = $null
    try { $dt = $latest.ConvertToDateTime($latest.CreationTime) } catch { }
    "  latest: $dt  ($($latest.Description))" | Out-File $out -Append -Encoding UTF8
    if ($dt) {
        $ageMin = [math]::Round(((Get-Date) - $dt).TotalMinutes, 1)
        "  age: $ageMin minutes" | Out-File $out -Append -Encoding UTF8
    }
} else {
    "  no restore points -> the refusal was misleading" | Out-File $out -Append -Encoding UTF8
}

"`n--- 5. BCD export status (independent of restore points) ---" | Out-File $out -Append -Encoding UTF8
"  bcd-backup exists: $(Test-Path 'D:\bsod-forensics\bcd-backup')" | Out-File $out -Append -Encoding UTF8

Write-Output "written to $out"
