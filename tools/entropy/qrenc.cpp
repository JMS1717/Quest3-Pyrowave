// qrenc <in.qcf> <out.qrf>: encode dumped coefficients (dump_qcoef.py) with the QR coder, check the
// CPU reference decoder reproduces them, and write the frame for qrbench.
#include <chrono>
#include <cstdio>
#include <fstream>
#include <iterator>
#include <string>

#include "qrcodec.hpp"

static std::vector<char> read_file(const char* path) {
    std::ifstream f(path, std::ios::binary);
    if (!f) throw std::runtime_error(std::string("cannot open ") + path);
    return std::vector<char>(std::istreambuf_iterator<char>(f), {});
}

int main(int argc, char** argv) {
    if (argc < 3) { std::fprintf(stderr, "usage: qrenc <in.qcf> <out.qrf>\n"); return 2; }
    std::vector<char> in = read_file(argv[1]);
    if (std::memcmp(in.data(), "QCF1", 4)) { std::fprintf(stderr, "not a QCF1 file\n"); return 1; }
    uint32_t nb;
    std::memcpy(&nb, in.data() + 4, 4);
    size_t pos = 8;
    std::vector<qr::InBand> bands;
    uint64_t total = 0, nonzero = 0;
    for (uint32_t i = 0; i < nb; i++) {
        uint32_t h3[3];
        std::memcpy(h3, in.data() + pos, 12);
        pos += 12;
        const int16_t* d = reinterpret_cast<const int16_t*>(in.data() + pos);
        bands.push_back({h3[0], h3[1], h3[2], d});
        pos += (size_t)h3[1] * h3[2] * 2;
        total += (uint64_t)h3[1] * h3[2];
        for (uint64_t k = 0; k < (uint64_t)h3[1] * h3[2]; k++) nonzero += d[k] != 0;
    }

    auto t0 = std::chrono::steady_clock::now();
    qr::Frame f = qr::encode(bands);
    auto t1 = std::chrono::steady_clock::now();

    std::vector<int16_t> out(total);
    for (uint32_t b = 0; b < f.block_offset.size(); b++) qr::decode_block(f, b, out.data());
    auto t2 = std::chrono::steady_clock::now();
    uint64_t bad = 0, o = 0;
    for (const qr::InBand& b : bands)
        for (uint64_t k = 0; k < (uint64_t)b.w * b.h; k++, o++) bad += out[o] != b.data[k];

    uint32_t live = 0;
    for (uint32_t off : f.block_offset) live += off != 0xffffffffu;
    size_t payload = f.words.size() * 2;
    size_t table = (size_t)live * 2 + f.block_offset.size() / 8 + qr::kContexts * 16 * 12 / 8;
    std::printf("bands %u, coefficients %llu, nonzero %llu, blocks %zu (%u coded)\n", nb,
                (unsigned long long)total, (unsigned long long)nonzero, f.block_offset.size(), live);
    std::printf("payload %zu bytes + tables %zu = %zu bytes (%.3f bits per coefficient)\n", payload, table,
                payload + table, (payload + table) * 8.0 / total);
    std::printf("encode %.0f ms, CPU decode %.0f ms, mismatches %llu\n",
                std::chrono::duration<double, std::milli>(t1 - t0).count(),
                std::chrono::duration<double, std::milli>(t2 - t1).count(), (unsigned long long)bad);

    std::ofstream w(argv[2], std::ios::binary);
    uint32_t hdr[4] = {0x31465251u /* 'QRF1' */, (uint32_t)f.bands.size(), (uint32_t)f.block_offset.size(),
                       (uint32_t)f.words.size()};
    w.write((const char*)hdr, sizeof(hdr));
    w.write((const char*)f.cum.data(), f.cum.size() * 2);
    w.write((const char*)f.bands.data(), f.bands.size() * sizeof(qr::Band));
    w.write((const char*)f.block_offset.data(), f.block_offset.size() * 4);
    w.write((const char*)f.words.data(), f.words.size() * 2);
    return bad ? 1 : 0;
}
