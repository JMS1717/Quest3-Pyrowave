#version 450
// Final Haar level plus BT.709, written straight to the RGBA target.
// Binding 0 is the luma wavelet at the finest level (layers: LL, horizontal, vertical, diagonal).
// Chroma planes are the reconstructed 4:2:0 R8 images. The output format is the
// color attachment, not a storage image.
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

// Same storage rounding as PyroWave's pair-local inverse Haar (idwt.comp,
// haar_intermediate at precision 1): FP32 math, FP16 rounding after each lifting step.
float half_round(float v) {
    return unpackHalf2x16(packHalf2x16(vec2(v, 0.0))).x;
}

// The decoder would store luma to an R8_UNORM plane; reproduce that quantization.
float quantize8(float v) {
    return floor(clamp(v, 0.0, 1.0) * 255.0 + 0.5) / 255.0;
}

void main() {
    ivec2 pix = ivec2(gl_FragCoord.xy);
    ivec2 coeff = pix >> 1;
    // Layers: 0 = LL from the level-1 reconstruction, 1 = horizontal, 2 = vertical, 3 = diagonal.
    float a = texelFetch(waveletY, ivec3(coeff, 0), 0).x;
    float horizontal = texelFetch(waveletY, ivec3(coeff, 1), 0).x;
    float vertical = texelFetch(waveletY, ivec3(coeff, 2), 0).x;
    float diagonal = texelFetch(waveletY, ivec3(coeff, 3), 0).x;
    // Vertical then horizontal, exactly as inverse_haar_pairs().
    float low_even = a - 0.5 * vertical;
    float low_odd = low_even + vertical;
    float high_even = horizontal - 0.5 * diagonal;
    float high_odd = high_even + diagonal;
    low_even = half_round(low_even);
    low_odd = half_round(low_odd);
    high_even = half_round(high_even);
    high_odd = half_round(high_odd);
    bool odd_row = (pix.y & 1) != 0;
    float low = odd_row ? low_odd : low_even;
    float high = odd_row ? high_odd : high_even;
    float even_col = low - 0.5 * high;
    float Y = (pix.x & 1) == 0 ? even_col : even_col + high;
    Y = quantize8(half_round(Y) + 0.5);

    ivec2 outSize = textureSize(planeCb, 0) * 2;
    vec2 uv = (vec2(pix) + 0.5) / vec2(outSize);
    float Cb = sample_chroma(planeCb, uv);
    float Cr = sample_chroma(planeCr, uv);
    if (params.limitedRange != 0) {
        Y = (Y - 16.0 / 255.0) * (255.0 / 219.0);
        Cb = (Cb - 128.0 / 255.0) * (255.0 / 224.0);
        Cr = (Cr - 128.0 / 255.0) * (255.0 / 224.0);
    } else {
        Cb -= 0.5;
        Cr -= 0.5;
    }
    color = vec4(clamp(vec3(Y + 1.5748 * Cr, Y - 0.1873 * Cb - 0.4681 * Cr,
                            Y + 1.8556 * Cb), 0.0, 1.0), 1.0);
}
