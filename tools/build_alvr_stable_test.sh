#!/bin/sh
# Builds the version-comparison client from the ALVR 20.14.1 clone (package
# alvr.client.stabletest), which installs alongside galaxy2013 rather than replacing it. The clone
# is an external input beside this repo -- see README "Building from source". --print-dir resolves the
# clone path without building.
set -eu
workspace_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
. "$workspace_dir/tools/lib/xrwired_env.sh"
alvr_dir="$inputs_dir/research/ALVR-stable-20.14.1"

if [ "${1:-}" = "--print-dir" ]; then printf '%s\n' "$alvr_dir"; exit 0; fi

cd "$alvr_dir"
env -u ANDROID_SDK_ROOT ANDROID_HOME="$android_sdk" \
    ANDROID_NDK_HOME="$android_ndk" \
    JAVA_HOME="$java_home" cargo +1.97.1 xtask build-client --release

printf '%s\n' "$alvr_dir/build/alvr_client_android/alvr_client_android.apk"
