// Copyright (c) 2026 Quest3-Pyrowave contributors
// SPDX-License-Identifier: MIT
// Rasterizes the latency stamp and reads it back, as a camera frame would be read.
#include "latency_stamp.h"
#include <cassert>
#include <cstdio>
#include <vector>

using namespace q3pw_latency;

namespace {

struct Canvas {
    int32_t w, h;
    std::vector<uint8_t> px;  // 0 untouched, 1 black, 2 white
    Canvas(int32_t w_, int32_t h_) : w(w_), h(h_), px(size_t(w_) * size_t(h_)) {}
    void fill(const Rect& r, uint8_t v) {
        assert(r.left >= 0 && r.top >= 0 && r.right <= w && r.bottom <= h);
        assert(r.left < r.right && r.top < r.bottom);
        for (int32_t y = r.top; y < r.bottom; y++)
            for (int32_t x = r.left; x < r.right; x++) px[size_t(y) * size_t(w) + size_t(x)] = v;
    }
    uint8_t at(int32_t x, int32_t y) const { return px[size_t(y) * size_t(w) + size_t(x)]; }
};

bool inside(const Rect& inner, const Rect& outer) {
    return inner.left >= outer.left && inner.top >= outer.top && inner.right <= outer.right && inner.bottom <= outer.bottom;
}

// Reads one digit from the segment centers; -1 if the pattern is not a digit.
int read_digit(const Canvas& c, const Layout& l, int32_t x, int32_t y) {
    const int32_t w = l.digit_w, h = l.digit_h, t = l.thickness;
    const int32_t probes[7][2] = {
        {x + w / 2, y + t / 2}, {x + w - 1 - t / 2, y + h / 4}, {x + w - 1 - t / 2, y + 3 * h / 4},
        {x + w / 2, y + h - 1 - t / 2}, {x + t / 2, y + 3 * h / 4}, {x + t / 2, y + h / 4}, {x + w / 2, y + h / 2},
    };
    uint8_t mask = 0;
    for (int s = 0; s < 7; s++)
        if (c.at(probes[s][0], probes[s][1]) == 2) mask |= uint8_t(1u << s);
    for (int d = 0; d < 10; d++)
        if (kSegments[d] == mask) return d;
    return -1;
}

void check_eye(int32_t eye_x, uint32_t eye_w, uint32_t eye_h, uint64_t ms, uint64_t frame) {
    Canvas c(eye_x + int32_t(eye_w), int32_t(eye_h));
    const Stamp s = make_stamp(eye_x, 0, eye_w, eye_h, ms, frame);
    const Rect eye = {eye_x, 0, eye_x + int32_t(eye_w), int32_t(eye_h)};
    assert(inside(s.background, eye));
    // Inside the smallest full-resolution center (Strong: 60% of each axis).
    const Rect center = {eye_x + int32_t(eye_w) / 5, int32_t(eye_h) / 5, eye_x + int32_t(eye_w) * 4 / 5, int32_t(eye_h) * 4 / 5};
    assert(inside(s.background, center));
    c.fill(s.background, 1);
    for (int i = 0; i < s.lit_count; i++) {
        assert(inside(s.lit[i], s.background));
        c.fill(s.lit[i], 2);
    }
    uint32_t value = 0;
    for (int i = 0; i < kDigits; i++) {
        const int d = read_digit(c, s.layout, s.digit_x[i], s.digit_y);
        assert(d >= 0);
        value = value * 10 + uint32_t(d);
    }
    assert(value == stamp_value(ms));
    int32_t mx = s.digit_x[kDigits - 1] + s.layout.digit_w + s.layout.gap;
    const int32_t my = s.digit_y + s.layout.digit_h / 2;
    for (int m = 0; m < kMarkers; m++) {
        assert((c.at(mx + s.layout.marker / 2, my) == 2) == bool(frame & (1ull << m)));
        mx += s.layout.marker + s.layout.gap;
    }
    // Black gaps between digits keep neighbours separable on camera.
    for (int i = 0; i + 1 < kDigits; i++)
        for (int32_t y = s.digit_y; y < s.digit_y + s.layout.digit_h; y++)
            assert(c.at(s.digit_x[i] + s.layout.digit_w + s.layout.gap / 2, y) == 1);
}

} // namespace

int main() {
    assert(stamp_value(123456789) == 56789);
    assert(stamp_value(99999) == 99999 && stamp_value(100000) == 0);
    const Layout native = layout_for_eye(2208);
    assert(native.digit_h == 99 && native.thickness == 12 && native.width() == 560);
    const uint32_t sizes[][2] = {{512, 544}, {1664, 1760}, {2080, 2208}, {3072, 3216}, {4096, 4096}};
    for (const auto& size : sizes)
        for (uint64_t ms : {0ull, 7ull, 1234567ull, 88888ull, 4294967296123ull})
            for (uint64_t frame = 0; frame < 4; frame++) {
                check_eye(0, size[0], size[1], ms, frame);
                check_eye(int32_t(size[0]), size[0], size[1], ms, frame);  // right half of a stereo texture
            }
    for (uint64_t ms = 0; ms < 100000; ms += 7) check_eye(0, 2080, 2208, ms, ms);
    printf("latency stamp layout: ok\n");
    return 0;
}
