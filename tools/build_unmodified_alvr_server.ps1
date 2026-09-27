param([Parameter(Mandatory, HelpMessage = 'Folder holding ALVR, android-tools and backups (this script predates the repo layout)')][string]$Workspace)
$ErrorActionPreference = 'Stop'
$clean = "$Workspace\ALVR-unmodified-ca2deca"
if (Test-Path $clean) { throw 'Clean test directory already exists; refusing to overwrite.' }
New-Item -ItemType Directory $clean | Out-Null
tar -xf "$Workspace\alvr-unmodified-ca2deca.tar" -C $clean
if ($LASTEXITCODE -ne 0) { throw 'Source extraction failed' }
New-Item -ItemType Junction -Path "$clean\deps" -Target "$Workspace\ALVR\deps" | Out-Null
Set-Location $clean
$env:CARGO_TARGET_DIR = "$Workspace\ALVR\target"
& cargo xtask build-streamer --release
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Copy-Item "$Workspace\backups\alvr-emulator-session-pixel-probe.json" "$clean\build\alvr_streamer_windows\session.json"
Write-Output 'UNMODIFIED_SERVER_BUILD_READY'
