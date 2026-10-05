// When the experimental debug.q3pw.fuse_color pass may replace PyroWave's final luma iDWT plus the
// separate colour conversion. Default off. fuse_color.frag reproduces exactly one reference: the
// original pair-local Haar final level at precision 1, followed by fragment colour conversion.
// Every other mode keeps the two-pass path, and PyroWave re-checks its own side
// (pyrowave_decoder_set_skip_final_luma_idwt), so a refusal is a log line, never unwritten luma.
#pragma once
#include <cstring>

struct FuseColorInputs {
    bool requested;              // debug.q3pw.fuse_color == "1"
    bool haar, chroma444;
    bool fragment_convert;       // RGBA written as a colour attachment, not by compute
    bool fragment_decode_path;   // PyroWave fragment iDWT (never chosen for Haar)
    const char *precision_env;   // PYROWAVE_PRECISION as pyroclient set it, or null
    const char *fused_haar_env;  // PYROWAVE_FUSED_HAAR
    const char *haar_pairs_env;  // PYROWAVE_HAAR_PAIRS
};

struct FuseColorChoice {
    bool enabled;
    const char *reason;
};

inline FuseColorChoice choose_fuse_color(const FuseColorInputs &in) {
    if (!in.requested) return {false, "not requested"};
    if (!in.haar) return {false, "needs Haar"};
    if (in.chroma444) return {false, "needs 4:2:0"};
    if (in.fragment_decode_path) return {false, "needs the compute decode path"};
    if (!in.fragment_convert) return {false, "needs fragment colour conversion"};
    // PyroWave defaults to precision 1 and parses this value the same way (strtol, 0..2).
    if (in.precision_env && strcmp(in.precision_env, "1") != 0) return {false, "needs precision 1"};
    if (in.fused_haar_env && !strcmp(in.fused_haar_env, "1")) return {false, "fused multilevel Haar is active"};
    if (in.haar_pairs_env && (!strcmp(in.haar_pairs_env, "64-column") || !strcmp(in.haar_pairs_env, "64-row") ||
                              !strcmp(in.haar_pairs_env, "128-row")))
        return {false, "dedicated Haar pair kernels are active"};
    return {true, "eligible"};
}
