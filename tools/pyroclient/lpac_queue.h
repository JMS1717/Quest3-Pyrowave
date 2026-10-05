// Experimental Adreno LPAC (Low Priority Async Compute) decode queue, debug.q3pw.lpac=1.
// Qualcomm documents LPAC on A740 and newer: a queue from a family that supports compute and
// transfer but not graphics, created at VK_QUEUE_GLOBAL_PRIORITY_LOW, runs on a separate
// command processor concurrently with the graphics pipe instead of being preempted by it.
// https://docs.qualcomm.com/bundle/publicresource/80-78185-2/topics/mobile_best_practices.md
// Whether the Quest 3 driver exposes such a family is unverified; pyroclient logs the families
// ([Q3PW_GPU_CAPS]) and falls back to the existing queue with a reason when it cannot apply.
#pragma once
#include <cstdint>
#include <vector>

struct LpacFamily {
    uint32_t flags = 0;          // VkQueueFlags
    uint32_t count = 0;          // queueCount
    uint32_t timestamp_bits = 0; // timestampValidBits; decode/convert timing needs > 0
    int low_priority = -1;       // 1 LOW listed, 0 not listed, -1 unknown (no per-family query)
};

struct LpacChoice {
    bool active;
    uint32_t family;
    const char *reason; // "off" | "applied" | "no_global_priority" | "no_compute_only_family" | "no_timestamps" | "low_unsupported"
};

// Flag values match VK_QUEUE_GRAPHICS_BIT / VK_QUEUE_COMPUTE_BIT; kept literal so the host test
// needs no Vulkan headers.
inline LpacChoice choose_lpac_family(const std::vector<LpacFamily> &families, uint32_t graphics_family,
                                     bool requested, bool global_priority_extension) {
    constexpr uint32_t graphics = 0x1, compute = 0x2;
    if (!requested) return {false, graphics_family, "off"};
    if (!global_priority_extension) return {false, graphics_family, "no_global_priority"};
    const char *rejected = "no_compute_only_family";
    for (uint32_t i = 0; i < families.size(); i++) {
        const LpacFamily &f = families[i];
        if (i == graphics_family || (f.flags & graphics) || !(f.flags & compute) || !f.count) continue;
        // Timing is the point of the experiment; never trade it for an unmeasurable queue.
        if (!f.timestamp_bits) { rejected = "no_timestamps"; continue; }
        if (f.low_priority == 0) { rejected = "low_unsupported"; continue; }
        return {true, i, "applied"};
    }
    return {false, graphics_family, rejected};
}
