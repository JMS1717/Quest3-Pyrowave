// Exact-pixel equivalence for the experimental debug.q3pw.fuse_color pass. No headset: CI runs it
// on Mesa lavapipe; on a real GPU it would compare that GPU's own two paths.
//
// On one Vulkan device and identical inputs it records
//   reference: PyroWave's shipped idwt.comp SPIR-V (HAAR, DCShift; extracted from slangmosh.hpp
//              by extract_pyrowave_spirv.py) into an R8 plane, then the shipped convert.frag;
//   fused:     the fuse_color.frag variant pyroclient would pick for the same FP16 choice;
//   control:   the unreviewed 656a81b shader, which must differ somewhere, or the test is blind;
// and requires every RGBA8 byte of fused to equal the reference. A CPU model of the final Haar
// level also checks that the reference really ran (every pixel written, wiring not transposed).
// Validation-layer errors fail the run when the layer is installed.
//
//   g++ -std=c++17 -O2 fuse_color_vk_test.cpp -lvulkan -o fuse_color_vk_test
//   ./fuse_color_vk_test idwt-fp16_0.spv 0 && ./fuse_color_vk_test idwt-fp16_1.spv 1
#include <vulkan/vulkan.h>
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iterator>
#include <vector>
#include "convert_vert_spv.h"
#include "convert_frag_spv.h"
#include "fuse_color_frag_spv.h"
#include "fuse_color_fp16_frag_spv.h"
#include "fuse_color_656a81b_frag_spv.h"

#define CHECK(x) do { VkResult r_ = (x); if (r_ != VK_SUCCESS) { fprintf(stderr, "%s:%d %s = %d\n", __FILE__, __LINE__, #x, (int)r_); exit(2); } } while (0)
#define REQUIRE(c, ...) do { if (!(c)) { fprintf(stderr, __VA_ARGS__); fputc('\n', stderr); exit(2); } } while (0)

static int validation_errors = 0;
static VKAPI_ATTR VkBool32 VKAPI_CALL on_message(VkDebugUtilsMessageSeverityFlagBitsEXT severity, VkDebugUtilsMessageTypeFlagsEXT,
                                                 const VkDebugUtilsMessengerCallbackDataEXT *data, void *) {
    if (severity & VK_DEBUG_UTILS_MESSAGE_SEVERITY_ERROR_BIT_EXT) {
        validation_errors++;
        fprintf(stderr, "VALIDATION: %s\n", data->pMessage);
    }
    return VK_FALSE;
}

// IEEE binary16, round to nearest even: the R16F wavelet texels the decoder would hold.
static uint16_t to_half(float f) {
    uint32_t x; memcpy(&x, &f, 4);
    const uint32_t sign = (x >> 16) & 0x8000u;
    x &= 0x7fffffffu;
    if (x >= 0x47800000u) return (uint16_t)(sign | 0x7c00u);            // overflow: inputs stay far below
    if (x < 0x38800000u) {                                                // subnormal half
        const uint32_t m = (x & 0x7fffffu) | 0x800000u;
        const int shift = 126 - (int)(x >> 23);
        if (shift > 24) return (uint16_t)sign;
        const uint32_t half = m >> shift, rest = m & ((1u << shift) - 1), mid = 1u << (shift - 1);
        return (uint16_t)(sign | (half + (rest > mid || (rest == mid && (half & 1)))));
    }
    const uint32_t rest = x & 0x1fffu, base = (x - 0x38000000u) >> 13;
    return (uint16_t)(sign | (base + (rest > 0x1000u || (rest == 0x1000u && (base & 1)))));
}
static float from_half(uint16_t h) {
    const uint32_t sign = (uint32_t)(h & 0x8000u) << 16, e = (h >> 10) & 0x1f, m = h & 0x3ffu;
    float v = e ? std::ldexp((float)(m | 0x400u), (int)e - 25) : std::ldexp((float)m, -24);
    uint32_t x; memcpy(&x, &v, 4); x |= sign; memcpy(&v, &x, 4);
    return v;
}
static float round_half(float v) { return from_half(to_half(v)); }

struct Rng {
    uint64_t s;
    uint32_t next() { s ^= s << 13; s ^= s >> 7; s ^= s << 17; return (uint32_t)(s >> 11); }
    float uniform(float lo, float hi) { return lo + (hi - lo) * (next() & 0xffffff) / 16777216.0f; }
};

struct Vk {
    VkInstance instance = VK_NULL_HANDLE;
    VkPhysicalDevice gpu = VK_NULL_HANDLE;
    VkDevice device = VK_NULL_HANDLE;
    VkQueue queue = VK_NULL_HANDLE;
    uint32_t family = 0;
    VkPhysicalDeviceMemoryProperties memory = {};
    bool fp16 = false;
};

static uint32_t memory_type(const Vk &vk, uint32_t bits, VkMemoryPropertyFlags flags) {
    for (uint32_t i = 0; i < vk.memory.memoryTypeCount; i++)
        if ((bits & (1u << i)) && (vk.memory.memoryTypes[i].propertyFlags & flags) == flags) return i;
    REQUIRE(false, "no memory type 0x%x", flags);
    return 0;
}

struct Image { VkImage image; VkDeviceMemory memory; VkImageView view; uint32_t w, h, layers; VkFormat format; };
struct Buffer { VkBuffer buffer; VkDeviceMemory memory; void *mapped; VkDeviceSize size; };

