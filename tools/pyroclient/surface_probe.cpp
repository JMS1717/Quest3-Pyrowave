// Copyright (c) 2026 Quest3-Pyrowave contributors
// SPDX-License-Identifier: MIT
// Optional creation probe plus default-off one-shot static chart; never decoder-owned.
#include <android/log.h>
#include <android/native_window_jni.h>
#include <vulkan/vulkan.h>
#include <vulkan/vulkan_android.h>
#include <algorithm>
#include <cstring>
#include <cstdlib>
#include <memory>
#include <vector>
#include "surface_chart_pixels.h"

#define PROBE_LOG(...) __android_log_print(ANDROID_LOG_INFO, "pyroclient", __VA_ARGS__)

namespace {
struct Probe {
    JavaVM *vm = nullptr;
    JNIEnv *env = nullptr;
    bool attached = false;
    jobject retained = nullptr;
    ANativeWindow *window = nullptr;
    VkInstance instance = VK_NULL_HANDLE;
    VkSurfaceKHR surface = VK_NULL_HANDLE;
    VkDevice device = VK_NULL_HANDLE;
    VkSwapchainKHR swapchain = VK_NULL_HANDLE;
    VkPhysicalDevice gpu = VK_NULL_HANDLE;
    uint32_t family = UINT32_MAX;
    VkExtent2D extent{};
    VkFormat format = VK_FORMAT_UNDEFINED;
    VkQueue queue = VK_NULL_HANDLE;
    VkBuffer upload = VK_NULL_HANDLE;
    VkDeviceMemory upload_memory = VK_NULL_HANDLE;
    VkCommandPool pool = VK_NULL_HANDLE;
    VkFence acquired = VK_NULL_HANDLE, rendered = VK_NULL_HANDLE, presented = VK_NULL_HANDLE;
    VkSemaphore ready = VK_NULL_HANDLE;
    bool submitted = false, present_called = false, retirement_logged = false;
    ~Probe() {
        // Chart-only: render completion is NOT presentation-resource retirement.
        // Never destroy/recycle resources after an unproved wait. An opt-in
        // diagnostic timeout terminates this client for external recovery.
        if (submitted && vkWaitForFences(device, 1, &rendered, VK_TRUE, 2000000000ull) != VK_SUCCESS) {
            PROBE_LOG("[Q3PW_SURFACE_CHART] retire_failed=render preserve_inflight=true");
            std::abort();
        }
        if (present_called && vkWaitForFences(device, 1, &presented, VK_TRUE, 2000000000ull) != VK_SUCCESS) {
            PROBE_LOG("[Q3PW_SURFACE_CHART] retire_failed=present preserve_inflight=true");
            std::abort();
        }
        if (present_called) PROBE_LOG("[Q3PW_SURFACE_CHART] retired=true gpu_done=true present_done=true");
        if (ready) vkDestroySemaphore(device, ready, nullptr);
        for (auto fence : {acquired, rendered, presented}) if (fence) vkDestroyFence(device, fence, nullptr);
        if (pool) vkDestroyCommandPool(device, pool, nullptr);
        if (upload) vkDestroyBuffer(device, upload, nullptr);
        if (upload_memory) vkFreeMemory(device, upload_memory, nullptr);
        // Destroy the producer before its Surface/window/JNI references.
        if (swapchain) vkDestroySwapchainKHR(device, swapchain, nullptr);
        if (device) vkDestroyDevice(device, nullptr);
        if (surface) vkDestroySurfaceKHR(instance, surface, nullptr);
        if (instance) vkDestroyInstance(instance, nullptr);
        if (window) ANativeWindow_release(window);
        if (retained) env->DeleteGlobalRef(retained);
        if (attached) vm->DetachCurrentThread();
    }
};

bool has_extension(const std::vector<VkExtensionProperties> &properties, const char *name) {
    return std::any_of(properties.begin(), properties.end(), [name](const auto &p) {
        return !strcmp(p.extensionName, name);
    });
}
} // namespace

