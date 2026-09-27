Write-Output "=== PC ADDRESSES ==="
Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.IPAddress -notlike "169.254*" } |
    Select-Object InterfaceAlias, InterfaceIndex, IPAddress, PrefixLength |
    ConvertTo-Json -Depth 3

Write-Output "=== NETWORK PROFILES ==="
Get-NetConnectionProfile |
    Select-Object InterfaceAlias, InterfaceIndex, Name, NetworkCategory, IPv4Connectivity |
    ConvertTo-Json -Depth 3

Write-Output "=== ALVR FIREWALL FILTERS ==="
Get-NetFirewallRule | Where-Object DisplayName -Match "ALVR" | ForEach-Object {
    $rule = $_
    $ports = $rule | Get-NetFirewallPortFilter
    $apps = $rule | Get-NetFirewallApplicationFilter
    [pscustomobject]@{
        Name = $rule.DisplayName
        Enabled = $rule.Enabled
        Direction = $rule.Direction
        Action = $rule.Action
        Profile = $rule.Profile
        Protocol = $ports.Protocol
        LocalPort = $ports.LocalPort
        Program = $apps.Program
    }
} | ConvertTo-Json -Depth 4

Write-Output "=== CURRENT 994X LISTENERS ==="
Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalPort -ge 9942 -and $_.LocalPort -le 9948 } |
    Select-Object LocalAddress, LocalPort, OwningProcess | ConvertTo-Json -Depth 3
Get-NetUDPEndpoint -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalPort -ge 9942 -and $_.LocalPort -le 9948 } |
    Select-Object LocalAddress, LocalPort, OwningProcess | ConvertTo-Json -Depth 3
