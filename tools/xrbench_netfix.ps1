# Elevated helper for xrbench RNDIS runs (runs as the XRWiredNetFix task, RunLevel Highest).
# Reads xrbench-runs\netfix-request.json {"ip": "<headset usb0 ip>", "alias": "<Windows RNDIS adapter>"}
# and makes the headset reachable only through that adapter:
#   - removes the tethering default route (so the PC's own traffic stays on the LAN)
#   - pins a /32 host route to the headset (VirtualBox host-only may claim the same /24)
# Writes xrbench-runs\netfix-result.json with the adapter Windows now picks for the headset.
$ErrorActionPreference = 'Continue'
$dir = Join-Path $PSScriptRoot 'xrbench-runs'
$request = Get-Content (Join-Path $dir 'netfix-request.json') -Raw | ConvertFrom-Json
$ip, $alias = $request.ip, $request.alias
$errors = @()
Get-NetRoute -InterfaceAlias $alias -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue |
    Remove-NetRoute -Confirm:$false -ErrorAction SilentlyContinue
Get-NetRoute -DestinationPrefix "$ip/32" -ErrorAction SilentlyContinue |
    Remove-NetRoute -Confirm:$false -ErrorAction SilentlyContinue
try { New-NetRoute -DestinationPrefix "$ip/32" -InterfaceAlias $alias -RouteMetric 1 -ErrorAction Stop | Out-Null }
catch { $errors += "New-NetRoute: $($_.Exception.Message)" }
$routed = (Find-NetRoute -RemoteIPAddress $ip | Select-Object -First 1).InterfaceAlias
$internet = (Find-NetRoute -RemoteIPAddress 1.1.1.1 | Select-Object -First 1).InterfaceAlias
[pscustomobject]@{ ip = $ip; alias = $alias; routed_via = $routed; internet_via = $internet; errors = $errors;
                   time = (Get-Date).ToString('o') } |
    ConvertTo-Json | Set-Content (Join-Path $dir 'netfix-result.json')
