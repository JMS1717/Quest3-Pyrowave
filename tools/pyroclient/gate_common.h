// Shared helpers for the standalone PyroWave decoder gates (dequant_haar_gate, haar32_gate):
// a Vulkan device that PyroWave borrows, R8 plane images, a deterministic test frame and a
// Haar encode to packets.
#pragma once
#include <vulkan/vulkan.h>
#include "pyrowave.h"

#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

#define VK_CHECK(x) do { VkResult r_ = (x); if (r_ != VK_SUCCESS) { \
    fprintf(stderr, "%s:%d %s = %d\n", __FILE__, __LINE__, #x, int(r_)); exit(2); } } while (0)
#define PW_CHECK(x) do { pyrowave_result r_ = (x); if (r_ != PYROWAVE_SUCCESS) { \
    fprintf(stderr, "%s:%d %s = %d\n", __FILE__, __LINE__, #x, int(r_)); exit(2); } } while (0)

namespace {

struct Image {
    VkImage image = VK_NULL_HANDLE;
    VkDeviceMemory memory = VK_NULL_HANDLE;
    VkImageView view = VK_NULL_HANDLE;
    uint32_t width = 0, height = 0;
};

struct Gpu {
    VkInstance instance = VK_NULL_HANDLE;
    VkPhysicalDevice physical = VK_NULL_HANDLE;
    VkDevice device = VK_NULL_HANDLE;
    VkQueue queue = VK_NULL_HANDLE;
    uint32_t family = 0;
    VkCommandPool pool = VK_NULL_HANDLE;
    VkApplicationInfo app = {};
    VkInstanceCreateInfo instance_info = {};
    VkDeviceQueueCreateInfo queue_info = {};
    VkDeviceCreateInfo device_info = {};
    VkPhysicalDeviceFeatures2 f2 = {};
    VkPhysicalDeviceVulkan11Features f11 = {};
    VkPhysicalDeviceVulkan12Features f12 = {};
    VkPhysicalDeviceVulkan13Features f13 = {};
    float priority = 1.0f;
    pyrowave_device pyro = nullptr;
};

uint32_t memory_type(const Gpu &g, uint32_t bits, VkMemoryPropertyFlags want) {
    VkPhysicalDeviceMemoryProperties p;
    vkGetPhysicalDeviceMemoryProperties(g.physical, &p);
    for (uint32_t i = 0; i < p.memoryTypeCount; i++)
        if ((bits & (1u << i)) && (p.memoryTypes[i].propertyFlags & want) == want) return i;
    fprintf(stderr, "no memory type\n");
    exit(2);
}

void create_gpu(Gpu &g) {
    g.app = { VK_STRUCTURE_TYPE_APPLICATION_INFO };
    g.app.pApplicationName = "q3pw_gate";
    g.app.apiVersion = VK_API_VERSION_1_3;
    g.instance_info = { VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO };
    g.instance_info.pApplicationInfo = &g.app;
    VK_CHECK(vkCreateInstance(&g.instance_info, nullptr, &g.instance));
    uint32_t n = 0;
    vkEnumeratePhysicalDevices(g.instance, &n, nullptr);
    std::vector<VkPhysicalDevice> gpus(n);
    vkEnumeratePhysicalDevices(g.instance, &n, gpus.data());
    if (gpus.empty()) { fprintf(stderr, "no Vulkan device\n"); exit(2); }
    g.physical = gpus[0];
    VkPhysicalDeviceProperties props;
    vkGetPhysicalDeviceProperties(g.physical, &props);
    printf("device: %s\n", props.deviceName);

    uint32_t fc = 0;
    vkGetPhysicalDeviceQueueFamilyProperties(g.physical, &fc, nullptr);
    std::vector<VkQueueFamilyProperties> fams(fc);
    vkGetPhysicalDeviceQueueFamilyProperties(g.physical, &fc, fams.data());
    g.family = UINT32_MAX;
    for (uint32_t i = 0; i < fc; i++)
        if ((fams[i].queueFlags & (VK_QUEUE_GRAPHICS_BIT | VK_QUEUE_COMPUTE_BIT)) ==
            (VK_QUEUE_GRAPHICS_BIT | VK_QUEUE_COMPUTE_BIT)) { g.family = i; break; }
    if (g.family == UINT32_MAX) { fprintf(stderr, "no graphics+compute queue\n"); exit(2); }

    // Same approach as pyroclient: enable exactly what the driver reports.
    g.queue_info = { VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO };
    g.queue_info.queueFamilyIndex = g.family;
    g.queue_info.queueCount = 1;
    g.queue_info.pQueuePriorities = &g.priority;
    g.f13 = { VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_3_FEATURES };
    g.f12 = { VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES };
    g.f11 = { VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_1_FEATURES };
    g.f2 = { VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2 };
    g.f2.pNext = &g.f11; g.f11.pNext = &g.f12; g.f12.pNext = &g.f13;
    vkGetPhysicalDeviceFeatures2(g.physical, &g.f2);
    g.device_info = { VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO };
    g.device_info.pNext = &g.f2;
    g.device_info.queueCreateInfoCount = 1;
    g.device_info.pQueueCreateInfos = &g.queue_info;
    VK_CHECK(vkCreateDevice(g.physical, &g.device_info, nullptr, &g.device));
    vkGetDeviceQueue(g.device, g.family, 0, &g.queue);

    pyrowave_device_create_info pi = {};
    pi.GetInstanceProcAddr = vkGetInstanceProcAddr;
    pi.instance = g.instance;
    pi.physical_device = g.physical;
    pi.device = g.device;
    pi.instance_create_info = &g.instance_info;
    pi.device_create_info = &g.device_info;
    PW_CHECK(pyrowave_create_device(&pi, &g.pyro));
    PW_CHECK(pyrowave_device_set_queue_type(g.pyro, VK_QUEUE_COMPUTE_BIT));

    VkCommandPoolCreateInfo pool = { VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO };
    pool.queueFamilyIndex = g.family;
    pool.flags = VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT;
    VK_CHECK(vkCreateCommandPool(g.device, &pool, nullptr, &g.pool));
}

Image create_image(const Gpu &g, VkFormat format, uint32_t w, uint32_t h, VkImageUsageFlags usage) {
    Image img; img.width = w; img.height = h;
    VkImageCreateInfo ci = { VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO };
    ci.imageType = VK_IMAGE_TYPE_2D; ci.format = format; ci.extent = { w, h, 1 };
    ci.mipLevels = 1; ci.arrayLayers = 1; ci.samples = VK_SAMPLE_COUNT_1_BIT;
    ci.tiling = VK_IMAGE_TILING_OPTIMAL; ci.usage = usage;
    ci.initialLayout = VK_IMAGE_LAYOUT_UNDEFINED;
    VK_CHECK(vkCreateImage(g.device, &ci, nullptr, &img.image));
    VkMemoryRequirements req;
    vkGetImageMemoryRequirements(g.device, img.image, &req);
    VkMemoryAllocateInfo ai = { VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO };
    ai.allocationSize = req.size;
    ai.memoryTypeIndex = memory_type(g, req.memoryTypeBits, VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT);
    VK_CHECK(vkAllocateMemory(g.device, &ai, nullptr, &img.memory));
    VK_CHECK(vkBindImageMemory(g.device, img.image, img.memory, 0));
    VkImageViewCreateInfo vi = { VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO };
    vi.image = img.image; vi.viewType = VK_IMAGE_VIEW_TYPE_2D; vi.format = format;
    vi.subresourceRange = { VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1 };
    VK_CHECK(vkCreateImageView(g.device, &vi, nullptr, &img.view));
    return img;
}

void destroy_image(const Gpu &g, Image &img) {
    vkDestroyImageView(g.device, img.view, nullptr);
    vkDestroyImage(g.device, img.image, nullptr);
    vkFreeMemory(g.device, img.memory, nullptr);
    img = {};
}

void barrier(VkCommandBuffer cmd, VkImage image, VkImageLayout from, VkImageLayout to,
             VkAccessFlags src_access, VkAccessFlags dst_access,
             VkPipelineStageFlags src_stage, VkPipelineStageFlags dst_stage) {
    VkImageMemoryBarrier b = { VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER };
    b.oldLayout = from; b.newLayout = to; b.srcAccessMask = src_access; b.dstAccessMask = dst_access;
    b.srcQueueFamilyIndex = b.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
    b.image = image; b.subresourceRange = { VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1 };
    vkCmdPipelineBarrier(cmd, src_stage, dst_stage, 0, 0, nullptr, 0, nullptr, 1, &b);
}

// AB_CHROMA=444 encodes and decodes 4:4:4 instead of 4:2:0.
inline bool ab_chroma444() {
    const char *c = getenv("AB_CHROMA");
    return c && !strcmp(c, "444");
}

// Deterministic, asymmetric content: gradients, a hard diagonal edge, fine stripes, colored
// blocks and hashed noise, so orientation, eye-edge and rounding errors all show up.
// AB_SOURCE=<file> loads a raw I420 frame of the same size instead (Y, then Cb, then Cr; I444
// with AB_CHROMA=444).
void make_source(uint32_t w, uint32_t h, std::vector<uint8_t> &y, std::vector<uint8_t> &cb,
                 std::vector<uint8_t> &cr) {
    const uint32_t cw = ab_chroma444() ? w : w / 2, ch = ab_chroma444() ? h : h / 2;
    y.resize(size_t(w) * h); cb.resize(size_t(cw) * ch); cr.resize(cb.size());
    if (const char *path = getenv("AB_SOURCE")) {
        FILE *f = fopen(path, "rb");
        if (!f) { fprintf(stderr, "AB_SOURCE: cannot open %s\n", path); exit(1); }
        const bool ok = fread(y.data(), 1, y.size(), f) == y.size() && fread(cb.data(), 1, cb.size(), f) == cb.size() &&
                        fread(cr.data(), 1, cr.size(), f) == cr.size();
        fclose(f);
        if (!ok) { fprintf(stderr, "AB_SOURCE: %s is smaller than a %ux%u I420 frame\n", path, w, h); exit(1); }
        return;
    }
    auto hash = [](uint32_t x) { x ^= x >> 16; x *= 0x7feb352du; x ^= x >> 15; x *= 0x846ca68bu; x ^= x >> 16; return x; };
    for (uint32_t j = 0; j < h; j++)
        for (uint32_t i = 0; i < w; i++) {
            int v = int(255.0 * i / (w - 1) * 0.6 + 255.0 * j / (h - 1) * 0.3);
            if (i * h > j * w * 2 / 3) v = 255 - v;
            if (i % 7 < 2 && j < h / 3) v = (j & 1) ? 16 : 235;
            v += int(hash(j * w + i) % 9) - 4;
            y[size_t(j) * w + i] = uint8_t(v < 0 ? 0 : v > 255 ? 255 : v);
        }
    for (uint32_t j = 0; j < ch; j++)
        for (uint32_t i = 0; i < cw; i++) {
            int u = 128 + int(100.0 * std::sin(i * 0.031) * ((j / 16) % 2 ? 1 : -1));
            int v = 128 + int(90.0 * std::cos(j * 0.023 + i * 0.002));
            if ((i / 24 + j / 24) % 5 == 0) { u = 40; v = 220; }
            // 4:4:4: per-pixel detail that 4:2:0 could not carry.
            if (cw == w && ((i ^ j) & 1)) u += 20;
            cb[size_t(j) * cw + i] = uint8_t(u < 0 ? 0 : u > 255 ? 255 : u);
            cr[size_t(j) * cw + i] = uint8_t(v < 0 ? 0 : v > 255 ? 255 : v);
        }
}

// AB_WAVELET=53 or 97 selects CDF 5/3 or 9/7 for both the encode and the decoders; default Haar.
inline pyrowave_wavelet ab_wavelet() {
    const char *w = getenv("AB_WAVELET");
    if (w && !strcmp(w, "53")) return PYROWAVE_WAVELET_CDF53;
    if (w && !strcmp(w, "97")) return PYROWAVE_WAVELET_CDF97;
    return PYROWAVE_WAVELET_HAAR;
}

// Encode on a separate default device: a borrowed device owns no queue for PyroWave's own submits.
std::vector<std::vector<uint8_t>> encode(uint32_t w, uint32_t h, size_t max_bytes, const std::vector<uint8_t> &y,
                                         const std::vector<uint8_t> &cb, const std::vector<uint8_t> &cr,
                                         pyrowave_wavelet wavelet = ab_wavelet()) {
    pyrowave_device dev = nullptr;
    PW_CHECK(pyrowave_create_default_device(&dev));
    pyrowave_encoder_create_info ei = {};
    ei.device = dev; ei.width = int(w); ei.height = int(h);
    const bool full = ab_chroma444();
    ei.chroma = full ? PYROWAVE_CHROMA_SUBSAMPLING_444 : PYROWAVE_CHROMA_SUBSAMPLING_420; ei.wavelet = wavelet;
    pyrowave_encoder enc = nullptr;
    PW_CHECK(pyrowave_encoder_create(&ei, &enc));
    pyrowave_cpu_buffer buf = {};
    buf.data[0] = const_cast<uint8_t *>(y.data());
    buf.data[1] = const_cast<uint8_t *>(cb.data());
    buf.data[2] = const_cast<uint8_t *>(cr.data());
    buf.row_stride_in_bytes[0] = w; buf.row_stride_in_bytes[1] = buf.row_stride_in_bytes[2] = full ? w : w / 2;
    buf.plane_size_in_bytes[0] = y.size(); buf.plane_size_in_bytes[1] = cb.size(); buf.plane_size_in_bytes[2] = cr.size();
    buf.width = int(w); buf.height = int(h);
    buf.format = full ? PYROWAVE_CPU_BUFFER_FORMAT_YUV444P : PYROWAVE_CPU_BUFFER_FORMAT_YUV420P;
    pyrowave_rate_control rc = { max_bytes };
    PW_CHECK(pyrowave_encoder_encode_cpu_synchronous(enc, &buf, &rc));
    const size_t boundary = 8192;
    size_t count = 0;
    PW_CHECK(pyrowave_encoder_compute_num_packets(enc, boundary, &count));
    std::vector<pyrowave_packet> packets(count);
    std::vector<uint8_t> bits(count * boundary + 65536);
    size_t produced = 0;
    PW_CHECK(pyrowave_encoder_packetize(enc, packets.data(), boundary, &produced, bits.data(), bits.size()));
    std::vector<std::vector<uint8_t>> out;
    size_t total = 0;
    for (size_t i = 0; i < produced; i++) {
        out.emplace_back(bits.begin() + packets[i].offset, bits.begin() + packets[i].offset + packets[i].size);
        total += packets[i].size;
    }
    printf("  encoded %zu bytes in %zu packets (cap %zu)\n", total, produced, max_bytes);
    pyrowave_encoder_destroy(enc);
    pyrowave_device_destroy(dev);
    return out;
}

} // namespace
