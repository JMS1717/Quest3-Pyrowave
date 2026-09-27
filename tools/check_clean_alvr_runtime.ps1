$steam = "C:\Program Files (x86)\Steam"
Write-Output "=== DRIVER REGISTRATION ==="
(Get-Content "$env:LOCALAPPDATA\openvr\openvrpaths.vrpath" -Raw | ConvertFrom-Json).external_drivers
Write-Output "=== DRIVER LOG ==="
Get-Content "$steam\logs\vrserver.txt" -Tail 500 |
    Select-String -Pattern "Loaded server driver|alvr|CustomHeadset|VirtualDesktop" |
    Select-Object -Last 50 | ForEach-Object { $_.Line }
Write-Output "=== XRWIRED MARKERS ==="
Get-Content "$steam\logs\vrserver.txt" -Tail 1000 |
    Select-String -Pattern "\[XRWIRED\]" |
    Select-Object -Last 50 | ForEach-Object { $_.Line }
