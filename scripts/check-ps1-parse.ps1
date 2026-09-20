# Parse-check PowerShell scripts with the REAL interpreter (Windows PowerShell 5.1).
# ASCII-only on purpose (PS 5.1 reads BOM-less files as GBK, which breaks Chinese).
param([string[]]$Paths)

$failed = 0
foreach ($p in $Paths) {
    $errs = $null
    $tokens = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile($p, [ref]$tokens, [ref]$errs)
    if ($errs -and $errs.Count -gt 0) {
        $failed++
        "PARSE FAIL: $p"
        $errs | Select-Object -First 4 | ForEach-Object { '    ' + $_.Message }
    } else {
        "PARSE OK  : $p"
    }
}
"---"
"failed = $failed"
