// Exact-pixel gate for the fused final-Haar + BT.709 color pass and the fused dequant + level-0
// Haar kernel, runnable without a headset.
//
// Encodes a deterministic asymmetric 4:2:0 frame with PyroWave Haar, decodes it three times on
// one Vulkan device and compares each candidate with the current path:
//   A: current path. PyroWave writes all three R8 planes; convert.frag makes RGBA.
//   B: fused color.  pyrowave_decoder_set_final_luma_store(0); fuse_color.frag reconstructs
//                    luma level 0 from the wavelet image while converting to RGBA.
//   C: fused dequant+Haar. pyrowave_decoder_set_fused_dequant_haar(1) decodes the level-0 luma
//                    bands inside the final Haar pass; convert.frag as in A. Luma and RGBA are
//                    compared.
// The color passes use the SPIR-V headers that ship in pyroclient. On CI this runs on Mesa lavapipe, so it
// proves shader/API equivalence for the same coefficients, not Adreno rounding or decode
// fidelity; repeat on Quest (default GATE_MIN_PSNR=30) before promotion.
//
// Usage: fuse_color_gate [width height max_bytes]...   (default: 512 320 131072 and 4160 2208 2083333)
// Exit 0 when every case has max RGBA difference <= 1 (and luma <= 1 for C), a non-trivial
// decoded luma plane and luma PSNR >= GATE_MIN_PSNR. GATE_ENCODE_ONLY / GATE_DECODE_ONLY (+ GATE_PACKET_DIR) split
// encode and decode across processes: lavapipe encodes at 512-bit (the encoder needs 16 lanes)
// and decodes at 256-bit. GATE_DUMP_DIR writes the source and decoded luma as PGM files.

#include <vulkan/vulkan.h>
#include "pyrowave.h"
#include "convert_vert_spv.h"
#include "convert_frag_spv.h"
#include "fuse_color_frag_spv.h"

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
    g.app.pApplicationName = "fuse_color_gate";
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

// Deterministic, asymmetric content: gradients, a hard diagonal edge, fine stripes, colored
// blocks and hashed noise, so orientation, eye-edge and rounding errors all show up.
void make_source(uint32_t w, uint32_t h, std::vector<uint8_t> &y, std::vector<uint8_t> &cb,
                 std::vector<uint8_t> &cr) {
    y.resize(size_t(w) * h); cb.resize(size_t(w / 2) * (h / 2)); cr.resize(cb.size());
    auto hash = [](uint32_t x) { x ^= x >> 16; x *= 0x7feb352du; x ^= x >> 15; x *= 0x846ca68bu; x ^= x >> 16; return x; };
    for (uint32_t j = 0; j < h; j++)
        for (uint32_t i = 0; i < w; i++) {
            int v = int(255.0 * i / (w - 1) * 0.6 + 255.0 * j / (h - 1) * 0.3);
            if (i * h > j * w * 2 / 3) v = 255 - v;
            if (i % 7 < 2 && j < h / 3) v = (j & 1) ? 16 : 235;
            v += int(hash(j * w + i) % 9) - 4;
            y[size_t(j) * w + i] = uint8_t(v < 0 ? 0 : v > 255 ? 255 : v);
        }
    for (uint32_t j = 0; j < h / 2; j++)
        for (uint32_t i = 0; i < w / 2; i++) {
            int u = 128 + int(100.0 * std::sin(i * 0.031) * ((j / 16) % 2 ? 1 : -1));
            int v = 128 + int(90.0 * std::cos(j * 0.023 + i * 0.002));
            if ((i / 24 + j / 24) % 5 == 0) { u = 40; v = 220; }
            cb[size_t(j) * (w / 2) + i] = uint8_t(u < 0 ? 0 : u > 255 ? 255 : u);
            cr[size_t(j) * (w / 2) + i] = uint8_t(v < 0 ? 0 : v > 255 ? 255 : v);
        }
}

