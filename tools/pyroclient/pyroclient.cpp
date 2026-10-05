#include <algorithm>
// See pyroclient.h. Ported from tools/pyrowave_android/main.cpp, which proved every step here on
// the Adreno 740; the harness stays as the record and the scoring tool.
#include "pyroclient.h"
#include "decode_path.h"
#include "release_fence_registry.h"
#include "prerecord_state.h"
#include <thread>
#include <utility>

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
#include <unistd.h>
#include <poll.h>
#include <cerrno>

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

ReleaseFenceRegistry release_registry([](int fd) { close(fd); });

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
    VkSemaphore released = VK_NULL_HANDLE;
    bool release_registered = false;
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
    bool haar = false;
    bool legall53 = false;      // Experiment 2: CDF 5/3 instead of 9/7 (compute path only)
    int decode_path_hint = 0;   // dashboard setting: 0 auto, 1 fragment, 2 compute
    bool low_queue_priority = false;
    bool decode_stage_probe = false; // Existing Granite timestamps; diagnostic only.
    uint64_t stage_probe_completions = 0;
    int32_t chroma_filter = 0; // 1 = Catmull-Rom chroma upsample

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
    VkDeviceQueueGlobalPriorityCreateInfoKHR queue_global_priority{};
    VkDeviceQueueCreateInfo queue_info{};
    VkPhysicalDeviceVulkan13Features f13{};
    VkPhysicalDeviceVulkan12Features f12{};
    VkPhysicalDeviceVulkan11Features f11{};
    VkPhysicalDeviceFeatures2 f2{};
    VkDeviceCreateInfo device_info{};
    std::vector<const char *> device_extensions = {
        VK_ANDROID_EXTERNAL_MEMORY_ANDROID_HARDWARE_BUFFER_EXTENSION_NAME,
        VK_EXT_QUEUE_FAMILY_FOREIGN_EXTENSION_NAME,
    };
    bool release_fences = false;
    bool release_poisoned = false;
    uint64_t release_imports = 0;
    PFN_vkImportSemaphoreFdKHR import_semaphore_fd = nullptr;
    bool ready_fences = false;
    PFN_vkGetSemaphoreFdKHR get_semaphore_fd = nullptr;
    VkSemaphore ready_semaphore = VK_NULL_HANDLE;
    bool pending_submission = false;
    std::chrono::steady_clock::time_point pending_begin, pending_submit;

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
    bool fragment_min_usage = false; // optional sampled/color-only imported output
    uint64_t optimal_ahb_usage = 0; // driver recommendation; valid only for minimal usage
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
    // Standalone prototype only. ALVR does not call enable or these APIs yet.
    bool prerecord_enabled = false;
    std::thread::id prerecord_owner;
    q3pw::PrerecordState prerecord;
    VkCommandBuffer spare_cmd = VK_NULL_HANDLE;
    bool prepared_first_use = false, prepared_planes_initialised = false;
    std::chrono::steady_clock::time_point prepared_begin, prepared_end, pending_record_done;
    double prepared_queue_ms = 0;
    bool prerecord_owned() const { return prerecord_enabled && prerecord_owner == std::this_thread::get_id(); }
    std::array<bool, 3> protected_slots(AHardwareBuffer *a, AHardwareBuffer *b) const {
        return {ring[0].ahb == a || ring[0].ahb == b, ring[1].ahb == a || ring[1].ahb == b,
                ring[2].ahb == a || ring[2].ahb == b};
    }
    bool record_commands(Slot &s);
    bool submit_recorded(Slot &s, bool wait_released, pyroclient_frame_info *info, int *ready_fd,
                         std::chrono::steady_clock::time_point begin,
                         std::chrono::steady_clock::time_point recorded, bool defer_finish);
    bool abandon_prepared(uint64_t generation);

    bool create_device();
    bool create_planes();
    bool create_convert();
    bool create_fragment_convert();
    bool create_slot(Slot &s);
    bool record_and_submit(Slot &s, pyroclient_frame_info *info, int *ready_fd = nullptr, bool defer_finish = false);
    bool finish_pending(pyroclient_frame_info *info);
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
    char release_prop[PROP_VALUE_MAX] = {};
    const bool release_requested = __system_property_get("debug.q3pw.release_fd", release_prop) > 0 && !strcmp(release_prop, "1");
    char ready_prop[PROP_VALUE_MAX] = {};
    const bool ready_requested = __system_property_get("debug.q3pw.ready_fd", ready_prop) > 0 && !strcmp(ready_prop, "1");
    if (release_requested || ready_requested) {
        uint32_t count = 0;
        vkEnumerateDeviceExtensionProperties(gpu, nullptr, &count, nullptr);
        std::vector<VkExtensionProperties> extensions(count);
        vkEnumerateDeviceExtensionProperties(gpu, nullptr, &count, extensions.data());
        for (const auto &extension : extensions) {
            if (!strcmp(extension.extensionName, VK_KHR_EXTERNAL_SEMAPHORE_FD_EXTENSION_NAME)) {
                VkPhysicalDeviceExternalSemaphoreInfo external = { VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_EXTERNAL_SEMAPHORE_INFO };
                external.handleType = VK_EXTERNAL_SEMAPHORE_HANDLE_TYPE_SYNC_FD_BIT;
                VkExternalSemaphoreProperties props = { VK_STRUCTURE_TYPE_EXTERNAL_SEMAPHORE_PROPERTIES };
                vkGetPhysicalDeviceExternalSemaphoreProperties(gpu, &external, &props);
                release_fences = release_requested && (props.externalSemaphoreFeatures & VK_EXTERNAL_SEMAPHORE_FEATURE_IMPORTABLE_BIT) != 0;
                ready_fences = ready_requested && (props.externalSemaphoreFeatures & VK_EXTERNAL_SEMAPHORE_FEATURE_EXPORTABLE_BIT) != 0;
                if (release_fences || ready_fences) device_extensions.push_back(VK_KHR_EXTERNAL_SEMAPHORE_FD_EXTENSION_NAME);
                break;
            }
        }
        if (release_requested) LOGI("[Q3PW_RELEASE_FD] Vulkan requested=1 importable=%d", release_fences);
        if (ready_requested) LOGI("[Q3PW_READY_FD] Vulkan requested=1 exportable=%d max_inflight=1", ready_fences);
    }
    // A LOW global-priority decode queue lets the medium-priority GLES eye copy
    // preempt decode instead of queueing behind it.
    const bool low_priority_requested = low_queue_priority;
    const char *priority_extension = nullptr;
    if (low_priority_requested) {
        uint32_t count = 0;
        vkEnumerateDeviceExtensionProperties(gpu, nullptr, &count, nullptr);
        std::vector<VkExtensionProperties> extensions(count);
        vkEnumerateDeviceExtensionProperties(gpu, nullptr, &count, extensions.data());
        for (const auto &extension : extensions) {
            if (!strcmp(extension.extensionName, VK_KHR_GLOBAL_PRIORITY_EXTENSION_NAME)) { priority_extension = VK_KHR_GLOBAL_PRIORITY_EXTENSION_NAME; break; }
            if (!strcmp(extension.extensionName, VK_EXT_GLOBAL_PRIORITY_EXTENSION_NAME)) priority_extension = VK_EXT_GLOBAL_PRIORITY_EXTENSION_NAME;
        }
    }
    if (priority_extension) {
        queue_global_priority = { VK_STRUCTURE_TYPE_DEVICE_QUEUE_GLOBAL_PRIORITY_CREATE_INFO_KHR };
        queue_global_priority.globalPriority = VK_QUEUE_GLOBAL_PRIORITY_LOW_KHR;
        queue_info.pNext = &queue_global_priority;
        device_extensions.push_back(priority_extension);
    }
    device_info.enabledExtensionCount = uint32_t(device_extensions.size());
    device_info.ppEnabledExtensionNames = device_extensions.data();
    VkResult created = vkCreateDevice(gpu, &device_info, nullptr, &device);
    if (created != VK_SUCCESS && priority_extension) {
        LOGE("[Q3PW_PRIORITY] low-priority decode queue rejected (%d); default priority", created);
        queue_info.pNext = nullptr;
        device_extensions.pop_back();
        device_info.enabledExtensionCount = uint32_t(device_extensions.size());
        device_info.ppEnabledExtensionNames = device_extensions.data();
        priority_extension = nullptr;
        created = vkCreateDevice(gpu, &device_info, nullptr, &device);
    }
    VK_TRY(created);
    LOGI("[Q3PW_PRIORITY] decode queue requested=%s applied=%d extension=%s", low_priority_requested ? "low" : "default",
         priority_extension != nullptr, priority_extension ? priority_extension : "none");
    vkGetDeviceQueue(device, family, 0, &queue);
    if (release_fences) {
        import_semaphore_fd = reinterpret_cast<PFN_vkImportSemaphoreFdKHR>(vkGetDeviceProcAddr(device, "vkImportSemaphoreFdKHR"));
        if (!import_semaphore_fd) { release_fences = false; LOGE("[Q3PW_RELEASE_FD] Vulkan import entry point unavailable"); }
    }
    if (ready_fences) {
        get_semaphore_fd = reinterpret_cast<PFN_vkGetSemaphoreFdKHR>(vkGetDeviceProcAddr(device, "vkGetSemaphoreFdKHR"));
        if (!get_semaphore_fd) ready_fences = false;
        else {
            VkExportSemaphoreCreateInfo exported = { VK_STRUCTURE_TYPE_EXPORT_SEMAPHORE_CREATE_INFO };
            exported.handleTypes = VK_EXTERNAL_SEMAPHORE_HANDLE_TYPE_SYNC_FD_BIT;
            VkSemaphoreCreateInfo ci = { VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO };
            ci.pNext = &exported;
            if (vkCreateSemaphore(device, &ci, nullptr, &ready_semaphore) != VK_SUCCESS) {
                ready_fences = false;
                LOGE("[Q3PW_READY_FD] semaphore creation failed; synchronous fallback");
            }
        }
    }

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
            has_prop ? prop : nullptr, legall53 || haar);
        fragment_path = choice.fragment;
        forced = choice.reason;
    }
    // The queue type must match the path, or the work is recorded against the wrong queue.
    PW_TRY(pyrowave_device_set_queue_type(pyro, fragment_path ? VK_QUEUE_GRAPHICS_BIT : VK_QUEUE_COMPUTE_BIT));
    LOGI("pyrowave device (borrowed), %s path", fragment_path ? "fragment" : "compute");
    LOGI("decode path %s (%s)", fragment_path ? "fragment" : "compute", forced);
    LOGI("wavelet %s", haar ? "Haar" : legall53 ? "CDF 5/3" : "CDF 9/7");

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
    di.wavelet = haar ? PYROWAVE_WAVELET_HAAR : legall53 ? PYROWAVE_WAVELET_CDF53 : PYROWAVE_WAVELET_CDF97;
    PW_TRY(pyrowave_decoder_create(&di, &decoder));
    // Experiment: decode the level-0 luma bands inside the final Haar pass. The decoder refuses,
    // and keeps the separate passes, for anything but Haar 4:2:0 compute at precision 1.
    char dequant_haar_prop[PROP_VALUE_MAX] = {};
    if (__system_property_get("debug.q3pw.dequant_haar", dequant_haar_prop) > 0 && !strcmp(dequant_haar_prop, "1")) {
        const bool applied = pyrowave_decoder_set_fused_dequant_haar(decoder, 1) == PYROWAVE_SUCCESS;
        LOGI("[Q3PW_DEQUANT_HAAR] requested=1 applied=%d", applied ? 1 : 0);
    }
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
    VkPushConstantRange pc = { VK_SHADER_STAGE_COMPUTE_BIT | VK_SHADER_STAGE_FRAGMENT_BIT, 0, sizeof(int32_t) * 2 };
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
                      VkDescriptorSet set, bool storage_output = true) {
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
    // The fragment shader only reads bindings 0..2; its output is a color attachment.
    // Do not write an unused storage descriptor for an image without STORAGE usage.
    vkUpdateDescriptorSets(device, storage_output ? 4 : 3, w, 0, nullptr);
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
    if (fragment_min_usage && optimal_ahb_usage) d.usage |= optimal_ahb_usage;
    int allocated = AHardwareBuffer_allocate(&d, &s.ahb);
    if (allocated != 0 && optimal_ahb_usage) {
        LOGI("[Q3PW_AHB_USAGE] recommended allocation failed=%d; retry standard flags", allocated);
        optimal_ahb_usage = 0;
        d.usage = AHARDWAREBUFFER_USAGE_GPU_SAMPLED_IMAGE | AHARDWAREBUFFER_USAGE_GPU_COLOR_OUTPUT;
        allocated = AHardwareBuffer_allocate(&d, &s.ahb);
    }
    if (allocated != 0 || !s.ahb) { LOGE("AHardwareBuffer_allocate %ux%u", width, height); return false; }
    AHardwareBuffer_Desc actual = {};
    AHardwareBuffer_describe(s.ahb, &actual);
    LOGI("[Q3PW_AHB_USAGE] allocated=0x%llx recommended=0x%llx", (unsigned long long)actual.usage,
         (unsigned long long)optimal_ahb_usage);

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
    const VkImageUsageFlags legacy_usage = VK_IMAGE_USAGE_SAMPLED_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT
        | (storage_on_ahb ? VK_IMAGE_USAGE_STORAGE_BIT : 0)
        | (fragment_convert ? VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT : 0);
    ii.usage = fragment_min_usage
        ? VK_IMAGE_USAGE_SAMPLED_BIT | VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT : legacy_usage;
    ii.sharingMode = VK_SHARING_MODE_EXCLUSIVE; ii.initialLayout = VK_IMAGE_LAYOUT_UNDEFINED;
    VkResult image_result = vkCreateImage(device, &ii, nullptr, &s.image);
    if (image_result != VK_SUCCESS && fragment_min_usage) {
        LOGI("[Q3PW_FRAGMENT_USAGE] minimal create failed=%d; retry legacy", int(image_result));
        fragment_min_usage = false;
        if (optimal_ahb_usage) {
            // Vendor-recommended allocations cannot be repurposed for broader
            // image usage. No VkImage/memory exists yet: reallocate a standard
            // AHB before retrying once with the legacy image parameters.
            optimal_ahb_usage = 0;
            AHardwareBuffer_release(s.ahb);
            s.ahb = nullptr;
            s.image = VK_NULL_HANDLE;
            return create_slot(s);
        }
        ii.usage = legacy_usage;
        image_result = vkCreateImage(device, &ii, nullptr, &s.image);
    }
    if (image_result != VK_SUCCESS) { LOGE("vkCreateImage output failed: %d", int(image_result)); return false; }
    LOGI("[Q3PW_FRAGMENT_USAGE] slot usage=0x%x minimal=%d", unsigned(ii.usage), fragment_min_usage);

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
        write_set(device, sampler, planes, s.view, s.set, !fragment_convert);
    }
    if (release_fences) {
        VkSemaphoreCreateInfo semaphore = { VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO };
        VK_TRY(vkCreateSemaphore(device, &semaphore, nullptr, &s.released));
        s.release_registered = release_registry.add(s.ahb);
        if (!s.release_registered) { LOGE("release registry duplicate buffer"); return false; }
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

bool pyroclient::record_and_submit(Slot &s, pyroclient_frame_info *info, int *ready_fd, bool defer_finish) {
    if (release_poisoned || pending_submission) return false;
    const auto t0 = std::chrono::steady_clock::now();
    bool wait_released = false;
    if (s.release_registered) {
        int fd = release_registry.begin_reuse(s.ahb);
        if (fd >= 0) {
            VkImportSemaphoreFdInfoKHR imported = { VK_STRUCTURE_TYPE_IMPORT_SEMAPHORE_FD_INFO_KHR };
            imported.semaphore = s.released;
            imported.flags = VK_SEMAPHORE_IMPORT_TEMPORARY_BIT;
            imported.handleType = VK_EXTERNAL_SEMAPHORE_HANDLE_TYPE_SYNC_FD_BIT;
            imported.fd = fd;
            if (import_semaphore_fd(device, &imported) == VK_SUCCESS) {
                wait_released = true; // Vulkan owns fd now; temporary payload resets after wait.
                if (++release_imports <= 3 || release_imports % 120 == 0)
                    LOGI("[Q3PW_RELEASE_FD] Vulkan imports=%llu gpu_wait=1", (unsigned long long)release_imports);
            } else {
                // Import failure must never turn an unfinished consumer into a free slot.
                const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(1);
                pollfd p = { fd, POLLIN, 0 };
                int result;
                do { result = poll(&p, 1, 10); }
                while ((result == 0 || (result < 0 && errno == EINTR)) && std::chrono::steady_clock::now() < deadline);
                bool ready = result > 0 && (p.revents & POLLIN) && !(p.revents & (POLLERR | POLLNVAL));
                close(fd);
                LOGE("[Q3PW_RELEASE_FD] import failure CPU fallback ready=%d", ready);
                if (!ready) { release_poisoned = true; return false; }
            }
        }
    }
    VK_TRY(vkResetFences(device, 1, &fence));
    if (!record_commands(s)) return false;
    return submit_recorded(s, wait_released, info, ready_fd, t0, std::chrono::steady_clock::now(), defer_finish);
}

bool pyroclient::record_commands(Slot &s) {
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

    const int32_t convert_params[2] = { full_range ? 0 : 1, chroma_filter };
    if (fragment_convert) {
        VkRenderPassBeginInfo begin = { VK_STRUCTURE_TYPE_RENDER_PASS_BEGIN_INFO };
        begin.renderPass = convert_render_pass; begin.framebuffer = s.framebuffer;
        begin.renderArea.extent = {width, height};
        vkCmdBeginRenderPass(cmd, &begin, VK_SUBPASS_CONTENTS_INLINE);
        vkCmdBindPipeline(cmd, VK_PIPELINE_BIND_POINT_GRAPHICS, fragment_pipeline);
        vkCmdPushConstants(cmd, pipeline_layout, VK_SHADER_STAGE_COMPUTE_BIT | VK_SHADER_STAGE_FRAGMENT_BIT, 0, sizeof convert_params, convert_params);
        vkCmdBindDescriptorSets(cmd, VK_PIPELINE_BIND_POINT_GRAPHICS, pipeline_layout, 0, 1, &s.set, 0, nullptr);
        vkCmdDraw(cmd, 3, 1, 0, 0);
        vkCmdEndRenderPass(cmd);
    } else if (storage_on_ahb) {
        vkCmdBindPipeline(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, pipeline);
        vkCmdPushConstants(cmd, pipeline_layout, VK_SHADER_STAGE_COMPUTE_BIT | VK_SHADER_STAGE_FRAGMENT_BIT, 0, sizeof convert_params, convert_params);
        vkCmdBindDescriptorSets(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, pipeline_layout, 0, 1, &s.set, 0, nullptr);
        vkCmdDispatch(cmd, (width + 7) / 8, (height + 7) / 8, 1);
    } else {
        image_barrier(cmd, scratch.image, VK_IMAGE_LAYOUT_UNDEFINED, VK_IMAGE_LAYOUT_GENERAL, 0,
                      VK_ACCESS_SHADER_WRITE_BIT, VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT);
        vkCmdBindPipeline(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, pipeline);
        vkCmdPushConstants(cmd, pipeline_layout, VK_SHADER_STAGE_COMPUTE_BIT | VK_SHADER_STAGE_FRAGMENT_BIT, 0, sizeof convert_params, convert_params);
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
    return true;
}

bool pyroclient::submit_recorded(Slot &s, bool wait_released, pyroclient_frame_info *info, int *ready_fd,
                                std::chrono::steady_clock::time_point t0,
                                std::chrono::steady_clock::time_point recorded, bool defer_finish) {
    if (pending_submission || release_poisoned) return false;
    VkSubmitInfo si = { VK_STRUCTURE_TYPE_SUBMIT_INFO };
    // Wait before the FOREIGN acquire barrier as well as any writes. Preserve
    // the existing image-family/layout transitions; a semaphore does not replace them.
    VkPipelineStageFlags wait_stage = VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT;
    if (wait_released) {
        si.waitSemaphoreCount = 1; si.pWaitSemaphores = &s.released;
        si.pWaitDstStageMask = &wait_stage;
    }
    si.commandBufferCount = 1; si.pCommandBuffers = &cmd;
    const bool early = ready_fd && ready_fences;
    if (early) { si.signalSemaphoreCount = 1; si.pSignalSemaphores = &ready_semaphore; }
    const auto t_submit = std::chrono::steady_clock::now();
    VK_TRY(vkQueueSubmit(queue, 1, &si, fence));
    pending_submission = true;
    pending_begin = t0; pending_submit = t_submit;
    // Legacy metrics keep their existing boundary. Prototype reports queue delay
    // separately and total still includes it; it cannot be called a decode gain.
    pending_record_done = prerecord_enabled ? recorded : t_submit;
    prepared_queue_ms = prerecord_enabled ? std::chrono::duration<double, std::milli>(t_submit - recorded).count() : 0;
    if (defer_finish) {
        if (info) info->record_ms = std::chrono::duration<double, std::milli>(recorded - t0).count();
        return true; // No output publication/completion until finish_pending.
    }
    if (early) {
        VkSemaphoreGetFdInfoKHR exported = { VK_STRUCTURE_TYPE_SEMAPHORE_GET_FD_INFO_KHR };
        exported.semaphore = ready_semaphore;
        exported.handleType = VK_EXTERNAL_SEMAPHORE_HANDLE_TYPE_SYNC_FD_BIT;
        int fd = -1;
        const VkResult export_result = get_semaphore_fd(device, &exported, &fd);
        if (export_result == VK_SUCCESS && fd >= 0) {
            *ready_fd = fd;
            if (info) info->record_ms = std::chrono::duration<double, std::milli>(t_submit - t0).count();
            return true; // Packet-complete; GPU completion must NOT be reported yet.
        }
        // SYNC_FD -1 on success represents an already-signaled payload. The
        // export still reset the semaphore; drain the CPU fence for legacy ABI.
        if (export_result == VK_SUCCESS && fd == -1) return finish_pending(info);
        if (fd >= 0) close(fd);
        LOGE("[Q3PW_READY_FD] export failed; draining before synchronous fallback");
        // An unexported signaled binary semaphore cannot be signaled again.
        ready_fences = false;
    }
    return finish_pending(info);
}

bool pyroclient::finish_pending(pyroclient_frame_info *info) {
    if (prerecord_enabled && (!prerecord_owned() || !info)) return false;
    if (release_poisoned) return false;
    if (!pending_submission) return true;
    const VkResult completed = vkWaitForFences(device, 1, &fence, VK_TRUE, 1000ull * 1000 * 1000);
    if (completed != VK_SUCCESS) {
        // A timeout is not completion: never reset an outstanding command/fence
        // or recycle shared Granite staging/planes after this failure.
        release_poisoned = true;
        if (prerecord_enabled) prerecord.poisoned = true;
        LOGE("[Q3PW_READY_FD] completion failed=%d; decoder poisoned", int(completed));
        return false;
    }
    const auto t_fence = std::chrono::steady_clock::now();
    if (info) {
        info->record_ms = std::chrono::duration<double, std::milli>(pending_record_done - pending_begin).count();
        info->wait_ms = std::chrono::duration<double, std::milli>(t_fence - pending_submit).count();
        uint64_t t[3] = {};
        if (vkGetQueryPoolResults(device, queries, 0, 3, sizeof t, t, sizeof(uint64_t),
                                  VK_QUERY_RESULT_64_BIT | VK_QUERY_RESULT_WAIT_BIT) == VK_SUCCESS) {
            info->decode_ms = double(t[1] - t[0]) * ns_per_tick / 1e6;
            info->convert_ms = double(t[2] - t[1]) * ns_per_tick / 1e6;
        } else if (prerecord_enabled) {
            release_poisoned = true;
            prerecord.poisoned = true;
            LOGE("[Q3PW_PRERECORD] completion query collection failed; stopped");
            return false;
        }
        info->total_ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - pending_begin).count();
    }
    pending_submission = false;
    if (prerecord_enabled && !prerecord.complete_active(true)) {
        release_poisoned = true; return false;
    }
    // Reporting reads already-collected Granite frame-context intervals; it
    // does not add GPU queries or claim per-frame percentiles. Only call after
    // completion, on this decoder's worker. First interval includes startup.
    if (decode_stage_probe && ++stage_probe_completions % 120 == 0) {
        pyrowave_device_report_performance_stats(pyro, [](void *userdata, const char *message) {
            const auto *client = static_cast<const pyroclient *>(userdata);
            if (!strncmp(message, "Dequant:", 8) || !strncmp(message, "iDWT:", 5) ||
                !strncmp(message, "iDWT fragment:", 14)) {
                LOGI("[Q3PW_DECODE_STAGE] complete=%llu %s",
                     (unsigned long long)client->stage_probe_completions, message);
            }
        }, this, true);
    }
    return true;
}

void pyroclient::destroy() {
    if (device) vkDeviceWaitIdle(device);
    // B has never been submitted. Reset its borrowed command before Granite can
    // advance/destruct its retained staging context. No unexecuted Granite GPU
    // queries exist: the prototype disabled those before the first recording.
    if (prerecord_enabled && prerecord.prepared.slot < 3 && spare_cmd) {
        vkResetCommandBuffer(spare_cmd, 0);
    }
    if (ready_semaphore) vkDestroySemaphore(device, ready_semaphore, nullptr);
    if (decoder) pyrowave_decoder_destroy(decoder);
    for (Slot &s : ring) {
        if (s.release_registered) release_registry.remove(s.ahb);
        if (s.released) vkDestroySemaphore(device, s.released, nullptr);
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

static_assert(sizeof(pyroclient_frame_info) == 48, "native frame timing ABI size");
static_assert(offsetof(pyroclient_frame_info, record_ms) == 32, "native timing ABI offset");

// ---- C API ----

extern "C" pyroclient *pyroclient_create(uint32_t width, uint32_t height, int chroma444, int full_range, uint32_t ring_size, int wavelet) {
    return pyroclient_create_ex(width, height, chroma444, full_range, ring_size, wavelet, 0);
}

extern "C" pyroclient *pyroclient_create_ex(uint32_t width, uint32_t height, int chroma444, int full_range, uint32_t ring_size, int wavelet, int decode_path) {
    return pyroclient_create_prioritized(width, height, chroma444, full_range, ring_size, wavelet, decode_path, 0);
}

extern "C" pyroclient *pyroclient_create_prioritized(uint32_t width, uint32_t height, int chroma444, int full_range, uint32_t ring_size, int wavelet, int decode_path, int low_queue_priority) {
    if (!width || !height || (!chroma444 && ((width | height) & 1))) { LOGE("bad geometry %ux%u", width, height); return nullptr; }
    if (wavelet != 97 && wavelet != 53 && wavelet != 2) { LOGE("bad wavelet %d (97, 53 or 2=Haar)", wavelet); return nullptr; }
    char pairs_prop[PROP_VALUE_MAX] = {};
    if (__system_property_get("debug.q3pw.haar_pairs", pairs_prop) > 0) {
        const bool allowed = !strcmp(pairs_prop, "64-column") || !strcmp(pairs_prop, "64-row") || !strcmp(pairs_prop, "128-row");
        setenv("PYROWAVE_HAAR_PAIRS", allowed ? pairs_prop : "0", 1);
    }
    LOGI("[Q3PW_HAAR_PAIRS] mode=%s default_off=true",
         getenv("PYROWAVE_HAAR_PAIRS") ? getenv("PYROWAVE_HAAR_PAIRS") : "0");
    char fused_prop[PROP_VALUE_MAX] = {};
    if (__system_property_get("debug.q3pw.haar_fused", fused_prop) > 0)
        setenv("PYROWAVE_FUSED_HAAR", !strcmp(fused_prop, "1") ? "1" : "0", 1);
    LOGI("optional multilevel Haar: %s", wavelet == 2 && getenv("PYROWAVE_FUSED_HAAR") &&
         !strcmp(getenv("PYROWAVE_FUSED_HAAR"), "1") ? "enabled" : "disabled");
    char batch_prop[PROP_VALUE_MAX] = {};
    if (__system_property_get("debug.q3pw.dequant_batch", batch_prop) > 0)
        setenv("PYROWAVE_BATCH_DEQUANT", !strcmp(batch_prop, "1") ? "1" : "0", 1);
    LOGI("optional batched dequant: %s", getenv("PYROWAVE_BATCH_DEQUANT") &&
         !strcmp(getenv("PYROWAVE_BATCH_DEQUANT"), "1") ? "enabled" : "disabled");
    char convert_prop[PROP_VALUE_MAX] = {};
    if (__system_property_get("debug.q3pw.convert_compute", convert_prop) > 0) {
        if (!strcmp(convert_prop, "1")) setenv("PYROWAVE_CONVERT_COMPUTE", "1", 1);
        else unsetenv("PYROWAVE_CONVERT_COMPUTE");
    }
    pyroclient *c = new pyroclient();
    c->width = width; c->height = height; c->chroma444 = chroma444 != 0; c->full_range = full_range != 0;
    c->legall53 = wavelet == 53;
    c->haar = wavelet == 2;
    c->decode_path_hint = decode_path;
    c->low_queue_priority = low_queue_priority != 0;
    char stage_prop[PROP_VALUE_MAX] = {};
    __system_property_get("debug.q3pw.decode_stages", stage_prop);
    c->decode_stage_probe = !strcmp(stage_prop, "1");
    LOGI("[Q3PW_DECODE_STAGE_SETUP] enabled=%d interval_decodes=120", c->decode_stage_probe);
    char chroma_prop[PROP_VALUE_MAX] = {};
    __system_property_get("debug.q3pw.chroma_filter", chroma_prop);
    c->chroma_filter = !strcmp(chroma_prop, "catmull") ? 1 : 0;
    LOGI("[Q3PW_CHROMA_FILTER] requested=%s applied=%d", chroma_prop[0] ? chroma_prop : "unset", c->chroma_filter);
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
        char minimal_prop[PROP_VALUE_MAX] = {};
        const bool requested = __system_property_get("debug.q3pw.fragment_min_usage", minimal_prop) > 0
            && !strcmp(minimal_prop, "1");
        if (requested && c->fragment_convert) {
            fi.usage = VK_IMAGE_USAGE_SAMPLED_BIT | VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT;
            VkAndroidHardwareBufferUsageANDROID recommended = { VK_STRUCTURE_TYPE_ANDROID_HARDWARE_BUFFER_USAGE_ANDROID };
            ep.pNext = &recommended;
            const VkResult supported = vkGetPhysicalDeviceImageFormatProperties2(c->gpu, &fi, &p2);
            c->fragment_min_usage = supported == VK_SUCCESS &&
                (ep.externalMemoryProperties.externalMemoryFeatures & VK_EXTERNAL_MEMORY_FEATURE_IMPORTABLE_BIT) &&
                p2.imageFormatProperties.maxExtent.width >= width &&
                p2.imageFormatProperties.maxExtent.height >= height &&
                (p2.imageFormatProperties.sampleCounts & VK_SAMPLE_COUNT_1_BIT);
            char optimal_prop[PROP_VALUE_MAX] = {};
            const bool optimal_requested = __system_property_get("debug.q3pw.optimal_ahb_usage", optimal_prop) > 0
                && !strcmp(optimal_prop, "1");
            const uint64_t required = AHARDWAREBUFFER_USAGE_GPU_SAMPLED_IMAGE | AHARDWAREBUFFER_USAGE_GPU_COLOR_OUTPUT;
            if (optimal_requested && c->fragment_min_usage &&
                (recommended.androidHardwareBufferUsage & required) == required)
                c->optimal_ahb_usage = recommended.androidHardwareBufferUsage;
            LOGI("[Q3PW_AHB_USAGE] requested=%d recommendation=0x%llx active=%d", optimal_requested,
                 (unsigned long long)recommended.androidHardwareBufferUsage, c->optimal_ahb_usage != 0);
            ep.pNext = nullptr;
        }
        LOGI("[Q3PW_FRAGMENT_USAGE] requested=%d supported=%d fragment=%d", requested,
             c->fragment_min_usage, c->fragment_convert);
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
    if (c->pending_submission || c->release_poisoned || c->prerecord_enabled) return -3;
    pyrowave_result r = pyrowave_decoder_push_packet(c->decoder, data, size);
    if (r != PYROWAVE_SUCCESS) return -2;
    return pyrowave_decoder_decode_is_ready(c->decoder, false) ? 1 : 0;
}

extern "C" int pyroclient_is_ready(pyroclient *c, int allow_partial) {
    return c && pyrowave_decoder_decode_is_ready(c->decoder, allow_partial != 0) ? 1 : 0;
}

extern "C" int pyroclient_decode(pyroclient *c, AHardwareBuffer **out, pyroclient_frame_info *info) {
    return pyroclient_decode_guarded(c, out, info, nullptr, nullptr);
}

extern "C" int pyroclient_decode_guarded(pyroclient *c, AHardwareBuffer **out, pyroclient_frame_info *info,
                                       AHardwareBuffer *protected_a, AHardwareBuffer *protected_b) {
    return pyroclient_submit_guarded(c, out, info, protected_a, protected_b, nullptr);
}

extern "C" int pyroclient_submit_guarded(pyroclient *c, AHardwareBuffer **out, pyroclient_frame_info *info,
                                       AHardwareBuffer *protected_a, AHardwareBuffer *protected_b, int *ready_fd) {
    if (ready_fd) *ready_fd = -1;
    if (!c || !out) return -1;
    *out = nullptr;
    if (c->pending_submission || c->release_poisoned || c->prerecord_enabled) return -6;
    // Partial reconstruction requires pristine low-frequency bands. The old UDP caller
    // decoded arbitrary packet subsets, which can make the entire picture disappear.
    // Both transports now require a fully validated frame before recording GPU work.
    if (!pyrowave_decoder_decode_is_ready(c->decoder, false)) return -3;
    if (info) { *info = pyroclient_frame_info{}; info->complete = pyrowave_decoder_decode_is_ready(c->decoder, false) ? 1 : 0; }
    uint32_t attempts = 0;
    while (c->ring[c->next_slot].ahb == protected_a || c->ring[c->next_slot].ahb == protected_b) {
        c->next_slot = (c->next_slot + 1) % (uint32_t)c->ring.size();
        if (++attempts == c->ring.size()) return -4;
    }
    Slot &s = c->ring[c->next_slot];
    c->next_slot = (c->next_slot + 1) % (uint32_t)c->ring.size();
    if (!c->record_and_submit(s, info, ready_fd)) {
        c->release_poisoned = true;
        return -2;
    }
    if (s.release_registered && !release_registry.publish(s.ahb)) {
        if (ready_fd && *ready_fd >= 0) { close(*ready_fd); *ready_fd = -1; }
        c->release_poisoned = true; return -5;
    }
    *out = s.ahb;
    return 0;
}

extern "C" int pyroclient_finish_pending(pyroclient *c, pyroclient_frame_info *info) {
    return c && c->finish_pending(info) ? 0 : -1;
}
extern "C" void pyroclient_clear(pyroclient *c) {
    if (c && !c->pending_submission && !c->release_poisoned && !c->prerecord_enabled) pyrowave_decoder_clear(c->decoder);
}

extern "C" uint64_t pyroclient_output_release_token(AHardwareBuffer *buffer) {
    return release_registry.token(buffer);
}
extern "C" int pyroclient_attach_release_fd(AHardwareBuffer *buffer, uint64_t token, int fd) {
    return release_registry.attach(buffer, token, fd) ? 1 : 0;
}

extern "C" void pyroclient_destroy(pyroclient *c) { if (!c) return; c->destroy(); delete c; }


// Explicit standalone diagnostic APIs. None are invoked by the installed ALVR
// path. Single producer owner, no early publication, exactly three output slots.
extern "C" int pyroclient_prerecord_enable(pyroclient *c) {
    if (!c || c->pending_submission || c->release_poisoned || c->prerecord_enabled ||
        !c->planes_initialised || !std::all_of(c->ring.begin(), c->ring.end(), [](const Slot &s) { return !s.first_use; }) ||
        !c->haar || c->chroma444 || c->fragment_path ||
        c->ready_fences || c->release_fences || c->decode_stage_probe || c->ring.size()!=3 ||
        !getenv("PYROWAVE_NO_LINEAR_TEX") || strcmp(getenv("PYROWAVE_NO_LINEAR_TEX"), "1")) return -1;
    VkCommandBufferAllocateInfo ca = {VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO};
    ca.commandPool=c->pool; ca.level=VK_COMMAND_BUFFER_LEVEL_PRIMARY; ca.commandBufferCount=1;
    if (vkAllocateCommandBuffers(c->device,&ca,&c->spare_cmd)!=VK_SUCCESS) return -2;
    if (pyrowave_decoder_set_timestamp_recording(c->decoder,0)!=PYROWAVE_SUCCESS) return -2;
    c->prerecord_owner=std::this_thread::get_id();
    c->prerecord_enabled=true;
    LOGI("[Q3PW_PRERECORD_SETUP] explicit_api=1 warmed_slots=3 max_gpu=1 max_prepared=1 granite_gpu_stage_queries=0 completion_queries=1");
    return 0;
}

extern "C" int pyroclient_prerecord_start(pyroclient *c, const void *data, size_t size,
                                          AHardwareBuffer **out, pyroclient_frame_info *info,
                                          AHardwareBuffer *a, AHardwareBuffer *b) {
    if (out) *out=nullptr;
    if (!c || !out || !info || !data || !size || !c->prerecord_owned() ||
        c->release_poisoned || c->pending_submission || c->prerecord.prepared.slot<3) return -6;
    auto ticket=c->prerecord.start(c->protected_slots(a,b));
    if (ticket.slot==3) return -4;
    pyrowave_decoder_clear(c->decoder);
    if (pyrowave_decoder_push_packet(c->decoder,data,size)!=PYROWAVE_SUCCESS ||
        !pyrowave_decoder_decode_is_ready(c->decoder,false)) {
        c->prerecord.reject_unsubmitted_start(ticket);
        return -3;
    }
    *info={}; info->complete=1;
    if (!c->record_and_submit(c->ring[ticket.slot],info,nullptr,true)) {
        c->release_poisoned=true; c->prerecord.poisoned=true; return -2;
    }
    *out=c->ring[ticket.slot].ahb;
    return 0; // Output remains reserved and MUST NOT be read/published yet.
}

extern "C" int pyroclient_prerecord_pending_status(pyroclient *c) {
    if (!c || !c->prerecord_owned() || c->release_poisoned || !c->pending_submission) return -1;
    auto status=vkGetFenceStatus(c->device,c->fence);
    if (status==VK_NOT_READY) return 0;
    if (status==VK_SUCCESS) return 1; // finish_pending still must collect queries.
    c->release_poisoned=true; c->prerecord.poisoned=true; return -2;
}

extern "C" int pyroclient_prerecord_prepare(pyroclient *c, const void *data, size_t size,
                                            AHardwareBuffer *a, AHardwareBuffer *b, uint64_t *generation) {
    if (generation) *generation=0;
    if (!c || !data || !size || !generation || !c->prerecord_owned() || c->release_poisoned ||
        !c->pending_submission || c->prerecord.prepared.slot<3) return -6;
    auto ticket=c->prerecord.prepare(c->protected_slots(a,b));
    if (ticket.slot==3) return -4;
    if (pyrowave_device_next_context_ready(c->pyro)!=1) {
        c->prerecord.abandon(ticket); return 0; // Defer, never blind context advance.
    }
    // The old packet's uploads are already copied/retained in its Granite context.
    // Fixed geometry is checked by PyroWave's sequence parser. GPU execution of
    // shared payload/offset/wavelet/planes remains serial. Growth is denied below.
    pyrowave_decoder_clear(c->decoder);
    if (pyrowave_decoder_push_packet(c->decoder,data,size)!=PYROWAVE_SUCCESS ||
        !pyrowave_decoder_decode_is_ready(c->decoder,false)) {
        c->prerecord.abandon(ticket); return -3;
    }
    if (pyrowave_decoder_prerecord_upload_fits(c->decoder)!=1) {
        c->prerecord.abandon(ticket);
        return -7; // Growth is deferred to a synchronous start AFTER A completes.
    }
    Slot &s=c->ring[ticket.slot];
    c->prepared_first_use=s.first_use;
    c->prepared_planes_initialised=c->planes_initialised;
    c->prepared_begin=std::chrono::steady_clock::now();
    std::swap(c->cmd,c->spare_cmd);
    bool recorded=c->record_commands(s);
    std::swap(c->cmd,c->spare_cmd);
    c->prepared_end=std::chrono::steady_clock::now();
    if (!recorded) {
        vkResetCommandBuffer(c->spare_cmd,0);
        s.first_use=c->prepared_first_use; c->planes_initialised=c->prepared_planes_initialised;
        c->release_poisoned=true; c->prerecord.poisoned=true; return -2;
    }
    *generation=ticket.generation;
    return 1; // B recorded; A may still be running; B has not been submitted.
}

bool pyroclient::abandon_prepared(uint64_t generation) {
    auto ticket=prerecord.prepared;
    if (!prerecord_owned() || release_poisoned || ticket.slot==3 || ticket.generation!=generation) return false;
    // Never advance the context until the unsubmitted borrowed command is reset.
    if (vkResetCommandBuffer(spare_cmd,0)!=VK_SUCCESS) {
        release_poisoned=true; prerecord.poisoned=true; return false;
    }
    ring[ticket.slot].first_use=prepared_first_use;
    planes_initialised=prepared_planes_initialised;
    pyrowave_decoder_clear(decoder);
    // Staging/views stay retained in this context. No resource is recycled now;
    // future checked advance retires it, after the older actual submission.
    return prerecord.abandon(ticket);
}

extern "C" int pyroclient_prerecord_cancel(pyroclient *c, uint64_t generation) {
    return c && c->abandon_prepared(generation) ? 0 : -1;
}

extern "C" int pyroclient_prerecord_submit(pyroclient *c, uint64_t generation,
                                          AHardwareBuffer **out, pyroclient_frame_info *info) {
    if (out) *out=nullptr;
    if (!c || !out || !info || !c->prerecord_owned() || c->release_poisoned || c->pending_submission) return -6;
    auto ticket=c->prerecord.prepared;
    if (ticket.slot==3 || ticket.generation!=generation || !c->prerecord.submit(ticket)) return -4;
    std::swap(c->cmd,c->spare_cmd); // Old active command was fence-verified, now spare.
    *info={}; info->complete=1;
    if (vkResetFences(c->device,1,&c->fence)!=VK_SUCCESS ||
        !c->submit_recorded(c->ring[ticket.slot],false,info,nullptr,c->prepared_begin,c->prepared_end,true)) {
        c->release_poisoned=true; c->prerecord.poisoned=true; return -2;
    }
    *out=c->ring[ticket.slot].ahb;
    return 0;
}

extern "C" double pyroclient_prerecord_queue_ms(pyroclient *c) {
    return c && c->prerecord_owned() ? c->prepared_queue_ms : -1;
}
