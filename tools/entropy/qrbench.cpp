// qrbench <frame.qrf> <reference.qcf> [iterations] [local-size]: decode a QR frame on the GPU
// (qrdec.comp), check every coefficient against the dump it was encoded from, and time the decode
// with timestamp queries. Runs on the Quest (adb shell) or any Vulkan desktop.
//
// Build for the headset (NDK):
//   clang++ --target=aarch64-linux-android29 -std=c++17 -O2 -I<vulkan-headers> qrbench.cpp -lvulkan
// The shader is embedded from qrdec_spv.h:
//   glslangValidator -V --target-env vulkan1.1 --vn kQrDecSpv -o qrdec_spv.h qrdec.comp
#include <vulkan/vulkan.h>

#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iterator>
#include <utility>
#include <stdexcept>
#include <string>
#include <vector>

#include "qrdec_spv.h"

static std::vector<char> read_file(const char* path) {
    std::ifstream f(path, std::ios::binary);
    if (!f) throw std::runtime_error(std::string("cannot open ") + path);
    return std::vector<char>(std::istreambuf_iterator<char>(f), {});
}

#define VK_CHECK(x) do { VkResult r_ = (x); if (r_ != VK_SUCCESS) { std::fprintf(stderr, "%s: %d\n", #x, (int)r_); std::exit(1); } } while (0)

struct Buf { VkBuffer buffer; VkDeviceMemory memory; void* map; VkDeviceSize size; };

