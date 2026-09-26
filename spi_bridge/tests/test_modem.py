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
import unittest.mock
from unittest.mock import MagicMock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
import modem as m  # noqa: E402


class FakeModem:
    """Stands in for pyserial's Serial AND for the modem behind it: it answers each AT command the way a SIM7600
    does. Texts waiting on the modem are `inbox` entries; `commands` records every command line received, and
    `texts` every message body sent. Switches make it misbehave on purpose (refuse, never prompt, fail deletes)."""

    def __init__(self, port, baud, timeout=None):
        self.port = port
        self.commands, self.texts = [], []
        self.inbox = []                   # {'sender', 'body', 'ts', 'read'}
        self.charset = 'GSM'
        self.refuse_send = None           # e.g. '+CMS ERROR: 304' instead of the '>' prompt
        self.reject_after_text = None     # e.g. '+CMS ERROR: 500' after the text was typed
        self.no_prompt = False
        self.fail_delete = False
        self.broken = False               # every read/write raises, as when the dongle is unplugged
        self._out = bytearray()
        self._line = bytearray()
        self._typing = None               # the number being texted while in text-entry mode

    # pyserial's side
    @property
    def in_waiting(self):
        return len(self._out)

    def read(self, n=1):
        if self.broken:
            raise OSError(5, 'Input/output error')
        chunk = bytes(self._out[:max(1, n)])
        del self._out[:len(chunk)]
        return chunk

    def write(self, data):
        if self.broken:
            raise OSError(5, 'Input/output error')
        for byte in data:
            ch = bytes([byte])
            if self._typing is not None:
                if ch == b'\x1a':
                    self.texts.append((self._typing, bytes(self._line).decode('latin-1')))
                    self._typing, self._line = None, bytearray()
                    self._say(self.reject_after_text or '+CMGS: 7\r\n\r\nOK')
                elif ch == b'\x1b':
                    self._typing, self._line = None, bytearray()
                    self._say('OK')
                else:
                    self._line += ch
            elif ch == b'\r':
                self._command(bytes(self._line).decode('ascii'))
                self._line = bytearray()
            elif ch == b'\x1b':
                pass
            else:
                self._line += ch

    # the modem's side
    def _say(self, text):
        self._out += ('\r\n' + text + '\r\n').encode('latin-1')

    def _hex(self, text):
        return text.encode('utf-16-be').hex().upper() if self.charset == 'UCS2' else text

    def _command(self, cmd):
        self.commands.append(cmd)
        if cmd.startswith('AT+CSCS='):
            self.charset = cmd.split('"')[1]
            self._say('OK')
        elif cmd.startswith('AT+CMGS='):
            if self.refuse_send:
                self._say(self.refuse_send)
            elif not self.no_prompt:
                self._typing = cmd.split('"')[1]
                self._out += b'\r\n> '
        elif cmd.startswith('AT+CMGL='):
            rows = []
            for i, msg in enumerate(self.inbox):
                if not msg['read']:
                    msg['read'] = True
                    rows.append('+CMGL: %d,"REC UNREAD","%s","","%s"\r\n%s'
                                % (i, self._hex(msg['sender']), msg['ts'], self._hex(msg['body'])))
            self._say('\r\n'.join(rows + ['', 'OK']) if rows else 'OK')
        elif cmd == 'AT+CMGD=1,1':
            if self.fail_delete:
                self._say('ERROR')
            else:
                self.inbox = [x for x in self.inbox if not x['read']]
                self._say('OK')
        elif cmd == 'AT+CSQ':
            self._say('+CSQ: 18,99\r\n\r\nOK')
        elif cmd == 'AT+CREG?':
            self._say('+CREG: 0,1\r\n\r\nOK')
        else:
            self._say('OK')

    def deliver(self, sender, body, ts='26/09/23,14:03:22-20'):
        self.inbox.append({'sender': sender, 'body': body, 'ts': ts, 'read': False})


