"""
test_network_control.py — Wi-Fi and Bluetooth wrapper (spi_bridge/network_control.py).

No real network or Bluetooth call: `subprocess.run` is mocked throughout, so this suite runs the same on the Mac,
in CI, or on the Radxa. It checks that every command is built as an argument list (never a shell string), that a
missing tool / timeout / other OS error never raises, and that the nmcli/bluetoothctl output shapes seen on the
real Radxa (2026-09-22) are parsed correctly.

    python3 -m pytest spi_bridge/tests/test_network_control.py -v
"""

import os
import subprocess
import sys
import unittest
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
import network_control as nc  # noqa: E402


def _proc(returncode=0, stdout='', stderr=''):
    class P:
        pass
    p = P()
    p.returncode, p.stdout, p.stderr = returncode, stdout, stderr
    return p


class RunNeverRaises(unittest.TestCase):
    """The one thing every caller depends on: `_run` reports failure instead of raising."""

    def test_missing_tool(self):
        with patch('network_control.subprocess.run', side_effect=FileNotFoundError()):
            ok, out, err = nc._run(['nmcli'], 1)
        self.assertFalse(ok)
        self.assertEqual(err, 'not installed')

    def test_timeout(self):
        with patch('network_control.subprocess.run', side_effect=subprocess.TimeoutExpired('nmcli', 1)):
            ok, out, err = nc._run(['nmcli'], 1)
        self.assertFalse(ok)
        self.assertEqual(err, 'timed out')

    def test_other_os_error(self):
        with patch('network_control.subprocess.run', side_effect=OSError('boom')):
            ok, out, err = nc._run(['nmcli'], 1)
        self.assertFalse(ok)
        self.assertIn('boom', err)

    def test_success_passthrough(self):
        with patch('network_control.subprocess.run', return_value=_proc(0, 'hi', '')) as m:
            ok, out, err = nc._run(['nmcli', 'x'], 1)
        self.assertTrue(ok)
        self.assertEqual(out, 'hi')
        # never a shell string
        m.assert_called_once()
        args = m.call_args[0][0]
        self.assertIsInstance(args, list)


class ArgumentListOnly(unittest.TestCase):
    """A password or SSID must always travel as one argument in a list, never folded into a shell string."""

    def test_wifi_connect_password_is_its_own_argument(self):
        with patch('network_control.subprocess.run', return_value=_proc(0)) as m:
            nc.wifi_connect('Some SSID; rm -rf /', 'p@ss "word" $(evil)')
        args = m.call_args[0][0]
        self.assertIsInstance(args, list)
        self.assertIn('Some SSID; rm -rf /', args)
        self.assertIn('p@ss "word" $(evil)', args)
        # the dangerous characters are literal argument text, not shell syntax — nothing here builds a string
        for a in args:
            self.assertNotIsInstance(a, bytes)

    def test_bt_pair_connect_mac_is_its_own_argument(self):
        with patch('network_control.subprocess.run', return_value=_proc(0, 'Paired: yes\nConnected: yes')) as m:
            nc.bt_pair_connect('AA:BB:CC:DD:EE:FF; reboot')
        for call in m.call_args_list:
            self.assertIsInstance(call[0][0], list)


class WifiStatusTest(unittest.TestCase):
    def test_connected(self):
        out = 'wlan0:wifi:connected:Maple\nwlan1:wifi:disconnected:\neth0:ethernet:unavailable:\nlo:loopback:unmanaged:\n'
        with patch('network_control.subprocess.run', return_value=_proc(0, out)):
            status = nc.wifi_status()
        self.assertTrue(status.connected)
        self.assertEqual(status.ssid, 'Maple')

    def test_not_connected(self):
        out = 'wlan0:wifi:disconnected:\n'
        with patch('network_control.subprocess.run', return_value=_proc(0, out)):
            status = nc.wifi_status()
        self.assertFalse(status.connected)

    def test_nmcli_missing(self):
        with patch('network_control.subprocess.run', side_effect=FileNotFoundError()):
            status = nc.wifi_status()
        self.assertFalse(status.connected)


