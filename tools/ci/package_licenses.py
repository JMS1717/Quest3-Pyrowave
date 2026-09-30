"""Bundle upstream and dependency notices with distributable builds."""
import argparse
import os
import shutil
from pathlib import Path


def collect(tree, destination):
    for root, dirs, files in os.walk(tree):
        dirs[:] = [d for d in dirs if not d.startswith(('.git', 'build', 'target'))]
        for name in files:
            upper = name.upper()
            if upper.startswith(('LICENSE', 'LICENCE', 'COPYING', 'NOTICE', 'COPYRIGHT')):
                source = Path(root) / name
                target = destination / source.relative_to(tree)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('sources', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    args.destination.mkdir(parents=True, exist_ok=True)
    for name in ('LICENSE', 'NOTICE', 'sources.lock.json'):
        shutil.copyfile(repo / name, args.destination / name)
    for name in ('ALVR-20.13.0', 'pyrowave'):
        collect(args.sources / name, args.destination / name)
    # Include notices from the populated Rust dependency cache as a conservative
    # superset; build-tool dependencies retain their attribution too.
    cargo = Path(os.environ.get('CARGO_HOME', str(Path.home() / '.cargo')))
    for name in ('registry/src', 'git/checkouts'):
        collect(cargo / name, args.destination / 'rust-dependencies' / name)
    (args.destination / 'README.txt').write_text(
        'Quest3-Pyrowave notices: LICENSE and NOTICE cover this integration.\n'
        'Upstream components retain the notices in their corresponding directories.\n'
        'sources.lock.json identifies the pinned upstream revisions.\n'
        'Rust notices include a superset of build-time and runtime dependencies.\n',
        encoding='utf-8')


if __name__ == '__main__':
    main()
