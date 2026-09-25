"""
test_modem.py — sending/receiving texts over a real cellular modem (spi_bridge/modem.py).

No real hardware and no pyserial required: SerialModem's AT dialogue is checked against a fake serial
port (FakeSerial, below) injected in place of pyserial, the same way other hardware modules are faked
elsewhere in this suite (spidev, gpiod, evdev, ...). SimModem needs nothing to test at all.

    python3 -m pytest spi_bridge/tests/test_modem.py -v
"""

import os
import re
import sys
import unittest
from unittest.mock import MagicMock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
import modem as m  # noqa: E402


class FakeSerial:
    """Stands in for pyserial's Serial: a byte pipe the test controls from both ends. `feed()` queues
    bytes as if the modem sent them; `written` records every byte SerialModem wrote, in order."""

    def __init__(self, port, baud, timeout=None):
        self.port, self.baud, self.timeout = port, baud, timeout
        self.written = bytearray()
        self._to_read = bytearray()

    def write(self, data):
        self.written += data

    def feed(self, s):
        self._to_read += s.encode('ascii') if isinstance(s, str) else s

    @property
    def in_waiting(self):
        return len(self._to_read)

    def read(self, n=1):
        n = max(1, n)
        chunk = bytes(self._to_read[:n])
        self._to_read = self._to_read[len(chunk):]
        return chunk


def _install_fake_serial():
    """Injects a fake 'serial' module so SerialModem's lazy `import serial` resolves to FakeSerial,
    and returns the FakeSerial instance it will construct."""
    fake_module = MagicMock()
    holder = {}

    def make(port, baud, timeout=None):
        holder['instance'] = FakeSerial(port, baud, timeout)
        return holder['instance']

    fake_module.Serial = make
    sys.modules['serial'] = fake_module
    return holder


def make_modem(port='/dev/ttyUSB2'):
    """A SerialModem over a FakeSerial, past its startup handshake (ATE0 / AT+CMGF=1 / AT+CSCS)."""
    holder = _install_fake_serial()

    # _configure() runs three commands synchronously during __init__, each waiting for "OK\r\n" —
    # queue all three answers up front so construction sails through.
    fake_module = sys.modules['serial']
    real_make = fake_module.Serial

    def make_and_prime(port_, baud, timeout=None):
        ser = real_make(port_, baud, timeout)
        ser.feed('OK\r\n' * 3)
        return ser

    fake_module.Serial = make_and_prime
    modem = m.SerialModem(port=port)
    return modem, holder['instance']


class SimModemTest(unittest.TestCase):
    def test_send_records_it(self):
        sm = m.SimModem()
        sm.send('+15550100001', 'hi there')
        self.assertEqual(sm.sent, [('+15550100001', 'hi there')])

    def test_a_failed_send_raises_and_is_not_recorded(self):
        sm = m.SimModem()
        sm.fail_next_send('no signal')
        with self.assertRaises(m.ModemError):
            sm.send('+15550100001', 'hi')
        self.assertEqual(sm.sent, [])

    def test_failure_is_one_shot(self):
        sm = m.SimModem()
        sm.fail_next_send()
        with self.assertRaises(m.ModemError):
            sm.send('+15550100001', 'a')
        sm.send('+15550100001', 'b')                 # the second attempt goes through
        self.assertEqual(sm.sent, [('+15550100001', 'b')])

    def test_delivered_messages_come_back_once(self):
        sm = m.SimModem()
        sm.deliver('+15550100002', 'hello')
        first = sm.poll_new()
        self.assertEqual(len(first), 1)
        self.assertEqual((first[0]['sender'], first[0]['body']), ('+15550100002', 'hello'))
        self.assertEqual(sm.poll_new(), [])            # not repeated

    def test_signal_and_registration_defaults(self):
        sm = m.SimModem()
        self.assertEqual(sm.signal_quality(), 20)
        self.assertTrue(sm.registered())
        sm = m.SimModem(signal=0, registered=False)
        self.assertEqual(sm.signal_quality(), 0)
        self.assertFalse(sm.registered())


class SerialModemConfigureTest(unittest.TestCase):
    def test_startup_sends_echo_off_text_mode_and_charset(self):
        modem, ser = make_modem()
        sent = bytes(ser.written).decode()
        self.assertEqual(sent, 'ATE0\r' + 'AT+CMGF=1\r' + 'AT+CSCS="GSM"\r')

    def test_a_bad_port_raises_modem_error_not_a_raw_exception(self):
        fake_module = MagicMock()

        def raise_os_error(*a, **kw):
            raise OSError('no such device')
        fake_module.Serial = raise_os_error
        sys.modules['serial'] = fake_module
        with self.assertRaises(m.ModemError):
            m.SerialModem(port='/dev/ttyNothing')

    def test_missing_pyserial_raises_modem_error(self):
        real_import = __import__

        def blocking_import(name, *a, **kw):
            if name == 'serial':
                raise ImportError('no module named serial')
            return real_import(name, *a, **kw)

        import builtins
        saved = sys.modules.pop('serial', None)
        old_import = builtins.__import__
        builtins.__import__ = blocking_import
        try:
            with self.assertRaises(m.ModemError) as cm:
                m.SerialModem(port='/dev/ttyUSB2')
            self.assertIn('pyserial', str(cm.exception))
        finally:
            builtins.__import__ = old_import
            if saved is not None:
                sys.modules['serial'] = saved


