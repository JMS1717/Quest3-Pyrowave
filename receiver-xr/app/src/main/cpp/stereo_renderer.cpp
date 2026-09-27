#include "stereo_renderer.h"

#include <android/log.h>

#include <algorithm>
#include <cstring>
#include <vector>

#define LOGI(...) __android_log_print(ANDROID_LOG_INFO, "XRWiredXR", __VA_ARGS__)
#define LOGE(...) __android_log_print(ANDROID_LOG_ERROR, "XRWiredXR", __VA_ARGS__)

namespace {

// Full-screen triangle; the fragment shader samples the eye's half of the decoded frame.
const char* kVertex = R"(#version 300 es
out vec2 v_uv;
uniform vec2 u_uv_offset;
uniform vec2 u_uv_scale;
uniform mat4 u_transform;            // SurfaceTexture's transform (origin/crop of the decoder buffer)
void main() {
    vec2 corner = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2);   // (0,0) (2,0) (0,2)
    gl_Position = vec4(corner * 2.0 - 1.0, 0.0, 1.0);
    vec2 uv = u_uv_offset + corner * u_uv_scale;
    v_uv = (u_transform * vec4(uv, 0.0, 1.0)).xy;
}
)";

const char* kFragment = R"(#version 300 es
#extension GL_OES_EGL_image_external_essl3 : require
precision mediump float;
in vec2 v_uv;
out vec4 fragColor;
uniform samplerExternalOES u_texture;
uniform int u_transfer;              // 0 = sRGB swapchain, 1 = PQ (HDR), 2 = linear swapchain
const float kPeakNits = 1000.0;
vec3 pq_encode(vec3 linear) {        // SMPTE ST 2084, input normalised to 10000 nits
    const float m1 = 0.1593017578125, m2 = 78.84375, c1 = 0.8359375, c2 = 18.8515625, c3 = 18.6875;
    vec3 y = pow(max(linear * (kPeakNits / 10000.0), vec3(0.0)), vec3(m1));
    return pow((c1 + c2 * y) / (1.0 + c3 * y), vec3(m2));
}
// The PC encodes sRGB-encoded pixels and the decoder hands them back unchanged. An sRGB swapchain
// re-encodes whatever we write, so we undo the encoding first - otherwise the image is gamma-applied
// twice and comes out washed out with crushed contrast.
vec3 srgb_to_linear(vec3 c) {
    return mix(c / 12.92, pow((c + 0.055) / 1.055, vec3(2.4)), step(vec3(0.04045), c));
}
void main() {
    vec3 encoded = texture(u_texture, v_uv).rgb;
    vec3 linear = srgb_to_linear(encoded);
    if (u_transfer == 1) fragColor = vec4(pq_encode(linear), 1.0);      // HDR swapchain
    else if (u_transfer == 2) fragColor = vec4(linear, 1.0);            // linear (10-bit) swapchain
    else fragColor = vec4(linear, 1.0);                                 // sRGB swapchain re-encodes
}
)";

GLuint compile(GLenum type, const char* source) {
    GLuint shader = glCreateShader(type);
    glShaderSource(shader, 1, &source, nullptr);
    glCompileShader(shader);
    GLint ok = 0;
    glGetShaderiv(shader, GL_COMPILE_STATUS, &ok);
    if (!ok) {
        char log[1024] = {};
        glGetShaderInfoLog(shader, sizeof(log) - 1, nullptr, log);
        LOGE("shader compile failed: %s", log);
        glDeleteShader(shader);
        return 0;
    }
    return shader;
}

}  // namespace

