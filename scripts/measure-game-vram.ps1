#Requires -Version 5.1
<#
.SYNOPSIS
    Measure GPU VRAM load while a game runs on Windows -- WITHOUT administrator rights.

.DESCRIPTION
    Solves the core problem that `nvidia-smi --query-compute-apps` returns
    "[Insufficient Permissions]" / "N/A" on this machine (non-elevated).

    Two independent data sources are combined, because each one alone is wrong:

      1) nvidia-smi --query-gpu=memory.used  -> ABSOLUTE TRUTH for total VRAM.
         Works fine without admin. Cannot attribute to a process.

      2) Windows performance counters  \GPU Process Memory(pid_N_luid_X)\Dedicated Usage
         -> PER-PROCESS attribution, and these DO work without admin
         (verified on this box; no elevation required).
         CAVEAT: the per-PID counters report *committed* allocations, which
         OVER-COUNT relative to nvidia-smi. Measured on this machine at idle:
             sum(per-PID dedicated, real GPU LUID) = 1508 MiB
             nvidia-smi memory.used                = 1058 MiB
         So use nvidia-smi for absolute numbers and per-PID counters for
         ATTRIBUTION / share / who-moved. Never mix the two scales.

    IMPORTANT: this machine exposes TWO GPU LUIDs:
        0x00000000_0x00018DD3  = the real RTX 5060 Ti
        0x00000000_0x00019E8B  = "Microsoft Basic Render Driver" (software)
    Summing both double-counts nothing but dilutes the game's share, so the
    script auto-detects and keeps only the real adapter.

.PARAMETER Mode
    baseline : sample the idle desktop, then exit. Run this FIRST, alone.
    session  : sample during gameplay. This is the run that matters.
    watch    : short no-file sampling to the console, for a quick sanity check.

.PARAMETER DurationSec
    0 = run until Ctrl+C (recommended for `session`). A value = hard stop.

.PARAMETER GameMatch
    Substring(s) of the game executable name, comma separated, e.g.
    "b1-Win64-Shipping,Client-Win64-Shipping,Fallout4".
    Used to attribute VRAM to the game specifically. If omitted, the script
    auto-detects the process whose VRAM grew the most since the first sample.

.EXAMPLE
    # 1) idle baseline, JoyAI running, game NOT running
    .\measure-game-vram.ps1 -Mode baseline -Label desktop-idle -DurationSec 30

