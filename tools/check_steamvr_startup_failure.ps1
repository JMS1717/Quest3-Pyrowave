param([Parameter(Mandatory, HelpMessage = 'Folder holding ALVR, android-tools and backups (this script predates the repo layout)')][string]$Workspace)
Write-Output "=== PROCESSES ==="
Get-Process | Where-Object { $_.ProcessName -Match "ALVR|vrserver|vrmonitor" } |
    Select-Object ProcessName, Id, Path | ConvertTo-Json -Depth 3
Write-Output "=== VRSERVER TAIL ==="
Get-Content "C:\Program Files (x86)\Steam\logs\vrserver.txt" -Tail 250
Write-Output "=== SESSION LOG ==="
Get-Content "$Workspace\ALVR\build\alvr_streamer_windows\session_log.txt" -Tail 250 -ErrorAction SilentlyContinue
