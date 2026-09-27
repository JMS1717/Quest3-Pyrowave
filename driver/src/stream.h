// Network side of the SteamVR driver: the same wire format the benchmark sender uses, so the headset
// client (com.xrwired.receiverxr) needs no changes.
//
//   header:  "XRW2" u32 width u32 height u32 fps u32 codec      (codec 0 = AVC, 3/4 = HEVC 8/10-bit)
//   frame:   u32 length u64 pts_us, then the Annex B access unit
//   acks:    u8 kind u64 pts_us  (kind 1 = decoded, 2 = displayed), kind 3 adds i64 ns-until-display
//
// One client at a time. The driver keeps encoding regardless; when nobody is connected, frames are
// dropped rather than queued, because a stale VR frame is worthless.
#pragma once

#include <cstdint>
#include <functional>
#include <string>

namespace xrwired {

struct StreamStats {
    uint64_t frames_sent = 0;
    uint64_t bytes_sent = 0;
    uint64_t acks_decoded = 0;
    double last_send_to_decoded_ms = 0;   // from handing the frame to the socket to its decode ack
    double last_send_to_photons_ms = 0;   // ... to the client's reported display time
};

class Stream {
public:
    ~Stream();

    /** Starts listening on `port` and accepting one client at a time, in the background. */
    bool listen(int port, int width, int height, int fps, int codec_id);

    /** Instead of waiting, dial the headset (which listens) and keep retrying until it answers. */
    bool connect_to(const std::string& host, int port, int width, int height, int fps, int codec_id);
    bool client_connected() const;

    /** Sends one access unit. Returns false when there is no client (the frame is dropped). */
    bool send_frame(const uint8_t* data, size_t length, uint64_t pts_us);

    /** Called on the ack thread for every ack: kind, pts, and for kind 3 the ns until display. */
    void on_ack(std::function<void(int kind, uint64_t pts_us, int64_t ahead_ns)> handler);

    /** Called for every head pose the client sends: its id, then orientation (x,y,z,w), position,
     *  both eyes' fov angles (left, right, up, down), and the head's linear and angular velocity -
     *  21 floats in that order. */
    void on_pose(std::function<void(uint64_t id, const float values[21])> handler);

    /** Called for every hand-joints record: pose id, left/right active flags, then 2*26*8 floats
     *  (left hand then right hand; each of 26 joints = px,py,pz, qx,qy,qz,qw, radius). */
    void on_hands(std::function<void(uint64_t id, bool left, bool right, const float joints[416])> handler);

    /** True when the client asked for a keyframe (set after a reconnect). Clears the flag. */
    bool take_keyframe_request();

    StreamStats stats() const;
    void stop();

private:
    struct Impl;
    Impl* impl_ = nullptr;
};

}  // namespace xrwired
