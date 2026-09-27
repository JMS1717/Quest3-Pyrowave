#!/bin/zsh
set -euo pipefail
repo_dir="${0:A:h:h}"
swiftc -O -framework Foundation -framework ScreenCaptureKit -framework CoreMedia -framework CoreVideo \
  "$repo_dir/tools/mac_screen_capture.swift" -o "$repo_dir/tools/mac_screen_capture"
print "Built $repo_dir/tools/mac_screen_capture"
