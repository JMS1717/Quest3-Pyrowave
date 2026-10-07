// Quest3-Pyrowave PC composition filter (pixel shader, ps_5_0).
//
// Replaces the composition pass's single bilinear tap when the game renders above the stream
// resolution. Each output pixel measures its footprint in source texels from the UV derivatives
// and applies a Catmull-Rom kernel widened to that footprint, so a 3072x3216 eye is averaged
// into a 2080x2208 stream pixel instead of point-sampled. At equal render and stream size the
// kernel collapses to the identity on texel centers. Compiled with KERNEL_LANCZOS3 it uses a
// 3-lobe Lanczos kernel instead: sharper (more response near the stream's Nyquist frequency)
// for about 1.8x the fetches.
//
// Separable weights let each pair of adjacent same-sign texels share one bilinear fetch (exact
// for a separable kernel). Pairs that straddle a kernel zero crossing, or are merged by edge
// clamping, use point fetches instead. Taps are clamped to the layer's submitted bounds so a
// side-by-side game texture never bleeds the other eye into the edge.
//
// Signature matches FrameRender.fx's VS output and PS; the post-filter colour handling below is
// copied from that PS so both paths produce identical colour on identical filtered input.
// tools/downsample/reference.py models the same weights for CPU regression tests.

cbuffer FrameRenderParams : register(b0) {
    float encodingGamma;
    float _padding0;
    float _padding1;
    float _padding2;
};

cbuffer DownsampleParams : register(b1) {
    float4 boundsLeft;  // uMin, vMin, uMax, vMax of the submitted eye region
    float4 boundsRight;
};

Texture2D txLeft : register(t0);
Texture2D txRight : register(t1);
SamplerState samClampLinear : register(s1);

struct PS_INPUT {
    float4 Pos : SV_POSITION;
    float2 Tex : TEXCOORD;
    uint View : VIEW;
};

// Footprints above this are filtered as if they were this wide (under-filtered, never skipped).
// 3.0 covers render scales up to 3x per axis: <= 7x7 bilinear pairs for Catmull-Rom (radius 2),
// <= 10x10 for Lanczos-3 (radius 3).
#define MAX_SCALE 3.0
#ifdef KERNEL_LANCZOS3
#define KERNEL_RADIUS 3.0
#define MAX_PAIRS 10
#else
#define KERNEL_RADIUS 2.0
#define MAX_PAIRS 7
#endif

float CatmullRom(float x) {
    x = abs(x);
    if (x < 1.0) return (1.5 * x - 2.5) * x * x + 1.0;
    if (x < 2.0) return ((-0.5 * x + 2.5) * x - 4.0) * x + 2.0;
    return 0.0;
}

float Lanczos3(float x) {
    x = abs(x);
    if (x < 1e-4) return 1.0;
    if (x >= 3.0) return 0.0;
    float px = 3.14159265 * x;
    return 3.0 * sin(px) * sin(px * (1.0 / 3.0)) / (px * px);
}

float Kernel(float x) {
#ifdef KERNEL_LANCZOS3
    return Lanczos3(x);
#else
    return CatmullRom(x);
#endif
}

struct AxisPair {
    float p0; // texel-space positions (texel i spans [i, i + 1])
    float w0;
    float p1;
    float w1;
};

// Pair k covers texels first + 2k and first + 2k + 1. lo/hi are the clamped texel indices.
AxisPair MakePair(float first, int k, float center, float invScale, float lo, float hi) {
    float ia = first + 2.0 * k;
    float ib = ia + 1.0;
    float wa = Kernel((ia + 0.5 - center) * invScale);
    float wb = Kernel((ib + 0.5 - center) * invScale);
    float ja = clamp(ia, lo, hi);
    float jb = clamp(ib, lo, hi);
    AxisPair r;
    r.p1 = 0.0;
    r.w1 = 0.0;
    if (ja == jb) {
        r.p0 = ja + 0.5;
        r.w0 = wa + wb;
    } else if (wa * wb > 0.0) {
        r.w0 = wa + wb;
        r.p0 = ja + 0.5 + wb / r.w0;
    } else {
        r.p0 = ja + 0.5;
        r.w0 = wa;
        r.p1 = jb + 0.5;
        r.w1 = wb;
    }
    return r;
}

