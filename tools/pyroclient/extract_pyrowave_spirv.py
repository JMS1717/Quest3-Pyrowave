"""Extract one shipped PyroWave SPIR-V program from the generated slangmosh.hpp.

The fused-colour equivalence test must run the decoder's exact compiled iDWT, not a local
recompile, so it reads the same words the library embeds:

    python3 tools/pyroclient/extract_pyrowave_spirv.py <pyrowave>/shaders/slangmosh.hpp \
        idwt 1 0 /tmp/idwt-p1-fp16_0.spv     # program, precision index, FP16 variant, output
"""
import argparse
from pathlib import Path
import re
import struct
import sys

SPIRV_MAGIC = 0x07230203


def spirv_bank(text):
    match = re.search(r'static const uint32_t spirv_bank\[\] =\s*\{(.*?)\};', text, re.S)
    if not match:
        raise ValueError('no spirv_bank array')
    return [int(word.rstrip('uU'), 0) for word in re.findall(r'0x[0-9a-fA-F]+u?|\d+u?', match.group(1))]


def program_words(text, name, index, fp16):
    pattern = (r'if \(resolver\("' + re.escape(name) + r'", "FP16"\) == (\d+)\)\s*\{[^{}]*?this->'
               + re.escape(name) + r'\[(\d+)\] = device\.request_program\(spirv_bank \+ (\d+), (\d+), &layout\);')
    found = [(int(o), int(n)) for v, i, o, n in re.findall(pattern, text) if int(v) == fp16 and int(i) == index]
    if len(found) != 1:
        raise ValueError(f'{name}[{index}] FP16={fp16}: expected one program, found {len(found)}')
    offset, size = found[0]
    if size % 4:
        raise ValueError(f'{name}[{index}] FP16={fp16}: size {size} is not whole words')
    bank = spirv_bank(text)
    words = bank[offset:offset + size // 4]
    if len(words) != size // 4 or words[0] != SPIRV_MAGIC:
        raise ValueError(f'{name}[{index}] FP16={fp16}: not a SPIR-V module at word {offset}')
    return words


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('header', type=Path)
    parser.add_argument('program')
    parser.add_argument('precision', type=int)
    parser.add_argument('fp16', type=int, choices=(0, 1))
    parser.add_argument('output', type=Path)
    args = parser.parse_args(argv)
    words = program_words(args.header.read_text(), args.program, args.precision, args.fp16)
    args.output.write_bytes(struct.pack(f'<{len(words)}I', *words))
    print(f'{args.program}[{args.precision}] FP16={args.fp16}: {len(words) * 4} bytes -> {args.output}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