class WifiScanTest(unittest.TestCase):
    SAMPLE = (
        '*:Maple:89:WPA2\n'
        ':Maple:55:WPA2\n'          # same SSID, weaker access point — folded to the strongest
        ':a1b2c3:100:WPA3\n'
        ':Willow_Street_5G:39:WPA2\n'
        '::74::--\n'                # a row with an empty SSID (hidden network) — skipped
    )

    def test_folds_duplicate_ssids_to_strongest_and_sorts(self):
        with patch('network_control.subprocess.run', return_value=_proc(0, self.SAMPLE)):
            networks = nc.wifi_scan()
        by_ssid = {n.ssid: n for n in networks}
        self.assertEqual(by_ssid['Maple'].signal, 89)
        self.assertEqual([n.ssid for n in networks], ['a1b2c3', 'Maple', 'Willow_Street_5G'])

    def test_in_use_and_security_flags(self):
        with patch('network_control.subprocess.run', return_value=_proc(0, self.SAMPLE)):
            networks = nc.wifi_scan()
        by_ssid = {n.ssid: n for n in networks}
        self.assertTrue(by_ssid['Maple'].in_use)
        self.assertTrue(by_ssid['Maple'].secured)
        self.assertFalse(by_ssid['Willow_Street_5G'].in_use)

    def test_connected_to_a_weaker_access_point_still_marks_the_network_connected(self):
        """Found on the real Radxa: joined to one access point while another of the same network was stronger."""
        for order in (':Maple:100:WPA2\n*:Maple:40:WPA2\n', '*:Maple:40:WPA2\n:Maple:100:WPA2\n'):
            with patch('network_control.subprocess.run', return_value=_proc(0, order)):
                networks = nc.wifi_scan()
            self.assertEqual([(n.ssid, n.signal, n.in_use) for n in networks], [('Maple', 100, True)], order)

    def test_open_network_not_secured(self):
        out = ':OpenCafe:60:--\n'
        with patch('network_control.subprocess.run', return_value=_proc(0, out)):
            networks = nc.wifi_scan()
        self.assertFalse(networks[0].secured)

    def test_scan_failure_returns_empty(self):
        with patch('network_control.subprocess.run', return_value=_proc(1, '', 'device not ready')):
            self.assertEqual(nc.wifi_scan(), [])


class WifiConnectTest(unittest.TestCase):
    def test_ok(self):
        with patch('network_control.subprocess.run', return_value=_proc(0)):
            result = nc.wifi_connect('Maple', 'hunter2')
        self.assertTrue(result.ok)

    def test_wrong_password(self):
        err = "Error: Connection activation failed: Secrets were required, but not provided.\n"
        with patch('network_control.subprocess.run', return_value=_proc(1, '', err)):
            result = nc.wifi_connect('Maple', 'wrong')
        self.assertFalse(result.ok)
        self.assertEqual(result.detail, 'wrong password')

    def test_network_not_found(self):
        err = "Error: No network with SSID 'Ghost' found.\n"
        with patch('network_control.subprocess.run', return_value=_proc(10, '', err)):
            result = nc.wifi_connect('Ghost')
        self.assertEqual(result.detail, 'network not found')

    def test_generic_failure(self):
        with patch('network_control.subprocess.run', return_value=_proc(1, '', 'some other nmcli error')):
            result = nc.wifi_connect('Maple', 'x')
        self.assertFalse(result.ok)
        self.assertEqual(result.detail, 'could not connect')

    def test_open_network_no_password_argument(self):
        with patch('network_control.subprocess.run', return_value=_proc(0)) as m:
            nc.wifi_connect('OpenCafe')
        self.assertNotIn('password', m.call_args[0][0])


class BtStatusTest(unittest.TestCase):
    def test_powered_and_connected_device(self):
        show = 'Powered: yes\nDiscoverable: no\n'
        paired = 'Device 11:22:33:44:55:66 ZitaoTech_q10\n'
        info = 'Paired: yes\nConnected: yes\n'

        def run(args, **kwargs):
            if args[:2] == ['bluetoothctl', 'show']:
                return _proc(0, show)
            if args[:2] == ['bluetoothctl', 'paired-devices']:
                return _proc(0, paired)
            if args[:2] == ['bluetoothctl', 'info']:
                return _proc(0, info)
            self.fail('unexpected command %r' % (args,))

        with patch('network_control.subprocess.run', side_effect=run):
            status = nc.bt_status()
        self.assertTrue(status.powered)
        self.assertEqual(status.connected_names, ['ZitaoTech_q10'])

    def test_powered_off(self):
        with patch('network_control.subprocess.run', return_value=_proc(0, 'Powered: no\n')):
            status = nc.bt_status()
        self.assertFalse(status.powered)
        self.assertEqual(status.connected_names, [])

    def test_bluetoothctl_missing(self):
        with patch('network_control.subprocess.run', side_effect=FileNotFoundError()):
            status = nc.bt_status()
        self.assertFalse(status.powered)
        self.assertEqual(status.connected_names, [])


