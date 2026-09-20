# Boot / bugcheck / driver-load event check for the BSOD observation window.
# ASCII-only on purpose (PowerShell 5.1 reads BOM-less files as GBK).
$today = (Get-Date).Date
$ids = 41, 1001, 6008, 7026, 1074, 109, 6005, 6006, 6009
Get-WinEvent -FilterHashtable @{LogName = 'System'; StartTime = $today} -ErrorAction SilentlyContinue |
    Where-Object { $ids -contains $_.Id } |
    Sort-Object TimeCreated |
    ForEach-Object {
        $first = ($_.Message -split "`r?`n")[0]
        '{0}  id={1,-5} {2}  {3}' -f $_.TimeCreated.ToString('HH:mm:ss'), $_.Id, $_.ProviderName, $first
    }
'--- uptime ---'
$os = Get-CimInstance Win32_OperatingSystem
'LastBootUpTime: ' + $os.LastBootUpTime
'Now           : ' + (Get-Date)
