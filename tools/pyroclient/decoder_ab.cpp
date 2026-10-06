// Standalone A/B for PyroWave decoder variants: exact-pixel gate, then interleaved GPU timing.
//
// Every variant ("arm") decodes the same Haar 4:2:0 bitstream on one Vulkan device. The gate
// requires all three R8 planes to be byte-identical to the default decoder, the luma PSNR
// against the source to reach AB_MIN_PSNR (default 30 dB) and the luma plane to be non-trivial.
// Only then does the timing run: blocks of AB_FRAMES decodes in ABBA order (AB_BLOCKS blocks,
// default 8 x 40), each frame one submission bracketed by GPU timestamps, like the client's
// synchronous decode. AB_STAGES=1 also enables PyroWave's own stage timestamps and prints
// the Dequant / iDWT averages per block. The Adreno clock (kgsl sysfs, when readable) is
// printed per block, because Quest's governor changes it.
//
// Usage: decoder_ab <arm> [width height max_bytes]...
//   arms: haar32 (two-pass [3,2] inverse Haar); controls base (the default decoder against
//   itself) and pairs64 (PYROWAVE_HAAR_PAIRS=64-column)
//   default cases: 512x320 / 131072, 1000x600 / 262144 (unaligned edges), 4160x2208 / 1041667
//   (the native stereo frame at 1000 Mbit/s and 120 Hz). Timing uses the last case.
// AB_GATE_ONLY=1 skips timing. Exit 0 only when every case passes the gate.

#include "gate_common.h"

#include <algorithm>
#include <string>

