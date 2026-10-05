// SPDX-License-Identifier: MIT
#include "prerecord_state.h"
#include "prerecord_fixture.h"
#include <cassert>
#include <cstdio>
using q3pw::PrerecordState;
int main() {
    const std::array<bool,3> free{};
    {
        PrerecordState s;
        assert(s.prepare(free).slot == s.none);
        assert(!s.complete_active(true));
        auto rejected=s.start(free);
        assert(s.reject_unsubmitted_start(rejected));
        assert(!s.reject_unsubmitted_start(rejected));
        auto a=s.start(free), b=s.prepare(free);
        assert(!s.reject_unsubmitted_start(a));
        assert(a.slot<3 && b.slot<3 && a.slot!=b.slot);
        assert(s.start(free).slot==s.none); // Never two GPU submissions.
        assert(s.prepare(free).slot==s.none); // Never two prepared commands.
        assert(!s.submit(b)); // Unobserved completion is not permission.
        assert(s.complete_active(true) && s.submit(b));
        assert(s.active.generation==b.generation && s.prepared.slot==s.none);
        assert(s.complete_active(true));
    }
    {
        // Sequence + one coefficient packet: header fields/coefficient bytes
        // survive padding; parser can still advance to the original next packet.
        std::vector<unsigned char> frame={1,2,3,0x80,5,6,7,8, 0x12,0x34,3,0x50,0xab,1,2,3, 9,10,11,12};
        auto padded=q3pw::padded_growth_frame(frame);
        assert(padded.size()==8+4095*4);
        for(int i=0;i<8;++i) assert(padded[i]==frame[i]);
        assert(padded[8]==0x12 && padded[9]==0x34 && padded[10]==0xff && padded[11]==0x5f);
        for(int i=12;i<20;++i) assert(padded[i]==frame[i]);
        for(size_t i=20;i<padded.size();++i) assert(padded[i]==0);
        for(auto bad:std::array<std::vector<unsigned char>,3>{
            std::vector<unsigned char>{1,2,3},
            std::vector<unsigned char>{1,2,0,0,0,0,0,0},
            std::vector<unsigned char>{1,2,3,0,0,0,0,0}}) {
            bool rejected=false;
            try {q3pw::padded_growth_frame(bad);} catch(const std::invalid_argument&) {rejected=true;}
            assert(rejected);
        }
    }
    {
        PrerecordState s;
        auto a=s.start({true,true,false});
        assert(a.slot==2 && s.prepare({true,true,false}).slot==s.none);
        // A consumer releasing one protected slot permits a conditional reserve.
        auto b=s.prepare({false,true,false});
        assert(b.slot==0 && b.slot!=a.slot);
        assert(s.abandon(b) && s.active.generation==a.generation);
        assert(!s.abandon(b)); // Double cancellation rejected.
        auto c=s.prepare({false,true,false});
        assert(c.slot==b.slot && c.generation!=b.generation);
        assert(s.complete_active(true) && !s.submit(b) && s.submit(c));
    }
    {
        PrerecordState s;
        s.start(free); auto b=s.prepare(free);
        assert(!s.complete_active(false) && s.poisoned);
        assert(s.active.slot<3 && s.prepared.slot<3); // Retain resources on failure.
        assert(!s.submit(b) && !s.abandon(b) && s.start(free).slot==s.none);
    }
    {
        PrerecordState s;
        assert(s.start({true,true,true}).slot==s.none);
        auto a=s.start(free);
        auto b=s.prepare(free);
        assert(s.complete_active(true));
        assert(s.abandon(b)); // Cancel after completion still does not publish B.
        auto c=s.start(free);
        assert(c.generation>a.generation && c.generation>b.generation);
    }
    {
        PrerecordState s;
        for (int i=0;i<10000;++i) {
            auto a=s.start(free), b=s.prepare(free);
            assert(a.slot<3 && b.slot<3 && a.slot!=b.slot);
            assert(!s.submit(b));
            assert(s.complete_active(true));
            if (i%2) {assert(s.abandon(b));}
            else {assert(s.submit(b)); assert(s.complete_active(true));}
        }
    }
    puts("PRERECORD_STATE_PASS slot exhaustion, generation, cancel, completion, poison and bounded reuse");
}
