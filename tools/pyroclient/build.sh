#!/bin/bash
# Builds libpyroclient.so (the ALVR client's PyroWave decoder) and pyroclient_test for the headset.
# Needs the pyrowave clone built for Android with -DPYROWAVE_DEVEL=OFF (libpyrowave-shared.so).
set -e
workspace_dir=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
. "$workspace_dir/tools/lib/xrwired_env.sh"
PW="$pyrowave_dir"
HERE=$(xrw_native "$(dirname -- "$0")")
[ -n "$ndk_bin" ] || { echo "no NDK host toolchain under $android_ndk"; exit 1; }
[ -f "$PW/build-android/libpyrowave-shared.so" ] || { echo "no libpyrowave-shared.so under $PW/build-android"; exit 1; }
CXX="$ndk_bin/clang++ --target=aarch64-linux-android29"
FLAGS="-std=c++17 -O2 -Wall -Wextra -Wno-missing-field-initializers -I$PW -I$PW/Granite/third_party/khronos/vulkan-headers/include"
$CXX $FLAGS -shared -fPIC -Wl,-soname,libpyroclient.so \
    -Wl,--version-script="$HERE/exports.map" \
    "$HERE/pyroclient.cpp" "$HERE/surface_probe.cpp" -o "$HERE/libpyroclient.so" \
    -L"$PW/build-android" -lpyrowave-shared -lvulkan -landroid -llog
$CXX $FLAGS "$HERE/pyroclient_test.cpp" "$HERE/gpu_readback_gles.cpp" \
    "$HERE/gpu_readback_android.cpp" -o "$HERE/pyroclient_test" \
    -L"$HERE" -lpyroclient -L"$PW/build-android" -lpyrowave-shared -landroid -llog -lEGL -lGLESv3 -lz
$CXX $FLAGS "$HERE/release_fence_gpu_test.cpp" "$HERE/gpu_readback_gles.cpp" \
    "$HERE/gpu_readback_android.cpp" -o "$HERE/release_fence_gpu_test" \
    -L"$HERE" -lpyroclient -L"$PW/build-android" -lpyrowave-shared -landroid -llog -lEGL -lGLESv3 -lz
$CXX $FLAGS "$HERE/ready_fence_gpu_test.cpp" "$HERE/gpu_readback_gles.cpp" \
    "$HERE/gpu_readback_android.cpp" -o "$HERE/ready_fence_gpu_test" \
    -L"$HERE" -lpyroclient -L"$PW/build-android" -lpyrowave-shared -landroid -llog -lEGL -lGLESv3 -lz
$CXX $FLAGS "$HERE/prerecord_gpu_test.cpp" "$HERE/gpu_readback_gles.cpp" \
    "$HERE/gpu_readback_android.cpp" -o "$HERE/prerecord_gpu_test" \
    -L"$HERE" -lpyroclient -L"$PW/build-android" -lpyrowave-shared -landroid -llog -lEGL -lGLESv3 -lz
# Exact-pixel fused color gate; on the Quest run it with the default GATE_MIN_PSNR=30.
$CXX $FLAGS "$HERE/fuse_color_gate.cpp" -o "$HERE/fuse_color_gate" \
    -L"$PW/build-android" -lpyrowave-shared -lvulkan
echo "BUILD_OK -> $HERE/libpyroclient.so $HERE/pyroclient_test $HERE/release_fence_gpu_test $HERE/ready_fence_gpu_test $HERE/fuse_color_gate"
