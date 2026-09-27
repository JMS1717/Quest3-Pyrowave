param([Parameter(Mandatory, HelpMessage = 'headset Wi-Fi IP address')][string]$HeadsetIp, [Parameter(Mandatory, HelpMessage = 'Folder holding ALVR, android-tools and backups (this script predates the repo layout)')][string]$Workspace)
$ErrorActionPreference='Stop'
$root=$Workspace
$session=Get-Content "$root\ALVR-stable-20.14.1\session.json" -Raw|ConvertFrom-Json
$client=$session.client_connections.'galaxy-stable-usb'
if ($client.current_ip -ne $HeadsetIp -or $client.connection_state -ne 'Streaming') { throw 'Wi-Fi stream is not active; benchmark stopped.' }
$source=Get-Content "$root\xrwired-benchmark-60.ps1" -Raw
$source=$source.Replace("$root\benchmark-72-2560-hevc-375", "$root\benchmark-wifi-ci-d430eac-72-2560-hevc-375")
Write-Host 'Starting same-method 60-second Wi-Fi capture.'
& ([ScriptBlock]::Create($source))
