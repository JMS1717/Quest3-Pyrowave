$headers = @{ "X-ALVR" = "true" }
$endpoint = "http://127.0.0.1:8082/api/session/values"
$path = @(
    @{ Name = "session_settings" },
    @{ Name = "connection" },
    @{ Name = "stream_protocol" }
)
$body = ConvertTo-Json -InputObject @(@{ path = $path; value = @{ variant = "Tcp" } }) -Depth 10 -Compress
Invoke-WebRequest -UseBasicParsing -Method Post -Headers $headers -ContentType "application/json" `
    -Body $body -Uri $endpoint | Out-Null
Write-Output "ALVR stream protocol set to TCP"
