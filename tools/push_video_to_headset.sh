#!/bin/sh
set -eu

ADB=${ADB:-$ANDROID_HOME/platform-tools/adb}
SOURCE=${1:?Usage: push_video_to_headset.sh VIDEO_FILE}
NAME=$(basename "$SOURCE")
DEST="/sdcard/Download/$NAME"

if [ ! -x "$ADB" ]; then
  echo "ADB not executable: $ADB" >&2
  exit 1
fi

if [ ! -f "$SOURCE" ]; then
  echo "Video not found: $SOURCE" >&2
  exit 1
fi

"$ADB" push "$SOURCE" "$DEST"
"$ADB" shell content call \
  --uri content://media \
  --method scan_file \
  --arg "file://$DEST" >/dev/null 2>&1 || true

echo "Transferred to $DEST"
echo "Open VLC on the headset and select Download/$NAME"
