"""modem.py — send and receive text messages over a real cellular modem (a Waveshare SIM7600G-H USB
dongle, or any modem that speaks the same Hayes AT command set) instead of over the internet.

Two implementations, the same shape as music_player.py's SimPlayer/GstPlayer split:

    SimModem     no hardware: an in-memory inbox/outbox for the emulator and every test. Tests call
                 .deliver() to make a text "arrive", and read .sent to see what was sent.
    SerialModem  the real thing: talks AT commands over the dongle's USB serial AT port (pyserial).
                 `serial` is imported lazily, inside __init__, so the Mac and the test suite never need
                 it — only a real phone with the dongle plugged in does.

Both implement the four calls kyphone_os.py needs:
    send(number, text)   -> raises ModemError if the network would not take it
    poll_new()           -> [{'sender', 'body', 'ts'}, ...] new inbound texts since the last poll (each
                             one is removed from the modem's own storage as it's read, so nothing repeats)
    signal_quality()     -> 0-31 (the AT+CSQ scale), or None if unknown
    registered()         -> True once the modem has joined the carrier's network

The AT dialogue (AT+CMGS/CMGL/CMGD/CSQ/CREG) is standard Hayes/3GPP TS 27.005, and matches Waveshare's own
SIM7600G-H notes. Sending and receiving use different modes on purpose:
    sending   text mode with AT+CSCS="IRA" (plain ASCII, which the modem turns into the GSM alphabet itself, so
              @ $ _ arrive as typed). A text longer than one SMS (160) goes as several texts, split between words.
    receiving PDU mode (AT+CMGF=0, AT+CMGL=4): each stored text arrives as ONE line of hex that decode_pdu() reads
              (3GPP TS 23.040), so a text with line breaks, or a text that is just "OK", can never be mistaken for
              the modem's own replies; accents survive, and a long text sent in parts is joined again. (Text mode
              in UCS2 was tried first: the real SIM7600 refuses every AT+CMGL filter in UCS2, found 2026-09-26.)
              Texts are kept on the SIM (AT+CPMS="SM"), which is where the SIM7600 stores them; the modem's own
              storage is read too, in case another setup left texts there. Each text is deleted by its index
              once it has been handed over.
One lock serializes every exchange (the send worker, the poll loop and start-up all use the one port), and any
serial failure is raised as ModemError so callers only ever handle that. It is checked against the real dongle once a SIM is active — nothing
here can prove real hardware talks back correctly; that is a phone-session step, not a test.
"""

import os
import re
import threading
import time
from datetime import datetime

MODEM_PORT_ENV = 'KYPHONE_MODEM_PORT'     # e.g. /dev/ttyUSB2 — which port lands on the AT interface
                                           # depends on USB enumeration order; see CLAUDE.md once set up.
DEFAULT_PORT   = '/dev/ttyUSB2'
DEFAULT_BAUD   = 115200

AT_TIMEOUT   = 5     # seconds to wait for a normal AT response (OK/ERROR)
SEND_TIMEOUT = 20     # sending a text can take longer (it waits on the network, not just the modem)
POLL_TIMEOUT = 8     # AT+CMGL can take a moment with several messages waiting
SMS_CHARS    = 160   # one text in the GSM alphabet

ME_CHECK_EVERY = 30  # polls between looks at the modem's own storage (about a minute at a 2 s poll)
PART_WAIT    = 600   # seconds to wait for the missing parts of a long text before showing what arrived

_CMGL_PDU    = re.compile(r'^\+CMGL:\s*(\d+),')
_HEX         = re.compile(r'^(?:[0-9A-Fa-f]{2})+$')
_CPMS_LINE   = re.compile(r'^\+CPMS:\s*"[A-Z]+",\s*(\d+),')
_CSQ_LINE    = re.compile(r'^\+CSQ:\s*(\d+),')
_CREG_LINE   = re.compile(r'^\+CREG:\s*\d+,\s*(\d+)')
_MODEM_TS    = re.compile(r'^(\d\d)/(\d\d)/(\d\d),(\d\d):(\d\d):(\d\d)')


