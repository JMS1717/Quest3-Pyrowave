// SPDX-License-Identifier: MIT
// Correctness/ownership diagnostic. Readback deliberately delays B submission;
// the timings printed here are NOT a throughput or VR-latency benchmark.
#include "pyroclient.h"
#include "gpu_readback_android.h"
#include "prerecord_fixture.h"
#include <android/hardware_buffer.h>
#include <array>
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <thread>
#include <vector>

struct Wave {int32_t p[8]{}; std::vector<unsigned char> bytes;};
static bool load(const char *path, Wave &w) {
    FILE *f=fopen(path,"rb"); if(!f) return false;
    char magic[8]; uint32_t len=0;
    bool ok=fread(magic,1,8,f)==8 && !memcmp(magic,"PYROWAVE",8) &&
            fread(w.p,sizeof w.p,1,f)==1 && fread(&len,4,1,f)==1 && len && len<=256u*1024*1024;
    if(ok) {w.bytes.resize(len); ok=fread(w.bytes.data(),1,len,f)==len;}
    fclose(f); return ok;
}
int main(int argc,char **argv) {
    if(argc!=3) {fprintf(stderr,"usage: %s A.wave B.wave (Haar/Compute/420; ready/release OFF)\n",argv[0]);return 2;}
    Wave a,b; if(!load(argv[1],a)||!load(argv[2],b)) return 2;
    for(int i=0;i<5;++i) if(a.p[i]!=b.p[i]) return 2;
    if(a.p[3]==1 || a.p[0]<=0 || a.p[1]<=0 || uint64_t(a.p[0])*a.p[1]*4>256ull*1024*1024) return 2;
    setenv("PYROWAVE_NO_LINEAR_TEX","1",1);
    using Owner=std::unique_ptr<pyroclient,decltype(&pyroclient_destroy)>;
    auto create=[&]() {return Owner(pyroclient_create_prioritized(a.p[0],a.p[1],0,a.p[4],3,2,2,1),&pyroclient_destroy);};
    std::string error;
    auto fail=[&](const char *why) {fprintf(stderr,"FAIL %s %s\n",why,error.c_str());return 1;};
    std::vector<unsigned char> ref_a,ref_b,actual;
    // Separate synchronous reference owner prevents B's allocation from warming
    // the prototype's smaller A upload. Readback waits for producer completion.
    {
        auto ref=create(); if(!ref) return fail("reference create");
        for(int i=0;i<2;++i) {
            const Wave &w=i?b:a; AHardwareBuffer *out=nullptr; pyroclient_frame_info info{};
            pyroclient_clear(ref.get());
            if(pyroclient_push_packet(ref.get(),w.bytes.data(),w.bytes.size())!=1 ||
               pyroclient_decode(ref.get(),&out,&info)!=0 || !info.complete || !out ||
               !readback_android_buffer(out,false,i?ref_b:ref_a,error)) return fail("synchronous reference");
        }
    }
    if(ref_a==ref_b) return fail("reference frames must differ");
    std::vector<unsigned char> growth;
    try {growth=q3pw::padded_growth_frame(b.bytes);} catch(const std::exception& e) {return fail(e.what());}
    auto client=create(); if(!client) return fail("create");
    auto c=client.get(); std::array<AHardwareBuffer *,3> slots{};
    if(pyroclient_prerecord_enable(c)!=-1) return fail("unwarmed enable accepted");
    for(int i=0;i<3;++i) {
        pyroclient_frame_info info{}; pyroclient_clear(c);
        if(pyroclient_push_packet(c,a.bytes.data(),a.bytes.size())!=1 ||
           pyroclient_decode(c,&slots[i],&info)!=0 || !info.complete || !slots[i]) return fail("warm slots");
        if(!readback_android_buffer(slots[i],false,actual,error)||actual!=ref_a) return fail("baseline pixels");
    }
    if(slots[0]==slots[1]||slots[1]==slots[2]||slots[0]==slots[2]) return fail("ring not distinct");
    if(pyroclient_prerecord_enable(c)!=0) return fail("enable inactive");
    int prepared=0,canceled=0,exact=0,before=0,after=0,exhausted=0,wrong_owner=0;
    double largest_queue=0;
    for(int i=0;i<12;++i) {
        AHardwareBuffer *first=nullptr,*second=nullptr,*rejected=nullptr;
        pyroclient_frame_info ia{},ib{};
        if(pyroclient_prerecord_start(c,a.bytes.data(),a.bytes.size(),&first,&ia,nullptr,nullptr)!=0 ||
           !first || !ia.complete || ia.total_ms!=0 || ia.decode_ms!=0) return fail("start claims completion or failed");
        if(pyroclient_prerecord_start(c,b.bytes.data(),b.bytes.size(),&rejected,&ib,nullptr,nullptr)!=-6 || rejected)
            return fail("second GPU submission permitted");
        if(pyroclient_finish_pending(c,nullptr)!=-1) return fail("query collection bypass permitted");
        int foreign=0;
        std::thread other([&]() {foreign=pyroclient_prerecord_pending_status(c);}); other.join();
        if(foreign!=-1) return fail("foreign producer owner accepted"); ++wrong_owner;
        AHardwareBuffer *p0=nullptr,*p1=nullptr;
        for(auto slot:slots) if(slot!=first) {if(!p0)p0=slot;else p1=slot;}
        uint64_t generation=999;
        if(pyroclient_prerecord_prepare(c,b.bytes.data(),b.bytes.size(),p0,p1,&generation)!=-4 || generation)
            return fail("three occupied slots overwritten");
        ++exhausted;
        int status=pyroclient_prerecord_pending_status(c);
        if(status<0) return fail("pending status"); if(status==0) ++before;
        int recorded=0;
        const auto deadline=std::chrono::steady_clock::now()+std::chrono::milliseconds(20);
        do {
            recorded=pyroclient_prerecord_prepare(c,b.bytes.data(),b.bytes.size(),nullptr,nullptr,&generation);
            if(recorded==0) std::this_thread::sleep_for(std::chrono::microseconds(100));
        } while(recorded==0 && std::chrono::steady_clock::now()<deadline);
        if(recorded!=1 || !generation) return fail("conditional prepare unavailable"); ++prepared;
        status=pyroclient_prerecord_pending_status(c);
        if(status<0) return fail("pending status after prepare"); if(status==0) ++after;
        if(pyroclient_prerecord_submit(c,generation,&rejected,&ib)!=-6 || rejected)
            return fail("prepared submitted before completion/query observation");
        uint64_t duplicate=9;
        if(pyroclient_prerecord_prepare(c,b.bytes.data(),b.bytes.size(),nullptr,nullptr,&duplicate)!=-6 || duplicate)
            return fail("second preparation accepted");
        if(i%2) {
            if(pyroclient_prerecord_cancel(c,generation)!=0 || pyroclient_prerecord_cancel(c,generation)!=-1)
                return fail("cancel/double cancel");
            ++canceled;
        }
        if(pyroclient_finish_pending(c,&ia)!=0 || !ia.complete || ia.total_ms<=0 || ia.decode_ms<=0 ||
           !readback_android_buffer(first,false,actual,error)||actual!=ref_a) return fail("A changed during B preparation/cancel");
        ++exact;
        if(i%2) {
            if(pyroclient_prerecord_submit(c,generation,&rejected,&ib)!=-4 || rejected)
                return fail("stale canceled generation submitted");
            if(pyroclient_prerecord_start(c,b.bytes.data(),b.bytes.size(),&second,&ib,first,nullptr)!=0)
                return fail("replacement after cancel");
        } else {
            if(pyroclient_prerecord_submit(c,generation,&second,&ib)!=0 || !second || second==first)
                return fail("prepared submit/reserved slot");
            largest_queue=std::max(largest_queue,pyroclient_prerecord_queue_ms(c));
        }
        if(pyroclient_finish_pending(c,&ib)!=0 || !ib.complete || !std::isfinite(ib.total_ms) || ib.total_ms<=0 ||
           ib.record_ms<0 || ib.wait_ms<0 || !readback_android_buffer(second,false,actual,error)||actual!=ref_b)
            return fail("B/replacement exact completion");
        ++exact;
    }
    // Complete coefficient-preserving padded packets exceed the existing upload
    // capacity. No B recording/growth is allowed while A is submitted. A stays
    // exact, and an ordinary start after verified A drain can safely grow and
    // produces B's exact pixels (also proves the fixture's unused-tail semantics).
    AHardwareBuffer *ga=nullptr,*gb=nullptr; pyroclient_frame_info gai{},gbi{}; uint64_t gg=0;
    if(pyroclient_prerecord_start(c,a.bytes.data(),a.bytes.size(),&ga,&gai,nullptr,nullptr)!=0 ||
       pyroclient_prerecord_prepare(c,growth.data(),growth.size(),nullptr,nullptr,&gg)!=-7 || gg)
        return fail("payload growth was not safely deferred");
    if(pyroclient_finish_pending(c,&gai)!=0 || !readback_android_buffer(ga,false,actual,error) || actual!=ref_a)
        return fail("growth rejection changed A");
    if(pyroclient_prerecord_start(c,growth.data(),growth.size(),&gb,&gbi,ga,nullptr)!=0 ||
       pyroclient_finish_pending(c,&gbi)!=0 || !readback_android_buffer(gb,false,actual,error) || actual!=ref_b)
        return fail("drained upload growth changed B");
    // Cancellation then continued reuse exercises Granite context retirement.
    // Teardown additionally owns both a submitted A and an unsubmitted B.
    AHardwareBuffer *last=nullptr; pyroclient_frame_info info{}; uint64_t generation=0;
    if(pyroclient_prerecord_start(c,a.bytes.data(),a.bytes.size(),&last,&info,nullptr,nullptr)!=0 ||
       pyroclient_prerecord_prepare(c,b.bytes.data(),b.bytes.size(),nullptr,nullptr,&generation)!=1)
        return fail("outstanding teardown setup");
    client.reset();
    if(a.p[0]>=4096 && after==0) return fail("native preparation never overlapped an unsignaled A");
    printf("{\"probe\":\"prerecord_serial_gpu\",\"width\":%d,\"height\":%d,\"prepared\":%d,\"canceled\":%d,"
           "\"exact_frames\":%d,\"unsignaled_before_prepare\":%d,\"unsignaled_after_prepare\":%d,"
           "\"slot_exhaustion_rejections\":%d,\"foreign_owner_rejections\":%d,\"max_gpu_submissions\":1,"
           "\"max_prepared\":1,\"outstanding_teardown\":true,\"exact_pixels\":true,"
           "\"growth_rejected_while_pending\":true,\"drained_growth_exact\":true,"
           "\"granite_stage_timestamps\":false,\"completion_queries\":true,\"largest_proof_queue_ms\":%.6f,"
           "\"performance_benchmark\":false}\n",a.p[0],a.p[1],prepared,canceled,exact,before,after,exhausted,wrong_owner,largest_queue);
}
