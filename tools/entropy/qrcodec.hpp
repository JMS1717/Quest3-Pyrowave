// qrcodec.hpp: the "QR" block entropy coder for wavelet coefficients (prototype, docs/ENTROPY.md).
//
// Each 32x32 block of a band is an independent rANS stream, so a GPU decodes one block per
// thread. Inside a block, 8x8 sub-blocks in raster order: a raw flag says whether any coefficient
// is nonzero. In a nonzero 8x8, 2x2 quads in raster order:
//   - the quad's 4-bit significance pattern, with a context from the largest magnitude of the
//     quad to the left and the quad above (each clamped to 3), inside the block;
//   - for each nonzero coefficient, |q| - 1 as a 16-symbol class with a context from the sum of
//     those two maxima (13 buckets), then the class's low bits and the sign as one raw field.
// Raw bits go through the same rANS state at probability 2^-n, so a block is one word stream.
// Frequencies are 12-bit, per frame, 16 per context, sent in the frame header.
#pragma once
#include <algorithm>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <stdexcept>
#include <vector>

namespace qr {

constexpr int kClasses = 26;               // band classes (see dump_qcoef.py)
constexpr int kPatternCtx = 16;
constexpr int kMagCtx = 14;
constexpr int kCtxPerClass = kPatternCtx + kMagCtx;
constexpr int kContexts = kClasses * kCtxPerClass;
constexpr int kProbBits = 12;
constexpr uint32_t kProbScale = 1u << kProbBits;
constexpr uint32_t kRansL = 1u << 16;      // state lives in [2^16, 2^32), words are 16 bits
constexpr int kKBuckets[13] = {1, 2, 3, 4, 6, 8, 12, 16, 24, 32, 48, 64, 128};

inline int top_bit(uint32_t v) {
    int e = 0;
    while (v >>= 1) e++;
    return e;
}

inline int mag_ctx(int sum) {
    int c = 0;
    while (c < 13 && kKBuckets[c] <= sum) c++;
    return c;
}

// Magnitude class of m = |q| - 1: m < 4 directly; then the exponent e >= 2 and the bit below the
// leading one, e - 1 raw low bits; class 15 escapes to 16 raw bits.
inline void mag_class(uint32_t m, int& cls, int& nbits, uint32_t& bits) {
    if (m < 4) { cls = (int)m; nbits = 0; bits = 0; return; }
    int e = top_bit(m);
    int sub = (m >> (e - 1)) & 1;
    cls = 4 + 2 * (e - 2) + sub;
    if (cls >= 15) { cls = 15; nbits = 16; bits = m; return; }
    nbits = e - 1;
    bits = m & ((1u << nbits) - 1);
}

inline uint32_t mag_from_class(int cls, uint32_t raw) {
    if (cls < 4) return (uint32_t)cls;
    if (cls == 15) return raw;
    int e = 2 + (cls - 4) / 2, sub = (cls - 4) & 1;
    return (1u << e) | ((uint32_t)sub << (e - 1)) | raw;
}

inline int class_raw_bits(int cls) { return cls < 4 ? 0 : cls == 15 ? 16 : 1 + (cls - 4) / 2; }

struct Band {
    uint32_t cls, w, h;
    uint32_t out_offset;   // in coefficients
    uint32_t first_block;  // index into the block table
};

// Frame layout (little-endian u32 unless noted):
//   'QRF1', band count, block count, word count
//   cum[kContexts][17] u16 (cum[0] = 0, cum[16] = 4096)
//   bands: cls, w, h, out_offset, first_block
//   blocks: u32 word offset of the block's stream, 0xffffffff for an all-zero block
//   words: u16
struct Frame {
    std::vector<uint16_t> cum;   // kContexts * 17
    std::vector<Band> bands;
    std::vector<uint32_t> block_offset;
    std::vector<uint32_t> block_band;  // not stored: derived
    std::vector<uint16_t> words;
};

// ---- encoder ----

struct Sym { uint32_t start, freq, bits; };  // bits = 0 for a modelled symbol, else raw width

struct Model {
    std::vector<uint32_t> counts = std::vector<uint32_t>(kContexts * 16, 0);
    std::vector<uint16_t> cum = std::vector<uint16_t>(kContexts * 17, 0);
    std::vector<uint16_t> freq = std::vector<uint16_t>(kContexts * 16, 0);

