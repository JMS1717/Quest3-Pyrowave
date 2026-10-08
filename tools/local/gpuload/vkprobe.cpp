// vkprobe: how long a tiny Vulkan compute job takes from submit to completion on each queue kind,
// while something else (a game, or gpuload) keeps the GPU busy. Answers whether a queue's global
// priority lets the streamer's work run beside a game instead of behind it.
//
//   vkprobe.exe [--adapter 7900] [--samples 300] [--interval-ms 9.7]
//
// Build (x64 Developer prompt, Vulkan SDK):
//   cl /O2 /EHsc vkprobe.cpp /I%VULKAN_SDK%\Include %VULKAN_SDK%\Lib\vulkan-1.lib
// The shader is embedded as SPIR-V for: layout(local_size_x=64) in; layout(set=0,binding=0) buffer B
// { uint v[]; }; void main() { v[gl_GlobalInvocationID.x] += 1u; }
#include <vulkan/vulkan.h>

#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <thread>
#include <vector>

#include "vkprobe_spv.h"

struct QueueChoice {
    const char* name;
    uint32_t family;
    VkQueueGlobalPriorityKHR priority;
};

static VkDevice MakeDevice(VkPhysicalDevice gpu, uint32_t family, VkQueueGlobalPriorityKHR priority, bool usePriority) {
    float qp = 1.0f;
    VkDeviceQueueGlobalPriorityCreateInfoKHR gp = { VK_STRUCTURE_TYPE_DEVICE_QUEUE_GLOBAL_PRIORITY_CREATE_INFO_KHR };
    gp.globalPriority = priority;
    VkDeviceQueueCreateInfo qi = { VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO };
    qi.pNext = usePriority ? &gp : nullptr;
    qi.queueFamilyIndex = family;
    qi.queueCount = 1;
    qi.pQueuePriorities = &qp;
    const char* ext = VK_KHR_GLOBAL_PRIORITY_EXTENSION_NAME;
    VkDeviceCreateInfo di = { VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO };
    di.queueCreateInfoCount = 1;
    di.pQueueCreateInfos = &qi;
    di.enabledExtensionCount = usePriority ? 1 : 0;
    di.ppEnabledExtensionNames = &ext;
    VkDevice device = VK_NULL_HANDLE;
    VkResult r = vkCreateDevice(gpu, &di, nullptr, &device);
    if (r != VK_SUCCESS) {
        printf("  vkCreateDevice: %d\n", (int)r);
        return VK_NULL_HANDLE;
    }
    return device;
}

