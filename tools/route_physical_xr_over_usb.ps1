param([Parameter(Mandatory, HelpMessage = 'IP address ALVR should dial the client at')][string]$ClientIp, [Parameter(Mandatory, HelpMessage = 'Folder holding ALVR, android-tools and backups (this script predates the repo layout)')][string]$Workspace)
$ErrorActionPreference = 'Stop'
$root = "$Workspace\ALVR-unmodified-ca2deca\build\alvr_streamer_windows"
Copy-Item "$root\session.json" "$Workspace\backups\before-physical-usb-$(Get-Date -Format yyyyMMdd-HHmmss).json"
$headers = @{'X-ALVR'='true'}
foreach ($entry in @(
    @('2386.client.local.', @{SetManualIps=@($ClientIp)}),
    @('0830.client.local.', 'RemoveEntry')
)) {
    $body = ConvertTo-Json -InputObject $entry -Depth 7 -Compress
    Invoke-WebRequest -UseBasicParsing -Method Post -Headers $headers -ContentType application/json -Body $body -Uri 'http://127.0.0.1:8082/api/session/client-connections' | Out-Null
}
Get-Process vrserver,vrcompositor,vrmonitor,steamvr_tutorial -ErrorAction SilentlyContinue | Stop-Process -Force
Stop-ScheduledTask XRWiredSteamVR -ErrorAction SilentlyContinue
Start-ScheduledTask XRWiredSteamVR
