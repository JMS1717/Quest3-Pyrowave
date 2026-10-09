#!/bin/sh
# Checks out the upstream sources the beta is built from and applies our patches, into <dest>:
#   <dest>/ALVR-20.13.0  alvr-org/ALVR at ALVR_BASE + patches/alvr-20.13.0-server-instrumentation.patch
#   <dest>/pyrowave      Themaister/pyrowave at PYROWAVE_BASE + patches/pyrowave-cdf53-haar-experiments2-3.patch,
#                        with Granite (and its submodules) at GRANITE_COMMIT
# Both patches are cumulative: base + one patch reproduces the measured clone exactly.
# Usage: tools/ci/fetch_sources.sh <dest>
# Q3PW_PYROWAVE_ONLY=1 reconstructs just the patched pyrowave tree, without ALVR or Granite, for
# shader-level checks that need only its sources and generated slangmosh.hpp.
# Q3PW_BASE_REPOS=<dir> takes the pinned base commits from an earlier full reconstruction in <dir>
# instead of GitHub and skips the unpatched submodules (ALVR's openvr headers, Granite). It stages
# only the patched trees, in about a second, for tools/local/fast_build.py; CI never sets it.
set -eu
: "${ALVR_BASE:=7eda092dbf0002281410a4222683ec228700cffb}"
: "${PYROWAVE_BASE:=d2997ac172bdc00e29c58e3f2938acb7e94580bf}"
: "${GRANITE_COMMIT:=842d9d5686ba8c799a7d34a78a68f98d6aeb5a68}"
repo=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
dest=${1:?usage: fetch_sources.sh <dest>}
[ ! -e "$dest/ALVR-20.13.0" ] && [ ! -e "$dest/pyrowave" ] || { echo "Destination already contains sources; use a new directory to preserve local work." >&2; exit 1; }
mkdir -p "$dest"

base_repos=${Q3PW_BASE_REPOS:-}
checkout() {  # <url> <dir> <commit>
    src=$1
    [ -z "$base_repos" ] || src="$base_repos/$(basename -- "$2")"
    git init -q "$2"
    git -C "$2" config core.autocrlf false
    git -C "$2" config core.longpaths true   # Granite's SPIRV-Cross test files pass 260 chars
    git -C "$2" fetch -q --depth 1 "$src" "$3"
    git -C "$2" checkout -q FETCH_HEAD
}

pyrowave_only=${Q3PW_PYROWAVE_ONLY:-0}
if [ "$pyrowave_only" != 1 ]; then
checkout https://github.com/alvr-org/ALVR "$dest/ALVR-20.13.0" "$ALVR_BASE"
[ -n "$base_repos" ] || git -C "$dest/ALVR-20.13.0" submodule update -q --init --recursive --depth 1   # openvr headers
git -C "$dest/ALVR-20.13.0" apply --binary "$repo/patches/alvr-20.13.0-server-instrumentation.patch"
git -C "$dest/ALVR-20.13.0" apply --binary "$repo/patches/quest3-alvr.patch"
cp "$repo/tools/foveation/light.glsl" "$dest/ALVR-20.13.0/alvr/graphics/resources/light_foveation.glsl"
cp "$repo/tools/latency/latency_stamp.h" "$dest/ALVR-20.13.0/alvr/server_openvr/cpp/platform/win32/latency_stamp.h"
cp "$repo/tools/fences/native_ready.rs" "$dest/ALVR-20.13.0/alvr/graphics/src/native_ready.rs"
cp "$repo/tools/fences/ready_wait.rs" "$dest/ALVR-20.13.0/alvr/graphics/src/ready_wait.rs"
cp "$repo/tools/fences/ready_frames.rs" "$dest/ALVR-20.13.0/alvr/client_core/src/video_decoder/ready_frames.rs"
cp "$repo/tools/fences/latest_wait.rs" "$dest/ALVR-20.13.0/alvr/client_openxr/src/latest_wait.rs"
cp "$repo/tools/quest3/cadence_probe.rs" "$dest/ALVR-20.13.0/alvr/client_openxr/src/cadence_probe.rs"
cp "$repo/tools/quest3/producer_opportunity.rs" "$dest/ALVR-20.13.0/alvr/client_core/src/video_decoder/producer_opportunity.rs"
cp "$repo/tools/quest3/producer_prerecord.rs" "$dest/ALVR-20.13.0/alvr/client_core/src/video_decoder/producer_prerecord.rs"
fi

checkout https://github.com/Themaister/pyrowave "$dest/pyrowave" "$PYROWAVE_BASE"
if [ "$pyrowave_only" != 1 ] && [ -z "$base_repos" ]; then
# pyrowave's checkout_granite.sh pins a newer Granite (9d44761), which spiked encoder p99 to 14 ms;
# the measurements used 842d9d5, cloned here with all of its submodules.
checkout https://github.com/Themaister/Granite "$dest/pyrowave/Granite" "$GRANITE_COMMIT"
git -C "$dest/pyrowave/Granite" submodule update -q --init --recursive --depth 1
[ "$(git -C "$dest/pyrowave/Granite" rev-parse HEAD)" = "$GRANITE_COMMIT" ] \
    || { echo "Granite is not at $GRANITE_COMMIT"; exit 1; }
fi
git -C "$dest/pyrowave" apply --binary "$repo/patches/pyrowave-cdf53-haar-experiments2-3.patch"
git -C "$dest/pyrowave" apply --binary "$repo/patches/quest3-pyrowave.patch"
git -C "$dest/pyrowave" apply --binary "$repo/patches/pyrowave-prerecord-api.patch"
git -C "$dest/pyrowave" apply --binary "$repo/patches/pyrowave-fuse-color.patch"
# Optional fused dequant + level-0 Haar (debug.q3pw.dequant_haar), with its embedded shaders.
git -C "$dest/pyrowave" apply --binary "$repo/patches/pyrowave-fused-dequant-haar.patch"
# Optional two-pass [3,2] inverse Haar (debug.q3pw.haar32), with its embedded shaders.
git -C "$dest/pyrowave" apply --binary "$repo/patches/pyrowave-haar32.patch"
# Optional Decoder V2 register-only inverse CDF 5/3 (debug.q3pw.cdf53v2), with its embedded shaders.
git -C "$dest/pyrowave" apply --binary "$repo/patches/pyrowave-cdf53v2.patch"
# Encoder rate-control quality ceiling (Skip invisible detail), with its embedded shader.
git -C "$dest/pyrowave" apply --binary "$repo/patches/pyrowave-quality-floor.patch"
# Haar decoder mode 7: 4:4:4 packed into one present buffer, as Decoder V2 mode 7 (debug.q3pw.haar32).
git -C "$dest/pyrowave" apply --binary "$repo/patches/pyrowave-haar444.patch"

[ "$pyrowave_only" != 1 ] || { echo "pyrowave-only sources ready in $dest/pyrowave (${PYROWAVE_BASE%${PYROWAVE_BASE#???????}})"; exit 0; }
echo "sources ready in $dest: ALVR ${ALVR_BASE%${ALVR_BASE#???????}}, pyrowave ${PYROWAVE_BASE%${PYROWAVE_BASE#???????}}, Granite ${GRANITE_COMMIT%${GRANITE_COMMIT#???????}}"