static int prepare_probe(Probe &probe, void *java_vm, void *java_surface, bool chart) {
    if (!java_vm || !java_surface) return -1;
    probe.vm = static_cast<JavaVM *>(java_vm);
    jint jni = probe.vm->GetEnv(reinterpret_cast<void **>(&probe.env), JNI_VERSION_1_6);
    if (jni == JNI_EDETACHED) {
        if (probe.vm->AttachCurrentThread(&probe.env, nullptr) != JNI_OK) return -2;
        probe.attached = true;
    } else if (jni != JNI_OK) return -2;
    if (probe.env->ExceptionCheck()) return -3; // Preserve a foreign pending exception.
    probe.retained = probe.env->NewGlobalRef(static_cast<jobject>(java_surface));
    if (!probe.retained || probe.env->ExceptionCheck()) {
        if (probe.env->ExceptionCheck()) probe.env->ExceptionClear();
        return -4;
    }
    probe.window = ANativeWindow_fromSurface(probe.env, probe.retained);
    if (!probe.window || probe.env->ExceptionCheck()) {
        if (probe.env->ExceptionCheck()) probe.env->ExceptionClear();
        return -5;
    }
    uint32_t count = 0;
    if (vkEnumerateInstanceExtensionProperties(nullptr, &count, nullptr) != VK_SUCCESS || count > 1024) return -6;
    std::vector<VkExtensionProperties> extensions(count);
    if (vkEnumerateInstanceExtensionProperties(nullptr, &count, extensions.data()) != VK_SUCCESS) return -6;
    if (!has_extension(extensions, VK_KHR_SURFACE_EXTENSION_NAME) ||
        !has_extension(extensions, VK_KHR_ANDROID_SURFACE_EXTENSION_NAME)) return -7;
    std::vector<const char *> instance_extensions = {VK_KHR_SURFACE_EXTENSION_NAME, VK_KHR_ANDROID_SURFACE_EXTENSION_NAME};
    if (chart) {
        bool maintenance = has_extension(extensions, VK_EXT_SURFACE_MAINTENANCE_1_EXTENSION_NAME);
        bool caps2 = has_extension(extensions, VK_KHR_GET_SURFACE_CAPABILITIES_2_EXTENSION_NAME);
        PROBE_LOG("[Q3PW_SURFACE_CHART_CAPS] surface_maintenance1=%d get_caps2=%d", maintenance, caps2);
        if (!maintenance || !caps2) return -17;
        instance_extensions.push_back(VK_EXT_SURFACE_MAINTENANCE_1_EXTENSION_NAME);
        instance_extensions.push_back(VK_KHR_GET_SURFACE_CAPABILITIES_2_EXTENSION_NAME);
    }
    VkApplicationInfo application = {VK_STRUCTURE_TYPE_APPLICATION_INFO};
    application.pApplicationName = "Quest3-Pyrowave Surface probe";
    application.apiVersion = VK_API_VERSION_1_1;
    VkInstanceCreateInfo instance = {VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO};
    instance.pApplicationInfo = &application;
    instance.enabledExtensionCount = uint32_t(instance_extensions.size());
    instance.ppEnabledExtensionNames = instance_extensions.data();
    VkResult result = vkCreateInstance(&instance, nullptr, &probe.instance);
    if (result != VK_SUCCESS) return int(result);
    VkAndroidSurfaceCreateInfoKHR android_surface = {VK_STRUCTURE_TYPE_ANDROID_SURFACE_CREATE_INFO_KHR};
    android_surface.window = probe.window;
    result = vkCreateAndroidSurfaceKHR(probe.instance, &android_surface, nullptr, &probe.surface);
    if (result != VK_SUCCESS) return int(result);
    if (vkEnumeratePhysicalDevices(probe.instance, &count, nullptr) != VK_SUCCESS || !count || count > 16) return -8;
    std::vector<VkPhysicalDevice> physical_devices(count);
    if (vkEnumeratePhysicalDevices(probe.instance, &count, physical_devices.data()) != VK_SUCCESS) return -8;
    VkPhysicalDevice gpu = VK_NULL_HANDLE;
    uint32_t family = UINT32_MAX;
    for (auto candidate : physical_devices) {
        uint32_t queues = 0;
        vkGetPhysicalDeviceQueueFamilyProperties(candidate, &queues, nullptr);
        if (!queues || queues > 128) continue;
        std::vector<VkQueueFamilyProperties> properties(queues);
        vkGetPhysicalDeviceQueueFamilyProperties(candidate, &queues, properties.data());
        for (uint32_t i = 0; i < queues; ++i) {
            VkBool32 present = VK_FALSE;
            if (properties[i].queueCount && (properties[i].queueFlags & VK_QUEUE_GRAPHICS_BIT) &&
                vkGetPhysicalDeviceSurfaceSupportKHR(candidate, i, probe.surface, &present) == VK_SUCCESS && present) {
                gpu = candidate; family = i; break;
            }
        }
        if (gpu) break;
    }
    if (!gpu) return -9;
    if (vkEnumerateDeviceExtensionProperties(gpu, nullptr, &count, nullptr) != VK_SUCCESS || count > 1024) return -10;
    extensions.resize(count);
    if (vkEnumerateDeviceExtensionProperties(gpu, nullptr, &count, extensions.data()) != VK_SUCCESS ||
        !has_extension(extensions, VK_KHR_SWAPCHAIN_EXTENSION_NAME)) return -10;
    VkPhysicalDeviceSwapchainMaintenance1FeaturesEXT maintenance = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SWAPCHAIN_MAINTENANCE_1_FEATURES_EXT};
    if (chart) {
        bool available = has_extension(extensions, VK_EXT_SWAPCHAIN_MAINTENANCE_1_EXTENSION_NAME);
        if (available) {
            VkPhysicalDeviceFeatures2 features = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2};
            features.pNext = &maintenance;
            vkGetPhysicalDeviceFeatures2(gpu, &features);
        }
        PROBE_LOG("[Q3PW_SURFACE_CHART_CAPS] swapchain_maintenance1=%d feature=%d", available, int(maintenance.swapchainMaintenance1));
        if (!available || !maintenance.swapchainMaintenance1) return -18;
    }
    VkSurfaceCapabilitiesKHR capabilities{};
    result = vkGetPhysicalDeviceSurfaceCapabilitiesKHR(gpu, probe.surface, &capabilities);
    if (result != VK_SUCCESS) return int(result);
    if (!(capabilities.supportedUsageFlags & VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT) ||
        !capabilities.minImageCount || capabilities.minImageCount > 16) return -11;
    if (chart && !(capabilities.supportedUsageFlags & VK_IMAGE_USAGE_TRANSFER_DST_BIT)) return -19;
    if (vkGetPhysicalDeviceSurfaceFormatsKHR(gpu, probe.surface, &count, nullptr) != VK_SUCCESS || !count || count > 1024) return -12;
    std::vector<VkSurfaceFormatKHR> formats(count);
    if (vkGetPhysicalDeviceSurfaceFormatsKHR(gpu, probe.surface, &count, formats.data()) != VK_SUCCESS) return -12;
    formats.resize(count);
    for (size_t i = 0; i < std::min<size_t>(formats.size(), 16); ++i)
        PROBE_LOG("[Q3PW_SURFACE_WSI_FORMAT] format=%d colorspace=%d", int(formats[i].format), int(formats[i].colorSpace));
    auto chosen = std::find_if(formats.begin(), formats.end(), [](auto f) {
        return f.format == VK_FORMAT_R8G8B8A8_UNORM && f.colorSpace == VK_COLOR_SPACE_SRGB_NONLINEAR_KHR;
    });
    if (chosen == formats.end()) chosen = std::find_if(formats.begin(), formats.end(), [](auto f) {
        return f.format == VK_FORMAT_B8G8R8A8_UNORM && f.colorSpace == VK_COLOR_SPACE_SRGB_NONLINEAR_KHR;
    });
    if (chosen == formats.end() && formats.size() == 1 && formats[0].format == VK_FORMAT_UNDEFINED &&
        formats[0].colorSpace == VK_COLOR_SPACE_SRGB_NONLINEAR_KHR) {
        formats[0].format = VK_FORMAT_R8G8B8A8_UNORM;
        chosen = formats.begin(); // WSI explicitly permits any format in this case.
    }
    if (chosen == formats.end()) return -13;
    float priority = 1.0f;
    VkDeviceQueueCreateInfo queue = {VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO};
    queue.queueFamilyIndex = family; queue.queueCount = 1; queue.pQueuePriorities = &priority;
    std::vector<const char *> device_extensions = {VK_KHR_SWAPCHAIN_EXTENSION_NAME};
    if (chart) device_extensions.push_back(VK_EXT_SWAPCHAIN_MAINTENANCE_1_EXTENSION_NAME);
    VkDeviceCreateInfo device = {VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO};
    device.queueCreateInfoCount = 1; device.pQueueCreateInfos = &queue;
    device.enabledExtensionCount = uint32_t(device_extensions.size()); device.ppEnabledExtensionNames = device_extensions.data();
    if (chart) device.pNext = &maintenance;
    result = vkCreateDevice(gpu, &device, nullptr, &probe.device);
    if (result != VK_SUCCESS) return int(result);
    VkExtent2D extent = capabilities.currentExtent;
    if (extent.width == UINT32_MAX) {
        extent.width = std::clamp(64u, capabilities.minImageExtent.width, capabilities.maxImageExtent.width);
        extent.height = std::clamp(32u, capabilities.minImageExtent.height, capabilities.maxImageExtent.height);
    }
    if (!extent.width || !extent.height || extent.width > 256 || extent.height > 256) return -14;
    if (chart && (extent.width != 64 || extent.height != 32)) return -26;
    VkSwapchainCreateInfoKHR swapchain = {VK_STRUCTURE_TYPE_SWAPCHAIN_CREATE_INFO_KHR};
    swapchain.surface = probe.surface;
    swapchain.minImageCount = std::min(capabilities.minImageCount + 1, 16u);
    if (capabilities.maxImageCount) swapchain.minImageCount = std::min(swapchain.minImageCount, capabilities.maxImageCount);
    swapchain.imageFormat = chosen->format; swapchain.imageColorSpace = chosen->colorSpace;
    swapchain.imageExtent = extent; swapchain.imageArrayLayers = 1;
    swapchain.imageUsage = VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT;
    if (chart) swapchain.imageUsage |= VK_IMAGE_USAGE_TRANSFER_DST_BIT;
    swapchain.imageSharingMode = VK_SHARING_MODE_EXCLUSIVE;
    swapchain.preTransform = capabilities.currentTransform;
    if (!capabilities.supportedCompositeAlpha) return -15;
    auto alpha = capabilities.supportedCompositeAlpha;
    swapchain.compositeAlpha = (alpha & VK_COMPOSITE_ALPHA_OPAQUE_BIT_KHR) ? VK_COMPOSITE_ALPHA_OPAQUE_BIT_KHR
        : static_cast<VkCompositeAlphaFlagBitsKHR>(alpha & (~alpha + 1u));
    swapchain.presentMode = VK_PRESENT_MODE_FIFO_KHR;
    swapchain.clipped = VK_TRUE;
    result = vkCreateSwapchainKHR(probe.device, &swapchain, nullptr, &probe.swapchain);
    if (result != VK_SUCCESS) return int(result);
    uint32_t images = 0;
    result = vkGetSwapchainImagesKHR(probe.device, probe.swapchain, &images, nullptr);
    if (result != VK_SUCCESS || !images || images > 32) return -16;
    VkPhysicalDeviceProperties properties{};
    vkGetPhysicalDeviceProperties(gpu, &properties);
    probe.gpu = gpu; probe.family = family; probe.extent = extent; probe.format = chosen->format;
    vkGetDeviceQueue(probe.device, family, 0, &probe.queue);
    PROBE_LOG("[%s] created=true vendor=0x%x device=0x%x family=%u extent=%ux%u images=%u format=%d usage=0x%x max_extent=%ux%u min_images=%u max_images=%u submitted=false",
        chart ? "Q3PW_SURFACE_CHART_WSI" : "Q3PW_SURFACE_WSI", properties.vendorID, properties.deviceID, family, extent.width, extent.height, images,
        int(chosen->format), unsigned(capabilities.supportedUsageFlags), capabilities.maxImageExtent.width,
        capabilities.maxImageExtent.height, capabilities.minImageCount, capabilities.maxImageCount);
    return 0;
}