int main(int argc, char** argv) {
    if (argc < 3) { std::fprintf(stderr, "usage: qrbench <frame.qrf> <reference.qcf> [iterations] [local-size]\n"); return 2; }
    int iterations = argc > 3 ? std::atoi(argv[3]) : 50;
    uint32_t local = argc > 4 ? (uint32_t)std::atoi(argv[4]) : 64;

    // ---- frame ----
    std::vector<char> fr = read_file(argv[1]);
    uint32_t hdr[4];
    std::memcpy(hdr, fr.data(), 16);
    if (hdr[0] != 0x31465251u) { std::fprintf(stderr, "not a QRF1 frame\n"); return 1; }
    const uint32_t nbands = hdr[1], nblocks = hdr[2], nwords = hdr[3];
    const uint32_t kContexts = 26 * 30;
    size_t pos = 16;
    std::vector<uint32_t> cum(kContexts * 17);
    for (size_t i = 0; i < cum.size(); i++) { uint16_t v; std::memcpy(&v, fr.data() + pos + i * 2, 2); cum[i] = v; }
    pos += cum.size() * 2;
    // shader table: per context cum[0..15] as eight u32 of two u16 (cum[16] = 4096 is implicit)
    std::vector<uint32_t> entries(kContexts * 8);
    for (uint32_t c = 0; c < kContexts; c++)
        for (uint32_t t = 0; t < 8; t++) entries[c * 8 + t] = cum[c * 17 + 2 * t] | (cum[c * 17 + 2 * t + 1] << 16);
    std::vector<uint32_t> bands(nbands * 5);
    std::memcpy(bands.data(), fr.data() + pos, bands.size() * 4);
    pos += bands.size() * 4;
    std::vector<uint32_t> offsets(nblocks);
    std::memcpy(offsets.data(), fr.data() + pos, nblocks * 4);
    pos += nblocks * 4;
    std::vector<uint32_t> words((nwords + 1) / 2 + 1, 0);  // the shader reads one word ahead
    std::memcpy(words.data(), fr.data() + pos, (size_t)nwords * 2);
    std::vector<uint32_t> block_band(nblocks);
    uint64_t coefficients = 0;
    for (uint32_t b = 0; b < nbands; b++) {
        uint32_t w = bands[b * 5 + 1], h = bands[b * 5 + 2], first = bands[b * 5 + 4];
        for (uint32_t i = 0; i < (w / 32) * (h / 32); i++) block_band[first + i] = b;
        coefficients += (uint64_t)w * h;
    }
    // decode order: frame order, or longest streams first with QR_ORDER=1
    std::vector<uint32_t> order(nblocks);
    {
        std::vector<std::pair<uint32_t, uint32_t>> len;  // (words, block)
        std::vector<std::pair<uint32_t, uint32_t>> starts;
        for (uint32_t b = 0; b < nblocks; b++) if (offsets[b] != 0xffffffffu) starts.push_back({offsets[b], b});
        std::sort(starts.begin(), starts.end());
        std::vector<uint32_t> words_of(nblocks, 0);
        for (size_t i = 0; i < starts.size(); i++)
            words_of[starts[i].second] = (i + 1 < starts.size() ? starts[i + 1].first : nwords) - starts[i].first;
        for (uint32_t b = 0; b < nblocks; b++) order[b] = b;
        const char* env = std::getenv("QR_ORDER");
        if (env && std::atoi(env) != 0)
            std::stable_sort(order.begin(), order.end(), [&](uint32_t a, uint32_t b) { return words_of[a] > words_of[b]; });
        // timing only: QR_SKIP=n leaves the first n blocks of the order undecoded
        if (const char* skip = std::getenv("QR_SKIP"))
            order.erase(order.begin(), order.begin() + std::min<size_t>(std::atoi(skip), order.size()));
    }
    // timing only: QR_DUP=n decodes every block n times (n copies of the order back to back)
    if (const char* dup = std::getenv("QR_DUP")) {
        std::vector<uint32_t> one = order;
        for (int i = 1; i < std::atoi(dup); i++) order.insert(order.end(), one.begin(), one.end());
    }
    const uint32_t ndispatch = (uint32_t)order.size();
    std::printf("frame: %u bands, %u blocks, %u words (%u bytes), %llu coefficients\n", nbands, nblocks, nwords,
                nwords * 2, (unsigned long long)coefficients);

    // QR_FLAGS: the shader's flags (see qrdec.comp). With bit 0 the output is cleared with
    // vkCmdFillBuffer before the first timestamp.
    uint32_t flags = std::getenv("QR_FLAGS") ? (uint32_t)std::atoi(std::getenv("QR_FLAGS")) : 0;
    const uint64_t groups = (ndispatch + local - 1) / local;
    const VkDeviceSize out_bytes = (flags & 8) ? groups * local * 2048 : coefficients * 2;

    // ---- Vulkan ----
    VkApplicationInfo app = { VK_STRUCTURE_TYPE_APPLICATION_INFO };
    app.apiVersion = VK_API_VERSION_1_1;
    VkInstanceCreateInfo ici = { VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO };
    ici.pApplicationInfo = &app;
    VkInstance instance;
    VK_CHECK(vkCreateInstance(&ici, nullptr, &instance));
    uint32_t count = 1;
    VkPhysicalDevice gpu;
    vkEnumeratePhysicalDevices(instance, &count, &gpu);
    VkPhysicalDeviceProperties props;
    vkGetPhysicalDeviceProperties(gpu, &props);
    std::printf("device: %s, timestamp period %.2f ns\n", props.deviceName, props.limits.timestampPeriod);
    uint32_t families = 0;
    vkGetPhysicalDeviceQueueFamilyProperties(gpu, &families, nullptr);
    std::vector<VkQueueFamilyProperties> fp(families);
    vkGetPhysicalDeviceQueueFamilyProperties(gpu, &families, fp.data());
    uint32_t family = 0;
    for (uint32_t i = 0; i < families; i++) if (fp[i].queueFlags & VK_QUEUE_COMPUTE_BIT) { family = i; break; }
    float qp = 1.0f;
    VkDeviceQueueCreateInfo qi = { VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO };
    qi.queueFamilyIndex = family;
    qi.queueCount = 1;
    qi.pQueuePriorities = &qp;
    VkDeviceCreateInfo di = { VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO };
    di.queueCreateInfoCount = 1;
    di.pQueueCreateInfos = &qi;
    VkDevice device;
    VK_CHECK(vkCreateDevice(gpu, &di, nullptr, &device));
    VkQueue queue;
    vkGetDeviceQueue(device, family, 0, &queue);

    VkPhysicalDeviceMemoryProperties mp;
    vkGetPhysicalDeviceMemoryProperties(gpu, &mp);
    auto make = [&](VkDeviceSize size, const void* data, bool device_only = false) {
        Buf b{};
        b.size = std::max<VkDeviceSize>(size, 16);
        VkBufferCreateInfo bi = { VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO };
        bi.size = b.size;
        bi.usage = VK_BUFFER_USAGE_STORAGE_BUFFER_BIT | VK_BUFFER_USAGE_TRANSFER_SRC_BIT | VK_BUFFER_USAGE_TRANSFER_DST_BIT;
        VK_CHECK(vkCreateBuffer(device, &bi, nullptr, &b.buffer));
        VkMemoryRequirements mr;
        vkGetBufferMemoryRequirements(device, b.buffer, &mr);
        const VkMemoryPropertyFlags want = device_only ? VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT
                                                       : VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT;
        uint32_t type = UINT32_MAX;
        for (uint32_t i = 0; i < mp.memoryTypeCount; i++)
            if ((mr.memoryTypeBits >> i & 1) && (mp.memoryTypes[i].propertyFlags & want) == want) {
                if (device_only) {
                    // prefer a type without host access, as a real decoder's output would be
                    if (type == UINT32_MAX || !(mp.memoryTypes[i].propertyFlags & VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT)) type = i;
                } else if (type == UINT32_MAX || (mp.memoryTypes[i].propertyFlags & VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT)) {
                    type = i;
                }
            }
        VkMemoryAllocateInfo ai = { VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO };
        ai.allocationSize = mr.size;
        ai.memoryTypeIndex = type;
        VK_CHECK(vkAllocateMemory(device, &ai, nullptr, &b.memory));
        VK_CHECK(vkBindBufferMemory(device, b.buffer, b.memory, 0));
        if (!device_only) {
            VK_CHECK(vkMapMemory(device, b.memory, 0, VK_WHOLE_SIZE, 0, &b.map));
            if (data) std::memcpy(b.map, data, size);
        }
        return b;
    };
    Buf bufs[7] = {
        make(entries.size() * 4, entries.data()), make(bands.size() * 4, bands.data()),
        make(offsets.size() * 4, offsets.data()), make(block_band.size() * 4, block_band.data()),
        make(words.size() * 4, words.data()), make(out_bytes, nullptr, true),
        make(order.size() * 4, order.data()),
    };
    // the output is device-local; the check reads a host copy
    Buf staging = make(out_bytes, nullptr);

    VkDescriptorSetLayoutBinding bind[7];
    for (uint32_t i = 0; i < 7; i++) bind[i] = { i, VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, 1, VK_SHADER_STAGE_COMPUTE_BIT, nullptr };
    VkDescriptorSetLayoutCreateInfo dli = { VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO };
    dli.bindingCount = 7;
    dli.pBindings = bind;
    VkDescriptorSetLayout dsl;
    VK_CHECK(vkCreateDescriptorSetLayout(device, &dli, nullptr, &dsl));
    VkPushConstantRange pcr = { VK_SHADER_STAGE_COMPUTE_BIT, 0, 4 };
    VkPipelineLayoutCreateInfo pli = { VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO };
    pli.setLayoutCount = 1;
    pli.pSetLayouts = &dsl;
    pli.pushConstantRangeCount = 1;
    pli.pPushConstantRanges = &pcr;
    VkPipelineLayout layout;
    VK_CHECK(vkCreatePipelineLayout(device, &pli, nullptr, &layout));
    VkShaderModuleCreateInfo smi = { VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO };
    smi.codeSize = sizeof(kQrDecSpv);
    smi.pCode = kQrDecSpv;
    VkShaderModule module;
    VK_CHECK(vkCreateShaderModule(device, &smi, nullptr, &module));
    uint32_t specData[2] = { local, flags };
    VkSpecializationMapEntry sme[2] = { { 0, 0, 4 }, { 1, 4, 4 } };
    VkSpecializationInfo spec = { 2, sme, 8, specData };
    VkComputePipelineCreateInfo cpi = { VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO };
    cpi.stage = { VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO, nullptr, 0, VK_SHADER_STAGE_COMPUTE_BIT, module, "main", &spec };
    cpi.layout = layout;
    VkPipeline pipeline;
    VK_CHECK(vkCreateComputePipelines(device, VK_NULL_HANDLE, 1, &cpi, nullptr, &pipeline));

    VkDescriptorPoolSize ps = { VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, 7 };
    VkDescriptorPoolCreateInfo dpi = { VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO };
    dpi.maxSets = 1;
    dpi.poolSizeCount = 1;
    dpi.pPoolSizes = &ps;
    VkDescriptorPool pool;
    VK_CHECK(vkCreateDescriptorPool(device, &dpi, nullptr, &pool));
    VkDescriptorSetAllocateInfo dsa = { VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO };
    dsa.descriptorPool = pool;
    dsa.descriptorSetCount = 1;
    dsa.pSetLayouts = &dsl;
    VkDescriptorSet set;
    VK_CHECK(vkAllocateDescriptorSets(device, &dsa, &set));
    VkDescriptorBufferInfo dbi[7];
    VkWriteDescriptorSet wr[7];
    for (uint32_t i = 0; i < 7; i++) {
        dbi[i] = { bufs[i].buffer, 0, VK_WHOLE_SIZE };
        wr[i] = { VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET };
        wr[i].dstSet = set;
        wr[i].dstBinding = i;
        wr[i].descriptorCount = 1;
        wr[i].descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
        wr[i].pBufferInfo = &dbi[i];
    }
    vkUpdateDescriptorSets(device, 7, wr, 0, nullptr);

    VkQueryPoolCreateInfo qpi = { VK_STRUCTURE_TYPE_QUERY_POOL_CREATE_INFO };
    qpi.queryType = VK_QUERY_TYPE_TIMESTAMP;
    qpi.queryCount = 2;
    VkQueryPool qpool;
    VK_CHECK(vkCreateQueryPool(device, &qpi, nullptr, &qpool));
    VkCommandPoolCreateInfo cpci = { VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO };
    cpci.queueFamilyIndex = family;
    VkCommandPool cmdPool;
    VK_CHECK(vkCreateCommandPool(device, &cpci, nullptr, &cmdPool));
    VkCommandBufferAllocateInfo cbai = { VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO };
    cbai.commandPool = cmdPool;
    cbai.commandBufferCount = 1;
    VkCommandBuffer cmd;
    VK_CHECK(vkAllocateCommandBuffers(device, &cbai, &cmd));
    VkCommandBufferBeginInfo cbbi = { VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO };
    VK_CHECK(vkBeginCommandBuffer(cmd, &cbbi));
    vkCmdResetQueryPool(cmd, qpool, 0, 2);
    if (flags & 1) {
        vkCmdFillBuffer(cmd, bufs[5].buffer, 0, VK_WHOLE_SIZE, 0);
        VkMemoryBarrier mb = { VK_STRUCTURE_TYPE_MEMORY_BARRIER, nullptr, VK_ACCESS_TRANSFER_WRITE_BIT, VK_ACCESS_SHADER_WRITE_BIT };
        vkCmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, 0, 1, &mb, 0, nullptr, 0, nullptr);
    }
    vkCmdWriteTimestamp(cmd, VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT, qpool, 0);
    vkCmdBindPipeline(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, pipeline);
    vkCmdBindDescriptorSets(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, layout, 0, 1, &set, 0, nullptr);
    vkCmdPushConstants(cmd, layout, VK_SHADER_STAGE_COMPUTE_BIT, 0, 4, &ndispatch);
    vkCmdDispatch(cmd, (uint32_t)groups, 1, 1);
    vkCmdWriteTimestamp(cmd, VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT, qpool, 1);
    VK_CHECK(vkEndCommandBuffer(cmd));
    VkCommandBuffer copy;
    VK_CHECK(vkAllocateCommandBuffers(device, &cbai, &copy));
    VK_CHECK(vkBeginCommandBuffer(copy, &cbbi));
    VkBufferCopy region = { 0, 0, out_bytes };
    vkCmdCopyBuffer(copy, bufs[5].buffer, staging.buffer, 1, &region);
    VK_CHECK(vkEndCommandBuffer(copy));
    VkFenceCreateInfo fci = { VK_STRUCTURE_TYPE_FENCE_CREATE_INFO };
    VkFence fence;
    VK_CHECK(vkCreateFence(device, &fci, nullptr, &fence));

    std::vector<double> ms;
    for (int i = 0; i < iterations + 5; i++) {
        VkSubmitInfo si = { VK_STRUCTURE_TYPE_SUBMIT_INFO };
        si.commandBufferCount = 1;
        si.pCommandBuffers = &cmd;
        VK_CHECK(vkQueueSubmit(queue, 1, &si, fence));
        VK_CHECK(vkWaitForFences(device, 1, &fence, VK_TRUE, UINT64_MAX));
        vkResetFences(device, 1, &fence);
        uint64_t ts[2];
        VK_CHECK(vkGetQueryPoolResults(device, qpool, 0, 2, sizeof(ts), ts, 8, VK_QUERY_RESULT_64_BIT | VK_QUERY_RESULT_WAIT_BIT));
        if (i == 0) {
            // verify the first decode against the dump
            si.pCommandBuffers = &copy;
            VK_CHECK(vkQueueSubmit(queue, 1, &si, fence));
            VK_CHECK(vkWaitForFences(device, 1, &fence, VK_TRUE, UINT64_MAX));
            vkResetFences(device, 1, &fence);
            std::vector<int16_t> raster;
            const int16_t* out = (const int16_t*)staging.map;
            if (flags & 8) {
                // undo the lane interleave
                raster.assign(coefficients, 0);
                for (uint32_t t = 0; t < ndispatch; t++) {
                    uint32_t block = order[t], b = block_band[block];
                    uint32_t w = bands[b * 5 + 1], oo = bands[b * 5 + 3], li = block - bands[b * 5 + 4];
                    uint32_t bx = li % (w / 32), by = li / (w / 32);
                    for (uint32_t y = 0; y < 32; y++)
                        for (uint32_t i = 0; i < 16; i++)
                            for (uint32_t h = 0; h < 2; h++)
                                raster[oo + (by * 32 + y) * w + bx * 32 + i * 2 + h] =
                                    out[((t / local * 512ull + y * 16 + i) * local + t % local) * 2 + h];
                }
                out = raster.data();
            }
            std::vector<char> ref = read_file(argv[2]);
            uint32_t nb;
            std::memcpy(&nb, ref.data() + 4, 4);
            size_t rp = 8;
            uint64_t o = 0, bad = 0;
            for (uint32_t b = 0; b < nb; b++) {
                uint32_t h3[3];
                std::memcpy(h3, ref.data() + rp, 12);
                rp += 12;
                const int16_t* d = (const int16_t*)(ref.data() + rp);
                for (uint64_t k = 0; k < (uint64_t)h3[1] * h3[2]; k++, o++) bad += out[o] != d[k];
                rp += (size_t)h3[1] * h3[2] * 2;
            }
            std::printf("verify: %llu of %llu coefficients differ\n", (unsigned long long)bad, (unsigned long long)o);
            if (bad && !(flags & 4) && !std::getenv("QR_SKIP")) return 1;
        }
        if (i >= 5) ms.push_back((ts[1] - ts[0]) * props.limits.timestampPeriod * 1e-6);
    }
    std::sort(ms.begin(), ms.end());
    std::printf("flags %u, order %s: ", flags, std::getenv("QR_ORDER") ? std::getenv("QR_ORDER") : "0");
    std::printf("decode (local size %u): p10 %.3f  p50 %.3f  p90 %.3f  max %.3f ms over %zu runs\n", local,
                ms[ms.size() / 10], ms[ms.size() / 2], ms[ms.size() * 9 / 10], ms.back(), ms.size());
    return 0;
}
