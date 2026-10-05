// Copyright (c) 2026 Quest3-Pyrowave contributors
// SPDX-License-Identifier: MIT
//
// Seven-segment clock stamp for camera-based optical latency measurement.
// The server draws the composition time (milliseconds of the Windows
// performance counter, modulo 100000) into each eye just before encoding.
// tools/quest3/latency_clock.py shows the same clock on a PC monitor, so one
// slow-motion video of the monitor and a headset lens gives
// latency = monitor value - lens value (+ the monitor's own display lag).
// Two square markers show frame-counter bits 0 and 1, which makes repeated or
// skipped frames visible on video. Header-only; fetch_sources.sh copies it
// next to FrameRender.cpp.
#pragma once

#include <cstdint>

namespace q3pw_latency {

struct Rect {
    int32_t left, top, right, bottom;
};

constexpr int kDigits = 5;
constexpr uint32_t kModulo = 100000;
constexpr int kMarkers = 2;
constexpr int kMaxLit = kDigits * 7 + kMarkers;

// Segment bits a..g = 1, 2, 4, 8, 16, 32, 64: top, upper right, lower right,
// bottom, lower left, upper left, middle.
constexpr uint8_t kSegments[10] = {0x3F, 0x06, 0x5B, 0x4F, 0x66, 0x6D, 0x7D, 0x07, 0x7F, 0x6F};

struct Layout {
    int32_t digit_w, digit_h, thickness, gap, pad, marker;
    int32_t width() const { return 2 * pad + kDigits * digit_w + kDigits * gap + kMarkers * marker + (kMarkers - 1) * gap; }
    int32_t height() const { return 2 * pad + digit_h; }
};

// About 4.5% of the eye height per digit: ~99 px at 2208, roughly 3 degrees in a Quest 3 lens.
inline Layout layout_for_eye(uint32_t eye_h) {
    Layout l;
    l.digit_h = static_cast<int32_t>(eye_h) * 9 / 200;
    if (l.digit_h < 16) l.digit_h = 16;
    l.thickness = l.digit_h / 8 > 2 ? l.digit_h / 8 : 2;
    l.digit_w = l.digit_h * 11 / 20;
    l.gap = 2 * l.thickness;
    l.pad = 2 * l.thickness;
    l.marker = l.digit_h / 2;
    return l;
}

struct Stamp {
    Rect background;       // drawn black first
    Rect lit[kMaxLit];     // then drawn white
    int lit_count;
    int32_t digit_x[kDigits];
    int32_t digit_y;
    Layout layout;
};

inline uint32_t stamp_value(uint64_t milliseconds) { return static_cast<uint32_t>(milliseconds % kModulo); }

inline void digit_segments(const Layout& l, int32_t x, int32_t y, int digit, Rect* out, int& count) {
    const int32_t w = l.digit_w, h = l.digit_h, t = l.thickness, half = h / 2;
    const Rect all[7] = {
        {x, y, x + w, y + t},                           // a
        {x + w - t, y, x + w, y + half},                // b
        {x + w - t, y + half, x + w, y + h},            // c
        {x, y + h - t, x + w, y + h},                   // d
        {x, y + half, x + t, y + h},                    // e
        {x, y, x + t, y + half},                        // f
        {x, y + half - t / 2, x + w, y + half - t / 2 + t}, // g
    };
    for (int s = 0; s < 7; s++)
        if (kSegments[digit] & (1u << s)) out[count++] = all[s];
}

// Centered horizontally in the eye, just below its vertical center, inside
// every fixed foveation profile's full-resolution center region.
inline Stamp make_stamp(int32_t eye_x, int32_t eye_y, uint32_t eye_w, uint32_t eye_h,
                        uint64_t milliseconds, uint64_t frame) {
    Stamp s{};
    s.layout = layout_for_eye(eye_h);
    const Layout& l = s.layout;
    const int32_t x0 = eye_x + (static_cast<int32_t>(eye_w) - l.width()) / 2;
    const int32_t y0 = eye_y + static_cast<int32_t>(eye_h) / 2 + l.digit_h / 2;
    s.background = {x0, y0, x0 + l.width(), y0 + l.height()};
    uint32_t value = stamp_value(milliseconds);
    int32_t x = x0 + l.pad;
    s.digit_y = y0 + l.pad;
    s.lit_count = 0;
    for (int i = 0; i < kDigits; i++) {
        s.digit_x[i] = x;
        x += l.digit_w + l.gap;
    }
    for (int i = kDigits - 1; i >= 0; i--) {
        digit_segments(l, s.digit_x[i], s.digit_y, static_cast<int>(value % 10), s.lit, s.lit_count);
        value /= 10;
    }
    const int32_t my = s.digit_y + (l.digit_h - l.marker) / 2;
    for (int m = 0; m < kMarkers; m++) {
        if (frame & (1ull << m)) s.lit[s.lit_count++] = {x, my, x + l.marker, my + l.marker};
        x += l.marker + l.gap;
    }
    return s;
}

} // namespace q3pw_latency
