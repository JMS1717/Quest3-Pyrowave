"""Turn a bitstream-tap capture into a PyroWave `.wave` file so it can be decoded offline.

The tap writes the encoded frames concatenated, plus a `.idx` saying where each one starts (see
`bitstream.py`). `pyrowave-decode` instead wants its own tiny container: an eight-byte magic, a
fixed header, then each frame as a length followed by its bytes. This converts one to the other,
which is what closes the quality loop --

    tap -> .wave -> pyrowave-decode -> .y4m -> ffmpeg psnr vs the encoder-input dump

-- and that loop contains no display, no compositor and no homography, unlike capturing a still on
the headset, where PSNR rose as bitrate fell because the capture path dominated.

Container layout, from pyrowave's own `encode.cpp`:

    "PYROWAVE"                     8 bytes
    int32[8]                       width, height, format, chroma, full_range, fps_num, fps_den, 0
    per frame: uint32 size, then `size` bytes
"""
import struct

from . import bitstream

MAGIC = b"PYROWAVE"

# YUV4MPEGFile::Format, as decode.cpp reads it back out of the header.
FORMAT_YUV420P = 0
FORMAT_YUV444P = 1
FORMAT_YUV420P16 = 2
FORMAT_YUV444P16 = 3

# PyroWave::ChromaSubsampling.
CHROMA_420 = 0
CHROMA_444 = 1


def header(width, height, fps_num=72, fps_den=1, chroma=CHROMA_444, full_range=True):
    """The fixed part of the container.

    Defaults describe what VideoEncoderPyroWave produces: 4:4:4, eight bit, full range. Get these
    wrong and the decode still succeeds -- it simply describes the frame incorrectly, which shows
    up as a wrong-looking image rather than an error, so they are worth passing explicitly.
    """
    if width <= 0 or height <= 0:
        raise ValueError(f"bad dimensions {width}x{height}")
    if chroma not in (CHROMA_420, CHROMA_444):
        raise ValueError(f"unknown chroma {chroma}")
    fmt = FORMAT_YUV444P if chroma == CHROMA_444 else FORMAT_YUV420P
    return MAGIC + struct.pack(
        "<8i", width, height, fmt, chroma, 1 if full_range else 0, fps_num, fps_den, 0
    )


def convert(payload_path, index_path, out_path, width, height, **kwargs):
    """Write a `.wave` holding every frame the index records. Returns the frames written.

    Frames are taken by offset and length from the index rather than by scanning the payload,
    because a PyroWave bitstream has no start codes to scan for -- unlike the H.264 the tap was
    originally built for.
    """
    rows = merge_packets(bitstream.read_index(index_path))
    if not rows:
        raise ValueError(f"{index_path} records no frames")

    written = 0
    with open(payload_path, "rb") as payload, open(out_path, "wb") as out:
        out.write(header(width, height, **kwargs))
        for row in rows:
            payload.seek(row["offset"])
            data = payload.read(row["bytes"])
            if len(data) != row["bytes"]:
                # Truncated capture: stop rather than emit a short frame the decoder would
                # misread as the next frame's length.
                break
            out.write(struct.pack("<I", len(data)))
            out.write(data)
            written += 1
    return written


def merge_packets(rows):
    """One row per frame. Over UDP the server taps every MTU-sized packet as its own record, all
    with the frame's pts and written back to back, so consecutive rows with one pts are one
    frame: the packets concatenated are exactly what the TCP path tapped as a single record."""
    out = []
    for row in rows:
        last = out[-1] if out else None
        if last and last["pts_ns"] == row["pts_ns"] and last["offset"] + last["bytes"] == row["offset"]:
            last["bytes"] += row["bytes"]
        else:
            out.append(dict(row))
    return out


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("payload", help="the tap's .h264/.h265 payload file")
    parser.add_argument("index", help="the matching .idx")
    parser.add_argument("out", help="the .wave to write")
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--height", type=int, required=True)
    parser.add_argument("--fps", type=int, default=72)
    parser.add_argument("--chroma", choices=("420", "444"), default="444")
    parser.add_argument("--limited-range", action="store_true")
    args = parser.parse_args(argv)

    frames = convert(
        args.payload,
        args.index,
        args.out,
        args.width,
        args.height,
        fps_num=args.fps,
        chroma=CHROMA_444 if args.chroma == "444" else CHROMA_420,
        full_range=not args.limited_range,
    )
    print(f"wrote {frames} frame(s) to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
