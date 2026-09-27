# Proves patches/ still reconstructs the clones: for each clone, checks out its base commit in a
# scratch copy (git clone --shared, no worktree), applies the repo's patch, and compares every changed
# or added file with the working clone in <workspace>\research. Mismatches print as MISSING /
# EXTRA / DIFFERS; "mismatches: 0" for every clone means a lost clone can be rebuilt from
# upstream + patches/. Files that differ only in line endings (OpenXR-SDK-Source checks out CRLF
# through its .gitattributes) are counted separately as line-ending-only. Usage: check_patches.ps1 [-Only <clone>] [-UsePatches a.patch,b.patch]
param([string]$Only = '', [string]$UsePatches = '')
$ErrorActionPreference = 'Continue'
$repo = (Resolve-Path "$PSScriptRoot\..\..").Path; $res = (Resolve-Path "$repo\..\research").Path
$tmp = Join-Path $env:TEMP 'xrw-patchcheck'
New-Item -ItemType Directory -Force $tmp | Out-Null
$sets = @(
  @('ALVR-20.13.0', '7eda092dbf0002281410a4222683ec228700cffb', @('alvr-20.13.0-server-instrumentation.patch')),
  @('ALVR-stable-20.14.1', 'a9f6542fa507a841f40ab4f3fcb531427cd02550', @('alvr-20.14.1-galaxy-xr-client.patch')),
  @('ALVR', 'ca2decae968f2fd37b43b777cca4ba597808ba52', @('alvr-ca2deca-XRWIRED.patch')),
  @('pyrowave', 'd2997ac172bdc00e29c58e3f2938acb7e94580bf', @('pyrowave-cdf53-haar-experiments2-3.patch')),
  @('OpenXR-SDK-Source', '2b99fec95e9cdf352c1a98e9cb23bf4def1cf8e6', @('openxr-sdk-2b99fec-hello_xr.patch'))
)
function Sha($p) { if (Test-Path -LiteralPath $p -PathType Leaf) { (Get-FileHash -LiteralPath $p -Algorithm SHA1).Hash } else { 'ABSENT' } }
foreach ($s in $sets) {
  $name, $base, $patches = $s
  if ($Only -and $name -ne $Only) { continue }
  if ($UsePatches) { $patches = $UsePatches -split ',' }
  $src = "$res\$name"; $t = "$tmp\$name"
  "##### $name (base $($base.Substring(0,7)); patches: $($patches -join ', '))"
  if (-not (Test-Path "$t\.git")) { git -c core.autocrlf=false -c core.longpaths=true clone -q --shared --no-checkout $src $t 2>&1 | Out-Null; git -C $t config core.autocrlf false; git -C $t config core.longpaths true }
  git -C $t checkout -q -f --detach $base 2>&1 | Out-Null
  git -C $t clean -q -fdx 2>&1 | Out-Null
  foreach ($p in $patches) {
    $r = git -C $t apply --binary --whitespace=nowarn "$repo\patches\$p" 2>&1
    if ($LASTEXITCODE -ne 0) { "  APPLY FAILED $p :: $($r | Select-Object -First 3)" } else { "  applied $p" }
  }
  $zset = @(git -C $src ls-files -m -o --exclude-standard) | Where-Object { $_ -notmatch '^(build-[^/]+|Granite)/' }
  $tset = @(git -C $t ls-files -m -o --exclude-standard)
  $all = @($zset + $tset | Sort-Object -Unique)
  $bad = @()
  $eol = 0
  foreach ($f in $all) {
    $pa = Join-Path $src $f; $pb = Join-Path $t $f; $a = Sha $pa; $b = Sha $pb
    if ($a -eq $b) { continue }
    if ($a -ne 'ABSENT' -and $b -ne 'ABSENT' -and
        ([IO.File]::ReadAllText($pa) -replace "`r`n","`n") -eq ([IO.File]::ReadAllText($pb) -replace "`r`n","`n")) { $eol++; continue }
    $bad += "  {0,-8} {1}" -f $(if ($b -eq 'ABSENT') {'MISSING'} elseif ($a -eq 'ABSENT') {'EXTRA'} else {'DIFFERS'}), $f
  }
  "  changed in Z clone: $($zset.Count); produced by patches: $($tset.Count); mismatches: $($bad.Count); line-ending-only: $eol"
  $bad
}
