"""Codec payload math; never a claim of sustainable network or decoder performance."""
import argparse
import json
import math
from pathlib import Path


def frame_budget(width, height, hz, mbps, chroma="420"):
    if not all(isinstance(v, int) and v > 0 for v in (width, height, mbps)):
        raise ValueError("Resolution and bitrate must be positive integers")
    if not math.isfinite(hz) or hz < 1 or chroma not in ("420", "444"):
        raise ValueError("Invalid refresh or chroma")
    w, h = ((v + 31) // 32 * 32 for v in (width, height))
    pixels = w * h * 2
    raw_bytes = pixels * 3 // (2 if chroma == "420" else 1)
    payload = mbps * 1_000_000 // 8 // math.floor(hz + 0.5)
    if payload == 0:
        raise ValueError("Bitrate is too small for the requested refresh")
    return {"requested_eye_resolution": [width, height], "aligned_eye_resolution": [w, h],
            "hz": hz, "mbps": mbps, "chroma": chroma, "frame_ms": 1000 / hz,
            "maximum_payload_bytes_per_frame": payload, "raw_bytes_per_frame": raw_bytes,
            "raw_to_payload_ratio": raw_bytes / payload,
            "encoded_bits_per_stereo_pixel": payload * 8 / pixels,
            # Full-size TCP segments on Ethernet, before ACKs/retries/Wi-Fi airtime.
            "minimum_tcp_ethernet_mbps_at_cap": mbps * 1538 / 1460,
            "fits_pwu2_fragment_limit": (payload + 1367) // 1368 <= 8192,
            "sustained_performance_verified": False}


def profiles(config):
    p = config["pyrowave"]
    width, height = p["requested_eye_resolution"]
    return [frame_budget(*v.get("requested_eye_resolution", [width, height]), v["hz"], v["mbps"], v["chroma"])
            for v in p["candidate_profiles"] + p["experimental_profiles"]]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--presets", default="presets/quest3.json")
    parser.add_argument("--out")
    args = parser.parse_args()
    report = profiles(json.loads(Path(args.presets).read_text()))
    text = json.dumps(report, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n")
    else:
        print(text)


if __name__ == "__main__":
    main()
