$ErrorActionPreference = "Stop"
$headers = @{ "X-ALVR" = "true" }
$path = @(@{ Name = "session_settings" }, @{ Name = "video" }, @{ Name = "foveated_encoding" }, @{ Name = "enabled" })
$body = ConvertTo-Json -InputObject @(@{ path = $path; value = $false }) -Depth 10 -Compress
Invoke-WebRequest -UseBasicParsing -Method Post -Headers $headers -ContentType "application/json" -Body $body -Uri "http://127.0.0.1:8082/api/session/values" | Out-Null
Invoke-WebRequest -UseBasicParsing -Method Post -Headers $headers -Uri "http://127.0.0.1:8082/api/steamvr/restart" | Out-Null
Write-Output "Disabled server foveated encoding and requested runtime restart."
