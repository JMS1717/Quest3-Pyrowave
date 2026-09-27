param([Parameter(Mandatory, HelpMessage = 'Folder holding ALVR, android-tools and backups (this script predates the repo layout)')][string]$Workspace)
$headers = @{ "X-ALVR" = "true" }
$path = "$Workspace\alvr-debug.json"
$values = Get-Content $path -Raw | ConvertFrom-Json
$socketValue = [pscustomobject]@{
    path = @(
        @{ Name = "session_settings" },
        @{ Name = "extra" },
        @{ Name = "logging" },
        @{ Name = "debug_groups" },
        @{ Name = "sockets" }
    )
    value = $true
}
$payload = @($values) + @($socketValue)
$body = $payload | ConvertTo-Json -Depth 10 -Compress
Invoke-WebRequest -UseBasicParsing -Method Post -Headers $headers -ContentType "application/json" `
    -Body $body -Uri "http://127.0.0.1:8082/api/session/values" | Out-Null
Write-Output "ALVR connection logging enabled"
