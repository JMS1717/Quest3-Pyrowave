param([Parameter(Mandatory, HelpMessage = 'Folder holding ALVR, android-tools and backups (this script predates the repo layout)')][string]$Workspace)
$ErrorActionPreference = 'Stop'
$source = $Workspace
$desktop = [Environment]::GetFolderPath('Desktop')
if (!$desktop) { throw 'Desktop location unavailable' }
$zip = Join-Path $desktop ('XR_Wired-backup-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.zip')
if (Test-Path $zip) { throw 'Backup target already exists' }
Get-Process vrserver,vrcompositor,vrmonitor,'ALVR Dashboard',steamvr_tutorial -ErrorAction SilentlyContinue | Stop-Process -Force
Write-Output "BACKUP_TARGET=$zip"
& tar -a -cf $zip -C (Split-Path $source -Parent) (Split-Path $source -Leaf)
if ($LASTEXITCODE -ne 0) { throw "Archive creation failed: $LASTEXITCODE" }
$entries = & tar -tf $zip
if ($LASTEXITCODE -ne 0) { throw 'Archive listing verification failed' }
if (!($entries | Where-Object {$_ -match 'backups/'})) { throw 'Configuration backups missing from archive' }
if (!($entries | Where-Object {$_ -match 'ALVR/Cargo.toml$'})) { throw 'ALVR source missing from archive' }
Write-Output "VERIFIED_ENTRIES=$($entries.Count)"
Get-Item $zip | Select-Object FullName,Length,LastWriteTime | ConvertTo-Json
