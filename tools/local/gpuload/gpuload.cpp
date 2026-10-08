// gpuload: a game-like GPU load for latency measurements on the streaming PC.
//
// Every period it submits compute work that keeps the GPU busy for about busy-ms, as a game
// rendering at that rate would, so the streamer's frame render and encode have to share the GPU.
// The work per dispatch is calibrated with timestamp queries at start-up.
//
//   gpuload.exe [--adapter <name substring>] [--busy-ms 5] [--period-ms 8.333] [--seconds 60]
//               [--stop-file <path>]
//   gpuload.exe --probe    (time a tiny D3D11 dispatch instead, while another gpuload runs)
//
// Build (x64 Developer prompt): cl /O2 /EHsc gpuload.cpp d3d11.lib dxgi.lib d3dcompiler.lib
#include <d3d11.h>
#include <d3dcompiler.h>
#include <dxgi.h>
#include <wrl/client.h>

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <thread>
#include <vector>
#include <algorithm>

using Microsoft::WRL::ComPtr;

static const char kShader[] = R"(
RWStructuredBuffer<float> outBuf : register(u0);
cbuffer Params : register(b0) { uint iterations; uint3 pad; };
[numthreads(256, 1, 1)]
void main(uint3 id : SV_DispatchThreadID) {
    float a = id.x * 1e-6, b = 1.0001;
    [loop] for (uint i = 0; i < iterations; i++) { a = a * b + 1e-7; b = b * 0.99999 + 1e-6; }
    outBuf[id.x] = a + b;
}
)";

