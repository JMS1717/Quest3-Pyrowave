param([Parameter(Mandatory = $true)][string]$Alvr)
$ErrorActionPreference = 'Stop'
$taskAlvrRoot = (Resolve-Path -LiteralPath $Alvr).Path
$taskSdkRoot = Join-Path ${env:ProgramFiles(x86)} 'Windows Kits\10\bin'
$taskCompiler = Get-ChildItem -LiteralPath $taskSdkRoot -Directory |
    Where-Object { $_.Name -match '^10\.\d+\.\d+\.\d+$' } |
    Sort-Object { [version]$_.Name } -Descending |
    ForEach-Object { Join-Path $_.FullName 'x64\fxc.exe' } |
    Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $taskCompiler) { throw 'Windows SDK x64 fxc.exe is required for matching FFE shader compilation' }
$taskSource = Join-Path $taskAlvrRoot 'alvr\server_openvr\cpp\alvr_server\shader\CompressAxisAlignedPixelShader.hlsl'
$taskOutput = Join-Path $taskAlvrRoot 'alvr\server_openvr\cpp\platform\win32\CompressAxisAlignedPixelShader.cso'
& $taskCompiler /nologo /T ps_5_0 /E main /O3 /Fo $taskOutput $taskSource
if ($LASTEXITCODE -ne 0) { throw 'Foveated encoding shader compilation failed' }
Write-Output ('Compiled FFE shader SHA256: ' + (Get-FileHash -LiteralPath $taskOutput -Algorithm SHA256).Hash.ToLower())

# The adaptive game-render downsample is compiled at runtime by FrameRender (D3DCompile), with a
# single-tap fallback. Compile it here as well, with the same profile, so a shader error fails
# the build instead of silently falling back on a tester's PC.
$taskDownsample = Join-Path $PSScriptRoot '..\downsample\frame_downsample.hlsl'
$taskDownsampleOutput = Join-Path ([System.IO.Path]::GetTempPath()) 'FrameDownsample-check.cso'
& $taskCompiler /nologo /T ps_5_0 /E main /O3 /Fo $taskDownsampleOutput $taskDownsample
if ($LASTEXITCODE -ne 0) { throw 'Adaptive downsample shader compilation failed' }
Remove-Item -LiteralPath $taskDownsampleOutput -Force
Write-Output 'Adaptive downsample shader compiles for ps_5_0'