// Encode on a separate default device: a borrowed device owns no queue for PyroWave's own submits.
std::vector<std::vector<uint8_t>> encode(uint32_t w, uint32_t h, size_t max_bytes, const std::vector<uint8_t> &y,
                                         const std::vector<uint8_t> &cb, const std::vector<uint8_t> &cr) {
    pyrowave_device dev = nullptr;
    PW_CHECK(pyrowave_create_default_device(&dev));
    pyrowave_encoder_create_info ei = {};
    ei.device = dev; ei.width = int(w); ei.height = int(h);
    ei.chroma = PYROWAVE_CHROMA_SUBSAMPLING_420; ei.wavelet = PYROWAVE_WAVELET_HAAR;
    pyrowave_encoder enc = nullptr;
    PW_CHECK(pyrowave_encoder_create(&ei, &enc));
    pyrowave_cpu_buffer buf = {};
    buf.data[0] = const_cast<uint8_t *>(y.data());
    buf.data[1] = const_cast<uint8_t *>(cb.data());
    buf.data[2] = const_cast<uint8_t *>(cr.data());
    buf.row_stride_in_bytes[0] = w; buf.row_stride_in_bytes[1] = buf.row_stride_in_bytes[2] = w / 2;
    buf.plane_size_in_bytes[0] = y.size(); buf.plane_size_in_bytes[1] = cb.size(); buf.plane_size_in_bytes[2] = cr.size();
    buf.width = int(w); buf.height = int(h); buf.format = PYROWAVE_CPU_BUFFER_FORMAT_YUV420P;
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

struct Pass {
    VkRenderPass render_pass = VK_NULL_HANDLE;
    VkDescriptorSetLayout set_layout = VK_NULL_HANDLE;
    VkPipelineLayout layout = VK_NULL_HANDLE;
    VkPipeline pipeline = VK_NULL_HANDLE;
};

// Mirrors pyroclient::create_fragment_convert / create_fuse_color: three combined samplers,
// two push-constant ints, full-screen triangle into an RGBA8 attachment.
Pass create_pass(const Gpu &g, uint32_t w, uint32_t h, const uint32_t *frag, size_t frag_size) {
    Pass p;
    VkAttachmentDescription attachment = {};
    attachment.format = VK_FORMAT_R8G8B8A8_UNORM; attachment.samples = VK_SAMPLE_COUNT_1_BIT;
    attachment.loadOp = VK_ATTACHMENT_LOAD_OP_DONT_CARE; attachment.storeOp = VK_ATTACHMENT_STORE_OP_STORE;
    attachment.stencilLoadOp = VK_ATTACHMENT_LOAD_OP_DONT_CARE; attachment.stencilStoreOp = VK_ATTACHMENT_STORE_OP_DONT_CARE;
    attachment.initialLayout = attachment.finalLayout = VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL;
    VkAttachmentReference reference = { 0, VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL };
    VkSubpassDescription subpass = {};
    subpass.pipelineBindPoint = VK_PIPELINE_BIND_POINT_GRAPHICS;
    subpass.colorAttachmentCount = 1; subpass.pColorAttachments = &reference;
    VkRenderPassCreateInfo rp = { VK_STRUCTURE_TYPE_RENDER_PASS_CREATE_INFO };
    rp.attachmentCount = 1; rp.pAttachments = &attachment; rp.subpassCount = 1; rp.pSubpasses = &subpass;
    VK_CHECK(vkCreateRenderPass(g.device, &rp, nullptr, &p.render_pass));

    VkDescriptorSetLayoutBinding b[3] = {};
    for (int i = 0; i < 3; i++) {
        b[i].binding = uint32_t(i); b[i].descriptorType = VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER;
        b[i].descriptorCount = 1; b[i].stageFlags = VK_SHADER_STAGE_FRAGMENT_BIT;
    }
    VkDescriptorSetLayoutCreateInfo li = { VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO };
    li.bindingCount = 3; li.pBindings = b;
    VK_CHECK(vkCreateDescriptorSetLayout(g.device, &li, nullptr, &p.set_layout));
    VkPushConstantRange pc = { VK_SHADER_STAGE_FRAGMENT_BIT, 0, sizeof(int32_t) * 2 };
    VkPipelineLayoutCreateInfo pli = { VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO };
    pli.setLayoutCount = 1; pli.pSetLayouts = &p.set_layout;
    pli.pushConstantRangeCount = 1; pli.pPushConstantRanges = &pc;
    VK_CHECK(vkCreatePipelineLayout(g.device, &pli, nullptr, &p.layout));

    VkShaderModule modules[2] = {};
    VkShaderModuleCreateInfo sm = { VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO };
    sm.codeSize = sizeof(CONVERT_VERT_SPV); sm.pCode = CONVERT_VERT_SPV;
    VK_CHECK(vkCreateShaderModule(g.device, &sm, nullptr, &modules[0]));
    sm.codeSize = frag_size; sm.pCode = frag;
    VK_CHECK(vkCreateShaderModule(g.device, &sm, nullptr, &modules[1]));
    VkPipelineShaderStageCreateInfo stages[2] = {};
    for (int i = 0; i < 2; i++) {
        stages[i].sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO;
        stages[i].stage = i ? VK_SHADER_STAGE_FRAGMENT_BIT : VK_SHADER_STAGE_VERTEX_BIT;
        stages[i].module = modules[i]; stages[i].pName = "main";
    }
    VkPipelineVertexInputStateCreateInfo vertex = { VK_STRUCTURE_TYPE_PIPELINE_VERTEX_INPUT_STATE_CREATE_INFO };
    VkPipelineInputAssemblyStateCreateInfo assembly = { VK_STRUCTURE_TYPE_PIPELINE_INPUT_ASSEMBLY_STATE_CREATE_INFO };
    assembly.topology = VK_PRIMITIVE_TOPOLOGY_TRIANGLE_LIST;
    VkViewport viewport = { 0, 0, float(w), float(h), 0, 1 };
    VkRect2D scissor = { { 0, 0 }, { w, h } };
    VkPipelineViewportStateCreateInfo vp = { VK_STRUCTURE_TYPE_PIPELINE_VIEWPORT_STATE_CREATE_INFO };
    vp.viewportCount = 1; vp.pViewports = &viewport; vp.scissorCount = 1; vp.pScissors = &scissor;
    VkPipelineRasterizationStateCreateInfo raster = { VK_STRUCTURE_TYPE_PIPELINE_RASTERIZATION_STATE_CREATE_INFO };
    raster.polygonMode = VK_POLYGON_MODE_FILL; raster.cullMode = VK_CULL_MODE_NONE; raster.lineWidth = 1;
    VkPipelineMultisampleStateCreateInfo samples = { VK_STRUCTURE_TYPE_PIPELINE_MULTISAMPLE_STATE_CREATE_INFO };
    samples.rasterizationSamples = VK_SAMPLE_COUNT_1_BIT;
    VkPipelineColorBlendAttachmentState blend = {}; blend.colorWriteMask = 0xf;
    VkPipelineColorBlendStateCreateInfo blends = { VK_STRUCTURE_TYPE_PIPELINE_COLOR_BLEND_STATE_CREATE_INFO };
    blends.attachmentCount = 1; blends.pAttachments = &blend;
    VkGraphicsPipelineCreateInfo info = { VK_STRUCTURE_TYPE_GRAPHICS_PIPELINE_CREATE_INFO };
    info.stageCount = 2; info.pStages = stages; info.pVertexInputState = &vertex;
    info.pInputAssemblyState = &assembly; info.pViewportState = &vp; info.pRasterizationState = &raster;
    info.pMultisampleState = &samples; info.pColorBlendState = &blends; info.layout = p.layout;
    info.renderPass = p.render_pass;
    VK_CHECK(vkCreateGraphicsPipelines(g.device, VK_NULL_HANDLE, 1, &info, nullptr, &p.pipeline));
    for (auto m : modules) vkDestroyShaderModule(g.device, m, nullptr);
    return p;
}

void destroy_pass(const Gpu &g, Pass &p) {
    vkDestroyPipeline(g.device, p.pipeline, nullptr);
    vkDestroyPipelineLayout(g.device, p.layout, nullptr);
    vkDestroyDescriptorSetLayout(g.device, p.set_layout, nullptr);
    vkDestroyRenderPass(g.device, p.render_pass, nullptr);
    p = {};
}

struct Result {
    std::vector<uint8_t> rgba;
    std::vector<uint8_t> luma; // empty for fused color, which never writes the luma plane
};

enum class Path { Current, FusedColor, DequantHaar };

// Decode one frame with a fresh decoder, convert it with `pass`, read RGBA (and luma) back.
Result run(const Gpu &g, uint32_t w, uint32_t h, const std::vector<std::vector<uint8_t>> &packets,
           Path path, int limited, int chroma_filter) {
    const bool fused = path == Path::FusedColor;
    pyrowave_decoder_create_info di = {};
    di.device = g.pyro; di.width = int(w); di.height = int(h);
    di.chroma = PYROWAVE_CHROMA_SUBSAMPLING_420; di.fragment_path = false; di.wavelet = PYROWAVE_WAVELET_HAAR;
    pyrowave_decoder dec = nullptr;
    PW_CHECK(pyrowave_decoder_create(&di, &dec));
    if (fused) PW_CHECK(pyrowave_decoder_set_final_luma_store(dec, 0));
    if (path == Path::DequantHaar) PW_CHECK(pyrowave_decoder_set_fused_dequant_haar(dec, 1));
    for (const auto &p : packets) PW_CHECK(pyrowave_decoder_push_packet(dec, p.data(), p.size()));
    if (!pyrowave_decoder_decode_is_ready(dec, false)) { fprintf(stderr, "frame incomplete\n"); exit(2); }

    const VkImageUsageFlags plane_usage = VK_IMAGE_USAGE_SAMPLED_BIT | VK_IMAGE_USAGE_STORAGE_BIT |
        VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT;
    Image planes[3] = {
        create_image(g, VK_FORMAT_R8_UNORM, w, h, plane_usage),
        create_image(g, VK_FORMAT_R8_UNORM, w / 2, h / 2, plane_usage),
        create_image(g, VK_FORMAT_R8_UNORM, w / 2, h / 2, plane_usage),
    };
    Image out = create_image(g, VK_FORMAT_R8G8B8A8_UNORM, w, h,
                             VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT);
    pyrowave_gpu_buffers buffers = {};
    for (int i = 0; i < 3; i++) {
        pyrowave_image_view &v = buffers.planes[i];
        v.image = planes[i].image; v.width = int(planes[i].width); v.height = int(planes[i].height);
        v.image_format = VK_FORMAT_R8_UNORM; v.view_format = VK_FORMAT_R8_UNORM;
        v.mip_level = 0; v.layer = 0; v.aspect = VK_IMAGE_ASPECT_COLOR_BIT;
        v.swizzle = VK_COMPONENT_SWIZZLE_IDENTITY; v.layout = VK_IMAGE_LAYOUT_GENERAL;
    }

    Pass pass = fused ? create_pass(g, w, h, FUSE_COLOR_FRAG_SPV, sizeof(FUSE_COLOR_FRAG_SPV))
                      : create_pass(g, w, h, CONVERT_FRAG_SPV, sizeof(CONVERT_FRAG_SPV));
    VkSampler sampler;
    VkSamplerCreateInfo si = { VK_STRUCTURE_TYPE_SAMPLER_CREATE_INFO };
    si.magFilter = si.minFilter = VK_FILTER_LINEAR; // pyroclient::create_convert
    si.addressModeU = si.addressModeV = si.addressModeW = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
    VK_CHECK(vkCreateSampler(g.device, &si, nullptr, &sampler));
    VkDescriptorPoolSize ps = { VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER, 3 };
    VkDescriptorPoolCreateInfo dpi = { VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO };
    dpi.maxSets = 1; dpi.poolSizeCount = 1; dpi.pPoolSizes = &ps;
    VkDescriptorPool pool;
    VK_CHECK(vkCreateDescriptorPool(g.device, &dpi, nullptr, &pool));
    VkDescriptorSetAllocateInfo sa = { VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO };
    sa.descriptorPool = pool; sa.descriptorSetCount = 1; sa.pSetLayouts = &pass.set_layout;
    VkDescriptorSet set;
    VK_CHECK(vkAllocateDescriptorSets(g.device, &sa, &set));
    VkImageView first = planes[0].view;
    if (fused) PW_CHECK(pyrowave_decoder_get_wavelet_view(dec, 0, 0, &first, nullptr));
    VkDescriptorImageInfo info[3] = {};
    const VkImageView views[3] = { first, planes[1].view, planes[2].view };
    VkWriteDescriptorSet writes[3] = {};
    for (int i = 0; i < 3; i++) {
        info[i] = { sampler, views[i], VK_IMAGE_LAYOUT_GENERAL };
        writes[i] = { VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET };
        writes[i].dstSet = set; writes[i].dstBinding = uint32_t(i); writes[i].descriptorCount = 1;
        writes[i].descriptorType = VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER; writes[i].pImageInfo = &info[i];
    }
    vkUpdateDescriptorSets(g.device, 3, writes, 0, nullptr);
    VkFramebufferCreateInfo fbi = { VK_STRUCTURE_TYPE_FRAMEBUFFER_CREATE_INFO };
    fbi.renderPass = pass.render_pass; fbi.attachmentCount = 1; fbi.pAttachments = &out.view;
    fbi.width = w; fbi.height = h; fbi.layers = 1;
    VkFramebuffer fb;
    VK_CHECK(vkCreateFramebuffer(g.device, &fbi, nullptr, &fb));

    // Host-visible readback buffer: RGBA then luma.
    const VkDeviceSize rgba_bytes = VkDeviceSize(w) * h * 4, luma_bytes = VkDeviceSize(w) * h;
    VkBufferCreateInfo bi = { VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO };
    bi.size = rgba_bytes + luma_bytes; bi.usage = VK_BUFFER_USAGE_TRANSFER_DST_BIT;
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

    // Same plane and output transitions as pyroclient::record_commands (compute decode,
    // fragment conversion), plus the fused path's compute -> fragment memory barrier.
    const VkPipelineStageFlags write_stages = VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT | VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT;
    const VkAccessFlags write_access = VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT | VK_ACCESS_SHADER_WRITE_BIT;
    for (auto &p : planes)
        barrier(cmd, p.image, VK_IMAGE_LAYOUT_UNDEFINED, VK_IMAGE_LAYOUT_GENERAL, 0, write_access,
                VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT, write_stages);
    pyrowave_device_set_command_buffer(g.pyro, cmd);
    PW_CHECK(pyrowave_decoder_decode_gpu_buffer(dec, nullptr, nullptr, &buffers));
    pyrowave_device_set_command_buffer(g.pyro, VK_NULL_HANDLE);
    for (auto &p : planes)
        barrier(cmd, p.image, VK_IMAGE_LAYOUT_GENERAL, VK_IMAGE_LAYOUT_GENERAL, write_access,
                VK_ACCESS_SHADER_READ_BIT, write_stages, VK_PIPELINE_STAGE_FRAGMENT_SHADER_BIT);
    if (fused) {
        VkMemoryBarrier mb = { VK_STRUCTURE_TYPE_MEMORY_BARRIER };
        mb.srcAccessMask = VK_ACCESS_SHADER_WRITE_BIT; mb.dstAccessMask = VK_ACCESS_SHADER_READ_BIT;
        vkCmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, VK_PIPELINE_STAGE_FRAGMENT_SHADER_BIT,
                             0, 1, &mb, 0, nullptr, 0, nullptr);
    }
    barrier(cmd, out.image, VK_IMAGE_LAYOUT_UNDEFINED, VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL, 0,
            VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT, VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT,
            VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT);
    VkRenderPassBeginInfo rpb = { VK_STRUCTURE_TYPE_RENDER_PASS_BEGIN_INFO };
    rpb.renderPass = pass.render_pass; rpb.framebuffer = fb; rpb.renderArea.extent = { w, h };
    vkCmdBeginRenderPass(cmd, &rpb, VK_SUBPASS_CONTENTS_INLINE);
    vkCmdBindPipeline(cmd, VK_PIPELINE_BIND_POINT_GRAPHICS, pass.pipeline);
    const int32_t params[2] = { limited, chroma_filter };
    vkCmdPushConstants(cmd, pass.layout, VK_SHADER_STAGE_FRAGMENT_BIT, 0, sizeof params, params);
    vkCmdBindDescriptorSets(cmd, VK_PIPELINE_BIND_POINT_GRAPHICS, pass.layout, 0, 1, &set, 0, nullptr);
    vkCmdDraw(cmd, 3, 1, 0, 0);
    vkCmdEndRenderPass(cmd);

    barrier(cmd, out.image, VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL,
            VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT, VK_ACCESS_TRANSFER_READ_BIT,
            VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT);
    VkBufferImageCopy copy = {};
    copy.imageSubresource = { VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1 };
    copy.imageExtent = { w, h, 1 };
    vkCmdCopyImageToBuffer(cmd, out.image, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, readback, 1, &copy);
    if (!fused) {
        barrier(cmd, planes[0].image, VK_IMAGE_LAYOUT_GENERAL, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL,
                VK_ACCESS_SHADER_READ_BIT, VK_ACCESS_TRANSFER_READ_BIT,
                VK_PIPELINE_STAGE_FRAGMENT_SHADER_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT);
        copy.bufferOffset = rgba_bytes;
        vkCmdCopyImageToBuffer(cmd, planes[0].image, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, readback, 1, &copy);
    }
    VK_CHECK(vkEndCommandBuffer(cmd));
    VkFence fence;
    VkFenceCreateInfo fi = { VK_STRUCTURE_TYPE_FENCE_CREATE_INFO };
    VK_CHECK(vkCreateFence(g.device, &fi, nullptr, &fence));
    VkSubmitInfo submit = { VK_STRUCTURE_TYPE_SUBMIT_INFO };
    submit.commandBufferCount = 1; submit.pCommandBuffers = &cmd;
    VK_CHECK(vkQueueSubmit(g.queue, 1, &submit, fence));
    VK_CHECK(vkWaitForFences(g.device, 1, &fence, VK_TRUE, 60ull * 1000 * 1000 * 1000));

    Result r;
    void *mapped = nullptr;
    VK_CHECK(vkMapMemory(g.device, readback_memory, 0, VK_WHOLE_SIZE, 0, &mapped));
    r.rgba.assign(static_cast<uint8_t *>(mapped), static_cast<uint8_t *>(mapped) + rgba_bytes);
    if (!fused) r.luma.assign(static_cast<uint8_t *>(mapped) + rgba_bytes, static_cast<uint8_t *>(mapped) + rgba_bytes + luma_bytes);
    vkUnmapMemory(g.device, readback_memory);

    VK_CHECK(vkDeviceWaitIdle(g.device));
    vkDestroyFence(g.device, fence, nullptr);
    vkFreeCommandBuffers(g.device, g.pool, 1, &cmd);
    vkDestroyBuffer(g.device, readback, nullptr);
    vkFreeMemory(g.device, readback_memory, nullptr);
    vkDestroyFramebuffer(g.device, fb, nullptr);
    vkDestroyDescriptorPool(g.device, pool, nullptr);
    vkDestroySampler(g.device, sampler, nullptr);
    destroy_pass(g, pass);
    destroy_image(g, out);
    for (auto &p : planes) destroy_image(g, p);
    pyrowave_decoder_destroy(dec);
    return r;
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

} // namespace

