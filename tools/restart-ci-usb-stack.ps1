param([Parameter(Mandatory, HelpMessage = 'adb serial of the headset')][string]$Serial, [Parameter(Mandatory, HelpMessage = 'Folder holding ALVR, android-tools and backups (this script predates the repo layout)')][string]$Workspace)
$ErrorActionPreference='Stop'
$adb="$Workspace\android-tools\platform-tools\adb.exe"
& $adb -s $serial shell am force-stop alvr.client.stabletest
foreach ($task in @('XRWiredStableALVR','XRWiredSteamVR','XRWiredSteamReconnect')) { Stop-ScheduledTask -TaskName $task }
Get-Process ALVR*,vrmonitor,vrdashboard,vrcompositor,vrserver,vrwebhelper,steam,steamwebhelper -ErrorAction SilentlyContinue | ForEach-Object { $_.Kill(); $_.WaitForExit(5000) | Out-Null }
Start-Sleep -Seconds 2
& $adb -s $serial forward tcp:9943 tcp:9943
& $adb -s $serial forward tcp:9944 tcp:9944
Start-ScheduledTask -TaskName XRWiredSteamReconnect
Start-Sleep -Seconds 8
Start-ScheduledTask -TaskName XRWiredStableALVR
Start-ScheduledTask -TaskName XRWiredSteamVR
Start-Sleep -Seconds 5
& $adb -s $serial shell monkey -p alvr.client.stabletest -c android.intent.category.LAUNCHER 1
Get-Process steam,'ALVR Dashboard',vrserver,vrcompositor -ErrorAction SilentlyContinue | Select-Object Name,Id,SessionId
& $adb -s $serial forward --list
