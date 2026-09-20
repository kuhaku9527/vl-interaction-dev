# Bluescreen root-cause deep dive (2026-09-20).
#
# Hypothesis chain:
#   BugCheck 0x20001 (HYPERVISOR_ERROR), param1=0x26
#   + "error detected on Harddisk2\DR2 during paging" x4
#   => paging I/O hit a disk error -> hypervisor-level bugcheck -> BSOD
#
# NOTE: this file is deliberately ASCII-only. Windows PowerShell 5.1 and
# even pwsh mis-decode non-ASCII comments in files without a UTF-8 BOM,
# which breaks parsing. Keep diagnostics scripts ASCII.
#
# Run: pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/diagnose-bluescreen-deep.ps1

$ErrorActionPreference = 'SilentlyContinue'
$out = Join-Path $PSScriptRoot '..\doc\research\bluescreen-deepdive.txt'

"=== BSOD deep dive $(Get-Date -Format o) ===" | Out-File $out -Encoding UTF8

"`n--- 1. Physical disks ---" | Out-File $out -Append -Encoding UTF8
Get-Disk | Select-Object Number, FriendlyName,
    @{n='SizeGB';e={[math]::Round($_.Size/1GB,1)}}, BusType, HealthStatus, OperationalStatus |
    Format-Table -AutoSize | Out-File $out -Append -Encoding UTF8

"`n--- 2. Volumes ---" | Out-File $out -Append -Encoding UTF8
Get-Volume | Where-Object { $_.DriveLetter } |
    Select-Object DriveLetter, FileSystemLabel, FileSystem,
    @{n='SizeGB';e={[math]::Round($_.Size/1GB,1)}}, HealthStatus |
    Format-Table -AutoSize | Out-File $out -Append -Encoding UTF8

"`n--- 3. Pagefile configuration ---" | Out-File $out -Append -Encoding UTF8
Get-CimInstance Win32_PageFileSetting |
    Select-Object Name, InitialSize, MaximumSize |
    Format-Table -AutoSize | Out-File $out -Append -Encoding UTF8
Get-CimInstance Win32_PageFileUsage |
    Select-Object Name, AllocatedBaseSize, CurrentUsage, PeakUsage |
    Format-Table -AutoSize | Out-File $out -Append -Encoding UTF8

"`n--- 4. WSL vhdx files ---" | Out-File $out -Append -Encoding UTF8
$vhdxPaths = @(
    "$env:LOCALAPPDATA\wsl",
    "$env:LOCALAPPDATA\Packages"
)
foreach ($base in $vhdxPaths) {
    Get-ChildItem -Path $base -Filter *.vhdx -Recurse -ErrorAction SilentlyContinue |
        Select-Object FullName,
            @{n='SizeGB';e={[math]::Round($_.Length/1GB,2)}}, LastWriteTime |
        Format-Table -AutoSize | Out-File $out -Append -Encoding UTF8
}

"`n--- 5. WSL status ---" | Out-File $out -Append -Encoding UTF8
(wsl.exe -l -v 2>&1 | Out-String) | Out-File $out -Append -Encoding UTF8

"`n--- 6. Disk health / reliability ---" | Out-File $out -Append -Encoding UTF8
Get-PhysicalDisk | Select-Object DeviceId, FriendlyName, MediaType, HealthStatus,
    @{n='SizeGB';e={[math]::Round($_.Size/1GB,1)}} |
    Format-Table -AutoSize | Out-File $out -Append -Encoding UTF8
Get-PhysicalDisk | Get-StorageReliabilityCounter |
    Select-Object DeviceId, ReadErrorsTotal, WriteErrorsTotal, Temperature, Wear |
    Format-Table -AutoSize | Out-File $out -Append -Encoding UTF8

"`n--- 7. Disk error events (id 51/7/11/153, last 2 days) ---" | Out-File $out -Append -Encoding UTF8
Get-WinEvent -FilterHashtable @{LogName='System'; Id=51,7,11,153; StartTime=(Get-Date).AddDays(-2)} -MaxEvents 30 |
    ForEach-Object {
        $msg = ($_.Message -replace '\s+', ' ')
        "[$($_.TimeCreated)] id=$($_.Id) $($_.ProviderName): $msg"
    } | Out-File $out -Append -Encoding UTF8

"`n--- 8. WHEA hardware error events ---" | Out-File $out -Append -Encoding UTF8
Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-WHEA-Logger'; StartTime=(Get-Date).AddDays(-2)} -MaxEvents 20 |
    ForEach-Object {
        $msg = ($_.Message -replace '\s+', ' ')
        "[$($_.TimeCreated)] id=$($_.Id): $msg"
    } | Out-File $out -Append -Encoding UTF8

"`n--- 9. Hyper-V / virtualization events (last day) ---" | Out-File $out -Append -Encoding UTF8
Get-WinEvent -FilterHashtable @{LogName='System'; StartTime=(Get-Date).AddDays(-1)} |
    Where-Object { $_.ProviderName -match 'Hyper-V|VBS|Virtualization|hv' } |
    Select-Object -First 20 |
    ForEach-Object {
        $msg = ($_.Message -replace '\s+', ' ')
        "[$($_.TimeCreated)] id=$($_.Id) $($_.ProviderName): $msg"
    } | Out-File $out -Append -Encoding UTF8

"`n--- 10. Hyper-V / VBS enablement ---" | Out-File $out -Append -Encoding UTF8
$dg = Get-CimInstance -ClassName Win32_DeviceGuard -Namespace root\Microsoft\Windows\DeviceGuard
"VBS status: $($dg.VirtualizationBasedSecurityStatus)" | Out-File $out -Append -Encoding UTF8
"Hyper-V-All: $((Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V-All).State)" | Out-File $out -Append -Encoding UTF8
"VirtualMachinePlatform: $((Get-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform).State)" | Out-File $out -Append -Encoding UTF8
"HypervisorPresent: $((Get-CimInstance Win32_ComputerSystem).HypervisorPresent)" | Out-File $out -Append -Encoding UTF8

"`n--- 11. Memory configuration ---" | Out-File $out -Append -Encoding UTF8
$os = Get-CimInstance Win32_OperatingSystem
"TotalVisibleMemory: $([math]::Round($os.TotalVisibleMemorySize/1MB,2)) GB" | Out-File $out -Append -Encoding UTF8
"FreePhysicalMemory: $([math]::Round($os.FreePhysicalMemory/1MB,2)) GB" | Out-File $out -Append -Encoding UTF8
"TotalVirtualMemory: $([math]::Round($os.TotalVirtualMemorySize/1MB,2)) GB" | Out-File $out -Append -Encoding UTF8
"FreeVirtualMemory: $([math]::Round($os.FreeVirtualMemory/1MB,2)) GB" | Out-File $out -Append -Encoding UTF8
Get-CimInstance Win32_PhysicalMemory |
    Select-Object BankLabel, DeviceLocator,
    @{n='GB';e={[math]::Round($_.Capacity/1GB,0)}}, Speed, Manufacturer, PartNumber |
    Format-Table -AutoSize | Out-File $out -Append -Encoding UTF8

"`n--- 12. Memory diagnostic results (if any) ---" | Out-File $out -Append -Encoding UTF8
Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-MemoryDiagnostics-Results'} -MaxEvents 10 |
    ForEach-Object { "[$($_.TimeCreated)] id=$($_.Id): $(($_.Message -split "`n")[0])" } |
    Out-File $out -Append -Encoding UTF8

Write-Output "written to $out"
