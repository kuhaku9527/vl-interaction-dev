# Verify the exact crash timeline and look for WER kernel report contents.
#
# The interim research report claims the AUDIODG crash wave and the BSOD are
# "temporally detached" (crash wave 17:10:59-17:11:38, BSOD blamed at 17:18).
# My reading is that 17:18 is the POST-REBOOT report time and the actual crash
# was at 17:11:24 - i.e. the crash wave and the bugcheck COINCIDE.
#
# Goal: settle this from the event log, and mine the WER kernel report for the
# bugcheck parameters (which may name the culprit module without needing windbg).
#
# ASCII-only (non-ASCII breaks PS parsing without a BOM).

$ErrorActionPreference = 'SilentlyContinue'
$out = Join-Path $PSScriptRoot '..\doc\research\bsod-timeline-verify.txt'

"=== BSOD timeline verification $(Get-Date -Format o) ===" | Out-File $out -Encoding UTF8

"`n--- 1. Exact boot/shutdown markers (Kernel-General/Boot, EventLog) ---" | Out-File $out -Append -Encoding UTF8
$ids = 12,13,6005,6006,6008,41,1001,109,20,27
Get-WinEvent -FilterHashtable @{LogName='System'; Id=$ids; StartTime=(Get-Date '2026-09-20 16:30:00')} -MaxEvents 60 |
    Sort-Object TimeCreated |
    ForEach-Object {
        $m = ($_.Message -replace '\s+', ' ')
        if ($m.Length -gt 110) { $m = $m.Substring(0, 110) }
        "[$($_.TimeCreated.ToString('HH:mm:ss.fff'))] id=$($_.Id) $($_.ProviderName): $m"
    } | Out-File $out -Append -Encoding UTF8

"`n--- 2. AUDIODG crash window: first and last (Application log id=1000) ---" | Out-File $out -Append -Encoding UTF8
$app = Get-WinEvent -FilterHashtable @{LogName='Application'; Id=1000; StartTime=(Get-Date '2026-09-20 16:30:00')} -MaxEvents 2000 |
    Where-Object { $_.Message -match 'AUDIODG' } | Sort-Object TimeCreated
"total AUDIODG crash events: $($app.Count)" | Out-File $out -Append -Encoding UTF8
if ($app.Count -gt 0) {
    "FIRST: $($app[0].TimeCreated.ToString('HH:mm:ss.fff'))" | Out-File $out -Append -Encoding UTF8
    "LAST : $($app[-1].TimeCreated.ToString('HH:mm:ss.fff'))" | Out-File $out -Append -Encoding UTF8
    "`nper-second histogram (top 20):" | Out-File $out -Append -Encoding UTF8
    $app | Group-Object { $_.TimeCreated.ToString('HH:mm:ss') } |
        Sort-Object Name | Select-Object -First 20 |
        ForEach-Object { "  $($_.Name)  x$($_.Count)" } | Out-File $out -Append -Encoding UTF8
    "`nlast 5 events:" | Out-File $out -Append -Encoding UTF8
    $app | Select-Object -Last 5 | ForEach-Object {
        "[$($_.TimeCreated.ToString('HH:mm:ss.fff'))] $(($_.Message -replace '\s+',' ').Substring(0,100))"
    } | Out-File $out -Append -Encoding UTF8
}

"`n--- 3. All events in the 90 seconds spanning the crash (17:10:30 - 17:12:00) ---" | Out-File $out -Append -Encoding UTF8
Get-WinEvent -FilterHashtable @{LogName='System'; StartTime=(Get-Date '2026-09-20 17:10:30'); EndTime=(Get-Date '2026-09-20 17:12:00')} -MaxEvents 200 |
    Sort-Object TimeCreated |
    ForEach-Object {
        $m = ($_.Message -replace '\s+', ' ')
        if ($m.Length -gt 120) { $m = $m.Substring(0, 120) }
        "[$($_.TimeCreated.ToString('HH:mm:ss.fff'))] L=$($_.Level) id=$($_.Id) $($_.ProviderName): $m"
    } | Out-File $out -Append -Encoding UTF8

