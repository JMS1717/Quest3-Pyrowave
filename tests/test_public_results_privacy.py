"""Public benchmark evidence must not contain serialized owner sessions."""
import json
from pathlib import Path
import unittest

RESULTS = Path(__file__).resolve().parents[1] / 'results'
PRIVATE_KEYS = {'effective_video', 'session_settings', 'client_connections',
                'openvr_config', 'device_serial', 'server_ip', 'private_key'}


def private_fields(value, path='$'):
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f'{path}.{key}'
            if key.lower() in PRIVATE_KEYS:
                yield child_path
            yield from private_fields(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from private_fields(child, f'{path}[{index}]')


class PublicResultsPrivacyTests(unittest.TestCase):
    def test_public_results_exclude_sessions_and_private_identifiers(self):
        files = sorted(RESULTS.glob('*.json'))
        self.assertTrue(files, 'No public evidence was checked')
        for file in files:
            with self.subTest(file=file.name):
                data = json.loads(file.read_text(encoding='utf-8'))
                self.assertEqual(list(private_fields(data)), [])

    def test_guard_checks_nested_sessions_and_identifier_fields(self):
        data = {'blocks': [{'session_settings': {}}, {'device_serial': 'fixture'}]}
        self.assertEqual(list(private_fields(data)),
                         ['$.blocks[0].session_settings', '$.blocks[1].device_serial'])


if __name__ == '__main__':
    unittest.main()
