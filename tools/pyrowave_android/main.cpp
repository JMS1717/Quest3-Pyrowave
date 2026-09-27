// PyroWave decode into AHardwareBuffer-backed images, on the headset.
//
// This is the core of the planned ALVR client decode path, standalone so a mistake costs a
// rebuild rather than an APK install and a headset session.
//
// Why this shape. ALVR's client is GLES/EGL (Backends::GL), so there is no VkDevice to borrow
// from it, and PyroWave cannot import an AHardwareBuffer either -- Granite's importer only
// handles Win32 handles and FDs. What is left, and what pyrowave.h explicitly describes, is the
// borrowed-device path: we create the Vulkan device, allocate the destination images ourselves,
// and hand PyroWave plain image views. AHardwareBuffer backing is what lets GLES then import the
// result through EGL_ANDROID_image_native_buffer, which ALVR already does for MediaCodec.
//
// Verification here is a CPU readback through AHardwareBuffer_lock rather than EGL: it proves the
// decode lands in the buffer correctly, which is the part that is actually in doubt.
//
// Usage: pyrowave_android <in.wave> <out.y4m>

#include <android/hardware_buffer.h>
#include <vulkan/vulkan.h>
#include <vulkan/vulkan_android.h>

#include "pyrowave.h"
#include "ycbcr_to_rgba_spv.h"
#include "haar_fused_spv.h"
#include "idwt97_fused_spv.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>
#include <chrono>

