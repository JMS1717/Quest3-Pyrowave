#version 450
// Final Haar level plus BT.709, written straight to the RGBA target.
// Binding 0 is the luma wavelet at the finest level (layers LL, LH, HL, HH).
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

float quantize8(float v) {
    return floor(clamp(v, 0.0, 1.0) * 255.0 + 0.5) / 255.0;
}

void main() {
    ivec2 pix = ivec2(gl_FragCoord.xy);
    ivec2 coeff = pix >> 1;
    float a = texelFetch(waveletY, ivec3(coeff, 0), 0).x;
    float lh = texelFetch(waveletY, ivec3(coeff, 1), 0).x;
    float hl = texelFetch(waveletY, ivec3(coeff, 2), 0).x;
    float hh = texelFetch(waveletY, ivec3(coeff, 3), 0).x;
    float top = a - 0.5 * hl;
    float bottom = top + hl;
    float topB = lh - 0.5 * hh;
    float bottomB = topB + hh;
    float p00 = top - 0.5 * topB;
    float p01 = p00 + topB;
    float p10 = bottom - 0.5 * bottomB;
    float p11 = p10 + bottomB;
    float Y = (pix.y & 1) == 0
        ? ((pix.x & 1) == 0 ? p00 : p01)
        : ((pix.x & 1) == 0 ? p10 : p11);
    Y = quantize8(Y + 0.5);

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
