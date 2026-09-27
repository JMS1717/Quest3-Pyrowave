#!/bin/zsh
set -euo pipefail
repo_dir="${0:A:h:h}"
gradle_bin="${GRADLE:-$HOME/.gradle/wrapper/dists/gradle-8.9-bin/90cnw93cvbtalezasaz0blq0a/gradle-8.9/bin/gradle}"
export ANDROID_HOME="${ANDROID_HOME:?set ANDROID_HOME to the Android SDK}"
export ANDROID_SDK_ROOT="$ANDROID_HOME"
export GRADLE_USER_HOME="$repo_dir/.gradle-home"
cd "$repo_dir/receiver-xr"
"$gradle_bin" --offline --no-daemon assembleDebug
apk="$repo_dir/receiver-xr/app/build/outputs/apk/debug/app-debug.apk"
print "APK: $apk"
unzip -l "$apk" | grep 'lib/arm64-v8a/'