static void Probe(VkPhysicalDevice gpu, const QueueChoice& choice, int samples, double intervalMs) {
    VkDevice device = MakeDevice(gpu, choice.family, choice.priority, choice.priority != VK_QUEUE_GLOBAL_PRIORITY_MEDIUM_KHR);
    if (!device) {
        printf("%-22s refused\n", choice.name);
        return;
    }
    VkQueue queue;
    vkGetDeviceQueue(device, choice.family, 0, &queue);

    VkPhysicalDeviceMemoryProperties mp;
    vkGetPhysicalDeviceMemoryProperties(gpu, &mp);
    VkBufferCreateInfo bi = { VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO };
    bi.size = 64 * 4 * 64;
    bi.usage = VK_BUFFER_USAGE_STORAGE_BUFFER_BIT;
    VkBuffer buffer;
    vkCreateBuffer(device, &bi, nullptr, &buffer);
    VkMemoryRequirements mr;
    vkGetBufferMemoryRequirements(device, buffer, &mr);
    uint32_t type = 0;
    for (uint32_t i = 0; i < mp.memoryTypeCount; i++)
        if ((mr.memoryTypeBits >> i) & 1) { type = i; if (mp.memoryTypes[i].propertyFlags & VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT) break; }
    VkMemoryAllocateInfo ai = { VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO };
    ai.allocationSize = mr.size;
    ai.memoryTypeIndex = type;
    VkDeviceMemory memory;
    vkAllocateMemory(device, &ai, nullptr, &memory);
    vkBindBufferMemory(device, buffer, memory, 0);

    VkDescriptorSetLayoutBinding b = { 0, VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, 1, VK_SHADER_STAGE_COMPUTE_BIT };
    VkDescriptorSetLayoutCreateInfo dli = { VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO };
    dli.bindingCount = 1;
    dli.pBindings = &b;
    VkDescriptorSetLayout dsl;
    vkCreateDescriptorSetLayout(device, &dli, nullptr, &dsl);
    VkPipelineLayoutCreateInfo pli = { VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO };
    pli.setLayoutCount = 1;
    pli.pSetLayouts = &dsl;
    VkPipelineLayout layout;
    vkCreatePipelineLayout(device, &pli, nullptr, &layout);
    VkShaderModuleCreateInfo smi = { VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO };
    smi.codeSize = sizeof(kProbeSpv);
    smi.pCode = kProbeSpv;
    VkShaderModule module;
    vkCreateShaderModule(device, &smi, nullptr, &module);
    VkComputePipelineCreateInfo cpi = { VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO };
    cpi.stage = { VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO, nullptr, 0, VK_SHADER_STAGE_COMPUTE_BIT, module, "main" };
    cpi.layout = layout;
    VkPipeline pipeline;
    vkCreateComputePipelines(device, VK_NULL_HANDLE, 1, &cpi, nullptr, &pipeline);

    VkDescriptorPoolSize ps = { VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, 1 };
    VkDescriptorPoolCreateInfo dpi = { VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO };
    dpi.maxSets = 1;
    dpi.poolSizeCount = 1;
    dpi.pPoolSizes = &ps;
    VkDescriptorPool pool;
    vkCreateDescriptorPool(device, &dpi, nullptr, &pool);
    VkDescriptorSetAllocateInfo dsa = { VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO };
    dsa.descriptorPool = pool;
    dsa.descriptorSetCount = 1;
    dsa.pSetLayouts = &dsl;
    VkDescriptorSet set;
    vkAllocateDescriptorSets(device, &dsa, &set);
    VkDescriptorBufferInfo dbi = { buffer, 0, VK_WHOLE_SIZE };
    VkWriteDescriptorSet w = { VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET };
    w.dstSet = set;
    w.descriptorCount = 1;
    w.descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
    w.pBufferInfo = &dbi;
    vkUpdateDescriptorSets(device, 1, &w, 0, nullptr);

    VkCommandPoolCreateInfo cpci = { VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO };
    cpci.queueFamilyIndex = choice.family;
    VkCommandPool cmdPool;
    vkCreateCommandPool(device, &cpci, nullptr, &cmdPool);
    VkCommandBufferAllocateInfo cbai = { VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO };
    cbai.commandPool = cmdPool;
    cbai.commandBufferCount = 1;
    VkCommandBuffer cmd;
    vkAllocateCommandBuffers(device, &cbai, &cmd);
    VkCommandBufferBeginInfo cbbi = { VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO };
    vkBeginCommandBuffer(cmd, &cbbi);
    vkCmdBindPipeline(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, pipeline);
    vkCmdBindDescriptorSets(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, layout, 0, 1, &set, 0, nullptr);
    vkCmdDispatch(cmd, 64, 1, 1);
    vkEndCommandBuffer(cmd);

    VkFenceCreateInfo fci = { VK_STRUCTURE_TYPE_FENCE_CREATE_INFO };
    VkFence fence;
    vkCreateFence(device, &fci, nullptr, &fence);
    std::vector<double> ms;
    for (int i = 0; i < samples + 10; i++) {
        VkSubmitInfo si = { VK_STRUCTURE_TYPE_SUBMIT_INFO };
        si.commandBufferCount = 1;
        si.pCommandBuffers = &cmd;
        auto t0 = std::chrono::steady_clock::now();
        vkQueueSubmit(queue, 1, &si, fence);
        vkWaitForFences(device, 1, &fence, VK_TRUE, UINT64_MAX);
        auto t1 = std::chrono::steady_clock::now();
        vkResetFences(device, 1, &fence);
        if (i >= 10) ms.push_back(std::chrono::duration<double, std::milli>(t1 - t0).count());
        std::this_thread::sleep_for(std::chrono::duration<double, std::milli>(intervalMs));
    }
    std::sort(ms.begin(), ms.end());
    printf("%-22s p50 %.3f  p90 %.3f  p99 %.3f  max %.3f ms\n", choice.name, ms[ms.size() / 2],
           ms[ms.size() * 9 / 10], ms[ms.size() * 99 / 100], ms.back());
    fflush(stdout);
    vkDeviceWaitIdle(device);
    vkDestroyDevice(device, nullptr);  // the rest is reclaimed with the device for a probe
}

