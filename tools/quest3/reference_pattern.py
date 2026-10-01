"""Create a deterministic asymmetric 4:2:0 frame for decoder regression checks."""
import argparse
from pathlib import Path


def write_pattern(path, width, height):
    if width < 64 or height < 64 or width % 32 or height % 32:
        raise ValueError("Use dimensions at least 64 and divisible by 32")
    with path.open("xb") as out:
        out.write(f"YUV4MPEG2 W{width} H{height} F120:1 Ip A1:1 C420jpeg XCOLORRANGE=LIMITED\nFRAME\n".encode())
        for y in range(height):
            out.write(bytes(16 + ((x * 3 + y * 5 + (120 if x >= width // 2 else 0)) % 220)
                            for x in range(width)))
        for y in range(height // 2):
            out.write(bytes(40 + ((x * 2 + y * 7) % 170) for x in range(width // 2)))
        for y in range(height // 2):
            out.write(bytes(200 - ((x * 5 + y * 3) % 150) for x in range(width // 2)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--width", type=int, default=512)
    parser.add_argument("--height", type=int, default=320)
    args = parser.parse_args()
    write_pattern(args.output, args.width, args.height)


if __name__ == "__main__":
    main()
