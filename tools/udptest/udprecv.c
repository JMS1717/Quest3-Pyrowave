/* UDP sink for the loss / jitter / CPU test. Runs on the headset (NDK build) or natively for a
 * loopback smoke test.
 *
 *   udprecv <port> <seconds> [--gro] [--core N] [--deadline-us N] [--rt]
 *
 * Every datagram starts with a 24-byte header: "XRWU", u32 seq, u64 send_ns (sender clock),
 * u32 frame_id, u16 pkt_index, u16 pkts_in_frame.
 *
 * Frames and deadlines: what matters for a video transport is not generic jitter but whether every
 * packet a frame needs arrives before that frame's deadline. The two clocks are unrelated, so the
 * deadline is receiver-side and relative to the frame's first packet: a frame is "on time" when all
 * pkts_in_frame packets arrive within --deadline-us of the first one (default one 90 Hz frame).
 * Each packet's arrival age relative to its frame's first packet is tracked; a packet older than the
 * deadline is counted as "late" -- the packets a real receiver would drop instead of recovering.
 * Prints one JSON object on stdout when the time is up. Counts are raw; the arithmetic lives in
 * xrbench/udptest.py so it can be unit-tested.
 *
 * Jitter is (arrival gap - send gap) per consecutive packet, in microseconds. The two clocks are
 * unrelated, so absolute one-way delay is deliberately not computed -- only gap differences,
 * which cancel the offset.
 *
 * --gro asks the kernel (>= 5.0) to coalesce datagrams (UDP_GRO). Caveat for the frame numbers:
 * every segment in one coalesced read gets the same arrival timestamp (the read), so with GRO
 * the age/assembly figures are quantised to the read cadence and are not comparable to the
 * plain run's; loss, late-packet counts and CPU are. --gro asks the kernel to coalesce datagrams (UDP_GRO); the segment size then arrives
 * as a cmsg and the buffer is walked per segment. This is the "can the kernel take the per-packet
 * cost off the app" probe. Whether it helps is what the CPU counters answer.
 */
#define _GNU_SOURCE
#include <arpa/inet.h>
#include <errno.h>
#include <inttypes.h>
#include <netinet/in.h>
#include <sched.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <time.h>
#include <unistd.h>

#ifndef UDP_GRO
#define UDP_GRO 104
#endif
#ifndef SOL_UDP
#define SOL_UDP 17
#endif

#ifndef __linux__
/* macOS has no recvmmsg; the loopback smoke test uses recvmsg one datagram at a time. */
struct mmsghdr { struct msghdr msg_hdr; unsigned int msg_len; };
#endif

#define HDR 24
#define FRAME_RING 256
#ifndef SO_TIMESTAMPNS
#define SO_TIMESTAMPNS 35
#endif
#ifndef SCM_TIMESTAMPNS
#define SCM_TIMESTAMPNS SO_TIMESTAMPNS
#endif
#define MAX_DGRAM 65536
#define JITTER_BINS 20000  /* 1 us bins up to 20 ms; beyond goes to the last bin */

static uint64_t now_ns(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec;
}

static uint32_t rd32(const unsigned char *p) { uint32_t v; memcpy(&v, p, 4); return v; }
static uint64_t rd64(const unsigned char *p) { uint64_t v; memcpy(&v, p, 8); return v; }

static uint32_t hist[JITTER_BINS];
static uint64_t hist_n;
static int64_t jitter_max_us;

static void note_jitter(int64_t us) {
    if (us < 0) us = -us;
    if (us > jitter_max_us) jitter_max_us = us;
    if (us >= JITTER_BINS) us = JITTER_BINS - 1;
    hist[us]++;
    hist_n++;
}

static int64_t percentile(double pct) {
    if (!hist_n) return 0;
    uint64_t target = (uint64_t)(hist_n * pct / 100.0), acc = 0;
    for (int i = 0; i < JITTER_BINS; i++) {
        acc += hist[i];
        if (acc >= target) return i;
    }
    return JITTER_BINS - 1;
}

static uint32_t rd16(const unsigned char *p) { uint16_t v; memcpy(&v, p, 2); return v; }

/* Per-frame bookkeeping in a ring keyed by frame id. A frame is closed when it completes or when a
 * newer frame's first packet arrives more than a deadline later; packets for closed frames are the
 * ones a real receiver should drop on sight. */
