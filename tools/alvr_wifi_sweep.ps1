# Runs one Wi-Fi measurement step against the ALVR 20.13 streamer:
# configure -> restart SteamVR -> confirm Wi-Fi streaming -> settle -> 60 s capture + filtered logcat.
# Extra parameters (e.g. -Codec H264 -Mbps 800 -EyeSize 2880) pass straight to configure_alvr_2013.ps1.
param(
    [Parameter(Mandatory)][string]$Label,
    [Parameter(Mandatory)][string]$ClientHostname,
    [Parameter(Mandatory)][string]$ClientWifiIp,
    [Parameter(Mandatory, HelpMessage = 'adb serial of the headset')][string]$Serial,
    [string]$Root = (Join-Path $PSScriptRoot 'ALVR-20.13.0'),
    [string]$SweepRoot = "$PSScriptRoot\sweeps\$(Get-Date -Format yyyy-MM-dd)",
    [int]$SettleSeconds = 15,
    [int]$ConnectTimeoutSeconds = 90,
    [switch]$WhatIf,
    [Parameter(ValueFromRemainingArguments)][object[]]$ConfigureArgs
)
$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
$adb = "$here\android-tools\platform-tools\adb.exe"
$out = Join-Path $SweepRoot $Label
$api = 'http://127.0.0.1:8082/api/dashboard-request'
$headers = @{ 'X-ALVR' = 'true' }

$configure = "$here\configure_alvr_2013.ps1"
$configureParams = @{ Root = $Root; ClientHostname = $ClientHostname; ClientWifiIp = $ClientWifiIp }
for ($i = 0; $i -lt $ConfigureArgs.Count; $i += 2) {
    $configureParams[([string]$ConfigureArgs[$i]).TrimStart('-')] = $ConfigureArgs[$i + 1]
}

if ($WhatIf) {
    Write-Host "Step '$Label' -> $out"
    & $configure @configureParams -WhatIf
    return
}

New-Item -ItemType Directory -Path $out -Force | Out-Null
& $configure @configureParams | Tee-Object -FilePath "$out\configure.txt"

# Most video settings carry the steamvr-restart flag.
& $adb -s $Serial logcat -c
Invoke-RestMethod $api -Method Post -Headers $headers -ContentType 'application/json' -Body '"RestartSteamvr"' | Out-Null

# Wi-Fi proof: no stream forwards, and the client is streaming from its Wi-Fi address.
foreach ($port in 9943, 9944) { & $adb -s $Serial forward --remove "tcp:$port" 2>$null }
$deadline = (Get-Date).AddSeconds($ConnectTimeoutSeconds)
do {
    Start-Sleep -Seconds 3
    $client = (Get-Content "$Root\session.json" -Raw | ConvertFrom-Json).client_connections.$ClientHostname
} until (($client.connection_state -eq 'Streaming') -or ((Get-Date) -gt $deadline))
if ($client.connection_state -ne 'Streaming') { throw "Client not streaming after $ConnectTimeoutSeconds s (state: $($client.connection_state))." }
if ("$($client.current_ip)" -ne $ClientWifiIp) { throw "Client streaming from $($client.current_ip), not Wi-Fi $ClientWifiIp." }
if (& $adb -s $Serial forward --list | Select-String ':994[34]') { throw 'ADB stream forwards are still present.' }
Write-Host "Streaming over Wi-Fi from $ClientWifiIp. Settling $SettleSeconds s."
Start-Sleep -Seconds $SettleSeconds

# Reuse the benchmark capture used for the earlier USB/Wi-Fi reports, pointed at this step.
$capture = Get-Content "$here\xrwired-benchmark-60.ps1" -Raw
$capture = $capture.Replace("$here\benchmark-72-2560-hevc-375", $out)
$capture = $capture.Replace("$here\ALVR-stable-20.14.1\session.json", "$Root\session.json")
$capture = $capture.Replace('<serial>', $Serial)   # the device serial hard-coded in the capture template
& ([ScriptBlock]::Create($capture))

& $adb -s $Serial logcat -d |
    Select-String 'Decoder saturation|Waiting for IDR|AMediaCodec format|display period|recv buffer|software fallback|Dropped video packet|foveat' |
    ForEach-Object { $_.Line } | Set-Content "$out\logcat-filtered.txt"
Write-Host "Step '$Label' complete: $out"
