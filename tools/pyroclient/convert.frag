#version 450
layout(set = 0, binding = 0) uniform sampler2D planeY;
layout(set = 0, binding = 1) uniform sampler2D planeCb;
layout(set = 0, binding = 2) uniform sampler2D planeCr;
layout(push_constant) uniform Params { int limitedRange; } params;
layout(location = 0) out vec4 color;
void main() {
    ivec2 coord = ivec2(gl_FragCoord.xy);
    vec2 uv = (vec2(coord) + 0.5) / vec2(textureSize(planeY, 0));
    float Y = texelFetch(planeY, coord, 0).r;
    float Cb = textureLod(planeCb, uv, 0.0).r;
    float Cr = textureLod(planeCr, uv, 0.0).r;
    if (params.limitedRange != 0) {
        Y = (Y - 16.0 / 255.0) * (255.0 / 219.0);
        Cb = (Cb - 128.0 / 255.0) * (255.0 / 224.0);
        Cr = (Cr - 128.0 / 255.0) * (255.0 / 224.0);
    } else { Cb -= 0.5; Cr -= 0.5; }
    color = vec4(clamp(vec3(Y + 1.5748 * Cr, Y - 0.1873 * Cb - 0.4681 * Cr,
                            Y + 1.8556 * Cb), 0.0, 1.0), 1.0);
}
