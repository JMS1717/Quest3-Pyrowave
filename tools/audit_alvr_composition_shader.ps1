param([Parameter(Mandatory, HelpMessage = 'Folder holding ALVR, android-tools and backups (this script predates the repo layout)')][string]$Workspace)
$fxc = "C:\Program Files (x86)\Windows Kits\10\bin\10.0.26100.0\x64\fxc.exe"
& $fxc /dumpbin "$Workspace\ALVR\alvr\server_openvr\cpp\platform\win32\FrameRenderPS.cso"
