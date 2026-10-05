// SPDX-License-Identifier: MIT
#pragma once
#include <array>
#include <cstdint>
#include <limits>

namespace q3pw {
// Exclusive producer owner. Output protection comes from one coherent consumer
// snapshot. A prepared slot remains reserved until submit or explicit abandon;
// GPU completion must be verified externally before complete_active(true).
class PrerecordState {
public:
    static constexpr uint32_t none = 3;
    struct Ticket { uint32_t slot = none; uint64_t generation = 0; };
    Ticket active{}, prepared{};
    bool poisoned = false;

    Ticket start(const std::array<bool, 3>& protected_slots) {
        if (poisoned || active.slot != none || prepared.slot != none) return {};
        active = reserve(protected_slots);
        return active;
    }
    Ticket prepare(const std::array<bool, 3>& protected_slots) {
        if (poisoned || active.slot == none || prepared.slot != none) return {};
        auto unavailable = protected_slots;
        unavailable[active.slot] = true;
        prepared = reserve(unavailable);
        return prepared;
    }
    bool complete_active(bool fence_verified) {
        if (poisoned || active.slot == none) return false;
        if (!fence_verified) { poisoned = true; return false; }
        active = {};
        return true;
    }
    // Input rejection before any command recording/submission is distinct from
    // observed GPU completion. Caller must never use this on submitted work.
    bool reject_unsubmitted_start(Ticket ticket) {
        if (poisoned || prepared.slot != none || ticket.slot != active.slot ||
            !ticket.generation || ticket.generation != active.generation) return false;
        active = {};
        return true;
    }
    bool submit(Ticket ticket) {
        if (poisoned || active.slot != none || !matches(ticket)) return false;
        active = prepared;
        prepared = {};
        return true;
    }
    bool abandon(Ticket ticket) {
        if (poisoned || !matches(ticket)) return false;
        prepared = {};
        return true;
    }
private:
    uint32_t next = 0;
    uint64_t generation = 0;
    bool matches(Ticket ticket) const {
        return ticket.slot < none && ticket.slot == prepared.slot &&
               ticket.generation && ticket.generation == prepared.generation;
    }
    Ticket reserve(const std::array<bool, 3>& unavailable) {
        if (generation == std::numeric_limits<uint64_t>::max()) {
            poisoned = true; return {};
        }
        for (uint32_t i=0; i<3; ++i) {
            const uint32_t slot = (next + i) % 3;
            if (!unavailable[slot]) {
                next = (slot + 1) % 3;
                return {slot, ++generation};
            }
        }
        return {};
    }
};
}
