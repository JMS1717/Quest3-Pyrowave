param([Parameter(Mandatory, HelpMessage = 'Folder holding ALVR, android-tools and backups (this script predates the repo layout)')][string]$Workspace)
$root = "$Workspace\ALVR\build\alvr_streamer_windows"
Get-Process vrserver,vrcompositor -ErrorAction SilentlyContinue | Select-Object Id,ProcessName
Get-Content "$root\crash_log.txt" -Tail 50 -ErrorAction SilentlyContinue
Get-Content "$root\session_log.txt" -Tail 80 -ErrorAction SilentlyContinue
Get-Content "C:\Program Files (x86)\Steam\logs\vrserver.txt" -Tail 160 -ErrorAction SilentlyContinue
$s = Get-Content "$root\session.json" -Raw | ConvertFrom-Json
$s.client_connections | ConvertTo-Json -Depth 6
$s.steamvr_hmd_init_config | ConvertTo-Json
$s.session_settings.video.foveated_encoding.enabled
