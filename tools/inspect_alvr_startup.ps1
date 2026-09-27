param([Parameter(Mandatory, HelpMessage = 'Folder holding ALVR, android-tools and backups (this script predates the repo layout)')][string]$Workspace)
$ErrorActionPreference = "SilentlyContinue"
Write-Output "=== MATCHING PROCESSES ==="
Get-Process | Where-Object { $_.ProcessName -Match "ALVR|vrmonitor|vrserver" } |
    Select-Object ProcessName, Id, Path | ConvertTo-Json -Depth 3
Write-Output "=== DASHBOARD BINARY ==="
Get-Item "$Workspace\ALVR\build\alvr_streamer_windows\ALVR Dashboard.exe" |
    Select-Object FullName, Length, LastWriteTime | ConvertTo-Json -Depth 3
Write-Output "=== RECENT ALVR FILES ==="
Get-ChildItem "$env:LOCALAPPDATA\ALVR" -Recurse -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 20 FullName, Length, LastWriteTime | ConvertTo-Json -Depth 3
Write-Output "=== APPLICATION ERRORS ==="
Get-WinEvent -FilterHashtable @{LogName = "Application"; StartTime = (Get-Date).AddMinutes(-10)} |
    Where-Object { $_.Message -Match "ALVR" -or $_.ProviderName -Match "Application Error" } |
    Select-Object -First 10 TimeCreated, ProviderName, Id, Message | ConvertTo-Json -Depth 4
