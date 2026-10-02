// Run on Mesa software GLES in CI, never as a Quest performance benchmark.
#include "gpu_readback_gles.h"
#include <GLES3/gl3.h>
#include <algorithm>
#include <cstdio>
#include <limits>

static bool check(bool value, const char *message) {
    if (!value) std::fprintf(stderr, "FAIL %s\n", message);
    return value;
}

int main() {
    std::string error;
    q3pw::ReadbackContext context;
    if (!context.initialize(error)) { std::fprintf(stderr, "%s\n", error.c_str()); return 1; }
    std::printf("Software GLES readback test: %s\n", reinterpret_cast<const char *>(glGetString(GL_RENDERER)));
    bool ok = check(!q3pw::has_extension("EGL_KHR_image_base_suffix", "EGL_KHR_image_base"), "extension suffix must not match");
    ok &= check(q3pw::has_extension("X EGL_KHR_image_base Y", "EGL_KHR_image_base"), "exact extension token");
    auto create = reinterpret_cast<PFNEGLCREATEIMAGEKHRPROC>(eglGetProcAddress("eglCreateImageKHR"));
    auto destroy = reinterpret_cast<PFNEGLDESTROYIMAGEKHRPROC>(eglGetProcAddress("eglDestroyImageKHR"));
    if (!create || !destroy || !q3pw::has_extension(eglQueryString(context.display(), EGL_EXTENSIONS), "EGL_KHR_gl_texture_2D_image")) {
        std::fprintf(stderr, "Mesa EGL texture image import unavailable\n"); return 1;
    }
    for (const auto &size : {std::pair<uint32_t, uint32_t>{7, 5}, {17, 9}}) {
        const uint32_t width = size.first, height = size.second;
        std::vector<unsigned char> original(size_t(width) * height * 4);
        for (uint32_t y = 0; y < height; y++) {
            for (uint32_t x = 0; x < width; x++) {
                const size_t p = (size_t(y) * width + x) * 4;
                original[p] = static_cast<unsigned char>(x * 43 + y * 17);
                original[p+1] = static_cast<unsigned char>(x * 19 + y * 67);
                original[p+2] = static_cast<unsigned char>(x * 97 + y * 31);
                original[p+3] = static_cast<unsigned char>(x * 71 + y * 23);
            }
        }
        GLuint source = 0;
        glGenTextures(1, &source);
        glBindTexture(GL_TEXTURE_2D, source);
        glTexStorage2D(GL_TEXTURE_2D, 1, GL_RGBA8, width, height);
        glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, width, height, GL_RGBA, GL_UNSIGNED_BYTE, original.data());
        glFinish();
        const EGLint attributes[] = {EGL_GL_TEXTURE_LEVEL_KHR, 0, EGL_IMAGE_PRESERVED_KHR, EGL_TRUE, EGL_NONE};
        const EGLImageKHR image = create(context.display(), context.context(), EGL_GL_TEXTURE_2D_KHR,
                                         reinterpret_cast<EGLClientBuffer>(uintptr_t(source)), attributes);
        if (image == EGL_NO_IMAGE_KHR) { std::fprintf(stderr, "Mesa EGL image creation failed\n"); return 1; }
        std::vector<unsigned char> actual;
        // Poison pack state: the reader must supply contiguous bytes to a CPU
        // pointer, not interpret it as an offset in somebody else's pack buffer.
        GLuint pack_buffer = 0;
        glGenBuffers(1, &pack_buffer);
        glBindBuffer(GL_PIXEL_PACK_BUFFER, pack_buffer);
        glBufferData(GL_PIXEL_PACK_BUFFER, 16384, nullptr, GL_STREAM_READ);
        glPixelStorei(GL_PACK_ROW_LENGTH, width + 13);
        glPixelStorei(GL_PACK_SKIP_ROWS, 2);
        glPixelStorei(GL_PACK_SKIP_PIXELS, 3);
        bool read = q3pw::readback_egl_image(image, width, height, false, actual, error);
        if (!read) std::fprintf(stderr, "%s\n", error.c_str());
        ok &= check(read && actual == original, "identity: asymmetric RGB/alpha, exact rows, odd width");
        std::vector<unsigned char> flipped(original.size());
        for (uint32_t y = 0; y < height; y++) {
            std::copy_n(original.data() + size_t(height-1-y) * width * 4, size_t(width) * 4,
                        flipped.data() + size_t(y) * width * 4);
        }
        read = q3pw::readback_egl_image(image, width, height, true, actual, error);
        if (!read) std::fprintf(stderr, "%s\n", error.c_str());
        ok &= check(read && actual == flipped, "explicit Y flip, exact color/alpha");
        // Update storage without replacing the image. A later import/read must
        // observe new pixels, rather than a previous frame's contents.
        for (size_t i = 0; i < original.size(); i += 4) { original[i] ^= 0xff; original[i+2] ^= 0x55; }
        glBindTexture(GL_TEXTURE_2D, source);
        glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, width, height, GL_RGBA, GL_UNSIGNED_BYTE, original.data());
        glFinish();
        read = q3pw::readback_egl_image(image, width, height, false, actual, error);
        if (!read) std::fprintf(stderr, "%s\n", error.c_str());
        ok &= check(read && actual == original, "changing pixels visible through the same EGL image");
        int hooks = 0;
        const auto before = [&](EGLDisplay display, std::string &) {
            ++hooks;
            return display == context.display();
        };
        read = q3pw::readback_egl_image(image, width, height, false, actual, error, before, 2);
        ok &= check(read && hooks == 1 && actual == original, "hook after queued repeated draws preserves pixels");
        const auto reject = [](EGLDisplay, std::string &reason) { reason = "expected rejection"; return false; };
        ok &= check(!q3pw::readback_egl_image(image, width, height, false, actual, error, reject)
                    && actual.empty() && error == "expected rejection", "hook failure cannot return stale pixels");
        ok &= check(!q3pw::readback_egl_image(image, width, height, false, actual, error, before, 0)
                    && hooks == 1, "zero queued draws rejected before hook");
        ok &= check(!q3pw::readback_egl_image(image, width, height, false, actual, error, before, 129), "draw queue is bounded");
        ok &= check(!q3pw::readback_egl_image(image, 0, height, false, actual, error) && actual.empty(), "zero extent rejected without stale output");
        ok &= check(!q3pw::readback_egl_image(image, std::numeric_limits<uint32_t>::max(), height, false, actual, error), "overflow extent rejected");
        ok &= check(!q3pw::readback_egl_image(image, 8192, 8193, false, actual, error), "more than 256MiB rejected");
        ok &= check(!q3pw::readback_egl_image(EGL_NO_IMAGE_KHR, width, height, false, actual, error), "missing image rejected");
        destroy(context.display(), image);
        glDeleteTextures(1, &source);
        glDeleteBuffers(1, &pack_buffer);
        ok &= check(glGetError() == GL_NO_ERROR, "no leaked GL error");
    }
    if (ok) std::puts("PASS EGL external-image readback identity/flip/freshness/bounds/pack-state");
    return ok ? 0 : 1;
}
