"""Drives driver/test/stream_test.exe and checks that xrwired::Stream is byte-compatible with
tools/xrbench/rawpipe.py (the python sender the headset client already speaks to).

    python stream_test.py --exe stream_test.exe --xrbench <workspace> [--codec 3]
                          [--rounds 2] [--scale 8192]

The exe listens; this script is the headset side: it verifies the 20-byte "XRW2" header against
rawpipe.stream_header(), verifies every [u32 length][u64 pts_us][payload] record against
rawpipe.frame_record(), acks each frame with a kind-1 (decoded) and a kind-3 (photons, + i64 ns
until display) record in the format rawpipe.read_acks() parses, and then checks the stats the exe
prints. Exit code 0 = pass.
"""
import argparse
import socket
import struct
import subprocess
import sys
import threading
import time

WIDTH, HEIGHT, FPS = 3264, 1408, 72
AHEAD_NS = 11_000_000          # what a VR client would report as "ns from this ack until photons"

failures = []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{(' - ' + detail) if detail else ''}")
    if not ok:
        failures.append(name)


def payload_length(i, scale=1):
    return (64 + i * 7) * scale


def payload_byte(i):
    return (i * 31 + 7) & 0xff


def read_exactly(sock, count):
    buf = b""
    while len(buf) < count:
        chunk = sock.recv(count - len(buf))
        if not chunk:
            raise EOFError(f"stream closed after {len(buf)}/{count} bytes")
        buf += chunk
    return buf


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", required=True)
    parser.add_argument("--xrbench", required=True, help="folder containing the xrbench package")
    parser.add_argument("--port", type=int, default=45177)
    parser.add_argument("--frames", type=int, default=12)
    parser.add_argument("--codec", type=int, default=3)
    parser.add_argument("--rounds", type=int, default=2, help="connect, stream, disconnect, reconnect")
    parser.add_argument("--scale", type=int, default=1, help="payload multiplier (big access units)")
    parser.add_argument("--stale-acks", type=int, default=0,
                        help="also ack pts 0 at the end of each round; with --frames > 256 that pts has "
                             "been evicted from the bounded send-time map, so it must not skew the latency")
    args = parser.parse_args()

    sys.path.insert(0, args.xrbench)
    from xrbench import rawpipe                      # the reference implementation of the wire format

    codec_name = {v: k for k, v in rawpipe.CODEC_IDS.items()}[args.codec]
    proc = subprocess.Popen([args.exe, str(args.port), str(args.frames), str(args.codec), str(args.rounds), str(args.scale)],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    out_lines = []
    threading.Thread(target=lambda: [out_lines.append(l.rstrip()) for l in proc.stdout],
                     daemon=True).start()

    expected_header = b"XRW2" + struct.pack(">IIII", WIDTH, HEIGHT, FPS, args.codec)
    # rawpipe only emits "XRW1" for the AVC name; every other codec id is the same 20-byte XRW2 header
    # the driver always sends (the receiver reads the codec id whenever the magic is XRW2).
    reference_header = expected_header if codec_name == "avc" else \
        rawpipe.stream_header(WIDTH, HEIGHT, FPS, codec_name)

    def wait_for_line(prefix, limit=15.0):
        deadline = time.time() + limit
        while time.time() < deadline:
            if any(line.startswith(prefix) for line in out_lines):
                return True
            time.sleep(0.02)
        return False

    for round_index in range(1, args.rounds + 1):
        print(f"round {round_index}: connect, header, {args.frames} frames, acks")
        if round_index > 1:
            # Let the exe notice the disconnect (and try to send into the gap) before reconnecting.
            check(f"round {round_index}: the exe saw the client go away",
                  wait_for_line(f"drop_between_rounds{round_index}="))
        sock = None
        for _ in range(100):                         # the exe needs a moment to bind / re-accept
            try:
                sock = socket.create_connection(("127.0.0.1", args.port), timeout=5)
                break
            except OSError:
                time.sleep(0.1)
        if sock is None:
            proc.kill()
            print("could not connect to the exe")
            print("\n".join(out_lines))
            return 1
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

        header = read_exactly(sock, 20)
        # For "avc" rawpipe still emits the legacy 16-byte XRW1 header, so there the reference is the
        # XRW2 form stream.h specifies (which the receiver parses the same way).
        source = "rawpipe.stream_header" if codec_name != "avc" else "the XRW2 form of rawpipe's header"
        check(f"round {round_index} header matches {source}({codec_name})",
              header == reference_header, f"got {header.hex()} want {reference_header.hex()}")
        check(f"round {round_index} header is XRW2 + w/h/fps/codec big-endian",
              header == expected_header, header.hex())

        bad_records = []
        for i in range(args.frames):
            head = read_exactly(sock, 12)
            length, pts = struct.unpack(">IQ", head)
            want_length, want_pts = payload_length(i, args.scale), i * 1_000_000 // FPS
            body = read_exactly(sock, length)
            want = rawpipe.frame_record(bytes([payload_byte(i)]) * want_length, want_pts)
            if head + body != want:
                bad_records.append(f"frame {i}: len {length}/{want_length} pts {pts}/{want_pts}")
            sock.sendall(struct.pack(">BQ", rawpipe.ACK_DECODED, pts))              # kind 1
            sock.sendall(struct.pack(">BQq", rawpipe.ACK_PHOTONS, pts, AHEAD_NS))   # kind 3 + i64
        check(f"round {round_index} records equal rawpipe.frame_record(payload, pts)",
              not bad_records, "; ".join(bad_records[:3]))
        if args.stale_acks:
            sock.sendall(struct.pack(">BQ", rawpipe.ACK_DECODED, 0))   # long-evicted pts
        time.sleep(0.5)                              # let the exe log the round before we disconnect
        sock.close()

    proc.wait(timeout=30)
    stats = {}
    for line in out_lines:
        for field in line.split():
            key, _, value = field.partition("=")
            if value:
                stats[key] = value
    print("exe output:")
    for line in out_lines:
        print("   ", line)

    print("exe stats:")
    check("exe finished cleanly", proc.returncode == 0 and stats.get("result") == "done",
          f"rc={proc.returncode} result={stats.get('result')}")
    check("send_frame without a client returns false", stats.get("drop_without_client") == "1")
    check("dropped frame is not counted in the stats", stats.get("drop_stats_clean") == "1")
    total = args.frames * args.rounds
    for r in range(1, args.rounds + 1):
        check(f"round {r}: a fresh client sets the keyframe request", stats.get(f"keyframe_requested{r}") == "1")
        check(f"round {r}: take_keyframe_request clears the flag", stats.get(f"keyframe_cleared{r}") == "1")
        check(f"round {r}: send_frame returned true for every frame",
              stats.get(f"send_frame_true{r}") == str(args.frames))
        if r > 1:
            check(f"round {r}: the frame sent while disconnected was dropped",
                  stats.get(f"drop_between_rounds{r}") == "1")
    check("frames_sent counts the frames", stats.get("frames_sent") == str(total))
    wire_bytes = sum(12 + payload_length(i, args.scale) for i in range(args.frames)) * args.rounds
    check("bytes_sent counts the bytes on the wire", stats.get("bytes_sent") == str(wire_bytes),
          f"got {stats.get('bytes_sent')} want {wire_bytes}")
    decoded_total = total + args.stale_acks * args.rounds
    check("acks_decoded counts the kind-1 acks", stats.get("acks_decoded") == str(decoded_total))
    check("the ack handler saw every ack",
          stats.get("cb_decoded") == str(decoded_total) and stats.get("cb_photons") == str(total)
          and stats.get("cb_displayed") == "0")
    check("the kind-3 ack carried the i64 ns-until-display", stats.get("last_ahead_ns") == str(AHEAD_NS))
    check("the last ack pts is the last frame's pts",
          stats.get("last_ack_pts") == str(0 if args.stale_acks else (args.frames - 1) * 1_000_000 // FPS))

    decoded_ms = float(stats.get("last_send_to_decoded_ms", 0))
    photons_ms = float(stats.get("last_send_to_photons_ms", 0))
    check("last_send_to_decoded_ms is sane for a loopback ack", 0 < decoded_ms < 100, f"{decoded_ms:.3f} ms")
    check("last_send_to_photons_ms = send->ack + the reported ns until display",
          abs(photons_ms - decoded_ms - AHEAD_NS / 1e6) < 5,
          f"{photons_ms:.3f} ms vs {decoded_ms:.3f} + {AHEAD_NS / 1e6:.1f} ms")
    check("stop() is idempotent and drops the client", stats.get("stopped_twice") == "1"
          and stats.get("connected_after_stop") == "0" and stats.get("drop_after_stop") == "1")

    print(("FAILED: " + ", ".join(failures)) if failures else "ALL CHECKS PASSED")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
