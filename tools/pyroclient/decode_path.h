// Which reconstruction path the headset decodes on. Precedence: CDF 5/3 needs the compute path;
// otherwise the research property debug.xrwired.decode_path (fragment|compute) wins; otherwise
// the dashboard's setting (hint 1 fragment, 2 compute, carried in the decoder config); otherwise
// PyroWave's own heuristic, which picks the fragment path on Qualcomm.
#pragma once
#include <cstring>

struct DecodePathChoice {
    bool fragment;
    const char *reason;  // "auto" | "setting" | "forced" | "forced by wavelet"
};

inline DecodePathChoice choose_decode_path(bool heuristic_fragment, int hint, const char *prop,
                                           bool legall53) {
    DecodePathChoice c = {heuristic_fragment, "auto"};
    if (hint == 1 || hint == 2) c = {hint == 1, "setting"};
    if (prop && !strcmp(prop, "fragment")) c = {true, "forced"};
    else if (prop && !strcmp(prop, "compute")) c = {false, "forced"};
    if (legall53 && c.fragment) c = {false, "forced by wavelet"};
    return c;
}
