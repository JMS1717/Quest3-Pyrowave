$candidates = @(
    "$env:LOCALAPPDATA\Android\Sdk",
    "C:\Android\Sdk",
    "$env:USERPROFILE\AppData\Local\Android\Sdk",
    "C:\Program Files\Android\Android Studio"
)
Write-Output "=== SDK CANDIDATES ==="
$candidateResults = foreach ($path in $candidates) {
    [pscustomobject]@{ Path = $path; Exists = Test-Path $path }
}
$candidateResults | ConvertTo-Json -Depth 3

Write-Output "=== COMMANDS ==="
Get-Command adb, emulator, sdkmanager, avdmanager -ErrorAction SilentlyContinue |
    Select-Object Name, Source | ConvertTo-Json -Depth 3

Write-Output "=== EMULATOR AND IMAGES ==="
Get-ChildItem "$env:USERPROFILE" -Filter emulator.exe -File -Recurse -ErrorAction SilentlyContinue |
    Select-Object -First 10 FullName, Length, LastWriteTime | ConvertTo-Json -Depth 3
Get-ChildItem "$env:USERPROFILE" -Filter package.xml -File -Recurse -ErrorAction SilentlyContinue |
    Where-Object FullName -Match "system-images" |
    Select-Object -First 20 FullName | ConvertTo-Json -Depth 3

Write-Output "=== VIRTUALIZATION ==="
Get-CimInstance Win32_Processor |
    Select-Object Name, VirtualizationFirmwareEnabled, SecondLevelAddressTranslationExtensions |
    ConvertTo-Json -Depth 3
Get-WindowsOptionalFeature -Online -FeatureName HypervisorPlatform,VirtualMachinePlatform,Microsoft-Hyper-V-All -ErrorAction SilentlyContinue |
    Select-Object FeatureName, State | ConvertTo-Json -Depth 3
