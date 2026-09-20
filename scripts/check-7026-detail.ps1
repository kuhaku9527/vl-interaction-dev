# Show the full 7026 message (boot-start / system-start drivers that failed to load)
# for the most recent boots. ASCII-only on purpose (PS 5.1 reads BOM-less files as GBK).
Get-WinEvent -FilterHashtable @{LogName = 'System'; Id = 7026; StartTime = (Get-Date).AddDays(-1)} -ErrorAction SilentlyContinue |
    Sort-Object TimeCreated -Descending |
    ForEach-Object {
        '=== ' + $_.TimeCreated
        $_.Message
        ''
    }