"`n--- 4. Application log same window ---" | Out-File $out -Append -Encoding UTF8
Get-WinEvent -FilterHashtable @{LogName='Application'; StartTime=(Get-Date '2026-09-20 17:10:30'); EndTime=(Get-Date '2026-09-20 17:12:00')} -MaxEvents 400 |
    Sort-Object TimeCreated |
    Group-Object { "$($_.Id)|$($_.ProviderName)" } |
    Sort-Object Count -Descending |
    ForEach-Object {
        $f = $_.Group[0]
        $m = ($f.Message -replace '\s+', ' ')
        if ($m.Length -gt 90) { $m = $m.Substring(0, 90) }
        "x$($_.Count.ToString().PadLeft(4))  [$($f.TimeCreated.ToString('HH:mm:ss'))] id=$($f.Id) $($f.ProviderName): $m"
    } | Out-File $out -Append -Encoding UTF8

"`n--- 5. WER kernel report (Kernel_20001_*) content ---" | Out-File $out -Append -Encoding UTF8
$werRoots = @("$env:ProgramData\Microsoft\Windows\WER\ReportArchive",
              "$env:ProgramData\Microsoft\Windows\WER\ReportQueue",
              "$env:LOCALAPPDATA\Microsoft\Windows\WER\ReportArchive")
foreach ($r in $werRoots) {
    Get-ChildItem $r -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match 'Kernel|20001|BlueScreen|LiveKernel' } |
        ForEach-Object {
            "DIR: $($_.FullName)  ($($_.LastWriteTime))" | Out-File $out -Append -Encoding UTF8
            Get-ChildItem $_.FullName -File | ForEach-Object {
                "  FILE: $($_.Name)  $($_.Length) bytes"
                if ($_.Name -eq 'Report.wer') {
                    "" 
                    "  ---- Report.wer content ----"
                    Get-Content $_.FullName -ErrorAction SilentlyContinue | ForEach-Object { "  $_" }
                }
            } | Out-File $out -Append -Encoding UTF8
        }
}

"`n--- 6. WER crash report count for AUDIODG (today) ---" | Out-File $out -Append -Encoding UTF8
$cnt = 0
foreach ($r in $werRoots) {
    $cnt += (Get-ChildItem $r -Directory -ErrorAction SilentlyContinue |
             Where-Object { $_.Name -match 'AppCrash_AUDIODG' }).Count
}
"AUDIODG AppCrash report dirs: $cnt" | Out-File $out -Append -Encoding UTF8

"`n--- 7. DTS / SteelSeries services and processes ---" | Out-File $out -Append -Encoding UTF8
Get-Service | Where-Object { $_.Name -match 'DTS|SteelSeries|Sonar|SSG' -or $_.DisplayName -match 'DTS|SteelSeries|Sonar' } |
    Select-Object Name, DisplayName, Status, StartType |
    Format-Table -AutoSize | Out-File $out -Append -Encoding UTF8
Get-Process | Where-Object { $_.Name -match 'DTS|SteelSeries|Sonar|SSG|GG' } |
    Select-Object Name, Id, @{n='MemMB';e={[math]::Round($_.WorkingSet64/1MB,1)}} |
    Format-Table -AutoSize | Out-File $out -Append -Encoding UTF8

"`n--- 8. DTS INF package info ---" | Out-File $out -Append -Encoding UTF8
$infDir = Get-ChildItem 'C:\Windows\System32\DriverStore\FileRepository' -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match 'dtsapo' } | Select-Object -First 3
foreach ($d in $infDir) {
    "DIR: $($d.Name)" | Out-File $out -Append -Encoding UTF8
    Get-ChildItem $d.FullName -File -ErrorAction SilentlyContinue | ForEach-Object {
        "  $($_.Name)  $($_.Length)"
    } | Out-File $out -Append -Encoding UTF8
    $inf = Get-ChildItem $d.FullName -Filter *.inf -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($inf) {
        "  ---- INF: $($inf.Name) (first 80 lines) ----" | Out-File $out -Append -Encoding UTF8
        Get-Content $inf.FullName -TotalCount 80 -ErrorAction SilentlyContinue |
            ForEach-Object { "  $_" } | Out-File $out -Append -Encoding UTF8
    }
}

Write-Output "written to $out"