bool StereoRenderer::init() {
    GLuint vertex = compile(GL_VERTEX_SHADER, kVertex), fragment = compile(GL_FRAGMENT_SHADER, kFragment);
    if (!vertex || !fragment) return false;
    program_ = glCreateProgram();
    glAttachShader(program_, vertex);
    glAttachShader(program_, fragment);
    glLinkProgram(program_);
    GLint ok = 0;
    glGetProgramiv(program_, GL_LINK_STATUS, &ok);
    glDeleteShader(vertex);
    glDeleteShader(fragment);
    if (!ok) {
        char log[1024] = {};
        glGetProgramInfoLog(program_, sizeof(log) - 1, nullptr, log);
        LOGE("program link failed: %s", log);
        return false;
    }
    u_transform_ = glGetUniformLocation(program_, "u_transform");
    u_uv_offset_ = glGetUniformLocation(program_, "u_uv_offset");
    u_uv_scale_ = glGetUniformLocation(program_, "u_uv_scale");
    u_transfer_ = glGetUniformLocation(program_, "u_transfer");
    glGenVertexArrays(1, &vao_);                 // GLES 3 needs a bound VAO even with no attributes
    glGenTextures(1, &external_texture_);
    glBindTexture(GL_TEXTURE_EXTERNAL_OES, external_texture_);
    glTexParameteri(GL_TEXTURE_EXTERNAL_OES, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_EXTERNAL_OES, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_EXTERNAL_OES, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_EXTERNAL_OES, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    glBindTexture(GL_TEXTURE_EXTERNAL_OES, 0);
    LOGI("renderer ready: program=%u external_texture=%u", program_, external_texture_);
    return true;
}

jobject StereoRenderer::create_decoder_surface(JNIEnv* env, int width, int height) {
    release_decoder_surface(env);
    jclass st_class = env->FindClass("android/graphics/SurfaceTexture");
    jobject surface_texture = env->NewObject(st_class, env->GetMethodID(st_class, "<init>", "(I)V"),
                                             static_cast<jint>(external_texture_));
    if (env->ExceptionCheck()) {
        env->ExceptionDescribe();
        env->ExceptionClear();
        LOGE("SurfaceTexture constructor failed");
        return nullptr;
    }
    env->CallVoidMethod(surface_texture, env->GetMethodID(st_class, "setDefaultBufferSize", "(II)V"),
                        width, height);
    update_tex_image_ = env->GetMethodID(st_class, "updateTexImage", "()V");
    get_transform_matrix_ = env->GetMethodID(st_class, "getTransformMatrix", "([F)V");
    get_timestamp_ = env->GetMethodID(st_class, "getTimestamp", "()J");
    surface_texture_ = env->NewGlobalRef(surface_texture);
    env->DeleteLocalRef(surface_texture);
    env->DeleteLocalRef(st_class);

    jclass surface_class = env->FindClass("android/view/Surface");
    jobject surface = env->NewObject(surface_class,
                                     env->GetMethodID(surface_class, "<init>", "(Landroid/graphics/SurfaceTexture;)V"),
                                     surface_texture_);
    if (env->ExceptionCheck()) {
        env->ExceptionDescribe();
        env->ExceptionClear();
        LOGE("Surface constructor failed");
        return nullptr;
    }
    decoder_surface_ = env->NewGlobalRef(surface);
    env->DeleteLocalRef(surface);
    env->DeleteLocalRef(surface_class);
    if (transform_array_ == nullptr) {
        jfloatArray array = env->NewFloatArray(16);
        transform_array_ = static_cast<jfloatArray>(env->NewGlobalRef(array));
        env->DeleteLocalRef(array);
    }
    frames_ = 0;
    LOGI("decoder surface created: %dx%d layout=%d", width, height, layout_);
    return decoder_surface_;
}

void StereoRenderer::release_decoder_surface(JNIEnv* env) {
    if (decoder_surface_ != nullptr) {
        jclass surface_class = env->GetObjectClass(decoder_surface_);
        env->CallVoidMethod(decoder_surface_, env->GetMethodID(surface_class, "release", "()V"));
        env->ExceptionClear();
        env->DeleteLocalRef(surface_class);
        env->DeleteGlobalRef(decoder_surface_);
        decoder_surface_ = nullptr;
    }
    if (surface_texture_ != nullptr) {
        jclass st_class = env->GetObjectClass(surface_texture_);
        env->CallVoidMethod(surface_texture_, env->GetMethodID(st_class, "release", "()V"));
        env->ExceptionClear();
        env->DeleteLocalRef(st_class);
        env->DeleteGlobalRef(surface_texture_);
        surface_texture_ = nullptr;
    }
}

bool StereoRenderer::update_texture(JNIEnv* env, int64_t* frame_timestamp_ns) {
    if (surface_texture_ == nullptr) return false;
    env->CallVoidMethod(surface_texture_, update_tex_image_);
    if (env->ExceptionCheck()) {                 // no frame queued yet
        env->ExceptionClear();
        return false;
    }
    env->CallVoidMethod(surface_texture_, get_transform_matrix_, transform_array_);
    env->GetFloatArrayRegion(transform_array_, 0, 16, transform_);
    int64_t timestamp = env->CallLongMethod(surface_texture_, get_timestamp_);
    if (frame_timestamp_ns != nullptr) *frame_timestamp_ns = timestamp;
    frames_++;
    return true;
}

void StereoRenderer::render_eye(int eye, GLuint framebuffer, int width, int height) {
    glBindFramebuffer(GL_FRAMEBUFFER, framebuffer);
    glViewport(0, 0, width, height);
    glDisable(GL_DEPTH_TEST);
    glDisable(GL_BLEND);
    glDisable(GL_SCISSOR_TEST);
    glClearColor(0, 0, 0, 1);
    glClear(GL_COLOR_BUFFER_BIT);
    if (!has_frame()) return;

    glUseProgram(program_);
    glBindVertexArray(vao_);
    glActiveTexture(GL_TEXTURE0);
    glBindTexture(GL_TEXTURE_EXTERNAL_OES, external_texture_);
    glUniform1i(glGetUniformLocation(program_, "u_texture"), 0);
    glUniformMatrix4fv(u_transform_, 1, GL_FALSE, transform_);
    glUniform1i(u_transfer_, transfer_);
    if (layout_ == SIDE_BY_SIDE) {
        glUniform2f(u_uv_offset_, eye == 0 ? 0.0f : 0.5f, 0.0f);
        glUniform2f(u_uv_scale_, 0.5f, 1.0f);
    } else {
        glUniform2f(u_uv_offset_, 0.0f, 0.0f);
        glUniform2f(u_uv_scale_, 1.0f, 1.0f);
    }
    glDrawArrays(GL_TRIANGLES, 0, 3);
    glBindVertexArray(0);
    glBindFramebuffer(GL_FRAMEBUFFER, 0);
}

void StereoRenderer::probe_rows(GLuint framebuffer, int width, int height, bool ten_bit) {
    // The test pattern's three bands, in image space (v = 0 at the top).
    static const float kRows[] = {1.0f / 6.0f, 0.5f, 5.0f / 6.0f};
    // The patch row only needs a few samples; the gradient rows need one per level to show whether
    // 8-bit levels actually survived the codec, which is what banding is.
    static const int kRowSamples[] = {64, 256, 256};
    // A 10-bit swapchain has to be read as packed 2-10-10-10, or the precision we are measuring is
    // thrown away by the driver converting down to bytes.
    std::vector<uint8_t> row(size_t(width) * 4);
    std::vector<uint32_t> packed(static_cast<size_t>(width));
    glBindFramebuffer(GL_FRAMEBUFFER, framebuffer);
    LOGI("XRPIX grid=%d eye=%dx%d depth=%d", kRowSamples[1], width, height, ten_bit ? 10 : 8);
    for (size_t r = 0; r < sizeof(kRows) / sizeof(kRows[0]); ++r) {
        const float v = kRows[r];
        const int samples = kRowSamples[r];
        const int y = std::min(height - 1, std::max(0, int((1.0f - v) * height)));   // GL reads bottom-up
        if (ten_bit) {
            glReadPixels(0, y, width, 1, GL_RGBA, GL_UNSIGNED_INT_2_10_10_10_REV, packed.data());
        } else {
            glReadPixels(0, y, width, 1, GL_RGBA, GL_UNSIGNED_BYTE, row.data());
        }
        const GLenum error = glGetError();
        if (error != GL_NO_ERROR) LOGE("XRPROBE read failed v=%.3f gl_error=0x%x", v, error);
        for (int i = 0; i < samples; ++i) {
            const int x = std::min(width - 1, int((i + 0.5f) * width / samples));
            int r, g, b;
            if (ten_bit) {
                const uint32_t value = packed[size_t(x)];
                r = int(value & 0x3ff);
                g = int((value >> 10) & 0x3ff);
                b = int((value >> 20) & 0x3ff);
            } else {
                const uint8_t* pixel = row.data() + size_t(x) * 4;
                r = pixel[0];
                g = pixel[1];
                b = pixel[2];
            }
            LOGI("XRPIX %.4f %.4f %d %d %d", (i + 0.5f) / samples, v, r, g, b);
        }
    }
    glBindFramebuffer(GL_FRAMEBUFFER, 0);
}

void StereoRenderer::destroy(JNIEnv* env) {
    release_decoder_surface(env);
    if (transform_array_ != nullptr) {
        env->DeleteGlobalRef(transform_array_);
        transform_array_ = nullptr;
    }
    if (external_texture_) glDeleteTextures(1, &external_texture_);
    if (vao_) glDeleteVertexArrays(1, &vao_);
    if (program_) glDeleteProgram(program_);
    external_texture_ = vao_ = program_ = 0;
}
