$headers = @{ "X-ALVR" = "true" }
$response = Invoke-WebRequest -UseBasicParsing -Headers $headers -Uri "http://127.0.0.1:8082/api/session"
Write-Output "STATUS=$($response.StatusCode) LENGTH=$($response.RawContentLength) TYPE=$($response.Headers['Content-Type'])"
Write-Output $response.Content