struct frame {
    uint32_t id; int in_use;
    uint32_t expected, got;
    uint64_t first_arr, last_arr;
    uint64_t first_send, last_send;
    int complete, reported_late;
};
static struct frame ring[FRAME_RING];
static uint64_t deadline_ns = 11111000ull;   /* one 90 Hz frame unless --deadline-us */
static uint64_t frames_seen, frames_complete, frames_complete_on_time, packets_late, packets_for_closed;
static uint32_t asm_hist[JITTER_BINS]; static uint64_t asm_n; static int64_t asm_max_us;
static uint32_t age_hist[JITTER_BINS]; static uint64_t age_n; static int64_t age_max_us;
static uint32_t span_hist[JITTER_BINS]; static uint64_t span_n; static int64_t span_max_us;
static int kernel_ts;   /* 1 once a kernel receive timestamp has been used */
/* Kernel-receive to application-read lag, per packet: the scheduler's contribution. */
static uint32_t lag_hist[JITTER_BINS]; static uint64_t lag_n; static int64_t lag_max_us;
static int rt_on;
static uint32_t newest_frame; static int have_newest;

static void hist_note(uint32_t *h, uint64_t *n, int64_t *mx, int64_t us) {
    if (us < 0) us = 0;
    if (us > *mx) *mx = us;
    if (us >= JITTER_BINS) us = JITTER_BINS - 1;
    h[us]++; (*n)++;
}
static int64_t hist_pct(const uint32_t *h, uint64_t n, double pct) {
    if (!n) return 0;
    uint64_t target = (uint64_t)(n * pct / 100.0), acc = 0;
    for (int i = 0; i < JITTER_BINS; i++) { acc += h[i]; if (acc >= target) return i; }
    return JITTER_BINS - 1;
}

static void close_frame(struct frame *f) {
    if (!f->in_use) return;
    if (f->complete) {
        frames_complete++;
        int64_t asm_us = (int64_t)(f->last_arr - f->first_arr) / 1000;
        hist_note(asm_hist, &asm_n, &asm_max_us, asm_us);
        if ((uint64_t)(f->last_arr - f->first_arr) <= deadline_ns) frames_complete_on_time++;
        int64_t span_us = (int64_t)(f->last_send - f->first_send) / 1000;
        hist_note(span_hist, &span_n, &span_max_us, span_us);
    }
    f->in_use = 0;
}

static void account_frame(uint32_t fid, uint32_t idx, uint32_t total, uint64_t arr, uint64_t send_ns) {
    (void)idx;
    struct frame *f = &ring[fid % FRAME_RING];
    if (!f->in_use || f->id != fid) {
        /* A packet for a frame we are not tracking: new frame, or one already closed. Treat it as
         * closed only if it is older than the newest frame we have seen; otherwise it is new. */
        if (have_newest && (int32_t)(fid - newest_frame) < 0) { packets_for_closed++; return; }
        close_frame(f);   /* evict whatever the slot held */
        memset(f, 0, sizeof *f);
        f->in_use = 1; f->id = fid; f->expected = total; f->first_arr = arr; f->first_send = send_ns;
        frames_seen++;
        newest_frame = fid; have_newest = 1;
        /* Any older frame still open is past its deadline once a newer one is more than a
         * deadline old; sweep the ring so incomplete frames get closed. */
        for (int i = 0; i < FRAME_RING; i++) {
            struct frame *g = &ring[i];
            if (g->in_use && g->id != fid && !g->complete && arr - g->first_arr > deadline_ns) close_frame(g);
        }
    }
    f->got++; f->last_arr = arr;
    if (send_ns > f->last_send) f->last_send = send_ns;
    int64_t age_us = (int64_t)(arr - f->first_arr) / 1000;
    hist_note(age_hist, &age_n, &age_max_us, age_us);
    if ((uint64_t)(arr - f->first_arr) > deadline_ns) packets_late++;
    if (f->got >= f->expected && !f->complete) { f->complete = 1; close_frame(f); }
}

/* One datagram (or one GRO segment). */
static uint64_t received, bytes, bad, max_seq, have_prev, prev_send_ns, prev_arr_ns;
static void account(const unsigned char *p, size_t len, uint64_t arr_ns) {
    bytes += len;
    if (len < HDR || memcmp(p, "XRWU", 4) != 0) { bad++; return; }
    uint32_t seq = rd32(p + 4);
    uint64_t send_ns = rd64(p + 8);
    uint32_t fid = rd32(p + 16), idx = rd16(p + 20), total = rd16(p + 22);
    received++;
    if (seq > max_seq) max_seq = seq;
    if (have_prev) {
        int64_t d_arr = (int64_t)(arr_ns - prev_arr_ns);
        int64_t d_send = (int64_t)(send_ns - prev_send_ns);
        note_jitter((d_arr - d_send) / 1000);
    }
    have_prev = 1; prev_send_ns = send_ns; prev_arr_ns = arr_ns;
    if (total) account_frame(fid, idx, total, arr_ns, send_ns);
}

