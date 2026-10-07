#version 450
layout(set = 0, binding = 0) uniform sampler2D planeY;
layout(set = 0, binding = 1) uniform sampler2D planeCb;
layout(set = 0, binding = 2) uniform sampler2D planeCr;
layout(push_constant) uniform Params { int limitedRange; int chromaFilter; } params;
layout(location = 0) out vec4 color;
// Packed luma, as in ycbcr_to_rgba.comp.
layout(constant_id = 0) const bool PACKED_LUMA = false;

// PyroWave Haar mode 4 writes Cb and Cr into one RG8 plane (planeCb); planeCr is not read.
layout(constant_id = 1) const bool DUAL_CHROMA = false;

vec2 catmull(vec2 a, vec2 b, vec2 c, vec2 d, float t) {
    return b + 0.5 * t * (c - a + t * (2.0 * a - 5.0 * b + 4.0 * c - d + t * (3.0 * (b - c) + d - a)));
}

// Returns .r of a single-chroma plane, or Cb and Cr of a DUAL_CHROMA plane.
vec2 sample_chroma(sampler2D plane, vec2 uv) {
    if (params.chromaFilter == 0)
        return textureLod(plane, uv, 0.0).rg;
    vec2 size = vec2(textureSize(plane, 0));
    vec2 coord = uv * size - 0.5;
    vec2 f = fract(coord);
    ivec2 i0 = ivec2(floor(coord));
    ivec2 last = ivec2(size) - 1;
    vec2 col[4];
    for (int x = 0; x < 4; x++) {
        vec2 row[4];
        for (int y = 0; y < 4; y++) {
            ivec2 p = clamp(i0 + ivec2(x - 1, y - 1), ivec2(0), last);
            row[y] = texelFetch(plane, p, 0).rg;
        }
        col[x] = catmull(row[0], row[1], row[2], row[3], f.y);
    }
    return catmull(col[0], col[1], col[2], col[3], f.x);
}

void main() {
    ivec2 coord = ivec2(gl_FragCoord.xy);
    vec2 uv = (vec2(coord) + 0.5) / vec2(textureSize(planeY, 0) * (PACKED_LUMA ? 2 : 1));
    float Y = PACKED_LUMA ? texelFetch(planeY, coord >> 1, 0)[(coord.x & 1) | ((coord.y & 1) << 1)]
                          : texelFetch(planeY, coord, 0).r;
    vec2 chroma = sample_chroma(planeCb, uv);
    float Cb = chroma.x;
    float Cr = DUAL_CHROMA ? chroma.y : sample_chroma(planeCr, uv).x;
    if (params.limitedRange != 0) {
        Y = (Y - 16.0 / 255.0) * (255.0 / 219.0);
        Cb = (Cb - 128.0 / 255.0) * (255.0 / 224.0);
        Cr = (Cr - 128.0 / 255.0) * (255.0 / 224.0);
    } else { Cb -= 0.5; Cr -= 0.5; }
    color = vec4(clamp(vec3(Y + 1.5748 * Cr, Y - 0.1873 * Cb - 0.4681 * Cr,
                            Y + 1.8556 * Cb), 0.0, 1.0), 1.0);
}
