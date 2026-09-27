# Sourced by the Android build scripts (POSIX sh; Git for Windows' sh on the PC). Resolves
# where the clones and the Android toolchain live, so no script hard-codes one machine's paths.
# The caller sets workspace_dir (the repo root). Sets: inputs_dir, pyrowave_dir, android_sdk,
# android_ndk, java_home, ndk_bin. Explicit environment variables always win:
#   XRWIRED_INPUTS  XRWIRED_PYROWAVE  XRWIRED_ANDROID_SDK  XRWIRED_NDK / ANDROID_NDK_HOME  JAVA_HOME
# Layout: the repo's parent is the workspace, holding <workspace>/research (the clones),
# <workspace>/toolchain (Android SDK/NDK, JDK) and <workspace>/keys (signing keys, never in git).

# An existing directory as the native absolute path (Z:/... under Git for Windows, /... elsewhere);
# a missing one is returned as given.
xrw_native() { (CDPATH= cd -- "$1" 2>/dev/null && { pwd -W 2>/dev/null || pwd; }) || printf '%s' "$1"; }

inputs_dir=$(xrw_native "${XRWIRED_INPUTS:-$workspace_dir/..}")

pyrowave_dir=$(xrw_native "${XRWIRED_PYROWAVE:-$inputs_dir/research/pyrowave}")

android_sdk=$(xrw_native "${XRWIRED_ANDROID_SDK:-$inputs_dir/toolchain/android-sdk}")

android_ndk=$(xrw_native "${XRWIRED_NDK:-${ANDROID_NDK_HOME:-$android_sdk/ndk/27.2.12479018}}")

if [ -n "${JAVA_HOME:-}" ]; then
    java_home=$(xrw_native "$JAVA_HOME")
elif [ -d "$inputs_dir/toolchain/jdk-17" ]; then
    java_home=$(xrw_native "$inputs_dir/toolchain/jdk-17")
elif [ -x /usr/libexec/java_home ]; then
    java_home=$(/usr/libexec/java_home -v 17)
else
    java_home=""
fi

# The NDK's host compiler dir: darwin-x86_64, windows-x86_64 or linux-x86_64, whichever is there.
ndk_bin=""
for xrw_host in "$android_ndk"/toolchains/llvm/prebuilt/*/bin; do
    [ -d "$xrw_host" ] && { ndk_bin=$xrw_host; break; }
done
unset xrw_host

# Release signing for the client APK: the beta key in <workspace>/keys/beta-release (never in the
# repo; CI gets it from a secret), so a local build installs over the CI-built beta. cargo-apk
# reads these two variables; set them yourself to sign with a different key.
if [ -z "${CARGO_APK_RELEASE_KEYSTORE:-}" ] \
    && [ -f "$inputs_dir/keys/beta-release/pyrowave-beta-release.p12" ] \
    && [ -f "$inputs_dir/keys/beta-release/keystore-password.txt" ]; then
    CARGO_APK_RELEASE_KEYSTORE=$(xrw_native "$inputs_dir/keys/beta-release")/pyrowave-beta-release.p12
    CARGO_APK_RELEASE_KEYSTORE_PASSWORD=$(cat "$inputs_dir/keys/beta-release/keystore-password.txt")
    export CARGO_APK_RELEASE_KEYSTORE CARGO_APK_RELEASE_KEYSTORE_PASSWORD
fi