int main(int argc, char** argv) {
    std::string adapterName = "7900";
    int samples = 300;
    double intervalMs = 9.7;
    for (int i = 1; i + 1 < argc; i += 2) {
        if (!strcmp(argv[i], "--adapter")) adapterName = argv[i + 1];
        else if (!strcmp(argv[i], "--samples")) samples = atoi(argv[i + 1]);
        else if (!strcmp(argv[i], "--interval-ms")) intervalMs = atof(argv[i + 1]);
    }
    VkApplicationInfo app = { VK_STRUCTURE_TYPE_APPLICATION_INFO };
    app.apiVersion = VK_API_VERSION_1_2;
    VkInstanceCreateInfo ici = { VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO };
    ici.pApplicationInfo = &app;
    VkInstance instance;
    if (vkCreateInstance(&ici, nullptr, &instance) != VK_SUCCESS) return 1;
    uint32_t count = 0;
    vkEnumeratePhysicalDevices(instance, &count, nullptr);
    std::vector<VkPhysicalDevice> gpus(count);
    vkEnumeratePhysicalDevices(instance, &count, gpus.data());
    VkPhysicalDevice gpu = VK_NULL_HANDLE;
    for (auto g : gpus) {
        VkPhysicalDeviceProperties p;
        vkGetPhysicalDeviceProperties(g, &p);
        if (strstr(p.deviceName, adapterName.c_str())) { gpu = g; printf("device: %s\n", p.deviceName); break; }
    }
    if (!gpu) return 1;
    uint32_t families = 0;
    vkGetPhysicalDeviceQueueFamilyProperties(gpu, &families, nullptr);
    std::vector<VkQueueFamilyProperties> fp(families);
    vkGetPhysicalDeviceQueueFamilyProperties(gpu, &families, fp.data());
    uint32_t graphics = 0, compute = 0;
    for (uint32_t i = 0; i < families; i++) {
        if (fp[i].queueFlags & VK_QUEUE_GRAPHICS_BIT) { graphics = i; break; }
    }
    for (uint32_t i = 0; i < families; i++) {
        if ((fp[i].queueFlags & VK_QUEUE_COMPUTE_BIT) && !(fp[i].queueFlags & VK_QUEUE_GRAPHICS_BIT)) { compute = i; break; }
    }
    const QueueChoice choices[] = {
        { "graphics/medium", graphics, VK_QUEUE_GLOBAL_PRIORITY_MEDIUM_KHR },
        { "graphics/high", graphics, VK_QUEUE_GLOBAL_PRIORITY_HIGH_KHR },
        { "compute/medium", compute, VK_QUEUE_GLOBAL_PRIORITY_MEDIUM_KHR },
        { "compute/high", compute, VK_QUEUE_GLOBAL_PRIORITY_HIGH_KHR },
        { "compute/realtime", compute, VK_QUEUE_GLOBAL_PRIORITY_REALTIME_KHR },
    };
    for (auto& c : choices) Probe(gpu, c, samples, intervalMs);
    vkDestroyInstance(instance, nullptr);
    return 0;
}