float4 FilterEye(Texture2D tex, float4 bounds, float2 uv, float2 scale) {
    float width, height;
    tex.GetDimensions(width, height);
    float2 size = float2(width, height);
    float2 t = uv * size;
    // Texels whose centers lie inside the submitted bounds (bounds may be flipped).
    float2 bMin = min(bounds.xy, bounds.zw) * size;
    float2 bMax = max(bounds.xy, bounds.zw) * size;
    float2 lo = ceil(bMin - 0.5);
    float2 hi = max(lo, floor(bMax - 0.5));
    float2 invScale = 1.0 / scale;
    float2 first = floor(t - 0.5 - KERNEL_RADIUS * scale);
    float2 last = floor(t - 0.5 + KERNEL_RADIUS * scale);
    int2 pairs = min(int2((last - first + 2.0) * 0.5), MAX_PAIRS);
    float2 invSize = 1.0 / size;

    float4 sum = 0.0;
    float weight = 0.0;
    [loop] for (int ky = 0; ky < pairs.y; ky++) {
        AxisPair py = MakePair(first.y, ky, t.y, invScale.y, lo.y, hi.y);
        [loop] for (int kx = 0; kx < pairs.x; kx++) {
            AxisPair px = MakePair(first.x, kx, t.x, invScale.x, lo.x, hi.x);
            [unroll] for (int sy = 0; sy < 2; sy++) {
                float wy = sy == 0 ? py.w0 : py.w1;
                float y = sy == 0 ? py.p0 : py.p1;
                [unroll] for (int sx = 0; sx < 2; sx++) {
                    float w = wy * (sx == 0 ? px.w0 : px.w1);
                    if (w != 0.0) {
                        float x = sx == 0 ? px.p0 : px.p1;
                        sum += w * tex.SampleLevel(samClampLinear, float2(x, y) * invSize, 0.0);
                        weight += w;
                    }
                }
            }
        }
    }
    return weight != 0.0 ? sum / weight : tex.SampleLevel(samClampLinear, uv, 0.0);
}

#define SRGB_GAMMA_TO_NONLINEAR (1.0 / 2.4)
#define SRGB_GAMMA_TO_LINEAR (2.4)

float4 EncodingLinearToNonlinearRGB(float4 color, float gamma) {
    float4 c;
    c.r = (color.r <= 0.0) ? color.r : pow(color.r, gamma);
    c.g = (color.g <= 0.0) ? color.g : pow(color.g, gamma);
    c.b = (color.b <= 0.0) ? color.b : pow(color.b, gamma);
    c.a = (color.a <= 0.0) ? color.a : pow(color.a, gamma);
    return c;
}

float4 LinearToNonlinearRGB(float4 color, float gamma) {
    float4 c;
    c.r = (color.r <= 0.0031308) ? (color.r * 12.92) : (1.055 * pow(color.r, gamma) - 0.055);
    c.g = (color.g <= 0.0031308) ? (color.g * 12.92) : (1.055 * pow(color.g, gamma) - 0.055);
    c.b = (color.b <= 0.0031308) ? (color.b * 12.92) : (1.055 * pow(color.b, gamma) - 0.055);
    c.a = (color.a <= 0.0031308) ? (color.a * 12.92) : (1.055 * pow(color.a, gamma) - 0.055);
    return c;
}

float4 NonlinearToLinearRGB(float4 color, float gamma) {
    float4 c;
    c.r = (color.r <= 0.04045) ? (color.r / 12.92) : pow((color.r + 0.055) / 1.055, gamma);
    c.g = (color.g <= 0.04045) ? (color.g / 12.92) : pow((color.g + 0.055) / 1.055, gamma);
    c.b = (color.b <= 0.04045) ? (color.b / 12.92) : pow((color.b + 0.055) / 1.055, gamma);
    c.a = (color.a <= 0.04045) ? (color.a / 12.92) : pow((color.a + 0.055) / 1.055, gamma);
    return c;
}

float4 main(PS_INPUT input) : SV_Target {
    uint correctionType = (input.View >> 1) & 0xF;
    uint shouldClamp = (input.View >> 5);

    // Footprint of one output pixel along each source axis, in source texels. Derivatives are
    // taken before the per-eye branch; View is constant across each triangle.
    bool right = (input.View & 1) == 1;
    float width, height;
    if (right) {
        txRight.GetDimensions(width, height);
    } else {
        txLeft.GetDimensions(width, height);
    }
    float2 t = input.Tex * float2(width, height);
    float2 dx = ddx(t);
    float2 dy = ddy(t);
    float2 scale = clamp(float2(length(float2(dx.x, dy.x)), length(float2(dx.y, dy.y))),
        1.0, MAX_SCALE);

    // if/else rather than ?: because HLSL evaluates both ternary operands.
    float4 color;
    if (right) {
        color = FilterEye(txRight, boundsRight, input.Tex, scale);
    } else {
        color = FilterEye(txLeft, boundsLeft, input.Tex, scale);
    }
    // Catmull-Rom and Lanczos lobes can undershoot; negative light is never valid input below.
    color = max(color, 0.0);

    if (shouldClamp == (uint)1) {
        color = clamp(color, 0.0, 1.0);
    }

    color = EncodingLinearToNonlinearRGB(color, encodingGamma);

    if (correctionType == (uint)1) {
        color = LinearToNonlinearRGB(color, SRGB_GAMMA_TO_NONLINEAR);
    } else if (correctionType == (uint)2) {
        color = NonlinearToLinearRGB(color, SRGB_GAMMA_TO_LINEAR);
    }
    if (shouldClamp == (uint)2) {
        color = clamp(color, 0.0, 1.0);
    }

    return color;
}