class BtScanTest(unittest.TestCase):
    def test_paired_device_kept_even_if_scan_misses_it(self):
        paired = 'Device 11:22:33:44:55:66 ZitaoTech_q10\n'
        seen = 'Device 66:55:44:33:22:11 66-55-44-33-22-11\n'   # the keyboard did not show up in this scan
        info = 'Paired: yes\nConnected: no\n'

        def run(args, **kwargs):
            if args[:2] == ['bluetoothctl', 'paired-devices']:
                return _proc(0, paired)
            if 'scan' in args:
                return _proc(0, '')
            if args[:2] == ['bluetoothctl', 'devices']:
                return _proc(0, seen)
            if args[:2] == ['bluetoothctl', 'info']:
                return _proc(0, info)
            self.fail('unexpected command %r' % (args,))

        with patch('network_control.subprocess.run', side_effect=run):
            devices = nc.bt_scan(seconds=1)
        macs = {d.mac for d in devices}
        self.assertIn('11:22:33:44:55:66', macs)
        self.assertIn('66:55:44:33:22:11', macs)

    def test_paired_devices_sort_first(self):
        paired = 'Device AA:AA:AA:AA:AA:AA Zebra Keyboard\n'
        seen = 'Device AA:AA:AA:AA:AA:AA Zebra Keyboard\nDevice BB:BB:BB:BB:BB:BB Apple Headphones\n'

        def run(args, **kwargs):
            if args[:2] == ['bluetoothctl', 'paired-devices']:
                return _proc(0, paired)
            if 'scan' in args:
                return _proc(0, '')
            if args[:2] == ['bluetoothctl', 'devices']:
                return _proc(0, seen)
            if args[:2] == ['bluetoothctl', 'info']:
                return _proc(0, 'Paired: yes\nConnected: no\n')
            self.fail('unexpected command %r' % (args,))

        with patch('network_control.subprocess.run', side_effect=run):
            devices = nc.bt_scan(seconds=1)
        self.assertTrue(devices[0].paired)
        self.assertEqual(devices[0].name, 'Zebra Keyboard')

    def test_scan_uses_a_self_stopping_timeout(self):
        calls = []

        def run(args, **kwargs):
            calls.append(args)
            return _proc(0, '')

        with patch('network_control.subprocess.run', side_effect=run):
            nc.bt_scan(seconds=7)
        scan_call = next(c for c in calls if 'scan' in c)
        self.assertEqual(scan_call, ['bluetoothctl', '--timeout', '7', 'scan', 'on'])

    def test_bluetoothctl_missing_returns_empty(self):
        with patch('network_control.subprocess.run', side_effect=FileNotFoundError()):
            self.assertEqual(nc.bt_scan(seconds=1), [])


