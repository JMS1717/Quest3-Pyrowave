// Copyright (c) 2026 Quest3-Pyrowave contributors
// SPDX-License-Identifier: MIT
#pragma once
#include <cstddef>
#include <cstdint>

// The chart belongs to the existing GLES-bound OpenXR session. Its .40
// compositor capture interpreted row zero as the bottom, despite the Vulkan
// producer. Keep this correction local to that diagnostic/session binding.
// A future Vulkan-bound session must establish its own orientation contract.
inline void fill_surface_chart(uint8_t *pixels, uint32_t width, uint32_t height, bool bgra) {
    for (uint32_t y = 0; y < height; ++y) for (uint32_t x = 0; x < width; ++x) {
        const bool left = x < width / 2;
        uint8_t r = left ? 190 : 20, g = left ? 20 : 190, b = 20;
        const bool edge_x = x < width / 8 || x >= width * 7 / 8;
        if (edge_x && y < height / 4) { r = 255; g = 255; b = left ? 255 : 0; }
        if (edge_x && y >= height * 3 / 4) { r = left ? 0 : 255; g = left ? 255 : 0; b = 255; }
        if (x == width / 2 || y == height / 2) r = g = b = 0;
        const size_t offset = (size_t(height - 1 - y) * width + x) * 4;
        pixels[offset] = bgra ? b : r;
        pixels[offset + 1] = g;
        pixels[offset + 2] = bgra ? r : b;
        pixels[offset + 3] = 255;
    }
}
