# Installs the Android build toolchain for this workspace, portably, under <workspace>\toolchain
# (no system-wide Java, no global JAVA_HOME/ANDROID_HOME; tools/lib/xrwired_env.sh finds it there).
# Versions match what the Mac previously built with. Idempotent: re-running skips what exists.
#   JDK 17 (Microsoft OpenJDK), Android cmdline-tools, platform-tools, platforms 35/36,
#   build-tools 34/35/36, NDK 27.2.12479018, CMake 3.22.1,
#   Rust 1.97.1 + aarch64-linux-android, cargo-ndk 4.1.2, cbindgen 0.29.4, cargo-apk (zarik5 0fd3126)
# The SDK licences must already be accepted: copy an accepted SDK's licenses\ folder into
# <workspace>\toolchain\android-sdk\licenses first (this script never accepts licences itself).
param([string]$Workspace = (Resolve-Path "$PSScriptRoot\..\..\..").Path)
$ErrorActionPreference = 'Stop'; $ProgressPreference = 'SilentlyContinue'
$tc = Join-Path $Workspace 'toolchain'; $sdk = "$tc\android-sdk"; $dl = "$tc\downloads"
New-Item -ItemType Directory -Force $dl, $sdk | Out-Null
function Say($m) { "[{0:HH:mm:ss}] $m" -f (Get-Date) }
if (-not (Test-Path "$sdk\licenses\android-sdk-license")) { throw "no accepted licences in $sdk\licenses" }

if (-not (Test-Path "$tc\jdk-17\bin\java.exe")) {
  Say 'JDK 17'
  Invoke-WebRequest 'https://aka.ms/download-jdk/microsoft-jdk-17-windows-x64.zip' -OutFile "$dl\jdk17.zip"
  Expand-Archive "$dl\jdk17.zip" "$dl\jdk17" -Force
  Move-Item (Get-ChildItem "$dl\jdk17" -Directory | Select-Object -First 1).FullName "$tc\jdk-17"
}
if (-not (Test-Path "$sdk\cmdline-tools\latest\bin\android.exe")) {
  Say 'Android cmdline-tools'
  Invoke-WebRequest 'https://dl.google.com/android/repository/commandlinetools-win-16111833_latest.zip' -OutFile "$dl\cmdline-tools.zip"
  Expand-Archive "$dl\cmdline-tools.zip" "$dl\clt" -Force
  New-Item -ItemType Directory -Force "$sdk\cmdline-tools" | Out-Null
  Move-Item "$dl\clt\cmdline-tools" "$sdk\cmdline-tools\latest"
}
$ErrorActionPreference = 'Continue'
$env:JAVA_HOME = "$tc\jdk-17"
# sdkmanager in this cmdline-tools release is deprecated and installs silently incompletely; the
# replacement `android sdk install` is used instead.
Say 'SDK packages'
& "$sdk\cmdline-tools\latest\bin\android.exe" "--sdk=$sdk" sdk install platform-tools platforms/android-35 platforms/android-36 `
  build-tools/34.0.0 build-tools/35.0.0 build-tools/36.0.0 ndk/27.2.12479018 cmake/3.22.1 2>&1 | Where-Object { $_ -notmatch '%' }
Say 'Rust'
rustup toolchain install 1.97.1 2>&1 | Select-Object -Last 1
rustup target add aarch64-linux-android --toolchain 1.97.1 2>&1 | Select-Object -Last 1
$have = (cargo install --list) -join "`n"
if ($have -notmatch 'cargo-ndk v4\.1\.2') { cargo +1.97.1 install cargo-ndk --version 4.1.2 --locked 2>&1 | Select-Object -Last 1 }
if ($have -notmatch 'cbindgen v0\.29\.4') { cargo +1.97.1 install cbindgen --version 0.29.4 --locked 2>&1 | Select-Object -Last 1 }
if ($have -notmatch '0fd3126d') { cargo +1.97.1 install --git https://github.com/zarik5/cargo-apk --rev 0fd3126dad5aa1c5f0f26cdae3410f2e5af62c60 cargo-apk 2>&1 | Select-Object -Last 1 }
& "$sdk\cmdline-tools\latest\bin\android.exe" "--sdk=$sdk" sdk list 2>&1 | Select-String '^\s+\S+/|platform-tools'
Say 'DONE'
