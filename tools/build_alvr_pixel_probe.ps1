param([Parameter(Mandatory, HelpMessage = 'Folder holding ALVR, android-tools and backups (this script predates the repo layout)')][string]$Workspace)
Set-Location "$Workspace\ALVR"
$sessionFile = "build\alvr_streamer_windows\session.json"
$backup = "$Workspace\backups\alvr-emulator-session-pixel-probe.json"
Stop-Process -Name vrserver,vrcompositor,vrmonitor,"ALVR Dashboard" -Force -ErrorAction SilentlyContinue
Copy-Item $sessionFile $backup -Force
$output = & cargo xtask build-streamer --release 2>&1
$code = $LASTEXITCODE
if ($code -eq 0) { Copy-Item $backup $sessionFile -Force }
$output | Select-String -Pattern "error C[0-9]+|Finished|error:|Copying|error LNK" | ForEach-Object { $_.Line }
exit $code
