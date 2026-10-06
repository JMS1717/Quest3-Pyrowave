import json
import tempfile
import unittest
from pathlib import Path

from tools.quest3 import refresh_scaling as rs

DSI_LINE = ('10-05 21:37:50.275     0     0 I [drm:dsi_display_set_mode [msm_drm]] [msm-dsi-info]: '
            'mdp_transfer_time=0, hactive={w}, vactive={h}, fps={fps}, clk_rate=0')


class FakeQuest:
    """Applies the display properties only when they change while awake, like HorizonOS."""

    def __init__(self, serial='serial-1', awake=False, applies=True):
        self.props = {'debug.oculus.forceDisplayScaling': '', 'debug.oculus.refreshRate': '120',
                      'ro.product.model': 'Quest 3'}
        self.mode = (4128, 2208, 120)
        self.serial, self.is_awake, self.applies, self.log = serial, awake, applies, []

    def run(self, *args):
        if args[0] == 'get-serialno':
            return self.serial + '\n'
        if args[:2] == ('shell', 'getprop'):
            return self.props.get(args[2], '') + '\n'
        command = args[1]
        self.log.append(command)
        if command.startswith('setprop '):
            _, name, value = command.split(' ', 2)
            value = value.strip("'")
            changed = self.props.get(name) != value
            self.props[name] = value
            if changed and self.is_awake and self.applies:
                scaled = self.props['debug.oculus.forceDisplayScaling'] == '1'
                rate = int(self.props['debug.oculus.refreshRate'] or 120)
                self.mode = (3104, 1664, rate) if scaled else (4128, 2208, min(rate, 207))
        elif command == 'input keyevent KEYCODE_WAKEUP':
            self.is_awake = True
        elif 'dsi_display_set_mode' in command:
            w, h, fps = self.mode
            return DSI_LINE.format(w=w, h=h, fps=fps)
        elif 'mWakefulness' in command:
            return '  mWakefulness=' + ('Awake' if self.is_awake else 'Asleep')
        return ''

    def shell(self, command):
        return self.run('shell', command)

    def getprop(self, name):
        return self.props.get(name, '')

    def panel_mode(self):
        return rs.parse_panel_mode(self.shell('logcat | grep dsi_display_set_mode | tail -1'))

    def awake(self):
        return self.is_awake


class RefreshScalingTest(unittest.TestCase):
    def setUp(self):
        self._sleep, self._clock = rs.time.sleep, rs.time.monotonic
        now = [0.0]

        def advance(seconds):
            now[0] += seconds
        rs.time.sleep = advance
        rs.time.monotonic = lambda: now[0]
        self.state = Path(tempfile.mkdtemp()) / 'scaling.json'

    def tearDown(self):
        rs.time.sleep, rs.time.monotonic = self._sleep, self._clock

    def test_parses_last_dsi_mode(self):
        text = DSI_LINE.format(w=4128, h=2208, fps=120) + '\n' + DSI_LINE.format(w=3104, h=1664, fps=240)
        self.assertEqual(rs.parse_panel_mode(text), (3104, 1664, 240))
        self.assertIsNone(rs.parse_panel_mode('nothing'))

    def test_enable_wakes_before_changing_properties(self):
        quest = FakeQuest(awake=False)
        self.assertEqual(rs.enable(quest, self.state), (3104, 1664, 240))
        self.assertLess(quest.log.index('input keyevent KEYCODE_WAKEUP'),
                        next(i for i, c in enumerate(quest.log) if c.startswith('setprop')))
        self.assertEqual(quest.log[-1], 'am broadcast -a com.oculus.vrpowermanager.automation_disable')
        saved = json.loads(self.state.read_text())
        self.assertEqual(saved['properties'], {'debug.oculus.forceDisplayScaling': '', 'debug.oculus.refreshRate': '120'})

    def test_enable_rolls_back_when_the_panel_never_switches(self):
        quest = FakeQuest(applies=False)
        with self.assertRaises(RuntimeError):
            rs.enable(quest, self.state)
        self.assertEqual(rs.values(quest), {'debug.oculus.forceDisplayScaling': '', 'debug.oculus.refreshRate': '120'})

    def test_restore_reapplies_original_values_while_awake(self):
        quest = FakeQuest()
        rs.enable(quest, self.state)
        quest.is_awake = False
        # Properties already put back while asleep: the panel would stay at 240 without a change event.
        quest.props.update({'debug.oculus.forceDisplayScaling': '', 'debug.oculus.refreshRate': '120'})
        self.assertEqual(quest.mode, (3104, 1664, 240))
        self.assertEqual(rs.restore(quest, self.state), (4128, 2208, 120))
        self.assertEqual(rs.values(quest), {'debug.oculus.forceDisplayScaling': '', 'debug.oculus.refreshRate': '120'})

    def test_restore_refuses_another_device(self):
        quest = FakeQuest()
        rs.enable(quest, self.state)
        with self.assertRaises(ValueError):
            rs.restore(FakeQuest(serial='other'), self.state)

    def test_state_file_is_never_overwritten(self):
        quest = FakeQuest()
        rs.enable(quest, self.state)
        with self.assertRaises(FileExistsError):
            rs.enable(quest, self.state)


if __name__ == '__main__':
    unittest.main()
