// SPDX-License-Identifier: MIT
#pragma once
#include <cstdint>
#include <stdexcept>
#include <vector>

namespace q3pw {
// Diagnostic only, pinned PyroWave 8-byte little-endian packet header. Extend
// unused packet tails while preserving ballot, quantization, block and sequence
// fields and every original coefficient byte. No encoder/streaming mutation.
inline std::vector<unsigned char> padded_growth_frame(const std::vector<unsigned char>& frame) {
    constexpr size_t limit=256u*1024*1024;
    if (frame.empty() || frame.size()>limit/3) throw std::invalid_argument("growth fixture size");
    std::vector<unsigned char> out;
    size_t offset=0, added=0;
    while(offset<frame.size()) {
        if(frame.size()-offset<8) throw std::invalid_argument("truncated header");
        const uint16_t flags=uint16_t(frame[offset+2]) | (uint16_t(frame[offset+3])<<8);
        const bool sequence=(flags&0x8000)!=0;
        const size_t words=flags&0x0fff;
        const size_t bytes=sequence?8:words*4;
        if(!sequence && words<2) throw std::invalid_argument("short packet");
        if(bytes>frame.size()-offset) throw std::invalid_argument("truncated packet");
        const size_t old_size=out.size();
        out.insert(out.end(),frame.begin()+offset,frame.begin()+offset+bytes);
        if(!sequence && added<frame.size()*2) {
            const size_t extra=4095*4-bytes;
            out[old_size+2]=0xff;
            out[old_size+3]=static_cast<unsigned char>((flags>>8)|0x0f);
            if(extra>limit-out.size()) throw std::invalid_argument("growth exceeds bound");
            out.resize(out.size()+extra,0);
            added+=extra;
        }
        offset+=bytes;
    }
    if(added<frame.size()*2) throw std::invalid_argument("insufficient unused packet capacity");
    return out;
}
}
