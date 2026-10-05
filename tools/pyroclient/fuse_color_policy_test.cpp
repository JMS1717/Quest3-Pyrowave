// Host test for debug.q3pw.fuse_color eligibility (no Vulkan, no Android): the one reference
// fuse_color.frag reproduces, and every mode that must keep the two-pass path.
//   c++ -std=c++17 fuse_color_policy_test.cpp -o fuse_color_policy_test && ./fuse_color_policy_test
#include "fuse_color_policy.h"
#include <cstdio>
#include <initializer_list>

static int failures = 0;
static void check(const char *name, FuseColorInputs in, bool want, const char *want_reason) {
    FuseColorChoice c = choose_fuse_color(in);
    if (c.enabled != want || strcmp(c.reason, want_reason) != 0) {
        printf("FAIL %s -> %d (%s), want %d (%s)\n", name, c.enabled, c.reason, want, want_reason);
        failures++;
    }
}

int main() {
    // requested, Haar, 4:2:0, compute decode, fragment convert, default precision, no other kernels
    const FuseColorInputs base = {true, true, false, true, false, nullptr, nullptr, nullptr};
    check("eligible", base, true, "eligible");
    FuseColorInputs in = base; in.requested = false;
    check("default off", in, false, "not requested");
    in = base; in.haar = false;
    check("CDF wavelets", in, false, "needs Haar");
    in = base; in.chroma444 = true;
    check("4:4:4", in, false, "needs 4:2:0");
    in = base; in.fragment_decode_path = true;
    check("fragment iDWT", in, false, "needs the compute decode path");
    in = base; in.fragment_convert = false;
    check("compute convert fallback", in, false, "needs fragment colour conversion");
    in = base; in.precision_env = "1";
    check("explicit precision 1", in, true, "eligible");
    in = base; in.precision_env = "0";
    check("FP16 math", in, false, "needs precision 1");
    in = base; in.precision_env = "2";
    check("FP32 storage", in, false, "needs precision 1");
    in = base; in.fused_haar_env = "1";
    check("fused multilevel Haar", in, false, "fused multilevel Haar is active");
    in = base; in.fused_haar_env = "0";
    check("fused Haar explicitly off", in, true, "eligible");
    for (const char *mode : {"64-column", "64-row", "128-row"}) {
        in = base; in.haar_pairs_env = mode;
        check(mode, in, false, "dedicated Haar pair kernels are active");
    }
    in = base; in.haar_pairs_env = "0";  // what pyroclient writes for an unknown property value
    check("pairs off", in, true, "eligible");
    if (failures) return 1;
    printf("fuse_color_policy_test: all passed\n");
    return 0;
}