class ModemError(Exception):
    """The modem could not be reached, or it reported failure sending or reading a text."""


# The GSM 03.38 default alphabet (septet value -> character) and its escape table (after 0x1B).
_GSM7 = ('@£$¥èéùìòÇ\nØø\rÅå'
         'Δ_ΦΓΛΩΠΨΣΘΞ\x1bÆæßÉ'
         ' !"#¤%&\'()*+,-./0123456789:;<=>?'
         '¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§'
         '¿abcdefghijklmnopqrstuvwxyzäöñüà')
_GSM7_EXT = {0x0A: '\f', 0x14: '^', 0x28: '{', 0x29: '}', 0x2F: '\\', 0x3C: '[', 0x3D: '~', 0x3E: ']',
             0x40: '|', 0x65: '€'}


def _gsm7_text(data, septets, skip=0):
    """`septets` characters packed 7 bits each into `data`, starting `skip` septets in (past a header)."""
    bits = int.from_bytes(data, 'little')
    out, escaped = [], False
    for i in range(skip, septets):
        c = (bits >> (7 * i)) & 0x7F
        if escaped:
            out.append(_GSM7_EXT.get(c, ' '))
            escaped = False
        elif c == 0x1B:
            escaped = True
        else:
            out.append(_GSM7[c])
    return ''.join(out)


def _swapped(octets):
    """Semi-octets, low nibble first (how PDUs store digits); F is padding."""
    return ''.join('%x%x' % (b & 0xF, b >> 4) for b in octets).rstrip('f')


def _alphabet(dcs):
    """The data coding scheme's alphabet: 'gsm', '8bit' or 'ucs2' (TS 23.038)."""
    if dcs < 0x80:
        return ('gsm', '8bit', 'ucs2', 'gsm')[(dcs >> 2) & 3]
    if dcs >> 4 == 0xF:
        return '8bit' if dcs & 4 else 'gsm'
    if dcs >> 4 == 0xE:
        return 'ucs2'
    return 'gsm'


