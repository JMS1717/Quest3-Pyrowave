"""Every hunk in patches/*.patch must hold exactly the lines its header counts.

git apply trusts the header: a hand-edited hunk that counts too few lines applies cleanly and
silently drops the rest. On 2026-10-07 that cut the last three lines of present_ycbcr.glsl and the
headset's ALVR panicked compiling it.
"""
import re
import unittest
from pathlib import Path

PATCHES = Path(__file__).resolve().parent.parent / 'patches'
HUNK = re.compile(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@')


def check(lines):
    """Yields (line number, message) for each malformed hunk."""
    i = 0
    while i < len(lines):
        m = HUNK.match(lines[i])
        if not m:
            i += 1
            continue
        start = i + 1
        old = int(m.group(2) or 1)
        new = int(m.group(4) or 1)
        i += 1
        while (old > 0 or new > 0) and i < len(lines):
            tag = lines[i][:1]
            if tag == ' ' or lines[i] == '':
                old -= 1
                new -= 1
            elif tag == '-':
                old -= 1
            elif tag == '+':
                new -= 1
            elif tag != '\\':
                break
            i += 1
        while i < len(lines) and lines[i].startswith('\\'):
            i += 1
        if old or new:
            yield start, f'hunk ends early (old {old}, new {new} lines missing)'
        elif i < len(lines) and lines[i].startswith(('+', '-')) and not lines[i].startswith(('--- ', '+++ ')):
            yield start, f'line {i + 1} follows the counted lines: {lines[i][:60]!r}'


class PatchHunkTests(unittest.TestCase):
    def test_counts_match(self):
        patches = sorted(PATCHES.glob('*.patch'))
        self.assertTrue(patches)
        for path in patches:
            text = path.read_text(encoding='utf-8', errors='replace').replace('\r\n', '\n')
            # Binary hunks ("GIT binary patch") carry no @@ headers and are skipped.
            with self.subTest(patch=path.name):
                self.assertEqual(list(check(text.split('\n'))), [])

    def test_detects_short_count(self):
        bad = ['@@ -0,0 +1,2 @@', '+a', '+b', '+c', 'diff --git a/x b/x']
        self.assertEqual(len(list(check(bad))), 1)
        good = ['@@ -0,0 +1,3 @@', '+a', '+b', '+c', 'diff --git a/x b/x']
        self.assertEqual(list(check(good)), [])


if __name__ == '__main__':
    unittest.main()
