param([Parameter(Mandatory, HelpMessage = 'Folder holding ALVR, android-tools and backups (this script predates the repo layout)')][string]$Workspace)
$adb = "$Workspace\android-tools\platform-tools\adb.exe"
& $adb kill-server
$action = New-ScheduledTaskAction -Execute $adb -Argument 'server nodaemon' -WorkingDirectory (Split-Path $adb)
Register-ScheduledTask -TaskName XRWiredADB -Action $action -User $env:USERNAME -RunLevel Limited -Force | Out-Null
Start-ScheduledTask XRWiredADB
Start-Sleep -Seconds 2
& $adb devices -l
