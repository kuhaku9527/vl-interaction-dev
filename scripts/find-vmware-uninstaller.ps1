# Find how to uninstall VMware Workstation when winget cannot see it.
#
# The Uninstall registry entry for VMware Workstation 17.0.0 exists but has
# EMPTY InstallLocation and UninstallString (a known VMware quirk). So we must
# locate the real installer/uninstaller on disk and read the MSI product code.
#
# Output: doc\research\vmware-uninstall-howto.txt   (ASCII only)

$ErrorActionPreference = 'Continue'
$out = Join-Path $PSScriptRoot '..\doc\research\vmware-uninstall-howto.txt'

"=== VMware uninstall reconnaissance $(Get-Date -Format o) ===" | Out-File $out -Encoding UTF8

"`n--- 1. Full registry detail for the VMware entry (incl. hidden keys) ---" | Out-File $out -Append -Encoding UTF8
foreach ($root in 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall',
                  'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall') {
    Get-ChildItem $root -ErrorAction SilentlyContinue | ForEach-Object {
        $p = Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue
        if ($p.DisplayName -match 'VMware') {
            "  KEY: $($_.PSChildName)" | Out-File $out -Append -Encoding UTF8
            $p.PSObject.Properties |
                Where-Object { $_.Name -notmatch '^PS' } |
                ForEach-Object { "      $($_.Name) = $($_.Value)" } |
                Out-File $out -Append -Encoding UTF8
            "" | Out-File $out -Append -Encoding UTF8
        }
    }
}

"`n--- 2. Find VMware install dirs / uninstallers on disk ---" | Out-File $out -Append -Encoding UTF8
$candidates = @(
    'C:\Program Files (x86)\VMware',
    'C:\Program Files\VMware',
    'C:\Program Files (x86)\Common Files\VMware',
    'C:\Program Files\Common Files\VMware'
)
foreach ($c in $candidates) {
    if (Test-Path $c) {
        "  DIR: $c" | Out-File $out -Append -Encoding UTF8
        Get-ChildItem $c -Recurse -Include *.exe -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -match 'install|uninstall|setup|vmware|vmnetcfg' } |
            Select-Object -First 25 |
            ForEach-Object { "      $($_.FullName)   $($_.Length)   $($_.LastWriteTime)" } |
            Out-File $out -Append -Encoding UTF8
    } else {
        "  (absent) $c" | Out-File $out -Append -Encoding UTF8
    }
}

"`n--- 3. Any vmware*.exe anywhere obvious ---" | Out-File $out -Append -Encoding UTF8
foreach ($base in 'C:\Program Files (x86)','C:\Program Files','D:\') {
    Get-ChildItem $base -Recurse -Depth 4 -Filter 'vm*.exe' -ErrorAction SilentlyContinue |
        Select-Object -First 15 |
        ForEach-Object { "    $($_.FullName)" } |
        Out-File $out -Append -Encoding UTF8
}

"`n--- 4. Installed MSI products matching VMware (via registry Installer) ---" | Out-File $out -Append -Encoding UTF8
Get-ChildItem 'HKLM:\SOFTWARE\Classes\Installer\Products' -ErrorAction SilentlyContinue |
    ForEach-Object {
        $pn = (Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue).ProductName
        if ($pn -match 'VMware') {
            "  ProductCode-ish key: $($_.PSChildName)   ProductName: $pn" |
                Out-File $out -Append -Encoding UTF8
        }
    }

"`n--- 5. Download cache / original installer present? ---" | Out-File $out -Append -Encoding UTF8
$dl = "$env:USERPROFILE\Downloads"
if (Test-Path $dl) {
    Get-ChildItem $dl -Filter '*VMware*' -ErrorAction SilentlyContinue |
        Select-Object -First 10 |
        ForEach-Object { "    $($_.Name)   $([math]::Round($_.Length/1MB,1)) MB   $($_.LastWriteTime)" } |
        Out-File $out -Append -Encoding UTF8
} else {
    "  (no Downloads dir)" | Out-File $out -Append -Encoding UTF8
}

"`n--- 6. Network adapters installed by VMware ---" | Out-File $out -Append -Encoding UTF8
Get-NetAdapter -ErrorAction SilentlyContinue |
    Where-Object { $_.InterfaceDescription -match 'VMware' -or $_.Name -match 'VMnet' } |
    Select-Object Name, InterfaceDescription, Status |
    Format-Table -AutoSize | Out-File $out -Append -Encoding UTF8

"`n--- 7. Windows Installer cache entries for VMware ---" | Out-File $out -Append -Encoding UTF8
Get-ChildItem 'C:\Windows\Installer' -Filter '*.msi' -ErrorAction SilentlyContinue |
    Select-Object -First 5 | ForEach-Object { "    $($_.FullName)" } |
    Out-File $out -Append -Encoding UTF8
"  (Windows Installer cache is ACL-protected; listing may be empty)" | Out-File $out -Append -Encoding UTF8

Write-Output "written to $out"
