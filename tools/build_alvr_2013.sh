#!/bin/sh
# Builds the Galaxy XR client APK from the patched ALVR 20.13.0 clone (package
# alvr.client.galaxy2013). The clone is an external input and lives beside this repo, not in it --
# see README "Building from source". Pass --print-dir to resolve the clone path
# without building.
set -eu
workspace_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
. "$workspace_dir/tools/lib/xrwired_env.sh"
alvr_dir="$inputs_dir/research/ALVR-20.13.0"

if [ "${1:-}" = "--print-dir" ]; then printf '%s\n' "$alvr_dir"; exit 0; fi

# The PyroWave decoder rides into the APK as two prebuilt .so files in cargo-apk's runtime_libs
# dir (which is gitignored in the clone): our libpyroclient.so and PyroWave's own library.
# client_core/build.rs links against the same dir.
"$workspace_dir/tools/pyroclient/build.sh" >/dev/null
cp "$workspace_dir/tools/pyroclient/libpyroclient.so" "$pyrowave_dir/build-android/libpyrowave-shared.so" \
    "$alvr_dir/deps/android_openxr/arm64-v8a/"

cd "$alvr_dir"
env -u ANDROID_SDK_ROOT ANDROID_HOME="$android_sdk" \
    ANDROID_NDK_HOME="$android_ndk" ANDROID_NDK_ROOT="$android_ndk" \
    JAVA_HOME="$java_home" cargo +1.97.1 xtask build-client --release

printf '%s\n' "$alvr_dir/build/alvr_client_android/alvr_client_android.apk"
