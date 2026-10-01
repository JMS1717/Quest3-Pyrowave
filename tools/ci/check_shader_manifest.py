"""Reject stale embedded PyroWave shaders after source or precision changes."""
import argparse
import hashlib
import json
from pathlib import Path

FILES = ("shaders/idwt.comp", "shaders/idwt_haar_fused.comp", "shaders/dwt.comp", "shaders/dwt_common.h",
         "shaders/dwt_swizzle.h", "shaders/slangmosh.json", "shaders/slangmosh.hpp")

def manifest(root):
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in FILES}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    path = args.root / "shaders/quest3-manifest.json"
    actual = manifest(args.root)
    if args.write:
        path.write_text(json.dumps(actual, indent=2) + "\n", encoding="utf-8")
    else:
        expected = json.loads(path.read_text(encoding="utf-8"))
        if expected != actual:
            raise SystemExit("Embedded shader manifest differs. Regenerate with the pinned slangmosh workflow before building.")
        print("Embedded shader source and binary manifest verified")

if __name__ == "__main__":
    main()