class SerialModemSendTest(unittest.TestCase):
    def test_a_successful_send_waits_for_the_prompt_then_sends_ctrl_z(self):
        modem, ser = make_modem()
        ser.written.clear()
        ser.feed('\r\n> ')                             # the modem's prompt for the message body
        # send() blocks reading for the prompt, then writes the body + Ctrl-Z, then reads for OK.
        # Feed the final OK "later" by pre-queuing it too, since our fake never blocks.
        ser.feed('\r\nOK\r\n')
        modem.send('+15550100001', 'hello there')
        written = bytes(ser.written)
        self.assertIn(b'AT+CMGS="+15550100001"\r', written)
        self.assertTrue(written.endswith(b'hello there\x1a'))

    def test_the_network_rejecting_it_raises(self):
        modem, ser = make_modem()
        ser.written.clear()
        ser.feed('\r\n> \r\n+CMS ERROR: 38\r\n')
        with self.assertRaises(m.ModemError):
            modem.send('+15550100001', 'hello')

    def test_no_prompt_ever_arriving_raises_rather_than_hanging(self):
        modem, ser = make_modem()
        ser.written.clear()
        with self.assertRaises(m.ModemError):
            with _patched_at_timeout(0.05):
                modem.send('+15550100001', 'hello')


def _patched_at_timeout(seconds):
    from unittest.mock import patch
    return patch.object(m, 'AT_TIMEOUT', seconds)


class SerialModemPollTest(unittest.TestCase):
    CMGL_TWO = (
        '\r\n+CMGL: 3,"REC UNREAD","+15550100002",,"26/09/23,14:03:22-20"\r\n'
        'Hello there\r\n'
        '+CMGL: 4,"REC UNREAD","+15550100003",,"26/09/23,14:05:01-20"\r\n'
        'Another message\r\n'
        'OK\r\n'
    )

    def test_parses_every_message_and_deletes_each_one(self):
        modem, ser = make_modem()
        ser.written.clear()
        ser.feed(self.CMGL_TWO)
        ser.feed('OK\r\n' * 2)                          # answers to the two AT+CMGD deletes
        found = modem.poll_new()
        self.assertEqual([(f['sender'], f['body']) for f in found],
                          [('+15550100002', 'Hello there'), ('+15550100003', 'Another message')])
        self.assertEqual(bytes(ser.written).count(b'AT+CMGD='), 2)
        self.assertIn(b'AT+CMGD=3\r', bytes(ser.written))
        self.assertIn(b'AT+CMGD=4\r', bytes(ser.written))

    def test_the_timestamp_is_converted_to_iso(self):
        modem, ser = make_modem()
        ser.feed(self.CMGL_TWO)
        ser.feed('OK\r\n' * 2)
        found = modem.poll_new()
        self.assertEqual(found[0]['ts'], '2026-09-23T14:03:22')

    def test_no_unread_messages_is_not_an_error(self):
        modem, ser = make_modem()
        ser.feed('\r\nOK\r\n')
        self.assertEqual(modem.poll_new(), [])

    def test_a_delete_that_fails_is_not_fatal(self):
        modem, ser = make_modem()
        ser.feed(self.CMGL_TWO)
        ser.feed('ERROR\r\n')                            # the first delete fails...
        ser.feed('OK\r\n')                                # ...the second still goes through
        found = modem.poll_new()                          # poll_new() must not raise even so
        self.assertEqual(len(found), 2)


class SerialModemStatusTest(unittest.TestCase):
    def test_signal_quality_parses_csq(self):
        modem, ser = make_modem()
        ser.feed('\r\n+CSQ: 18,99\r\nOK\r\n')
        self.assertEqual(modem.signal_quality(), 18)

    def test_unknown_signal_is_none(self):
        modem, ser = make_modem()
        ser.feed('\r\n+CSQ: 99,99\r\nOK\r\n')
        self.assertIsNone(modem.signal_quality())

    def test_registered_home_and_roaming_both_count(self):
        for stat, expected in (('1', True), ('5', True), ('0', False), ('2', False)):
            modem, ser = make_modem()
            ser.feed('\r\n+CREG: 0,%s\r\nOK\r\n' % stat)
            self.assertEqual(modem.registered(), expected, stat)


if __name__ == '__main__':
    unittest.main()
