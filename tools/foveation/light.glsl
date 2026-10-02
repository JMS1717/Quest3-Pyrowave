// SPDX-License-Identifier: MIT
// Fixed inverse of ALVR's CompressAxisAlignedPixelShader (MIT upstream).
// Parameters come from the existing ALVR CPU derivation, not a second formula.
uniform highp int view_idx;
uniform vec2 ffe_view_ratio;
uniform vec2 ffe_edge_ratio;
uniform vec2 ffe_c2;
uniform vec4 ffe_p[4];
uniform vec2 ffe_half_texel;

float light_inverse_axis(float u, float c1, float c2, float edge,
                         float lo, float hi, float al, float bl,
                         float ar, float br, float cr) {
    if (u < lo)
        return (-bl + sqrt(max(0.0, bl * bl + 4.0 * al * u))) / (2.0 * al);
    if (u > hi)
        return (-br + sqrt(max(0.0, br * br - 4.0 * (cr - ar * u)))) / (2.0 * ar);
    return (u - c1) * edge / c2;
}

vec2 light_foveated_uv(vec2 stereo_uv) {
    // Work within one eye. ALVR mirrors BOTH the forward and inverse right-eye
    // maps; doing just one would place the right region incorrectly.
    vec2 eye_uv = vec2(stereo_uv.x * 2.0 - float(view_idx), stereo_uv.y);
    if (view_idx == 1) eye_uv.x = 1.0 - eye_uv.x;
    vec2 encoded;
    encoded.x = light_inverse_axis(eye_uv.x, ffe_p[0].x, ffe_c2.x, ffe_edge_ratio.x,
        ffe_p[0].z, ffe_p[1].x, ffe_p[1].z, ffe_p[2].x,
        ffe_p[2].z, ffe_p[3].x, ffe_p[3].z);
    encoded.y = light_inverse_axis(eye_uv.y, ffe_p[0].y, ffe_c2.y, ffe_edge_ratio.y,
        ffe_p[0].w, ffe_p[1].y, ffe_p[1].w, ffe_p[2].y,
        ffe_p[2].w, ffe_p[3].y, ffe_p[3].w);
    encoded *= ffe_view_ratio;
    if (view_idx == 1) encoded.x = 1.0 - encoded.x;
    vec2 result = vec2((encoded.x + float(view_idx)) * 0.5, encoded.y);
    // Bilinear sampling must never borrow the neighbouring eye at the seam.
    return clamp(result, vec2(float(view_idx) * 0.5 + ffe_half_texel.x, ffe_half_texel.y),
                 vec2(float(view_idx + 1) * 0.5 - ffe_half_texel.x, 1.0 - ffe_half_texel.y));
}
