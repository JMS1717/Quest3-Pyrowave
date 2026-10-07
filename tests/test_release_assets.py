"""tools/ci/release_assets.py: verifies CI artifacts and writes release assets without rebuilding."""
import hashlib
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'tools' / 'ci'))
import release_assets  # noqa: E402

SHA = 'b8e905c' + '0' * 33
CERT = ('Signer #1 certificate DN: CN=Quest3\n'
        'Signer #1 certificate SHA-256 digest: ' + 'ab' * 32 + '\n'
        'Signer #1 certificate SHA-1 digest: ' + 'cd' * 20 + '\n')


def write_sums(directory, utf16=False):
    lines = ''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n'
                    for p in sorted(directory.iterdir()) if p.name != 'SHA256SUMS.txt')
    (directory / 'SHA256SUMS.txt').write_bytes(lines.encode('utf-16') if utf16 else lines.encode())


class ReleaseAssetsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.android, self.windows, self.out = root / 'a', root / 'w', root / 'out'
        self.android.mkdir()
        self.windows.mkdir()
        with zipfile.ZipFile(self.android / 'Quest3-Pyrowave-dev.apk', 'w') as z:
            z.writestr('lib/arm64-v8a/libother.so', b'\0nothing here\0')
            z.writestr('lib/arm64-v8a/libalvr_client_openxr.so', b'\0\x7f20.13.0-quest3.pyro.62\0')
        (self.android / 'LICENSES.zip').write_bytes(b'licenses')
        (self.android / 'APK-CERTIFICATE.txt').write_text(CERT)
        (self.android / 'pyroclient_test').write_bytes(b'test binary')
        (self.windows / 'Quest3-Pyrowave-Windows.zip').write_bytes(b'streamer')
        write_sums(self.android)
        write_sums(self.windows, utf16=True)
        self.reference = root / 'reference-cert.txt'
        self.reference.write_text(CERT)

    def tearDown(self):
        self.tmp.cleanup()

    def build(self, **kw):
        args = dict(run_id='37643381349', source_sha=SHA, tag='v0.1.0-alpha.9',
                    reference_cert=self.reference, expect_version='.62')
        args.update(kw)
        return release_assets.build(self.android, self.windows, self.out, **args)

    def test_writes_release_assets_with_metadata_and_sums(self):
        meta = self.build()
        names = sorted(p.name for p in self.out.iterdir())
        self.assertEqual(names, ['APK-CERTIFICATE.txt', 'BUILD-METADATA.json', 'Quest3-Pyrowave-Android-LICENSES.zip',
                                 'Quest3-Pyrowave-Windows.zip', 'Quest3-Pyrowave-dev.apk', 'SHA256SUMS.txt'])
        self.assertEqual(meta['client_version'], '.62')
        self.assertEqual(meta['actions_run_id'], 37643381349)
        self.assertTrue(meta['certificate_matches_reference'])
        self.assertEqual(json.loads((self.out / 'BUILD-METADATA.json').read_text()), meta)
        sums = release_assets.read_sums(self.out / 'SHA256SUMS.txt')
        self.assertEqual(set(sums), set(names) - {'SHA256SUMS.txt'})
        for name, digest in sums.items():
            self.assertEqual(digest, release_assets.sha256(self.out / name))

    def test_rejects_a_different_signing_certificate(self):
        self.reference.write_text(CERT.replace('ab' * 32, 'ef' * 32))
        with self.assertRaisesRegex(release_assets.ReleaseError, 'certificate differs'):
            self.build()

    def test_rejects_an_artifact_that_does_not_match_its_sums(self):
        (self.windows / 'Quest3-Pyrowave-Windows.zip').write_bytes(b'tampered')
        with self.assertRaisesRegex(release_assets.ReleaseError, 'hash does not match'):
            self.build()

    def test_rejects_the_wrong_client_version(self):
        with self.assertRaisesRegex(release_assets.ReleaseError, 'expected .63'):
            self.build(expect_version='.63')

    def test_refuses_signing_material_in_the_inputs(self):
        (self.android / 'release.p12').write_bytes(b'key')
        with self.assertRaisesRegex(release_assets.ReleaseError, 'signing material'):
            self.build()

    def test_rejects_short_sha_and_odd_tag(self):
        with self.assertRaisesRegex(release_assets.ReleaseError, 'full commit'):
            self.build(source_sha='b8e905c')
        with self.assertRaisesRegex(release_assets.ReleaseError, 'unexpected tag'):
            self.build(tag='alpha 9; rm -rf')

    def test_cli_reports_failure_without_traceback(self):
        code = release_assets.main(['--android', str(self.android), '--windows', str(self.windows),
                                    '--out', str(self.out), '--run-id', '1', '--source-sha', 'x', '--tag', 'v1.0.0'])
        self.assertEqual(code, 1)


if __name__ == '__main__':
    unittest.main()
