#!/bin/bash
# Cross-compiles the PyroWave AHardwareBuffer decode harness for the headset.
# Needs the pyrowave clone built for Android with -DPYROWAVE_DEVEL=OFF (which produces
# libpyrowave-shared.so).
set -e
workspace_dir=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
. "$workspace_dir/tools/lib/xrwired_env.sh"
PW="$pyrowave_dir"
HERE=$(xrw_native "$(dirname -- "$0")")
[ -n "$ndk_bin" ] || { echo "no NDK host toolchain under $android_ndk"; exit 1; }

[ -f "$PW/pyrowave.h" ] || { echo "no pyrowave.h under $PW"; exit 1; }
[ -f "$PW/build-android/libpyrowave-shared.so" ] || {
    echo "no libpyrowave-shared.so -- build pyrowave for android with -DPYROWAVE_DEVEL=OFF"; exit 1; }

# API 29 for AHARDWAREBUFFER_FORMAT_R8_UNORM.
"$ndk_bin/clang++" --target=aarch64-linux-android29 \
    -std=c++17 -O2 -Wall \
    -I"$PW" \
    -I"$PW/Granite/third_party/khronos/vulkan-headers/include" \
    "$HERE/main.cpp" \
    -o "$HERE/pyrowave_android" \
    -L"$PW/build-android" -lpyrowave-shared \
    -lvulkan -landroid -lnativewindow -llog

echo "BUILD_OK -> $HERE/pyrowave_android"