def make_modem(port='/dev/ttyUSB2'):
    """A SerialModem talking to a FakeModem, past its start-up."""
    holder = {}
    fake_module = MagicMock()

    def make(port_, baud, timeout=None):
        holder['instance'] = FakeModem(port_, baud, timeout)
        return holder['instance']

    fake_module.Serial = make
    sys.modules['serial'] = fake_module
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
    def test_startup_turns_echo_off_selects_text_mode_and_clears_read_texts(self):
        modem, fake = make_modem()
        self.assertEqual(fake.commands, ['ATE0', 'AT+CMGF=1', 'AT+CPMS="ME","ME","ME"', 'AT+CMGD=1,1'])

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
    def test_a_text_goes_in_ascii_so_at_dollar_and_underscore_arrive_as_typed(self):
        modem, fake = make_modem()
        modem.send('+15550100001', 'meet @ 5, $20, my_email [ok]')
        self.assertIn('AT+CSCS="IRA"', fake.commands)
        self.assertEqual(fake.texts, [('+15550100001', 'meet @ 5, $20, my_email [ok]')])

    def test_a_long_text_goes_as_several_split_between_words(self):
        modem, fake = make_modem()
        text = ' '.join(['word%03d' % i for i in range(40)])          # 319 characters
        modem.send('+15550100001', text)
        parts = [t for _, t in fake.texts]
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(p) <= m.SMS_CHARS for p in parts))
        self.assertEqual(' '.join(parts), text)                       # nothing lost, no word cut

    def test_the_network_rejecting_it_after_the_text_raises(self):
        modem, fake = make_modem()
        fake.reject_after_text = '+CMS ERROR: 500'
        with self.assertRaises(m.ModemError):
            modem.send('+15550100001', 'hi')

    def test_a_refusal_instead_of_the_prompt_raises_and_leaves_the_modem_usable(self):
        modem, fake = make_modem()
        fake.refuse_send = '+CMS ERROR: 304'
        with self.assertRaises(m.ModemError) as cm:
            modem.send('+15550100001', 'hi')
        self.assertIn('304', str(cm.exception))
        self.assertEqual(modem.signal_quality(), 18)                  # the next exchange is not confused

    def test_no_prompt_ever_raises_and_cancels_text_entry(self):
        modem, fake = make_modem()
        fake.no_prompt = True
        with unittest.mock.patch.object(m, 'AT_TIMEOUT', 0.3):
            with self.assertRaises(m.ModemError):
                modem.send('+15550100001', 'hi')
        fake.no_prompt = False
        self.assertEqual(modem.signal_quality(), 18)

    def test_split_text(self):
        self.assertEqual(m.split_text('short'), ['short'])
        self.assertEqual(m.split_text('x' * 170), ['x' * 160, 'x' * 10])      # no space: cut at the limit


class SerialModemPollTest(unittest.TestCase):
    def test_texts_arrive_whole_even_with_line_breaks_or_a_body_of_ok(self):
        modem, fake = make_modem()
        fake.deliver('+15550100002', 'See you\nat 5')
        fake.deliver('+15550100003', 'OK')
        fake.deliver('+15550100004', 'caf\u00e9 @ 7')
        got = modem.poll_new()
        self.assertEqual([(g['sender'], g['body']) for g in got],
                         [('+15550100002', 'See you\nat 5'), ('+15550100003', 'OK'), ('+15550100004', 'caf\u00e9 @ 7')])
        self.assertIn('AT+CSCS="UCS2"', fake.commands)
        self.assertEqual(fake.commands[-1], 'AT+CMGD=1,1')            # then cleared from storage
        self.assertEqual(fake.inbox, [])
        self.assertEqual(modem.poll_new(), [])                         # nothing repeats

    def test_the_timestamp_is_converted_to_iso(self):
        modem, fake = make_modem()
        fake.deliver('+15550100002', 'hi', ts='26/09/23,14:03:22-20')
        self.assertEqual(modem.poll_new()[0]['ts'], '2026-09-23T14:03:22')

    def test_no_unread_texts_is_not_an_error_and_deletes_nothing(self):
        modem, fake = make_modem()
        fake.commands.clear()
        self.assertEqual(modem.poll_new(), [])
        self.assertNotIn('AT+CMGD=1,1', fake.commands)

    def test_a_failed_clean_up_is_not_fatal_and_the_next_sweep_catches_up(self):
        modem, fake = make_modem()
        fake.deliver('+15550100002', 'one')
        fake.fail_delete = True
        self.assertEqual(len(modem.poll_new()), 1)
        self.assertEqual(len(fake.inbox), 1)                          # still stored, but marked read
        fake.fail_delete = False
        fake.deliver('+15550100003', 'two')
        self.assertEqual([g['body'] for g in modem.poll_new()], ['two'])   # the old one is not repeated
        self.assertEqual(fake.inbox, [])                              # and the sweep removed both

    def test_an_unplugged_dongle_raises_modem_error_not_a_raw_exception(self):
        modem, fake = make_modem()
        fake.broken = True
        with self.assertRaises(m.ModemError):
            modem.poll_new()
        with self.assertRaises(m.ModemError):
            modem.send('+15550100001', 'hi')

    def test_sending_and_polling_at_once_never_interleave(self):
        import threading, time
        modem, fake = make_modem()
        slow_read = m.SerialModem._read_raw

        def read_slowly(self):
            time.sleep(0.002)
            return slow_read(self)
        with unittest.mock.patch.object(m.SerialModem, '_read_raw', read_slowly):
            for i in range(5):
                fake.deliver('+1555010000%d' % i, 'msg %d' % i)
            errors, got = [], []

            def sender():
                try:
                    for i in range(5):
                        modem.send('+15550100009', 'out %d' % i)
                except Exception as e:
                    errors.append(e)

            def poller():
                try:
                    for _ in range(5):
                        got.extend(modem.poll_new())
                except Exception as e:
                    errors.append(e)
            threads = [threading.Thread(target=sender), threading.Thread(target=poller)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(30)
        self.assertEqual(errors, [])
        self.assertEqual([t for _, t in fake.texts], ['out %d' % i for i in range(5)])
        self.assertEqual(sorted(g['body'] for g in got), ['msg %d' % i for i in range(5)])


class SerialModemStatusTest(unittest.TestCase):
    def test_signal_quality_and_registration(self):
        modem, fake = make_modem()
        self.assertEqual(modem.signal_quality(), 18)
        self.assertTrue(modem.registered())


if __name__ == '__main__':
    unittest.main()