class BtPairConnectTest(unittest.TestCase):
    def test_already_paired_just_connects(self):
        calls = []

        def run(args, **kwargs):
            calls.append(args)
            if args[:2] == ['bluetoothctl', 'info']:
                return _proc(0, 'Paired: yes\n')
            if args[:2] == ['bluetoothctl', 'connect']:
                return _proc(0, 'Connection successful\n')
            self.fail('unexpected command %r' % (args,))

        with patch('network_control.subprocess.run', side_effect=run):
            result = nc.bt_pair_connect('AA:BB:CC:DD:EE:FF')
        self.assertTrue(result.ok)
        self.assertFalse(any(c[:2] == ['bluetoothctl', 'pair'] for c in calls))   # never re-pairs

    def test_not_paired_pairs_then_connects(self):
        calls = []

        def run(args, **kwargs):
            calls.append(args)
            if args[:2] == ['bluetoothctl', 'info']:
                return _proc(0, 'Paired: no\n')
            if args[:2] == ['bluetoothctl', 'pair']:
                return _proc(0, 'Pairing successful\n')
            if args[:2] == ['bluetoothctl', 'connect']:
                return _proc(0, 'Connection successful\n')
            self.fail('unexpected command %r' % (args,))

        with patch('network_control.subprocess.run', side_effect=run):
            result = nc.bt_pair_connect('AA:BB:CC:DD:EE:FF')
        self.assertTrue(result.ok)
        self.assertTrue(any(c[:2] == ['bluetoothctl', 'pair'] for c in calls))

    def test_pairing_refused(self):
        def run(args, **kwargs):
            if args[:2] == ['bluetoothctl', 'info']:
                return _proc(0, 'Paired: no\n')
            if args[:2] == ['bluetoothctl', 'pair']:
                return _proc(1, '', 'Failed to pair: org.bluez.Error.AuthenticationFailed\n')
            self.fail('unexpected command %r' % (args,))

        with patch('network_control.subprocess.run', side_effect=run):
            result = nc.bt_pair_connect('AA:BB:CC:DD:EE:FF')
        self.assertFalse(result.ok)
        self.assertEqual(result.detail, 'pairing was refused')

    def test_pairing_timeout(self):
        def run(args, **kwargs):
            if args[:2] == ['bluetoothctl', 'info']:
                return _proc(0, 'Paired: no\n')
            if args[:2] == ['bluetoothctl', 'pair']:
                return _proc(1, '', 'Failed to pair: org.bluez.Error.AuthenticationTimeout\n')
            self.fail('unexpected command %r' % (args,))

        with patch('network_control.subprocess.run', side_effect=run):
            result = nc.bt_pair_connect('AA:BB:CC:DD:EE:FF')
        self.assertEqual(result.detail, 'timed out')

    def test_connect_fails_after_successful_pair(self):
        def run(args, **kwargs):
            if args[:2] == ['bluetoothctl', 'info']:
                return _proc(0, 'Paired: no\n')
            if args[:2] == ['bluetoothctl', 'pair']:
                return _proc(0, 'Pairing successful\n')
            if args[:2] == ['bluetoothctl', 'connect']:
                return _proc(1, '', 'Failed to connect\n')
            self.fail('unexpected command %r' % (args,))

        with patch('network_control.subprocess.run', side_effect=run):
            result = nc.bt_pair_connect('AA:BB:CC:DD:EE:FF')
        self.assertFalse(result.ok)
        self.assertEqual(result.detail, 'could not connect')

    def test_never_touches_another_device(self):
        """A call for one address must never mention any other address (e.g. the keyboard's)."""
        keyboard_mac = '11:22:33:44:55:66'

        def run(args, **kwargs):
            self.assertNotIn(keyboard_mac, args)
            if args[:2] == ['bluetoothctl', 'info']:
                return _proc(0, 'Paired: yes\n')
            if args[:2] == ['bluetoothctl', 'connect']:
                return _proc(0, 'Connection successful\n')
            self.fail('unexpected command %r' % (args,))

        with patch('network_control.subprocess.run', side_effect=run):
            nc.bt_pair_connect('AA:BB:CC:DD:EE:FF')



class Switches(unittest.TestCase):
    def test_wifi_enabled_reads_nmcli_radio(self):
        for out, want in (('enabled\n', True), ('disabled\n', False)):
            with patch('network_control.subprocess.run', return_value=_proc(0, out)) as m:
                self.assertEqual(nc.wifi_enabled(), want)
            self.assertEqual(m.call_args[0][0], ['nmcli', 'radio', 'wifi'])

    def test_the_wifi_and_bluetooth_switches(self):
        with patch('network_control.subprocess.run', return_value=_proc(0)) as m:
            self.assertTrue(nc.wifi_set_enabled(False).ok)
            self.assertEqual(m.call_args[0][0], ['nmcli', 'radio', 'wifi', 'off'])
            self.assertTrue(nc.bt_set_powered(True).ok)
            self.assertEqual(m.call_args[0][0], ['bluetoothctl', 'power', 'on'])
        with patch('network_control.subprocess.run', return_value=_proc(1)):
            self.assertEqual(nc.wifi_set_enabled(True).detail, 'could not switch Wi-Fi on')


