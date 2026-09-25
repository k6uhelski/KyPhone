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

The AT dialogue (text-mode SMS, AT+CMGS/CMGL/CMGD/CSQ/CREG) is standard Hayes/3GPP TS 27.005, and matches
Waveshare's own SIM7600G-H notes. It is checked against the real dongle once a SIM is active — nothing
here can prove real hardware talks back correctly; that is a phone-session step, not a test.
"""

import os
import re
import time
from datetime import datetime

MODEM_PORT_ENV = 'KYPHONE_MODEM_PORT'     # e.g. /dev/ttyUSB2 — which port lands on the AT interface
                                           # depends on USB enumeration order; see CLAUDE.md once set up.
DEFAULT_PORT   = '/dev/ttyUSB2'
DEFAULT_BAUD   = 115200

AT_TIMEOUT   = 5     # seconds to wait for a normal AT response (OK/ERROR)
SEND_TIMEOUT = 20     # sending a text can take longer (it waits on the network, not just the modem)
POLL_TIMEOUT = 8     # AT+CMGL can take a moment with several messages waiting

_CMGL_HEADER = re.compile(r'^\+CMGL:\s*(\d+),"[^"]*","([^"]*)",[^,]*,"([^"]*)"')
_CSQ_LINE    = re.compile(r'^\+CSQ:\s*(\d+),')
_CREG_LINE   = re.compile(r'^\+CREG:\s*\d+,\s*(\d+)')
_MODEM_TS    = re.compile(r'^(\d\d)/(\d\d)/(\d\d),(\d\d):(\d\d):(\d\d)')


class ModemError(Exception):
    """The modem could not be reached, or it reported failure sending or reading a text."""


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
        self._cmd('ATE0')                  # echo off — every response after this is just the answer
        self._cmd('AT+CMGF=1')             # text-mode SMS (not the raw PDU/hex format)
        self._cmd('AT+CSCS="GSM"')         # the character set the phone's own printable-ASCII text fits

    # -- the AT dialogue --

    def _write(self, s):
        self._ser.write((s + '\r').encode('ascii', errors='replace'))

    def _read_raw(self):
        chunk = self._ser.read(max(1, self._ser.in_waiting))
        return chunk.decode('ascii', errors='replace') if chunk else ''

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
        self._write('AT+CMGS="%s"' % number)
        deadline = time.monotonic() + AT_TIMEOUT
        while '>' not in self._buf:
            if time.monotonic() >= deadline:
                raise ModemError('modem never prompted for the message text')
            self._buf += self._read_raw()
        self._buf = self._buf.split('>', 1)[1]        # the prompt itself is not a line _read_until sees
        self._ser.write(text.encode('ascii', errors='replace') + b'\x1a')   # Ctrl-Z: send what was typed
        ok, lines, last = self._read_until(('OK',), SEND_TIMEOUT)
        if not ok:
            raise ModemError('send failed: %s' % last)

    def poll_new(self):
        lines = self._cmd('AT+CMGL="REC UNREAD"', timeout=POLL_TIMEOUT)
        messages, indexes = [], []
        for line in lines:
            m = _CMGL_HEADER.match(line)
            if m:
                indexes.append(int(m.group(1)))
                messages.append({'sender': m.group(2), 'body': '', 'ts': _parse_modem_ts(m.group(3))})
            elif messages:
                messages[-1]['body'] = line          # the line right after a header is that text's body
        for i in indexes:
            try:
                self._cmd('AT+CMGD=%d' % i)          # read once, gone from the modem's own storage
            except ModemError:
                pass                                  # not fatal: worst case it's read again next poll
        return messages

    def signal_quality(self):
        for line in self._cmd('AT+CSQ'):
            m = _CSQ_LINE.match(line)
            if m:
                v = int(m.group(1))
                return v if v != 99 else None         # 99 = "unknown", per the AT spec
        return None

    def registered(self):
        for line in self._cmd('AT+CREG?'):
            m = _CREG_LINE.match(line)
            if m:
                return int(m.group(1)) in (1, 5)       # 1 home, 5 roaming; 0/2/3/4 = not registered
        return False