namespace {

struct Arm {
    const char *name;
    pyrowave_result (*apply)(pyrowave_decoder);
    // Decoder environment switch, set only while the arm's decoder is created.
    const char *env_name = nullptr;
    const char *env_value = nullptr;
    // Luma plane is RGBA8 at half size, one 2x2 pixel quad per texel (decoder mode 3).
    bool packed_luma = false;
    // Plane 1 is RG8 holding Cb and Cr (decoder mode 4); plane 2 is passed but not written.
    bool dual_chroma = false;
};

pyrowave_result no_change(pyrowave_decoder) { return PYROWAVE_SUCCESS; }

const Arm ARMS[] = {
    { "haar32", [](pyrowave_decoder d) { return pyrowave_decoder_set_haar32(d, 1); } },
    { "haar32q", [](pyrowave_decoder d) { return pyrowave_decoder_set_haar32(d, 2); } },
    { "haar32qo", [](pyrowave_decoder d) { return pyrowave_decoder_set_haar32(d, 3); }, nullptr, nullptr, true },
    { "haar32qd", [](pyrowave_decoder d) { return pyrowave_decoder_set_haar32(d, 4); }, nullptr, nullptr, true, true },
    // Decoder V2 register-only inverse CDF 5/3; run with AB_WAVELET=53 so the base decodes 5/3 too.
    { "cdf53v2", [](pyrowave_decoder d) { return pyrowave_decoder_set_cdf53v2(d, 1); } },
    { "cdf53v2q", [](pyrowave_decoder d) { return pyrowave_decoder_set_cdf53v2(d, 2); }, nullptr, nullptr, true },
    { "cdf53v2qp", [](pyrowave_decoder d) { return pyrowave_decoder_set_cdf53v2(d, 3); }, nullptr, nullptr, true },
    // Controls: the default decoder against itself, and the dedicated pair-local Haar kernel.
    { "base", no_change },
    { "pairs64", no_change, "PYROWAVE_HAAR_PAIRS", "64-column" },
    // Cost probes (wrong output by design; run with AB_SKIP_GATE=1).
    { "dqprobe", no_change, "PYROWAVE_DEQUANT_PROBE", "1" },
    { "h32nostore", [](pyrowave_decoder d) { return pyrowave_decoder_set_haar32(d, 1); }, "PYROWAVE_HAAR32_PROBE", "1" },
    { "h32nofetch", [](pyrowave_decoder d) { return pyrowave_decoder_set_haar32(d, 1); }, "PYROWAVE_HAAR32_PROBE", "2" },
};

struct Session {
    pyrowave_decoder decoder = nullptr;
    Image planes[3];
    pyrowave_gpu_buffers buffers = {};
    VkCommandBuffer cmd = VK_NULL_HANDLE;
    VkFence fence = VK_NULL_HANDLE;
    VkQueryPool queries = VK_NULL_HANDLE;
    bool initialized_layout = false;
    bool packed_luma = false;
    bool dual_chroma = false;
};

Session create_session(const Gpu &g, uint32_t w, uint32_t h, const Arm *arm,
                       pyrowave_wavelet wavelet = ab_wavelet(), bool fragment = false) {
    Session s;
    pyrowave_decoder_create_info di = {};
    di.device = g.pyro; di.width = int(w); di.height = int(h);
    di.chroma = PYROWAVE_CHROMA_SUBSAMPLING_420; di.fragment_path = fragment; di.wavelet = wavelet;
    if (arm && arm->env_name) setenv(arm->env_name, arm->env_value, 1);
    PW_CHECK(pyrowave_decoder_create(&di, &s.decoder));
    if (arm && arm->env_name) unsetenv(arm->env_name);
    if (arm && arm->apply(s.decoder) != PYROWAVE_SUCCESS) {
        fprintf(stderr, "decoder refused arm %s\n", arm->name);
        exit(2);
    }
    const VkImageUsageFlags usage = VK_IMAGE_USAGE_SAMPLED_BIT | VK_IMAGE_USAGE_STORAGE_BIT |
                                    VK_IMAGE_USAGE_TRANSFER_SRC_BIT | VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT;
    s.packed_luma = arm && arm->packed_luma;
    const VkFormat luma_format = s.packed_luma ? VK_FORMAT_R8G8B8A8_UNORM : VK_FORMAT_R8_UNORM;
    s.planes[0] = s.packed_luma ? create_image(g, luma_format, w / 2, h / 2, usage) : create_image(g, luma_format, w, h, usage);
    s.dual_chroma = arm && arm->dual_chroma;
    const VkFormat chroma_format = s.dual_chroma ? VK_FORMAT_R8G8_UNORM : VK_FORMAT_R8_UNORM;
    s.planes[1] = create_image(g, chroma_format, w / 2, h / 2, usage);
    s.planes[2] = create_image(g, VK_FORMAT_R8_UNORM, w / 2, h / 2, usage);
    for (int i = 0; i < 3; i++) {
        pyrowave_image_view &v = s.buffers.planes[i];
        v.image = s.planes[i].image; v.width = int(s.planes[i].width); v.height = int(s.planes[i].height);
        v.image_format = v.view_format = i == 0 ? luma_format : i == 1 ? chroma_format : VK_FORMAT_R8_UNORM;
        v.mip_level = 0; v.layer = 0; v.aspect = VK_IMAGE_ASPECT_COLOR_BIT;
        v.swizzle = VK_COMPONENT_SWIZZLE_IDENTITY; v.layout = VK_IMAGE_LAYOUT_GENERAL;
    }
    VkCommandBufferAllocateInfo ca = { VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO };
    ca.commandPool = g.pool; ca.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY; ca.commandBufferCount = 1;
    VK_CHECK(vkAllocateCommandBuffers(g.device, &ca, &s.cmd));
    VkFenceCreateInfo fi = { VK_STRUCTURE_TYPE_FENCE_CREATE_INFO };
    VK_CHECK(vkCreateFence(g.device, &fi, nullptr, &s.fence));
    VkQueryPoolCreateInfo qi = { VK_STRUCTURE_TYPE_QUERY_POOL_CREATE_INFO };
    qi.queryType = VK_QUERY_TYPE_TIMESTAMP; qi.queryCount = 2;
    VK_CHECK(vkCreateQueryPool(g.device, &qi, nullptr, &s.queries));
    return s;
}

void destroy_session(const Gpu &g, Session &s) {
    VK_CHECK(vkDeviceWaitIdle(g.device));
    vkDestroyQueryPool(g.device, s.queries, nullptr);
    vkDestroyFence(g.device, s.fence, nullptr);
    vkFreeCommandBuffers(g.device, g.pool, 1, &s.cmd);
    for (auto &p : s.planes) destroy_image(g, p);
    pyrowave_decoder_destroy(s.decoder);
    s = {};
}

// One frame: push the packets, record the decode between two timestamps, submit, wait.
// Returns GPU milliseconds. With readback, also copies the three planes out.
double decode(const Gpu &g, Session &s, const std::vector<std::vector<uint8_t>> &packets, double ns_per_tick,
              std::vector<std::vector<uint8_t>> *readback = nullptr) {
    pyrowave_decoder_clear(s.decoder);
    for (const auto &p : packets) PW_CHECK(pyrowave_decoder_push_packet(s.decoder, p.data(), p.size()));
    if (!pyrowave_decoder_decode_is_ready(s.decoder, false)) { fprintf(stderr, "frame incomplete\n"); exit(2); }

    VkBuffer buffer = VK_NULL_HANDLE;
    VkDeviceMemory memory = VK_NULL_HANDLE;
    VkDeviceSize offsets[3] = {}, total = 0;
    if (readback) {
        for (int i = 0; i < 3; i++) {
            offsets[i] = total;
            total += VkDeviceSize(s.planes[i].width) * s.planes[i].height *
                     (i == 0 && s.packed_luma ? 4 : i == 1 && s.dual_chroma ? 2 : 1);
        }
        VkBufferCreateInfo bi = { VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO };
        bi.size = total; bi.usage = VK_BUFFER_USAGE_TRANSFER_DST_BIT;
        VK_CHECK(vkCreateBuffer(g.device, &bi, nullptr, &buffer));
        VkMemoryRequirements req;
        vkGetBufferMemoryRequirements(g.device, buffer, &req);
        VkMemoryAllocateInfo ai = { VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO };
        ai.allocationSize = req.size;
        ai.memoryTypeIndex = memory_type(g, req.memoryTypeBits,
            VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT);
        VK_CHECK(vkAllocateMemory(g.device, &ai, nullptr, &memory));
        VK_CHECK(vkBindBufferMemory(g.device, buffer, memory, 0));
    }

    VK_CHECK(vkResetCommandBuffer(s.cmd, 0));
    VkCommandBufferBeginInfo begin = { VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO };
    begin.flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
    VK_CHECK(vkBeginCommandBuffer(s.cmd, &begin));
    for (auto &p : s.planes)
        barrier(s.cmd, p.image, s.initialized_layout ? VK_IMAGE_LAYOUT_GENERAL : VK_IMAGE_LAYOUT_UNDEFINED,
                VK_IMAGE_LAYOUT_GENERAL, VK_ACCESS_SHADER_WRITE_BIT | VK_ACCESS_TRANSFER_READ_BIT,
                VK_ACCESS_SHADER_WRITE_BIT, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT | VK_PIPELINE_STAGE_TRANSFER_BIT,
                VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT);
    s.initialized_layout = true;
    vkCmdResetQueryPool(s.cmd, s.queries, 0, 2);
    vkCmdWriteTimestamp(s.cmd, VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT, s.queries, 0);
    pyrowave_device_set_command_buffer(g.pyro, s.cmd);
    PW_CHECK(pyrowave_decoder_decode_gpu_buffer(s.decoder, nullptr, nullptr, &s.buffers));
    pyrowave_device_set_command_buffer(g.pyro, VK_NULL_HANDLE);
    vkCmdWriteTimestamp(s.cmd, VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT, s.queries, 1);
    if (readback) {
        for (int i = 0; i < 3; i++) {
            barrier(s.cmd, s.planes[i].image, VK_IMAGE_LAYOUT_GENERAL, VK_IMAGE_LAYOUT_GENERAL,
                    VK_ACCESS_SHADER_WRITE_BIT, VK_ACCESS_TRANSFER_READ_BIT,
                    VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT);
            VkBufferImageCopy copy = {};
            copy.bufferOffset = offsets[i];
            copy.imageSubresource = { VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1 };
            copy.imageExtent = { s.planes[i].width, s.planes[i].height, 1 };
            vkCmdCopyImageToBuffer(s.cmd, s.planes[i].image, VK_IMAGE_LAYOUT_GENERAL, buffer, 1, &copy);
        }
    }
    VK_CHECK(vkEndCommandBuffer(s.cmd));
    VK_CHECK(vkResetFences(g.device, 1, &s.fence));
    VkSubmitInfo submit = { VK_STRUCTURE_TYPE_SUBMIT_INFO };
    submit.commandBufferCount = 1; submit.pCommandBuffers = &s.cmd;
    VK_CHECK(vkQueueSubmit(g.queue, 1, &submit, s.fence));
    VK_CHECK(vkWaitForFences(g.device, 1, &s.fence, VK_TRUE, 60ull * 1000 * 1000 * 1000));

    uint64_t ticks[2] = {};
    VK_CHECK(vkGetQueryPoolResults(g.device, s.queries, 0, 2, sizeof ticks, ticks, sizeof(uint64_t),
                                   VK_QUERY_RESULT_64_BIT | VK_QUERY_RESULT_WAIT_BIT));
    if (readback) {
        readback->assign(3, {});
        void *mapped = nullptr;
        VK_CHECK(vkMapMemory(g.device, memory, 0, VK_WHOLE_SIZE, 0, &mapped));
        for (int i = 0; i < 3; i++) {
            const uint8_t *base = static_cast<uint8_t *>(mapped) + offsets[i];
            const size_t tw = s.planes[i].width, th = s.planes[i].height;
            if (i == 0 && s.packed_luma) {
                // Unpack the 2x2 quads back to a w x h plane for the comparison.
                (*readback)[i].resize(tw * th * 4);
                for (size_t y = 0; y < th * 2; y++)
                    for (size_t x = 0; x < tw * 2; x++)
                        (*readback)[i][y * tw * 2 + x] = base[((y / 2) * tw + x / 2) * 4 + (x & 1) + 2 * (y & 1)];
            } else if (i == 1 && s.dual_chroma) {
                // Split RG back into the Cb and Cr planes.
                (*readback)[1].resize(tw * th);
                (*readback)[2].resize(tw * th);
                for (size_t k = 0; k < tw * th; k++) {
                    (*readback)[1][k] = base[2 * k];
                    (*readback)[2][k] = base[2 * k + 1];
                }
            } else if (i == 2 && s.dual_chroma) {
                continue;
            } else
                (*readback)[i].assign(base, base + tw * th);
        }
        vkUnmapMemory(g.device, memory);
        vkDestroyBuffer(g.device, buffer, nullptr);
        vkFreeMemory(g.device, memory, nullptr);
    }
    return double(ticks[1] - ticks[0]) * ns_per_tick * 1e-6;
}

struct Diff { int max = 0; size_t differing = 0; };

Diff compare(const std::vector<uint8_t> &a, const std::vector<uint8_t> &b) {
    Diff r;
    if (a.size() != b.size()) { r.max = 256; return r; }
    for (size_t i = 0; i < a.size(); i++) {
        const int d = std::abs(int(a[i]) - int(b[i]));
        if (d) r.differing++;
        if (d > r.max) r.max = d;
    }
    return r;
}

double psnr(const std::vector<uint8_t> &a, const std::vector<uint8_t> &b) {
    double se = 0;
    for (size_t i = 0; i < a.size(); i++) { double d = double(a[i]) - double(b[i]); se += d * d; }
    const double mse = se / double(a.size());
    return mse == 0 ? 99.0 : 10.0 * std::log10(255.0 * 255.0 / mse);
}

std::string read_sysfs(const char *path) {
    char buf[64] = {};
    FILE *f = fopen(path, "r");
    if (!f) return "na";
    size_t n = fread(buf, 1, sizeof buf - 1, f);
    fclose(f);
    while (n && (buf[n - 1] == '\n' || buf[n - 1] == ' ')) buf[--n] = 0;
    return buf;
}

std::string gpu_clock() {
    std::string hz = read_sysfs("/sys/class/kgsl/kgsl-3d0/gpuclk");
    if (hz == "na") return hz;
    return std::to_string(atoll(hz.c_str()) / 1000000) + "MHz";
}

double percentile(std::vector<double> v, double p) {
    std::sort(v.begin(), v.end());
    return v.empty() ? 0 : v[std::min(v.size() - 1, size_t(p * double(v.size() - 1) + 0.5))];
}

} // namespace

