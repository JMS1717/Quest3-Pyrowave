// Copyright (c) 2026 Quest3-Pyrowave contributors
// SPDX-License-Identifier: MIT
#include "surface_chart_pixels.h"
#include <array>
#include <cassert>

int main() {
    constexpr uint32_t w = 64, h = 32;
    for (bool bgra : {false, true}) {
        std::array<uint8_t, w*h*4 + 2> storage{};
        storage.front() = 17; storage.back() = 29;
        auto pixels = storage.data() + 1;
        fill_surface_chart(pixels, w, h, bgra);
        auto visible = [&](uint32_t x, uint32_t y) {
            const auto p = pixels + ((h-1-y)*w+x)*4;
            return std::array<uint8_t,4>{p[bgra ? 2 : 0], p[1], p[bgra ? 0 : 2], p[3]};
        };
        // Expected colors are defined in displayed top-left coordinates,
        // independent of byte order; a straight top-down upload fails these.
        assert((visible(0,0) == std::array<uint8_t,4>{255,255,255,255}));
        assert((visible(w-1,0) == std::array<uint8_t,4>{255,255,0,255}));
        assert((visible(0,h-1) == std::array<uint8_t,4>{0,255,255,255}));
        assert((visible(w-1,h-1) == std::array<uint8_t,4>{255,0,255,255}));
        assert((visible(w/4,h/4) == std::array<uint8_t,4>{190,20,20,255}));
        assert((visible(w*3/4,h/4) == std::array<uint8_t,4>{20,190,20,255}));
        for (uint32_t x=0; x<w; ++x) assert((visible(x,h/2) == std::array<uint8_t,4>{0,0,0,255}));
        for (uint32_t y=0; y<h; ++y) assert((visible(w/2,y) == std::array<uint8_t,4>{0,0,0,255}));
        for (size_t i=3; i<w*h*4; i+=4) assert(pixels[i] == 255);
        assert(storage.front() == 17 && storage.back() == 29);
    }
}
