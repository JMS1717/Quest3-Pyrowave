#!/bin/zsh
set -euo pipefail
repo_dir="${0:A:h:h}"
adb="${ADB:-${ANDROID_HOME:?set ANDROID_HOME to the Android SDK, or ADB to adb}/platform-tools/adb}"
"$repo_dir/tools/build_receiver.sh"
"$adb" install -r "$repo_dir/receiver/app/build/outputs/apk/debug/app-debug.apk"
"$adb" shell am force-stop com.xrwired.receiver
"$adb" shell am start -n com.xrwired.receiver/.MainActivity
"$adb" forward tcp:45100 tcp:45100
print "Receiver launched. Start: $repo_dir/tools/send_video.py"