// `decoder_ab wavelets [w h bytes]`: the default decoder on Haar, CDF 5/3 and CDF 9/7, plus Haar
// decoder mode 3, each on its own bitstream of the same byte cap, timed round-robin in one process
// (order reversed every other block) so all four see the same GPU clock. No exactness gate: the
// arms decode different bitstreams. Prints each arm's luma PSNR against the synthetic source.
int run_wavelets(uint32_t w, uint32_t h, size_t bytes, int blocks, int frames, bool stages) {
    struct WArm { const char *name; pyrowave_wavelet wavelet; const Arm *arm; bool fragment; };
    const Arm *mode3 = nullptr, *mode4 = nullptr, *v2 = nullptr, *v2q = nullptr, *v2qp = nullptr;
    for (const Arm &a : ARMS)
    {
        if (!strcmp(a.name, "haar32qo")) mode3 = &a;
        if (!strcmp(a.name, "haar32qd")) mode4 = &a;
        if (!strcmp(a.name, "cdf53v2")) v2 = &a;
        if (!strcmp(a.name, "cdf53v2q")) v2q = &a;
        if (!strcmp(a.name, "cdf53v2qp")) v2qp = &a;
    }
    const WArm warms[] = { { "haar", PYROWAVE_WAVELET_HAAR, nullptr, false },
                           { "haar_mode3", PYROWAVE_WAVELET_HAAR, mode3, false },
                           { "haar_mode4", PYROWAVE_WAVELET_HAAR, mode4, false },
                           { "cdf53", PYROWAVE_WAVELET_CDF53, nullptr, false },
                           { "cdf97", PYROWAVE_WAVELET_CDF97, nullptr, false },
                           { "cdf97_frag", PYROWAVE_WAVELET_CDF97, nullptr, true },
                           { "cdf53_v2", PYROWAVE_WAVELET_CDF53, v2, false },
                           { "cdf53_v2q", PYROWAVE_WAVELET_CDF53, v2q, false },
                           { "cdf53_v2qp", PYROWAVE_WAVELET_CDF53, v2qp, false } };
    // AB_ARMS=name,name,... runs a subset (default: all).
    std::vector<WArm> chosen;
    const char *only = getenv("AB_ARMS");
    for (const WArm &a : warms)
        if (!only || strstr((std::string(",") + only + ",").c_str(), (std::string(",") + a.name + ",").c_str()))
            chosen.push_back(a);
    const int n = int(chosen.size());
    Gpu g;
    create_gpu(g);
    VkPhysicalDeviceProperties props;
    vkGetPhysicalDeviceProperties(g.physical, &props);
    const double ns_per_tick = props.limits.timestampPeriod;
    std::vector<uint8_t> y, cb, cr;
    make_source(w, h, y, cb, cr);
    std::vector<std::vector<std::vector<uint8_t>>> packets(n);
    std::vector<Session> s;
    for (int i = 0; i < n; i++) {
        packets[i] = encode(w, h, bytes, y, cb, cr, chosen[i].wavelet);
        size_t total = 0;
        for (auto &p : packets[i]) total += p.size();
        s.push_back(create_session(g, w, h, chosen[i].arm, chosen[i].wavelet, chosen[i].fragment));
        PW_CHECK(pyrowave_decoder_set_timestamp_recording(s[i].decoder, stages ? 1 : 0));
        std::vector<std::vector<uint8_t>> out;
        decode(g, s[i], packets[i], ns_per_tick, &out);
        printf("arm=%s bytes=%zu luma_psnr=%.3f\n", chosen[i].name, total, psnr(out[0], y));
    }
    for (int warm = 0; warm < 10; warm++)
        for (int i = 0; i < n; i++) decode(g, s[i], packets[i], ns_per_tick);
    if (stages) pyrowave_device_report_performance_stats(g.pyro, [](void *, const char *) {}, nullptr, true);
    std::vector<std::vector<double>> all(n);
    for (int block = 0; block < blocks; block++) {
        for (int k = 0; k < n; k++) {
            const int i = block % 2 ? n - 1 - k : k;
            const std::string clock = gpu_clock();
            std::vector<double> ms;
            for (int f = 0; f < frames; f++) ms.push_back(decode(g, s[i], packets[i], ns_per_tick));
            all[i].insert(all[i].end(), ms.begin(), ms.end());
            printf("block=%d arm=%s p50=%.3f clock=%s", block, chosen[i].name, percentile(ms, 0.5), clock.c_str());
            if (stages)
                pyrowave_device_report_performance_stats(g.pyro, [](void *, const char *message) {
                    // "iDWT L<n>:" per level with PYROWAVE_V2_LEVEL_TIMES=1.
                    if (!strncmp(message, "Dequant:", 8) || !strncmp(message, "iDWT", 4)) {
                        std::string m(message);
                        while (!m.empty() && m.back() == '\n') m.pop_back();
                        printf(" | %s", m.c_str());
                    }
                }, nullptr, true);
            printf("\n");
        }
    }
    printf("SUMMARY %ux%u cap=%zu", w, h, bytes);
    for (int i = 0; i < n; i++) printf(" %s_p50=%.3f", chosen[i].name, percentile(all[i], 0.5));
    printf("\n");
    for (auto &x : s) destroy_session(g, x);
    pyrowave_device_destroy(g.pyro);
    vkDestroyCommandPool(g.device, g.pool, nullptr);
    vkDestroyDevice(g.device, nullptr);
    vkDestroyInstance(g.instance, nullptr);
    return 0;
}

