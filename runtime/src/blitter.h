// Blitter: scale a source D3D11 texture region into a viewport of a render target. Lifted from the
// SteamVR driver; generalized to a normalized UV rect (instead of OpenVR's VRTextureBounds_t) and
// coerced to a raw-byte (UNORM, never _SRGB) sample+store so the sRGB-encoded pixels the app rendered
// pass through unchanged — the headset's sRGB swapchain then displays them with correct gamma.
#pragma once

#include <d3d11.h>

#include <map>

class Blitter {
public:
    bool init(ID3D11Device* device);
    // Sample the (u0,v0)+(uW,vH) region of `source` into the [x,y,w,h] viewport of `target`.
    void blit(ID3D11Device* device, ID3D11DeviceContext* context, ID3D11Texture2D* source,
              float u0, float v0, float uW, float vH, ID3D11RenderTargetView* target, float x, float y,
              float w, float h);
    void release();

private:
    ID3D11ShaderResourceView* shader_view(ID3D11Device* device, ID3D11Texture2D* texture);

    ID3D11VertexShader* vs_ = nullptr;
    ID3D11PixelShader* ps_ = nullptr;
    ID3D11SamplerState* sampler_ = nullptr;
    ID3D11Buffer* constants_ = nullptr;
    std::map<ID3D11Texture2D*, ID3D11ShaderResourceView*> views_;
};
