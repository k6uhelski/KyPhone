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
        self.inbox = {}                   # index -> {'pdu', 'storage', 'read'}
        self.charset = 'GSM'
        self.pdu_mode = False
        self.storage = 'ME'
        self.refuse_send = None           # e.g. '+CMS ERROR: 304' instead of the '>' prompt
        self.reject_after_text = None     # e.g. '+CMS ERROR: 500' after the text was typed
        self.no_prompt = False
        self.fail_delete = False
        self.broken = False               # every read/write raises, as when the dongle is unplugged
        self.sim, self.reg = 'READY', '1'  # AT+CPIN? state ('' = no SIM) and AT+CEREG? registration
        self._out = bytearray()
        self._line = bytearray()
        self._typing = None               # the number being texted while in text-entry mode

    # pyserial's side
    closed = False

    def close(self):
        self.closed = True

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
        elif cmd == 'AT+CMGF=0' or cmd == 'AT+CMGF=1':
            self.pdu_mode = cmd.endswith('0')
            self._say('OK')
        elif cmd == 'AT+CPMS?':
            used = sum(1 for x in self.inbox.values() if x['storage'] == self.storage)
            self._say('+CPMS: "{0}",{1},30,"{0}",{1},30,"{0}",{1},30\r\n\r\nOK'.format(self.storage, used))
        elif cmd.startswith('AT+CPMS='):
            self.storage = cmd.split('"')[1]
            used = sum(1 for x in self.inbox.values() if x['storage'] == self.storage)
            self._say('+CPMS: {0},30,{0},30,{0},30\r\n\r\nOK'.format(used))
        elif cmd.startswith('AT+CMGL='):
            # As the real SIM7600 does (found on the phone, 2026-09-26): texts are on the SIM, and PDU mode lists them.
            if not self.pdu_mode or cmd != 'AT+CMGL=4':
                self._say('+CMS ERROR: Unknown error')
                return
            rows = []
            for i, msg in sorted(self.inbox.items()):
                if msg['storage'] == self.storage:
                    msg['read'] = True
                    rows.append('+CMGL: %d,%d,"",%d\r\n%s' % (i, 1, len(msg['pdu']) // 2 - 8, msg['pdu']))
            self._say('\r\n'.join(rows + ['', 'OK']) if rows else 'OK')
        elif cmd.startswith('AT+CMGD='):
            index = int(cmd.split('=')[1].split(',')[0])
            if self.fail_delete or index not in self.inbox or self.inbox[index]['storage'] != self.storage:
                self._say('+CMS ERROR: 321')
            else:
                del self.inbox[index]
                self._say('OK')
        elif cmd == 'AT+CSQ':
            self._say('+CSQ: 18,99\r\n\r\nOK')
        elif cmd == 'AT+CPIN?':
            self._say('+CPIN: %s\r\n\r\nOK' % self.sim if self.sim else '+CME ERROR: 10')
        elif cmd == 'AT+CEREG?':
            self._say('+CEREG: 0,%s\r\n\r\nOK' % self.reg)
        elif cmd == 'AT+COPS?':
            self._say('+COPS: 0,0,"Test Carrier",7\r\n\r\nOK')
        elif cmd == 'AT+CSCA?':
            self._say('+CSCA: "+15550100900",145\r\n\r\nOK')
        elif cmd == 'AT+CREG?':
            self._say('+CREG: 0,%s\r\n\r\nOK' % self.reg)
        else:
            self._say('OK')

    def deliver(self, sender, body, ts='26/09/23,14:03:22-20', storage='SM', part=None):
        self.store(make_pdu(sender, body, ts, part), storage)

    def store(self, pdu, storage='SM'):
        index = max(self.inbox, default=-1) + 1
        self.inbox[index] = {'pdu': pdu, 'storage': storage, 'read': False}


def _semi(digits):
    digits += 'F' * (len(digits) % 2)
    return ''.join(digits[i + 1] + digits[i] for i in range(0, len(digits), 2))


def make_pdu(sender, body, ts='26/09/23,14:03:22-20', part=None, report=False):
    """An SMS-DELIVER PDU as a SIM7600 lists it: GSM 7-bit when the body fits the basic alphabet, else UCS2;
    `part` = (ref, count, n) adds a concatenation header."""
    smsc = '0791' + _semi('15550100900')
    first = (0x02 if report else 0x04) | (0x40 if part else 0)
    digits = sender.lstrip('+')
    if sender[:1].isalpha():
        packed = pack7(sender)
        oa = '%02X' % (len(packed) * 2) + 'D0' + packed.hex().upper()
    else:
        oa = '%02X%s%s' % (len(digits), '91' if sender.startswith('+') else '81', _semi(digits))
    y, mo, d = ts[0:2], ts[3:5], ts[6:8]
    h, mi, se = ts[9:11], ts[12:14], ts[15:17]
    scts = _semi(y + mo + d + h + mi + se) + '8A'
    if part and part[0] > 255:                                   # 16-bit reference number
        udh = bytes([6, 8, 4, part[0] >> 8, part[0] & 0xFF, part[1], part[2]])
    else:
        udh = bytes([5, 0, 3] + list(part)) if part else b''
    if all(c in m._GSM7 for c in body):
        septets = [m._GSM7.index(c) for c in body]
        skip = (len(udh) * 8 + 6) // 7
        bits = int.from_bytes(udh, 'little')
        for k, c in enumerate(septets):
            bits |= c << (7 * (skip + k))
        udl = skip + len(septets)
        ud = bits.to_bytes((udl * 7 + 7) // 8, 'little')
        dcs = '00'
    else:
        ud = udh + body.encode('utf-16-be')
        udl = len(ud)
        dcs = '08'
    return (smsc + '%02X' % first + oa + '00' + dcs + scts + '%02X' % udl + ud.hex()).upper()


def _gsm_pdu_with(sender, body):
    """A GSM 7-bit PDU whose body uses the escape table ({ } [ ] ~ ^ | \\ and the euro sign)."""
    ext = {v: k for k, v in m._GSM7_EXT.items()}
    septets = []
    for c in body:
        septets += [0x1B, ext[c]] if c in ext else [m._GSM7.index(c)]
    bits = 0
    for k, c in enumerate(septets):
        bits |= c << (7 * k)
    ud = bits.to_bytes((len(septets) * 7 + 7) // 8, 'little')
    digits = sender.lstrip('+')
    return ('0791' + _semi('15550100900') + '04' + '%02X91%s' % (len(digits), _semi(digits)) + '0000'
            + _semi('260923140322') + '8A' + '%02X' % len(septets) + ud.hex()).upper()


def pack7(text):
    septets = [m._GSM7.index(c) for c in text]
    bits = 0
    for k, c in enumerate(septets):
        bits |= c << (7 * k)
    return bits.to_bytes((len(septets) * 7 + 7) // 8, 'little')


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
    def test_startup_turns_echo_off_selects_text_mode_and_sim_storage(self):
        modem, fake = make_modem()
        self.assertEqual(fake.commands, ['ATE0', 'AT+CMGF=1', 'AT+CPMS="SM","SM","SM"'])

    def test_a_modem_still_booting_closes_the_port_it_opened(self):
        holder = {}
        fake_module = MagicMock()

        def make(port_, baud, timeout=None):
            holder['fake'] = FakeModem(port_, baud, timeout)
            holder['fake'].broken = True               # opens, but nothing answers yet
            return holder['fake']
        fake_module.Serial = make
        sys.modules['serial'] = fake_module
        with self.assertRaises(m.ModemError):
            m.SerialModem(port='/dev/ttyUSB2')
        self.assertTrue(holder['fake'].closed)

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
        self.assertIn('AT+CMGL=4', fake.commands)                      # listed in PDU mode
        self.assertEqual(fake.inbox, {})                               # then each deleted
        self.assertTrue(fake.commands.index('AT+CMGF=1', fake.commands.index('AT+CMGL=4')))   # back to text mode
        self.assertEqual(modem.poll_new(), [])                         # nothing repeats

    def test_the_timestamp_is_converted_to_iso(self):
        modem, fake = make_modem()
        fake.deliver('+15550100002', 'hi', ts='26/09/23,14:03:22-20')
        self.assertEqual(modem.poll_new()[0]['ts'], '2026-09-23T14:03:22')

    def test_no_unread_texts_is_not_an_error_and_deletes_nothing(self):
        modem, fake = make_modem()
        fake.commands.clear()
        self.assertEqual(modem.poll_new(), [])
        self.assertFalse([c for c in fake.commands if c.startswith('AT+CMGD')])

    def test_a_failed_clean_up_is_not_fatal_and_the_next_sweep_catches_up(self):
        modem, fake = make_modem()
        fake.deliver('+15550100002', 'one')
        fake.fail_delete = True
        self.assertEqual(len(modem.poll_new()), 1)
        self.assertEqual(len(fake.inbox), 1)                          # still stored, but marked read
        fake.fail_delete = False
        fake.deliver('+15550100003', 'two')
        self.assertEqual([g['body'] for g in modem.poll_new()], ['two'])   # the old one is not repeated
        self.assertEqual(modem.poll_new(), [])
        self.assertEqual(fake.inbox, {})                              # and both are gone now

    def test_a_long_text_in_two_parts_arrives_as_one(self):
        modem, fake = make_modem()
        fake.deliver('6700', 'Your plan is active for 30 days. ', part=(7, 2, 1))
        fake.deliver('+15550100002', 'between')
        fake.deliver('6700', 'Reply HELP for help.', part=(7, 2, 2))
        got = modem.poll_new()
        self.assertEqual(sorted(g['body'] for g in got),
                         ['Your plan is active for 30 days. Reply HELP for help.', 'between'])
        self.assertEqual(fake.inbox, {})

    def test_parts_arriving_out_of_order_are_joined_in_order(self):
        modem, fake = make_modem()
        fake.deliver('+15550100002', 'world', part=(300, 2, 2))
        fake.deliver('+15550100002', 'hello ', part=(300, 2, 1))
        self.assertEqual([g['body'] for g in modem.poll_new()], ['hello world'])

    def test_a_missing_part_is_waited_for_then_what_arrived_is_shown(self):
        modem, fake = make_modem()
        fake.deliver('+15550100002', 'first half ', part=(9, 2, 1))
        self.assertEqual(modem.poll_new(), [])
        self.assertEqual(len(fake.inbox), 1)                          # kept until the rest comes
        fake.deliver('+15550100002', 'second half', part=(9, 2, 2))
        self.assertEqual([g['body'] for g in modem.poll_new()], ['first half second half'])
        fake.deliver('+15550100003', 'lost the rest', part=(4, 3, 1))
        self.assertEqual(modem.poll_new(), [])
        with unittest.mock.patch.object(m.time, 'monotonic', return_value=m.time.monotonic() + m.PART_WAIT + 1):
            self.assertEqual([g['body'] for g in modem.poll_new()], ['lost the rest'])
        self.assertEqual(fake.inbox, {})

    def test_short_codes_names_and_escaped_characters(self):
        modem, fake = make_modem()
        fake.deliver('6700', 'Thank you')
        fake.deliver('Carrier', 'hi')
        fake.store(make_pdu('+15550100004', 'placeholder'))
        fake.inbox[max(fake.inbox)]['pdu'] = _gsm_pdu_with('+15550100004', '{a}[b]\\~^|\u20ac')
        got = [(g['sender'], g['body']) for g in modem.poll_new()]
        self.assertIn(('6700', 'Thank you'), got)
        self.assertIn(('Carrier', 'hi'), got)
        self.assertIn(('+15550100004', '{a}[b]\\~^|\u20ac'), got)

    def test_emoji_and_accents_survive(self):
        modem, fake = make_modem()
        fake.deliver('+15550100002', 'caf\u00e9 \U0001F600')
        self.assertEqual(modem.poll_new()[0]['body'], 'caf\u00e9 \U0001F600')

    def test_a_delivery_report_is_cleared_away_not_shown(self):
        modem, fake = make_modem()
        fake.store(make_pdu('+15550100002', 'status', report=True))
        self.assertEqual(modem.poll_new(), [])
        self.assertEqual(fake.inbox, {})

    def test_an_unreadable_pdu_is_left_alone(self):
        modem, fake = make_modem()
        fake.store('0791AB')
        self.assertEqual(modem.poll_new(), [])
        self.assertEqual(len(fake.inbox), 1)

    def test_texts_in_the_modems_own_storage_are_read_too(self):
        modem, fake = make_modem()
        fake.deliver('+15550100002', 'on the SIM')
        fake.deliver('+15550100003', 'on the modem', storage='ME')
        self.assertEqual(sorted(g['body'] for g in modem.poll_new()), ['on the SIM', 'on the modem'])
        self.assertEqual(fake.inbox, {})
        self.assertEqual(fake.commands[-1], 'AT+CPMS="SM","SM","SM"')  # new texts still land on the SIM

    def test_texts_come_back_oldest_first(self):
        modem, fake = make_modem()
        fake.deliver('+15550100002', 'later', ts='26/09/23,14:05:00-20')
        fake.deliver('+15550100002', 'earlier', ts='26/09/23,14:01:00-20')
        self.assertEqual([g['body'] for g in modem.poll_new()], ['earlier', 'later'])

    def test_a_send_after_a_poll_is_in_text_mode(self):
        modem, fake = make_modem()
        fake.deliver('+15550100002', 'hi')
        modem.poll_new()
        modem.send('+15550100001', 'back')
        self.assertFalse(fake.pdu_mode)
        self.assertEqual(fake.texts, [('+15550100001', 'back')])

    def test_an_empty_sim_costs_one_command_per_poll(self):
        modem, fake = make_modem()
        modem.poll_new()                                              # the first poll also looks at ME
        fake.commands.clear()
        for _ in range(5):
            self.assertEqual(modem.poll_new(), [])
        self.assertEqual(fake.commands, ['AT+CPMS?'] * 5)
        fake.deliver('+15550100002', 'now there is one')
        self.assertEqual([g['body'] for g in modem.poll_new()], ['now there is one'])

    def test_the_modems_own_storage_is_checked_now_and_then(self):
        modem, fake = make_modem()
        modem.poll_new()
        fake.deliver('+15550100003', 'on the modem', storage='ME')
        got = []
        for _ in range(m.ME_CHECK_EVERY):
            got += modem.poll_new()
        self.assertEqual([g['body'] for g in got], ['on the modem'])

    def test_an_unreadable_count_falls_back_to_listing(self):
        modem, fake = make_modem()
        modem.poll_new()
        fake.deliver('+15550100002', 'hi')
        with unittest.mock.patch.object(m, '_CPMS_LINE', m.re.compile('^never$')):
            self.assertEqual([g['body'] for g in modem.poll_new()], ['hi'])

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


class ModemCheckTool(unittest.TestCase):
    """tools/modem_check.py: the first-real-text test, against the scripted fake modem."""

    def run_tool(self, *argv, sim='READY', reg='1', deliver=None):
        import io
        from contextlib import redirect_stdout
        sys.path.insert(0, os.path.join(HERE, '..', 'tools'))
        import modem_check, find_modem_port
        holder = {}
        fake_module = MagicMock()

        def make(port_, baud, timeout=None):
            holder['fake'] = FakeModem(port_, baud, timeout)
            holder['fake'].sim, holder['fake'].reg = sim, reg
            if deliver:
                holder['fake'].deliver(*deliver)
            return holder['fake']
        fake_module.Serial = make
        sys.modules['serial'] = fake_module
        out = io.StringIO()
        with unittest.mock.patch.object(find_modem_port, 'candidates', return_value=['/dev/ttyUSB2']), \
                unittest.mock.patch.object(find_modem_port, 'probe', return_value=(True, 'OK')), \
                unittest.mock.patch.object(modem_check.time, 'sleep', lambda s: None), redirect_stdout(out):
            code = modem_check.main(list(argv))
        return code, out.getvalue(), holder.get('fake')

    def test_everything_ready(self):
        code, out, _ = self.run_tool()
        self.assertEqual(code, 0, out)
        for words in ('ok 1. the modem answers on /dev/ttyUSB2', 'ok 2. the SIM is in and unlocked',
                      'ok 3. signal 18 of 31', 'ok 4. registered on the home network', 'carrier: Test Carrier',
                      'ok 5. SMS centre set', 'KYPHONE_MODEM_PORT=/dev/ttyUSB2'):
            self.assertIn(words, out)

    def test_no_sim_or_not_activated_says_what_to_do(self):
        code, out, _ = self.run_tool(sim='')
        self.assertEqual(code, 1)
        self.assertIn('X  2. no SIM detected', out)
        code, out, _ = self.run_tool(reg='3')
        self.assertIn('the SIM may not be activated yet', out)

    def test_send_and_wait_for_a_reply(self):
        code, out, fake = self.run_tool('--send', '5550100001', '--wait', '30',
                                        deliver=('+15550100001', 'got it'))
        self.assertEqual(code, 0, out)
        self.assertEqual(fake.texts[0][0], '+15550100001')           # formatted as the phone sends it
        self.assertIn('ok 7. received from +15550100001', out)
        self.assertIn('got it', out)

    def test_waiting_prints_every_text_and_keeps_going_until_the_reply(self):
        code, out, fake = self.run_tool('--send', '5550100001', '--wait', '1',
                                        deliver=('6700', 'carrier notice'))
        self.assertIn('carrier notice', out)
        self.assertEqual(code, 1, out)            # the carrier's text is not the reply; nothing else came

    def test_interpreters(self):
        import modem_check as mc
        self.assertEqual(mc.interpret_csq(['+CSQ: 99,99'])[0], False)
        self.assertEqual(mc.interpret_csq(['+CSQ: 25,99']), (True, 'signal 25 of 31 (excellent)'))
        self.assertEqual(mc.interpret_reg(['+CREG: 0,2', '+CEREG: 0,5']), (True, 'registered (roaming)'))
        self.assertEqual(mc.interpret_cpin(['+CPIN: SIM PIN'])[0], False)
        self.assertEqual(mc.interpret_csca(['+CSCA: "",145'])[0], False)


if __name__ == '__main__':
    unittest.main()
