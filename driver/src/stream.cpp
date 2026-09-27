// Winsock implementation of the driver's stream endpoint. See stream.h for the wire format.
//
// Threads: the caller's render/encode thread calls send_frame(); an accept thread owns the listening
// socket and waits for one client at a time; while a client is connected an ack thread blocks in
// recv() on that socket. The client handle and every send are guarded by one mutex, the stats and the
// pts -> send-time map by another (always taken *inside* the socket mutex, never the other way round).
//
// Nothing here writes to stdout or exits the process: this code lands inside a SteamVR driver DLL.
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif

#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>

#include "stream.h"

#include <atomic>
#include <chrono>
#include <cstdio>
#include <map>
#include <share.h>

#include <mutex>
#include <thread>

#pragma comment(lib, "ws2_32.lib")

namespace xrwired {
namespace {

using Clock = std::chrono::steady_clock;

constexpr size_t kHeaderBytes = 20;            // "XRW2" + four big-endian u32
constexpr size_t kRecordHeaderBytes = 12;      // u32 length + u64 pts_us
constexpr size_t kMaxAccessUnit = 16u << 20;   // what the receiver app accepts (StreamReceiver.MAX_AU)
constexpr size_t kTrackedFrames = 256;         // pts -> send time entries kept; ~3.5 s at 72 fps
constexpr int kSendBufferBytes = 8 << 20;      // one big kernel buffer so a burst never blocks the encoder

std::string describe(int error);

/** Debug log: the Windows debugger channel, plus a file when %XRWIRED_STREAM_LOG% names one. Only
 *  connection-level events call this; the per-frame path never logs. */
static void log(const std::string& text) {
    const std::string line = "[xrwired.stream] " + text + "\n";
    OutputDebugStringA(line.c_str());

    static std::mutex file_mutex;
    std::lock_guard<std::mutex> lock(file_mutex);
    static FILE* file = nullptr;
    static bool opened = false;
    if (!opened) {
        opened = true;
        char path[MAX_PATH] = {};
        DWORD length = GetEnvironmentVariableA("XRWIRED_STREAM_LOG", path, MAX_PATH);
        if (length == 0 || length >= MAX_PATH) {
            // Default: beside this module, next to the driver's own log - vrserver is started by
            // SteamVR, so an environment variable never reaches us.
            HMODULE module = nullptr;
            GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                               reinterpret_cast<LPCSTR>(&describe), &module);
            char module_path[MAX_PATH] = {};
            GetModuleFileNameA(module, module_path, MAX_PATH);
            std::string folder(module_path);
            folder = folder.substr(0, folder.find_last_of('\\'));
            _snprintf_s(path, MAX_PATH, _TRUNCATE, "%s\\driver_xrwired.log", folder.c_str());
        }
        // _fsopen with _SH_DENYNO: we append, but anything else can still read the log live.
        file = _fsopen(path, "a", _SH_DENYNO);
    }
    if (file != nullptr) {
        fputs(line.c_str(), file);
        fflush(file);
    }
}

std::string describe(int error) {
    char text[64];
    _snprintf_s(text, sizeof(text), _TRUNCATE, "WSA error %d", error);
    return text;
}

// Big-endian packing by hand: htonll is not available everywhere and htonl would hide the intent.
void put_u32(uint8_t* out, uint32_t value) {
    out[0] = static_cast<uint8_t>(value >> 24);
    out[1] = static_cast<uint8_t>(value >> 16);
    out[2] = static_cast<uint8_t>(value >> 8);
    out[3] = static_cast<uint8_t>(value);
}

void put_u64(uint8_t* out, uint64_t value) {
    put_u32(out, static_cast<uint32_t>(value >> 32));
    put_u32(out + 4, static_cast<uint32_t>(value & 0xffffffffull));
}

uint64_t get_u64(const uint8_t* in) {
    uint64_t value = 0;
    for (int i = 0; i < 8; ++i) value = (value << 8) | in[i];
    return value;
}

double ms_since(Clock::time_point start, Clock::time_point end) {
    return std::chrono::duration<double, std::milli>(end - start).count();
}

/** WSAStartup/WSACleanup reference counting: several Stream objects (or a re-listen) must not tear
 *  Winsock down under each other. */
class Winsock {
public:
    static bool acquire() {
        std::lock_guard<std::mutex> lock(mutex());
        if (count() == 0) {
            WSADATA data;
            int error = WSAStartup(MAKEWORD(2, 2), &data);
            if (error != 0) {
                log("WSAStartup failed: " + describe(error));
                return false;
            }
        }
        ++count();
        return true;
    }

