param([Parameter(Mandatory, HelpMessage = 'Folder holding ALVR, android-tools and backups (this script predates the repo layout)')][string]$Workspace)
$ErrorActionPreference = 'Continue'
Set-Location "$Workspace\ALVR-unmodified-ca2deca"
$env:CARGO_TARGET_DIR = "$Workspace\ALVR\target"
$output = & cargo xtask build-streamer --release 2>&1
$code = $LASTEXITCODE
$output | Select-String -Pattern 'error C[0-9]+|fatal error|error:|error LNK|Finished|Copying' | ForEach-Object { $_.Line }
exit $code