static Image make_image(const Vk &vk, VkFormat format, uint32_t w, uint32_t h, uint32_t layers, VkImageUsageFlags usage) {
    Image img = {}; img.w = w; img.h = h; img.layers = layers; img.format = format;
    VkImageCreateInfo ii = { VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO };
    ii.imageType = VK_IMAGE_TYPE_2D; ii.format = format; ii.extent = { w, h, 1 }; ii.mipLevels = 1;
    ii.arrayLayers = layers; ii.samples = VK_SAMPLE_COUNT_1_BIT; ii.tiling = VK_IMAGE_TILING_OPTIMAL;
    ii.usage = usage; ii.initialLayout = VK_IMAGE_LAYOUT_UNDEFINED;
    CHECK(vkCreateImage(vk.device, &ii, nullptr, &img.image));
    VkMemoryRequirements req; vkGetImageMemoryRequirements(vk.device, img.image, &req);
    VkMemoryAllocateInfo ai = { VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO };
    ai.allocationSize = req.size; ai.memoryTypeIndex = memory_type(vk, req.memoryTypeBits, VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT);
    CHECK(vkAllocateMemory(vk.device, &ai, nullptr, &img.memory));
    CHECK(vkBindImageMemory(vk.device, img.image, img.memory, 0));
    VkImageViewCreateInfo vi = { VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO };
    vi.image = img.image; vi.viewType = layers > 1 ? VK_IMAGE_VIEW_TYPE_2D_ARRAY : VK_IMAGE_VIEW_TYPE_2D;
    vi.format = format; vi.subresourceRange = { VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, layers };
    CHECK(vkCreateImageView(vk.device, &vi, nullptr, &img.view));
    return img;
}
static void free_image(const Vk &vk, Image &img) {
    vkDestroyImageView(vk.device, img.view, nullptr); vkDestroyImage(vk.device, img.image, nullptr);
    vkFreeMemory(vk.device, img.memory, nullptr);
}
static Buffer make_buffer(const Vk &vk, VkDeviceSize size, VkBufferUsageFlags usage) {
    Buffer b = {}; b.size = size;
    VkBufferCreateInfo bi = { VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO };
    bi.size = size; bi.usage = usage;
    CHECK(vkCreateBuffer(vk.device, &bi, nullptr, &b.buffer));
    VkMemoryRequirements req; vkGetBufferMemoryRequirements(vk.device, b.buffer, &req);
    VkMemoryAllocateInfo ai = { VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO };
    ai.allocationSize = req.size;
    ai.memoryTypeIndex = memory_type(vk, req.memoryTypeBits, VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT);
    CHECK(vkAllocateMemory(vk.device, &ai, nullptr, &b.memory));
    CHECK(vkBindBufferMemory(vk.device, b.buffer, b.memory, 0));
    CHECK(vkMapMemory(vk.device, b.memory, 0, size, 0, &b.mapped));
    return b;
}
static void free_buffer(const Vk &vk, Buffer &b) {
    vkUnmapMemory(vk.device, b.memory); vkDestroyBuffer(vk.device, b.buffer, nullptr); vkFreeMemory(vk.device, b.memory, nullptr);
}

static void barrier(VkCommandBuffer cmd, const Image &img, VkImageLayout from, VkImageLayout to, VkAccessFlags src_access,
                    VkAccessFlags dst_access, VkPipelineStageFlags src, VkPipelineStageFlags dst) {
    VkImageMemoryBarrier b = { VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER };
    b.oldLayout = from; b.newLayout = to; b.srcAccessMask = src_access; b.dstAccessMask = dst_access;
    b.srcQueueFamilyIndex = b.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED; b.image = img.image;
    b.subresourceRange = { VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, img.layers };
    vkCmdPipelineBarrier(cmd, src, dst, 0, 0, nullptr, 0, nullptr, 1, &b);
}

static VkShaderModule module(const Vk &vk, const uint32_t *code, size_t bytes) {
    VkShaderModuleCreateInfo si = { VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO };
    si.codeSize = bytes; si.pCode = code;
    VkShaderModule m; CHECK(vkCreateShaderModule(vk.device, &si, nullptr, &m));
    return m;
}