    static void release() {
        std::lock_guard<std::mutex> lock(mutex());
        if (count() > 0 && --count() == 0) WSACleanup();
    }

private:
    static std::mutex& mutex() { static std::mutex m; return m; }
    static int& count() { static int n = 0; return n; }
};

/** Sends every byte of `buffers` (one WSASend when the socket takes it all, a loop otherwise). */
bool send_all(SOCKET socket, WSABUF* buffers, int count) {
    while (count > 0) {
        DWORD sent = 0;
        if (WSASend(socket, buffers, static_cast<DWORD>(count), &sent, 0, nullptr, nullptr) != 0) return false;
        if (sent == 0) return false;
        while (count > 0 && sent >= buffers->len) {
            sent -= buffers->len;
            ++buffers;
            --count;
        }
        if (count > 0 && sent > 0) {
            buffers->buf += sent;
            buffers->len -= static_cast<ULONG>(sent);
        }
    }
    return true;
}

bool recv_all(SOCKET socket, uint8_t* out, int count) {
    int filled = 0;
    while (filled < count) {
        int got = recv(socket, reinterpret_cast<char*>(out) + filled, count - filled, 0);
        if (got <= 0) return false;
        filled += got;
    }
    return true;
}

}  // namespace

struct Stream::Impl {
    SOCKET listener = INVALID_SOCKET;
    SOCKET client = INVALID_SOCKET;
    mutable std::mutex socket_mutex;          // guards `listener`, `client` and every send
    mutable std::mutex stats_mutex;           // guards `stats` and `sent_at`
    std::mutex handler_mutex;

    std::thread accept_thread;
    std::thread ack_thread;                   // owned by the accept thread only
    std::atomic<bool> running{false};
    std::atomic<bool> keyframe_request{false};
    bool winsock = false;

    std::function<void(int, uint64_t, int64_t)> handler;
    std::function<void(uint64_t, const float*)> pose_handler;
    std::function<void(uint64_t, bool, bool, const float*)> hands_handler;
    std::string host;                         // set when dialling out instead of listening
    int port = 0;
    StreamStats stats;
    std::map<uint64_t, Clock::time_point> sent_at;

    uint8_t header[kHeaderBytes] = {};

    void accept_loop();
    void dial_loop();
    void serve_client(SOCKET fresh);          // greet, run the ack loop, clean up
    void ack_loop(SOCKET socket);
    void close_client();                      // takes socket_mutex
    void close_client_locked();               // socket_mutex already held
    void remember_send(uint64_t pts_us, Clock::time_point when);
};

void Stream::Impl::close_client_locked() {
    if (client == INVALID_SOCKET) return;
    SOCKET dying = client;
    client = INVALID_SOCKET;
    shutdown(dying, SD_BOTH);                 // wakes the ack thread's recv() before the handle dies
    closesocket(dying);
}

void Stream::Impl::close_client() {
    std::lock_guard<std::mutex> lock(socket_mutex);
    close_client_locked();
}

void Stream::Impl::remember_send(uint64_t pts_us, Clock::time_point when) {
    sent_at[pts_us] = when;                   // caller holds stats_mutex
    while (sent_at.size() > kTrackedFrames) sent_at.erase(sent_at.begin());   // oldest pts first
}

void Stream::Impl::accept_loop() {
    while (running.load()) {
        SOCKET socket_handle;
        {
            std::lock_guard<std::mutex> lock(socket_mutex);
            socket_handle = listener;
        }
        if (socket_handle == INVALID_SOCKET) break;

        SOCKET fresh = accept(socket_handle, nullptr, nullptr);
        if (fresh == INVALID_SOCKET) {
            if (!running.load()) break;
            log("accept failed: " + describe(WSAGetLastError()));
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
            continue;
        }

        serve_client(fresh);
    }
}

