import math
import random
import re
import unittest
from pathlib import Path
from tools.downsample import reference as ds


def cosine(size, cycles):
    return [math.cos(2 * math.pi * cycles * (i + 0.5) / size) for i in range(size)]


def rms(values):
    return math.sqrt(sum(v * v for v in values) / len(values))


class DownsampleFilterTests(unittest.TestCase):
    def test_pair_fetches_equal_direct_convolution(self):
        rng = random.Random(7)
        values = [rng.random() for _ in range(64)]
        for _ in range(2000):
            scale = rng.uniform(0.5, 3.5)
            center = rng.uniform(-2.0, 66.0)
            lo = rng.randint(0, 20)
            hi = rng.randint(lo, 63)
            self.assertAlmostEqual(ds.shader_sample(values, center, scale, lo, hi),
                                   ds.direct_sample(values, center, scale, lo, hi), places=9)

    def test_equal_render_and_stream_size_is_identity(self):
        rng = random.Random(3)
        values = [rng.random() for _ in range(2080)]
        out = ds.resample(values, 2080, ds.shader_sample)
        self.assertLess(max(abs(a - b) for a, b in zip(out, values)), 1e-12)

    def test_fetch_count_stays_bounded(self):
        for scale in (1.0, 1.48, 2.0, 3.0, 8.0):
            for center in (100.0, 100.25, 100.5, 100.75):
                self.assertLessEqual(len(ds.axis_taps(center, scale, 0, 4095)), 2 * ds.MAX_PAIRS)
        # 3072 -> 2080 per axis: a few fetches per axis, not one per source texel.
        self.assertLessEqual(len(ds.axis_taps(1000.3, 3072 / 2080, 0, 3071)), 5)

    def test_bounds_stop_the_other_eye_bleeding_in(self):
        # Side-by-side texture: left eye texels 0..99 are 0, right eye 100..199 are 1.
        values = [0.0] * 100 + [1.0] * 100
        for center in (95.0, 98.5, 99.9):
            self.assertEqual(ds.shader_sample(values, center, 2.0, 0, 99), 0.0)

    def test_supersampled_detail_above_stream_nyquist_is_suppressed(self):
        # 3072 -> 2080 per eye. 1400 cycles cannot be represented at 2080 pixels and would
        # alias to 680 cycles; it should be removed, not folded into visible moire.
        source, stream = 3072, 2080
        legacy = ds.resample(cosine(source, 1400), stream, lambda v, c, s: ds.legacy_sample(v, c))
        filtered = ds.resample(cosine(source, 1400), stream, ds.shader_sample)
        self.assertLess(rms(filtered), 0.5 * rms(legacy))

    def test_detail_the_stream_can_carry_is_kept(self):
        source, stream = 3072, 2080
        kept = rms(ds.resample(cosine(source, 400), stream, ds.shader_sample)) / rms(cosine(stream, 400))
        self.assertGreater(kept, 0.85)

    def test_downsample_beats_rendering_at_stream_size(self):
        # Ground truth is a continuous edge-rich signal. Rendering at 3072 then filtering should
        # land closer to its box-filtered stream-pixel average than rendering at 2080 directly.
        def scene(x):
            return 0.5 + 0.5 * math.copysign(1.0, math.sin(2 * math.pi * 333.3 * x)) * (x % 0.37 > 0.1)

        def point_render(size):
            return [scene((i + 0.5) / size) for i in range(size)]

        stream = 2080
        truth = [sum(scene((j + (k + 0.5) / 64) / stream) for k in range(64)) / 64 for j in range(stream)]
        native = point_render(stream)
        super_ = ds.resample(point_render(3072), stream, ds.shader_sample)
        err = lambda image: rms([a - b for a, b in zip(image, truth)])
        self.assertLess(err(super_), 0.8 * err(native))


class EmbeddedShaderTests(unittest.TestCase):
    def test_patch_embeds_the_canonical_shader(self):
        root = Path(__file__).resolve().parents[1]
        patch = (root / 'patches/quest3-alvr.patch').read_text(encoding='utf-8')
        section = re.search(r'(?ms)^diff --git a/\S+/FrameDownsample\.hlsl\.inc .*?^@@[^\n]*\n(.*?)(?=^diff --git |\Z)', patch)
        self.assertIsNotNone(section, 'FrameDownsample.hlsl.inc missing from quest3-alvr.patch')
        embedded = ''.join(line[1:] + '\n' for line in section.group(1).splitlines())
        canonical = (root / 'tools/downsample/frame_downsample.hlsl').read_text(encoding='utf-8')
        self.assertEqual(embedded, 'R"HLSL(\n' + canonical + ')HLSL"\n')
        # MSVC rejects string literals longer than 16380 characters.
        self.assertLess(len(canonical), 16000)

    def test_shader_constants_match_the_model(self):
        root = Path(__file__).resolve().parents[1]
        shader = (root / 'tools/downsample/frame_downsample.hlsl').read_text(encoding='utf-8')
        self.assertIn(f'#define MAX_SCALE {ds.MAX_SCALE}', shader)
        self.assertIn(f'#define MAX_PAIRS {ds.MAX_PAIRS}', shader)


if __name__ == '__main__':
    unittest.main()
