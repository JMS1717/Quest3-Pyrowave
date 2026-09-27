#!/usr/bin/env python3
"""Encode a video/test source and send framed H.264 access units to XR Wired Receiver."""
import argparse
import os
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path


def nal_units(stream):
    buf = bytearray()
    eof = False
    while not eof:
        chunk = stream.read(256 * 1024)
        if chunk:
            buf.extend(chunk)
        else:
            eof = True
        starts = []
        i = 0
        while i + 3 <= len(buf):
            if buf[i:i+3] == b"\x00\x00\x01":
                starts.append(i)
                i += 3
            elif i + 4 <= len(buf) and buf[i:i+4] == b"\x00\x00\x00\x01":
                starts.append(i)
                i += 4
            else:
                i += 1
        keep = len(starts) if eof else max(0, len(starts) - 1)
        for index in range(keep):
            end = starts[index + 1] if index + 1 < len(starts) else len(buf)
            yield bytes(buf[starts[index]:end])
        if keep:
            del buf[:starts[keep] if keep < len(starts) else len(buf)]
        elif eof:
            break


def nal_type(nal):
    offset = 4 if nal.startswith(b"\x00\x00\x00\x01") else 3
    return nal[offset] & 0x1f if len(nal) > offset else -1


def access_units(stream):
    current = bytearray()
    for nal in nal_units(stream):
        if nal_type(nal) == 9 and current:
            yield bytes(current)
            current.clear()
        current.extend(nal)
    if current:
        yield bytes(current)


def adb_path(environ=None):
    """adb from the Android SDK in ANDROID_HOME, else whatever adb is on the PATH."""
    environ = os.environ if environ is None else environ
    sdk = environ.get("ANDROID_HOME")
    return str(Path(sdk) / "platform-tools" / "adb") if sdk else "adb"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", nargs="?", help="video file; omit for a generated test pattern")
    parser.add_argument("--desktop", action="store_true", help="capture a live macOS desktop")
    parser.add_argument("--stereo-test", action="store_true",
                        help="send a side-by-side OpenXR test image (width is the combined width)")
    parser.add_argument("--screen", default="0", help="ScreenCaptureKit display index (default: 0)")
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--fps", type=int, default=72)
    parser.add_argument("--bitrate", default="20M")
    parser.add_argument("--port", type=int, default=45100)
    parser.add_argument("--no-adb-forward", action="store_true")
    args = parser.parse_args()

    if args.stereo_test:
        if args.input or args.desktop:
            parser.error("--stereo-test cannot be combined with --desktop or a video file")
        if args.width % 2:
            parser.error("--stereo-test width must be even")
        if args.port == 45100:
            args.port = 45101

    adb = adb_path()
    if not args.no_adb_forward:
        subprocess.run([adb, "forward", f"tcp:{args.port}", f"tcp:{args.port}"], check=True)
    if args.desktop and args.input:
        parser.error("choose either --desktop or a video file")
    if args.desktop:
        source = ["-f", "rawvideo", "-pixel_format", "bgra", "-video_size",
                  f"{args.width}x{args.height}", "-framerate", str(args.fps), "-i", "pipe:0"]
    elif args.input:
        source = ["-re", "-i", args.input]
    elif args.stereo_test:
        eye_width = args.width // 2
        stereo_graph = (f"testsrc2=size={eye_width}x{args.height}:rate={args.fps},split[left][right];"
                        "[right]hue=h=60[right_tinted];[left][right_tinted]hstack")
        source = ["-re", "-f", "lavfi", "-i", stereo_graph]
    else:
        source = ["-re", "-f", "lavfi", "-i", f"testsrc2=size={args.width}x{args.height}:rate={args.fps}"]
    video_filter = (f"scale={args.width}:{args.height}:force_original_aspect_ratio=decrease,"
                    f"pad={args.width}:{args.height}:(ow-iw)/2:(oh-ih)/2:black")
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", *source, "-an", "-vf",
               video_filter, "-r", str(args.fps), "-c:v", "libx264", "-preset", "ultrafast",
               "-tune", "zerolatency", "-pix_fmt", "yuv420p", "-b:v", args.bitrate,
               "-maxrate", args.bitrate, "-bufsize", "2M", "-g", str(args.fps), "-bf", "0",
               "-x264-params", "aud=1:repeat-headers=1:scenecut=0", "-f", "h264", "pipe:1"]
    print("Encoding:", " ".join(command), file=sys.stderr)
    capturer = None
    encoder_stdin = None
    if args.desktop:
        capture_binary = Path(__file__).with_name("mac_screen_capture")
        if not capture_binary.exists():
            raise SystemExit("Run tools/build_screen_capture.sh first")
        capturer = subprocess.Popen([str(capture_binary), str(args.width), str(args.height),
                                     str(args.fps), str(args.screen)], stdout=subprocess.PIPE)
        encoder_stdin = capturer.stdout
    encoder = subprocess.Popen(command, stdin=encoder_stdin, stdout=subprocess.PIPE)
    count = 0
    try:
        with socket.create_connection(("127.0.0.1", args.port), timeout=10) as sock:
            sock.settimeout(None)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            magic = b"XRS1" if args.stereo_test else b"XRW1"
            sock.sendall(magic + struct.pack(">III", args.width, args.height, args.fps))
            assert encoder.stdout is not None
            started = time.monotonic()
            for count, au in enumerate(access_units(encoder.stdout), 1):
                if args.desktop:
                    delay = started + (count - 1) / args.fps - time.monotonic()
                    if delay > 0:
                        time.sleep(delay)
                pts_us = (count - 1) * 1_000_000 // args.fps
                sock.sendall(struct.pack(">IQ", len(au), pts_us) + au)
                if count % args.fps == 0:
                    print(f"sent {count} access units", file=sys.stderr)
    except (BrokenPipeError, ConnectionError) as exc:
        print(f"stream stopped: {exc}", file=sys.stderr)
    finally:
        encoder.terminate()
        encoder.wait()
        if capturer is not None:
            capturer.terminate()
            capturer.wait()


if __name__ == "__main__":
    main()