void Stream::Impl::serve_client(SOCKET fresh) {
    BOOL nodelay = TRUE;
    setsockopt(fresh, IPPROTO_TCP, TCP_NODELAY, reinterpret_cast<const char*>(&nodelay), sizeof(nodelay));
    int send_buffer = kSendBufferBytes;
    setsockopt(fresh, SOL_SOCKET, SO_SNDBUF, reinterpret_cast<const char*>(&send_buffer), sizeof(send_buffer));

    bool greeted = false;
    {
        std::lock_guard<std::mutex> lock(socket_mutex);
        if (!running.load()) {
            closesocket(fresh);
            return;
        }
        client = fresh;
        WSABUF buffer = {static_cast<ULONG>(kHeaderBytes), reinterpret_cast<char*>(header)};
        greeted = send_all(fresh, &buffer, 1);
        if (!greeted) close_client_locked();
    }
    if (!greeted) {
        log("client dropped: header send failed");
        return;
    }
    {
        std::lock_guard<std::mutex> lock(stats_mutex);
        sent_at.clear();
    }
    keyframe_request.store(true);          // a fresh client has no reference frames: it needs an IDR
    log("client connected");

    ack_thread = std::thread(&Impl::ack_loop, this, fresh);
    ack_thread.join();                     // returns when the client (or stop()) closes the socket
    ack_thread = std::thread();
    close_client();
    log("client disconnected");
}

