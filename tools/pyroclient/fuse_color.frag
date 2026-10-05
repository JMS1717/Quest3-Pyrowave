#version 450
#if FP16
#extension GL_EXT_shader_explicit_arithmetic_types_float16 : require
#endif
// Experimental debug.q3pw.fuse_color (default off): the final 4:2:0 Haar luma level and BT.709
// in one fragment pass, written straight to the RGBA8 colour attachment.
//
// It must reproduce, byte for byte, PyroWave's idwt.comp inverse_haar_pairs() (HAAR, DCShift,
// PRECISION 1: FP32 arithmetic, FP16 rounding where the old path stored to shared memory),
// the R8_UNORM plane store, and then convert.frag. fuse_color_vk_test.cpp runs this shader and
// that reference on the same Vulkan device and compares every output byte. Keep the arithmetic
// order identical to idwt.comp; adds of exact halves are what make the result reproducible.
//
// Binding 0 is the luma level-0 wavelet view: R16F array, layers LL, LH ("horizontal"),
// HL ("vertical"), HH ("diagonal"). Chroma planes are the reconstructed 4:2:0 R8 images.
layout(set = 0, binding = 0) uniform sampler2DArray waveletY;
layout(set = 0, binding = 1) uniform sampler2D planeCb;
layout(set = 0, binding = 2) uniform sampler2D planeCr;
layout(push_constant) uniform Params { int limitedRange; int chromaFilter; } params;
layout(location = 0) out vec4 color;

float catmull(float a, float b, float c, float d, float t) {
    return b + 0.5 * t * (c - a + t * (2.0 * a - 5.0 * b + 4.0 * c - d + t * (3.0 * (b - c) + d - a)));
}

float sample_chroma(sampler2D plane, vec2 uv) {
    if (params.chromaFilter == 0)
        return textureLod(plane, uv, 0.0).r;
    vec2 size = vec2(textureSize(plane, 0));
    vec2 coord = uv * size - 0.5;
    vec2 f = fract(coord);
    ivec2 i0 = ivec2(floor(coord));
    ivec2 last = ivec2(size) - 1;
    float col[4];
    for (int x = 0; x < 4; x++) {
        float row[4];
        for (int y = 0; y < 4; y++) {
            ivec2 p = clamp(i0 + ivec2(x - 1, y - 1), ivec2(0), last);
            row[y] = texelFetch(plane, p, 0).r;
        }
        col[x] = catmull(row[0], row[1], row[2], row[3], f.y);
    }
    return catmull(col[0], col[1], col[2], col[3], f.x);
}

// idwt.comp haar_intermediate() for PRECISION 1. PyroWave picks its FP16 variant when the device
// has shaderFloat16; pyroclient picks the matching variant of this shader the same way, so each
// rounds through the same instruction (OpFConvert or packHalf2x16) as the pass it replaces.
float haar_intermediate(float v) {
#if FP16
    return float(float16_t(v));
#else
    return unpackHalf2x16(packHalf2x16(vec2(v, 0.0))).x;
#endif
}

// Vulkan's float -> UNORM8 store: clamp, scale, round to nearest.
float store_unorm8(float v) {
    return roundEven(clamp(v, 0.0, 1.0) * 255.0) / 255.0;
}

void main() {
    ivec2 pix = ivec2(gl_FragCoord.xy);
    ivec2 coeff = pix >> 1;
    float a = texelFetch(waveletY, ivec3(coeff, 0), 0).x;
    float horizontal = texelFetch(waveletY, ivec3(coeff, 1), 0).x;
    float vertical = texelFetch(waveletY, ivec3(coeff, 2), 0).x;
    float diagonal = texelFetch(waveletY, ivec3(coeff, 3), 0).x;
    float low_even = a - 0.5 * vertical;
    float low_odd = low_even + vertical;
    float high_even = horizontal - 0.5 * diagonal;
    float high_odd = high_even + diagonal;
    // Row parity picks the vertical half, column parity the horizontal one (pixel = 2 * coeff).
    bool odd_row = (pix.y & 1) != 0;
    float low = haar_intermediate(odd_row ? low_odd : low_even);
    float high = haar_intermediate(odd_row ? high_odd : high_even);
    float even = low - 0.5 * high;
    float odd = even + high;
    float Y = haar_intermediate((pix.x & 1) != 0 ? odd : even);
    Y = store_unorm8(Y + 0.5);

    // convert.frag samples chroma at the luma plane's texel centres; 4:2:0 luma is twice chroma.
    vec2 uv = (vec2(pix) + 0.5) / vec2(textureSize(planeCb, 0) * 2);
    float Cb = sample_chroma(planeCb, uv);
    float Cr = sample_chroma(planeCr, uv);
    if (params.limitedRange != 0) {
        Y = (Y - 16.0 / 255.0) * (255.0 / 219.0);
        Cb = (Cb - 128.0 / 255.0) * (255.0 / 224.0);
        Cr = (Cr - 128.0 / 255.0) * (255.0 / 224.0);
    } else { Cb -= 0.5; Cr -= 0.5; }
    color = vec4(clamp(vec3(Y + 1.5748 * Cr, Y - 0.1873 * Cb - 0.4681 * Cr,
                            Y + 1.8556 * Cb), 0.0, 1.0), 1.0);
}
