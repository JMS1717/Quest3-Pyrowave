// See pyroclient.h. Ported from tools/pyrowave_android/main.cpp, which proved every step here on
// the Adreno 740; the harness stays as the record and the scoring tool.
#include "pyroclient.h"
#include "decode_path.h"

#include <android/hardware_buffer.h>
#include <android/log.h>
#include <vulkan/vulkan.h>
#include <vulkan/vulkan_android.h>

#include <chrono>
#include <sys/system_properties.h>
#include <cstdio>
#include <cstring>
#include <cstdlib>
#include <vector>

#include "pyrowave.h"
#include "ycbcr_to_rgba_spv.h"
#include "convert_vert_spv.h"
#include "convert_frag_spv.h"

#define TAG "pyroclient"
#define LOGE(...) __android_log_print(ANDROID_LOG_ERROR, TAG, __VA_ARGS__)
#define LOGI(...) __android_log_print(ANDROID_LOG_INFO, TAG, __VA_ARGS__)

#define VK_TRY(x)                                                                            \
    do {                                                                                     \
        VkResult _r = (x);                                                                   \
        if (_r != VK_SUCCESS) {                                                              \
            LOGE("%s failed: %d (%s:%d)", #x, (int)_r, __FILE__, __LINE__);                 \
            return false;                                                                    \
        }                                                                                    \
    } while (0)
#define PW_TRY(x)                                                                            \
    do {                                                                                     \
        pyrowave_result _r = (x);                                                            \
        if (_r != PYROWAVE_SUCCESS) {                                                        \
            LOGE("%s failed: %d (%s:%d)", #x, (int)_r, __FILE__, __LINE__);                 \
            return false;                                                                    \
        }                                                                                    \
    } while (0)

namespace {

struct Plane {
    VkImage image = VK_NULL_HANDLE;
    VkDeviceMemory memory = VK_NULL_HANDLE;
    VkImageView view = VK_NULL_HANDLE;
    uint32_t width = 0, height = 0;
};

// One output slot: an RGBA8 AHardwareBuffer, the VkImage bound to it, and its view. `storage` is
// whether the driver let us bind it as a storage image (then the convert pass writes it directly);
// otherwise the pass writes `scratch` and a copy moves it across.
struct Slot {
    AHardwareBuffer *ahb = nullptr;
    VkImage image = VK_NULL_HANDLE;
    VkDeviceMemory memory = VK_NULL_HANDLE;
    VkImageView view = VK_NULL_HANDLE;
    VkDescriptorSet set = VK_NULL_HANDLE;
    VkFramebuffer framebuffer = VK_NULL_HANDLE;
    bool first_use = true;
};

uint32_t find_memory_type(VkPhysicalDevice gpu, uint32_t bits, VkMemoryPropertyFlags want) {
    VkPhysicalDeviceMemoryProperties props;
    vkGetPhysicalDeviceMemoryProperties(gpu, &props);
    for (uint32_t t = 0; t < props.memoryTypeCount; t++)
        if ((bits & (1u << t)) && (props.memoryTypes[t].propertyFlags & want) == want) return t;
    return UINT32_MAX;
}

}  // namespace

struct pyroclient {
    uint32_t width = 0, height = 0;
    bool chroma444 = false;
    bool full_range = true;
    bool fragment_path = false;
    bool legall53 = false;      // Experiment 2: CDF 5/3 instead of 9/7 (compute path only)
    int decode_path_hint = 0;   // dashboard setting: 0 auto, 1 fragment, 2 compute

    VkInstance instance = VK_NULL_HANDLE;
    VkPhysicalDevice gpu = VK_NULL_HANDLE;
    VkDevice device = VK_NULL_HANDLE;
    uint32_t family = 0;
    VkQueue queue = VK_NULL_HANDLE;
    double ns_per_tick = 1.0;

    // The create infos must outlive the pyrowave_device.
    VkApplicationInfo app_info{};
    VkInstanceCreateInfo instance_info{};
    float queue_priority = 1.0f;
    VkDeviceQueueCreateInfo queue_info{};
    VkPhysicalDeviceVulkan13Features f13{};
    VkPhysicalDeviceVulkan12Features f12{};
    VkPhysicalDeviceVulkan11Features f11{};
    VkPhysicalDeviceFeatures2 f2{};
    VkDeviceCreateInfo device_info{};
    const char *device_extensions[2] = {
        VK_ANDROID_EXTERNAL_MEMORY_ANDROID_HARDWARE_BUFFER_EXTENSION_NAME,
        VK_EXT_QUEUE_FAMILY_FOREIGN_EXTENSION_NAME,
    };

    pyrowave_device pyro = nullptr;
    pyrowave_decoder decoder = nullptr;
    Plane planes[3];
    pyrowave_gpu_buffers buffers{};

    // Conversion pass.
    VkSampler sampler = VK_NULL_HANDLE;
    VkDescriptorSetLayout set_layout = VK_NULL_HANDLE;
    VkPipelineLayout pipeline_layout = VK_NULL_HANDLE;
    VkPipeline pipeline = VK_NULL_HANDLE;
    VkPipeline fragment_pipeline = VK_NULL_HANDLE;
    VkRenderPass convert_render_pass = VK_NULL_HANDLE;
    bool fragment_convert = false;
    VkDescriptorPool desc_pool = VK_NULL_HANDLE;
    bool storage_on_ahb = false;   // convert writes the AHB image directly
    Plane scratch;                 // else convert writes here and a copy follows
    VkDescriptorSet scratch_set = VK_NULL_HANDLE;

    std::vector<Slot> ring;
    uint32_t next_slot = 0;

    VkCommandPool pool = VK_NULL_HANDLE;
    VkCommandBuffer cmd = VK_NULL_HANDLE;
    VkFence fence = VK_NULL_HANDLE;
    VkQueryPool queries = VK_NULL_HANDLE;
    bool planes_initialised = false;

    bool create_device();
    bool create_planes();
    bool create_convert();
    bool create_fragment_convert();
    bool create_slot(Slot &s);
    bool record_and_submit(Slot &s, pyroclient_frame_info *info);
    void destroy();
};

bool pyroclient::create_device() {
    app_info = { VK_STRUCTURE_TYPE_APPLICATION_INFO };
    app_info.pApplicationName = "pyroclient";
    app_info.apiVersion = VK_API_VERSION_1_3;
    instance_info = { VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO };
    instance_info.pApplicationInfo = &app_info;
    VK_TRY(vkCreateInstance(&instance_info, nullptr, &instance));

    uint32_t n = 0;
    vkEnumeratePhysicalDevices(instance, &n, nullptr);
    std::vector<VkPhysicalDevice> gpus(n);
    vkEnumeratePhysicalDevices(instance, &n, gpus.data());
    if (gpus.empty()) { LOGE("no Vulkan device"); return false; }
    gpu = gpus[0];
    VkPhysicalDeviceProperties props;
    vkGetPhysicalDeviceProperties(gpu, &props);
    ns_per_tick = props.limits.timestampPeriod;
    LOGI("gpu %s", props.deviceName);

    uint32_t fc = 0;
    vkGetPhysicalDeviceQueueFamilyProperties(gpu, &fc, nullptr);
    std::vector<VkQueueFamilyProperties> fams(fc);
    vkGetPhysicalDeviceQueueFamilyProperties(gpu, &fc, fams.data());
    family = UINT32_MAX;
    for (uint32_t i = 0; i < fc; i++)
        if (fams[i].queueFlags & VK_QUEUE_GRAPHICS_BIT) { family = i; break; }
    if (family == UINT32_MAX) { LOGE("no graphics queue"); return false; }

    queue_info = { VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO };
    queue_info.queueFamilyIndex = family;
    queue_info.queueCount = 1;
    queue_info.pQueuePriorities = &queue_priority;
    f13 = { VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_3_FEATURES };
    f12 = { VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES };
    f11 = { VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_1_FEATURES };
    f2 = { VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2 };
    f2.pNext = &f11; f11.pNext = &f12; f12.pNext = &f13;
    vkGetPhysicalDeviceFeatures2(gpu, &f2);   // enable exactly what the driver has
    device_info = { VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO };
    device_info.pNext = &f2;
    device_info.queueCreateInfoCount = 1;
    device_info.pQueueCreateInfos = &queue_info;
    device_info.enabledExtensionCount = 2;
    device_info.ppEnabledExtensionNames = device_extensions;
    VK_TRY(vkCreateDevice(gpu, &device_info, nullptr, &device));
    vkGetDeviceQueue(device, family, 0, &queue);

    // Experiment 2: debug.xrwired.pyro_precision = 0|1|2 selects PyroWave's math /
    // storage precision (0 = FP16 math, all levels R16F; 1 = FP32 math, 2 levels R16F; 2 = FP32).
    // PyroWave reads it from the environment when the device is created, so it is set here, once.
    // Precision 0 needs shaderFloat16; the library silently falls back to 1 without it, so both are
    // logged: the property and the feature are the FP16 validation gate's necessary conditions.
    {
        char prop[PROP_VALUE_MAX] = {0};
        const char *requested = "(default 1)";
        if (__system_property_get("debug.xrwired.pyro_precision", prop) > 0 && prop[0] >= '0' && prop[0] <= '2' && !prop[1]) {
            setenv("PYROWAVE_PRECISION", prop, 1);
            requested = prop;
        } else {
            unsetenv("PYROWAVE_PRECISION");
        }
        const char *effective = requested;
        if (!strcmp(requested, "0") && !f12.shaderFloat16) effective = "1 (no shaderFloat16, FP16 math unavailable)";
        LOGI("pyro precision requested %s, effective %s; shaderFloat16=%u storageBuffer16BitAccess=%u shaderInt16=%u",
             requested, effective, (unsigned)f12.shaderFloat16, (unsigned)f11.storageBuffer16BitAccess, (unsigned)f2.features.shaderInt16);
    }

    pyrowave_device_create_info pi = {};
    pi.GetInstanceProcAddr = vkGetInstanceProcAddr;
    pi.instance = instance;
    pi.physical_device = gpu;
    pi.device = device;
    pi.instance_create_info = &instance_info;
    pi.device_create_info = &device_info;
    PW_TRY(pyrowave_create_device(&pi, &pyro));
    // The dashboard's headset decode path arrives as a hint; the research property
    // debug.xrwired.decode_path = fragment|compute still overrides it (Experiment 1 A/B'd the two
    // on identical bytes), and CDF 5/3 exists only in the compute shaders. Read once, here.
    const char *forced;
    {
        char prop[PROP_VALUE_MAX] = {0};
        const bool has_prop = __system_property_get("debug.xrwired.decode_path", prop) > 0;
        char model[PROP_VALUE_MAX] = {0};
        __system_property_get("ro.product.model", model);
        const bool quest3 = !strcmp(model, "Quest 3");
        // Quest 3 reference/readback test favors Compute correctness. Keep explicit A/B choices.
        DecodePathChoice choice = choose_decode_path(
            quest3 ? false : pyrowave_decoder_device_prefers_fragment_path(pyro), decode_path_hint,
            has_prop ? prop : nullptr, legall53);
        fragment_path = choice.fragment;
        forced = choice.reason;
    }
    // The queue type must match the path, or the work is recorded against the wrong queue.
    PW_TRY(pyrowave_device_set_queue_type(pyro, fragment_path ? VK_QUEUE_GRAPHICS_BIT : VK_QUEUE_COMPUTE_BIT));
    LOGI("pyrowave device (borrowed), %s path", fragment_path ? "fragment" : "compute");
    LOGI("decode path %s (%s)", fragment_path ? "fragment" : "compute", forced);
    LOGI("wavelet CDF %s", legall53 ? "5/3" : "9/7");

    VkCommandPoolCreateInfo pool_info = { VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO };
    pool_info.queueFamilyIndex = family;
    pool_info.flags = VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT;
    VK_TRY(vkCreateCommandPool(device, &pool_info, nullptr, &pool));
    VkCommandBufferAllocateInfo ca = { VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO };
    ca.commandPool = pool; ca.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY; ca.commandBufferCount = 1;
    VK_TRY(vkAllocateCommandBuffers(device, &ca, &cmd));
    VkFenceCreateInfo fi = { VK_STRUCTURE_TYPE_FENCE_CREATE_INFO };
    VK_TRY(vkCreateFence(device, &fi, nullptr, &fence));
    VkQueryPoolCreateInfo qi = { VK_STRUCTURE_TYPE_QUERY_POOL_CREATE_INFO };
    qi.queryType = VK_QUERY_TYPE_TIMESTAMP; qi.queryCount = 3;
    VK_TRY(vkCreateQueryPool(device, &qi, nullptr, &queries));
    return true;
}

// Plain R8 images: this device has no single-component hardware buffers, and PyroWave's planes
// are single-component. Both STORAGE and COLOR_ATTACHMENT, because the header wants STORAGE on a
// decode view and the fragment path writes colour attachments.
static bool create_plain_image(VkPhysicalDevice gpu, VkDevice device, VkFormat format, uint32_t w,
                               uint32_t h, VkImageUsageFlags usage, Plane &p) {
    VkImageCreateInfo ii = { VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO };
    ii.imageType = VK_IMAGE_TYPE_2D; ii.format = format; ii.extent = { w, h, 1 };
    ii.mipLevels = 1; ii.arrayLayers = 1; ii.samples = VK_SAMPLE_COUNT_1_BIT;
    ii.tiling = VK_IMAGE_TILING_OPTIMAL; ii.usage = usage;
    ii.sharingMode = VK_SHARING_MODE_EXCLUSIVE; ii.initialLayout = VK_IMAGE_LAYOUT_UNDEFINED;
    VK_TRY(vkCreateImage(device, &ii, nullptr, &p.image));
    VkMemoryRequirements req;
    vkGetImageMemoryRequirements(device, p.image, &req);
    uint32_t type = find_memory_type(gpu, req.memoryTypeBits, VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT);
    if (type == UINT32_MAX) { LOGE("no device-local memory"); return false; }
    VkMemoryAllocateInfo ai = { VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO };
    ai.allocationSize = req.size; ai.memoryTypeIndex = type;
    VK_TRY(vkAllocateMemory(device, &ai, nullptr, &p.memory));
    VK_TRY(vkBindImageMemory(device, p.image, p.memory, 0));
    VkImageViewCreateInfo vi = { VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO };
    vi.image = p.image; vi.viewType = VK_IMAGE_VIEW_TYPE_2D; vi.format = format;
    vi.subresourceRange = { VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1 };
    VK_TRY(vkCreateImageView(device, &vi, nullptr, &p.view));
    p.width = w; p.height = h;
    return true;
}

bool pyroclient::create_planes() {
    const uint32_t cw = chroma444 ? width : width / 2, ch = chroma444 ? height : height / 2;
    for (int i = 0; i < 3; i++) {
        if (!create_plain_image(gpu, device, VK_FORMAT_R8_UNORM, i ? cw : width, i ? ch : height,
                                VK_IMAGE_USAGE_SAMPLED_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT
                                    | VK_IMAGE_USAGE_STORAGE_BIT | VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT,
                                planes[i]))
            return false;
        pyrowave_image_view &v = buffers.planes[i];
        v.image = planes[i].image;
        // WrappedViewBuffers uses these extents for viewport/render-area construction.
        // A 4:2:0 chroma target must describe its actual half-sized image.
        v.width = planes[i].width; v.height = planes[i].height;
        v.image_format = VK_FORMAT_R8_UNORM; v.view_format = VK_FORMAT_R8_UNORM;
        v.mip_level = 0; v.layer = 0; v.aspect = VK_IMAGE_ASPECT_COLOR_BIT;
        v.swizzle = VK_COMPONENT_SWIZZLE_IDENTITY; v.layout = VK_IMAGE_LAYOUT_GENERAL;
    }
    pyrowave_decoder_create_info di = {};
    di.device = pyro; di.width = width; di.height = height;
    di.chroma = chroma444 ? PYROWAVE_CHROMA_SUBSAMPLING_444 : PYROWAVE_CHROMA_SUBSAMPLING_420;
    di.fragment_path = fragment_path;
    di.wavelet = legall53 ? PYROWAVE_WAVELET_CDF53 : PYROWAVE_WAVELET_CDF97;
    PW_TRY(pyrowave_decoder_create(&di, &decoder));
    return true;
}

bool pyroclient::create_convert() {
    VkSamplerCreateInfo si = { VK_STRUCTURE_TYPE_SAMPLER_CREATE_INFO };
    si.magFilter = si.minFilter = VK_FILTER_LINEAR;
    si.addressModeU = si.addressModeV = si.addressModeW = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
    VK_TRY(vkCreateSampler(device, &si, nullptr, &sampler));

    VkDescriptorSetLayoutBinding b[4] = {};
    for (int i = 0; i < 3; i++) {
        b[i].binding = i; b[i].descriptorType = VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER;
        b[i].descriptorCount = 1; b[i].stageFlags = VK_SHADER_STAGE_COMPUTE_BIT | VK_SHADER_STAGE_FRAGMENT_BIT;
    }
    b[3].binding = 3; b[3].descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_IMAGE;
    b[3].descriptorCount = 1; b[3].stageFlags = VK_SHADER_STAGE_COMPUTE_BIT;
    VkDescriptorSetLayoutCreateInfo li = { VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO };
    li.bindingCount = 4; li.pBindings = b;
    VK_TRY(vkCreateDescriptorSetLayout(device, &li, nullptr, &set_layout));
    VkPushConstantRange pc = { VK_SHADER_STAGE_COMPUTE_BIT | VK_SHADER_STAGE_FRAGMENT_BIT, 0, sizeof(int32_t) };
    VkPipelineLayoutCreateInfo pli = { VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO };
    pli.setLayoutCount = 1; pli.pSetLayouts = &set_layout;
    pli.pushConstantRangeCount = 1; pli.pPushConstantRanges = &pc;
    VK_TRY(vkCreatePipelineLayout(device, &pli, nullptr, &pipeline_layout));
    VkShaderModuleCreateInfo smi = { VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO };
    smi.codeSize = sizeof(YCBCR_TO_RGBA_SPV); smi.pCode = YCBCR_TO_RGBA_SPV;
    VkShaderModule module;
    VK_TRY(vkCreateShaderModule(device, &smi, nullptr, &module));
    VkComputePipelineCreateInfo cpi = { VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO };
    cpi.stage.sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO;
    cpi.stage.stage = VK_SHADER_STAGE_COMPUTE_BIT; cpi.stage.module = module; cpi.stage.pName = "main";
    cpi.layout = pipeline_layout;
    VkResult pr = vkCreateComputePipelines(device, VK_NULL_HANDLE, 1, &cpi, nullptr, &pipeline);
    vkDestroyShaderModule(device, module, nullptr);
    if (pr != VK_SUCCESS) { LOGE("convert pipeline: %d", (int)pr); return false; }

    const uint32_t sets = (uint32_t)ring.size() + 1;
    VkDescriptorPoolSize ps[2] = {
        { VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER, 3 * sets },
        { VK_DESCRIPTOR_TYPE_STORAGE_IMAGE, sets },
    };
    VkDescriptorPoolCreateInfo dpi = { VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO };
    dpi.maxSets = sets; dpi.poolSizeCount = 2; dpi.pPoolSizes = ps;
    VK_TRY(vkCreateDescriptorPool(device, &dpi, nullptr, &desc_pool));
    return true;
}

bool pyroclient::create_fragment_convert() {
    VkAttachmentDescription attachment = {};
    attachment.format = VK_FORMAT_R8G8B8A8_UNORM; attachment.samples = VK_SAMPLE_COUNT_1_BIT;
    attachment.loadOp = VK_ATTACHMENT_LOAD_OP_DONT_CARE; attachment.storeOp = VK_ATTACHMENT_STORE_OP_STORE;
    attachment.stencilLoadOp = VK_ATTACHMENT_LOAD_OP_DONT_CARE; attachment.stencilStoreOp = VK_ATTACHMENT_STORE_OP_DONT_CARE;
    attachment.initialLayout = attachment.finalLayout = VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL;
    VkAttachmentReference reference = {0, VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL};
    VkSubpassDescription subpass = {}; subpass.pipelineBindPoint = VK_PIPELINE_BIND_POINT_GRAPHICS;
    subpass.colorAttachmentCount = 1; subpass.pColorAttachments = &reference;
    VkRenderPassCreateInfo rp = { VK_STRUCTURE_TYPE_RENDER_PASS_CREATE_INFO };
    rp.attachmentCount = 1; rp.pAttachments = &attachment; rp.subpassCount = 1; rp.pSubpasses = &subpass;
    VK_TRY(vkCreateRenderPass(device, &rp, nullptr, &convert_render_pass));
    VkShaderModule modules[2] = {};
    VkShaderModuleCreateInfo sm = { VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO };
    sm.codeSize = sizeof(CONVERT_VERT_SPV); sm.pCode = CONVERT_VERT_SPV;
    VK_TRY(vkCreateShaderModule(device, &sm, nullptr, &modules[0]));
    sm.codeSize = sizeof(CONVERT_FRAG_SPV); sm.pCode = CONVERT_FRAG_SPV;
    VkResult created = vkCreateShaderModule(device, &sm, nullptr, &modules[1]);
    if (created != VK_SUCCESS) { vkDestroyShaderModule(device, modules[0], nullptr); return false; }
    VkPipelineShaderStageCreateInfo stages[2] = {};
    for (int i = 0; i < 2; i++) {
        stages[i].sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO;
        stages[i].stage = i ? VK_SHADER_STAGE_FRAGMENT_BIT : VK_SHADER_STAGE_VERTEX_BIT;
        stages[i].module = modules[i]; stages[i].pName = "main";
    }
    VkPipelineVertexInputStateCreateInfo vertex = { VK_STRUCTURE_TYPE_PIPELINE_VERTEX_INPUT_STATE_CREATE_INFO };
    VkPipelineInputAssemblyStateCreateInfo assembly = { VK_STRUCTURE_TYPE_PIPELINE_INPUT_ASSEMBLY_STATE_CREATE_INFO };
    assembly.topology = VK_PRIMITIVE_TOPOLOGY_TRIANGLE_LIST;
    VkViewport viewport = {0, 0, float(width), float(height), 0, 1};
    VkRect2D scissor = {{0, 0}, {width, height}};
    VkPipelineViewportStateCreateInfo vp = { VK_STRUCTURE_TYPE_PIPELINE_VIEWPORT_STATE_CREATE_INFO };
    vp.viewportCount = 1; vp.pViewports = &viewport; vp.scissorCount = 1; vp.pScissors = &scissor;
    VkPipelineRasterizationStateCreateInfo raster = { VK_STRUCTURE_TYPE_PIPELINE_RASTERIZATION_STATE_CREATE_INFO };
    raster.polygonMode = VK_POLYGON_MODE_FILL; raster.cullMode = VK_CULL_MODE_NONE; raster.lineWidth = 1;
    VkPipelineMultisampleStateCreateInfo samples = { VK_STRUCTURE_TYPE_PIPELINE_MULTISAMPLE_STATE_CREATE_INFO };
    samples.rasterizationSamples = VK_SAMPLE_COUNT_1_BIT;
    VkPipelineColorBlendAttachmentState blend = {}; blend.colorWriteMask = 0xf;
    VkPipelineColorBlendStateCreateInfo blends = { VK_STRUCTURE_TYPE_PIPELINE_COLOR_BLEND_STATE_CREATE_INFO };
    blends.attachmentCount = 1; blends.pAttachments = &blend;
    VkGraphicsPipelineCreateInfo pipeline_info = { VK_STRUCTURE_TYPE_GRAPHICS_PIPELINE_CREATE_INFO };
    pipeline_info.stageCount = 2; pipeline_info.pStages = stages; pipeline_info.pVertexInputState = &vertex;
    pipeline_info.pInputAssemblyState = &assembly; pipeline_info.pViewportState = &vp;
    pipeline_info.pRasterizationState = &raster; pipeline_info.pMultisampleState = &samples;
    pipeline_info.pColorBlendState = &blends; pipeline_info.layout = pipeline_layout;
    pipeline_info.renderPass = convert_render_pass;
    VkResult result = vkCreateGraphicsPipelines(device, VK_NULL_HANDLE, 1, &pipeline_info, nullptr, &fragment_pipeline);
    for (auto module : modules) vkDestroyShaderModule(device, module, nullptr);
    return result == VK_SUCCESS;
}

static bool write_set(VkDevice device, VkSampler sampler, const Plane planes[3], VkImageView out,
                      VkDescriptorSet set) {
    VkDescriptorImageInfo pi[3] = {};
    VkWriteDescriptorSet w[4] = {};
    for (int i = 0; i < 3; i++) {
        pi[i].sampler = sampler; pi[i].imageView = planes[i].view; pi[i].imageLayout = VK_IMAGE_LAYOUT_GENERAL;
        w[i].sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET; w[i].dstSet = set; w[i].dstBinding = i;
        w[i].descriptorCount = 1; w[i].descriptorType = VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER;
        w[i].pImageInfo = &pi[i];
    }
    VkDescriptorImageInfo oi = {}; oi.imageView = out; oi.imageLayout = VK_IMAGE_LAYOUT_GENERAL;
    w[3].sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET; w[3].dstSet = set; w[3].dstBinding = 3;
    w[3].descriptorCount = 1; w[3].descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_IMAGE; w[3].pImageInfo = &oi;
    vkUpdateDescriptorSets(device, 4, w, 0, nullptr);
    return true;
}

// An RGBA8 AHardwareBuffer the GLES side can import, bound to a VkImage we can write. Storage
// usage on an imported buffer is what makes the convert pass one dispatch; if the driver refuses
// it we fall back to TRANSFER_DST and a copy, and say so once.
bool pyroclient::create_slot(Slot &s) {
    AHardwareBuffer_Desc d = {};
    d.width = width; d.height = height; d.layers = 1;
    d.format = AHARDWAREBUFFER_FORMAT_R8G8B8A8_UNORM;
    d.usage = AHARDWAREBUFFER_USAGE_GPU_SAMPLED_IMAGE | AHARDWAREBUFFER_USAGE_GPU_COLOR_OUTPUT;
    if (AHardwareBuffer_allocate(&d, &s.ahb) != 0 || !s.ahb) { LOGE("AHardwareBuffer_allocate %ux%u", width, height); return false; }

    auto getProps = (PFN_vkGetAndroidHardwareBufferPropertiesANDROID)vkGetDeviceProcAddr(device, "vkGetAndroidHardwareBufferPropertiesANDROID");
    if (!getProps) { LOGE("no vkGetAndroidHardwareBufferPropertiesANDROID"); return false; }
    VkAndroidHardwareBufferPropertiesANDROID ap = { VK_STRUCTURE_TYPE_ANDROID_HARDWARE_BUFFER_PROPERTIES_ANDROID };
    VK_TRY(getProps(device, s.ahb, &ap));

    VkExternalMemoryImageCreateInfo ext = { VK_STRUCTURE_TYPE_EXTERNAL_MEMORY_IMAGE_CREATE_INFO };
    ext.handleTypes = VK_EXTERNAL_MEMORY_HANDLE_TYPE_ANDROID_HARDWARE_BUFFER_BIT_ANDROID;
    VkImageCreateInfo ii = { VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO };
    ii.pNext = &ext;
    ii.imageType = VK_IMAGE_TYPE_2D; ii.format = VK_FORMAT_R8G8B8A8_UNORM;
    ii.extent = { width, height, 1 }; ii.mipLevels = 1; ii.arrayLayers = 1;
    ii.samples = VK_SAMPLE_COUNT_1_BIT; ii.tiling = VK_IMAGE_TILING_OPTIMAL;
    ii.usage = VK_IMAGE_USAGE_SAMPLED_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT
        | (storage_on_ahb ? VK_IMAGE_USAGE_STORAGE_BIT : 0)
        | (fragment_convert ? VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT : 0);
    ii.sharingMode = VK_SHARING_MODE_EXCLUSIVE; ii.initialLayout = VK_IMAGE_LAYOUT_UNDEFINED;
    VK_TRY(vkCreateImage(device, &ii, nullptr, &s.image));

    VkImportAndroidHardwareBufferInfoANDROID imp = { VK_STRUCTURE_TYPE_IMPORT_ANDROID_HARDWARE_BUFFER_INFO_ANDROID };
    imp.buffer = s.ahb;
    VkMemoryDedicatedAllocateInfo ded = { VK_STRUCTURE_TYPE_MEMORY_DEDICATED_ALLOCATE_INFO };
    ded.image = s.image; ded.pNext = &imp;
    uint32_t type = find_memory_type(gpu, ap.memoryTypeBits, 0);
    if (type == UINT32_MAX) { LOGE("no memory type for AHB"); return false; }
    VkMemoryAllocateInfo ai = { VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO };
    ai.pNext = &ded; ai.allocationSize = ap.allocationSize; ai.memoryTypeIndex = type;
    VK_TRY(vkAllocateMemory(device, &ai, nullptr, &s.memory));
    VK_TRY(vkBindImageMemory(device, s.image, s.memory, 0));

    VkImageViewCreateInfo vi = { VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO };
    vi.image = s.image; vi.viewType = VK_IMAGE_VIEW_TYPE_2D; vi.format = VK_FORMAT_R8G8B8A8_UNORM;
    vi.subresourceRange = { VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1 };
    VK_TRY(vkCreateImageView(device, &vi, nullptr, &s.view));
    if (fragment_convert) {
        VkFramebufferCreateInfo fb = { VK_STRUCTURE_TYPE_FRAMEBUFFER_CREATE_INFO };
        fb.renderPass = convert_render_pass; fb.attachmentCount = 1; fb.pAttachments = &s.view;
        fb.width = width; fb.height = height; fb.layers = 1;
        VK_TRY(vkCreateFramebuffer(device, &fb, nullptr, &s.framebuffer));
    }

    if (storage_on_ahb) {
        VkDescriptorSetAllocateInfo sa = { VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO };
        sa.descriptorPool = desc_pool; sa.descriptorSetCount = 1; sa.pSetLayouts = &set_layout;
        VK_TRY(vkAllocateDescriptorSets(device, &sa, &s.set));
        write_set(device, sampler, planes, s.view, s.set);
    }
    return true;
}

static void image_barrier(VkCommandBuffer cmd, VkImage img, VkImageLayout from, VkImageLayout to,
                          VkAccessFlags srcA, VkAccessFlags dstA, VkPipelineStageFlags srcS,
                          VkPipelineStageFlags dstS, uint32_t srcQ = VK_QUEUE_FAMILY_IGNORED,
                          uint32_t dstQ = VK_QUEUE_FAMILY_IGNORED) {
    VkImageMemoryBarrier b = { VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER };
    b.oldLayout = from; b.newLayout = to; b.srcAccessMask = srcA; b.dstAccessMask = dstA;
    b.srcQueueFamilyIndex = srcQ; b.dstQueueFamilyIndex = dstQ; b.image = img;
    b.subresourceRange = { VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1 };
    vkCmdPipelineBarrier(cmd, srcS, dstS, 0, 0, nullptr, 0, nullptr, 1, &b);
}

bool pyroclient::record_and_submit(Slot &s, pyroclient_frame_info *info) {
    const auto t0 = std::chrono::steady_clock::now();
    VK_TRY(vkResetFences(device, 1, &fence));
    VK_TRY(vkResetCommandBuffer(cmd, 0));
    VkCommandBufferBeginInfo bi = { VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO };
    bi.flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
    VK_TRY(vkBeginCommandBuffer(cmd, &bi));

    const VkPipelineStageFlags writeStages = VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT | VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT;
    const VkAccessFlags writeAccess = VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT | VK_ACCESS_SHADER_WRITE_BIT;
    // Planes: created UNDEFINED, the views declare GENERAL, and PyroWave transitions nothing.
    for (int i = 0; i < 3; i++)
        image_barrier(cmd, planes[i].image, planes_initialised ? VK_IMAGE_LAYOUT_GENERAL : VK_IMAGE_LAYOUT_UNDEFINED,
                      VK_IMAGE_LAYOUT_GENERAL, planes_initialised ? VK_ACCESS_SHADER_READ_BIT : 0, writeAccess,
                      planes_initialised ? (VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT | VK_PIPELINE_STAGE_FRAGMENT_SHADER_BIT) : VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT, writeStages);
    planes_initialised = true;

    vkCmdResetQueryPool(cmd, queries, 0, 3);
    vkCmdWriteTimestamp(cmd, VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT, queries, 0);
    pyrowave_device_set_command_buffer(pyro, cmd);
    pyrowave_result dr = pyrowave_decoder_decode_gpu_buffer(decoder, nullptr, nullptr, &buffers);
    pyrowave_device_set_command_buffer(pyro, VK_NULL_HANDLE);
    if (dr != PYROWAVE_SUCCESS) { LOGE("decode_gpu_buffer: %d", (int)dr); return false; }
    vkCmdWriteTimestamp(cmd, VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT, queries, 1);

    for (int i = 0; i < 3; i++)
        image_barrier(cmd, planes[i].image, VK_IMAGE_LAYOUT_GENERAL, VK_IMAGE_LAYOUT_GENERAL, writeAccess,
                      VK_ACCESS_SHADER_READ_BIT, writeStages,
                      fragment_convert ? VK_PIPELINE_STAGE_FRAGMENT_SHADER_BIT : VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT);

    // The output slot: take it back from the foreign (GLES) queue family, or from UNDEFINED the
    // first time, into GENERAL for the shader or TRANSFER_DST for the copy.
    const VkImageLayout outLayout = fragment_convert ? VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL :
        storage_on_ahb ? VK_IMAGE_LAYOUT_GENERAL : VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;
    const VkAccessFlags outAccess = fragment_convert ? VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT :
        storage_on_ahb ? VK_ACCESS_SHADER_WRITE_BIT : VK_ACCESS_TRANSFER_WRITE_BIT;
    const VkPipelineStageFlags outStage = fragment_convert ? VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT :
        storage_on_ahb ? VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT : VK_PIPELINE_STAGE_TRANSFER_BIT;
    image_barrier(cmd, s.image, VK_IMAGE_LAYOUT_UNDEFINED, outLayout, 0, outAccess,
                  VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT, outStage,
                  s.first_use ? VK_QUEUE_FAMILY_IGNORED : VK_QUEUE_FAMILY_FOREIGN_EXT,
                  s.first_use ? VK_QUEUE_FAMILY_IGNORED : family);
    s.first_use = false;

    const int32_t limited = full_range ? 0 : 1;
    if (fragment_convert) {
        VkRenderPassBeginInfo begin = { VK_STRUCTURE_TYPE_RENDER_PASS_BEGIN_INFO };
        begin.renderPass = convert_render_pass; begin.framebuffer = s.framebuffer;
        begin.renderArea.extent = {width, height};
        vkCmdBeginRenderPass(cmd, &begin, VK_SUBPASS_CONTENTS_INLINE);
        vkCmdBindPipeline(cmd, VK_PIPELINE_BIND_POINT_GRAPHICS, fragment_pipeline);
        vkCmdPushConstants(cmd, pipeline_layout, VK_SHADER_STAGE_COMPUTE_BIT | VK_SHADER_STAGE_FRAGMENT_BIT, 0, sizeof limited, &limited);
        vkCmdBindDescriptorSets(cmd, VK_PIPELINE_BIND_POINT_GRAPHICS, pipeline_layout, 0, 1, &s.set, 0, nullptr);
        vkCmdDraw(cmd, 3, 1, 0, 0);
        vkCmdEndRenderPass(cmd);
    } else if (storage_on_ahb) {
        vkCmdBindPipeline(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, pipeline);
        vkCmdPushConstants(cmd, pipeline_layout, VK_SHADER_STAGE_COMPUTE_BIT | VK_SHADER_STAGE_FRAGMENT_BIT, 0, sizeof limited, &limited);
        vkCmdBindDescriptorSets(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, pipeline_layout, 0, 1, &s.set, 0, nullptr);
        vkCmdDispatch(cmd, (width + 7) / 8, (height + 7) / 8, 1);
    } else {
        image_barrier(cmd, scratch.image, VK_IMAGE_LAYOUT_UNDEFINED, VK_IMAGE_LAYOUT_GENERAL, 0,
                      VK_ACCESS_SHADER_WRITE_BIT, VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT);
        vkCmdBindPipeline(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, pipeline);
        vkCmdPushConstants(cmd, pipeline_layout, VK_SHADER_STAGE_COMPUTE_BIT | VK_SHADER_STAGE_FRAGMENT_BIT, 0, sizeof limited, &limited);
        vkCmdBindDescriptorSets(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, pipeline_layout, 0, 1, &scratch_set, 0, nullptr);
        vkCmdDispatch(cmd, (width + 7) / 8, (height + 7) / 8, 1);
        image_barrier(cmd, scratch.image, VK_IMAGE_LAYOUT_GENERAL, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL,
                      VK_ACCESS_SHADER_WRITE_BIT, VK_ACCESS_TRANSFER_READ_BIT,
                      VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT);
        VkImageCopy c = {};
        c.srcSubresource = { VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1 };
        c.dstSubresource = c.srcSubresource;
        c.extent = { width, height, 1 };
        vkCmdCopyImage(cmd, scratch.image, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, s.image,
                       VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1, &c);
    }
    vkCmdWriteTimestamp(cmd, VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT, queries, 2);

    // Hand the buffer to the foreign (GLES) queue family in GENERAL; the EGL import reads it.
    image_barrier(cmd, s.image, outLayout, VK_IMAGE_LAYOUT_GENERAL, outAccess, 0,
                  outStage, VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT, family, VK_QUEUE_FAMILY_FOREIGN_EXT);

    VK_TRY(vkEndCommandBuffer(cmd));
    VkSubmitInfo si = { VK_STRUCTURE_TYPE_SUBMIT_INFO };
    si.commandBufferCount = 1; si.pCommandBuffers = &cmd;
    VK_TRY(vkQueueSubmit(queue, 1, &si, fence));
    VK_TRY(vkWaitForFences(device, 1, &fence, VK_TRUE, 1000ull * 1000 * 1000));

    if (info) {
        uint64_t t[3] = {};
        if (vkGetQueryPoolResults(device, queries, 0, 3, sizeof t, t, sizeof(uint64_t),
                                  VK_QUERY_RESULT_64_BIT | VK_QUERY_RESULT_WAIT_BIT) == VK_SUCCESS) {
            info->decode_ms = double(t[1] - t[0]) * ns_per_tick / 1e6;
            info->convert_ms = double(t[2] - t[1]) * ns_per_tick / 1e6;
        }
        info->total_ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count();
    }
    return true;
}

void pyroclient::destroy() {
    if (device) vkDeviceWaitIdle(device);
    if (decoder) pyrowave_decoder_destroy(decoder);
    for (Slot &s : ring) {
        if (s.framebuffer) vkDestroyFramebuffer(device, s.framebuffer, nullptr);
        if (s.view) vkDestroyImageView(device, s.view, nullptr);
        if (s.image) vkDestroyImage(device, s.image, nullptr);
        if (s.memory) vkFreeMemory(device, s.memory, nullptr);
        if (s.ahb) AHardwareBuffer_release(s.ahb);
    }
    auto killPlane = [&](Plane &p) {
        if (p.view) vkDestroyImageView(device, p.view, nullptr);
        if (p.image) vkDestroyImage(device, p.image, nullptr);
        if (p.memory) vkFreeMemory(device, p.memory, nullptr);
    };
    for (Plane &p : planes) killPlane(p);
    killPlane(scratch);
    if (pipeline) vkDestroyPipeline(device, pipeline, nullptr);
    if (fragment_pipeline) vkDestroyPipeline(device, fragment_pipeline, nullptr);
    if (convert_render_pass) vkDestroyRenderPass(device, convert_render_pass, nullptr);
    if (pipeline_layout) vkDestroyPipelineLayout(device, pipeline_layout, nullptr);
    if (desc_pool) vkDestroyDescriptorPool(device, desc_pool, nullptr);
    if (set_layout) vkDestroyDescriptorSetLayout(device, set_layout, nullptr);
    if (sampler) vkDestroySampler(device, sampler, nullptr);
    if (queries) vkDestroyQueryPool(device, queries, nullptr);
    if (fence) vkDestroyFence(device, fence, nullptr);
    if (pool) vkDestroyCommandPool(device, pool, nullptr);
    if (pyro) pyrowave_device_destroy(pyro);
    if (device) vkDestroyDevice(device, nullptr);
    if (instance) vkDestroyInstance(instance, nullptr);
}

// ---- C API ----

extern "C" pyroclient *pyroclient_create(uint32_t width, uint32_t height, int chroma444, int full_range, uint32_t ring_size, int wavelet) {
    return pyroclient_create_ex(width, height, chroma444, full_range, ring_size, wavelet, 0);
}

extern "C" pyroclient *pyroclient_create_ex(uint32_t width, uint32_t height, int chroma444, int full_range, uint32_t ring_size, int wavelet, int decode_path) {
    if (!width || !height || (!chroma444 && ((width | height) & 1))) { LOGE("bad geometry %ux%u", width, height); return nullptr; }
    if (wavelet != 97 && wavelet != 53) { LOGE("bad wavelet %d (97 or 53)", wavelet); return nullptr; }
    pyroclient *c = new pyroclient();
    c->width = width; c->height = height; c->chroma444 = chroma444 != 0; c->full_range = full_range != 0;
    c->legall53 = wavelet == 53;
    c->decode_path_hint = decode_path;
    c->ring.resize(ring_size < 2 ? 2 : ring_size);
    if (!c->create_device() || !c->create_planes() || !c->create_convert()) { c->destroy(); delete c; return nullptr; }

    // Does this driver take STORAGE usage on an imported AHB image? Ask before creating the ring.
    {
        VkPhysicalDeviceExternalImageFormatInfo ext = { VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_EXTERNAL_IMAGE_FORMAT_INFO };
        ext.handleType = VK_EXTERNAL_MEMORY_HANDLE_TYPE_ANDROID_HARDWARE_BUFFER_BIT_ANDROID;
        VkPhysicalDeviceImageFormatInfo2 fi = { VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_IMAGE_FORMAT_INFO_2 };
        fi.pNext = &ext; fi.format = VK_FORMAT_R8G8B8A8_UNORM; fi.type = VK_IMAGE_TYPE_2D;
        fi.tiling = VK_IMAGE_TILING_OPTIMAL;
        fi.usage = VK_IMAGE_USAGE_SAMPLED_BIT | VK_IMAGE_USAGE_STORAGE_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT;
        VkExternalImageFormatProperties ep = { VK_STRUCTURE_TYPE_EXTERNAL_IMAGE_FORMAT_PROPERTIES };
        VkImageFormatProperties2 p2 = { VK_STRUCTURE_TYPE_IMAGE_FORMAT_PROPERTIES_2 };
        p2.pNext = &ep;
        c->storage_on_ahb = vkGetPhysicalDeviceImageFormatProperties2(c->gpu, &fi, &p2) == VK_SUCCESS
            && (ep.externalMemoryProperties.externalMemoryFeatures & VK_EXTERNAL_MEMORY_FEATURE_IMPORTABLE_BIT);
        LOGI("RGBA8 AHB as storage image: %s", c->storage_on_ahb ? "yes (direct convert)" : "no (convert + copy)");
        VkPhysicalDeviceProperties properties;
        vkGetPhysicalDeviceProperties(c->gpu, &properties);
        // Tile-based color output is faster on the measured Adreno 740. This changes
        // only the YCbCr-to-RGBA bridge, independently of the selected wavelet path.
        const bool prefer_fragment_convert = properties.vendorID == 0x5143 || getenv("PYROWAVE_FRAGMENT_CONVERT");
        if (c->storage_on_ahb && prefer_fragment_convert && !getenv("PYROWAVE_CONVERT_COMPUTE")) {
            fi.usage |= VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT;
            if (vkGetPhysicalDeviceImageFormatProperties2(c->gpu, &fi, &p2) == VK_SUCCESS &&
                (ep.externalMemoryProperties.externalMemoryFeatures & VK_EXTERNAL_MEMORY_FEATURE_IMPORTABLE_BIT)) {
                c->fragment_convert = c->create_fragment_convert();
            }
        }
        LOGI("RGBA conversion: %s", c->fragment_convert ? "fragment" : "compute fallback");
    }
    if (!c->storage_on_ahb) {
        if (!create_plain_image(c->gpu, c->device, VK_FORMAT_R8G8B8A8_UNORM, width, height,
                                VK_IMAGE_USAGE_STORAGE_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT, c->scratch)) { c->destroy(); delete c; return nullptr; }
        VkDescriptorSetAllocateInfo sa = { VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO };
        sa.descriptorPool = c->desc_pool; sa.descriptorSetCount = 1; sa.pSetLayouts = &c->set_layout;
        if (vkAllocateDescriptorSets(c->device, &sa, &c->scratch_set) != VK_SUCCESS) { c->destroy(); delete c; return nullptr; }
        write_set(c->device, c->sampler, c->planes, c->scratch.view, c->scratch_set);
    }
    for (Slot &s : c->ring)
        if (!c->create_slot(s)) { c->destroy(); delete c; return nullptr; }
    LOGI("ready: %ux%u %s %s range, ring %zu", width, height, chroma444 ? "4:4:4" : "4:2:0", full_range ? "full" : "limited", c->ring.size());
    return c;
}

extern "C" int pyroclient_push_packet(pyroclient *c, const void *data, size_t size) {
    if (!c || !data || !size) return -1;
    pyrowave_result r = pyrowave_decoder_push_packet(c->decoder, data, size);
    if (r != PYROWAVE_SUCCESS) return -2;
    return pyrowave_decoder_decode_is_ready(c->decoder, false) ? 1 : 0;
}

extern "C" int pyroclient_is_ready(pyroclient *c, int allow_partial) {
    return c && pyrowave_decoder_decode_is_ready(c->decoder, allow_partial != 0) ? 1 : 0;
}

extern "C" int pyroclient_decode(pyroclient *c, AHardwareBuffer **out, pyroclient_frame_info *info) {
    if (!c || !out) return -1;
    *out = nullptr;
    // Partial reconstruction requires pristine low-frequency bands. The old UDP caller
    // decoded arbitrary packet subsets, which can make the entire picture disappear.
    // Both transports now require a fully validated frame before recording GPU work.
    if (!pyrowave_decoder_decode_is_ready(c->decoder, false)) return -3;
    if (info) { *info = pyroclient_frame_info{}; info->complete = pyrowave_decoder_decode_is_ready(c->decoder, false) ? 1 : 0; }
    Slot &s = c->ring[c->next_slot];
    c->next_slot = (c->next_slot + 1) % (uint32_t)c->ring.size();
    if (!c->record_and_submit(s, info)) return -2;
    *out = s.ahb;
    return 0;
}

extern "C" void pyroclient_clear(pyroclient *c) { if (c) pyrowave_decoder_clear(c->decoder); }

extern "C" void pyroclient_destroy(pyroclient *c) { if (!c) return; c->destroy(); delete c; }
