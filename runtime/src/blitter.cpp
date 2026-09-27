#include "blitter.h"

#include <d3dcompiler.h>

#include <cstring>

#include "log.h"

using xrw::log;

namespace {
DXGI_FORMAT as_unorm(DXGI_FORMAT f) {
    switch (f) {
        case DXGI_FORMAT_R8G8B8A8_TYPELESS:
        case DXGI_FORMAT_R8G8B8A8_UNORM_SRGB:
            return DXGI_FORMAT_R8G8B8A8_UNORM;
        case DXGI_FORMAT_B8G8R8A8_TYPELESS:
        case DXGI_FORMAT_B8G8R8A8_UNORM_SRGB:
            return DXGI_FORMAT_B8G8R8A8_UNORM;
        default:
            return f;
    }
}
}  // namespace

bool Blitter::init(ID3D11Device* device) {
    static const char* kShader = R"(
struct VsOut { float4 pos : SV_POSITION; float2 uv : TEXCOORD0; };
cbuffer Region : register(b0) { float2 uv_offset; float2 uv_scale; };
VsOut vs(uint id : SV_VertexID) {
    float2 corner = float2((id << 1) & 2, id & 2);
    VsOut o;
    o.pos = float4(corner * float2(2, -2) + float2(-1, 1), 0, 1);
    o.uv = uv_offset + corner * uv_scale;
    return o;
}
Texture2D source : register(t0);
SamplerState linear_clamp : register(s0);
float4 ps(VsOut i) : SV_TARGET { return source.Sample(linear_clamp, i.uv); }
)";
    ID3DBlob* vs_blob = nullptr;
    ID3DBlob* ps_blob = nullptr;
    ID3DBlob* errors = nullptr;
    if (FAILED(D3DCompile(kShader, strlen(kShader), nullptr, nullptr, nullptr, "vs", "vs_4_0", 0, 0,
                          &vs_blob, &errors)) ||
        FAILED(D3DCompile(kShader, strlen(kShader), nullptr, nullptr, nullptr, "ps", "ps_4_0", 0, 0,
                          &ps_blob, &errors))) {
        log(std::string("blit shader failed: ") +
            (errors != nullptr ? static_cast<const char*>(errors->GetBufferPointer()) : "?"));
        return false;
    }
    device->CreateVertexShader(vs_blob->GetBufferPointer(), vs_blob->GetBufferSize(), nullptr, &vs_);
    device->CreatePixelShader(ps_blob->GetBufferPointer(), ps_blob->GetBufferSize(), nullptr, &ps_);
    vs_blob->Release();
    ps_blob->Release();

    D3D11_SAMPLER_DESC sampler = {};
    sampler.Filter = D3D11_FILTER_MIN_MAG_MIP_LINEAR;
    sampler.AddressU = sampler.AddressV = sampler.AddressW = D3D11_TEXTURE_ADDRESS_CLAMP;
    device->CreateSamplerState(&sampler, &sampler_);

    D3D11_BUFFER_DESC constants = {};
    constants.ByteWidth = 16;
    constants.Usage = D3D11_USAGE_DYNAMIC;
    constants.BindFlags = D3D11_BIND_CONSTANT_BUFFER;
    constants.CPUAccessFlags = D3D11_CPU_ACCESS_WRITE;
    device->CreateBuffer(&constants, nullptr, &constants_);
    return vs_ && ps_ && sampler_ && constants_;
}

void Blitter::blit(ID3D11Device* device, ID3D11DeviceContext* context, ID3D11Texture2D* source,
                   float u0, float v0, float uW, float vH, ID3D11RenderTargetView* target, float x,
                   float y, float w, float h) {
    ID3D11ShaderResourceView* view = shader_view(device, source);
    if (view == nullptr) return;
    D3D11_MAPPED_SUBRESOURCE mapped = {};
    if (SUCCEEDED(context->Map(constants_, 0, D3D11_MAP_WRITE_DISCARD, 0, &mapped))) {
        const float region[4] = {u0, v0, uW, vH};
        memcpy(mapped.pData, region, sizeof(region));
        context->Unmap(constants_, 0);
    }
    D3D11_VIEWPORT viewport = {x, y, w, h, 0.0f, 1.0f};
    context->OMSetRenderTargets(1, &target, nullptr);
    context->RSSetViewports(1, &viewport);
    context->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
    context->IASetInputLayout(nullptr);
    context->VSSetShader(vs_, nullptr, 0);
    context->VSSetConstantBuffers(0, 1, &constants_);
    context->PSSetShader(ps_, nullptr, 0);
    context->PSSetShaderResources(0, 1, &view);
    context->PSSetSamplers(0, 1, &sampler_);
    context->OMSetBlendState(nullptr, nullptr, 0xffffffff);
    context->OMSetDepthStencilState(nullptr, 0);
    context->RSSetState(nullptr);
    context->Draw(3, 0);
    ID3D11ShaderResourceView* none = nullptr;
    context->PSSetShaderResources(0, 1, &none);
    ID3D11RenderTargetView* no_target = nullptr;   // free the target so NVENC can register it
    context->OMSetRenderTargets(1, &no_target, nullptr);
}

void Blitter::release() {
    for (auto& entry : views_) entry.second->Release();
    views_.clear();
    if (vs_) vs_->Release();
    if (ps_) ps_->Release();
    if (sampler_) sampler_->Release();
    if (constants_) constants_->Release();
    vs_ = nullptr;
    ps_ = nullptr;
    sampler_ = nullptr;
    constants_ = nullptr;
}

ID3D11ShaderResourceView* Blitter::shader_view(ID3D11Device* device, ID3D11Texture2D* texture) {
    auto cached = views_.find(texture);
    if (cached != views_.end()) return cached->second;
    D3D11_TEXTURE2D_DESC desc = {};
    texture->GetDesc(&desc);
    D3D11_SHADER_RESOURCE_VIEW_DESC view_desc = {};
    view_desc.Format = as_unorm(desc.Format);   // raw bytes: no sRGB linearization on sample
    view_desc.ViewDimension = D3D11_SRV_DIMENSION_TEXTURE2D;
    view_desc.Texture2D.MipLevels = 1;
    ID3D11ShaderResourceView* view = nullptr;
    if (FAILED(device->CreateShaderResourceView(texture, &view_desc, &view))) {
        log("CreateShaderResourceView failed");
        return nullptr;
    }
    views_[texture] = view;
    return view;
}
