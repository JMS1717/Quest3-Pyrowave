// Copyright (c) 2026 Quest3-Pyrowave contributors
// SPDX-License-Identifier: MIT
// Optional creation-only WSI probe. No acquire, submit or present; never decoder-owned.
#include <android/log.h>
#include <android/native_window_jni.h>
#include <vulkan/vulkan.h>
#include <vulkan/vulkan_android.h>
#include <algorithm>
#include <cstring>
#include <vector>

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
    ~Probe() {
        // No GPU work was submitted. Destroy the producer before its Surface/window.
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

extern "C" int pyroclient_probe_android_surface(void *java_vm, void *java_surface) {
    if (!java_vm || !java_surface) return -1;
    Probe probe;
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
    const char *instance_extensions[] = {VK_KHR_SURFACE_EXTENSION_NAME, VK_KHR_ANDROID_SURFACE_EXTENSION_NAME};
    VkApplicationInfo application = {VK_STRUCTURE_TYPE_APPLICATION_INFO};
    application.pApplicationName = "Quest3-Pyrowave Surface probe";
    application.apiVersion = VK_API_VERSION_1_1;
    VkInstanceCreateInfo instance = {VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO};
    instance.pApplicationInfo = &application;
    instance.enabledExtensionCount = 2;
    instance.ppEnabledExtensionNames = instance_extensions;
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
    VkSurfaceCapabilitiesKHR capabilities{};
    result = vkGetPhysicalDeviceSurfaceCapabilitiesKHR(gpu, probe.surface, &capabilities);
    if (result != VK_SUCCESS) return int(result);
    if (!(capabilities.supportedUsageFlags & VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT) ||
        !capabilities.minImageCount || capabilities.minImageCount > 16) return -11;
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
    const char *device_extension = VK_KHR_SWAPCHAIN_EXTENSION_NAME;
    VkDeviceCreateInfo device = {VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO};
    device.queueCreateInfoCount = 1; device.pQueueCreateInfos = &queue;
    device.enabledExtensionCount = 1; device.ppEnabledExtensionNames = &device_extension;
    result = vkCreateDevice(gpu, &device, nullptr, &probe.device);
    if (result != VK_SUCCESS) return int(result);
    VkExtent2D extent = capabilities.currentExtent;
    if (extent.width == UINT32_MAX) {
        extent.width = std::clamp(64u, capabilities.minImageExtent.width, capabilities.maxImageExtent.width);
        extent.height = std::clamp(32u, capabilities.minImageExtent.height, capabilities.maxImageExtent.height);
    }
    if (!extent.width || !extent.height || extent.width > 256 || extent.height > 256) return -14;
    VkSwapchainCreateInfoKHR swapchain = {VK_STRUCTURE_TYPE_SWAPCHAIN_CREATE_INFO_KHR};
    swapchain.surface = probe.surface;
    swapchain.minImageCount = std::min(capabilities.minImageCount + 1, 16u);
    if (capabilities.maxImageCount) swapchain.minImageCount = std::min(swapchain.minImageCount, capabilities.maxImageCount);
    swapchain.imageFormat = chosen->format; swapchain.imageColorSpace = chosen->colorSpace;
    swapchain.imageExtent = extent; swapchain.imageArrayLayers = 1;
    swapchain.imageUsage = VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT;
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
    PROBE_LOG("[Q3PW_SURFACE_WSI] created=true vendor=0x%x device=0x%x family=%u extent=%ux%u images=%u format=%d usage=0x%x max_extent=%ux%u min_images=%u max_images=%u submitted=false",
        properties.vendorID, properties.deviceID, family, extent.width, extent.height, images,
        int(chosen->format), unsigned(capabilities.supportedUsageFlags), capabilities.maxImageExtent.width,
        capabilities.maxImageExtent.height, capabilities.minImageCount, capabilities.maxImageCount);
    return 0;
}
