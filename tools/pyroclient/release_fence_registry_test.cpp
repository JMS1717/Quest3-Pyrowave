#include "release_fence_registry.h"
#include <cassert>
#include <vector>
#include <algorithm>

static std::vector<int> closed;
static void fake_close(int fd) { closed.push_back(fd); }
int main() {
    int buffers[3]{};
    uint64_t first = 0;
    {
        ReleaseFenceRegistry r(fake_close);
        for (int &buffer : buffers) assert(r.add(&buffer));
        assert(!r.add(&buffers[0]));
        first = r.publish(&buffers[0]);
        assert(first && r.token(&buffers[0]) == first);
        assert(r.attach(&buffers[0], first, 10));
        assert(r.attach(&buffers[0], first, 11));
        assert(closed == std::vector<int>{10});
        assert(r.begin_reuse(&buffers[0]) == 11); // FD ownership transfers to caller
        assert(!r.attach(&buffers[0], first, 12)); // writing, stale consumer rejected
        auto next = r.publish(&buffers[0]);
        assert(next != first);
        assert(!r.attach(&buffers[0], first, 13));
        assert(r.attach(&buffers[0], next, 14));
        assert(r.publish(&buffers[0]) == 0); // outstanding read fence cannot disappear
        r.remove(&buffers[0]);
        assert(!r.attach(&buffers[0], next, 15));
        assert(r.add(&buffers[0])); // same address, new allocation
        auto replacement = r.publish(&buffers[0]);
        assert(replacement != next);
        assert(!r.attach(&buffers[0], next, 16));
        assert(r.attach(&buffers[0], replacement, 17));
        for (int i = 1; i < 3; ++i) {
            auto token = r.publish(&buffers[i]);
            assert(r.attach(&buffers[i], token, 17 + i));
        }
    }
    std::sort(closed.begin(), closed.end());
    assert(closed == (std::vector<int>{10,12,13,14,15,16,17,18,19}));
    // Transferred FD11 was not closed by the registry; no other FD leaked or closed twice.
}
