// NVENC encoder for the SteamVR driver: a D3D11 texture in, one Annex B access unit out.
//
// The driver hands us the frame SteamVR just rendered (a shared D3D11 texture), so the pixels never
// leave the GPU before NVENC reads them. Settings follow what the headset measured best: H.264 with
// CAVLC (its decoder caps CABAC around 355 Mbps), the low-latency tuning, CBR with a one-frame VBV,
// no B frames, and an IDR whenever the client asks for one.
#pragma once

#include <d3d11.h>

#include <cstdint>
#include <string>
#include <vector>

namespace xrwired {

struct EncoderConfig {
    int width = 3264;          // both eyes side by side
    int height = 1408;
    int fps = 72;
    int mbps = 400;
    bool hevc = false;         // false = H.264 CAVLC (fastest to decode), true = HEVC (10-bit capable)
    bool ten_bit = false;      // HEVC only
    int preset = 1;            // NVENC P1..P7
    std::string tuning = "ll";  // "ll", "ull", "hq"
};

class Encoder {
public:
    ~Encoder();

    /** Opens an NVENC session on the given device. Returns false and fills error() on failure. */
    bool init(ID3D11Device* device, const EncoderConfig& config);

    /** Encodes one frame. `texture` must be config.width x config.height, on the same device.
     *  The access unit (with parameter sets on IDR frames) is appended to `out`. */
    bool encode(ID3D11Texture2D* texture, uint64_t pts_us, bool force_idr, std::vector<uint8_t>* out);

    /** Milliseconds spent inside the last encode() call. */
    double last_encode_ms() const { return last_encode_ms_; }

    const std::string& error() const { return error_; }
    void shutdown();

private:
    struct Impl;
    Impl* impl_ = nullptr;
    double last_encode_ms_ = 0;
    std::string error_;
};

}  // namespace xrwired
