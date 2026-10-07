// Exact-pixel gate for the fused dequant + level-0 Haar kernel, runnable without a headset.
//
// Encodes a deterministic asymmetric 4:2:0 frame with PyroWave Haar, decodes it twice on one
// Vulkan device and compares the three R8 planes:
//   A: current path. Dequant stores every band to the wavelet image; the final Haar pass
//      reloads level 0 and writes luma.
//   B: pyrowave_decoder_set_fused_dequant_haar(1). The final pass decodes the level-0 luma
//      bands in registers (wavelet_dequant_haar0.comp) and writes the same luma plane.
// Chroma never passes through the fused kernel, so it must match exactly. On CI this runs on
// Mesa lavapipe, which proves path equivalence for the same bitstream, not Adreno rounding or
// decode fidelity; repeat on Quest (default GATE_MIN_PSNR=30) before any streaming A/B.
//
// Usage: dequant_haar_gate [width height max_bytes]...   (default: 512 320 131072 and 4160 2208 2083333)
// Exit 0 when every case has luma difference <= 1, identical chroma, a non-trivial decoded
// luma plane and luma PSNR >= GATE_MIN_PSNR. GATE_ENCODE_ONLY / GATE_DECODE_ONLY (+
// GATE_PACKET_DIR) split encode and decode across processes: lavapipe encodes at 512-bit (the
// encoder needs 16 lanes) and decodes at 256-bit. GATE_DUMP_DIR writes the source and both
// decoded luma planes as PGM files.

#include "gate_common.h"

