#pragma once
#include <cstdint>
#include <mutex>
#include <unordered_map>

// Owns every accepted FD until reuse or removal. Tokens prevent late callbacks
// from attaching a fence to a different frame or recycled AHB pointer.
class ReleaseFenceRegistry {
public:
    using Close = void (*)(int);
    explicit ReleaseFenceRegistry(Close close) : close_(close) {}
    ~ReleaseFenceRegistry() {
        for (auto &pair : slots_) close_fd(pair.second.fd);
    }
    bool add(void *buffer) {
        std::lock_guard<std::mutex> lock(mutex_);
        return slots_.emplace(buffer, State{}).second;
    }
    void remove(void *buffer) {
        std::lock_guard<std::mutex> lock(mutex_);
        auto it = slots_.find(buffer);
        if (it != slots_.end()) { close_fd(it->second.fd); slots_.erase(it); }
    }
    uint64_t publish(void *buffer) {
        std::lock_guard<std::mutex> lock(mutex_);
        auto it = slots_.find(buffer);
        if (it == slots_.end() || it->second.fd >= 0) return 0;
        if (++next_token_ == 0) ++next_token_;
        return it->second.token = next_token_;
    }
    uint64_t token(void *buffer) {
        std::lock_guard<std::mutex> lock(mutex_);
        auto it = slots_.find(buffer);
        return it == slots_.end() ? 0 : it->second.token;
    }
    // Consumes FD on every path, including rejection. A newer fence on the
    // same GLES command stream covers preceding reads and replaces the older FD.
    bool attach(void *buffer, uint64_t token, int fd) {
        std::lock_guard<std::mutex> lock(mutex_);
        auto it = slots_.find(buffer);
        if (fd < 0 || !token || it == slots_.end() || it->second.token != token) {
            close_fd(fd); return false;
        }
        close_fd(it->second.fd);
        it->second.fd = fd;
        return true;
    }
    // Caller already excludes both leased and pending buffers. Invalidating the
    // token prevents a stale consumer callback during command recording/reuse.
    int begin_reuse(void *buffer) {
        std::lock_guard<std::mutex> lock(mutex_);
        auto it = slots_.find(buffer);
        if (it == slots_.end()) return -1;
        int fd = it->second.fd;
        it->second = State{};
        return fd;
    }
private:
    struct State { uint64_t token = 0; int fd = -1; };
    void close_fd(int fd) { if (fd >= 0) close_(fd); }
    Close close_;
    std::mutex mutex_;
    std::unordered_map<void *, State> slots_;
    uint64_t next_token_ = 0;
};
