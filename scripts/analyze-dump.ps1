# Analyze the BSOD minidump with the Windows debugging engine.
#
# REQUIRES ADMINISTRATOR: C:\Windows\Minidump is protected, so this script
# must be run from an elevated PowerShell.
#
# It tries, in order:
#   1. cdb.exe  (Windows SDK "Debugging Tools for Windows" - CLI, best)
#   2. windbg.exe -c ... -logo  (GUI, driven non-interactively if CLI absent)
#
# Output -> doc\research\dump-analysis.txt

$ErrorActionPreference = 'Continue'

$dump = Get-ChildItem 'C:\Windows\Minidump\*.dmp' | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $dump) {
    Write-Output 'NO DUMP FOUND in C:\Windows\Minidump'
    exit 1
}

$out = Join-Path $PSScriptRoot '..\doc\research\dump-analysis.txt'
"=== Dump analysis $(Get-Date -Format o) ===" | Out-File $out -Encoding UTF8
"Dump: $($dump.FullName)  ($($dump.Length) bytes, $($dump.LastWriteTime))" | Out-File $out -Append -Encoding UTF8

# Symbols: use the public Microsoft symbol server so the stack resolves to names.
$symPath = 'srv*C:\Symbols*https://msdl.microsoft.com/download/symbols'

function Find-Debugger {
    # 1) cdb.exe anywhere on disk / in SDK locations
    $candidates = @()
    $candidates += Get-ChildItem 'C:\Program Files (x86)\Windows Kits\*\Debuggers\x64\cdb.exe' -ErrorAction SilentlyContinue
    $candidates += Get-ChildItem 'C:\Program Files\Windows Kits\*\Debuggers\x64\cdb.exe' -ErrorAction SilentlyContinue
    $candidates += Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WindowsApps\cdb.exe" -ErrorAction SilentlyContinue
    $candidates += Get-ChildItem "$env:ProgramFiles\WindowsApps\Microsoft.WinDbg*\amd64\cdb.exe" -ErrorAction SilentlyContinue
    $found = $candidates | Select-Object -First 1
    if ($found) { return $found.FullName }
    return $null
}

$cdb = Find-Debugger

if ($cdb) {
    "Debugger: $cdb" | Out-File $out -Append -Encoding UTF8
    # Core BSOD commands:
    #   !analyze -v      -> full crash analysis (bugcheck, likely culprit, stack)
    #   lm kv            -> loaded kernel modules
    #   kv               -> stack with parameters
    $cmds = '!analyze -v; .echo ===STACK===; kv; .echo ===MODULES===; lm kv; q'
    & $cdb -z $dump.FullName -y $symPath -c $cmds 2>&1 |
        Out-File $out -Append -Encoding UTF8
    Write-Output "done (cdb) -> $out"
    exit 0
}

# Fallback: the GUI WinDbg can still run commands and write a log.
$wb = @(
    'D:\工具\图吧工具箱202601\tools\其他工具\WinDbg\windbg.exe'
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if ($wb) {
    "Debugger: $wb (GUI, non-interactive log mode)" | Out-File $out -Append -Encoding UTF8
    $log = Join-Path $env:TEMP 'windbg-bsod.log'
    & $wb -z $dump.FullName -y $symPath -logo $log -c '!analyze -v; kv; lm kv; q' 2>&1 | Out-Null
    Start-Sleep -Seconds 5
    if (Test-Path $log) {
        Get-Content $log | Out-File $out -Append -Encoding UTF8
        Write-Output "done (windbg GUI) -> $out"
    } else {
        Write-Output 'windbg produced no log'
    }
    exit 0
}

Write-Output 'NO DEBUGGER FOUND. Install with:'
Write-Output '  winget install Microsoft.WinDbg'
"NO DEBUGGER FOUND" | Out-File $out -Append -Encoding UTF8
exit 2
