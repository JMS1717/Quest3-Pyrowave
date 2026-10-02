#include "gpu_readback_gles.h"
#include <GLES3/gl3.h>
#include <GLES2/gl2ext.h>
#include <algorithm>
#include <cstring>
#include <limits>
#include <new>
#include <sstream>

namespace q3pw {
bool has_extension(const char *list, const char *name) {
    if (!list || !name || !*name || std::strchr(name, ' ')) return false;
    const size_t length = std::strlen(name);
    for (const char *p = list; (p = std::strstr(p, name)); p += length) {
        if ((p == list || p[-1] == ' ') && (p[length] == '\0' || p[length] == ' ')) return true;
    }
    return false;
}

static bool egl_failure(const char *stage, std::string &error) {
    std::ostringstream stream;
    stream << stage << ": EGL error 0x" << std::hex << eglGetError();
    error = stream.str();
    return false;
}

ReadbackContext::~ReadbackContext() {
    if (display_ == EGL_NO_DISPLAY) return;
    if (context_ != EGL_NO_CONTEXT) {
        if (eglGetCurrentContext() == context_) glFinish();
        eglMakeCurrent(display_, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
        eglDestroyContext(display_, context_);
    }
    if (surface_ != EGL_NO_SURFACE) eglDestroySurface(display_, surface_);
    if (initialized_) eglTerminate(display_);
}

bool ReadbackContext::initialize(std::string &error) {
    error.clear();
    if (initialized_ || context_ != EGL_NO_CONTEXT) {
        error = "ReadbackContext may only be initialized once";
        return false;
    }
    display_ = eglGetDisplay(EGL_DEFAULT_DISPLAY);
    if (display_ == EGL_NO_DISPLAY) return egl_failure("eglGetDisplay", error);
    if (!eglInitialize(display_, nullptr, nullptr)) return egl_failure("eglInitialize", error);
    initialized_ = true;
    if (!eglBindAPI(EGL_OPENGL_ES_API)) return egl_failure("eglBindAPI", error);
    const EGLint config_attributes[] = {
        EGL_SURFACE_TYPE, EGL_PBUFFER_BIT, EGL_RENDERABLE_TYPE, EGL_OPENGL_ES3_BIT_KHR,
        EGL_RED_SIZE, 8, EGL_GREEN_SIZE, 8, EGL_BLUE_SIZE, 8, EGL_ALPHA_SIZE, 8,
        EGL_DEPTH_SIZE, 0, EGL_STENCIL_SIZE, 0, EGL_SAMPLE_BUFFERS, 0, EGL_NONE};
    EGLConfig config = nullptr;
    EGLint count = 0;
    if (!eglChooseConfig(display_, config_attributes, &config, 1, &count) || count != 1) {
        return egl_failure("ES3 pbuffer config", error);
    }
    const EGLint surface_attributes[] = {EGL_WIDTH, 1, EGL_HEIGHT, 1, EGL_NONE};
    surface_ = eglCreatePbufferSurface(display_, config, surface_attributes);
    if (surface_ == EGL_NO_SURFACE) return egl_failure("eglCreatePbufferSurface", error);
    const EGLint context_attributes[] = {EGL_CONTEXT_CLIENT_VERSION, 3, EGL_NONE};
    context_ = eglCreateContext(display_, config, EGL_NO_CONTEXT, context_attributes);
    if (context_ == EGL_NO_CONTEXT) return egl_failure("eglCreateContext", error);
    if (!eglMakeCurrent(display_, surface_, surface_, context_)) return egl_failure("eglMakeCurrent", error);
    return true;
}

static bool gl_extension(const char *name) {
    GLint count = 0;
    glGetIntegerv(GL_NUM_EXTENSIONS, &count);
    for (GLint i = 0; i < count; i++) {
        const char *extension = reinterpret_cast<const char *>(glGetStringi(GL_EXTENSIONS, i));
        if (extension && !std::strcmp(extension, name)) return true;
    }
    return false;
}

static bool gl_ok(const char *stage, std::string &error) {
    const GLenum code = glGetError();
    if (code == GL_NO_ERROR) return true;
    std::ostringstream stream;
    stream << stage << ": GL error 0x" << std::hex << code;
    error = stream.str();
    return false;
}

struct DrawObjects {
    GLuint source = 0, target = 0, framebuffer = 0, program = 0, array = 0;
    GLuint vertex = 0, fragment = 0;
    ~DrawObjects() {
        // Even an error after a draw must finish sampling before the caller may
        // destroy its imported image or release/reuse the producer buffer.
        glFinish();
        glBindFramebuffer(GL_FRAMEBUFFER, 0);
        glBindVertexArray(0);
        glUseProgram(0);
        glBindTexture(GL_TEXTURE_EXTERNAL_OES, 0);
        glBindTexture(GL_TEXTURE_2D, 0);
        if (source) glDeleteTextures(1, &source);
        if (target) glDeleteTextures(1, &target);
        if (framebuffer) glDeleteFramebuffers(1, &framebuffer);
        if (array) glDeleteVertexArrays(1, &array);
        if (program) glDeleteProgram(program);
        if (vertex) glDeleteShader(vertex);
        if (fragment) glDeleteShader(fragment);
    }
};

static bool shader(GLuint &id, GLenum kind, const char *source, std::string &error) {
    id = glCreateShader(kind);
    if (!id) { error = "glCreateShader failed"; return false; }
    glShaderSource(id, 1, &source, nullptr);
    glCompileShader(id);
    GLint success = GL_FALSE;
    glGetShaderiv(id, GL_COMPILE_STATUS, &success);
    if (success == GL_TRUE) return true;
    char log[4096] = {};
    glGetShaderInfoLog(id, sizeof(log), nullptr, log);
    error = std::string("Readback shader: ") + log;
    return false;
}

bool readback_egl_image(EGLImageKHR image, uint32_t width, uint32_t height,
                        bool flip_y, std::vector<unsigned char> &rgba,
                        std::string &error, const BeforeReadback &before,
                        uint32_t draw_repeats) {
    rgba.clear();
    error.clear();
    if (image == EGL_NO_IMAGE_KHR || !width || !height || !draw_repeats || draw_repeats > 128 ||
        width > uint32_t(std::numeric_limits<GLsizei>::max()) ||
        height > uint32_t(std::numeric_limits<GLsizei>::max()) ||
        uint64_t(width) * height * 4 > 256ull * 1024 * 1024) {
        error = "Invalid readback extent/image or more than 256 MiB";
        return false;
    }
    if (!gl_ok("before readback", error)) return false;
    GLint max_texture = 0, max_viewport[2] = {};
    glGetIntegerv(GL_MAX_TEXTURE_SIZE, &max_texture);
    glGetIntegerv(GL_MAX_VIEWPORT_DIMS, max_viewport);
    if (width > uint32_t(max_texture) || height > uint32_t(max_texture) ||
        width > uint32_t(max_viewport[0]) || height > uint32_t(max_viewport[1])) {
        error = "Readback exceeds GLES texture/viewport limits";
        return false;
    }
    if (!gl_extension("GL_OES_EGL_image_external") ||
        !gl_extension("GL_OES_EGL_image_external_essl3")) {
        error = "Required GLES external-image extensions unavailable";
        return false;
    }
    auto image_target = reinterpret_cast<PFNGLEGLIMAGETARGETTEXTURE2DOESPROC>(
        eglGetProcAddress("glEGLImageTargetTexture2DOES"));
    if (!image_target) { error = "glEGLImageTargetTexture2DOES unavailable"; return false; }
    const char *vertex_source = R"(#version 300 es
precision highp float;
out highp vec2 uv;
void main() {
    vec2 p = vec2(float((gl_VertexID << 1) & 2), float(gl_VertexID & 2));
    uv = p;
    gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
})";
    const char *fragment_source = R"(#version 300 es
#extension GL_OES_EGL_image_external_essl3 : require
precision highp float;
precision highp samplerExternalOES;
in highp vec2 uv;
uniform samplerExternalOES source;
uniform bool flip_y;
layout(location=0) out vec4 color;
void main() {
    color = texture(source, vec2(uv.x, flip_y ? 1.0 - uv.y : uv.y));
})";
    DrawObjects objects;
    if (!shader(objects.vertex, GL_VERTEX_SHADER, vertex_source, error) ||
        !shader(objects.fragment, GL_FRAGMENT_SHADER, fragment_source, error)) return false;
    objects.program = glCreateProgram();
    glAttachShader(objects.program, objects.vertex);
    glAttachShader(objects.program, objects.fragment);
    glLinkProgram(objects.program);
    GLint linked = GL_FALSE;
    glGetProgramiv(objects.program, GL_LINK_STATUS, &linked);
    if (linked != GL_TRUE) {
        char log[4096] = {};
        glGetProgramInfoLog(objects.program, sizeof(log), nullptr, log);
        error = std::string("Readback program: ") + log;
        return false;
    }
    glActiveTexture(GL_TEXTURE0);
    glBindSampler(0, 0);
    glGenTextures(1, &objects.source);
    glBindTexture(GL_TEXTURE_EXTERNAL_OES, objects.source);
    image_target(GL_TEXTURE_EXTERNAL_OES, image);
    glTexParameteri(GL_TEXTURE_EXTERNAL_OES, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_EXTERNAL_OES, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_EXTERNAL_OES, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_EXTERNAL_OES, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    if (!gl_ok("external image import", error)) return false;
    glGenTextures(1, &objects.target);
    glBindTexture(GL_TEXTURE_2D, objects.target);
    glTexStorage2D(GL_TEXTURE_2D, 1, GL_RGBA8, width, height);
    glGenFramebuffers(1, &objects.framebuffer);
    glBindFramebuffer(GL_FRAMEBUFFER, objects.framebuffer);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, objects.target, 0);
    if (!gl_ok("readback target", error) ||
        glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE) {
        if (error.empty()) error = "Readback framebuffer incomplete";
        return false;
    }
    glViewport(0, 0, width, height);
    for (GLenum state : {GL_BLEND, GL_DEPTH_TEST, GL_STENCIL_TEST, GL_SCISSOR_TEST,
                         GL_CULL_FACE, GL_DITHER, GL_SAMPLE_ALPHA_TO_COVERAGE, GL_SAMPLE_COVERAGE}) glDisable(state);
    glColorMask(GL_TRUE, GL_TRUE, GL_TRUE, GL_TRUE);
    glGenVertexArrays(1, &objects.array);
    glBindVertexArray(objects.array);
    glUseProgram(objects.program);
    const GLint source_location = glGetUniformLocation(objects.program, "source");
    const GLint flip_location = glGetUniformLocation(objects.program, "flip_y");
    if (source_location < 0 || flip_location < 0) { error = "Missing readback uniform"; return false; }
    glUniform1i(source_location, 0);
    glUniform1i(flip_location, flip_y);
    for (uint32_t i = 0; i < draw_repeats; ++i) glDrawArrays(GL_TRIANGLES, 0, 3);
    if (!gl_ok("readback draw", error)) return false;
    if (before && !before(eglGetCurrentDisplay(), error)) return false;
    glFinish();
    glReadBuffer(GL_COLOR_ATTACHMENT0);
    glBindBuffer(GL_PIXEL_PACK_BUFFER, 0);
    glPixelStorei(GL_PACK_ALIGNMENT, 1);
    glPixelStorei(GL_PACK_ROW_LENGTH, 0);
    glPixelStorei(GL_PACK_SKIP_ROWS, 0);
    glPixelStorei(GL_PACK_SKIP_PIXELS, 0);
    std::vector<unsigned char> pixels;
    try { pixels.resize(size_t(width) * height * 4); }
    catch (const std::bad_alloc &) { error = "Readback allocation failed"; return false; }
    glReadPixels(0, 0, width, height, GL_RGBA, GL_UNSIGNED_BYTE, pixels.data());
    if (!gl_ok("glReadPixels", error)) return false;
    rgba.swap(pixels);
    return true;
}
} // namespace q3pw