class ForgetNetwork(unittest.TestCase):

    def test_deletes_every_profile_for_that_network_and_nothing_else(self):
        profiles = 'Maple:802-11-wireless\nMaple 1:802-11-wireless\nWired:802-3-ethernet\nBirch:802-11-wireless\n'
        calls = []
        def fake(args, **kw):
            calls.append(args)
            if args[:4] == ['nmcli', '-t', '-f', 'NAME,TYPE']:
                return _proc(0, profiles)
            if args[:3] == ['nmcli', '-g', '802-11-wireless.ssid']:
                return _proc(0, {'Maple': 'Maple', 'Maple 1': 'Maple', 'Birch': 'Birch'}[args[-1]] + '\n')
            return _proc(0)
        with patch('network_control.subprocess.run', side_effect=fake):
            self.assertTrue(nc.wifi_forget('Maple').ok)
        deleted = [a[-1] for a in calls if a[:3] == ['nmcli', 'connection', 'delete']]
        self.assertEqual(deleted, ['Maple', 'Maple 1'])

    def test_a_colon_in_a_name_is_unescaped(self):
        calls = []
        def fake(args, **kw):
            calls.append(args)
            if args[:4] == ['nmcli', '-t', '-f', 'NAME,TYPE']:
                return _proc(0, 'Cafe\\:Guest:802-11-wireless\n')
            if args[:3] == ['nmcli', '-g', '802-11-wireless.ssid']:
                return _proc(0, 'Cafe\\:Guest\n')
            return _proc(0)
        with patch('network_control.subprocess.run', side_effect=fake):
            self.assertTrue(nc.wifi_forget('Cafe:Guest').ok)
        self.assertIn(['nmcli', 'connection', 'delete', 'id', 'Cafe:Guest'], calls)

    def test_an_unknown_network_or_a_failed_delete_says_so(self):
        with patch('network_control.subprocess.run', return_value=_proc(0, '')):
            self.assertEqual(nc.wifi_forget('Nowhere').detail, 'not a saved network')
        def fake(args, **kw):
            if args[:4] == ['nmcli', '-t', '-f', 'NAME,TYPE']:
                return _proc(0, 'Maple:802-11-wireless\n')
            if args[:3] == ['nmcli', '-g', '802-11-wireless.ssid']:
                return _proc(0, 'Maple\n')
            return _proc(1)
        with patch('network_control.subprocess.run', side_effect=fake):
            self.assertEqual(nc.wifi_forget('Maple').detail, 'could not forget it')


class KnownDevices(unittest.TestCase):
    def test_paired_devices_a_to_z_with_connection_and_input_flags_and_no_scan(self):
        infos = {'11:22:33:44:55:66': 'Paired: yes\nConnected: yes\nIcon: input-keyboard\n',
                 'AA:AA:AA:AA:AA:AA': 'Paired: yes\nConnected: no\nIcon: audio-headphones\n'}
        calls = []
        def fake(args, **kw):
            calls.append(args)
            if args == ['bluetoothctl', 'paired-devices']:
                return _proc(0, 'Device 11:22:33:44:55:66 ZitaoTech_q10\nDevice AA:AA:AA:AA:AA:AA Headphones\n')
            if args[:2] == ['bluetoothctl', 'info']:
                return _proc(0, infos[args[2]])
            return _proc(1)
        with patch('network_control.subprocess.run', side_effect=fake):
            devices = nc.bt_known()
        self.assertEqual([(d.name, d.connected, d.is_input) for d in devices],
                         [('Headphones', False, False), ('ZitaoTech_q10', True, True)])
        self.assertFalse(any('scan' in a for a in calls))

    def test_forget_removes_the_device(self):
        with patch('network_control.subprocess.run', return_value=_proc(0)) as m:
            self.assertTrue(nc.bt_forget('AA:AA:AA:AA:AA:AA').ok)
        self.assertEqual(m.call_args[0][0], ['bluetoothctl', 'remove', 'AA:AA:AA:AA:AA:AA'])


if __name__ == '__main__':
    unittest.main()
