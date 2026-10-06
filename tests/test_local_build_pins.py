"""The local fast build repeats CI's toolchain pins. If CI moves a pin and the local copy does not,
local builds would quietly diverge from the reviewed CI artifacts, so the copies must agree."""
import json
import re
import unittest
from pathlib import Path

from tools.local import fast_build as fb

REPO = Path(__file__).resolve().parents[1]
CI = (REPO / '.github' / 'workflows' / 'ci.yml').read_text(encoding='utf-8')
LOCK = json.loads((REPO / 'sources.lock.json').read_text(encoding='utf-8'))
FETCH = (REPO / 'tools' / 'ci' / 'fetch_sources.sh').read_text(encoding='utf-8')


def ci_env(name):
    return re.search(rf"^  {name}: '([^']+)'", CI, re.M).group(1)


class LocalBuildPinTests(unittest.TestCase):
    def test_rust_and_ndk_match_ci_and_the_lock(self):
        self.assertEqual(fb.RUST, ci_env('RUST_TOOLCHAIN'))
        self.assertEqual(fb.RUST, LOCK['rust'])
        self.assertEqual(fb.NDK, ci_env('NDK_VERSION'))
        self.assertEqual(fb.NDK, LOCK['android_ndk'])

    def test_granite_matches_the_fetch_script_and_the_lock(self):
        self.assertIn(f': "${{GRANITE_COMMIT:={fb.GRANITE_COMMIT}}}"', FETCH)
        self.assertEqual(fb.GRANITE_COMMIT, LOCK['granite']['commit'])

    def test_cargo_tools_match_ci(self):
        crates, apk = fb.CARGO_TOOLS
        self.assertIn(f"install --locked {' '.join(crates)}", CI)
        self.assertIn(' '.join(apk), CI)
        self.assertEqual(apk[apk.index('--rev') + 1], LOCK['cargo_apk']['commit'])

    def test_openxr_loader_matches_ci_and_the_lock(self):
        url, _digest = fb.OPENXR_LOADER
        self.assertIn(url, CI)
        self.assertIn(f"/download/{LOCK['openxr_loader']}/", url)

    def test_android_platform_and_build_tools_match_ci(self):
        self.assertIn('"platforms;android-35" "build-tools;35.0.0"', CI)
        source = (REPO / 'tools' / 'local' / 'fast_build.py').read_text(encoding='utf-8')
        self.assertIn('"platforms;android-35",', source)
        self.assertIn('"build-tools;35.0.0"', source)


if __name__ == '__main__':
    unittest.main()
