// Host test for the LPAC queue-family choice (no Vulkan, no Android).
//   c++ -std=c++17 lpac_queue_test.cpp -o lpac_queue_test && ./lpac_queue_test
#include "lpac_queue.h"
#include <cstdio>
#include <cstring>

static int failures = 0;
static void check(const std::vector<LpacFamily> &families, bool requested, bool ext,
                  bool want_active, uint32_t want_family, const char *want_reason) {
    const LpacChoice c = choose_lpac_family(families, 0, requested, ext);
    if (c.active != want_active || c.family != want_family || strcmp(c.reason, want_reason) != 0) {
        printf("FAIL requested=%d ext=%d -> active=%d family=%u (%s), want %d %u (%s)\n", requested, ext,
               c.active, c.family, c.reason, want_active, want_family, want_reason);
        failures++;
    }
}

int main() {
    const LpacFamily gfx = {0x1 | 0x2 | 0x4, 3, 64, 1};
    const LpacFamily lpac = {0x2 | 0x4, 1, 64, 1};
    const LpacFamily sparse = {0x8, 1, 0, -1};
    // Off unless asked; a request needs the global-priority extension.
    check({gfx, lpac}, false, true, false, 0, "off");
    check({gfx, lpac}, true, false, false, 0, "no_global_priority");
    // Single universal family (typical desktop, lavapipe, or an older Adreno driver).
    check({gfx}, true, true, false, 0, "no_compute_only_family");
    // Sparse-only families are not compute queues.
    check({gfx, sparse}, true, true, false, 0, "no_compute_only_family");
    // The documented A740 layout.
    check({gfx, lpac}, true, true, true, 1, "applied");
    check({gfx, sparse, lpac}, true, true, true, 2, "applied");
    // Unknown per-family priority support is attempted; device creation falls back on rejection.
    check({gfx, {0x2, 1, 64, -1}}, true, true, true, 1, "applied");
    // Rejections keep the most specific reason.
    check({gfx, {0x2, 1, 0, 1}}, true, true, false, 0, "no_timestamps");
    check({gfx, {0x2, 1, 64, 0}}, true, true, false, 0, "low_unsupported");
    check({gfx, {0x2, 0, 64, 1}}, true, true, false, 0, "no_compute_only_family");
    // A later usable family still wins over an earlier rejected one.
    check({gfx, {0x2, 1, 0, 1}, lpac}, true, true, true, 2, "applied");
    if (failures) return 1;
    printf("lpac_queue_test: all passed\n");
    return 0;
}