static Vk create_vulkan(bool want_fp16) {
    Vk vk;
    uint32_t count = 0;
    vkEnumerateInstanceLayerProperties(&count, nullptr);
    std::vector<VkLayerProperties> layers(count);
    vkEnumerateInstanceLayerProperties(&count, layers.data());
    bool validation = false;
    for (auto &l : layers) validation |= !strcmp(l.layerName, "VK_LAYER_KHRONOS_validation");
    const char *layer = "VK_LAYER_KHRONOS_validation";
    const char *ext = VK_EXT_DEBUG_UTILS_EXTENSION_NAME;
    VkApplicationInfo app = { VK_STRUCTURE_TYPE_APPLICATION_INFO };
    app.pApplicationName = "fuse_color_vk_test"; app.apiVersion = VK_API_VERSION_1_2;
    VkInstanceCreateInfo ci = { VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO };
    ci.pApplicationInfo = &app;
    if (validation) { ci.enabledLayerCount = 1; ci.ppEnabledLayerNames = &layer; ci.enabledExtensionCount = 1; ci.ppEnabledExtensionNames = &ext; }
    CHECK(vkCreateInstance(&ci, nullptr, &vk.instance));
    printf("validation layer: %s\n", validation ? "enabled" : "not installed");
    if (validation) {
        auto create = (PFN_vkCreateDebugUtilsMessengerEXT)vkGetInstanceProcAddr(vk.instance, "vkCreateDebugUtilsMessengerEXT");
        VkDebugUtilsMessengerCreateInfoEXT mi = { VK_STRUCTURE_TYPE_DEBUG_UTILS_MESSENGER_CREATE_INFO_EXT };
        mi.messageSeverity = VK_DEBUG_UTILS_MESSAGE_SEVERITY_ERROR_BIT_EXT | VK_DEBUG_UTILS_MESSAGE_SEVERITY_WARNING_BIT_EXT;
        mi.messageType = VK_DEBUG_UTILS_MESSAGE_TYPE_GENERAL_BIT_EXT | VK_DEBUG_UTILS_MESSAGE_TYPE_VALIDATION_BIT_EXT;
        mi.pfnUserCallback = on_message;
        VkDebugUtilsMessengerEXT messenger;
        CHECK(create(vk.instance, &mi, nullptr, &messenger));
    }
    vkEnumeratePhysicalDevices(vk.instance, &count, nullptr);
    REQUIRE(count, "no Vulkan device");
    std::vector<VkPhysicalDevice> gpus(count);
    vkEnumeratePhysicalDevices(vk.instance, &count, gpus.data());
    vk.gpu = gpus[0];
    VkPhysicalDeviceProperties props; vkGetPhysicalDeviceProperties(vk.gpu, &props);
    printf("device: %s (driver 0x%x)\n", props.deviceName, props.driverVersion);
    vkGetPhysicalDeviceMemoryProperties(vk.gpu, &vk.memory);

    VkPhysicalDeviceVulkan12Features f12 = { VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES };
    VkPhysicalDeviceFeatures2 f2 = { VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2 };
    f2.pNext = &f12;
    vkGetPhysicalDeviceFeatures2(vk.gpu, &f2);
    REQUIRE(f2.features.shaderStorageImageWriteWithoutFormat, "idwt.comp needs shaderStorageImageWriteWithoutFormat");
    REQUIRE(!want_fp16 || f12.shaderFloat16, "FP16 variant requested but shaderFloat16 is unsupported");
    VkPhysicalDeviceVulkan12Features e12 = { VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES };
    e12.shaderFloat16 = want_fp16;
    VkPhysicalDeviceFeatures2 e2 = { VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2 };
    e2.pNext = &e12; e2.features.shaderStorageImageWriteWithoutFormat = VK_TRUE;
    vk.fp16 = want_fp16;

    VkFormatProperties fp; vkGetPhysicalDeviceFormatProperties(vk.gpu, VK_FORMAT_R8_UNORM, &fp);
    REQUIRE(fp.optimalTilingFeatures & VK_FORMAT_FEATURE_STORAGE_IMAGE_BIT, "R8_UNORM storage images unsupported");

    vkGetPhysicalDeviceQueueFamilyProperties(vk.gpu, &count, nullptr);
    std::vector<VkQueueFamilyProperties> families(count);
    vkGetPhysicalDeviceQueueFamilyProperties(vk.gpu, &count, families.data());
    vk.family = UINT32_MAX;
    for (uint32_t i = 0; i < count; i++)
        if ((families[i].queueFlags & (VK_QUEUE_GRAPHICS_BIT | VK_QUEUE_COMPUTE_BIT)) == (VK_QUEUE_GRAPHICS_BIT | VK_QUEUE_COMPUTE_BIT)) { vk.family = i; break; }
    REQUIRE(vk.family != UINT32_MAX, "no graphics+compute queue");
    const float priority = 1.0f;
    VkDeviceQueueCreateInfo qi = { VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO };
    qi.queueFamilyIndex = vk.family; qi.queueCount = 1; qi.pQueuePriorities = &priority;
    VkDeviceCreateInfo di = { VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO };
    di.pNext = &e2; di.queueCreateInfoCount = 1; di.pQueueCreateInfos = &qi;
    CHECK(vkCreateDevice(vk.gpu, &di, nullptr, &vk.device));
    vkGetDeviceQueue(vk.device, vk.family, 0, &vk.queue);
    return vk;
}

struct Case { uint32_t w, h; int limited, filter; uint64_t seed; };

struct Outputs { std::vector<uint8_t> y, ref, fused, control; };

// Wavelet values shaped like dequantized coefficients: LL around the DC-shifted mean, sparse
// details, some quantizer-grid values and a few large ones so the store clamps both ways.
static float coefficient(Rng &rng, int band) {
    const uint32_t kind = rng.next() % 100;
    if (band == 0) {
        if (kind < 3) return rng.uniform(-0.9f, 0.9f);
        if (kind < 40) return (float)((int)(rng.next() % 257) - 128) / 256.0f;
        return rng.uniform(-0.5f, 0.5f);
    }
    if (kind < 35) return 0.0f;
    if (kind < 65) return (float)((int)(rng.next() % 65) - 32) * (1.0f / 512.0f) * (float)(1 + rng.next() % 3);
    if (kind < 68) return rng.uniform(-0.8f, 0.8f);
    return rng.uniform(-0.15f, 0.15f);
}

