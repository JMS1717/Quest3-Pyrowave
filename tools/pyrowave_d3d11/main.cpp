// D3D11 -> PyroWave encode harness.
//
// This is the core of the planned VideoEncoderPyroWave, developed standalone because a mistake
// here costs a 10 s rebuild rather than a 15 min ALVR streamer rebuild. The flow mirrors what
// ALVR's encoder will do exactly: take a D3D11 NV12 texture, hand it to PyroWave as an imported
// external image, encode, packetize. The only thing the harness adds is reading the frame from a
// y4m and writing a .wave, so the result can be decoded by pyrowave-decode and scored.
//
// ALVR's own textures are NOT created shared, so -- like VideoEncoderNVENC, which copies into its
// own NVENC input texture -- we own a shared NV12 texture and CopyResource into it.
//
// Usage: pyrowave_d3d11 <in.y4m> <out.wave> <max_bytes_per_frame> [frame_count]

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <d3d11_4.h>
#include <dxgi1_2.h>
#include <vulkan/vulkan.h>
#include "pyrowave.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

#define HR(x) do { HRESULT _hr = (x); if (FAILED(_hr)) { \
    fprintf(stderr, "FAILED %s -> 0x%08lx (line %d)\n", #x, (unsigned long)_hr, __LINE__); \
    return 1; } } while (0)

#define PW(x) do { pyrowave_result _r = (x); if (_r != PYROWAVE_SUCCESS) { \
    fprintf(stderr, "FAILED %s -> %d (line %d)\n", #x, (int)_r, __LINE__); \
    return 1; } } while (0)

namespace {

// Minimal y4m reader: 8-bit I420 only, which is what our capture pipeline produces.
struct Y4M {
    FILE *f = nullptr;
    int width = 0, height = 0, fps_num = 30, fps_den = 1;
    bool full_range = false;
    bool chroma444 = false;

    int chroma_width() const { return chroma444 ? width : width / 2; }
    int chroma_height() const { return chroma444 ? height : height / 2; }

    bool open(const char *path) {
        f = fopen(path, "rb");
        if (!f) { fprintf(stderr, "cannot open %s\n", path); return false; }
        std::string header;
        for (int c; (c = fgetc(f)) != EOF && c != '\n'; ) header.push_back(char(c));
        if (header.rfind("YUV4MPEG2", 0) != 0) { fprintf(stderr, "not a y4m\n"); return false; }
        size_t pos = 0;
        while ((pos = header.find(' ', pos)) != std::string::npos) {
            ++pos;
            if (pos >= header.size()) break;
            char tag = header[pos];
            const char *v = header.c_str() + pos + 1;
            if (tag == 'W') width = atoi(v);
            else if (tag == 'H') height = atoi(v);
            else if (tag == 'F') { fps_num = atoi(v); const char *c = strchr(v, ':'); if (c) fps_den = atoi(c + 1); }
            else if (tag == 'C') {
                if (strncmp(v, "444", 3) == 0) chroma444 = true;
                else if (strncmp(v, "420", 3) != 0) {
                    fprintf(stderr, "only 8-bit 420 or 444 supported, got C%s\n", v);
                    return false;
                }
            }
        }
        if (width <= 0 || height <= 0) { fprintf(stderr, "bad dimensions\n"); return false; }
        return true;
    }

    // Reads one frame's three planes at their native size. 4:4:4 chroma is full resolution,
    // which is the case the NV12 path below cannot represent at all.
    bool read_planes(std::vector<uint8_t> &y, std::vector<uint8_t> &cb, std::vector<uint8_t> &cr) {
        std::string marker;
        for (int c; (c = fgetc(f)) != EOF && c != '\n'; ) marker.push_back(char(c));
        if (marker.rfind("FRAME", 0) != 0) return false;
        const size_t luma = size_t(width) * height;
        const size_t chroma = size_t(chroma_width()) * chroma_height();
        y.resize(luma); cb.resize(chroma); cr.resize(chroma);
        if (fread(y.data(), 1, luma, f) != luma) return false;
        if (fread(cb.data(), 1, chroma, f) != chroma) return false;
        if (fread(cr.data(), 1, chroma, f) != chroma) return false;
        return true;
    }

