// SPDX-License-Identifier: MIT
// Diagnostic: native Vulkan ready FD -> GLES server wait before reading all
// three AHB slots. Compare against synchronous decode; not an FPS/optical test.
#include "pyroclient.h"
#include "gpu_readback_android.h"
#include <android/hardware_buffer.h>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <memory>
#include <poll.h>
#include <unistd.h>
#include <zlib.h>

struct Wave { int32_t params[8]{}; std::vector<unsigned char> bytes; };
static bool load(const char *path,Wave &w) {
    FILE *f=fopen(path,"rb"); if (!f) return false;
    char magic[8]; uint32_t len=0;
    bool ok=fread(magic,1,8,f)==8 && !memcmp(magic,"PYROWAVE",8)
        && fread(w.params,sizeof(w.params),1,f)==1 && fread(&len,4,1,f)==1
        && len && len<=256u*1024*1024;
    if (ok) {w.bytes.resize(len); ok=fread(w.bytes.data(),1,len,f)==len;}
    fclose(f); return ok;
}
struct Image {
    EGLDisplay display; EGLImageKHR image; PFNEGLDESTROYIMAGEKHRPROC destroy;
    AHardwareBuffer *buffer;
    ~Image() {destroy(display,image); AHardwareBuffer_release(buffer);}
};
int main(int argc,char **argv) {
    if (argc!=3) {fprintf(stderr,"usage: %s A.wave B.wave (Haar/compute, debug.q3pw.ready_fd=1)\n",argv[0]);return 2;}
    Wave a,b; if (!load(argv[1],a)||!load(argv[2],b)) return 2;
    for (int i=0;i<5;++i) if (a.params[i]!=b.params[i]) return 2;
    const int width=a.params[0],height=a.params[1];
    if (width<=0||height<=0||uint64_t(width)*height*4>256ull*1024*1024) return 2;
    pyroclient *raw=pyroclient_create_ex(width,height,a.params[3]==1,a.params[4],3,2,2);
    if (!raw) return 1;
    std::unique_ptr<pyroclient,decltype(&pyroclient_destroy)> client(raw,&pyroclient_destroy);
    std::string error;
    auto fail=[&](){fprintf(stderr,"FAIL %s\n",error.c_str());return 1;};
    auto push=[&](const Wave &w) {
        pyroclient_clear(raw);
        if (pyroclient_push_packet(raw,w.bytes.data(),w.bytes.size())!=1) {error="Complete packet assembly failed";return false;}
        return true;
    };
    q3pw::ReadbackContext context;
    if (!context.initialize(error)) return fail();
    const char *extensions=eglQueryString(context.display(),EGL_EXTENSIONS);
    if (!q3pw::has_extension(extensions,"EGL_ANDROID_native_fence_sync") ||
        !q3pw::has_extension(extensions,"EGL_KHR_wait_sync")) {error="Ready-fence extensions unavailable";return fail();}
    auto native=reinterpret_cast<PFNEGLGETNATIVECLIENTBUFFERANDROIDPROC>(eglGetProcAddress("eglGetNativeClientBufferANDROID"));
    auto image_create=reinterpret_cast<PFNEGLCREATEIMAGEKHRPROC>(eglGetProcAddress("eglCreateImageKHR"));
    auto image_destroy=reinterpret_cast<PFNEGLDESTROYIMAGEKHRPROC>(eglGetProcAddress("eglDestroyImageKHR"));
    auto create=reinterpret_cast<PFNEGLCREATESYNCKHRPROC>(eglGetProcAddress("eglCreateSyncKHR"));
    auto destroy=reinterpret_cast<PFNEGLDESTROYSYNCKHRPROC>(eglGetProcAddress("eglDestroySyncKHR"));
    auto wait=reinterpret_cast<PFNEGLWAITSYNCKHRPROC>(eglGetProcAddress("eglWaitSyncKHR"));
    if (!native||!image_create||!image_destroy||!create||!destroy||!wait) {error="EGL entry points unavailable";return fail();}
    std::vector<std::unique_ptr<Image>> images;
    std::vector<unsigned char> ref_a,ref_b,actual;
    // Initialize every EGL import and the same persistent GLES context before
    // early submission, avoiding expensive context creation in the fenced path.
    for (int i=0;i<3;++i) {
        const Wave &w=i==1?b:a;
        AHardwareBuffer *buffer=nullptr; pyroclient_frame_info info{};
        if (!push(w)||pyroclient_decode(raw,&buffer,&info)!=0||!buffer||!info.complete) {error="Synchronous reference decode failed";return fail();}
        const EGLint attrs[]={EGL_IMAGE_PRESERVED_KHR,EGL_TRUE,EGL_NONE};
        EGLImageKHR img=image_create(context.display(),EGL_NO_CONTEXT,EGL_NATIVE_BUFFER_ANDROID,native(buffer),attrs);
        if (img==EGL_NO_IMAGE_KHR) {error="Reference import failed";return fail();}
        AHardwareBuffer_acquire(buffer);
        auto image=std::unique_ptr<Image>(new Image{context.display(),img,image_destroy,buffer});
        if (!q3pw::readback_egl_image(img,width,height,false,actual,error)) return fail();
        if (i==1) ref_b=actual; else if (i==0) ref_a=actual; else if (actual!=ref_a) {error="Reference slots differ";return fail();}
        images.push_back(std::move(image));
    }
    if (ref_a==ref_b) {error="Distinct reference images required";return fail();}
    int unsignaled=0,completed=0,guard_rejections=0;
    for (int i=0;i<6;++i) {
        const Wave &w=i%2?b:a;
        AHardwareBuffer *buffer=nullptr; pyroclient_frame_info info{}; int fd=-1;
        if (!push(w)||pyroclient_submit_guarded(raw,&buffer,&info,nullptr,nullptr,&fd)!=0||!buffer||fd<0) {
            if(fd>=0) close(fd); error="Early publication inactive or submission failed";return fail();
        }
        pollfd observed{fd,POLLIN,0}; int ready=poll(&observed,1,0);
        if (ready==0) ++unsignaled;
        if (ready<0||(observed.revents&(POLLERR|POLLHUP|POLLNVAL))) {close(fd);error="Invalid ready descriptor";return fail();}
        if (info.total_ms!=0||info.decode_ms!=0) {close(fd);error="Submission claimed completion";return fail();}
        if (pyroclient_push_packet(raw,w.bytes.data(),w.bytes.size())!=-3) {close(fd);error="Outstanding packet reuse permitted";return fail();}
        pyroclient_clear(raw); // Must be a no-op while command/staging is pending.
        AHardwareBuffer *rejected=nullptr; int rejected_fd=-1;
        if (pyroclient_submit_guarded(raw,&rejected,nullptr,nullptr,nullptr,&rejected_fd)!=-6||rejected||rejected_fd!=-1) {
            close(fd); if(rejected_fd>=0) close(rejected_fd);error="Second outstanding submission permitted";return fail();
        }
        ++guard_rejections;
        const EGLint attrs[]={EGL_SYNC_NATIVE_FENCE_FD_ANDROID,fd,EGL_NONE};
        EGLSyncKHR sync=create(context.display(),EGL_SYNC_NATIVE_FENCE_ANDROID,attrs);
        fd=-1; // EGL owns the descriptor, including the error path.
        if (sync==EGL_NO_SYNC_KHR) {error="Ready import failed";return fail();}
        const bool ordered=wait(context.display(),sync,0)==EGL_TRUE;
        const bool destroyed=destroy(context.display(),sync)==EGL_TRUE;
        if (!ordered||!destroyed) {error="GPU wait/destroy failed";return fail();}
        Image *image=nullptr;
        for (const auto &candidate:images) if(candidate->buffer==buffer) image=candidate.get();
        if (!image) {error="Output outside three-slot ring";return fail();}
        if (!q3pw::readback_egl_image(image->image,width,height,false,actual,error)) return fail();
        if (actual!=(i%2?ref_b:ref_a)) {error="Fenced pixels differ from synchronous reference";return fail();}
        if (pyroclient_finish_pending(raw,&info)!=0 || !std::isfinite(info.total_ms) || info.total_ms<=0 || !info.complete
            || !pyroclient_is_ready(raw,0)) {error="Verified completion or clear protection failed";return fail();}
        ++completed;
    }
    if (!unsignaled) {error="No initially unsignaled native fence observed; queued handoff not exercised";return fail();}
    // Explicit pending teardown must drain safely without another decode/clear.
    AHardwareBuffer *last=nullptr; int fd=-1;
    if (!push(a)||pyroclient_submit_guarded(raw,&last,nullptr,nullptr,nullptr,&fd)!=0||fd<0) {if(fd>=0)close(fd);error="Pending teardown submission failed";return fail();}
    close(fd); images.clear(); client.reset();
    printf("{\"probe\":\"ready_fd_gpu_handoff\",\"width\":%d,\"height\":%d,\"completed\":%d,\"unsignaled_at_export\":%d,"
           "\"guard_rejections\":%d,\"exact_pixels\":true,\"pending_teardown\":true,\"max_inflight\":1}\n",width,height,completed,unsignaled,guard_rejections);
    return 0;
}
