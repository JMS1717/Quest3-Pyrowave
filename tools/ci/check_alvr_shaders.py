"""Compile every fragment shader variant the patched ALVR client builds at run time.

ALVR compiles its GLES shaders on the headset; a syntax error there panics the client after it
connects. This composes the sources the way staging.rs and direct_eye.rs do (packed-YCbCr present
function, light foveation, FFE filter axes) and runs glslangValidator on each.

Usage: python3 tools/ci/check_alvr_shaders.py <ALVR-20.13.0 dir>
"""
import subprocess
import sys
import tempfile
from pathlib import Path


def variants(res):
    present = (res / 'present_ycbcr.glsl').read_text()
    foveation = (res / 'light_foveation.glsl').read_text()

    def with_present(src):
        return (src.replace('#version 300 es', '#version 300 es\n#define Q3PW_PRESENT_YCBCR', 1)
                .replace('// Q3PW_PRESENT_FUNCTION', present))

    # The stock staging shader declares no default float precision; the Adreno driver accepts
    # that, glslang does not.
    staging = (res / 'staging_fragment.glsl').read_text().replace(
        'uniform samplerExternalOES tex;', 'precision mediump float;\nuniform samplerExternalOES tex;', 1)
    for limited in (False, True):
        src = staging.replace('#version 300 es', '#version 300 es\n#define FIX_LIMITED_RANGE', 1) if limited else staging
        yield f'staging_limited{int(limited)}', src
        yield f'staging_present_limited{int(limited)}', with_present(src)
    direct = (res / 'direct_eye_fragment.glsl').read_text()
    for ffe in (False, True):
        src = (direct.replace('#version 300 es', '#version 300 es\n#define Q3PW_FFE')
               .replace('// Q3PW_FOVEATION_FUNCTION', foveation)) if ffe else direct
        yield f'direct_ffe{int(ffe)}', src
        for axes in range(4 if ffe else 1):
            defines = ''.join(f'\n#define {name}' for bit, name in ((1, 'Q3PW_FFE_FILTER_X'), (2, 'Q3PW_FFE_FILTER_Y'))
                              if axes & bit)
            yield f'direct_present_ffe{int(ffe)}_axes{axes}', with_present(src).replace(
                '#version 300 es', '#version 300 es' + defines, 1)


def main():
    res = Path(sys.argv[1]) / 'alvr' / 'graphics' / 'resources'
    failures = 0
    with tempfile.TemporaryDirectory() as tmp:
        for name, src in variants(res):
            path = Path(tmp) / f'{name}.frag'
            path.write_text(src)
            run = subprocess.run(['glslangValidator', str(path)], capture_output=True, text=True)
            print(name, 'ok' if run.returncode == 0 else 'FAILED')
            if run.returncode:
                failures += 1
                print(run.stdout + run.stderr)
    sys.exit(1 if failures else 0)


if __name__ == '__main__':
    main()
