// SPDX-License-Identifier: MIT
#pragma once
#include <algorithm>
#include <array>
#include <cmath>
#include <mutex>

// One gaze update is one binocular layout. Reading four independent atomics can
// combine two samples during a saccade; publish and snapshot all components together.
class FoveationCenterState {
public:
    void publish(std::array<float, 4> value) {
        for (float &v : value) v = std::isfinite(v) ? std::clamp(v, -0.9f, 0.9f) : 0.0f;
        std::lock_guard<std::mutex> lock(mutex_);
        center_ = value;
    }
    std::array<float, 4> snapshot() const {
        std::lock_guard<std::mutex> lock(mutex_);
        return center_;
    }
private:
    mutable std::mutex mutex_;
    std::array<float, 4> center_{};
};
