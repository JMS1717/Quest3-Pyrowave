"""CPU reference coordinates for the actual shared GLES light-warp helper."""
import sys
from pathlib import Path
from tools.quest3.foveation import axis, stereo_uv


def write(path):
    eye = (2080, 2208)
    x, y = [axis(v) for v in eye]
    values = [x['ratio'], y['ratio'], 1.5, 1.5, x['c2'], y['c2']]
    params = [x[k] if i % 2 == 0 else y[k] for i, k in enumerate(
        ('c1', 'c1', 'lo', 'lo', 'hi', 'hi', 'al', 'al',
         'bl', 'bl', 'ar', 'ar', 'br', 'br', 'cr', 'cr'))]
    half = (0.25 / x['encoded'], 0.5 / y['encoded'])
    rows = []
    for index in (0, 1):
        us = [n / 32 for n in range(33)] + [x['lo'], x['hi']]
        vs = [0, y['lo'], .25, .5, .75, y['hi'], 1]
        for u in us:
            for v in vs:
                source = ((u + index) * .5, v)
                expected = stereo_uv(u, v, index, eye)
                expected = (min((index + 1) * .5 - half[0], max(index * .5 + half[0], expected[0])),
                            min(1 - half[1], max(half[1], expected[1])))
                rows.append((index, *source, *expected))
    text = ' '.join(map(str, (*values, *params, *half))) + '\n'
    text += '\n'.join(' '.join(map(str, row)) for row in rows) + '\n'
    Path(path).write_text(text)


if __name__ == '__main__':
    write(sys.argv[1])
