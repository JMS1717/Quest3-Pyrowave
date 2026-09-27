param([Parameter(Mandatory, HelpMessage = 'Folder holding ALVR, android-tools and backups (this script predates the repo layout)')][string]$Workspace)
$ErrorActionPreference = 'Stop'
$root = "$Workspace\ALVR-unmodified-ca2deca\build\alvr_streamer_windows"
Stop-Process -Name vrdashboard -Force -ErrorAction SilentlyContinue
$headers = @{'X-ALVR'='true'}
$base = 'http://127.0.0.1:8082/api'
Invoke-WebRequest -UseBasicParsing -Method Post -Headers $headers -Uri "$base/recording/start" | Out-Null
Start-Sleep -Seconds 5
Invoke-WebRequest -UseBasicParsing -Method Post -Headers $headers -Uri "$base/recording/stop" | Out-Null
Get-ChildItem $root -Filter 'recording.*' | Sort-Object LastWriteTime -Descending | Select-Object -First 1 FullName,Length | ConvertTo-Json
Get-Process vrserver,vrcompositor,steamvr_tutorial -ErrorAction SilentlyContinue | Select-Object Name,Id
