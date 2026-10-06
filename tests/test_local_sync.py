"""The local fast build is only incremental, and only trustworthy, if its content sync touches
exactly the files whose bytes changed and never loses work left in the build tree."""
import os
import tempfile
import unittest
from pathlib import Path

from tools.local import fast_build as fb

LOCK = 'ALVR-20.13.0/Cargo.lock'


def write(root, rel, text):
    path = Path(root) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, newline='\n')
    return path


def age(path, seconds=1000):
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns - seconds * 1_000_000_000))
    return path.stat().st_mtime_ns


class LocalSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.stage, self.tree, self.backups = base / 'stage', base / 'tree', base / 'backups'
        recipe = {'ALVR-20.13.0/alvr/a.rs': 'a1', 'ALVR-20.13.0/alvr/b.rs': 'b1',
                  'pyrowave/pyrowave.h': 'h1', LOCK: 'lock1'}
        for rel, text in recipe.items():
            write(self.stage, rel, text)
            write(self.tree, rel, text)
        fb.sync_tree(self.stage, self.tree, self.backups)
        self.old_b = age(self.tree / 'ALVR-20.13.0/alvr/b.rs')

    def tearDown(self):
        self.tmp.cleanup()

    def sync(self):
        return fb.sync_tree(self.stage, self.tree, self.backups)

    def test_only_changed_bytes_are_rewritten(self):
        write(self.stage, 'ALVR-20.13.0/alvr/a.rs', 'a2')
        write(self.stage, 'ALVR-20.13.0/alvr/b.rs', 'b1')  # same bytes, new stage timestamp
        report = self.sync()
        self.assertEqual(report['changed'], ['ALVR-20.13.0/alvr/a.rs'])
        self.assertEqual((self.tree / 'ALVR-20.13.0/alvr/a.rs').read_text(), 'a2')
        self.assertEqual((self.tree / 'ALVR-20.13.0/alvr/b.rs').stat().st_mtime_ns, self.old_b)

    def test_added_and_removed_recipe_files_follow_the_stage(self):
        write(self.stage, 'ALVR-20.13.0/alvr/new.rs', 'n')
        (self.stage / 'pyrowave/pyrowave.h').unlink()
        report = self.sync()
        self.assertEqual(report['added'], ['ALVR-20.13.0/alvr/new.rs'])
        self.assertEqual(report['removed'], ['pyrowave/pyrowave.h'])
        self.assertFalse((self.tree / 'pyrowave/pyrowave.h').exists())
        self.assertEqual(report['backed_up'], [])

    def test_build_outputs_git_metadata_and_submodules_are_never_touched(self):
        target = write(self.tree, 'ALVR-20.13.0/target/release/x.rlib', 'out')
        granite = write(self.tree, 'pyrowave/Granite/vulkan/device.cpp', 'granite')
        write(self.stage, 'ALVR-20.13.0/.git/HEAD', 'stage-head')
        write(self.stage, 'ALVR-20.13.0/openvr/headers/openvr.h', 'stage-copy')
        report = self.sync()
        self.assertEqual(report, {'changed': [], 'added': [], 'removed': [], 'backed_up': []})
        self.assertEqual(target.read_text(), 'out')
        self.assertEqual(granite.read_text(), 'granite')
        self.assertFalse((self.tree / 'ALVR-20.13.0/.git').exists())

    def test_a_hand_edit_in_the_tree_is_backed_up_before_it_is_replaced(self):
        write(self.tree, 'ALVR-20.13.0/alvr/a.rs', 'edited in the build tree')
        report = self.sync()
        self.assertEqual(report['backed_up'], ['ALVR-20.13.0/alvr/a.rs'])
        self.assertEqual((self.backups / 'ALVR-20.13.0/alvr/a.rs').read_text(), 'edited in the build tree')
        self.assertEqual((self.tree / 'ALVR-20.13.0/alvr/a.rs').read_text(), 'a1')

    def test_a_hand_edit_to_a_removed_file_is_backed_up_too(self):
        write(self.tree, 'pyrowave/pyrowave.h', 'edited')
        (self.stage / 'pyrowave/pyrowave.h').unlink()
        report = self.sync()
        self.assertEqual(report['backed_up'], ['pyrowave/pyrowave.h'])
        self.assertEqual((self.backups / 'pyrowave/pyrowave.h').read_text(), 'edited')

    def test_build_rewritten_files_keep_the_build_version_until_the_recipe_changes(self):
        rewritten = write(self.tree, LOCK, 'lock rewritten by cargo')
        stamp = rewritten.stat().st_mtime_ns
        report = self.sync()
        self.assertEqual(report['changed'], [])
        self.assertEqual(rewritten.read_text(), 'lock rewritten by cargo')
        self.assertEqual(rewritten.stat().st_mtime_ns, stamp)
        write(self.stage, LOCK, 'lock2')
        report = self.sync()
        self.assertEqual(report['changed'], [LOCK])
        self.assertEqual(report['backed_up'], [])
        self.assertEqual(rewritten.read_text(), 'lock2')

    def test_a_second_sync_without_changes_reads_only_the_stage(self):
        report = self.sync()
        self.assertEqual(report, {'changed': [], 'added': [], 'removed': [], 'backed_up': []})


class PackageListTests(unittest.TestCase):
    def test_android_outputs_come_from_this_branch_ci_copy_step(self):
        outputs = fb.ci_android_outputs()
        self.assertIn('tools/pyroclient/libpyroclient.so', outputs)
        self.assertTrue(all(o.startswith('tools/') for o in outputs), outputs)
        self.assertFalse(any('$' in o for o in outputs), outputs)


if __name__ == '__main__':
    unittest.main()
