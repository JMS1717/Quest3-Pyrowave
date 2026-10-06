import json
import tempfile
import unittest
from pathlib import Path

from tools.quest3 import gpu_level


class FakeDevice:
    def __init__(self, serial='serial-1', value='', accept=True):
        self.props = {gpu_level.PROPERTY: value}
        self.serial, self.accept = serial, accept

    def run(self, *args):
        if args[0] == 'get-serialno':
            return self.serial + '\n'
        _, name, value = args[1].split(' ', 2)
        if self.accept:
            self.props[name] = value.strip("'")
        return ''

    def getprop(self, name):
        return self.props.get(name, '')


class GpuLevelTest(unittest.TestCase):
    def setUp(self):
        self.state = Path(tempfile.mkdtemp()) / 'gpu.json'

    def test_enable_saves_original_and_restore_puts_it_back(self):
        device = FakeDevice()
        self.assertEqual(gpu_level.enable(device, self.state, 7), '7')
        self.assertEqual(json.loads(self.state.read_text())['value'], '')
        self.assertEqual(gpu_level.restore(device, self.state), '')
        self.assertEqual(device.getprop(gpu_level.PROPERTY), '')

    def test_rejects_out_of_range_levels(self):
        with self.assertRaises(ValueError):
            gpu_level.enable(FakeDevice(), self.state, 9)

    def test_rejected_write_is_reported(self):
        with self.assertRaises(RuntimeError):
            gpu_level.enable(FakeDevice(accept=False), self.state, 7)

    def test_restore_refuses_another_device(self):
        gpu_level.enable(FakeDevice(), self.state, 7)
        with self.assertRaises(ValueError):
            gpu_level.restore(FakeDevice(serial='other'), self.state)

    def test_state_is_never_overwritten(self):
        gpu_level.enable(FakeDevice(), self.state, 7)
        with self.assertRaises(FileExistsError):
            gpu_level.enable(FakeDevice(), self.state, 7)


if __name__ == '__main__':
    unittest.main()