int main(int argc, char **argv) {
    setvbuf(stdout, nullptr, _IONBF, 0);
    if (argc >= 2 && !strcmp(argv[1], "wavelets"))
        return run_wavelets(argc > 4 ? atoi(argv[2]) : 4160, argc > 4 ? atoi(argv[3]) : 2208,
                            argc > 4 ? size_t(atoll(argv[4])) : 603864,
                            getenv("AB_BLOCKS") ? atoi(getenv("AB_BLOCKS")) : 4,
                            getenv("AB_FRAMES") ? atoi(getenv("AB_FRAMES")) : 30,
                            getenv("AB_STAGES") && !strcmp(getenv("AB_STAGES"), "1"));
    if (argc < 2) { fprintf(stderr, "usage: decoder_ab <arm> [width height max_bytes]...\n"); return 2; }
    const Arm *arm = nullptr;
    for (const Arm &a : ARMS)
        if (!strcmp(a.name, argv[1])) arm = &a;
    if (!arm) { fprintf(stderr, "unknown arm %s\n", argv[1]); return 2; }

    struct Case { uint32_t w, h; size_t bytes; };
    std::vector<Case> cases;
    for (int i = 2; i + 2 < argc; i += 3)
        cases.push_back({ uint32_t(atoi(argv[i])), uint32_t(atoi(argv[i + 1])), size_t(atoll(argv[i + 2])) });
    if (cases.empty()) cases = { { 512, 320, 131072 }, { 1000, 600, 262144 }, { 4160, 2208, 1041667 } };
    const double min_psnr = getenv("AB_MIN_PSNR") ? atof(getenv("AB_MIN_PSNR")) : 30.0;
    const int blocks = getenv("AB_BLOCKS") ? atoi(getenv("AB_BLOCKS")) : 8;
    const int frames = getenv("AB_FRAMES") ? atoi(getenv("AB_FRAMES")) : 40;
    const bool stages = getenv("AB_STAGES") && !strcmp(getenv("AB_STAGES"), "1");
    // AB_ALLOW_DIFF=n accepts per-pixel differences up to n (default 0, exact) so a near-exact
    // arm can still be timed; the gate line then says WITHIN instead of EXACT.
    const int allowed_diff = getenv("AB_ALLOW_DIFF") ? atoi(getenv("AB_ALLOW_DIFF")) : 0;

    Gpu g;
    create_gpu(g);
    VkPhysicalDeviceProperties props;
    vkGetPhysicalDeviceProperties(g.physical, &props);
    const double ns_per_tick = props.limits.timestampPeriod;
    printf("arm=%s clock=%s\n", arm->name, gpu_clock().c_str());

    bool ok = true;
    std::vector<std::vector<uint8_t>> bench_packets;
    for (const Case &c : cases) {
        printf("case %ux%u cap %zu\n", c.w, c.h, c.bytes);
        std::vector<uint8_t> y, cb, cr;
        make_source(c.w, c.h, y, cb, cr);
        const auto packets = encode(c.w, c.h, c.bytes, y, cb, cr);
        Session a = create_session(g, c.w, c.h, nullptr);
        Session b = create_session(g, c.w, c.h, arm);
        std::vector<std::vector<uint8_t>> pa, pb;
        decode(g, a, packets, ns_per_tick, &pa);
        decode(g, b, packets, ns_per_tick, &pb);
        destroy_session(g, a);
        destroy_session(g, b);
        const Diff dy = compare(pa[0], pb[0]), dcb = compare(pa[1], pb[1]), dcr = compare(pa[2], pb[2]);
        const double luma_psnr = psnr(pa[0], y);
        std::vector<int> seen(256);
        for (uint8_t v : pa[0]) seen[v] = 1;
        int distinct = 0;
        for (int v : seen) distinct += v;
        const int max_diff = std::max(dy.max, std::max(dcb.max, dcr.max));
        const bool pass = max_diff <= allowed_diff && distinct >= 64 && luma_psnr >= min_psnr;
        printf("  luma_psnr=%.3f dB arm_luma_psnr=%.3f dB distinct_luma=%d differing luma=%zu cb=%zu cr=%zu "
               "max_diff=%d %s\n", luma_psnr, psnr(pb[0], y), distinct, dy.differing, dcb.differing, dcr.differing,
               max_diff, max_diff == 0 && pass ? "EXACT" : pass ? "WITHIN" : "FAIL");
        ok = ok && (pass || (getenv("AB_SKIP_GATE") && !strcmp(getenv("AB_SKIP_GATE"), "1")));
        bench_packets = packets;
    }
    printf(ok ? "DECODER_AB_GATE_PASS\n" : "DECODER_AB_GATE_FAIL\n");

    if (ok && !(getenv("AB_GATE_ONLY") && !strcmp(getenv("AB_GATE_ONLY"), "1"))) {
        const Case &c = cases.back();
        Session s[2] = { create_session(g, c.w, c.h, nullptr), create_session(g, c.w, c.h, arm) };
        for (auto &x : s) PW_CHECK(pyrowave_decoder_set_timestamp_recording(x.decoder, stages ? 1 : 0));
        std::vector<double> all[2], block_medians[2];
        for (int warm = 0; warm < 10; warm++)
            for (auto &x : s) decode(g, x, bench_packets, ns_per_tick);
        if (stages) pyrowave_device_report_performance_stats(g.pyro, [](void *, const char *) {}, nullptr, true);
        for (int block = 0; block < blocks; block++) {
            const int which = (block % 4 == 0 || block % 4 == 3) ? 0 : 1;
            const std::string clock_before = gpu_clock();
            std::vector<double> ms;
            for (int f = 0; f < frames; f++) ms.push_back(decode(g, s[which], bench_packets, ns_per_tick));
            const std::string clock_after = gpu_clock();
            all[which].insert(all[which].end(), ms.begin(), ms.end());
            block_medians[which].push_back(percentile(ms, 0.5));
            printf("block=%d arm=%s frames=%d gpu_ms p50=%.3f p90=%.3f min=%.3f clock=%s->%s\n", block,
                   which ? arm->name : "base", frames, percentile(ms, 0.5), percentile(ms, 0.9),
                   percentile(ms, 0.0), clock_before.c_str(), clock_after.c_str());
            if (stages)
                pyrowave_device_report_performance_stats(g.pyro, [](void *, const char *message) {
                    if (!strncmp(message, "Dequant:", 8) || !strncmp(message, "iDWT:", 5))
                        printf("  stage %s\n", message);
                }, nullptr, true);
        }
        const double a50 = percentile(all[0], 0.5), b50 = percentile(all[1], 0.5);
        printf("SUMMARY arm=%s case=%ux%u cap=%zu base_p50=%.3f arm_p50=%.3f base_p90=%.3f arm_p90=%.3f "
               "delta=%+.1f%% block_medians base=[", arm->name, c.w, c.h, c.bytes, a50, b50,
               percentile(all[0], 0.9), percentile(all[1], 0.9), 100.0 * (b50 - a50) / a50);
        for (double m : block_medians[0]) printf(" %.3f", m);
        printf(" ] arm=[");
        for (double m : block_medians[1]) printf(" %.3f", m);
        printf(" ]\n");
        for (auto &x : s) destroy_session(g, x);
    }

    pyrowave_device_destroy(g.pyro);
    vkDestroyCommandPool(g.device, g.pool, nullptr);
    vkDestroyDevice(g.device, nullptr);
    vkDestroyInstance(g.instance, nullptr);
    return ok ? 0 : 1;
}