int main(int argc, char **argv) {
    if (argc < 3) { fprintf(stderr, "usage: udprecv <port> <seconds> [--gro] [--core N] [--deadline-us N]\n"); return 2; }
    int port = atoi(argv[1]);
    double seconds = atof(argv[2]);
    int want_gro = 0, core = -1;
    for (int i = 3; i < argc; i++) {
        if (!strcmp(argv[i], "--gro")) want_gro = 1;
        else if (!strcmp(argv[i], "--core") && i + 1 < argc) core = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--deadline-us") && i + 1 < argc) deadline_ns = (uint64_t)atoll(argv[++i]) * 1000ull;
        else if (!strcmp(argv[i], "--rt")) rt_on = 1;
    }
#ifdef __linux__
    if (rt_on) {
        /* What a real transport would do for its receive thread. Needs CAP_SYS_NICE; on a stock
         * headset it may be refused, which is itself worth knowing. */
        struct sched_param sp = { .sched_priority = 50 };
        if (sched_setscheduler(0, SCHED_FIFO, &sp) != 0) { fprintf(stderr, "SCHED_FIFO: %s\n", strerror(errno)); rt_on = 0; }
    }
    if (core >= 0) {
        cpu_set_t set; CPU_ZERO(&set); CPU_SET(core, &set);
        if (sched_setaffinity(0, sizeof set, &set) != 0) fprintf(stderr, "affinity: %s\n", strerror(errno));
    }
#endif
    (void)core; (void)want_gro;  /* only used on Linux */
    int fd = socket(AF_INET, SOCK_DGRAM, 0);
    if (fd < 0) { perror("socket"); return 1; }
    int rcvbuf = 16 << 20;
    setsockopt(fd, SOL_SOCKET, SO_RCVBUF, &rcvbuf, sizeof rcvbuf);
    int gro_on = 0;
#ifdef __linux__
    if (want_gro) {
        int one = 1;
        gro_on = setsockopt(fd, SOL_UDP, UDP_GRO, &one, sizeof one) == 0;
        if (!gro_on) fprintf(stderr, "UDP_GRO: %s\n", strerror(errno));
    }
#endif
#ifdef __linux__
    { int one = 1; if (setsockopt(fd, SOL_SOCKET, SO_TIMESTAMPNS, &one, sizeof one) != 0) fprintf(stderr, "SO_TIMESTAMPNS: %s\n", strerror(errno)); }
#endif
    struct sockaddr_in addr = {0};
    addr.sin_family = AF_INET; addr.sin_addr.s_addr = htonl(INADDR_ANY); addr.sin_port = htons(port);
    if (bind(fd, (struct sockaddr *)&addr, sizeof addr) != 0) { perror("bind"); return 1; }
    struct timeval tv = {0, 200000};
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv);

    /* Batch receives: 64 messages per syscall where the kernel offers it. */
    enum { BATCH = 64 };
    static unsigned char bufs[BATCH][MAX_DGRAM];
    struct iovec iov[BATCH];
    struct mmsghdr msgs[BATCH];
    static char cbuf[BATCH][128];
    for (int i = 0; i < BATCH; i++) {
        iov[i].iov_base = bufs[i]; iov[i].iov_len = MAX_DGRAM;
        memset(&msgs[i], 0, sizeof msgs[i]);
        msgs[i].msg_hdr.msg_iov = &iov[i]; msgs[i].msg_hdr.msg_iovlen = 1;
        msgs[i].msg_hdr.msg_control = cbuf[i]; msgs[i].msg_hdr.msg_controllen = sizeof cbuf[i];
    }

    uint64_t start = now_ns(), first_arrival = 0, last_arrival = 0;
    uint64_t syscalls = 0, gro_segments = 0;
    uint64_t deadline = start + (uint64_t)(seconds * 1e9);
    while (now_ns() < deadline) {
#ifdef __linux__
        /* MSG_WAITFORONE: return as soon as one datagram is there and take whatever else is
         * already queued. Without it a blocking recvmmsg waits for all BATCH slots or the
         * SO_RCVTIMEO, which held the tail of every frame until the next frame arrived -- an
         * artifact that read as 11 ms of "scheduler lag" and 200 ms stalls. */
        int n = recvmmsg(fd, msgs, BATCH, MSG_WAITFORONE, NULL);
#else
        int n = recvmsg(fd, &msgs[0].msg_hdr, 0);
        if (n >= 0) { msgs[0].msg_len = (unsigned)n; n = 1; }
#endif
        if (n < 0) { if (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR) continue; perror("recv"); break; }
        syscalls++;
        uint64_t arr_batch = now_ns();
        struct timespec rt_now; clock_gettime(CLOCK_REALTIME, &rt_now);
        uint64_t arr_batch_rt = (uint64_t)rt_now.tv_sec * 1000000000ull + (uint64_t)rt_now.tv_nsec;
        (void)arr_batch_rt;  /* only used with kernel timestamps on Linux */
        if (!first_arrival) first_arrival = arr_batch;
        last_arrival = arr_batch;
        for (int i = 0; i < n; i++) {
            size_t len = msgs[i].msg_len;
            size_t seg = len;
            uint64_t arr = arr_batch;
#ifdef __linux__
            for (struct cmsghdr *c = CMSG_FIRSTHDR(&msgs[i].msg_hdr); c; c = CMSG_NXTHDR(&msgs[i].msg_hdr, c)) {
                if (c->cmsg_level == SOL_UDP && c->cmsg_type == UDP_GRO) { int gso; memcpy(&gso, CMSG_DATA(c), sizeof gso); if (gso > 0) seg = (size_t)gso; }
                else if (c->cmsg_level == SOL_SOCKET && c->cmsg_type == SCM_TIMESTAMPNS) {
                    /* Kernel receive time: when the driver handed the packet up, not when this
                     * thread got scheduled to read it. Realtime clock, so only differences within
                     * this run are used -- same as everything else here. */
                    struct timespec ts; memcpy(&ts, CMSG_DATA(c), sizeof ts);
                    arr = (uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec; kernel_ts = 1;
                    hist_note(lag_hist, &lag_n, &lag_max_us, ((int64_t)arr_batch_rt - (int64_t)arr) / 1000);
                }
            }
#endif
            if (seg < len) {
                for (size_t off = 0; off < len; off += seg) { account(bufs[i] + off, len - off < seg ? len - off : seg, arr); gro_segments++; }
            } else account(bufs[i], len, arr);
            msgs[i].msg_hdr.msg_controllen = sizeof cbuf[i];
        }
    }
    for (int i = 0; i < FRAME_RING; i++) close_frame(&ring[i]);
    struct rusage ru; getrusage(RUSAGE_SELF, &ru);
    double span = last_arrival > first_arrival ? (last_arrival - first_arrival) / 1e9 : 0.0;
    printf("{\"received\":%" PRIu64 ",\"bytes\":%" PRIu64 ",\"bad\":%" PRIu64 ",\"max_seq\":%" PRIu64
           ",\"seconds\":%.3f,\"syscalls\":%" PRIu64 ",\"gro_enabled\":%d,\"gro_segments\":%" PRIu64
           ",\"jitter_us_p50\":%" PRId64 ",\"jitter_us_p99\":%" PRId64 ",\"jitter_us_max\":%" PRId64
           ",\"frames_seen\":%" PRIu64 ",\"frames_complete\":%" PRIu64 ",\"frames_complete_on_time\":%" PRIu64
           ",\"packets_late\":%" PRIu64 ",\"packets_for_closed_frames\":%" PRIu64
           ",\"assembly_us_p50\":%" PRId64 ",\"assembly_us_p99\":%" PRId64 ",\"assembly_us_max\":%" PRId64
           ",\"age_us_p50\":%" PRId64 ",\"age_us_p99\":%" PRId64 ",\"age_us_max\":%" PRId64
           ",\"send_span_us_p50\":%" PRId64 ",\"send_span_us_p99\":%" PRId64 ",\"send_span_us_max\":%" PRId64
           ",\"app_lag_us_p50\":%" PRId64 ",\"app_lag_us_p99\":%" PRId64 ",\"app_lag_us_max\":%" PRId64 ",\"rt_scheduling\":%d"
           ",\"kernel_timestamps\":%d"
           ",\"deadline_us\":%" PRIu64
           ",\"cpu_user_us\":%" PRIu64 ",\"cpu_sys_us\":%" PRIu64 "}\n",
           received, bytes, bad, max_seq, span, syscalls, gro_on, gro_segments,
           percentile(50), percentile(99), jitter_max_us,
           frames_seen, frames_complete, frames_complete_on_time, packets_late, packets_for_closed,
           hist_pct(asm_hist, asm_n, 50), hist_pct(asm_hist, asm_n, 99), asm_max_us,
           hist_pct(age_hist, age_n, 50), hist_pct(age_hist, age_n, 99), age_max_us,
           hist_pct(span_hist, span_n, 50), hist_pct(span_hist, span_n, 99), span_max_us,
           hist_pct(lag_hist, lag_n, 50), hist_pct(lag_hist, lag_n, 99), lag_max_us, rt_on,
           kernel_ts,
           (uint64_t)(deadline_ns / 1000ull),
           (uint64_t)ru.ru_utime.tv_sec * 1000000 + ru.ru_utime.tv_usec,
           (uint64_t)ru.ru_stime.tv_sec * 1000000 + ru.ru_stime.tv_usec);
    close(fd);
    return 0;
}
