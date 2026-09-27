#!/bin/sh
set -eu

ADB=${ADB:-$ANDROID_HOME/platform-tools/adb}
OUT=${1:-captures/galaxy-xr-baseline.txt}

if [ ! -x "$ADB" ]; then
  echo "ADB not executable: $ADB" >&2
  exit 1
fi

mkdir -p "$(dirname "$OUT")"

{
  date -u '+captured_at_utc=%Y-%m-%dT%H:%M:%SZ'
  "$ADB" devices -l
  "$ADB" shell '
    echo "== BUILD =="
    getprop ro.product.manufacturer
    getprop ro.product.model
    getprop ro.build.fingerprint
    getprop ro.build.version.release
    getprop ro.build.version.sdk
    echo "== USB =="
    getprop sys.usb.config
    getprop sys.usb.state
    svc usb getUsbSpeed 2>&1
    svc usb getGadgetHalVersion 2>&1
    echo "== NETWORK =="
    ip -br addr
    ip route
    echo "== XR AND STREAMING PACKAGES =="
    pm list packages | grep -E "steam|openxr|xr|vrlink"
    echo "== STEAM LINK VR =="
    dumpsys package com.valvesoftware.steamlinkvr | grep -E "versionName|versionCode|firstInstallTime|lastUpdateTime|installerPackageName|signatures="
    echo "== THERMAL SUMMARY =="
    dumpsys thermalservice | head -n 45
  '
} > "$OUT"

echo "Wrote $OUT"