// Dial the headset until it answers, then serve it; on disconnect, start dialling again.
void Stream::Impl::dial_loop() {
    while (running.load()) {
        SOCKET fresh = ::socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
        if (fresh == INVALID_SOCKET) {
            std::this_thread::sleep_for(std::chrono::seconds(1));
            continue;
        }
        sockaddr_in address = {};
        address.sin_family = AF_INET;
        address.sin_port = htons(static_cast<u_short>(port));
        if (inet_pton(AF_INET, host.c_str(), &address.sin_addr) != 1) {
            log("bad client address " + host);
            closesocket(fresh);
            return;
        }
        if (connect(fresh, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == SOCKET_ERROR) {
            closesocket(fresh);
            if (!running.load()) return;
            std::this_thread::sleep_for(std::chrono::seconds(1));
            continue;
        }
        serve_client(fresh);
    }
}

void Stream::Impl::ack_loop(SOCKET socket_handle) {
    uint8_t record[17];
    while (running.load()) {
        if (!recv_all(socket_handle, record, 9)) break;
        const int kind = record[0];
        const uint64_t pts_us = get_u64(record + 1);
        int64_t ahead_ns = 0;
        if (kind == 3) {                        // the VR client adds ns from this ack until photons
            if (!recv_all(socket_handle, record + 9, 8)) break;
            ahead_ns = static_cast<int64_t>(get_u64(record + 9));
        } else if (kind == 4) {                 // head pose: 21 big-endian floats after the id
            uint8_t body[84];
            if (!recv_all(socket_handle, body, sizeof(body))) break;
            float values[21];
            for (int i = 0; i < 21; ++i) {
                const uint32_t bits = (uint32_t(body[i * 4]) << 24) | (uint32_t(body[i * 4 + 1]) << 16) |
                                      (uint32_t(body[i * 4 + 2]) << 8) | uint32_t(body[i * 4 + 3]);
                memcpy(&values[i], &bits, sizeof(bits));
            }
            std::function<void(uint64_t, const float*)> pose_callback;
            {
                std::lock_guard<std::mutex> lock(handler_mutex);
                pose_callback = pose_handler;
            }
            if (pose_callback) pose_callback(pts_us, values);
            continue;
        } else if (kind == 5) {                 // hand joints: 2 active bytes + 2*26*8 floats
            uint8_t flags[2];
            if (!recv_all(socket_handle, flags, 2)) break;
            uint8_t body[416 * 4];
            if (!recv_all(socket_handle, body, sizeof(body))) break;
            float joints[416];
            for (int i = 0; i < 416; ++i) {
                const uint32_t bits = (uint32_t(body[i * 4]) << 24) | (uint32_t(body[i * 4 + 1]) << 16) |
                                      (uint32_t(body[i * 4 + 2]) << 8) | uint32_t(body[i * 4 + 3]);
                memcpy(&joints[i], &bits, sizeof(bits));
            }
            std::function<void(uint64_t, bool, bool, const float*)> hands_callback;
            {
                std::lock_guard<std::mutex> lock(handler_mutex);
                hands_callback = hands_handler;
            }
            if (hands_callback) hands_callback(pts_us, flags[0] != 0, flags[1] != 0, joints);
            continue;
        }
        const Clock::time_point now = Clock::now();
        {
            std::lock_guard<std::mutex> lock(stats_mutex);
            auto sent = sent_at.find(pts_us);
            const bool known = sent != sent_at.end();
            if (kind == 1) {
                stats.acks_decoded++;
                if (known) stats.last_send_to_decoded_ms = ms_since(sent->second, now);
            } else if (kind == 3 && known) {
                stats.last_send_to_photons_ms = ms_since(sent->second, now) + ahead_ns / 1e6;
            }
        }
        std::function<void(int, uint64_t, int64_t)> callback;
        {
            std::lock_guard<std::mutex> lock(handler_mutex);
            callback = handler;
        }
        if (callback) callback(kind, pts_us, ahead_ns);
    }
}

Stream::~Stream() {
    stop();
    delete impl_;
    impl_ = nullptr;
}

bool Stream::listen(int port, int width, int height, int fps, int codec_id) {
    if (port <= 0 || port > 65535 || width <= 0 || height <= 0 || fps <= 0) return false;
    if (impl_ != nullptr && impl_->running.load()) return false;      // already listening
    if (impl_ == nullptr) impl_ = new Impl();
    if (!Winsock::acquire()) return false;
    impl_->winsock = true;

    // "XRW2" width height fps codec, all big-endian (rawpipe.stream_header for a non-AVC codec).
    memcpy(impl_->header, "XRW2", 4);
    put_u32(impl_->header + 4, static_cast<uint32_t>(width));
    put_u32(impl_->header + 8, static_cast<uint32_t>(height));
    put_u32(impl_->header + 12, static_cast<uint32_t>(fps));
    put_u32(impl_->header + 16, static_cast<uint32_t>(codec_id));

    SOCKET listener = ::socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (listener == INVALID_SOCKET) {
        log("socket() failed: " + describe(WSAGetLastError()));
        Winsock::release();
        impl_->winsock = false;
        return false;
    }
    BOOL exclusive = TRUE;   // not SO_REUSEADDR: on Windows that would let anyone steal the port
    setsockopt(listener, SOL_SOCKET, SO_EXCLUSIVEADDRUSE,
               reinterpret_cast<const char*>(&exclusive), sizeof(exclusive));

    sockaddr_in address = {};
    address.sin_family = AF_INET;
    address.sin_addr.s_addr = INADDR_ANY;
    address.sin_port = htons(static_cast<u_short>(port));
    if (bind(listener, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == SOCKET_ERROR ||
        ::listen(listener, 1) == SOCKET_ERROR) {
        log("bind/listen on port " + std::to_string(port) + " failed: " + describe(WSAGetLastError()));
        closesocket(listener);
        Winsock::release();
        impl_->winsock = false;
        return false;
    }

    {
        std::lock_guard<std::mutex> lock(impl_->socket_mutex);
        impl_->listener = listener;
    }
    impl_->running.store(true);
    impl_->accept_thread = std::thread(&Impl::accept_loop, impl_);
    log("listening on TCP " + std::to_string(port) + " for " + std::to_string(width) + "x" +
        std::to_string(height) + "@" + std::to_string(fps) + " codec " + std::to_string(codec_id));
    return true;
}

bool Stream::connect_to(const std::string& host, int port, int width, int height, int fps, int codec_id) {
    if (host.empty() || port <= 0 || port > 65535 || width <= 0 || height <= 0 || fps <= 0) return false;
    if (impl_ != nullptr && impl_->running.load()) return false;
    if (impl_ == nullptr) impl_ = new Impl();
    if (!Winsock::acquire()) return false;
    impl_->winsock = true;

    memcpy(impl_->header, "XRW2", 4);
    put_u32(impl_->header + 4, static_cast<uint32_t>(width));
    put_u32(impl_->header + 8, static_cast<uint32_t>(height));
    put_u32(impl_->header + 12, static_cast<uint32_t>(fps));
    put_u32(impl_->header + 16, static_cast<uint32_t>(codec_id));

    impl_->host = host;
    impl_->port = port;
    impl_->running.store(true);
    impl_->accept_thread = std::thread(&Impl::dial_loop, impl_);
    log("dialling " + host + ":" + std::to_string(port) + " for " + std::to_string(width) + "x" +
        std::to_string(height) + "@" + std::to_string(fps) + " codec " + std::to_string(codec_id));
    return true;
}

bool Stream::client_connected() const {
    if (impl_ == nullptr) return false;
    std::lock_guard<std::mutex> lock(impl_->socket_mutex);
    return impl_->client != INVALID_SOCKET;
}

bool Stream::send_frame(const uint8_t* data, size_t length, uint64_t pts_us) {
    if (impl_ == nullptr || data == nullptr || length == 0 || length > kMaxAccessUnit) return false;

    uint8_t record[kRecordHeaderBytes];
    put_u32(record, static_cast<uint32_t>(length));
    put_u64(record + 4, pts_us);
    WSABUF buffers[2] = {{static_cast<ULONG>(kRecordHeaderBytes), reinterpret_cast<char*>(record)},
                         {static_cast<ULONG>(length),
                          reinterpret_cast<char*>(const_cast<uint8_t*>(data))}};

    std::lock_guard<std::mutex> lock(impl_->socket_mutex);
    if (impl_->client == INVALID_SOCKET) return false;        // nobody listening: drop, never queue
    if (!send_all(impl_->client, buffers, 2)) {
        const int error = WSAGetLastError();
        impl_->close_client_locked();
        log("send failed, dropping the client: " + describe(error));
        return false;
    }
    const Clock::time_point now = Clock::now();               // last byte handed to the socket
    {
        std::lock_guard<std::mutex> stats_lock(impl_->stats_mutex);
        impl_->stats.frames_sent++;
        impl_->stats.bytes_sent += kRecordHeaderBytes + length;   // what actually went on the wire
        impl_->remember_send(pts_us, now);
    }
    return true;
}

void Stream::on_pose(std::function<void(uint64_t, const float[21])> handler) {
    if (impl_ == nullptr) impl_ = new Impl();
    std::lock_guard<std::mutex> lock(impl_->handler_mutex);
    impl_->pose_handler = [handler](uint64_t id, const float* values) { handler(id, values); };
}

void Stream::on_hands(std::function<void(uint64_t, bool, bool, const float[416])> handler) {
    if (impl_ == nullptr) impl_ = new Impl();
    std::lock_guard<std::mutex> lock(impl_->handler_mutex);
    impl_->hands_handler = [handler](uint64_t id, bool l, bool r, const float* j) { handler(id, l, r, j); };
}

void Stream::on_ack(std::function<void(int kind, uint64_t pts_us, int64_t ahead_ns)> handler) {
    if (impl_ == nullptr) impl_ = new Impl();
    std::lock_guard<std::mutex> lock(impl_->handler_mutex);
    impl_->handler = std::move(handler);
}

bool Stream::take_keyframe_request() {
    return impl_ != nullptr && impl_->keyframe_request.exchange(false);
}

StreamStats Stream::stats() const {
    if (impl_ == nullptr) return StreamStats();
    std::lock_guard<std::mutex> lock(impl_->stats_mutex);
    return impl_->stats;
}

void Stream::stop() {
    if (impl_ == nullptr) return;
    const bool was_running = impl_->running.exchange(false);
    {
        std::lock_guard<std::mutex> lock(impl_->socket_mutex);
        if (impl_->listener != INVALID_SOCKET) {
            closesocket(impl_->listener);          // unblocks accept()
            impl_->listener = INVALID_SOCKET;
        }
        impl_->close_client_locked();              // unblocks the ack thread's recv()
    }
    if (impl_->accept_thread.joinable()) impl_->accept_thread.join();   // which joins the ack thread
    if (impl_->winsock) {
        impl_->winsock = false;
        Winsock::release();
    }
    if (was_running) log("stopped");
}

}  // namespace xrwired