    // Reads one frame as I420 and interleaves chroma into NV12 layout.
    bool read_nv12(std::vector<uint8_t> &y, std::vector<uint8_t> &uv) {
        std::string marker;
        for (int c; (c = fgetc(f)) != EOF && c != '\n'; ) marker.push_back(char(c));
        if (marker.rfind("FRAME", 0) != 0) return false;
        const size_t luma = size_t(width) * height;
        const size_t chroma = luma / 4;
        y.resize(luma);
        std::vector<uint8_t> u(chroma), v(chroma);
        if (fread(y.data(), 1, luma, f) != luma) return false;
        if (fread(u.data(), 1, chroma, f) != chroma) return false;
        if (fread(v.data(), 1, chroma, f) != chroma) return false;
        uv.resize(chroma * 2);
        for (size_t i = 0; i < chroma; i++) { uv[2 * i] = u[i]; uv[2 * i + 1] = v[i]; }
        return true;
    }
};

} // namespace

int main(int argc, char **argv) {
    if (argc < 4) {
        fprintf(stderr, "usage: %s <in.y4m> <out.wave> <max_bytes_per_frame> [frame_count]\n", argv[0]);
        return 1;
    }
    const char *in_path = argv[1];
    const char *out_path = argv[2];
    const size_t max_bytes = strtoull(argv[3], nullptr, 0);
    const int frame_limit = argc > 4 ? atoi(argv[4]) : 0;
    // PYROWAVE_CPU_PATH=1 encodes the identical NV12 bytes through the CPU entry point instead of
    // the imported D3D11 image. Same data, different pyrowave path -- bisects "is my NV12 wrong"
    // from "is the 2-plane GPU view path wrong".
    const bool cpu_path = [] {
        const char *e = getenv("PYROWAVE_CPU_PATH");
        return e && *e && *e != '0';
    }();
    printf("encode path: %s\n", cpu_path ? "CPU (NV12 buffers)" : "GPU (imported D3D11 image)");

    Y4M y4m;
    if (!y4m.open(in_path)) return 1;
    const int width = y4m.width, height = y4m.height;
    printf("input %dx%d  %d/%d fps  budget %zu bytes/frame\n",
           width, height, y4m.fps_num, y4m.fps_den, max_bytes);

    // --- D3D11, on the same adapter PyroWave will be matched to by LUID.
    IDXGIFactory1 *factory = nullptr;
    IDXGIAdapter *adapter = nullptr;
    HR(CreateDXGIFactory1(__uuidof(IDXGIFactory1), (void **)&factory));
    HR(factory->EnumAdapters(0, &adapter));
    DXGI_ADAPTER_DESC adapter_desc;
    HR(adapter->GetDesc(&adapter_desc));
    wprintf(L"adapter: %ls\n", adapter_desc.Description);

    ID3D11Device *device = nullptr;
    ID3D11DeviceContext *context = nullptr;
    HR(D3D11CreateDevice(adapter, D3D_DRIVER_TYPE_UNKNOWN, nullptr, 0, nullptr, 0,
                         D3D11_SDK_VERSION, &device, nullptr, &context));
    ID3D11Device5 *device5 = nullptr;
    ID3D11DeviceContext4 *context4 = nullptr;
    HR(device->QueryInterface(__uuidof(ID3D11Device5), (void **)&device5));
    HR(context->QueryInterface(__uuidof(ID3D11DeviceContext4), (void **)&context4));

    static_assert(sizeof(LUID) == sizeof(pyrowave_luid), "LUID size mismatch");
    pyrowave_device pyro = nullptr;
    PW(pyrowave_create_device_by_compat(
        0, 0, nullptr, nullptr,
        reinterpret_cast<pyrowave_luid *>(&adapter_desc.AdapterLuid), &pyro));
    printf("pyrowave device created (LUID-matched)\n");

    // --- The shared NV12 texture PyroWave reads, and a staging texture to fill it from.
    D3D11_TEXTURE2D_DESC desc = {};
    desc.Width = width;
    desc.Height = height;
    desc.MipLevels = 1;
    desc.ArraySize = 1;
    desc.Format = DXGI_FORMAT_NV12;
    desc.SampleDesc.Count = 1;
    desc.Usage = D3D11_USAGE_DEFAULT;
    desc.BindFlags = D3D11_BIND_SHADER_RESOURCE;
    desc.MiscFlags = D3D11_RESOURCE_MISC_SHARED | D3D11_RESOURCE_MISC_SHARED_NTHANDLE;
    ID3D11Texture2D *shared_tex = nullptr;
    HR(device5->CreateTexture2D(&desc, nullptr, &shared_tex));

    D3D11_TEXTURE2D_DESC staging_desc = desc;
    staging_desc.Usage = D3D11_USAGE_STAGING;
    staging_desc.BindFlags = 0;
    staging_desc.MiscFlags = 0;
    staging_desc.CPUAccessFlags = D3D11_CPU_ACCESS_WRITE;
    ID3D11Texture2D *staging_tex = nullptr;
    HR(device5->CreateTexture2D(&staging_desc, nullptr, &staging_tex));

    // PYROWAVE_READBACK=1 copies the shared texture back and dumps it as a y4m, to prove whether
    // the texture PyroWave reads actually holds the frame. Attributes chroma faults to the upload
    // rather than to the codec.
    const bool readback_mode = [] {
        const char *e = getenv("PYROWAVE_READBACK");
        return e && *e && *e != '0';
    }();
    D3D11_TEXTURE2D_DESC rb_desc = staging_desc;
    rb_desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
    ID3D11Texture2D *readback_tex = nullptr;
    if (readback_mode)
        HR(device5->CreateTexture2D(&rb_desc, nullptr, &readback_tex));
    FILE *rb_out = nullptr;
    if (readback_mode) {
        rb_out = fopen("readback.y4m", "wb");
        if (!rb_out) { fprintf(stderr, "cannot open readback.y4m\n"); return 1; }
        fprintf(rb_out, "YUV4MPEG2 W%d H%d F%d:%d Ip A1:1 C420mpeg2\n",
                width, height, y4m.fps_num, y4m.fps_den);
    }

    // --- Import the shared texture as a PyroWave external image.
    HANDLE shared_handle = nullptr;
    IDXGIResource1 *res1 = nullptr;
    HR(shared_tex->QueryInterface(__uuidof(IDXGIResource1), (void **)&res1));
    HR(res1->CreateSharedHandle(nullptr, GENERIC_ALL, nullptr, &shared_handle));

    VkImageCreateInfo ici = { VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO };
    ici.imageType = VK_IMAGE_TYPE_2D;
    ici.extent = { uint32_t(width), uint32_t(height), 1u };
    ici.mipLevels = 1;
    ici.arrayLayers = 1;
    // MUTABLE is required to take per-plane views of a planar format.
    ici.flags = VK_IMAGE_CREATE_MUTABLE_FORMAT_BIT;
    ici.usage = VK_IMAGE_USAGE_SAMPLED_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT
              | VK_IMAGE_USAGE_TRANSFER_DST_BIT;
    ici.format = VK_FORMAT_G8_B8R8_2PLANE_420_UNORM; // matches DXGI_FORMAT_NV12
    ici.samples = VK_SAMPLE_COUNT_1_BIT;
    ici.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
    ici.tiling = VK_IMAGE_TILING_OPTIMAL;

    pyrowave_image_create_info img_info = {};
    img_info.device = pyro;
    img_info.external_handle = (pyrowave_os_handle)shared_handle;
    img_info.handle_type = VK_EXTERNAL_MEMORY_HANDLE_TYPE_D3D11_TEXTURE_BIT;
    img_info.image_create_info = &ici;
    pyrowave_image pyro_img = nullptr;
    PW(pyrowave_image_create(&img_info, &pyro_img));
    printf("imported D3D11 NV12 texture as pyrowave_image\n");

    // NV12 is 2-plane; the helper synthesises the third plane with a component swizzle.
    pyrowave_gpu_buffers buffers = {};
    const VkImageAspectFlagBits aspects[3] = {
        VK_IMAGE_ASPECT_PLANE_0_BIT, VK_IMAGE_ASPECT_PLANE_1_BIT, VK_IMAGE_ASPECT_PLANE_2_BIT
    };
    for (int i = 0; i < 3; i++)
        PW(pyrowave_image_get_image_view(pyro_img, aspects[i], VK_IMAGE_USAGE_SAMPLED_BIT,
                                         &buffers.planes[i]));
    // PYROWAVE_NV12_HALF=1 rewrites the NV12 chroma views to the chroma plane's own extent.
    // The helper reports the luma extent for these, which is what the docs specify for a plane
    // view -- but native extents are what the separate-image path needs, so this tests whether
    // the helper's value is right for 2-plane chroma.
    if (getenv("PYROWAVE_NV12_HALF")) {
        for (int i = 1; i < 3; i++) {
            buffers.planes[i].width = uint32_t(width / 2);
            buffers.planes[i].height = uint32_t(height / 2);
        }
        printf("NV12 chroma views overridden to %dx%d\n", width / 2, height / 2);
    }
    for (int i = 0; i < 3; i++) {
        const pyrowave_image_view &v = buffers.planes[i];
        printf("plane[%d]: %ux%u img_fmt=%d view_fmt=%d aspect=0x%x swizzle=%d layout=%d mip=%u layer=%u\n",
               i, v.width, v.height, (int)v.image_format, (int)v.view_format,
               (unsigned)v.aspect, (int)v.swizzle, (int)v.layout, v.mip_level, v.layer);
    }

    // PYROWAVE_3PLANE=1 imports three separate single-component textures (Y full-res, Cb and Cr
    // at half-res) instead of one NV12 texture. This is the layout the working CLI uses, so it
    // distinguishes "imported images are broken" from "2-plane chroma views are broken".
    const bool three_plane = [] {
        const char *e = getenv("PYROWAVE_3PLANE");
        return e && *e && *e != '0';
    }();
    ID3D11Texture2D *plane_tex[3] = {};
    ID3D11Texture2D *plane_staging[3] = {};
    if (three_plane) {
        for (int i = 0; i < 3; i++) {
            D3D11_TEXTURE2D_DESC pd = desc;
            pd.Format = DXGI_FORMAT_R8_UNORM;
            pd.Width = i == 0 ? width : y4m.chroma_width();
            pd.Height = i == 0 ? height : y4m.chroma_height();
            HR(device5->CreateTexture2D(&pd, nullptr, &plane_tex[i]));
            D3D11_TEXTURE2D_DESC sd = pd;
            sd.Usage = D3D11_USAGE_STAGING;
            sd.BindFlags = 0;
            sd.MiscFlags = 0;
            sd.CPUAccessFlags = D3D11_CPU_ACCESS_WRITE;
            HR(device5->CreateTexture2D(&sd, nullptr, &plane_staging[i]));
        }
        for (int i = 0; i < 3; i++) {
            HANDLE h = nullptr;
            IDXGIResource1 *r1 = nullptr;
            HR(plane_tex[i]->QueryInterface(__uuidof(IDXGIResource1), (void **)&r1));
            HR(r1->CreateSharedHandle(nullptr, GENERIC_ALL, nullptr, &h));
            VkImageCreateInfo pi = ici;
            pi.format = VK_FORMAT_R8_UNORM;
            pi.flags = 0;
            pi.extent = { uint32_t(i == 0 ? width : y4m.chroma_width()),
                          uint32_t(i == 0 ? height : y4m.chroma_height()), 1u };
            pyrowave_image_create_info pinfo = {};
            pinfo.device = pyro;
            pinfo.external_handle = (pyrowave_os_handle)h;
            pinfo.handle_type = VK_EXTERNAL_MEMORY_HANDLE_TYPE_D3D11_TEXTURE_BIT;
            pinfo.image_create_info = &pi;
            pyrowave_image pimg = nullptr;
            PW(pyrowave_image_create(&pinfo, &pimg));
            PW(pyrowave_image_get_image_view(pimg, VK_IMAGE_ASPECT_PLANE_0_BIT,
                                             VK_IMAGE_USAGE_SAMPLED_BIT, &buffers.planes[i]));
            // PYROWAVE_LUMA_EXTENT=1 forces the chroma views to report the luma extent. The
            // docs specify that for a plane view of a planar image; for three separate images
            // the natural reading is each image's own extent. Which one pyrowave wants is the
            // thing under test.
            if (getenv("PYROWAVE_LUMA_EXTENT")) {
                buffers.planes[i].width = uint32_t(width);
                buffers.planes[i].height = uint32_t(height);
            }
            printf("3plane[%d]: %ux%u view_fmt=%d aspect=0x%x swz=%d layout=%d\n", i,
                   buffers.planes[i].width, buffers.planes[i].height,
                   (int)buffers.planes[i].view_format, (unsigned)buffers.planes[i].aspect,
                   (int)buffers.planes[i].swizzle, (int)buffers.planes[i].layout);
        }
    }

    pyrowave_encoder_create_info enc_info = {};
    enc_info.device = pyro;
    enc_info.width = width;
    enc_info.height = height;
    enc_info.chroma = y4m.chroma444 ? PYROWAVE_CHROMA_SUBSAMPLING_444
                                    : PYROWAVE_CHROMA_SUBSAMPLING_420;
    printf("encoder chroma: %s\n", y4m.chroma444 ? "4:4:4" : "4:2:0");
    if (y4m.chroma444 && (cpu_path || readback_mode)) {
        // Both of those paths go through NV12, which cannot represent full-resolution chroma.
        // Failing loudly beats quietly measuring the wrong thing.
        fprintf(stderr, "PYROWAVE_CPU_PATH and PYROWAVE_READBACK are 4:2:0 only\n");
        return 1;
    }
    if (y4m.chroma444 && !three_plane) {
        fprintf(stderr, "4:4:4 needs PYROWAVE_3PLANE=1; NV12 cannot carry it\n");
        return 1;
    }
    pyrowave_encoder enc = nullptr;
    PW(pyrowave_encoder_create(&enc_info, &enc));

    // Orders the D3D11 copy before the Vulkan read. A CPU wait is heavier than a shared
    // fence imported as a timeline semaphore, but it is unambiguous, and the harness exists to
    // establish correctness first.
    ID3D11Fence *fence = nullptr;
    HR(device5->CreateFence(0, D3D11_FENCE_FLAG_NONE, __uuidof(ID3D11Fence), (void **)&fence));
    HANDLE fence_event = CreateEventW(nullptr, FALSE, FALSE, nullptr);
    uint64_t fence_value = 0;

    FILE *out = fopen(out_path, "wb");
    if (!out) { fprintf(stderr, "cannot open %s\n", out_path); return 1; }
    fwrite("PYROWAVE", 1, 8, out);
    const int32_t params[8] = {
        width, height,
        y4m.chroma444 ? 1 : 0, // YUV4MPEGFile::Format: YUV444P / YUV420P
        y4m.chroma444 ? 1 : 0, // ChromaSubsampling: Chroma444 / Chroma420
        y4m.full_range ? 1 : 0,
        y4m.fps_num, y4m.fps_den, 0
    };
    fwrite(params, sizeof(params), 1, out);

    std::vector<uint8_t> bitstream(max_bytes * 2 + (1u << 20));
    std::vector<uint8_t> y, uv;
    int frames = 0;
    size_t total_bytes = 0;

    std::vector<uint8_t> cb, cr;
    while (y4m.read_planes(y, cb, cr)) {
        // NV12 can only carry 4:2:0, so build it from the planes and only upload it when the
        // source is subsampled. The 3-plane path handles both.
        if (!y4m.chroma444) {
            uv.resize(cb.size() * 2);
            for (size_t i = 0; i < cb.size(); i++) { uv[2 * i] = cb[i]; uv[2 * i + 1] = cr[i]; }
        }

        D3D11_MAPPED_SUBRESOURCE mapped;
        if (!y4m.chroma444) {
        HR(context->Map(staging_tex, 0, D3D11_MAP_WRITE, 0, &mapped));
        uint8_t *dst = static_cast<uint8_t *>(mapped.pData);
        for (int r = 0; r < height; r++)
            memcpy(dst + size_t(r) * mapped.RowPitch, y.data() + size_t(r) * width, width);
        uint8_t *dst_uv = dst + size_t(mapped.RowPitch) * height;
        for (int r = 0; r < height / 2; r++)
            memcpy(dst_uv + size_t(r) * mapped.RowPitch, uv.data() + size_t(r) * width, width);
        if (frames == 0) {
            // The UV plane offset is the one thing about the NV12 staging layout that is not
            // guaranteed; print it rather than trust it.
            printf("staging: RowPitch=%u DepthPitch=%u  expected_uv_offset=%zu  "
                   "depth_if_tight=%zu\n",
                   mapped.RowPitch, mapped.DepthPitch,
                   size_t(mapped.RowPitch) * height,
                   size_t(mapped.RowPitch) * height * 3 / 2);
        }
        context->Unmap(staging_tex, 0);
        }

        if (three_plane) {
            const int chroma_w = y4m.chroma_width(), chroma_h = y4m.chroma_height();
            const uint8_t *srcs[3] = { y.data(), cb.data(), cr.data() };
            for (int i = 0; i < 3; i++) {
                const int pw = i == 0 ? width : chroma_w;
                const int ph = i == 0 ? height : chroma_h;
                D3D11_MAPPED_SUBRESOURCE pm;
                HR(context->Map(plane_staging[i], 0, D3D11_MAP_WRITE, 0, &pm));
                for (int r = 0; r < ph; r++)
                    memcpy(static_cast<uint8_t *>(pm.pData) + size_t(r) * pm.RowPitch,
                           srcs[i] + size_t(r) * pw, pw);
                context->Unmap(plane_staging[i], 0);
                context->CopyResource(plane_tex[i], plane_staging[i]);
            }
        }
        if (!y4m.chroma444) context->CopyResource(shared_tex, staging_tex);
        HR(context4->Signal(fence, ++fence_value));
        context->Flush();
        HR(fence->SetEventOnCompletion(fence_value, fence_event));
        WaitForSingleObject(fence_event, INFINITE);

        if (readback_mode) {
            context->CopyResource(readback_tex, shared_tex);
            context->Flush();
            D3D11_MAPPED_SUBRESOURCE rb;
            HR(context->Map(readback_tex, 0, D3D11_MAP_READ, 0, &rb));
            const uint8_t *src = static_cast<const uint8_t *>(rb.pData);
            fprintf(rb_out, "FRAME\n");
            for (int r = 0; r < height; r++)
                fwrite(src + size_t(r) * rb.RowPitch, 1, width, rb_out);
            // De-interleave NV12 chroma back to I420 so it can be compared against the source.
            const uint8_t *src_uv = src + size_t(rb.RowPitch) * height;
            std::vector<uint8_t> cb(size_t(width / 2) * (height / 2));
            std::vector<uint8_t> cr(cb.size());
            for (int r = 0; r < height / 2; r++)
                for (int c = 0; c < width / 2; c++) {
                    cb[size_t(r) * (width / 2) + c] = src_uv[size_t(r) * rb.RowPitch + 2 * c];
                    cr[size_t(r) * (width / 2) + c] = src_uv[size_t(r) * rb.RowPitch + 2 * c + 1];
                }
            fwrite(cb.data(), 1, cb.size(), rb_out);
            fwrite(cr.data(), 1, cr.size(), rb_out);
            context->Unmap(readback_tex, 0);
        }

        pyrowave_rate_control rc = {};
        rc.maximum_bitstream_size = max_bytes;
        if (cpu_path) {
            pyrowave_cpu_buffer cpu = {};
            cpu.data[0] = y.data();
            cpu.data[1] = uv.data();
            cpu.row_stride_in_bytes[0] = size_t(width);
            cpu.row_stride_in_bytes[1] = size_t(width);
            cpu.plane_size_in_bytes[0] = size_t(width) * height;
            cpu.plane_size_in_bytes[1] = size_t(width) * height / 2;
            cpu.width = width;
            cpu.height = height;
            cpu.format = PYROWAVE_CPU_BUFFER_FORMAT_NV12;
            PW(pyrowave_encoder_encode_cpu_synchronous(enc, &cpu, &rc));
        } else {
            // An imported image needs an explicit ownership acquire/release: pyrowave performs no
            // layout transitions of its own in the GPU paths, and the views declare GENERAL.
            // Without this, luma survives but chroma is read wrong -- planes carry different
            // compression metadata, so the layout mismatch shows up on chroma first.
            // The semaphore is VK_NULL_HANDLE ("no sync") because the D3D11 fence is CPU-waited
            // above; this operation is purely for the barrier, not for ordering.
            pyrowave_gpu_external_reference ref = { pyro_img, VK_QUEUE_FAMILY_EXTERNAL };
            pyrowave_gpu_sync_operation acquire = {};
            acquire.images = &ref;
            acquire.num_images = 1;
            pyrowave_gpu_sync_operation release = acquire;
            PW(pyrowave_encoder_encode_gpu_synchronous(enc, &acquire, &release, &buffers, &rc));
        }

        size_t num_packets = 0;
        PW(pyrowave_encoder_compute_num_packets(enc, bitstream.size(), &num_packets));
        std::vector<pyrowave_packet> packets(num_packets ? num_packets : 1);
        size_t out_packets = 0;
        PW(pyrowave_encoder_packetize(enc, packets.data(), bitstream.size(), &out_packets,
                                      bitstream.data(), bitstream.size()));
        if (out_packets != 1) {
            fprintf(stderr, "expected a single whole-frame packet, got %zu\n", out_packets);
            return 1;
        }
        const uint32_t size32 = uint32_t(packets[0].size);
        fwrite(&size32, sizeof(size32), 1, out);
        fwrite(bitstream.data() + packets[0].offset, 1, packets[0].size, out);
        total_bytes += packets[0].size;
        frames++;
        if (frame_limit && frames >= frame_limit) break;
    }

    fclose(out);
    if (rb_out) { fclose(rb_out); printf("wrote readback.y4m\n"); }
    printf("encoded %d frame(s), %zu bytes total, %.1f bytes/frame avg\n",
           frames, total_bytes, frames ? double(total_bytes) / frames : 0.0);
    if (frames) {
        const double mbps = double(total_bytes) / frames * 8.0
                          * (double(y4m.fps_num) / y4m.fps_den) / 1e6;
        printf("=> %.1f Mbps at %d/%d fps\n", mbps, y4m.fps_num, y4m.fps_den);
    }

    pyrowave_encoder_destroy(enc);
    pyrowave_image_destroy(pyro_img);
    pyrowave_device_destroy(pyro);
    return frames > 0 ? 0 : 1;
}
