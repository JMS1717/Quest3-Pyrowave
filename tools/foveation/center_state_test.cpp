#include "center_state.h"
#include <atomic>
#include <cassert>
#include <limits>
#include <thread>

int main() {
    FoveationCenterState state;
    std::atomic<bool> finished{false};
    std::thread writer([&] {
        for (int i = 0; i < 100000; i++) {
            float v = (i & 1) ? .75f : -.75f;
            state.publish({v, v, v, v});
        }
        finished.store(true);
    });
    do {
        auto c = state.snapshot();
        assert(c[0] == c[1] && c[1] == c[2] && c[2] == c[3]);
    } while (!finished.load());
    writer.join();
    state.publish({-2, 2, std::numeric_limits<float>::quiet_NaN(), std::numeric_limits<float>::infinity()});
    auto c = state.snapshot();
    assert(c[0] == -.9f && c[1] == .9f && c[2] == 0 && c[3] == 0);
}
