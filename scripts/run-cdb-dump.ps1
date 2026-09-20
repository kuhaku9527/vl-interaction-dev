# Run the REAL debugger against the preserved kernel minidump.
#
# The dump was already copied to D:\bsod-forensics by the user, so no
# elevation is needed for the file itself.
#
# Uses cdb.exe from the installed WinDbg package. Downloads Microsoft public
# symbols on first run (a few minutes), then caches under C:\Symbols.
#
# Output: doc\research\dump-analysis.txt
#
# ASCII-only on purpose.

$ErrorActionPreference = 'Continue'

$dump = 'D:\bsod-forensics\092026-16046-01.dmp'
if (-not (Test-Path $dump)) {
    Write-Output "dump not found: $dump"
    exit 1
}

$pkg = Get-AppxPackage -Name Microsoft.WinDbg -ErrorAction SilentlyContinue
$cdb = $null
if ($pkg) {
    $cand = Join-Path $pkg.InstallLocation 'amd64\cdb.exe'
    if (Test-Path $cand) { $cdb = $cand }
}
if (-not $cdb) {
    $c = Get-ChildItem 'C:\Program Files (x86)\Windows Kits\*\Debuggers\x64\cdb.exe' -ErrorAction SilentlyContinue |
         Select-Object -First 1
    if ($c) { $cdb = $c.FullName }
}
if (-not $cdb) {
    Write-Output 'cdb.exe not found. Install: winget install Microsoft.WinDbg'
    exit 2
}

$out = Join-Path $PSScriptRoot '..\doc\research\dump-analysis.txt'
$sym = 'srv*C:\Symbols*https://msdl.microsoft.com/download/symbols'

"=== Dump analysis $(Get-Date -Format o) ===" | Out-File $out -Encoding UTF8
"debugger: $cdb" | Out-File $out -Append -Encoding UTF8
"dump    : $dump" | Out-File $out -Append -Encoding UTF8
"symbols : $sym" | Out-File $out -Append -Encoding UTF8
"" | Out-File $out -Append -Encoding UTF8

# Commands, in order of forensic value:
#   !analyze -v   -> MODULE_NAME / IMAGE_NAME / FAILURE_BUCKET_ID (the answer)
#   .bugcheck     -> raw code + params
#   kv            -> stack with frame pointers
#   lm kv m       -> module list (kernel modules, verbose)
#   !vm           -> VM/hypervisor state
#   !pcr          -> processor control region (current thread)
#   q             -> quit
$cmds = '!analyze -v; .bugcheck; kv; lm kv m; !vm; q'

Write-Output "running cdb (symbols download can take a few minutes)..."
& $cdb -z $dump -y $sym -c $cmds 2>&1 | Out-File $out -Append -Encoding UTF8

Write-Output "done -> $out"
