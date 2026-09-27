// stereo_renderer: the decoder's output texture drawn into one OpenXR swapchain image per eye.
//
// MediaCodec decodes into a Surface backed by a SurfaceTexture bound to our GL context, which gives
// us the frame as a GL_TEXTURE_EXTERNAL_OES texture (no copy through the CPU). Each XR frame we take
// the newest decoded frame and draw the half of it that belongs to the eye being rendered.
#pragma once

#include <EGL/egl.h>
#include <GLES3/gl3.h>
#include <GLES2/gl2ext.h>   // after gl3.h: it needs the GL types
#include <jni.h>

#include <cstdint>

class StereoRenderer {
public:
    // How the decoded frame carries the two eyes.
    enum Layout { SIDE_BY_SIDE = 0, SINGLE_VIEW = 1 };
    // How the swapchain expects colour: linear 8/10-bit, or PQ for an HDR swapchain.
    enum Transfer { TRANSFER_PASSTHROUGH = 0, TRANSFER_PQ = 1 };

    bool init();                       // GL thread, after the EGL context is current
    void destroy(JNIEnv* env);

    jobject create_decoder_surface(JNIEnv* env, int width, int height);   // returns a global ref
    void release_decoder_surface(JNIEnv* env);

    // Newest decoded frame -> external texture. Returns false when nothing has been decoded yet.
    // frame_timestamp_ns is the buffer timestamp MediaCodec attached (the access unit's pts).
    bool update_texture(JNIEnv* env, int64_t* frame_timestamp_ns);

    void render_eye(int eye, GLuint framebuffer, int width, int height);

    /** Reads back rows of the swapchain image we just rendered and logs them as XRPIX samples, so the
     *  colour the headset is about to show can be compared with what the PC sent. */
    void probe_rows(GLuint framebuffer, int width, int height, bool ten_bit);

    void set_layout(Layout layout) { layout_ = layout; }
    void set_transfer(Transfer transfer) { transfer_ = transfer; }
    bool has_frame() const { return frames_ > 0; }
    int64_t frames() const { return frames_; }

private:
    GLuint program_ = 0, vao_ = 0, external_texture_ = 0;
    GLint u_transform_ = -1, u_uv_offset_ = -1, u_uv_scale_ = -1, u_transfer_ = -1;
    jobject surface_texture_ = nullptr;       // global ref
    jobject decoder_surface_ = nullptr;       // global ref
    jmethodID update_tex_image_ = nullptr, get_transform_matrix_ = nullptr, get_timestamp_ = nullptr;
    jfloatArray transform_array_ = nullptr;   // global ref, reused every frame
    float transform_[16] = {1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1};
    Layout layout_ = SIDE_BY_SIDE;
    Transfer transfer_ = TRANSFER_PASSTHROUGH;
    int64_t frames_ = 0;
};
