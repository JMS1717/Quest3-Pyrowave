$steam = 'C:\Program Files (x86)\Steam'
foreach ($name in @('vrcompositor.txt','vrserver.txt','vrclient_steamvr_tutorial.txt')) {
    Write-Output "LOG: $name"
    $path = "$steam\logs\$name"
    if (Test-Path $path) {
        Get-Content $path -Tail 1500 | Select-String -Pattern 'error|fail|adapter|gpu|texture|submit|focus|scene|tutorial|direct|display|headset|tracking|standby' | Select-Object -Last 45 | ForEach-Object {$_.Line}
    }
}
Get-CimInstance Win32_VideoController | Select-Object Name,DriverVersion,Status
Get-Content "$steam\config\steamvr.vrsettings" -ErrorAction SilentlyContinue
