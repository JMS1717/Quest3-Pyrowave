// Host test for the headset decode-path choice (no Vulkan, no Android): the dashboard's hint,
// the research property override and the CDF 5/3 constraint, in that order of precedence.
//   c++ -std=c++17 decode_path_test.cpp -o decode_path_test && ./decode_path_test
#include "decode_path.h"
#include <cstdio>
#include <cstring>

static int failures = 0;
static void check(bool heuristic_fragment, int hint, const char *prop, bool legall53,
                  bool want_fragment, const char *want_reason) {
    DecodePathChoice c = choose_decode_path(heuristic_fragment, hint, prop, legall53);
    if (c.fragment != want_fragment || strcmp(c.reason, want_reason) != 0) {
        printf("FAIL heuristic=%d hint=%d prop=%s 53=%d -> %s (%s), want %s (%s)\n",
               heuristic_fragment, hint, prop ? prop : "(none)", legall53,
               c.fragment ? "fragment" : "compute", c.reason,
               want_fragment ? "fragment" : "compute", want_reason);
        failures++;
    }
}

int main() {
    // no hint, no property: the driver heuristic (fragment on Qualcomm)
    check(true, 0, nullptr, false, true, "auto");
    check(false, 0, "", false, false, "auto");
    // the dashboard's choice
    check(true, 2, nullptr, false, false, "setting");
    check(false, 1, nullptr, false, true, "setting");
    // the research property wins over the setting
    check(true, 2, "fragment", false, true, "forced");
    check(true, 1, "compute", false, false, "forced");
    // an unknown property value is ignored
    check(true, 2, "bogus", false, false, "setting");
    // CDF 5/3 exists only on the compute path, whatever asked for fragment
    check(true, 1, nullptr, true, false, "forced by wavelet");
    check(true, 0, "fragment", true, false, "forced by wavelet");
    // an out-of-range hint is auto
    check(false, 7, nullptr, false, false, "auto");
    if (failures) return 1;
    printf("decode_path_test: all passed\n");
    return 0;
}
