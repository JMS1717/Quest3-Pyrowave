#!/bin/sh
set -eu

ADB_BIN="${ADB_BIN:-$ANDROID_HOME/platform-tools/adb}"
OUT_DIR="${1:-captures/galaxy-xr-optics}"

if ! "$ADB_BIN" get-state >/dev/null 2>&1; then
    echo "No ADB device is connected." >&2
    exit 1
fi

mkdir -p "$OUT_DIR/active" "$OUT_DIR/efs" "$OUT_DIR/vendor-defaults"

remote_uid=$("$ADB_BIN" shell 'id -u' | tr -d '\r')
use_su=0
if [ "$remote_uid" != "0" ]; then
    if "$ADB_BIN" shell 'su -c id -u' 2>/dev/null | tr -d '\r' | grep -qx 0; then
        use_su=1
    else
        echo "Root is required. This device currently exposes only the ADB shell user." >&2
        echo "Expected active profiles are under /data/vendor/qvr and factory originals under /mnt/vendor/efs." >&2
        exit 2
    fi
fi

read_remote() {
    remote_path=$1
    local_path=$2
    if [ "$use_su" -eq 1 ]; then
        "$ADB_BIN" exec-out su -c "cat '$remote_path'" > "$local_path"
    else
        "$ADB_BIN" exec-out cat "$remote_path" > "$local_path"
    fi
    if [ ! -s "$local_path" ]; then
        echo "Empty or unreadable: $remote_path" >&2
        return 1
    fi
}

for name in device_calibration.xml svrapi_lens_left.csv svrapi_lens_right.csv svrapi_config.txt xr_device_config.json; do
    read_remote "/data/vendor/qvr/$name" "$OUT_DIR/active/$name"
done

for name in device_calibration.xml svrapi_lens_left.csv svrapi_lens_right.csv svrapi_config.txt; do
    read_remote "/mnt/vendor/efs/$name" "$OUT_DIR/efs/$name" || true
done

for name in device_calibration_default.xml svrapi_lens_left_default.csv svrapi_lens_right_default.csv xr_device_config_default.json; do
    "$ADB_BIN" exec-out cat "/vendor/etc/qvr/$name" > "$OUT_DIR/vendor-defaults/$name"
done

"$ADB_BIN" shell 'getprop ro.boot.serialno; getprop ro.revision; getprop ro.build.fingerprint' \
    | tr -d '\r' > "$OUT_DIR/device-identity.txt"

(
    cd "$OUT_DIR"
    find active efs vendor-defaults -type f -exec shasum -a 256 {} \; | sort > SHA256SUMS
)

echo "Extracted optics data to $OUT_DIR"
echo "Compare active/ with vendor-defaults/; EFS is the factory source when present."