extern "C" int pyroclient_probe_android_surface(void *java_vm, void *java_surface) {
    Probe probe;
    return prepare_probe(probe, java_vm, java_surface, false);
}

extern "C" void *pyroclient_create_surface_chart(void *java_vm, void *java_surface, int *status) {
    if (!status) return nullptr;
    auto probe = std::make_unique<Probe>();
    *status = prepare_probe(*probe, java_vm, java_surface, true);
    return *status == 0 ? probe.release() : nullptr;
}

extern "C" int pyroclient_present_surface_chart(void *opaque) {
    if (!opaque) return -20;
    auto &p = *static_cast<Probe *>(opaque);
    if (p.present_called) return -21; // Exactly ONE static image; no hidden frame ring.
    const VkDeviceSize bytes = VkDeviceSize(p.extent.width) * p.extent.height * 4;
    VkBufferCreateInfo buffer = {VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO};
    buffer.size = bytes; buffer.usage = VK_BUFFER_USAGE_TRANSFER_SRC_BIT;
    buffer.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
    VkResult result = vkCreateBuffer(p.device, &buffer, nullptr, &p.upload);
    if (result != VK_SUCCESS) return int(result);
    VkMemoryRequirements requirements{};
    vkGetBufferMemoryRequirements(p.device, p.upload, &requirements);
    VkPhysicalDeviceMemoryProperties memory{};
    vkGetPhysicalDeviceMemoryProperties(p.gpu, &memory);
    uint32_t type = UINT32_MAX;
    for (uint32_t i = 0; i < memory.memoryTypeCount; ++i) {
        auto wanted = VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT;
        if ((requirements.memoryTypeBits & (1u << i)) && (memory.memoryTypes[i].propertyFlags & wanted) == wanted) { type = i; break; }
    }
    if (type == UINT32_MAX) return -22;
    VkMemoryAllocateInfo allocation = {VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO};
    allocation.allocationSize = requirements.size; allocation.memoryTypeIndex = type;
    if ((result = vkAllocateMemory(p.device, &allocation, nullptr, &p.upload_memory)) != VK_SUCCESS) return int(result);
    if ((result = vkBindBufferMemory(p.device, p.upload, p.upload_memory, 0)) != VK_SUCCESS) return int(result);
    void *mapping = nullptr;
    if ((result = vkMapMemory(p.device, p.upload_memory, 0, bytes, 0, &mapping)) != VK_SUCCESS) return int(result);
    fill_surface_chart(static_cast<uint8_t *>(mapping), p.extent.width, p.extent.height,
                       p.format == VK_FORMAT_B8G8R8A8_UNORM);
    PROBE_LOG("[Q3PW_SURFACE_CHART_ORIENTATION] gles_session=true upload_rows=bottom_up");
    vkUnmapMemory(p.device, p.upload_memory);
    VkCommandPoolCreateInfo pool = {VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO};
    pool.queueFamilyIndex = p.family;
    if ((result = vkCreateCommandPool(p.device, &pool, nullptr, &p.pool)) != VK_SUCCESS) return int(result);
    VkCommandBufferAllocateInfo commands = {VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO};
    commands.commandPool = p.pool; commands.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY; commands.commandBufferCount = 1;
    VkCommandBuffer cmd = VK_NULL_HANDLE;
    if ((result = vkAllocateCommandBuffers(p.device, &commands, &cmd)) != VK_SUCCESS) return int(result);
    VkFenceCreateInfo fence = {VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};
    for (auto f : {&p.acquired, &p.rendered, &p.presented})
        if ((result = vkCreateFence(p.device, &fence, nullptr, f)) != VK_SUCCESS) return int(result);
    VkSemaphoreCreateInfo semaphore = {VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO};
    if ((result = vkCreateSemaphore(p.device, &semaphore, nullptr, &p.ready)) != VK_SUCCESS) return int(result);
    uint32_t count = 0;
    if (vkGetSwapchainImagesKHR(p.device, p.swapchain, &count, nullptr) != VK_SUCCESS || !count || count > 32) return -23;
    std::vector<VkImage> images(count);
    if (vkGetSwapchainImagesKHR(p.device, p.swapchain, &count, images.data()) != VK_SUCCESS) return -23;
    uint32_t index = 0;
    result = vkAcquireNextImageKHR(p.device, p.swapchain, 100000000ull, VK_NULL_HANDLE, p.acquired, &index);
    if (result != VK_SUCCESS && result != VK_SUBOPTIMAL_KHR) return int(result);
    // One-shot diagnostic deliberately uses an acquisition fence, not an unproved
    // semaphore recycle. Production handoff must instead remain GPU-side.
    if (vkWaitForFences(p.device, 1, &p.acquired, VK_TRUE, 2000000000ull) != VK_SUCCESS) {
        PROBE_LOG("[Q3PW_SURFACE_CHART] acquire_completion_failed preserve_inflight=true"); std::abort();
    }
    if (index >= images.size()) return -24;
    VkCommandBufferBeginInfo begin = {VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};
    begin.flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
    if ((result = vkBeginCommandBuffer(cmd, &begin)) != VK_SUCCESS) return int(result);
    VkImageMemoryBarrier barrier = {VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER};
    barrier.oldLayout = VK_IMAGE_LAYOUT_UNDEFINED; barrier.newLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;
    barrier.srcQueueFamilyIndex = barrier.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
    barrier.image = images[index]; barrier.subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT,0,1,0,1};
    barrier.dstAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;
    vkCmdPipelineBarrier(cmd,VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT,VK_PIPELINE_STAGE_TRANSFER_BIT,0,0,nullptr,0,nullptr,1,&barrier);
    VkBufferImageCopy copy{};
    copy.imageSubresource = {VK_IMAGE_ASPECT_COLOR_BIT,0,0,1};
    copy.imageExtent = {p.extent.width,p.extent.height,1};
    vkCmdCopyBufferToImage(cmd,p.upload,images[index],VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,1,&copy);
    barrier.oldLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL; barrier.newLayout = VK_IMAGE_LAYOUT_PRESENT_SRC_KHR;
    barrier.srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT; barrier.dstAccessMask = 0;
    vkCmdPipelineBarrier(cmd,VK_PIPELINE_STAGE_TRANSFER_BIT,VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT,0,0,nullptr,0,nullptr,1,&barrier);
    if ((result = vkEndCommandBuffer(cmd)) != VK_SUCCESS) return int(result);
    VkSubmitInfo submit = {VK_STRUCTURE_TYPE_SUBMIT_INFO};
    submit.commandBufferCount = 1; submit.pCommandBuffers = &cmd;
    submit.signalSemaphoreCount = 1; submit.pSignalSemaphores = &p.ready;
    if (vkQueueSubmit(p.queue,1,&submit,p.rendered) != VK_SUCCESS) {
        PROBE_LOG("[Q3PW_SURFACE_CHART] submit_failed preserve_inflight=true"); std::abort();
    }
    p.submitted = true;
    VkSwapchainPresentFenceInfoEXT retirement = {VK_STRUCTURE_TYPE_SWAPCHAIN_PRESENT_FENCE_INFO_EXT};
    retirement.swapchainCount = 1; retirement.pFences = &p.presented;
    VkPresentInfoKHR present = {VK_STRUCTURE_TYPE_PRESENT_INFO_KHR};
    present.pNext = &retirement; present.waitSemaphoreCount = 1; present.pWaitSemaphores = &p.ready;
    present.swapchainCount = 1; present.pSwapchains = &p.swapchain; present.pImageIndices = &index;
    p.present_called = true;
    result = vkQueuePresentKHR(p.queue,&present);
    PROBE_LOG("[Q3PW_SURFACE_CHART] present_result=%d image=%u extent=%ux%u format=%d one_shot=true",int(result),index,p.extent.width,p.extent.height,int(p.format));
    return (result == VK_SUCCESS || result == VK_SUBOPTIMAL_KHR) ? 0 : int(result);
}

extern "C" int pyroclient_poll_surface_chart(void *opaque) {
    if (!opaque) return -20;
    auto &p = *static_cast<Probe *>(opaque);
    if (!p.present_called) return 0;
    VkResult render = vkGetFenceStatus(p.device,p.rendered), present = vkGetFenceStatus(p.device,p.presented);
    if ((render != VK_SUCCESS && render != VK_NOT_READY) || (present != VK_SUCCESS && present != VK_NOT_READY)) return -25;
    if (render == VK_SUCCESS && present == VK_SUCCESS && !p.retirement_logged) {
        PROBE_LOG("[Q3PW_SURFACE_CHART] fences_ready=true gpu_done=true present_done=true");
        p.retirement_logged = true;
    }
    return (render == VK_SUCCESS ? 1 : 0) | (present == VK_SUCCESS ? 2 : 0);
}

extern "C" void pyroclient_destroy_surface_chart(void *opaque) { delete static_cast<Probe *>(opaque); }