.EXAMPLE
    # 2) during gameplay; Ctrl+C when the run is over
    .\measure-game-vram.ps1 -Mode session -Label wukong-medium-1440p `
        -GameMatch "b1-Win64-Shipping"

.EXAMPLE
    # 3) five-second sanity check, nothing written to disk
    .\measure-game-vram.ps1 -Mode watch -DurationSec 5

.NOTES
    Output: <OutDir>\<Label>-timeseries.csv   (one row per sample, wide)
            <OutDir>\<Label>-perprocess.csv   (one row per process per sample, long)
            <OutDir>\<Label>-summary.json     (peak / steady / attribution)
    Read-only. Starts nothing. Consumes well under 1% CPU and 0 VRAM.
#>
[CmdletBinding()]
param(
    [ValidateSet('baseline', 'session', 'watch')]
    [string]$Mode = 'session',

    # Default resolved after param binding ($PSScriptRoot is not reliably
    # available inside a default value when launched via -File).
    [string]$OutDir = '',

    [int]$DurationSec = 0,

    [int]$IntervalMs = 1000,

    [string]$Label = '',

    [string]$GameMatch = '',

    [int]$WarmupSec = 20
)

$ErrorActionPreference = 'Stop'
$Inv = [System.Globalization.CultureInfo]::InvariantCulture

if (-not $Label) { $Label = "$Mode-$(Get-Date -Format 'yyyyMMdd-HHmmss')" }

if (-not $OutDir) {
    $scriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }
    $OutDir = Join-Path (Split-Path $scriptDir -Parent) 'measure-out'
}

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

function Get-NvidiaSmiPath {
    $cmd = Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($p in @(
            "$env:ProgramFiles\NVIDIA Corporation\NVSMI\nvidia-smi.exe",
            "$env:SystemRoot\System32\nvidia-smi.exe")) {
        if (Test-Path $p) { return $p }
    }
    throw "nvidia-smi.exe not found. Install/repair the NVIDIA driver."
}

function Invoke-NvidiaSmi {
    <# Returns a hashtable of the GPU's instantaneous state. #>
    param([string]$Exe)

    $q = 'memory.used,memory.total,memory.reserved,memory.free,utilization.gpu,temperature.gpu,power.draw'
    $raw = & $Exe --query-gpu=$q --format=csv,noheader,nounits 2>$null
    if (-not $raw) { return $null }
    $parts = ($raw | Select-Object -First 1).ToString().Split(',') | ForEach-Object { $_.Trim() }

    function ToD($s) {
        if ([string]::IsNullOrWhiteSpace($s) -or $s -eq '[N/A]' -or $s -eq 'N/A') { return $null }
        $d = 0.0
        if ([double]::TryParse($s, [System.Globalization.NumberStyles]::Float, $Inv, [ref]$d)) { return $d }
        return $null
    }

    return [ordered]@{
        used_mib     = ToD $parts[0]
        total_mib    = ToD $parts[1]
        reserved_mib = ToD $parts[2]
        free_mib     = ToD $parts[3]
        util_pct     = ToD $parts[4]
        temp_c       = ToD $parts[5]
        power_w      = ToD $parts[6]
    }
}

function Get-RealGpuLuid {
    <#
      The real adapter is the LUID with the highest committed dedicated memory.
      The "Microsoft Basic Render Driver" always sits at ~0.
    #>
    try {
        $s = (Get-Counter -Counter '\GPU Adapter Memory(*)\Dedicated Usage' -MaxSamples 1).CounterSamples
    } catch { return $null }

    $best = $null; $bestVal = -1
    foreach ($c in $s) {
        if ($c.CookedValue -gt $bestVal) { $bestVal = $c.CookedValue; $best = $c.InstanceName }
    }
    return $best
}

function Get-GpuProcessSample {
    <#
      Per-process dedicated VRAM for the real adapter only.
      Returns an array of PSCustomObjects: pid, name, dedicated_mib
    #>
    param([string]$RealLuid)

    $filter = if ($RealLuid) { "*$RealLuid*" } else { '*' }

    try {
        $samples = (Get-Counter -Counter "\GPU Process Memory($filter)\Dedicated Usage" -MaxSamples 1).CounterSamples
    } catch {
        return @()
    }

    $agg = @{}
    foreach ($c in $samples) {
        $inst = $c.InstanceName
        if ($inst -notmatch 'pid_(\d+)') { continue }
        $pidNum = [int]$Matches[1]
        if ($c.CookedValue -le 0) { continue }
        if (-not $agg.ContainsKey($pidNum)) { $agg[$pidNum] = 0.0 }
        $agg[$pidNum] += [double]$c.CookedValue
    }

    $out = foreach ($k in $agg.Keys) {
        $pname = "pid_$k"
        try {
            $p = Get-Process -Id $k -ErrorAction Stop
            $pname = $p.ProcessName
        } catch { }
        [pscustomobject]@{
            pid          = $k
            name         = $pname
            dedicated_mib = [math]::Round($agg[$k] / 1MB, 1)
        }
    }
    return @($out | Sort-Object dedicated_mib -Descending)
}

function Get-SteamVramBudget {
    <#
      WDDM budget via D3DKMTQueryVideoMemoryInfo -- the number Task Manager shows.
      Needs the x64-correct struct layout (hProcess is a HANDLE = 8 bytes).
      Best effort: returns $null if the P/Invoke is unavailable.
    #>
    try {
        if (-not ('WddmBudget' -as [type])) {
            Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class WddmBudget {
    [StructLayout(LayoutKind.Sequential)]
    public struct LUID { public uint LowPart; public int HighPart; }
    [StructLayout(LayoutKind.Sequential)]
    public struct OPEN { public LUID AdapterLuid; public uint hAdapter; }
    [StructLayout(LayoutKind.Sequential)]
    public struct QVM {
        public IntPtr hProcess;
        public uint hAdapter;
        public uint MemorySegmentGroup;
        public ulong Budget;
        public ulong CurrentUsage;
        public ulong CurrentReservation;
        public ulong AvailableForReservation;
        public uint PhysicalAdapterIndex;
    }
    [StructLayout(LayoutKind.Sequential)]
    public struct CLOSE { public uint hAdapter; }
    [DllImport("gdi32.dll")] public static extern int D3DKMTOpenAdapterFromLuid(ref OPEN p);
    [DllImport("gdi32.dll")] public static extern int D3DKMTQueryVideoMemoryInfo(ref QVM p);
    [DllImport("gdi32.dll")] public static extern int D3DKMTCloseAdapter(ref CLOSE p);
    [DllImport("kernel32.dll")] public static extern IntPtr GetCurrentProcess();
    public static ulong[] Query(uint luidLow, int luidHigh) {
        OPEN o = new OPEN();
        o.AdapterLuid.LowPart = luidLow;
        o.AdapterLuid.HighPart = luidHigh;
        if (D3DKMTOpenAdapterFromLuid(ref o) != 0) return null;
        ulong[] res = new ulong[4];
        QVM q = new QVM();
        q.hProcess = GetCurrentProcess();
        q.hAdapter = o.hAdapter;
        q.MemorySegmentGroup = 0;   // Local = VRAM
        if (D3DKMTQueryVideoMemoryInfo(ref q) != 0) {
            CLOSE c0 = new CLOSE(); c0.hAdapter = o.hAdapter; D3DKMTCloseAdapter(ref c0);
            return null;
        }
        res[0] = q.Budget; res[1] = q.CurrentUsage;
        res[2] = q.CurrentReservation; res[3] = q.AvailableForReservation;
        CLOSE c = new CLOSE(); c.hAdapter = o.hAdapter; D3DKMTCloseAdapter(ref c);
        return res;
    }
}
'@
        }
        # Real GPU LUID observed on this machine: 0x00018DD3 / high 0
        $r = [WddmBudget]::Query(0x00018DD3, 0)
        if ($null -eq $r) { return $null }
        return [ordered]@{
            budget_mib      = [math]::Round($r[0] / 1MB, 1)
            current_mib     = [math]::Round($r[1] / 1MB, 1)
            reservation_mib = [math]::Round($r[2] / 1MB, 1)
        }
    } catch { return $null }
}

function Get-Stats {
    param([double[]]$Values)
    $v = @($Values | Where-Object { $null -ne $_ } | Sort-Object)
    if ($v.Count -eq 0) { return $null }
    $median = if ($v.Count % 2 -eq 1) { $v[[int]($v.Count / 2)] }
              else { ($v[$v.Count / 2 - 1] + $v[$v.Count / 2]) / 2 }
    $idx95 = [math]::Min($v.Count - 1, [int][math]::Ceiling($v.Count * 0.95) - 1)
    return [ordered]@{
        n      = $v.Count
        min    = [math]::Round($v[0], 1)
        median = [math]::Round($median, 1)
        p95    = [math]::Round($v[$idx95], 1)
        max    = [math]::Round($v[-1], 1)
        mean   = [math]::Round((($v | Measure-Object -Average).Average), 1)
    }
}

# ---------------------------------------------------------------------------
# start
# ---------------------------------------------------------------------------

$smi = Get-NvidiaSmiPath
$realLuid = Get-RealGpuLuid

Write-Host ''
Write-Host '=== GPU VRAM measurement ===' -ForegroundColor Cyan
Write-Host ("mode          : {0}" -f $Mode)
Write-Host ("label         : {0}" -f $Label)
Write-Host ("nvidia-smi    : {0}" -f $smi)
Write-Host ("real GPU LUID : {0}" -f ($(if ($realLuid) { $realLuid } else { '<auto/all>' })))

$gpu0 = Invoke-NvidiaSmi -Exe $smi
if ($gpu0) {
    Write-Host ("total VRAM    : {0} MiB (reserved {1} MiB)" -f $gpu0.total_mib, $gpu0.reserved_mib)
}
$budget = Get-SteamVramBudget
if ($budget) { Write-Host ("WDDM budget   : {0} MiB" -f $budget.budget_mib) }

$gamePatterns = @()
if ($GameMatch) { $gamePatterns = $GameMatch.Split(',') | ForEach-Object { $_.Trim() } | Where-Object { $_ } }
if ($gamePatterns.Count) { Write-Host ("game match    : {0}" -f ($gamePatterns -join ', ')) }

$writeFiles = ($Mode -ne 'watch')
if ($writeFiles) {
    if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir -Force | Out-Null }
    $OutDir = (Resolve-Path $OutDir).Path
    Write-Host ("output dir    : {0}" -f $OutDir)
}

Write-Host ''
Write-Host 'sampling... (Ctrl+C to stop)' -ForegroundColor DarkGray
Write-Host ''

# ---------------------------------------------------------------------------
# sampling loop
# ---------------------------------------------------------------------------

$rows      = New-Object System.Collections.Generic.List[object]
$perProc   = New-Object System.Collections.Generic.List[object]
$gameMax   = @{}        # pid -> peak MiB
$gameNames = @{}        # pid -> name
$firstProc = @{}        # pid -> first-sample MiB (for auto-detect)
$startTime = Get-Date
$deadline  = if ($DurationSec -gt 0) { $startTime.AddSeconds($DurationSec) } else { $null }
$i = 0

try {
    while ($true) {
        if ($deadline -and (Get-Date) -ge $deadline) { break }

        $ts = Get-Date
        $gpu = Invoke-NvidiaSmi -Exe $smi
        $procs = Get-GpuProcessSample -RealLuid $realLuid

        $procSum = 0.0
        $gameSum = 0.0
        $gamePids = @()
        $topName = ''
        $topVal = -1.0

        foreach ($p in $procs) {
            $procSum += $p.dedicated_mib
            if ($p.dedicated_mib -gt $topVal) { $topVal = $p.dedicated_mib; $topName = $p.name }

            if (-not $firstProc.ContainsKey($p.pid)) { $firstProc[$p.pid] = $p.dedicated_mib }

            $isGame = $false
            if ($gamePatterns.Count) {
                foreach ($g in $gamePatterns) {
                    if ($p.name -like "*$g*") { $isGame = $true; break }
                }
            }
            if ($isGame) {
                $gameSum += $p.dedicated_mib
                $gamePids += $p.pid
                $gameNames[$p.pid] = $p.name
                if (-not $gameMax.ContainsKey($p.pid) -or $p.dedicated_mib -gt $gameMax[$p.pid]) {
                    $gameMax[$p.pid] = $p.dedicated_mib
                }
            }

            $perProc.Add([pscustomobject][ordered]@{
                timestamp        = $ts.ToString('yyyy-MM-dd HH:mm:ss.fff')
                t_sec            = [math]::Round(($ts - $startTime).TotalSeconds, 2)
                pid              = $p.pid
                process          = $p.name
                dedicated_mib    = $p.dedicated_mib
                is_game_match    = $isGame
            })
        }

        $row = [pscustomobject][ordered]@{
            timestamp     = $ts.ToString('yyyy-MM-dd HH:mm:ss.fff')
            t_sec         = [math]::Round(($ts - $startTime).TotalSeconds, 2)
            gpu_used_mib  = $gpu.used_mib
            gpu_free_mib  = $gpu.free_mib
            gpu_total_mib = $gpu.total_mib
            gpu_util_pct  = $gpu.util_pct
            temp_c        = $gpu.temp_c
            power_w       = $gpu.power_w
            proc_sum_mib  = [math]::Round($procSum, 1)
            game_sum_mib  = [math]::Round($gameSum, 1)
            game_pids     = ($gamePids -join '|')
            top_process   = $topName
            top_mib       = [math]::Round([math]::Max($topVal, 0), 1)
        }
        $rows.Add($row)

        # one-line live console readout, overwritten in place
        $line = "{0,7:N0} MiB used | {1,3}% util | proc_sum {2,6:N0} | game {3,6:N0} | {4} {5}" -f `
            [double]$gpu.used_mib, [int]$gpu.util_pct, $procSum, $gameSum, $topName, $topVal
        Write-Host ("`r[{0,4}s] {1}" -f [int]($ts - $startTime).TotalSeconds, $line) -NoNewline

        $i++
        $sleep = $IntervalMs - [int]((Get-Date) - $ts).TotalMilliseconds
        if ($sleep -gt 0) { Start-Sleep -Milliseconds $sleep }
    }
} finally {
    Write-Host ''
    Write-Host ''
}