#define VK_CHECK(x)                                                                                \
    do {                                                                                           \
        VkResult _r = (x);                                                                         \
        if (_r != VK_SUCCESS) {                                                                    \
            fprintf(stderr, "FAILED %s -> %d (line %d)\n", #x, (int)_r, __LINE__);                 \
            return 1;                                                                              \
        }                                                                                          \
    } while (0)

#define PW_CHECK(x)                                                                                \
    do {                                                                                           \
        pyrowave_result _r = (x);                                                                  \
        if (_r != PYROWAVE_SUCCESS) {                                                              \
            fprintf(stderr, "FAILED %s -> %d (line %d)\n", #x, (int)_r, __LINE__);                 \
            return 1;                                                                              \
        }                                                                                          \
    } while (0)

namespace {

struct WaveFile {
    int width = 0, height = 0, format = 0, chroma = 0, full_range = 0, fps_num = 72, fps_den = 1;
    std::vector<uint8_t> frame;

    bool load(const char *path) {
        FILE *f = fopen(path, "rb");
        if (!f) {
            fprintf(stderr, "cannot open %s\n", path);
            return false;
        }
        char magic[8];
        int32_t params[8];
        uint32_t size = 0;
        bool ok = fread(magic, 1, 8, f) == 8 && memcmp(magic, "PYROWAVE", 8) == 0
            && fread(params, sizeof(params), 1, f) == 1 && fread(&size, sizeof(size), 1, f) == 1;
        if (ok) {
            width = params[0];
            height = params[1];
            format = params[2];
            chroma = params[3];
            full_range = params[4];
            fps_num = params[5];
            fps_den = params[6];
            frame.resize(size);
            ok = fread(frame.data(), 1, size, f) == size;
        }
        fclose(f);
        if (!ok) {
            fprintf(stderr, "%s is not a readable .wave\n", path);
        }
        return ok;
    }
};

// One decode target plane: an AHardwareBuffer, the VkImage bound to its memory, and a view.
struct Plane {
    AHardwareBuffer *ahb = nullptr;
    VkImage image = VK_NULL_HANDLE;
    VkDeviceMemory memory = VK_NULL_HANDLE;
    VkImageView view = VK_NULL_HANDLE;
    int width = 0, height = 0;
};

} // namespace

int main(int argc, char **argv) {
    if (argc < 3) {
        fprintf(stderr, "usage: %s <in.wave> <out.y4m>\n", argv[0]);
        return 1;
    }

    WaveFile wave;
    if (!wave.load(argv[1])) {
        return 1;
    }
    const bool chroma444 = wave.chroma == 1;
    const int chroma_w = chroma444 ? wave.width : wave.width / 2;
    const int chroma_h = chroma444 ? wave.height : wave.height / 2;
    printf("input %dx%d %s %s range, %zu byte frame\n", wave.width, wave.height,
           chroma444 ? "4:4:4" : "4:2:0", wave.full_range ? "full" : "limited", wave.frame.size());

    // --- Vulkan device. PyroWave's decoder wants subgroup basics plus subgroup size control
    // (both Vulkan 1.3 core), so a 1.3 instance with default features covers it; the AHB
    // extension is what this harness is really here to exercise.
    VkApplicationInfo appInfo = { VK_STRUCTURE_TYPE_APPLICATION_INFO };
    appInfo.pApplicationName = "pyrowave_android";
    appInfo.apiVersion = VK_API_VERSION_1_3;

    VkInstanceCreateInfo instanceInfo = { VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO };
    instanceInfo.pApplicationInfo = &appInfo;
    VkInstance instance;
    VK_CHECK(vkCreateInstance(&instanceInfo, nullptr, &instance));

    uint32_t gpuCount = 0;
    vkEnumeratePhysicalDevices(instance, &gpuCount, nullptr);
    std::vector<VkPhysicalDevice> gpus(gpuCount);
    vkEnumeratePhysicalDevices(instance, &gpuCount, gpus.data());
    if (gpus.empty()) {
        fprintf(stderr, "no Vulkan device\n");
        return 1;
    }
    VkPhysicalDevice gpu = gpus[0];
    VkPhysicalDeviceProperties props;
    vkGetPhysicalDeviceProperties(gpu, &props);
    printf("gpu: %s (API %u.%u.%u)\n", props.deviceName, VK_VERSION_MAJOR(props.apiVersion),
           VK_VERSION_MINOR(props.apiVersion), VK_VERSION_PATCH(props.apiVersion));

    uint32_t familyCount = 0;
    vkGetPhysicalDeviceQueueFamilyProperties(gpu, &familyCount, nullptr);
    std::vector<VkQueueFamilyProperties> families(familyCount);
    vkGetPhysicalDeviceQueueFamilyProperties(gpu, &familyCount, families.data());
    uint32_t graphicsFamily = UINT32_MAX;
    for (uint32_t i = 0; i < familyCount; i++) {
        if (families[i].queueFlags & VK_QUEUE_GRAPHICS_BIT) {
            graphicsFamily = i;
            break;
        }
    }
    if (graphicsFamily == UINT32_MAX) {
        fprintf(stderr, "no graphics queue\n");
        return 1;
    }

    const char *deviceExtensions[] = {
        VK_ANDROID_EXTERNAL_MEMORY_ANDROID_HARDWARE_BUFFER_EXTENSION_NAME,
        VK_EXT_QUEUE_FAMILY_FOREIGN_EXTENSION_NAME,
    };

    // The create infos must outlive the pyrowave_device, so they live here in main.
    const float queuePriority = 1.0f;
    VkDeviceQueueCreateInfo queueInfo = { VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO };
    queueInfo.queueFamilyIndex = graphicsFamily;
    queueInfo.queueCount = 1;
    queueInfo.pQueuePriorities = &queuePriority;

    VkPhysicalDeviceVulkan13Features features13 = {
        VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_3_FEATURES
    };
    VkPhysicalDeviceVulkan12Features features12 = {
        VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES
    };
    VkPhysicalDeviceVulkan11Features features11 = {
        VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_1_FEATURES
    };
    VkPhysicalDeviceFeatures2 features2 = { VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2 };
    features2.pNext = &features11;
    features11.pNext = &features12;
    features12.pNext = &features13;
    // Ask the driver what it has, then enable exactly that. PyroWave needs shaderInt16,
    // storageBuffer8BitAccess and subgroup size control; if any are missing it will say so.
    vkGetPhysicalDeviceFeatures2(gpu, &features2);
    printf("features: shaderInt16=%d storageBuffer8Bit=%d subgroupSizeControl=%d\n",
           features2.features.shaderInt16, features12.storageBuffer8BitAccess,
           features13.subgroupSizeControl);

    VkDeviceCreateInfo deviceInfo = { VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO };
    deviceInfo.pNext = &features2;
    deviceInfo.queueCreateInfoCount = 1;
    deviceInfo.pQueueCreateInfos = &queueInfo;
    deviceInfo.enabledExtensionCount = sizeof(deviceExtensions) / sizeof(deviceExtensions[0]);
    deviceInfo.ppEnabledExtensionNames = deviceExtensions;

    VkDevice device;
    VK_CHECK(vkCreateDevice(gpu, &deviceInfo, nullptr, &device));

    // --- Hand the device to PyroWave. This is the path that avoids external memory entirely:
    // we own the images, so nothing has to be imported or exported between us and the codec.
    pyrowave_device_create_info pyroInfo = {};
    pyroInfo.GetInstanceProcAddr = vkGetInstanceProcAddr;
    pyroInfo.instance = instance;
    pyroInfo.physical_device = gpu;
    pyroInfo.device = device;
    pyroInfo.instance_create_info = &instanceInfo;
    pyroInfo.device_create_info = &deviceInfo;

    pyrowave_device pyro = nullptr;
    PW_CHECK(pyrowave_create_device(&pyroInfo, &pyro));
    printf("pyrowave device created (borrowed VkDevice)\n");

    bool fragmentPath = pyrowave_decoder_device_prefers_fragment_path(pyro);
    if (getenv("PYROWAVE_FORCE_COMPUTE")) fragmentPath = false;
    if (getenv("PYROWAVE_FORCE_FRAGMENT")) fragmentPath = true;
    printf("decode path: %s\n", fragmentPath ? "fragment" : "compute");

    // --- Three AHardwareBuffer-backed planes.
    //
    // GPU_COLOR_OUTPUT is what makes the fragment decode path legal: it writes planes as colour
    // attachments rather than storage images. GPU_SAMPLED_IMAGE is for the GLES side later.
    // PYROWAVE_AHB=1 decodes straight into AHardwareBuffer-backed RGBA8 images. Default is
    // plain single-component R8 images, which is what PyroWave's planes actually are -- this
    // device allocates no single-component hardware buffer, so the two cannot be the same image.
    const bool useAhb = [] {
        const char *e = getenv("PYROWAVE_AHB");
        return e && *e && *e != '0';
    }();
    printf("decode target: %s\n", useAhb ? "AHardwareBuffer RGBA8" : "plain R8_UNORM");

    const VkFormat planeFormat = useAhb ? VK_FORMAT_R8G8B8A8_UNORM : VK_FORMAT_R8_UNORM;
    Plane planes[3] = {};
    for (int i = 0; i < 3; i++) {
        Plane &p = planes[i];
        p.width = i == 0 ? wave.width : chroma_w;
        p.height = i == 0 ? wave.height : chroma_h;

        if (!useAhb) {
            VkImageCreateInfo plainInfo = { VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO };
            plainInfo.imageType = VK_IMAGE_TYPE_2D;
            plainInfo.format = planeFormat;
            plainInfo.extent = { (uint32_t)p.width, (uint32_t)p.height, 1u };
            plainInfo.mipLevels = 1;
            plainInfo.arrayLayers = 1;
            plainInfo.samples = VK_SAMPLE_COUNT_1_BIT;
            plainInfo.tiling = VK_IMAGE_TILING_OPTIMAL;
            // Both: pyrowave.h says a decode view needs STORAGE, while the fragment path writes
            // planes as colour attachments. Granting only one leaves it unable to make the view
            // it wants, and it fails by writing nothing rather than by returning an error.
            plainInfo.usage = VK_IMAGE_USAGE_SAMPLED_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT
                | VK_IMAGE_USAGE_STORAGE_BIT | VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT;
            plainInfo.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
            plainInfo.initialLayout = VK_IMAGE_LAYOUT_UNDEFINED;
            VK_CHECK(vkCreateImage(device, &plainInfo, nullptr, &p.image));

            VkMemoryRequirements req;
            vkGetImageMemoryRequirements(device, p.image, &req);
            VkPhysicalDeviceMemoryProperties memProps;
            vkGetPhysicalDeviceMemoryProperties(gpu, &memProps);
            uint32_t type = UINT32_MAX;
            for (uint32_t t = 0; t < memProps.memoryTypeCount; t++) {
                if ((req.memoryTypeBits & (1u << t))
                    && (memProps.memoryTypes[t].propertyFlags
                        & VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT)) {
                    type = t;
                    break;
                }
            }
            if (type == UINT32_MAX) {
                fprintf(stderr, "no device-local memory type for plane %d\n", i);
                return 1;
            }
            VkMemoryAllocateInfo alloc = { VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO };
            alloc.allocationSize = req.size;
            alloc.memoryTypeIndex = type;
            VK_CHECK(vkAllocateMemory(device, &alloc, nullptr, &p.memory));
            VK_CHECK(vkBindImageMemory(device, p.image, p.memory, 0));

            VkImageViewCreateInfo plainView = { VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO };
            plainView.image = p.image;
            plainView.viewType = VK_IMAGE_VIEW_TYPE_2D;
            plainView.format = planeFormat;
            plainView.subresourceRange = { VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1 };
            VK_CHECK(vkCreateImageView(device, &plainView, nullptr, &p.view));
            printf("plane[%d]: %dx%d plain R8, %llu bytes\n", i, p.width, p.height,
                   (unsigned long long)req.size);
            continue;
        }

        AHardwareBuffer_Desc desc = {};
        desc.width = (uint32_t)p.width;
        desc.height = (uint32_t)p.height;
        desc.layers = 1;
        // RGBA8 rather than R8: this device allocates no single-component hardware buffers at
        // all (R8, R8G8 and R16 all report isSupported=false), only RGBA8/RGBX8 and
        // Y8Cb8Cr8_420. Three of the four channels go unused, which is wasteful but is the only
        // thing that allocates.
        desc.format = AHARDWAREBUFFER_FORMAT_R8G8B8A8_UNORM;
        desc.usage = AHARDWAREBUFFER_USAGE_GPU_SAMPLED_IMAGE
            | AHARDWAREBUFFER_USAGE_GPU_COLOR_OUTPUT
            | AHARDWAREBUFFER_USAGE_CPU_READ_OFTEN;
        if (AHardwareBuffer_allocate(&desc, &p.ahb) != 0 || p.ahb == nullptr) {
            fprintf(stderr, "AHardwareBuffer_allocate failed for plane %d (%dx%d R8)\n", i,
                    p.width, p.height);
            return 1;
        }

        auto getProps = (PFN_vkGetAndroidHardwareBufferPropertiesANDROID)vkGetDeviceProcAddr(
            device, "vkGetAndroidHardwareBufferPropertiesANDROID");
        if (!getProps) {
            fprintf(stderr, "vkGetAndroidHardwareBufferPropertiesANDROID missing\n");
            return 1;
        }
        VkAndroidHardwareBufferPropertiesANDROID ahbProps = {
            VK_STRUCTURE_TYPE_ANDROID_HARDWARE_BUFFER_PROPERTIES_ANDROID
        };
        VK_CHECK(getProps(device, p.ahb, &ahbProps));

        VkExternalMemoryImageCreateInfo externalInfo = {
            VK_STRUCTURE_TYPE_EXTERNAL_MEMORY_IMAGE_CREATE_INFO
        };
        externalInfo.handleTypes =
            VK_EXTERNAL_MEMORY_HANDLE_TYPE_ANDROID_HARDWARE_BUFFER_BIT_ANDROID;

        VkImageCreateInfo imageInfo = { VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO };
        imageInfo.pNext = &externalInfo;
        imageInfo.imageType = VK_IMAGE_TYPE_2D;
        imageInfo.format = planeFormat;
        imageInfo.extent = { (uint32_t)p.width, (uint32_t)p.height, 1u };
        imageInfo.mipLevels = 1;
        imageInfo.arrayLayers = 1;
        imageInfo.samples = VK_SAMPLE_COUNT_1_BIT;
        imageInfo.tiling = VK_IMAGE_TILING_OPTIMAL;
        imageInfo.usage = VK_IMAGE_USAGE_SAMPLED_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT
            | (fragmentPath ? VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT : VK_IMAGE_USAGE_STORAGE_BIT);
        imageInfo.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
        imageInfo.initialLayout = VK_IMAGE_LAYOUT_UNDEFINED;
        VK_CHECK(vkCreateImage(device, &imageInfo, nullptr, &p.image));

        VkImportAndroidHardwareBufferInfoANDROID importInfo = {
            VK_STRUCTURE_TYPE_IMPORT_ANDROID_HARDWARE_BUFFER_INFO_ANDROID
        };
        importInfo.buffer = p.ahb;
        VkMemoryDedicatedAllocateInfo dedicated = {
            VK_STRUCTURE_TYPE_MEMORY_DEDICATED_ALLOCATE_INFO
        };
        dedicated.image = p.image;
        dedicated.pNext = &importInfo;

        uint32_t memoryType = UINT32_MAX;
        VkPhysicalDeviceMemoryProperties memProps;
        vkGetPhysicalDeviceMemoryProperties(gpu, &memProps);
        for (uint32_t t = 0; t < memProps.memoryTypeCount; t++) {
            if (ahbProps.memoryTypeBits & (1u << t)) {
                memoryType = t;
                break;
            }
        }
        if (memoryType == UINT32_MAX) {
            fprintf(stderr, "no memory type for the hardware buffer\n");
            return 1;
        }

        VkMemoryAllocateInfo allocInfo = { VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO };
        allocInfo.pNext = &dedicated;
        allocInfo.allocationSize = ahbProps.allocationSize;
        allocInfo.memoryTypeIndex = memoryType;
        VK_CHECK(vkAllocateMemory(device, &allocInfo, nullptr, &p.memory));
        VK_CHECK(vkBindImageMemory(device, p.image, p.memory, 0));

        VkImageViewCreateInfo viewInfo = { VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO };
        viewInfo.image = p.image;
        viewInfo.viewType = VK_IMAGE_VIEW_TYPE_2D;
        viewInfo.format = planeFormat;
        viewInfo.subresourceRange = { VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1 };
        VK_CHECK(vkCreateImageView(device, &viewInfo, nullptr, &p.view));

        printf("plane[%d]: %dx%d AHB imported, %llu bytes\n", i, p.width, p.height,
               (unsigned long long)ahbProps.allocationSize);
    }

    // --- Decode.
    // pyrowave.h: "Decoder: VK_QUEUE_COMPUTE_BIT (if using normal path), VK_QUEUE_GRAPHICS_BIT
    // (if using fragment path)". The default is compute, so the fragment path needs this or the
    // work is recorded against the wrong queue type.
    PW_CHECK(pyrowave_device_set_queue_type(
        pyro, fragmentPath ? VK_QUEUE_GRAPHICS_BIT : VK_QUEUE_COMPUTE_BIT));
    printf("queue type: %s\n", fragmentPath ? "GRAPHICS" : "COMPUTE");

    pyrowave_decoder_create_info decoderInfo = {};
    decoderInfo.device = pyro;
    decoderInfo.width = wave.width;
    decoderInfo.height = wave.height;
    decoderInfo.chroma =
        chroma444 ? PYROWAVE_CHROMA_SUBSAMPLING_444 : PYROWAVE_CHROMA_SUBSAMPLING_420;
    decoderInfo.fragment_path = fragmentPath;
    // XRW Experiment 2: PYROWAVE_WAVELET=53 -> CDF 5/3 decoder (compute only); the stream's
    // header must agree or pyrowave rejects the packet. PYROWAVE_PRECISION is read by pyrowave
    // itself (0 = FP16 math, 1 = FP32 math / FP16 storage, 2 = FP32).
    const char *wv = getenv("PYROWAVE_WAVELET");
    decoderInfo.wavelet = (wv && strcmp(wv, "53") == 0) ? PYROWAVE_WAVELET_CDF53
                        : (wv && strcmp(wv, "haar") == 0) ? PYROWAVE_WAVELET_HAAR : PYROWAVE_WAVELET_CDF97;
    printf("wavelet: %s, PYROWAVE_PRECISION=%s\n", decoderInfo.wavelet == PYROWAVE_WAVELET_HAAR ? "Haar" : decoderInfo.wavelet == PYROWAVE_WAVELET_CDF53 ? "CDF 5/3" : "CDF 9/7",
           getenv("PYROWAVE_PRECISION") ? getenv("PYROWAVE_PRECISION") : "(default 1)");
    pyrowave_decoder decoder = nullptr;
    PW_CHECK(pyrowave_decoder_create(&decoderInfo, &decoder));

    PW_CHECK(pyrowave_decoder_push_packet(decoder, wave.frame.data(), wave.frame.size()));
    const bool ready = pyrowave_decoder_decode_is_ready(decoder, false);
    printf("decode_is_ready(complete) = %s\n", ready ? "yes" : "no");

    pyrowave_gpu_buffers buffers = {};
    for (int i = 0; i < 3; i++) {
        pyrowave_image_view &v = buffers.planes[i];
        v.image = planes[i].image;
        // Chroma views report the LUMA extent here: unlike three separate imported images, these
        // are the decoder's own targets and it derives chroma size from the subsampling mode.
        v.width = (uint32_t)wave.width;
        v.height = (uint32_t)wave.height;
        v.image_format = planeFormat;
        v.view_format = planeFormat;
        v.mip_level = 0;
        v.layer = 0;
        v.aspect = VK_IMAGE_ASPECT_COLOR_BIT;
        v.swizzle = VK_COMPONENT_SWIZZLE_IDENTITY;
        v.layout = VK_IMAGE_LAYOUT_GENERAL;
    }

    // Record the decode into OUR command buffer and submit it ourselves.
    //
    // With a borrowed VkDevice, PyroWave owns no queue, so leaving it to submit on its own
    // leaves the work never executed -- decode_gpu_buffer returns success and the planes stay
    // zeroed, which reads as a flat green frame. Setting a command buffer is also how a real
    // client wants this anyway: the decode belongs in the frame's own command buffer.
    VkQueue queue;
    vkGetDeviceQueue(device, graphicsFamily, 0, &queue);

    VkCommandPoolCreateInfo poolInfo = { VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO };
    poolInfo.queueFamilyIndex = graphicsFamily;
    VkCommandPool decodePool;
    VK_CHECK(vkCreateCommandPool(device, &poolInfo, nullptr, &decodePool));
    VkCommandBufferAllocateInfo cmdAlloc = { VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO };
    cmdAlloc.commandPool = decodePool;
    cmdAlloc.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY;
    cmdAlloc.commandBufferCount = 1;
    VkCommandBuffer decodeCmd;
    VK_CHECK(vkAllocateCommandBuffers(device, &cmdAlloc, &decodeCmd));

    // --- The GLES-bridge conversion pass (T3): three planes -> one RGBA8 image.
    //
    // A Vulkan-native client would not need this at all; it exists because GLES can only import
    // an RGBA8 AHardwareBuffer here. Timing it is timing the bridge.
    VkImage rgbaImage = VK_NULL_HANDLE;
    VkDeviceMemory rgbaMemory = VK_NULL_HANDLE;
    VkImageView rgbaView = VK_NULL_HANDLE;
    {
        VkImageCreateInfo info = { VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO };
        info.imageType = VK_IMAGE_TYPE_2D;
        info.format = VK_FORMAT_R8G8B8A8_UNORM;
        info.extent = { (uint32_t)wave.width, (uint32_t)wave.height, 1u };
        info.mipLevels = 1;
        info.arrayLayers = 1;
        info.samples = VK_SAMPLE_COUNT_1_BIT;
        info.tiling = VK_IMAGE_TILING_OPTIMAL;
        info.usage = VK_IMAGE_USAGE_STORAGE_BIT | VK_IMAGE_USAGE_SAMPLED_BIT
            | VK_IMAGE_USAGE_TRANSFER_SRC_BIT;
        info.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
        info.initialLayout = VK_IMAGE_LAYOUT_UNDEFINED;
        VK_CHECK(vkCreateImage(device, &info, nullptr, &rgbaImage));

        VkMemoryRequirements req;
        vkGetImageMemoryRequirements(device, rgbaImage, &req);
        VkPhysicalDeviceMemoryProperties memProps;
        vkGetPhysicalDeviceMemoryProperties(gpu, &memProps);
        uint32_t type = UINT32_MAX;
        for (uint32_t t = 0; t < memProps.memoryTypeCount; t++) {
            if ((req.memoryTypeBits & (1u << t))
                && (memProps.memoryTypes[t].propertyFlags
                    & VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT)) {
                type = t;
                break;
            }
        }
        VkMemoryAllocateInfo alloc = { VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO };
        alloc.allocationSize = req.size;
        alloc.memoryTypeIndex = type;
        VK_CHECK(vkAllocateMemory(device, &alloc, nullptr, &rgbaMemory));
        VK_CHECK(vkBindImageMemory(device, rgbaImage, rgbaMemory, 0));

        VkImageViewCreateInfo viewInfo = { VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO };
        viewInfo.image = rgbaImage;
        viewInfo.viewType = VK_IMAGE_VIEW_TYPE_2D;
        viewInfo.format = VK_FORMAT_R8G8B8A8_UNORM;
        viewInfo.subresourceRange = { VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1 };
        VK_CHECK(vkCreateImageView(device, &viewInfo, nullptr, &rgbaView));
    }

    VkSampler sampler;
    {
        VkSamplerCreateInfo info = { VK_STRUCTURE_TYPE_SAMPLER_CREATE_INFO };
        info.magFilter = VK_FILTER_LINEAR;
        info.minFilter = VK_FILTER_LINEAR;
        info.addressModeU = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
        info.addressModeV = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
        info.addressModeW = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
        VK_CHECK(vkCreateSampler(device, &info, nullptr, &sampler));
    }

    VkDescriptorSetLayout setLayout;
    VkPipelineLayout pipelineLayout;
    VkPipeline convertPipeline;
    VkDescriptorPool descPool;
    VkDescriptorSet descSet;
    {
        VkDescriptorSetLayoutBinding bindings[4] = {};
        for (int i = 0; i < 3; i++) {
            bindings[i].binding = i;
            bindings[i].descriptorType = VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER;
            bindings[i].descriptorCount = 1;
            bindings[i].stageFlags = VK_SHADER_STAGE_COMPUTE_BIT;
        }
        bindings[3].binding = 3;
        bindings[3].descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_IMAGE;
        bindings[3].descriptorCount = 1;
        bindings[3].stageFlags = VK_SHADER_STAGE_COMPUTE_BIT;

        VkDescriptorSetLayoutCreateInfo layoutInfo = {
            VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO
        };
        layoutInfo.bindingCount = 4;
        layoutInfo.pBindings = bindings;
        VK_CHECK(vkCreateDescriptorSetLayout(device, &layoutInfo, nullptr, &setLayout));

        VkPipelineLayoutCreateInfo plInfo = { VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO };
        plInfo.setLayoutCount = 1;
        plInfo.pSetLayouts = &setLayout;
        VK_CHECK(vkCreatePipelineLayout(device, &plInfo, nullptr, &pipelineLayout));

        VkShaderModuleCreateInfo smInfo = { VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO };
        smInfo.codeSize = sizeof(YCBCR_TO_RGBA_SPV);
        smInfo.pCode = YCBCR_TO_RGBA_SPV;
        VkShaderModule module;
        VK_CHECK(vkCreateShaderModule(device, &smInfo, nullptr, &module));

        VkComputePipelineCreateInfo cpInfo = { VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO };
        cpInfo.stage.sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO;
        cpInfo.stage.stage = VK_SHADER_STAGE_COMPUTE_BIT;
        cpInfo.stage.module = module;
        cpInfo.stage.pName = "main";
        cpInfo.layout = pipelineLayout;
        VK_CHECK(vkCreateComputePipelines(device, VK_NULL_HANDLE, 1, &cpInfo, nullptr,
                                          &convertPipeline));
        vkDestroyShaderModule(device, module, nullptr);

        VkDescriptorPoolSize sizes[2] = {};
        sizes[0].type = VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER;
        sizes[0].descriptorCount = 3;
        sizes[1].type = VK_DESCRIPTOR_TYPE_STORAGE_IMAGE;
        sizes[1].descriptorCount = 1;
        VkDescriptorPoolCreateInfo poolCreate = {
            VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO
        };
        poolCreate.maxSets = 1;
        poolCreate.poolSizeCount = 2;
        poolCreate.pPoolSizes = sizes;
        VK_CHECK(vkCreateDescriptorPool(device, &poolCreate, nullptr, &descPool));

        VkDescriptorSetAllocateInfo setAlloc = {
            VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO
        };
        setAlloc.descriptorPool = descPool;
        setAlloc.descriptorSetCount = 1;
        setAlloc.pSetLayouts = &setLayout;
        VK_CHECK(vkAllocateDescriptorSets(device, &setAlloc, &descSet));

        VkDescriptorImageInfo planeInfos[3] = {};
        VkWriteDescriptorSet writes[4] = {};
        for (int i = 0; i < 3; i++) {
            planeInfos[i].sampler = sampler;
            planeInfos[i].imageView = planes[i].view;
            planeInfos[i].imageLayout = VK_IMAGE_LAYOUT_GENERAL;
            writes[i].sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET;
            writes[i].dstSet = descSet;
            writes[i].dstBinding = i;
            writes[i].descriptorCount = 1;
            writes[i].descriptorType = VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER;
            writes[i].pImageInfo = &planeInfos[i];
        }
        VkDescriptorImageInfo outInfo = {};
        outInfo.imageView = rgbaView;
        outInfo.imageLayout = VK_IMAGE_LAYOUT_GENERAL;
        writes[3].sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET;
        writes[3].dstSet = descSet;
        writes[3].dstBinding = 3;
        writes[3].descriptorCount = 1;
        writes[3].descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_IMAGE;
        writes[3].pImageInfo = &outInfo;
        vkUpdateDescriptorSets(device, 4, writes, 0, nullptr);
    }

    // ---- Experiment 3, Arm C: fused Haar reconstruction outside libpyrowave ----------------
    // PYROWAVE_FUSED=H3 (one stage, 5 levels) or H2 (two stages, 3 + 2 levels). libpyrowave
    // records dequant only; the harness reconstructs from the wavelet image into the R8 planes
    // with haar_fused.comp, inside the same T0..T2 timestamp window as the library's own iDWT.
    struct FusedStage { int level_in, level_out; };
    std::vector<FusedStage> fusedStages;
    if (const char *f = getenv("PYROWAVE_FUSED")) {
        if (!strcmp(f, "H3")) fusedStages = { {5, 0} };
        else if (!strcmp(f, "H2")) fusedStages = { {5, 2}, {2, 0} };
        else if (!strcmp(f, "H1")) fusedStages = { {5, 3}, {3, 1}, {1, 0} };
        else if (!strcmp(f, "H0")) fusedStages = { {5, 4}, {4, 3}, {3, 2}, {2, 1}, {1, 0} };
        else {
            // Experiment 4: an explicit stage list, coarsest first, e.g. "3,2" or "1,2,2"; must sum to 5.
            int level = 5;
            const char *q = f;
            while (*q) {
                char *end; long k = strtol(q, &end, 10);
                if (end == q || k < 1 || k > level) { fprintf(stderr, "PYROWAVE_FUSED must be H0|H1|H2|H3 or a list of levels per stage summing to 5\n"); return 1; }
                fusedStages.push_back({ level, level - (int)k }); level -= (int)k;
                q = end; if (*q == ',') q++;
            }
            if (level != 0) { fprintf(stderr, "PYROWAVE_FUSED stages must sum to 5 levels\n"); return 1; }
        }
    }
    // Experiment 4: PYROWAVE_FUSED97=<stage list> runs the fused CDF 9/7 kernel instead (wavelet 97).
    bool fused97 = false;
    if (const char *f = getenv("PYROWAVE_FUSED97")) {
        if (!fusedStages.empty()) { fprintf(stderr, "PYROWAVE_FUSED and PYROWAVE_FUSED97 are exclusive\n"); return 1; }
        int level = 5; const char *q = f;
        while (*q) {
            char *end; long k = strtol(q, &end, 10);
            if (end == q || k < 1 || k > level) { fprintf(stderr, "PYROWAVE_FUSED97 must be a list of levels per stage summing to 5\n"); return 1; }
            fusedStages.push_back({ level, level - (int)k }); level -= (int)k; q = end; if (*q == ',') q++;
        }
        if (level != 0) { fprintf(stderr, "PYROWAVE_FUSED97 stages must sum to 5 levels\n"); return 1; }
        fused97 = true;
    }
    int mirrorEven = getenv("PYROWAVE_FUSED97_MIRROR_EVEN") ? atoi(getenv("PYROWAVE_FUSED97_MIRROR_EVEN")) : 1;
    int mirrorOdd = getenv("PYROWAVE_FUSED97_MIRROR_ODD") ? atoi(getenv("PYROWAVE_FUSED97_MIRROR_ODD")) : 0;
    int farEven = getenv("PYROWAVE_FUSED97_FAR_EVEN") ? atoi(getenv("PYROWAVE_FUSED97_FAR_EVEN")) : 0;
    int farOdd = getenv("PYROWAVE_FUSED97_FAR_ODD") ? atoi(getenv("PYROWAVE_FUSED97_FAR_ODD")) : 1;
    const bool fused = !fusedStages.empty();
    int fusedSwapXY = getenv("PYROWAVE_FUSED_SWAP_XY") ? atoi(getenv("PYROWAVE_FUSED_SWAP_XY")) : 0;
    int fusedSwapBands = getenv("PYROWAVE_FUSED_SWAP_BANDS") ? atoi(getenv("PYROWAVE_FUSED_SWAP_BANDS")) : 0;
    // Experiment 4 lane-utilisation test: 4 lanes per quad at the levels where the tile has fewer
    // quads than lanes (same bytes, same barriers; only which lanes do the arithmetic changes).
    int fusedWide = getenv("PYROWAVE_FUSED_WIDE") ? atoi(getenv("PYROWAVE_FUSED_WIDE")) : 0;
    VkDescriptorSetLayout fusedSetLayout = VK_NULL_HANDLE;
    VkPipelineLayout fusedPipelineLayout = VK_NULL_HANDLE;
    VkPipeline fusedPipeline = VK_NULL_HANDLE;
    VkDescriptorPool fusedPool = VK_NULL_HANDLE;
    std::vector<VkDescriptorSet> fusedSets;           // [stage * 3 + component]
    struct TempLL { VkImage image = VK_NULL_HANDLE; VkDeviceMemory memory = VK_NULL_HANDLE; VkImageView view = VK_NULL_HANDLE; int w = 0, h = 0; };
    std::vector<TempLL> tempLL;                       // [stage_index * 3 + component] for stages that do not end at level 0
    struct FusedPush { int levels, ll_layer, swap_xy, swap_bands, final_r8, tile_in, wide; };
    struct Fused97Push { int levels, ll_layer, final_r8, nominal_in, mirror_even, mirror_odd, far_even, far_odd; };
    if (fused) {
        if (!fused97 && decoderInfo.wavelet != PYROWAVE_WAVELET_HAAR) { fprintf(stderr, "PYROWAVE_FUSED needs PYROWAVE_WAVELET=haar\n"); return 1; }
        if (fused97 && decoderInfo.wavelet != PYROWAVE_WAVELET_CDF97) { fprintf(stderr, "PYROWAVE_FUSED97 needs CDF 9/7\n"); return 1; }
        if (useAhb) { fprintf(stderr, "PYROWAVE_FUSED needs plain R8 planes\n"); return 1; }
        PW_CHECK(pyrowave_decoder_set_idwt_enabled(decoder, 0));
        VkPhysicalDeviceProperties fp; vkGetPhysicalDeviceProperties(gpu, &fp);
        printf("fused %s: %zu stage(s), maxComputeSharedMemorySize %u, swap_xy %d swap_bands %d mirror %d/%d\n",
               fused97 ? "CDF 9/7" : "Haar", fusedStages.size(), fp.limits.maxComputeSharedMemorySize, fusedSwapXY, fusedSwapBands, mirrorEven, mirrorOdd);

        VkDescriptorSetLayoutBinding fb[3] = {};
        fb[0] = { 0, VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER, 1, VK_SHADER_STAGE_COMPUTE_BIT, nullptr };
        fb[1] = { 1, VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER, 5, VK_SHADER_STAGE_COMPUTE_BIT, nullptr };
        fb[2] = { 2, VK_DESCRIPTOR_TYPE_STORAGE_IMAGE, 1, VK_SHADER_STAGE_COMPUTE_BIT, nullptr };
        VkDescriptorSetLayoutCreateInfo fl = { VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO };
        fl.bindingCount = 3; fl.pBindings = fb;
        VK_CHECK(vkCreateDescriptorSetLayout(device, &fl, nullptr, &fusedSetLayout));
        VkPushConstantRange pcr = { VK_SHADER_STAGE_COMPUTE_BIT, 0, (uint32_t)std::max(sizeof(FusedPush), sizeof(Fused97Push)) };
        VkPipelineLayoutCreateInfo fpl = { VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO };
        fpl.setLayoutCount = 1; fpl.pSetLayouts = &fusedSetLayout; fpl.pushConstantRangeCount = 1; fpl.pPushConstantRanges = &pcr;
        VK_CHECK(vkCreatePipelineLayout(device, &fpl, nullptr, &fusedPipelineLayout));
        VkShaderModuleCreateInfo fsm = { VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO };
        fsm.codeSize = fused97 ? sizeof(IDWT97_FUSED_SPV) : sizeof(HAAR_FUSED_SPV); fsm.pCode = fused97 ? IDWT97_FUSED_SPV : HAAR_FUSED_SPV;
        VkShaderModule fmod; VK_CHECK(vkCreateShaderModule(device, &fsm, nullptr, &fmod));
        VkComputePipelineCreateInfo fcp = { VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO };
        fcp.stage.sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO;
        fcp.stage.stage = VK_SHADER_STAGE_COMPUTE_BIT; fcp.stage.module = fmod; fcp.stage.pName = "main";
        fcp.layout = fusedPipelineLayout;
        VK_CHECK(vkCreateComputePipelines(device, VK_NULL_HANDLE, 1, &fcp, nullptr, &fusedPipeline));
        vkDestroyShaderModule(device, fmod, nullptr);

        // Temporary LL images between stages (f16, one layer, sampled + storage).
        VkPhysicalDeviceMemoryProperties memProps; vkGetPhysicalDeviceMemoryProperties(gpu, &memProps);
        for (size_t si = 0; si + 1 < fusedStages.size(); si++) {
            int lvl = fusedStages[si].level_out;   // the LL written at this level
            for (int c = 0; c < 3; c++) {
                TempLL t; t.w = (planes[c].width + (1 << lvl) - 1) >> lvl; t.h = (planes[c].height + (1 << lvl) - 1) >> lvl;
                VkImageCreateInfo ii = { VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO };
                ii.imageType = VK_IMAGE_TYPE_2D; ii.format = VK_FORMAT_R16_SFLOAT;
                ii.extent = { (uint32_t)t.w, (uint32_t)t.h, 1u }; ii.mipLevels = 1; ii.arrayLayers = 1;
                ii.samples = VK_SAMPLE_COUNT_1_BIT; ii.tiling = VK_IMAGE_TILING_OPTIMAL;
                ii.usage = VK_IMAGE_USAGE_SAMPLED_BIT | VK_IMAGE_USAGE_STORAGE_BIT;
                ii.initialLayout = VK_IMAGE_LAYOUT_UNDEFINED;
                VK_CHECK(vkCreateImage(device, &ii, nullptr, &t.image));
                VkMemoryRequirements mr; vkGetImageMemoryRequirements(device, t.image, &mr);
                uint32_t mt = 0; for (; mt < memProps.memoryTypeCount; mt++) if ((mr.memoryTypeBits & (1u << mt)) && (memProps.memoryTypes[mt].propertyFlags & VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT)) break;
                VkMemoryAllocateInfo ma = { VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO }; ma.allocationSize = mr.size; ma.memoryTypeIndex = mt;
                VK_CHECK(vkAllocateMemory(device, &ma, nullptr, &t.memory));
                VK_CHECK(vkBindImageMemory(device, t.image, t.memory, 0));
                VkImageViewCreateInfo vi = { VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO };
                vi.image = t.image; vi.viewType = VK_IMAGE_VIEW_TYPE_2D_ARRAY; vi.format = VK_FORMAT_R16_SFLOAT;
                vi.subresourceRange = { VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1 };
                VK_CHECK(vkCreateImageView(device, &vi, nullptr, &t.view));
                tempLL.push_back(t);
            }
        }

        VkDescriptorPoolSize fps[2] = {};
        fps[0] = { VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER, (uint32_t)(6 * 3 * fusedStages.size()) };
        fps[1] = { VK_DESCRIPTOR_TYPE_STORAGE_IMAGE, (uint32_t)(3 * fusedStages.size()) };
        VkDescriptorPoolCreateInfo fpc = { VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO };
        fpc.maxSets = (uint32_t)(3 * fusedStages.size()); fpc.poolSizeCount = 2; fpc.pPoolSizes = fps;
        VK_CHECK(vkCreateDescriptorPool(device, &fpc, nullptr, &fusedPool));
        for (size_t si = 0; si < fusedStages.size(); si++) {
            for (int c = 0; c < 3; c++) {
                VkDescriptorSetAllocateInfo sa = { VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO };
                sa.descriptorPool = fusedPool; sa.descriptorSetCount = 1; sa.pSetLayouts = &fusedSetLayout;
                VkDescriptorSet set; VK_CHECK(vkAllocateDescriptorSets(device, &sa, &set));
                VkDescriptorImageInfo llInfo = {}, detailInfo[5] = {}, outInfo = {};
                llInfo.sampler = sampler; llInfo.imageLayout = VK_IMAGE_LAYOUT_GENERAL;
                if (si == 0) {
                    VK_CHECK((VkResult)0);
                    PW_CHECK(pyrowave_decoder_get_wavelet_view(decoder, c, fusedStages[si].level_in - 1, &llInfo.imageView, nullptr));
                } else {
                    llInfo.imageView = tempLL[(si - 1) * 3 + c].view;
                }
                for (int j = 0; j < 5; j++) {
                    int lvl = fusedStages[si].level_in - j;      // 5..1
                    if (lvl <= fusedStages[si].level_out) lvl = fusedStages[si].level_in;   // pad unused slots with a valid view
                    detailInfo[j].sampler = sampler; detailInfo[j].imageLayout = VK_IMAGE_LAYOUT_GENERAL;
                    PW_CHECK(pyrowave_decoder_get_wavelet_view(decoder, c, lvl - 1, &detailInfo[j].imageView, nullptr));
                }
                outInfo.imageLayout = VK_IMAGE_LAYOUT_GENERAL;
                outInfo.imageView = fusedStages[si].level_out == 0 ? planes[c].view : tempLL[si * 3 + c].view;
                VkWriteDescriptorSet w[3] = {};
                w[0] = { VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET, nullptr, set, 0, 0, 1, VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER, &llInfo, nullptr, nullptr };
                w[1] = { VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET, nullptr, set, 1, 0, 5, VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER, detailInfo, nullptr, nullptr };
                w[2] = { VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET, nullptr, set, 2, 0, 1, VK_DESCRIPTOR_TYPE_STORAGE_IMAGE, &outInfo, nullptr, nullptr };
                vkUpdateDescriptorSets(device, 3, w, 0, nullptr);
                fusedSets.push_back(set);
            }
        }
    }

    // GPU timestamps around the decode. This is T2: PyroWave's decode alone, into the R8 planes
    // it writes natively, with no conversion or cross-API handoff.
    VkQueryPoolCreateInfo queryInfo = { VK_STRUCTURE_TYPE_QUERY_POOL_CREATE_INFO };
    queryInfo.queryType = VK_QUERY_TYPE_TIMESTAMP;
    queryInfo.queryCount = 3 + 8;   // 0 start, 1 after decode, 2 after convert, 3.. after each fused stage
    VkQueryPool queryPool;
    VK_CHECK(vkCreateQueryPool(device, &queryInfo, nullptr, &queryPool));
    VkPhysicalDeviceProperties gpuProps;
    vkGetPhysicalDeviceProperties(gpu, &gpuProps);
    const double nsPerTick = gpuProps.limits.timestampPeriod;

    int iterations = 1;
    if (const char *n = getenv("PYROWAVE_ITERATIONS")) iterations = atoi(n);
    if (iterations < 1) iterations = 1;

    double bestMs = 1e9, totalMs = 0.0;
    double bestConvertMs = 1e9, totalConvertMs = 0.0;
    double bestWallMs = 1e9, totalWallMs = 0.0;
    double stageTotalMs[8] = {};   // Experiment 4: per fused stage (stage 1 counted from the decode start, i.e. dequant + stage 1)   // Experiment 3: submit -> queue idle, the standalone stand-in for submit->fence
    for (int iter = 0; iter < iterations; iter++) {
    if (iter > 0) {
        // Re-push: a decode consumes the queued frame.
        pyrowave_decoder_clear(decoder);
        PW_CHECK(pyrowave_decoder_push_packet(decoder, wave.frame.data(), wave.frame.size()));
    }
    VkCommandBufferBeginInfo beginInfo = { VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO };
    beginInfo.flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
    VK_CHECK(vkBeginCommandBuffer(decodeCmd, &beginInfo));

    // The planes were created UNDEFINED; the views declare GENERAL, and PyroWave performs no
    // layout transitions of its own in the GPU buffer paths.
    VkImageMemoryBarrier toGeneral[3] = {};
    for (int i = 0; i < 3; i++) {
        toGeneral[i].sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER;
        toGeneral[i].oldLayout = VK_IMAGE_LAYOUT_UNDEFINED;
        toGeneral[i].newLayout = VK_IMAGE_LAYOUT_GENERAL;
        toGeneral[i].srcAccessMask = 0;
        toGeneral[i].dstAccessMask =
            VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT | VK_ACCESS_SHADER_WRITE_BIT;
        toGeneral[i].srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
        toGeneral[i].dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
        toGeneral[i].image = planes[i].image;
        toGeneral[i].subresourceRange = { VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1 };
    }
    vkCmdPipelineBarrier(decodeCmd, VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT,
                         VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT
                             | VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
                         0, 0, nullptr, 0, nullptr, 3, toGeneral);

    vkCmdResetQueryPool(decodeCmd, queryPool, 0, 3 + 8);
    vkCmdWriteTimestamp(decodeCmd, VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT, queryPool, 0);

    pyrowave_device_set_command_buffer(pyro, decodeCmd);
    // acquire/release must both be NULL when a command buffer is set.
    PW_CHECK(pyrowave_decoder_decode_gpu_buffer(decoder, nullptr, nullptr, &buffers));
    pyrowave_device_set_command_buffer(pyro, VK_NULL_HANDLE);

    if (fused) {
        // Temp LL images to GENERAL once; then one dispatch round per stage per component,
        // with a compute->compute barrier between stages (the only global barriers Arm C has).
        if (iter == 0 && !tempLL.empty()) {
            std::vector<VkImageMemoryBarrier> tb(tempLL.size());
            for (size_t i = 0; i < tempLL.size(); i++) {
                tb[i] = {}; tb[i].sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER;
                tb[i].oldLayout = VK_IMAGE_LAYOUT_UNDEFINED; tb[i].newLayout = VK_IMAGE_LAYOUT_GENERAL;
                tb[i].dstAccessMask = VK_ACCESS_SHADER_WRITE_BIT | VK_ACCESS_SHADER_READ_BIT;
                tb[i].srcQueueFamilyIndex = tb[i].dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
                tb[i].image = tempLL[i].image; tb[i].subresourceRange = { VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1 };
            }
            vkCmdPipelineBarrier(decodeCmd, VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
                                 0, 0, nullptr, 0, nullptr, (uint32_t)tb.size(), tb.data());
        }
        vkCmdBindPipeline(decodeCmd, VK_PIPELINE_BIND_POINT_COMPUTE, fusedPipeline);
        for (size_t si = 0; si < fusedStages.size(); si++) {
            if (si > 0) {
                VkMemoryBarrier mb = { VK_STRUCTURE_TYPE_MEMORY_BARRIER };
                mb.srcAccessMask = VK_ACCESS_SHADER_WRITE_BIT; mb.dstAccessMask = VK_ACCESS_SHADER_READ_BIT;
                vkCmdPipelineBarrier(decodeCmd, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
                                     0, 1, &mb, 0, nullptr, 0, nullptr);
            }
            const int k = fusedStages[si].level_in - fusedStages[si].level_out;
            if (fused97) {
                Fused97Push pc = { k, 0, fusedStages[si].level_out == 0 ? 1 : 0, 32 >> k, mirrorEven, mirrorOdd, farEven, farOdd };
                vkCmdPushConstants(decodeCmd, fusedPipelineLayout, VK_SHADER_STAGE_COMPUTE_BIT, 0, sizeof(pc), &pc);
            } else {
                FusedPush pc = { k, 0, fusedSwapXY, fusedSwapBands, fusedStages[si].level_out == 0 ? 1 : 0, 32 >> k, fusedWide };
                vkCmdPushConstants(decodeCmd, fusedPipelineLayout, VK_SHADER_STAGE_COMPUTE_BIT, 0, sizeof(pc), &pc);
            }
            for (int c = 0; c < 3; c++) {
                vkCmdBindDescriptorSets(decodeCmd, VK_PIPELINE_BIND_POINT_COMPUTE, fusedPipelineLayout, 0, 1,
                                        &fusedSets[si * 3 + c], 0, nullptr);
                int ow = fusedStages[si].level_out == 0 ? planes[c].width : tempLL[si * 3 + c].w;
                int oh = fusedStages[si].level_out == 0 ? planes[c].height : tempLL[si * 3 + c].h;
                vkCmdDispatch(decodeCmd, (uint32_t)((ow + 31) / 32), (uint32_t)((oh + 31) / 32), 1);
            }
            if (si < 8)
                vkCmdWriteTimestamp(decodeCmd, VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT, queryPool, (uint32_t)(3 + si));
        }
    }

    vkCmdWriteTimestamp(decodeCmd, VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT, queryPool, 1);

    // --- T2 -> T3: the conversion pass.
    // The decode wrote the planes; make them readable and put the RGBA target in GENERAL.
    {
        VkImageMemoryBarrier barriers[4] = {};
        for (int i = 0; i < 3; i++) {
            barriers[i].sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER;
            barriers[i].oldLayout = VK_IMAGE_LAYOUT_GENERAL;
            barriers[i].newLayout = VK_IMAGE_LAYOUT_GENERAL;
            barriers[i].srcAccessMask =
                VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT | VK_ACCESS_SHADER_WRITE_BIT;
            barriers[i].dstAccessMask = VK_ACCESS_SHADER_READ_BIT;
            barriers[i].srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
            barriers[i].dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
            barriers[i].image = planes[i].image;
            barriers[i].subresourceRange = { VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1 };
        }
        barriers[3].sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER;
        barriers[3].oldLayout = iter == 0 ? VK_IMAGE_LAYOUT_UNDEFINED : VK_IMAGE_LAYOUT_GENERAL;
        barriers[3].newLayout = VK_IMAGE_LAYOUT_GENERAL;
        barriers[3].srcAccessMask = 0;
        barriers[3].dstAccessMask = VK_ACCESS_SHADER_WRITE_BIT;
        barriers[3].srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
        barriers[3].dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
        barriers[3].image = rgbaImage;
        barriers[3].subresourceRange = { VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1 };
        vkCmdPipelineBarrier(decodeCmd,
                             VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT
                                 | VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
                             VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, 0, 0, nullptr, 0, nullptr, 4,
                             barriers);
    }

    vkCmdBindPipeline(decodeCmd, VK_PIPELINE_BIND_POINT_COMPUTE, convertPipeline);
    vkCmdBindDescriptorSets(decodeCmd, VK_PIPELINE_BIND_POINT_COMPUTE, pipelineLayout, 0, 1,
                            &descSet, 0, nullptr);
    vkCmdDispatch(decodeCmd, (uint32_t)((wave.width + 7) / 8), (uint32_t)((wave.height + 7) / 8),
                  1);

    vkCmdWriteTimestamp(decodeCmd, VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT, queryPool, 2);

    VK_CHECK(vkEndCommandBuffer(decodeCmd));
    VkSubmitInfo decodeSubmit = { VK_STRUCTURE_TYPE_SUBMIT_INFO };
    decodeSubmit.commandBufferCount = 1;
    decodeSubmit.pCommandBuffers = &decodeCmd;
    auto wall0 = std::chrono::steady_clock::now();
    VK_CHECK(vkQueueSubmit(queue, 1, &decodeSubmit, VK_NULL_HANDLE));
    VK_CHECK(vkQueueWaitIdle(queue));
    double wallMs = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - wall0).count();
    if (wallMs < bestWallMs) bestWallMs = wallMs;
    totalWallMs += wallMs;

    uint64_t ticks[3 + 8] = {};
    const uint32_t nq = 3 + (uint32_t)std::min<size_t>(fusedStages.size(), 8);
    VK_CHECK(vkGetQueryPoolResults(device, queryPool, 0, nq, sizeof(ticks), ticks, sizeof(uint64_t),
                                   VK_QUERY_RESULT_64_BIT | VK_QUERY_RESULT_WAIT_BIT));
    for (size_t si = 0; si < fusedStages.size() && si < 8; si++) {
        uint64_t prev = si == 0 ? ticks[0] : ticks[3 + si - 1];
        stageTotalMs[si] += double(ticks[3 + si] - prev) * nsPerTick / 1e6;
    }
    const double ms = double(ticks[1] - ticks[0]) * nsPerTick / 1e6;
    const double convertMs = double(ticks[2] - ticks[1]) * nsPerTick / 1e6;
    totalMs += ms;
    totalConvertMs += convertMs;
    if (ms < bestMs) bestMs = ms;
    if (convertMs < bestConvertMs) bestConvertMs = convertMs;
    VK_CHECK(vkResetCommandPool(device, decodePool, 0));
    }

    printf("T2 decode      : best %.3f ms, mean %.3f ms\n", bestMs, totalMs / iterations);
    printf("T3-T2 convert  : best %.3f ms, mean %.3f ms   (the GLES bridge)\n", bestConvertMs,
           totalConvertMs / iterations);
    printf("submit->idle wall: best %.3f ms, mean %.3f ms   (CPU-observed completion of decode+convert)\n", bestWallMs, totalWallMs / iterations);
    for (size_t si = 0; si < fusedStages.size() && si < 8; si++)
        printf("fused stage %zu (levels %d->%d): mean %.3f ms%s\n", si + 1, fusedStages[si].level_in, fusedStages[si].level_out,
               stageTotalMs[si] / iterations, si == 0 ? "   (includes dequant)" : "");
    printf("over %d iteration(s)\n", iterations);

    // Without a command buffer of our own, PyroWave submitted and we need it finished before the
    // CPU reads the buffers. A device-wide wait is heavy-handed but unambiguous for a harness.
    VK_CHECK(vkQueueWaitIdle(queue));
    VK_CHECK(vkDeviceWaitIdle(device));


    // --- Read the planes back through the hardware buffer and write a y4m.
    FILE *out = fopen(argv[2], "wb");
    if (!out) {
        fprintf(stderr, "cannot open %s\n", argv[2]);
        return 1;
    }
    fprintf(out, "YUV4MPEG2 W%d H%d F%d:%d Ip A1:1 XCOLORRANGE=%s C%s\n", wave.width, wave.height,
            wave.fps_num, wave.fps_den, wave.full_range ? "FULL" : "LIMITED",
            chroma444 ? "444" : "420");
    fprintf(out, "FRAME\n");

    // Plain images need a GPU copy into host-visible memory; the AHB path can just be locked.
    VkCommandPool pool = VK_NULL_HANDLE;
    VkCommandBuffer cmd = VK_NULL_HANDLE;
    VkBuffer readbackBuffer = VK_NULL_HANDLE;
    VkDeviceMemory readbackMemory = VK_NULL_HANDLE;
    if (!useAhb) {
        VkCommandPoolCreateInfo poolInfo = { VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO };
        poolInfo.queueFamilyIndex = graphicsFamily;
        VK_CHECK(vkCreateCommandPool(device, &poolInfo, nullptr, &pool));
        VkCommandBufferAllocateInfo cmdInfo = {
            VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO
        };
        cmdInfo.commandPool = pool;
        cmdInfo.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY;
        cmdInfo.commandBufferCount = 1;
        VK_CHECK(vkAllocateCommandBuffers(device, &cmdInfo, &cmd));

        const VkDeviceSize biggest = (VkDeviceSize)wave.width * wave.height;
        VkBufferCreateInfo bufInfo = { VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO };
        bufInfo.size = biggest;
        bufInfo.usage = VK_BUFFER_USAGE_TRANSFER_DST_BIT;
        bufInfo.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
        VK_CHECK(vkCreateBuffer(device, &bufInfo, nullptr, &readbackBuffer));

        VkMemoryRequirements req;
        vkGetBufferMemoryRequirements(device, readbackBuffer, &req);
        VkPhysicalDeviceMemoryProperties memProps;
        vkGetPhysicalDeviceMemoryProperties(gpu, &memProps);
        uint32_t type = UINT32_MAX;
        for (uint32_t t = 0; t < memProps.memoryTypeCount; t++) {
            const VkMemoryPropertyFlags want = VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT
                | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT;
            if ((req.memoryTypeBits & (1u << t))
                && (memProps.memoryTypes[t].propertyFlags & want) == want) {
                type = t;
                break;
            }
        }
        if (type == UINT32_MAX) {
            fprintf(stderr, "no host-visible memory type\n");
            return 1;
        }
        VkMemoryAllocateInfo alloc = { VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO };
        alloc.allocationSize = req.size;
        alloc.memoryTypeIndex = type;
        VK_CHECK(vkAllocateMemory(device, &alloc, nullptr, &readbackMemory));
        VK_CHECK(vkBindBufferMemory(device, readbackBuffer, readbackMemory, 0));
    }

    for (int i = 0; i < 3; i++) {
        Plane &p = planes[i];

        if (!useAhb) {
            VkCommandBufferBeginInfo begin = { VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO };
            begin.flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
            VK_CHECK(vkResetCommandBuffer(cmd, 0));
            VK_CHECK(vkBeginCommandBuffer(cmd, &begin));

            // PyroWave left the plane in GENERAL, as its image view declared.
            VkImageMemoryBarrier barrier = { VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER };
            barrier.oldLayout = VK_IMAGE_LAYOUT_GENERAL;
            barrier.newLayout = VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL;
            barrier.srcAccessMask = VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT
                | VK_ACCESS_SHADER_WRITE_BIT;
            barrier.dstAccessMask = VK_ACCESS_TRANSFER_READ_BIT;
            barrier.srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
            barrier.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
            barrier.image = p.image;
            barrier.subresourceRange = { VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1 };
            vkCmdPipelineBarrier(cmd,
                                 VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT
                                     | VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
                                 VK_PIPELINE_STAGE_TRANSFER_BIT, 0, 0, nullptr, 0, nullptr, 1,
                                 &barrier);

            VkBufferImageCopy copy = {};
            copy.imageSubresource = { VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1 };
            copy.imageExtent = { (uint32_t)p.width, (uint32_t)p.height, 1u };
            vkCmdCopyImageToBuffer(cmd, p.image, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL,
                                   readbackBuffer, 1, &copy);
            VK_CHECK(vkEndCommandBuffer(cmd));

            VkSubmitInfo submit = { VK_STRUCTURE_TYPE_SUBMIT_INFO };
            submit.commandBufferCount = 1;
            submit.pCommandBuffers = &cmd;
            VK_CHECK(vkQueueSubmit(queue, 1, &submit, VK_NULL_HANDLE));
            VK_CHECK(vkQueueWaitIdle(queue));

            void *mappedBuf = nullptr;
            VK_CHECK(vkMapMemory(device, readbackMemory, 0, VK_WHOLE_SIZE, 0, &mappedBuf));
            fwrite(mappedBuf, 1, (size_t)p.width * p.height, out);
            vkUnmapMemory(device, readbackMemory);
            continue;
        }

        AHardwareBuffer_Desc desc = {};
        AHardwareBuffer_describe(p.ahb, &desc);
        void *mapped = nullptr;
        if (AHardwareBuffer_lock(p.ahb, AHARDWAREBUFFER_USAGE_CPU_READ_OFTEN, -1, nullptr,
                                 &mapped)
                != 0
            || !mapped) {
            fprintf(stderr, "AHardwareBuffer_lock failed for plane %d\n", i);
            fclose(out);
            return 1;
        }
        // stride is in pixels; the plane lives in the R channel of an RGBA8 buffer.
        const uint8_t *src = static_cast<const uint8_t *>(mapped);
        std::vector<uint8_t> row(p.width);
        for (int y = 0; y < p.height; y++) {
            const uint8_t *line = src + (size_t)y * desc.stride * 4;
            for (int x = 0; x < p.width; x++) {
                row[x] = line[x * 4];
            }
            fwrite(row.data(), 1, row.size(), out);
        }
        AHardwareBuffer_unlock(p.ahb, nullptr);
    }
    fclose(out);
    printf("wrote %s\n", argv[2]);

    pyrowave_decoder_destroy(decoder);
    pyrowave_device_destroy(pyro);
    return 0;
}