int main(int argc, char **argv) {
    struct Case { uint32_t w, h; size_t bytes; };
    std::vector<Case> cases;
    for (int i = 1; i + 2 < argc; i += 3)
        cases.push_back({ uint32_t(atoi(argv[i])), uint32_t(atoi(argv[i + 1])), size_t(atoll(argv[i + 2])) });
    if (cases.empty()) cases = { { 512, 320, 131072 }, { 4160, 2208, 2083333 } };

    setvbuf(stdout, nullptr, _IONBF, 0);
    // Decode fidelity floor against the source. Lavapipe's PyroWave decode is not faithful
    // (about 13 dB), so CI sets GATE_MIN_PSNR=0 and checks only path equivalence there.
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
        const bool rejected = pyrowave_decoder_set_final_luma_store(dec, 0) != PYROWAVE_SUCCESS;
        pyrowave_decoder_destroy(dec);
        di.chroma = PYROWAVE_CHROMA_SUBSAMPLING_420; di.wavelet = PYROWAVE_WAVELET_CDF97;
        PW_CHECK(pyrowave_decoder_create(&di, &dec));
        const bool rejected97 = pyrowave_decoder_set_final_luma_store(dec, 0) != PYROWAVE_SUCCESS;
        const bool dh_rejected97 = pyrowave_decoder_set_fused_dequant_haar(dec, 1) != PYROWAVE_SUCCESS;
        pyrowave_decoder_destroy(dec);
        // The two final-pass replacements are mutually exclusive in either order.
        di.wavelet = PYROWAVE_WAVELET_HAAR;
        PW_CHECK(pyrowave_decoder_create(&di, &dec));
        PW_CHECK(pyrowave_decoder_set_final_luma_store(dec, 0));
        const bool dh_after_skip = pyrowave_decoder_set_fused_dequant_haar(dec, 1) != PYROWAVE_SUCCESS;
        PW_CHECK(pyrowave_decoder_set_final_luma_store(dec, 1));
        PW_CHECK(pyrowave_decoder_set_fused_dequant_haar(dec, 1));
        const bool skip_after_dh = pyrowave_decoder_set_final_luma_store(dec, 0) != PYROWAVE_SUCCESS;
        pyrowave_decoder_destroy(dec);
        printf("unsupported 4:4:4 rejected=%d, CDF 9/7 rejected=%d, dequant_haar CDF 9/7 rejected=%d, "
               "exclusive=%d/%d\n", rejected, rejected97, dh_rejected97, dh_after_skip, skip_after_dh);
        if (!rejected || !rejected97 || !dh_rejected97 || !dh_after_skip || !skip_after_dh) return 1;
    }

    bool ok = true;
    for (const Case &c : cases) {
        printf("case %ux%u cap %zu\n", c.w, c.h, c.bytes);
        std::vector<uint8_t> y, cb, cr;
        make_source(c.w, c.h, y, cb, cr);
        std::vector<std::vector<uint8_t>> packets;
        char path[256];
        snprintf(path, sizeof path, "%s/fuse_gate_%ux%u_%zu.packets", getenv("GATE_PACKET_DIR") ? getenv("GATE_PACKET_DIR") : ".", c.w, c.h, c.bytes);
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
        for (int limited = 0; limited < 2; limited++)
            for (int filter = 0; filter < 2; filter++) {
                const Result a = run(g, c.w, c.h, packets, Path::Current, limited, filter);
                const Result b = run(g, c.w, c.h, packets, Path::FusedColor, limited, filter);
                const Diff rgba = compare(a.rgba, b.rgba);
                // The dequant+Haar kernel does not touch color conversion, so one variant suffices.
                Diff dh_luma, dh_rgba;
                const bool run_dh = limited == 0 && filter == 0;
                if (run_dh) {
                    const Result d = run(g, c.w, c.h, packets, Path::DequantHaar, limited, filter);
                    dh_luma = compare(a.luma, d.luma);
                    dh_rgba = compare(a.rgba, d.rgba);
                }
                const double luma_psnr = psnr(a.luma, y);
                if (const char *dir = getenv("GATE_DUMP_DIR"); dir && limited == 0 && filter == 0) {
                    // Source and decoded luma as PGM, to inspect a low PSNR by eye.
                    for (int which = 0; which < 2; which++) {
                        char name[512];
                        snprintf(name, sizeof name, "%s/fuse_gate_%ux%u_%s.pgm", dir, c.w, c.h, which ? "decoded" : "source");
                        if (FILE *f = fopen(name, "wb")) {
                            fprintf(f, "P5\n%u %u\n255\n", c.w, c.h);
                            fwrite(which ? a.luma.data() : y.data(), 1, y.size(), f);
                            fclose(f);
                        }
                    }
                }
                std::vector<int> seen(256);
                for (uint8_t v : a.luma) seen[v] = 1;
                int distinct = 0;
                for (int v : seen) distinct += v;
                // Identical but trivial images (blank decode) must not pass.
                const bool nontrivial = distinct >= 64;
                const bool pass = rgba.max <= 1 && nontrivial && luma_psnr >= min_psnr;
                printf("  limited=%d catmull=%d luma_psnr=%.2f dB distinct_luma=%d rgba_max_diff=%d differing_bytes=%zu/%zu %s\n",
                       limited, filter, luma_psnr, distinct, rgba.max, rgba.differing, a.rgba.size(), pass ? "PASS" : "FAIL");
                ok = ok && pass;
                if (run_dh) {
                    const bool dh_pass = dh_luma.max <= 1 && dh_rgba.max <= 1 && nontrivial;
                    printf("  dequant_haar luma_max_diff=%d differing=%zu/%zu rgba_max_diff=%d differing_bytes=%zu/%zu %s\n",
                           dh_luma.max, dh_luma.differing, a.luma.size(), dh_rgba.max, dh_rgba.differing,
                           a.rgba.size(), dh_pass ? "PASS" : "FAIL");
                    ok = ok && dh_pass;
                }
            }
    }
    pyrowave_device_destroy(g.pyro);
    vkDestroyCommandPool(g.device, g.pool, nullptr);
    vkDestroyDevice(g.device, nullptr);
    vkDestroyInstance(g.instance, nullptr);
    printf(ok ? "FUSE_COLOR_GATE_PASS\n" : "FUSE_COLOR_GATE_FAIL\n");
    return ok ? 0 : 1;
}