if ($rows.Count -eq 0) {
    Write-Warning 'No samples collected.'
    return
}

# ---------------------------------------------------------------------------
# summary
# ---------------------------------------------------------------------------

$usedVals = @($rows | ForEach-Object { $_.gpu_used_mib })
$overall  = Get-Stats -Values $usedVals

# steady state = after warmup, ignores load-in spikes
$steadyRows = @($rows | Where-Object { $_.t_sec -ge $WarmupSec })
$steady = if ($steadyRows.Count) { Get-Stats -Values @($steadyRows | ForEach-Object { $_.gpu_used_mib }) } else { $null }

$baseSec = if ($Mode -eq 'baseline') { 0 } else { 0 }
$baselineVals = @($rows | Where-Object { $_.t_sec -lt [math]::Min(5, $WarmupSec) } | ForEach-Object { $_.gpu_used_mib })
$baseline = Get-Stats -Values $baselineVals

$peakRow = $rows | Sort-Object gpu_used_mib -Descending | Select-Object -First 1

# auto-detect the game process if not given: biggest grower vs its first sample
$autoGame = @{}
foreach ($r in $perProc) {
    if ($firstProc.ContainsKey($r.pid)) {
        $delta = $r.dedicated_mib - $firstProc[$r.pid]
        if ($delta -gt 200) {
            if (-not $autoGame.ContainsKey($r.pid) -or $delta -gt $autoGame[$r.pid].delta) {
                $autoGame[$r.pid] = [pscustomobject]@{ name = $r.process; delta = $delta; peak = $r.dedicated_mib }
            }
        }
    }
}

