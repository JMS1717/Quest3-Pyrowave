#!/bin/sh
# Builds the client from the ALVR master clone (package alvr.client.dev) -- the abandoned
# direct-USB/stereo experiment, kept buildable because patches/alvr-ca2deca-XRWIRED.patch backs
# 19 files of work there. The clone is an external input beside this repo. --print-dir resolves
# the clone path without building.
set -eu
workspace_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
. "$workspace_dir/tools/lib/xrwired_env.sh"
alvr_dir="$inputs_dir/research/ALVR"

if [ "${1:-}" = "--print-dir" ]; then printf '%s\n' "$alvr_dir"; exit 0; fi

cd "$alvr_dir"
env -u ANDROID_SDK_ROOT \
    ANDROID_HOME="$android_sdk" \
    ANDROID_NDK_HOME="$android_ndk" \
    JAVA_HOME="$java_home" \
    cargo xtask build-client --release

printf '%s\n' "$alvr_dir/build/alvr_client_android/alvr_client_android.apk"