    void normalize() {
        for (int c = 0; c < kContexts; c++) {
            uint32_t* n = &counts[c * 16];
            uint64_t total = 0;
            for (int s = 0; s < 16; s++) total += n[s];
            uint16_t* f = &freq[c * 16];
            if (!total) {
                for (int s = 0; s < 16; s++) f[s] = kProbScale / 16;
            } else {
                int sum = 0, best = 0;
                for (int s = 0; s < 16; s++) {
                    f[s] = n[s] ? (uint16_t)std::max<uint64_t>(1, (uint64_t)n[s] * kProbScale / total) : 0;
                    sum += f[s];
                    if (f[s] > f[best]) best = s;
                }
                // fix the total on the most frequent symbol (rarely needs more than a few counts)
                int diff = (int)kProbScale - sum;
                while (diff < 0) {
                    for (int s = 0; s < 16 && diff < 0; s++)
                        if (f[s] > 1 && (s == best || f[s] > 8)) { f[s]--; diff++; }
                }
                f[best] += diff;
            }
            uint16_t* cu = &cum[c * 17];
            cu[0] = 0;
            for (int s = 0; s < 16; s++) cu[s + 1] = cu[s] + f[s];
        }
    }
};

// Walks one block in decode order and calls emit(ctx, symbol) for modelled symbols and
// raw(nbits, value) for raw bits.
template <class Emit, class Raw>
void walk_block(const int16_t* band, uint32_t w, uint32_t bx, uint32_t by, uint32_t cls, Emit emit, Raw raw) {
    uint8_t qmax[16][16] = {};
    const int base = (int)cls * kCtxPerClass;
    for (int sb = 0; sb < 16; sb++) {
        int sx = (sb & 3) * 8, sy = (sb >> 2) * 8;
        bool any = false;
        for (int y = 0; y < 8 && !any; y++)
            for (int x = 0; x < 8; x++)
                if (band[(by * 32 + sy + y) * w + bx * 32 + sx + x]) { any = true; break; }
        raw(1, any ? 1u : 0u);
        if (!any) continue;
        for (int qy = 0; qy < 4; qy++)
            for (int qx = 0; qx < 4; qx++) {
                int QX = sx / 2 + qx, QY = sy / 2 + qy;
                int left = QX ? qmax[QY][QX - 1] : 0, up = QY ? qmax[QY - 1][QX] : 0;
                int pctx = std::min(left, 3) * 4 + std::min(up, 3);
                int kctx = mag_ctx(left + up);
                int16_t v[4];
                int pat = 0, mx = 0;
                for (int i = 0; i < 4; i++) {
                    int x = bx * 32 + QX * 2 + (i & 1), y = by * 32 + QY * 2 + (i >> 1);
                    v[i] = band[y * w + x];
                    if (v[i]) pat |= 1 << i;
                    mx = std::max(mx, std::abs((int)v[i]));
                }
                emit(base + pctx, pat);
                for (int i = 0; i < 4; i++) {
                    if (!v[i]) continue;
                    int c, nb; uint32_t bits;
                    mag_class((uint32_t)std::abs((int)v[i]) - 1, c, nb, bits);
                    emit(base + kPatternCtx + kctx, c);
                    // low bits and sign in one raw field: (bits << 1) | sign
                    raw(nb + 1, (bits << 1) | (v[i] < 0 ? 1u : 0u));
                }
                qmax[QY][QX] = (uint8_t)std::min(mx, 255);
            }
    }
}

inline void encode_block_rans(const std::vector<Sym>& syms, std::vector<uint16_t>& out) {
    std::vector<uint16_t> rev;
    uint32_t x = kRansL;
    for (size_t i = syms.size(); i-- > 0;) {
        const Sym& s = syms[i];
        if (s.bits) {
            // raw value s.start in s.bits bits: a symbol of frequency 1 at precision s.bits
            uint32_t xmax = ((kRansL >> s.bits) << 16);
            while (x >= xmax) { rev.push_back((uint16_t)x); x >>= 16; }
            x = (x << s.bits) | s.start;
        } else {
            uint32_t xmax = ((kRansL >> kProbBits) << 16) * s.freq;
            while (x >= xmax) { rev.push_back((uint16_t)x); x >>= 16; }
            x = ((x / s.freq) << kProbBits) + (x % s.freq) + s.start;
        }
    }
    rev.push_back((uint16_t)x);
    rev.push_back((uint16_t)(x >> 16));
    out.insert(out.end(), rev.rbegin(), rev.rend());
}

struct InBand { uint32_t cls, w, h; const int16_t* data; };

inline Frame encode(const std::vector<InBand>& in) {
    Model model;
    auto count = [&](int ctx, int sym) { model.counts[ctx * 16 + sym]++; };
    auto none = [](int, uint32_t) {};
    for (const InBand& b : in)
        for (uint32_t by = 0; by < b.h / 32; by++)
            for (uint32_t bx = 0; bx < b.w / 32; bx++)
                walk_block(b.data, b.w, bx, by, b.cls, count, none);
    model.normalize();

    Frame f;
    f.cum = model.cum;
    uint32_t out_offset = 0;
    std::vector<Sym> syms;
    for (const InBand& b : in) {
        if (b.w % 32 || b.h % 32) throw std::runtime_error("band not a multiple of 32");
        f.bands.push_back({b.cls, b.w, b.h, out_offset, (uint32_t)f.block_offset.size()});
        out_offset += b.w * b.h;
        for (uint32_t by = 0; by < b.h / 32; by++)
            for (uint32_t bx = 0; bx < b.w / 32; bx++) {
                bool any = false;
                for (uint32_t y = 0; y < 32 && !any; y++)
                    for (uint32_t x = 0; x < 32; x++)
                        if (b.data[(by * 32 + y) * b.w + bx * 32 + x]) { any = true; break; }
                f.block_band.push_back((uint32_t)f.bands.size() - 1);
                if (!any) { f.block_offset.push_back(0xffffffffu); continue; }
                f.block_offset.push_back((uint32_t)f.words.size());
                syms.clear();
                walk_block(b.data, b.w, bx, by, b.cls,
                    [&](int ctx, int s) { syms.push_back({model.cum[ctx * 17 + s], model.freq[ctx * 16 + s], 0}); },
                    [&](int nb, uint32_t v) {
                        // raw widths above 12 are split so the renormalization bound holds
                        if (nb > 12) { syms.push_back({v >> 12, 1, (uint32_t)(nb - 12)}); syms.push_back({v & 0xfff, 1, 12}); }
                        else syms.push_back({v, 1, (uint32_t)nb});
                    });
                encode_block_rans(syms, f.words);
            }
    }
    return f;
}

// ---- reference decoder (the GPU shader mirrors this) ----

struct Reader {
    const uint16_t* w; uint32_t pos; uint32_t x;
    void init() { x = ((uint32_t)w[pos] << 16) | w[pos + 1]; pos += 2; }
    void renorm() { while (x < kRansL) x = (x << 16) | w[pos++]; }
    uint32_t raw(int n) { uint32_t v = x & ((1u << n) - 1); x >>= n; renorm(); return v; }
    uint32_t raw_wide(int n) { if (n > 12) { uint32_t hi = raw(n - 12); return (hi << 12) | raw(12); } return raw(n); }
    int sym(const uint16_t* cu) {
        uint32_t slot = x & (kProbScale - 1);
        int s = 0;
        while (cu[s + 1] <= slot) s++;
        x = (uint32_t)(cu[s + 1] - cu[s]) * (x >> kProbBits) + slot - cu[s];
        renorm();
        return s;
    }
};

inline void decode_block(const Frame& f, uint32_t block, int16_t* out) {
    const Band& b = f.bands[f.block_band[block]];
    uint32_t local = block - b.first_block;
    uint32_t bx = local % (b.w / 32), by = local / (b.w / 32);
    int16_t* o = out + b.out_offset;
    for (uint32_t y = 0; y < 32; y++)
        std::memset(&o[(by * 32 + y) * b.w + bx * 32], 0, 64);
    if (f.block_offset[block] == 0xffffffffu) return;
    Reader r{f.words.data(), f.block_offset[block], 0};
    r.init();
    uint8_t qmax[16][16] = {};
    const uint16_t* cum = f.cum.data() + b.cls * kCtxPerClass * 17;
    for (int sb = 0; sb < 16; sb++) {
        if (!r.raw(1)) continue;
        int sx = (sb & 3) * 8, sy = (sb >> 2) * 8;
        for (int qy = 0; qy < 4; qy++)
            for (int qx = 0; qx < 4; qx++) {
                int QX = sx / 2 + qx, QY = sy / 2 + qy;
                int left = QX ? qmax[QY][QX - 1] : 0, up = QY ? qmax[QY - 1][QX] : 0;
                int pat = r.sym(cum + (std::min(left, 3) * 4 + std::min(up, 3)) * 17);
                const uint16_t* kc = cum + (kPatternCtx + mag_ctx(left + up)) * 17;
                int mx = 0;
                for (int i = 0; i < 4; i++) {
                    if (!(pat >> i & 1)) continue;
                    int c = r.sym(kc);
                    uint32_t f = r.raw_wide(class_raw_bits(c) + 1);
                    uint32_t m = mag_from_class(c, f >> 1) + 1;
                    int v = (f & 1) ? -(int)m : (int)m;
                    o[(by * 32 + QY * 2 + (i >> 1)) * b.w + bx * 32 + QX * 2 + (i & 1)] = (int16_t)v;
                    mx = std::max(mx, (int)m);
                }
                qmax[QY][QX] = (uint8_t)std::min(mx, 255);
            }
    }
}

}  // namespace qr