$procPeaks = @($perProc | Group-Object pid | ForEach-Object {
        $peak = ($_.Group | Measure-Object dedicated_mib -Maximum).Maximum
        [pscustomobject]@{
            pid           = [int]$_.Name
            process       = $_.Group[0].process
            peak_mib      = $peak
            first_mib     = $firstProc[[int]$_.Name]
            growth_mib    = [math]::Round($peak - $firstProc[[int]$_.Name], 1)
            is_game_match = [bool]$_.Group[0].is_game_match
        }
    } | Sort-Object peak_mib -Descending)

$summary = [ordered]@{
    label              = $Label
    mode               = $Mode
    started            = $startTime.ToString('yyyy-MM-dd HH:mm:ss')
    duration_sec       = [math]::Round(($rows[-1].t_sec), 1)
    samples            = $rows.Count
    interval_ms        = $IntervalMs
    gpu_total_mib      = $gpu0.total_mib
    gpu_reserved_mib   = $gpu0.reserved_mib
    wddm_budget_mib    = if ($budget) { $budget.budget_mib } else { $null }
    real_gpu_luid      = $realLuid
    warmup_sec         = $WarmupSec
    baseline           = $baseline
    overall            = $overall
    steady_state       = $steady
    peak_total_mib     = $peakRow.gpu_used_mib
    peak_at_sec        = $peakRow.t_sec
    game_match         = $gamePatterns
    game_peak_mib      = if ($gameMax.Count) { [math]::Round((($gameMax.Values | Measure-Object -Maximum).Maximum), 1) } else { $null }
    game_vram_by_pid   = @($gameMax.GetEnumerator() | ForEach-Object {
            [pscustomobject]@{ pid = $_.Key; process = $gameNames[$_.Key]; peak_mib = [math]::Round($_.Value, 1) }
        })
    autodetected_game  = @($autoGame.GetEnumerator() | ForEach-Object {
            [pscustomobject]@{ pid = $_.Key; process = $_.Value.name; peak_mib = $_.Value.peak; growth_mib = [math]::Round($_.Value.delta, 1) }
        } | Sort-Object growth_mib -Descending)
    top_processes      = @($procPeaks | Select-Object -First 15)
    note               = 'nvidia-smi memory.used is absolute truth. per-PID dedicated counters over-count (committed vs resident); use them for attribution only.'
}

