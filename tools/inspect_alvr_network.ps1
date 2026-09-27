$dashboard = Get-Process | Where-Object ProcessName -Like "*ALVR*" | Select-Object -First 1
Write-Output "=== DASHBOARD ==="
$dashboard | Select-Object ProcessName, Id, Path, StartTime | ConvertTo-Json -Depth 3
if ($dashboard) {
    Write-Output "=== TCP OWNED ==="
    Get-NetTCPConnection -OwningProcess $dashboard.Id -ErrorAction SilentlyContinue |
        Select-Object State, LocalAddress, LocalPort, RemoteAddress, RemotePort | ConvertTo-Json -Depth 3
    Write-Output "=== UDP OWNED ==="
    Get-NetUDPEndpoint -OwningProcess $dashboard.Id -ErrorAction SilentlyContinue |
        Select-Object LocalAddress, LocalPort | ConvertTo-Json -Depth 3
}
Write-Output "=== ALL ALVR PORT RANGE ==="
Get-NetTCPConnection -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalPort -ge 9940 -and $_.LocalPort -le 9950 } |
    Select-Object State, LocalAddress, LocalPort, OwningProcess | ConvertTo-Json -Depth 3
Get-NetUDPEndpoint -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalPort -ge 9940 -and $_.LocalPort -le 9950 } |
    Select-Object LocalAddress, LocalPort, OwningProcess | ConvertTo-Json -Depth 3
Write-Output "=== RECENT LOG CANDIDATES ==="
Get-ChildItem "$env:USERPROFILE" -Recurse -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -Match "alvr|session_log" -and $_.LastWriteTime -gt (Get-Date).AddHours(-2) } |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 30 FullName, Length, LastWriteTime | ConvertTo-Json -Depth 3