static Outputs run_case(const Vk &vk, const std::vector<uint32_t> &idwt_spirv, const Case &c) {
    const uint32_t aligned_w = (c.w + 31) & ~31u, aligned_h = (c.h + 31) & ~31u;  // PyroWave Alignment = 32
    const uint32_t cw = aligned_w / 2, ch = aligned_h / 2;                           // level-0 wavelet view
    const uint32_t chroma_w = c.w / 2, chroma_h = c.h / 2;
    Rng rng = { c.seed * 0x9E3779B97F4A7C15ull + 1 };
    std::vector<uint16_t> wavelet((size_t)cw * ch * 4);
    for (uint32_t layer = 0; layer < 4; layer++)
        for (size_t i = 0; i < (size_t)cw * ch; i++)
            wavelet[layer * (size_t)cw * ch + i] = to_half(coefficient(rng, (int)layer));
    std::vector<uint8_t> cb((size_t)chroma_w * chroma_h), cr(cb.size());
    for (size_t i = 0; i < cb.size(); i++) {
        // Half noise, half smooth ramps, so both chroma filters see edges and gradients.
        const uint32_t x = (uint32_t)(i % chroma_w), y = (uint32_t)(i / chroma_w);
        const bool noise = ((x / 8) + (y / 8)) & 1;
        cb[i] = noise ? (uint8_t)rng.next() : (uint8_t)(16 + (x * 7 + y * 3) % 225);
        cr[i] = noise ? (uint8_t)rng.next() : (uint8_t)(16 + (x * 5 + y * 11) % 225);
    }

    const VkImageUsageFlags out_usage = VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT;
    Image wav = make_image(vk, VK_FORMAT_R16_SFLOAT, cw, ch, 4, VK_IMAGE_USAGE_SAMPLED_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT);
    Image yplane = make_image(vk, VK_FORMAT_R8_UNORM, c.w, c.h, 1, VK_IMAGE_USAGE_STORAGE_BIT | VK_IMAGE_USAGE_SAMPLED_BIT |
                              VK_IMAGE_USAGE_TRANSFER_SRC_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT);
    Image cbi = make_image(vk, VK_FORMAT_R8_UNORM, chroma_w, chroma_h, 1, VK_IMAGE_USAGE_SAMPLED_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT);
    Image cri = make_image(vk, VK_FORMAT_R8_UNORM, chroma_w, chroma_h, 1, VK_IMAGE_USAGE_SAMPLED_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT);
    Image outs[3] = { make_image(vk, VK_FORMAT_R8G8B8A8_UNORM, c.w, c.h, 1, out_usage),
                      make_image(vk, VK_FORMAT_R8G8B8A8_UNORM, c.w, c.h, 1, out_usage),
                      make_image(vk, VK_FORMAT_R8G8B8A8_UNORM, c.w, c.h, 1, out_usage) };
    const VkDeviceSize wav_bytes = wavelet.size() * 2, chroma_bytes = cb.size(), rgba_bytes = (VkDeviceSize)c.w * c.h * 4;
    Buffer upload = make_buffer(vk, wav_bytes + 2 * chroma_bytes, VK_BUFFER_USAGE_TRANSFER_SRC_BIT);
    memcpy(upload.mapped, wavelet.data(), wav_bytes);
    memcpy((uint8_t *)upload.mapped + wav_bytes, cb.data(), chroma_bytes);
    memcpy((uint8_t *)upload.mapped + wav_bytes + chroma_bytes, cr.data(), chroma_bytes);
    Buffer readback = make_buffer(vk, (VkDeviceSize)c.w * c.h + 3 * rgba_bytes, VK_BUFFER_USAGE_TRANSFER_DST_BIT);

    // pyroclient's sampler; texelFetch ignores it, the chroma path does not.
    VkSamplerCreateInfo si = { VK_STRUCTURE_TYPE_SAMPLER_CREATE_INFO };
    si.magFilter = si.minFilter = VK_FILTER_LINEAR;
    si.addressModeU = si.addressModeV = si.addressModeW = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
    VkSampler sampler; CHECK(vkCreateSampler(vk.device, &si, nullptr, &sampler));

    // Reference compute pipeline: binding 0 wavelet sampler2DArray, binding 1 R8 storage output.
    VkDescriptorSetLayoutBinding cb_bindings[2] = {};
    cb_bindings[0] = { 0, VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER, 1, VK_SHADER_STAGE_COMPUTE_BIT, nullptr };
    cb_bindings[1] = { 1, VK_DESCRIPTOR_TYPE_STORAGE_IMAGE, 1, VK_SHADER_STAGE_COMPUTE_BIT, nullptr };
    VkDescriptorSetLayoutCreateInfo li = { VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO };
    li.bindingCount = 2; li.pBindings = cb_bindings;
    VkDescriptorSetLayout compute_set_layout; CHECK(vkCreateDescriptorSetLayout(vk.device, &li, nullptr, &compute_set_layout));
    VkPushConstantRange compute_push = { VK_SHADER_STAGE_COMPUTE_BIT, 0, 16 };
    VkPipelineLayoutCreateInfo pli = { VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO };
    pli.setLayoutCount = 1; pli.pSetLayouts = &compute_set_layout; pli.pushConstantRangeCount = 1; pli.pPushConstantRanges = &compute_push;
    VkPipelineLayout compute_layout; CHECK(vkCreatePipelineLayout(vk.device, &pli, nullptr, &compute_layout));
    const VkBool32 spec_values[3] = { VK_TRUE, VK_FALSE, VK_TRUE };  // DCShift, LEGALL53, HAAR
    VkSpecializationMapEntry spec_entries[3] = { { 0, 0, 4 }, { 1, 4, 4 }, { 2, 8, 4 } };
    VkSpecializationInfo spec = { 3, spec_entries, sizeof spec_values, spec_values };
    VkComputePipelineCreateInfo cpi = { VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO };
    cpi.stage.sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO; cpi.stage.stage = VK_SHADER_STAGE_COMPUTE_BIT;
    cpi.stage.module = module(vk, idwt_spirv.data(), idwt_spirv.size() * 4); cpi.stage.pName = "main";
    cpi.stage.pSpecializationInfo = &spec; cpi.layout = compute_layout;
    VkPipeline compute_pipeline; CHECK(vkCreateComputePipelines(vk.device, VK_NULL_HANDLE, 1, &cpi, nullptr, &compute_pipeline));
    vkDestroyShaderModule(vk.device, cpi.stage.module, nullptr);

    // Fragment pipelines share one layout: three combined samplers, {limitedRange, chromaFilter}.
    VkDescriptorSetLayoutBinding fb[3] = {};
    for (uint32_t i = 0; i < 3; i++) fb[i] = { i, VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER, 1, VK_SHADER_STAGE_FRAGMENT_BIT, nullptr };
    li.bindingCount = 3; li.pBindings = fb;
    VkDescriptorSetLayout frag_set_layout; CHECK(vkCreateDescriptorSetLayout(vk.device, &li, nullptr, &frag_set_layout));
    VkPushConstantRange frag_push = { VK_SHADER_STAGE_FRAGMENT_BIT, 0, 8 };
    pli.pSetLayouts = &frag_set_layout; pli.pPushConstantRanges = &frag_push;
    VkPipelineLayout frag_layout; CHECK(vkCreatePipelineLayout(vk.device, &pli, nullptr, &frag_layout));

    VkAttachmentDescription attachment = {};
    attachment.format = VK_FORMAT_R8G8B8A8_UNORM; attachment.samples = VK_SAMPLE_COUNT_1_BIT;
    attachment.loadOp = VK_ATTACHMENT_LOAD_OP_DONT_CARE; attachment.storeOp = VK_ATTACHMENT_STORE_OP_STORE;
    attachment.stencilLoadOp = VK_ATTACHMENT_LOAD_OP_DONT_CARE; attachment.stencilStoreOp = VK_ATTACHMENT_STORE_OP_DONT_CARE;
    attachment.initialLayout = VK_IMAGE_LAYOUT_UNDEFINED; attachment.finalLayout = VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL;
    VkAttachmentReference color_ref = { 0, VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL };
    VkSubpassDescription subpass = {}; subpass.pipelineBindPoint = VK_PIPELINE_BIND_POINT_GRAPHICS;
    subpass.colorAttachmentCount = 1; subpass.pColorAttachments = &color_ref;
    VkSubpassDependency deps[2] = {};
    deps[0] = { VK_SUBPASS_EXTERNAL, 0, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT | VK_PIPELINE_STAGE_TRANSFER_BIT,
                VK_PIPELINE_STAGE_FRAGMENT_SHADER_BIT | VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT,
                VK_ACCESS_SHADER_WRITE_BIT | VK_ACCESS_TRANSFER_WRITE_BIT, VK_ACCESS_SHADER_READ_BIT | VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT, 0 };
    deps[1] = { 0, VK_SUBPASS_EXTERNAL, VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT,
                VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT, VK_ACCESS_TRANSFER_READ_BIT, 0 };
    VkRenderPassCreateInfo rpi = { VK_STRUCTURE_TYPE_RENDER_PASS_CREATE_INFO };
    rpi.attachmentCount = 1; rpi.pAttachments = &attachment; rpi.subpassCount = 1; rpi.pSubpasses = &subpass;
    rpi.dependencyCount = 2; rpi.pDependencies = deps;
    VkRenderPass render_pass; CHECK(vkCreateRenderPass(vk.device, &rpi, nullptr, &render_pass));

    const uint32_t *fused_code = vk.fp16 ? FUSE_COLOR_FP16_FRAG_SPV : FUSE_COLOR_FRAG_SPV;
    const size_t fused_bytes = vk.fp16 ? sizeof FUSE_COLOR_FP16_FRAG_SPV : sizeof FUSE_COLOR_FRAG_SPV;
    const uint32_t *frag_code[3] = { CONVERT_FRAG_SPV, fused_code, FUSE_COLOR_656A81B_FRAG_SPV };
    const size_t frag_bytes[3] = { sizeof CONVERT_FRAG_SPV, fused_bytes, sizeof FUSE_COLOR_656A81B_FRAG_SPV };
    VkShaderModule vert = module(vk, CONVERT_VERT_SPV, sizeof CONVERT_VERT_SPV);
    VkPipeline frag_pipelines[3];
    for (int p = 0; p < 3; p++) {
        VkPipelineShaderStageCreateInfo stages[2] = {};
        stages[0] = { VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO, nullptr, 0, VK_SHADER_STAGE_VERTEX_BIT, vert, "main", nullptr };
        stages[1] = { VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO, nullptr, 0, VK_SHADER_STAGE_FRAGMENT_BIT,
                      module(vk, frag_code[p], frag_bytes[p]), "main", nullptr };
        VkPipelineVertexInputStateCreateInfo vertex = { VK_STRUCTURE_TYPE_PIPELINE_VERTEX_INPUT_STATE_CREATE_INFO };
        VkPipelineInputAssemblyStateCreateInfo assembly = { VK_STRUCTURE_TYPE_PIPELINE_INPUT_ASSEMBLY_STATE_CREATE_INFO };
        assembly.topology = VK_PRIMITIVE_TOPOLOGY_TRIANGLE_LIST;
        VkViewport viewport = { 0, 0, (float)c.w, (float)c.h, 0, 1 };
        VkRect2D scissor = { { 0, 0 }, { c.w, c.h } };
        VkPipelineViewportStateCreateInfo vp = { VK_STRUCTURE_TYPE_PIPELINE_VIEWPORT_STATE_CREATE_INFO };
        vp.viewportCount = 1; vp.pViewports = &viewport; vp.scissorCount = 1; vp.pScissors = &scissor;
        VkPipelineRasterizationStateCreateInfo raster = { VK_STRUCTURE_TYPE_PIPELINE_RASTERIZATION_STATE_CREATE_INFO };
        raster.polygonMode = VK_POLYGON_MODE_FILL; raster.cullMode = VK_CULL_MODE_NONE; raster.lineWidth = 1;
        VkPipelineMultisampleStateCreateInfo samples = { VK_STRUCTURE_TYPE_PIPELINE_MULTISAMPLE_STATE_CREATE_INFO };
        samples.rasterizationSamples = VK_SAMPLE_COUNT_1_BIT;
        VkPipelineColorBlendAttachmentState blend = {}; blend.colorWriteMask = 0xf;
        VkPipelineColorBlendStateCreateInfo blends = { VK_STRUCTURE_TYPE_PIPELINE_COLOR_BLEND_STATE_CREATE_INFO };
        blends.attachmentCount = 1; blends.pAttachments = &blend;
        VkGraphicsPipelineCreateInfo gi = { VK_STRUCTURE_TYPE_GRAPHICS_PIPELINE_CREATE_INFO };
        gi.stageCount = 2; gi.pStages = stages; gi.pVertexInputState = &vertex; gi.pInputAssemblyState = &assembly;
        gi.pViewportState = &vp; gi.pRasterizationState = &raster; gi.pMultisampleState = &samples;
        gi.pColorBlendState = &blends; gi.layout = frag_layout; gi.renderPass = render_pass;
        CHECK(vkCreateGraphicsPipelines(vk.device, VK_NULL_HANDLE, 1, &gi, nullptr, &frag_pipelines[p]));
        vkDestroyShaderModule(vk.device, stages[1].module, nullptr);
    }
    vkDestroyShaderModule(vk.device, vert, nullptr);

    VkDescriptorPoolSize ps[2] = { { VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER, 8 }, { VK_DESCRIPTOR_TYPE_STORAGE_IMAGE, 1 } };
    VkDescriptorPoolCreateInfo dpi = { VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO };
    dpi.maxSets = 3; dpi.poolSizeCount = 2; dpi.pPoolSizes = ps;
    VkDescriptorPool pool; CHECK(vkCreateDescriptorPool(vk.device, &dpi, nullptr, &pool));
    VkDescriptorSetLayout set_layouts[3] = { compute_set_layout, frag_set_layout, frag_set_layout };
    VkDescriptorSetAllocateInfo sa = { VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO };
    sa.descriptorPool = pool; sa.descriptorSetCount = 3; sa.pSetLayouts = set_layouts;
    VkDescriptorSet sets[3]; CHECK(vkAllocateDescriptorSets(vk.device, &sa, sets));
    // Same layouts as production: everything sampled or stored in GENERAL.
    const VkImageLayout G = VK_IMAGE_LAYOUT_GENERAL;
    VkDescriptorImageInfo infos[8] = {
        { sampler, wav.view, G }, { VK_NULL_HANDLE, yplane.view, G },          // reference iDWT
        { sampler, yplane.view, G }, { sampler, cbi.view, G }, { sampler, cri.view, G },  // convert.frag
        { sampler, wav.view, G }, { sampler, cbi.view, G }, { sampler, cri.view, G },     // fused shaders
    };
    VkWriteDescriptorSet writes[8] = {};
    const VkDescriptorSet write_set[8] = { sets[0], sets[0], sets[1], sets[1], sets[1], sets[2], sets[2], sets[2] };
    const uint32_t write_binding[8] = { 0, 1, 0, 1, 2, 0, 1, 2 };
    for (int i = 0; i < 8; i++) {
        writes[i] = { VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET };
        writes[i].dstSet = write_set[i]; writes[i].dstBinding = write_binding[i]; writes[i].descriptorCount = 1;
        writes[i].descriptorType = i == 1 ? VK_DESCRIPTOR_TYPE_STORAGE_IMAGE : VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER;
        writes[i].pImageInfo = &infos[i];
    }
    vkUpdateDescriptorSets(vk.device, 8, writes, 0, nullptr);

    VkFramebuffer framebuffers[3];
    for (int i = 0; i < 3; i++) {
        VkFramebufferCreateInfo fi = { VK_STRUCTURE_TYPE_FRAMEBUFFER_CREATE_INFO };
        fi.renderPass = render_pass; fi.attachmentCount = 1; fi.pAttachments = &outs[i].view;
        fi.width = c.w; fi.height = c.h; fi.layers = 1;
        CHECK(vkCreateFramebuffer(vk.device, &fi, nullptr, &framebuffers[i]));
    }

    VkCommandPoolCreateInfo cpci = { VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO };
    cpci.queueFamilyIndex = vk.family;
    VkCommandPool cmd_pool; CHECK(vkCreateCommandPool(vk.device, &cpci, nullptr, &cmd_pool));
    VkCommandBufferAllocateInfo cai = { VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO };
    cai.commandPool = cmd_pool; cai.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY; cai.commandBufferCount = 1;
    VkCommandBuffer cmd; CHECK(vkAllocateCommandBuffers(vk.device, &cai, &cmd));
    VkCommandBufferBeginInfo bi = { VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO };
    bi.flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
    CHECK(vkBeginCommandBuffer(cmd, &bi));

    const VkPipelineStageFlags T = VK_PIPELINE_STAGE_TRANSFER_BIT, C = VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
                               F = VK_PIPELINE_STAGE_FRAGMENT_SHADER_BIT;
    for (Image *img : { &wav, &cbi, &cri, &yplane })
        barrier(cmd, *img, VK_IMAGE_LAYOUT_UNDEFINED, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 0, VK_ACCESS_TRANSFER_WRITE_BIT,
                VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT, T);
    VkBufferImageCopy copy = {};
    copy.imageSubresource = { VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 4 }; copy.imageExtent = { cw, ch, 1 };
    vkCmdCopyBufferToImage(cmd, upload.buffer, wav.image, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1, &copy);
    copy.imageSubresource.layerCount = 1; copy.imageExtent = { chroma_w, chroma_h, 1 };
    copy.bufferOffset = wav_bytes;
    vkCmdCopyBufferToImage(cmd, upload.buffer, cbi.image, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1, &copy);
    copy.bufferOffset = wav_bytes + chroma_bytes;
    vkCmdCopyBufferToImage(cmd, upload.buffer, cri.image, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1, &copy);
    // A sentinel in the luma plane: any pixel the reference iDWT fails to write is caught by the CPU model.
    VkClearColorValue sentinel = {}; sentinel.float32[0] = 77.0f / 255.0f;
    VkImageSubresourceRange whole = { VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1 };
    vkCmdClearColorImage(cmd, yplane.image, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, &sentinel, 1, &whole);
    for (Image *img : { &wav, &cbi, &cri })
        barrier(cmd, *img, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, G, VK_ACCESS_TRANSFER_WRITE_BIT, VK_ACCESS_SHADER_READ_BIT, T, C | F);
    barrier(cmd, yplane, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, G, VK_ACCESS_TRANSFER_WRITE_BIT, VK_ACCESS_SHADER_WRITE_BIT, T, C);

    // idwt.comp final level: resolution is transposed (texture height, width), 16x16 coefficient tiles.
    struct { int32_t res[2]; float inv[2]; } push = { { (int32_t)ch, (int32_t)cw }, { 1.0f / ch, 1.0f / cw } };
    vkCmdBindPipeline(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, compute_pipeline);
    vkCmdBindDescriptorSets(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, compute_layout, 0, 1, &sets[0], 0, nullptr);
    vkCmdPushConstants(cmd, compute_layout, VK_SHADER_STAGE_COMPUTE_BIT, 0, sizeof push, &push);
    vkCmdDispatch(cmd, (ch + 15) / 16, (cw + 15) / 16, 1);
    barrier(cmd, yplane, G, G, VK_ACCESS_SHADER_WRITE_BIT, VK_ACCESS_SHADER_READ_BIT | VK_ACCESS_TRANSFER_READ_BIT, C, F | T);

    const int32_t params[2] = { c.limited, c.filter };
    const VkDescriptorSet frag_sets[3] = { sets[1], sets[2], sets[2] };
    for (int p = 0; p < 3; p++) {
        VkRenderPassBeginInfo rb = { VK_STRUCTURE_TYPE_RENDER_PASS_BEGIN_INFO };
        rb.renderPass = render_pass; rb.framebuffer = framebuffers[p]; rb.renderArea.extent = { c.w, c.h };
        vkCmdBeginRenderPass(cmd, &rb, VK_SUBPASS_CONTENTS_INLINE);
        vkCmdBindPipeline(cmd, VK_PIPELINE_BIND_POINT_GRAPHICS, frag_pipelines[p]);
        vkCmdBindDescriptorSets(cmd, VK_PIPELINE_BIND_POINT_GRAPHICS, frag_layout, 0, 1, &frag_sets[p], 0, nullptr);
        vkCmdPushConstants(cmd, frag_layout, VK_SHADER_STAGE_FRAGMENT_BIT, 0, sizeof params, params);
        vkCmdDraw(cmd, 3, 1, 0, 0);
        vkCmdEndRenderPass(cmd);
    }
    VkBufferImageCopy back = {};
    back.imageSubresource = { VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1 }; back.imageExtent = { c.w, c.h, 1 };
    vkCmdCopyImageToBuffer(cmd, yplane.image, G, readback.buffer, 1, &back);
    for (int p = 0; p < 3; p++) {
        back.bufferOffset = (VkDeviceSize)c.w * c.h + p * rgba_bytes;
        vkCmdCopyImageToBuffer(cmd, outs[p].image, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, readback.buffer, 1, &back);
    }
    VkMemoryBarrier host = { VK_STRUCTURE_TYPE_MEMORY_BARRIER };
    host.srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT; host.dstAccessMask = VK_ACCESS_HOST_READ_BIT;
    vkCmdPipelineBarrier(cmd, T, VK_PIPELINE_STAGE_HOST_BIT, 0, 1, &host, 0, nullptr, 0, nullptr);
    CHECK(vkEndCommandBuffer(cmd));
    VkFenceCreateInfo fci = { VK_STRUCTURE_TYPE_FENCE_CREATE_INFO };
    VkFence fence; CHECK(vkCreateFence(vk.device, &fci, nullptr, &fence));
    VkSubmitInfo submit = { VK_STRUCTURE_TYPE_SUBMIT_INFO };
    submit.commandBufferCount = 1; submit.pCommandBuffers = &cmd;
    CHECK(vkQueueSubmit(vk.queue, 1, &submit, fence));
    CHECK(vkWaitForFences(vk.device, 1, &fence, VK_TRUE, UINT64_MAX));

    Outputs out;
    const uint8_t *bytes = (const uint8_t *)readback.mapped;
    out.y.assign(bytes, bytes + (size_t)c.w * c.h);
    bytes += (size_t)c.w * c.h;
    out.ref.assign(bytes, bytes + rgba_bytes);
    out.fused.assign(bytes + rgba_bytes, bytes + 2 * rgba_bytes);
    out.control.assign(bytes + 2 * rgba_bytes, bytes + 3 * rgba_bytes);

    // CPU models of inverse_haar_pairs() + R8 store (FP32 arithmetic, round-to-nearest store), with
    // and without the FP16 round trips. A driver may fold f32->f16->f32 away under Vulkan's relaxed
    // float rules, so report which model the reference followed; either must hold to 1 LSB.
    int mismatch[2] = {}, worst[2] = {};
    for (uint32_t y = 0; y < c.h; y++)
        for (uint32_t x = 0; x < c.w; x++) {
            const size_t k = (size_t)(y / 2) * cw + x / 2, layer = (size_t)cw * ch;
            const float a = from_half(wavelet[k]), horizontal = from_half(wavelet[layer + k]);
            const float vertical = from_half(wavelet[2 * layer + k]), diagonal = from_half(wavelet[3 * layer + k]);
            const float low_even = a - 0.5f * vertical, low_odd = low_even + vertical;
            const float high_even = horizontal - 0.5f * diagonal, high_odd = high_even + diagonal;
            const int got = out.y[(size_t)y * c.w + x];
            for (int rounded = 0; rounded < 2; rounded++) {
                auto r = [&](float v) { return rounded ? round_half(v) : v; };
                const float low = r((y & 1) ? low_odd : low_even), high = r((y & 1) ? high_odd : high_even);
                const float even = low - 0.5f * high, odd = even + high;
                float v = r((x & 1) ? odd : even) + 0.5f;
                v = v < 0.0f ? 0.0f : v > 1.0f ? 1.0f : v;
                const int d = std::abs((int)std::nearbyint(v * 255.0f) - got);
                if (d) { mismatch[rounded]++; worst[rounded] = std::max(worst[rounded], d); }
            }
        }
    printf("  reference luma vs CPU model: FP16 round trips kept %d differ (worst %d), folded %d differ (worst %d), of %u\n",
           mismatch[1], worst[1], mismatch[0], worst[0], c.w * c.h);
    REQUIRE(std::min(worst[0], worst[1]) <= 1, "reference iDWT output does not match the Haar model: harness or wiring fault");

    vkDestroyFence(vk.device, fence, nullptr);
    vkDestroyCommandPool(vk.device, cmd_pool, nullptr);
    for (VkFramebuffer f : framebuffers) vkDestroyFramebuffer(vk.device, f, nullptr);
    vkDestroyDescriptorPool(vk.device, pool, nullptr);
    for (VkPipeline p : frag_pipelines) vkDestroyPipeline(vk.device, p, nullptr);
    vkDestroyPipeline(vk.device, compute_pipeline, nullptr);
    vkDestroyRenderPass(vk.device, render_pass, nullptr);
    vkDestroyPipelineLayout(vk.device, frag_layout, nullptr);
    vkDestroyPipelineLayout(vk.device, compute_layout, nullptr);
    vkDestroyDescriptorSetLayout(vk.device, frag_set_layout, nullptr);
    vkDestroyDescriptorSetLayout(vk.device, compute_set_layout, nullptr);
    vkDestroySampler(vk.device, sampler, nullptr);
    free_buffer(vk, readback); free_buffer(vk, upload);
    for (Image &img : outs) free_image(vk, img);
    free_image(vk, cri); free_image(vk, cbi); free_image(vk, yplane); free_image(vk, wav);
    return out;
}