int main(int argc, char** argv) {
    std::string adapterName = "7900";
    double busyMs = 5.0, periodMs = 1000.0 / 120.0, seconds = 60.0;
    const char* stopFile = nullptr;
    bool probe = false;
    for (int i = 1; i < argc; i++)
        if (!strcmp(argv[i], "--probe")) probe = true;
    for (int i = 1; i + 1 < argc; i += 2) {
        if (!strcmp(argv[i], "--adapter")) adapterName = argv[i + 1];
        else if (!strcmp(argv[i], "--busy-ms")) busyMs = atof(argv[i + 1]);
        else if (!strcmp(argv[i], "--period-ms")) periodMs = atof(argv[i + 1]);
        else if (!strcmp(argv[i], "--seconds")) seconds = atof(argv[i + 1]);
        else if (!strcmp(argv[i], "--stop-file")) stopFile = argv[i + 1];
    }

    ComPtr<IDXGIFactory1> factory;
    CreateDXGIFactory1(IID_PPV_ARGS(&factory));
    ComPtr<IDXGIAdapter1> adapter, chosen;
    for (UINT i = 0; factory->EnumAdapters1(i, &adapter) != DXGI_ERROR_NOT_FOUND; i++) {
        DXGI_ADAPTER_DESC1 desc;
        adapter->GetDesc1(&desc);
        char name[128];
        wcstombs(name, desc.Description, sizeof(name));
        if (strstr(name, adapterName.c_str())) {
            chosen = adapter;
            printf("adapter: %s\n", name);
            break;
        }
    }
    if (!chosen) {
        fprintf(stderr, "no adapter matching '%s'\n", adapterName.c_str());
        return 1;
    }

    ComPtr<ID3D11Device> device;
    ComPtr<ID3D11DeviceContext> context;
    D3D_FEATURE_LEVEL level = D3D_FEATURE_LEVEL_11_0;
    if (FAILED(D3D11CreateDevice(chosen.Get(), D3D_DRIVER_TYPE_UNKNOWN, nullptr, 0, &level, 1,
                                 D3D11_SDK_VERSION, &device, nullptr, &context))) {
        fprintf(stderr, "D3D11CreateDevice failed\n");
        return 1;
    }

    ComPtr<ID3DBlob> code, errors;
    if (FAILED(D3DCompile(kShader, sizeof(kShader) - 1, "gpuload", nullptr, nullptr, "main", "cs_5_0",
                          D3DCOMPILE_OPTIMIZATION_LEVEL3, 0, &code, &errors))) {
        fprintf(stderr, "compile failed: %s\n", errors ? (const char*)errors->GetBufferPointer() : "");
        return 1;
    }
    ComPtr<ID3D11ComputeShader> shader;
    device->CreateComputeShader(code->GetBufferPointer(), code->GetBufferSize(), nullptr, &shader);

    const UINT threads = 256 * 4096;
    D3D11_BUFFER_DESC bd = {};
    bd.ByteWidth = threads * sizeof(float);
    bd.BindFlags = D3D11_BIND_UNORDERED_ACCESS;
    bd.MiscFlags = D3D11_RESOURCE_MISC_BUFFER_STRUCTURED;
    bd.StructureByteStride = sizeof(float);
    ComPtr<ID3D11Buffer> buffer;
    device->CreateBuffer(&bd, nullptr, &buffer);
    ComPtr<ID3D11UnorderedAccessView> uav;
    device->CreateUnorderedAccessView(buffer.Get(), nullptr, &uav);

    D3D11_BUFFER_DESC cbd = {};
    cbd.ByteWidth = 16;
    cbd.BindFlags = D3D11_BIND_CONSTANT_BUFFER;
    cbd.Usage = D3D11_USAGE_DEFAULT;
    ComPtr<ID3D11Buffer> params;
    device->CreateBuffer(&cbd, nullptr, &params);

    context->CSSetShader(shader.Get(), nullptr, 0);
    context->CSSetUnorderedAccessViews(0, 1, uav.GetAddressOf(), nullptr);
    context->CSSetConstantBuffers(0, 1, params.GetAddressOf());

    D3D11_QUERY_DESC qd = { D3D11_QUERY_TIMESTAMP_DISJOINT, 0 };
    ComPtr<ID3D11Query> disjoint, t0, t1;
    device->CreateQuery(&qd, &disjoint);
    qd.Query = D3D11_QUERY_TIMESTAMP;
    device->CreateQuery(&qd, &t0);
    device->CreateQuery(&qd, &t1);

    auto timeDispatch = [&](UINT iterations) {
        UINT data[4] = { iterations, 0, 0, 0 };
        context->UpdateSubresource(params.Get(), 0, nullptr, data, 0, 0);
        context->Begin(disjoint.Get());
        context->End(t0.Get());
        context->Dispatch(threads / 256, 1, 1);
        context->End(t1.Get());
        context->End(disjoint.Get());
        D3D11_QUERY_DATA_TIMESTAMP_DISJOINT dj;
        while (context->GetData(disjoint.Get(), &dj, sizeof(dj), 0) != S_OK) {}
        UINT64 a, b;
        while (context->GetData(t0.Get(), &a, sizeof(a), 0) != S_OK) {}
        while (context->GetData(t1.Get(), &b, sizeof(b), 0) != S_OK) {}
        return dj.Disjoint ? -1.0 : (double)(b - a) * 1000.0 / (double)dj.Frequency;
    };

    // Calibrate at a steady state: warm the clocks up, then scale iterations to busy-ms.
    UINT iterations = 1000;
    for (int i = 0; i < 20; i++) timeDispatch(iterations);
    for (int round = 0; round < 6; round++) {
        double ms = timeDispatch(iterations);
        if (ms > 0) iterations = (UINT)(iterations * busyMs / ms);
        if (iterations < 1) iterations = 1;
    }
    if (probe) {
        // Submit-to-completion time of a tiny dispatch on this D3D11 device, for comparison with
        // vkprobe while another gpuload runs.
        UINT tiny[4] = { 1, 0, 0, 0 };
        context->UpdateSubresource(params.Get(), 0, nullptr, tiny, 0, 0);
        D3D11_QUERY_DESC ed = { D3D11_QUERY_EVENT, 0 };
        ComPtr<ID3D11Query> done;
        device->CreateQuery(&ed, &done);
        std::vector<double> ms;
        for (int i = 0; i < 210; i++) {
            auto t0 = std::chrono::steady_clock::now();
            context->Dispatch(1, 1, 1);
            context->End(done.Get());
            context->Flush();
            BOOL finished = FALSE;
            while (context->GetData(done.Get(), &finished, sizeof(finished), 0) != S_OK || !finished) {}
            auto t1 = std::chrono::steady_clock::now();
            if (i >= 10) ms.push_back(std::chrono::duration<double, std::milli>(t1 - t0).count());
            std::this_thread::sleep_for(std::chrono::duration<double, std::milli>(9.7));
        }
        std::sort(ms.begin(), ms.end());
        printf("d3d11 tiny dispatch  p50 %.3f  p90 %.3f  p99 %.3f  max %.3f ms\n", ms[ms.size() / 2],
               ms[ms.size() * 9 / 10], ms[ms.size() * 99 / 100], ms.back());
        return 0;
    }
    printf("calibrated: %u iterations ~ %.2f ms per period of %.3f ms\n", iterations, timeDispatch(iterations), periodMs);
    fflush(stdout);

    UINT data[4] = { iterations, 0, 0, 0 };
    context->UpdateSubresource(params.Get(), 0, nullptr, data, 0, 0);
    using clock = std::chrono::steady_clock;
    const auto start = clock::now();
    auto next = start;
    const auto period = std::chrono::duration_cast<clock::duration>(std::chrono::duration<double, std::milli>(periodMs));
    unsigned long long frames = 0;
    while (std::chrono::duration<double>(clock::now() - start).count() < seconds) {
        if (stopFile && (frames % 120) == 0) {
            FILE* f = fopen(stopFile, "rb");
            if (f) { fclose(f); break; }
        }
        context->Dispatch(threads / 256, 1, 1);
        context->Flush();
        frames++;
        next += period;
        std::this_thread::sleep_until(next);
    }
    printf("done: %llu periods\n", frames);
    return 0;
}