namespace {

// Decode one frame with a fresh decoder and read the three planes back.
std::vector<std::vector<uint8_t>> run(const Gpu &g, uint32_t w, uint32_t h,
                                      const std::vector<std::vector<uint8_t>> &packets, bool fused) {
    pyrowave_decoder_create_info di = {};
    di.device = g.pyro; di.width = int(w); di.height = int(h);
    di.chroma = PYROWAVE_CHROMA_SUBSAMPLING_420; di.fragment_path = false; di.wavelet = PYROWAVE_WAVELET_HAAR;
    pyrowave_decoder dec = nullptr;
    PW_CHECK(pyrowave_decoder_create(&di, &dec));
    if (fused) PW_CHECK(pyrowave_decoder_set_fused_dequant_haar(dec, 1));
    for (const auto &p : packets) PW_CHECK(pyrowave_decoder_push_packet(dec, p.data(), p.size()));
    if (!pyrowave_decoder_decode_is_ready(dec, false)) { fprintf(stderr, "frame incomplete\n"); exit(2); }

    const VkImageUsageFlags usage = VK_IMAGE_USAGE_SAMPLED_BIT | VK_IMAGE_USAGE_STORAGE_BIT |
                                    VK_IMAGE_USAGE_TRANSFER_SRC_BIT;
    Image planes[3] = {
        create_image(g, VK_FORMAT_R8_UNORM, w, h, usage),
        create_image(g, VK_FORMAT_R8_UNORM, w / 2, h / 2, usage),
        create_image(g, VK_FORMAT_R8_UNORM, w / 2, h / 2, usage),
    };
    pyrowave_gpu_buffers buffers = {};
    VkDeviceSize offsets[3], total = 0;
    for (int i = 0; i < 3; i++) {
        pyrowave_image_view &v = buffers.planes[i];
        v.image = planes[i].image; v.width = int(planes[i].width); v.height = int(planes[i].height);
        v.image_format = VK_FORMAT_R8_UNORM; v.view_format = VK_FORMAT_R8_UNORM;
        v.mip_level = 0; v.layer = 0; v.aspect = VK_IMAGE_ASPECT_COLOR_BIT;
        v.swizzle = VK_COMPONENT_SWIZZLE_IDENTITY; v.layout = VK_IMAGE_LAYOUT_GENERAL;
        offsets[i] = total;
        total += VkDeviceSize(planes[i].width) * planes[i].height;
    }

    VkBufferCreateInfo bi = { VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO };
    bi.size = total; bi.usage = VK_BUFFER_USAGE_TRANSFER_DST_BIT;
    VkBuffer readback;
    VK_CHECK(vkCreateBuffer(g.device, &bi, nullptr, &readback));
    VkMemoryRequirements req;
    vkGetBufferMemoryRequirements(g.device, readback, &req);
    VkMemoryAllocateInfo ai = { VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO };
    ai.allocationSize = req.size;
    ai.memoryTypeIndex = memory_type(g, req.memoryTypeBits,
        VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT);
    VkDeviceMemory readback_memory;
    VK_CHECK(vkAllocateMemory(g.device, &ai, nullptr, &readback_memory));
    VK_CHECK(vkBindBufferMemory(g.device, readback, readback_memory, 0));

    VkCommandBufferAllocateInfo ca = { VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO };
    ca.commandPool = g.pool; ca.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY; ca.commandBufferCount = 1;
    VkCommandBuffer cmd;
    VK_CHECK(vkAllocateCommandBuffers(g.device, &ca, &cmd));
    VkCommandBufferBeginInfo begin = { VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO };
    begin.flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
    VK_CHECK(vkBeginCommandBuffer(cmd, &begin));
    for (auto &p : planes)
        barrier(cmd, p.image, VK_IMAGE_LAYOUT_UNDEFINED, VK_IMAGE_LAYOUT_GENERAL, 0, VK_ACCESS_SHADER_WRITE_BIT,
                VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT);
    pyrowave_device_set_command_buffer(g.pyro, cmd);
    PW_CHECK(pyrowave_decoder_decode_gpu_buffer(dec, nullptr, nullptr, &buffers));
    pyrowave_device_set_command_buffer(g.pyro, VK_NULL_HANDLE);
    for (int i = 0; i < 3; i++) {
        barrier(cmd, planes[i].image, VK_IMAGE_LAYOUT_GENERAL, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL,
                VK_ACCESS_SHADER_WRITE_BIT, VK_ACCESS_TRANSFER_READ_BIT,
                VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT);
        VkBufferImageCopy copy = {};
        copy.bufferOffset = offsets[i];
        copy.imageSubresource = { VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1 };
        copy.imageExtent = { planes[i].width, planes[i].height, 1 };
        vkCmdCopyImageToBuffer(cmd, planes[i].image, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, readback, 1, &copy);
    }
    VK_CHECK(vkEndCommandBuffer(cmd));
    VkFence fence;
    VkFenceCreateInfo fi = { VK_STRUCTURE_TYPE_FENCE_CREATE_INFO };
    VK_CHECK(vkCreateFence(g.device, &fi, nullptr, &fence));
    VkSubmitInfo submit = { VK_STRUCTURE_TYPE_SUBMIT_INFO };
    submit.commandBufferCount = 1; submit.pCommandBuffers = &cmd;
    VK_CHECK(vkQueueSubmit(g.queue, 1, &submit, fence));
    VK_CHECK(vkWaitForFences(g.device, 1, &fence, VK_TRUE, 60ull * 1000 * 1000 * 1000));

    std::vector<std::vector<uint8_t>> out(3);
    void *mapped = nullptr;
    VK_CHECK(vkMapMemory(g.device, readback_memory, 0, VK_WHOLE_SIZE, 0, &mapped));
    for (int i = 0; i < 3; i++) {
        const uint8_t *base = static_cast<uint8_t *>(mapped) + offsets[i];
        out[i].assign(base, base + size_t(planes[i].width) * planes[i].height);
    }
    vkUnmapMemory(g.device, readback_memory);

    VK_CHECK(vkDeviceWaitIdle(g.device));
    vkDestroyFence(g.device, fence, nullptr);
    vkFreeCommandBuffers(g.device, g.pool, 1, &cmd);
    vkDestroyBuffer(g.device, readback, nullptr);
    vkFreeMemory(g.device, readback_memory, nullptr);
    for (auto &p : planes) destroy_image(g, p);
    pyrowave_decoder_destroy(dec);
    return out;
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

void write_pgm(const char *dir, uint32_t w, uint32_t h, const char *what, const std::vector<uint8_t> &plane) {
    char name[512];
    snprintf(name, sizeof name, "%s/dequant_haar_gate_%ux%u_%s.pgm", dir, w, h, what);
    if (FILE *f = fopen(name, "wb")) {
        fprintf(f, "P5\n%u %u\n255\n", w, h);
        fwrite(plane.data(), 1, plane.size(), f);
        fclose(f);
    }
}

} // namespace

int main(int argc, char **argv) {
    struct Case { uint32_t w, h; size_t bytes; };
    std::vector<Case> cases;
    for (int i = 1; i + 2 < argc; i += 3)
        cases.push_back({ uint32_t(atoi(argv[i])), uint32_t(atoi(argv[i + 1])), size_t(atoll(argv[i + 2])) });
    if (cases.empty()) cases = { { 512, 320, 131072 }, { 4160, 2208, 2083333 } };

    setvbuf(stdout, nullptr, _IONBF, 0);
    // Decode fidelity floor against the source. Lavapipe's PyroWave decode is not faithful
    // (about 9-13 dB), so CI sets GATE_MIN_PSNR=0 and checks only path equivalence there.
    const double min_psnr = getenv("GATE_MIN_PSNR") ? atof(getenv("GATE_MIN_PSNR")) : 30.0;
    Gpu g;
    create_gpu(g);
    // Reject a decoder that silently accepts an unsupported combination.
    {
        pyrowave_decoder_create_info di = {};
        di.device = g.pyro; di.width = 64; di.height = 64; di.chroma = PYROWAVE_CHROMA_SUBSAMPLING_444;
        di.wavelet = PYROWAVE_WAVELET_HAAR;
        pyrowave_decoder dec = nullptr;
        PW_CHECK(pyrowave_decoder_create(&di, &dec));
        const bool rejected444 = pyrowave_decoder_set_fused_dequant_haar(dec, 1) != PYROWAVE_SUCCESS;
        pyrowave_decoder_destroy(dec);
        di.chroma = PYROWAVE_CHROMA_SUBSAMPLING_420; di.wavelet = PYROWAVE_WAVELET_CDF97;
        PW_CHECK(pyrowave_decoder_create(&di, &dec));
        const bool rejected97 = pyrowave_decoder_set_fused_dequant_haar(dec, 1) != PYROWAVE_SUCCESS;
        pyrowave_decoder_destroy(dec);
        printf("unsupported 4:4:4 rejected=%d, CDF 9/7 rejected=%d\n", rejected444, rejected97);
        if (!rejected444 || !rejected97) return 1;
    }

    bool ok = true;
    for (const Case &c : cases) {
        printf("case %ux%u cap %zu\n", c.w, c.h, c.bytes);
        std::vector<uint8_t> y, cb, cr;
        make_source(c.w, c.h, y, cb, cr);
        std::vector<std::vector<uint8_t>> packets;
        char path[256];
        snprintf(path, sizeof path, "%s/dequant_haar_gate_%ux%u_%zu.packets", getenv("GATE_PACKET_DIR") ? getenv("GATE_PACKET_DIR") : ".", c.w, c.h, c.bytes);
        if (getenv("GATE_DECODE_ONLY")) {
            FILE *f = fopen(path, "rb");
            if (!f) { fprintf(stderr, "missing %s\n", path); return 2; }
            uint32_t n = 0;
            while (fread(&n, 4, 1, f) == 1) { packets.emplace_back(n); if (fread(packets.back().data(), 1, n, f) != n) return 2; }
            fclose(f);
            printf("  loaded %zu packets from %s\n", packets.size(), path);
        } else {
            packets = encode(c.w, c.h, c.bytes, y, cb, cr);
            if (getenv("GATE_ENCODE_ONLY")) {
                FILE *f = fopen(path, "wb");
                for (const auto &p : packets) { uint32_t n = uint32_t(p.size()); fwrite(&n, 4, 1, f); fwrite(p.data(), 1, n, f); }
                fclose(f);
                printf("  wrote %s\n", path);
                continue;
            }
        }
        const auto a = run(g, c.w, c.h, packets, false);
        const auto b = run(g, c.w, c.h, packets, true);
        const Diff luma = compare(a[0], b[0]);
        const Diff cb_diff = compare(a[1], b[1]);
        const Diff cr_diff = compare(a[2], b[2]);
        const double luma_psnr = psnr(a[0], y);
        if (const char *dir = getenv("GATE_DUMP_DIR")) {
            write_pgm(dir, c.w, c.h, "source", y);
            write_pgm(dir, c.w, c.h, "current", a[0]);
            write_pgm(dir, c.w, c.h, "fused", b[0]);
        }
        std::vector<int> seen(256);
        for (uint8_t v : a[0]) seen[v] = 1;
        int distinct = 0;
        for (int v : seen) distinct += v;
        // Identical but trivial planes (blank decode) must not pass.
        const bool nontrivial = distinct >= 64;
        const bool pass = luma.max <= 1 && cb_diff.max == 0 && cr_diff.max == 0 && nontrivial && luma_psnr >= min_psnr;
        printf("  luma_psnr=%.2f dB distinct_luma=%d luma_max_diff=%d luma_differing=%zu/%zu chroma_max_diff=%d %s\n",
               luma_psnr, distinct, luma.max, luma.differing, a[0].size(),
               cb_diff.max > cr_diff.max ? cb_diff.max : cr_diff.max, pass ? "PASS" : "FAIL");
        ok = ok && pass;
    }
    pyrowave_device_destroy(g.pyro);
    vkDestroyCommandPool(g.device, g.pool, nullptr);
    vkDestroyDevice(g.device, nullptr);
    vkDestroyInstance(g.instance, nullptr);
    printf(ok ? "DEQUANT_HAAR_GATE_PASS\n" : "DEQUANT_HAAR_GATE_FAIL\n");
    return ok ? 0 : 1;
}