static int count_diff(const std::vector<uint8_t> &a, const std::vector<uint8_t> &b, uint32_t w, int *worst, int report) {
    int pixels = 0; *worst = 0;
    for (size_t i = 0; i < a.size(); i += 4) {
        int d = 0;
        for (int ch = 0; ch < 4; ch++) d = std::max(d, std::abs((int)a[i + ch] - (int)b[i + ch]));
        if (!d) continue;
        if (pixels < report)
            printf("    pixel (%zu,%zu) ref %u,%u,%u,%u got %u,%u,%u,%u\n", (i / 4) % w, (i / 4) / w, a[i], a[i + 1], a[i + 2],
                   a[i + 3], b[i], b[i + 1], b[i + 2], b[i + 3]);
        pixels++; *worst = std::max(*worst, d);
    }
    return pixels;
}

int main(int argc, char **argv) {
    if (argc != 3 || (strcmp(argv[2], "0") && strcmp(argv[2], "1"))) {
        fprintf(stderr, "usage: %s <shipped idwt.spv> <fp16 variant 0|1>\n", argv[0]);
        return 2;
    }
    std::ifstream in(argv[1], std::ios::binary);
    std::vector<char> raw((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
    REQUIRE(raw.size() >= 20 && raw.size() % 4 == 0, "%s is not SPIR-V", argv[1]);
    std::vector<uint32_t> idwt(raw.size() / 4);
    memcpy(idwt.data(), raw.data(), raw.size());
    REQUIRE(idwt[0] == 0x07230203u, "%s is not SPIR-V", argv[1]);
    const bool fp16 = argv[2][0] == '1';
    Vk vk = create_vulkan(fp16);

    // The production eye (2080x2208, aligned exactly), a small frame whose wavelet is padded
    // (66x34 -> 96x64), and one with odd chroma dimensions and partial 16x16 tiles.
    const Case cases[] = {
        { 2080, 2208, 1, 0, 1 }, { 2080, 2208, 0, 1, 2 },
        { 66, 34, 1, 0, 3 }, { 66, 34, 1, 1, 4 }, { 66, 34, 0, 0, 5 }, { 66, 34, 0, 1, 6 },
        { 1922, 1090, 1, 1, 7 }, { 1922, 1090, 0, 0, 8 },
    };
    int failures = 0; long control_pixels = 0;
    for (const Case &c : cases) {
        printf("case %ux%u %s range, chroma %s, FP16 variant %d\n", c.w, c.h, c.limited ? "limited" : "full",
               c.filter ? "catmull" : "bilinear", fp16);
        Outputs out = run_case(vk, idwt, c);
        int worst = 0;
        const int fused = count_diff(out.ref, out.fused, c.w, &worst, 8);
        printf("  fused vs reference: %d of %u pixels differ, worst %d\n", fused, c.w * c.h, worst);
        failures += fused != 0;
        const int control = count_diff(out.ref, out.control, c.w, &worst, 0);
        printf("  656a81b control vs reference: %d pixels differ, worst %d\n", control, worst);
        control_pixels += control;
    }
    printf("validation errors: %d\n", validation_errors);
    if (!control_pixels) { printf("FAIL: the 656a81b control matched everywhere, so this test cannot see the defect\n"); return 1; }
    if (failures || validation_errors) { printf("FAIL: %d case(s) differ\n", failures); return 1; }
    printf("fuse_color_vk_test: fused output is byte-identical to the shipped two-pass path\n");
    return 0;
}
