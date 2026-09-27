// Standalone round-trip test for xrwired::Stream: listens on a port, waits for the python harness
// (driver/test/stream_test.py) to connect, sends a deterministic set of access units, and prints the
// stats it computed from the harness's acks. Everything the harness checks is printed as key=value.
//
//   stream_test.exe <port> <frames> <codec_id> [rounds] [payload scale]
//
// With rounds > 1 the harness disconnects and reconnects between rounds, which exercises the accept
// loop going back to accepting (and asking for a fresh keyframe) after a client drops.
//
// Printing to stdout is fine here (this is an exe, not the driver DLL); stream.cpp itself only logs
// through OutputDebugStringA / XRWIRED_STREAM_LOG.
#include "../src/stream.h"

#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <thread>
#include <vector>

using namespace std::chrono_literals;

namespace {

constexpr int kWidth = 3264;
constexpr int kHeight = 1408;
constexpr int kFps = 72;

// The harness rebuilds these two rules, so both sides agree on every byte without a handshake.
// `scale` blows the access units up to megabytes, which exercises the partial-send loop.
size_t payload_scale = 1;
size_t payload_length(int frame) { return (64 + static_cast<size_t>(frame) * 7) * payload_scale; }
uint8_t payload_byte(int frame) { return static_cast<uint8_t>((frame * 31 + 7) & 0xff); }

bool wait_for(const std::function<bool()>& done, std::chrono::milliseconds limit) {
    auto deadline = std::chrono::steady_clock::now() + limit;
    while (std::chrono::steady_clock::now() < deadline) {
        if (done()) return true;
        std::this_thread::sleep_for(2ms);
    }
    return done();
}

}  // namespace

int main(int argc, char** argv) {
    int port = argc > 1 ? std::atoi(argv[1]) : 45199;
    int frames = argc > 2 ? std::atoi(argv[2]) : 12;
    int codec_id = argc > 3 ? std::atoi(argv[3]) : 3;
    int rounds = argc > 4 ? std::atoi(argv[4]) : 1;
    payload_scale = argc > 5 ? static_cast<size_t>(std::atoi(argv[5])) : 1;

    xrwired::Stream stream;
    std::atomic<int> decoded_acks{0}, displayed_acks{0}, photon_acks{0};
    std::atomic<long long> last_ahead_ns{0};
    std::atomic<unsigned long long> last_ack_pts{0};
    stream.on_ack([&](int kind, uint64_t pts, int64_t ahead_ns) {
        if (kind == 1) decoded_acks++;
        else if (kind == 2) displayed_acks++;
        else if (kind == 3) { photon_acks++; last_ahead_ns = ahead_ns; }
        last_ack_pts = pts;
    });

    if (!stream.listen(port, kWidth, kHeight, kFps, codec_id)) {
        std::printf("result=listen_failed\n");
        return 2;
    }
    std::printf("listening=%d width=%d height=%d fps=%d codec=%d\n", port, kWidth, kHeight, kFps, codec_id);
    std::fflush(stdout);

    // No client yet: a frame must be dropped, not queued, and must not count in the stats.
    std::vector<uint8_t> dropped(128, 0xab);
    bool drop_returned_false = !stream.send_frame(dropped.data(), dropped.size(), 1);
    bool drop_kept_stats_clean = stream.stats().frames_sent == 0 && stream.stats().bytes_sent == 0;
    std::printf("drop_without_client=%d drop_stats_clean=%d\n", drop_returned_false ? 1 : 0,
                drop_kept_stats_clean ? 1 : 0);
    std::fflush(stdout);

    for (int round = 1; round <= rounds; ++round) {
        if (round > 1) {
            // The harness dropped the connection: the accept loop must let go and take a new client.
            if (!wait_for([&] { return !stream.client_connected(); }, 10s)) {
                std::printf("result=client_never_dropped\n");
                stream.stop();
                return 4;
            }
            std::vector<uint8_t> orphan(64, 0xcd);
            std::printf("drop_between_rounds%d=%d\n", round,
                        stream.send_frame(orphan.data(), orphan.size(), 3) ? 0 : 1);
            std::fflush(stdout);
        }
        if (!wait_for([&] { return stream.client_connected(); }, 20s)) {
            std::printf("result=no_client\n");
            stream.stop();
            return 3;
        }
        // A fresh client needs an IDR; the flag must clear after it is taken.
        bool keyframe_requested = stream.take_keyframe_request();
        bool keyframe_cleared = !stream.take_keyframe_request();
        std::printf("keyframe_requested%d=%d keyframe_cleared%d=%d\n", round, keyframe_requested ? 1 : 0,
                    round, keyframe_cleared ? 1 : 0);
        std::fflush(stdout);

        int sent = 0;
        for (int i = 0; i < frames; ++i) {
            std::vector<uint8_t> au(payload_length(i), payload_byte(i));
            uint64_t pts = static_cast<uint64_t>(i) * 1000000ull / kFps;
            if (stream.send_frame(au.data(), au.size(), pts)) sent++;
            std::this_thread::sleep_for(5ms);
        }
        std::printf("send_frame_true%d=%d\n", round, sent);
        std::fflush(stdout);

        wait_for([&] { return decoded_acks.load() >= frames * round && photon_acks.load() >= frames * round; }, 5s);
        std::this_thread::sleep_for(200ms);   // let the very last ack land in the stats
    }

    xrwired::StreamStats s = stream.stats();
    std::printf("frames_sent=%llu bytes_sent=%llu acks_decoded=%llu\n",
                static_cast<unsigned long long>(s.frames_sent),
                static_cast<unsigned long long>(s.bytes_sent),
                static_cast<unsigned long long>(s.acks_decoded));
    std::printf("last_send_to_decoded_ms=%.3f last_send_to_photons_ms=%.3f\n",
                s.last_send_to_decoded_ms, s.last_send_to_photons_ms);
    std::printf("cb_decoded=%d cb_displayed=%d cb_photons=%d last_ahead_ns=%lld last_ack_pts=%llu\n",
                decoded_acks.load(), displayed_acks.load(), photon_acks.load(),
                static_cast<long long>(last_ahead_ns.load()),
                static_cast<unsigned long long>(last_ack_pts.load()));
    std::fflush(stdout);

    stream.stop();
    stream.stop();                        // idempotent
    bool connected_after_stop = stream.client_connected();
    bool drop_after_stop = !stream.send_frame(dropped.data(), dropped.size(), 2);
    std::printf("stopped_twice=1 connected_after_stop=%d drop_after_stop=%d\n",
                connected_after_stop ? 1 : 0, drop_after_stop ? 1 : 0);
    std::printf("result=done\n");
    std::fflush(stdout);
    return 0;
}