Write-Host '--- SUMMARY ---' -ForegroundColor Cyan
Write-Host ("samples            : {0} over {1}s" -f $summary.samples, $summary.duration_sec)
if ($baseline) { Write-Host ("baseline (first 5s): median {0} MiB  min {1}" -f $baseline.median, $baseline.min) }
if ($steady)   { Write-Host ("steady state       : median {0} MiB  p95 {1} MiB  max {2} MiB" -f $steady.median, $steady.p95, $steady.max) }
Write-Host ("PEAK total VRAM    : {0} MiB  (at t+{1}s)" -f $summary.peak_total_mib, $summary.peak_at_sec) -ForegroundColor Yellow

if ($summary.game_peak_mib) {
    Write-Host ("game peak (matched): {0} MiB" -f $summary.game_peak_mib) -ForegroundColor Yellow
}
if ($summary.autodetected_game.Count) {
    Write-Host 'biggest VRAM growers (auto-detected):'
    $summary.autodetected_game | Select-Object -First 5 | ForEach-Object {
        Write-Host ("    {0,-28} pid {1,-7} peak {2,8} MiB  (+{3} MiB)" -f $_.process, $_.pid, $_.peak_mib, $_.growth_mib)
    }
}
if ($baseline -and $steady) {
    Write-Host ("delta steady-baseline : {0} MiB" -f ([math]::Round($steady.median - $baseline.median, 1)))
}