def decode_pdu(hexstr):
    """One received SMS PDU (SMSC included, as AT+CMGL gives it in PDU mode) as
    {'sender', 'body', 'ts', 'part': (ref, count, n) or None}, or None if it is not an incoming text
    (a delivery report, say). Raises ValueError on a PDU too short or malformed to read."""
    d = bytes.fromhex(hexstr.strip())
    i = d[0] + 1                                   # skip the SMS centre's address
    first = d[i]; i += 1
    if first & 0x03 != 0:                          # not SMS-DELIVER
        return None
    digits, toa = d[i], d[i + 1]; i += 2
    raw = d[i:i + (digits + 1) // 2]; i += (digits + 1) // 2
    if toa & 0x70 == 0x50:                         # alphanumeric sender ("T-Mobile")
        sender = _gsm7_text(raw, digits * 4 // 7).rstrip('@')   # a 7-character name leaves a zero septet of padding
    else:
        sender = ('+' if toa & 0x70 == 0x10 else '') + _swapped(raw)[:digits]
    i += 1                                         # protocol identifier
    dcs = d[i]; i += 1
    ts = _swapped(d[i:i + 6]); i += 7              # YYMMDDhhmmss, then a timezone we ignore
    udl = d[i]; i += 1
    ud = d[i:]
    alphabet = _alphabet(dcs)
    needed = (udl * 7 + 7) // 8 if alphabet == 'gsm' else udl
    if len(ts) != 12 or len(ud) < needed:
        raise ValueError('short PDU')
    part, header = None, 0
    if first & 0x40:                               # a user data header: look for the concatenation element
        header = ud[0] + 1
        j = 1
        while j + 1 < header:
            iei, length = ud[j], ud[j + 1]
            v = ud[j + 2:j + 2 + length]
            if iei == 0x00 and length == 3:
                part = (v[0], v[1], v[2])
            elif iei == 0x08 and length == 4:
                part = (v[0] << 8 | v[1], v[2], v[3])
            j += 2 + length
    if alphabet == 'gsm':
        body = _gsm7_text(ud, udl, skip=(header * 8 + 6) // 7)
    elif alphabet == 'ucs2':
        body = ud[header:udl].decode('utf-16-be', errors='replace')
    else:
        body = ud[header:udl].decode('latin-1')
    stamp = '%s/%s/%s,%s:%s:%s' % (ts[0:2], ts[2:4], ts[4:6], ts[6:8], ts[8:10], ts[10:12])
    return {'sender': sender, 'body': body, 'ts': _parse_modem_ts(stamp), 'part': part}


def split_text(text, size=SMS_CHARS):
    """A long text as several SMS of at most `size` characters, split between words where possible."""
    parts, rest = [], text
    while len(rest) > size:
        cut = rest.rfind(' ', 0, size + 1)
        cut = cut if cut > size // 2 else size
        parts.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    parts.append(rest)
    return parts


def _parse_modem_ts(raw):
    """The AT+CMGL timestamp ("26/09/23,14:03:22-20", YY/MM/DD,HH:MM:SS, a timezone offset we ignore) as
    an ISO string, matching what the rest of kyphone_os.py stores. Anything unparseable falls back to
    now — a message is never lost over a timestamp the modem phrased oddly."""
    m = _MODEM_TS.match(raw)
    if not m:
        return datetime.now().isoformat()
    yy, mm, dd, h, mi, s = (int(x) for x in m.groups())
    try:
        return datetime(2000 + yy, mm, dd, h, mi, s).isoformat()
    except ValueError:
        return datetime.now().isoformat()


class SimModem:
    """No hardware: an in-memory modem for the emulator and every test."""

    def __init__(self, signal=20, registered=True):
        self.port = 'sim'           # SerialModem's .port has a real device path; this just fills the shape
        self.sent = []              # [(number, text)], in the order send() was called
        self._inbox = []
        self._fail_next = None      # set to a message string to make the next send() raise it
        self._signal = signal
        self._registered = registered

    def deliver(self, sender, body, ts=None):
        """Test/emulator hook: pretend a text just arrived from `sender`."""
        self._inbox.append({'sender': sender, 'body': body, 'ts': ts or datetime.now().isoformat()})

    def fail_next_send(self, reason='no service (simulated failure)'):
        """Test hook: the next send() raises `reason` instead of succeeding."""
        self._fail_next = reason

    def send(self, number, text):
        if self._fail_next is not None:
            reason, self._fail_next = self._fail_next, None
            raise ModemError(reason)
        self.sent.append((number, text))

    def poll_new(self):
        new, self._inbox = self._inbox, []
        return new

    def signal_quality(self):
        return self._signal

    def registered(self):
        return self._registered


class SerialModem:
    """Talks AT commands to a real modem over its USB serial AT port. `port` defaults to
    KYPHONE_MODEM_PORT, else DEFAULT_PORT — the exact /dev/ttyUSBn the AT interface lands on depends on
    USB enumeration order and may need setting explicitly once the dongle is plugged in."""

    def __init__(self, port=None, baud=DEFAULT_BAUD):
        self.port = port or os.environ.get(MODEM_PORT_ENV, DEFAULT_PORT)
        self._lock = threading.RLock()     # one AT exchange at a time: send, poll and start-up share the port
        self._polls = 0          # poll_new calls so far (the modem's own storage is checked on some)
        self._handed = set()     # PDUs already handed over whose delete failed (so they never repeat)
        self._parts_seen = {}    # (storage, sender, ref) of a long text still missing parts -> when first seen
        self._buf = ''      # bytes already read from the port but not yet consumed by any command —
                             # a read can come back with more than one response's worth at once, so
                             # this has to live on the instance, not as a local in _read_until/send()
        try:
            import serial   # lazy: only real hardware needs pyserial's device access
            self._ser = serial.Serial(self.port, baud, timeout=0.2)
        except ImportError as e:
            raise ModemError('pyserial not installed (%s)' % e.__class__.__name__)
        except OSError as e:                # no such port, permission denied, device unplugged, ...
            raise ModemError('could not open %s (%s)' % (self.port, e))
        self._configure()

    def _configure(self):
        with self._lock:
            self._cmd('ATE0')              # echo off — every response after this is just the answer
            self._cmd('AT+CMGF=1')         # text mode for sending (poll_new switches to PDU and back)
            self._cmd('AT+CPMS="SM","SM","SM"')    # texts live on the SIM, where the SIM7600 stores them

    # -- the AT dialogue --

    def _write(self, s):
        self._write_bytes((s + '\r').encode('ascii', errors='replace'))

    def _write_bytes(self, b):
        try:
            self._ser.write(b)
        except Exception as e:             # pyserial's SerialException, OSError: the dongle went away
            raise ModemError('write failed (%s)' % e.__class__.__name__)

    def _read_raw(self):
        try:
            chunk = self._ser.read(max(1, self._ser.in_waiting))
        except Exception as e:
            raise ModemError('read failed (%s)' % e.__class__.__name__)
        return chunk.decode('ascii', errors='replace') if chunk else ''

    def _cancel_text_entry(self):
        """After a send went wrong: ESC leaves the modem's text-entry mode, then whatever it said is dropped."""
        try:
            self._write_bytes(b'\x1b')
            time.sleep(0.3)
            self._read_raw()
        except ModemError:
            pass
        self._buf = ''

    def _read_until(self, terminators, timeout):
        """Reads lines until one is exactly a terminator, or an error line (+CME ERROR / +CMS ERROR /
        ERROR) — whichever comes first. Returns (ok, body_lines, last_line). Anything read past that
        line stays in self._buf for whatever is read next — a single read can come back with more than
        one response's worth of bytes at once."""
        deadline = time.monotonic() + timeout
        lines = []
        while True:
            while '\n' in self._buf:
                line, self._buf = self._buf.split('\n', 1)
                line = line.strip('\r\n \t')
                if not line:
                    continue
                if line in terminators:
                    return line == 'OK', lines, line
                if line == 'ERROR' or line.startswith('+CME ERROR') or line.startswith('+CMS ERROR'):
                    return False, lines, line
                lines.append(line)
            if time.monotonic() >= deadline:
                raise ModemError('modem did not answer within %ss' % timeout)
            self._buf += self._read_raw()

    def _cmd(self, command, timeout=AT_TIMEOUT):
        self._write(command)
        ok, lines, last = self._read_until(('OK',), timeout)
        if not ok:
            raise ModemError('%s -> %s' % (command, last))
        return lines

    # -- what kyphone_os.py calls --

    def send(self, number, text):
        """One text, or several if it is longer than one SMS. Raises ModemError on the first part that fails."""
        with self._lock:
            self._cmd('AT+CMGF=1')
            self._cmd('AT+CSCS="IRA"')
            for part in split_text(text):
                self._send_one(number, part)

    def _send_one(self, number, text):
        self._write('AT+CMGS="%s"' % number)
        deadline = time.monotonic() + AT_TIMEOUT
        while '>' not in self._buf:
            refused = re.search(r'(?:^|\n)\s*(ERROR|\+CMS ERROR[^\r\n]*|\+CME ERROR[^\r\n]*)\s*\r?\n', self._buf)
            if refused:
                self._cancel_text_entry()
                raise ModemError('send refused: %s' % refused.group(1))
            if time.monotonic() >= deadline:
                self._cancel_text_entry()
                raise ModemError('modem never prompted for the message text')
            self._buf += self._read_raw()
        self._buf = self._buf.split('>', 1)[1]        # the prompt itself is not a line _read_until sees
        self._write_bytes(text.encode('ascii', errors='replace') + b'\x1a')   # Ctrl-Z: send what was typed
        try:
            ok, lines, last = self._read_until(('OK',), SEND_TIMEOUT)
        except ModemError:
            self._cancel_text_entry()
            raise
        if not ok:
            raise ModemError('send failed: %s' % last)

    def poll_new(self):
        """New texts, oldest first, each deleted from storage once read. A long text sent in parts comes back as one
        text once every part is in (or after PART_WAIT seconds, with what arrived). A text whose delete failed is
        remembered, so it is never handed over twice.

        Polled every couple of seconds, so the common case is one command: AT+CPMS? says how many texts the SIM
        holds, and nothing is listed when it is none. The modem's own storage, where the SIM7600 has never put a
        text, is only looked at on the first poll and every ME_CHECK_EVERY polls after."""
        with self._lock:
            self._polls += 1
            check_me = self._polls % ME_CHECK_EVERY == 1
            if not check_me and self._stored_count() == 0:
                return []
            messages = []
            for storage in ('SM', 'ME') if check_me else ('SM',):
                try:
                    self._cmd('AT+CPMS="%s"' % storage)       # the storage that listing and deleting act on
                except ModemError:
                    continue                                   # e.g. no ME storage on this modem
                messages += self._take_stored(storage)
            self._cmd('AT+CPMS="SM","SM","SM"')
            return sorted(messages, key=lambda x: x['ts'])

    def _stored_count(self):
        """How many texts the SIM holds (AT+CPMS?'s first count), or None if the answer could not be read."""
        for line in self._cmd('AT+CPMS?'):
            m = _CPMS_LINE.match(line)
            if m:
                return int(m.group(1))
        return None

    def _take_stored(self, storage):
        try:
            self._cmd('AT+CMGF=0')
            lines = self._cmd('AT+CMGL=4', timeout=POLL_TIMEOUT)     # 4 = every stored text, read or not
        finally:
            try:
                self._cmd('AT+CMGF=1')
            except ModemError:
                pass
        stored = []                                   # (index, pdu hex, decoded or None)
        for header, pdu in zip(lines, lines[1:]):
            m = _CMGL_PDU.match(header)
            if not m or not _HEX.match(pdu):
                continue
            try:
                stored.append((int(m.group(1)), pdu, decode_pdu(pdu)))
            except (ValueError, IndexError):
                pass                                  # unreadable: left where it is, never deleted unseen
        now = time.monotonic()
        present = {(storage, pdu) for _, pdu, _ in stored}
        self._handed = {h for h in self._handed if h[0] != storage or h in present}
        waiting = {(storage, x['sender'], x['part'][0]) for _, _, x in stored if x and x['part']}
        self._parts_seen = {k: t for k, t in self._parts_seen.items() if k[0] != storage or k in waiting}
        done, groups = [], {}
        for index, pdu, msg in stored:
            if msg is None:
                done.append((index, pdu, None))       # a delivery report or the like: just cleared away
            elif msg['part'] is None:
                done.append((index, pdu, msg))
            else:
                groups.setdefault((storage, msg['sender'], msg['part'][0]), []).append((index, pdu, msg))
        for key, parts in groups.items():
            count = parts[0][2]['part'][1]
            first_seen = self._parts_seen.setdefault(key, now)
            if len({p[2]['part'][2] for p in parts}) < count and now - first_seen < PART_WAIT:
                continue                              # the rest is still on its way
            parts.sort(key=lambda p: p[2]['part'][2])
            whole = dict(parts[0][2], body=''.join(p[2]['body'] for p in parts))
            done.append((parts[0][0], parts[0][1], whole))
            done += [(index, pdu, None) for index, pdu, _ in parts[1:]]
            self._parts_seen.pop(key, None)
        out = []
        for index, pdu, msg in done:
            if msg is not None and (storage, pdu) not in self._handed:
                out.append({'sender': msg['sender'], 'body': msg['body'], 'ts': msg['ts']})
            self._handed.add((storage, pdu))
            try:
                self._cmd('AT+CMGD=%d' % index)
                self._handed.discard((storage, pdu))
            except ModemError:
                pass                                  # still stored; remembered, so not handed over again
        return out

    def signal_quality(self):
        with self._lock:
            for line in self._cmd('AT+CSQ'):
                m = _CSQ_LINE.match(line)
                if m:
                    v = int(m.group(1))
                    return v if v != 99 else None     # 99 = "unknown", per the AT spec
        return None

    def registered(self):
        with self._lock:
            for line in self._cmd('AT+CREG?'):
                m = _CREG_LINE.match(line)
                if m:
                    return int(m.group(1)) in (1, 5)   # 1 home, 5 roaming; 0/2/3/4 = not registered
        return False
