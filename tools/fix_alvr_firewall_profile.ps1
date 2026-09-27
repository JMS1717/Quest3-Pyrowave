$rules = Get-NetFirewallRule | Where-Object {
    $_.DisplayName -Match '^ALVR (TCP|UDP) 994[3-8]$'
}
$rules | Set-NetFirewallRule -Profile Any
$rules | Select-Object DisplayName, Enabled, Profile, Direction, Action |
    Sort-Object DisplayName -Unique | ConvertTo-Json -Depth 3
