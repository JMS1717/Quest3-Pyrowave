"""Assemble a release from one successful main CI run's artifacts (no rebuild).

Inputs are the downloaded `Quest3-Pyrowave-Android` and `Quest3-Pyrowave-Windows` artifact
directories. The script checks each artifact against its own SHA256SUMS, checks the APK version
and (optionally) that the APK signing certificate matches a previous release, and writes the
player-facing assets:

  Quest3-Pyrowave-dev.apk, Quest3-Pyrowave-Windows.zip, Quest3-Pyrowave-Android-LICENSES.zip,
  APK-CERTIFICATE.txt, BUILD-METADATA.json, SHA256SUMS.txt

Test executables and native probe binaries stay in the Actions artifacts. Signing material is
never an input and any keystore-like file in the inputs is an error.

Usage: release_assets.py --android DIR --windows DIR --out DIR --run-id N --source-sha SHA
                         --tag TAG [--reference-cert FILE] [--expect-version .62]
"""
import argparse
import hashlib
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path

FORBIDDEN = re.compile(r'\.(p12|pfx|jks|keystore|pem|key)$', re.IGNORECASE)
DIGEST = re.compile(r'certificate SHA-256 digest:\s*([0-9a-f]{64})', re.IGNORECASE)


class ReleaseError(Exception):
    pass


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def read_sums(path):
    raw = Path(path).read_bytes()
    text = raw.decode('utf-16') if raw[:2] in (b'\xff\xfe', b'\xfe\xff') else raw.decode('utf-8-sig')
    sums = {}
    for line in text.splitlines():
        parts = line.strip().split()
        if len(parts) == 2:
            sums[parts[1].lstrip('*')] = parts[0].lower()
    return sums


def verify_artifact(directory, names):
    directory = Path(directory)
    for path in directory.rglob('*'):
        if FORBIDDEN.search(path.name):
            raise ReleaseError(f'signing material must not be released: {path.name}')
    sums_path = directory / 'SHA256SUMS.txt'
    if not sums_path.is_file():
        raise ReleaseError(f'{directory}: missing SHA256SUMS.txt')
    sums = read_sums(sums_path)
    for name in names:
        path = directory / name
        if not path.is_file():
            raise ReleaseError(f'{directory}: missing {name}')
        if sums.get(name) != sha256(path):
            raise ReleaseError(f'{name}: hash does not match the run\'s SHA256SUMS.txt')


def cert_digests(text):
    return sorted(set(m.lower() for m in DIGEST.findall(text)))


def apk_version(apk):
    """The client version suffix embedded in the APK's native libraries, e.g. '.62'."""
    with zipfile.ZipFile(apk) as z:
        for info in z.infolist():
            if info.filename.startswith('lib/') and info.filename.endswith('.so'):
                m = re.search(rb'20\.13\.0-quest3\.pyro\.(\d+)', z.read(info))
                if m:
                    return '.' + m.group(1).decode()
    return None


def build(android, windows, out, run_id, source_sha, tag, reference_cert=None, expect_version=None):
    verify_artifact(android, ['Quest3-Pyrowave-dev.apk', 'LICENSES.zip', 'APK-CERTIFICATE.txt'])
    verify_artifact(windows, ['Quest3-Pyrowave-Windows.zip'])
    android, windows, out = Path(android), Path(windows), Path(out)
    if not re.fullmatch(r'[0-9a-f]{40}', source_sha):
        raise ReleaseError('source sha must be a full commit hash')
    if not re.fullmatch(r'v\d+\.\d+\.\d+(-[0-9A-Za-z.]+)?', tag):
        raise ReleaseError(f'unexpected tag {tag!r}')
    cert_text = (android / 'APK-CERTIFICATE.txt').read_text(encoding='utf-8')
    digests = cert_digests(cert_text)
    if not digests:
        raise ReleaseError('APK-CERTIFICATE.txt has no SHA-256 certificate digest')
    if reference_cert is not None:
        reference = cert_digests(Path(reference_cert).read_text(encoding='utf-8'))
        if digests != reference:
            raise ReleaseError('APK signing certificate differs from the reference release; '
                               'a temporary-key build cannot replace a stable install')
    version = apk_version(android / 'Quest3-Pyrowave-dev.apk')
    if expect_version is not None and version != expect_version:
        raise ReleaseError(f'APK reports version {version}, expected {expect_version}')

    out.mkdir(parents=True, exist_ok=True)
    assets = {
        'Quest3-Pyrowave-dev.apk': android / 'Quest3-Pyrowave-dev.apk',
        'Quest3-Pyrowave-Windows.zip': windows / 'Quest3-Pyrowave-Windows.zip',
        'Quest3-Pyrowave-Android-LICENSES.zip': android / 'LICENSES.zip',
        'APK-CERTIFICATE.txt': android / 'APK-CERTIFICATE.txt',
    }
    for name, src in assets.items():
        shutil.copyfile(src, out / name)
    metadata = {
        'tag': tag,
        'source_commit': source_sha,
        'actions_run_id': int(run_id),
        'client_version': version,
        'apk_certificate_sha256': digests,
        'certificate_matches_reference': reference_cert is not None,
        'assets': {name: sha256(out / name) for name in assets},
        'limitations': 'Built and tested in CI only. Publishing does not add hardware testing: '
                       'sustained play, optical latency and in-headset quality are separate gates.',
    }
    (out / 'BUILD-METADATA.json').write_text(json.dumps(metadata, indent=1) + '\n', encoding='utf-8')
    names = sorted(list(assets) + ['BUILD-METADATA.json'])
    (out / 'SHA256SUMS.txt').write_text(''.join(f'{sha256(out / n)}  {n}\n' for n in names), encoding='utf-8')
    return metadata


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--android', required=True)
    ap.add_argument('--windows', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--run-id', required=True)
    ap.add_argument('--source-sha', required=True)
    ap.add_argument('--tag', required=True)
    ap.add_argument('--reference-cert')
    ap.add_argument('--expect-version')
    args = ap.parse_args(argv)
    try:
        meta = build(args.android, args.windows, args.out, args.run_id, args.source_sha, args.tag,
                     args.reference_cert, args.expect_version)
    except ReleaseError as e:
        print(f'release check failed: {e}', file=sys.stderr)
        return 1
    print(json.dumps(meta, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