Write-Host ''
Write-Host 'top VRAM consumers (peak):' -ForegroundColor Cyan
$procPeaks | Select-Object -First 10 | ForEach-Object {
    $flag = if ($_.is_game_match) { ' <== GAME' } else { '' }
    Write-Host ("    {0,-28} pid {1,-7} peak {2,8} MiB  (+{3,8} MiB){4}" -f $_.process, $_.pid, $_.peak_mib, $_.growth_mib, $flag)
}

if ($writeFiles) {
    $tsCsv  = Join-Path $OutDir "$Label-timeseries.csv"
    $ppCsv  = Join-Path $OutDir "$Label-perprocess.csv"
    $sumJs  = Join-Path $OutDir "$Label-summary.json"

    $rows     | Export-Csv -Path $tsCsv -NoTypeInformation -Encoding UTF8
    $perProc  | Export-Csv -Path $ppCsv -NoTypeInformation -Encoding UTF8
    ($summary | ConvertTo-Json -Depth 6) | Set-Content -Path $sumJs -Encoding UTF8

    Write-Host ''
    Write-Host 'written:' -ForegroundColor Green
    Write-Host ("    {0}" -f $tsCsv)
    Write-Host ("    {0}" -f $ppCsv)
    Write-Host ("    {0}" -f $sumJs)
}
