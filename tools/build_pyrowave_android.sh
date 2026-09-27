#!/bin/sh
# Builds PyroWave for the headset: libpyrowave-shared.so in <pyrowave>/build-android, which
# pyroclient/build.sh, pyrowave_android/build.sh and the ALVR client link against. Settings are the
# ones the Mac's build-android was configured with (its CMakeCache): arm64-v8a,
# android-30, Release, -DPYROWAVE_DEVEL=OFF, -DPYROWAVE_FP32_MATH=ON, Ninja.
set -eu
workspace_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
. "$workspace_dir/tools/lib/xrwired_env.sh"
[ -f "$pyrowave_dir/pyrowave.h" ] || { echo "no pyrowave.h under $pyrowave_dir"; exit 1; }
[ -f "$android_ndk/build/cmake/android.toolchain.cmake" ] || { echo "no NDK at $android_ndk"; exit 1; }
build="$pyrowave_dir/build-android"
cmake -S "$pyrowave_dir" -B "$build" -G Ninja \
    -DCMAKE_TOOLCHAIN_FILE="$android_ndk/build/cmake/android.toolchain.cmake" \
    -DANDROID_ABI=arm64-v8a -DANDROID_PLATFORM=android-30 \
    -DCMAKE_BUILD_TYPE=Release -DPYROWAVE_DEVEL=OFF -DPYROWAVE_FP32_MATH=ON
cmake --build "$build" --target pyrowave-shared
echo "BUILD_OK -> $build/libpyrowave-shared.so"
