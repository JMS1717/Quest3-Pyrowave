import re
import struct
import tempfile
import unittest
from pathlib import Path

from tools.pyroclient.extract_pyrowave_spirv import SPIRV_MAGIC, main, program_words

ROOT = Path(__file__).resolve().parents[1]

HEADER = '''static const uint32_t spirv_bank[] =
{
	0x07230203u, 0x00010300u, 0x11111111u, 0x22222222u,
	0x07230203u, 0x00010300u, 0x33333333u,
};
	if (resolver("idwt", "FP16") == 0)
	{
		layout.unserialize(reflection_bank + 1740, 348);
		this->idwt[1] = device.request_program(spirv_bank + 0, 16, &layout);
	}
	if (resolver("idwt", "FP16") == 1)
	{
		layout.unserialize(reflection_bank + 2088, 348);
		this->idwt[1] = device.request_program(spirv_bank + 4, 12, &layout);
	}
'''


class ExtractShippedSpirvTests(unittest.TestCase):
    def test_selects_program_by_precision_and_fp16_variant(self):
        self.assertEqual(program_words(HEADER, 'idwt', 1, 0), [SPIRV_MAGIC, 0x00010300, 0x11111111, 0x22222222])
        self.assertEqual(program_words(HEADER, 'idwt', 1, 1), [SPIRV_MAGIC, 0x00010300, 0x33333333])

    def test_missing_or_misaligned_programs_are_errors(self):
        with self.assertRaises(ValueError):
            program_words(HEADER, 'idwt', 0, 0)
        with self.assertRaises(ValueError):
            program_words(HEADER.replace('spirv_bank + 4, 12', 'spirv_bank + 2, 8'), 'idwt', 1, 1)
        with self.assertRaises(ValueError):
            program_words(HEADER.replace('spirv_bank + 4, 12', 'spirv_bank + 4, 10'), 'idwt', 1, 1)

    def test_writes_little_endian_words(self):
        with tempfile.TemporaryDirectory() as tmp:
            header, out = Path(tmp) / 'slangmosh.hpp', Path(tmp) / 'idwt.spv'
            header.write_text(HEADER)
            self.assertEqual(main([str(header), 'idwt', '1', '1', str(out)]), 0)
            self.assertEqual(struct.unpack('<3I', out.read_bytes())[0], SPIRV_MAGIC)


class FuseColorLifetimeTests(unittest.TestCase):
    """The reviewed design hands the skip to PyroWave per decoder, never through the environment."""

    def test_no_process_wide_fuse_color_variable(self):
        sources = [*ROOT.glob('tools/pyroclient/*.cpp'), *ROOT.glob('tools/pyroclient/*.h'),
                   ROOT / 'patches/pyrowave-fuse-color.patch']
        for path in sources:
            self.assertNotIn('PYROWAVE_FUSE_COLOR', path.read_text(errors='replace'), path)

    def test_skip_is_enabled_only_after_the_fused_pipeline_exists(self):
        text = (ROOT / 'tools/pyroclient/pyroclient.cpp').read_text()
        create = text.index('c->create_fuse_color()')
        skip = text.index('pyrowave_decoder_set_skip_final_luma_idwt(c->decoder, 1)')
        active = text.index('c->fuse_color = true')
        self.assertLess(create, skip)
        self.assertLess(skip, active)

    def test_decoder_refuses_modes_the_shader_does_not_mirror(self):
        patch = (ROOT / 'patches/pyrowave-fuse-color.patch').read_text()
        guard = re.search(r'bool final_luma_idwt_skippable\(\) const\n\+\t\{\n\+\t\treturn (.*?);', patch, re.S)
        self.assertIsNotNone(guard)
        for condition in ('haar &&', 'chroma == ChromaSubsampling::Chroma420', '!fragment_path', '!fused_haar',
                          'haar_pairs_mode == 0', 'get_precision() == 1'):
            self.assertIn(condition, guard.group(1))
        self.assertIn('if (enabled && !impl->final_luma_idwt_skippable())\n+\t\treturn false;', patch)

    def test_prerecord_prototype_refuses_fused_colour(self):
        text = (ROOT / 'tools/pyroclient/pyroclient.cpp').read_text()
        enable = text[text.index('extern "C" int pyroclient_prerecord_enable'):]
        self.assertIn('c->fuse_color', enable[:enable.index('return -1;')])

    def test_both_fp16_variants_are_generated_from_one_source(self):
        script = (ROOT / 'tools/pyroclient/compile_shaders.py').read_text()
        self.assertIn("'fuse_color_frag_spv.h', 'FUSE_COLOR_FRAG_SPV', ['-DFP16=0']", script)
        self.assertIn("'fuse_color_fp16_frag_spv.h', 'FUSE_COLOR_FP16_FRAG_SPV', ['-DFP16=1']", script)


if __name__ == '__main__':
    unittest.main()
