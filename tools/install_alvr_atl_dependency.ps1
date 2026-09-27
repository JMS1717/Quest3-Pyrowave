$ErrorActionPreference = 'Stop'
$installer = 'C:\Program Files (x86)\Microsoft Visual Studio\Installer\setup.exe'
$installation = 'C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools'
if (!(Test-Path $installer)) { throw 'Visual Studio installer not found' }
$p = Start-Process $installer -ArgumentList @('modify','--installPath',('"'+$installation+'"'),'--add','Microsoft.VisualStudio.Component.VC.ATL','--quiet','--norestart') -PassThru -Wait
Write-Output "ATL_INSTALL_EXIT=$($p.ExitCode)"
Get-ChildItem "$installation\VC\Tools\MSVC" -Filter atlbase.h -Recurse | Select-Object FullName
