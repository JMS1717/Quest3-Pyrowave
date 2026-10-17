"""CPU reference coordinates for the actual shared GLES light-warp helper."""
import sys
from pathlib import Path
from tools.quest3.foveation import axis, stereo_uv


def write(path):
    eye = (2080, 2208)
    x, y = [axis(v, dim=dim) for dim, v in enumerate(eye)]
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




def write_dynamic(path):
    """Repeated uniform updates in one GLES program, matching moving Frame gaze."""
    from tools.quest3.foveation import configured_axis, inverse
    blocks = []
    eye_size = (2176, 2176)  # 2160 panel padded by ALVR to 32 pixels
    for sx, sy in [(0., 0.), (.89, -.89), (-.89, .89), (.31, -.27)] * 4:
        x, y = [configured_axis(n, .5, shift, 2.) for n, shift in zip(eye_size, (sx, sy))]
        values = [x['ratio'], y['ratio'], 2., 2., x['c2'], y['c2']]
        params = [a[k] for k in ('c1', 'lo', 'hi', 'al', 'bl', 'ar', 'br', 'cr') for a in (x, y)]
        half = (.25 / x['encoded'], .5 / y['encoded'])
        rows = []
        for index in (0, 1):
            for u in [n / 32 for n in range(33)] + [x['lo'], x['hi']]:
                for v in [0., y['lo'], .25, .5, .75, y['hi'], 1.]:
                    encoded_x = inverse(1. - u if index else u, x)
                    if index: encoded_x = 1. - encoded_x
                    expected = ((encoded_x + index) * .5, inverse(v, y))
                    expected = (min((index + 1) * .5 - half[0], max(index * .5 + half[0], expected[0])),
                                min(1 - half[1], max(half[1], expected[1])))
                    rows.append((index, (u + index) * .5, v, *expected))
        blocks.append(' '.join(map(str, (*values, *params, *half))) + '\n'
            + '\n'.join(' '.join(map(str, row)) for row in rows) + '\n')
    Path(path).write_text(''.join(blocks))

if __name__ == '__main__':
    (write_dynamic if '--dynamic' in sys.argv else write)(sys.argv[1])
